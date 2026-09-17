from __future__ import annotations

from typing import Any

from core.market_observation_store import (
    market_observation_static_table,
    static_store_metadata as market_observation_static_store_metadata,
)


def build_market_observation_payload(
    surface: Any,
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
) -> dict[str, Any]:
    _cache_surface_payload = surface._cache_surface_payload
    _candidate_payload = surface._candidate_payload
    _candidate_pairs = surface._candidate_pairs
    _borrowable_symbols = surface._borrowable_symbols
    _observation_anchor_symbols = surface._observation_anchor_symbols
    _v4_index_bridge_pairs_for_anchor_groups = surface._v4_index_bridge_pairs_for_anchor_groups
    _chain_fund_anchor_pair_observation = surface._chain_fund_anchor_pair_observation
    _anchor_observation_sequences = surface._anchor_observation_sequences
    _token_graph = surface._token_graph
    _pair_symbols = surface._pair_symbols
    _pools_for_pair = surface._pools_for_pair
    _pair_token_entry = surface._pair_token_entry
    _norm_symbol = surface._norm_symbol
    _route_symbol_allowed = surface._route_symbol_allowed
    _best_pool_rows = surface._best_pool_rows
    _pool_address = surface._pool_address
    _reentry_route_has_reference_profit = surface._reentry_route_has_reference_profit
    _closed_route_from_sequence = surface._closed_route_from_sequence
    _candidate_observation_groups = surface._candidate_observation_groups
    _unique_symbols = surface._unique_symbols
    _int = surface._int
    _number = surface._number
    _price_map_from_pairs = surface._price_map_from_pairs

    pool_payload = _candidate_payload(chain_id, protocols)
    pairs = _candidate_pairs(pool_payload.get("pairs") or [])
    borrow_symbols = _borrowable_symbols(pool_payload, chain_id, protocols=protocols)
    observation_anchors = _observation_anchor_symbols(
        pool_payload,
        chain_id=chain_id,
        protocols=protocols,
        borrow_symbols=borrow_symbols,
    )
    anchor_groups = list(pool_payload.get("anchorGroups") or [])
    bridge_pairs, bridge_index_lookup = _v4_index_bridge_pairs_for_anchor_groups(
        anchor_groups,
        chain_id=chain_id,
        protocols=protocols,
    )
    fund_anchor_pair_observation = _chain_fund_anchor_pair_observation(chain_id=chain_id, protocols=protocols)
    chain_observation_sequences = [
        sequence
        for sequence in fund_anchor_pair_observation.get("observationSequences") or []
        if isinstance(sequence, dict)
    ]
    observation_sequences = _anchor_observation_sequences(anchor_groups, pairs + bridge_pairs) + chain_observation_sequences
    token_by_key, symbol_to_keys, neighbor_keys, pair_by_edge = _token_graph(pairs)
    direct: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in observation_anchors}

    def pair_row(pair: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
        pools = _pools_for_pair(pair)
        return {
            "symbolPair": pair.get("symbolPair"),
            "tradeSymbols": _pair_symbols(pair),
            "poolCount": len(pools),
            "classification": pair.get("classification") or {},
            "pools": pools[:limit],
        }

    for pair in pairs:
        symbols = _pair_symbols(pair)
        pools = _pools_for_pair(pair)
        matched = [symbol for symbol in observation_anchors if symbol in symbols]
        if matched:
            row = pair_row(pair)
            row["matchedObservationSymbols"] = matched
            row["matchedBorrowSymbols"] = [symbol for symbol in matched if symbol in borrow_symbols] or matched
            direct.append(row)
            for symbol in matched:
                by_symbol.setdefault(symbol, []).append(row)
        elif pools:
            unmatched.append(pair_row(pair, limit=3))

    route_seen: set[tuple[str, ...]] = set()
    closed_routes: list[dict[str, Any]] = []
    direct_closed_routes: list[dict[str, Any]] = []
    bridge_routes: list[dict[str, Any]] = []
    used_pair_symbols: set[str] = {str(row.get("symbolPair") or "") for row in direct}
    price_map = _price_map_from_pairs(pairs)

    for anchor in observation_anchors:
        anchor_keys = sorted(symbol_to_keys.get(anchor) or [])
        for anchor_key in anchor_keys:
            for target_key in sorted(neighbor_keys.get(anchor_key) or []):
                if target_key == anchor_key:
                    continue
                first_pair = pair_by_edge.get(frozenset((anchor_key, target_key)))
                if first_pair is None:
                    continue
                target_token = _pair_token_entry(first_pair, target_key)
                anchor_token = _pair_token_entry(first_pair, anchor_key)
                if target_token is None or anchor_token is None:
                    continue
                target_symbol = _norm_symbol(target_token.get("symbol"))
                if not target_symbol or not _route_symbol_allowed(target_symbol):
                    continue
                first_pair_pools = _best_pool_rows(_pools_for_pair(first_pair))
                if len(first_pair_pools) < 2:
                    continue
                for first_pool_index, first_pool in enumerate(first_pair_pools):
                    for second_pool in first_pair_pools[first_pool_index + 1 :]:
                        first_pool_id = _pool_address(first_pool)
                        second_pool_id = _pool_address(second_pool)
                        if not first_pool_id or not second_pool_id or first_pool_id == second_pool_id:
                            continue
                        if not _reentry_route_has_reference_profit(
                            anchor_token=anchor_token,
                            counter_token=target_token,
                            first_pool=first_pool,
                            second_pool=second_pool,
                            price_map=price_map,
                        ):
                            continue
                        route_key = ("direct", anchor_key, target_key, first_pool_id.lower(), second_pool_id.lower())
                        if route_key in route_seen:
                            continue
                        route_seen.add(route_key)
                        used_pair_symbols.add(str(first_pair.get("symbolPair") or ""))
                        first_route_row = pair_row(first_pair, limit=3)
                        first_route_row["pool"] = first_pool_id
                        first_route_row["pools"] = [first_pool]
                        second_route_row = pair_row(first_pair, limit=3)
                        second_route_row["pool"] = second_pool_id
                        second_route_row["pools"] = [second_pool]
                        route = {
                            "kind": "direct_closed_pair",
                            "borrowSymbol": anchor,
                            "anchorSymbol": anchor,
                            "targetSymbol": target_symbol,
                            "bridgeSymbol": target_symbol,
                            "pathSymbols": [anchor, target_symbol, anchor],
                            "pathText": f"{anchor} -> {target_symbol} -> {anchor}",
                            "closedLoop": True,
                            "firstLegCount": 1,
                            "secondLegCount": 1,
                            "firstLegPair": first_route_row,
                            "secondLegPairs": [second_route_row],
                            "routePairs": [first_route_row, second_route_row],
                        }
                        direct_closed_routes.append(route)
                        closed_routes.append(route)

        for anchor_key in anchor_keys:
            # General case: X -> Y -> Z -> X, where Y and Z are distinct and the
            # Y-Z bridge pool exists.
            for mid_key in sorted(neighbor_keys.get(anchor_key) or []):
                if mid_key == anchor_key:
                    continue
                first_pair = pair_by_edge.get(frozenset((anchor_key, mid_key)))
                if first_pair is None:
                    continue
                first_row = pair_row(first_pair, limit=3)
                mid_token = _pair_token_entry(first_pair, mid_key)
                anchor_token = _pair_token_entry(first_pair, anchor_key)
                if mid_token is None or anchor_token is None:
                    continue
                mid_symbol = _norm_symbol(mid_token.get("symbol"))
                if not mid_symbol or not _route_symbol_allowed(mid_symbol):
                    continue
                for target_key in sorted(neighbor_keys.get(mid_key) or []):
                    if target_key in {anchor_key, mid_key}:
                        continue
                    second_pair = pair_by_edge.get(frozenset((mid_key, target_key)))
                    third_pair = pair_by_edge.get(frozenset((target_key, anchor_key)))
                    if second_pair is None or third_pair is None:
                        continue
                    second_row = pair_row(second_pair, limit=3)
                    third_row = pair_row(third_pair, limit=3)
                    target_token = _pair_token_entry(second_pair, target_key)
                    anchor_token_zx = _pair_token_entry(third_pair, anchor_key)
                    if target_token is None or anchor_token_zx is None:
                        continue
                    target_symbol = _norm_symbol(target_token.get("symbol"))
                    if not target_symbol or not _route_symbol_allowed(target_symbol):
                        continue
                    if mid_key >= target_key:
                        continue
                    route_key = (
                        "triangle",
                        anchor_key,
                        mid_key,
                        target_key,
                        str(first_pair.get("symbolPair") or ""),
                        str(second_pair.get("symbolPair") or ""),
                        str(third_pair.get("symbolPair") or ""),
                    )
                    if route_key in route_seen:
                        continue
                    route_seen.add(route_key)
                    used_pair_symbols.add(str(first_pair.get("symbolPair") or ""))
                    used_pair_symbols.add(str(second_pair.get("symbolPair") or ""))
                    used_pair_symbols.add(str(third_pair.get("symbolPair") or ""))
                    route = {
                        "kind": "bridge_closed_loop",
                        "borrowSymbol": anchor,
                        "anchorSymbol": anchor,
                        "bridgeSymbol": mid_symbol,
                        "targetSymbol": target_symbol,
                        "pathSymbols": [anchor, mid_symbol, target_symbol, anchor],
                        "pathText": f"{anchor} -> {mid_symbol} -> {target_symbol} -> {anchor}",
                        "closedLoop": True,
                        "firstLegCount": 1,
                        "secondLegCount": 2,
                        "firstLegPair": first_row,
                        "secondLegPairs": [second_row, third_row],
                        "routePairs": [first_row, second_row, third_row],
                    }
                    bridge_routes.append(route)
                    closed_routes.append(route)

    for sequence in observation_sequences:
        route = _closed_route_from_sequence(sequence)
        route_pool_ids = tuple(
            _pool_address(step).lower()
            for step in route.get("routePools") or []
            if isinstance(step, dict) and _pool_address(step)
        )
        if not route_pool_ids:
            continue
        route_key = ("yc_zc_bridge", str(route.get("pathText") or ""), *route_pool_ids)
        if route_key in route_seen:
            continue
        route_seen.add(route_key)
        for step in route.get("routePools") or []:
            if isinstance(step, dict):
                used_pair_symbols.add(str(step.get("pairSymbol") or step.get("symbolPair") or ""))
        bridge_routes.append(route)
        closed_routes.append(route)

    def route_sort_key(route: dict[str, Any]) -> tuple[float, int, str]:
        pools = [pool for pair in route.get("routePairs") or [] for pool in pair.get("pools") or []]
        if not pools:
            pools = [pool for pool in route.get("routePools") or [] if isinstance(pool, dict)]
        tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
        return (tvl, len(pools), str(route.get("pathText") or ""))

    def route_identity(route: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
        pools = [pool for pair in route.get("routePairs") or [] for pool in pair.get("pools") or []]
        if not pools:
            pools = [pool for pool in route.get("routePools") or [] if isinstance(pool, dict)]
        pool_ids = tuple(_pool_address(pool).lower() for pool in pools if _pool_address(pool))
        return (str(route.get("kind") or ""), str(route.get("pathText") or ""), pool_ids)

    def dedupe_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_routes: set[tuple[str, str, tuple[str, ...]]] = set()
        for route in routes:
            identity = route_identity(route)
            if identity in seen_routes:
                continue
            seen_routes.add(identity)
            deduped.append(route)
        return deduped

    direct_closed_routes = sorted(dedupe_routes(direct_closed_routes), key=route_sort_key, reverse=True)
    bridge_routes = sorted(dedupe_routes(bridge_routes), key=route_sort_key, reverse=True)
    closed_routes = sorted(dedupe_routes(closed_routes), key=route_sort_key, reverse=True)[:200]
    bridges = bridge_routes[:120]
    candidate_observation_groups = _candidate_observation_groups(anchor_groups, observation_sequences, closed_routes)
    candidate_observation_groups.sort(
        key=lambda group: (
            -(_int(group.get("observationSequenceCount")) + _int(group.get("closedLoopRouteCount"))),
            str(group.get("anchorSymbol") or ""),
        ),
    )
    unmatched = [row for row in unmatched if str(row.get("symbolPair") or "") not in used_pair_symbols]

    sequence_anchor_symbols = sorted(
        {
            str(sequence.get("anchorSymbol") or "").strip().upper()
            for sequence in observation_sequences
            if str(sequence.get("anchorSymbol") or "").strip()
        }
    )
    sequence_pool_ids = sorted(
        {
            _pool_address(step)
            for sequence in observation_sequences
            for step in sequence.get("routePools") or []
            if isinstance(step, dict) and _pool_address(step)
        }
    )
    group_pool_ids = sorted(
        {
            _pool_address(step)
            for group in candidate_observation_groups
            for sequence in group.get("observationSequences") or []
            for step in sequence.get("routePools") or []
            if isinstance(step, dict) and _pool_address(step)
        }
    )

    payload = {
        "mode": "dex_market_observation",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "marketObservationStaticTable": market_observation_static_table(
            {
                **market_observation_static_store_metadata(
                    chain_id=chain_id,
                    protocols=protocols,
                ),
                "surface": "dex_market_observation",
                "source": "computed",
            }
        ),
        "borrowableSymbols": borrow_symbols,
        "observationAnchorSymbols": observation_anchors,
        "anchorGroups": anchor_groups,
        "candidateObservationGroups": candidate_observation_groups,
        "observationSequences": observation_sequences,
        "directPools": direct,
        "directClosedLoopRoutes": direct_closed_routes[:120],
        "closedLoopRoutes": closed_routes,
        "indirectRoutes": bridges,
        "unmatchedPools": unmatched[:20],
        "bridgePoolIndexLookup": bridge_index_lookup,
        "chainSpecificObservationPairs": fund_anchor_pair_observation.get("associatedObservationPairs") or [],
        "chainSpecificObservationPools": fund_anchor_pair_observation.get("associatedObservationPools") or [],
        "chainSpecificObservationTokens": fund_anchor_pair_observation.get("associatedObservationTokens") or [],
        "chainSpecificObservation": {
            "robinhoodTestnetV4FundAnchorPairObservation": {
                key: value
                for key, value in fund_anchor_pair_observation.items()
                if key
                not in {
                    "observationSequences",
                    "associatedObservationPairs",
                    "associatedObservationPools",
                    "associatedObservationTokens",
                }
            },
            "robinhoodTestnetV4NonEthPairFirst": {
                key: value
                for key, value in fund_anchor_pair_observation.items()
                if key
                not in {
                    "observationSequences",
                    "associatedObservationPairs",
                    "associatedObservationPools",
                    "associatedObservationTokens",
                }
            },
        },
        "summary": {
            "borrowableTokenCount": len(borrow_symbols),
            "observationAnchorCount": len(observation_anchors),
            "candidateObservationGroupCount": len(candidate_observation_groups),
            "bridgePoolIndexMatchedPairCount": len(bridge_pairs),
            "bridgePoolIndexMatchedPoolCount": _int(bridge_index_lookup.get("matchedPoolCount")),
            "chainSpecificObservationSequenceCount": len(chain_observation_sequences),
            "fundAnchorPairObservationRouteCount": _int(fund_anchor_pair_observation.get("routeCount")),
            "fundAnchorPairObservationAssociatedPairCount": _int(
                fund_anchor_pair_observation.get("associatedObservationPairCount")
                or fund_anchor_pair_observation.get("associatedFundAnchorPairCount")
            ),
            "fundAnchorPairObservationAssociatedObservationPoolCount": _int(
                fund_anchor_pair_observation.get("associatedObservationPoolCount")
            ),
            "fundAnchorPairObservationAssociatedObservationTokenCount": _int(
                fund_anchor_pair_observation.get("associatedObservationTokenCount")
            ),
            "fundAnchorPairObservationPoolCount": _int(fund_anchor_pair_observation.get("fundAnchorCandidatePairCount")),
            "nonEthPairFirstRouteCount": _int(fund_anchor_pair_observation.get("routeCount")),
            "nonEthPairFirstAssociatedPairCount": _int(
                fund_anchor_pair_observation.get("associatedObservationPairCount")
                or fund_anchor_pair_observation.get("associatedNonEthPairCount")
            ),
            "nonEthPairFirstAssociatedObservationPoolCount": _int(
                fund_anchor_pair_observation.get("associatedObservationPoolCount")
            ),
            "nonEthPairFirstAssociatedObservationTokenCount": _int(
                fund_anchor_pair_observation.get("associatedObservationTokenCount")
            ),
            "nonEthPairFirstPoolCount": _int(fund_anchor_pair_observation.get("nonEthPairCount")),
            "candidateObservationSequenceCount": len(observation_sequences),
            "candidateObservationDirectCount": len(direct_closed_routes),
            "candidateObservationBridgeCount": len(bridge_routes),
            "candidateObservationPoolCount": len(group_pool_ids),
            "observationSequenceCount": len(observation_sequences),
            "observationSequenceAnchorCount": len(sequence_anchor_symbols),
            "observationSequencePoolCount": len(sequence_pool_ids),
            "directPairCount": len(direct),
            "directClosedLoopCount": len(direct_closed_routes),
            "indirectRouteCount": len(bridges),
            "closedLoopRouteCount": len(closed_routes),
            "unmatchedPairCount": len(unmatched),
            "observableTokenCount": len(_unique_symbols([pool for pair in pairs for pool in pair.get("preferredPools") or []])),
        },
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
        "dexSettlement": pool_payload.get("dexSettlement") or {},
    }
    _cache_surface_payload(
        "dex_market_observation",
        payload,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=False,
    )
    return payload
