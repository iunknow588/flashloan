import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from db.storage_common import OBSERVER_ADVISORY_LOCK_ID, db_connection, dedicated_db_connection, require_psycopg
from execution.liquidation_priority import liquidation_account_activity_tier


def _upsert_scan_config_snapshot_cursor(
    cursor,
    *,
    config_key: str,
    source_table: str,
    payload: dict[str, Any],
    category: str = "scan",
    source_key: str | None = None,
    active: bool = True,
) -> None:
    item = dict(payload or {})
    item.setdefault("config_key", config_key)
    item.setdefault("source_table", source_table)
    cursor.execute(
        """
        INSERT INTO liquidation_scan_config_library (
            config_key, category, source_table, source_key,
            active, payload_json, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (config_key) DO UPDATE SET
            category = EXCLUDED.category,
            source_table = EXCLUDED.source_table,
            source_key = EXCLUDED.source_key,
            active = EXCLUDED.active,
            payload_json = EXCLUDED.payload_json,
            updated_at = NOW()
        """,
        (
            config_key,
            str(category or "scan"),
            source_table,
            str(source_key) if source_key is not None else None,
            bool(active),
            json.dumps(item, ensure_ascii=True, separators=(",", ":")),
        ),
    )


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
    with db_connection(database_url, connect_timeout=8) as connection:
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
    with db_connection(database_url, connect_timeout=8) as connection:
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


