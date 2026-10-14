"""Market observation and route candidate construction helpers."""

from __future__ import annotations

from typing import Any, Callable

from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery as _default_query_flash_loan_pool_discovery
from core.market_profiles import DEFAULT_ROBINHOOD_CHAIN_ID as _ROBINHOOD_CHAIN_ID
from market.binance_market.dex_surface_graph import (
    _pair_token_entry,
    _pool_count,
    _pools_for_pair,
    _route_addresses_from_tokens,
    _route_pools,
    _route_score,
    _route_symbols_from_tokens,
    _token_graph,
    _token_key,
)
from market.binance_market.dex_surface_quotes import _route_quote_analysis as _default_route_quote_analysis
from market.binance_market.dex_surface_types import (
    _int,
    _is_stable_anchor,
    _norm_symbol,
    _pool_address,
    _pool_protocol,
    _pool_symbols,
    _pool_tokens,
    _pair_symbols,
    _route_symbol_allowed,
    _split_symbol_components,
    _token_address,
)
from market.binance_market.dex_surface_v4 import _pool_tvl_usd
from market.binance_market.dex_surface_observation_helpers import (
    _anchor_group_direction_rows,
    _anchor_group_direction_tokens,
    _best_pool_rows,
    _borrow_anchor_symbols,
    _borrowable_symbols_from_discovery,
    _candidate_anchor_symbols,
    _candidate_pairs,
    _borrowable_symbols,
    _chain_borrowable_symbols_from_pool_payload,
    _graph_stable_anchor_symbols,
    _group_token_pool_rows,
    _observation_anchor_symbols,
    _sequence_rows_by_counter,
    _token_instance_keys,
    _v4_poolmanager_balance_symbols,
)
from market.binance_market.dex_surface_observation_sequences import (
    _anchor_observation_sequences,
    _candidate_observation_groups,
    _closed_route_from_sequence,
    _hop_candidate_sets_from_pairs,
    _select_best_pool_rows,
    _route_anchor_symbol,
    _route_step_row,
    _sequence_pair_row,
)
from market.binance_market.dex_surface_observation_chain import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    _chain_observation_module,
    _chain_observation_function,
    _chain_network_name,
)
from market.binance_market.pool_candidates import dex_pool_candidates_payload

def query_flash_loan_pool_discovery(*args: Any, **kwargs: Any):
    compatibility = None
    try:
        from market.binance_market import dex_surfaces

        compatibility = getattr(dex_surfaces, "query_flash_loan_pool_discovery", None)
    except (AttributeError, ImportError, TypeError):
        compatibility = None
    if callable(compatibility) and compatibility is not query_flash_loan_pool_discovery:
        return compatibility(*args, **kwargs)
    return _default_query_flash_loan_pool_discovery(*args, **kwargs)


def _chain_db_first_only(chain_id: int | None, protocols: list[str] | None = None) -> bool:
    try:
        chain_is_robinhood = int(chain_id) in {4663, 46630}
    except (TypeError, ValueError):
        return False
    if not chain_is_robinhood:
        return False
    if not protocols:
        return True
    return "uniswap_v4" in {str(protocol or "").strip().lower() for protocol in protocols}


def _route_quote_analysis(*args: Any, **kwargs: Any):
    compatibility = None
    try:
        from market.binance_market import dex_surfaces

        compatibility = getattr(dex_surfaces, "_route_quote_analysis", None)
    except (AttributeError, ImportError, TypeError):
        compatibility = None
    if callable(compatibility) and compatibility is not _route_quote_analysis:
        return compatibility(*args, **kwargs)
    return _default_route_quote_analysis(*args, **kwargs)

def robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
):
    function = _chain_observation_function(chain_id, "bridge_pairs_for_anchor_groups")
    if function is None:
        return [], {"enabled": False, "status": "chain_observation_extension_missing"}
    return function(anchor_groups, chain_id=chain_id, protocols=protocols)

def robinhood_testnet_v4_fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
):
    function = _chain_observation_function(chain_id, "fund_anchor_pair_observation")
    if function is None:
        return {
            "enabled": False,
            "status": "chain_observation_extension_missing",
            "routeCount": 0,
            "associatedObservationPairCount": 0,
            "associatedObservationPoolCount": 0,
            "associatedObservationTokenCount": 0,
            "observationSequences": [],
            "associatedObservationPairs": [],
            "associatedObservationPools": [],
            "associatedObservationTokens": [],
        }
    return function(chain_id=chain_id, protocols=protocols)

