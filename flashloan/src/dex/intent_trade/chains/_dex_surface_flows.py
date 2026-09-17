from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from market.binance_market.dex_surfaces import (
    dex_market_observation_payload,
    dex_market_observation_surface_payload,
    dex_overview_payload,
    dex_quote_request_payload,
    dex_quote_routes_payload,
)
from market.binance_market.dex_quote_route_common import quote_route_status


def _request_protocols(request: ChainFlowRequest, protocols: list[str] | None = None) -> list[str]:
    allowed = {str(item or "").strip().lower() for item in request.flow.get("allowed_protocols") or []}
    requested = [
        str(protocol or "").strip().lower()
        for protocol in (protocols or ([request.protocol] if request.protocol else []))
        if str(protocol or "").strip()
    ]
    return [protocol for protocol in requested if not allowed or protocol in allowed]


def market_observation_flow(
    *,
    request: ChainFlowRequest,
    protocols: list[str] | None = None,
    force_compute: bool = False,
    **_: Any,
) -> dict[str, Any]:
    requested_protocols = _request_protocols(request, protocols)
    if force_compute:
        return dex_market_observation_payload(
            chain_id=request.chain_id,
            protocols=requested_protocols,
        )
    return dex_market_observation_surface_payload(
        chain_id=request.chain_id,
        protocols=requested_protocols,
        use_static_observation=True,
    )


def dex_overview_flow(*, request: ChainFlowRequest, protocols: list[str] | None = None, **_: Any) -> dict[str, Any]:
    return dex_overview_payload(
        chain_id=request.chain_id,
        protocols=_request_protocols(request, protocols),
        use_static_observation=True,
    )


def dex_quote_routes_flow(*, request: ChainFlowRequest, protocols: list[str] | None = None, **_: Any) -> dict[str, Any]:
    payload = dex_quote_routes_payload(
        chain_id=request.chain_id,
        protocols=_request_protocols(request, protocols),
        use_static_observation=True,
    )
    # The quote-routes page is a review surface, not a scanner snapshot. The
    # opportunity scanner attaches evidence to its working route copies while
    # reviewing candidates; that evidence is deliberately kept in the scanner
    # snapshot. Returning it through this flow makes the page replay stale
    # scanner conclusions (for example a missing_effective_pool label from an
    # older route build) and re-introduces the recurring "old evidence" issue.
    payload = deepcopy(payload)
    for route in payload.get("routes") or []:
        if not isinstance(route, dict):
            continue
        route.pop("live_onchain_evidence", None)
        route.pop("onchainEvidenceStatus", None)
        analysis = route.get("quoteAnalysis")
        if isinstance(analysis, dict):
            analysis.pop("live_onchain_evidence", None)
            analysis.pop("onchainEvidenceStatus", None)
        route["quoteStatus"] = quote_route_status(analysis or {})
    return payload


def dex_quote_request_flow(
    *,
    request: ChainFlowRequest,
    protocols: list[str] | None = None,
    pair: str | None = None,
    venue: str | None = None,
    amount: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    requested_protocols = _request_protocols(request, protocols)
    payload = dex_quote_request_payload(
        chain_id=request.chain_id,
        protocols=requested_protocols,
        pair=pair,
        venue=venue,
        amount=amount,
        use_static_observation=True,
    )
    payload.setdefault("chain_id", request.chain_id)
    payload.setdefault("selected_protocols", requested_protocols)
    return payload


__all__ = [
    "dex_overview_flow",
    "dex_quote_request_flow",
    "dex_quote_routes_flow",
    "market_observation_flow",
]