def record_liquidation_scan_config_snapshot(
    database_url: str,
    *,
    config_key: str,
    source_table: str,
    payload: dict[str, Any],
    category: str = "scan",
    source_key: str | None = None,
    active: bool = True,
) -> dict[str, Any]:
    key = str(config_key or "").strip()
    table = str(source_table or "").strip()
    if not key or not table:
        return {}
    item = dict(payload or {})
    item.setdefault("config_key", key)
    item.setdefault("source_table", table)
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_scan_config_library (
                    config_key, category, source_table, source_key,
                    active, payload_json, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (config_key) DO UPDATE SET
                    category = EXCLUDED.category,
                    source_table = EXCLUDED.source_table,
                    source_key = EXCLUDED.source_key,
                    active = EXCLUDED.active,
                    payload_json = EXCLUDED.payload_json,
                    updated_at = NOW()
                """,
                (
                    key,
                    str(category or "scan"),
                    table,
                    str(source_key) if source_key is not None else None,
                    bool(active),
                    json.dumps(item, ensure_ascii=True, separators=(",", ":")),
                ),
            )
    return item


def load_liquidation_scan_config_library(
    database_url: str,
    *,
    category: str | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    query = """
        SELECT config_key, category, source_table, source_key, active, payload_json, updated_at
        FROM liquidation_scan_config_library
        WHERE 1=1
    """
    params: list[Any] = []
    if category:
        query += " AND category = %s"
        params.append(str(category))
    if active_only:
        query += " AND active = TRUE"
    query += " ORDER BY updated_at DESC, config_key ASC LIMIT %s"
    params.append(max(1, int(limit)))
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "config_key": str(row[0]),
                "category": str(row[1]),
                "source_table": str(row[2]),
                "source_key": str(row[3]) if row[3] else None,
                "active": bool(row[4]),
                "payload": _json_or_default(row[5], {}),
                "updated_at": row[6].isoformat() if row[6] else None,
            }
        )
    return result


def rebuild_liquidation_scan_config_library(database_url: str) -> dict[str, Any]:
    psycopg = require_psycopg()
    rebuilt: list[str] = []
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COUNT(*) FILTER (WHERE active = TRUE) AS active_count,
                    COUNT(*) FILTER (WHERE active = TRUE AND activity_tier = 'hot') AS hot_count,
                    COUNT(*) FILTER (WHERE active = TRUE AND activity_tier = 'warm') AS warm_count,
                    COUNT(*) FILTER (WHERE active = TRUE AND (activity_tier = 'cold' OR activity_tier IS NULL)) AS cold_count,
                    MIN(scan_start_at) AS earliest_scan_start_at,
                    MAX(scan_end_at) AS latest_scan_end_at
                FROM liquidation_accounts
                """
            )
            row = cursor.fetchone() or (0, 0, 0, 0, 0, None, None)
            cursor.execute(
                """
                SELECT account
                FROM liquidation_accounts
                WHERE active = TRUE
                ORDER BY
                    CASE
                        WHEN activity_tier = 'hot' THEN 0
                        WHEN activity_tier = 'warm' THEN 1
                        WHEN activity_tier = 'cold' THEN 2
                        ELSE 1
                    END,
                    COALESCE(last_scanned_at, scan_end_at, updated_at) DESC,
                    account ASC
                LIMIT 100
                """
            )
            sample_accounts = [str(item[0]) for item in cursor.fetchall() if item and item[0]]
            _upsert_scan_config_snapshot_cursor(
                cursor,
                config_key="liquidation_accounts.latest",
                source_table="liquidation_accounts",
                payload={
                    "total_count": int(row[0] or 0),
                    "active_count": int(row[1] or 0),
                    "hot_count": int(row[2] or 0),
                    "warm_count": int(row[3] or 0),
                    "cold_count": int(row[4] or 0),
                    "earliest_scan_start_at": row[5].isoformat() if row[5] else None,
                    "latest_scan_end_at": row[6].isoformat() if row[6] else None,
                    "sample_accounts": sample_accounts,
                    "rebuilt_from_existing_tables": True,
                },
            )
            rebuilt.append("liquidation_accounts.latest")

            cursor.execute(
                """
                SELECT mode, status, rpc_url, pool_address, from_block, to_block,
                       scan_start_at, scan_end_at, discovered_count, error
                FROM liquidation_discovery_scans
                WHERE status = 'success'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row:
                payload = {
                    "mode": row[0],
                    "status": row[1],
                    "rpc_url": row[2],
                    "pool_address": row[3],
                    "from_block": int(row[4]),
                    "to_block": int(row[5]),
                    "scan_start_at": row[6].isoformat() if row[6] else None,
                    "scan_end_at": row[7].isoformat() if row[7] else None,
                    "discovered_count": int(row[8] or 0),
                    "error": row[9],
                    "rebuilt_from_existing_tables": True,
                }
                _upsert_scan_config_snapshot_cursor(
                    cursor,
                    config_key="liquidation_discovery_scans.latest_success",
                    source_table="liquidation_discovery_scans",
                    payload=payload,
                    active=True,
                )
                rebuilt.append("liquidation_discovery_scans.latest_success")

            pool_specs = [
                ("liquidation_borrow_health_pool.latest", "liquidation_borrow_health_pool", "health_factor ASC, updated_at DESC"),
                ("liquidation_high_frequency_pool.latest", "liquidation_high_frequency_pool", "priority_score DESC, health_factor ASC, updated_at DESC"),
                ("liquidation_core_opportunity_pool.latest", "liquidation_core_opportunity_pool", "priority_score DESC, health_factor ASC, updated_at DESC"),
            ]
            for config_key, table, order_by in pool_specs:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS active_count,
                           MIN(health_factor) AS min_health_factor,
                           MAX(updated_at) AS latest_updated_at
                    FROM {table}
                    WHERE active = TRUE
                    """
                )
                row = cursor.fetchone() or (0, None, None)
                cursor.execute(
                    f"""
                    SELECT account
                    FROM {table}
                    WHERE active = TRUE
                    ORDER BY {order_by}
                    LIMIT 100
                    """
                )
                active_accounts = [str(item[0]) for item in cursor.fetchall() if item and item[0]]
                _upsert_scan_config_snapshot_cursor(
                    cursor,
                    config_key=config_key,
                    source_table=table,
                    payload={
                        "active_count": int(row[0] or 0),
                        "min_health_factor": float(row[1]) if row[1] is not None else None,
                        "latest_updated_at": row[2].isoformat() if row[2] else None,
                        "active_accounts": active_accounts,
                        "rebuilt_from_existing_tables": True,
                    },
                    active=bool(active_accounts),
                )
                rebuilt.append(config_key)

            cursor.execute(
                """
                SELECT id, started_at, finished_at, status, account_count, scanned_count,
                       risk_count, error_count, entered_count, exited_count, rpc_url,
                       block_number, watch_health_factor, error, metadata_json
                FROM liquidation_borrow_health_scan_batches
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row:
                _upsert_scan_config_snapshot_cursor(
                    cursor,
                    config_key="liquidation_borrow_health_scan_batches.latest",
                    source_table="liquidation_borrow_health_scan_batches",
                    source_key=str(row[0]),
                    payload={
                        "batch_id": int(row[0]),
                        "started_at": row[1].isoformat() if row[1] else None,
                        "finished_at": row[2].isoformat() if row[2] else None,
                        "status": row[3],
                        "account_count": int(row[4] or 0),
                        "scanned_count": int(row[5] or 0),
                        "risk_count": int(row[6] or 0),
                        "error_count": int(row[7] or 0),
                        "entered_count": int(row[8] or 0),
                        "exited_count": int(row[9] or 0),
                        "rpc_url": row[10],
                        "block_number": int(row[11]) if row[11] is not None else None,
                        "watch_health_factor": float(row[12]) if row[12] is not None else None,
                        "error": row[13],
                        "metadata": _json_or_default(row[14], {}),
                        "rebuilt_from_existing_tables": True,
                    },
                    active=True,
                )
                rebuilt.append("liquidation_borrow_health_scan_batches.latest")
    return {"rebuilt_count": len(rebuilt), "config_keys": rebuilt}


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
    with db_connection(database_url, connect_timeout=8) as connection:
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
            _upsert_scan_config_snapshot_cursor(
                cursor,
                config_key="liquidation_accounts.latest",
                source_table="liquidation_accounts",
                payload={
                    "source": source,
                    "active": bool(active),
                    "update_existing": bool(update_existing),
                    "account_count": len(unique_accounts),
                    "scan_start_at": started_at.isoformat() if hasattr(started_at, "isoformat") else str(started_at),
                    "scan_end_at": now.isoformat() if hasattr(now, "isoformat") else str(now),
                    "sample_accounts": unique_accounts[:100],
                },
            )
    return unique_accounts


def load_liquidation_accounts(
    database_url: str,
    active_only: bool = True,
    retained_days: int = 0,
    scan_start_after: datetime | None = None,
    scan_end_before: datetime | None = None,
) -> list[str]:
    psycopg = require_psycopg()
    query = "SELECT account FROM liquidation_accounts WHERE 1=1"
    params: list[Any] = []
    if active_only:
        query += " AND active = TRUE"
    if retained_days and retained_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(retained_days))
        query += " AND scan_end_at >= %s"
        params.append(cutoff)
    if scan_start_after is not None:
        query += " AND scan_start_at >= %s"
        params.append(scan_start_after)
    if scan_end_before is not None:
        query += " AND scan_end_at <= %s"
        params.append(scan_end_before)
    query += """
        ORDER BY
            CASE
                WHEN activity_tier = 'hot' THEN 0
                WHEN activity_tier = 'warm' THEN 1
                WHEN activity_tier = 'cold' THEN 2
                ELSE 1
            END,
            COALESCE(last_scanned_at, scan_end_at, updated_at) DESC,
            account ASC
    """
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [str(row[0]) for row in cursor.fetchall() if row and row[0]]


def liquidation_account_registry_stats(database_url: str, retained_days: int = 365) -> dict[str, Any]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COUNT(*) FILTER (WHERE active = TRUE) AS active_count,
                    COUNT(*) FILTER (WHERE active = TRUE AND activity_tier = 'hot') AS hot_count,
                    COUNT(*) FILTER (WHERE active = TRUE AND activity_tier = 'warm') AS warm_count,
                    COUNT(*) FILTER (WHERE active = TRUE AND (activity_tier = 'cold' OR activity_tier IS NULL)) AS cold_count,
                    MIN(scan_start_at) AS earliest_scan_start_at,
                    MAX(scan_end_at) AS latest_scan_end_at
                FROM liquidation_accounts
                """
            )
            row = cursor.fetchone() or (0, 0, None, None)
            return {
                "total_count": int(row[0] or 0),
                "active_count": int(row[1] or 0),
                "hot_count": int(row[2] or 0),
                "warm_count": int(row[3] or 0),
                "cold_count": int(row[4] or 0),
                "earliest_scan_start_at": row[5].isoformat() if row[5] else None,
                "latest_scan_end_at": row[6].isoformat() if row[6] else None,
                "retained_days": int(retained_days),
            }


