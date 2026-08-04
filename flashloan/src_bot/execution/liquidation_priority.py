from __future__ import annotations

from typing import Any


def liquidation_pool_tier(health_factor: float | int | None) -> str:
    try:
        hf = float(health_factor)
    except (TypeError, ValueError):
        return "unknown"
    if hf <= 1.01:
        return "core"
    if hf < 1.1:
        return "high_frequency"
    if hf < 1.5:
        return "borrow_health"
    return "healthy"


def liquidation_account_activity_tier(row: dict[str, Any] | None) -> str:
    row = row or {}
    status = str(row.get("last_status") or "").lower()
    try:
        health_factor = float(row.get("last_health_factor"))
    except (TypeError, ValueError):
        health_factor = None
    try:
        debt_base = float(row.get("last_total_debt_base") or row.get("total_debt_base") or 0.0)
    except (TypeError, ValueError):
        debt_base = 0.0
    if status in {"liquidatable", "warning"}:
        return "hot"
    if health_factor is not None and health_factor < 1.5:
        return "hot"
    if debt_base > 0 or status:
        return "warm"
    return "cold"


def liquidation_priority_score(row: dict[str, Any]) -> float:
    try:
        health_factor = float(row.get("health_factor"))
    except (TypeError, ValueError):
        health_factor = 10.0
    try:
        debt_base = float(row.get("total_debt_base") or row.get("total_debt_in_base_currency") or 0.0)
    except (TypeError, ValueError):
        debt_base = 0.0
    candidate = row.get("recommended_candidate") or {}
    profit = candidate.get("estimated_profit") or row.get("liquidation_profit") or {}
    try:
        net_profit = float(profit.get("net_profit_base") or profit.get("net_profit_usd") or 0.0)
    except (TypeError, ValueError):
        net_profit = 0.0
    try:
        gas_cost = float(profit.get("gas_cost_usd") or row.get("estimated_gas_cost_usd") or 0.0)
    except (TypeError, ValueError):
        gas_cost = 0.0
    hf_score = max(0.0, 1.5 - health_factor) * 1000.0
    debt_score = min(debt_base / 1_000_000.0, 250.0)
    profit_score = max(net_profit, 0.0) / 10_000.0
    quote_score = 25.0 if candidate else 0.0
    return round(hf_score + debt_score + profit_score + quote_score - gas_cost, 6)


def enrich_liquidation_tier(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    tier = liquidation_pool_tier(item.get("health_factor"))
    item["pool_tier"] = tier
    item["priority_score"] = liquidation_priority_score(item)
    return item
