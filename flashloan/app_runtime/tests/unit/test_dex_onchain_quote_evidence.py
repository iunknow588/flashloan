import pytest

from runtime import dex_onchain_quote_evidence
from runtime.dex_onchain_quote_evidence import build_route_onchain_evidence
from runtime.dex_opportunity_helpers import _merge_onchain_evidence_into_route
from runtime.dex_quote_evidence_helpers import dex_error_stack_from_state
from runtime.dex_quote_evidence_helpers import _token_decimals
from runtime.dex_quote_evidence_helpers import _robinhood_network_for_chain_id
from runtime.dex_quote_evidence_helpers import _offchain_initial_probe_amount_raw
from bot.execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter
from core.error_registry import ErrorCode
from dex.intent_trade.chains.robinhood_testnet import v4_quote_adapter
from dex.intent_trade.chains.robinhood_testnet import v4_pool_liquidity
from dex.intent_trade.chains.robinhood_testnet import trigger_context
from eth_abi import encode as abi_encode


_POOL_ID = "0xea84630b" + "1ccfd69145b791334c55a7d8be1565910cb6e290c489413c977fd9c5"


def test_robinhood_testnet_trigger_context_uses_confirmed_live_head(monkeypatch):
    class FakeEth:
        block_number = 100

        def get_block(self, block_number):
            assert block_number == 97
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setenv("DEX_ONCHAIN_TRIGGER_CONFIRMATIONS", "3")
    monkeypatch.setattr(
        v4_quote_adapter,
        "_connect_chain_rpc",
        lambda network, chain_id: (FakeW3(), "https://rpc.example"),
    )

    context = trigger_context.capture_robinhood_testnet_trigger_context(
        chain_id=46630,
        network="robinhood-testnet",
        observed_at="2026-09-02T00:00:00+00:00",
    )

    assert context["source"] == "scan_trigger_chain_head_confirmed"
    assert context["triggerBlockNumber"] == 97
    assert context["headBlockNumber"] == 100
    assert context["blockTimestamp"] == 1_777_777_777


def test_token_decimals_refuses_symbol_only_assumption():
    with pytest.raises(ValueError, match="missing exact token decimals"):
        _token_decimals(w3=None, address=None, symbol="USDC", cache={})


def test_token_decimals_accepts_native_zero_address_without_rpc():
    assert _token_decimals(w3=None, address="0x0000000000000000000000000000000000000000", symbol="ETH", cache={}) == 18


def test_offchain_initial_probe_uses_1000u_price_anchor():
    route = {
        "routeSymbols": ["WETH", "USDG", "FOO", "WETH"],
        "quoteAnalysis": {
            "inputSymbol": "WETH",
            "steps": [],
        },
    }
    amount_raw, basis, reason, reasons = _offchain_initial_probe_amount_raw(
        route,
        input_decimals=18,
        price_map={"WETH": 1000.0},
    )
    assert amount_raw == 10**18
    assert basis == "borrow_token"
    assert "stablecoin_usd_1000u_anchor" in reasons
    assert "stablecoin_usd_1000u_anchor" in reason


def test_offchain_initial_probe_falls_back_to_one_basic_unit_without_price():
    route = {
        "routeSymbols": ["FOO", "BAR", "BAZ", "FOO"],
        "quoteAnalysis": {
            "inputSymbol": "FOO",
            "steps": [],
        },
    }
    amount_raw, basis, reason, reasons = _offchain_initial_probe_amount_raw(
        route,
        input_decimals=6,
        price_map={},
    )
    assert amount_raw == 10**6
    assert basis == "borrow_token"
    assert "one_basic_unit_fallback" in reasons
    assert "one_basic_unit_fallback" in reason


def test_offchain_initial_probe_ignores_existing_probe_fields():
    route = {
        "routeSymbols": ["USDG", "FOO", "USDG"],
        "offchainInitialProbeAmountRaw": "500",
        "recommendedBorrowInputAmountRaw": "400",
        "quoteAnalysis": {
            "inputSymbol": "USDG",
            "steps": [],
            "initialProbeAmountRaw": "300",
            "sizeAnalysis": {
                "offchainInitialProbeAmountRaw": "999",
                "recommendedBorrowInputAmountRaw": "888",
                "maxExecutableAmountRaw": "777",
            },
        },
    }
    amount_raw, basis, reason, reasons = _offchain_initial_probe_amount_raw(
        route,
        input_decimals=6,
        price_map={"USDG": 1.0},
    )
    assert amount_raw == 1000 * 10**6
    assert basis == "borrow_token"
    assert "stablecoin_usd_1000u_anchor" in reasons
    assert "stablecoin_usd_1000u_anchor" in reason


def test_offchain_initial_probe_does_not_infer_stable_usd_price_from_reference_notional():
    route = {
        "routeSymbols": ["WETH", "USDG", "FOO", "WETH"],
        "quoteAnalysis": {
            "inputSymbol": "WETH",
            "referenceInputAmount": 2.5,
            "notionalUsd": 2500.0,
            "steps": [],
        },
    }
    amount_raw, basis, reason, reasons = _offchain_initial_probe_amount_raw(
        route,
        input_decimals=18,
        price_map={},
    )
    assert amount_raw == 10**18
    assert basis == "borrow_token"
    assert "one_basic_unit_fallback" in reasons
    assert "one_basic_unit_fallback" in reason


def test_offchain_initial_probe_caps_by_pool_borrow_liquidity():
    route = {
        "routeSymbols": ["USDG", "FOO", "USDG"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "token0Symbol": "USDG",
                    "token1Symbol": "FOO",
                    "token0Amount": "100",
                }
            ]
        },
        "quoteAnalysis": {
            "inputSymbol": "USDG",
            "steps": [],
        },
    }
    amount_raw, basis, reason, reasons = _offchain_initial_probe_amount_raw(
        route,
        input_decimals=6,
        price_map={"USDG": 1.0},
    )
    assert amount_raw == 2 * 10**6
    assert basis == "borrow_token"
    assert "liquidity_cap_pool_reserve_2pct" in reasons
    assert reason == "liquidity_cap_pool_reserve_2pct"


def test_offchain_initial_probe_never_caps_below_one_basic_unit():
    route = {
        "routeSymbols": ["USDG", "FOO", "USDG"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "token0Symbol": "USDG",
                    "token1Symbol": "FOO",
                    "token0Amount": "5",
                }
            ]
        },
        "quoteAnalysis": {
            "inputSymbol": "USDG",
            "steps": [],
        },
    }
    amount_raw, basis, reason, reasons = _offchain_initial_probe_amount_raw(
        route,
        input_decimals=6,
        price_map={"USDG": 1.0},
    )
    assert amount_raw == 10**6
    assert basis == "borrow_token"
    assert "liquidity_cap_pool_reserve_2pct" in reasons
    assert reason == "liquidity_cap_pool_reserve_2pct"


def test_robinhood_network_for_chain_id_prefers_testnet_chain_metadata():
    assert _robinhood_network_for_chain_id(46630, "robinhood") == "robinhood-testnet"
    assert _robinhood_network_for_chain_id(46630, "chain_46630") == "robinhood-testnet"
    assert _robinhood_network_for_chain_id(None, "robinhood-testnet") == "robinhood-testnet"
    assert _robinhood_network_for_chain_id(4663, "robinhood-testnet") == "robinhood"


def test_dex_error_stack_from_state_returns_only_module_code_message():
    rows = dex_error_stack_from_state(
        "pool_not_initialized",
        message="V4 pool is not initialized",
        details={"routeKey": "ETH->0XC9F9C8->ETH"},
    )
    assert rows[0] == {
        "module": "DEX",
        "code": int(ErrorCode.DEX_POOL_NOT_INITIALIZED),
        "message": "V4 pool is not initialized",
    }


