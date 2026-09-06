import json
from typing import Any

from core.liquidation_pool_mapping import (
    candidate_for as _candidate_for,
    core_profit_assessment as _core_profit_assessment,
    estimated_operator_net_profit_usd as _estimated_operator_net_profit_usd,
    health_factor_or_none as _health_factor_or_none,
    merge_summary_with_registry as _merge_summary_with_registry,
)

from core.db.scoped_adapter import validate_scoped_row
from core.db.storage_common import db_connection, db_transaction, require_psycopg
from core.db.storage_liquidation_accounts import write_liquidation_account_scan_reports
from core.db.storage_liquidation_reports import (
    account_report_with_summary as _account_report_with_summary,
    account_summary_is_valid as _account_summary_is_valid,
    json_or_default as _json_or_default,
    load_historical_account_report_sources,
    merge_account_report_sources as _merge_account_report_sources,
)
from core.liquidation_priority import enrich_liquidation_tier, liquidation_pool_tier
from core.db import storage_liquidation_pool_helpers as _storage_liquidation_pool_helpers
from core.db.storage_liquidation_pool_helpers import (
    borrow_health_refresh_seconds as _borrow_health_refresh_seconds,
    cache_lookup as _cache_lookup,
    cache_namespace as _cache_namespace,
    cache_store as _cache_store,
    core_opportunity_refresh_seconds as _core_opportunity_refresh_seconds,
    database_cache_key as _database_cache_key,
    high_frequency_refresh_seconds as _high_frequency_refresh_seconds,
    invalidate_pool_cache as _invalidate_pool_cache,
    json_value as _json,
    market_scope as _market_scope,
    pool_counts_refresh_seconds as _pool_counts_refresh_seconds,
    scoped_account_key as _scoped_account_key,
    upsert_scan_config_snapshot as _upsert_scan_config_snapshot,
    write_changed as _write_changed,
)
from core.db.liquidation_pool_rows import (
    borrow_health_scan_batch_from_row as _borrow_health_scan_batch_from_row,
)

# Backward-compatible handles for tests and callers that clear runtime caches.
_POOL_READ_CACHE = _storage_liquidation_pool_helpers._POOL_READ_CACHE
_POOL_WRITE_CACHE = _storage_liquidation_pool_helpers._POOL_WRITE_CACHE


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


_legacy_merge_summary_with_registry = _merge_summary_with_registry
_legacy_candidate_for = _candidate_for
_legacy_estimated_operator_net_profit_usd = _estimated_operator_net_profit_usd
_legacy_health_factor_or_none = _health_factor_or_none
_legacy_core_profit_assessment = _core_profit_assessment


def _core_opportunity_viable(
    row: dict[str, Any],
    min_operator_net_profit_usd: float = 1.0,
) -> bool:
    net_profit = _estimated_operator_net_profit_usd(row)
    try:
        debt_base = float(row.get("total_debt_base") or row.get("total_debt_in_base_currency") or 0.0)
    except (TypeError, ValueError):
        debt_base = 0.0
    return debt_base > 0 and net_profit > 0


# === WRITE OPERATIONS ===
# These helpers upsert scan snapshots and refresh pool state.

