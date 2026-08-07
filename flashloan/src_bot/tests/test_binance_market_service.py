import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from execution.cow_routes import CowToken
from strategy.arbitrage import ArbitrageConfig
from web.binance_market_service import (
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
from db.storage_cow_execution import (
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
        cow_network="sepolia",
        min_spread_percent=1.0,
        registry=registry,
    )

    assert [row["base_symbol"] for row in payload["top"]] == ["AAA"]
    assert [row["base_symbol"] for row in payload["bottom"]] == ["BBB"]
    assert [row["base_symbol"] for row in payload["raw_top"]][:2] == ["UNSUPPORTEDTOP", "AAA"]
    assert [row["base_symbol"] for row in payload["raw_bottom"]][:2] == ["UNSUPPORTEDBOTTOM", "BBB"]
    assert payload["cow_filter"]["enabled"] is True
    assert payload["cow_filter"]["supported_symbol_count"] == 2
    assert payload["cow_filter"]["unsupported_symbol_count"] == 2
    assert payload["cow_filter"]["market_excluded_symbol_count"] == 4
    assert payload["cow_filter"]["min_side_change_percent"] == 0.3
    assert payload["cow_filter"]["min_token_price_usd"] == 0.01
    assert payload["pair_count"] == 1
    assert [(pair["x_base_symbol"], pair["y_base_symbol"]) for pair in payload["pairs"]] == [
        ("AAA", "BBB"),
    ]
    assert all(pair["window_spread_percent"] > 1.0 for pair in payload["pairs"])


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
        cow_network="bnb",
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

    assert precheck["status"] in {"limit_order_ready_to_submit", "limit_order_ready_not_submitted"}
    assert precheck["checks_passed"] is True
    assert precheck["profit_positive"] is True
    assert precheck["profit_above_auto_threshold"] is False
    assert precheck["local_profit_gate_enforced"] is False
    assert precheck["local_profit_diagnostic_reasons"]
    assert precheck["auto_execute_min_profit_usd"] == "6.18"
    assert precheck["auto_execute_min_profit_percent"] == "0.618"


def test_cow_pure_profit_intent_uses_fixed_1000u_principal_and_token_scope_only(monkeypatch):
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

    assert intent["initial_amount"] == "1000"
    assert intent["control_mode"] == "intent"
    assert intent["control_surface"]["route_hop_constraints_enforced"] is False
    assert intent["formula"] == "solver_owned_token_scope_only"
    assert intent["baseline_percent"] == "100"
    assert intent["total_required_percent"] == "100"
    assert intent["cow_sdk_order_intent"]["sell_amount_before_fee"] == "1000"
    assert intent["min_final_amount"] == "1000"
    assert intent["token_scope"]["input_symbol"] == "USDC"
    assert intent["token_scope"]["output_symbol"] == "USDC"
    assert intent["token_scope"]["tokens"] == ["T0", "T0USDT", "B0", "B0USDT", "USDC"]
    assert intent["token_scope"]["token_count"] == 5
    assert intent["token_scope"]["scope_role"] == "solver_owned_token_universe_only"
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


def test_cow_execution_precheck_route_hop_mode_enforces_hop_constraints(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_CONTROL_MODE", "route_hop")

    precheck = _cow_execution_precheck(_route_hop_constraint_precheck_payload())

    assert precheck["control_mode"] == "route_hop"
    assert precheck["route_hop_constraints_enforced"] is True
    assert precheck["route_hop_constraints_passed"] is False
    assert precheck["checks_passed"] is False
    assert precheck["status"] == "price_guard_failed"


def test_cow_execution_precheck_can_enter_submit_ready_state(monkeypatch):
    monkeypatch.setenv("COW_FLASHLOAN_CONTROL_MODE", "intent")
    monkeypatch.setenv("COW_ORDER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setattr(
        "web.control_panel_cow_pause.cow_submission_pause_guard_status",
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
    assert precheck["order_submission_signer_ready"] is True
    assert precheck["status"] in {"limit_order_ready_to_submit", "limit_order_ready_not_submitted"}


def test_cow_execution_precheck_records_drawdown_when_quote_loses_money(monkeypatch):
    monkeypatch.setenv("COW_ORDER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setattr(
        "web.control_panel_cow_pause.cow_submission_pause_guard_status",
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
    assert precheck["profit_positive"] is False
    assert precheck["drawdown_amount"] == "25"
    assert precheck["drawdown_percent"] == "2.5"
    assert any("cow_quote_drawdown" in reason for reason in precheck["local_profit_diagnostic_reasons"])
    assert precheck["local_profit_gate_enforced"] is False


def test_cow_execution_precheck_rejects_three_hop_plan_without_capability_evidence():
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
    assert precheck["checks_passed"] is False
    assert precheck["status"] == "cow_flashloan_sdk_plan_required"
    assert any("cow_flashloan_sdk_plan_required" in reason for reason in precheck["reasons"])


def test_cow_execution_precheck_blocks_when_official_sdk_plan_missing():
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
    assert precheck["checks_passed"] is False
    assert precheck["status"] == "cow_flashloan_sdk_plan_required"
    assert any("cow_flashloan_sdk_plan_required" in reason for reason in precheck["reasons"])


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
        cow_network="bnb",
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
        cow_network="bnb",
        registry=registry,
        min_spread_percent=1.5,
        min_side_change_percent=0.05,
    )

    assert payload["pair_count"] == 1
    assert payload["pairs"][0]["window_spread_percent"] > 1.5


def test_cow_network_market_claims_lists_each_mainnet_support_set():
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.8),
            _row("BBBUSDT", 10.0, 9.4),
            _row("CCCUSDT", 10.0, 10.7),
            _row("DDDUSDT", 10.0, 9.3),
            _row("PEPEUSDT", 0.000002, 0.000003),
        ],
        "sample_count": 5,
    }
    claims = build_cow_network_market_claims(
        extremes,
        {
            "avalanche": {
                "source": "test",
                "token_count": 3,
                "registry": {
                    "AAA": CowToken("AAA", "0x" + "1" * 40, 18, "test"),
                    "BBB": CowToken("BBB", "0x" + "2" * 40, 18, "test"),
                },
            },
            "bnb": {
                "source": "test",
                "token_count": 3,
                "registry": {
                    "CCC": CowToken("CCC", "0x" + "3" * 40, 18, "test"),
                    "DDD": CowToken("DDD", "0x" + "4" * 40, 18, "test"),
                    "PEPE": CowToken("PEPE", "0x" + "5" * 40, 18, "test"),
                },
            },
            "sepolia": {"source": "test", "token_count": 1, "registry": {}},
        },
        limit=50,
        min_spread_percent=1.0,
        min_side_change_percent=0.5,
    )

    by_network = {item["network"]: item for item in claims}
    assert "sepolia" not in by_network
    assert [row["base_symbol"] for row in by_network["avalanche"]["top"]] == ["AAA"]
    assert [row["base_symbol"] for row in by_network["avalanche"]["bottom"]] == ["BBB"]
    assert [row["base_symbol"] for row in by_network["bnb"]["top"]] == ["CCC"]
    assert [row["base_symbol"] for row in by_network["bnb"]["bottom"]] == ["DDD"]
    assert by_network["bnb"]["market_excluded_symbol_count"] == 1
    assert by_network["bnb"]["min_side_change_percent"] == 0.3
    assert by_network["bnb"]["pair_count"] == 1


