import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from dex.cow_flashloan.routes import CowToken
from market.strategy.arbitrage import ArbitrageConfig
from market.binance_market.service import (
    build_binance_market_state,
    build_cow_network_market_claims,
    build_cow_supported_market_overview,
    build_binance_rest_market_snapshot,
    build_cow_route_precheck,
    build_cow_quote_verification,
    _cow_pure_profit_intent,
    _cow_order_submission_enabled,
    load_cow_supported_token_registry,
    cost_adjusted_cow_thresholds,
    read_binance_market_snapshot,
    refresh_cow_supported_token_cache,
    select_binance_market_extremes,
    needs_binance_rest_snapshot,
    _cow_execution_precheck,
    _query_window_timing,
)
from core.db.storage_cow_execution import (
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    _category_within_retention,
    append_cow_execution_attempts_jsonl,
    build_cow_execution_attempts,
    build_cow_market_claim_candidate_attempts,
    build_cow_market_claim_pairs,
    build_cow_market_candidate_attempts,
    load_recent_cow_execution_attempts_jsonl,
)


def _row(symbol: str, start: float, end: float) -> dict:
    return {
        "symbol": symbol,
        "start_price": start,
        "end_price": end,
        "current_price": end,
        "change_percent": (end - start) / start * 100,
        "price_source": "ws",
        "window_ready": True,
    }


def _sdk_ready_status() -> dict:
    return {
        "ready": True,
        "reason": "cow_flashloan_sdk_ready",
        "missing_packages": [],
        "install_command": "cd flashLoan\\flashloan\\app_runtime\\cow_flashloan\\node_adapter && npm install",
    }


def _sdk_missing_status() -> dict:
    return {
        "ready": False,
        "reason": "cow_flashloan_sdk_install_required",
        "missing_packages": ["@cowprotocol/sdk-flash-loans"],
        "install_command": "cd flashLoan\\flashloan\\app_runtime\\cow_flashloan\\node_adapter && npm install",
    }


def _fake_pool_filter(symbols: list[str], pairs: list[tuple[str, str]]) -> dict:
    pair_map = {
        tuple(sorted((left.upper(), right.upper()))): {
            "pair": f"{left.upper()}:{right.upper()}",
            "effectivePoolCount": 2,
        }
        for left, right in pairs
    }
    return {
        "loaded": True,
        "source": "test",
        "pair_count": len(pair_map),
        "symbols": [symbol.upper() for symbol in symbols],
        "pairs": pair_map,
        "cache_block_number": "1",
        "fetched_at": "test",
        "error": None,
    }


def test_query_window_timing_identifies_previous_window_and_lagging_quote():
    timing = _query_window_timing(
        [
            {"source": "previous_window", "price": Decimal("10")},
            {"source": "current_window", "price": Decimal("12")},
            {"source": "query_quote", "price": Decimal("10.5")},
        ],
        "price",
        query_value=Decimal("10.5"),
    )

    assert timing["closer_to"] == "previous_window"
    assert timing["timing_vs_current_window"] == "lagging"
    assert timing["previous_to_current_percent"] == "20"


