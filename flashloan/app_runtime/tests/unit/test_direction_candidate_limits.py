from __future__ import annotations

from core.db.direction_pool_score import (
    MAX_DIRECTION_CANDIDATE_POOLS,
    select_directional_pool_ids,
)


def _pool(pool_id: str, price: float, fee: int = 3000) -> dict:
    return {
        "pool_id": pool_id,
        "poolId": pool_id,
        "token0": "0xa",
        "token1": "0xb",
        "fee": fee,
        "liquidity_index": 10_000.0,
        "estimated_tvl_usd": 10_000.0,
        "liquidity": 10_000,
        "sqrt_price_x96": int((price ** 0.5) * (1 << 96)),
    }


def test_select_directional_pool_ids_keeps_five_per_direction():
    pools = [_pool(f"p{i}", float(100 + i)) for i in range(1, 8)]
    forward = select_directional_pool_ids(pools, "0xa", "0xb")
    reverse = select_directional_pool_ids(pools, "0xb", "0xa")

    assert len(forward) == MAX_DIRECTION_CANDIDATE_POOLS == 5
    assert len(reverse) == MAX_DIRECTION_CANDIDATE_POOLS == 5
    # Higher token1-per-token0 price means fewer B per A but more A per B.
    assert forward[0] == "p1"
    assert reverse[0] == "p7"
    # The same physical pools are valid for both directions, but direction
    # reverses the price order and can therefore keep a different top five:
    # the best A -> B pool is the worst B -> A pool.
    shared = [pool_id for pool_id in forward if pool_id in reverse]
    assert shared == ["p3", "p4", "p5"]
    assert [pool_id for pool_id in reverse if pool_id in shared] == ["p5", "p4", "p3"]


def test_directional_order_requires_depth_for_membership():
    shallow = _pool("p1", 100.0)
    shallow["liquidity_index"] = 0
    shallow["estimated_tvl_usd"] = 0
    deep = [_pool("p2", 100.0), _pool("p3", 101.0)]

    assert select_directional_pool_ids([shallow, *deep], "0xa", "0xb") == ["p2", "p3"]
