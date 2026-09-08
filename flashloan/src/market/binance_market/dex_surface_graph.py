"""Token and pool graph helpers for DEX surface payloads."""

from __future__ import annotations

from typing import Any

from market.binance_market.dex_surface_types import (
    PRICE_ALIAS_GROUPS,
    STABLE_ANCHOR_SYMBOLS,
    _int,
    _norm_symbol,
    _number,
    _pair_symbols,
    _pool_address,
    _pool_protocol,
    _pool_symbols,
    _pool_tokens,
    _token_address,
)
from market.binance_market.dex_surface_v4 import (
    _pool_reserve_amount,
    _pool_reserve_usd,
    _pool_tvl_usd,
)


def _pair_tokens(pair: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = []
    for address_key, symbol_key in (("tokenX", "tokenXSymbol"), ("tokenY", "tokenYSymbol")):
        symbol = _norm_symbol(pair.get(symbol_key))
        address = _token_address(pair.get(address_key))
        if symbol:
            tokens.append({"symbol": symbol, "address": address})
    if len(tokens) >= 2 and all(token.get("address") for token in tokens):
        return tokens[:2]
    for pool in _pools_for_pair(pair):
        pool_tokens = _pool_tokens(pool)
        if len(pool_tokens) >= 2:
            return [{"symbol": token["symbol"], "address": token["address"]} for token in pool_tokens[:2]]
    return tokens[:2]

def _token_key(token: dict[str, Any]) -> str:
    address = _token_address(token.get("address"))
    if address:
        return f"address:{address}"
    return f"symbol:{_norm_symbol(token.get('symbol'))}"

def _route_symbols_from_tokens(tokens: list[dict[str, Any]]) -> list[str]:
    return [_norm_symbol(token.get("symbol")) for token in tokens if _norm_symbol(token.get("symbol"))]

def _route_addresses_from_tokens(tokens: list[dict[str, Any]]) -> list[str]:
    return [_token_address(token.get("address")) for token in tokens]

def _propagate_price_aliases(price_map: dict[str, float]) -> None:
    for group in PRICE_ALIAS_GROUPS:
        known = [price_map[symbol] for symbol in group if price_map.get(symbol)]
        if not known:
            continue
        price = sum(known) / len(known)
        for symbol in group:
            price_map.setdefault(symbol, price)

def _price_map_from_pairs(pairs: list[dict[str, Any]]) -> dict[str, float]:
    price_map: dict[str, float] = {symbol: 1.0 for symbol in STABLE_ANCHOR_SYMBOLS}
    pools = [pool for pair in pairs for pool in _pools_for_pair(pair)]
    _propagate_price_aliases(price_map)
    for pool in pools:
        if _pool_protocol(pool) == "uniswap_v4":
            continue
        symbols = _pool_symbols(pool)
        for index, symbol in enumerate(symbols[:2]):
            amount = _pool_reserve_amount(pool, index)
            usd = _pool_reserve_usd(pool, index, price_map={})
            if amount and usd and usd > 0:
                price_map[symbol] = usd / amount
    _propagate_price_aliases(price_map)
    for _ in range(4):
        changed = False
        for pool in pools:
            if _pool_protocol(pool) == "uniswap_v4":
                continue
            symbols = _pool_symbols(pool)
            if len(symbols) < 2:
                continue
            amounts = [_pool_reserve_amount(pool, 0), _pool_reserve_amount(pool, 1)]
            tvl = _pool_tvl_usd(pool)
            for known_index, missing_index in ((0, 1), (1, 0)):
                known_symbol = symbols[known_index]
                missing_symbol = symbols[missing_index]
                if price_map.get(missing_symbol) or not price_map.get(known_symbol) or not amounts[missing_index]:
                    continue
                if tvl > 0 and amounts[known_index]:
                    missing_usd = tvl - amounts[known_index] * price_map[known_symbol]
                    if missing_usd > 0:
                        price_map[missing_symbol] = missing_usd / amounts[missing_index]
                        changed = True
                        continue
                if amounts[known_index] and amounts[missing_index]:
                    price_map[missing_symbol] = price_map[known_symbol] * amounts[known_index] / amounts[missing_index]
                    changed = True
        _propagate_price_aliases(price_map)
        if not changed:
            break
    return {symbol: price for symbol, price in price_map.items() if price > 0}

def _unique_symbols(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for row in rows:
        for symbol in _pool_symbols(row):
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
    return result

def _pool_count(pair: dict[str, Any], key: str) -> int:
    return len(pair.get(key) or []) or _int(pair.get(key.replace("Pools", "PoolCount")))

def _pools_for_pair(pair: dict[str, Any]) -> list[dict[str, Any]]:
    return list(pair.get("executionPools") or pair.get("preferredPools") or pair.get("strictQualityPools") or [])

def _route_pools(route: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    pools: list[dict[str, Any]] = []
    for key in ("executionPools", "preferredPools", "strictQualityPools"):
        for pool in route.get(key) or []:
            pool_id = str(pool.get("pool") or id(pool)).lower()
            if pool_id in seen:
                continue
            seen.add(pool_id)
            pools.append(pool)
    for pool in route.get("routePools") or []:
        if not isinstance(pool, dict):
            continue
        pool_id = str(pool.get("pool") or pool.get("poolId") or id(pool)).lower()
        if pool_id in seen:
            continue
        seen.add(pool_id)
        pools.append(pool)
    for pair in route.get("routePairs") or []:
        if not isinstance(pair, dict):
            continue
        for pool in pair.get("pools") or []:
            if not isinstance(pool, dict):
                continue
            pool_id = str(pool.get("pool") or pool.get("poolId") or id(pool)).lower()
            if pool_id in seen:
                continue
            seen.add(pool_id)
            pools.append(pool)
    return pools

def _route_score(route: dict[str, Any]) -> tuple[float, int, str]:
    pools = _route_pools(route)
    tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
    return (tvl, len(pools), str(route.get("symbolPair") or ""))

def _pair_score(pair: dict[str, Any]) -> tuple[float, int, str]:
    pools = _pools_for_pair(pair)
    tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
    return (tvl, len(pools), str(pair.get("symbolPair") or ""))

def _pair_token_entry(pair: dict[str, Any], token_key: str) -> dict[str, Any] | None:
    for token in _pair_tokens(pair):
        if _token_key(token) == token_key:
            return token
    return None

def _token_graph(
    pairs: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], dict[str, set[str]], dict[frozenset[str], dict[str, Any]]]:
    token_by_key: dict[str, dict[str, Any]] = {}
    symbol_to_keys: dict[str, set[str]] = {}
    neighbor_keys: dict[str, set[str]] = {}
    pair_by_edge: dict[frozenset[str], dict[str, Any]] = {}
    for pair in pairs:
        tokens = _pair_tokens(pair)
        if len(tokens) < 2:
            continue
        left, right = tokens[:2]
        left_key = _token_key(left)
        right_key = _token_key(right)
        if not left_key or not right_key or left_key == right_key:
            continue
        token_by_key.setdefault(left_key, left)
        token_by_key.setdefault(right_key, right)
        symbol_to_keys.setdefault(_norm_symbol(left.get("symbol")), set()).add(left_key)
        symbol_to_keys.setdefault(_norm_symbol(right.get("symbol")), set()).add(right_key)
        neighbor_keys.setdefault(left_key, set()).add(right_key)
        neighbor_keys.setdefault(right_key, set()).add(left_key)
        edge_key = frozenset((left_key, right_key))
        current = pair_by_edge.get(edge_key)
        if current is None or _pair_score(pair) > _pair_score(current):
            pair_by_edge[edge_key] = pair
    return token_by_key, symbol_to_keys, neighbor_keys, pair_by_edge

def _pair_symbol_graph(pairs: list[dict[str, Any]]) -> dict[frozenset[str], dict[str, Any]]:
    pair_by_edge: dict[frozenset[str], dict[str, Any]] = {}
    for pair in pairs:
        symbols = [symbol for symbol in _pair_symbols(pair) if symbol]
        if len(symbols) < 2:
            continue
        left, right = symbols[:2]
        if left == right:
            continue
        edge_key = frozenset((left, right))
        current = pair_by_edge.get(edge_key)
        if current is None or _pair_score(pair) > _pair_score(current):
            pair_by_edge[edge_key] = pair
    return pair_by_edge

def _pool_matches_edge(
    pool: dict[str, Any],
    from_symbol: str,
    to_symbol: str,
    from_address: str | None = None,
    to_address: str | None = None,
) -> bool:
    tokens = _pool_tokens(pool)
    from_key = _token_address(from_address)
    to_key = _token_address(to_address)
    if from_key and to_key:
        addresses = {_token_address(token.get("address")) for token in tokens[:2]}
        return from_key in addresses and to_key in addresses and from_key != to_key
    symbols = set(_pool_symbols(pool)[:2])
    return from_symbol in symbols and to_symbol in symbols and len(symbols) == 2
