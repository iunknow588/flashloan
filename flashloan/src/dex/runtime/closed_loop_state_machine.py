"""Closed-loop runtime state machine with index-only queues.

The state names align with the route-level UI state machine in
``app_runtime/web/static/dex_quotes/route_state_machine.js`` and the terminal
execution states produced by ``rust_closed_loop``.  Queue entries only carry
``loop_key``/``group_key``; consumers must claim the loop state atomically
before starting work.  A loop that is already ``preflight_running`` or
``execution_submitted`` cannot be claimed again until it finishes or is
explicitly released by its owner.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from core.db.storage_business_io import execute, query, query_one, transaction
from core.market_identity import canonical_market_id_from_legacy
from dex.runtime.closed_loop_identity import (
    normalize_physical_loop_key,
    worker_group_key_from_symbols,
)


STATE_OFFCHAIN_PENDING = "offchain_pending"
STATE_OFFCHAIN_ESTIMATE = "offchain_estimate"
STATE_PREFLIGHT_RUNNING = "preflight_running"
STATE_PREFLIGHT_DONE = "preflight_done"
STATE_PREFLIGHT_FAILED = "preflight_failed"
STATE_EXECUTION_NOT_REQUESTED = "execution_not_requested"
STATE_EXECUTION_PENDING = "execution_pending"
STATE_EXECUTION_SUBMITTED = "execution_submitted"
STATE_EXECUTION_SUCCESS = "execution_success"
STATE_EXECUTION_FAILED = "execution_failed"

CLOSED_LOOP_STATES = frozenset(
    {
        STATE_OFFCHAIN_PENDING,
        STATE_OFFCHAIN_ESTIMATE,
        STATE_PREFLIGHT_RUNNING,
        STATE_PREFLIGHT_DONE,
        STATE_PREFLIGHT_FAILED,
        STATE_EXECUTION_NOT_REQUESTED,
        STATE_EXECUTION_PENDING,
        STATE_EXECUTION_SUBMITTED,
        STATE_EXECUTION_SUCCESS,
        STATE_EXECUTION_FAILED,
    }
)

REVIEW_RUNNING_STATES = frozenset({STATE_PREFLIGHT_RUNNING})
BROADCAST_RUNNING_STATES = frozenset({STATE_EXECUTION_SUBMITTED})
TERMINAL_EXECUTION_STATES = frozenset({STATE_EXECUTION_SUCCESS, STATE_EXECUTION_FAILED})
ORPHAN_PRUNE_PRESERVED_STATES = frozenset(
    {
        STATE_EXECUTION_SUBMITTED,
        STATE_EXECUTION_SUCCESS,
        STATE_EXECUTION_FAILED,
    }
)
CYCLE_TERMINAL_STATES = frozenset(
    {
        STATE_EXECUTION_SUCCESS,
        STATE_EXECUTION_FAILED,
        STATE_EXECUTION_NOT_REQUESTED,
    }
)

LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_OFFCHAIN_PENDING: frozenset(
        {
            STATE_OFFCHAIN_ESTIMATE,
            STATE_PREFLIGHT_RUNNING,
            STATE_PREFLIGHT_DONE,
            STATE_EXECUTION_PENDING,
        }
    ),
    STATE_OFFCHAIN_ESTIMATE: frozenset(
        {
            STATE_OFFCHAIN_ESTIMATE,
            STATE_PREFLIGHT_RUNNING,
            STATE_PREFLIGHT_DONE,
            STATE_EXECUTION_PENDING,
        }
    ),
    STATE_PREFLIGHT_RUNNING: frozenset(
        {STATE_PREFLIGHT_DONE, STATE_PREFLIGHT_FAILED}
    ),
    STATE_PREFLIGHT_DONE: frozenset(
        {
            STATE_PREFLIGHT_RUNNING,
            STATE_PREFLIGHT_FAILED,
            STATE_EXECUTION_PENDING,
            STATE_EXECUTION_NOT_REQUESTED,
        }
    ),
    STATE_PREFLIGHT_FAILED: frozenset({STATE_PREFLIGHT_RUNNING}),
    STATE_EXECUTION_NOT_REQUESTED: frozenset({STATE_PREFLIGHT_RUNNING}),
    STATE_EXECUTION_PENDING: frozenset(
        {STATE_EXECUTION_SUBMITTED, STATE_EXECUTION_FAILED, STATE_PREFLIGHT_DONE}
    ),
    STATE_EXECUTION_SUBMITTED: frozenset(
        {STATE_EXECUTION_SUCCESS, STATE_EXECUTION_FAILED, STATE_EXECUTION_PENDING}
    ),
    STATE_EXECUTION_FAILED: frozenset(
        {STATE_EXECUTION_PENDING, STATE_PREFLIGHT_RUNNING}
    ),
    STATE_EXECUTION_SUCCESS: frozenset(),
}

V4_CLOSED_LOOP_RUNTIME_STATE_TABLE = "v4_closed_loop_runtime_state"

V4_CLOSED_LOOP_ERROR_ISOLATION_TABLE = "v4_closed_loop_error_isolation"

V4_CLOSED_LOOPS_TABLE = "v4_closed_loops"

PROTECTED_TEST_RESIDUE_LOOP_KEYS = frozenset({"aaa|bbb|ccc"})

V4_CLOSED_LOOP_RUNTIME_STATE_DDL = f"""
CREATE TABLE IF NOT EXISTS {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} (
    network TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    market_id TEXT NOT NULL,
    loop_key TEXT NOT NULL,
    state TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    owner_thread TEXT,
    tx_hash TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    review_cycle_id TEXT,
    claimed_at TEXT,
    heartbeat_at TEXT,
    lease_until TEXT,
    finished_at TEXT,
    persisted_at TEXT,
    PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
)
"""

V4_CLOSED_LOOP_ERROR_ISOLATION_DDL = f"""
CREATE TABLE IF NOT EXISTS {V4_CLOSED_LOOP_ERROR_ISOLATION_TABLE} (
    network TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    error_fingerprint TEXT NOT NULL,
    disposition TEXT NOT NULL,
    reason TEXT,
    blocked_until TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key, error_fingerprint)
)
"""


def isolation_cooldown_seconds() -> int:
    try:
        return max(0, int(os.getenv("DEX_ERROR_ISOLATION_COOLDOWN_SECONDS", "300") or "300"))
    except (TypeError, ValueError):
        return 300


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _notify_state_change(
    database_url: str,
    *,
    loop_key: str,
    old_state: str | None = None,
    new_state: str | None = None,
    last_error: str | None = None,
) -> None:
    """Update the in-memory per-loop stats owned by the chain runtime.

    The runtime metrics object is the only place UI pages read loop counters.
    Every durable state write funnels through this best-effort hook so the
    in-memory object stays current without page-time SQL aggregation.
    """
    try:
        from dex.runtime.closed_loop_object import ClosedLoopRuntimeMetrics

        metrics = ClosedLoopRuntimeMetrics.cached_for(database_url)
        if metrics is not None:
            metrics.record_state_change(
                loop_key=loop_key,
                old_state=old_state,
                new_state=new_state,
                last_error=last_error,
            )
    except Exception:
        pass


def _lease_seconds(operation: str) -> int:
    env_name = (
        "DEX_CLOSED_LOOP_REVIEW_LEASE_SECONDS"
        if operation == "review"
        else "DEX_CLOSED_LOOP_BROADCAST_LEASE_SECONDS"
    )
    try:
        return max(5, min(3600, int(os.getenv(env_name, "120") or "120")))
    except (TypeError, ValueError):
        return 120


def _lease_until(operation: str, now_epoch: float | None = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime((time.time() if now_epoch is None else now_epoch) + _lease_seconds(operation)),
    )


def _scope_market_id(value: str | None) -> str | None:
    """Normalize legacy underscore market ids for matching current tables."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return normalized.replace("_", "-")


def _decode_json_array(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip().lower() for item in parsed if str(item).strip()]


