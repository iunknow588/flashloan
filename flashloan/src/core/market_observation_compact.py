from typing import Any

from core.market_observation_io import _text


def _pool_registry_key(pool: dict[str, Any]) -> str:
    return str(pool.get("poolId") or pool.get("pool") or "").strip().lower()


def _pool_registry_from_sequences(sequences: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the minimal pool rows referenced by every persisted route."""
    registry: dict[str, Any] = {}
    for sequence in sequences:
        if not isinstance(sequence, dict):
            continue
        for step in sequence.get("routePools") or []:
            if not isinstance(step, dict):
                continue
            key = _pool_registry_key(step)
            if key:
                registry[key] = {**registry.get(key, {}), **step}
    return registry


def _hydrate_route_pools_from_registry(
    sequences: list[dict[str, Any]],
    registry: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Merge registry rows back into routePools so static routes can be restored."""
    pool_rows = {
        str(key or "").lower(): row
        for key, row in (registry or {}).items()
        if isinstance(row, dict) and str(key or "").strip()
    }
    hydrated: list[dict[str, Any]] = []
    for sequence in sequences:
        if not isinstance(sequence, dict):
            hydrated.append(sequence)
            continue
        copied = dict(sequence)
        route_pools: list[dict[str, Any]] = []
        for step in copied.get("routePools") or []:
            if not isinstance(step, dict):
                route_pools.append(step)
                continue
            key = _pool_registry_key(step)
            registry_row = pool_rows.get(key) if key else None
            route_pools.append({**(registry_row or {}), **step})
        copied["routePools"] = route_pools
        hydrated.append(copied)
    return hydrated


def _compact_route_pool(step: dict[str, Any]) -> dict[str, Any]:
    """Keep only the hop evidence needed to rebuild quote-route inputs."""
    keys = (
        "pool",
        "poolId",
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
        "token0Decimals",
        "token1Decimals",
        "token0_decimals",
        "token1_decimals",
        "token0Amount",
        "token1Amount",
        "token0Usd",
        "token1Usd",
        "token0Symbol",
        "token1Symbol",
        "pairSymbol",
        "pairAddress",
        "poolManager",
        "pool_manager",
        "poolKey",
        "pool_key",
        "tickSpacing",
        "tick_spacing",
        "hooks",
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
    path_symbols = list(compact.get("pathSymbols") or [])
    path_text = compact.get("pathText") or ""
    if not path_symbols and isinstance(path_text, str) and " -> " in path_text:
        compact["pathSymbols"] = [part.strip() for part in path_text.split(" -> ") if part.strip()]
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
    route_addresses = [_text(address) for address in sequence.get("routeAddresses") or [] if _text(address)]
    if not route_addresses:
        for step in route_pools:
            step_addresses = [_text(address) for address in step.get("routeAddresses") or [] if _text(address)]
            if len(step_addresses) < 2:
                continue
            if not route_addresses:
                route_addresses.append(step_addresses[0])
            route_addresses.append(step_addresses[1])
    if len(route_addresses) > len(sequence.get("pathSymbols") or []):
        route_addresses = route_addresses[: len(sequence.get("pathSymbols") or [])]
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
        "routeAddresses": route_addresses,
        "routePools": [_compact_route_pool(step) for step in route_pools],
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
    path_symbols = list(route.get("pathSymbols") or [])
    path_text = route.get("pathText") or ""
    if not path_symbols and isinstance(path_text, str) and " -> " in path_text:
        path_symbols = [part.strip() for part in path_text.split(" -> ") if part.strip()]
    return {
        "kind": route.get("kind") or "bridge_closed_loop",
        "source": route.get("source") or "cached_snapshot",
        "anchorSymbol": route.get("anchorSymbol") or route.get("borrowSymbol"),
        "counterSymbol": route.get("targetSymbol") or route.get("counterSymbol"),
        "bridgeSymbol": route.get("bridgeSymbol"),
        "pathSymbols": path_symbols,
        "pathText": path_text,
        "pool": route.get("pool"),
        "routePools": route_pools,
        "candidatePoolCount": route.get("candidatePoolCount") or str(len(route_pools)),
        "matchedPoolCount": route.get("matchedPoolCount") or str(len(route_pools)),
        "anchorAddresses": list(route.get("anchorAddresses") or []),
        "counterTokenCount": route.get("counterTokenCount"),
    }


def _closed_loop_dedup_key(sequence: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    symbols = [_text(value).upper() for value in sequence.get("pathSymbols") or [] if _text(value)]
    anchor = symbols[0] if symbols else _text(sequence.get("anchorSymbol")).upper()
    mids = tuple(symbols[1:-1]) if len(symbols) >= 4 and symbols[0] == symbols[-1] else tuple(symbols)
    if mids and tuple(reversed(mids)) < mids:
        mids = tuple(reversed(mids))
    return (anchor, mids)


def _is_closed_loop_sequence(sequence: dict[str, Any]) -> bool:
    symbols = [_text(value).upper() for value in sequence.get("pathSymbols") or [] if _text(value)]
    return bool(symbols) and len(symbols) >= 4 and symbols[0] == symbols[-1]


def _observation_sequences_from_observation(observation: dict[str, Any]) -> list[dict[str, Any]]:
    sequences = [
        _compact_observation_sequence(sequence)
        for sequence in observation.get("observationSequences") or []
        if isinstance(sequence, dict)
    ]
    route_sources = [
        route
        for key in ("closedLoopRoutes", "indirectRoutes")
        for route in observation.get(key) or []
        if isinstance(route, dict)
    ]
    if sequences:
        seen: set[tuple[str, tuple[str, ...]]] = set()
        deduped: list[dict[str, Any]] = []
        for sequence in sequences:
            if not _is_closed_loop_sequence(sequence):
                continue
            identity = _closed_loop_dedup_key(sequence)
            if not identity:
                continue
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(sequence)
        for route in route_sources:
            sequence = _sequence_from_route(route)
            if not _is_closed_loop_sequence(sequence):
                continue
            if len(sequence.get("routePools") or []) < 3:
                continue
            identity = _closed_loop_dedup_key(sequence)
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(sequence)
        return deduped
    seen: set[tuple[str, tuple[str, ...]]] = set()
    recovered: list[dict[str, Any]] = []
    for route in route_sources:
        sequence = _sequence_from_route(route)
        if not _is_closed_loop_sequence(sequence):
            continue
        if len(sequence.get("routePools") or []) < 3:
            continue
        identity = _closed_loop_dedup_key(sequence)
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
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for sequence in sequences:
        if not _is_closed_loop_sequence(sequence):
            continue
        identity = _closed_loop_dedup_key(sequence)
        if identity in seen:
            continue
        seen.add(identity)
        route_pairs = _route_pairs_from_sequence(sequence)
        if len(route_pairs) < 3:
            continue
        routes.append(
            {
                "kind": "bridge_closed_loop",
                "borrowSymbol": sequence.get("anchorSymbol"),
                "anchorSymbol": sequence.get("anchorSymbol"),
                "bridgeSymbol": sequence.get("bridgeSymbol"),
                "targetSymbol": sequence.get("counterSymbol"),
                "pathSymbols": list(sequence.get("pathSymbols") or []),
                "pathText": sequence.get("pathText") or "",
                "closedLoop": True,
                "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
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
