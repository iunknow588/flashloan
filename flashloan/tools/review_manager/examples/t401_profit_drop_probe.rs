//! Read-only probe for the `T-复.01` profit-equivalence / `profitDrop` reading.
//!
//! What it measures
//! ----------------
//! The closed-loop layer does not store profit: the profit of a loop is the
//! profit of its **effective amount** (the folded single-tick capacity) at
//! the prices of the moment.  The `M-4` switch changed *which* capacity a
//! loop carries, and for six loops *which pools* it walks.  This probe
//! therefore compares two closed-loop generations **at one price snapshot**:
//! the capacity and the pools of the older generation are replayed against
//! the pool states of the newer generation, and the newer generation is
//! replayed against the very same states.
//!
//! Two readings come out of that one experiment:
//!
//! * **profit equivalence** -- for every loop whose path (pools and
//!   directions) and capacity are unchanged, the effective profit of the
//!   old path and of the new path have to be **equal**, loop by loop.  The
//!   probe checks that one loop at a time and reports the exceptions.
//! * **`profitDrop`** -- for every loop the change actually touched, the
//!   relative drop of the effective profit, and the **median** of those
//!   drops.  A loop whose old profit is zero has no relative drop; it is
//!   reported as such instead of being turned into a number.
//!
//! Why the arithmetic is not re-implemented here
//! ---------------------------------------------
//! The profit of a route is the output of the **kernel** swap arithmetic.
//! A second copy of that arithmetic would be a second rounding rule, so the
//! probe calls the functions Review Manager publishes
//! (`build_hop_chain`, `simulate_route`) and Pool Manager publishes
//! (`read_pool_parity_states_on_connection`).  The probe reads; it never
//! writes.
//!
//! Usage
//! -----
//! `cargo run --offline -p review-manager --example t401_profit_drop_probe`
//! with `--live <current store> --baseline <pre-switch store> --label <name>`

use std::collections::{BTreeMap, BTreeSet};
use std::str::FromStr;

use manager_contracts::DirectionalPoolId;
use pool_manager::{read_pool_parity_states_on_connection, PoolParityState};
use review_manager::{build_hop_chain, simulate_route, HopDirectionList};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;

const NETWORK: &str = "robinhood";
const CHAIN_ID: i64 = 4663;
const PROTOCOL: &str = "uniswap_v4";
const SCOPE_KEY: &str = "core4";

/// One closed loop of one generation.
#[derive(Debug, Clone)]
struct LoopRow {
    loop_key: String,
    path_addresses: Vec<String>,
    /// The directional pools the loop walks, as the closed loop publishes
    /// them.  The base pool travels inside every one of them.
    directional_pool_ids: Vec<String>,
    capacity_anchor_raw: Option<u128>,
    hop_directions: HopDirectionList,
    /// The directional pool of every hop, in hop order.
    hop_directional_pools: Vec<String>,
}

/// The measured outcome of one loop.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct LoopMeasurement {
    loop_key: String,
    // The 128-bit readings are reported as **strings**.  A capacity anchor
    // is a wei amount that does not fit an u64, and a profit may not fit an
    // i64 either, so a JSON number would either fail to serialise or lose
    // precision.  The same rule the data layer follows for its own rows.
    old_capacity_raw: Option<String>,
    new_capacity_raw: Option<String>,
    capacity_changed: bool,
    pools_changed: bool,
    directions_changed: bool,
    old_profit_raw: Option<String>,
    new_profit_raw: Option<String>,
    old_reason: Option<String>,
    new_reason: Option<String>,
    relative_drop: Option<f64>,
    drop_status: String,
    // The same two paths replayed at the **same amount** (the old
    // capacity), and the profit per unit of that amount.  Without these two
    // readings the raw drop cannot be told apart from a smaller capacity
    // envelope: a smaller trade earns less profit even when the route
    // itself did not get worse.
    common_amount_raw: Option<String>,
    old_profit_at_common_amount_raw: Option<String>,
    new_profit_at_common_amount_raw: Option<String>,
    old_profit_rate: Option<f64>,
    new_profit_rate: Option<f64>,
    rate_change: Option<f64>,
    // How many hops the kernel refused because the stored direction and
    // the route's own token order disagree.  A hop the kernel refuses is a
    // hop that is missing from the route, so the count is reported.
    old_direction_mismatches: usize,
    new_direction_mismatches: usize,
    affected: bool,
}

