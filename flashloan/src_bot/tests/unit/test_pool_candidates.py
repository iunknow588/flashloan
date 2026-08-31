from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market.candidate_pool_cache import store as candidate_pool_cache_store
from intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation
from strategy.arbitrage import ArbitrageConfig


def _write_json(path, payload):
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


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


def test_anchor_candidate_groups_split_yc_and_zc_by_anchor():
    pair_reports = [
        {
            "symbolPair": "USDT:ETH",
            "tokenXSymbol": "USDT",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDT"], "nonStableSymbols": ["ETH"]},
            "preferredPools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "a" * 40,
                    "token1": "0x" + "b" * 40,
                    "token0Symbol": "USDT",
                    "token1Symbol": "ETH",
                    "balances": {"tvlUsd": 1000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        }
    ]

    groups = build_anchor_candidate_groups(pair_reports)

    assert groups[0]["anchorSymbol"] == "USDT"
    assert groups[0]["ycPoolCount"] == "1"
    assert groups[0]["zcPoolCount"] == "1"
    assert groups[0]["ycTokens"][0]["symbol"] == "ETH"
    assert groups[0]["zcTokens"][0]["symbol"] == "ETH"
    assert groups[0]["ycPools"][0]["routeText"] == "USDT -> ETH"
    assert groups[0]["zcPools"][0]["routeText"] == "ETH -> USDT"


def test_anchor_candidate_groups_include_all_symbols_when_mixed_pairs_are_present():
    pair_reports = [
        {
            "symbolPair": "USDC:FRAX",
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "FRAX",
            "classification": {"stableSymbols": ["USDC", "FRAX"], "nonStableSymbols": []},
            "preferredPools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "a" * 40,
                    "token1": "0x" + "b" * 40,
                    "token0Symbol": "USDC",
                    "token1Symbol": "FRAX",
                    "balances": {"tvlUsd": 1000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        },
        {
            "symbolPair": "WETH:WAVAX",
            "tokenXSymbol": "WETH",
            "tokenYSymbol": "WAVAX",
            "classification": {"stableSymbols": [], "nonStableSymbols": ["WETH", "WAVAX"]},
            "preferredPools": [
                {
                    "pool": "0x" + "2" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "c" * 40,
                    "token1": "0x" + "d" * 40,
                    "token0Symbol": "WETH",
                    "token1Symbol": "WAVAX",
                    "balances": {"tvlUsd": 2000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        },
    ]

    groups = build_anchor_candidate_groups(pair_reports)
    anchor_symbols = [group["anchorSymbol"] for group in groups]

    assert anchor_symbols == ["FRAX", "USDC", "WAVAX", "WETH"]
    assert {group["anchorSymbol"] for group in groups} == {"FRAX", "USDC", "WAVAX", "WETH"}


def test_anchor_candidate_groups_use_all_pools_and_keep_unique_queues():
    pool_a = {
        "pool": "0x" + "1" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "pass",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    pool_b = {
        "pool": "0x" + "2" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "rejected",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 500},
        "depthBand": {"level": "shallow", "label": "shallow"},
    }

    pair_reports = [
        {
            "symbolPair": "USDC:ETH",
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDC"], "nonStableSymbols": ["ETH"]},
            "allPools": [pool_a, pool_a, pool_b],
            "preferredPools": [pool_a],
            "strictQualityPools": [pool_a],
            "executionPools": [pool_a],
            "rejectedPools": [pool_b],
        }
    ]

    groups = build_anchor_candidate_groups(pair_reports)
    group = groups[0]

    assert group["candidatePoolCount"] == "2"
    assert group["candidateQueueCount"] == "4"
    assert len(group["ycPools"]) == 2
    assert len(group["zcPools"]) == 2
    assert len({row["routeKey"] for row in group["ycPools"]}) == 2


def test_robinhood_testnet_v4_pool_candidates_use_chain_owned_flow():
    payload = pool_candidates.dex_pool_candidates_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert payload["poolStaticTable"]["network"] == "robinhood-testnet"
    assert payload["poolStaticTable"]["chainId"] == 46630
    assert payload["poolStaticTable"]["mode"] in {"robinhood_testnet_v4_pool_index", "robinhood_testnet_v4_pool_liquidity"}
    assert payload["poolStaticTable"]["exists"] is True
    assert payload["processing"]["handler"] == (
        "intent_trade.chains.robinhood_testnet.dex_pool_candidates:dex_pool_candidates_payload"
    )
    assert int(payload["summary"]["pairTableCount"]) > 0
    assert int(payload["summary"]["anchorGroupCount"]) > 0
    assert int(payload["summary"]["anchorGroupCount"]) <= int(payload["summary"]["flashAccountingAssetCount"])
    assert payload["summary"]["candidateSetStorageMode"] == "shared_yc_zc_pool_set"
    assert int(payload["summary"]["ycZcSameSetGroupCount"]) > 0
    assert int(payload["summary"]["candidateRouteCount"]) == int(payload["summary"]["v4ActivePoolCount"])
    assert payload["pairs"][0]["identityMode"] == "pool_key"
    assert payload["pairs"][0]["preferredPools"][0]["poolIdentityMode"] == "pool_id"
    assert payload["pairs"][0]["preferredPools"][0]["protocol"] == "uniswap_v4"
    assert payload["anchorGroups"]
    assert payload["anchorGroups"][0]["ycZcSameSet"] is True
    assert payload["anchorGroups"][0]["ycPoolsRef"] == "sharedPools"
    assert payload["anchorGroups"][0]["sharedPools"]


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
    monkeypatch.setattr(pool_candidates, "_candidate_pool_source_available", lambda chain_id: True)
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
    assert v4_request["quoteMode"] == "uniswap_v4_sdk_exact_input_route"
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


def test_dex_opportunity_scanner_full_scans_robinhood_testnet_recordable_routes():
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
    assert calls["count"] == 3
    assert [attempt["pair"] for attempt in attempts] == ["A -> B -> A", "B -> C -> B", "C -> D -> C"]
    assert all(attempt["quote"]["live_onchain_evidence"]["status"] == "live_verified" for attempt in attempts)
    assert all(attempt["quote"]["live_onchain_evidence"]["status"] != "live_evidence_deferred" for attempt in attempts)


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


def test_robinhood_testnet_dex_surfaces_use_all_candidate_symbols(monkeypatch):
    pool_template = lambda suffix, left, right: {
        "pool": f"0x{suffix * 40}",
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "fee": "0",
        "quality": "pass",
        "token0": f"0x{left.lower() * 40}",
        "token1": f"0x{right.lower() * 40}",
        "token0Symbol": left,
        "token1Symbol": right,
        "balances": {"tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    payload = {
        "pairs": [
            {
                "symbolPair": "FRAX:USDC",
                "tokenXSymbol": "FRAX",
                "tokenYSymbol": "USDC",
                "tokenX": "0x" + "a" * 40,
                "tokenY": "0x" + "b" * 40,
                "preferredPools": [pool_template("1", "FRAX", "USDC")],
                "executionPools": [pool_template("1", "FRAX", "USDC")],
                "strictQualityPools": [pool_template("1", "FRAX", "USDC")],
            },
            {
                "symbolPair": "ETH:CCOX",
                "tokenXSymbol": "ETH",
                "tokenYSymbol": "CCOX",
                "tokenX": "0x" + "c" * 40,
                "tokenY": "0x" + "d" * 40,
                "preferredPools": [pool_template("2", "ETH", "CCOX")],
                "executionPools": [pool_template("2", "ETH", "CCOX")],
                "strictQualityPools": [pool_template("2", "ETH", "CCOX")],
            },
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
    }

    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
        lambda anchor_groups, chain_id=None, protocols=None: ([], {"enabled": True, "status": "test_disabled"}),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {"enabled": True, "status": "test_disabled", "observationSequences": []},
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_route_quote_analysis",
        lambda route, price_map=None, notional_usd=None, **kwargs: {
            "method": "mock",
            "notionalUsd": notional_usd or 1000.0,
            "inputSymbol": (route.get("routeSymbols") or [None])[0],
            "outputSymbol": (route.get("routeSymbols") or [None])[-1],
            "complete": True,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "stepCount": 0,
            "steps": [],
            "finalOutputUsd": 1010.0,
            "referenceFinalOutputUsd": 1010.0,
            "grossProfitUsd": 10.0,
            "referenceGrossProfitUsd": 10.0,
            "flashLoanFeeUsd": 0.5,
            "gasReserveUsd": 1.0,
            "minProfitUsd": 1.0,
            "offchainMinProfitUsd": 1.0,
            "submissionMinProfitUsd": 0.5,
            "submissionNetProfitUsd": 9.5,
            "offchainNetProfitUsd": 8.5,
            "netProfitUsd": 8.5,
            "referenceNetProfitUsd": 8.5,
            "netProfitBps": 85.0,
            "referenceNetProfitBps": 85.0,
            "profitable": True,
            "executableCandidate": True,
            "sdkAdapterRequiredStepCount": 0,
            "recommendation": {"level": "ready_for_precheck", "action": "进入自动交易预检", "reasons": []},
            "warnings": [],
        },
    )

    overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v4"])
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v4"])

    expected_symbols = ["CCOX", "ETH", "FRAX", "USDC"]
    assert overview["decisionBasis"] == "robinhood_testnet_market_observation_closed_loop"
    assert overview["summary"]["anchorBorrowSymbols"] == ["ETH"]
    assert observation["summary"]["observationAnchorCount"] == 4
    assert observation["observationAnchorSymbols"] == expected_symbols
    assert quotes["summary"]["anchorBorrowSymbols"] == expected_symbols
    assert quotes["summary"]["closedLoopAnchorSymbols"] == expected_symbols
    assert quotes["summary"]["routeCount"] > 1
    assert len({route["overviewRoute"]["anchorBorrowSymbol"] for route in quotes["routes"]}) > 1


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
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "WETH", "flash_borrowable": True}],
                }
            },
        },
    )

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)
    route = next(item for item in quotes["routes"] if item["symbolPair"] == "WETH -> PONS -> WETH")
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
    assert analysis["recommendation"]["action"] == "参考测算完成，待链上复核"