def test_binance_market_state_defaults_to_top50_bottom50_and_keeps_pair_hints_at_5():
    top = [_row(f"T{i}USDT", 100.0, 101.0 + i / 10) for i in range(50)]
    bottom = [_row(f"B{i}USDT", 100.0, 99.0 - i / 10) for i in range(50)]
    extremes = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "window_seconds": 1.0,
        "sample_count": 100,
        "observation_universe_size": 100,
        "price_source": "ws",
        "top": top,
        "bottom": bottom,
        "basket": [*top, *bottom, _row("AAVEUSDT", 100.0, 101.0)],
    }
    config = ArbitrageConfig(
        notional_usd=1000.0,
        trade_fee_percent=0.1,
        flashloan_fee_percent=0.05,
    )

    payload = build_binance_market_state(
        extremes,
        aave_symbols=["AAVEUSDT", "AVAXUSDT"],
        arbitrage_config=config,
    )

    assert len(payload["top"]) == 50
    assert len(payload["bottom"]) == 50
    assert payload["pair_count"] == 25
    assert payload["pairs"][0]["quote_verified"] is False
    assert payload["pairs"][0]["route_count"] == 2
    assert payload["pairs"][0]["full_route_count"] == 2
    assert len(payload["pairs"][0]["route_results"]) == 2
    assert len(payload["pairs"][0]["route_results_full"]) == 2
    plan = payload["pairs"][0]["route_results"][0]["binance_execution_plan"]
    assert plan["available"] is True
    assert plan["initial_amount"] == "1000"
    assert plan["slippage_bps"] == 50
    assert [step["price_basis"] for step in plan["steps"]] == [
        "current_binance_buy_low",
        "pre_change_binance_cross",
        "current_binance_sell_high",
    ]
    assert payload["pairs"][0]["quote_required"] is True
    assert payload["pairs"][0]["estimation_available"] is False
    assert payload["pairs"][0]["profit_usd"] is None
    assert payload["pairs"][0]["best_route"] is None
    assert payload["pairs"][0]["blocked_reasons"] == ["requires_cow_or_dex_quote"]
    assert payload["aave_rows"][0]["symbol"] == "AAVEUSDT"
    assert payload["aave_rows"][0]["tracked"] is True
    assert payload["aave_rows"][1]["tracked"] is False
    assert payload["best"] is None
    assert payload["candidate_basis"] == "binance_token_names_only"


def test_binance_market_state_lists_quote_candidates_without_paper_profit():
    extremes = {
        "top": [_row("XUSDT", 10.0, 12.0)],
        "bottom": [_row("YUSDT", 10.0, 8.0)],
        "sample_count": 2,
    }
    config = ArbitrageConfig(
        notional_usd=1000.0,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=0,
    )

    payload = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=config,
        pair_side_limit=1,
    )

    pair = payload["pairs"][0]
    assert [route["route_no"] for route in pair["route_results"]] == [1, 2]
    assert pair["best_route_no"] is None
    assert pair["best_strategy"] is None
    assert pair["profit_usd"] is None
    assert pair["profit_percent"] is None
    assert all(route["quote_required"] for route in pair["route_results"])
    assert all(route["net_after_flashloan_percent"] is None for route in pair["route_results"])
    assert pair["candidate_basis"] == "binance_token_names_only"
    assert pair["route_results"][0]["route"] == ["USDC", "Y", "X", "USDC"]
    assert pair["route_results"][0]["priority_reason"] == "buy_loser_then_gainer"
    assert pair["route_results"][1]["route"] == ["USDC", "X", "Y", "USDC"]
    assert pair["route_results"][1]["priority_reason"] == "reverse_check"
    assert {route["initial_symbol"] for route in pair["route_results"]} == {"USDC"}
    assert pair["edge_hint_percent"] == 20.0


def test_binance_market_state_filters_cow_supported_tokens_before_ranking():
    extremes = {
        "top": [
            _row("UNSUPPORTEDTOPUSDT", 100.0, 120.0),
            _row("AAAUSDT", 100.0, 103.0),
            _row("CCCUSDT", 100.0, 100.1),
            _row("PEPEUSDT", 0.00000292, 0.00000310),
        ],
        "bottom": [
            _row("UNSUPPORTEDBOTTOMUSDT", 100.0, 80.0),
            _row("BBBUSDT", 100.0, 99.2),
            _row("DDDUSDT", 100.0, 99.8),
            _row("SHIBUSDT", 0.000011, 0.000010),
        ],
        "sample_count": 6,
    }
    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB", "CCC", "DDD", "PEPE", "SHIB"], start=1)
    }
    config = ArbitrageConfig(
        notional_usd=1000.0,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        min_window_spread_percent=0,
    )

    payload = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=config,
        top_limit=5,
        bottom_limit=5,
        pair_side_limit=5,
        cow_network="avalanche",
        min_spread_percent=1.0,
        registry=registry,
    )

    assert [row["base_symbol"] for row in payload["top"]] == []
    assert [row["base_symbol"] for row in payload["bottom"]] == []
    assert [row["base_symbol"] for row in payload["raw_top"]][:2] == ["UNSUPPORTEDTOP", "AAA"]
    assert [row["base_symbol"] for row in payload["raw_bottom"]][:2] == ["UNSUPPORTEDBOTTOM", "BBB"]
    assert payload["cow_filter"]["enabled"] is True
    assert payload["cow_filter"]["pool_filter"]["enabled"] is True
    assert payload["cow_filter"]["market_excluded_symbol_count"] == 4
    assert payload["cow_filter"]["min_side_change_percent"] == 0.3
    assert payload["cow_filter"]["min_token_price_usd"] == 0.01
    assert payload["pair_count"] == 0


