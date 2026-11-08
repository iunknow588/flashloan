from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest


_SCANNERS: list[Any] = []


@pytest.fixture(autouse=True)
def _stop_background_review_pipelines():
    _SCANNERS.clear()
    yield
    for scanner in _SCANNERS:
        scanner.stop()
    _SCANNERS.clear()


def test_route_evidence_dedup_key_keeps_reverse_direction():
    from dex.runtime.dex_opportunity_helpers import _route_evidence_dedup_key

    forward = {"routeAddresses": ["0xTZ", "0xTB", "0xTA", "0xTZ"]}
    reverse = {"routeAddresses": ["0xTZ", "0xTA", "0xTB", "0xTZ"]}
    symbol_forward = {"routeSymbols": ["TZ", "TB", "TA", "TZ"]}
    symbol_reverse = {"routeSymbols": ["TZ", "TA", "TB", "TZ"]}

    assert _route_evidence_dedup_key(forward) != _route_evidence_dedup_key(reverse)
    assert _route_evidence_dedup_key(symbol_forward) != _route_evidence_dedup_key(symbol_reverse)


def test_order_attempts_for_execution_keeps_temporary_routes_last():
    from dex.runtime.dex_opportunity_auto_execute import (
        _order_attempts_for_execution,
        _order_routes_for_review,
    )

    default_attempt = {
        "route_path": ["TZ", "TA", "TB", "TZ"],
        "quote": {
            "temporaryRoute": False,
            "temporaryRoutePolicy": {},
        },
    }
    temporary_attempt = {
        "route_path": ["TZ", "TA2", "TB", "TZ"],
        "quote": {
            "temporaryRoute": True,
            "temporaryRoutePolicy": {
                "temporaryRoute": True,
                "executeLast": True,
                "notForDefaultScan": True,
            },
        },
    }

    ordered = _order_attempts_for_execution([temporary_attempt, default_attempt])

    assert ordered == [default_attempt, temporary_attempt]

    default_route = {
        "routeSymbols": ["TZ", "TA", "TB", "TZ"],
        "temporaryRoute": False,
        "temporaryRoutePolicy": {},
    }
    temporary_route = {
        **default_route,
        "routeSymbols": ["TZ", "TA2", "TB", "TZ"],
        "temporaryRoute": True,
        "temporaryRoutePolicy": {"temporaryRoute": True, "executeLast": True},
    }
    ordered_routes = _order_routes_for_review([temporary_route, default_route])

    assert ordered_routes == [default_route, temporary_route]


def test_closed_loop_review_group_key_isolates_temporary_replacement():
    from dex.runtime.dex_opportunity_route_runner import _closed_loop_review_group_key

    default_route = {
        "routeAddresses": ["0xTZ", "0xTB", "0xTA", "0xTZ"],
        "poolPathKey": "default_pool_a|default_pool_b|default_pool_c",
        "temporaryRoute": False,
        "temporaryRoutePolicy": {},
    }
    temporary_route = {
        **default_route,
        "poolPathKey": "temporary_pool_a|bridge_pool|temporary_pool_c",
        "temporaryRoute": True,
        "temporaryRoutePolicy": {
            "temporaryRoute": True,
            "executeLast": True,
            "notForDefaultScan": True,
        },
    }

    assert _closed_loop_review_group_key(default_route) != _closed_loop_review_group_key(temporary_route)


def _quote_payload_for_robinhood_testnet(*, symbol_pair: str = "TZ -> TA -> TB -> TZ") -> dict:
    return {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "summary": {
            "routeCount": 1,
            "completeStaticQuoteCount": 1,
            "executableCandidateCount": 0,
            "profitableRouteCount": 1,
            "liveQuoterRequiredCount": 1,
            "bestNetProfitUsd": 0.5,
        },
        "routes": [
            {
                "symbolPair": symbol_pair,
                "routeSymbols": symbol_pair.split(" -> "),
                "routeAddresses": [
                    "0x" + "1" * 40,
                    "0x" + "2" * 40,
                    "0x" + "3" * 40,
                    "0x" + "1" * 40,
                ],
                "quoteStatus": "requires_live_quoter",
                "quoteInputs": {
                    "candidatePools": [],
                    "quoteRequests": [{"venue": "uniswap_v4", "status": "sdk_adapter_required"}],
                },
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": True,
                    "referenceInputSymbol": "TZ",
                    "referenceInputAmount": 1.0,
                    "referenceNetProfitUsd": 0.5,
                    "steps": [
                        {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TA"},
                        {"hop": 2, "fromSymbol": "TA", "toSymbol": "TB"},
                        {"hop": 3, "fromSymbol": "TB", "toSymbol": "TZ"},
                    ],
                },
            }
        ],
    }


def _profitable_live_evidence(route) -> dict:
    return {
        "status": "live_verified",
        "priceEvidenceMode": "live_verified",
        "livePriceVerified": True,
        "trustedForExecution": False,
        "readyForTransactionPreflight": True,
        "profitable": True,
        "blockNumber": 123456,
        "routeSymbols": route.get("routeSymbols") or [],
        "routeAddresses": route.get("routeAddresses") or [],
        "hops": [{"hop": 1, "status": "live_verified"}],
        "costModel": {
            "selectedBorrowInputAmount": 1.5,
            "selectedBorrowInputAmountRaw": 1500000000000000000,
            "borrowInputAmount": 1.5,
            "netProfitUsd": 0.5,
            "netProfitAmount": 0.5,
            "netProfitBps": 33.0,
            "profitBasis": "borrow_token",
        },
        "validation": {
            "allHopsQuoted": True,
            "routeClosed": True,
            "profitableAfterCosts": True,
            "finalOutputSymbol": "TZ",
        },
        "blockers": [],
        "bestAmountConfirmed": True,
        "transactionDevelopment": {"status": "ready_for_transaction_preflight_development", "executionEnabled": False},
    }


def _scanner(tmp_path, *, evidence=None, callback=None, enabled=None, cooldown=600.0, background_refresh=True):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    def onchain_evidence_provider(route, **kwargs):
        if evidence is not None:
            return evidence(route, **kwargs)
        return _profitable_live_evidence(route)

    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda *, chain_id=None, protocols=None: _quote_payload_for_robinhood_testnet(),
        onchain_evidence_provider=onchain_evidence_provider,
        trigger_context_provider=lambda **kwargs: {"source": "test", "triggerBlockNumber": 123456},
        auto_execute_callback=callback,
        auto_execute_enabled=enabled,
        auto_execute_cooldown_seconds=cooldown,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
        max_onchain_evidence_routes=1,
    )
    if not background_refresh:
        scanner.closed_loop_review_pipeline.refresh_fn = lambda task: {"routes": []}
    _SCANNERS.append(scanner)
    return scanner


def test_auto_execute_disabled_by_default_keeps_callback_unused(tmp_path, monkeypatch):
    import runtime.dex_opportunity_scanner as scanner_module

    monkeypatch.setattr(scanner_module, "default_auto_execute_env_enabled", lambda: False)
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append((route_symbols, amount, broadcast, chain_id))
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == []
    assert result["scanner"]["auto_execute_enabled"] is False
    assert result["scanner"]["auto_execute_callback_configured"] is True
    assert "autoExecuteEnabled" not in result["snapshot"]["recording"]
    assert scanner._auto_execute_count == 0
    assert scanner._last_auto_execute_at is None


