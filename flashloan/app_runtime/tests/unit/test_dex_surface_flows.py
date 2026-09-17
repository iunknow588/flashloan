from __future__ import annotations

from types import SimpleNamespace

from dex.intent_trade.chains._dex_surface_flows import dex_quote_routes_flow
from market.binance_market.dex_surface_cache import (
    _cache_surface_payload,
    _clear_dex_surface_cache,
)


def test_dex_quote_routes_flow_does_not_return_scanner_evidence_to_page():
    _clear_dex_surface_cache()
    polluted_payload = {
        "routes": [
            {
                "symbolPair": "TA -> TZ -> TB -> TA",
                "routeSymbols": ["TA", "TZ", "TB", "TA"],
                "routeTokenPath": [],
                "routeAddresses": [],
                "quoteStatus": "missing_effective_pool",
                "live_onchain_evidence": {"status": "missing_effective_pool"},
                "onchainEvidenceStatus": "missing_effective_pool",
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": True,
                    "livePriceVerified": False,
                    "adapterRequired": False,
                },
            }
        ]
    }
    _cache_surface_payload(
        "dex_quote_routes",
        polluted_payload,
        chain_id=46630,
        protocols=["uniswap_v4"],
        use_static_observation=True,
    )

    request = SimpleNamespace(
        chain_id=46630,
        protocol="uniswap_v4",
        flow={"allowed_protocols": {"uniswap_v4"}},
    )
    payload = dex_quote_routes_flow(request=request, protocols=["uniswap_v4"])

    route = payload["routes"][0]
    assert route["quoteStatus"] == "requires_live_quoter"
    assert "live_onchain_evidence" not in route
    assert "onchainEvidenceStatus" not in route
    assert "live_onchain_evidence" not in route["quoteAnalysis"]
