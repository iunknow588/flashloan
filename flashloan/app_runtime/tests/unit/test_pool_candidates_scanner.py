import pytest

from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market.candidate_pool_cache import store as candidate_pool_cache_store
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from dex.intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation
from market.strategy.arbitrage import ArbitrageConfig


def _write_json(path, payload):
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_dex_surface_cache():
    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    try:
        yield
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)


def _pool(pool_id: str, token0_symbol: str, token1_symbol: str, token0: str, token1: str, *, price_native: str, token0_amount: float, token1_amount: float, tvl_usd: float) -> dict:
    return {
        "pool": pool_id,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "3000",
        "token0": token0,
        "token1": token1,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "balances": {"token0": token0_amount, "token1": token1_amount, "tvlUsd": tvl_usd},
        "market": {"priceNative": price_native, "priceUsd": "1"},
        "depthBand": {"level": "deep", "label": "deep"},
    }


def test_dex_opportunity_scanner_keeps_latest_quote_snapshot(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    calls = []

    def quote_payload_provider(*, chain_id=None, protocols=None):
        calls.append({"chain_id": chain_id, "protocols": protocols})
        return {
            "summary": {
                "routeCount": 2,
                "completeStaticQuoteCount": 1,
                "executableCandidateCount": 1,
                "profitableRouteCount": 1,
                "liveQuoterRequiredCount": 0,
                "uniswapxQuoteableRouteCount": 1,
                "bestNetProfitUsd": 7.5,
            },
            "routes": [{"symbolPair": "USDC -> WBNB -> USDC"}],
        }

    scanner = DexOpportunityScanner(
        interval_seconds=3,
        quote_payload_provider=quote_payload_provider,
        trigger_context_provider=lambda **kwargs: {
            "source": "test_trigger",
            "observedAt": kwargs.get("observed_at"),
            "network": "bnb",
            "chainId": 56,
            "available": False,
            "triggerBlockNumber": None,
        },
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
    )

    payload = scanner.scan_now(chain_id=56, protocols=[])

    assert calls == [{"chain_id": 56, "protocols": []}]
    assert payload["ok"] is True
    assert payload["scanner"]["scan_count"] == 1
    assert payload["snapshot"]["chain_id"] == 56
    assert payload["snapshot"]["summary"]["route_count"] == 2
    assert payload["snapshot"]["summary"]["executable_candidate_count"] == 1
    assert payload["snapshot"]["summary"]["best_net_profit_usd"] == 7.5
    assert payload["snapshot"]["payload"]["routes"][0]["symbolPair"] == "USDC -> WBNB -> USDC"
    assert (tmp_path / "dex_opportunity_snapshot.json").exists()


def test_dex_opportunity_scanner_reviews_onchain_evidence_when_recording_is_disabled(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    evidence_calls = []
    record_calls = []

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood-testnet", "chainId": chain_id},
            "routes": [{
                "symbolPair": "ETH -> TOKEN -> ETH",
                "routeSymbols": ["ETH", "TOKEN", "ETH"],
                "quoteStatus": "requires_live_quoter",
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": True,
                    "referenceNetProfitUsd": 10.0,
                    "steps": [{"hop": 1, "fromSymbol": "ETH", "toSymbol": "TOKEN"}],
                },
            }],
        }

    def onchain_evidence_provider(route, **kwargs):
        evidence_calls.append(route["symbolPair"])
        return {
            "status": "live_quote_failed",
            "executionTrust": "static_reference_only",
            "trustedForExecution": False,
            "blockNumber": 12345,
            "hops": [],
            "costModel": {},
            "blockers": ["test quote failure"],
        }

    def record_attempts(attempts):
        record_calls.append(attempts)
        return {"recorded": len(attempts), "source": "test", "error": None}

    scanner = DexOpportunityScanner(
        quote_payload_provider=quote_payload_provider,
        onchain_evidence_provider=onchain_evidence_provider,
        record_attempts=record_attempts,
        trigger_context_provider=lambda **kwargs: {"triggerBlockNumber": 12345},
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
        max_onchain_evidence_routes=1,
    )

    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    route = result["snapshot"]["payload"]["routes"][0]
    assert evidence_calls == ["ETH -> TOKEN -> ETH"]
    assert record_calls == []
    assert result["snapshot"]["recording"] == {
        "candidate_count": 1,
        "recorded": 0,
        "source": "disabled",
        "error": None,
    }
    assert route["live_onchain_evidence"]["status"] == "live_quote_failed"
    assert route["quoteVerification"]["blockNumber"] == 12345


