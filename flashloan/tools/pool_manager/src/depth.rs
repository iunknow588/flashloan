//! On-chain capacity and depth evaluation for Uniswap V4 pools.
//!
//! The depth verdict is expressed entirely in token units:
//!
//! ```text
//! capacity(i) = max { a : priceImpact(a) <= epsilon }
//! ```
//!
//! where `epsilon` is 10 bps by default. The price impact is measured
//! against the pool's own mid price, so no external ETH/USD or equity quote
//! is involved. A USD-denominated TVL reading can therefore never change the
//! admission decision; it is only kept as a human-facing tie-breaker.
//!
//! The primary maths path uses the canonical Uniswap V4 SDK
//! (`uniswap-v4-sdk`, which re-exports the V3 SDK's `sqrt_price_math` and
//! `full_math`). A self-contained fallback reproduces the same rounding
//! rules so the tool keeps working if the SDK is unavailable for a given
//! build. Both paths are locked down by the same test vectors.

use serde_json::{json, Value};

use crate::chain::V4PoolState;

/// Maximum price impact, in basis points, a candidate position may impose.
///
/// This mirrors `dex_quote_evidence_pool.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS`
/// so admission and execution share one epsilon.
pub use crate::depth_config::PRICE_IMPACT_LIMIT_BPS;

/// Basis recorded on every capacity observation.
pub use crate::depth_config::DEPTH_BASIS;

/// Q96 in 256-bit form (`2^96`).
///
/// `U256::from_limbs` takes `[u64; 4]`, so the limbs are laid out least
/// significant first. `2^96` therefore needs the second limb to hold
/// `2^32`, not `1`: writing `[0, 0, 1, 0]` would silently produce `2^128`.
const Q96: alloy_primitives::U256 = alloy_primitives::U256::from_limbs([0, 4_294_967_296, 0, 0]);

/// The largest value a `uint160` can hold, used to pick the exact Uniswap
/// rounding branch for the next sqrt price.
const U160_MAX: alloy_primitives::U256 =
    alloy_primitives::U256::from_limbs([u64::MAX, u64::MAX, u32::MAX as u64, 0]);

/// Q192 in 256-bit form (`2^192`).
///
/// This constant is written out rather than computed as `Q96 * Q96`: the
/// big-integer multiplication of two Q96 values overflows the intermediate
/// product and silently yields zero, so the literal is the only safe source.
const Q192: alloy_primitives::U256 = alloy_primitives::U256::from_limbs([0, 0, 0, 1]);

/// Multiplies a liquidity value by `Q96` without overflowing.
///
/// The canonical Uniswap formulation is `liquidity << 96`. A shift is used
/// instead of a multiplication because `ruint` multiplication wraps silently
/// on overflow, whereas a shift is exact for every liquidity that fits the
/// canonical `L << 96` range.
fn liquidity_q96(liquidity: u128) -> alloy_primitives::U256 {
    alloy_primitives::U256::from(liquidity) << 96
}

/// One-sided tradable capacity of a pool, denominated in the input token.
#[derive(Clone, Debug, Default)]
pub struct Capacity {
    /// Capacity for a token0 -> token1 swap, in raw token0 units.
    pub capacity_token0_raw: u128,
    /// Capacity for a token1 -> token0 swap, in raw token1 units.
    pub capacity_token1_raw: u128,
    /// Price impact, in basis points, reached at the reported capacity.
    pub price_impact_bps: f64,
    /// Fee, in hundredths of a bip, applied to the sized position.
    pub fee_pips: u32,
    /// Whether the pool reported usable liquidity.
    pub has_liquidity: bool,
    /// Reason the capacity could not be computed, when applicable.
    pub error: Option<String>,
    /// The largest input a token0 -> token1 swap may carry while it stays
    /// inside the current tick range.
    ///
    /// This is the **version gate** of the first arbitrage version: a hop
    /// whose size exceeds this bound would cross a tick boundary, where the
    /// single-step pricing no longer holds. The bound is `None` when the
    /// pool state does not carry a tick, because the range cannot be
    /// located without one.
    pub single_tick_max_token0_raw: Option<u128>,
    /// The largest input a token1 -> token0 swap may carry while it stays
    /// inside the current tick range.
    pub single_tick_max_token1_raw: Option<u128>,
}

impl Capacity {
    /// Returns the larger of the two one-sided capacities.
    ///
    /// This is the value the admission ordering uses.
    pub fn best_raw(&self) -> u128 {
        self.capacity_token0_raw.max(self.capacity_token1_raw)
    }

    /// The largest input that stays inside the current tick range, for the
    /// given direction.
    ///
    /// `true` asks for the token0 -> token1 direction.
    ///
    /// The bound is the **effective** capacity of the version: a position
    /// may never be larger than both the price-impact capacity and the
    /// tick-range bound, because exceeding either one makes the off-chain
    /// price untrue. The smaller of the two is therefore the size the
    /// orchestrator may use.
    pub fn effective_max_raw(&self, zero_for_one: bool) -> Option<u128> {
        let tick_bound = if zero_for_one {
            self.single_tick_max_token0_raw
        } else {
            self.single_tick_max_token1_raw
        };
        let depth_bound = if zero_for_one {
            self.capacity_token0_raw
        } else {
            self.capacity_token1_raw
        };
        tick_bound.map(|bound| bound.min(depth_bound))
    }
}

/// Which maths implementation produced a result.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MathBackend {
    /// Canonical Uniswap V4 SDK maths.
    Sdk,
    /// Self-contained fallback maths.
    Fallback,
}

/// Evaluates the one-sided capacity of a pool for a given price-impact limit.
///
/// `sqrt_price_x96` and `liquidity` are read live from the pool manager. The
/// computation follows the standard concentrated-liquidity exact-input
/// rounding: the next sqrt price is derived from the input amount, and the
/// realised output is compared against the zero-impact output to obtain the
/// price impact.
pub fn evaluate_capacity(
    state: &V4PoolState,
    price_impact_limit_bps: f64,
    tick_spacing: i32,
) -> Capacity {
    let mut capacity = Capacity {
        has_liquidity: state.has_liquidity,
        fee_pips: state.lp_fee as u32,
        ..Default::default()
    };
    if !state.has_liquidity || state.liquidity == 0 {
        capacity.error = Some("pool has no live liquidity".to_string());
        return capacity;
    }
    let sqrt_price_x96 = match state.sqrt_price_x96.parse::<u128>() {
        Ok(value) if value > 0 => value,
        _ => {
            capacity.error = Some("pool sqrtPriceX96 is unavailable".to_string());
            return capacity;
        }
    };
    match capacity_from_sqrt_price(
        sqrt_price_x96,
        state.liquidity,
        state.lp_fee as u32,
        price_impact_limit_bps,
    ) {
        Ok(mut value) => {
            // The tick-range bound is the gate of the first arbitrage
            // version: a position may not be larger than the amount that
            // keeps the price inside the current tick range, because the
            // single-step pricing is only valid there.
            let (token0_bound, token1_bound) = single_tick_bounds(
                sqrt_price_x96,
                state.liquidity,
                state.lp_fee as u32,
                tick_spacing,
                state.tick,
            );
            value.single_tick_max_token0_raw = token0_bound;
            value.single_tick_max_token1_raw = token1_bound;
            value
        }
        Err(error) => {
            capacity.error = Some(error);
            capacity
        }
    }
}

