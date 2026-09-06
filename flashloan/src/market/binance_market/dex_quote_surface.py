from __future__ import annotations

import os
from typing import Any, Callable
from urllib.parse import quote

from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID
from market.binance_market.dex_surface_analysis import DEFAULT_QUOTE_PROBE_NOTIONAL_USD
from market.binance_market.dex_quote_route_builder import build_quote_route_payload
from market.binance_market.dex_quote_route_common import (
    build_quote_analysis_defaults,
    build_quote_inputs_bundle,
    build_quote_request_specs,
    build_quote_verification_payload,
    quote_route_status,
)


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else 0.0


def _pool_address(pool: dict[str, Any]) -> str:
    return str(pool.get("pool") or pool.get("address") or "").strip()


def _framework_route_pools(route_payload: dict[str, Any]) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = []
    quote_inputs = route_payload.get("quoteInputs") if isinstance(route_payload.get("quoteInputs"), dict) else {}
    for key in ("routePools", "candidatePools"):
        for pool in quote_inputs.get(key) or []:
            if isinstance(pool, dict):
                pools.append(pool)
    for key in ("preferredPools", "strictQualityPools", "executionPools", "rejectedPools"):
        for pool in route_payload.get(key) or []:
            if isinstance(pool, dict):
                pools.append(pool)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pool in pools:
        pool_id = _pool_address(pool) or str(pool.get("pool") or "").strip().lower()
        if not pool_id or pool_id in seen:
            continue
        seen.add(pool_id)
        deduped.append(pool)
    return deduped


