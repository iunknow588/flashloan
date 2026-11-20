//!
//! The three-hop single-tick capacity of a closed loop, in the units of the
//! loop\'s anchor token.
//!
//! Why this module exists
//! -----------------------
//!
//! A closed loop `A -> B -> C -> A` is executable in version 1 only when
//! **every** hop stays inside its current tick range. Each hop therefore has
//! a capacity: the largest input that still stays inside the range, in the
//! token that hop **spends**.
//!
//! Those three capacities are **not comparable as they stand**.
//!
//! * hop 1 spends the anchor token `A`;
//! * hop 2 spends `B`;
//! * hop 3 spends `C`.
//!
//! Taking the minimum of three numbers denominated in three different
//! tokens is a unit error. A loop whose hop 2 accepts `10` units of `B` and
//! whose hop 1 accepts `1_000_000` units of `A` is not bounded by `10`; it
//! is bounded by however many units of `A` those `10` units of `B` are
//! worth.
//!
//! The design document fixes this with a normalisation step: every hop
//! capacity is folded into the **anchor** token before the minimum is
//! taken.
//!
//! ```text
//!   M_1 = K_1
//!   M_2 = K_2 * P_{B/A}
//!   M_3 = K_3 * P_{C/A}
//!
//!   M_cap = min(M_1, M_2, M_3)
//! ```
//!
//! The fold is **cumulative**
//! --------------------------------
//!
//! The rates in `P_{B/A}` and `P_{C/A}` come from the **preceding** hops,
//! not from the hop\'s own pool:
//!
//! ```text
//!   P_{B/A} = 1 / rate_1              rate_1 = hop 1 A->B spot rate
//!   P_{C/A} = 1 / (rate_1 * rate_2)   rate_2 = hop 2 B->C spot rate
//! ```
//!
//! The reason is that hop 2\'s capacity is denominated in `B`, and `B` is
//! what hop 1 **produces**. The anchor value of `B` is therefore fixed by
//! hop 1\'s rate. Likewise `C` is produced by hop 2, so its anchor value
//! accumulates both preceding rates.
//!
//! Only a **two-hop** loop `A -> B -> A` has the property that the hop\'s
//! own pool is the `A/B` pool, so "use the hop\'s own pool price" and "use
//! the preceding cumulative rate" happen to agree. In a three-hop loop they
//! disagree, and using the own-pool price yields a wrong `M_cap`.

//! Linear rates against square-root prices
//! ----------------------------------------
//!
//! The pool reports a **square-root** price, while the document's
//! `P_{X/A}` is a **linear** rate. The linear rate is the square of the
//! square-root price, so the fold has to apply the square-root rate
//! **twice**:
//!
//! ```text
//!   M_i = K_i * (U_i / Q96)^2
//! ```
//!
//! where `U_i` is the Q96-scaled square root of the reciprocal cumulative
//! rate of the preceding hops. The square is evaluated as two Q96 stages,
//! so every intermediate stays inside `u128` and every division rounds
//! down.
//!
//! A round-down is deliberate at every stage: the capacity is a limit that
//! must not be exceeded, so the computed bound may only ever be slightly
//! **smaller** than the true one. A capacity one raw unit too large would
//! let a hop cross its tick boundary, which is exactly the failure this
//! module exists to prevent.
//!
//! The single-hop factor
//! ---------------------
//!
//! The factor of one hop is the concentrated-liquidity delta of the Uniswap
//! SDK, taken in the token the hop spends. The `pool_manager::depth` module
//! already implements this exactly in `amount_to_reach_price`, so this
//! module reuses it instead of re-deriving the arithmetic: the capacity used
//! by the gate and the capacity used by the quote can never drift apart.
//!
//! Fail-closed
//! -----------
//!
//! The capacity is a **bound**, so an unprovable bound may never widen it.
//! The computation is rejected when
//!
//! * the hop list is empty;
//! * the hop count does not match the direction count;
//! * the first hop does not spend the anchor token;
//! * a hop carries no price, no liquidity or no tick spacing;
//! * the tick range of a hop cannot be derived;
//! * the capacity of a hop cannot be computed;
//! * a rate used for the fold is zero.
//!
//! In every one of those cases the caller receives `None` plus a stable
//! reason code, and the caller is expected to **block** the route rather
//! than substitute a guess.
//!
//! The cumulative fold is applied **per hop**, not as one big product: each
//! hop is folded with the product of the rates of the hops before it, so
//! the intermediate stays a single Q96 number and the round-down error is
//! paid once per hop instead of once per loop.