/// The largest input each direction may carry inside the current tick range.
///
/// The two directions are bounded by the two sides of the range, so a
/// downward swap runs into the lower bound and an upward swap into the
/// upper one. The bounds are computed with the same single-step arithmetic
/// the first arbitrage version prices with, so the gate and the price can
/// never drift apart.
fn single_tick_bounds(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    tick_spacing: i32,
    tick: i64,
) -> (Option<u128>, Option<u128>) {
    // A spacing of zero means the pool's spacing is unknown, so the tick
    // range cannot be located and no bound is published.
    if tick_spacing <= 0 {
        return (None, None);
    }
    let Ok(tick) = i32::try_from(tick) else {
        return (None, None);
    };
    let bounds = match tick_range_bounds(tick, tick_spacing, sqrt_price_x96) {
        Ok(bounds) => bounds,
        Err(_) => return (None, None),
    };
    let down = amount_to_reach_price(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        bounds.lower_sqrt_price_x96,
        true,
    )
    .ok();
    let up = amount_to_reach_price(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        bounds.upper_sqrt_price_x96,
        false,
    )
    .ok();
    (down, up)
}

/// Recomputes the single-tick bounds from a raw pool observation.
///
/// TASK-172 / AC-221. The depth snapshot table publishes
/// `single_tick_max_token0_raw` / `single_tick_max_token1_raw`, but a
/// consumer that reads a **stored** bound depends on the writer having
/// measured the bound from the *same* on-chain moment as the price it
/// published next to it. That was not guaranteed, and a consumer that
/// materialised the stored bound published a stale number.
///
/// This function lets a consumer derive the bound from the observation it
/// already holds (price, liquidity, tick, fee, spacing) instead of
/// trusting a stored number. It is the **same** arithmetic the pool manager
/// used to produce the stored value, so the two agree exactly when the
/// observation is the one the bound was computed from.
///
/// The boundary arithmetic stays in Rust (DPO-10); this is the Rust-side
/// entry point, not a Python re-implementation.
///
/// A `None` bound means the bound could not be located: the tick spacing is
/// unknown, the tick does not fit an `i32`, or the tick range could not be
/// computed. That is the same meaning the stored `NULL` carries.
pub fn recompute_single_tick_bounds(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    tick_spacing: i32,
    tick: i64,
) -> (Option<u128>, Option<u128>) {
    single_tick_bounds(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        tick_spacing,
        tick,
    )
}

/// Computes the one-sided capacity for a pool at a given sqrt price.
///
/// This is the pure function behind [`evaluate_capacity`]. It is deliberately
/// free of I/O so it can be locked down with deterministic test vectors.
pub fn capacity_from_sqrt_price(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    price_impact_limit_bps: f64,
) -> Result<Capacity, String> {
    capacity_from_sqrt_price_with_backend(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        price_impact_limit_bps,
        MathBackend::Sdk,
    )
}

/// Computes the capacity using a specific maths backend.
pub fn capacity_from_sqrt_price_with_backend(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    price_impact_limit_bps: f64,
    backend: MathBackend,
) -> Result<Capacity, String> {
    if sqrt_price_x96 == 0 {
        return Err("sqrtPriceX96 must be greater than zero".to_string());
    }
    if liquidity == 0 {
        return Err("liquidity must be greater than zero".to_string());
    }
    if !(0.0..=10_000.0).contains(&price_impact_limit_bps) {
        return Err("price impact limit must be within 0..=10000 bps".to_string());
    }

    // token0 -> token1 pushes the price down, token1 -> token0 pushes it up.
    let token0 = solve_capacity(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        price_impact_limit_bps,
        true,
        backend,
    )?;
    let token1 = solve_capacity(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        price_impact_limit_bps,
        false,
        backend,
    )?;

    Ok(Capacity {
        capacity_token0_raw: token0.0,
        capacity_token1_raw: token1.0,
        // The reported impact belongs to the binding (smaller) side, because
        // that is the amount the pool can actually absorb.
        price_impact_bps: token0.1.max(token1.1),
        fee_pips,
        has_liquidity: true,
        error: None,
        // This function has no tick, so it cannot locate the tick range and
        // therefore publishes no tick bound.
        single_tick_max_token0_raw: None,
        single_tick_max_token1_raw: None,
    })
}

/// Binary-searches the largest input amount whose price impact stays within
/// `price_impact_limit_bps`.
///
/// Returns the raw capacity and the price impact it realises.
fn solve_capacity(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    price_impact_limit_bps: f64,
    zero_for_one: bool,
    backend: MathBackend,
) -> Result<(u128, f64), String> {
    // The usable bracket is derived from the liquidity itself: the virtual
    // reserves of the pool are bounded by `L * Q96 / sqrtP` in token0 units
    // and by `L * sqrtP / Q96` in token1 units. Searching far beyond those
    // bounds only produces "insufficient liquidity" errors, so the bracket
    // starts at the reserve bound and is doubled if the search needs more
    // room.
    let mut high = reserve_bound(sqrt_price_x96, liquidity, zero_for_one);
    if high == 0 {
        high = 1;
    }
    // Grow the bracket until the pool reports a usable price impact at the
    // top of it, so the binary search always has a finite upper bound.
    // The reserve ceiling is a hard upper bound on the amount the pool can
    // absorb. Growing the bracket past it would only enter the saturated
    // region, where the price impact stops being meaningful.
    let mut ceiling = reserve_ceiling(sqrt_price_x96, liquidity, zero_for_one);
    if ceiling == 0 {
        ceiling = 1;
    }
    let mut attempts = 0;
    loop {
        match price_impact_for_input_with_backend(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            high,
            zero_for_one,
            backend,
        ) {
            Ok(_) => break,
            Err(_) => {
                if high >= ceiling || high > u128::MAX / 2 {
                    // Clamp the bracket to the ceiling: beyond it the pool
                    // has no reserves left and the impact is undefined.
                    high = ceiling;
                    break;
                }
                high = high.saturating_mul(2);
                attempts += 1;
                if attempts > 128 {
                    break;
                }
            }
        }
    }
    if high == 0 {
        high = 1;
    }
    let mut low: u128 = 0;
    let mut best: u128 = 0;
    let mut best_impact = 0.0f64;

    // 128 iterations of binary search resolve the answer to raw token units.
    for _ in 0..128 {
        if high <= low + 1 {
            break;
        }
        let mid = low + (high - low) / 2;
        match price_impact_for_input_with_backend(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            mid,
            zero_for_one,
            backend,
        ) {
            Ok(impact) => {
                if impact <= price_impact_limit_bps {
                    best = mid;
                    best_impact = impact;
                    low = mid;
                } else {
                    high = mid;
                }
            }
            Err(_) => {
                // An amount the pool cannot absorb at all is above capacity.
                high = mid;
            }
        }
    }
    Ok((best, best_impact))
}

/// Returns an upper bound, in raw input-token units, on the amount the pool
/// can absorb.
///
/// The concentrated-liquidity virtual reserves give the natural bound:
/// token0 in is bounded by `L * Q96 / sqrtP`, token1 in by
/// `L * sqrtP / Q96`.
fn reserve_bound(sqrt_price_x96: u128, liquidity: u128, zero_for_one: bool) -> u128 {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;

    let liquidity_u256 = alloy_primitives::U256::from(liquidity);
    let sqrt_u256 = alloy_primitives::U256::from(sqrt_price_x96);
    let value = if zero_for_one {
        // token0 in: `L * Q96 / sqrtP`.
        mul_div(liquidity_q96(liquidity), Q96, sqrt_u256).unwrap_or(alloy_primitives::U256::ZERO)
    } else {
        // token1 in: `L * sqrtP / Q96`.
        mul_div(liquidity_u256, sqrt_u256, Q96).unwrap_or(alloy_primitives::U256::ZERO)
    };
    let capped = value.min(alloy_primitives::U256::from(u128::MAX));
    capped.to::<u128>()
}

