from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from web.data_routes import dex_surface_helpers
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