def test_cow_network_market_claims_do_not_exclude_shared_tokens_between_networks():
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.8),
            _row("BBBUSDT", 10.0, 9.4),
        ],
        "sample_count": 2,
    }
    shared_registry = {
        "AAA": CowToken("AAA", "0x" + "1" * 40, 18, "test"),
        "BBB": CowToken("BBB", "0x" + "2" * 40, 18, "test"),
    }

    claims = build_cow_network_market_claims(
        extremes,
        {
            "avalanche": {"source": "test", "token_count": 2, "registry": shared_registry},
            "polygon": {"source": "test", "token_count": 2, "registry": shared_registry},
        },
        limit=1,
        min_spread_percent=1.0,
    )

    by_network = {item["network"]: item for item in claims}
    assert [row["base_symbol"] for row in by_network["avalanche"]["top"]] == ["AAA"]
    assert [row["base_symbol"] for row in by_network["avalanche"]["bottom"]] == ["BBB"]
    assert [row["base_symbol"] for row in by_network["polygon"]["top"]] == ["AAA"]
    assert [row["base_symbol"] for row in by_network["polygon"]["bottom"]] == ["BBB"]
    assert by_network["avalanche"]["pair_count"] == 1
    assert by_network["polygon"]["pair_count"] == 1


def test_cow_supported_market_overview_lists_union_top_bottom_50_without_chain_exclusion():
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.8),
            _row("CCCUSDT", 10.0, 10.7),
            _row("BBBUSDT", 10.0, 9.4),
            _row("DDDUSDT", 10.0, 9.3),
            _row("EEEUSDT", 10.0, 10.9),
        ],
        "sample_count": 5,
    }
    shared_registry = {
        "AAA": CowToken("AAA", "0x" + "1" * 40, 18, "test"),
        "BBB": CowToken("BBB", "0x" + "2" * 40, 18, "test"),
    }
    overview = build_cow_supported_market_overview(
        extremes,
        {
            "avalanche": {"source": "test", "token_count": 2, "registry": shared_registry},
            "polygon": {"source": "test", "token_count": 3, "registry": shared_registry | {"CCC": CowToken("CCC", "0x" + "3" * 40, 18, "test")}},
            "bnb": {"source": "test", "token_count": 1, "registry": {"DDD": CowToken("DDD", "0x" + "4" * 40, 18, "test")}},
        },
        limit=50,
        min_side_change_percent=0.5,
    )

    assert overview["limit"] == 50
    assert overview["source_market_row_count"] == 5
    assert overview["market_eligible_symbol_count"] == 5
    assert overview["market_excluded_symbol_count"] == 0
    assert overview["market_excluded_reason_counts"] == {}
    assert overview["supported_symbol_count"] == 4
    assert overview["unsupported_symbol_count"] == 1
    assert overview["min_side_change_percent"] == 0.3
    assert overview["min_token_price_usd"] == 0.01
    assert [row["base_symbol"] for row in overview["top"]] == ["AAA", "CCC"]
    assert [row["base_symbol"] for row in overview["bottom"]] == ["DDD", "BBB"]
    aaa = overview["top"][0]
    assert aaa["cow_networks"] == ["avalanche", "polygon"]
    assert aaa["cow_network_count"] == 2


def test_cow_supported_market_overview_uses_only_side_move_price_and_cow_support_filters():
    extremes = {
        "basket": [
            _row("GAINUSDT", 10.0, 10.04),
            _row("GAINLOWMOVEUSDT", 10.0, 10.004),
            _row("GAINLOWPRICEUSDT", 0.009, 0.0092),
            _row("GAINUNSUPPORTEDUSDT", 10.0, 10.04),
            _row("LOSSUSDT", 10.0, 9.96),
            _row("LOSSLOWMOVEUSDT", 10.0, 9.996),
            _row("LOSSLOWPRICEUSDT", 0.0092, 0.009),
            _row("LOSSUNSUPPORTEDUSDT", 10.0, 9.96),
        ],
        "observation_universe_size": 8,
        "sample_count": 8,
    }
    registry = {
        "GAIN": CowToken("GAIN", "0x" + "1" * 40, 18, "test"),
        "GAINLOWMOVE": CowToken("GAINLOWMOVE", "0x" + "2" * 40, 18, "test"),
        "GAINLOWPRICE": CowToken("GAINLOWPRICE", "0x" + "3" * 40, 18, "test"),
        "LOSS": CowToken("LOSS", "0x" + "4" * 40, 18, "test"),
        "LOSSLOWMOVE": CowToken("LOSSLOWMOVE", "0x" + "5" * 40, 18, "test"),
        "LOSSLOWPRICE": CowToken("LOSSLOWPRICE", "0x" + "6" * 40, 18, "test"),
    }

    overview = build_cow_supported_market_overview(
        extremes,
        {"avalanche": {"source": "test", "token_count": len(registry), "registry": registry}},
        limit=50,
        min_side_change_percent=9.0,
        threshold_detail={"min_window_spread_percent": "99"},
    )

    assert overview["min_side_change_percent"] == 0.3
    assert [row["base_symbol"] for row in overview["top"]] == ["GAIN"]
    assert [row["base_symbol"] for row in overview["bottom"]] == ["LOSS"]
    assert overview["top_filter"]["market_eligible_symbol_count"] == 2
    assert overview["top_filter"]["supported_symbol_count"] == 1
    assert overview["top_filter"]["unsupported_symbol_count"] == 1
    assert overview["bottom_filter"]["market_eligible_symbol_count"] == 2
    assert overview["bottom_filter"]["supported_symbol_count"] == 1
    assert overview["bottom_filter"]["unsupported_symbol_count"] == 1


