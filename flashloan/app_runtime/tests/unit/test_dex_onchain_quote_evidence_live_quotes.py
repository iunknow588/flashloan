import runtime.dex_onchain_quote_evidence as dex_onchain_quote_evidence
import dex.runtime.dex_onchain_quote_evidence as dex_onchain_quote_evidence_runtime
from runtime.dex_onchain_quote_evidence import build_route_onchain_evidence, _unsupported_evidence
from runtime.dex_opportunity_helpers import _merge_onchain_evidence_into_route
from runtime.dex_opportunity_scanner import _scanner_onchain_evidence_provider
from runtime.dex_quote_evidence_helpers import _is_missing_historical_state_error
from core.error_registry import ErrorCode


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

    assert evidence["status"] == "route_unprofitable"
    assert evidence["trustedForExecution"] is False
    assert evidence["executionTrust"] == "live_quoter_verified_readonly"
    assert evidence["hops"][0]["quoteMethod"] == "v3_quoter_quoteExactInputSingle_eth_call"
    assert evidence["hops"][0]["pool"] == pool


def test_live_quote_falls_back_to_latest_when_trigger_block_missing(monkeypatch):
    class FakeEth:
        block_number = 999

        def get_block(self, block_number):
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "_connect_robinhood_family",
        lambda *args, **kwargs: (FakeW3(), "https://rpc.example"),
    )
    pool = "0x" + "5" * 40

    def provider(*, step, amount_in_raw, block_number, **kwargs):
        out_raw = int(amount_in_raw) * 101 // 100
        return {
            "status": "live_verified",
            "quoteMethod": "test_latest_fallback",
            "protocol": "uniswap_v2",
            "pool": step.get("pool"),
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(out_raw),
            "amountIn": "1000",
            "amountOut": format(out_raw / 10**6, "f"),
            "inputDecimals": 6,
            "outputDecimals": 6,
            "priceImpactBps": 0,
        }

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
                    "token0": "0x" + "6" * 40,
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
        quote_hop_provider=provider,
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "live_verified"
    assert evidence["blockNumber"] == 999
    assert evidence["quoteBlockSource"] == "latest_onchain_review"


def test_live_quote_rpc_failure_is_reported_as_failed_verification(monkeypatch):
    def fail_connect(*args, **kwargs):
        raise RuntimeError("Robinhood Testnet RPC unavailable")

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", fail_connect)
    evidence = build_route_onchain_evidence(
        {
            "routeSymbols": ["ETH", "ETH"],
            "quoteAnalysis": {"notionalUsd": 1000.0, "steps": []},
        },
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 1, "observedAt": "2026-08-24T00:00:00+00:00"},
    )

    assert evidence["status"] == "rpc_verification_failed"
    assert evidence["executionTrust"] == "rpc_verification_failed"
    assert any("RPC" in blocker for blocker in evidence["blockers"])
    assert evidence["errorStack"][0]["code"] == int(ErrorCode.DEX_RPC_FAILED)


def test_unsupported_evidence_prefers_liquidity_zero_over_rpc_label():
    evidence = _unsupported_evidence(
        chain_id=46630,
        network="robinhood-testnet",
        route={"routeSymbols": ["WETH", "ETH", "WETH"]},
        observed_at="2026-08-24T00:00:00+00:00",
        reasons=[
            "robinhood testnet v4 pool manager live liquidity is zero",
            "hop 1 live quote failed",
        ],
        trigger_context={},
    )

    assert evidence["status"] == "not_enough_liquidity"
    assert evidence["executionTrust"] == "not_enough_liquidity"
    assert evidence["errorStack"][0]["code"] == int(ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY)


def test_metadata_missing_is_recognized_as_historical_state_gap():
    assert _is_missing_historical_state_error(
        RuntimeError("{'code': -32000, 'message': 'metadata is not found, 107982015'}")
    )
    assert _is_missing_historical_state_error(RuntimeError("missing trie node 0xabc"))
    assert not _is_missing_historical_state_error(RuntimeError("revert: NotEnoughLiquidity(bytes32)"))


