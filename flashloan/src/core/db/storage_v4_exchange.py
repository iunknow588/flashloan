"""V4 exchange pool and liquidity snapshot storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db.storage_business_io import execute, query


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _int_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def upsert_v4_exchange_pools(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    pool_rows: list[dict[str, Any]],
) -> int:
    now = _stamp()
    rows = []
    for pool in pool_rows:
        pool_id = _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        if not pool_id or not token0 or not token1:
            continue
        fee = _int_none(pool.get("fee"))
        dynamic_fee = _bool(fee is not None and fee & 8388608)
        special_fee = _bool(fee is None or fee == 0 or dynamic_fee or (fee or 0) > 100_000)
        fee_label = "dynamic" if dynamic_fee else ("special" if special_fee else fee)
        rows.append(
            (
                network,
                chain_id,
                protocol,
                pool_id,
                pool.get("pool_identity_mode") or "v4_pool_id",
                _text(pool.get("pool_manager")),
                _json_dumps(pool.get("pool_key") or pool.get("poolKey") or {}),
                token0,
                token1,
                fee,
                fee_label,
                dynamic_fee,
                special_fee,
                _int_none(pool.get("tick_spacing") or pool.get("tickSpacing")),
                _text(pool.get("hooks")),
                f"{token0}:{token1}",
                now,
                now,
            )
        )
    if not rows:
        return 0
    return execute_many_upsert(
        database_url,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, pool_key_json, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
            pool_identity_mode = excluded.pool_identity_mode,
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
        rows,
    )


def execute_many_upsert(database_url: str, sql: str, rows: list[tuple[Any, ...]]) -> int:
    from core.db.storage_business_io import executemany

    return executemany(database_url, sql, rows)


def upsert_v4_liquidity_snapshots(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    rows: list[dict[str, Any]],
) -> int:
    now = _stamp()
    params = []
    for row in rows:
        pool_id = _text(row.get("pool_id") or row.get("poolId") or row.get("pool"))
        if not pool_id:
            continue
        params.append(
            (
                network,
                chain_id,
                protocol,
                pool_id,
                1 if bool(row.get("has_liquidity") or row.get("hasLiquidity")) else 0,
                row.get("liquidity") or "",
                _float_none(row.get("liquidity_index") or row.get("liquidityIndex")),
                _float_none(row.get("estimated_tvl_usd") or row.get("estimatedTvlUsd") or row.get("tvl_usd")),
                _json_dumps(row.get("balances") or row.get("balances_json") or {}),
                row.get("depth_status") or row.get("depthStatus") or "",
                _json_dumps(row.get("depth_band") or row.get("depthBand") or {}),
                _float_none(row.get("depth_score_usd") or row.get("depthScoreUsd")),
                row.get("sqrt_price_x96") or row.get("sqrtPriceX96") or "",
                _float_none(row.get("price")),
                _int_none(row.get("tick")),
                row.get("liquidity_read_at") or row.get("liquidityReadAt") or None,
                _int_none(row.get("liquidity_block_number") or row.get("liquidityBlockNumber")),
                1 if bool(row.get("recent_swap_active") or row.get("recentSwapActive")) else 0,
                row.get("observed_at") or now,
                row.get("refreshed_at") or now,
            )
        )
    if not params:
        return 0
    return execute_many_upsert(
        database_url,
        """
        INSERT INTO v4_exchange_pool_liquidity_snapshots (
            network, chain_id, protocol, pool_id, has_liquidity, liquidity,
            liquidity_index, estimated_tvl_usd, balances_json, depth_status,
            depth_band_json, depth_score_usd, sqrt_price_x96, price, tick,
            liquidity_read_at, liquidity_block_number, recent_swap_active,
            observed_at, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
            has_liquidity = excluded.has_liquidity,
            liquidity = excluded.liquidity,
            liquidity_index = excluded.liquidity_index,
            estimated_tvl_usd = excluded.estimated_tvl_usd,
            balances_json = excluded.balances_json,
            depth_status = excluded.depth_status,
            depth_band_json = excluded.depth_band_json,
            depth_score_usd = excluded.depth_score_usd,
            sqrt_price_x96 = excluded.sqrt_price_x96,
            price = excluded.price,
            tick = excluded.tick,
            liquidity_read_at = excluded.liquidity_read_at,
            liquidity_block_number = excluded.liquidity_block_number,
            recent_swap_active = excluded.recent_swap_active,
            observed_at = excluded.observed_at,
            refreshed_at = excluded.refreshed_at
        """,
        params,
    )


def load_v4_exchange_pools(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT network, chain_id, protocol, pool_id, pool_identity_mode,
               pool_manager, pool_key_json, token0, token1, fee, fee_label,
               dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
               created_at, updated_at
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol),
    )


def load_v4_pool_liquidity(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT network, chain_id, protocol, pool_id, has_liquidity, liquidity,
               liquidity_index, estimated_tvl_usd, balances_json, depth_status,
               depth_band_json, depth_score_usd, sqrt_price_x96, price, tick,
               liquidity_read_at, liquidity_block_number, recent_swap_active,
               observed_at, refreshed_at
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol),
    )


def find_v4_pools_between_tokens(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    token_a: str,
    token_b: str,
) -> list[dict[str, Any]]:
    first = _text(token_a)
    second = _text(token_b)
    if first == second:
        return []
    if first > second:
        first, second = second, first
    return query(
        database_url,
        """
        SELECT network, chain_id, protocol, pool_id, pool_identity_mode,
               pool_manager, pool_key_json, token0, token1, fee, fee_label,
               dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
               created_at, updated_at
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND token0 = ? AND token1 = ?
        ORDER BY fee
        """,
        (network, chain_id, protocol, first, second),
    )


__all__ = [
    "find_v4_pools_between_tokens",
    "load_v4_exchange_pools",
    "load_v4_pool_liquidity",
    "upsert_v4_exchange_pools",
    "upsert_v4_liquidity_snapshots",
]