def test_binance_market_snapshot_fills_full_market_gainers_and_losers(monkeypatch):
    previous = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "basket": [
            {"symbol": "AAAUSDT", "current_price": 1.0, "end_ms": 1785801600000},
            {"symbol": "BBBUSDT", "current_price": 2.0, "end_ms": 1785801600000},
            {"symbol": "CCCUSDT", "current_price": 5.0, "end_ms": 1785801600000},
            {"symbol": "DDDUSDT", "current_price": 4.0, "end_ms": 1785801600000},
        ],
    }
    current_rows = [
        {"symbol": "AAAUSDT", "current_price": 1.2},
        {"symbol": "BBBUSDT", "current_price": 2.2},
        {"symbol": "CCCUSDT", "current_price": 4.0},
        {"symbol": "DDDUSDT", "current_price": 3.8},
    ]

    snapshot = build_binance_rest_market_snapshot(
        side_limit=2,
        previous_snapshot=previous,
        current_rows=current_rows,
    )

    assert snapshot["price_source"] == "rest_interval"
    assert snapshot["observation_universe_size"] == 4
    assert [row["symbol"] for row in snapshot["top"]] == ["AAAUSDT", "BBBUSDT"]
    assert [row["symbol"] for row in snapshot["bottom"]] == ["CCCUSDT", "DDDUSDT"]
    assert round(snapshot["top"][0]["change_percent"], 6) == 20.0


def test_binance_market_snapshot_first_sample_has_no_window_ranking():
    snapshot = build_binance_rest_market_snapshot(
        side_limit=2,
        previous_snapshot=None,
        current_rows=[
            {"symbol": "AAAUSDT", "current_price": 1.2},
            {"symbol": "BBBUSDT", "current_price": 2.2},
        ],
    )

    assert snapshot["price_source"] == "rest_interval"
    assert snapshot["top"] == []
    assert snapshot["bottom"] == []
    assert snapshot["sample_count"] == 2


def test_binance_market_state_flags_insufficient_realtime_extremes():
    assert needs_binance_rest_snapshot({"basket": [_row("AVAXUSDT", 100.0, 101.0)]}, side_limit=5)

    truncated = {
        "observation_universe_size": 100,
        "top": [_row(f"T{i}USDT", 100.0, 101.0) for i in range(5)],
        "bottom": [_row(f"B{i}USDT", 100.0, 99.0) for i in range(5)],
    }
    assert needs_binance_rest_snapshot(truncated, side_limit=5)

    enough = {
        "observation_universe_size": 10,
        "top": [_row(f"T{i}USDT", 100.0, 101.0) for i in range(5)],
        "bottom": [_row(f"B{i}USDT", 100.0, 99.0) for i in range(5)],
        "basket": [
            *[_row(f"T{i}USDT", 100.0, 101.0) for i in range(5)],
            *[_row(f"B{i}USDT", 100.0, 99.0) for i in range(5)],
        ],
    }
    assert not needs_binance_rest_snapshot(enough, side_limit=5)


def test_select_binance_market_extremes_refreshes_cached_snapshot(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "binance_market_snapshot.json"
    snapshot = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "top": [
            {"symbol": f"A{i}USDT", "change_percent": 10 - i, "start_price": 1, "current_price": 1 + i}
            for i in range(5)
        ],
        "bottom": [
            {"symbol": f"B{i}USDT", "change_percent": -10 + i, "start_price": 2, "current_price": 2 + i}
            for i in range(5)
        ],
        "basket": [
            *[
                {"symbol": f"A{i}USDT", "change_percent": 10 - i, "start_price": 1, "current_price": 1 + i}
                for i in range(5)
            ],
            *[
                {"symbol": f"B{i}USDT", "change_percent": -10 + i, "start_price": 2, "current_price": 2 + i}
                for i in range(5)
            ],
        ],
        "observation_universe_size": 10,
        "sample_count": 10,
        "price_source": "rest_interval",
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    selected = select_binance_market_extremes(
        {"basket": [_row("AVAXUSDT", 100.0, 101.0)]},
        side_limit=5,
        snapshot_path=snapshot_path,
        current_rows=[
            {"symbol": "A0USDT", "current_price": 1.2},
            {"symbol": "A1USDT", "current_price": 2.2},
            {"symbol": "A2USDT", "current_price": 3.2},
            {"symbol": "A3USDT", "current_price": 4.2},
            {"symbol": "A4USDT", "current_price": 5.2},
            {"symbol": "B0USDT", "current_price": 1.8},
            {"symbol": "B1USDT", "current_price": 2.8},
            {"symbol": "B2USDT", "current_price": 3.8},
            {"symbol": "B3USDT", "current_price": 4.8},
            {"symbol": "B4USDT", "current_price": 5.8},
        ],
    )

    assert selected["market_state_source"] == "rest_interval_live"
    assert [row["symbol"] for row in selected["top"]][:2] == ["A0USDT", "A1USDT"]
    assert [row["symbol"] for row in selected["bottom"]][:2] == ["B0USDT", "B1USDT"]
    assert read_binance_market_snapshot(snapshot_path)["price_source"] == "rest_interval"


def test_select_binance_market_extremes_ignores_24h_rankings(tmp_path):
    snapshot_path = tmp_path / "binance_market_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "basket": [
                    {"symbol": "AAAUSDT", "current_price": 10.0},
                    {"symbol": "BBBUSDT", "current_price": 10.0},
                ],
                "price_source": "rest_interval",
            }
        ),
        encoding="utf-8",
    )
    realtime_24h = {
        "price_source": "binance_rest_24h",
        "top": [_row("PUMPUSDT", 1.0, 2.0)],
        "bottom": [_row("DUMPUSDT", 2.0, 1.0)],
        "basket": [_row("PUMPUSDT", 1.0, 2.0), _row("DUMPUSDT", 2.0, 1.0)],
    }

    selected = select_binance_market_extremes(
        realtime_24h,
        side_limit=1,
        snapshot_path=snapshot_path,
        current_rows=[
            {"symbol": "AAAUSDT", "current_price": 10.5},
            {"symbol": "BBBUSDT", "current_price": 9.5},
        ],
    )

    assert selected["price_source"] == "rest_interval"
    assert selected["market_state_source"] == "rest_interval_live"
    assert [row["symbol"] for row in selected["top"]] == ["AAAUSDT"]
    assert [row["symbol"] for row in selected["bottom"]] == ["BBBUSDT"]


