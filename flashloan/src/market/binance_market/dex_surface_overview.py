"""Overview payload and execution-size helpers for the DEX surface facade."""

from __future__ import annotations

import os
from typing import Any

from market.binance_market.dex_surface_analysis import (
    apply_route_overview_profit,
    build_overview_profit_payload,
    build_route_size_analysis,
)
from market.binance_market.dex_surface_quotes import _route_quote_analysis as _default_route_quote_analysis
from market.binance_market.dex_surface_types import (
    _int,
    _norm_symbol,
    _number,
    _raw_from_human,
)
from market.binance_market.dex_surface_observation_chain import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    _chain_network_name,
)
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_CHAIN_ID
QUOTE_PROBE_NOTIONAL_USD = 1000.0


def _closed_loop_dedup_key(
    anchor: str,
    route_symbols: list[str],
    route_addresses: list[str] | None = None,
) -> tuple[str, tuple[str, ...]]:
    route_addresses = [str(value or "").lower() for value in (route_addresses or []) if str(value or "").strip()]
    if route_addresses and len(route_addresses) == len(route_symbols):
        anchor_key = route_addresses[0]
        mids = tuple(route_addresses[1:-1]) if len(route_addresses) >= 3 else tuple(route_addresses)
    else:
        anchor_key = anchor
        mids = tuple(route_symbols[1:-1]) if len(route_symbols) >= 3 and route_symbols[0] == route_symbols[-1] else tuple(route_symbols)
    if mids and tuple(reversed(mids)) < mids:
        mids = tuple(reversed(mids))
    return (anchor_key, mids)


def _compat_module():
    try:
        from market.binance_market import dex_surfaces

        return dex_surfaces
    except Exception:
        return None


def _route_quote_analysis(*args: Any, **kwargs: Any):
    module = _compat_module()
    function = getattr(module, "_route_quote_analysis", None) if module is not None else None
    if callable(function) and function is not _route_quote_analysis:
        return function(*args, **kwargs)
    return _default_route_quote_analysis(*args, **kwargs)


def _v4_execution_size_probe_enabled() -> bool:
    raw = os.getenv("DEX_V4_EXECUTION_SIZE_PROBE_ENABLED")
    return raw is not None and str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _overview_pool_payload_from_static_observation(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": {},
        "pairs": [],
        "poolStaticTable": observation.get("poolStaticTable") or {},
        "flashLoanLiquidity": observation.get("flashLoanLiquidity") or {},
        "dexSettlement": observation.get("dexSettlement") or {},
    }


