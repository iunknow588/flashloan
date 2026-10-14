"""Market observation Y-Z pairs, intermediate pools and liquidity snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db.storage_business_io import execute, executemany, query
from core.db.direction_pool_score import has_confirmed_liquidity, score_pool_by_direction


MAX_OBSERVATION_POOLS_PER_DIRECTION = 3
MIN_OBSERVATION_POOL_DEPTH_USD = 1000.0


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _bool_tri(value: Any) -> int | None:
    """Return 1/0 for explicit evidence and None when the row has no signal."""
    if value is None:
        return None
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes"} else 0
    return 1 if bool(value) else 0


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _int_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def replace_market_observation_tables(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_scope: str,
    anchor_symbol: str,
    directions: list[dict[str, Any]],
    pools: list[dict[str, Any]],
    liquidity: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    now = _stamp()
    pair_rows = []
    for direction in directions:
        token_x = _text(direction.get("token_x_address") or direction.get("tokenX"))
        token_y = _text(direction.get("token_y_address") or direction.get("tokenY"))
        direction_key = _text(direction.get("direction_key") or direction.get("directionKey"))
        if not token_x or not token_y:
            continue
        if not direction_key:
            direction_key = f"{token_x}:{token_y}"
        address_pair = f"{token_x}:{token_y}"
        all_pools = direction.get("allPools") or direction.get("all_pools") or []
        scored_pools = sorted(
            (
                item
                for item in all_pools
                if item
                and (
                    item.get("poolId")
                    or item.get("pool_id")
                    or item.get("pool")
                )
            ),
            key=lambda item: (
                -score_pool_by_direction(item, token_x, token_y),
                _text(item.get("poolId") or item.get("pool_id") or item.get("pool")),
            ),
        )
        pool_ids = []
        seen_pool_ids: set[str] = set()
        for item in scored_pools:
            if not has_confirmed_liquidity(
                item,
                min_usd=MIN_OBSERVATION_POOL_DEPTH_USD,
                fallback_token_base=1.0,
            ):
                continue
            pool_id = _text(item.get("poolId") or item.get("pool_id") or item.get("pool"))
            if pool_id and pool_id not in seen_pool_ids:
                seen_pool_ids.add(pool_id)
                pool_ids.append(pool_id)
                if len(pool_ids) >= MAX_OBSERVATION_POOLS_PER_DIRECTION:
                    break
        optimal_pool_id = pool_ids[0] if pool_ids else ""
        empty_reason = (
            direction.get("observation_empty_reason")
            or direction.get("observationEmptyReason")
            or ""
        )
        if not pool_ids and not empty_reason:
            empty_reason = (
                "no_confirmed_liquidity"
                if all_pools
                else "no_reachable_v4_pool"
            )
        pair_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                direction_key, token_x, token_y, address_pair,
                _bool(bool(pool_ids)),
                len(pool_ids),
                _json_dumps(pool_ids),
                optimal_pool_id,
                empty_reason,
                now, now,
            )
        )
    pool_rows = []
    for pool in pools:
        pool_id = _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        if not pool_id or not token0 or not token1:
            continue
        fee = _int_none(pool.get("fee"))
        dynamic_fee = bool(fee is not None and fee & 8388608)
        special_fee = bool(fee is None or fee == 0 or dynamic_fee or (fee or 0) > 100_000)
        fee_label = "dynamic" if dynamic_fee else ("special" if special_fee else fee)
        pool_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id, pool.get("pool_identity_mode") or "v4_pool_id",
                _text(pool.get("pool_manager")),
                _json_dumps(pool.get("pool_key") or pool.get("poolKey") or {}),
                token0, token1, fee, fee_label,
                _bool(dynamic_fee), _bool(special_fee),
                _int_none(pool.get("tick_spacing") or pool.get("tickSpacing")),
                _text(pool.get("hooks")),
                f"{token0}:{token1}",
                now, now,
            )
        )
    liquidity_rows = []
    for row in liquidity or []:
        pool_id = _text(row.get("pool_id") or row.get("poolId") or row.get("pool"))
        if not pool_id:
            continue
        liquidity_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id,
                _bool_tri(
                    row.get("has_liquidity")
                    if row.get("has_liquidity") is not None
                    else row.get("hasLiquidity")
                ),
                row.get("liquidity") or "",
                _float_none(row.get("liquidity_index") or row.get("liquidityIndex")),
                _float_none(row.get("estimated_tvl_usd") or row.get("estimatedTvlUsd")),
                _json_dumps(row.get("balances") or row.get("balances_json") or {}),
                row.get("depth_status") or row.get("depthStatus") or "",
                _json_dumps(row.get("depth_band") or row.get("depthBand") or {}),
                row.get("sqrt_price_x96") or row.get("sqrtPriceX96") or "",
                _int_none(row.get("tick")),
                row.get("liquidity_read_at") or row.get("liquidityReadAt"),
                _int_none(row.get("liquidity_block_number") or row.get("liquidityBlockNumber")),
                _bool_tri(
                    row.get("recent_swap_active")
                    if row.get("recent_swap_active") is not None
                    else row.get("recentSwapActive")
                ),
                row.get("observed_at") or now,
                row.get("refreshed_at") or now,
            )
        )
    if pair_rows:
        executemany(
            database_url,
            """
            INSERT INTO market_observation_yz_pairs (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                direction_key, token_x_address, token_y_address, address_pair,
                has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
                optimal_pool_id, observation_empty_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
            DO UPDATE SET token_x_address = excluded.token_x_address,
                token_y_address = excluded.token_y_address,
                address_pair = excluded.address_pair,
                has_direct_swap_pool = excluded.has_direct_swap_pool,
                direct_swap_pool_count = excluded.direct_swap_pool_count,
                pool_ids_json = excluded.pool_ids_json,
                optimal_pool_id = excluded.optimal_pool_id,
                observation_empty_reason = excluded.observation_empty_reason,
                updated_at = excluded.updated_at
            """,
            pair_rows,
        )
    if pool_rows:
        executemany(
            database_url,
            """
            INSERT INTO market_observation_intermediate_pools (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id, pool_identity_mode, pool_manager, pool_key_json,
                token0, token1, fee, fee_label, dynamic_fee, special_fee,
                tick_spacing, hooks, address_pair, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            DO UPDATE SET pool_identity_mode = excluded.pool_identity_mode,
                pool_manager = excluded.pool_manager,
                pool_key_json = excluded.pool_key_json,
                token0 = excluded.token0,
                token1 = excluded.token1,
                fee = excluded.fee,
                fee_label = excluded.fee_label,
                dynamic_fee = excluded.dynamic_fee,
                special_fee = excluded.special_fee,
                tick_spacing = excluded.tick_spacing,
                hooks = excluded.hooks,
                address_pair = excluded.address_pair,
                updated_at = excluded.updated_at
            """,
            pool_rows,
        )
    if liquidity_rows:
        executemany(
            database_url,
            """
            INSERT INTO market_observation_liquidity_snapshots (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id, has_liquidity, liquidity, liquidity_index,
                estimated_tvl_usd, balances_json, depth_status, depth_band_json,
                sqrt_price_x96, tick, liquidity_read_at, liquidity_block_number,
                recent_swap_active, observed_at, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            DO UPDATE SET has_liquidity = CASE
                WHEN excluded.has_liquidity IS NULL
                THEN market_observation_liquidity_snapshots.has_liquidity
                ELSE excluded.has_liquidity
            END,
                liquidity = CASE
                    WHEN excluded.liquidity IS NULL OR excluded.liquidity = ''
                    THEN market_observation_liquidity_snapshots.liquidity
                    ELSE excluded.liquidity
                END,
                liquidity_index = COALESCE(
                    excluded.liquidity_index,
                    market_observation_liquidity_snapshots.liquidity_index
                ),
                estimated_tvl_usd = COALESCE(
                    excluded.estimated_tvl_usd,
                    market_observation_liquidity_snapshots.estimated_tvl_usd
                ),
                balances_json = COALESCE(
                    excluded.balances_json,
                    market_observation_liquidity_snapshots.balances_json
                ),
                depth_status = CASE
                    WHEN excluded.depth_status IS NULL OR excluded.depth_status = ''
                    THEN market_observation_liquidity_snapshots.depth_status
                    ELSE excluded.depth_status
                END,
                depth_band_json = COALESCE(
                    excluded.depth_band_json,
                    market_observation_liquidity_snapshots.depth_band_json
                ),
                sqrt_price_x96 = CASE
                    WHEN excluded.sqrt_price_x96 IS NULL OR excluded.sqrt_price_x96 = ''
                    THEN market_observation_liquidity_snapshots.sqrt_price_x96
                    ELSE excluded.sqrt_price_x96
                END,
                tick = COALESCE(excluded.tick, market_observation_liquidity_snapshots.tick),
                liquidity_read_at = COALESCE(
                    excluded.liquidity_read_at,
                    market_observation_liquidity_snapshots.liquidity_read_at
                ),
                liquidity_block_number = COALESCE(
                    excluded.liquidity_block_number,
                    market_observation_liquidity_snapshots.liquidity_block_number
                ),
                recent_swap_active = CASE
                    WHEN excluded.recent_swap_active IS NULL
                    THEN market_observation_liquidity_snapshots.recent_swap_active
                    ELSE excluded.recent_swap_active
                END,
                observed_at = COALESCE(
                    excluded.observed_at,
                    market_observation_liquidity_snapshots.observed_at
                ),
                refreshed_at = excluded.refreshed_at
            """,
            liquidity_rows,
        )
    return {"pairs": len(pair_rows), "pools": len(pool_rows), "liquidity": len(liquidity_rows)}


def replace_market_observation_abnormal_tables(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_scope: str,
    anchor_symbol: str,
    directions: list[dict[str, Any]],
    pools: list[dict[str, Any]],
) -> dict[str, int]:
    """Write non-normal market-observation Y-Z pools and direction rows.

    These rows are informational only.  Closed-loop construction and DEX
    overview must keep consuming the normal ``market_observation_yz_pairs`` and
    ``market_observation_intermediate_pools`` tables.
    """
    now = _stamp()
    pair_rows = []
    for direction in directions:
        token_x = _text(direction.get("token_x_address") or direction.get("tokenX"))
        token_y = _text(direction.get("token_y_address") or direction.get("tokenY"))
        direction_key = _text(direction.get("direction_key") or direction.get("directionKey"))
        if not token_x or not token_y:
            continue
        if not direction_key:
            direction_key = f"{token_x}:{token_y}"
        address_pair = f"{token_x}:{token_y}"
        all_pools = direction.get("allPools") or direction.get("all_pools") or []
        scored_pools = sorted(
            (
                item
                for item in all_pools
                if item
                and (
                    item.get("poolId")
                    or item.get("pool_id")
                    or item.get("pool")
                )
            ),
            key=lambda item: (
                -score_pool_by_direction(item, token_x, token_y),
                _text(item.get("poolId") or item.get("pool_id") or item.get("pool")),
            ),
        )
        pool_ids = []
        seen_pool_ids: set[str] = set()
        for item in scored_pools:
            if not has_confirmed_liquidity(
                item,
                min_usd=MIN_OBSERVATION_POOL_DEPTH_USD,
                fallback_token_base=1.0,
            ):
                continue
            pool_id = _text(item.get("poolId") or item.get("pool_id") or item.get("pool"))
            if pool_id and pool_id not in seen_pool_ids:
                seen_pool_ids.add(pool_id)
                pool_ids.append(pool_id)
                if len(pool_ids) >= MAX_OBSERVATION_POOLS_PER_DIRECTION:
                    break
        optimal_pool_id = pool_ids[0] if pool_ids else ""
        empty_reason = (
            direction.get("observation_empty_reason")
            or direction.get("observationEmptyReason")
            or ""
        )
        if not pool_ids and not empty_reason:
            empty_reason = (
                "no_confirmed_liquidity"
                if all_pools
                else "no_reachable_v4_pool"
            )
        pair_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                direction_key, token_x, token_y, address_pair,
                _bool(bool(pool_ids)),
                len(pool_ids),
                _json_dumps(pool_ids),
                optimal_pool_id,
                empty_reason,
                now, now,
            )
        )
    pool_rows = []
    for pool in pools:
        pool_id = _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        if not pool_id or not token0 or not token1:
            continue
        fee = _int_none(pool.get("fee"))
        dynamic_fee = bool(fee is not None and fee & 8388608)
        special_fee = bool(fee is None or fee == 0 or dynamic_fee or (fee or 0) > 100_000)
        fee_label = "dynamic" if dynamic_fee else ("special" if special_fee else fee)
        pool_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id, pool.get("pool_identity_mode") or "v4_pool_id",
                _text(pool.get("pool_manager")),
                _json_dumps(pool.get("pool_key") or pool.get("poolKey") or {}),
                token0, token1, fee, fee_label,
                _bool(dynamic_fee), _bool(special_fee),
                _int_none(pool.get("tick_spacing") or pool.get("tickSpacing")),
                _text(pool.get("hooks")),
                pool.get("address_pair") or pool.get("addressPair") or f"{token0}:{token1}",
                now, now,
            )
        )
    if pair_rows:
        executemany(
            database_url,
            """
            INSERT INTO market_observation_abnormal_yz_pairs (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                direction_key, token_x_address, token_y_address, address_pair,
                has_non_normal_swap_pool, non_normal_swap_pool_count, pool_ids_json,
                optimal_pool_id, observation_empty_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
            DO UPDATE SET token_x_address = excluded.token_x_address,
                token_y_address = excluded.token_y_address,
                address_pair = excluded.address_pair,
                has_non_normal_swap_pool = excluded.has_non_normal_swap_pool,
                non_normal_swap_pool_count = excluded.non_normal_swap_pool_count,
                pool_ids_json = excluded.pool_ids_json,
                optimal_pool_id = excluded.optimal_pool_id,
                observation_empty_reason = excluded.observation_empty_reason,
                updated_at = excluded.updated_at
            """,
            pair_rows,
        )
    if pool_rows:
        executemany(
            database_url,
            """
            INSERT INTO market_observation_abnormal_intermediate_pools (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id, pool_identity_mode, pool_manager, pool_key_json,
                token0, token1, fee, fee_label, dynamic_fee, special_fee,
                tick_spacing, hooks, address_pair, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            DO UPDATE SET pool_identity_mode = excluded.pool_identity_mode,
                pool_manager = excluded.pool_manager,
                pool_key_json = excluded.pool_key_json,
                token0 = excluded.token0,
                token1 = excluded.token1,
                fee = excluded.fee,
                fee_label = excluded.fee_label,
                dynamic_fee = excluded.dynamic_fee,
                special_fee = excluded.special_fee,
                tick_spacing = excluded.tick_spacing,
                hooks = excluded.hooks,
                address_pair = excluded.address_pair,
                updated_at = excluded.updated_at
            """,
            pool_rows,
        )
    return {"pairs": len(pair_rows), "pools": len(pool_rows)}


def load_market_observation(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_symbol: str,
) -> dict[str, Any]:
    pairs = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               direction_key, token_x_address, token_y_address, address_pair,
               has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
               optimal_pool_id, observation_empty_reason, created_at, updated_at
        FROM market_observation_yz_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY direction_key
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    pools = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               pool_id, pool_identity_mode, pool_manager, pool_key_json,
               token0, token1, fee, fee_label, dynamic_fee, special_fee,
               tick_spacing, hooks, address_pair, created_at, updated_at
        FROM market_observation_intermediate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    liquidity = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               pool_id, has_liquidity, liquidity, liquidity_index,
               estimated_tvl_usd, balances_json, depth_status, depth_band_json,
               sqrt_price_x96, tick, liquidity_read_at, liquidity_block_number,
               recent_swap_active, observed_at, refreshed_at
        FROM market_observation_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    abnormal_pairs = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               direction_key, token_x_address, token_y_address, address_pair,
               has_non_normal_swap_pool, non_normal_swap_pool_count, pool_ids_json,
               optimal_pool_id, observation_empty_reason, created_at, updated_at
        FROM market_observation_abnormal_yz_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY direction_key
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    abnormal_pools = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               pool_id, pool_identity_mode, pool_manager, pool_key_json,
               token0, token1, fee, fee_label, dynamic_fee, special_fee,
               tick_spacing, hooks, address_pair, created_at, updated_at
        FROM market_observation_abnormal_intermediate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    return {
        "pairs": pairs,
        "pools": pools,
        "liquidity": liquidity,
        "abnormalPairs": abnormal_pairs,
        "abnormalPools": abnormal_pools,
    }


def delete_market_observation_anchor(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_symbol: str,
) -> None:
    for table in (
        "market_observation_yz_pairs",
        "market_observation_intermediate_pools",
        "market_observation_abnormal_yz_pairs",
        "market_observation_abnormal_intermediate_pools",
        "market_observation_liquidity_snapshots",
    ):
        execute(
            database_url,
            f"DELETE FROM {table} WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?",
            (network, chain_id, protocol, anchor_symbol),
        )


__all__ = [
    "delete_market_observation_anchor",
    "load_market_observation",
    "replace_market_observation_abnormal_tables",
    "replace_market_observation_tables",
]
