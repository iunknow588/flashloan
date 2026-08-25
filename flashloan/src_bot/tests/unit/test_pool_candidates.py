from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from strategy.arbitrage import ArbitrageConfig


def _write_json(path, payload):
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_binance_market_state_uses_shared_pool_candidates(monkeypatch):
    monkeypatch.setattr(
        quote_service,
        "dex_pool_candidates_payload",
        lambda: {
            "mode": "stub_pool_candidates",
            "pairs": [],
            "summary": {},
            "poolStaticTable": {},
        },
    )

    payload = quote_service.build_binance_market_state(
        {},
        aave_symbols=[],
        arbitrage_config=ArbitrageConfig(notional_usd=1, trade_fee_percent=0, flashloan_fee_percent=0),
        top_limit=1,
        bottom_limit=1,
    )

    assert payload["pool_candidates"]["mode"] == "stub_pool_candidates"


def test_dex_pool_candidates_classifies_stable_and_non_stable_pools(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "USDC:USDT",
                    "token0Symbol": "USDC",
                    "token1Symbol": "USDT",
                    "token0Usd": 500_000,
                    "token1Usd": 500_000,
                    "tvlUsd": 1_000_000,
                    "status": "recommended",
                },
                {
                    "pool": "0x" + "2" * 40,
                    "symbolPair": "USDC:WBNB",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 250_000,
                    "token1Usd": 260_000,
                    "tvlUsd": 510_000,
                    "status": "recommended",
                },
                {
                    "pool": "0x" + "3" * 40,
                    "symbolPair": "BTCB:WBNB",
                    "token0Symbol": "BTCB",
                    "token1Symbol": "WBNB",
                    "token0Usd": 100_000,
                    "token1Usd": 100_000,
                    "tvlUsd": 200_000,
                    "status": "recommended",
                },
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [{"protocol": "venus_core"}, {"protocol": "pancakeswap_v2"}],
            "removed_protocols": [],
        },
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path)

    assert payload["summary"]["pairClassCounts"] == {
        "non_stable_pair": 1,
        "stable_pair": 1,
        "stable_to_non_stable_pair": 1,
    }
    assert payload["summary"]["stablePairCount"] == "1"
    assert payload["flashLoanLiquidity"]["available_protocols"] == ["venus_core", "pancakeswap_v2"]
    assert payload["dexSettlement"]["uniswapx"]["supported"] is True
    assert payload["dexSettlement"]["arbitrage_integration"]["can_combine_with_flashloan"] is True
    by_pair = {item["symbolPair"]: item for item in payload["pairs"]}
    assert by_pair["USDC:USDT"]["classification"]["pairClass"] == "stable_pair"
    assert by_pair["USDC:WBNB"]["classification"]["pairClass"] == "stable_to_non_stable_pair"
    assert by_pair["BTCB:WBNB"]["classification"]["pairClass"] == "non_stable_pair"
    assert by_pair["USDC:WBNB"]["flashLoanLinkage"]["direct_stable_flashloan"] is True
    assert by_pair["USDC:WBNB"]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is True


