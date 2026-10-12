"""Database-first builders for candidate pools, observation and closed loops.

The page-facing builders intentionally consume only rows already stored in the
business tables. They do not parse large candidate shards inside page
requests; initialisation/refresh scripts call :func:`build_all_anchor_data`.

Rules kept here:

- YC/ZC is a single shared set for V4; UI can display ``ZC/YC``.
- Candidate YC/ZC and market observation both use fixed fee <= 3000 plus no
  hook.  Depth/price are dynamic fields refreshed separately and are not
  static membership filters; DEX overview applies liquidity later.
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
from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_dex_candidate import (
    MAX_USABLE_CANDIDATE_FIXED_FEE,
    replace_dex_candidate_abnormal_tables,
    replace_dex_candidate_tables,
)
from core.db.storage_market_observation import (
    delete_market_observation_anchor,
    replace_market_observation_abnormal_tables,
    replace_market_observation_tables,
)
from core.db.storage_v4_closed_loop import replace_v4_closed_loop_tables
from dex.intent_trade.chains.robinhood_common.chain_config import (
    MAINNET_ANCHOR_ADDRESS_BY_SYMBOL,
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_MARKET_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
    TESTNET_ANCHOR_ADDRESS_BY_SYMBOL,
    TESTNET_ANCHOR_SYMBOLS,
    configured_anchor_address_by_symbol as _shared_anchor_address_map,
)


ANCHOR_SYMBOLS = tuple(MAINNET_ANCHOR_ADDRESS_BY_SYMBOL)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ROBINHOOD_TOKEN_ALLOWLIST_PATH = (
    Path(__file__).resolve().parents[2]
    / "chains"
    / "robinhood"
    / "v4_token_allowlist.json"
)
ROBINHOOD_TESTNET_TOKEN_ALLOWLIST_PATH = (
    Path(__file__).resolve().parents[2]
    / "chains"
    / "robinhood-testnet"
    / "v4_token_allowlist.json"
)
DYNAMIC_FEE_FLAG = 8388608
MAX_FIXED_FEE = 100_000
MAX_RECOMMENDATIONS_PER_HOP = 3
MAX_CANDIDATE_POOLS_PER_PAIR = 6
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


def _is_fixed_fee(pool: dict[str, Any], *, max_fixed_fee: int = MAX_FIXED_FEE) -> bool:
    fee = _int(pool.get("fee"))
    return fee > 0 and fee <= max_fixed_fee and not bool(fee & DYNAMIC_FEE_FLAG)


def _has_no_hook(pool: dict[str, Any]) -> bool:
    return _text(pool.get("hooks")) in NO_HOOK_ADDRESSES


def _has_depth(pool: dict[str, Any]) -> bool:
    has_liquidity = pool.get("has_liquidity")
    if has_liquidity is not None:
        return bool(has_liquidity) or _float(pool.get("liquidity_index")) > 0
    return _float(pool.get("liquidity_index")) > 0 or _float(pool.get("estimated_tvl_usd")) > 0


def _fixed_fee_no_hook_sql(*, max_fixed_fee: int = MAX_FIXED_FEE) -> str:
    """SQL predicate shared by candidate and observation builders.

    Callers can tighten ``max_fixed_fee`` for the candidate surface.  Depth
    and price are dynamic fields refreshed separately, so the static YC/ZC
    surface is only constrained by fixed fee and no hook.
    """
    return (
        f"p.fee > 0 AND p.fee <= {int(max_fixed_fee)} AND (p.fee & 8388608) = 0 "
        "AND (COALESCE(p.hooks, '') = '' OR lower(p.hooks) = '0x0000000000000000000000000000000000000000')"
    )


def _seed_chain_token_symbols(
    database_url: str,
    *,
    network: str,
    chain_id: int,
) -> None:
    """Refresh display symbols from the chain token allowlist.

    The allowlist is chain configuration metadata, not a page data shard.  It
    keeps address-sorted YC/ZC rows readable on pages that read SQLite only.
    """
    allowlist_path = (
        ROBINHOOD_TESTNET_TOKEN_ALLOWLIST_PATH
        if _text(network) == ROBINHOOD_TESTNET_NETWORK
        else ROBINHOOD_TOKEN_ALLOWLIST_PATH
    )
    anchor_symbols = TESTNET_ANCHOR_SYMBOLS if _text(network) == ROBINHOOD_TESTNET_NETWORK else ANCHOR_SYMBOLS
    stable_symbols = (
        {"USDG", "USDE", "TZ"}
        if _text(network) == ROBINHOOD_TESTNET_NETWORK
        else {"USDG", "USDE", "TZ"}
    )
    try:
        payload = json.loads(allowlist_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if not isinstance(tokens, dict):
        return
    rows = []
    for item in tokens.values():
        if not isinstance(item, dict):
            continue
        address = _text(item.get("address"))
        symbol = str(item.get("symbol") or "").strip().upper()
        if not address or not symbol:
            continue
        category = str(item.get("category") or "").strip().lower()
        rows.append(
            {
                "token_address": address,
                "token_symbol": symbol,
                "token_name": symbol,
                "token_decimals": item.get("decimals"),
                "token_kind": "native"
                if category == "native"
                else "wrapped_native"
                if category == "wrapped_native"
                else "erc20",
                "is_native": 1 if category == "native" else 0,
                "is_wrapped_native": 1 if category == "wrapped_native" else 0,
                "is_stable": 1 if symbol in stable_symbols else 0,
                "anchor_symbol": symbol if symbol in anchor_symbols else None,
                "token_metadata": {"category": category, "source": item.get("source")},
                "source": {
                    "seed": (
                        "robinhood_testnet_v4_token_allowlist"
                        if _text(network) == ROBINHOOD_TESTNET_NETWORK
                        else "robinhood_v4_token_allowlist"
                    )
                },
            }
        )
    if rows:
        seed_chain_tokens(
            database_url,
            network=network,
            chain_id=chain_id,
            token_rows=rows,
        )
    existing_addresses = {
        _text(row.get("token_address"))
        for row in query(
            str(database_url),
            "SELECT token_address FROM chain_tokens WHERE network = ? AND chain_id = ?",
            (network, chain_id),
        )
    }
    missing_symbols = {
        _text(item.get("address")): {
            "symbol": str(item.get("symbol") or "").strip().upper(),
            "category": str(item.get("category") or "").strip().lower(),
        }
        for item in (tokens or {}).values()
        if isinstance(item, dict)
        and _text(item.get("address"))
        and str(item.get("symbol") or "").strip()
        and _text(item.get("address")) not in existing_addresses
    }
    if missing_symbols:
        seed_chain_tokens(
            database_url,
            network=network,
            chain_id=chain_id,
            token_rows=[
                {
                    "token_address": address,
                    "token_symbol": meta["symbol"],
                    "token_name": meta["symbol"],
                    "token_decimals": None,
                    "token_kind": "erc20",
                    "is_native": 0,
                    "is_wrapped_native": 0,
                    "is_stable": 0,
                    "anchor_symbol": symbol if symbol in anchor_symbols else None,
                    "token_metadata": {
                        "category": meta.get("category") or "test_tokens",
                        "source": (
                            "robinhood_testnet_v4_token_allowlist"
                            if _text(network) == ROBINHOOD_TESTNET_NETWORK
                            else "robinhood_v4_token_allowlist"
                        ),
                    },
                    "source": {
                        "seed": (
                            "robinhood_testnet_v4_token_allowlist"
                            if _text(network) == ROBINHOOD_TESTNET_NETWORK
                            else "robinhood_v4_token_allowlist"
                        )
                    },
                }
                for address, meta in sorted(missing_symbols.items())
            ],
        )


def _token_symbol_lookup(database_url: str, *, network: str, chain_id: int) -> dict[str, str]:
    rows = query(
        str(database_url),
        "SELECT token_address, token_symbol FROM chain_tokens WHERE network = ? AND chain_id = ?",
        (network, chain_id),
    )
    return {
        _text(row.get("token_address")): str(row.get("token_symbol") or "").strip().upper()
        for row in rows
        if _text(row.get("token_address")) and str(row.get("token_symbol") or "").strip()
    }


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
    *,
    network: str = ROBINHOOD_NETWORK,
) -> str:
    """Return the exact configured anchor address for a symbol.

    Anchors are fixed on-chain facts for each network; the pool table and
    chain token metadata are never used to reverse-infer an anchor address.
    """
    return _configured_anchor_address_by_symbol(network).get(_text(anchor_symbol).upper(), "")


def _configured_anchor_address_by_symbol(network: str) -> dict[str, str]:
    return _shared_anchor_address_map(network)


def _market_id_for_network(network: str) -> str:
    if _text(network) == ROBINHOOD_TESTNET_NETWORK:
        return ROBINHOOD_TESTNET_MARKET_ID
    return ROBINHOOD_MARKET_ID


def _pool_depth(pool: dict[str, Any]) -> float:
    return max(
        _float(pool.get("liquidity_index")),
        _float(pool.get("estimated_tvl_usd")),
        _float(pool.get("depth_score_usd")),
    )


def _pool_reference_price(pool: dict[str, Any]) -> float | None:
    try:
        price = float(pool.get("price"))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _representative_pool_rows(
    pools: list[dict[str, Any]],
    *,
    limit: int = MAX_CANDIDATE_POOLS_PER_PAIR,
) -> list[dict[str, Any]]:
    """Keep at most ``limit`` representative pools per token pair.

    Selection prefers the deepest pool, then the highest/lowest reference
    price when present, then the remaining slots by depth and low fee.  The
    same rule is applied to abnormal pools so their page surface stays
    bounded without inventing new token pairs.
    """
    if limit <= 0 or len(pools) <= limit:
        return list(pools)
    ordered = sorted(
        pools,
        key=lambda row: (
            -_pool_depth(row),
            _int(row.get("fee")),
            _pool_id(row),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def _add(row: dict[str, Any]) -> None:
        pool_id = _pool_id(row)
        if pool_id:
            if pool_id in selected_ids:
                return
            selected_ids.add(pool_id)
        selected.append(row)

    if ordered:
        _add(ordered[0])
    priced = [row for row in ordered if _pool_reference_price(row) is not None]
    if len(priced) >= 2:
        _add(max(priced, key=lambda row: _pool_reference_price(row) or 0.0))
        _add(min(priced, key=lambda row: _pool_reference_price(row) or 0.0))
    for row in ordered:
        if len(selected) >= limit:
            break
        _add(row)
    return selected[:limit]


def _select_recommendations(pools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return up to three first/third-hop recommendations, low fee first.

    Depth and price are refreshed dynamically, so the static recommendation
    only requires a fixed-fee no-hook pool.
    """

    liquid = list(pools)
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
        fixed = [
            pool
            for pool in pools
            if _is_fixed_fee(pool, max_fixed_fee=MAX_USABLE_CANDIDATE_FIXED_FEE) and _has_no_hook(pool)
        ]
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
                "price_outlier_filter": "fixed_fee_no_hook_low_fee",
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
                    "selection_basis": "fixed_fee_no_hook_low_fee_first_hop",
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
                    "selection_basis": "fixed_fee_no_hook_low_fee_third_hop",
                }
            )
    return {
        "tokens": tokens,
        "pairs": pairs,
        "pools": pool_rows,
        "recommendations": recommendations,
    }