def test_metadata_missing_reports_explicit_failure(monkeypatch):
    from types import SimpleNamespace

    class FakeEth:
        block_number = 107982015

        def get_block(self, block_number):
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "_connect_robinhood_family",
        lambda *args, **kwargs: (FakeW3(), "https://rpc.example"),
    )
    pool = "0x" + "9" * 40

    def provider(*, step, amount_in_raw, block_number, **kwargs):
        raise RuntimeError("{'code': -32000, 'message': 'metadata is not found, 107982015'}")

    route = {
        "routeSymbols": ["ETH", "0X937454", "0X862554", "ETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": pool,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "token0Symbol": "ETH",
                    "token0": "0x" + "0" * 40,
                    "token0Decimals": 18,
                    "token1Symbol": "0X937454",
                    "token1": "0x" + "1" * 40,
                    "token1Decimals": 18,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "ETH",
                    "toSymbol": "0X937454",
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "pool": pool,
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 107982015, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=provider,
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "live_quote_failed"
    assert evidence["hops"][0]["status"] == "live_quote_failed"
    assert any("metadata is not found" in blocker for blocker in evidence["blockers"])


def test_direct_evidence_falls_back_to_latest_when_historical_state_missing(monkeypatch):
    from types import SimpleNamespace

    class FakeEth:
        block_number = 999

        def get_block(self, block_number):
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "_connect_robinhood_family",
        lambda *args, **kwargs: (FakeW3(), "https://rpc.example"),
    )
    pool = "0x" + "B" * 40
    seen = []

    def provider(*, step, amount_in_raw, block_number, **kwargs):
        seen.append(block_number)
        if block_number != FakeEth.block_number:
            raise RuntimeError("{'code': -32000, 'message': 'metadata is not found, 107982015'}")
        out_raw = int(amount_in_raw) * 101 // 100
        return {
            "status": "live_verified",
            "quoteMethod": "test_core_latest_fallback",
            "protocol": "uniswap_v4",
            "pool": step.get("pool"),
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(out_raw),
            "amountIn": "1",
            "amountOut": format(out_raw / 10**18, "f"),
            "inputDecimals": 18,
            "outputDecimals": 18,
            "priceImpactBps": 0,
        }

    route = {
        "routeSymbols": ["ETH", "ETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": pool,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "token0Symbol": "ETH",
                    "token0": "0x" + "0" * 40,
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x" + "1" * 40,
                    "token1Decimals": 18,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "ETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "pool": pool,
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 107982015, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=provider,
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "live_verified"
    assert evidence["quoteBlockSource"] == "latest_onchain_review"
    assert FakeEth.block_number in seen
    assert len(seen) == 1
    assert seen[0] == 999


def test_scanner_provider_falls_back_to_latest_when_historical_state_missing(monkeypatch):
    from types import SimpleNamespace

    class FakeEth:
        block_number = 999

        def get_block(self, block_number):
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(
        dex_onchain_quote_evidence_runtime,
        "_connect_robinhood_family",
        lambda *args, **kwargs: (FakeW3(), "https://rpc.example"),
    )
    pool = "0x" + "A" * 40
    seen = []

    def provider(*, step, amount_in_raw, block_number, **kwargs):
        seen.append(block_number)
        if block_number != FakeEth.block_number:
            raise RuntimeError("{'code': -32000, 'message': 'metadata is not found, 107982015'}")
        out_raw = int(amount_in_raw) * 101 // 100
        return {
            "status": "live_verified",
            "quoteMethod": "test_latest_fallback",
            "protocol": "uniswap_v4",
            "pool": step.get("pool"),
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(out_raw),
            "amountIn": "1",
            "amountOut": format(out_raw / 10**18, "f"),
            "inputDecimals": 18,
            "outputDecimals": 18,
            "priceImpactBps": 0,
        }

    route = {
        "routeSymbols": ["ETH", "ETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": pool,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "token0Symbol": "ETH",
                    "token0": "0x" + "0" * 40,
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x" + "1" * 40,
                    "token1Decimals": 18,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "ETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "pool": pool,
                }
            ],
        },
    }

    evidence = _scanner_onchain_evidence_provider(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 107982015, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=provider,
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "live_verified"
    assert evidence["quoteBlockSource"] == "latest_onchain_review"
    assert FakeEth.block_number in seen
    assert len(seen) == 1
    assert seen[0] == 999


def test_merge_onchain_evidence_preserves_full_static_route_when_live_stops_early():
    route = {
        "routeSymbols": ["ETH", "0XC9F9C8", "0X71178B", "ETH"],
        "quoteAnalysis": {
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "0XC9F9C8", "referenceOutputAmount": 0.009995},
                {"hop": 2, "fromSymbol": "0XC9F9C8", "toSymbol": "0X71178B", "referenceOutputAmount": 0.0049825075},
                {"hop": 3, "fromSymbol": "0X71178B", "toSymbol": "ETH", "referenceOutputAmount": 0.000000986536485},
            ]
        },
    }
    evidence = {
        "status": "not_enough_liquidity",
        "hops": [
            {
                "hop": 1,
                "fromSymbol": "ETH",
                "toSymbol": "0XC9F9C8",
                "status": "live_verified",
                "amountOutRaw": "9950000000000000",
                "amountIn": "0.01",
                "amountOut": "0.009995",
            },
            {
                "hop": 2,
                "fromSymbol": "0XC9F9C8",
                "toSymbol": "0X71178B",
                "status": "not_enough_liquidity",
                "liveQuoteError": "robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)",
                "amountIn": "0.009995",
            },
        ],
        "blockers": ["hop 2 0XC9F9C8->0X71178B has not enough liquidity"],
    }

    merged = _merge_onchain_evidence_into_route(route, evidence)

    steps = merged["quoteAnalysis"]["steps"]
    assert [step.get("hop") for step in steps] == [1, 2, 3]
    assert abs(steps[1]["inputAmount"] - 0.009995) < 1e-12
    assert abs(steps[2]["referenceOutputAmount"] - 0.000000986536485) < 1e-12
    assert steps[1]["liveQuoteError"] and "NotEnoughLiquidity" in steps[1]["liveQuoteError"]


