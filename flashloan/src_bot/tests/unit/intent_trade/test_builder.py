import inspect
import json
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

from intent_trade import (
    bind_cow_intent_context,
    build_cow_intent_trade,
    build_triangular_onchain_intent_trade,
    pair_index_from_tokenx_tokeny,
    set_usdc_pair_memory_table,
    submit_cow_intent_trade,
)
from intent_trade import direct
from intent_trade import direct_utils
from intent_trade import live_evidence
from intent_trade.unified_live_signal_schema import (
    UNIFIED_LIVE_SIGNAL_REQUIRED_FIELDS,
    UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
    UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
    unified_live_signal_schema,
)


def _addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def _clear_direct_pair_env(monkeypatch):
    for name in (
        "TRIANGULAR_USDC_PAIR_ID",
        "TRIANGULAR_PAIR_ID",
        "TRIANGULAR_USDC_PAIRS_JSON",
        "TRIANGULAR_TOKEN_X",
        "TRIANGULAR_TOKEN_Y",
        "TRIANGULAR_DEX_ROUTER",
        "DEX_ROUTER_ADDRESS",
        "FUJI_DEX_ROUTER",
        "TRIANGULAR_RUNTIME_TRADES_JSON",
        "UNIFIED_EXECUTOR_ADDRESS",
        "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS",
        "UNIFIED_EXECUTOR_ARTIFACT",
        "TRIANGULAR_DIRECT_USE_DYNAMIC_CANDIDATES",
        "TRIANGULAR_DIRECT_CANDIDATE_STRATEGY",
        "TRIANGULAR_ENABLE_NON_USDC_CROSS_POOL",
        "TRIANGULAR_DIRECT_ENABLE_TOKEN_X_CROSS_POOL",
        "TRIANGULAR_MIN_PROFIT_USDC",
        "TRIANGULAR_MIN_PROFIT_USDC_BASE_UNITS",
        "TRIANGULAR_MIN_NET_PROFIT_USDC",
        "TRIANGULAR_MIN_NET_PROFIT_USDC_BASE_UNITS",
        "TRIANGULAR_EXECUTION_MIN_PROFIT_USDC",
        "TRIANGULAR_USDC_ADDRESS",
        "USDC_ADDRESS",
        "TRIANGULAR_DIRECT_NETWORK",
        "TRIANGULAR_ONCHAIN_NETWORK",
        "TRIANGULAR_TESTNET_NAME",
        "TRIANGULAR_NETWORK",
        "TRIANGULAR_TESTNET_CHAIN_ID",
        "AAVE_RESERVE_CACHE_FILE",
        "AVAX_USDC_PRICE",
        "GAS_TOKEN_USDC_PRICE",
        "AVAX_USDC_PRICE_UPDATED_AT",
        "GAS_TOKEN_USDC_PRICE_UPDATED_AT",
        "AVAX_USDC_PRICE_MAX_AGE_SECONDS",
        "GAS_TOKEN_USDC_PRICE_MAX_AGE_SECONDS",
        "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_USDC",
        "TRIANGULAR_CACHE_RISK_PENALTY_USDC",
        "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        "TRIANGULAR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        "UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE",
        "TRIANGULAR_TOKEN_REGISTRY_FILE",
        "DIRECT_ONCHAIN_PRE_PAUSE_FILE",
        "UNIFIED_EXECUTOR_PRE_PAUSE_FILE",
        "UNIFIED_EXECUTOR_PRICE_SOURCES_JSON",
        "UNIFIED_EXECUTOR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "TRIANGULAR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "LIQUIDATION_PRIVATE_RELAY_RESEARCH_ENABLED",
        "UNIFIED_EXECUTOR_ALLOW_PUBLIC_FALLBACK",
        "TRIANGULAR_ALLOW_PUBLIC_FALLBACK",
        "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
        "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
        "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
        "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])
    monkeypatch.setattr(direct, "_UNIFIED_EXECUTOR_ABI_CACHE", None)


def _runtime_trades_json(token_x: str | None = None, token_y: str | None = None, pool: str | None = None) -> str:
    return json.dumps([
        {
            "tradeIndex": 0,
            "tokenX": token_x or _addr(101),
            "tokenY": token_y or _addr(102),
            "pools": [
                {"adapterKind": 1, "pool": pool or _addr(103)},
                {"adapterKind": 1, "pool": _addr(104)},
            ],
        }
    ])


def _unified_runtime_trades_json(
    usdc: str | None = None,
    token_x: str | None = None,
    token_y: str | None = None,
) -> str:
    usdc = usdc or _addr(500)
    token_x = token_x or _addr(501)
    token_y = token_y or _addr(502)
    return json.dumps([
        {
            "tradeIndex": 0,
            "tokenX": usdc,
            "tokenY": token_x,
            "strategyStatus": 1,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(601)}, {"adapterKind": 1, "pool": _addr(602)}],
        },
        {
            "tradeIndex": 1,
            "tokenX": usdc,
            "tokenY": token_y,
            "strategyStatus": 2,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(603)}, {"adapterKind": 1, "pool": _addr(604)}],
        },
        {
            "tradeIndex": 2,
            "tokenX": token_x,
            "tokenY": token_y,
            "strategyStatus": 3,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(605)}, {"adapterKind": 1, "pool": _addr(606)}],
        },
        {
            "tradeIndex": 3,
            "tokenX": token_y,
            "tokenY": token_x,
            "strategyStatus": 5,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(607)}, {"adapterKind": 1, "pool": _addr(608)}],
        },
    ])


def _live_runtime_trade(
    token_x: str | None = None,
    token_y: str | None = None,
    pool: str | None = None,
) -> dict:
    return {
        "tradeIndex": 0,
        "tokenX": token_x or _addr(500),
        "tokenY": token_y or _addr(501),
        "pools": [{"adapterKind": 1, "pool": pool or _addr(601)}],
    }


def _live_signal_result(
    *,
    runtime_trades: list[dict] | None = None,
    status: str = "static_call_passed",
    submitted: bool = False,
    request_overrides: dict | None = None,
) -> dict:
    request = {
        "runtimeTrades": runtime_trades if runtime_trades is not None else [_live_runtime_trade()],
        "deliveryPolicy": {
            "mode": "public_rpc_direct_after_fresh_gates",
            "privateRelayResearchEnabled": False,
            "privateFirst": False,
            "publicFallbackRequested": False,
            "publicFallbackRequiresRevalidation": False,
            "privacyBoundary": "deferred_to_cow_or_intent_layer",
        },
        "netProfitModel": {
            "enabled": "false",
            "relayCostWei": "0",
            "deliveryCostWei": "0",
            "publicMempoolRiskPenaltyUsdc": "0",
            "publicMempoolRiskPenaltyBps": "0",
        },
        "routeEvaluations": [
            {
                "netProfit": {
                    "relayCostWei": "0",
                    "relayCostUsdc": "0",
                    "deliveryCostWei": "0",
                    "deliveryCostUsdc": "0",
                    "publicMempoolRiskPenaltyUsdc": "0",
                    "publicMempoolRiskPenaltyFixedUsdc": "0",
                    "publicMempoolRiskPenaltyBps": "0",
                    "publicMempoolRiskPenaltyBpsUsdc": "0",
                }
            }
        ],
    }
    if request_overrides:
        request.update(request_overrides)
    return {
        "ok": status in {"static_call_passed", "submitted_success"},
        "submitted": submitted,
        "status": status,
        "request": request,
    }


def _market_row(symbol: str, change: float) -> dict:
    return {
        "symbol": f"{symbol}USDT",
        "base_symbol": symbol,
        "change_percent": change,
        "current_price": 1.0,
        "window_ready": True,
    }


def _pool_cache_for_symbols(symbols: list[str]) -> dict:
    token_by_symbol = {symbol: _addr(500 + index) for index, symbol in enumerate(symbols)}
    pools = []
    pool_id = 700
    for x_symbol in symbols:
        for y_symbol in symbols:
            if x_symbol == y_symbol:
                continue
            pools.append(
                {
                    "tokenX_symbol": x_symbol,
                    "tokenY_symbol": y_symbol,
                    "tokenX": token_by_symbol[x_symbol],
                    "tokenY": token_by_symbol[y_symbol],
                    "pools": [
                        {"adapterKind": 1, "pool": _addr(pool_id)},
                        {"adapterKind": 1, "pool": _addr(pool_id + 1)},
                        {"adapterKind": 2, "pool": _addr(pool_id + 2)},
                    ],
                }
            )
            pool_id += 3
    return {"network": "avalanche", "protocols": ["uniswap_v3"], "pools": pools}


def _pool_cache_with_metadata(symbols: list[str], *, chain_id: int = 43114, block_number: int = 100) -> dict:
    cache = _pool_cache_for_symbols(symbols)
    cache["chain_id"] = chain_id
    cache["block_number"] = block_number
    return cache


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


def test_unified_live_signal_schema_exports_required_evidence_fields():
    schema = unified_live_signal_schema()

    assert schema["properties"]["schemaVersion"]["const"] == UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION
    assert schema["properties"]["schemaConstantVersion"]["const"] == UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION
    assert set(UNIFIED_LIVE_SIGNAL_REQUIRED_FIELDS).issubset(set(schema["required"]))
    assert "runtimeTradesHash" in schema["required"]
    assert "evidenceHash" in schema["required"]
    assert "marketFeasibility" in schema["required"]
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
    monkeypatch.setenv("UNIFIED_EXECUTOR_ADDRESS", "0x" + "2" * 40)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "0x" + "5" * 40)
    monkeypatch.setenv("TRIANGULAR_RUNTIME_TRADES_JSON", _unified_runtime_trades_json())

    intent = build_triangular_onchain_intent_trade("USDC->BBB->AAA->USDC", "6.18", ["AAA"], ["BBB"])

    protocol = intent["direct_onchain_protocol"]
    assert protocol["kind"] == "unified_flashloan_mev_executor_runtime_v1"
    assert protocol["execution_mode"] == "ordered_auto"
    assert protocol["unified_executor_address"] == "0x" + "2" * 40
    assert protocol["executor_address"] == "0x" + "2" * 40
    assert protocol["runtime_trades"][0]["tradeIndex"] == 0
    assert protocol["runtime_trades"][0]["pools"][0]["adapterKind"] == 1
    assert protocol["runtime_trade_limit"] == 5
    assert protocol["runtime_candidate_limit"] == 25
    assert protocol["candidate_strategy"] == "expanded"
    assert protocol["selection_strategy"] == "all_usdc_route_groups_preview_then_best"
    assert intent["direct_onchain_ready"] is True
    assert intent["intent_protocol"] == "direct_onchain"
    assert intent["submission_protocol"] == "direct_onchain"


