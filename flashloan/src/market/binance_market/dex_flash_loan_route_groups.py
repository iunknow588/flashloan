"""Closed-loop route groups keyed by the flash-loan borrowable token.

The observation snapshot already contains atomic closed loops. This module
re-groups those loops by ``anchorSymbol`` (the token that both starts and ends
the route and can be borrowed via the flash loan), so callers can preflight
one borrow token at a time without rebuilding the full route universe.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_symbol(value: Any) -> str:
    return _text(value).upper()


def _pool_identity(pool: dict[str, Any]) -> str:
    pool_id = _text(pool.get("poolId") or pool.get("pool"))
    if pool_id:
        return pool_id.lower()
    symbols = [_norm_symbol(value) for value in pool.get("routeSymbols") or [] if _norm_symbol(value)]
    direction = _text(pool.get("direction"))
    return f"{'->'.join(symbols)}|{direction.lower()}".lower()


def _pool_dependency(pool: dict[str, Any]) -> dict[str, Any]:
    route_symbols = [_norm_symbol(value) for value in pool.get("routeSymbols") or [] if _norm_symbol(value)]
    route_addresses = [_text(value) for value in pool.get("routeAddresses") or [] if _text(value)]
    return {
        "pool": _text(pool.get("pool")),
        "poolId": _text(pool.get("poolId") or pool.get("pool")),
        "pairSymbol": _text(pool.get("pairSymbol") or " -> ".join(route_symbols)),
        "direction": _text(pool.get("direction")),
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "protocol": _text(pool.get("protocol") or pool.get("dex")),
        "fee": _text(pool.get("fee") or pool.get("feeBps")),
        "tokenIn": _text(pool.get("tokenIn") or pool.get("tokenX") or pool.get("token0")),
        "tokenOut": _text(pool.get("tokenOut") or pool.get("tokenY") or pool.get("token1")),
        "anchorSymbol": _norm_symbol(pool.get("anchorSymbol")),
        "counterSymbol": _norm_symbol(pool.get("counterSymbol")),
        "pairAddress": _text(pool.get("pairAddress")),
    }


def _route_tokens(summary: dict[str, Any]) -> list[dict[str, Any]]:
    path_symbols = [_norm_symbol(value) for value in summary.get("pathSymbols") or [] if _norm_symbol(value)]
    route_addresses = [_text(value) for value in summary.get("routeAddresses") or [] if _text(value)]
    if not route_addresses:
        route_addresses = [_text(value) for value in summary.get("anchorAddresses") or [] if _text(value)]
    return [
        {
            "symbol": symbol,
            "address": route_addresses[index] if index < len(route_addresses) else "",
        }
        for index, symbol in enumerate(path_symbols)
    ]


def _route_address_path(summary: dict[str, Any], route: dict[str, Any]) -> list[str]:
    for key in ("routeAddresses", "pathAddresses", "anchorAddresses"):
        value = summary.get(key)
        if not isinstance(value, list):
            value = route.get(key)
        if not isinstance(value, list):
            continue
        addresses = [_text(item).lower() for item in value if _text(item)]
        if addresses:
            return addresses
    for pool in route.get("routePools") or []:
        if isinstance(pool, dict) and pool.get("routeAddresses"):
            addresses = [_text(item).lower() for item in pool["routeAddresses"] if _text(item)]
            if addresses:
                return addresses
    return []


def _closed_loop_route_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    route_tokens = _route_tokens(summary)
    return {
        "routeKey": _text(summary.get("routeKey")),
        "kind": _text(summary.get("kind")),
        "source": _text(summary.get("source")),
        "anchorSymbol": _norm_symbol(summary.get("anchorSymbol")),
        "counterSymbol": _norm_symbol(summary.get("counterSymbol")),
        "bridgeSymbol": _norm_symbol(summary.get("bridgeSymbol")),
        "pathText": _text(summary.get("pathText")),
        "pathSymbols": [_norm_symbol(value) for value in summary.get("pathSymbols") or [] if _norm_symbol(value)],
        "pathAddresses": [_text(value) for value in summary.get("routeAddresses") or [] if _text(value)],
        "routeTokens": route_tokens,
        "pairSymbols": [_text(value) for value in summary.get("pairSymbols") or [] if _text(value)],
        "poolIds": [_text(value) for value in summary.get("poolIds") or [] if _text(value)],
        "routePools": [_pool_dependency(pool) for pool in summary.get("routePools") or [] if isinstance(pool, dict)],
        "observationRef": _text(summary.get("observationRef")),
    }


def _borrowable_symbols(snapshot: dict[str, Any]) -> set[str]:
    return {
        _norm_symbol(value)
        for value in snapshot.get("borrowableSymbols") or []
        if _norm_symbol(value)
    }


def _closed_loop_dedup_key(summary: dict[str, Any], route: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    path_symbols = [
        _norm_symbol(value)
        for value in summary.get("pathSymbols") or route.get("pathSymbols") or []
        if _norm_symbol(value)
    ]
    anchor = _norm_symbol(summary.get("anchorSymbol") or route.get("anchorSymbol"))
    if not anchor and path_symbols:
        anchor = path_symbols[0]
    path_addresses = _route_address_path(summary, route)
    if path_addresses and len(path_addresses) == len(path_symbols):
        anchor = path_addresses[0]
        mids = tuple(path_addresses[1:-1]) if len(path_addresses) >= 3 else tuple(path_addresses)
    else:
        mids = tuple(path_symbols[1:-1]) if len(path_symbols) >= 3 and path_symbols[0] == path_symbols[-1] else tuple(path_symbols)
    if mids and tuple(reversed(mids)) < mids:
        mids = tuple(reversed(mids))
    # Reverse execution direction is the same closed loop.
    return (anchor, mids)


def _group_key(summary: dict[str, Any], route: dict[str, Any], anchor_symbol: str) -> str:
    path_addresses = _route_address_path(summary, route)
    if path_addresses:
        return path_addresses[0]
    anchor_address = _first_anchor_address(summary, route, anchor_symbol)
    return anchor_address.lower() or anchor_symbol


def group_closed_loop_routes_by_flash_loan_token(
    snapshot: dict[str, Any],
    *,
    include_routes: bool = True,
) -> list[dict[str, Any]]:
    """Group ``closedLoopRouteSummaries`` by their flash-loan anchor token.

    For each closed loop, ``start == end`` is the borrowable token. The group
    carries the shared flash loan token plus the deduplicated pool dependencies
    required to execute every route in that group.
    """

    borrowable = _borrowable_symbols(snapshot)
    groups: dict[str, dict[str, Any]] = {}
    seen_routes: set[tuple[str, tuple[str, ...]]] = set()
    for summary in snapshot.get("closedLoopRouteSummaries") or []:
        if not isinstance(summary, dict):
            continue
        path_symbols = [_norm_symbol(value) for value in summary.get("pathSymbols") or [] if _norm_symbol(value)]
        anchor_symbol = _norm_symbol(summary.get("anchorSymbol"))
        if not anchor_symbol and path_symbols and len(path_symbols) > 1:
            anchor_symbol = path_symbols[0]
        if not anchor_symbol:
            continue
        route = _closed_loop_route_from_summary(summary)
        if not route["routePools"] and not route["poolIds"]:
            continue
        dedup_key = _closed_loop_dedup_key(summary, route)
        is_duplicate_route = dedup_key in seen_routes
        seen_routes.add(dedup_key)

        group_key = _group_key(summary, route, anchor_symbol)
        group = groups.setdefault(
            group_key,
            {
                "flashLoanToken": {
                    "symbol": anchor_symbol,
                    "address": _first_anchor_address(summary, route, anchor_symbol),
                },
                "startToken": {
                    "symbol": anchor_symbol,
                    "address": _first_anchor_address(summary, route, anchor_symbol),
                },
                "endToken": {
                    "symbol": anchor_symbol,
                    "address": _first_anchor_address(summary, route, anchor_symbol),
                },
                "borrowable": anchor_symbol in borrowable,
                "routeCount": 0,
                "poolDependencyCount": 0,
                "routes": [],
                "poolDependencies": [],
                "poolDependencyIndex": {},
            },
        )
        if not is_duplicate_route:
            if include_routes:
                group["routes"].append(route)
            group["routeCount"] += 1
        for pool in route["routePools"]:
            identity = _pool_identity(pool)
            if not identity:
                continue
            if identity in group["poolDependencyIndex"]:
                continue
            group["poolDependencyIndex"][identity] = len(group["poolDependencies"])
            group["poolDependencies"].append(pool)
        group["poolDependencyCount"] = len(group["poolDependencies"])
        if not include_routes:
            group.pop("routes", None)

    result: list[dict[str, Any]] = []
    ordered = sorted(
        groups.items(),
        key=lambda item: (0 if item[1]["borrowable"] else 1, item[0]),
    )
    for _key, group in ordered:
        group.pop("poolDependencyIndex", None)
        result.append(group)
    return result


def _first_anchor_address(summary: dict[str, Any], route: dict[str, Any], anchor_symbol: str) -> str:
    anchor_addresses = [_text(value) for value in summary.get("anchorAddresses") or [] if _text(value)]
    if anchor_addresses:
        return anchor_addresses[0]
    route_addresses = route.get("pathAddresses") or []
    if route_addresses:
        return route_addresses[0]
    for pool in route.get("routePools") or []:
        for symbol, address in zip(pool.get("routeSymbols") or [], pool.get("routeAddresses") or []):
            if _norm_symbol(symbol) == anchor_symbol and _text(address):
                return address
    return ""


def closed_loop_route_groups_payload(
    snapshot: dict[str, Any],
    *,
    include_routes: bool = True,
) -> dict[str, Any]:
    """Build a compact API-friendly payload from an overview snapshot."""

    groups = group_closed_loop_routes_by_flash_loan_token(
        snapshot,
        include_routes=include_routes,
    )
    return {
        "ok": True,
        "chainId": snapshot.get("chain_id"),
        "network": _text(snapshot.get("network") or snapshot.get("chain_key")),
        "mode": "dex_closed_loop_groups",
        "includeRoutes": bool(include_routes),
        "summary": {
            "borrowableSymbolCount": len(_borrowable_symbols(snapshot)),
            "groupCount": len(groups),
            "closedLoopRouteCount": sum(int(group.get("routeCount") or 0) for group in groups),
        },
        "groups": groups,
    }


__all__ = [
    "closed_loop_route_groups_payload",
    "group_closed_loop_routes_by_flash_loan_token",
]