def test_cow_quote_verification_quotes_selected_pairs(monkeypatch):
    monkeypatch.setenv("COW_NETWORK", "avalanche")
    monkeypatch.setenv("COW_CHAIN_ID", "43114")
    monkeypatch.setenv("COW_OWNER_SEPOLIA", "0x" + "9" * 40)
    monkeypatch.setenv("COW_OWNER_AVALANCHE", "0x" + "8" * 40)
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"},
            {"rank": 2, "pair": "CCC / DDD", "x_base_symbol": "CCC", "y_base_symbol": "DDD"},
        ],
    }

    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB", "CCC", "DDD"], start=1)
    }
    monkeypatch.setattr("web.binance_market_service.build_token_registry", lambda *args, **kwargs: registry)

    quoted_paths = []
    quoted_networks = []
    quoted_owners = []

    def fake_evaluate(route, **kwargs):
        path = route["path"]
        quoted_paths.append(path)
        quoted_networks.append(kwargs["cow_network"])
        quoted_owners.append(kwargs["owner"])
        return {
            "name": route["name"],
            "path": path,
            "input_amount": "1000",
            "input_symbol": path[0],
            "final_symbol": path[-1],
            "final_amount_units": "1200" if path[1] == "BBB" else "900",
            "final_amount": "1200" if path[1] == "BBB" else "900",
            "viable": path[1] == "BBB",
            "hops": [],
        }

    monkeypatch.setattr("web.binance_market_service.evaluate_cow_route", fake_evaluate)
    monkeypatch.setattr(
        "web.binance_market_service.rank_cow_routes",
        lambda items: sorted(items, key=lambda item: int(item["final_amount_units"]), reverse=True),
    )

    payload = build_cow_quote_verification(market_state, quote_limit=2)

    assert payload["selected_pair_count"] == 2
    assert payload["route_count"] == 4
    assert payload["viable_count"] == 1
    assert payload["cow_network"] == "avalanche"
    assert payload["cow_chain_id"] == 43114
    assert payload["cow_testnet"] is False
    assert payload["owner"] == "0x" + "8" * 40
    assert payload["owner_source"] == "COW_OWNER_AVALANCHE"
    assert quoted_paths[0] == ["USDC", "BBB", "AAA", "USDC"]
    assert quoted_paths[1] == ["USDC", "AAA", "BBB", "USDC"]
    assert len(quoted_paths) == 4
    assert set(quoted_networks) == {"avalanche"}
    assert set(quoted_owners) == {"0x" + "8" * 40}
    assert payload["best"]["path"][1] == "BBB"
    assert payload["best"]["priority_reason"] == "buy_loser_then_gainer"
    assert payload["best"]["costs"]["quote_api_gas_used"] == 0
    assert payload["best"]["costs"]["settlement_gas_payer"] == "solver"
    assert payload["best"]["costs"]["approval_gas_status"] == "requires_allowance_check_before_execution"
    assert payload["best"]["costs"]["profit_amount"] == "200"


def test_cow_quote_verification_records_all_chain_candidates(monkeypatch):
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"},
            {"rank": 2, "pair": "CCC / DDD", "x_base_symbol": "CCC", "y_base_symbol": "DDD"},
            {"rank": 3, "pair": "EEE / FFF", "x_base_symbol": "EEE", "y_base_symbol": "FFF"},
        ],
    }
    registry = {
        symbol: CowToken(symbol, "0x" + f"{index:040d}"[-40:], 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"], start=1)
    }
    monkeypatch.setattr("web.binance_market_service.build_token_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(
        "web.binance_market_service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "final_amount_units": "1000500000000000000000",
            "final_amount": "1000.5",
            "viable": True,
            "hops": [{"hop": 1, "sell_symbol": "USDC", "buy_symbol": route["path"][1], "sell_amount": "1000", "buy_amount": "1000"}],
        },
    )
    monkeypatch.setattr("web.binance_market_service.rank_cow_routes", lambda items: items)

    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)
    attempts = build_cow_execution_attempts(payload, market_state=market_state)

    assert payload["selected_pair_count"] == 1
    assert payload["route_count"] == 2
    assert len(attempts) == 2
    assert {attempt["pair"] for attempt in attempts} == {"AAA / BBB"}


