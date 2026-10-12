"""Shared Robinhood V4 SQLite market-observation readers.

Mainnet and testnet chain modules should only provide configuration
(network, chain id, anchor symbols, market id). Everything else in this file
is intentionally chain-agnostic and reads the authoritative business tables.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import load_chain_tokens
from core.db.storage_dex_candidate import MAX_USABLE_CANDIDATE_FIXED_FEE
from core.rs_bridge import (
    load_market_observation,
    load_v4_closed_loop,
    load_v4_exchange_pools,
    load_v4_pool_liquidity,
)


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
V4_DYNAMIC_FEE_FLAG = 8388608
V4_SPECIAL_FEE_THRESHOLD = 100_000


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _text(value: object) -> str:
    return str(value or "").strip()


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _address(value: object) -> str:
    text = _text(value)
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return value


def _pool_id(pool: dict[str, Any]) -> str:
    return _text(
        pool.get("pool")
        or pool.get("poolId")
        or pool.get("pool_id")
        or pool.get("id")
    )


def _pool_key(pool: dict[str, Any]) -> str:
    return _pool_id(pool).lower()


def _pool_edge(pool: dict[str, Any]) -> tuple[str, str] | None:
    token0 = _address(pool.get("token0") or pool.get("currency0"))
    token1 = _address(pool.get("token1") or pool.get("currency1"))
    if not token0 or not token1 or token0 == token1:
        return None
    return tuple(sorted((token0, token1)))


def _pool_fee(pool: dict[str, Any]) -> int | None:
    raw = pool.get("fee")
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _pool_dynamic_fee(pool: dict[str, Any]) -> bool:
    fee = _pool_fee(pool)
    return bool(fee) and bool(fee & V4_DYNAMIC_FEE_FLAG)


def _pool_special_fee(pool: dict[str, Any]) -> bool:
    fee = _pool_fee(pool)
    if not fee:
        return True
    return bool(fee & V4_DYNAMIC_FEE_FLAG) or fee > V4_SPECIAL_FEE_THRESHOLD


def _pool_fee_label(pool: dict[str, Any]) -> str | int | None:
    if _pool_dynamic_fee(pool):
        return "dynamic"
    if _pool_special_fee(pool):
        return "special"
    return _pool_fee(pool)


def _observation_pool_usable(pool: dict[str, Any]) -> bool:
    """Return whether a pool belongs to the fixed-fee no-hook observation surface.

    Liquidity is a dynamic field refreshed separately and is intentionally not
    a static pool-formation condition.
    """
    fee = _pool_fee(pool)
    if fee is None or fee <= 0 or fee > MAX_USABLE_CANDIDATE_FIXED_FEE:
        return False
    if _pool_dynamic_fee(pool):
        return False
    hooks = str(pool.get("hooks") or "").strip()
    if hooks and hooks.lower() != ZERO_ADDRESS.lower():
        return False
    return True


def _pool_usable_for_observation(pool: dict[str, Any]) -> bool:
    return _observation_pool_usable(pool)


def _chain_token_symbol_map(
    database_url: str,
    *,
    network: str,
    chain_id: int,
) -> dict[str, str]:
    rows = load_chain_tokens(database_url, network=network, chain_id=chain_id)
    return {
        _address(row.get("token_address")): _symbol(row.get("token_symbol"))
        for row in rows
        if _address(row.get("token_address"))
    }


def _db_pool_to_observation_row(
    pool: dict[str, Any],
    liquidity: dict[str, Any] | None,
    *,
    symbol_map: dict[str, str],
) -> dict[str, Any] | None:
    pool_id = _pool_id(pool)
    token0 = _address(pool.get("token0") or pool.get("currency0"))
    token1 = _address(pool.get("token1") or pool.get("currency1"))
    if not pool_id or not token0 or not token1:
        return None
    fee = _pool_fee(pool)
    dynamic_fee = _pool_dynamic_fee(pool)
    special_fee = _pool_special_fee(pool)
    usable = _observation_pool_usable(pool)
    token0_symbol = (
        symbol_map.get(token0)
        or _symbol(pool.get("token0_symbol") or pool.get("token0Symbol"))
        or token0[:8].upper()
    )
    token1_symbol = (
        symbol_map.get(token1)
        or _symbol(pool.get("token1_symbol") or pool.get("token1Symbol"))
        or token1[:8].upper()
    )
    balances = _json_loads(
        (liquidity or {}).get("balances_json")
        or pool.get("balances_json")
        or {}
    )
    if not isinstance(balances, dict):
        balances = {}
    depth_band = _json_loads(
        (liquidity or {}).get("depth_band_json")
        or pool.get("depth_band_json")
        or {}
    )
    if not isinstance(depth_band, dict):
        depth_band = {}
    liquidity_index = (liquidity or {}).get("liquidity_index")
    if liquidity_index is None:
        raw_liquidity = (liquidity or {}).get("liquidity")
        if raw_liquidity not in (None, ""):
            try:
                liquidity_index = float(raw_liquidity)
            except (TypeError, ValueError):
                liquidity_index = pool.get("liquidity_index")
        else:
            liquidity_index = pool.get("liquidity_index")
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": pool.get("pool_identity_mode") or "v4_pool_id",
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "fee": fee,
        "dynamicFee": dynamic_fee,
        "specialFee": special_fee,
        "feeLabel": (
            pool.get("fee_label")
            or pool.get("feeLabel")
            or _pool_fee_label(pool)
        ),
        "observationUsable": usable,
        "usableForObservation": usable,
        "tickSpacing": pool.get("tick_spacing") or pool.get("tickSpacing"),
        "hooks": pool.get("hooks"),
        "poolManager": pool.get("pool_manager") or pool.get("poolManager") or "",
        "poolKey": _json_loads(pool.get("pool_key_json") or {}),
        "token0": token0,
        "token1": token1,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "token0Decimals": pool.get("token0_decimals") or pool.get("token0Decimals"),
        "token1Decimals": pool.get("token1_decimals") or pool.get("token1Decimals"),
        "sqrtPriceX96": (
            (liquidity or {}).get("sqrt_price_x96")
            or pool.get("sqrt_price_x96")
            or ""
        ),
        "tick": (
            (liquidity or {}).get("tick")
            if (liquidity or {}).get("tick") is not None
            else pool.get("tick")
        ),
        "liquidity": (liquidity or {}).get("liquidity") or "",
        "liquidityIndex": liquidity_index,
        "hasLiquidity": (liquidity or {}).get("has_liquidity"),
        "estimatedTvlUsd": (liquidity or {}).get("estimated_tvl_usd"),
        "balances": balances,
        "liquiditySource": "sqlite_db",
        "liquidityReadAt": (
            (liquidity or {}).get("liquidity_read_at")
            or pool.get("liquidity_read_at")
        ),
        "liquidityBlockNumber": (
            (liquidity or {}).get("liquidity_block_number")
            or pool.get("liquidity_block_number")
        ),
        "depthStatus": (
            (liquidity or {}).get("depth_status")
            or pool.get("depth_status")
            or ""
        ),
        "depthBand": depth_band,
        "recentSwapActive": (liquidity or {}).get("recent_swap_active"),
        "symbolPair": (
            f"{token0_symbol}:{token1_symbol}"
            if token0_symbol and token1_symbol
            else ""
        ),
        "addressPair": pool.get("address_pair") or f"{token0}:{token1}",
        "source": "sqlite_db",
        "status": "initialized_pool",
    }


def load_db_observation_pools(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_symbols: Iterable[str],
    database_url: str | None = None,
    require_nonempty: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load the complete Y-Z pool surface from SQLite business tables."""
    database_url = str(absolute_database_path(database_url))
    symbol_map = _chain_token_symbol_map(
        database_url,
        network=network,
        chain_id=chain_id,
    )
    merged_pools: dict[str, dict[str, Any]] = {}
    total_pairs = 0
    for anchor_symbol in anchor_symbols:
        payload = load_market_observation(
            network,
            chain_id,
            protocol,
            anchor_symbol,
            database_url=database_url,
        )
        pools = payload.get("pools") or []
        liquidity = payload.get("liquidity") or []
        total_pairs += len(payload.get("pairs") or [])
        liquidity_by_pool_id = {
            _pool_key(row): row
            for row in liquidity
            if _pool_key(row)
        }
        for pool in pools:
            pool_key = _pool_key(pool)
            if not pool_key:
                continue
            row = _db_pool_to_observation_row(
                pool,
                liquidity_by_pool_id.get(pool_key),
                symbol_map=symbol_map,
            )
            if row is not None and _observation_pool_usable(row):
                merged_pools[pool_key] = row
    pool_rows = sorted(merged_pools.values(), key=lambda row: _pool_key(row))
    if require_nonempty and not pool_rows:
        raise RuntimeError(
            f"market observation SQLite tables are empty for "
            f"{network}/{chain_id}/{protocol}; rebuild anchor data before reading"
        )
    return pool_rows, {
        "source": "rust_sqlite",
        "databasePath": database_url,
        "pools": pool_rows,
        "directionCount": total_pairs,
    }


