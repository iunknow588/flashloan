"""Database-first builders for candidate pools, observation and closed loops.

The page-facing builders intentionally consume only rows already stored in the
business tables. They do not parse large candidate shards inside page
requests; initialisation/refresh scripts call :func:`build_all_anchor_data`.

Rules kept here:

- YC/ZC is a single shared set for V4; UI can display ``ZC/YC``.
- Fixed fee + no hook + meaningful depth are the recommendation constraints.
- Market observation must keep every ``N*(N-1)`` direction, including no-pool rows.
- One V4 pool is one closed-loop hop; reverse directions are not separate loops.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Iterable

from core.db.database_path import absolute_database_path
from core.db.storage_business_io import delete_rows, query
from core.db.storage_dex_candidate import replace_dex_candidate_tables
from core.db.storage_market_observation import (
    delete_market_observation_anchor,
    replace_market_observation_tables,
)
from core.db.storage_v4_closed_loop import replace_v4_closed_loop_tables


ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"
ANCHOR_SYMBOLS = ("ETH", "USDG", "USDE", "TZ", "WETH")
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DYNAMIC_FEE_FLAG = 8388608
MAX_FIXED_FEE = 100_000
MAX_RECOMMENDATIONS_PER_HOP = 3
NO_HOOK_ADDRESSES = {"", ZERO_ADDRESS}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def _json_loads(value: str | None) -> Any:
    if value is None:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _is_fixed_fee(pool: dict[str, Any]) -> bool:
    fee = _int(pool.get("fee"))
    return fee > 0 and fee <= MAX_FIXED_FEE and not bool(fee & DYNAMIC_FEE_FLAG)


def _has_no_hook(pool: dict[str, Any]) -> bool:
    return _text(pool.get("hooks")) in NO_HOOK_ADDRESSES


def _has_depth(pool: dict[str, Any]) -> bool:
    has_liquidity = pool.get("has_liquidity")
    if has_liquidity is not None:
        return bool(has_liquidity) or _float(pool.get("liquidity_index")) > 0
    return _float(pool.get("liquidity_index")) > 0 or _float(pool.get("estimated_tvl_usd")) > 0


def _pool_id(pool: dict[str, Any]) -> str:
    return _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))


def _validate_sorted_token_addresses(token_addresses: Iterable[str]) -> list[str]:
    addresses = sorted({_text(item) for item in token_addresses if _text(item)})
    if len(addresses) != len({item.lower() for item in addresses}):
        raise ValueError("duplicate token addresses in YC/ZC set")
    if list(addresses) != sorted(addresses):
        raise ValueError("YC/ZC token addresses are not sorted")
    return addresses


def _anchor_address_for_symbol(anchor_symbol: str, tokens: list[dict[str, Any]]) -> str:
    needle = _text(anchor_symbol).upper()
    for token in tokens:
        if _text(token.get("token_symbol") or token.get("symbol")).upper() == needle:
            return _text(token.get("token_address") or token.get("address"))
    return ""


def _resolve_anchor_address(
    anchor_symbol: str,
    tokens: list[dict[str, Any]],
    pools: list[dict[str, Any]],
) -> str:
    """Resolve an anchor address without depending on chain metadata alone.

    Priority is chain metadata, then configured manifest/env, then the pool
    table itself (a pool that touches the configured anchor symbol set).
    """
    from_meta = _anchor_address_for_symbol(anchor_symbol, tokens)
    if from_meta:
        return from_meta
    symbol = _text(anchor_symbol)
    display_symbol = symbol.upper()
    if display_symbol in {"ETH", "WETH", "USDG", "USDE", "TZ"}:
        stable_addresses: dict[str, str] = {}
        test_addresses: dict[str, str] = {}
        try:
            from dex.intent_trade.chains.robinhood.dex_pool_candidates import (
                _MAINNET_STABLE_ADDRESS_BY_SYMBOL,
                _MAINNET_TEST_ADDRESS_BY_SYMBOL,
            )

            stable_addresses = {_text(key): _text(value) for key, value in _MAINNET_STABLE_ADDRESS_BY_SYMBOL.items()}
            test_addresses = {_text(key): _text(value) for key, value in _MAINNET_TEST_ADDRESS_BY_SYMBOL.items()}
        except Exception:
            pass
        for address in {address for row in pools for address in (_text(row.get("token0")), _text(row.get("token1")))}:
            if not address:
                continue
            if display_symbol == "ETH" and address == ZERO_ADDRESS:
                return address
            if display_symbol == "WETH":
                try:
                    from dex.intent_trade.chains.robinhood.config import WETH_ADDRESS

                    if address == _text(WETH_ADDRESS):
                        return address
                except Exception:
                    pass
            if symbol in stable_addresses and address == stable_addresses[symbol]:
                return address
            if symbol in test_addresses and address == test_addresses[symbol]:
                return address
    return ""


def _read_json(path: str | Any) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _observation_token_addresses_from_files(
    *,
    runtime_root: str | Any | None = None,
    anchor_symbol: str,
) -> list[str]:
    """Read the 19-token observation set from the existing intermediate file.

    The file is used as a bootstrap source only; the database rebuild then
    owns the full N*(N-1) direction surface.
    """

    root = runtime_root if runtime_root is not None else None
    from core.runtime_paths import default_runtime_dir

    root = Path(root) if root is not None else default_runtime_dir()
    observation_root = (
        root
        / "market_observation"
        / "robinhood"
        / "4663"
        / "uniswap_v4"
        / "intermediate_pool_files"
    )
    payload = _read_json(observation_root / f"{anchor_symbol.upper()}.json")
    tokens = payload.get("ycZcTokens") or []
    addresses = [_text(item.get("address")) for item in tokens if _text(item.get("address"))]
    return _validate_sorted_token_addresses(addresses)


def _pool_depth(pool: dict[str, Any]) -> float:
    return max(
        _float(pool.get("liquidity_index")),
        _float(pool.get("estimated_tvl_usd")),
        _float(pool.get("depth_score_usd")),
    )


def _select_recommendations(pools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return up to three first/third-hop recommendations, low fee first.

    Only fixed-fee pools with meaningful depth are recommendations; pools
    without current depth remain candidate rows but must not be selected as
    the executable first/third hop.
    """

    liquid = [pool for pool in pools if _has_depth(pool)]
    first: list[dict[str, Any]] = []
    third: list[dict[str, Any]] = []
    for pool in sorted(liquid, key=lambda item: (_int(item.get("fee")), _pool_id(item))):
        if len(first) >= MAX_RECOMMENDATIONS_PER_HOP:
            break
        first.append(pool)
    for pool in sorted(liquid, key=lambda item: (_int(item.get("fee")), _pool_id(item))):
        if len(third) >= MAX_RECOMMENDATIONS_PER_HOP:
            break
        third.append(pool)
    return first, third


