import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from db.storage_common import OBSERVER_ADVISORY_LOCK_ID, require_psycopg
def record_liquidation_discovery_scan(
    database_url: str,
    *,
    mode: str,
    status: str,
    rpc_url: str,
    pool_address: str,
    from_block: int,
    to_block: int,
    scan_start_at: datetime,
    scan_end_at: datetime,
    discovered_count: int = 0,
    error: str | None = None,
) -> None:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_discovery_scans (
                    mode, status, rpc_url, pool_address,
                    from_block, to_block, scan_start_at, scan_end_at,
                    discovered_count, error, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (mode, pool_address, from_block, to_block) DO UPDATE SET
                    status = EXCLUDED.status,
                    rpc_url = EXCLUDED.rpc_url,
                    scan_start_at = EXCLUDED.scan_start_at,
                    scan_end_at = EXCLUDED.scan_end_at,
                    discovered_count = EXCLUDED.discovered_count,
                    error = EXCLUDED.error,
                    created_at = NOW()
                """,
                (
                    mode,
                    status,
                    rpc_url,
                    pool_address,
                    int(from_block),
                    int(to_block),
                    scan_start_at,
                    scan_end_at,
                    int(discovered_count),
                    error,
                ),
            )


def liquidation_discovery_scan_progress(database_url: str, pool_address: str) -> dict[str, Any]:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    MAX(scan_end_at) FILTER (WHERE mode = 'recent' AND status = 'success') AS latest_recent_scan_end_at,
                    MIN(scan_start_at) FILTER (WHERE mode = 'historical-backfill' AND status = 'success') AS earliest_backfill_scan_start_at,
                    MAX(to_block) FILTER (WHERE mode = 'recent' AND status = 'success') AS latest_recent_to_block,
                    MIN(from_block) FILTER (WHERE mode = 'historical-backfill' AND status = 'success') AS earliest_backfill_from_block,
                    COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                    COUNT(*) FILTER (WHERE status = 'error') AS error_count,
                    COALESCE(SUM(GREATEST(0, to_block - from_block + 1)) FILTER (WHERE status = 'success'), 0) AS scanned_block_count
                FROM liquidation_discovery_scans
                WHERE pool_address = %s
                """,
                (pool_address,),
            )
            row = cursor.fetchone() or (None, None, 0, 0, 0)
            return {
                "latest_recent_scan_end_at": row[0].isoformat() if row[0] else None,
                "earliest_backfill_scan_start_at": row[1].isoformat() if row[1] else None,
                "latest_recent_to_block": int(row[2]) if row[2] is not None else None,
                "earliest_backfill_from_block": int(row[3]) if row[3] is not None else None,
                "success_count": int(row[4] or 0),
                "error_count": int(row[5] or 0),
                "scanned_block_count": int(row[6] or 0),
            }


def upsert_liquidation_accounts(
    database_url: str,
    accounts: Iterable[str],
    source: str = "manual",
    active: bool = True,
    scan_start_at: datetime | None = None,
    scan_end_at: datetime | None = None,
    update_existing: bool = True,
) -> list[str]:
    unique_accounts: list[str] = []
    for account in accounts:
        if not account:
            continue
        value = str(account)
        if value not in unique_accounts:
            unique_accounts.append(value)
    if not unique_accounts:
        return []
    psycopg = require_psycopg()
    now = scan_end_at or datetime.now(timezone.utc)
    started_at = scan_start_at or now
    values = [(account, source, active, started_at, now) for account in unique_accounts]
    conflict_clause = (
        """
                ON CONFLICT (account) DO UPDATE SET
                    source = EXCLUDED.source,
                    active = EXCLUDED.active,
                    scan_start_at = COALESCE(liquidation_accounts.scan_start_at, EXCLUDED.scan_start_at),
                    scan_end_at = EXCLUDED.scan_end_at,
                    updated_at = NOW()
                """
        if update_existing
        else "ON CONFLICT (account) DO NOTHING"
    )
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                f"""
                INSERT INTO liquidation_accounts (
                    account, source, active, scan_start_at, scan_end_at, added_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                {conflict_clause}
                """,
                values,
            )
    return unique_accounts


