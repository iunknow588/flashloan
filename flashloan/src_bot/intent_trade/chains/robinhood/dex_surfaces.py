from __future__ import annotations

from typing import Any

from market.binance_market.dex_surfaces import (
    dex_market_observation_payload,
    dex_market_observation_surface_payload,
    dex_overview_payload,
    dex_quote_request_payload,
    dex_quote_routes_payload,
)


def _request_protocols(request, protocols: list[str] | None = None) -> list[str]:
    allowed = {str(item or "").strip().lower() for item in request.flow.get("allowed_protocols") or []}
    requested = [
        str(protocol or "").strip().lower()
        for protocol in (protocols or ([request.protocol] if request.protocol else []))
        if str(protocol or "").strip()
    ]
    return [protocol for protocol in requested if not allowed or protocol in allowed]


def market_observation_flow(
    *,
    request,
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
    )


def dex_overview_flow(*, request, protocols: list[str] | None = None, **_: Any) -> dict[str, Any]:
    return dex_overview_payload(
        chain_id=request.chain_id,
        protocols=_request_protocols(request, protocols),
        use_static_observation=True,
    )


def dex_quote_routes_flow(*, request, protocols: list[str] | None = None, **_: Any) -> dict[str, Any]:
    return dex_quote_routes_payload(
        chain_id=request.chain_id,
        protocols=_request_protocols(request, protocols),
        use_static_observation=True,
    )


def dex_quote_request_flow(
    *,
    request,
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
    "dex_market_observation_payload",
    "dex_market_observation_surface_payload",
    "dex_overview_payload",
    "dex_quote_request_payload",
    "dex_quote_routes_payload",
    "dex_overview_flow",
    "dex_quote_request_flow",
    "dex_quote_routes_flow",
    "market_observation_flow",
]
