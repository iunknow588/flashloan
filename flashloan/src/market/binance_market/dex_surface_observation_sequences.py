"""Observation sequence and candidate group helpers."""

from __future__ import annotations

from typing import Any

from market.binance_market.dex_surface_graph import (
    _pair_score,
    _pair_symbol_graph,
    _pools_for_pair,
    _token_graph,
    _token_key,
)
from market.binance_market.dex_surface_observation_helpers import (
    _anchor_group_direction_rows,
    _anchor_group_direction_tokens,
    _best_pool_rows,
    _group_token_pool_rows,
    _token_instance_keys,
)
from market.binance_market.dex_surface_types import (
    _int,
    _norm_symbol,
    _number,
    _pool_address,
    _route_symbol_allowed,
    _token_address,
)


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
    try:
        _fee_int = int(str(pool.get("fee") or "").strip() or 0)
    except (TypeError, ValueError):
        _fee_int = 0
    _dynamic_fee = bool(_fee_int & 8388608)
    _special_fee = _dynamic_fee or _fee_int > 100_000
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
        "dynamicFee": pool.get("dynamicFee", _dynamic_fee),
        "specialFee": pool.get("specialFee", _special_fee),
        "feeLabel": pool.get("feeLabel") or ("dynamic" if _dynamic_fee else "special" if _special_fee else pool.get("fee")),
        "usableForObservation": pool.get("usableForObservation"),
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
        "recentSwapActive": pool.get("recentSwapActive"),
        "liquidity": pool.get("liquidity") or "",
        "liquidityIndex": pool.get("liquidityIndex"),
        "hasLiquidity": pool.get("hasLiquidity"),
        "estimatedTvlUsd": pool.get("estimatedTvlUsd"),
        "token0Reserve": pool.get("token0Reserve"),
        "token1Reserve": pool.get("token1Reserve"),
        "depthStatus": pool.get("depthStatus") or "",
        "depthBand": pool.get("depthBand") or {},
    }


def _anchor_observation_sequences(
    anchor_groups: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _, symbol_to_keys, _, pair_by_edge = _token_graph(pairs)
    pair_by_symbol_edge = _pair_symbol_graph(pairs)

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

    sequences: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
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
        "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
        "source": sequence.get("source") or "yc_zc_bridge_observation",
    }


def _candidate_observation_groups(
    anchor_groups: list[dict[str, Any]],
    observation_sequences: list[dict[str, Any]],
    closed_routes: list[dict[str, Any]],
    *,
    judge_observation_state: bool = True,
) -> list[dict[str, Any]]:
    sequences_by_anchor: dict[str, list[dict[str, Any]]] = {}
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
        if kind == "bridge_closed_loop":
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
        bridge_routes = sorted(bridge_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        closed = sorted(closed_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        yc_token_count = _int(group.get("ycTokenCount"))
        zc_token_count = _int(group.get("zcTokenCount"))
        if sequences:
            observation_status = "reachable"
            empty_reason = None
        elif not judge_observation_state:
            observation_status = "yc_zc_candidate_set"
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
        copied["bridgeClosedLoopRoutes"] = bridge_routes[:10]
        copied["bridgeClosedLoopCount"] = len(bridge_routes)
        copied["closedLoopRoutes"] = closed[:20]
        copied["closedLoopRouteCount"] = len(closed)
        copied["observationCombinationCount"] = len(sequences)
        copied["observationCombinationPreview"] = copied["observationSequencePreview"]
        grouped.append(copied)
    return grouped


__all__ = [
    "_anchor_observation_sequences",
    "_candidate_observation_groups",
    "_closed_route_from_sequence",
    "_route_anchor_symbol",
    "_route_step_row",
    "_sequence_pair_row",
]
