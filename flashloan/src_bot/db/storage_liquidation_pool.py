import json
from typing import Any

from db.storage_common import db_connection, require_psycopg
from execution.liquidation_priority import enrich_liquidation_tier, liquidation_pool_tier


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=True, separators=(",", ":"))


def _upsert_scan_config_snapshot(
    cursor,
    *,
    config_key: str,
    source_table: str,
    payload: dict[str, Any],
    source_key: str | None = None,
    category: str = "scan",
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
            category,
            source_table,
            source_key,
            bool(active),
            _json(item),
        ),
    )


def _json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else default
    except json.JSONDecodeError:
        return default


def _candidate_for(row: dict[str, Any]) -> dict[str, Any]:
    candidates = row.get("liquidation_candidates") or []
    if isinstance(candidates, list) and candidates:
        return candidates[0] or {}
    return row.get("recommended_candidate") or {}


def _core_opportunity_viable(row: dict[str, Any]) -> bool:
    candidate = _candidate_for(row)
    profit = candidate.get("estimated_profit") or row.get("liquidation_profit") or {}
    try:
        net_profit = float(profit.get("net_profit_base") or profit.get("net_profit_usd") or 0.0)
    except (TypeError, ValueError):
        net_profit = 0.0
    try:
        debt_base = float(row.get("total_debt_base") or row.get("total_debt_in_base_currency") or 0.0)
    except (TypeError, ValueError):
        debt_base = 0.0
    return debt_base > 0 and net_profit > 0


