//! Read-only A/B of the USDG valuation's market snapshot source (`T-闭.02`).
//!
//! The probe answers one question: **what changes** in the closed loop's
//! profit gate when the valuation reads its market half from the liquidity
//! snapshot table instead of the base pool tables.
//!
//! It reads the live store in read-only mode (`mode=ro` plus
//! `PRAGMA query_only = ON`) and never writes.
//!
//! Two snapshots are compared over the **same** pool universe:
//!
//! * **A -- base pool tables**: the reader that shipped before the change.
//!   Both halves -- the price *and* the static facts -- come from
//!   `v4_exchange_pools_candidate_exchange` / `v4_exchange_pools_core4`.
//! * **B -- liquidity snapshot table**: the reader that ships now.  The
//!   static facts still come from the base pool tables, the market half
//!   from `v4_exchange_pool_liquidity_snapshots`.
//!
//! The valuation arithmetic is **not** re-implemented: the probe calls the
//! shipped `usdg_valuation::value_tokens_in_usdg`, so both sides are
//! valued by the production code.
//!
//! The A side is self-checking.  The live closed loop rows carry the
//! `borrow_token_usdg_value_raw` that the shipped code published, and the
//! probe recomputes the same numbers from the same source.  A mismatch
//! means the A side of the probe is not the code that published the rows,
//! and the whole comparison would mean nothing.
//!
//! Usage:
//!
//! ```text
//! cargo run --release --offline --example valuation_snapshot_ab_20261126 -- \
//!     <store.sqlite3> <network> <chain_id> <report.json>
//! ```

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use closed_loop_manager::usdg_valuation::{value_tokens_in_usdg, ValuationPoolState};
use rusqlite::{params, Connection, OpenFlags};
use serde_json::{json, Value};

const NETWORK_PROTOCOL: &str = "uniswap_v4";
const USDG_SYMBOL: &str = "USDG";
const BASE_POOL_TABLES: [&str; 2] = [
    "v4_exchange_pools_candidate_exchange",
    "v4_exchange_pools_core4",
];
const SNAPSHOT_TABLE: &str = "v4_exchange_pool_liquidity_snapshots";

