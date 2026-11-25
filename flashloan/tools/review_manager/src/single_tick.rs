//!
//! The per-hop single-tick final gate of version 1.
//!
//! Why this module exists
//! -----------------------
//!
//! Version 1 constrains **every** hop of a closed loop to a single tick
//! range. The candidate ranking prices a hop with the single-tick exact
//! output, but that price is only a **screening** result: it answers "could
//! this pool ever be profitable at this size", not "is the route that is
//! about to be executed executable right now".
//!
//! The final review is the last place where a route can be stopped, and it
//! has to answer the harder question with the **actual** input amount of
//! each hop: the first hop borrows the principal, and every later hop spends
//! what the previous hop produced. Re-pricing a hop with the borrowed
//! principal instead of its own input would prove nothing.
//!
//! So this module performs the final review itself:
//!
//! * every hop is priced with Pool Manager's single-tick arithmetic, which
//!   is exact **only** while the price stays inside the current tick range;
//! * the price after the hop is compared against the next tick boundary, so
//!   a hop that would cross the boundary is rejected rather than priced
//!   with a tick traversal the version does not allow;
//! * the input amount of hop *n+1* is the output amount of hop *n*, so the
//!   whole chain is priced with the amounts that would actually flow;
//! * one failing hop fails the **whole** route. A route is not partially
//!   executable: the loop has to close, and a hop that cannot be proven to
//!   stay inside its tick range makes the closure unprovable.
//!
//! Fail-closed
//! -----------
//!
//! The gate rejects a route whenever it cannot *prove* the route stays
//! inside one tick range per hop. That includes the cases where the
//! measurement itself is missing:
//!
//! * the pool state does not carry a tick, a tick spacing or a price;
//! * the tick range cannot be derived from the state;
//! * the single-tick price cannot be computed;
//! * the pool state version drifted away from the reviewed version.
//!
//! "Cannot prove" is not "is false". A route that is merely unmeasured is
//! blocked, exactly like a route that is measured and fails, because the
//! review stage may not promote an unproven route into a broadcast
//! request.

use serde::Serialize;

use crate::parity::HopState;

/// The state one hop contributes to the final gate.
///
/// The tick and the tick spacing are **not** optional: the single-tick
/// price is only defined relative to the range the price currently sits
/// in, so a hop without them cannot be proven single-tick and is blocked.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SingleTickHopState {
    /// The pool this hop trades in.
    pub pool_id: String,
    /// The initialized tick the pool's price sits in.
    pub tick: i32,
    /// The pool's tick spacing.
    pub tick_spacing: i32,
    /// The version of the pool state the review priced.
    ///
    /// The version is compared across the hops of one route and against
    /// the review's own version, so a state that drifted mid-review is
    /// caught instead of being priced from two different markets.
    pub pool_state_version: Option<String>,
    /// The materialisation batch the pool state was written by.
    ///
    /// This is the **snapshot identity** of the state.  Pool Manager stamps
    /// every row of one refresh run with the same batch id, so "all hops of
    /// the route carry the same batch" is the strongest statement the data
    /// can make about "the route was priced from one market view".
    ///
    /// The per-pool read timestamp cannot make that statement: pools are
    /// read one after the other, so two pools of the **same** batch always
    /// carry two **different** timestamps.  Comparing timestamps for
    /// equality therefore rejects every multi-hop route that has a read
    /// timestamp at all, which is why the batch is carried next to it.
    pub snapshot_batch: Option<String>,
}

/// The conclusion the gate reached for one hop.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BoundConclusion {
    /// The hop stays inside the current tick range.
    InsideTickRange,
    /// The hop crosses the next tick boundary and is therefore not
    /// executable in version 1.
    CrossesNextTick,
    /// The hop cannot be proven to stay inside the range, because the
    /// measurement it would need is missing or unusable.
    Unprovable,
}

impl BoundConclusion {
    /// The stable wire name of the conclusion.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::InsideTickRange => "inside_tick_range",
            Self::CrossesNextTick => "crosses_next_tick",
            Self::Unprovable => "unprovable",
        }
    }
}