def test_merge_onchain_evidence_clears_static_reference_profit_for_not_enough_liquidity():
    route = {
        "routeSymbols": ["A", "B", "A"],
        "quoteAnalysis": {
            "referenceFinalOutputAmount": 2,
            "referenceFinalOutputUsd": 3,
            "referenceGrossProfitUsd": 1,
            "referenceGrossProfitAmount": 1,
            "referenceNetProfitAmount": -1,
            "referenceNetReturnBps": -500,
        },
    }
    merged = _merge_onchain_evidence_into_route(
        route,
        {
            "status": "not_enough_liquidity",
            "blockers": ["hop 1 A -> B has no LP liquidity on chain"],
        },
    )
    analysis = merged["quoteAnalysis"]
    assert analysis["onchainEvidenceStatus"] == "not_enough_liquidity"
    assert analysis["referenceFinalOutputAmount"] is None
    assert analysis["referenceFinalOutputUsd"] is None
    assert analysis["referenceGrossProfitUsd"] is None
    assert analysis["referenceGrossProfitAmount"] is None
    assert analysis["referenceNetProfitAmount"] is None
    assert analysis["referenceNetReturnBps"] is None


def test_merge_onchain_evidence_does_not_use_requested_amount_as_best_amount():
    route = {
        "routeSymbols": ["A", "B", "A"],
        "quoteAnalysis": {},
    }
    merged = _merge_onchain_evidence_into_route(
        route,
        {
            "status": "live_verified",
            "costModel": {
                "requestedBorrowInputAmount": 1000.0,
                "netProfitAmount": 1.0,
                "finalOutputAmount": 1001.0,
            },
        },
    )
    analysis = merged["quoteAnalysis"]
    assert analysis["referenceInputAmount"] == 1000.0
    assert analysis["bestAmount"] is None


def test_v4_route_blocks_when_sdk_adapter_is_unset(monkeypatch):
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

    assert evidence["status"] == "adapter_required"
    assert evidence["trustedForExecution"] is False
    assert evidence["readyForTransactionPreflight"] is False
    assert evidence["executionTrust"] == "static_reference_only"
    assert len(evidence["hops"]) == 1
    assert evidence["hops"][0]["status"] == "adapter_required"
    assert evidence["hops"][0]["executionTrust"] == "adapter_required"
    assert evidence["hops"][0]["amountOutRaw"] is None
    assert evidence["validation"]["adapterRequired"] is True
    assert any("quote adapter is required" in blocker.lower() for blocker in evidence["blockers"])
    hop_stamp = evidence["hops"][0]["errorStack"][0]
    assert set(hop_stamp.keys()) == {"module", "code", "message"}
    assert hop_stamp["module"] == "DEX"
    assert "USDG->STONKBROKER" in hop_stamp["message"]
    evidence_stamp = evidence["errorStack"][0]
    assert set(evidence_stamp.keys()) == {"module", "code", "message"}
    assert evidence_stamp["module"] == "DEX"


def test_v4_route_reports_not_enough_liquidity_as_business_result(monkeypatch):
    class FakeEth:
        block_number = 123

        def contract(self, **kwargs):
            return None

        def get_block(self, block_number):
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", lambda: (FakeW3(), "https://rpc.example"))

    def fake_quote_hop_provider(*, w3, quoter, step, pool, amount_in_raw, block_number, token_cache):
        raise RuntimeError("robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)")

    route = {
        "symbolPair": "USDG -> STONKBROKER",
        "routeSymbols": ["USDG", "STONKBROKER"],
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
        quote_hop_provider=fake_quote_hop_provider,
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "not_enough_liquidity"
    assert evidence["trustedForExecution"] is False
    assert evidence["readyForTransactionPreflight"] is False
    assert evidence["executionTrust"] == "not_enough_liquidity"
    assert len(evidence["hops"]) == 1
    assert evidence["hops"][0]["status"] == "not_enough_liquidity"
    assert evidence["hops"][0]["executionTrust"] == "not_enough_liquidity"
    assert evidence["hops"][0]["amountOutRaw"] is None
    assert evidence["validation"]["allHopsQuoted"] is False
    assert evidence["validation"]["notEnoughLiquidity"] is True
    assert any("not enough liquidity" in blocker.lower() for blocker in evidence["blockers"])
    assert evidence["hops"][0]["errorStack"][0]["code"] == int(ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY)


def test_v4_route_short_circuits_no_lp_liquidity_before_rpc_connect(monkeypatch):
    def unexpected_rpc_connect(*args, **kwargs):
        raise AssertionError("RPC should not be contacted when liquidity is already known missing")

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", unexpected_rpc_connect)

    route = {
        "symbolPair": "WETH -> ETH -> 0XC9F9C8 -> WETH",
        "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "WETH",
                    "token0": "0x" + "2" * 40,
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x" + "3" * 40,
                    "token1Decimals": 18,
                    "network": "robinhood",
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "warnings": ["hop_1_not_enough_liquidity"],
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "pool": "0x" + "1" * 40,
                    "executionTrust": "not_enough_liquidity",
                    "liveQuoteError": "robinhood testnet v4 pool manager live liquidity is zero",
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "not_enough_liquidity"
    assert evidence["executionTrust"] == "not_enough_liquidity"
    assert evidence["trustedForExecution"] is False
    assert any("no LP liquidity" in blocker for blocker in evidence["blockers"])


def test_v4_route_short_circuits_warnings_only_no_lp_liquidity_to_business_status(monkeypatch):
    def unexpected_rpc_connect(*args, **kwargs):
        raise AssertionError("RPC should not be contacted when route warnings already report zero liquidity")

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", unexpected_rpc_connect)

    route = {
        "symbolPair": "WETH -> ETH -> 0XC9F9C8 -> WETH",
        "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "WETH",
                    "token0": "0x" + "2" * 40,
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x" + "3" * 40,
                    "token1Decimals": 18,
                    "network": "robinhood",
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "warnings": [
                "hop_1_not_enough_liquidity",
                "hop_1_live_quote_error:robinhood testnet v4 pool manager live liquidity is zero",
                "route_truncated_at_hop_1_not_enough_liquidity",
            ],
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "pool": "0x" + "1" * 40,
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "not_enough_liquidity"
    assert evidence["executionTrust"] == "not_enough_liquidity"
    assert any("no LP liquidity" in blocker for blocker in evidence["blockers"])


def test_v4_route_cache_missing_pool_does_not_short_circuit_as_no_lp(monkeypatch):
    def unexpected_rpc_connect(*args, **kwargs):
        raise RuntimeError("rpc unavailable in test")

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", unexpected_rpc_connect)
    monkeypatch.setattr(
        v4_pool_liquidity,
        "load_robinhood_testnet_v4_pool_liquidity",
        lambda: {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "pools": [{"poolId": "0x" + "a" * 64}],
            "pool_keys": [],
        },
    )

    route = {
        "symbolPair": "WETH -> ETH -> 0XC9F9C8 -> WETH",
        "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": _POOL_ID,
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "WETH",
                    "token0": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x0000000000000000000000000000000000000000",
                    "token1Decimals": 18,
                    "network": "robinhood-testnet",
                }
            ]
        },
        "quoteAnalysis": {
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "pool": _POOL_ID,
                }
            ]
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-09-17T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 120202493, "observedAt": "2026-09-17T00:00:00+00:00"},
        skip_execution_size_probe=True,
    )

    assert evidence["status"] not in {"not_enough_liquidity", "pool_not_initialized"}
    assert evidence["executionTrust"] not in {"not_enough_liquidity", "pool_not_initialized"}
    assert not any("has no LP liquidity in cached evidence" in blocker for blocker in evidence.get("blockers") or [])