def test_dex_pool_candidates_marks_uniswapx_as_settlement_not_pool(tmp_path, monkeypatch):
    cache_path = tmp_path / "polygon_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "polygon",
            "chainId": 137,
            "pairs": [
                {
                    "symbolPair": "USDC:WETH",
                    "tokenXSymbol": "USDC",
                    "tokenYSymbol": "WETH",
                    "allPools": [
                        {
                            "pool": "0x" + "4" * 40,
                            "token0_symbol": "USDC",
                            "token1_symbol": "WETH",
                            "tvlUsd": 100_000,
                            "quality": "pass",
                        }
                    ],
                    "preferredPools": [
                        {
                            "pool": "0x" + "4" * 40,
                            "token0_symbol": "USDC",
                            "token1_symbol": "WETH",
                            "tvlUsd": 100_000,
                            "quality": "pass",
                        }
                    ],
                    "executionPools": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": [{"protocol": "aave_v3"}]},
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path)

    assert payload["dexSettlement"]["uniswapx"]["pool_model"] == "intent_order_flow_not_amm_pool"
    assert payload["dexSettlement"]["uniswapx"]["supported"] is False
    assert payload["dexSettlement"]["arbitrage_integration"]["can_combine_with_flashloan"] is False
    assert payload["pairs"][0]["settlementRails"] == []
    assert payload["pairs"][0]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is False


def test_dex_quote_routes_are_driven_by_candidate_pools_not_flashloan(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "5" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "pancakeswap_v2",
                    "protocol": "v2",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 600_000,
                    "token1Usd": 610_000,
                    "tvlUsd": 1_210_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path))

    candidates = pool_candidates.dex_pool_candidates_payload(cache_path)
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=56)
    request = dex_surfaces.dex_quote_request_payload(chain_id=56, pair="USDC -> WBNB -> USDC", venue="uniswapx", amount="1000")

    assert candidates["pairs"][0]["preferredPoolCount"] == "1"
    assert candidates["pairs"][0]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is False
    assert quotes["summary"]["routeCount"] == 1
    assert quotes["summary"]["probeNotionalUsd"] == 1000.0
    assert quotes["summary"]["completeStaticQuoteCount"] == 1
    assert quotes["summary"]["uniswapxQuoteableRouteCount"] == 1
    assert quotes["routes"][0]["overviewRoute"]["anchorBorrowSymbol"] == "USDC"
    assert quotes["routes"][0]["overviewRoute"]["targetSymbol"] == "WBNB"
    assert quotes["routes"][0]["quoteAnalysis"]["notionalUsd"] == 1000.0
    assert quotes["routes"][0]["quoteAnalysis"]["stepCount"] == 2
    assert quotes["routes"][0]["quoteAnalysis"]["steps"][0]["fromSymbol"] == "USDC"
    assert quotes["routes"][0]["quoteAnalysis"]["steps"][0]["toSymbol"] == "WBNB"
    assert quotes["routes"][0]["quoteAnalysis"]["netProfitUsd"] is not None
    assert quotes["routes"][0]["quoteAnalysis"]["recommendation"]["action"]
    assert quotes["routes"][0]["quoteInputs"]["quoteRequests"][0]["venue"] == "amm_pool"
    assert quotes["routes"][0]["quoteInputs"]["routePools"]
    assert len(quotes["routes"][0]["quoteInputs"]["routePools"]) >= len(quotes["routes"][0]["quoteInputs"]["candidatePools"])
    assert any(item["venue"] == "uniswapx" for item in quotes["routes"][0]["quoteInputs"]["quoteRequests"])
    assert request["ok"] is True
    assert request["status"] == "quote_request_ready"
    assert request["quoteAnalysis"]["notionalUsd"] == 1000.0
    assert request["quoteRequests"][0]["venue"] == "uniswapx"


def test_dex_quote_routes_keep_uniswap_v4_quote_requests(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "9" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 600_000,
                    "token1Usd": 610_000,
                    "tvlUsd": 1_210_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path))

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=56)
    request = dex_surfaces.dex_quote_request_payload(chain_id=56, pair="USDC -> WBNB -> USDC", venue="uniswap_v4", amount="1000")

    assert quotes["summary"]["liveQuoterRequiredCount"] == 1
    assert any(item["venue"] == "uniswap_v4" for item in quotes["routes"][0]["quoteInputs"]["quoteRequests"])
    v4_request = next(item for item in request["quoteRequests"] if item["venue"] == "uniswap_v4")
    assert request["ok"] is True
    assert request["status"] == "quote_request_ready"
    assert v4_request["quoteMode"] == "uniswap_v4_sdk_exact_input_single"
    assert v4_request["status"] == "sdk_adapter_required"