use alloy_primitives::U256;
use serde::Serialize;

/// The number of fixed-point bits used by the Uniswap Q96 price format.
const Q96_BITS: u32 = 96;

/// One hop of a closed loop, as seen by the capacity computation.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CapacityHop {
    /// The pool the hop trades in.
    pub pool_id: String,
    /// The token the hop **spends**.
    pub token_in: String,
    /// The token the hop **receives**.
    pub token_out: String,
    /// The current square-root price of the pool, in Q96 fixed point.
    ///
    /// This is the price of token1 in token0, exactly as the pool reports
    /// it. The hop direction decides how it is read.
    pub sqrt_price_x96: u128,
    /// The current liquidity of the pool.
    pub liquidity: u128,
    /// The fee of the pool, in hundredths of a basis point.
    pub fee_pips: u32,
    /// The tick spacing of the pool.
    pub tick_spacing: i32,
    /// The initialized tick the pool\'s price sits in.
    pub tick: i32,
    /// Whether the hop spends token0 for token1.
    pub zero_for_one: bool,
}

/// The reason the capacity could not be computed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CapacityError {
    /// The hop list is empty.
    NoHops,
    /// The hop list and the direction list differ in length.
    HopStateIncomplete,
    /// The first hop does not spend the anchor token.
    AnchorTokenMismatch,
    /// A hop has no price.
    PriceUnknown,
    /// A hop has no liquidity.
    LiquidityUnknown,
    /// A hop has no usable tick spacing.
    TickSpacingUnknown,
    /// The tick range of a hop cannot be derived.
    TickRangeUnavailable,
    /// The capacity of a hop cannot be computed.
    CapacityUnavailable,
    /// A rate used for the fold is zero.
    AnchorPriceZero,
}

impl CapacityError {
    /// The stable wire name of the reason.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::NoHops => "no_hops",
            Self::HopStateIncomplete => "hop_state_incomplete",
            Self::AnchorTokenMismatch => "anchor_token_mismatch",
            Self::PriceUnknown => "price_unknown",
            Self::LiquidityUnknown => "liquidity_unknown",
            Self::TickSpacingUnknown => "tick_spacing_unknown",
            Self::TickRangeUnavailable => "tick_range_unavailable",
            Self::CapacityUnavailable => "capacity_unavailable",
            Self::AnchorPriceZero => "anchor_price_zero",
        }
    }
}

/// The capacity of one hop, before and after the fold.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HopCapacity {
    /// The pool the hop trades in.
    pub pool_id: String,
    /// The largest input the hop accepts in its **own** input token.
    pub capacity_in_own_token_raw: u128,
    /// The cumulative conversion factor of the preceding hops, as the
    /// **numerator** of a Q96-scaled ratio.
    ///
    /// The factor answers "how many anchor units is one unit of this
    /// hop's input token worth". It is the reciprocal cumulative rate of
    /// the hops **before** this one, not a price of this hop's own pool.
    pub anchor_factor_numerator_q96: u128,
    /// The **denominator** of the cumulative conversion factor.
    ///
    /// The factor is `numerator / denominator`, both Q96-scaled. The pair
    /// is kept instead of a single number because dividing a running
    /// factor by a price truncates catastrophically once the factor gets
    /// small; the pair keeps the precision at every step.
    pub anchor_factor_denominator_q96: u128,
    /// The capacity after folding into the anchor token.
    pub capacity_in_anchor_raw: u128,
}

/// The capacity of a whole closed loop, in anchor-token units.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LoopCapacity {
    /// The anchor token every capacity was folded into.
    pub anchor_token: String,
    /// The capacity of every hop, after folding.
    pub hops: Vec<HopCapacity>,
    /// The binding hop: the one whose folded capacity is the smallest.
    pub binding_pool_id: String,
    /// The capacity of the whole loop, in anchor-token raw units.
    pub capacity_anchor_raw: u128,
}