fn main() {
    if let Err(error) = run() {
        eprintln!("valuation snapshot A/B failed: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let store = arguments
        .first()
        .map(PathBuf::from)
        .unwrap_or_else(|| default_store());
    let network = arguments
        .get(1)
        .cloned()
        .unwrap_or_else(|| "robinhood".to_owned());
    let chain_id: i64 = arguments
        .get(2)
        .map(|value| value.parse::<i64>().unwrap_or_default())
        .unwrap_or(4663);
    let report_path = arguments
        .get(3)
        .map(PathBuf::from)
        .unwrap_or_else(|| default_report_path());

    if !store.is_file() {
        return Err(format!("store not found: {}", store.display()));
    }

    let connection = open_read_only(&store)?;

    // --- the two readers -------------------------------------------------
    let legacy = read_legacy_states(&connection, &network, chain_id)?;
    let current = read_current_states(&connection, &network, chain_id)?;

    // --- the valuation ---------------------------------------------------
    let decimals = read_token_decimals(&connection, &network, chain_id)?;
    let usdg_token_key_id = read_usdg_token_key_id(&connection, &network, chain_id)?;
    let Some(usdg_token_key_id) = usdg_token_key_id else {
        return Err("the quote asset (USDG) is absent from the token catalog".to_owned());
    };
    let usdg_decimals = *decimals
        .get(&usdg_token_key_id)
        .ok_or_else(|| format!("the quote asset {usdg_token_key_id} carries no decimals"))?;

    let legacy_valuation = value_all(&legacy, &usdg_token_key_id, &decimals, usdg_decimals);
    let current_valuation = value_all(&current, &usdg_token_key_id, &decimals, usdg_decimals);

    // --- the comparison --------------------------------------------------
    let legacy_tokens: BTreeSet<&String> = legacy_valuation.keys().collect();
    let current_tokens: BTreeSet<&String> = current_valuation.keys().collect();
    let only_legacy: Vec<&String> = legacy_tokens.difference(&current_tokens).copied().collect();
    let only_current: Vec<&String> = current_tokens.difference(&legacy_tokens).copied().collect();

    let mut token_rows: Vec<Value> = Vec::new();
    let mut changed_tokens = 0usize;
    let mut changed_pool = 0usize;
    let mut max_ppm = 0u128;
    let mut max_ppm_token: Option<String> = None;
    for token_key_id in &current_tokens {
        let Some(before) = legacy_valuation.get(*token_key_id) else {
            continue;
        };
        let after = &current_valuation[*token_key_id];
        let delta = after.value_usdg_raw.abs_diff(before.value_usdg_raw);
        let ppm = if before.value_usdg_raw == 0 {
            0
        } else {
            delta * 1_000_000u128 / before.value_usdg_raw.max(1)
        };
        if delta > 0 {
            changed_tokens += 1;
        }
        if before.pool_id != after.pool_id {
            changed_pool += 1;
        }
        if ppm > max_ppm {
            max_ppm = ppm;
            max_ppm_token = Some((*token_key_id).clone());
        }
        token_rows.push(json!({
            "tokenKeyId": token_key_id,
            "valueUsdgRawBefore": before.value_usdg_raw.to_string(),
            "valueUsdgRawAfter": after.value_usdg_raw.to_string(),
            "deltaUsdgRaw": delta.to_string(),
            "deltaPpm": ppm.to_string(),
            "poolBefore": before.pool_id,
            "poolAfter": after.pool_id,
        }));
    }

    // --- the pools that lost their state -------------------------------
    let dropped: Vec<String> = legacy
        .keys()
        .filter(|pool_id| !current.contains_key(*pool_id))
        .cloned()
        .collect();
    let mut dropped_rows: Vec<Value> = Vec::new();
    for pool_id in &dropped {
        let snapshot = connection
            .query_row(
                "SELECT sqrt_price_x96, tick, liquidity FROM v4_exchange_pool_liquidity_snapshots
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND pool_id = ?4",
                params![network, chain_id, NETWORK_PROTOCOL, pool_id],
                |row| {
                    Ok((
                        row.get::<_, Option<String>>(0)?,
                        row.get::<_, Option<i64>>(1)?,
                        row.get::<_, Option<String>>(2)?,
                    ))
                },
            )
            .ok();
        let legacy_state = legacy.get(pool_id);
        dropped_rows.push(json!({
            "poolId": pool_id,
            "legacySqrtPriceX96": legacy_state.map(|state| state.sqrt_price_x96.to_string()),
            "snapshotRowPresent": snapshot.is_some(),
            "snapshotSqrtPriceX96": snapshot
                .as_ref()
                .and_then(|row| row.0.clone()),
            "snapshotTick": snapshot.as_ref().and_then(|row| row.1),
            "snapshotLiquidity": snapshot.as_ref().and_then(|row| row.2.clone()),
        }));
    }

    // --- the closed loop rows -------------------------------------------
    let loops = read_published_loops(&connection, &network, chain_id)?;
    let mut loop_rows: Vec<Value> = Vec::new();
    let mut self_check_matched = 0usize;
    let mut self_check_mismatched = 0usize;
    let mut loops_with_valuation_change = 0usize;
    let mut loops_losing_valuation = 0usize;
    let mut loops_without_published_value = 0usize;
    for entry in &loops {
        let before = legacy_valuation.get(&entry.anchor_token_key_id);
        let after = current_valuation.get(&entry.anchor_token_key_id);
        let published = entry.borrow_token_usdg_value_raw.map(|value| value.to_string());
        let before_value = before.map(|valuation| valuation.value_usdg_raw);
        let after_value = after.map(|valuation| valuation.value_usdg_raw);

        // The self-check: the published value was produced by the reader
        // that read the base pool tables, so it has to equal the A side.
        match (published.as_deref(), before_value) {
            (Some(published), Some(before)) => {
                if published == before.to_string() {
                    self_check_matched += 1;
                } else {
                    self_check_mismatched += 1;
                }
            }
            (None, _) => loops_without_published_value += 1,
            (Some(_), None) => loops_without_published_value += 1,
        }

        let changed = match (before_value, after_value) {
            (Some(before), Some(after)) => before != after,
            _ => false,
        };
        if changed {
            loops_with_valuation_change += 1;
        }
        if before_value.is_some() && after_value.is_none() {
            loops_losing_valuation += 1;
        }

        // The gate sensitivity: the single-tick profit at which the USDG
        // profit reaches the review threshold.
        let break_even_before = break_even_profit_raw(
            entry.cross_tick_review_threshold_usdg_raw,
            before_value,
            entry.borrow_token_decimals,
        );
        let break_even_after = break_even_profit_raw(
            entry.cross_tick_review_threshold_usdg_raw,
            after_value,
            entry.borrow_token_decimals,
        );

        loop_rows.push(json!({
            "loopKey": entry.loop_key,
            "anchorTokenKeyId": entry.anchor_token_key_id,
            "publishedBorrowTokenUsdgValueRaw": published,
            "valueUsdgRawBefore": before_value.map(|value| value.to_string()),
            "valueUsdgRawAfter": after_value.map(|value| value.to_string()),
            "borrowTokenDecimals": entry.borrow_token_decimals,
            "crossTickReviewThresholdUsdgRaw": entry.cross_tick_review_threshold_usdg_raw.to_string(),
            "crossTickCapacityAnchorRaw": entry.cross_tick_capacity_anchor_raw.map(|value| value.to_string()),
            "breakEvenSingleTickProfitRawBefore": break_even_before.map(|value| value.to_string()),
            "breakEvenSingleTickProfitRawAfter": break_even_after.map(|value| value.to_string()),
        }));
    }

    let report = json!({
        "probe": "valuation_snapshot_ab_20261126",
        "task": "T-闭.02",
        "readOnly": true,
        "store": store.display().to_string(),
        "network": network,
        "chainId": chain_id,
        "usdgTokenKeyId": usdg_token_key_id,
        "poolUniverse": {
            "legacyStateCount": legacy.len(),
            "currentStateCount": current.len(),
            "droppedPoolCount": dropped.len(),
        },
        "coverage": {
            "legacyValuedTokens": legacy_valuation.len(),
            "currentValuedTokens": current_valuation.len(),
            "onlyInLegacy": only_legacy,
            "onlyInCurrent": only_current,
            "coverageShrank": !only_legacy.is_empty(),
        },
        "valuationChange": {
            "changedTokens": changed_tokens,
            "changedWinningPool": changed_pool,
            "maxDeltaPpm": max_ppm.to_string(),
            "maxDeltaPpmToken": max_ppm_token,
        },
        "selfCheck": {
            "publishedValueMatchedLegacy": self_check_matched,
            "publishedValueMismatchedLegacy": self_check_mismatched,
            "loopsWithoutPublishedValue": loops_without_published_value,
        },
        "gateImpact": {
            "publishedLoops": loops.len(),
            "loopsWithValuationChange": loops_with_valuation_change,
            "loopsLosingValuation": loops_losing_valuation,
        },
        "tokens": token_rows,
        "droppedPools": dropped_rows,
        "loops": loop_rows,
    });

    if let Some(parent) = report_path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    std::fs::write(&report_path, format!("{}\n", serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?))
        .map_err(|error| error.to_string())?;
    println!("{}", serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?);
    Ok(())
}
// --- the readers --------------------------------------------------------

fn table_exists(connection: &Connection, table: &str) -> Result<bool, String> {
    let count = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name = ?1",
            params![table],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|error| error.to_string())?;
    Ok(count > 0)
}

fn table_columns(connection: &Connection, table: &str) -> Result<BTreeSet<String>, String> {
    let mut statement = connection
        .prepare(&format!("PRAGMA table_info({table})"))
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|error| error.to_string())?;
    let mut columns = BTreeSet::new();
    for row in rows {
        columns.insert(row.map_err(|error| error.to_string())?);
    }
    Ok(columns)
}

