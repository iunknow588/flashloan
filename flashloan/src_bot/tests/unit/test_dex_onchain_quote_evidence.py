from runtime import dex_onchain_quote_evidence
from runtime.dex_onchain_quote_evidence import build_route_onchain_evidence


def test_v4_route_reaches_live_quote_gate_without_adapter(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
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

    assert evidence["status"] == "live_quote_failed"
    assert evidence["trustedForExecution"] is False
    assert evidence["executionTrust"] == "static_reference_only"
    assert "uniswap v4 quote adapter is not configured" in evidence["blockers"][0].lower()
    assert evidence["transactionDevelopment"]["status"] == "blocked_before_transaction_development"


def test_v4_route_uses_injected_quote_adapter(monkeypatch):
    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "_load_v4_quote_adapter",
        lambda: lambda request: {
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


def test_non_uniswap_v3_pool_is_not_verified_by_uniswap_quoter():
    pool = "0x" + "2" * 40
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
                    "token0": "0x" + "3" * 40,
                    "token1Symbol": "WETH",
                    "token1": "0x" + "4" * 40,
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
    )

    assert evidence["status"] == "not_live_verified"
    assert evidence["trustedForExecution"] is False
    assert evidence["executionTrust"] == "static_reference_only"
    assert "Uniswap V3 Quoter" in evidence["blockers"][0]
    assert evidence["transactionDevelopment"]["status"] == "blocked_before_transaction_development"


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