def _candidate_abnormal_rows(
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
    """Build candidate rows for pools outside the normal fixed-fee/no-hook set."""
    tokens: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    pool_rows: list[dict[str, Any]] = []
    for counter in token_addresses:
        if counter == anchor_address:
            continue
        edge = f"{anchor_address}:{counter}" if anchor_address <= counter else f"{counter}:{anchor_address}"
        pools = pools_by_edge.get(edge, [])
        if not pools:
            continue
        meta = tokens_meta.get(counter, {})
        tokens.append(
            {
                "token_role": "yc_zc_abnormal",
                "token_address": counter,
                "token_symbol": meta.get("token_symbol") or "",
                "pool_count": len(pools),
                "has_yc_optimal_pool": False,
                "has_zc_optimal_pool": False,
                "yc_optimal_pool_id": "",
                "zc_optimal_pool_id": "",
                "yc_zc_same_set": True,
            }
        )
        pairs.append(
            {
                "anchor_address": anchor_address,
                "counter_address": counter,
                "address_pair": edge,
                "pool_count": len(pools),
                "fixed_fee_liquid_pool_count": 0,
                "priced_pool_count": sum(1 for pool in pools if _has_depth(pool)),
                "yc_optimal_pool_id": "",
                "zc_optimal_pool_id": "",
                "price_reference": {},
                "price_outlier_filter": "abnormal_pool",
            }
        )
        for pool in pools:
            pool_id = _pool_id(pool)
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
                    "usable_for_observation": False,
                    "is_first_hop_recommended": False,
                    "is_third_hop_recommended": False,
                    "hop_recommendation": {"first": False, "third": False},
                    "optimal_roles": {"yc": False, "zc": False},
                    "liquidity_read_at": pool.get("liquidity_read_at"),
                    "liquidity_block_number": pool.get("liquidity_block_number"),
                }
            )
    return {"tokens": tokens, "pairs": pairs, "pools": pool_rows}


