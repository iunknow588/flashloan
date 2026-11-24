//! The directional pool layer.
//!
//! A **directional pool** is a base V4 pool projected onto one of its two
//! swap directions.  The projection adds **no new on-chain fact**: the
//! identity comes from the base pool table, the state comes from the
//! liquidity snapshot table, and the direction is a pure function of the
//! token order.
//!
//! Direction **0** trades the higher address token into the lower one
//! (`token1 -> token0`), direction **1** trades the lower address token into
//! the higher one (`token0 -> token1`).  A base pool therefore projects onto
//! **exactly two** rows, and the two rows differ only in the direction
//! dependent fields.
//!
//! The standing rules of the system are kept: a pool with a dynamic fee or
//! with a hook is not a pool the off-chain arithmetic may price, so it is
//! not projected at all.  The layer never re-decides admission; it only
//! projects what the pool manager already published.

use candidate_edge_pool_manager::{price_directional_output, DirectionalPriceInput};
use manager_contracts::directional_pool::PoolDirection;
use manager_contracts::ids::{DirectionalPoolId, PairKeyId};
use rusqlite::{params, params_from_iter, Connection, OpenFlags, OptionalExtension, Row};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

pub const NETWORK_PROTOCOL: &str = "uniswap_v4";
pub const MAX_REQUEST_ID_LENGTH: usize = 128;
pub const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
pub const MAX_SNAPSHOT_LIMIT: usize = 1_000;
pub const MATERIALIZE_OPERATION: &str = "directional_pools.materialize";

/// Environment variable that overrides the directional candidate capacity.
///
/// The candidate count is a **capacity knob**, not an admission threshold:
/// it answers "how many pools may one (pair, direction) group keep as
/// candidates", never "is this pool qualified".  Changing it changes the
/// system's behaviour, so it is a knob and belongs in the environment
/// rather than in the source text.
pub const MAX_DIRECTION_CANDIDATE_POOLS_ENV: &str = "FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS";

/// The default capacity when the environment does not override it.
pub const DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS: usize = 5;

/// The two tiers a `(pair, direction)` group is split into.
///
/// The group holds every pool that passed admission; the **rank** decides
/// which tier a pool belongs to.  Ranks `1..=N` are the candidates, the
/// ranks beyond that are the backups.  The split is a *layering*, not a
/// truncation: the backups stay in the table and stay reachable.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DirectionalPoolTier {
    /// A pool of the candidate tier: one of the first `N` ranks.
    Candidate,
    /// A pool of the backup tier: a qualified pool that did not make the cut.
    Backup,
}

/// Resolve the directional candidate capacity from the environment.
///
/// A malformed or non-positive value falls back to the default instead of
/// failing the build: a capacity knob must never be able to stop the
/// scanner from running.
pub fn max_direction_candidate_pools() -> usize {
    std::env::var(MAX_DIRECTION_CANDIDATE_POOLS_ENV)
        .ok()
        .and_then(|raw| raw.trim().parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS)
}

/// The size of the candidate tier of a group that holds `eligible` pools.
///
/// The identity `N == min(N_max, eligible)` is the whole point of the
/// layering: the group is never padded to the capacity and never cut below
/// the number of qualified pools it actually has.
pub fn candidate_tier_size(eligible: usize) -> usize {
    eligible.min(max_direction_candidate_pools())
}

/// The tier of the row that holds `rank` inside a group of `eligible` pools.
///
/// The ranks are continuous by construction, so the split is exact: the
/// candidate tier owns `1..=N` and the backup tier owns `N+1..=eligible`.
pub fn tier_of_rank(rank: i64, eligible: usize) -> DirectionalPoolTier {
    let candidate_count = candidate_tier_size(eligible) as i64;
    if rank >= 1 && rank <= candidate_count {
        DirectionalPoolTier::Candidate
    } else {
        DirectionalPoolTier::Backup
    }
}

/// The two tables of the directional pool layer.
pub const DIRECTIONAL_POOL_TABLE: &str = "manager_v4_directional_exchange_pools";
pub const DIRECTIONAL_POOL_STATE_TABLE: &str = "manager_v4_directional_pool_library_state";

/// The zero address: the marker of a pool without a hook.
const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

#[derive(Debug)]
pub enum DirectionalPoolError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    InvalidRequest(String),
    /// The pool scope of the operation is missing or disabled.
    MissingPoolScope(String),
    /// The base pool table of the scope is not a table this layer may read.
    UnsupportedPoolTable(String),
    /// The liquidity snapshot table is not there yet.
    MissingSnapshotTable,
    IdempotencyKeyReused {
        idempotency_key: String,
        existing_request_id: String,
        submitted_request_id: String,
    },
}

impl fmt::Display for DirectionalPoolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "sqlite error: {error}"),
            Self::Json(error) => write!(formatter, "json error: {error}"),
            Self::InvalidRequest(message) => write!(formatter, "invalid request: {message}"),
            Self::MissingPoolScope(scope_key) => {
                write!(formatter, "pool scope {scope_key} is missing or disabled")
            }
            Self::UnsupportedPoolTable(table) => {
                write!(formatter, "pool table {table} is not a directional scope table")
            }
            Self::MissingSnapshotTable => {
                formatter.write_str("the liquidity snapshot table does not exist")
            }
            Self::IdempotencyKeyReused {
                idempotency_key,
                existing_request_id,
                submitted_request_id,
            } => write!(
                formatter,
                "idempotency key {idempotency_key} was already used by request {existing_request_id}; the new request is {submitted_request_id}"
            ),
        }
    }
}

impl std::error::Error for DirectionalPoolError {}

impl From<rusqlite::Error> for DirectionalPoolError {
    fn from(value: rusqlite::Error) -> Self {
        Self::Sqlite(value)
    }
}

impl From<serde_json::Error> for DirectionalPoolError {
    fn from(value: serde_json::Error) -> Self {
        Self::Json(value)
    }
}

/// The upstream the operation expects to find unchanged.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpectedUpstream {
    pub pool_scope_version: Option<i64>,
    pub pool_scope_checksum: Option<String>,
    pub snapshot_checksum: Option<String>,
}