def test_binance_market_state_honors_requested_raw_side_limit_up_to_50():
    rows = [_row(f"T{i}USDT", 100.0, 101.0 + i / 10) for i in range(20)]
    losers = [_row(f"B{i}USDT", 100.0, 99.0 - i / 10) for i in range(20)]
    config = ArbitrageConfig(
        notional_usd=1000.0,
        trade_fee_percent=0.1,
        flashloan_fee_percent=0.05,
    )

    payload = build_binance_market_state(
        {"top": rows, "bottom": losers, "sample_count": 40},
        aave_symbols=[],
        arbitrage_config=config,
        top_limit=10,
        bottom_limit=10,
        pair_side_limit=10,
    )

    assert len(payload["top"]) == 10
    assert len(payload["bottom"]) == 10
    assert payload["pair_count"] == 25


def test_binance_market_state_keeps_raw_50_separate_from_explicit_network_display_5():
    top = [_row(f"T{i}USDT", 10.0, 10.6 + i / 100) for i in range(60)]
    bottom = [_row(f"B{i}USDT", 10.0, 9.4 - i / 100) for i in range(60)]
    extremes = {"basket": [*top, *bottom], "sample_count": 120}
    registry = {
        symbol: CowToken(symbol, "0x" + f"{index:040d}"[-40:], 18, "test")
        for index, symbol in enumerate(
            ["USDC", *[f"T{i}" for i in range(60)], *[f"B{i}" for i in range(60)]],
            start=1,
        )
    }
    config = ArbitrageConfig(
        notional_usd=1000.0,
        trade_fee_percent=0.1,
        flashloan_fee_percent=0.05,
    )

    payload = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=config,
        top_limit=50,
        bottom_limit=50,
        pair_side_limit=5,
        cow_display_limit=5,
        cow_network=None,
        registry=registry,
        min_spread_percent=1.0,
    )

    assert len(payload["raw_top"]) == 50
    assert len(payload["raw_bottom"]) == 50
    assert len(payload["top"]) == 5
    assert len(payload["bottom"]) == 5
    assert payload["cow_filter"]["cow_display_limit"] == 5
    assert payload["pair_count"] == 25


def test_binance_raw_rankings_show_window_movers_even_below_trade_threshold():
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.01),
            _row("BBBUSDT", 10.0, 9.99),
            _row("LOWUSDT", 0.001, 0.0011),
        ],
        "sample_count": 3,
    }
    config = ArbitrageConfig(
        notional_usd=1000.0,
        trade_fee_percent=0.1,
        flashloan_fee_percent=0.05,
    )

    payload = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=config,
        top_limit=50,
        bottom_limit=50,
        pair_side_limit=1,
        cow_network=None,
        min_spread_percent=1.0,
    )

    assert [row["base_symbol"] for row in payload["raw_top"]] == ["AAA"]
    assert [row["base_symbol"] for row in payload["raw_bottom"]] == ["BBB"]
    assert payload["pair_count"] == 0