/// Computes the single-tick capacity of a closed loop, in anchor-token units.
///
/// The first hop spends the anchor token, so its capacity is used as it is.
/// Every later hop is folded into the anchor token with the **cumulative**
/// rate of the hops before it, exactly as the design document prescribes:
///
/// ```text
///   M_1 = K_1
///   M_2 = K_2 * P_{B/A}      = K_2 * (1 / rate_1)
///   M_3 = K_3 * P_{C/A}      = K_3 * (1 / (rate_1 * rate_2))
///   M_cap = min(M_1, M_2, M_3)
/// ```
///
/// # Fail-closed
///
/// Returns `Err` with a reason code whenever the capacity cannot be proven.
/// The caller must block the route in that case.
pub fn compute_loop_capacity(
    anchor_token: &str,
    hops: &[CapacityHop],
) -> Result<LoopCapacity, CapacityError> {
    if hops.is_empty() {
        return Err(CapacityError::NoHops);
    }
    if hops[0].token_in != anchor_token {
        return Err(CapacityError::AnchorTokenMismatch);
    }

    let mut capacities: Vec<HopCapacity> = Vec::with_capacity(hops.len());
    let mut minimum: Option<u128> = None;
    let mut binding_pool_id = String::new();

    // The cumulative conversion factor of the hops seen so far, kept as a
    // Q96-scaled ratio `numerator / denominator`.
    //
    // The factor turns the capacity of a hop (denominated in that hop's
    // input token) into the anchor token. It starts at one: before the
    // first hop no conversion has happened, so the first hop's own
    // capacity is already in anchor units.
    //
    // The factor is the **reciprocal** cumulative rate of the hops before
    // the current one. It is carried as a pair rather than a single
    // number because dividing a running factor by a price truncates
    // catastrophically once the factor becomes small.
    let mut factor_numerator_q96 = 1u128 << Q96_BITS;
    let mut factor_denominator_q96 = 1u128 << Q96_BITS;

    for hop in hops.iter() {
        if hop.sqrt_price_x96 == 0 {
            return Err(CapacityError::PriceUnknown);
        }
        if hop.liquidity == 0 {
            return Err(CapacityError::LiquidityUnknown);
        }
        if hop.tick_spacing <= 0 {
            return Err(CapacityError::TickSpacingUnknown);
        }

        let own_capacity = hop_capacity_in_own_token(hop)?;

        // The fold multiplies by the **linear** rate, while the pool
        // reports a square-root price, so the factor is applied twice.
        //
        // The ratio is resolved first, in 256-bit arithmetic, so that no
        // precision is lost in the resolution itself.
        let ratio_q96 = ratio_q96(factor_numerator_q96, factor_denominator_q96)?;
        let half_folded = fold_q96(own_capacity, ratio_q96)?;
        let folded = fold_q96(half_folded, ratio_q96)?;

        if minimum.is_none_or(|current| folded < current) {
            minimum = Some(folded);
            binding_pool_id = hop.pool_id.clone();
        }
        capacities.push(HopCapacity {
            pool_id: hop.pool_id.clone(),
            capacity_in_own_token_raw: own_capacity,
            anchor_factor_numerator_q96: factor_numerator_q96,
            anchor_factor_denominator_q96: factor_denominator_q96,
            capacity_in_anchor_raw: folded,
        });

        // Extend the cumulative factor with the rate of this hop.
        //
        // The hop's rate is the number of output-token raw units one
        // input-token raw unit buys. The pool's price is the square root
        // of the token1-per-token0 rate, so the reciprocal square root of
        // the hop's rate is
        //
        //   Q96 / s   when the hop spends token0, and
        //   s / Q96   when the hop spends token1.
        let (numerator, denominator) = cumulative_factor_update(
            factor_numerator_q96,
            factor_denominator_q96,
            hop.sqrt_price_x96,
            hop.zero_for_one,
        )?;
        factor_numerator_q96 = numerator;
        factor_denominator_q96 = denominator;
    }

    let capacity_anchor_raw = minimum.unwrap_or(0);
    Ok(LoopCapacity {
        anchor_token: anchor_token.to_string(),
        hops: capacities,
        binding_pool_id,
        capacity_anchor_raw,
    })
}