def test_dex_opportunity_scanner_reconciles_live_summary_after_review(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood-testnet", "chainId": chain_id},
            "summary": {
                "routeCount": 2,
                "liveQuoterRequiredCount": 2,
                "sdkAdapterRequiredCount": 2,
            },
            "routes": [
                {
                    "symbolPair": "A -> B -> A",
                    "routeSymbols": ["A", "B", "A"],
                    "quoteStatus": "requires_live_quoter",
                    "quoteAnalysis": {"requiresLiveQuoter": True, "adapterRequired": True, "complete": True},
                },
                {
                    "symbolPair": "A -> C -> A",
                    "routeSymbols": ["A", "C", "A"],
                    "quoteStatus": "requires_live_quoter",
                    "quoteAnalysis": {"requiresLiveQuoter": True, "adapterRequired": True, "complete": True},
                },
            ],
        }

    def onchain_evidence_provider(route, **kwargs):
        if route["symbolPair"].startswith("A -> B"):
            return {
                "status": "live_verified",
                "livePriceVerified": True,
                "readyForTransactionPreflight": False,
                "costModel": {"netProfitUsd": -1.25},
                "hops": [],
                "blockNumber": 123,
                "blockers": ["negative live net profit"],
            }
        return {
            "status": "live_quote_failed",
            "readyForTransactionPreflight": False,
            "costModel": {},
            "hops": [],
            "blockNumber": 124,
            "blockers": ["quote failed"],
        }

    scanner = DexOpportunityScanner(
        quote_payload_provider=quote_payload_provider,
        onchain_evidence_provider=onchain_evidence_provider,
        trigger_context_provider=lambda **kwargs: {"triggerBlockNumber": 123},
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
        max_onchain_evidence_routes=1,
        testnet_max_onchain_evidence_routes=2,
    )

    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])
    summary = result["snapshot"]["payload"]["summary"]

    assert summary["onchainEvidenceReviewedRouteCount"] == 2
    assert summary["onchainEvidenceDeferredRouteCount"] == 0
    assert summary["onchainEvidenceFailedRouteCount"] == 1
    assert summary["liveQuoterRequiredCount"] == 0
    assert summary["liveVerifiedRouteCount"] == 1
    assert summary["bestLiveNetProfitUsd"] == -1.25
    assert result["scanner"]["testnet_max_onchain_evidence_routes"] == 2


def test_dex_opportunity_scanner_uses_registry_network_for_testnet(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    trigger_contexts = []

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood-testnet", "chainId": chain_id},
            "summary": {"routeCount": 0},
            "routes": [],
        }

    def trigger_context_provider(**kwargs):
        trigger_contexts.append(kwargs)
        return {"source": "test_trigger", "network": kwargs.get("network"), "chainId": kwargs.get("chain_id")}

    scanner = DexOpportunityScanner(
        interval_seconds=3,
        quote_payload_provider=quote_payload_provider,
        trigger_context_provider=trigger_context_provider,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
    )

    payload = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert payload["ok"] is True
    assert trigger_contexts[0]["network"] == "robinhood-testnet"
    assert payload["snapshot"]["trigger_context"]["network"] == "robinhood-testnet"