def test_v4_route_short_circuits_missing_effective_pool_before_rpc_connect(monkeypatch):
    def unexpected_rpc_connect(*args, **kwargs):
        raise AssertionError("RPC should not be contacted when a step has no effective pool id")

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood", unexpected_rpc_connect)

    route = {
        "symbolPair": "WETH -> ETH -> 0XC9F9C8 -> WETH",
        "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "quoteInputs": {
            "routePools": [],
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-09-17T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 120202493, "observedAt": "2026-09-17T00:00:00+00:00"},
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "pool_not_initialized"
    assert evidence["executionTrust"] == "pool_not_initialized"
    assert evidence["trustedForExecution"] is False
    assert any("missing effective pool" in blocker.lower() for blocker in evidence["blockers"])


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
            "priceImpactBps": 0,
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


def test_robinhood_testnet_closed_loop_v4_route_probes_size_without_route_metadata(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    captured = {"probe_calls": 0}

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "_probe_robinhood_testnet_route_execution_size",
        lambda *args, **kwargs: {
            "status": "route_execution_size_probe_complete",
            "searchStatus": "bounded",
            "probeAttempts": 3,
            "requestedAmountRaw": "1000000000000000000",
            "maxExecutableAmountRaw": "750000000000000000",
            "firstFailureAmountRaw": "800000000000000000",
            "maxExecutableAmountConfirmed": True,
        },
    )

    def fake_quote_hop_provider(*, w3, quoter, step, pool, amount_in_raw, block_number, token_cache):
        captured["probe_calls"] += 1
        output_map = {
            1: "0x" + "2" * 40,
            2: "0x" + "3" * 40,
            3: "0x" + "0" * 40,
        }
        amount_out_raw = amount_in_raw + 1
        return {
            "status": "live_verified",
            "quoteMethod": "fake_quote",
            "pool": step.get("pool"),
            "tokenIn": step.get("tokenIn"),
            "tokenOut": step.get("tokenOut"),
            "fee": step.get("fee") or 0,
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(amount_out_raw),
            "amountIn": "1",
                "amountOut": "2",
                "inputDecimals": 18,
                "outputDecimals": 18,
                "priceImpactBps": 0,
                "quoterGasEstimate": 12345,
        }

    route = {
        "network": "robinhood-testnet",
        "routeSymbols": ["ETH", "A", "B", "ETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "token0": "0x0000000000000000000000000000000000000000",
                    "token1": "0x" + "2" * 40,
                    "token0Symbol": "ETH",
                    "token1Symbol": "A",
                },
                {
                    "pool": "0x" + "2" * 40,
                    "protocol": "uniswap_v4",
                    "token0": "0x" + "2" * 40,
                    "token1": "0x" + "3" * 40,
                    "token0Symbol": "A",
                    "token1Symbol": "B",
                },
                {
                    "pool": "0x" + "3" * 40,
                    "protocol": "uniswap_v4",
                    "token0": "0x" + "3" * 40,
                    "token1": "0x0000000000000000000000000000000000000000",
                    "token0Symbol": "B",
                    "token1Symbol": "ETH",
                },
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "referenceInputAmount": 1.0,
            "inputSymbol": "ETH",
            "outputSymbol": "ETH",
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "executableCandidate": True,
            "chainSubmissionCandidate": True,
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "A", "protocol": "uniswap_v4", "pool": "0x" + "1" * 40, "tokenIn": "0x0000000000000000000000000000000000000000", "tokenOut": "0x" + "2" * 40, "fee": 100},
                {"hop": 2, "fromSymbol": "A", "toSymbol": "B", "protocol": "uniswap_v4", "pool": "0x" + "2" * 40, "tokenIn": "0x" + "2" * 40, "tokenOut": "0x" + "3" * 40, "fee": 3000},
                {"hop": 3, "fromSymbol": "B", "toSymbol": "ETH", "protocol": "uniswap_v4", "pool": "0x" + "3" * 40, "tokenIn": "0x" + "3" * 40, "tokenOut": "0x0000000000000000000000000000000000000000", "fee": 500},
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=fake_quote_hop_provider,
    )

    assert captured["probe_calls"] == 3
    assert evidence["executionSizeProbe"]["status"] == "route_execution_size_probe_complete"
    assert evidence["executionSizeProbe"]["maxExecutableAmountRaw"] == "750000000000000000"
    assert evidence["requestedBorrowInputAmountRaw"] == "1000000000000000000"
    assert evidence["status"] == "live_verified"


def test_robinhood_testnet_skip_probe_uses_one_basic_unit_without_reference(monkeypatch):
    class FakeEth:
        block_number = 123

        def contract(self, **kwargs):
            return None

        def get_block(self, block_number):
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    captured_amounts = []

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))

    def fake_quote_hop_provider(*, step, amount_in_raw, block_number, **kwargs):
        captured_amounts.append(int(amount_in_raw))
        amount_out_raw = int(amount_in_raw) + 1
        return {
            "status": "live_verified",
            "quoteMethod": "fake_quote",
            "pool": step.get("pool"),
            "tokenIn": step.get("tokenIn"),
            "tokenOut": step.get("tokenOut"),
            "fee": step.get("fee") or 0,
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(amount_out_raw),
            "amountIn": "1",
            "amountOut": "2",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "priceImpactBps": 0,
        }

    route = {
        "network": "robinhood-testnet",
        "routeSymbols": ["ETH", "A", "B", "ETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "token0": "0x0000000000000000000000000000000000000000",
                    "token1": "0x" + "2" * 40,
                    "token0Symbol": "ETH",
                    "token1Symbol": "A",
                    "token0Decimals": 18,
                    "token1Decimals": 18,
                },
                {
                    "pool": "0x" + "2" * 40,
                    "protocol": "uniswap_v4",
                    "token0": "0x" + "2" * 40,
                    "token1": "0x" + "3" * 40,
                    "token0Symbol": "A",
                    "token1Symbol": "B",
                    "token0Decimals": 18,
                    "token1Decimals": 18,
                },
                {
                    "pool": "0x" + "3" * 40,
                    "protocol": "uniswap_v4",
                    "token0": "0x" + "3" * 40,
                    "token1": "0x0000000000000000000000000000000000000000",
                    "token0Symbol": "B",
                    "token1Symbol": "ETH",
                    "token0Decimals": 18,
                    "token1Decimals": 18,
                },
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "inputSymbol": "ETH",
            "outputSymbol": "ETH",
            "complete": True,
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "A", "protocol": "uniswap_v4", "pool": "0x" + "1" * 40, "tokenIn": "0x0000000000000000000000000000000000000000", "tokenOut": "0x" + "2" * 40, "fee": 100},
                {"hop": 2, "fromSymbol": "A", "toSymbol": "B", "protocol": "uniswap_v4", "pool": "0x" + "2" * 40, "tokenIn": "0x" + "2" * 40, "tokenOut": "0x" + "3" * 40, "fee": 3000},
                {"hop": 3, "fromSymbol": "B", "toSymbol": "ETH", "protocol": "uniswap_v4", "pool": "0x" + "3" * 40, "tokenIn": "0x" + "3" * 40, "tokenOut": "0x0000000000000000000000000000000000000000", "fee": 500},
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=fake_quote_hop_provider,
        skip_execution_size_probe=True,
    )

    assert captured_amounts[0] == 1000000000000000000
    assert evidence["selectedBorrowInputAmountRaw"] == "1000000000000000000"
    assert evidence["requestedBorrowInputAmountRaw"] == "1000000000000000000"