/// The aggregate of one comparison.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct Comparison {
    label: String,
    common_loops: usize,
    only_in_baseline: usize,
    only_in_live: usize,
    capacity_changed_loops: usize,
    pools_changed_loops: usize,
    directions_changed_loops: usize,
    affected_loops: usize,
    unchanged_loops: usize,
    unchanged_loops_with_equal_profit: usize,
    unchanged_loops_with_unequal_profit: usize,
    measurable_drops: usize,
    median_drop: Option<f64>,
    mean_drop: Option<f64>,
    min_drop: Option<f64>,
    max_drop: Option<f64>,
    // The same attribution, measured at one common amount: how the profit
    // of the two paths compares when both of them trade the same size, and
    // how the profit **per unit of that size** compares.
    measurable_common_amount_changes: usize,
    median_common_amount_change: Option<f64>,
    measurable_rate_changes: usize,
    median_rate_change: Option<f64>,
    baseline_direction_mismatches: usize,
    live_direction_mismatches: usize,
}

fn open_read_only(path: &str) -> Result<Connection, String> {
    Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_URI
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|error| format!("open {path}: {error}"))
}

fn parse_json_array(raw: Option<String>) -> Vec<String> {
    let text = raw.unwrap_or_default();
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }
    serde_json::from_str::<Vec<String>>(trimmed).unwrap_or_default()
}

/// The base pool a published pool identity names.
///
/// The closed loop publishes the **directional** pools it walks, and the
/// base pool travels inside the directional pool id:
/// `dpool:v1:{network}:{chain_id}:{scope}:{base_pool_id}:{direction}`.
/// The pool states the probe replays are published per **base** pool, so
/// the base pool is resolved here, at the boundary.
///
/// An identity that is not a directional pool id is already a pool id and
/// is returned unchanged: the resolution never invents a pool.
fn base_pool_of(published_pool_id: &str) -> String {
    let trimmed = published_pool_id.trim();
    match DirectionalPoolId::from_str(trimmed) {
        Ok(directional) => directional.base_pool_id().to_owned(),
        Err(_) => trimmed.to_owned(),
    }
}

