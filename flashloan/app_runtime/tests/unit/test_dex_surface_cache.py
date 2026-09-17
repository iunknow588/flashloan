from __future__ import annotations

from market.binance_market.dex_surface_cache import (
    _cache_surface_payload,
    _clear_dex_surface_cache,
    _cached_surface_payload,
)


def test_cached_surface_payload_is_deeply_isolated_from_nested_route_mutations():
    _clear_dex_surface_cache()
    payload = {
        "routes": [
            {
                "routeSymbols": ["TA", "TZ", "TB", "TA"],
                "live_onchain_evidence": {"status": "static_reference_only"},
            }
        ]
    }
    _cache_surface_payload(
        "dex_quote_routes",
        payload,
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    working = _cached_surface_payload(
        "dex_quote_routes",
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )
    working["routes"][0]["live_onchain_evidence"]["status"] = "route_unprofitable"
    working["routes"][0]["routeSymbols"].append("MUTATED")

    cached_again = _cached_surface_payload(
        "dex_quote_routes",
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )
    assert cached_again["routes"][0]["live_onchain_evidence"]["status"] == "static_reference_only"
    assert cached_again["routes"][0]["routeSymbols"] == ["TA", "TZ", "TB", "TA"]


def test_cache_stores_private_deepcopy_when_initial_payload_is_cached():
    _clear_dex_surface_cache()
    payload = {
        "routes": [
            {
                "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                "live_onchain_evidence": {"status": "live_evidence_deferred"},
            }
        ]
    }
    _cache_surface_payload(
        "dex_quote_routes",
        payload,
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    payload["routes"][0]["live_onchain_evidence"]["status"] = "missing_effective_pool"
    payload["routes"][0]["routeSymbols"].append("MUTATED")

    cached = _cached_surface_payload(
        "dex_quote_routes",
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )
    assert cached["routes"][0]["live_onchain_evidence"]["status"] == "live_evidence_deferred"
    assert cached["routes"][0]["routeSymbols"] == ["TZ", "TB", "TA", "TZ"]