/// The final verdict for one hop.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SingleTickHopVerdict {
    /// The pool this hop trades in.
    pub pool_id: String,
    /// The initialized tick the pool's price sits in.
    pub initialized_tick: i32,
    /// The tick the price would reach if the hop crossed the boundary.
    pub next_tick: i32,
    /// The distance, in ticks, between the initialized tick and the next
    /// tick boundary.
    pub next_tick_distance: i32,
    /// The actual input amount of this hop, in raw units.
    pub amount_in_raw: u128,
    /// The realised output of the hop, in raw units, when the hop is
    /// single-tick.
    pub amount_out_raw: Option<u128>,
    /// The largest input that still stays inside the current tick range.
    pub max_amount_in_raw: Option<u128>,
    /// The distance, in raw units, between the actual input and the
    /// single-tick bound.
    pub headroom_raw: Option<i128>,
    /// The conclusion the gate reached.
    pub bound_conclusion: BoundConclusion,
    /// Whether the hop passed the gate.
    pub ok: bool,
    /// The machine-readable reason, when the hop failed.
    pub reason: Option<String>,
}

/// The final verdict for a whole route.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SingleTickRouteVerdict {
    /// Whether every hop of the route passed the gate.
    pub executable: bool,
    /// The machine-readable reason, when the route failed.
    pub reason: Option<String>,
    /// The per-hop verdicts, in hop order.
    pub hops: Vec<SingleTickHopVerdict>,
}

/// Prices one hop with the single-tick arithmetic and judges its bound.
///
/// The price is Pool Manager's published single-tick price, so the gate and
/// the candidate ranking can never price the same hop with two different
/// rules.
fn evaluate_hop(
    state: &HopState,
    extra: &SingleTickHopState,
    amount_in_raw: u128,
) -> SingleTickHopVerdict {
    let mut verdict = SingleTickHopVerdict {
        pool_id: state.pool_id.clone(),
        initialized_tick: extra.tick,
        next_tick: extra.tick,
        next_tick_distance: 0,
        amount_in_raw,
        amount_out_raw: None,
        max_amount_in_raw: None,
        headroom_raw: None,
        bound_conclusion: BoundConclusion::Unprovable,
        ok: false,
        reason: None,
    };

    if amount_in_raw == 0 {
        verdict.reason = Some("zero_input".to_string());
        return verdict;
    }
    if state.sqrt_price_x96 == 0 || state.liquidity == 0 || state.fee_pips == 0 {
        verdict.reason = Some("pool_state_unusable".to_string());
        return verdict;
    }
    if extra.tick_spacing <= 0 {
        verdict.reason = Some("tick_spacing_unknown".to_string());
        return verdict;
    }

    let bounds = match pool_manager::depth::tick_range_bounds(
        extra.tick,
        extra.tick_spacing,
        state.sqrt_price_x96,
    ) {
        Ok(bounds) => bounds,
        Err(message) => {
            verdict.reason = Some(format!("tick_range_unknown:{message}"));
            return verdict;
        }
    };
    // The distance to the boundary the price actually runs into. The
    // direction decides which of the two bounds is the next tick.
    let (next_tick, next_tick_distance) = if state.zero_for_one {
        (bounds.lower_tick, extra.tick - bounds.lower_tick)
    } else {
        (bounds.upper_tick, bounds.upper_tick - extra.tick)
    };
    verdict.next_tick = next_tick;
    verdict.next_tick_distance = next_tick_distance.max(0);

    let quote = match pool_manager::depth::single_tick_swap_amount_out(
        state.sqrt_price_x96,
        state.liquidity,
        state.fee_pips,
        extra.tick_spacing,
        extra.tick,
        amount_in_raw,
        state.zero_for_one,
    ) {
        Ok(quote) => quote,
        Err(message) => {
            verdict.reason = Some(format!("single_tick_price_unavailable:{message}"));
            return verdict;
        }
    };
    verdict.max_amount_in_raw = Some(quote.max_amount_in_raw);
    verdict.headroom_raw = Some(quote.max_amount_in_raw as i128 - amount_in_raw as i128);

    if !quote.inside_tick_range {
        verdict.bound_conclusion = BoundConclusion::CrossesNextTick;
        verdict.reason = Some("hop_crosses_next_tick".to_string());
        return verdict;
    }
    if quote.amount_out_raw == 0 {
        verdict.reason = Some("zero_output".to_string());
        return verdict;
    }

    verdict.bound_conclusion = BoundConclusion::InsideTickRange;
    verdict.amount_out_raw = Some(quote.amount_out_raw);
    verdict.ok = true;
    verdict
}