/// Side **A**: the reader that shipped before the change.
///
/// Both halves come from the base pool tables.  The filters are the ones
/// of the pre-change `read_valuation_pool_states_from_table`, line for
/// line: a table that does not carry every required column contributes
/// nothing at all, and the first table that publishes a pool wins.
fn read_legacy_states(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, ValuationPoolState>, String> {
    let mut states: BTreeMap<String, ValuationPoolState> = BTreeMap::new();
    for table in BASE_POOL_TABLES {
        if !table_exists(connection, table)? {
            continue;
        }
        let columns = table_columns(connection, table)?;
        let mut usable = true;
        for required in [
            "sqrt_price_x96",
            "tick",
            "liquidity",
            "fee",
            "tick_spacing",
            "token0_key_id",
            "token1_key_id",
        ] {
            if !columns.contains(required) {
                usable = false;
                break;
            }
        }
        if !usable {
            continue;
        }
        let mut statement = connection
            .prepare(&format!(
                "SELECT pool_id, sqrt_price_x96, tick, liquidity, fee, tick_spacing,
                        token0_key_id, token1_key_id
                 FROM {table}
                 WHERE network = ?1 AND chain_id = ?2"
            ))
            .map_err(|error| error.to_string())?;
        let rows = statement
            .query_map(params![network, chain_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, Option<i64>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, Option<i64>>(4)?,
                    row.get::<_, Option<i64>>(5)?,
                    row.get::<_, Option<String>>(6)?,
                    row.get::<_, Option<String>>(7)?,
                ))
            })
            .map_err(|error| error.to_string())?;
        for row in rows {
            let (
                pool_id,
                sqrt_price_x96,
                tick,
                liquidity,
                fee,
                tick_spacing,
                token0_key_id,
                token1_key_id,
            ) = row.map_err(|error| error.to_string())?;
            let Some(sqrt_price_x96) = sqrt_price_x96
                .and_then(|text| text.trim().parse::<u128>().ok())
            else {
                continue;
            };
            if sqrt_price_x96 == 0 {
                continue;
            }
            let Some(liquidity) = liquidity.and_then(|text| text.trim().parse::<u128>().ok()) else {
                continue;
            };
            if liquidity <= 0 {
                continue;
            }
            if tick.is_none() {
                continue;
            }
            let Some(fee) = fee else { continue };
            if fee <= 0 {
                continue;
            }
            let Some(tick_spacing) = tick_spacing else { continue };
            if tick_spacing <= 0 {
                continue;
            }
            let Some(token0_key_id) = token0_key_id else { continue };
            let Some(token1_key_id) = token1_key_id else { continue };
            if token0_key_id.trim().is_empty() || token1_key_id.trim().is_empty() {
                continue;
            }
            states.entry(pool_id.clone()).or_insert(ValuationPoolState {
                sqrt_price_x96,
                token0_key_id,
                token1_key_id,
            });
        }
    }
    Ok(states)
}

