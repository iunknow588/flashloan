from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from web.data_routes import dex_surface_helpers
from web.data_routes import dex_surface_routes_quotes
from dex.runtime.dex_opportunity_helpers import _merge_onchain_evidence_into_route


def _attempt(route_symbols, *, created_at, status):
    return {
        "chain_id": 46630,
        "created_at": created_at,
        "observed_at": created_at,
        "route_path": route_symbols,
        "quote": {
            "live_onchain_evidence": {
                "status": status,
                "executionTrust": status,
                "blockNumber": 1,
            }
        },
    }


def test_route_dedup_key_prefers_addresses_over_symbols():
    route_a = {
        "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "routeAddresses": [
            "0x7943e237c7F95DA44E0301572D358911207852Fa",
            "0x0000000000000000000000000000000000000000",
            "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
            "0x7943e237c7F95DA44E0301572D358911207852Fa",
        ],
    }
    route_b = {
        "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "routeAddresses": [
            "0x33e4191705c386532ba27cBF171Db86919200B94",
            "0x0000000000000000000000000000000000000000",
            "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
            "0x33e4191705c386532ba27cBF171Db86919200B94",
        ],
    }

    assert dex_surface_helpers._route_dedup_key(route_a) != dex_surface_helpers._route_dedup_key(route_b)
    assert dex_surface_helpers._route_dedup_key(route_a) == dex_surface_helpers._route_dedup_key(dict(route_a))


def test_zero_address_filtering_rejects_native_placeholder_routes():
    assert dex_surface_routes_quotes._is_zero_address("0x0000000000000000000000000000000000000000")
    assert dex_surface_routes_quotes._is_zero_address("0x0")
    assert not dex_surface_routes_quotes._is_zero_address("0x7943e237c7F95DA44E0301572D358911207852Fa")

    zero_route = {
        "routeSymbols": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "routeAddresses": [
            "0x7943e237c7F95DA44E0301572D358911207852Fa",
            "0x0000000000000000000000000000000000000000",
            "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
            "0x7943e237c7F95DA44E0301572D358911207852Fa",
        ],
    }
    clean_route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "routeAddresses": [
            "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E",
            "0x28cc9a0b94aD34A3A0CB2734c8d58aD95862C6c4",
            "0xB169FE96425783D7BA7a9F905fe8dE5f80E3Fdc8",
            "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E",
        ],
    }
    assert dex_surface_routes_quotes._route_has_zero_address(zero_route) is True
    assert dex_surface_routes_quotes._route_has_zero_address(clean_route) is False


def test_merge_recent_attempt_evidence_ignores_stale_history(monkeypatch):
    now = datetime.now(timezone.utc)
    old = _attempt(
        ["TZ", "TB", "TA", "TZ"],
        created_at=(now - timedelta(seconds=7200)).isoformat(),
        status="live_verified",
    )
    routes = [
        {
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "quoteAnalysis": {"requiresLiveQuoter": True},
        }
    ]
    monkeypatch.setattr(
        dex_surface_helpers,
        "cow_execution_attempt_log_path",
        lambda **kwargs: Path("unused/attempts.jsonl"),
    )
    monkeypatch.setattr(
        dex_surface_helpers,
        "load_recent_cow_execution_attempts_jsonl",
        lambda *args, **kwargs: [old],
    )

    dex_surface_helpers._merge_recent_attempt_evidence_into_routes(
        routes,
        chain_id=46630,
        max_age_seconds=600,
    )

    assert "live_onchain_evidence" not in routes[0]