def test_cow_thresholds_use_dynamic_profit_floor_not_requested_spread():
    thresholds = cost_adjusted_cow_thresholds(
        requested_min_spread_percent=1.0,
        amount="100",
        arbitrage_config=ArbitrageConfig(
            notional_usd=1000.0,
            trade_fee_percent=0.1,
            flashloan_fee_percent=0.05,
            fee_reserve_percent=0.1,
        ),
        slippage_bps=50,
    )

    assert thresholds["requested_min_spread_percent"] == "1"
    assert thresholds["adjusted_min_spread_percent"] == "0.968"
    assert thresholds["min_window_spread_percent"] == "0.968"
    assert thresholds["min_side_change_percent"] == "0.3"
    assert thresholds["route_cost_floor_percent"] == "0.968"
    assert thresholds["slippage_percent"] is None
    assert thresholds["slippage_model"] == "dynamic_target_minus_acceptable_price"
    assert thresholds["min_profit_usd"] == "0.618"
    assert thresholds["min_profit_percent"] == "0.618"


def test_cow_execution_precheck_does_not_block_intent_mode_on_profit_floor():
    precheck = _cow_execution_precheck(
        {
            "input_amount": "1000",
            "final_delta_amount": "1",
            "final_amount": "1001",
            "final_symbol": "USDC",
            "viable": True,
            "cow_support": {"supported": True},
            "hops": [{"buy_amount": "100"}],
            "cow_flashloan_intent": {
                "enabled": True,
                "ready": True,
                "control_mode": "intent",
                "min_final_amount": "1005",
            },
            "quote_verified": True,
            "cow_flashloan_sdk_plan": {
                "flashloan_capability": {"submission_safe": True},
            },
            "binance_execution_plan": {
                "available": True,
                "final_symbol": "USDC",
                "profit_amount": "1",
                "profit_percent": "0.1",
                "steps": [
                    {
                        "from_symbol": "USDC",
                        "to_symbol": "AAA",
                        "input_amount": "1000",
                        "cow_sdk_parameters": {
                            "sell_amount_before_fee": "1000",
                            "target_buy_amount_after_fee": "100",
                            "min_buy_amount_after_fee": "99",
                        },
                    }
                ]
            },
        }
    )

    assert precheck["status"] in {
        "limit_order_ready_to_submit",
        "order_submission_switch_off",
        "order_submission_adapter_unavailable",
        "order_submission_signer_not_ready",
    }
    assert precheck["checks_passed"] is True
    assert precheck["profit_prediction_disabled"] is True
    assert precheck["quote_delta_positive"] is None
    assert precheck["quote_delta_above_diagnostic_floor"] is None
    assert precheck["local_profit_gate_enforced"] is False
    assert precheck["quote_probe_notes"] == ["upper_profit_prediction_disabled_intent_only"]
    assert precheck["auto_execute_min_profit_usd"] == "6.18"
    assert precheck["auto_execute_min_profit_percent"] == "0.618"