def test_summary_counts_static_reference_as_pending_live_review():
    from runtime.dex_opportunity_helpers import _refresh_payload_summary

    payload = {
        "summary": {},
        "routes": [
            {
                "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                "quoteAnalysis": {
                    "live_onchain_evidence": {
                        "status": "static_reference_pending_live_review",
                        "costModel": {},
                    }
                },
            }
        ],
    }

    summary = _refresh_payload_summary(payload)

    assert summary["onchainEvidenceReviewedRouteCount"] == 0
    assert summary["onchainEvidencePendingRouteCount"] == 1
    assert summary["liveQuoterRequiredCount"] == 1
    assert summary["autoExecutableClosedLoopCount"] == 0


def test_summary_counts_readonly_live_reference_as_reviewed():
    from runtime.dex_opportunity_helpers import _refresh_payload_summary

    payload = {
        "summary": {},
        "routes": [
            {
                "routeSymbols": ["TSLA", "ETH", "0X71178B", "TSLA"],
                "live_onchain_evidence": {
                    "status": "live_quote_reference_only",
                    "costModel": {},
                },
            }
        ],
    }

    summary = _refresh_payload_summary(payload)

    assert summary["onchainEvidenceReviewedRouteCount"] == 1
    assert summary["onchainEvidencePendingRouteCount"] == 0


def test_auto_execute_scan_now_defers_to_closed_loop_deep_review(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append({"route_symbols": route_symbols, "amount": amount, "broadcast": broadcast, "chain_id": chain_id})
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback, enabled=True, background_refresh=False)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == []
    recording = result["snapshot"]["recording"]
    assert recording["source"] == "closed_loop_deep_review"
    assert recording["autoExecuteEnabled"] is True
    assert recording["autoExecuted"] == 0
    assert scanner._auto_execute_count == 0


def test_auto_execute_scan_now_skips_old_evidence_timeout_path(tmp_path):
    calls = []

    def callback(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "txHash": "0xtx"}

    scanner = _scanner(tmp_path, callback=callback, enabled=True, background_refresh=False)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == []
    recording = result["snapshot"]["recording"]
    assert recording["source"] == "closed_loop_deep_review"
    assert recording["autoExecuteSkipped"] == 0
    assert recording["autoExecuted"] == 0


def test_auto_execute_scan_now_does_not_use_legacy_runner_cooldown(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append({"route_symbols": route_symbols, "amount": amount, "broadcast": broadcast, "chain_id": chain_id})
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback, enabled=True, cooldown=3600.0, background_refresh=False)

    first = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])
    second = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == []
    assert first["snapshot"]["recording"]["source"] == "closed_loop_deep_review"
    assert second["snapshot"]["recording"]["source"] == "closed_loop_deep_review"