#[derive(Debug, Clone)]
pub struct DirectionalPoolWriteRequest {
    pub network: String,
    pub chain_id: i64,
    pub scope_key: String,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub expected_upstream: ExpectedUpstream,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DirectionalPoolMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub apply: bool,
    pub dry_run: bool,
    pub pool_scope_version: i64,
    pub pool_scope_checksum: String,
    pub snapshot_checksum: String,
    pub base_pool_count: usize,
    pub directional_pool_count: usize,
    pub direction0_count: usize,
    pub direction1_count: usize,
    pub eligible_pair_count: usize,
    pub measured_capacity_count: usize,
    pub measured_output_count: usize,
    pub excluded_pool_count: usize,
    pub changed_rows: usize,
    pub directional_pool_version: i64,
    pub materialized_checksum: String,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DirectionalPoolWriteOutcome {
    #[serde(flatten)]
    pub result: DirectionalPoolMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DirectionalPoolStatus {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub directional_pool_version: i64,
    pub materialized_checksum: Option<String>,
    pub pool_scope_version: Option<i64>,
    pub pool_scope_checksum: Option<String>,
    pub snapshot_checksum: Option<String>,
    pub base_pool_count: usize,
    pub directional_pool_count: usize,
    pub direction0_count: usize,
    pub direction1_count: usize,
    pub eligible_pair_count: usize,
    pub measured_capacity_count: usize,
    pub measured_output_count: usize,
    pub excluded_pool_count: usize,
}

/// One row of the directional pool table.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DirectionalPoolRow {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub base_pool_id: String,
    pub direction: i64,
    pub directional_pool_id: String,
    pub pool_manager: String,
    pub pool_key_json: String,
    pub token0: String,
    pub token1: String,
    pub token0_key_id: String,
    pub token1_key_id: String,
    pub fee: i64,
    pub tick_spacing: i64,
    pub hooks: String,
    pub pair_key_id: String,
    pub input_token_key_id: String,
    pub output_token_key_id: String,
    pub zero_for_one: bool,
    pub sqrt_price_x96: Option<String>,
    pub tick: Option<i64>,
    pub liquidity: Option<String>,
    pub price: Option<f64>,
    pub depth_status: Option<String>,
    pub liquidity_read_at: Option<String>,
    pub liquidity_block_number: Option<i64>,
    pub evidence_source: Option<String>,
    pub input_capacity_raw: Option<String>,
    pub input_capacity_measured: bool,
    /// The price-impact depth capacity of the base pool, in raw units.
    ///
    /// The depth capacity does not depend on the direction, so both of the
    /// directional rows of one base pool carry the same number.
    pub depth_capacity_raw: Option<String>,
    pub directional_output: Option<f64>,
    pub directional_output_measured: bool,
    pub pair_direction_rank: Option<i64>,
    pub pool_status: String,
    pub pool_status_reason: Option<String>,
    pub source_block_number: Option<i64>,
    pub source_tx_hash: Option<String>,
    pub materialized_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DirectionalPoolSnapshot {
    pub status: DirectionalPoolStatus,
    pub rows: Vec<DirectionalPoolRow>,
}

/// The comparison of the published directional layer against the base pool
/// universe.
///
/// The directional layer is a projection, so every one of these counters is
/// expected to be zero.  A non-zero counter is a defect of the layer, not a
/// market fact.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DirectionalPoolComparison {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub base_pool_count: usize,
    pub directional_pool_count: usize,
    /// Base pools that do not appear in exactly two rows.
    pub pools_without_two_rows: usize,
    /// Rows whose direction does not match their input and output tokens.
    pub direction_mismatches: usize,
    /// Row pairs of one base pool whose shared columns differ.
    pub projection_mismatches: usize,
    /// The rows that were examined.
    pub examined_rows: usize,
    pub differences_truncated: bool,
    pub ready_for_cutover: bool,
}

#[derive(Debug, Clone)]
struct BasePool {
    pool_id: String,
    pool_manager: String,
    pool_key_json: String,
    token0: String,
    token1: String,
    token0_key_id: String,
    token1_key_id: String,
    fee: i64,
    dynamic_fee: i64,
    tick_spacing: i64,
    hooks: String,
    pool_status: String,
    status_reason: Option<String>,
    source_block_number: Option<i64>,
    source_tx_hash: Option<String>,
}

#[derive(Debug, Clone, Default)]
struct SnapshotRow {
    sqrt_price_x96: Option<String>,
    tick: Option<i64>,
    liquidity: Option<String>,
    price: Option<f64>,
    depth_status: Option<String>,
    liquidity_read_at: Option<String>,
    liquidity_block_number: Option<i64>,
    evidence_source: Option<String>,
    single_tick_max_token0_raw: Option<String>,
    single_tick_max_token1_raw: Option<String>,
    capacity_raw: Option<String>,
}

struct ScopeInput {
    pool_scope_version: i64,
    pool_scope_checksum: String,
    snapshot_checksum: String,
    pools: Vec<BasePool>,
    snapshots: BTreeMap<String, SnapshotRow>,
    decimals: BTreeMap<String, u8>,
}

/// The projection of one base pool, before the ranks are assigned.
struct ProjectedRow {
    row: DirectionalPoolRow,
    tick_ordering_rank: Option<i64>,
}

/// The fields of one published row that the comparison checks.
struct ComparedRow {
    base_pool_id: String,
    direction: i64,
    #[allow(dead_code)]
    directional_pool_id: String,
    pool_manager: String,
    pool_key_json: String,
    token0: String,
    token1: String,
    token0_key_id: String,
    token1_key_id: String,
    fee: i64,
    tick_spacing: i64,
    hooks: String,
    pair_key_id: String,
    input_token_key_id: String,
    output_token_key_id: String,
    zero_for_one: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedDirectionalWrite {
    request_id: String,
    request_digest: String,
    result: DirectionalPoolMaterializationResult,
}

/// Materialize the directional projection without writing it.
pub fn materialize_directional_pools(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    apply: bool,
) -> Result<DirectionalPoolMaterializationResult, DirectionalPoolError> {
    materialize_impl(
        database_path.as_ref(),
        network,
        chain_id,
        scope_key,
        apply,
        None,
    )
    .map(|(result, _)| result)
}

/// Materialize the directional projection and record the write request.
pub fn materialize_directional_pools_write(
    database_path: impl AsRef<Path>,
    request: DirectionalPoolWriteRequest,
) -> Result<DirectionalPoolWriteOutcome, DirectionalPoolError> {
    let (result, idempotent_replay) = materialize_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        &request.scope_key,
        request.apply,
        Some(&request),
    )?;
    Ok(DirectionalPoolWriteOutcome {
        result,
        idempotent_replay,
    })
}

/// Read the published state of the directional pool layer.
pub fn read_directional_pool_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<DirectionalPoolStatus, DirectionalPoolError> {
    validate_operation(network, chain_id, scope_key)?;
    let connection = open_connection(database_path.as_ref(), false)?;
    read_status_on_connection(&connection, network, chain_id, scope_key)
}

/// Read the published rows of the directional pool layer.
pub fn read_directional_pool_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    direction: Option<i64>,
    limit: usize,
) -> Result<DirectionalPoolSnapshot, DirectionalPoolError> {
    validate_operation(network, chain_id, scope_key)?;
    if limit == 0 || limit > MAX_SNAPSHOT_LIMIT {
        return Err(DirectionalPoolError::InvalidRequest(format!(
            "limit must be between 1 and {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    if direction.is_some_and(|value| value != 0 && value != 1) {
        return Err(DirectionalPoolError::InvalidRequest(
            "direction must be 0 or 1".to_owned(),
        ));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let status = read_status_on_connection(&connection, network, chain_id, scope_key)?;
    let mut rows = Vec::new();
    if !table_exists(&connection, DIRECTIONAL_POOL_TABLE)? {
        // Not migrated yet: the layer is empty, which is a fact and not a
        // failure.
        return Ok(DirectionalPoolSnapshot { status, rows });
    }
    let mut statement = connection.prepare(
        "SELECT network, chain_id, protocol, scope_key, base_pool_id, direction,
                directional_pool_id, pool_manager, pool_key_json, token0, token1,
                token0_key_id, token1_key_id, fee, tick_spacing, hooks, pair_key_id,
                input_token_key_id, output_token_key_id, zero_for_one,
                sqrt_price_x96, tick, liquidity, price, depth_status,
                liquidity_read_at, liquidity_block_number, evidence_source,
                input_capacity_raw, input_capacity_measured, depth_capacity_raw,
                directional_output, directional_output_measured, pair_direction_rank, pool_status,
                pool_status_reason, source_block_number, source_tx_hash,
                materialized_at, updated_at
         FROM manager_v4_directional_exchange_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR direction = ?5)
         ORDER BY pair_key_id, direction, pair_direction_rank, base_pool_id
         LIMIT ?6",
    )?;
    let rows_iter = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, scope_key, direction, limit as i64],
        |row| Ok(read_row(row)),
    )?;
    for row in rows_iter {
        rows.push(row??);
    }
    Ok(DirectionalPoolSnapshot { status, rows })
}

fn materialize_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    apply: bool,
    request: Option<&DirectionalPoolWriteRequest>,
) -> Result<(DirectionalPoolMaterializationResult, bool), DirectionalPoolError> {
    validate_operation(network, chain_id, scope_key)?;
    let prepared_write = match request {
        Some(request) => {
            validate_write_request(request, network, chain_id, scope_key, apply)?;
            Some((request, request_digest(request)?))
        }
        None => None,
    };

    let mut connection = open_connection(database_path, apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if apply {
        let transaction =
            connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let outcome = materialize_on_connection(
            &transaction,
            network,
            chain_id,
            scope_key,
            true,
            prepared_write,
        )?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        materialize_on_connection(
            &connection,
            network,
            chain_id,
            scope_key,
            false,
            prepared_write,
        )
    }
}

/// Compare the published directional layer against the base pool universe.
///
/// The check is the executable form of the three structural facts the layer
/// rests on: a base pool appears exactly twice, the two rows differ only in
/// the direction dependent fields, and the direction agrees with the input
/// and output tokens.
pub fn compare_directional_pools(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    sample_limit: usize,
) -> Result<DirectionalPoolComparison, DirectionalPoolError> {
    validate_operation(network, chain_id, scope_key)?;
    if sample_limit == 0 || sample_limit > MAX_SNAPSHOT_LIMIT {
        return Err(DirectionalPoolError::InvalidRequest(format!(
            "sample_limit must be between 1 and {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let scope = connection
        .query_row(
            "SELECT pool_table FROM pool_scope_config
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = ?3",
            params![network, chain_id, scope_key],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    let Some(pool_table) = scope else {
        // The comparison is a diagnostic: it answers whether the published
        // layer matches the base pool universe.  Without a configured scope
        // there is no universe to compare against, so the honest answer is
        // "nothing has been published yet", not a failure.
        return Ok(empty_comparison(network, chain_id, scope_key));
    };
    validate_pool_table(&pool_table)?;
    if !table_exists(&connection, DIRECTIONAL_POOL_TABLE)? {
        return Ok(empty_comparison(network, chain_id, scope_key));
    }
    let base_pool_count: i64 = connection.query_row(
        &format!(
            "SELECT COUNT(*) FROM {pool_table}
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
        ),
        params![network, chain_id, NETWORK_PROTOCOL],
        |row| row.get(0),
    )?;

    let mut statement = connection.prepare(
        "SELECT base_pool_id, direction, directional_pool_id, pool_manager,
                pool_key_json, token0, token1, token0_key_id, token1_key_id,
                fee, tick_spacing, hooks, pair_key_id, input_token_key_id,
                output_token_key_id, zero_for_one
         FROM manager_v4_directional_exchange_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY base_pool_id, direction",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, scope_key],
        |row| {
            Ok((
                ComparedRow {
                    base_pool_id: row.get(0)?,
                    direction: row.get(1)?,
                    directional_pool_id: row.get(2)?,
                    pool_manager: row.get(3)?,
                    pool_key_json: row.get(4)?,
                    token0: row.get(5)?,
                    token1: row.get(6)?,
                    token0_key_id: row.get(7)?,
                    token1_key_id: row.get(8)?,
                    fee: row.get(9)?,
                    tick_spacing: row.get(10)?,
                    hooks: row.get(11)?,
                    pair_key_id: row.get(12)?,
                    input_token_key_id: row.get(13)?,
                    output_token_key_id: row.get(14)?,
                    zero_for_one: row.get::<_, i64>(15)? != 0,
                },
            ))
        },
    )?;

    let mut groups: BTreeMap<String, Vec<ComparedRow>> = BTreeMap::new();
    let mut examined_rows = 0usize;
    let mut differences_truncated = false;
    for row in rows {
        if examined_rows >= sample_limit {
            differences_truncated = true;
            break;
        }
        let row = row?.0;
        examined_rows += 1;
        groups
            .entry(row.base_pool_id.clone())
            .or_default()
            .push(row);
    }

    let mut pools_without_two_rows = 0usize;
    let mut direction_mismatches = 0usize;
    let mut projection_mismatches = 0usize;
    for (_, entries) in &groups {
        if entries.len() != 2 {
            pools_without_two_rows += 1;
            continue;
        }
        let first = &entries[0];
        let second = &entries[1];
        // The two rows of one base pool must agree on everything that is
        // not direction dependent.
        if first.pool_manager != second.pool_manager
            || first.pool_key_json != second.pool_key_json
            || first.token0 != second.token0
            || first.token1 != second.token1
            || first.token0_key_id != second.token0_key_id
            || first.token1_key_id != second.token1_key_id
            || first.fee != second.fee
            || first.tick_spacing != second.tick_spacing
            || first.hooks != second.hooks
            || first.pair_key_id != second.pair_key_id
        {
            projection_mismatches += 1;
        }
        // The direction must agree with the tokens: direction 0 spends the
        // higher address token, direction 1 the lower one.
        for entry in entries {
            let (expected_input, expected_output, expected_zero_for_one) = match entry.direction {
                0 => (&entry.token1_key_id, &entry.token0_key_id, false),
                1 => (&entry.token0_key_id, &entry.token1_key_id, true),
                _ => (&entry.token0_key_id, &entry.token1_key_id, false),
            };
            if entry.input_token_key_id != *expected_input
                || entry.output_token_key_id != *expected_output
                || entry.zero_for_one != expected_zero_for_one
            {
                direction_mismatches += 1;
            }
        }
    }

    let directional_pool_count: i64 = connection.query_row(
        "SELECT COUNT(*) FROM manager_v4_directional_exchange_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        params![network, chain_id, NETWORK_PROTOCOL, scope_key],
        |row| row.get(0),
    )?;

    Ok(DirectionalPoolComparison {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: scope_key.to_owned(),
        base_pool_count: usize_from_i64(base_pool_count),
        directional_pool_count: usize_from_i64(directional_pool_count),
        pools_without_two_rows,
        direction_mismatches,
        projection_mismatches,
        examined_rows,
        differences_truncated,
        ready_for_cutover: pools_without_two_rows == 0
            && direction_mismatches == 0
            && projection_mismatches == 0
            && !differences_truncated,
    })
}

/// A comparison of a layer that has not been published.
///
/// Every counter is zero because there is nothing to compare, and the layer
/// is not ready for cutover because there is no layer.
fn empty_comparison(network: &str, chain_id: i64, scope_key: &str) -> DirectionalPoolComparison {
    DirectionalPoolComparison {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: scope_key.to_owned(),
        base_pool_count: 0,
        directional_pool_count: 0,
        pools_without_two_rows: 0,
        direction_mismatches: 0,
        projection_mismatches: 0,
        examined_rows: 0,
        differences_truncated: false,
        ready_for_cutover: false,
    }
}

fn materialize_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    apply: bool,
    prepared_write: Option<(&DirectionalPoolWriteRequest, String)>,
) -> Result<(DirectionalPoolMaterializationResult, bool), DirectionalPoolError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(evidence_json) = find_write_request(connection, request)? {
                let persisted = serde_json::from_str::<PersistedDirectionalWrite>(&evidence_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(DirectionalPoolError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok((persisted.result, true));
            }
        }
    }

    let input = read_scope_input(connection, network, chain_id, scope_key)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &input)?;
    }
    let desired = build_directional_rows(&input, network, chain_id, scope_key)?;
    let materialized_checksum = directional_checksum(&input, &desired)?;
    let previous = read_status_on_connection(connection, network, chain_id, scope_key)?;
    let counts_match = previous.enabled
        && previous.directional_pool_count == desired.len()
        && previous.direction0_count == desired.direction0_count()
        && previous.direction1_count == desired.direction1_count();
    let checksum_changed = previous.materialized_checksum.as_deref()
        != Some(materialized_checksum.as_str())
        || !counts_match;
    let directional_pool_version = if checksum_changed {
        previous.directional_pool_version.saturating_add(1).max(1)
    } else {
        previous.directional_pool_version.max(1)
    };
    let changed_rows = if checksum_changed { desired.len() } else { 0 };
    let result = DirectionalPoolMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: scope_key.to_owned(),
        apply,
        dry_run: !apply,
        pool_scope_version: input.pool_scope_version,
        pool_scope_checksum: input.pool_scope_checksum.clone(),
        snapshot_checksum: input.snapshot_checksum.clone(),
        base_pool_count: input.pools.len(),
        directional_pool_count: desired.len(),
        direction0_count: desired.direction0_count(),
        direction1_count: desired.direction1_count(),
        eligible_pair_count: desired.eligible_pair_count(),
        measured_capacity_count: desired.measured_capacity_count(),
        measured_output_count: desired.measured_output_count(),
        excluded_pool_count: desired.excluded_pool_count,
        changed_rows,
        directional_pool_version,
        materialized_checksum,
        consumers_need_rebuild: checksum_changed,
    };

    if apply {
        if checksum_changed {
            replace_directional_rows(connection, network, chain_id, scope_key, &desired, &result)?;
        }
        if let Some((request, request_digest)) = prepared_write {
            persist_write_request(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

fn validate_operation(
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<(), DirectionalPoolError> {
    if network.trim().is_empty() {
        return Err(DirectionalPoolError::InvalidRequest(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(DirectionalPoolError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if scope_key.trim().is_empty() {
        return Err(DirectionalPoolError::InvalidRequest(
            "scope_key must not be empty".to_owned(),
        ));
    }
    Ok(())
}

fn validate_write_request(
    request: &DirectionalPoolWriteRequest,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    apply: bool,
) -> Result<(), DirectionalPoolError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(DirectionalPoolError::InvalidRequest(
            "write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.scope_key != scope_key {
        return Err(DirectionalPoolError::InvalidRequest(
            "write request scope_key does not match the operation".to_owned(),
        ));
    }
    if request.apply != apply {
        return Err(DirectionalPoolError::InvalidRequest(
            "write request apply flag does not match the operation".to_owned(),
        ));
    }
    validate_write_token("operation", &request.operation, MAX_REQUEST_ID_LENGTH)?;
    validate_write_token("X-Request-Id", &request.request_id, MAX_REQUEST_ID_LENGTH)?;
    validate_write_token(
        "Idempotency-Key",
        &request.idempotency_key,
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )
}

fn validate_write_token(
    label: &str,
    value: &str,
    max_length: usize,
) -> Result<(), DirectionalPoolError> {
    if value.is_empty() || value.len() > max_length {
        return Err(DirectionalPoolError::InvalidRequest(format!(
            "{label} must contain 1..={max_length} bytes"
        )));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':'))
    {
        return Err(DirectionalPoolError::InvalidRequest(format!(
            "{label} contains unsupported characters"
        )));
    }
    Ok(())
}

fn request_digest(
    request: &DirectionalPoolWriteRequest,
) -> Result<String, DirectionalPoolError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "scopeKey": request.scope_key,
        "operation": request.operation,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&payload)?))
    ))
}

fn validate_expected_upstream(
    expected: &ExpectedUpstream,
    input: &ScopeInput,
) -> Result<(), DirectionalPoolError> {
    if let Some(expected_version) = expected.pool_scope_version {
        if expected_version != input.pool_scope_version {
            return Err(DirectionalPoolError::InvalidRequest(format!(
                "pool scope version mismatch: expected {expected_version}, found {}",
                input.pool_scope_version
            )));
        }
    }
    if let Some(expected_checksum) = expected.pool_scope_checksum.as_deref() {
        if expected_checksum != input.pool_scope_checksum {
            return Err(DirectionalPoolError::InvalidRequest(
                "pool scope checksum mismatch".to_owned(),
            ));
        }
    }
    if let Some(expected_checksum) = expected.snapshot_checksum.as_deref() {
        if expected_checksum != input.snapshot_checksum {
            return Err(DirectionalPoolError::InvalidRequest(
                "snapshot checksum mismatch".to_owned(),
            ));
        }
    }
    Ok(())
}

/// Read everything the projection needs.
fn read_scope_input(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<ScopeInput, DirectionalPoolError> {
    let scope = connection
        .query_row(
            "SELECT pool_table, materialized_checksum, library_versions_json, enabled
             FROM pool_scope_config
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = ?3",
            params![network, chain_id, scope_key],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)? != 0,
                ))
            },
        )
        .optional()?;
    let Some((pool_table, checksum, library_versions_json, enabled)) = scope else {
        return Err(DirectionalPoolError::MissingPoolScope(scope_key.to_owned()));
    };
    if !enabled {
        return Err(DirectionalPoolError::MissingPoolScope(scope_key.to_owned()));
    }
    let pool_scope_checksum = checksum
        .ok_or_else(|| DirectionalPoolError::MissingPoolScope(scope_key.to_owned()))?;
    validate_pool_table(&pool_table)?;

    let library_versions: BTreeMap<String, i64> =
        serde_json::from_str(&library_versions_json).unwrap_or_default();
    // The scope version is the highest library version the scope is built on.
    // It moves whenever one of the underlying libraries moves, which is
    // exactly when the projection has to be rebuilt.
    let pool_scope_version = library_versions.values().copied().max().unwrap_or(0);

    let pools = read_scope_pools(connection, network, chain_id, &pool_table)?;
    let snapshots = read_snapshot_rows(connection, network, chain_id)?;
    let decimals = read_token_decimals(connection, network, chain_id)?;
    let snapshot_checksum = snapshot_checksum(&snapshots)?;

    Ok(ScopeInput {
        pool_scope_version,
        pool_scope_checksum,
        snapshot_checksum,
        pools,
        snapshots,
        decimals,
    })
}

