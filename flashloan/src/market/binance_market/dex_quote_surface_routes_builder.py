from __future__ import annotations

from typing import Any, Callable

from market.binance_market.dex_quote_route_builder import build_quote_route_payload
from market.binance_market.dex_quote_surface_helpers import (
    _enrich_route_pools_from_index,
    _framework_route_pools,
    _number,
    _static_hop_steps,
)
from market.binance_market.dex_quote_route_common import (
    build_quote_analysis_defaults,
    build_quote_inputs_bundle,
    build_quote_request_specs,
)
from market.binance_market.dex_quote_route_policy import QuoteLiveTestPolicy


def _pool_usable_for_observation(pool: dict[str, Any]) -> bool:
    if pool.get("usableForObservation") is not None:
        return bool(pool.get("usableForObservation"))
    if pool.get("specialFee") or pool.get("dynamicFee"):
        return False
    has_liquidity = pool.get("hasLiquidity")
    if has_liquidity is not None:
        return bool(has_liquidity)
    try:
        return float(pool.get("liquidityIndex") or pool.get("liquidity") or 0) > 0
    except (TypeError, ValueError):
        return False


def _build_quote_routes_and_best(
    *,
    paths: list[dict[str, Any]],
    overview: dict[str, Any],
    chain_id: int | None,
    route_quote_analysis_fn: Callable[..., dict[str, Any]],
    route_size_analysis_fn: Callable[..., dict[str, Any]],
    quote_probe_notional_usd: float,
    price_map: dict[str, float] | None,
    v4_index_by_pool: dict[str, dict[str, Any]],
    settlement: dict[str, Any],
    route_live_test_policy: QuoteLiveTestPolicy,
    size_probe_limit: int,
) -> tuple[list[dict[str, Any]], list[float], list[float], dict[str, Any]]:
    price_map = price_map or {}
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
    best_token_basis_profit = None
    best_token_basis_profit_basis = None

    matched_route_index = 0
    for path in paths:
        if path.get("routeUsableForObservation") is False:
            continue
        if not route_live_test_policy.matches_anchor(path):
            continue
        matched_route_index += 1
        route = dict(path)
        overview_route = route.get("overviewRoute") or {}
        route_symbols = list(route.get("routeSymbols") or overview_route.get("pathSymbols") or [])
        route_addresses = list(route.get("routeAddresses") or [])
        route_tokens = list(route.get("routeTokenPath") or [])
        pools = _enrich_route_pools_from_index(
            _framework_route_pools(route),
            index_by_pool=v4_index_by_pool,
        )
        if path.get("routeUsableForObservation") is True:
            pools = [pool for pool in pools if _pool_usable_for_observation(pool)]
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
        path_source = route.get("pathSource") or overview.get("routeFrameworkSource") or "market_observation"
        source_table = str(route.get("tableSource") or overview.get("tableSource") or "").strip()
        chain_review_eligible = bool(
            str(path_source or "").strip() == "market_observation"
            and source_table in {"", "dex_table"}
        )
        should_run_live_route = bool(
            chain_review_eligible
            and pools
            and (
                route_live_test_policy.force_live_refresh
                or (
                    not base_quote_analysis.get("steps")
                    and (
                        route_live_test_policy.probe_all_testnet_routes
                        or matched_route_index <= route_live_test_policy.live_route_limit
                    )
                )
                or base_quote_analysis.get("executableCandidate")
            )
        )
        if should_run_live_route:
            analysis_route["quoteInputs"] = quote_inputs
            live_analysis = route_quote_analysis_fn(analysis_route, price_map=price_map, chain_id=chain_id)
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
            or route_live_test_policy.probe_all_testnet_routes
            or matched_route_index <= size_probe_limit
            or quote_analysis.get("livePriceVerified")
            or quote_analysis.get("executableCandidate")
            or quote_analysis.get("chainSubmissionCandidate")
        )
        if should_probe_size:
            size_analysis = route_size_analysis_fn(analysis_route, price_map=price_map, chain_id=chain_id)
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
            chain_review_eligible=chain_review_eligible,
            settlement=settlement,
            price_evidence_source=quote_analysis.get("priceEvidenceSource"),
            price_evidence_mode=quote_analysis.get("priceEvidenceMode"),
        )
        route_payload["chainReviewEligible"] = bool(
            chain_review_eligible and route_payload.get("tableSource") == "dex_table"
        )
        route_payload["diagnosticOnly"] = not route_payload["chainReviewEligible"]
        route_quote_analysis = route_payload.get("quoteAnalysis")
        if isinstance(route_quote_analysis, dict) and not route_quote_analysis.get("steps"):
            route_quote_analysis["steps"] = _static_hop_steps(
                pools=pools,
                route_symbols=route_symbols,
                route_addresses=route_addresses,
                route_quote_analysis=route_quote_analysis,
            )
            route_quote_analysis["stepCount"] = len(route_quote_analysis["steps"])
        routes.append(route_payload)
        observed_value = quote_analysis.get("referenceNetProfitUsd")
        if observed_value is not None:
            observed_net_profits.append(_number(observed_value))
        executable_value = quote_analysis.get("netProfitUsd")
        if quote_analysis.get("executableCandidate") and executable_value is not None:
            executable_net_profits.append(_number(executable_value))
        if observed_value is not None and (best_observed_profit is None or float(observed_value) > float(best_observed_profit)):
            best_observed_profit = observed_value
            best_observed_notional = quote_analysis.get("notionalUsd")
        if executable_value is not None and (best_executable_profit is None or float(executable_value) > float(best_executable_profit)):
            best_executable_profit = executable_value
            best_executable_notional = quote_analysis.get("notionalUsd")
        token_basis_profit = (
            quote_analysis.get("referenceNetProfitAmount")
            or quote_analysis.get("netProfitAmount")
            or quote_analysis.get("referenceNetProfitUsd")
            or quote_analysis.get("netProfitUsd")
        )
        token_basis_profit_basis = (
            "usd"
            if quote_analysis.get("referenceNetProfitUsd") is not None or quote_analysis.get("netProfitUsd") is not None
            else "borrow_token"
        )
        if (
            quote_analysis.get("referenceReturnBasis") == "borrow_token"
            and token_basis_profit is not None
            and (best_token_basis_profit is None or float(token_basis_profit) > float(best_token_basis_profit))
        ):
            best_token_basis_profit = token_basis_profit
            best_token_basis_profit_basis = token_basis_profit_basis
            best_token_basis_bps = quote_analysis.get("referenceNetReturnBps")
            best_token_basis_notional = quote_analysis.get("notionalUsd")
            best_token_basis_symbol = quote_analysis.get("referenceInputSymbol")
            best_token_basis_amount = quote_analysis.get("referenceNetProfitAmount")

    return (
        routes,
        observed_net_profits,
        executable_net_profits,
        {
            "best_observed_notional": best_observed_notional,
            "best_observed_profit": best_observed_profit,
            "best_executable_notional": best_executable_notional,
            "best_executable_profit": best_executable_profit,
            "best_token_basis_notional": best_token_basis_notional,
            "best_token_basis_symbol": best_token_basis_symbol,
            "best_token_basis_amount": best_token_basis_amount,
            "best_token_basis_bps": best_token_basis_bps,
            "best_token_basis_profit": best_token_basis_profit,
            "best_token_basis_profit_basis": best_token_basis_profit_basis,
        },
    )