def sync_liquidation_borrow_health_pool(
    database_url: str,
    rows: list[dict[str, Any]],
    watch_health_factor: float = 1.5,
) -> dict[str, int]:
    psycopg = require_psycopg()
    active_accounts: list[str] = []
    high_frequency_accounts: list[str] = []
    core_accounts: list[str] = []
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT account FROM liquidation_borrow_health_pool WHERE active = TRUE")
            previous_active = {str(row[0]) for row in cursor.fetchall()}
            for row in rows:
                account = str(row.get("account") or "").strip()
                health_factor = row.get("health_factor")
                if not account or not isinstance(health_factor, (int, float)):
                    continue
                is_active = float(health_factor) < float(watch_health_factor)
                tier = liquidation_pool_tier(health_factor)
                if is_active:
                    active_accounts.append(account)
                if tier == "high_frequency":
                    high_frequency_accounts.append(account)
                summary = {
                    "health_factor": health_factor,
                    "status": row.get("status"),
                    "health_factor_band": row.get("health_factor_band"),
                    "candidate_count": len(row.get("liquidation_candidates") or []),
                    "pool_tier": tier,
                }
                cursor.execute(
                    """
                    INSERT INTO liquidation_borrow_health_pool (
                        account, health_factor, status, health_factor_band,
                        total_collateral_base, total_debt_base, candidate_count,
                        summary_json, report_json, active, last_scanned_at, updated_at
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                    ON CONFLICT (account) DO UPDATE SET
                        health_factor = EXCLUDED.health_factor,
                        status = EXCLUDED.status,
                        health_factor_band = EXCLUDED.health_factor_band,
                        total_collateral_base = EXCLUDED.total_collateral_base,
                        total_debt_base = EXCLUDED.total_debt_base,
                        candidate_count = EXCLUDED.candidate_count,
                        summary_json = EXCLUDED.summary_json,
                        report_json = EXCLUDED.report_json,
                        active = EXCLUDED.active,
                        last_scanned_at = NOW(),
                        updated_at = NOW()
                    """,
                    (
                        account,
                        health_factor,
                        row.get("status"),
                        row.get("health_factor_band"),
                        row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                        row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                        len(row.get("liquidation_candidates") or []),
                        json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                        json.dumps(row, ensure_ascii=True, separators=(",", ":")),
                        is_active,
                    ),
                )
                if tier == "high_frequency":
                    enriched = enrich_liquidation_tier(row)
                    cursor.execute(
                        """
                        INSERT INTO liquidation_high_frequency_pool (
                            account, health_factor, status, total_collateral_base,
                            total_debt_base, candidate_count, priority_score,
                            summary_json, report_json, active, last_scanned_at, updated_at
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW(),NOW())
                        ON CONFLICT (account) DO UPDATE SET
                            health_factor = EXCLUDED.health_factor,
                            status = EXCLUDED.status,
                            total_collateral_base = EXCLUDED.total_collateral_base,
                            total_debt_base = EXCLUDED.total_debt_base,
                            candidate_count = EXCLUDED.candidate_count,
                            priority_score = EXCLUDED.priority_score,
                            summary_json = EXCLUDED.summary_json,
                            report_json = EXCLUDED.report_json,
                            active = TRUE,
                            last_scanned_at = NOW(),
                            updated_at = NOW()
                        """,
                        (
                            account,
                            health_factor,
                            row.get("status"),
                            row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                            row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                            len(row.get("liquidation_candidates") or []),
                            enriched["priority_score"],
                            _json(summary),
                            _json(row),
                        ),
                    )
                if tier == "core" and _core_opportunity_viable(row):
                    core_accounts.append(account)
                if tier == "core" and _core_opportunity_viable(row):
                    candidate = _candidate_for(row)
                    profit = candidate.get("estimated_profit") or row.get("liquidation_profit") or {}
                    enriched = enrich_liquidation_tier(row)
                    cursor.execute(
                        """
                        INSERT INTO liquidation_core_opportunity_pool (
                            account, health_factor, priority_score, total_debt_base,
                            total_collateral_base, best_debt_asset, best_collateral_asset,
                            debt_to_cover_units, estimated_operator_net_profit_usd,
                            estimated_gas_cost_usd, quote_viable, static_call_status,
                            payload_state, blocked_reasons_json, last_scanned_at,
                            updated_at,
                            active, metadata_json
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),TRUE,%s)
                        ON CONFLICT (account) DO UPDATE SET
                            health_factor = EXCLUDED.health_factor,
                            priority_score = EXCLUDED.priority_score,
                            total_debt_base = EXCLUDED.total_debt_base,
                            total_collateral_base = EXCLUDED.total_collateral_base,
                            best_debt_asset = EXCLUDED.best_debt_asset,
                            best_collateral_asset = EXCLUDED.best_collateral_asset,
                            debt_to_cover_units = EXCLUDED.debt_to_cover_units,
                            estimated_operator_net_profit_usd = EXCLUDED.estimated_operator_net_profit_usd,
                            estimated_gas_cost_usd = EXCLUDED.estimated_gas_cost_usd,
                            quote_viable = EXCLUDED.quote_viable,
                            static_call_status = EXCLUDED.static_call_status,
                            payload_state = EXCLUDED.payload_state,
                            blocked_reasons_json = EXCLUDED.blocked_reasons_json,
                            last_scanned_at = NOW(),
                            updated_at = NOW(),
                            active = TRUE,
                            metadata_json = EXCLUDED.metadata_json
                        """,
                        (
                            account,
                            health_factor,
                            enriched["priority_score"],
                            row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                            row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                            candidate.get("debt_asset") or candidate.get("debt_symbol"),
                            candidate.get("collateral_asset") or candidate.get("collateral_symbol"),
                            str(candidate.get("amount_to_pass_to_liquidation_call") or candidate.get("max_debt_to_liquidate") or ""),
                            profit.get("net_profit_base") or profit.get("net_profit_usd"),
                            profit.get("gas_cost_usd"),
                            bool(candidate),
                            "pending",
                            "pending",
                            _json([]),
                            _json({"summary": summary, "report": row, "recommended_candidate": candidate}),
                        ),
                    )
            cursor.execute(
                "UPDATE liquidation_borrow_health_pool SET active = FALSE, updated_at = NOW() "
                "WHERE active = TRUE AND health_factor >= %s",
                (float(watch_health_factor),),
            )
            cursor.execute(
                "UPDATE liquidation_high_frequency_pool SET active = FALSE, updated_at = NOW() "
                "WHERE active = TRUE AND NOT (account = ANY(%s))",
                (high_frequency_accounts or ["__none__"],),
            )
            cursor.execute(
                "UPDATE liquidation_core_opportunity_pool SET active = FALSE "
                "WHERE active = TRUE AND NOT (account = ANY(%s))",
                (core_accounts or ["__none__"],),
            )
            _upsert_scan_config_snapshot(
                cursor,
                config_key="liquidation_borrow_health_pool.latest",
                source_table="liquidation_borrow_health_pool",
                payload={
                    "watch_health_factor": float(watch_health_factor),
                    "active_count": len(active_accounts),
                    "high_frequency_count": len(high_frequency_accounts),
                    "core_count": len(core_accounts),
                    "entered_count": len(set(active_accounts) - previous_active),
                    "exited_count": len(previous_active - set(active_accounts)),
                    "scan_reference": "borrow-health-sync",
                    "active_accounts": active_accounts[:100],
                },
            )
            _upsert_scan_config_snapshot(
                cursor,
                config_key="liquidation_high_frequency_pool.latest",
                source_table="liquidation_high_frequency_pool",
                payload={
                    "watch_health_factor": float(watch_health_factor),
                    "active_count": len(high_frequency_accounts),
                    "scan_reference": "borrow-health-sync",
                    "active_accounts": high_frequency_accounts[:100],
                },
            )
            _upsert_scan_config_snapshot(
                cursor,
                config_key="liquidation_core_opportunity_pool.latest",
                source_table="liquidation_core_opportunity_pool",
                payload={
                    "watch_health_factor": float(watch_health_factor),
                    "active_count": len(core_accounts),
                    "scan_reference": "borrow-health-sync",
                    "active_accounts": core_accounts[:100],
                },
            )
    current_active = set(active_accounts)
    return {
        "active_count": len(active_accounts),
        "entered_count": len(current_active - previous_active),
        "exited_count": len(previous_active - current_active),
        "high_frequency_count": len(high_frequency_accounts),
        "core_count": len(core_accounts),
    }


