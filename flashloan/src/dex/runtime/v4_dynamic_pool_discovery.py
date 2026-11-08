"""Dynamic pool discovery for the Robinhood testnet V4 maintenance pipeline.

The fixed pool liquidity worker normally walks a cursor over candidate-eligible
pools that already exist in SQLite.  That cannot recover a direction whose
known pools were overwritten by NULL liquidity evidence or whose pool set was
never discovered.  This module adds the self-healing edge: when a directed
pair has fewer than ``MAX_DIRECTION_CANDIDATE_POOLS`` usable candidates, the
worker scans NULL/zero-liquidity raw pools for that pair and, if the route is
known, scans Initialize logs newest-to-oldest to discover pools that are not
yet in the raw table.

Only robinhood 4663 and robinhood-testnet 46630 are accepted.  All discovery
is dry-run capable and always records evidence, so operators can inspect what
the next dynamic scan would do before a maintenance window opens.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from core.db.direction_pool_score import (
    MAX_DIRECTION_CANDIDATE_POOLS,
    MIN_DIRECTION_CANDIDATE_POOLS,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_MARKET_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
)


SRC_ROOT = Path(__file__).resolve().parents[2] / "runtime"
PROTOCOL = "uniswap_v4"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
V4_DYNAMIC_POOL_DISCOVERY_DOMAIN = "v4_dynamic_pool_discovery_worker"
DEFAULT_DYNAMIC_POOL_SCAN_WINDOW_BLOCKS = 10_000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _robinhood_scope(network: str, chain_id: int) -> tuple[str, int, str]:
    """Return the canonical dynamic-discovery scope for Robinhood chains."""
    normalized = str(network or "").strip().lower()
    if normalized == ROBINHOOD_TESTNET_NETWORK or int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID:
        return ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_MARKET_ID
    return ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID, ROBINHOOD_MARKET_ID


def _scope_market_id(network: str, chain_id: int) -> str:
    return _robinhood_scope(network, chain_id)[2]


def _scope_pool_manager(network: str, chain_id: int) -> str:
    if _robinhood_scope(network, chain_id)[1] == ROBINHOOD_TESTNET_CHAIN_ID:
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
            ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        )

        return ROBINHOOD_TESTNET_V4_POOL_MANAGER
    from dex.intent_trade.chains.robinhood.config import V4_POOL_MANAGER_ADDRESS

    return V4_POOL_MANAGER_ADDRESS or _scope_pool_manager(ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID)


def _scope_rpc_url(network: str, chain_id: int) -> str:
    if _robinhood_scope(network, chain_id)[1] == ROBINHOOD_TESTNET_CHAIN_ID:
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
            _rpc_url_from_env_or_default,
        )

        return _rpc_url_from_env_or_default()
    from dex.intent_trade.chains.robinhood.v4_pool_rpc import (
        _rpc_url_from_env_or_default,
    )

    return _rpc_url_from_env_or_default()


def _scope_rpc_urls(network: str, chain_id: int) -> list[str]:
    if _robinhood_scope(network, chain_id)[1] == ROBINHOOD_TESTNET_CHAIN_ID:
        from dex.intent_trade.chains.robinhood.config import (
            testnet_rpc_urls_from_env,
        )

        urls = testnet_rpc_urls_from_env()
    else:
        from dex.intent_trade.chains.robinhood.config import (
            rpc_urls_from_env,
        )

        urls = rpc_urls_from_env()
    return [str(url).strip() for url in urls if str(url or "").strip()]


def _scope_pool_index_module(network: str, chain_id: int):
    if _robinhood_scope(network, chain_id)[1] == ROBINHOOD_TESTNET_CHAIN_ID:
        return __import__(
            "dex.intent_trade.chains.robinhood_testnet.v4_pool_index",
            fromlist=["build_v4_pool_index_from_initialize_logs", "build_v4_directional_pool_backfill"],
        )
    return __import__(
        "dex.intent_trade.chains.robinhood.v4_pool_index",
        fromlist=["build_v4_pool_index_from_initialize_logs", "build_v4_directional_pool_backfill"],
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_evidence(report: dict[str, Any]) -> Path:
    network = str(report.get("network") or ROBINHOOD_TESTNET_NETWORK).strip().lower()
    evidence_root = SRC_ROOT / "evidence" / network / "dynamic_pool_discovery"
    report_path = evidence_root / _stamp() / "report.json"
    _write_json(report_path, report)
    return report_path


def _validate_scope(network: str, chain_id: int) -> None:
    normalized = str(network or "").strip().lower()
    try:
        cid = int(chain_id or 0)
    except (TypeError, ValueError):
        cid = 0
    allowed = (
        normalized == ROBINHOOD_TESTNET_NETWORK and cid == ROBINHOOD_TESTNET_CHAIN_ID
    ) or (
        normalized == ROBINHOOD_NETWORK and cid == ROBINHOOD_CHAIN_ID
    )
    if not allowed:
        raise ValueError(
            "unsupported_scope: dynamic pool discovery only accepts "
            "robinhood 4663 or robinhood-testnet 46630"
        )


def _known_pool_token_addresses(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> list[str]:
    """Return the fixed token universe used by the liquidity worker."""
    from core.db.storage_business_io import query

    rows = query(
        database,
        """
        SELECT DISTINCT lower(token_address) AS token_address
        FROM chain_tokens
        WHERE network = ? AND chain_id = ?
          AND lower(COALESCE(json_extract(token_metadata_json, '$.category'), ''))
              NOT IN ('', 'unknown')
        ORDER BY token_address
        """,
        (network, chain_id),
    )
    addresses = [
        str(row.get("token_address") or "").strip().lower()
        for row in rows
        if str(row.get("token_address") or "").strip().lower().startswith("0x")
    ]
    if addresses:
        return sorted(set(addresses))
    rows = query(
        database,
        """
        SELECT DISTINCT lower(token0) AS token_address
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        UNION
        SELECT DISTINCT lower(token1) AS token_address
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, PROTOCOL, network, chain_id, PROTOCOL),
    )
    return sorted(
        {
            str(row.get("token_address") or "").strip().lower()
            for row in rows
            if str(row.get("token_address") or "").strip().lower().startswith("0x")
        }
    )