def test_dex_opportunity_scanner_records_uniswap_v4_adapter_required_routes(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    recorded_batches = []

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood", "chainId": 4663},
            "summary": {
                "routeCount": 1,
                "completeStaticQuoteCount": 1,
                "executableCandidateCount": 0,
                "profitableRouteCount": 0,
                "liveQuoterRequiredCount": 1,
                "sdkAdapterRequiredCount": 1,
                "bestObservedNetProfitUsd": None,
            },
            "routes": [
                {
                    "symbolPair": "USDC -> WBNB -> USDC",
                    "routeSymbols": ["USDC", "WBNB", "USDC"],
                    "quoteStatus": "requires_live_quoter",
                    "overviewRoute": {"kind": "direct_anchor_pair", "targetSymbol": "WBNB"},
                    "quoteInputs": {
                        "candidatePools": [
                            {
                                "pool": "0x" + "9" * 40,
                                "dex": "uniswap_v4",
                                "protocol": "uniswap_v4",
                                "token0Symbol": "USDC",
                                "token1Symbol": "WBNB",
                            }
                        ],
                        "quoteRequests": [
                            {
                                "venue": "uniswap_v4",
                                "quoteMode": "uniswap_v4_sdk_exact_input_single",
                                "status": "sdk_adapter_required",
                            }
                        ],
                    },
                    "quoteAnalysis": {
                        "method": "static_amm_depth_probe",
                        "notionalUsd": 1000.0,
                        "inputSymbol": "USDC",
                        "outputSymbol": "USDC",
                        "finalOutputUsd": None,
                        "netProfitUsd": None,
                        "profitable": False,
                        "complete": True,
                        "requiresLiveQuoter": True,
                        "adapterRequired": True,
                        "executableCandidate": False,
                        "recommendation": {
                            "level": "requires_live_quote",
                            "action": "等待链上 Quoter 复核",
                            "reasons": ["v4 SDK adapter required"],
                        },
                        "steps": [
                            {
                                "hop": 1,
                                "fromSymbol": "USDC",
                                "toSymbol": "WBNB",
                                "executionTrust": "sdk_adapter_required",
                                "quoteSource": "uniswap_v4_sdk_adapter_required",
                            }
                        ],
                    },
                }
            ],
        }

    def record_attempts(attempts):
        recorded_batches.append(attempts)
        return {"recorded": len(attempts), "source": "jsonl", "error": None}

    def onchain_evidence_provider(route, **kwargs):
        return {
            "evidenceVersion": 1,
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood",
            "chainId": 4663,
            "status": "live_quote_failed",
            "executionTrust": "static_reference_only",
            "trustedForExecution": False,
            "blockNumber": None,
            "triggerContext": kwargs.get("trigger_context") or {},
            "triggerBlockNumber": (kwargs.get("trigger_context") or {}).get("triggerBlockNumber"),
            "quoteBlockSource": "trigger_block",
            "routeSymbols": route.get("routeSymbols") or [],
            "hops": [],
            "costModel": {},
            "blockers": ["uniswap v4 quote adapter is not configured"],
            "transactionDevelopment": {"status": "blocked_before_transaction_development", "executionEnabled": False, "blockers": ["uniswap v4 quote adapter is not configured"]},
        }

    scanner = DexOpportunityScanner(
        interval_seconds=3,
        record_cooldown_seconds=0,
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

    payload = scanner.scan_now(chain_id=4663, protocols=["uniswap_v4"])

    assert payload["snapshot"]["summary"]["sdk_adapter_required_count"] == 1
    assert payload["snapshot"]["recording"]["candidate_count"] == 1
    assert payload["snapshot"]["recording"]["recorded"] == 1
    assert len(recorded_batches) == 1
    assert recorded_batches[0][0]["quote"]["live_onchain_evidence"]["status"] == "live_quote_failed"
    assert recorded_batches[0][0]["quote"]["quote_requests"][0]["status"] == "sdk_adapter_required"


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


def test_dex_opportunity_scanner_records_discovered_opportunities(tmp_path):
    from db.storage_cow_execution import append_cow_execution_attempts_jsonl, load_recent_cow_execution_attempts_jsonl
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


def test_clear_cow_execution_attempts_jsonl_filters_by_network(tmp_path):
    from db.storage_cow_execution import (
        append_cow_execution_attempts_jsonl,
        clear_cow_execution_attempts_jsonl,
        load_recent_cow_execution_attempts_jsonl,
    )

    path = tmp_path / "cow_execution_attempts.jsonl"
    attempts = [
        {
            "observed_at": "2026-08-24T00:00:00+00:00",
            "network": "robinhood",
            "pair": "USDG -> WETH -> USDG",
            "state": "quote_required",
            "execution_phase": "market_candidate",
            "checks_passed": False,
            "can_submit_order": False,
        },
        {
            "observed_at": "2026-08-24T00:00:01+00:00",
            "network": "bnb",
            "pair": "USDC -> WBNB -> USDC",
            "state": "quote_required",
            "execution_phase": "market_candidate",
            "checks_passed": False,
            "can_submit_order": False,
        },
    ]

    assert append_cow_execution_attempts_jsonl(path, attempts) == 2
    result = clear_cow_execution_attempts_jsonl(path, networks=["robinhood"])
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)

    assert result["cleared"] == 1
    assert [row["network"] for row in rows] == ["bnb"]


