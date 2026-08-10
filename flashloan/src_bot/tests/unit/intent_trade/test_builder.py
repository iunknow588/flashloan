import inspect
from types import SimpleNamespace

from intent_trade import (
    bind_cow_intent_context,
    build_cow_intent_trade,
    build_triangular_onchain_intent_trade,
    submit_cow_intent_trade,
)
from intent_trade import direct_utils


def test_build_cow_intent_trade_exposes_a_small_public_surface():
    params = list(inspect.signature(build_cow_intent_trade).parameters)

    assert params == ["link_name", "expected_profit", "rising_tokens", "falling_tokens"]
    assert list(inspect.signature(build_triangular_onchain_intent_trade).parameters) == params
    assert list(inspect.signature(bind_cow_intent_context).parameters) == [
        "intent",
        "requested_amount",
        "input_symbol",
        "final_symbol",
        "owner",
        "cow_network",
        "cow_chain_id",
    ]
    assert list(inspect.signature(submit_cow_intent_trade).parameters) == [
        "quote_payload",
        "opportunity",
        "timeout_seconds",
    ]


def _zero_cost_env(monkeypatch):
    for name in (
        "COW_FLASHLOAN_PURE_INTENT_TRADE_FEE_PERCENT",
        "COW_FLASHLOAN_PURE_INTENT_FLASHLOAN_FEE_PERCENT",
        "COW_FLASHLOAN_PURE_INTENT_FEE_RESERVE_PERCENT",
        "COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC",
        "COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC",
        "ARBITRAGE_TRADE_FEE_PERCENT",
        "ARBITRAGE_FLASHLOAN_FEE_PERCENT",
        "ARBITRAGE_FEE_RESERVE_PERCENT",
    ):
        monkeypatch.setenv(name, "0")


def test_build_cow_intent_trade_computes_fee_budget_and_route_scope(monkeypatch):
    _zero_cost_env(monkeypatch)

    intent = build_cow_intent_trade(
        "USDC->BBB->AAA->USDC",
        "6.18",
        [
            {"symbol": "AAA", "base_symbol": "AAA"},
            {"symbol": "AAAUSDT", "base_symbol": "AAA"},
        ],
        [
            {"symbol": "BBB", "base_symbol": "BBB"},
            {"symbol": "BBBUSDT", "base_symbol": "BBB"},
        ],
    )

    assert intent["initial_amount"] == "1000"
    assert intent["expected_profit_amount"] == "6.18"
    assert intent["min_pure_profit_amount"] == "6.18"
    assert intent["min_final_amount"] == "1006.18"
    assert intent["x_amount"] == "6.18"
    assert intent["target_token_amount"] == "1006.18"
    assert intent["route_path"] == ["USDC", "BBB", "AAA", "USDC"]
    assert intent["route_direction"] == "buy_loser_then_gainer"
    assert intent["token_scope"]["tokens"] == ["AAA", "AAAUSDT", "BBB", "BBBUSDT", "USDC"]
    assert intent["token_scope"]["scope_role"] == "solver_owned_token_universe_only"
    assert intent["fee_components"]["total_cost_usdc"] == "0"
    assert intent["control_surface"]["current_mode"] == "intent"
    assert intent["cow_sdk_order_intent"]["minimum_final_buy_amount_after_all_costs"] == "1006.18"
    assert intent["cow_app_data_schema"]["custom_metadata_allowed"] is False
    assert intent["cow_app_data_schema"]["base_metadata"]["orderClass"]["orderClass"] == "limit"
    assert intent["cow_app_data_schema"]["flashloan_metadata_keys"] == ["flashloan", "hooks"]
    assert intent["cow_app_data_schema"]["exchange_token_scope_appdata_supported"] is False


def test_build_cow_intent_trade_adds_configured_costs_to_x(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_TRADE_FEE_PERCENT", "0.10")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_FLASHLOAN_FEE_PERCENT", "0.05")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_FEE_RESERVE_PERCENT", "0")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC", "0.25")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC", "0.10")

    intent = build_cow_intent_trade("buy_loser_then_gainer", "6.18", ["AAA"], ["BBB"])

    assert intent["fee_components"]["route_trade_fee_usdc"] == "2"
    assert intent["fee_components"]["flashloan_fee_usdc"] == "0.5"
    assert intent["fee_components"]["total_cost_usdc"] == "2.85"
    assert intent["x_amount"] == "9.03"
    assert intent["min_final_amount"] == "1009.03"
    assert intent["target_token_amount"] == "1009.03"
    assert intent["cow_sdk_order_intent"]["minimum_final_buy_amount_after_all_costs"] == "1009.03"


