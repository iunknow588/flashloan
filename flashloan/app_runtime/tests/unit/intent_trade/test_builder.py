import inspect
import json
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

from dex.intent_trade import (
    bind_cow_intent_context,
    build_cow_intent_trade,
    build_triangular_onchain_intent_trade,
    pair_index_from_tokenx_tokeny,
    set_usdc_pair_memory_table,
    submit_cow_intent_trade,
)
from dex.intent_trade import direct
from dex.intent_trade import direct_utils
from dex.intent_trade import live_evidence
from dex.intent_trade.unified_live_signal_schema import (
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
        "TRIANGULAR_RUNTIME_POOL_CACHE_FILE",
        "PINAX_POOL_DISCOVERY_CACHE_FILE",
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
        "ROBINHOOD_RPC_URL",
        "ROBINHOOD_RPC",
        "ROBINHOOD_RPCS",
        "ROBINHOOD_MAINNET_RPC_URL",
        "ROBINHOOD_MAINNET_RPC",
        "ROBINHOOD_TESTNET_RPC_URL",
        "ROBINHOOD_TESTNET_RPC",
        "ROBINHOOD_TESTNET_RPCS",
        "ROBINHOOD_USDC_ADDRESS",
        "ROBINHOOD_TESTNET_USDC_ADDRESS",
        "FUJI_USDC_ADDRESS",
        "FUJI_USDC",
        "AVALANCHE_USDC_ADDRESS",
        "AVALANCHE_USDC",
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


def test_direct_onchain_network_can_target_bnb_in_shadow_only_mode(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_DIRECT_NETWORK", "bsc")

    network, chain_id, testnet = direct._resolve_direct_onchain_network({})

    assert network == "bnb"
    assert chain_id == 56
    assert testnet is False
    assert direct._usdc_address_for_network("56") == "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
    assert direct._unified_executor_supported_for_network("bnb") is False


def test_direct_onchain_network_can_target_robinhood_in_shadow_only_mode(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_DIRECT_NETWORK", "robinhood-chain")

    network, chain_id, testnet = direct._resolve_direct_onchain_network({})

    assert network == "robinhood"
    assert chain_id == 4663
    assert testnet is False
    assert direct._unified_executor_supported_for_network("4663") is False
    assert direct._usdc_address_for_network("robinhood") == ""


def test_direct_onchain_network_can_target_robinhood_testnet_by_chain_id(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    network, chain_id, testnet = direct._resolve_direct_onchain_network({"chain_id": 46630})

    assert network == "robinhood-testnet"
    assert chain_id == 46630
    assert testnet is True


def test_robinhood_testnet_usdc_ignores_robinhood_mainnet_env(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("ROBINHOOD_USDC_ADDRESS", _addr(770))

    assert direct._usdc_address_for_network("robinhood-testnet") == ""


def test_robinhood_testnet_usdc_uses_testnet_specific_env(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("ROBINHOOD_USDC_ADDRESS", _addr(770))
    monkeypatch.setenv("ROBINHOOD_TESTNET_USDC_ADDRESS", _addr(771))

    assert direct._usdc_address_for_network("robinhood-testnet") == _addr(771)


def test_direct_onchain_rpc_prefers_robinhood_rpc_for_robinhood(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("AVALANCHE_RPC_URL", "https://mainnet.example")
    monkeypatch.setenv("ROBINHOOD_RPC_URL", "https://robinhood.example")

    rpc_url, rpc_env_names = direct._direct_rpc_url("robinhood")

    assert rpc_url == "https://robinhood.example"
    assert rpc_env_names[0] == "ROBINHOOD_RPC_URL"


def test_bnb_direct_submission_is_explicitly_disabled(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": False,
                    "network": "bnb",
                    "chain_id": 56,
                }
            }
        },
        opportunity={},
        timeout_seconds=1,
    )

    assert result["submitted"] is False
    assert result["status"] == "bnb_executor_adapter_unverified"
    assert result["network"] == "bnb"


def test_robinhood_direct_submission_is_explicitly_disabled(monkeypatch):
    _clear_direct_pair_env(monkeypatch)

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": False,
                    "network": "robinhood",
                    "chain_id": 4663,
                }
            }
        },
        opportunity={},
        timeout_seconds=1,
    )

    assert result["submitted"] is False
    assert result["status"] == "robinhood_executor_adapter_unverified"
    assert result["network"] == "robinhood"


def test_direct_onchain_rpc_prefers_fuji_rpc_for_fuji(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("AVALANCHE_RPC_URL", "https://mainnet.example")
    monkeypatch.setenv("FUJI_RPC_URL", "https://fuji.example")

    rpc_url, rpc_env_names = direct._direct_rpc_url("fuji")

    assert rpc_url == "https://fuji.example"
    assert rpc_env_names == ("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL")


def test_avalanche_mainnet_usdc_ignores_fuji_specific_env(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("FUJI_USDC_ADDRESS", _addr(777))

    assert direct._usdc_address_for_network("avalanche") == direct.AVALANCHE_USDC_ADDRESS


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