def test_dex_overview_does_not_reuse_robinhood_borrow_anchor_on_other_chains(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "6" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "pancakeswap_v2",
                    "protocol": "v2",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 400_000,
                    "token1Usd": 390_000,
                    "tvlUsd": 790_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [],
            "removed_protocols": [],
            "raw_snapshots": {
                "robinhood_flash_liquidity": {
                    "chainId": 4663,
                    "sources": [
                        {"protocol": "morpho_blue", "token_symbol": "USDG", "flash_borrowable": True},
                    ],
                }
            },
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(
        dex_surfaces,
        "dex_pool_candidates_payload",
        lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path),
    )

    overview = dex_surfaces.dex_overview_payload(chain_id=56)

    assert overview["summary"]["anchorBorrowSymbols"] == ["USDC"]
    assert overview["arbitragePaths"][0]["overviewRoute"]["pathText"] == "USDC -> WBNB -> USDC"


def test_dex_quote_routes_do_not_apply_v2_reserve_math_to_v3_pools(monkeypatch):
    weth_usdg_pool = {
        "pool": "0x" + "7" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "WETH",
        "token1Symbol": "USDG",
        "balances": {"token0": 66.1894, "token1": 1_602_651, "tvlUsd": 1_765_261.05},
        "market": {"priceNative": "2456.7359", "priceUsd": "2456.73"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    pons_weth_pool = {
        "pool": "0x" + "8" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "PONS",
        "token1Symbol": "WETH",
        "balances": {"token0": 10_253_738, "token1": 306.3267, "tvlUsd": 1_464_712.3},
        "market": {"priceNative": "0.00002810", "priceUsd": "0.06924"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "WETH:USDG",
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "USDG",
                "preferredPools": [weth_usdg_pool],
                "executionPools": [weth_usdg_pool],
                "strictQualityPools": [weth_usdg_pool],
            },
            {
                "symbolPair": "PONS:WETH",
                "tokenXSymbol": "PONS",
                "tokenYSymbol": "WETH",
                "preferredPools": [pons_weth_pool],
                "executionPools": [pons_weth_pool],
                "strictQualityPools": [pons_weth_pool],
            },
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood", "chainId": 4663},
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [],
            "removed_protocols": [],
            "raw_snapshots": {
                "robinhood_flash_liquidity": {
                    "chainId": 4663,
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "USDG", "flash_borrowable": True}],
                }
            },
        },
    )

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)
    route = next(item for item in quotes["routes"] if item["symbolPair"] == "USDG -> WETH -> PONS -> WETH -> USDG")
    analysis = route["quoteAnalysis"]

    assert route["quoteStatus"] == "requires_live_quoter"
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["executableCandidate"] is False
    assert analysis["finalOutputUsd"] is None
    assert analysis["referenceFinalOutputUsd"] < 1000
    assert analysis["netProfitUsd"] is None
    assert analysis["referenceNetProfitUsd"] is not None
    assert analysis["steps"][0]["outputUsd"] is None
    assert analysis["steps"][0]["referenceOutputUsd"] is not None
    assert all(step["quoteSource"] == "reference_price_requires_live_quoter" for step in analysis["steps"])
    assert analysis["recommendation"]["action"] == "等待链上 Quoter 复核"