def sync_liquidation_borrow_health_pool(
    database_url: str,
    rows: list[dict[str, Any]],
    watch_health_factor: float = 1.5,
    account_reports: list[dict[str, Any]] | None = None,
    min_operator_net_profit_usd: float = 1.0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, int]:
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    active_accounts: list[str] = []
    high_frequency_accounts: list[str] = []
    core_accounts: list[str] = []
    wrote_anything = False
    with db_connection(database_url, connect_timeout=8) as connection:
        with db_transaction(connection, operation="liquidation.borrow_health_scan_batch"):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT account FROM liquidation_borrow_health_pool
                    WHERE active = TRUE AND market_id = %s AND chain_id = %s
                    """,
                    (scope["market_id"], scope["chain_id"]),
                )
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
                    borrow_payload = {
                        "market_id": scope["market_id"],
                        "chain_id": scope["chain_id"],
                        "account": account,
                        "health_factor": health_factor,
                        "status": row.get("status"),
                        "health_factor_band": row.get("health_factor_band"),
                        "total_collateral_base": row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                        "total_debt_base": row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                        "candidate_count": len(row.get("liquidation_candidates") or []),
                        "summary_json": summary,
                        "report_json": row,
                        "active": is_active,
                    }
                    if _write_changed(database_url, "liquidation_borrow_health_pool", _scoped_account_key(scope, account), borrow_payload):
                        cursor.execute(
                            """
                            INSERT INTO liquidation_borrow_health_pool (
                                market_id, chain_id, network, protocol,
                                account, health_factor, status, health_factor_band,
                                total_collateral_base, total_debt_base, candidate_count,
                                summary_json, report_json, active, last_scanned_at, updated_at
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                            ON CONFLICT (market_id, chain_id, account) DO UPDATE SET
                                network = EXCLUDED.network,
                                protocol = EXCLUDED.protocol,
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
                                scope["market_id"],
                                scope["chain_id"],
                                scope["network"],
                                scope["protocol"],
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
                        wrote_anything = True
                    if tier == "high_frequency":
                        enriched = enrich_liquidation_tier(row)
                        high_payload = {
                            "market_id": scope["market_id"],
                            "chain_id": scope["chain_id"],
                            "account": account,
                            "health_factor": health_factor,
                            "status": row.get("status"),
                            "total_collateral_base": row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                            "total_debt_base": row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                            "candidate_count": len(row.get("liquidation_candidates") or []),
                            "priority_score": enriched["priority_score"],
                            "summary_json": summary,
                            "report_json": row,
                            "active": True,
                        }
                        if _write_changed(database_url, "liquidation_high_frequency_pool", _scoped_account_key(scope, account), high_payload):
                            cursor.execute(
                                """
                                INSERT INTO liquidation_high_frequency_pool (
                                    market_id, chain_id, network, protocol,
                                    account, health_factor, status, total_collateral_base,
                                    total_debt_base, candidate_count, priority_score,
                                    summary_json, report_json, active, last_scanned_at, updated_at
                                )
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW(),NOW())
                                ON CONFLICT (market_id, chain_id, account) DO UPDATE SET
                                    network = EXCLUDED.network,
                                    protocol = EXCLUDED.protocol,
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
                                    scope["market_id"],
                                    scope["chain_id"],
                                    scope["network"],
                                    scope["protocol"],
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
                            wrote_anything = True
                    core_viable = tier == "core" and _core_opportunity_viable(row, min_operator_net_profit_usd)
                    if core_viable:
                        core_accounts.append(account)
                        candidate = _candidate_for(row)
                        profit = candidate.get("estimated_profit") or row.get("liquidation_profit") or {}
                        enriched = enrich_liquidation_tier(row)
                        profit_assessment = _core_profit_assessment(row, min_operator_net_profit_usd)
                        blocked_reasons = list(profit_assessment.get("blocked_reasons") or [])
                        metadata_json = {
                            "summary": summary,
                            "report": row,
                            "recommended_candidate": candidate,
                            "profit_assessment": profit_assessment,
                        }
                        core_payload = {
                            "market_id": scope["market_id"],
                            "chain_id": scope["chain_id"],
                            "account": account,
                            "health_factor": health_factor,
                            "priority_score": enriched["priority_score"],
                            "total_debt_base": row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                            "total_collateral_base": row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                            "best_debt_asset": candidate.get("debt_asset") or candidate.get("debt_symbol"),
                            "best_collateral_asset": candidate.get("collateral_asset") or candidate.get("collateral_symbol"),
                            "debt_to_cover_units": str(candidate.get("amount_to_pass_to_liquidation_call") or candidate.get("max_debt_to_liquidate") or ""),
                            "estimated_operator_net_profit_usd": _estimated_operator_net_profit_usd(row),
                            "estimated_gas_cost_usd": profit.get("gas_cost_usd"),
                            "quote_viable": bool(candidate),
                            "static_call_status": "pending",
                            "payload_state": profit_assessment["label"],
                            "blocked_reasons_json": blocked_reasons,
                            "metadata_json": metadata_json,
                            "active": True,
                        }
                        if _write_changed(database_url, "liquidation_core_opportunity_pool", _scoped_account_key(scope, account), core_payload):
                            cursor.execute(
                                """
                                INSERT INTO liquidation_core_opportunity_pool (
                                    market_id, chain_id, network, protocol,
                                    account, health_factor, priority_score, total_debt_base,
                                    total_collateral_base, best_debt_asset, best_collateral_asset,
                                    debt_to_cover_units, estimated_operator_net_profit_usd,
                                    estimated_gas_cost_usd, quote_viable, static_call_status,
                                    payload_state, blocked_reasons_json, last_scanned_at,
                                    updated_at,
                                    active, metadata_json
                                )
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),TRUE,%s)
                                ON CONFLICT (market_id, chain_id, account) DO UPDATE SET
                                    network = EXCLUDED.network,
                                    protocol = EXCLUDED.protocol,
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
                                    scope["market_id"],
                                    scope["chain_id"],
                                    scope["network"],
                                    scope["protocol"],
                                    account,
                                    health_factor,
                                    enriched["priority_score"],
                                    row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                                    row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                                    candidate.get("debt_asset") or candidate.get("debt_symbol"),
                                    candidate.get("collateral_asset") or candidate.get("collateral_symbol"),
                                    str(candidate.get("amount_to_pass_to_liquidation_call") or candidate.get("max_debt_to_liquidate") or ""),
                                    _estimated_operator_net_profit_usd(row),
                                    profit.get("gas_cost_usd"),
                                    bool(candidate),
                                    "pending",
                                    profit_assessment["label"],
                                    _json(blocked_reasons),
                                    _json(metadata_json),
                                ),
                            )
                            wrote_anything = True
                if _write_changed(
                    database_url,
                    "liquidation_borrow_health_pool.active",
                    f"{scope['market_id']}:{scope['chain_id']}:active_set",
                    {"active_accounts": sorted(active_accounts), "watch_health_factor": float(watch_health_factor), "market_id": scope["market_id"], "chain_id": scope["chain_id"]},
                ):
                    cursor.execute(
                        "UPDATE liquidation_borrow_health_pool SET active = FALSE, updated_at = NOW() "
                        "WHERE active = TRUE AND market_id = %s AND chain_id = %s AND health_factor >= %s",
                        (scope["market_id"], scope["chain_id"], float(watch_health_factor)),
                    )
                    wrote_anything = True
                if _write_changed(
                    database_url,
                    "liquidation_high_frequency_pool.active",
                    f"{scope['market_id']}:{scope['chain_id']}:active_set",
                    {"active_accounts": sorted(high_frequency_accounts), "market_id": scope["market_id"], "chain_id": scope["chain_id"]},
                ):
                    cursor.execute(
                        "UPDATE liquidation_high_frequency_pool SET active = FALSE, updated_at = NOW() "
                        "WHERE active = TRUE AND market_id = %s AND chain_id = %s AND NOT (account = ANY(%s))",
                        (scope["market_id"], scope["chain_id"], high_frequency_accounts or ["__none__"]),
                    )
                    wrote_anything = True
                if _write_changed(
                    database_url,
                    "liquidation_core_opportunity_pool.active",
                    f"{scope['market_id']}:{scope['chain_id']}:active_set",
                    {"active_accounts": sorted(core_accounts), "market_id": scope["market_id"], "chain_id": scope["chain_id"]},
                ):
                    cursor.execute(
                        "UPDATE liquidation_core_opportunity_pool SET active = FALSE, updated_at = NOW() "
                        "WHERE active = TRUE AND market_id = %s AND chain_id = %s AND NOT (account = ANY(%s))",
                        (scope["market_id"], scope["chain_id"], core_accounts or ["__none__"]),
                    )
                    wrote_anything = True
                _upsert_scan_config_snapshot(
                    cursor,
                    database_url=database_url,
                    config_key="liquidation_borrow_health_pool.latest",
                    source_table="liquidation_borrow_health_pool",
                    payload={
                        "watch_health_factor": float(watch_health_factor),
                        "active_count": len(active_accounts),
                        "market_id": scope["market_id"],
                        "chain_id": scope["chain_id"],
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
                    database_url=database_url,
                    config_key="liquidation_high_frequency_pool.latest",
                    source_table="liquidation_high_frequency_pool",
                    payload={
                        "watch_health_factor": float(watch_health_factor),
                        "market_id": scope["market_id"],
                        "chain_id": scope["chain_id"],
                        "active_count": len(high_frequency_accounts),
                        "scan_reference": "borrow-health-sync",
                        "active_accounts": high_frequency_accounts[:100],
                    },
                )
                _upsert_scan_config_snapshot(
                    cursor,
                    database_url=database_url,
                    config_key="liquidation_core_opportunity_pool.latest",
                    source_table="liquidation_core_opportunity_pool",
                    payload={
                        "watch_health_factor": float(watch_health_factor),
                        "market_id": scope["market_id"],
                        "chain_id": scope["chain_id"],
                        "min_operator_net_profit_usd": float(min_operator_net_profit_usd),
                        "active_count": len(core_accounts),
                        "scan_reference": "borrow-health-sync",
                        "active_accounts": core_accounts[:100],
                    },
                )
                if account_reports:
                    scan_reports = []
                    for report in account_reports:
                        scoped_report = dict(report or {})
                        scoped_report.setdefault("market_id", scope["market_id"])
                        scoped_report.setdefault("chain_id", scope["chain_id"])
                        key = _scoped_account_key(scope, str(scoped_report.get("account") or ""))
                        if _write_changed(database_url, "liquidation_account_health_scans", key, scoped_report):
                            scan_reports.append(scoped_report)
                    if scan_reports:
                        write_liquidation_account_scan_reports(cursor, scan_reports)
                        wrote_anything = True
    current_active = set(active_accounts)
    if wrote_anything:
        _invalidate_pool_cache(
            database_url,
            "liquidation_borrow_health_pool",
            "liquidation_high_frequency_pool",
            "liquidation_core_opportunity_pool",
            "liquidation_pool_counts",
        )
    return {
        "active_count": len(active_accounts),
        "entered_count": len(current_active - previous_active),
        "exited_count": len(previous_active - current_active),
        "high_frequency_count": len(high_frequency_accounts),
        "core_count": len(core_accounts),
    }


# === READ OPERATIONS ===
# These helpers only query liquidation pool snapshots and counts.

def load_liquidation_borrow_health_pool(
    database_url: str,
    limit: int = 500,
    offset: int = 0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    scope = _market_scope(market_id, chain_id)
    cache_key = (_database_cache_key(database_url), _cache_namespace("liquidation_borrow_health_pool", scope), int(limit), max(0, int(offset)))
    cached = _cache_lookup(cache_key, _borrow_health_refresh_seconds())
    if cached is not None:
        return cached
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT market_id, chain_id, network, protocol,
                       account, health_factor, status, health_factor_band,
                       total_collateral_base, total_debt_base, candidate_count,
                       last_scanned_at, report_json
                FROM liquidation_borrow_health_pool
                WHERE active = TRUE AND market_id = %s AND chain_id = %s
                ORDER BY health_factor ASC, updated_at DESC
                LIMIT %s OFFSET %s
                """,
                (scope["market_id"], scope["chain_id"], int(limit), max(0, int(offset))),
            )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        if len(row) == 9:
            row = (
                scope["market_id"],
                scope["chain_id"],
                scope["network"],
                scope["protocol"],
                *row,
            )
        report = {}
        try:
            report = json.loads(row[12]) if row[12] else {}
        except json.JSONDecodeError:
            report = {}
        result.append(
            {
                "market_id": row[0],
                "chain_id": row[1],
                "network": row[2],
                "protocol": row[3],
                "account": row[4],
                "health_factor": row[5],
                "status": row[6],
                "health_factor_band": row[7],
                "total_collateral_base": row[8],
                "total_debt_base": row[9],
                "candidate_count": row[10],
                "last_scanned_at": row[11].isoformat() if row[11] else None,
                "report": report,
            }
        )
    _cache_store(cache_key, result)
    return result


def load_liquidation_high_frequency_pool(
    database_url: str,
    limit: int = 100,
    offset: int = 0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    scope = _market_scope(market_id, chain_id)
    cache_key = (_database_cache_key(database_url), _cache_namespace("liquidation_high_frequency_pool", scope), int(limit), max(0, int(offset)))
    cached = _cache_lookup(cache_key, _high_frequency_refresh_seconds())
    if cached is not None:
        return cached
    result = _load_pool_rows(
        database_url,
        "liquidation_high_frequency_pool",
        "priority_score DESC, health_factor ASC, updated_at DESC",
        limit,
        offset,
        scope=scope,
    )
    _cache_store(cache_key, result)
    return result


def load_liquidation_core_opportunity_pool(
    database_url: str,
    limit: int = 100,
    offset: int = 0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    scope = _market_scope(market_id, chain_id)
    cache_key = (_database_cache_key(database_url), _cache_namespace("liquidation_core_opportunity_pool", scope), int(limit), max(0, int(offset)))
    cached = _cache_lookup(cache_key, _core_opportunity_refresh_seconds())
    if cached is not None:
        return cached
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    market_id, chain_id, network, protocol,
                    account, health_factor, priority_score, total_debt_base,
                    total_collateral_base, best_debt_asset, best_collateral_asset,
                    debt_to_cover_units, estimated_operator_net_profit_usd,
                    estimated_gas_cost_usd, quote_viable, static_call_status,
                    payload_state, blocked_reasons_json, last_scanned_at, metadata_json
                FROM liquidation_core_opportunity_pool
                WHERE active = TRUE AND market_id = %s AND chain_id = %s
                ORDER BY priority_score DESC, health_factor ASC, last_scanned_at DESC
                    LIMIT %s OFFSET %s
                """,
                (scope["market_id"], scope["chain_id"], int(limit), max(0, int(offset))),
            )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        if len(row) == 16:
            row = (
                scope["market_id"],
                scope["chain_id"],
                scope["network"],
                scope["protocol"],
                *row,
            )
        metadata = _json_or_default(row[19], {})
        profit_assessment = metadata.get("profit_assessment") if isinstance(metadata, dict) else None
        if not isinstance(profit_assessment, dict):
            profit = _float_or_zero(row[12])
            above_threshold = profit > 1.0
            profit_assessment = {
                "estimated_operator_net_profit_usd": profit,
                "min_operator_net_profit_usd": 1.0,
                "above_auto_profit_threshold": above_threshold,
                "manual_review_required": True,
                "auto_execution_blocked": not above_threshold or "profit_below_minimum" in _json_or_default(row[17], []),
                "label": "over_1u_candidate" if above_threshold else "low_profit_manual_test",
            }
        result.append(
            {
            "market_id": row[0],
            "chain_id": row[1],
            "network": row[2],
            "protocol": row[3],
            "account": row[4],
            "health_factor": row[5],
            "priority_score": row[6],
            "total_debt_base": row[7],
            "total_collateral_base": row[8],
            "best_debt_asset": row[9],
            "best_collateral_asset": row[10],
            "debt_to_cover_units": row[11],
            "estimated_operator_net_profit_usd": row[12],
            "estimated_gas_cost_usd": row[13],
            "quote_viable": row[14],
            "static_call_status": row[15],
            "payload_state": row[16],
            "blocked_reasons": _json_or_default(row[17], []),
            "last_scanned_at": row[18].isoformat() if row[18] else None,
            "metadata": metadata,
            "profit_assessment": profit_assessment,
            "profit_assessment_label": profit_assessment.get("label"),
            "above_auto_profit_threshold": bool(profit_assessment.get("above_auto_profit_threshold")),
            "auto_execution_blocked": bool(profit_assessment.get("auto_execution_blocked")),
            "manual_review_required": bool(profit_assessment.get("manual_review_required", True)),
        }
        )
    _cache_store(cache_key, result)
    return result


def load_liquidation_pool_counts(
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, int]:
    scope = _market_scope(market_id, chain_id)
    cache_key = (_database_cache_key(database_url), _cache_namespace("liquidation_pool_counts", scope), 0, 0)
    cached = _cache_lookup(cache_key, _pool_counts_refresh_seconds())
    if cached is not None:
        return cached
    require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM liquidation_borrow_health_pool WHERE active = TRUE AND market_id = %s AND chain_id = %s),
                    (SELECT COUNT(*) FROM liquidation_high_frequency_pool WHERE active = TRUE AND market_id = %s AND chain_id = %s),
                    (SELECT COUNT(*) FROM liquidation_core_opportunity_pool WHERE active = TRUE AND market_id = %s AND chain_id = %s)
                """,
                (
                    scope["market_id"],
                    scope["chain_id"],
                    scope["market_id"],
                    scope["chain_id"],
                    scope["market_id"],
                    scope["chain_id"],
                ),
            )
            row = cursor.fetchone()
    result = {
        "borrow_health_count": int(row[0] or 0),
        "high_frequency_count": int(row[1] or 0),
        "core_opportunity_count": int(row[2] or 0),
    }
    _cache_store(cache_key, result)
    return result


def load_liquidation_accounts_for_assets(
    database_url: str,
    assets: list[str],
    limit: int = 500,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[str]:
    normalized_assets = [str(asset).strip() for asset in assets if str(asset or "").strip()]
    if not normalized_assets:
        return []
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT account
                FROM liquidation_core_opportunity_pool
                WHERE active = TRUE
                  AND market_id = %s
                  AND chain_id = %s
                  AND (
                    best_debt_asset = ANY(%s)
                    OR best_collateral_asset = ANY(%s)
                  )
                ORDER BY account
                LIMIT %s
                """,
                (scope["market_id"], scope["chain_id"], normalized_assets, normalized_assets, int(limit)),
            )
            return [str(row[0]) for row in cursor.fetchall()]