fn read_generation(path: &str) -> Result<(BTreeMap<String, LoopRow>, String, i64), String> {
    let connection = open_read_only(path)?;
    let version: i64 = connection
        .query_row(
            "SELECT closed_loop_version FROM manager_v4_closed_loop_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
            rusqlite::params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
            |row| row.get(0),
        )
        .map_err(|error| format!("read closed loop version of {path}: {error}"))?;
    let checksum: String = connection
        .query_row(
            "SELECT materialized_checksum FROM manager_v4_closed_loop_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
            rusqlite::params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
            |row| row.get(0),
        )
        .map_err(|error| format!("read closed loop checksum of {path}: {error}"))?;

    let mut hops: BTreeMap<String, Vec<(i64, String, Option<i64>)>> = BTreeMap::new();
    {
        let mut statement = connection
            .prepare(
                "SELECT loop_key, hop_index, directional_pool_id, hop_direction
                 FROM manager_v4_closed_loop_hops
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
                 ORDER BY loop_key, hop_index",
            )
            .map_err(|error| format!("prepare hops of {path}: {error}"))?;
        let rows = statement
            .query_map(
                rusqlite::params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, Option<i64>>(3)?,
                    ))
                },
            )
            .map_err(|error| format!("query hops of {path}: {error}"))?;
        for row in rows.flatten() {
            hops.entry(row.0).or_default().push((row.1, row.2, row.3));
        }
    }

    let mut loops: BTreeMap<String, LoopRow> = BTreeMap::new();
    {
        let mut statement = connection
            .prepare(
                "SELECT loop_key, path_addresses_json, directional_pool_ids_json,
                        single_tick_capacity_anchor_raw
                 FROM manager_v4_closed_loops
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
            )
            .map_err(|error| format!("prepare loops of {path}: {error}"))?;
        let rows = statement
            .query_map(
                rusqlite::params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
                |row| {
                    Ok(LoopRow {
                        loop_key: row.get::<_, String>(0)?,
                        path_addresses: parse_json_array(row.get::<_, Option<String>>(1)?),
                        directional_pool_ids: parse_json_array(row.get::<_, Option<String>>(2)?),
                        capacity_anchor_raw: row
                            .get::<_, Option<String>>(3)?
                            .and_then(|value| value.trim().parse::<u128>().ok()),
                        hop_directions: Vec::new(),
                        hop_directional_pools: Vec::new(),
                    })
                },
            )
            .map_err(|error| format!("query loops of {path}: {error}"))?;
        for mut row in rows.flatten() {
            if let Some(entries) = hops.get(&row.loop_key) {
                row.hop_directions = entries.iter().map(|entry| entry.2).collect();
                row.hop_directional_pools = entries.iter().map(|entry| entry.1.clone()).collect();
            }
            loops.insert(row.loop_key.clone(), row);
        }
    }
    Ok((loops, checksum, version))
}

/// One simulation of a path at one amount.
fn profit_at(
    row: &LoopRow,
    states: &BTreeMap<String, PoolParityState>,
    amount_raw: u128,
) -> (Option<i128>, Option<String>, usize) {
    if amount_raw == 0 {
        return (None, Some("capacity_unavailable".to_string()), 0);
    }
    // The kernel simulates a signed amount.  A capacity that does not fit a
    // signed 128-bit integer would wrap into a negative amount and the
    // simulation would answer a question nobody asked, so it is refused.
    if amount_raw > i128::MAX as u128 {
        return (
            None,
            Some("capacity_exceeds_signed_128_bit".to_string()),
            0,
        );
    }
    let pool_ids = if row.directional_pool_ids.is_empty() {
        row.hop_directional_pools.clone()
    } else {
        row.directional_pool_ids.clone()
    };
    // The chain is replayed against the **base** pools: the pool states
    // the kernel reads are published per base pool, and the base pool
    // travels inside every directional pool id.
    let base_pool_ids: Vec<String> = pool_ids.iter().map(|pool_id| base_pool_of(pool_id)).collect();
    let Some(chain) = build_hop_chain(
        &row.path_addresses,
        states,
        &base_pool_ids,
        Some(&row.hop_directions),
    ) else {
        return (None, Some("hop_chain_unavailable".to_string()), 0);
    };
    if chain.hops.is_empty() {
        return (
            None,
            Some("all_hops_dropped".to_string()),
            chain.direction_mismatches.len(),
        );
    }
    let mismatches = chain.direction_mismatches.len();
    let result = simulate_route(&chain.hops, amount_raw as i128, 0);
    if result.profit_raw == 0 {
        return (Some(0), Some(result.reason.clone()), mismatches);
    }
    (Some(result.profit_raw), None, mismatches)
}

fn median(values: &mut Vec<f64>) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    let middle = values.len() / 2;
    if values.len() % 2 == 1 {
        Some(values[middle])
    } else {
        Some((values[middle - 1] + values[middle]) / 2.0)
    }
}

/// Keep a floating-point reading only when it is a real number.
///
/// `serde_json` refuses to serialise `NaN` and `inf`, and the report has to
/// survive a broken loop.  A non-finite reading is therefore dropped here
/// and the reason is reported next to it, instead of the whole report
/// failing to serialise.
fn finite(value: Option<f64>) -> Option<f64> {
    value.filter(|number| number.is_finite())
}