def load_latest_liquidation_account_reports(database_url: str, limit: int = 500) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    query = """
        SELECT
            account, source, active, scan_start_at, scan_end_at, last_scanned_at,
            last_health_factor, last_status, last_health_factor_band, last_candidate_count,
            last_total_collateral_base, last_total_debt_base, activity_tier,
            last_summary_json, last_report_json
        FROM liquidation_accounts
        WHERE last_report_json IS NOT NULL OR last_summary_json IS NOT NULL
        ORDER BY COALESCE(last_scanned_at, scan_end_at, updated_at) DESC, account ASC
        LIMIT %s
    """
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (int(limit),))
            rows = cursor.fetchall()
    reports: list[dict[str, Any]] = []
    for row in rows:
        summary = {}
        report = {}
        try:
            if row[13]:
                summary = json.loads(row[13]) if isinstance(row[13], str) else {}
        except json.JSONDecodeError:
            summary = {}
        try:
            if row[14]:
                report = json.loads(row[14]) if isinstance(row[14], str) else {}
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
                "last_total_collateral_base": float(row[10]) if row[10] is not None else None,
                "last_total_debt_base": float(row[11]) if row[11] is not None else None,
                "activity_tier": str(row[12]) if row[12] else None,
                "summary": summary,
                "report": report,
            }
        )
    return reports


