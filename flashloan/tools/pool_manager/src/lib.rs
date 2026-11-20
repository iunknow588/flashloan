use rusqlite::{params_from_iter, Connection};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub mod baseline_audit;
pub mod chain;
pub mod depth;
pub mod depth_config;
pub mod parity_state;
pub mod pool_roles;
pub mod scope;
pub mod single_tick_capacity;
pub mod state_transition;
pub mod tick_swap;
pub mod usdg_depth;

pub use baseline_audit::{audit_mainnet_baseline, BaselineAuditError, MainnetBaselineAudit};
pub use chain::{
    pool_state_batch_to_json, read_v4_pool_states, read_v4_pool_states_with_fallback,
    redact_rpc_text, rpc_urls, rpc_urls_for, rpc_urls_from_chain_config,
    rpc_urls_from_chain_config_for, rpc_urls_from_env, v4_pool_state_slots, V4PoolState,
    V4PoolStateBatch, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_V4_POOL_MANAGER,
};
pub use depth::{
    capacity_from_sqrt_price, capacity_json, evaluate_capacity, exact_input_swap_amount_out,
    price_impact_for_input, raw_to_decimal_string, swap_amount_out, Capacity, DEPTH_BASIS,
    PRICE_IMPACT_LIMIT_BPS,
};
pub use depth_config::{
    assert_depth_policy_matches_file, depth_policy, depth_policy_path, DepthPolicy,
    DEPTH_BAND_BASIS, DEPTH_BAND_DEEP_CAPACITY, DEPTH_BAND_MEDIUM_CAPACITY,
    DEPTH_BAND_SHALLOW_CAPACITY, MAX_CANDIDATE_FIXED_FEE, MIN_CANDIDATE_BINDING_CAPACITY,
    MIN_CANDIDATE_CAPACITY_RAW,
};
pub use parity_state::{
    read_live_pool_parity_states, read_newest_liquidity_read_at, read_pool_parity_states,
    read_pool_parity_states_on_connection, resolve_pool_ids_by_key,
    resolve_pool_ids_by_key_on_connection, PoolKeyIdentity, PoolParityState, PoolStateReadError,
    POOL_PROTOCOL,
};
pub use pool_roles::{
    cleanup_pool_roles, cleanup_window, ensure_pool_role_schema, materialize_pool_roles,
    monitor_swap_activity_wss, open_role_store, read_block_timestamps, read_trade_activity,
    role_cleanup_report, run_pool_role_maintenance, scan_swap_activity, swap_scan_report,
    v4_swap_topic, write_trade_activity, wss_monitor_report, PoolRole, PoolRoleCleanupReport,
    PoolRoleDecision, PoolRoleOptions, SwapActivity, SwapScanOptions, SwapScanReport,
    WssMonitorOptions, WssMonitorReport, BACKUP_RETENTION_DAYS, CLEANUP_HOUR,
    CLEANUP_INTERVAL_DAYS, POOL_ROLE_TABLE, TRADE_ACTIVITY_TABLE, V4_SWAP_TOPIC,
};
pub use scope::{
    materialize_scope, materialize_scope_write, read_scope_status, ScopeError,
    ScopeMaterializationResult, ScopeStatus, ScopeWriteOutcome, ScopeWriteRequest,
};
pub use state_transition::{
    evaluate_state_transition, CapacityBasis, SingleTickExecutionState, StateTransitionKind,
    StateTransitionRequest, StateTransitionVerdict, DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS,
};
pub use usdg_depth::{
    capacity_to_usdg_depth, ensure_usdg_depth_schema, is_depth_bearing_token, read_usdg_valuations,
    read_usdg_valuations_from_db, valuation_index, write_usdg_depth_observations,
    UsdgDepthObservation, UsdgValuation, MAX_USDG_DEPTH_RAW, ZERO_TOKEN_ADDRESS,
};

const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";
/// Depth band boundaries expressed as a price-impact capacity ratio.
///
/// The depth verdict is a chain property: the amount a pool can absorb within
/// the price-impact limit, divided by the size of the position it must carry.
/// USD-denominated bands were removed because an external ETH/USD or equity
/// quote does not express tradable value inside the arbitrage environment.
/// The boundaries below are the capacity bands that the Python pool index
/// (`v4_pool_index.py::_liquidity_depth_status`) classifies with the same
/// `>=` operator.
/// Reference-only USD thresholds.
///
/// These values are **not** an admission gate any more. They remain purely
/// as a human-facing sanity band for the USD valuation that is still
/// recorded for observation and tie-breaking. A pool is admitted or rejected
/// on its on-chain capacity alone.
pub const MIN_CANDIDATE_TVL_USD_REFERENCE: f64 = 100_000.0;
pub const MAX_CANDIDATE_TVL_USD_REFERENCE: f64 = 1_000_000_000.0;

fn depth_band_rank(depth_status: Option<&str>) -> Option<u8> {
    match lowered(depth_status).as_str() {
        "deep" => Some(4),
        "liquid" => Some(3),
        "medium" => Some(2),
        "shallow" | "thin" => Some(1),
        "dust" => Some(0),
        _ => None,
    }
}

/// Whether the store carries the capacity evidence at all.
///
/// A chain whose schema predates the capacity columns never measured a
/// capacity, so the absence of the column is a coverage gap of the store
/// and not a missing measurement of a pool. The distinction matters: a
/// store **with** the column and a NULL value is a pool whose measurement
/// failed, while a store **without** the column cannot carry a verdict at
/// all. Treating the latter as a missing measurement disqualifies every
/// pool of that chain.
fn store_carries_capacity_evidence(present: &[&str]) -> bool {
    present.contains(&"capacity_raw")
}

fn depth_status_reasons(
    depth_status: Option<&str>,
    _estimated_tvl_usd: Option<f64>,
    capacity_raw: Option<f64>,
    capacity_evidence_available: bool,
) -> Vec<String> {
    let mut reasons = Vec::new();
    let label = lowered(depth_status);
    let capacity = capacity_raw.filter(|value| value.is_finite() && *value > 0.0);
    match label.as_str() {
        "deep" | "liquid" | "medium" => {}
        "thin" | "shallow" => reasons.push("depth_thin".to_string()),
        "dust" => reasons.push("depth_dust".to_string()),
        "unpriced" => reasons.push("depth_unpriced".to_string()),
        "no_liquidity" => reasons.push("depth_no_liquidity".to_string()),
        "" => reasons.push("depth_unknown".to_string()),
        _ => reasons.push(format!("depth_unrecognized_{label}")),
    }
    // The capacity is the admission signal. A pool without a measurable
    // capacity cannot carry a position, whatever its USD valuation says.
    // A store that does not carry the capacity column at all never measured
    // a capacity, so the absence of the measurement is not a defect of the
    // pool and must not disqualify it.
    if capacity.is_none() && capacity_evidence_available {
        reasons.push("missing_capacity_evidence".to_string());
    }
    reasons
}

/// The **reference-only** observations of a pool.
///
/// The USD valuation is not an admission gate: it is denominated in the
/// chain's own quote asset, so a band expressed in USD rejects every pool of
/// a chain whose quote is not the USD. The shared depth policy lists
/// ``depth_admission`` and ``capacity_admission`` under
/// ``usdgPegPolicy.forbiddenUses`` for exactly that reason, and the operating
/// manual forbids dropping a token pair on a TVL conclusion.
///
/// These observations therefore stay **out** of the disqualifying reasons.
/// They used to be returned by ``depth_status_reasons`` together with the
/// real defects, and because every non-empty reason list marks a pool as
/// ineligible, a pool whose USD reading was merely below the reference band
/// was dropped from the candidate set even though its on-chain capacity was
/// measured and adequate. The cleanup then removed the pool, the closed
/// loops that named it lost a hop, and the chain churned its scope on every
/// repair round without ever fixing the real defect.
///
/// The observations are still surfaced: the audit reports them next to the
/// disqualifying reasons, so a malformed reading is visible to an operator
/// without ever becoming a gate.
fn reference_only_observations(
    estimated_tvl_usd: Option<f64>,
) -> Vec<String> {
    let mut observations = Vec::new();
    let tvl = estimated_tvl_usd.filter(|value| value.is_finite() && *value > 0.0);
    if let Some(value) = tvl {
        if value < MIN_CANDIDATE_TVL_USD_REFERENCE {
            observations.push("tvl_below_reference_band".to_string());
        }
        if value > MAX_CANDIDATE_TVL_USD_REFERENCE {
            observations.push("tvl_out_of_range_bad_reading".to_string());
        }
    }
    observations
}

/// Returns the decimals-normalised binding-side capacity of a pool.
///
/// A round-trip arbitrage sends a position through a pool and back, so both
/// sides must absorb it. The binding side is the smaller of the two
/// one-sided capacities after each side is converted into its own token
/// units, because a raw unit is only comparable to another raw unit when
/// both tokens share the same number of decimals.
///
/// `None` means the value cannot be decided, which is a coverage gap rather
/// than a statement about the market.
pub fn binding_side_capacity(
    capacity_token0_raw: Option<u128>,
    capacity_token1_raw: Option<u128>,
    token0_decimals: Option<i64>,
    token1_decimals: Option<i64>,
) -> Option<f64> {
    let capacity0 = capacity_token0_raw?;
    let capacity1 = capacity_token1_raw?;
    let decimals0 = token0_decimals?;
    let decimals1 = token1_decimals?;
    if decimals0 < 0 || decimals1 < 0 {
        return None;
    }
    let normalised0 = capacity0 as f64 / 10f64.powi(decimals0 as i32);
    let normalised1 = capacity1 as f64 / 10f64.powi(decimals1 as i32);
    if !normalised0.is_finite() || !normalised1.is_finite() {
        return None;
    }
    Some(normalised0.min(normalised1))
}

/// Classifies a decimals-normalised binding-side capacity into a band.
///
/// The boundary operator is `>=`, matching the Python side.
pub fn depth_band_for_binding_capacity(binding_capacity: Option<f64>) -> &'static str {
    match binding_capacity {
        Some(value) if value >= DEPTH_BAND_DEEP_CAPACITY => "deep",
        Some(value) if value >= DEPTH_BAND_MEDIUM_CAPACITY => "medium",
        Some(value) if value >= DEPTH_BAND_SHALLOW_CAPACITY => "shallow",
        Some(_) => "dust",
        None => "unavailable",
    }
}

/// Builds the depth-band document.
///
/// `binding_capacity` is the decimals-normalised binding-side capacity that
/// actually decides the band. When it is unavailable the band cannot be
/// decided on the shared basis, so the document reports `observed` and says
/// which basis applies.
#[allow(dead_code)]
fn depth_band_json_for_binding(
    depth_status: &str,
    estimated_tvl_usd: Option<f64>,
    capacity_raw: Option<f64>,
    binding_capacity: Option<f64>,
) -> String {
    depth_band_json_for_binding_with_impact(
        depth_status,
        estimated_tvl_usd,
        capacity_raw,
        binding_capacity,
        None,
    )
}

/// Builds the depth-band document, including the realised price impact.
///
/// ARCH-1a: `priceImpactBps` is the secondary recommendation sort key. It
/// travels with the document so a consumer that only sees the candidate table
/// can reconstruct the full ordering without reading the snapshot table.
fn depth_band_json_for_binding_with_impact(
    depth_status: &str,
    estimated_tvl_usd: Option<f64>,
    capacity_raw: Option<f64>,
    binding_capacity: Option<f64>,
    price_impact_bps: Option<f64>,
) -> String {
    let binding = binding_capacity.filter(|value| value.is_finite() && *value >= 0.0);
    let level = match binding {
        Some(value) => depth_band_for_binding_capacity(Some(value)),
        None => {
            if depth_status.trim().is_empty() {
                "unavailable"
            } else {
                "observed"
            }
        }
    };
    json!({
        "level": level,
        "label": level,
        "capacityRaw": capacity_raw,
        "capacityBindingRaw": binding_capacity,
        "priceImpactBps": price_impact_bps,
        "depthBandBasis": DEPTH_BAND_BASIS,
        "estimatedTvlUsd": estimated_tvl_usd,
        "depthBasis": DEPTH_BASIS,
    })
    .to_string()
}

/// Builds the depth-band document with the shared admission verdict.
///
/// The verdict is owned by the Rust toolchain and read from the shared depth
/// policy. It travels on the document so every consumer of a physical pool
/// reads one decision from one place, including the chain scoped liquidity
/// snapshot that no anchor scoped surface can replace.
fn depth_band_json_with_admission(
    depth_status: &str,
    estimated_tvl_usd: Option<f64>,
    capacity_raw: Option<f64>,
    binding_capacity: Option<f64>,
    price_impact_bps: Option<f64>,
    has_liquidity: Option<bool>,
) -> String {
    let mut document: serde_json::Map<String, serde_json::Value> = match serde_json::from_str(
        &depth_band_json_for_binding_with_impact(
            depth_status,
            estimated_tvl_usd,
            capacity_raw,
            binding_capacity,
            price_impact_bps,
        ),
    ) {
        Ok(serde_json::Value::Object(map)) => map,
        _ => serde_json::Map::new(),
    };
    let verdict = depth_config::admit_candidate_pool_with_liquidity(
        Some(depth_status),
        binding_capacity,
        has_liquidity,
    );
    document.insert(
        "kernelAdmitted".to_string(),
        serde_json::Value::Bool(verdict.admitted),
    );
    document.insert(
        "kernelAdmissionReason".to_string(),
        serde_json::Value::String(verdict.reason),
    );
    if let Some(band) = verdict.depth_band {
        document.insert(
            "kernelDepthBand".to_string(),
            serde_json::Value::String(band),
        );
    }
    if let Some(capacity) = verdict.capacity_binding_raw {
        document.insert(
            "kernelCapacityBindingRaw".to_string(),
            serde_json::Number::from_f64(capacity)
                .map(serde_json::Value::Number)
                .unwrap_or(serde_json::Value::Null),
        );
    }
    serde_json::Value::Object(document).to_string()
}

/// Reads the token decimals of every candidate pool from `chain_tokens`.
///
/// The decimals are required to turn a raw capacity into a comparable
/// token amount: a raw unit of a 6-decimal stablecoin and a raw unit of an
/// 18-decimal token are not the same quantity, so the depth bands cannot be
/// applied to raw amounts.
///
/// A pool whose decimals cannot be resolved keeps an unmeasured binding
/// capacity. That is a coverage gap, not a shallow pool, so it is never
/// turned into a rejection.
fn read_token_decimals(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, i64>, String> {
    let table_exists: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='chain_tokens'",
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);
    if table_exists == 0 {
        return Ok(BTreeMap::new());
    }
    let mut statement = match connection.prepare(
        "SELECT token_address, token_decimals FROM chain_tokens \
         WHERE network = ?1 AND chain_id = ?2 AND token_decimals IS NOT NULL",
    ) {
        Ok(statement) => statement,
        Err(_) => return Ok(BTreeMap::new()),
    };
    let rows = statement.query_map(rusqlite::params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    });
    let mut decimals = BTreeMap::new();
    if let Ok(rows) = rows {
        for row in rows.flatten() {
            decimals.insert(row.0.trim().to_lowercase(), row.1);
        }
    }
    Ok(decimals)
}

/// Reads the token addresses of every pool the chain knows.
///
/// The pool manager needs the token pair of a pool to attach the decimals
/// to the two sides of its capacity.
///
/// The **chain scoped** exchange pool table is read as well, and it is read
/// **first**. The candidate tables are derived views: they only carry the
/// pools that some anchor happens to recommend. A pool that only exists in
/// the chain scoped table therefore had no token pair at all, so no decimals
/// could be attached to either side of its capacity, so the binding side
/// capacity stayed unmeasured and the admission verdict became
/// `unavailable`.
///
/// That is a **core** defect and not a property of any chain: the depth
/// reader measured a physical pool and then refused to price it, because the
/// pool it measured was not reachable through the anchor scoped surface. The
/// fix belongs here, in the chain agnostic reader, and not in the policy and
/// not in a per chain special case.
/// Whether a live pool state may replace the stored measurement.
///
/// A live read is only a measurement when **both** halves of the pool were
/// read: its `slot0` and its liquidity. The reader fills the missing half
/// of a failed read with the default zero, so an incomplete state looks
/// exactly like a real, empty pool. Writing that state would destroy a
/// good stored measurement and would let the shared admission verdict
/// reject a pool that is in fact full.
///
/// The rule is chain agnostic: a node that rate limits one chain can rate
/// limit every chain, so the protection belongs to the reader and not to a
/// per chain policy.
fn depth_state_is_writable(state: &chain::V4PoolState) -> bool {
    state.complete
}
fn read_pool_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, (String, String)>, String> {
    let mut tokens = BTreeMap::new();
    for table in [
        "v4_exchange_pools",
        "manager_dex_candidate_pools",
        "dex_candidate_pools",
    ] {
        let sql = format!(
            "SELECT pool_id, token0, token1 FROM {table} WHERE network = ?1 AND chain_id = ?2"
        );
        let Ok(mut statement) = connection.prepare(&sql) else {
            continue;
        };
        let Ok(rows) = statement.query_map(rusqlite::params![network, chain_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<String>>(2)?,
            ))
        }) else {
            continue;
        };
        for row in rows.flatten() {
            let (pool_id, token0, token1) = row;
            let key = pool_id.trim().to_lowercase();
            let entry = (
                token0.unwrap_or_default().trim().to_lowercase(),
                token1.unwrap_or_default().trim().to_lowercase(),
            );
            tokens.entry(key).or_insert(entry);
        }
    }
    Ok(tokens)
}

/// Reads the tick spacing of every pool.
///
/// The tick spacing is the granularity at which a pool may change its
/// liquidity, so it is the granularity of the tick range the first
/// arbitrage version constrains a hop to. A pool without a spacing is
/// absent from the map, and the caller then skips the tick bound for it
/// rather than guessing one.
fn read_pool_tick_spacings(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> BTreeMap<String, i32> {
    let mut spacings = BTreeMap::new();
    for table in [
        "v4_exchange_pools",
        "manager_dex_candidate_pools",
        "dex_candidate_pools",
    ] {
        let sql = format!(
            "SELECT pool_id, tick_spacing FROM {table} WHERE network = ?1 AND chain_id = ?2"
        );
        let Ok(mut statement) = connection.prepare(&sql) else {
            continue;
        };
        let Ok(rows) = statement.query_map(rusqlite::params![network, chain_id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<i64>>(1)?))
        }) else {
            continue;
        };
        for row in rows.flatten() {
            let (pool_id, spacing) = row;
            let Some(spacing) = spacing else { continue };
            if spacing <= 0 {
                continue;
            }
            spacings
                .entry(pool_id.trim().to_lowercase())
                .or_insert(spacing as i32);
        }
    }
    spacings
}

/// Resolves the token decimals of both sides of a pool.
///
/// The pool id is matched case-insensitively against the candidate pool
/// tables, and each token address is then resolved against `chain_tokens`.
fn pool_decimals(
    token_decimals: &BTreeMap<String, i64>,
    pool_tokens: &BTreeMap<String, (String, String)>,
    pool_id: &str,
) -> (Option<i64>, Option<i64>) {
    let key = pool_id.trim().to_lowercase();
    let Some((token0, token1)) = pool_tokens.get(&key) else {
        return (None, None);
    };
    (
        token_decimals.get(token0).copied(),
        token_decimals.get(token1).copied(),
    )
}

/// The network namespace of a chain id.
///
/// The mapping lives in the chain module, so the pool library, the depth
/// reader and the RPC endpoint resolver can never drift apart.
pub use chain::network_for_chain_id;

fn canonical_pair_key(base: &str, quote: &str) -> String {
    let base = base.trim().to_lowercase();
    let quote = quote.trim().to_lowercase();
    if base <= quote {
        format!("{base}:{quote}")
    } else {
        format!("{quote}:{base}")
    }
}

fn pair_key_variants(base: &str, quote: &str) -> Vec<String> {
    let canonical = canonical_pair_key(base, quote);
    let reverse = canonical
        .split_once(':')
        .map(|(left, right)| format!("{right}:{left}"))
        .unwrap_or_else(|| canonical.clone());
    vec![canonical, reverse]
}

fn lowered(value: Option<&str>) -> String {
    value.unwrap_or("").trim().to_lowercase()
}

fn is_valid_token_address(value: &str) -> bool {
    let text = value.trim().to_lowercase();
    if !text.starts_with("0x") {
        return false;
    }
    if let Ok(number) = i128::from_str_radix(text.trim_start_matches("0x"), 16) {
        if number == 0 {
            return true;
        }
    }
    text.len() == 42
}

fn is_zero_hook(value: Option<&str>) -> bool {
    let text = lowered(value);
    text.is_empty() || text == ZERO_ADDRESS
}

struct PoolQuality<'a> {
    fee: i64,
    dynamic_fee: i64,
    special_fee: i64,
    hooks: Option<&'a str>,
    token0: Option<&'a str>,
    token1: Option<&'a str>,
    has_liquidity: i64,
    liquidity_index: Option<f64>,
    depth_status: Option<&'a str>,
    estimated_tvl_usd: Option<f64>,
    capacity_raw: Option<f64>,
    /// Whether the store carries the capacity evidence at all.
    capacity_evidence_available: bool,
}

fn quality_reasons(pool: PoolQuality<'_>) -> Vec<String> {
    let PoolQuality {
        fee,
        dynamic_fee,
        special_fee,
        hooks,
        token0,
        token1,
        has_liquidity,
        liquidity_index,
        depth_status,
        estimated_tvl_usd,
        capacity_raw,
        capacity_evidence_available,
    } = pool;
    let mut reasons = Vec::new();
    if fee <= 0 {
        reasons.push("fee_non_positive".to_string());
    }
    if fee > MAX_CANDIDATE_FIXED_FEE {
        reasons.push("fee_too_high".to_string());
    }
    if depth::is_dynamic_fee_pool(fee, dynamic_fee) {
        reasons.push("dynamic_fee".to_string());
    }
    if special_fee != 0 {
        reasons.push("special_fee".to_string());
    }
    if !is_zero_hook(hooks) {
        reasons.push("non_zero_hook".to_string());
    }
    let token0 = token0.unwrap_or("");
    let token1 = token1.unwrap_or("");
    if !is_valid_token_address(token0) {
        reasons.push("invalid_token0".to_string());
    }
    if !is_valid_token_address(token1) {
        reasons.push("invalid_token1".to_string());
    }
    if token0.trim().to_lowercase() == token1.trim().to_lowercase() && !token0.trim().is_empty() {
        reasons.push("same_token_addresses".to_string());
    }
    if has_liquidity == 0 {
        reasons.push("missing_liquidity".to_string());
    }
    if liquidity_index.unwrap_or(0.0) <= 0.0 {
        reasons.push("missing_positive_liquidity".to_string());
    }
    reasons.extend(depth_status_reasons(
        depth_status,
        estimated_tvl_usd,
        capacity_raw,
        capacity_evidence_available,
    ));
    reasons
}



fn chunks<T: Clone>(values: &[T], size: usize) -> Vec<Vec<T>> {
    values.chunks(size).map(|chunk| chunk.to_vec()).collect()
}

fn params_for_scoped_values(network: &str, chain_id: i64, values: &[String]) -> Vec<String> {
    let mut params = vec![network.to_string(), chain_id.to_string()];
    params.extend(values.iter().cloned());
    params
}