def test_robinhood_testnet_closed_loop_v4_route_does_not_use_old_max_executable_as_best_amount(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    captured_amounts = []

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))

    def fake_quote_hop_provider(*, w3, quoter, step, pool, amount_in_raw, block_number, token_cache):
        captured_amounts.append(int(amount_in_raw))
        amount_out_raw = int(amount_in_raw) + int(amount_in_raw // 2) + 1
        return {
            "status": "live_verified",
            "quoteMethod": "fake_quote",
            "pool": step.get("pool"),
            "tokenIn": step.get("tokenIn"),
            "tokenOut": step.get("tokenOut"),
            "fee": step.get("fee") or 0,
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(amount_out_raw),
            "amountIn": "1",
                "amountOut": "2",
                "inputDecimals": 18,
                "outputDecimals": 18,
                "priceImpactBps": 0,
                "quoterGasEstimate": 12345,
        }

    route = {
        "network": "robinhood-testnet",
        "routeSymbols": ["ETH", "A", "B", "ETH"],
        "routeAddresses": [
            "0x0000000000000000000000000000000000000000",
            "0x" + "2" * 40,
            "0x" + "3" * 40,
            "0x0000000000000000000000000000000000000000",
        ],
        "sizeAnalysis": {
            "status": "liquidity_probe_complete",
            "searchStatus": "bounded",
            "probeAttempts": 3,
            "maxExecutableAmountRaw": "750000000000000000",
            "firstFailureAmountRaw": "800000000000000000",
            "maxExecutableAmountConfirmed": True,
            "executionSizeProbe": {
                "status": "liquidity_probe_complete",
                "searchStatus": "bounded",
                "probeAttempts": 3,
                "maxExecutableAmountRaw": "750000000000000000",
                "firstFailureAmountRaw": "800000000000000000",
                "maxExecutableAmountConfirmed": True,
            },
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "referenceInputAmount": 1.0,
            "inputSymbol": "ETH",
            "outputSymbol": "ETH",
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "executableCandidate": True,
            "chainSubmissionCandidate": True,
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "A", "protocol": "uniswap_v4", "pool": "0x" + "1" * 40, "tokenIn": "0x0000000000000000000000000000000000000000", "tokenOut": "0x" + "2" * 40, "fee": 100},
                {"hop": 2, "fromSymbol": "A", "toSymbol": "B", "protocol": "uniswap_v4", "pool": "0x" + "2" * 40, "tokenIn": "0x" + "2" * 40, "tokenOut": "0x" + "3" * 40, "fee": 3000},
                {"hop": 3, "fromSymbol": "B", "toSymbol": "ETH", "protocol": "uniswap_v4", "pool": "0x" + "3" * 40, "tokenIn": "0x" + "3" * 40, "tokenOut": "0x0000000000000000000000000000000000000000", "fee": 500},
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=fake_quote_hop_provider,
    )

    assert evidence["requestedBorrowInputAmountRaw"] == "1000000000000000000"
    assert evidence["selectedBorrowInputAmountRaw"] == "1000000000000000000"
    assert evidence["selectedBorrowInputAmountRaw"] != "750000000000000000"
    assert evidence["hops"][0]["amountInRaw"] == "1000000000000000000"
    assert captured_amounts[0] == 1000000000000000000
    assert evidence["status"] == "live_verified"
    assert evidence["recommendation"]["profitable"] is True
    assert evidence["recommendation"]["readyForTransactionPreflight"] is True
    assert "正收益" in evidence["recommendation"]["conclusion"]


def test_robinhood_testnet_closed_loop_route_slippage_above_limit_blocks_preflight(monkeypatch):
    class FakeEth:
        block_number = 123

        def get_block(self, block_number):
            assert block_number == 123
            return {"timestamp": 1_777_777_777}

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))

    def fake_quote_hop_provider(*, w3, quoter, step, pool, amount_in_raw, block_number, token_cache):
        hop_no = int(step.get("hop") or 1)
        price_impacts = {1: 0, 2: 374, 3: 554}
        amount_out_raw = int(amount_in_raw) + int(amount_in_raw // 2) + 1
        return {
            "status": "live_verified",
            "quoteMethod": "fake_quote",
            "pool": step.get("pool"),
            "tokenIn": step.get("tokenIn"),
            "tokenOut": step.get("tokenOut"),
            "fee": step.get("fee") or 0,
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(amount_out_raw),
            "amountIn": "1",
            "amountOut": "2",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "priceImpactBps": price_impacts.get(hop_no, 0),
            "quoterGasEstimate": 12345,
        }

    route = {
        "network": "robinhood-testnet",
        "routeSymbols": ["ETH", "A", "B", "ETH"],
        "routeAddresses": [
            "0x0000000000000000000000000000000000000000",
            "0x" + "2" * 40,
            "0x" + "3" * 40,
            "0x0000000000000000000000000000000000000000",
        ],
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "referenceInputAmount": 1.0,
            "inputSymbol": "ETH",
            "outputSymbol": "ETH",
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "executableCandidate": True,
            "chainSubmissionCandidate": True,
            "steps": [
                {"hop": 1, "fromSymbol": "ETH", "toSymbol": "A", "protocol": "uniswap_v4", "pool": "0x" + "1" * 40, "tokenIn": "0x0000000000000000000000000000000000000000", "tokenOut": "0x" + "2" * 40, "fee": 100},
                {"hop": 2, "fromSymbol": "A", "toSymbol": "B", "protocol": "uniswap_v4", "pool": "0x" + "2" * 40, "tokenIn": "0x" + "2" * 40, "tokenOut": "0x" + "3" * 40, "fee": 3000},
                {"hop": 3, "fromSymbol": "B", "toSymbol": "ETH", "protocol": "uniswap_v4", "pool": "0x" + "3" * 40, "tokenIn": "0x" + "3" * 40, "tokenOut": "0x0000000000000000000000000000000000000000", "fee": 500},
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-08-24T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-08-24T00:00:00+00:00"},
        quote_hop_provider=fake_quote_hop_provider,
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "slippage_limit_exceeded"
    assert evidence["readyForTransactionPreflight"] is False
    assert evidence["validation"]["allHopsWithinPriceImpactLimit"] is False
    assert evidence["recommendation"]["profitable"] is True
    assert evidence["recommendation"]["readyForTransactionPreflight"] is False
    assert "价格影响超过 10 bps" in evidence["recommendation"]["conclusion"]
    assert any(
        "hop 3 B->ETH price impact" in blocker
        and "554" in blocker
        and "exceeds 10 bps limit" in blocker
        for blocker in evidence["blockers"]
    )


def test_robinhood_testnet_route_execution_probe_ignores_impact_limit_when_profitable(monkeypatch):
    attempted = []

    def fake_build_route_onchain_evidence(*args, **kwargs):
        amount_raw = int(kwargs["borrow_input_amount_override_raw"])
        attempted.append(amount_raw)
        return {
            "status": "live_verified" if amount_raw <= 60 else "slippage_limit_exceeded",
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "tokenContinuity": True,
                "profitableAfterCosts": True,
                "allHopsWithinPriceImpactLimit": amount_raw <= 60,
            },
            "costModel": {"netProfitAmount": "1"},
            "readyForTransactionPreflight": False,
        }

    monkeypatch.setattr(
        dex_onchain_quote_evidence,
        "build_route_onchain_evidence",
        fake_build_route_onchain_evidence,
    )

    probe = dex_onchain_quote_evidence._probe_robinhood_testnet_route_execution_size(
        {},
        payload={},
        chain_id=46630,
        observed_at="2026-09-02T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123},
        quote_hop_provider=None,
        requested_amount_raw=100,
        max_attempts=24,
    )

    assert probe["status"] == "route_execution_size_probe_complete"
    assert probe["maxExecutableAmountRaw"] == "100"
    assert probe["selectedProfitableAmountRaw"] == "100"
    assert probe["bestProfitAmount"] == 1.0
    assert probe["maxExecutableAmountConfirmed"] is True
    assert probe["searchStatus"] == "profitable_amount_found"
    assert attempted[0] == 100
    assert 200 in attempted


def test_route_execution_probe_stops_immediately_on_not_enough_liquidity(monkeypatch):
    attempted = []

    def fake_build_route_onchain_evidence(*args, **kwargs):
        amount_raw = int(kwargs["borrow_input_amount_override_raw"])
        attempted.append(amount_raw)
        if amount_raw > 60:
            return {
                "status": "live_quote_failed",
                "validation": {
                    "allHopsQuoted": False,
                    "routeClosed": True,
                    "tokenContinuity": True,
                    "profitableAfterCosts": False,
                },
                "blockers": ["hop 2 verification failed: NotEnoughLiquidity(bytes32)"],
            }
        return {
            "status": "live_verified",
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "tokenContinuity": True,
                "profitableAfterCosts": True,
            },
            "costModel": {"netProfitAmount": "1"},
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "build_route_onchain_evidence", fake_build_route_onchain_evidence)
    probe = dex_onchain_quote_evidence._probe_robinhood_testnet_route_execution_size(
        {},
        payload={},
        chain_id=46630,
        observed_at="2026-09-02T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123},
        quote_hop_provider=None,
        requested_amount_raw=100,
        max_attempts=24,
    )

    assert probe["status"] == "route_execution_size_probe_unexecutable"
    assert probe["searchStatus"] == "not_enough_liquidity"
    assert probe["maxExecutableAmountRaw"] is None
    assert probe["firstFailureAmountRaw"] == "100"
    assert attempted[0] == 100
    assert attempted == [100]


def test_route_execution_probe_expands_profitable_starting_amount(monkeypatch):
    attempted = []

    def fake_build_route_onchain_evidence(*args, **kwargs):
        amount_raw = int(kwargs["borrow_input_amount_override_raw"])
        attempted.append(amount_raw)
        return {
            "status": "live_verified" if amount_raw <= 60 else "slippage_limit_exceeded",
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "tokenContinuity": True,
                "profitableAfterCosts": True,
                "allHopsWithinPriceImpactLimit": amount_raw <= 60,
            },
            "costModel": {"netProfitAmount": "1"},
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "build_route_onchain_evidence", fake_build_route_onchain_evidence)
    probe = dex_onchain_quote_evidence._probe_robinhood_testnet_route_execution_size(
        {},
        payload={},
        chain_id=46630,
        observed_at="2026-09-03T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123},
        quote_hop_provider=None,
        requested_amount_raw=25,
        max_attempts=10,
    )

    assert probe["searchStatus"] == "profitable_amount_found"
    assert probe["selectedProfitableAmountRaw"] == "25"
    assert probe["firstFailureAmountRaw"] == "26"
    assert probe["expansionStopReason"] == "not_improving_profit"
    assert probe["maxExecutableAmountConfirmed"] is True
    assert attempted[0] == 25
    assert 50 in attempted