def test_fixed_refresh_deep_mode_runs_auto_execute_when_enabled(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []

    scanner = _scanner(tmp_path, enabled=True)
    payload = _quote_payload_for_robinhood_testnet()
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.5)

    def fake_run_official(**kwargs):
        symbols = kwargs.get("route_symbols") or []
        addresses = kwargs.get("route_addresses") or []
        broadcast = bool(kwargs.get("broadcast"))
        calls.append({"route_symbols": symbols, "amount": kwargs.get("amount"), "broadcast": broadcast, "chain_id": kwargs.get("chain_id")})
        return {
            "ok": True,
            "mode": "dex_route_broadcast_test",
            "actionStage": "onchain_execution" if broadcast else "onchain_preflight",
            "quoteStatus": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": True,
            "profitable": True,
            "profit": 0.5,
            "bestAmountConfirmed": True,
            "routeSymbols": symbols,
            "routeAddresses": addresses,
            "amount": kwargs.get("amount"),
            "bestAmount": kwargs.get("amount"),
            "steps": [],
            "analysis": {
                "referenceNetProfitAmount": 0.5,
                "referenceNetReturnBps": 33.3,
            },
            "txHash": "0xtx" if broadcast else None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    assert calls == [
        {
            "route_symbols": ["TZ", "TA", "TB", "TZ"],
            "amount": 1.5,
            "broadcast": False,
            "chain_id": 46630,
        },
        {
            "route_symbols": ["TZ", "TA", "TB", "TZ"],
            "amount": 3.0,
            "broadcast": False,
            "chain_id": 46630,
        },
        {
            "route_symbols": ["TZ", "TA", "TB", "TZ"],
            "amount": 1.125,
            "broadcast": False,
            "chain_id": 46630,
        },
        {
            "route_symbols": ["TZ", "TA", "TB", "TZ"],
            "amount": 2.25,
            "broadcast": False,
            "chain_id": 46630,
        },
        {
            "route_symbols": ["TZ", "TA", "TB", "TZ"],
            "amount": 1.5,
            "broadcast": True,
            "chain_id": 46630,
        },
    ]
    assert (done.result or {}).get("auto_execute", {}).get("autoExecuted") == 1
    assert (done.result or {}).get("recording", {}).get("review_count") == 1
    status = scanner.status(chain_id=46630)
    assert status["closed_loop_scan_count"] == 1
    assert status["closed_loop_review_count"] == 1
    assert status["auto_broadcast_count"] >= 1
    assert scanner._auto_execute_count == 1
    refreshed_route = (done.result or {}).get("routes", [])[0]
    refreshed_evidence = refreshed_route.get("live_onchain_evidence") or {}
    refreshed_costs = refreshed_evidence.get("costModel") or {}
    assert refreshed_costs.get("selectedBorrowInputAmount") == 1.5
    assert refreshed_costs.get("selectedBorrowInputAmountConfirmedMax") is True
    assert refreshed_costs.get("netProfitAmount") == 0.5
    assert refreshed_route.get("bestAmountConfirmed") is True
    assert refreshed_route.get("bestAmountMissing") is False


def test_fixed_refresh_deep_mode_skips_broadcast_when_manual_review_not_profitable(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []
    scanner = _scanner(tmp_path, enabled=True)
    payload = _quote_payload_for_robinhood_testnet()
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.5)

    def fake_run_official(**kwargs):
        symbols = kwargs.get("route_symbols") or []
        calls.append(
            {
                "broadcast": bool(kwargs.get("broadcast")),
                "symbols": symbols,
                "amount": kwargs.get("amount"),
            }
        )
        return {
            "ok": True,
            "quoteStatus": "route_unprofitable",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": False,
            "profitable": False,
            "profit": -0.5,
            "bestAmountConfirmed": True,
            "routeSymbols": symbols,
            "routeAddresses": kwargs.get("route_addresses") or [],
            "amount": kwargs.get("amount"),
            "bestAmount": kwargs.get("amount"),
            "steps": [],
            "analysis": {"referenceNetProfitAmount": -0.5, "referenceNetReturnBps": -10.0},
            "txHash": None,
            "broadcast": False,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    assert all(not call["broadcast"] for call in calls)
    assert calls[0]["amount"] == 1.5
    assert calls[-1]["amount"] == 0.001
    assert len(calls) == 12
    auto_execute = (done.result or {}).get("auto_execute", {})
    assert auto_execute.get("autoExecuted") == 0
    assert auto_execute.get("autoExecuteSkipped") == 1
    assert (done.result or {}).get("routes", [])[0].get("auto_execute", {}).get("skipped") is True
    route = (done.result or {}).get("routes", [])[0]
    size_probe = (route.get("__liveEstimate") or {}).get("sizeProbe") or {}
    assert size_probe.get("searchStatus") == "not_profitable_after_costs"
    attempt_amounts = [entry.get("amount") for entry in size_probe.get("attempts") or []]
    assert 0.001 in attempt_amounts


def test_fixed_refresh_deep_mode_shrinks_until_positive_profit_and_broadcasts(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []
    scanner = _scanner(tmp_path, enabled=True)
    payload = _quote_payload_for_robinhood_testnet()
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.0)

    def fake_run_official(**kwargs):
        broadcast = bool(kwargs.get("broadcast"))
        amount = kwargs.get("amount")
        symbols = kwargs.get("route_symbols") or []
        calls.append({"broadcast": broadcast, "symbols": symbols, "amount": amount})
        profitable = amount == 0.25
        profit = 0.2 if profitable else -0.5
        return {
            "ok": True,
            "quoteStatus": "live_verified" if profitable else "route_unprofitable",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": profitable,
            "profitable": profitable,
            "profit": profit,
            "profitHuman": profit,
            "bestAmountConfirmed": True,
            "routeSymbols": symbols,
            "routeAddresses": kwargs.get("route_addresses") or [],
            "amount": amount,
            "bestAmount": amount,
            "steps": [],
            "analysis": {
                "referenceNetProfitAmount": profit,
                "referenceNetReturnBps": 33.3 if profitable else -10.0,
            },
            "txHash": "0xsuccess" if broadcast else None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    preflights = [call for call in calls if not call["broadcast"]]
    broadcasts = [call for call in calls if call["broadcast"]]
    assert len(broadcasts) == 1
    assert broadcasts[0] == {"broadcast": True, "symbols": ["TZ", "TA", "TB", "TZ"], "amount": 0.25}
    assert preflights[0]["amount"] == 1.0
    probe_amounts = [call["amount"] for call in preflights]
    assert 1.0 in probe_amounts
    assert 0.5 in probe_amounts
    assert 0.25 in probe_amounts
    assert 0.375 in probe_amounts
    assert all(amount >= 0.125 for amount in probe_amounts)
    assert 0.001 not in probe_amounts
    auto_execute = (done.result or {}).get("auto_execute", {})
    assert auto_execute.get("autoExecuted") == 1
    route = (done.result or {}).get("routes", [])[0]
    size_probe = (route.get("__liveEstimate") or {}).get("sizeProbe") or {}
    assert size_probe.get("searchStatus") == "profitable_amount_found"
    assert size_probe.get("selectedProfitableAmount") == 0.25
    assert size_probe.get("minProbeAmountHuman") == "0.001 borrow token unit"
    attempt_amounts = [entry.get("amount") for entry in size_probe.get("attempts") or []]
    assert 0.25 in attempt_amounts
    assert 0.375 in attempt_amounts
    assert all(amount >= 0.125 for amount in attempt_amounts)


def test_fixed_refresh_deep_mode_expands_after_initial_profit_and_broadcasts(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []
    scanner = _scanner(tmp_path, enabled=True)
    payload = _quote_payload_for_robinhood_testnet()
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.0)

    profitable_profits = {
        1.0: 1.0,
        1.25: 1.25,
        1.5: 1.5,
        1.75: 1.75,
        2.0: 2.0,
        3.0: 1.5,
    }

    def fake_run_official(**kwargs):
        broadcast = bool(kwargs.get("broadcast"))
        amount = kwargs.get("amount")
        symbols = kwargs.get("route_symbols") or []
        profit = profitable_profits.get(amount, -1.0)
        profitable = profit > 0
        calls.append({"broadcast": broadcast, "symbols": symbols, "amount": amount})
        return {
            "ok": True,
            "quoteStatus": "live_verified" if profitable else "route_unprofitable",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": profitable,
            "profitable": profitable,
            "profit": profit,
            "profitHuman": profit,
            "bestAmountConfirmed": True,
            "routeSymbols": symbols,
            "routeAddresses": kwargs.get("route_addresses") or [],
            "amount": amount,
            "bestAmount": amount,
            "steps": [],
            "analysis": {
                "referenceNetProfitAmount": profit,
                "referenceNetReturnBps": 33.3 if profitable else -10.0,
            },
            "txHash": "0xsuccess" if broadcast else None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    preflights = [call for call in calls if not call["broadcast"]]
    broadcasts = [call for call in calls if call["broadcast"]]
    assert len(broadcasts) == 1
    assert broadcasts[0]["amount"] == 2.0
    probe_amounts = [call["amount"] for call in preflights]
    assert probe_amounts[0] == 1.0
    assert 2.0 in probe_amounts
    assert 4.0 in probe_amounts
    assert 1.5 in probe_amounts
    assert 3.0 in probe_amounts
    assert all(amount >= 1.0 for amount in probe_amounts)
    auto_execute = (done.result or {}).get("auto_execute", {})
    assert auto_execute.get("autoExecuted") == 1
    route = (done.result or {}).get("routes", [])[0]
    size_probe = (route.get("__liveEstimate") or {}).get("sizeProbe") or {}
    assert size_probe.get("searchStatus") == "profitable_amount_found"
    assert size_probe.get("selectedProfitableAmount") == 2.0
    assert size_probe.get("selectedProfitAmount") == 2.0
    attempt_amounts = [entry.get("amount") for entry in size_probe.get("attempts") or []]
    assert 1.0 in attempt_amounts
    assert 2.0 in attempt_amounts
    assert 4.0 in attempt_amounts
    assert all(amount >= 1.0 for amount in attempt_amounts)


def test_manual_review_is_profitable_requires_ok_and_positive_closed_loop_token_profit():
    from dex.runtime.dex_opportunity_review_engine import DexOpportunityReviewEngineMixin

    is_profitable = DexOpportunityReviewEngineMixin._manual_review_is_profitable
    assert is_profitable({"ok": True, "profitable": True, "profit": "-0.5"}) is False
    assert is_profitable({"ok": True, "profitable": True}) is False
    assert is_profitable({"ok": True, "profit": "0.002743052111607823"}) is True
    assert is_profitable({"ok": True, "profitHuman": "0.1"}) is True
    assert is_profitable({"ok": True, "analysis": {"netProfitAmount": "0.25"}}) is True
    assert (
        is_profitable(
            {
                "ok": True,
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "0.002743052111607823"},
                }
            }
        )
        is True
    )
    assert (
        is_profitable(
            {
                "ok": True,
                "evidence": {
                    "referenceNetProfitAmount": "0.002743052111607823",
                }
            }
        )
        is True
    )
    assert is_profitable({"profit": "0.002743052111607823"}) is False
    assert is_profitable({"ok": True, "profitable": False, "profit": 0}) is False
    assert is_profitable({}) is False
    assert is_profitable({"ok": True, "profit": "-0.002743052111607823"}) is False
    assert (
        is_profitable(
            {
                "ok": True,
                "live_onchain_evidence": {"costModel": {"netProfitAmount": "0"}},
            }
        )
        is False
    )


def test_manual_profit_amount_extracts_borrow_token_profit_from_evidence():
    from dex.runtime.dex_opportunity_review_engine import DexOpportunityReviewEngineMixin

    profit_amount = DexOpportunityReviewEngineMixin._manual_profit_amount
    assert profit_amount({"profit": "0.25"}) == 0.25
    assert profit_amount({"analysis": {"netProfitAmount": "0.5"}}) == 0.5
    assert (
        profit_amount(
            {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "0.125"},
                }
            }
        )
        == 0.125
    )
    assert profit_amount({"evidence": {"referenceNetProfitAmount": "0.75"}}) == 0.75
    assert profit_amount({"profitable": True}) is None
    assert profit_amount({}) is None
    assert profit_amount({"profit": "-0.5"}) == -0.5