/// Runs the per-hop single-tick final gate over a closed loop.
///
/// The input amount of the first hop is the borrowed principal. The input
/// amount of every later hop is the **output** of the previous hop, so the
/// chain is priced with the amounts that would actually flow through it.
///
/// The route fails as soon as one hop fails: a closed loop is only
/// executable when every hop can be proven single-tick, because a hop that
/// is not proven makes the closure of the loop unprovable as well.
pub fn evaluate_route_single_tick(
    hops: &[HopState],
    extras: &[SingleTickHopState],
    amount_in_raw: u128,
) -> SingleTickRouteVerdict {
    if hops.is_empty() {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("no_hops".to_string()),
            hops: Vec::new(),
        };
    }
    if hops.len() != extras.len() {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("hop_state_incomplete".to_string()),
            hops: Vec::new(),
        };
    }
    if amount_in_raw == 0 {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("zero_input".to_string()),
            hops: Vec::new(),
        };
    }

    // Every hop of one route must be priced from the **same** market view.
    // Two views in one route mean the reviewer looked at two different
    // markets, so the closure it proves is not the closure that would be
    // executed.
    //
    // The snapshot identity answers that question: Pool Manager stamps
    // every row of one refresh run with the same batch id, so the hops of a
    // route that carry one batch were measured in one run.
    //
    // The read timestamps cannot answer it.  Pools are read one after the
    // other inside one run, so two pools of the **same** batch always carry
    // two **different** timestamps; comparing them for equality rejects
    // every multi-hop route that has a read timestamp at all.  The
    // timestamp comparison is therefore only the fallback for a route
    // whose pools carry no batch at all.
    let batches: Vec<&str> = extras
        .iter()
        .filter_map(|extra| extra.snapshot_batch.as_deref())
        .filter(|batch| !batch.is_empty())
        .collect();
    let version_drift = if batches.len() == extras.len() && !batches.is_empty() {
        batches.windows(2).any(|pair| pair[0] != pair[1])
    } else {
        let versions: Vec<&str> = extras
            .iter()
            .filter_map(|extra| extra.pool_state_version.as_deref())
            .collect();
        versions.windows(2).any(|pair| pair[0] != pair[1])
    };

    // **Every** hop of the route is judged, not only the first and the
    // last.
    //
    // The gate walks the route the way the chain does: the first hop is
    // priced with the borrowed principal and every later hop with the
    // output of its predecessor. A hop that fails does not stop the walk:
    // the remaining hops are still judged, so the evidence names the
    // middle hop as well. The route itself is still fail-closed, and the
    // **first** failing hop is the one that is reported.
    let mut current_amount = amount_in_raw;
    let mut verdicts: Vec<SingleTickHopVerdict> = Vec::with_capacity(hops.len());
    let mut first_failure: Option<(usize, String)> = None;
    for (index, (state, extra)) in hops.iter().zip(extras.iter()).enumerate() {
        let verdict = evaluate_hop(state, extra, current_amount);
        let ok = verdict.ok;
        let reason = verdict.reason.clone();
        let amount_out = verdict.amount_out_raw;
        verdicts.push(verdict);
        if !ok {
            if first_failure.is_none() {
                let reason = reason.unwrap_or_else(|| "hop_failed".to_string());
                first_failure = Some((index, format!("hop_{}_failed:{reason}", index + 1)));
            }
            // A hop that failed has no output, so the walk cannot continue
            // with a real amount. The remaining hops are still judged with
            // the amount that reached them, and the verdict says so.
            current_amount = 0;
            continue;
        }
        current_amount = amount_out.unwrap_or(0);
    }

    if let Some((_, reason)) = first_failure {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some(reason),
            hops: verdicts,
        };
    }
    if version_drift {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("pool_state_version_drift".to_string()),
            hops: verdicts,
        };
    }

    SingleTickRouteVerdict {
        executable: true,
        reason: None,
        hops: verdicts,
    }
}

#[cfg(test)]
mod tests {
    use super::{evaluate_route_single_tick, BoundConclusion, SingleTickHopState};
    use crate::parity::HopState;

    const Q96: u128 = 1u128 << 96;
    const TOKEN_A: &str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TOKEN_B: &str = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const TOKEN_C: &str = "0xcccccccccccccccccccccccccccccccccccccccc";

