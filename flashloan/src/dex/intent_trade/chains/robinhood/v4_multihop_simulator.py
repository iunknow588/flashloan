"""Off-chain multi-hop simulator for the Robinhood V4 closed loop.

ARCH-2 of the mainnet orchestrator plan.

Why this module exists
----------------------

The "blind send" idea (skip the chain-side ``eth_call`` preview and push the
transaction straight at the sequencer) is only safe when the off-chain
computation reproduces the chain computation **exactly**. Until that parity
exists, dropping the preview means trading on a number nobody has verified,
and the ladder would happily optimise against a wrong profit curve.

This module provides that parity target. It evaluates a three-hop closed
loop entirely off-chain, using the same integer arithmetic as the canonical
Uniswap V4 concentrated-liquidity formulas, and it reproduces the chain's
own profit definition.

What it reproduces
------------------

The chain executes a route as ``X -> Y -> Z -> X`` through three pools and
then settles:

* the flash loan repays ``amount + fee(amount, feeBps)`` where
  ``fee(a, bps) = (a * bps + 9999) / 10000`` (rounded **up**);
* the profit is ``balance - requiredRepay`` when the balance is sufficient,
  otherwise **zero**;
* the route is executable only when that profit is strictly positive.

See ``contract/robinhood/DEX/src/RobinhoodPausedFlashLoanExecutor.sol``
(``_requiredRepay``, ``_fee``, ``_handleV4FlashAccountingCallback``) and
``contract/robinhood-testnet/DEX/src/test/OfficialV4FlashArb.sol``
(``previewArbitrageBestAmount``).

Every intermediate amount is carried in **raw token units** and every
division is an **integer** division, so no float ever touches a profit
figure. The single float in this module is the price-impact measurement,
which is an observation, not an accounting value.

Zero USD dependency
-------------------

No part of this module reads an ETH price, a stock price, or a TVL figure.
The verdict is expressed in the borrow token's own units, exactly like the
capacity chain in ``v4_pool_capacity``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from dex.intent_trade.chains.robinhood.v4_pool_capacity import (
    price_impact_bps,
    swap_amount_out,
)


# The flash-loan fee denominator, matching the contract's `_fee`.
_FLASH_FEE_DENOMINATOR = 10_000

# The largest number of hops the closed loop currently has. The production
# book is 3 hops for every one of its 88 loops.
HOP_COUNT = 3


def flash_fee(amount_raw: int, fee_bps: int) -> int:
    """Return the flash-loan fee, rounded up, exactly like the contract.

    The contract computes ``(amount * feeBps + 9999) / 10000``. The rounding
    is **up**, so the simulator must not round down: rounding down would make
    the simulator slightly more optimistic than the chain and could report a
    profitable route that the chain rejects.
    """
    amount_raw = int(amount_raw)
    fee_bps = int(fee_bps)
    if amount_raw <= 0 or fee_bps <= 0:
        return 0
    return (amount_raw * fee_bps + _FLASH_FEE_DENOMINATOR - 1) // _FLASH_FEE_DENOMINATOR


def required_repay(amount_raw: int, fee_bps: int) -> int:
    """Return the amount the flash loan requires back, including the fee."""
    return int(amount_raw) + flash_fee(amount_raw, fee_bps)


@dataclass(frozen=True)
class HopState:
    """The immutable on-chain state of one pool on one hop.

    Attributes
    ----------
    pool_id:
        Opaque identifier, used only for reporting.
    token_in:
        Lower-case token address that enters this hop.
    token_out:
        Lower-case token address that leaves this hop.
    sqrt_price_x96:
        The pool's current square-root price.
    liquidity:
        The pool's active liquidity.
    fee_pips:
        The pool's fixed fee in hundredths of a bip (Uniswap's unit).
    zero_for_one:
        ``True`` when token0 is the input side of this hop.
    token0_decimals / token1_decimals:
        Decimals of the pool's own token0 and token1.
    """

    pool_id: str
    token_in: str
    token_out: str
    sqrt_price_x96: int
    liquidity: int
    fee_pips: int
    zero_for_one: bool
    token0_decimals: int = 18
    token1_decimals: int = 18

    def __post_init__(self) -> None:
        if int(self.sqrt_price_x96) <= 0:
            raise ValueError(f"pool {self.pool_id} has no usable sqrt price")
        if int(self.liquidity) <= 0:
            raise ValueError(f"pool {self.pool_id} has no usable liquidity")
        if int(self.fee_pips) <= 0:
            raise ValueError(f"pool {self.pool_id} has no usable fee")

    @property
    def input_decimals(self) -> int:
        return self.token0_decimals if self.zero_for_one else self.token1_decimals

    @property
    def output_decimals(self) -> int:
        return self.token1_decimals if self.zero_for_one else self.token0_decimals


@dataclass(frozen=True)
class HopResult:
    """The outcome of simulating one hop."""

    pool_id: str
    amount_in_raw: int
    amount_out_raw: int
    price_impact_bps: float
    ok: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "poolId": self.pool_id,
            "amountInRaw": self.amount_in_raw,
            "amountOutRaw": self.amount_out_raw,
            "priceImpactBps": self.price_impact_bps,
            "ok": self.ok,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RouteResult:
    """The outcome of simulating a whole closed loop."""

    amount_in_raw: int
    amount_out_raw: int
    required_repay_raw: int
    profit_raw: int
    executable: bool
    reason: str = ""
    hops: list[HopResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "amountInRaw": self.amount_in_raw,
            "amountOutRaw": self.amount_out_raw,
            "requiredRepayRaw": self.required_repay_raw,
            "profitRaw": self.profit_raw,
            "executable": self.executable,
            "reason": self.reason,
            "hops": [hop.to_dict() for hop in self.hops],
        }


def simulate_hop(state: HopState, amount_in_raw: int) -> HopResult:
    """Simulate one exact-input swap through one pool.

    The output is the realised output of the swap, i.e. it already carries
    the price impact and the liquidity-provider fee, exactly like the chain.
    """
    amount_in_raw = int(amount_in_raw)
    if amount_in_raw <= 0:
        return HopResult(state.pool_id, amount_in_raw, 0, 0.0, False, "zero_input")

    amount_out_raw = swap_amount_out(
        state.sqrt_price_x96,
        state.liquidity,
        state.fee_pips,
        amount_in_raw,
        state.zero_for_one,
    )
    if amount_out_raw <= 0:
        return HopResult(state.pool_id, amount_in_raw, 0, 0.0, False, "zero_output")

    try:
        impact = price_impact_bps(
            state.sqrt_price_x96,
            state.liquidity,
            state.fee_pips,
            amount_in_raw,
            state.zero_for_one,
        )
    except ValueError as error:
        return HopResult(state.pool_id, amount_in_raw, amount_out_raw, 0.0, False, str(error))

    return HopResult(state.pool_id, amount_in_raw, amount_out_raw, impact, True)


def simulate_route(
    hops: Sequence[HopState],
    amount_in_raw: int,
    *,
    flash_fee_bps: int = 0,
) -> RouteResult:
    """Simulate a closed loop and settle it exactly like the chain does.

    Parameters
    ----------
    hops:
        The hops in execution order. The last hop must return to the token
        the first hop consumed; the function checks the token continuity but
        does not otherwise constrain the route.
    amount_in_raw:
        The flash-loan principal, in the borrow token's raw units.
    flash_fee_bps:
        The flash source's fee, in basis points. Zero means the flash source
        charges nothing.

    Notes
    -----
    The settlement mirrors the contract:

    * ``required_repay = amount + ceil(amount * fee_bps / 10000)``;
    * ``profit = max(0, amount_out - required_repay)``;
    * the route is executable only when ``profit > 0``.
    """
    amount_in_raw = int(amount_in_raw)
    if not hops:
        raise ValueError("a closed loop needs at least one hop")
    if amount_in_raw <= 0:
        return RouteResult(
            amount_in_raw, 0, required_repay(amount_in_raw, flash_fee_bps), 0, False, "zero_input"
        )

    current_amount = amount_in_raw
    expected_token = hops[0].token_in
    hop_results: list[HopResult] = []

    for index, state in enumerate(hops):
        if state.token_in != expected_token:
            return RouteResult(
                amount_in_raw,
                current_amount,
                required_repay(amount_in_raw, flash_fee_bps),
                0,
                False,
                f"token_discontinuity_at_hop_{index + 1}",
                hop_results,
            )
        result = simulate_hop(state, current_amount)
        hop_results.append(result)
        if not result.ok:
            return RouteResult(
                amount_in_raw,
                current_amount,
                required_repay(amount_in_raw, flash_fee_bps),
                0,
                False,
                f"hop_{index + 1}_failed:{result.reason}",
                hop_results,
            )
        current_amount = result.amount_out_raw
        expected_token = state.token_out

    if expected_token != hops[0].token_in:
        return RouteResult(
            amount_in_raw,
            current_amount,
            required_repay(amount_in_raw, flash_fee_bps),
            0,
            False,
            "route_does_not_close",
            hop_results,
        )

    repay = required_repay(amount_in_raw, flash_fee_bps)
    profit = current_amount - repay
    if profit < 0:
        profit = 0
    return RouteResult(
        amount_in_raw,
        current_amount,
        repay,
        profit,
        profit > 0,
        "profitable" if profit > 0 else "not_profitable",
        hop_results,
    )


def route_min_capacity_raw(hops: Sequence[HopState]) -> int:
    """Return the tightest one-sided capacity across the route, in raw units.

    This is the "barrel" rule: a round trip needs **every** hop to absorb the
    position, so the route is bounded by its tightest hop.

    The capacity is expressed in the **input** token's raw units of the hop
    that produced it, so the values are comparable only when the hops share
    the borrow token. For the three-hop closed loop the first hop's input is
    the borrow token, which is what the sizing path needs.
    """
    if not hops:
        return 0
    from dex.intent_trade.chains.robinhood.v4_pool_capacity import capacity_from_sqrt_price

    limits: list[int] = []
    for state in hops:
        capacity = capacity_from_sqrt_price(
            state.sqrt_price_x96,
            state.liquidity,
            state.fee_pips,
        )
        if state.zero_for_one:
            value = int(capacity.get("capacityToken0Raw", 0) or 0)
        else:
            value = int(capacity.get("capacityToken1Raw", 0) or 0)
        if value > 0:
            limits.append(value)
    return min(limits) if limits else 0