def test_merge_fills_failed_liquidity_hop_input_from_previous_output():
    route = {
        "routeSymbols": ["ETH", "0XC9F9C8", "0X71178B", "ETH"],
        "quoteAnalysis": {
            "referenceInputAmount": 0.01,
            "referenceFinalOutputAmount": 0.000000986536485,
            "referenceInputSymbol": "ETH",
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "0XC9F9C8", "inputAmount": 0.01, "outputAmount": 0.009995},
                {"hop": 2, "fromSymbol": "0XC9F9C8", "toSymbol": "0X71178B", "inputAmount": 0.009995, "outputAmount": 0.0049825075},
                {"hop": 3, "fromSymbol": "0X71178B", "toSymbol": "ETH", "inputAmount": 0.0049825075, "outputAmount": 0.000000986536485},
            ],
        },
    }
    evidence = {
        "status": "not_enough_liquidity",
        "hops": [
            {
                "hop": 1,
                "fromSymbol": "ETH",
                "toSymbol": "0XC9F9C8",
                "status": "live_verified",
                "amountOutRaw": "946652893391705934",
                "amountOut": "0.946652893391705934",
            },
            {
                "hop": 2,
                "fromSymbol": "0XC9F9C8",
                "toSymbol": "0X71178B",
                "status": "not_enough_liquidity",
                "amountInRaw": "946652893391705934",
                "liveQuoteError": "robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)",
            },
        ],
        "blockers": ["hop 2 0XC9F9C8->0X71178B has not enough liquidity"],
    }

    merged = _merge_onchain_evidence_into_route(route, evidence)
    steps = merged["quoteAnalysis"]["steps"]
    assert [step.get("hop") for step in steps] == [1, 2, 3]
    assert abs(float(steps[1]["inputAmount"]) - 0.946652893391705934) < 1e-15
    assert abs(float(steps[2]["outputAmount"]) - 0.000000986536485) < 1e-12
    live_hops = merged["live_onchain_evidence"]["hops"]
    failed_hop = next(hop for hop in live_hops if hop["hop"] == 2)
    assert failed_hop["amountIn"] == "0.946652893391705934"
    assert abs(float(failed_hop["inputAmount"]) - 0.946652893391705934) < 1e-15


