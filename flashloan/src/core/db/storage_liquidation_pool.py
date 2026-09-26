from __future__ import annotations

import time
from typing import Any

from core.kv_rows import collection_key, count, load_rows, upsert_row
from core.local_kv import ensure_store
from core.liquidation_priority import enrich_liquidation_tier, liquidation_pool_tier
from core.liquidation_pool_mapping import candidate_for, core_profit_assessment, estimated_operator_net_profit_usd
from core.db.storage_liquidation_account_snapshot import load_liquidation_account_pool_snapshot
from core.db.storage_liquidation_reports import (
    account_report_with_summary as _account_report_with_summary,
    account_summary_is_valid as _account_summary_is_valid,
    merge_account_report_sources as _merge_account_report_sources,
)
from core.liquidation_pool_mapping import merge_summary_with_registry as _merge_summary_with_registry


def _market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    return {
        "market_id": str(market_id or "default"),
        "chain_id": int(chain_id) if chain_id is not None else 0,
        "network": str(market_id or "local"),
        "protocol": "aave",
    }


def _core_opportunity_viable(row: dict[str, Any], min_operator_net_profit_usd: float = 1.0) -> bool:
    try:
        debt_base = float(row.get("total_debt_base") or row.get("total_debt_in_base_currency") or 0.0)
    except (TypeError, ValueError):
        debt_base = 0.0
    return debt_base > 0 and estimated_operator_net_profit_usd(row) > 0


def _filtered_rows(database_url: str, collection: str, market_id: str | None = None, chain_id: int | None = None) -> list[dict]:
    scope = _market_scope(market_id, chain_id)
    rows = load_rows(database_url, collection)
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == scope["market_id"]]
    if chain_id is not None:
        rows = [row for row in rows if int(row.get("chain_id") or 0) == scope["chain_id"]]
    return rows


def sync_liquidation_borrow_health_pool(
    database_url: str,
    rows: list[dict[str, Any]],
    watch_health_factor: float = 1.5,
    account_reports: list[dict[str, Any]] | None = None,
    min_operator_net_profit_usd: float = 1.0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, int]:
    ensure_store(database_url)
    scope = _market_scope(market_id, chain_id)
    active_accounts = []
    high_frequency_accounts = []
    core_accounts = []
    for row in rows:
        account = str(row.get("account") or "").strip()
        health_factor = row.get("health_factor")
        if not account or not isinstance(health_factor, (int, float)):
            continue
        tier = liquidation_pool_tier(health_factor)
        is_active = float(health_factor) < float(watch_health_factor)
        if is_active:
            active_accounts.append(account)
        if tier == "high_frequency":
            high_frequency_accounts.append(account)
        if tier == "core" and _core_opportunity_viable(row, min_operator_net_profit_usd):
            core_accounts.append(account)
        key = collection_key(scope["market_id"], scope["chain_id"], account.lower())
        payload = {
            **scope,
            "account": account,
            "health_factor": health_factor,
            "status": row.get("status"),
            "health_factor_band": row.get("health_factor_band"),
            "total_collateral_base": row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
            "total_debt_base": row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
            "candidate_count": len(row.get("liquidation_candidates") or []),
            "summary_json": {
                "health_factor": health_factor,
                "status": row.get("status"),
                "health_factor_band": row.get("health_factor_band"),
                "pool_tier": tier,
            },
            "report_json": row,
            "active": is_active,
            "last_scanned_at": time.time(),
            "updated_at": time.time(),
        }
        upsert_row(database_url, "liquidation_borrow_health_pool", key, payload)
        if tier == "high_frequency":
            enriched = enrich_liquidation_tier(row)
            upsert_row(
                database_url,
                "liquidation_high_frequency_pool",
                key,
                {
                    **scope,
                    "account": account,
                    "health_factor": health_factor,
                    "priority_score": enriched["priority_score"],
                    "summary_json": payload["summary_json"],
                    "report_json": row,
                    "active": True,
                    "last_scanned_at": time.time(),
                    "updated_at": time.time(),
                },
            )
        if tier == "core" and _core_opportunity_viable(row, min_operator_net_profit_usd):
            candidate = candidate_for(row)
            profit = candidate.get("estimated_profit") or row.get("liquidation_profit") or {}
            profit_assessment = core_profit_assessment(row, min_operator_net_profit_usd)
            upsert_row(
                database_url,
                "liquidation_core_opportunity_pool",
                key,
                {
                    **scope,
                    "account": account,
                    "health_factor": health_factor,
                    "priority_score": float(row.get("priority_score") or 0),
                    "total_debt_base": payload["total_debt_base"],
                    "total_collateral_base": payload["total_collateral_base"],
                    "best_debt_asset": candidate.get("debt_asset") or candidate.get("debt_symbol"),
                    "best_collateral_asset": candidate.get("collateral_asset") or candidate.get("collateral_symbol"),
                    "debt_to_cover_units": str(
                        candidate.get("amount_to_pass_to_liquidation_call")
                        or candidate.get("max_debt_to_liquidate")
                        or ""
                    ),
                    "estimated_operator_net_profit_usd": estimated_operator_net_profit_usd(row),
                    "estimated_gas_cost_usd": profit.get("gas_cost_usd"),
                    "quote_viable": bool(candidate),
                    "static_call_status": row.get("static_call_status"),
                    "payload_state": profit_assessment["label"],
                    "blocked_reasons_json": profit_assessment["blocked_reasons"],
                    "report_json": row,
                    "active": True,
                    "last_scanned_at": time.time(),
                },
            )
    return {
        "active_count": len(active_accounts),
        "entered_count": 0,
        "exited_count": 0,
        "high_frequency_count": len(high_frequency_accounts),
        "core_count": len(core_accounts),
    }