fn affected_loop_keys_for_pool_ids(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> Result<Vec<String>, String> {
    let mut affected_loop_keys = BTreeSet::new();
    for chunk in chunks(pool_ids, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "SELECT DISTINCT loop_key FROM v4_closed_loop_hops
             WHERE network = ? AND chain_id = ? AND pool_id IN ({placeholders})"
        );
        let params = params_for_scoped_values(network, chain_id, &chunk);
        let mut hop_stmt = connection
            .prepare(&sql)
            .map_err(|error| error.to_string())?;
        let hop_rows = hop_stmt
            .query_map(params_from_iter(params.iter()), |row| {
                row.get::<_, String>(0)
            })
            .map_err(|error| error.to_string())?;
        for loop_key in hop_rows.flatten() {
            affected_loop_keys.insert(loop_key);
        }

        let json_placeholders = chunk
            .iter()
            .map(|_| "pool_ids_json LIKE ?")
            .collect::<Vec<_>>()
            .join(" OR ");
        let json_sql = format!(
            "SELECT DISTINCT loop_key FROM v4_closed_loops
             WHERE network = ? AND chain_id = ? AND ({json_placeholders})"
        );
        let mut params = vec![network.to_string(), chain_id.to_string()];
        params.extend(chunk.iter().map(|pool_id| format!("%{pool_id}%")));
        let mut json_stmt = connection
            .prepare(&json_sql)
            .map_err(|error| error.to_string())?;
        let json_rows = json_stmt
            .query_map(params_from_iter(params.iter()), |row| {
                row.get::<_, String>(0)
            })
            .map_err(|error| error.to_string())?;
        for loop_key in json_rows.flatten() {
            affected_loop_keys.insert(loop_key);
        }
    }
    let mut keys: Vec<String> = affected_loop_keys.into_iter().collect();
    keys.sort();
    Ok(keys)
}

/// The optional capacity columns of the liquidity snapshot table.
///
/// Every chain owns its own SQLite file, and the files are not guaranteed
/// to carry the same schema version: the mainnet store predates the
/// capacity columns while the testnet store already has them. A query that
/// selects one of these columns unconditionally therefore fails on the
/// chain that does not have it, and the audit reports a schema error instead
/// of the pool verdict. The audit therefore asks the schema which columns
/// exist and selects only those.
fn optional_snapshot_columns(connection: &Connection) -> Result<Vec<&'static str>, String> {
    let mut statement = connection
        .prepare("PRAGMA table_info(v4_exchange_pool_liquidity_snapshots)")
        .map_err(|error| error.to_string())?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|error| error.to_string())?;
    let mut present: Vec<&'static str> = Vec::new();
    for column in columns {
        let column = column.map_err(|error| error.to_string())?;
        match column.as_str() {
            "capacity_raw" => present.push("capacity_raw"),
            "capacity_binding_raw" => present.push("capacity_binding_raw"),
            "depth_score_usd" => present.push("depth_score_usd"),
            "depth_status" => present.push("depth_status"),
            _ => {}
        }
    }
    Ok(present)
}

