from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from core.evidence_scope import evidence_scope_matches, infer_evidence_scope
from core.db.storage_common import db_connection, db_transaction
from core.db.cow_execution_mapping import scope_consistency_reasons
from core.db.cow_execution_review import (
    _attempt_route_address_identity,
    _build_review_summary,
    _market_state_summary,
)

DEFAULT_COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_RETENTION_LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE = "not_executable"
COW_ATTEMPT_CATEGORY_EXECUTION_FAILED = "execution_failed"
COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS = "execution_success"
COW_ATTEMPT_CATEGORY_RETENTION_DAYS = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: 2,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: 14,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: None,
}
COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: 1,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: 7,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: None,
}
COW_ATTEMPT_CATEGORY_RETENTION_POLICY = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: {
        "unit": "day",
        "active_bucket_days": 1,
        "storage_buckets": 2,
        "storage_days": 2,
        "timezone": "Asia/Shanghai",
        "description": "current and previous local-day buckets",
    },
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: {
        "unit": "iso_week",
        "active_bucket_days": 7,
        "storage_buckets": 2,
        "storage_days": 14,
        "timezone": "Asia/Shanghai",
        "description": "current and previous local ISO-week buckets",
    },
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: {
        "unit": "forever",
        "active_bucket_days": None,
        "storage_buckets": None,
        "storage_days": None,
        "timezone": "Asia/Shanghai",
        "description": "long-term retention",
    },
}
COW_ATTEMPT_CATEGORY_LABELS = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: "not executable",
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: "execution failed",
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: "execution success",
}

_SUCCESS_STATES = {
    "confirmed_success",
    "confirmation_success",
    "settled",
    "settlement_success",
    "executed",
    "execution_success",
    "success",
    "submitted_success",
}
_SUCCESS_PHASES = {"confirmed_success", "settled", "execution_success"}
_NOT_EXECUTABLE_STATES = {
    "market_candidate",
    "quote_required",
    "quote_failed",
    "unsupported",
    "unsupported_route",
    "unsupported_token",
    "live_quote_failed",
    "live_quote_verified",
    "ready_for_transaction_development",
    "transaction_development_required",
    "missing_token",
    "quote_unavailable",
    "flashloan_payload_required",
    "price_guard_failed",
    "submission_paused",
    "profit_below_threshold",
    "not_profitable",
    "checks_failed",
    "order_disabled",
}
_FAILED_STATES = {
    "checks_passed_order_disabled",
    "limit_order_ready_not_submitted",
    "limit_order_ready_to_submit",
    "ready_not_submitted",
    "ready_to_submit",
    "submission_failed",
    "submit_failed",
    "order_failed",
    "execution_failed",
    "settlement_failed",
    "confirmation_failed",
    "confirmed_failed",
    "failed",
    "error",
}
_EXECUTION_PHASES = {
    "order_submission",
    "submission",
    "execution",
    "settlement",
    "confirmation",
}




































from core.db.storage_cow_policy import (
    _category_retention_cutoff,
    _category_within_retention,
    _cow_control_mode_from_attempt,
    _cow_intent_from_attempt,
    _cow_sdk_result_from_attempt,
    _cow_sdk_status_from_attempt,
    _decorate_attempt_review,
    _json_loads,
    _json_text,
    _local_iso_week_start,
    _local_midnight,
    _parse_datetime,
    _route_hop_constraints_enforced_from_attempt,
    cow_execution_attempt_analysis_days,
    cow_execution_attempt_category,
    cow_execution_attempt_retention_days,
    cow_execution_attempt_retention_policy,
)
from core.db.storage_cow_candidate_builders import (
    _attempt_signature,
    _is_market_candidate,
    build_cow_execution_attempts,
    build_cow_market_candidate_attempts,
    build_cow_market_claim_candidate_attempts,
    build_cow_market_claim_pairs,
)