def test_robinhood_testnet_live_v4_adapter_quotes_mark_live_verified(monkeypatch):
    from market.binance_market import dex_surfaces

    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    pool_xy = {
        "pool": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "0X71178B",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "tickSpacing": 60,
        "poolManager": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
    }
    route = {
        "symbolPair": "ETH -> 0X71178B -> ETH",
        "routeSymbols": ["ETH", "0X71178B", "ETH"],
        "routeAddresses": [eth, token_y, eth],
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "ETH", "address": eth},
        ],
        "routePools": [pool_xy],
        "preferredPools": [pool_xy],
        "strictQualityPools": [pool_xy],
        "executionPools": [pool_xy],
    }

    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())

    def fake_quote_v4_exact_input_single(*, adapter, request):
        return {
            "amountOutRaw": int(int(request["amountInRaw"]) * 101 / 100),
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_quote_v4_exact_input_single)

    analysis = dex_surfaces._route_quote_analysis(
        route,
        price_map={"ETH": 1.0, "0X71178B": 1.0},
        chain_id=46630,
    )

    assert analysis["livePriceVerified"] is True
    assert analysis["priceEvidenceSource"] == "chain_live_quote"
    assert analysis["priceEvidenceMode"] == "live_verified"
    assert analysis["steps"][0]["trustedUsdPrice"] is True
    assert analysis["steps"][0]["outputUsd"] is not None
    assert analysis["executableCandidate"] is True
    assert analysis["chainSubmissionCandidate"] is True


def test_robinhood_testnet_v4_pool_id_only_routes_still_use_live_adapter(monkeypatch):
    from market.binance_market import dex_surfaces

    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    pool_xy = {
        "poolId": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "10000",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "AMD",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "tickSpacing": 60,
        "poolManager": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
    }
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "AMD", "address": token_y},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy],
    }

    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())

    def fake_quote_v4_exact_input_single(*, adapter, request):
        assert request["pool"] == pool_xy["poolId"]
        return {
            "amountOutRaw": str(int(int(request["amountInRaw"]) * 99 / 100)),
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_quote_v4_exact_input_single)

    quote = dex_surfaces._edge_quote(
        pool=pool_xy,
        from_symbol="ETH",
        to_symbol="AMD",
        from_address=eth,
        to_address=token_y,
        input_amount=1.0,
        input_usd=1000.0,
        price_map={"ETH": 1.0, "AMD": 1.0},
        chain_id=46630,
    )

    assert quote["executionTrust"] == "live_v4_adapter"
    assert quote["quoteSource"] == "uniswap_v4_live_adapter_quote"
    assert quote["outputAmount"] is not None


def test_robinhood_testnet_quote_routes_reuses_cached_overview(monkeypatch):
    from market.binance_market import dex_surfaces

    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    calls = {"overview": 0}

    overview_payload = {
        "mode": "dex_overview",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "summary": {"arbitragePathCount": 1},
        "decisionBasis": "robinhood_testnet_market_observation_closed_loop",
        "marketObservationSource": "computed",
        "routeFrameworkSource": "market_observation",
        "marketObservation": {"summary": {}, "chainSpecificObservationPairs": []},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "arbitragePaths": [
            {
                "symbolPair": "A -> B -> A",
                "overviewRoute": {
                    "kind": "bridge_pair",
                    "anchorBorrowSymbol": "A",
                    "bridgeSymbols": ["B"],
                    "pathSymbols": ["A", "B", "A"],
                    "pathText": "A -> B -> A",
                },
                "routeSymbols": ["A", "B", "A"],
                "routeAddresses": [],
                "routeTokenPath": [],
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": False,
                    "adapterRequired": False,
                    "livePriceVerified": False,
                    "priceEvidenceMode": "framework_reused_live_confirmation",
                    "priceEvidenceSource": "cached_framework_live_confirmation",
                    "profitable": False,
                    "executableCandidate": False,
                    "chainSubmissionCandidate": False,
                    "chainSubmissionPolicy": "live_framework_reuse",
                    "recommendation": {"level": "watch", "action": "watch", "reasons": []},
                },
                "quoteInputs": {"candidatePools": [], "routePools": [], "quoteRequests": []},
            }
        ],
    }

    def fake_overview_payload(**kwargs):
        calls["overview"] += 1
        return overview_payload

    monkeypatch.setattr(dex_surfaces, "dex_overview_payload", fake_overview_payload)
    try:
        first = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v4"], use_static_observation=True)
        second = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v4"], use_static_observation=True)
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)

    assert calls["overview"] == 1
    assert first["summary"]["routeCount"] == 1
    assert second["summary"]["routeCount"] == 1
    assert first["routes"][0]["symbolPair"] == "A -> B -> A"