def test_auto_execute_skip_reason_requires_consistent_live_evidence_and_positive_profit():
    from dex.runtime.dex_opportunity_auto_execute import _auto_execute_skip_reason

    consistent_positive = {
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "readyForTransactionPreflight": True,
                "blockers": [],
                "costModel": {
                    "netProfitAmount": "0.002743052111607823",
                },
            }
        }
    }
    assert _auto_execute_skip_reason(consistent_positive, chain_id=46630) is None

    inconsistent_positive = {
        "quote": {
            "live_onchain_evidence": {
                "status": "static_reference_pending_live_review",
                "readyForTransactionPreflight": False,
                "bestAmountConfirmed": False,
                "hops": [],
                "blockNumber": None,
                "costModel": {
                    "netProfitAmount": "0.002743052111607823",
                },
            }
        }
    }
    assert _auto_execute_skip_reason(inconsistent_positive, chain_id=46630) == "chain_review_inconsistent"

    zero_profit = {
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "readyForTransactionPreflight": True,
                "bestAmountConfirmed": True,
                "hops": [{"hop": 1}],
                "blockers": [],
                "costModel": {"netProfitAmount": "0"},
            }
        }
    }
    assert _auto_execute_skip_reason(zero_profit, chain_id=46630) == "not_profitable"

    gas_failed = {
        "quote": {
            "live_onchain_evidence": {
                "status": "rpc_verification_failed",
                "errorKind": "gas_estimation_failed",
                "costModel": {"netProfitAmount": None},
            }
        }
    }
    assert _auto_execute_skip_reason(gas_failed, chain_id=46630) == "gas_estimation_failed"

    rpc_failed = {
        "quote": {
            "live_onchain_evidence": {
                "status": "rpc_verification_failed",
                "costModel": {"netProfitAmount": None},
            }
        }
    }
    assert _auto_execute_skip_reason(rpc_failed, chain_id=46630) == "rpc_verification_failed"


def test_evidence_ready_for_auto_execute_requires_live_consistent_profit():
    from dex.runtime.dex_opportunity_helpers import _evidence_ready_for_auto_execute

    static_positive = {
        "status": "static_reference_pending_live_review",
        "readyForTransactionPreflight": False,
        "bestAmountConfirmed": False,
        "hops": [],
        "blockNumber": None,
        "costModel": {
            "profitBasis": "borrow_token",
            "netProfitAmount": "0.002743052111607823",
        },
    }
    assert _evidence_ready_for_auto_execute(static_positive) is False

    live_consistent = {
        "status": "live_verified",
        "readyForTransactionPreflight": True,
        "bestAmountConfirmed": True,
        "blockers": [],
        "hops": [{"hop": 1}],
        "costModel": {
            "profitBasis": "borrow_token",
            "netProfitAmount": "0.002743052111607823",
        },
    }
    assert _evidence_ready_for_auto_execute(live_consistent) is True

    tz_unprofitable = {
        "status": "live_verified",
        "readyForTransactionPreflight": True,
        "bestAmountConfirmed": True,
        "hops": [{"hop": 1}],
        "blockers": [],
        "costModel": {"netProfitAmount": "-0.002743052111607823"},
    }
    assert _evidence_ready_for_auto_execute(tz_unprofitable) is False


