"""Market observation and route candidate construction helpers."""

from __future__ import annotations

from typing import Any

from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery as _default_query_flash_loan_pool_discovery
from market.binance_market.dex_surface_graph import (
    _pair_score,
    _pair_token_entry,
    _pair_symbol_graph,
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
    _number,
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

def _route_step_row(
    pool: dict[str, Any],
    *,
    from_token: dict[str, Any],
    to_token: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    from_symbol = _norm_symbol(from_token.get("symbol"))
    to_symbol = _norm_symbol(to_token.get("symbol"))
    from_address = _token_address(from_token.get("address"))
    to_address = _token_address(to_token.get("address"))
    route_symbols = [from_symbol, to_symbol]
    route_addresses = [from_address, to_address]
    return {
        "pool": _pool_address(pool),
        "direction": direction,
        "anchorSymbol": from_symbol,
        "anchorAddress": from_address,
        "counterSymbol": to_symbol,
        "counterAddress": to_address,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeText": " -> ".join(route_symbols),
        "poolIdentityMode": pool.get("poolIdentityMode") or "",
        "protocol": pool.get("protocol") or pool.get("dex") or "",
        "dex": pool.get("dex") or pool.get("protocol") or "",
        "fee": pool.get("fee"),
        "quality": pool.get("quality"),
        "classification": pool.get("classification") or {},
        "balances": pool.get("balances") or {},
        "depthBand": pool.get("depthBand") or {},
        "quoteSummary": pool.get("quoteSummary") or {},
        "tick": pool.get("tick"),
        "sqrtPriceX96": pool.get("sqrtPriceX96"),
        "liquidity": pool.get("liquidity"),
        "poolManager": pool.get("poolManager") or pool.get("pool_manager"),
        "poolKey": pool.get("poolKey") or pool.get("pool_key"),
        "tickSpacing": pool.get("tickSpacing") or pool.get("tick_spacing"),
        "hooks": pool.get("hooks") or "",
        "token0": pool.get("token0"),
        "token1": pool.get("token1"),
        "token0Symbol": pool.get("token0Symbol"),
        "token1Symbol": pool.get("token1Symbol"),
        "token0Decimals": pool.get("token0Decimals") or pool.get("token0_decimals"),
        "token1Decimals": pool.get("token1Decimals") or pool.get("token1_decimals"),
        "pairSymbol": pool.get("symbolPair") or "",
        "pairAddress": pool.get("addressPair") or "",
        "source": pool.get("source") or "",
        "evidence": pool.get("evidence") or {},
    }

def _anchor_observation_sequences(
    anchor_groups: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _, symbol_to_keys, _, pair_by_edge = _token_graph(pairs)
    pair_by_symbol_edge = _pair_symbol_graph(pairs)
    sequences: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    def _best_pool_from_pair(pair: dict[str, Any], *, exclude_pool_ids: set[str] | None = None) -> dict[str, Any] | None:
        pools = _best_pool_rows(_pools_for_pair(pair), exclude_pool_ids=exclude_pool_ids)
        return pools[0] if pools else None

    def _best_pair_for_tokens(y_token: dict[str, Any], z_token: dict[str, Any]) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        for y_key in _token_instance_keys(y_token, symbol_to_keys):
            for z_key in _token_instance_keys(z_token, symbol_to_keys):
                if y_key == z_key:
                    continue
                candidate = pair_by_edge.get(frozenset((y_key, z_key)))
                if candidate is not None and (best is None or _pair_score(candidate) > _pair_score(best)):
                    best = candidate
        if best is not None:
            return best
        y_symbol = _norm_symbol(y_token.get("symbol"))
        z_symbol = _norm_symbol(z_token.get("symbol"))
        if y_symbol and z_symbol and y_symbol != z_symbol:
            return pair_by_symbol_edge.get(frozenset((y_symbol, z_symbol)))
        return None

    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _norm_symbol(group.get("anchorSymbol"))
        if not anchor_symbol or not _route_symbol_allowed(anchor_symbol):
            continue
        anchor_address = _token_address((group.get("anchorAddresses") or [""])[0])
        anchor_token = {"symbol": anchor_symbol, "address": anchor_address}
        yc_tokens = _anchor_group_direction_tokens(group, "yc")
        zc_tokens = _anchor_group_direction_tokens(group, "zc")
        yc_rows = _anchor_group_direction_rows(group, "yc")
        zc_rows = _anchor_group_direction_rows(group, "zc")
        for yc_token in yc_tokens:
            y_symbol = _norm_symbol(yc_token.get("symbol"))
            if not y_symbol or not _route_symbol_allowed(y_symbol):
                continue
            yc_pool = (_group_token_pool_rows(yc_token, yc_rows) or [None])[0]
            if yc_pool is None:
                continue
            yc_pool_id = _pool_address(yc_pool)
            if not yc_pool_id:
                continue
            for zc_token in zc_tokens:
                z_symbol = _norm_symbol(zc_token.get("symbol"))
                if not z_symbol or not _route_symbol_allowed(z_symbol):
                    continue
                if _token_key(yc_token) == _token_key(zc_token):
                    continue
                zc_pool = (_group_token_pool_rows(zc_token, zc_rows) or [None])[0]
                if zc_pool is None:
                    continue
                zc_pool_id = _pool_address(zc_pool)
                if not zc_pool_id:
                    continue
                pair_yz = _best_pair_for_tokens(yc_token, zc_token)
                if pair_yz is None:
                    continue
                yz_pool = _best_pool_from_pair(pair_yz)
                if yz_pool is None:
                    continue
                yz_pool_id = _pool_address(yz_pool)
                if not yz_pool_id:
                    continue
                key = (
                    anchor_symbol,
                    y_symbol,
                    z_symbol,
                    yc_pool_id.lower(),
                    yz_pool_id.lower(),
                    zc_pool_id.lower(),
                )
                if key in seen:
                    continue
                seen.add(key)
                route_rows = [
                    _route_step_row(yc_pool, from_token=anchor_token, to_token=yc_token, direction="yc"),
                    _route_step_row(yz_pool, from_token=yc_token, to_token=zc_token, direction="bridge"),
                    _route_step_row(zc_pool, from_token=zc_token, to_token=anchor_token, direction="zc"),
                ]
                route_pool_ids = {yc_pool_id, yz_pool_id, zc_pool_id}
                sequences.append(
                    {
                        "kind": "anchor_closed_loop_token_bridge",
                        "anchorSymbol": anchor_symbol,
                        "counterSymbol": z_symbol,
                        "bridgeSymbol": y_symbol,
                        "pathSymbols": [anchor_symbol, y_symbol, z_symbol, anchor_symbol],
                        "pathText": f"{anchor_symbol} -> {y_symbol} -> {z_symbol} -> {anchor_symbol}",
                        "pool": yz_pool_id,
                        "ycPool": route_rows[0],
                        "bridgePool": route_rows[1],
                        "zcPool": route_rows[-1],
                        "routePools": route_rows,
                        "ycPoolCount": str(len(yc_rows)),
                        "zcPoolCount": str(len(zc_rows)),
                        "candidatePoolCount": str(len(route_pool_ids)),
                        "matchedPoolCount": str(len(route_rows)),
                        "anchorAddresses": list(group.get("anchorAddresses") or []),
                        "counterTokenCount": str(len({y_symbol, z_symbol} - {anchor_symbol})),
                    }
                )
    sequences.sort(
        key=lambda item: (
            str(item.get("anchorSymbol") or ""),
            str(item.get("counterSymbol") or ""),
            str(item.get("pool") or ""),
        )
    )
    return sequences

def _route_anchor_symbol(route: dict[str, Any]) -> str:
    return _norm_symbol(route.get("anchorSymbol") or route.get("borrowSymbol"))

def _sequence_pair_row(step: dict[str, Any]) -> dict[str, Any]:
    route_symbols = [symbol for symbol in step.get("routeSymbols") or [] if symbol]
    return {
        "symbolPair": step.get("pairSymbol") or " -> ".join(route_symbols),
        "tradeSymbols": route_symbols,
        "poolCount": 1 if _pool_address(step) else 0,
        "classification": step.get("classification") or {},
        "pool": _pool_address(step),
        "protocol": step.get("protocol") or step.get("dex") or "",
        "fee": step.get("fee"),
        "pools": [step] if _pool_address(step) else [],
    }

def _closed_route_from_sequence(sequence: dict[str, Any]) -> dict[str, Any]:
    route_pools = [step for step in sequence.get("routePools") or [] if isinstance(step, dict)]
    route_pairs = [_sequence_pair_row(step) for step in route_pools]
    first_leg = route_pairs[0] if route_pairs else {}
    second_legs = route_pairs[1:] if len(route_pairs) > 1 else []
    return {
        "kind": "bridge_closed_loop",
        "borrowSymbol": sequence.get("anchorSymbol"),
        "anchorSymbol": sequence.get("anchorSymbol"),
        "bridgeSymbol": sequence.get("bridgeSymbol"),
        "targetSymbol": sequence.get("counterSymbol"),
        "pathSymbols": list(sequence.get("pathSymbols") or []),
        "pathText": sequence.get("pathText") or "",
        "closedLoop": True,
        "firstLegCount": 1 if first_leg else 0,
        "secondLegCount": len(second_legs),
        "firstLegPair": first_leg,
        "secondLegPairs": second_legs,
        "routePairs": route_pairs,
        "routePools": route_pools,
        "source": sequence.get("source") or "yc_zc_bridge_observation",
    }

def _candidate_observation_groups(
    anchor_groups: list[dict[str, Any]],
    observation_sequences: list[dict[str, Any]],
    closed_routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sequences_by_anchor: dict[str, list[dict[str, Any]]] = {}
    direct_routes_by_anchor: dict[str, list[dict[str, Any]]] = {}
    bridge_routes_by_anchor: dict[str, list[dict[str, Any]]] = {}
    closed_routes_by_anchor: dict[str, list[dict[str, Any]]] = {}

    for sequence in observation_sequences:
        anchor_symbol = _norm_symbol(sequence.get("anchorSymbol"))
        if anchor_symbol:
            sequences_by_anchor.setdefault(anchor_symbol, []).append(sequence)

    for route in closed_routes:
        anchor_symbol = _route_anchor_symbol(route)
        if not anchor_symbol:
            continue
        closed_routes_by_anchor.setdefault(anchor_symbol, []).append(route)
        kind = str(route.get("kind") or "").strip().lower()
        if kind == "direct_closed_pair":
            direct_routes_by_anchor.setdefault(anchor_symbol, []).append(route)
        elif kind == "bridge_closed_loop":
            bridge_routes_by_anchor.setdefault(anchor_symbol, []).append(route)

    def route_sort_key(route: dict[str, Any]) -> tuple[float, int, str]:
        pools = [pool for pair in route.get("routePairs") or [] for pool in pair.get("pools") or []]
        if not pools:
            pools = [pool for pool in route.get("routePools") or [] if isinstance(pool, dict)]
        tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
        return (tvl, len(route.get("routePairs") or []), str(route.get("pathText") or route.get("symbolPair") or ""))

    def sequence_sort_key(sequence: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(sequence.get("counterSymbol") or ""),
            str(sequence.get("pool") or ""),
            str(sequence.get("pathText") or ""),
        )

    grouped: list[dict[str, Any]] = []
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _norm_symbol(group.get("anchorSymbol"))
        if not anchor_symbol:
            continue
        sequences = sorted(sequences_by_anchor.get(anchor_symbol) or [], key=sequence_sort_key)
        direct_routes = sorted(direct_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        bridge_routes = sorted(bridge_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        closed = sorted(closed_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        yc_token_count = _int(group.get("ycTokenCount"))
        zc_token_count = _int(group.get("zcTokenCount"))
        if sequences:
            observation_status = "reachable"
            empty_reason = None
        elif not yc_token_count or not zc_token_count:
            observation_status = "missing_yc_or_zc"
            empty_reason = "YC or ZC candidate set is empty"
        else:
            observation_status = "no_yz_pair"
            empty_reason = "YC and ZC candidates exist, but no Y-Z trading pair pool is available"
        copied = dict(group)
        copied["observationSequences"] = sequences
        copied["observationSequenceCount"] = len(sequences)
        copied["observationSequencePreview"] = [sequence.get("pathText") or sequence.get("pool") or "" for sequence in sequences[:6]]
        copied["observationReachable"] = bool(sequences)
        copied["observationEmptyReason"] = empty_reason
        copied["observationState"] = {
            "status": observation_status,
            "reachable": bool(sequences),
            "reason": empty_reason,
            "ycTokenCount": yc_token_count,
            "zcTokenCount": zc_token_count,
            "groupCount": len(sequences),
        }
        copied["directClosedLoopRoutes"] = direct_routes[:10]
        copied["directClosedLoopCount"] = len(direct_routes)
        copied["bridgeClosedLoopRoutes"] = bridge_routes[:10]
        copied["bridgeClosedLoopCount"] = len(bridge_routes)
        copied["closedLoopRoutes"] = closed[:20]
        copied["closedLoopRouteCount"] = len(closed)
        copied["observationCombinationCount"] = len(sequences)
        copied["observationCombinationPreview"] = copied["observationSequencePreview"]
        grouped.append(copied)
    return grouped

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
    primary = pairs[-1] if pairs else {}
    route_tokens = path_tokens or [{"symbol": symbol, "address": ""} for symbol in path_symbols if symbol]
    route_symbols = _route_symbols_from_tokens(route_tokens)
    route_addresses = _route_addresses_from_tokens(route_tokens)
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
        "flashLoanLinkage": {
            "strategy": "stable_anchor_candidate_path" if kind == "direct_anchor_pair" else "stable_anchor_bridge_candidate_path",
            "borrow_symbols": [anchor],
            "borrow_tokens": [route_addresses[0]] if route_addresses and route_addresses[0] else [],
            "route_symbols": route_symbols,
            "route_addresses": route_addresses,
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
    anchor = _norm_symbol(route.get("anchorSymbol") or route.get("borrowSymbol") or (route_symbols[0] if route_symbols else ""))
    target = _norm_symbol(route.get("targetSymbol") or route.get("counterSymbol") or (route_symbols[-2] if len(route_symbols) > 2 else ""))
    kind = "direct_anchor_pair" if str(route.get("kind") or "").lower() == "direct_closed_pair" or len(route_symbols) <= 3 else "bridge_pair"
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
        "classification": {"pairClass": "market_observation_closed_loop", "pairClassLabel": "市场观察闭环"},
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
        "marketObservationRoute": route,
        "flashLoanLinkage": {
            "strategy": strategy,
            "borrow_symbols": [anchor] if anchor else [],
            "borrow_tokens": [route_addresses[0]] if route_addresses and route_addresses[0] else [],
            "route_symbols": route_symbols,
            "route_addresses": route_addresses,
            "ready_for_atomic_arbitrage": bool(pools and len(route_symbols) >= 2 and route_symbols[0] == route_symbols[-1]),
            "conditions": [
                "路径必须来自市场观察闭环集合，不能绕过链路专用观察算法。",
                "报价页必须复核每一跳池子输出、滑点、gas 和闪电贷费用。",
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
        },
    }

def _path_candidates_from_market_observation(observation: dict[str, Any], *, chain_id: int | None) -> list[dict[str, Any]]:
    routes = [
        route
        for route in observation.get("closedLoopRoutes") or []
        if isinstance(route, dict) and route.get("closedLoop") is not False
    ]
    candidates = [_observation_route_to_path_candidate(route, chain_id=chain_id) for route in routes]
    candidates = [candidate for candidate in candidates if candidate.get("routeTokenPath") and _route_pools(candidate)]
    candidates.sort(key=_route_score, reverse=True)
    return candidates

def _route_framework_context_from_observation(
    observation: dict[str, Any],
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    overview_paths = _path_candidates_from_market_observation(observation, chain_id=chain_id)
    path_source = "market_observation"
    watch_pairs = list(observation.get("chainSpecificObservationPairs") or [])
    if not overview_paths:
        overview_paths, watch_pairs, anchor_symbols = _overview_path_candidates(pool_payload, chain_id, protocols=protocols)
        closed_loop_anchor_symbols = _observation_anchor_symbols(pool_payload, chain_id=chain_id, protocols=protocols)
        used_market_observation = False
        path_source = "candidate_pool_fallback"
    else:
        anchor_symbols = sorted(
            {
                str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip().upper()
                for path in overview_paths
                if str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip()
            }
        )
        closed_loop_anchor_symbols = anchor_symbols
        used_market_observation = True
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

def _overview_path_candidates(
    pool_payload: dict[str, Any],
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    pairs = _candidate_pairs(pool_payload.get("pairs") or [])
    anchors = _borrow_anchor_symbols(pool_payload, chain_id, protocols=protocols)
    token_by_key, symbol_to_keys, neighbor_keys, pair_by_edge = _token_graph(pairs)
    direct_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    routes: list[dict[str, Any]] = []
    route_keys: set[tuple[str, ...]] = set()

    def _route_key_from_route(route: dict[str, Any], *pair_keys: str) -> tuple[str, ...]:
        path_tokens = route.get("routeTokenPath") or []
        token_keys = tuple(_token_key(token) for token in path_tokens if isinstance(token, dict))
        return (*token_keys, *pair_keys)

    for anchor in anchors:
        anchor_keys = sorted(symbol_to_keys.get(anchor) or [])
        for anchor_key in anchor_keys:
            neighbor_keys_for_anchor = sorted(neighbor_keys.get(anchor_key) or [])
            for target_key in neighbor_keys_for_anchor:
                if target_key == anchor_key:
                    continue
                pair = pair_by_edge.get(frozenset((anchor_key, target_key)))
                if pair is None:
                    continue
                anchor_token = _pair_token_entry(pair, anchor_key)
                target_token = _pair_token_entry(pair, target_key)
                if anchor_token is None or target_token is None:
                    continue
                target = _norm_symbol(target_token.get("symbol"))
                if not target or not _route_symbol_allowed(target):
                    continue
                route = _route_candidate(
                    kind="direct_anchor_pair",
                    anchor=anchor,
                    target=target,
                    path_symbols=[anchor, target, anchor],
                    path_tokens=[anchor_token, target_token, anchor_token],
                    pairs=[pair],
                )
                key = (anchor_key, target_key)
                current = direct_by_key.get(key)
                if current is None or _route_score(route) > _route_score(current):
                    direct_by_key[key] = route

    routes.extend(direct_by_key.values())
    route_keys.update(direct_by_key)
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
