import inspect
import json
import sys
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
        "TRIANGULAR_EXECUTION_MIN_PROFIT_USDC",
        "TRIANGULAR_USDC_ADDRESS",
        "USDC_ADDRESS",
        "TRIANGULAR_DIRECT_NETWORK",
        "TRIANGULAR_ONCHAIN_NETWORK",
        "TRIANGULAR_TESTNET_NAME",
        "TRIANGULAR_NETWORK",
        "TRIANGULAR_TESTNET_CHAIN_ID",
        "AAVE_RESERVE_CACHE_FILE",
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


def test_build_triangular_onchain_intent_trade_requires_runtime_trades(monkeypatch):
    monkeypatch.setenv("UNIFIED_EXECUTOR_ADDRESS", "0x" + "2" * 40)

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


def test_submit_direct_onchain_trade_runs_unified_preview_and_static_call_before_broadcast(monkeypatch):
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

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": True,
                    "network": "fuji",
                    "unified_executor_address": executor_address,
                    "runtime_trades": json.loads(_unified_runtime_trades_json(usdc, token_x, token_y)),
                    "execute_runtime_trade": True,
                    "execution_amount": 1_000_000,
                    "amountOutMinUsdc": 1_000_501,
                    "minProfitUsdc": 1,
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
    assert contract.functions.preview_args[3] is False
    assert contract.functions.run_args[3] is False


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
    assert direct_keys == {"USDC:AAA", "USDC:BBB", "USDC:CCC"}
    assert triangular_keys == {"AAA:BBB", "AAA:CCC", "BBB:CCC"}
    assert all(len(group["trades"]) == 1 for group in groups if group["routeKind"] == "usdc_cross_pool")
    assert all(3 <= len(group["trades"]) <= 4 for group in groups if group["routeKind"] == "usdc_triangular")


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
    assert [item["selected"] for item in evaluations] == [True, True]


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


def test_submit_direct_onchain_trade_reports_incomplete_when_token_pair_is_not_in_memory_table(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
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