def prune_liquidation_accounts(database_url: str, retained_days: int = 365) -> int:
    return 0


def record_liquidation_account_scan(database_url: str, report: dict[str, Any]) -> None:
    account = str(report.get("account") or "").strip()
    if not account:
        return
    summary = report.get("summary") or {}
    account_tier = liquidation_account_activity_tier(
        {
            "last_status": summary.get("status"),
            "last_health_factor": summary.get("health_factor"),
            "last_total_debt_base": summary.get("total_debt_base") or report.get("total_debt_base"),
        }
    )
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
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
                    last_total_collateral_base, last_total_debt_base, activity_tier,
                    last_summary_json, last_report_json
                )
                VALUES (
                    %s, 'scan', TRUE, NOW(), NOW(),
                    COALESCE(%s, NOW()), NOW(),
                    NOW(), %s, %s,
                    %s, %s,
                    %s, %s, %s,
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
                    last_total_collateral_base = EXCLUDED.last_total_collateral_base,
                    last_total_debt_base = EXCLUDED.last_total_debt_base,
                    activity_tier = EXCLUDED.activity_tier,
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
                    summary.get("total_collateral_base") or report.get("total_collateral_base") or report.get("total_collateral_in_base_currency"),
                    summary.get("total_debt_base") or report.get("total_debt_base") or report.get("total_debt_in_base_currency"),
                    account_tier,
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
    with db_connection(database_url, connect_timeout=8) as connection:
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


def record_liquidation_failure_sample(
    database_url: str,
    *,
    account: str | None = None,
    block_number: int | None = None,
    collateral_asset: str | None = None,
    debt_asset: str | None = None,
    failure_type: str,
    failure_reason: str | None = None,
    payload: dict[str, Any] | None = None,
    source: str = "execution_attempt",
) -> int:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_failure_samples (
                    account, block_number, collateral_asset, debt_asset,
                    failure_type, failure_reason, payload_json, source, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (
                    account,
                    block_number,
                    collateral_asset,
                    debt_asset,
                    failure_type,
                    failure_reason,
                    json.dumps(payload or {}, ensure_ascii=True, separators=(",", ":")),
                    source,
                ),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0


def load_recent_liquidation_failure_samples(database_url: str, limit: int = 20) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, block_number, collateral_asset, debt_asset,
                    failure_type, failure_reason, payload_json, source, created_at
                FROM liquidation_failure_samples
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()
    samples: list[dict[str, Any]] = []
    for row in rows:
        samples.append(
            {
                "id": int(row[0]),
                "account": str(row[1]) if row[1] else None,
                "block_number": int(row[2]) if row[2] is not None else None,
                "collateral_asset": str(row[3]) if row[3] else None,
                "debt_asset": str(row[4]) if row[4] else None,
                "failure_type": str(row[5]),
                "failure_reason": str(row[6]) if row[6] else None,
                "payload": _json_or_default(row[7], {}),
                "source": str(row[8]),
                "created_at": row[9].isoformat() if row[9] else None,
            }
        )
    return samples


def load_liquidation_failure_samples_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, block_number, collateral_asset, debt_asset,
                    failure_type, failure_reason, payload_json, source, created_at
                FROM liquidation_failure_samples
                WHERE lower(account) = lower(%s)
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (account, max(1, int(limit))),
            )
            rows = cursor.fetchall()
    return [
        {
            "id": int(row[0]),
            "account": str(row[1]) if row[1] else None,
            "block_number": int(row[2]) if row[2] is not None else None,
            "collateral_asset": str(row[3]) if row[3] else None,
            "debt_asset": str(row[4]) if row[4] else None,
            "failure_type": str(row[5]),
            "failure_reason": str(row[6]) if row[6] else None,
            "payload": _json_or_default(row[7], {}),
            "source": str(row[8]),
            "created_at": row[9].isoformat() if row[9] else None,
        }
        for row in rows
    ]


def load_recent_liquidation_execution_attempts(database_url: str, limit: int = 20) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
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


def load_liquidation_execution_attempts_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                FROM liquidation_execution_attempts
                WHERE lower(account) = lower(%s)
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (account, max(1, int(limit))),
            )
            rows = cursor.fetchall()
    return [
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
        for row in rows
    ]


def liquidation_execution_attempt_stats(database_url: str) -> dict[str, int]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
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
    connection = dedicated_db_connection(database_url, connect_timeout=8)
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


