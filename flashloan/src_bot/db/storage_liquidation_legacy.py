import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from db.storage_common import OBSERVER_ADVISORY_LOCK_ID, db_connection, dedicated_db_connection, require_psycopg


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
