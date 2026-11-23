from __future__ import annotations

from core.db import builders
from core.db.direction_pool_score import (
    DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS,
    MAX_DIRECTION_CANDIDATE_POOLS_ENV,
    MAX_DIRECTION_CANDIDATE_POOLS,
    resolve_max_direction_candidate_pools,
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
        # The Rust kernel admitted this pool.
        "kernel_admitted": True,
        "kernel_admission_reason": "admitted",
        "kernel_depth_band": "deep",
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
    # The kernel rejected the shallow pool.
    shallow["kernel_admitted"] = False
    shallow["kernel_admission_reason"] = "binding_capacity_below_floor"
    deep = [_pool("p2", 100.0), _pool("p3", 101.0)]

    assert select_directional_pool_ids([shallow, *deep], "0xa", "0xb") == ["p2", "p3"]


def test_the_capacity_knob_comes_from_the_environment(monkeypatch):
    """The candidate count is an operator knob, not a hardcoded constant."""
    monkeypatch.setenv(MAX_DIRECTION_CANDIDATE_POOLS_ENV, "3")
    assert resolve_max_direction_candidate_pools() == 3
    # The module attributes that carry the knob follow the environment.
    assert builders.MAX_CANDIDATE_POOLS_PER_PAIR == 3
    assert builders.MAX_RECOMMENDATIONS_PER_HOP == 3
    # The selection itself honours the knob.
    pools = [_pool(f"p{i}", float(100 + i)) for i in range(1, 8)]
    assert len(select_directional_pool_ids(pools, "0xa", "0xb")) == 3


def test_a_malformed_capacity_falls_back_to_the_default(monkeypatch):
    """A capacity knob must never be able to stop the scanner."""
    for raw in ("not-a-number", "", "0", "-4"):
        monkeypatch.setenv(MAX_DIRECTION_CANDIDATE_POOLS_ENV, raw)
        assert resolve_max_direction_candidate_pools() == DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS


def test_the_unset_capacity_is_five(monkeypatch):
    monkeypatch.delenv(MAX_DIRECTION_CANDIDATE_POOLS_ENV, raising=False)
    assert resolve_max_direction_candidate_pools() == 5
    assert DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS == 5
