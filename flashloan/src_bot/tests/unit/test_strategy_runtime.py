from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market.binance_market.control_panel_config import STRATEGY_DEFAULTS
from market.binance_market.strategy_runtime import (
    arbitrage_config_from_strategy,
    assert_fresh_execution_plan,
    configured_fee_slippage_percent,
    read_execution_plan_max_age_seconds,
    read_slippage_bps,
)


def test_fee_slippage_falls_back_to_alert_diff_for_invalid_override(monkeypatch):
    monkeypatch.setenv("ALERT_DIFF_PERCENT", "bad")
    monkeypatch.setenv("FEE_SLIPPAGE_PERCENT", "also-bad")

    assert configured_fee_slippage_percent() == 0.30


def test_readers_use_strategy_defaults_for_invalid_config_values():
    config = dict(STRATEGY_DEFAULTS)
    config["EXECUTION_SLIPPAGE_BPS"] = "bad"
    config["EXECUTION_PLAN_MAX_AGE_SECONDS"] = "bad"
    config["ARBITRAGE_NOTIONAL_USD"] = "bad"
    config["ARBITRAGE_TRADE_FEE_PERCENT"] = "bad"
    config["ARBITRAGE_FLASHLOAN_FEE_PERCENT"] = "bad"
    config["ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT"] = "bad"
    config["ARBITRAGE_MIN_PAPER_PROFIT_USD"] = "bad"
    config["ARBITRAGE_FEE_RESERVE_PERCENT"] = "bad"
    config["ARBITRAGE_BASKET_SIZE"] = "bad"

    assert read_slippage_bps(config) == 50
    assert read_execution_plan_max_age_seconds(config) == 15.0

    arbitrage_config = arbitrage_config_from_strategy(config)
    assert arbitrage_config.notional_usd == 1000.0
    assert arbitrage_config.min_paper_profit_usd == pytest.approx(6.0)
    assert arbitrage_config.basket_size == 2


def test_assert_fresh_execution_plan_rejects_false_signal(monkeypatch):
    config = dict(STRATEGY_DEFAULTS)
    monkeypatch.setenv("ARBITRAGE_REQUIRE_BINANCE_WS", "false")

    with pytest.raises(RuntimeError, match="blocked"):
        assert_fresh_execution_plan(
            {"signal": False, "blocked_reasons": ["no signal"]},
            config,
            now=datetime.now(timezone.utc),
        )


def test_assert_fresh_execution_plan_rejects_stale_plan(monkeypatch):
    config = dict(STRATEGY_DEFAULTS)
    config["EXECUTION_PLAN_MAX_AGE_SECONDS"] = 5
    monkeypatch.setenv("ARBITRAGE_REQUIRE_BINANCE_WS", "false")
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(seconds=30)).isoformat()

    with pytest.raises(RuntimeError, match="stale plan"):
        assert_fresh_execution_plan(
            {"signal": True, "price_source": "ws", "observed_at": stale},
            config,
            now=now,
        )