def test_dex_opportunity_scanner_records_discovered_opportunities(tmp_path):
    from core.db.storage_cow_execution import append_cow_execution_attempts_jsonl, load_recent_cow_execution_attempts_jsonl
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    log_path = tmp_path / "cow_execution_attempts.jsonl"
    recorded_batches = []
    seen_trigger_contexts = []

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood", "chainId": chain_id},
            "summary": {
                "routeCount": 1,
                "completeStaticQuoteCount": 1,
                "executableCandidateCount": 0,
                "profitableRouteCount": 1,
                "liveQuoterRequiredCount": 1,
                "bestObservedNetProfitUsd": 12.5,
            },
            "routes": [
                {
                    "symbolPair": "USDG -> WETH -> TEST -> WETH -> USDG",
                    "routeSymbols": ["USDG", "WETH", "TEST", "WETH", "USDG"],
                    "quoteStatus": "requires_live_quoter",
                    "overviewRoute": {"kind": "bridge_pair", "targetSymbol": "TEST"},
                    "quoteInputs": {
                        "candidatePools": [{"pool": "0x" + "9" * 40}],
                        "conditions": ["需要链上 Quoter 复核"],
                    },
                    "quoteAnalysis": {
                        "method": "static_amm_depth_probe",
                        "notionalUsd": 1000.0,
                        "inputSymbol": "USDG",
                        "outputSymbol": "USDG",
                        "finalOutputUsd": None,
                        "referenceFinalOutputUsd": 1012.5,
                        "grossProfitUsd": None,
                        "referenceGrossProfitUsd": 12.5,
                        "netProfitUsd": None,
                        "referenceNetProfitUsd": 12.0,
                        "referenceNetProfitBps": 120.0,
                        "profitable": False,
                        "complete": True,
                        "requiresLiveQuoter": True,
                        "executableCandidate": False,
                        "recommendation": {
                            "level": "requires_live_quote",
                            "action": "等待链上 Quoter 复核",
                            "reasons": ["静态观察存在正向空间"],
                        },
                        "steps": [{"hop": 1, "fromSymbol": "USDG", "toSymbol": "WETH"}],
                    },
                }
            ],
        }

    def record_attempts(attempts):
        recorded_batches.append(attempts)
        return {"recorded": append_cow_execution_attempts_jsonl(log_path, attempts), "source": "jsonl", "error": None}

    def onchain_evidence_provider(route, **kwargs):
        seen_trigger_contexts.append(kwargs.get("trigger_context"))
        trigger_context = kwargs.get("trigger_context") or {}
        return {
            "evidenceVersion": 1,
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood",
            "chainId": 4663,
            "status": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "trustedForExecution": False,
            "readyForTransactionPreflight": True,
            "blockNumber": 12345,
            "triggerContext": trigger_context,
            "triggerBlockNumber": trigger_context.get("triggerBlockNumber"),
            "quoteBlockSource": "trigger_block",
            "routeSymbols": route.get("routeSymbols"),
            "hops": [{"hop": 1, "status": "live_verified"}],
            "costModel": {
                "notionalUsd": 1000.0,
                "finalOutputUsd": 1012.5,
                "grossProfitUsd": 12.5,
                "flashLoanFeeUsd": 0.5,
                "gasReserveUsd": 1.0,
                "netProfitUsd": 11.0,
                "netProfitBps": 110.0,
            },
            "validation": {
                "allHopsQuoted": True,
                "routeClosed": True,
                "profitableAfterCosts": True,
                "finalOutputSymbol": "USDG",
            },
            "blockers": [],
            "transactionDevelopment": {"status": "ready_for_transaction_preflight_development", "executionEnabled": False},
        }

    scanner = DexOpportunityScanner(
        interval_seconds=3,
        record_cooldown_seconds=60,
        quote_payload_provider=quote_payload_provider,
        record_attempts=record_attempts,
        onchain_evidence_provider=onchain_evidence_provider,
        trigger_context_provider=lambda **kwargs: {
            "source": "test_trigger",
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood",
            "chainId": 4663,
            "available": True,
            "triggerBlockNumber": 12345,
            "blockNumber": 12345,
        },
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
    )

    first = scanner.scan_now(chain_id=4663, protocols=[])
    second = scanner.scan_now(chain_id=4663, protocols=[])
    rows = load_recent_cow_execution_attempts_jsonl(log_path, limit=10, networks=["robinhood"])

    assert first["snapshot"]["recording"]["candidate_count"] == 1
    assert first["snapshot"]["recording"]["recorded"] == 1
    assert second["snapshot"]["recording"]["candidate_count"] == 1
    assert second["snapshot"]["recording"]["attempted_record_count"] == 0
    assert len(recorded_batches[0]) == 1
    assert seen_trigger_contexts[0]["triggerBlockNumber"] == 12345
    assert rows[0]["network"] == "robinhood"
    assert rows[0]["pair"] == "USDG -> WETH -> TEST -> WETH -> USDG"
    assert rows[0]["execution_phase"] == "market_candidate"
    assert rows[0]["review_category"] == "not_executable"
    assert rows[0]["quote"]["candidate_basis"] == "dex_opportunity_scanner"
    assert rows[0]["quote"]["live_onchain_evidence"]["status"] == "live_verified"
    assert rows[0]["quote"]["readonly_viable"] is True
    assert rows[0]["quote"]["viable"] is False
    assert rows[0]["quote"]["final_amount"] is None
    assert rows[0]["quote"]["reference_final_amount"] == 1012.5
    assert rows[0]["quote"]["final_delta_amount"] is None
    assert rows[0]["quote"]["reference_final_delta_amount"] == 12.0
    assert rows[0]["quote"]["transaction_development"]["status"] == "ready_for_transaction_preflight_development"
    assert rows[0]["checks_passed"] is False
    assert rows[0]["priority_reason"] == "live_quote_verified_preflight_required"
    assert rows[0]["review_summary"]["onchain_evidence"]["block_number"] == 12345
    assert rows[0]["review_summary"]["onchain_evidence"]["trigger_block_number"] == 12345
    assert rows[0]["market_state"]["trigger_block_number"] == 12345
    assert rows[0]["review_summary"]["candidate_basis"] == "dex_opportunity_scanner"