def _closed_loop_identity_sets(
    rows: list[dict[str, Any]],
) -> tuple[set[str], set[str], set[str]]:
    """Return exact physical and legacy diagnostic keys for current loops."""
    exact: set[str] = set()
    symbol_keys: set[str] = set()
    address_keys: set[str] = set()
    for row in rows:
        loop_key = normalize_physical_loop_key(row.get("loop_key"))
        if loop_key:
            exact.add(loop_key)
        symbol_path = _decode_json_array(row.get("path_symbols_json"))
        symbol_key = worker_group_key_from_symbols(symbol_path)
        if symbol_key:
            symbol_keys.add(symbol_key)
        address_path = _decode_json_array(row.get("path_addresses_json"))
        address_key = worker_group_key_from_symbols(address_path)
        if address_key:
            address_keys.add(address_key)
    return exact, symbol_keys, address_keys


def _state_identity_matches(
    loop_key: str,
    *,
    exact_keys: set[str],
) -> bool:
    normalized = normalize_physical_loop_key(loop_key)
    return bool(normalized and normalized in exact_keys)


def _legacy_state_identity_kind(
    loop_key: str,
    *,
    symbol_keys: set[str],
    address_keys: set[str],
) -> str | None:
    """Classify a legacy non-physical key without treating it as stable."""
    normalized = str(loop_key or "").strip().lower()
    if not normalized or normalize_physical_loop_key(normalized):
        return None
    parts = [
        str(item).strip().lower()
        for item in normalized.replace(":", "|").split("|")
        if str(item).strip()
    ]
    group_key = worker_group_key_from_symbols(parts)
    if not group_key:
        return None
    if group_key in symbol_keys:
        return "symbol"
    if group_key in address_keys:
        return "address"
    return None


def _is_protected_test_residue(loop_key: Any) -> bool:
    return str(loop_key or "").strip().lower() in PROTECTED_TEST_RESIDUE_LOOP_KEYS


def _runtime_key_for_closed_loop(row: dict[str, Any]) -> str | None:
    """Return the physical runtime key represented by a closed-loop row."""
    loop_key = normalize_physical_loop_key(row.get("loop_key"))
    if loop_key:
        return loop_key
    address_path = _decode_json_array(row.get("path_addresses_json"))
    if len(address_path) == 4 and address_path[0] == address_path[-1]:
        return normalize_physical_loop_key(address_path[:3])
    return None


def _canonical_runtime_market_id(value: Any) -> tuple[str, bool]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "", False
    canonical = canonical_market_id_from_legacy(normalized)
    if canonical:
        return canonical, canonical != normalized
    return normalized, False


def ensure_runtime_state_table(database_url: str) -> None:
    execute(database_url, V4_CLOSED_LOOP_RUNTIME_STATE_DDL)
    execute(database_url, V4_CLOSED_LOOP_ERROR_ISOLATION_DDL)
    existing_columns = {
        str(row.get("name") or "")
        for row in query(
            database_url,
            f"PRAGMA table_info({V4_CLOSED_LOOP_RUNTIME_STATE_TABLE})",
        )
    }
    for name, declaration in {
        "review_cycle_id": "TEXT",
        "claimed_at": "TEXT",
        "heartbeat_at": "TEXT",
        "lease_until": "TEXT",
        "finished_at": "TEXT",
        "persisted_at": "TEXT",
    }.items():
        if name not in existing_columns:
            execute(
                database_url,
                f"ALTER TABLE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} ADD COLUMN {name} {declaration}",
            )


def _isolation_identity_sql() -> str:
    return (
        "network = ? AND chain_id = ? AND protocol = ? AND scope_key = ? "
        "AND error_fingerprint = ?"
    )


def _isolation_identity_params(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    scope_key: str,
    error_fingerprint: str,
) -> tuple[str, int, str, str, str]:
    return (
        str(network or "").strip().lower(),
        int(chain_id),
        str(protocol or "").strip().lower(),
        str(scope_key or "").strip().lower(),
        str(error_fingerprint or "").strip().lower(),
    )


