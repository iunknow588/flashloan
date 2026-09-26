from __future__ import annotations

import pytest

from market.binance_market.dex_surface_analysis import build_route_size_analysis
from market.binance_market.dex_route_test import (
    _official_route_addresses,
    _quote_failure_status,
    _raw_amount_to_human,
    _step_failure_status,
    estimate_route_live_quote,
    is_official_triangle_route,
    normalize_route_symbols,
    recommended_route_test_amount,
    run_official_v4_arb_route_test,
)


def test_estimate_route_live_quote_marks_unprofitable_even_with_live_adapter(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module
    import market.binance_market.dex_surface_graph as dex_surface_graph
    import market.binance_market.dex_surface_quotes as dex_surface_quotes
    import market.binance_market.pool_candidates as pool_candidates

    monkeypatch.setattr(
        route_test_module,
        "_routes_payload",
        lambda **kwargs: {"routes": []},
    )
    monkeypatch.setattr(
        pool_candidates,
        "dex_pool_candidates_payload",
        lambda **kwargs: {"pairs": []},
    )
    monkeypatch.setattr(
        dex_surface_graph,
        "_price_map_from_pairs",
        lambda pairs: {},
    )

    def fake_route_quote_analysis(route, *, price_map, chain_id, token_basis_input_amount):
        return {
            "routeAddresses": [
                "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e",
                "0x28cc9a0b94ad34a3a0cb2734c8d58ad95862c6c4",
                "0xb169fe96425783d7ba7a9f905fe8de5f80e3fdc8",
                "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e",
            ],
            "referenceInputSymbol": "TZ",
            "referenceInputAmount": 1000.0,
            "referenceFinalOutputAmount": 10.348436749171217,
            "referenceFinalOutputSymbol": "TZ",
            "referenceNetProfitAmount": -990.1515632508288,
            "referenceNetReturnBps": -9901.515632508288,
            "maxStaticDepthImpactBps": None,
            "warnings": ["reference_usd_price_missing_or_untrusted"],
            "steps": [
                {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "executionTrust": "live_v4_adapter", "outputAmount": 52.369752540558274},
                {"hop": 2, "fromSymbol": "TB", "toSymbol": "TA", "executionTrust": "live_v4_adapter", "outputAmount": 1.9862697254812829},
                {"hop": 3, "fromSymbol": "TA", "toSymbol": "TZ", "executionTrust": "live_v4_adapter", "outputAmount": 10.348436749171217},
            ],
        }

    monkeypatch.setattr(dex_surface_quotes, "_route_quote_analysis", fake_route_quote_analysis)

    result = estimate_route_live_quote(
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_symbols=["TZ", "TB", "TA", "TZ"],
        amount=1000.0,
        route={
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "routeAddresses": [],
        },
    )

    assert result["quoteStatus"] == "route_unprofitable"
    assert result["liveVerified"] is True
    assert result["profitable"] is False
    assert result["readyForTransactionPreflight"] is False
    assert result["analysis"]["recommendation"]["action"] == "on-chain verified but net profit is not positive after flash loan fees; do not execute"


def test_estimate_route_live_quote_builds_best_amount_ladder(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module
    import market.binance_market.dex_surface_graph as dex_surface_graph
    import market.binance_market.dex_surface_quotes as dex_surface_quotes
    import market.binance_market.pool_candidates as pool_candidates

    monkeypatch.setattr(
        route_test_module,
        "_routes_payload",
        lambda **kwargs: {"routes": []},
    )
    monkeypatch.setattr(
        pool_candidates,
        "dex_pool_candidates_payload",
        lambda **kwargs: {"pairs": []},
    )
    monkeypatch.setattr(
        dex_surface_graph,
        "_price_map_from_pairs",
        lambda pairs: {},
    )

    def fake_route_quote_analysis(route, *, price_map, chain_id, token_basis_input_amount):
        amount = float(token_basis_input_amount or 0)
        return {
            "routeAddresses": [
                "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e",
                "0xb169fe96425783d7ba7a9f905fe8de5f80e3fdc8",
                "0x28cc9a0b94ad34a3a0cb2734c8d58ad95862c6c4",
                "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e",
            ],
            "referenceInputSymbol": "TZ",
            "referenceInputAmount": amount,
            "referenceFinalOutputAmount": amount * 1.1,
            "referenceFinalOutputSymbol": "TZ",
            "referenceNetProfitAmount": amount * 0.1,
            "referenceNetReturnBps": 1000.0,
            "maxStaticDepthImpactBps": None,
            "steps": [
                {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TA", "executionTrust": "live_v4_adapter", "outputAmount": amount * 0.188},
                {"hop": 2, "fromSymbol": "TA", "toSymbol": "TB", "executionTrust": "live_v4_adapter", "outputAmount": amount * 1.875},
                {"hop": 3, "fromSymbol": "TB", "toSymbol": "TZ", "executionTrust": "live_v4_adapter", "outputAmount": amount * 1.1},
            ],
        }

    monkeypatch.setattr(dex_surface_quotes, "_route_quote_analysis", fake_route_quote_analysis)

    result = estimate_route_live_quote(
        chain_id=46630,
        protocols=["uniswap_v4"],
        route_symbols=["TZ", "TA", "TB", "TZ"],
        amount=1.0,
        route={
            "routeSymbols": ["TZ", "TA", "TB", "TZ"],
            "routeAddresses": [],
        },
    )

    assert result["quoteStatus"] == "live_verified"
    assert result["requestedAmount"] == 1.0
    assert result["bestAmount"] == 131072.0
    assert len(result["probeRecords"]) >= 7
    assert result["probeRecords"][-1]["isBest"] is True
    assert result["probeRecords"][-1]["amount"] == 131072.0
    assert result["analysis"]["probeRecords"][-1]["borrowSymbol"] == "TZ"
    assert result["bestAmountCalculation"]["method"] == "live_route_amount_ladder"
    assert result["bestAmountCalculation"]["requestedAmount"] == 1.0
    assert result["analysis"]["bestAmountCalculation"]["probeRecords"][-1]["isBest"] is True


def test_live_quote_applies_initial_amount_as_base_multiplier():
    from market.binance_market.dex_route_helpers import (
        _optimize_amount_ladder,
        _probe_record_item,
    )

    def fake_quote_analysis(route, *, price_map, chain_id, token_basis_input_amount):
        amount = float(token_basis_input_amount or 0)
        return {
            "referenceInputAmount": amount,
            "referenceInputSymbol": "TZ",
            "referenceFinalOutputAmount": amount * 1.1,
            "referenceFinalOutputSymbol": "TZ",
            "referenceNetProfitAmount": amount * 0.1,
            "referenceNetReturnBps": 1000.0,
            "maxStaticDepthImpactBps": None,
            "warnings": [],
            "steps": [],
        }

    results = []
    for requested_amount in (0.1, 10.0, 1000.0):
        initial_analysis = fake_quote_analysis(
            {},
            price_map={},
            chain_id=46630,
            token_basis_input_amount=requested_amount,
        )
        probe_records = [_probe_record_item(initial_analysis, requested_amount, ["TZ"])]
        _best_analysis, best_amount, _best_profit, records, selection_meta = _optimize_amount_ladder(
            route={},
            route_symbols=["TZ", "TA", "TB", "TZ"],
            price_map={},
            chain_id=46630,
            amount=requested_amount,
            max_attempts=18,
            analysis=initial_analysis,
            probe_records=probe_records,
            quote_analysis_fn=fake_quote_analysis,
        )
        results.append((best_amount, records[-1].get("amount")))

    assert results[0][0] == pytest.approx(0.1 * 131072.0)
    assert results[1][0] == pytest.approx(10.0 * 131072.0)
    assert results[2][0] == pytest.approx(1000.0 * 131072.0)


def test_normalize_route_symbols_supports_arrow_and_comma_text():
    assert normalize_route_symbols("TZ -> TB -> TA -> TZ") == ["TZ", "TB", "TA", "TZ"]
    assert normalize_route_symbols("ta,tb,tz,ta") == ["TA", "TB", "TZ", "TA"]


def test_official_triangle_route_validation():
    assert is_official_triangle_route(["TA", "TB", "TZ", "TA"])
    assert is_official_triangle_route(["WETH", "ETH", "0XC9F9C8", "WETH"])
    assert is_official_triangle_route(["A", "B", "C", "A"])
    assert not is_official_triangle_route(["TZ", "TB", "TA"])
    assert not is_official_triangle_route(["TZ", "TB", "TA", "TB"])
    assert not is_official_triangle_route(["A", "B", "A", "A"])


def test_official_route_addresses_resolve_from_chain_allowlist():
    addresses = _official_route_addresses(["TZ", "TB", "TA", "TZ"])
    assert addresses == [
        "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E",
        "0x28cc9a0b94aD34A3A0CB2734c8d58aD95862C6c4",
        "0xB169FE96425783D7BA7a9F905fe8dE5f80E3Fdc8",
        "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E",
    ]


def test_official_route_addresses_rejects_ambiguous_weth():
    assert _official_route_addresses(["WETH", "ETH", "0XC9F9C8", "WETH"]) == []


def test_official_route_addresses_rejects_unknown_symbol_instead_of_guessing():
    assert _official_route_addresses(["WETH", "UNKNOWN_BAD_TOKEN", "WETH"]) == []


def test_unsupported_route_returns_friendly_blocked_result():
    result = run_official_v4_arb_route_test(
        route_symbols=["WETH", "ETH", "0XC9F9C8", "WETH"],
        amount=1,
        broadcast=False,
    )
    assert result["ok"] is False
    assert result["error"] == "official_arb_broadcast_route_unsupported"
    assert result["routeSymbols"] == ["WETH", "ETH", "0XC9F9C8", "WETH"]


def test_official_triangle_route_rejects_non_robinhood_testnet_chain():
    result = run_official_v4_arb_route_test(
        route_symbols=["TZ", "TB", "TA", "TZ"],
        amount=1,
        broadcast=True,
        chain_id=56,
    )

    assert result["ok"] is False
    assert result["error"] == "official_arb_broadcast_route_unsupported"


def test_official_triangle_route_rejects_missing_chain_scope():
    result = run_official_v4_arb_route_test(
        route_symbols=["TZ", "TB", "TA", "TZ"],
        amount=1,
        broadcast=True,
    )

    assert result["ok"] is False
    assert result["error"] == "official_arb_broadcast_route_unsupported"


def test_quote_route_test_broadcast_does_not_fallback_to_estimate(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module
    from web.control_panel import app

    monkeypatch.setattr(
        "web.data_routes.dex_surfaces.record_dex_route_test_attempt",
        lambda payload, **kwargs: {"recorded": 0, "ok": True},
    )
    monkeypatch.setattr(
        route_test_module,
        "run_official_v4_arb_route_test",
        lambda *, route_symbols, amount, broadcast, chain_id, route_addresses=None, amount_is_best=False: {
            "ok": False,
            "error": "official_arb_broadcast_route_unsupported",
            "detail": "broadcast unsupported for this route",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        },
    )

    def unexpected_estimate(**kwargs):
        raise AssertionError("broadcast request must not fall back to estimate")

    monkeypatch.setattr(route_test_module, "estimate_route_live_quote", unexpected_estimate)
    monkeypatch.setattr(
        route_test_module,
        "_routes_payload",
        lambda **kwargs: {"routes": []},
    )

    response = app.test_client().post(
        "/api/dex/quote-route/test?chain_id=46630&protocols=uniswap_v4",
        json={
            "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
            "amount": 1,
            "mode": "auto",
            "broadcast": True,
        },
    )

    data = response.get_json()
    assert data["broadcast"] is True
    assert data["broadcastAttempted"] is True
    assert data["broadcastPerformed"] is False
    assert data["error"] == "official_arb_broadcast_route_unsupported"
    assert data["bestAmountCalculation"]["method"] == "static_route_size_analysis_probe_ladder"


def test_recommended_route_test_amount_uses_size_analysis(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module

    monkeypatch.setattr(
        route_test_module,
        "_routes_payload",
        lambda **kwargs: {
            "routes": [
                {
                    "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                    "sizeAnalysis": {
                        "recommendedBorrowInputAmount": 12.5,
                        "tokenAmountSteps": [0.01, 0.1, 1.0, 10.0, 100.0],
                    },
                }
            ]
        },
    )
    amount = recommended_route_test_amount(
        route_symbols=["TZ", "TB", "TA", "TZ"],
        chain_id=46630,
        protocols=["uniswap_v4"],
    )
    assert amount == 12.5


def test_recommended_route_test_amount_falls_back_to_token_steps(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module

    monkeypatch.setattr(
        route_test_module,
        "_routes_payload",
        lambda **kwargs: {
            "routes": [
                {
                    "routeSymbols": ["TZ", "TB", "TZ"],
                    "sizeAnalysis": {
                        "tokenAmountSteps": [0.01, 0.1, 1.0, 10.0, 100.0],
                    },
                }
            ]
        },
    )
    amount = recommended_route_test_amount(
        route_symbols=["TZ", "TB", "TZ"],
        chain_id=46630,
        protocols=["uniswap_v4"],
    )
    assert amount == 0.01


def test_quote_failure_status_detects_liquidity_zero():
    assert _quote_failure_status("robinhood testnet v4 pool manager live liquidity is zero") == "not_enough_liquidity"
    assert _quote_failure_status("robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)") == "not_enough_liquidity"
    assert _quote_failure_status("Robinhood RPC unavailable: HTTP endpoint did not answer") == "rpc_verification_failed"
    assert _quote_failure_status("Robinhood RPC unavailable: request timed out") == "rpc_verification_timeout"
    assert _quote_failure_status(
        "Robinhood Testnet RPC unavailable after 3 connect attempts (10s/20s/30s): endpoint timed out"
    ) == "rpc_verification_failed"
    assert _quote_failure_status("pool not initialized or missing sqrtPriceX96") == "pool_not_initialized"


def test_testnet_rpc_connect_timeouts_default_and_override(monkeypatch):
    from dex.intent_trade.chains.robinhood import config as robinhood_config

    monkeypatch.delenv("DEX_ONCHAIN_RPC_CONNECT_TIMEOUTS_SECONDS", raising=False)
    monkeypatch.delenv("DEX_ONCHAIN_RPC_CONNECT_ATTEMPTS", raising=False)
    assert robinhood_config.testnet_rpc_connect_timeouts() == (10.0, 20.0, 30.0)

    monkeypatch.setenv("DEX_ONCHAIN_RPC_CONNECT_TIMEOUTS_SECONDS", "5,15,40")
    assert robinhood_config.testnet_rpc_connect_timeouts() == (5.0, 15.0, 40.0)

    monkeypatch.delenv("DEX_ONCHAIN_RPC_CONNECT_TIMEOUTS_SECONDS", raising=False)
    monkeypatch.setenv("DEX_ONCHAIN_RPC_CONNECT_ATTEMPTS", "2")
    assert robinhood_config.testnet_rpc_connect_timeouts() == (10.0, 20.0)


def test_route_quote_analysis_returns_missing_identity_for_symbol_only_route():
    from market.binance_market.dex_surface_route_quotes import _route_quote_analysis

    analysis = _route_quote_analysis(
        {
            "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
            "routeAddresses": [],
            "quoteInputs": {"routePools": []},
        },
        price_map={},
        chain_id=46630,
        token_basis_input_amount=1.0,
    )

    assert analysis["terminalStatus"] == "missing_route_identity"
    assert analysis["requiresLiveQuoter"] is False
    assert "route_lacks_complete_token_address_identity" in analysis["warnings"]


def test_route_quote_analysis_uses_route_addresses_without_token_path():
    from market.binance_market.dex_surface_route_quotes import _route_quote_analysis

    addresses = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        "0x0000000000000000000000000000000000000003",
        "0x0000000000000000000000000000000000000001",
    ]
    analysis = _route_quote_analysis(
        {
            "routeSymbols": ["TZ", "TA", "TB", "TZ"],
            "routeAddresses": addresses,
            "quoteInputs": {"routePools": []},
        },
        price_map={},
        chain_id=46630,
        token_basis_input_amount=0.1,
    )

    assert analysis.get("terminalStatus") != "missing_route_identity"
    assert analysis["routeAddresses"] == addresses


def test_quote_route_status_returns_missing_identity_before_live_quoter():
    from market.binance_market.dex_quote_route_common import quote_route_status

    assert quote_route_status(
        {
            "terminalStatus": "missing_route_identity",
            "requiresLiveQuoter": True,
            "livePriceVerified": False,
        }
    ) == "missing_route_identity"


def test_step_failure_status_upgrades_liquidity_error():
    step = {
        "executionTrust": "live_quote_failed",
        "liveQuoteError": "robinhood testnet v4 pool manager live liquidity is zero",
    }
    assert _step_failure_status(step) == "not_enough_liquidity"


def test_best_amount_calculation_from_route_uses_size_analysis():
    from market.binance_market.dex_route_test import best_amount_calculation_from_route

    calc = best_amount_calculation_from_route(
        {
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "sizeAnalysis": {
                "recommendedBorrowInputAmount": 10.0,
                "recommendedBorrowInputAmountReason": "best_positive_observed_net_profit",
                "tokenAmountSteps": [0.1, 1.0, 10.0],
                "rows": [
                    {
                        "borrowAmount": 0.1,
                        "borrowSymbol": "TZ",
                        "referenceNetProfitAmount": 0.001,
                        "referenceNetReturnBps": 100.0,
                        "executableCandidate": True,
                    },
                    {
                        "borrowAmount": 1.0,
                        "borrowSymbol": "TZ",
                        "referenceNetProfitAmount": 0.005,
                        "referenceNetReturnBps": 50.0,
                        "executableCandidate": True,
                    },
                    {
                        "borrowAmount": 10.0,
                        "borrowSymbol": "TZ",
                        "referenceNetProfitAmount": 0.02,
                        "referenceNetReturnBps": 20.0,
                        "executableCandidate": True,
                    },
                ],
            },
        },
        amount=1.0,
    )

    assert calc["recommendedBorrowInputAmount"] == 10.0
    assert len(calc["probeRecords"]) == 3
    assert calc["probeRecords"][0]["amount"] == 0.1
    assert calc["probeRecords"][1]["isBest"] is True
    assert calc["probeRecords"][2]["isRecommended"] is True


def test_best_amount_selection_uses_absolute_profit_not_bps_without_usd_anchor():
    route = {
        "routeSymbols": ["TZ", "TA", "TB", "TZ"],
        "routeTokenPath": [
            {"symbol": "TZ", "decimals": 18},
            {"symbol": "TA", "decimals": 18},
            {"symbol": "TB", "decimals": 18},
            {"symbol": "TZ", "decimals": 18},
        ],
    }

    def quote_analysis(_route, *, price_map, chain_id, notional_usd=1000.0, token_basis_input_amount=None):
        amount = float(token_basis_input_amount or 0)
        return {
            "complete": True,
            "requiresLiveQuoter": True,
            "adapterRequired": True,
            "referenceInputAmount": amount,
            "referenceInputSymbol": "TZ",
            "referenceFinalOutputAmount": amount * 1.1,
            "referenceFinalOutputSymbol": "TZ",
            "referenceNetProfitAmount": amount * 0.1,
            "referenceNetReturnBps": 1000.0 if amount == 0.1 else 10.0,
            "referenceReturnBasis": "borrow_token",
            "referenceReturnTrusted": True,
            "tokenBasisReferenceProfitable": True,
            "tokenBasisProfitable": True,
            "executableCandidate": True,
            "observedNetProfitUsd": None,
            "netProfitUsd": None,
            "referenceNetProfitUsd": None,
        }

    analysis = build_route_size_analysis(
        route,
        price_map={},
        chain_id=46630,
        route_quote_analysis_fn=quote_analysis,
    )

    assert analysis["recommendedBorrowInputAmount"] == 64.0
    assert analysis["recommendedBorrowInputAmountReason"] == "best_positive_absolute_profit"
    assert analysis["recommendedSelectionBasis"] == "borrow_token"
    assert analysis["recommendedSelectionMetric"] == 6.4


def test_best_amount_calculation_includes_selection_metadata():
    from market.binance_market.dex_route_test import best_amount_calculation_from_route

    calc = best_amount_calculation_from_route(
        {
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "sizeAnalysis": {
                "recommendedBorrowInputAmount": 10.0,
                "recommendedBorrowInputAmountReason": "best_positive_absolute_profit",
                "recommendedSelectionMetric": 0.5,
                "recommendedSelectionBasis": "borrow_token",
                "recommendedSelectionRowIndex": 2,
                "rows": [
                    {
                        "borrowAmount": 0.1,
                        "borrowSymbol": "TZ",
                        "referenceNetProfitAmount": 0.001,
                        "referenceNetReturnBps": 100.0,
                        "executableCandidate": True,
                    },
                    {
                        "borrowAmount": 1.0,
                        "borrowSymbol": "TZ",
                        "referenceNetProfitAmount": 0.05,
                        "referenceNetReturnBps": 50.0,
                        "executableCandidate": True,
                    },
                    {
                        "borrowAmount": 10.0,
                        "borrowSymbol": "TZ",
                        "referenceNetProfitAmount": 0.5,
                        "referenceNetReturnBps": 20.0,
                        "executableCandidate": True,
                    },
                ],
            },
        },
        amount=10.0,
    )

    assert calc["selectionMetric"] == 0.5
    assert calc["selectionBasis"] == "borrow_token"
    assert calc["selectedRowIndex"] == 2
    assert calc["recommendedSelectionMetric"] == 0.5
    assert calc["recommendedSelectionBasis"] == "borrow_token"
    assert calc["recommendedSelectionRowIndex"] == 2
    assert calc["probeRecords"][2]["profitAmount"] == 0.5
    assert calc["probeRecords"][2]["profitBasis"] == "borrow_token"


def test_live_candidate_ladder_keeps_small_peak_candidates_with_large_requested_amount():
    from market.binance_market.dex_route_helpers import _candidate_amount_ladder

    route = {
        "sizeAnalysis": {
            "recommendedBorrowInputAmount": 0.1,
            "tokenAmountSteps": [0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0],
        },
    }
    small_ladder = _candidate_amount_ladder(route=route, amount=0.1, max_attempts=12)
    large_ladder = _candidate_amount_ladder(route=route, amount=1000.0, max_attempts=12)

    # The ladder is dynamic: it halves down and doubles up from the base
    # amount instead of reading a fixed multiplier table.
    assert small_ladder[0] == pytest.approx(0.1 / 64.0)
    assert large_ladder[0] == pytest.approx(1000.0 / 64.0)
    assert pytest.approx(0.1) in small_ladder
    assert pytest.approx(1000.0) in large_ladder
    assert pytest.approx(1000.0 * 2.0) in large_ladder


def test_candidate_ladder_includes_default_peak_points_without_size_analysis():
    from market.binance_market.dex_route_helpers import _candidate_amount_ladder

    ladder = _candidate_amount_ladder(route={}, amount=1000.0, max_attempts=12)

    assert pytest.approx(1000.0 * 1.0) in ladder
    assert ladder[0] < 1000.0 < ladder[-1]


def test_amount_ladder_halves_between_profitable_and_unprofitable_neighbors():
    from market.binance_market.dex_route_helpers import _optimize_amount_ladder, _probe_record_item

    def fake_quote_analysis(route, *, price_map, chain_id, token_basis_input_amount):
        amount = float(token_basis_input_amount or 0)
        if amount >= 1.0:
            profit = -1.0
        elif 0.7 < amount < 0.8:
            profit = 2.5
        elif amount >= 0.6:
            profit = 1.5
        elif amount >= 0.5:
            profit = 2.3
        else:
            profit = 1.8
        return {
            "referenceInputAmount": amount,
            "referenceInputSymbol": "TZ",
            "referenceFinalOutputAmount": amount + profit,
            "referenceFinalOutputSymbol": "TZ",
            "referenceNetProfitAmount": profit,
            "referenceNetReturnBps": 100.0,
            "maxStaticDepthImpactBps": None,
            "warnings": [],
            "steps": [],
        }

    initial_analysis = fake_quote_analysis({}, price_map={}, chain_id=46630, token_basis_input_amount=0.5)
    probe_records = [_probe_record_item(initial_analysis, 0.5, ["TZ"])]
    route = {
        "sizeAnalysis": {
            "recommendedBorrowInputAmount": 0.1,
            "tokenAmountSteps": [0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0],
        },
    }
    best_analysis, best_amount, best_profit, records, selection_meta = _optimize_amount_ladder(
        route=route,
        route_symbols=["TZ", "TA", "TB", "TZ"],
        price_map={},
        chain_id=46630,
        amount=0.5,
        max_attempts=18,
        analysis=initial_analysis,
        probe_records=probe_records,
        quote_analysis_fn=fake_quote_analysis,
    )

    assert best_profit == 2.5
    assert best_amount == 0.75
    assert any(abs(float(record.get("amount") or 0) - 0.75) < 1e-9 for record in records)
    assert not any(abs(float(record.get("amount") or 0) - 0.875) < 1e-9 for record in records)


def test_amount_ladder_stops_at_first_non_improving_probe_without_crossing_valley():
    from market.binance_market.dex_route_helpers import _optimize_amount_ladder, _probe_record_item

    def fake_quote_analysis(route, *, price_map, chain_id, token_basis_input_amount):
        amount = float(token_basis_input_amount or 0)
        if amount == 2.0:
            profit = 5.0
        elif amount == 4.0:
            profit = 40.0
        elif amount > 4.0:
            profit = 0.0
        elif amount >= 1.0:
            profit = 10.0
        else:
            profit = amount
        return {
            "referenceInputAmount": amount,
            "referenceInputSymbol": "TZ",
            "referenceFinalOutputAmount": amount + profit,
            "referenceFinalOutputSymbol": "TZ",
            "referenceNetProfitAmount": profit,
            "referenceNetReturnBps": 100.0,
            "maxStaticDepthImpactBps": None,
            "warnings": [],
            "steps": [],
        }

    initial_analysis = fake_quote_analysis({}, price_map={}, chain_id=46630, token_basis_input_amount=1.0)
    probe_records = [_probe_record_item(initial_analysis, 1.0, ["TZ"])]
    route = {
        "sizeAnalysis": {
            "recommendedBorrowInputAmount": 0.1,
            "tokenAmountSteps": [1.0, 2.0, 4.0, 8.0, 16.0],
        },
    }
    _best_analysis, best_amount, best_profit, records, _selection_meta = _optimize_amount_ladder(
        route=route,
        route_symbols=["TZ", "TA", "TB", "TZ"],
        price_map={},
        chain_id=46630,
        amount=1.0,
        max_attempts=18,
        analysis=initial_analysis,
        probe_records=probe_records,
        quote_analysis_fn=fake_quote_analysis,
    )

    assert best_amount == 1.0
    assert best_profit == 10.0
    assert not any(abs(float(record.get("amount") or 0) - 4.0) < 1e-9 for record in records)


def test_run_official_v4_arb_route_test_payload_includes_best_amount_calculation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import market.binance_market.dex_route_test as route_test_module

    script = tmp_path / "official-arb.js"
    script.write_text("", encoding="utf-8")
    dex_dir = tmp_path / "DEX"
    dex_dir.mkdir()
    monkeypatch.setattr(route_test_module, "OFFICIAL_ARB_SCRIPT", script)
    monkeypatch.setattr(route_test_module, "OFFICIAL_DEX_DIR", dex_dir)
    monkeypatch.setattr(
        route_test_module,
        "_probe_robinhood_testnet_rpc",
        lambda: ("https://rpc.testnet.chain.robinhood.com", 12345),
    )

    def fake_routes_payload(**kwargs):
        return {
            "routes": [
                {
                    "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                    "sizeAnalysis": {
                        "recommendedBorrowInputAmount": 10.0,
                        "recommendedBorrowInputAmountReason": "best_positive_observed_net_profit",
                        "tokenAmountSteps": [0.1, 1.0, 10.0],
                        "rows": [
                            {
                                "borrowAmount": 0.1,
                                "borrowSymbol": "TZ",
                                "referenceNetProfitAmount": 0.001,
                                "referenceNetReturnBps": 100.0,
                                "executableCandidate": True,
                            },
                            {
                                "borrowAmount": 1.0,
                                "borrowSymbol": "TZ",
                                "referenceNetProfitAmount": 0.005,
                                "referenceNetReturnBps": 50.0,
                                "executableCandidate": True,
                            },
                            {
                                "borrowAmount": 10.0,
                                "borrowSymbol": "TZ",
                                "referenceNetProfitAmount": 0.02,
                                "referenceNetReturnBps": 20.0,
                                "executableCandidate": True,
                            },
                        ],
                    },
                }
            ]
        }

    monkeypatch.setattr(route_test_module, "_routes_payload", fake_routes_payload)

    class FakeProc:
        returncode = 0
        stdout = 'OFFICIAL_V4_ARB_RESULT_JSON {"ok":true,"txHash":"0x123","profit":"1000000000000000","outAmount":"2000000000000000"}'
        stderr = ""

    monkeypatch.setattr(
        route_test_module,
        "subprocess",
        SimpleNamespace(run=lambda *args, **kwargs: FakeProc()),
    )

    result = route_test_module.run_official_v4_arb_route_test(
        route_symbols=["TZ", "TB", "TA", "TZ"],
        amount=1.0,
        broadcast=True,
        chain_id=46630,
    )

    assert result["ok"] is True
    assert result["txHash"] == "0x123"
    assert result["profit"] == pytest.approx(0.001)
    assert result["profitHuman"] == pytest.approx(0.001)
    assert result["profitRaw"] == "1000000000000000"
    assert result["outAmount"] == pytest.approx(0.002)
    assert result["outAmountHuman"] == pytest.approx(0.002)
    assert result["bestAmountCalculation"]["recommendedBorrowInputAmount"] == 10.0
    assert len(result["probe_records"]) == 3
    assert result["probe_records"][1]["isBest"] is True
    assert result["probe_records"][2]["isRecommended"] is True
    assert result["analysis"]["bestAmountCalculation"]["recommendedBorrowInputAmount"] == 10.0


def test_raw_amount_to_human_converts_raw_wei_before_ui_display():
    assert _raw_amount_to_human("16000000000000000000") == pytest.approx(16.0)
    assert _raw_amount_to_human("1000000000000000") == pytest.approx(0.001)
    assert _raw_amount_to_human(None) is None


def test_fetch_dex_tx_receipt_rejects_missing_tx_hash():
    from market.binance_market.dex_route_test import fetch_dex_tx_receipt

    result = fetch_dex_tx_receipt(chain_id=46630, tx_hash="")
    assert result["ok"] is False
    assert result["error"] == "tx_hash_required"


def test_dex_submission_onchain_check_route(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module
    from web.control_panel import app

    def fake_fetch_dex_tx_receipt(*, chain_id, tx_hash):
        assert int(chain_id) == 46630
        assert tx_hash == "0xabc"
        return {
            "ok": True,
            "chain_id": 46630,
            "network": "robinhood-testnet",
            "explorer_url": "https://explorer.testnet.chain.robinhood.com",
            "tx_hash": tx_hash,
            "status": "confirmed",
            "receipt": {"blockNumber": 12345, "status": 1},
            "transaction": {"from": "0xfrom", "to": "0xto", "blockNumber": 12345},
        }

    monkeypatch.setattr(route_test_module, "fetch_dex_tx_receipt", fake_fetch_dex_tx_receipt)

    response = app.test_client().post(
        "/api/dex/submissions/check-onchain?chain_id=46630",
        json={"tx_hash": "0xabc"},
    )
    data = response.get_json()
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["status"] == "confirmed"
    assert data["receipt"]["blockNumber"] == 12345