def test_auto_execute_broadcast_without_tx_hash_is_not_counted_as_success(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []
    scanner = _scanner(tmp_path, enabled=True)
    payload = _quote_payload_for_robinhood_testnet()
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.0)

    def fake_run_official(**kwargs):
        broadcast = bool(kwargs.get("broadcast"))
        calls.append({"broadcast": broadcast, "symbols": kwargs.get("route_symbols") or []})
        return {
            "ok": True,
            "quoteStatus": "live_verified",
            "readyForTransactionPreflight": True,
            "bestAmountConfirmed": True,
            "profitable": True,
            "profit": "0.5",
            "profitHuman": "0.5",
            "routeSymbols": kwargs.get("route_symbols") or [],
            "routeAddresses": kwargs.get("route_addresses") or [],
            "amount": kwargs.get("amount"),
            "bestAmount": kwargs.get("amount"),
            "steps": [],
            "analysis": {"referenceNetProfitAmount": 0.5, "referenceNetReturnBps": 33.3},
            "txHash": None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    assert calls == [
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": True, "symbols": ["TZ", "TA", "TB", "TZ"]},
    ]
    auto_execute = (done.result or {}).get("auto_execute", {})
    assert auto_execute.get("autoExecuted") == 0
    assert auto_execute.get("autoExecuteFailed") == 1
    route = (done.result or {}).get("routes", [])[0]
    assert route.get("auto_execute", {}).get("executed") is False


def test_fixed_refresh_deep_mode_broadcasts_tz_style_profit_without_hops_block_or_usd(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []
    scanner = _scanner(tmp_path, enabled=True)
    payload = _quote_payload_for_robinhood_testnet()
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.0)

    def fake_run_official(**kwargs):
        symbols = list(kwargs.get("route_symbols") or [])
        broadcast = bool(kwargs.get("broadcast"))
        calls.append({"broadcast": broadcast, "symbols": symbols})
        return {
            "ok": True,
            "quoteStatus": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": False,
            "bestAmountConfirmed": False,
            "profitable": True,
            "profit": "0.002743052111607823",
            "profitHuman": "0.002743052111607823",
            "routeSymbols": symbols,
            "routeAddresses": kwargs.get("route_addresses") or [],
            "amount": kwargs.get("amount"),
            "bestAmount": kwargs.get("amount"),
            "steps": [],
            "hops": [],
            "blockNumber": None,
            "analysis": {
                "referenceNetProfitAmount": "0.002743052111607823",
                "referenceFinalOutputSymbol": "TZ",
            },
            "txHash": "0x" + "b" * 64 if broadcast else None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    assert calls == [
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": True, "symbols": ["TZ", "TA", "TB", "TZ"]},
    ]
    auto_execute = (done.result or {}).get("auto_execute", {})
    assert auto_execute.get("autoExecuted") == 1
    refreshed_route = (done.result or {}).get("routes", [])[0]
    evidence = refreshed_route.get("live_onchain_evidence") or {}
    assert evidence.get("hops") == []
    assert evidence.get("blockNumber") is None
    assert evidence.get("costModel", {}).get("netProfitUsd") is None
    assert evidence.get("costModel", {}).get("netProfitAmount") == pytest.approx(0.002743052111607823)


def test_fixed_refresh_deep_mode_broadcasts_when_net_profit_is_positive_even_if_profit_field_is_missing(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []
    scanner = _scanner(tmp_path, enabled=True)
    payload = _quote_payload_for_robinhood_testnet()
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.5)

    def fake_run_official(**kwargs):
        symbols = kwargs.get("route_symbols") or []
        broadcast = bool(kwargs.get("broadcast"))
        calls.append({"broadcast": broadcast, "symbols": symbols})
        return {
            "ok": True,
            "quoteStatus": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": True,
            "profitable": False,
            "profit": None,
            "bestAmountConfirmed": True,
            "routeSymbols": symbols,
            "routeAddresses": kwargs.get("route_addresses") or [],
            "amount": kwargs.get("amount"),
            "bestAmount": kwargs.get("amount"),
            "steps": [],
            "analysis": {"referenceNetProfitAmount": 0.5, "referenceNetReturnBps": 33.3},
            "txHash": "0xtx" if broadcast else None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    assert calls == [
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]},
        {"broadcast": True, "symbols": ["TZ", "TA", "TB", "TZ"]},
    ]
    auto_execute = (done.result or {}).get("auto_execute", {})
    assert auto_execute.get("autoExecuted") == 1
    assert (done.result or {}).get("routes", [])[0].get("auto_execute", {}).get("executed") is True


def test_fixed_refresh_deep_mode_uses_system_initial_probe_when_route_has_no_amount_fields(tmp_path, monkeypatch):
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    calls = []
    scanner = _scanner(tmp_path, enabled=True)
    payload = {
        "summary": {
            "routeCount": 1,
            "completeStaticQuoteCount": 1,
            "executableCandidateCount": 0,
            "profitableRouteCount": 1,
            "liveQuoterRequiredCount": 1,
            "bestNetProfitUsd": 0.5,
        },
        "routes": [
            {
                "symbolPair": "WETH -> 0XC9F9C8 -> ETH -> WETH",
                "routeSymbols": ["WETH", "0XC9F9C8", "ETH", "WETH"],
                "routeAddresses": [
                    "0x" + "1" * 40,
                    "0x" + "2" * 40,
                    "0x" + "3" * 40,
                    "0x" + "1" * 40,
                ],
                "quoteStatus": "requires_live_quoter",
                "quoteInputs": {
                    "candidatePools": [
                        {
                            "pool": "0x" + "a" * 40,
                            "token0": "0x" + "1" * 40,
                            "token0Symbol": "WETH",
                            "token0Decimals": 18,
                            "token1": "0x" + "2" * 40,
                            "token1Symbol": "0XC9F9C8",
                            "token1Decimals": 18,
                        }
                    ]
                },
            }
        ],
    }
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "_routes_payload", lambda **kwargs: payload)

    def fake_run_official(**kwargs):
        symbols = kwargs.get("route_symbols") or []
        broadcast = bool(kwargs.get("broadcast"))
        calls.append({"amount": kwargs.get("amount"), "broadcast": broadcast, "symbols": symbols})
        return {
            "ok": True,
            "quoteStatus": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": True,
            "profitable": True,
            "profit": 0.5,
            "bestAmountConfirmed": True,
            "routeSymbols": symbols,
            "routeAddresses": kwargs.get("route_addresses") or [],
            "amount": kwargs.get("amount"),
            "bestAmount": kwargs.get("amount"),
            "steps": [],
            "analysis": {"referenceNetProfitAmount": 0.5, "referenceNetReturnBps": 33.3},
            "txHash": "0xtx" if broadcast else None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="WETH",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    assert calls == [
        {"amount": 1.0, "broadcast": False, "symbols": ["WETH", "0XC9F9C8", "ETH", "WETH"]},
        {"amount": 2.0, "broadcast": False, "symbols": ["WETH", "0XC9F9C8", "ETH", "WETH"]},
        {"amount": 0.75, "broadcast": False, "symbols": ["WETH", "0XC9F9C8", "ETH", "WETH"]},
        {"amount": 1.5, "broadcast": False, "symbols": ["WETH", "0XC9F9C8", "ETH", "WETH"]},
        {"amount": 1.0, "broadcast": True, "symbols": ["WETH", "0XC9F9C8", "ETH", "WETH"]},
    ]
    assert (done.result or {}).get("recording", {}).get("source") == "closed_loop_deep_review"
    assert (done.result or {}).get("recording", {}).get("review_count") == 1
    status = scanner.status(chain_id=46630)
    assert status["closed_loop_scan_count"] == 1
    assert status["closed_loop_review_count"] == 1
    assert status["auto_broadcast_count"] >= 1


def test_fixed_refresh_deep_mode_records_each_route_when_parallel(tmp_path, monkeypatch):
    import threading

    from runtime.dex_opportunity_scanner import DexOpportunityScanner
    from runtime import dex_opportunity_scanner as scanner_module
    from market.binance_market import dex_route_test

    monkeypatch.setenv("DEX_CLOSED_LOOP_WORKERS", "2")
    calls = []
    recorded_batches = []
    calls_lock = threading.Lock()

    def fake_record_attempts(attempts):
        recorded_batches.append(list(attempts))
        return {"recorded": len(attempts), "source": "closed_loop_deep_review"}

    payload = {
        "summary": {
            "routeCount": 2,
            "completeStaticQuoteCount": 2,
            "executableCandidateCount": 0,
            "profitableRouteCount": 2,
            "liveQuoterRequiredCount": 2,
            "bestNetProfitUsd": 0.5,
        },
        "routes": [
            {
                "routeSymbols": ["TZ", "TA", "TB", "TZ"],
                "routeAddresses": [
                    "0x" + "1" * 40,
                    "0x" + "2" * 40,
                    "0x" + "3" * 40,
                    "0x" + "1" * 40,
                ],
                "quoteStatus": "requires_live_quoter",
            },
            {
                "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                "routeAddresses": [
                    "0x" + "4" * 40,
                    "0x" + "3" * 40,
                    "0x" + "2" * 40,
                    "0x" + "4" * 40,
                ],
                "quoteStatus": "requires_live_quoter",
            },
        ],
    }
    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: payload,
        onchain_evidence_provider=_profitable_live_evidence,
        trigger_context_provider=lambda **kwargs: {},
        auto_execute_enabled=True,
        record_attempts=fake_record_attempts,
        record_opportunities=True,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        periodic_scan_enabled=False,
    )
    monkeypatch.setattr(scanner_module, "dex_quote_routes_payload", lambda **kwargs: payload)
    monkeypatch.setattr(dex_route_test, "recommended_route_test_amount", lambda **kwargs: 1.5)

    def fake_run_official(**kwargs):
        symbols = list(kwargs.get("route_symbols") or [])
        addresses = list(kwargs.get("route_addresses") or [])
        broadcast = bool(kwargs.get("broadcast"))
        with calls_lock:
            calls.append((symbols, broadcast))
        return {
            "ok": True,
            "quoteStatus": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "readyForTransactionPreflight": True,
            "profitable": True,
            "profit": 0.5,
            "bestAmountConfirmed": True,
            "routeSymbols": symbols,
            "routeAddresses": addresses,
            "amount": kwargs.get("amount"),
            "bestAmount": kwargs.get("amount"),
            "steps": [],
            "analysis": {"referenceNetProfitAmount": 0.5, "referenceNetReturnBps": 33.3},
            "txHash": "0xtx" if broadcast else None,
            "broadcast": broadcast,
        }

    monkeypatch.setattr(dex_route_test, "run_official_v4_arb_route_test", fake_run_official)
    task = scanner.closed_loop_review_queue.submit_group(
        group_key="TZ",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="deep",
    )
    done = scanner.closed_loop_review_pipeline.run_once()

    assert done is not None
    assert done.state == "done"
    assert len(recorded_batches) == 2
    assert all(len(batch) == 1 for batch in recorded_batches)
    assert len(calls) == 10
    assert len([call for call in calls if call[1]]) == 2
    status = scanner.status(chain_id=46630)
    assert status["closed_loop_scan_count"] == 1
    assert status["closed_loop_review_count"] == 2
    assert status["recorded_attempt_count"] == 2