def test_dex_opportunity_snapshot_does_not_start_scanner(monkeypatch):
    from runtime import dex_opportunity_scanner

    calls = {"start": 0, "watch": 0, "snapshot": 0}

    class FakeScanner:
        def start(self, *args, **kwargs):
            calls["start"] += 1

        def watch(self, *args, **kwargs):
            calls["watch"] += 1

        def snapshot(self, *args, **kwargs):
            calls["snapshot"] += 1
            return {"ok": True, "scanner": {"running": False}, "snapshot": {"chain_id": 46630}}

    monkeypatch.setattr(dex_opportunity_scanner, "get_dex_opportunity_scanner", lambda: FakeScanner())

    payload = dex_opportunity_scanner.dex_opportunity_snapshot(chain_id=46630, protocols=["uniswap_v4"])

    assert payload["ok"] is True
    assert calls == {"start": 0, "watch": 1, "snapshot": 1}


def test_ensure_dex_opportunity_scanner_running_populates_first_snapshot(monkeypatch):
    from runtime import dex_opportunity_scanner

    calls = {"start": 0, "watch": 0, "snapshot": 0, "quote_payload": 0, "trigger_context": 0}

    class FakeScanner:
        def start(self, *args, **kwargs):
            calls["start"] += 1

        def watch(self, *args, **kwargs):
            calls["watch"] += 1

        def snapshot(self, *args, **kwargs):
            calls["snapshot"] += 1
            return {"ok": True, "scanner": {"running": True}, "snapshot": {"payload": None}}

        def quote_payload_provider(self, *args, **kwargs):
            calls["quote_payload"] += 1
            return {"summary": {"routeCount": 1}, "routes": [{"symbolPair": "A -> B -> A"}]}

        def trigger_context_provider(self, *args, **kwargs):
            calls["trigger_context"] += 1
            return {"source": "test_trigger"}

        _lock = __import__("threading").Lock()
        _snapshots = {}

    monkeypatch.setattr(dex_opportunity_scanner, "get_dex_opportunity_scanner", lambda: FakeScanner())

    scanner = dex_opportunity_scanner.ensure_dex_opportunity_scanner_running(chain_id=46630, protocols=["uniswap_v4"])

    assert scanner is not None
    assert calls == {"start": 1, "watch": 0, "snapshot": 1, "quote_payload": 1, "trigger_context": 1}


def test_dex_opportunity_scanner_limits_routes_to_top_candidates_on_non_testnet():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    payload = {
        "poolStaticTable": {"network": "bnb", "chainId": 56},
        "routes": [
            {
                "symbolPair": "A -> B -> A",
                "routeSymbols": ["A", "B", "A"],
                "routeAddresses": ["0x1", "0x2", "0x1"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": True,
                    "referenceNetProfitUsd": 1.0,
                    "referenceNetReturnBps": 10.0,
                    "complete": True,
                },
            },
            {
                "symbolPair": "B -> C -> B",
                "routeSymbols": ["B", "C", "B"],
                "routeAddresses": ["0x2", "0x3", "0x2"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": True,
                    "referenceNetProfitUsd": 10.0,
                    "referenceNetReturnBps": 100.0,
                    "complete": True,
                },
            },
            {
                "symbolPair": "C -> D -> C",
                "routeSymbols": ["C", "D", "C"],
                "routeAddresses": ["0x3", "0x4", "0x3"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": True,
                    "chainSubmissionCandidate": True,
                    "referenceNetProfitUsd": 2.0,
                    "referenceNetReturnBps": 20.0,
                    "complete": True,
                },
            },
            {
                "symbolPair": "D -> E -> D",
                "routeSymbols": ["D", "E", "D"],
                "routeAddresses": ["0x4", "0x5", "0x4"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": False,
                    "complete": True,
                },
            },
        ],
    }

    attempts = build_dex_opportunity_attempts(
        payload,
        chain_id=56,
        observed_at="2026-08-29T00:00:00Z",
        max_onchain_evidence_routes=0,
        max_recordable_routes=2,
    )

    assert len(attempts) == 2
    assert {attempt["pair"] for attempt in attempts} == {"B -> C -> B", "C -> D -> C"}