def _load_pool_rows(
    database_url: str,
    collection: str,
    order_by: str,
    limit: int,
    offset: int,
    *,
    scope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = _filtered_rows(database_url, collection, scope.get("market_id") if scope else None, scope.get("chain_id") if scope else None)
    rows = [row for row in rows if bool(row.get("active"))]
    rows.sort(key=lambda row: (row.get("health_factor") if row.get("health_factor") is not None else 1e9,))
    return rows[int(offset):int(offset) + int(limit)]


def load_liquidation_borrow_health_pool(
    database_url: str,
    limit: int = 500,
    offset: int = 0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = _filtered_rows(database_url, "liquidation_borrow_health_pool", market_id, chain_id)
    rows = [row for row in rows if bool(row.get("active"))]
    rows.sort(key=lambda row: float(row.get("health_factor") or 0))
    return rows[max(0, int(offset)):max(0, int(offset)) + int(limit)]


def load_liquidation_high_frequency_pool(
    database_url: str,
    limit: int = 100,
    offset: int = 0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = _filtered_rows(database_url, "liquidation_high_frequency_pool", market_id, chain_id)
    rows = [row for row in rows if bool(row.get("active"))]
    rows.sort(key=lambda row: float(row.get("priority_score") or 0), reverse=True)
    return rows[max(0, int(offset)):max(0, int(offset)) + int(limit)]


def load_liquidation_core_opportunity_pool(
    database_url: str,
    limit: int = 100,
    offset: int = 0,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = _filtered_rows(database_url, "liquidation_core_opportunity_pool", market_id, chain_id)
    rows = [row for row in rows if bool(row.get("active"))]
    rows.sort(key=lambda row: float(row.get("priority_score") or 0), reverse=True)
    return rows[max(0, int(offset)):max(0, int(offset)) + int(limit)]


def load_liquidation_pool_counts(
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, int]:
    return {
        "borrow_health_count": len(_filtered_rows(database_url, "liquidation_borrow_health_pool", market_id, chain_id)),
        "high_frequency_count": len(_filtered_rows(database_url, "liquidation_high_frequency_pool", market_id, chain_id)),
        "core_opportunity_count": len(_filtered_rows(database_url, "liquidation_core_opportunity_pool", market_id, chain_id)),
    }


def load_liquidation_accounts_for_assets(
    database_url: str,
    assets: list[str],
    limit: int = 500,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[str]:
    assets = {str(asset).strip() for asset in assets if str(asset or "").strip()}
    if not assets:
        return []
    rows = _filtered_rows(database_url, "liquidation_core_opportunity_pool", market_id, chain_id)
    result = []
    for row in rows:
        report = row.get("report_json") or {}
        collaterals = set()
        for key in ("best_collateral_asset", "collateral_asset"):
            if row.get(key):
                collaterals.add(str(row[key]))
        if collaterals.intersection(assets):
            account = str(row.get("account") or "")
            if account and account not in result:
                result.append(account)
        if len(result) >= int(limit):
            break
    return result


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
    ensure_store(database_url)
    scope = _market_scope(market_id, chain_id)
    batch_id = count(database_url, "liquidation_borrow_health_scan_batches") + 1
    batch = {
        "id": batch_id,
        **scope,
        "started_at": str(started_at),
        "finished_at": str(finished_at),
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
        "metadata": metadata or {},
    }
    upsert_row(database_url, "liquidation_borrow_health_scan_batches", str(batch_id), batch)
    return batch


def load_liquidation_borrow_health_scan_batches(
    database_url: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = _filtered_rows(database_url, "liquidation_borrow_health_scan_batches", market_id, chain_id)
    rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]


__all__ = [
    "_account_report_with_summary",
    "_account_summary_is_valid",
    "_merge_account_report_sources",
    "_merge_summary_with_registry",
    "load_liquidation_accounts_for_assets",
    "load_liquidation_account_pool_snapshot",
    "load_liquidation_borrow_health_pool",
    "load_liquidation_borrow_health_scan_batches",
    "load_liquidation_core_opportunity_pool",
    "load_liquidation_high_frequency_pool",
    "load_liquidation_pool_counts",
    "record_liquidation_borrow_health_scan_batch",
    "sync_liquidation_borrow_health_pool",
]
