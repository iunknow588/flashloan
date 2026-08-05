from strategy.arbitrage import (
    ArbitrageConfig,
    choose_signed_strategy,
    select_cross_pair_rows,
    simulate_basket,
    simulate_four_route_cycles,
    simulate_pair,
)


def row(symbol: str, start: float, end: float) -> dict:
    return {
        "symbol": symbol,
        "start_price": start,
        "end_price": end,
        "change_percent": (end / start - 1) * 100,
    }


def test_select_cross_pair_rows_builds_m_by_n_grid():
    top = [row("X", 10, 12), row("Z", 20, 21)]
    bottom = [row("Y", 10, 8), row("W", 5, 4)]

    pairs = select_cross_pair_rows(top, bottom)

    assert len(pairs) == 4
    assert {(a["symbol"], b["symbol"]) for a, b in pairs} == {
        ("X", "Y"),
        ("X", "W"),
        ("Z", "Y"),
        ("Z", "W"),
    }


def test_choose_signed_strategy_selects_forward_or_reverse_by_absolute_profit():
    assert choose_signed_strategy(3, 5)["strategy"] == "strategy_2_forward"
    assert choose_signed_strategy(-3, -5)["strategy"] == "strategy_2_reverse"
    assert choose_signed_strategy(3, -5)["strategy"] == "strategy_2_reverse"
    assert choose_signed_strategy(-7, 5)["strategy"] == "strategy_1_reverse"


def test_simulate_pair_applies_signed_two_strategy_rule():
    config = ArbitrageConfig(
        notional_usd=100,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=0,
    )

    pair = simulate_pair(row("X", 10, 12), row("Y", 10, 8), config, 100)

    assert pair["best_strategy"] == "strategy_3_stable_usdc_to_y_to_x_to_usdc"
    assert pair["borrow_symbol"] == "USDC"
    assert pair["route_symbols"] == ["USDC", "Y", "X", "USDC"]
    assert len(pair["candidate_strategies"]) == 2
    assert pair["m1_profit_usd"] > 0
    assert pair["m2_profit_usd"] > 0
    assert pair["profit_usd"] > 0


def test_simulate_four_route_cycles_reports_remaining_amount_from_100_tokens():
    config = ArbitrageConfig(
        notional_usd=100,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=0,
    )

    routes = simulate_four_route_cycles(row("X", 10, 12), row("Y", 10, 8), config, 100)

    assert len(routes) == 4
    assert {route["initial_amount"] for route in routes} == {100}
    assert all(route["route_symbols"][0] == route["route_symbols"][-1] for route in routes)
    assert all("remaining_amount" in route for route in routes)
    assert all("profit_percent" in route for route in routes)


def test_simulate_basket_reports_grid_counts_and_closed_execution_route():
    extremes = {
        "observed_at": "2026-07-27T00:00:00Z",
        "window_seconds": 10,
        "sample_count": 4,
        "top": [row("X", 10, 12), row("Z", 20, 22)],
        "bottom": [row("Y", 10, 8), row("W", 5, 4.5)],
    }
    config = ArbitrageConfig(
        notional_usd=100,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=0,
        basket_size=1,
    )

    result = simulate_basket(extremes, config)

    assert result["candidate_pair_count"] == 4
    assert result["evaluated_strategy_count"] == 8
    assert result["signal"] is True
    assert result["borrow_symbol"] == "USDC"
    assert result["route_symbols"][0] == "USDC"
    assert result["route_symbols"][-1] == "USDC"
    plan = result["execution_plan"]
    assert plan["strategy_model"] == "m_by_n_grid_usdc_flashloan_stable_borrow_cycle"
    assert plan["route_invariant"] == "first route symbol must equal final route symbol; repay step must output the borrowed asset"
    assert plan["borrow_symbols"] == ["USDC"]
    assert plan["repay_symbols"] == ["USDC"]
    assert plan["buy_steps"][0]["from_symbol"] == plan["repay_steps"][0]["to_symbol"]


def test_simulate_basket_uses_pair_spread_instead_of_side_thresholds():
    extremes = {
        "observed_at": "2026-07-27T00:00:00Z",
        "window_seconds": 10,
        "sample_count": 2,
        "top": [row("X", 10, 10.06)],
        "bottom": [row("Y", 10, 9.95)],
    }
    config = ArbitrageConfig(
        notional_usd=1000,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=1.0,
        min_up_change_percent=1.0,
        min_down_change_percent=1.0,
        basket_size=1,
    )

    result = simulate_basket(extremes, config)

    assert result["candidate_pair_count"] == 1
    assert result["window_spread_percent"] > result["min_window_spread_percent"]
    assert result["signal"] is True


def test_simulate_basket_requires_pair_spread_strictly_above_threshold():
    extremes = {
        "observed_at": "2026-07-27T00:00:00Z",
        "window_seconds": 10,
        "sample_count": 2,
        "top": [{"symbol": "X", "start_price": 10, "end_price": 10.05, "change_percent": 0.5}],
        "bottom": [{"symbol": "Y", "start_price": 10, "end_price": 9.95, "change_percent": -0.5}],
    }
    config = ArbitrageConfig(
        notional_usd=1000,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=1.0,
        basket_size=1,
    )

    assert simulate_basket(extremes, config) is None


def test_simulate_basket_generates_quote_candidate_when_negative_m_selects_reverse():
    extremes = {
        "observed_at": "2026-07-27T00:00:00Z",
        "window_seconds": 10,
        "sample_count": 2,
        "top": [row("X", 10, 10.01)],
        "bottom": [row("Y", 10, 9.99)],
    }
    config = ArbitrageConfig(
        notional_usd=100,
        trade_fee_percent=0.3,
        flashloan_fee_percent=0.05,
        min_window_spread_percent=0,
        min_paper_profit_usd=0,
        basket_size=1,
    )

    result = simulate_basket(extremes, config)

    assert result["borrow_symbol"] == "USDC"
    assert result["selected_signed_profit_usd"] < 0
    assert result["selected_direction_score_usd"] < 0
    assert result["selected_expected_profit_usd"] < 0
    assert result["paper_route_profit_usd"] < 0
    assert result["candidate_score_usd"] < 0
    assert result["signal"] is False
    assert result["execution_plan"] is None


def test_simulate_basket_blocks_candidates_below_one_usd_expected_profit():
    extremes = {
        "observed_at": "2026-07-27T00:00:00Z",
        "window_seconds": 10,
        "sample_count": 2,
        "top": [row("X", 10, 10.002)],
        "bottom": [row("Y", 10, 9.998)],
    }
    config = ArbitrageConfig(
        notional_usd=1000,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=0,
        min_paper_profit_usd=1,
        basket_size=1,
    )

    result = simulate_basket(extremes, config)

    assert result["net_signal_profit_usd"] < 1
    assert result["signal"] is False
    assert "candidate_score_below_threshold" in result["blocked_reasons"]
    assert result["execution_plan"] is None