def test_cow_pure_profit_intent_binds_requested_principal_and_token_scope_only(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_CONTROL_MODE", "intent")
    market_state = {
        "top": [
            {"symbol": "T0USDT", "base_symbol": "T0", "change_percent": 1, "start_price": 10, "current_price": 10.5},
        ],
        "bottom": [
            {"symbol": "B0USDT", "base_symbol": "B0", "change_percent": -1, "start_price": 10, "current_price": 9.5},
        ],
    }

    intent = _cow_pure_profit_intent(
        amount="2500",
        input_symbol="USDC",
        final_symbol="USDC",
        path=["USDC", "AAA", "BBB", "USDC"],
        owner="0x" + "1" * 40,
        cow_network="bnb",
        cow_chain_id=56,
        threshold_detail={
            "route_trade_fee_percent": "0.2",
            "flashloan_fee_percent": "0.05",
            "fee_reserve_percent": "0.1",
            "min_profit_percent": "0.618",
        },
        market_state=market_state,
    )

    assert intent["initial_amount"] == "2500"
    assert intent["control_mode"] == "intent"
    assert intent["control_surface"]["current_mode"] == "intent"
    assert intent["control_surface"]["custom_mode_available"] is False
    assert intent["formula"] == "solver_owned_token_scope_only"
    assert intent["baseline_percent"] == "100"
    assert intent["total_required_percent"] == "100"
    assert intent["borrow_token_amount"] == "2500"
    assert intent["cow_sdk_order_intent"]["sell_amount_before_fee"] == "2500"
    assert intent["min_final_amount"] == "2524.2"
    assert intent["min_pure_profit_amount"] == "15.45"
    assert intent["fee_components"]["total_cost_usdc"] == "8.75"
    assert intent["x_amount"] == "24.2"
    assert intent["target_token_amount"] == "2524.2"
    assert intent["cow_sdk_order_intent"]["minimum_final_buy_amount_after_all_costs"] == "2524.2"
    assert intent["token_scope"]["input_symbol"] == "USDC"
    assert intent["token_scope"]["output_symbol"] == "USDC"
    assert intent["token_scope"]["tokens"] == ["T0", "T0USDT", "B0", "B0USDT", "USDC"]
    assert intent["token_scope"]["token_count"] == 5
    assert intent["token_scope"]["scope_role"] == "solver_owned_token_universe_only"
    assert intent["cow_app_data_schema"]["custom_metadata_allowed"] is False
    assert intent["cow_app_data_schema"]["exchange_token_scope_appdata_supported"] is False
    assert "market_hints" not in intent
    assert "route_path_hint" not in intent


def _route_hop_constraint_precheck_payload() -> dict:
    return {
        "input_amount": "1000",
        "final_amount": "1010",
        "final_delta_amount": "10",
        "final_symbol": "USDC",
        "viable": True,
        "cow_support": {"supported": True},
        "cow_flashloan_intent": {
            "enabled": True,
            "ready": True,
            "control_mode": "intent",
            "min_final_amount": "1005",
            "min_pure_profit_amount": "5",
        },
        "hops": [{"buy_amount": "90"}],
        "cow_flashloan_sdk_plan": {
            "flashloan_capability": {"submission_safe": True},
        },
        "quote_verified": True,
        "binance_execution_plan": {
            "available": True,
            "route": ["USDC", "AAA", "BBB", "USDC"],
            "final_symbol": "USDC",
            "profit_amount": "10",
            "profit_percent": "1",
            "steps": [
                {
                    "step": 1,
                    "from_symbol": "USDC",
                    "to_symbol": "AAA",
                    "input_amount": "1000",
                    "min_output_amount": "99",
                    "target_output_amount": "100",
                    "cow_sdk_parameters": {
                        "sell_amount_before_fee": "1000",
                        "target_buy_amount_after_fee": "100",
                        "min_buy_amount_after_fee": "99",
                    },
                }
            ],
        },
    }


def test_cow_execution_precheck_default_intent_mode_keeps_hop_constraints_diagnostic(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_CONTROL_MODE", "intent")

    precheck = _cow_execution_precheck(_route_hop_constraint_precheck_payload())

    assert precheck["control_mode"] == "intent"
    assert precheck["route_hop_constraints_enforced"] is False
    assert precheck["route_hop_constraints_passed"] is False
    assert precheck["checks_passed"] is True
    assert precheck["intent_mode_ready"] is True


def test_cow_execution_precheck_ignores_route_hop_mode_request(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_CONTROL_MODE", "route_hop")

    precheck = _cow_execution_precheck(_route_hop_constraint_precheck_payload())

    assert precheck["control_mode"] == "intent"
    assert precheck["route_hop_constraints_enforced"] is False
    assert precheck["route_hop_constraints_passed"] is False
    assert precheck["checks_passed"] is True
    assert precheck["status"] in {
        "limit_order_ready_to_submit",
        "order_submission_switch_off",
        "order_submission_adapter_unavailable",
        "order_submission_signer_not_ready",
    }


def test_cow_execution_precheck_can_enter_submit_ready_state(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_CONTROL_MODE", "intent")
    monkeypatch.setenv("COW_ORDER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_adapter_available", lambda: True)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_sdk_status", _sdk_ready_status)
    monkeypatch.setattr(
         "dex.runtime.cow_submission_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    precheck = _cow_execution_precheck(_route_hop_constraint_precheck_payload())

    assert precheck["checks_passed"] is True
    assert precheck["can_submit_order"] is True
    assert precheck["order_submission_enabled"] is True
    assert precheck["order_submission_sdk_ready"] is True
    assert precheck["order_submission_signer_ready"] is True
    assert precheck["status"] == "limit_order_ready_to_submit"


def test_cow_execution_precheck_blocks_submission_when_runtime_sdk_missing(monkeypatch):
    monkeypatch.setenv("COW_ORDER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_adapter_available", lambda: True)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_sdk_status", _sdk_missing_status)
    monkeypatch.setattr(
         "dex.runtime.cow_submission_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    precheck = _cow_execution_precheck(_route_hop_constraint_precheck_payload())

    assert precheck["checks_passed"] is True
    assert precheck["order_submission_sdk_ready"] is False
    assert precheck["order_submission_sdk_status"]["install_command"]
    assert precheck["can_submit_order"] is False
    assert precheck["status"] == "cow_flashloan_sdk_install_required"
    assert "cow_flashloan_sdk_install_required" in precheck["reasons"]


def test_cow_execution_precheck_records_drawdown_when_quote_loses_money(monkeypatch):
    monkeypatch.setenv("COW_ORDER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_adapter_available", lambda: True)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_sdk_status", _sdk_ready_status)
    monkeypatch.setattr(
         "dex.runtime.cow_submission_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    precheck = _cow_execution_precheck(
        {
            "input_amount": "1000",
            "final_delta_amount": "-25",
            "final_symbol": "USDC",
            "viable": True,
            "cow_support": {"supported": True},
            "hops": [{"buy_amount": "100"}],
            "cow_flashloan_intent": {
                "enabled": True,
                "ready": True,
                "control_mode": "intent",
            },
            "quote_verified": True,
            "cow_flashloan_sdk_plan": {
                "flashloan_capability": {"submission_safe": True},
            },
            "binance_execution_plan": {
                "available": True,
                "final_symbol": "USDC",
                "steps": [
                    {
                        "from_symbol": "USDC",
                        "to_symbol": "AAA",
                        "input_amount": "1000",
                        "cow_sdk_parameters": {
                            "sell_amount_before_fee": "1000",
                            "target_buy_amount_after_fee": "100",
                            "min_buy_amount_after_fee": "99",
                        },
                    }
                ]
            },
        }
    )

    assert precheck["status"] == "limit_order_ready_to_submit"
    assert precheck["can_submit_order"] is True
    assert precheck["profit_prediction_disabled"] is True
    assert precheck["quote_delta_positive"] is None
    assert precheck["quote_delta_offset_amount"] is None
    assert precheck["quote_delta_offset_percent"] is None
    assert precheck["quote_probe_notes"] == ["upper_profit_prediction_disabled_intent_only"]
    assert precheck["local_profit_gate_enforced"] is False


def test_cow_execution_precheck_keeps_three_hop_sdk_capability_as_diagnostic_only():
    precheck = _cow_execution_precheck(
        {
            "input_amount": "1000",
            "final_delta_amount": "10",
            "final_symbol": "USDC",
            "viable": True,
            "cow_support": {"supported": True},
            "hops": [
                {"buy_amount": "100"},
                {"buy_amount": "110"},
                {"buy_amount": "1010"},
            ],
            "cow_flashloan_intent": {
                "enabled": True,
                "ready": True,
                "control_mode": "intent",
            },
            "quote_verified": True,
            "binance_execution_plan": {
                "available": True,
                "route": ["USDC", "AAA", "BBB", "USDC"],
                "initial_amount": "1000",
                "final_symbol": "USDC",
                "profit_amount": "10",
                "profit_percent": "1",
                "steps": [
                    {
                        "step": 1,
                        "from_symbol": "USDC",
                        "to_symbol": "AAA",
                        "input_amount": "1000",
                        "min_output_amount": "99",
                        "target_output_amount": "100",
                        "cow_sdk_parameters": {
                            "sell_amount_before_fee": "1000",
                            "target_buy_amount_after_fee": "100",
                            "min_buy_amount_after_fee": "99",
                        },
                    }
                    ,
                    {
                        "step": 2,
                        "from_symbol": "AAA",
                        "to_symbol": "BBB",
                        "input_amount": "100",
                        "min_output_amount": "99",
                        "target_output_amount": "100",
                        "cow_sdk_parameters": {
                            "sell_amount_before_fee": "100",
                            "target_buy_amount_after_fee": "100",
                            "min_buy_amount_after_fee": "99",
                        },
                    },
                    {
                        "step": 3,
                        "from_symbol": "BBB",
                        "to_symbol": "USDC",
                        "input_amount": "110",
                        "min_output_amount": "1000",
                        "target_output_amount": "1010",
                        "cow_sdk_parameters": {
                            "sell_amount_before_fee": "110",
                            "target_buy_amount_after_fee": "1010",
                            "min_buy_amount_after_fee": "1000",
                        },
                    },
                ],
            },
            "cow_flashloan_sdk_plan": {
                "sdk": "@cowprotocol/sdk-flash-loans",
                "flow": "AaveCollateralSwapSdk",
                "route": ["USDC", "AAA", "BBB", "USDC"],
                "steps": [
                    {"step": 1, "from_symbol": "USDC", "to_symbol": "AAA"},
                    {"step": 2, "from_symbol": "AAA", "to_symbol": "BBB"},
                    {"step": 3, "from_symbol": "BBB", "to_symbol": "USDC"},
                ],
            },
        }
    )

    assert precheck["cow_sdk_flashloan_ready"] is False
    assert precheck["flashloan_sdk_gate_enforced"] is False
    assert precheck["checks_passed"] is True
    assert precheck["intent_mode_ready"] is True
    assert precheck["status"] in {
        "limit_order_ready_to_submit",
        "order_submission_switch_off",
        "order_submission_adapter_unavailable",
        "order_submission_signer_not_ready",
    }
    assert "cow_intent_ready" in precheck["reasons"]
    assert any("diagnostic_cow_flashloan_sdk_plan_required" in reason for reason in precheck["reasons"])


def test_cow_execution_precheck_does_not_block_when_official_sdk_plan_missing(monkeypatch):
    monkeypatch.setenv("COW_ORDER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_adapter_available", lambda: True)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_sdk_status", _sdk_ready_status)
    monkeypatch.setattr(
         "dex.runtime.cow_submission_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    precheck = _cow_execution_precheck(
        {
            "input_amount": "1000",
            "final_delta_amount": "10",
            "final_symbol": "USDC",
            "viable": True,
            "cow_support": {"supported": True},
            "hops": [{"buy_amount": "100"}],
            "cow_flashloan_intent": {
                "enabled": True,
                "ready": True,
                "control_mode": "intent",
            },
            "quote_verified": True,
            "binance_execution_plan": {
                "available": True,
                "route": ["USDC", "AAA"],
                "initial_amount": "1000",
                "final_symbol": "USDC",
                "profit_amount": "10",
                "profit_percent": "1",
                "steps": [
                    {
                        "step": 1,
                        "from_symbol": "USDC",
                        "to_symbol": "AAA",
                        "input_amount": "1000",
                        "min_output_amount": "99",
                        "target_output_amount": "100",
                        "cow_sdk_parameters": {
                            "sell_amount_before_fee": "1000",
                            "target_buy_amount_after_fee": "100",
                            "min_buy_amount_after_fee": "99",
                        },
                    }
                ],
            },
        }
    )

    assert precheck["cow_sdk_flashloan_ready"] is False
    assert precheck["flashloan_sdk_gate_enforced"] is False
    assert precheck["checks_passed"] is True
    assert precheck["intent_mode_ready"] is True
    assert precheck["can_submit_order"] is True
    assert precheck["status"] == "limit_order_ready_to_submit"
    assert "cow_intent_ready" in precheck["reasons"]
    assert any("diagnostic_cow_flashloan_sdk_plan_required" in reason for reason in precheck["reasons"])


def test_binance_market_state_uses_pair_spread_threshold_for_trades():
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.08),
            _row("BBBUSDT", 10.0, 9.92),
        ],
        "sample_count": 2,
    }
    registry = {
        "USDC": CowToken("USDC", "0x" + "1" * 40, 6, "test"),
        "AAA": CowToken("AAA", "0x" + "2" * 40, 18, "test"),
        "BBB": CowToken("BBB", "0x" + "3" * 40, 18, "test"),
    }
    config = ArbitrageConfig(notional_usd=1000.0, trade_fee_percent=0.1, flashloan_fee_percent=0.05)
    thresholds = {
        "adjusted_min_spread_percent": "2",
        "min_window_spread_percent": "2",
        "min_side_change_percent": "0.05",
        "min_token_price_usd": "0.01",
    }

    payload = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=config,
        pair_side_limit=1,
        cow_network=None,
        registry=registry,
        min_spread_percent=2.0,
        min_side_change_percent=0.05,
        threshold_detail=thresholds,
    )

    assert [row["base_symbol"] for row in payload["top"]] == ["AAA"]
    assert [row["base_symbol"] for row in payload["bottom"]] == ["BBB"]
    assert payload["pair_count"] == 0
    assert payload["cow_filter"]["threshold_detail"] == thresholds


def test_binance_market_state_pair_spread_can_pass_below_old_side_threshold():
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.08),
            _row("BBBUSDT", 10.0, 9.92),
        ],
        "sample_count": 2,
    }
    registry = {
        "USDC": CowToken("USDC", "0x" + "1" * 40, 6, "test"),
        "AAA": CowToken("AAA", "0x" + "2" * 40, 18, "test"),
        "BBB": CowToken("BBB", "0x" + "3" * 40, 18, "test"),
    }
    config = ArbitrageConfig(notional_usd=1000.0, trade_fee_percent=0.1, flashloan_fee_percent=0.05)

    payload = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=config,
        pair_side_limit=1,
        cow_network=None,
        registry=registry,
        min_spread_percent=1.5,
        min_side_change_percent=0.05,
    )

    assert payload["pair_count"] == 1
    assert payload["pairs"][0]["window_spread_percent"] > 1.5


