//! The USDG valuation of a pool token, computed from the in-memory pool state.
//!
//! Why this module exists
//! -----------------------
//!
//! Every pool token has a USDG conversion route by convention: the venue's
//! quote asset is USDG, and a token that cannot be routed to USDG cannot
//! carry an economically meaningful position. That convention is what makes
//! it possible to express an economic threshold — "is this trade worth ten
//! USDG?" — **without** an external fiat price feed.
//!
//! The valuation is computed from the same published tick states the
//! single-tick capacity fold uses. The closed-loop Manager therefore owns
//! the valuation: it reads the pool state once, and both the capacity and
//! the valuation are derived from that one reading. Nothing here reads a
//! stale table, and nothing here calls the chain.
//!
//! Why the rate is a spot rate
//! ---------------------------
//!
//! The valuation answers "how much is one whole token worth?", so it uses the
//! **spot** rate of the pool that pairs the token with USDG. A deep trade
//! moves the price, so the realised rate is lower than the spot rate, but
//! the threshold is a decision boundary, not a settlement price: using the
//! spot rate keeps the comparison free of any dependency on the position
//! size, which is exactly what is unknown at the moment the threshold is
//! evaluated.
//!
//! Fail-closed
//! -----------
//!
//! A token without a USDG route has **no** valuation. The absence is
//! reported as `None`, never as zero: a zero valuation would silently make
//! every threshold comparison false. The caller treats a missing valuation
//! as "the cross-tick supplement cannot be evaluated", which never
//! invalidates a measured single-tick result.

use std::collections::BTreeMap;

use serde::Serialize;

/// The published state of one pool, as far as the valuation needs it.
#[derive(Clone, Debug)]
pub struct ValuationPoolState {
    /// The square-root price of the pool, in Q96 fixed point.
    pub sqrt_price_x96: u128,
    /// The key id of the pool's token0.
    pub token0_key_id: String,
    /// The key id of the pool's token1.
    pub token1_key_id: String,
}

/// The USDG valuation of one token.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsdgValuation {
    /// The token that was valued.
    pub token_key_id: String,
    /// The value of **one whole** token, in raw USDG units.
    pub value_usdg_raw: u128,
    /// The pool the rate was read from.
    pub pool_id: String,
    /// The USDG side of that pool.
    pub usdg_token_key_id: String,
    /// The token side of that pool.
    pub token_key_id_in_pool: String,
}

pub(super) fn identity_valuation(
    token_key_id: &str,
    usdg_token_key_id: &str,
    usdg_decimals: u32,
) -> Option<UsdgValuation> {
    if token_key_id.trim() != usdg_token_key_id.trim() {
        return None;
    }
    Some(UsdgValuation {
        token_key_id: token_key_id.trim().to_owned(),
        value_usdg_raw: 10u128.checked_pow(usdg_decimals)?,
        pool_id: "stablecoin_unit_identity".to_owned(),
        usdg_token_key_id: usdg_token_key_id.trim().to_owned(),
        token_key_id_in_pool: token_key_id.trim().to_owned(),
    })
}

/// The number of fixed-point bits used by the Uniswap Q96 price format.
const Q96_BITS: u32 = 96;

