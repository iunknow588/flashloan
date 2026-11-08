"""Shared RuntimeCore adapter and comparison helpers."""

from __future__ import annotations

import sys
from typing import Any

from flask import has_app_context
from core.manager_api_client import ManagerApiClient, ManagerApiClientError, manager_ui_data_source

CANDIDATE_SNAPSHOT_LIMIT = 1000
CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT = 200
CLOSED_LOOP_SNAPSHOT_LIMIT = 1000
CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT = 200
CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT = 1000
CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT = 200
RUNTIME_CORE_OVERVIEW_VIEW = "overview"
RUNTIME_CORE_OVERVIEW_LIMIT = CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT
SUBMISSION_SNAPSHOT_LIMIT = 1000
RUNTIME_CORE_MANAGER_VIEWS = {
    "/v1/candidate-edge-pools": "candidate_edge",
    "/v1/candidate-exchange-pools": "candidate_exchange",
    "/v1/closed-loops": "closed_loops",
    "/v1/reviews": "reviews",
    "/v1/broadcasts": "broadcasts",
}
SUBMISSION_FAILURE_CATEGORY_ORDER = ("all", "broadcast", "submission", "code", "rpc", "execution", "quote", "blocked", "deferred", "other")

def _compat_manager_client():
    facade = sys.modules.get("web.data_routes.manager_page_compare")
    return getattr(facade, "ManagerApiClient", ManagerApiClient) if facade is not None else ManagerApiClient

from urllib.parse import quote
from .manager_page_compare_common import (
    ManagerApiClientError,
    _compat_runtime_core_manager_overview_envelope,
    _int_or_none,
    _manager_read_error,
    _mapping_value,
    _shadow_error,
)
from .manager_overview_payload import _attach_closed_loop_control, _overview_manager_contract_error


