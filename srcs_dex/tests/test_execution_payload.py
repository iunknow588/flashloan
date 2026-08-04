from execution.execution_payload import PayloadConfig, build_execution_payload
from execution.dex_costs import USDC


def test_build_payload_marks_plan_as_aave_compatible_when_it_returns_to_borrowed_asset():
    execution_plan = {"version": 1, "mode": "paper_sequential"}
    quote = {
        "dex_name": "Trader Joe V2",
        "router_address": "0x60aE616a2155Ee3d9A68541Ba4544862310933d4",
        "slippage_bps": 50,
        "errors": [],
        "viable": True,
        "quoted_profit_usdc": 1.0,
        "total_sell_usdc": 10.0,
        "total_repay_usdc": 9.0,
        "buy_steps": [
            {
                "rank": 1,
                "action": "buy_top_asset",
                "from_symbol": "AVAXUSDT",
                "to_symbol": "AAVEUSDT",
                "input_amount": 1.0,
                "min_output_amount": 0.5,
                "path": [
                    "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
                    "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
                    "0x63a72806098Bd3D9520cC43356dD78afe5D386D9",
                ],
                "path_symbols": ["AVAXUSDT", "USDC", "AAVEUSDT"],
            }
        ],
        "sell_steps": [
            {
                "rank": 1,
                "action": "sell_top_asset",
                "from_symbol": "AAVEUSDT",
                "to_symbol": "USDC",
                "input_amount": 0.5,
                "min_output_amount": 10.0,
                "path": [
                    "0x63a72806098Bd3D9520cC43356dD78afe5D386D9",
                    USDC,
                ],
                "path_symbols": ["AAVEUSDT", "USDC"],
            }
        ],
        "repay_steps": [
            {
                "rank": 1,
                "action": "buy_repay_asset",
                "from_symbol": "USDC",
                "to_symbol": "AVAXUSDT",
                "max_input_amount": 9.0,
                "output_amount": 1.0,
                "path": [
                    USDC,
                    "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
                ],
                "path_symbols": ["USDC", "AVAXUSDT"],
            }
        ],
    }

    payload = build_execution_payload(execution_plan, quote, PayloadConfig(min_profit_usdc=0.25))

    assert len(payload["contract"]["mockFundedExecutor"]["steps"]) == 3
    assert payload["contract"]["mockFundedExecutor"]["minProfit"] == "250000"
    assert payload["contract"]["aaveSequentialFlashLoanExecutor"]["compatible"] is True
    assert payload["contract"]["aaveSequentialFlashLoanExecutor"]["borrowAsset"] == "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"


def test_build_payload_rejects_non_viable_quote():
    try:
        build_execution_payload({}, {"viable": False, "errors": []})
    except ValueError as exc:
        assert "execution_plan is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")