def test_robinhood_testnet_overview_reuses_cached_market_observation(monkeypatch):
    from market.binance_market import dex_surfaces

    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    calls = {"observation": 0}

    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    cached_observation = {
        "mode": "dex_market_observation",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "marketObservationSource": "cached_surface_framework",
        "summary": {"observationSequenceCount": 1, "closedLoopRouteCount": 1},
        "chainSpecificObservationPairs": [],
        "chainSpecificObservationPools": [],
        "closedLoopRoutes": [
            {
                "kind": "bridge_closed_loop",
                "anchorSymbol": "USDC",
                "targetSymbol": "DAI",
                "closedLoop": True,
                "pathSymbols": ["USDC", "WETH", "DAI", "USDC"],
                "pathText": "USDC -> WETH -> DAI -> USDC",
                "routeTokenPath": [
                    {"symbol": "USDC", "address": usdc},
                    {"symbol": "WETH", "address": weth},
                    {"symbol": "DAI", "address": dai},
                    {"symbol": "USDC", "address": usdc},
                ],
                "routeAddresses": [usdc, weth, dai, usdc],
                "routePools": [
                    {"pool": "0x" + "a" * 40, "protocol": "uniswap_v4", "fee": 100, "routeSymbols": ["USDC", "WETH"], "routeAddresses": [usdc, weth]},
                    {"pool": "0x" + "b" * 40, "protocol": "uniswap_v4", "fee": 30, "routeSymbols": ["WETH", "DAI"], "routeAddresses": [weth, dai]},
                    {"pool": "0x" + "c" * 40, "protocol": "uniswap_v4", "fee": 5, "routeSymbols": ["DAI", "USDC"], "routeAddresses": [dai, usdc]},
                ],
                "routePairs": [
                    {"symbolPair": "USDC:WETH", "addressPair": f"{usdc}:{weth}", "preferredPools": [], "executionPools": []},
                    {"symbolPair": "WETH:DAI", "addressPair": f"{weth}:{dai}", "preferredPools": [], "executionPools": []},
                    {"symbolPair": "DAI:USDC", "addressPair": f"{dai}:{usdc}", "preferredPools": [], "executionPools": []},
                ],
            }
        ],
    }
    overview_cache_key = dex_surfaces._surface_cache_key(
        "dex_market_observation",
        chain_id=46630,
        protocols=["uniswap_v4"],
    )
    dex_surfaces._DEX_SURFACE_CACHE[overview_cache_key] = cached_observation

    def fake_market_observation_payload(**kwargs):
        calls["observation"] += 1
        return cached_observation

    monkeypatch.setattr(dex_surfaces, "dex_market_observation_payload", fake_market_observation_payload)
    monkeypatch.setattr(
        dex_surfaces,
        "dex_pool_candidates_payload",
        lambda chain_id=None, protocols=None: {
            "pairs": [],
            "summary": {},
            "dexSettlement": {"uniswapx": {"supported": False}},
            "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        },
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_path_candidates_from_market_observation",
        lambda observation, chain_id=None: [
            {
                "symbolPair": "USDC -> WETH -> DAI -> USDC",
                "routeSymbols": ["USDC", "WETH", "DAI", "USDC"],
                "routeAddresses": [usdc, weth, dai, usdc],
                "routeTokenPath": [
                    {"symbol": "USDC", "address": usdc},
                    {"symbol": "WETH", "address": weth},
                    {"symbol": "DAI", "address": dai},
                    {"symbol": "USDC", "address": usdc},
                ],
                "routePools": cached_observation["closedLoopRoutes"][0]["routePools"],
                "overviewRoute": {
                    "kind": "bridge_pair",
                    "anchorBorrowSymbol": "USDC",
                    "pathSymbols": ["USDC", "WETH", "DAI", "USDC"],
                    "pathText": "USDC -> WETH -> DAI -> USDC",
                },
            }
        ],
    )
    monkeypatch.setattr(
        dex_surfaces,
        "_route_with_overview_profit",
        lambda route, **kwargs: {
            **route,
            "overviewProfit": {
                "complete": True,
                "requiresLiveQuoter": False,
                "adapterRequired": False,
                "profitable": False,
                "executableCandidate": False,
                "chainSubmissionCandidate": False,
                "referenceNetProfitUsd": 1.0,
            },
        },
    )
    try:
        overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)

    assert calls["observation"] == 0
    assert overview["routeFrameworkSource"] == "market_observation"
    assert overview["marketObservationSource"] == "cached_surface_framework"
    assert overview["arbitragePaths"][0]["overviewRoute"]["pathText"] == "USDC -> WETH -> DAI -> USDC"


def test_dex_overview_payload_reuses_cached_framework_payload(monkeypatch):
    from market.binance_market import dex_surfaces

    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    calls = {"candidates": 0, "surface": 0}

    def fake_market_observation_surface_payload(**kwargs):
        calls["surface"] += 1
        return {
            "summary": {},
            "chainSpecificObservationPairs": [],
            "chainSpecificObservationPools": [],
            "closedLoopRoutes": [],
            "marketObservationSource": "computed",
            "marketObservationSourceStage": "computed",
        }

    def fake_candidate_payload(chain_id, protocols=None):
        calls["candidates"] += 1
        return {"pairs": [], "summary": {}, "dexSettlement": {}, "poolStaticTable": {}, "flashLoanLiquidity": {}}

    def fake_route_framework_context_from_observation(observation, pool_payload, *, chain_id, protocols):
        return {
            "overview_paths": [
                {
                    "overviewRoute": {
                        "pathText": "USDC -> WETH -> USDC",
                        "kind": "direct_anchor_pair",
                        "candidatePairSymbols": ["USDC:WETH"],
                    },
                    "routeSymbols": ["USDC", "WETH", "USDC"],
                }
            ],
            "watch_pairs": [],
            "anchor_symbols": ["USDC"],
            "closed_loop_anchor_symbols": ["USDC"],
            "observation_anchor_symbols": ["USDC"],
            "path_source": "market_observation",
            "used_market_observation": True,
        }

    try:
        monkeypatch.setattr(dex_surfaces, "dex_market_observation_surface_payload", fake_market_observation_surface_payload)
        monkeypatch.setattr(dex_surfaces, "_candidate_payload", fake_candidate_payload)
        monkeypatch.setattr(dex_surfaces, "_route_framework_context_from_observation", fake_route_framework_context_from_observation)
        monkeypatch.setattr(dex_surfaces, "_route_with_overview_profit", lambda route, **kwargs: route)

        first = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
        second = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v4"])
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)

    assert calls == {"candidates": 1, "surface": 1}
    assert first == second
    assert second["marketObservationSourceStage"] == "computed"


def test_bootstrap_dex_surface_dependencies_walks_backwards(monkeypatch):
    from web.data_routes import dex_surfaces as web_dex_surfaces

    calls = []

    monkeypatch.setattr(web_dex_surfaces, "dex_pool_candidates_payload", lambda **kwargs: calls.append("candidates") or {})
    monkeypatch.setattr(web_dex_surfaces, "dex_market_observation_surface_payload", lambda **kwargs: calls.append("surface") or {})
    monkeypatch.setattr(web_dex_surfaces, "dex_overview_payload", lambda **kwargs: calls.append("overview") or {})
    monkeypatch.setattr(web_dex_surfaces, "dex_quote_routes_payload", lambda **kwargs: calls.append("quotes") or {})

    web_dex_surfaces._bootstrap_dex_surface_dependencies(
        chain_id=46630,
        protocols=["uniswap_v4"],
        flow_name="dex_submission_review",
    )

    assert calls == ["candidates", "surface", "overview"]


def test_submission_review_framework_payload_exposes_static_framework_label(monkeypatch):
    from web.data_routes import dex_surfaces as web_dex_surfaces

    monkeypatch.setattr(
        web_dex_surfaces,
        "dex_overview_payload",
        lambda **kwargs: {
            "routeFrameworkSource": "market_observation",
            "marketObservationSource": "cached_snapshot",
            "marketObservationSourceStage": "cached_snapshot",
            "decisionBasis": "demo",
            "summary": {
                "arbitragePathCount": 2,
                "marketObservationSequenceCount": 1,
                "observationAnchorCount": 1,
                "candidatePathCount": 1,
                "overviewChainSubmissionCandidateCount": 1,
            },
            "arbitragePaths": [{"pathText": "A -> B -> A", "quoteStatus": "live_verified"}],
            "marketObservation": {"summary": {"observationSequenceCount": 1}},
        },
    )

    payload = web_dex_surfaces._submission_review_framework_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert payload["routeFrameworkSource"] == "market_observation"
    assert payload["routeFrameworkLabel"] == "静态框架"
    assert payload["marketObservationSource"] == "cached_snapshot"
    assert payload["marketObservationLabel"] == "静态框架"
    assert payload["marketObservationSourceStage"] == "cached_snapshot"
    assert payload["marketObservationSourceStageLabel"] == "缓存静态快照"
    assert payload["routeCount"] == 2
    assert payload["topRouteText"] == "A -> B -> A"