def load_liquidation_accounts(
    database_url: str,
    active_only: bool = True,
    retained_days: int = 365,
    scan_start_after: datetime | None = None,
    scan_end_before: datetime | None = None,
) -> list[str]:
    psycopg = require_psycopg()
    query = "SELECT account FROM liquidation_accounts WHERE 1=1"
    params: list[Any] = []
    if active_only:
        query += " AND active = TRUE"
    if retained_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(retained_days))
        query += " AND scan_end_at >= %s"
        params.append(cutoff)
    if scan_start_after is not None:
        query += " AND scan_start_at >= %s"
        params.append(scan_start_after)
    if scan_end_before is not None:
        query += " AND scan_end_at <= %s"
        params.append(scan_end_before)
    query += " ORDER BY scan_end_at DESC, account ASC"
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [str(row[0]) for row in cursor.fetchall() if row and row[0]]


def liquidation_account_registry_stats(database_url: str, retained_days: int = 365) -> dict[str, Any]:
    psycopg = require_psycopg()
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(retained_days))
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COUNT(*) FILTER (WHERE active = TRUE AND scan_end_at >= %s) AS active_count,
                    MIN(scan_start_at) FILTER (WHERE scan_end_at >= %s) AS earliest_scan_start_at,
                    MAX(scan_end_at) FILTER (WHERE scan_end_at >= %s) AS latest_scan_end_at
                FROM liquidation_accounts
                """,
                (cutoff, cutoff, cutoff),
            )
            row = cursor.fetchone() or (0, 0, None, None)
            return {
                "total_count": int(row[0] or 0),
                "active_count": int(row[1] or 0),
                "earliest_scan_start_at": row[2].isoformat() if row[2] else None,
                "latest_scan_end_at": row[3].isoformat() if row[3] else None,
                "retained_days": int(retained_days),
            }


def load_latest_liquidation_account_reports(database_url: str, limit: int = 500) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    query = """
        SELECT
            account, source, active, scan_start_at, scan_end_at, last_scanned_at,
            last_health_factor, last_status, last_health_factor_band, last_candidate_count,
            last_summary_json, last_report_json
        FROM liquidation_accounts
        WHERE last_report_json IS NOT NULL OR last_summary_json IS NOT NULL
        ORDER BY COALESCE(last_scanned_at, scan_end_at, updated_at) DESC, account ASC
        LIMIT %s
    """
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (int(limit),))
            rows = cursor.fetchall()
    reports: list[dict[str, Any]] = []
    for row in rows:
        summary = {}
        report = {}
        try:
            if row[10]:
                summary = json.loads(row[10]) if isinstance(row[10], str) else {}
        except json.JSONDecodeError:
            summary = {}
        try:
            if row[11]:
                report = json.loads(row[11]) if isinstance(row[11], str) else {}
        except json.JSONDecodeError:
            report = {}
        reports.append(
            {
                "account": str(row[0]),
                "source": str(row[1]),
                "active": bool(row[2]),
                "scan_start_at": row[3].isoformat() if row[3] else None,
                "scan_end_at": row[4].isoformat() if row[4] else None,
                "last_scanned_at": row[5].isoformat() if row[5] else None,
                "last_health_factor": float(row[6]) if row[6] is not None else None,
                "last_status": str(row[7]) if row[7] else None,
                "last_health_factor_band": str(row[8]) if row[8] else None,
                "last_candidate_count": int(row[9]) if row[9] is not None else None,
                "summary": summary,
                "report": report,
            }
        )
    return reports


def prune_liquidation_accounts(database_url: str, retained_days: int = 365) -> int:
    psycopg = require_psycopg()
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(retained_days))
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM liquidation_accounts
                WHERE active = FALSE
                   OR scan_end_at < %s
                """,
                (cutoff,),
            )
            return int(cursor.rowcount or 0)


