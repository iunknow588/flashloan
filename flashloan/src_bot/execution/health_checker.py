from __future__ import annotations

from typing import Any

from execution.profit_guard import calculate_liquidation_profit


def classify_health_factor(health_factor: float, warning_threshold: float, liquidation_threshold: float) -> str:
    if health_factor < liquidation_threshold:
        return "liquidatable"
    if health_factor < warning_threshold:
        return "warning"
    return "healthy"


def health_factor_band(health_factor: float) -> str:
    value = float(health_factor)
    if value < 1.0:
        return "red"
    if value < 1.1:
        return "orange"
    if value < 1.2:
        return "yellow"
    if value < 1.3:
        return "beige"
    return "green"


def estimate_liquidation_profit(
    total_debt_base: float,
    liquidation_bonus_percent: float,
    flashloan_fee_percent: float,
    dex_slippage_percent: float,
    gas_cost_usd: float,
    repay_fraction: float = 0.5,
    mev_buffer_usd: float = 0.0,
    retry_buffer_usd: float = 0.0,
    flashloan_premium_source: str = "fallback_config",
) -> dict[str, Any]:
    result = calculate_liquidation_profit(
        repay_base=max(0.0, float(total_debt_base)) * max(0.0, min(1.0, float(repay_fraction))),
        bonus_rate=max(0.0, float(liquidation_bonus_percent)) / 100.0,
        flashloan_rate=max(0.0, float(flashloan_fee_percent)) / 100.0,
        slippage_rate=max(0.0, float(dex_slippage_percent)) / 100.0,
        gas_cost_usd=gas_cost_usd,
        mev_buffer_usd=mev_buffer_usd,
        retry_buffer_usd=retry_buffer_usd,
        repay_fraction=1.0,
    )
    result["flashloan_premium_source"] = flashloan_premium_source
    result["flashloan_premium_verified"] = flashloan_premium_source != "fallback_config"
    return result