def upsert_error_isolation(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    scope_key: str,
    error_fingerprint: str,
    disposition: str,
    reason: str | None = None,
    cooldown_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Record an error isolation entry with a bounded cooldown."""
    ensure_runtime_state_table(database_url)
    identity = _isolation_identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        scope_key=scope_key,
        error_fingerprint=error_fingerprint,
    )
    cooldown = max(0, int(cooldown_seconds if cooldown_seconds is not None else isolation_cooldown_seconds()))
    now = _stamp()
    blocked_until = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + cooldown),
    )
    execute(
        database_url,
        f"""
        INSERT INTO {V4_CLOSED_LOOP_ERROR_ISOLATION_TABLE} (
            network, chain_id, protocol, scope_key, error_fingerprint,
            disposition, reason, blocked_until, attempt, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(network, chain_id, protocol, scope_key, error_fingerprint)
        DO UPDATE SET
            disposition = excluded.disposition,
            reason = excluded.reason,
            blocked_until = excluded.blocked_until,
            attempt = {V4_CLOSED_LOOP_ERROR_ISOLATION_TABLE}.attempt + 1,
            updated_at = excluded.updated_at
        """,
        (
            *identity,
            str(disposition or "").strip().upper() or "DESIGN_BLOCKED",
            str(reason or "").strip() or None,
            blocked_until,
            now,
        ),
    )
    return {
        "network": identity[0],
        "chain_id": identity[1],
        "protocol": identity[2],
        "scope_key": identity[3],
        "error_fingerprint": identity[4],
        "disposition": str(disposition or "").strip().upper() or "DESIGN_BLOCKED",
        "blockedUntil": blocked_until,
    }


def load_error_isolation(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    scope_key: str,
    error_fingerprint: str,
) -> dict[str, Any] | None:
    ensure_runtime_state_table(database_url)
    identity = _isolation_identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        scope_key=scope_key,
        error_fingerprint=error_fingerprint,
    )
    row = query_one(
        database_url,
        f"""
        SELECT network, chain_id, protocol, scope_key, error_fingerprint,
               disposition, reason, blocked_until, attempt, updated_at
        FROM {V4_CLOSED_LOOP_ERROR_ISOLATION_TABLE}
        WHERE {_isolation_identity_sql()}
        """,
        identity,
    )
    if row is None:
        return None
    return {
        "network": row.get("network"),
        "chain_id": row.get("chain_id"),
        "protocol": row.get("protocol"),
        "scope_key": row.get("scope_key"),
        "error_fingerprint": row.get("error_fingerprint"),
        "disposition": row.get("disposition"),
        "reason": row.get("reason"),
        "blocked_until": row.get("blocked_until"),
        "attempt": row.get("attempt"),
        "updated_at": row.get("updated_at"),
    }


def error_isolation_active(
    database_url: str | None,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    scope_key: str,
    error_fingerprint: str,
    now: str | None = None,
) -> bool:
    """Return True while an error isolation cooldown has not expired."""
    if not database_url:
        return False
    record = load_error_isolation(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        scope_key=scope_key,
        error_fingerprint=error_fingerprint,
    )
    if record is None or not record.get("blocked_until"):
        return False
    current = now or _stamp()
    return str(record.get("blocked_until") or "") > str(current)


def scope_error_isolation_active(
    database_url: str | None,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    scope_key: str,
    now: str | None = None,
) -> bool:
    """Return True if any error isolation for the scope is still cooling."""
    if not database_url:
        return False
    ensure_runtime_state_table(database_url)
    current = now or _stamp()
    row = query_one(
        database_url,
        f"""
        SELECT MAX(blocked_until) AS blocked_until
        FROM {V4_CLOSED_LOOP_ERROR_ISOLATION_TABLE}
        WHERE network = ? AND chain_id = ? AND protocol = ? AND scope_key = ?
        """,
        (
            str(network or "").strip().lower(),
            int(chain_id),
            str(protocol or "").strip().lower(),
            str(scope_key or "").strip().lower(),
        ),
    )
    blocked_until = str((row or {}).get("blocked_until") or "")
    return bool(blocked_until) and blocked_until > str(current)


def can_transition(current_state: str, next_state: str) -> bool:
    return next_state in LEGAL_TRANSITIONS.get(current_state, frozenset())


def _identity_sql() -> str:
    return (
        "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
        "AND loop_key = ?"
    )


def _identity_params(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
) -> tuple[str, int, str, str, str]:
    return (network, int(chain_id), protocol, market_id, loop_key)


def upsert_loop_state(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    state: str,
    generation: int = 1,
    owner_thread: str | None = None,
    tx_hash: str | None = None,
    last_error: str | None = None,
    clear_cycle: bool = False,
) -> dict[str, Any]:
    if state not in CLOSED_LOOP_STATES:
        raise ValueError(f"unknown closed-loop state: {state}")
    ensure_runtime_state_table(database_url)
    now = _stamp()
    conflict_updates = [
        "state = excluded.state",
        "generation = excluded.generation",
        "owner_thread = excluded.owner_thread",
        "tx_hash = excluded.tx_hash",
        "last_error = excluded.last_error",
        "updated_at = excluded.updated_at",
    ]
    if clear_cycle:
        conflict_updates.extend(
            [
                "review_cycle_id = NULL",
                "claimed_at = NULL",
                "heartbeat_at = NULL",
                "lease_until = NULL",
                "finished_at = NULL",
                "persisted_at = NULL",
            ]
        )
    execute(
        database_url,
        f"""
        INSERT INTO {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} (
            network, chain_id, protocol, market_id, loop_key, state,
            generation, owner_thread, tx_hash, last_error, updated_at,
            review_cycle_id, claimed_at, heartbeat_at, lease_until,
            finished_at, persisted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)
        ON CONFLICT(network, chain_id, protocol, market_id, loop_key)
        DO UPDATE SET {", ".join(conflict_updates)}
        """,
        (
            network,
            int(chain_id),
            protocol,
            market_id,
            loop_key,
            state,
            int(generation),
            owner_thread,
            tx_hash,
            last_error,
            now,
        ),
    )
    row = load_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    if row is not None:
        _notify_state_change(
            database_url,
            loop_key=loop_key,
            new_state=str(row.get("state") or ""),
            last_error=row.get("last_error"),
        )
    return row


def load_loop_state(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
) -> dict[str, Any] | None:
    ensure_runtime_state_table(database_url)
    return query_one(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key, state,
               generation, owner_thread, tx_hash, last_error, updated_at,
               review_cycle_id, claimed_at, heartbeat_at, lease_until,
               finished_at, persisted_at
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {_identity_sql()}
        """,
        _identity_params(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_key=loop_key,
        ),
    )


def _claim_sources(operation: str) -> frozenset[str]:
    if operation == "review":
        return frozenset(
            {
                STATE_OFFCHAIN_PENDING,
                STATE_OFFCHAIN_ESTIMATE,
                STATE_PREFLIGHT_DONE,
                STATE_PREFLIGHT_FAILED,
            }
        )
    if operation == "broadcast":
        return frozenset({STATE_EXECUTION_PENDING})
    raise ValueError(f"unsupported closed-loop operation: {operation}")


def _claim_target(operation: str) -> str:
    if operation == "review":
        return STATE_PREFLIGHT_RUNNING
    if operation == "broadcast":
        return STATE_EXECUTION_SUBMITTED
    raise ValueError(f"unsupported closed-loop operation: {operation}")


def claim_loop(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    operation: str,
    owner_thread: str,
    expected_generation: int | None = None,
) -> dict[str, Any] | None:
    """Atomically claim one loop; returns the new state row on success."""
    ensure_runtime_state_table(database_url)
    sources = _claim_sources(operation)
    target = _claim_target(operation)
    placeholders = ", ".join("?" for _ in sources)
    now_stamp = _stamp()
    cycle_id = f"{operation}:{loop_key}:{int(time.time())}:{os.getpid()}"
    lease_stamp = _lease_until(operation)
    identity = _identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    params: list[Any] = [
        target,
        owner_thread,
        now_stamp,
        cycle_id,
        now_stamp,
        now_stamp,
        lease_stamp,
        *identity,
        *sorted(sources),
    ]
    generation_clause = ""
    if expected_generation is not None:
        generation_clause = " AND generation = ?"
        params.append(int(expected_generation))
    changed = execute(
        database_url,
        f"""
        UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        SET state = ?, owner_thread = ?, updated_at = ?,
            review_cycle_id = ?, claimed_at = ?, heartbeat_at = ?,
            lease_until = ?, finished_at = NULL, persisted_at = NULL
        WHERE {_identity_sql()}
          AND state IN ({placeholders})
          {generation_clause}
          AND (owner_thread IS NULL OR owner_thread = ?)
        """,
        (*params, owner_thread),
    )
    if not changed:
        return None
    row = load_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    if row is not None:
        _notify_state_change(
            database_url,
            loop_key=loop_key,
            new_state=str(row.get("state") or ""),
            last_error=row.get("last_error"),
        )
    return row


def enqueue_execution(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    expected_generation: int | None = None,
) -> dict[str, Any] | None:
    """Move a reviewed loop into the execution queue (index-only)."""
    ensure_runtime_state_table(database_url)
    identity = _identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    params: list[Any] = [
        STATE_EXECUTION_PENDING,
        _stamp(),
        *identity,
        STATE_PREFLIGHT_DONE,
    ]
    generation_clause = ""
    if expected_generation is not None:
        generation_clause = " AND generation = ?"
        params.append(int(expected_generation))
    changed = execute(
        database_url,
        f"""
        UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        SET state = ?, owner_thread = NULL, updated_at = ?,
            review_cycle_id = NULL, claimed_at = NULL, heartbeat_at = NULL,
            lease_until = NULL, finished_at = NULL
        WHERE {_identity_sql()}
          AND state = ?
          {generation_clause}
        """,
        tuple(params),
    )
    if not changed:
        return None
    row = load_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    if row is not None:
        _notify_state_change(
            database_url,
            loop_key=loop_key,
            new_state=str(row.get("state") or ""),
            last_error=row.get("last_error"),
        )
    return row


def transition_loop_state(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    from_state: str,
    to_state: str,
    expected_generation: int | None = None,
    owner_thread: str | None = None,
    tx_hash: str | None = None,
    last_error: str | None = None,
) -> dict[str, Any] | None:
    """Atomically transition one loop with state/owner/generation guards."""
    if from_state not in CLOSED_LOOP_STATES or to_state not in CLOSED_LOOP_STATES:
        raise ValueError(f"unknown closed-loop state: {from_state} -> {to_state}")
    ensure_runtime_state_table(database_url)
    identity = _identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    now = _stamp()
    set_clauses = [
        "state = ?",
        "owner_thread = NULL",
        "tx_hash = ?",
        "last_error = ?",
        "updated_at = ?",
        "review_cycle_id = NULL",
        "claimed_at = NULL",
        "heartbeat_at = NULL",
        "lease_until = NULL",
        "finished_at = ?",
        "persisted_at = NULL",
    ]
    params: list[Any] = [to_state, tx_hash, last_error, now, now]
    where_sql = f"{_identity_sql()} AND state = ?"
    params.extend([*identity, from_state])
    if expected_generation is not None:
        where_sql += " AND generation = ?"
        params.append(int(expected_generation))
    if owner_thread is not None:
        where_sql += " AND owner_thread = ?"
        params.append(owner_thread)
    changed = execute(
        database_url,
        f"""
        UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        SET {", ".join(set_clauses)}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    if not changed:
        return None
    row = load_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    if row is not None:
        _notify_state_change(
            database_url,
            loop_key=loop_key,
            old_state=from_state,
            new_state=str(row.get("state") or ""),
            last_error=row.get("last_error"),
        )
    return row


def mark_execution_not_requested(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    expected_generation: int | None = None,
    reason: str = "execution_not_requested",
) -> dict[str, Any] | None:
    """Close a successfully reviewed loop that did not request a broadcast."""
    return transition_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
        from_state=STATE_PREFLIGHT_DONE,
        to_state=STATE_EXECUTION_NOT_REQUESTED,
        expected_generation=expected_generation,
        last_error=reason,
    )


def complete_loop(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    operation: str,
    outcome: str,
    owner_thread: str,
    tx_hash: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Complete a running review/broadcast; only the owner may transition."""
    ensure_runtime_state_table(database_url)
    if operation == "review":
        running = STATE_PREFLIGHT_RUNNING
        if outcome == "success":
            target = STATE_PREFLIGHT_DONE
        elif outcome == "failed":
            target = STATE_PREFLIGHT_FAILED
        else:
            raise ValueError(f"unsupported review outcome: {outcome}")
    elif operation == "broadcast":
        running = STATE_EXECUTION_SUBMITTED
        if outcome == "success":
            target = STATE_EXECUTION_SUCCESS
        elif outcome == "failed":
            target = STATE_EXECUTION_FAILED
        elif outcome == "submitted":
            target = STATE_EXECUTION_SUBMITTED
        else:
            raise ValueError(f"unsupported broadcast outcome: {outcome}")
    else:
        raise ValueError(f"unsupported closed-loop operation: {operation}")
    identity = _identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    changed = execute(
        database_url,
        f"""
        UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        SET state = ?, owner_thread = NULL, tx_hash = ?, last_error = ?,
            updated_at = ?, heartbeat_at = NULL, review_cycle_id = NULL,
            claimed_at = NULL, lease_until = NULL, finished_at = ?
        WHERE {_identity_sql()}
          AND state = ?
          AND owner_thread = ?
        """,
        (
            target,
            tx_hash,
            error,
            _stamp(),
            _stamp(),
            *identity,
            running,
            owner_thread,
        ),
    )
    if not changed:
        return None
    row = load_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    if row is not None:
        _notify_state_change(
            database_url,
            loop_key=loop_key,
            old_state=running,
            new_state=str(row.get("state") or ""),
            last_error=row.get("last_error"),
        )
    return row


def release_loop(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    operation: str,
    owner_thread: str,
) -> dict[str, Any] | None:
    """Release a wedged running loop back to its pending state by owner."""
    ensure_runtime_state_table(database_url)
    if operation == "review":
        running = STATE_PREFLIGHT_RUNNING
        target = STATE_OFFCHAIN_ESTIMATE
    elif operation == "broadcast":
        running = STATE_EXECUTION_SUBMITTED
        target = STATE_EXECUTION_PENDING
    else:
        raise ValueError(f"unsupported closed-loop operation: {operation}")
    identity = _identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    changed = execute(
        database_url,
        f"""
        UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        SET state = ?, owner_thread = NULL, updated_at = ?,
            review_cycle_id = NULL, claimed_at = NULL, heartbeat_at = NULL,
            lease_until = NULL, finished_at = NULL
        WHERE {_identity_sql()}
          AND state = ?
          AND owner_thread = ?
        """,
        (target, _stamp(), *identity, running, owner_thread),
    )
    if not changed:
        return None
    row = load_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    if row is not None:
        _notify_state_change(
            database_url,
            loop_key=loop_key,
            old_state=running,
            new_state=str(row.get("state") or ""),
            last_error=row.get("last_error"),
        )
    return row


def heartbeat_loop(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    operation: str,
    owner_thread: str,
    expected_cycle_id: str | None = None,
) -> dict[str, Any] | None:
    """Refresh lease for a running loop without allowing cross-owner claims."""
    ensure_runtime_state_table(database_url)
    running = (
        STATE_PREFLIGHT_RUNNING
        if operation == "review"
        else STATE_EXECUTION_SUBMITTED
        if operation == "broadcast"
        else None
    )
    if running is None:
        raise ValueError(f"unsupported closed-loop operation: {operation}")
    identity = _identity_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    params: list[Any] = [_stamp(), _stamp(), _lease_until(operation), *identity, running, owner_thread]
    cycle_clause = ""
    if expected_cycle_id:
        cycle_clause = " AND review_cycle_id = ?"
        params.append(str(expected_cycle_id))
    changed = execute(
        database_url,
        f"""
        UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        SET heartbeat_at = ?, updated_at = ?, lease_until = ?
        WHERE {_identity_sql()}
          AND state = ?
          AND owner_thread = ?
          {cycle_clause}
        """,
        tuple(params),
    )
    if not changed:
        return None
    return load_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )


def reclaim_expired_review_claims(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
    max_age_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Reclaim only stale ``preflight_running`` rows to the review gate.

    This is a per-loop CAS-style recovery: one stuck review worker may be
    reclaimed without touching running or queued rows for other loops.
    """
    ensure_runtime_state_table(database_url)
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - max(0, int(max_age_seconds))),
    )
    filters: list[str] = ["state = ?", "owner_thread IS NOT NULL", "updated_at <= ?"]
    params: list[Any] = [STATE_PREFLIGHT_RUNNING, cutoff]
    if network is not None:
        filters.append("network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append("chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append("protocol = ?")
        params.append(str(protocol).strip().lower())
    if market_id is not None:
        filters.append("market_id = ?")
        params.append(str(market_id).strip().lower())
    where_sql = " AND ".join(filters)
    stale_rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key, owner_thread,
               updated_at
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    changed = 0
    if stale_rows:
        changed = execute(
            database_url,
            f"""
            UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            SET state = ?, owner_thread = NULL, updated_at = ?,
                review_cycle_id = NULL, claimed_at = NULL, heartbeat_at = NULL,
                lease_until = NULL, finished_at = NULL, persisted_at = NULL
            WHERE {where_sql}
              AND owner_thread IS NOT NULL
            """,
            (STATE_OFFCHAIN_ESTIMATE, _stamp(), *params),
        )
    reclaimed_at = _stamp()
    records = []
    for row in stale_rows:
        records.append(
            {
                "network": row.get("network"),
                "chain_id": row.get("chain_id"),
                "protocol": row.get("protocol"),
                "market_id": row.get("market_id"),
                "loop_key": row.get("loop_key"),
                "old_owner_thread": row.get("owner_thread"),
                "old_updated_at": row.get("updated_at"),
                "new_state": STATE_OFFCHAIN_ESTIMATE,
                "disposition": "review_lease_expired_reclaimed",
                "reclaimedCount": changed,
                "claimed": True,
                "reclaimedAt": reclaimed_at,
            }
        )
        _notify_state_change(
            database_url,
            loop_key=str(row.get("loop_key") or ""),
            old_state=STATE_PREFLIGHT_RUNNING,
            new_state=STATE_OFFCHAIN_ESTIMATE,
        )
    return records


def reclaim_expired_broadcast_claims(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
    max_age_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Reclaim only stale ``execution_submitted`` rows back to execution pending."""
    ensure_runtime_state_table(database_url)
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - max(0, int(max_age_seconds))),
    )
    filters: list[str] = ["state = ?", "owner_thread IS NOT NULL", "updated_at <= ?"]
    params: list[Any] = [STATE_EXECUTION_SUBMITTED, cutoff]
    if network is not None:
        filters.append("network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append("chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append("protocol = ?")
        params.append(str(protocol).strip().lower())
    if market_id is not None:
        filters.append("market_id = ?")
        params.append(str(market_id).strip().lower())
    where_sql = " AND ".join(filters)
    stale_rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key, owner_thread,
               updated_at
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    changed = 0
    if stale_rows:
        changed = execute(
            database_url,
            f"""
            UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            SET state = ?, owner_thread = NULL, updated_at = ?,
                review_cycle_id = NULL, claimed_at = NULL, heartbeat_at = NULL,
                lease_until = NULL, finished_at = NULL, persisted_at = NULL
            WHERE {where_sql}
              AND owner_thread IS NOT NULL
            """,
            (STATE_EXECUTION_PENDING, _stamp(), *params),
        )
    reclaimed_at = _stamp()
    records = []
    for row in stale_rows:
        records.append(
            {
                "network": row.get("network"),
                "chain_id": row.get("chain_id"),
                "protocol": row.get("protocol"),
                "market_id": row.get("market_id"),
                "loop_key": row.get("loop_key"),
                "old_owner_thread": row.get("owner_thread"),
                "old_updated_at": row.get("updated_at"),
                "new_state": STATE_EXECUTION_PENDING,
                "disposition": "broadcast_lease_expired_reclaimed",
                "reclaimedCount": changed,
                "claimed": True,
                "reclaimedAt": reclaimed_at,
            }
        )
        _notify_state_change(
            database_url,
            loop_key=str(row.get("loop_key") or ""),
            old_state=STATE_EXECUTION_SUBMITTED,
            new_state=STATE_EXECUTION_PENDING,
        )
    return records


def broadcast_result_missing_timeout_seconds() -> int:
    """How long an execution-pending loop may wait before it becomes a missing-broadcast terminal."""
    try:
        return max(
            30,
            int(
                os.getenv(
                    "DEX_CLOSED_LOOP_BROADCAST_RESULT_TIMEOUT_SECONDS",
                    "300",
                )
                or "300"
            ),
        )
    except (TypeError, ValueError):
        return 300


def review_only_timeout_seconds() -> int:
    """How long a reviewed loop may stay in ``preflight_done`` without a terminal."""
    try:
        return max(
            30,
            int(
                os.getenv(
                    "DEX_CLOSED_LOOP_REVIEW_ONLY_TIMEOUT_SECONDS",
                    "300",
                )
                or "300"
            ),
        )
    except (TypeError, ValueError):
        return 300


def fail_stuck_execution_loops(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
    loop_keys: list[str] | None = None,
    max_age_seconds: int | None = None,
    reason: str = "broadcast_result_missing",
) -> list[dict[str, Any]]:
    """Fail loops that stayed in execution pending/submitted without a result.

    A successfully reviewed loop must eventually produce a broadcast success or
    broadcast failure terminal.  When the execution queue never returns a result
    within the timeout, the loop is moved to ``execution_failed`` with a
    machine-readable ``last_error`` so the page always has a terminal outcome
    instead of an invisible 'no queue, no result' state.
    """
    ensure_runtime_state_table(database_url)
    timeout_seconds = max(
        0,
        int(
            max_age_seconds
            if max_age_seconds is not None
            else broadcast_result_missing_timeout_seconds()
        ),
    )
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - timeout_seconds),
    )
    filters = [
        "state IN (?, ?)",
        "updated_at <= ?",
    ]
    params: list[Any] = [STATE_EXECUTION_PENDING, STATE_EXECUTION_SUBMITTED, cutoff]
    if network is not None:
        filters.append("network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append("chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append("protocol = ?")
        params.append(str(protocol).strip().lower())
    if market_id is not None:
        filters.append("market_id = ?")
        params.append(str(market_id).strip().lower())
    if loop_keys:
        normalized_keys = [str(key).strip().lower() for key in loop_keys if str(key or "").strip()]
        if normalized_keys:
            filters.append(f"loop_key IN ({', '.join('?' for _ in normalized_keys)})")
            params.extend(normalized_keys)
    where_sql = " AND ".join(filters)
    stale_rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key, state,
               generation, owner_thread, updated_at
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    changed = 0
    if stale_rows:
        now = _stamp()
        changed = execute(
            database_url,
            f"""
            UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            SET state = ?, owner_thread = NULL, last_error = ?,
                updated_at = ?, review_cycle_id = NULL, claimed_at = NULL,
                heartbeat_at = NULL, lease_until = NULL, finished_at = ?,
                persisted_at = NULL
            WHERE {where_sql}
            """,
            (STATE_EXECUTION_FAILED, reason, now, now, *params),
        )
    records: list[dict[str, Any]] = []
    for row in stale_rows:
        records.append(
            {
                "network": row.get("network"),
                "chain_id": row.get("chain_id"),
                "protocol": row.get("protocol"),
                "market_id": row.get("market_id"),
                "loop_key": row.get("loop_key"),
                "old_state": row.get("state"),
                "old_owner_thread": row.get("owner_thread"),
                "new_state": STATE_EXECUTION_FAILED,
                "last_error": reason,
                "disposition": "BROADCAST_RESULT_MISSING",
                "changedCount": changed,
                "updatedAt": _stamp(),
            }
        )
        _notify_state_change(
            database_url,
            loop_key=str(row.get("loop_key") or ""),
            old_state=str(row.get("state") or ""),
            new_state=STATE_EXECUTION_FAILED,
            last_error=reason,
        )
    return records


def close_stale_review_only_loops(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
    loop_keys: list[str] | None = None,
    max_age_seconds: int | None = None,
    reason: str = "execution_not_requested",
) -> list[dict[str, Any]]:
    """Close ``preflight_done`` rows that completed without an execution request.

    This is the review-only analogue of ``fail_stuck_execution_loops``: the
    loop gets a real terminal instead of staying in an invisible
    "review finished, nothing happened" state.
    """
    ensure_runtime_state_table(database_url)
    timeout_seconds = max(
        0,
        int(
            max_age_seconds
            if max_age_seconds is not None
            else review_only_timeout_seconds()
        ),
    )
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - timeout_seconds),
    )
    filters = ["state = ?", "updated_at <= ?", "owner_thread IS NULL"]
    params: list[Any] = [STATE_PREFLIGHT_DONE, cutoff]
    if network is not None:
        filters.append("network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append("chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append("protocol = ?")
        params.append(str(protocol).strip().lower())
    if market_id is not None:
        filters.append("market_id = ?")
        params.append(str(market_id).strip().lower())
    if loop_keys:
        normalized_keys = [str(key).strip().lower() for key in loop_keys if str(key or "").strip()]
        if normalized_keys:
            filters.append(f"loop_key IN ({', '.join('?' for _ in normalized_keys)})")
            params.extend(normalized_keys)
    where_sql = " AND ".join(filters)
    stale_rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key, state,
               generation, owner_thread, updated_at
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    changed = 0
    if stale_rows:
        now = _stamp()
        changed = execute(
            database_url,
            f"""
            UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            SET state = ?, owner_thread = NULL, last_error = ?,
                updated_at = ?, review_cycle_id = NULL, claimed_at = NULL,
                heartbeat_at = NULL, lease_until = NULL, finished_at = ?,
                persisted_at = NULL
            WHERE {where_sql}
            """,
            (STATE_EXECUTION_NOT_REQUESTED, reason, now, now, *params),
        )
    records: list[dict[str, Any]] = []
    for row in stale_rows:
        records.append(
            {
                "network": row.get("network"),
                "chain_id": row.get("chain_id"),
                "protocol": row.get("protocol"),
                "market_id": row.get("market_id"),
                "loop_key": row.get("loop_key"),
                "old_state": row.get("state"),
                "old_owner_thread": row.get("owner_thread"),
                "new_state": STATE_EXECUTION_NOT_REQUESTED,
                "last_error": reason,
                "disposition": "EXECUTION_NOT_REQUESTED",
                "changedCount": changed,
                "updatedAt": _stamp(),
            }
        )
        _notify_state_change(
            database_url,
            loop_key=str(row.get("loop_key") or ""),
            old_state=str(row.get("state") or ""),
            new_state=STATE_EXECUTION_NOT_REQUESTED,
            last_error=reason,
        )
    return records


def prune_legacy_unresolved_runtime_state(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
) -> dict[str, Any]:
    """Delete legacy ``unresolved:`` placeholder rows from the runtime state.

    Old scanner versions stored route keys such as
    ``unresolved:0x...|spy|unresolved:0x...`` in the runtime state table even
    though no corresponding ``v4_closed_loops`` row exists.  Those rows are not
    state-machine loops and would otherwise occupy the UI summary forever as
    fake execution/review records.  The cleanup is idempotent and only removes
    orphan unresolved keys; a key with a matching closed-loop row is preserved.
    """
    ensure_runtime_state_table(database_url)
    loops_table_rows = query(
        database_url,
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (V4_CLOSED_LOOPS_TABLE,),
    )
    loops_table_exists = bool(loops_table_rows)

    table = V4_CLOSED_LOOP_RUNTIME_STATE_TABLE
    filters = [f"lower({table}.loop_key) LIKE ?"]
    params: list[Any] = ["%unresolved:%"]
    if network is not None:
        filters.append(f"{table}.network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append(f"{table}.chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append(f"{table}.protocol = ?")
        params.append(str(protocol).strip().lower())
    if market_id is not None:
        filters.append(f"{table}.market_id = ?")
        params.append(str(market_id).strip().lower())
    where_sql = " AND ".join(filters)

    if loops_table_exists:
        select_sql = f"""
            SELECT {table}.network, {table}.chain_id, {table}.protocol,
                   {table}.market_id, {table}.loop_key, {table}.state,
                   {table}.updated_at, closed_loop.loop_key AS closed_loop_key
            FROM {table}
            LEFT JOIN {V4_CLOSED_LOOPS_TABLE} AS closed_loop
              ON closed_loop.network = {table}.network
             AND closed_loop.chain_id = {table}.chain_id
             AND closed_loop.protocol = {table}.protocol
             AND closed_loop.market_id = {table}.market_id
             AND closed_loop.loop_key = {table}.loop_key
            WHERE {where_sql}
        """
    else:
        select_sql = f"""
            SELECT network, chain_id, protocol, market_id, loop_key, state,
                   updated_at
            FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            WHERE {where_sql}
        """
    rows = query(database_url, select_sql, tuple(params))

    if loops_table_exists:
        orphan_rows = [
            row
            for row in rows
            if row.get("closed_loop_key") is None and row.get("loop_key") is not None
        ]
        matched_count = len(rows) - len(orphan_rows)
    else:
        orphan_rows = list(rows)
        matched_count = 0

    deleted = 0
    if orphan_rows:
        if loops_table_exists:
            deleted = execute(
                database_url,
                f"""
                DELETE FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
                WHERE {where_sql}
                  AND NOT EXISTS (
                      SELECT 1 FROM {V4_CLOSED_LOOPS_TABLE} AS c
                      WHERE c.network = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.network
                        AND c.chain_id = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.chain_id
                        AND c.protocol = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.protocol
                        AND c.market_id = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.market_id
                        AND c.loop_key = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.loop_key
                  )
                """,
                tuple(params),
            )
        else:
            deleted = execute(
                database_url,
                f"""
                DELETE FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
                WHERE {where_sql}
                """,
                tuple(params),
            )

    records: list[dict[str, Any]] = []
    for row in orphan_rows:
        records.append(
            {
                "network": row.get("network"),
                "chain_id": row.get("chain_id"),
                "protocol": row.get("protocol"),
                "market_id": row.get("market_id"),
                "loop_key": row.get("loop_key"),
                "old_state": row.get("state"),
                "old_updated_at": row.get("updated_at"),
                "disposition": "legacy_unresolved_runtime_state_pruned",
            }
        )
    return {
        "ok": True,
        "deleted": len(orphan_rows),
        "deletedCount": int(deleted or 0),
        "matchedClosedLoopCount": matched_count,
        "records": records,
        "disposition": "legacy_unresolved_runtime_state_pruned",
        "generatedAt": _stamp(),
    }


def prune_test_residue_runtime_state(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
    apply_cleanup: bool = False,
    approval_id: str | None = None,
    snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    """Detect the confirmed test residue; delete only under explicit approval.

    Routine maintenance must preserve this row as evidence.  A delete requires
    ``apply_cleanup=True`` plus both a non-empty approval id and the SHA256 of
    the database snapshot that approved the cleanup.
    """
    approval = str(approval_id or "").strip()
    checksum = str(snapshot_sha256 or "").strip().lower()
    if apply_cleanup and (not approval or not checksum):
        raise ValueError(
            "explicit cleanup requires approval_id and snapshot_sha256"
        )
    ensure_runtime_state_table(database_url)
    loops_table_rows = query(
        database_url,
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (V4_CLOSED_LOOPS_TABLE,),
    )
    loops_table_exists = bool(loops_table_rows)
    records: list[dict[str, Any]] = []
    if not loops_table_exists:
        return {
            "ok": True,
            "deleted": 0,
            "deletedCount": 0,
            "residueDetected": 0,
            "residualPruned": 0,
            "preservedCount": 0,
            "records": records,
            "disposition": "test_residue_runtime_state_preserved_no_closed_loop_table",
            "applyRequested": bool(apply_cleanup),
            "approvalId": approval or None,
            "snapshotSha256": checksum or None,
            "generatedAt": _stamp(),
        }

    table = V4_CLOSED_LOOP_RUNTIME_STATE_TABLE
    filters = [f"lower({table}.loop_key) = ?"]
    params: list[Any] = ["aaa|bbb|ccc"]
    if network is not None:
        filters.append(f"{table}.network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append(f"{table}.chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append(f"{table}.protocol = ?")
        params.append(str(protocol).strip().lower())
    if market_id is not None:
        filters.append(f"{table}.market_id = ?")
        params.append(str(market_id).strip().lower())
    where_sql = " AND ".join(filters)
    rows = query(
        database_url,
        f"""
        SELECT {table}.network, {table}.chain_id, {table}.protocol,
               {table}.market_id, {table}.loop_key, {table}.state,
               {table}.updated_at, closed_loop.loop_key AS closed_loop_key
        FROM {table}
        LEFT JOIN {V4_CLOSED_LOOPS_TABLE} AS closed_loop
          ON closed_loop.network = {table}.network
         AND closed_loop.chain_id = {table}.chain_id
         AND closed_loop.protocol = {table}.protocol
         AND closed_loop.market_id = {table}.market_id
         AND closed_loop.loop_key = {table}.loop_key
        WHERE {where_sql}
        """,
        tuple(params),
    )
    orphan_rows = [
        row
        for row in rows
        if row.get("closed_loop_key") is None and row.get("loop_key") is not None
    ]
    deleted = 0
    if apply_cleanup and orphan_rows:
        deleted = execute(
            database_url,
            f"""
            DELETE FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            WHERE {where_sql}
              AND NOT EXISTS (
                  SELECT 1 FROM {V4_CLOSED_LOOPS_TABLE} AS c
                  WHERE c.network = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.network
                    AND c.chain_id = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.chain_id
                    AND c.protocol = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.protocol
                    AND c.market_id = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.market_id
                    AND c.loop_key = {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}.loop_key
              )
            """,
            tuple(params),
        )
    for row in orphan_rows:
        records.append(
            {
                "network": row.get("network"),
                "chain_id": row.get("chain_id"),
                "protocol": row.get("protocol"),
                "market_id": row.get("market_id"),
                "loop_key": row.get("loop_key"),
                "old_state": row.get("state"),
                "old_updated_at": row.get("updated_at"),
                "disposition": (
                    "test_residue_runtime_state_pruned"
                    if apply_cleanup
                    else "test_residue_runtime_state_detected_preserved"
                ),
            }
        )
    return {
        "ok": True,
        "deleted": len(orphan_rows) if apply_cleanup else 0,
        "deletedCount": int(deleted or 0),
        "residueDetected": len(orphan_rows),
        "residualPruned": int(deleted or 0),
        "preservedCount": 0 if apply_cleanup else len(orphan_rows),
        "records": records,
        "disposition": (
            "test_residue_runtime_state_pruned"
            if apply_cleanup
            else "test_residue_runtime_state_detected_preserved"
        ),
        "applyRequested": bool(apply_cleanup),
        "approvalId": approval or None,
        "snapshotSha256": checksum or None,
        "generatedAt": _stamp(),
    }


def canonicalize_runtime_state_market_ids(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
) -> dict[str, Any]:
    """Migrate legacy runtime-state market ids to their canonical spelling.

    Runtime state uses ``market_id`` as part of its primary key.  Rewriting a
    row is therefore allowed only when the canonical key does not already
    exist.  Conflicts are reported and preserved rather than deleting one of
    two rows with potentially different review or broadcast state.
    """
    ensure_runtime_state_table(database_url)
    filters: list[str] = []
    params: list[Any] = []
    if network is not None:
        filters.append("network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append("chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append("protocol = ?")
        params.append(str(protocol).strip().lower())
    where_sql = " AND ".join(filters) if filters else "1 = 1"
    rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key, state,
               generation, updated_at
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {where_sql}
        ORDER BY network, chain_id, protocol, market_id, loop_key
        """,
        tuple(params),
    )
    target_market, _ = _canonical_runtime_market_id(market_id)

    identities = {
        (
            str(row.get("network") or "").strip().lower(),
            int(row.get("chain_id") or 0),
            str(row.get("protocol") or "").strip().lower(),
            str(row.get("market_id") or "").strip().lower(),
            str(row.get("loop_key") or "").strip().lower(),
        )
        for row in rows
    }
    changed_rows: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []
    for row in rows:
        old_market = str(row.get("market_id") or "").strip().lower()
        new_market, should_change = _canonical_runtime_market_id(old_market)
        if not should_change:
            continue
        if target_market and new_market != target_market:
            continue
        old_identity = (
            str(row.get("network") or "").strip().lower(),
            int(row.get("chain_id") or 0),
            str(row.get("protocol") or "").strip().lower(),
            old_market,
            str(row.get("loop_key") or "").strip().lower(),
        )
        new_identity = (*old_identity[:3], new_market, old_identity[4])
        if new_identity in identities:
            conflict_rows.append({**row, "canonical_market_id": new_market})
            continue
        changed = execute(
            database_url,
            f"""
            UPDATE {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            SET market_id = ?
            WHERE network = ? AND chain_id = ? AND protocol = ?
              AND market_id = ? AND loop_key = ?
              AND NOT EXISTS (
                  SELECT 1 FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
                  WHERE network = ? AND chain_id = ? AND protocol = ?
                    AND market_id = ? AND loop_key = ?
              )
            """,
            (
                new_market,
                *old_identity,
                *new_identity,
            ),
        )
        if changed:
            identities.discard(old_identity)
            identities.add(new_identity)
            changed_rows.append({**row, "canonical_market_id": new_market})
        else:
            conflict_rows.append({**row, "canonical_market_id": new_market})

    return {
        "ok": not conflict_rows,
        "examined": len(rows),
        "updated": len(changed_rows),
        "conflicts": len(conflict_rows),
        "records": changed_rows,
        "conflictRecords": conflict_rows,
        "disposition": (
            "runtime_market_ids_canonicalized"
            if not conflict_rows
            else "runtime_market_id_conflicts_preserved"
        ),
        "generatedAt": _stamp(),
    }


def backfill_runtime_state_for_closed_loops(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
) -> dict[str, Any]:
    """Create one ``offchain_pending`` runtime row per directed closed loop."""
    ensure_runtime_state_table(database_url)
    loops_table_rows = query(
        database_url,
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (V4_CLOSED_LOOPS_TABLE,),
    )
    if not loops_table_rows:
        return {
            "ok": True,
            "created": 0,
            "existing": 0,
            "runtimeStateCount": 0,
            "runtimeGroupCount": 0,
            "records": [],
            "disposition": "runtime_state_backfill_skipped_no_closed_loop_table",
            "generatedAt": _stamp(),
        }

    filters: list[str] = []
    params: list[Any] = []
    if network is not None:
        filters.append("network = ?")
        params.append(str(network).strip().lower())
    if chain_id is not None:
        filters.append("chain_id = ?")
        params.append(int(chain_id))
    if protocol is not None:
        filters.append("protocol = ?")
        params.append(str(protocol).strip().lower())
    target_market, _ = _canonical_runtime_market_id(market_id)
    if target_market:
        filters.append("replace(lower(market_id), '_', '-') = ?")
        params.append(target_market)
    where_sql = " AND ".join(filters) if filters else "1 = 1"
    loops = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key,
               path_symbols_json
        FROM {V4_CLOSED_LOOPS_TABLE}
        WHERE {where_sql}
        ORDER BY network, chain_id, protocol, market_id, loop_key
        """,
        tuple(params),
    )

    current_loops: dict[tuple[str, int, str, str, str], dict[str, Any]] = {}
    for row in loops:
        runtime_key = _runtime_key_for_closed_loop(row)
        if not runtime_key:
            continue
        canonical_market, _ = _canonical_runtime_market_id(row.get("market_id"))
        identity = (
            str(row.get("network") or "").strip().lower(),
            int(row.get("chain_id") or 0),
            str(row.get("protocol") or "").strip().lower(),
            canonical_market,
            runtime_key,
        )
        if identity in current_loops:
            # A duplicate physical key violates the closed-loop authority and
            # must not be collapsed into one runtime row.
            return {
                "ok": False,
                "created": 0,
                "existing": 0,
                "runtimeStateCount": 0,
                "runtimeGroupCount": 0,
                "duplicateDirectedKey": 1,
                "records": [],
                "disposition": "duplicate_directed_closed_loop_key",
                "generatedAt": _stamp(),
            }
        current_loops[identity] = row

    state_filters: list[str] = []
    state_params: list[Any] = []
    if network is not None:
        state_filters.append("network = ?")
        state_params.append(str(network).strip().lower())
    if chain_id is not None:
        state_filters.append("chain_id = ?")
        state_params.append(int(chain_id))
    if protocol is not None:
        state_filters.append("protocol = ?")
        state_params.append(str(protocol).strip().lower())
    if target_market:
        state_filters.append("replace(lower(market_id), '_', '-') = ?")
        state_params.append(target_market)
    state_where_sql = " AND ".join(state_filters) if state_filters else "1 = 1"
    state_rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {state_where_sql}
        """,
        tuple(state_params),
    )
    existing_identities: set[tuple[str, int, str, str, str]] = set()
    for row in state_rows:
        canonical_market, _ = _canonical_runtime_market_id(row.get("market_id"))
        loop_key = normalize_physical_loop_key(row.get("loop_key"))
        if not loop_key:
            continue
        existing_identities.add(
            (
                str(row.get("network") or "").strip().lower(),
                int(row.get("chain_id") or 0),
                str(row.get("protocol") or "").strip().lower(),
                canonical_market,
                loop_key,
            )
        )

    now = _stamp()
    statements: list[tuple[str, tuple[Any, ...]]] = []
    created_records: list[dict[str, Any]] = []
    for identity in sorted(current_loops):
        if identity in existing_identities:
            continue
        statements.append(
            (
                f"""
                INSERT INTO {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} (
                    network, chain_id, protocol, market_id, loop_key, state,
                    generation, owner_thread, tx_hash, last_error, updated_at,
                    review_cycle_id, claimed_at, heartbeat_at, lease_until,
                    finished_at, persisted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?,
                          NULL, NULL, NULL, NULL, NULL, NULL)
                ON CONFLICT(network, chain_id, protocol, market_id, loop_key)
                DO NOTHING
                """,
                (
                    *identity,
                    STATE_OFFCHAIN_PENDING,
                    1,
                    now,
                ),
            )
        )
        created_records.append(
            {
                "network": identity[0],
                "chain_id": identity[1],
                "protocol": identity[2],
                "market_id": identity[3],
                "loop_key": identity[4],
                "state": STATE_OFFCHAIN_PENDING,
            }
        )
    created = transaction(database_url, statements) if statements else 0
    return {
        "ok": True,
        "created": int(created or 0),
        "existing": len(set(current_loops) & existing_identities),
        "runtimeStateCount": len(current_loops),
        "runtimeGroupCount": len(current_loops),
        "duplicateDirectedKey": 0,
        "records": created_records,
        "disposition": "directed_runtime_state_backfilled",
        "generatedAt": _stamp(),
    }


def prune_orphan_runtime_state(
    database_url: str,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
) -> dict[str, Any]:
    """Remove runtime-state keys that no longer exist in the current loop set.

    Closed loops are rebuilt from liquidity-backed candidates, so a runtime
    state key may survive from a removed pool even though the route no longer
    exists. Stable matching is exact physical ``loopKey`` only. Legacy symbol
    and address groups are retained as pending-migration diagnostics because
    deleting them before P0-2.4 would erase the aggregate history needed for
    the controlled re-key. Execution terminal and in-flight states, plus the
    known Testnet residue, remain protected. It never touches execution
    attempts, broadcasts, reviews or closed-loop tables.
    """
    ensure_runtime_state_table(database_url)
    loops_table_rows = query(
        database_url,
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (V4_CLOSED_LOOPS_TABLE,),
    )
    if not loops_table_rows:
        return {
            "ok": True,
            "deleted": 0,
            "deletedCount": 0,
            "remaining": 0,
            "records": [],
            "disposition": "orphan_runtime_state_preserved_no_closed_loop_table",
            "generatedAt": _stamp(),
        }

    scope_filters: list[str] = []
    loop_params: list[Any] = []
    if network is not None:
        scope_filters.append("network = ?")
        loop_params.append(str(network).strip().lower())
    if chain_id is not None:
        scope_filters.append("chain_id = ?")
        loop_params.append(int(chain_id))
    if protocol is not None:
        scope_filters.append("protocol = ?")
        loop_params.append(str(protocol).strip().lower())
    canonical_market = _scope_market_id(market_id)
    if canonical_market is not None:
        scope_filters.append("replace(lower(market_id), '_', '-') = ?")
        loop_params.append(canonical_market)
    scope_sql = " AND ".join(scope_filters) if scope_filters else "1 = 1"

    current_loops = query(
        database_url,
        f"""
        SELECT loop_key, path_symbols_json, path_addresses_json
        FROM {V4_CLOSED_LOOPS_TABLE}
        WHERE {scope_sql}
        """,
        tuple(loop_params),
    )
    exact_keys, symbol_keys, address_keys = _closed_loop_identity_sets(current_loops)

    state_filters = []
    state_params: list[Any] = []
    if network is not None:
        state_filters.append("network = ?")
        state_params.append(str(network).strip().lower())
    if chain_id is not None:
        state_filters.append("chain_id = ?")
        state_params.append(int(chain_id))
    if protocol is not None:
        state_filters.append("protocol = ?")
        state_params.append(str(protocol).strip().lower())
    if canonical_market is not None:
        state_filters.append("replace(lower(market_id), '_', '-') = ?")
        state_params.append(canonical_market)
    state_sql = " AND ".join(state_filters) if state_filters else "1 = 1"

    rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, market_id, loop_key, state,
               updated_at
        FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
        WHERE {state_sql}
        ORDER BY network, chain_id, protocol, market_id, loop_key
        """,
        tuple(state_params),
    )
    orphan_rows: list[dict[str, Any]] = []
    matched_rows: list[dict[str, Any]] = []
    preserved_rows: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        if _state_identity_matches(
            str(row.get("loop_key") or ""),
            exact_keys=exact_keys,
        ):
            matched_rows.append(row)
        elif _is_protected_test_residue(row.get("loop_key")):
            preserved_rows.append((row, "protected_test_residue_preserved"))
        elif str(row.get("state") or "").strip().lower() in ORPHAN_PRUNE_PRESERVED_STATES:
            preserved_rows.append((row, "orphan_execution_state_preserved"))
        elif (
            legacy_kind := _legacy_state_identity_kind(
                str(row.get("loop_key") or ""),
                symbol_keys=symbol_keys,
                address_keys=address_keys,
            )
        ):
            preserved_rows.append(
                (
                    row,
                    f"legacy_{legacy_kind}_identity_pending_migration",
                )
            )
        else:
            orphan_rows.append(row)

    statements: list[tuple[str, tuple[Any, ...]]] = []
    for row in orphan_rows:
        statements.append(
            (
                f"""
                DELETE FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
                WHERE network = ? AND chain_id = ? AND protocol = ?
                  AND market_id = ? AND loop_key = ?
                """,
                (
                    str(row.get("network") or "").strip().lower(),
                    int(row.get("chain_id") or 0),
                    str(row.get("protocol") or "").strip().lower(),
                    str(row.get("market_id") or "").strip().lower(),
                    str(row.get("loop_key") or "").strip().lower(),
                ),
            )
        )
    deleted = transaction(database_url, statements) if statements else 0
    records = [
        {
            "network": row.get("network"),
            "chain_id": row.get("chain_id"),
            "protocol": row.get("protocol"),
            "market_id": row.get("market_id"),
            "loop_key": row.get("loop_key"),
            "old_state": row.get("state"),
            "old_updated_at": row.get("updated_at"),
            "disposition": "orphan_runtime_state_pruned",
        }
        for row in orphan_rows
    ]
    preserved_records = [
        {
            "network": row.get("network"),
            "chain_id": row.get("chain_id"),
            "protocol": row.get("protocol"),
            "market_id": row.get("market_id"),
            "loop_key": row.get("loop_key"),
            "state": row.get("state"),
            "updated_at": row.get("updated_at"),
            "disposition": disposition,
        }
        for row, disposition in preserved_rows
    ]
    return {
        "ok": True,
        "deleted": len(orphan_rows),
        "deletedCount": int(deleted or 0),
        "remaining": len(matched_rows) + len(preserved_rows),
        "orphanCount": len(orphan_rows),
        "matchedClosedLoopCount": len(matched_rows),
        "preservedExecutionStateCount": sum(
            1
            for _row, disposition in preserved_rows
            if disposition == "orphan_execution_state_preserved"
        ),
        "legacySymbolIdentityPreservedCount": sum(
            1
            for _row, disposition in preserved_rows
            if disposition == "legacy_symbol_identity_pending_migration"
        ),
        "legacyAddressIdentityPreservedCount": sum(
            1
            for _row, disposition in preserved_rows
            if disposition == "legacy_address_identity_pending_migration"
        ),
        "protectedTestResidueCount": sum(
            1
            for _row, disposition in preserved_rows
            if disposition == "protected_test_residue_preserved"
        ),
        "records": records,
        "preservedRecords": preserved_records,
        "disposition": "orphan_runtime_state_pruned",
        "generatedAt": _stamp(),
    }


def fail_stuck_execution_loop(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    max_age_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Fail one loop that is waiting for a broadcast result."""
    records = fail_stuck_execution_loops(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_keys=[loop_key],
        max_age_seconds=max_age_seconds,
    )
    return records[0] if records else None


def reset_loop_for_new_cycle(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    generation: int,
) -> dict[str, Any]:
    """Start a fresh cycle without reusing an old queue index."""
    ensure_runtime_state_table(database_url)
    return upsert_loop_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
        state=STATE_OFFCHAIN_ESTIMATE,
        generation=int(generation),
        owner_thread=None,
        tx_hash=None,
        last_error=None,
        clear_cycle=True,
    )


def resolve_max_workers(value: str | int | None = None) -> int:
    """Resolve the global worker cap; default 16, hard ceiling 32."""
    if value is None:
        raw = os.getenv("DEX_CLOSED_LOOP_MAX_WORKERS", "16")
    else:
        raw = str(value)
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 16
    return max(1, min(32, parsed))


__all__ = [
    "BROADCAST_RUNNING_STATES",
    "CLOSED_LOOP_STATES",
    "CYCLE_TERMINAL_STATES",
    "LEGAL_TRANSITIONS",
    "REVIEW_RUNNING_STATES",
    "STATE_EXECUTION_FAILED",
    "STATE_EXECUTION_NOT_REQUESTED",
    "STATE_EXECUTION_PENDING",
    "STATE_EXECUTION_SUBMITTED",
    "STATE_EXECUTION_SUCCESS",
    "STATE_OFFCHAIN_ESTIMATE",
    "STATE_OFFCHAIN_PENDING",
    "STATE_PREFLIGHT_DONE",
    "STATE_PREFLIGHT_FAILED",
    "STATE_PREFLIGHT_RUNNING",
    "TERMINAL_EXECUTION_STATES",
    "V4_CLOSED_LOOP_RUNTIME_STATE_TABLE",
    "backfill_runtime_state_for_closed_loops",
    "can_transition",
    "canonicalize_runtime_state_market_ids",
    "claim_loop",
    "close_stale_review_only_loops",
    "complete_loop",
    "enqueue_execution",
    "ensure_runtime_state_table",
    "error_isolation_active",
    "fail_stuck_execution_loop",
    "fail_stuck_execution_loops",
    "isolation_cooldown_seconds",
    "load_loop_state",
    "load_error_isolation",
    "mark_execution_not_requested",
    "prune_orphan_runtime_state",
    "prune_legacy_unresolved_runtime_state",
    "prune_test_residue_runtime_state",
    "release_loop",
    "reclaim_expired_broadcast_claims",
    "reclaim_expired_review_claims",
    "reset_loop_for_new_cycle",
    "resolve_max_workers",
    "review_only_timeout_seconds",
    "scope_error_isolation_active",
    "transition_loop_state",
    "upsert_error_isolation",
    "upsert_loop_state",
]