/// Returns the amount that reaches the curve once the liquidity-provider fee
/// has been taken.
///
/// The fee is expressed in hundredths of a bip (1e-6), matching the Uniswap
/// convention used by the pool manager.
fn fee_adjusted_input(amount_in_raw: u128, fee_pips: u32) -> u128 {
    let fee_complement = 1_000_000u64.saturating_sub(fee_pips as u64);
    if fee_complement == 0 {
        return 0;
    }
    // The intermediate is computed in 256 bits: a raw token amount can be
    // close to `u128::MAX`, so a native 128-bit multiplication would overflow.
    let adjusted = alloy_primitives::U256::from(amount_in_raw)
        .saturating_mul(alloy_primitives::U256::from(fee_complement))
        / alloy_primitives::U256::from(1_000_000u64);
    adjusted
        .min(alloy_primitives::U256::from(u128::MAX))
        .to::<u128>()
}
/// Returns the largest output the pool can produce for the given direction.
///
/// This is the concentrated-liquidity virtual reserve: `L * Q96 / sqrtP` for
/// token0 output and `L * sqrtP / Q96` for token1 output. A swap that
/// reaches this ceiling has consumed the whole reserve and has no capacity
/// left, so it must be reported as an error rather than as a finite result.
fn reserve_ceiling(sqrt_price_x96: u128, liquidity: u128, zero_for_one: bool) -> u128 {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;

    let liquidity_u256 = alloy_primitives::U256::from(liquidity);
    let sqrt_u256 = alloy_primitives::U256::from(sqrt_price_x96);
    let value = if zero_for_one {
        // token0 in, token1 out: `L * sqrtP / Q96`.
        mul_div(liquidity_u256, sqrt_u256, Q96).unwrap_or(alloy_primitives::U256::ZERO)
    } else {
        // token1 in, token0 out: `L * Q96 / sqrtP`.
        //
        // The `Q96` factor is applied inside the 512-bit `mul_div`. Forming
        // `L << 96` in 256 bits overflows for a large liquidity and wraps to
        // a meaningless ceiling, which would let the capacity bracket grow
        // without bound.
        mul_div(liquidity_u256, Q96, sqrt_u256)
            .unwrap_or(alloy_primitives::U256::ZERO)
            .saturating_mul(Q96)
    };
    value
        .min(alloy_primitives::U256::from(u128::MAX))
        .to::<u128>()
}

/// Returns the realised price impact, in basis points, of an exact-input swap.
///
/// The realised output is compared against the zero-slippage output implied by
/// the current mid price. This is the same definition used by
/// `dex_quote_evidence_pool._annotate_hop_price_impact`, so the capacity
/// limit and the execution preflight agree on what "price impact" means.
pub fn price_impact_for_input(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Result<f64, String> {
    price_impact_for_input_with_backend(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        amount_in_raw,
        zero_for_one,
        MathBackend::Sdk,
    )
}

/// Returns the realised price impact using a specific maths backend.
pub fn price_impact_for_input_with_backend(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
    backend: MathBackend,
) -> Result<f64, String> {
    if amount_in_raw == 0 {
        return Ok(0.0);
    }
    if sqrt_price_x96 == 0 || liquidity == 0 {
        return Err("pool state is not usable for a price impact measurement".to_string());
    }
    // The price impact is measured *without* the liquidity-provider fee, which
    // is exactly what `dex_quote_evidence_pool._annotate_hop_price_impact`
    // does (`priceImpactFeeExcluded = True`). Charging the fee on both sides
    // would otherwise report the fee as a price impact and collapse the
    // capacity to a small fraction of the real depth.
    let fee_free_amount = fee_adjusted_input(amount_in_raw, fee_pips);
    let amount_out = match backend {
        MathBackend::Sdk => sdk_swap_amount_out(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            amount_in_raw,
            zero_for_one,
        )?,
        MathBackend::Fallback => fallback_swap_amount_out(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            amount_in_raw,
            zero_for_one,
        )?,
    };
    // Zero-impact output uses the current mid price without any slippage and
    // on the same fee-free input, so only the curve movement is measured.
    let spot_out = spot_amount_out(sqrt_price_x96, fee_free_amount, zero_for_one);
    if spot_out == 0 {
        return Err("spot output is zero for the requested amount".to_string());
    }
    // The pool cannot pay out more than its virtual reserve. When the
    // mid-price spot output exceeds that reserve the amount is already
    // beyond the pool's capacity, and the price impact is undefined.
    let ceiling = reserve_ceiling(sqrt_price_x96, liquidity, zero_for_one);
    if spot_out > ceiling {
        return Err("amount exceeds the pool's reserve ceiling".to_string());
    }
    if amount_out >= spot_out {
        // Rounding can make the realised output one unit larger than the
        // idealised spot output. Treat that as no impact.
        return Ok(0.0);
    }
    let impact = (spot_out - amount_out) as f64 * 10_000.0 / spot_out as f64;
    Ok(impact)
}

/// Zero-slippage output for an exact input, computed from the mid price.
pub fn spot_amount_out(sqrt_price_x96: u128, amount_in_raw: u128, zero_for_one: bool) -> u128 {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;

    // The spot output is computed with the SDK's full-precision `mul_div`,
    // which evaluates the 512-bit product exactly. A plain 256-bit
    // multiplication wraps for amounts near `u128::MAX`, and the wrapped
    // result then compared as "no slippage", which made every oversized
    // amount look like it had zero price impact.
    let amount = alloy_primitives::U256::from(amount_in_raw);
    let sqrt_price = alloy_primitives::U256::from(sqrt_price_x96);
    let value = if zero_for_one {
        // token0 in, token1 out: `amountIn * sqrtP^2 / Q192`.
        let squared = sqrt_price.saturating_mul(sqrt_price);
        if squared.is_zero() {
            return 0;
        }
        mul_div(amount, squared, Q192).unwrap_or(alloy_primitives::U256::ZERO)
    } else {
        // token1 in, token0 out: inverse of the mid price, i.e. divided by
        // `sqrtP` twice.
        let squared = sqrt_price.saturating_mul(sqrt_price);
        if squared.is_zero() {
            return 0;
        }
        mul_div(amount, Q192, squared).unwrap_or(alloy_primitives::U256::ZERO)
    };
    // The result is clamped before the cast to `u128`: the intermediate can
    // exceed the 128-bit range and the unchecked cast would truncate.
    value
        .min(alloy_primitives::U256::from(u128::MAX))
        .to::<u128>()
}

// ---------------------------------------------------------------------------
// The single-tick version
// ---------------------------------------------------------------------------

/// The bounds of the **current tick range** of a pool.
///
/// The tick range is the price interval in which the pool's active
/// liquidity is constant. A swap that stays inside one tick range therefore
/// needs **no** tick traversal: the whole swap is priced with the same
/// liquidity, which is exactly the single-step arithmetic.
///
/// This is the first arbitrage version: every hop is constrained to one
/// tick range, so the off-chain arithmetic never has to walk the tick
/// bitmap. The price bound is derived from the tick spacing, which is the
/// granularity at which the pool may have liquidity boundaries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TickRangeBounds {
    /// The tick below the current one, at which the range ends.
    pub lower_tick: i32,
    /// The tick above the current one, at which the range ends.
    pub upper_tick: i32,
    /// The square-root price at the lower bound.
    pub lower_sqrt_price_x96: u128,
    /// The square-root price at the upper bound.
    pub upper_sqrt_price_x96: u128,
}

/// The smallest and the largest tick the V4 tick range may use.
pub const MIN_TICK: i32 = -887_272;
/// The largest tick the V4 tick range may use.
pub const MAX_TICK: i32 = 887_272;