def test_merge_recent_attempt_evidence_keeps_fresh_history(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = _attempt(
        ["TZ", "TB", "TA", "TZ"],
        created_at=(now - timedelta(seconds=30)).isoformat(),
        status="not_enough_liquidity",
    )
    routes = [
        {
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "quoteAnalysis": {"requiresLiveQuoter": True},
        }
    ]
    monkeypatch.setattr(
        dex_surface_helpers,
        "cow_execution_attempt_log_path",
        lambda **kwargs: Path("unused/attempts.jsonl"),
    )
    monkeypatch.setattr(
        dex_surface_helpers,
        "load_recent_cow_execution_attempts_jsonl",
        lambda *args, **kwargs: [fresh],
    )

    dex_surface_helpers._merge_recent_attempt_evidence_into_routes(
        routes,
        chain_id=46630,
        max_age_seconds=600,
    )

    assert routes[0]["live_onchain_evidence"]["status"] == "not_enough_liquidity"


def test_merge_recent_attempt_evidence_replaces_static_manifest_with_live_verified(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = {
        "chain_id": 4663,
        "created_at": (now - timedelta(seconds=30)).isoformat(),
        "observed_at": (now - timedelta(seconds=30)).isoformat(),
        "route_path": ["TZ", "TB", "TA", "TZ"],
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "executionTrust": "live_quoter_verified_readonly",
                "bestAmountConfirmed": True,
                "bestAmountMissing": False,
                "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                "hops": [
                    {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "status": "live_verified"},
                    {"hop": 2, "fromSymbol": "TB", "toSymbol": "TA", "status": "live_verified"},
                    {"hop": 3, "fromSymbol": "TA", "toSymbol": "TZ", "status": "live_verified"},
                ],
                "costModel": {
                    "borrowInputSymbol": "TZ",
                    "borrowInputAmount": 16384.0,
                    "selectedBorrowInputAmount": 16384.0,
                    "netProfitAmount": 92565.84103581037,
                    "bestAmountConfirmed": True,
                    "bestAmountMissing": False,
                },
            }
        },
    }
    routes = [
        {
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "live_onchain_evidence": {
                "status": "static_reference_pending_live_review",
                "quoteBlockSource": "mainnet_v4_manifest_initial_probe",
                "bestAmountConfirmed": False,
                "bestAmountMissing": True,
                "hops": [],
                "costModel": {
                    "borrowInputAmount": 1.0,
                    "selectedBorrowInputAmount": 1.0,
                    "bestAmountConfirmed": False,
                    "bestAmountMissing": True,
                },
            },
        }
    ]
    monkeypatch.setattr(
        dex_surface_helpers,
        "cow_execution_attempt_log_path",
        lambda **kwargs: Path("unused/attempts.jsonl"),
    )
    monkeypatch.setattr(
        dex_surface_helpers,
        "load_recent_cow_execution_attempts_jsonl",
        lambda *args, **kwargs: [fresh],
    )

    dex_surface_helpers._merge_recent_attempt_evidence_into_routes(
        routes,
        chain_id=4663,
        max_age_seconds=600,
    )

    evidence = routes[0]["live_onchain_evidence"]
    assert evidence["status"] == "live_verified"
    assert evidence["bestAmountConfirmed"] is True
    assert evidence["costModel"]["selectedBorrowInputAmount"] == 16384.0
    assert evidence["costModel"]["netProfitAmount"] == 92565.84103581037


def test_evict_stale_evidence_clears_old_live_evidence():
    routes = [
        {
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "quoteStatus": "live_verified",
            "live_onchain_evidence": {
                "status": "live_verified",
                "executionTrust": "live_v4_adapter",
            },
            "quoteAnalysis": {
                "live_onchain_evidence": {
                    "status": "live_verified",
                    "executionTrust": "live_v4_adapter",
                },
                "onchainEvidenceStatus": "live_verified",
                "requiresLiveQuoter": False,
            },
        }
    ]

    dex_surface_helpers.evict_stale_evidence_from_routes(routes)

    assert routes[0]["quoteStatus"] == "stale_refresh_pending"
    assert routes[0]["onchainEvidenceStatus"] == "stale_refresh_pending"
    assert "live_onchain_evidence" not in routes[0]
    assert "live_onchain_evidence" not in routes[0]["quoteAnalysis"]
    assert routes[0]["quoteAnalysis"]["onchainEvidenceStatus"] == "stale_refresh_pending"
    assert routes[0]["quoteAnalysis"]["requiresLiveQuoter"] is True


def test_merge_evidence_demotes_cache_source_away_from_live_verified():
    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteAnalysis": {
            "requiresLiveQuoter": True,
            "steps": [
                {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB"},
            ],
        },
    }
    evidence = {
        "status": "live_verified",
        "executionTrust": "live_v4_adapter",
        "source": "token_balance_scan_cache",
        "hops": [
            {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "status": "live_verified"},
        ],
        "costModel": {},
        "validation": {},
        "blockers": [],
    }

    _merge_onchain_evidence_into_route(route, evidence)

    assert route["quoteStatus"] == "static_reference_pending_live_review"
    assert route["onchainEvidenceStatus"] == "static_reference_pending_live_review"
    assert route["quoteAnalysis"]["requiresLiveQuoter"] is True
    assert route["live_onchain_evidence"]["executionTrust"] == "static_reference_pending_live_review"


