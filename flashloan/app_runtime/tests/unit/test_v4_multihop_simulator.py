"""Tests for the off-chain three-hop closed-loop simulator (ARCH-2).

The simulator is the **parity target** for the "blind send" idea: dropping
the chain-side preview is only safe when the off-chain computation
reproduces the chain computation exactly. These tests lock down that claim:

* the flash-loan fee and repayment match the on-chain `_fee` /
  `_requiredRepay` in `RobinhoodPausedFlashLoanExecutor.sol`;
* a single hop reproduces the canonical Uniswap V4 swap output, i.e. the
  same integer arithmetic as the Rust `fallback_swap_amount_out` in
  `pool_manager/src/depth.rs`;
* the settlement reproduces the chain's profit definition
  (`balance - requiredRepay`, floored at zero);
* a route that does not close, or whose token chain breaks, is rejected
  instead of being reported as a profit;
* nothing in the simulator reads a USD price, a TVL figure, or the network.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from dex.intent_trade.chains.robinhood import v4_pool_capacity
from dex.intent_trade.chains.robinhood.v4_multihop_simulator import (
    HOP_COUNT,
    HopState,
    flash_fee,
    required_repay,
    route_min_capacity_raw,
    simulate_hop,
    simulate_route,
)


Q96 = 1 << 96
FEE_DENOMINATOR = 10_000


def _strip_comments_and_docstrings(source: str) -> str:
    """Return the executable part of a module, without prose.

    The architectural guard must look at the code, not at the docstrings: the
    module documents *why* it has no USD dependency, and that sentence must
    not be mistaken for a USD dependency.
    """
    import io
    import tokenize

    kept: list[str] = []
    previous_token_type = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous_token_type in (
            tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, tokenize.DEDENT
        ):
            # A bare string statement is a docstring.
            continue
        if token.type not in (tokenize.NL, tokenize.NEWLINE):
            previous_token_type = token.type
        kept.append(token.string)
    return "\n".join(kept)

TOKEN_A = "0x" + "aa" * 20
TOKEN_B = "0x" + "bb" * 20
TOKEN_C = "0x" + "cc" * 20


def _hop(pool_id: str, token_in: str, token_out: str, *,
         sqrt_price_x96: int = Q96, liquidity: int = 10 ** 20,
         fee_pips: int = 3000, zero_for_one: bool = True,
         token0_decimals: int = 18, token1_decimals: int = 18) -> HopState:
    return HopState(
        pool_id=pool_id,
        token_in=token_in,
        token_out=token_out,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=liquidity,
        fee_pips=fee_pips,
        zero_for_one=zero_for_one,
        token0_decimals=token0_decimals,
        token1_decimals=token1_decimals,
    )


def _closed_loop(**kwargs) -> list[HopState]:
    """Build a three-hop A -> B -> C -> A loop."""
    return [
        _hop("pool-a", TOKEN_A, TOKEN_B, **kwargs),
        _hop("pool-b", TOKEN_B, TOKEN_C, **kwargs),
        _hop("pool-c", TOKEN_C, TOKEN_A, **kwargs),
    ]


# ---------------------------------------------------------------------------
# Flash-loan fee / repayment parity with the on-chain contract
# ---------------------------------------------------------------------------

def test_flash_fee_matches_the_contract_rounding():
    """The contract computes `(amount * feeBps + 9999) / 10000`.

    The simulator must round **up** exactly like the contract. Rounding down
    would make the simulator optimistic and could report a profitable route
    that the chain rejects.
    """
    vectors = [
        (0, 5, 0),
        (1, 1, 1),
        (9_999, 1, 1),
        (10_000, 1, 1),
        (1_000_000, 5, 500),
        (10 ** 18, 5, 5 * 10 ** 14),
        (10 ** 18, 0, 0),
    ]
    for amount, fee_bps, expected in vectors:
        assert flash_fee(amount, fee_bps) == expected, (amount, fee_bps)
        # Cross-check against the contract's literal expression.
        contract_fee = 0 if fee_bps == 0 else (amount * fee_bps + FEE_DENOMINATOR - 1) // FEE_DENOMINATOR
        assert flash_fee(amount, fee_bps) == contract_fee


def test_flash_fee_never_rounds_down():
    """Every fee is the smallest integer not below the exact value."""
    for amount in (1, 7, 123_456, 10 ** 18):
        for fee_bps in (1, 3, 5, 30, 250):
            exact = amount * fee_bps
            quotient, remainder = divmod(exact, FEE_DENOMINATOR)
            assert flash_fee(amount, fee_bps) == quotient + (1 if remainder else 0)


def test_required_repay_is_amount_plus_fee():
    """The contract's `_requiredRepay` is `amount + _fee(amount, feeBps)`."""
    for amount in (0, 1, 10 ** 6, 10 ** 18):
        for fee_bps in (0, 5, 9):
            assert required_repay(amount, fee_bps) == amount + flash_fee(amount, fee_bps)