/// A scope pool table name comes out of the database, so it is checked
/// against the shape of the pool scope tables before it is interpolated into
/// a statement.
fn validate_pool_table(table: &str) -> Result<(), DirectionalPoolError> {
    let valid = table.starts_with("v4_exchange_pools_")
        && table.len() > "v4_exchange_pools_".len()
        && table
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_');
    if valid {
        Ok(())
    } else {
        Err(DirectionalPoolError::UnsupportedPoolTable(table.to_owned()))
    }
}

fn read_scope_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_table: &str,
) -> Result<Vec<BasePool>, DirectionalPoolError> {
    let has_status_reason = table_has_column(connection, pool_table, "status_reason")?;
    let reason_expression = if has_status_reason {
        "status_reason"
    } else {
        "NULL"
    };
    let sql = format!(
        "SELECT pool_id, pool_manager, pool_key_json, token0, token1,
                token0_key_id, token1_key_id, fee, dynamic_fee, tick_spacing,
                hooks, pool_status, {reason_expression},
                source_block_number, source_tx_hash
         FROM {pool_table}
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
         ORDER BY pool_id"
    );
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(params![network, chain_id, NETWORK_PROTOCOL], |row| {
        Ok(BasePool {
            pool_id: row.get(0)?,
            pool_manager: row.get(1)?,
            pool_key_json: row.get(2)?,
            token0: row.get(3)?,
            token1: row.get(4)?,
            token0_key_id: row.get(5)?,
            token1_key_id: row.get(6)?,
            fee: row.get(7)?,
            dynamic_fee: row.get(8)?,
            tick_spacing: row.get(9)?,
            hooks: row.get(10)?,
            pool_status: row.get(11)?,
            status_reason: row.get(12)?,
            source_block_number: row.get(13)?,
            source_tx_hash: row.get(14)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn table_has_column(
    connection: &Connection,
    table: &str,
    column: &str,
) -> Result<bool, DirectionalPoolError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let names = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(names.iter().any(|name| name == column))
}

/// Whether a table is present in the store.
///
/// The directional tables come from a migration, so a store that has not
/// been migrated yet simply does not have them.  That is a normal state of
/// the world for a reader and must not be reported as a broken database.
fn table_exists(connection: &Connection, table: &str) -> Result<bool, DirectionalPoolError> {
    let exists: i64 = connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
        params![table],
        |row| row.get(0),
    )?;
    Ok(exists > 0)
}

fn read_snapshot_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, SnapshotRow>, DirectionalPoolError> {
    let exists: i64 = connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master
         WHERE type = 'table' AND name = 'v4_exchange_pool_liquidity_snapshots'",
        [],
        |row| row.get(0),
    )?;
    if exists == 0 {
        return Err(DirectionalPoolError::MissingSnapshotTable);
    }
    let has_single_tick = table_has_column(
        connection,
        "v4_exchange_pool_liquidity_snapshots",
        "single_tick_max_token0_raw",
    )?;
    let capacity_expression = if has_single_tick {
        "single_tick_max_token0_raw, single_tick_max_token1_raw"
    } else {
        "NULL, NULL"
    };
    // The depth capacity is optional: a database that has never run the
    // depth rollout simply does not carry the column, and the projection
    // then publishes no depth capacity at all.
    let has_depth_capacity = table_has_column(
        connection,
        "v4_exchange_pool_liquidity_snapshots",
        "capacity_raw",
    )?;
    let depth_capacity_expression = if has_depth_capacity {
        "capacity_raw"
    } else {
        "NULL"
    };
    let sql = format!(
        "SELECT pool_id, sqrt_price_x96, tick, liquidity, price, depth_status,
                liquidity_read_at, liquidity_block_number, evidence_source,
                {capacity_expression}, {depth_capacity_expression}
         FROM v4_exchange_pool_liquidity_snapshots
         WHERE network = ?1 AND chain_id = ?2"
    );
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            SnapshotRow {
                sqrt_price_x96: row.get(1)?,
                tick: row.get(2)?,
                liquidity: row.get(3)?,
                price: row.get(4)?,
                depth_status: row.get(5)?,
                liquidity_read_at: row.get(6)?,
                liquidity_block_number: row.get(7)?,
                evidence_source: row.get(8)?,
                single_tick_max_token0_raw: row.get(9)?,
                single_tick_max_token1_raw: row.get(10)?,
                capacity_raw: row.get(11)?,
            },
        ))
    })?;
    let mut snapshots = BTreeMap::new();
    for row in rows {
        let (pool_id, snapshot) = row?;
        snapshots.insert(pool_id, snapshot);
    }
    Ok(snapshots)
}