def load_liquidation_borrow_health_pool(database_url: str, limit: int = 500) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT account, health_factor, status, health_factor_band,
                       total_collateral_base, total_debt_base, candidate_count,
                       last_scanned_at, report_json
                FROM liquidation_borrow_health_pool
                WHERE active = TRUE
                ORDER BY health_factor ASC, updated_at DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        report = {}
        try:
            report = json.loads(row[8]) if row[8] else {}
        except json.JSONDecodeError:
            report = {}
        result.append(
            {
                "account": row[0],
                "health_factor": row[1],
                "status": row[2],
                "health_factor_band": row[3],
                "total_collateral_base": row[4],
                "total_debt_base": row[5],
                "candidate_count": row[6],
                "last_scanned_at": row[7].isoformat() if row[7] else None,
                "report": report,
            }
        )
    return result


def load_liquidation_high_frequency_pool(database_url: str, limit: int = 100) -> list[dict[str, Any]]:
    return _load_pool_rows(
        database_url,
        "liquidation_high_frequency_pool",
        "priority_score DESC, health_factor ASC, updated_at DESC",
        limit,
    )


def load_liquidation_core_opportunity_pool(database_url: str, limit: int = 100) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    account, health_factor, priority_score, total_debt_base,
                    total_collateral_base, best_debt_asset, best_collateral_asset,
                    debt_to_cover_units, estimated_operator_net_profit_usd,
                    estimated_gas_cost_usd, quote_viable, static_call_status,
                    payload_state, blocked_reasons_json, last_scanned_at, metadata_json
                FROM liquidation_core_opportunity_pool
                WHERE active = TRUE
                ORDER BY priority_score DESC, health_factor ASC, last_scanned_at DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = cursor.fetchall()
    return [
        {
            "account": row[0],
            "health_factor": row[1],
            "priority_score": row[2],
            "total_debt_base": row[3],
            "total_collateral_base": row[4],
            "best_debt_asset": row[5],
            "best_collateral_asset": row[6],
            "debt_to_cover_units": row[7],
            "estimated_operator_net_profit_usd": row[8],
            "estimated_gas_cost_usd": row[9],
            "quote_viable": row[10],
            "static_call_status": row[11],
            "payload_state": row[12],
            "blocked_reasons": _json_or_default(row[13], []),
            "last_scanned_at": row[14].isoformat() if row[14] else None,
            "metadata": _json_or_default(row[15], {}),
        }
        for row in rows
    ]