def test_cow_quote_verification_selects_target_and_slippage_from_three_prices(monkeypatch):
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {
                "rank": 1,
                "pair": "AAA / BBB",
                "x_base_symbol": "AAA",
                "y_base_symbol": "BBB",
                "x_start_price": 10,
                "x_current_price": 12,
                "y_start_price": 9,
                "y_current_price": 10,
            },
        ],
    }
    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB"], start=1)
    }

    def fake_evaluate(route, **kwargs):
        path = route["path"]
        if path == ["USDC", "BBB", "AAA", "USDC"]:
            hops = [
                {
                    "hop": 1,
                    "sell_symbol": "USDC",
                    "buy_symbol": "BBB",
                    "sell_amount": "1000",
                    "buy_amount": "125",
                    "exchange_rate": "0.125",
                    "fee_amount": "0",
                },
                {
                    "hop": 2,
                    "sell_symbol": "BBB",
                    "buy_symbol": "AAA",
                    "sell_amount": "125",
                    "buy_amount": "137.5",
                    "exchange_rate": "1.1",
                    "fee_amount": "0",
                },
                {
                    "hop": 3,
                    "sell_symbol": "AAA",
                    "buy_symbol": "USDC",
                    "sell_amount": "137.5",
                    "buy_amount": "1512.5",
                    "exchange_rate": "11",
                    "fee_amount": "0",
                },
            ]
            return {
                "name": route["name"],
                "path": path,
                "input_amount": "1000",
                "input_symbol": "USDC",
                "final_symbol": "USDC",
                "final_amount_units": "1512",
                "final_amount": "1512.5",
                "viable": True,
                "hops": hops,
            }
        return {
            "name": route["name"],
            "path": path,
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "final_amount_units": "0",
            "final_amount": "0",
            "viable": False,
            "hops": [],
        }

    monkeypatch.setattr("web.binance_market_service.evaluate_cow_route", fake_evaluate)
    monkeypatch.setattr("web.binance_market_service.rank_cow_routes", lambda items: items)
    monkeypatch.setattr("web.binance_market_service._cow_order_submission_adapter_available", lambda: False)

    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)

    plan = payload["ranking"][0]["binance_execution_plan"]
    buy_step, middle_step, sell_step = plan["steps"]
    assert buy_step["price_candidates"] == [
        {"source": "previous_window", "price": "9"},
        {"source": "current_window", "price": "10"},
        {"source": "query_quote", "price": "8"},
    ]
    assert buy_step["selected_target_source"] == "previous_window"
    assert buy_step["selected_target_price_usd_per_token"] == "9"
    assert buy_step["acceptable_slippage_price_usd_per_token"] == "10"
    assert buy_step["query_price_position"]["position"] == "lowest"
    assert buy_step["query_guard_analysis"]["status"] == "better_than_target"
    assert buy_step["cow_sdk_parameters"]["sell_amount_before_fee"] == "1000"
    assert buy_step["cow_sdk_parameters"]["target_buy_amount_after_fee"] == "111.1111111111111111111111111"
    assert buy_step["cow_sdk_parameters"]["min_buy_amount_after_fee"] == "100"

    assert middle_step["selected_target_source"] == "previous_window"
    assert middle_step["selected_target_exchange_rate"] == "0.9"
    assert middle_step["acceptable_slippage_exchange_rate"] == "0.9"
    assert middle_step["query_rate_position"]["position"] == "highest"
    assert middle_step["query_guard_analysis"]["status"] == "better_than_target"
    assert middle_step["cow_sdk_parameters"]["target_buy_amount_after_fee"] == "90"
    assert middle_step["cow_sdk_parameters"]["min_buy_amount_after_fee"] == "90"

    assert sell_step["selected_target_source"] == "current_window"
    assert sell_step["selected_target_price_usd_per_token"] == "12"
    assert sell_step["acceptable_slippage_price_usd_per_token"] == "12"
    assert sell_step["query_price_position"]["position"] == "middle"
    assert sell_step["query_guard_analysis"]["status"] == "worse_than_guard"
    assert sell_step["cow_sdk_parameters"]["target_buy_amount_after_fee"] == "1080"
    assert sell_step["cow_sdk_parameters"]["min_buy_amount_after_fee"] == "1080"
    precheck = payload["ranking"][0]["execution_precheck"]
    sdk_plan = precheck["cow_flashloan_sdk_plan"]
    assert sdk_plan["single_solver_order_count"] == 1
    assert sdk_plan["diagnostic_hop_count"] == 3
    assert precheck["status"] == "limit_order_ready_not_submitted"
    assert precheck["checks_passed"] is True
    assert precheck["can_submit_order"] is False
    assert precheck["price_guards_passed"] is True
    assert precheck["profit_positive"] is True
    assert precheck["flashloan_capability"]["multi_step_route"] is True
    assert precheck["flashloan_capability"]["supports_multi_step_atomic_settlement"] is True
    assert precheck["flashloan_capability"]["quote_probe_reliability"]["per_hop_quotes_are_not_atomicity_proof"] is True
    assert precheck["flashloan_capability"]["router_payload"]["loan_model"] == "single_flashloan_for_solver_settlement"
    assert precheck["flashloan_capability"]["router_payload"]["loan_count"] == 1
    assert precheck["flashloan_capability"]["router_payload"]["independent_per_hop_orders"] == 0
    assert precheck["flashloan_capability"]["router_payload"]["solver_intermediate_symbols"] == ["BBB", "AAA"]
    assert precheck["flashloan_capability"]["router_payload"]["closed_cycle"] is True
    assert precheck["flashloan_capability"]["router_payload"]["loans"][0]["token_symbol"] == "USDC"
    assert [item["status"] for item in precheck["hop_checks"]] == ["pass", "pass", "pass"]
    assert precheck["hop_checks"][0]["min_buy_amount_after_fee"] == "100"
    assert payload["opportunity_count"] == 1
    assert payload["best_opportunity"]["pair"] == "AAA / BBB"
    assert payload["best"]["execution_precheck"]["checks_passed"] is True


def test_cow_order_submission_enabled_when_page_switch_is_on(monkeypatch):
    monkeypatch.delenv("COW_ORDER_SUBMISSION_ENABLED", raising=False)
    monkeypatch.setattr(
        "web.control_panel_cow_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    assert _cow_order_submission_enabled() is True


def test_cow_order_submission_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.delenv("COW_ORDER_SUBMISSION_ENABLED", raising=False)
    monkeypatch.setenv("COW_ORDER_SUBMISSION_ADAPTER_ENABLED", "false")
    monkeypatch.setattr(
        "web.control_panel_cow_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    assert _cow_order_submission_enabled() is False


def test_cow_quote_unavailable_uses_quote_error_as_primary_blocker(monkeypatch, tmp_path):
    cloudfront_403 = """
    <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
    <HTML><HEAD><TITLE>ERROR: The request could not be satisfied</TITLE></HEAD>
    <BODY><H1>403 ERROR</H1><H2>The request could not be satisfied.</H2>
    Request blocked. Generated by cloudfront (CloudFront)</BODY></HTML>
    """
    market_state = {
        "observed_at": "2026-08-05T13:45:37+00:00",
        "window_seconds": 118.613,
        "price_source": "rest_interval",
        "pairs": [
            {
                "rank": 1,
                "pair": "SPCXBUSDT / SOXSBUSDT",
                "x_symbol": "SPCXBUSDT",
                "y_symbol": "SOXSBUSDT",
                "x_base_symbol": "SPCXB",
                "y_base_symbol": "SOXSB",
                "x_start_price": 110.9,
                "x_current_price": 112.77,
                "x_change_percent": 0,
                "y_start_price": 43.87,
                "y_current_price": 43.65,
                "y_change_percent": 0,
            }
        ],
    }
    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "SPCXB", "SOXSB"], start=1)
    }
    monkeypatch.setattr(
        "web.binance_market_service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "viable": False,
            "error": cloudfront_403,
            "hops": [],
        },
    )

    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)
    precheck = payload["ranking"][0]["execution_precheck"]

    assert precheck["status"] == "quote_unavailable"
    assert precheck["checks_passed"] is False
    assert precheck["can_submit_order"] is False
    assert precheck["quote_available"] is False
    assert precheck["own_limit_order_ready"] is True
    assert precheck["quote_error_type"] == "quote_api_http_403_cloudfront_request_blocked"
    assert "quote_error_type:quote_api_http_403_cloudfront_request_blocked" in precheck["reasons"]
    assert "cow_flashloan_sdk_intent_ready" in precheck["reasons"]
    assert not any("actual_output_below_own_minimum" in reason for reason in precheck["reasons"])
    assert set(item["failure_cause"] for item in precheck["hop_checks"]) == {"quote_api_http_403_cloudfront_request_blocked"}
    assert set(item["status"] for item in precheck["hop_checks"]) == {"not_checked"}

    attempts = build_cow_execution_attempts(payload, market_state=market_state)
    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, attempts) == 2
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)
    assert {row["priority_reason"] for row in rows} == {"buy_loser_then_gainer", "reverse_check"}
    summary = rows[0]["review_summary"]
    assert summary["cow_quote"]["error_type"] == "quote_api_http_403_cloudfront_request_blocked"
    assert summary["cow_quote"]["error"] == "CoW quote API HTTP 403: CloudFront request blocked"
    assert summary["market_prices"][0]["change_percent"] != 0
    assert summary["market_prices"][1]["change_percent"] != 0