def _candidate_rows(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_scope: str,
    anchor_symbol: str,
    anchor_address: str,
    token_addresses: list[str],
    pools_by_edge: dict[str, list[dict[str, Any]]],
    tokens_meta: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tokens: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    pool_rows: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    for counter in token_addresses:
        if counter == anchor_address:
            continue
        edge = f"{anchor_address}:{counter}" if anchor_address <= counter else f"{counter}:{anchor_address}"
        pools = pools_by_edge.get(edge, [])
        meta = tokens_meta.get(counter, {})
        fixed = [pool for pool in pools if _is_fixed_fee(pool) and _has_no_hook(pool)]
        first, third = _select_recommendations(fixed)
        yc_pool = first[0]["pool_id"] if first else ""
        zc_pool = third[0]["pool_id"] if third else ""
        tokens.append(
            {
                "token_address": counter,
                "token_symbol": meta.get("token_symbol") or "",
                "pool_count": len(pools),
                "has_yc_optimal_pool": bool(yc_pool),
                "has_zc_optimal_pool": bool(zc_pool),
                "yc_optimal_pool_id": yc_pool,
                "zc_optimal_pool_id": zc_pool,
                "yc_zc_same_set": True,
            }
        )
        pairs.append(
            {
                "anchor_address": anchor_address,
                "counter_address": counter,
                "address_pair": edge,
                "pool_count": len(pools),
                "fixed_fee_liquid_pool_count": len(fixed),
                "priced_pool_count": sum(1 for pool in pools if _has_depth(pool)),
                "yc_optimal_pool_id": yc_pool,
                "zc_optimal_pool_id": zc_pool,
                "price_reference": {},
                "price_outlier_filter": "fixed_fee_no_hook_depth_low_fee",
            }
        )
        for pool in pools:
            pool_id = _pool_id(pool)
            is_first = pool_id in {item["pool_id"] for item in first}
            is_third = pool_id in {item["pool_id"] for item in third}
            pool_rows.append(
                {
                    "address_pair": edge,
                    "pool_id": pool_id,
                    "pool_key": _json_loads(pool.get("pool_key_json")),
                    "pool_manager": pool.get("pool_manager") or "",
                    "token0": pool.get("token0"),
                    "token1": pool.get("token1"),
                    "fee": pool.get("fee"),
                    "fee_label": pool.get("fee_label"),
                    "dynamic_fee": pool.get("dynamic_fee"),
                    "special_fee": pool.get("special_fee"),
                    "tick_spacing": pool.get("tick_spacing"),
                    "hooks": pool.get("hooks"),
                    "sqrt_price_x96": pool.get("sqrt_price_x96"),
                    "tick": pool.get("tick"),
                    "has_liquidity": pool.get("has_liquidity"),
                    "liquidity": pool.get("liquidity"),
                    "liquidity_index": pool.get("liquidity_index"),
                    "estimated_tvl_usd": pool.get("estimated_tvl_usd"),
                    "balances": _json_loads(pool.get("balances_json")),
                    "depth_status": pool.get("depth_status"),
                    "depth_band": _json_loads(pool.get("depth_band_json")),
                    "usable_for_observation": bool(fixed),
                    "is_first_hop_recommended": is_first,
                    "is_third_hop_recommended": is_third,
                    "hop_recommendation": {"first": is_first, "third": is_third},
                    "optimal_roles": {
                        "yc": yc_pool == pool_id,
                        "zc": zc_pool == pool_id,
                    },
                    "liquidity_read_at": pool.get("liquidity_read_at"),
                    "liquidity_block_number": pool.get("liquidity_block_number"),
                }
            )
        for rank, pool in enumerate(first, start=1):
            recommendations.append(
                {
                    "counter_address": counter,
                    "hop": "first",
                    "rank": rank,
                    "pool_id": _pool_id(pool),
                    "depth_score": _pool_depth(pool),
                    "price": pool.get("price"),
                    "fee": pool.get("fee"),
                    "selection_basis": "fixed_fee_no_hook_depth_low_fee_first_hop",
                }
            )
        for rank, pool in enumerate(third, start=1):
            recommendations.append(
                {
                    "counter_address": counter,
                    "hop": "third",
                    "rank": rank,
                    "pool_id": _pool_id(pool),
                    "depth_score": _pool_depth(pool),
                    "price": pool.get("price"),
                    "fee": pool.get("fee"),
                    "selection_basis": "fixed_fee_no_hook_depth_low_fee_third_hop",
                }
            )
    return {
        "tokens": tokens,
        "pairs": pairs,
        "pools": pool_rows,
        "recommendations": recommendations,
    }


def build_candidate_anchor(
    database_url: str,
    *,
    network: str = ROBINHOOD_NETWORK,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocol: str = "uniswap_v4",
    anchor_scope: str = ROBINHOOD_MARKET_ID,
    anchor_symbol: str,
    token_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Rebuild one anchor's candidate rows from the V4 exchange pool tables."""

    resolved = absolute_database_path(database_url)
    pools = query(
        str(resolved),
        """
        SELECT p.pool_id, p.pool_manager, p.pool_key_json, p.token0, p.token1,
               p.fee, p.fee_label, p.dynamic_fee, p.special_fee, p.tick_spacing,
               p.hooks, l.has_liquidity, l.liquidity, l.liquidity_index,
               l.estimated_tvl_usd, l.balances_json, l.depth_status,
               l.depth_band_json, l.depth_score_usd, l.sqrt_price_x96, l.price,
               l.tick, l.liquidity_read_at, l.liquidity_block_number
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots l
          ON l.network = p.network AND l.chain_id = p.chain_id
         AND l.protocol = p.protocol AND l.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
        ORDER BY p.pool_id
        """,
        (network, chain_id, protocol),
    )
    tokens_meta = {
        _text(row.get("token_address")): row
        for row in query(
            str(resolved),
            "SELECT token_address, token_symbol FROM chain_tokens WHERE network = ? AND chain_id = ?",
            (network, chain_id),
        )
    }
    anchor_address = _resolve_anchor_address(anchor_symbol, list(tokens_meta.values()), pools)
    if not anchor_address:
        raise ValueError(f"anchor symbol {anchor_symbol} has no chain token address")
    pools_by_edge: dict[str, list[dict[str, Any]]] = {}
    for pool in pools:
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if not token0 or not token1:
            continue
        pools_by_edge.setdefault(f"{token0}:{token1}", []).append(pool)
    if token_addresses is None:
        token_set = set()
        for edge, edge_pools in pools_by_edge.items():
            if anchor_address not in edge:
                continue
            for address in edge.split(":"):
                if address and address != anchor_address:
                    token_set.add(address)
        token_addresses = _validate_sorted_token_addresses(token_set)
    else:
        token_addresses = _validate_sorted_token_addresses(token_addresses)
        # A token is only valid when a direct anchor pool actually exists in
        # the database. Do not silently invent YC/ZC entries.
        valid = []
        for address in token_addresses:
            edge = f"{anchor_address}:{address}" if anchor_address <= address else f"{address}:{anchor_address}"
            if pools_by_edge.get(edge):
                valid.append(address)
        token_addresses = valid
    rows = _candidate_rows(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_scope=anchor_scope,
        anchor_symbol=anchor_symbol,
        anchor_address=anchor_address,
        token_addresses=token_addresses,
        pools_by_edge=pools_by_edge,
        tokens_meta=tokens_meta,
    )
    for table in (
        "dex_candidate_pool_recommendations",
        "dex_candidate_pools",
        "dex_candidate_pool_pairs",
        "dex_candidate_anchor_tokens",
    ):
        delete_rows(
            str(resolved),
            table,
            "network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?",
            (network, chain_id, protocol, anchor_symbol),
        )
    counts = replace_dex_candidate_tables(
        str(resolved),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_scope=anchor_scope,
        anchor_symbol=anchor_symbol,
        tokens=rows["tokens"],
        pairs=rows["pairs"],
        pools=rows["pools"],
        recommendations=rows["recommendations"],
    )
    return {
        "anchorSymbol": anchor_symbol,
        "anchorAddress": anchor_address,
        "tokenCount": len(rows["tokens"]),
        "pairCount": len(rows["pairs"]),
        "poolCount": len(rows["pools"]),
        "recommendationCount": len(rows["recommendations"]),
        "counts": counts,
    }


def build_market_observation_anchor(
    database_url: str,
    *,
    network: str = ROBINHOOD_NETWORK,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocol: str = "uniswap_v4",
    anchor_scope: str = ROBINHOOD_MARKET_ID,
    anchor_symbol: str,
    token_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Rebuild every Y-Z direction for one anchor, including no-pool rows."""

    resolved = absolute_database_path(database_url)
    if token_addresses is None:
        token_rows = query(
            str(resolved),
            """
            SELECT token_address FROM dex_candidate_anchor_tokens
            WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
            ORDER BY token_address
            """,
            (network, chain_id, protocol, anchor_symbol),
        )
        token_addresses = [row["token_address"] for row in token_rows]
    tokens = _validate_sorted_token_addresses(token_addresses)
    placeholders = ",".join("?" for _ in tokens)
    pools = query(
        str(resolved),
        f"""
        SELECT p.pool_id, p.pool_identity_mode, p.pool_manager, p.pool_key_json,
               p.token0, p.token1, p.fee, p.fee_label, p.dynamic_fee,
               p.special_fee, p.tick_spacing, p.hooks, l.has_liquidity,
               l.liquidity, l.liquidity_index, l.estimated_tvl_usd,
               l.balances_json, l.depth_status, l.depth_band_json,
               l.depth_score_usd, l.sqrt_price_x96, l.price, l.tick,
               l.liquidity_read_at, l.liquidity_block_number,
               l.recent_swap_active, l.observed_at, l.refreshed_at
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots l
          ON l.network = p.network AND l.chain_id = p.chain_id
         AND l.protocol = p.protocol AND l.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND (p.token0 IN ({placeholders}) OR p.token1 IN ({placeholders}))
        ORDER BY p.pool_id
        """,
        (network, chain_id, protocol, *tokens, *tokens),
    )
    token_set = set(tokens)
    pools_by_edge: dict[str, list[dict[str, Any]]] = {}
    for pool in pools:
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if token0 not in token_set or token1 not in token_set:
            continue
        pools_by_edge.setdefault(f"{token0}:{token1}", []).append(pool)
    directions: list[dict[str, Any]] = []
    pool_rows: list[dict[str, Any]] = []
    seen_pool_ids: set[str] = set()
    for token_x in tokens:
        for token_y in tokens:
            if token_x == token_y:
                continue
            edge = f"{token_x}:{token_y}" if token_x <= token_y else f"{token_y}:{token_x}"
            matched = pools_by_edge.get(edge, [])
            directions.append(
                {
                    "direction_key": f"{token_x}:{token_y}",
                    "token_x_address": token_x,
                    "token_y_address": token_y,
                    "address_pair": edge,
                    "allPools": matched,
                    "observation_empty_reason": "" if matched else "no_reachable_v4_pool",
                }
            )
            for pool in matched:
                pool_id = _pool_id(pool)
                if pool_id in seen_pool_ids:
                    continue
                seen_pool_ids.add(pool_id)
                pool_rows.append(pool)
    liquidity_rows = [
        {
            "pool_id": _pool_id(pool),
            "has_liquidity": pool.get("has_liquidity"),
            "liquidity": pool.get("liquidity"),
            "liquidity_index": pool.get("liquidity_index"),
            "estimated_tvl_usd": pool.get("estimated_tvl_usd"),
            "balances": _json_loads(pool.get("balances_json")),
            "depth_status": pool.get("depth_status"),
            "depth_band": _json_loads(pool.get("depth_band_json")),
            "sqrt_price_x96": pool.get("sqrt_price_x96"),
            "tick": pool.get("tick"),
            "liquidity_read_at": pool.get("liquidity_read_at"),
            "liquidity_block_number": pool.get("liquidity_block_number"),
            "recent_swap_active": pool.get("recent_swap_active"),
            "observed_at": pool.get("observed_at"),
            "refreshed_at": pool.get("refreshed_at"),
        }
        for pool in pool_rows
    ]
    delete_market_observation_anchor(
        str(resolved),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_symbol=anchor_symbol,
    )
    counts = replace_market_observation_tables(
        str(resolved),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_scope=anchor_scope,
        anchor_symbol=anchor_symbol,
        directions=directions,
        pools=pool_rows,
        liquidity=liquidity_rows,
    )
    return {
        "anchorSymbol": anchor_symbol,
        "tokenCount": len(tokens),
        "directionCount": len(directions),
        "noPoolDirectionCount": sum(1 for direction in directions if not direction["allPools"]),
        "poolCount": len(pool_rows),
        "counts": counts,
    }


def _build_loop_candidates(
    *,
    anchor_address: str,
    anchor_symbol: str,
    token_addresses: list[str],
    candidate_pools: dict[str, Any],
    observation_pools: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build hop-1/2/3 closed loops from recommendation and observation rows."""

    recommendations = candidate_pools.get("recommendations") or []
    first_by_counter: dict[str, list[dict[str, Any]]] = {}
    third_by_counter: dict[str, list[dict[str, Any]]] = {}
    for rec in recommendations:
        counter = _text(rec.get("counter_address"))
        hop = _text(rec.get("hop"))
        if hop == "first":
            first_by_counter.setdefault(counter, []).append(rec)
        elif hop == "third":
            third_by_counter.setdefault(counter, []).append(rec)
    pools_by_edge: dict[str, list[dict[str, Any]]] = {}
    for pool in observation_pools.get("pools") or []:
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if token0 and token1:
            pools_by_edge.setdefault(f"{token0}:{token1}", []).append(pool)
    loops: list[dict[str, Any]] = []
    hops: list[dict[str, Any]] = []
    seen_loop_keys: set[str] = set()
    for y_address in token_addresses:
        if y_address == anchor_address:
            continue
        first_options = first_by_counter.get(y_address, [])
        if not first_options:
            continue
        for z_address in token_addresses:
            if z_address in {anchor_address, y_address}:
                continue
            third_options = third_by_counter.get(z_address, [])
            if not third_options:
                continue
            yz_edge = f"{y_address}:{z_address}" if y_address <= z_address else f"{z_address}:{y_address}"
            yz_pools = pools_by_edge.get(yz_edge, [])
            if not yz_pools:
                continue
            middle = yz_pools[0]
            for first in first_options[:1]:
                for third in third_options[:1]:
                    pool_ids = (first["pool_id"], _pool_id(middle), third["pool_id"])
                    token_path = (anchor_address, y_address, z_address, anchor_address)
                    sorted_tokens = tuple(sorted((anchor_address, y_address, z_address)))
                    loop_key = "|".join((*sorted_tokens, *sorted(pool_ids)))
                    if loop_key in seen_loop_keys:
                        continue
                    seen_loop_keys.add(loop_key)
                    loop_key_short = ":".join((anchor_address, y_address, z_address))
                    loops.append(
                        {
                            "loop_key": loop_key_short,
                            "anchor_address": anchor_address,
                            "anchor_symbol": anchor_symbol,
                            "path_text": " -> ".join(token_path),
                            "path_addresses": list(token_path),
                            "path_symbols": [anchor_symbol, y_address, z_address, anchor_symbol],
                            "pool_path_key": ":".join((*token_path, *pool_ids)),
                            "pool_ids": list(pool_ids),
                            "hop_candidate_counts": {"1": len(first_options), "2": len(yz_pools), "3": len(third_options)},
                            "hop_candidate_combination_count": len(first_options) * len(yz_pools) * len(third_options),
                            "recommendation_basis": "fixed_fee_no_hook_depth_low_fee",
                            "recommended_first_priority": 1,
                            "status": "active",
                            "source": "database_builder",
                        }
                    )
                    for hop_index, (input_address, output_address, pool_id, basis) in enumerate(
                        (
                            (anchor_address, y_address, first["pool_id"], first.get("selection_basis") or "first_hop_recommended"),
                            (y_address, z_address, _pool_id(middle), "observation_middle_hop"),
                            (z_address, anchor_address, third["pool_id"], third.get("selection_basis") or "third_hop_recommended"),
                        ),
                        start=1,
                    ):
                        hops.append(
                            {
                                "loop_key": loop_key_short,
                                "hop_index": hop_index,
                                "hop_name": "yc" if hop_index == 1 else ("y-z" if hop_index == 2 else "zc"),
                                "input_address": input_address,
                                "output_address": output_address,
                                "pool_id": pool_id,
                                "base_pool_id": pool_id,
                                "pool_selection_basis": basis,
                                "temporary_override": 0,
                            }
                        )
    return loops, hops


def build_closed_loops_anchor(
    database_url: str,
    *,
    network: str = ROBINHOOD_NETWORK,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocol: str = "uniswap_v4",
    market_id: str = ROBINHOOD_MARKET_ID,
    anchor_symbol: str,
) -> dict[str, Any]:
    resolved = absolute_database_path(database_url)
    candidate_pools = query(
        str(resolved),
        """
        SELECT counter_address, hop, rank, pool_id, depth_score, price, fee,
               selection_basis
        FROM dex_candidate_pool_recommendations
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY counter_address, hop, rank
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    observation_pools = query(
        str(resolved),
        """
        SELECT pool_id, pool_identity_mode, pool_manager, pool_key_json,
               token0, token1, fee, fee_label, dynamic_fee, special_fee,
               tick_spacing, hooks, address_pair
        FROM market_observation_intermediate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    token_rows = query(
        str(resolved),
        """
        SELECT token_address FROM dex_candidate_anchor_tokens
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY token_address
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    token_addresses = _validate_sorted_token_addresses(row["token_address"] for row in token_rows)
    token_meta = query(
        str(resolved),
        "SELECT token_address, token_symbol FROM chain_tokens WHERE network = ? AND chain_id = ?",
        (network, chain_id),
    )
    anchor_address = _anchor_address_for_symbol(anchor_symbol, token_meta)
    if not anchor_address:
        # The candidate pool rows are the authority for reachability. When the
        # chain token table lacks a display symbol (for example TZ), fall back
        # to the same V4 pool-table resolution used by the candidate builder.
        anchor_pools = query(
            str(resolved),
            """
            SELECT pool_id, token0, token1, fee, fee_label, dynamic_fee,
                   special_fee, tick_spacing, hooks
            FROM v4_exchange_pools
            WHERE network = ? AND chain_id = ? AND protocol = ?
            ORDER BY pool_id
            """,
            (network, chain_id, protocol),
        )
        anchor_address = _resolve_anchor_address(anchor_symbol, token_meta, anchor_pools)
    loops, hops = _build_loop_candidates(
        anchor_address=anchor_address,
        anchor_symbol=anchor_symbol,
        token_addresses=token_addresses,
        candidate_pools={"recommendations": candidate_pools},
        observation_pools={"pools": observation_pools},
    )
    for table in (
        "v4_closed_loop_optimizations",
        "v4_closed_loop_hops",
        "v4_closed_loops",
    ):
        delete_rows(
            str(resolved),
            table,
            "network = ? AND chain_id = ? AND protocol = ? AND market_id = ?",
            (network, chain_id, protocol, market_id),
        )
    counts = replace_v4_closed_loop_tables(
        str(resolved),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loops=loops,
        hops=hops,
    )
    return {
        "anchorSymbol": anchor_symbol,
        "anchorAddress": anchor_address,
        "loopCount": len(loops),
        "hopCount": len(hops),
        "counts": counts,
    }


def build_all_anchor_data(
    database_url: str,
    *,
    network: str = ROBINHOOD_NETWORK,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocol: str = "uniswap_v4",
    anchor_scope: str = ROBINHOOD_MARKET_ID,
    market_id: str = ROBINHOOD_MARKET_ID,
    anchors: Iterable[str] = ANCHOR_SYMBOLS,
) -> dict[str, Any]:
    resolved = absolute_database_path(database_url)
    reports = []
    for anchor_symbol in anchors:
        # Candidate page owns the YC/ZC token set: only tokens with a direct
        # anchor pool are valid first/third-hop candidates. Market observation
        # must use exactly that set, otherwise direction counts and loop rows
        # diverge from the candidate surface.
        candidate = build_candidate_anchor(
            str(resolved),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            anchor_scope=anchor_scope,
            anchor_symbol=anchor_symbol,
        )
        observation_token_addresses = [
            row["token_address"]
            for row in query(
                str(resolved),
                """
                SELECT token_address FROM dex_candidate_anchor_tokens
                WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
                ORDER BY token_address
                """,
                (network, chain_id, protocol, anchor_symbol),
            )
        ]
        observation = build_market_observation_anchor(
            str(resolved),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            anchor_scope=anchor_scope,
            anchor_symbol=anchor_symbol,
            token_addresses=observation_token_addresses,
        )
        loop = build_closed_loops_anchor(
            str(resolved),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            anchor_symbol=anchor_symbol,
        )
        reports.append(
            {
                "anchorSymbol": anchor_symbol,
                "candidate": candidate,
                "observation": observation,
                "loop": loop,
            }
        )
    return {
        "ok": True,
        "network": network,
        "chainId": chain_id,
        "protocol": protocol,
        "anchors": reports,
        "generatedAt": _stamp(),
    }


def verify_anchor_integrity(
    database_url: str,
    *,
    network: str = ROBINHOOD_NETWORK,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocol: str = "uniswap_v4",
    market_id: str = ROBINHOOD_MARKET_ID,
    anchors: Iterable[str] = ANCHOR_SYMBOLS,
) -> dict[str, Any]:
    resolved = absolute_database_path(database_url)
    reports = []
    ok = True
    for anchor_symbol in anchors:
        tokens = query(
            str(resolved),
            """
            SELECT token_address FROM dex_candidate_anchor_tokens
            WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
            """,
            (network, chain_id, protocol, anchor_symbol),
        )
        token_count = len(tokens)
        directions = query(
            str(resolved),
            """
            SELECT has_direct_swap_pool, direct_swap_pool_count, observation_empty_reason
            FROM market_observation_yz_pairs
            WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
            """,
            (network, chain_id, protocol, anchor_symbol),
        )
        loops = query(
            str(resolved),
            """
            SELECT loop_key FROM v4_closed_loops
            WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
            """,
            (network, chain_id, protocol, market_id),
        )
        no_pool_directions = [row for row in directions if not row.get("has_direct_swap_pool")]
        missing_reason = [
            row["observation_empty_reason"]
            for row in no_pool_directions
            if not row.get("observation_empty_reason")
        ]
        report = {
            "anchorSymbol": anchor_symbol,
            "tokenCount": token_count,
            "expectedDirectionCount": token_count * (token_count - 1),
            "directionCount": len(directions),
            "noPoolDirectionCount": len(no_pool_directions),
            "loopCount": len(loops),
            "complete": len(directions) == token_count * (token_count - 1) and not missing_reason,
        }
        reports.append(report)
        ok = ok and report["complete"]
    return {
        "ok": ok,
        "database": str(resolved),
        "network": network,
        "chainId": chain_id,
        "protocol": protocol,
        "marketId": market_id,
        "anchors": reports,
    }


__all__ = [
    "ANCHOR_SYMBOLS",
    "ROBINHOOD_MARKET_ID",
    "build_all_anchor_data",
    "build_candidate_anchor",
    "build_closed_loops_anchor",
    "build_market_observation_anchor",
    "verify_anchor_integrity",
]
