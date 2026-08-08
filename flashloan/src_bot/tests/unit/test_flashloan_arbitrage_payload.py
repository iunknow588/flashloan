import pytest

from execution.cow_routes import CowToken
from execution.execution_payload import USE_FULL_BALANCE
from execution.flashloan_arbitrage_payload import (
    AtomicFlashLoanPayloadConfig,
    build_atomic_flashloan_payload_from_limit_plan,
)


def _registry():
    return {
        "USDC": CowToken("USDC", "0x0000000000000000000000000000000000000001", 6, "test"),
        "LOW": CowToken("LOW", "0x0000000000000000000000000000000000000002", 18, "test"),
        "HIGH": CowToken("HIGH", "0x0000000000000000000000000000000000000003", 18, "test"),
    }


def _plan(route=None, steps=None):
    return {
        "available": True,
        "route": route or ["USDC", "LOW", "HIGH", "USDC"],
        "initial_amount": "1000",
        "initial_symbol": "USDC",
        "final_symbol": "USDC",
        "profit_amount": "12.5",
        "profit_percent": "1.25",
        "steps": steps
        or [
            {"step": 1, "from_symbol": "USDC", "to_symbol": "LOW", "input_amount": "1000", "min_output_amount": "100"},
            {"step": 2, "from_symbol": "LOW", "to_symbol": "HIGH", "input_amount": "100", "min_output_amount": "90"},
            {"step": 3, "from_symbol": "HIGH", "to_symbol": "USDC", "input_amount": "90", "min_output_amount": "1012.5"},
        ],
    }


def test_builds_atomic_flashloan_payload_from_three_step_limit_plan():
    payload = build_atomic_flashloan_payload_from_limit_plan(
        _plan(),
        _registry(),
        AtomicFlashLoanPayloadConfig(
            router_address="0x0000000000000000000000000000000000000009",
            min_profit_usd="6.18",
            deadline_seconds=120,
        ),
    )

    aave = payload["contract"]["aaveSequentialFlashLoanExecutor"]
    assert payload["atomicity"] == "single_transaction_aave_executeOperation_all_steps_or_revert"
    assert payload["stepCount"] == 3
    assert aave["borrowAmount"] == "1000000000"
    assert aave["plan"]["profitToken"] == "0x0000000000000000000000000000000000000001"
    assert aave["plan"]["minProfitAmount"] == "6180000"
    assert aave["plan"]["steps"][0]["amountIn"] == "1000000000"
    assert aave["plan"]["steps"][1]["amountIn"] == USE_FULL_BALANCE
    assert aave["plan"]["steps"][2]["amountIn"] == USE_FULL_BALANCE
    assert [step["action"] for step in aave["plan"]["steps"]] == [
        "flashloan_buy_loser_token",
        "swap_loser_to_gainer_token",
        "sell_gainer_back_to_borrowed_asset",
    ]


def test_rejects_single_step_flashloan_payloads():
    with pytest.raises(ValueError, match="requires at least 3 swap steps"):
        build_atomic_flashloan_payload_from_limit_plan(
            _plan(
                route=["USDC", "LOW"],
                steps=[
                    {
                        "step": 1,
                        "from_symbol": "USDC",
                        "to_symbol": "LOW",
                        "input_amount": "1000",
                        "min_output_amount": "100",
                    }
                ],
            ),
            _registry(),
            AtomicFlashLoanPayloadConfig(router_address="0x0000000000000000000000000000000000000009"),
        )


def test_rejects_open_routes():
    with pytest.raises(ValueError, match="must end in the borrowed asset"):
        build_atomic_flashloan_payload_from_limit_plan(
            _plan(route=["USDC", "LOW", "HIGH", "LOW"]),
            _registry(),
            AtomicFlashLoanPayloadConfig(router_address="0x0000000000000000000000000000000000000009"),
        )
