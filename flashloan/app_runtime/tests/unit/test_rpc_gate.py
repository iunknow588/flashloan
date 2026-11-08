from __future__ import annotations

import pytest

from dex.intent_trade.chains.robinhood_common import rpc_gate


def test_v4_live_quote_gate_defaults_for_robinhood_chains(monkeypatch):
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {})

    assert rpc_gate.v4_live_quote_enabled(chain_id=4663) is True
    assert rpc_gate.v4_live_quote_enabled(chain_id=46630) is True
    assert rpc_gate.v4_live_quote_enabled(chain_id=1) is False
    assert rpc_gate.v4_live_quote_enabled(chain_id=None) is False


def test_v4_live_quote_gate_ignores_obsolete_live_quote_switches(monkeypatch):
    # The quote/routing page always requires live quoting.  Obsolete global and
    # chain-scoped switches are deliberately not read and must not disable it.
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {})
    monkeypatch.setenv("DEX_V4_LIVE_QUOTE_ENABLED", "0")
    monkeypatch.setenv("DEX_V4_ROBINHOOD_MAINNET_LIVE_QUOTE_ENABLED", "0")
    monkeypatch.setenv("DEX_V4_ROBINHOOD_TESTNET_LIVE_QUOTE_ENABLED", "0")
    assert rpc_gate.v4_live_quote_enabled(chain_id=4663) is True
    assert rpc_gate.v4_live_quote_enabled(chain_id=46630) is True
    rpc_gate.require_v4_live_quote(chain_id=4663)

    with pytest.raises(RuntimeError, match="disabled"):
        rpc_gate.require_v4_live_quote(chain_id=1)


def test_v4_live_quote_gate_rpc_offline_is_dynamic_and_fail_closed(monkeypatch):
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {})
    rpc_gate.mark_v4_rpc_offline(4663, reason="network_unreachable")

    assert rpc_gate.v4_live_quote_enabled(chain_id=4663) is False
    assert rpc_gate.v4_live_quote_enabled(chain_id=46630) is True
    with pytest.raises(RuntimeError, match="network_unreachable"):
        rpc_gate.require_v4_live_quote(chain_id=4663)

    rpc_gate.clear_v4_rpc_offline(4663)
    assert rpc_gate.v4_live_quote_enabled(chain_id=4663) is True


def test_v4_edge_quote_disabled_does_not_connect_live_adapter(monkeypatch):
    import market.binance_market.dex_surface_route_quotes as route_quotes
    from market.binance_market.dex_surface_v4_edge_quote import _v4_edge_quote

    calls: list[str] = []

    def _fail(*args, **kwargs):
        calls.append("rpc")
        raise AssertionError("live V4 RPC must not be used when gate is disabled")

    monkeypatch.setattr(route_quotes, "load_v4_sdk_adapter", _fail)
    monkeypatch.setattr(route_quotes, "quote_v4_exact_input_single", _fail)
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {46630: "test_offline"})

    token0 = "0x" + "a" * 40
    token1 = "0x" + "b" * 40
    pool = {
        "pool": "0x" + "1" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token0,
        "token1": token1,
        "token0Symbol": "TZ",
        "token1Symbol": "USDE",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "balances": {"token0": 1000.0, "token1": 1000.0, "tvlUsd": 2000.0},
    }
    result = _v4_edge_quote(
        pool=pool,
        from_symbol="TZ",
        to_symbol="USDE",
        from_address=token0,
        to_address=token1,
        input_amount=1.0,
        input_usd=1.0,
        price_map={"TZ": 1.0, "USDE": 1.0},
        chain_id=46630,
    )

    assert calls == []
    assert result is not None
    assert result["quoteSource"] == "uniswap_v4_sdk_adapter_required"


def test_static_closed_loop_route_quote_does_not_connect_live_adapter(monkeypatch):
    import market.binance_market.dex_surface_route_quotes as route_quotes

    calls: list[str] = []

    def _fail(*args, **kwargs):
        calls.append("rpc")
        raise AssertionError("static observation must not call the live V4 adapter")

    monkeypatch.setattr(route_quotes, "load_v4_sdk_adapter", _fail)
    monkeypatch.setattr(route_quotes, "quote_v4_exact_input_single", _fail)

    token0 = "0x" + "a" * 40
    token1 = "0x" + "b" * 40
    pool = {
        "pool": "0x" + "1" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token0,
        "token1": token1,
        "token0Symbol": "TZ",
        "token1Symbol": "USDE",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "balances": {"token0": 1000.0, "token1": 1000.0, "tvlUsd": 2000.0},
    }
    route = {
        "routeSymbols": ["TZ", "USDE", "TZ"],
        "routeAddresses": [token0, token1, token0],
        "routeTokenPath": [],
        "routePools": [pool],
    }
    analysis = route_quotes._route_quote_analysis(
        route,
        price_map={"TZ": 1.0, "USDE": 1.0},
        chain_id=46630,
        use_static_observation=True,
    )

    assert calls == []
    assert analysis["complete"] is True
    assert analysis["livePriceVerified"] is False
    assert analysis["priceEvidenceMode"] == "reference_only"
    assert analysis["steps"][0]["executionTrust"] == "sdk_adapter_required"


