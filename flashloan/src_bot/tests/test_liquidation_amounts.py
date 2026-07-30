from execution.liquidation_amounts import build_liquidation_amounts, token_amount
from execution.liquidation_payload import build_liquidation_execution_payload


def test_token_amount_handles_units_and_decimals():
    assert token_amount(1_500_000, 6) == 1.5
    assert token_amount(2 * 10**18, 18) == 2.0
    assert token_amount(0, 18) == 0.0


def test_build_liquidation_amounts_labels_units_amounts_and_profit():
    candidate = {
        "collateral_asset": "0x0000000000000000000000000000000000000002",
        "collateral_symbol": "WAVAX",
        "collateral_decimals": 18,
        "collateral_price": 25,
        "max_collateral_to_liquidate": 2 * 10**18,
        "debt_asset": "0x0000000000000000000000000000000000000003",
        "debt_symbol": "USDC",
        "debt_decimals": 6,
        "debt_price": 1,
        "estimated_profit": {"net_profit_base": 12.5, "gas_cost_usd": 1.25},
    }

    amounts = build_liquidation_amounts(
        candidate,
        debt_to_cover_units=1_000_000,
        min_collateral_swap_out_units=1_050_000,
        min_profit_units=100_000,
    )

    assert amounts["debt"]["debt_to_cover_units"] == "1000000"
    assert amounts["debt"]["debt_to_cover_amount"] == 1.0
    assert amounts["collateral"]["max_collateral_to_liquidate_amount"] == 2.0
    assert amounts["collateral"]["max_collateral_to_liquidate_usd"] == 50.0
    assert amounts["profit"]["min_profit_amount"] == 0.1
    assert amounts["profit"]["operator_net_profit_estimate_usd"] == 11.25


def test_liquidation_payload_includes_structured_amounts():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "execution_plan": {"execution_ready": True},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "collateral_symbol": "WAVAX",
            "collateral_decimals": 18,
            "collateral_price": 25,
            "max_collateral_to_liquidate": 2 * 10**18,
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "debt_symbol": "USDC",
            "debt_decimals": 6,
            "debt_price": 1,
            "amount_to_pass_to_liquidation_call": 1_000_000,
            "min_collateral_swap_out": 1_050_000,
            "estimated_profit": {"net_profit_base": 123, "gas_cost_usd": 2},
        },
    }

    payload = build_liquidation_execution_payload(
        report,
        executor_address="0x0000000000000000000000000000000000000004",
        router_address="0x0000000000000000000000000000000000000005",
        deadline=123456,
    )

    assert payload["request"]["debtToCover"] == "1000000"
    assert payload["amounts"]["debt"]["debt_to_cover_amount"] == 1.0
    assert payload["amounts"]["profit"]["legacy_net_profit_base"] == 123.0