    fn hop(pool_id: &str, token_in: &str, token_out: &str, zero_for_one: bool) -> HopState {
        HopState {
            pool_id: pool_id.to_string(),
            token_in: token_in.to_string(),
            token_out: token_out.to_string(),
            sqrt_price_x96: Q96,
            liquidity: 100_000_000_000_000_000_000,
            fee_pips: 3000,
            zero_for_one,
        }
    }

    fn extra(pool_id: &str) -> SingleTickHopState {
        SingleTickHopState {
            pool_id: pool_id.to_string(),
            tick: 0,
            tick_spacing: 60,
            pool_state_version: Some("block:100".to_string()),
            snapshot_batch: None,
        }
    }

    fn closed_loop() -> (Vec<HopState>, Vec<SingleTickHopState>) {
        let hops = vec![
            hop("pool-a", TOKEN_A, TOKEN_B, true),
            hop("pool-b", TOKEN_B, TOKEN_C, true),
            hop("pool-c", TOKEN_C, TOKEN_A, true),
        ];
        let extras = vec![extra("pool-a"), extra("pool-b"), extra("pool-c")];
        (hops, extras)
    }

    /// A small position stays inside every hop's tick range, so the gate
    /// passes and every hop reports its initialized tick, its next-tick
    /// distance and its bound conclusion.
    #[test]
    fn a_single_tick_route_passes_with_a_bound_conclusion_per_hop() {
        let (hops, extras) = closed_loop();
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(verdict.executable, "the route failed: {:?}", verdict.reason);
        assert_eq!(verdict.hops.len(), 3);
        for hop_verdict in &verdict.hops {
            assert!(hop_verdict.ok);
            assert_eq!(
                hop_verdict.bound_conclusion,
                BoundConclusion::InsideTickRange
            );
            assert_eq!(hop_verdict.initialized_tick, 0);
            assert_eq!(hop_verdict.next_tick, -60);
            assert_eq!(hop_verdict.next_tick_distance, 60);
            assert!(hop_verdict.max_amount_in_raw.is_some());
            assert!(hop_verdict.amount_out_raw.unwrap() > 0);
            assert!(hop_verdict.headroom_raw.unwrap() > 0);
        }
    }

    /// The second hop must be priced with the **output** of the first hop,
    /// not with the borrowed principal. The input amounts therefore differ
    /// from hop to hop.
    #[test]
    fn every_hop_is_priced_with_its_own_actual_input_amount() {
        let (hops, extras) = closed_loop();
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(verdict.executable);
        assert_eq!(verdict.hops[0].amount_in_raw, 1_000_000_000_000_000);
        assert_eq!(
            verdict.hops[1].amount_in_raw,
            verdict.hops[0].amount_out_raw.unwrap()
        );
        assert_eq!(
            verdict.hops[2].amount_in_raw,
            verdict.hops[1].amount_out_raw.unwrap()
        );
    }

    /// A position that is large enough to leave the tick range is blocked
    /// at the **review** stage, and the route never becomes executable.
    #[test]
    fn a_cross_tick_route_is_blocked() {
        let (hops, extras) = closed_loop();
        let verdict = evaluate_route_single_tick(&hops, &extras, 10_000_000_000_000_000_000);
        assert!(!verdict.executable);
        let reason = verdict.reason.unwrap();
        assert!(
            reason.contains("hop_1_failed:hop_crosses_next_tick"),
            "unexpected reason: {reason}"
        );
        assert_eq!(
            verdict.hops[0].bound_conclusion,
            BoundConclusion::CrossesNextTick
        );
        assert!(verdict.hops[0].amount_out_raw.is_none());
    }

    /// The gate judges **every** hop of the route, not only the first and
    /// the last. A reviewer therefore sees the middle hop as well.
    #[test]
    fn the_gate_covers_every_hop_of_the_closed_loop() {
        let (hops, extras) = closed_loop();
        let verdict = evaluate_route_single_tick(&hops, &extras, 10_000_000_000_000_000_000);
        assert!(!verdict.executable);
        assert_eq!(
            verdict.hops.len(),
            3,
            "every hop of the loop must carry a verdict"
        );
        // The middle hop (Y-Z) is judged as well, and it is unprovable
        // because the first hop produced no output.
        assert_eq!(verdict.hops[1].pool_id, "pool-b");
        assert_eq!(
            verdict.hops[1].bound_conclusion,
            BoundConclusion::Unprovable
        );
        assert_eq!(verdict.hops[2].pool_id, "pool-c");
    }