def test_v4_token_metadata_disabled_skips_live_erc20_read(monkeypatch):
    import market.binance_market.dex_surface_v4 as dex_surface_v4

    calls: list[str] = []

    def _fail(*args, **kwargs):
        calls.append("erc20_decimals")
        raise AssertionError("live ERC20 decimals must not be read when gate is disabled")

    monkeypatch.setattr(dex_surface_v4, "_live_v4_token_decimals", _fail)
    monkeypatch.setattr(dex_surface_v4, "_v4_token_metadata_cache", lambda *args, **kwargs: {})
    monkeypatch.setenv("DEX_V4_LIVE_TOKEN_METADATA_ENABLED", "1")
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {4663: "test_offline"})

    pool = {"token0": "0x" + "c" * 40}
    result = dex_surface_v4._v4_pool_token_metadata(pool, 0, chain_id=4663)

    assert calls == []
    assert result["source"] == "pool_index"


def test_capture_onchain_trigger_context_offline_is_rpc_free(monkeypatch):
    import dex.runtime.dex_onchain_quote_evidence as evidence

    calls: list[str] = []

    def _fail(*args, **kwargs):
        calls.append("rpc")
        raise AssertionError("trigger-context RPC must not run when gate is disabled")

    monkeypatch.setattr(evidence, "_connect_robinhood_family", _fail)
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {4663: "test_offline"})

    context = evidence.capture_onchain_trigger_context(
        chain_id=4663,
        network="robinhood",
        observed_at="2026-10-17T00:00:00Z",
    )

    assert calls == []
    assert context["error"] == "rpc_unavailable"


def test_build_route_onchain_evidence_offline_marks_rpc_unavailable(monkeypatch):
    import dex.runtime.dex_onchain_quote_evidence as evidence

    calls: list[str] = []

    def _fail(*args, **kwargs):
        calls.append("rpc")
        raise AssertionError("on-chain evidence RPC must not run when gate is disabled")

    monkeypatch.setattr(evidence, "_connect_robinhood_family", _fail)
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {4663: "test_offline"})
    route = {
        "routeSymbols": ["TZ", "USDE", "TZ"],
        "routeAddresses": ["0x1", "0x2", "0x1"],
    }

    result = evidence.build_route_onchain_evidence(
        route,
        payload={},
        chain_id=4663,
        observed_at="2026-10-17T00:00:00Z",
        trigger_context={},
    )

    assert calls == []
    assert result["status"] == "rpc_unavailable"


def test_official_v4_preview_disabled_skips_rpc_probe(monkeypatch):
    import market.binance_market.dex_route_test as route_test

    calls: list[str] = []

    def _fail_probe(*args, **kwargs):
        calls.append("probe")
        raise AssertionError("official V4 preview RPC probe must not run when gate is disabled")

    monkeypatch.setattr(route_test, "_probe_robinhood_family_rpc", _fail_probe)
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {4663: "test_offline"})

    preview = route_test._official_v4_preview_python(
        route_symbols=["TZ", "USDE", "TZ"],
        route_addresses=["0x1", "0x2", "0x1"],
        amount=1.0,
        route_pool_keys=[
            {"currency0": "0x1", "currency1": "0x2", "fee": 3000, "tickSpacing": 60, "hooks": "0x" + "0" * 40},
            {"currency0": "0x2", "currency1": "0x3", "fee": 3000, "tickSpacing": 60, "hooks": "0x" + "0" * 40},
            {"currency0": "0x3", "currency1": "0x1", "fee": 3000, "tickSpacing": 60, "hooks": "0x" + "0" * 40},
        ],
        chain_id=4663,
    )

    assert preview is None
    assert calls == []


def test_run_official_v4_arb_route_test_offline_returns_preview_rpc_error(monkeypatch):
    import market.binance_market.dex_route_test as route_test

    calls: list[str] = []
    addresses = [
        "0x0000000000000000000000000000000000000201",
        "0x0000000000000000000000000000000000000202",
        "0x0000000000000000000000000000000000000203",
        "0x0000000000000000000000000000000000000201",
    ]

    def _fail_probe(*args, **kwargs):
        calls.append("probe")
        raise AssertionError("read-only route review RPC must not run when gate is disabled")

    monkeypatch.setattr(route_test, "_probe_robinhood_family_rpc", _fail_probe)
    monkeypatch.setattr(
        route_test,
        "_official_v4_route_pool_keys",
        lambda **kwargs: [
            {
                "currency0": addresses[1],
                "currency1": addresses[0],
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "currency0": addresses[1],
                "currency1": addresses[2],
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "currency0": addresses[2],
                "currency1": addresses[0],
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
        ],
    )
    # The live-quote gate is shared module state and a background worker may
    # clear the offline marker mid-test. Pin the reference held by route_test
    # directly so this test deterministically stays in the offline path.
    monkeypatch.setattr(route_test, "v4_live_quote_enabled", lambda chain_id=None: False)
    monkeypatch.setattr(rpc_gate, "_OFFLINE_REASONS", {4663: "test_offline"})

    result = route_test.run_official_v4_arb_route_test(
        route_symbols=["TZ", "TB", "TA", "TZ"],
        amount=1.0,
        broadcast=False,
        chain_id=4663,
        route_addresses=addresses,
    )

    assert result["error"] == "official_v4_readonly_preview_rpc_unavailable"
    assert result["quoteStatus"] == "rpc_unavailable"
    assert calls == []
