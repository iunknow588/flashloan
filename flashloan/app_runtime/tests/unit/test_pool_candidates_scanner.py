import pytest

from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market import dex_quote_surface
from market.binance_market.candidate_pool_cache import store as candidate_pool_cache_store
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from dex.intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation
from market.strategy.arbitrage import ArbitrageConfig


def test_scanner_evidence_timeout_has_rpc_retry_budget_floor(monkeypatch):
    from dex.runtime import dex_opportunity_scanner

    monkeypatch.setenv("DEX_OPPORTUNITY_SCANNER_EVIDENCE_TIMEOUT_SECONDS", "45")
    assert dex_opportunity_scanner._scanner_evidence_timeout_seconds() >= 60.0


def test_scanner_dedupes_same_closed_loop_route_by_addresses():
    from dex.runtime.dex_opportunity_scanner import _dedupe_payload_routes

    routes = [
        {
            "routeAddresses": ["0xTZ", "0xTB", "0xTA", "0xTZ"],
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "quoteStatus": "rpc_verification_timeout",
        },
        {
            "routeAddresses": ["0xTZ", "0xTB", "0xTA", "0xTZ"],
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "quoteStatus": "requires_live_quoter",
        },
    ]

    deduped = _dedupe_payload_routes(routes)

    assert len(deduped) == 1
    assert deduped[0]["quoteStatus"] == "rpc_verification_timeout"


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


def test_dex_opportunity_scanner_snapshot_is_protocol_scoped(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "summary": {"routeCount": 1},
            "routes": [{"symbolPair": "TZ -> TB -> TA -> TZ", "quoteStatus": "live_verified"}],
        }

    scanner = DexOpportunityScanner(
        quote_payload_provider=quote_payload_provider,
        trigger_context_provider=lambda **kwargs: {"triggerBlockNumber": 12345},
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
    )

    scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    specific = scanner.snapshot(chain_id=46630, protocols=["uniswap_v4"])
    other = scanner.snapshot(chain_id=46630, protocols=["uniswap_v2"])
    legacy = scanner.snapshot(chain_id=46630)

    assert specific["ok"] is True
    assert specific["snapshot"]["payload"]["routes"][0]["symbolPair"] == "TZ -> TB -> TA -> TZ"
    assert other["ok"] is True
    assert not other["snapshot"].get("payload")
    assert legacy["ok"] is True
    assert legacy["snapshot"]["payload"]["routes"][0]["symbolPair"] == "TZ -> TB -> TA -> TZ"


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

    assert summary["onchainEvidenceReviewedRouteCount"] == 1
    assert summary["onchainEvidenceDeferredRouteCount"] == 0
    assert summary["onchainEvidenceFailedRouteCount"] == 1
    assert summary["liveQuoterRequiredCount"] == 1
    assert summary["liveVerifiedRouteCount"] == 0
    assert summary["bestLiveNetProfitUsd"] is None
    assert result["scanner"]["testnet_max_onchain_evidence_routes"] == 2


def test_scanner_does_not_mutate_quote_routes_payload_for_page_surface(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    evidence = {
        "status": "live_quote_failed",
        "executionTrust": "static_reference_only",
        "trustedForExecution": False,
        "blockNumber": 12345,
        "hops": [],
        "costModel": {},
        "blockers": ["test quote failure"],
    }

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood-testnet", "chainId": chain_id},
            "routes": [
                {
                    "symbolPair": "WOOP -> TOKEN -> WOOP",
                    "routeSymbols": ["WOOP", "TOKEN", "WOOP"],
                    "quoteStatus": "requires_live_quoter",
                    "quoteAnalysis": {
                        "complete": True,
                        "requiresLiveQuoter": True,
                        "referenceNetProfitUsd": 10.0,
                        "steps": [
                            {"hop": 1, "fromSymbol": "WOOP", "toSymbol": "TOKEN"},
                            {"hop": 2, "fromSymbol": "TOKEN", "toSymbol": "WOOP"},
                        ],
                    },
                }
            ],
        }

    source_payload = quote_payload_provider(chain_id=46630, protocols=["uniswap_v4"])
    scanner = DexOpportunityScanner(
        quote_payload_provider=quote_payload_provider,
        onchain_evidence_provider=lambda route, **kwargs: evidence,
        trigger_context_provider=lambda **kwargs: {"triggerBlockNumber": 12345},
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
        max_onchain_evidence_routes=0,
    )

    scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert "live_onchain_evidence" not in source_payload["routes"][0]
    snapshot_route = scanner.snapshot(chain_id=46630)["snapshot"]["payload"]["routes"][0]
    assert snapshot_route["live_onchain_evidence"]["status"] == "live_quote_failed"


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