def test_dex_opportunity_scanner_keeps_robinhood_testnet_candidates_but_bounds_live_review():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    calls = {"count": 0}

    payload = {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "routes": [
            {
                "symbolPair": "A -> B -> A",
                "routeSymbols": ["A", "B", "A"],
                "routeAddresses": ["0x1", "0x2", "0x1"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": True,
                    "referenceNetProfitUsd": 1.0,
                    "referenceNetReturnBps": 10.0,
                    "complete": True,
                },
            },
            {
                "symbolPair": "B -> C -> B",
                "routeSymbols": ["B", "C", "B"],
                "routeAddresses": ["0x2", "0x3", "0x2"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": True,
                    "referenceNetProfitUsd": 10.0,
                    "referenceNetReturnBps": 100.0,
                    "complete": True,
                },
            },
            {
                "symbolPair": "C -> D -> C",
                "routeSymbols": ["C", "D", "C"],
                "routeAddresses": ["0x3", "0x4", "0x3"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": True,
                    "chainSubmissionCandidate": True,
                    "referenceNetProfitUsd": 2.0,
                    "referenceNetReturnBps": 20.0,
                    "complete": True,
                },
            },
            {
                "symbolPair": "D -> E -> D",
                "routeSymbols": ["D", "E", "D"],
                "routeAddresses": ["0x4", "0x5", "0x4"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "profitable": False,
                    "complete": True,
                },
            },
        ],
    }

    def onchain_evidence_provider(route, **kwargs):
        calls["count"] += 1
        return {
            "evidenceVersion": 1,
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood-testnet",
            "chainId": 46630,
            "status": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "trustedForExecution": False,
            "readyForTransactionPreflight": False,
            "blockNumber": 12345,
            "triggerContext": kwargs.get("trigger_context") or {},
            "triggerBlockNumber": (kwargs.get("trigger_context") or {}).get("triggerBlockNumber"),
            "quoteBlockSource": "trigger_block",
            "routeSymbols": route.get("routeSymbols") or [],
            "routeAddresses": route.get("routeAddresses") or [],
            "hops": [{"hop": 1, "protocol": "uniswap_v4", "quoteMethod": "uniswap_v4_sdk_exact_input_single"}],
            "costModel": {"netProfitUsd": 1.0, "netProfitBps": 10.0},
            "validation": {"allHopsQuoted": True, "routeClosed": True, "finalOutputSymbol": "A"},
            "blockers": [],
            "transactionDevelopment": {"status": "ready_for_transaction_preflight_development", "executionEnabled": False, "blockers": []},
        }

    attempts = build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at="2026-08-29T00:00:00Z",
        onchain_evidence_provider=onchain_evidence_provider,
        max_onchain_evidence_routes=1,
        max_recordable_routes=1,
    )

    assert len(attempts) == 3
    assert calls["count"] == 1
    assert [attempt["pair"] for attempt in attempts] == ["A -> B -> A", "B -> C -> B", "C -> D -> C"]
    assert attempts[0]["quote"]["live_onchain_evidence"]["status"] == "live_verified"
    assert all(
        attempt["quote"]["live_onchain_evidence"]["status"] == "live_evidence_deferred"
        for attempt in attempts[1:]
    )
    assert payload["routes"][0]["live_onchain_evidence"]["status"] == "live_verified"
    assert payload["routes"][1]["quoteAnalysis"]["live_onchain_evidence"]["status"] == "live_evidence_deferred"