def test_cow_execution_attempt_history_can_fallback_to_jsonl(tmp_path):
    payload = {
        "cow_network": "avalanche",
        "cow_chain_id": 43114,
        "owner": "0x" + "8" * 40,
        "ranking": [
            {
                "pair": "AAA / BBB",
                "pair_rank": 1,
                "priority_reason": "buy_loser_then_gainer",
                "path": ["USDC", "BBB", "AAA", "USDC"],
                "final_delta_amount": "1.2",
                "final_symbol": "USDC",
                "execution_precheck": {
                    "status": "checks_passed_order_disabled",
                    "checks_passed": True,
                    "can_submit_order": False,
                    "order_submission_enabled": False,
                    "auto_execute_requested": True,
                    "reasons": ["真实下单模块尚未开放"],
                },
            }
        ],
    }

    attempts = build_cow_execution_attempts(
        payload,
        market_state={"observed_at": "2026-08-05T00:00:00+00:00", "window_seconds": 3},
    )
    path = tmp_path / "cow_execution_attempts.jsonl"

    assert append_cow_execution_attempts_jsonl(path, attempts) == 1
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)
    assert rows[0]["network"] == "avalanche"
    assert rows[0]["state"] == "checks_passed_order_disabled"
    assert rows[0]["blocked_reasons"] == ["真实下单模块尚未开放"]


def test_cow_market_candidate_history_records_displayed_bnb_routes(tmp_path):
    market_state = {
        "observed_at": "2026-08-05T00:00:00+00:00",
        "window_seconds": 3,
        "price_source": "binance_ws",
        "cow_filter": {"network": "bnb", "chain_id": 56},
        "pairs": [
            {
                "rank": 1,
                "pair": "AAAUSDT / BBBUSDT",
                "x_symbol": "AAAUSDT",
                "y_symbol": "BBBUSDT",
                "x_base_symbol": "AAA",
                "y_base_symbol": "BBB",
                "x_change_percent": 2.5,
                "y_change_percent": -1.5,
                "x_start_price": 1.0,
                "x_current_price": 1.025,
                "y_start_price": 2.0,
                "y_current_price": 1.97,
                "window_spread_percent": 4.0,
                "quote_required": True,
                "candidate_basis": "binance_token_names_only",
                "blocked_reasons": ["requires_cow_or_dex_quote"],
                "route_results": [
                    {
                        "route_no": 1,
                        "route": ["USDC", "BBB", "AAA", "USDC"],
                        "initial_amount": "1000",
                        "initial_symbol": "USDC",
                        "priority_reason": "buy_loser_then_gainer",
                        "quote_required": True,
                        "binance_execution_plan": {
                            "available": True,
                            "initial_amount": "1000",
                            "initial_symbol": "USDC",
                            "final_target_amount": "1012",
                            "final_symbol": "USDC",
                            "profit_amount": "12",
                            "profit_percent": "1.2",
                            "steps": [],
                        },
                    },
                ],
            }
        ],
    }

    attempts = build_cow_market_candidate_attempts(market_state)

    assert len(attempts) == 1
    assert {attempt["network"] for attempt in attempts} == {"bnb"}
    assert {attempt["chain_id"] for attempt in attempts} == {56}
    assert {attempt["execution_phase"] for attempt in attempts} == {"market_candidate"}
    assert {attempt["state"] for attempt in attempts} == {"quote_required"}
    assert attempts[0]["precheck"]["reasons"] == ["requires_cow_or_dex_quote"]
    assert attempts[0]["quote"]["window_spread_percent"] == 4.0
    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, attempts) == 1
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10, networks=["bnb"])
    summary = rows[0]["review_summary"]
    assert summary["window_spread_percent"] == 4.0
    assert summary["market_prices"][0]["start_price"] == 1.0
    assert summary["market_prices"][1]["current_price"] == 1.97
    assert summary["plan"]["profit_amount"] == "12"
    assert summary["plan"]["profit_percent"] == "1.2"
    assert summary["profit_guard"]["status"] == "quote_required"


def test_cow_market_claim_history_records_displayed_chain_candidates():
    claims = [
        {
            "network": "bnb",
            "chain_id": 56,
            "min_spread_percent": 1.0,
            "threshold_detail": {"amount": "1000", "adjusted_min_spread_percent": "1.0"},
            "top": [
                {
                    "symbol": "AAAUSDT",
                    "base_symbol": "AAA",
                    "change_percent": 2.5,
                    "start_price": 1,
                    "current_price": 1.025,
                }
            ],
            "bottom": [
                {
                    "symbol": "BBBUSDT",
                    "base_symbol": "BBB",
                    "change_percent": -1.2,
                    "start_price": 2,
                    "current_price": 1.976,
                }
            ],
        }
    ]

    attempts = build_cow_market_claim_candidate_attempts(
        claims,
        observed_at="2026-08-05T00:00:00+00:00",
        price_source="binance_ws",
    )

    assert len(attempts) == 2
    assert {attempt["network"] for attempt in attempts} == {"bnb"}
    assert {attempt["pair"] for attempt in attempts} == {"AAAUSDT / BBBUSDT"}
    assert {attempt["execution_phase"] for attempt in attempts} == {"market_candidate"}
    assert {attempt["quote"]["candidate_basis"] for attempt in attempts} == {"cow_network_claim_top_bottom"}
    assert {tuple(attempt["route_path"]) for attempt in attempts} == {
        ("USDC", "BBB", "AAA", "USDC"),
        ("USDC", "AAA", "BBB", "USDC"),
    }