fn read_token_decimals(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, u8>, DirectionalPoolError> {
    let mut statement = connection.prepare(
        "SELECT token_key_id, token_decimals
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    })?;
    let mut decimals = BTreeMap::new();
    for row in rows {
        let (token_key_id, value) = row?;
        decimals.insert(token_key_id, value.clamp(0, 38) as u8);
    }
    Ok(decimals)
}

fn snapshot_checksum(
    snapshots: &BTreeMap<String, SnapshotRow>,
) -> Result<String, DirectionalPoolError> {
    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct Payload<'a> {
        pool_id: &'a str,
        sqrt_price_x96: &'a Option<String>,
        tick: &'a Option<i64>,
        liquidity: &'a Option<String>,
        price: &'a Option<f64>,
        depth_status: &'a Option<String>,
        liquidity_read_at: &'a Option<String>,
        liquidity_block_number: &'a Option<i64>,
        evidence_source: &'a Option<String>,
        single_tick_max_token0_raw: &'a Option<String>,
        single_tick_max_token1_raw: &'a Option<String>,
        capacity_raw: &'a Option<String>,
    }
    let payload: Vec<Payload<'_>> = snapshots
        .iter()
        .map(|(pool_id, snapshot)| Payload {
            pool_id,
            sqrt_price_x96: &snapshot.sqrt_price_x96,
            tick: &snapshot.tick,
            liquidity: &snapshot.liquidity,
            price: &snapshot.price,
            depth_status: &snapshot.depth_status,
            liquidity_read_at: &snapshot.liquidity_read_at,
            liquidity_block_number: &snapshot.liquidity_block_number,
            evidence_source: &snapshot.evidence_source,
            single_tick_max_token0_raw: &snapshot.single_tick_max_token0_raw,
            single_tick_max_token1_raw: &snapshot.single_tick_max_token1_raw,
            capacity_raw: &snapshot.capacity_raw,
        })
        .collect();
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&payload)?))
    ))
}