def test_dex_provenance_framework_label_helper_shares_static_mapping():
    from web.dex_provenance import build_price_evidence_payload, framework_label, price_evidence_label, price_evidence_mode_label

    assert framework_label("market_observation") == "静态框架"
    assert framework_label("cached_snapshot") == "静态框架"
    assert framework_label("candidate_pool_fallback") == "候选框架"
    assert framework_label("computed") == "链上重算"
    assert framework_label("custom_source") == "custom_source"
    assert price_evidence_label("static_reference_probe") == "静态探针"
    assert price_evidence_label("live_quoter_required") == "需要链上 Quoter"
    assert price_evidence_label("chain_live_quote") == "链上实时报价"
    assert price_evidence_label("cached_framework_live_confirmation") == "静态框架 + 实时复核"
    assert price_evidence_mode_label("reference_only") == "参考价"
    assert price_evidence_mode_label("live_verified") == "实时价格已验证"
    evidence_payload = build_price_evidence_payload(source="static_reference_probe", mode="reference_only")
    assert evidence_payload["priceEvidenceSourceLabel"] == "静态探针"
    assert evidence_payload["priceEvidenceModeLabel"] == "参考价"


def test_overview_profit_payload_exposes_price_evidence_labels():
    from market.binance_market.dex_surfaces import _overview_profit_from_analysis

    payload = _overview_profit_from_analysis(
        {
            "priceEvidenceSource": "static_reference_probe",
            "priceEvidenceMode": "reference_only",
        }
    )

    assert payload["priceEvidenceSourceLabel"] == "静态探针"
    assert payload["priceEvidenceModeLabel"] == "参考价"


def test_v4_pool_token_metadata_uses_live_decimals_fallback(monkeypatch):
    token_address = "0x" + "1" * 40
    monkeypatch.setattr(
        dex_surfaces,
        "_v4_token_metadata_cache",
        lambda pool_manager: {token_address.lower(): {"symbol": "TEST", "decimals": None}},
    )
    monkeypatch.setattr(dex_surfaces, "_live_v4_token_decimals", lambda address, chain_id=None: 12)

    metadata = dex_surfaces._v4_pool_token_metadata(
        {
            "poolManager": "0x" + "3" * 40,
            "token0": token_address,
            "token0Symbol": "TEST",
            "token1": "0x" + "2" * 40,
            "token1Symbol": "TEST2",
        },
        0,
        chain_id=46630,
    )

    assert metadata["decimals"] == 12
    assert metadata["source"] == "chain_live_erc20_decimals"
    assert metadata["metadataOk"] is True


def test_market_observation_source_stage_labels_are_distinct_from_framework_labels():
    from web.dex_provenance import market_observation_source_stage_label

    assert market_observation_source_stage_label("cached_snapshot") == "缓存静态快照"
    assert market_observation_source_stage_label("generated_live_snapshot") == "实时生成快照"
    assert market_observation_source_stage_label("refreshed_live_snapshot") == "实时刷新快照"
    assert market_observation_source_stage_label("computed") == "链上重算"


def test_static_framework_labels_stay_separate_from_live_price_evidence_labels():
    from web.dex_provenance import build_price_evidence_payload, build_route_framework_payload

    route_framework = build_route_framework_payload(
        route_framework_source="market_observation",
        market_observation_source="cached_snapshot",
        market_observation_source_stage="cached_snapshot",
        decision_basis="demo",
    )
    price_evidence = build_price_evidence_payload(source="live_quoter_required", mode="live_required")

    assert route_framework["routeFrameworkLabel"] == route_framework["marketObservationLabel"]
    assert route_framework["routeFrameworkLabel"] != price_evidence["priceEvidenceSourceLabel"]
    assert route_framework["marketObservationSourceStageLabel"] != price_evidence["priceEvidenceSourceLabel"]
    assert route_framework["routeFrameworkLabel"] != price_evidence["priceEvidenceModeLabel"]


def test_robinhood_chain_market_observation_flow_prefers_static_snapshot(monkeypatch):
    from core.chain_flow_dispatcher import ChainFlowRequest
    from intent_trade.chains.robinhood import dex_surfaces as robinhood_dex_surfaces

    calls = {"surface": 0, "overview": 0, "quotes": 0, "request": 0}

    req = ChainFlowRequest(
        flow_name="dex_overview",
        protocol="uniswap_v4",
        chain_id=46630,
        network="robinhood",
        global_chain_id="robinhood-46630",
        handler="intent_trade.chains.robinhood.dex_surfaces:dex_overview_flow",
        flow={"allowed_protocols": ["uniswap_v4"]},
        chain_config={},
    )

    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_market_observation_surface_payload",
        lambda **kwargs: calls.__setitem__("surface", calls["surface"] + 1) or {"mode": "dex_market_observation"},
    )
    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_overview_payload",
        lambda **kwargs: calls.__setitem__("overview", calls["overview"] + 1) or {"mode": "dex_overview"},
    )
    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_quote_routes_payload",
        lambda **kwargs: calls.__setitem__("quotes", calls["quotes"] + 1) or {"mode": "dex_quote_routes"},
    )
    monkeypatch.setattr(
        robinhood_dex_surfaces,
        "dex_quote_request_payload",
        lambda **kwargs: calls.__setitem__("request", calls["request"] + 1) or {"mode": "dex_quote_request"},
    )

    assert robinhood_dex_surfaces.market_observation_flow(request=req, protocols=["uniswap_v4"])["mode"] == "dex_market_observation"
    assert robinhood_dex_surfaces.dex_overview_flow(request=req, protocols=["uniswap_v4"])["mode"] == "dex_overview"
    assert robinhood_dex_surfaces.dex_quote_routes_flow(request=req, protocols=["uniswap_v4"])["mode"] == "dex_quote_routes"
    assert robinhood_dex_surfaces.dex_quote_request_flow(request=req, protocols=["uniswap_v4"], pair="A", venue="uniswap_v4")["mode"] == "dex_quote_request"
    assert calls == {"surface": 1, "overview": 1, "quotes": 1, "request": 1}


def test_dex_opportunity_scanner_warmup_uses_live_overview(monkeypatch):
    from runtime import dex_opportunity_scanner

    calls = {"overview": []}

    class FakeScanner:
        _lock = __import__("threading").Lock()
        _snapshots = {}

        def start(self, *args, **kwargs):
            return None

        def snapshot(self, *args, **kwargs):
            return {"snapshot": {}}

        def quote_payload_provider(self, *args, **kwargs):
            return {"summary": {"routeCount": 1}, "routes": [{"symbolPair": "A -> B -> A"}]}

        def trigger_context_provider(self, *args, **kwargs):
            return {"source": "test"}

    monkeypatch.setattr(dex_opportunity_scanner, "get_dex_opportunity_scanner", lambda: FakeScanner())
    monkeypatch.setattr(
        dex_opportunity_scanner,
        "dex_overview_payload",
        lambda **kwargs: calls["overview"].append(kwargs) or {"mode": "dex_overview"},
    )

    scanner = dex_opportunity_scanner.ensure_dex_opportunity_scanner_running(chain_id=46630, protocols=["uniswap_v4"])

    assert scanner is not None
    assert calls["overview"] and calls["overview"][0]["use_static_observation"] is False