def group_overview_paths_by_flash_loan_token(
    overview_paths: list[dict[str, Any]],
    *,
    borrowable_symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Group overview routes by their closed-loop borrowable anchor token."""

    borrowable = {
        _norm_symbol(value)
        for value in borrowable_symbols or []
        if _norm_symbol(value)
    }
    groups: dict[str, dict[str, Any]] = {}
    seen_routes: set[tuple[str, tuple[str, ...]]] = set()
    for path in overview_paths:
        if not isinstance(path, dict):
            continue
        overview_route = path.get("overviewRoute") if isinstance(path.get("overviewRoute"), dict) else {}
        linkage = path.get("flashLoanLinkage") if isinstance(path.get("flashLoanLinkage"), dict) else {}
        route_symbols = [_norm_symbol(value) for value in path.get("routeSymbols") or [] if _norm_symbol(value)]
        route_addresses = [str(value or "").strip() for value in path.get("routeAddresses") or [] if str(value or "").strip()]
        anchor = _norm_symbol(overview_route.get("anchorBorrowSymbol"))
        if not anchor:
            borrow_symbols = [_norm_symbol(value) for value in linkage.get("borrow_symbols") or [] if _norm_symbol(value)]
            anchor = borrow_symbols[0] if borrow_symbols else (route_symbols[0] if route_symbols else "")
        if not anchor:
            continue
        anchor_address = overview_route.get("anchorBorrowAddress") or overview_route.get("anchorAddress")
        if not anchor_address and route_addresses:
            anchor_address = route_addresses[0]
        dedup_key = _closed_loop_dedup_key(anchor, route_symbols, route_addresses)
        is_duplicate = dedup_key in seen_routes
        seen_routes.add(dedup_key)
        group_key = str(anchor_address or "").strip().lower() or anchor
        group = groups.setdefault(
            group_key,
            {
                "flashLoanToken": {"symbol": anchor, "address": str(anchor_address or "").strip()},
                "startToken": {"symbol": anchor, "address": str(anchor_address or "").strip()},
                "endToken": {"symbol": anchor, "address": str(anchor_address or "").strip()},
                "borrowable": anchor in borrowable,
                "pathCount": 0,
                "poolDependencyCount": 0,
                "paths": [],
                "poolDependencies": [],
                "poolDependencyIndex": {},
            },
        )
        if not is_duplicate:
            group["paths"].append(path)
            group["pathCount"] += 1
        for pool in path.get("executionPools") or path.get("preferredPools") or []:
            if not isinstance(pool, dict):
                continue
            pool_id = str(pool.get("pool") or pool.get("poolId") or pool.get("pairAddress") or "").strip().lower()
            if pool_id and pool_id not in group["poolDependencyIndex"]:
                group["poolDependencyIndex"][pool_id] = True
                group["poolDependencies"].append(pool)
        group["poolDependencyCount"] = len(group["poolDependencies"])

    result = []
    ordered = sorted(
        groups.items(),
        key=lambda item: (0 if item[1]["borrowable"] else 1, item[0]),
    )
    for _, group in ordered:
        group.pop("poolDependencyIndex", None)
        result.append(group)
    return result


def _overview_paths_from_static_summary(snapshot: dict[str, Any], *, chain_id: int | None) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    seen_routes: set[tuple[str, tuple[str, ...]]] = set()
    for summary in snapshot.get("closedLoopRouteSummaries") or []:
        if not isinstance(summary, dict):
            continue
        path_symbols = [_norm_symbol(value) for value in summary.get("pathSymbols") or [] if _norm_symbol(value)]
        if not path_symbols:
            path_symbols = [
                _norm_symbol(value)
                for value in str(summary.get("pathText") or "").split("->")
                if _norm_symbol(value)
            ]
        path_text = str(summary.get("pathText") or " -> ".join(path_symbols)).strip()
        if len(path_symbols) < 4 or path_symbols[0] != path_symbols[-1] or not path_text:
            continue
        anchor = _norm_symbol(summary.get("anchorSymbol") or path_symbols[0])
        dedup_key = _closed_loop_dedup_key(anchor, path_symbols)
        if dedup_key in seen_routes:
            continue
        seen_routes.add(dedup_key)
        target = _norm_symbol(summary.get("counterSymbol") or (path_symbols[-2] if len(path_symbols) > 2 else path_symbols[-1]))
        route_addresses = [str(address or "").strip() for address in summary.get("routeAddresses") or [] if str(address or "").strip()]
        if not route_addresses:
            route_addresses = list(summary.get("anchorAddresses") or [])
        route_pools = [
            dict(pool)
            for pool in summary.get("routePools") or []
            if isinstance(pool, dict)
        ]
        route_tokens = [
            {
                "symbol": symbol,
                "address": route_addresses[index] if index < len(route_addresses) else "",
            }
            for index, symbol in enumerate(path_symbols)
        ]
        kind = "bridge_pair"
        paths.append(
            {
                "symbolPair": path_text,
                "routeSymbols": path_symbols,
                "routeAddresses": route_addresses,
                "routeTokenPath": route_tokens,
                "tokenXSymbol": anchor,
                "tokenYSymbol": target,
                "executionPools": route_pools,
                "preferredPools": route_pools,
                "strictQualityPools": route_pools,
                "routePools": route_pools,
                "overviewRoute": {
                    "kind": kind,
                    "anchorBorrowSymbol": anchor,
                    "targetSymbol": target,
                    "bridgeSymbols": path_symbols[1:-1],
                    "pathSymbols": path_symbols,
                    "pathTokens": route_tokens,
                    "pathAddresses": route_addresses,
                    "pathText": path_text,
                    "candidatePairSymbols": list(summary.get("pairSymbols") or []),
                    "analysisStage": "market_observation_closed_loop",
                    "nextStep": "quotes_and_route_analysis",
                    "observationSource": summary.get("source") or "market_observation",
                    "marketObservationRoute": True,
                    "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
                },
                "flashLoanLinkage": {
                    "strategy": (
                        "robinhood_testnet_market_observation_closed_loop"
                        if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
                        else "market_observation_closed_loop"
                    ),
                    "borrow_symbols": [anchor] if anchor else [],
                    "route_symbols": path_symbols,
                    "route_addresses": route_addresses,
                    "ready_for_atomic_arbitrage": bool(route_pools),
                },
                "overviewProfit": {
                    "method": "static_overview_summary",
                    "notionalUsd": QUOTE_PROBE_NOTIONAL_USD,
                    "complete": False,
                    "requiresLiveQuoter": True,
                    "adapterRequired": True,
                    "profitable": False,
                    "executableCandidate": False,
                    "chainSubmissionCandidate": False,
                    "referenceReturnBasis": "borrow_token",
                    "referenceReturnTrusted": False,
                    "recommendationLevel": "requires_live_quote",
                    "recommendationAction": "requires live quoter verification",
                    "warnings": ["overview_summary_without_live_quote"],
                    "priceEvidenceSource": "chain_live_quote_required",
                    "priceEvidenceMode": "live_required",
                },
                "routeFrameworkSource": "market_observation",
                "pathSource": "market_observation",
                "marketObservationSourceStage": "cached_snapshot",
                "priceEvidenceSource": "chain_live_quote_required",
            }
        )
    return paths


def _overview_profit_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    return build_overview_profit_payload(analysis)


def _route_with_overview_profit(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
) -> dict[str, Any]:
    return apply_route_overview_profit(
        route,
        price_map=price_map,
        chain_id=chain_id,
        route_quote_analysis_fn=_route_quote_analysis,
    )


def _route_execution_size_probe(route_analysis: dict[str, Any], *, chain_id: int | None = None) -> dict[str, Any] | None:
    if chain_id not in {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID} or not _v4_execution_size_probe_enabled():
        return None
    steps = [
        step
        for step in route_analysis.get("steps") or []
        if isinstance(step, dict) and str(step.get("protocol") or step.get("dex") or "").strip().lower() == "uniswap_v4"
    ]
    if not steps:
        return None
    network = _chain_network_name(chain_id)
    if not network:
        return None
    module_name = (
        "dex.intent_trade.chains.robinhood.v4_quote_adapter"
        if chain_id == ROBINHOOD_CHAIN_ID
        else "dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter"
    )
    probe_module = __import__(module_name, fromlist=["probe_v4_executable_amount_from_pool_index"])
    probe_v4_executable_amount_from_pool_index = probe_module.probe_v4_executable_amount_from_pool_index
    max_probe_attempts = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_ATTEMPTS", "24") or 24))
    max_probe_multiplier = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_MULTIPLIER", "64") or 64))
    min_probe_amount_raw = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MIN_PROBE_AMOUNT_RAW", "1") or 1))
    step_probes: list[dict[str, Any]] = []
    successful_maxima: list[int] = []
    first_failures: list[int] = []
    total_attempts = 0
    for index, step in enumerate(steps, start=1):
        input_decimals = _int(step.get("inputDecimals"))
        if input_decimals is None:
            raise ValueError(
                f"missing exact token decimals for step {index} "
                f"{step.get('fromSymbol')}->{step.get('toSymbol')}"
            )
        amount_in_raw = _int(step.get("amountInRaw"))
        if amount_in_raw <= 0:
            amount_in_raw = _raw_from_human(step.get("inputAmount") or step.get("amountIn") or step.get("referenceInputAmount"), input_decimals)
        request = {
            "network": network,
            "chainId": chain_id,
            "pool": step.get("pool"),
            "tokenIn": step.get("tokenIn"),
            "tokenOut": step.get("tokenOut"),
            "amountInRaw": str(amount_in_raw or 0),
            "fee": step.get("fee") or step.get("feeBps"),
            "quoteMode": step.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
            "minProbeAmountRaw": str(min_probe_amount_raw),
            "maxProbeAttempts": str(max_probe_attempts),
            "maxProbeMultiplier": str(max_probe_multiplier),
        }
        try:
            result = probe_v4_executable_amount_from_pool_index(request)
            total_attempts += _int(result.get("probeAttempts"))
            maximum = _int(result.get("maxExecutableAmountRaw"))
            first_failure = _int(result.get("firstFailureAmountRaw"))
            probe_row = {
                "step": index,
                "pool": step.get("pool"),
                "tokenIn": step.get("tokenIn"),
                "tokenOut": step.get("tokenOut"),
                "amountInRaw": str(amount_in_raw or 0),
                "status": result.get("status"),
                "searchStatus": result.get("searchStatus"),
                "probeAttempts": result.get("probeAttempts"),
                "maxExecutableAmountRaw": result.get("maxExecutableAmountRaw"),
                "firstFailureAmountRaw": result.get("firstFailureAmountRaw"),
                "quoterAddress": result.get("quoterAddress"),
            }
            if maximum > 0:
                successful_maxima.append(maximum)
            if first_failure > 0:
                first_failures.append(first_failure)
        except Exception as exc:
            probe_row = {
                "step": index,
                "pool": step.get("pool"),
                "tokenIn": step.get("tokenIn"),
                "tokenOut": step.get("tokenOut"),
                "amountInRaw": str(amount_in_raw or 0),
                "status": "probe_failed",
                "searchStatus": "probe_failed",
                "error": str(exc),
            }
        step_probes.append(probe_row)
    if not step_probes:
        return None
    probe_success_count = len(successful_maxima)
    failed_count = len(step_probes) - probe_success_count
    status = "liquidity_probe_unexecutable" if probe_success_count == 0 else ("liquidity_probe_partial" if failed_count > 0 else "liquidity_probe_complete")
    search_status = "bounded" if first_failures else ("lower_bound_verified" if successful_maxima else "no_successful_amount_found")
    return {
        "status": status,
        "searchStatus": search_status,
        "probeAttempts": total_attempts,
        "stepProbes": step_probes,
        "inputAmountUnit": "per_hop",
        "maxExecutableAmountRaw": None,
        "firstFailureAmountRaw": None,
    }


def _route_size_analysis(route: dict[str, Any], *, price_map: dict[str, float], chain_id: int | None = None) -> dict[str, Any]:
    route_analysis = _route_quote_analysis(route, price_map=price_map, chain_id=chain_id)
    execution_size_probe = _route_execution_size_probe(route_analysis, chain_id=chain_id)

    def route_quote_analysis_with_probe(
        inner_route: dict[str, Any],
        *,
        price_map: dict[str, float],
        chain_id: int | None = None,
        notional_usd: float = QUOTE_PROBE_NOTIONAL_USD,
        token_basis_input_amount: float | None = None,
    ) -> dict[str, Any]:
        analysis = _route_quote_analysis(
            inner_route,
            price_map=price_map,
            chain_id=chain_id,
            notional_usd=notional_usd,
            token_basis_input_amount=token_basis_input_amount,
        )
        if execution_size_probe and inner_route is route:
            analysis = {**analysis, **execution_size_probe, "executionSizeProbe": execution_size_probe}
        return analysis

    return build_route_size_analysis(
        route,
        price_map=price_map,
        chain_id=chain_id,
        route_quote_analysis_fn=route_quote_analysis_with_probe,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
    )
