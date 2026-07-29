from execution.profit_guard import ProfitGuardConfig, evaluate_profit_guard


def test_profit_guard_subtracts_slippage_and_safety_margin_but_not_gas():
    result = evaluate_profit_guard(
        {"profit_usdc_units": "2000000"},
        ProfitGuardConfig(
            notional_usd=100.0,
            slippage_bps=50,
            gas_cost_usdc=0.25,
            min_net_profit_usdc=0.5,
            safety_margin_usdc=0.1,
        ),
    )

    assert result["quoted_profit_usdc"] == 2.0
    assert result["slippage_reserve_usdc"] == 0.5
    assert result["gas_cost_usdc"] == 0.25
    assert result["safety_margin_usdc"] == 0.1
    assert result["net_profit_usdc"] == 1.4
    assert result["net_profit_verified"] is True


def test_profit_guard_allows_zero_gas_cost_because_gas_is_advisory():
    result = evaluate_profit_guard(
        {"profit_usdc_units": "2000000"},
        ProfitGuardConfig(
            notional_usd=100.0,
            slippage_bps=50,
            gas_cost_usdc=0,
            min_net_profit_usdc=0.5,
        ),
    )

    assert result["net_profit_verified"] is True
    assert result["blocked_reasons"] == []