/// The projection of the base pool table onto the directional table.
struct DesiredDirectionalData {
    rows: Vec<DirectionalPoolRow>,
    excluded_pool_count: usize,
}

impl DesiredDirectionalData {
    fn len(&self) -> usize {
        self.rows.len()
    }

    fn direction0_count(&self) -> usize {
        self.rows.iter().filter(|row| row.direction == 0).count()
    }

    fn direction1_count(&self) -> usize {
        self.rows.iter().filter(|row| row.direction == 1).count()
    }

    fn measured_capacity_count(&self) -> usize {
        self.rows.iter().filter(|row| row.input_capacity_measured).count()
    }

    fn measured_output_count(&self) -> usize {
        self.rows
            .iter()
            .filter(|row| row.directional_output_measured)
            .count()
    }

    /// The number of (pair, direction) groups that carry at least one
    /// ranked row, which is the number of directions the closed loop can
    /// actually walk.
    fn eligible_pair_count(&self) -> usize {
        self.rows
            .iter()
            .filter(|row| row.pair_direction_rank.is_some())
            .map(|row| (row.pair_key_id.clone(), row.direction))
            .collect::<BTreeSet<_>>()
            .len()
    }
}

/// Parses a stored 128-bit quantity.
///
/// The snapshot table stores the 256-bit and 128-bit values as **text**,
/// because they do not fit an SQLite integer. The accepted spelling is
/// decimal or a `0x`-prefixed hexadecimal literal, which is the same rule
/// the pool manager's own readers apply.
fn parse_u128(value: &str) -> Option<u128> {
    let text = value.trim();
    if text.is_empty() {
        return None;
    }
    if let Some(hex) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        return u128::from_str_radix(hex, 16).ok();
    }
    text.parse::<u128>().ok()
}

/// Recomputes the single-tick bounds of one snapshot row.
///
/// TASK-172 / AC-221. The values come from the observation the row already
/// carries, so the bound and the price describe the same on-chain moment.
/// A missing observation yields a missing bound, exactly as the stored
/// `NULL` does: "not measured" is never folded into "measured as zero".
fn recompute_single_tick_bounds(
    snapshot: &SnapshotRow,
    pool: &BasePool,
) -> (Option<String>, Option<String>) {
    let Some(sqrt_price_x96) = snapshot.sqrt_price_x96.as_deref().and_then(parse_u128) else {
        return (None, None);
    };
    let Some(liquidity) = snapshot.liquidity.as_deref().and_then(parse_u128) else {
        return (None, None);
    };
    let Some(tick) = snapshot.tick else {
        return (None, None);
    };
    // The spacing decides the granularity of the tick range, so the bound
    // cannot be located without it. A pool whose spacing is unknown keeps
    // no bound, which is what the pool manager publishes as well.
    let Ok(tick_spacing) = i32::try_from(pool.tick_spacing) else {
        return (None, None);
    };
    if tick_spacing <= 0 {
        return (None, None);
    }
    let fee_pips = u32::try_from(pool.fee).unwrap_or(0);
    let (token0_bound, token1_bound) = pool_manager::depth::recompute_single_tick_bounds(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        tick_spacing,
        tick,
    );
    (
        token0_bound.map(|value| value.to_string()),
        token1_bound.map(|value| value.to_string()),
    )
}

