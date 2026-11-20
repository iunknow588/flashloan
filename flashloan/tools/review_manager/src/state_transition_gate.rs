//! The three-state execution gate of version 1.
//!
//! Why this module exists
//! ---------------------
//!
//! The candidate ranking prices a hop from an **expected** pool state, and
//! the transaction executes against a **current** pool state. Between the
//! two readings the pool may have moved, and the move has to be classified
//! before the route may be broadcast.
//!
//! Pool Manager owns the arithmetic of that classification
//! (`pool_manager::state_transition`). This module owns the *policy*:
//!
//! * **fatal** — the current price left the expected tick range, or the
//!   active liquidity cannot be proven. The route may not execute at all.
//! * **adverse** — the same input now produces a smaller output. The route
//!   may not execute, and the reason is stable: `adverse_price_move`.
//! * **unchanged** — the same input produces the same output. The route
//!   keeps its proven capacity.
//! * **favorable** — the same input produces a larger output and the price
//!   stayed inside the tick range. The route may execute, but the enlarged
//!   capacity is only a **candidate**: the review still has to re-price
//!   every hop with the final input amount.
//!
//! Fail-closed
//! ----------
//!
//! A hop whose state cannot be read, or whose expected state is missing, is
//! **fatal**. "Cannot prove" is not "is false": an unmeasured hop may not
//! become a broadcast request.

use serde::Serialize;

use pool_manager::state_transition::{
    evaluate_state_transition, SingleTickExecutionState, StateTransitionKind,
    StateTransitionRequest, StateTransitionVerdict,
};

/// The three-state classification of one hop.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TransitionOutcome {
    /// The route may not execute.
    Fatal,
    /// The route may not execute: the market moved against it.
    Adverse,
    /// The market did not move in a way that changes the output.
    Unchanged,
    /// The market moved in favour of the route.
    Favorable,
}

impl TransitionOutcome {
    /// The stable wire name of the outcome.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Fatal => "fatal",
            Self::Adverse => "adverse",
            Self::Unchanged => "unchanged",
            Self::Favorable => "favorable",
        }
    }

    /// Whether the route may execute with this hop.
    pub fn allows_broadcast(self) -> bool {
        matches!(self, Self::Unchanged | Self::Favorable)
    }
}

/// The state one hop contributes to the gate.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HopExecutionState {
    /// The pool this hop trades in.
    pub pool_id: String,
    /// The state the candidate ranking priced.
    pub expected: SingleTickExecutionState,
    /// The state the route would execute against.
    pub current: SingleTickExecutionState,
    /// The pool's fixed fee, in hundredths of a bip.
    pub fee_pips: u32,
    /// Whether the hop spends token0.
    pub zero_for_one: bool,
    /// The input amount this hop would carry, in raw units.
    pub amount_in_raw: u128,
    /// The capacity of the hop in the expected state, in raw units.
    pub expected_capacity_raw: u128,
}

/// The verdict for one hop.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HopTransitionVerdict {
    /// The pool this hop trades in.
    pub pool_id: String,
    /// The three-state classification of the move.
    pub outcome: TransitionOutcome,
    /// The stable reason code of the verdict.
    pub reason: String,
    /// Whether this hop alone permits the broadcast.
    pub broadcast_allowed: bool,
    /// The price limit of the hop, in Q96 fixed point.
    pub sqrt_price_limit_x96: Option<String>,
    pub boundary_sqrt_price_x96: Option<String>,
    pub expected_amount_out_raw: Option<String>,
    pub current_amount_out_raw: Option<String>,
    pub expected_capacity_raw: String,
    /// The capacity of the hop in the current state.
    pub current_capacity_raw: Option<String>,
    /// The candidate capacity the hop may use.
    pub candidate_capacity_raw: Option<String>,
    /// How the candidate capacity was derived.
    pub capacity_basis: String,
    pub capacity_ratio_numerator: Option<String>,
    pub capacity_ratio_denominator: Option<String>,
    pub capacity_multiplier_bps: Option<u32>,
    /// The old price.
    pub expected_sqrt_price_x96: String,
    /// The new price.
    pub current_sqrt_price_x96: String,
}