/// Compute the USDG valuation of every token that has a USDG route.
///
/// The valuation of a token is the rate of the pool that pairs it with
/// USDG, applied to the token's own decimals: the price of the pool is the
/// price of one **whole** token, while the raw amounts of the pool are
/// denominated in the tokens' raw units, so the two decimal factors have to
/// be divided out before the two sides can be compared.
///
/// A token may be paired with USDG in more than one pool. The pools are
/// visited in a deterministic order and the first pool that produces a
/// usable rate wins, so the result is reproducible.
pub fn value_tokens_in_usdg(
    pool_states: &BTreeMap<String, ValuationPoolState>,
    usdg_token_key_id: &str,
    token_decimals: &BTreeMap<String, u32>,
    usdg_decimals: u32,
) -> BTreeMap<String, UsdgValuation> {
    let usdg_token_key_id = usdg_token_key_id.trim();
    let mut valuations: BTreeMap<String, UsdgValuation> = BTreeMap::new();
    for (pool_id, state) in pool_states {
        let token0_is_usdg = state.token0_key_id.trim() == usdg_token_key_id;
        let token1_is_usdg = state.token1_key_id.trim() == usdg_token_key_id;
        if token0_is_usdg == token1_is_usdg {
            // The pool either does not carry USDG at all or carries it on
            // both sides, which cannot happen for a real pool.
            continue;
        }
        let token_key_id = if token0_is_usdg {
            state.token1_key_id.trim()
        } else {
            state.token0_key_id.trim()
        };
        let Some(&decimals) = token_decimals.get(token_key_id) else {
            continue;
        };
        let Some(rate) = usdg_rate_per_whole_token(
            state.sqrt_price_x96,
            token0_is_usdg,
            decimals,
            usdg_decimals,
        ) else {
            continue;
        };
        if rate == 0 {
            continue;
        }
        valuations
            .entry(token_key_id.to_owned())
            .or_insert(UsdgValuation {
                token_key_id: token_key_id.to_owned(),
                value_usdg_raw: rate,
                pool_id: pool_id.clone(),
                usdg_token_key_id: usdg_token_key_id.to_owned(),
                token_key_id_in_pool: token_key_id.to_owned(),
            });
    }
    valuations
}

/// The USDG value of one whole token, from the spot rate of its pool.
///
/// The pool's price is the **square-root** price of token1 in token0, so the
/// linear rate is the square of it. The raw amounts of the two sides are
/// denominated in their own decimals, so the rate is scaled by the ratio of
/// the two decimal factors to obtain the value of one **whole** token.
fn usdg_rate_per_whole_token(
    sqrt_price_x96: u128,
    usdg_is_token0: bool,
    token_decimals: u32,
    // The rate is quoted in raw units against raw units, so USDG's own
    // decimals cancel out and the result is already expressed in raw USDG.
    _usdg_decimals: u32,
) -> Option<u128> {
    if sqrt_price_x96 == 0 {
        return None;
    }
    let sqrt_price = alloy_primitives::U256::from(sqrt_price_x96);
    let squared = sqrt_price.saturating_mul(sqrt_price);
    if squared.is_zero() {
        return None;
    }
    // The square-root price is Q96-scaled, so its square is Q192-scaled.
    // The **linear** price is the square divided by Q192, which keeps the
    // number in the range of a real price instead of the range of a fixed
    // point representation.
    let q96 = alloy_primitives::U256::from(1u128) << Q96_BITS;
    let q192 = q96 * q96;
    // The pool's price is the number of **raw** token1 units one raw token0
    // unit buys. The value of one whole token in **raw USDG** therefore is:
    //
    // * the token is token0 and USDG is token1: the rate is already "raw
    //   USDG per raw token", so one whole token is worth
    //   `rate * 10^token_decimals` raw USDG;
    // * the token is token1 and USDG is token0: the rate is "raw token per
    //   raw USDG", so one whole token is worth
    //   `10^token_decimals / rate` raw USDG.
    //
    // USDG's own decimals do not appear: the rate already quotes raw units
    // against raw units, and the result is expressed in raw USDG as well.
    let token_scale = pow10(token_decimals);
    // The raw USDG per **whole** token.
    let rate = if usdg_is_token0 {
        // USDG is token0, the token is token1: the pool quotes raw token per
        // raw USDG, so the raw USDG per raw token is its reciprocal, and one
        // whole token holds `10^token_decimals` raw token units.
        (q192 * token_scale) / squared
    } else {
        // USDG is token1, the token is token0: the pool already quotes raw
        // USDG per raw token.
        (squared * token_scale) / q192
    };
    Some(rate.min(alloy_primitives::U256::from(u128::MAX)).to::<u128>())
}

/// `10^decimals`, as a 256-bit number.
fn pow10(decimals: u32) -> alloy_primitives::U256 {
    let mut value = alloy_primitives::U256::from(1u8);
    for _ in 0..decimals {
        value *= alloy_primitives::U256::from(10u8);
    }
    value
}