def test_cow_market_claim_pairs_can_feed_quote_verification(monkeypatch):
    claim = {
        "network": "bnb",
        "chain_id": 56,
        "min_spread_percent": 1.0,
        "threshold_detail": {"amount": "1000", "adjusted_min_spread_percent": "1.0"},
        "top": [
            {"symbol": "AAAUSDT", "base_symbol": "AAA", "change_percent": 2.5, "start_price": 1, "current_price": 1.025}
        ],
        "bottom": [
            {"symbol": "BBBUSDT", "base_symbol": "BBB", "change_percent": -1.2, "start_price": 2, "current_price": 1.976}
        ],
    }

    pairs = build_cow_market_claim_pairs(claim)
    market_state = {"observed_at": "2026-08-05T00:00:00+00:00", "pairs": pairs}
    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB"], start=1)
    }
    monkeypatch.setattr(
        "web.binance_market_service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "final_amount": "1001",
            "viable": True,
            "hops": [],
        },
    )
    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)

    assert len(pairs) == 1
    assert pairs[0]["pair"] == "AAAUSDT / BBBUSDT"
    assert payload["route_count"] == 2
    assert all(route["supported"] is True for route in payload["precheck"]["routes"])


def test_cow_market_claim_pairs_can_quote_displayed_rows_below_spread_floor():
    claim = {
        "network": "bnb",
        "chain_id": 56,
        "min_spread_percent": 1.0,
        "threshold_detail": {"amount": "1000", "adjusted_min_spread_percent": "1.0"},
        "top": [
            {"symbol": "AAAUSDT", "base_symbol": "AAA", "change_percent": 0.2, "start_price": 1, "current_price": 1.002}
        ],
        "bottom": [
            {"symbol": "BBBUSDT", "base_symbol": "BBB", "change_percent": -0.1, "start_price": 2, "current_price": 1.998}
        ],
    }

    strict_pairs = build_cow_market_claim_pairs(claim)
    quoted_display_pairs = build_cow_market_claim_pairs(
        claim,
        include_below_min_spread=True,
        max_pairs=1,
    )

    assert strict_pairs == []
    assert len(quoted_display_pairs) == 1
    assert quoted_display_pairs[0]["candidate_basis"] == "cow_network_claim_top_bottom_below_spread"
    assert quoted_display_pairs[0]["blocked_reasons"] == ["spread_below_dynamic_min", "requires_cow_or_dex_quote"]


def test_cow_market_candidate_jsonl_keeps_two_day_bucket_window_and_dedupes(tmp_path):
    path = tmp_path / "cow_execution_attempts.jsonl"
    old_created_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    recent_created_at = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    old_row = {
        "created_at": old_created_at,
        "observed_at": old_created_at,
        "network": "bnb",
        "pair": "OLD / ROW",
        "route_path": ["USDC", "OLD", "ROW", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
    }
    recent_polygon_row = {
        "created_at": recent_created_at,
        "observed_at": recent_created_at,
        "network": "polygon",
        "pair": "POL / ROW",
        "route_path": ["USDC", "POL", "ROW", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
    }
    path.write_text("\n".join(json.dumps(row) for row in [old_row, recent_polygon_row]) + "\n", encoding="utf-8")
    bnb_attempt = {
        "observed_at": "2026-08-05T00:00:00+00:00",
        "network": "bnb",
        "chain_id": 56,
        "pair": "AAA / BBB",
        "pair_rank": 1,
        "priority_reason": "buy_loser_then_gainer",
        "route_path": ["USDC", "BBB", "AAA", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
        "checks_passed": False,
        "can_submit_order": False,
        "blocked_reasons": ["requires_cow_or_dex_quote"],
    }
    polygon_attempt = {**bnb_attempt, "network": "polygon", "pair": "POL / ROW"}

    assert append_cow_execution_attempts_jsonl(path, [bnb_attempt]) == 1
    assert append_cow_execution_attempts_jsonl(path, [bnb_attempt]) == 0
    assert append_cow_execution_attempts_jsonl(path, [polygon_attempt], dedupe_market_candidates=False) == 1

    bnb_rows = load_recent_cow_execution_attempts_jsonl(path, limit=10, networks=["bnb"])
    per_network_rows = load_recent_cow_execution_attempts_jsonl(path, limit=1, networks=["bnb", "polygon"])
    all_rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)

    assert [row["pair"] for row in bnb_rows] == ["AAA / BBB"]
    assert {row["network"] for row in per_network_rows} == {"bnb", "polygon"}
    assert {row["pair"] for row in all_rows} == {"AAA / BBB", "POL / ROW"}


def test_cow_execution_attempt_jsonl_uses_category_retention(tmp_path):
    path = tmp_path / "cow_execution_attempts.jsonl"
    old_not_executable = {
        "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        "network": "bnb",
        "pair": "OLD / BLOCKED",
        "route_path": ["USDC", "OLD", "BLOCKED", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
        "checks_passed": False,
        "can_submit_order": False,
    }
    recent_failed = {
        "created_at": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),
        "network": "bnb",
        "pair": "RECENT / FAILED",
        "route_path": ["USDC", "RECENT", "FAILED", "USDC"],
        "execution_phase": "execution",
        "state": "execution_failed",
        "checks_passed": True,
        "can_submit_order": True,
    }
    old_failed = {
        **recent_failed,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
        "pair": "OLD / FAILED",
    }
    old_success = {
        "created_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "network": "bnb",
        "pair": "OLD / SUCCESS",
        "route_path": ["USDC", "OLD", "SUCCESS", "USDC"],
        "execution_phase": "settled",
        "state": "settled",
        "checks_passed": True,
        "can_submit_order": True,
    }
    path.write_text(
        "\n".join(json.dumps(row) for row in [old_not_executable, recent_failed, old_failed, old_success]) + "\n",
        encoding="utf-8",
    )

    assert append_cow_execution_attempts_jsonl(path, []) == 0
    assert append_cow_execution_attempts_jsonl(path, [{"network": "bnb", "pair": "NEW / BLOCKED", "execution_phase": "market_candidate", "state": "quote_required"}]) == 1

    not_executable = load_recent_cow_execution_attempts_jsonl(path, limit=10, category=COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE)
    failed = load_recent_cow_execution_attempts_jsonl(path, limit=10, category=COW_ATTEMPT_CATEGORY_EXECUTION_FAILED)
    success = load_recent_cow_execution_attempts_jsonl(path, limit=10, category=COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS)
    all_pairs = {row["pair"] for row in load_recent_cow_execution_attempts_jsonl(path, limit=20)}

    assert [row["pair"] for row in not_executable] == ["NEW / BLOCKED"]
    assert [row["pair"] for row in failed] == ["RECENT / FAILED"]
    assert [row["pair"] for row in success] == ["OLD / SUCCESS"]
    assert all_pairs == {"NEW / BLOCKED", "RECENT / FAILED", "OLD / SUCCESS"}
    assert not_executable[0]["review_category"] == COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    assert failed[0]["review_category"] == COW_ATTEMPT_CATEGORY_EXECUTION_FAILED
    assert success[0]["review_category"] == COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS


def test_cow_not_executable_retention_uses_two_local_day_buckets():
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 5, 0, 1, tzinfo=local_tz)
    previous_day_start = {
        "created_at": datetime(2026, 8, 4, 0, 0, tzinfo=local_tz).isoformat(),
        "execution_phase": "market_candidate",
        "state": "quote_required",
    }
    before_previous_day = {
        **previous_day_start,
        "created_at": datetime(2026, 8, 3, 23, 59, 59, tzinfo=local_tz).isoformat(),
    }

    assert _category_within_retention(previous_day_start, now=now) is True
    assert _category_within_retention(before_previous_day, now=now) is False


def test_cow_failed_retention_uses_two_local_week_buckets():
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 10, 0, 1, tzinfo=local_tz)
    previous_week_start = {
        "created_at": datetime(2026, 8, 3, 0, 0, tzinfo=local_tz).isoformat(),
        "execution_phase": "execution",
        "state": "execution_failed",
        "checks_passed": True,
        "can_submit_order": True,
    }
    before_previous_week = {
        **previous_week_start,
        "created_at": datetime(2026, 8, 2, 23, 59, 59, tzinfo=local_tz).isoformat(),
    }

    assert _category_within_retention(previous_week_start, now=now) is True
    assert _category_within_retention(before_previous_week, now=now) is False