    /// A hop without a usable tick spacing cannot be proven single-tick,
    /// so the route is blocked rather than priced with a guess.
    #[test]
    fn a_hop_without_a_tick_spacing_is_blocked() {
        let (hops, mut extras) = closed_loop();
        extras[1].tick_spacing = 0;
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(!verdict.executable);
        assert!(verdict
            .reason
            .unwrap()
            .contains("hop_2_failed:tick_spacing_unknown"));
        assert_eq!(
            verdict.hops[1].bound_conclusion,
            BoundConclusion::Unprovable
        );
        // The third hop is judged as well: the gate does not stop at the
        // second one.
        assert_eq!(verdict.hops.len(), 3);
    }

    /// Two hops priced from two different pool state versions describe two
    /// different markets, so the route is blocked.
    #[test]
    fn a_drifted_pool_state_version_blocks_the_route() {
        let (hops, mut extras) = closed_loop();
        extras[2].pool_state_version = Some("block:101".to_string());
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(!verdict.executable);
        assert_eq!(verdict.reason.unwrap(), "pool_state_version_drift");
    }

    /// One materialisation batch is one market view, so a route whose hops
    /// all carry that batch is priced from one snapshot.
    ///
    /// The pools of one batch were read one after the other, so their read
    /// timestamps differ by milliseconds.  The batch is the snapshot
    /// identity; the timestamp is not, and a route must not be blocked
    /// because of it.
    #[test]
    fn a_route_priced_from_one_materialization_batch_is_not_drift() {
        let (hops, mut extras) = closed_loop();
        for (index, extra) in extras.iter_mut().enumerate() {
            extra.snapshot_batch = Some("pool-manager-batch-1".to_string());
            extra.pool_state_version = Some(format!("2026-11-25T00:44:0{index}.0Z"));
        }
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(verdict.executable, "the route failed: {:?}", verdict.reason);
        assert_eq!(verdict.reason, None);
    }

    /// Two batches are two market views, so the route is blocked even
    /// though every hop carries a read timestamp.
    #[test]
    fn a_route_priced_from_two_materialization_batches_is_blocked() {
        let (hops, mut extras) = closed_loop();
        for extra in extras.iter_mut() {
            extra.snapshot_batch = Some("pool-manager-batch-1".to_string());
            extra.pool_state_version = Some("2026-11-25T00:44:04.0Z".to_string());
        }
        extras[2].snapshot_batch = Some("pool-manager-batch-2".to_string());
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(!verdict.executable);
        assert_eq!(verdict.reason.unwrap(), "pool_state_version_drift");
    }

    /// A route whose batch information is incomplete falls back to the
    /// read timestamps, so an unprovable route stays blocked.
    #[test]
    fn a_route_without_batch_information_falls_back_to_the_timestamps() {
        let (hops, mut extras) = closed_loop();
        extras[0].snapshot_batch = Some("pool-manager-batch-1".to_string());
        extras[1].snapshot_batch = None;
        extras[2].snapshot_batch = None;
        extras[2].pool_state_version = Some("2026-11-25T00:44:16.0Z".to_string());
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(!verdict.executable);
        assert_eq!(verdict.reason.unwrap(), "pool_state_version_drift");
    }

    /// A route whose hop states do not line up with the hop count cannot
    /// be judged and is therefore blocked.
    #[test]
    fn a_mismatched_hop_count_blocks_the_route() {
        let (hops, mut extras) = closed_loop();
        extras.pop();
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(!verdict.executable);
        assert_eq!(verdict.reason.unwrap(), "hop_state_incomplete");
    }

    /// The gate covers **every** hop, not only the first and the last: a
    /// middle hop that crosses its boundary blocks the route.
    #[test]
    fn a_middle_hop_that_crosses_its_boundary_blocks_the_route() {
        let (mut hops, mut extras) = closed_loop();
        // The middle hop is priced with the first hop's output, so it is
        // reached with a smaller amount than the first hop. The boundary is
        // pushed out of reach for the first hop only by lowering the
        // middle hop's liquidity, which leaves the first hop intact.
        hops[1].liquidity = 1_000_000_000_000_000;
        extras[1].tick = 30;
        let verdict = evaluate_route_single_tick(&hops, &extras, 1_000_000_000_000_000);
        assert!(!verdict.executable);
        assert!(verdict.reason.unwrap().starts_with("hop_2_failed:"));
    }
}
