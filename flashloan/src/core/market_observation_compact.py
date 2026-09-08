from typing import Any

from core.market_observation_io import _text


def _compact_route_pool(step: dict[str, Any]) -> dict[str, Any]:
    """Keep only the hop evidence needed to rebuild quote-route inputs."""
    keys = (
        "pool",
        "direction",
        "anchorSymbol",
        "anchorAddress",
        "counterSymbol",
        "counterAddress",
        "routeSymbols",
        "routeAddresses",
        "routeText",
        "poolIdentityMode",
        "protocol",
        "dex",
        "fee",
        "quality",
        "classification",
        "balances",
        "depthBand",
        "quoteSummary",
        "tick",
        "sqrtPriceX96",
        "liquidity",
        "token0",
        "token1",
        "token0Symbol",
        "token1Symbol",
        "pairSymbol",
        "pairAddress",
        "source",
        "evidence",
    )
    return {key: step[key] for key in keys if key in step}


def _compact_observation_sequence(sequence: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in sequence.items()
        if key not in {"ycPool", "bridgePool", "zcPool", "routePairs"}
    }
    route_pools = [
        _compact_route_pool(step)
        for step in sequence.get("routePools") or []
        if isinstance(step, dict)
    ]
    compact["routePools"] = route_pools
    return compact


def _compact_candidate_group(group: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "anchorSymbol",
        "anchorAddresses",
        "candidatePoolCount",
        "candidateQueueCount",
        "candidateSetMode",
        "sharedPoolCount",
        "sharedTokenCount",
        "ycPoolCount",
        "ycTokenCount",
        "ycZcSameSet",
        "ycZcSharedPoolSet",
        "zcPoolCount",
        "zcTokenCount",
        "observationSequenceCount",
        "observationSequencePreview",
        "observationReachable",
        "observationEmptyReason",
        "observationState",
        "observationCombinationCount",
        "observationCombinationPreview",
        "directClosedLoopCount",
        "bridgeClosedLoopCount",
        "closedLoopRouteCount",
    )
    compact = {key: group[key] for key in keep if key in group}

    def compact_tokens(rows: object) -> list[dict[str, Any]]:
        return [
            {
                key: token[key]
                for key in ("symbol", "address", "poolCount")
                if key in token
            }
            for token in rows or []
            if isinstance(token, dict)
        ]

    shared_tokens = compact_tokens(group.get("sharedTokens"))
    yc_tokens = compact_tokens(group.get("ycTokens"))
    zc_tokens = compact_tokens(group.get("zcTokens"))
    if shared_tokens:
        compact["sharedTokens"] = shared_tokens
        compact["ycTokensRef"] = "sharedTokens"
        compact["zcTokensRef"] = "sharedTokens"
    else:
        if yc_tokens:
            compact["ycTokens"] = yc_tokens
        if zc_tokens:
            compact["zcTokens"] = zc_tokens
    return compact


def _compact_observation_pair(pair: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "symbolPair",
        "addressPair",
        "token0",
        "token1",
        "poolCount",
        "kind",
        "source",
        "observationReason",
        "closedLoopReady",
        "associatedFundSymbols",
        "routeSemantics",
        "directSwapRequired",
        "directSwapPoolCount",
        "closedLoopRoutePreviews",
    )
    return {key: pair[key] for key in keep if key in pair}


def _overview_route_summary(sequence: dict[str, Any]) -> dict[str, Any]:
    route_pools = [
        step
        for step in sequence.get("routePools") or []
        if isinstance(step, dict)
    ]
    pool_ids = [
        _text(step.get("pool")).lower()
        for step in route_pools
        if _text(step.get("pool"))
    ]
    pair_symbols = [
        _text(step.get("pairSymbol") or " -> ".join(step.get("routeSymbols") or ""))
        for step in route_pools
        if _text(step.get("pairSymbol") or " -> ".join(step.get("routeSymbols") or ""))
    ]
    return {
        "routeKey": "|".join(
            [
                _text(sequence.get("pathText")),
                *pool_ids,
            ]
        ),
        "kind": sequence.get("kind") or "bridge_closed_loop",
        "source": sequence.get("source") or "market_observation",
        "anchorSymbol": sequence.get("anchorSymbol"),
        "counterSymbol": sequence.get("counterSymbol"),
        "bridgeSymbol": sequence.get("bridgeSymbol"),
        "pathSymbols": list(sequence.get("pathSymbols") or []),
        "pathText": sequence.get("pathText") or "",
        "poolIds": pool_ids,
        "pairSymbols": pair_symbols,
        "anchorAddresses": list(sequence.get("anchorAddresses") or []),
        "observationRef": "observation.json",
    }