def test_dex_market_observation_includes_bridge_closed_loop(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_usdc_dai = _pool("0x" + "b" * 40, "USDC", "DAI", usdc, dai, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    pool_weth_dai = _pool("0x" + "c" * 40, "WETH", "DAI", weth, dai, price_native="2500.0", token0_amount=400, token1_amount=1_000_000, tvl_usd=2_000_000)
    pair_reports = [
        {
            "symbolPair": "USDC:WETH",
            "tokenX": usdc,
            "tokenY": weth,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "WETH",
            "preferredPools": [pool_usdc_weth],
            "executionPools": [pool_usdc_weth],
            "strictQualityPools": [pool_usdc_weth],
        },
        {
            "symbolPair": "USDC:DAI",
            "tokenX": usdc,
            "tokenY": dai,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "DAI",
            "preferredPools": [pool_usdc_dai],
            "executionPools": [pool_usdc_dai],
            "strictQualityPools": [pool_usdc_dai],
        },
        {
            "symbolPair": "WETH:DAI",
            "tokenX": weth,
            "tokenY": dai,
            "tokenXSymbol": "WETH",
            "tokenYSymbol": "DAI",
            "preferredPools": [pool_weth_dai],
            "executionPools": [pool_weth_dai],
            "strictQualityPools": [pool_weth_dai],
        },
    ]
    payload = {
        "pairs": pair_reports,
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": build_anchor_candidate_groups(pair_reports, anchor_symbols=["USDC"]),
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert observation["summary"]["closedLoopRouteCount"] >= 1
    assert any(
        route["kind"] == "bridge_closed_loop"
        and route["anchorSymbol"] == "USDC"
        and route["closedLoop"] is True
        and route["pathSymbols"][0] == "USDC"
        and route["pathSymbols"][-1] == "USDC"
        and set(route["pathSymbols"][1:-1]) == {"WETH", "DAI"}
        for route in observation["closedLoopRoutes"]
    )


def test_dex_market_observation_surface_uses_static_snapshot_when_requested(monkeypatch):
    snapshot = {
        "exists": True,
        "path": "observation.json",
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "marketObservationSource": "generated_live_snapshot",
        "summary": {
            "observationAnchorCount": 2,
            "candidateObservationGroupCount": 1,
            "observationSequenceCount": 1,
            "closedLoopRouteCount": 1,
        },
        "observationAnchorSymbols": ["ETH", "USDC"],
        "borrowableSymbols": ["ETH", "USDC"],
        "candidateObservationGroups": [{"anchorSymbol": "ETH", "observationSequenceCount": 1, "closedLoopRouteCount": 1}],
        "observationSequences": [{"pathText": "ETH -> USDC -> ETH"}],
        "closedLoopRoutes": [{"pathText": "ETH -> USDC -> ETH"}],
        "chainSpecificObservationPairs": [],
        "chainSpecificObservationPools": [],
        "directPools": [],
        "directClosedLoopRoutes": [],
        "indirectRoutes": [],
        "unmatchedPools": [],
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630, "exists": True},
        "dexSettlement": {"uniswapx": {"supported": False}},
    }

    monkeypatch.setattr(dex_surfaces, "_cached_framework_surface_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(dex_surfaces, "load_market_observation_static", lambda **kwargs: snapshot)

    observation = dex_surfaces.dex_market_observation_surface_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    assert observation["marketObservationSource"] == "cached_snapshot"
    assert observation["marketObservationSourceStage"] == "cached_snapshot"
    assert observation["summary"]["closedLoopRouteCount"] == 1
    assert observation["observationAnchorSymbols"] == ["ETH", "USDC"]


def test_dex_market_observation_links_yc_to_zc_when_yz_pair_exists(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_weth_dai = _pool("0x" + "b" * 40, "WETH", "DAI", weth, dai, price_native="2500.0", token0_amount=400, token1_amount=1_000_000, tvl_usd=2_000_000)
    pool_dai_usdc = _pool("0x" + "c" * 40, "DAI", "USDC", dai, usdc, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    yc_row = dex_surfaces._route_step_row(
        pool_usdc_weth,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "WETH", "address": weth},
        direction="yc",
    )
    zc_row = dex_surfaces._route_step_row(
        pool_dai_usdc,
        from_token={"symbol": "DAI", "address": dai},
        to_token={"symbol": "USDC", "address": usdc},
        direction="zc",
    )
    payload = {
        "pairs": [
            {
                "symbolPair": "WETH:DAI",
                "tokenX": weth,
                "tokenY": dai,
                "tokenXSymbol": "WETH",
                "tokenYSymbol": "DAI",
                "preferredPools": [pool_weth_dai],
                "executionPools": [pool_weth_dai],
                "strictQualityPools": [pool_weth_dai],
            }
        ],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": [
            {
                "anchorSymbol": "USDC",
                "anchorAddresses": [usdc],
                "ycPools": [yc_row],
                "zcPools": [zc_row],
                "ycTokens": [{"symbol": "WETH", "address": weth, "poolCount": "1", "pools": [yc_row]}],
                "zcTokens": [{"symbol": "DAI", "address": dai, "poolCount": "1", "pools": [zc_row]}],
                "ycPoolCount": "1",
                "zcPoolCount": "1",
                "ycTokenCount": "1",
                "zcTokenCount": "1",
                "candidatePoolCount": "2",
            }
        ],
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert observation["summary"]["observationSequenceCount"] == 1
    sequence = observation["observationSequences"][0]
    assert sequence["kind"] == "anchor_closed_loop_token_bridge"
    assert sequence["pathSymbols"] == ["USDC", "WETH", "DAI", "USDC"]
    assert [row["direction"] for row in sequence["routePools"]] == ["yc", "bridge", "zc"]
    assert observation["closedLoopRoutes"][0]["source"] == "yc_zc_bridge_observation"
    group = observation["candidateObservationGroups"][0]
    assert group["observationReachable"] is True
    assert group["observationState"]["status"] == "reachable"

    overview = dex_surfaces.dex_overview_payload(chain_id=46630, protocols=["uniswap_v3"])
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert overview["decisionBasis"] == "robinhood_testnet_market_observation_closed_loop"
    assert overview["arbitragePaths"][0]["overviewRoute"]["pathText"] == "USDC -> WETH -> DAI -> USDC"
    assert quotes["summary"]["pathSource"] == "market_observation"
    assert quotes["routes"][0]["symbolPair"] == "USDC -> WETH -> DAI -> USDC"


def test_dex_market_observation_uses_v4_index_for_shared_yz_bridge(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_usdc_dai = _pool("0x" + "b" * 40, "USDC", "DAI", usdc, dai, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    for pool in (pool_usdc_weth, pool_usdc_dai):
        pool["dex"] = "uniswap_v4"
        pool["protocol"] = "uniswap_v4"
    shared_weth = dex_surfaces._route_step_row(
        pool_usdc_weth,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "WETH", "address": weth},
        direction="shared",
    )
    shared_dai = dex_surfaces._route_step_row(
        pool_usdc_dai,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "DAI", "address": dai},
        direction="shared",
    )
    payload = {
        "pairs": [],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": [
            {
                "anchorSymbol": "USDC",
                "anchorAddresses": [usdc],
                "ycPools": [],
                "zcPools": [],
                "sharedPools": [shared_weth, shared_dai],
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "ycTokens": [],
                "zcTokens": [],
                "sharedTokens": [
                    {"symbol": "WETH", "address": weth, "poolCount": "1", "pools": [shared_weth]},
                    {"symbol": "DAI", "address": dai, "poolCount": "1", "pools": [shared_dai]},
                ],
                "ycTokensRef": "sharedTokens",
                "zcTokensRef": "sharedTokens",
                "ycZcSameSet": True,
                "ycPoolCount": "2",
                "zcPoolCount": "2",
                "ycTokenCount": "2",
                "zcTokenCount": "2",
                "candidatePoolCount": "2",
            }
        ],
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)
    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                {
                    "poolId": "0x" + "c" * 64,
                    "protocol": "uniswap_v4",
                    "token0": weth,
                    "token1": dai,
                    "token0Symbol": "0x222222",
                    "token1Symbol": "0x333333",
                    "fee": 3000,
                    "poolKeyDecoded": True,
                }
            ],
        }

    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
        lambda anchor_groups, chain_id=None, protocols=None: robinhood_testnet_market_observation.robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
            anchor_groups,
            chain_id=chain_id,
            protocols=protocols,
            pool_index_loader=load_v4_pool_index,
        ),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "robinhood_testnet_v4_fund_anchor_pair_observation",
        lambda chain_id=None, protocols=None: {"enabled": True, "status": "test_disabled", "observationSequences": []},
    )

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert observation["summary"]["bridgePoolIndexMatchedPairCount"] == 1
    assert observation["summary"]["observationSequenceCount"] >= 1
    assert any(sequence["pathSymbols"] == ["USDC", "WETH", "DAI", "USDC"] for sequence in observation["observationSequences"])
    bridge_pool = observation["observationSequences"][0]["bridgePool"]
    assert bridge_pool["protocol"] == "uniswap_v4"
    assert bridge_pool["source"] == "v4_pool_index_bridge_pool"