def test_dex_opportunity_snapshot_starts_scanner_for_fresh_review(monkeypatch):
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
    assert calls == {"start": 1, "watch": 0, "snapshot": 1}


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


def test_dex_opportunity_scanner_reviews_all_robinhood_testnet_candidates_each_scan():
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
            "bestAmountConfirmed": True,
            "blockNumber": 12345,
            "triggerContext": kwargs.get("trigger_context") or {},
            "triggerBlockNumber": (kwargs.get("trigger_context") or {}).get("triggerBlockNumber"),
            "quoteBlockSource": "trigger_block",
            "routeSymbols": route.get("routeSymbols") or [],
            "routeAddresses": route.get("routeAddresses") or [],
            "hops": [{"hop": 1, "protocol": "uniswap_v4", "quoteMethod": "uniswap_v4_sdk_exact_input_single"}],
            "costModel": {
                "bestAmountConfirmed": True,
                "selectedBorrowInputAmount": 1.0,
                "borrowInputAmount": 1.0,
                "netProfitUsd": 1.0,
                "netProfitBps": 10.0,
            },
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
    assert calls["count"] == 3
    assert [attempt["pair"] for attempt in attempts] == ["A -> B -> A", "B -> C -> B", "C -> D -> C"]
    assert attempts[0]["quote"]["live_onchain_evidence"]["status"] == "live_verified"
    assert all(attempt["quote"]["live_onchain_evidence"]["status"] == "live_verified" for attempt in attempts[1:])
    assert payload["routes"][0]["live_onchain_evidence"]["status"] == "live_verified"
    assert payload["routes"][1]["quoteAnalysis"]["live_onchain_evidence"]["status"] == "live_verified"
    assert payload["routes"][2]["quoteAnalysis"]["live_onchain_evidence"]["status"] == "live_verified"


def test_dex_opportunity_scanner_reviews_large_candidate_set_without_deferred_states():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    calls = {"count": 0}
    routes = []
    for index in range(40):
        token = f"T{index:02d}"
        routes.append(
            {
                "symbolPair": f"T00 -> {token} -> T00",
                "routeSymbols": ["T00", token, "T00"],
                "routeAddresses": ["0x00", f"0x{index:040x}", "0x00"],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": True,
                },
            }
        )
    payload = {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "routes": routes,
    }

    def onchain_evidence_provider(route, **kwargs):
        calls["count"] += 1
        return {
            "evidenceVersion": 1,
            "network": "robinhood-testnet",
            "chainId": 46630,
            "status": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "trustedForExecution": False,
            "blockNumber": 12345,
            "hops": [],
            "costModel": {},
            "blockers": [],
        }

    attempts = build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at="2026-08-29T00:00:00Z",
        onchain_evidence_provider=onchain_evidence_provider,
        max_onchain_evidence_routes=0,
    )

    assert len(attempts) == 40
    assert calls["count"] == 40
    assert all(attempt["quote"]["live_onchain_evidence"]["status"] == "live_verified" for attempt in attempts)


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

    def onchain_evidence_provider(route, **kwargs):
        return {
            "evidenceVersion": 1,
            "network": "robinhood-testnet",
            "chainId": 46630,
            "status": "missing_effective_pool",
            "executionTrust": "static_reference_only",
            "trustedForExecution": False,
            "blockNumber": 12345,
            "hops": [],
            "costModel": {},
            "blockers": ["on-chain reverify complete; missing effective trading pool"],
        }

    attempts = build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at="2026-08-29T00:00:00Z",
        onchain_evidence_provider=onchain_evidence_provider,
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