fn build_directional_rows(
    input: &ScopeInput,
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<DesiredDirectionalData, DirectionalPoolError> {
    let now = chrono::Utc::now().to_rfc3339();
    let mut projected: Vec<ProjectedRow> = Vec::new();
    let mut excluded_pool_count = 0usize;

    for pool in &input.pools {
        // The standing rules of the system: a dynamic fee pool and a pool
        // with a hook are not pools the off-chain arithmetic may price, so
        // they are not projected at all.
        if pool_manager::depth::is_dynamic_fee_pool(pool.fee, pool.dynamic_fee) {
            excluded_pool_count += 1;
            continue;
        }
        if !hooks_is_absent(&pool.hooks) {
            excluded_pool_count += 1;
            continue;
        }
        if !token_order_is_canonical(&pool.token0, &pool.token1) {
            excluded_pool_count += 1;
            continue;
        }

        let pair_key_id = PairKeyId::new(chain_id as u64, &pool.token0, &pool.token1)
            .map_err(|error| DirectionalPoolError::InvalidRequest(error.to_string()))?
            .as_str()
            .to_owned();
        let snapshot = input.snapshots.get(&pool.pool_id);

        for direction in PoolDirection::ALL {
            let input_token_key_id = match direction.input_token_index() {
                0 => pool.token0_key_id.as_str(),
                _ => pool.token1_key_id.as_str(),
            };
            let output_token_key_id = match direction.input_token_index() {
                0 => pool.token1_key_id.as_str(),
                _ => pool.token0_key_id.as_str(),
            };
            let directional_pool_id = DirectionalPoolId::new(
                network,
                chain_id as u64,
                scope_key,
                &pool.pool_id,
                direction.raw() as u8,
            )
            .map_err(|error| DirectionalPoolError::InvalidRequest(error.to_string()))?
            .as_str()
            .to_owned();

            // The capacity of this direction: the input side of the pool.
            //
            // TASK-172 / AC-221: the bound is **recomputed** from the
            // observation this row already holds (price, liquidity, tick,
            // fee, spacing) instead of being read from the stored
            // `single_tick_max_token*_raw` columns.
            //
            // The stored bound is the output of the same arithmetic, but it
            // was published by a **writer that also wrote the price**,
            // whereas this row's price may come from a different
            // observation. Reading the stored bound would therefore publish
            // a number that belongs to another on-chain moment. Recomputing
            // binds the bound to the row's own observation, which is what
            // makes the row self-consistent.
            //
            // The recomputation stays in Rust (DPO-10): it calls the pool
            // manager's own boundary arithmetic, so the value is identical
            // to the stored one whenever the observation is the same.
            let capacity = match snapshot {
                Some(snapshot) => {
                    let (token0_bound, token1_bound) =
                        recompute_single_tick_bounds(snapshot, pool);
                    match direction {
                        // Direction 0 spends token1, so it carries the token1
                        // bound; direction 1 spends token0.
                        PoolDirection::HighToLow => token1_bound,
                        PoolDirection::LowToHigh => token0_bound,
                    }
                }
                None => None,
            };

            // The price quality of this direction.
            let token_in_decimals = input.decimals.get(input_token_key_id).copied();
            let token_out_decimals = input.decimals.get(output_token_key_id).copied();
            let price = match (token_in_decimals, token_out_decimals) {
                (Some(token_in_decimals), Some(token_out_decimals)) => {
                    price_directional_output(
                        &DirectionalPriceInput {
                            pool_id: pool.pool_id.clone(),
                            token0_key_id: pool.token0_key_id.clone(),
                            token1_key_id: pool.token1_key_id.clone(),
                            fee: pool.fee,
                            tick_spacing: pool.tick_spacing,
                            sqrt_price_x96: snapshot.and_then(|item| item.sqrt_price_x96.clone()),
                            tick: snapshot.and_then(|item| item.tick),
                            liquidity: snapshot.and_then(|item| item.liquidity.clone()),
                        },
                        input_token_key_id,
                        token_in_decimals,
                        token_out_decimals,
                    )
                }
                _ => None,
            };

            let row = DirectionalPoolRow {
                network: network.to_owned(),
                chain_id,
                protocol: NETWORK_PROTOCOL.to_owned(),
                scope_key: scope_key.to_owned(),
                base_pool_id: pool.pool_id.clone(),
                direction: direction.raw(),
                directional_pool_id,
                pool_manager: pool.pool_manager.clone(),
                pool_key_json: pool.pool_key_json.clone(),
                token0: pool.token0.clone(),
                token1: pool.token1.clone(),
                token0_key_id: pool.token0_key_id.clone(),
                token1_key_id: pool.token1_key_id.clone(),
                fee: pool.fee,
                tick_spacing: pool.tick_spacing,
                hooks: pool.hooks.clone(),
                pair_key_id: pair_key_id.clone(),
                input_token_key_id: input_token_key_id.to_owned(),
                output_token_key_id: output_token_key_id.to_owned(),
                zero_for_one: direction.zero_for_one(),
                sqrt_price_x96: snapshot.and_then(|item| item.sqrt_price_x96.clone()),
                tick: snapshot.and_then(|item| item.tick),
                liquidity: snapshot.and_then(|item| item.liquidity.clone()),
                price: snapshot.and_then(|item| item.price),
                depth_status: snapshot.and_then(|item| item.depth_status.clone()),
                liquidity_read_at: snapshot.and_then(|item| item.liquidity_read_at.clone()),
                liquidity_block_number: snapshot.and_then(|item| item.liquidity_block_number),
                evidence_source: snapshot.and_then(|item| item.evidence_source.clone()),
                input_capacity_raw: capacity.clone(),
                input_capacity_measured: capacity.is_some(),
                depth_capacity_raw: snapshot.and_then(|item| item.capacity_raw.clone()),
                directional_output: price.as_ref().map(|price| price.output_rate),
                directional_output_measured: price.is_some(),
                pair_direction_rank: None,
                pool_status: pool.pool_status.clone(),
                pool_status_reason: pool.status_reason.clone(),
                source_block_number: pool.source_block_number,
                source_tx_hash: pool.source_tx_hash.clone(),
                materialized_at: now.clone(),
                updated_at: now.clone(),
            };
            projected.push(ProjectedRow {
                row,
                tick_ordering_rank: price.and_then(|price| price.tick_ordering_rank),
            });
        }
    }

    assign_ranks(&mut projected);
    let rows = projected.into_iter().map(|projected| projected.row).collect();
    Ok(DesiredDirectionalData {
        rows,
        excluded_pool_count,
    })
}

/// Assign the 1-based rank inside every (pair, direction) group.
///
/// The main order is the price quality: the directional output descending,
/// then the tick order descending, then the fee ascending, and finally the
/// base pool id ascending so that the order never depends on the row order
/// of the database.
///
/// One key sits between the fee and the pool id: when the main order keys
/// are all equal, the pool with the **larger** direction capacity wins, and
/// only a capacity tie falls through to the pool id.  The capacity is
/// denominated in the token the direction spends, and the comparison happens
/// inside one `(pair, direction)` group, so the two capacities are the same
/// unit — comparing them across pairs would be a unit error.
///
/// Only a row that may be selected takes a rank: the pool has to be active
/// and its direction has to be priced.  A row that is not priced has no
/// rank, because "not measured" and "measured as zero" are different facts.
fn assign_ranks(rows: &mut [ProjectedRow]) {
    let mut groups: BTreeMap<(String, i64), Vec<usize>> = BTreeMap::new();
    for (index, projected) in rows.iter().enumerate() {
        if projected.row.pool_status != "active" || !projected.row.directional_output_measured {
            continue;
        }
        groups
            .entry((projected.row.pair_key_id.clone(), projected.row.direction))
            .or_default()
            .push(index);
    }
    for indices in groups.values_mut() {
        indices.sort_by(|left, right| {
            let left_row = &rows[*left].row;
            let right_row = &rows[*right].row;
            let left_output = left_row.directional_output.unwrap_or_default();
            let right_output = right_row.directional_output.unwrap_or_default();
            right_output
                .partial_cmp(&left_output)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| {
                    rows[*right]
                        .tick_ordering_rank
                        .unwrap_or(i64::MIN)
                        .cmp(&rows[*left].tick_ordering_rank.unwrap_or(i64::MIN))
                })
                .then_with(|| left_row.fee.cmp(&right_row.fee))
                // The capacity is the tie breaker of the main order: the
                // larger capacity wins.  A pool whose capacity was never
                // measured has none to offer, so it loses the comparison.
                .then_with(|| {
                    direction_capacity(left_row).cmp(&direction_capacity(right_row))
                })
                .then_with(|| left_row.base_pool_id.cmp(&right_row.base_pool_id))
        });
        for (position, index) in indices.iter().enumerate() {
            rows[*index].row.pair_direction_rank = Some(position as i64 + 1);
        }
    }
}

/// The capacity of a row in the token its direction spends.
///
/// An unmeasured capacity is **not** a zero capacity: it is the absence of
/// a measurement, and it loses every comparison against a measured one.
fn direction_capacity(row: &DirectionalPoolRow) -> Option<u128> {
    row.input_capacity_raw
        .as_deref()
        .map(str::trim)
        .and_then(|text| parse_u128(text))
}

