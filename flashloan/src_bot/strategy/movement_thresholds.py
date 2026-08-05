from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy.limits import (
    DEFAULT_ARBITRAGE_ROUTE_TRADE_FEE_HOPS,
    DEFAULT_ARBITRAGE_TARGET_PROFIT_PERCENT,
    normalize_min_paper_profit_usd,
)


@dataclass(frozen=True)
class MovementThresholdConfig:
    trade_fee_percent: float
    flashloan_fee_percent: float
    target_profit_percent: float = DEFAULT_ARBITRAGE_TARGET_PROFIT_PERCENT
    route_trade_fee_hops: int = DEFAULT_ARBITRAGE_ROUTE_TRADE_FEE_HOPS


@dataclass(frozen=True)
class MovementThresholds:
    min_up_change_percent: float
    min_down_change_percent: float
    min_window_spread_percent: float
    trade_fee_percent: float
    flashloan_fee_percent: float
    target_profit_percent: float
    route_trade_fee_hops: int
    source: str
    flashloan_premium: dict[str, Any]


def effective_route_trade_fee_percent(trade_fee_percent: float, route_trade_fee_hops: int) -> float:
    fee_rate = max(0.0, float(trade_fee_percent)) / 100.0
    hops = max(1, int(route_trade_fee_hops))
    return (1.0 - (1.0 - fee_rate) ** hops) * 100.0


def calculate_movement_thresholds(
    config: MovementThresholdConfig,
    *,
    flashloan_premium: dict[str, Any] | None = None,
) -> MovementThresholds:
    premium = flashloan_premium or {}
    flashloan_fee_percent = max(
        0.0,
        float(premium.get("premium_percent") if premium.get("premium_percent") is not None else config.flashloan_fee_percent),
    )
    route_trade_fee_percent = effective_route_trade_fee_percent(
        config.trade_fee_percent,
        config.route_trade_fee_hops,
    )
    target_profit_percent = max(0.0, float(config.target_profit_percent))
    required_spread_percent = route_trade_fee_percent + flashloan_fee_percent + target_profit_percent
    side_threshold_percent = required_spread_percent / 2.0
    return MovementThresholds(
        min_up_change_percent=side_threshold_percent,
        min_down_change_percent=side_threshold_percent,
        min_window_spread_percent=required_spread_percent,
        trade_fee_percent=route_trade_fee_percent,
        flashloan_fee_percent=flashloan_fee_percent,
        target_profit_percent=target_profit_percent,
        route_trade_fee_hops=max(1, int(config.route_trade_fee_hops)),
        source=str(premium.get("source") or "fallback_config"),
        flashloan_premium=premium,
    )


def enforce_min_paper_profit_usd(value: float) -> float:
    return normalize_min_paper_profit_usd(value)
