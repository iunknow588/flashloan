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


def test_submit_direct_onchain_trade_runs_unified_preview_and_static_call_before_broadcast(monkeypatch, tmp_path):
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


