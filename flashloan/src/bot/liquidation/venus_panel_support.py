"""Pure display and mapping helpers for the BNB Venus control panel views."""

from __future__ import annotations

import math
from typing import Any

from bot.execution.health_checker import health_factor_band
from bot.execution.liquidation_priority import liquidation_pool_tier
from bot.execution.venus_liquidation_scan import venus_health_factor


def _page(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _page_size(value: object, default: int = 20) -> int:
    try:
        return max(1, min(100, int(value or default)))
    except (TypeError, ValueError):
        return default


def _pagination(page: int, page_size: int, total_count: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "page_count": max(1, math.ceil(total_count / page_size)),
    }


def _slice(rows: list[dict], page: int, page_size: int) -> list[dict]:
    start = (page - 1) * page_size
    return rows[start : start + page_size]


def _as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _refresh_pool_tier(health_factor: float | int | None) -> str:
    tier = liquidation_pool_tier(health_factor)
    return tier if tier in {"core", "high_frequency"} else "borrow_health"


def _health_fields(summary: dict[str, Any], risk: dict[str, Any]) -> tuple[float | None, str | None, str]:
    health_factor = _as_float(summary.get("health_factor"))
    if health_factor is None:
        health_factor = _as_float(risk.get("health_factor"))
    source = str(summary.get("health_factor_source") or risk.get("health_factor_source") or "")
    if health_factor is None:
        health_factor = venus_health_factor(
            risk.get("liquidity_raw", summary.get("liquidity_raw")),
            risk.get("shortfall_raw", summary.get("shortfall_raw")),
            summary.get("total_debt_value_usd", summary.get("total_debt_base")),
        )
        source = source or "venus_account_liquidity"
    if health_factor is None:
        return None, None, source or "venus_liquidity_only"
    return health_factor, health_factor_band(health_factor), source or "venus_account_liquidity"


def _normalized_positions(
    positions: list[dict[str, Any]],
    health_factor: float | None,
    health_factor_band_value: str | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        normalized.append(
            {
                "symbol": position.get("underlying_symbol") or position.get("vtoken_symbol") or "-",
                "token_symbol": position.get("underlying_symbol") or position.get("vtoken_symbol") or "-",
                "oracle_price": position.get("oracle_price_usd"),
                "current_token_price": position.get("oracle_price_usd"),
                "collateral_amount": position.get("supply_underlying_amount"),
                "collateral_value_base": position.get("collateral_value_usd"),
                "stable_debt_amount": 0,
                "variable_debt_amount": position.get("borrow_amount"),
                "debt_value_base": position.get("debt_value_usd"),
                "usage_as_collateral_enabled": bool(position.get("entered_market")),
                "health_factor": health_factor,
                "health_factor_band": health_factor_band_value,
            }
        )
    return normalized


def _normalized_candidates(candidates: object) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        rows.append(
            {
                **candidate,
                "collateral_symbol": candidate.get("collateral_symbol") or candidate.get("collateral_asset"),
                "debt_symbol": candidate.get("debt_symbol") or candidate.get("debt_asset"),
                "collateral_price": candidate.get("collateral_price") or candidate.get("collateral_value_usd"),
                "debt_price": candidate.get("debt_price") or candidate.get("oracle_price_usd"),
                "debt_amount": candidate.get("debt_amount"),
                "amount_to_pass_to_liquidation_call_amount": candidate.get("repay_value_usd"),
                "estimated_profit": candidate.get("estimated_profit") or candidate.get("liquidation_profit") or {},
            }
        )
    return rows


def _health_display(
    health_factor: float | None,
    summary: dict[str, Any],
    risk: dict[str, Any],
) -> str:
    if health_factor is not None:
        return f"{health_factor:.3f}"
    liquidity = risk.get("liquidity_raw", summary.get("liquidity_raw"))
    shortfall = risk.get("shortfall_raw", summary.get("shortfall_raw"))
    if liquidity is not None or shortfall is not None:
        return f"L {liquidity or 0} / S {shortfall or 0}"
    return "-"


__all__ = [
    "_page",
    "_page_size",
    "_pagination",
    "_slice",
    "_as_float",
    "_refresh_pool_tier",
    "_health_fields",
    "_normalized_positions",
    "_normalized_candidates",
    "_health_display",
]