def test_route_execution_probe_selects_larger_amount_when_profit_improves(monkeypatch):
    attempted = []

    def fake_build_route_onchain_evidence(*args, **kwargs):
        amount_raw = int(kwargs["borrow_input_amount_override_raw"])
        attempted.append(amount_raw)
        profits = {
            16: 10,
            32: 25,
            64: 20,
            128: 5,
        }
        profit = profits.get(amount_raw, -1)
        return {
            "status": "live_verified" if profit > 0 else "route_unprofitable",
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "tokenContinuity": True,
                "profitableAfterCosts": profit > 0,
                "allHopsWithinPriceImpactLimit": True,
            },
            "costModel": {"netProfitAmount": str(profit)},
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "build_route_onchain_evidence", fake_build_route_onchain_evidence)
    probe = dex_onchain_quote_evidence._probe_robinhood_testnet_route_execution_size(
        {},
        payload={},
        chain_id=46630,
        observed_at="2026-09-04T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123},
        quote_hop_provider=None,
        requested_amount_raw=16,
        max_attempts=8,
    )

    assert probe["status"] == "route_execution_size_probe_complete"
    assert probe["selectedProfitableAmountRaw"] == "32"
    assert probe["maxExecutableAmountRaw"] == "32"
    assert probe["bestProfitAmount"] == 25
    assert probe["searchStatus"] == "profitable_amount_found"
    assert attempted[0] == 16
    assert 32 in attempted
    assert 64 in attempted


def test_route_size_advice_ignores_per_hop_token_units_for_borrow_amount():
    probe, max_executable_raw, first_failure_raw, recommended_raw = dex_onchain_quote_evidence._route_size_advice(
        {
            "sizeAnalysis": {
                "inputAmountUnit": "per_hop",
                "maxExecutableAmountRaw": "123",
                "firstFailureAmountRaw": "124",
                "executionSizeProbe": {"status": "liquidity_probe_complete"},
            }
        }
    )

    assert probe == {"status": "liquidity_probe_complete"}
    assert max_executable_raw is None
    assert first_failure_raw is None
    assert recommended_raw is None


def test_route_size_advice_prefers_offchain_recommended_borrow_amount():
    probe, max_executable_raw, first_failure_raw, recommended_raw = dex_onchain_quote_evidence._route_size_advice(
        {
            "sizeAnalysis": {
                "recommendedBorrowInputAmountRaw": "250",
                "maxExecutableAmountRaw": "500",
                "firstFailureAmountRaw": "501",
                "executionSizeProbe": {"status": "liquidity_probe_complete"},
            }
        }
    )

    assert probe == {"status": "liquidity_probe_complete"}
    assert max_executable_raw == 500
    assert first_failure_raw == 501
    assert recommended_raw == 250


def test_route_execution_probe_halves_unprofitable_starting_amounts_until_exhausted(monkeypatch):
    attempted = []

    def fake_build_route_onchain_evidence(*args, **kwargs):
        amount_raw = int(kwargs["borrow_input_amount_override_raw"])
        attempted.append(amount_raw)
        return {
            "status": "live_verified",
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "tokenContinuity": True,
                "profitableAfterCosts": False,
                "allHopsWithinPriceImpactLimit": True,
            },
            "costModel": {"netProfitAmount": "-1"},
            "blockers": ["链上核算扣除闪电贷费用后仍未转正"],
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "build_route_onchain_evidence", fake_build_route_onchain_evidence)
    probe = dex_onchain_quote_evidence._probe_robinhood_testnet_route_execution_size(
        {},
        payload={},
        chain_id=46630,
        observed_at="2026-09-02T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123},
        quote_hop_provider=None,
        requested_amount_raw=10**12,
        max_attempts=24,
    )

    assert probe["status"] == "route_execution_size_probe_unexecutable"
    assert probe["searchStatus"] == "not_profitable_after_costs"
    assert probe["probeAttempts"] == 24
    assert len(attempted) == 24
    assert attempted[0] == 10**12


def test_route_execution_probe_halves_low_slippage_unprofitable_amount(monkeypatch):
    attempted = []

    def fake_build_route_onchain_evidence(*args, **kwargs):
        amount_raw = int(kwargs["borrow_input_amount_override_raw"])
        attempted.append(amount_raw)
        return {
            "status": "route_unprofitable",
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "tokenContinuity": True,
                "profitableAfterCosts": False,
                "allHopsWithinPriceImpactLimit": True,
            },
            "costModel": {"netProfitAmount": "-1"},
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "build_route_onchain_evidence", fake_build_route_onchain_evidence)
    probe = dex_onchain_quote_evidence._probe_robinhood_testnet_route_execution_size(
        {},
        payload={},
        chain_id=46630,
        observed_at="2026-09-02T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123},
        quote_hop_provider=None,
        requested_amount_raw=100,
    )

    assert probe["searchStatus"] == "not_profitable_after_costs"
    assert probe["probeAttempts"] == 7
    assert attempted == [100, 50, 25, 12, 6, 3, 1]


