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
    from bot.execution import private_tx

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

    monkeypatch.setattr("dex.intent_trade.submission.order_submission.submit_cow_flashloan_order", fail_cow_submit)

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


def test_unified_executor_abi_is_loaded_from_the_current_contract_artifact(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)

    artifact = tmp_path / "UnifiedFlashLoanMevExecutor.json"
    artifact.write_text(
        json.dumps(
            {
                "abi": [
                    {"type": "function", "name": "previewOrderedRuntimeAutoExecution", "inputs": [], "outputs": []},
                    {"type": "function", "name": "runOrderedRuntimeTradesAndExecuteAuto", "inputs": [], "outputs": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("UNIFIED_EXECUTOR_ARTIFACT", str(artifact))
    monkeypatch.setattr(direct, "_UNIFIED_EXECUTOR_ABI_CACHE", None)

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