def test_closed_loop_groups_use_deep_mode_when_auto_execute_enabled(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: _quote_payload_for_robinhood_testnet(),
        onchain_evidence_provider=lambda route, **kwargs: _profitable_live_evidence(route),
        trigger_context_provider=lambda **kwargs: {},
        auto_execute_enabled=True,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
        periodic_scan_enabled=False,
    )
    payload = _quote_payload_for_robinhood_testnet()
    scanner._enqueue_closed_loop_groups_from_payload(payload, chain_id=46630, protocols=["uniswap_v4"])
    rows = scanner.closed_loop_review_queue.snapshot()
    assert rows and rows[0]["mode"] == "deep"


def test_session_stats_are_reset_explicitly(tmp_path):
    scanner = _scanner(tmp_path, enabled=True)
    scanner._record_session_executed({"ok": True, "txHash": "0xabc", "profit": 1.25}, auto=True, chain_id=46630)
    status = scanner.status(chain_id=46630)
    session = status["session_stats"]
    assert session["executed_count"] == 1
    assert session["auto_executed_count"] == 1
    assert session["positive_profit_count"] == 1
    assert session["positive_profit_amount"] == 1.25

    scanner.reset_dex_opportunity_stats()
    status = scanner.status(chain_id=46630)
    session = status["session_stats"]
    assert session["executed_count"] == 0
    assert session["auto_executed_count"] == 0
    assert session["positive_profit_count"] == 0
    assert session["positive_profit_amount"] == 0


def test_chain_specific_status_and_reset_keep_global_and_other_chain(tmp_path):
    scanner = _scanner(tmp_path)
    scanner._record_session_executed({"ok": True, "txHash": "0xa", "profit": 1.0}, auto=True, chain_id=46630)
    scanner._record_session_executed({"ok": True, "txHash": "0xb", "profit": 2.0}, auto=True, chain_id=4663)

    chain_a = scanner.status(chain_id=46630)
    chain_b = scanner.status(chain_id=4663)
    global_stats = scanner.status()

    assert chain_a["session_stats"]["executed_count"] == 1
    assert chain_b["session_stats"]["executed_count"] == 1
    assert global_stats["session_stats"]["executed_count"] == 2

    scanner.reset_dex_opportunity_stats(chain_id=46630)

    chain_a = scanner.status(chain_id=46630)
    chain_b = scanner.status(chain_id=4663)
    global_stats = scanner.status()
    assert chain_a["session_stats"]["executed_count"] == 0
    assert chain_b["session_stats"]["executed_count"] == 1
    assert global_stats["session_stats"]["executed_count"] == 2


def test_scan_count_is_isolated_per_chain(tmp_path):
    scanner = _scanner(tmp_path)
    scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])
    scanner.scan_now(chain_id=4663, protocols=["uniswap_v4"])

    assert scanner.status(chain_id=46630)["scan_count"] == 1
    assert scanner.status(chain_id=4663)["scan_count"] == 1
    assert scanner.status()["scan_count"] == 2


def test_status_unknown_chain_does_not_show_global_totals(tmp_path):
    scanner = _scanner(tmp_path)
    scanner._record_session_executed({"ok": True, "txHash": "0xchain-a", "profit": 1.0}, auto=True, chain_id=46630)

    unknown_chain = scanner.status(chain_id=4663)
    assert unknown_chain["scan_count"] == 0
    assert unknown_chain["session_stats"]["executed_count"] == 0
    assert scanner.status()["session_stats"]["executed_count"] == 1


def test_chain_stats_are_persisted_then_restored(tmp_path):
    scanner = _scanner(tmp_path)
    scanner._record_session_executed({"ok": True, "txHash": "0xc", "profit": 3.0}, auto=True, chain_id=46630)
    scanner._write_cache()

    reloaded = _scanner(tmp_path)
    assert reloaded.status(chain_id=46630)["session_stats"]["executed_count"] == 1
    assert reloaded.status(chain_id=46630)["session_stats"]["positive_profit_amount"] == 3.0


def test_scan_exception_records_structured_error_stack(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    def broken_quote_payload_provider(*, chain_id=None, protocols=None):
        raise RuntimeError("broken quote payload")

    scanner = DexOpportunityScanner(
        quote_payload_provider=broken_quote_payload_provider,
        onchain_evidence_provider=lambda route, **kwargs: {},
        trigger_context_provider=lambda **kwargs: {"source": "test", "triggerBlockNumber": 123456},
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
        max_onchain_evidence_routes=1,
    )
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])
    snapshot = result["snapshot"]
    assert snapshot["error"] == "broken quote payload"
    assert snapshot["errorStack"][0]["module"] == "DEX"
    assert snapshot["errorStack"][0]["code"] == 3099
    assert snapshot["errorStack"][0]["flow"] == "opportunity_scan"
    assert snapshot["errorStack"][0]["nodeId"] == "46630|protocols:uniswap_v4"
    assert result["scanner"]["last_error_stack"][0]["code"] == 3099


def test_build_dex_opportunity_attempts_reuses_existing_live_verified_evidence(tmp_path):
    from runtime import dex_opportunity_scanner as scanner_module

    payload = _quote_payload_for_robinhood_testnet()
    route = payload["routes"][0]
    route["live_onchain_evidence"] = _profitable_live_evidence(route)

    def raise_evidence_provider(route, **kwargs):
        raise AssertionError("should reuse recent manual evidence instead of on-chain provider")

    attempts = scanner_module.build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at=datetime.now(timezone.utc).isoformat(),
        onchain_evidence_provider=raise_evidence_provider,
        max_onchain_evidence_routes=1,
    )

    assert attempts
    assert attempts[0]["quote"]["live_onchain_evidence"]["status"] == "live_verified"
    assert attempts[0]["quote"]["live_onchain_evidence"]["reusedRecentAttemptEvidence"] is True


def test_merge_recent_manual_review_excludes_submitted_and_keeps_viable_evidence(tmp_path, monkeypatch):
    from runtime import dex_opportunity_helpers as helpers

    payload = _quote_payload_for_robinhood_testnet()
    route = payload["routes"][0]
    submitted_evidence = _profitable_live_evidence(route)
    viable_evidence = deepcopy(_profitable_live_evidence(route))
    viable_evidence["bestAmountConfirmed"] = True
    submitted_attempt = {
        "chain_id": 46630,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "route_path": route["routeSymbols"],
        "quote": {"tx_hash": "0xdone", "live_onchain_evidence": submitted_evidence},
    }
    viable_attempt = {
        "chain_id": 46630,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "route_path": route["routeSymbols"],
        "quote": {"live_onchain_evidence": viable_evidence},
    }
    monkeypatch.setattr(
        helpers,
        "load_recent_cow_execution_attempts",
        lambda *args, **kwargs: [submitted_attempt, viable_attempt],
    )
    monkeypatch.setattr(helpers, "database_url", lambda: str(tmp_path / "local_store.sqlite3"))

    merged = helpers.merge_recent_manual_review_evidence_into_routes([route], chain_id=46630)
    evidence = merged[0].get("live_onchain_evidence") or (merged[0].get("quoteAnalysis") or {}).get("live_onchain_evidence")

    assert evidence.get("status") == "live_verified"
    assert evidence.get("reusedRecentAttemptEvidence") is None