def ensure_cow_execution_attempts_table(database_url: str) -> None:
    with db_connection(database_url, connect_timeout=8) as connection:
        with db_transaction(connection, operation="cow.execution_attempts"):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cow_execution_attempts (
                        id BIGSERIAL PRIMARY KEY,
                        observed_at TIMESTAMPTZ,
                        network TEXT NOT NULL,
                        chain_id INTEGER,
                        owner_address TEXT,
                        pair TEXT,
                        pair_rank INTEGER,
                        priority_reason TEXT,
                        route_path_json TEXT,
                        state TEXT NOT NULL,
                        execution_phase TEXT NOT NULL,
                        checks_passed BOOLEAN NOT NULL DEFAULT FALSE,
                        can_submit_order BOOLEAN NOT NULL DEFAULT FALSE,
                        order_submission_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                        auto_execute_requested BOOLEAN NOT NULL DEFAULT FALSE,
                        control_mode TEXT,
                        route_hop_constraints_enforced BOOLEAN NOT NULL DEFAULT FALSE,
                        final_delta_amount TEXT,
                        final_symbol TEXT,
                        blocked_reasons_json TEXT,
                        cow_flashloan_intent_json TEXT,
                        cow_sdk_result_json TEXT,
                        quote_json TEXT,
                        precheck_json TEXT,
                        market_state_json TEXT,
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_network_time "
                    "ON cow_execution_attempts(network, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_state_time "
                    "ON cow_execution_attempts(state, created_at DESC)"
                )
                cursor.execute("ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS control_mode TEXT")
                cursor.execute(
                    "ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS "
                    "route_hop_constraints_enforced BOOLEAN NOT NULL DEFAULT FALSE"
                )
                cursor.execute("ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS cow_flashloan_intent_json TEXT")
                cursor.execute("ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS cow_sdk_result_json TEXT")