def test_route_execution_probe_finds_smaller_profitable_amount_after_large_unprofitable_probe(monkeypatch):
    attempted = []

    def fake_build_route_onchain_evidence(*args, **kwargs):
        amount_raw = int(kwargs["borrow_input_amount_override_raw"])
        attempted.append(amount_raw)
        return {
            "status": "live_verified" if amount_raw <= 50 else "route_unprofitable",
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "tokenContinuity": True,
                "profitableAfterCosts": amount_raw <= 50,
                "allHopsWithinPriceImpactLimit": True,
            },
            "costModel": {"netProfitAmount": "1" if amount_raw <= 50 else "-1"},
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "build_route_onchain_evidence", fake_build_route_onchain_evidence)
    probe = dex_onchain_quote_evidence._probe_robinhood_testnet_route_execution_size(
        {},
        payload={},
        chain_id=46630,
        observed_at="2026-09-02T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123},
        quote_hop_provider=None,
        requested_amount_raw=100,
        max_attempts=24,
    )

    assert probe["status"] == "route_execution_size_probe_complete"
    assert probe["searchStatus"] == "profitable_amount_found"
    assert probe["selectedProfitableAmountRaw"] == "50"
    assert probe["maxExecutableAmountRaw"] == "50"
    assert probe["firstFailureAmountRaw"] == "51"
    assert attempted[0] == 100
    assert attempted[1] == 50


def test_hop_price_impact_uses_spot_price_and_token_decimals_are_not_compared_as_amounts():
    hop = {
        "tokenIn": "0x" + "1" * 40,
        "tokenOut": "0x" + "2" * 40,
        "amountInRaw": "1000000",
        "amountOutRaw": "99000000",
        "inputDecimals": 6,
        "outputDecimals": 9,
        "poolState": {
            "token0": "0x" + "1" * 40,
            "token1": "0x" + "2" * 40,
            "sqrtPriceX96": str(10 * 2**96),
        },
    }
    annotated = dex_onchain_quote_evidence._annotate_hop_price_impact(
        hop,
        step={"fee": 0},
        pool={},
    )

    assert annotated["priceImpactBps"] == pytest.approx(70.21063189568706)
    assert annotated["priceImpactWithinLimit"] is False


def test_hop_price_impact_excludes_lp_fee_when_provider_supplies_impact():
    annotated = dex_onchain_quote_evidence._annotate_hop_price_impact(
        {"amountInRaw": "100", "amountOutRaw": "99", "priceImpactBps": 8.5},
        step={"fee": 3000},
        pool={},
    )

    assert annotated["priceImpactBps"] == 8.5
    assert annotated["priceImpactWithinLimit"] is True
    assert annotated["priceImpactFeeExcluded"] is True


def test_robinhood_testnet_chain_default_v4_adapter_is_configured():
    adapter = load_v4_sdk_adapter(network="robinhood-testnet")

    assert callable(adapter)


def test_robinhood_testnet_chain_id_default_v4_adapter_is_configured():
    adapter = load_v4_sdk_adapter(chain_id=46630)

    assert callable(adapter)


