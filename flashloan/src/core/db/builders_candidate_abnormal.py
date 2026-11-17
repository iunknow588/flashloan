"""Candidate rows for pools outside the normal fixed-fee/no-hook set.

The candidate builder splits the observed pool universe into two parts:

- the **normal** set, whose pools carry a fixed fee at or below the shared
  ceiling and no hook, and which therefore takes part in the market
  observation and in the closed-loop construction; and
- the **abnormal** set, every other observed pool, which is recorded for
  traceability but is never used as a route member.

This module owns the abnormal half. It is a separate module because the
candidate builder is the largest module in the database layer and the
architecture guard keeps every helper module under its own line limit.

The module only **builds rows**. It performs no database access and it owns no
policy: the fee ceiling, the hook rule and the depth rule are decided by the
caller and applied by the shared pool helpers.
"""

from __future__ import annotations

from typing import Any

from core.db.builders_pool_helpers import (
    _has_depth,
    _json_loads,
    _pool_id,
    _pools_for_token_pair,
    _text,
)


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
        pools = _pools_for_token_pair(pools_by_edge, anchor_address, counter)
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
        for direction_key, direction_anchor, direction_counter in (
            (f"{anchor_address}:{counter}", anchor_address, counter),
            (f"{counter}:{anchor_address}", counter, anchor_address),
        ):
            pairs.append(
                {
                    "anchor_address": direction_anchor,
                    "counter_address": direction_counter,
                    "address_pair": direction_key,
                    "direction_key": direction_key,
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
            if not pool_id:
                continue
            token0 = _text(pool.get("token0"))
            token1 = _text(pool.get("token1"))
            canonical_pair = f"{token0}:{token1}" if token0 and token1 else f"{anchor_address}:{counter}"
            pool_rows.append(
                {
                    "address_pair": canonical_pair,
                    "direction_key": canonical_pair,
                    "direction_address_pair": canonical_pair,
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