def record_liquidation_account_scan(database_url: str, report: dict[str, Any]) -> None:
    account = str(report.get("account") or "").strip()
    if not account:
        return
    summary = report.get("summary") or {}
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_account_health_scans (
                    account, scanned_at, health_factor, status,
                    health_factor_band, candidate_count, summary_json, report_json
                )
                VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
                """,
                (
                    account,
                    summary.get("health_factor"),
                    summary.get("status"),
                    summary.get("health_factor_band"),
                    int(summary.get("candidate_count") or 0),
                    json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(report, ensure_ascii=True, separators=(",", ":")),
                ),
            )
            cursor.execute(
                """
                INSERT INTO liquidation_accounts (
                    account, source, active, added_at, updated_at,
                    scan_start_at, scan_end_at,
                    last_scanned_at, last_health_factor, last_status,
                    last_health_factor_band, last_candidate_count,
                    last_summary_json, last_report_json
                )
                VALUES (
                    %s, 'scan', TRUE, NOW(), NOW(),
                    COALESCE(%s, NOW()), NOW(),
                    NOW(), %s, %s,
                    %s, %s,
                    %s, %s
                )
                ON CONFLICT (account) DO UPDATE SET
                    source = 'scan',
                    active = liquidation_accounts.active,
                    updated_at = NOW(),
                    scan_start_at = COALESCE(liquidation_accounts.scan_start_at, EXCLUDED.scan_start_at),
                    scan_end_at = EXCLUDED.scan_end_at,
                    last_scanned_at = NOW(),
                    last_health_factor = EXCLUDED.last_health_factor,
                    last_status = EXCLUDED.last_status,
                    last_health_factor_band = EXCLUDED.last_health_factor_band,
                    last_candidate_count = EXCLUDED.last_candidate_count,
                    last_summary_json = EXCLUDED.last_summary_json,
                    last_report_json = EXCLUDED.last_report_json
                """,
                (
                    account,
                    None,
                    summary.get("health_factor"),
                    summary.get("status"),
                    summary.get("health_factor_band"),
                    summary.get("candidate_count"),
                    json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(report, ensure_ascii=True, separators=(",", ":")),
                ),
            )


def record_liquidation_execution_attempt(
    database_url: str,
    *,
    account: str | None = None,
    mode: str,
    state: str,
    blocked_reasons: list[str] | None = None,
    request_payload: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
    preflight: dict[str, Any] | None = None,
    tx_hash: str | None = None,
    receipt: dict[str, Any] | None = None,
    error: str | None = None,
) -> int:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_execution_attempts (
                    account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (
                    account,
                    mode,
                    state,
                    json.dumps(blocked_reasons or [], ensure_ascii=True, separators=(",", ":")),
                    json.dumps(request_payload or {}, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(quote or {}, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(preflight or {}, ensure_ascii=True, separators=(",", ":")),
                    tx_hash,
                    json.dumps(receipt or {}, ensure_ascii=True, separators=(",", ":")),
                    error,
                ),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0


def load_recent_liquidation_execution_attempts(database_url: str, limit: int = 20) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                FROM liquidation_execution_attempts
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()
    attempts: list[dict[str, Any]] = []
    for row in rows:
        attempts.append(
            {
                "id": int(row[0]),
                "account": str(row[1]) if row[1] else None,
                "mode": str(row[2]),
                "state": str(row[3]),
                "blocked_reasons": _json_or_default(row[4], []),
                "request": _json_or_default(row[5], {}),
                "quote": _json_or_default(row[6], {}),
                "preflight": _json_or_default(row[7], {}),
                "tx_hash": str(row[8]) if row[8] else None,
                "receipt": _json_or_default(row[9], {}),
                "error": str(row[10]) if row[10] else None,
                "created_at": row[11].isoformat() if row[11] else None,
                "updated_at": row[12].isoformat() if row[12] else None,
            }
        )
    return attempts


def liquidation_execution_attempt_stats(database_url: str) -> dict[str, int]:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE state = 'submission_blocked') AS blocked,
                    COUNT(*) FILTER (WHERE tx_hash IS NOT NULL) AS submitted,
                    COUNT(*) FILTER (WHERE state = 'confirmed_success') AS confirmed_success,
                    COUNT(*) FILTER (WHERE state = 'confirmed_failed') AS confirmed_failed,
                    COUNT(*) FILTER (WHERE state = 'static_call_failed') AS static_call_failed,
                    COUNT(*) FILTER (WHERE error IS NOT NULL) AS errors
                FROM liquidation_execution_attempts
                """
            )
            row = cursor.fetchone() or (0, 0, 0, 0, 0, 0, 0)
    return {
        "total": int(row[0] or 0),
        "blocked": int(row[1] or 0),
        "submitted": int(row[2] or 0),
        "confirmed_success": int(row[3] or 0),
        "confirmed_failed": int(row[4] or 0),
        "static_call_failed": int(row[5] or 0),
        "errors": int(row[6] or 0),
    }


def _json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else default
    except json.JSONDecodeError:
        return default


def try_acquire_observer_lock(database_url: str, lock_id: int = OBSERVER_ADVISORY_LOCK_ID):
    psycopg = require_psycopg()
    connection = psycopg.connect(database_url, connect_timeout=8)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
            locked = bool(cursor.fetchone()[0])
        if locked:
            return connection
    except Exception:
        connection.close()
        raise
    connection.close()
    return None


