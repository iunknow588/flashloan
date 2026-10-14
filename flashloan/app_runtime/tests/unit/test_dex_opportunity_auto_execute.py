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
        calls.append({"broadcast": bool(kwargs.get("broadcast")), "symbols": symbols})
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
    assert calls == [{"broadcast": False, "symbols": ["TZ", "TA", "TB", "TZ"]}]
    auto_execute = (done.result or {}).get("auto_execute", {})
    assert auto_execute.get("autoExecuted") == 0
    assert auto_execute.get("autoExecuteSkipped") == 1
    assert (done.result or {}).get("routes", [])[0].get("auto_execute", {}).get("skipped") is True


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
    assert len(calls) == 4
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
        "load_recent_cow_execution_attempts_jsonl",
        lambda *args, **kwargs: [submitted_attempt, viable_attempt],
    )
    monkeypatch.setattr(helpers, "cow_execution_attempt_log_path", lambda **kwargs: tmp_path / "attempts.jsonl")

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


def test_auto_execute_tests_do_not_leave_background_refresh_threads():
    import threading

    assert [
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("fixed-closed-loop-refresh")
    ] == []