def test_robinhood_testnet_v4_non_eth_pair_first_links_native_fund_to_bridge_pair():
    zero = "0x" + "0" * 40
    token_y = "0x" + "4" * 40
    token_z = "0x" + "5" * 40

    def pool(pool_id, token0, token1, symbol0, symbol1):
        return {
            "poolId": pool_id,
            "token0": token0,
            "token1": token1,
            "token0Symbol": symbol0,
            "token1Symbol": symbol1,
            "fee": 3000,
            "poolKeyDecoded": True,
        }

    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                pool("0x" + "a" * 64, zero, token_y, "ETH", "AAA"),
                pool("0x" + "b" * 64, token_y, token_z, "AAA", "BBB"),
                pool("0x" + "c" * 64, zero, token_z, "ETH", "BBB"),
            ],
        }

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pool_manager": "0x" + "9" * 40,
            "payload": {
                "poolManager": "0x" + "9" * 40,
                "nativePoolManagerBalanceWei": "2000000000000000000",
            },
            "pools": [],
            "pool_keys": [],
        }

    observation = robinhood_testnet_market_observation.robinhood_testnet_v4_non_eth_pair_first_observation(
        chain_id=46630,
        protocols=["uniswap_v4"],
        pool_index_loader=load_v4_pool_index,
        pool_liquidity_loader=load_v4_pool_liquidity,
    )

    assert observation["nonEthPairCount"] == 1
    assert observation["associatedNonEthPairCount"] == 1
    assert observation["associatedObservationPairCount"] == 1
    assert observation["fundAnchorCandidatePairCount"] == 3
    assert observation["associatedObservationCandidatePairCount"] == 3
    assert observation["associatedObservationPoolCount"] == 3
    assert observation["associatedObservationTokenCount"] == 3
    assert observation["associatedObservationPairs"][0]["symbolPair"] == "AAA:BBB"
    assert observation["associatedObservationPairs"][0]["poolCount"] == 1
    assert observation["associatedObservationPairs"][0]["directSwapRequired"] is True
    assert observation["associatedObservationPairs"][0]["directSwapPoolCount"] == 1
    assert observation["associatedObservationPairs"][0]["directSwapPools"][0]["role"] == "direct_y_z_swap_pool"
    assert observation["associatedObservationPairs"][0]["closedLoopReady"] is True
    assert set(observation["associatedObservationPairs"][0]["closedLoopRoutePreviews"]) == {
        "ETH -> AAA -> BBB -> ETH",
        "ETH -> BBB -> AAA -> ETH",
    }
    assert {
        tuple(step["direction"] for step in route["routePools"])
        for route in observation["associatedObservationPairs"][0]["routeEvidence"]
    } == {("yc", "bridge", "zc")}
    assert observation["routeCount"] == 2
    assert {sequence["pathText"] for sequence in observation["observationSequences"]} == {
        "ETH -> AAA -> BBB -> ETH",
        "ETH -> BBB -> AAA -> ETH",
    }
    assert all(sequence["kind"] == "robinhood_testnet_v4_fund_anchor_pair_observation" for sequence in observation["observationSequences"])


