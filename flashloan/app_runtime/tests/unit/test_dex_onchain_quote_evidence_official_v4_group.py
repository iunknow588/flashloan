from eth_abi.abi import decode, encode
import runtime.dex_onchain_quote_evidence_official_v4 as official_v4


def test_official_v4_arb_address_resolution_prefers_external_config(monkeypatch):
    monkeypatch.delenv("OFFICIAL_V4_ARB_ADDRESS", raising=False)
    monkeypatch.setattr(official_v4, "_chain_data_official_v4_address", lambda field, chain_id=None: "0x" + "c" * 40)
    monkeypatch.setattr(official_v4, "_manifest_official_v4_address", lambda field, chain_id: "0x" + "d" * 40)

    monkeypatch.setenv("OFFICIAL_V4_ARB_ADDRESS", "0x" + "a" * 40)
    assert official_v4._official_v4_arb_address() == "0x" + "a" * 40

    monkeypatch.delenv("OFFICIAL_V4_ARB_ADDRESS", raising=False)
    assert official_v4._official_v4_arb_address() == "0x" + "c" * 40

    monkeypatch.setattr(official_v4, "_chain_data_official_v4_address", lambda field, chain_id=None: None)
    assert official_v4._official_v4_arb_address() == "0x" + "d" * 40


def test_official_v4_mainnet_env_address_resolution(monkeypatch):
    monkeypatch.delenv("OFFICIAL_V4_ARB_ADDRESS", raising=False)
    monkeypatch.delenv("OFFICIAL_V4_COMPOSER_ADDRESS", raising=False)
    monkeypatch.delenv("ROBINHOOD_MAINNET_OFFICIAL_V4_ARB_ADDRESS", raising=False)
    monkeypatch.delenv("ROBINHOOD_MAINNET_OFFICIAL_V4_COMPOSER_ADDRESS", raising=False)
    monkeypatch.setenv("ROBINHOOD_MAINNET_OFFICIAL_V4_ARB_ADDRESS", "0x" + "a" * 40)
    monkeypatch.setenv("ROBINHOOD_MAINNET_OFFICIAL_V4_COMPOSER_ADDRESS", "0x" + "b" * 40)

    assert official_v4._official_v4_arb_address(chain_id=4663) == "0x" + "a" * 40
    assert official_v4._official_v4_composer_address(chain_id=4663) == "0x" + "b" * 40


def _pool(symbol_a: str, addr_a: str, symbol_b: str, addr_b: str, fee: int, pool_id: str) -> dict:
    return {
        "pool": pool_id,
        "token0Symbol": symbol_a,
        "token0": addr_a,
        "token0Decimals": 18,
        "token1Symbol": symbol_b,
        "token1": addr_b,
        "token1Decimals": 18,
        "fee": fee,
        "tickSpacing": 60,
        "hooks": "0x0000000000000000000000000000000000000000",
        "poolKey": {
            "currency0": addr_a,
            "currency1": addr_b,
            "fee": fee,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
        },
    }


def _step(hop: int, from_symbol: str, to_symbol: str, pool_id: str) -> dict:
    return {
        "hop": hop,
        "fromSymbol": from_symbol,
        "toSymbol": to_symbol,
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "pool": pool_id,
    }


def _route(symbols: list[str], addresses: list[str], pools: list[dict]) -> dict:
    steps = [
        _step(1, symbols[0], symbols[1], pools[0]["pool"]),
        _step(2, symbols[1], symbols[2], pools[1]["pool"]),
        _step(3, symbols[2], symbols[3], pools[2]["pool"]),
    ]
    return {
        "routeSymbols": symbols,
        "routeAddresses": addresses,
        "quoteInputs": {"routePools": pools},
        "quoteAnalysis": {"steps": steps},
    }


class FakeRunBest:
    def __init__(self):
        self.calls = []

    def __call__(self, token_z, candidates, amount_in, min_profit, min_deviation):
        self.calls.append((token_z, candidates, amount_in, min_profit, min_deviation))

        class CallResult:
            def call(self, **kwargs):
                result_data = encode(
                    ["uint256", "uint256", "uint256", "uint256", "uint256"],
                    [1000, 500, 1200, 200, 0],
                )
                return (True, 0, 200, result_data)

        return CallResult()


class FakeFunctions:
    def __init__(self):
        self.run_best = FakeRunBest()

    def runBest(self, *args):
        return self.run_best(*args)


class FakeContract:
    def __init__(self):
        self.functions = FakeFunctions()


class FakeCodec:
    @staticmethod
    def decode_abi(types, payload):
        return decode(types, payload)


class FakeEth:
    def __init__(self, contract):
        self.contract_obj = contract

    def contract(self, *args, **kwargs):
        return self.contract_obj


class FakeW3:
    def __init__(self, contract):
        self.eth = FakeEth(contract)
        self.codec = FakeCodec()