def test_merge_evidence_blocks_hop_amount_continuity_mismatch():
    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteAnalysis": {
            "referenceFinally": None,
            "steps": [
                {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "inputAmount": 1000.0, "outputAmount": 0.018829891539466887},
                {"hop": 2, "fromSymbol": "TB", "toSymbol": "TA", "inputAmount": 52.369752540558274, "outputAmount": 0.187722992171735303},
                {"hop": 3, "fromSymbol": "TA", "toSymbol": "TZ", "inputAmount": 1.9862697254812829, "outputAmount": 1.865088633546139159},
            ],
        },
    }
    evidence = {
        "status": "route_unprofitable",
        "executionTrust": "live_v4_adapter",
        "livePriceVerified": True,
        "costModel": {
            "borrowInputSymbol": "TZ",
            "borrowInputAmount": 1000.0,
            "finalOutputAmount": 10.348436749171217,
            "finalOutputSymbol": "TZ",
            "netProfitAmount": -990.1515632508288,
            "netProfitAmountBps": -9901.515632508288,
        },
        "validation": {
            "finalOutputAmount": 10.348436749171217,
            "finalOutputSymbol": "TZ",
            "netProfitAmount": -990.1515632508288,
        },
        "hops": [
            {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "outputAmount": 0.018829891539466887},
            {"hop": 2, "fromSymbol": "TB", "toSymbol": "TA", "inputAmount": 52.369752540558274, "outputAmount": 0.187722992171735303},
            {"hop": 3, "fromSymbol": "TA", "toSymbol": "TZ", "inputAmount": 1.9862697254812829, "outputAmount": 1.865088633546139159},
        ],
        "blockers": [],
    }

    _merge_onchain_evidence_into_route(route, evidence)
    analysis = route["quoteAnalysis"]

    assert any("hop amounts are inconsistent" in blocker for blocker in analysis.get("blockers", []) or evidence.get("blockers", []))
    assert "hop_amount_continuity_mismatch" in analysis["warnings"]
    assert analysis["referenceInputAmount"] == 1000.0
    assert analysis["referenceFinalOutputAmount"] == 10.348436749171217
    assert analysis["referenceNetProfitAmount"] == -990.1515632508288
    assert analysis["referenceNetReturnBps"] == -9901.515632508288
    assert analysis["recommendation"]["action"] == "on-chain best amount unconfirmed; cannot enter transaction preflight"


def test_merge_live_route_estimate_keeps_borrow_input_aligned_with_best_amount():
    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteAnalysis": {
            "referenceInputAmount": 1.0,
            "referenceInputSymbol": "TZ",
        },
    }
    live_payload = {
        "amount": 1.0,
        "bestAmount": 8.0,
        "bestAmountConfirmed": True,
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "steps": [
            {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "inputAmount": 8.0},
            {"hop": 2, "fromSymbol": "TB", "toSymbol": "TA", "inputAmount": 8.0},
            {"hop": 3, "fromSymbol": "TA", "toSymbol": "TZ", "inputAmount": 8.0},
        ],
        "analysis": {
            "referenceInputSymbol": "TZ",
            "referenceFinalOutputAmount": 11.414891501849196,
            "referenceNetProfitAmount": 3.4148915018491968,
        },
    }

    result = dex_surface_helpers._merge_live_route_estimate(route, live_payload)
    cost_model = result["live_onchain_evidence"]["costModel"]

    assert cost_model["borrowInputAmount"] == 8.0
    assert cost_model["selectedBorrowInputAmount"] == 8.0
    assert cost_model["requestedBorrowInputAmount"] == 1.0
    assert result["quoteAnalysis"]["referenceInputAmount"] == 8.0


def test_merge_live_route_estimate_does_not_fabricate_best_amount_from_requested_amount():
    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteAnalysis": {
            "referenceInputAmount": 1.0,
            "referenceInputSymbol": "TZ",
        },
    }
    live_payload = {
        "amount": 1.0,
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "steps": [
            {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "inputAmount": 1.0},
            {"hop": 2, "fromSymbol": "TB", "toSymbol": "TA", "inputAmount": 1.0},
            {"hop": 3, "fromSymbol": "TA", "toSymbol": "TZ", "inputAmount": 1.0},
        ],
        "quoteStatus": "static_reference_pending_live_review",
        "analysis": {
            "referenceFinalOutputAmount": 1.071441601366711,
            "referenceNetProfitAmount": 0.07094160136671104,
        },
    }

    result = dex_surface_helpers._merge_live_route_estimate(route, live_payload)
    cost_model = result["live_onchain_evidence"]["costModel"]

    assert cost_model["bestAmountComputed"] is False
    assert cost_model["bestAmountMissing"] is True
    assert cost_model["borrowInputAmount"] is None
    assert cost_model["selectedBorrowInputAmount"] is None
    assert cost_model["requestedBorrowInputAmount"] == 1.0
    assert result["quoteAnalysis"].get("bestAmount") is None