/// Side **B**: the reader that ships now.
///
/// The static facts come from the base pool tables and the market half
/// from the liquidity snapshot table, exactly as the shipped
/// `read_valuation_pool_states` does.
fn read_current_states(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, ValuationPoolState>, String> {
    let mut facts: BTreeMap<String, (String, String)> = BTreeMap::new();
    for table in BASE_POOL_TABLES {
        if !table_exists(connection, table)? {
            continue;
        }
        let columns = table_columns(connection, table)?;
        let mut usable = true;
        for required in ["fee", "tick_spacing", "token0_key_id", "token1_key_id"] {
            if !columns.contains(required) {
                usable = false;
                break;
            }
        }
        if !usable {
            continue;
        }
        let mut statement = connection
            .prepare(&format!(
                "SELECT pool_id, fee, tick_spacing, token0_key_id, token1_key_id
                 FROM {table}
                 WHERE network = ?1 AND chain_id = ?2"
            ))
            .map_err(|error| error.to_string())?;
        let rows = statement
            .query_map(params![network, chain_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<i64>>(1)?,
                    row.get::<_, Option<i64>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, Option<String>>(4)?,
                ))
            })
            .map_err(|error| error.to_string())?;
        for row in rows {
            let (pool_id, fee, tick_spacing, token0_key_id, token1_key_id) =
                row.map_err(|error| error.to_string())?;
            let Some(fee) = fee else { continue };
            if fee <= 0 {
                continue;
            }
            let Some(tick_spacing) = tick_spacing else { continue };
            if tick_spacing <= 0 {
                continue;
            }
            let Some(token0_key_id) = token0_key_id else { continue };
            let Some(token1_key_id) = token1_key_id else { continue };
            if token0_key_id.trim().is_empty() || token1_key_id.trim().is_empty() {
                continue;
            }
            facts
                .entry(pool_id)
                .or_insert((token0_key_id, token1_key_id));
        }
    }

    let mut market: BTreeMap<String, u128> = BTreeMap::new();
    if table_exists(connection, SNAPSHOT_TABLE)? {
        let columns = table_columns(connection, SNAPSHOT_TABLE)?;
        let mut usable = true;
        for required in ["sqrt_price_x96", "tick", "liquidity"] {
            if !columns.contains(required) {
                usable = false;
                break;
            }
        }
        if usable {
            let mut statement = connection
                .prepare(&format!(
                    "SELECT pool_id, sqrt_price_x96, tick, liquidity
                     FROM {SNAPSHOT_TABLE}
                     WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
                ))
                .map_err(|error| error.to_string())?;
            let rows = statement
                .query_map(params![network, chain_id, NETWORK_PROTOCOL], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, Option<String>>(1)?,
                        row.get::<_, Option<i64>>(2)?,
                        row.get::<_, Option<String>>(3)?,
                    ))
                })
                .map_err(|error| error.to_string())?;
            for row in rows {
                let (pool_id, sqrt_price_x96, tick, liquidity) =
                    row.map_err(|error| error.to_string())?;
                let Some(sqrt_price_x96) = sqrt_price_x96
                    .and_then(|text| text.trim().parse::<u128>().ok())
                else {
                    continue;
                };
                if sqrt_price_x96 == 0 {
                    continue;
                }
                let Some(liquidity) = liquidity.and_then(|text| text.trim().parse::<u128>().ok())
                else {
                    continue;
                };
                if liquidity <= 0 {
                    continue;
                }
                if tick.is_none() {
                    continue;
                }
                market.entry(pool_id).or_insert(sqrt_price_x96);
            }
        }
    }

    let mut states: BTreeMap<String, ValuationPoolState> = BTreeMap::new();
    for (pool_id, (token0_key_id, token1_key_id)) in facts {
        let Some(sqrt_price_x96) = market.get(&pool_id).copied() else {
            continue;
        };
        states.insert(
            pool_id,
            ValuationPoolState {
                sqrt_price_x96,
                token0_key_id,
                token1_key_id,
            },
        );
    }
    Ok(states)
}
// --- the catalog --------------------------------------------------------