def _directed_pairs(route_addresses: list[str]) -> tuple[str, ...]:
    """Return the six directed edges of an A->B->C->A closed loop."""
    if len(route_addresses) < 4:
        return ()
    anchor = _text(route_addresses[0])
    y = _text(route_addresses[1])
    z = _text(route_addresses[2])
    if anchor != _text(route_addresses[3]):
        return ()
    pairs = [
        f"{anchor}:{y}",
        f"{y}:{anchor}",
        f"{y}:{z}",
        f"{z}:{y}",
        f"{z}:{anchor}",
        f"{anchor}:{z}",
    ]
    return tuple(dict.fromkeys(pairs))


def _directional_keys(token0: object, token1: object) -> tuple[str, str]:
    left = _text(token0)
    right = _text(token1)
    if not (left.startswith("0x") and right.startswith("0x")):
        return f"{left}:{right}", f"{right}:{left}"
    small, large = sorted((left, right))
    return f"{small}:{large}", f"{large}:{small}"


def _usable_candidate_pool(row: dict[str, Any]) -> bool:
    fee = row.get("fee")
    if fee is None:
        return False
    try:
        fee = int(fee)
    except (TypeError, ValueError):
        return False
    if fee <= 0 or fee > 3000 or bool(fee & 8388608):
        return False
    hooks = _text(row.get("hooks"))
    return hooks in {"", ZERO_ADDRESS}


