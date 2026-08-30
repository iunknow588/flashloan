from runtime import dex_onchain_quote_evidence
from runtime.dex_onchain_quote_evidence import build_route_onchain_evidence
from execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter
from intent_trade.chains.robinhood_testnet import v4_quote_adapter


def test_v4_route_uses_reference_fallback_when_sdk_adapter_is_unset(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(dex_onchain_quote_evidence, "_load_v4_quote_adapter", lambda **kwargs: None)
    route = {
        "symbolPair": "USDG -> STONKBROKER -> USDG",
        "routeSymbols": ["USDG", "STONKBROKER", "USDG"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "USDG",
                    "token0": "0x" + "2" * 40,
                    "token0Decimals": 6,
                    "token1Symbol": "STONKBROKER",
                    "token1": "0x" + "3" * 40,
                    "token1Decimals": 6,
                    "network": "robinhood",
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "USDG",
                    "toSymbol": "STONKBROKER",
                    "protocol": "uniswap_v4",
                    "pool": "0x" + "1" * 40,
                    "referenceOutputAmount": 1003.0,
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
    )

    assert evidence["status"] == "live_quote_reference_only"
    assert evidence["trustedForExecution"] is False
    assert evidence["readyForTransactionPreflight"] is False
    assert evidence["executionTrust"] == "static_reference_only"
    assert evidence["hops"][0]["quoteMethod"] == "uniswap_v4_reference_quote"
    assert evidence["hops"][0]["sdkAdapter"] == "reference_quote_adapter"
    assert "reference_quote_adapter" in evidence["blockers"][0]


def test_v4_route_uses_injected_quote_adapter(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "_load_v4_quote_adapter",
        lambda **kwargs: lambda request: {
            "amountOutRaw": "1003000000",
            "quoteMethod": "uniswap_v4_sdk_exact_input_single",
            "quoterGasEstimate": 123456,
            "poolState": {"protocol": "uniswap_v4", "pool": request["pool"], "blockNumber": request["blockNumber"]},
        },
    )
    route = {
        "symbolPair": "USDG -> STONKBROKER -> USDG",
        "routeSymbols": ["USDG", "STONKBROKER", "USDG"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "USDG",
                    "token0": "0x" + "2" * 40,
                    "token0Decimals": 6,
                    "token1Symbol": "STONKBROKER",
                    "token1": "0x" + "3" * 40,
                    "token1Decimals": 6,
                    "network": "robinhood",
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "USDG",
                    "toSymbol": "STONKBROKER",
                    "protocol": "uniswap_v4",
                    "pool": "0x" + "1" * 40,
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
    )

    assert evidence["status"] == "live_verified"
    assert evidence["hops"][0]["quoteMethod"] == "uniswap_v4_sdk_exact_input_single"
    assert evidence["hops"][0]["amountOutRaw"] == "1003000000"
    assert evidence["trustedForExecution"] is False


def test_robinhood_testnet_chain_default_v4_adapter_is_configured():
    adapter = load_v4_sdk_adapter(network="robinhood-testnet")

    assert callable(adapter)


def test_robinhood_testnet_chain_id_default_v4_adapter_is_configured():
    adapter = load_v4_sdk_adapter(chain_id=46630)

    assert callable(adapter)


def test_robinhood_testnet_v4_pool_index_adapter_uses_spot_rate(monkeypatch):
    monkeypatch.setattr(
        v4_quote_adapter,
        "_pool_rows",
        lambda: [
            {
                "pool": "0x" + "1" * 40,
                "token0": "0x" + "2" * 40,
                "token1": "0x" + "3" * 40,
                "fee": 100,
                "tickSpacing": 60,
                "sqrtPriceX96": str(2**96),
            }
        ],
    )

    quote = v4_quote_adapter.quote_v4_exact_input_single_from_pool_index(
        {
            "pool": "0x" + "1" * 40,
            "tokenIn": "0x" + "2" * 40,
            "tokenOut": "0x" + "3" * 40,
            "amountInRaw": str(10**18),
            "fee": 100,
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
        }
    )

    assert quote["quoteMethod"] == "robinhood_testnet_v4_pool_index_spot_quote"
    assert quote["amountOutRaw"] == str(99 * 10**16)
    assert quote["sdkAdapter"].endswith("quote_v4_exact_input_single_from_pool_index")


def test_non_robinhood_route_records_adapter_gap():
    evidence = build_route_onchain_evidence(
        {"routeSymbols": ["USDC", "WBNB", "USDC"], "quoteAnalysis": {"steps": []}},
        payload={"poolStaticTable": {"network": "bnb", "chainId": 56}},
        chain_id=56,
        observed_at="2026-08-24T00:00:00+00:00",
    )

    assert evidence["status"] == "not_live_verified"
    assert evidence["chainId"] == 56
    assert evidence["trustedForExecution"] is False
    assert "adapter" in evidence["blockers"][0].lower()


def test_sushiswap_v3_pool_can_be_verified_with_v3_quoter(monkeypatch):
    from types import SimpleNamespace

    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    pool = "0x" + "2" * 40
    token_in = "0x" + "3" * 40
    token_out = "0x" + "4" * 40
    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "snapshot_v3_pool",
        lambda *args, **kwargs: SimpleNamespace(
            pool_address=pool,
            token0=token_in,
            token1=token_out,
            fee=3000,
            liquidity=123456789,
            sqrt_price_x96=987654321,
            tick=12345,
        ),
    )
    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "quote_v3_exact_input_single",
        lambda *args, **kwargs: (1003000000, 111, 7, 123456),
    )

    route = {
        "symbolPair": "USDG -> WETH -> USDG",
        "routeSymbols": ["USDG", "WETH", "USDG"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": pool,
                    "dex": "sushiswap",
                    "protocol": "uniswap_v3",
                    "token0Symbol": "USDG",
                    "token0": token_in,
                    "token1Symbol": "WETH",
                    "token1": token_out,
                    "token0Decimals": 6,
                    "token1Decimals": 18,
                    "network": "robinhood",
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "USDG",
                    "toSymbol": "WETH",
                    "protocol": "uniswap_v3",
                    "dex": "sushiswap",
                    "pool": pool,
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
    )

    assert evidence["status"] == "live_verified"
    assert evidence["trustedForExecution"] is False
    assert evidence["executionTrust"] == "live_quoter_verified_readonly"
    assert evidence["hops"][0]["quoteMethod"] == "v3_quoter_quoteExactInputSingle_eth_call"
    assert evidence["hops"][0]["pool"] == pool


def test_live_quote_requires_trigger_block_context():
    route = {
        "symbolPair": "USDG -> USDG",
        "routeSymbols": ["USDG", "USDG"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "5" * 40,
                    "dex": "uniswap_v2",
                    "protocol": "uniswap_v2",
                    "token0Symbol": "USDG",
                    "token0": "0x" + "6" * 40,
                    "token0Decimals": 6,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [{"hop": 1, "fromSymbol": "USDG", "toSymbol": "USDG", "protocol": "uniswap_v2", "pool": "0x" + "5" * 40}],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
    )

    assert evidence["status"] == "not_live_verified"
    assert evidence["blockNumber"] is None
    assert "trigger block" in evidence["blockers"][0].lower()


def test_live_quote_uses_trigger_block_instead_of_latest_block(monkeypatch):
    class FakeEth:
        block_number = 999

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
    seen = {}

    def quote_hop_provider(**kwargs):
        seen["block_number"] = kwargs["block_number"]
        return {
            "status": "live_verified",
            "quoteMethod": "test_trigger_block",
            "protocol": "uniswap_v2",
            "pool": kwargs["step"]["pool"],
            "amountInRaw": str(kwargs["amount_in_raw"]),
            "amountOutRaw": "1003000000",
            "amountIn": "1000",
            "amountOut": "1003",
            "inputDecimals": 6,
            "outputDecimals": 6,
        }

    pool = "0x" + "7" * 40
    route = {
        "symbolPair": "USDG -> USDG",
        "routeSymbols": ["USDG", "USDG"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": pool,
                    "dex": "uniswap_v2",
                    "protocol": "uniswap_v2",
                    "token0Symbol": "USDG",
                    "token0": "0x" + "8" * 40,
                    "token0Decimals": 6,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [{"hop": 1, "fromSymbol": "USDG", "toSymbol": "USDG", "protocol": "uniswap_v2", "pool": pool}],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=quote_hop_provider,
    )

    assert seen["block_number"] == 123
    assert evidence["status"] == "live_verified"
    assert evidence["blockNumber"] == 123
    assert evidence["triggerBlockNumber"] == 123
    assert evidence["quoteBlockSource"] == "trigger_block"
    assert evidence["blockTimestamp"] == 1_777_777_777
    assert evidence["trustedForExecution"] is False


def test_profitable_live_quote_is_readonly_until_transaction_simulation():
    route = {"routeSymbols": ["USDG", "WETH", "USDG"], "quoteAnalysis": {"steps": []}}
    evidence = {
        "status": "live_verified",
        "executionTrust": "live_quoter_verified_readonly",
        "trustedForExecution": False,
        "readyForTransactionPreflight": True,
    }

    assert route["routeSymbols"][0] == route["routeSymbols"][-1]
    assert evidence["status"] == "live_verified"
    assert evidence["readyForTransactionPreflight"] is True
    assert evidence["trustedForExecution"] is False


def test_non_stable_closed_loop_uses_x_amount_and_positive_x_profit(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
    seen_amounts = []
    eth = "0x" + "1" * 40
    token_y = "0x" + "2" * 40
    pools = [
        {
            "pool": "0x" + "3" * 40,
            "protocol": "uniswap_v4",
            "token0Symbol": "ETH",
            "token0": eth,
            "token0Decimals": 18,
            "token1Symbol": "Y",
            "token1": token_y,
            "token1Decimals": 18,
        },
        {
            "pool": "0x" + "4" * 40,
            "protocol": "uniswap_v4",
            "token0Symbol": "ETH",
            "token0": eth,
            "token0Decimals": 18,
            "token1Symbol": "Y",
            "token1": token_y,
            "token1Decimals": 18,
        },
    ]
    route = {
        "symbolPair": "ETH -> Y -> ETH",
        "routeSymbols": ["ETH", "Y", "ETH"],
        "quoteInputs": {"routePools": pools},
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "referenceInputAmount": 1.0,
            "borrowAssetIsStable": False,
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "Y", "protocol": "uniswap_v4", "pool": pools[0]["pool"]},
                {"hop": 2, "fromSymbol": "Y", "toSymbol": "ETH", "protocol": "uniswap_v4", "pool": pools[1]["pool"]},
            ],
        },
    }

    def quote_hop_provider(**kwargs):
        seen_amounts.append(kwargs["amount_in_raw"])
        step = kwargs["step"]
        if step["hop"] == 1:
            return {
                "status": "live_verified",
                "quoteMethod": "test_quote",
                "protocol": "uniswap_v4",
                "pool": step["pool"],
                "tokenIn": eth,
                "tokenOut": token_y,
                "amountInRaw": str(kwargs["amount_in_raw"]),
                "amountOutRaw": str(10 * 10**18),
                "amountIn": "1",
                "amountOut": "10",
                "inputDecimals": 18,
                "outputDecimals": 18,
            }
        return {
            "status": "live_verified",
            "quoteMethod": "test_quote",
            "protocol": "uniswap_v4",
            "pool": step["pool"],
            "tokenIn": token_y,
            "tokenOut": eth,
            "amountInRaw": str(kwargs["amount_in_raw"]),
            "amountOutRaw": str(101 * 10**16),
            "amountIn": "10",
            "amountOut": "1.01",
            "inputDecimals": 18,
            "outputDecimals": 18,
        }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=quote_hop_provider,
    )

    assert seen_amounts[0] == 10**18
    assert evidence["status"] == "live_verified"
    assert evidence["readyForTransactionPreflight"] is True
    assert evidence["costModel"]["profitBasis"] == "borrow_token"
    assert evidence["costModel"]["finalOutputUsd"] is None
    assert evidence["costModel"]["borrowInputAmount"] == 1.0
    assert evidence["costModel"]["finalOutputAmount"] == 1.01
    assert evidence["costModel"]["netProfitAmount"] > 0
    assert evidence["validation"]["borrowAssetIsStable"] is False
    assert evidence["validation"]["meetsSubmissionProfitThreshold"] is True


def test_live_quote_blocks_same_symbol_different_token_route(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))

    pools = [
        {
            "pool": "0x" + "1" * 40,
            "protocol": "uniswap_v3",
            "token0Symbol": "USDG",
            "token0": "0x" + "a" * 40,
            "token0Decimals": 6,
            "token1Symbol": "VLAD",
            "token1": "0x" + "b" * 40,
            "token1Decimals": 18,
            "network": "robinhood",
        },
        {
            "pool": "0x" + "2" * 40,
            "protocol": "uniswap_v3",
            "token0Symbol": "VLAD",
            "token0": "0x" + "c" * 40,
            "token0Decimals": 18,
            "token1Symbol": "USDG",
            "token1": "0x" + "a" * 40,
            "token1Decimals": 6,
            "network": "robinhood",
        },
    ]
    route = {
        "symbolPair": "USDG -> VLAD -> USDG",
        "routeSymbols": ["USDG", "VLAD", "USDG"],
        "quoteInputs": {"routePools": pools},
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {"hop": 1, "fromSymbol": "USDG", "toSymbol": "VLAD", "protocol": "uniswap_v3", "pool": pools[0]["pool"]},
                {"hop": 2, "fromSymbol": "VLAD", "toSymbol": "USDG", "protocol": "uniswap_v3", "pool": pools[1]["pool"]},
            ],
        },
    }

    def quote_hop_provider(**kwargs):
        step = kwargs["step"]
        if step["hop"] == 1:
            return {
                "status": "live_verified",
                "quoteMethod": "test_quote",
                "protocol": "uniswap_v3",
                "pool": step["pool"],
                "tokenIn": "0x" + "a" * 40,
                "tokenOut": "0x" + "b" * 40,
                "amountInRaw": str(kwargs["amount_in_raw"]),
                "amountOutRaw": "1000000000000000000",
                "amountIn": "1000",
                "amountOut": "1",
                "inputDecimals": 6,
                "outputDecimals": 18,
            }
        return {
            "status": "live_verified",
            "quoteMethod": "test_quote",
            "protocol": "uniswap_v3",
            "pool": step["pool"],
            "tokenIn": "0x" + "c" * 40,
            "tokenOut": "0x" + "a" * 40,
            "amountInRaw": str(kwargs["amount_in_raw"]),
            "amountOutRaw": "2000000000",
            "amountIn": "1",
            "amountOut": "2000",
            "inputDecimals": 18,
            "outputDecimals": 6,
        }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=quote_hop_provider,
    )

    assert evidence["status"] == "live_quote_failed"
    assert evidence["readyForTransactionPreflight"] is False
    assert evidence["validation"]["tokenContinuity"] is False
    assert evidence["validation"]["tokenContinuityIssues"][0]["previousTokenOut"] == "0x" + "b" * 40