def test_dex_opportunity_scanner_does_not_mark_observed_v4_pool_with_live_price_as_missing():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    payload = {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "routes": [
            {
                "symbolPair": "TZ -> TA -> TB -> TZ",
                "routeSymbols": ["TZ", "TA", "TB", "TZ"],
                "routeAddresses": ["0x53", "0xab", "0xaa", "0x53"],
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
                            "pool": "0xcdef",
                            "protocol": "uniswap_v4",
                            "balances": {"tvlUsd": 0},
                            "liquidity": "",
                            "poolKey": {
                                "poolId": "0xcdef",
                                "sqrtPriceX96": "25054144837438405210904448839",
                            },
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
    assert evidence["status"] != "missing_effective_pool"


def test_dex_opportunity_scanner_uses_route_level_v4_pool_when_candidate_pool_is_shallow():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    payload = {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "routes": [
            {
                "symbolPair": "TA -> TB -> TZ -> TA",
                "routeSymbols": ["TA", "TB", "TZ", "TA"],
                "routeAddresses": ["0xab", "0xcd", "0xef", "0xab"],
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
                            "pool": "0xcdef",
                            "balances": {"tvlUsd": 0},
                            "liquidity": "",
                        }
                    ],
                    "quoteRequests": [{}],
                },
                "routePools": [
                    {
                        "pool": "0xcdef",
                        "protocol": "uniswap_v4",
                        "balances": {"tvlUsd": 0},
                        "liquidity": "",
                        "poolKey": {
                            "poolId": "0xcdef",
                            "sqrtPriceX96": "25054144837438405210904448839",
                        },
                    }
                ],
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
    assert evidence["status"] != "missing_effective_pool"


def test_robinhood_testnet_route_step_preserves_pool_key_fields():
    from dex.intent_trade.chains.robinhood_testnet.market_observation import _step_row

    pool = {
        "pool": "0x1111",
        "poolIdentityMode": "v4_pool_id",
        "poolManager": "0xPoolManager",
        "poolKey": {"poolId": "0x1111", "sqrtPriceX96": "250541"},
        "quoter": "0xQuoter",
        "token0": "0x00",
        "token1": "0x01",
        "token0Symbol": "TA",
        "token1Symbol": "TB",
        "token0Decimals": 18,
        "token1Decimals": 6,
        "tickSpacing": 60,
        "hooks": "0x0000000000000000000000000000000000000000",
        "sqrtPriceX96": "250541",
        "liquidity": "1000",
    }
    row = _step_row(pool, from_token={"symbol": "TA", "address": "0x00"}, to_token={"symbol": "TB", "address": "0x01"}, direction="bridge")

    assert row["pool"] == "0x1111"
    assert row["poolId"] == "0x1111"
    assert row["poolManager"] == "0xPoolManager"
    assert row["poolKey"] == {"poolId": "0x1111", "sqrtPriceX96": "250541"}
    assert row["quoter"] == "0xQuoter"
    assert row["token0Decimals"] == 18
    assert row["token1Decimals"] == 6
    assert row["tickSpacing"] == 60


def test_framework_route_pools_merges_shallow_candidate_with_full_route_pool():
    merged = dex_quote_surface._framework_route_pools(
        {
            "routePools": [
                {
                    "pool": "0xcdef",
                    "protocol": "uniswap_v4",
                    "poolKey": {"poolId": "0xcdef", "sqrtPriceX96": "1"},
                    "poolManager": "0xPoolManager",
                    "token0Decimals": 18,
                    "token1Decimals": 6,
                }
            ],
            "quoteInputs": {
                "candidatePools": [
                    {
                        "pool": "0xcdef",
                        "balances": {"tvlUsd": 0},
                        "liquidity": "",
                    }
                ]
            },
        }
    )

    assert len(merged) == 1
    assert merged[0]["protocol"] == "uniswap_v4"
    assert merged[0]["poolKey"] == {"poolId": "0xcdef", "sqrtPriceX96": "1"}
    assert merged[0]["poolManager"] == "0xPoolManager"
    assert merged[0]["token0Decimals"] == 18
    assert merged[0]["balances"] == {"tvlUsd": 0}


def test_framework_route_pools_enriches_stale_shallow_pool_from_v4_index_lookup():
    enriched = dex_quote_surface._enrich_route_pools_from_index(
        [
            {
                "pool": "0xabcd",
                "protocol": "uniswap_v4",
                "sqrtPriceX96": "1",
                "token0": "0x00",
                "token1": "0x01",
            }
        ],
        index_by_pool={
            "0xabcd": {
                "poolId": "0xabcd",
                "poolManager": "0xPoolManager",
                "poolKey": {"poolId": "0xabcd", "sqrtPriceX96": "1"},
                "token0Decimals": 18,
                "token1Decimals": 6,
            }
        },
    )

    assert len(enriched) == 1
    assert enriched[0]["poolManager"] == "0xPoolManager"
    assert enriched[0]["poolKey"] == {"poolId": "0xabcd", "sqrtPriceX96": "1"}
    assert enriched[0]["token0Decimals"] == 18
    assert enriched[0]["token1Decimals"] == 6


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

    assert payload["summary"]["attemptCount"] == 0
    assert payload["summary"]["blockedCount"] == 0
    assert payload["attempts"] == []


def test_dex_submission_review_hides_quote_only_rows_without_tx_hash():
    payload = dex_surfaces.dex_submission_review_payload(
        attempts=[
            {
                "review_category": "not_executable",
                "state": "quote_required",
                "pair": "TZ -> TA -> TB -> TZ",
            },
            {
                "review_category": "execution_success",
                "state": "submitted_success",
                "pair": "TZ -> TA -> TB -> TZ",
                "tx_hash": "0x" + "a" * 64,
                "quote": {
                    "profit_amount": 1.5,
                    "final_amount": 12.5,
                },
            },
        ],
        groups={},
        source="jsonl",
    )

    assert payload["summary"]["attemptCount"] == 1
    assert payload["summary"]["successCount"] == 1
    assert payload["summary"]["blockedCount"] == 0
    assert payload["attempts"][0]["tx_hash"] == "0x" + "a" * 64


def test_dex_submission_review_hides_broadcast_attempt_without_tx_hash():
    payload = dex_surfaces.dex_submission_review_payload(
        attempts=[
            {
                "review_category": "execution_failed",
                "state": "submission_failed",
                "execution_phase": "submission",
                "precheck": {"broadcast": True},
                "pair": "TZ -> TA -> TB -> TZ",
                "error": "official_arb_broadcast_failed",
                "blocked_reasons": ["static_call_ok"],
            }
        ],
        groups={},
        source="jsonl",
    )

    assert payload["summary"]["attemptCount"] == 0
    assert payload["attempts"] == []


def test_dex_submission_review_keeps_offchain_probe_data_from_latest_estimate():
    probe = [
        {"amount": 1.0, "borrowAmount": 1.0, "referenceNetProfitAmount": 13.26, "executableCandidate": True},
        {"amount": 16.0, "borrowAmount": 16.0, "referenceNetProfitAmount": 148.9, "executableCandidate": True},
    ]
    payload = dex_surfaces.dex_submission_review_payload(
        attempts=[
            {
                "network": "robinhood-testnet",
                "created_at": "2026-09-24T08:47:00+00:00",
                "state": "quote_verified",
                "execution_phase": "quote_precheck",
                "precheck": {"broadcast": False},
                "pair": "TZ -> TA -> TB -> TZ",
                "probe_records": probe,
                "best_amount_calculation": {"bestAmount": 16.0, "probe_records": probe},
            },
            {
                "network": "robinhood-testnet",
                "created_at": "2026-09-24T08:50:00+00:00",
                "state": "submitted_success",
                "execution_phase": "order_submission",
                "precheck": {"broadcast": True},
                "pair": "TZ -> TA -> TB -> TZ",
                "tx_hash": "0x" + "c" * 64,
                "probe_records": [],
                "best_amount_calculation": {},
            },
        ],
        groups={},
        source="jsonl",
    )

    assert payload["summary"]["attemptCount"] == 1
    row = payload["attempts"][0]
    assert row["tx_hash"] == "0x" + "c" * 64
    assert len(row["probe_records"]) == 2
    assert row["best_amount_calculation"]["bestAmount"] == 16.0


def test_evidence_provider_returns_timeout_when_provider_is_slow():
    import time

    from dex.runtime.dex_opportunity_scanner import _evidence_provider_with_timeout

    def slow_provider(route, **kwargs):
        time.sleep(10.0)
        return {"status": "live_verified"}

    evidence = _evidence_provider_with_timeout(
        slow_provider,
        0.05,
        {"routeSymbols": ["TZ", "TB", "TZ"]},
        chain_id=46630,
    )

    assert evidence["status"] == "rpc_verification_timeout"
    assert evidence["blockers"] == ["on-chain evidence timed out after 0.1s"]
    assert evidence["routeSymbols"] == ["TZ", "TB", "TZ"]


def test_evidence_provider_returns_provider_result_when_fast():
    from dex.runtime.dex_opportunity_scanner import _evidence_provider_with_timeout

    def fast_provider(route, **kwargs):
        return {"status": "live_verified"}

    evidence = _evidence_provider_with_timeout(
        fast_provider,
        1.0,
        {"routeSymbols": ["TZ", "TB", "TZ"]},
        chain_id=46630,
    )

    assert evidence["status"] == "live_verified"


def test_closed_loop_quad_group_evidence_is_reused_within_one_scan():
    from runtime.dex_opportunity_scanner import build_dex_opportunity_attempts

    calls = {"count": 0}
    anchor = "0x00"
    mid_a = "0x" + "a" * 40
    mid_b = "0x" + "b" * 40
    payload = {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "routes": [
            {
                "symbolPair": "TZ -> TA -> TB -> TZ",
                "routeSymbols": ["TZ", "TA", "TB", "TZ"],
                "routeAddresses": [anchor, mid_a, mid_b, anchor],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {"complete": True, "requiresLiveQuoter": True},
            },
            {
                "symbolPair": "TZ -> TB -> TA -> TZ",
                "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                "routeAddresses": [anchor, mid_b, mid_a, anchor],
                "quoteStatus": "quote_required",
                "quoteAnalysis": {"complete": True, "requiresLiveQuoter": True},
            },
        ],
    }

    def onchain_evidence_provider(route, **kwargs):
        calls["count"] += 1
        return {
            "evidenceVersion": 1,
            "network": "robinhood-testnet",
            "chainId": 46630,
            "status": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "trustedForExecution": False,
            "blockNumber": 12345,
            "hops": [{"hop": 1, "status": "live_verified"}],
            "costModel": {},
            "blockers": [],
        }

    attempts = build_dex_opportunity_attempts(
        payload,
        chain_id=46630,
        observed_at="2026-09-19T00:00:00Z",
        onchain_evidence_provider=onchain_evidence_provider,
        max_onchain_evidence_routes=0,
    )

    assert len(attempts) == 2
    assert calls["count"] == 1
    assert attempts[0]["quote"]["live_onchain_evidence"]["status"] == "live_verified"
    assert attempts[1]["quote"]["live_onchain_evidence"]["status"] == "live_verified"


