from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


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


def _scanner(tmp_path, *, evidence=None, callback=None, enabled=None, cooldown=600.0):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    def onchain_evidence_provider(route, **kwargs):
        if evidence is not None:
            return evidence(route, **kwargs)
        return _profitable_live_evidence(route)

    return DexOpportunityScanner(
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


def test_auto_execute_disabled_by_default_keeps_callback_unused(tmp_path):
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


def test_auto_execute_calls_callback_only_for_live_verified_profitable_route(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append({"route_symbols": route_symbols, "amount": amount, "broadcast": broadcast, "chain_id": chain_id})
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback, enabled=True)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == [
        {
            "route_symbols": ["TZ", "TA", "TB", "TZ"],
            "amount": 1.5,
            "broadcast": True,
            "chain_id": 46630,
        }
    ]
    recording = result["snapshot"]["recording"]
    assert result["snapshot"]["summary"]["auto_executable_closed_loop_count"] == 1
    assert result["snapshot"]["payload"]["summary"]["autoExecutableClosedLoopCount"] == 1
    assert recording["autoExecuteEnabled"] is True
    assert recording["autoExecuted"] == 1
    assert recording["autoExecuteFailed"] == 0
    assert scanner._auto_execute_count == 1
    assert scanner._last_auto_execute_at is not None


def test_auto_execute_skips_without_live_evidence(tmp_path):
    calls = []

    def callback(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "txHash": "0xtx"}

    def no_evidence(route, **kwargs):
        return {
            "status": "live_quote_failed",
            "trustedForExecution": False,
            "readyForTransactionPreflight": False,
            "blockNumber": 123,
            "hops": [],
            "costModel": {},
            "blockers": ["quote failed"],
        }

    scanner = _scanner(tmp_path, evidence=no_evidence, callback=callback, enabled=True)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == []
    recording = result["snapshot"]["recording"]
    assert recording["autoExecuteSkipped"] == 1
    assert recording["autoExecuted"] == 0
    snapshot_route = result["snapshot"]["payload"]["routes"][0]
    assert snapshot_route["quoteAnalysis"]["live_onchain_evidence"]["status"] == "live_quote_failed"


def test_auto_execute_respects_cooldown_keyed_to_route_amount_and_block(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append({"route_symbols": route_symbols, "amount": amount, "broadcast": broadcast, "chain_id": chain_id})
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback, enabled=True, cooldown=3600.0)

    first = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])
    second = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert len(calls) == 1
    assert first["snapshot"]["recording"]["autoExecuted"] == 1
    assert second["snapshot"]["recording"]["autoExecuted"] == 0
    assert second["snapshot"]["recording"]["autoExecuteSkipped"] == 1


def test_auto_execute_records_failure_and_attempt_markers(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append((route_symbols, amount))
        return {"ok": False, "error": "official_arb_broadcast_rpc_unavailable", "txHash": None}

    scanner = _scanner(tmp_path, callback=callback, enabled=True)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert len(calls) == 1
    recording = result["snapshot"]["recording"]
    assert recording["autoExecuted"] == 0
    assert recording["autoExecuteFailed"] == 1
    assert recording["autoExecuteErrors"] == ["official_arb_broadcast_rpc_unavailable"]
    snapshot_route = result["snapshot"]["payload"]["routes"][0]
    assert snapshot_route["quoteAnalysis"]["live_onchain_evidence"]["status"] == "live_verified"


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
    assert scanner._auto_execute_count == 1


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


def test_auto_execute_enabled_forces_execution_size_probe(tmp_path):
    enabled_skips = []
    disabled_skips = []

    def enabled_evidence(route, **kwargs):
        enabled_skips.append(kwargs.get("skip_execution_size_probe"))
        return _profitable_live_evidence(route)

    def disabled_evidence(route, **kwargs):
        disabled_skips.append(kwargs.get("skip_execution_size_probe"))
        return _profitable_live_evidence(route)

    enabled_scanner = _scanner(tmp_path, evidence=enabled_evidence, enabled=True)
    enabled_scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    disabled_scanner = _scanner(tmp_path, evidence=disabled_evidence, enabled=False)
    disabled_scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert enabled_skips == [False]
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