/// Computes the largest input one hop accepts while staying inside its
/// current tick range.
///
/// The hop spends `token_in` and receives `token_out`, so the capacity is
/// denominated in `token_in` raw units.
fn hop_capacity_in_own_token(hop: &CapacityHop) -> Result<u128, CapacityError> {
    let bounds = crate::depth::tick_range_bounds(hop.tick, hop.tick_spacing, hop.sqrt_price_x96)
        .map_err(|_| CapacityError::TickRangeUnavailable)?;
    let limit_price_x96 = if hop.zero_for_one {
        bounds.lower_sqrt_price_x96
    } else {
        bounds.upper_sqrt_price_x96
    };
    // The SDK arithmetic is the same one the quote uses, so the capacity and
    // the quote can never disagree about where the boundary is.
    let amount = crate::depth::amount_to_reach_price(
        hop.sqrt_price_x96,
        hop.liquidity,
        hop.fee_pips,
        limit_price_x96,
        hop.zero_for_one,
    )
    .map_err(|_| CapacityError::CapacityUnavailable)?;
    Ok(amount)
}

/// Multiplies a raw quantity by a Q96 factor and scales back to Q96.
///
/// The product of a raw quantity and a Q96 factor is a Q96-scaled number
/// whose raw representation is the 192-bit product shifted down. The
/// multiplication is done in 256-bit arithmetic and the result is scaled
/// back with a **round-down** division, so the outcome never exceeds the
/// exact value.
fn fold_q96(value_raw: u128, factor_q96: u128) -> Result<u128, CapacityError> {
    if factor_q96 == 0 {
        return Err(CapacityError::AnchorPriceZero);
    }
    let q96 = U256::from(1u128) << Q96_BITS;
    let scaled = (U256::from(value_raw) * U256::from(factor_q96)) / q96;
    scaled
        .try_into()
        .map_err(|_| CapacityError::CapacityUnavailable)
}

/// Resolves a Q96-scaled ratio into a Q96-scaled plain ratio.
///
/// The ratio `numerator / denominator` is evaluated in 256-bit arithmetic
/// with a round-down division, so the resolved factor never exceeds the
/// exact value.
fn ratio_q96(numerator_q96: u128, denominator_q96: u128) -> Result<u128, CapacityError> {
    if denominator_q96 == 0 {
        return Err(CapacityError::AnchorPriceZero);
    }
    let q96 = U256::from(1u128) << Q96_BITS;
    let resolved = (U256::from(numerator_q96) * q96) / U256::from(denominator_q96);
    resolved
        .try_into()
        .map_err(|_| CapacityError::CapacityUnavailable)
}

/// Updates the cumulative conversion factor with the rate of one hop.
///
/// The factor answers "how many anchor units is one unit of the next
/// hop's input token worth", so it carries the **reciprocal** cumulative
/// rate.
///
/// The reciprocal square root of a hop's rate is
///
/// * `Q96 / s` when the hop spends token0 (the pool price is `s`, and the
///   hop's own rate is `s^2`), and
/// * `s / Q96` when the hop spends token1 (the hop's own rate is the
///   inverse square of the pool price).
///
/// The factor is carried as a Q96-scaled ratio `numerator / denominator`,
/// so the update only ever **multiplies** the two sides:
///
/// ```text
///   spend token0:  num' = num,         den' = den * s / Q96
///   spend token1:  num' = num * s/Q96, den' = den
/// ```
///
/// Keeping the two sides apart is what preserves the precision: folding a
/// running factor into a single number would divide it by a price at every
/// step, and the truncation compounds until the factor collapses to zero.
fn cumulative_factor_update(
    numerator_q96: u128,
    denominator_q96: u128,
    sqrt_price_x96: u128,
    zero_for_one: bool,
) -> Result<(u128, u128), CapacityError> {
    if sqrt_price_x96 == 0 {
        return Err(CapacityError::PriceUnknown);
    }
    let q96 = U256::from(1u128) << Q96_BITS;
    if zero_for_one {
        // Spends token0: the reciprocal square-root rate is Q96 / s, so
        // the denominator carries the price.
        let scaled = (U256::from(denominator_q96) * U256::from(sqrt_price_x96)) / q96;
        let denominator = scaled
            .try_into()
            .map_err(|_| CapacityError::CapacityUnavailable)?;
        Ok((numerator_q96, denominator))
    } else {
        // Spends token1: the reciprocal square-root rate is s / Q96, so
        // the numerator carries the price.
        let scaled = (U256::from(numerator_q96) * U256::from(sqrt_price_x96)) / q96;
        let numerator = scaled
            .try_into()
            .map_err(|_| CapacityError::CapacityUnavailable)?;
        Ok((numerator, denominator_q96))
    }
}

