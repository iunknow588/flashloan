"""On-chain price-impact capacity for Uniswap V4 pools.

The depth verdict of a pool is expressed entirely in token units:

``capacity(i) = max { a : priceImpact(a) <= epsilon }``

where ``epsilon`` is 10 bps by default. The price impact is measured against
the pool's own mid price, so no external ETH/USD or equity quote is involved.
A USD-denominated TVL reading can therefore never change the admission
decision; it is only kept as a human-facing tie-breaker.

This module is the Python side of the shared configuration. The Rust pool
manager uses the same epsilon and the same capacity bands, so both sides
reach the same verdict for the same on-chain state.

The maths mirrors the canonical Uniswap concentrated-liquidity formulas:

* token0 in: ``sqrtP_next = L * Q96 * sqrtP / (L * Q96 + amount * sqrtP)``
* token1 in: ``sqrtP_next = sqrtP + amount * Q96 / L``

The price impact excludes the liquidity-provider fee, exactly like
``dex_quote_evidence_pool._annotate_hop_price_impact`` does
(``priceImpactFeeExcluded = True``).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any


# Location of the shared depth policy.
#
# The Rust pool manager reads the very same file, so a threshold can never
# differ between the two implementations. Both sides assert their view
# matches the file at start-up and fail hard on a mismatch.
DEPTH_POLICY_RELATIVE_PATH = Path("flashloan/config/depth_policy.json")


def _resolve_depth_policy_path() -> Path:
    """Return the absolute path of the shared depth policy file."""
    override = os.environ.get("FLASHLOAN_DEPTH_POLICY_PATH")
    if override:
        return Path(override).expanduser().resolve()
    # Walk up from this file until the repository-relative path resolves.
    for parent in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        candidate = parent / DEPTH_POLICY_RELATIVE_PATH
        if candidate.is_file():
            return candidate
        nested = parent / "config" / "depth_policy.json"
        if nested.is_file() and parent.name == "flashloan":
            return nested
    raise FileNotFoundError(
        "shared depth policy file not found: "
        f"{DEPTH_POLICY_RELATIVE_PATH} (set FLASHLOAN_DEPTH_POLICY_PATH to override)"
    )


def load_depth_policy() -> dict[str, Any]:
    """Load and validate the shared depth policy."""
    path = _resolve_depth_policy_path()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"depth policy must be a JSON object: {path}")
    required = (
        "priceImpactLimitBps",
        "depthBandDeepCapacity",
        "depthBandMediumCapacity",
        "depthBandShallowCapacity",
        "minCandidateCapacityRaw",
        "maxCandidateFixedFee",
        "depthBasis",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"depth policy is missing required keys {missing}: {path}")
    return payload


def load_economic_policy() -> dict[str, Any]:
    """Load the USDG economic floor from the shared policy file.

    VAL-4-12: the economic floor used to be a hardcoded constant in the
    Rust token manager. It now lives in the same shared file as the depth
    thresholds, so the Python orchestrator, the Rust token manager and the
    configuration file cannot disagree about what a position has to be
    worth.

    The thresholds are denominated in USDG, the venue's own quote asset,
    never in USD: a USD threshold cannot express tradable value inside the
    arbitrage environment.
    """
    policy = load_depth_policy()
    section = policy.get("economicPolicy")
    if not isinstance(section, dict):
        raise ValueError(
            "depth policy carries no economicPolicy section; "
            "schemaVersion 3 is required"
        )
    missing = [
        key
        for key in ("minEconomicPositionUsdgRaw", "minPoolDepthUsdgRaw", "usdgDecimals")
        if key not in section
    ]
    if missing:
        raise ValueError(f"economicPolicy is missing required keys {missing}")
    return section


def load_usdg_peg_policy() -> dict[str, Any]:
    """Load and validate the USDG peg policy from the shared policy file.

    R-11 (2026-11-12, user ruling): USDG may be treated as economically
    equivalent to USD, so ``1 USDG == 1 USD``. The peg ratio is therefore
    exactly ``1`` and the allowed deviation is zero: if USDG ever depegs,
    every conversion that depends on the peg must fail loudly instead of
    silently producing a near-USD answer.

    The policy also names the uses the peg may enter and the uses it may
    never enter. Keeping the forbidden list in the shared file (rather than
    only in prose) is what stops "USDG equals USD" from silently widening
    into "any USD number may take part in an admission decision".

    Nothing here introduces an external fiat price source. USDG is a real
    on-chain asset; its anchor to USD is a property of the asset, not a feed.

    The ruling is a **replacement**, not a reporting tweak: most tokens have
    no exchange channel, so no external USD price can be obtained for them,
    while USDG is exchangeable in the large majority of cases. Every
    USD-denominated economic quantity is therefore expressed as an equal
    amount of USDG. The replacement acts on the **denomination** only; it
    never turns into a depth, capacity or membership gate.
    """
    policy = load_depth_policy()
    section = policy.get("usdgPegPolicy")
    if not isinstance(section, dict):
        raise ValueError(
            "depth policy carries no usdgPegPolicy section; "
            "R-11 requires the USDG peg to be declared explicitly"
        )
    missing = [
        key
        for key in (
            "pegRatio",
            "basis",
            "deviationToleranceBps",
            "allowedUses",
            "forbiddenUses",
        )
        if key not in section
    ]
    if missing:
        raise ValueError(f"usdgPegPolicy is missing required keys {missing}")
    try:
        peg_ratio = float(section["pegRatio"])
    except (TypeError, ValueError) as error:
        raise ValueError("usdgPegPolicy.pegRatio must be numeric") from error
    if peg_ratio != 1.0:
        # The ruling is "USDG is USD-equivalent by definition". A different
        # ratio would be a different ruling, and it must be made explicitly
        # rather than inherited from a stale file.
        raise ValueError(
            "usdgPegPolicy.pegRatio must be 1.0 under R-11 "
            f"(USDG is USD-equivalent by definition); got {peg_ratio}"
        )
    try:
        deviation_tolerance_bps = int(float(section["deviationToleranceBps"]))
    except (TypeError, ValueError) as error:
        raise ValueError(
            "usdgPegPolicy.deviationToleranceBps must be an integer"
        ) from error
    if float(section["deviationToleranceBps"]) != float(deviation_tolerance_bps):
        raise ValueError(
            "usdgPegPolicy.deviationToleranceBps must be an integral number"
        )
    if deviation_tolerance_bps != 0:
        # A non-zero tolerance would silently accept a depegged USDG.
        raise ValueError(
            "usdgPegPolicy.deviationToleranceBps must be 0 under R-11 "
            "(a depegged USDG must fail, not be approximated); "
            f"got {deviation_tolerance_bps}"
        )
    for key in ("allowedUses", "forbiddenUses"):
        value = section[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"usdgPegPolicy.{key} must be a list of strings")
    return section


DEPTH_POLICY = load_depth_policy()

# USDG economic floor, shared with the Rust token manager.
#
# The floor is expressed in raw USDG units (six decimals), so the value of
# ten USDG is 10_000_000. It is read from the shared file and never from a
# local copy.
ECONOMIC_POLICY = load_economic_policy()
MIN_ECONOMIC_POSITION_USDG_RAW = int(ECONOMIC_POLICY["minEconomicPositionUsdgRaw"])
MIN_POOL_DEPTH_USDG_RAW = int(ECONOMIC_POLICY["minPoolDepthUsdgRaw"])
USDG_DECIMALS = int(ECONOMIC_POLICY["usdgDecimals"])

# USDG peg policy (R-11), shared with the Rust token manager.
#
# The ratio is the auditable field every USDG/USD conversion carries. It is
# exactly 1 because USDG is USD-equivalent by definition, and the deviation
# tolerance is zero so a depegged USDG fails instead of being approximated.
#
# The allowed/forbidden use sets are the enforcement mechanism for the
# ruling's boundary: the peg may state an economic floor, a reported
# notional or a gas comparison, but it may never decide depth, capacity or
# library membership on its own.
USDG_PEG_POLICY = load_usdg_peg_policy()
USDG_PEG_RATIO = float(USDG_PEG_POLICY["pegRatio"])
USDG_PEG_BASIS = str(USDG_PEG_POLICY["basis"])
USDG_PEG_DEVIATION_TOLERANCE_BPS = int(USDG_PEG_POLICY["deviationToleranceBps"])
USDG_PEG_ALLOWED_USES = frozenset(USDG_PEG_POLICY["allowedUses"])
USDG_PEG_FORBIDDEN_USES = frozenset(USDG_PEG_POLICY["forbiddenUses"])

# Maximum price impact, in basis points, a candidate position may impose.
#
# This mirrors `dex_quote_evidence_pool.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS`
# so admission and execution share one epsilon. The value comes from the
# shared policy file, never from a local copy.
DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS = DEPTH_POLICY["priceImpactLimitBps"]

# Basis recorded on every capacity observation.
DEPTH_BASIS = DEPTH_POLICY["depthBasis"]

# Capacity bands, expressed in raw token units of the binding side.
#
# The boundaries are shared with the Rust pool manager (`depth_band_json_for`)
# so that both sides classify the same pool identically. The boundary operator
# is `>=` on both sides.
# The band boundaries are raw token amounts, so they stay integral even
# though the shared file stores them as JSON numbers.
#
# DEP-4.2: the bands are expressed in decimals-normalised binding-side
# capacity, not in raw token units. A raw-unit threshold compares a
# 6-decimal stablecoin amount against an 18-decimal token amount, which
# says nothing about tradable depth. The binding side is the smaller of
# the two one-sided capacities after each side is converted into its own
# token units, because a round trip needs BOTH sides to absorb the
# position.
#
# The boundaries are anchored on the position sizes the system actually
# attempts rather than on percentiles of the pool population: the observed
# position sizes have a median magnitude of about 0.02 token, so the shallow
# boundary is the smallest attempted position (0.001 token), the medium
# boundary is ten times that and the deep boundary is a hundred times that.
# Measured against the 94 candidate pools this yields 15 dust, 35 shallow,
# 29 medium and 19 deep.
DEPTH_BAND_DEEP_CAPACITY = float(DEPTH_POLICY["depthBandDeepCapacity"])
DEPTH_BAND_MEDIUM_CAPACITY = float(DEPTH_POLICY["depthBandMediumCapacity"])
DEPTH_BAND_SHALLOW_CAPACITY = float(DEPTH_POLICY["depthBandShallowCapacity"])

# Basis the depth bands are computed on.
DEPTH_BAND_BASIS = DEPTH_POLICY.get("depthBandBasis", "binding_side_decimal_normalised")

# Minimum on-chain capacity for a pool to be admitted as a candidate.
#
# The raw floor is a dust guard only. The admission floor is the
# normalised binding-side capacity below.
MIN_CANDIDATE_CAPACITY_RAW = DEPTH_POLICY["minCandidateCapacityRaw"]

# Minimum decimals-normalised binding-side capacity for a candidate pool.
MIN_CANDIDATE_BINDING_CAPACITY = float(DEPTH_POLICY["minCandidateBindingCapacity"])

# Maximum fixed fee, in hundredths of a bip, that a candidate pool may charge.
MAX_CANDIDATE_FIXED_FEE = DEPTH_POLICY["maxCandidateFixedFee"]

# Reference gas estimate, in ETH. Observation only.
GAS_ESTIMATE_ETH = DEPTH_POLICY.get("gasEstimateEth")

# `Q96` is `2**96`. The constants below are written out rather than derived
# from each other because the intermediate products overflow the 256-bit
# arithmetic used by the canonical Uniswap implementations.
_Q96 = 1 << 96
_Q192 = 1 << 192

# Uniswap expresses the liquidity-provider fee in hundredths of a bip.
_FEE_DENOMINATOR = 1_000_000


def fee_adjusted_input(amount_in_raw: int, fee_pips: int) -> int:
    """Return the amount that reaches the curve once the LP fee is taken."""
    fee_complement = max(0, _FEE_DENOMINATOR - int(fee_pips))
    if fee_complement == 0:
        return 0
    return (int(amount_in_raw) * fee_complement) // _FEE_DENOMINATOR


def spot_amount_out(sqrt_price_x96: int, amount_in_raw: int, zero_for_one: bool) -> int:
    """Return the zero-slippage output for an exact input at the mid price."""
    sqrt_price_x96 = int(sqrt_price_x96)
    amount_in_raw = int(amount_in_raw)
    if sqrt_price_x96 <= 0 or amount_in_raw <= 0:
        return 0
    if zero_for_one:
        # token0 in, token1 out: `amountIn * sqrtP^2 / Q192`.
        return (amount_in_raw * sqrt_price_x96 * sqrt_price_x96) // _Q192
    # token1 in, token0 out: the inverse of the mid price.
    return (amount_in_raw * _Q192) // (sqrt_price_x96 * sqrt_price_x96)


def _next_sqrt_price_from_amount0(sqrt_price_x96: int, liquidity: int, amount_in_raw: int) -> int:
    """Return the next sqrt price after adding token0, rounded up."""
    if amount_in_raw <= 0:
        return sqrt_price_x96
    numerator1 = liquidity << 96
    product = amount_in_raw * sqrt_price_x96
    denominator = numerator1 + product
    if denominator <= 0 or denominator < numerator1:
        return sqrt_price_x96
    quotient = (numerator1 * sqrt_price_x96 + denominator - 1) // denominator
    return quotient if quotient < sqrt_price_x96 else sqrt_price_x96


def _next_sqrt_price_from_amount1(sqrt_price_x96: int, liquidity: int, amount_in_raw: int) -> int:
    """Return the next sqrt price after adding token1, rounded down."""
    if amount_in_raw <= 0:
        return sqrt_price_x96
    quotient = (amount_in_raw << 96) // liquidity
    return sqrt_price_x96 + quotient


def _amount1_delta(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
    """Return `L * (sqrtB - sqrtA) / Q96`, rounded down."""
    low, high = (sqrt_a, sqrt_b) if sqrt_a <= sqrt_b else (sqrt_b, sqrt_a)
    return (liquidity * (high - low)) // _Q96


def _amount0_delta(sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
    """Return `L * (sqrtB - sqrtA) * Q96 / (sqrtA * sqrtB)`, rounded down."""
    low, high = (sqrt_a, sqrt_b) if sqrt_a <= sqrt_b else (sqrt_b, sqrt_a)
    if low <= 0:
        return 0
    numerator1 = liquidity << 96
    numerator2 = high - low
    return ((numerator1 * numerator2) // high) // low


def swap_amount_out(
    sqrt_price_x96: int,
    liquidity: int,
    fee_pips: int,
    amount_in_raw: int,
    zero_for_one: bool,
) -> int:
    """Return the realised output of one exact-input swap step."""
    amount_in_less_fee = fee_adjusted_input(amount_in_raw, fee_pips)
    if amount_in_less_fee <= 0:
        return 0
    if zero_for_one:
        sqrt_next = _next_sqrt_price_from_amount0(sqrt_price_x96, liquidity, amount_in_less_fee)
        return _amount1_delta(sqrt_next, sqrt_price_x96, liquidity)
    sqrt_next = _next_sqrt_price_from_amount1(sqrt_price_x96, liquidity, amount_in_less_fee)
    return _amount0_delta(sqrt_next, sqrt_price_x96, liquidity)


def price_impact_bps(
    sqrt_price_x96: int,
    liquidity: int,
    fee_pips: int,
    amount_in_raw: int,
    zero_for_one: bool,
) -> float:
    """Return the realised price impact, in basis points, of an exact input.

    The liquidity-provider fee is excluded from the measurement, matching
    `dex_quote_evidence_pool._annotate_hop_price_impact`.
    """
    amount_in_raw = int(amount_in_raw)
    if amount_in_raw <= 0:
        return 0.0
    if int(sqrt_price_x96) <= 0 or int(liquidity) <= 0:
        raise ValueError("pool state is not usable for a price impact measurement")
    amount_out = swap_amount_out(sqrt_price_x96, liquidity, fee_pips, amount_in_raw, zero_for_one)
    fee_free_amount = fee_adjusted_input(amount_in_raw, fee_pips)
    spot_out = spot_amount_out(sqrt_price_x96, fee_free_amount, zero_for_one)
    if spot_out <= 0:
        raise ValueError("spot output is zero for the requested amount")
    if amount_out >= spot_out:
        # Rounding can make the realised output one unit larger than the
        # idealised spot output. Treat that as no impact.
        return 0.0
    return (spot_out - amount_out) * 10_000.0 / float(spot_out)


def _reserve_bound(sqrt_price_x96: int, liquidity: int, zero_for_one: bool) -> int:
    """Return an upper bound on the amount the pool can absorb.

    The concentrated-liquidity virtual reserves give the natural bound:
    token0 in is bounded by `L * Q96 / sqrtP`, token1 in by `L * sqrtP / Q96`.
    """
    if zero_for_one:
        return ((liquidity << 96) * _Q96) // sqrt_price_x96
    return (liquidity * sqrt_price_x96) // _Q96


def _solve_capacity(
    sqrt_price_x96: int,
    liquidity: int,
    fee_pips: int,
    price_impact_limit_bps: float,
    zero_for_one: bool,
) -> tuple[int, float]:
    """Binary-search the largest input whose price impact stays within limit."""
    high = _reserve_bound(sqrt_price_x96, liquidity, zero_for_one)
    if high <= 0:
        high = 1
    # Grow the bracket until the pool reports a usable price impact at the top
    # of it, so the binary search always has a finite upper bound.
    for _ in range(128):
        try:
            price_impact_bps(sqrt_price_x96, liquidity, fee_pips, high, zero_for_one)
        except ValueError:
            if high > (1 << 127):
                break
            high *= 2
            continue
        break
    low = 0
    best = 0
    best_impact = 0.0
    # 128 iterations resolve the answer to raw token units.
    for _ in range(128):
        if high <= low + 1:
            break
        mid = low + (high - low) // 2
        try:
            impact = price_impact_bps(sqrt_price_x96, liquidity, fee_pips, mid, zero_for_one)
        except ValueError:
            # An amount the pool cannot absorb at all is above capacity.
            high = mid
            continue
        if impact <= price_impact_limit_bps:
            best = mid
            best_impact = impact
            low = mid
        else:
            high = mid
    return best, best_impact


def capacity_from_sqrt_price(
    sqrt_price_x96: int,
    liquidity: int,
    fee_pips: int = 3000,
    price_impact_limit_bps: float = float(DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS),
) -> dict[str, Any]:
    """Return the one-sided tradable capacity of a pool.

    The returned mapping carries the raw capacity of both sides, the realised
    price impact at that capacity and the basis on which it was computed. All
    quantities are denominated in the pool's own tokens.
    """
    sqrt_price_x96 = int(sqrt_price_x96)
    liquidity = int(liquidity)
    if sqrt_price_x96 <= 0:
        raise ValueError("sqrtPriceX96 must be greater than zero")
    if liquidity <= 0:
        raise ValueError("liquidity must be greater than zero")
    if not 0.0 <= float(price_impact_limit_bps) <= 10_000.0:
        raise ValueError("price impact limit must be within 0..=10000 bps")

    token0_raw, token0_impact = _solve_capacity(
        sqrt_price_x96, liquidity, fee_pips, float(price_impact_limit_bps), True
    )
    token1_raw, token1_impact = _solve_capacity(
        sqrt_price_x96, liquidity, fee_pips, float(price_impact_limit_bps), False
    )
    return {
        "capacityToken0Raw": token0_raw,
        "capacityToken1Raw": token1_raw,
        "capacityRaw": max(token0_raw, token1_raw),
        "priceImpactBps": max(token0_impact, token1_impact),
        "priceImpactLimitBps": float(price_impact_limit_bps),
        "feePips": int(fee_pips),
        "hasLiquidity": True,
        "depthBasis": DEPTH_BASIS,
    }


def raw_to_decimal_text(raw: int, decimals: int) -> str:
    """Render a raw token amount as a human-readable decimal string."""
    raw = int(raw)
    decimals = int(decimals)
    if decimals <= 0:
        return str(raw)
    with localcontext() as context:
        context.prec = 80
        value = (Decimal(raw) / (Decimal(10) ** decimals)).normalize()
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def binding_side_capacity(
    capacity_token0_raw: int | None,
    capacity_token1_raw: int | None,
    token0_decimals: int | None,
    token1_decimals: int | None,
) -> float | None:
    """Return the decimals-normalised capacity of the weaker side.

    A round-trip arbitrage sends a position through a pool in one direction
    and back in the other. Both sides therefore have to absorb the
    position, so the pool's usable depth is bounded by the *weaker* side.

    Each side is first converted into its own token units, because a raw
    unit is only comparable to another raw unit when both tokens share the
    same number of decimals. The result is therefore a fraction of one
    token, and it is directly comparable across pools.

    ``None`` means the capacity cannot be decided: one of the capacities or
    decimals is unknown. The caller must treat that as unmeasured rather
    than as a shallow pool.
    """
    if capacity_token0_raw is None or capacity_token1_raw is None:
        return None
    if token0_decimals is None or token1_decimals is None:
        return None
    try:
        token0_decimals = int(token0_decimals)
        token1_decimals = int(token1_decimals)
        capacity_token0 = int(capacity_token0_raw)
        capacity_token1 = int(capacity_token1_raw)
    except (TypeError, ValueError):
        return None
    if token0_decimals < 0 or token1_decimals < 0:
        return None
    if capacity_token0 < 0 or capacity_token1 < 0:
        return None
    normalised0 = capacity_token0 / (10 ** token0_decimals)
    normalised1 = capacity_token1 / (10 ** token1_decimals)
    return min(normalised0, normalised1)


def capacity_band(binding_capacity: float | None) -> str:
    """Classify a binding-side capacity into the shared depth bands.

    The bands are expressed in decimals-normalised binding-side units.
    The boundary operator is ``>=``, matching the Rust side.
    """
    if binding_capacity is None:
        return "unavailable"
    binding_capacity = float(binding_capacity)
    if binding_capacity >= DEPTH_BAND_DEEP_CAPACITY:
        return "deep"
    if binding_capacity >= DEPTH_BAND_MEDIUM_CAPACITY:
        return "medium"
    if binding_capacity >= DEPTH_BAND_SHALLOW_CAPACITY:
        return "shallow"
    return "dust"


# ---------------------------------------------------------------------------
# Capacity coverage against a gas budget
# ---------------------------------------------------------------------------
#
# A pool is only usable for a position when the position can both pay for
# itself and stay inside the epsilon price-impact budget. The binding
# constraint is therefore:
#
#     capacity >= required_amount
#
# where ``required_amount`` is the trade size that produces the expected
# profit. When the expected profit is quoted per token of trade size, the
# required amount is ``gas_profit / unit_profit``.
#
# The check is expressed entirely in the pool's own token units, so it never
# depends on a USD price feed.

CAPACITY_INSUFFICIENT_REASON = "capacity_insufficient"

# The capacity of at least one hop was never measured. This is not a market
# conclusion: it means the pool index has not published a capacity for the
# hop yet. It is surfaced separately so an operator can tell "the pool is too
# shallow" apart from "the pool state is missing".
CAPACITY_UNKNOWN_REASON = "capacity_unknown"

# Minimum number of raw token units a pool capacity must reach before the
# capacity gate can ever pass. A capacity below this bound is dust and can
# never carry a meaningful position, whatever the profit rate.
MIN_CAPACITY_RAW_FLOOR = 1

# Environment switch that promotes an unmeasured capacity into a hard block.
#
# The capacity gate can only prove that a pool is too shallow once the pool
# index has published a capacity for it. Until the production depth rollout
# has run, every hop is unmeasured, and treating that as a block would halt
# the whole pipeline instead of surfacing the missing measurement. The gate
# therefore blocks on a *known* insufficient capacity and reports an
# unmeasured capacity as a non-blocking "not measured" signal. Setting this
# switch to 1 turns the unmeasured case into a hard block as well.
CAPACITY_GATE_REQUIRE_MEASURED_ENV = "DEX_V4_CAPACITY_GATE_REQUIRE_MEASURED"


def capacity_gate_requires_measured() -> bool:
    """Return whether an unmeasured capacity must block execution."""
    raw = str(os.environ.get(CAPACITY_GATE_REQUIRE_MEASURED_ENV, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def capacity_covers_amount(
    capacity_raw: int | None,
    required_raw: int | None,
) -> bool | None:
    """Return whether a capacity can carry a required trade size.

    ``None`` means "cannot be decided": either side of the comparison is
    unknown. An unknown capacity must never be treated as sufficient, because
    that would silently re-enable the very failure mode the gate exists to
    catch.
    """
    if capacity_raw is None or required_raw is None:
        return None
    capacity_raw = int(capacity_raw)
    required_raw = int(required_raw)
    if capacity_raw < MIN_CAPACITY_RAW_FLOOR:
        return False
    if required_raw <= 0:
        # No positive requirement: the capacity cannot be the binding limit.
        return True
    return capacity_raw >= required_raw


def required_amount_for_profit(
    gas_profit: float | None,
    unit_profit: float | None,
) -> int | None:
    """Return the trade size needed to cover ``gas_profit`` at ``unit_profit``.

    ``unit_profit`` is the expected profit produced by one unit of trade
    size. When it is unknown or not positive the requirement cannot be
    computed, and the capacity gate must stay undecided rather than block a
    route on a missing number.
    """
    if gas_profit is None or unit_profit is None:
        return None
    try:
        gas_profit = float(gas_profit)
        unit_profit = float(unit_profit)
    except (TypeError, ValueError):
        return None
    if gas_profit <= 0:
        return 0
    if unit_profit <= 0:
        return None
    return max(1, int(gas_profit / unit_profit))


def capacity_gate(
    capacity_raw: int | None,
    required_raw: int | None,
    *,
    require_measured: bool | None = None,
) -> dict[str, Any]:
    """Return the capacity-gate verdict for one pool or path.

    The verdict distinguishes three states, because collapsing them would
    hide the difference between "the pool is too shallow" and "the pool state
    is unknown":

    ``pass``
        The capacity is known and covers the required amount.
    ``insufficient``
        The capacity is known and is smaller than the required amount. This
        is a pool-selection defect and always blocks automatic execution.
    ``unknown``
        The capacity was never published for this hop. It is reported, but
        it does not block by default: a missing measurement is a coverage gap
        of the pool index, not a statement about the market.

    ``require_measured`` promotes the last state into a block. It defaults
    to the environment switch, so the rollout decides when a missing
    measurement becomes fatal.
    """
    if require_measured is None:
        require_measured = capacity_gate_requires_measured()
    capacity_raw = None if capacity_raw is None else int(capacity_raw)
    required_raw = None if required_raw is None else int(required_raw)
    verdict = capacity_covers_amount(capacity_raw, required_raw)
    if verdict is True:
        return {
            "verdict": "pass",
            "blocked": False,
            "reason": None,
            "measured": True,
            "capacityRaw": capacity_raw,
            "requiredRaw": required_raw,
        }
    if verdict is False:
        return {
            "verdict": "insufficient",
            "blocked": True,
            "reason": CAPACITY_INSUFFICIENT_REASON,
            "measured": True,
            "capacityRaw": capacity_raw,
            "requiredRaw": required_raw,
        }
    return {
        "verdict": "unknown",
        "blocked": bool(require_measured),
        "reason": CAPACITY_UNKNOWN_REASON if require_measured else None,
        "measured": False,
        "capacityRaw": capacity_raw,
        "requiredRaw": required_raw,
    }