impl HopTransitionVerdict {
    fn from_verdict(pool_id: String, verdict: &StateTransitionVerdict) -> Self {
        let outcome = match verdict.kind {
            StateTransitionKind::Fatal => TransitionOutcome::Fatal,
            StateTransitionKind::Adverse => TransitionOutcome::Adverse,
            StateTransitionKind::Unchanged => TransitionOutcome::Unchanged,
            StateTransitionKind::Favorable => TransitionOutcome::Favorable,
        };
        Self {
            pool_id,
            outcome,
            reason: verdict.reason.clone(),
            broadcast_allowed: verdict.broadcast_allowed,
            sqrt_price_limit_x96: verdict.sqrt_price_limit_x96.clone(),
            boundary_sqrt_price_x96: verdict.boundary_sqrt_price_x96.clone(),
            expected_amount_out_raw: verdict.expected_amount_out_raw.clone(),
            current_amount_out_raw: verdict.current_amount_out_raw.clone(),
            expected_capacity_raw: verdict.expected_capacity_raw.clone(),
            current_capacity_raw: verdict.exact_current_capacity_raw.clone(),
            candidate_capacity_raw: verdict.candidate_capacity_raw.clone(),
            capacity_basis: format!("{:?}", verdict.capacity_basis),
            capacity_ratio_numerator: verdict.capacity_ratio_numerator.clone(),
            capacity_ratio_denominator: verdict.capacity_ratio_denominator.clone(),
            capacity_multiplier_bps: verdict.capacity_multiplier_bps,
            expected_sqrt_price_x96: verdict.expected_sqrt_price_x96.clone(),
            current_sqrt_price_x96: verdict.current_sqrt_price_x96.clone(),
        }
    }
}

/// The verdict for a whole route.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RouteTransitionVerdict {
    /// Whether every hop permits the broadcast.
    pub broadcast_allowed: bool,
    /// The stable reason code of the route verdict.
    pub reason: Option<String>,
    /// The per-hop verdicts, in hop order.
    pub hops: Vec<HopTransitionVerdict>,
}

/// Runs the three-state gate over a closed loop.
///
/// Every hop is judged, and a hop that fails does not stop the walk: the
/// remaining hops are still judged, so the evidence names the middle hop as
/// well. The route itself is fail-closed, and the **first** failing hop is
/// the one that is reported.
pub fn evaluate_route_transition(
    hops: &[HopExecutionState],
    max_capacity_multiplier_bps: u32,
) -> RouteTransitionVerdict {
    if hops.is_empty() {
        return RouteTransitionVerdict {
            broadcast_allowed: false,
            reason: Some("no_hops".to_string()),
            hops: Vec::new(),
        };
    }

    let mut verdicts = Vec::with_capacity(hops.len());
    let mut first_failure: Option<(usize, String)> = None;
    for hop in hops {
        let verdict = evaluate_state_transition(&StateTransitionRequest {
            expected: hop.expected,
            current: hop.current,
            fee_pips: hop.fee_pips,
            zero_for_one: hop.zero_for_one,
            amount_in_raw: hop.amount_in_raw,
            expected_capacity_raw: hop.expected_capacity_raw,
            max_capacity_multiplier_bps,
        });
        let allowed = verdict.broadcast_allowed;
        let reason = verdict.reason.clone();
        verdicts.push(HopTransitionVerdict::from_verdict(
            hop.pool_id.clone(),
            &verdict,
        ));
        if !allowed && first_failure.is_none() {
            first_failure = Some((
                verdicts.len() - 1,
                format!("hop_{}_failed:{reason}", verdicts.len()),
            ));
        }
    }

    if let Some((_, reason)) = first_failure {
        return RouteTransitionVerdict {
            broadcast_allowed: false,
            reason: Some(reason),
            hops: verdicts,
        };
    }
    RouteTransitionVerdict {
        broadcast_allowed: true,
        reason: None,
        hops: verdicts,
    }
}
/// The price limits of a reviewed route, in route order.
///
/// The list is empty unless every hop proved a usable limit. A partial
/// list is not safe to encode: a missing hop would silently receive the
/// global V4 price bound, which is exactly what version 1 forbids.
pub fn route_transition_price_limits(verdict: &RouteTransitionVerdict) -> Vec<String> {
    if verdict.hops.len() != 3 {
        return Vec::new();
    }
    let mut limits = Vec::with_capacity(3);
    for hop in &verdict.hops {
        let Some(limit) = hop.sqrt_price_limit_x96.as_ref() else {
            return Vec::new();
        };
        if limit.trim().is_empty() {
            return Vec::new();
        }
        limits.push(limit.trim().to_owned());
    }
    limits
}

#[cfg(test)]
mod tests {
    use super::*;
    use pool_manager::state_transition::DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS;