def _sequence_from_route(route: dict[str, Any]) -> dict[str, Any]:
    route_pools = [
        _compact_route_pool(step)
        for step in route.get("routePools") or []
        if isinstance(step, dict)
    ]
    if not route_pools:
        for pair in route.get("routePairs") or []:
            if not isinstance(pair, dict):
                continue
            pools = pair.get("pools") or []
            if pools and isinstance(pools[0], dict):
                route_pools.append(_compact_route_pool(pools[0]))
    return {
        "kind": route.get("kind") or "bridge_closed_loop",
        "source": route.get("source") or "cached_snapshot",
        "anchorSymbol": route.get("anchorSymbol") or route.get("borrowSymbol"),
        "counterSymbol": route.get("targetSymbol") or route.get("counterSymbol"),
        "bridgeSymbol": route.get("bridgeSymbol"),
        "pathSymbols": list(route.get("pathSymbols") or []),
        "pathText": route.get("pathText") or "",
        "pool": route.get("pool"),
        "routePools": route_pools,
        "candidatePoolCount": route.get("candidatePoolCount") or str(len(route_pools)),
        "matchedPoolCount": route.get("matchedPoolCount") or str(len(route_pools)),
        "anchorAddresses": list(route.get("anchorAddresses") or []),
        "counterTokenCount": route.get("counterTokenCount"),
    }


def _observation_sequences_from_observation(observation: dict[str, Any]) -> list[dict[str, Any]]:
    sequences = [
        _compact_observation_sequence(sequence)
        for sequence in observation.get("observationSequences") or []
        if isinstance(sequence, dict)
    ]
    if sequences:
        return sequences
    routes = [
        route
        for key in ("closedLoopRoutes", "indirectRoutes", "directClosedLoopRoutes")
        for route in observation.get(key) or []
        if isinstance(route, dict)
    ]
    seen: set[tuple[str, str]] = set()
    recovered: list[dict[str, Any]] = []
    for route in routes:
        sequence = _sequence_from_route(route)
        identity = (str(sequence.get("pathText") or ""), str(sequence.get("pool") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        recovered.append(sequence)
    return recovered


def _route_pairs_from_sequence(sequence: dict[str, Any]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for step in sequence.get("routePools") or []:
        if not isinstance(step, dict):
            continue
        route_symbols = list(step.get("routeSymbols") or [])
        pairs.append(
            {
                "symbolPair": step.get("pairSymbol") or " -> ".join(route_symbols),
                "tradeSymbols": route_symbols,
                "poolCount": 1 if step.get("pool") else 0,
                "classification": step.get("classification") or {},
                "pool": step.get("pool"),
                "protocol": step.get("protocol") or step.get("dex") or "",
                "fee": step.get("fee"),
                "pools": [step] if step.get("pool") else [],
            }
        )
    return pairs


def _closed_routes_from_sequences(sequences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for sequence in sequences:
        route_pairs = _route_pairs_from_sequence(sequence)
        routes.append(
            {
                "kind": "direct_closed_pair" if len(route_pairs) <= 2 else "bridge_closed_loop",
                "borrowSymbol": sequence.get("anchorSymbol"),
                "anchorSymbol": sequence.get("anchorSymbol"),
                "bridgeSymbol": sequence.get("bridgeSymbol"),
                "targetSymbol": sequence.get("counterSymbol"),
                "pathSymbols": list(sequence.get("pathSymbols") or []),
                "pathText": sequence.get("pathText") or "",
                "closedLoop": True,
                "firstLegCount": 1 if route_pairs else 0,
                "secondLegCount": max(0, len(route_pairs) - 1),
                "firstLegPair": route_pairs[0] if route_pairs else {},
                "secondLegPairs": route_pairs[1:],
                "routePairs": route_pairs,
                "routePools": list(sequence.get("routePools") or []),
                "source": sequence.get("source") or "yc_zc_bridge_observation",
            }
        )
    return routes


def _candidate_groups_from_sequences(
    groups: list[dict[str, Any]],
    sequences: list[dict[str, Any]],
    anchors: list[str],
) -> list[dict[str, Any]]:
    by_anchor: dict[str, list[dict[str, Any]]] = {}
    for sequence in sequences:
        anchor = _text(sequence.get("anchorSymbol")).upper()
        if anchor:
            by_anchor.setdefault(anchor, []).append(sequence)
    existing = {
        _text(group.get("anchorSymbol")).upper(): _compact_candidate_group(group)
        for group in groups
        if isinstance(group, dict) and _text(group.get("anchorSymbol"))
    }
    result: list[dict[str, Any]] = []
    for anchor in sorted(set(anchors) | set(existing) | set(by_anchor)):
        group = dict(existing.get(anchor) or {"anchorSymbol": anchor})
        rows = by_anchor.get(anchor) or []
        group["observationSequenceCount"] = len(rows)
        group["observationSequencePreview"] = [
            row.get("pathText") or row.get("pool") or "" for row in rows[:6]
        ]
        group["observationReachable"] = bool(rows)
        if rows:
            group.pop("observationEmptyReason", None)
        elif group.get("observationEmptyReason") is not None:
            group["observationEmptyReason"] = group.get("observationEmptyReason")
        group["observationState"] = {
            **(group.get("observationState") or {}),
            "status": "reachable" if rows else "no_observation_sequence",
            "reachable": bool(rows),
            "groupCount": len(rows),
        }
        group["observationCombinationCount"] = len(rows)
        group["observationCombinationPreview"] = group["observationSequencePreview"]
        result.append(group)
    return result
