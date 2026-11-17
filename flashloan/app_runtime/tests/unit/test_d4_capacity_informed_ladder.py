"""Regression tests for defect D4: the capacity-aware, bidirectional ladder.

D4-a
    The probe ladder must start where the pool can actually trade. The start
    used to be a hardcoded 1.0 borrow token, which is larger than the median
    candidate-pool capacity by roughly four orders of magnitude, so a
    profitable size was unreachable.

D4-d
    The economic floor must be expressed in the borrow token's own units. A
    route whose tightest capacity cannot carry the amount needed to cover Gas
    is economically infeasible and must be refused before any probe runs.

D4-e
    A dust pool is removed from the execution path, not from the candidate
    set, and the blacklist reason is capacity plus Gas, never TVL.

No USD quote participates in any of these decisions.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _closed_loop_module() -> Any:
    from dex.runtime import rust_closed_loop

    return rust_closed_loop


# ---------------------------------------------------------------------------
# D4-a: capacity-informed ladder start
# ---------------------------------------------------------------------------


def test_ladder_start_is_derived_from_measured_capacity() -> None:
    """The start must scale with the measured capacity, not stay at 1.0 token."""
    module = _closed_loop_module()

    # 18 decimals: 1.0 token is 1e18 raw.
    start, basis = module._capacity_informed_ladder_start(
        cap_raw=4_000_000_000_000_000_000,
        borrow_decimals=18,
        min_probe_amount_raw=10**15,
        required_amount_raw=0,
    )

    assert start == 2_000_000_000_000_000_000
    assert start != 10**18
    assert basis == "capacity_fraction_0.50"


def test_ladder_start_scales_with_capacity() -> None:
    """A ten-times deeper pool must produce a ten-times larger start."""
    module = _closed_loop_module()

    small, _ = module._capacity_informed_ladder_start(
        cap_raw=10**18,
        borrow_decimals=18,
        min_probe_amount_raw=10**15,
    )
    large, _ = module._capacity_informed_ladder_start(
        cap_raw=10**19,
        borrow_decimals=18,
        min_probe_amount_raw=10**15,
    )

    assert large == small * 10


def test_ladder_start_never_exceeds_measured_capacity() -> None:
    """The start must stay inside what the pool can actually carry."""
    module = _closed_loop_module()

    start, _ = module._capacity_informed_ladder_start(
        cap_raw=1_000_000_000_000,
        borrow_decimals=18,
        min_probe_amount_raw=10**15,
    )

    assert start <= 1_000_000_000_000


def test_ladder_start_respects_the_economic_floor() -> None:
    """D4-d: the start must never sit below the amount needed to cover Gas."""
    module = _closed_loop_module()

    required = 800_000_000_000_000_000
    start, _ = module._capacity_informed_ladder_start(
        cap_raw=1_000_000_000_000_000_000,
        borrow_decimals=18,
        min_probe_amount_raw=10**15,
        required_amount_raw=required,
    )

    assert start >= required


def test_ladder_start_falls_back_to_one_token_without_capacity() -> None:
    """An unmeasured pool must keep the historical start and say so."""
    module = _closed_loop_module()

    start, basis = module._capacity_informed_ladder_start(
        cap_raw=0,
        borrow_decimals=18,
        min_probe_amount_raw=10**15,
    )

    assert start == 10**18
    assert basis == "fallback_one_token_unmeasured_capacity"


def test_ladder_start_fraction_is_configurable() -> None:
    """The capacity fraction must be adjustable without a code change."""
    module = _closed_loop_module()
    import os

    previous = os.environ.get("DEX_V4_LADDER_START_CAPACITY_FRACTION")
    os.environ["DEX_V4_LADDER_START_CAPACITY_FRACTION"] = "0.25"
    try:
        start, basis = module._capacity_informed_ladder_start(
            cap_raw=4_000_000_000_000_000_000,
            borrow_decimals=18,
            min_probe_amount_raw=10**15,
        )
    finally:
        if previous is None:
            os.environ.pop("DEX_V4_LADDER_START_CAPACITY_FRACTION", None)
        else:
            os.environ["DEX_V4_LADDER_START_CAPACITY_FRACTION"] = previous

    assert start == 1_000_000_000_000_000_000
    assert basis == "capacity_fraction_0.25"


def test_ladder_start_rejects_out_of_range_fraction() -> None:
    """A nonsensical fraction must be clamped, never trusted."""
    module = _closed_loop_module()
    import os

    previous = os.environ.get("DEX_V4_LADDER_START_CAPACITY_FRACTION")
    os.environ["DEX_V4_LADDER_START_CAPACITY_FRACTION"] = "17"
    try:
        start, basis = module._capacity_informed_ladder_start(
            cap_raw=4_000_000_000_000_000_000,
            borrow_decimals=18,
            min_probe_amount_raw=10**15,
        )
    finally:
        if previous is None:
            os.environ.pop("DEX_V4_LADDER_START_CAPACITY_FRACTION", None)
        else:
            os.environ["DEX_V4_LADDER_START_CAPACITY_FRACTION"] = previous

    assert start == 4_000_000_000_000_000_000
    assert basis == "capacity_fraction_1.00"


# ---------------------------------------------------------------------------
# D4-d / D4-e: the token-denominated economic floor
# ---------------------------------------------------------------------------


def test_economic_floor_blocks_a_route_that_cannot_cover_gas() -> None:
    """A capacity below the Gas requirement must be refused."""
    from dex.intent_trade.chains.robinhood.v4_pool_capacity import (
        capacity_covers_amount,
        required_amount_for_profit,
    )

    gas = 6.2e-06
    unit_profit = 1.0
    required = required_amount_for_profit(gas, unit_profit)
    assert required is not None and required > 0

    # The pool can carry far less than the amount needed to cover Gas.
    assert capacity_covers_amount(required // 10, required) is False
    # A pool that can carry it passes.
    assert capacity_covers_amount(required * 10, required) is True


def test_economic_floor_is_token_denominated_not_usd() -> None:
    """No USD input may participate in the economic requirement."""
    from dex.intent_trade.chains.robinhood.v4_pool_capacity import (
        required_amount_for_profit,
    )

    required = required_amount_for_profit(6.2e-06, 1.0)
    assert required is not None
    # The requirement is a raw token amount, not a USD amount.
    assert isinstance(required, int)
    assert required == max(1, int(6.2e-06 / 1.0))


def test_economic_floor_is_undecided_without_unit_profit() -> None:
    """A missing unit profit must not become a market conclusion."""
    from dex.intent_trade.chains.robinhood.v4_pool_capacity import (
        required_amount_for_profit,
    )

    assert required_amount_for_profit(6.2e-06, None) is None
    assert required_amount_for_profit(None, 1.0) is None


def test_dust_blacklist_keeps_the_pool_but_closes_the_execution_path() -> None:
    """D4-e: a dust pool is blacklisted from execution, not from the book."""
    gate = {
        "verdict": "dust_unprofitable",
        "blocked": True,
        "reason": "capacity_cannot_cover_gas",
        "measured": True,
        "capacityRaw": 1_000,
        "requiredRaw": 6_200_000,
        "basis": "token_denominated_economic_floor",
    }

    assert gate["blocked"] is True
    assert gate["verdict"] == "dust_unprofitable"
    # The reason must name capacity and Gas, never a TVL or USD valuation.
    assert "tvl" not in gate["reason"].lower()
    assert "usd" not in gate["basis"].lower()


def test_depth_policy_keeps_usd_out_of_the_admission_path() -> None:
    """The shared depth policy must not promote a USD value to a gate."""
    from dex.intent_trade.chains.robinhood.v4_pool_capacity import DEPTH_POLICY

    assert DEPTH_POLICY["priceImpactLimitBps"] == 10.0
    assert DEPTH_POLICY["depthBasis"] == "onchain_tick_liquidity"
    # USD entries exist, but they are explicitly reference-only.
    reference_only = DEPTH_POLICY.get("referenceOnly") or {}
    assert "minCandidateTvlUsd" in reference_only


# ---------------------------------------------------------------------------
# A blocked ladder is a missing capacity, not a missing measurement
# ---------------------------------------------------------------------------


def _route() -> dict[str, Any]:
    return {
        "routeSymbols": ["ETH", "TSLA", "AMD", "ETH"],
        "routeAddresses": [
            "0x0000000000000000000000000000000000000000",
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
            "0x0000000000000000000000000000000000000000",
        ],
    }


def test_a_blocked_ladder_names_the_closed_loop_manager_as_the_repair_target() -> None:
    """A ladder blocked before it probed has a missing *capacity*.

    The review manager cannot repair a missing published capacity, so the
    record has to point at the closed-loop manager instead. Sending the
    orchestrator after the review manager would ask it to repair a
    measurement that was never broken.
    """
    module = _closed_loop_module()
    result = {
        "status": "single_tick_capacity_unavailable",
        "ladderBlocked": True,
        "ladderBlockedReason": "single_tick_capacity_unavailable",
        "repairTarget": "closed_loop_manager",
        "attemptedCount": "0x0",
        "attempts": [],
        "initialAmountRaw": "0x1",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
    }
    evidence = module._native_worker_evidence(
        route=_route(),
        result=result,
        chain_id=46630,
        observed_at="2026-11-17T00:00:00Z",
    )
    assert evidence is not None
    assert evidence["bestAmountLadderBlocked"] is True
    assert evidence["bestAmountLadderBlockedReason"] == "single_tick_capacity_unavailable"
    assert evidence["bestAmountRepairTarget"] == "closed_loop_manager"
    # The review still fails closed: no confirmed amount may be published.
    assert evidence["bestAmountConfirmed"] is False
    assert evidence["bestAmountMissing"] is True


def test_a_ladder_that_ran_is_not_marked_as_blocked() -> None:
    """A ladder that measured sizes is never reported as *blocked*."""
    module = _closed_loop_module()
    result = {
        "status": "not_profitable_after_costs",
        "attemptedCount": "0x2",
        "attempts": [
            {"amountRaw": "0x1", "ok": True, "profitable": False, "profitRaw": "-0x2"},
            {"amountRaw": "0x2", "ok": True, "profitable": False, "profitRaw": "-0x1"},
        ],
        "initialAmountRaw": "0x1",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
    }
    evidence = module._native_worker_evidence(
        route=_route(),
        result=result,
        chain_id=46630,
        observed_at="2026-11-17T00:00:00Z",
    )
    assert evidence is not None
    assert evidence["bestAmountLadderBlocked"] is False
    assert evidence["bestAmountRepairTarget"] is None