def test_binance_market_state_requires_avalanche_v3_pool_route(monkeypatch):
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.8),
            _row("BBBUSDT", 10.0, 9.4),
            _row("CCCUSDT", 10.0, 10.7),
        ],
        "sample_count": 3,
    }
    registry = {
        "USDC": CowToken("USDC", "0x" + "1" * 40, 6, "test"),
        "AAA": CowToken("AAA", "0x" + "2" * 40, 18, "test"),
        "BBB": CowToken("BBB", "0x" + "3" * 40, 18, "test"),
        "CCC": CowToken("CCC", "0x" + "4" * 40, 18, "test"),
    }
    monkeypatch.setattr(
        "market.binance_market.service._load_avalanche_v3_pool_filter",
        lambda: _fake_pool_filter(
            ["USDC", "AAA", "BBB", "CCC"],
            [("USDC", "AAA"), ("USDC", "BBB"), ("USDC", "CCC"), ("AAA", "BBB")],
        ),
    )
    config = ArbitrageConfig(notional_usd=1000.0, trade_fee_percent=0.1, flashloan_fee_percent=0.05)

    payload = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=config,
        pair_side_limit=2,
        cow_network="avalanche",
        registry=registry,
        min_spread_percent=1.0,
        min_side_change_percent=0.05,
    )

    assert payload["pair_count"] == 1
    assert [(pair["x_base_symbol"], pair["y_base_symbol"]) for pair in payload["pairs"]] == [("AAA", "BBB")]
    assert payload["pairs"][0]["pool_assembly_supported"] is True
    assert payload["pairs"][0]["pool_assembly"]["missingPairs"] == []
    assert payload["cow_filter"]["pool_filter"]["rejected_pair_count"] == 1


