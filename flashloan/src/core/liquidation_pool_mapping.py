"""Pure row mapping and profitability helpers for liquidation pool storage."""

from __future__ import annotations

from typing import Any


def merge_summary_with_registry(summary: dict[str, Any], registry: tuple[Any, ...] | None) -> dict[str, Any]:
    merged = dict(summary or {})
    if not registry:
        return merged
    registry_values = {
        "health_factor": registry[8], "status": registry[9], "health_factor_band": registry[10],
        "candidate_count": registry[11], "total_collateral_base": registry[12], "total_debt_base": registry[13],
    }
    for key, value in registry_values.items():
        if value is None:
            continue
        current = merged.get(key)
        if current in (None, "") or (key == "status" and current == "error" and value != "error"):
            merged[key] = value
    return merged


def candidate_for(row: dict[str, Any]) -> dict[str, Any]:
    candidates = row.get("liquidation_candidates") or []
    if isinstance(candidates, list) and candidates:
        return candidates[0] or {}
    return row.get("recommended_candidate") or {}


def estimated_operator_net_profit_usd(row: dict[str, Any]) -> float:
    candidate = candidate_for(row)
    profit = candidate.get("estimated_profit") or row.get("liquidation_profit") or {}
    try:
        return float(
            profit.get("operator_net_profit_usd")
            or profit.get("operator_net_profit_estimate_usd")
            or profit.get("net_profit_usd")
            or profit.get("net_profit_base")
            or 0.0
        )
    except (TypeError, ValueError):
        return 0.0


def health_factor_or_none(row: dict[str, Any]) -> float | None:
    try:
        return float(row.get("health_factor"))
    except (TypeError, ValueError):
        return None


def core_profit_assessment(row: dict[str, Any], min_operator_net_profit_usd: float) -> dict[str, Any]:
    net_profit = estimated_operator_net_profit_usd(row)
    above_threshold = net_profit > float(min_operator_net_profit_usd)
    health_factor = health_factor_or_none(row)
    status = str(row.get("status") or "").strip().lower()
    candidate_present = bool(candidate_for(row))
    blocked_reasons: list[str] = []
    if (health_factor is not None and health_factor >= 1.0) or (status and status != "liquidatable"):
        blocked_reasons.append("account_not_liquidatable")
    if not candidate_present:
        blocked_reasons.append("no_liquidation_candidate")
    if not above_threshold:
        blocked_reasons.append("profit_below_minimum")
    if "account_not_liquidatable" in blocked_reasons:
        label = "watch_only_not_liquidatable"
    elif "no_liquidation_candidate" in blocked_reasons:
        label = "no_executable_candidate"
    elif above_threshold:
        label = "over_1u_candidate"
    else:
        label = "low_profit_manual_test"
    return {
        "estimated_operator_net_profit_usd": net_profit,
        "min_operator_net_profit_usd": float(min_operator_net_profit_usd),
        "above_auto_profit_threshold": above_threshold,
        "manual_review_required": True,
        "auto_execution_blocked": bool(blocked_reasons),
        "executable_candidate_present": candidate_present,
        "blocked_reasons": blocked_reasons,
        "label": label,
    }