def test_dex_quote_routes_drop_polluted_bridge_symbols(monkeypatch):
    usdg_weth_pool = {
        "pool": "0x" + "a" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "USDG",
        "token1Symbol": "WETH",
        "balances": {"token0": 1_000_000, "token1": 400, "tvlUsd": 1_000_000},
        "market": {"priceNative": "0.0004", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    weth_robinhood_pool = {
        "pool": "0x" + "b" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "WETH",
        "token1Symbol": "ROBINHOOD",
        "balances": {"token0": 400, "token1": 10_000_000, "tvlUsd": 1_000_000},
        "market": {"priceNative": "25000", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    robinhood_usdg_pool = {
        "pool": "0x" + "c" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0Symbol": "ROBINHOOD",
        "token1Symbol": "USDG",
        "balances": {"token0": 10_000_000, "token1": 900_000, "tvlUsd": 1_000_000},
        "market": {"priceNative": "0.00009", "priceUsd": "0.00009"},
        "depthBand": {"level": "deep", "label": "深度充足"},
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "USDG:WETH",
                "tokenXSymbol": "USDG",
                "tokenYSymbol": "WETH",
                "preferredPools": [usdg_weth_pool],
                "executionPools": [usdg_weth_pool],
                "strictQualityPools": [usdg_weth_pool],
            },
            {
                "symbolPair": "WETH:ROBINHOOD",
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "ROBINHOOD",
                "preferredPools": [weth_robinhood_pool],
                "executionPools": [weth_robinhood_pool],
                "strictQualityPools": [weth_robinhood_pool],
            },
            {
                "symbolPair": "ROBINHOOD:USDG",
                "tokenXSymbol": "ROBINHOOD",
                "tokenYSymbol": "USDG",
                "preferredPools": [robinhood_usdg_pool],
                "executionPools": [robinhood_usdg_pool],
                "strictQualityPools": [robinhood_usdg_pool],
            },
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood", "chainId": 4663},
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [],
            "removed_protocols": [],
            "raw_snapshots": {
                "robinhood_flash_liquidity": {
                    "chainId": 4663,
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "USDG", "flash_borrowable": True}],
                }
            },
        },
    )

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)

    assert all("ROBINHOOD" not in route["symbolPair"] for route in quotes["routes"])
    assert all("ROBINHOOD" not in " -> ".join(route["routeSymbols"]) for route in quotes["routes"])
    assert quotes["summary"]["routeCount"] == len(quotes["routes"])


def test_robinhood_pool_candidates_split_same_symbol_by_token_address(monkeypatch):
    rows = [
        {
            "pool": "0x" + "1" * 40,
            "dex": "uniswap",
            "protocol": "uniswap_v3",
            "token0": "0x" + "a" * 40,
            "token1": "0x" + "b" * 40,
            "token0Symbol": "WETH",
            "token1Symbol": "VLAD",
            "symbolPair": "WETH:VLAD",
            "quality": "pass",
            "tvlUsd": 100_000,
        },
        {
            "pool": "0x" + "2" * 40,
            "dex": "uniswap",
            "protocol": "uniswap_v3",
            "token0": "0x" + "a" * 40,
            "token1": "0x" + "c" * 40,
            "token0Symbol": "WETH",
            "token1Symbol": "VLAD",
            "symbolPair": "WETH:VLAD",
            "quality": "pass",
            "tvlUsd": 200_000,
        },
    ]
    monkeypatch.setattr(pool_candidates, "_robinhood_pool_rows_and_source", lambda table, path: (rows, path, "test"))

    payload = pool_candidates._robinhood_pool_table_payload(
        {"schemaVersion": "robinhood-test", "network": "robinhood", "chainId": 4663},
        pool_candidates.ROBINHOOD_DEX_CANDIDATE_PATH,
        source="test",
        fallback_reason=None,
    )

    vlad_pairs = [pair for pair in payload["pairs"] if pair["symbolPair"] == "WETH:VLAD"]
    assert len(vlad_pairs) == 2
    assert {pair["addressPair"] for pair in vlad_pairs} == {
        "0x" + "a" * 40 + ":" + "0x" + "b" * 40,
        "0x" + "a" * 40 + ":" + "0x" + "c" * 40,
    }
    assert all(pair["identityMode"] == "address" for pair in vlad_pairs)