def build_quote_routes_payload_from_overview(
    overview: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    route_quote_analysis_fn: Callable[..., dict[str, Any]],
    route_size_analysis_fn: Callable[..., dict[str, Any]],
    quote_probe_notional_usd: float = DEFAULT_QUOTE_PROBE_NOTIONAL_USD,
    price_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    paths = [path for path in overview.get("arbitragePaths") or [] if isinstance(path, dict)]
    summary = dict(overview.get("summary") or {})
    market_observation = overview.get("marketObservation") if isinstance(overview.get("marketObservation"), dict) else {}
    settlement = overview.get("dexSettlement") if isinstance(overview.get("dexSettlement"), dict) else {}
    pool_static_table = overview.get("poolStaticTable") if isinstance(overview.get("poolStaticTable"), dict) else {}
    routes: list[dict[str, Any]] = []
    observed_net_profits: list[float] = []
    executable_net_profits: list[float] = []
    best_observed_notional = None
    best_observed_profit = None
    best_executable_notional = None
    best_executable_profit = None
    best_token_basis_notional = None
    best_token_basis_symbol = None
    best_token_basis_amount = None
    best_token_basis_bps = None
    force_live_refresh = str(os.getenv("DEX_QUOTE_ROUTE_FORCE_REFRESH", "")).strip().lower() in {"1", "true", "yes", "on"}
    size_probe_limit = max(0, int(os.getenv("DEX_QUOTE_ROUTE_SIZE_PROBE_LIMIT", "3") or 3))
    probe_all_testnet_routes = (
        chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        and str(os.getenv("DEX_QUOTE_ROUTE_PROBE_ALL_TESTNET_ROUTES", "")).strip().lower()
        in {"1", "true", "yes", "on"}
    )

    for route_index, path in enumerate(paths, start=1):
        route = dict(path)
        overview_route = route.get("overviewRoute") or {}
        route_symbols = list(route.get("routeSymbols") or overview_route.get("pathSymbols") or [])
        route_addresses = list(route.get("routeAddresses") or [])
        route_tokens = list(route.get("routeTokenPath") or [])
        pools = _framework_route_pools(route)
        base_quote_analysis = route.get("quoteAnalysis") or route.get("overviewProfit") or {}
        quote_analysis = build_quote_analysis_defaults(
            quote_analysis=base_quote_analysis,
            route_symbols=route_symbols,
            price_evidence_source=route.get("priceEvidenceSource")
            or base_quote_analysis.get("priceEvidenceSource")
            or "cached_framework_live_confirmation",
            price_evidence_mode=route.get("priceEvidenceMode")
            or base_quote_analysis.get("priceEvidenceMode")
            or "framework_reused_live_confirmation",
            notional_usd=quote_probe_notional_usd,
        )
        analysis_route = dict(route)
        if not route.get("quoteInputs"):
            quote_requests = build_quote_request_specs(
                pair_symbol=str(route.get("symbolPair") or ""),
                pools=pools,
                route_symbols=route_symbols,
                route_addresses=route_addresses,
                route_tokens=route_tokens,
                amm_quote_mode="overview_route_snapshot",
                amm_status="waiting_live_price",
                v4_quote_mode="overview_route_snapshot",
                v4_status="waiting_live_price",
                uniswapx_quote_mode="intent_exact_input",
                uniswapx_status="waiting_live_price",
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
                    "报价与路由必须基于统一的实时复核结果，不再从零遍历静态池子",
                    "需要链上实时 Quoter 复核后才能进入自动交易",
                ],
            )
        else:
            quote_inputs = dict(route.get("quoteInputs") or {})
        if pools and (force_live_refresh or not base_quote_analysis.get("steps")):
            analysis_route["quoteInputs"] = quote_inputs
            live_analysis = route_quote_analysis_fn(analysis_route, price_map=price_map or {}, chain_id=chain_id)
            if isinstance(live_analysis, dict) and (live_analysis.get("steps") or live_analysis.get("complete")):
                quote_analysis = build_quote_analysis_defaults(
                    quote_analysis=live_analysis,
                    route_symbols=route_symbols,
                    price_evidence_source=live_analysis.get("priceEvidenceSource")
                    or quote_analysis.get("priceEvidenceSource")
                    or "cached_framework_live_confirmation",
                    price_evidence_mode=live_analysis.get("priceEvidenceMode")
                    or quote_analysis.get("priceEvidenceMode")
                    or "framework_reused_live_confirmation",
                    notional_usd=quote_probe_notional_usd,
                )
        size_analysis = dict(route.get("sizeAnalysis") or {})
        should_probe_size = bool(
            size_analysis
            or probe_all_testnet_routes
            or route_index <= size_probe_limit
            or quote_analysis.get("livePriceVerified")
            or quote_analysis.get("executableCandidate")
            or quote_analysis.get("chainSubmissionCandidate")
        )
        if should_probe_size:
            size_analysis = route_size_analysis_fn(analysis_route, price_map=price_map or {}, chain_id=chain_id)
            size_analysis = dict(size_analysis)
            recommended_analysis = size_analysis.get("recommendedQuoteAnalysis")
            if isinstance(recommended_analysis, dict):
                quote_analysis = build_quote_analysis_defaults(
                    quote_analysis=recommended_analysis,
                    route_symbols=route_symbols,
                    price_evidence_source=recommended_analysis.get("priceEvidenceSource"),
                    price_evidence_mode=recommended_analysis.get("priceEvidenceMode"),
                    notional_usd=quote_probe_notional_usd,
                )
        path_source = route.get("pathSource") or overview.get("routeFrameworkSource") or "market_observation"
        route_payload = build_quote_route_payload(
            route=route,
            route_symbols=route_symbols,
            route_addresses=route_addresses,
            route_tokens=route_tokens,
            pools=pools,
            quote_analysis=quote_analysis,
            size_analysis=size_analysis,
            chain_id=chain_id,
            path_source=path_source,
            settlement=settlement,
            price_evidence_source=quote_analysis.get("priceEvidenceSource"),
            price_evidence_mode=quote_analysis.get("priceEvidenceMode"),
        )
        routes.append(route_payload)
        observed_value = quote_analysis.get("referenceNetProfitUsd")
        if observed_value is not None:
            observed_net_profits.append(_number(observed_value))
        executable_value = quote_analysis.get("netProfitUsd")
        if quote_analysis.get("executableCandidate") and executable_value is not None:
            executable_net_profits.append(_number(executable_value))
        if best_observed_profit is None and quote_analysis.get("referenceNetProfitUsd") is not None:
            best_observed_profit = quote_analysis.get("referenceNetProfitUsd")
            best_observed_notional = quote_analysis.get("notionalUsd")
        if best_executable_profit is None and quote_analysis.get("netProfitUsd") is not None:
            best_executable_profit = quote_analysis.get("netProfitUsd")
            best_executable_notional = quote_analysis.get("notionalUsd")
        if quote_analysis.get("referenceReturnBasis") == "borrow_token" and best_token_basis_bps is None:
            best_token_basis_bps = quote_analysis.get("referenceNetReturnBps")
            best_token_basis_notional = quote_analysis.get("notionalUsd")
            best_token_basis_symbol = quote_analysis.get("referenceInputSymbol")
            best_token_basis_amount = quote_analysis.get("referenceNetProfitAmount")
    summary.update(
        {
            "probeNotionalUsd": quote_probe_notional_usd,
            "routeCount": len(routes),
            "candidateRouteCount": len(routes),
            "completeStaticQuoteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("complete")),
            "liveQuoterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("requiresLiveQuoter")),
            "sdkAdapterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("adapterRequired")),
            "profitableRouteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("profitable")),
            "chainSubmissionCandidateCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("chainSubmissionCandidate")),
            "nonStableChainSubmissionCandidateCount": sum(
                1
                for route in routes
                if (route.get("quoteAnalysis") or {}).get("chainSubmissionCandidate")
                and not (route.get("quoteAnalysis") or {}).get("borrowAssetIsStable")
            ),
            "bestNetProfitUsd": max(executable_net_profits) if executable_net_profits else None,
            "bestObservedNetProfitUsd": max(observed_net_profits) if observed_net_profits else None,
            "bestObservedBorrowNotionalUsd": best_observed_notional,
            "bestObservedBorrowNetProfitUsd": best_observed_profit,
            "bestExecutableBorrowNotionalUsd": best_executable_notional,
            "bestExecutableBorrowNetProfitUsd": best_executable_profit,
            "bestTokenBasisBorrowNotionalUsd": best_token_basis_notional,
            "bestTokenBasisInputSymbol": best_token_basis_symbol,
            "bestTokenBasisNetProfitAmount": best_token_basis_amount,
            "bestTokenBasisNetReturnBps": best_token_basis_bps,
            "directAnchorRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
            "bridgeRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "marketObservationRouteCount": len(overview.get("marketObservation", {}).get("chainSpecificObservationPairs") or []),
            "marketObservationSequenceCount": int((market_observation.get("summary") or {}).get("observationSequenceCount") or 0),
            "chainSpecificObservationPairCount": len(overview.get("marketObservation", {}).get("chainSpecificObservationPairs") or []),
            "pathSource": overview.get("routeFrameworkSource") or "market_observation",
            "uniswapxQuoteableRouteCount": sum(
                1
                for route in routes
                for request in (route.get("quoteInputs") or {}).get("quoteRequests") or []
                if request.get("venue") == "uniswapx"
            ),
        }
    )
    return {
        "mode": "dex_quote_routes",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "marketObservationSource": overview.get("marketObservationSource") or "computed",
        "marketObservationSourceStage": overview.get("marketObservationSourceStage")
        or overview.get("marketObservationSource")
        or "computed",
        "summary": summary,
        "routes": routes,
        "marketObservation": {
            "summary": market_observation.get("summary") or {},
            "chainSpecificObservationPairs": overview.get("marketObservation", {}).get("chainSpecificObservationPairs") or [],
        },
        "routeFrameworkSource": overview.get("routeFrameworkSource") or "market_observation",
        "dexSettlement": settlement,
        "poolStaticTable": pool_static_table,
    }


