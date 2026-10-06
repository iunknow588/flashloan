from typing import Any

from market.binance_market.dex_surface_graph import (
    _pool_count,
    _pools_for_pair,
)
from market.binance_market.dex_surface_observation_chain import (
    _chain_observation_function,
    _chain_network_name,
)
from market.binance_market.dex_surface_types import (
    _int,
    _is_stable_anchor,
    _norm_symbol,
    _number,
    _pair_symbols,
    _pool_address,
    _pool_protocol,
    _route_symbol_allowed,
    _split_symbol_components,
    _token_address,
)


def _borrowable_symbols_from_discovery(chain_id: int | None) -> list[str]:
    try:
        try:
            from market.binance_market import dex_surfaces

            compatibility = getattr(dex_surfaces, "query_flash_loan_pool_discovery", None)
        except Exception:
            compatibility = None
        if callable(compatibility):
            snapshot = compatibility(chain_id=chain_id)
        else:
            from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery

            snapshot = query_flash_loan_pool_discovery(chain_id=chain_id)
    except Exception:
        return []
    symbols: list[str] = []
    for raw in (snapshot.get("raw_snapshots") or {}).values():
        if not isinstance(raw, dict):
            continue
        raw_chain_id = _int(raw.get("chainId") or raw.get("chain_id"))
        if chain_id is not None and raw_chain_id and raw_chain_id != int(chain_id):
            continue
        for source in raw.get("sources") or []:
            symbol = _norm_symbol((source or {}).get("token_symbol"))
            if symbol and bool((source or {}).get("flash_borrowable", True)):
                symbols.append(symbol)
    if symbols:
        return sorted(set(symbols))
    fallback: set[str] = set()
    for protocol in snapshot.get("protocols") or []:
        for key in ("token_symbol", "asset_symbol", "symbol"):
            symbol = _norm_symbol(protocol.get(key))
            if symbol:
                fallback.add(symbol)
    return sorted(fallback)


def _v4_poolmanager_balance_symbols(pairs: list[dict[str, Any]]) -> list[str]:
    symbols: set[str] = set()
    for pair in pairs:
        for pool in _pools_for_pair(pair):
            if _pool_protocol(pool) != "uniswap_v4":
                continue
            if not (pool.get("poolManager") or pool.get("pool_manager") or pool.get("poolIdentityMode") == "pool_id"):
                continue
            balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
            for index in (0, 1):
                symbol = _norm_symbol(pool.get(f"token{index}Symbol"))
                if not symbol or not _route_symbol_allowed(symbol):
                    continue
                if _number(balances.get(f"token{index}")) > 0:
                    symbols.add(symbol)
    return sorted(symbols)