/// Derives the bounds of the tick range the current price sits in.
///
/// The range is bounded by the neighbouring multiples of the **tick
/// spacing**: the pool may only change its liquidity on a tick that is a
/// multiple of the tick spacing, so the price moves freely between two
/// consecutive multiples.
///
/// The current price always lies **strictly inside** the range:
///
/// ```text
///   lower_sqrt_price_x96  <  current_sqrt_price_x96  <  upper_sqrt_price_x96
/// ```
///
/// A price that sits exactly on a spacing boundary would otherwise leave
/// that boundary with no room on one side, so the boundary is pushed one
/// further step out on that side. The tick is used only as a starting
/// point; the **price** decides where the range ends, because the price is
/// what the swap actually moves.
pub fn tick_range_bounds(
    tick: i32,
    tick_spacing: i32,
    sqrt_price_x96: u128,
) -> Result<TickRangeBounds, String> {
    let spacing = tick_spacing.max(1);
    let base = tick.div_euclid(spacing) * spacing;
    let mut lower_tick = base.max(MIN_TICK);
    let mut upper_tick = (base + spacing).min(MAX_TICK);
    if lower_tick >= upper_tick {
        return Err("the tick range is empty".to_string());
    }
    let mut lower = sqrt_ratio_at_tick(lower_tick)?;
    let mut upper = sqrt_ratio_at_tick(upper_tick)?;
    // The current price must have room on both sides. When it sits exactly
    // on a boundary, that boundary is moved one spacing step outwards.
    if lower >= sqrt_price_x96 {
        let moved = (lower_tick - spacing).max(MIN_TICK);
        if moved < lower_tick {
            lower_tick = moved;
            lower = sqrt_ratio_at_tick(lower_tick)?;
        }
    }
    if upper <= sqrt_price_x96 {
        let moved = (upper_tick + spacing).min(MAX_TICK);
        if moved > upper_tick {
            upper_tick = moved;
            upper = sqrt_ratio_at_tick(upper_tick)?;
        }
    }
    if lower >= upper {
        return Err("the tick range is empty".to_string());
    }
    Ok(TickRangeBounds {
        lower_tick,
        upper_tick,
        lower_sqrt_price_x96: lower,
        upper_sqrt_price_x96: upper,
    })
}

/// The square-root price at a tick, taken from the Uniswap V4 SDK.
fn sqrt_ratio_at_tick(tick: i32) -> Result<u128, String> {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::tick_math::get_sqrt_ratio_at_tick;
    let Ok(alias) = alloy_primitives::aliases::I24::try_from(tick) else {
        return Err(format!("tick {tick} is outside the V4 range"));
    };
    get_sqrt_ratio_at_tick(alias)
        .map_err(|error| format!("could not derive the price at tick {tick}: {error:?}"))
        .and_then(|value| {
            // The price at a tick always fits in 160 bits, but the cast is
            // checked anyway: an unchecked cast here would **panic** on a
            // malformed value, and a measurement must never abort the
            // process that asked for it.
            to_u128_checked(value, &format!("the price at tick {tick}"))
        })
}

/// Narrows a 256-bit value to 128 bits, or reports the overflow.
///
/// The chain can report a value that does not fit in 128 bits. An unchecked
/// cast would **panic** and abort the process that asked for a measurement,
/// which is exactly what a measurement must never do. The overflow is
/// therefore reported as an error, so the caller can decide what to do with
/// a route it cannot represent.
fn to_u128_checked<const BITS: usize, const LIMBS: usize>(
    value: alloy_primitives::Uint<BITS, LIMBS>,
    label: &str,
) -> Result<u128, String> {
    // The value is laid out big-endian, so it fits in 128 bits exactly when
    // every byte above the low 16 is zero.
    let bytes = value.to_be_bytes_vec();
    if bytes.len() < 16 || bytes[..bytes.len() - 16].iter().any(|byte| *byte != 0) {
        return Err(format!("{label} does not fit in 128 bits"));
    }
    let mut low = [0u8; 16];
    low.copy_from_slice(&bytes[bytes.len() - 16..]);
    Ok(u128::from_be_bytes(low))
}

/// The result of one single-tick swap.
#[derive(Clone, Debug, Default)]
pub struct SingleTickQuote {
    /// The realised output of the swap, in raw output-token units.
    pub amount_out_raw: u128,
    /// The largest input that still stays inside the current tick range.
    pub max_amount_in_raw: u128,
    /// The price reached by the swap.
    pub sqrt_price_x96_after: u128,
    /// Whether the swap stayed inside the tick range.
    pub inside_tick_range: bool,
    /// The reason the swap could not be priced, when applicable.
    pub error: Option<String>,
}