def test_live_evidence_without_hops_stays_live_verified_when_best_amount_confirmed():
    from runtime import dex_opportunity_helpers as helpers

    payload = _quote_payload_for_robinhood_testnet()
    route = payload["routes"][0]
    evidence = dict(_profitable_live_evidence(route))
    evidence["hops"] = []
    evidence["bestAmountConfirmed"] = True
    evidence["costModel"] = {
        **_profitable_live_evidence(route)["costModel"],
        "bestAmountConfirmed": True,
    }

    merged = helpers._merge_onchain_evidence_into_route(route, evidence)
    analysis_evidence = merged.get("quoteAnalysis", {}).get("live_onchain_evidence", {})

    assert analysis_evidence.get("status") == "live_verified"
    assert merged["quoteAnalysis"]["executableCandidate"] is True
    assert merged["quoteAnalysis"]["chainSubmissionCandidate"] is True


def test_build_attempts_reviews_all_robinhood_routes_despite_testnet_cap(tmp_path):
    from runtime import dex_opportunity_scanner as scanner_module

    payload = _quote_payload_for_robinhood_testnet()
    route_one = payload["routes"][0]
    route_two = dict(route_one)
    route_two["symbolPair"] = "ETH -> 0X102154 -> 0X71178B -> ETH"
    route_two["routeSymbols"] = ["ETH", "0X102154", "0X71178B", "ETH"]
    route_two["routeAddresses"] = [
        "0x0000000000000000000000000000000000000000",
        "0x102154e70d8485ff466bab229be90a763bf33264",
        "0x71178bac73cbeb415514eb542a8995b82669778d",
        "0x0000000000000000000000000000000000000000",
    ]
    route_two["live_onchain_evidence"] = _profitable_live_evidence(route_two)
    payload["routes"] = [route_one, route_two]
    reviewed_paths = []

    def provider(route, **kwargs):
        reviewed_paths.append(route.get("routeSymbols"))
        return _profitable_live_evidence(route)

    attempts = scanner_module.build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at=datetime.now(timezone.utc).isoformat(),
        onchain_evidence_provider=provider,
        testnet_max_recordable_routes=1,
    )

    assert len(attempts) == 2
    assert {tuple(a["route_path"]) for a in attempts} == {
        tuple(route_one["routeSymbols"]),
        tuple(route_two["routeSymbols"]),
    }
    assert reviewed_paths == [route_one["routeSymbols"]]
    ready_attempt = next(a for a in attempts if a["route_path"] == route_two["routeSymbols"])
    assert ready_attempt["quote"]["live_onchain_evidence"]["status"] == "live_verified"


def test_auto_execute_enabled_skips_old_scan_evidence_probe(tmp_path):
    enabled_skips = []
    disabled_skips = []

    def enabled_evidence(route, **kwargs):
        enabled_skips.append(kwargs.get("skip_execution_size_probe"))
        return _profitable_live_evidence(route)

    def disabled_evidence(route, **kwargs):
        disabled_skips.append(kwargs.get("skip_execution_size_probe"))
        return _profitable_live_evidence(route)

    enabled_scanner = _scanner(tmp_path, evidence=enabled_evidence, enabled=True, background_refresh=False)
    enabled_scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    disabled_scanner = _scanner(tmp_path, evidence=disabled_evidence, enabled=False)
    disabled_scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert enabled_skips == []
    assert disabled_skips == [True]


def test_failed_submission_classifier_includes_quote_timeout_without_tx_hash():
    from web.data_routes.dex_surface_routes_ops import (
        _failed_submission_rows,
        _merge_failed_submission_rows_into_payload,
    )

    timeout_row = {
        "pair": "TZ -> TB -> TA -> TZ",
        "created_at": "2026-10-01T08:00:00+00:00",
        "state": "quote_required",
        "quote": {
            "live_onchain_evidence": {
                "status": "rpc_verification_timeout",
                "costModel": {},
            }
        },
    }
    submitted_row = {
        "pair": "ETH -> 0X102154 -> 0X71178B -> ETH",
        "created_at": "2026-10-01T08:01:00+00:00",
        "state": "submitted_success",
        "tx_hash": "0x" + "a" * 64,
        "quote": {"tx_hash": "0x" + "a" * 64},
    }

    failed_rows = _failed_submission_rows([timeout_row, submitted_row])

    assert len(failed_rows) == 1
    assert failed_rows[0]["pair"] == "TZ -> TB -> TA -> TZ"

    payload = {
        "attempts": [submitted_row],
        "summary": {"failedCount": 0, "attemptCount": 1, "successCount": 1},
    }
    merged = _merge_failed_submission_rows_into_payload(payload, failed_rows)

    assert merged["summary"]["failedCount"] == 1
    assert merged["summary"]["attemptCount"] == 2
    assert len(merged["attempts"]) == 2
    assert any(row.get("pair") == "TZ -> TB -> TA -> TZ" for row in merged["attempts"])
    assert any(row.get("tx_hash") for row in merged["attempts"])


def test_failed_submission_merge_keeps_multiple_rows_for_same_pair_with_different_timestamps():
    from web.data_routes.dex_surface_routes_ops import (
        _failed_submission_rows,
        _merge_failed_submission_rows_into_payload,
    )

    first_timeout = {
        "pair": "TZ -> TB -> TA -> TZ",
        "created_at": "2026-10-02T06:50:00+00:00",
        "state": "quote_required",
        "quote": {
            "live_onchain_evidence": {
                "status": "rpc_verification_timeout",
                "costModel": {},
            }
        },
    }
    second_timeout = {
        "pair": "TZ -> TB -> TA -> TZ",
        "created_at": "2026-10-02T06:55:00+00:00",
        "state": "quote_required",
        "quote": {
            "live_onchain_evidence": {
                "status": "rpc_verification_timeout",
                "costModel": {},
            }
        },
    }

    failed_rows = _failed_submission_rows([first_timeout, second_timeout])
    merged = _merge_failed_submission_rows_into_payload({"attempts": [], "summary": {}}, failed_rows)

    assert len(failed_rows) == 2
    assert len(merged["attempts"]) == 2
    assert merged["summary"]["failedCount"] == 2
    assert merged["summary"]["attemptCount"] == 2


def test_failed_submission_classifier_excludes_business_reverts_from_system_error_count():
    from web.data_routes.dex_surface_routes_ops import (
        _business_blocked_submission_rows,
        _failed_submission_rows,
        _is_system_failure_row,
        _system_failed_submission_rows,
    )

    rows = [
        {
            "pair": "ETH -> 0X102154 -> 0X1FBE1A -> ETH",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "status": "live_quote_failed",
                    "executionTrust": "live_quote_failed",
                    "blockers": ["ArbitrageNotProfitable(received, required) received=0.015407157132245466 required=0.015625000000000001"],
                }
            },
        },
        {
            "pair": "ETH -> 0X102154 -> 0X71178B -> ETH",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "status": "live_quote_failed",
                    "executionTrust": "live_quote_failed",
                    "blockers": ["revert selector 0x486aa307"],
                }
            },
        },
        {
            "pair": "TZ -> TB -> TA -> TZ",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "status": "rpc_verification_timeout",
                    "executionTrust": "rpc_verification_timeout",
                }
            },
        },
    ]

    failed = _failed_submission_rows(rows)
    system = _system_failed_submission_rows(failed)
    business = _business_blocked_submission_rows(failed)

    assert len(failed) == 3
    assert len(system) == 1
    assert len(business) == 2
    assert all(not _is_system_failure_row(row) for row in business)
    assert system[0]["quote"]["live_onchain_evidence"]["status"] == "rpc_verification_timeout"


