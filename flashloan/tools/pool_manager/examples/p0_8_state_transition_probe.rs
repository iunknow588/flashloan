use chrono::Utc;
use pool_manager::depth::{single_tick_swap_amount_out, tick_range_bounds};
use pool_manager::{
    evaluate_state_transition, SingleTickExecutionState, StateTransitionRequest,
    DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS,
};
use serde::Serialize;

const Q96: u128 = 79_228_162_514_264_337_593_543_950_336;
const LIQUIDITY: u128 = 10_000_000_000_000_000_000;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct EvidenceCase {
    name: &'static str,
    verdict: pool_manager::StateTransitionVerdict,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct EvidenceReport {
    schema_version: &'static str,
    generated_at: String,
    stage: &'static str,
    component: &'static str,
    mode: &'static str,
    production_write: bool,
    passed: bool,
    case_count: usize,
    cases: Vec<EvidenceCase>,
}

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
    .expect("fixture quote");
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

fn main() {
    let unchanged = request(true);

    let mut adverse = request(true);
    adverse.current.sqrt_price_x96 -= 50_000_000_000_000_000_000_000_000;

    let mut favorable_token0 = request(true);
    favorable_token0.current.sqrt_price_x96 += 50_000_000_000_000_000_000_000_000;

    let mut favorable_token1 = request(false);
    favorable_token1.current.sqrt_price_x96 -= 50_000_000_000_000_000_000_000_000;

    let mut liquidity_changed = request(true);
    liquidity_changed.current.sqrt_price_x96 += 50_000_000_000_000_000_000_000_000;
    liquidity_changed.current.liquidity *= 2;

    let mut crossed_tick = request(true);
    let bounds = tick_range_bounds(
        crossed_tick.expected.tick,
        crossed_tick.expected.tick_spacing,
        crossed_tick.expected.sqrt_price_x96,
    )
    .expect("fixture bounds");
    crossed_tick.current.sqrt_price_x96 = bounds.lower_sqrt_price_x96;
    crossed_tick.current.tick = bounds.lower_tick;

    let inputs = [
        ("unchanged", unchanged),
        ("adverse_exact_output", adverse),
        ("favorable_token0", favorable_token0),
        ("favorable_token1", favorable_token1),
        ("liquidity_changed", liquidity_changed),
        ("crossed_tick", crossed_tick),
    ];
    let cases = inputs
        .into_iter()
        .map(|(name, input)| EvidenceCase {
            name,
            verdict: evaluate_state_transition(&input),
        })
        .collect::<Vec<_>>();
    let passed = cases.iter().all(|case| match case.name {
        "unchanged" => case.verdict.broadcast_allowed,
        "favorable_token0" | "favorable_token1" | "liquidity_changed" => {
            case.verdict.broadcast_allowed
        }
        "adverse_exact_output" | "crossed_tick" => !case.verdict.broadcast_allowed,
        _ => false,
    });
    let report = EvidenceReport {
        schema_version: "1.0",
        generated_at: Utc::now().to_rfc3339(),
        stage: "P0-8",
        component: "pool_manager.state_transition",
        mode: "test",
        production_write: false,
        passed,
        case_count: cases.len(),
        cases,
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&report).expect("serialize evidence")
    );
}