def test_merge_completes_missing_route_hops_from_pool_set():
    token_map = {
        "ETH": "0x" + "0" * 40,
        "0X71178B": "0x" + "2" * 40,
        "0XC9F9C8": "0x" + "3" * 40,
        "0X862554": "0x" + "4" * 40,
        "0X937454": "0x" + "5" * 40,
    }
    pools = []
    for index, (token_a, token_b) in enumerate(
        [
            ("ETH", "0XC9F9C8"),
            ("0XC9F9C8", "0X71178B"),
            ("0X71178B", "ETH"),
        ],
        start=1,
    ):
        pools.append(
            {
                "pool": "0x" + f"{index:x}" * 64,
                "protocol": "uniswap_v4",
                "token0Symbol": token_a,
                "token0": token_map[token_a],
                "token0Decimals": 18,
                "token1Symbol": token_b,
                "token1": token_map[token_b],
                "token1Decimals": 18,
            }
        )
    route = {
        "routeSymbols": ["ETH", "0XC9F9C8", "0X71178B", "ETH"],
        "routeAddresses": [token_map["ETH"], token_map["0XC9F9C8"], token_map["0X71178B"], token_map["ETH"]],
        "quoteInputs": {"routePools": pools},
        "quoteAnalysis": {
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "0XC9F9C8", "pool": pools[0]["pool"]},
                {
                    "hop": 2,
                    "fromSymbol": "0XC9F9C8",
                    "toSymbol": "0X71178B",
                    "pool": pools[1]["pool"],
                },
            ]
        },
    }
    evidence = {
        "status": "not_enough_liquidity",
        "hops": [
            {
                "hop": 1,
                "fromSymbol": "ETH",
                "toSymbol": "0XC9F9C8",
                "status": "live_verified",
                "amountOutRaw": "946652893391705934",
                "amountOut": "0.946652893391705934",
            },
            {
                "hop": 2,
                "fromSymbol": "0XC9F9C8",
                "toSymbol": "0X71178B",
                "status": "not_enough_liquidity",
                "amountInRaw": "946652893391705934",
                "liveQuoteError": "robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)",
            },
        ],
        "blockers": ["hop 2 0XC9F9C8->0X71178B has not enough liquidity"],
    }

    merged = _merge_onchain_evidence_into_route(route, evidence)
    steps = merged["quoteAnalysis"]["steps"]
    assert [step.get("hop") for step in steps] == [1, 2, 3]
    assert steps[2]["fromSymbol"] == "0X71178B"
    assert steps[2]["toSymbol"] == "ETH"
    assert steps[2]["pool"] == pools[2]["pool"]


def test_merge_replaces_stale_top_level_error_stack_with_evidence_stack():
    route = {
        "routeSymbols": ["ETH", "0XC9F9C8", "0X71178B", "ETH"],
        "errorStack": [{"module": "DEX", "code": 3006, "message": "requires_live_quoter"}],
        "quoteAnalysis": {
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "0XC9F9C8"},
                {"hop": 2, "fromSymbol": "0XC9F9C8", "toSymbol": "0X71178B"},
                {"hop": 3, "fromSymbol": "0X71178B", "toSymbol": "ETH"},
            ]
        },
    }
    evidence_stack = [
        {"module": "DEX", "code": 3003, "message": "hop 2 0XC9F9C8->0X71178B verification failed"},
        {"module": "DEX", "code": 3003, "message": "NotEnoughLiquidity(bytes32)"},
    ]
    evidence = {
        "status": "not_enough_liquidity",
        "errorStack": evidence_stack,
        "hops": [
            {
                "hop": 2,
                "fromSymbol": "0XC9F9C8",
                "toSymbol": "0X71178B",
                "status": "not_enough_liquidity",
                "liveQuoteError": "NotEnoughLiquidity(bytes32)",
            }
        ],
        "blockers": ["hop 2 0XC9F9C8->0X71178B has not enough liquidity"],
    }

    merged = _merge_onchain_evidence_into_route(route, evidence)

    assert merged["errorStack"] == evidence_stack
    assert merged["errorStack"][0]["message"] != "requires_live_quoter"