fn compare(
    label: &str,
    baseline: &BTreeMap<String, LoopRow>,
    live: &BTreeMap<String, LoopRow>,
    states: &BTreeMap<String, PoolParityState>,
) -> (Comparison, Vec<LoopMeasurement>) {
    let mut measurements: Vec<LoopMeasurement> = Vec::new();
    let mut only_in_baseline = 0usize;
    for (loop_key, old) in baseline {
        let Some(new) = live.get(loop_key) else {
            only_in_baseline += 1;
            continue;
        };
        let old_capacity = old.capacity_anchor_raw;
        let new_capacity = new.capacity_anchor_raw;
        let capacity_changed = old_capacity != new_capacity;
        let old_pools = if old.directional_pool_ids.is_empty() {
            old.hop_directional_pools.clone()
        } else {
            old.directional_pool_ids.clone()
        };
        let new_pools = if new.directional_pool_ids.is_empty() {
            new.hop_directional_pools.clone()
        } else {
            new.directional_pool_ids.clone()
        };
        let pools_changed = old_pools != new_pools;
        let directions_changed = old.hop_directions != new.hop_directions;
        let (old_profit, old_reason, old_mismatches) = match old_capacity {
            Some(amount) => profit_at(old, states, amount),
            None => (None, Some("capacity_unavailable".to_string()), 0),
        };
        let (new_profit, new_reason, new_mismatches) = match new_capacity {
            Some(amount) => profit_at(new, states, amount),
            None => (None, Some("capacity_unavailable".to_string()), 0),
        };
        // The drop is computed in floating point only.  The profits are
        // 128-bit integers, so the subtraction is done as two independent
        // conversions: an integer subtraction could overflow on a very
        // large negative profit and would abort the probe.
        let (relative_drop, drop_status) = match (old_profit, new_profit) {
            (Some(old_value), Some(new_value)) if old_value > 0 => {
                let old_f64 = old_value as f64;
                let new_f64 = new_value as f64;
                let drop = (old_f64 - new_f64) / old_f64;
                if drop.is_finite() {
                    (Some(drop), "measured".to_string())
                } else {
                    (None, "non_finite_ratio".to_string())
                }
            }
            (Some(_), Some(_)) => (None, "baseline_profit_not_positive".to_string()),
            (Some(_), None) => (None, "new_profit_unavailable".to_string()),
            (None, Some(_)) => (None, "baseline_profit_unavailable".to_string()),
            (None, None) => (None, "no_profit_on_both_sides".to_string()),
        };
        let affected = capacity_changed || pools_changed || directions_changed;
        // Replay both paths at one common amount, so the reading answers
        // "did the route get worse" instead of "did the trade get smaller".
        let (common_amount_raw, old_at_common, new_at_common) = match old_capacity {
            Some(amount) => {
                let old_profit = profit_at(old, states, amount).0;
                let new_profit = profit_at(new, states, amount).0;
                (Some(amount), old_profit, new_profit)
            }
            None => (None, None, None),
        };
        let old_rate = match (old_profit, old_capacity) {
            (Some(profit), Some(amount)) if amount > 0 && profit > 0 => {
                Some(profit as f64 / amount as f64)
            }
            _ => None,
        };
        let new_rate = match (new_profit, new_capacity) {
            (Some(profit), Some(amount)) if amount > 0 && profit > 0 => {
                Some(profit as f64 / amount as f64)
            }
            _ => None,
        };
        let rate_change = match (old_rate, new_rate) {
            (Some(old_rate), Some(new_rate)) if old_rate > 0.0 => {
                let change = (new_rate - old_rate) / old_rate;
                if change.is_finite() {
                    Some(change)
                } else {
                    None
                }
            }
            _ => None,
        };
        measurements.push(LoopMeasurement {
            loop_key: loop_key.clone(),
            old_capacity_raw: old_capacity.map(|value| value.to_string()),
            new_capacity_raw: new_capacity.map(|value| value.to_string()),
            capacity_changed,
            pools_changed,
            directions_changed,
            old_profit_raw: old_profit.map(|value| value.to_string()),
            new_profit_raw: new_profit.map(|value| value.to_string()),
            old_reason,
            new_reason,
            relative_drop,
            drop_status,
            common_amount_raw: common_amount_raw.map(|value| value.to_string()),
            old_profit_at_common_amount_raw: old_at_common.map(|value| value.to_string()),
            new_profit_at_common_amount_raw: new_at_common.map(|value| value.to_string()),
            old_profit_rate: finite(old_rate),
            new_profit_rate: finite(new_rate),
            rate_change: finite(rate_change),
            old_direction_mismatches: old_mismatches,
            new_direction_mismatches: new_mismatches,
            affected,
        });
    }
    let only_in_live = live.keys().filter(|key| !baseline.contains_key(*key)).count();

    let capacity_changed_loops = measurements.iter().filter(|item| item.capacity_changed).count();
    let pools_changed_loops = measurements.iter().filter(|item| item.pools_changed).count();
    let directions_changed_loops = measurements
        .iter()
        .filter(|item| item.directions_changed)
        .count();
    let affected_loops = measurements.iter().filter(|item| item.affected).count();
    let unchanged: Vec<&LoopMeasurement> =
        measurements.iter().filter(|item| !item.affected).collect();
    let mut unchanged_equal = 0usize;
    let mut unchanged_unequal = 0usize;
    for item in &unchanged {
        // The profits are compared as their decimal strings: the string is
        // the canonical decimal form of the 128-bit integer, so the
        // comparison is exact and does not go through a float.
        match (
            item.old_profit_raw.as_deref(),
            item.new_profit_raw.as_deref(),
        ) {
            (Some(left), Some(right)) if left == right => unchanged_equal += 1,
            _ => unchanged_unequal += 1,
        }
    }
    let drops: Vec<f64> = measurements
        .iter()
        .filter(|item| item.affected)
        .filter_map(|item| item.relative_drop)
        .collect();
    let measurable_drops = drops.len();
    let mean = if drops.is_empty() {
        None
    } else {
        Some(drops.iter().sum::<f64>() / drops.len() as f64)
    };
    // The drops are sorted before the extremes are read: the extremes are
    // the smallest and the largest drop, not the first and the last one
    // that happened to be collected.
    let mut sorted = drops.clone();
    let median_value = median(&mut sorted);
    let min = sorted.first().copied();
    let max = sorted.last().copied();

    // The profit change of the two paths at one common amount.
    let mut common_amount_changes: Vec<f64> = Vec::new();
    for item in &measurements {
        let old_at_common = item
            .old_profit_at_common_amount_raw
            .as_deref()
            .and_then(|value| value.parse::<i128>().ok());
        let new_at_common = item
            .new_profit_at_common_amount_raw
            .as_deref()
            .and_then(|value| value.parse::<i128>().ok());
        if let (Some(old_value), Some(new_value)) = (old_at_common, new_at_common) {
            if old_value > 0 {
                let change = (old_value as f64 - new_value as f64) / old_value as f64;
                if change.is_finite() {
                    common_amount_changes.push(change);
                }
            }
        }
    }
    let measurable_common_amount_changes = common_amount_changes.len();
    let mut sorted_common = common_amount_changes.clone();
    let median_common_amount_change = median(&mut sorted_common);

    // The profit per unit of the traded amount.
    let mut rate_changes: Vec<f64> = measurements
        .iter()
        .filter_map(|item| item.rate_change)
        .collect();
    let measurable_rate_changes = rate_changes.len();
    let median_rate_change = median(&mut rate_changes);
    let baseline_direction_mismatches = measurements
        .iter()
        .map(|item| item.old_direction_mismatches)
        .sum();
    let live_direction_mismatches = measurements
        .iter()
        .map(|item| item.new_direction_mismatches)
        .sum();
    (
        Comparison {
            label: label.to_string(),
            common_loops: measurements.len(),
            only_in_baseline,
            only_in_live,
            capacity_changed_loops,
            pools_changed_loops,
            directions_changed_loops,
            affected_loops,
            unchanged_loops: unchanged.len(),
            unchanged_loops_with_equal_profit: unchanged_equal,
            unchanged_loops_with_unequal_profit: unchanged_unequal,
            measurable_drops,
            median_drop: finite(median_value),
            mean_drop: finite(mean),
            min_drop: finite(min),
            max_drop: finite(max),
            measurable_common_amount_changes,
            median_common_amount_change: finite(median_common_amount_change),
            measurable_rate_changes,
            median_rate_change: finite(median_rate_change),
            baseline_direction_mismatches,
            live_direction_mismatches,
        },
        measurements,
    )
}