def test_bind_cow_intent_context_recomputes_cost_inclusive_final_target(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_TRADE_FEE_PERCENT", "0.10")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_FLASHLOAN_FEE_PERCENT", "0.05")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_FEE_RESERVE_PERCENT", "0.10")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC", "0")
    monkeypatch.setenv("COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC", "0")

    intent = build_cow_intent_trade("USDC->BBB->AAA->USDC", "15.45", ["AAA"], ["BBB"])
    bound = bind_cow_intent_context(
        intent,
        requested_amount="2500",
        input_symbol="USDC",
        final_symbol="USDC",
        owner="0x" + "1" * 40,
        cow_network="bnb",
        cow_chain_id=56,
    )

    assert bound["borrow_token_amount"] == "2500"
    assert bound["fee_components"]["route_trade_fee_usdc"] == "5"
    assert bound["fee_components"]["flashloan_fee_usdc"] == "1.25"
    assert bound["fee_components"]["fee_reserve_usdc"] == "2.5"
    assert bound["fee_components"]["total_cost_usdc"] == "8.75"
    assert bound["min_pure_profit_amount"] == "15.45"
    assert bound["x_amount"] == "24.2"
    assert bound["min_final_amount"] == "2524.2"
    assert bound["target_token_amount"] == "2524.2"
    assert bound["cow_sdk_order_intent"]["minimum_final_buy_amount_after_all_costs"] == "2524.2"


def test_build_triangular_onchain_intent_trade_exposes_direct_protocol(monkeypatch):
    monkeypatch.setenv("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "0x" + "1" * 40)
    monkeypatch.setenv("AAVE_TRIANGULAR_EXECUTOR_ADDRESS", "0x" + "2" * 40)
    monkeypatch.setenv("TRIANGULAR_DEX_ROUTER", "0x" + "4" * 40)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "0x" + "5" * 40)

    intent = build_triangular_onchain_intent_trade("USDC->BBB->AAA->USDC", "6.18", ["AAA"], ["BBB"])

    protocol = intent["direct_onchain_protocol"]
    assert protocol["kind"] == "triangular_route_controller_v1"
    assert protocol["controller_address"] == "0x" + "1" * 40
    assert protocol["executor_address"] == "0x" + "2" * 40
    assert protocol["candidate_symbols"] == ["BBB", "AAA"]
    assert intent["intent_protocol"] == "direct_onchain"
    assert intent["submission_protocol"] == "direct_onchain"


def test_direct_bool_value_parses_string_false():
    assert direct_utils._bool_value("false", True) is False
    assert direct_utils._bool_value("0", True) is False
    assert direct_utils._bool_value("true", False) is True
    assert direct_utils._bool_value(None, True) is True


def test_direct_raw_signed_transaction_supports_web3_v6_field():
    assert direct_utils._raw_signed_transaction(SimpleNamespace(rawTransaction=b"raw")) == b"raw"


def test_direct_token_registry_falls_back_to_local_aave_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "aave_reserve_assets.json"
    cache_path.write_text(
        '{"assets":[{"symbol":"USDC","address":"0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E","decimals":6}]}',
        encoding="utf-8",
    )

    calls = []

    def fake_build_token_registry(**kwargs):
        calls.append(kwargs)
        if kwargs.get("include_cow_token_list", True):
            raise OSError("remote token list unavailable")
        return {"USDC": "local-usdc"}

    monkeypatch.setattr(direct_utils, "build_token_registry", fake_build_token_registry)

    registry = direct_utils._build_direct_token_registry(aave_cache_path=cache_path, network="avalanche")

    assert registry == {"USDC": "local-usdc"}
    assert calls[-1]["include_cow_token_list"] is False


def test_direct_route_decision_report_names_precise_failure():
    report = direct_utils._route_decision_report(
        [
            False,
            False,
            0,
            0,
            ["USDC", "JOE", "AI", "USDC"],
            0,
            1055,
            1000,
            0,
            3,
            1000500001,
            0,
            0,
        ]
    )

    assert report["failureCode"] == "3"
    assert report["failureReason"] == "middle_hop_quote_failed"
    assert report["path"] == ["USDC", "JOE", "AI", "USDC"]
    assert report["requiredFinalUsdc"] == "1000500001"


def test_direct_execution_failure_report_decodes_executor_revert_data():
    values = [1, 1_000_500_000, 1_000_500_000, 0, 0, 0]
    raw = "0x" + direct_utils.EXECUTOR_ERROR_SELECTOR + "".join(f"{value:064x}" for value in values)

    report = direct_utils._execution_failure_report(Exception({"data": raw}))

    assert report["source"] == "aave_triangular_executor"
    assert report["failureCode"] == "1"
    assert report["failureReason"] == "post_swap_balance_below_actual_repayment"
    assert report["amountOutMinUsdc"] == "1000500000"


def test_direct_execution_failure_report_decodes_router_selector():
    raw = "0x" + direct_utils.ROUTER_SWAP_ERROR_SELECTOR + "deadbeef" + ("0" * 56)

    report = direct_utils._execution_failure_report(Exception({"data": raw}))

    assert report["source"] == "aave_triangular_executor"
    assert report["failureCode"] == "router_swap_failed"
    assert report["failureReason"] == "router_swap_reverted_selector_0xdeadbeef"


def test_direct_execution_failure_report_decodes_invalid_router_result():
    raw = "0x" + direct_utils.ROUTER_SWAP_RESULT_INVALID_SELECTOR + f"{3:064x}"

    report = direct_utils._execution_failure_report(Exception({"data": raw}))

    assert report["source"] == "aave_triangular_executor"
    assert report["failureCode"] == "router_swap_result_invalid"
    assert report["failureReason"] == "router_swap_result_length_invalid"
    assert report["resultLength"] == "3"