def _load_pool_rows(database_url: str, table: str, order_by: str, limit: int) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT account, health_factor, status, total_collateral_base,
                       total_debt_base, candidate_count, priority_score,
                       last_scanned_at, report_json
                FROM {table}
                WHERE active = TRUE
                ORDER BY {order_by}
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = cursor.fetchall()
    return [
        {
            "account": row[0],
            "health_factor": row[1],
            "status": row[2],
            "total_collateral_base": row[3],
            "total_debt_base": row[4],
            "candidate_count": row[5],
            "priority_score": row[6],
            "last_scanned_at": row[7].isoformat() if row[7] else None,
            "report": _json_or_default(row[8], {}),
        }
        for row in rows
    ]


def record_liquidation_borrow_health_scan_batch(
    database_url: str,
    *,
    started_at,
    finished_at,
    status: str,
    account_count: int = 0,
    scanned_count: int = 0,
    risk_count: int = 0,
    error_count: int = 0,
    entered_count: int = 0,
    exited_count: int = 0,
    rpc_url: str | None = None,
    block_number: int | None = None,
    watch_health_factor: float | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_borrow_health_scan_batches (
                    started_at, finished_at, status, account_count, scanned_count,
                    risk_count, error_count, entered_count, exited_count, rpc_url,
                    block_number, watch_health_factor, error, metadata_json
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    started_at,
                    finished_at,
                    status,
                    int(account_count),
                    int(scanned_count),
                    int(risk_count),
                    int(error_count),
                    int(entered_count),
                    int(exited_count),
                    rpc_url,
                    block_number,
                    watch_health_factor,
                    error,
                    _json(metadata or {}),
                ),
            )
            row = cursor.fetchone()
            batch_id = int(row[0]) if row else 0
            _upsert_scan_config_snapshot(
                cursor,
                config_key="liquidation_borrow_health_scan_batches.latest",
                source_table="liquidation_borrow_health_scan_batches",
                source_key=str(batch_id) if batch_id else None,
                payload={
                    "batch_id": batch_id,
                    "status": status,
                    "account_count": int(account_count),
                    "scanned_count": int(scanned_count),
                    "risk_count": int(risk_count),
                    "error_count": int(error_count),
                    "entered_count": int(entered_count),
                    "exited_count": int(exited_count),
                    "rpc_url": rpc_url,
                    "block_number": block_number,
                    "watch_health_factor": watch_health_factor,
                    "started_at": started_at.isoformat() if hasattr(started_at, "isoformat") else str(started_at),
                    "finished_at": finished_at.isoformat() if hasattr(finished_at, "isoformat") else str(finished_at),
                    "metadata": metadata or {},
                },
            )
    batch = dict(metadata or {})
    batch.update(
        {
            "id": batch_id,
            "started_at": started_at.isoformat() if hasattr(started_at, "isoformat") else str(started_at),
            "finished_at": finished_at.isoformat() if hasattr(finished_at, "isoformat") else str(finished_at),
            "status": status,
            "account_count": int(account_count),
            "scanned_count": int(scanned_count),
            "risk_count": int(risk_count),
            "error_count": int(error_count),
            "entered_count": int(entered_count),
            "exited_count": int(exited_count),
            "rpc_url": rpc_url,
            "block_number": block_number,
            "watch_health_factor": watch_health_factor,
            "error": error,
        }
    )
    return batch


def load_liquidation_borrow_health_scan_batches(database_url: str, limit: int = 20) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, started_at, finished_at, status, account_count, scanned_count,
                       risk_count, error_count, entered_count, exited_count, rpc_url,
                       block_number, watch_health_factor, error, metadata_json
                FROM liquidation_borrow_health_scan_batches
                ORDER BY started_at DESC, id DESC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()
    return [
        {
            "id": int(row[0]),
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
            "watch_health_factor": row[12],
            "error": row[13],
            "metadata": _json_or_default(row[14], {}),
        }
        for row in rows
    ]