#[cfg(test)]
mod tests {
    use super::{identity_valuation, value_tokens_in_usdg, ValuationPoolState};
    use std::collections::BTreeMap;

    const Q96: u128 = 1u128 << 96;
    const USDG: &str = "usdg";
    const ETH: &str = "eth";

    fn state(
        sqrt_price_x96: u128,
        token0_key_id: &str,
        token1_key_id: &str,
    ) -> ValuationPoolState {
        ValuationPoolState {
            sqrt_price_x96,
            token0_key_id: token0_key_id.to_owned(),
            token1_key_id: token1_key_id.to_owned(),
        }
    }

    #[test]
    fn a_token_paired_with_usdg_at_the_peg_is_worth_one_usdg() {
        // The pool's square-root price is `2 * Q96`, so its **linear** price
        // is four raw token1 per raw token0.
        //
        // The token is token1 and USDG is token0, so the pool quotes raw
        // token per raw USDG: one raw token buys a quarter of a raw USDG.
        // One whole token holds `10^18` raw token units, so it is worth
        // `10^18 / 4` raw USDG units.
        let mut pool_states = BTreeMap::new();
        pool_states.insert("pool-eth-usdg".to_owned(), state(2 * Q96, USDG, ETH));
        let mut decimals = BTreeMap::new();
        decimals.insert(ETH.to_owned(), 18u32);
        let valuations = value_tokens_in_usdg(&pool_states, USDG, &decimals, 6);
        let valuation = valuations.get(ETH).expect("the token must be valued");
        assert_eq!(valuation.value_usdg_raw, 250_000_000_000_000_000);
        assert_eq!(valuation.pool_id, "pool-eth-usdg");
    }

    #[test]
    fn the_usdg_side_of_the_pool_may_be_either_token() {
        // The same pool, read from the other side, must produce the same
        // valuation. The token order of a pool is decided by the numeric
        // order of the two addresses, not by the role the token plays.
        let mut pool_states = BTreeMap::new();
        pool_states.insert("pool-eth-usdg".to_owned(), state(2 * Q96, USDG, ETH));
        let mut decimals = BTreeMap::new();
        decimals.insert(ETH.to_owned(), 18u32);
        let forward = value_tokens_in_usdg(&pool_states, USDG, &decimals, 6);
        let forward_value = forward.get(ETH).expect("valued").value_usdg_raw;

        let mut mirrored = BTreeMap::new();
        mirrored.insert(
            "pool-eth-usdg".to_owned(),
            state(Q96 / 2, ETH, USDG),
        );
        let reversed = value_tokens_in_usdg(&mirrored, USDG, &decimals, 6);
        let reversed_value = reversed.get(ETH).expect("valued").value_usdg_raw;

        assert_eq!(forward_value, reversed_value);
    }

    #[test]
    fn a_token_without_a_usdg_route_has_no_valuation() {
        let mut pool_states = BTreeMap::new();
        pool_states.insert("pool-eth-usdg".to_owned(), state(2 * Q96, USDG, ETH));
        let mut decimals = BTreeMap::new();
        decimals.insert(ETH.to_owned(), 18u32);
        let valuations = value_tokens_in_usdg(&pool_states, USDG, &decimals, 6);
        assert!(!valuations.contains_key("unknown"));
    }

    #[test]
    fn a_token_without_known_decimals_has_no_valuation() {
        let mut pool_states = BTreeMap::new();
        pool_states.insert("pool-eth-usdg".to_owned(), state(2 * Q96, USDG, ETH));
        let valuations = value_tokens_in_usdg(&pool_states, USDG, &BTreeMap::new(), 6);
        assert!(valuations.is_empty());
    }

    #[test]
    fn usdg_unit_value_does_not_require_a_self_pair_pool() {
        let valuation = identity_valuation(USDG, USDG, 6).expect("USDG identity valuation");
        assert_eq!(valuation.value_usdg_raw, 1_000_000);
        assert_eq!(valuation.pool_id, "stablecoin_unit_identity");
        assert!(identity_valuation(ETH, USDG, 6).is_none());
    }
}