def test_dex_quote_routes_keep_same_symbol_token_addresses_separate(monkeypatch):
    usdg = "0x" + "d" * 40
    weth = "0x" + "a" * 40
    vlad_a = "0x" + "b" * 40
    vlad_b = "0x" + "c" * 40
    usdg_weth_pool = {
        "pool": "0x" + "1" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v2",
        "fee": "v2",
        "token0": usdg,
        "token1": weth,
        "token0Symbol": "USDG",
        "token1Symbol": "WETH",
        "balances": {"token0": 1_000_000, "token1": 400, "tvlUsd": 1_000_000},
        "market": {"priceNative": "0.0004", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    weth_vlad_a_pool = {
        "pool": "0x" + "2" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0": weth,
        "token1": vlad_a,
        "token0Symbol": "WETH",
        "token1Symbol": "VLAD",
        "balances": {"token0": 400, "token1": 10_000_000, "tvlUsd": 1_000_000},
        "market": {"priceNative": "25000", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    weth_vlad_b_pool = {
        "pool": "0x" + "3" * 40,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "v3",
        "token0": weth,
        "token1": vlad_b,
        "token0Symbol": "WETH",
        "token1Symbol": "VLAD",
        "balances": {"token0": 400, "token1": 20_000_000, "tvlUsd": 1_200_000},
        "market": {"priceNative": "50000", "priceUsd": "2500"},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "USDG:WETH",
                "addressPair": f"{usdg}:{weth}",
                "tokenX": usdg,
                "tokenY": weth,
                "tokenXSymbol": "USDG",
                "tokenYSymbol": "WETH",
                "preferredPools": [usdg_weth_pool],
                "executionPools": [usdg_weth_pool],
                "strictQualityPools": [usdg_weth_pool],
            },
            {
                "symbolPair": "WETH:VLAD",
                "addressPair": f"{weth}:{vlad_a}",
                "tokenX": weth,
                "tokenY": vlad_a,
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "VLAD",
                "preferredPools": [weth_vlad_a_pool],
                "executionPools": [weth_vlad_a_pool],
                "strictQualityPools": [weth_vlad_a_pool],
            },
            {
                "symbolPair": "WETH:VLAD",
                "addressPair": f"{weth}:{vlad_b}",
                "tokenX": weth,
                "tokenY": vlad_b,
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "VLAD",
                "preferredPools": [weth_vlad_b_pool],
                "executionPools": [weth_vlad_b_pool],
                "strictQualityPools": [weth_vlad_b_pool],
            },
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood", "chainId": 4663},
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [],
            "removed_protocols": [],
            "raw_snapshots": {
                "robinhood_flash_liquidity": {
                    "chainId": 4663,
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "USDG", "flash_borrowable": True}],
                }
            },
        },
    )

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)
    vlad_routes = [route for route in quotes["routes"] if route["symbolPair"] == "USDG -> WETH -> VLAD -> WETH -> USDG"]

    assert len(vlad_routes) == 2
    assert {route["routeAddresses"][2] for route in vlad_routes} == {vlad_a, vlad_b}
    for route in vlad_routes:
        steps = route["quoteAnalysis"]["steps"]
        assert steps[1]["tokenOut"] == steps[2]["tokenIn"]
        assert route["routeTokenPath"][2]["address"] == steps[1]["tokenOut"]
        assert route["routeTokenPath"][2]["address"] == steps[2]["tokenIn"]


def test_dex_pool_candidates_returns_empty_for_unconfigured_chain(monkeypatch):
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )

    payload = pool_candidates.dex_pool_candidates_payload(chain_id=137)

    assert payload["poolStaticTable"]["chainId"] == 137
    assert payload["poolStaticTable"]["exists"] is False
    assert payload["fallback_reason"] == "dex_pool_static_table_not_configured_for_chain"
    assert payload["pairs"] == []