def _candidate_counts(
    database: str,
    *,
    network: str,
    chain_id: int,
    directed_pairs: tuple[str, ...],
) -> tuple[dict[str, int], dict[str, list[str]], dict[str, list[str]], dict[str, list[str]], int]:
    """Return usable, NULL and explicit-zero pool ids for directed pairs."""
    from core.db.storage_business_io import query

    pairs: list[tuple[str, str]] = []
    for direction in directed_pairs:
        left, separator, right = str(direction).partition(":")
        if (
            separator
            and _text(left).startswith("0x")
            and len(_text(left)) == 42
            and _text(right).startswith("0x")
            and len(_text(right)) == 42
        ):
            pairs.append((_text(left), _text(right)))
    pairs = list(dict.fromkeys(pairs))
    counts = {direction: 0 for direction in directed_pairs}
    usable: dict[str, list[str]] = {direction: [] for direction in directed_pairs}
    null_pools: dict[str, list[str]] = {direction: [] for direction in directed_pairs}
    zero_pools: dict[str, list[str]] = {direction: [] for direction in directed_pairs}
    if not pairs:
        return counts, usable, null_pools, zero_pools, 0
    pair_placeholders = ",".join("(?, ?)" for _ in pairs)
    pair_params = [part for pair in pairs for part in pair]
    rows = query(
        database,
        f"""
        SELECT p.pool_id, p.token0, p.token1, p.fee, p.hooks,
               s.has_liquidity, s.liquidity_read_at, s.refreshed_at
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = p.network AND s.chain_id = p.chain_id
         AND s.protocol = p.protocol AND s.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND (
            (lower(p.token0), lower(p.token1)) IN ({pair_placeholders})
            OR (lower(p.token1), lower(p.token0)) IN ({pair_placeholders})
          )
        """,
        (network, chain_id, PROTOCOL, *pair_params, *pair_params),
    )
    for row in rows:
        pool_id = _text(row.get("pool_id"))
        if not pool_id.startswith("0x"):
            continue
        direction, reverse = _directional_keys(row.get("token0"), row.get("token1"))
        has_liquidity = row.get("has_liquidity")
        if int(has_liquidity or 0) == 1 and _usable_candidate_pool(row):
            for key in (direction, reverse):
                if key in counts:
                    counts[key] += 1
                    usable[key].append(pool_id)
        elif has_liquidity is None:
            for key in (direction, reverse):
                if key in counts:
                    null_pools[key].append(pool_id)
        elif int(has_liquidity or 0) == 0:
            for key in (direction, reverse):
                if key in counts:
                    zero_pools[key].append(pool_id)
    return counts, usable, null_pools, zero_pools, len(rows)


def _known_route_directions(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> list[str]:
    """Return distinct anchor route address sets from SQLite closed loops."""
    from core.db.storage_business_io import query

    rows = query(
        database,
        """
        SELECT path_addresses_json
        FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
          AND status = 'active'
          AND path_addresses_json IS NOT NULL AND path_addresses_json != ''
        ORDER BY updated_at DESC
        LIMIT 200
        """,
        (network, chain_id, PROTOCOL, _scope_market_id(network, chain_id)),
    )
    routes: list[list[str]] = []
    for row in rows:
        try:
            parsed = json.loads(str(row.get("path_addresses_json")))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, list) or len(parsed) < 4:
            continue
        addresses = [
            _text(address)
            for address in parsed
            if _text(address).startswith("0x") and len(_text(address)) == 42
        ]
        if _directed_pairs(addresses):
            routes.append(addresses)
    directions: list[str] = []
    seen: set[str] = set()
    for route in routes:
        for pair in _directed_pairs(route):
            if pair not in seen:
                seen.add(pair)
                directions.append(pair)
    return directions


def _direction_matches_pool(pool: dict[str, Any], directions: set[str]) -> bool:
    token0 = _text(pool.get("token0"))
    token1 = _text(pool.get("token1"))
    if not token0.startswith("0x") or not token1.startswith("0x"):
        return False
    left, right = sorted((token0, token1))
    return f"{left}:{right}" in directions or f"{right}:{left}" in directions


def _known_pair_sql(addresses: list[str]) -> tuple[str, tuple[str, ...]]:
    placeholders = ",".join("?" for _ in addresses)
    return (
        f"(lower(p.token0) IN ({placeholders}) AND lower(p.token1) IN ({placeholders}))",
        tuple(addresses) + tuple(addresses),
    )


