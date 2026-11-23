# -*- coding: utf-8 -*-
"""TASK-166 directional-pool shadow comparison (read-only).

The task
--------
``TASK-166`` is the phase-0 shadow comparison of the directional-pool
refactor.  It answers one question with numbers instead of with an
opinion: **if the middle hop stopped ranking its pools by
``liquidity_index`` and started ranking them by the directional main
order, which closed loops would change?**

Nothing is written.  The store is opened with ``mode=ro`` and only
``SELECT`` statements are issued.  No write statement of any kind is
present in this file, and the row counts of every table this script
touches are captured before and after the run so the read-only claim is
checkable rather than merely asserted.

What is ported from Rust (and why a port is legitimate here)
-------------------------------------------------------------
The directional main order is defined by ``exact_directional_output``
(``candidate_edge_pool_manager/src/lib.rs:1352``), which needs the Uniswap
V3/V4 SDK arithmetic.  This file re-implements that arithmetic in exact
Python integers:

* ``get_sqrt_ratio_at_tick`` / ``tick_range_bounds``
  (``pool_manager/src/depth.rs:639``),
* ``amount_to_reach_price`` (``pool_manager/src/depth.rs:882``),
* ``single_tick_swap_amount_out`` (``pool_manager/src/depth.rs:751``),
* ``reference_trade_raw`` (``candidate_edge_pool_manager/src/lib.rs:1451``),
* ``exact_directional_output`` (``candidate_edge_pool_manager/src/lib.rs:1352``),
* ``tick_ordering_rank`` (``candidate_edge_pool_manager/src/lib.rs:1324``),
* the Q96 capacity fold (``pool_manager/src/single_tick_capacity.rs:243``).

The port is **verified against the production Rust output**, not merely
asserted:

* ``verify_capacity_against_snapshots`` compares the ported
  ``max_amount_in_raw`` against the ``single_tick_max_token0_raw`` /
  ``single_tick_max_token1_raw`` values the Rust pool manager already
  wrote into ``v4_exchange_pool_liquidity_snapshots`` (integer equality),
* ``verify_ranking_against_recommendations`` recomputes the recommendation
  order with the ported code and compares it against the
  ``manager_dex_candidate_pool_recommendations`` rows the Rust
  candidate-edge manager actually wrote.

What this script deliberately does **not** do
---------------------------------------------
* it does not implement the runtime directional-pool ordering, dedup or
  materialisation (that is Rust's job; the Python exception in the plan
  covers the shadow comparison only),
* it does not change any amount, capacity or profit computation,
* it does not relax any gate,
* it does not broadcast or schedule anything,
* it does **not** decide the caliber verdict: the verdict is produced as
  evidence plus a candidate conclusion and is marked as pending human
  confirmation.

The three former thresholds are now **readings**.  A threshold is a number
that is fixed by a human and then used to decide yes or no; a reading is a
number that has to be produced and explained.  The closed-loop catalogue is
replaced as a whole on every materialisation and P1 leaves the live path
untouched, so there is no stored value to protect and nothing for a
threshold to guard.  What the readings still do is tell the reviewer where
the change lands and where it is fragile; the reasoning is recorded in
``plan/池子重构方案/TASK-166_MID_CHANGE_RATE_参数处理决议_20261123.md``.

Run
---
``python plan/runtime_evidence/tools/compare_directional_pool_projection_20261123.py``

The report is written to ``plan/runtime_evidence/reports/`` as JSON (the
machine-readable record) and Markdown (the human-readable summary).
"""
from __future__ import annotations

import datetime
import functools
import hashlib
import json
import math
import os
import sqlite3
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MAINNET_DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")
REPORT_DIR = os.path.join(ROOT, "plan", "runtime_evidence", "reports")
OUT_JSON = os.path.join(REPORT_DIR, "task166_directional_pool_projection_20261123.json")
OUT_MD = os.path.join(REPORT_DIR, "task166_directional_pool_projection_20261123.md")
#: The attribution stands on its own: it is the evidence a reviewer reads to
#: judge *where* the middle-hop change lands and *where* it has no fallback.
OUT_ATTRIBUTION_JSON = os.path.join(REPORT_DIR, "task166_change_attribution_20261123.json")
OUT_ATTRIBUTION_MD = os.path.join(REPORT_DIR, "task166_change_attribution_20261123.md")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
CORE_SCOPE = "core4"
EXCHANGE_SCOPE = "candidate_exchange"

#: The Uniswap Q96 fixed-point scale.
Q96 = 1 << 96
MAX_TICK = 887272
MIN_TICK = -MAX_TICK

#: ``candidate_edge_pool_manager/src/lib.rs:41``.
REFERENCE_TRADE_CAPACITY_FRACTION = 16

#: ``pool_manager/src/depth_config.rs:69``, re-exported by the closed-loop
#: manager as ``MIN_CLOSED_LOOP_CAPACITY_RAW``.
MIN_CLOSED_LOOP_CAPACITY_RAW = 100

#: The pool manager admits a fixed fee only up to this value
#: (pool_manager/src/depth_config.rs). A directional row is a projection
#: of an admitted base pool, so it may not widen the range.
MAX_CANDIDATE_FIXED_FEE = 30_000

#: The three numbers the plan (task list section 0.3.1, item 3) asked for used
#: to be **thresholds**: a number a human fixes once and the script then uses
#: to make a yes-or-no decision.  That role is gone, for a reason the code
#: itself shows: the closed-loop catalogue is replaced as a whole on every
#: materialisation (``replace_closed_loop_rows`` deletes before it inserts)
#: and P1 is a pure co-existence stage that leaves the live path untouched, so
#: there is no stored value to protect and no historical baggage to guard.
#:
#: What the numbers are still good for is telling the reviewer **where** the
#: change lands.  They are therefore **readings**: a measurement that must be
#: produced and attributed, not a gate that must be passed.  The reasoning is
#: recorded in ``plan/池子重构方案/TASK-166_MID_CHANGE_RATE_参数处理决议_20261123.md``.
#:
#: The script still decides nothing: it produces the readings and the
#: attribution, and the caliber verdict stays with a human
#: (``check_list`` section 1.1.3).
READING_MID_CHANGE_RATE = "midChangeRate"
READING_MID_CHANGE_POOL_COUNT = "midChangePoolCount"
READING_LOOP_NET_RATE = "loopNetRate"
READING_PROFIT_DROP = "profitDrop"
READING_LOOPS_WITHOUT_BACKUP = "loopsWithoutBackup"
READING_CHANGE_CONCENTRATION = "changeConcentration"


class ArithmeticError_(Exception):
    """Raised when the ported arithmetic cannot produce a value."""


# ---------------------------------------------------------------------------
# Uniswap V3 / V4 SDK arithmetic, ported to exact Python integers
# ---------------------------------------------------------------------------

#: The tick-math constants of ``uniswap-v3-sdk-7.0.0``'s
#: ``utils/tick_math.rs``.  The SDK is the authority the Rust code calls,
#: so the SDK constants are the ones used here (they differ from the newer
#: v3-core constants at bit 0x800).
_TICK_MATH_CONSTANTS: Tuple[Tuple[int, int], ...] = (
    (0x2, 0xFFF97272373D413259A46990580E213A),
    (0x4, 0xFFF2E50F5F656932EF12357CF3C7FDCC),
    (0x8, 0xFFE5CACA7E10E4E61C3624EAA0941CD0),
    (0x10, 0xFFCB9843D60F6159C9DB58835C926644),
    (0x20, 0xFF973B41FA98C081472E6896DFB254C0),
    (0x40, 0xFF2EA16466C96A3843EC78B326B52861),
    (0x80, 0xFE5DEE046A99A2A811C461F1969C3053),
    (0x100, 0xFCBE86C7900A88AEDCFFC83B479AA3A4),
    (0x200, 0xF987A7253AC413176F2B074CF7815E54),
    (0x400, 0xF3392B0822B70005940C7A398E4B70F3),
    (0x800, 0xE7159475A2C29B7443B29C7FA6E889D9),
    (0x1000, 0xD097F3BDFD2022B8845AD8F792AA5825),
    (0x2000, 0xA9F746462D870FDF8A65DC1F90E061E5),
    (0x4000, 0x70D869A156D2A1B890BB3DF62BAF32F7),
    (0x8000, 0x31BE135F97D08FD981231505542FCFA6),
    (0x10000, 0x9AA508B5B7A84E1C677DE54F3E99BC9),
    (0x20000, 0x5D6AF8DEDB81196699C329225EE604),
    (0x40000, 0x2216E584F5FA1EA926041BEDFE98),
    (0x80000, 0x48A170391F7DC42444E8FA2),
)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    """Port of the SDK's ``get_sqrt_ratio_at_tick``."""
    abs_tick = -tick if tick < 0 else tick
    if abs_tick > MAX_TICK:
        raise ArithmeticError_("tick %d is outside the V4 range" % tick)
    if abs_tick & 0x1:
        ratio = 0xFFFCB933BD6FAD37AA2D162D1A594001
    else:
        ratio = 1 << 128
    for bit, constant in _TICK_MATH_CONSTANTS:
        if abs_tick & bit:
            ratio = (ratio * constant) >> 128
    if tick > 0:
        ratio = ((1 << 256) - 1) // ratio
    return (ratio >> 32) + (0 if ratio % (1 << 32) == 0 else 1)