def test_summary_always_exports_auto_executable_closed_loop_count():
    from runtime.dex_opportunity_helpers import _refresh_payload_summary

    summary = _refresh_payload_summary({"summary": {}, "routes": []})

    assert summary["autoExecutableClosedLoopCount"] == 0


def test_submission_review_surfaces_auto_execute_tx_hash():
    from market.binance_market.dex_surfaces import dex_submission_review_payload

    auto_row = {
        "network": "robinhood-testnet",
        "created_at": "2026-10-01T10:33:33+00:00",
        "pair": "ETH -> 0X102154 -> 0X71178B -> ETH",
        "state": "ready_for_transaction_development",
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "bestAmountConfirmed": True,
                "readyForTransactionPreflight": True,
                "profitable": True,
                "costModel": {"selectedBorrowInputAmount": 0.015625},
            }
        },
        "auto_execute": {
            "executed": True,
            "result": {
                "ok": True,
                "txHash": "0x" + "b" * 64,
            },
        },
    }

    payload = dex_submission_review_payload(
        attempts=[auto_row],
        groups={},
        source="jsonl",
    )

    assert payload["summary"]["successCount"] == 1
    assert payload["summary"]["failedCount"] == 0
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["tx_hash"] == "0x" + "b" * 64
    assert payload["attempts"][0]["review_category"] == "submitted_success"
    assert payload["attempts"][0]["state"] == "submitted_success"


def test_success_submission_rows_require_chain_tx_hash():
    from web.data_routes.dex_surface_routes_ops import (
        _attempt_tx_hash,
        _success_submission_rows,
    )

    rows = [
        {
            "pair": "TZ -> TB -> TA -> TZ",
            "state": "execution_success",
            "tx_hash": "0x" + "a" * 64,
        },
        {
            "pair": "ETH -> AMD -> AMZN -> ETH",
            "state": "execution_success",
            "auto_execute": {"result": {"ok": True}},
        },
        {
            "pair": "BTC -> AMD -> AMZN -> BTC",
            "state": "submitted_success",
            "quote": {"tx_hash": "0x" + "b" * 64},
        },
    ]

    success_rows = _success_submission_rows(rows)

    assert [row["pair"] for row in success_rows] == [
        "TZ -> TB -> TA -> TZ",
        "BTC -> AMD -> AMZN -> BTC",
    ]
    assert all(_attempt_tx_hash(row) for row in success_rows)


def test_pending_broadcast_stays_on_success_page_but_is_not_confirmed():
    from web.data_routes.dex_surface_routes_ops import _success_submission_rows

    row = {
        "pair": "AAA -> BBB -> CCC -> AAA",
        "state": "submitted_success",
        "tx_hash": "0x" + "c" * 64,
        "auto_execute": {
            "submitted": True,
            "confirmed": False,
            "confirmationStatus": "pending",
            "reason": "broadcast_pending_confirmation",
            "result": {"receiptStatus": "receipt_pending"},
        },
    }

    assert _success_submission_rows([row]) == [row]


def test_reverted_broadcast_is_classified_as_execution_failed():
    from web.data_routes.dex_surface_routes_ops import _submission_failed_rows

    row = {
        "pair": "AAA -> BBB -> CCC -> AAA",
        "state": "execution_failed",
        "tx_hash": "0x" + "d" * 64,
        "auto_execute": {
            "submitted": True,
            "confirmed": False,
            "confirmationStatus": "failed",
            "reason": "broadcast_reverted",
            "result": {
                "receiptStatus": "0x0",
                "error": "chain receipt status 0x0: transaction mined but execution reverted",
            },
        },
    }

    assert _submission_failed_rows([row]) == [row]


def test_storage_category_distinguishes_pending_and_reverted_broadcasts():
    from core.db.storage_cow_policy import cow_execution_attempt_category

    pending = {
        "state": "submitted_success",
        "tx_hash": "0x" + "e" * 64,
        "auto_execute": {
            "executed": True,
            "submitted": True,
            "confirmed": False,
            "confirmationStatus": "pending",
        },
    }
    reverted = {
        "state": "execution_failed",
        "tx_hash": "0x" + "f" * 64,
        "auto_execute": {
            "executed": True,
            "submitted": True,
            "confirmed": False,
            "confirmationStatus": "failed",
            "result": {"receiptStatus": "0x0"},
        },
    }

    assert cow_execution_attempt_category(pending) == "execution_success"
    assert cow_execution_attempt_category(reverted) == "execution_failed"


def test_storage_category_counts_only_attempted_broadcast_failures():
    from core.db.storage_cow_policy import cow_execution_attempt_category

    preflight = {
        "state": "execution_failed",
        "tx_hash": "",
        "precheck": {"requires_live_quoter": True},
        "quote": {
            "live_onchain_evidence": {
                "status": "static_reference_pending_live_review",
                "readyForTransactionPreflight": False,
            }
        },
        "auto_execute": {
            "attempted": False,
            "broadcast": False,
            "reason": "static_reference_pending_live_review",
        },
    }
    attempted = {
        "state": "execution_failed",
        "tx_hash": "",
        "precheck": {"requires_live_quoter": True},
        "quote": {
            "live_onchain_evidence": {
                "status": "static_reference_pending_live_review",
                "readyForTransactionPreflight": False,
            }
        },
        "auto_execute": {
            "attempted": True,
            "broadcast": True,
            "reason": "broadcast_failed",
            "result": {
                "attempted": True,
                "failed": True,
                "status": "broadcast_failed",
                "submitted": False,
                "txHash": None,
            },
        },
    }

    assert cow_execution_attempt_category(preflight) == "not_executable"
    assert cow_execution_attempt_category(attempted) == "execution_failed"


def test_failure_reason_text_surfaces_missing_broadcast_with_open_gate():
    from web.data_routes.dex_surface_routes_ops import _row_failure_reason_text

    row = {
        "state": "ready_for_transaction_development",
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "readyForTransactionPreflight": True,
                "profitable": True,
                "costModel": {"netProfitAmount": 0.0001},
            }
        },
        "auto_execute": {
            "skipped": True,
            "attempted": False,
            "broadcast": False,
            "broadcastResultMissing": True,
            "reason": "broadcast_result_missing",
            "broadcastGate": {
                "autoExecuteEnabled": True,
                "broadcastEnabled": True,
                "broadcastAllowed": True,
                "privateKeyConfigured": True,
                "reasons": [],
            },
        },
    }

    text = _row_failure_reason_text(row)

    assert "broadcast_result_missing" in text
    assert "broadcastGate open" in text


def test_failure_reason_text_surfaces_broadcast_failed_result_error():
    from web.data_routes.dex_surface_routes_ops import _row_failure_reason_text

    row = {
        "state": "execution_failed",
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "readyForTransactionPreflight": True,
                "profitable": True,
                "costModel": {"netProfitAmount": 0.0001},
            }
        },
        "auto_execute": {
            "attempted": True,
            "broadcast": True,
            "executed": False,
            "reason": "broadcast_failed",
            "result": {
                "ok": False,
                "error": "eth_sendRawTransaction RPC request failed",
            },
        },
    }

    text = _row_failure_reason_text(row)

    assert "broadcast_failed" in text
    assert "eth_sendRawTransaction RPC request failed" in text


def test_auto_execute_tests_do_not_leave_background_refresh_threads():
    import threading

    assert [
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("fixed-closed-loop-refresh")
    ] == []