/// Whether the snapshot table carries the given column.
///
/// The snapshot writer has to stay usable on a store that predates a column:
/// SQLite never alters an existing table, so a store created before the
/// depth-evidence columns existed keeps its old shape. A write that names a
/// missing column fails outright, so every optional assignment is resolved
/// against the schema the store actually has.
fn snapshot_has_column(connection: &Connection, column: &str) -> Result<bool, String> {
    let mut statement = connection
        .prepare("PRAGMA table_info(v4_exchange_pool_liquidity_snapshots)")
        .map_err(|error| error.to_string())?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|error| error.to_string())?;
    for value in columns {
        let value = value.map_err(|error| error.to_string())?;
        if value == column {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Render a select expression for an optional column: the column when it
/// exists, a typed NULL when it does not.
fn optional_column_expression(column: &str, present: &[&str]) -> String {
    if present.contains(&column) {
        format!("s.{column}")
    } else {
        format!("NULL AS {column}")
    }
}

fn audit_internal(connection: &Connection, chain_id: i64) -> Result<Value, String> {
    let network = network_for_chain_id(chain_id);
    let present = optional_snapshot_columns(connection)?;
    let capacity_expression = optional_column_expression("capacity_raw", &present);
    // The admission verdict has to be the **measured** one. The candidate
    // row carries a copy of the depth verdict that is only refreshed when
    // the pool is re-measured, so a pool that was re-measured out of the
    // adequate band kept its old adequate verdict on the candidate row
    // until the next refresh propagated it. The audit read that stale copy
    // and therefore admitted a pool whose measured depth was `thin`.
    //
    // The snapshot row is the measured surface, so its verdict wins whenever
    // it carries one. The candidate row stays the fallback for a store whose
    // snapshot never carried a verdict: an absent measurement is a coverage
    // gap, not a reason to disqualify the pool.
    let snapshot_depth_status_expression = if present.contains(&"depth_status") {
        "COALESCE(NULLIF(TRIM(s.depth_status), ''), d.depth_status)".to_string()
    } else {
        "d.depth_status".to_string()
    };
    let sql = format!(
        r#"
        SELECT d.pool_id, d.fee, d.dynamic_fee, d.special_fee, d.hooks,
               d.token0, d.token1, {snapshot_depth_status_expression} AS depth_status,
               d.has_liquidity,
               d.liquidity_index, d.usable_for_observation,
               s.estimated_tvl_usd,
               s.evidence_source,
               {capacity_expression}
        FROM dex_candidate_pools d
        LEFT JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = d.network AND s.chain_id = d.chain_id
         AND s.protocol = d.protocol AND s.pool_id = d.pool_id
        WHERE d.network = ? AND d.chain_id = ?
        ORDER BY d.pool_id
    "#
    );
    let mut stmt = connection.prepare(&sql).map_err(|error| error.to_string())?;

    let rows = stmt
        .query_map(
            params_from_iter([network.as_str(), &chain_id.to_string()]),
            |row| {
                let pool_id: String = row.get(0)?;
                let fee: i64 = row.get(1)?;
                let dynamic_fee: i64 = row.get(2)?;
                let special_fee: i64 = row.get(3)?;
                let hooks: Option<String> = row.get(4)?;
                let token0: Option<String> = row.get(5)?;
                let token1: Option<String> = row.get(6)?;
                let depth_status: Option<String> = row.get(7)?;
                let has_liquidity: i64 = row.get(8)?;
                let liquidity_index: Option<f64> = row.get(9)?;
                let usable_for_observation: Option<i64> = row.get(10)?;
                let estimated_tvl_usd: Option<f64> = row.get(11)?;
                let evidence_source: Option<String> = row.get(12)?;
                let capacity_raw: Option<String> = row.get(13)?;
                Ok((
                    pool_id.to_string(),
                    fee,
                    dynamic_fee,
                    special_fee,
                    hooks,
                    token0,
                    token1,
                    depth_status,
                    has_liquidity,
                    liquidity_index,
                    usable_for_observation.unwrap_or(0),
                    estimated_tvl_usd,
                    evidence_source,
                    capacity_raw,
                ))
            },
        )
        .map_err(|error| error.to_string())?;

    let mut bad_pool_ids = BTreeSet::new();
    let mut issue_by_pool: BTreeMap<String, Value> = BTreeMap::new();
    let mut reference_by_pool: BTreeMap<String, Value> = BTreeMap::new();
    let mut candidate_total = 0;
    for row in rows {
        let row = row.map_err(|error| error.to_string())?;
        candidate_total += 1;
        let pool_quality = PoolQuality {
            fee: row.1,
            dynamic_fee: row.2,
            special_fee: row.3,
            hooks: row.4.as_deref(),
            token0: row.5.as_deref(),
            token1: row.6.as_deref(),
            has_liquidity: row.8,
            liquidity_index: row.9,
            depth_status: row.7.as_deref(),
            estimated_tvl_usd: row.11,
            capacity_raw: row
                .13
                .as_deref()
                .and_then(|value| value.trim().parse::<f64>().ok()),
            capacity_evidence_available: store_carries_capacity_evidence(&present),
        };
        // The reference-only observations are reported next to the
        // disqualifying reasons so that a malformed USD reading stays
        // visible, but they never make a pool ineligible: the USD valuation
        // is denominated in the chain's own quote asset and is therefore not
        // an admission gate.
        let observations = reference_only_observations(pool_quality.estimated_tvl_usd);
        let reasons = quality_reasons(pool_quality);
        if !reasons.is_empty() {
            bad_pool_ids.insert(row.0.clone());
            issue_by_pool.insert(
                row.0.clone(),
                json!({
                    "poolId": row.0,
                    "fee": row.1,
                    "dynamicFee": row.2 != 0,
                    "specialFee": row.3 != 0,
                    "depthStatus": row.7,
                    "hasLiquidity": row.8 != 0,
                    "usableForObservation": row.10 != 0,
                    "estimatedTvlUsd": row.11,
                    "evidenceSource": row.12,
                    "depthRank": depth_band_rank(row.7.as_deref()),
                    "reasons": reasons,
                    "referenceObservations": observations,
                }),
            );
        } else if !observations.is_empty() {
            // A pool that is admitted but carries a reference-only
            // observation is reported separately, so the observation is
            // never silently dropped when the pool itself is fine.  It is
            // kept out of ``reasonsByPool``: that map describes the pools
            // that are **ineligible**, and a pool that was admitted must
            // never appear in it.
            reference_by_pool.insert(
                row.0.clone(),
                json!({
                    "poolId": row.0,
                    "depthStatus": row.7,
                    "estimatedTvlUsd": row.11,
                    "observations": observations,
                }),
            );
        }
    }

    let bad_pool_list: Vec<String> = bad_pool_ids.into_iter().collect();
    let affected_loop_keys =
        affected_loop_keys_for_pool_ids(connection, &network, chain_id, &bad_pool_list)?;
    let affected_recommendations =
        count_affected_recommendations(connection, &network, chain_id, &bad_pool_list)?;

    Ok(json!({
        "network": network,
        "chainId": chain_id,
        "candidateTotal": candidate_total,
        "eligibleTotal": candidate_total - bad_pool_list.len(),
        "ineligibleTotal": bad_pool_list.len(),
        "ineligiblePoolIds": bad_pool_list,
        "reasonsByPool": issue_by_pool,
        "referenceObservationsByPool": reference_by_pool,
        "affectedClosedLoopCount": affected_loop_keys.len(),
        "affectedClosedLoopKeys": affected_loop_keys,
        "affectedRecommendationCount": affected_recommendations,
    }))
}

fn count_affected_recommendations(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> Result<usize, String> {
    let mut count = 0;
    for chunk in chunks(pool_ids, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "SELECT COUNT(*) FROM dex_candidate_pool_recommendations
             WHERE network = ? AND chain_id = ? AND pool_id IN ({placeholders})"
        );
        let params = params_for_scoped_values(network, chain_id, &chunk);
        let value: i64 = connection
            .query_row(&sql, params_from_iter(params.iter()), |row| row.get(0))
            .map_err(|error| error.to_string())?;
        count += value as usize;
    }
    Ok(count)
}

pub fn audit(store: &str, chain_id: i64) -> Result<Value, String> {
    let connection = Connection::open_with_flags(
        store,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    audit_internal(&connection, chain_id)
}

/// Idempotently create the review-window index used by the healer.
///
/// The existing `idx_cow_execution_reviews_scope_created` index is
/// `(network, chain_id, category, created_at DESC)`. Because `category` sits
/// between the scope columns and `created_at`, SQLite cannot seek the
/// `created_at` range and falls back to a temporary B-tree sort over the
/// whole table. This index has the scope columns adjacent to `created_at` so
/// the 24h review window becomes a range scan.
pub const REVIEW_SCOPE_CREATED_INDEX: &str = "idx_cow_execution_reviews_scope_created_v2";

/// Index that serves the profit-source breakdown of the review window.
///
/// The healer splits the review window by `selected_profit_source` to tell
/// an estimated rate delta apart from a confirmed contract result. Without
/// an index that column is scanned for every aggregate, so the breakdown
/// degenerates into a full table read.
pub const REVIEW_PROFIT_SOURCE_INDEX: &str =
    "idx_cow_execution_reviews_scope_profit_source_created";

pub fn ensure_indexes(store: &str, chain_id: i64) -> Result<Value, String> {
    let connection = Connection::open_with_flags(
        store,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    connection
        .execute_batch(
            "CREATE INDEX IF NOT EXISTS idx_cow_execution_reviews_scope_created_v2 \
             ON cow_execution_reviews(network, chain_id, created_at DESC)",
        )
        .map_err(|error| error.to_string())?;
    // The profit-source breakdown filters on `selected_profit_source` inside
    // the same scope and window, so the source column leads the index and
    // `created_at` follows it to keep the window a range scan.
    connection
        .execute_batch(
            "CREATE INDEX IF NOT EXISTS idx_cow_execution_reviews_scope_profit_source_created \
             ON cow_execution_reviews(network, chain_id, selected_profit_source, created_at DESC)",
        )
        .map_err(|error| error.to_string())?;
    let mut indexes = Vec::new();
    for name in [REVIEW_SCOPE_CREATED_INDEX, REVIEW_PROFIT_SOURCE_INDEX] {
        let index_exists: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?1",
                [name],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        indexes.push(json!({"name": name, "exists": index_exists > 0}));
    }
    let plan: Vec<String> = connection
        .prepare(
            "EXPLAIN QUERY PLAN SELECT id FROM cow_execution_reviews \
             WHERE network=?1 AND chain_id=?2 AND created_at>=datetime('now','-24 hours') \
             ORDER BY created_at DESC LIMIT 20000",
        )
        .and_then(|mut statement| {
            statement
                .query_map(
                    [network_for_chain_id(chain_id), chain_id.to_string()],
                    |row| {
                        // SQLite reports the plan `detail` column as TEXT. Reading it
                        // as an integer made `ensure-indexes` fail with
                        // "Invalid column type Text at index: 3" even though the
                        // index had been created successfully.
                        row.get::<_, String>(3)
                    },
                )?
                .collect::<Result<Vec<String>, rusqlite::Error>>()
        })
        .map_err(|error| error.to_string())?;
    // The profit-source breakdown must not fall back to a full table scan.
    let profit_source_plan: Vec<String> = connection
        .prepare(
            "EXPLAIN QUERY PLAN SELECT selected_profit_source, COUNT(*) FROM cow_execution_reviews \
             WHERE network=?1 AND chain_id=?2 AND created_at>=datetime('now','-24 hours') \
             AND selected_profit_source='native_contract_rate_delta_estimate' \
             GROUP BY selected_profit_source",
        )
        .and_then(|mut statement| {
            statement
                .query_map([network_for_chain_id(chain_id), chain_id.to_string()], |row| {
                    // SQLite reports the plan `detail` column as TEXT. Reading it
                // as an integer made `ensure-indexes` fail with
                // "Invalid column type Text at index: 3" even though the
                // index had been created successfully.
                row.get::<_, String>(3)
                })?
                .collect::<Result<Vec<String>, rusqlite::Error>>()
        })
        .map_err(|error| error.to_string())?;
    let profit_source_plan_uses_index = profit_source_plan
        .iter()
        .any(|line| line.contains(REVIEW_PROFIT_SOURCE_INDEX));
    Ok(json!({
        "store": store,
        "chainId": chain_id,
        "indexes": indexes,
        "reviewWindowQueryPlan": plan,
        "profitSourceQueryPlan": profit_source_plan,
        "profitSourceQueryPlanUsesIndex": profit_source_plan_uses_index,
    }))
}

/// Columns that carry the on-chain capacity evidence into the snapshot table.
///
/// The capacity is denominated in raw token units, so it carries no external
/// price dependency.
pub const DEPTH_CAPACITY_COLUMNS: &[(&str, &str)] = &[
    ("capacity_token0_raw", "TEXT"),
    ("capacity_token1_raw", "TEXT"),
    ("capacity_raw", "TEXT"),
    // DEP-4.2: the decimals-normalised binding-side capacity, which is the
    // quantity the depth bands are actually applied to.
    ("capacity_binding_raw", "REAL"),
    ("price_impact_bps", "REAL"),
    ("price_impact_limit_bps", "REAL"),
    ("depth_basis", "TEXT"),
    ("tick", "INTEGER"),
    ("sqrt_price_x96", "TEXT"),
    // The single-tick bounds of the first arbitrage version: the largest
    // input each direction may carry while the price stays inside the
    // pool's current tick range. A NULL bound means the pool's tick
    // spacing is unknown, so the range could not be located.
    ("single_tick_max_token0_raw", "TEXT"),
    ("single_tick_max_token1_raw", "TEXT"),
];

/// Adds the capacity columns to the liquidity snapshot table when they are
/// missing.
///
/// SQLite has no `ADD COLUMN IF NOT EXISTS`, so the column list is inspected
/// first. The operation is idempotent and only ever runs under `--apply`.
pub fn ensure_depth_schema(store: &str, chain_id: i64) -> Result<Value, String> {
    let connection = Connection::open_with_flags(
        store,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    let added = add_depth_capacity_columns(&connection)?;
    Ok(json!({
        "store": store,
        "chainId": chain_id,
        "table": DEPTH_SNAPSHOT_TABLE,
        "addedColumns": added,
    }))
}

/// The table that carries the on-chain depth observation.
const DEPTH_SNAPSHOT_TABLE: &str = "v4_exchange_pool_liquidity_snapshots";

/// Adds the missing depth capacity columns to an open connection.
///
/// The operation is idempotent: every column is inspected first, and only
/// the absent ones are added. It runs on the caller's connection so that a
/// write path can repair its own schema instead of failing on a missing
/// column.
fn add_depth_capacity_columns(connection: &Connection) -> Result<Vec<&'static str>, String> {
    let mut added = Vec::new();
    for (column, column_type) in DEPTH_CAPACITY_COLUMNS {
        let exists: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM pragma_table_info(?1) WHERE name=?2",
                rusqlite::params![DEPTH_SNAPSHOT_TABLE, column],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        if exists > 0 {
            continue;
        }
        connection
            .execute_batch(&format!(
                "ALTER TABLE {DEPTH_SNAPSHOT_TABLE} ADD COLUMN {column} {column_type}"
            ))
            .map_err(|error| error.to_string())?;
        added.push(*column);
    }
    Ok(added)
}

fn delete_values(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
    values: &[String],
    column: &str,
) -> Result<usize, String> {
    if values.is_empty() {
        return Ok(0);
    }
    let mut count = 0;
    for chunk in chunks(values, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "DELETE FROM {table}
             WHERE network = ? AND chain_id = ? AND {column} IN ({placeholders})"
        );
        let params = params_for_scoped_values(network, chain_id, &chunk);
        count += connection
            .execute(&sql, params_from_iter(params.iter()))
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(count)
}

fn delete_values_scoped(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
    anchor_symbol: &str,
    values: &[String],
    column: &str,
) -> Result<usize, String> {
    if values.is_empty() {
        return Ok(0);
    }
    let mut count = 0;
    for chunk in chunks(values, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "DELETE FROM {table}
             WHERE network = ? AND chain_id = ? AND anchor_symbol = ?
               AND {column} IN ({placeholders})"
        );
        let mut params = vec![
            network.to_string(),
            chain_id.to_string(),
            anchor_symbol.to_string(),
        ];
        params.extend(chunk.iter().cloned());
        count += connection
            .execute(&sql, params_from_iter(params.iter()))
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(count)
}

fn existing_candidate_pool_ids(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_symbol: &str,
    pair_variants: &[String],
) -> Result<Vec<String>, String> {
    let placeholders = pair_variants
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT pool_id FROM dex_candidate_pools
         WHERE network = ? AND chain_id = ? AND anchor_symbol = ?
           AND address_pair IN ({placeholders})
         ORDER BY pool_id"
    );
    let mut params = vec![
        network.to_string(),
        chain_id.to_string(),
        anchor_symbol.to_string(),
    ];
    params.extend(pair_variants.iter().cloned());
    let mut stmt = connection
        .prepare(&sql)
        .map_err(|error| error.to_string())?;
    let rows = stmt
        .query_map(params_from_iter(params.iter()), |row| {
            row.get::<_, String>(0)
        })
        .map_err(|error| error.to_string())?;
    let mut ids = Vec::new();
    for row in rows {
        ids.push(row.map_err(|error| error.to_string())?);
    }
    ids.sort();
    ids.dedup();
    Ok(ids)
}

pub fn apply_cleanup(store: &str, chain_id: i64) -> Result<Value, String> {
    let connection = Connection::open(store).map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    let audit = audit_internal(&connection, chain_id)?;
    let network = network_for_chain_id(chain_id);
    let bad_pool_ids: Vec<String> = audit["ineligiblePoolIds"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|value| value.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let loop_keys: Vec<String> = audit["affectedClosedLoopKeys"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|value| value.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let mut cleanup = serde_json::Map::new();
    if !bad_pool_ids.is_empty() {
        cleanup.insert(
            "candidatePoolsDeleted".to_string(),
            json!(delete_values(
                &connection,
                "dex_candidate_pools",
                &network,
                chain_id,
                &bad_pool_ids,
                "pool_id",
            )?),
        );
        cleanup.insert(
            "recommendationsDeleted".to_string(),
            json!(delete_values(
                &connection,
                "dex_candidate_pool_recommendations",
                &network,
                chain_id,
                &bad_pool_ids,
                "pool_id",
            )?),
        );
    }
    cleanup.insert("consumersNeedRebuild".to_string(), json!(loop_keys.len()));
    Ok(json!({
        "audit": audit,
        "cleanup": cleanup,
    }))
}

#[derive(serde::Serialize)]
struct RawEligiblePool {
    pool_id: String,
    token0: String,
    token1: String,
    fee: i64,
    depth_status: String,
    liquidity_index: Option<f64>,
    estimated_tvl_usd: Option<f64>,
    depth_score_usd: Option<f64>,
    capacity_raw: Option<String>,
    /// The decimals-normalised binding-side capacity.
    ///
    /// ARCH-1a: this is the primary recommendation sort key. It measures the
    /// weaker side of the pool in the pool's own token units, so it is
    /// comparable across tokens with different decimals. The candidate table
    /// has no dedicated column for it, so it travels inside `depth_band_json`
    /// alongside the band it produced.
    capacity_binding_raw: Option<f64>,
    /// The realised price impact of the measured capacity, in basis points.
    ///
    /// ARCH-1a: this is the secondary sort key. Among pools that carry the
    /// same binding capacity the one that disturbs the price least is
    /// preferred.
    price_impact_bps: Option<f64>,
    /// The measured capacity payload from `v4_exchange_pool_liquidity_snapshots`.
    ///
    /// D6: this is the ONLY source of token-denominated capacity AMOUNTS.
    /// The pool address columns must never be written into the `balances`
    /// payload, because the sizing path parses them as decimal amounts.
    snapshot_balances_json: Option<String>,
}

/// Minimum on-chain capacity a candidate pool must be able to absorb within
/// the price-impact limit, expressed in raw token units of the traded side.
///
/// The capacity criterion is the primary admission signal: a pool with a
/// small TVL but enough tradable depth can still carry a position, while a
/// large TVL that cannot absorb the position cannot. The threshold is
/// deliberately not expressed in USD: an external quote does not express
/// tradable value inside the arbitrage environment.
///
/// The floor is the shared "shallow" band boundary, so a pool is admitted
/// exactly when its capacity reaches the shallow band. The healer used a
/// different floor (1 raw unit), which admitted pools the depth verdict
/// called dust; the two sides now agree on one boundary.

/// Maximum price impact, in basis points, that a candidate pool may impose on
/// the sized position. This mirrors
/// `dex_quote_evidence_pool.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS` so both
/// admission and execution use one epsilon.
pub const CANDIDATE_PRICE_IMPACT_LIMIT_BPS: f64 = depth_config::PRICE_IMPACT_LIMIT_BPS;

/// Compares a raw decimal amount stored as TEXT against an integer floor.
///
/// The capacity is a raw uint128 amount. SQLite's `CAST(... AS INTEGER)`
/// saturates at `i64::MAX`, so every capacity above that limit would be
/// clamped to the same value: the admission predicate would then compare a
/// clamped maximum against the floor, and the ordering would collapse every
/// oversized pool onto one rank. The comparison is therefore done on the
/// decimal text itself, which is exact for the whole uint128 range.
///
/// The text is normalised first: optional whitespace, an optional leading
/// `+`, and leading zeros are removed. Anything that is not a plain decimal
/// integer is treated as "not measurable" and never satisfies the floor.
pub fn decimal_text_at_least(value: Option<&str>, floor: u128) -> bool {
    let Some(value) = value else {
        return false;
    };
    let trimmed = value.trim();
    let digits = trimmed.strip_prefix('+').unwrap_or(trimmed);
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return false;
    }
    let normalised = digits.trim_start_matches('0');
    let normalised = if normalised.is_empty() {
        "0"
    } else {
        normalised
    };
    let floor_text = floor.to_string();
    normalised.len() > floor_text.len()
        || (normalised.len() == floor_text.len() && normalised >= floor_text.as_str())
}

fn eligible_raw_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    limit: usize,
) -> Result<(Vec<RawEligiblePool>, Vec<String>), String> {
    // The admission predicate deliberately does NOT filter on
    // `liquidity_index`. The liquidity index is `L / 1e18`, which is a raw
    // concentrated-liquidity scale and carries no information about depth:
    // measured against the true reserve TVL its rank correlation is only
    // ~0.09. Filtering on it removed genuinely deep pools (the ETH/USDG pool
    // with a $1.28M reserve and 24 live closed loops had an index of 0.0285).
    //
    // Admission therefore uses the on-chain capacity as the primary signal,
    // and the USD valuation only as the tie-breaker for ordering. The USD
    // valuation is never a gate: it cannot express tradable value inside the
    // arbitrage environment, and a bad reading of it previously disqualified
    // genuinely deep pools.
    // DEP-4.2: admission and ordering use the decimals-normalised
    // binding-side capacity. A raw capacity is not comparable across tokens
    // with different decimals, and the raw maximum of the two sides hides a
    // pool whose weaker side cannot absorb anything.
    //
    // The raw floor stays as a dust guard only, so a snapshot written before
    // the binding capacity was introduced is still admitted when it carries
    // a real raw capacity.
    // Every chain owns its own SQLite file, and the files are not
    // guaranteed to carry the same schema version. The capacity columns
    // exist only on a store that has been migrated to the binding-capacity
    // schema, so the query is rendered against the schema the store
    // actually has.
    let present = optional_snapshot_columns(connection)?;
    let has_capacity = present.contains(&"capacity_raw");
    let depth_score_expression = optional_column_expression("depth_score_usd", &present);
    let capacity_expression = optional_column_expression("capacity_raw", &present);
    let binding_expression = optional_column_expression("capacity_binding_raw", &present);
    let price_impact_expression = optional_column_expression("price_impact_bps", &present);
    // Without the capacity columns there is no raw capacity to compare, so
    // the admission falls back to the measured depth status and the USD
    // valuation instead of rejecting every pool.
    // The capacity gate is the **admission floor** of the shared depth
    // policy. It compares the decimals-normalised binding-side capacity
    // against ``minCandidateBindingCapacity``.
    //
    // The previous form carried ``OR (s.capacity_binding_raw IS NULL AND
    // 1 = 1)``, which is a tautology: it made the whole capacity condition
    // true for every pool, so the floor was never applied and every pool
    // with a capacity row was admitted regardless of its measured depth.
    // A pool whose binding capacity is **absent** is a coverage gap of the
    // measurement, so it stays in the query (the kernel publishes the
    // verdict for it) but it must not satisfy the floor on its own: the
    // floor is only satisfied by a **measured** capacity that reaches it.
    // The capacity floor is **not** expressed in SQL. It is applied in Rust
    // below, because the raw capacity is a uint128 stored as TEXT and
    // SQLite's INTEGER cast saturates at i64::MAX, so a SQL predicate would
    // compare a clamped value instead of the real capacity. The Rust path
    // also distinguishes "capacity absent" (a coverage gap of the
    // measurement, admitted with a gap reason) from "capacity measured
    // below the floor" (rejected), which a single SQL predicate cannot.
    //
    // The USD valuation is never part of this gate: the shared depth policy
    // lists ``depth_admission`` and ``capacity_admission`` under
    // ``usdgPegPolicy.forbiddenUses``.
    let capacity_gate = "AND 1 = 1".to_string();
    let capacity_order = if has_capacity {
        "CAST(COALESCE(s.capacity_binding_raw, '0') AS REAL) DESC,\n                 s.capacity_raw DESC,"
    } else {
        ""
    };
    let sql = format!(
        r#"
        SELECT e.pool_id, e.token0, e.token1, e.fee,
               s.depth_status, s.liquidity_index, s.has_liquidity,
               s.estimated_tvl_usd, {depth_score_expression}, {capacity_expression},
               {binding_expression}, {price_impact_expression},
               s.balances_json
        FROM v4_exchange_pools e
        JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = e.network AND s.chain_id = e.chain_id
         AND s.protocol = e.protocol AND s.pool_id = e.pool_id
        WHERE e.network = ? AND e.chain_id = ? AND e.protocol = 'uniswap_v4'
          AND ((lower(e.token0) = ? AND lower(e.token1) = ?)
            OR (lower(e.token0) = ? AND lower(e.token1) = ?))
          AND length(e.token0) = 42 AND length(e.token1) = 42
          AND e.fee > 0 AND e.fee <= ?
          AND e.dynamic_fee = 0 AND e.special_fee = 0
          AND lower(COALESCE(e.hooks, '')) IN ('', '0x0000000000000000000000000000000000000000')
          AND s.has_liquidity = 1
          AND COALESCE(s.depth_status, '') IN ('deep', 'liquid', 'medium')
          {capacity_gate}
        ORDER BY {capacity_order}
                 e.pool_id
    "#
    );
    let base = base.trim().to_lowercase();
    let quote = quote.trim().to_lowercase();
    let parameters = vec![
        rusqlite::types::Value::Text(network.to_string()),
        rusqlite::types::Value::Text(chain_id.to_string()),
        rusqlite::types::Value::Text(base.to_string()),
        rusqlite::types::Value::Text(quote.to_string()),
        rusqlite::types::Value::Text(quote.to_string()),
        rusqlite::types::Value::Text(base.to_string()),
        // The fee ceiling is the shared pool-library ceiling, so the
        // admission query and the storage rule cannot drift apart.
        rusqlite::types::Value::Integer(depth_config::MAX_CANDIDATE_FIXED_FEE),
    ];
    let mut stmt = connection.prepare(&sql).map_err(|error| error.to_string())?;
    let rows = stmt
        .query_map(
            params_from_iter(parameters),
            |row| {
                Ok(RawEligiblePool {
                    pool_id: row.get(0)?,
                    token0: row.get(1)?,
                    token1: row.get(2)?,
                    fee: row.get(3)?,
                    depth_status: row.get(4)?,
                    liquidity_index: row.get(5)?,
                    estimated_tvl_usd: row.get(7)?,
                    depth_score_usd: row.get(8)?,
                    capacity_raw: row.get(9)?,
                    capacity_binding_raw: row.get(10)?,
                    price_impact_bps: row.get(11)?,
                    snapshot_balances_json: row.get(12)?,
                })
            },
        )
        .map_err(|error| error.to_string())?;
    let mut pools = Vec::new();
    let mut pool_ids = Vec::new();
    for row in rows {
        let row = row.map_err(|error| error.to_string())?;
        // The raw capacity floor is applied here rather than in SQL: the
        // capacity is a uint128 stored as TEXT, and SQLite's INTEGER cast
        // saturates at i64::MAX, so a SQL predicate would compare a clamped
        // value instead of the real capacity.
        if has_capacity {
            let raw_capacity_at_floor =
                decimal_text_at_least(row.capacity_raw.as_deref(), MIN_CANDIDATE_CAPACITY_RAW);
            let binding_at_floor = row
                .capacity_binding_raw
                .map(|value| value >= MIN_CANDIDATE_BINDING_CAPACITY)
                .unwrap_or(false);
            if !raw_capacity_at_floor {
                continue;
            }
            if !binding_at_floor && row.capacity_binding_raw.is_some() {
                continue;
            }
        }
        pool_ids.push(row.pool_id.clone());
        pools.push(row);
        if pools.len() >= limit {
            break;
        }
    }
    Ok((pools, pool_ids))
}

fn upsert_eligible_pool_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    pool_group: &str,
    pools: &[RawEligiblePool],
) -> Result<usize, String> {
    let pair = canonical_pair_key(base, quote);
    let protocol = "uniswap_v4".to_string();
    let anchor_scope = "v4".to_string();
    let pool_group = pool_group.to_string();
    let fee_label = "".to_string();
    let zero_hook = ZERO_ADDRESS.to_string();
    let empty_text = "".to_string();
    let empty_json = "{}".to_string();
    let hop_json = "{\"first\":false,\"third\":false}".to_string();
    let none_text = Option::<String>::None;
    let none_i64 = Option::<i64>::None;
    let mut insert_stmt = connection
        .prepare(
            r#"
            INSERT OR REPLACE INTO dex_candidate_pools (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair,
                pool_id, pool_key_json, pool_manager, token0, token1,
                fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                liquidity_index, estimated_tvl_usd, balances_json,
                depth_status, depth_band_json, usable_for_observation,
                is_first_hop_recommended, is_third_hop_recommended,
                hop_recommendation_json, optimal_roles_json,
                liquidity_read_at, liquidity_block_number,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
            "#,
        )
        .map_err(|error| error.to_string())?;
    let mut inserted = 0;
    for pool in pools {
        let fee = pool.fee;
        let capacity_raw = pool
            .capacity_raw
            .as_deref()
            .and_then(|value| value.trim().parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value > 0.0);
        // ARCH-1a: the candidate table must carry the binding-side capacity
        // and the realised price impact, because they are the primary and
        // secondary recommendation sort keys. The previous call passed `None`
        // for the binding capacity, so every candidate row stored
        // `capacityBindingRaw: null` and the sort key had to be reconstructed
        // from a secondary source that no longer exists.
        let depth_band_json = depth_band_json_with_admission(
            &pool.depth_status,
            pool.estimated_tvl_usd,
            capacity_raw,
            pool.capacity_binding_raw,
            pool.price_impact_bps,
            // The candidate row does not carry the liquidity observation, so
            // the verdict stays on the capacity and the depth label alone. A
            // missing observation is a coverage gap, never a rejection.
            None,
        );
        // Persist the capacity evidence that drove the admission decision. The
        // candidate schema has no dedicated capacity column, so it is stored
        // inside `balances_json`, which was previously always written as an
        // empty object and therefore carried no information at all. The
        // `token0`/`token1` entries carry the token-denominated reserves that
        // the sizing path needs to stop falling back to a fixed probe cap.
        // D6: `token0` / `token1` in this payload MUST be token-denominated
        // AMOUNTS, not token addresses.
        //
        // The consumer is `dex_quote_evidence_size.py::_pool_borrow_side_amount`,
        // which resolves the borrow side through
        // `_positive_decimal_or_none(balances["token0"|"token1"])`. That helper
        // funnels into `_decimal()`, which RAISES `ValueError("invalid decimal
        // amount")` for a `0x...` string instead of returning `None`.
        //
        // The previous code wrote `pool.token0` / `pool.token1`, which come
        // from `v4_exchange_pools.token0` / `.token1` and are therefore
        // ADDRESSES. That silently corrupted the payload: the sizing path
        // would have raised an uncaught exception inside the review loop
        // rather than degrading to its `None` fallback.
        //
        // The amount fields now come from the measured snapshot payload, and
        // the addresses are preserved under explicit `token0Address` /
        // `token1Address` keys so nothing loses the address identity.
        let balances_json = match pool.snapshot_balances_json.as_deref() {
            Some(raw) if !raw.trim().is_empty() && raw.trim() != "{}" => raw.trim().to_string(),
            _ => json!({
                // No measured snapshot yet: emit an EMPTY payload rather than
                // an address masquerading as an amount. The sizing path then
                // degrades safely to its `None` fallback and the pool stays
                // visible for observation.
                "token0": Value::Null,
                "token1": Value::Null,
                "token0Address": pool.token0,
                "token1Address": pool.token1,
                "capacityRaw": pool.capacity_raw,
                "depthScoreUsd": pool.depth_score_usd,
                "estimatedTvlUsd": pool.estimated_tvl_usd,
                "priceImpactLimitBps": CANDIDATE_PRICE_IMPACT_LIMIT_BPS,
                "depthBasis": DEPTH_BASIS,
                "evidenceSource": "pool_manager_candidate_refresh_without_measured_snapshot",
            })
            .to_string(),
        };
        let params: Vec<&dyn rusqlite::ToSql> = vec![
            &network,
            &chain_id,
            &protocol,
            &anchor_scope,
            &pool_group,
            &pair,
            &pair,
            &pair,
            &pool.pool_id,
            &none_text,
            &none_text,
            &pool.token0,
            &pool.token1,
            &fee,
            &fee_label,
            &0i64,
            &0i64,
            &none_i64,
            &zero_hook,
            &none_text,
            &none_i64,
            &1i64,
            &empty_text,
            &pool.liquidity_index,
            &pool.estimated_tvl_usd,
            &balances_json,
            &pool.depth_status,
            &depth_band_json,
            &1i64,
            &0i64,
            &0i64,
            &hop_json,
            &empty_json,
            &none_text,
            &none_i64,
        ];
        inserted += insert_stmt
            .execute(params_from_iter(params))
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(inserted)
}

pub fn refresh_pair(
    store: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    pool_group: &str,
    limit: usize,
    apply: bool,
) -> Result<Value, String> {
    let network = network_for_chain_id(chain_id);
    let connection = if apply {
        Connection::open(store).map_err(|error| error.to_string())?
    } else {
        Connection::open_with_flags(
            store,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .map_err(|error| error.to_string())?
    };
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    let pair_variants = pair_key_variants(base, quote);
    let existing_ids =
        existing_candidate_pool_ids(&connection, &network, chain_id, pool_group, &pair_variants)?;
    let (pools, eligible_ids) =
        eligible_raw_pools(&connection, &network, chain_id, base, quote, limit.max(1))?;
    let eligible_set: BTreeSet<&String> = eligible_ids.iter().collect();
    let existing_set: BTreeSet<&String> = existing_ids.iter().collect();
    let stale_ids: Vec<String> = existing_ids
        .iter()
        .filter(|pool_id| !eligible_set.contains(pool_id))
        .cloned()
        .collect();
    let missing_ids: Vec<String> = eligible_ids
        .iter()
        .filter(|pool_id| !existing_set.contains(pool_id))
        .cloned()
        .collect();
    let affected_keys =
        affected_loop_keys_for_pool_ids(&connection, &network, chain_id, &stale_ids)?;

    let mut inserted = 0;
    let mut deleted = 0;
    if apply {
        inserted = upsert_eligible_pool_rows(
            &connection,
            &network,
            chain_id,
            base,
            quote,
            pool_group,
            &pools,
        )?;
        deleted = delete_values_scoped(
            &connection,
            "dex_candidate_pools",
            &network,
            chain_id,
            pool_group,
            &stale_ids,
            "pool_id",
        )?;
        let deleted_recommendations = delete_values_scoped(
            &connection,
            "dex_candidate_pool_recommendations",
            &network,
            chain_id,
            pool_group,
            &stale_ids,
            "pool_id",
        )?;
        deleted += deleted_recommendations;
    }

    Ok(json!({
        "network": network,
        "chainId": chain_id,
        "poolGroup": pool_group,
        "base": base.to_lowercase(),
        "quote": quote.to_lowercase(),
        "pairKey": canonical_pair_key(base, quote),
        "apply": apply,
        "limit": limit.max(1),
        "existingPoolCount": existing_ids.len(),
        "eligiblePoolCount": pools.len(),
        "eligiblePoolIds": eligible_ids,
        "stalePoolCount": stale_ids.len(),
        "stalePoolIds": stale_ids,
        "missingPoolCount": missing_ids.len(),
        "missingPoolIds": missing_ids,
        "inserted": inserted,
        "deletedRows": deleted,
        "consumersNeedRebuild": affected_keys.len(),
        "affectedClosedLoopKeys": affected_keys,
    }))
}

/// Which pool population the `depth` command should refresh.
///
/// The candidate scope is the default because it is the population the
/// arbitrage pipeline actually consumes: the union of `dex_candidate_pools`
/// and `manager_dex_candidate_pools`. The full snapshot scope covers every
/// pool ever observed in `v4_exchange_pool_liquidity_snapshots` and is a
/// long-running background task, not an on-demand refresh.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum DepthScope {
    /// Candidate pools referenced by the candidate tables (default).
    #[default]
    Candidate,
    /// Every pool present in the liquidity snapshot table.
    All,
}

impl DepthScope {
    /// Parses the `--scope` command line value.
    pub fn parse(value: &str) -> Result<Self, String> {
        match value.trim().to_lowercase().as_str() {
            "candidate" => Ok(DepthScope::Candidate),
            "all" => Ok(DepthScope::All),
            other => Err(format!(
                "invalid depth scope: {other} (expected 'candidate' or 'all')"
            )),
        }
    }

    /// Stable wire name used in the report.
    pub fn as_str(&self) -> &'static str {
        match self {
            DepthScope::Candidate => "candidate",
            DepthScope::All => "all",
        }
    }
}

/// Options for the `depth` command.
#[derive(Clone, Debug, Default)]
pub struct DepthOptions {
    /// Restrict the refresh to these pool ids. When non-empty this wins over
    /// `scope` entirely.
    pub pool_ids: Vec<String>,
    /// Which pool population to refresh.
    pub scope: DepthScope,
    /// Skip pools whose snapshot already carries a `liquidity_read_at`
    /// timestamp, so a long run can be resumed after an interruption.
    pub resume: bool,
    /// Upper bound on the number of pools processed in this run. `None` means
    /// "process everything the scope resolved to".
    pub max_pools: Option<usize>,
    /// Emit a progress line to stderr after this many processed pools.
    pub progress_every: usize,
    /// Explicit RPC endpoint. When empty the configured environment
    /// variables are consulted.
    pub rpc_url: Option<String>,
    /// Number of `eth_call` requests sent per JSON-RPC batch.
    pub batch_size: usize,
    /// Per-request timeout in seconds.
    pub timeout_seconds: u64,
    /// Write the refreshed observations back to the database.
    pub apply: bool,
    /// Path of a `token-manager value-tokens` report holding the USDG
    /// valuation of every token.
    ///
    /// When the path is present, the refresh also writes the
    /// `token_usdg_depth_measurements` rows the token manager's economic
    /// gate reads. The report is only an input: a token without a
    /// `route_found` valuation simply produces no depth row, because
    /// "not valued" and "worth zero" are different statements.
    pub usdg_valuation_report: Option<String>,
    /// Path of the durable USDG valuation store.
    ///
    /// The report file is transient; the store is the statement the
    /// system actually relies on. When a store path is given it wins
    /// over the report, so the depth measurement always uses the
    /// valuations that survived a restart.
    pub usdg_valuation_db: Option<String>,
}

/// Refreshes the on-chain depth evidence of the candidate pools.
///
/// The command reads the live pool state from the chain, derives the
/// token-denominated capacity of every pool, and records the observation.
/// Without `--apply` nothing is written: the report is returned only.
pub fn refresh_depth(store: &str, chain_id: i64, options: &DepthOptions) -> Result<Value, String> {
    let network = network_for_chain_id(chain_id);
    let connection = if options.apply {
        Connection::open(store).map_err(|error| error.to_string())?
    } else {
        Connection::open_with_flags(
            store,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .map_err(|error| error.to_string())?
    };
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;

    // The write path below stores the measured capacity in dedicated
    // columns. When those columns are missing the write would fail with
    // "no such column", which reads like a corrupted database rather than
    // an un-migrated schema. The refresh therefore repairs its own schema
    // before it measures, so `depth --apply` works on a store that has
    // never seen `ensure-depth-schema`. The repair is idempotent and only
    // ever runs under `--apply`.
    let depth_columns_added = if options.apply {
        add_depth_capacity_columns(&connection)?
    } else {
        Vec::new()
    };

    let started = std::time::Instant::now();
    let resolved = collect_candidate_pool_ids(
        &connection,
        &network,
        chain_id,
        &options.pool_ids,
        options.scope,
    )?;
    let total_pool_count = resolved.pool_count;
    let mut pool_ids = resolved.pool_ids;
    let mut skipped_resume = resolved.skipped_resume;
    let scope_source_tables = resolved.source_tables;

    // DEP-4.2: the depth bands need the token decimals, because a raw unit
    // of a 6-decimal stablecoin and a raw unit of an 18-decimal token are
    // not comparable quantities.
    let token_decimals = read_token_decimals(&connection, &network, chain_id)?;
    let pool_tokens = read_pool_tokens(&connection, &network, chain_id)?;
    let pool_tick_spacings = read_pool_tick_spacings(&connection, &network, chain_id);

    // VAL-4-8: the USDG depth evidence is a second product of the same
    // measurement pass. The valuation report is read once, up front, so a
    // missing report simply produces no depth rows instead of an error.
    let usdg_valuations = match options.usdg_valuation_db.as_deref().map(str::trim) {
        Some(path) if !path.is_empty() => {
            read_usdg_valuations_from_db(std::path::Path::new(path), &network, chain_id)?
        }
        _ => match options.usdg_valuation_report.as_deref().map(str::trim) {
            Some(path) if !path.is_empty() => read_usdg_valuations(std::path::Path::new(path))?,
            _ => Vec::new(),
        },
    };
    let usdg_valuation_index = valuation_index(&usdg_valuations);

    if options.resume && !pool_ids.is_empty() {
        let fresh = filter_already_refreshed(&connection, &network, chain_id, &pool_ids)?;
        skipped_resume += fresh.len();
        pool_ids.retain(|pool_id| !fresh.contains(pool_id));
    }

    let mut truncated = false;
    if let Some(max_pools) = options.max_pools {
        if pool_ids.len() > max_pools {
            pool_ids.truncate(max_pools);
            truncated = true;
        }
    }

    if pool_ids.is_empty() {
        return Ok(json!({
            "ok": true,
            "apply": options.apply,
            "network": network,
            "chainId": chain_id,
            "scope": options.scope.as_str(),
            "sourceTables": scope_source_tables,
            "totalPoolCount": total_pool_count,
            "poolCount": 0,
            "processedPoolCount": 0,
            "skippedResumedPoolCount": skipped_resume,
            "truncated": truncated,
            "depthColumnsAdded": depth_columns_added,
            "durationMs": started.elapsed().as_millis() as u64,
            "refreshed": [],
            "errors": [],
        }));
    }

    let rpc_urls: Vec<String> = match &options.rpc_url {
        Some(url) if !url.trim().is_empty() => vec![url.trim().to_string()],
        // The endpoints have to belong to the chain that is being measured.
        // Reading the mainnet configuration for a testnet run would measure
        // the mainnet's pools and report them as the testnet's depth.
        _ => chain::rpc_urls_for(chain_id),
    };
    if rpc_urls.is_empty() {
        return Err(
            "no RPC endpoint configured; set the chain RPC environment variable or pass --rpc-url"
                .to_string(),
        );
    }

    let batch_size = options.batch_size.max(1);
    let timeout_seconds = if options.timeout_seconds == 0 {
        20
    } else {
        options.timeout_seconds
    };
    let mut refreshed = Vec::new();
    let mut errors = Vec::new();
    let mut written = 0usize;
    let mut processed = 0usize;
    // CORE-R34-02: how many pools the node answered only half of. Those
    // pools keep their stored measurement and are reported separately, so a
    // rate limited node can never look like a set of empty pools.
    let mut incomplete_reads = 0usize;
    let mut rpc_endpoint_used = String::new();
    let mut skipped_endpoints: Vec<String> = Vec::new();
    // VAL-4-8: the USDG depth rows are accumulated while the pool loop runs
    // and written after it, so the evidence shares the measurement pass and
    // the timestamp of the snapshot it was derived from.
    let mut usdg_depth_observations: Vec<UsdgDepthObservation> = Vec::new();

    for chunk in pool_ids.chunks(batch_size) {
        // The public node rate-limits large batches. When a batch is refused
        // the chunk is split and re-read instead of losing the pools, so the
        // refresh stays complete without a manual retry loop.
        let batch = match chain::read_v4_pool_states_with_fallback(
            &rpc_urls,
            chain::ROBINHOOD_V4_POOL_MANAGER,
            chunk,
            timeout_seconds,
        ) {
            Ok(batch) => batch,
            Err(error) if chunk.len() > 1 && is_rate_limited(&error) => {
                let midpoint = chunk.len() / 2;
                let (head, tail) = chunk.split_at(midpoint);
                let mut split_errors = Vec::new();
                let mut split_states = Vec::new();
                for sub in [head, tail] {
                    match chain::read_v4_pool_states_with_fallback(
                        &rpc_urls,
                        chain::ROBINHOOD_V4_POOL_MANAGER,
                        sub,
                        timeout_seconds,
                    ) {
                        Ok(part) => {
                            if rpc_endpoint_used.is_empty() {
                                rpc_endpoint_used = part.rpc_url.clone();
                            }
                            skipped_endpoints.extend(part.skipped_endpoints.iter().cloned());
                            split_states.extend(part.states);
                            split_errors.extend(part.errors);
                        }
                        Err(sub_error) => split_errors.push(sub_error),
                    }
                }
                chain::V4PoolStateBatch {
                    states: split_states,
                    errors: split_errors,
                    rpc_url: rpc_endpoint_used.clone(),
                    block_number: None,
                    skipped_endpoints: Vec::new(),
                }
            }
            Err(error) => return Err(error),
        };
        if rpc_endpoint_used.is_empty() {
            rpc_endpoint_used = batch.rpc_url.clone();
        }
        skipped_endpoints.extend(batch.skipped_endpoints.iter().cloned());
        for state in &batch.states {
            // CORE-R34-02: a pool whose read was only half successful is not
            // a measurement.
            //
            // The batch reader reads two things per pool: the `slot0` (the
            // price) and the liquidity. When one of the two failed, the
            // state carries the default value of the missing half: a zero
            // price and a zero liquidity. Writing that state would replace a
            // good stored measurement with a fabricated empty pool, and the
            // admission verdict would then reject a pool that is in fact
            // full.
            //
            // The stored row is left untouched instead. The failure is
            // reported through the error list, so the gap stays visible, and
            // the next successful read replaces the row.
            //
            // This is a **core** defect and not a property of any chain: the
            // reader is chain agnostic, and a node that rate limits one
            // chain can rate limit every chain.
            if !depth_state_is_writable(&state) {
                incomplete_reads += 1;
                continue;
            }
            // The tick spacing decides the granularity of the tick range,
            // so the version gate can only be computed when the pool's
            // spacing is known. A pool without one keeps the depth
            // capacity as its bound.
            let tick_spacing = pool_tick_spacings
                .get(&state.pool_id.trim().to_lowercase())
                .copied()
                .unwrap_or(0);
            let capacity =
                depth::evaluate_capacity(state, depth::PRICE_IMPACT_LIMIT_BPS, tick_spacing);
            let (token0_decimals, token1_decimals) =
                pool_decimals(&token_decimals, &pool_tokens, &state.pool_id);
            let binding_capacity = binding_side_capacity(
                Some(capacity.capacity_token0_raw),
                Some(capacity.capacity_token1_raw),
                token0_decimals,
                token1_decimals,
            );
            let observation = json!({
                "poolId": state.pool_id,
                "sqrtPriceX96": state.sqrt_price_x96,
                "tick": state.tick,
                "lpFee": state.lp_fee,
                "protocolFee": state.protocol_fee,
                "liquidity": state.liquidity.to_string(),
                "hasLiquidity": state.has_liquidity,
                "token0Decimals": token0_decimals,
                "token1Decimals": token1_decimals,
                "capacityBindingRaw": binding_capacity,
                "capacityBand": depth_band_for_binding_capacity(binding_capacity),
                "capacity": depth::capacity_json(&capacity, state.tick, &state.sqrt_price_x96),
            });
            if options.apply {
                write_depth_snapshot(
                    &connection,
                    &network,
                    chain_id,
                    state,
                    &capacity,
                    binding_capacity,
                    token0_decimals,
                    token1_decimals,
                )?;
                written += 1;
            }
            // A pool contributes one row per side that has both a measured
            // capacity and a USDG valuation. A side without a valuation
            // contributes nothing: the absence of the row is the statement
            // "this depth was not measured", never "this depth is zero".
            let pool_state_version = Some(state.sqrt_price_x96.clone());
            if let Some((token0, token1)) = pool_tokens.get(&state.pool_id.trim().to_lowercase()) {
                let sides = [
                    (
                        token0.as_str(),
                        capacity.capacity_token0_raw,
                        token0_decimals,
                    ),
                    (
                        token1.as_str(),
                        capacity.capacity_token1_raw,
                        token1_decimals,
                    ),
                ];
                for (token_address, capacity_raw, decimals) in sides {
                    // The native zero address is a placeholder for an
                    // unknown pool side, not a token. It must never become
                    // a depth row.
                    if !usdg_depth::is_depth_bearing_token(token_address) {
                        continue;
                    }
                    let valuation = usdg_valuation_index.get(token_address).copied();
                    let Some(depth_usdg_raw) =
                        capacity_to_usdg_depth(capacity_raw, decimals, valuation)
                    else {
                        continue;
                    };
                    usdg_depth_observations.push(UsdgDepthObservation {
                        token_address: token_address.to_string(),
                        pool_id: state.pool_id.clone(),
                        depth_usdg_raw,
                        token_decimals: decimals,
                        valuation_usdg_raw: valuation,
                        price_impact_bps: Some(capacity.price_impact_bps),
                        pool_state_version: pool_state_version.clone(),
                    });
                }
            }
            refreshed.push(observation);
        }
        errors.extend(batch.errors.iter().cloned());
        processed += chunk.len();
        if options.progress_every > 0 && processed % options.progress_every < chunk.len() {
            eprintln!("progress {processed}/{total_pool_count} depth pools");
        }
    }

    // D5: the snapshot rows written above are not sufficient on their own.
    // The sizing path reads the CANDIDATE table, so the measured payload has
    // to be propagated there before the ladder can use it.
    let candidate_balances_propagated = if options.apply {
        let touched: Vec<String> = refreshed
            .iter()
            .filter_map(|observation| {
                observation
                    .get("poolId")
                    .and_then(Value::as_str)
                    .map(|value| value.to_string())
            })
            .collect();
        propagate_balances_to_candidate_pools(&connection, &network, chain_id, &touched)?
    } else {
        0
    };

    // VAL-4-8: write the USDG depth evidence. The write happens only with
    // `--apply`, and only for the pools that actually produced a
    // measurement, so an unmeasured pool never gains a row.
    let usdg_depth_rows_written = if options.apply && !usdg_depth_observations.is_empty() {
        let measured_at = chrono::Utc::now().to_rfc3339();
        write_usdg_depth_observations(
            &connection,
            &network,
            chain_id,
            &measured_at,
            &usdg_depth_observations,
        )?
    } else {
        0
    };

    skipped_endpoints.sort();
    skipped_endpoints.dedup();

    Ok(json!({
        "ok": true,
        "apply": options.apply,
        "network": network,
        "chainId": chain_id,
        "scope": options.scope.as_str(),
        "sourceTables": scope_source_tables,
        "totalPoolCount": total_pool_count,
        "poolCount": pool_ids.len(),
        "processedPoolCount": processed,
        "skippedResumedPoolCount": skipped_resume,
        "truncated": truncated,
        "refreshedCount": refreshed.len(),
        "writtenCount": written,
        "incompleteReadCount": incomplete_reads,
        "candidateBalancesPropagated": candidate_balances_propagated,
        "usdgDepthRowsWritten": usdg_depth_rows_written,
        "usdgValuationCount": usdg_valuations.len(),
        "depthColumnsAdded": depth_columns_added,
        "rpcEndpointUsed": rpc_endpoint_used,
        "skippedEndpoints": skipped_endpoints,
        "priceImpactLimitBps": depth::PRICE_IMPACT_LIMIT_BPS,
        "depthBasis": depth::DEPTH_BASIS,
        "durationMs": started.elapsed().as_millis() as u64,
        "refreshed": refreshed,
        "errors": errors,
    }))
}

/// The resolved pool population of one `depth` run.
struct ResolvedPoolIds {
    pool_ids: Vec<String>,
    /// How many pools the scope resolved to before any resume/max filter.
    pool_count: usize,
    /// How many pools were skipped because a snapshot already carried a
    /// `liquidity_read_at` timestamp.
    skipped_resume: usize,
    /// The tables that contributed to the resolved population.
    source_tables: Vec<String>,
}

/// Whether an error message describes a rate-limited RPC response.
fn is_rate_limited(error: &str) -> bool {
    error.contains("429") || error.to_lowercase().contains("too many requests")
}

/// Collects the pool ids whose depth evidence should be refreshed.
///
/// Explicit `--pool-id` values win over everything else. Otherwise the
/// scope selects the population: the candidate tables by default (the
/// population the arbitrage pipeline consumes) or every observed snapshot
/// with `DepthScope::All`.
fn collect_candidate_pool_ids(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    requested: &[String],
    scope: DepthScope,
) -> Result<ResolvedPoolIds, String> {
    if !requested.is_empty() {
        let mut ids: Vec<String> = requested
            .iter()
            .map(|value| value.trim().to_lowercase())
            .filter(|value| !value.is_empty())
            .collect();
        ids.sort();
        ids.dedup();
        // An explicitly named pool is still filtered: a dynamic-fee pool is
        // never priced off chain, so naming it must not make the tool
        // process it.
        let excluded = exclude_dynamic_fee_pools(connection, network, chain_id, &ids);
        ids.retain(|pool_id| !excluded.contains(pool_id));
        let pool_count = ids.len();
        return Ok(ResolvedPoolIds {
            pool_ids: ids,
            pool_count,
            skipped_resume: 0,
            source_tables: vec!["explicit_pool_id".to_string()],
        });
    }

    let (sql, source_tables): (&str, Vec<String>) = match scope {
        DepthScope::Candidate => (
            "SELECT pool_id FROM dex_candidate_pools WHERE network = ? AND chain_id = ? \
             UNION SELECT pool_id FROM manager_dex_candidate_pools WHERE network = ? AND chain_id = ?",
            vec![
                "dex_candidate_pools".to_string(),
                "manager_dex_candidate_pools".to_string(),
            ],
        ),
        DepthScope::All => (
            "SELECT DISTINCT pool_id FROM v4_exchange_pool_liquidity_snapshots \
             WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
            vec!["v4_exchange_pool_liquidity_snapshots".to_string()],
        ),
    };

    let mut ids = Vec::new();
    let mut statement = connection.prepare(sql).map_err(|error| error.to_string())?;
    let params: Vec<String> = if scope == DepthScope::Candidate {
        vec![
            network.to_string(),
            chain_id.to_string(),
            network.to_string(),
            chain_id.to_string(),
        ]
    } else {
        vec![network.to_string(), chain_id.to_string()]
    };
    let rows = statement
        .query_map(params_from_iter(params), |row| row.get::<_, String>(0))
        .map_err(|error| error.to_string())?;
    for row in rows {
        let value = row.map_err(|error| error.to_string())?;
        let normalized = value.trim().to_lowercase();
        if !normalized.is_empty() {
            ids.push(normalized);
        }
    }
    ids.sort();
    ids.dedup();
    // The `All` scope covers every observed snapshot, which includes the
    // dynamic-fee pools. Those pools are excluded here, at the single
    // place where the population is resolved, so no later stage has to
    // remember the rule.
    let excluded = exclude_dynamic_fee_pools(connection, network, chain_id, &ids);
    ids.retain(|pool_id| !excluded.contains(pool_id));
    let pool_count = ids.len();
    Ok(ResolvedPoolIds {
        pool_ids: ids,
        pool_count,
        skipped_resume: 0,
        source_tables,
    })
}

/// Returns the subset of `pool_ids` that charges a dynamic fee.
///
/// A dynamic-fee pool's effective rate is decided by its hook at swap time,
/// so the off-chain depth arithmetic cannot price it: the stored rate and
/// the rate the chain will use are not the same number. Such a pool is
/// therefore never measured, never admitted and never traded, no matter how
/// it was selected.
fn exclude_dynamic_fee_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> std::collections::HashSet<String> {
    let mut excluded = std::collections::HashSet::new();
    if pool_ids.is_empty() {
        return excluded;
    }
    let sql = "SELECT pool_id FROM v4_exchange_pools \
               WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4' \
                 AND (dynamic_fee <> 0 OR (fee & ?) <> 0)";
    let Ok(mut statement) = connection.prepare(sql) else {
        return excluded;
    };
    let Ok(rows) = statement.query_map(
        params_from_iter([
            network.to_string(),
            chain_id.to_string(),
            i64::from(manager_contracts::V4_DYNAMIC_FEE_FLAG).to_string(),
        ]),
        |row| row.get::<_, String>(0),
    ) else {
        return excluded;
    };
    for row in rows.flatten() {
        let normalized = row.trim().to_lowercase();
        if pool_ids.iter().any(|pool_id| pool_id == &normalized) {
            excluded.insert(normalized);
        }
    }
    excluded
}

/// Returns the subset of `pool_ids` whose snapshot already carries a
/// `liquidity_read_at` timestamp.
///
/// A pool without a snapshot row is always refreshed: the missing row is
/// exactly the state the refresh has to repair.
fn filter_already_refreshed(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> Result<std::collections::HashSet<String>, String> {
    let mut fresh = std::collections::HashSet::new();
    let mut statement = connection
        .prepare(
            "SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots \
             WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4' \
             AND liquidity_read_at IS NOT NULL AND liquidity_read_at <> ''",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map(
            params_from_iter([network.to_string(), chain_id.to_string()]),
            |row| row.get::<_, String>(0),
        )
        .map_err(|error| error.to_string())?;
    for row in rows {
        let value = row.map_err(|error| error.to_string())?;
        let normalized = value.trim().to_lowercase();
        if pool_ids.iter().any(|pool_id| pool_id == &normalized) {
            fresh.insert(normalized);
        }
    }
    Ok(fresh)
}

/// Writes a refreshed depth observation into the snapshot table.
/// Converts a raw token amount into its human-readable form.
///
/// The sizing path (`dex_quote_evidence_size.py::_pool_borrow_side_amount`)
/// reads `balances["token0"]` / `balances["token1"]` as **human** amounts and
/// converts them back to raw units with the token decimals. Writing raw wei
/// there would inflate a 6-decimal stablecoin by a factor of 10^12 and make
/// the liquidity cap meaningless in the other direction.
fn human_amount(raw: u128, decimals: Option<i64>) -> Option<String> {
    let decimals = decimals?;
    if !(0..=77).contains(&decimals) {
        return None;
    }
    let divisor = 10f64.powi(decimals as i32);
    if !(divisor.is_finite() && divisor > 0.0) {
        return None;
    }
    let value = (raw as f64) / divisor;
    if !value.is_finite() {
        return None;
    }
    Some(format!("{value:.18}"))
}

/// D5: propagate the measured capacity payload from the snapshot table into
/// the candidate table.
///
/// The sizing path reads `dex_candidate_pools.balances_json`
/// (`core/db/builders.py:688` and `:823`), NOT the snapshot table. `depth`
/// only ever wrote the snapshot table, so after a successful `depth --apply`
/// the candidate rows still carried an empty `{}` payload and
/// `_liquidity_cap_raw()` kept returning `None` for every candidate pool.
///
/// The consequence was that the D4 capacity-aware ladder start could never
/// take effect: it kept falling back to `fallback_one_token_unmeasured_capacity`,
/// and the D4 economic floor never saw a measured capacity.
///
/// The propagation copies the snapshot payload verbatim, so the candidate row
/// and the snapshot row carry exactly the same token-denominated amounts. A
/// candidate row with no matching snapshot is left untouched: an absent
/// measurement must stay absent rather than become a fabricated zero.
///
/// The **depth verdict** travels with the payload. A candidate row that
/// keeps the verdict of an earlier measurement while the snapshot carries
/// the fresh one would make the candidate layer report a pool as shallow
/// that the chain has just measured as deep, and the repair cascade would
/// then keep looking for a replacement for a pool that is already good.
fn propagate_balances_to_candidate_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> Result<usize, String> {
    if pool_ids.is_empty() {
        return Ok(0);
    }
    let mut updated = 0usize;
    // A store that was migrated before the depth verdict lived on the
    // candidate row has no such column. The propagation then still
    // carries the measured payload, and the verdict is simply not
    // propagated: an absent column is a coverage gap, not a reason to
    // fail the whole measurement.
    let verdict_column = if table_has_column(connection, "dex_candidate_pools", "depth_status")
        && table_has_column(
            connection,
            "v4_exchange_pool_liquidity_snapshots",
            "depth_status",
        )
    {
        ", depth_status = ( \
             SELECT s3.depth_status FROM v4_exchange_pool_liquidity_snapshots s3 \
              WHERE s3.network = ?1 AND s3.chain_id = ?2 \
                AND s3.protocol = dex_candidate_pools.protocol \
                AND s3.pool_id = dex_candidate_pools.pool_id \
                AND s3.depth_status IS NOT NULL \
                AND TRIM(s3.depth_status) != '' \
         )"
    } else {
        ""
    };
    // CORE-R34-05: the candidate row's own `has_liquidity` flag used to
    // keep whatever an earlier writer had put there. A refresh that measured
    // the pool as empty therefore left the candidate row claiming that it
    // had liquidity, and the admission verdict — which reads the candidate
    // row — accepted a pool the chain had just reported as empty.
    //
    // The measured flag travels with the measured payload, so the two
    // surfaces can never disagree about whether the pool holds anything.
    // A store without the column keeps its own flag: an absent column is a
    // coverage gap, not a reason to fail the measurement.
    let liquidity_column = if table_has_column(connection, "dex_candidate_pools", "has_liquidity")
        && table_has_column(
            connection,
            "v4_exchange_pool_liquidity_snapshots",
            "has_liquidity",
        )
    {
        ", has_liquidity = ( \
             SELECT s3.has_liquidity FROM v4_exchange_pool_liquidity_snapshots s3 \
              WHERE s3.network = ?1 AND s3.chain_id = ?2 \
                AND s3.protocol = dex_candidate_pools.protocol \
                AND s3.pool_id = dex_candidate_pools.pool_id \
         )"
    } else {
        ""
    };
    let mut statement = connection
        .prepare(&format!(
            "UPDATE dex_candidate_pools SET balances_json = ( \
                 SELECT s.balances_json FROM v4_exchange_pool_liquidity_snapshots s \
                  WHERE s.network = ?1 AND s.chain_id = ?2 \
                    AND s.protocol = dex_candidate_pools.protocol \
                    AND s.pool_id = dex_candidate_pools.pool_id \
                    AND s.balances_json IS NOT NULL \
                    AND TRIM(s.balances_json) NOT IN ('', '{{}}') \
             ){verdict_column}{liquidity_column}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') \
             WHERE network = ?3 AND chain_id = ?4 \
               AND pool_id = ?5 \
               AND EXISTS ( \
                 SELECT 1 FROM v4_exchange_pool_liquidity_snapshots s2 \
                  WHERE s2.network = ?1 AND s2.chain_id = ?2 \
                    AND s2.protocol = dex_candidate_pools.protocol \
                    AND s2.pool_id = dex_candidate_pools.pool_id \
                    AND s2.balances_json IS NOT NULL \
                    AND TRIM(s2.balances_json) NOT IN ('', '{{}}') \
               )",
            verdict_column = verdict_column,
            liquidity_column = liquidity_column,
        ))
        .map_err(|error| error.to_string())?;
    for pool_id in pool_ids {
        updated += statement
            .execute(rusqlite::params![
                network, chain_id, network, chain_id, pool_id,
            ])
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(updated)
}

/// Whether a table carries a column.
fn table_has_column(connection: &Connection, table: &str, column: &str) -> bool {
    let mut statement = match connection.prepare(&format!("PRAGMA table_info({table})")) {
        Ok(statement) => statement,
        Err(_) => return false,
    };
    let rows = statement.query_map([], |row| row.get::<_, String>(1));
    match rows {
        Ok(rows) => rows.flatten().any(|name| name == column),
        Err(_) => false,
    }
}

fn write_depth_snapshot(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    state: &chain::V4PoolState,
    capacity: &depth::Capacity,
    binding_capacity: Option<f64>,
    token0_decimals: Option<i64>,
    token1_decimals: Option<i64>,
) -> Result<(), String> {
    let now = chrono::Utc::now().to_rfc3339();
    // The `balances` payload is the bridge between the chain read and the
    // sizing path. It was previously written as `{}`, which made
    // `_liquidity_cap_raw()` return `None` for every pool, which in turn
    // pinned `maxProbeAmountRaw` to the fixed 64-token fallback and stopped
    // the best-amount ladder from ever expanding.
    //
    // `token0` / `token1` are the per-side capacity expressed in the token's
    // own units. They are what the sizing path needs to size a position
    // against the depth that is actually available.
    let balances_json = json!({
        "token0": human_amount(capacity.capacity_token0_raw, token0_decimals),
        "token1": human_amount(capacity.capacity_token1_raw, token1_decimals),
        "capacityToken0": human_amount(capacity.capacity_token0_raw, token0_decimals),
        "capacityToken1": human_amount(capacity.capacity_token1_raw, token1_decimals),
        "token0Decimals": token0_decimals,
        "token1Decimals": token1_decimals,
        "capacityToken0Raw": capacity.capacity_token0_raw.to_string(),
        "capacityToken1Raw": capacity.capacity_token1_raw.to_string(),
        "capacityRaw": capacity.best_raw().to_string(),
        "capacityBindingRaw": binding_capacity,
        "singleTickMaxToken0Raw": capacity.single_tick_max_token0_raw.map(|v| v.to_string()),
        "singleTickMaxToken1Raw": capacity.single_tick_max_token1_raw.map(|v| v.to_string()),
        "priceImpactBps": capacity.price_impact_bps,
        "priceImpactLimitBps": depth::PRICE_IMPACT_LIMIT_BPS,
        "depthBasis": depth::DEPTH_BASIS,
        "evidenceSource": "pool_manager_extsload_live_state_batch_rust",
    })
    .to_string();
    // The retired USD valuation column is cleared on every write, but
    // only when the store actually has it: a store created before the
    // column existed must still accept the write.
    let estimated_tvl_assignment =
        if snapshot_has_column(connection, "estimated_tvl_usd")? {
            ", estimated_tvl_usd = NULL"
        } else {
            ""
        };
    // The depth verdict of a snapshot row is the band derived from the
    // measured binding-side capacity. It is the same basis the candidate
    // surface uses, so both surfaces publish one admission decision.
    let snapshot_depth_status = depth_band_for_binding_capacity(binding_capacity);
    connection
        .execute(
            // ``estimated_tvl_usd`` is cleared on every write. The USD
            // valuation is a reference-only observation: the shared depth
            // policy lists ``depth_admission`` and ``capacity_admission``
            // under ``usdgPegPolicy.forbiddenUses``, and the pool manager no
            // longer maintains a USD valuation. Leaving the column untouched
            // kept whatever value the retired TVL valuation path had written,
            // so a stale number (values as large as 4.3e22 were observed)
            // survived every refresh and was still read back by consumers
            // that treated it as a live depth reading.
            &format!(
                "UPDATE v4_exchange_pool_liquidity_snapshots SET \
                liquidity = ?1, has_liquidity = ?2, tick = ?3, sqrt_price_x96 = ?4, \
                capacity_token0_raw = ?5, capacity_token1_raw = ?6, capacity_raw = ?7, \
                price_impact_bps = ?8, price_impact_limit_bps = ?9, depth_basis = ?10, \
                liquidity_read_at = ?11, refreshed_at = ?11, evidence_source = ?12, \
                capacity_binding_raw = ?13, depth_band_json = ?14, balances_json = ?15, \
                single_tick_max_token0_raw = ?16, single_tick_max_token1_raw = ?17, \
                depth_status = ?18{} \
             WHERE network = ?19 AND chain_id = ?20 AND protocol = 'uniswap_v4' \
               AND pool_id = ?21",
                estimated_tvl_assignment
            ),
            rusqlite::params![
                state.liquidity.to_string(),
                if state.has_liquidity { 1i64 } else { 0i64 },
                state.tick,
                state.sqrt_price_x96,
                capacity.capacity_token0_raw.to_string(),
                capacity.capacity_token1_raw.to_string(),
                capacity.best_raw().to_string(),
                capacity.price_impact_bps,
                depth::PRICE_IMPACT_LIMIT_BPS,
                depth::DEPTH_BASIS,
                now,
                "pool_manager_extsload_live_state_batch_rust",
                binding_capacity,
                // The chain scoped snapshot is the only row every consumer of
                // a physical pool can reach, so it has to carry the shared
                // admission verdict as well. Without it a pool that is absent
                // from an anchor scoped surface has no verdict at all and
                // every consumer reads it as an unresolved coverage gap.
                depth_band_json_with_admission(
                    snapshot_depth_status,
                    None,
                    Some(capacity.best_raw() as f64),
                    binding_capacity,
                    None,
                    Some(state.has_liquidity),
                ),
                balances_json,
                // The tick-range bounds are published as raw amounts, so the
                // closed-loop layer can size a position without recomputing
                // the range.
                capacity
                    .single_tick_max_token0_raw
                    .map(|value| value.to_string()),
                capacity
                    .single_tick_max_token1_raw
                    .map(|value| value.to_string()),
                // CORE-R34-03: the measured band is also the row's own
                // `depth_status`. The column used to keep whatever a
                // historical writer had put there, so a refresh could leave
                // a pool marked `deep` next to a live measurement of zero
                // liquidity. The status is part of the measurement, so the
                // measurement writes it.
                snapshot_depth_status,
                network,
                chain_id,
                state.pool_id,
            ],
        )
        .map_err(|error| error.to_string())?;
    Ok(())
}

pub fn refresh_key(
    store: &str,
    chain_id: i64,
    key_id: &str,
    pool_group: &str,
    limit: usize,
    apply: bool,
) -> Result<Value, String> {
    let key_id = key_id.trim();
    let (base, quote) = key_id
        .split_once(':')
        .ok_or_else(|| format!("key_id must use <tokenX>:<tokenY>, got {key_id:?}"))?;
    if !is_valid_token_address(base) || !is_valid_token_address(quote) {
        return Err(format!(
            "key_id contains an invalid token address: {key_id}"
        ));
    }
    if base.trim().to_lowercase() == quote.trim().to_lowercase() {
        return Err("key_id must contain two distinct token addresses".to_string());
    }
    let mut payload = refresh_pair(store, chain_id, base, quote, pool_group, limit, apply)?;
    let eligible_count = payload
        .get("eligiblePoolCount")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if let Some(object) = payload.as_object_mut() {
        object.insert("keyId".to_string(), json!(canonical_pair_key(base, quote)));
        object.insert("ready".to_string(), json!(eligible_count > 0));
    }
    Ok(payload)
}

pub fn add_pair(
    store: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    pool_group: &str,
    limit: usize,
    apply: bool,
) -> Result<Value, String> {
    let network = network_for_chain_id(chain_id);
    let connection = if apply {
        Connection::open(store).map_err(|error| error.to_string())?
    } else {
        Connection::open_with_flags(
            store,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .map_err(|error| error.to_string())?
    };
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    let (pools, pool_ids) =
        eligible_raw_pools(&connection, &network, chain_id, base, quote, limit.max(1))?;

    let mut inserted = 0;
    if apply {
        inserted = upsert_eligible_pool_rows(
            &connection,
            &network,
            chain_id,
            base,
            quote,
            pool_group,
            &pools,
        )?;
    }

    Ok(json!({
        "network": network,
        "chainId": chain_id,
        "base": base.to_lowercase(),
        "quote": quote.to_lowercase(),
        "limit": limit.max(1),
        "apply": apply,
        "eligiblePoolCount": pools.len(),
        "eligiblePoolIds": pool_ids,
        "inserted": inserted,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;
    use tempfile::NamedTempFile;

    /// CORE-R34-02: a pool whose live read was only half successful must
    /// never replace a stored measurement.
    ///
    /// The batch reader reads the `slot0` and the liquidity of a pool. When
    /// one of the two failed, the state carries the default of the missing
    /// half, so a zero price and a zero liquidity. Writing that state turns a
    /// pool that is in fact full into an empty pool, and the shared
    /// admission verdict then rejects it.
    ///
    /// The regression is chain agnostic: it states the rule of the reader,
    /// not the state of any chain.
    #[test]
    fn an_incomplete_live_read_is_never_written_as_a_measurement() {
        let failed_half = chain::V4PoolState {
            pool_id: "0x01".to_string(),
            sqrt_price_x96: String::new(),
            liquidity: 0,
            has_liquidity: false,
            complete: false,
            error: Some("0x01:liquidity:rate limited".to_string()),
            ..Default::default()
        };
        assert!(
            !failed_half.complete,
            "a pool with a failed half read is not a measurement"
        );
        // The default of the missing half is what made the defect dangerous:
        // it looks like a real, empty pool.
        assert_eq!(failed_half.liquidity, 0);
        assert!(!failed_half.has_liquidity);
        // The reader must therefore refuse to write it.
        assert!(
            !depth_state_is_writable(&failed_half),
            "an incomplete read must not be written"
        );
    }

    /// CORE-R34-03: a refresh has to write the row's own `depth_status`.
    ///
    /// The refresh used to update the liquidity, the price, the capacity and
    /// the depth document, but it left the `depth_status` column exactly as
    /// the last historical writer had put it. A pool that was measured as
    /// empty therefore kept the `deep` label of an older measurement, and
    /// the two columns of the same row contradicted each other.
    ///
    /// The regression is chain agnostic: it states the rule of the writer,
    /// not the state of any chain.
    #[test]
    fn a_refresh_writes_the_measured_depth_status_next_to_the_measurement() {
        let connection = Connection::open_in_memory().expect("in-memory database");
        connection
            .execute(
                "CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    has_liquidity INTEGER, liquidity TEXT, tick INTEGER,
                    sqrt_price_x96 TEXT, capacity_token0_raw TEXT,
                    capacity_token1_raw TEXT, capacity_raw TEXT,
                    price_impact_bps REAL, price_impact_limit_bps REAL,
                    depth_basis TEXT, liquidity_read_at TEXT, refreshed_at TEXT,
                    evidence_source TEXT, capacity_binding_raw REAL,
                    depth_band_json TEXT, balances_json TEXT,
                    single_tick_max_token0_raw TEXT, single_tick_max_token1_raw TEXT,
                    depth_status TEXT
                )",
                [],
            )
            .expect("snapshot table");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, has_liquidity, liquidity,
                    depth_status
                ) VALUES ('robinhood', 4663, 'uniswap_v4', '0xpool', 1, '42', 'deep')",
                [],
            )
            .expect("seed row");
        let state = chain::V4PoolState {
            pool_id: "0xpool".to_string(),
            sqrt_price_x96: "7".to_string(),
            tick: 3,
            liquidity: 0,
            has_liquidity: false,
            complete: true,
            ..Default::default()
        };
        let capacity = depth::Capacity {
            has_liquidity: false,
            ..Default::default()
        };
        write_depth_snapshot(
            &connection,
            "robinhood",
            4663,
            &state,
            &capacity,
            Some(0.0),
            Some(18),
            Some(18),
        )
        .expect("write snapshot");
        let row: (i64, String, Option<String>) = connection
            .query_row(
                "SELECT has_liquidity, liquidity, depth_status
                 FROM v4_exchange_pool_liquidity_snapshots WHERE pool_id = '0xpool'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .expect("read back");
        assert_eq!(row.0, 0, "the measured liquidity flag is written");
        assert_eq!(row.1, "0", "the measured liquidity is written");
        assert_eq!(
            row.2.as_deref(),
            Some("dust"),
            "the depth status must follow the measurement, not the history"
        );
    }

    /// CORE-R34-05: the candidate surface must carry the measured liquidity
    /// flag together with the measured payload.
    ///
    /// The propagation copied the balances and the verdict, but the
    /// candidate row's own `has_liquidity` flag kept its historical value.
    /// A candidate row could therefore claim `has_liquidity = 1` while the
    /// snapshot it was copied from said the pool was empty, and the
    /// admission verdict — which reads the candidate row — admitted a pool
    /// the chain had just reported as empty.
    ///
    /// The regression is chain agnostic: it states the rule of the
    /// propagation, not the state of any chain.
    #[test]
    fn the_candidate_surface_carries_the_measured_liquidity_flag() {
        let connection = Connection::open_in_memory().expect("in-memory database");
        connection
            .execute(
                "CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    has_liquidity INTEGER, liquidity TEXT, depth_status TEXT,
                    balances_json TEXT
                )",
                [],
            )
            .expect("snapshot table");
        connection
            .execute(
                "CREATE TABLE dex_candidate_pools (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    has_liquidity INTEGER, depth_status TEXT, balances_json TEXT,
                    updated_at TEXT
                )",
                [],
            )
            .expect("candidate table");
        connection
            .execute(
                r#"INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, has_liquidity, liquidity,
                    depth_status, balances_json
                ) VALUES ('robinhood', 4663, 'uniswap_v4', '0xpool', 0, '0', 'dust',
                          '{"token0":"0","token1":"0","capacityRaw":"0"}')"#,
                [],
            )
            .expect("snapshot row");
        connection
            .execute(
                "INSERT INTO dex_candidate_pools (
                    network, chain_id, protocol, pool_id, has_liquidity,
                    depth_status, balances_json
                ) VALUES ('robinhood', 4663, 'uniswap_v4', '0xpool', 1, 'deep', '{}')",
                [],
            )
            .expect("candidate row");
        let updated = propagate_balances_to_candidate_pools(
            &connection,
            "robinhood",
            4663,
            &["0xpool".to_string()],
        )
        .expect("propagation");
        assert_eq!(updated, 1, "the candidate row is updated");
        let row: (i64, Option<String>) = connection
            .query_row(
                "SELECT has_liquidity, depth_status
                 FROM dex_candidate_pools WHERE pool_id = '0xpool'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .expect("read back");
        assert_eq!(
            row.0, 0,
            "the candidate row must not claim liquidity the snapshot denies"
        );
        assert_eq!(
            row.1.as_deref(),
            Some("dust"),
            "the candidate row must carry the measured verdict"
        );
    }
    /// The counterpart of the rule above: a pool whose two reads both
    /// succeeded is a measurement and is written.
    #[test]
    fn a_complete_live_read_is_written_as_a_measurement() {
        let measured = chain::V4PoolState {
            pool_id: "0x02".to_string(),
            sqrt_price_x96: "12345".to_string(),
            liquidity: 42,
            has_liquidity: true,
            complete: true,
            ..Default::default()
        };
        assert!(depth_state_is_writable(&measured));
    }

    /// CORE-R34-01: the depth reader has to resolve the token pair of a
    /// **chain scoped** pool, not only of a pool that some anchor happens to
    /// recommend.
    ///
    /// A pool that only exists in `v4_exchange_pools` has no row in the
    /// candidate tables. When the reader ignored the chain scoped table the
    /// pool had no token pair, so no decimals could be attached to either
    /// side of its capacity, so the binding side capacity stayed unmeasured
    /// and the shared admission verdict became `unavailable` and rejected a
    /// pool that had just been measured.
    ///
    /// The regression is chain agnostic: it uses the table layout and not a
    /// chain id, so it protects every chain the same way.
    #[test]
    fn a_chain_scoped_pool_resolves_its_token_pair_without_a_candidate_row() {
        let connection = Connection::open_in_memory().expect("in-memory database");
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pools (
                     network TEXT NOT NULL,
                     chain_id INTEGER NOT NULL,
                     pool_id TEXT NOT NULL,
                     token0 TEXT,
                     token1 TEXT
                 );
                 CREATE TABLE manager_dex_candidate_pools (
                     network TEXT NOT NULL,
                     chain_id INTEGER NOT NULL,
                     pool_id TEXT NOT NULL,
                     token0 TEXT,
                     token1 TEXT
                 );",
            )
            .expect("schema");
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (network, chain_id, pool_id, token0, token1)
                 VALUES ('scope', 1, '0xpool', '0xTokenZero', '0xTokenOne')",
                [],
            )
            .expect("chain scoped pool");
        let tokens = read_pool_tokens(&connection, "scope", 1).expect("pool tokens");
        assert_eq!(
            tokens.get("0xpool"),
            Some(&("0xtokenzero".to_owned(), "0xtokenone".to_owned())),
            "a chain scoped pool must resolve its token pair"
        );
    }

    /// The chain scoped pool table wins over the derived candidate surfaces,
    /// because it is the physical truth and the candidate tables are views.
    #[test]
    fn the_chain_scoped_pool_table_wins_over_the_candidate_surfaces() {
        let connection = Connection::open_in_memory().expect("in-memory database");
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pools (
                     network TEXT NOT NULL,
                     chain_id INTEGER NOT NULL,
                     pool_id TEXT NOT NULL,
                     token0 TEXT,
                     token1 TEXT
                 );
                 CREATE TABLE manager_dex_candidate_pools (
                     network TEXT NOT NULL,
                     chain_id INTEGER NOT NULL,
                     pool_id TEXT NOT NULL,
                     token0 TEXT,
                     token1 TEXT
                 );",
            )
            .expect("schema");
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (network, chain_id, pool_id, token0, token1)
                 VALUES ('scope', 1, '0xpool', '0xPhysicalZero', '0xPhysicalOne')",
                [],
            )
            .expect("chain scoped pool");
        connection
            .execute(
                "INSERT INTO manager_dex_candidate_pools (network, chain_id, pool_id, token0, token1)
                 VALUES ('scope', 1, '0xpool', '0xViewZero', '0xViewOne')",
                [],
            )
            .expect("candidate pool");
        let tokens = read_pool_tokens(&connection, "scope", 1).expect("pool tokens");
        assert_eq!(
            tokens.get("0xpool"),
            Some(&("0xphysicalzero".to_owned(), "0xphysicalone".to_owned())),
            "the chain scoped pool table is the physical truth"
        );
    }

    /// A USD valuation outside the reference band is a **diagnostic**, never
    /// a gate.  The shared depth policy lists ``depth_admission`` and
    /// ``capacity_admission`` under ``usdgPegPolicy.forbiddenUses``, and the
    /// operating manual forbids dropping a token pair on a TVL conclusion.
    ///
    /// The reasons used to carry the TVL observation, and because every
    /// non-empty reason list marks a pool as ineligible, a pool whose USD
    /// reading was merely below the band was dropped from the candidate set
    /// even though its on-chain capacity had been measured and was adequate.
    #[test]
    fn a_usd_reading_outside_the_reference_band_never_disqualifies_a_pool() {
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let pool = PoolQuality {
            fee: 3000,
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(ZERO_ADDRESS),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(1000.0),
            depth_status: Some("medium"),
            // Far below the reference band, but the on-chain capacity is
            // measured and adequate.
            estimated_tvl_usd: Some(173.89),
            capacity_raw: Some(5_000.0),
            capacity_evidence_available: true,
        };
        assert!(
            quality_reasons(PoolQuality {
                fee: pool.fee,
                dynamic_fee: pool.dynamic_fee,
                special_fee: pool.special_fee,
                hooks: pool.hooks,
                token0: pool.token0,
                token1: pool.token1,
                has_liquidity: pool.has_liquidity,
                liquidity_index: pool.liquidity_index,
                depth_status: pool.depth_status,
                estimated_tvl_usd: pool.estimated_tvl_usd,
                capacity_raw: pool.capacity_raw,
                capacity_evidence_available: pool.capacity_evidence_available,
            })
            .is_empty(),
            "a USD reading outside the reference band is not a disqualifier"
        );
        assert_eq!(
            reference_only_observations(pool.estimated_tvl_usd),
            vec!["tvl_below_reference_band".to_string()],
            "the observation is still surfaced"
        );

        // A reading above the ceiling is a malformed reading.  It is
        // surfaced the same way and disqualifies nothing on its own.
        assert_eq!(
            reference_only_observations(Some(2_000_000_000.0)),
            vec!["tvl_out_of_range_bad_reading".to_string()]
        );
    }

    #[test]
    fn quality_ignores_usd_depth_labels_but_requires_onchain_liquidity() {
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let reasons = quality_reasons(PoolQuality {
            fee: 3000,
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(ZERO_ADDRESS),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(1000.0),
            depth_status: Some("liquid"),
            estimated_tvl_usd: Some(250_000.0),
            capacity_raw: Some(5_000.0),
            capacity_evidence_available: true,
        });
        assert!(reasons.is_empty());
        assert_eq!(
            quality_reasons(PoolQuality {
                fee: 3000,
                dynamic_fee: 0,
                special_fee: 0,
                hooks: Some(ZERO_ADDRESS),
                token0: Some(&token0),
                token1: Some(&token1),
                has_liquidity: 1,
                liquidity_index: Some(1000.0),
                depth_status: Some("liquid"),
                estimated_tvl_usd: Some(250_000.0),
                capacity_raw: Some(5_000.0),
            capacity_evidence_available: true,
            }),
            Vec::<String>::new()
        );
        assert!(quality_reasons(PoolQuality {
            fee: 3000,
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(ZERO_ADDRESS),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(0.0),
            depth_status: Some("liquid"),
            estimated_tvl_usd: Some(250_000.0),
            capacity_raw: Some(5_000.0),
            capacity_evidence_available: true,
        })
        .iter()
        .any(|reason| reason == "missing_positive_liquidity"));
    }

    #[test]
    fn quality_rejects_pool_without_capacity_even_when_liquidity_index_is_large() {
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let reasons = quality_reasons(PoolQuality {
            fee: 3000,
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(ZERO_ADDRESS),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(1_000_000.0),
            depth_status: Some("thin"),
            estimated_tvl_usd: Some(7_955.0),
            capacity_raw: None,
            capacity_evidence_available: true,
        });
        assert!(reasons.iter().any(|reason| reason == "depth_thin"));
        assert!(reasons
            .iter()
            .any(|reason| reason == "missing_capacity_evidence"));
    }

    /// The name of this test states the intent the design demands: a stale
    /// or malformed USD reading is **surfaced** but it never disqualifies a
    /// pool whose on-chain capacity is measurable.
    ///
    /// The assertion used to require the TVL observation to appear in the
    /// **disqualifying** reason list, which contradicted the name and the
    /// shared depth policy: every non-empty reason list marks a pool as
    /// ineligible, so the observation did disqualify the pool.  The
    /// observation now lives in the reference-only channel, and the test
    /// asserts the real behaviour.
    #[test]
    fn quality_flags_stale_out_of_range_tvl_reading_without_disqualifying_the_pool() {
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let pool = PoolQuality {
            fee: 3000,
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(ZERO_ADDRESS),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(1_000.0),
            depth_status: Some("deep"),
            estimated_tvl_usd: Some(1e43),
            capacity_raw: Some(5_000.0),
            capacity_evidence_available: true,
        };
        // The malformed USD reading is surfaced, but it does not reject a
        // pool whose on-chain capacity is measurable.
        assert_eq!(
            reference_only_observations(pool.estimated_tvl_usd),
            vec!["tvl_out_of_range_bad_reading".to_string()]
        );
        assert!(quality_reasons(PoolQuality {
            fee: pool.fee,
            dynamic_fee: pool.dynamic_fee,
            special_fee: pool.special_fee,
            hooks: pool.hooks,
            token0: pool.token0,
            token1: pool.token1,
            has_liquidity: pool.has_liquidity,
            liquidity_index: pool.liquidity_index,
            depth_status: pool.depth_status,
            estimated_tvl_usd: pool.estimated_tvl_usd,
            capacity_raw: pool.capacity_raw,
            capacity_evidence_available: pool.capacity_evidence_available,
        })
        .is_empty());
    }

    #[test]
    fn depth_band_rank_orders_depth_labels() {
        assert_eq!(depth_band_rank(Some("deep")), Some(4));
        assert_eq!(depth_band_rank(Some("liquid")), Some(3));
        assert_eq!(depth_band_rank(Some("medium")), Some(2));
        assert_eq!(depth_band_rank(Some("thin")), Some(1));
        assert_eq!(depth_band_rank(Some("dust")), Some(0));
        assert_eq!(depth_band_rank(Some("unpriced")), None);
        assert_eq!(depth_band_rank(None), None);
    }

    /// The liquidity snapshot table is not guaranteed to carry the same
    /// columns on every chain: the mainnet store predates the capacity
    /// columns while the testnet store already has them. The audit and the
    /// admission query therefore have to work against both schemas, and
    /// they must not report a schema error instead of the pool verdict.
    #[test]
    fn the_audit_works_on_a_store_without_the_capacity_columns() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("legacy.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE dex_candidate_pools (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    fee INTEGER, dynamic_fee INTEGER, special_fee INTEGER,
                    hooks TEXT, token0 TEXT, token1 TEXT, depth_status TEXT,
                    has_liquidity INTEGER, liquidity_index REAL,
                    usable_for_observation INTEGER);
                 CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    has_liquidity INTEGER, liquidity_index REAL,
                    estimated_tvl_usd REAL, depth_status TEXT,
                    balances_json TEXT, evidence_source TEXT);
                 CREATE TABLE v4_exchange_pools (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    token0 TEXT, token1 TEXT, fee INTEGER, dynamic_fee INTEGER,
                    special_fee INTEGER, hooks TEXT);
                 CREATE TABLE dex_candidate_pool_recommendations (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    anchor_scope TEXT, anchor_symbol TEXT, address_pair TEXT,
                    direction_key TEXT, direction_address_pair TEXT,
                    recommendation_role TEXT, created_at TEXT, updated_at TEXT);
                 CREATE TABLE v4_closed_loops (
                    network TEXT, chain_id INTEGER, protocol TEXT,
                    market_id TEXT, loop_key TEXT, path_symbols_json TEXT,
                    pool_ids_json TEXT, anchor_symbol TEXT, status TEXT,
                    created_at TEXT, updated_at TEXT);
                 CREATE TABLE v4_closed_loop_hops (
                    network TEXT, chain_id INTEGER, protocol TEXT,
                    market_id TEXT, loop_key TEXT, hop_index INTEGER,
                    hop_name TEXT, input_address TEXT, output_address TEXT,
                    pool_id TEXT, base_pool_id TEXT, pool_selection_basis TEXT,
                    optimization_id TEXT, temporary_override INTEGER,
                    created_at TEXT, updated_at TEXT);
                 INSERT INTO dex_candidate_pools VALUES
                    ('robinhood', 4663, 'uniswap_v4', 'pool-a', 3000, 0, 0, '',
                     '0x1111111111111111111111111111111111111111',
                     '0x2222222222222222222222222222222222222222',
                     'deep', 1, 0.5, 1);
                 INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES
                    ('robinhood', 4663, 'uniswap_v4', 'pool-a', 1, 0.5, 250000.0,
                     'deep', '{}', 'test');
                 INSERT INTO v4_exchange_pools VALUES
                    ('robinhood', 4663, 'uniswap_v4', 'pool-a',
                     '0x1111111111111111111111111111111111111111',
                     '0x2222222222222222222222222222222222222222',
                     3000, 0, 0, '');",
            )
            .unwrap();

        let audit = audit_internal(&connection, 4663).unwrap();
        assert_eq!(audit["candidateTotal"], 1);
        assert_eq!(audit["ineligibleTotal"], 0);
        assert_eq!(audit["ineligiblePoolIds"].as_array().unwrap().len(), 0);

        let (pools, _ids) = eligible_raw_pools(
            &connection,
            "robinhood",
            4663,
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            50,
        )
        .unwrap();
        assert_eq!(pools.len(), 1, "a deep pool must stay eligible");
        assert_eq!(pools[0].pool_id, "pool-a");
    }

    /// The candidate row carries a **copy** of the depth verdict that is only
    /// refreshed when the pool is re-measured. A pool that was measured out
    /// of the adequate band therefore kept its old adequate verdict on the
    /// candidate row until the next refresh propagated it, and the audit read
    /// that stale copy: a pool whose measured depth was `thin` was admitted
    /// as `medium` and stayed in the candidate set forever.
    ///
    /// The measured verdict on the snapshot row is the authority.
    #[test]
    fn the_audit_uses_the_measured_depth_verdict_over_the_stale_candidate_copy() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("stale.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE dex_candidate_pools (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    fee INTEGER, dynamic_fee INTEGER, special_fee INTEGER,
                    hooks TEXT, token0 TEXT, token1 TEXT, depth_status TEXT,
                    has_liquidity INTEGER, liquidity_index REAL,
                    usable_for_observation INTEGER);
                 CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    has_liquidity INTEGER, liquidity_index REAL,
                    estimated_tvl_usd REAL, depth_status TEXT,
                    capacity_raw TEXT, balances_json TEXT, evidence_source TEXT);
                 CREATE TABLE v4_exchange_pools (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    token0 TEXT, token1 TEXT, fee INTEGER, dynamic_fee INTEGER,
                    special_fee INTEGER, hooks TEXT);
                 CREATE TABLE dex_candidate_pool_recommendations (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    anchor_scope TEXT, anchor_symbol TEXT, address_pair TEXT,
                    direction_key TEXT, direction_address_pair TEXT,
                    recommendation_role TEXT, created_at TEXT, updated_at TEXT);
                 CREATE TABLE v4_closed_loops (
                    network TEXT, chain_id INTEGER, protocol TEXT,
                    market_id TEXT, loop_key TEXT, path_symbols_json TEXT,
                    pool_ids_json TEXT, anchor_symbol TEXT, status TEXT,
                    created_at TEXT, updated_at TEXT);
                 CREATE TABLE v4_closed_loop_hops (
                    network TEXT, chain_id INTEGER, protocol TEXT,
                    market_id TEXT, loop_key TEXT, hop_index INTEGER,
                    hop_name TEXT, input_address TEXT, output_address TEXT,
                    pool_id TEXT, base_pool_id TEXT, pool_selection_basis TEXT,
                    optimization_id TEXT, temporary_override INTEGER,
                    created_at TEXT, updated_at TEXT);
                 -- The candidate row still claims the old, adequate verdict.
                 INSERT INTO dex_candidate_pools VALUES
                    ('robinhood', 4663, 'uniswap_v4', 'pool-a', 3000, 0, 0, '',
                     '0x1111111111111111111111111111111111111111',
                     '0x2222222222222222222222222222222222222222',
                     'medium', 1, 0.5, 1);
                 -- The measurement says the pool is thin.
                 INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES
                    ('robinhood', 4663, 'uniswap_v4', 'pool-a', 1, 0.5, 250000.0,
                     'thin', 0.011, '{}', 'test');
                 INSERT INTO v4_exchange_pools VALUES
                    ('robinhood', 4663, 'uniswap_v4', 'pool-a',
                     '0x1111111111111111111111111111111111111111',
                     '0x2222222222222222222222222222222222222222',
                     3000, 0, 0, '');",
            )
            .unwrap();

        let audit = audit_internal(&connection, 4663).unwrap();
        assert_eq!(audit["candidateTotal"], 1);
        assert_eq!(
            audit["ineligibleTotal"], 1,
            "the measured thin verdict disqualifies the pool"
        );
        let ineligible = audit["ineligiblePoolIds"].as_array().unwrap();
        assert_eq!(ineligible[0], "pool-a");
        let reasons = &audit["reasonsByPool"]["pool-a"]["reasons"];
        assert!(
            reasons
                .as_array()
                .unwrap()
                .iter()
                .any(|reason| reason == "depth_thin"),
            "the measured verdict is the reason, got {:?}",
            reasons
        );
    }

    #[test]
    fn eligible_raw_pools_admits_on_capacity_and_orders_by_capacity() {
        let file = NamedTempFile::new().expect("temp db");
        let path = file.path().to_path_buf();
        let connection = Connection::open(&path).expect("database");
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    token0 TEXT NOT NULL,
                    token1 TEXT NOT NULL,
                    fee INTEGER NOT NULL,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    hooks TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    depth_status TEXT NOT NULL,
                    liquidity_index REAL,
                    has_liquidity INTEGER NOT NULL,
                    estimated_tvl_usd REAL,
                    depth_score_usd REAL,
                    capacity_raw TEXT,
                    capacity_binding_raw REAL,
                    price_impact_bps REAL,
                    balances_json TEXT
                );
                "#,
            )
            .expect("schema");
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        // A pool with a small USD valuation but a large on-chain capacity. It
        // must be admitted: the USD reading never expresses tradable value.
        let deep = "0xdeep";
        // A pool with a large USD valuation but no measurable capacity. It
        // must be rejected: it cannot absorb a position.
        let thin = "0xthin";
        // A pool with a large USD valuation and a usable capacity. It must be
        // admitted, and ordered behind the pool with the larger capacity.
        let mid = "0xmid";
        for (pool_id, depth, tvl, capacity, binding) in [
            (deep, "deep", 7_955.0_f64, "5000000000", 5.0_f64),
            (thin, "deep", 900_000_000.0_f64, "0", 0.0_f64),
            (mid, "deep", 900_000.0_f64, "1000000000", 0.5_f64),
        ] {
            connection
                .execute(
                    "INSERT INTO v4_exchange_pools (
                        network, chain_id, protocol, pool_id, token0, token1,
                        fee, dynamic_fee, special_fee, hooks
                     ) VALUES ('robinhood', 4663, 'uniswap_v4', ?1, ?2, ?3, 3000, 0, 0, ?4)",
                    params![pool_id, token0, token1, ZERO_ADDRESS],
                )
                .expect("pool");
            connection
                .execute(
                    "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                        network, chain_id, protocol, pool_id, depth_status,
                        liquidity_index, has_liquidity, estimated_tvl_usd, depth_score_usd,
                        capacity_raw, capacity_binding_raw
                     ) VALUES ('robinhood', 4663, 'uniswap_v4', ?1, ?2, 1000.0, 1, ?3, ?3, ?4, ?5)",
                    params![pool_id, depth, tvl, capacity, binding],
                )
                .expect("snapshot");
        }
        let (pools, _) = eligible_raw_pools(&connection, "robinhood", 4663, &token0, &token1, 50)
            .expect("eligible pools");
        let ids: Vec<&str> = pools.iter().map(|pool| pool.pool_id.as_str()).collect();
        // Capacity decides. The low-TVL pool with capacity is kept, the
        // high-TVL pool without capacity is dropped, and the ordering follows
        // the capacity rather than the USD valuation.
        assert_eq!(ids, vec![deep, mid]);
        assert_eq!(pools[0].estimated_tvl_usd, Some(7_955.0));
        assert_eq!(pools[0].capacity_raw.as_deref(), Some("5000000000"));
    }

    #[test]
    fn depth_band_json_reflects_capacity_bands() {
        // DEP-4.2: the band is decided on the decimals-normalised
        // binding-side capacity, so the raw capacity no longer selects the
        // band on its own.
        assert!(depth_band_json_for_binding(
            "deep",
            Some(2_000_000.0),
            Some(50_000.0),
            Some(DEPTH_BAND_DEEP_CAPACITY)
        )
        .contains("\"deep\""));
        assert!(depth_band_json_for_binding(
            "liquid",
            Some(250_000.0),
            Some(5_000.0),
            Some(DEPTH_BAND_MEDIUM_CAPACITY)
        )
        .contains("\"medium\""));
        assert!(depth_band_json_for_binding(
            "thin",
            Some(20_000.0),
            Some(500.0),
            Some(DEPTH_BAND_SHALLOW_CAPACITY)
        )
        .contains("\"shallow\""));
        assert!(depth_band_json_for_binding(
            "dust",
            Some(100.0),
            Some(10.0),
            Some(DEPTH_BAND_SHALLOW_CAPACITY * 0.999)
        )
        .contains("\"dust\""));
        assert!(depth_band_json_for_binding("", None, None, None).contains("\"unavailable\""));
        assert!(depth_band_json_for_binding("deep", None, None, None).contains("\"observed\""));
    }

    /// The chain scoped liquidity snapshot is the only row every consumer of
    /// a physical pool can reach. The admission verdict has to travel with
    /// it, otherwise a pool that is missing from an anchor scoped surface
    /// reads as an unresolved coverage gap instead of a decision.
    #[test]
    fn the_depth_document_carries_the_shared_admission_verdict() {
        let document = depth_band_json_with_admission(
            "deep",
            None,
            Some(1.0),
            Some(DEPTH_BAND_DEEP_CAPACITY * 10.0),
            None,
            Some(true),
        );
        assert!(document.contains("\"kernelAdmitted\":true"));
        assert!(document.contains("\"kernelDepthBand\":\"deep\""));
        // The measured facts stay untouched next to the verdict.
        assert!(document.contains("\"capacityBindingRaw\""));
    }

    /// An explicit zero liquidity is a rejection, whatever the band says.
    #[test]
    fn the_depth_document_rejects_an_explicit_zero_liquidity() {
        let document = depth_band_json_with_admission(
            "deep",
            None,
            Some(1.0),
            Some(DEPTH_BAND_DEEP_CAPACITY * 10.0),
            None,
            Some(false),
        );
        assert!(document.contains("\"kernelAdmitted\":false"));
        assert!(document.contains("explicit_zero_liquidity"));
    }

    /// A never measured liquidity stays a coverage gap, not a rejection.
    #[test]
    fn the_depth_document_keeps_a_missing_liquidity_unresolved() {
        let document = depth_band_json_with_admission(
            "deep",
            None,
            Some(1.0),
            Some(DEPTH_BAND_DEEP_CAPACITY * 10.0),
            None,
            None,
        );
        assert!(document.contains("\"kernelAdmitted\":true"));
    }

    /// A capacity below the shared floor rejects the pool even when the
    /// measured label says `deep`.
    #[test]
    fn the_depth_document_rejects_a_capacity_below_the_shared_floor() {
        let document = depth_band_json_with_admission(
            "deep",
            None,
            Some(1.0),
            Some(MIN_CANDIDATE_BINDING_CAPACITY / 10.0),
            None,
            Some(true),
        );
        assert!(document.contains("\"kernelAdmitted\":false"));
        assert!(document.contains("binding_capacity_below_floor"));
    }

    #[test]
    fn a_pool_is_only_as_deep_as_its_weaker_side() {
        // The binding side is the smaller of the two one-sided capacities
        // after each side is converted into its own token units. A pool whose
        // weaker side cannot absorb anything is dust, even when the other
        // side reports a huge raw capacity.
        let binding = binding_side_capacity(Some(0), Some(1_000_000_000_000), Some(18), Some(6));
        assert_eq!(binding, Some(0.0));
        assert_eq!(depth_band_for_binding_capacity(binding), "dust");

        // The same raw maximum on both sides of a 6-decimal token is a
        // different quantity than the same raw maximum on an 18-decimal
        // token, which is exactly why the bands are normalised.
        let stable = binding_side_capacity(Some(1_000_000), Some(1_000_000), Some(6), Some(6));
        let token = binding_side_capacity(Some(1_000_000), Some(1_000_000), Some(18), Some(18));
        assert!(stable.unwrap() > token.unwrap());
    }

    #[test]
    fn depth_band_boundaries_match_the_python_pool_index() {
        // The Python pool index classifies with the same `>=` operator and the
        // same boundaries, so both sides must return the same band for the
        // exact boundary values.
        assert!(
            depth_band_json_for_binding("deep", None, None, Some(DEPTH_BAND_DEEP_CAPACITY))
                .contains("\"deep\"")
        );
        assert!(depth_band_json_for_binding(
            "medium",
            None,
            None,
            Some(DEPTH_BAND_MEDIUM_CAPACITY)
        )
        .contains("\"medium\""));
        assert!(depth_band_json_for_binding(
            "shallow",
            None,
            None,
            Some(DEPTH_BAND_SHALLOW_CAPACITY)
        )
        .contains("\"shallow\""));
        assert!(depth_band_json_for_binding(
            "dust",
            None,
            None,
            Some(DEPTH_BAND_SHALLOW_CAPACITY * 0.999)
        )
        .contains("\"dust\""));
    }

    #[test]
    fn the_depth_thresholds_have_a_single_source() {
        // The Rust toolchain used to declare the same thresholds in three
        // places: the depth module, the candidate admission and the scope
        // materialization. They could drift apart without any test noticing.
        // Every threshold now comes from `depth_config`, so the assertions
        // below are identities by construction.
        assert_eq!(
            depth::PRICE_IMPACT_LIMIT_BPS,
            depth_config::PRICE_IMPACT_LIMIT_BPS
        );
        assert_eq!(
            CANDIDATE_PRICE_IMPACT_LIMIT_BPS,
            depth_config::PRICE_IMPACT_LIMIT_BPS
        );
        assert_eq!(
            MIN_CANDIDATE_CAPACITY_RAW,
            depth_config::MIN_CANDIDATE_CAPACITY_RAW
        );
        assert_eq!(
            MAX_CANDIDATE_FIXED_FEE,
            depth_config::MAX_CANDIDATE_FIXED_FEE
        );
        assert_eq!(depth::DEPTH_BASIS, depth_config::DEPTH_BASIS);
    }

    #[test]
    fn candidate_admission_floor_matches_the_shared_shallow_band() {
        // DEP-4.2: the admission floor is the shallow band boundary on the
        // normalised binding-side scale. The raw floor stays a separate dust
        // guard, because a raw unit is not comparable across tokens with
        // different decimals.
        assert_eq!(MIN_CANDIDATE_BINDING_CAPACITY, DEPTH_BAND_SHALLOW_CAPACITY);
    }

    #[test]
    fn the_raw_capacity_floor_is_exact_beyond_the_integer_range() {
        // The capacity is a raw uint128 stored as TEXT. SQLite's INTEGER
        // cast saturates at i64::MAX, so every capacity above that limit used
        // to collapse onto one clamped value. The decimal comparison must
        // stay exact across the whole range.
        let floor = MIN_CANDIDATE_CAPACITY_RAW;
        assert!(!decimal_text_at_least(Some("0"), floor));
        assert!(!decimal_text_at_least(Some("99"), floor));
        assert!(decimal_text_at_least(Some("100"), floor));
        assert!(decimal_text_at_least(Some("0100"), floor));
        assert!(decimal_text_at_least(Some(" 100 "), floor));
        assert!(decimal_text_at_least(Some("+100"), floor));
        assert!(!decimal_text_at_least(None, floor));
        assert!(!decimal_text_at_least(Some(""), floor));
        assert!(!decimal_text_at_least(Some("abc"), floor));
        assert!(!decimal_text_at_least(Some("1.5"), floor));
        // A value above i64::MAX must be admitted, not clamped.
        assert!(decimal_text_at_least(Some("9223372036854775808"), floor));
        assert!(decimal_text_at_least(
            Some("340282366920938463463374607431768211455"),
            floor
        ));
        assert!(decimal_text_at_least(
            Some("3964406291552619510676191932674463"),
            floor
        ));
    }

    #[test]
    fn v4_pool_state_slots_match_the_python_keccak_layout() {
        // The slot layout is shared with the Python readers. The pool id is
        // hashed with `keccak(poolId ++ 0x00 * 31 ++ 0x06)` and `liquidity`
        // lives in the **fourth** word of that struct.
        let pool_id = "0x1111111111111111111111111111111111111111111111111111111111111111";
        let (state_slot, liquidity_slot) = chain::v4_pool_state_slots(pool_id).expect("slots");
        assert_ne!(state_slot, liquidity_slot);
        // `liquidity_slot` is `state_slot + 3`. The `+1` word is
        // `feeGrowthGlobal0X128`, which is a fee growth accumulator and not
        // a liquidity number.
        let mut expected = state_slot;
        let mut carry = 3u16;
        for byte in expected.iter_mut().rev() {
            let value = u16::from(*byte) + carry;
            *byte = value as u8;
            carry = value >> 8;
            if carry == 0 {
                break;
            }
        }
        assert_eq!(expected, liquidity_slot);
    }

    #[test]
    fn v4_pool_state_slots_reject_malformed_pool_ids() {
        assert!(chain::v4_pool_state_slots("0xdeadbeef").is_err());
        assert!(chain::v4_pool_state_slots("not-a-pool-id").is_err());
    }

    #[test]
    fn parse_v4_slot0_words_decodes_the_packed_layout() {
        // sqrtPriceX96 occupies the low 160 bits, followed by tick,
        // protocolFee and lpFee.
        let sqrt_price: u128 = 0x0102030405060708;
        let tick: i32 = -12345;
        let protocol_fee: u64 = 0x0102;
        let lp_fee: u64 = 0x0304;
        let mut word = [0u8; 32];
        // tick is a signed 24-bit value stored in bytes 9..12.
        let tick_bits = (tick as u32) & 0x00ff_ffff;
        word[9] = ((tick_bits >> 16) & 0xff) as u8;
        word[10] = ((tick_bits >> 8) & 0xff) as u8;
        word[11] = (tick_bits & 0xff) as u8;
        // protocolFee is bytes 6..9.
        word[6] = ((protocol_fee >> 16) & 0xff) as u8;
        word[7] = ((protocol_fee >> 8) & 0xff) as u8;
        word[8] = (protocol_fee & 0xff) as u8;
        // lpFee is bytes 3..6.
        word[3] = ((lp_fee >> 16) & 0xff) as u8;
        word[4] = ((lp_fee >> 8) & 0xff) as u8;
        word[5] = (lp_fee & 0xff) as u8;
        // `sqrtPriceX96` occupies the low 160 bits, i.e. the trailing 20
        // bytes of the word. A `u128` value is right-aligned inside that
        // field, so the four leading bytes of the field stay zero.
        word[12..16].copy_from_slice(&[0u8; 4]);
        word[16..32].copy_from_slice(&sqrt_price.to_be_bytes());
        let raw = format!("0x{}", hex::encode(word));
        let (parsed_sqrt, parsed_tick, parsed_protocol, parsed_lp) =
            chain::parse_v4_slot0_words(&raw);
        assert_eq!(parsed_sqrt.to_decimal_string(), sqrt_price.to_string());
        assert_eq!(parsed_tick, i64::from(tick));
        assert_eq!(parsed_protocol, protocol_fee);
        assert_eq!(parsed_lp, lp_fee);
    }

    #[test]
    fn parse_v4_liquidity_word_reads_the_low_128_bits() {
        let raw = format!("0x{}", "00".repeat(16));
        assert_eq!(chain::parse_v4_liquidity_word(&raw), 0);
        let raw = format!("0x{}", "ff".repeat(16));
        assert_eq!(chain::parse_v4_liquidity_word(&raw), u128::MAX);
    }

    #[test]
    fn capacity_scales_with_liquidity() {
        // Doubling the liquidity must never reduce the capacity.
        let sqrt_price = 2u128.pow(96);
        let small =
            depth::capacity_from_sqrt_price(sqrt_price, 1_000_000_000_000_000_000, 3000, 10.0)
                .expect("capacity");
        let large =
            depth::capacity_from_sqrt_price(sqrt_price, 2_000_000_000_000_000_000, 3000, 10.0)
                .expect("capacity");
        assert!(large.capacity_token0_raw > small.capacity_token0_raw);
        assert!(large.capacity_token1_raw > small.capacity_token1_raw);
        // The realised impact stays inside the configured limit.
        assert!(small.price_impact_bps <= 10.0);
        assert!(large.price_impact_bps <= 10.0);
    }

    #[test]
    fn capacity_respects_the_price_impact_limit() {
        let sqrt_price = 2u128.pow(96);
        let liquidity = 1_000_000_000_000_000_000u128;
        let tight =
            depth::capacity_from_sqrt_price(sqrt_price, liquidity, 3000, 1.0).expect("capacity");
        let loose =
            depth::capacity_from_sqrt_price(sqrt_price, liquidity, 3000, 100.0).expect("capacity");
        // A looser limit can only admit a larger position.
        assert!(loose.capacity_token0_raw > tight.capacity_token0_raw);
        assert!(loose.capacity_token1_raw > tight.capacity_token1_raw);
    }

    #[test]
    fn capacity_rejects_an_unusable_pool_state() {
        assert!(depth::capacity_from_sqrt_price(0, 1, 3000, 10.0).is_err());
        assert!(depth::capacity_from_sqrt_price(1, 0, 3000, 10.0).is_err());
    }

    #[test]
    fn sdk_and_fallback_maths_agree_on_the_swap_output() {
        // The two maths paths must produce the same output for the same
        // inputs, otherwise the capacity verdict would depend on which build
        // produced it.
        let sqrt_price = 2u128.pow(96);
        let liquidity = 1_000_000_000_000_000_000u128;
        for amount in [
            1_000_000u128,
            1_000_000_000_000u128,
            1_000_000_000_000_000u128,
        ] {
            for zero_for_one in [true, false] {
                let sdk = depth::price_impact_for_input_with_backend(
                    sqrt_price,
                    liquidity,
                    3000,
                    amount,
                    zero_for_one,
                    depth::MathBackend::Sdk,
                )
                .expect("sdk impact");
                let fallback = depth::price_impact_for_input_with_backend(
                    sqrt_price,
                    liquidity,
                    3000,
                    amount,
                    zero_for_one,
                    depth::MathBackend::Fallback,
                )
                .expect("fallback impact");
                let difference = (sdk - fallback).abs();
                assert!(
                    difference <= 1.0,
                    "sdk impact {sdk} and fallback impact {fallback} diverge"
                );
            }
        }
    }

    #[test]
    fn raw_to_decimal_string_renders_token_amounts() {
        assert_eq!(depth::raw_to_decimal_string(0, 18), "0");
        assert_eq!(
            depth::raw_to_decimal_string(1_000_000_000_000_000_000, 18),
            "1"
        );
        assert_eq!(
            depth::raw_to_decimal_string(1_500_000_000_000_000_000, 18),
            "1.5"
        );
        assert_eq!(depth::raw_to_decimal_string(1_000_000, 6), "1");
    }

    #[test]
    fn eligible_raw_pools_keeps_low_liquidity_index_pool_with_real_depth() {
        // The ETH/USDG pool with a $1.28M reserve and 24 live closed loops had
        // a liquidity index of 0.0285. Filtering on the index removed a
        // genuinely deep pool, so the index must no longer gate admission.
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        create_pool_tables(&connection);
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, token0, token1, fee,
                    dynamic_fee, special_fee, hooks
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'deep-low-index', ?1, ?2, 3000, 0, 0, ?3)",
                params![token0, token1, ZERO_ADDRESS],
            )
            .expect("pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, depth_status,
                    liquidity_index, has_liquidity, estimated_tvl_usd, depth_score_usd,
                    capacity_raw
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'deep-low-index', 'deep',
                           0.0285, 1, 1280000.0, 1280000.0, '5000000000')",
                [],
            )
            .expect("snapshot");
        let (pools, _) = eligible_raw_pools(&connection, "robinhood", 4663, &token0, &token1, 50)
            .expect("eligible pools");
        assert_eq!(pools.len(), 1);
        assert_eq!(pools[0].pool_id, "deep-low-index");
    }

    #[test]
    fn eligible_raw_pools_excludes_pool_without_capacity() {
        // A pool with enough TVL but no tradable capacity cannot carry a
        // position, so it must not be admitted.
        let token0 = format!("0x{}", "3".repeat(40));
        let token1 = format!("0x{}", "4".repeat(40));
        let (_store, connection) = temp_store();
        create_pool_tables(&connection);
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, token0, token1, fee,
                    dynamic_fee, special_fee, hooks
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'no-capacity', ?1, ?2, 3000, 0, 0, ?3)",
                params![token0, token1, ZERO_ADDRESS],
            )
            .expect("pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, depth_status,
                    liquidity_index, has_liquidity, estimated_tvl_usd, depth_score_usd,
                    capacity_raw
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'no-capacity', 'deep',
                           1000.0, 1, 900000.0, 0.0, '0')",
                [],
            )
            .expect("snapshot");
        let (pools, _) = eligible_raw_pools(&connection, "robinhood", 4663, &token0, &token1, 50)
            .expect("eligible pools");
        assert!(pools.is_empty());
    }

    #[test]
    fn rejects_hook_and_high_fee() {
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let reasons = quality_reasons(PoolQuality {
            // The fee ceiling is 30000 (3.00%), so a pool above it is
            // refused.
            fee: 50_000,
            dynamic_fee: 1,
            special_fee: 1,
            hooks: Some("0x0000000000000000000000000000000000000001"),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(1000.0),
            depth_status: Some("liquid"),
            estimated_tvl_usd: Some(250_000.0),
            capacity_raw: Some(5_000.0),
            capacity_evidence_available: true,
        });
        assert!(reasons.contains(&"fee_too_high".to_string()));
        assert!(reasons.contains(&"dynamic_fee".to_string()));
        assert!(reasons.contains(&"special_fee".to_string()));
        assert!(reasons.contains(&"non_zero_hook".to_string()));
    }

    fn temp_store() -> (NamedTempFile, Connection) {
        let store = NamedTempFile::new().expect("create temp store");
        let path = store.path().to_string_lossy().into_owned();
        let connection = Connection::open(path).expect("open temp store");
        (store, connection)
    }

    fn create_pool_tables(connection: &Connection) {
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_manager TEXT,
                    pool_key_json TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    dynamic_fee INTEGER,
                    special_fee INTEGER,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    depth_status TEXT,
                    liquidity_index REAL,
                    has_liquidity INTEGER,
                    estimated_tvl_usd REAL,
                    depth_score_usd REAL,
                    evidence_source TEXT,
                    capacity_raw TEXT,
                    capacity_binding_raw REAL,
                    price_impact_bps REAL,
                    balances_json TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE dex_candidate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    direction_address_pair TEXT,
                    pool_id TEXT NOT NULL,
                    pool_key_json TEXT,
                    pool_manager TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    fee_label TEXT,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    sqrt_price_x96 TEXT,
                    tick INTEGER,
                    has_liquidity INTEGER,
                    liquidity TEXT,
                    liquidity_index REAL,
                    estimated_tvl_usd REAL,
                    balances_json TEXT,
                    depth_status TEXT,
                    depth_band_json TEXT,
                    usable_for_observation INTEGER,
                    is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
                    is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
                    hop_recommendation_json TEXT,
                    optimal_roles_json TEXT,
                    liquidity_read_at TEXT,
                    liquidity_block_number INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
                );
                CREATE TABLE dex_candidate_pool_recommendations (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    counter_address TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    hop TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    pool_id TEXT NOT NULL,
                    depth_score REAL,
                    price REAL,
                    fee INTEGER,
                    selection_basis TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, direction_key, hop, pool_id)
                );
                CREATE TABLE v4_closed_loops (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    loop_key TEXT NOT NULL,
                    pool_ids_json TEXT,
                    PRIMARY KEY (network, chain_id, loop_key)
                );
                CREATE TABLE v4_closed_loop_hops (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    loop_key TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, loop_key, pool_id)
                );
                "#,
            )
            .expect("create test schema");
    }

    fn seed_raw_pool(
        connection: &Connection,
        pool_id: &str,
        token0: &str,
        token1: &str,
        depth_status: &str,
        has_liquidity: i64,
        fee: i64,
        estimated_tvl_usd: f64,
        depth_score_usd: Option<f64>,
        capacity_raw: &str,
    ) {
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, token0, token1, fee,
                    dynamic_fee, special_fee, hooks
                 ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    "4663".to_string(),
                    "uniswap_v4".to_string(),
                    pool_id.to_string(),
                    token0.to_string(),
                    token1.to_string(),
                    fee.to_string(),
                    ZERO_ADDRESS.to_string(),
                ]),
            )
            .expect("seed raw pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, depth_status, liquidity_index,
                    has_liquidity, estimated_tvl_usd, depth_score_usd, capacity_raw
                 ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    "4663".to_string(),
                    "uniswap_v4".to_string(),
                    pool_id.to_string(),
                    depth_status.to_string(),
                    "1000.0".to_string(),
                    has_liquidity.to_string(),
                    estimated_tvl_usd.to_string(),
                    depth_score_usd
                        .map(|value| value.to_string())
                        .unwrap_or_default(),
                    capacity_raw.to_string(),
                ]),
            )
            .expect("seed snapshot");
    }

    #[test]
    fn add_pair_accepts_positive_onchain_liquidity_without_usd_depth_labels() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(
            &connection,
            "deep-pool",
            &token_a,
            &token_b,
            "deep",
            1,
            3000,
            500_000.0,
            Some(500_000.0),
            "5000000000",
        );
        seed_raw_pool(
            &connection,
            "thin-pool",
            &token_a,
            &token_b,
            "thin",
            1,
            3000,
            7_955.0,
            None,
            "100000000",
        );

        let result =
            add_pair(&store, 4663, &token_a, &token_b, "TEST", 5, false).expect("add-pair dry run");
        assert_eq!(result["eligiblePoolIds"][0], "deep-pool");
        // The thin pool is rejected: its TVL is below the candidate floor, so
        // it can only ever produce dust-sized best trade amounts.
        assert_eq!(result["eligiblePoolCount"], 1);

        let write =
            add_pair(&store, 4663, &token_a, &token_b, "TEST", 5, true).expect("add-pair apply");
        assert_eq!(write["inserted"], 1);
        let inserted = connection
            .query_row(
                "SELECT COUNT(*) FROM dex_candidate_pools WHERE anchor_symbol = 'TEST'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .expect("count candidates");
        assert_eq!(inserted, 1);
    }

    #[test]
    fn refresh_key_reports_hop_ready_and_does_not_write_closed_loops() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(
            &connection,
            "unpriced-live-pool",
            &token_a,
            &token_b,
            "unpriced",
            1,
            3000,
            500_000.0,
            Some(500_000.0),
            "5000000000",
        );

        let dry = refresh_key(
            &store,
            4663,
            &format!("{token_a}:{token_b}"),
            "TEST",
            5,
            false,
        )
        .expect("refresh-key dry run");
        assert_eq!(dry["keyId"], canonical_pair_key(&token_a, &token_b));
        // An unpriced pool is not a usable candidate: without a TVL reading it
        // cannot prove enough depth to produce a meaningful best trade amount.
        assert_eq!(dry["eligiblePoolCount"], 0);
        assert_eq!(dry["ready"], false);

        let applied = refresh_key(
            &store,
            4663,
            &format!("{token_a}:{token_b}"),
            "TEST",
            5,
            true,
        )
        .expect("refresh-key apply");
        assert_eq!(applied["inserted"], 0);
        let loops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loops", [], |row| row.get(0))
            .expect("count loops");
        let hops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loop_hops", [], |row| {
                row.get(0)
            })
            .expect("count hops");
        assert_eq!(loops, 0);
        assert_eq!(hops, 0);
    }

    #[test]
    fn refresh_pair_never_writes_closed_loop_tables() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(
            &connection,
            "deep-pool",
            &token_a,
            &token_b,
            "deep",
            1,
            3000,
            500_000.0,
            Some(500_000.0),
            "5000000000",
        );
        seed_raw_pool(
            &connection,
            "old-pool",
            &token_a,
            &token_b,
            "thin",
            0,
            3000,
            7_955.0,
            None,
            "100000000",
        );
        let now = "2026-11-02T00:00:00Z";
        connection
            .execute(
                "INSERT INTO dex_candidate_pools (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    address_pair, direction_key, direction_address_pair, pool_id,
                    token0, token1, fee, dynamic_fee, special_fee, hooks,
                    depth_status, has_liquidity, liquidity_index, created_at, updated_at
                 ) VALUES (?, ?, 'uniswap_v4', 'v4', 'TEST', ?, ?, ?, 'old-pool',
                           ?, ?, 3000, 0, 0, ?, 'thin', 1, 1000.0, ?, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    "4663".to_string(),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    token_a.clone(),
                    token_b.clone(),
                    ZERO_ADDRESS.to_string(),
                    now.to_string(),
                    now.to_string(),
                ]),
            )
            .expect("insert old candidate");
        connection
            .execute(
                "INSERT INTO v4_closed_loops (network, chain_id, loop_key, pool_ids_json)
                 VALUES (?, 4663, 'loop-old', ?)",
                params_from_iter([network_for_chain_id(4663), "%old-pool%".to_string()]),
            )
            .expect("insert consumer loop");
        connection
            .execute(
                "INSERT INTO v4_closed_loop_hops (network, chain_id, loop_key, pool_id)
                 VALUES (?, 4663, 'loop-old', 'old-pool')",
                params_from_iter([network_for_chain_id(4663)]),
            )
            .expect("insert consumer hop");

        let dry = refresh_pair(&store, 4663, &token_a, &token_b, "TEST", 5, false)
            .expect("refresh dry run");
        assert_eq!(dry["existingPoolCount"], 1);
        assert_eq!(dry["stalePoolIds"][0], "old-pool");
        assert_eq!(dry["consumersNeedRebuild"], 1);

        let applied =
            refresh_pair(&store, 4663, &token_a, &token_b, "TEST", 5, true).expect("refresh apply");
        assert_eq!(applied["deletedRows"], 1);
        assert_eq!(applied["consumersNeedRebuild"], 1);
        let loops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loops", [], |row| row.get(0))
            .expect("count loops");
        let hops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loop_hops", [], |row| {
                row.get(0)
            })
            .expect("count hops");
        assert_eq!(loops, 1);
        assert_eq!(hops, 1);
    }

    #[test]
    fn cleanup_output_does_not_claim_closed_loop_delete_count() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(
            &connection,
            "bad-pool",
            &token_a,
            &token_b,
            "thin",
            0,
            3000,
            7_955.0,
            None,
            "100000000",
        );
        connection
            .execute(
                "INSERT INTO dex_candidate_pools (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    address_pair, direction_key, direction_address_pair, pool_id,
                    token0, token1, fee, dynamic_fee, special_fee, hooks,
                    depth_status, has_liquidity, liquidity_index, created_at, updated_at
                 ) VALUES (?, 4663, 'uniswap_v4', 'v4', 'TEST', ?, ?, ?, 'bad-pool',
                           ?, ?, 3000, 0, 0, ?, 'thin', 0, 0.0, ?, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    token_a,
                    token_b,
                    ZERO_ADDRESS.to_string(),
                    "2026-11-02T00:00:00Z".to_string(),
                    "2026-11-02T00:00:00Z".to_string(),
                ]),
            )
            .expect("insert bad candidate");

        let result = apply_cleanup(&store, 4663).expect("cleanup apply");
        assert!(result["cleanup"]["candidatePoolsDeleted"].as_i64().unwrap() >= 1);
        assert!(result["cleanup"].get("closedLoopsDeleted").is_none());
        assert!(result["cleanup"]["consumersNeedRebuild"].is_number());
    }

    // ------------------------------------------------------------------
    // W1: depth scope, resume, max-pools
    // ------------------------------------------------------------------

    /// Creates the three pool tables the depth scope resolver reads.
    fn create_depth_scope_tables(connection: &Connection) {
        connection
            .execute_batch(
                r#"
                CREATE TABLE IF NOT EXISTS v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    liquidity_read_at TEXT,
                    balances_json TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE IF NOT EXISTS dex_candidate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
                );
                CREATE TABLE IF NOT EXISTS manager_dex_candidate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    counter_address_pair TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address_pair, pool_id)
                );
                "#,
            )
            .expect("create depth scope tables");
    }

    fn insert_scope_pool(
        connection: &Connection,
        table: &str,
        pool_id: &str,
        liquidity_read_at: Option<&str>,
    ) {
        match table {
            "dex_candidate_pools" => {
                connection
                    .execute(
                        "INSERT INTO dex_candidate_pools \
                         (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, \
                          direction_key, pool_id) VALUES (?,?,?,?,?,?,?,?)",
                        params![
                            "robinhood",
                            4663i64,
                            "uniswap_v4",
                            "v4",
                            "ETH/USD",
                            "eth/usd",
                            "eth/usd",
                            pool_id
                        ],
                    )
                    .expect("insert dex candidate");
            }
            "manager_dex_candidate_pools" => {
                connection
                    .execute(
                        "INSERT INTO manager_dex_candidate_pools \
                         (network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address_pair, \
                          pool_id) VALUES (?,?,?,?,?,?,?)",
                        params!["robinhood", 4663i64, "uniswap_v4", "v4", "ETH/USD", "eth/usd", pool_id],
                    )
                    .expect("insert manager candidate");
            }
            _ => {
                connection
                    .execute(
                        "INSERT INTO v4_exchange_pool_liquidity_snapshots \
                         (network, chain_id, protocol, pool_id, liquidity_read_at) VALUES (?,?,?,?,?)",
                        params!["robinhood", 4663i64, "uniswap_v4", pool_id, liquidity_read_at],
                    )
                    .expect("insert snapshot");
            }
        }
    }

    #[test]
    fn depth_scope_candidate_is_the_union_of_candidate_tables() {
        let (store, connection) = temp_store();
        create_depth_scope_tables(&connection);
        insert_scope_pool(&connection, "dex_candidate_pools", "0xaaa", None);
        insert_scope_pool(&connection, "manager_dex_candidate_pools", "0xbbb", None);
        // A snapshot-only pool must NOT enter the candidate scope.
        insert_scope_pool(&connection, "snapshot", "0xccc", None);

        let resolved =
            collect_candidate_pool_ids(&connection, "robinhood", 4663, &[], DepthScope::Candidate)
                .expect("resolve candidate scope");

        assert_eq!(
            resolved.pool_count, 2,
            "candidate scope must be the table union"
        );
        assert_eq!(
            resolved.pool_ids,
            vec!["0xaaa".to_string(), "0xbbb".to_string()]
        );
        assert!(resolved
            .source_tables
            .contains(&"dex_candidate_pools".to_string()));
        assert!(resolved
            .source_tables
            .contains(&"manager_dex_candidate_pools".to_string()));
        drop(store);
    }

    #[test]
    fn depth_scope_all_covers_every_snapshot_pool() {
        let (store, connection) = temp_store();
        create_depth_scope_tables(&connection);
        insert_scope_pool(&connection, "snapshot", "0xaaa", None);
        insert_scope_pool(&connection, "snapshot", "0xbbb", None);
        insert_scope_pool(&connection, "dex_candidate_pools", "0xccc", None);

        let resolved =
            collect_candidate_pool_ids(&connection, "robinhood", 4663, &[], DepthScope::All)
                .expect("resolve all scope");

        assert_eq!(
            resolved.pool_count, 2,
            "all scope covers every snapshot pool"
        );
        assert_eq!(resolved.pool_ids.len(), 2);
        assert_eq!(
            resolved.source_tables,
            vec!["v4_exchange_pool_liquidity_snapshots".to_string()]
        );
        drop(store);
    }

    #[test]
    fn depth_explicit_pool_ids_win_over_scope() {
        let (store, connection) = temp_store();
        create_depth_scope_tables(&connection);
        insert_scope_pool(&connection, "dex_candidate_pools", "0xaaa", None);

        let resolved = collect_candidate_pool_ids(
            &connection,
            "robinhood",
            4663,
            &["0xAAA".to_string(), "0xaaa".to_string()],
            DepthScope::Candidate,
        )
        .expect("resolve explicit ids");

        assert_eq!(
            resolved.pool_count, 1,
            "explicit ids are normalised and de-duplicated"
        );
        assert_eq!(resolved.pool_ids, vec!["0xaaa".to_string()]);
        assert_eq!(resolved.source_tables, vec!["explicit_pool_id".to_string()]);
        drop(store);
    }

    #[test]
    fn depth_resume_skips_pools_that_already_carry_a_read_timestamp() {
        let (store, connection) = temp_store();
        create_depth_scope_tables(&connection);
        insert_scope_pool(
            &connection,
            "snapshot",
            "0xaaa",
            Some("2026-11-11T10:00:00Z"),
        );
        insert_scope_pool(&connection, "snapshot", "0xbbb", None);

        let fresh = filter_already_refreshed(
            &connection,
            "robinhood",
            4663,
            &["0xaaa".to_string(), "0xbbb".to_string()],
        )
        .expect("filter already refreshed");

        assert_eq!(fresh.len(), 1);
        assert!(fresh.contains("0xaaa"));
        assert!(
            !fresh.contains("0xbbb"),
            "a pool without a snapshot is always refreshed"
        );
        drop(store);
    }

    /// A dynamic-fee pool is decided by its hook at swap time, so the
    /// off-chain depth arithmetic cannot price it. It must never enter the
    /// population that the depth refresh measures, not even when it is named
    /// explicitly.
    #[test]
    fn a_dynamic_fee_pool_is_excluded_from_the_depth_population() {
        let (store, connection) = temp_store();
        create_depth_scope_tables(&connection);
        insert_scope_pool(&connection, "dex_candidate_pools", "0xaaa", None);
        insert_scope_pool(&connection, "dex_candidate_pools", "0xdynamic", None);
        // One candidate is a dynamic-fee pool. Its rate is decided by the
        // hook at swap time, so it can never be priced off chain.
        connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS v4_exchange_pools (
                     network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                     fee INTEGER NOT NULL DEFAULT 0, dynamic_fee INTEGER NOT NULL DEFAULT 0);
                 INSERT INTO v4_exchange_pools
                     VALUES ('robinhood', 4663, 'uniswap_v4', '0xaaa', 3000, 0);
                 INSERT INTO v4_exchange_pools
                     VALUES ('robinhood', 4663, 'uniswap_v4', '0xdynamic', 8388608, 1);",
            )
            .expect("seed the pool table");

        let resolved =
            collect_candidate_pool_ids(&connection, "robinhood", 4663, &[], DepthScope::Candidate)
                .expect("resolve candidate scope");
        assert!(
            !resolved
                .pool_ids
                .iter()
                .any(|pool_id| pool_id == "0xdynamic"),
            "a dynamic-fee pool must not enter the candidate scope: {:?}",
            resolved.pool_ids
        );

        // An explicitly named dynamic-fee pool is excluded as well.
        let explicit = collect_candidate_pool_ids(
            &connection,
            "robinhood",
            4663,
            &["0xdynamic".to_string(), "0xaaa".to_string()],
            DepthScope::Candidate,
        )
        .expect("resolve the explicit scope");
        assert_eq!(explicit.pool_ids, vec!["0xaaa".to_string()]);
        drop(store);
    }

    #[test]
    fn depth_scope_parses_only_candidate_and_all() {
        assert_eq!(
            DepthScope::parse("candidate").expect("candidate"),
            DepthScope::Candidate
        );
        assert_eq!(DepthScope::parse("ALL").expect("all"), DepthScope::All);
        assert!(DepthScope::parse("everything").is_err());
        assert_eq!(DepthScope::default(), DepthScope::Candidate);
        assert_eq!(DepthScope::Candidate.as_str(), "candidate");
        assert_eq!(DepthScope::All.as_str(), "all");
    }
    #[test]
    fn ensure_indexes_survives_sqlite_text_query_plan_detail() {
        // Regression: SQLite reports the fourth EXPLAIN QUERY PLAN column as
        // TEXT. Reading it as an integer made `ensure-indexes --apply` abort
        // with "Invalid column type Text at index: 3" even though the index
        // had already been created.
        let store = NamedTempFile::new().expect("create temp store");
        let path = store.path().to_string_lossy().into_owned();
        let connection = Connection::open(&path).expect("open temp store");
        connection
            .execute_batch(
                r#"
                CREATE TABLE cow_execution_reviews (
                    id TEXT PRIMARY KEY,
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    selected_profit_source TEXT
                );
                "#,
            )
            .expect("create review table");
        connection
            .execute(
                "INSERT INTO cow_execution_reviews \
                 (id, network, chain_id, created_at, selected_profit_source) \
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    "review-1",
                    "robinhood",
                    4663i64,
                    "2026-11-11 00:00:00",
                    "native_contract_rate_delta_estimate"
                ],
            )
            .expect("seed review row");
        drop(connection);

        let report = ensure_indexes(&path, 4663).expect("ensure indexes must not fail");
        assert_eq!(
            report["profitSourceQueryPlanUsesIndex"],
            json!(true),
            "profit-source plan must use the dedicated index: {:?}",
            report["profitSourceQueryPlan"]
        );
        assert!(
            report["reviewWindowQueryPlan"]
                .as_array()
                .expect("plan array")
                .iter()
                .any(|line| line
                    .as_str()
                    .expect("plan line")
                    .contains(REVIEW_SCOPE_CREATED_INDEX)),
            "review-window plan must use the scope/created index: {:?}",
            report["reviewWindowQueryPlan"]
        );
        drop(store);
    }

    #[test]
    fn depth_snapshot_writes_token_denominated_balances_payload() {
        // Regression: the snapshot writer left `balances_json` as `{}`, so the
        // Python sizing path could not derive a liquidity cap and pinned
        // `maxProbeAmountRaw` to the fixed 64-token fallback.
        let store = NamedTempFile::new().expect("create temp store");
        let path = store.path().to_string_lossy().into_owned();
        let connection = Connection::open(&path).expect("open temp store");
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    liquidity TEXT,
                    has_liquidity INTEGER,
                    tick INTEGER,
                    sqrt_price_x96 TEXT,
                    capacity_token0_raw TEXT,
                    capacity_token1_raw TEXT,
                    capacity_raw TEXT,
                    price_impact_bps REAL,
                    price_impact_limit_bps REAL,
                    depth_basis TEXT,
                    liquidity_read_at TEXT,
                    refreshed_at TEXT,
                    evidence_source TEXT,
                    capacity_binding_raw REAL,
                    depth_band_json TEXT,
                    balances_json TEXT,
                    single_tick_max_token0_raw TEXT,
                    single_tick_max_token1_raw TEXT,
                    depth_status TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                INSERT INTO v4_exchange_pool_liquidity_snapshots
                    (network, chain_id, protocol, pool_id)
                VALUES ('robinhood', 4663, 'uniswap_v4', '0xpool');
                "#,
            )
            .expect("create snapshot table");

        let state = chain::V4PoolState {
            pool_id: "0xpool".to_string(),
            sqrt_price_x96: "79228162514264337593543950336".to_string(),
            tick: 0,
            protocol_fee: 0,
            lp_fee: 3000,
            liquidity: 1_000_000_000_000_000_000,
            has_liquidity: true,
            error: None,
            complete: true,
        };
        let capacity = depth::Capacity {
            capacity_token0_raw: 9_549_508,
            capacity_token1_raw: 139_413_914_538_385_642,
            price_impact_bps: 9.999_999_835_354_826,
            fee_pips: 3000,
            has_liquidity: true,
            error: None,
            single_tick_max_token0_raw: Some(7_777),
            single_tick_max_token1_raw: Some(8_888),
        };

        write_depth_snapshot(
            &connection,
            "robinhood",
            4663,
            &state,
            &capacity,
            Some(0.139_413_914_538_385_64),
            Some(6),
            Some(18),
        )
        .expect("write depth snapshot");

        let balances_json: String = connection
            .query_row(
                "SELECT balances_json FROM v4_exchange_pool_liquidity_snapshots \
                 WHERE network='robinhood' AND chain_id=4663 AND protocol='uniswap_v4' \
                 AND pool_id='0xpool'",
                [],
                |row| row.get(0),
            )
            .expect("read balances_json");
        let balances: serde_json::Value =
            serde_json::from_str(&balances_json).expect("balances_json must be valid JSON");
        assert_eq!(balances["token0"], json!("9.549507999999999441"));
        assert_eq!(balances["token1"], json!("0.139413914538385642"));
        assert_eq!(balances["capacityToken0"], balances["token0"]);
        assert_eq!(balances["capacityToken1"], balances["token1"]);
        assert_eq!(balances["token0Decimals"], json!(6));
        assert_eq!(balances["token1Decimals"], json!(18));
        // The tick-range bounds are published as raw amounts, so the
        // closed-loop layer never has to recompute the range.
        assert_eq!(balances["singleTickMaxToken0Raw"], json!("7777"));
        assert_eq!(balances["singleTickMaxToken1Raw"], json!("8888"));
        let (token0_bound, token1_bound): (Option<String>, Option<String>) = connection
            .query_row(
                "SELECT single_tick_max_token0_raw, single_tick_max_token1_raw
                 FROM v4_exchange_pool_liquidity_snapshots
                 WHERE network='robinhood' AND chain_id=4663 AND protocol='uniswap_v4'
                   AND pool_id='0xpool'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .expect("read the tick bounds");
        assert_eq!(token0_bound.as_deref(), Some("7777"));
        assert_eq!(token1_bound.as_deref(), Some("8888"));
        assert_eq!(
            balances["capacityRaw"],
            json!(capacity.best_raw().to_string()),
            "capacityRaw reports the deeper side"
        );
        assert_eq!(
            balances["capacityBindingRaw"],
            json!(0.139_413_914_538_385_64)
        );
        assert_eq!(balances["depthBasis"], json!(depth::DEPTH_BASIS));
        assert!(balances["priceImpactBps"].is_number());
        assert!(
            balances["token0"].as_str().is_some(),
            "token0 must be present for the sizing path"
        );
        assert!(
            balances["token1"].as_str().is_some(),
            "token1 must be present for the sizing path"
        );
        drop(store);
    }

    /// Regression: the snapshot write has to clear the retired USD
    /// valuation column.
    ///
    /// The pool manager no longer maintains a USD valuation: the shared
    /// depth policy lists ``depth_admission`` and ``capacity_admission``
    /// under ``usdgPegPolicy.forbiddenUses``, and the depth-band document
    /// carries ``estimatedTvlUsd: null``. The column was left untouched by
    /// the write, so whatever value the retired TVL valuation path had
    /// written survived every refresh. Values as large as 4.3e22 were
    /// observed on the mainnet store, and consumers that still read the
    /// column reported a malformed reading for a perfectly healthy pool.
    #[test]
    fn depth_snapshot_clears_the_retired_usd_valuation_column() {
        let store = NamedTempFile::new().expect("create temp store");
        let path = store.path().to_string_lossy().into_owned();
        let connection = Connection::open(&path).expect("open temp store");
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    liquidity TEXT,
                    has_liquidity INTEGER,
                    tick INTEGER,
                    sqrt_price_x96 TEXT,
                    capacity_token0_raw TEXT,
                    capacity_token1_raw TEXT,
                    capacity_raw TEXT,
                    price_impact_bps REAL,
                    price_impact_limit_bps REAL,
                    depth_basis TEXT,
                    liquidity_read_at TEXT,
                    refreshed_at TEXT,
                    evidence_source TEXT,
                    capacity_binding_raw REAL,
                    depth_band_json TEXT,
                    balances_json TEXT,
                    single_tick_max_token0_raw TEXT,
                    single_tick_max_token1_raw TEXT,
                    estimated_tvl_usd REAL,
                    depth_status TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                INSERT INTO v4_exchange_pool_liquidity_snapshots
                    (network, chain_id, protocol, pool_id, estimated_tvl_usd)
                VALUES ('robinhood', 4663, 'uniswap_v4', '0xpool', 4.323561321500667e22);
                "#,
            )
            .expect("create snapshot table");

        let state = chain::V4PoolState {
            pool_id: "0xpool".to_string(),
            sqrt_price_x96: "79228162514264337593543950336".to_string(),
            tick: 0,
            protocol_fee: 0,
            lp_fee: 3000,
            liquidity: 1_000_000_000_000_000_000,
            has_liquidity: true,
            error: None,
            complete: true,
        };
        let capacity = depth::Capacity {
            capacity_token0_raw: 9_549_508,
            capacity_token1_raw: 139_413_914_538_385_642,
            price_impact_bps: 9.999_999_835_354_826,
            fee_pips: 3000,
            has_liquidity: true,
            error: None,
            single_tick_max_token0_raw: Some(7_777),
            single_tick_max_token1_raw: Some(8_888),
        };
        write_depth_snapshot(
            &connection,
            "robinhood",
            4663,
            &state,
            &capacity,
            Some(0.139_413_914_538_385_64),
            Some(6),
            Some(18),
        )
        .expect("write depth snapshot");

        let stored: Option<f64> = connection
            .query_row(
                "SELECT estimated_tvl_usd FROM v4_exchange_pool_liquidity_snapshots
                 WHERE network='robinhood' AND chain_id=4663 AND protocol='uniswap_v4'
                   AND pool_id='0xpool'",
                [],
                |row| row.get(0),
            )
            .expect("read the retired valuation");
        assert!(
            stored.is_none(),
            "the retired USD valuation must not survive a snapshot write"
        );
        drop(store);
    }


    /// D6 regression: the candidate table's `balances_json` must carry
    /// token-denominated AMOUNTS, never token ADDRESSES.
    ///
    /// The sizing path (`dex_quote_evidence_size.py::_pool_borrow_side_amount`)
    /// parses `balances["token0"]` / `["token1"]` as decimal amounts. A `0x...`
    /// string makes `_decimal()` raise `ValueError("invalid decimal amount")`
    /// instead of returning `None`, so an address in this payload would abort
    /// the review loop rather than degrade to its fallback.
    #[test]
    fn d6_candidate_balances_never_contain_token_addresses() {
        let (store, connection) = temp_store();
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    hooks TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    depth_status TEXT,
                    has_liquidity INTEGER,
                    liquidity_index REAL,
                    estimated_tvl_usd REAL,
                    depth_score_usd REAL,
                    capacity_raw TEXT,
                    capacity_binding_raw REAL,
                    price_impact_bps REAL,
                    balances_json TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE dex_candidate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    direction_address_pair TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_key_json TEXT,
                    pool_manager TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    fee_label TEXT,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    sqrt_price_x96 TEXT,
                    tick INTEGER,
                    has_liquidity INTEGER,
                    liquidity TEXT,
                    liquidity_index REAL,
                    estimated_tvl_usd REAL,
                    balances_json TEXT,
                    depth_status TEXT,
                    depth_band_json TEXT,
                    usable_for_observation INTEGER,
                    is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
                    is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
                    hop_recommendation_json TEXT,
                    optimal_roles_json TEXT,
                    liquidity_read_at TEXT,
                    liquidity_block_number INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id));
                "#,
            )
            .expect("schema");

        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let pool_id = "0xpool";
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (network, chain_id, protocol, pool_id, token0, token1, fee, dynamic_fee, special_fee, hooks) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, ?2, ?3, 3000, 0, 0, '0x0000000000000000000000000000000000000000')",
                rusqlite::params![pool_id, token0, token1],
            )
            .expect("insert pool");

        // No measured snapshot yet: the candidate payload must NOT contain the
        // token addresses where the sizing path expects amounts.
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (network, chain_id, protocol, pool_id, depth_status, has_liquidity, capacity_raw) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, 'deep', 1, '1000000')",
                rusqlite::params![pool_id],
            )
            .expect("insert snapshot");

        let (pools, _) = eligible_raw_pools(&connection, "robinhood", 4663, &token0, &token1, 10)
            .expect("eligible");
        assert_eq!(pools.len(), 1, "the pool must be admitted on capacity");
        upsert_eligible_pool_rows(
            &connection,
            "robinhood",
            4663,
            &token0,
            &token1,
            "anchor",
            &pools,
        )
        .expect("upsert");

        let balances_raw: String = connection
            .query_row(
                "SELECT balances_json FROM dex_candidate_pools WHERE pool_id = ?1",
                rusqlite::params![pool_id],
                |row| row.get(0),
            )
            .expect("read balances");
        let balances: serde_json::Value = serde_json::from_str(&balances_raw).expect("valid json");
        assert!(
            !balances["token0"]
                .as_str()
                .unwrap_or_default()
                .starts_with("0x"),
            "token0 must not be a token address: {}",
            balances_raw
        );
        assert!(
            !balances["token1"]
                .as_str()
                .unwrap_or_default()
                .starts_with("0x"),
            "token1 must not be a token address: {}",
            balances_raw
        );
        // The address identity is preserved under an explicit key.
        assert_eq!(balances["token0Address"], json!(token0));
        assert_eq!(balances["token1Address"], json!(token1));
        drop(store);
    }

    /// D6 regression: once a measured snapshot exists, the candidate payload
    /// must carry the snapshot's token-denominated AMOUNTS verbatim.
    #[test]
    fn d6_candidate_balances_adopt_the_measured_snapshot_amounts() {
        let (store, connection) = temp_store();
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL, token0 TEXT, token1 TEXT, fee INTEGER,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0, special_fee INTEGER NOT NULL DEFAULT 0,
                    hooks TEXT, PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL, depth_status TEXT, has_liquidity INTEGER,
                    liquidity_index REAL,
                    estimated_tvl_usd REAL, depth_score_usd REAL, capacity_raw TEXT,
                    capacity_binding_raw REAL, price_impact_bps REAL, balances_json TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE dex_candidate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    direction_address_pair TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_key_json TEXT,
                    pool_manager TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    fee_label TEXT,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    sqrt_price_x96 TEXT,
                    tick INTEGER,
                    has_liquidity INTEGER,
                    liquidity TEXT,
                    liquidity_index REAL,
                    estimated_tvl_usd REAL,
                    balances_json TEXT,
                    depth_status TEXT,
                    depth_band_json TEXT,
                    usable_for_observation INTEGER,
                    is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
                    is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
                    hop_recommendation_json TEXT,
                    optimal_roles_json TEXT,
                    liquidity_read_at TEXT,
                    liquidity_block_number INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id));
                "#,
            )
            .expect("schema");

        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let pool_id = "0xpool";
        let measured = json!({
            "token0": "9.549507999999999441",
            "token1": "0.139413914538385642",
            "capacityRaw": "1000000",
            "depthBasis": "onchain_tick_liquidity",
        })
        .to_string();

        connection
            .execute(
                "INSERT INTO v4_exchange_pools (network, chain_id, protocol, pool_id, token0, token1, fee, dynamic_fee, special_fee, hooks) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, ?2, ?3, 3000, 0, 0, '0x0000000000000000000000000000000000000000')",
                rusqlite::params![pool_id, token0, token1],
            )
            .expect("insert pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (network, chain_id, protocol, pool_id, depth_status, has_liquidity, capacity_raw, balances_json) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, 'deep', 1, '1000000', ?2)",
                rusqlite::params![pool_id, measured],
            )
            .expect("insert snapshot");

        let (pools, _) = eligible_raw_pools(&connection, "robinhood", 4663, &token0, &token1, 10)
            .expect("eligible");
        upsert_eligible_pool_rows(
            &connection,
            "robinhood",
            4663,
            &token0,
            &token1,
            "anchor",
            &pools,
        )
        .expect("upsert");

        let balances_raw: String = connection
            .query_row(
                "SELECT balances_json FROM dex_candidate_pools WHERE pool_id = ?1",
                rusqlite::params![pool_id],
                |row| row.get(0),
            )
            .expect("read balances");
        let balances: serde_json::Value = serde_json::from_str(&balances_raw).expect("valid json");
        assert_eq!(balances["token0"], json!("9.549507999999999441"));
        assert_eq!(balances["token1"], json!("0.139413914538385642"));
        drop(store);
    }

    /// The depth verdict has to travel with the measured payload.
    ///
    /// A candidate row that keeps the verdict of an earlier measurement
    /// while the snapshot carries the fresh one would report a pool as
    /// shallow that the chain has just measured as deep, and the repair
    /// cascade would keep looking for a replacement for a pool that is
    /// already good.
    #[test]
    fn the_depth_verdict_travels_with_the_measured_payload() {
        let (store, connection) = temp_store();
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL, balances_json TEXT, depth_status TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE dex_candidate_pools (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL, anchor_symbol TEXT NOT NULL, address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL, pool_id TEXT NOT NULL, balances_json TEXT,
                    depth_status TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
                );
                "#,
            )
            .expect("schema");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots \
                 (network, chain_id, protocol, pool_id, balances_json, depth_status) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', '0xpool', '{\"token0\":\"1\"}', 'deep')",
                [],
            )
            .expect("insert snapshot");
        connection
            .execute(
                "INSERT INTO dex_candidate_pools \
                 (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, direction_key, pool_id, balances_json, depth_status, created_at, updated_at) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', 'v4', 'ETH', 'ETH:USDG', 'ETH:USDG', '0xpool', '{}', 'thin', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                [],
            )
            .expect("insert candidate");

        let updated = propagate_balances_to_candidate_pools(
            &connection,
            "robinhood",
            4663,
            &["0xpool".to_string()],
        )
        .expect("propagate");
        assert_eq!(updated, 1);

        let depth_status: String = connection
            .query_row(
                "SELECT depth_status FROM dex_candidate_pools WHERE pool_id = '0xpool'",
                [],
                |row| row.get(0),
            )
            .expect("read verdict");
        assert_eq!(
            depth_status, "deep",
            "the candidate row must carry the verdict the chain just measured"
        );
        let _ = store;
    }

    /// D5 regression: `depth --apply` must propagate the measured payload into
    /// the CANDIDATE table, because the sizing path reads the candidate table
    /// and not the snapshot table.
    ///
    /// Without this propagation the candidate row keeps an empty `{}` payload,
    /// `_liquidity_cap_raw()` keeps returning `None`, and the D4 capacity-aware
    /// ladder start never leaves its fallback path.
    #[test]
    fn d5_depth_apply_propagates_balances_into_the_candidate_table() {
        let (store, connection) = temp_store();
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL, balances_json TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE dex_candidate_pools (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL, anchor_symbol TEXT NOT NULL, address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL, pool_id TEXT NOT NULL, balances_json TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
                );
                "#,
            )
            .expect("schema");

        let measured = json!({
            "token0": "9.549507999999999441",
            "token1": "0.139413914538385642",
            "capacityRaw": "1000000",
        })
        .to_string();
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (network, chain_id, protocol, pool_id, balances_json) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', '0xpool', ?1)",
                rusqlite::params![measured],
            )
            .expect("insert snapshot");
        connection
            .execute(
                "INSERT INTO dex_candidate_pools (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, direction_key, pool_id, balances_json, created_at, updated_at) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', 'v4', 'ETH', 'ETH:USDG', 'ETH:USDG', '0xpool', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                [],
            )
            .expect("insert candidate");

        let updated = propagate_balances_to_candidate_pools(
            &connection,
            "robinhood",
            4663,
            &["0xpool".to_string()],
        )
        .expect("propagate");
        assert_eq!(updated, 1, "exactly one candidate row must be updated");

        let balances_raw: String = connection
            .query_row(
                "SELECT balances_json FROM dex_candidate_pools WHERE pool_id = '0xpool'",
                [],
                |row| row.get(0),
            )
            .expect("read balances");
        let balances: serde_json::Value = serde_json::from_str(&balances_raw).expect("valid json");
        assert_eq!(balances["token0"], json!("9.549507999999999441"));
        assert_eq!(balances["token1"], json!("0.139413914538385642"));
        drop(store);
    }

    /// D5 regression: a candidate row with no measured snapshot must keep its
    /// payload untouched. An absent measurement must stay absent rather than
    /// become a fabricated zero.
    #[test]
    fn d5_propagation_leaves_unmeasured_candidates_untouched() {
        let (store, connection) = temp_store();
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL, balances_json TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE dex_candidate_pools (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL, protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL, anchor_symbol TEXT NOT NULL, address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL, pool_id TEXT NOT NULL, balances_json TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
                );
                "#,
            )
            .expect("schema");
        connection
            .execute(
                "INSERT INTO dex_candidate_pools (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, direction_key, pool_id, balances_json, created_at, updated_at) \
                 VALUES ('robinhood', 4663, 'uniswap_v4', 'v4', 'ETH', 'ETH:USDG', 'ETH:USDG', '0xpool', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                [],
            )
            .expect("insert candidate");

        let updated = propagate_balances_to_candidate_pools(
            &connection,
            "robinhood",
            4663,
            &["0xpool".to_string()],
        )
        .expect("propagate");
        assert_eq!(updated, 0, "an unmeasured candidate must not be updated");
        let balances_raw: String = connection
            .query_row(
                "SELECT balances_json FROM dex_candidate_pools WHERE pool_id = '0xpool'",
                [],
                |row| row.get(0),
            )
            .expect("read balances");
        assert_eq!(balances_raw, "{}");
        drop(store);
    }

    /// VAL-4-8 regression: `depth --apply` must repair its own schema.
    ///
    /// The write path stores the measured capacity in dedicated columns.
    /// When those columns are missing the write failed with "no such
    /// column", which looked like a corrupted database instead of an
    /// un-migrated schema. The refresh now adds them itself.
    #[test]
    fn val4_8_the_depth_refresh_adds_the_missing_capacity_columns() {
        let connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                     network TEXT NOT NULL,
                     chain_id INTEGER NOT NULL,
                     protocol TEXT NOT NULL,
                     pool_id TEXT NOT NULL,
                     liquidity TEXT,
                     has_liquidity INTEGER,
                     tick INTEGER,
                     sqrt_price_x96 TEXT,
                     liquidity_read_at TEXT,
                     refreshed_at TEXT,
                     evidence_source TEXT,
                     depth_band_json TEXT,
                     balances_json TEXT
                 );",
            )
            .expect("legacy snapshot table");
        let added = add_depth_capacity_columns(&connection).expect("add columns");
        assert!(added.contains(&"capacity_token0_raw"));
        assert!(added.contains(&"capacity_token1_raw"));
        assert!(added.contains(&"capacity_binding_raw"));
        // The repair is idempotent.
        let again = add_depth_capacity_columns(&connection).expect("add columns again");
        assert!(again.is_empty());
        // Every declared column is present afterwards.
        for (column, _) in DEPTH_CAPACITY_COLUMNS {
            let exists: i64 = connection
                .query_row(
                    "SELECT COUNT(*) FROM pragma_table_info('v4_exchange_pool_liquidity_snapshots')
                      WHERE name = ?1",
                    rusqlite::params![column],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(exists, 1, "column {column} is missing");
        }
    }

    /// VAL-4-8 regression: the USDG depth table must be readable with the
    /// exact query the token manager uses. The acceptance criterion is a
    /// real read-back, not a write counter.
    #[test]
    fn val4_8_usdg_depth_is_readable_with_the_token_manager_query() {
        let (store, connection) = temp_store();
        ensure_usdg_depth_schema(&connection).expect("schema");
        let observations = vec![UsdgDepthObservation {
            token_address: "0xaaa".to_string(),
            pool_id: "0xpool".to_string(),
            depth_usdg_raw: 3_000_000,
            token_decimals: Some(18),
            valuation_usdg_raw: Some(1_500_000),
            price_impact_bps: Some(9.5),
            pool_state_version: Some("v1".to_string()),
        }];
        write_usdg_depth_observations(
            &connection,
            "robinhood",
            4663,
            "2026-11-12T00:00:00Z",
            &observations,
        )
        .expect("write");

        // This is the query `token_manager::usdg_evidence::read_measured_depths`
        // issues. It must see the row the pool manager wrote.
        let mut statement = connection
            .prepare(
                "SELECT token_address, depth_usdg_raw
                   FROM token_usdg_depth_measurements
                  WHERE network = ?1 AND chain_id = ?2 AND depth_usdg_raw > 0",
            )
            .expect("prepare");
        let rows = statement
            .query_map(rusqlite::params!["robinhood", 4663], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
            })
            .expect("query");
        let read: Vec<(String, i64)> = rows.flatten().collect();
        assert_eq!(read, vec![("0xaaa".to_string(), 3_000_000)]);
        drop(store);
    }

    /// VAL-4-8 regression: a pool without a valuation must not produce a
    /// row. The absence of the row is the statement "not measured".
    #[test]
    fn val4_8_an_unvalued_token_produces_no_depth_row() {
        let (store, connection) = temp_store();
        ensure_usdg_depth_schema(&connection).expect("schema");
        let observations: Vec<UsdgDepthObservation> = Vec::new();
        let written = write_usdg_depth_observations(
            &connection,
            "robinhood",
            4663,
            "2026-11-12T00:00:00Z",
            &observations,
        )
        .expect("write");
        assert_eq!(written, 0);
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM token_usdg_depth_measurements",
                [],
                |row| row.get(0),
            )
            .expect("count");
        assert_eq!(count, 0, "an unmeasured pool must leave no row behind");
        drop(store);
    }

    /// VAL-4-8 regression: the conversion from token capacity to USDG depth
    /// must never turn "unknown" into zero.
    #[test]
    fn val4_8_capacity_conversion_keeps_unknowns_missing() {
        assert_eq!(capacity_to_usdg_depth(1_000, Some(18), None), None);
        assert_eq!(capacity_to_usdg_depth(1_000, None, Some(1_500_000)), None);
        assert_eq!(capacity_to_usdg_depth(0, Some(18), Some(1_500_000)), None);
        assert_eq!(
            capacity_to_usdg_depth(2_000_000_000_000_000_000, Some(18), Some(1_500_000)),
            Some(3_000_000)
        );
    }
}
