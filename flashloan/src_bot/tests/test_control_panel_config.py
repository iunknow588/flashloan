import pytest

from strategy.limits import strategy_defaults
from web.control_panel_config import STRATEGY_DEFAULTS, sanitize_strategy_config, unified_sampling_profile


def test_strategy_defaults_come_from_single_strategy_library():
    assert STRATEGY_DEFAULTS == strategy_defaults()
    assert STRATEGY_DEFAULTS["TRIGGER_MIN_UP_CHANGE_PERCENT"] == 0.05
    assert STRATEGY_DEFAULTS["TRIGGER_MIN_DOWN_CHANGE_PERCENT"] == 0.05
    assert STRATEGY_DEFAULTS["ARBITRAGE_MIN_PAPER_PROFIT_USD"] == 0.0


def test_sampling_profile_allows_configured_200ms_window():
    config = sanitize_strategy_config({"BINANCE_CHANGE_WINDOW_SECONDS": 0.2})
    profile = unified_sampling_profile(config)

    assert config["BINANCE_CHANGE_WINDOW_SECONDS"] == 0.2
    assert profile["seconds"] == 0.2


def test_market_fee_slippage_uses_default_for_invalid_env(monkeypatch):
    from web import control_panel_market

    monkeypatch.setenv("ALERT_DIFF_PERCENT", "bad")
    monkeypatch.setenv("FEE_SLIPPAGE_PERCENT", "also-bad")

    assert control_panel_market.configured_fee_slippage_percent() == 0.30


def test_market_strategy_numeric_readers_fall_back_for_invalid_config(monkeypatch):
    from web import control_panel_market

    monkeypatch.setattr(
        control_panel_market,
        "strategy_config",
        lambda: {
            "EXECUTION_SLIPPAGE_BPS": "bad",
            "EXECUTION_PLAN_MAX_AGE_SECONDS": "bad",
            "ARBITRAGE_NOTIONAL_USD": "bad",
            "ARBITRAGE_TRADE_FEE_PERCENT": "bad",
            "ARBITRAGE_FLASHLOAN_FEE_PERCENT": "bad",
            "ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT": "bad",
            "ARBITRAGE_MIN_PAPER_PROFIT_USD": "bad",
            "ARBITRAGE_FEE_RESERVE_PERCENT": "bad",
            "ARBITRAGE_BASKET_SIZE": "bad",
        },
    )

    assert control_panel_market.read_slippage_bps() == 50
    assert control_panel_market.read_execution_plan_max_age_seconds() == 15.0
    config = control_panel_market.arbitrage_config_from_strategy()
    assert config.notional_usd == 1000.0
    assert config.min_paper_profit_usd == pytest.approx(6.18)
    assert config.basket_size == 2