def build_candidate_anchor(
    database_url: str,
    *,
    network: str = ROBINHOOD_NETWORK,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocol: str = "uniswap_v4",
    anchor_scope: str | None = None,
    anchor_symbol: str,
    token_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Rebuild one anchor's candidate rows from the V4 exchange pool tables."""

    resolved = absolute_database_path(database_url)
    anchor_scope = anchor_scope or _market_id_for_network(network)
    pools = query(
        str(resolved),
        f"""
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
    anchor_address = _resolve_anchor_address(
        anchor_symbol,
        list(tokens_meta.values()),
        pools,
        network=network,
    )
    if not anchor_address:
        raise ValueError(f"anchor symbol {anchor_symbol} has no chain token address")
    normal_pools = [
        pool
        for pool in pools
        if _is_fixed_fee(pool, max_fixed_fee=MAX_USABLE_CANDIDATE_FIXED_FEE) and _has_no_hook(pool)
    ]
    abnormal_pools = [
        pool
        for pool in pools
        if not (_is_fixed_fee(pool, max_fixed_fee=MAX_USABLE_CANDIDATE_FIXED_FEE) and _has_no_hook(pool))
    ]
    pools_by_edge: dict[str, list[dict[str, Any]]] = {}
    for pool in normal_pools:
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if token0 and token1:
            pools_by_edge.setdefault(f"{token0}:{token1}", []).append(pool)
    abnormal_pools_by_edge: dict[str, list[dict[str, Any]]] = {}
    for pool in abnormal_pools:
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if token0 and token1:
            abnormal_pools_by_edge.setdefault(f"{token0}:{token1}", []).append(pool)
    pools_by_edge = {
        edge: _representative_pool_rows(edge_pools)
        for edge, edge_pools in pools_by_edge.items()
    }
    abnormal_pools_by_edge = {
        edge: _representative_pool_rows(edge_pools)
        for edge, edge_pools in abnormal_pools_by_edge.items()
    }
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
    abnormal_token_addresses = _validate_sorted_token_addresses(
        {
            address
            for edge, edge_pools in abnormal_pools_by_edge.items()
            if anchor_address in edge
            for address in edge.split(":")
            if address and address != anchor_address
        }
    )
    abnormal_rows = _candidate_abnormal_rows(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_scope=anchor_scope,
        anchor_symbol=anchor_symbol,
        anchor_address=anchor_address,
        token_addresses=abnormal_token_addresses,
        pools_by_edge=abnormal_pools_by_edge,
        tokens_meta=tokens_meta,
    )
    for table in (
        "dex_candidate_pool_recommendations",
        "dex_candidate_pools",
        "dex_candidate_pool_pairs",
        "dex_candidate_anchor_tokens",
        "dex_candidate_abnormal_pools",
        "dex_candidate_abnormal_pool_pairs",
        "dex_candidate_abnormal_anchor_tokens",
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
    abnormal_counts = replace_dex_candidate_abnormal_tables(
        str(resolved),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_scope=anchor_scope,
        anchor_symbol=anchor_symbol,
        tokens=abnormal_rows["tokens"],
        pairs=abnormal_rows["pairs"],
        pools=abnormal_rows["pools"],
    )
    return {
        "anchorSymbol": anchor_symbol,
        "anchorAddress": anchor_address,
        "tokenCount": len(rows["tokens"]),
        "pairCount": len(rows["pairs"]),
        "poolCount": len(rows["pools"]),
        "recommendationCount": len(rows["recommendations"]),
        "counts": counts,
        "abnormalTokenCount": len(abnormal_rows["tokens"]),
        "abnormalPairCount": len(abnormal_rows["pairs"]),
        "abnormalPoolCount": len(abnormal_rows["pools"]),
        "abnormalCounts": abnormal_counts,
    }


def build_market_observation_anchor(
    database_url: str,
    *,
    network: str = ROBINHOOD_NETWORK,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocol: str = "uniswap_v4",
    anchor_scope: str | None = None,
    anchor_symbol: str,
    token_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Rebuild every Y-Z direction for one anchor, including no-pool rows."""

    resolved = absolute_database_path(database_url)
    anchor_scope = anchor_scope or _market_id_for_network(network)
    _seed_chain_token_symbols(
        str(resolved),
        network=network,
        chain_id=chain_id,
    )
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
    if not tokens:
        delete_market_observation_anchor(
            str(resolved),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            anchor_symbol=anchor_symbol,
        )
        return {
            "anchorSymbol": anchor_symbol,
            "tokenCount": 0,
            "directionCount": 0,
            "noPoolDirectionCount": 0,
            "poolCount": 0,
            "counts": {},
            "abnormalDirectionCount": 0,
            "abnormalPoolCount": 0,
            "abnormalCounts": {},
        }
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
        if _is_fixed_fee(pool, max_fixed_fee=MAX_USABLE_CANDIDATE_FIXED_FEE) and _has_no_hook(pool):
            pools_by_edge.setdefault(f"{token0}:{token1}", []).append(pool)
    abnormal_pools_by_edge: dict[str, list[dict[str, Any]]] = {}
    for pool in pools:
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if token0 in token_set and token1 in token_set:
            if not (_is_fixed_fee(pool, max_fixed_fee=MAX_USABLE_CANDIDATE_FIXED_FEE) and _has_no_hook(pool)):
                abnormal_pools_by_edge.setdefault(f"{token0}:{token1}", []).append(pool)
    directions: list[dict[str, Any]] = []
    abnormal_directions: list[dict[str, Any]] = []
    pool_rows: list[dict[str, Any]] = []
    abnormal_pool_rows: list[dict[str, Any]] = []
    seen_pool_ids: set[str] = set()
    seen_abnormal_pool_ids: set[str] = set()
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
            abnormal_matched = abnormal_pools_by_edge.get(edge, [])
            if abnormal_matched:
                abnormal_directions.append(
                    {
                        "direction_key": f"{token_x}:{token_y}",
                        "token_x_address": token_x,
                        "token_y_address": token_y,
                        "address_pair": edge,
                        "allPools": abnormal_matched,
                        "observation_empty_reason": "",
                    }
                )
            for pool in abnormal_matched:
                pool_id = _pool_id(pool)
                if pool_id in seen_abnormal_pool_ids:
                    continue
                seen_abnormal_pool_ids.add(pool_id)
                abnormal_pool_rows.append(pool)
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
        for pool in [*pool_rows, *abnormal_pool_rows]
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
    abnormal_counts = replace_market_observation_abnormal_tables(
        str(resolved),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_scope=anchor_scope,
        anchor_symbol=anchor_symbol,
        directions=abnormal_directions,
        pools=abnormal_pool_rows,
    )
    return {
        "anchorSymbol": anchor_symbol,
        "tokenCount": len(tokens),
        "directionCount": len(directions),
        "noPoolDirectionCount": sum(1 for direction in directions if not direction["allPools"]),
        "poolCount": len(pool_rows),
        "counts": counts,
        "abnormalDirectionCount": len(abnormal_directions),
        "abnormalPoolCount": len(abnormal_pool_rows),
        "abnormalCounts": abnormal_counts,
    }


def _build_loop_candidates(
    *,
    anchor_address: str,
    anchor_symbol: str,
    token_addresses: list[str],
    candidate_pools: dict[str, Any],
    observation_pools: dict[str, Any],
    token_symbols: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build hop-1/2/3 closed loops from recommendation and observation rows."""

    recommendations = candidate_pools.get("recommendations") or []
    token_symbols = token_symbols or {}
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
    symbol_for = lambda address: token_symbols.get(_text(address), _text(address))
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
                    path_symbols = [
                        anchor_symbol,
                        symbol_for(y_address),
                        symbol_for(z_address),
                        anchor_symbol,
                    ]
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
                            "path_text": " -> ".join(path_symbols),
                            "path_addresses": list(token_path),
                            "path_symbols": path_symbols,
                            "pool_path_key": ":".join((*token_path, *pool_ids)),
                            "pool_ids": list(pool_ids),
                            "hop_candidate_counts": {"1": len(first_options), "2": len(yz_pools), "3": len(third_options)},
                            "hop_candidate_combination_count": len(first_options) * len(yz_pools) * len(third_options),
                            "recommendation_basis": "fixed_fee_no_hook_low_fee",
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
    market_id: str | None = None,
    anchor_symbol: str,
) -> dict[str, Any]:
    resolved = absolute_database_path(database_url)
    market_id = market_id or _market_id_for_network(network)
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
    if not token_addresses:
        delete_rows(
            str(resolved),
            "v4_closed_loop_optimizations",
            "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
            "AND loop_key IN (SELECT loop_key FROM v4_closed_loops "
            "WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ? AND anchor_symbol = ?)",
            (network, chain_id, protocol, market_id, network, chain_id, protocol, market_id, anchor_symbol),
        )
        delete_rows(
            str(resolved),
            "v4_closed_loop_hops",
            "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
            "AND loop_key IN (SELECT loop_key FROM v4_closed_loops "
            "WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ? AND anchor_symbol = ?)",
            (network, chain_id, protocol, market_id, network, chain_id, protocol, market_id, anchor_symbol),
        )
        delete_rows(
            str(resolved),
            "v4_closed_loops",
            "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? AND anchor_symbol = ?",
            (network, chain_id, protocol, market_id, anchor_symbol),
        )
        return {
            "anchorSymbol": anchor_symbol,
            "anchorAddress": "",
            "loopCount": 0,
            "hopCount": 0,
            "counts": {},
        }
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
        anchor_address = _resolve_anchor_address(
            anchor_symbol,
            token_meta,
            anchor_pools,
            network=network,
        )
    loops, hops = _build_loop_candidates(
        anchor_address=anchor_address,
        anchor_symbol=anchor_symbol,
        token_addresses=token_addresses,
        candidate_pools={"recommendations": candidate_pools},
        observation_pools={"pools": observation_pools},
        token_symbols=_token_symbol_lookup(
            str(resolved),
            network=network,
            chain_id=chain_id,
        ),
    )
    delete_rows(
        str(resolved),
        "v4_closed_loop_optimizations",
        "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
        "AND loop_key IN (SELECT loop_key FROM v4_closed_loops "
        "WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ? AND anchor_symbol = ?)",
        (network, chain_id, protocol, market_id, network, chain_id, protocol, market_id, anchor_symbol),
    )
    delete_rows(
        str(resolved),
        "v4_closed_loop_hops",
        "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
        "AND loop_key IN (SELECT loop_key FROM v4_closed_loops "
        "WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ? AND anchor_symbol = ?)",
        (network, chain_id, protocol, market_id, network, chain_id, protocol, market_id, anchor_symbol),
    )
    delete_rows(
        str(resolved),
        "v4_closed_loops",
        "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? AND anchor_symbol = ?",
        (network, chain_id, protocol, market_id, anchor_symbol),
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
    anchor_scope: str | None = None,
    market_id: str | None = None,
    anchors: Iterable[str] = ANCHOR_SYMBOLS,
    token_universe: Iterable[str] | None = None,
) -> dict[str, Any]:
    resolved = absolute_database_path(database_url)
    anchor_scope = anchor_scope or _market_id_for_network(network)
    market_id = market_id or _market_id_for_network(network)
    _seed_chain_token_symbols(
        str(resolved),
        network=network,
        chain_id=chain_id,
    )
    anchor_symbols = tuple(
        str(item or "").strip().upper()
        for item in anchors
        if str(item or "").strip()
    )
    universe = sorted({_text(item) for item in token_universe}) if token_universe is not None else None
    # The full rebuild owns every business row for this network/chain/protocol.
    # Older interrupted imports may have written rows under the mainnet market
    # id/anchor scope; deleting by network+chain+protocol removes that residue
    # before the per-anchor builders run.
    for table in (
        "dex_candidate_anchor_tokens",
        "dex_candidate_pool_pairs",
        "dex_candidate_pools",
        "dex_candidate_pool_recommendations",
        "dex_candidate_abnormal_anchor_tokens",
        "dex_candidate_abnormal_pool_pairs",
        "dex_candidate_abnormal_pools",
        "market_observation_yz_pairs",
        "market_observation_abnormal_yz_pairs",
        "market_observation_intermediate_pools",
        "market_observation_abnormal_intermediate_pools",
        "market_observation_liquidity_snapshots",
        "v4_closed_loop_optimizations",
        "v4_closed_loop_hops",
        "v4_closed_loops",
    ):
        delete_rows(
            str(resolved),
            table,
            "network = ? AND chain_id = ? AND protocol = ?",
            (network, chain_id, protocol),
        )
    if anchor_symbols:
        placeholders = ",".join("?" for _ in anchor_symbols)
        for table in (
            "dex_candidate_anchor_tokens",
            "dex_candidate_pool_pairs",
            "dex_candidate_pools",
            "dex_candidate_pool_recommendations",
            "dex_candidate_abnormal_anchor_tokens",
            "dex_candidate_abnormal_pool_pairs",
            "dex_candidate_abnormal_pools",
            "market_observation_yz_pairs",
            "market_observation_abnormal_yz_pairs",
            "market_observation_intermediate_pools",
            "market_observation_abnormal_intermediate_pools",
            "market_observation_liquidity_snapshots",
        ):
            delete_rows(
                str(resolved),
                table,
                "network = ? AND chain_id = ? AND protocol = ? "
                f"AND anchor_symbol NOT IN ({placeholders})",
                (network, chain_id, protocol, *anchor_symbols),
            )
        delete_rows(
            str(resolved),
            "v4_closed_loop_optimizations",
            "network = ? AND chain_id = ? AND protocol = ? AND market_id = ?",
            (network, chain_id, protocol, market_id),
        )
        delete_rows(
            str(resolved),
            "v4_closed_loop_hops",
            "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
            "AND loop_key IN (SELECT loop_key FROM v4_closed_loops "
            "WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
            f"AND anchor_symbol NOT IN ({placeholders}))",
            (network, chain_id, protocol, market_id, network, chain_id, protocol, market_id, *anchor_symbols),
        )
        delete_rows(
            str(resolved),
            "v4_closed_loops",
            "network = ? AND chain_id = ? AND protocol = ? AND market_id = ? "
            f"AND anchor_symbol NOT IN ({placeholders})",
            (network, chain_id, protocol, market_id, *anchor_symbols),
        )
    # A full rebuild owns every loop row for this network/chain/protocol.
    # Older interrupted imports may have written rows under a different
    # market_id (for example the mainnet market id), which would otherwise
    # survive the per-anchor delete below and duplicate the loop key.
    for table in (
        "v4_closed_loop_optimizations",
        "v4_closed_loop_hops",
        "v4_closed_loops",
    ):
        delete_rows(
            str(resolved),
            table,
            "network = ? AND chain_id = ? AND protocol = ?",
            (network, chain_id, protocol),
        )
    reports = []
    for anchor_symbol in anchor_symbols:
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
            token_addresses=universe,
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
