from __future__ import annotations

from typing import Any

from market.binance_market.dex_quote_route_common import (
    build_quote_analysis_defaults,
    build_quote_inputs_bundle,
    build_quote_request_specs,
    build_quote_verification_payload,
    quote_route_status,
)
from dex.error_stack import route_error_stack
from core.dex_provenance import build_price_evidence_payload


def build_quote_route_payload(
    *,
    route: dict[str, Any],
    route_symbols: list[str],
    route_addresses: list[str],
    route_tokens: list[dict[str, Any]],
    pools: list[dict[str, Any]],
    quote_analysis: dict[str, Any],
    size_analysis: dict[str, Any],
    chain_id: int | None,
    path_source: str,
    chain_review_eligible: bool = True,
    settlement: dict[str, Any],
    price_evidence_source: str | None = None,
    price_evidence_mode: str | None = None,
) -> dict[str, Any]:
    linkage = route.get("flashLoanLinkage") or {}
    overview_route = route.get("overviewRoute") or {}
    uniswapx_fallback = route.get("uniswapxFallback") if isinstance(route.get("uniswapxFallback"), dict) else {}
    uniswapx_middle_hop = (
        uniswapx_fallback.get("middleHop")
        if isinstance(uniswapx_fallback.get("middleHop"), dict)
        and uniswapx_fallback.get("status") in {"selected", "filled_via_uniswapx_middle_hop"}
        else None
    )
    quote_analysis = build_quote_analysis_defaults(
        quote_analysis=quote_analysis,
        route_symbols=route_symbols,
        price_evidence_source=price_evidence_source
        or (quote_analysis or {}).get("priceEvidenceSource")
        or route.get("priceEvidenceSource")
        or "cached_framework_live_confirmation",
        price_evidence_mode=price_evidence_mode
        or (quote_analysis or {}).get("priceEvidenceMode")
        or route.get("priceEvidenceMode")
        or "framework_reused_live_confirmation",
    )
    size_analysis = dict(size_analysis or {})
    quote_status = quote_route_status(quote_analysis)
    quote_requests = build_quote_request_specs(
        pair_symbol=str(route.get("symbolPair") or ""),
        pools=pools,
        route_symbols=route_symbols,
        route_addresses=route_addresses,
        route_tokens=route_tokens,
        amm_quote_mode="pool_exact_input_probe",
        amm_status=quote_status,
        v4_quote_mode="uniswap_v4_sdk_exact_input_route" if len(route_symbols) > 2 else "uniswap_v4_sdk_exact_input_single",
        v4_status="sdk_adapter_required" if quote_analysis.get("requiresLiveQuoter") else "quoteable_after_static_probe",
        uniswapx_quote_mode="intent_exact_input",
        uniswapx_status="quoteable_after_static_probe",
        uniswapx_middle_hop=uniswapx_middle_hop,
        chain_id=chain_id,
        settlement=settlement,
        adapter_hints=[
            "pool_manager_state_required",
            "hook_aware_quote_required",
            "multi_hop_path_must_follow_market_observation_route",
            "sdk_or_adapter_must_return_amount_out_raw",
        ],
    )
    quote_inputs = build_quote_inputs_bundle(
        candidate_pools=pools[:5],
        route_pools=pools,
        quote_requests=quote_requests,
        conditions=[
            "候选池必须保持可执行深度，不能只依赖静态池子列表。",
            "路径来源必须与市场观察页面展示的闭环路径一致。",
            "AMM 报价必须通过滑点、gas、闪电贷费用和最小利润阈值检查。",
            "UniswapX 需要意图报价请求，并确认求解器或填单方可用后才能执行。",
        ],
    )
    route_strategy = (
        route.get("strategy")
        or linkage.get("strategy")
        or (
            "quote_from_market_observation"
            if path_source == "market_observation"
            else "quote_candidate_from_borrow_anchor_path"
        )
    )
    route_payload = {
        "symbolPair": route.get("symbolPair"),
        "overviewRoute": overview_route,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeTokenPath": route_tokens,
        "borrowSymbols": linkage.get("borrow_symbols") or [],
        "strategy": route_strategy,
        "pathSource": path_source,
        "tableSource": "dex_table",
        "referencedTable": "ob_table",
        "routeFrameworkSource": route.get("routeFrameworkSource") or path_source,
        "chainReviewEligible": bool(chain_review_eligible),
        "diagnosticOnly": not bool(chain_review_eligible),
        "priceEvidenceSource": quote_analysis.get("priceEvidenceSource")
        or (
            "live_quoter_required"
            if quote_analysis.get("requiresLiveQuoter") or quote_analysis.get("adapterRequired")
            else "static_reference_probe"
        ),
        "priceEvidenceMode": quote_analysis.get("priceEvidenceMode")
        or (
            "live_required"
            if quote_analysis.get("requiresLiveQuoter") or quote_analysis.get("adapterRequired")
            else "reference_only"
        ),
        "routeUsableForObservation": route.get("routeUsableForObservation"),
        "routeHasLiquidityEvidence": route.get("routeHasLiquidityEvidence"),
        "hopCandidates": list(route.get("hopCandidates") or []),
        "hopCandidateCounts": dict(route.get("hopCandidateCounts") or {}),
        "hopCandidateCombinationCount": max(1, int(route.get("hopCandidateCombinationCount") or 1)),
        "poolPathKey": route.get("poolPathKey") or "",
        "hopCandidateSelection": {
            "selectionBasis": (
                route.get("selectionBasis")
                or (list(route.get("hopCandidates") or [])[0].get("selectionBasis") if list(route.get("hopCandidates") or []) else "tvl_depth_desc")
            ),
            "preferredPoolIds": [
                str(candidate.get("preferredPoolId") or "").strip()
                for candidate in route.get("hopCandidates") or []
                if isinstance(candidate, dict) and str(candidate.get("preferredPoolId") or "").strip()
            ],
        },
        "hopCandidateReplacement": dict(route.get("hopCandidateReplacement") or {}),
        "uniswapxFallback": dict(uniswapx_fallback),
        "temporaryRoute": bool(route.get("temporaryRoute")),
        "temporaryRoutePolicy": dict(route.get("temporaryRoutePolicy") or {}),
        "temporaryRouteSnapshot": dict(route.get("temporaryRouteSnapshot") or {}),
        "notForDefaultScan": bool(route.get("notForDefaultScan")),
        "referencePoolPathKey": str(
            route.get("referencePoolPathKey")
            or route.get("defaultPoolPathKey")
            or ""
        ),
        "recommendedFirstPriority": bool(
            route.get("recommendedFirstPriority")
            or overview_route.get("recommendedFirstPriority")
        ),
        "routeRecommendationBasis": (
            route.get("routeRecommendationBasis")
            or overview_route.get("routeRecommendationBasis")
            or "tvl_depth_desc"
        ),
        "poolCount": len(pools),
        "routePools": pools,
        "executionPools": pools,
        "preferredPools": pools,
        "strictQualityPools": pools,
        "quoteStatus": quote_status,
        "quoteVerification": build_quote_verification_payload(quote_analysis=quote_analysis),
        "quoteAnalysis": quote_analysis,
        "sizeAnalysis": size_analysis,
        "quoteVenueCount": len(quote_requests),
        "quoteInputs": quote_inputs,
        "settlementRails": route.get("settlementRails") or [],
    }
    route_payload.update(
        build_price_evidence_payload(
            source=route_payload["priceEvidenceSource"],
            mode=route_payload["priceEvidenceMode"],
        )
    )
    evidence = quote_analysis.get("live_onchain_evidence")
    route_payload["errorStack"] = route_error_stack(
        status=quote_status,
        message=quote_analysis.get("failureReason") or quote_analysis.get("error"),
        evidence=evidence if isinstance(evidence, dict) else None,
        blockers=list(quote_analysis.get("blockers") or quote_analysis.get("blocked_reasons") or []),
        existing=quote_analysis.get("errorStack"),
    )
    return route_payload