# ---------------------------------------------------------------------------
# Single-hop swap parity with the canonical Uniswap maths
# ---------------------------------------------------------------------------

def test_simulate_hop_reproduces_the_canonical_swap_output():
    """The hop output must equal `v4_pool_capacity.swap_amount_out`."""
    for zero_for_one in (True, False):
        for amount in (10 ** 15, 10 ** 16, 10 ** 17):
            state = _hop("pool-x", TOKEN_A, TOKEN_B, zero_for_one=zero_for_one)
            result = simulate_hop(state, amount)
            assert result.ok
            expected = v4_pool_capacity.swap_amount_out(
                state.sqrt_price_x96, state.liquidity, state.fee_pips, amount, zero_for_one
            )
            assert result.amount_out_raw == expected


def test_simulate_hop_reproduces_the_rust_fallback_arithmetic():
    """The hop output must equal the Rust `fallback_swap_amount_out` maths.

    The Rust fallback in `pool_manager/src/depth.rs` uses the canonical
    Uniswap rounding: up for the amount0 / sqrt-price step, down for the
    amount1 step, and down for both deltas. This test re-implements that
    arithmetic independently, so a drift in either implementation is caught
    instead of being hidden by a shared helper.
    """
    def rust_next_sqrt_amount0(sqrt_price_x96: int, liquidity: int, amount: int) -> int:
        numerator1 = liquidity << 96
        product = amount * sqrt_price_x96
        denominator = numerator1 + product
        if denominator <= 0 or denominator < numerator1:
            return sqrt_price_x96
        quotient = (numerator1 * sqrt_price_x96 + denominator - 1) // denominator
        return quotient if quotient < sqrt_price_x96 else sqrt_price_x96

    def rust_next_sqrt_amount1(sqrt_price_x96: int, liquidity: int, amount: int) -> int:
        return sqrt_price_x96 + ((amount << 96) // liquidity)

    def rust_amount1_delta(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
        low, high = (sqrt_a, sqrt_b) if sqrt_a <= sqrt_b else (sqrt_b, sqrt_a)
        return (liquidity * (high - low)) // Q96

    def rust_amount0_delta(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
        low, high = (sqrt_a, sqrt_b) if sqrt_a <= sqrt_b else (sqrt_b, sqrt_a)
        if low <= 0:
            return 0
        numerator1 = liquidity << 96
        return ((numerator1 * (high - low)) // high) // low

    def rust_swap_amount_out(sqrt_price_x96: int, liquidity: int, fee_pips: int,
                             amount_in: int, zero_for_one: bool) -> int:
        fee_complement = 1_000_000 - fee_pips
        if fee_complement == 0:
            return 0
        amount_in_less_fee = (amount_in * fee_complement) // 1_000_000
        if amount_in_less_fee <= 0:
            return 0
        if zero_for_one:
            sqrt_next = rust_next_sqrt_amount0(sqrt_price_x96, liquidity, amount_in_less_fee)
            return rust_amount1_delta(sqrt_next, sqrt_price_x96, liquidity)
        sqrt_next = rust_next_sqrt_amount1(sqrt_price_x96, liquidity, amount_in_less_fee)
        return rust_amount0_delta(sqrt_next, sqrt_price_x96, liquidity)

    vectors = [
        (Q96, 10 ** 20, 3000, 10 ** 17, True),
        (Q96, 10 ** 20, 3000, 10 ** 17, False),
        (Q96 * 3, 10 ** 19, 500, 10 ** 16, True),
        (Q96 * 3, 10 ** 19, 500, 10 ** 16, False),
        (Q96 // 2, 5 * 10 ** 21, 1000, 10 ** 18, True),
        (Q96 // 2, 5 * 10 ** 21, 1000, 10 ** 18, False),
    ]
    for sqrt_price_x96, liquidity, fee_pips, amount, zero_for_one in vectors:
        state = _hop("pool-r", TOKEN_A, TOKEN_B, sqrt_price_x96=sqrt_price_x96,
                     liquidity=liquidity, fee_pips=fee_pips, zero_for_one=zero_for_one)
        result = simulate_hop(state, amount)
        expected = rust_swap_amount_out(
            sqrt_price_x96, liquidity, fee_pips, amount, zero_for_one
        )
        assert result.amount_out_raw == expected, (
            zero_for_one, sqrt_price_x96, liquidity, fee_pips, amount
        )


def test_simulate_hop_reports_the_observed_price_impact():
    """The reported impact is the observation, matching the capacity chain."""
    state = _hop("pool-i", TOKEN_A, TOKEN_B)
    amount = 10 ** 16
    result = simulate_hop(state, amount)
    expected = v4_pool_capacity.price_impact_bps(
        state.sqrt_price_x96, state.liquidity, state.fee_pips, amount, True
    )
    assert result.price_impact_bps == pytest.approx(expected)


def test_simulate_hop_rejects_a_zero_input():
    """A zero input is a rejection, not a zero-valued success."""
    state = _hop("pool-z", TOKEN_A, TOKEN_B)
    result = simulate_hop(state, 0)
    assert not result.ok
    assert result.reason == "zero_input"
    assert result.amount_out_raw == 0


def test_hop_state_rejects_an_unusable_pool():
    """A pool without a price, liquidity, or fee cannot be simulated."""
    with pytest.raises(ValueError):
        HopState("p", TOKEN_A, TOKEN_B, 0, 10 ** 20, 3000, True)
    with pytest.raises(ValueError):
        HopState("p", TOKEN_A, TOKEN_B, Q96, 0, 3000, True)
    with pytest.raises(ValueError):
        HopState("p", TOKEN_A, TOKEN_B, Q96, 10 ** 20, 0, True)


def test_hop_state_exposes_the_decimal_sides():
    """The decimals follow the swap direction."""
    zero_for_one = _hop("p", TOKEN_A, TOKEN_B, zero_for_one=True,
                        token0_decimals=18, token1_decimals=6)
    one_for_zero = _hop("p", TOKEN_B, TOKEN_A, zero_for_one=False,
                        token0_decimals=18, token1_decimals=6)
    assert zero_for_one.input_decimals == 18
    assert zero_for_one.output_decimals == 6
    assert one_for_zero.input_decimals == 6
    assert one_for_zero.output_decimals == 18


# ---------------------------------------------------------------------------
# Route settlement parity with the production executor
# ---------------------------------------------------------------------------

def test_route_settlement_matches_the_production_executor():
    """Profit is `max(0, balance - requiredRepay)` and executable iff > 0.

    This mirrors `RobinhoodPausedFlashLoanExecutor._handleV4FlashAccountingCallback`:
    `requiredRepay = _requiredRepay(amount, feeBps)` and
    `profitAfterRepay = balance > requiredRepay ? balance - requiredRepay : 0`.
    """
    hops = _closed_loop()
    amount = 10 ** 18
    fee_bps = 5
    result = simulate_route(hops, amount, flash_fee_bps=fee_bps)

    repay = amount + flash_fee(amount, fee_bps)
    assert result.required_repay_raw == repay
    expected_profit = max(0, result.amount_out_raw - repay)
    assert result.profit_raw == expected_profit
    assert result.executable is (expected_profit > 0)


def test_route_rejects_a_non_closing_path():
    """A path that does not return to the borrow token is not a profit."""
    hops = [
        _hop("pool-a", TOKEN_A, TOKEN_B),
        _hop("pool-b", TOKEN_B, TOKEN_C),
        _hop("pool-c", TOKEN_C, TOKEN_B),
    ]
    result = simulate_route(hops, 10 ** 18)
    assert not result.executable
    assert result.profit_raw == 0
    assert result.reason == "route_does_not_close"


def test_route_rejects_a_token_discontinuity():
    """A broken token chain is rejected at the offending hop."""
    hops = [
        _hop("pool-a", TOKEN_A, TOKEN_B),
        _hop("pool-b", TOKEN_C, TOKEN_C),  # wrong input token
        _hop("pool-c", TOKEN_C, TOKEN_A),
    ]
    result = simulate_route(hops, 10 ** 18)
    assert not result.executable
    assert result.profit_raw == 0
    assert "token_discontinuity" in result.reason


def test_route_rejects_a_zero_input():
    """A zero principal is a rejection."""
    result = simulate_route(_closed_loop(), 0)
    assert not result.executable
    assert result.profit_raw == 0
    assert result.reason == "zero_input"


def test_route_requires_at_least_one_hop():
    """An empty route is a programming error, not a silent zero."""
    with pytest.raises(ValueError):
        simulate_route([], 10 ** 18)


def test_route_of_a_flat_market_is_not_profitable():
    """Three identical pools at the same price lose to fees and impact."""
    result = simulate_route(_closed_loop(), 10 ** 18, flash_fee_bps=5)
    assert not result.executable
    assert result.profit_raw == 0
    assert result.reason == "not_profitable"
    assert len(result.hops) == HOP_COUNT


def test_route_reports_a_profit_when_the_market_is_favourable():
    """A route whose last pool pays a better rate closes with a profit."""
    # The first two hops trade at 1:1, the last hop at 1.05:1 in favour of A.
    hops = [
        _hop("pool-a", TOKEN_A, TOKEN_B),
        _hop("pool-b", TOKEN_B, TOKEN_C),
        _hop("pool-c", TOKEN_C, TOKEN_A, sqrt_price_x96=int(Q96 * 1.05)),
    ]
    result = simulate_route(hops, 10 ** 17, flash_fee_bps=5)
    assert result.amount_out_raw > result.amount_in_raw
    assert result.executable is True
    assert result.profit_raw == result.amount_out_raw - result.required_repay_raw
    assert result.reason == "profitable"


def test_route_serialises_to_a_reporting_shape():
    """The result must be serialisable for the evidence trail."""
    result = simulate_route(_closed_loop(), 10 ** 18, flash_fee_bps=5)
    payload = result.to_dict()
    assert payload["amountInRaw"] == 10 ** 18
    assert payload["executable"] is False
    assert len(payload["hops"]) == HOP_COUNT
    assert payload["hops"][0]["poolId"] == "pool-a"


# ---------------------------------------------------------------------------
# Route-level capacity (barrel rule)
# ---------------------------------------------------------------------------

def test_route_min_capacity_uses_the_tightest_hop():
    """The round trip is bounded by its tightest hop."""
    tight = _hop("pool-tight", TOKEN_A, TOKEN_B, liquidity=10 ** 18)
    wide = _hop("pool-wide", TOKEN_B, TOKEN_C, liquidity=10 ** 22)
    medium = _hop("pool-medium", TOKEN_C, TOKEN_A, liquidity=10 ** 20)
    capacity = route_min_capacity_raw([tight, wide, medium])
    tight_capacity = route_min_capacity_raw([tight])
    assert capacity == tight_capacity
    assert capacity > 0


def test_route_min_capacity_of_an_empty_route_is_zero():
    """No hops means no capacity."""
    assert route_min_capacity_raw([]) == 0


def test_route_min_capacity_is_token_denominated():
    """The capacity must not be scaled by any USD price.

    The bound is expressed in the pool's own token units, so doubling the
    liquidity doubles the capacity without any external reference.
    """
    one = _hop("pool-1", TOKEN_A, TOKEN_B, liquidity=10 ** 20)
    two = _hop("pool-2", TOKEN_A, TOKEN_B, liquidity=2 * 10 ** 20)
    single = route_min_capacity_raw([one])
    doubled = route_min_capacity_raw([two])
    assert single > 0
    # The capacity is proportional to the liquidity: doubling the liquidity
    # doubles the capacity, up to the integer granularity of the bisection.
    assert abs(doubled - 2 * single) <= 2 * single / 10 ** 12


# ---------------------------------------------------------------------------
# Architectural guards
# ---------------------------------------------------------------------------

def test_simulator_module_has_no_usd_or_price_dependency():
    """The simulator must not read a USD price, a TVL figure, or the network.

    A USD reading cannot express tradable value inside the arbitrage
    environment, so any such dependency would be an architectural
    regression even if the current code path happens not to use it.
    """
    module_path = pathlib.Path(v4_pool_capacity.__file__).with_name(
        "v4_multihop_simulator.py"
    )
    source = module_path.read_text(encoding="utf-8")
    code = _strip_comments_and_docstrings(source)

    forbidden = [
        r"\busd\b",
        r"\btvl\b",
        r"eth_price",
        r"stock_price",
        r"requests\.",
        r"urllib",
        r"httpx",
        r"aiohttp",
        r"socket\.",
        r"sqlite3",
        r"price_usd",
    ]
    for pattern in forbidden:
        assert re.search(pattern, code, re.IGNORECASE) is None, pattern


def test_simulator_imports_no_network_or_storage_module():
    """Only the pure integer maths may be imported."""
    module_path = pathlib.Path(v4_pool_capacity.__file__).with_name(
        "v4_multihop_simulator.py"
    )
    source = module_path.read_text(encoding="utf-8")

    for forbidden in ("import requests", "import urllib", "import httpx", "import aiohttp",
                      "import socket", "import sqlite3", "import requests"):
        assert forbidden not in source, forbidden


def test_simulator_only_uses_integer_accounting():
    """Every accounting figure must be an integer raw-token amount."""
    module_path = pathlib.Path(v4_pool_capacity.__file__).with_name(
        "v4_multihop_simulator.py"
    )
    source = module_path.read_text(encoding="utf-8")

    # The only float in the module is the price-impact measurement. The
    # check looks at the code, because the prose explains that the float is
    # an observation rather than an accounting value.
    code = _strip_comments_and_docstrings(source)
    assert code.count("float") == 1, code.count("float")
    assert "price_impact_bps: float" in source
    # No float-typed accounting field may exist.
    assert "profit_raw: float" not in source
    assert "amount_in_raw: float" not in source
    assert "amount_out_raw: float" not in source