def _v4_index_bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    pool_liquidity_loader: Callable[[], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bridge_function = robinhood_testnet_v4_bridge_pairs_for_anchor_groups
    try:
        from market.binance_market import dex_surfaces

        compatibility = getattr(dex_surfaces, "robinhood_testnet_v4_bridge_pairs_for_anchor_groups", None)
        if callable(compatibility) and compatibility is not bridge_function:
            bridge_function = compatibility
    except (AttributeError, ImportError, TypeError):
        pass
    return bridge_function(
        anchor_groups,
        chain_id=chain_id,
        protocols=protocols,
        **({"pool_liquidity_loader": pool_liquidity_loader} if pool_liquidity_loader is not None else {}),
    )

def _chain_fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    fund_function = robinhood_testnet_v4_fund_anchor_pair_observation
    try:
        from market.binance_market import dex_surfaces

        compatibility = getattr(dex_surfaces, "robinhood_testnet_v4_fund_anchor_pair_observation", None)
        if callable(compatibility) and compatibility is not fund_function:
            fund_function = compatibility
    except (AttributeError, ImportError, TypeError):
        pass
    return fund_function(chain_id=chain_id, protocols=protocols)

def _reentry_route_has_reference_profit(
    *,
    anchor_token: dict[str, Any],
    counter_token: dict[str, Any],
    first_pool: dict[str, Any],
    second_pool: dict[str, Any],
    price_map: dict[str, float],
) -> bool:
    route = {
        "routeTokenPath": [anchor_token, counter_token, anchor_token],
        "executionPools": [first_pool, second_pool],
        "preferredPools": [first_pool, second_pool],
        "strictQualityPools": [first_pool, second_pool],
    }
    analysis = _route_quote_analysis(route, price_map=price_map)
    reference_net_profit_usd = analysis.get("referenceNetProfitUsd")
    return reference_net_profit_usd is not None and float(reference_net_profit_usd) > 0.0

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


def _pool_has_explicit_observation_quality(pool: dict[str, Any]) -> bool:
    for key in (
        "usableForObservation",
        "specialFee",
        "dynamicFee",
        "hasLiquidity",
        "liquidityIndex",
        "liquidity",
    ):
        value = pool.get(key)
        if isinstance(value, str) and not value.strip():
            continue
        if value is not None:
            return True
    return False


def _route_usability_from_pairs(pairs: list[dict[str, Any]]) -> tuple[bool | None, bool]:
    """Return ``(route_usable, has_explicit_liquidity_evidence)`` for a route.

    Pools without explicit liquidity/special-fee flags are treated as usable for
    compatibility with legacy candidate tables. Routes that have explicit
    liquidity evidence must pass every hop to be considered usable.
    """
    any_explicit = False
    all_usable = True
    for pair in pairs:
        pools = _pools_for_pair(pair)
        if not pools:
            return False, True
        explicit_pools = [pool for pool in pools if _pool_has_explicit_observation_quality(pool)]
        if not explicit_pools:
            continue
        any_explicit = True
        if not any(_pool_usable_for_observation(pool) for pool in explicit_pools):
            all_usable = False
    return (all_usable if any_explicit else None, any_explicit)


def _route_usability_from_pools(pools: list[dict[str, Any]]) -> tuple[bool | None, bool]:
    """Return ``(route_usable, has_explicit_liquidity_evidence)`` for a pool list.

    Each pool represents one hop on the route.  Pools without explicit
    liquidity/special-fee flags are treated as usable for compatibility; once a
    route has explicit liquidity evidence, every hop must be usable.
    """
    if not pools:
        return False, True
    any_explicit = False
    all_usable = True
    for pool in pools:
        if not isinstance(pool, dict):
            return False, True
        if _pool_has_explicit_observation_quality(pool):
            any_explicit = True
            if not _pool_usable_for_observation(pool):
                all_usable = False
    return (all_usable if any_explicit else None, any_explicit)


def _pair_liquidity_evidence(pair: dict[str, Any]) -> dict[str, Any]:
    explicit_pools = [pool for pool in _pools_for_pair(pair) if _pool_has_explicit_observation_quality(pool)]
    if not explicit_pools:
        return {
            "pairSymbol": pair.get("symbolPair") or "",
            "pairAddress": pair.get("addressPair") or "",
            "usableForObservation": None,
        }
    return {
        "pairSymbol": pair.get("symbolPair") or "",
        "pairAddress": pair.get("addressPair") or "",
        "usableForObservation": any(_pool_usable_for_observation(pool) for pool in explicit_pools),
    }

def _route_candidate(
    *,
    kind: str,
    anchor: str,
    target: str,
    path_symbols: list[str],
    pairs: list[dict[str, Any]],
    bridge_symbols: list[str] | None = None,
    path_tokens: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pools = [pool for pair in pairs for pool in _pools_for_pair(pair)]
    route_usable, route_has_explicit = _route_usability_from_pairs(pairs)
    primary = pairs[-1] if pairs else {}
    route_tokens = path_tokens or [{"symbol": symbol, "address": ""} for symbol in path_symbols if symbol]
    route_symbols = _route_symbols_from_tokens(route_tokens)
    route_addresses = _route_addresses_from_tokens(route_tokens)
    route_pool_rows = [pool for pair in pairs for pool in _select_best_pool_rows(_pools_for_pair(pair))[:1]]
    hop_candidates = _hop_candidate_sets_from_pairs(pairs, route_tokens)
    hop_candidate_counts = {candidate["leg"]: candidate["poolCount"] for candidate in hop_candidates if isinstance(candidate, dict)}
    combination_count = 1
    for candidate in hop_candidates:
        if isinstance(candidate, dict):
            combination_count = max(1, combination_count * max(1, candidate.get("poolCount") or 0))
    pool_path_key = "|".join(
        [_pool_address(pool) for pool in route_pool_rows if _pool_address(pool)]
    )
    return {
        "symbolPair": " -> ".join(route_symbols),
        "addressPair": " -> ".join(address for address in route_addresses if address),
        "routeTokenPath": route_tokens,
        "routeAddresses": route_addresses,
        "tokenX": primary.get("tokenX"),
        "tokenY": primary.get("tokenY"),
        "tokenXSymbol": anchor,
        "tokenYSymbol": target,
        "classification": primary.get("classification") or {},
        "allPoolCount": str(len(pools)),
        "strictQualityPoolCount": str(len(pools)),
        "preferredPoolCount": str(len(pools)),
        "executionPoolCount": str(len(pools[:5])),
        "rejectedPoolCount": "0",
        "hasStrictQualityPools": bool(pools),
        "hasPreferredPools": bool(pools),
        "strictQualityPools": pools,
        "preferredPools": pools,
        "executionPools": pools[:5],
        "rejectedPools": [],
        "rejectReasonCounts": {},
        "routeUsableForObservation": route_usable,
        "routeHasLiquidityEvidence": route_has_explicit,
        "hopCandidates": hop_candidates,
        "hopCandidateCounts": hop_candidate_counts,
        "hopCandidateCombinationCount": combination_count,
        "poolPathKey": pool_path_key,
        "recommendedFirstPriority": True,
        "routeRecommendationBasis": "tvl_depth_desc",
        "flashLoanLinkage": {
            "strategy": "stable_anchor_bridge_candidate_path",
            "borrow_symbols": [anchor],
            "borrow_tokens": [route_addresses[0]] if route_addresses and route_addresses[0] else [],
            "route_symbols": route_symbols,
            "route_addresses": route_addresses,
            "candidate_combination_count": combination_count,
            "ready_for_atomic_arbitrage": bool(pools),
            "conditions": [
                "quote page must verify executable output after slippage",
                "route must repay the borrowed anchor asset in the same transaction",
                "gas and flash loan fee must be below gross spread",
            ],
        },
        "overviewRoute": {
            "kind": kind,
            "anchorBorrowSymbol": anchor,
            "targetSymbol": target,
            "bridgeSymbols": bridge_symbols or [],
            "pathSymbols": route_symbols,
            "pathTokens": route_tokens,
            "pathAddresses": route_addresses,
            "pathText": " -> ".join(route_symbols),
            "pathAddressText": " -> ".join(address for address in route_addresses if address),
            "candidatePairSymbols": [pair.get("symbolPair") for pair in pairs],
            "candidatePairAddresses": [pair.get("addressPair") for pair in pairs],
            "analysisStage": "overview_candidate_only",
            "nextStep": "quotes_and_route_analysis",
            "routeUsableForObservation": route_usable,
            "hopLiquidityEvidence": [
                _pair_liquidity_evidence(pair)
                for pair in pairs
            ],
            "hopCandidates": hop_candidates,
            "hopCandidateCounts": hop_candidate_counts,
            "hopCandidateCombinationCount": combination_count,
            "poolPathKey": pool_path_key,
            "recommendedFirstPriority": True,
            "routeRecommendationBasis": "tvl_depth_desc",
        },
    }

def _token_from_pool_for_symbol(pool: dict[str, Any], symbol: str) -> dict[str, Any]:
    normalized = _norm_symbol(symbol)
    for token in _pool_tokens(pool):
        if _norm_symbol(token.get("symbol")) == normalized:
            return {"symbol": normalized, "address": _token_address(token.get("address"))}
    return {"symbol": normalized, "address": ""}

def _token_path_from_route_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        symbols = [_norm_symbol(symbol) for symbol in step.get("routeSymbols") or [] if _norm_symbol(symbol)]
        addresses = [_token_address(address) for address in step.get("routeAddresses") or []]
        if len(symbols) < 2:
            symbols = [_norm_symbol(step.get("anchorSymbol")), _norm_symbol(step.get("counterSymbol"))]
            addresses = [_token_address(step.get("anchorAddress")), _token_address(step.get("counterAddress"))]
        if len(symbols) < 2 or not symbols[0] or not symbols[1]:
            continue
        first = {"symbol": symbols[0], "address": addresses[0] if len(addresses) > 0 else ""}
        second = {"symbol": symbols[1], "address": addresses[1] if len(addresses) > 1 else ""}
        if not tokens:
            tokens.append(first)
        tokens.append(second)
    return tokens

def _token_path_from_observation_route(route: dict[str, Any]) -> list[dict[str, Any]]:
    route_tokens = [token for token in route.get("routeTokenPath") or [] if isinstance(token, dict)]
    if route_tokens:
        return route_tokens
    route_steps = [step for step in route.get("routePools") or [] if isinstance(step, dict)]
    if route_steps:
        tokens = _token_path_from_route_steps(route_steps)
        if tokens:
            return tokens
    path_symbols = [_norm_symbol(symbol) for symbol in route.get("pathSymbols") or [] if _norm_symbol(symbol)]
    path_addresses = [_token_address(address) for address in route.get("routeAddresses") or []]
    if path_symbols and len(path_addresses) == len(path_symbols):
        return [{"symbol": symbol, "address": path_addresses[index]} for index, symbol in enumerate(path_symbols)]
    route_pairs = [pair for pair in route.get("routePairs") or [] if isinstance(pair, dict)]
    tokens: list[dict[str, Any]] = []
    for index, symbol in enumerate(path_symbols):
        if not symbol:
            continue
        pair = None
        if index < len(route_pairs):
            pair = route_pairs[index]
        elif index > 0 and index - 1 < len(route_pairs):
            pair = route_pairs[index - 1]
        pool = (_pools_for_pair(pair) or [{}])[0] if pair else {}
        tokens.append(_token_from_pool_for_symbol(pool, symbol))
    return tokens or [{"symbol": symbol, "address": ""} for symbol in path_symbols]

def _candidate_pair_symbols_from_observation_route(route: dict[str, Any]) -> list[str]:
    symbols: list[str] = []
    for pair in route.get("routePairs") or []:
        if isinstance(pair, dict):
            symbol = str(pair.get("symbolPair") or pair.get("pairSymbol") or "").strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    for step in route.get("routePools") or []:
        if isinstance(step, dict):
            symbol = str(step.get("pairSymbol") or step.get("symbolPair") or "").strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return symbols

def _observation_route_to_path_candidate(route: dict[str, Any], *, chain_id: int | None) -> dict[str, Any]:
    route_tokens = _token_path_from_observation_route(route)
    route_symbols = _route_symbols_from_tokens(route_tokens) or [_norm_symbol(symbol) for symbol in route.get("pathSymbols") or [] if _norm_symbol(symbol)]
    route_addresses = _route_addresses_from_tokens(route_tokens)
    pools = _route_pools(route)
    hop_candidates = list(route.get("hopCandidates") or [])
    hop_candidate_counts = dict(route.get("hopCandidateCounts") or {})
    hop_candidate_combination_count = max(1, _int(route.get("hopCandidateCombinationCount")) or 1)
    pool_path_key = str(route.get("poolPathKey") or "|".join([_pool_address(pool) for pool in pools]))
    route_usable, route_has_explicit = _route_usability_from_pools(pools)
    anchor = _norm_symbol(route.get("anchorSymbol") or route.get("borrowSymbol") or (route_symbols[0] if route_symbols else ""))
    target = _norm_symbol(route.get("targetSymbol") or route.get("counterSymbol") or (route_symbols[-2] if len(route_symbols) > 2 else ""))
    kind = "bridge_pair"
    candidate_pair_symbols = _candidate_pair_symbols_from_observation_route(route)
    symbol_pair = route.get("pathText") or " -> ".join(route_symbols)
    strategy = (
        "robinhood_testnet_market_observation_closed_loop"
        if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else "market_observation_closed_loop"
    )
    return {
        "symbolPair": symbol_pair,
        "addressPair": " -> ".join(address for address in route_addresses if address),
        "routeTokenPath": route_tokens,
        "routeAddresses": route_addresses,
        "tokenX": route_addresses[0] if route_addresses else "",
        "tokenY": route_addresses[-2] if len(route_addresses) > 2 else (route_addresses[1] if len(route_addresses) > 1 else ""),
        "tokenXSymbol": anchor,
        "tokenYSymbol": target,
        "classification": {"pairClass": "market_observation_closed_loop", "pairClassLabel": "甯傚満瑙傚療闂幆"},
        "allPoolCount": str(len(pools)),
        "strictQualityPoolCount": str(len(pools)),
        "preferredPoolCount": str(len(pools)),
        "executionPoolCount": str(len(pools)),
        "rejectedPoolCount": "0",
        "hasStrictQualityPools": bool(pools),
        "hasPreferredPools": bool(pools),
        "strictQualityPools": pools,
        "preferredPools": pools,
        "executionPools": pools,
        "rejectedPools": [],
        "rejectReasonCounts": {},
        "routeUsableForObservation": route_usable,
        "routeHasLiquidityEvidence": route_has_explicit,
        "hopCandidates": hop_candidates,
        "hopCandidateCounts": hop_candidate_counts,
        "hopCandidateCombinationCount": hop_candidate_combination_count,
        "poolPathKey": pool_path_key,
        "recommendedFirstPriority": True,
        "routeRecommendationBasis": "tvl_depth_desc",
        "marketObservationRoute": route,
        "flashLoanLinkage": {
            "strategy": strategy,
            "borrow_symbols": [anchor] if anchor else [],
            "borrow_tokens": [route_addresses[0]] if route_addresses and route_addresses[0] else [],
            "route_symbols": route_symbols,
            "route_addresses": route_addresses,
            "candidate_combination_count": hop_candidate_combination_count,
            "ready_for_atomic_arbitrage": bool(pools and len(route_symbols) >= 2 and route_symbols[0] == route_symbols[-1]),
            "conditions": [
                "路线必须来自市场观察闭环集合，不能绕过链路专用观察算法。",
                "报价页必须复核每个跳池子输出、滑点、gas 和闪电贷费用。",
                "执行前必须确认借入锚点资产能在同一交易内归还。",
            ],
        },
        "overviewRoute": {
            "kind": kind,
            "anchorBorrowSymbol": anchor,
            "targetSymbol": target,
            "bridgeSymbols": route_symbols[1:-1],
            "pathSymbols": route_symbols,
            "pathTokens": route_tokens,
            "pathAddresses": route_addresses,
            "pathText": symbol_pair,
            "pathAddressText": " -> ".join(address for address in route_addresses if address),
            "candidatePairSymbols": candidate_pair_symbols,
            "candidatePairAddresses": [
                str(pair.get("addressPair") or pair.get("pairAddress") or "").strip()
                for pair in route.get("routePairs") or []
                if isinstance(pair, dict) and str(pair.get("addressPair") or pair.get("pairAddress") or "").strip()
            ],
            "analysisStage": "market_observation_closed_loop",
            "nextStep": "quotes_and_route_analysis",
            "observationSource": route.get("source") or route.get("kind") or "dex_market_observation",
            "marketObservationRoute": True,
            "routeUsableForObservation": route_usable,
            "hopCandidates": hop_candidates,
            "hopCandidateCounts": hop_candidate_counts,
            "hopCandidateCombinationCount": hop_candidate_combination_count,
            "poolPathKey": pool_path_key,
            "recommendedFirstPriority": True,
            "routeRecommendationBasis": "tvl_depth_desc",
            "hopLiquidityEvidence": [
                {
                    "pool": pool.get("pool") or pool.get("poolId"),
                    "usableForObservation": _pool_usable_for_observation(pool),
                }
                for pool in pools
            ],
        },
    }

def _path_candidates_from_market_observation(
    observation: dict[str, Any],
    *,
    chain_id: int | None,
    reverse_symmetric: bool = True,
) -> list[dict[str, Any]]:
    routes = [
        route
        for route in observation.get("closedLoopRoutes") or []
        if isinstance(route, dict) and route.get("closedLoop") is not False
    ]
    candidates = [_observation_route_to_path_candidate(route, chain_id=chain_id) for route in routes]
    candidates = [candidate for candidate in candidates if candidate.get("routeTokenPath") and _route_pools(candidate)]
    candidates.sort(key=_route_score, reverse=True)
    seen: set[tuple[str, tuple[str, ...]]] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        key = _route_candidate_dedup_key(
            candidate,
            reverse_symmetric=reverse_symmetric,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    deduped.sort(key=_route_score, reverse=True)
    return deduped


def _route_candidate_dedup_key(
    candidate: dict[str, Any],
    *,
    reverse_symmetric: bool = True,
) -> tuple[str, tuple[str, ...]]:
    overview_route = candidate.get("overviewRoute") if isinstance(candidate.get("overviewRoute"), dict) else {}
    route_symbols = [
        _norm_symbol(value)
        for value in candidate.get("routeSymbols") or overview_route.get("pathSymbols") or []
        if _norm_symbol(value)
    ]
    anchor = _norm_symbol(overview_route.get("anchorBorrowSymbol"))
    if not anchor and route_symbols:
        anchor = route_symbols[0]
    route_addresses = [
        str(value or "").lower()
        for value in candidate.get("routeAddresses") or overview_route.get("pathAddresses") or []
        if str(value or "").strip()
    ]
    if route_addresses and len(route_addresses) == len(route_symbols):
        mids = tuple(route_addresses[1:-1]) if len(route_addresses) >= 3 else tuple(route_addresses)
    else:
        mids = tuple(route_symbols[1:-1]) if len(route_symbols) >= 3 and route_symbols[0] == route_symbols[-1] else tuple(route_symbols)
    if reverse_symmetric and mids and tuple(reversed(mids)) < mids:
        mids = tuple(reversed(mids))
    # Reversing B/C produces the same route in the opposite direction.
    return (anchor, mids)

def _route_framework_context_from_observation(
    observation: dict[str, Any],
    pool_payload: dict[str, Any] | None = None,
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    overview_paths, candidate_watch, candidate_anchors = _overview_path_candidates_from_observation_sets(
        observation,
        None,
        chain_id=chain_id,
        protocols=protocols,
    )
    # Depth/liquidity filters are display state only. They must not cut the
    # closed-loop candidate set: a depth-less pool can still be replaced by a
    # priced candidate on the quote/routing page, so overview must receive the
    # full DEX-overview route set.
    path_source = "market_observation"
    watch_pairs = list(observation.get("chainSpecificObservationPairs") or [])
    watch_pair_symbols = {str(pair.get("symbolPair") or "").strip() for pair in watch_pairs}
    for pair in candidate_watch:
        symbol = str(pair.get("symbolPair") or "").strip()
        if symbol and symbol not in watch_pair_symbols:
            watch_pair_symbols.add(symbol)
            watch_pairs.append(pair)
    path_anchor_symbols = {
        str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip().upper()
        for path in overview_paths
        if str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip()
    }
    path_source = "market_observation"
    used_market_observation = bool(
        observation.get("candidateObservationGroups")
        or observation.get("observationAnchorSymbols")
    )
    observed_anchors = {
        str(value).strip().upper()
        for value in observation.get("observationAnchorSymbols") or []
        if str(value or "").strip()
    }
    anchor_symbols = sorted(path_anchor_symbols | observed_anchors | set(candidate_anchors))
    closed_loop_anchor_symbols = sorted(
        {
            str(group.get("anchorSymbol") or "").strip().upper()
            for group in observation.get("anchorGroups") or []
            if isinstance(group, dict) and str(group.get("anchorSymbol") or "").strip()
        }
    )
    if not closed_loop_anchor_symbols:
        closed_loop_anchor_symbols = sorted(observed_anchors)
    observation_anchor_symbols = list(observation.get("observationAnchorSymbols") or [])
    return {
        "overview_paths": overview_paths,
        "watch_pairs": watch_pairs,
        "anchor_symbols": anchor_symbols,
        "closed_loop_anchor_symbols": closed_loop_anchor_symbols,
        "observation_anchor_symbols": observation_anchor_symbols,
        "path_source": path_source,
        "used_market_observation": used_market_observation,
    }


def _observation_active_pool_filter(
    observation: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[bool, set[str]]:
    liquidity_filter = observation.get("liquidityPoolFilter") or {}
    recent_swap_filter = observation.get("recentSwapPoolFilter") or {}
    summary = observation.get("summary") or {}
    applied = bool(
        liquidity_filter.get("liquidityFilterApplied")
        or liquidity_filter.get("recentSwapFilterApplied")
        or recent_swap_filter.get("recentSwapFilterApplied")
        or recent_swap_filter.get("liquidityFilterApplied")
        or summary.get("liquidityFilterApplied")
        or summary.get("recentSwapFilterApplied")
    )
    active_pool_ids = {
        str(pool_id).lower()
        for pool_id in (
            liquidity_filter.get("activePoolIds")
            or recent_swap_filter.get("activePoolIds")
            or []
        )
        if str(pool_id).strip()
    }
    if not active_pool_ids:
        active_pool_ids = {
            str(pool_id).lower()
            for pool_id in summary.get("activePoolIds") or []
            if str(pool_id).strip()
        }
    if applied and not active_pool_ids:
        chain_function = _chain_observation_function(chain_id, "fund_anchor_pair_observation")
        if chain_function is not None:
            chain_observation = chain_function(chain_id=chain_id, protocols=protocols) or {}
            active_pool_ids = {
                str(pool_id).lower()
                for pool_id in chain_observation.get("activePoolIds")
                or chain_observation.get("liquidPoolIds")
                or []
                if str(pool_id).strip()
            }
    return applied, active_pool_ids


def _overview_path_candidates_from_observation_sets(
    observation: dict[str, Any],
    pool_payload: dict[str, Any] | None = None,
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    compact_groups = observation.get("candidateObservationGroups") or []
    # The observation snapshot (OB_TABLE) is the only upstream graph source.
    anchor_groups = observation.get("anchorGroups") or compact_groups
    if not isinstance(anchor_groups, list):
        anchor_groups = []
    pairs = _candidate_pairs(observation.get("pairs") or [])
    bridge_pairs = list(observation.get("bridgePairs") or [])
    observation_source = str(
        observation.get("marketObservationSource")
        or observation.get("marketObservationSourceStage")
        or ""
    ).strip().lower()
    if (
        not bridge_pairs
        and anchor_groups
        and observation_source != "sqlite_db"
    ):
        bridge_pairs, _bridge_index = _v4_index_bridge_pairs_for_anchor_groups(
            anchor_groups,
            chain_id=chain_id,
            protocols=protocols,
        )
    closed_loop_routes = [
        route
        for route in observation.get("closedLoopRoutes") or []
        if isinstance(route, dict) and route.get("closedLoop") is not False
    ]
    if closed_loop_routes:
        # DB-first surfaces carry the authoritative three-hop loop set from
        # SQLite. Consume those routes directly instead of re-enumerating every
        # YC x ZC direction combination, which can reach hundreds of thousands
        # of rows on a live chain even when only a handful of loops are real.
        candidates = _path_candidates_from_market_observation(
            observation,
            chain_id=chain_id,
            reverse_symmetric=False,
        )
    else:
        if _chain_db_first_only(chain_id, protocols=protocols):
            sequences = []
        else:
            sequences = _anchor_observation_sequences(anchor_groups, pairs + bridge_pairs)
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for sequence in sequences:
            # Market observation builds every Y-Z direction combination,
            # including combinations without a middle-hop pool. DEX overview
            # must only select closed loops that have a real Y-Z bridge pool;
            # no-pool rows remain on the observation page as discovery state
            # and must not become paths.
            if not (sequence.get("observationReachable") or sequence.get("observationState") == "reachable"):
                continue
            route = _closed_route_from_sequence(sequence)
            candidate = _observation_route_to_path_candidate(route, chain_id=chain_id)
            if not candidate.get("routeTokenPath") or not _route_pools(candidate):
                continue
            key = _route_candidate_dedup_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    candidates.sort(key=_route_score, reverse=True)
    watch_pairs = list(observation.get("chainSpecificObservationPairs") or [])
    used_pair_symbols = {
        pair_symbol
        for candidate in candidates
        for pair_symbol in (candidate.get("overviewRoute") or {}).get("candidatePairSymbols") or []
        if pair_symbol
    }
    if not closed_loop_routes:
        for pair in pairs:
            symbol = str(pair.get("symbolPair") or "").strip()
            if symbol and symbol not in used_pair_symbols:
                watch_pairs.append(pair)
    watch_pairs = watch_pairs[:12]
    anchors = sorted(
        {
            _norm_symbol(group.get("anchorSymbol"))
            for group in anchor_groups
            if isinstance(group, dict) and _norm_symbol(group.get("anchorSymbol"))
        }
    )
    return candidates, watch_pairs, anchors


def _candidate_path_pool_ids(path: dict[str, Any]) -> set[str]:
    pool_ids: set[str] = set()
    for pool in (
        path.get("strictQualityPools")
        or path.get("preferredPools")
        or path.get("executionPools")
        or []
    ):
        if not isinstance(pool, dict):
            continue
        pool_id = _pool_address(pool)
        if pool_id:
            pool_ids.add(pool_id.lower())
    return pool_ids


def _candidate_path_uses_active_pools(
    path: dict[str, Any],
    active_pool_ids: set[str],
) -> bool:
    pool_ids = _candidate_path_pool_ids(path)
    if not pool_ids:
        return False
    return pool_ids <= active_pool_ids

def _overview_path_candidates(
    pool_payload: dict[str, Any],
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    pairs = _candidate_pairs(pool_payload.get("pairs") or [])
    anchors = _borrow_anchor_symbols(pool_payload, chain_id, protocols=protocols)
    _, symbol_to_keys, neighbor_keys, pair_by_edge = _token_graph(pairs)
    chain_anchors = [
        symbol
        for symbol in anchors
        if symbol_to_keys.get(symbol)
    ]
    discovered_anchors = [
        symbol
        for symbol in _borrowable_symbols_from_discovery(chain_id)
        if symbol not in chain_anchors and symbol_to_keys.get(symbol)
    ]
    extended_borrow_symbols = [
        symbol
        for symbol in _borrowable_symbols(pool_payload, chain_id, protocols=protocols)
        if symbol not in chain_anchors and symbol not in discovered_anchors and symbol_to_keys.get(symbol)
    ]
    if chain_id == _ROBINHOOD_CHAIN_ID:
        if any(symbol == "ETH" for symbol in chain_anchors):
            discovered_anchors = [
                symbol
                for symbol in discovered_anchors
                if symbol not in {"WETH", "WETH.E"}
            ]
        native_chain_anchors = [
            symbol
            for symbol in chain_anchors
            if symbol in {"ETH", "WETH", "WETH.E"}
        ]
        stable_chain_anchors = [
            symbol
            for symbol in chain_anchors
            if symbol not in native_chain_anchors
        ]
        anchors = [*native_chain_anchors, *discovered_anchors, *stable_chain_anchors, *extended_borrow_symbols]
    else:
        anchors = [*chain_anchors, *discovered_anchors, *extended_borrow_symbols]
    group_anchor_symbols = {
        _norm_symbol(group.get("anchorSymbol"))
        for group in pool_payload.get("anchorGroups") or []
        if isinstance(group, dict) and _norm_symbol(group.get("anchorSymbol")) and _route_symbol_allowed(group.get("anchorSymbol"))
    }
    anchors = sorted(
        {
            symbol
            for symbol in [*anchors, *group_anchor_symbols]
            if _route_symbol_allowed(symbol)
        },
        key=lambda symbol: (symbol_to_keys.get(symbol) is None, symbol),
    )
    routes: list[dict[str, Any]] = []
    route_keys: set[tuple[str, ...]] = set()
    seen_loop_keys: set[tuple[str, tuple[str, ...]]] = set()

    def _route_key_from_route(route: dict[str, Any], *pair_keys: str) -> tuple[str, ...]:
        path_tokens = route.get("routeTokenPath") or []
        token_keys = tuple(_token_key(token) for token in path_tokens if isinstance(token, dict))
        return (*token_keys, *pair_keys)

    for anchor in anchors:
        anchor_keys = sorted(symbol_to_keys.get(anchor) or [])
        for anchor_key in anchor_keys:
            for mid_key in sorted(neighbor_keys.get(anchor_key) or []):
                if mid_key == anchor_key:
                    continue
                pair_xy = pair_by_edge.get(frozenset((anchor_key, mid_key)))
                if pair_xy is None:
                    continue
                mid_token_xy = _pair_token_entry(pair_xy, mid_key)
                anchor_token_xy = _pair_token_entry(pair_xy, anchor_key)
                if mid_token_xy is None or anchor_token_xy is None:
                    continue
                mid_symbol = _norm_symbol(mid_token_xy.get("symbol"))
                if not mid_symbol or not _route_symbol_allowed(mid_symbol):
                    continue
                for target_key in sorted(neighbor_keys.get(mid_key) or []):
                    if target_key in {anchor_key, mid_key}:
                        continue
                    pair_yz = pair_by_edge.get(frozenset((mid_key, target_key)))
                    pair_zx = pair_by_edge.get(frozenset((target_key, anchor_key)))
                    if pair_yz is None or pair_zx is None:
                        continue
                    target_token_yz = _pair_token_entry(pair_yz, target_key)
                    anchor_token_zx = _pair_token_entry(pair_zx, anchor_key)
                    target_token_zx = _pair_token_entry(pair_zx, target_key)
                    mid_token_yz = _pair_token_entry(pair_yz, mid_key)
                    if any(token is None for token in (target_token_yz, anchor_token_zx, target_token_zx, mid_token_yz)):
                        continue
                    target = _norm_symbol(target_token_yz.get("symbol"))
                    if not target or not _route_symbol_allowed(target):
                        continue
                    route = _route_candidate(
                        kind="bridge_pair",
                        anchor=anchor,
                        target=target,
                        bridge_symbols=[mid_symbol, target],
                        path_symbols=[anchor, mid_symbol, target, anchor],
                        path_tokens=[anchor_token_xy, mid_token_xy, target_token_yz, anchor_token_zx],
                        pairs=[pair_xy, pair_yz, pair_zx],
                    )
                    key = _route_key_from_route(route, str(pair_xy.get("symbolPair") or ""), str(pair_yz.get("symbolPair") or ""), str(pair_zx.get("symbolPair") or ""))
                    if key in route_keys:
                        continue
                    token_path = route.get("routeTokenPath") or (route.get("overviewRoute") or {}).get("pathTokens") or []
                    path_keys = tuple(_token_key(token) for token in token_path if isinstance(token, dict))
                    loop_anchor = path_keys[0] if path_keys else _norm_symbol((route.get("overviewRoute") or {}).get("anchorBorrowSymbol"))
                    loop_mids = tuple(path_keys[1:-1]) if len(path_keys) >= 3 and path_keys[0] == path_keys[-1] else tuple(path_keys)
                    if loop_mids and tuple(reversed(loop_mids)) < loop_mids:
                        loop_mids = tuple(reversed(loop_mids))
                    loop_key = (loop_anchor, loop_mids)
                    if loop_key in seen_loop_keys:
                        continue
                    seen_loop_keys.add(loop_key)
                    routes.append(route)
                    route_keys.add(key)

    routes.sort(key=_route_score, reverse=True)
    used_pairs = {
        pair_symbol
        for route in routes
        for pair_symbol in (route.get("overviewRoute") or {}).get("candidatePairSymbols") or []
    }
    watch_pairs = [pair for pair in pairs if pair.get("symbolPair") not in used_pairs][:12]
    return routes, watch_pairs, anchors
