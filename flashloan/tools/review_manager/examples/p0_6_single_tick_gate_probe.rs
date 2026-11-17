use std::collections::BTreeMap;

use review_manager::{
    evaluate_route_single_tick, parity::HopState, BoundConclusion, SingleTickHopState,
};

/// The evidence row of one verified sample.
#[derive(Debug, serde::Serialize)]
struct Row {
    sample: String,
    hop_count: usize,
    executable: bool,
    reason: Option<String>,
    conclusions: Vec<String>,
    initialized_ticks: Vec<i32>,
    next_tick_distances: Vec<i32>,
    actual_inputs: Vec<u128>,
    actual_outputs: Vec<Option<String>>,
}

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

fn extra(pool_id: &str, version: &str) -> SingleTickHopState {
    SingleTickHopState {
        pool_id: pool_id.to_string(),
        tick: 0,
        tick_spacing: 60,
        pool_state_version: Some(version.to_string()),
    }
}

/// The YC route: the anchor token into Y and Y back into the anchor.
fn yc_loop() -> (Vec<HopState>, Vec<SingleTickHopState>) {
    (
        vec![
            hop("pool-yc-a", TOKEN_A, TOKEN_B, true),
            hop("pool-yc-b", TOKEN_B, TOKEN_A, false),
        ],
        vec![
            extra("pool-yc-a", "block:100"),
            extra("pool-yc-b", "block:100"),
        ],
    )
}

/// The Y-Z route: the anchor token into Y and Y into Z.
fn yz_loop() -> (Vec<HopState>, Vec<SingleTickHopState>) {
    (
        vec![
            hop("pool-yz-a", TOKEN_A, TOKEN_B, true),
            hop("pool-yz-b", TOKEN_B, TOKEN_C, true),
        ],
        vec![
            extra("pool-yz-a", "block:100"),
            extra("pool-yz-b", "block:100"),
        ],
    )
}

/// The ZC route: the anchor token into Z and Z back into the anchor.
fn zc_loop() -> (Vec<HopState>, Vec<SingleTickHopState>) {
    (
        vec![
            hop("pool-zc-a", TOKEN_A, TOKEN_C, true),
            hop("pool-zc-b", TOKEN_C, TOKEN_A, false),
        ],
        vec![
            extra("pool-zc-a", "block:100"),
            extra("pool-zc-b", "block:100"),
        ],
    )
}

/// The full closed loop: YC, Y-Z and ZC inside one route.
fn closed_loop() -> (Vec<HopState>, Vec<SingleTickHopState>) {
    (
        vec![
            hop("pool-a", TOKEN_A, TOKEN_B, true),
            hop("pool-yz", TOKEN_B, TOKEN_C, true),
            hop("pool-d", TOKEN_C, TOKEN_A, true),
        ],
        vec![
            extra("pool-a", "block:100"),
            extra("pool-yz", "block:100"),
            extra("pool-d", "block:100"),
        ],
    )
}

fn evaluate(
    sample: &str,
    hops: &[HopState],
    extras: &[SingleTickHopState],
    amount_in_raw: u128,
) -> Row {
    let verdict = evaluate_route_single_tick(hops, extras, amount_in_raw);
    Row {
        sample: sample.to_string(),
        hop_count: hops.len(),
        executable: verdict.executable,
        reason: verdict.reason.clone(),
        conclusions: verdict
            .hops
            .iter()
            .map(|value| value.bound_conclusion.as_str().to_string())
            .collect(),
        initialized_ticks: verdict
            .hops
            .iter()
            .map(|value| value.initialized_tick)
            .collect(),
        next_tick_distances: verdict
            .hops
            .iter()
            .map(|value| value.next_tick_distance)
            .collect(),
        actual_inputs: verdict
            .hops
            .iter()
            .map(|value| value.amount_in_raw)
            .collect(),
        actual_outputs: verdict
            .hops
            .iter()
            .map(|value| value.amount_out_raw.map(|amount| amount.to_string()))
            .collect(),
    }
}

fn main() {
    let small = 1_000_000_000_000_000u128;
    let large = 10_000_000_000_000_000_000u128;
    let mut rows: Vec<Row> = Vec::new();

    // --- the single-tick samples: every hop stays inside its range --------
    let (hops, extras) = yc_loop();
    rows.push(evaluate("yc_single_tick", &hops, &extras, small));
    let (hops, extras) = yz_loop();
    rows.push(evaluate("yz_single_tick", &hops, &extras, small));
    let (hops, extras) = zc_loop();
    rows.push(evaluate("zc_single_tick", &hops, &extras, small));
    let (hops, extras) = closed_loop();
    rows.push(evaluate("closed_loop_single_tick", &hops, &extras, small));

    // --- the cross-tick samples: the gate blocks them ----------------------
    let (hops, extras) = yc_loop();
    rows.push(evaluate("yc_cross_tick", &hops, &extras, large));
    let (hops, extras) = yz_loop();
    rows.push(evaluate("yz_cross_tick", &hops, &extras, large));
    let (hops, extras) = zc_loop();
    rows.push(evaluate("zc_cross_tick", &hops, &extras, large));
    let (hops, extras) = closed_loop();
    rows.push(evaluate("closed_loop_cross_tick", &hops, &extras, large));

    // --- the middle hop is covered, not only the two ends ------------------
    let (mut hops, extras) = closed_loop();
    hops[1].liquidity = 1_000_000_000_000_000;
    let mut extras = extras;
    extras[1].tick = 30;
    rows.push(evaluate(
        "closed_loop_middle_hop_cross",
        &hops,
        &extras,
        small,
    ));

    // --- the state drift sample -------------------------------------------
    let (hops, mut extras) = closed_loop();
    extras[2].pool_state_version = Some("block:101".to_string());
    rows.push(evaluate("closed_loop_state_drift", &hops, &extras, small));

    // --- the unprovable sample --------------------------------------------
    let (hops, mut extras) = closed_loop();
    extras[1].tick_spacing = 0;
    rows.push(evaluate("closed_loop_unprovable", &hops, &extras, small));

    let mut payload = BTreeMap::new();
    payload.insert("rows".to_string(), serde_json::to_value(&rows).unwrap());
    let json = serde_json::to_string_pretty(&payload).unwrap();
    println!("{json}");
}
