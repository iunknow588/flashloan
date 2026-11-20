//! Deterministic initialization of the cross-tick review.
//!
//! The single-tick mode is always the first, physically safe mode. Its
//! economic optimum is the **anchor** of the cross-tick mode: the cross-tick
//! search starts at exactly that amount and only explores the larger domain
//! beyond it.
//!
//! Two rules make this deterministic and safe:
//!
//! * the cross-tick seed is the single-tick best amount, never a magic
//!   constant such as `1` and never a random value;
//! * a single-tick best amount of zero **never** starts a cross-tick search.
//!   A zero seed means "nothing works inside the current tick range", so a
//!   cross-tick search started from it would degenerate.
//!
//! The search itself is **not** a new algorithm. The cross-tick mode reuses
//! the existing ladder: the seed is doubled upwards until the size fails,
//! and halved downwards towards the seed until the size succeeds. Only the
//! starting point changes; the stepping rule stays the one the single-tick
//! ladder already uses.

use num_bigint::BigUint;
use num_traits::Zero;
use serde_json::{json, Value};

/// The decision about the cross-tick supplement.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CrossTickDecision {
    /// The cross-tick mode must not be started.
    NotStarted {
        /// The machine-readable reason.
        reason: &'static str,
    },
    /// The cross-tick mode may start at the published seed.
    Start {
        /// The deterministic initial amount, in borrow-token raw units.
        seed_raw: BigUint,
        /// The upper bound of the cross-tick search, in borrow-token raw units.
        capacity_raw: BigUint,
    },
}

/// Decide whether the cross-tick supplement starts, and with which seed.
///
/// The inputs are all optional on purpose: the cross-tick mode is a
/// supplement, and a route that does not carry every input must keep its
/// measured single-tick result instead of becoming unmeasured.
pub fn decide(
    single_tick_best_amount_raw: &BigUint,
    single_tick_best_profit_raw: &BigUint,
    threshold_usdg_raw: Option<&BigUint>,
    borrow_token_usdg_value_raw: Option<&BigUint>,
    borrow_token_decimals: Option<u32>,
    cross_tick_capacity_raw: Option<&BigUint>,
) -> CrossTickDecision {
    if single_tick_best_amount_raw.is_zero() {
        return CrossTickDecision::NotStarted {
            reason: "single_tick_best_amount_zero",
        };
    }
    let (Some(threshold), Some(value), Some(decimals), Some(capacity)) = (
        threshold_usdg_raw,
        borrow_token_usdg_value_raw,
        borrow_token_decimals,
        cross_tick_capacity_raw,
    ) else {
        return CrossTickDecision::NotStarted {
            reason: "cross_tick_inputs_unavailable",
        };
    };
    if threshold.is_zero() || value.is_zero() || capacity.is_zero() {
        return CrossTickDecision::NotStarted {
            reason: "cross_tick_inputs_unavailable",
        };
    }
    let scale = BigUint::from(10u8).pow(decimals);
    let profit_usdg_raw = (single_tick_best_profit_raw * value) / &scale;
    if profit_usdg_raw >= *threshold {
        return CrossTickDecision::NotStarted {
            reason: "single_tick_profit_above_threshold",
        };
    }
    if single_tick_best_amount_raw > capacity {
        return CrossTickDecision::NotStarted {
            reason: "single_tick_best_amount_exceeds_cross_tick_capacity",
        };
    }
    CrossTickDecision::Start {
        seed_raw: single_tick_best_amount_raw.clone(),
        capacity_raw: capacity.clone(),
    }
}

/// Render a decision into the JSON contract published by the kernel.
pub fn to_json(decision: &CrossTickDecision) -> Value {
    match decision {
        CrossTickDecision::NotStarted { reason } => json!({
            "started": false,
            "reason": reason,
        }),
        CrossTickDecision::Start {
            seed_raw,
            capacity_raw,
        } => json!({
            "started": true,
            "reason": "single_tick_profit_below_threshold",
            "seedAmountRaw": format!("0x{:x}", seed_raw),
            "capacityRaw": format!("0x{:x}", capacity_raw),
            "basis": "single_tick_best_amount",
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::{decide, CrossTickDecision};
    use num_bigint::BigUint;
    use num_traits::Zero;

    fn config() -> (BigUint, BigUint, BigUint, u32, BigUint) {
        (
            BigUint::from(10u32).pow(18),
            BigUint::from(1_000_000u64),
            BigUint::from(9_896_800u64),
            18,
            BigUint::from(1_000u32) * BigUint::from(10u32).pow(18),
        )
    }

    #[test]
    fn a_zero_single_tick_best_amount_never_starts_the_cross_tick_search() {
        let (profit, value, threshold, decimals, capacity) = config();
        let decision = decide(
            &BigUint::zero(),
            &profit,
            Some(&threshold),
            Some(&value),
            Some(decimals),
            Some(&capacity),
        );
        assert_eq!(
            decision,
            CrossTickDecision::NotStarted {
                reason: "single_tick_best_amount_zero"
            }
        );
    }

    #[test]
    fn the_cross_tick_seed_is_exactly_the_single_tick_best_amount() {
        let (profit, value, threshold, decimals, capacity) = config();
        let seed = BigUint::from(7u32) * BigUint::from(10u32).pow(18);
        let decision = decide(
            &seed,
            &profit,
            Some(&threshold),
            Some(&value),
            Some(decimals),
            Some(&capacity),
        );
        assert_eq!(
            decision,
            CrossTickDecision::Start {
                seed_raw: seed,
                capacity_raw: capacity,
            }
        );
    }

    #[test]
    fn the_cross_tick_search_does_not_change_a_seed_above_its_capacity() {
        let (profit, value, threshold, decimals, capacity) = config();
        let seed = capacity.clone() * BigUint::from(2u32);
        let decision = decide(
            &seed,
            &profit,
            Some(&threshold),
            Some(&value),
            Some(decimals),
            Some(&capacity),
        );
        assert_eq!(
            decision,
            CrossTickDecision::NotStarted {
                reason: "single_tick_best_amount_exceeds_cross_tick_capacity"
            }
        );
    }

    #[test]
    fn a_profit_above_the_threshold_does_not_start_the_cross_tick_search() {
        let (_, value, threshold, decimals, capacity) = config();
        let decision = decide(
            &BigUint::from(10u32).pow(18),
            &(BigUint::from(20u32) * BigUint::from(10u32).pow(18)),
            Some(&threshold),
            Some(&value),
            Some(decimals),
            Some(&capacity),
        );
        assert_eq!(
            decision,
            CrossTickDecision::NotStarted {
                reason: "single_tick_profit_above_threshold"
            }
        );
    }

    #[test]
    fn missing_cross_tick_inputs_never_invalidate_the_single_tick_result() {
        let (profit, _, _, _, capacity) = config();
        let decision = decide(
            &BigUint::from(10u32).pow(18),
            &profit,
            None,
            None,
            None,
            Some(&capacity),
        );
        assert_eq!(
            decision,
            CrossTickDecision::NotStarted {
                reason: "cross_tick_inputs_unavailable"
            }
        );
    }
}