def test_merge_live_route_estimate_ignores_best_amount_when_missing_flag_is_true():
    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteAnalysis": {},
    }
    live_payload = {
        "amount": 1.0,
        "bestAmount": 8.0,
        "bestAmountMissing": True,
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteStatus": "static_reference_pending_live_review",
        "analysis": {
            "referenceInputAmount": 1.0,
            "referenceNetProfitAmount": 0.07,
        },
    }

    result = dex_surface_helpers._merge_live_route_estimate(route, live_payload)
    cost_model = result["live_onchain_evidence"]["costModel"]

    assert cost_model["borrowInputAmount"] is None
    assert cost_model["selectedBorrowInputAmount"] is None
    assert cost_model["bestAmountMissing"] is True
    assert result["quoteAnalysis"].get("bestAmount") is None


def test_merge_evidence_does_not_fabricate_scaled_steps_without_live_hops():
    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteAnalysis": {
            "referenceInputAmount": 1.0,
            "referenceInputSymbol": "TZ",
            "steps": [
                {
                    "hop": 1,
                    "fromSymbol": "TZ",
                    "toSymbol": "TB",
                    "inputAmount": 1.0,
                    "outputAmount": 1.623477868780824281,
                },
                {
                    "hop": 2,
                    "fromSymbol": "TB",
                    "toSymbol": "TA",
                    "inputAmount": 1.623477868780824281,
                    "outputAmount": 0.161777836025244087,
                },
                {
                    "hop": 3,
                    "fromSymbol": "TA",
                    "toSymbol": "TZ",
                    "inputAmount": 0.161777836025244087,
                    "outputAmount": 1.071441601366711039,
                },
            ],
        },
    }
    evidence = {
        "status": "live_verified",
        "executionTrust": "live_v4_adapter",
        "readyForTransactionPreflight": True,
        "costModel": {
            "borrowInputSymbol": "TZ",
            "borrowInputAmount": 8.0,
            "selectedBorrowInputAmount": 8.0,
            "requestedBorrowInputAmount": 1.0,
            "finalOutputAmount": 11.414891501849196,
            "finalOutputSymbol": "TZ",
            "netProfitAmount": 3.4148915018491968,
            "netProfitAmountBps": 4268.614377311496,
        },
        "validation": {
            "finalOutputAmount": 11.414891501849196,
            "finalOutputSymbol": "TZ",
        },
        "hops": [],
        "blockers": [],
    }

    _merge_onchain_evidence_into_route(route, evidence)
    analysis = route["quoteAnalysis"]
    steps = analysis["steps"]

    assert analysis["bestAmount"] == 8.0
    assert analysis["livePriceVerified"] is False
    assert analysis["quote_verified"] is False
    assert analysis["executableCandidate"] is False
    assert analysis["chainSubmissionCandidate"] is False
    assert analysis["onchainEvidenceStatus"] == "static_reference_pending_live_review"
    assert analysis["referenceInputAmount"] == 1.0
    assert analysis["referenceFinalOutputAmount"] is None
    assert analysis["referenceNetProfitAmount"] is None
    assert steps[0]["inputAmount"] == 1.0
    assert steps[2]["outputAmount"] == 1.071441601366711039


