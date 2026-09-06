"""Pure strategy parameter readers used by Binance market services."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone

from core.config_schema import parse_env_float
from market.strategy.limits import resolve_min_paper_profit_usd
from market.strategy.arbitrage import ArbitrageConfig

from .control_panel_config import STRATEGY_DEFAULTS


def configured_fee_slippage_percent() -> float:
    fallback = parse_env_float("ALERT_DIFF_PERCENT", 0.30, minimum=0)[0]
    value, error = parse_env_float("FEE_SLIPPAGE_PERCENT", fallback, minimum=0)
    return fallback if error else value


def strategy_float(
    key: str,
    config: dict,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    default = float(STRATEGY_DEFAULTS[key])
    raw = config.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def strategy_int(
    key: str,
    config: dict,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = int(strategy_float(key, config))
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def read_slippage_bps(config: dict) -> int:
    return strategy_int("EXECUTION_SLIPPAGE_BPS", config, minimum=0, maximum=5000)


def read_execution_plan_max_age_seconds(config: dict) -> float:
    return float(strategy_int("EXECUTION_PLAN_MAX_AGE_SECONDS", config, minimum=1))


def arbitrage_config_from_strategy(config: dict) -> ArbitrageConfig:
    notional_usd = strategy_float("ARBITRAGE_NOTIONAL_USD", config, minimum=0.0)
    target_profit_percent = strategy_float(
        "ARBITRAGE_TARGET_PROFIT_PERCENT",
        config,
        minimum=0.0,
    )
    configured_min_paper_profit_usd = strategy_float(
        "ARBITRAGE_MIN_PAPER_PROFIT_USD",
        config,
        minimum=0.0,
    )
    return ArbitrageConfig(
        notional_usd=notional_usd,
        trade_fee_percent=strategy_float("ARBITRAGE_TRADE_FEE_PERCENT", config, minimum=0.0),
        flashloan_fee_percent=strategy_float("ARBITRAGE_FLASHLOAN_FEE_PERCENT", config, minimum=0.0),
        min_window_spread_percent=strategy_float("ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT", config, minimum=0.0),
        min_paper_profit_usd=resolve_min_paper_profit_usd(
            configured_min_paper_profit_usd,
            notional_usd=notional_usd,
            target_profit_percent=target_profit_percent,
        ),
        fee_reserve_percent=strategy_float("ARBITRAGE_FEE_RESERVE_PERCENT", config, minimum=0.0),
        basket_size=strategy_int("ARBITRAGE_BASKET_SIZE", config, minimum=1, maximum=10),
        min_up_change_percent=strategy_float("TRIGGER_MIN_UP_CHANGE_PERCENT", config, minimum=0.0),
        min_down_change_percent=strategy_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", config, minimum=0.0),
    )


def read_require_binance_ws_for_execution() -> bool:
    return os.getenv("ARBITRAGE_REQUIRE_BINANCE_WS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def assert_fresh_execution_plan(
    simulation: dict,
    config: dict,
    *,
    now: datetime | None = None,
) -> None:
    if not simulation.get("signal"):
        reasons = ", ".join(simulation.get("blocked_reasons") or ["signal is false"])
        raise RuntimeError(f"execution plan is blocked: {reasons}")

    if read_require_binance_ws_for_execution() and simulation.get("price_source") != "ws":
        raise RuntimeError("execution plan is blocked: Binance WebSocket price source is required")

    observed_at = str(simulation.get("observed_at", "")).replace("Z", "+00:00")
    try:
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RuntimeError("execution plan is blocked: invalid observed_at") from exc

    age_seconds = (now or datetime.now(timezone.utc)) - observed.astimezone(timezone.utc)
    max_age = read_execution_plan_max_age_seconds(config)
    if age_seconds.total_seconds() > max_age:
        raise RuntimeError(
            f"execution plan is blocked: stale plan age={age_seconds.total_seconds():.1f}s max={max_age:.1f}s"
        )


__all__ = [
    "arbitrage_config_from_strategy",
    "assert_fresh_execution_plan",
    "configured_fee_slippage_percent",
    "read_execution_plan_max_age_seconds",
    "read_require_binance_ws_for_execution",
    "read_slippage_bps",
    "strategy_float",
    "strategy_int",
]