fn hooks_is_absent(hooks: &str) -> bool {
    let normalized = hooks.trim().to_ascii_lowercase();
    normalized.is_empty() || normalized == ZERO_ADDRESS
}

fn token_order_is_canonical(token0: &str, token1: &str) -> bool {
    token0.trim().to_ascii_lowercase() < token1.trim().to_ascii_lowercase()
}

fn directional_checksum(
    input: &ScopeInput,
    desired: &DesiredDirectionalData,
) -> Result<String, DirectionalPoolError> {
    /// The significant fields of one row.
    ///
    /// The timestamps are deliberately left out: they change on every run,
    /// and a checksum that changes when nothing changed would rebuild the
    /// layer for ever.
    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct RowPayload<'a> {
        base_pool_id: &'a str,
        direction: i64,
        directional_pool_id: &'a str,
        pool_manager: &'a str,
        pool_key_json: &'a str,
        token0: &'a str,
        token1: &'a str,
        token0_key_id: &'a str,
        token1_key_id: &'a str,
        fee: i64,
        tick_spacing: i64,
        hooks: &'a str,
        pair_key_id: &'a str,
        input_token_key_id: &'a str,
        output_token_key_id: &'a str,
        zero_for_one: bool,
        sqrt_price_x96: &'a Option<String>,
        tick: Option<i64>,
        liquidity: &'a Option<String>,
        price: Option<f64>,
        depth_status: &'a Option<String>,
        input_capacity_raw: &'a Option<String>,
        input_capacity_measured: bool,
        directional_output: Option<f64>,
        directional_output_measured: bool,
        pair_direction_rank: Option<i64>,
        pool_status: &'a str,
        pool_status_reason: &'a Option<String>,
    }

    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct Payload<'a> {
        pool_scope_version: i64,
        pool_scope_checksum: &'a str,
        snapshot_checksum: &'a str,
        rows: Vec<RowPayload<'a>>,
    }
    let payload = Payload {
        pool_scope_version: input.pool_scope_version,
        pool_scope_checksum: &input.pool_scope_checksum,
        snapshot_checksum: &input.snapshot_checksum,
        rows: desired
            .rows
            .iter()
            .map(|row| RowPayload {
                base_pool_id: &row.base_pool_id,
                direction: row.direction,
                directional_pool_id: &row.directional_pool_id,
                pool_manager: &row.pool_manager,
                pool_key_json: &row.pool_key_json,
                token0: &row.token0,
                token1: &row.token1,
                token0_key_id: &row.token0_key_id,
                token1_key_id: &row.token1_key_id,
                fee: row.fee,
                tick_spacing: row.tick_spacing,
                hooks: &row.hooks,
                pair_key_id: &row.pair_key_id,
                input_token_key_id: &row.input_token_key_id,
                output_token_key_id: &row.output_token_key_id,
                zero_for_one: row.zero_for_one,
                sqrt_price_x96: &row.sqrt_price_x96,
                tick: row.tick,
                liquidity: &row.liquidity,
                price: row.price,
                depth_status: &row.depth_status,
                input_capacity_raw: &row.input_capacity_raw,
                input_capacity_measured: row.input_capacity_measured,
                directional_output: row.directional_output,
                directional_output_measured: row.directional_output_measured,
                pair_direction_rank: row.pair_direction_rank,
                pool_status: &row.pool_status,
                pool_status_reason: &row.pool_status_reason,
            })
            .collect(),
    };
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&payload)?))
    ))
}

fn replace_directional_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    desired: &DesiredDirectionalData,
    result: &DirectionalPoolMaterializationResult,
) -> Result<(), DirectionalPoolError> {
    let now = chrono::Utc::now().to_rfc3339();
    connection.execute(
        "DELETE FROM manager_v4_directional_exchange_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        params![network, chain_id, NETWORK_PROTOCOL, scope_key],
    )?;
    for row in &desired.rows {
        connection.execute(
            "INSERT INTO manager_v4_directional_exchange_pools (
                 network, chain_id, protocol, scope_key, base_pool_id, direction,
                 directional_pool_id, pool_manager, pool_key_json, token0, token1,
                 token0_key_id, token1_key_id, fee, tick_spacing, hooks, pair_key_id,
                 input_token_key_id, output_token_key_id, zero_for_one,
                 sqrt_price_x96, tick, liquidity, price, depth_status,
                 liquidity_read_at, liquidity_block_number, evidence_source,
                 input_capacity_raw, input_capacity_measured, directional_output,
                 depth_capacity_raw, directional_output_measured, pair_direction_rank, pool_status,
                 pool_status_reason, source_block_number, source_tx_hash,
                 materialized_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13,
                       ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?24, ?25,
                       ?26, ?27, ?28, ?29, ?30, ?31, ?32, ?33, ?34, ?35, ?36, ?37,
                       ?38, ?39, ?40)",
            params_from_iter(directional_row_params(row)),
        )?;
    }
    insert_state(connection, network, chain_id, scope_key, result, &now)?;
    Ok(())
}

/// The bound parameters of one directional pool row, in the order of the
/// insert statement.
fn directional_row_params(row: &DirectionalPoolRow) -> Vec<rusqlite::types::Value> {
    vec![
        row.network.clone().into(),
        row.chain_id.into(),
        row.protocol.clone().into(),
        row.scope_key.clone().into(),
        row.base_pool_id.clone().into(),
        row.direction.into(),
        row.directional_pool_id.clone().into(),
        row.pool_manager.clone().into(),
        row.pool_key_json.clone().into(),
        row.token0.clone().into(),
        row.token1.clone().into(),
        row.token0_key_id.clone().into(),
        row.token1_key_id.clone().into(),
        row.fee.into(),
        row.tick_spacing.into(),
        row.hooks.clone().into(),
        row.pair_key_id.clone().into(),
        row.input_token_key_id.clone().into(),
        row.output_token_key_id.clone().into(),
        i64::from(row.zero_for_one).into(),
        row.sqrt_price_x96.clone().into(),
        row.tick.into(),
        row.liquidity.clone().into(),
        row.price.into(),
        row.depth_status.clone().into(),
        row.liquidity_read_at.clone().into(),
        row.liquidity_block_number.into(),
        row.evidence_source.clone().into(),
        row.input_capacity_raw.clone().into(),
        i64::from(row.input_capacity_measured).into(),
        row.directional_output.into(),
        row.depth_capacity_raw.clone().into(),
        i64::from(row.directional_output_measured).into(),
        row.pair_direction_rank.into(),
        row.pool_status.clone().into(),
        row.pool_status_reason.clone().into(),
        row.source_block_number.into(),
        row.source_tx_hash.clone().into(),
        row.materialized_at.clone().into(),
        row.updated_at.clone().into(),
    ]
}

fn insert_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    result: &DirectionalPoolMaterializationResult,
    now: &str,
) -> Result<(), DirectionalPoolError> {
    connection.execute(
        "INSERT INTO manager_v4_directional_pool_library_state (
             network, chain_id, protocol, scope_key, directional_pool_version,
             materialized_checksum, pool_scope_version, pool_scope_checksum,
             snapshot_checksum, library_versions_json, library_checksums_json,
             base_pool_count, directional_pool_count, direction0_count,
             direction1_count, eligible_pair_count, measured_capacity_count,
             measured_output_count, excluded_pool_count, generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14,
                   ?15, ?16, ?17, ?18, ?19, ?20, ?20)
         ON CONFLICT(network, chain_id, protocol, scope_key) DO UPDATE SET
             directional_pool_version = excluded.directional_pool_version,
             materialized_checksum = excluded.materialized_checksum,
             pool_scope_version = excluded.pool_scope_version,
             pool_scope_checksum = excluded.pool_scope_checksum,
             snapshot_checksum = excluded.snapshot_checksum,
             library_versions_json = excluded.library_versions_json,
             library_checksums_json = excluded.library_checksums_json,
             base_pool_count = excluded.base_pool_count,
             directional_pool_count = excluded.directional_pool_count,
             direction0_count = excluded.direction0_count,
             direction1_count = excluded.direction1_count,
             eligible_pair_count = excluded.eligible_pair_count,
             measured_capacity_count = excluded.measured_capacity_count,
             measured_output_count = excluded.measured_output_count,
             excluded_pool_count = excluded.excluded_pool_count,
             generated_at = excluded.generated_at,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            scope_key,
            result.directional_pool_version,
            result.materialized_checksum,
            result.pool_scope_version,
            result.pool_scope_checksum,
            result.snapshot_checksum,
            "{}",
            "{}",
            result.base_pool_count as i64,
            result.directional_pool_count as i64,
            result.direction0_count as i64,
            result.direction1_count as i64,
            result.eligible_pair_count as i64,
            result.measured_capacity_count as i64,
            result.measured_output_count as i64,
            result.excluded_pool_count as i64,
            now,
        ],
    )?;
    Ok(())
}