def load_liquidation_account_pool_snapshot(
    database_url: str,
    account: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    normalized = str(account or "").strip()
    if not normalized:
        return {"found": False, "error": "account is required"}
    scope = _market_scope(market_id, chain_id)
    require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT market_id, chain_id, network, protocol,
                       account, source, active, last_scanned_at, last_health_factor,
                       last_status, last_health_factor_band, last_candidate_count,
                       last_total_collateral_base, last_total_debt_base,
                       activity_tier, last_summary_json, last_report_json
                FROM liquidation_accounts
                WHERE market_id = %s AND chain_id = %s AND lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            registry = cursor.fetchone()
            if registry:
                registry = validate_scoped_row(
                    {
                        "market_id": registry[0],
                        "chain_id": registry[1],
                        "network": registry[2],
                        "protocol": registry[3],
                    },
                    scope,
                ) | {"_raw": registry}
            cursor.execute(
                """
                WITH ranked AS (
                  SELECT row_number() OVER (ORDER BY health_factor ASC, updated_at DESC) AS rn,
                         account, health_factor, status, health_factor_band,
                         total_collateral_base, total_debt_base, candidate_count,
                         last_scanned_at, report_json
                  FROM liquidation_borrow_health_pool
                  WHERE active = TRUE AND market_id = %s AND chain_id = %s
                )
                SELECT * FROM ranked WHERE lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            borrow = cursor.fetchone()
            cursor.execute(
                """
                WITH ranked AS (
                  SELECT row_number() OVER (ORDER BY priority_score DESC, health_factor ASC, last_scanned_at DESC) AS rn,
                         account, health_factor, priority_score, total_collateral_base,
                         total_debt_base, best_debt_asset, best_collateral_asset,
                         debt_to_cover_units, estimated_operator_net_profit_usd,
                         estimated_gas_cost_usd, quote_viable, static_call_status,
                         payload_state, blocked_reasons_json, last_scanned_at, metadata_json
                  FROM liquidation_core_opportunity_pool
                  WHERE active = TRUE AND market_id = %s AND chain_id = %s
                )
                SELECT * FROM ranked WHERE lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            core = cursor.fetchone()
            cursor.execute(
                """
                WITH ranked AS (
                  SELECT row_number() OVER (ORDER BY priority_score DESC, health_factor ASC, updated_at DESC) AS rn,
                         account, health_factor, priority_score, total_collateral_base,
                         total_debt_base, candidate_count, last_scanned_at, report_json
                  FROM liquidation_high_frequency_pool
                  WHERE active = TRUE AND market_id = %s AND chain_id = %s
                )
                SELECT * FROM ranked WHERE lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            high = cursor.fetchone()
            latest_valid_scan, latest_positions_scan = load_historical_account_report_sources(
                cursor,
                market_id=scope["market_id"],
                chain_id=scope["chain_id"],
                account=normalized,
                expected_scope=scope,
            )

    report_sources: list[dict[str, Any]] = []
    recommended_candidate: dict[str, Any] = {}
    if borrow:
        borrow_report = _json_or_default(borrow[9], {})
        if isinstance(borrow_report, dict):
            report_sources.append(borrow_report)
    if core:
        metadata = _json_or_default(core[16], {})
        if isinstance(metadata, dict):
            core_report = metadata.get("report") if isinstance(metadata.get("report"), dict) else {}
            if core_report:
                report_sources.append(core_report)
            recommended_candidate = metadata.get("recommended_candidate") if isinstance(metadata.get("recommended_candidate"), dict) else {}
    if high:
        high_report = _json_or_default(high[8], {})
        if isinstance(high_report, dict):
            report_sources.append(high_report)
    if registry:
        registry_row = registry["_raw"]
        registry_report = _json_or_default(registry_row[16], {})
        if isinstance(registry_report, dict):
            report_sources.append(registry_report)
    current_positions_present = any(
        isinstance(source.get("positions"), list) and bool(source.get("positions"))
        for source in report_sources
        if isinstance(source, dict)
    )
    if latest_valid_scan:
        valid_scan_report = _account_report_with_summary(latest_valid_scan[2], latest_valid_scan[1])
        if valid_scan_report:
            report_sources.append(valid_scan_report)
    if latest_positions_scan:
        positions_scan_report = _account_report_with_summary(latest_positions_scan[2], latest_positions_scan[1])
        if positions_scan_report:
            report_sources.append(positions_scan_report)
    # Pool summaries are optimized for ranking and may omit positions. Merge
    # current pool/registry data first, then use valid history only as fallback.
    report = _merge_account_report_sources(report_sources)

    summary = dict(report.get("summary") or {})
    if not summary and report:
        summary = {
            "health_factor": report.get("health_factor"),
            "status": report.get("status"),
            "health_factor_band": report.get("health_factor_band"),
            "candidate_count": len(report.get("liquidation_candidates") or []),
            "total_collateral_base": report.get("total_collateral_base") or report.get("total_collateral_in_base_currency"),
            "total_debt_base": report.get("total_debt_base") or report.get("total_debt_in_base_currency"),
        }
    if registry:
        summary = _merge_summary_with_registry(summary, registry["_raw"])
    positions = report.get("positions") or []
    if isinstance(positions, list):
        summary.setdefault("positions_count", len(positions))
        summary.setdefault(
            "debt_positions_count",
            sum(
                1
                for position in positions
                if isinstance(position, dict)
                and (
                    _float_or_zero(position.get("stable_debt_amount")) > 0
                    or _float_or_zero(position.get("variable_debt_amount")) > 0
                    or _float_or_zero(position.get("debt_value_base")) > 0
                )
            ),
        )
        summary.setdefault(
            "collateral_positions_count",
            sum(
                1
                for position in positions
                if isinstance(position, dict)
                and (
                    bool(position.get("usage_as_collateral_enabled"))
                    and _float_or_zero(position.get("collateral_amount")) > 0
                )
            ),
        )

    core_payload = None
    if core:
        blocked_reasons = _json_or_default(core[14], [])
        metadata = _json_or_default(core[16], {})
        assessment_row = {
            "health_factor": core[2],
            "status": summary.get("status"),
            "total_debt_base": core[5],
            "recommended_candidate": recommended_candidate or {},
            "liquidation_profit": {"operator_net_profit_usd": core[9], "net_profit_base": core[9]},
        }
        effective_assessment = _core_profit_assessment(
            assessment_row,
            float((metadata.get("profit_assessment") or {}).get("min_operator_net_profit_usd") or 1.0) if isinstance(metadata, dict) else 1.0,
        )
        effective_blocked_reasons = list(effective_assessment.get("blocked_reasons") or blocked_reasons or [])
        core_payload = {
            "rank": int(core[0]),
            "health_factor": core[2],
            "priority_score": core[3],
            "total_collateral_base": core[4],
            "total_debt_base": core[5],
            "best_debt_asset": core[6],
            "best_collateral_asset": core[7],
            "debt_to_cover_units": core[8],
            "estimated_operator_net_profit_usd": core[9],
            "estimated_gas_cost_usd": core[10],
            "quote_viable": bool(core[11]),
            "static_call_status": core[12],
            "payload_state": effective_assessment["label"],
            "blocked_reasons": effective_blocked_reasons,
            "last_scanned_at": core[15].isoformat() if core[15] else None,
            "profit_assessment": effective_assessment,
        }

    return {
        "found": bool(registry or borrow or core or high),
        "cached": True,
        "source": "database_pool_cache",
        "market_id": scope["market_id"],
        "chain_id": scope["chain_id"],
        "account": normalized,
        "summary": summary,
        "liquidation_profit": report.get("liquidation_profit") or {},
        "recommended_candidate": recommended_candidate or None,
        "liquidation_candidates": report.get("liquidation_candidates") or [],
        "positions": positions,
        "positions_source": (
            "database_scan_report"
            if positions and current_positions_present
            else "database_health_scan_history"
            if positions
            else "unavailable"
        ),
        "context": {
            "source": "database_pool_cache",
            "report_scanned_at": (
                latest_valid_scan[0].isoformat()
                if latest_valid_scan and latest_valid_scan[0]
                else None
            ),
            "positions_scanned_at": (
                latest_positions_scan[0].isoformat()
                if latest_positions_scan and latest_positions_scan[0]
                else None
            ),
            "registry": {
                "active": bool(registry["_raw"][6]) if registry else False,
                "market_id": registry["market_id"] if registry else scope["market_id"],
                "chain_id": int(registry["chain_id"]) if registry and registry["chain_id"] is not None else scope["chain_id"],
                "network": registry["network"] if registry else None,
                "protocol": registry["protocol"] if registry else None,
                "source": registry["_raw"][5] if registry else None,
                "activity_tier": registry["_raw"][14] if registry else None,
                "last_scanned_at": registry["_raw"][7].isoformat() if registry and registry["_raw"][7] else None,
            },
            "borrow_pool": {
                "active": bool(borrow),
                "rank": int(borrow[0]) if borrow else None,
                "last_scanned_at": borrow[8].isoformat() if borrow and borrow[8] else None,
            },
            "core_opportunity": core_payload,
            "high_frequency": {
                "active": bool(high),
                "rank": int(high[0]) if high else None,
                "last_scanned_at": high[7].isoformat() if high and high[7] else None,
            },
        },
    }


def _load_pool_rows(
    database_url: str,
    table: str,
    order_by: str,
    limit: int,
    offset: int = 0,
    *,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scope = scope or _market_scope()
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT market_id, chain_id, network, protocol,
                       account, health_factor, status, total_collateral_base,
                       total_debt_base, candidate_count, priority_score,
                       last_scanned_at, report_json
                FROM {table}
                WHERE active = TRUE AND market_id = %s AND chain_id = %s
                ORDER BY {order_by}
                LIMIT %s OFFSET %s
                """,
                (scope["market_id"], scope["chain_id"], int(limit), max(0, int(offset))),
            )
            rows = cursor.fetchall()
    column_names = (
        "market_id", "chain_id", "network", "protocol", "account", "health_factor",
        "status", "total_collateral_base", "total_debt_base", "candidate_count",
        "priority_score", "last_scanned_at", "report_json",
    )
    scoped_rows = validate_scoped_rows(
        [dict(zip(column_names, row)) for row in rows],
        scope,
    )
    return [
        {
            "market_id": row["market_id"],
            "chain_id": row["chain_id"],
            "network": row["network"],
            "protocol": row["protocol"],
            "account": row["account"],
            "health_factor": row["health_factor"],
            "status": row["status"],
            "total_collateral_base": row["total_collateral_base"],
            "total_debt_base": row["total_debt_base"],
            "candidate_count": row["candidate_count"],
            "priority_score": row["priority_score"],
            "last_scanned_at": row["last_scanned_at"].isoformat() if row["last_scanned_at"] else None,
            "report": _json_or_default(row["report_json"], {}),
        }
        for row in scoped_rows
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
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with db_transaction(connection, operation="liquidation.pool_snapshot"):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO liquidation_borrow_health_scan_batches (
                        market_id, chain_id, network, protocol,
                        started_at, finished_at, status, account_count, scanned_count,
                        risk_count, error_count, entered_count, exited_count, rpc_url,
                        block_number, watch_health_factor, error, metadata_json
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        scope["market_id"],
                        scope["chain_id"],
                        scope["network"],
                        scope["protocol"],
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
                    market_id=scope["market_id"],
                    chain_id=scope["chain_id"],
                    payload={
                        "batch_id": batch_id,
                        "market_id": scope["market_id"],
                        "chain_id": scope["chain_id"],
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
            "market_id": scope["market_id"],
            "chain_id": scope["chain_id"],
            "network": scope["network"],
            "protocol": scope["protocol"],
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


def load_liquidation_borrow_health_scan_batches(
    database_url: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT market_id, chain_id, network, protocol,
                       id, started_at, finished_at, status, account_count, scanned_count,
                       risk_count, error_count, entered_count, exited_count, rpc_url,
                       block_number, watch_health_factor, error, metadata_json
                FROM liquidation_borrow_health_scan_batches
                WHERE market_id = %s AND chain_id = %s
                ORDER BY started_at DESC, id DESC
                LIMIT %s
                """,
                (scope["market_id"], scope["chain_id"], max(1, int(limit))),
            )
            rows = cursor.fetchall()
    return [_borrow_health_scan_batch_from_row(row, scope) for row in rows]