/// Prices one exact-input swap that is constrained to the **current tick
/// range**.
///
/// The swap is priced with the single-step arithmetic of the Uniswap SDK,
/// which is exactly what the contract does while the price stays inside one
/// range: the liquidity is constant there, so no tick crossing takes place
/// and the whole swap is one step.
///
/// The function also reports the largest input that stays inside the range,
/// which is the **version gate** the first arbitrage version uses: a hop
/// whose size exceeds that bound would cross the range boundary, so it is
/// not executable in this version and the orchestrator has to skip it rather
/// than guess the tick traversal.
///
/// The bound is the tick-spacing step, not the distance to the next
/// **initialized** tick: a liquidity boundary may exist anywhere, and
/// finding it is the job of the separate tick-traversal interface.
pub fn single_tick_swap_amount_out(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    tick_spacing: i32,
    tick: i32,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Result<SingleTickQuote, String> {
    if sqrt_price_x96 == 0 {
        return Err("the pool carries a zero price".to_string());
    }
    if liquidity == 0 {
        return Err("the pool has no live liquidity".to_string());
    }
    if fee_pips >= 1_000_000 {
        return Err("the fee consumes the entire input".to_string());
    }
    let bounds = tick_range_bounds(tick, tick_spacing, sqrt_price_x96)?;
    let (limit_price, max_amount_in_raw) = if zero_for_one {
        (
            bounds.lower_sqrt_price_x96,
            amount_to_reach_price(
                sqrt_price_x96,
                liquidity,
                fee_pips,
                bounds.lower_sqrt_price_x96,
                true,
            )?,
        )
    } else {
        (
            bounds.upper_sqrt_price_x96,
            amount_to_reach_price(
                sqrt_price_x96,
                liquidity,
                fee_pips,
                bounds.upper_sqrt_price_x96,
                false,
            )?,
        )
    };
    let step = compute_swap_step_raw(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        amount_in_raw,
        zero_for_one,
        limit_price,
    )?;
    // The swap is inside the range exactly when the whole input was
    // consumed without reaching the boundary: the step stops at the limit
    // only when the input is large enough to reach it.
    let inside_tick_range = amount_in_raw <= max_amount_in_raw;
    Ok(SingleTickQuote {
        amount_out_raw: step.amount_out,
        max_amount_in_raw,
        sqrt_price_x96_after: step.sqrt_price_next_x96,
        inside_tick_range,
        error: None,
    })
}

/// One swap step, priced with the Uniswap SDK arithmetic.
struct SwapStepRaw {
    /// The price the step ended at.
    sqrt_price_next_x96: u128,
    /// The input the step consumed, fee included.
    #[allow(dead_code)]
    amount_in: u128,
    /// The output the step produced.
    amount_out: u128,
}

/// Runs one swap step with the SDK's own `compute_swap_step`.
///
/// The SDK function is the exact arithmetic of the V3/V4 pool: it takes the
/// current price, the target price and the remaining input, and it returns
/// the price the step ends at together with the amounts it consumed and
/// produced. The target price is the tick-range boundary, so the step is
/// stopped at the boundary exactly as the contract would stop it at the
/// next initialized tick.
fn compute_swap_step_raw(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
    limit_price_x96: u128,
) -> Result<SwapStepRaw, String> {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::swap_math::compute_swap_step;

    // The SDK infers the direction from the price comparison, so the target
    // must be on the correct side of the current price. The tick range
    // guarantees that, except when the current price sits exactly on the
    // boundary and the swap moves away from the range.
    if zero_for_one {
        if limit_price_x96 >= sqrt_price_x96 {
            return Err("the price limit is not below the current price".to_string());
        }
    } else if limit_price_x96 <= sqrt_price_x96 {
        return Err("the price limit is not above the current price".to_string());
    }

    let current = alloy_primitives::U256::from(sqrt_price_x96);
    let target = alloy_primitives::U256::from(limit_price_x96);
    let remaining = alloy_primitives::I256::from_raw(alloy_primitives::U256::from(amount_in_raw));
    let fee = alloy_primitives::aliases::U24::try_from(fee_pips)
        .map_err(|error| format!("the fee is out of range: {error:?}"))?;
    let (sqrt_next, amount_in, amount_out, _fee_amount) =
        compute_swap_step(current, target, liquidity, remaining, fee)
            .map_err(|error| format!("the swap step failed: {error:?}"))?;
    Ok(SwapStepRaw {
        sqrt_price_next_x96: to_u128_checked(sqrt_next, "the next sqrt price")?,
        amount_in: to_u128_checked(amount_in, "the swap input amount")?,
        amount_out: to_u128_checked(amount_out, "the swap output amount")?,
    })
}

/// The largest input that moves the price to the given target.
///
/// The fee is taken off the input, so the input that reaches the target is
/// the fee-adjusted amount divided by the fee complement.
///
/// The result is the **per-token** delta of the Uniswap SDK: the amount of
/// token0 for a downward move and the amount of token1 for an upward move.
/// That distinction matters, because the two deltas are not the same
/// quantity: the token0 delta carries the additional price denominator
/// `sqrt(P_cur) * sqrt(P_boundary)` that the token1 delta does not. The
/// single-tick capacity of a hop is denominated in the token the hop
/// **spends**, so this function is the single source of truth for it.
pub fn amount_to_reach_price(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    target_sqrt_price_x96: u128,
    zero_for_one: bool,
) -> Result<u128, String> {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div_rounding_up;
    use uniswap_v4_sdk::prelude::v3_sdk::utils::sqrt_price_math as spm;

    let fee_complement = 1_000_000u64.saturating_sub(fee_pips as u64);
    if fee_complement == 0 {
        return Err("the fee consumes the entire input".to_string());
    }
    let amount_remaining = if zero_for_one {
        spm::get_amount_0_delta(
            alloy_primitives::U256::from(target_sqrt_price_x96),
            alloy_primitives::U256::from(sqrt_price_x96),
            liquidity,
            true,
        )
        .map_err(|error| format!("the amount to reach the lower bound failed: {error:?}"))?
    } else {
        spm::get_amount_1_delta(
            alloy_primitives::U256::from(sqrt_price_x96),
            alloy_primitives::U256::from(target_sqrt_price_x96),
            liquidity,
            true,
        )
        .map_err(|error| format!("the amount to reach the upper bound failed: {error:?}"))?
    };
    let raw = mul_div_rounding_up(
        amount_remaining,
        alloy_primitives::U256::from(1_000_000u64),
        alloy_primitives::U256::from(fee_complement),
    )
    .map_err(|error| format!("the fee adjustment failed: {error:?}"))?;
    Ok(raw
        .min(alloy_primitives::U256::from(u128::MAX))
        .to::<u128>())
}

// ---------------------------------------------------------------------------
// The arbiter versions
// ---------------------------------------------------------------------------

/// The arbitrage versions the orchestrator knows.
///
/// The version is not a feature flag: it is a **statement about what the
/// off-chain arithmetic can prove**. Version 1 prices every hop inside a
/// single tick range, which needs no tick traversal and is therefore exact
/// by construction.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArbiterVersion {
    /// Every hop stays inside one tick range.
    ///
    /// The pricing is the single-step arithmetic, which is exact while the
    /// price does not leave the range. A hop that would leave the range is
    /// **not executable** in this version: the orchestrator skips it rather
    /// than guessing the tick crossing.
    SingleTick,
}

impl ArbiterVersion {
    /// The stable wire name of the version.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::SingleTick => "single_tick",
        }
    }

    /// The version the orchestrator runs unless it is told otherwise.
    pub fn default_version() -> Self {
        Self::SingleTick
    }

    /// Parses a version name.
    pub fn parse(value: &str) -> Result<Self, String> {
        match value.trim().to_lowercase().as_str() {
            "single_tick" | "single-tick" | "v1" | "1" => Ok(Self::SingleTick),
            other => Err(format!("unknown arbiter version: {other}")),
        }
    }
}

/// Whether a pool charges a dynamic fee.
///
/// Uniswap V4 marks a dynamic-fee pool with `DYNAMIC_FEE_FLAG` in the
/// `PoolKey.fee` field. The effective rate of such a pool is decided by its
/// hook at swap time, so it cannot be reproduced off chain from the stored
/// state. A dynamic-fee pool is therefore never a candidate, never a
/// recommendation and never a backup: the off-chain arithmetic would price a
/// rate the chain does not use.
///
/// The check is the single source of truth for that rule. The admission
/// path, the pool role materialization and the depth arithmetic all consult
/// it, so the three can never drift apart.
pub fn is_dynamic_fee_pool(fee: i64, dynamic_fee: i64) -> bool {
    dynamic_fee != 0 || (fee as u32 & manager_contracts::V4_DYNAMIC_FEE_FLAG) != 0
}