def test_robinhood_testnet_rejects_reference_v4_adapter_env(monkeypatch):
    monkeypatch.setenv(
        "ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER",
        "bot.execution.uniswap_v4_sdk_adapter:reference_quote_adapter",
    )
    for name in (
        "UNISWAP_V4_SDK_ADAPTER",
        "DEX_V4_SDK_ADAPTER",
        "ROBINHOOD_TESTNET_UNISWAP_V4_SDK_ADAPTER",
        "ROBINHOOD_V4_QUOTE_ADAPTER",
        "ROBINHOOD_V4_SDK_ADAPTER",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Robinhood Testnet requires a real V4 quote adapter"):
        load_v4_sdk_adapter(network="robinhood-testnet")


def test_robinhood_testnet_v4_live_state_failure_uses_network_label():
    class FakeExtsload:
        def __init__(self, slot):
            self.slot = slot

        def call(self, **kwargs):
            return b"\x00" * 32

    class FakeFunctions:
        extsload = FakeExtsload

    class FakeEth:
        def contract(self, **kwargs):
            return type("FakeContract", (), {"functions": FakeFunctions()})()

    class FakeW3:
        eth = FakeEth()

    with pytest.raises(ValueError, match="pool manager live slot0 missing sqrtPriceX96"):
        v4_quote_adapter._read_live_pool_state_with_w3(
            w3=FakeW3(),
            rpc_url="rpc",
            pool_manager="0x" + "4" * 40,
            pool_id="0x" + "1" * 64,
            network="robinhood-testnet",
            chain_id=46630,
        )


def test_robinhood_testnet_v4_pool_index_adapter_uses_live_pool_state(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        v4_quote_adapter,
        "_pool_rows",
        lambda *args, **kwargs: [
            {
                "poolId": "0x" + "1" * 64,
                "token0": "0x" + "2" * 40,
                "token1": "0x" + "3" * 40,
                "fee": 100,
                "tickSpacing": 60,
                "sqrtPriceX96": "1",
            }
        ],
    )
    monkeypatch.setattr(
        v4_quote_adapter,
        "_pool_manager_address",
        lambda *args, **kwargs: "0x" + "4" * 40,
    )
    def fake_read_live_pool_state(**kwargs):
        captured["stateBlockNumber"] = kwargs.get("block_number")
        return {
            "source": "pool_manager_extsload_live_state",
            "poolManager": "0x" + "4" * 40,
            "poolId": "0x" + "1" * 64,
            "stateSlot": "0x" + "5" * 64,
            "sqrtPriceX96": str(2**96),
            "tick": 0,
            "protocolFee": 0,
            "lpFee": 100,
            "liquidity": str(10**18),
            "rpcUrl": "https://rpc.example",
        }

    def fake_quote_v4_exact_input_single(**kwargs):
        captured["quoteBlockNumber"] = kwargs.get("block_number")
        return 99 * 10**16, 123456

    monkeypatch.setattr(v4_quote_adapter, "_read_live_pool_state", fake_read_live_pool_state)
    monkeypatch.setattr(v4_quote_adapter, "_quote_v4_exact_input_single", fake_quote_v4_exact_input_single)

    quote = v4_quote_adapter.quote_v4_exact_input_single_from_pool_index(
        {
            "pool": "0x" + "1" * 64,
            "tokenIn": "0x" + "2" * 40,
            "tokenOut": "0x" + "3" * 40,
            "amountInRaw": str(10**18),
            "fee": 100,
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
            "blockNumber": 456,
        }
    )

    assert quote["quoteMethod"] == "robinhood_testnet_v4_quoter_quoteExactInputSingle_eth_call"
    assert quote["amountOutRaw"] == str(99 * 10**16)
    assert quote["sdkAdapter"].endswith("quote_v4_exact_input_single_from_pool_index")
    assert quote["quoterGasEstimate"] == 123456
    assert quote["poolState"]["source"] == "pool_manager_extsload_live_state"
    assert quote["poolState"]["lpFee"] == 100
    assert quote["poolState"]["blockNumber"] == 456
    assert captured == {"stateBlockNumber": 456, "quoteBlockNumber": 456}


def test_robinhood_testnet_v4_pool_index_adapter_normalizes_raw_v4_fee_units(monkeypatch):
    monkeypatch.setattr(
        v4_quote_adapter,
        "_pool_rows",
        lambda *args, **kwargs: [
            {
                "poolId": "0x" + "1" * 64,
                "token0": "0x" + "2" * 40,
                "token1": "0x" + "3" * 40,
                "fee": 10000,
                "tickSpacing": 60,
                "sqrtPriceX96": str(2**96),
            }
        ],
    )
    monkeypatch.setattr(
        v4_quote_adapter,
        "_pool_manager_address",
        lambda *args, **kwargs: "0x" + "4" * 40,
    )
    monkeypatch.setattr(
        v4_quote_adapter,
        "_read_live_pool_state",
        lambda **kwargs: {
            "source": "pool_manager_extsload_live_state",
            "poolManager": "0x" + "4" * 40,
            "poolId": "0x" + "1" * 64,
            "stateSlot": "0x" + "5" * 64,
            "sqrtPriceX96": str(2**96),
            "tick": 0,
            "protocolFee": 0,
            "lpFee": 10000,
            "liquidity": str(10**18),
            "rpcUrl": "https://rpc.example",
        },
    )
    monkeypatch.setattr(
        v4_quote_adapter,
        "_quote_v4_exact_input_single",
        lambda **kwargs: (99 * 10**16, 123456),
    )

    quote = v4_quote_adapter.quote_v4_exact_input_single_from_pool_index(
        {
            "pool": "0x" + "1" * 64,
            "tokenIn": "0x" + "2" * 40,
            "tokenOut": "0x" + "3" * 40,
            "amountInRaw": str(10**18),
            "fee": 10000,
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
        }
    )

    assert quote["quoteMethod"] == "robinhood_testnet_v4_quoter_quoteExactInputSingle_eth_call"
    assert quote["amountOutRaw"] == str(99 * 10**16)
    assert quote["poolState"]["lpFee"] == 10000


def test_robinhood_testnet_v4_liquidity_probe_finds_max_executable_amount(monkeypatch):
    monkeypatch.setattr(
        v4_quote_adapter,
        "_resolve_v4_quote_context",
        lambda request: {
            "pool": {
                "pool": "0x" + "1" * 64,
                "poolId": "0x" + "1" * 64,
                "token0": "0x" + "2" * 40,
                "token1": "0x" + "3" * 40,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x" + "0" * 40,
            },
            "network": "robinhood-testnet",
            "chainId": 46630,
            "poolId": "0x" + "1" * 64,
            "poolManager": "0x" + "4" * 40,
            "quoterAddress": "0x" + "5" * 40,
            "w3": None,
            "rpcUrl": "https://rpc.example",
            "liveState": {
                "source": "pool_manager_extsload_live_state",
                "poolManager": "0x" + "4" * 40,
                "poolId": "0x" + "1" * 64,
                "stateSlot": "0x" + "6" * 64,
                "sqrtPriceX96": str(2**96),
                "tick": 0,
                "protocolFee": 0,
                "lpFee": 3000,
                "liquidity": str(10**18),
                "rpcUrl": "https://rpc.example",
            },
            "amountInRaw": int(request["amountInRaw"]),
            "feeBps": 30,
            "hookData": b"",
            "zeroForOne": True,
            "poolKey": (
                "0x" + "2" * 40,
                "0x" + "3" * 40,
                3000,
                60,
                "0x" + "0" * 40,
            ),
            "callFrom": None,
        },
    )

    def fake_quote(**kwargs):
        amount = int(kwargs["amount_in_raw"])
        if amount > 60:
            raise RuntimeError("robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)")
        return amount * 2, 123456

    monkeypatch.setattr(v4_quote_adapter, "_quote_v4_exact_input_single", fake_quote)

    result = v4_quote_adapter.probe_v4_executable_amount_from_pool_index(
        {
            "pool": "0x" + "1" * 64,
            "tokenIn": "0x" + "2" * 40,
            "tokenOut": "0x" + "3" * 40,
            "amountInRaw": "100",
            "fee": 3000,
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
            "maxProbeAttempts": 24,
        }
    )

    assert result["status"] == "liquidity_probe_complete"
    assert result["maxExecutableAmountRaw"] == "60"
    assert result["firstFailureAmountRaw"] == "61"
    assert int(result["probeAttempts"]) > 1


def test_robinhood_testnet_v4_liquidity_probe_reports_unexecutable_amount(monkeypatch):
    monkeypatch.setattr(
        v4_quote_adapter,
        "_resolve_v4_quote_context",
        lambda request: {
            "pool": {
                "pool": "0x" + "1" * 64,
                "poolId": "0x" + "1" * 64,
                "token0": "0x" + "2" * 40,
                "token1": "0x" + "3" * 40,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x" + "0" * 40,
            },
            "network": "robinhood-testnet",
            "chainId": 46630,
            "poolId": "0x" + "1" * 64,
            "poolManager": "0x" + "4" * 40,
            "quoterAddress": "0x" + "5" * 40,
            "w3": None,
            "rpcUrl": "https://rpc.example",
            "liveState": {
                "source": "pool_manager_extsload_live_state",
                "poolManager": "0x" + "4" * 40,
                "poolId": "0x" + "1" * 64,
                "stateSlot": "0x" + "6" * 64,
                "sqrtPriceX96": str(2**96),
                "tick": 0,
                "protocolFee": 0,
                "lpFee": 3000,
                "liquidity": str(10**18),
                "rpcUrl": "https://rpc.example",
            },
            "amountInRaw": int(request["amountInRaw"]),
            "feeBps": 30,
            "hookData": b"",
            "zeroForOne": True,
            "poolKey": (
                "0x" + "2" * 40,
                "0x" + "3" * 40,
                3000,
                60,
                "0x" + "0" * 40,
            ),
            "callFrom": None,
        },
    )

    def fake_quote(**kwargs):
        raise RuntimeError("robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)")

    monkeypatch.setattr(v4_quote_adapter, "_quote_v4_exact_input_single", fake_quote)

    result = v4_quote_adapter.probe_v4_executable_amount_from_pool_index(
        {
            "pool": "0x" + "1" * 64,
            "tokenIn": "0x" + "2" * 40,
            "tokenOut": "0x" + "3" * 40,
            "amountInRaw": "1",
            "fee": 3000,
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
            "maxProbeAttempts": 4,
        }
    )

    assert result["status"] == "liquidity_probe_unexecutable"
    assert result["searchStatus"] == "no_successful_amount_found"
    assert result["minExecutableAmountRaw"] is None
    assert result["maxExecutableAmountRaw"] is None


def test_robinhood_testnet_v4_summarize_pool_not_initialized():
    assert v4_quote_adapter._summarize_v4_revert_data("0x486aa307") == "PoolNotInitialized()"

    nested = abi_encode(["bytes"], [bytes.fromhex("486aa307")]).hex()
    data = "0x6190b2b0" + nested
    assert (
        v4_quote_adapter._summarize_v4_revert_data(data)
        == "UnexpectedRevertBytes(bytes) -> PoolNotInitialized()"
    )


def test_robinhood_testnet_live_pool_precheck_reports_not_enough_liquidity(monkeypatch):
    class FakeEth:
        block_number = 123

        def contract(self, **kwargs):
            return None

    class FakeW3:
        eth = FakeEth()

    def fake_batch_state(**kwargs):
        return {
            _POOL_ID.lower(): {
                "error": "robinhood testnet v4 pool manager live liquidity is zero",
                "poolId": _POOL_ID,
            }
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(v4_quote_adapter, "_batch_read_live_pool_states", fake_batch_state)

    route = {
        "routeSymbols": ["WETH", "ETH", "WETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": _POOL_ID,
                    "protocol": "uniswap_v4",
                    "token0Symbol": "WETH",
                    "token0": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x0000000000000000000000000000000000000000",
                    "token1Decimals": 18,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "pool": _POOL_ID,
                    "tokenIn": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "tokenOut": "0x0000000000000000000000000000000000000000",
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-09-18T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-09-18T00:00:00+00:00"},
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "not_enough_liquidity"
    assert evidence["executionTrust"] == "not_enough_liquidity"
    assert any("has no LP liquidity on chain" in blocker for blocker in evidence["blockers"])


def test_robinhood_testnet_live_pool_precheck_reports_pool_not_initialized(monkeypatch):
    class FakeEth:
        block_number = 123

        def contract(self, **kwargs):
            return None

    class FakeW3:
        eth = FakeEth()

    def fake_batch_state(**kwargs):
        return {
            _POOL_ID.lower(): {
                "error": "robinhood testnet v4 pool manager live slot0 missing sqrtPriceX96",
                "poolId": _POOL_ID,
            }
        }

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(v4_quote_adapter, "_batch_read_live_pool_states", fake_batch_state)

    route = {
        "routeSymbols": ["WETH", "ETH", "WETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": _POOL_ID,
                    "protocol": "uniswap_v4",
                    "token0Symbol": "WETH",
                    "token0": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x0000000000000000000000000000000000000000",
                    "token1Decimals": 18,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "pool": _POOL_ID,
                    "tokenIn": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "tokenOut": "0x0000000000000000000000000000000000000000",
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-09-18T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-09-18T00:00:00+00:00"},
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "pool_not_initialized"
    assert evidence["executionTrust"] == "pool_not_initialized"
    assert any("pool is not initialized" in blocker for blocker in evidence["blockers"])


def test_robinhood_testnet_live_pool_precheck_uses_latest_block(monkeypatch):
    class FakeEth:
        block_number = 999

        def contract(self, **kwargs):
            return None

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(dex_onchain_quote_evidence, "_live_pool_state_precheck_failures", lambda **kwargs: [])

    def fake_quote_hop_provider(*, step, amount_in_raw, block_number, **kwargs):
        assert block_number == 999
        amount_out_raw = int(amount_in_raw) + 1
        return {
            "status": "live_verified",
            "quoteMethod": "test_latest_precheck_fallback",
            "pool": step.get("pool"),
            "tokenIn": step.get("tokenIn"),
            "tokenOut": step.get("tokenOut"),
            "fee": step.get("fee") or 0,
            "amountInRaw": str(amount_in_raw),
            "amountOutRaw": str(amount_out_raw),
            "amountIn": "1",
            "amountOut": "2",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "priceImpactBps": 0,
        }

    route = {
        "routeSymbols": ["WETH", "ETH", "WETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": _POOL_ID,
                    "protocol": "uniswap_v4",
                    "token0Symbol": "WETH",
                    "token0": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x0000000000000000000000000000000000000000",
                    "token1Decimals": 18,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "pool": _POOL_ID,
                    "tokenIn": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "tokenOut": "0x0000000000000000000000000000000000000000",
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-09-18T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-09-18T00:00:00+00:00"},
        quote_hop_provider=fake_quote_hop_provider,
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "live_verified"
    assert evidence["executionTrust"] == "live_quoter_verified_readonly"
    assert evidence["hops"][0]["quoteMethod"] == "test_latest_precheck_fallback"
    assert evidence["quoteBlockSource"] == "latest_onchain_review"
    assert evidence["triggerBlockNumber"] == 123


def test_robinhood_testnet_live_pool_precheck_reports_rpc_failure(monkeypatch):
    class FakeEth:
        block_number = 123

        def contract(self, **kwargs):
            return None

    class FakeW3:
        eth = FakeEth()

    def fake_batch_state(**kwargs):
        raise RuntimeError("robinhood testnet v4 pool manager extsload RPC connection timed out")

    monkeypatch.setattr(dex_onchain_quote_evidence, "_connect_robinhood_family", lambda *args, **kwargs: (FakeW3(), "https://rpc.example"))
    monkeypatch.setattr(v4_quote_adapter, "_batch_read_live_pool_states", fake_batch_state)

    route = {
        "routeSymbols": ["WETH", "ETH", "WETH"],
        "quoteInputs": {
            "routePools": [
                {
                    "pool": _POOL_ID,
                    "protocol": "uniswap_v4",
                    "token0Symbol": "WETH",
                    "token0": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "token0Decimals": 18,
                    "token1Symbol": "ETH",
                    "token1": "0x0000000000000000000000000000000000000000",
                    "token1Decimals": 18,
                }
            ]
        },
        "quoteAnalysis": {
            "notionalUsd": 1000.0,
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "WETH",
                    "toSymbol": "ETH",
                    "protocol": "uniswap_v4",
                    "pool": _POOL_ID,
                    "tokenIn": "0x7943e237c7F95DA44E0301572D358911207852Fa",
                    "tokenOut": "0x0000000000000000000000000000000000000000",
                }
            ],
        },
    }

    evidence = build_route_onchain_evidence(
        route,
        payload={"poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630}},
        chain_id=46630,
        observed_at="2026-09-18T00:00:00+00:00",
        trigger_context={"triggerBlockNumber": 123, "observedAt": "2026-09-18T00:00:00+00:00"},
        skip_execution_size_probe=True,
    )

    assert evidence["status"] == "rpc_verification_timeout"
    assert evidence["executionTrust"] == "rpc_verification_timeout"
    assert any("on-chain live pool state verification failed" in blocker for blocker in evidence["blockers"])


def test_robinhood_testnet_v4_liquidity_probe_reports_pool_not_initialized(monkeypatch):
    monkeypatch.setattr(
        v4_quote_adapter,
        "_resolve_v4_quote_context",
        lambda request: {
            "pool": {
                "pool": "0x" + "1" * 64,
                "poolId": "0x" + "1" * 64,
                "token0": "0x" + "2" * 40,
                "token1": "0x" + "3" * 40,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x" + "0" * 40,
            },
            "network": "robinhood-testnet",
            "chainId": 46630,
            "poolId": "0x" + "1" * 64,
            "poolManager": "0x" + "4" * 40,
            "quoterAddress": "0x" + "5" * 40,
            "w3": None,
            "rpcUrl": "https://rpc.example",
            "liveState": {
                "source": "pool_manager_extsload_live_state",
                "poolManager": "0x" + "4" * 40,
                "poolId": "0x" + "1" * 64,
                "stateSlot": "0x" + "6" * 64,
                "sqrtPriceX96": str(2**96),
                "tick": 0,
                "protocolFee": 0,
                "lpFee": 3000,
                "liquidity": str(10**18),
                "rpcUrl": "https://rpc.example",
            },
            "amountInRaw": int(request["amountInRaw"]),
            "feeBps": 30,
            "hookData": b"",
            "zeroForOne": True,
            "poolKey": (
                "0x" + "2" * 40,
                "0x" + "3" * 40,
                3000,
                60,
                "0x" + "0" * 40,
            ),
            "callFrom": None,
        },
    )

    def fake_quote(**kwargs):
        raise RuntimeError("robinhood testnet v4 quoter reverted: PoolNotInitialized()")

    monkeypatch.setattr(v4_quote_adapter, "_quote_v4_exact_input_single", fake_quote)

    result = v4_quote_adapter.probe_v4_executable_amount_from_pool_index(
        {
            "pool": "0x" + "1" * 64,
            "tokenIn": "0x" + "2" * 40,
            "tokenOut": "0x" + "3" * 40,
            "amountInRaw": "100",
            "fee": 3000,
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
            "maxProbeAttempts": 24,
        }
    )

    assert result["status"] == "liquidity_probe_unexecutable"
    assert result["searchStatus"] == "pool_not_initialized"
    assert "PoolNotInitialized()" in result["failureReason"]
    assert result["maxExecutableAmountRaw"] is None