def test_robinhood_testnet_v4_associated_non_eth_pool_is_observed_without_same_anchor_loop():
    fund_a = "0x" + "1" * 40
    fund_b = "0x" + "2" * 40
    token_y = "0x" + "4" * 40
    token_z = "0x" + "5" * 40

    def pool(pool_id, token0, token1, symbol0, symbol1):
        return {
            "poolId": pool_id,
            "token0": token0,
            "token1": token1,
            "token0Symbol": symbol0,
            "token1Symbol": symbol1,
            "fee": 3000,
            "poolKeyDecoded": True,
        }

    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                pool("0x" + "a" * 64, fund_a, token_y, "FUND_A", "AAA"),
                pool("0x" + "b" * 64, token_y, token_z, "AAA", "BBB"),
                pool("0x" + "c" * 64, token_z, fund_b, "BBB", "FUND_B"),
            ],
        }

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pool_manager": "0x" + "9" * 40,
            "payload": {
                "poolManager": "0x" + "9" * 40,
                "flashAccountingSources": [
                    {
                        "address": fund_a,
                        "symbol": "FUND_A",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                    {
                        "address": fund_b,
                        "symbol": "FUND_B",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                ],
            },
            "pools": [],
            "pool_keys": [],
        }

    observation = robinhood_testnet_market_observation.robinhood_testnet_v4_non_eth_pair_first_observation(
        chain_id=46630,
        protocols=["uniswap_v4"],
        pool_index_loader=load_v4_pool_index,
        pool_liquidity_loader=load_v4_pool_liquidity,
    )

    assert observation["associatedObservationCandidatePairCount"] == 3
    assert observation["associatedObservationPairCount"] == 0
    assert observation["associatedObservationPoolCount"] == 3
    assert observation["associatedObservationTokenCount"] == 4
    assert observation["associatedObservationPairs"] == []
    assert observation["routeCount"] == 0
    bridge_pool = next(
        pool
        for pool in observation["associatedObservationPools"]
        if pool["symbolPair"] == "AAA:BBB"
    )
    assert bridge_pool["observationPool"] is True
    assert bridge_pool["closedLoopReady"] is False
    assert bridge_pool["token0AssociatedFunds"][0]["symbol"] == "FUND_A"
    assert bridge_pool["token1AssociatedFunds"][0]["symbol"] == "FUND_B"


def test_robinhood_testnet_v4_fund_anchor_pair_allows_yz_to_include_other_fund_asset():
    fund_a = "0x" + "1" * 40
    fund_b = "0x" + "2" * 40
    token_y = "0x" + "4" * 40

    def pool(pool_id, token0, token1, symbol0, symbol1):
        return {
            "poolId": pool_id,
            "token0": token0,
            "token1": token1,
            "token0Symbol": symbol0,
            "token1Symbol": symbol1,
            "fee": 3000,
            "poolKeyDecoded": True,
        }

    def load_v4_pool_index():
        return {
            "ok": True,
            "status": "v4_pool_index_available",
            "source": "test-index",
            "block_number": 123,
            "pools": [
                pool("0x" + "a" * 64, fund_a, fund_b, "FUND_A", "FUND_B"),
                pool("0x" + "b" * 64, fund_b, token_y, "FUND_B", "AAA"),
                pool("0x" + "c" * 64, token_y, fund_a, "AAA", "FUND_A"),
            ],
        }

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "status": "v4_pool_liquidity_cache_available",
            "source": "test-liquidity",
            "pool_manager": "0x" + "9" * 40,
            "payload": {
                "poolManager": "0x" + "9" * 40,
                "flashAccountingSources": [
                    {
                        "address": fund_a,
                        "symbol": "FUND_A",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                    {
                        "address": fund_b,
                        "symbol": "FUND_B",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "1000000000000000000",
                        "poolManagerBalance": 1,
                    },
                ],
            },
            "pools": [],
            "pool_keys": [],
        }

    observation = robinhood_testnet_market_observation.robinhood_testnet_v4_fund_anchor_pair_observation(
        chain_id=46630,
        protocols=["uniswap_v4"],
        pool_index_loader=load_v4_pool_index,
        pool_liquidity_loader=load_v4_pool_liquidity,
    )

    assert observation["associatedObservationPairCount"] == 2
    assert {pair["symbolPair"] for pair in observation["associatedObservationPairs"]} == {
        "FUND_B:AAA",
        "AAA:FUND_A",
    }
    assert {sequence["pathText"] for sequence in observation["observationSequences"]} == {
        "FUND_A -> FUND_B -> AAA -> FUND_A",
        "FUND_A -> AAA -> FUND_B -> FUND_A",
        "FUND_B -> AAA -> FUND_A -> FUND_B",
        "FUND_B -> FUND_A -> AAA -> FUND_B",
    }
    assert all(
        sequence["anchorAddress"] not in {
            sequence["routePools"][1]["routeAddresses"][0],
            sequence["routePools"][1]["routeAddresses"][1],
        }
        for sequence in observation["observationSequences"]
    )


def test_dex_market_observation_keeps_class_when_no_yz_pair_exists(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    dai = "0x" + "3" * 40
    pool_usdc_weth = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_dai_usdc = _pool("0x" + "c" * 40, "DAI", "USDC", dai, usdc, price_native="1.0", token0_amount=1_000_000, token1_amount=1_000_000, tvl_usd=2_000_000)
    yc_row = dex_surfaces._route_step_row(
        pool_usdc_weth,
        from_token={"symbol": "USDC", "address": usdc},
        to_token={"symbol": "WETH", "address": weth},
        direction="yc",
    )
    zc_row = dex_surfaces._route_step_row(
        pool_dai_usdc,
        from_token={"symbol": "DAI", "address": dai},
        to_token={"symbol": "USDC", "address": usdc},
        direction="zc",
    )
    payload = {
        "pairs": [],
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": [
            {
                "anchorSymbol": "USDC",
                "anchorAddresses": [usdc],
                "ycPools": [yc_row],
                "zcPools": [zc_row],
                "ycTokens": [{"symbol": "WETH", "address": weth, "poolCount": "1", "pools": [yc_row]}],
                "zcTokens": [{"symbol": "DAI", "address": dai, "poolCount": "1", "pools": [zc_row]}],
                "ycPoolCount": "1",
                "zcPoolCount": "1",
                "ycTokenCount": "1",
                "zcTokenCount": "1",
                "candidatePoolCount": "2",
            }
        ],
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert observation["summary"]["candidateObservationGroupCount"] == 1
    assert observation["summary"]["observationSequenceCount"] == 0
    group = observation["candidateObservationGroups"][0]
    assert group["anchorSymbol"] == "USDC"
    assert group["observationReachable"] is False
    assert group["observationState"]["status"] == "no_yz_pair"
    assert "Y-Z trading pair pool" in group["observationEmptyReason"]


def test_dex_market_observation_filters_same_symbol_reentry_without_profit(monkeypatch):
    usdc = "0x" + "1" * 40
    weth = "0x" + "2" * 40
    pool_a = _pool("0x" + "a" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pool_b = _pool("0x" + "b" * 40, "USDC", "WETH", usdc, weth, price_native="0.0004", token0_amount=1_000_000, token1_amount=400, tvl_usd=2_000_000)
    pair_reports = [
        {
            "symbolPair": "USDC:WETH",
            "tokenX": usdc,
            "tokenY": weth,
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "WETH",
            "preferredPools": [pool_a, pool_b],
            "executionPools": [pool_a, pool_b],
            "strictQualityPools": [pool_a, pool_b],
        }
    ]
    payload = {
        "pairs": pair_reports,
        "summary": {},
        "dexSettlement": {"uniswapx": {"supported": False}},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "anchorGroups": build_anchor_candidate_groups(pair_reports, anchor_symbols=["USDC"]),
    }
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: payload)

    observation = dex_surfaces.dex_market_observation_payload(chain_id=46630, protocols=["uniswap_v3"])

    assert all(route["kind"] != "direct_closed_pair" for route in observation["closedLoopRoutes"])
    assert all(sequence["kind"] != "anchor_closed_loop_reentry" for sequence in observation["observationSequences"])


def test_v4_sqrt_price_reference_quote_completes_static_measurement():
    usdt = "0x" + "1" * 40
    eth = "0x" + "0" * 40
    pool = {
        "pool": "0x" + "2" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "0",
        "token0": usdt,
        "token1": eth,
        "token0Symbol": "USDT",
        "token1Symbol": "ETH",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "sqrtPriceX96": str(2**96),
        "balances": {"token0": 1_000_000, "token1": 1_000_000, "tvlUsd": None},
    }
    route = {
        "routeTokenPath": [
            {"symbol": "USDT", "address": usdt},
            {"symbol": "ETH", "address": eth},
            {"symbol": "USDT", "address": usdt},
        ],
        "executionPools": [pool],
    }

    analysis = dex_surfaces._route_quote_analysis(route, price_map={"USDT": 1.0, "ETH": 1.0})

    assert analysis["complete"] is True
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is True
    assert analysis["finalOutputUsd"] is None
    assert analysis["referenceFinalOutputUsd"] == 1000.0
    assert analysis["referenceGrossProfitUsd"] == 0.0
    assert analysis["referenceNetProfitUsd"] == -1.5
    assert len(analysis["steps"]) == 2
    assert all(step["referenceOutputUsd"] == 1000.0 for step in analysis["steps"])
    assert all(step["quoteSource"] == "uniswap_v4_sdk_adapter_required" for step in analysis["steps"])


def test_v4_reference_route_without_usd_anchor_does_not_create_fake_profit():
    eth = "0x" + "0" * 40
    token_y = "0x71178bac73cbeb415514eb542a8995b82669778d"
    token_z = "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e"
    pool_xy = {
        "pool": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "10000",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "0X71178B",
        "sqrtPriceX96": "560227709747861399187319382274",
    }
    pool_yz = {
        "pool": "0x" + "b" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token_y,
        "token1": token_z,
        "token0Symbol": "0X71178B",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": "112045541949572279837463876454",
    }
    pool_zx = {
        "pool": "0x" + "c" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_z,
        "token0Symbol": "ETH",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": str(2**96),
    }
    pairs = [
        {"symbolPair": "ETH:0X71178B", "preferredPools": [pool_xy]},
        {"symbolPair": "0X71178B:0XC9F9C8", "preferredPools": [pool_yz]},
        {"symbolPair": "ETH:0XC9F9C8", "preferredPools": [pool_zx]},
    ]
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "0XC9F9C8", "address": token_z},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy, pool_yz, pool_zx],
    }

    price_map = dex_surfaces._price_map_from_pairs(pairs)
    analysis = dex_surfaces._route_quote_analysis(route, price_map=price_map)

    assert "ETH" not in price_map
    assert analysis["complete"] is False
    assert analysis["stepCount"] == 1
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is True
    assert analysis["trustedUsdReference"] is False
    assert analysis["tokenDecimalsTrusted"] is True
    assert analysis["profitable"] is False
    assert analysis["referenceFinalOutputUsd"] is None
    assert analysis["referenceNetProfitUsd"] is None
    assert analysis["referenceFinalOutputAmount"] is None
    assert analysis["referenceNetReturnBps"] is None
    assert analysis["tokenBasisReferenceProfitable"] is False
    assert analysis["tokenBasisProfitable"] is False
    assert analysis["chainSubmissionCandidate"] is False
    assert analysis["referenceReturnTrusted"] is False
    assert analysis["recommendation"]["level"] == "blocked"
    assert analysis["recommendation"]["action"] == "等待真实报价"
    assert "reference_price_step_requires_live_quoter" in analysis["warnings"]
    assert "route_truncated_at_hop_1_requires_live_quoter" in analysis["warnings"]




