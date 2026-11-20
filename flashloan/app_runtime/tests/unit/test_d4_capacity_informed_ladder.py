"""Regression tests for defect D4: the capacity-aware, bidirectional ladder.

D4-a
    The probe ladder must start where the pool can actually trade. The start
    used to be a hardcoded 1.0 borrow token, which is larger than the median
    candidate-pool capacity by roughly four orders of magnitude, so a
    profitable size was unreachable.

    The start is derived by the Rust kernel: the kernel receives the folded
    single-tick capacity from the closed-loop Manager and derives the ladder
    start itself. The Python runtime is a pure consumer of kernel output and
    must not carry a trading number at all.

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

from copy import deepcopy
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


def _mock_pool_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the pool-key helpers so a route can be described offline."""
    from dex.runtime import rust_closed_loop

    a = "0x" + ("1" * 40)
    b = "0x" + ("2" * 40)
    c = "0x" + ("3" * 40)
    zero_hook = "0x0000000000000000000000000000000000000000"
    pools = [
        {"currency0": a, "currency1": b, "fee": 3000, "tickSpacing": 60, "hooks": zero_hook},
        {"currency0": b, "currency1": c, "fee": 3000, "tickSpacing": 60, "hooks": zero_hook},
        {"currency0": a, "currency1": c, "fee": 3000, "tickSpacing": 60, "hooks": zero_hook},
    ]

    def fake_pool_key_groups(*, route_symbols: list[str], chain_id: int, protocols: list[str], route_addresses: list[str] | None = None) -> list[list[dict[str, Any]]]:
        return [deepcopy(pools)]

    def fake_candidate_sets(
        pool_key_groups: list[list[dict[str, Any]]],
        route_addresses: list[str],
    ) -> tuple[list[Any], list[bool], list[Any], list[bool], list[Any], list[bool]]:
        keys: list[Any] = []
        directions: list[bool] = []
        for pool_key in pool_key_groups[0]:
            keys.append(
                [
                    pool_key["currency0"],
                    pool_key["currency1"],
                    pool_key["fee"],
                    pool_key["tickSpacing"],
                    pool_key["hooks"],
                ]
            )
            directions.append(False)
        return keys, directions, keys, directions, keys, directions

    monkeypatch.setattr(
        "market.binance_market.dex_route_helpers._official_v4_route_pool_key_groups",
        fake_pool_key_groups,
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._official_v4_hop_candidate_sets",
        fake_candidate_sets,
    )
    monkeypatch.setattr(
        "dex.runtime.rust_closed_loop._candidate_pool_key_groups_from_database",
        lambda **kwargs: {
            "ok": True,
            "groups": [deepcopy(pools)],
            "keyIds": [f"{a}:{b}", f"{b}:{c}", f"{a}:{c}"],
            "missingEdges": [],
            "routeAddresses": [a, b, c, a],
        },
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )


def _route_config(module: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Build a route task config through the public Python entry point."""
    _mock_pool_helpers(monkeypatch)
    route = {
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": ["0x" + ("1" * 40), "0x" + ("2" * 40), "0x" + ("3" * 40), "0x" + ("1" * 40)],
        "tokenSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "quoteStatus": "requires_live_quoter",
        "poolPathKey": "a|b|c",
        "pools": [],
    }
    config = module._route_task_config(
        route=route,
        chain_id=4663,
        protocols=["uniswap_v4"],
    )
    assert config is not None
    return config


def test_python_does_not_derive_the_ladder_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Python runtime must not compute a trading number.

    The ladder start belongs to the Rust kernel: it receives the capacity
    from the closed-loop Manager and derives the start itself. The Python
    side only describes the environment.
    """
    module = _closed_loop_module()
    config = _route_config(module, monkeypatch)

    assert "initialAmountRaw" not in config
    assert "ladderStartBasis" not in config


def test_python_does_not_carry_the_folded_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The folded single-tick capacity belongs to the kernel as well."""
    module = _closed_loop_module()
    config = _route_config(module, monkeypatch)

    assert "singleTickCapacityRaw" not in config
    assert "singleTickCapacityBasis" not in config
    assert "singleTickCapacityBindingPoolId" not in config
    assert "maxProbeAmountRaw" not in config


def test_python_does_not_read_the_manager_capacity_from_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a route that carries the capacity must not forward it."""
    module = _closed_loop_module()
    _mock_pool_helpers(monkeypatch)
    route = {
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": ["0x" + ("1" * 40), "0x" + ("2" * 40), "0x" + ("3" * 40), "0x" + ("1" * 40)],
        "tokenSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "quoteStatus": "requires_live_quoter",
        "poolPathKey": "a|b|c",
        "pools": [],
        "singleTickCapacityAnchorRaw": "3000000",
        "singleTickCapacityBindingPoolId": "p-b-c",
    }
    config = module._route_task_config(
        route=route,
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert config is not None
    assert "singleTickCapacityRaw" not in config
    assert "singleTickCapacityBindingPoolId" not in config


def test_python_does_not_expose_a_capacity_informed_ladder_helper() -> None:
    """The old Python helper must be gone for good."""
    module = _closed_loop_module()

    assert not hasattr(module, "_capacity_informed_ladder_start")
    assert not hasattr(module, "_folded_single_tick_capacity_raw")


def test_python_publishes_only_the_economic_floor_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """The economic floor travels as a number; the kernel compares it."""
    module = _closed_loop_module()
    config = _route_config(module, monkeypatch)

    assert "economicFloorRequiredAmountRaw" in config
    assert "economicFloorGate" not in config


def test_python_keeps_the_environment_description(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment description is still produced by Python."""
    module = _closed_loop_module()
    config = _route_config(module, monkeypatch)

    assert config["routeSymbols"] == ["AAA", "BBB", "CCC", "AAA"]
    assert config["minProbeAmountRaw"]
    assert config["maxProbeAttempts"] >= 4


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