fn read_token_decimals(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, u32>, String> {
    let mut decimals = BTreeMap::new();
    if !table_exists(connection, "token_library_catalog")? {
        return Ok(decimals);
    }
    let mut statement = connection
        .prepare(
            "SELECT token_key_id, token_decimals
             FROM token_library_catalog
             WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map(params![network, chain_id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<i64>>(1)?))
        })
        .map_err(|error| error.to_string())?;
    for row in rows {
        let (token_key_id, token_decimals) = row.map_err(|error| error.to_string())?;
        let Some(token_decimals) = token_decimals else {
            continue;
        };
        if !(0..=255).contains(&token_decimals) {
            continue;
        }
        decimals.insert(token_key_id, token_decimals as u32);
    }
    Ok(decimals)
}
fn read_usdg_token_key_id(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Option<String>, String> {
    if !table_exists(connection, "token_library_catalog")? {
        return Ok(None);
    }
    let key_id = connection
        .query_row(
            "SELECT token_key_id FROM token_library_catalog
             WHERE network = ?1 AND chain_id = ?2 AND token_symbol = ?3
               AND admission_status = 'admitted'
             ORDER BY CASE library_key
                 WHEN 'core' THEN 0
                 WHEN 'stocks' THEN 1
                 WHEN 'test' THEN 2
                 ELSE 3
             END
             LIMIT 1",
            params![network, chain_id, USDG_SYMBOL],
            |row| row.get::<_, String>(0),
        )
        .ok();
    Ok(key_id.filter(|value| !value.trim().is_empty()))
}
/// Value every token that has a USDG route, with the **shipped** function.
///
/// The quote asset itself is its own unit, which the shipped caller
/// handles before it calls the valuation; the probe does the same.
fn value_all(
    states: &BTreeMap<String, ValuationPoolState>,
    usdg_token_key_id: &str,
    decimals: &BTreeMap<String, u32>,
    usdg_decimals: u32,
) -> BTreeMap<String, closed_loop_manager::usdg_valuation::UsdgValuation> {
    let mut valuations = value_tokens_in_usdg(states, usdg_token_key_id, decimals, usdg_decimals);
    valuations.insert(
        usdg_token_key_id.to_owned(),
        closed_loop_manager::usdg_valuation::UsdgValuation {
            token_key_id: usdg_token_key_id.to_owned(),
            value_usdg_raw: 10u128.saturating_pow(usdg_decimals),
            pool_id: "stablecoin_unit_identity".to_owned(),
            usdg_token_key_id: usdg_token_key_id.to_owned(),
            token_key_id_in_pool: usdg_token_key_id.to_owned(),
        },
    );
    valuations
}
// --- the published loops ------------------------------------------------

struct PublishedLoop {
    loop_key: String,
    anchor_token_key_id: String,
    borrow_token_usdg_value_raw: Option<u128>,
    borrow_token_decimals: Option<u32>,
    cross_tick_review_threshold_usdg_raw: u128,
    cross_tick_capacity_anchor_raw: Option<u128>,
}

fn read_published_loops(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<PublishedLoop>, String> {
    let mut loops = Vec::new();
    if !table_exists(connection, "manager_v4_closed_loops")? {
        return Ok(loops);
    }
    let mut statement = connection
        .prepare(
            "SELECT loop_key, anchor_token_key_id, borrow_token_usdg_value_raw,
                    borrow_token_decimals, cross_tick_review_threshold_usdg_raw,
                    cross_tick_capacity_anchor_raw
             FROM manager_v4_closed_loops
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
             ORDER BY loop_key",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map(params![network, chain_id, NETWORK_PROTOCOL], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<i64>>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, Option<String>>(5)?,
            ))
        })
        .map_err(|error| error.to_string())?;
    for row in rows {
        let row = row.map_err(|error| error.to_string())?;
        loops.push(PublishedLoop {
            loop_key: row.0,
            anchor_token_key_id: row.1,
            borrow_token_usdg_value_raw: row.2
                .and_then(|text| text.trim().parse::<u128>().ok()),
            borrow_token_decimals: row.3.map(|value| value as u32),
            cross_tick_review_threshold_usdg_raw: row.4
                .and_then(|text| text.trim().parse::<u128>().ok())
                .unwrap_or(0),
            cross_tick_capacity_anchor_raw: row.5
                .and_then(|text| text.trim().parse::<u128>().ok()),
        });
    }
    Ok(loops)
}
/// The single-tick profit at which the USDG profit reaches the threshold.
///
/// The gate compares `profit_raw * value / 10^decimals` with the
/// threshold, so the break-even profit is
/// `threshold * 10^decimals / value`.  It is the sensitivity of the gate:
/// a valuation that moves shifts this line, and a decision flips when the
/// measured profit sits between the two.
fn break_even_profit_raw(
    threshold_usdg_raw: u128,
    value_usdg_raw: Option<u128>,
    decimals: Option<u32>,
) -> Option<u128> {
    let value = value_usdg_raw?;
    let decimals = decimals?;
    if value == 0 || threshold_usdg_raw == 0 {
        return None;
    }
    let scale = 10u128.checked_pow(decimals)?;
    Some(threshold_usdg_raw.checked_mul(scale)?.div_ceil(value))
}

// --- the plumbing -------------------------------------------------------

fn default_store() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
        .join("flashloan")
        .join("db")
        .join("local_store_4663.sqlite3")
}

fn default_report_path() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
        .join("plan")
        .join("runtime_evidence")
        .join("valuation_snapshot_ab_20261126.json")
}

fn open_read_only(store: &Path) -> Result<Connection, String> {
    let connection = Connection::open_with_flags(
        store,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| error.to_string())?;
    connection
        .execute_batch("PRAGMA query_only = ON;")
        .map_err(|error| error.to_string())?;
    Ok(connection)
}