fn read_status_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<DirectionalPoolStatus, DirectionalPoolError> {
    if !table_exists(connection, DIRECTIONAL_POOL_STATE_TABLE)? {
        // The layer has not been migrated yet.  It is off, not broken.
        return Ok(DirectionalPoolStatus {
            enabled: false,
            network: network.to_owned(),
            chain_id,
            protocol: NETWORK_PROTOCOL.to_owned(),
            scope_key: scope_key.to_owned(),
            directional_pool_version: 0,
            materialized_checksum: None,
            pool_scope_version: None,
            pool_scope_checksum: None,
            snapshot_checksum: None,
            base_pool_count: 0,
            directional_pool_count: 0,
            direction0_count: 0,
            direction1_count: 0,
            eligible_pair_count: 0,
            measured_capacity_count: 0,
            measured_output_count: 0,
            excluded_pool_count: 0,
        });
    }
    let stored = connection
        .query_row(
            "SELECT directional_pool_version, materialized_checksum,
                    pool_scope_version, pool_scope_checksum, snapshot_checksum,
                    base_pool_count, directional_pool_count, direction0_count,
                    direction1_count, eligible_pair_count,
                    measured_capacity_count, measured_output_count,
                    excluded_pool_count
             FROM manager_v4_directional_pool_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4",
            params![network, chain_id, NETWORK_PROTOCOL, scope_key],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, i64>(7)?,
                    row.get::<_, i64>(8)?,
                    row.get::<_, i64>(9)?,
                    row.get::<_, i64>(10)?,
                    row.get::<_, i64>(11)?,
                    row.get::<_, i64>(12)?,
                ))
            },
        )
        .optional()?;
    let Some((
        directional_pool_version,
        materialized_checksum,
        pool_scope_version,
        pool_scope_checksum,
        snapshot_checksum,
        base_pool_count,
        directional_pool_count,
        direction0_count,
        direction1_count,
        eligible_pair_count,
        measured_capacity_count,
        measured_output_count,
        excluded_pool_count,
    )) = stored
    else {
        return Ok(DirectionalPoolStatus {
            enabled: false,
            network: network.to_owned(),
            chain_id,
            protocol: NETWORK_PROTOCOL.to_owned(),
            scope_key: scope_key.to_owned(),
            directional_pool_version: 0,
            materialized_checksum: None,
            pool_scope_version: None,
            pool_scope_checksum: None,
            snapshot_checksum: None,
            base_pool_count: 0,
            directional_pool_count: 0,
            direction0_count: 0,
            direction1_count: 0,
            eligible_pair_count: 0,
            measured_capacity_count: 0,
            measured_output_count: 0,
            excluded_pool_count: 0,
        });
    };
    Ok(DirectionalPoolStatus {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: scope_key.to_owned(),
        directional_pool_version,
        materialized_checksum: Some(materialized_checksum),
        pool_scope_version: Some(pool_scope_version),
        pool_scope_checksum: Some(pool_scope_checksum),
        snapshot_checksum: Some(snapshot_checksum),
        base_pool_count: usize_from_i64(base_pool_count),
        directional_pool_count: usize_from_i64(directional_pool_count),
        direction0_count: usize_from_i64(direction0_count),
        direction1_count: usize_from_i64(direction1_count),
        eligible_pair_count: usize_from_i64(eligible_pair_count),
        measured_capacity_count: usize_from_i64(measured_capacity_count),
        measured_output_count: usize_from_i64(measured_output_count),
        excluded_pool_count: usize_from_i64(excluded_pool_count),
    })
}

fn read_row(row: &Row<'_>) -> rusqlite::Result<DirectionalPoolRow> {
    Ok(DirectionalPoolRow {
        network: row.get(0)?,
        chain_id: row.get(1)?,
        protocol: row.get(2)?,
        scope_key: row.get(3)?,
        base_pool_id: row.get(4)?,
        direction: row.get(5)?,
        directional_pool_id: row.get(6)?,
        pool_manager: row.get(7)?,
        pool_key_json: row.get(8)?,
        token0: row.get(9)?,
        token1: row.get(10)?,
        token0_key_id: row.get(11)?,
        token1_key_id: row.get(12)?,
        fee: row.get(13)?,
        tick_spacing: row.get(14)?,
        hooks: row.get(15)?,
        pair_key_id: row.get(16)?,
        input_token_key_id: row.get(17)?,
        output_token_key_id: row.get(18)?,
        zero_for_one: row.get::<_, i64>(19)? != 0,
        sqrt_price_x96: row.get(20)?,
        tick: row.get(21)?,
        liquidity: row.get(22)?,
        price: row.get(23)?,
        depth_status: row.get(24)?,
        liquidity_read_at: row.get(25)?,
        liquidity_block_number: row.get(26)?,
        evidence_source: row.get(27)?,
        input_capacity_raw: row.get(28)?,
        input_capacity_measured: row.get::<_, i64>(29)? != 0,
        depth_capacity_raw: row.get(30)?,
        directional_output: row.get(31)?,
        directional_output_measured: row.get::<_, i64>(32)? != 0,
        pair_direction_rank: row.get(33)?,
        pool_status: row.get(34)?,
        pool_status_reason: row.get(35)?,
        source_block_number: row.get(36)?,
        source_tx_hash: row.get(37)?,
        materialized_at: row.get(38)?,
        updated_at: row.get(39)?,
    })
}

fn find_write_request(
    connection: &Connection,
    request: &DirectionalPoolWriteRequest,
) -> Result<Option<String>, DirectionalPoolError> {
    Ok(connection
        .query_row(
            "SELECT response_json
             FROM manager_write_requests
             WHERE network = ?1 AND chain_id = ?2 AND operation = ?3
               AND idempotency_key = ?4",
            params![
                request.network,
                request.chain_id,
                request.operation,
                request.idempotency_key
            ],
            |row| row.get(0),
        )
        .optional()?)
}

fn persist_write_request(
    connection: &Connection,
    request: &DirectionalPoolWriteRequest,
    request_digest: &str,
    result: &DirectionalPoolMaterializationResult,
) -> Result<(), DirectionalPoolError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "scopeKey": request.scope_key,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    let response_json = serde_json::to_string(&PersistedDirectionalWrite {
        request_id: request.request_id.clone(),
        request_digest: request_digest.to_owned(),
        result: result.clone(),
    })?;
    connection.execute(
        "INSERT INTO manager_write_requests (
             network, chain_id, operation, idempotency_key, request_id,
             request_digest, request_json, response_json, created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            request.network,
            request.chain_id,
            request.operation,
            request.idempotency_key,
            request.request_id,
            request_digest,
            request_json.to_string(),
            response_json,
            chrono::Utc::now().to_rfc3339()
        ],
    )?;
    Ok(())
}

fn open_connection(path: &Path, writable: bool) -> Result<Connection, DirectionalPoolError> {
    let flags = if writable {
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI
    } else {
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI
    };
    Ok(Connection::open_with_flags(path, flags)?)
}

fn usize_from_i64(value: i64) -> usize {
    usize::try_from(value).unwrap_or(0)
}
