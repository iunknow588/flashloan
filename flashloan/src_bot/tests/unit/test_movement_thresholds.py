import pytest

from strategy.limits import calculate_min_paper_profit_usd, resolve_min_paper_profit_usd
from strategy.movement_thresholds import (
    MovementThresholdConfig,
    calculate_movement_thresholds,
    effective_route_trade_fee_percent,
    enforce_min_paper_profit_usd,
)


def test_effective_route_trade_fee_compounds_per_hop_fee():
    assert effective_route_trade_fee_percent(0.10, 3) == pytest.approx(0.2997001)


def test_calculates_up_and_down_threshold_from_fees_and_target_profit():
    thresholds = calculate_movement_thresholds(
        MovementThresholdConfig(
            trade_fee_percent=0.10,
            flashloan_fee_percent=1.0,
            target_profit_percent=0.618,
            route_trade_fee_hops=3,
        ),
        flashloan_premium={"premium_percent": 0.05, "source": "aave_pool"},
    )

    expected_spread = (1 - (1 - 0.001) ** 3) * 100 + 0.05 + 0.618
    assert thresholds.min_window_spread_percent == pytest.approx(expected_spread)
    assert thresholds.min_up_change_percent == pytest.approx(expected_spread / 2)
    assert thresholds.min_down_change_percent == pytest.approx(expected_spread / 2)
    assert thresholds.flashloan_fee_percent == 0.05
    assert thresholds.source == "aave_pool"


def test_minimum_paper_profit_is_dynamic_percent_floor():
    assert enforce_min_paper_profit_usd(0) == 0.0
    assert enforce_min_paper_profit_usd(2.5) == 2.5
    assert calculate_min_paper_profit_usd(1000, 0.618) == pytest.approx(6.18)
    assert resolve_min_paper_profit_usd(0, notional_usd=1000, target_profit_percent=0.618) == pytest.approx(6.18)
    assert resolve_min_paper_profit_usd(2.5, notional_usd=1000, target_profit_percent=0.618) == 2.5