def test_robinhood_testnet_requires_live_quoter_before_submission_candidate(monkeypatch):
    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    token_z = "0x" + "3" * 40
    pool_xy = {
        "pool": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "0X71178B",
        "sqrtPriceX96": str(2**96),
    }
    pool_yz = {
        "pool": "0x" + "b" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token_y,
        "token1": token_z,
        "token0Symbol": "0X71178B",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": "112045541949572279837463876454",
    }
    pool_zx = {
        "pool": "0x" + "c" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_z,
        "token0Symbol": "ETH",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": str(2**96),
    }
    pairs = [
        {"symbolPair": "ETH:0X71178B", "preferredPools": [pool_xy]},
        {"symbolPair": "0X71178B:0XC9F9C8", "preferredPools": [pool_yz]},
        {"symbolPair": "ETH:0XC9F9C8", "preferredPools": [pool_zx]},
    ]
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "0XC9F9C8", "address": token_z},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy, pool_yz, pool_zx],
    }

    price_map = dex_surfaces._price_map_from_pairs(pairs)
    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())

    def fake_quote_v4_exact_input_single(*, adapter, request):
        return {
            "amountOutRaw": str(int(int(request["amountInRaw"]) * 10005 / 10000)),
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_quote_v4_exact_input_single)
    analysis = dex_surfaces._route_quote_analysis(route, price_map=price_map, chain_id=46630)

    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is True
    assert analysis["chainSubmissionCandidate"] is False
    assert analysis["executableCandidate"] is False
    assert analysis["tokenBasisReferenceProfitable"] is False


def test_robinhood_testnet_zero_output_hop_truncates_route_with_explicit_zero_quote(monkeypatch):
    eth = "0x" + "0" * 40
    token_y = "0x" + "2" * 40
    token_z = "0x" + "3" * 40
    pool_xy = {
        "pool": "0x" + "a" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "10000",
        "token0": eth,
        "token1": token_y,
        "token0Symbol": "ETH",
        "token1Symbol": "0X71178B",
        "sqrtPriceX96": str(2**96),
    }
    pool_yz = {
        "pool": "0x" + "b" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "3000",
        "token0": token_y,
        "token1": token_z,
        "token0Symbol": "0X71178B",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": "112045541949572279837463876454",
    }
    pool_zx = {
        "pool": "0x" + "c" * 64,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": "500",
        "token0": eth,
        "token1": token_z,
        "token0Symbol": "ETH",
        "token1Symbol": "0XC9F9C8",
        "sqrtPriceX96": str(2**96),
    }
    route = {
        "routeTokenPath": [
            {"symbol": "ETH", "address": eth},
            {"symbol": "0X71178B", "address": token_y},
            {"symbol": "0XC9F9C8", "address": token_z},
            {"symbol": "ETH", "address": eth},
        ],
        "executionPools": [pool_xy, pool_yz, pool_zx],
    }

    monkeypatch.setattr(dex_surfaces, "load_v4_sdk_adapter", lambda **kwargs: object())

    def fake_zero_quote(*, adapter, request):
        return {
            "amountOutRaw": "0",
            "quoteMode": request.get("quoteMode"),
            "sdkAdapter": "mock",
            "quoteAdapter": "mock",
        }

    monkeypatch.setattr(dex_surfaces, "quote_v4_exact_input_single", fake_zero_quote)
    analysis = dex_surfaces._route_quote_analysis(route, price_map={}, chain_id=46630)

    assert analysis["complete"] is False
    assert analysis["stepCount"] == 1
    assert analysis["requiresLiveQuoter"] is True
    assert analysis["adapterRequired"] is True
    assert analysis["chainSubmissionCandidate"] is False
    assert analysis["executableCandidate"] is False
    assert analysis["referenceFinalOutputUsd"] is None
    assert analysis["referenceNetProfitUsd"] is None
    assert analysis["tokenBasisReferenceProfitable"] is False
    assert any(
        warning in analysis["warnings"]
        for warning in ["robinhood_testnet_live_price_only_enforced", "reference_price_step_requires_live_quoter"]
    )

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
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "WETH", "flash_borrowable": True}],
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
                    "sources": [{"protocol": "morpho_blue", "token_symbol": "WETH", "flash_borrowable": True}],
                }
            },
        },
    )

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=4663)
    vlad_routes = [route for route in quotes["routes"] if route["symbolPair"] == "WETH -> VLAD -> WETH"]

    assert len(vlad_routes) == 2
    assert {route["routeAddresses"][1] for route in vlad_routes} == {vlad_a, vlad_b}
    for route in vlad_routes:
        steps = route["quoteAnalysis"]["steps"]
        assert steps[0]["tokenOut"] == steps[1]["tokenIn"]
        assert route["routeTokenPath"][1]["address"] == steps[0]["tokenOut"]
        assert route["routeTokenPath"][1]["address"] == steps[1]["tokenIn"]


def test_dex_pool_candidates_returns_chain_owned_empty_when_local_json_missing(monkeypatch):
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )

    payload = pool_candidates.dex_pool_candidates_payload(chain_id=137)

    assert payload["poolStaticTable"]["chainId"] == 137
    assert payload["poolStaticTable"]["exists"] is False
    assert payload["poolStaticTable"]["network"] == "polygon"
    assert payload["fallback_reason"] == "dex_pool_candidates_local_json_missing"
    assert payload["pairs"] == []


def test_dex_pool_candidates_returns_empty_for_unregistered_chain(monkeypatch):
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )

    payload = pool_candidates.dex_pool_candidates_payload(chain_id=999999)

    assert payload["poolStaticTable"]["chainId"] == 999999
    assert payload["poolStaticTable"]["exists"] is False
    assert payload["fallback_reason"] == "dex_pool_static_table_not_configured_for_chain"
    assert payload["pairs"] == []


def test_dex_pool_candidates_uses_candidate_pool_cache_and_full_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_pool_cache_store, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(pool_candidates, "_candidate_pool_source_available", lambda chain_id: True)

    pool = {
        "pool": "0x" + "1" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "pass",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "WETH",
        "balances": {"token0": 10, "token1": 10, "tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    pair_report = {
        "symbolPair": "USDC:WETH",
        "tokenX": pool["token0"],
        "tokenY": pool["token1"],
        "tokenXSymbol": pool["token0Symbol"],
        "tokenYSymbol": pool["token1Symbol"],
        "preferredPools": [pool],
        "strictQualityPools": [pool],
        "executionPools": [pool],
        "rejectedPools": [],
        "allPools": [pool],
        "hasStrictQualityPools": True,
        "hasPreferredPools": True,
        "allPoolCount": "1",
        "strictQualityPoolCount": "1",
        "preferredPoolCount": "1",
        "executionPoolCount": "1",
        "rejectedPoolCount": "0",
        "rejectReasonCounts": {},
        "classification": {"stableSymbols": ["USDC"], "nonStableSymbols": ["WETH"]},
    }
    source_payload = {
        "mode": "dex_pool_candidates",
        "source": "chain_flow",
        "generatedAt": "2026-08-28T00:00:00+00:00",
        "poolStaticTable": {"path": "source.json", "exists": True, "network": "bnb", "chainId": 56},
        "filters": {},
        "summary": {"pairTableCount": "1", "pairsWithStrictQualityPools": "1", "pairsWithPreferredPools": "1"},
        "pairs": [pair_report],
        "anchorGroups": build_anchor_candidate_groups([pair_report]),
    }
    calls = {"count": 0}

    def fake_dispatch_chain_flow(**kwargs):
        import json

        calls["count"] += 1
        return json.loads(json.dumps(source_payload))

    monkeypatch.setattr(pool_candidates, "dispatch_chain_flow", fake_dispatch_chain_flow)

    first = pool_candidates.dex_pool_candidates_payload(chain_id=56)
    cache_paths = candidate_pool_cache_store.candidate_pool_cache_paths(network="bnb", chain_id=56, cache_root=tmp_path)
    second = pool_candidates.dex_pool_candidates_payload(chain_id=56)
    refreshed = pool_candidates.dex_pool_candidates_payload(chain_id=56, refresh=True)
    full = pool_candidates.dex_pool_candidates_payload(chain_id=56, refresh=True, force_full=True)

    assert calls["count"] == 3
    assert cache_paths["snapshot"].exists()
    assert cache_paths["manifest"].exists()
    assert cache_paths["shards"].exists()
    assert (cache_paths["shards"] / "usdc.json").exists()
    assert first["poolStaticTable"]["path"] == str(cache_paths["snapshot"])
    assert second["poolStaticTable"]["path"] == str(cache_paths["snapshot"])
    assert refreshed["poolStaticTable"]["path"] == str(cache_paths["snapshot"])
    assert full["candidatePoolCache"]["forceFull"] is True


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