def build_quote_request_payload(
    *,
    quote_routes_payload: dict[str, Any],
    pair: str | None = None,
    venue: str | None = None,
    amount: str | None = None,
) -> dict[str, Any]:
    normalized_pair = str(pair or "").strip().upper()
    normalized_venue = str(venue or "amm_pool").strip().lower()
    matched = None
    for route in quote_routes_payload.get("routes") or []:
        if str(route.get("symbolPair") or "").strip().upper() == normalized_pair:
            matched = route
            break
    if matched is None:
        return {
            "ok": False,
            "mode": "dex_quote_request",
            "error": "candidate_pair_not_found",
            "pair": normalized_pair,
            "venue": normalized_venue,
            "availablePairs": [route.get("symbolPair") for route in quote_routes_payload.get("routes") or []],
        }
    requests = [
        request
        for request in (matched.get("quoteInputs") or {}).get("quoteRequests") or []
        if str(request.get("venue") or "").strip().lower() == normalized_venue
    ]
    quote_analysis = matched.get("quoteAnalysis") or {}
    quote_verification = matched.get("quoteVerification") or build_quote_verification_payload(quote_analysis=quote_analysis)
    return {
        "ok": bool(requests),
        "mode": "dex_quote_request",
        "pair": matched.get("symbolPair"),
        "venue": normalized_venue,
        "amount": amount,
        "routeSymbols": matched.get("routeSymbols") or [],
        "quoteStatus": quote_route_status(quote_analysis),
        "quoteVerification": quote_verification,
        "quoteAnalysis": quote_analysis,
        "quoteRequests": requests,
        "candidatePools": (matched.get("quoteInputs") or {}).get("candidatePools") or [],
        "status": "quote_request_ready" if requests else "venue_not_available_for_pair",
        "note": "This endpoint returns the exact quote request specification; live quote execution is handled by the configured venue adapter.",
    }


def build_submission_review_payload(
    *,
    attempts: list[dict[str, Any]] | None = None,
    groups: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    rows = list(attempts or [])

    def row_status(row: dict[str, Any]) -> str:
        return str(row.get("review_category") or row.get("category") or row.get("status") or row.get("state") or "").lower()

    success = [row for row in rows if row_status(row).endswith("success")]
    failed = [row for row in rows if "fail" in row_status(row)]
    blocked = [row for row in rows if "not_executable" in row_status(row) or row_status(row) == "quote_required"]
    return {
        "mode": "dex_submission_review",
        "source": source or "execution_attempts",
        "summary": {
            "attemptCount": len(rows),
            "successCount": len(success),
            "failedCount": len(failed),
            "blockedCount": len(blocked),
        },
        "groups": groups or {},
        "attempts": rows[:100],
    }