def test_route_evidence_expands_missing_hops_when_pool_set_is_complete(monkeypatch):
    from types import SimpleNamespace

    class FakeEth:
        block_number = 999

        def get_block(self, block_number):
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
    token_map = {
        "ETH": "0x" + "0" * 40,
        "0X71178B": "0x" + "2" * 40,
        "0XC9F9C8": "0x" + "3" * 40,
    }
    pools = []
    for index, (token_a, token_b) in enumerate(
        [("ETH", "0X71178B"), ("0X71178B", "0XC9F9C8"), ("0XC9F9C8", "ETH")],
        start=1,
    ):
        pools.append(
            {
                "pool": "0x" + ("a" * 40) + f"{index}",
                "dex": "uniswap_v4",
                "protocol": "uniswap_v4",
                "fee": "500" if index in {1, 3} else "3000",
                "token0": token_map[token_a],
                "token1": token_map[token_b],
                "token0Symbol": token_a,
                "token1Symbol": token_b,
                "token0Decimals": 18,
                "token1Decimals": 18,
            }
        )
    route = {
        "routeSymbols": ["ETH", "0X71178B", "0XC9F9C8", "ETH"],
        "routeAddresses": [token_map["ETH"], token_map["0X71178B"], token_map["0XC9F9C8"], token_map["ETH"]],
        "quoteInputs": {
            "routePools": pools,
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "referenceInputAmount": 1.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "ETH",
                    "toSymbol": "0X71178B",
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "pool": pools[0]["pool"],
                }
            ],
        },
    }

    def provider(*, step, amount_in_raw, block_number, **kwargs):
        return {
            "protocol": "uniswap_v4",
            "pool": step.get("pool"),
            "fromSymbol": step.get("fromSymbol"),
            "toSymbol": step.get("toSymbol"),
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(int(amount_in_raw) + 1),
            "amountIn": "1",
            "amountOut": "1",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "quoteMethod": "test_provider",
            "priceImpactBps": 0,
        }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 4663}},
        chain_id=4663,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 1, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=provider,
        skip_execution_size_probe=True,
    )

    assert len(evidence["hops"]) == 3
    assert [hop["hop"] for hop in evidence["hops"]] == [1, 2, 3]


def test_live_quote_uses_latest_block_for_review(monkeypatch):
    class FakeEth:
        block_number = 999

        def get_block(self, block_number):
            assert block_number == 999
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))
    seen = {}

    def quote_hop_provider(**kwargs):
        seen["block_number"] = kwargs["block_number"]
        return {
            "status": "live_verified",
            "quoteMethod": "test_latest_review",
            "protocol": "uniswap_v2",
            "pool": kwargs["step"]["pool"],
            "amountInRaw": str(kwargs["amount_in_raw"]),
            "amountOutRaw": "1003000000",
            "amountIn": "1000",
            "amountOut": "1003",
            "inputDecimals": 6,
            "outputDecimals": 6,
            "priceImpactBps": 0,
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

    assert seen["block_number"] == 999
    assert evidence["status"] == "live_verified"
    assert evidence["blockNumber"] == 999
    assert evidence["triggerBlockNumber"] == 123
    assert evidence["quoteBlockSource"] == "latest_onchain_review"
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
                "priceImpactBps": 0,
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
            "priceImpactBps": 0,
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
                "priceImpactBps": 0,
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