def prune_cow_execution_attempts(database_url: str, retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS) -> int:
    ensure_cow_execution_attempts_table(database_url)
    now = datetime.now(timezone.utc)
    prune_scan_before = _local_midnight(now).astimezone(timezone.utc)
    with db_connection(database_url, connect_timeout=8) as connection:
        with db_transaction(connection, operation="cow.execution_history"):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        id, observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        error, created_at, quote_json, precheck_json, market_state_json
                    FROM cow_execution_attempts
                    WHERE created_at < %s
                    LIMIT 5000
                    """,
                    (prune_scan_before,),
                )
                expired_ids = []
                for row in cursor.fetchall():
                    item = {
                        "id": int(row[0]),
                        "observed_at": row[1],
                        "network": row[2],
                        "chain_id": row[3],
                        "owner_address": row[4],
                        "pair": row[5],
                        "pair_rank": row[6],
                        "priority_reason": row[7],
                        "route_path": _json_loads(row[8], []),
                        "state": row[9],
                        "execution_phase": row[10],
                        "checks_passed": bool(row[11]),
                        "can_submit_order": bool(row[12]),
                        "order_submission_enabled": bool(row[13]),
                        "auto_execute_requested": bool(row[14]),
                        "final_delta_amount": row[15],
                        "final_symbol": row[16],
                        "blocked_reasons": _json_loads(row[17], []),
                        "error": row[18],
                        "created_at": row[19],
                        "quote": _json_loads(row[20], {}),
                        "precheck": _json_loads(row[21], {}),
                        "market_state": _json_loads(row[22], {}),
                    }
                    if not _category_within_retention(item, now=now):
                        expired_ids.append(int(row[0]))
                if not expired_ids:
                    return 0
                cursor.execute("DELETE FROM cow_execution_attempts WHERE id = ANY(%s)", (expired_ids,))
                return int(cursor.rowcount or 0)


# === WRITE OPERATIONS ===
# These helpers create, deduplicate, prune, and persist attempt records.

def record_cow_execution_attempts(
    database_url: str,
    attempts: list[dict[str, Any]],
    *,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    dedupe_market_candidates: bool = True,
) -> list[int]:
    if not attempts:
        return []
    ensure_cow_execution_attempts_table(database_url)
    if retention_days:
        prune_cow_execution_attempts(database_url, retention_days=retention_days)
    ids = []
    with db_connection(database_url, connect_timeout=8) as connection:
        with db_transaction(connection, operation="cow.candidate_queue"):
            with connection.cursor() as cursor:
                for item in attempts:
                    route_path_json = _json_text(item.get("route_path") or [])
                    if dedupe_market_candidates and _is_market_candidate(item):
                        cursor.execute(
                            """
                            SELECT id, quote_json
                            FROM cow_execution_attempts
                            WHERE observed_at IS NOT DISTINCT FROM %s
                              AND network IS NOT DISTINCT FROM %s
                              AND pair IS NOT DISTINCT FROM %s
                              AND pair_rank IS NOT DISTINCT FROM %s
                              AND priority_reason IS NOT DISTINCT FROM %s
                              AND route_path_json IS NOT DISTINCT FROM %s
                              AND execution_phase IS NOT DISTINCT FROM %s
                            LIMIT 50
                            """,
                            (
                                item.get("observed_at"),
                                item.get("network"),
                                item.get("pair"),
                                item.get("pair_rank"),
                                item.get("priority_reason"),
                                route_path_json,
                                item.get("execution_phase") or "market_candidate",
                            ),
                        )
                        duplicate = False
                        item_signature = _attempt_signature(item)
                        for existing_id, quote_json in cursor.fetchall():
                            existing_item = {
                                **item,
                                "id": existing_id,
                                "quote": _json_loads(quote_json, {}),
                            }
                            if _attempt_signature(existing_item) == item_signature:
                                duplicate = True
                                break
                        if duplicate:
                            continue
                    cursor.execute(
                        """
                        INSERT INTO cow_execution_attempts (
                            observed_at, network, chain_id, owner_address,
                            pair, pair_rank, priority_reason, route_path_json,
                            state, execution_phase, checks_passed, can_submit_order,
                            order_submission_enabled, auto_execute_requested,
                            control_mode, route_hop_constraints_enforced,
                            final_delta_amount, final_symbol, blocked_reasons_json,
                            cow_flashloan_intent_json, cow_sdk_result_json,
                            quote_json, precheck_json, market_state_json, error, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING id
                        """,
                        (
                            item.get("observed_at"),
                            item.get("network"),
                            item.get("chain_id"),
                            item.get("owner_address"),
                            item.get("pair"),
                            item.get("pair_rank"),
                            item.get("priority_reason"),
                            route_path_json,
                            item.get("state") or "unknown",
                            item.get("execution_phase") or "quote_precheck",
                            bool(item.get("checks_passed")),
                            bool(item.get("can_submit_order")),
                            bool(item.get("order_submission_enabled")),
                            bool(item.get("auto_execute_requested")),
                            _cow_control_mode_from_attempt(item),
                            _route_hop_constraints_enforced_from_attempt(item),
                            item.get("final_delta_amount"),
                            item.get("final_symbol"),
                            _json_text(item.get("blocked_reasons") or []),
                            _json_text(_cow_intent_from_attempt(item)),
                            _json_text(_cow_sdk_result_from_attempt(item)),
                            _json_text(item.get("quote") or {}),
                            _json_text(item.get("precheck") or {}),
                            _json_text(item.get("market_state") or {}),
                            item.get("error"),
                        ),
                    )
                    row = cursor.fetchone()
                    if row:
                        ids.append(int(row[0]))
    return ids


def _jsonl_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return _attempt_signature(row)


def _within_retention(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    created_at = _parse_datetime(row.get("created_at") or row.get("observed_at"))
    return created_at is None or created_at >= cutoff


def append_cow_execution_attempts_jsonl(
    path: Path,
    attempts: list[dict[str, Any]],
    *,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    dedupe_market_candidates: bool = True,
) -> int:
    if not attempts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    existing_rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and _category_within_retention(row, now=now):
                existing_rows.append(row)
    market_signatures = {
        _jsonl_signature(row)
        for row in existing_rows
        if _is_market_candidate(row)
    }
    additions = []
    for item in attempts:
        row = {**item, "created_at": created_at}
        if scope_consistency_reasons(row):
            # A contradictory evidence record must never enter the durable log.
            continue
        if dedupe_market_candidates and _is_market_candidate(row):
            signature = _jsonl_signature(row)
            if signature in market_signatures:
                continue
            market_signatures.add(signature)
        additions.append(row)
    rows = [*existing_rows, *additions]
    with path.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(_json_text(item) + "\n")
    return len(additions)


def _jsonl_source_paths(path: Path) -> list[Path]:
    if path.is_dir():
        paths = [item for item in path.rglob("*.jsonl") if item.is_file()]
        legacy_file = path.with_suffix(".jsonl")
        if legacy_file.exists() and legacy_file.is_file():
            paths.append(legacy_file)
        return sorted({item for item in paths})
    return [path]


def clear_cow_execution_attempts_jsonl(
    path: Path,
    *,
    networks: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    source_paths = [item for item in _jsonl_source_paths(path) if item.exists()]
    if not source_paths:
        return {"cleared": 0, "remaining": 0, "source": "jsonl", "error": None}
    wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    wanted_category = str(category or "").strip().lower()
    cleared = 0
    remaining = 0
    for source_path in source_paths:
        kept_rows = []
        for line in source_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                kept_rows.append(line)
                continue
            if not isinstance(row, dict):
                kept_rows.append(line)
                continue
            network_matches = not wanted or str(row.get("network") or "").strip().lower() in wanted
            category_matches = not wanted_category or cow_execution_attempt_category(row) == wanted_category
            if network_matches and category_matches:
                cleared += 1
                continue
            kept_rows.append(_json_text(row))
        source_path.parent.mkdir(parents=True, exist_ok=True)
        with source_path.open("w", encoding="utf-8") as handle:
            for item in kept_rows:
                handle.write(str(item) + "\n")
        remaining += len(kept_rows)
    return {"cleared": cleared, "remaining": remaining, "source": "jsonl", "error": None}


def clear_cow_execution_attempts(
    database_url: str,
    *,
    networks: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    ensure_cow_execution_attempts_table(database_url)
    wanted = [str(item).strip().lower() for item in networks or [] if str(item).strip()]
    wanted_category = str(category or "").strip().lower()
    where = []
    params: list[Any] = []
    if wanted:
        where.append("LOWER(network) = ANY(%s)")
        params.append(wanted)
    if wanted_category:
        rows = load_recent_cow_execution_attempts(
            database_url,
            limit=100_000,
            networks=wanted,
            category=wanted_category,
            retention_days=DEFAULT_COW_EXECUTION_RETENTION_DAYS,
        )
        ids = [int(row["id"]) for row in rows if row.get("id") is not None]
        if not ids:
            return {"cleared": 0, "source": "database", "error": None}
        where.append("id = ANY(%s)")
        params.append(ids)
    query = "DELETE FROM cow_execution_attempts"
    if where:
        query += " WHERE " + " AND ".join(where)
    with db_connection(database_url, connect_timeout=8) as connection:
        with db_transaction(connection, operation="cow.execution_attempts_clear"):
            with connection.cursor() as cursor:
                cursor.execute(query, tuple(params))
                return {"cleared": int(cursor.rowcount or 0), "source": "database", "error": None}


# === READ OPERATIONS ===
# These helpers only load or filter attempt history and JSONL snapshots.

def load_recent_cow_execution_attempts_jsonl(
    path: Path,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
) -> list[dict[str, Any]]:
    source_paths = [item for item in _jsonl_source_paths(path) if item.exists()]
    if not source_paths:
        return []
    wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    wanted_chain_ids = {int(item) for item in chain_ids or [] if item is not None}
    wanted_market_ids = {str(item).strip().lower() for item in market_ids or [] if str(item).strip()}
    wanted_category = str(category or "").strip().lower()
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    global_limit = max(1, int(limit))
    for source_path in source_paths:
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if not evidence_scope_matches(
                payload,
                networks=wanted,
                chain_ids=wanted_chain_ids,
                market_ids=wanted_market_ids,
            ):
                continue
            payload_category = cow_execution_attempt_category(payload)
            if wanted_category and payload_category != wanted_category:
                continue
            if not _category_within_retention(payload, now=now):
                continue
            rows.append(_decorate_attempt_review(payload))
    rows.sort(key=lambda item: (str(item.get("created_at") or item.get("observed_at") or ""), int(item.get("id") or 0)), reverse=True)
    if wanted:
        network_counts: dict[str, int] = {}
        limited_rows: list[dict[str, Any]] = []
        for row in rows:
            network = infer_evidence_scope(row).network
            if network_counts.get(network, 0) >= global_limit:
                continue
            network_counts[network] = network_counts.get(network, 0) + 1
            limited_rows.append(row)
            if wanted.issubset({key for key, count in network_counts.items() if count >= global_limit}):
                break
        return limited_rows
    return rows[:global_limit]


def load_recent_cow_execution_attempts(
    database_url: str,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
) -> list[dict[str, Any]]:
    ensure_cow_execution_attempts_table(database_url)
    wanted = [str(item).strip().lower() for item in networks or [] if str(item).strip()]
    wanted_chain_ids = [int(item) for item in chain_ids or [] if item is not None]
    wanted_market_ids = [str(item).strip().lower() for item in market_ids or [] if str(item).strip()]
    wanted_category = str(category or "").strip().lower()
    where = ["created_at >= NOW() - (%s * INTERVAL '1 day')"]
    params: list[Any] = [max(1, int(retention_days or DEFAULT_COW_EXECUTION_RETENTION_DAYS))]
    if wanted_category:
        cutoff = _category_retention_cutoff(wanted_category)
        if cutoff is None:
            where = ["TRUE"]
            params = []
        else:
            where = ["created_at >= %s"]
            params = [cutoff.astimezone(timezone.utc)]
    if wanted:
        where.append("LOWER(network) = ANY(%s)")
        params.append(wanted)
    if wanted_chain_ids:
        where.append("chain_id = ANY(%s)")
        params.append(wanted_chain_ids)
    query_limit = max(1, int(limit))
    params.append(query_limit * 5 if wanted_category else query_limit)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            if wanted:
                cursor.execute(
                    f"""
                    SELECT
                        id, observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        control_mode, route_hop_constraints_enforced,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        cow_flashloan_intent_json, cow_sdk_result_json,
                        error, created_at, quote_json, precheck_json, market_state_json
                    FROM (
                        SELECT
                            id, observed_at, network, chain_id, owner_address,
                            pair, pair_rank, priority_reason, route_path_json,
                            state, execution_phase, checks_passed, can_submit_order,
                            order_submission_enabled, auto_execute_requested,
                            control_mode, route_hop_constraints_enforced,
                            final_delta_amount, final_symbol, blocked_reasons_json,
                            cow_flashloan_intent_json, cow_sdk_result_json,
                            error, created_at, quote_json, precheck_json, market_state_json,
                            ROW_NUMBER() OVER (
                                PARTITION BY LOWER(network)
                                ORDER BY created_at DESC, id DESC
                            ) AS network_row_number
                        FROM cow_execution_attempts
                        WHERE {" AND ".join(where)}
                    ) scoped
                    WHERE network_row_number <= %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    tuple(params),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT
                        id, observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        control_mode, route_hop_constraints_enforced,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        cow_flashloan_intent_json, cow_sdk_result_json,
                        error, created_at, quote_json, precheck_json, market_state_json
                    FROM cow_execution_attempts
                    WHERE {" AND ".join(where)}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        item = {
                "id": int(row[0]),
                "observed_at": row[1].isoformat() if row[1] else None,
                "network": row[2],
                "chain_id": row[3],
                "owner_address": row[4],
                "pair": row[5],
                "pair_rank": row[6],
                "priority_reason": row[7],
                "route_path": json.loads(row[8] or "[]"),
                "state": row[9],
                "execution_phase": row[10],
                "checks_passed": bool(row[11]),
                "can_submit_order": bool(row[12]),
                "order_submission_enabled": bool(row[13]),
                "auto_execute_requested": bool(row[14]),
                "control_mode": row[15],
                "route_hop_constraints_enforced": bool(row[16]),
                "final_delta_amount": row[17],
                "final_symbol": row[18],
                "blocked_reasons": _json_loads(row[19], []),
                "cow_flashloan_intent": _json_loads(row[20], {}),
                "cow_sdk_result": _json_loads(row[21], {}),
                "error": row[22],
                "created_at": row[23].isoformat() if row[23] else None,
                "quote": _json_loads(row[24], {}),
                "precheck": _json_loads(row[25], {}),
                "market_state": _json_loads(row[26], {}),
            }
        if wanted_category and cow_execution_attempt_category(item) != wanted_category:
            continue
        if not evidence_scope_matches(
            item,
            networks=wanted,
            chain_ids=wanted_chain_ids,
            market_ids=wanted_market_ids,
        ):
            continue
        if not _category_within_retention(item):
            continue
        result.append(_decorate_attempt_review(item))
        if len(result) >= query_limit:
            break
    return result
