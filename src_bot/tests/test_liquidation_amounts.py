import pytest

from execution.liquidation_amounts import build_liquidation_amounts, token_amount
from execution.liquidation_payload import (
    LiquidationExecutionPayloadConfig,
    build_liquidation_execution_payload,
    validate_min_profit_consistency,
)


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


def test_build_liquidation_amounts_reuses_unified_profit_inputs():
    candidate = {
        "collateral_asset": "0x0000000000000000000000000000000000000002",
        "collateral_decimals": 18,
        "debt_asset": "0x0000000000000000000000000000000000000003",
        "debt_decimals": 6,
        "debt_price": 1,
        "estimated_profit": {
            "repay_base": 100.0,
            "bonus_rate": 0.05,
            "flashloan_rate": 0.0005,
            "slippage_rate": 0.001,
            "gas_cost_usd": 0.5,
            "mev_buffer_usd": 0.25,
            "retry_buffer_usd": 0.1,
        },
    }

    amounts = build_liquidation_amounts(
        candidate,
        debt_to_cover_units=100_000_000,
        min_collateral_swap_out_units=103_000_000,
        min_profit_units=1_000_000,
    )

    assert amounts["profit"]["gross_profit_base"] == 5.0
    assert amounts["profit"]["fee_base"] == 0.15
    assert amounts["profit"]["contract_surplus_base"] == 4.85
    assert amounts["profit"]["operator_net_profit_estimate_usd"] == pytest.approx(4.0)


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
    assert payload["request"]["gasLimit"] == "0"
    assert payload["amounts"]["debt"]["debt_to_cover_amount"] == 1.0
    assert payload["amounts"]["profit"]["legacy_net_profit_base"] == 123.0


def test_liquidation_payload_can_set_contract_gas_limit_and_validate_min_profit():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "execution_plan": {"execution_ready": True},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "amount_to_pass_to_liquidation_call": 1_000_000,
            "min_collateral_swap_out": 1_050_000,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    payload = build_liquidation_execution_payload(
        report,
        executor_address="0x0000000000000000000000000000000000000004",
        router_address="0x0000000000000000000000000000000000000005",
        deadline=123456,
        config=LiquidationExecutionPayloadConfig(
            min_profit_buffer_base=20,
            rounding_buffer_units=3,
            gas_limit=700_000,
        ),
    )

    assert payload["request"]["minProfitAmount"] == "100"
    assert payload["request"]["gasLimit"] == "700000"
    assert payload["preflight"]["min_profit_consistency"] == {
        "consistent": True,
        "actual_min_profit_amount": 100,
        "expected_min_profit_amount": 100,
        "min_profit_buffer_base": 20,
        "rounding_buffer_units": 3,
    }
    assert validate_min_profit_consistency(
        {"minProfitAmount": "99"},
        {"net_profit_base": 123},
        min_profit_buffer_base=20,
        rounding_buffer_units=3,
    )["consistent"] is False