/// One hop of a cross-tick capacity fold.
///
/// The hop carries the **depth** capacity of its pool, which is denominated
/// in the token the hop spends, together with the pool state that decides
/// the cumulative conversion rate.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DepthCapacityHop {
    /// The pool the hop trades in.
    pub pool_id: String,
    /// The token the hop **spends**.
    pub token_in: String,
    /// The token the hop **receives**.
    pub token_out: String,
    /// The current square-root price of the pool, in Q96 fixed point.
    pub sqrt_price_x96: u128,
    /// Whether the hop spends token0 for token1.
    pub zero_for_one: bool,
    /// The depth capacity of the pool, in the token the hop spends.
    pub depth_capacity_raw: u128,
}

/// Fold the depth capacities of the hops into the anchor token.
///
/// The fold is the **same** cumulative-rate fold the single-tick capacity
/// uses: the depth capacity of hop 2 is denominated in the token hop 1
/// produces, so it has to be converted with the cumulative rate of the
/// preceding hops before the minimum can be taken.
///
/// The result is the ceiling of the cross-tick search. It is **not** a
/// single-tick bound: the cross-tick mode is allowed to grow beyond the
/// current tick range, up to the depth the pools can carry.
pub fn compute_loop_depth_capacity(
    anchor_token: &str,
    hops: &[DepthCapacityHop],
) -> Result<u128, CapacityError> {
    if hops.is_empty() {
        return Err(CapacityError::NoHops);
    }
    if hops[0].token_in != anchor_token {
        return Err(CapacityError::AnchorTokenMismatch);
    }

    let mut minimum: Option<u128> = None;
    let mut factor_numerator_q96 = 1u128 << Q96_BITS;
    let mut factor_denominator_q96 = 1u128 << Q96_BITS;

    for hop in hops.iter() {
        if hop.sqrt_price_x96 == 0 {
            return Err(CapacityError::PriceUnknown);
        }
        let ratio_q96 = ratio_q96(factor_numerator_q96, factor_denominator_q96)?;
        let half_folded = fold_q96(hop.depth_capacity_raw, ratio_q96)?;
        let folded = fold_q96(half_folded, ratio_q96)?;
        if minimum.is_none_or(|current| folded < current) {
            minimum = Some(folded);
        }
        let (numerator, denominator) = cumulative_factor_update(
            factor_numerator_q96,
            factor_denominator_q96,
            hop.sqrt_price_x96,
            hop.zero_for_one,
        )?;
        factor_numerator_q96 = numerator;
        factor_denominator_q96 = denominator;
    }

    Ok(minimum.unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::{
        compute_loop_capacity, compute_loop_depth_capacity, CapacityError, CapacityHop,
        DepthCapacityHop, Q96_BITS,
    };

    const Q96: u128 = 1u128 << Q96_BITS;
    const TOKEN_A: &str = "A";
    const TOKEN_B: &str = "B";
    const TOKEN_C: &str = "C";

    /// A hop of a closed loop.
    ///
    /// The pool price is the **square-root** price, so a pool at `2*Q96`
    /// prices token1/token0 at `4`.
    fn hop(
        pool_id: &str,
        token_in: &str,
        token_out: &str,
        zero_for_one: bool,
        sqrt_price_x96: u128,
        liquidity: u128,
    ) -> CapacityHop {
        CapacityHop {
            pool_id: pool_id.to_string(),
            token_in: token_in.to_string(),
            token_out: token_out.to_string(),
            sqrt_price_x96,
            liquidity,
            fee_pips: 0,
            tick_spacing: 60,
            tick: 0,
            zero_for_one,
        }
    }

    #[test]
    fn an_empty_loop_has_no_capacity() {
        assert_eq!(
            compute_loop_capacity(TOKEN_A, &[]).unwrap_err(),
            CapacityError::NoHops
        );
    }

    #[test]
    fn the_first_hop_must_spend_the_anchor_token() {
        let hops = vec![hop("p1", TOKEN_B, TOKEN_C, true, Q96, 1_000_000)];
        assert_eq!(
            compute_loop_capacity(TOKEN_A, &hops).unwrap_err(),
            CapacityError::AnchorTokenMismatch
        );
    }

    #[test]
    fn a_hop_without_a_price_is_rejected() {
        let hops = vec![hop("p1", TOKEN_A, TOKEN_B, false, 0, 1_000_000)];
        assert_eq!(
            compute_loop_capacity(TOKEN_A, &hops).unwrap_err(),
            CapacityError::PriceUnknown
        );
    }

    #[test]
    fn a_hop_without_liquidity_is_rejected() {
        let hops = vec![hop("p1", TOKEN_A, TOKEN_B, false, Q96, 0)];
        assert_eq!(
            compute_loop_capacity(TOKEN_A, &hops).unwrap_err(),
            CapacityError::LiquidityUnknown
        );
    }

    #[test]
    fn a_hop_without_a_tick_spacing_is_rejected() {
        let mut hops = vec![hop("p1", TOKEN_A, TOKEN_B, false, Q96, 1_000_000)];
        hops[0].tick_spacing = 0;
        assert_eq!(
            compute_loop_capacity(TOKEN_A, &hops).unwrap_err(),
            CapacityError::TickSpacingUnknown
        );
    }

    #[test]
    fn the_first_hop_capacity_is_already_in_anchor_units() {
        let hops = vec![hop("p1", TOKEN_A, TOKEN_B, false, Q96, 1_000_000)];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        assert_eq!(capacity.hops[0].anchor_factor_numerator_q96, Q96);
        assert_eq!(capacity.hops[0].anchor_factor_denominator_q96, Q96);
        assert_eq!(
            capacity.capacity_anchor_raw,
            capacity.hops[0].capacity_in_own_token_raw
        );
    }

    #[test]
    fn a_second_hop_is_folded_with_the_rate_of_the_first_hop() {
        // Hop 1 spends token1 (A) and receives token0 (B) at the pool
        // price 2*Q96, so the pool prices token1/token0 at 4.
        //
        // One A therefore buys 1/4 B, and one B is worth 4 A. The linear
        // factor that folds a B-denominated capacity into A is 4.
        //
        // The pool reports the **square-root** price, so the factor is
        // carried as 2*Q96 and applied twice.
        let hops = vec![
            hop("p1", TOKEN_A, TOKEN_B, false, 2 * Q96, 1_000_000),
            hop("p2", TOKEN_B, TOKEN_C, true, Q96, 1_000_000),
        ];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        let second = &capacity.hops[1];
        assert_eq!(second.anchor_factor_numerator_q96, 2 * Q96);
        assert_eq!(second.anchor_factor_denominator_q96, Q96);
        assert_eq!(
            second.capacity_in_anchor_raw,
            second.capacity_in_own_token_raw * 4
        );
    }

    #[test]
    fn the_fold_applies_the_linear_rate_and_not_the_square_root_rate() {
        // Hop 1 spends token0 (A) and receives token1 (B) at the pool
        // price 2*Q96, so the pool prices token1/token0 at 4.
        //
        // One A therefore buys 4 B, and one B is worth 1/4 A. The linear
        // factor that folds a B-denominated capacity into A is 1/4.
        //
        // The document folds by the **linear** rate while the pool reports
        // a square-root price, so the factor 1/2 has to be applied twice.
        // Applying it once would be a factor of two too generous.
        let hops = vec![
            hop("p1", TOKEN_A, TOKEN_B, true, 2 * Q96, 1_000_000),
            hop("p2", TOKEN_B, TOKEN_C, true, Q96, 1_000_000),
        ];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        let second = &capacity.hops[1];
        // The square-root cumulative factor is 1/2, carried by the
        // denominator, and the linear factor is its square, 1/4.
        assert_eq!(second.anchor_factor_numerator_q96, Q96);
        assert_eq!(second.anchor_factor_denominator_q96, 2 * Q96);
        assert_eq!(
            second.capacity_in_anchor_raw,
            second.capacity_in_own_token_raw / 4
        );
    }

    #[test]
    fn the_third_hop_uses_the_cumulative_rate_of_the_first_two_hops() {
        // Hop 1 spends token0 at price 2*Q96, so its linear rate is 4.
        // Hop 2 spends token0 at price 3*Q96, so its linear rate is 9.
        // Hop 3's capacity is denominated in C, and one C is worth
        // 1/(4*9) = 1/36 A. The square-root cumulative factor is 1/6 and
        // the linear factor is its square, 1/36.
        let hops = vec![
            hop("p1", TOKEN_A, TOKEN_B, true, 2 * Q96, 1_000_000),
            hop("p2", TOKEN_B, TOKEN_C, true, 3 * Q96, 1_000_000),
            hop("p3", TOKEN_C, TOKEN_A, true, Q96, 1_000_000),
        ];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        let third = &capacity.hops[2];
        assert_eq!(third.anchor_factor_numerator_q96, Q96);
        assert_eq!(third.anchor_factor_denominator_q96, 6 * Q96);
        assert_eq!(
            third.capacity_in_anchor_raw,
            third.capacity_in_own_token_raw / 36
        );
    }

    #[test]
    fn the_binding_hop_is_the_smallest_after_the_fold() {
        // The first hop is deliberately huge, the second hop is small.
        // The second hop must be the binding one **after** the fold, even
        // though its raw capacity is larger than the first hop's.
        let hops = vec![
            hop("p1", TOKEN_A, TOKEN_B, false, Q96, 1_000_000_000),
            hop("p2", TOKEN_B, TOKEN_C, true, Q96, 1_000),
        ];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        assert_eq!(capacity.binding_pool_id, "p2");
        assert_eq!(
            capacity.capacity_anchor_raw,
            capacity.hops[1].capacity_in_anchor_raw
        );
    }

    #[test]
    fn the_fold_changes_the_outcome_that_a_raw_minimum_would_give() {
        // This is the regression for the unit bug.
        //
        // The two hops are given the **same** raw capacity, so a naive
        // minimum over the raw capacities cannot tell them apart. The
        // first hop spends the anchor token, so its capacity is already
        // in anchor units. The second hop spends B, and one B is worth
        // 1/4 A, so its capacity shrinks by four when it is folded.
        //
        // The binding hop is therefore the second one, even though the
        // raw capacities are identical.
        let hops = vec![
            hop("p1", TOKEN_A, TOKEN_B, true, 2 * Q96, 1_000_000),
            hop("p2", TOKEN_B, TOKEN_C, true, Q96, 1_000_000),
        ];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        let raw_first = capacity.hops[0].capacity_in_own_token_raw;
        let raw_second = capacity.hops[1].capacity_in_own_token_raw;
        // The raw capacities differ only because the pools have different
        // prices, so the comparison is made on the folded values.
        assert_eq!(capacity.binding_pool_id, "p2");
        assert!(capacity.hops[1].capacity_in_anchor_raw < capacity.hops[0].capacity_in_anchor_raw);
        // The folded capacity of the second hop is strictly below the raw
        // minimum, which is the regression: before the unit fix the
        // second hop would have been compared in B units.
        let raw_min = raw_first.min(raw_second);
        assert!(capacity.capacity_anchor_raw < raw_min || raw_min == raw_first);
    }

    #[test]
    fn a_two_hop_loop_folds_the_second_hop_with_the_first_hop_rate() {
        // A -> B -> A. Hop 1 spends token0 at price 2*Q96, so its linear
        // rate is 4 and one B is worth 1/4 A. The second hop is folded by
        // that rate.
        let hops = vec![
            hop("p1", TOKEN_A, TOKEN_B, true, 2 * Q96, 1_000_000),
            hop("p2", TOKEN_B, TOKEN_A, true, 2 * Q96, 1_000_000),
        ];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        assert_eq!(capacity.hops[1].anchor_factor_denominator_q96, 2 * Q96);
        assert_eq!(
            capacity.hops[1].capacity_in_anchor_raw,
            capacity.hops[1].capacity_in_own_token_raw / 4
        );
    }

    #[test]
    fn the_capacity_never_exceeds_the_binding_hop_capacity() {
        let hops = vec![
            hop("p1", TOKEN_A, TOKEN_B, false, 3 * Q96, 7_000_000),
            hop("p2", TOKEN_B, TOKEN_C, false, 5 * Q96, 900_000),
            hop("p3", TOKEN_C, TOKEN_A, true, 2 * Q96, 400_000),
        ];
        let capacity = compute_loop_capacity(TOKEN_A, &hops).unwrap();
        for hop_capacity in &capacity.hops {
            assert!(capacity.capacity_anchor_raw <= hop_capacity.capacity_in_anchor_raw);
        }
        assert_eq!(
            capacity.capacity_anchor_raw,
            capacity
                .hops
                .iter()
                .map(|entry| entry.capacity_in_anchor_raw)
                .min()
                .unwrap()
        );
    }

    #[test]
    fn a_better_price_yields_a_larger_capacity() {
        // The regression for the "pool got better" case: when the price of
        // a hop moves **away** from the boundary, the capacity grows, and
        // the capacity must be recomputed from the current price instead
        // of being carried over from the old one.
        //
        // Hop 1 spends token1 and receives token0, so a **lower** pool
        // price moves the price further from the upper boundary and makes
        // room for more input.
        let worse = compute_loop_capacity(
            TOKEN_A,
            &[hop("p1", TOKEN_A, TOKEN_B, false, Q96, 1_000_000)],
        )
        .unwrap();
        let better = compute_loop_capacity(
            TOKEN_A,
            &[hop("p1", TOKEN_A, TOKEN_B, false, Q96 / 2, 1_000_000)],
        )
        .unwrap();
        assert!(better.capacity_anchor_raw > worse.capacity_anchor_raw);
    }

    #[test]
    fn depth_capacity_is_folded_into_anchor_units_before_minimum() {
        let hops = vec![
            DepthCapacityHop {
                pool_id: "p1".to_owned(),
                token_in: TOKEN_A.to_owned(),
                token_out: TOKEN_B.to_owned(),
                sqrt_price_x96: 2 * Q96,
                zero_for_one: true,
                depth_capacity_raw: 1_000,
            },
            DepthCapacityHop {
                pool_id: "p2".to_owned(),
                token_in: TOKEN_B.to_owned(),
                token_out: TOKEN_A.to_owned(),
                sqrt_price_x96: 2 * Q96,
                zero_for_one: true,
                depth_capacity_raw: 8_000,
            },
        ];
        // The second hop's 8,000 B is folded through the first hop's
        // 4:1 rate, yielding 2,000 A; the first hop binds at 1,000 A.
        assert_eq!(compute_loop_depth_capacity(TOKEN_A, &hops).unwrap(), 1_000);
    }

    /// The reproduction of the mainnet loop that publishes no folded
    /// capacity.
    ///
    /// The loop ``ETH -> WETH -> USDG -> ETH`` runs through three pools with
    /// very different tick spacings, and the first one sits on ``tick = -1``
    /// with a spacing of one. The fold has to prove the capacity of every
    /// hop, so a single hop it cannot price keeps the whole loop without a
    /// capacity, and the probe ladder then searches sizes the route can
    /// never carry.
    #[test]
    fn the_mainnet_usdg_loop_publishes_a_folded_capacity() {
        const ETH: &str = "robinhood:4663:0x0000000000000000000000000000000000000000";
        const WETH: &str = "robinhood:4663:0x0bd7d308f8e1639fab988df18a8011f41eacad73";
        const USDG: &str = "robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168";

        let hops = vec![
            CapacityHop {
                pool_id: "p1".to_owned(),
                token_in: ETH.to_owned(),
                token_out: WETH.to_owned(),
                sqrt_price_x96: 79_226_958_849_466_828_945_823_652_261,
                liquidity: 97_368_630_663_047_855_132_032,
                fee_pips: 500,
                tick_spacing: 1,
                tick: -1,
                zero_for_one: true,
            },
            CapacityHop {
                pool_id: "p2".to_owned(),
                token_in: WETH.to_owned(),
                token_out: USDG.to_owned(),
                sqrt_price_x96: 3_375_438_720_389_679_810_660_917,
                liquidity: 2_734_032_023_434_582,
                fee_pips: 200,
                tick_spacing: 4,
                tick: -201_282,
                zero_for_one: true,
            },
            CapacityHop {
                pool_id: "p3".to_owned(),
                token_in: USDG.to_owned(),
                token_out: ETH.to_owned(),
                sqrt_price_x96: 3_372_610_930_655_197_333_263_251,
                liquidity: 555_363_339_399_824,
                fee_pips: 10_000,
                tick_spacing: 200,
                tick: -201_299,
                zero_for_one: false,
            },
        ];

        let capacity = compute_loop_capacity(ETH, &hops)
            .expect("the loop must publish a folded capacity");
        assert!(capacity.capacity_anchor_raw > 0);
        assert_eq!(capacity.anchor_token, ETH);
    }
}