def _chain_borrowable_symbols_from_pool_payload(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    function = _chain_observation_function(chain_id, "borrowable_symbols_from_pool_payload")
    if function is None:
        return []
    result = function(pool_payload, chain_id=chain_id, protocols=protocols)
    return [_norm_symbol(symbol) for symbol in result or [] if _route_symbol_allowed(symbol)]


def _borrowable_symbols(
    pool_payload: dict[str, Any],
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    discovered = set(_borrowable_symbols_from_discovery(chain_id))
    for pair in pool_payload.get("pairs") or []:
        linkage = pair.get("flashLoanLinkage") or {}
        for symbol in linkage.get("borrow_symbols") or []:
            normalized = _norm_symbol(symbol)
            if normalized:
                discovered.add(normalized)
    discovered.update(_v4_poolmanager_balance_symbols(pool_payload.get("pairs") or []))
    discovered.update(_chain_borrowable_symbols_from_pool_payload(pool_payload, chain_id=chain_id, protocols=protocols))
    expanded = {part for symbol in discovered for part in _split_symbol_components(symbol)}
    return sorted(discovered | expanded)


def _graph_stable_anchor_symbols(pairs: list[dict[str, Any]]) -> list[str]:
    anchors: set[str] = set()
    for pair in pairs:
        for symbol in _pair_symbols(pair):
            if _is_stable_anchor(symbol) and _route_symbol_allowed(symbol):
                anchors.add(symbol)
    return sorted(anchors)


def _observation_anchor_symbols(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
    borrow_symbols: list[str] | None = None,
) -> list[str]:
    pairs = pool_payload.get("pairs") or []
    anchors = _candidate_anchor_symbols(pool_payload, chain_id, protocols=protocols)
    if anchors:
        return anchors
    anchors = {_norm_symbol(symbol) for symbol in (borrow_symbols or _borrowable_symbols(pool_payload, chain_id, protocols=protocols))}
    anchors = {symbol for symbol in anchors if symbol and ":" not in symbol and _route_symbol_allowed(symbol)}
    if anchors:
        return sorted(anchors)
    return _graph_stable_anchor_symbols(pairs)


def _candidate_anchor_symbols(
    pool_payload: dict[str, Any],
    chain_id: int | None,
    *,
    protocols: list[str] | None = None,
) -> list[str]:
    function = _chain_observation_function(chain_id, "observation_anchor_symbols")
    if function is None:
        return []
    result = function(pool_payload, chain_id=chain_id, protocols=protocols)
    return sorted({_norm_symbol(symbol) for symbol in result or [] if _route_symbol_allowed(symbol)})


def _candidate_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        pair
        for pair in pairs
        if _pool_count(pair, "preferredPools") or _pool_count(pair, "executionPools") or _pool_count(pair, "strictQualityPools")
    ]


def _borrow_anchor_symbols(pool_payload: dict[str, Any], chain_id: int | None, protocols: list[str] | None = None) -> list[str]:
    anchors = _candidate_anchor_symbols(pool_payload, chain_id, protocols=protocols)
    if anchors:
        return anchors
    borrow_symbols = _borrowable_symbols(pool_payload, chain_id, protocols=protocols)
    anchors = [symbol for symbol in borrow_symbols if _is_stable_anchor(symbol) and ":" not in symbol]
    if anchors:
        return sorted(set(anchors))
    return sorted({symbol for symbol in borrow_symbols if ":" not in symbol})


def _sequence_rows_by_counter(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        counter_symbol = _norm_symbol(row.get("counterSymbol"))
        pool_id = _pool_address(row)
        if not counter_symbol or not pool_id:
            continue
        grouped.setdefault(counter_symbol, {})[pool_id] = row
    return grouped


def _group_token_pool_rows(token: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    token_symbol = _norm_symbol(token.get("symbol"))
    token_address = _token_address(token.get("address"))
    candidates = [
        row
        for row in token.get("pools") or []
        if isinstance(row, dict) and _pool_address(row)
    ]
    if not candidates:
        candidates = [
            row
            for row in rows
            if isinstance(row, dict)
            and _pool_address(row)
            and _norm_symbol(row.get("counterSymbol")) == token_symbol
        ]
    if token_address:
        addressed = [
            row
            for row in candidates
            if _token_address(row.get("counterAddress")) == token_address
        ]
        if addressed:
            candidates = addressed
    return _best_pool_rows(candidates)


def _token_instance_keys(token: dict[str, Any], symbol_to_keys: dict[str, set[str]]) -> list[str]:
    address = _token_address(token.get("address"))
    if address:
        return [f"address:{address}"]
    symbol = _norm_symbol(token.get("symbol"))
    if not symbol:
        return []
    return sorted(symbol_to_keys.get(symbol) or [f"symbol:{symbol}"])


def _anchor_group_direction_rows(group: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    rows = [row for row in group.get(f"{direction}Pools") or [] if isinstance(row, dict) and _pool_address(row)]
    if rows or group.get(f"{direction}PoolsRef") != "sharedPools":
        return rows
    directional_rows: list[dict[str, Any]] = []
    route_text_key = f"{direction}RouteText"
    route_key_key = f"{direction}RouteKey"
    for row in group.get("sharedPools") or []:
        if not isinstance(row, dict) or not _pool_address(row):
            continue
        copied = dict(row)
        copied["direction"] = direction
        if row.get(route_text_key):
            copied["routeText"] = row.get(route_text_key)
        if row.get(route_key_key):
            copied["routeKey"] = row.get(route_key_key)
        directional_rows.append(copied)
    return directional_rows


def _anchor_group_direction_tokens(group: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    tokens = [
        row
        for row in group.get(f"{direction}Tokens") or []
        if isinstance(row, dict) and _norm_symbol(row.get("symbol"))
    ]
    if tokens or group.get(f"{direction}TokensRef") != "sharedTokens":
        return tokens
    return [
        row
        for row in group.get("sharedTokens") or []
        if isinstance(row, dict) and _norm_symbol(row.get("symbol"))
    ]


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


def _best_pool_rows(pools: list[dict[str, Any]], *, exclude_pool_ids: set[str] | None = None) -> list[dict[str, Any]]:
    from market.binance_market.dex_surface_v4 import _pool_tvl_usd

    exclude = {str(pool_id).strip().lower() for pool_id in (exclude_pool_ids or set()) if str(pool_id or "").strip()}
    rows = [pool for pool in pools if isinstance(pool, dict) and _pool_address(pool)]
    if exclude:
        rows = [pool for pool in rows if _pool_address(pool).lower() not in exclude]
    # Market observation is the liquidity-qualified handoff: explicitly
    # unusable pools must never form the single route variant carried into
    # overview/quote routing.  Pools without evidence are kept behind usable
    # ones so older legacy rows still remain discoverable.
    usable_rows = [pool for pool in rows if _pool_usable_for_observation(pool)]
    evidence_rows = [pool for pool in rows if not _pool_usable_for_observation(pool)]
    ordered = usable_rows + evidence_rows
    ordered.sort(
        key=lambda pool: (
            pool in evidence_rows,
            -float(_pool_tvl_usd(pool) or 0),
            -float(_number((pool.get("balances") or {}).get("tvlUsd")) or 0),
            str(_pool_address(pool)),
        ),
    )
    return ordered