def _anchor_directions(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> list[str]:
    """Return directed pairs observed in the fixed token universe.

    Candidate tables are a downstream product and can legitimately be empty
    after a bad import.  Dynamic discovery therefore reads the raw pool table
    instead.  Both endpoints must be categorized tokens, so the YC/ZC and
    Y-Z discovery boundary stays bounded by the configured universe.
    """
    from core.db.storage_business_io import query

    known_tokens = _known_pool_token_addresses(
        database,
        network=network,
        chain_id=chain_id,
    )
    if not known_tokens:
        return []
    pair_sql, pair_params = _known_pair_sql(known_tokens)
    rows = query(
        database,
        f"""
        SELECT DISTINCT lower(p.token0) AS token0, lower(p.token1) AS token1
        FROM v4_exchange_pools p
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND {pair_sql}
        ORDER BY token0, token1
        """,
        (network, chain_id, PROTOCOL, *pair_params),
    )
    tokens = sorted(
        {
            _text(address)
            for row in rows
            for address in (row.get("token0"), row.get("token1"))
            if _text(address).startswith("0x") and len(_text(address)) == 42
        }
    )
    directions: list[str] = []
    seen: set[str] = set()
    for row in rows:
        token0 = _text(row.get("token0"))
        token1 = _text(row.get("token1"))
        if not (token0.startswith("0x") and token1.startswith("0x")):
            continue
        small, large = sorted((token0, token1))
        for pair in (f"{small}:{large}", f"{large}:{small}"):
            if pair in seen:
                continue
            seen.add(pair)
            directions.append(pair)
    return directions


def _new_pool_candidates(
    database: str,
    *,
    network: str,
    chain_id: int,
    known_token_addresses: list[str],
    directed_pairs: tuple[str, ...] = (),
    limit: int = 128,
) -> list[str]:
    """Return fixed-universe pools that still need a live verdict.

    Both sides must be in the known token universe.  Requiring only one side
    would admit arbitrary pools that happen to touch a known anchor and would
    recreate the 39k-pool scan that this worker is meant to avoid.
    """
    from core.db.storage_business_io import query

    addresses = sorted(
        {
            str(address).strip().lower()
            for address in known_token_addresses
            if str(address or "").strip().lower().startswith("0x")
        }
    )
    if not addresses:
        return []
    pair_sql, pair_params = _known_pair_sql(addresses)
    direction_set = {
        _text(direction)
        for direction in directed_pairs
        if ":" in _text(direction)
    }
    rows = query(
        database,
        f"""
        SELECT lower(p.pool_id) AS pool_id, p.token0, p.token1
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = p.network AND s.chain_id = p.chain_id
         AND s.protocol = p.protocol AND s.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND p.fee > 0 AND p.fee <= 3000 AND (p.fee & 8388608) = 0
          AND (COALESCE(p.hooks, '') = '' OR lower(p.hooks) = ?)
          AND (s.pool_id IS NULL OR s.has_liquidity IS NULL)
          AND {pair_sql}
        ORDER BY lower(p.pool_id)
        LIMIT ?
        """,
        (network, chain_id, PROTOCOL, ZERO_ADDRESS, *pair_params, int(limit)),
    )
    return [
        str(row.get("pool_id") or "").strip().lower()
        for row in rows
        if str(row.get("pool_id") or "").strip().lower().startswith("0x")
        and (not direction_set or _direction_matches_pool(row, direction_set))
    ]


def dynamic_pool_discovery_plan(
    *,
    database: str,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    route_addresses: list[str] | None = None,
    limit: int = 128,
    direction_target: int | None = None,
) -> dict[str, Any]:
    """Return the dynamic discovery plan without writing SQLite rows."""
    _validate_scope(network, chain_id)
    target = max(
        MIN_DIRECTION_CANDIDATE_POOLS,
        min(
            MAX_DIRECTION_CANDIDATE_POOLS,
            int(direction_target) if direction_target is not None else MAX_DIRECTION_CANDIDATE_POOLS,
        ),
    )
    directed_pairs = _directed_pairs(route_addresses or [])
    if not directed_pairs:
        directed_pairs = tuple(dict.fromkeys(_known_route_directions(database, network=network, chain_id=chain_id)))
    if not directed_pairs:
        directed_pairs = tuple(dict.fromkeys(_anchor_directions(database, network=network, chain_id=chain_id)))
    counts, usable, null_pools, zero_pools, raw_pool_count = _candidate_counts(
        database,
        network=network,
        chain_id=chain_id,
        directed_pairs=directed_pairs,
    )
    short_directions = [
        direction
        for direction in directed_pairs
        if counts.get(direction, 0) < target
    ]
    known_token_addresses = _known_pool_token_addresses(
        database,
        network=network,
        chain_id=chain_id,
    )
    recoverable_pool_ids = sorted(
        {
            pool_id
            for direction in short_directions
            for pool_id in (null_pools.get(direction) or []) + (zero_pools.get(direction) or [])
        }
    )
    new_pool_ids = (
        _new_pool_candidates(
            database,
            network=network,
            chain_id=chain_id,
            known_token_addresses=known_token_addresses,
            directed_pairs=tuple(short_directions),
            limit=limit,
        )
        if short_directions
        else []
    )
    return {
        "ok": True,
        "network": network,
        "chainId": chain_id,
        "database": database,
        "schemaVersion": "robinhood-v4-dynamic-pool-discovery-plan-v1",
        "generatedAt": _utc_now_iso(),
        "directionTarget": target,
        "minimumDirectionTarget": int(MIN_DIRECTION_CANDIDATE_POOLS),
        "maximumDirectionTarget": int(MAX_DIRECTION_CANDIDATE_POOLS),
        "directionCount": len(directed_pairs),
        "shortDirectionCount": len(short_directions),
        "shortDirections": short_directions,
        "counts": counts,
        "usablePoolIdsByDirection": usable,
        "nullLiquidityPoolIdsByDirection": null_pools,
        "zeroLiquidityPoolIdsByDirection": zero_pools,
        "recoverablePoolIds": recoverable_pool_ids,
        "recoverablePoolCount": len(recoverable_pool_ids),
        "candidateTransferTargetCount": len(recoverable_pool_ids),
        "noLiquidityPoolIds": recoverable_pool_ids,
        "noLiquidityPoolCount": len(recoverable_pool_ids),
        "newEligiblePoolIds": new_pool_ids,
        "newEligiblePoolCount": len(new_pool_ids),
        "rawPoolCount": raw_pool_count,
        "knownTokenAddressCount": len(known_token_addresses),
        "rpcUrl": _scope_rpc_url(network, chain_id),
        "poolManager": _scope_pool_manager(network, chain_id),
        "writesSqlite": False,
    }


def run_dynamic_pool_discovery(
    *,
    database: str,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    route_addresses: list[str] | None = None,
    limit: int = 128,
    dry_run: bool = True,
    direction_target: int | None = None,
) -> dict[str, Any]:
    """Write evidence for a dynamic discovery plan; SQLite writes are opt-in."""
    try:
        report = dynamic_pool_discovery_plan(
            database=database,
            network=network,
            chain_id=chain_id,
            route_addresses=route_addresses,
            limit=limit,
            direction_target=direction_target,
        )
        report["dryRun"] = bool(dry_run)
        report["phase"] = "dry_run" if dry_run else "writes_sqlite"
        if not dry_run:
            # The liquidity worker owns live SQLite writes. A non-dry-run
            # invocation must pass the recovered/new pool ids back to that
            # worker; this module intentionally does not mutate raw tables.
            report["phase"] = "pool_ids_ready_for_liquidity_worker"
            report["writesSqlite"] = False
        evidence_path = _write_evidence(report)
        report["evidencePath"] = str(evidence_path)
        return report
    except Exception as exc:
        error_report = {
            "ok": False,
            "network": network,
            "chainId": chain_id,
            "error": str(exc)[:800],
            "generatedAt": _utc_now_iso(),
            "dryRun": bool(dry_run),
            "writesSqlite": False,
        }
        evidence_path = _write_evidence(error_report)
        error_report["evidencePath"] = str(evidence_path)
        return error_report


def dynamic_discovery_eligible_pool_ids(
    *,
    database: str,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    route_addresses: list[str] | None = None,
    limit: int = 128,
    direction_target: int | None = None,
) -> list[str]:
    """Return pool ids the liquidity worker should refresh next.

    The result combines:
    1. short-direction NULL/zero raw pools that may have recovered liquidity;
    2. fixed-fee/no-hook known-token pools that still lack an explicit verdict.
    The caller runs this before ``refresh_v4_live(skip_index=True, ...)`` so a
    live batch can turn them back into usable candidates.
    """
    plan = dynamic_pool_discovery_plan(
        database=database,
        network=network,
        chain_id=chain_id,
        route_addresses=route_addresses,
        limit=limit,
        direction_target=direction_target,
    )
    return sorted(
        {
            *(plan.get("recoverablePoolIds") or []),
            *(plan.get("newEligiblePoolIds") or []),
        }
    )


def _load_discovery_completeness(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    raw = row.get("completeness_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def load_dynamic_pool_discovery_cursor(
    database: str,
    *,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    protocol: str = PROTOCOL,
) -> dict[str, Any]:
    """Return the persisted next-block cursor for incremental Initialize scans."""
    from core.db.storage_chain_core import load_initialization

    row = load_initialization(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=V4_DYNAMIC_POOL_DISCOVERY_DOMAIN,
    )
    completeness = _load_discovery_completeness(row)

    def _int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    return {
        "next_from_block": _int(completeness.get("next_from_block")),
        "last_scanned_block": _int(completeness.get("last_scanned_block")),
        "direction_target": _int(
            completeness.get("direction_target"),
            MAX_DIRECTION_CANDIDATE_POOLS,
        ),
        "short_directions": completeness.get("short_directions") or [],
        "pool_manager": str(completeness.get("pool_manager") or "").strip(),
        "mode": str(completeness.get("mode") or "idle"),
        "error": completeness.get("error") or None,
        "refreshed_at": row.get("refreshed_at") if isinstance(row, dict) else None,
    }


def save_dynamic_pool_discovery_cursor(
    database: str,
    *,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    protocol: str = PROTOCOL,
    next_from_block: int = 0,
    last_scanned_block: int = 0,
    direction_target: int = MAX_DIRECTION_CANDIDATE_POOLS,
    short_directions: list[str] | None = None,
    pool_manager: str | None = None,
    mode: str = "idle",
    error: str | None = None,
) -> dict[str, Any]:
    """Persist one incremental dynamic-discovery checkpoint."""
    from core.db.storage_chain_core import upsert_initialization

    completeness = {
        "next_from_block": max(0, int(next_from_block)),
        "last_scanned_block": max(0, int(last_scanned_block)),
        "direction_target": max(1, int(direction_target)),
        "short_directions": [
            str(item).strip().lower()
            for item in (short_directions or [])
            if ":" in str(item or "")
        ],
        "pool_manager": str(pool_manager or "").strip(),
        "mode": mode,
        "error": error,
        "observedAt": _utc_now_iso(),
    }
    upsert_initialization(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=V4_DYNAMIC_POOL_DISCOVERY_DOMAIN,
        status="ok" if not error else "error",
        completeness=completeness,
        error={"message": error} if error else None,
    )
    return completeness


def _known_token_set(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> set[str]:
    return {
        str(address).strip().lower()
        for address in _known_pool_token_addresses(
            database,
            network=network,
            chain_id=chain_id,
        )
        if str(address or "").strip().lower().startswith("0x")
    }


def _existing_raw_pool_keys(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> set[str]:
    """Return ``currency0:currency1`` pairs already persisted in raw SQLite."""
    from core.db.storage_business_io import query

    rows = query(
        database,
        """
        SELECT DISTINCT lower(token0) AS token0, lower(token1) AS token1
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, PROTOCOL),
    )
    result: set[str] = set()
    for row in rows:
        token0 = str(row.get("token0") or "").strip().lower()
        token1 = str(row.get("token1") or "").strip().lower()
        if token0.startswith("0x") and token1.startswith("0x"):
            small, large = sorted((token0, token1))
            result.add(f"{small}:{large}")
    return result


def _latest_block(
    rpc_url: str,
    timeout_seconds: float = 20.0,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    rpc_urls: list[str] | None = None,
) -> int:
    """Return the current Robinhood chain block number with RPC retry/failover."""
    import json as _json
    import time as _time
    import urllib.request as _url_request

    from dex.intent_trade.chains.robinhood_common.rpc_rate_limit import (
        throttle_rpc_call,
    )

    urls: list[str] = []
    if str(rpc_url or "").strip():
        urls.append(str(rpc_url).strip())
    for url in rpc_urls or []:
        if str(url or "").strip() and str(url).strip() not in urls:
            urls.append(str(url).strip())
    if not urls:
        raise RuntimeError("no_rpc_url")

    last_error = "no_rpc_attempt"
    for attempt in range(max(1, int(attempts))):
        for url in urls:
            try:
                throttle_rpc_call(url)
                body = _json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
                ).encode("utf-8")
                request = _url_request.Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "flashloan-robinhood-v4-dynamic/1.0",
                        "Connection": "close",
                    },
                    method="POST",
                )
                with _url_request.urlopen(
                    request, timeout=max(1.0, float(timeout_seconds))
                ) as response:
                    payload = _json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and payload.get("error"):
                    raise RuntimeError(f"eth_blockNumber error: {payload['error']}")
                raw = payload.get("result") if isinstance(payload, dict) else None
                if raw is None:
                    raise RuntimeError("eth_blockNumber returned no result")
                return int(str(raw), 16) if str(raw).startswith("0x") else int(raw)
            except Exception as exc:
                last_error = str(exc)
        if attempt + 1 < max(1, int(attempts)):
            _time.sleep(max(0.0, float(retry_delay_seconds)))
    raise RuntimeError(last_error)


def run_dynamic_pool_index_slice(
    *,
    database: str,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    window_blocks: int = DEFAULT_DYNAMIC_POOL_SCAN_WINDOW_BLOCKS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan one incremental Initialize window and persist newly found pools.

    The cursor stores the next ``from_block`` so the fixed pool-liquidity
    worker only scans a bounded window per tick.  Discovered rows are filtered
    to the fixed token universe before ``import_v4_evidence_rows`` writes the
    raw pool table; the follow-up liquidity refresh then prices and adds them
    back to the candidate YC/ZC stage.
    """
    _validate_scope(network, chain_id)
    window = max(1, int(window_blocks))
    cursor = load_dynamic_pool_discovery_cursor(
        database,
        network=network,
        chain_id=chain_id,
    )
    known = _known_token_set(database, network=network, chain_id=chain_id)
    plan = dynamic_pool_discovery_plan(
        database=database,
        network=network,
        chain_id=chain_id,
        direction_target=int(cursor.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS),
    )
    targeted_pool_ids = sorted(
        {
            *(plan.get("recoverablePoolIds") or []),
            *(plan.get("newEligiblePoolIds") or []),
        }
    )
    if targeted_pool_ids:
        result = {
            "ok": True,
            "phase": "targeted_verification_pending",
            "poolIds": targeted_pool_ids,
            "poolCount": len(targeted_pool_ids),
            "shortDirectionCount": int(plan.get("shortDirectionCount") or 0),
            "shortDirections": plan.get("shortDirections") or [],
            "writesSqlite": False,
            "dryRun": bool(dry_run),
        }
        if not dry_run:
            save_dynamic_pool_discovery_cursor(
                database,
                network=network,
                chain_id=chain_id,
                next_from_block=int(cursor.get("next_from_block") or 0),
                last_scanned_block=int(cursor.get("last_scanned_block") or 0),
                direction_target=int(
                    cursor.get("direction_target")
                    or MAX_DIRECTION_CANDIDATE_POOLS
                ),
                short_directions=plan.get("shortDirections") or [],
                pool_manager=str(
                    pool_manager or _scope_pool_manager(network, chain_id)
                ),
                mode="targeted_pools_pending",
            )
        return result
    from core.db.v4_scan_state import load_v4_scan_state

    scan_state = load_v4_scan_state(
        database,
        network=network,
        chain_id=chain_id,
    )
    next_from_block = max(
        0,
        int(
            cursor.get("next_from_block")
            if int(cursor.get("next_from_block") or 0) > 0
            else (int(scan_state.get("last_scanned_block") or 0) + 1)
        ),
    )
    resolved_pool_manager = str(
        pool_manager or _scope_pool_manager(network, chain_id)
    )
    rpc = str(rpc_url or _scope_rpc_url(network, chain_id)).strip()
    if not rpc:
        result = {
            "ok": False,
            "phase": "rpc_unavailable",
            "error": "no_rpc_url",
            "nextFromBlock": next_from_block,
            "lastScannedBlock": int(cursor.get("last_scanned_block") or 0),
            "windowBlocks": window,
            "writesSqlite": False,
            "dryRun": bool(dry_run),
        }
        if not dry_run:
            save_dynamic_pool_discovery_cursor(
                database,
                network=network,
                chain_id=chain_id,
                next_from_block=next_from_block,
                last_scanned_block=int(cursor.get("last_scanned_block") or 0),
                direction_target=int(cursor.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS),
                short_directions=plan.get("shortDirections") or [],
                pool_manager=resolved_pool_manager,
                mode="rpc_unavailable",
                error="no_rpc_url",
            )
        return result

    pool_index_module = _scope_pool_index_module(network, chain_id)
    build_v4_pool_index_from_initialize_logs = (
        pool_index_module.build_v4_pool_index_from_initialize_logs
    )

    try:
        latest_block = _latest_block(
            rpc,
            rpc_urls=_scope_rpc_urls(network, chain_id),
        )
        scan_to_block = min(
            latest_block,
            next_from_block + window - 1,
        )
        if next_from_block > latest_block:
            index_payload = {
                "ok": True,
                "pools": [],
                "poolKeys": [],
                "toBlock": latest_block,
                "fromBlock": next_from_block,
                "initializeLogCount": 0,
                "generatedBlockNumber": latest_block,
            }
        else:
            index_payload = build_v4_pool_index_from_initialize_logs(
                rpc_url=rpc,
                pool_manager=resolved_pool_manager,
                from_block=next_from_block,
                to_block=scan_to_block,
                chunk_size=window,
                pool_limit=0,
                non_eth_only=False,
                include_metadata=False,
                mark_recent_swaps=False,
            )
    except Exception as exc:
        error_text = str(exc)
        result = {
            "ok": False,
            "phase": "index_scan_failed",
            "error": error_text[:800],
            "nextFromBlock": next_from_block,
            "lastScannedBlock": int(cursor.get("last_scanned_block") or 0),
            "windowBlocks": window,
            "writesSqlite": False,
            "dryRun": bool(dry_run),
        }
        if not dry_run:
            save_dynamic_pool_discovery_cursor(
                database,
                network=network,
                chain_id=chain_id,
                next_from_block=next_from_block,
                last_scanned_block=int(cursor.get("last_scanned_block") or 0),
                direction_target=int(cursor.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS),
                short_directions=plan.get("shortDirections") or [],
                pool_manager=resolved_pool_manager,
                mode="index_scan_failed",
                error=error_text,
            )
        return result

    scanned_to_block = int(index_payload.get("toBlock") or next_from_block)
    candidate_pools: list[dict[str, Any]] = []
    for pool in index_payload.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        token0 = str(pool.get("token0") or "").strip().lower()
        token1 = str(pool.get("token1") or "").strip().lower()
        if not token0.startswith("0x") or not token1.startswith("0x"):
            continue
        if token0 not in known or token1 not in known:
            continue
        candidate_pools.append(pool)

    import_result: dict[str, Any] = {}
    if candidate_pools and not dry_run:
        from dex.tools.v4_rust_refresh import import_v4_evidence_rows

        import_payload = {
            **index_payload,
            "pools": candidate_pools,
            "poolKeys": [
                pool.get("poolKey")
                for pool in candidate_pools
                if isinstance(pool.get("poolKey"), dict)
            ],
        }
        try:
            import_result = import_v4_evidence_rows(
                database=database,
                network=network,
                chain_id=chain_id,
                index_payload=import_payload,
                liquidity_payload=None,
                protocol=PROTOCOL,
                replace=False,
            )
        except Exception as exc:
            import_result = {"ok": False, "error": str(exc)[:800]}

    discovered_pool_ids = sorted(
        {
            str(pool.get("poolId") or "").strip().lower()
            for pool in candidate_pools
            if str(pool.get("poolId") or "").strip().lower().startswith("0x")
        }
    )
    result = {
        "ok": True,
        "phase": "incremental_window_scanned",
        "network": network,
        "chainId": chain_id,
        "database": database,
        "poolManager": resolved_pool_manager,
        "nextFromBlock": max(0, int(scanned_to_block) + 1),
        "lastScannedBlock": int(scanned_to_block),
        "scannedBlockCount": max(0, int(scanned_to_block) - int(next_from_block) + 1),
        "windowBlocks": window,
        "initializeLogCount": int(index_payload.get("initializeLogCount") or 0),
        "knownTokenPairCount": len(
            {
                ":".join(sorted((pool.get("token0"), pool.get("token1"))))
                for pool in candidate_pools
            }
        ),
        "discoveredPoolCount": len(discovered_pool_ids),
        "discoveredPoolIds": discovered_pool_ids,
        "shortDirectionCount": int(plan.get("shortDirectionCount") or 0),
        "shortDirections": plan.get("shortDirections") or [],
        "directionTarget": int(cursor.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS),
        "existingRawPairCount": len(
            _existing_raw_pool_keys(database, network=network, chain_id=chain_id)
        ),
        "import": import_result,
        "writesSqlite": bool(import_result.get("ok")),
        "dryRun": bool(dry_run),
    }
    if not dry_run:
        save_dynamic_pool_discovery_cursor(
            database,
            network=network,
            chain_id=chain_id,
            next_from_block=result["nextFromBlock"],
            last_scanned_block=int(scanned_to_block),
            direction_target=int(cursor.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS),
            short_directions=plan.get("shortDirections") or [],
            pool_manager=resolved_pool_manager,
            mode=(
                "complete"
                if (not candidate_pools or import_result.get("ok"))
                else "import_failed"
            ),
            error=(
                str(import_result.get("error"))
                if candidate_pools and import_result and not import_result.get("ok")
                else None
            ),
        )
    return result


__all__ = [
    "dynamic_discovery_eligible_pool_ids",
    "dynamic_pool_discovery_plan",
    "load_dynamic_pool_discovery_cursor",
    "run_dynamic_pool_index_slice",
    "run_dynamic_pool_discovery",
    "save_dynamic_pool_discovery_cursor",
]