fn main() {
    let mut live_path = String::new();
    let mut baseline_path = String::new();
    let mut label = String::new();
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let value = args.next().unwrap_or_default();
        match arg.as_str() {
            "--live" => live_path = value,
            "--baseline" => baseline_path = value,
            "--label" => label = value,
            _ => {}
        }
    }
    if live_path.is_empty() || baseline_path.is_empty() {
        eprintln!("usage: t401_profit_drop_probe --live <db> --baseline <db> --label <name>");
        std::process::exit(2);
    }

    let (baseline_loops, baseline_checksum, baseline_version) =
        read_generation(&baseline_path).expect("read baseline generation");
    let (live_loops, live_checksum, live_version) =
        read_generation(&live_path).expect("read live generation");

    // One price snapshot: every pool of both generations is read once, from
    // the newest liquidity snapshot the pool manager holds.  Both paths are
    // then replayed against these very states, so the comparison cannot be
    // an artefact of two different moments.
    let mut pool_ids: BTreeSet<String> = BTreeSet::new();
    for row in baseline_loops.values().chain(live_loops.values()) {
        for published in row
            .directional_pool_ids
            .iter()
            .chain(row.hop_directional_pools.iter())
        {
            // The parity states are published per **base** pool.
            let base_pool_id = base_pool_of(published);
            if !base_pool_id.trim().is_empty() {
                pool_ids.insert(base_pool_id.trim().to_lowercase());
            }
        }
    }
    let wanted: Vec<String> = pool_ids.into_iter().collect();
    let connection = open_read_only(&live_path).expect("open live store");
    let read_states = read_pool_parity_states_on_connection(
        &connection,
        NETWORK,
        CHAIN_ID,
        PROTOCOL,
        &wanted,
    )
    .expect("read pool states");
    let states: BTreeMap<String, PoolParityState> = read_states
        .into_iter()
        .map(|state| (state.pool_id.trim().to_lowercase(), state))
        .collect();

    let (summary, measurements) = compare(&label, &baseline_loops, &live_loops, &states);
    let report = serde_json::json!({
        "probe": "t401_profit_drop_probe",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "protocol": PROTOCOL,
        "scopeKey": SCOPE_KEY,
        "baseline": {
            "path": baseline_path,
            "closedLoopVersion": baseline_version,
            "materializedChecksum": baseline_checksum,
            "loopCount": baseline_loops.len(),
        },
        "live": {
            "path": live_path,
            "closedLoopVersion": live_version,
            "materializedChecksum": live_checksum,
            "loopCount": live_loops.len(),
        },
        "priceSnapshot": {
            "source": "v4_exchange_pools JOIN v4_exchange_pool_liquidity_snapshots",
            "readAt": chrono::Utc::now().to_rfc3339(),
            "poolsRequested": wanted.len(),
            "poolsWithState": states.len(),
            "note": "both paths are replayed against these very states",
        },
        "comparison": summary,
        "loops": measurements,
    });
    println!(
        "{}",
        serde_json::to_string_pretty(&report).unwrap_or_else(|_| "{}".to_string())
    );
}