/// Runs one exact-input swap step through the Uniswap V4 SDK maths.
///
/// The SDK's `compute_swap_step` needs a price target, which for a capacity
/// probe is the far end of the current tick range. The probe instead uses
/// the SDK's `get_next_sqrt_price_from_input` and the matching amount delta
/// helpers, which is the exact same arithmetic the swap loop performs.
/// Returns the realised output of one exact-input swap step.
///
/// This is the pool arithmetic, so it lives in Pool Manager and is published
/// here for the modules that need it (Review Manager's off-chain parity
/// comparison, for example). The SDK maths is the primary path; the
/// self-contained fallback mirrors its rounding rules exactly.
///
/// The output is the realised output of the swap, i.e. it already carries the
/// price impact and the liquidity-provider fee, exactly like the chain.
pub fn swap_amount_out(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Result<u128, String> {
    match sdk_swap_amount_out(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        amount_in_raw,
        zero_for_one,
    ) {
        Ok(value) => Ok(value),
        Err(_) => fallback_swap_amount_out(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            amount_in_raw,
            zero_for_one,
        ),
    }
}

/// Returns the realised output of one exact-input swap step.
///
/// This is the **canonical Uniswap exact-input arithmetic**, taken from the
/// official SDK rather than re-derived here:
///
/// 1. the fee is taken off the input with
///    `mul_div(amount_in, 1_000_000 - fee_pips, 1_000_000)`;
/// 2. the next sqrt price comes from
///    `get_next_sqrt_price_from_input`, which is the SDK's exact-input
///    primitive (`add = true` on the amount0 step, `add = true` on the
///    amount1 step);
/// 3. the realised output is the corresponding `get_amount_*_delta` with
///    `round_up = false`.
///
/// That is precisely the sequence the Uniswap V4 `Pool` contract follows for
/// an exact-input swap, so this reproduces the chain's settlement exactly.
///
/// The SDK is the authoritative path. The self-contained fallback mirrors the
/// same rounding rules and is only used if the SDK maths is unavailable for a
/// given build; the two are locked down by the same test vectors.
pub fn exact_input_swap_amount_out(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Result<u128, String> {
    match sdk_swap_amount_out(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        amount_in_raw,
        zero_for_one,
    ) {
        Ok(value) => Ok(value),
        Err(_) => fallback_swap_amount_out(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            amount_in_raw,
            zero_for_one,
        ),
    }
}

/// The SDK-backed swap maths.
fn sdk_swap_amount_out(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Result<u128, String> {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;
    use uniswap_v4_sdk::prelude::v3_sdk::utils::sqrt_price_math as spm;

    let sqrt_price = alloy_primitives::U256::from(sqrt_price_x96);
    let fee_complement = 1_000_000u64.saturating_sub(fee_pips as u64);
    if fee_complement == 0 {
        return Err("fee consumes the entire input".to_string());
    }
    // The amount that actually reaches the curve once the fee is taken.
    let amount_in_less_fee = mul_div(
        alloy_primitives::U256::from(amount_in_raw),
        alloy_primitives::U256::from(fee_complement),
        alloy_primitives::U256::from(1_000_000u64),
    )
    .map_err(|error| format!("fee adjustment failed: {error:?}"))?;
    if amount_in_less_fee.is_zero() {
        return Err("input amount is below the fee resolution".to_string());
    }

    let sqrt_next = if zero_for_one {
        spm::get_next_sqrt_price_from_amount_0_rounding_up(
            sqrt_price,
            liquidity,
            amount_in_less_fee,
            true,
        )
    } else {
        spm::get_next_sqrt_price_from_amount_1_rounding_down(
            sqrt_price,
            liquidity,
            amount_in_less_fee,
            true,
        )
    }
    .map_err(|error| format!("next sqrt price failed: {error:?}"))?;

    let amount_out = if zero_for_one {
        spm::get_amount_1_delta(sqrt_next, sqrt_price, liquidity, false)
    } else {
        spm::get_amount_0_delta(sqrt_next, sqrt_price, liquidity, false)
    }
    .map_err(|error| format!("amount delta failed: {error:?}"))?;

    to_u128_checked(amount_out, "the amount delta")
}

/// Runs one exact-input swap step through the self-contained fallback maths.
///
/// The fallback reproduces the Uniswap rounding rules with 256-bit
/// intermediates. It exists so the pool tool can still produce a capacity
/// verdict on a build where the SDK maths is unavailable.
fn fallback_swap_amount_out(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Result<u128, String> {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;

    let fee_complement = 1_000_000u64.saturating_sub(fee_pips as u64);
    if fee_complement == 0 {
        return Err("fee consumes the entire input".to_string());
    }
    let amount_in_less_fee = mul_div(
        alloy_primitives::U256::from(amount_in_raw),
        alloy_primitives::U256::from(fee_complement),
        alloy_primitives::U256::from(1_000_000u64),
    )
    .map_err(|error| format!("fee adjustment failed: {error:?}"))?;
    if amount_in_less_fee.is_zero() {
        return Err("input amount is below the fee resolution".to_string());
    }
    let sqrt_price = alloy_primitives::U256::from(sqrt_price_x96);
    let sqrt_next = if zero_for_one {
        fallback_next_sqrt_price_from_amount0_rounding_up(sqrt_price, liquidity, amount_in_less_fee)
    } else {
        fallback_next_sqrt_price_from_amount1_rounding_down(
            sqrt_price,
            liquidity,
            amount_in_less_fee,
        )
    };
    let amount_out = if zero_for_one {
        fallback_amount1_delta(sqrt_next, sqrt_price, liquidity)
    } else {
        fallback_amount0_delta(sqrt_next, sqrt_price, liquidity)
    };
    to_u128_checked(amount_out, "the fallback amount delta")
}

/// Next sqrt price after adding token0, rounded up.
fn fallback_next_sqrt_price_from_amount0_rounding_up(
    sqrt_price_x96: alloy_primitives::U256,
    liquidity: u128,
    amount_in_raw: alloy_primitives::U256,
) -> alloy_primitives::U256 {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div_rounding_up;

    if amount_in_raw.is_zero() {
        return sqrt_price_x96;
    }
    let numerator1 = liquidity_q96(liquidity);
    let product = sqrt_price_x96.saturating_mul(amount_in_raw);
    let denominator = numerator1.saturating_add(product);
    if denominator.is_zero() || denominator < numerator1 {
        return sqrt_price_x96;
    }
    // The SDK rounds this division up; the fallback must round identically so
    // both maths paths agree on the realised output.
    let quotient = mul_div_rounding_up(numerator1, sqrt_price_x96, denominator)
        .unwrap_or(alloy_primitives::U256::ZERO);
    if quotient >= sqrt_price_x96 {
        sqrt_price_x96
    } else {
        quotient
    }
}

/// Next sqrt price after adding token1, rounded down.
fn fallback_next_sqrt_price_from_amount1_rounding_down(
    sqrt_price_x96: alloy_primitives::U256,
    liquidity: u128,
    amount_in_raw: alloy_primitives::U256,
) -> alloy_primitives::U256 {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;

    if amount_in_raw.is_zero() {
        return sqrt_price_x96;
    }
    // The canonical formula is `sqrtP + amount * Q96 / L`. The small-amount
    // branch of the SDK uses a plain shift and division; the large-amount
    // branch needs full precision, which `mul_div` provides.
    let quotient = if amount_in_raw <= U160_MAX {
        (amount_in_raw << 96) / alloy_primitives::U256::from(liquidity)
    } else {
        mul_div(amount_in_raw, Q96, alloy_primitives::U256::from(liquidity))
            .unwrap_or(alloy_primitives::U256::ZERO)
    };
    sqrt_price_x96.saturating_add(quotient)
}

/// `L * (sqrtB - sqrtA) / Q96`, rounded down.
fn fallback_amount1_delta(
    sqrt_a: alloy_primitives::U256,
    sqrt_b: alloy_primitives::U256,
    liquidity: u128,
) -> alloy_primitives::U256 {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;

    let (low, high) = if sqrt_a <= sqrt_b {
        (sqrt_a, sqrt_b)
    } else {
        (sqrt_b, sqrt_a)
    };
    // `L * (sqrtB - sqrtA) / Q96` needs a full-precision intermediate, so the
    // canonical `mul_div` is used instead of a plain multiplication.
    mul_div(alloy_primitives::U256::from(liquidity), high - low, Q96)
        .unwrap_or(alloy_primitives::U256::ZERO)
}

/// `L * (sqrtB - sqrtA) * Q96 / (sqrtA * sqrtB)`, rounded down.
fn fallback_amount0_delta(
    sqrt_a: alloy_primitives::U256,
    sqrt_b: alloy_primitives::U256,
    liquidity: u128,
) -> alloy_primitives::U256 {
    use uniswap_v4_sdk::prelude::v3_sdk::utils::full_math::mul_div;

    let (low, high) = if sqrt_a <= sqrt_b {
        (sqrt_a, sqrt_b)
    } else {
        (sqrt_b, sqrt_a)
    };
    if low.is_zero() {
        return alloy_primitives::U256::ZERO;
    }
    let numerator1 = liquidity_q96(liquidity);
    let numerator2 = high - low;
    let intermediate =
        mul_div(numerator1, numerator2, high).unwrap_or(alloy_primitives::U256::ZERO);
    intermediate / low
}

/// Renders a raw token amount as a decimal string using the token decimals.
pub fn raw_to_decimal_string(raw: u128, decimals: u32) -> String {
    if decimals == 0 {
        return raw.to_string();
    }
    let divisor = 10u128.pow(decimals);
    let whole = raw / divisor;
    let fraction = raw % divisor;
    if fraction == 0 {
        return whole.to_string();
    }
    let mut fraction_text = format!("{fraction:0width$}", width = decimals as usize);
    while fraction_text.ends_with('0') {
        fraction_text.pop();
    }
    format!("{whole}.{fraction_text}")
}

/// Builds the evidence payload stored alongside a pool snapshot.
pub fn capacity_json(capacity: &Capacity, tick: i64, sqrt_price_x96: &str) -> Value {
    json!({
        "capacityToken0Raw": capacity.capacity_token0_raw.to_string(),
        "capacityToken1Raw": capacity.capacity_token1_raw.to_string(),
        "capacityRaw": capacity.best_raw().to_string(),
        "priceImpactBps": capacity.price_impact_bps,
        "priceImpactLimitBps": PRICE_IMPACT_LIMIT_BPS,
        "feePips": capacity.fee_pips,
        "hasLiquidity": capacity.has_liquidity,
        "tick": tick,
        "sqrtPriceX96": sqrt_price_x96,
        "depthBasis": DEPTH_BASIS,
        // The tick-range bounds are the gate of the first arbitrage
        // version. They are published with the capacity so the consumer
        // does not have to recompute the range itself.
        "singleTickMaxToken0Raw": capacity.single_tick_max_token0_raw.map(|v| v.to_string()),
        "singleTickMaxToken1Raw": capacity.single_tick_max_token1_raw.map(|v| v.to_string()),
        "effectiveMaxToken0Raw": capacity.effective_max_raw(true).map(|v| v.to_string()),
        "effectiveMaxToken1Raw": capacity.effective_max_raw(false).map(|v| v.to_string()),
        "error": capacity.error,
    })
}

#[cfg(test)]
mod single_tick_tests {
    use super::*;

    /// The tick range of a pool is bounded by the neighbouring multiples of
    /// the tick spacing, because the pool may only change its liquidity on
    /// a tick that is a multiple of that spacing.
    #[test]
    fn the_tick_range_is_bounded_by_the_tick_spacing() {
        // The price at tick 1005 sits between the boundaries 1000 and 1010.
        let price_at_1005 = sqrt_ratio_at_tick(1005).expect("price");
        let bounds = tick_range_bounds(1005, 10, price_at_1005).expect("bounds");
        assert_eq!(bounds.lower_tick, 1000);
        assert_eq!(bounds.upper_tick, 1010);
        assert!(bounds.lower_sqrt_price_x96 < bounds.upper_sqrt_price_x96);
        // A negative tick floors towards negative infinity.
        let price_at_minus_1005 = sqrt_ratio_at_tick(-1005).expect("price");
        let bounds = tick_range_bounds(-1005, 10, price_at_minus_1005).expect("bounds");
        assert_eq!(bounds.lower_tick, -1010);
        assert_eq!(bounds.upper_tick, -1000);
    }

    /// A price that sits exactly on a spacing boundary has no room on that
    /// side, so the boundary moves one step outwards: the range always has
    /// room in both directions.
    #[test]
    fn a_price_on_a_boundary_still_has_room_in_both_directions() {
        let price_at_0 = sqrt_ratio_at_tick(0).expect("price");
        let bounds = tick_range_bounds(0, 60, price_at_0).expect("bounds");
        assert!(bounds.lower_sqrt_price_x96 < price_at_0);
        assert!(bounds.upper_sqrt_price_x96 > price_at_0);
        // The price the range offers must be reachable in both directions.
        let liquidity = 1_000_000_000_000_000_000u128;
        let down = single_tick_swap_amount_out(price_at_0, liquidity, 3000, 60, 0, 1_000, true)
            .expect("down");
        let up = single_tick_swap_amount_out(price_at_0, liquidity, 3000, 60, 0, 1_000, false)
            .expect("up");
        assert!(down.inside_tick_range);
        assert!(up.inside_tick_range);
    }

    /// The single-tick pricing must agree with the single-step arithmetic
    /// while the swap stays inside the range: inside one range the
    /// liquidity is constant, so the two are the same arithmetic.
    #[test]
    fn the_single_tick_pricing_matches_the_single_step_arithmetic() {
        let sqrt_price_x96 = 79_228_162_514_264_337_593_543_950_336u128;
        let liquidity = 1_000_000_000_000_000_000u128;
        let fee_pips = 3000u32;
        let amount_in = 1_000_000_000_000u128;
        let quote = single_tick_swap_amount_out(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            60,
            0,
            amount_in,
            false,
        )
        .expect("quote");
        let single_step =
            exact_input_swap_amount_out(sqrt_price_x96, liquidity, fee_pips, amount_in, false)
                .expect("single step");
        assert_eq!(quote.amount_out_raw, single_step);
        assert!(quote.inside_tick_range);
    }

    /// A swap that would leave the tick range is **not** executable in the
    /// first version: the gate has to report it rather than let the
    /// orchestrator use a price that does not hold.
    #[test]
    fn a_swap_that_leaves_the_tick_range_is_reported() {
        let sqrt_price_x96 = 79_228_162_514_264_337_593_543_950_336u128;
        let liquidity = 1_000_000_000_000_000_000u128;
        let quote = single_tick_swap_amount_out(
            sqrt_price_x96,
            liquidity,
            3000,
            60,
            0,
            u128::MAX / 4,
            false,
        )
        .expect("quote");
        assert!(!quote.inside_tick_range);
        assert!(quote.max_amount_in_raw < u128::MAX / 4);
    }

    /// The capacity publishes the tick-range bound, so the consumer does
    /// not have to recompute the range and the gate cannot drift from the
    /// price.
    #[test]
    fn the_capacity_publishes_the_tick_range_bound() {
        let sqrt_price_x96 = 79_228_162_514_264_337_593_543_950_336u128;
        let state = V4PoolState {
            pool_id: "0x0".to_string(),
            sqrt_price_x96: sqrt_price_x96.to_string(),
            tick: 0,
            protocol_fee: 0,
            lp_fee: 3000,
            liquidity: 1_000_000_000_000_000_000,
            has_liquidity: true,
            error: None,
            complete: true,
        };
        let capacity = evaluate_capacity(&state, 10.0, 60);
        let down = capacity
            .single_tick_max_token0_raw
            .expect("the downward bound");
        let up = capacity
            .single_tick_max_token1_raw
            .expect("the upward bound");
        assert!(down > 0);
        assert!(up > 0);
        // The effective bound is the smaller of the tick bound and the
        // depth capacity, because exceeding either one makes the price
        // untrue.
        let effective_down = capacity.effective_max_raw(true).expect("effective");
        assert!(effective_down <= down);
        assert!(effective_down <= capacity.capacity_token0_raw);
        // A swap that stays inside the bound is executable in version 1.
        let quote = single_tick_swap_amount_out(
            sqrt_price_x96,
            state.liquidity,
            3000,
            60,
            0,
            effective_down,
            true,
        )
        .expect("quote");
        assert!(quote.inside_tick_range);
    }

    /// A pool without a tick cannot be located inside a tick range. Version
    /// 1 is single-tick only, so missing tick evidence must fail closed
    /// instead of falling back to the price-impact capacity.
    #[test]
    fn a_pool_without_a_tick_has_no_effective_capacity() {
        let capacity = Capacity {
            capacity_token0_raw: 500,
            capacity_token1_raw: 700,
            ..Default::default()
        };
        assert_eq!(capacity.effective_max_raw(true), None);
        assert_eq!(capacity.effective_max_raw(false), None);
    }

    /// The version gate must recognise its own names and refuse the rest.
    #[test]
    fn the_arbiter_version_is_recognised() {
        assert_eq!(
            ArbiterVersion::parse("single_tick").expect("version"),
            ArbiterVersion::SingleTick
        );
        assert_eq!(
            ArbiterVersion::parse("v1").expect("version"),
            ArbiterVersion::SingleTick
        );
        assert_eq!(
            ArbiterVersion::default_version(),
            ArbiterVersion::SingleTick
        );
        assert!(ArbiterVersion::parse("tick_traversal").is_err());
    }
}

#[cfg(test)]
mod capacity_regression_tests {
    use super::*;

    /// Live state of pool `0x2077...` on the Robinhood mainnet, read through
    /// `extsload` on 2026-11-11.
    const SQRT_PRICE_X96: u128 = 79220114170182869900576992101;
    const LIQUIDITY: u128 = 99778947815985782472265;
    const FEE_PIPS: u32 = 500;

    #[test]
    fn a_saturated_amount_is_not_reported_as_zero_price_impact() {
        // A raw amount near `u128::MAX` saturates the next-price
        // calculation at the tick boundary. It must not read as "no impact",
        // because that made the capacity search report an unbounded pool.
        let impact = price_impact_for_input(
            SQRT_PRICE_X96,
            LIQUIDITY,
            FEE_PIPS,
            340_282_366_920_938_463_463_374_607_431_768_211_454,
            true,
        );
        assert!(
            impact.is_err(),
            "a saturated amount must be an error, got {impact:?}"
        );
    }

    #[test]
    fn the_capacity_of_a_live_pool_is_finite_and_plausible() {
        let capacity = capacity_from_sqrt_price(SQRT_PRICE_X96, LIQUIDITY, FEE_PIPS, 10.0)
            .expect("capacity must be computable");
        assert!(capacity.has_liquidity);
        assert!(
            capacity.capacity_token0_raw < 340_282_366_920_938_463_463_374_607_431_768_211_454,
            "capacity must not saturate, got {}",
            capacity.capacity_token0_raw
        );
        assert!(
            capacity.price_impact_bps <= 10.0 + 1e-9,
            "impact must stay within the limit, got {}",
            capacity.price_impact_bps
        );
        assert!(
            capacity.capacity_token0_raw > 0 && capacity.capacity_token1_raw > 0,
            "both sides must have capacity, got {capacity:?}"
        );
    }

    #[test]
    fn the_capacity_bracket_never_exceeds_the_reserve_ceiling() {
        let ceiling = reserve_ceiling(SQRT_PRICE_X96, LIQUIDITY, true);
        let capacity = capacity_from_sqrt_price(SQRT_PRICE_X96, LIQUIDITY, FEE_PIPS, 10.0)
            .expect("capacity must be computable");
        assert!(
            capacity.capacity_token0_raw <= ceiling,
            "capacity {} exceeded the ceiling {ceiling}",
            capacity.capacity_token0_raw
        );
    }

    #[test]
    fn a_pool_without_liquidity_has_no_capacity() {
        let capacity = evaluate_capacity(
            &V4PoolState {
                pool_id: "0x0".to_string(),
                sqrt_price_x96: SQRT_PRICE_X96.to_string(),
                tick: -3,
                protocol_fee: 0,
                lp_fee: FEE_PIPS as u64,
                liquidity: 0,
                has_liquidity: false,
                error: None,
                complete: true,
            },
            10.0,
            60,
        );
        assert!(!capacity.has_liquidity);
        assert_eq!(capacity.best_raw(), 0);
        assert!(capacity.error.is_some());
    }

    /// The SDK maths and the self-contained fallback must agree exactly.
    ///
    /// The off-chain parity comparison reproduces the chain's settlement, so
    /// a disagreement between the two maths paths would be a silent source of
    /// false parity deviations. This test pins them together over a range of
    /// prices, liquidity levels, fees and input sizes.
    #[test]
    fn the_sdk_and_the_fallback_agree_on_the_exact_input_swap() {
        const Q96_U128: u128 = 1u128 << 96;
        let sqrt_prices = [
            Q96_U128,
            Q96_U128 / 2,
            Q96_U128 * 2,
            79_228_162_514_264_337_593_543_950_336,
            4_000_000_000_000_000_000,
        ];
        let liquidities = [
            100_000_000_000_000_000_000u128,
            1_000_000_000_000_000_000_000u128,
            12_345_678_901_234_567_890u128,
        ];
        let fees = [0u32, 3000u32, 10_000u32];
        let inputs = [
            1_000_000_000_000_000u128,
            1_000_000_000_000_000_000u128,
            1_000_000_000_000_000_000_000u128,
        ];

        let mut compared = 0usize;
        for sqrt_price in sqrt_prices {
            for liquidity in liquidities {
                for fee in fees {
                    for amount_in in inputs {
                        for zero_for_one in [true, false] {
                            let sdk = sdk_swap_amount_out(
                                sqrt_price,
                                liquidity,
                                fee,
                                amount_in,
                                zero_for_one,
                            )
                            .expect("the SDK maths must handle the fixture");
                            let fallback = fallback_swap_amount_out(
                                sqrt_price,
                                liquidity,
                                fee,
                                amount_in,
                                zero_for_one,
                            )
                            .expect("the fallback maths must handle the fixture");
                            assert_eq!(
                                sdk, fallback,
                                "SDK and fallback disagreed at sqrt_price={sqrt_price} liquidity={liquidity} fee={fee} amount_in={amount_in} zero_for_one={zero_for_one}"
                            );
                            compared += 1;
                        }
                    }
                }
            }
        }
        assert!(
            compared >= 270,
            "the equivalence sweep must cover the fixtures, compared={compared}"
        );
    }

    /// The published exact-input entry point is the SDK path, and it agrees
    /// with the SDK maths directly.
    #[test]
    fn the_published_exact_input_swap_uses_the_sdk_result() {
        let sqrt_price = 79_228_162_514_264_337_593_543_950_336u128;
        let liquidity = 100_000_000_000_000_000_000u128;
        let amount_in = 1_000_000_000_000_000_000u128;
        for zero_for_one in [true, false] {
            let published =
                exact_input_swap_amount_out(sqrt_price, liquidity, 3000, amount_in, zero_for_one)
                    .expect("the published path must succeed");
            let sdk = sdk_swap_amount_out(sqrt_price, liquidity, 3000, amount_in, zero_for_one)
                .expect("the SDK maths must succeed");
            assert_eq!(published, sdk);
        }
    }
}
#[cfg(test)]
mod tests {
    #[test]
    fn an_amount_that_does_not_fit_in_128_bits_is_an_error_and_not_a_panic() {
        // The chain can report a value that does not fit in 128 bits. An
        // unchecked cast would **panic** and abort the process that asked
        // for a measurement, which is exactly what a measurement must never
        // do. The overflow is therefore reported as an error.
        let overflow = alloy_primitives::U256::from_limbs([u64::MAX, u64::MAX, u64::MAX, 1]);
        let error = super::to_u128_checked(overflow, "the test amount")
            .expect_err("a 256-bit value must not fit in 128 bits");
        assert!(error.contains("does not fit in 128 bits"));

        // A value that does fit is returned unchanged.
        let value = alloy_primitives::U256::from(42u64);
        assert_eq!(
            super::to_u128_checked(value, "the test amount").expect("value"),
            42
        );
    }

    #[test]
    fn a_160_bit_price_is_narrowed_without_a_panic() {
        // The price at a tick is a 160-bit value. It always fits in 128
        // bits in practice, and the narrowing reports an error instead of
        // panicking when it does not.
        let price = alloy_primitives::aliases::U160::from(1_000_000u64);
        assert_eq!(
            super::to_u128_checked(price, "the test price").expect("price"),
            1_000_000
        );
    }
}