def test_direct_onchain_network_can_target_fuji_without_cow_network_config(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_TESTNET_NAME", "fuji")

    network, chain_id, testnet = direct._resolve_direct_onchain_network({})

    assert network == "fuji"
    assert chain_id == 43113
    assert testnet is True


def test_direct_onchain_rpc_prefers_fuji_rpc_for_fuji(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("AVALANCHE_RPC_URL", "https://mainnet.example")
    monkeypatch.setenv("FUJI_RPC_URL", "https://fuji.example")

    rpc_url, rpc_env_names = direct._direct_rpc_url("fuji")

    assert rpc_url == "https://fuji.example"
    assert rpc_env_names == ("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL")


def test_build_triangular_onchain_intent_trade_uses_signal_and_pool_cache(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("UNIFIED_EXECUTOR_ADDRESS", _addr(9))
    cache_path = tmp_path / "avalanche_v3_pools.json"
    cache_path.write_text(json.dumps(_pool_cache_for_symbols(["USDC", "AAA", "BBB"])), encoding="utf-8")
    monkeypatch.setenv("PINAX_POOL_DISCOVERY_CACHE_FILE", str(cache_path))

    intent = build_triangular_onchain_intent_trade("USDC->BBB->AAA->USDC", "6.18", ["AAA"], ["BBB"])

    protocol = intent["direct_onchain_protocol"]
    assert protocol["runtime_trades"]
    assert [trade["strategyStatus"] for trade in protocol["runtime_trades"]] == [1, 2, 3, 5]
    assert len(protocol["runtime_trades"][0]["pools"]) == 5
    assert intent["direct_onchain_ready"] is True


def test_build_triangular_onchain_intent_trade_prefers_requested_route_in_large_cache(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("UNIFIED_EXECUTOR_ADDRESS", _addr(9))
    cache_path = tmp_path / "avalanche_v3_pools.json"
    cache_path.write_text(json.dumps(_pool_cache_for_symbols(["USDC", "AAA", "BBB", "CCC"])), encoding="utf-8")
    monkeypatch.setenv("PINAX_POOL_DISCOVERY_CACHE_FILE", str(cache_path))

    intent = build_triangular_onchain_intent_trade("USDC->CCC->AAA->USDC", "6.18", ["AAA"], ["CCC"])

    protocol = intent["direct_onchain_protocol"]
    assert protocol["intent_route_match"]["matched"] is True
    assert protocol["intent_route_match"]["routeKey"] == "AAA:CCC"
    assert protocol["intent_route_match"]["routePath"] == ["USDC", "CCC", "AAA", "USDC"]
    assert {trade["routeKey"] for trade in protocol["runtime_trades"]} == {"AAA:CCC"}
    assert protocol["runtime_trades"][3]["routeSymbols"] == ["USDC", "CCC", "AAA", "USDC"]


def test_build_triangular_onchain_intent_trade_requires_runtime_trades(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("UNIFIED_EXECUTOR_ADDRESS", "0x" + "2" * 40)
    cache_path = tmp_path / "empty_pools.json"
    cache_path.write_text(json.dumps({"chain_id": 43114, "pools": []}), encoding="utf-8")
    monkeypatch.setenv("PINAX_POOL_DISCOVERY_CACHE_FILE", str(cache_path))

    intent = build_triangular_onchain_intent_trade("USDC", "6.18", [], [])

    protocol = intent["direct_onchain_protocol"]
    assert "pair_id" not in protocol
    assert protocol["runtime_trades"] == []
    assert intent["direct_onchain_ready"] is False


def test_pair_index_from_tokenx_tokeny_scans_env_pair_table(monkeypatch):
    x0 = _addr(1)
    y0 = _addr(2)
    x1 = _addr(3)
    y1 = _addr(4)
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv(
        "TRIANGULAR_USDC_PAIRS_JSON",
        json.dumps([
            {"tokenX": x0, "tokenY": y0, "router": _addr(5)},
            {"tokenX": x1, "tokenY": y1, "router": _addr(6)},
        ]),
    )

    assert pair_index_from_tokenx_tokeny(x1.upper().replace("0X", "0x"), y1) == 1
    assert pair_index_from_tokenx_tokeny(y1, x1) == 1


def test_pair_index_from_tokenx_tokeny_scans_memory_table_before_env(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    env_x = _addr(1)
    env_y = _addr(2)
    memory_x = _addr(7)
    memory_y = _addr(8)
    monkeypatch.setenv("TRIANGULAR_USDC_PAIRS_JSON", json.dumps([{"tokenX": env_x, "tokenY": env_y}]))
    set_usdc_pair_memory_table([
        {"tokenX": _addr(3), "tokenY": _addr(4)},
        {"tokenX": memory_x, "tokenY": memory_y},
    ])

    assert pair_index_from_tokenx_tokeny(memory_x, memory_y) == 1
    assert pair_index_from_tokenx_tokeny(env_x, env_y) is None
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])


def test_pair_index_from_tokenx_tokeny_uses_router_to_disambiguate_duplicate_pairs(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    token_x = _addr(10)
    token_y = _addr(11)
    router_a = _addr(12)
    router_b = _addr(13)
    set_usdc_pair_memory_table([
        {"tokenX": token_x, "tokenY": token_y, "router": router_a},
        {"tokenX": token_x, "tokenY": token_y, "router": router_b},
    ])

    assert pair_index_from_tokenx_tokeny(token_x, token_y) == 0
    assert pair_index_from_tokenx_tokeny(token_x, token_y, router=router_b) == 1
    assert pair_index_from_tokenx_tokeny(token_x, token_y, router=_addr(14)) is None
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])


def test_pair_index_from_tokenx_tokeny_tracks_swap_and_pop_memory_refresh(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    removed_x = _addr(21)
    removed_y = _addr(22)
    moved_x = _addr(23)
    moved_y = _addr(24)
    set_usdc_pair_memory_table([
        {"tokenX": _addr(19), "tokenY": _addr(20)},
        {"tokenX": removed_x, "tokenY": removed_y},
        {"tokenX": moved_x, "tokenY": moved_y},
    ])
    assert pair_index_from_tokenx_tokeny(moved_x, moved_y) == 2

    set_usdc_pair_memory_table([
        {"tokenX": _addr(19), "tokenY": _addr(20)},
        {"tokenX": moved_x, "tokenY": moved_y},
    ])

    assert pair_index_from_tokenx_tokeny(moved_x, moved_y) == 1
    assert pair_index_from_tokenx_tokeny(removed_x, removed_y) is None
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])


def test_build_triangular_onchain_intent_trade_uses_runtime_env_not_pair_table(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    x0 = _addr(1)
    y0 = _addr(2)
    x1 = _addr(3)
    y1 = _addr(4)
    monkeypatch.setenv("UNIFIED_EXECUTOR_ADDRESS", _addr(9))
    monkeypatch.setenv("TRIANGULAR_RUNTIME_TRADES_JSON", _unified_runtime_trades_json(_addr(500), x1, y1))
    monkeypatch.setenv(
        "TRIANGULAR_USDC_PAIRS_JSON",
        json.dumps([
            {"tokenX": x0, "tokenY": y0},
            {"tokenX": x1, "tokenY": y1},
        ]),
    )

    intent = build_triangular_onchain_intent_trade(f"USDC->{x1}->{y1}->USDC", "6.18", [], [])

    protocol = intent["direct_onchain_protocol"]
    assert "pair_id" not in protocol
    assert protocol["runtime_trades"][2]["tokenX"] == x1
    assert protocol["runtime_trades"][2]["tokenY"] == y1
    assert intent["direct_onchain_ready"] is True


def test_submit_direct_onchain_trade_accepts_runtime_trades_before_network_setup(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", _addr(500))
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    token_x = _addr(31)
    token_y = _addr(32)

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": True,
                    "network": "avalanche",
                    "unified_executor_address": _addr(33),
                    "runtime_trades": json.loads(_unified_runtime_trades_json(_addr(500), token_x, token_y)),
                }
            }
        },
        opportunity={"tokenX": token_x, "tokenY": token_y},
    )

    assert result["status"] == "network_config_missing"
    assert result["blocked_reason"] == "network_config_missing"


def test_runtime_candidate_pairs_use_default_router(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    router = _addr(55)
    pairs = direct._runtime_candidate_pairs(
        {"router": router},
        {"candidate_pairs": [{"tokenX": _addr(51), "tokenY": _addr(52)}]},
    )

    assert pairs == [{"tokenX": _addr(51), "tokenY": _addr(52), "router": router}]


def test_submit_direct_onchain_trade_accepts_opportunity_runtime_trades_before_network_setup(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", _addr(500))
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL"):
        monkeypatch.delenv(name, raising=False)

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": True,
                    "network": "avalanche",
                    "unified_executor_address": _addr(61),
                }
            }
        },
        opportunity={"runtime_trades": json.loads(_unified_runtime_trades_json(_addr(500), _addr(62), _addr(63)))},
    )

    assert result["status"] == "network_config_missing"
    assert result["blocked_reason"] == "network_config_missing"