def test_merge_evidence_uses_borrow_input_amount_when_selected_missing():
    route = {
        "routeSymbols": ["TZ", "TB", "TA", "TZ"],
        "quoteAnalysis": {
            "steps": [
                {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TB", "inputAmount": 1.0},
                {"hop": 2, "fromSymbol": "TB", "toSymbol": "TA", "inputAmount": 1.0},
                {"hop": 3, "fromSymbol": "TA", "toSymbol": "TZ", "inputAmount": 1.0, "outputAmount": 1.071441601366711},
            ],
        },
    }
    evidence = {
        "status": "live_verified",
        "costModel": {
            "borrowInputSymbol": "TZ",
            "borrowInputAmount": 8.0,
        },
        "hops": [],
        "blockers": [],
    }

    _merge_onchain_evidence_into_route(route, evidence)

    assert route["quoteAnalysis"]["bestAmount"] == 8.0
    assert route["quoteAnalysis"]["steps"][0]["inputAmount"] == 1.0


def test_route_has_complete_v4_pool_identity_accepts_full_pool_keys():
    route = {
        "routeSymbols": ["ETH", "0X102154", "0X5884AD", "ETH"],
        "routePools": [
            {
                "poolId": "0x" + "a" * 64,
                "poolKey": {
                    "currency0": "0x0000000000000000000000000000000000000000",
                    "currency1": "0x" + "1" * 40,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
            },
            {
                "poolId": "0x" + "b" * 64,
                "poolKey": {
                    "currency0": "0x" + "1" * 40,
                    "currency1": "0x" + "2" * 40,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
            },
            {
                "poolId": "0x" + "c" * 64,
                "poolKey": {
                    "currency0": "0x" + "2" * 40,
                    "currency1": "0x0000000000000000000000000000000000000000",
                    "fee": 500,
                    "tickSpacing": 10,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
            },
        ],
    }

    assert dex_surface_routes_quotes._route_has_complete_v4_pool_identity(route) is True


def test_route_has_complete_v4_pool_identity_rejects_missing_middle_pool_key():
    route = {
        "routeSymbols": ["ETH", "0X102154", "0X5884AD", "ETH"],
        "routePools": [
            {
                "poolId": "0x" + "a" * 64,
                "poolKey": {
                    "currency0": "0x0000000000000000000000000000000000000000",
                    "currency1": "0x" + "1" * 40,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
            },
            {"poolId": "0x" + "b" * 64},
            {
                "poolId": "0x" + "c" * 64,
                "poolKey": {
                    "currency0": "0x" + "2" * 40,
                    "currency1": "0x0000000000000000000000000000000000000000",
                    "fee": 500,
                    "tickSpacing": 10,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
            },
        ],
    }

    assert dex_surface_routes_quotes._route_has_complete_v4_pool_identity(route) is False


def test_compact_static_route_pool_preserves_pool_key():
    pool = {
        "pool": "0x" + "a" * 64,
        "poolId": "0x" + "a" * 64,
        "pairSymbol": "ETH:0X102154",
        "fee": 3000,
        "token0": "0x0000000000000000000000000000000000000000",
        "token1": "0x" + "1" * 40,
        "poolKey": {
            "currency0": "0x0000000000000000000000000000000000000000",
            "currency1": "0x" + "1" * 40,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
        },
    }

    compact = dex_surface_routes_quotes._compact_static_route_pool(pool)

    assert compact["poolKey"] == pool["poolKey"]
    assert dex_surface_routes_quotes._route_pool_has_complete_v4_identity(compact) is True


def test_filter_quote_routes_with_complete_v4_pool_identity_drops_scanner_snapshot_routes():
    pool_key = {
        "currency0": "0x0000000000000000000000000000000000000000",
        "currency1": "0x" + "1" * 40,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": "0x0000000000000000000000000000000000000000",
    }
    good_route = {
        "routeSymbols": ["ETH", "0X102154", "0X5884AD", "ETH"],
        "routePools": [
            {"poolId": "0x" + "a" * 64, "poolKey": pool_key},
            {"poolId": "0x" + "b" * 64, "poolKey": pool_key},
            {"poolId": "0x" + "c" * 64, "poolKey": pool_key},
        ],
    }
    bad_route = {
        "routeSymbols": ["ETH", "0X102154", "0X1FBE1A", "ETH"],
        "routePools": [
            {"poolId": "0x" + "d" * 64, "poolKey": pool_key},
            {"poolId": "0x" + "e" * 64},
            {"poolId": "0x" + "f" * 64, "poolKey": pool_key},
        ],
    }
    payload = {
        "snapshot": {
            "payload": {
                "routes": [good_route, bad_route],
                "summary": {"routeCount": 2, "candidateRouteCount": 2, "chainReviewEligiblePathCount": 2},
            }
        }
    }

    dex_surface_routes_quotes.filter_quote_routes_with_complete_v4_pool_identity(payload)

    routes = payload["snapshot"]["payload"]["routes"]
    assert routes == [good_route]
    summary = payload["snapshot"]["payload"]["summary"]
    assert summary["routeCount"] == 1
    assert summary["candidateRouteCount"] == 1
    assert summary["chainReviewEligiblePathCount"] == 1
