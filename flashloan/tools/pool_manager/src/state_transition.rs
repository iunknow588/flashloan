//! Single-tick execution-state arbitration.
//!
//! The off-chain quote is produced from an expected pool state, while the
//! transaction executes against a later state. Version 1 may only continue
//! when the current state is still inside the expected tick range and the
//! same final input remains single-tick. A favourable move may enlarge the
//! candidate capacity, but the enlarged value is never a profit proof: the
//! review manager must still re-price every hop with the final input.

use alloy_primitives::U256;
use serde::Serialize;

use crate::depth::{single_tick_swap_amount_out, tick_range_bounds};

pub const DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS: u32 = 20_000;
const BPS_DENOMINATOR: u128 = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StateTransitionKind {
    Fatal,
    Adverse,
    Unchanged,
    Favorable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CapacityBasis {
    Unavailable,
    ExpectedState,
    ExactCurrentLiquidity,
    PriceDistanceToken0,
    PriceDistanceToken1,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SingleTickExecutionState {
    pub sqrt_price_x96: u128,
    pub liquidity: u128,
    pub tick: i32,
    pub tick_spacing: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StateTransitionRequest {
    pub expected: SingleTickExecutionState,
    pub current: SingleTickExecutionState,
    pub fee_pips: u32,
    pub zero_for_one: bool,
    pub amount_in_raw: u128,
    pub expected_capacity_raw: u128,
    pub max_capacity_multiplier_bps: u32,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StateTransitionVerdict {
    pub kind: StateTransitionKind,
    pub reason: String,
    pub expected_sqrt_price_x96: String,
    pub current_sqrt_price_x96: String,
    pub expected_liquidity: String,
    pub current_liquidity: String,
    pub boundary_sqrt_price_x96: Option<String>,
    pub expected_amount_out_raw: Option<String>,
    pub current_amount_out_raw: Option<String>,
    pub expected_capacity_raw: String,
    pub exact_current_capacity_raw: Option<String>,
    pub candidate_capacity_raw: Option<String>,
    pub capacity_basis: CapacityBasis,
    pub capacity_multiplier_bps: Option<u32>,
    pub capacity_ratio_numerator: Option<String>,
    pub capacity_ratio_denominator: Option<String>,
    pub sqrt_price_limit_x96: Option<String>,
    pub broadcast_allowed: bool,
}

impl StateTransitionVerdict {
    fn new(request: &StateTransitionRequest) -> Self {
        Self {
            kind: StateTransitionKind::Fatal,
            reason: "state_transition_unproved".to_string(),
            expected_sqrt_price_x96: request.expected.sqrt_price_x96.to_string(),
            current_sqrt_price_x96: request.current.sqrt_price_x96.to_string(),
            expected_liquidity: request.expected.liquidity.to_string(),
            current_liquidity: request.current.liquidity.to_string(),
            boundary_sqrt_price_x96: None,
            expected_amount_out_raw: None,
            current_amount_out_raw: None,
            expected_capacity_raw: request.expected_capacity_raw.to_string(),
            exact_current_capacity_raw: None,
            candidate_capacity_raw: None,
            capacity_basis: CapacityBasis::Unavailable,
            capacity_multiplier_bps: None,
            capacity_ratio_numerator: None,
            capacity_ratio_denominator: None,
            sqrt_price_limit_x96: None,
            broadcast_allowed: false,
        }
    }
}

pub fn evaluate_state_transition(request: &StateTransitionRequest) -> StateTransitionVerdict {
    let mut verdict = StateTransitionVerdict::new(request);
    if request.amount_in_raw == 0
        || request.expected_capacity_raw == 0
        || request.expected.sqrt_price_x96 == 0
        || request.current.sqrt_price_x96 == 0
    {
        verdict.reason = "invalid_zero_input".to_string();
        return verdict;
    }
    if request.expected.liquidity == 0 || request.current.liquidity == 0 {
        verdict.reason = "liquidity_unavailable".to_string();
        return verdict;
    }
    if request.expected.tick_spacing <= 0
        || request.current.tick_spacing != request.expected.tick_spacing
    {
        verdict.reason = "tick_spacing_changed".to_string();
        return verdict;
    }

    let expected_bounds = match tick_range_bounds(
        request.expected.tick,
        request.expected.tick_spacing,
        request.expected.sqrt_price_x96,
    ) {
        Ok(bounds) => bounds,
        Err(error) => {
            verdict.reason = format!("expected_tick_range_unavailable:{error}");
            return verdict;
        }
    };
    let current_bounds = match tick_range_bounds(
        request.current.tick,
        request.current.tick_spacing,
        request.current.sqrt_price_x96,
    ) {
        Ok(bounds) => bounds,
        Err(error) => {
            verdict.reason = format!("current_tick_range_unavailable:{error}");
            return verdict;
        }
    };
    let current_price = request.current.sqrt_price_x96;
    if current_bounds.lower_tick != expected_bounds.lower_tick
        || current_bounds.upper_tick != expected_bounds.upper_tick
        || current_price <= expected_bounds.lower_sqrt_price_x96
        || current_price >= expected_bounds.upper_sqrt_price_x96
    {
        verdict.reason = "tick_range_changed".to_string();
        return verdict;
    }

    let boundary = if request.zero_for_one {
        current_bounds.lower_sqrt_price_x96
    } else {
        current_bounds.upper_sqrt_price_x96
    };
    verdict.boundary_sqrt_price_x96 = Some(boundary.to_string());
    verdict.sqrt_price_limit_x96 = Some(boundary.to_string());

    let expected_quote = match single_tick_swap_amount_out(
        request.expected.sqrt_price_x96,
        request.expected.liquidity,
        request.fee_pips,
        request.expected.tick_spacing,
        request.expected.tick,
        request.amount_in_raw,
        request.zero_for_one,
    ) {
        Ok(quote) if quote.inside_tick_range => quote,
        Ok(_) => {
            verdict.reason = "expected_amount_crosses_tick".to_string();
            return verdict;
        }
        Err(error) => {
            verdict.reason = format!("expected_quote_unavailable:{error}");
            return verdict;
        }
    };
    verdict.expected_amount_out_raw = Some(expected_quote.amount_out_raw.to_string());
    if request.expected_capacity_raw > expected_quote.max_amount_in_raw {
        verdict.reason = "expected_capacity_exceeds_exact_bound".to_string();
        return verdict;
    }

    let current_quote = match single_tick_swap_amount_out(
        request.current.sqrt_price_x96,
        request.current.liquidity,
        request.fee_pips,
        request.current.tick_spacing,
        request.current.tick,
        request.amount_in_raw,
        request.zero_for_one,
    ) {
        Ok(quote) => quote,
        Err(error) => {
            verdict.reason = format!("current_quote_unavailable:{error}");
            return verdict;
        }
    };
    verdict.current_amount_out_raw = Some(current_quote.amount_out_raw.to_string());
    verdict.exact_current_capacity_raw = Some(current_quote.max_amount_in_raw.to_string());
    if request.current.liquidity != request.expected.liquidity {
        verdict.capacity_ratio_numerator = Some(current_quote.max_amount_in_raw.to_string());
        verdict.capacity_ratio_denominator = Some(request.expected_capacity_raw.to_string());
    } else if let Some((numerator, denominator, _)) =
        capacity_ratio_same_liquidity(request, boundary)
    {
        verdict.capacity_ratio_numerator = Some(numerator.to_string());
        verdict.capacity_ratio_denominator = Some(denominator.to_string());
    }
    if !current_quote.inside_tick_range || request.amount_in_raw > current_quote.max_amount_in_raw {
        verdict.kind = StateTransitionKind::Adverse;
        verdict.reason = "current_capacity_below_final_input".to_string();
        return verdict;
    }
    if current_quote.amount_out_raw < expected_quote.amount_out_raw {
        verdict.kind = StateTransitionKind::Adverse;
        verdict.reason = "adverse_price_move".to_string();
        return verdict;
    }

    if current_quote.amount_out_raw == expected_quote.amount_out_raw {
        verdict.kind = StateTransitionKind::Unchanged;
        verdict.reason = "same_input_output_unchanged".to_string();
        let (candidate, basis) = if request.current.liquidity != request.expected.liquidity {
            (
                current_quote.max_amount_in_raw,
                CapacityBasis::ExactCurrentLiquidity,
            )
        } else {
            (
                request
                    .expected_capacity_raw
                    .min(current_quote.max_amount_in_raw),
                CapacityBasis::ExpectedState,
            )
        };
        let max_multiplier_bps = normalized_multiplier_bps(request.max_capacity_multiplier_bps);
        let hard_cap = mul_div_down(
            request.expected_capacity_raw,
            max_multiplier_bps as u128,
            BPS_DENOMINATOR,
        )
        .unwrap_or(request.expected_capacity_raw);
        let candidate = candidate.min(hard_cap).min(current_quote.max_amount_in_raw);
        if candidate < request.amount_in_raw {
            verdict.kind = StateTransitionKind::Adverse;
            verdict.reason = "candidate_capacity_below_final_input".to_string();
            return verdict;
        }
        verdict.candidate_capacity_raw = Some(candidate.to_string());
        verdict.capacity_basis = basis;
        verdict.capacity_multiplier_bps = ratio_bps(candidate, request.expected_capacity_raw);
        verdict.broadcast_allowed = true;
        return verdict;
    }

    let (candidate, basis) = if request.current.liquidity != request.expected.liquidity {
        (
            current_quote.max_amount_in_raw,
            CapacityBasis::ExactCurrentLiquidity,
        )
    } else {
        match scaled_capacity_same_liquidity(request, boundary) {
            Some((capacity, basis)) => (capacity.min(current_quote.max_amount_in_raw), basis),
            None => {
                verdict.reason = "capacity_ratio_unavailable".to_string();
                return verdict;
            }
        }
    };
    let max_multiplier_bps = normalized_multiplier_bps(request.max_capacity_multiplier_bps);
    let hard_cap = mul_div_down(
        request.expected_capacity_raw,
        max_multiplier_bps as u128,
        BPS_DENOMINATOR,
    )
    .unwrap_or(request.expected_capacity_raw);
    let candidate = candidate.min(hard_cap).min(current_quote.max_amount_in_raw);
    if candidate < request.amount_in_raw {
        verdict.kind = StateTransitionKind::Adverse;
        verdict.reason = "candidate_capacity_below_final_input".to_string();
        return verdict;
    }

    verdict.kind = StateTransitionKind::Favorable;
    verdict.reason = "favorable_price_move".to_string();
    verdict.candidate_capacity_raw = Some(candidate.to_string());
    verdict.capacity_basis = basis;
    verdict.capacity_multiplier_bps = ratio_bps(candidate, request.expected_capacity_raw);
    verdict.broadcast_allowed = true;
    verdict
}

fn scaled_capacity_same_liquidity(
    request: &StateTransitionRequest,
    boundary_sqrt_price_x96: u128,
) -> Option<(u128, CapacityBasis)> {
    let (numerator, denominator, basis) =
        capacity_ratio_same_liquidity(request, boundary_sqrt_price_x96)?;
    let capacity = U256::from(request.expected_capacity_raw)
        .checked_mul(numerator)?
        .checked_div(denominator)?;
    Some((capacity.min(U256::from(u128::MAX)).to::<u128>(), basis))
}

fn capacity_ratio_same_liquidity(
    request: &StateTransitionRequest,
    boundary_sqrt_price_x96: u128,
) -> Option<(U256, U256, CapacityBasis)> {
    let old_distance = request
        .expected
        .sqrt_price_x96
        .abs_diff(boundary_sqrt_price_x96);
    let new_distance = request
        .current
        .sqrt_price_x96
        .abs_diff(boundary_sqrt_price_x96);
    if old_distance == 0 || new_distance == 0 {
        return None;
    }
    if request.zero_for_one {
        let numerator =
            U256::from(new_distance).checked_mul(U256::from(request.expected.sqrt_price_x96))?;
        let denominator =
            U256::from(old_distance).checked_mul(U256::from(request.current.sqrt_price_x96))?;
        Some((numerator, denominator, CapacityBasis::PriceDistanceToken0))
    } else {
        Some((
            U256::from(new_distance),
            U256::from(old_distance),
            CapacityBasis::PriceDistanceToken1,
        ))
    }
}

fn mul_div_down(value: u128, numerator: u128, denominator: u128) -> Option<u128> {
    if denominator == 0 {
        return None;
    }
    let result = U256::from(value)
        .checked_mul(U256::from(numerator))?
        .checked_div(U256::from(denominator))?;
    Some(result.min(U256::from(u128::MAX)).to::<u128>())
}

fn ratio_bps(value: u128, baseline: u128) -> Option<u32> {
    let ratio = mul_div_down(value, BPS_DENOMINATOR, baseline)?;
    Some(ratio.min(u32::MAX as u128) as u32)
}

fn normalized_multiplier_bps(configured: u32) -> u32 {
    if configured == 0 {
        DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS
    } else {
        configured.max(10_000)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const Q96: u128 = 79_228_162_514_264_337_593_543_950_336;
    const LIQUIDITY: u128 = 10_000_000_000_000_000_000;

    fn request(zero_for_one: bool) -> StateTransitionRequest {
        let expected = SingleTickExecutionState {
            sqrt_price_x96: Q96 + 150_000_000_000_000_000_000_000_000,
            liquidity: LIQUIDITY,
            tick: 1,
            tick_spacing: 60,
        };
        let quote = single_tick_swap_amount_out(
            expected.sqrt_price_x96,
            expected.liquidity,
            3_000,
            expected.tick_spacing,
            expected.tick,
            1_000_000_000_000,
            zero_for_one,
        )
        .unwrap();
        StateTransitionRequest {
            expected,
            current: expected,
            fee_pips: 3_000,
            zero_for_one,
            amount_in_raw: 1_000_000_000_000,
            expected_capacity_raw: quote.max_amount_in_raw,
            max_capacity_multiplier_bps: DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS,
        }
    }

    #[test]
    fn crossing_the_expected_tick_range_is_fatal() {
        let mut input = request(true);
        let bounds = tick_range_bounds(
            input.expected.tick,
            input.expected.tick_spacing,
            input.expected.sqrt_price_x96,
        )
        .unwrap();
        input.current.sqrt_price_x96 = bounds.lower_sqrt_price_x96;
        input.current.tick = bounds.lower_tick;
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Fatal);
        assert_eq!(verdict.reason, "tick_range_changed");
        assert!(!verdict.broadcast_allowed);
    }

    #[test]
    fn a_worse_exact_output_is_adverse() {
        let mut input = request(true);
        input.current.sqrt_price_x96 -= 50_000_000_000_000_000_000_000_000;
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Adverse);
        assert_eq!(verdict.reason, "adverse_price_move");
        assert!(!verdict.broadcast_allowed);
    }

    #[test]
    fn token0_favorable_move_uses_the_reciprocal_price_factor() {
        let mut input = request(true);
        input.current.sqrt_price_x96 += 50_000_000_000_000_000_000_000_000;
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Favorable);
        assert_eq!(verdict.capacity_basis, CapacityBasis::PriceDistanceToken0);
        let bounds = tick_range_bounds(
            input.current.tick,
            input.current.tick_spacing,
            input.current.sqrt_price_x96,
        )
        .unwrap();
        let expected_numerator =
            U256::from(input.current.sqrt_price_x96 - bounds.lower_sqrt_price_x96)
                * U256::from(input.expected.sqrt_price_x96);
        let expected_denominator =
            U256::from(input.expected.sqrt_price_x96 - bounds.lower_sqrt_price_x96)
                * U256::from(input.current.sqrt_price_x96);
        assert_eq!(
            verdict.capacity_ratio_numerator.as_deref(),
            Some(expected_numerator.to_string().as_str())
        );
        assert_eq!(
            verdict.capacity_ratio_denominator.as_deref(),
            Some(expected_denominator.to_string().as_str())
        );
        let candidate = verdict
            .candidate_capacity_raw
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let exact = verdict
            .exact_current_capacity_raw
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert!(candidate <= exact);
    }

    #[test]
    fn token1_favorable_move_uses_the_linear_distance_ratio() {
        let mut input = request(false);
        input.current.sqrt_price_x96 -= 50_000_000_000_000_000_000_000_000;
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Favorable);
        assert_eq!(verdict.capacity_basis, CapacityBasis::PriceDistanceToken1);
        let bounds = tick_range_bounds(
            input.current.tick,
            input.current.tick_spacing,
            input.current.sqrt_price_x96,
        )
        .unwrap();
        assert_eq!(
            verdict.capacity_ratio_numerator.as_deref(),
            Some(
                (bounds.upper_sqrt_price_x96 - input.current.sqrt_price_x96)
                    .to_string()
                    .as_str()
            )
        );
        assert_eq!(
            verdict.capacity_ratio_denominator.as_deref(),
            Some(
                (bounds.upper_sqrt_price_x96 - input.expected.sqrt_price_x96)
                    .to_string()
                    .as_str()
            )
        );
        assert!(verdict.broadcast_allowed);
    }

    #[test]
    fn changed_liquidity_forces_an_exact_capacity_recompute() {
        let mut input = request(true);
        input.current.sqrt_price_x96 += 50_000_000_000_000_000_000_000_000;
        input.current.liquidity *= 2;
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Favorable);
        assert_eq!(verdict.capacity_basis, CapacityBasis::ExactCurrentLiquidity);
        assert_eq!(
            verdict.capacity_ratio_numerator.as_deref(),
            verdict.exact_current_capacity_raw.as_deref()
        );
        assert_eq!(
            verdict.capacity_ratio_denominator.as_deref(),
            Some(input.expected_capacity_raw.to_string().as_str())
        );
        let candidate = verdict
            .candidate_capacity_raw
            .unwrap()
            .parse::<u128>()
            .unwrap();
        let exact = verdict
            .exact_current_capacity_raw
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert!(candidate <= exact);
        assert!(candidate <= input.expected_capacity_raw * 2);
    }

    #[test]
    fn the_capacity_multiplier_is_hard_capped() {
        let mut input = request(true);
        input.current.sqrt_price_x96 += 50_000_000_000_000_000_000_000_000;
        input.max_capacity_multiplier_bps = 10_050;
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Favorable);
        let candidate = verdict
            .candidate_capacity_raw
            .unwrap()
            .parse::<u128>()
            .unwrap();
        assert!(candidate <= input.expected_capacity_raw * 10_050 / 10_000);
    }

    #[test]
    fn unchanged_state_keeps_the_smaller_exact_capacity() {
        let input = request(true);
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Unchanged);
        assert!(verdict.broadcast_allowed);
        assert_eq!(verdict.capacity_basis, CapacityBasis::ExpectedState);
    }

    #[test]
    fn a_final_input_above_the_current_exact_capacity_is_blocked() {
        let mut input = request(true);
        input.current.sqrt_price_x96 -= 100_000_000_000_000_000_000_000_000;
        let current_quote = single_tick_swap_amount_out(
            input.current.sqrt_price_x96,
            input.current.liquidity,
            input.fee_pips,
            input.current.tick_spacing,
            input.current.tick,
            1,
            input.zero_for_one,
        )
        .unwrap();
        input.amount_in_raw = current_quote.max_amount_in_raw.saturating_add(1);
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Adverse);
        assert_eq!(verdict.reason, "current_capacity_below_final_input");
        assert!(!verdict.broadcast_allowed);
    }

    #[test]
    fn an_untrusted_expected_capacity_above_the_exact_bound_is_fatal() {
        let mut input = request(true);
        input.expected_capacity_raw = input.expected_capacity_raw.saturating_add(1);
        let verdict = evaluate_state_transition(&input);
        assert_eq!(verdict.kind, StateTransitionKind::Fatal);
        assert_eq!(verdict.reason, "expected_capacity_exceeds_exact_bound");
    }
}