def load_db_exchange_pools(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    database_url: str | None = None,
    anchor_addresses: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return every SQLite V4 exchange pool, optionally anchor-adjacent only."""
    database_url = str(absolute_database_path(database_url))
    symbol_map = _chain_token_symbol_map(
        database_url,
        network=network,
        chain_id=chain_id,
    )
    rows = load_v4_exchange_pools(
        network,
        chain_id,
        protocol,
        database_url=database_url,
    )
    liquidity_rows = load_v4_pool_liquidity(
        network,
        chain_id,
        protocol,
        database_url=database_url,
    )
    liquidity_by_pool_id = {
        _pool_key(row): row
        for row in liquidity_rows
        if _pool_key(row)
    }
    pools: list[dict[str, Any]] = []
    for pool in rows:
        if anchor_addresses is not None:
            edge = _pool_edge(pool)
            if edge is None or set(edge).isdisjoint(anchor_addresses):
                continue
        row = _db_pool_to_observation_row(
            pool,
            liquidity_by_pool_id.get(_pool_key(pool)),
            symbol_map=symbol_map,
        )
        if row is not None:
            pools.append(row)
    return pools


def _db_closed_loop_step_row(
    pool: dict[str, Any],
    *,
    from_address: str,
    to_address: str,
    from_symbol: str,
    to_symbol: str,
    direction: str,
    symbol_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    base = _db_pool_to_observation_row(
        pool,
        None,
        symbol_map=symbol_map or {},
    )
    if base is None:
        return None
    from_symbol = _symbol(from_symbol) or symbol_map.get(_address(from_address), "") or from_address[:8].upper()
    to_symbol = _symbol(to_symbol) or symbol_map.get(_address(to_address), "") or to_address[:8].upper()
    return {
        "pool": base["pool"],
        "poolId": base["poolId"],
        "direction": direction,
        "anchorSymbol": from_symbol,
        "anchorAddress": from_address,
        "counterSymbol": to_symbol,
        "counterAddress": to_address,
        "routeSymbols": [from_symbol, to_symbol],
        "routeAddresses": [from_address, to_address],
        "routeText": f"{from_symbol} -> {to_symbol}",
        "poolIdentityMode": base["poolIdentityMode"],
        "protocol": base["protocol"],
        "dex": base["dex"],
        "fee": base["fee"],
        "dynamicFee": base["dynamicFee"],
        "specialFee": base["specialFee"],
        "feeLabel": base["feeLabel"],
        "observationUsable": base["observationUsable"],
        "usableForObservation": base["usableForObservation"],
        "tickSpacing": base["tickSpacing"],
        "hooks": base["hooks"],
        "poolManager": base["poolManager"],
        "poolKey": base["poolKey"],
        "token0": base["token0"],
        "token1": base["token1"],
        "token0Symbol": base["token0Symbol"] or from_symbol,
        "token1Symbol": base["token1Symbol"] or to_symbol,
        "source": "v4_closed_loop_db",
        "status": "fixed_fee_no_hook_observable",
        "liquidity": base["liquidity"],
        "liquidityIndex": base["liquidityIndex"],
        "hasLiquidity": base["hasLiquidity"],
        "estimatedTvlUsd": base["estimatedTvlUsd"],
        "balances": base["balances"],
        "depthBand": base["depthBand"],
        "depthStatus": base["depthStatus"],
        "sqrtPriceX96": base["sqrtPriceX96"],
        "tick": base["tick"],
        "recentSwapActive": base["recentSwapActive"],
        "liquidityReadAt": base["liquidityReadAt"],
        "liquidityBlockNumber": base["liquidityBlockNumber"],
    }


def db_closed_loop_sequences(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    kind: str,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """Return validated three-hop closed-loop sequences from SQLite."""
    database_url = str(absolute_database_path(database_url))
    symbol_map = _chain_token_symbol_map(
        database_url,
        network=network,
        chain_id=chain_id,
    )
    payload = load_v4_closed_loop(
        network,
        chain_id,
        protocol,
        market_id,
        database_url=database_url,
    )
    loops = payload.get("loops") or []
    hops = payload.get("hops") or []
    if not loops:
        return []
    hops_by_loop: dict[str, list[dict[str, Any]]] = {}
    for hop in hops:
        loop_key = _text(hop.get("loop_key") or hop.get("loopKey")).lower()
        if loop_key:
            hops_by_loop.setdefault(loop_key, []).append(hop)
    for group in hops_by_loop.values():
        group.sort(key=lambda row: _int(row.get("hop_index") or row.get("hopIndex")))
    exchange_rows = load_v4_exchange_pools(
        network,
        chain_id,
        protocol,
        database_url=database_url,
    )
    pool_by_id = {_pool_key(row): row for row in exchange_rows if _pool_key(row)}
    sequences: list[dict[str, Any]] = []
    for loop in loops:
        loop_key = _text(loop.get("loop_key") or loop.get("loopKey")).lower()
        path_addresses = _json_loads(
            loop.get("path_addresses_json")
            or loop.get("pathAddresses")
            or []
        )
        path_symbols = _json_loads(
            loop.get("path_symbols_json")
            or loop.get("pathSymbols")
            or []
        )
        path_text = _text(loop.get("path_text") or loop.get("pathText"))
        anchor_address = _address(loop.get("anchor_address") or loop.get("anchorAddress"))
        anchor_symbol = _symbol(loop.get("anchor_symbol") or loop.get("anchorSymbol"))
        if not anchor_address or len(path_addresses) < 4 or not path_text:
            continue
        route_rows: list[dict[str, Any]] = []
        hop_candidates: list[dict[str, Any]] = []
        candidate_counts = {"x-y": 0, "y-z": 0, "z-x": 0}
        for hop in hops_by_loop.get(loop_key) or []:
            direction = {
                1: "yc",
                2: "bridge",
                3: "zc",
            }.get(_int(hop.get("hop_index") or hop.get("hopIndex")), "")
            if not direction:
                continue
            pool_id = _text(hop.get("pool_id") or hop.get("poolId")).lower()
            pool = pool_by_id.get(pool_id)
            if pool is None:
                continue
            from_address = _address(hop.get("input_address") or hop.get("inputAddress"))
            to_address = _address(hop.get("output_address") or hop.get("outputAddress"))
            if not from_address or not to_address:
                continue
            from_symbol = symbol_map.get(from_address) or (
                "ETH" if from_address == ZERO_ADDRESS.lower() else from_address[:8].upper()
            )
            to_symbol = symbol_map.get(to_address) or (
                "ETH" if to_address == ZERO_ADDRESS.lower() else to_address[:8].upper()
            )
            route_step = _db_closed_loop_step_row(
                pool,
                from_address=from_address,
                to_address=to_address,
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                direction=direction,
                symbol_map=symbol_map,
            )
            if route_step is None:
                continue
            route_rows.append(route_step)
            leg = {"yc": "x-y", "bridge": "y-z", "zc": "z-x"}[direction]
            candidate_counts[leg] = 1
            hop_candidates.append(
                {
                    "leg": leg,
                    "direction": direction,
                    "fromSymbol": from_symbol,
                    "toSymbol": to_symbol,
                    "fromAddress": from_address,
                    "toAddress": to_address,
                    "symbolPair": f"{from_symbol} -> {to_symbol}",
                    "poolCount": 1,
                    "usablePoolCount": 1,
                    "selectionBasis": "fixed_fee_no_hook_low_fee",
                    "preferredPoolId": pool_id,
                    "preferredPoolUsable": True,
                    "pools": [route_step],
                }
            )
        if len(route_rows) != 3:
            continue
        yc_step = next(
            (step for step in route_rows if _text(step.get("direction")).lower() == "yc"),
            None,
        )
        bridge_step = next(
            (step for step in route_rows if _text(step.get("direction")).lower() == "bridge"),
            None,
        )
        zc_step = next(
            (step for step in route_rows if _text(step.get("direction")).lower() == "zc"),
            None,
        )
        bridge_symbol = _symbol(bridge_step.get("counterSymbol")) if bridge_step else ""
        counter_symbol = _symbol(zc_step.get("fromSymbol")) if zc_step else ""
        sequences.append(
            {
                "kind": kind,
                "source": "v4_closed_loop_db",
                "anchorSymbol": anchor_symbol,
                "anchorAddress": anchor_address,
                "counterSymbol": counter_symbol or (
                    path_symbols[-2] if len(path_symbols) > 2 else ""
                ),
                "bridgeSymbol": bridge_symbol or (
                    path_symbols[1] if len(path_symbols) > 1 else ""
                ),
                "pathSymbols": path_symbols,
                "pathText": path_text,
                "pool": _pool_id(bridge_step) if bridge_step else "",
                "ycPool": yc_step,
                "bridgePool": bridge_step,
                "zcPool": zc_step,
                "routePools": route_rows,
                "hopCandidates": hop_candidates,
                "hopCandidateCounts": candidate_counts,
                "hopCandidateCombinationCount": 1,
                "candidatePoolCount": "3",
                "matchedPoolCount": "3",
                "anchorAddresses": [anchor_address],
                "counterTokenCount": "2",
                "recommendedFirstPriority": True,
                "routeRecommendationBasis": "fixed_fee_no_hook_low_fee",
                "executorSupported": False,
                "poolManagerBalance": None,
                "poolManagerBalanceRaw": None,
            }
        )
    return sequences


def db_closed_loop_routes(sequences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert closed-loop sequences into the DEX overview route shape."""
    routes: list[dict[str, Any]] = []
    for sequence in sequences:
        route_pools = [
            step
            for step in sequence.get("routePools") or []
            if isinstance(step, dict)
        ]
        route_boundaries = list(sequence.get("pathSymbols") or [])
        anchor = _symbol(sequence.get("anchorSymbol"))
        bridge = _symbol(sequence.get("bridgeSymbol"))
        target = _symbol(sequence.get("counterSymbol"))
        routes.append(
            {
                "kind": "bridge_closed_loop",
                "closedLoop": True,
                "borrowSymbol": anchor,
                "anchorSymbol": anchor,
                "bridgeSymbol": bridge,
                "targetSymbol": target,
                "pathSymbols": route_boundaries,
                "pathText": sequence.get("pathText") or " -> ".join(route_boundaries),
                "routePools": route_pools,
                "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
                "hopCandidates": list(sequence.get("hopCandidates") or []),
                "hopCandidateCounts": dict(sequence.get("hopCandidateCounts") or {}),
                "hopCandidateCombinationCount": 1,
                "poolPathKey": "|".join(
                    _pool_key(pool)
                    for pool in route_pools
                    if _pool_key(pool)
                ),
                "recommendedFirstPriority": True,
                "routeRecommendationBasis": "fixed_fee_no_hook_low_fee",
                "source": "v4_closed_loop_db",
            }
        )
    return routes