def test_cow_network_options_exposes_supported_networks():
    from web.binance_market_service import cow_network_options

    options = cow_network_options()

    assert options["default_network"] == "avalanche"
    assert {item["network"] for item in options["networks"]} == {
        "ethereum",
        "gnosis",
        "arbitrum_one",
        "base",
        "polygon",
        "avalanche",
        "bnb",
        "linea",
        "plasma",
        "ink",
        "sepolia",
    }
    assert {item["label"] for item in options["networks"]} >= {"BNB Chain", "Arbitrum One"}


def test_cow_supported_token_cache_refreshes_file_and_loads_registry(tmp_path, monkeypatch):
    cache_path = tmp_path / "cow_tokens.json"
    monkeypatch.setattr(
        "web.binance_market_service.load_cow_token_list",
        lambda *args, **kwargs: [
            CowToken("USDC", "0x" + "1" * 40, 6, "test"),
            CowToken("WETH", "0x" + "2" * 40, 18, "test"),
        ],
    )

    refreshed = refresh_cow_supported_token_cache(cow_network="sepolia", cache_path=cache_path)
    loaded = load_cow_supported_token_registry(
        cow_network="sepolia",
        cache_path=cache_path,
        allow_live_fallback=False,
    )

    assert refreshed["token_count"] == 2
    assert loaded["source"] == "memory"
    assert loaded["token_count"] == 2
    assert set(loaded["registry"]) >= {"USDC", "WETH"}


def test_cow_supported_token_cache_dedupes_duplicate_addresses(tmp_path, monkeypatch):
    cache_path = tmp_path / "cow_tokens.json"
    monkeypatch.setattr(
        "web.binance_market_service.load_cow_token_list",
        lambda *args, **kwargs: [
            CowToken("AAA", "0x" + "1" * 40, 18, "test"),
            CowToken("AAA2", "0x" + "1" * 40, 18, "test"),
            CowToken("BBB", "0x" + "2" * 40, 18, "test"),
        ],
    )

    refreshed = refresh_cow_supported_token_cache(cow_network="bnb", cache_path=cache_path)
    loaded = load_cow_supported_token_registry(
        cow_network="bnb",
        cache_path=cache_path,
        allow_live_fallback=False,
    )

    assert refreshed["token_count"] == 2
    assert loaded["token_count"] == 2
    assert {token["address"] for token in loaded["tokens"]} == {"0x" + "1" * 40, "0x" + "2" * 40}


def test_cow_quote_verification_marks_requested_owner_source(monkeypatch):
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"},
        ],
    }

    monkeypatch.setattr(
        "web.binance_market_service.build_token_registry",
        lambda *args, **kwargs: {"AAA": object(), "BBB": object()},
    )
    monkeypatch.setattr(
        "web.binance_market_service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": route["path"][0],
            "final_symbol": route["path"][-1],
            "final_amount_units": "1200",
            "final_amount": "1200",
            "viable": True,
            "hops": [],
        },
    )
    monkeypatch.setattr(
        "web.binance_market_service.rank_cow_routes",
        lambda items: items,
    )

    payload = build_cow_quote_verification(market_state, owner="0x" + "7" * 40)

    assert payload["owner_source"] == "request.owner"


def test_cow_quote_verification_keeps_failed_unknown_token_routes():
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {
                "rank": 1,
                "pair": "PYRUSDT / ICXUSDT",
                "x_base_symbol": "PYR",
                "y_base_symbol": "ICX",
                "x_start_price": 0.062,
                "x_current_price": 0.063,
                "y_start_price": 0.0216,
                "y_current_price": 0.0215,
            },
        ],
    }

    payload = build_cow_quote_verification(
        market_state,
        quote_limit=1,
        registry={},
    )

    assert payload["route_count"] == 2
    assert payload["viable_count"] == 0
    assert len(payload["ranking"]) == 2
    assert {item["viable"] for item in payload["ranking"]} == {False}
    assert all("unsupported CoW tokens on avalanche" in item["error"] for item in payload["ranking"])
    assert all(item["cow_support"]["supported"] is False for item in payload["ranking"])


def test_cow_route_precheck_marks_unsupported_tokens_before_quote():
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {
                "rank": 1,
                "pair": "PYRUSDT / ICXUSDT",
                "x_base_symbol": "PYR",
                "y_base_symbol": "ICX",
                "x_start_price": 0.062,
                "x_current_price": 0.063,
                "y_start_price": 0.0216,
                "y_current_price": 0.0215,
            },
        ],
    }

    payload = build_cow_route_precheck(
        market_state,
        quote_limit=1,
        registry={},
    )

    assert payload["route_count"] == 2
    assert payload["supported_route_count"] == 0
    assert payload["unsupported_route_count"] == 2
    assert {route["status"] for route in payload["routes"]} == {"unsupported_tokens"}
    assert {tuple(route["unsupported_tokens"]) for route in payload["routes"]} == {
        ("USDC", "ICX", "PYR"),
        ("USDC", "PYR", "ICX"),
    }