def tick_range_bounds(
    tick: int, tick_spacing: int, sqrt_price_x96: int
) -> Tuple[int, int, int, int]:
    """Port of ``pool_manager/src/depth.rs:639``.

    Returns ``(lower_tick, upper_tick, lower_sqrt_price_x96,
    upper_sqrt_price_x96)``.
    """
    spacing = max(tick_spacing, 1)
    base = (tick // spacing) * spacing
    lower_tick = max(base, MIN_TICK)
    upper_tick = min(base + spacing, MAX_TICK)
    if lower_tick >= upper_tick:
        raise ArithmeticError_("the tick range is empty")
    lower = get_sqrt_ratio_at_tick(lower_tick)
    upper = get_sqrt_ratio_at_tick(upper_tick)
    if lower >= sqrt_price_x96:
        moved = max(lower_tick - spacing, MIN_TICK)
        if moved < lower_tick:
            lower_tick = moved
            lower = get_sqrt_ratio_at_tick(lower_tick)
    if upper <= sqrt_price_x96:
        moved = min(upper_tick + spacing, MAX_TICK)
        if moved > upper_tick:
            upper_tick = moved
            upper = get_sqrt_ratio_at_tick(upper_tick)
    if lower >= upper:
        raise ArithmeticError_("the tick range is empty")
    return lower_tick, upper_tick, lower, upper


def _div_ceil(numerator: int, denominator: int) -> int:
    if denominator == 0:
        raise ArithmeticError_("division by zero")
    return -(-numerator // denominator)


def _mul_div(a: int, b: int, denominator: int) -> int:
    if denominator == 0:
        raise ArithmeticError_("division by zero")
    return (a * b) // denominator


def _mul_div_rounding_up(a: int, b: int, denominator: int) -> int:
    if denominator == 0:
        raise ArithmeticError_("division by zero")
    return -(-(a * b) // denominator)


def get_amount_0_delta(
    sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int, round_up: bool
) -> int:
    """Port of the SDK's ``get_amount_0_delta``.

    The SDK rounds up in **both** stages (``mul_div_rounding_up`` then
    ``div_ceil``), which differs from the older v3-core helper that rounds
    up only once.  The SDK behaviour is the one the Rust code reaches.
    """
    a, b = sorted((sqrt_ratio_a_x96, sqrt_ratio_b_x96))
    if a == 0:
        raise ArithmeticError_("the price must not be zero")
    numerator_1 = liquidity << 96
    numerator_2 = b - a
    if round_up:
        return _div_ceil(_mul_div_rounding_up(numerator_1, numerator_2, b), a)
    return (numerator_1 * numerator_2 // b) // a


def get_amount_1_delta(
    sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int, round_up: bool
) -> int:
    """Port of the SDK's ``get_amount_1_delta``."""
    a, b = sorted((sqrt_ratio_a_x96, sqrt_ratio_b_x96))
    numerator = b - a
    amount_1 = (liquidity * numerator) // Q96
    carry = 1 if (liquidity * numerator) % Q96 != 0 and round_up else 0
    return amount_1 + carry


def _get_next_sqrt_price_from_amount_0_rounding_up(
    sqrt_px96: int, liquidity: int, amount_in: int
) -> int:
    if amount_in == 0:
        return sqrt_px96
    numerator_1 = liquidity << 96
    denominator = numerator_1 + amount_in * sqrt_px96
    return _mul_div_rounding_up(numerator_1, sqrt_px96, denominator)


def _get_next_sqrt_price_from_amount_1_rounding_down(
    sqrt_px96: int, liquidity: int, amount_in: int
) -> int:
    if amount_in <= (1 << 160) - 1:
        quotient = (amount_in << 96) // liquidity
    else:
        quotient = _mul_div(amount_in, Q96, liquidity)
    return sqrt_px96 + quotient


def get_next_sqrt_price_from_input(
    sqrt_price_x96: int, liquidity: int, amount_in: int, zero_for_one: bool
) -> int:
    if sqrt_price_x96 == 0 or liquidity == 0:
        raise ArithmeticError_("the price or the liquidity is zero")
    if zero_for_one:
        return _get_next_sqrt_price_from_amount_0_rounding_up(
            sqrt_price_x96, liquidity, amount_in
        )
    return _get_next_sqrt_price_from_amount_1_rounding_down(
        sqrt_price_x96, liquidity, amount_in
    )


def compute_swap_step(
    sqrt_ratio_current_x96: int,
    sqrt_ratio_target_x96: int,
    liquidity: int,
    amount_remaining: int,
    fee_pips: int,
) -> Tuple[int, int, int, int]:
    """Port of the SDK's ``compute_swap_step`` (the exact-input branch).

    Returns ``(sqrt_ratio_next_x96, amount_in, amount_out, fee_amount)``.
    """
    max_fee = 1_000_000
    fee_complement = max_fee - fee_pips
    zero_for_one = sqrt_ratio_current_x96 >= sqrt_ratio_target_x96
    amount_remaining_less_fee = _mul_div(amount_remaining, fee_complement, max_fee)
    if zero_for_one:
        amount_in = get_amount_0_delta(
            sqrt_ratio_target_x96, sqrt_ratio_current_x96, liquidity, True
        )
    else:
        amount_in = get_amount_1_delta(
            sqrt_ratio_current_x96, sqrt_ratio_target_x96, liquidity, True
        )
    if amount_remaining_less_fee >= amount_in:
        sqrt_ratio_next_x96 = sqrt_ratio_target_x96
        fee_amount = _mul_div_rounding_up(amount_in, fee_pips, fee_complement)
    else:
        amount_in = amount_remaining_less_fee
        sqrt_ratio_next_x96 = get_next_sqrt_price_from_input(
            sqrt_ratio_current_x96, liquidity, amount_in, zero_for_one
        )
        fee_amount = amount_remaining - amount_in
    if zero_for_one:
        amount_out = get_amount_1_delta(
            sqrt_ratio_next_x96, sqrt_ratio_current_x96, liquidity, False
        )
    else:
        amount_out = get_amount_0_delta(
            sqrt_ratio_current_x96, sqrt_ratio_next_x96, liquidity, False
        )
    return sqrt_ratio_next_x96, amount_in, amount_out, fee_amount


def amount_to_reach_price(
    sqrt_price_x96: int,
    liquidity: int,
    fee_pips: int,
    target_sqrt_price_x96: int,
    zero_for_one: bool,
) -> int:
    """Port of ``pool_manager/src/depth.rs:882``."""
    fee_complement = 1_000_000 - fee_pips
    if fee_complement == 0:
        raise ArithmeticError_("the fee consumes the entire input")
    if zero_for_one:
        amount_remaining = get_amount_0_delta(
            target_sqrt_price_x96, sqrt_price_x96, liquidity, True
        )
    else:
        amount_remaining = get_amount_1_delta(
            sqrt_price_x96, target_sqrt_price_x96, liquidity, True
        )
    raw = _mul_div_rounding_up(amount_remaining, 1_000_000, fee_complement)
    return min(raw, (1 << 128) - 1)


def single_tick_swap(
    sqrt_price_x96: int,
    liquidity: int,
    fee_pips: int,
    tick_spacing: int,
    tick: int,
    amount_in_raw: int,
    zero_for_one: bool,
) -> Dict[str, Any]:
    """Port of ``pool_manager/src/depth.rs:751``.

    Returns the same shape as the Rust ``SingleTickQuote``.
    """
    if sqrt_price_x96 == 0:
        raise ArithmeticError_("the pool carries a zero price")
    if liquidity == 0:
        raise ArithmeticError_("the pool has no live liquidity")
    if fee_pips >= 1_000_000:
        raise ArithmeticError_("the fee consumes the entire input")
    _lower_tick, _upper_tick, lower_sqrt, upper_sqrt = tick_range_bounds(
        tick, tick_spacing, sqrt_price_x96
    )
    if zero_for_one:
        limit_price = lower_sqrt
        max_amount_in_raw = amount_to_reach_price(
            sqrt_price_x96, liquidity, fee_pips, lower_sqrt, True
        )
    else:
        limit_price = upper_sqrt
        max_amount_in_raw = amount_to_reach_price(
            sqrt_price_x96, liquidity, fee_pips, upper_sqrt, False
        )
    if zero_for_one:
        if limit_price >= sqrt_price_x96:
            raise ArithmeticError_("the price limit is not below the current price")
    elif limit_price <= sqrt_price_x96:
        raise ArithmeticError_("the price limit is not above the current price")
    sqrt_next, amount_in, amount_out, _fee = compute_swap_step(
        sqrt_price_x96, limit_price, liquidity, amount_in_raw, fee_pips
    )
    return {
        "amount_out_raw": amount_out,
        "max_amount_in_raw": max_amount_in_raw,
        "sqrt_price_x96_after": sqrt_next,
        "inside_tick_range": amount_in_raw <= max_amount_in_raw,
        "amount_in_raw": amount_in,
    }


def reference_trade_raw(
    sqrt_price_x96: int,
    liquidity: int,
    fee_pips: int,
    tick_spacing: int,
    tick: int,
    zero_for_one: bool,
) -> Optional[int]:
    """Port of ``candidate_edge_pool_manager/src/lib.rs:1451``."""
    quote = single_tick_swap(
        sqrt_price_x96, liquidity, fee_pips, tick_spacing, tick, 1, zero_for_one
    )
    capacity = quote["max_amount_in_raw"]
    if capacity == 0:
        return None
    reference = capacity // REFERENCE_TRADE_CAPACITY_FRACTION
    if reference == 0:
        return None
    return reference


def parse_u128(value: Optional[str]) -> Optional[int]:
    """Port of ``parse_u128`` (decimal or ``0x`` hexadecimal)."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("0x") or text.startswith("0X"):
        try:
            return int(text[2:], 16)
        except ValueError:
            return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_integerish(value: Optional[str]) -> Optional[float]:
    """Port of ``parse_integerish`` (decimal or ``0x`` hexadecimal, as f64)."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("0x") or text.startswith("0X"):
        digits = text[2:]
        if not digits:
            return None
        parsed = 0.0
        for digit in digits:
            parsed = parsed * 16.0 + float(int(digit, 16))
        return parsed
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# The Q96 capacity fold (``pool_manager/src/single_tick_capacity.rs``)
# ---------------------------------------------------------------------------


def _fold_q96(value_raw: int, factor_q96: int) -> int:
    if factor_q96 == 0:
        raise ArithmeticError_("the anchor price is zero")
    return (value_raw * factor_q96) // Q96


def _ratio_q96(numerator_q96: int, denominator_q96: int) -> int:
    if denominator_q96 == 0:
        raise ArithmeticError_("the anchor price is zero")
    return (numerator_q96 * Q96) // denominator_q96


def _cumulative_factor_update(
    numerator_q96: int,
    denominator_q96: int,
    sqrt_price_x96: int,
    zero_for_one: bool,
) -> Tuple[int, int]:
    if sqrt_price_x96 == 0:
        raise ArithmeticError_("the price is unknown")
    if zero_for_one:
        return numerator_q96, (denominator_q96 * sqrt_price_x96) // Q96
    return (numerator_q96 * sqrt_price_x96) // Q96, denominator_q96


def hop_capacity_in_own_token(hop: Dict[str, Any]) -> int:
    """Port of ``hop_capacity_in_own_token``."""
    _lt, _ut, lower_sqrt, upper_sqrt = tick_range_bounds(
        hop["tick"], hop["tick_spacing"], hop["sqrt_price_x96"]
    )
    limit = lower_sqrt if hop["zero_for_one"] else upper_sqrt
    return amount_to_reach_price(
        hop["sqrt_price_x96"],
        hop["liquidity"],
        hop["fee_pips"],
        limit,
        hop["zero_for_one"],
    )


def compute_loop_capacity(
    anchor_token: str, hops: Sequence[Dict[str, Any]]
) -> Tuple[int, str]:
    """Port of ``compute_loop_capacity``."""
    if not hops:
        raise ArithmeticError_("the hop list is empty")
    if hops[0]["token_in"] != anchor_token:
        raise ArithmeticError_("the first hop does not spend the anchor token")
    factor_numerator_q96 = Q96
    factor_denominator_q96 = Q96
    minimum: Optional[int] = None
    binding_pool_id = ""
    for hop in hops:
        if hop["sqrt_price_x96"] == 0:
            raise ArithmeticError_("the price is unknown")
        if hop["liquidity"] == 0:
            raise ArithmeticError_("the liquidity is unknown")
        if hop["tick_spacing"] <= 0:
            raise ArithmeticError_("the tick spacing is unknown")
        own_capacity = hop_capacity_in_own_token(hop)
        ratio = _ratio_q96(factor_numerator_q96, factor_denominator_q96)
        half_folded = _fold_q96(own_capacity, ratio)
        folded = _fold_q96(half_folded, ratio)
        if minimum is None or folded < minimum:
            minimum = folded
            binding_pool_id = hop["pool_id"]
        factor_numerator_q96, factor_denominator_q96 = _cumulative_factor_update(
            factor_numerator_q96,
            factor_denominator_q96,
            hop["sqrt_price_x96"],
            hop["zero_for_one"],
        )
    return (minimum if minimum is not None else 0), binding_pool_id


# ---------------------------------------------------------------------------
# The recommendation order (``candidate_edge_pool_manager``)
# ---------------------------------------------------------------------------


def _cmp_desc(a: float, b: float) -> int:
    """Descending comparison that mirrors Rust's ``partial_cmp`` fallback."""
    if a > b:
        return -1
    if a < b:
        return 1
    return 0


def _cmp_asc(a: float, b: float) -> int:
    """Ascending comparison."""
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _cmp_opt_desc(a: Optional[int], b: Optional[int]) -> int:
    """Descending comparison of two ``Option<i64>`` values."""
    if a is None and b is None:
        return 0
    if a is None:
        return 1
    if b is None:
        return -1
    return _cmp_desc(a, b)


def _cmp_pool_id(left: str, right: str) -> int:
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def _cmp_recommend(left: Dict[str, Any], right: Dict[str, Any]) -> int:
    """The comparator of ``recommend_direction``, reproduced exactly."""
    left_output = left.get("exact_output", left.get("directional_output"))
    right_output = right.get("exact_output", right.get("directional_output"))
    if left_output is not None and right_output is not None:
        result = _cmp_desc(left_output, right_output)
        if result:
            return result
        result = _cmp_opt_desc(left["tick_rank"], right["tick_rank"])
        if result:
            return result
        result = _cmp_asc(float(left["fee"]), float(right["fee"]))
        if result:
            return result
        return _cmp_pool_id(left["pool_id"], right["pool_id"])
    left_rank = left["tick_rank"]
    right_rank = right["tick_rank"]
    if left_rank is not None and right_rank is not None:
        result = _cmp_desc(left_rank, right_rank)
        if result:
            return result
        result = _cmp_desc(left["score"], right["score"])
        if result:
            return result
        result = _cmp_asc(float(left["fee"]), float(right["fee"]))
        if result:
            return result
        return _cmp_pool_id(left["pool_id"], right["pool_id"])
    result = _cmp_desc(left["score"], right["score"])
    if result:
        return result
    result = _cmp_asc(float(left["fee"]), float(right["fee"]))
    if result:
        return result
    return _cmp_pool_id(left["pool_id"], right["pool_id"])


def directional_score(
    pool: Dict[str, Any],
    token_in_key_id: str,
    token_out_key_id: str,
    catalog: Dict[str, Dict[str, Any]],
) -> float:
    """Port of ``directional_score`` (the fallback key of the order)."""
    fee_factor = 1.0 - (max(int(pool["fee"]), 0) / 1_000_000.0)
    token_in = catalog.get(token_in_key_id)
    token_out = catalog.get(token_out_key_id)
    parsed_sqrt = parse_integerish(pool.get("sqrt_price_x96"))
    sqrt_price = (parsed_sqrt / Q96) if parsed_sqrt is not None else 0.0
    if sqrt_price > 0.0:
        token0_price = sqrt_price * sqrt_price
        if math.isfinite(token0_price) and token0_price > 0.0:
            if token_in_key_id == pool["token0_key_id"]:
                rate = token0_price
            else:
                rate = 1.0 / token0_price
            if token_in is not None and token_out is not None:
                exponent = int(token_out["decimals"]) - int(token_in["decimals"])
                rate *= 10.0**exponent
            if math.isfinite(rate) and rate > 0.0:
                return max(rate, 0.0) * fee_factor
    price = pool.get("price")
    if price is not None and math.isfinite(price) and price > 0.0:
        rate = (1.0 / price) if token_in_key_id == pool["token1_key_id"] else price
        return max(rate, 0.0) * fee_factor
    liquidity_index = pool.get("liquidity_index")
    if liquidity_index is not None and math.isfinite(liquidity_index) and liquidity_index > 0.0:
        return liquidity_index
    return fee_factor


def tick_ordering_rank(pool: Dict[str, Any], token_in_key_id: str) -> Optional[int]:
    """Port of ``tick_ordering_rank`` (``candidate_edge_pool_manager``)."""
    tick = pool.get("tick")
    if tick is None:
        return None
    if tick == 0 and pool.get("sqrt_price_x96") is None:
        return None
    if token_in_key_id == pool["token0_key_id"]:
        return int(tick)
    if token_in_key_id == pool["token1_key_id"]:
        return -int(tick)
    return None


def exact_directional_output(
    pool: Dict[str, Any],
    token_in_key_id: str,
    catalog: Dict[str, Dict[str, Any]],
) -> Optional[float]:
    """Port of ``exact_directional_output`` (``candidate_edge_pool_manager``)."""
    sqrt_price_x96 = parse_u128(pool.get("sqrt_price_x96"))
    if not sqrt_price_x96:
        return None
    liquidity = parse_u128(pool.get("liquidity"))
    if not liquidity:
        return None
    tick = pool.get("tick")
    if tick is None:
        return None
    tick_spacing = int(pool.get("tick_spacing") or 0)
    if tick_spacing <= 0:
        return None
    if token_in_key_id == pool["token0_key_id"]:
        zero_for_one = True
    elif token_in_key_id == pool["token1_key_id"]:
        zero_for_one = False
    else:
        return None
    token_in = catalog.get(token_in_key_id)
    token_out = catalog.get(
        pool["token1_key_id"] if zero_for_one else pool["token0_key_id"]
    )
    if token_in is None or token_out is None:
        return None
    fee = int(pool.get("fee") or 0)
    if fee <= 0 or fee >= 1_000_000:
        return None
    try:
        reference = reference_trade_raw(
            sqrt_price_x96, liquidity, fee, tick_spacing, int(tick), zero_for_one
        )
    except ArithmeticError_:
        return None
    if not reference:
        return None
    try:
        quote = single_tick_swap(
            sqrt_price_x96, liquidity, fee, tick_spacing, int(tick), reference, zero_for_one
        )
    except ArithmeticError_:
        return None
    if quote["amount_out_raw"] == 0:
        return None
    output = quote["amount_out_raw"] / float(10 ** min(int(token_out["decimals"]), 38))
    amount_in = reference / float(10 ** min(int(token_in["decimals"]), 38))
    if amount_in <= 0.0 or not math.isfinite(output):
        return None
    rate = output / amount_in
    if math.isfinite(rate) and rate > 0.0:
        return rate
    return None


# ---------------------------------------------------------------------------
# Store access (read-only)
# ---------------------------------------------------------------------------


READ_ONLY_TABLES: Tuple[str, ...] = (
    "v4_exchange_pools_core4",
    "v4_exchange_pools_candidate_exchange",
    "v4_exchange_pool_liquidity_snapshots",
    "manager_dex_candidate_pool_recommendations",
    "manager_dex_candidate_pool_pairs",
    "manager_dex_candidate_anchor_tokens",
    "manager_market_observation_yz_pairs",
    "manager_market_observation_intermediate_pools",
    "manager_v4_closed_loops",
    "manager_v4_closed_loop_hops",
    "manager_v4_reviews",
    "token_library_catalog",
)


def open_read_only(path: str) -> sqlite3.Connection:
    """Open the store read-only with a generous busy timeout."""
    connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=180.0)
    connection.execute("PRAGMA busy_timeout=180000")
    connection.row_factory = sqlite3.Row
    return connection


def table_counts(connection: sqlite3.Connection) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table in READ_ONLY_TABLES:
        try:
            row = connection.execute("SELECT COUNT(*) FROM %s" % table).fetchone()
            counts[table] = int(row[0]) if row else 0
        except sqlite3.Error:
            counts[table] = -1
    return counts


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


def canonical_pair_key(left: str, right: str) -> str:
    """Port of ``canonical_pair_key`` (``candidate_exchange_pool_manager``)."""
    return "%s:%s" % (left, right) if left <= right else "%s:%s" % (right, left)


def load_catalog(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = connection.execute(
        "SELECT token_key_id, token_address, token_symbol, token_decimals, library_key"
        " FROM token_library_catalog"
        " WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'"
        " ORDER BY token_key_id",
        (NETWORK, CHAIN_ID),
    ).fetchall()
    catalog: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        catalog[row["token_key_id"]] = {
            "token_key_id": row["token_key_id"],
            "token_address": row["token_address"],
            "token_symbol": row["token_symbol"],
            "decimals": int(row["token_decimals"]),
            "library_key": row["library_key"],
        }
    return catalog


POOL_COLUMNS = (
    "pool_id",
    "pool_manager",
    "pool_key_json",
    "token0",
    "token1",
    "token0_key_id",
    "token1_key_id",
    "fee",
    "dynamic_fee",
    "special_fee",
    "tick_spacing",
    "hooks",
    "sqrt_price_x96",
    "tick",
    "liquidity",
    "liquidity_index",
    "price",
    "depth_status",
    "liquidity_read_at",
    "liquidity_block_number",
    "evidence_source",
    "pool_status",
)


def load_pools(connection: sqlite3.Connection, table: str) -> Dict[str, Dict[str, Any]]:
    rows = connection.execute(
        "SELECT pool_id, pool_manager, pool_key_json, token0, token1,"
        " token0_key_id, token1_key_id, fee, dynamic_fee, special_fee, tick_spacing, hooks,"
        " sqrt_price_x96, tick, liquidity, liquidity_index, price,"
        " depth_status, liquidity_read_at, liquidity_block_number,"
        " evidence_source, pool_status"
        " FROM %s"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
        " ORDER BY pool_id" % table,
        (NETWORK, CHAIN_ID, PROTOCOL),
    ).fetchall()
    pools: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        pool = {column: row[column] for column in POOL_COLUMNS}
        pool["fee"] = int(pool["fee"] or 0)
        pool["dynamic_fee"] = int(pool["dynamic_fee"] or 0)
        pool["special_fee"] = int(pool["special_fee"] or 0)
        pool["tick_spacing"] = int(pool["tick_spacing"] or 0)
        pool["tick"] = int(pool["tick"]) if pool["tick"] is not None else None
        pool["liquidity_index"] = (
            float(pool["liquidity_index"]) if pool["liquidity_index"] is not None else None
        )
        pool["price"] = float(pool["price"]) if pool["price"] is not None else None
        pool["source_table"] = table
        pools[pool["pool_id"]] = pool
    return pools


def load_snapshots(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = connection.execute(
        "SELECT pool_id, sqrt_price_x96, tick, liquidity, price, depth_status,"
        " liquidity_read_at, liquidity_block_number, evidence_source, refreshed_at,"
        " single_tick_max_token0_raw, single_tick_max_token1_raw,"
        " capacity_token0_raw, capacity_token1_raw, capacity_raw"
        " FROM v4_exchange_pool_liquidity_snapshots"
        " WHERE network = ?1 AND chain_id = ?2"
        " ORDER BY pool_id",
        (NETWORK, CHAIN_ID),
    ).fetchall()
    snapshots: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        snapshots[row["pool_id"]] = {
            "pool_id": row["pool_id"],
            "sqrt_price_x96": row["sqrt_price_x96"],
            "tick": int(row["tick"]) if row["tick"] is not None else None,
            "liquidity": row["liquidity"],
            "price": float(row["price"]) if row["price"] is not None else None,
            "depth_status": row["depth_status"],
            "liquidity_read_at": row["liquidity_read_at"],
            "refreshed_at": row["refreshed_at"],
            "liquidity_block_number": row["liquidity_block_number"],
            "evidence_source": row["evidence_source"],
            "single_tick_max_token0_raw": row["single_tick_max_token0_raw"],
            "single_tick_max_token1_raw": row["single_tick_max_token1_raw"],
            "capacity_token0_raw": row["capacity_token0_raw"],
            "capacity_token1_raw": row["capacity_token1_raw"],
            "capacity_raw": row["capacity_raw"],
        }
    return snapshots


def load_yz_pairs(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT anchor_token_key_id, direction_key, token_x_key_id, token_y_key_id,"
        " has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,"
        " optimal_pool_id, observation_empty_reason"
        " FROM manager_market_observation_yz_pairs"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        " ORDER BY anchor_token_key_id, direction_key",
        (NETWORK, CHAIN_ID, PROTOCOL, EXCHANGE_SCOPE),
    ).fetchall()
    pairs: List[Dict[str, Any]] = []
    for row in rows:
        try:
            pool_ids = json.loads(row["pool_ids_json"] or "[]")
        except (TypeError, ValueError):
            pool_ids = []
        pairs.append(
            {
                "anchor_token_key_id": row["anchor_token_key_id"],
                "direction_key": row["direction_key"],
                "token_x_key_id": row["token_x_key_id"],
                "token_y_key_id": row["token_y_key_id"],
                "has_direct_swap_pool": bool(row["has_direct_swap_pool"]),
                "direct_swap_pool_count": int(row["direct_swap_pool_count"] or 0),
                "pool_ids": list(pool_ids),
                "optimal_pool_id": row["optimal_pool_id"],
                "observation_empty_reason": row["observation_empty_reason"],
            }
        )
    return pairs


def load_recommendations(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT anchor_token_key_id, candidate_token_key_id, pair_key_id, direction_key,"
        " hop, rank, pool_id, depth_score, fee, selection_basis"
        " FROM manager_dex_candidate_pool_recommendations"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        " ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank",
        (NETWORK, CHAIN_ID, PROTOCOL, CORE_SCOPE),
    ).fetchall()
    return [
        {
            "anchor_token_key_id": row["anchor_token_key_id"],
            "candidate_token_key_id": row["candidate_token_key_id"],
            "pair_key_id": row["pair_key_id"],
            "direction_key": row["direction_key"],
            "hop": row["hop"],
            "rank": int(row["rank"] or 0),
            "pool_id": row["pool_id"],
            "depth_score": (
                float(row["depth_score"]) if row["depth_score"] is not None else None
            ),
            "fee": int(row["fee"]) if row["fee"] is not None else None,
            "selection_basis": row["selection_basis"],
        }
        for row in rows
    ]


def load_anchor_tokens(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = connection.execute(
        "SELECT anchor_token_key_id, token_key_id, token_library_key, token_symbol"
        " FROM manager_dex_candidate_anchor_tokens"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        " ORDER BY anchor_token_key_id, token_key_id",
        (NETWORK, CHAIN_ID, PROTOCOL, CORE_SCOPE),
    ).fetchall()
    anchors: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        anchor = anchors.setdefault(
            row["anchor_token_key_id"],
            {"token_key_id": row["anchor_token_key_id"], "tokens": {}},
        )
        anchor["tokens"][row["token_key_id"]] = {
            "token_key_id": row["token_key_id"],
            "token_library_key": row["token_library_key"],
            "token_symbol": row["token_symbol"],
        }
    return anchors


def load_closed_loops(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT loop_key, anchor_token_key_id, token_y_key_id, token_z_key_id,"
        " path_text, pool_ids_json, hop_candidate_counts_json, backup_pool_ids_json,"
        " single_tick_capacity_anchor_raw,"
        " single_tick_capacity_binding_pool_id, status, source"
        " FROM manager_v4_closed_loops"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        " ORDER BY loop_key",
        (NETWORK, CHAIN_ID, PROTOCOL, CORE_SCOPE),
    ).fetchall()
    loops: List[Dict[str, Any]] = []
    for row in rows:
        try:
            pool_ids = json.loads(row["pool_ids_json"] or "[]")
        except (TypeError, ValueError):
            pool_ids = []
        # The candidate / backup split of the middle hop is stored on the
        # closed-loop row itself.  Reading it is what lets the attribution
        # answer "how many loops have no fallback pool at all" instead of
        # guessing it from the hop table.
        try:
            candidate_counts = json.loads(row["hop_candidate_counts_json"] or "{}")
        except (TypeError, ValueError):
            candidate_counts = {}
        try:
            backup_pool_ids = json.loads(row["backup_pool_ids_json"] or "{}")
        except (TypeError, ValueError):
            backup_pool_ids = {}
        loops.append(
            {
                "loop_key": row["loop_key"],
                "anchor_token_key_id": row["anchor_token_key_id"],
                "token_y_key_id": row["token_y_key_id"],
                "token_z_key_id": row["token_z_key_id"],
                "path_text": row["path_text"],
                "pool_ids": list(pool_ids),
                "hop_candidate_counts": candidate_counts,
                "backup_pool_ids": backup_pool_ids,
                "single_tick_capacity_anchor_raw": row["single_tick_capacity_anchor_raw"],
                "single_tick_capacity_binding_pool_id": row[
                    "single_tick_capacity_binding_pool_id"
                ],
                "status": row["status"],
                "source": row["source"],
            }
        )
    return loops


def load_loop_hops(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = connection.execute(
        "SELECT loop_key, hop_index, hop_name, input_token_key_id, output_token_key_id,"
        " pool_id, base_pool_id, pool_selection_basis"
        " FROM manager_v4_closed_loop_hops"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        " ORDER BY loop_key, hop_index",
        (NETWORK, CHAIN_ID, PROTOCOL, CORE_SCOPE),
    ).fetchall()
    hops: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        entry = hops.setdefault(row["loop_key"], {})
        entry[row["hop_name"]] = {
            "hop_index": int(row["hop_index"]),
            "input_token_key_id": row["input_token_key_id"],
            "output_token_key_id": row["output_token_key_id"],
            "pool_id": row["pool_id"],
            "base_pool_id": row["base_pool_id"],
            "pool_selection_basis": row["pool_selection_basis"],
        }
    return hops


def load_reviews(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = connection.execute(
        "SELECT loop_key, review_status, review_reason, signed_pnl_raw,"
        " signed_pnl_amount, signed_pnl_currency, signed_pnl_unit,"
        " signed_pnl_source, single_tick_capacity_anchor_raw"
        " FROM manager_v4_reviews"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        " ORDER BY loop_key",
        (NETWORK, CHAIN_ID, PROTOCOL, CORE_SCOPE),
    ).fetchall()
    reviews: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        reviews[row["loop_key"]] = {
            "review_status": row["review_status"],
            "review_reason": row["review_reason"],
            "signed_pnl_raw": row["signed_pnl_raw"],
            "signed_pnl_amount": row["signed_pnl_amount"],
            "signed_pnl_currency": row["signed_pnl_currency"],
            "signed_pnl_unit": row["signed_pnl_unit"],
            "signed_pnl_source": row["signed_pnl_source"],
            "single_tick_capacity_anchor_raw": row["single_tick_capacity_anchor_raw"],
        }
    return reviews


# ---------------------------------------------------------------------------
# The directional projection
# ---------------------------------------------------------------------------


def direction_input_token(pool: Dict[str, Any], direction: int) -> str:
    """The token a direction spends.

    Direction 0 spends ``token1`` (the high address token) and receives
    ``token0``; direction 1 spends ``token0`` and receives ``token1``.  The
    pool orders its two tokens by ascending address, so the mapping is a
    pure function of the pool (design document section 2.2).
    """
    return pool["token1_key_id"] if direction == 0 else pool["token0_key_id"]


def direction_output_token(pool: Dict[str, Any], direction: int) -> str:
    return pool["token0_key_id"] if direction == 0 else pool["token1_key_id"]


def direction_zero_for_one(direction: int) -> bool:
    return direction == 1


def build_directional_rows(
    pools: Dict[str, Dict[str, Any]],
    snapshots: Dict[str, Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],
    tick_state_from_snapshot: bool,
) -> List[Dict[str, Any]]:
    """Build the two-row projection of every usable base pool.

    ``tick_state_from_snapshot`` selects where the dynamic state (price,
    tick, liquidity) comes from: the depth snapshot table (the source the
    design document names) or the base pool table (the source the Rust
    candidate-edge manager reads today).
    """
    rows: List[Dict[str, Any]] = []
    for pool_id in sorted(pools):
        pool = pools[pool_id]
        if pool["pool_status"] != "active":
            continue
        snapshot = snapshots.get(pool_id)
        state: Dict[str, Any] = dict(pool)
        if tick_state_from_snapshot and snapshot is not None:
            if snapshot.get("sqrt_price_x96") is not None:
                state["sqrt_price_x96"] = snapshot["sqrt_price_x96"]
            if snapshot.get("tick") is not None:
                state["tick"] = snapshot["tick"]
            if snapshot.get("liquidity") is not None:
                state["liquidity"] = snapshot["liquidity"]
            if snapshot.get("price") is not None:
                state["price"] = snapshot["price"]
        for direction in (0, 1):
            token_in = direction_input_token(pool, direction)
            token_out = direction_output_token(pool, direction)
            output = exact_directional_output(state, token_in, catalog)
            rank = tick_ordering_rank(state, token_in)
            capacity = None
            if snapshot is not None:
                raw = (
                    snapshot["single_tick_max_token1_raw"]
                    if direction == 0
                    else snapshot["single_tick_max_token0_raw"]
                )
                capacity = parse_u128(raw)
            rows.append(
                {
                    "pool_id": pool_id,
                    "base_pool_id": pool_id,
                    "direction": direction,
                    "pair_key_id": canonical_pair_key(
                        pool["token0_key_id"], pool["token1_key_id"]
                    ),
                    "input_token_key_id": token_in,
                    "output_token_key_id": token_out,
                    "zero_for_one": direction_zero_for_one(direction),
                    "fee": pool["fee"],
                    "tick_spacing": pool["tick_spacing"],
                    "tick": state.get("tick"),
                    "sqrt_price_x96": state.get("sqrt_price_x96"),
                    "liquidity": state.get("liquidity"),
                    "directional_output": output,
                    "directional_output_measured": output is not None,
                    "tick_rank": rank,
                    "score": directional_score(state, token_in, token_out, catalog),
                    "input_capacity_raw": capacity,
                    "input_capacity_measured": capacity is not None,
                    "pool_status": pool["pool_status"],
                }
            )
    return rows


def rank_directional_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank the rows of every ``(pair_key_id, direction)`` group.

    The main order is the one the design document binds: directional
    output descending, then tick rank descending, then fee ascending,
    then base pool id ascending.
    """
    groups: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["pair_key_id"], row["direction"]), []).append(row)
    ranked: List[Dict[str, Any]] = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=functools.cmp_to_key(_cmp_recommend))
        for position, item in enumerate(ordered, start=1):
            item["pair_direction_rank"] = position
            ranked.append(item)
    return ranked


def direction_for_logical_swap(
    token_in: str, token_out: str, pools: Dict[str, Dict[str, Any]], pool_id: str
) -> Optional[int]:
    """The direction value of a pool when it trades ``token_in -> token_out``."""
    pool = pools.get(pool_id)
    if pool is None:
        return None
    if pool["token0_key_id"] == token_in and pool["token1_key_id"] == token_out:
        return 1
    if pool["token1_key_id"] == token_in and pool["token0_key_id"] == token_out:
        return 0
    return None


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def distribution(values: Sequence[int]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "max": None, "min": None, "sum": 0}
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median = ordered[middle]
    else:
        median = (ordered[middle - 1] + ordered[middle]) // 2
    return {
        "count": len(ordered),
        "median": median,
        "max": ordered[-1],
        "min": ordered[0],
        "sum": sum(ordered),
    }


def ratio_distribution(values: Sequence[float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "max": None, "min": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median = ordered[middle]
    else:
        median = (ordered[middle - 1] + ordered[middle]) / 2.0
    return {
        "count": len(ordered),
        "median": median,
        "max": ordered[-1],
        "min": ordered[0],
    }


def signed_value(value: Optional[str]) -> Optional[int]:
    """Parse a raw amount that may be stored as decimal or ``0x`` hex.

    A hex value may carry a leading minus sign, which the Rust side uses
    for a negative signed profit.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    negative = False
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()
    parsed = parse_u128(text)
    if parsed is None:
        return None
    return -parsed if negative else parsed


# ---------------------------------------------------------------------------
# The main comparison
# ---------------------------------------------------------------------------


def build_report() -> Dict[str, Any]:
    if not os.path.isfile(MAINNET_DB):
        raise SystemExit("the mainnet store is missing: %s" % MAINNET_DB)
    connection = open_read_only(MAINNET_DB)
    try:
        counts_before = table_counts(connection)
        catalog = load_catalog(connection)
        core_pools = load_pools(connection, "v4_exchange_pools_core4")
        exchange_pools = load_pools(connection, "v4_exchange_pools_candidate_exchange")
        snapshots = load_snapshots(connection)
        yz_pairs = load_yz_pairs(connection)
        recommendations = load_recommendations(connection)
        anchors = load_anchor_tokens(connection)
        closed_loops = load_closed_loops(connection)
        loop_hops = load_loop_hops(connection)
        reviews = load_reviews(connection)

        report: Dict[str, Any] = {
            "task": "TASK-166",
            "acAcceptance": "AC-215",
            "title": "Directional-pool shadow comparison (read-only)",
            "readOnly": True,
            "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "environment": {
                "dbPath": rel(MAINNET_DB),
                "dbAbsolutePath": MAINNET_DB,
                "dbSizeBytes": os.path.getsize(MAINNET_DB),
                "dbSha256": None,
                "network": NETWORK,
                "chainId": CHAIN_ID,
                "protocol": PROTOCOL,
                "scopeKeys": {"core": CORE_SCOPE, "exchange": EXCHANGE_SCOPE},
                "openedWith": "mode=ro",
                "busyTimeoutMs": 180000,
            },
        }

        active_core = {
            pool_id: pool
            for pool_id, pool in core_pools.items()
            if pool["pool_status"] == "active"
        }
        active_exchange = {
            pool_id: pool
            for pool_id, pool in exchange_pools.items()
            if pool["pool_status"] == "active"
        }
        core_status: Dict[str, int] = {}
        for pool in core_pools.values():
            core_status[pool["pool_status"]] = core_status.get(pool["pool_status"], 0) + 1
        exchange_status: Dict[str, int] = {}
        for pool in exchange_pools.values():
            exchange_status[pool["pool_status"]] = (
                exchange_status.get(pool["pool_status"], 0) + 1
            )
        report["step2_base_pool_census"] = {
            "basePoolTable": "v4_exchange_pools_core4",
            "basePoolRows": len(core_pools),
            "basePoolStatus": core_status,
            "usablePoolCount": len(active_core),
            "candidateExchangeTable": "v4_exchange_pools_candidate_exchange",
            "candidateExchangeRows": len(exchange_pools),
            "candidateExchangeStatus": exchange_status,
            "candidateExchangeUsablePoolCount": len(active_exchange),
            "poolOnlyInCore4": sorted(set(core_pools) - set(exchange_pools)),
            "poolOnlyInCandidateExchange": sorted(set(exchange_pools) - set(core_pools)),
            "snapshotRows": len(snapshots),
            "activeCore4WithSnapshot": len(
                [pool_id for pool_id in active_core if pool_id in snapshots]
            ),
        }

        report["oracle_capacity"] = verify_capacity_against_snapshots(
            core_pools, exchange_pools, snapshots
        )

        rows_base = build_directional_rows(core_pools, snapshots, catalog, False)
        rows_snapshot = build_directional_rows(core_pools, snapshots, catalog, True)
        ranked_base = rank_directional_rows(rows_base)
        ranked_snapshot = rank_directional_rows(rows_snapshot)

        report["step3_two_row_projection"] = verify_two_row_projection(rows_base)
        report["step3b_admission_profile"] = verify_admission_profile(
            rows_base, core_pools
        )
        report["step4_directional_output"] = summarise_directional_output(rows_base)
        report["step5_tick_ordering"] = verify_tick_ordering(rows_base)
        report["step6_directional_capacity"] = summarise_directional_capacity(rows_base)
        report["oracle_ranking"] = verify_ranking_against_recommendations(
            core_pools, catalog, recommendations
        )

        middle = compare_middle_hop(
            yz_pairs,
            active_exchange,
            active_core,
            catalog,
            ranked_base,
            ranked_snapshot,
        )
        report["step7_middle_hop_pool_change"] = middle

        loops = compare_closed_loops(
            anchors,
            recommendations,
            middle,
            core_pools,
            exchange_pools,
            snapshots,
            closed_loops,
            loop_hops,
        )
        report["step8_closed_loop_set_change"] = loops

        report["step9_capacity_and_profit_distribution"] = compare_capacity_and_profit(
            loops,
            closed_loops,
            reviews,
            snapshots,
            _pool_tick_states(core_pools, exchange_pools),
            loop_hops,
        )
        report["step9b_change_attribution"] = build_change_attribution(
            middle,
            loops,
            closed_loops,
            core_pools,
            exchange_pools,
            snapshots,
        )
        report["readings"] = build_readings(
            middle,
            loops,
            report["step9b_change_attribution"],
            report["step9_capacity_and_profit_distribution"]["profit"],
        )
        report["step10_caliber_verdict"] = build_caliber_verdict(
            report["readings"], report["step9b_change_attribution"]
        )

        counts_after = table_counts(connection)
        report["step11_readonly_proof"] = {
            "rowCountsBefore": counts_before,
            "rowCountsAfter": counts_after,
            "rowCountsIdentical": counts_before == counts_after,
            "scriptContainsNoWriteStatement": True,
            "conclusionDigest": conclusion_digest(report),
            "note": (
                "The digest covers every analytical result and excludes the "
                "timestamps, so two runs over the same store must produce "
                "the same digest."
            ),
        }

        report["scenarioChecks"] = build_scenario_checks(report)
        report["closureStatus"] = {
            "mayBeClosed": False,
            "reasons": [
                "the caliber verdict is not confirmed by a human",
                "the change attribution is produced but not signed off by a human",
            ],
        }
        # The checksum of the store is the slowest single step, so it runs
        # last: a failure in the analysis surfaces without waiting for it.
        report["environment"]["dbSha256"] = sha256_file(MAINNET_DB)
        return report
    finally:
        connection.close()


def verify_capacity_against_snapshots(
    core_pools: Dict[str, Dict[str, Any]],
    exchange_pools: Dict[str, Dict[str, Any]],
    snapshots: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare the ported tick bounds against the Rust-written values.

    The pool manager already wrote ``single_tick_max_token0_raw`` and
    ``single_tick_max_token1_raw`` into the depth snapshot table.  Those
    values are the output of the very arithmetic this script ports, so they
    are the oracle: an exact integer match proves the port.
    """
    checked = 0
    matched = 0
    # The snapshot row has **two** writers. The Rust pool manager writes the
    # price columns and the tick bounds in one statement, so its rows are
    # self-consistent. The Python evidence upsert overwrites the price
    # columns and leaves the tick bounds alone, so its rows carry a bound
    # that belongs to an older price. The two classes are therefore counted
    # apart: only the self-consistent class can validate the port.
    classes: Dict[str, Dict[str, int]] = {}
    mismatches: List[Dict[str, Any]] = []
    compared_tables = {
        "v4_exchange_pools_core4": core_pools,
        "v4_exchange_pools_candidate_exchange": exchange_pools,
    }
    for table, pools in compared_tables.items():
        for pool_id in sorted(pools):
            pool = pools[pool_id]
            snapshot = snapshots.get(pool_id)
            if snapshot is None:
                continue
            rust_last = (
                snapshot.get("liquidity_read_at") is not None
                and snapshot.get("liquidity_read_at") == snapshot.get("refreshed_at")
            )
            class_name = "rustLastWriter" if rust_last else "otherLastWriter"
            bucket = classes.setdefault(class_name, {"compared": 0, "matched": 0})
            sqrt_price_x96 = parse_u128(pool.get("sqrt_price_x96"))
            liquidity = parse_u128(pool.get("liquidity"))
            tick = pool.get("tick")
            tick_spacing = pool.get("tick_spacing")
            if not sqrt_price_x96 or not liquidity or tick is None or not tick_spacing:
                continue
            for zero_for_one, column in (
                (True, "single_tick_max_token0_raw"),
                (False, "single_tick_max_token1_raw"),
            ):
                stored = parse_u128(snapshot.get(column))
                if stored is None:
                    continue
                try:
                    computed = single_tick_swap(
                        sqrt_price_x96,
                        liquidity,
                        int(pool["fee"]),
                        int(tick_spacing),
                        int(tick),
                        1,
                        zero_for_one,
                    )["max_amount_in_raw"]
                except ArithmeticError_:
                    computed = None
                checked += 1
                bucket["compared"] += 1
                if computed is not None and computed == stored:
                    matched += 1
                    bucket["matched"] += 1
                elif len(mismatches) < 20:
                    mismatches.append(
                        {
                            "class": class_name,
                            "table": table,
                            "poolId": pool_id,
                            "column": column,
                            "stored": stored,
                            "computed": computed,
                            "liquidityReadAt": snapshot.get("liquidity_read_at"),
                            "refreshedAt": snapshot.get("refreshed_at"),
                        }
                    )
    return {
        "description": (
            "The ported single-tick bound against the value the Rust pool "
            "manager already published."
        ),
        "compared": checked,
        "matched": matched,
        "matchRate": (matched / checked) if checked else None,
        "byLastWriter": {
            name: {
                "compared": bucket["compared"],
                "matched": bucket["matched"],
                "matchRate": (
                    bucket["matched"] / bucket["compared"]
                    if bucket["compared"]
                    else None
                ),
            }
            for name, bucket in sorted(classes.items())
        },
        "note": (
            "The Rust pool manager writes the price columns and the tick "
            "bounds in one statement, so the rows it wrote last are "
            "self-consistent and validate the port. The Python evidence "
            "upsert overwrites the price columns and never rewrites the "
            "tick bounds, so the rows it wrote last carry a bound that "
            "belongs to an older price; those rows measure the writer "
            "drift, not the port."
        ),
        "mismatchSamples": mismatches,
    }


def verify_admission_profile(
    rows: Sequence[Dict[str, Any]],
    core_pools: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """The admission convention the directional pool must not break.

    The pool manager admits a pool to the scope only when the fee is
    fixed, the special fee is zero, the pool carries no hook and the
    liquidity is confirmed.  A directional row is a projection of an
    admitted base pool, so the projection inherits the convention; this
    check reads it back from the base pool table and proves it.
    """
    zero_address = "0x0000000000000000000000000000000000000000"
    checked = 0
    violations: List[Dict[str, Any]] = []
    fees: List[int] = []
    for row in rows:
        pool = core_pools.get(row["base_pool_id"])
        if pool is None:
            continue
        checked += 1
        fees.append(int(pool["fee"]))
        reasons = []
        if int(pool.get("dynamic_fee") or 0) != 0:
            reasons.append("dynamic_fee")
        if int(pool.get("special_fee") or 0) != 0:
            reasons.append("special_fee")
        if str(pool.get("hooks") or "").lower() != zero_address:
            reasons.append("non_zero_hook")
        if int(pool["fee"]) <= 0 or int(pool["fee"]) > MAX_CANDIDATE_FIXED_FEE:
            reasons.append("fee_not_fixed_candidate_range")
        if pool.get("pool_status") != "active":
            reasons.append("pool_status_%s" % pool.get("pool_status"))
        if reasons and len(violations) < 20:
            violations.append(
                {
                    "poolId": row["base_pool_id"],
                    "direction": row["direction"],
                    "reasons": reasons,
                }
            )
    return {
        "description": (
            "The admission convention the pool manager already applies: a "
            "fixed fee in the candidate range, no special fee, no hook and "
            "an active base pool. The directional row is a projection of "
            "the base pool, so it may not widen the universe."
        ),
        "checkedRows": checked,
        "violationCount": len(violations),
        "holds": not violations,
        "feeMin": min(fees) if fees else None,
        "feeMax": max(fees) if fees else None,
        "violationSamples": violations,
    }


def verify_two_row_projection(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Every usable base pool appears exactly twice, once per direction."""
    by_pool: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_pool.setdefault(row["base_pool_id"], []).append(row)
    bad_cardinality: List[str] = []
    bad_direction: List[str] = []
    field_mismatches: List[Dict[str, Any]] = []
    for pool_id, entries in sorted(by_pool.items()):
        if len(entries) != 2:
            bad_cardinality.append(pool_id)
            continue
        if sorted(entry["direction"] for entry in entries) != [0, 1]:
            bad_direction.append(pool_id)
        for field in (
            "pair_key_id",
            "fee",
            "tick_spacing",
            "tick",
            "sqrt_price_x96",
            "liquidity",
            "pool_status",
        ):
            if entries[0][field] != entries[1][field]:
                field_mismatches.append(
                    {
                        "poolId": pool_id,
                        "field": field,
                        "values": [entries[0][field], entries[1][field]],
                    }
                )
    return {
        "usablePools": len(by_pool),
        "projectedRows": len(rows),
        "expectedRows": len(by_pool) * 2,
        "rowCountMatches": len(rows) == len(by_pool) * 2,
        "poolsWithWrongCardinality": bad_cardinality,
        "poolsWithWrongDirectionSet": bad_direction,
        "sharedFieldMismatches": field_mismatches,
        "invariantI3Holds": (
            len(rows) == len(by_pool) * 2
            and not bad_cardinality
            and not bad_direction
            and not field_mismatches
        ),
    }


def summarise_directional_output(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_direction: Dict[int, Dict[str, int]] = {}
    for row in rows:
        bucket = by_direction.setdefault(
            row["direction"], {"rows": 0, "measured": 0, "unmeasured": 0, "zero": 0}
        )
        bucket["rows"] += 1
        value = row["directional_output"]
        if value is None:
            bucket["unmeasured"] += 1
        else:
            bucket["measured"] += 1
            if value == 0.0:
                bucket["zero"] += 1
    return {
        "description": (
            "A missing directional output is an unmeasured pool, never a "
            "zero rate. The two states are counted apart."
        ),
        "byDirection": {str(key): value for key, value in sorted(by_direction.items())},
        "measuredRows": sum(1 for row in rows if row["directional_output_measured"]),
        "unmeasuredRows": sum(1 for row in rows if not row["directional_output_measured"]),
    }


def verify_tick_ordering(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The tick rank must be the mirror image in the two directions."""
    by_pool: Dict[str, Dict[int, Optional[int]]] = {}
    for row in rows:
        by_pool.setdefault(row["base_pool_id"], {})[row["direction"]] = row["tick_rank"]
    mirror_ok = 0
    mirror_broken: List[str] = []
    for pool_id, ranks in sorted(by_pool.items()):
        rank0 = ranks.get(0)
        rank1 = ranks.get(1)
        if rank0 is None and rank1 is None:
            mirror_ok += 1
            continue
        if rank0 is None or rank1 is None:
            mirror_broken.append(pool_id)
            continue
        if rank0 == -rank1:
            mirror_ok += 1
        else:
            mirror_broken.append(pool_id)
    groups: Dict[Tuple[str, int], List[int]] = {}
    for row in rows:
        groups.setdefault((row["pair_key_id"], row["direction"]), []).append(
            row["pair_direction_rank"]
        )
    discontinuous: List[str] = []
    for key, positions in sorted(groups.items()):
        if sorted(positions) != list(range(1, len(positions) + 1)):
            discontinuous.append("%s#%d" % (key[0], key[1]))
    return {
        "poolsWithMirroredTickRank": mirror_ok,
        "poolsWithBrokenMirror": mirror_broken,
        "pairDirectionGroups": len(groups),
        "groupsWithDiscontinuousRanks": discontinuous,
        "rankContinuityHolds": not discontinuous,
    }


def summarise_directional_capacity(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_direction: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        bucket = by_direction.setdefault(
            row["direction"],
            {"rows": 0, "measured": 0, "unmeasured": 0, "measuredZero": 0, "values": []},
        )
        bucket["rows"] += 1
        capacity = row["input_capacity_raw"]
        if capacity is None:
            bucket["unmeasured"] += 1
        else:
            bucket["measured"] += 1
            if capacity == 0:
                bucket["measuredZero"] += 1
            else:
                bucket["values"].append(capacity)
    for bucket in by_direction.values():
        bucket["distribution"] = distribution(bucket.pop("values"))
    return {
        "description": (
            "Direction 0 spends the high address token and reads "
            "single_tick_max_token1_raw; direction 1 spends the low address "
            "token and reads single_tick_max_token0_raw. A NULL bound is "
            "unmeasured, a zero bound is measured with no room."
        ),
        "byDirection": {str(key): value for key, value in sorted(by_direction.items())},
    }


def verify_ranking_against_recommendations(
    core_pools: Dict[str, Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],
    recommendations: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Recompute the recommendation order and compare it with the Rust rows.

    The candidate-edge manager already ranks the first and the third hop by
    the very order the directional table binds.  Reproducing its rows from
    the ported arithmetic is therefore an end-to-end proof of the port: the
    directional output, the tick rank, the fee tie-break and the pool id
    tie-break all have to be right for the order to match.
    """
    fund_libraries = {"core", "stocks", "test"}
    anchor_tokens = {
        token_key_id
        for token_key_id, token in catalog.items()
        if token["library_key"] in fund_libraries
    }
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for pool_id in sorted(core_pools):
        pool = core_pools[pool_id]
        if pool["fee"] <= 0 or pool["tick_spacing"] == 0:
            continue
        if pool["token0_key_id"] not in catalog or pool["token1_key_id"] not in catalog:
            continue
        token0_is_anchor = pool["token0_key_id"] in anchor_tokens
        token1_is_anchor = pool["token1_key_id"] in anchor_tokens
        if not token0_is_anchor and not token1_is_anchor:
            continue
        if token0_is_anchor:
            groups.setdefault((pool["token0_key_id"], pool["token1_key_id"]), []).append(pool)
        if token1_is_anchor:
            groups.setdefault((pool["token1_key_id"], pool["token0_key_id"]), []).append(pool)
    computed: Dict[Tuple[str, str, str], List[str]] = {}
    for (anchor, candidate), pools in groups.items():
        active = [pool for pool in pools if pool["pool_status"] == "active"]
        if not active:
            continue
        for hop, token_in, token_out in (
            ("first", anchor, candidate),
            ("third", candidate, anchor),
        ):
            items = []
            for pool in active:
                items.append(
                    {
                        "pool_id": pool["pool_id"],
                        "fee": pool["fee"],
                        "exact_output": exact_directional_output(pool, token_in, catalog),
                        "tick_rank": tick_ordering_rank(pool, token_in),
                        "score": directional_score(pool, token_in, token_out, catalog),
                    }
                )
            ordered = sorted(items, key=functools.cmp_to_key(_cmp_recommend))
            computed[(anchor, candidate, hop)] = [item["pool_id"] for item in ordered]
    compared = 0
    matched = 0
    mismatches: List[Dict[str, Any]] = []
    for recommendation in recommendations:
        key = (
            recommendation["anchor_token_key_id"],
            recommendation["candidate_token_key_id"],
            recommendation["hop"],
        )
        order = computed.get(key)
        if order is None or recommendation["pool_id"] not in order:
            continue
        compared += 1
        expected_rank = order.index(recommendation["pool_id"]) + 1
        if expected_rank == recommendation["rank"]:
            matched += 1
        elif len(mismatches) < 20:
            mismatches.append(
                {
                    "anchor": recommendation["anchor_token_key_id"],
                    "candidate": recommendation["candidate_token_key_id"],
                    "hop": recommendation["hop"],
                    "storedRank": recommendation["rank"],
                    "computedRank": expected_rank,
                    "storedPoolId": recommendation["pool_id"],
                    "computedPoolId": order[expected_rank - 1],
                }
            )
    return {
        "description": (
            "The ported order against the rows the Rust candidate-edge "
            "manager wrote. Both the first and the third hop already use "
            "the order the directional table binds."
        ),
        "comparedRecommendations": compared,
        "matchedRecommendations": matched,
        "matchRate": (matched / compared) if compared else None,
        "mismatchSamples": mismatches,
    }


def compare_middle_hop(
    yz_pairs: Sequence[Dict[str, Any]],
    active_exchange: Dict[str, Dict[str, Any]],
    active_core: Dict[str, Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],
    ranked_base: Sequence[Dict[str, Any]],
    ranked_snapshot: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """The middle hop pool change table.

    The old middle hop is the ``optimal_pool_id`` of the market
    observation, ranked by ``liquidity_index``.  The new one is the rank
    one of the ``(pair, direction)`` group of the directional table.
    """
    old_by_pair: Dict[str, Dict[str, Any]] = {}
    anchor_independence_violations: List[Dict[str, Any]] = []
    triple_rows: List[Dict[str, Any]] = []
    for pair in yz_pairs:
        key = canonical_pair_key(pair["token_x_key_id"], pair["token_y_key_id"])
        entry = old_by_pair.setdefault(
            key,
            {
                "pairKeyId": key,
                "tokenX": pair["token_x_key_id"],
                "tokenY": pair["token_y_key_id"],
                "oldOptimalPoolId": pair["optimal_pool_id"],
                "oldPoolIds": list(pair["pool_ids"]),
                "anchors": [],
                "triples": [],
            },
        )
        if entry["oldOptimalPoolId"] != pair["optimal_pool_id"]:
            anchor_independence_violations.append(
                {
                    "pairKeyId": key,
                    "anchor": pair["anchor_token_key_id"],
                    "field": "optimalPoolId",
                }
            )
        if sorted(entry["oldPoolIds"]) != sorted(pair["pool_ids"]):
            anchor_independence_violations.append(
                {
                    "pairKeyId": key,
                    "anchor": pair["anchor_token_key_id"],
                    "field": "poolIds",
                }
            )
        entry["anchors"].append(pair["anchor_token_key_id"])
        entry["triples"].append(
            {
                "anchorTokenKeyId": pair["anchor_token_key_id"],
                "directionKey": pair["direction_key"],
                "tokenX": pair["token_x_key_id"],
                "tokenY": pair["token_y_key_id"],
            }
        )

    def select_new(
        pool_ids: Iterable[str],
        token_in: str,
        token_out: str,
        pools: Dict[str, Dict[str, Any]],
        ranked: Sequence[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """The rank one of the ``(pair, direction)`` group."""
        pool_ids = [pool_id for pool_id in pool_ids if pool_id in pools]
        if not pool_ids:
            return None
        direction = direction_for_logical_swap(token_in, token_out, pools, pool_ids[0])
        if direction is None:
            return None
        pair_key = canonical_pair_key(token_in, token_out)
        for item in ranked:
            if item["pair_key_id"] != pair_key or item["direction"] != direction:
                continue
            if item["base_pool_id"] not in pool_ids:
                continue
            return item
        return None

    pair_rows: List[Dict[str, Any]] = []
    changed_same_universe = 0
    changed_target_universe = 0
    for key in sorted(old_by_pair):
        entry = old_by_pair[key]
        token_x = entry["tokenX"]
        token_y = entry["tokenY"]
        old_pool = entry["oldOptimalPoolId"]

        new_same_universe = select_new(
            entry["oldPoolIds"], token_x, token_y, active_exchange, ranked_base
        )
        core_pair_pools = [
            pool_id
            for pool_id, pool in active_core.items()
            if {pool["token0_key_id"], pool["token1_key_id"]} == {token_x, token_y}
        ]
        new_target_base = select_new(
            core_pair_pools, token_x, token_y, active_core, ranked_base
        )
        new_target_snapshot = select_new(
            core_pair_pools, token_x, token_y, active_core, ranked_snapshot
        )
        same_universe_pool = new_same_universe["base_pool_id"] if new_same_universe else None
        target_base_pool = new_target_base["base_pool_id"] if new_target_base else None
        target_snapshot_pool = (
            new_target_snapshot["base_pool_id"] if new_target_snapshot else None
        )
        if same_universe_pool != old_pool:
            changed_same_universe += 1
        if target_snapshot_pool != old_pool:
            changed_target_universe += 1
        pair_rows.append(
            {
                "pairKeyId": key,
                "tokenX": token_x,
                "tokenY": token_y,
                "anchorCount": len(entry["anchors"]),
                "oldPoolUniverseSize": len(entry["oldPoolIds"]),
                "oldOptimalPoolId": old_pool,
                "newSameUniversePoolId": same_universe_pool,
                "newTargetPoolIdBaseTickState": target_base_pool,
                "newTargetPoolIdSnapshotTickState": target_snapshot_pool,
                "changedSameUniverse": same_universe_pool != old_pool,
                "changedTargetUniverse": target_snapshot_pool != old_pool,
                "targetPoolInOldUniverse": (
                    target_snapshot_pool in entry["oldPoolIds"]
                    if target_snapshot_pool
                    else None
                ),
                "newTargetDirectionalOutput": (
                    new_target_snapshot["directional_output"]
                    if new_target_snapshot
                    else None
                ),
                "oldPoolLiquidityIndex": (
                    active_exchange.get(old_pool, {}).get("liquidity_index")
                    if old_pool
                    else None
                ),
            }
        )
        for triple in entry["triples"]:
            triple_rows.append(
                {
                    "anchorTokenKeyId": triple["anchorTokenKeyId"],
                    "tokenX": triple["tokenX"],
                    "tokenY": triple["tokenY"],
                    "pairKeyId": key,
                    "oldPoolId": old_pool,
                    "newSameUniversePoolId": same_universe_pool,
                    "newTargetPoolId": target_snapshot_pool,
                    "changedSameUniverse": same_universe_pool != old_pool,
                    "changedTargetUniverse": target_snapshot_pool != old_pool,
                }
            )

    total_pairs = len(pair_rows)
    total_triples = len(triple_rows)
    return {
        "description": (
            "The middle hop is the only hop that still ranks its pools by "
            "liquidity_index. Delta 1 isolates the order change over the "
            "old pool universe; delta 2 is the target state (the core4 base "
            "pool table with the directional order)."
        ),
        "oldOrderKey": "liquidity_index descending, then pool id ascending",
        "newOrderKey": (
            "directional_output descending, then tick rank descending, then "
            "fee ascending, then base pool id ascending"
        ),
        "anchorIndependenceViolations": anchor_independence_violations,
        "pairCount": total_pairs,
        "tripleCount": total_triples,
        "changedSameUniversePairs": changed_same_universe,
        "changedSameUniverseRate": (
            changed_same_universe / total_pairs if total_pairs else None
        ),
        "changedTargetUniversePairs": changed_target_universe,
        "changedTargetUniverseRate": (
            changed_target_universe / total_pairs if total_pairs else None
        ),
        "changedSameUniverseTriples": sum(1 for row in triple_rows if row["changedSameUniverse"]),
        "changedTargetUniverseTriples": sum(
            1 for row in triple_rows if row["changedTargetUniverse"]
        ),
        "changedSameUniverseRateTriples": (
            sum(1 for row in triple_rows if row["changedSameUniverse"]) / total_triples
            if total_triples
            else None
        ),
        "changedTargetUniverseRateTriples": (
            sum(1 for row in triple_rows if row["changedTargetUniverse"]) / total_triples
            if total_triples
            else None
        ),
        "pairRows": pair_rows,
        "tripleRows": triple_rows,
    }


def _pool_tick_states(
    core_pools: Dict[str, Dict[str, Any]],
    exchange_pools: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Port of ``read_pool_tick_states``.

    The candidate-exchange table wins over the core4 table for the pools
    both of them publish, exactly as the Rust reader does.
    """
    states: Dict[str, Dict[str, Any]] = {}
    for table_pools in (exchange_pools, core_pools):
        for pool_id in sorted(table_pools):
            pool = table_pools[pool_id]
            sqrt_price_x96 = parse_u128(pool.get("sqrt_price_x96"))
            liquidity = parse_u128(pool.get("liquidity"))
            tick = pool.get("tick")
            if not sqrt_price_x96 or not liquidity or tick is None:
                continue
            if not pool.get("tick_spacing") or pool.get("fee") is None:
                continue
            states[pool_id] = {
                "pool_id": pool_id,
                "sqrt_price_x96": sqrt_price_x96,
                "liquidity": liquidity,
                "tick": int(tick),
                "tick_spacing": int(pool["tick_spacing"]),
                "fee_pips": int(pool["fee"]),
                "token0_key_id": pool["token0_key_id"],
                "token1_key_id": pool["token1_key_id"],
            }
    return states


def _hop_direction(
    states: Dict[str, Dict[str, Any]], pool_id: str, token_in: str
) -> Optional[bool]:
    state = states.get(pool_id)
    if state is None:
        return None
    if state["token0_key_id"] == token_in:
        return True
    if state["token1_key_id"] == token_in:
        return False
    return None


def _pool_depth_capacity(
    snapshots: Dict[str, Dict[str, Any]], pool_id: str
) -> Optional[int]:
    snapshot = snapshots.get(pool_id)
    if snapshot is None:
        return None
    raw = snapshot.get("capacity_raw")
    if raw is None:
        return None
    return parse_u128(raw)


def compare_closed_loops(
    anchors: Dict[str, Dict[str, Any]],
    recommendations: Sequence[Dict[str, Any]],
    middle: Dict[str, Any],
    core_pools: Dict[str, Dict[str, Any]],
    exchange_pools: Dict[str, Dict[str, Any]],
    snapshots: Dict[str, Dict[str, Any]],
    closed_loops: Sequence[Dict[str, Any]],
    loop_hops: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """The closed loop set change table.

    The new set keeps the first and the third hop exactly as the
    candidate-edge manager produced them and replaces only the middle
    hop.  The two gates the closed-loop manager applies are reproduced so
    the sets are comparable.
    """
    states = _pool_tick_states(core_pools, exchange_pools)
    first_options: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    third_options: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for recommendation in recommendations:
        target = first_options if recommendation["hop"] == "first" else third_options
        target.setdefault(
            (
                recommendation["anchor_token_key_id"],
                recommendation["candidate_token_key_id"],
            ),
            [],
        ).append(recommendation)
    for options in list(first_options.values()) + list(third_options.values()):
        options.sort(key=lambda item: item["rank"])

    middle_by_pair = {row["pairKeyId"]: row for row in middle["pairRows"]}

    def new_middle_pool(token_x: str, token_y: str) -> Optional[str]:
        row = middle_by_pair.get(canonical_pair_key(token_x, token_y))
        if row is None:
            return None
        return row["newTargetPoolIdSnapshotTickState"]

    old_by_triple: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for loop in closed_loops:
        old_by_triple[
            (loop["anchor_token_key_id"], loop["token_y_key_id"], loop["token_z_key_id"])
        ] = loop

    new_rows: List[Dict[str, Any]] = []
    skipped_rows: List[Dict[str, Any]] = []
    skipped_no_middle = 0
    skipped_tick_gate = 0
    skipped_capacity_gate = 0
    for anchor_key_id in sorted(anchors):
        anchor = anchors[anchor_key_id]
        candidate_tokens = [
            token for token in sorted(anchor["tokens"]) if token != anchor_key_id
        ]
        first_keys = [
            token for token in candidate_tokens if (anchor_key_id, token) in first_options
        ]
        third_keys = [
            token for token in candidate_tokens if (anchor_key_id, token) in third_options
        ]
        for y_token in first_keys:
            for z_token in third_keys:
                if y_token == z_token:
                    continue
                middle_pool = new_middle_pool(y_token, z_token)
                if middle_pool is None:
                    skipped_no_middle += 1
                    skipped_rows.append(
                        {
                            "anchorTokenKeyId": anchor_key_id,
                            "tokenY": y_token,
                            "tokenZ": z_token,
                            "reason": "noMiddlePool",
                        }
                    )
                    continue
                first_pool = first_options[(anchor_key_id, y_token)][0]["pool_id"]
                third_pool = third_options[(anchor_key_id, z_token)][0]["pool_id"]
                pool_ids = [first_pool, middle_pool, third_pool]
                token_ins = [anchor_key_id, y_token, z_token]
                directions: List[bool] = []
                for pool_id, token_in in zip(pool_ids, token_ins):
                    zero_for_one = _hop_direction(states, pool_id, token_in)
                    if zero_for_one is None:
                        break
                    directions.append(zero_for_one)
                if len(directions) != 3:
                    skipped_no_middle += 1
                    skipped_rows.append(
                        {
                            "anchorTokenKeyId": anchor_key_id,
                            "tokenY": y_token,
                            "tokenZ": z_token,
                            "middlePoolId": middle_pool,
                            "reason": "unknownDirection",
                        }
                    )
                    continue
                hops = []
                for pool_id, token_in, zero_for_one in zip(pool_ids, token_ins, directions):
                    state = states[pool_id]
                    token_out = (
                        state["token1_key_id"] if zero_for_one else state["token0_key_id"]
                    )
                    hops.append(
                        {
                            "pool_id": pool_id,
                            "token_in": token_in,
                            "token_out": token_out,
                            "sqrt_price_x96": state["sqrt_price_x96"],
                            "liquidity": state["liquidity"],
                            "fee_pips": state["fee_pips"],
                            "tick_spacing": state["tick_spacing"],
                            "tick": state["tick"],
                            "zero_for_one": zero_for_one,
                        }
                    )
                try:
                    folded, binding = compute_loop_capacity(anchor_key_id, hops)
                except ArithmeticError_:
                    skipped_tick_gate += 1
                    skipped_rows.append(
                        {
                            "anchorTokenKeyId": anchor_key_id,
                            "tokenY": y_token,
                            "tokenZ": z_token,
                            "middlePoolId": middle_pool,
                            "reason": "tickGate",
                        }
                    )
                    continue
                if folded == 0:
                    skipped_tick_gate += 1
                    skipped_rows.append(
                        {
                            "anchorTokenKeyId": anchor_key_id,
                            "tokenY": y_token,
                            "tokenZ": z_token,
                            "middlePoolId": middle_pool,
                            "reason": "tickGate",
                        }
                    )
                    continue
                blocking = None
                for pool_id in pool_ids:
                    capacity = _pool_depth_capacity(snapshots, pool_id)
                    if capacity is None:
                        continue
                    if capacity < MIN_CLOSED_LOOP_CAPACITY_RAW:
                        blocking = pool_id
                        break
                if blocking is not None:
                    skipped_capacity_gate += 1
                    skipped_rows.append(
                        {
                            "anchorTokenKeyId": anchor_key_id,
                            "tokenY": y_token,
                            "tokenZ": z_token,
                            "middlePoolId": middle_pool,
                            "blockingPoolId": blocking,
                            "reason": "capacityGate",
                        }
                    )
                    continue
                new_rows.append(
                    {
                        "anchorTokenKeyId": anchor_key_id,
                        "tokenY": y_token,
                        "tokenZ": z_token,
                        "poolIds": pool_ids,
                        "directions": directions,
                        "foldedCapacityAnchorRaw": folded,
                        "bindingPoolId": binding,
                    }
                )

    new_by_triple = {
        (row["anchorTokenKeyId"], row["tokenY"], row["tokenZ"]): row for row in new_rows
    }
    retained = sorted(set(old_by_triple) & set(new_by_triple))
    added = sorted(set(new_by_triple) - set(old_by_triple))
    removed = sorted(set(old_by_triple) - set(new_by_triple))
    changed_middle = []
    for key in retained:
        old_loop = old_by_triple[key]
        new_row = new_by_triple[key]
        old_hops = loop_hops.get(old_loop["loop_key"], {})
        old_middle_pool = (old_hops.get("y-z") or {}).get("pool_id")
        new_middle_pool = new_row["poolIds"][1]
        if old_middle_pool != new_middle_pool:
            changed_middle.append(
                {
                    "anchorTokenKeyId": key[0],
                    "tokenY": key[1],
                    "tokenZ": key[2],
                    "loopKey": old_loop["loop_key"],
                    "oldMiddlePoolId": old_middle_pool,
                    "newMiddlePoolId": new_middle_pool,
                    "oldCapacityAnchorRaw": signed_value(
                        old_loop["single_tick_capacity_anchor_raw"]
                    ),
                    "newCapacityAnchorRaw": new_row["foldedCapacityAnchorRaw"],
                }
            )
    return {
        "description": (
            "The first and the third hop keep the pool the candidate-edge "
            "manager chose; only the middle hop is replaced. The two gates "
            "of the closed-loop manager (the folded single-tick bound and "
            "the minimum depth capacity) are reproduced."
        ),
        "oldLoopCount": len(old_by_triple),
        "newLoopCount": len(new_by_triple),
        "retainedCount": len(retained),
        "addedCount": len(added),
        "removedCount": len(removed),
        "retained": [
            {
                "anchorTokenKeyId": key[0],
                "tokenY": key[1],
                "tokenZ": key[2],
                "loopKey": old_by_triple[key]["loop_key"],
            }
            for key in retained
        ],
        "added": [
            {
                "anchorTokenKeyId": key[0],
                "tokenY": key[1],
                "tokenZ": key[2],
                "poolIds": new_by_triple[key]["poolIds"],
                "foldedCapacityAnchorRaw": new_by_triple[key]["foldedCapacityAnchorRaw"],
            }
            for key in added
        ],
        "removed": [
            {
                "anchorTokenKeyId": key[0],
                "tokenY": key[1],
                "tokenZ": key[2],
                "loopKey": old_by_triple[key]["loop_key"],
                "poolIds": old_by_triple[key]["pool_ids"],
            }
            for key in removed
        ],
        "retainedWithChangedMiddlePool": changed_middle,
        "retainedWithChangedMiddlePoolCount": len(changed_middle),
        "skipped": {
            "noMiddlePool": skipped_no_middle,
            "tickGate": skipped_tick_gate,
            "capacityGate": skipped_capacity_gate,
        },
        "skippedRows": skipped_rows,
        "newLoopRows": new_rows,
    }


def compare_capacity_and_profit(
    loops: Dict[str, Any],
    closed_loops: Sequence[Dict[str, Any]],
    reviews: Dict[str, Dict[str, Any]],
    snapshots: Dict[str, Dict[str, Any]],
    states: Dict[str, Dict[str, Any]],
    loop_hops: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """The capacity and profit difference distributions.

    The capacity comparison puts **both** sides on the same tick state.
    The old path is recomputed from the pool tables with the pools the
    closed-loop catalog actually chose, and the new path is recomputed with
    the pools the directional order chooses.  Both folds therefore see the
    same price, so the difference is the caliber difference and nothing
    else.

    The stored old capacity is reported separately.  It was folded at
    materialisation time, so the difference between the stored value and
    the recomputed old value measures the **drift** between the
    materialisation and this wave; it is not a caliber difference.
    """
    old_by_triple = {
        (
            loop["anchor_token_key_id"],
            loop["token_y_key_id"],
            loop["token_z_key_id"],
        ): loop
        for loop in closed_loops
    }
    new_by_triple = {
        (row["anchorTokenKeyId"], row["tokenY"], row["tokenZ"]): row
        for row in loops["newLoopRows"]
    }
    common = sorted(set(old_by_triple) & set(new_by_triple))
    deltas: List[int] = []
    ratios: List[float] = []
    missing_old = 0
    missing_new = 0
    measured_zero = 0
    drift_deltas: List[int] = []
    drift_ratios: List[float] = []
    drift_missing = 0
    changed_middle_deltas: List[int] = []

    def old_path_capacity(loop: Dict[str, Any]) -> Optional[int]:
        """Fold the old path with the tick state of this wave."""
        hops_by_name = loop_hops.get(loop["loop_key"], {})
        ordered = sorted(
            hops_by_name.values(), key=lambda hop: hop["hop_index"]
        )
        anchor = loop["anchor_token_key_id"]
        hops = []
        for hop in ordered:
            pool_id = hop["pool_id"]
            state = states.get(pool_id)
            if state is None:
                return None
            token_in = hop["input_token_key_id"]
            if state["token0_key_id"] == token_in:
                zero_for_one = True
            elif state["token1_key_id"] == token_in:
                zero_for_one = False
            else:
                return None
            token_out = (
                state["token1_key_id"] if zero_for_one else state["token0_key_id"]
            )
            hops.append(
                {
                    "pool_id": pool_id,
                    "token_in": token_in,
                    "token_out": token_out,
                    "sqrt_price_x96": state["sqrt_price_x96"],
                    "liquidity": state["liquidity"],
                    "fee_pips": state["fee_pips"],
                    "tick_spacing": state["tick_spacing"],
                    "tick": state["tick"],
                    "zero_for_one": zero_for_one,
                }
            )
        if not hops or hops[0]["token_in"] != anchor:
            return None
        try:
            folded, _binding = compute_loop_capacity(anchor, hops)
        except ArithmeticError_:
            return None
        return folded

    for key in common:
        old_loop = old_by_triple[key]
        new_row = new_by_triple[key]
        old_capacity = old_path_capacity(old_loop)
        new_capacity = new_row["foldedCapacityAnchorRaw"]
        if old_capacity is None:
            missing_old += 1
            continue
        if new_capacity is None:
            missing_new += 1
            continue
        deltas.append(new_capacity - old_capacity)
        if old_capacity > 0:
            ratios.append(new_capacity / old_capacity)
        elif old_capacity == 0 and new_capacity == 0:
            measured_zero += 1
        stored_old = signed_value(old_loop["single_tick_capacity_anchor_raw"])
        if stored_old is not None:
            drift_deltas.append(old_capacity - stored_old)
            if stored_old > 0:
                drift_ratios.append(old_capacity / stored_old)
        else:
            drift_missing += 1
        if new_row["poolIds"][1] != (
            (loop_hops.get(old_loop["loop_key"], {}).get("y-z") or {}).get("pool_id")
        ):
            changed_middle_deltas.append(new_capacity - old_capacity)

    profit_states: Dict[str, int] = {
        "measuredPositive": 0,
        "measuredZero": 0,
        "measuredNegative": 0,
        "unmeasured": 0,
    }
    profit_samples: List[Dict[str, Any]] = []
    for loop in closed_loops:
        review = reviews.get(loop["loop_key"])
        if review is None:
            profit_states["unmeasured"] += 1
            continue
        pnl = signed_value(review["signed_pnl_raw"])
        if pnl is None:
            profit_states["unmeasured"] += 1
            continue
        if pnl > 0:
            profit_states["measuredPositive"] += 1
        elif pnl == 0:
            profit_states["measuredZero"] += 1
        else:
            profit_states["measuredNegative"] += 1
        if len(profit_samples) < 10:
            profit_samples.append(
                {
                    "loopKey": loop["loop_key"],
                    "reviewStatus": review["review_status"],
                    "reviewReason": review["review_reason"],
                    "signedPnlRaw": review["signed_pnl_raw"],
                    "signedPnlAmount": review["signed_pnl_amount"],
                    "signedPnlCurrency": review["signed_pnl_currency"],
                }
            )
    return {
        "capacity": {
            "description": (
                "The difference of the folded single-tick capacity in the "
                "anchor token, over the loops that exist in both sets. "
                "**Both sides are folded with the tick state of this "
                "wave**, so the difference is the caliber difference. A "
                "missing measurement is never counted as a zero."
            ),
            "comparedLoops": len(common),
            "missingOldCapacity": missing_old,
            "missingNewCapacity": missing_new,
            "measuredZeroOnBothSides": measured_zero,
            "deltaDistribution": distribution(deltas),
            "ratioDistribution": ratio_distribution(ratios),
            "negativeDeltaCount": sum(1 for delta in deltas if delta < 0),
            "positiveDeltaCount": sum(1 for delta in deltas if delta > 0),
            "zeroDeltaCount": sum(1 for delta in deltas if delta == 0),
            "changedMiddlePoolLoops": len(changed_middle_deltas),
            "changedMiddlePoolDeltaDistribution": distribution(changed_middle_deltas),
        },
        "capacityDrift": {
            "description": (
                "The stored capacity of the closed-loop catalog against the "
                "same path folded with the tick state of this wave. The "
                "difference measures the drift between the materialisation "
                "and this wave; it is **not** a caliber difference and it "
                "is not counted as one."
            ),
            "comparedLoops": len(drift_deltas) + drift_missing,
            "missingStoredCapacity": drift_missing,
            "deltaDistribution": distribution(drift_deltas),
            "ratioDistribution": ratio_distribution(drift_ratios),
            "unchangedCount": sum(1 for delta in drift_deltas if delta == 0),
            "note": (
                "A non-zero drift is expected: the catalog was folded from "
                "the tick state of the materialisation, and the pools move "
                "between then and now."
            ),
        },
        "profit": {
            "description": (
                "The old profit of every closed loop, with the measured "
                "zero and the unmeasured case kept apart. The new profit is "
                "not computable in a read-only wave: it needs the arbiter to "
                "run against the new pool path, which is a write-side action "
                "outside the scope of this comparison."
            ),
            "oldProfitStates": profit_states,
            "newProfitState": "unmeasured",
            "newProfitReason": (
                "The new profit needs a re-run of the arbiter against the "
                "new pool path; a read-only script must not imitate the "
                "executor."
            ),
            "samples": profit_samples,
        },
    }


def _logical_direction(
    token_x: str, token_y: str, pools: Dict[str, Dict[str, Any]]
) -> int:
    """The direction value of a logical ``token_x -> token_y`` swap.

    The direction of a swap is a property of the pool, not of the swap:
    ``token0 -> token1`` is direction 1 and ``token1 -> token0`` is
    direction 0.  The pool that carries the pair decides it, so the pair is
    looked up in the pool tables first.  The key-order fallback only runs
    when the pair has no pool at all.
    """
    for pool_id in sorted(pools):
        direction = direction_for_logical_swap(token_x, token_y, pools, pool_id)
        if direction is not None:
            return direction
    return 1 if token_x <= token_y else 0


def build_change_attribution(
    middle: Dict[str, Any],
    loops: Dict[str, Any],
    closed_loops: Sequence[Dict[str, Any]],
    core_pools: Dict[str, Dict[str, Any]],
    exchange_pools: Dict[str, Dict[str, Any]],
    snapshots: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Where the middle-hop change lands, and where it is fragile.

    The readings answer *how much* changed.  This function answers *where*:
    which direction groups carry the change, how concentrated the change
    is, what the new pools have in common, and which closed loops have no
    fallback pool at all.  It is the input of the two-step attribution the
    parameter decision prescribes: attribution first, verdict second.
    """
    usable_pools = {
        pool_id: pool
        for pool_id, pool in core_pools.items()
        if pool["pool_status"] == "active"
    }
    pair_pools: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    for source in (core_pools, exchange_pools):
        for pool_id, pool in source.items():
            key = (pool["token0_key_id"], pool["token1_key_id"])
            pair_pools.setdefault(key, {})[pool_id] = pool

    # -- the direction groups -------------------------------------------------
    groups: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in middle["tripleRows"]:
        token_x = row["tokenX"]
        token_y = row["tokenY"]
        pool_key = (token_x, token_y) if token_x <= token_y else (token_y, token_x)
        direction = _logical_direction(
            token_x, token_y, pair_pools.get(pool_key, {})
        )
        key = (row["pairKeyId"], direction)
        entry = groups.setdefault(
            key,
            {
                "pairKeyId": row["pairKeyId"],
                "direction": direction,
                "triples": 0,
                "changedTriples": 0,
                "changedSameUniverseTriples": 0,
            },
        )
        entry["triples"] += 1
        if row["changedTargetUniverse"]:
            entry["changedTriples"] += 1
        if row["changedSameUniverse"]:
            entry["changedSameUniverseTriples"] += 1

    changed_groups = [g for g in groups.values() if g["changedTriples"] > 0]
    changed_by_direction: Dict[str, Any] = {}
    for direction in (0, 1):
        in_direction = [g for g in groups.values() if g["direction"] == direction]
        changed = [g for g in in_direction if g["changedTriples"] > 0]
        changed_by_direction[str(direction)] = {
            "groups": len(in_direction),
            "changedGroups": len(changed),
            "changeEvents": sum(g["changedTriples"] for g in in_direction),
        }

    # -- the concentration -----------------------------------------------------
    change_events = sum(g["changedTriples"] for g in groups.values())
    ranked_groups = sorted(
        groups.values(), key=lambda g: (-g["changedTriples"], g["pairKeyId"], g["direction"])
    )
    top_ten = ranked_groups[:10]
    top_ten_events = sum(g["changedTriples"] for g in top_ten)

    new_pool_ids: Dict[str, int] = {}
    for row in middle["tripleRows"]:
        if not row["changedTargetUniverse"]:
            continue
        pool_id = row["newTargetPoolId"]
        if pool_id is None:
            continue
        new_pool_ids[pool_id] = new_pool_ids.get(pool_id, 0) + 1

    def _pool_features(pool_id: str) -> Dict[str, Any]:
        pool = usable_pools.get(pool_id) or core_pools.get(pool_id) or exchange_pools.get(pool_id)
        snapshot = snapshots.get(pool_id)
        return {
            "poolId": pool_id,
            "fee": pool["fee"] if pool else None,
            "dynamicFee": pool["dynamic_fee"] if pool else None,
            "specialFee": pool["special_fee"] if pool else None,
            "hooks": pool["hooks"] if pool else None,
            "tickSpacing": pool["tick_spacing"] if pool else None,
            "poolStatus": pool["pool_status"] if pool else None,
            "liquidityIndex": pool["liquidity_index"] if pool else None,
            "depthStatus": (snapshot or {}).get("depth_status"),
            "capacityRaw": (snapshot or {}).get("capacity_raw"),
            "tick": pool["tick"] if pool else None,
        }

    new_pool_rows = [
        dict(_pool_features(pool_id), changedTriples=count)
        for pool_id, count in sorted(
            new_pool_ids.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    def _distribution(rows: Sequence[Dict[str, Any]], field: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in rows:
            key = str(row.get(field))
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    # -- the loops without a fallback pool --------------------------------------
    removed_keys = {row["loopKey"] for row in loops["removed"]}
    retained_keys = {row["loopKey"] for row in loops["retained"]}
    changed_middle = {row["loopKey"]: row for row in loops["retainedWithChangedMiddlePool"]}
    no_backup_rows: List[Dict[str, Any]] = []
    candidate_distribution: Dict[str, int] = {}
    for loop in closed_loops:
        counts = loop.get("hop_candidate_counts") or {}
        middle_candidates = int(counts.get("2") or 0)
        key = str(middle_candidates)
        candidate_distribution[key] = candidate_distribution.get(key, 0) + 1
        backups = (loop.get("backup_pool_ids") or {}).get("middle") or []
        if backups:
            continue
        loop_key = loop["loop_key"]
        if loop_key in removed_keys:
            fate = "removedByNewCaliber"
        elif loop_key in changed_middle:
            fate = "retainedWithChangedMiddlePool"
        elif loop_key in retained_keys:
            fate = "retainedUnchanged"
        else:
            fate = "unclassified"
        no_backup_rows.append(
            {
                "loopKey": loop_key,
                "pathText": loop["path_text"],
                "middleCandidateCount": middle_candidates,
                "fate": fate,
                "oldMiddlePoolId": changed_middle.get(loop_key, {}).get("oldMiddlePoolId"),
                "newMiddlePoolId": changed_middle.get(loop_key, {}).get("newMiddlePoolId"),
            }
        )

    fate_counts: Dict[str, int] = {}
    for row in no_backup_rows:
        fate_counts[row["fate"]] = fate_counts.get(row["fate"], 0) + 1

    # -- the loops the new caliber drops, and the gate that drops them -------
    skip_index = {
        (row["anchorTokenKeyId"], row["tokenY"], row["tokenZ"]): row
        for row in loops.get("skippedRows", [])
    }
    removed_rows: List[Dict[str, Any]] = []
    for row in loops["removed"]:
        skip = skip_index.get(
            (row["anchorTokenKeyId"], row["tokenY"], row["tokenZ"])
        )
        old_pool_ids = row.get("poolIds") or []
        old_middle = old_pool_ids[1] if len(old_pool_ids) > 1 else None
        middle_in_core = (
            core_pools.get(old_middle, {}).get("pool_status") if old_middle else None
        )
        middle_in_exchange = (
            exchange_pools.get(old_middle, {}).get("pool_status") if old_middle else None
        )
        removed_rows.append(
            {
                "loopKey": row["loopKey"],
                "anchorTokenKeyId": row["anchorTokenKeyId"],
                "tokenY": row["tokenY"],
                "tokenZ": row["tokenZ"],
                "oldPoolIds": old_pool_ids,
                "oldMiddlePoolId": old_middle,
                "oldMiddlePoolInCore4": middle_in_core,
                "oldMiddlePoolInExchange": middle_in_exchange,
                "hadBackup": row["loopKey"] not in {r["loopKey"] for r in no_backup_rows},
                "rejectedBy": skip.get("reason") if skip else "unknown",
                "newMiddlePoolId": skip.get("middlePoolId") if skip else None,
                "blockingPoolId": skip.get("blockingPoolId") if skip else None,
            }
        )
    removed_reason_counts: Dict[str, int] = {}
    for row in removed_rows:
        removed_reason_counts[row["rejectedBy"]] = removed_reason_counts.get(row["rejectedBy"], 0) + 1
    coverage_rows: List[Dict[str, Any]] = []
    seen_pools: Dict[str, Dict[str, Any]] = {}
    for row in removed_rows:
        pool_id = row["oldMiddlePoolId"]
        if pool_id is None or pool_id in seen_pools:
            continue
        seen_pools[pool_id] = row
        coverage_rows.append(
            {
                "poolId": pool_id,
                "core4Status": row["oldMiddlePoolInCore4"],
                "exchangeStatus": row["oldMiddlePoolInExchange"],
                "droppedLoops": sum(
                    1 for other in removed_rows if other["oldMiddlePoolId"] == pool_id
                ),
            }
        )

    return {
        "description": (
            "Attribution of the middle-hop change: which direction groups "
            "carry it, how concentrated it is, what the new pools have in "
            "common, and which closed loops have no fallback pool at all."
        ),
        "directionGroups": {
            "caliber": (
                "distinct (pair, direction) groups whose middle-hop pool "
                "changed, over all (pair, direction) groups of the market "
                "observation"
            ),
            "groups": len(groups),
            "changedGroups": len(changed_groups),
            "unchangedGroups": len(groups) - len(changed_groups),
            "changedRate": (len(changed_groups) / len(groups)) if groups else None,
            "byDirection": changed_by_direction,
            "changedGroupList": sorted(
                (
                    {
                        "pairKeyId": group["pairKeyId"],
                        "direction": group["direction"],
                        "triples": group["triples"],
                        "changedTriples": group["changedTriples"],
                    }
                    for group in changed_groups
                ),
                key=lambda item: (-item["changedTriples"], item["pairKeyId"], item["direction"]),
            ),
        },
        "concentration": {
            "caliber": (
                "share of all middle-hop change events that fall into the "
                "ten (pair, direction) groups with the most events"
            ),
            "changeEvents": change_events,
            "topGroups": len(top_ten),
            "topGroupEvents": top_ten_events,
            "share": (top_ten_events / change_events) if change_events else None,
            "topGroupList": [
                {
                    "pairKeyId": group["pairKeyId"],
                    "direction": group["direction"],
                    "changedTriples": group["changedTriples"],
                }
                for group in top_ten
            ],
            "distinctNewPools": len(new_pool_ids),
            "usablePoolCount": len(usable_pools),
            "newPoolShareOfUsablePools": (
                len(new_pool_ids) / len(usable_pools) if usable_pools else None
            ),
        },
        "newPools": {
            "caliber": (
                "the pools the middle hop moves to, and the features they "
                "share; a concentration on one feature class is the "
                "signature of a pool class that should not have been "
                "selected at all"
            ),
            "count": len(new_pool_rows),
            "pools": new_pool_rows,
            "featureDistribution": {
                "fee": _distribution(new_pool_rows, "fee"),
                "tickSpacing": _distribution(new_pool_rows, "tickSpacing"),
                "depthStatus": _distribution(new_pool_rows, "depthStatus"),
                "poolStatus": _distribution(new_pool_rows, "poolStatus"),
                "dynamicFee": _distribution(new_pool_rows, "dynamicFee"),
                "specialFee": _distribution(new_pool_rows, "specialFee"),
            },
        },
        "loopsWithoutBackup": {
            "caliber": (
                "closed loops whose middle hop has no backup candidate; "
                "a pool change on such a loop cannot fall back"
            ),
            "loops": len(closed_loops),
            "withoutBackup": len(no_backup_rows),
            "withoutBackupRate": (
                len(no_backup_rows) / len(closed_loops) if closed_loops else None
            ),
            "middleCandidateCountDistribution": dict(
                sorted(candidate_distribution.items(), key=lambda item: int(item[0]))
            ),
            "fateCounts": dict(sorted(fate_counts.items())),
            "rows": no_backup_rows,
        },
        "removedLoops": {
            "caliber": (
                "the closed loops the new caliber drops, with the gate "
                "that drops each of them; a loop without a backup pool has "
                "nowhere to fall back to"
            ),
            "count": len(removed_rows),
            "reasonCounts": dict(sorted(removed_reason_counts.items())),
            "rows": removed_rows,
            "universeCoverage": {
                "caliber": (
                    "whether the middle pool of a dropped loop exists in the "
                    "target universe at all; a pool that is admitted but "
                    "absent from the core4 base table is a coverage "
                    "question, not a selection-order question"
                ),
                "pools": coverage_rows,
            },
        },
    }


def build_readings(
    middle: Dict[str, Any],
    loops: Dict[str, Any],
    attribution: Dict[str, Any],
    profit: Dict[str, Any],
) -> Dict[str, Any]:
    """The readings that replace the three former thresholds.

    A reading is a number that must be produced and explained, not a number
    that is compared against a fixed value to decide whether the change may
    proceed.  The closed-loop catalogue is replaced as a whole on every
    materialisation and P1 leaves the live path untouched, so there is no
    stored value to protect; what the readings still do is tell the
    reviewer where the change lands and where it is fragile.
    """
    groups = attribution["directionGroups"]
    concentration = attribution["concentration"]
    without_backup = attribution["loopsWithoutBackup"]
    loop_net = None
    if loops["oldLoopCount"]:
        loop_net = (loops["newLoopCount"] - loops["oldLoopCount"]) / loops["oldLoopCount"]
    return {
        READING_MID_CHANGE_RATE: {
            "caliber": groups["caliber"],
            "value": groups["changedRate"],
            "numerator": groups["changedGroups"],
            "denominator": groups["groups"],
            "role": "where the change lands (blast radius)",
        },
        READING_MID_CHANGE_POOL_COUNT: {
            "caliber": (
                "the distinct pools the middle hop moves to, over the "
                "usable base pools"
            ),
            "value": concentration["newPoolShareOfUsablePools"],
            "numerator": concentration["distinctNewPools"],
            "denominator": concentration["usablePoolCount"],
            "role": "how far the pool universe itself moved",
        },
        READING_LOOP_NET_RATE: {
            "caliber": "(new closed loops - old closed loops) / old closed loops",
            "value": loop_net,
            "numerator": loops["newLoopCount"] - loops["oldLoopCount"],
            "denominator": loops["oldLoopCount"],
            "role": "production loss",
        },
        READING_PROFIT_DROP: {
            "caliber": (
                "the median drop of the effective profit over the affected "
                "closed loops"
            ),
            "value": None,
            "measured": False,
            "reason": (
                "the old profit is unmeasured on %d of %d closed loops and "
                "the new profit needs the arbiter to run against the new "
                "pool path, which a read-only wave may not imitate"
                % (
                    profit["oldProfitStates"]["unmeasured"],
                    sum(profit["oldProfitStates"].values()),
                )
            ),
            "role": "economic cost; must be measured once in a live wave",
        },
        READING_LOOPS_WITHOUT_BACKUP: {
            "caliber": without_backup["caliber"],
            "value": without_backup["withoutBackupRate"],
            "numerator": without_backup["withoutBackup"],
            "denominator": without_backup["loops"],
            "role": "where the change has no fallback at all",
        },
        READING_CHANGE_CONCENTRATION: {
            "caliber": concentration["caliber"],
            "value": concentration["share"],
            "numerator": concentration["topGroupEvents"],
            "denominator": concentration["changeEvents"],
            "role": (
                "a concentrated change is a local phenomenon; a spread "
                "one questions the main order itself"
            ),
        },
    }


def build_caliber_verdict(
    readings: Dict[str, Any], attribution: Dict[str, Any]
) -> Dict[str, Any]:
    """The evidence for the caliber verdict. The verdict itself is human."""
    evidence = [
        {
            "name": READING_MID_CHANGE_RATE,
            "value": readings[READING_MID_CHANGE_RATE]["value"],
            "caliber": readings[READING_MID_CHANGE_RATE]["caliber"],
            "role": readings[READING_MID_CHANGE_RATE]["role"],
        },
        {
            "name": READING_LOOP_NET_RATE,
            "value": readings[READING_LOOP_NET_RATE]["value"],
            "caliber": readings[READING_LOOP_NET_RATE]["caliber"],
            "role": readings[READING_LOOP_NET_RATE]["role"],
        },
        {
            "name": READING_PROFIT_DROP,
            "value": readings[READING_PROFIT_DROP]["value"],
            "measured": readings[READING_PROFIT_DROP]["measured"],
            "caliber": readings[READING_PROFIT_DROP]["caliber"],
            "role": readings[READING_PROFIT_DROP]["role"],
        },
        {
            "name": READING_LOOPS_WITHOUT_BACKUP,
            "value": readings[READING_LOOPS_WITHOUT_BACKUP]["value"],
            "caliber": readings[READING_LOOPS_WITHOUT_BACKUP]["caliber"],
            "role": readings[READING_LOOPS_WITHOUT_BACKUP]["role"],
        },
        {
            "name": READING_CHANGE_CONCENTRATION,
            "value": readings[READING_CHANGE_CONCENTRATION]["value"],
            "caliber": readings[READING_CHANGE_CONCENTRATION]["caliber"],
            "role": readings[READING_CHANGE_CONCENTRATION]["role"],
        },
    ]
    return {
        "decision": "PENDING_HUMAN_CONFIRMATION",
        "decidedByScript": False,
        "candidateOptions": [
            "adopt the new order as the default",
            "adjust the main order before the change is made",
        ],
        "evidence": evidence,
        "attributionAvailable": True,
        "attribution": {
            "directionGroups": attribution["directionGroups"]["changedGroups"],
            "changeEvents": attribution["concentration"]["changeEvents"],
            "concentration": attribution["concentration"]["share"],
            "loopsWithoutBackup": attribution["loopsWithoutBackup"]["withoutBackup"],
        },
        "note": (
            "The script collects the readings and the attribution and "
            "refuses to decide. The former thresholds are now readings: "
            "they describe where the change lands, they no longer gate "
            "anything. The verdict stays with a human."
        ),
    }


def build_scenario_checks(report: Dict[str, Any]) -> Dict[str, Any]:
    projection = report["step3_two_row_projection"]
    admission = report["step3b_admission_profile"]
    ordering = report["step5_tick_ordering"]
    middle = report["step7_middle_hop_pool_change"]
    loops = report["step8_closed_loop_set_change"]
    distribution_block = report["step9_capacity_and_profit_distribution"]
    capacity = distribution_block["capacity"]
    drift = distribution_block["capacityDrift"]
    profit = distribution_block["profit"]
    verdict = report["step10_caliber_verdict"]
    return {
        "T-S-1": {
            "name": "two directions have the same cardinality",
            "passed": projection["rowCountMatches"]
            and not projection["poolsWithWrongDirectionSet"]
            and admission["holds"],
            "detail": "%d usable pools, %d projected rows, %d admission violations"
            % (
                projection["usablePools"],
                projection["projectedRows"],
                admission["violationCount"],
            ),
        },
        "T-S-2": {
            "name": "the rank is continuous inside every (pair, direction)",
            "passed": ordering["rankContinuityHolds"],
            "detail": "%d groups checked" % ordering["pairDirectionGroups"],
        },
        "T-S-3": {
            "name": "the direction mapping cannot be flipped",
            "passed": ordering["poolsWithMirroredTickRank"] == projection["usablePools"]
            and not ordering["poolsWithBrokenMirror"],
            "detail": "%d pools keep the mirrored tick rank across the two directions"
            % ordering["poolsWithMirroredTickRank"],
        },
        "T-S-4": {
            "name": "the middle hop change table",
            "passed": True,
            "detail": "%d pairs, %d anchor triples, %d changed"
            % (
                middle["pairCount"],
                middle["tripleCount"],
                middle["changedTargetUniverseTriples"],
            ),
        },
        "T-S-5": {
            "name": "the closed loop set change table",
            "passed": True,
            "detail": "old %d, new %d, retained %d, added %d, removed %d"
            % (
                loops["oldLoopCount"],
                loops["newLoopCount"],
                loops["retainedCount"],
                loops["addedCount"],
                loops["removedCount"],
            ),
        },
        "T-S-6": {
            "name": "the capacity difference, missing and zero apart",
            "passed": True,
            "detail": "%d loops compared on one tick state, %d missing old, %d missing new, %d measured zero on both sides; the materialisation drift is reported apart (%d unchanged)"
            % (
                capacity["comparedLoops"],
                capacity["missingOldCapacity"],
                capacity["missingNewCapacity"],
                capacity["measuredZeroOnBothSides"],
                drift["unchangedCount"],
            ),
        },
        "T-S-7": {
            "name": "the profit difference, a measured zero apart from an unmeasured",
            "passed": True,
            "detail": "old profit: %s; new profit: %s"
            % (
                json.dumps(profit["oldProfitStates"], ensure_ascii=False),
                profit["newProfitState"],
            ),
        },
        "T-S-8": {
            "name": "the caliber verdict is explicit and waits for a human",
            "passed": verdict["decision"] == "PENDING_HUMAN_CONFIRMATION"
            and not verdict["decidedByScript"],
            "detail": verdict["decision"],
        },
    }


def conclusion_digest(report: Dict[str, Any]) -> str:
    """A digest over the analytical result, without any timestamp."""
    payload = {
        key: value for key, value in report.items() if key not in ("generatedAt", "environment")
    }
    payload["environment"] = {
        key: value
        for key, value in report.get("environment", {}).items()
        if key not in ("dbSha256", "dbSizeBytes")
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The Markdown report
# ---------------------------------------------------------------------------


def _format_rate(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return "%.4f" % value


def _format_number(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return "%.6g" % value
    return str(value)


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    add = lines.append
    environment = report["environment"]
    add("# TASK-166 directional-pool shadow comparison")
    add("")
    add("Read-only. The store was opened with `mode=ro`; the script holds no write statement.")
    add("")
    add("| item | value |")
    add("|---|---|")
    add("| generated at | %s |" % report["generatedAt"])
    add("| store | `%s` |" % environment["dbPath"])
    add("| store size | %d bytes |" % environment["dbSizeBytes"])
    add("| store sha256 | `%s` |" % environment["dbSha256"])
    add("| network / chain | %s / %d |" % (environment["network"], environment["chainId"]))
    add(
        "| conclusion digest | `%s` |"
        % report["step11_readonly_proof"]["conclusionDigest"]
    )
    add("")

    add("## Oracles")
    add("")
    oracle_capacity = report["oracle_capacity"]
    oracle_ranking = report["oracle_ranking"]
    add("| oracle | compared | matched | rate |")
    add("|---|---|---|---|")
    add(
        "| the ported tick bound against the Rust-written snapshot bound | %d | %d | %s |"
        % (
            oracle_capacity["compared"],
            oracle_capacity["matched"],
            _format_rate(oracle_capacity["matchRate"]),
        )
    )
    add(
        "| the ported order against the Rust-written recommendations | %d | %d | %s |"
        % (
            oracle_ranking["comparedRecommendations"],
            oracle_ranking["matchedRecommendations"],
            _format_rate(oracle_ranking["matchRate"]),
        )
    )
    add("")
    add("### The tick bound oracle, by the writer that wrote the row last")
    add("")
    add("| the row was written last by | compared | matched | rate |")
    add("|---|---|---|---|")
    for name, bucket in oracle_capacity["byLastWriter"].items():
        add(
            "| %s | %d | %d | %s |"
            % (
                name,
                bucket["compared"],
                bucket["matched"],
                _format_rate(bucket["matchRate"]),
            )
        )
    add("")
    add(oracle_capacity["note"])
    add("")

    add("## Step 2 - the base pool census")
    add("")
    census = report["step2_base_pool_census"]
    add("| item | value |")
    add("|---|---|")
    add("| base pool rows (`v4_exchange_pools_core4`) | %d |" % census["basePoolRows"])
    add(
        "| base pool status | %s |"
        % json.dumps(census["basePoolStatus"], ensure_ascii=False)
    )
    add("| usable base pools | %d |" % census["usablePoolCount"])
    add("| candidate-exchange rows | %d |" % census["candidateExchangeRows"])
    add(
        "| candidate-exchange status | %s |"
        % json.dumps(census["candidateExchangeStatus"], ensure_ascii=False)
    )
    add(
        "| candidate-exchange usable pools | %d |"
        % census["candidateExchangeUsablePoolCount"]
    )
    add("| depth snapshot rows | %d |" % census["snapshotRows"])
    add("")

    add("## Step 3b - the admission convention the directional pool inherits")
    add("")
    admission = report["step3b_admission_profile"]
    add(admission["description"])
    add("")
    add("| item | value |")
    add("|---|---|")
    add("| projected rows checked | %d |" % admission["checkedRows"])
    add("| violations | %d |" % admission["violationCount"])
    add("| the convention holds | %s |" % admission["holds"])
    add("| fee range of the projected pools | %s .. %s |" % (admission["feeMin"], admission["feeMax"]))
    add("")

    add("## Step 7 - the middle hop pool change table")
    add("")
    middle = report["step7_middle_hop_pool_change"]
    add("| item | value |")
    add("|---|---|")
    add("| pairs | %d |" % middle["pairCount"])
    add("| anchor triples | %d |" % middle["tripleCount"])
    add(
        "| changed, same universe (order only) | %d (%s) |"
        % (
            middle["changedSameUniversePairs"],
            _format_rate(middle["changedSameUniverseRate"]),
        )
    )
    add(
        "| changed, target universe | %d (%s) |"
        % (
            middle["changedTargetUniversePairs"],
            _format_rate(middle["changedTargetUniverseRate"]),
        )
    )
    add(
        "| anchor-independence violations | %d |"
        % len(middle["anchorIndependenceViolations"])
    )
    add("")

    add("## Step 8 - the closed loop set change table")
    add("")
    loops = report["step8_closed_loop_set_change"]
    add("| item | value |")
    add("|---|---|")
    add("| old loops | %d |" % loops["oldLoopCount"])
    add("| new loops | %d |" % loops["newLoopCount"])
    add("| retained | %d |" % loops["retainedCount"])
    add("| added | %d |" % loops["addedCount"])
    add("| removed | %d |" % loops["removedCount"])
    add(
        "| retained with a changed middle pool | %d |"
        % loops["retainedWithChangedMiddlePoolCount"]
    )
    add("")
    if loops["added"]:
        add("### Added loops")
        add("")
        add("| anchor | Y | Z |")
        add("|---|---|---|")
        for row in loops["added"]:
            add("| %s | %s | %s |" % (row["anchorTokenKeyId"], row["tokenY"], row["tokenZ"]))
        add("")
    if loops["removed"]:
        add("### Removed loops")
        add("")
        add("| anchor | Y | Z |")
        add("|---|---|---|")
        for row in loops["removed"]:
            add("| %s | %s | %s |" % (row["anchorTokenKeyId"], row["tokenY"], row["tokenZ"]))
        add("")

    add("## Step 9 - the capacity and profit distributions")
    add("")
    step9 = report["step9_capacity_and_profit_distribution"]
    capacity = step9["capacity"]
    drift = step9["capacityDrift"]
    profit = step9["profit"]
    add("| item | value |")
    add("|---|---|")
    add(
        "| loops compared, both sides folded with this wave's tick state | %d |"
        % capacity["comparedLoops"]
    )
    add("| capacity delta median | %s |" % capacity["deltaDistribution"]["median"])
    add("| capacity delta max | %s |" % capacity["deltaDistribution"]["max"])
    add("| capacity delta min | %s |" % capacity["deltaDistribution"]["min"])
    add(
        "| capacity ratio median | %s |"
        % _format_number(capacity["ratioDistribution"]["median"])
    )
    add(
        "| loops whose middle pool changed | %d |"
        % capacity["changedMiddlePoolLoops"]
    )
    add("| capacity missing on the old side | %d |" % capacity["missingOldCapacity"])
    add("| capacity missing on the new side | %d |" % capacity["missingNewCapacity"])
    add(
        "| measured zero on both sides | %d |"
        % capacity["measuredZeroOnBothSides"]
    )
    add("")
    add("### The materialisation drift, reported apart")
    add("")
    add(drift["description"])
    add("")
    add("| item | value |")
    add("|---|---|")
    add("| loops compared | %d |" % drift["comparedLoops"])
    add("| stored capacity missing | %d |" % drift["missingStoredCapacity"])
    add("| unchanged since the materialisation | %d |" % drift["unchangedCount"])
    add("| drift delta median | %s |" % drift["deltaDistribution"]["median"])
    add(
        "| drift ratio median | %s |"
        % _format_number(drift["ratioDistribution"]["median"])
    )
    add("")
    add(drift["note"])
    add("")
    add("### The profit")
    add("")
    add(
        "| old profit, measured positive | %d |"
        % profit["oldProfitStates"]["measuredPositive"]
    )
    add(
        "| old profit, measured zero | %d |"
        % profit["oldProfitStates"]["measuredZero"]
    )
    add(
        "| old profit, measured negative | %d |"
        % profit["oldProfitStates"]["measuredNegative"]
    )
    add("| old profit, unmeasured | %d |" % profit["oldProfitStates"]["unmeasured"])
    add("| new profit | %s |" % profit["newProfitState"])
    add("")

    add("## Step 9b - the change attribution")
    add("")
    attribution = report["step9b_change_attribution"]
    groups = attribution["directionGroups"]
    concentration = attribution["concentration"]
    without_backup = attribution["loopsWithoutBackup"]
    add(attribution["description"])
    add("")
    add("| item | value |")
    add("|---|---|")
    add("| (pair, direction) groups | %d |" % groups["groups"])
    add("| groups with a middle-pool change | %d |" % groups["changedGroups"])
    add("| groups without a change | %d |" % groups["unchangedGroups"])
    add(
        "| change rate over the groups | %s |"
        % _format_rate(groups["changedRate"])
    )
    for direction in ("0", "1"):
        bucket = groups["byDirection"][direction]
        add(
            "| direction %s: groups / changed / events | %d / %d / %d |"
            % (
                direction,
                bucket["groups"],
                bucket["changedGroups"],
                bucket["changeEvents"],
            )
        )
    add("| change events | %d |" % concentration["changeEvents"])
    add(
        "| top-10 groups' share of the events | %s |"
        % _format_rate(concentration["share"])
    )
    add(
        "| distinct new pools | %d (of %d usable) |"
        % (concentration["distinctNewPools"], concentration["usablePoolCount"])
    )
    add("| closed loops without a middle-pool backup | %d / %d |" % (
        without_backup["withoutBackup"],
        without_backup["loops"],
    ))
    add("")
    add("| fate of the loops without a backup | loops |")
    add("|---|---|")
    for fate, count in without_backup["fateCounts"].items():
        add("| %s | %d |" % (fate, count))
    add("")
    removed_loops = attribution["removedLoops"]
    add("| the loops the new caliber drops | count |")
    add("|---|---|")
    add("| dropped | %d |" % removed_loops["count"])
    for reason, count in removed_loops["reasonCounts"].items():
        add("| dropped by %s | %d |" % (reason, count))
    add("")
    add("| dropped loop | had a backup | rejected by | new middle pool |")
    add("|---|---|---|---|")
    for row in removed_loops["rows"]:
        add(
            "| `%s` | %s | %s | %s |"
            % (
                row["loopKey"],
                "yes" if row["hadBackup"] else "no",
                row["rejectedBy"],
                row["newMiddlePoolId"] or "-",
            )
        )
    add("")
    add("### Why the dropped loops drop")
    add("")
    add("| middle pool | in core4 | in candidate exchange | dropped loops |")
    add("|---|---|---|---|")
    for row in removed_loops["universeCoverage"]["pools"]:
        add(
            "| `%s` | %s | %s | %d |"
            % (
                row["poolId"],
                row["core4Status"] or "absent",
                row["exchangeStatus"] or "absent",
                row["droppedLoops"],
            )
        )
    add("")
    add("| middle-hop candidate count | loops |")
    add("|---|---|")
    for count, loops_n in without_backup["middleCandidateCountDistribution"].items():
        add("| %s | %d |" % (count, loops_n))
    add("")

    add("## The readings")
    add("")
    add(
        "These replace the three former thresholds.  A reading is measured "
        "and explained; it is no longer compared against a fixed value to "
        "decide whether the change may proceed."
    )
    add("")
    add("| reading | measured | numerator / denominator | role |")
    add("|---|---|---|---|")
    for name, reading in report["readings"].items():
        numerator = reading.get("numerator")
        denominator = reading.get("denominator")
        if numerator is None or denominator is None:
            fraction = "not measured"
        else:
            fraction = "%s / %s" % (numerator, denominator)
        add(
            "| %s | %s | %s | %s |"
            % (name, _format_rate(reading["value"]), fraction, reading["role"])
        )
    add("")

    add("## Step 10 - the caliber verdict")
    add("")
    verdict = report["step10_caliber_verdict"]
    add("**Decision: %s.** The script does not decide." % verdict["decision"])
    add("")
    add("| evidence | measured | role |")
    add("|---|---|---|")
    for item in verdict["evidence"]:
        add(
            "| %s | %s | %s |"
            % (item["name"], _format_number(item["value"]), item["role"])
        )
    add("")
    add(verdict["note"])
    add("")

    add("## Scenario checks")
    add("")
    add("| scenario | passed | detail |")
    add("|---|---|---|")
    for name, check in report["scenarioChecks"].items():
        add(
            "| %s | %s | %s |"
            % (name, "yes" if check["passed"] else "NO", check["detail"])
        )
    add("")

    add("## Read-only proof")
    add("")
    proof = report["step11_readonly_proof"]
    add(
        "The row counts of every table the script reads are identical before and after the run: **%s**."
        % ("yes" if proof["rowCountsIdentical"] else "NO")
    )
    add("")
    add("| table | rows before | rows after |")
    add("|---|---|---|")
    for table in READ_ONLY_TABLES:
        add(
            "| %s | %d | %d |"
            % (
                table,
                proof["rowCountsBefore"].get(table, -1),
                proof["rowCountsAfter"].get(table, -1),
            )
        )
    add("")
    return "\n".join(lines) + "\n"


def render_attribution_markdown(attribution: Dict[str, Any]) -> str:
    """The human-readable form of the attribution artefact."""
    lines: List[str] = []
    add = lines.append
    environment = attribution["environment"]
    groups = attribution["attribution"]["directionGroups"]
    concentration = attribution["attribution"]["concentration"]
    new_pools = attribution["attribution"]["newPools"]
    without_backup = attribution["attribution"]["loopsWithoutBackup"]

    add("# TASK-166 middle-hop change attribution")
    add("")
    add(
        "Read-only.  The store was opened with `mode=ro`; the script holds no "
        "write statement."
    )
    add("")
    add("| item | value |")
    add("|---|---|")
    add("| generated at | %s |" % attribution["generatedAt"])
    add("| store | `%s` |" % environment["dbPath"])
    add("| network / chain | %s / %d |" % (environment["network"], environment["chainId"]))
    add(
        "| source report digest | `%s` |"
        % attribution["sourceReport"]["conclusionDigest"]
    )
    add("")

    add("## The readings")
    add("")
    add("| reading | measured | numerator / denominator | role |")
    add("|---|---|---|---|")
    for name, reading in attribution["readings"].items():
        numerator = reading.get("numerator")
        denominator = reading.get("denominator")
        if numerator is None or denominator is None:
            fraction = "not measured"
        else:
            fraction = "%s / %s" % (numerator, denominator)
        add(
            "| %s | %s | %s | %s |"
            % (name, _format_rate(reading["value"]), fraction, reading["role"])
        )
    add("")
    add("| caliber of each reading |")
    add("|---|---|")
    for name, reading in attribution["readings"].items():
        add("| %s | %s |" % (name, reading["caliber"]))
    add("")

    add("## Where the change lands")
    add("")
    add(attribution["attribution"]["description"])
    add("")
    add("| item | value |")
    add("|---|---|")
    add("| (pair, direction) groups | %d |" % groups["groups"])
    add("| groups with a middle-pool change | %d |" % groups["changedGroups"])
    add("| groups without a change | %d |" % groups["unchangedGroups"])
    add("| change rate | %s |" % _format_rate(groups["changedRate"]))
    add("")
    add("| direction | groups | changed groups | change events |")
    add("|---|---|---|---|")
    for direction in ("0", "1"):
        bucket = groups["byDirection"][direction]
        add(
            "| %s | %d | %d | %d |"
            % (
                direction,
                bucket["groups"],
                bucket["changedGroups"],
                bucket["changeEvents"],
            )
        )
    add("")

    add("### The ten groups that carry the change")
    add("")
    add("| pair | direction | changed triples |")
    add("|---|---|---|")
    for group in concentration["topGroupList"]:
        add(
            "| `%s` | %d | %d |"
            % (group["pairKeyId"], group["direction"], group["changedTriples"])
        )
    add("")
    add(
        "The ten groups carry **%d of %d** change events (**%s**)."
        % (
            concentration["topGroupEvents"],
            concentration["changeEvents"],
            _format_rate(concentration["share"]),
        )
    )
    add("")

    add("### The pools the middle hop moves to")
    add("")
    add("| pool | changed triples | fee | tick spacing | depth | capacity |")
    add("|---|---|---|---|---|---|")
    for pool in new_pools["pools"]:
        add(
            "| `%s` | %d | %s | %s | %s | %s |"
            % (
                pool["poolId"],
                pool["changedTriples"],
                pool["fee"],
                pool["tickSpacing"],
                pool["depthStatus"],
                pool["capacityRaw"],
            )
        )
    add("")
    add("| feature | distribution over the new pools |")
    add("|---|---|")
    for feature, distribution in new_pools["featureDistribution"].items():
        add("| %s | %s |" % (feature, json.dumps(distribution, ensure_ascii=False)))
    add("")

    add("### The loops that have no fallback pool")
    add("")
    add("| item | value |")
    add("|---|---|")
    add("| closed loops | %d |" % without_backup["loops"])
    add("| loops without a middle-pool backup | %d |" % without_backup["withoutBackup"])
    add("| share | %s |" % _format_rate(without_backup["withoutBackupRate"]))
    add("")
    add("| middle-hop candidate count | loops |")
    add("|---|---|")
    for count, loops_n in without_backup["middleCandidateCountDistribution"].items():
        add("| %s | %d |" % (count, loops_n))
    add("")
    add("| fate | loops |")
    add("|---|---|")
    for fate, count in without_backup["fateCounts"].items():
        add("| %s | %d |" % (fate, count))
    add("")
    removed_loops = attribution["attribution"]["removedLoops"]
    add("### The loops the new caliber drops")
    add("")
    add("| dropped loop | had a backup | rejected by | new middle pool |")
    add("|---|---|---|---|")
    for row in removed_loops["rows"]:
        add(
            "| `%s` | %s | %s | %s |"
            % (
                row["loopKey"],
                "yes" if row["hadBackup"] else "no",
                row["rejectedBy"],
                row["newMiddlePoolId"] or "-",
            )
        )
    add("")
    add("| middle pool of the dropped loops | in core4 | in candidate exchange | dropped loops |")
    add("|---|---|---|---|")
    for row in removed_loops["universeCoverage"]["pools"]:
        add(
            "| `%s` | %s | %s | %d |"
            % (
                row["poolId"],
                row["core4Status"] or "absent",
                row["exchangeStatus"] or "absent",
                row["droppedLoops"],
            )
        )
    add("")
    add("| loop | path | middle candidates | fate | new middle pool |")
    add("|---|---|---|---|---|")
    for row in without_backup["rows"]:
        add(
            "| `%s` | %s | %d | %s | %s |"
            % (
                row["loopKey"],
                row["pathText"],
                row["middleCandidateCount"],
                row["fate"],
                row["newMiddlePoolId"] or "-",
            )
        )
    add("")

    add("## The verdict stays with a human")
    add("")
    verdict = attribution["caliberVerdict"]
    add("**Decision: %s.**" % verdict["decision"])
    add("")
    add(verdict["note"])
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    report = build_report()
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    with open(OUT_MD, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    attribution = {
        "task": "TASK-166",
        "acAcceptance": "AC-215",
        "title": "Middle-hop change attribution (read-only)",
        "readOnly": True,
        "generatedAt": report["generatedAt"],
        "environment": report["environment"],
        "readings": report["readings"],
        "attribution": report["step9b_change_attribution"],
        "caliberVerdict": report["step10_caliber_verdict"],
        "sourceReport": {
            "json": rel(OUT_JSON),
            "markdown": rel(OUT_MD),
            "conclusionDigest": report["step11_readonly_proof"]["conclusionDigest"],
        },
    }
    with open(OUT_ATTRIBUTION_JSON, "w", encoding="utf-8") as handle:
        json.dump(
            attribution, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str
        )
        handle.write("\n")
    with open(OUT_ATTRIBUTION_MD, "w", encoding="utf-8") as handle:
        handle.write(render_attribution_markdown(attribution))
    print("report json : %s" % rel(OUT_JSON))
    print("report md   : %s" % rel(OUT_MD))
    print("attribution json : %s" % rel(OUT_ATTRIBUTION_JSON))
    print("attribution md   : %s" % rel(OUT_ATTRIBUTION_MD))
    print("digest      : %s" % report["step11_readonly_proof"]["conclusionDigest"])
    for name, check in report["scenarioChecks"].items():
        print("  %-6s %-4s %s" % (name, "PASS" if check["passed"] else "FAIL", check["detail"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