def test_dex_pool_candidates_reads_robinhood_candidate_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "robinhood_dex_candidates.json"
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_MARKET_POOL_PATH", tmp_path / "missing_market.json")
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_EVIDENCE_ROOT", tmp_path / "missing_evidence")
    _write_json(
        cache_path,
        {
            "schemaVersion": "robinhood-candidate-cache-v1",
            "network": "robinhood",
            "generatedAt": "2026-08-24T00:00:00+00:00",
            "candidates": [
                {
                    "route_key": "USDG:WETH:100->500",
                    "route_symbols": ["USDG", "WETH", "USDG"],
                    "pool_addresses": ["0x" + "a" * 40, "0x" + "b" * 40],
                    "evidence": {
                        "pools": [
                            {"liquidity": "960106731002746306", "blockNumber": 43037858},
                            {"liquidity": "290336060850729630", "blockNumber": 43037858},
                        ],
                    },
                    "status": "observed",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [{"protocol": "morpho_blue"}, {"protocol": "uniswap_v3"}],
            "removed_protocols": [],
        },
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path, protocols=["uniswap_v3"])
    lending_filtered_payload = pool_candidates.dex_pool_candidates_payload(cache_path, protocols=["morpho_blue"])

    assert payload["poolStaticTable"]["chainId"] == 4663
    assert payload["poolStaticTable"]["network"] == "robinhood"
    assert payload["summary"]["pairTableCount"] == "1"
    assert payload["pairs"][0]["symbolPair"] == "USDG:WETH"
    assert payload["pairs"][0]["preferredPoolCount"] == "2"
    assert payload["pairs"][0]["preferredPools"][0]["protocol"] == "uniswap_v3"
    assert payload["pairs"][0]["preferredPools"][0]["fee"] == "100"
    assert payload["pairs"][0]["preferredPools"][0]["liquidity"] == "960106731002746306"
    assert payload["pairs"][0]["preferredPools"][0]["depthBand"]["level"] == "observed"
    assert lending_filtered_payload["summary"]["pairTableCount"] == "1"
    assert lending_filtered_payload["pairs"][0]["symbolPair"] == "USDG:WETH"


def test_dex_pool_candidates_prefers_robinhood_market_pool_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "robinhood_dex_candidates.json"
    market_path = tmp_path / "robinhood_dex_market_pools.json"
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_MARKET_POOL_PATH", market_path)
    monkeypatch.setattr(pool_candidates, "ROBINHOOD_DEX_EVIDENCE_ROOT", tmp_path / "missing_evidence")
    _write_json(
        cache_path,
        {
            "schemaVersion": "robinhood-candidate-cache-v1",
            "network": "robinhood",
            "chainId": 4663,
            "generatedAt": "2026-08-24T00:00:00+00:00",
            "candidates": [
                {
                    "route_key": "USDG:WETH:100->500",
                    "route_symbols": ["USDG", "WETH", "USDG"],
                    "pool_addresses": ["0x" + "a" * 40, "0x" + "b" * 40],
                }
            ],
        },
    )
    _write_json(
        market_path,
        {
            "schemaVersion": "robinhood-dex-market-cache-v1",
            "network": "robinhood",
            "chainId": 4663,
            "generatedAt": "2026-08-24T00:00:00+00:00",
            "pools": [
                {
                    "pool": "0x" + "1" * 40,
                    "dex": "uniswap",
                    "protocol": "uniswap_v3",
                    "token0Symbol": "WALLET",
                    "token1Symbol": "WETH",
                    "symbolPair": "WALLET:WETH",
                    "quality": "pass",
                    "tvlUsd": 796394.48,
                    "volumeH24Usd": 517990.57,
                },
                {
                    "pool": "0x" + "2" * 40,
                    "dex": "uniswap",
                    "protocol": "uniswap_v3",
                    "token0Symbol": "BRODIE",
                    "token1Symbol": "WETH",
                    "symbolPair": "BRODIE:WETH",
                    "quality": "pass",
                    "tvlUsd": 190013.68,
                    "volumeH24Usd": 131934.42,
                },
            ],
        },
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path, protocols=["morpho_blue"])

    assert payload["filters"]["source"] == "robinhood_dexscreener_market_cache"
    assert payload["summary"]["pairTableCount"] == "2"
    assert {pair["symbolPair"] for pair in payload["pairs"]} == {"WALLET:WETH", "BRODIE:WETH"}
    by_pair = {pair["symbolPair"]: pair for pair in payload["pairs"]}
    assert by_pair["WALLET:WETH"]["preferredPools"][0]["balances"]["tvlUsd"] == 796394.48