def test_submit_direct_onchain_trade_runs_unified_preview_and_static_call_before_broadcast(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    usdc = _addr(500)
    token_x = _addr(501)
    token_y = _addr(502)
    signer = _addr(700)
    executor_address = _addr(701)

    class FakeCall:
        def __init__(self, value):
            self.value = value

        def call(self, *_args, **_kwargs):
            return self.value

    class FakeRunCall(FakeCall):
        def estimate_gas(self, *_args, **_kwargs):
            return 321_000

    hop = (1, usdc, token_x, _addr(601), 500, 1_000_000, 1_001_000, 0)
    preview_result = (
        True,
        4,
        1,
        2,
        (True, 2, token_x, token_y, _addr(605), _addr(606), 500, 3000, 1, 1, -100, 250, 350, 2, 0),
        (usdc, usdc, 1_000_000, 1, 0, b"\x01", 1_003_003, 500, 1_000_501, 1_000_501, 1, 2_502),
        (1, 0, 1, (hop, hop, hop), 1_003_003, 500, 1_000_501, 2_502),
        (
            1104,
            4,
            4,
            0b01111,
            0b01000,
            0b10000,
            1,
            tuple((index + 1, 1, 0, 0, 0, 0, index, index, usdc, 0, 0, 0) for index in range(5)),
        ),
    )
    static_result = (1204, 4, 1, 1, 2, usdc, 2_503, 2_503, 0b01111, 0b10000, 1)

    class FakeFunctions:
        def owner(self):
            return FakeCall(signer)

        def previewOrderedRuntimeAutoExecution(self, *args):
            self.preview_args = args
            return FakeCall(preview_result)

        def runOrderedRuntimeTradesAndExecuteAuto(self, *args):
            self.run_args = args
            return FakeRunCall(static_result)

    class FakeContract:
        def __init__(self):
            self.functions = FakeFunctions()

    contract = FakeContract()

    class FakeEth:
        chain_id = 43113
        block_number = 100

        def contract(self, *, address, abi):
            self.address = address
            self.abi = abi
            return contract

    class FakeWeb3:
        def __init__(self, _provider):
            self.eth = FakeEth()

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return (url, request_kwargs)

        @staticmethod
        def to_checksum_address(value):
            return value.lower()

    class FakeAccount:
        @staticmethod
        def from_key(_key):
            return SimpleNamespace(address=signer)

    web3_module = ModuleType("web3")
    web3_module.Web3 = FakeWeb3
    account_module = ModuleType("eth_account")
    account_module.Account = FakeAccount
    monkeypatch.setitem(sys.modules, "web3", web3_module)
    monkeypatch.setitem(sys.modules, "eth_account", account_module)
    monkeypatch.setenv("FUJI_RPC_URL", "https://api.avax-test.network/ext/bc/C/rpc")
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", usdc)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("UNIFIED_EXECUTOR_BROADCAST_ENABLED", "false")
    reserve_cache = tmp_path / "aave_reserve_assets.json"
    reserve_cache.write_text(
        json.dumps(
            {
                "chain_id": 43113,
                "rpc_url": "https://api.avax-test.network/ext/bc/C/rpc",
                "block_number": 100,
                "assets": [
                    {
                        "token_address": usdc,
                        "active": True,
                        "paused": False,
                        "borrowing_enabled": True,
                        "available_liquidity": 10_000_000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AAVE_RESERVE_CACHE_FILE", str(reserve_cache))
    pool_cache = tmp_path / "avalanche_v3_pools.json"
    pool_cache.write_text(
        json.dumps(_pool_cache_with_metadata(["USDC", "AAA", "BBB"], chain_id=43113, block_number=100)),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRIANGULAR_RUNTIME_POOL_CACHE_FILE", str(pool_cache))

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": True,
                        "network": "fuji",
                        "unified_executor_address": executor_address,
                        "runtime_trades": json.loads(_unified_runtime_trades_json(usdc, token_x, token_y)),
                        "tokenRisks": [
                            {"token": usdc, "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                            {"token": token_x, "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                            {"token": token_y, "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                        ],
                        "execute_runtime_trade": True,
                    "execution_amount": 1_000_000,
                    "amountOutMinUsdc": 1_000_501,
                        "minProfitUsdc": 1,
                        "priceSourcePolicy": "multi-source-median",
                        "priceSources": [
                            {"id": "source-a", "priceUsdc": "1", "updatedAt": "2026-08-12T00:00:00Z"},
                            {"id": "source-b", "priceUsdc": "1", "updatedAt": "2026-08-12T00:00:01Z"},
                        ],
                    }
                }
            },
        opportunity={"tokenX": token_x, "tokenY": token_y},
    )

    assert result["status"] == "static_call_passed"
    assert result["blocked_reason"] == "broadcast_disabled"
    assert result["preflight"]["strategyStatus"] == "4"
    assert result["route_direction"] == "1"
    assert result["static_call"]["gasEstimate"] == "321000"
    assert result["static_call"]["runResult"]["profitSwept"] == "2503"
    assert result["request"]["deliveryPolicy"]["mode"] == "public_rpc_direct_after_fresh_gates"
    assert result["request"]["deliveryPolicy"]["privateFirst"] is False
    assert result["request"]["deliveryPolicy"]["privacyBoundary"] == "deferred_to_cow_or_intent_layer"
    assert contract.functions.preview_args[3] is False
    assert contract.functions.run_args[3] is False

    monkeypatch.setenv("TRIANGULAR_DIRECT_BROADCAST_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_EXECUTOR_BROADCAST_ENABLED", "true")
    monkeypatch.setenv("TRIANGULAR_MAX_GAS_PRICE_WEI", "1")
    monkeypatch.setenv("AVAX_USDC_PRICE", "1")
    monkeypatch.setenv("AVAX_USDC_PRICE_UPDATED_AT", "2026-08-12T00:00:00Z")
    monkeypatch.setenv("AVAX_USDC_PRICE_MAX_AGE_SECONDS", "999999999")
    monkeypatch.setattr(
        direct,
        "estimate_gas_price",
        lambda *_args, **_kwargs: SimpleNamespace(max_fee=2, strategy="normal"),
    )
    blocked = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": True,
                        "network": "fuji",
                        "unified_executor_address": executor_address,
                        "runtime_trades": json.loads(_unified_runtime_trades_json(usdc, token_x, token_y)),
                        "tokenRisks": [
                            {"token": usdc, "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                            {"token": token_x, "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                            {"token": token_y, "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                        ],
                        "execute_runtime_trade": True,
                    "execution_amount": 1_000_000,
                    "amountOutMinUsdc": 1_000_501,
                    "minProfitUsdc": 1,
                    "priceSourcePolicy": "multi-source-median",
                    "priceSources": [
                        {"id": "source-a", "priceUsdc": "1", "updatedAt": "2026-08-12T00:00:00Z"},
                        {"id": "source-b", "priceUsdc": "1", "updatedAt": "2026-08-12T00:00:01Z"},
                    ],
                }
            }
        },
        opportunity={"tokenX": token_x, "tokenY": token_y},
    )

    assert blocked["status"] == "gas_price_cap_exceeded"
    assert blocked["blocked_reason"] == "gas_price_cap_exceeded"
    assert blocked["static_call"]["gasPricing"]["gasPriceWei"] == "2"


def test_runtime_trade_candidates_use_cached_v3_pools_for_top5_bottom5(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    top = [_market_row(f"T{i}", 5.0 - i) for i in range(6)]
    bottom = [_market_row(f"B{i}", -5.0 - i) for i in range(6)]
    symbols = ["USDC", *[row["base_symbol"] for row in [*top, *bottom]]]
    cache_path = tmp_path / "avalanche_v3_pools.json"
    cache_path.write_text(json.dumps(_pool_cache_for_symbols(symbols)), encoding="utf-8")
    monkeypatch.setenv("PINAX_POOL_DISCOVERY_CACHE_FILE", str(cache_path))

    trades = direct._runtime_trades_from_market_state(
        {"top": top, "bottom": bottom},
        cache=json.loads(cache_path.read_text(encoding="utf-8")),
        trade_limit=16,
    )

    assert len(trades) == 4
    assert [trade["tradeIndex"] for trade in trades] == [0, 1, 2, 3]
    assert [trade["strategyStatus"] for trade in trades] == [1, 2, 3, 5]
    assert all(2 <= len([pool for pool in trade["pools"] if pool["adapterKind"] == 1 and pool["pool"]]) <= 5 for trade in trades)
    assert all(pool["adapterKind"] in {0, 1} for trade in trades for pool in trade["pools"])
    assert len({trade["routeKey"] for trade in trades}) == 1

    default_trades = direct._runtime_trade_candidates(
        {"network": "avalanche"},
        {"market_state": {"top": top, "bottom": bottom}},
    )

    assert len(default_trades) == 4
    assert [trade["strategyStatus"] for trade in default_trades] == [1, 2, 3, 5]


def test_runtime_trade_candidates_expand_usdc_cross_pool_before_xy(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    top = [_market_row("AAA", 4.0)]
    bottom = [_market_row("BBB", -3.0)]
    cache_path = tmp_path / "avalanche_v3_pools.json"
    cache_path.write_text(json.dumps(_pool_cache_for_symbols(["USDC", "AAA", "BBB"])), encoding="utf-8")
    monkeypatch.setenv("PINAX_POOL_DISCOVERY_CACHE_FILE", str(cache_path))
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", _addr(500))

    trades = direct._runtime_trades_from_market_state(
        {"top": top, "bottom": bottom},
        cache=json.loads(cache_path.read_text(encoding="utf-8")),
        trade_limit=16,
    )

    assert [trade["strategyStatus"] for trade in trades] == [1, 2, 3, 5]
    assert trades[0]["routeSymbols"] == ["USDC", "AAA", "USDC"]
    assert trades[0]["tokenX"] == _addr(500)
    assert trades[0]["tokenY"] == _addr(501)
    assert trades[1]["routeSymbols"] == ["USDC", "BBB", "USDC"]
    assert trades[2]["routeSymbols"] == ["USDC", "AAA", "BBB", "USDC"]
    assert trades[3]["routeSymbols"] == ["USDC", "BBB", "AAA", "USDC"]


def test_runtime_route_groups_cover_all_cache_discovered_usdc_tokens(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    usdc = _addr(500)
    cache = _pool_cache_for_symbols(["USDC", "AAA", "BBB", "CCC"])
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", usdc)

    groups = direct._runtime_route_groups_from_market_state(
        {
            "top": [_market_row("AAA", 6.0), _market_row("BBB", 3.0)],
            "bottom": [_market_row("CCC", -4.0)],
        },
        cache=cache,
        group_limit=0,
    )

    direct_keys = {group["routeKey"] for group in groups if group["routeKind"] == "usdc_cross_pool"}
    triangular_keys = {group["routeKey"] for group in groups if group["routeKind"] == "usdc_triangular"}
    assert direct_keys == set()
    assert triangular_keys == {"AAA:BBB", "AAA:CCC", "BBB:CCC"}
    assert all(3 <= len(group["trades"]) <= 4 for group in groups if group["routeKind"] == "usdc_triangular")

    diagnostic_groups = direct._runtime_route_groups_from_market_state(
        {
            "top": [_market_row("AAA", 6.0), _market_row("BBB", 3.0)],
            "bottom": [_market_row("CCC", -4.0)],
        },
        cache=cache,
        group_limit=0,
        include_single_pair_diagnostic=True,
    )
    diagnostic_keys = {
        group["routeKey"]
        for group in diagnostic_groups
        if group["routeKind"] == "usdc_cross_pool_diagnostic"
    }
    assert diagnostic_keys == {"USDC:AAA", "USDC:BBB", "USDC:CCC"}
    assert all(
        len(group["trades"]) == 1
        for group in diagnostic_groups
        if group["routeKind"] == "usdc_cross_pool_diagnostic"
    )


def test_runtime_route_groups_filter_stable_targets_but_keep_wavax(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    cache = _pool_cache_for_symbols(["USDC", "USDt", "WAVAX", "BTC.b"])
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", _addr(500))

    groups = direct._runtime_route_groups_from_market_state(
        {
            "top": [_market_row("USDt", 1.0), _market_row("BTC.b", 0.8)],
            "bottom": [_market_row("WAVAX", -2.0)],
        },
        cache=cache,
        group_limit=0,
    )

    route_keys = {group["routeKey"] for group in groups}
    assert "BTC.B:WAVAX" in route_keys
    assert all("USDT" not in key.upper() for key in route_keys)


def test_dynamic_route_groups_inject_reviewed_token_registry_metadata(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    usdc, token_x, token_y = _addr(500), _addr(501), _addr(502)
    registry_path = tmp_path / "unified_token_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "registryVersion": "2026-08-12-review-1",
                "chainId": 43114,
                "tokens": [
                    {"token": usdc, "symbol": "USDC", "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                    {"token": token_x, "symbol": "AAA", "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                    {"token": token_y, "symbol": "BBB", "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", usdc)
    monkeypatch.setenv("UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE", str(registry_path))
    cache = _pool_cache_for_symbols(["USDC", "AAA", "BBB"])

    groups = direct._runtime_route_groups_from_market_state(
        {"network": "avalanche", "top": [_market_row("AAA", 2)], "bottom": [_market_row("BBB", -2)]},
        cache=cache,
        group_limit=0,
    )

    group = next(item for item in groups if item["routeKey"] == "AAA:BBB")
    assert {item["token"] for item in group["tokenRisks"]} == {usdc, token_x, token_y}
    assert group["tokenRiskRegistry"]["available"] is True
    assert group["tokenRiskRegistry"]["registryVersion"] == "2026-08-12-review-1"
    assert group["tokenRiskRegistry"]["canonicalHash"].startswith("sha256:")
    assert group["tokenRiskRegistry"]["integrityOk"] is True


def test_dynamic_route_groups_report_missing_registry_metadata_without_guessing(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    registry_path = tmp_path / "unified_token_registry.json"
    registry_path.write_text(
        json.dumps({"schemaVersion": 1, "registryVersion": "incomplete", "chainId": 43114, "tokens": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE", str(registry_path))
    cache = _pool_cache_for_symbols(["USDC", "AAA", "BBB"])

    groups = direct._runtime_route_groups_from_market_state(
        {"network": "avalanche", "top": [_market_row("AAA", 2)], "bottom": [_market_row("BBB", -2)]},
        cache=cache,
        group_limit=0,
    )

    group = next(item for item in groups if item["routeKey"] == "AAA:BBB")
    assert "tokenRisks" not in group
    assert group["tokenRiskRegistry"]["reason"] == "token_registry_metadata_missing"


def test_dynamic_route_groups_reject_token_registry_hash_mismatch(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    usdc, token_x, token_y = _addr(500), _addr(501), _addr(502)
    registry_path = tmp_path / "unified_token_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "registryVersion": "tampered",
                "chainId": 43114,
                "tokens": [
                    {"token": usdc, "symbol": "USDC", "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                    {"token": token_x, "symbol": "AAA", "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                    {"token": token_y, "symbol": "BBB", "reentrancyRisk": "low", "decimalTruncationRisk": "low"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", usdc)
    monkeypatch.setenv("UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("UNIFIED_EXECUTOR_TOKEN_REGISTRY_HASH", "sha256:" + "0" * 64)
    cache = _pool_cache_for_symbols(["USDC", "AAA", "BBB"])

    groups = direct._runtime_route_groups_from_market_state(
        {"network": "avalanche", "top": [_market_row("AAA", 2)], "bottom": [_market_row("BBB", -2)]},
        cache=cache,
        group_limit=0,
    )

    group = next(item for item in groups if item["routeKey"] == "AAA:BBB")
    assert "tokenRisks" not in group
    assert group["tokenRiskRegistry"]["reason"] == "token_registry_hash_mismatch"
    assert group["tokenRiskRegistry"]["canonicalHash"].startswith("sha256:")


def test_dynamic_route_groups_report_expired_token_registry_metadata(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    usdc, token_x, token_y = _addr(500), _addr(501), _addr(502)
    registry_path = tmp_path / "unified_token_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "registryVersion": "expired-token",
                "chainId": 43114,
                "tokens": [
                    {
                        "token": usdc,
                        "symbol": "USDC",
                        "reentrancyRisk": "low",
                        "decimalTruncationRisk": "low",
                        "expiresAt": "2099-01-01T00:00:00+00:00",
                    },
                    {
                        "token": token_x,
                        "symbol": "AAA",
                        "reentrancyRisk": "low",
                        "decimalTruncationRisk": "low",
                        "expiresAt": "2020-01-01T00:00:00+00:00",
                    },
                    {
                        "token": token_y,
                        "symbol": "BBB",
                        "reentrancyRisk": "low",
                        "decimalTruncationRisk": "low",
                        "expiresAt": "2099-01-01T00:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", usdc)
    monkeypatch.setenv("UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE", str(registry_path))
    cache = _pool_cache_for_symbols(["USDC", "AAA", "BBB"])

    groups = direct._runtime_route_groups_from_market_state(
        {"network": "avalanche", "top": [_market_row("AAA", 2)], "bottom": [_market_row("BBB", -2)]},
        cache=cache,
        group_limit=0,
    )

    group = next(item for item in groups if item["routeKey"] == "AAA:BBB")
    assert "tokenRisks" not in group
    assert group["tokenRiskRegistry"]["reason"] == "token_registry_metadata_expired"
    assert group["tokenRiskRegistry"]["expiredTokens"] == [token_x]
    assert group["tokenRiskRegistry"]["automaticDowngrade"] == "route_group_diagnostic_only_until_registry_refresh"


def test_live_signal_evidence_rejects_tampered_runtime_trades_hash():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-1",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    assert live_evidence.validate_unified_live_signal_evidence(evidence)["ok"] is True
    evidence["runtimeTrades"][0]["tokenY"] = _addr(502)
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "runtime_trades_hash_mismatch" in validation["errors"]


def test_live_signal_evidence_rejects_schema_constant_version_drift():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-schema-version",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    evidence["schemaConstantVersion"] = "unified_live_signal_schema:v0"
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "unsupported_schema_constant_version" in validation["errors"]


def test_live_signal_evidence_rejects_result_hash_tampering():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-1",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    evidence["result"]["status"] = "submitted_success"
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "result_hash_mismatch" in validation["errors"]


def test_live_signal_evidence_rejects_result_status_family_tampering():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-family-tamper",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    evidence["result"]["statusFamily"] = "broadcast_blocked"
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "result_status_family_mismatch" in validation["errors"]


def test_live_signal_evidence_records_default_delivery_policy_and_delivery_cost_aliases():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-delivery-policy",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is True
    assert evidence["deliveryPolicy"]["mode"] == "public_rpc_direct_after_fresh_gates"
    assert evidence["deliveryPolicy"]["privateFirst"] is False
    assert evidence["deliveryPolicy"]["privacyBoundary"] == "deferred_to_cow_or_intent_layer"
    assert evidence["netProfitModel"]["deliveryCostWei"] == evidence["netProfitModel"]["relayCostWei"]
    assert evidence["marketFeasibility"]["conclusion"] == "insufficient_data"
    assert evidence["marketFeasibility"]["competitorPressure"]["emptyObservationMeaning"] == (
        "no_sufficient_data_to_infer_competitor_pressure"
    )


def test_live_signal_evidence_rejects_missing_market_feasibility():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-market-feasibility-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    del evidence["marketFeasibility"]
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_missing" in validation["errors"]


def test_live_signal_evidence_rejects_competitor_pressure_without_empty_meaning():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-market-feasibility-empty-meaning",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    del evidence["marketFeasibility"]["competitorPressure"]["emptyObservationMeaning"]
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_empty_observation_meaning_missing" in validation["errors"]


def test_live_signal_evidence_rejects_delivery_cost_alias_mismatch():
    result = _live_signal_result()
    result["request"]["netProfitModel"]["deliveryCostWei"] = "1"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-delivery-cost-mismatch",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "delivery_cost_wei_relay_alias_mismatch" in validation["errors"]


def test_live_signal_evidence_requires_public_mempool_risk_penalty_fields():
    result = _live_signal_result()
    del result["request"]["netProfitModel"]["publicMempoolRiskPenaltyUsdc"]
    del result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyUsdc"]
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "public_mempool_risk_penalty_usdc_missing" in validation["errors"]
    assert "route_evaluation_0_public_mempool_risk_penalty_usdc_missing" in validation["errors"]


def test_live_signal_evidence_rejects_public_mempool_risk_penalty_component_mismatch():
    result = _live_signal_result()
    result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyFixedUsdc"] = "3"
    result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyBpsUsdc"] = "4"
    result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyUsdc"] = "8"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-mismatch",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "route_evaluation_0_public_mempool_risk_penalty_mismatch" in validation["errors"]
    assert validation["disposition"] == "diagnostic_only_notify_ops"


def test_live_signal_evidence_flags_large_public_mempool_penalty_for_manual_review():
    result = _live_signal_result()
    net_profit = result["request"]["routeEvaluations"][0]["netProfit"]
    net_profit["expectedProfit"] = "1000"
    net_profit["publicMempoolRiskPenaltyFixedUsdc"] = "600"
    net_profit["publicMempoolRiskPenaltyBpsUsdc"] = "0"
    net_profit["publicMempoolRiskPenaltyUsdc"] = "600"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-review",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is True
    assert validation["disposition"] == "manual_review_required"
    assert validation["reviewFlags"] == [
        "route_evaluation_0_public_mempool_penalty_gt_50pct_expected_profit"
    ]
    assert validation["manualReviewThresholdBps"] == "5000"


def test_live_signal_evidence_uses_configured_public_mempool_review_threshold():
    result = _live_signal_result()
    result["request"]["manualReviewThresholdBps"] = "7000"
    result["request"]["manualReviewThresholdAdjustmentHistory"] = [
        {
            "previousThresholdBps": "5000",
            "newThresholdBps": "7000",
            "proposedAt": "2026-08-12T00:00:00+00:00",
            "effectiveScope": "manual_review_candidate_only",
            "independentWindowStatistics": [
                {"runId": "window-1"},
                {"runId": "window-2"},
                {"runId": "window-3"},
            ],
            "competitorPressureSummary": "independent_provider_evidence_reviewed",
            "reviewConclusion": "approved_for_manual_review_only",
            "approvedBy": "reviewer@example.invalid",
            "rollbackCondition": "revert_on_any_window_quality_regression",
        }
    ]
    net_profit = result["request"]["routeEvaluations"][0]["netProfit"]
    net_profit["expectedProfit"] = "1000"
    net_profit["publicMempoolRiskPenaltyFixedUsdc"] = "600"
    net_profit["publicMempoolRiskPenaltyBpsUsdc"] = "0"
    net_profit["publicMempoolRiskPenaltyUsdc"] = "600"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-custom-threshold",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is True
    assert validation["disposition"] == "g07d_passed"
    assert validation["reviewFlags"] == []
    assert validation["manualReviewThresholdBps"] == "7000"


def test_live_signal_evidence_rejects_collected_competitor_source_without_provider_or_retention():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-metric-source-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    source = evidence["marketFeasibility"]["competitorPressure"]["metricSources"][
        "sameBlockHigherGasIndicator"
    ]
    source.update(
        {
            "sourceKind": "confirmed_block_receipts",
            "unavailableReason": None,
            "endpointHost": "rpc.example.invalid",
            "blockRange": {"from": 1, "to": 2},
            "transactionFilter": "target_pair_pool_interactions",
            "collectedAt": "2026-08-12T00:00:00+00:00",
            "rawEvidencePath": "evidence/raw.json",
            "rawEvidenceHash": "sha256:" + "a" * 64,
            "retentionUntil": "2026-08-13T00:00:00+00:00",
        }
    )
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_competitor_metric_source_provider_missing" in validation["errors"]
    assert "market_feasibility_competitor_metric_source_retention_too_short" in validation["errors"]


def test_live_signal_evidence_rejects_high_confidence_with_same_provider_sources():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-provider-independence",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    competitor = evidence["marketFeasibility"]["competitorPressure"]
    competitor["confidence"] = "high_confidence"
    collected_source = {
        "sourceKind": "confirmed_block_receipts",
        "sourceProvider": "provider-a",
        "endpointHost": "rpc.provider-a.invalid",
        "blockRange": {"from": 1, "to": 2},
        "transactionFilter": "target_pair_pool_interactions",
        "collectedAt": "2026-08-12T00:00:00+00:00",
        "rawEvidencePath": "evidence/raw-a.json",
        "rawEvidenceHash": "sha256:" + "a" * 64,
        "retentionUntil": "2026-11-10T00:00:00+00:00",
        "unavailableReason": None,
    }
    competitor["metricSources"]["sameBlockHigherGasIndicator"] = collected_source
    competitor["metricSources"]["independentSources"] = [
        {
            **collected_source,
            "sourceKind": "pending_websocket",
            "endpointHost": "ws.provider-a.invalid",
            "rawEvidencePath": "evidence/raw-b.json",
            "rawEvidenceHash": "sha256:" + "b" * 64,
        }
    ]
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_competitor_sources_not_independent" in validation["errors"]


def test_live_signal_evidence_rejects_low_frequency_without_24h_chain_confirmation():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-low-frequency-incomplete",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    market = evidence["marketFeasibility"]
    market["expectedProfitDistribution"].update(
        {
            "sampleSufficiency": "low_frequency_market",
            "sampleSufficiencyReason": "candidate_count_below_100",
            "candidateGenerationRatePerHour": 1.5,
            "lowFrequencyConfirmation": None,
        }
    )
    market["candidateGeneration"].update(
        {"listenerHealth": "healthy", "rpcHealth": "healthy", "cacheHealth": "healthy"}
    )
    evidence["listenerStartedAt"] = "2026-08-12T00:00:00+00:00"
    evidence["listenerStoppedAt"] = "2026-08-12T01:00:00+00:00"
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_low_frequency_requires_healthy_24h_window" in validation["errors"]
    assert "market_feasibility_low_frequency_confirmation_missing" in validation["errors"]


def test_live_signal_evidence_rejects_regime_declared_after_listener_start():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-regime-declared-late",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    evidence["listenerStartedAt"] = "2026-08-12T00:00:00+00:00"
    evidence["marketFeasibility"]["volatilitySummary"]["regimeDefinitionDeclaredAt"] = (
        "2026-08-12T00:00:01+00:00"
    )
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_regime_definition_declared_after_listener_start" in validation["errors"]


def test_live_signal_evidence_rejects_non_default_threshold_without_approval_history():
    result = _live_signal_result()
    result["request"]["manualReviewThresholdBps"] = "7000"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-threshold-history-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "manual_review_threshold_non_default_without_adjustment_history" in validation["errors"]


def test_live_signal_evidence_requires_pre_pause_trigger_source():
    result = _live_signal_result()
    result["prePause"] = {
        "prePause": True,
        "pauseDetectedAt": "2026-08-12T00:00:00+00:00",
        "signerBlockedAt": "2026-08-12T00:00:01+00:00",
        "pausePropagationMs": "1000",
    }
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-pre-pause-trigger-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "pre_pause_trigger_source_invalid" in validation["errors"]


def test_live_signal_evidence_rejects_diagnostic_input_claiming_live_evidence():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-2",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(runtime_trades=[]),
        live_signal_only=False,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "live_signal_semantics_missing" in validation["errors"]
    assert "runtime_trades_empty" in validation["errors"]


def test_live_signal_evidence_rejects_empty_runtime_trades_for_live_signal():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-empty-runtime-trades",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(runtime_trades=[]),
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "runtime_trades_empty" in validation["errors"]


def test_live_signal_evidence_rejects_incomplete_runtime_trade_shape():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-incomplete-runtime-trades",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(
            runtime_trades=[
                {"tradeIndex": 0, "tokenX": _addr(500), "tokenY": _addr(501)}
            ],
        ),
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "runtime_trade_0_pools_missing" in validation["errors"]
    assert "runtime_trade_0_pools_empty" in validation["errors"]


def test_live_signal_evidence_requires_schema_validation_and_no_positive_profit_claim_without_broadcast():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-3",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
        positive_profit_proven=True,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "positive_profit_claim_without_broadcast" in validation["errors"]
    assert "schema_validation_missing" not in validation["errors"]


def test_live_signal_evidence_rejects_failed_schema_validation():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-4",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )
    evidence["schemaValidation"] = {"ok": False, "errors": ["manual_failure"]}
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "schema_validation_failed" in validation["errors"]


def test_live_signal_evidence_rejects_broadcast_in_shadow_mode():
    result = _live_signal_result(status="submitted_success", submitted=True)
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-shadow-broadcast",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
        mode="shadow",
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "shadow_broadcast_detected" in validation["errors"]


def test_prepare_unified_route_groups_rejects_single_pair_without_diagnostic_flag():
    usdc = _addr(500)
    trade = direct._normalize_runtime_trade(
        {
            "tradeIndex": 0,
            "tokenX": usdc,
            "tokenY": _addr(501),
            "strategyStatus": 1,
            "routeKey": "USDC:AAA",
            "pools": [{"adapterKind": 1, "pool": _addr(601)}, {"adapterKind": 1, "pool": _addr(602)}],
        },
        0,
    )
    trade["strategyStatus"] = 1
    trade["routeKey"] = "USDC:AAA"
    groups = [{"routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool", "trades": [trade]}]

    prepared, rejected = direct._prepare_unified_route_groups(groups, usdc_address=usdc)
    diagnostic_prepared, _diagnostic_rejected = direct._prepare_unified_route_groups(
        groups,
        usdc_address=usdc,
        allow_single_pair_diagnostic=True,
    )

    assert prepared == []
    assert rejected[0]["reason"] == "single_pair_diagnostic_disabled"
    assert diagnostic_prepared[0]["routeCheck"]["routeDirection"] == "U-X-U"


def test_prepare_unified_route_groups_rejects_explicit_high_risk_token_metadata():
    usdc = _addr(500)
    token_x = _addr(501)
    token_y = _addr(502)
    groups = [
        {
            "routeKey": "AAA:BBB",
            "routeKind": "usdc_triangular",
            "tokenRisks": [
                {"token": token_x, "reentrancyRisk": "high"},
                {"token": token_y, "decimalTruncationRisk": "low"},
            ],
            "trades": [
                {
                    "tradeIndex": 0,
                    "tokenX": usdc,
                    "tokenY": token_x,
                    "strategyStatus": 1,
                    "pools": [{"adapterKind": 1, "pool": _addr(601)}],
                },
                {
                    "tradeIndex": 1,
                    "tokenX": usdc,
                    "tokenY": token_y,
                    "strategyStatus": 2,
                    "pools": [{"adapterKind": 1, "pool": _addr(602)}],
                },
                {
                    "tradeIndex": 2,
                    "tokenX": token_x,
                    "tokenY": token_y,
                    "strategyStatus": 3,
                    "pools": [{"adapterKind": 1, "pool": _addr(603)}],
                },
            ],
        }
    ]

    prepared, rejected = direct._prepare_unified_route_groups(groups, usdc_address=usdc)

    assert prepared == []
    assert rejected[0]["reason"] == "token_risk_broadcast_disabled"
    assert rejected[0]["tokenRisk"]["blocked"][0]["token"] == token_x


def test_prepare_unified_route_groups_requires_complete_token_risk_metadata_for_broadcast():
    usdc = _addr(500)
    token_x = _addr(501)
    token_y = _addr(502)
    groups = [
        {
            "routeKey": "AAA:BBB",
            "routeKind": "usdc_triangular",
            "tokenRisks": [{"token": usdc, "reentrancyRisk": "low"}],
            "trades": [
                {
                    "tradeIndex": 0,
                    "tokenX": usdc,
                    "tokenY": token_x,
                    "strategyStatus": 1,
                    "pools": [{"adapterKind": 1, "pool": _addr(601)}],
                },
                {
                    "tradeIndex": 1,
                    "tokenX": usdc,
                    "tokenY": token_y,
                    "strategyStatus": 2,
                    "pools": [{"adapterKind": 1, "pool": _addr(602)}],
                },
                {
                    "tradeIndex": 2,
                    "tokenX": token_x,
                    "tokenY": token_y,
                    "strategyStatus": 3,
                    "pools": [{"adapterKind": 1, "pool": _addr(603)}],
                },
            ],
        }
    ]

    prepared, rejected = direct._prepare_unified_route_groups(
        groups,
        usdc_address=usdc,
        require_token_risk_metadata=True,
    )

    assert prepared == []
    assert rejected[0]["reason"] == "token_risk_metadata_missing"
    assert set(rejected[0]["tokenRisk"]["missingTokens"]) == {token_x, token_y}


def test_runtime_route_group_candidates_preserve_explicit_risk_and_penalty_metadata():
    usdc = _addr(500)
    groups = direct._runtime_route_group_candidates(
        {
            "runtime_trades": [
                {
                    "tradeIndex": 0,
                    "tokenX": usdc,
                    "tokenY": _addr(501),
                    "pools": [{"adapterKind": 1, "pool": _addr(601)}],
                }
            ],
            "tokenRisks": [{"token": _addr(501), "reentrancyRisk": "low"}],
            "slippageRiskPenaltyUsdc": "250000",
            "routeCorrelationPenaltyUsdc": "500000",
        }
    )

    assert groups[0]["tokenRisks"][0]["reentrancyRisk"] == "low"
    assert groups[0]["slippageRiskPenaltyUsdc"] == "250000"
    assert groups[0]["routeCorrelationPenaltyUsdc"] == "500000"


def test_unified_route_group_check_accepts_single_usdc_cross_pool_trade():
    usdc = _addr(500)
    trade = direct._normalize_runtime_trade(
        {
            "tradeIndex": 0,
            "tokenX": usdc,
            "tokenY": _addr(501),
            "strategyStatus": 1,
            "routeKey": "USDC:AAA",
            "pools": [{"adapterKind": 1, "pool": _addr(601)}, {"adapterKind": 1, "pool": _addr(602)}],
        },
        0,
    )
    trade["strategyStatus"] = 1
    trade["routeKey"] = "USDC:AAA"

    ordered, plan = direct._ordered_runtime_trade_plan([trade], usdc_address=usdc)
    check = direct._unified_executor_route_order_check(ordered, usdc_address=usdc)

    assert plan["ok"] is True
    assert len(ordered) == 1
    assert check["ok"] is True
    assert check["routeDirection"] == "U-X-U"


def test_unified_route_group_selection_uses_highest_usdc_expected_profit():
    usdc = _addr(500)
    groups = [
        {"groupIndex": 0, "routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool"},
        {"groupIndex": 1, "routeKey": "USDC:BBB", "routeKind": "usdc_cross_pool"},
    ]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_000_000 + expected_profit, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda group: preview(12 if group["routeKey"] == "USDC:AAA" else 34),
    )

    assert selected["routeKey"] == "USDC:BBB"
    assert report["executionPreview"]["expectedProfit"] == "34"
    assert [item["profitableCandidate"] for item in evaluations] == [True, True]
    assert [item["selected"] for item in evaluations] == [False, True]


def test_unified_route_group_selection_uses_net_profit_when_gas_model_is_supplied():
    usdc = _addr(500)
    groups = [
        {"groupIndex": 0, "routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool"},
        {"groupIndex": 1, "routeKey": "USDC:BBB", "routeKind": "usdc_cross_pool"},
    ]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_000_000 + expected_profit, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, _report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda group: preview(100 if group["routeKey"] == "USDC:AAA" else 120),
        estimate_group_gas=lambda group: 10 if group["routeKey"] == "USDC:AAA" else 100,
        gas_price_wei=10**18,
        avax_usdc_price_micro=1,
    )

    assert selected["routeKey"] == "USDC:AAA"
    assert [item["netProfit"]["netProfit"] for item in evaluations] == ["90", "20"]


def test_unified_route_group_selection_applies_slippage_and_correlation_penalties():
    usdc = _addr(500)
    groups = [
        {
            "groupIndex": 0,
            "routeKey": "USDC:AAA",
            "routeKind": "usdc_cross_pool",
            "slippageRiskPenaltyUsdc": 30,
            "routeCorrelationPenaltyUsdc": 20,
        },
        {
            "groupIndex": 1,
            "routeKey": "USDC:BBB",
            "routeKind": "usdc_cross_pool",
        },
    ]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_000_000 + expected_profit, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, _report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda _group: preview(100),
    )

    assert selected["routeKey"] == "USDC:BBB"
    assert evaluations[0]["netProfit"]["slippageRiskPenaltyUsdc"] == "30"
    assert evaluations[0]["netProfit"]["routeCorrelationPenaltyUsdc"] == "20"
    assert [item["netProfit"]["netProfit"] for item in evaluations] == ["50", "100"]


def test_unified_route_group_selection_applies_public_mempool_risk_penalty():
    usdc = _addr(500)
    groups = [{"groupIndex": 0, "routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool"}]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_001_000, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, _report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda _group: preview(1_000),
        public_mempool_risk_penalty_usdc=100,
        public_mempool_risk_penalty_bps=1_000,
    )

    assert selected["routeKey"] == "USDC:AAA"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyFixedUsdc"] == "100"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyBps"] == "1000"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyBpsUsdc"] == "100"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyUsdc"] == "200"
    assert evaluations[0]["netProfit"]["netProfit"] == "800"


def test_legacy_direct_onchain_path_is_disabled():
    result = direct._submit_legacy_direct_onchain_trade(
        quote_payload={"cow_flashloan_intent": {"direct_onchain_protocol": {"enabled": True}}},
        opportunity={},
    )

    assert result["status"] == "legacy_direct_path_disabled"
    assert result["submitted"] is False


def test_runtime_cache_validation_rejects_stale_or_wrong_chain_cache():
    assert not direct._runtime_cache_validation(
        {"chain_id": 43113, "block_number": 90},
        network="avalanche",
        current_block=100,
        max_age_blocks=30,
    )["ok"]

    stale = direct._runtime_cache_validation(
        {"chain_id": 43114, "block_number": 10},
        network="avalanche",
        current_block=100,
        max_age_blocks=30,
    )

    assert stale["reason"] == "cache_stale"


def test_private_tx_can_disable_public_fallback(monkeypatch):
    from execution import private_tx

    class FakeEth:
        def send_raw_transaction(self, _raw):
            raise AssertionError("public fallback must stay disabled")

    result = private_tx.send_raw_transaction_private_first(
        b"raw",
        public_w3=SimpleNamespace(eth=FakeEth()),
        relay_urls="",
        allow_public_fallback=False,
    )

    assert result["broadcast_channel"] == "not_broadcast"
    assert result["tx_hash"] is None


def test_delivery_policy_requires_explicit_private_relay_research(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    default_policy = direct._delivery_policy_report({}, {})
    explicit_policy = direct._delivery_policy_report(
        {"privateRelayResearchEnabled": True, "allowPublicFallback": True},
        {},
    )

    assert default_policy["mode"] == "public_rpc_direct_after_fresh_gates"
    assert default_policy["privateFirst"] is False
    assert default_policy["publicFallbackRequested"] is False
    assert default_policy["privacyBoundary"] == "deferred_to_cow_or_intent_layer"
    assert explicit_policy["mode"] == "private_relay_research"
    assert explicit_policy["privateRelayResearchEnabled"] is True
    assert explicit_policy["privateFirst"] is True
    assert explicit_policy["publicFallbackRequested"] is True
    assert explicit_policy["privacyBoundary"] == "operator_supplied_private_rpc_research_only"


def test_delivery_cost_input_alias_takes_precedence_over_deprecated_relay_cost(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    assert direct._relay_cost_wei({"relayCostWei": "1", "deliveryCostWei": "2"}, {}) == 2

    monkeypatch.setenv("UNIFIED_EXECUTOR_RELAY_COST_WEI", "3")
    monkeypatch.setenv("UNIFIED_EXECUTOR_DELIVERY_COST_WEI", "4")
    assert direct._relay_cost_wei({}, {}) == 4


def test_direct_circuit_breaker_persists_threshold_pause(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    state_path = tmp_path / "direct_breaker.json"
    monkeypatch.setenv("DIRECT_ONCHAIN_CIRCUIT_BREAKER_FILE", str(state_path))
    monkeypatch.setenv("DIRECT_ONCHAIN_CIRCUIT_OFFCHAIN_THRESHOLD", "2")
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    failure = {"ok": False, "submitted": False, "status": "net_profit_not_positive"}

    direct._record_direct_circuit_result(failure, network="avalanche", route_key="USDC:AAA", now=now)
    first = direct._direct_circuit_status(network="avalanche", route_key="USDC:AAA", now=now, path=state_path)
    direct._record_direct_circuit_result(failure, network="avalanche", route_key="USDC:AAA", now=now, path=state_path)
    second = direct._direct_circuit_status(network="avalanche", route_key="USDC:AAA", now=now, path=state_path)

    assert first["paused"] is False
    assert second["paused"] is True
    assert second["level"] == "yellow"
    assert second["state"]["offchainFailures"] == 2


def test_direct_circuit_breaker_success_clears_route_group(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    state_path = tmp_path / "direct_breaker.json"
    monkeypatch.setenv("DIRECT_ONCHAIN_CIRCUIT_BREAKER_FILE", str(state_path))
    direct._record_direct_circuit_result(
        {"ok": False, "submitted": True, "status": "submitted_failed"},
        network="avalanche",
        route_key="USDC:AAA",
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
        path=state_path,
    )
    direct._record_direct_circuit_result(
        {"ok": True, "submitted": True, "status": "submitted_success"},
        network="avalanche",
        route_key="USDC:AAA",
        now=datetime(2026, 8, 12, 0, 1, tzinfo=timezone.utc),
        path=state_path,
    )
    status = direct._direct_circuit_status(
        network="avalanche",
        route_key="USDC:AAA",
        now=datetime(2026, 8, 12, 0, 1, tzinfo=timezone.utc),
        path=state_path,
    )

    assert status["paused"] is False
    assert status["level"] == "green"
    assert status["state"]["onchainFailures"] == 0


def test_submit_cow_intent_trade_reports_incomplete_direct_protocol_without_cow_fallback(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    def fail_cow_submit(**_kwargs):
        raise AssertionError("direct-onchain intent must not fall back to cow order submission")

    monkeypatch.setattr("intent_trade.submission.order_submission.submit_cow_flashloan_order", fail_cow_submit)

    result = submit_cow_intent_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "ready": True,
                "submission_protocol": "direct_onchain",
                "direct_onchain_protocol": {
                    "enabled": True,
                    "network": "avalanche",
                },
            }
        },
        opportunity={},
    )

    assert result["status"] == "direct_protocol_incomplete"
    assert result["error"] == "unified executor address is required"


def test_unified_executor_route_plan_enforces_one_coherent_xy_group(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", _addr(500))
    cache_path = tmp_path / "pools.json"
    cache = _pool_cache_for_symbols(["USDC", "AAA", "BBB"])
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    trades = direct._runtime_trades_from_market_state(
        {"top": [_market_row("AAA", 4.0)], "bottom": [_market_row("BBB", -3.0)]},
        cache=cache,
    )
    ordered, plan = direct._ordered_runtime_trade_plan(trades, usdc_address=_addr(500))
    check = direct._unified_executor_route_order_check(ordered, usdc_address=_addr(500))

    assert plan["ok"] is True
    assert [trade["strategyStatus"] for trade in ordered] == [1, 2, 3, 5]
    assert len({trade["routeKey"] for trade in ordered}) == 1
    assert check["ok"] is True
    assert check["routeDirection"] == "U-X-Y-U"
    assert all(len(trade["pools"]) == 5 for trade in ordered)


def test_unified_executor_route_plan_rejects_mixed_xy_slots():
    usdc = _addr(500)
    trades = json.loads(_unified_runtime_trades_json(usdc, _addr(501), _addr(502)))
    trades[2]["tokenX"] = _addr(503)
    normalized = [direct._normalize_runtime_trade(trade, index) for index, trade in enumerate(trades)]

    assert direct._unified_executor_route_order_check(normalized, usdc_address=usdc)["ok"] is False


def test_unified_executor_abi_is_loaded_from_the_current_contract_artifact(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    abi = direct._load_unified_executor_abi()
    names = {entry.get("name") for entry in abi if entry.get("type") == "function"}

    assert "previewOrderedRuntimeAutoExecution" in names
    assert "runOrderedRuntimeTradesAndExecuteAuto" in names
    assert "previewFirstProfitableRuntimeAutoExecution" not in names


def test_aave_borrowable_cache_rejects_mainnet_data_for_fuji():
    cache = {
        "rpc_url": "https://api.avax.network/ext/bc/C/rpc",
        "assets": [
            {
                "token_address": _addr(900),
                "available_liquidity": 1_000_000,
            }
        ],
    }

    assert direct._aave_borrowable_token_addresses(cache, network="fuji") == set()


def test_cross_pool_filter_allows_usdc_base_and_blocks_non_usdc_by_default(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    usdc = _addr(500)
    usdc_trade = {"tokenX": usdc, "tokenY": _addr(501)}
    xy_trade = {"tokenX": _addr(501), "tokenY": _addr(502)}

    assert direct._cross_pool_trade_allowed(
        usdc_trade,
        usdc_address=usdc,
        borrowable_addresses=set(),
        allow_non_usdc=False,
    )
    assert not direct._cross_pool_trade_allowed(
        xy_trade,
        usdc_address=usdc,
        borrowable_addresses={_addr(501)},
        allow_non_usdc=False,
    )
    assert direct._cross_pool_trade_allowed(
        xy_trade,
        usdc_address=usdc,
        borrowable_addresses={_addr(501)},
        allow_non_usdc=True,
    )


def test_selected_strategy_status_maps_xy_cross_pool_and_triangular_fallback():
    xy_trade = {"tradeIndex": 3, "strategyStatus": 3}

    assert direct._selected_strategy_status(xy_trade, execution_mode="auto", execution_kind=2) == 3
    assert direct._selected_strategy_status(xy_trade, execution_mode="auto", execution_kind=1) == 4
    assert direct._selected_strategy_status(xy_trade, execution_mode="triangular", execution_kind=1) == 4
    assert direct._selected_strategy_status({"strategyStatus": 5}, execution_mode="triangular", execution_kind=1) == 5
    assert direct._selected_strategy_status(None, execution_mode="auto", execution_kind=None) == 55555
    assert direct._SELECTED_STRATEGY_STATUS_TRANSLATION[("auto", 1, 3)] == 4
    assert direct._SELECTED_STRATEGY_STATUS_TRANSLATION[("auto", 2, 3)] == 3
    assert direct._SELECTED_STRATEGY_STATUS_TRANSLATION[("triangular", 1, 3)] == 4


def test_runtime_trade_candidates_keep_input_order_and_cap_at_top_five(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    supplied = []
    for index in range(6):
        supplied.append(
            {
                "tradeIndex": index,
                "tokenX": _addr(800 + index * 2),
                "tokenY": _addr(801 + index * 2),
                "pools": [
                    {"adapterKind": 1, "pool": _addr(900 + index * 2)},
                    {"adapterKind": 1, "pool": _addr(901 + index * 2)},
                ],
            }
        )

    trades = direct._runtime_trade_candidates({"runtime_trades": supplied})

    assert [trade["tradeIndex"] for trade in trades] == [0, 1, 2, 3, 4]


def test_unified_route_plan_drops_the_unused_fifth_trade_input():
    usdc = _addr(500)
    trades = json.loads(_unified_runtime_trades_json(usdc, _addr(501), _addr(502)))
    trades.append(
        {
            "tradeIndex": 4,
            "tokenX": usdc,
            "tokenY": _addr(503),
            "strategyStatus": 6,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(608)}, {"adapterKind": 1, "pool": _addr(609)}],
        }
    )
    normalized = [direct._normalize_runtime_trade(trade, index) for index, trade in enumerate(trades)]

    ordered, plan = direct._ordered_runtime_trade_plan(normalized, usdc_address=usdc)

    assert plan["ok"] is True
    assert len(ordered) == 4
    assert [trade["strategyStatus"] for trade in ordered] == [1, 2, 3, 5]


def test_unified_route_plan_caps_ungrouped_external_inputs_at_four():
    usdc = _addr(500)
    source = []
    for index in range(5):
        source.append(
            direct._normalize_runtime_trade(
                {
                    "tradeIndex": index,
                    "tokenX": usdc if index < 2 else _addr(501),
                    "tokenY": _addr(510 + index),
                    "pools": [{"adapterKind": 1, "pool": _addr(600 + index)}],
                },
                index,
            )
        )

    ordered, plan = direct._ordered_runtime_trade_plan(source, usdc_address=usdc)

    assert plan["ok"] is True
    assert len(ordered) == 4


def test_rank_runtime_trades_by_profit_keeps_top_five_and_stable_ties():
    candidates = [{"tradeIndex": index} for index in range(6)]
    quotes = {
        0: (True, 1_000_510),
        1: (True, 1_000_800),
        2: (False, 0),
        3: (True, 1_000_600),
        4: (True, 1_000_800),
        5: (True, 1_000_550),
    }

    def preview_trade(trade):
        found, quoted_final = quotes[trade["tradeIndex"]]
        return [
            found,
            0,
            [],
            [
                _addr(999),
                "0x1234",
                quoted_final,
                500,
                1_000_501,
                1_000_501,
                1,
            ],
        ]

    selected, reports = direct._rank_runtime_trades_by_profit(candidates, preview_trade)

    assert [trade["tradeIndex"] for trade in selected] == [0, 5, 3, 1, 4]
    assert [report["tradeIndex"] for report in reports] == ["0", "5", "3", "1", "4"]
    assert reports[0]["netProfitUsdc"] == "10"


def test_submit_direct_onchain_trade_builds_runtime_trades_from_market_state_cache(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_USDC_ADDRESS", _addr(500))
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    top = [_market_row("AAA", 4.0)]
    bottom = [_market_row("BBB", -3.0)]
    cache_path = tmp_path / "avalanche_v3_pools.json"
    cache_path.write_text(json.dumps(_pool_cache_for_symbols(["USDC", "AAA", "BBB"])), encoding="utf-8")
    monkeypatch.setenv("PINAX_POOL_DISCOVERY_CACHE_FILE", str(cache_path))

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "ready": True,
                "direct_onchain_protocol": {
                    "enabled": True,
                    "network": "avalanche",
                    "unified_executor_address": _addr(71),
                },
            }
        },
        opportunity={"market_state": {"top": top, "bottom": bottom}},
    )

    assert result["status"] == "network_config_missing"
    assert result["blocked_reason"] == "network_config_missing"


def test_runtime_execution_params_use_explicit_min_profit_base_units(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    params = direct._runtime_execution_params(
        {
            "execution_amount": "1000000",
            "deadline": "2000000000",
            "amountOutMinUsdc": "1000100",
            "minProfitUsdc": "12345",
            "expected_profit_usdc": "6.18",
        },
        None,
    )

    assert params == {
        "amount": 1_000_000,
        "deadline": 2_000_000_000,
        "amountOutMinUsdc": 1_000_100,
        "minProfitUsdc": 12_345,
        "usdcToTokenXFee": 3_000,
        "tokenYToUsdcFee": 3_000,
    }


def test_runtime_execution_params_convert_expected_profit_usdc_to_base_units(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    params = direct._runtime_execution_params(
        {
            "execution_amount": "1000000",
            "deadline": "2000000000",
            "amountOutMinUsdc": "1000000",
            "expected_profit_usdc": "6.18",
        },
        None,
    )

    assert params["minProfitUsdc"] == 6_180_000


def test_runtime_execution_params_accept_testnet_env_aliases(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_BORROW_AMOUNT_UNITS", "100000000")
    monkeypatch.setenv("TRIANGULAR_AMOUNT_OUT_MIN_USDC", "100010000")
    monkeypatch.setenv("TRIANGULAR_MIN_PROFIT_USDC", "123")

    params = direct._runtime_execution_params({}, None)

    assert params["amount"] == 100_000_000
    assert params["amountOutMinUsdc"] == 100_010_000
    assert params["minProfitUsdc"] == 123


def test_runtime_execution_params_keeps_configured_net_profit_floor(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_BORROW_AMOUNT_UNITS", "100000000")
    monkeypatch.setenv("TRIANGULAR_AMOUNT_OUT_MIN_USDC", "100010000")
    monkeypatch.setenv("TRIANGULAR_MIN_PROFIT_USDC_BASE_UNITS", "1")
    monkeypatch.setenv("TRIANGULAR_MIN_NET_PROFIT_USDC_BASE_UNITS", "1000000")

    params = direct._runtime_execution_params(
        {
            "execution_amount": "100000000",
            "minProfitUsdc": "12345",
            "deadline": "2000000000",
        },
        None,
    )

    assert params["minProfitUsdc"] == 1_000_000


def test_broadcast_gas_guard_requires_a_nonzero_price_under_the_configured_cap(monkeypatch):
    monkeypatch.setenv("TRIANGULAR_MAX_GAS_PRICE_WEI", "30000000000")

    accepted = direct._broadcast_gas_guard(
        200_000,
        SimpleNamespace(max_fee=25_000_000_000, strategy="normal"),
    )
    capped = direct._broadcast_gas_guard(
        200_000,
        SimpleNamespace(max_fee=30_000_000_001, strategy="normal"),
    )
    unavailable = direct._broadcast_gas_guard(
        200_000,
        SimpleNamespace(max_fee=0, strategy="blocked"),
    )

    assert accepted["ok"] is True
    assert accepted["report"]["estimatedCostWei"] == "5000000000000000"
    assert capped["status"] == "gas_price_cap_exceeded"
    assert unavailable["status"] == "gas_price_unavailable"


def test_gas_token_price_report_requires_fresh_timestamp(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    now = datetime(2026, 8, 12, 0, 2, tzinfo=timezone.utc)

    missing_timestamp = direct._gas_token_usdc_price_report(
        {"avaxUsdcPrice": "24.5"},
        now=now,
    )
    fresh = direct._gas_token_usdc_price_report(
        {
            "avaxUsdcPrice": "24.5",
            "avaxUsdcPriceUpdatedAt": "2026-08-12T00:01:30Z",
            "gasTokenPriceMaxAgeSeconds": 60,
        },
        now=now,
    )
    stale = direct._gas_token_usdc_price_report(
        {
            "avaxUsdcPrice": "24.5",
            "avaxUsdcPriceUpdatedAt": "2026-08-12T00:00:00Z",
            "gasTokenPriceMaxAgeSeconds": 60,
        },
        now=now,
    )

    assert missing_timestamp["reason"] == "gas_token_usdc_price_timestamp_missing"
    assert fresh["ok"] is True
    assert fresh["priceMicro"] == "24500000"
    assert fresh["ageSeconds"] == "30"
    assert stale["reason"] == "gas_token_usdc_price_stale"


def test_gas_token_price_report_accepts_unix_timestamp():
    report = direct._gas_token_usdc_price_report(
        {
            "gasTokenUsdcPrice": "25",
            "gasTokenUsdcPriceUpdatedAt": "1786492890",
            "gasTokenPriceMaxAgeSeconds": 60,
        },
        now=datetime(2026, 8, 12, 0, 2, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert report["priceMicro"] == "25000000"


def test_gas_token_price_report_requires_healthy_multi_source_for_broadcast():
    now = datetime(2026, 8, 12, 0, 2, tzinfo=timezone.utc)
    sources = [
        {"id": "chainlink_avax_usd", "kind": "chainlink-derived", "priceUsdc": "25.00", "updatedAt": "2026-08-12T00:01:50Z"},
        {"id": "independent_quote", "kind": "independent", "priceUsdc": "25.01", "updatedAt": "2026-08-12T00:01:55Z"},
    ]

    report = direct._gas_token_usdc_price_report(
        {
            "priceSourcePolicy": "multi-source-median",
            "priceSources": sources,
            "gasTokenPriceMaxAgeSeconds": 60,
            "priceMaxDeviationBps": 20,
        },
        now=now,
        require_production_sources=True,
    )
    blocked = direct._gas_token_usdc_price_report(
        {"priceSourcePolicy": "diagnostic-single-source", "priceSources": [sources[0]]},
        now=now,
        require_production_sources=True,
    )

    assert report["ok"] is True
    assert report["priceSourceCount"] == "2"
    assert report["priceSourcePolicy"] == "multi-source-median"
    assert blocked["reason"] == "gas_token_price_source_health_failed"


def test_direct_pre_pause_persists_and_blocks_new_broadcasts(tmp_path):
    path = tmp_path / "direct_pre_pause.json"

    active = direct.set_direct_pre_pause(
        True,
        reason="drill",
        set_by="test",
        pause_trigger_source="watchdog",
        pause_detected_at="2026-08-13T00:00:00+00:00",
        blocked_candidate_count=3,
        path=path,
    )
    cleared = direct.set_direct_pre_pause(False, reason="manual_resume", set_by="test", path=path)

    assert active["prePause"] is True
    assert active["reason"] == "drill"
    assert active["pauseTriggerSource"] == "watchdog"
    assert active["pauseDetectedAt"] == "2026-08-13T00:00:00+00:00"
    assert active["signerBlockedAt"] >= active["pauseDetectedAt"]
    assert int(active["pausePropagationMs"]) >= 0
    assert active["blockedCandidateCount"] == "3"
    assert cleared["prePause"] is False
    assert cleared["reason"] == "manual_resume"


def test_cache_risk_penalty_adds_per_block_age(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_CACHE_RISK_PENALTY_USDC", "0.25")
    monkeypatch.setenv("TRIANGULAR_CACHE_RISK_PENALTY_PER_BLOCK_USDC", "0.01")

    penalty = direct._cache_risk_penalty_usdc_base_units(
        cache_reports={
            "runtimePoolCache": {"ok": True, "currentBlock": "120", "cacheBlock": "100"},
            "aaveReserveCache": {"ok": True, "ageBlocks": "7"},
        }
    )

    assert penalty == 450_000


def test_submit_direct_onchain_trade_reports_incomplete_when_token_pair_is_not_in_memory_table(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRIANGULAR_RUNTIME_POOL_CACHE_FILE", str(tmp_path / "missing_pool_cache.json"))
    set_usdc_pair_memory_table([{"tokenX": _addr(41), "tokenY": _addr(42)}])

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": True,
                    "network": "avalanche",
                    "unified_executor_address": _addr(43),
                }
            }
        },
        opportunity={"tokenX": _addr(44), "tokenY": _addr(45)},
    )

    assert result["status"] == "direct_protocol_incomplete"
    assert result["error"] == "runtime_trades_empty"
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])


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
            0,
            0,
            0,
            0,
            0,
            -70000,
        ]
    )

    assert report["failureCode"] == "3"
    assert report["failureReason"] == "middle_hop_quote_failed"
    assert report["path"] == ["USDC", "JOE", "AI", "USDC"]
    assert report["requiredFinalUsdc"] == "1000500001"
    assert report["mBps"] == "-70000"


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