    const Q96: u128 = 79_228_162_514_264_337_593_543_950_336;
    const LIQUIDITY: u128 = 10_000_000_000_000_000_000;

    fn hop(zero_for_one: bool) -> HopExecutionState {
        let expected = SingleTickExecutionState {
            sqrt_price_x96: Q96 + 150_000_000_000_000_000_000_000_000,
            liquidity: LIQUIDITY,
            tick: 1,
            tick_spacing: 60,
        };
        let quote = pool_manager::depth::single_tick_swap_amount_out(
            expected.sqrt_price_x96,
            expected.liquidity,
            3_000,
            expected.tick_spacing,
            expected.tick,
            1_000_000_000_000,
            zero_for_one,
        )
        .unwrap();
        HopExecutionState {
            pool_id: "pool-a".to_owned(),
            expected,
            current: expected,
            fee_pips: 3_000,
            zero_for_one,
            amount_in_raw: 1_000_000_000_000,
            expected_capacity_raw: quote.max_amount_in_raw,
        }
    }

    /// The market did not move: the route keeps its proven capacity.
    #[test]
    fn an_unchanged_market_keeps_the_route_executable() {
        let verdict = evaluate_route_transition(&[hop(true)], DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS);
        assert!(verdict.broadcast_allowed);
        assert_eq!(verdict.hops[0].outcome, TransitionOutcome::Unchanged);
        assert!(verdict.hops[0].broadcast_allowed);
        assert!(verdict.hops[0].sqrt_price_limit_x96.is_some());
        assert!(verdict.hops[0].boundary_sqrt_price_x96.is_some());
        assert!(verdict.hops[0].expected_amount_out_raw.is_some());
        assert_eq!(
            verdict.hops[0].expected_amount_out_raw,
            verdict.hops[0].current_amount_out_raw
        );
        assert!(verdict.hops[0].capacity_ratio_numerator.is_some());
        assert!(verdict.hops[0].capacity_ratio_denominator.is_some());
        assert_eq!(verdict.hops[0].capacity_multiplier_bps, Some(10_000));
    }

    /// The market moved against the route: the route may not execute and the
    /// reason is the stable `adverse_price_move`.
    #[test]
    fn an_adverse_market_blocks_the_route_with_a_stable_reason() {
        let mut state = hop(true);
        state.current.sqrt_price_x96 -= 50_000_000_000_000_000_000_000_000;
        let verdict = evaluate_route_transition(&[state], DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS);
        assert!(!verdict.broadcast_allowed);
        assert_eq!(verdict.hops[0].outcome, TransitionOutcome::Adverse);
        let expected_out = verdict.hops[0]
            .expected_amount_out_raw
            .as_deref()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let current_out = verdict.hops[0]
            .current_amount_out_raw
            .as_deref()
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert!(current_out < expected_out);
        assert_eq!(
            verdict.reason.as_deref(),
            Some("hop_1_failed:adverse_price_move")
        );
    }

    /// The price left the expected tick range: the route is fatal.
    #[test]
    fn a_cross_tick_market_is_fatal() {
        let mut state = hop(true);
        let bounds = pool_manager::depth::tick_range_bounds(
            state.expected.tick,
            state.expected.tick_spacing,
            state.expected.sqrt_price_x96,
        )
        .unwrap();
        state.current.sqrt_price_x96 = bounds.lower_sqrt_price_x96;
        state.current.tick = bounds.lower_tick;
        let verdict = evaluate_route_transition(&[state], DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS);
        assert!(!verdict.broadcast_allowed);
        assert_eq!(verdict.hops[0].outcome, TransitionOutcome::Fatal);
    }

    /// A route without hops cannot be proven executable.
    #[test]
    fn a_route_without_hops_is_blocked() {
        let verdict = evaluate_route_transition(&[], DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS);
        assert!(!verdict.broadcast_allowed);
        assert_eq!(verdict.reason.as_deref(), Some("no_hops"));
    }

    /// Every hop is judged, so the evidence names the middle hop as well.
    #[test]
    fn every_hop_is_judged() {
        let first = hop(true);
        let mut second = hop(false);
        second.pool_id = "pool-b".to_owned();
        let mut third = hop(true);
        third.pool_id = "pool-c".to_owned();
        let verdict =
            evaluate_route_transition(&[first, second, third], DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS);
        assert_eq!(verdict.hops.len(), 3);
        assert_eq!(verdict.hops[1].pool_id, "pool-b");
    }
}