def test_dex_opportunity_scanner_marks_unreviewed_route_without_pool_depth_as_missing_effective_pool():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    payload = {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "routes": [
            {
                "symbolPair": "ETH -> TOKEN -> ETH",
                "routeSymbols": ["ETH", "TOKEN", "ETH"],
                "routeAddresses": ["0x1", "0x2", "0x1"],
                "quoteStatus": "requires_live_quoter",
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": True,
                    "staticDepthAvailable": False,
                    "staticDepthStatus": "unavailable",
                    "warnings": [],
                },
                "quoteInputs": {
                    "candidatePools": [
                        {
                            "pool": "0xabc",
                            "pairSymbol": "ETH:TOKEN",
                            "balances": {"tvlUsd": 0},
                            "liquidity": "",
                        }
                    ],
                    "quoteRequests": [{}],
                },
            }
        ],
    }

    attempts = build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at="2026-08-29T00:00:00Z",
        max_onchain_evidence_routes=0,
        max_recordable_routes=1,
    )

    assert len(attempts) == 1
    evidence = attempts[0]["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "missing_effective_pool"
    assert "missing effective trading pool" in evidence["blockers"][0]
    assert attempts[0]["quote"]["quote_required"] is False
    assert attempts[0]["precheck"]["requires_live_quoter"] is False
    assert payload["routes"][0]["quoteAnalysis"]["requiresLiveQuoter"] is False
    assert payload["routes"][0]["quoteAnalysis"]["live_onchain_evidence"]["status"] == "missing_effective_pool"


def test_dex_opportunity_scanner_writes_failed_onchain_evidence_into_snapshot_route():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    payload = {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "routes": [{
            "symbolPair": "ETH -> TOKEN -> ETH",
            "routeSymbols": ["ETH", "TOKEN", "ETH"],
            "routeAddresses": ["0x1", "0x2", "0x1"],
            "quoteStatus": "requires_live_quoter",
            "quoteAnalysis": {
                "complete": True,
                "requiresLiveQuoter": True,
                "referenceNetProfitUsd": 10.0,
                "steps": [{"hop": 1, "fromSymbol": "ETH", "toSymbol": "TOKEN"}],
            },
        }],
    }

    def onchain_evidence_provider(route, **kwargs):
        return {
            "status": "live_quote_failed",
            "livePriceVerified": False,
            "executionTrust": "static_reference_only",
            "trustedForExecution": False,
            "readyForTransactionPreflight": False,
            "blockNumber": 456,
            "hops": [{
                "hop": 2,
                "status": "live_quote_failed",
                "fromSymbol": "TOKEN",
                "toSymbol": "ETH",
                "amountIn": 100,
                "amountOutRaw": None,
                "liveQuoteError": "NotEnoughLiquidity",
            }],
            "costModel": {},
            "blockers": ["Hop 2 quote failed: NotEnoughLiquidity"],
        }

    attempts = build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at="2026-09-02T00:00:00Z",
        onchain_evidence_provider=onchain_evidence_provider,
        max_onchain_evidence_routes=1,
        max_recordable_routes=1,
    )

    route = payload["routes"][0]
    assert attempts[0]["quote"]["live_onchain_evidence"]["status"] == "live_quote_failed"
    assert route["live_onchain_evidence"]["status"] == "live_quote_failed"
    assert route["quoteVerification"]["blockNumber"] == 456
    failed_steps = [
        step
        for step in route["quoteAnalysis"]["steps"]
        if isinstance(step, dict) and step.get("liveQuoteError") == "NotEnoughLiquidity"
    ]
    assert len(failed_steps) == 1
    assert failed_steps[0]["hop"] == 2
    assert route["quoteAnalysis"]["netProfitUsd"] is None


def test_dex_submission_review_counts_recorded_opportunities_as_blocked():
    payload = dex_surfaces.dex_submission_review_payload(
        attempts=[
            {
                "review_category": "not_executable",
                "state": "quote_required",
                "pair": "USDG -> WETH -> TEST -> WETH -> USDG",
            }
        ],
        groups={},
        source="jsonl",
    )

    assert payload["summary"]["attemptCount"] == 1
    assert payload["summary"]["blockedCount"] == 1
    assert payload["attempts"][0]["pair"] == "USDG -> WETH -> TEST -> WETH -> USDG"