def quote_routes_manager_page_payload(
    *,
    chain_id: int,
    protocols: list[str],
    anchor: str | None = None,
    chain_selection: dict[str, Any] | None = None,
    request_id: str | None = None,
    overview_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read and map the RuntimeCore manager overview into the quote-routes DTO."""

    envelope = overview_envelope or _compat_runtime_core_manager_overview_envelope(
        chain_id=int(chain_id),
        limit=RUNTIME_CORE_OVERVIEW_LIMIT,
        request_id=request_id,
    )
    data = envelope.get("data")
    if envelope.get("ok") is False or not isinstance(data, dict):
        return _manager_read_error(
            envelope.get("code") or "RUNTIME_CORE_INVALID_RESPONSE",
            envelope.get("detail")
            or "quote-routes manager response must contain an object data field",
            upstream_status=envelope.get("upstreamStatus"),
        )
    contract_error = _overview_manager_contract_error(data, chain_id=chain_id)
    if contract_error is not None:
        return contract_error
    for field in ("arbitragePaths", "arbitragePathGroups"):
        if any(not isinstance(row, dict) for row in data.get(field) or []):
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                f"overview manager response field {field} must contain objects",
            )

    all_paths = list(data.get("arbitragePaths") or [])
    anchor_filter = str(anchor or "").strip()
    anchors = {
        item.strip().upper()
        for item in anchor_filter.split(",")
        if item.strip()
    }
    filtered_paths = [
        path
        for path in all_paths
        if not anchors or _quote_route_anchor(path) in anchors
    ]
    selected_protocols = list(protocols) if protocols else list(data.get("selectedProtocols") or [])
    route_framework_source = str(
        data.get("routeFrameworkSource")
        or _mapping_value(data.get("routeFramework")).get("routeFrameworkSource")
        or "manager_closed_loop"
    )
    routes = [
        _quote_route_manager_dto(
            path,
            chain_id=int(chain_id),
            default_route_framework_source=route_framework_source,
        )
        for path in filtered_paths
    ]
    groups = [
        _quote_route_group_manager_dto(
            group,
            chain_id=int(chain_id),
            anchors=anchors,
            default_route_framework_source=route_framework_source,
        )
        for group in data.get("arbitragePathGroups") or []
    ]
    groups = [group for group in groups if group.get("paths")]
    summary = dict(data.get("summary") or {})
    summary.update(
        {
            "routeCount": len(routes),
            "candidateRouteCount": len(routes),
            "chainReviewEligiblePathCount": sum(
                1 for route in routes if route.get("chainReviewEligible")
            ),
            "diagnosticOnlyPathCount": sum(
                1 for route in routes if route.get("diagnosticOnly")
            ),
            "managerReviewCompleteCount": sum(
                1
                for path in filtered_paths
                if _mapping_value(path.get("overviewProfit")).get("complete") is True
            ),
            "unfilteredRouteCount": len(all_paths),
            "anchorFilter": anchor_filter or None,
            "pathSource": route_framework_source,
        }
    )
    closed_loop_control = _attach_closed_loop_control(
        routes,
        chain_id=int(chain_id),
    )
    data_quality = dict(_mapping_value(data.get("dataQuality")))
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "mode": "dex_quote_routes",
        "chain_id": int(chain_id),
        "network": data.get("network"),
        "selected_protocols": selected_protocols,
        "requested_protocols": list(protocols),
        "manager_selected_protocols": list(data.get("selectedProtocols") or []),
        "chain_selection": dict(chain_selection or {}),
        "processing": {
            "flow_name": "dex_quote_routes",
            "network": data.get("network"),
            "handler": "runtime_core:overview",
        },
        "tableSource": "manager_closed_loop",
        "referencedTable": "manager_arbitrage_path",
        "routeFrameworkSource": route_framework_source,
        "marketObservationSource": data.get("marketObservationSource"),
        "marketObservationSourceStage": data.get("marketObservationSourceStage"),
        "decisionBasis": data.get("decisionBasis"),
        "generatedAt": data.get("generatedAt"),
        "summary": summary,
        "routes": routes,
        "closedLoopGroups": groups,
        "watchPairs": list(data.get("watchPairs") or []),
        "liquidPoolRegistry": list(data.get("liquidPoolRegistry") or []),
        "routeFramework": dict(_mapping_value(data.get("routeFramework"))),
        "flashLoanLiquidity": dict(_mapping_value(data.get("flashLoanLiquidity"))),
        "poolStaticTable": dict(_mapping_value(data.get("poolStaticTable"))),
        "versions": dict(_mapping_value(data.get("versions"))),
        "dataQuality": data_quality,
        "pipelineStatus": dict(_mapping_value(data.get("pipelineStatus"))),
        "closedLoopControl": {
            key: value
            for key, value in closed_loop_control.items()
            if key not in {"workers", "executionTasks"}
        },
        "managerEnvelopeVersion": envelope.get("version"),
        "runtimeDataSource": envelope.get("dataSource") or "rust_runtime_core",
        "runtimeCore": dict(envelope.get("runtimeCore") or {}),
    }


def quote_request_manager_page_payload(
    *,
    chain_id: int,
    protocols: list[str],
    pair: str | None,
    venue: str | None,
    amount: str | None,
    chain_selection: dict[str, Any] | None = None,
    request_id: str | None = None,
    overview_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one RuntimeCore overview path and return its quote DTO."""

    routes_payload = quote_routes_manager_page_payload(
        chain_id=chain_id,
        protocols=protocols,
        chain_selection=chain_selection,
        request_id=request_id,
        overview_envelope=overview_envelope,
    )
    if not routes_payload.get("ok"):
        return routes_payload
    normalized_pair = str(pair or "").strip().upper()
    normalized_venue = str(venue or "manager").strip().lower()
    routes = list(routes_payload.get("routes") or [])
    matched = _match_quote_request_route(routes, normalized_pair)
    if matched is None:
        return _quote_request_pair_not_found(
            routes=routes,
            routes_payload=routes_payload,
            pair=normalized_pair,
            venue=normalized_venue,
            amount=amount,
            chain_id=int(chain_id),
            protocols=routes_payload.get("selected_protocols") or list(protocols),
            chain_selection=chain_selection,
        )
    return _quote_request_route_manager_dto(
        matched,
        routes_payload=routes_payload,
        pair=normalized_pair,
        venue=normalized_venue,
        amount=amount,
        chain_id=int(chain_id),
        protocols=routes_payload.get("selected_protocols") or list(protocols),
        chain_selection=chain_selection,
    )


def _match_quote_request_route(
    routes: list[dict[str, Any]],
    normalized_pair: str,
) -> dict[str, Any] | None:
    for route in routes:
        if not isinstance(route, dict):
            continue
        candidates = [
            route.get("symbolPair"),
            _mapping_value(route.get("overviewRoute")).get("pathText"),
            " -> ".join(str(symbol or "").strip() for symbol in route.get("routeSymbols") or []),
        ]
        if any(str(candidate or "").strip().upper() == normalized_pair for candidate in candidates):
            return route
    return None


def _quote_request_pair_not_found(
    *,
    routes: list[dict[str, Any]],
    routes_payload: dict[str, Any],
    pair: str | None,
    venue: str,
    amount: str | None,
    chain_id: int,
    protocols: list[str],
    chain_selection: dict[str, Any] | None,
) -> dict[str, Any]:
    detail = (
        "manager overview does not contain the requested candidate pair"
        if pair
        else "pair is required"
    )
    return {
        "ok": False,
        "source": "manager",
        "dataSource": "manager",
        "mode": "dex_quote_request",
        "chain_id": chain_id,
        "network": routes_payload.get("network"),
        "selected_protocols": list(protocols),
        "processing": {
            "flow_name": "dex_quote_request",
            "network": routes_payload.get("network"),
            "handler": "runtime_core:overview",
        },
        "pair": pair or None,
        "venue": venue,
        "amount": amount,
        "quoteRequests": [],
        "error": "candidate_pair_not_found",
        "detail": detail,
        "availablePairs": [
            str(route.get("symbolPair") or "").strip()
            for route in routes
            if str(route.get("symbolPair") or "").strip()
        ],
        "errorStack": {
            "status": "candidate_pair_not_found",
            "message": detail,
            "evidenceSource": "manager_overview",
            "manager": True,
            "blockers": ["manager_overview_missing_pair"],
        },
        "chain_selection": dict(chain_selection or {}),
        "versions": dict(_mapping_value(routes_payload.get("versions"))),
        "dataQuality": dict(_mapping_value(routes_payload.get("dataQuality"))),
        "managerEnvelopeVersion": routes_payload.get("managerEnvelopeVersion"),
        "runtimeDataSource": routes_payload.get("runtimeDataSource"),
        "runtimeCore": dict(_mapping_value(routes_payload.get("runtimeCore"))),
    }


def _quote_request_route_manager_dto(
    route: dict[str, Any],
    *,
    routes_payload: dict[str, Any],
    pair: str | None,
    venue: str,
    amount: str | None,
    chain_id: int,
    protocols: list[str],
    chain_selection: dict[str, Any] | None,
) -> dict[str, Any]:
    route_symbols = list(route.get("routeSymbols") or [])
    route_addresses = list(route.get("routeAddresses") or [])
    quote_inputs = _mapping_value(route.get("quoteInputs"))
    candidate_pools = list(quote_inputs.get("candidatePools") or [])
    route_pools = list(route.get("routePools") or quote_inputs.get("routePools") or [])
    pool_ids = list(route.get("poolIds") or [])
    quote_analysis = dict(_mapping_value(route.get("quoteAnalysis")))
    quote_verification = _manager_quote_verification_payload(quote_analysis)
    quote_status = str(route.get("quoteStatus") or quote_verification.get("status") or "").strip()
    project_requests = list(quote_inputs.get("quoteRequests") or [])
    quote_requests = []
    for request in project_requests:
        if not isinstance(request, dict):
            continue
        mapped = dict(request)
        mapped.update(
            {
                "venue": venue,
                "quoteMode": "manager_overview_projection",
                "status": quote_status,
                "quoteRequestUrl": (
                    f"/api/dex/quote-request?pair={quote(str(route.get('symbolPair') or ''), safe='')}"
                    f"&venue={venue}"
                ),
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "routeTokenPath": list(route.get("routeTokenPath") or []),
                "pools": pool_ids,
                "chainId": chain_id,
                "managerOverview": True,
            }
        )
        quote_requests.append(mapped)
    status = "quote_request_ready" if quote_requests else "manager_overview_pair_unavailable"
    error_stack = {
        "status": quote_status or "manager_review_pending",
        "message": quote_analysis.get("failureReason")
        or quote_analysis.get("error")
        or ("manager_review_signed_pnl" if quote_analysis.get("complete") is True else "manager_review_pending"),
        "evidenceSource": "manager_overview",
        "manager": True,
        "blockers": list(quote_analysis.get("blockers") or quote_analysis.get("blocked_reasons") or []),
    }
    return {
        "ok": bool(quote_requests),
        "source": "manager",
        "dataSource": "manager",
        "mode": "dex_quote_request",
        "chain_id": chain_id,
        "network": routes_payload.get("network"),
        "selected_protocols": list(protocols),
        "processing": {
            "flow_name": "dex_quote_request",
            "network": routes_payload.get("network"),
            "handler": "runtime_core:overview",
        },
        "pair": str(route.get("symbolPair") or ""),
        "requestedPair": pair or None,
        "venue": venue,
        "amount": amount,
        "routeKey": route.get("routeKey") or "",
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeTokenPath": list(route.get("routeTokenPath") or []),
        "poolIds": pool_ids,
        "quoteStatus": quote_status,
        "quoteVerification": quote_verification,
        "quoteAnalysis": quote_analysis,
        "quoteRequests": quote_requests,
        "status": status,
        "errorStack": error_stack,
        "candidatePools": candidate_pools,
        "routePools": route_pools,
        "tableSource": "manager_closed_loop",
        "referencedTable": "manager_arbitrage_path",
        "routeFrameworkSource": route.get("routeFrameworkSource")
        or routes_payload.get("routeFrameworkSource")
        or "manager_closed_loop",
        "marketObservationSource": routes_payload.get("marketObservationSource"),
        "marketObservationSourceStage": routes_payload.get("marketObservationSourceStage"),
        "decisionBasis": routes_payload.get("decisionBasis"),
        "generatedAt": routes_payload.get("generatedAt"),
        "chain_selection": dict(chain_selection or {}),
        "versions": dict(_mapping_value(routes_payload.get("versions"))),
        "dataQuality": dict(_mapping_value(routes_payload.get("dataQuality"))),
        "pipelineStatus": dict(_mapping_value(routes_payload.get("pipelineStatus"))),
        "managerEnvelopeVersion": routes_payload.get("managerEnvelopeVersion"),
        "runtimeDataSource": routes_payload.get("runtimeDataSource"),
        "runtimeCore": dict(_mapping_value(routes_payload.get("runtimeCore"))),
    }


def _manager_quote_verification_payload(
    quote_analysis: dict[str, Any],
) -> dict[str, Any]:
    live_verified = bool(quote_analysis.get("livePriceVerified")) or str(
        quote_analysis.get("priceEvidenceMode") or ""
    ).strip().lower() == "live_verified"
    requires_live_quoter = bool(quote_analysis.get("requiresLiveQuoter")) or bool(
        quote_analysis.get("adapterRequired")
    )
    chain_candidate = bool(quote_analysis.get("chainSubmissionCandidate"))
    executable = bool(quote_analysis.get("executableCandidate"))
    return {
        "status": (
            "live_verified"
            if live_verified
            else "live_required"
            if requires_live_quoter
            else "reference_only"
        ),
        "livePriceVerified": live_verified,
        "requiresLiveQuoter": requires_live_quoter,
        "adapterRequired": bool(quote_analysis.get("adapterRequired")),
        "executableCandidate": executable,
        "chainSubmissionCandidate": chain_candidate,
        "trustedForExecution": bool(live_verified and (chain_candidate or executable)),
        "priceEvidenceSource": quote_analysis.get("priceEvidenceSource")
        or "manager_review",
        "priceEvidenceMode": quote_analysis.get("priceEvidenceMode")
        or ("live_verified" if live_verified else "review_pending"),
    }


def _quote_route_anchor(path: dict[str, Any]) -> str:
    overview_route = _mapping_value(path.get("overviewRoute"))
    anchor = str(overview_route.get("anchorBorrowSymbol") or "").strip()
    if anchor:
        return anchor.upper()
    route_symbols = path.get("routeSymbols")
    if isinstance(route_symbols, list) and route_symbols:
        return str(route_symbols[0] or "").strip().upper()
    return ""


def _quote_route_manager_dto(
    path: dict[str, Any],
    *,
    chain_id: int,
    default_route_framework_source: str,
) -> dict[str, Any]:
    overview_route = dict(_mapping_value(path.get("overviewRoute")))
    overview_profit = dict(_mapping_value(path.get("overviewProfit")))
    linkage = dict(_mapping_value(path.get("flashLoanLinkage")))
    route_symbols = [
        str(symbol or "").strip()
        for symbol in path.get("routeSymbols") or []
        if str(symbol or "").strip()
    ]
    route_addresses = [
        str(address or "").strip()
        for address in path.get("routeAddresses") or []
        if str(address or "").strip()
    ]
    execution_pools = [
        _quote_route_pool_dto(pool)
        for pool in path.get("executionPools") or []
        if isinstance(pool, dict)
    ]
    preferred_pools = [
        _quote_route_pool_dto(pool)
        for pool in path.get("preferredPools") or []
        if isinstance(pool, dict)
    ]
    route_pools = execution_pools or preferred_pools
    pool_ids = _quote_route_pool_ids(route_pools)
    path_source = str(path.get("pathSource") or default_route_framework_source)
    route_framework_source = str(
        path.get("routeFrameworkSource") or default_route_framework_source
    )
    route_usable = path.get("routeUsableForObservation")
    chain_review_eligible = bool(
        route_symbols
        and route_pools
        and route_usable is not False
        and all(_quote_route_pool_has_identity(pool) for pool in route_pools)
    )
    route_key = str(path.get("routeKey") or path.get("routeKeyId") or "").strip()
    if not route_key:
        identity = route_addresses or route_symbols
        route_key = "|".join(
            [
                "manager",
                *(str(item).lower() for item in identity),
                *(str(item).lower() for item in pool_ids),
            ]
        )
    hop_candidates = []
    for candidate in path.get("hopCandidates") or []:
        if not isinstance(candidate, dict):
            continue
        mapped = dict(candidate)
        mapped["pools"] = [
            _quote_route_pool_dto(pool)
            for pool in candidate.get("pools") or []
            if isinstance(pool, dict)
        ]
        hop_candidates.append(mapped)
    steps = []
    for index in range(max(0, len(route_symbols) - 1)):
        pool = route_pools[index] if index < len(route_pools) else {}
        steps.append(
            {
                "hop": index + 1,
                "fromSymbol": route_symbols[index],
                "toSymbol": route_symbols[index + 1],
                "protocol": pool.get("protocol") or "uniswap_v4",
                "poolId": pool.get("poolId"),
                "status": (
                    "manager_review_signed_pnl"
                    if overview_profit.get("complete") is True
                    else "manager_review_pending"
                ),
            }
        )
    route_token_path = [
        {"symbol": symbol, "address": route_addresses[index] if index < len(route_addresses) else None}
        for index, symbol in enumerate(route_symbols)
    ]
    complete = overview_profit.get("complete") is True
    live_verified = (
        str(overview_profit.get("priceEvidenceMode") or "").strip().lower()
        == "live_verified"
    )
    quote_status = (
        "manager_review_verified"
        if complete and live_verified
        else "manager_review_pending"
    )
    quote_analysis = {
        **overview_profit,
        "method": overview_profit.get("method") or "manager_review_signed_pnl",
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "steps": steps,
        "stepCount": len(steps),
        "livePriceVerified": live_verified,
        "requiresLiveQuoter": bool(overview_profit.get("requiresLiveQuoter", not complete)),
        "adapterRequired": bool(overview_profit.get("adapterRequired", not complete)),
        "complete": complete,
        "profitable": bool(overview_profit.get("profitable")),
        "executableCandidate": bool(overview_profit.get("executableCandidate")),
        "chainSubmissionCandidate": bool(
            overview_profit.get("chainSubmissionCandidate")
        ),
        "priceEvidenceSource": overview_profit.get("priceEvidenceSource")
        or "manager_review",
        "priceEvidenceMode": overview_profit.get("priceEvidenceMode")
        or "review_pending",
    }
    return {
        "routeKey": route_key,
        "kind": overview_route.get("kind") or "closed_loop",
        "source": path_source,
        "symbolPair": path.get("symbolPair") or "",
        "selected_basis": "closed_loop",
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeTokenPath": route_token_path,
        "routePools": route_pools,
        "executionPools": route_pools,
        "preferredPools": route_pools,
        "strictQualityPools": route_pools,
        "poolIds": pool_ids,
        "poolCount": len(route_pools),
        "poolPathKey": "|".join(pool_ids),
        "tableSource": "manager_closed_loop",
        "referencedTable": "manager_arbitrage_path",
        "pathSource": path_source,
        "routeFrameworkSource": route_framework_source,
        "marketObservationSourceStage": path.get("marketObservationSourceStage"),
        "priceEvidenceSource": path.get("priceEvidenceSource")
        or quote_analysis["priceEvidenceSource"],
        "priceEvidenceSourceLabel": path.get("priceEvidenceSourceLabel"),
        "priceEvidenceMode": quote_analysis["priceEvidenceMode"],
        "routeUsableForObservation": route_usable,
        "routeHasLiquidityEvidence": path.get("routeHasLiquidityEvidence"),
        "chainReviewEligible": chain_review_eligible,
        "diagnosticOnly": not chain_review_eligible,
        "overviewRoute": overview_route,
        "overviewProfit": overview_profit,
        "flashLoanLinkage": linkage,
        "hopCandidates": hop_candidates,
        "hopCandidateCombinationCount": path.get("hopCandidateCombinationCount") or 1,
        "quoteStatus": quote_status,
        "quoteAnalysis": quote_analysis,
        "sizeAnalysis": {},
        "quoteInputs": {
            "candidatePools": route_pools[:5],
            "routePools": route_pools,
            "quoteRequests": [
                {
                    "venue": "manager",
                    "quoteMode": "manager_overview_projection",
                    "status": quote_status,
                    "routeSymbols": route_symbols,
                    "routeAddresses": route_addresses,
                    "routeTokenPath": route_token_path,
                    "pools": pool_ids,
                    "chainId": int(chain_id),
                }
            ],
        },
    }


def _quote_route_group_manager_dto(
    group: dict[str, Any],
    *,
    chain_id: int,
    anchors: set[str],
    default_route_framework_source: str,
) -> dict[str, Any]:
    paths = [
        _quote_route_manager_dto(
            path,
            chain_id=chain_id,
            default_route_framework_source=default_route_framework_source,
        )
        for path in group.get("paths") or []
        if isinstance(path, dict)
        and (not anchors or _quote_route_anchor(path) in anchors)
    ]
    pool_dependencies = [
        _quote_route_pool_dto(pool)
        for pool in group.get("poolDependencies") or []
        if isinstance(pool, dict)
    ]
    return {
        "flashLoanToken": dict(_mapping_value(group.get("flashLoanToken"))),
        "startToken": dict(_mapping_value(group.get("startToken"))),
        "endToken": dict(_mapping_value(group.get("endToken"))),
        "paths": paths,
        "poolDependencies": pool_dependencies,
        "routeCount": len(paths),
        "poolDependencyCount": len(pool_dependencies),
        "borrowable": True,
        "borrowableBasis": "manager_closed_loop_anchor",
    }


def _quote_route_pool_dto(pool: dict[str, Any]) -> dict[str, Any]:
    pool_key = dict(_mapping_value(pool.get("poolKey")))
    currency0 = (
        pool_key.get("currency0")
        or pool_key.get("token0")
        or pool.get("token0")
        or ""
    )
    currency1 = (
        pool_key.get("currency1")
        or pool_key.get("token1")
        or pool.get("token1")
        or ""
    )
    fee = pool_key.get("fee", pool.get("fee"))
    tick_spacing = pool_key.get("tickSpacing", pool.get("tickSpacing"))
    hooks = pool_key.get("hooks", pool.get("hooks"))
    pool_id = str(pool.get("poolId") or pool.get("pool") or "").strip()
    token0_symbol = str(pool.get("token0Symbol") or "").strip()
    token1_symbol = str(pool.get("token1Symbol") or "").strip()
    pair_symbol = (
        f"{token0_symbol}/{token1_symbol}"
        if token0_symbol and token1_symbol
        else ""
    )
    mapped = {
        "pool": str(pool.get("pool") or pool_id).strip(),
        "poolId": pool_id,
        "poolManager": str(pool.get("poolManager") or "").strip(),
        "protocol": str(pool.get("protocol") or "uniswap_v4").strip(),
        "dex": str(pool.get("dex") or "uniswap_v4").strip(),
        "token0": str(pool.get("token0") or "").strip(),
        "token1": str(pool.get("token1") or "").strip(),
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "token0KeyId": str(pool.get("token0KeyId") or "").strip(),
        "token1KeyId": str(pool.get("token1KeyId") or "").strip(),
        "pairSymbol": pair_symbol,
        "symbolPair": pair_symbol,
        "fee": fee,
        "feeLabel": pool.get("feeLabel"),
        "tickSpacing": tick_spacing,
        "hooks": hooks,
        "routeSymbols": list(pool.get("routeSymbols") or []),
        "routeText": pool.get("routeText"),
        "dynamicFee": bool(pool.get("dynamicFee")),
        "specialFee": bool(pool.get("specialFee")),
        "hasLiquidity": pool.get("hasLiquidity"),
        "liquidity": pool.get("liquidity"),
        "liquidityIndex": pool.get("liquidityIndex"),
        "price": pool.get("price"),
        "depthStatus": pool.get("depthStatus"),
        "depthBand": dict(_mapping_value(pool.get("depthBand"))),
        "liquidityReadAt": pool.get("liquidityReadAt"),
        "liquidityBlockNumber": pool.get("liquidityBlockNumber"),
        "evidenceSource": pool.get("evidenceSource"),
        "usableForObservation": bool(pool.get("usableForObservation")),
        "poolKey": {
            "currency0": str(currency0 or "").strip(),
            "currency1": str(currency1 or "").strip(),
            "fee": fee,
            "tickSpacing": tick_spacing,
            "hooks": str(hooks or "").strip(),
        },
    }
    status = str(pool.get("status") or pool.get("depthStatus") or "").strip()
    if status:
        mapped["status"] = status
    return mapped


def _quote_route_pool_has_identity(pool: dict[str, Any]) -> bool:
    pool_key = _mapping_value(pool.get("poolKey"))
    return bool(
        str(pool.get("poolId") or "").strip()
        and str(pool_key.get("currency0") or "").strip()
        and str(pool_key.get("currency1") or "").strip()
        and pool_key.get("fee") is not None
        and pool_key.get("tickSpacing") is not None
        and str(pool_key.get("hooks") or "").strip()
    )


def _quote_route_pool_ids(pools: list[dict[str, Any]]) -> list[str]:
    pool_ids: list[str] = []
    for pool in pools:
        pool_id = str(pool.get("poolId") or pool.get("pool") or "").strip()
        if pool_id and pool_id not in pool_ids:
            pool_ids.append(pool_id)
    return pool_ids