def test_official_v4_group_static_hop_aggregates_closed_loop_routes(monkeypatch):
    tz = "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E"
    tb = "0x28cc9a0b94aD34A3A0CB2734c8d58aD95862C6c4"
    ta = "0xB169FE96425783D7BA7a9F905fe8dE5f80E3Fdc8"
    pool_a1 = _pool("TZ", tz, "TB", tb, 3000, "0x" + "1" * 64)
    pool_a2 = _pool("TB", tb, "TA", ta, 3000, "0x" + "2" * 64)
    pool_a3 = _pool("TA", ta, "TZ", tz, 3000, "0x" + "3" * 64)
    pool_b1 = _pool("TZ", tz, "TA", ta, 3000, "0x" + "4" * 64)
    pool_b2 = _pool("TA", ta, "TB", tb, 3000, "0x" + "5" * 64)
    pool_b3 = _pool("TB", tb, "TZ", tz, 3000, "0x" + "6" * 64)
    route_a = _route(["TZ", "TB", "TA", "TZ"], [tz, tb, ta, tz], [pool_a1, pool_a2, pool_a3])
    route_b = _route(["TZ", "TA", "TB", "TZ"], [tz, ta, tb, tz], [pool_b1, pool_b2, pool_b3])

    contract = FakeContract()
    w3 = FakeW3(contract)
    monkeypatch.setattr(official_v4, "_official_v4_arb_address", lambda chain_id=None: "0x" + "a" * 40)
    monkeypatch.setattr(official_v4, "_official_v4_composer_address", lambda chain_id=None: "0x" + "b" * 40)

    result = official_v4.official_v4_arb_static_hop_rows(
        w3=w3,
        route=route_a,
        pools={pool["pool"].lower(): pool for pool in [pool_a1, pool_a2, pool_a3]},
        steps=route_a["quoteAnalysis"]["steps"],
        amount_in_raw=10**17,
        chain_id=46630,
        routes_group=[route_a, route_b],
    )

    assert result is not None
    hop_rows, selected_symbols, selected_addresses, _selected_steps = result
    assert len(hop_rows) == 3
    assert selected_symbols == ["TZ", "TB", "TA", "TZ"]
    assert selected_addresses == [tz, tb, ta, tz]
    assert len(contract.functions.run_best.calls) == 1
    token_z, candidates, _amount, _min_profit, _min_deviation = contract.functions.run_best.calls[0]
    assert token_z.lower() == tz.lower()
    assert len(candidates) == 1
    assert len(candidates[0][0]) == 4
    assert len(candidates[0][1]) == 4


def test_official_v4_group_static_hop_falls_back_without_composer(monkeypatch):
    tz = "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E"
    tb = "0x28cc9a0b94aD34A3A0CB2734c8d58aD95862C6c4"
    ta = "0xB169FE96425783D7BA7a9F905fe8dE5f80E3Fdc8"
    pools = [
        _pool("TZ", tz, "TB", tb, 3000, "0x" + "1" * 64),
        _pool("TB", tb, "TA", ta, 3000, "0x" + "2" * 64),
        _pool("TA", ta, "TZ", tz, 3000, "0x" + "3" * 64),
    ]
    route_a = _route(["TZ", "TB", "TA", "TZ"], [tz, tb, ta, tz], pools)

    class FakeRunArbitrageBest:
        def __call__(self, *args):
            class CallResult:
                    def call(self, **kwargs):
                        return (1000, 500, 1200, 200, 1)
            return CallResult()

    class FakeArbFunctions:
        def runArbitrageBest(self, *args):
            return FakeRunArbitrageBest()(*args)

    class FakeArbContract:
        def __init__(self):
            self.functions = FakeArbFunctions()

    class FakeEth2:
        def __init__(self, contract):
            self.contract_obj = contract

        def contract(self, *args, **kwargs):
            return self.contract_obj

    class FakeW32:
        def __init__(self, contract):
            self.eth = FakeEth2(contract)
            self.codec = FakeCodec()

    w3 = FakeW32(FakeArbContract())
    monkeypatch.setattr(official_v4, "_official_v4_arb_address", lambda chain_id=None: "0x" + "a" * 40)
    monkeypatch.setattr(official_v4, "_official_v4_composer_address", lambda chain_id=None: None)

    result = official_v4.official_v4_arb_static_hop_rows(
        w3=w3,
        route=route_a,
        pools={pool["pool"].lower(): pool for pool in pools},
        steps=route_a["quoteAnalysis"]["steps"],
        amount_in_raw=10**17,
        chain_id=46630,
    )

    assert result is not None
    _hop_rows, selected_symbols, _selected_addresses, _selected_steps = result
    assert selected_symbols == ["TZ", "TA", "TB", "TZ"]
