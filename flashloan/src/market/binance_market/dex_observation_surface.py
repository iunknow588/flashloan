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
    _closed_route_from_sequence = surface._closed_route_from_sequence
    _candidate_observation_groups = surface._candidate_observation_groups
    _unique_symbols = surface._unique_symbols
    _int = surface._int
    _number = surface._number

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
    # YC/ZC candidate sets are provided by the candidate-pool page.  Market
    # observation keeps every liquid YC-ZC bridge pool and builds the concrete
    # middle-hop exchange pools here, so the page can show the depth surface
    # before the DEX overview selects the best direction/pool.
    observation_sequences = _anchor_observation_sequences(anchor_groups, pairs + bridge_pairs)
    chain_observation_sequences = list(observation_sequences)
    closed_loop_sequences = list(
        fund_anchor_pair_observation.get("closedLoopSequences")
        or fund_anchor_pair_observation.get("observationSequences")
        or []
    )
    active_pool_ids = {
        str(pool_id).lower()
        for pool_id in fund_anchor_pair_observation.get("activePoolIds") or []
        if str(pool_id).strip()
    }
    liquid_pool_ids = {
        str(pool_id).lower()
        for pool_id in fund_anchor_pair_observation.get("liquidPoolIds")
        or fund_anchor_pair_observation.get("activePoolIds")
        or []
        if str(pool_id).strip()
    }
    recent_swap_filter_applied = bool(fund_anchor_pair_observation.get("recentSwapFilterApplied"))
    liquidity_filter_applied = bool(fund_anchor_pair_observation.get("liquidityFilterApplied"))
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

    closed_routes: list[dict[str, Any]] = []
    bridge_routes: list[dict[str, Any]] = []
    used_pair_symbols: set[str] = {str(row.get("symbolPair") or "") for row in direct}
    bridges: list[dict[str, Any]] = []

    candidate_observation_groups = _candidate_observation_groups(
        anchor_groups,
        observation_sequences,
        closed_routes,
        judge_observation_state=False,
    )
    candidate_observation_groups.sort(
        key=lambda group: (
            -(_int(group.get("observationSequenceCount")) + _int(group.get("closedLoopRouteCount"))),
            str(group.get("anchorSymbol") or ""),
        ),
    )
    # One Uniswap V4 pool serves both directions of the same token pair.  The
    # contract can only execute the optimal direction, so the observation page
    # keeps every complete Y-Z direction combination, but DEX overview counts
    # one pool-backed closed loop per bridge-pool address instead of treating
    # reverse token paths as separate loops.
    for group in candidate_observation_groups:
        sequences = list(group.get("observationSequences") or [])
        grouped_by_bridge_pool: dict[str, list[dict[str, Any]]] = {}
        for sequence in sequences:
            bridge_pool_id = ""
            for step in sequence.get("routePools") or []:
                if not isinstance(step, dict):
                    continue
                if str(step.get("direction") or "").lower() == "bridge":
                    bridge_pool_id = _pool_address(step)
                    break
            if not bridge_pool_id:
                bridge_pool_id = str(sequence.get("pool") or "").strip().lower()
            grouped_by_bridge_pool.setdefault(bridge_pool_id, []).append(sequence)
        reachable_sequences = [
            sequence
            for sequence in sequences
            if sequence.get("observationReachable") or sequence.get("observationState") == "reachable"
        ]
        reachable_by_bridge_pool: dict[str, list[dict[str, Any]]] = {}
        for sequence in reachable_sequences:
            bridge_pool_id = ""
            for step in sequence.get("routePools") or []:
                if not isinstance(step, dict):
                    continue
                if str(step.get("direction") or "").lower() == "bridge":
                    bridge_pool_id = _pool_address(step)
                    break
            if not bridge_pool_id:
                bridge_pool_id = str(sequence.get("pool") or "").strip().lower()
            reachable_by_bridge_pool.setdefault(bridge_pool_id, []).append(sequence)
        group["observationSequences"] = sequences
        group["bridgePoolObservationGroups"] = [
            {
                "bridgePool": bridge_pool_id,
                "directionCount": len(rows),
                "observationSequences": rows,
                "preview": (rows[0].get("pathText") if rows else "") or bridge_pool_id or "",
            }
            for bridge_pool_id, rows in reachable_by_bridge_pool.items()
        ]
        group["observationSequenceCount"] = len(reachable_by_bridge_pool)
        group["observationSequencePreview"] = [
            (rows[0].get("pathText") if rows else "") or bridge_pool_id or ""
            for bridge_pool_id, rows in reachable_by_bridge_pool.items()
        ][:6]
        group["candidateCombinationCount"] = len(sequences)
        group["reachableCombinationCount"] = len(reachable_sequences)
        group["noPoolCombinationCount"] = len(sequences) - len(reachable_sequences)
        group["observationCombinationCount"] = len(reachable_by_bridge_pool)
        group["observationCombinationPreview"] = group["observationSequencePreview"]
        group["observationState"] = {
            **dict(group.get("observationState") or {}),
            "status": "reachable" if reachable_sequences else group.get("observationState", {}).get("status"),
            "reachable": bool(reachable_sequences),
            "reason": None if reachable_sequences else group.get("observationState", {}).get("reason"),
            "groupCount": len(reachable_by_bridge_pool),
        }
        group["observationReachable"] = bool(reachable_sequences)
        group["observationEmptyReason"] = None if reachable_sequences else group.get("observationEmptyReason")
        # Keep the full directional sequence list for the page's Y-Z surface.
        # ``bridgePoolObservationGroups`` remains the pool-address view used by
        # DEX overview.
        group["directionCombinationCount"] = len(sequences)
        group["directionCombinationPreview"] = [
            sequence.get("pathText") or sequence.get("pool") or ""
            for sequence in sequences[:6]
        ]
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
    active_filter_summary = {
        "recentSwapFilterApplied": recent_swap_filter_applied,
        "liquidityFilterApplied": liquidity_filter_applied,
        "activePoolCount": len(active_pool_ids),
        "activePoolIds": sorted(active_pool_ids),
        "liquidPoolCount": len(liquid_pool_ids),
        "liquidPoolIds": sorted(liquid_pool_ids),
    }
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
        "tableSource": "ob_table",
        "referencedTable": "ch_table",
        "poolIndexVersion": {
            "path": (pool_payload.get("poolStaticTable") or {}).get("path"),
            "network": (pool_payload.get("poolStaticTable") or {}).get("network"),
            "chainId": (pool_payload.get("poolStaticTable") or {}).get("chainId"),
            "generatedAt": (pool_payload.get("poolStaticTable") or {}).get("generatedAt"),
            "generatedBlockNumber": (pool_payload.get("poolStaticTable") or {}).get("generatedBlockNumber"),
            "source": (pool_payload.get("poolStaticTable") or {}).get("mode"),
        },
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
        "pairs": pairs,
        "bridgePairs": bridge_pairs,
        "candidateObservationGroups": candidate_observation_groups,
        "observationSequences": observation_sequences,
        "directPools": direct,
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
                    "closedLoopSequences",
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
                    "closedLoopSequences",
                    "observationSequences",
                    "associatedObservationPairs",
                    "associatedObservationPools",
                    "associatedObservationTokens",
                }
            },
        },
        "closedLoopSequences": closed_loop_sequences,
        "recentSwapPoolFilter": active_filter_summary,
        "liquidityPoolFilter": active_filter_summary,
        "summary": {
            "borrowableTokenCount": len(borrow_symbols),
            "observationAnchorCount": len(observation_anchors),
            "candidateObservationGroupCount": len(candidate_observation_groups),
            "closedLoopSequenceCount": len(closed_loop_sequences),
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
            "candidateObservationBridgeCount": len(bridge_routes),
            "candidateObservationPoolCount": len(group_pool_ids),
            "observationSequenceCount": len(observation_sequences),
            "observationSequenceAnchorCount": len(sequence_anchor_symbols),
            "observationSequencePoolCount": len(sequence_pool_ids),
            "directPairCount": len(direct),
            "indirectRouteCount": len(bridges),
            "closedLoopRouteCount": len(closed_routes),
            "recentSwapFilterApplied": recent_swap_filter_applied,
            "liquidityFilterApplied": liquidity_filter_applied,
            "activePoolCount": len(active_pool_ids),
            "liquidPoolCount": len(liquid_pool_ids),
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
