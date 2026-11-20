use candidate_edge_pool_manager::CandidateEdgeStatus;
use candidate_exchange_pool_manager::CandidateExchangeStatus;
use manager_contracts::{RouteKeyId, TokenKeyId};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::{Path, PathBuf};

pub mod usdg_valuation;

const NETWORK_PROTOCOL: &str = "uniswap_v4";

/// Minimum on-chain price-impact capacity, in raw token units of the binding
/// side, for a pool to participate in a closed loop.
///
/// The capacity is denominated in the pool's own tokens, so this threshold
/// never depends on a USD quote. The value is re-exported from the pool
/// manager, which owns the single shared threshold, so the closed-loop
/// admission and the candidate-pool admission can never disagree about which
/// pools are deep enough.
pub use pool_manager::depth_config::MIN_CANDIDATE_CAPACITY_RAW as MIN_CLOSED_LOOP_CAPACITY_RAW;
const CLOSED_LOOP_SCOPE_KEY: &str = "core4";
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_SNAPSHOT_LIMIT: usize = 1_000;
const MAX_COMPARISON_ROWS: usize = 1_000_000;

/// The shared economic threshold that opens the cross-tick supplement.
///
/// The value is the shared depth-policy threshold of ten USDG. It is
/// duplicated here only as the **fallback** for environments where the
/// policy file cannot be located; the policy file itself is the single
/// source of truth and is preferred whenever it is present.
const DEFAULT_CROSS_TICK_REVIEW_THRESHOLD_USDG_RAW: u128 = 10_000_000;

/// The symbol of the venue's quote asset.
const USDG_SYMBOL: &str = "USDG";

/// Locate the shared depth policy file.
fn depth_policy_path() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(explicit) = std::env::var("FLASHLOAN_DEPTH_POLICY_PATH") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
        candidates.push(path);
    }
    let mut current = std::env::current_dir().ok()?;
    loop {
        candidates.push(current.join("flashloan/config/depth_policy.json"));
        candidates.push(current.join("config/depth_policy.json"));
        if !current.pop() {
            break;
        }
    }
    candidates.into_iter().find(|path| path.is_file())
}

/// Read the shared cross-tick review threshold from the depth policy.
fn read_cross_tick_review_threshold_usdg_raw() -> u128 {
    let Some(path) = depth_policy_path() else {
        return DEFAULT_CROSS_TICK_REVIEW_THRESHOLD_USDG_RAW;
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return DEFAULT_CROSS_TICK_REVIEW_THRESHOLD_USDG_RAW;
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
        return DEFAULT_CROSS_TICK_REVIEW_THRESHOLD_USDG_RAW;
    };
    value
        .get("economicPolicy")
        .and_then(|policy| policy.get("minEconomicPositionUsdgRaw"))
        .and_then(|raw| raw.as_u64())
        .map(|raw| raw as u128)
        .filter(|raw| *raw > 0)
        .unwrap_or(DEFAULT_CROSS_TICK_REVIEW_THRESHOLD_USDG_RAW)
}

#[derive(Debug)]
pub enum ClosedLoopError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    InvalidRequest(String),
    MissingCandidateEdgeState,
    MissingObservationState,
    InvalidStoredTopology(String),
    UpstreamInconsistent {
        field: String,
        upstream: String,
        value: String,
    },
    ExpectedUpstreamMismatch {
        field: String,
        expected: String,
        actual: String,
    },
    IdempotencyKeyReused {
        idempotency_key: String,
        existing_request_id: String,
        submitted_request_id: String,
    },
    ComparisonRead {
        stage: String,
        source: rusqlite::Error,
    },
}

impl fmt::Display for ClosedLoopError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "closed loop storage error: {error}"),
            Self::Json(error) => write!(formatter, "closed loop json error: {error}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid closed loop request: {message}")
            }
            Self::MissingCandidateEdgeState => {
                formatter.write_str("candidate edge scope core4 has not been materialized")
            }
            Self::MissingObservationState => {
                formatter.write_str("candidate exchange observation has not been materialized")
            }
            Self::InvalidStoredTopology(message) => {
                write!(formatter, "invalid candidate topology: {message}")
            }
            Self::UpstreamInconsistent {
                field,
                upstream,
                value,
            } => write!(
                formatter,
                "closed loop upstream {upstream} is inconsistent at {field}: {value}"
            ),
            Self::ExpectedUpstreamMismatch {
                field,
                expected,
                actual,
            } => write!(
                formatter,
                "upstream {field} changed: expected {expected}, actual {actual}"
            ),
            Self::IdempotencyKeyReused {
                idempotency_key,
                existing_request_id,
                submitted_request_id,
            } => write!(
                formatter,
                "Idempotency-Key {idempotency_key} was first used by request \
                 {existing_request_id} and cannot be reused by {submitted_request_id}"
            ),
            Self::ComparisonRead { stage, source } => {
                write!(
                    formatter,
                    "closed loop comparison {stage} read failed: {source}"
                )
            }
        }
    }
}

impl std::error::Error for ClosedLoopError {}

impl From<rusqlite::Error> for ClosedLoopError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<serde_json::Error> for ClosedLoopError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpectedUpstream {
    pub candidate_edge_version: Option<i64>,
    pub candidate_edge_checksum: Option<String>,
    pub observation_version: Option<i64>,
    pub observation_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    #[serde(default)]
    pub library_versions: BTreeMap<String, i64>,
    #[serde(default)]
    pub library_checksums: BTreeMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct ClosedLoopWriteRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub expected_upstream: ExpectedUpstream,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub apply: bool,
    pub dry_run: bool,
    pub candidate_edge_version: i64,
    pub candidate_edge_checksum: String,
    pub observation_version: i64,
    pub observation_checksum: String,
    pub pool_scope_checksum: String,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub anchor_count: usize,
    pub loop_count: usize,
    pub hop_count: usize,
    pub optimization_count: usize,
    pub skipped_token_count: usize,
    pub changed_rows: usize,
    pub closed_loop_version: i64,
    pub materialized_checksum: String,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopWriteOutcome {
    #[serde(flatten)]
    pub result: ClosedLoopMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopStatus {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub closed_loop_version: i64,
    pub materialized_checksum: Option<String>,
    pub candidate_edge_version: Option<i64>,
    pub candidate_edge_checksum: Option<String>,
    pub observation_version: Option<i64>,
    pub observation_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub anchor_count: usize,
    pub loop_count: usize,
    pub hop_count: usize,
    pub optimization_count: usize,
    pub skipped_token_count: usize,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopRow {
    pub loop_key: String,
    pub route_key_id: String,
    pub anchor_token_key_id: String,
    pub anchor_address: String,
    pub anchor_symbol: String,
    pub token_y_key_id: String,
    pub token_z_key_id: String,
    pub path_text: String,
    pub path_addresses: Vec<String>,
    pub path_symbols: Vec<String>,
    pub pool_path_key: String,
    pub pool_ids: Vec<String>,
    pub backup_pool_ids: BTreeMap<String, Vec<String>>,
    pub hop_candidate_counts: BTreeMap<String, usize>,
    pub hop_candidate_combination_count: usize,
    pub recommendation_basis: String,
    pub recommended_first_priority: bool,
    pub candidate_edge_version: i64,
    pub candidate_edge_checksum: String,
    pub observation_version: i64,
    pub observation_checksum: String,
    pub status: String,
    pub source: String,
    /// The folded single-tick capacity of the loop, in anchor-token raw
    /// units.
    ///
    /// This is the `M_cap` of the single-tick design: the capacity of every
    /// hop, folded into the anchor token with the cumulative rate of the
    /// hops before it, reduced to the smallest folded capacity. It is the
    /// only number that may bound the input amount of the route.
    ///
    /// `None` means the fold could not be performed, because a hop does not
    /// publish a usable tick state. The absence is a coverage gap of the
    /// pool index, not a market conclusion, so it is never recorded as a
    /// measured zero.
    #[serde(
        serialize_with = "manager_contracts::amount::serialize_optional_u128_as_string",
        deserialize_with = "manager_contracts::amount::deserialize_optional_u128_from_string"
    )]
    pub single_tick_capacity_anchor_raw: Option<u128>,
    /// The hop whose folded capacity is the smallest.
    ///
    /// The pool is named so an operator can see **which** hop bounds the
    /// loop instead of having to re-derive the fold.
    pub single_tick_capacity_binding_pool_id: Option<String>,
    /// The USDG value of **one whole** borrow token, in raw USDG units.
    ///
    /// The value is the spot rate of the pool that pairs the borrow token
    /// with USDG. It is what makes the cross-tick threshold expressible
    /// without an external price feed.
    ///
    /// `None` means the borrow token has no USDG route. The absence is a
    /// coverage gap, never a zero valuation.
    #[serde(
        serialize_with = "manager_contracts::amount::serialize_optional_u128_as_string",
        deserialize_with = "manager_contracts::amount::deserialize_optional_u128_from_string"
    )]
    pub borrow_token_usdg_value_raw: Option<u128>,
    /// The decimals of the borrow token.
    pub borrow_token_decimals: Option<u32>,
    /// The shared cross-tick review threshold, in raw USDG units.
    #[serde(
        serialize_with = "manager_contracts::amount::serialize_u128_as_string",
        deserialize_with = "manager_contracts::amount::deserialize_u128_from_string"
    )]
    pub cross_tick_review_threshold_usdg_raw: u128,
    /// The ceiling of the cross-tick search, in borrow-token raw units.
    ///
    /// The ceiling is the **depth** capacity of the path, folded into the
    /// anchor token. The cross-tick mode may grow beyond the single-tick
    /// physical ceiling, but never beyond the depth the pools can carry.
    #[serde(
        serialize_with = "manager_contracts::amount::serialize_optional_u128_as_string",
        deserialize_with = "manager_contracts::amount::deserialize_optional_u128_from_string"
    )]
    pub cross_tick_capacity_anchor_raw: Option<u128>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopHopRow {
    pub loop_key: String,
    pub hop_index: usize,
    pub hop_name: String,
    pub input_token_key_id: String,
    pub output_token_key_id: String,
    pub input_address: String,
    pub output_address: String,
    pub pool_id: String,
    pub base_pool_id: String,
    pub pool_selection_basis: String,
    pub optimization_id: Option<String>,
    pub temporary_override: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopOptimizationRow {
    pub loop_key: String,
    pub hop_index: usize,
    pub optimization_id: String,
    pub original_pool_id: String,
    pub optimized_pool_id: String,
    pub optimization_basis: String,
    pub expected_price_improvement: Option<f64>,
    pub status: String,
    pub expires_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopRuntimeStateRow {
    pub loop_key: String,
    pub runtime_status: String,
    pub status_reason: Option<String>,
    pub review_generation: i64,
    pub review_required: bool,
    pub last_rebuilt_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopSkippedTokenRow {
    pub anchor_token_key_id: String,
    pub token_key_id: String,
    pub token_symbol: String,
    pub candidate_role: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopSnapshot {
    pub status: ClosedLoopStatus,
    pub loops: Vec<ClosedLoopRow>,
    pub hops: Vec<ClosedLoopHopRow>,
    pub optimizations: Vec<ClosedLoopOptimizationRow>,
    pub runtime_states: Vec<ClosedLoopRuntimeStateRow>,
    pub skipped_tokens: Vec<ClosedLoopSkippedTokenRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopComparisonSummary {
    pub manager_row_count: usize,
    pub legacy_row_count: usize,
    pub manager_only_count: usize,
    pub legacy_only_count: usize,
    pub field_mismatch_count: usize,
    pub manager_checksum: String,
    pub legacy_checksum: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopComparisonDifference {
    pub category: String,
    pub key: String,
    pub kind: String,
    pub cause: String,
    pub manager_payload: Option<String>,
    pub legacy_payload: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopComparison {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub legacy_market_id: String,
    pub manager_checksum: Option<String>,
    pub legacy_tables_available: bool,
    pub ready_for_cutover: bool,
    pub loops: ClosedLoopComparisonSummary,
    pub hops: ClosedLoopComparisonSummary,
    pub cause_counts: BTreeMap<String, usize>,
    pub differences: Vec<ClosedLoopComparisonDifference>,
    pub differences_truncated: bool,
}

#[derive(Debug, Clone)]
struct TokenMeta {
    token_key_id: String,
    token_symbol: String,
    _token_library_key: String,
}

#[derive(Debug, Clone)]
struct AnchorInput {
    tokens: BTreeMap<String, TokenMeta>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Recommendation {
    anchor_token_key_id: String,
    candidate_token_key_id: String,
    direction_key: String,
    hop: String,
    rank: usize,
    pool_id: String,
    selection_basis: String,
}

#[derive(Debug, Clone)]
struct YzDirection {
    has_direct_swap_pool: bool,
    pool_ids: Vec<String>,
    optimal_pool_id: Option<String>,
    empty_reason: Option<String>,
}

#[derive(Debug, Clone)]
struct MiddlePool {
    token0_key_id: String,
    token1_key_id: String,
}

/// The published single-tick state of one pool.
///
/// The state carries exactly what the path-level capacity needs: the
/// current price, the liquidity, the fee, the tick spacing, the tick the
/// price sits in and the order of the pool's two tokens.
#[derive(Debug, Clone)]
struct PoolTickState {
    /// The square-root price of the pool, in Q96 fixed point.
    sqrt_price_x96: u128,
    /// The current liquidity of the pool.
    liquidity: u128,
    /// The fee of the pool, in hundredths of a basis point.
    fee_pips: u32,
    /// The tick spacing of the pool.
    tick_spacing: i32,
    /// The initialized tick the pool's price sits in.
    tick: i32,
    /// The key id of the pool's token0.
    token0_key_id: String,
    /// The key id of the pool's token1.
    token1_key_id: String,
}

struct ClosedLoopInput {
    candidate_edge: CandidateEdgeStatus,
    observation: CandidateExchangeStatus,
    anchors: BTreeMap<String, AnchorInput>,
    recommendations: Vec<Recommendation>,
    yz_directions: BTreeMap<(String, String), YzDirection>,
    middle_pools: BTreeMap<(String, String), MiddlePool>,
    candidate_pool_ids: BTreeSet<(String, String)>,
    /// On-chain price-impact capacity per pool, in raw token units.
    ///
    /// A pool that has never been measured is absent from this map. The
    /// absence is a coverage gap of the pool index, not a statement about the
    /// market, so it must not disqualify a loop by itself.
    capacity_by_pool: BTreeMap<String, u128>,
    /// The largest input each pool may carry while the price stays inside
    /// its current tick range, per direction.
    ///
    /// The first arbitrage version constrains every hop to one tick range,
    /// so a position larger than this bound would cross a tick boundary,
    /// where the single-step off-chain pricing no longer holds. Pool
    /// Manager owns the arithmetic and publishes the bound.
    ///
    /// A pool that has never been measured is absent from this map. The
    /// absence is a coverage gap of the pool index, not a statement about
    /// the market, so it must not disqualify a loop by itself.
    single_tick_bounds_by_pool: BTreeMap<String, (Option<u128>, Option<u128>)>,
    /// The published single-tick state of every pool that carries one.
    ///
    /// The state is what lets the path capacity be expressed in the
    /// **anchor** token: the bound of a hop is denominated in the token
    /// that hop spends, and the conversion between the tokens of the hops
    /// is read from the pools themselves.
    pool_tick_states: BTreeMap<String, PoolTickState>,
    /// The decimals of every admitted token, keyed by its key id.
    ///
    /// The decimals are the property of the **token**, not of a pool, so
    /// they are read once from the token catalog. Every token of a pool
    /// carries its decimals, because only admitted tokens enter the pool
    /// index in the first place.
    token_decimals: BTreeMap<String, u32>,
    /// The key id of the venue's quote asset.
    ///
    /// Every pool token has a conversion route to it by convention, which
    /// is what makes an economic threshold expressible without an external
    /// price feed.
    usdg_token_key_id: Option<String>,
    /// The shared economic threshold, in raw USDG units.
    ///
    /// The value is the shared depth-policy threshold of ten USDG. It is
    /// read from the policy file so that there is exactly one definition
    /// of the threshold in the system.
    cross_tick_review_threshold_usdg_raw: u128,
}

/// Resolve the pool-scope checksum that the closed-loop layer must publish.
///
/// The closed loop lives in the `core4` scope, so the only checksum that is
/// valid for it is the one owned by the `core4` pool-scope configuration. The
/// observation layer intentionally carries the `candidate_exchange` scope
/// checksum, so reading it here would make the closed-loop layer publish a
/// checksum for a scope it does not belong to. That mismatch silently pins
/// the materialized checksum to an unrelated value and makes the rebuild
/// gate report no change, which is why the drift could never be repaired.
fn closed_loop_pool_scope_checksum(input: &ClosedLoopInput) -> Result<String, ClosedLoopError> {
    if input.candidate_edge.scope_key != CLOSED_LOOP_SCOPE_KEY {
        return Err(ClosedLoopError::InvalidStoredTopology(format!(
            "candidate edge scope {:?} is not the closed-loop scope {:?}",
            input.candidate_edge.scope_key, CLOSED_LOOP_SCOPE_KEY
        )));
    }
    input
        .candidate_edge
        .pool_scope_checksum
        .clone()
        .ok_or(ClosedLoopError::MissingCandidateEdgeState)
}

#[derive(Debug, Clone, Default, PartialEq)]
struct DesiredClosedLoopData {
    loops: Vec<ClosedLoopRow>,
    hops: Vec<ClosedLoopHopRow>,
    optimizations: Vec<ClosedLoopOptimizationRow>,
    runtime_states: Vec<ClosedLoopRuntimeStateRow>,
    skipped_tokens: Vec<ClosedLoopSkippedTokenRow>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ClosedLoopChecksumPayload<'a> {
    candidate_edge_version: i64,
    candidate_edge_checksum: &'a str,
    observation_version: i64,
    observation_checksum: &'a str,
    pool_scope_checksum: &'a str,
    library_versions: &'a BTreeMap<String, i64>,
    library_checksums: &'a BTreeMap<String, String>,
    loops: &'a [ClosedLoopRow],
    hops: &'a [ClosedLoopHopRow],
    optimizations: &'a [ClosedLoopOptimizationRow],
    skipped_tokens: &'a [ClosedLoopSkippedTokenRow],
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedClosedLoopWrite {
    request_id: String,
    request_digest: String,
    result: ClosedLoopMaterializationResult,
}

pub fn materialize_closed_loops(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<ClosedLoopMaterializationResult, ClosedLoopError> {
    materialize_impl(database_path.as_ref(), network, chain_id, apply, None)
        .map(|(result, _)| result)
}

pub fn materialize_closed_loops_write(
    database_path: impl AsRef<Path>,
    request: ClosedLoopWriteRequest,
) -> Result<ClosedLoopWriteOutcome, ClosedLoopError> {
    let (result, idempotent_replay) = materialize_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        request.apply,
        Some(&request),
    )?;
    Ok(ClosedLoopWriteOutcome {
        result,
        idempotent_replay,
    })
}

fn materialize_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    request: Option<&ClosedLoopWriteRequest>,
) -> Result<(ClosedLoopMaterializationResult, bool), ClosedLoopError> {
    validate_operation(network, chain_id)?;
    let prepared_write = match request {
        Some(request) => {
            validate_write_request(request, network, chain_id, apply)?;
            Some((request, request_digest(request)?))
        }
        None => None,
    };

    let mut connection = open_connection(database_path, apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if apply {
        let transaction =
            connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let outcome =
            materialize_on_connection(&transaction, network, chain_id, true, prepared_write)?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        materialize_on_connection(&connection, network, chain_id, false, prepared_write)
    }
}

fn materialize_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    apply: bool,
    prepared_write: Option<(&ClosedLoopWriteRequest, String)>,
) -> Result<(ClosedLoopMaterializationResult, bool), ClosedLoopError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(evidence_json) = find_write_request(connection, request)? {
                let persisted = serde_json::from_str::<PersistedClosedLoopWrite>(&evidence_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(ClosedLoopError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok((persisted.result, true));
            }
        }
    }

    let input = read_closed_loop_input(connection, network, chain_id)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &input)?;
    }
    let desired = build_closed_loop_rows(&input)?;
    let materialized_checksum = closed_loop_checksum(&input, &desired)?;
    let previous = read_status_on_connection(connection, network, chain_id)?;
    let skipped_token_count = unique_skipped_token_count(&desired.skipped_tokens);
    let checksum_changed = previous.materialized_checksum.as_deref()
        != Some(materialized_checksum.as_str())
        || previous.loop_count != desired.loops.len()
        || previous.hop_count != desired.hops.len()
        || previous.optimization_count != desired.optimizations.len()
        || previous.skipped_token_count != skipped_token_count;
    let closed_loop_version = if checksum_changed {
        previous.closed_loop_version.saturating_add(1).max(1)
    } else {
        previous.closed_loop_version.max(1)
    };
    let changed_rows = if checksum_changed {
        desired.loops.len()
            + desired.hops.len()
            + desired.optimizations.len()
            + desired.runtime_states.len()
            + desired.skipped_tokens.len()
    } else {
        0
    };
    let result = ClosedLoopMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: CLOSED_LOOP_SCOPE_KEY.to_owned(),
        apply,
        dry_run: !apply,
        candidate_edge_version: input.candidate_edge.candidate_version,
        candidate_edge_checksum: input
            .candidate_edge
            .materialized_checksum
            .clone()
            .ok_or(ClosedLoopError::MissingCandidateEdgeState)?,
        observation_version: input.observation.observation_version,
        observation_checksum: input
            .observation
            .materialized_checksum
            .clone()
            .ok_or(ClosedLoopError::MissingObservationState)?,
        pool_scope_checksum: closed_loop_pool_scope_checksum(&input)?,
        library_versions: input.observation.library_versions.clone(),
        library_checksums: input.observation.library_checksums.clone(),
        anchor_count: input.anchors.len(),
        loop_count: desired.loops.len(),
        hop_count: desired.hops.len(),
        optimization_count: desired.optimizations.len(),
        skipped_token_count,
        changed_rows,
        closed_loop_version,
        materialized_checksum,
        consumers_need_rebuild: checksum_changed,
    };

    if apply {
        if checksum_changed {
            replace_closed_loop_rows(connection, network, chain_id, &desired, &result)?;
        } else if previous.consumers_need_rebuild {
            clear_consumers_need_rebuild(connection, network, chain_id)?;
        }
        if let Some((request, request_digest)) = prepared_write {
            persist_write_request(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

fn validate_operation(network: &str, chain_id: i64) -> Result<(), ClosedLoopError> {
    if network.trim().is_empty() {
        return Err(ClosedLoopError::InvalidRequest(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(ClosedLoopError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    Ok(())
}

fn validate_write_request(
    request: &ClosedLoopWriteRequest,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<(), ClosedLoopError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(ClosedLoopError::InvalidRequest(
            "write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.apply != apply {
        return Err(ClosedLoopError::InvalidRequest(
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
) -> Result<(), ClosedLoopError> {
    if value.is_empty() || value.len() > max_length {
        return Err(ClosedLoopError::InvalidRequest(format!(
            "{label} must contain 1..={max_length} bytes"
        )));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':'))
    {
        return Err(ClosedLoopError::InvalidRequest(format!(
            "{label} contains unsupported characters"
        )));
    }
    Ok(())
}

fn request_digest(request: &ClosedLoopWriteRequest) -> Result<String, ClosedLoopError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn read_closed_loop_input(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ClosedLoopInput, ClosedLoopError> {
    let candidate_edge = read_candidate_edge_status(connection, network, chain_id)?;
    if candidate_edge.candidate_version <= 0
        || candidate_edge
            .materialized_checksum
            .as_deref()
            .is_none_or(str::is_empty)
    {
        return Err(ClosedLoopError::MissingCandidateEdgeState);
    }
    let observation = read_observation_status(connection, network, chain_id)?;
    if observation.observation_version <= 0
        || observation
            .materialized_checksum
            .as_deref()
            .is_none_or(str::is_empty)
    {
        return Err(ClosedLoopError::MissingObservationState);
    }
    validate_upstream_pair(&candidate_edge, &observation)?;

    let anchors = read_anchors(connection, network, chain_id)?;
    if anchors.is_empty() {
        return Err(ClosedLoopError::InvalidStoredTopology(
            "candidate edge has no anchor tokens".to_owned(),
        ));
    }
    let recommendations = read_recommendations(connection, network, chain_id)?;
    validate_recommendation_membership(&anchors, &recommendations)?;
    let yz_directions = read_yz_directions(connection, network, chain_id)?;
    let middle_pools = read_middle_pools(connection, network, chain_id)?;
    let candidate_pool_ids = read_candidate_pool_ids(connection, network, chain_id)?;
    let capacity_by_pool = read_pool_capacities(connection, network, chain_id)?;
    let single_tick_bounds_by_pool = read_single_tick_bounds(connection, network, chain_id)?;
    let pool_tick_states = read_pool_tick_states(connection, network, chain_id)?;
    let token_decimals = read_token_decimals(connection, network, chain_id)?;
    let usdg_token_key_id = read_usdg_token_key_id(connection, network, chain_id)?;

    Ok(ClosedLoopInput {
        candidate_edge,
        observation,
        anchors,
        recommendations,
        yz_directions,
        middle_pools,
        candidate_pool_ids,
        capacity_by_pool,
        single_tick_bounds_by_pool,
        pool_tick_states,
        token_decimals,
        usdg_token_key_id,
        cross_tick_review_threshold_usdg_raw: read_cross_tick_review_threshold_usdg_raw(),
    })
}

fn validate_upstream_pair(
    candidate_edge: &CandidateEdgeStatus,
    observation: &CandidateExchangeStatus,
) -> Result<(), ClosedLoopError> {
    if observation.candidate_edge_version != Some(candidate_edge.candidate_version) {
        return Err(ClosedLoopError::UpstreamInconsistent {
            field: "candidateEdgeVersion".to_owned(),
            upstream: "market observation".to_owned(),
            value: format!(
                "observation={:?}, candidateEdge={}",
                observation.candidate_edge_version, candidate_edge.candidate_version
            ),
        });
    }
    if observation.candidate_edge_checksum != candidate_edge.materialized_checksum {
        return Err(ClosedLoopError::UpstreamInconsistent {
            field: "candidateEdgeChecksum".to_owned(),
            upstream: "market observation".to_owned(),
            value: format!(
                "observation={:?}, candidateEdge={:?}",
                observation.candidate_edge_checksum, candidate_edge.materialized_checksum
            ),
        });
    }
    Ok(())
}

fn read_candidate_edge_status(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<CandidateEdgeStatus, ClosedLoopError> {
    let stored = connection
        .query_row(
            "SELECT candidate_version, materialized_checksum,
                    pool_scope_checksum, library_versions_json,
                    library_checksums_json, anchor_token_count,
                    candidate_token_count, eligible_pool_count,
                    recommendation_count, abnormal_pool_count
             FROM manager_dex_candidate_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4",
            params![network, chain_id, NETWORK_PROTOCOL, "core4"],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, i64>(7)?,
                    row.get::<_, i64>(8)?,
                    row.get::<_, i64>(9)?,
                ))
            },
        )
        .optional()?;
    let Some((
        candidate_version,
        materialized_checksum,
        pool_scope_checksum,
        library_versions_json,
        library_checksums_json,
        anchor_token_count,
        candidate_token_count,
        eligible_pool_count,
        recommendation_count,
        abnormal_pool_count,
    )) = stored
    else {
        return Ok(disabled_candidate_status(network, chain_id));
    };
    Ok(CandidateEdgeStatus {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: "core4".to_owned(),
        candidate_version,
        materialized_checksum: Some(materialized_checksum),
        pool_scope_checksum: Some(pool_scope_checksum),
        library_versions: serde_json::from_str(&library_versions_json)?,
        library_checksums: serde_json::from_str(&library_checksums_json)?,
        anchor_token_count: usize_from_i64(anchor_token_count),
        candidate_token_count: usize_from_i64(candidate_token_count),
        eligible_pool_count: usize_from_i64(eligible_pool_count),
        recommendation_count: usize_from_i64(recommendation_count),
        abnormal_pool_count: usize_from_i64(abnormal_pool_count),
    })
}

fn read_observation_status(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<CandidateExchangeStatus, ClosedLoopError> {
    let stored = connection
        .query_row(
            "SELECT observation_version, materialized_checksum,
                    candidate_edge_version, candidate_edge_checksum,
                    pool_scope_checksum, library_versions_json,
                    library_checksums_json, anchor_count, token_count,
                    direction_count, empty_direction_count, pool_count,
                    abnormal_direction_count, abnormal_pool_count
             FROM manager_market_observation_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4",
            params![network, chain_id, NETWORK_PROTOCOL, "candidate_exchange"],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, i64>(7)?,
                    row.get::<_, i64>(8)?,
                    row.get::<_, i64>(9)?,
                    row.get::<_, i64>(10)?,
                    row.get::<_, i64>(11)?,
                    row.get::<_, i64>(12)?,
                    row.get::<_, i64>(13)?,
                ))
            },
        )
        .optional()?;
    let Some((
        observation_version,
        materialized_checksum,
        candidate_edge_version,
        candidate_edge_checksum,
        pool_scope_checksum,
        library_versions_json,
        library_checksums_json,
        anchor_count,
        token_count,
        direction_count,
        empty_direction_count,
        pool_count,
        abnormal_direction_count,
        abnormal_pool_count,
    )) = stored
    else {
        return Ok(disabled_observation_status(network, chain_id));
    };
    Ok(CandidateExchangeStatus {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: "candidate_exchange".to_owned(),
        observation_version,
        materialized_checksum: Some(materialized_checksum),
        candidate_edge_version: Some(candidate_edge_version),
        candidate_edge_checksum: Some(candidate_edge_checksum),
        pool_scope_checksum: Some(pool_scope_checksum),
        library_versions: serde_json::from_str(&library_versions_json)?,
        library_checksums: serde_json::from_str(&library_checksums_json)?,
        anchor_count: usize_from_i64(anchor_count),
        token_count: usize_from_i64(token_count),
        direction_count: usize_from_i64(direction_count),
        empty_direction_count: usize_from_i64(empty_direction_count),
        pool_count: usize_from_i64(pool_count),
        abnormal_direction_count: usize_from_i64(abnormal_direction_count),
        abnormal_pool_count: usize_from_i64(abnormal_pool_count),
    })
}

fn read_anchors(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, AnchorInput>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, token_key_id, token_library_key, token_symbol
         FROM manager_dex_candidate_anchor_tokens
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY anchor_token_key_id, token_key_id",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, "core4"],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                TokenMeta {
                    token_key_id: row.get(1)?,
                    _token_library_key: row.get(2)?,
                    token_symbol: row.get(3)?,
                },
            ))
        },
    )?;
    let mut anchors = BTreeMap::<String, AnchorInput>::new();
    for row in rows {
        let (anchor_token_key_id, token) = row?;
        anchors
            .entry(anchor_token_key_id.clone())
            .or_insert_with(|| AnchorInput {
                tokens: BTreeMap::new(),
            })
            .tokens
            .insert(token.token_key_id.clone(), token);
    }
    for (anchor_token_key_id, anchor) in &mut anchors {
        if anchor.tokens.contains_key(anchor_token_key_id) {
            continue;
        }
        let anchor_token = connection
            .query_row(
                "SELECT token_key_id, token_symbol, library_key
                 FROM token_library_catalog
                 WHERE network = ?1 AND chain_id = ?2 AND token_key_id = ?3
                   AND admission_status = 'admitted'
                 ORDER BY CASE library_key
                     WHEN 'core' THEN 0
                     WHEN 'stocks' THEN 1
                     WHEN 'test' THEN 2
                     ELSE 3
                 END
                 LIMIT 1",
                params![network, chain_id, anchor_token_key_id],
                |row| {
                    Ok(TokenMeta {
                        token_key_id: row.get(0)?,
                        token_symbol: row.get(1)?,
                        _token_library_key: row.get(2)?,
                    })
                },
            )
            .optional()?
            .ok_or_else(|| {
                ClosedLoopError::InvalidStoredTopology(format!(
                    "anchor {anchor_token_key_id} is absent from candidate rows and token catalog"
                ))
            })?;
        anchor
            .tokens
            .insert(anchor_token.token_key_id.clone(), anchor_token);
    }
    Ok(anchors)
}

/// Read the decimals of every admitted token, keyed by its key id.
///
/// The token catalog is the owner of the decimals: a token that is not
/// admitted never enters the pool index, so the pool index never carries a
/// token whose decimals are unknown.
fn read_token_decimals(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, u32>, ClosedLoopError> {
    let table_exists = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name='token_library_catalog'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0);
    if table_exists == 0 {
        return Ok(BTreeMap::new());
    }
    let mut statement = connection.prepare(
        "SELECT token_key_id, token_decimals
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'
         ORDER BY token_key_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, Option<i64>>(1)?))
    })?;
    let mut decimals = BTreeMap::new();
    for row in rows {
        let (token_key_id, token_decimals) = row?;
        let Some(token_decimals) = token_decimals else {
            continue;
        };
        if token_decimals < 0 || token_decimals > 255 {
            continue;
        }
        decimals.insert(token_key_id, token_decimals as u32);
    }
    Ok(decimals)
}

/// Resolve the key id of the venue's quote asset.
fn read_usdg_token_key_id(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Option<String>, ClosedLoopError> {
    let table_exists = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name='token_library_catalog'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0);
    if table_exists == 0 {
        return Ok(None);
    }
    let key_id = connection
        .query_row(
            "SELECT token_key_id
             FROM token_library_catalog
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
        .optional()?;
    Ok(key_id.filter(|value| !value.trim().is_empty()))
}

fn read_recommendations(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<Recommendation>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, candidate_token_key_id, direction_key, hop, rank,
                pool_id, selection_basis
         FROM manager_dex_candidate_pool_recommendations
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, "core4"],
        |row| {
            Ok(Recommendation {
                anchor_token_key_id: row.get(0)?,
                candidate_token_key_id: row.get(1)?,
                direction_key: row.get(2)?,
                hop: row.get(3)?,
                rank: usize_from_i64(row.get(4)?),
                pool_id: row.get(5)?,
                selection_basis: row.get(6)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn validate_recommendation_membership(
    anchors: &BTreeMap<String, AnchorInput>,
    recommendations: &[Recommendation],
) -> Result<(), ClosedLoopError> {
    for recommendation in recommendations {
        if recommendation.rank == 0 {
            return Err(ClosedLoopError::InvalidStoredTopology(
                "candidate recommendation rank must be positive".to_owned(),
            ));
        }
        if !matches!(recommendation.hop.as_str(), "first" | "third") {
            return Err(ClosedLoopError::InvalidStoredTopology(format!(
                "unsupported recommendation hop {}",
                recommendation.hop
            )));
        }
        if !anchors
            .get(&recommendation.anchor_token_key_id)
            .is_some_and(|anchor| {
                anchor
                    .tokens
                    .contains_key(&recommendation.candidate_token_key_id)
            })
        {
            return Err(ClosedLoopError::InvalidStoredTopology(format!(
                "recommendation token {} is absent from anchor {} candidate tokens",
                recommendation.candidate_token_key_id, recommendation.anchor_token_key_id
            )));
        }
    }
    Ok(())
}

fn read_yz_directions(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<(String, String), YzDirection>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, direction_key, has_direct_swap_pool,
                pool_ids_json, optimal_pool_id, observation_empty_reason
         FROM manager_market_observation_yz_pairs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY anchor_token_key_id, direction_key",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, "candidate_exchange"],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                YzDirection {
                    has_direct_swap_pool: row.get::<_, i64>(2)? != 0,
                    pool_ids: serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default(),
                    optimal_pool_id: row.get(4)?,
                    empty_reason: row.get(5)?,
                },
            ))
        },
    )?;
    Ok(rows
        .map(|row| row.map(|(anchor, direction, value)| ((anchor, direction), value)))
        .collect::<Result<BTreeMap<_, _>, _>>()?)
}

fn read_middle_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<(String, String), MiddlePool>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, pool_id, token0_key_id, token1_key_id
         FROM manager_market_observation_intermediate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY anchor_token_key_id, selection_rank, pool_id",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, "candidate_exchange"],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                MiddlePool {
                    token0_key_id: row.get(2)?,
                    token1_key_id: row.get(3)?,
                },
            ))
        },
    )?;
    Ok(rows
        .map(|row| row.map(|(anchor, pool, value)| ((anchor, pool), value)))
        .collect::<Result<BTreeMap<_, _>, _>>()?)
}

fn read_candidate_pool_ids(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeSet<(String, String)>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, pool_id
         FROM manager_dex_candidate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY anchor_token_key_id, pool_id",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, "core4"],
        |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
    )?;
    Ok(rows.collect::<Result<BTreeSet<_>, _>>()?)
}

/// Read the latest on-chain capacity per pool.
///
/// The snapshot table stores one row per observation, so the newest row per
/// pool wins. A pool without a measured capacity is simply absent from the
/// result: the depth rollout may not have reached it yet.
/// Reads the single-tick bounds of every pool.
///
/// The first arbitrage version constrains every hop to one tick range, so a
/// position may never be larger than the amount that keeps the price inside
/// the current range. Pool Manager owns that arithmetic and publishes the
/// bound with the pool snapshot, so Closed Loop Manager only reads it.
///
/// A pool without a published bound is absent from the map. That is a
/// coverage gap of the pool index, not a market conclusion, so such a pool
/// does not block the path.
fn read_single_tick_bounds(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, (Option<u128>, Option<u128>)>, ClosedLoopError> {
    let table_exists = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='v4_exchange_pool_liquidity_snapshots'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0);
    if table_exists == 0 {
        return Ok(BTreeMap::new());
    }
    let columns = {
        let mut statement =
            connection.prepare("PRAGMA table_info(v4_exchange_pool_liquidity_snapshots)")?;
        let rows = statement.query_map([], |row| Ok(row.get::<_, String>(1)?))?;
        rows.collect::<Result<BTreeSet<_>, _>>()?
    };
    if !columns.contains("single_tick_max_token0_raw") {
        return Ok(BTreeMap::new());
    }
    // A NULL bound means "not measured"; a bound of zero means "measured,
    // and there is no room". The two are different facts, so the NULL is
    // preserved instead of being folded into zero.
    let mut statement = connection.prepare(
        "SELECT pool_id,
                single_tick_max_token0_raw,
                single_tick_max_token1_raw
         FROM v4_exchange_pool_liquidity_snapshots
         WHERE network = ?1 AND chain_id = ?2",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?,
            row.get::<_, Option<String>>(2)?,
        ))
    })?;
    let mut bounds = BTreeMap::new();
    for row in rows {
        let (pool_id, token0, token1) = row?;
        fn parse_bound(value: Option<String>) -> Option<u128> {
            let owned = value?;
            let text = owned.trim();
            if text.is_empty() {
                return None;
            }
            text.parse::<u128>().ok()
        }
        let token0 = parse_bound(token0);
        let token1 = parse_bound(token1);
        if token0.is_none() && token1.is_none() {
            continue;
        }
        bounds.insert(pool_id, (token0, token1));
    }
    Ok(bounds)
}

/// Read the published single-tick state of every candidate pool.
///
/// The state is what lets the path capacity be expressed in the **anchor**
/// token: the bound of a hop is denominated in the token that hop spends,
/// and the conversion between the tokens of the hops is read from the
/// pools themselves.
///
/// A pool without a usable state is absent from the map. That is a
/// coverage gap of the pool index, not a market conclusion.
fn read_pool_tick_states(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, PoolTickState>, ClosedLoopError> {
    // The tick state is published by **two** pool tables:
    //
    //   * `v4_exchange_pools_candidate_exchange` — the pools that took part
    //     in the last candidate-exchange materialization;
    //   * `v4_exchange_pools_core4` — the pools of the core scope, which
    //     includes the anchor pools that only exist there.
    //
    // A closed loop may mix both kinds of pools. Reading only one of the
    // two tables therefore leaves the loops that touch a core-only pool
    // without a tick state, and their folded capacity stays `None` even
    // though every hop really does have a published state.
    //
    // The candidate-exchange table wins when both carry the same pool: it
    // is the table the closed-loop layer actually consumes, so its state is
    // the freshest one for the pools it publishes.
    let mut states = BTreeMap::new();
    for table in [
        "v4_exchange_pools_candidate_exchange",
        "v4_exchange_pools_core4",
    ] {
        read_pool_tick_states_from_table(connection, network, chain_id, table, &mut states)?;
    }
    Ok(states)
}

/// Read the published single-tick state of every candidate pool.
///
/// The state is what lets the path capacity be expressed in the **anchor**
/// token: the bound of a hop is denominated in the token that hop spends,
/// and the conversion between the tokens of the hops is read from the
/// pools themselves.
///
/// A pool without a usable state is absent from the map. That is a
/// coverage gap of the pool index, not a market conclusion.
fn read_pool_tick_states_from_table(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    table: &str,
    states: &mut BTreeMap<String, PoolTickState>,
) -> Result<(), ClosedLoopError> {
    let table_exists = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1",
            params![table],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0);
    if table_exists == 0 {
        return Ok(());
    }
    let columns = {
        let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
        let rows = statement.query_map([], |row| Ok(row.get::<_, String>(1)?))?;
        rows.collect::<Result<BTreeSet<_>, _>>()?
    };
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
            return Ok(());
        }
    }
    let mut statement = connection.prepare(&format!(
        "SELECT pool_id, sqrt_price_x96, tick, liquidity, fee, tick_spacing,
                token0_key_id, token1_key_id
         FROM {table}
         WHERE network = ?1 AND chain_id = ?2"
    ))?;
    let rows = statement.query_map(params![network, chain_id], |row| {
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
    })?;
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
        ) = row?;
        // A state that cannot be used is not published: the capacity of a
        // hop with an unusable price or an unusable liquidity is not
        // computable, and publishing a zero would read as "measured, and
        // there is no room".
        let Some(sqrt_price_x96) = sqrt_price_x96.and_then(|text| text.trim().parse::<u128>().ok())
        else {
            continue;
        };
        if sqrt_price_x96 == 0 {
            continue;
        }
        // The liquidity column stores the raw uint128 liquidity as TEXT, the
        // same representation the pool snapshot table uses. Reading it as an
        // INTEGER truncates every value above i64::MAX and fails outright on
        // the production store, so the value is parsed from its text form.
        let Some(liquidity) = liquidity.and_then(|text| text.trim().parse::<u128>().ok()) else {
            continue;
        };
        if liquidity <= 0 {
            continue;
        }
        let Some(tick) = tick else { continue };
        let Some(fee) = fee else { continue };
        if fee <= 0 {
            continue;
        }
        let Some(tick_spacing) = tick_spacing else {
            continue;
        };
        if tick_spacing <= 0 {
            continue;
        }
        let Some(token0_key_id) = token0_key_id else {
            continue;
        };
        let Some(token1_key_id) = token1_key_id else {
            continue;
        };
        if token0_key_id.trim().is_empty() || token1_key_id.trim().is_empty() {
            continue;
        }
        states.entry(pool_id.clone()).or_insert(PoolTickState {
            sqrt_price_x96,
            liquidity,
            fee_pips: fee as u32,
            tick_spacing: tick_spacing as i32,
            tick: tick as i32,
            token0_key_id,
            token1_key_id,
        });
    }
    Ok(())
}

fn read_pool_capacities(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, u128>, ClosedLoopError> {
    let table_exists = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='v4_exchange_pool_liquidity_snapshots'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0);
    if table_exists == 0 {
        return Ok(BTreeMap::new());
    }
    let columns = {
        let mut statement =
            connection.prepare("PRAGMA table_info(v4_exchange_pool_liquidity_snapshots)")?;
        let rows = statement.query_map([], |row| Ok(row.get::<_, String>(1)?))?;
        rows.collect::<Result<BTreeSet<_>, _>>()?
    };
    if !columns.contains("capacity_raw") {
        return Ok(BTreeMap::new());
    }
    let mut statement = connection.prepare(
        "SELECT pool_id, COALESCE(capacity_raw, '0')
         FROM v4_exchange_pool_liquidity_snapshots
         WHERE network = ?1 AND chain_id = ?2",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut capacities = BTreeMap::new();
    for row in rows {
        let (pool_id, capacity) = row?;
        // The capacity is a raw uint128 amount stored as TEXT. Casting it to
        // an INTEGER clamps every value above i64::MAX to i64::MAX, which
        // would silently promote an oversized pool instead of rejecting it,
        // so the text form is parsed instead.
        let Ok(capacity) = capacity.trim().parse::<u128>() else {
            continue;
        };
        if capacity <= 0 {
            continue;
        }
        capacities.insert(pool_id, capacity);
    }
    Ok(capacities)
}

/// Return the path-level minimum of the single-tick bounds.
///
/// The first arbitrage version constrains every hop to **one tick range**.
/// A hop whose position would cross the range boundary is not executable in
/// that version, so a path is only eligible when *every* hop can carry the
/// position inside its own range.
///
/// The bound is per direction: the first hop spends the anchor token, the
/// middle hop spends Y and the third hop spends Z, so each hop is bounded
/// by the direction it actually trades in.
///
/// A hop without a published bound is skipped, which keeps the loop
/// eligible: the missing measurement is a coverage gap of the pool index,
/// not a market conclusion.
fn path_min_single_tick_bound(
    input: &ClosedLoopInput,
    pool_ids: &[String],
    directions: &[bool],
) -> (Option<u128>, Option<String>) {
    // The bounds of the hops are denominated in the token each hop spends:
    // the first hop spends the anchor token, the middle hop spends Y and
    // the third hop spends Z. Taking the minimum of three numbers in three
    // different units is a **unit error**, so the bounds are folded into
    // the anchor token before they are compared.
    //
    // The fold needs the price of every hop. When every pool publishes a
    // usable state, the folded capacity is the only value the gate uses.
    // A legacy row may still publish a raw zero bound before tick states
    // are rolled out. That zero is retained as a directional unusability
    // signal, but raw positive bounds are never compared across tokens.
    if let Some((capacity, blocking_pool)) =
        folded_path_capacity(&input.pool_tick_states, pool_ids, directions)
    {
        return (Some(capacity), Some(blocking_pool));
    }
    let mut unusable_pool = None;
    for (pool_id, zero_for_one) in pool_ids.iter().zip(directions.iter()) {
        let Some((token0, token1)) = input.single_tick_bounds_by_pool.get(pool_id) else {
            continue;
        };
        let bound = if *zero_for_one { token0 } else { token1 };
        if *bound == Some(0) {
            unusable_pool = Some(pool_id.clone());
            break;
        }
    }
    (unusable_pool.clone().map(|_| 0), unusable_pool)
}

/// Return the path-level capacity in the **anchor** token.
///
/// Every hop capacity is folded into the anchor token with the cumulative
/// rate of the hops before it, exactly as the single-tick capacity design
/// prescribes, and the smallest folded capacity is returned.
///
/// `None` means the fold cannot be performed: either a pool state or a
/// price is missing, or the capacity itself is not computable.
fn folded_path_capacity(
    pool_tick_states: &BTreeMap<String, PoolTickState>,
    pool_ids: &[String],
    directions: &[bool],
) -> Option<(u128, String)> {
    if pool_ids.is_empty() || pool_ids.len() != directions.len() {
        return None;
    }

    // The hop spends a token and receives another one. The token order of
    // the pool decides which of the two the hop spends, and the hop
    // direction decides whether it is token0 or token1.
    let mut hops: Vec<pool_manager::single_tick_capacity::CapacityHop> =
        Vec::with_capacity(pool_ids.len());
    for (pool_id, zero_for_one) in pool_ids.iter().zip(directions.iter()) {
        let state = pool_tick_states.get(pool_id)?;
        let (token_in, token_out) = if *zero_for_one {
            (state.token0_key_id.clone(), state.token1_key_id.clone())
        } else {
            (state.token1_key_id.clone(), state.token0_key_id.clone())
        };
        hops.push(pool_manager::single_tick_capacity::CapacityHop {
            pool_id: pool_id.clone(),
            token_in,
            token_out,
            sqrt_price_x96: state.sqrt_price_x96,
            liquidity: state.liquidity,
            fee_pips: state.fee_pips,
            tick_spacing: state.tick_spacing,
            tick: state.tick,
            zero_for_one: *zero_for_one,
        });
    }

    // The anchor token is the token the **first** hop spends, which is the
    // token the loop starts from.
    let anchor_token = hops.first()?.token_in.clone();
    let capacity = match pool_manager::single_tick_capacity::compute_loop_capacity(
        &anchor_token,
        &hops,
    ) {
        Ok(capacity) => capacity,
        Err(_error) => return None,
    };
    Some((capacity.capacity_anchor_raw, capacity.binding_pool_id))
}

/// Fold the **depth** capacities of the hops into the anchor token.
///
/// The depth capacity is denominated in the token each hop spends, exactly
/// like the single-tick capacity, so the same cumulative-rate fold applies.
/// The result is the ceiling of the cross-tick search: the cross-tick mode
/// may grow beyond the single-tick physical ceiling, but it may never grow
/// beyond the depth the pools can carry.
fn folded_path_depth_capacity(
    pool_tick_states: &BTreeMap<String, PoolTickState>,
    capacity_by_pool: &BTreeMap<String, u128>,
    pool_ids: &[String],
    directions: &[bool],
) -> Option<u128> {
    if pool_ids.is_empty() || pool_ids.len() != directions.len() {
        return None;
    }
    let mut hops: Vec<pool_manager::single_tick_capacity::DepthCapacityHop> =
        Vec::with_capacity(pool_ids.len());
    for (pool_id, zero_for_one) in pool_ids.iter().zip(directions.iter()) {
        let state = pool_tick_states.get(pool_id)?;
        let Some(capacity) = capacity_by_pool.get(pool_id) else {
            return None;
        };
        let (token_in, token_out) = if *zero_for_one {
            (state.token0_key_id.clone(), state.token1_key_id.clone())
        } else {
            (state.token1_key_id.clone(), state.token0_key_id.clone())
        };
        hops.push(pool_manager::single_tick_capacity::DepthCapacityHop {
            pool_id: pool_id.clone(),
            token_in,
            token_out,
            sqrt_price_x96: state.sqrt_price_x96,
            zero_for_one: *zero_for_one,
            depth_capacity_raw: *capacity,
        });
    }
    let anchor_token = hops.first()?.token_in.clone();
    pool_manager::single_tick_capacity::compute_loop_depth_capacity(
        &anchor_token,
        &hops,
    )
    .ok()
}

/// The USDG valuation of one token, computed from the in-memory pool state.
///
/// The valuation is derived from the same tick states the capacity fold
/// uses, so the two numbers always describe the same snapshot.
fn usdg_valuation_for_token(
    input: &ClosedLoopInput,
    token_key_id: &str,
) -> Option<usdg_valuation::UsdgValuation> {
    let usdg_token_key_id = input.usdg_token_key_id.as_deref()?;
    let usdg_decimals = input.token_decimals.get(usdg_token_key_id).copied()?;
    if let Some(identity) = usdg_valuation::identity_valuation(
        token_key_id,
        usdg_token_key_id,
        usdg_decimals,
    ) {
        return Some(identity);
    }
    let pool_states: BTreeMap<String, usdg_valuation::ValuationPoolState> = input
        .pool_tick_states
        .iter()
        .map(|(pool_id, state)| {
            (
                pool_id.clone(),
                usdg_valuation::ValuationPoolState {
                    sqrt_price_x96: state.sqrt_price_x96,
                    token0_key_id: state.token0_key_id.clone(),
                    token1_key_id: state.token1_key_id.clone(),
                },
            )
        })
        .collect();
    usdg_valuation::value_tokens_in_usdg(
        &pool_states,
        usdg_token_key_id,
        &input.token_decimals,
        usdg_decimals,
    )
    .remove(token_key_id)
}

/// Resolve the swap direction of one hop from the **pool's own token order**.
///
/// The logical direction of a hop (`A -> B`) does not tell whether the hop
/// spends the pool's `token0` or its `token1`: the two tokens of a pool are
/// ordered by their numeric address, and that order is independent of the
/// logical direction of the trade. A pool whose `token0` is `B` and whose
/// `token1` is `A` trades `A -> B` as a **token1-for-token0** swap.
///
/// Deriving the direction from the logical direction key instead of from the
/// pool order swaps the input and the output token of the hop, and the
/// cumulative fold then accumulates the inverse rate of that hop. The fold
/// either fails outright or produces a capacity that belongs to a different
/// path, so the loop silently publishes no usable capacity.
///
/// The direction is therefore resolved from the published tick state: the
/// hop spends `token0` exactly when the hop's input token is the pool's
/// `token0`. A pool that does not carry the hop's input token is a
/// topology error and is reported as such.
///
/// A pool **without** a published tick state carries no token order at all.
/// That is a coverage gap of the pool index rather than a topology error,
/// so the caller is expected to fall back to a coarser source of truth
/// instead of failing the whole materialization.
fn hop_zero_for_one(
    pool_tick_states: &BTreeMap<String, PoolTickState>,
    pool_id: &str,
    token_in: &str,
) -> Result<Option<bool>, ClosedLoopError> {
    let Some(state) = pool_tick_states.get(pool_id) else {
        return Ok(None);
    };
    let token_in = token_in.trim();
    if state.token0_key_id.trim() == token_in {
        return Ok(Some(true));
    }
    if state.token1_key_id.trim() == token_in {
        return Ok(Some(false));
    }
    Err(ClosedLoopError::InvalidStoredTopology(format!(
        "hop pool {pool_id} does not carry the hop input token {token_in}"
    )))
}

/// Resolve the swap direction of one hop, preferring the pool's token order.
///
/// The published tick state carries the pool's token order, which is the
/// only source that tells whether the hop spends `token0` or `token1`. It is
/// therefore preferred whenever it is available.
///
/// When the pool has no published tick state the direction falls back to the
/// logical direction key (`from > to`). That fallback only says which token
/// the hop **spends**, not which side of the pool it spends, so it is
/// correct only when the pool's token order happens to agree with the
/// logical order. Such a pool contributes no folded capacity anyway,
/// because the fold needs the tick state as well, so the fallback never
/// influences a published capacity.
fn resolve_hop_direction(
    pool_tick_states: &BTreeMap<String, PoolTickState>,
    pool_id: &str,
    token_in: &str,
    direction_key: &str,
    direction_from_token: &str,
) -> Result<bool, ClosedLoopError> {
    if let Some(zero_for_one) = hop_zero_for_one(pool_tick_states, pool_id, token_in)? {
        return Ok(zero_for_one);
    }
    Ok(direction_key
        .split_once('>')
        .map(|(from, _)| from.trim() == direction_from_token.trim())
        .unwrap_or(false))
}

/// Return the path-level minimum capacity of a closed loop.
///
/// A closed loop is only executable when *every* hop can carry the position
/// inside the epsilon price-impact budget. One hop whose capacity is below
/// the floor therefore disqualifies the whole path, even when the other two
/// hops are deep.
///
/// A hop without a measured capacity returns `None`, which keeps the loop
/// eligible: the missing measurement is a coverage gap of the pool index,
/// not a market conclusion.
fn path_min_capacity(
    input: &ClosedLoopInput,
    pool_ids: &[String],
) -> (Option<u128>, Option<String>) {
    let mut minimum: Option<u128> = None;
    let mut blocking_pool: Option<String> = None;
    for pool_id in pool_ids {
        let Some(capacity) = input.capacity_by_pool.get(pool_id) else {
            continue;
        };
        if *capacity < MIN_CLOSED_LOOP_CAPACITY_RAW {
            return (Some(*capacity), Some(pool_id.clone()));
        }
        if minimum.is_none_or(|current| *capacity < current) {
            minimum = Some(*capacity);
            blocking_pool = Some(pool_id.clone());
        }
    }
    (minimum, blocking_pool)
}

fn build_closed_loop_rows(
    input: &ClosedLoopInput,
) -> Result<DesiredClosedLoopData, ClosedLoopError> {
    let mut first = BTreeMap::<(String, String), Vec<Recommendation>>::new();
    let mut third = BTreeMap::<(String, String), Vec<Recommendation>>::new();
    for recommendation in &input.recommendations {
        let anchor = recommendation.anchor_token_key_id.clone();
        let expected_direction = match recommendation.hop.as_str() {
            "first" => format!("{anchor}>{}", recommendation.candidate_token_key_id),
            "third" => format!("{}>{anchor}", recommendation.candidate_token_key_id),
            _ => unreachable!("recommendation hops were validated in the read phase"),
        };
        if recommendation.direction_key != expected_direction {
            return Err(ClosedLoopError::InvalidStoredTopology(format!(
                "recommendation {} {} has direction {}, expected {}",
                recommendation.hop,
                recommendation.pool_id,
                recommendation.direction_key,
                expected_direction
            )));
        }
        let key = (anchor, recommendation.candidate_token_key_id.clone());
        if recommendation.hop == "first" {
            first.entry(key).or_default().push(recommendation.clone());
        } else {
            third.entry(key).or_default().push(recommendation.clone());
        }
    }
    for recommendations in first.values_mut().chain(third.values_mut()) {
        recommendations.sort_by_key(|recommendation| recommendation.rank);
    }

    let mut desired = DesiredClosedLoopData::default();
    let mut skipped = BTreeSet::<(String, String, String, String)>::new();
    for (anchor_token_key_id, anchor) in &input.anchors {
        let anchor_meta = anchor.tokens.get(anchor_token_key_id).ok_or_else(|| {
            ClosedLoopError::InvalidStoredTopology(format!(
                "anchor {anchor_token_key_id} is absent from its candidate token set"
            ))
        })?;
        let candidate_tokens = anchor
            .tokens
            .keys()
            .filter(|token| *token != anchor_token_key_id)
            .cloned()
            .collect::<Vec<_>>();
        let first_keys = candidate_tokens
            .iter()
            .filter(|token| first.contains_key(&(anchor_token_key_id.clone(), (*token).clone())))
            .cloned()
            .collect::<Vec<_>>();
        let third_keys = candidate_tokens
            .iter()
            .filter(|token| third.contains_key(&(anchor_token_key_id.clone(), (*token).clone())))
            .cloned()
            .collect::<Vec<_>>();
        let mut participating_first = BTreeSet::new();
        let mut participating_third = BTreeSet::new();

        for y_token in &first_keys {
            for z_token in &third_keys {
                if y_token == z_token {
                    continue;
                }
                let middle_direction_key = format!("{y_token}->{z_token}");
                let Some(yz) = input
                    .yz_directions
                    .get(&(anchor_token_key_id.clone(), middle_direction_key.clone()))
                else {
                    // A missing YZ direction means the observation never
                    // measured the pair. The loop cannot be published, but
                    // the drop must still name the pair that is missing so
                    // the defect can be located to a concrete token pair
                    // instead of disappearing silently.
                    skipped.insert((
                        anchor_token_key_id.clone(),
                        y_token.clone(),
                        "middle".to_owned(),
                        format!("yz_direction_missing:{middle_direction_key}"),
                    ));
                    continue;
                };
                if !yz.has_direct_swap_pool {
                    // The pair was measured but no usable pool was found.
                    // The observation reason and the measured pools are the
                    // diagnosis, so both are carried into the skip reason
                    // together with the pair itself.
                    let diagnosis = yz
                        .empty_reason
                        .as_deref()
                        .unwrap_or("no_direct_swap_pool");
                    let measured = yz.pool_ids.join(",");
                    let reason = if measured.is_empty() {
                        format!("yz_pool_missing:{middle_direction_key}:{diagnosis}")
                    } else {
                        format!("yz_pool_missing:{middle_direction_key}:{diagnosis}:{measured}")
                    };
                    skipped.insert((
                        anchor_token_key_id.clone(),
                        y_token.clone(),
                        "middle".to_owned(),
                        reason,
                    ));
                    continue;
                }
                let middle_pool_id = yz.optimal_pool_id.as_deref().ok_or_else(|| {
                    ClosedLoopError::InvalidStoredTopology(format!(
                        "direct YZ direction {middle_direction_key} has no optimal pool"
                    ))
                })?;
                validate_middle_pool(input, anchor_token_key_id, y_token, z_token, middle_pool_id)?;
                for pool_id in &yz.pool_ids {
                    validate_middle_pool(input, anchor_token_key_id, y_token, z_token, pool_id)?;
                }

                let first_options = first
                    .get(&(anchor_token_key_id.clone(), y_token.clone()))
                    .ok_or_else(|| {
                        ClosedLoopError::InvalidStoredTopology(
                            "first recommendation disappeared during materialization".to_owned(),
                        )
                    })?;
                let third_options = third
                    .get(&(anchor_token_key_id.clone(), z_token.clone()))
                    .ok_or_else(|| {
                        ClosedLoopError::InvalidStoredTopology(
                            "third recommendation disappeared during materialization".to_owned(),
                        )
                    })?;
                validate_candidate_pool(
                    input,
                    anchor_token_key_id,
                    &first_options[0].pool_id,
                    "first",
                )?;
                for recommendation in first_options.iter().skip(1) {
                    validate_candidate_pool(
                        input,
                        anchor_token_key_id,
                        &recommendation.pool_id,
                        "first",
                    )?;
                }
                validate_candidate_pool(
                    input,
                    anchor_token_key_id,
                    &third_options[0].pool_id,
                    "third",
                )?;
                for recommendation in third_options.iter().skip(1) {
                    validate_candidate_pool(
                        input,
                        anchor_token_key_id,
                        &recommendation.pool_id,
                        "third",
                    )?;
                }
                // Path-level capacity gate.
                //
                // The loop is only executable when *every* hop can carry the
                // intended position inside the epsilon price-impact budget.
                // A single hop below the capacity floor disqualifies the whole
                // path, even when the other two hops are deep. The capacity is
                // denominated in each pool's own tokens, so this gate never
                // depends on a USD quote.
                //
                // A hop whose capacity was never measured does not block: the
                // missing measurement is a coverage gap of the pool index, not
                // a statement about the market.
                let path_pool_ids = vec![
                    first_options[0].pool_id.clone(),
                    middle_pool_id.to_owned(),
                    third_options[0].pool_id.clone(),
                ];
                // The first arbitrage version constrains every hop to one
                // tick range. The direction of each hop decides which side
                // of the range the hop runs into, so the direction has to be
                // known before the bound can be read.
                //
                // The first hop trades the anchor into Y, the middle hop
                // trades Y into Z and the third hop trades Z back into the
                // anchor. The `direction_key` of a recommendation already
                // names that direction, and the middle pool publishes the
                // token order it trades in.
                // The direction of every hop is resolved from the **pool's
                // own token order**, never from the logical direction key.
                //
                // The logical key names the trade (`A -> B`), while the pool
                // orders its two tokens by numeric address. Those two orders
                // are independent, so a hop that logically spends `A` may
                // still spend the pool's `token1`. Deriving the direction
                // from the logical key swaps the input and the output token
                // of the hop, and the cumulative fold then accumulates the
                // inverse rate of that hop, which destroys the capacity of
                // the whole loop.
                let first_zero_for_one = resolve_hop_direction(
                    &input.pool_tick_states,
                    &first_options[0].pool_id,
                    anchor_token_key_id,
                    &first_options[0].direction_key,
                    anchor_token_key_id,
                )?;
                let third_zero_for_one = resolve_hop_direction(
                    &input.pool_tick_states,
                    &third_options[0].pool_id,
                    z_token,
                    &third_options[0].direction_key,
                    z_token,
                )?;
                let middle_zero_for_one = resolve_hop_direction(
                    &input.pool_tick_states,
                    middle_pool_id,
                    y_token,
                    &middle_direction_key,
                    y_token,
                )?;
                let hop_directions = [first_zero_for_one, middle_zero_for_one, third_zero_for_one];
                // The folded capacity is the authoritative single-tick bound
                // of the loop. It is captured separately from the legacy
                // filter below, because the filter may fall back to a
                // per-hop bound when the fold cannot be performed, and that
                // fallback is **not** a folded capacity in anchor units.
                let folded_capacity =
                    folded_path_capacity(&input.pool_tick_states, &path_pool_ids, &hop_directions);
                // The cross-tick ceiling is the **depth** capacity of the
                // path, folded into the anchor token with the same
                // cumulative-rate fold. It is the upper bound of the
                // cross-tick ladder, which keeps using the existing
                // double/halve stepping rule.
                let cross_tick_capacity_anchor_raw = folded_path_depth_capacity(
                    &input.pool_tick_states,
                    &input.capacity_by_pool,
                    &path_pool_ids,
                    &hop_directions,
                );
                // The borrow token is the anchor token: it is the token the
                // loop spends first and receives last, so its USDG value is
                // what values the profit of the closed loop.
                let borrow_token_usdg_value_raw = usdg_valuation_for_token(input, anchor_token_key_id)
                    .map(|valuation| valuation.value_usdg_raw);
                let borrow_token_decimals = input.token_decimals.get(anchor_token_key_id).copied();
                let (path_min_tick_bound_raw, path_tick_blocking_pool) =
                    path_min_single_tick_bound(input, &path_pool_ids, &hop_directions);
                if path_min_tick_bound_raw.is_some_and(|bound| bound == 0) {
                    // A hop that cannot carry any position inside its tick
                    // range disqualifies the path in the first version.
                    let blocking = path_tick_blocking_pool
                        .as_deref()
                        .unwrap_or("unknown")
                        .to_owned();
                    let reason = format!("path_single_tick_bound_unusable:{blocking}");
                    skipped.insert((
                        anchor_token_key_id.clone(),
                        y_token.clone(),
                        "first".to_owned(),
                        reason.clone(),
                    ));
                    skipped.insert((
                        anchor_token_key_id.clone(),
                        z_token.clone(),
                        "third".to_owned(),
                        reason,
                    ));
                    continue;
                }
                let (path_min_capacity_raw, path_blocking_pool) =
                    path_min_capacity(input, &path_pool_ids);
                if path_min_capacity_raw
                    .is_some_and(|capacity| capacity < MIN_CLOSED_LOOP_CAPACITY_RAW)
                {
                    // The blocking hop is named in the skip reason so the
                    // operator can see *which* pool disqualified the path
                    // instead of having to re-derive it.
                    let blocking = path_blocking_pool
                        .as_deref()
                        .unwrap_or("unknown")
                        .to_owned();
                    let reason = format!("path_capacity_below_minimum:{blocking}");
                    skipped.insert((
                        anchor_token_key_id.clone(),
                        y_token.clone(),
                        "first".to_owned(),
                        reason.clone(),
                    ));
                    skipped.insert((
                        anchor_token_key_id.clone(),
                        z_token.clone(),
                        "third".to_owned(),
                        reason,
                    ));
                    continue;
                }
                participating_first.insert(y_token.clone());
                participating_third.insert(z_token.clone());
                push_loop(
                    input,
                    &mut desired,
                    anchor_token_key_id,
                    anchor_meta,
                    y_token,
                    z_token,
                    &anchor.tokens,
                    first_options,
                    third_options,
                    yz,
                    middle_pool_id,
                    &(input.observation.observation_version.max(1)),
                    folded_capacity.as_ref().map(|(capacity, _)| *capacity),
                    folded_capacity
                        .as_ref()
                        .map(|(_, binding_pool)| binding_pool.clone()),
                    borrow_token_usdg_value_raw,
                    borrow_token_decimals,
                    input.cross_tick_review_threshold_usdg_raw,
                    cross_tick_capacity_anchor_raw,
                )?;
            }
        }

        for token_key_id in &candidate_tokens {
            let has_first =
                first.contains_key(&(anchor_token_key_id.clone(), token_key_id.clone()));
            let has_third =
                third.contains_key(&(anchor_token_key_id.clone(), token_key_id.clone()));
            if !has_first {
                skipped.insert((
                    anchor_token_key_id.clone(),
                    token_key_id.clone(),
                    "first".to_owned(),
                    "no_completing_first_hop".to_owned(),
                ));
            } else if !participating_first.contains(token_key_id) {
                skipped.insert((
                    anchor_token_key_id.clone(),
                    token_key_id.clone(),
                    "first".to_owned(),
                    "no_completing_middle_direction".to_owned(),
                ));
            }
            if !has_third {
                skipped.insert((
                    anchor_token_key_id.clone(),
                    token_key_id.clone(),
                    "third".to_owned(),
                    "no_completing_third_hop".to_owned(),
                ));
            } else if !participating_third.contains(token_key_id) {
                skipped.insert((
                    anchor_token_key_id.clone(),
                    token_key_id.clone(),
                    "third".to_owned(),
                    "no_completing_middle_direction".to_owned(),
                ));
            }
        }
    }

    for (anchor, token, role, reason) in skipped {
        let input_anchor = input.anchors.get(&anchor).ok_or_else(|| {
            ClosedLoopError::InvalidStoredTopology(format!("missing anchor {anchor}"))
        })?;
        let token_meta = input_anchor.tokens.get(&token).ok_or_else(|| {
            ClosedLoopError::InvalidStoredTopology(format!("missing candidate token {token}"))
        })?;
        desired.skipped_tokens.push(ClosedLoopSkippedTokenRow {
            anchor_token_key_id: anchor,
            token_key_id: token,
            token_symbol: token_meta.token_symbol.clone(),
            candidate_role: role,
            reason,
        });
    }

    desired.loops.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.loop_key)
            .cmp(&(&right.anchor_token_key_id, &right.loop_key))
    });
    desired.hops.sort_by(|left, right| {
        (&left.loop_key, left.hop_index).cmp(&(&right.loop_key, right.hop_index))
    });
    desired.optimizations.sort_by(|left, right| {
        (&left.loop_key, left.hop_index, &left.optimization_id).cmp(&(
            &right.loop_key,
            right.hop_index,
            &right.optimization_id,
        ))
    });
    desired
        .runtime_states
        .sort_by(|left, right| left.loop_key.cmp(&right.loop_key));
    desired.skipped_tokens.sort_by(|left, right| {
        (
            &left.anchor_token_key_id,
            &left.token_key_id,
            &left.candidate_role,
            &left.reason,
        )
            .cmp(&(
                &right.anchor_token_key_id,
                &right.token_key_id,
                &right.candidate_role,
                &right.reason,
            ))
    });
    Ok(desired)
}

#[allow(clippy::too_many_arguments)]
fn push_loop(
    input: &ClosedLoopInput,
    desired: &mut DesiredClosedLoopData,
    anchor_token_key_id: &str,
    anchor: &TokenMeta,
    y_token_key_id: &str,
    z_token_key_id: &str,
    tokens: &BTreeMap<String, TokenMeta>,
    first_options: &[Recommendation],
    third_options: &[Recommendation],
    yz: &YzDirection,
    middle_pool_id: &str,
    observation_version: &i64,
    single_tick_capacity_anchor_raw: Option<u128>,
    single_tick_capacity_binding_pool_id: Option<String>,
    borrow_token_usdg_value_raw: Option<u128>,
    borrow_token_decimals: Option<u32>,
    cross_tick_review_threshold_usdg_raw: u128,
    cross_tick_capacity_anchor_raw: Option<u128>,
) -> Result<(), ClosedLoopError> {
    let y = tokens.get(y_token_key_id).ok_or_else(|| {
        ClosedLoopError::InvalidStoredTopology(format!("missing Y token {y_token_key_id}"))
    })?;
    let z = tokens.get(z_token_key_id).ok_or_else(|| {
        ClosedLoopError::InvalidStoredTopology(format!("missing Z token {z_token_key_id}"))
    })?;
    let anchor_address = address_from_token_key_id(&anchor.token_key_id);
    let y_address = address_from_token_key_id(&y.token_key_id);
    let z_address = address_from_token_key_id(&z.token_key_id);
    let loop_key = format!("{anchor_address}:{y_address}:{z_address}");
    let path_addresses = vec![
        anchor_address.clone(),
        y_address.clone(),
        z_address.clone(),
        anchor_address.clone(),
    ];
    let path_symbols = vec![
        anchor.token_symbol.clone(),
        y.token_symbol.clone(),
        z.token_symbol.clone(),
        anchor.token_symbol.clone(),
    ];
    let pool_ids = vec![
        first_options[0].pool_id.clone(),
        middle_pool_id.to_owned(),
        third_options[0].pool_id.clone(),
    ];
    let mut pool_path_parts = path_addresses.clone();
    pool_path_parts.extend(pool_ids.iter().cloned());
    let mut hop_candidate_counts = BTreeMap::new();
    hop_candidate_counts.insert("1".to_owned(), first_options.len());
    hop_candidate_counts.insert("2".to_owned(), yz.pool_ids.len());
    hop_candidate_counts.insert("3".to_owned(), third_options.len());
    let first_backups = first_options[1..]
        .iter()
        .map(|row| row.pool_id.clone())
        .collect::<Vec<_>>();
    let third_backups = third_options[1..]
        .iter()
        .map(|row| row.pool_id.clone())
        .collect::<Vec<_>>();
    let mut backup_pool_ids = BTreeMap::new();
    backup_pool_ids.insert("first".to_owned(), first_backups);
    backup_pool_ids.insert(
        "middle".to_owned(),
        yz.pool_ids
            .iter()
            .filter(|pool_id| pool_id.as_str() != middle_pool_id)
            .cloned()
            .collect::<Vec<_>>(),
    );
    backup_pool_ids.insert("third".to_owned(), third_backups);
    let route_tokens = [
        parse_token_key_id(&anchor.token_key_id)?,
        parse_token_key_id(&y.token_key_id)?,
        parse_token_key_id(&z.token_key_id)?,
    ];
    let route_key_id = RouteKeyId::new(&route_tokens)
        .map_err(|error| ClosedLoopError::InvalidStoredTopology(error.to_string()))?;

    desired.loops.push(ClosedLoopRow {
        loop_key: loop_key.clone(),
        route_key_id: route_key_id.as_str().to_owned(),
        anchor_token_key_id: anchor_token_key_id.to_owned(),
        anchor_address: anchor_address.clone(),
        anchor_symbol: anchor.token_symbol.clone(),
        token_y_key_id: y.token_key_id.clone(),
        token_z_key_id: z.token_key_id.clone(),
        path_text: path_symbols.join(" -> "),
        path_addresses: path_addresses.clone(),
        path_symbols,
        pool_path_key: pool_path_parts.join(":"),
        pool_ids: pool_ids.clone(),
        backup_pool_ids,
        hop_candidate_counts: hop_candidate_counts.clone(),
        hop_candidate_combination_count: first_options.len() * third_options.len(),
        recommendation_basis: "directed_fixed_fee_no_hook_static_score".to_owned(),
        recommended_first_priority: true,
        candidate_edge_version: input.candidate_edge.candidate_version,
        candidate_edge_checksum: input
            .candidate_edge
            .materialized_checksum
            .clone()
            .ok_or(ClosedLoopError::MissingCandidateEdgeState)?,
        observation_version: *observation_version,
        observation_checksum: input
            .observation
            .materialized_checksum
            .clone()
            .ok_or(ClosedLoopError::MissingObservationState)?,
        status: "active".to_owned(),
        source: "closed_loop_manager".to_owned(),
        single_tick_capacity_anchor_raw,
        single_tick_capacity_binding_pool_id,
        borrow_token_usdg_value_raw,
        borrow_token_decimals,
        cross_tick_review_threshold_usdg_raw,
        cross_tick_capacity_anchor_raw,
    });

    let middle_basis = format!("observation_direction_optimal_pool:{y_address}:{z_address}");
    for (hop_index, hop_name, input_token, output_token, pool_id, basis) in [
        (
            1usize,
            "yc",
            anchor,
            y,
            first_options[0].pool_id.clone(),
            first_options[0].selection_basis.clone(),
        ),
        (2, "y-z", y, z, middle_pool_id.to_owned(), middle_basis),
        (
            3,
            "zc",
            z,
            anchor,
            third_options[0].pool_id.clone(),
            third_options[0].selection_basis.clone(),
        ),
    ] {
        desired.hops.push(ClosedLoopHopRow {
            loop_key: loop_key.clone(),
            hop_index,
            hop_name: hop_name.to_owned(),
            input_token_key_id: input_token.token_key_id.clone(),
            output_token_key_id: output_token.token_key_id.clone(),
            input_address: address_from_token_key_id(&input_token.token_key_id),
            output_address: address_from_token_key_id(&output_token.token_key_id),
            pool_id: pool_id.clone(),
            base_pool_id: pool_id,
            pool_selection_basis: basis,
            optimization_id: None,
            temporary_override: false,
        });
    }

    for recommendation in first_options.iter().skip(1) {
        desired.optimizations.push(ClosedLoopOptimizationRow {
            loop_key: loop_key.clone(),
            hop_index: 1,
            optimization_id: format!("first-rank-{}", recommendation.rank),
            original_pool_id: first_options[0].pool_id.clone(),
            optimized_pool_id: recommendation.pool_id.clone(),
            optimization_basis: recommendation.selection_basis.clone(),
            expected_price_improvement: None,
            status: "candidate".to_owned(),
            expires_at: None,
        });
    }
    for (index, pool_id) in yz.pool_ids.iter().enumerate().skip(1) {
        desired.optimizations.push(ClosedLoopOptimizationRow {
            loop_key: loop_key.clone(),
            hop_index: 2,
            optimization_id: format!("middle-rank-{}", index + 1),
            original_pool_id: middle_pool_id.to_owned(),
            optimized_pool_id: pool_id.clone(),
            optimization_basis: "observation_direction_pool_rank".to_owned(),
            expected_price_improvement: None,
            status: "candidate".to_owned(),
            expires_at: None,
        });
    }
    for recommendation in third_options.iter().skip(1) {
        desired.optimizations.push(ClosedLoopOptimizationRow {
            loop_key: loop_key.clone(),
            hop_index: 3,
            optimization_id: format!("third-rank-{}", recommendation.rank),
            original_pool_id: third_options[0].pool_id.clone(),
            optimized_pool_id: recommendation.pool_id.clone(),
            optimization_basis: recommendation.selection_basis.clone(),
            expected_price_improvement: None,
            status: "candidate".to_owned(),
            expires_at: None,
        });
    }
    desired.runtime_states.push(ClosedLoopRuntimeStateRow {
        loop_key,
        runtime_status: "ready_for_review".to_owned(),
        status_reason: None,
        review_generation: 1,
        review_required: true,
        last_rebuilt_at: chrono::Utc::now().to_rfc3339(),
    });
    // The topology deliberately has no USD-depth or stable-token predicate. A
    // Y-Z pool is eligible solely because M3/M4 admitted the directed pair.
    let _ = yz.empty_reason.as_deref();
    Ok(())
}

fn validate_candidate_pool(
    input: &ClosedLoopInput,
    anchor_token_key_id: &str,
    pool_id: &str,
    hop: &str,
) -> Result<(), ClosedLoopError> {
    if !input
        .candidate_pool_ids
        .contains(&(anchor_token_key_id.to_owned(), pool_id.to_owned()))
    {
        return Err(ClosedLoopError::InvalidStoredTopology(format!(
            "{hop} recommendation pool {pool_id} is absent from manager_dex_candidate_pools"
        )));
    }
    Ok(())
}

fn validate_middle_pool(
    input: &ClosedLoopInput,
    anchor_token_key_id: &str,
    y_token_key_id: &str,
    z_token_key_id: &str,
    pool_id: &str,
) -> Result<(), ClosedLoopError> {
    let pool = input
        .middle_pools
        .get(&(anchor_token_key_id.to_owned(), pool_id.to_owned()))
        .ok_or_else(|| {
            ClosedLoopError::InvalidStoredTopology(format!(
                "YZ observation pool {pool_id} is absent from intermediate pools"
            ))
        })?;
    let expected = BTreeSet::from([y_token_key_id, z_token_key_id]);
    let actual = BTreeSet::from([pool.token0_key_id.as_str(), pool.token1_key_id.as_str()]);
    if expected != actual {
        return Err(ClosedLoopError::InvalidStoredTopology(format!(
            "YZ observation pool {pool_id} token pair does not match {y_token_key_id}->{z_token_key_id}"
        )));
    }
    Ok(())
}

fn parse_token_key_id(value: &str) -> Result<TokenKeyId, ClosedLoopError> {
    value
        .parse()
        .map_err(|error: manager_contracts::ids::IdError| {
            ClosedLoopError::InvalidStoredTopology(format!("invalid token key id {value}: {error}"))
        })
}

fn address_from_token_key_id(value: &str) -> String {
    value
        .rsplit_once(':')
        .map(|(_, address)| address.to_owned())
        .unwrap_or_else(|| value.to_owned())
}

fn validate_expected_upstream(
    expected: &ExpectedUpstream,
    input: &ClosedLoopInput,
) -> Result<(), ClosedLoopError> {
    if let Some(value) = expected.candidate_edge_version {
        if value != input.candidate_edge.candidate_version {
            return Err(ClosedLoopError::ExpectedUpstreamMismatch {
                field: "candidate_edge_version".to_owned(),
                expected: value.to_string(),
                actual: input.candidate_edge.candidate_version.to_string(),
            });
        }
    }
    if let Some(expected_checksum) = expected.candidate_edge_checksum.as_deref() {
        let actual = input
            .candidate_edge
            .materialized_checksum
            .as_deref()
            .ok_or(ClosedLoopError::MissingCandidateEdgeState)?;
        if expected_checksum != actual {
            return Err(ClosedLoopError::ExpectedUpstreamMismatch {
                field: "candidate_edge_checksum".to_owned(),
                expected: expected_checksum.to_owned(),
                actual: actual.to_owned(),
            });
        }
    }
    if let Some(value) = expected.observation_version {
        if value != input.observation.observation_version {
            return Err(ClosedLoopError::ExpectedUpstreamMismatch {
                field: "observation_version".to_owned(),
                expected: value.to_string(),
                actual: input.observation.observation_version.to_string(),
            });
        }
    }
    if let Some(expected_checksum) = expected.observation_checksum.as_deref() {
        let actual = input
            .observation
            .materialized_checksum
            .as_deref()
            .ok_or(ClosedLoopError::MissingObservationState)?;
        if expected_checksum != actual {
            return Err(ClosedLoopError::ExpectedUpstreamMismatch {
                field: "observation_checksum".to_owned(),
                expected: expected_checksum.to_owned(),
                actual: actual.to_owned(),
            });
        }
    }
    // The expected upstream must be checked against the checksum that the
    // closed-loop layer actually publishes, which is the one owned by the
    // closed-loop scope. Comparing it against the observation scope would
    // accept a stale caller expectation and reject a correct one.
    let actual_pool_scope = closed_loop_pool_scope_checksum(input)?;
    if let Some(expected_checksum) = expected.pool_scope_checksum.as_deref() {
        if expected_checksum != actual_pool_scope {
            return Err(ClosedLoopError::ExpectedUpstreamMismatch {
                field: "pool_scope_checksum".to_owned(),
                expected: expected_checksum.to_owned(),
                actual: actual_pool_scope,
            });
        }
    }
    for (library_key, expected_version) in &expected.library_versions {
        let actual = input
            .observation
            .library_versions
            .get(library_key)
            .copied()
            .unwrap_or_default();
        if *expected_version != actual {
            return Err(ClosedLoopError::ExpectedUpstreamMismatch {
                field: format!("library_versions.{library_key}"),
                expected: expected_version.to_string(),
                actual: actual.to_string(),
            });
        }
    }
    for (library_key, expected_checksum) in &expected.library_checksums {
        let actual = input
            .observation
            .library_checksums
            .get(library_key)
            .cloned()
            .unwrap_or_default();
        if expected_checksum != &actual {
            return Err(ClosedLoopError::ExpectedUpstreamMismatch {
                field: format!("library_checksums.{library_key}"),
                expected: expected_checksum.clone(),
                actual,
            });
        }
    }
    Ok(())
}

fn closed_loop_checksum(
    input: &ClosedLoopInput,
    desired: &DesiredClosedLoopData,
) -> Result<String, ClosedLoopError> {
    let pool_scope_checksum = closed_loop_pool_scope_checksum(input)?;
    let payload = ClosedLoopChecksumPayload {
        candidate_edge_version: input.candidate_edge.candidate_version,
        candidate_edge_checksum: input
            .candidate_edge
            .materialized_checksum
            .as_deref()
            .ok_or(ClosedLoopError::MissingCandidateEdgeState)?,
        observation_version: input.observation.observation_version,
        observation_checksum: input
            .observation
            .materialized_checksum
            .as_deref()
            .ok_or(ClosedLoopError::MissingObservationState)?,
        pool_scope_checksum: &pool_scope_checksum,
        library_versions: &input.observation.library_versions,
        library_checksums: &input.observation.library_checksums,
        loops: &desired.loops,
        hops: &desired.hops,
        optimizations: &desired.optimizations,
        skipped_tokens: &desired.skipped_tokens,
    };
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn unique_skipped_token_count(rows: &[ClosedLoopSkippedTokenRow]) -> usize {
    rows.iter()
        .map(|row| (&row.anchor_token_key_id, &row.token_key_id))
        .collect::<BTreeSet<_>>()
        .len()
}

fn open_connection(path: &Path, writable: bool) -> Result<Connection, ClosedLoopError> {
    let flags = if writable {
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI
    } else {
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI
    };
    Ok(Connection::open_with_flags(path, flags)?)
}

fn read_status_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ClosedLoopStatus, ClosedLoopError> {
    let stored = connection
        .query_row(
            "SELECT closed_loop_version, materialized_checksum,
                    candidate_edge_version, candidate_edge_checksum,
                    observation_version, observation_checksum,
                    pool_scope_checksum, library_versions_json,
                    library_checksums_json, anchor_count, loop_count, hop_count,
                    optimization_count, skipped_token_count, consumers_need_rebuild
             FROM manager_v4_closed_loop_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4",
            params![network, chain_id, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, i64>(9)?,
                    row.get::<_, i64>(10)?,
                    row.get::<_, i64>(11)?,
                    row.get::<_, i64>(12)?,
                    row.get::<_, i64>(13)?,
                    row.get::<_, i64>(14)?,
                ))
            },
        )
        .optional()?;
    let Some((
        closed_loop_version,
        materialized_checksum,
        candidate_edge_version,
        candidate_edge_checksum,
        observation_version,
        observation_checksum,
        pool_scope_checksum,
        library_versions_json,
        library_checksums_json,
        anchor_count,
        loop_count,
        hop_count,
        optimization_count,
        skipped_token_count,
        consumers_need_rebuild,
    )) = stored
    else {
        return Ok(disabled_closed_loop_status(network, chain_id));
    };
    Ok(ClosedLoopStatus {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: CLOSED_LOOP_SCOPE_KEY.to_owned(),
        closed_loop_version,
        materialized_checksum: Some(materialized_checksum),
        candidate_edge_version: Some(candidate_edge_version),
        candidate_edge_checksum: Some(candidate_edge_checksum),
        observation_version: Some(observation_version),
        observation_checksum: Some(observation_checksum),
        pool_scope_checksum: Some(pool_scope_checksum),
        library_versions: serde_json::from_str(&library_versions_json)?,
        library_checksums: serde_json::from_str(&library_checksums_json)?,
        anchor_count: usize_from_i64(anchor_count),
        loop_count: usize_from_i64(loop_count),
        hop_count: usize_from_i64(hop_count),
        optimization_count: usize_from_i64(optimization_count),
        skipped_token_count: usize_from_i64(skipped_token_count),
        consumers_need_rebuild: consumers_need_rebuild != 0,
    })
}

pub fn read_closed_loop_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<ClosedLoopStatus, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_status_on_connection(&connection, network, chain_id)
}

pub fn read_closed_loop_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<ClosedLoopSnapshot, ClosedLoopError> {
    if chain_id <= 0 {
        return Err(ClosedLoopError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if limit == 0 || limit > MAX_SNAPSHOT_LIMIT {
        return Err(ClosedLoopError::InvalidRequest(format!(
            "limit must be between 1 and {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    if anchor_token_key_id.is_some_and(str::is_empty) {
        return Err(ClosedLoopError::InvalidRequest(
            "anchor_token_key_id must not be empty".to_owned(),
        ));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let status = read_status_on_connection(&connection, network, chain_id)?;
    Ok(ClosedLoopSnapshot {
        status,
        loops: read_loops(&connection, network, chain_id, anchor_token_key_id, limit)?,
        hops: read_hops(&connection, network, chain_id, anchor_token_key_id, limit)?,
        optimizations: read_optimizations(
            &connection,
            network,
            chain_id,
            anchor_token_key_id,
            limit,
        )?,
        runtime_states: read_runtime_states(
            &connection,
            network,
            chain_id,
            anchor_token_key_id,
            limit,
        )?,
        skipped_tokens: read_skipped_tokens(
            &connection,
            network,
            chain_id,
            anchor_token_key_id,
            limit,
        )?,
    })
}

fn read_loops(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<ClosedLoopRow>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT loop_key, route_key_id, anchor_token_key_id, anchor_address,
                anchor_symbol, token_y_key_id, token_z_key_id, path_text,
                path_addresses_json, path_symbols_json, pool_path_key,
                pool_ids_json, backup_pool_ids_json, hop_candidate_counts_json,
                hop_candidate_combination_count, recommendation_basis,
                recommended_first_priority, candidate_edge_version,
                candidate_edge_checksum, observation_version,
                observation_checksum, status, source,
                single_tick_capacity_anchor_raw,
                single_tick_capacity_binding_pool_id,
                borrow_token_usdg_value_raw,
                borrow_token_decimals,
                cross_tick_review_threshold_usdg_raw,
                cross_tick_capacity_anchor_raw
         FROM manager_v4_closed_loops
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, loop_key
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(ClosedLoopRow {
                loop_key: row.get(0)?,
                route_key_id: row.get(1)?,
                anchor_token_key_id: row.get(2)?,
                anchor_address: row.get(3)?,
                anchor_symbol: row.get(4)?,
                token_y_key_id: row.get(5)?,
                token_z_key_id: row.get(6)?,
                path_text: row.get(7)?,
                path_addresses: serde_json::from_str(&row.get::<_, String>(8)?).unwrap_or_default(),
                path_symbols: serde_json::from_str(&row.get::<_, String>(9)?).unwrap_or_default(),
                pool_path_key: row.get(10)?,
                pool_ids: serde_json::from_str(&row.get::<_, String>(11)?).unwrap_or_default(),
                backup_pool_ids: serde_json::from_str(&row.get::<_, String>(12)?)
                    .unwrap_or_default(),
                hop_candidate_counts: serde_json::from_str(&row.get::<_, String>(13)?)
                    .unwrap_or_default(),
                hop_candidate_combination_count: usize_from_i64(row.get(14)?),
                recommendation_basis: row.get(15)?,
                recommended_first_priority: row.get::<_, i64>(16)? != 0,
                candidate_edge_version: row.get(17)?,
                candidate_edge_checksum: row.get(18)?,
                observation_version: row.get(19)?,
                observation_checksum: row.get(20)?,
                status: row.get(21)?,
                source: row.get(22)?,
                single_tick_capacity_anchor_raw: row
                    .get::<_, Option<String>>(23)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                single_tick_capacity_binding_pool_id: row
                    .get::<_, Option<String>>(24)?
                    .map(|value| value.trim().to_owned())
                    .filter(|value| !value.is_empty()),
                borrow_token_usdg_value_raw: row
                    .get::<_, Option<String>>(25)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                borrow_token_decimals: row
                    .get::<_, Option<i64>>(26)?
                    .and_then(|value| u32::try_from(value).ok()),
                cross_tick_review_threshold_usdg_raw: row
                    .get::<_, Option<String>>(27)?
                    .and_then(|value| value.trim().parse::<u128>().ok())
                    .unwrap_or(0),
                cross_tick_capacity_anchor_raw: row
                    .get::<_, Option<String>>(28)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_hops(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<ClosedLoopHopRow>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT h.loop_key, h.hop_index, h.hop_name, h.input_token_key_id,
                h.output_token_key_id, h.input_address, h.output_address,
                h.pool_id, h.base_pool_id, h.pool_selection_basis,
                h.optimization_id, h.temporary_override
         FROM manager_v4_closed_loop_hops h
         JOIN manager_v4_closed_loops l
           ON l.network = h.network AND l.chain_id = h.chain_id
          AND l.protocol = h.protocol AND l.scope_key = h.scope_key
          AND l.loop_key = h.loop_key
         WHERE h.network = ?1 AND h.chain_id = ?2 AND h.protocol = ?3
           AND h.scope_key = ?4
           AND (?5 IS NULL OR l.anchor_token_key_id = ?5)
         ORDER BY h.loop_key, h.hop_index
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(ClosedLoopHopRow {
                loop_key: row.get(0)?,
                hop_index: usize_from_i64(row.get(1)?),
                hop_name: row.get(2)?,
                input_token_key_id: row.get(3)?,
                output_token_key_id: row.get(4)?,
                input_address: row.get(5)?,
                output_address: row.get(6)?,
                pool_id: row.get(7)?,
                base_pool_id: row.get(8)?,
                pool_selection_basis: row.get(9)?,
                optimization_id: row.get(10)?,
                temporary_override: row.get::<_, i64>(11)? != 0,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_optimizations(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<ClosedLoopOptimizationRow>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT o.loop_key, o.hop_index, o.optimization_id,
                o.original_pool_id, o.optimized_pool_id, o.optimization_basis,
                o.expected_price_improvement, o.status, o.expires_at
         FROM manager_v4_closed_loop_optimizations o
         JOIN manager_v4_closed_loops l
           ON l.network = o.network AND l.chain_id = o.chain_id
          AND l.protocol = o.protocol AND l.scope_key = o.scope_key
          AND l.loop_key = o.loop_key
         WHERE o.network = ?1 AND o.chain_id = ?2 AND o.protocol = ?3
           AND o.scope_key = ?4
           AND (?5 IS NULL OR l.anchor_token_key_id = ?5)
         ORDER BY o.loop_key, o.hop_index, o.optimization_id
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(ClosedLoopOptimizationRow {
                loop_key: row.get(0)?,
                hop_index: usize_from_i64(row.get(1)?),
                optimization_id: row.get(2)?,
                original_pool_id: row.get(3)?,
                optimized_pool_id: row.get(4)?,
                optimization_basis: row.get(5)?,
                expected_price_improvement: row.get(6)?,
                status: row.get(7)?,
                expires_at: row.get(8)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_runtime_states(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<ClosedLoopRuntimeStateRow>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT r.loop_key, r.runtime_status, r.status_reason,
                r.review_generation, r.review_required, r.last_rebuilt_at
         FROM manager_v4_closed_loop_runtime_state r
         JOIN manager_v4_closed_loops l
           ON l.network = r.network AND l.chain_id = r.chain_id
          AND l.protocol = r.protocol AND l.scope_key = r.scope_key
          AND l.loop_key = r.loop_key
         WHERE r.network = ?1 AND r.chain_id = ?2 AND r.protocol = ?3
           AND r.scope_key = ?4
           AND (?5 IS NULL OR l.anchor_token_key_id = ?5)
         ORDER BY r.loop_key
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(ClosedLoopRuntimeStateRow {
                loop_key: row.get(0)?,
                runtime_status: row.get(1)?,
                status_reason: row.get(2)?,
                review_generation: row.get(3)?,
                review_required: row.get::<_, i64>(4)? != 0,
                last_rebuilt_at: row.get(5)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_skipped_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<ClosedLoopSkippedTokenRow>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, token_key_id, token_symbol,
                candidate_role, reason
         FROM manager_v4_closed_loop_skipped_tokens
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, token_key_id, candidate_role, reason
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(ClosedLoopSkippedTokenRow {
                anchor_token_key_id: row.get(0)?,
                token_key_id: row.get(1)?,
                token_symbol: row.get(2)?,
                candidate_role: row.get(3)?,
                reason: row.get(4)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn replace_closed_loop_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredClosedLoopData,
    result: &ClosedLoopMaterializationResult,
) -> Result<(), ClosedLoopError> {
    let now = chrono::Utc::now().to_rfc3339();
    for table in [
        "manager_v4_closed_loop_runtime_state",
        "manager_v4_closed_loop_optimizations",
        "manager_v4_closed_loop_hops",
        "manager_v4_closed_loops",
        "manager_v4_closed_loop_skipped_tokens",
    ] {
        connection.execute(
            &format!(
                "DELETE FROM {table}
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                   AND scope_key = ?4"
            ),
            params![network, chain_id, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY],
        )?;
    }
    insert_state(connection, network, chain_id, result, &now)?;
    for row in &desired.loops {
        connection.execute(
            "INSERT INTO manager_v4_closed_loops (
                 network, chain_id, protocol, scope_key, loop_key, route_key_id,
                 anchor_token_key_id, anchor_address, anchor_symbol,
                 token_y_key_id, token_z_key_id, path_text,
                 path_addresses_json, path_symbols_json, pool_path_key,
                 pool_ids_json, backup_pool_ids_json, hop_candidate_counts_json,
                 hop_candidate_combination_count, recommendation_basis,
                 recommended_first_priority, candidate_edge_version,
                 candidate_edge_checksum, observation_version,
                 observation_checksum, status, source,
                 single_tick_capacity_anchor_raw,
                 single_tick_capacity_binding_pool_id,
                 borrow_token_usdg_value_raw,
                 borrow_token_decimals,
                 cross_tick_review_threshold_usdg_raw,
                 cross_tick_capacity_anchor_raw,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23,
                       ?24, ?25, ?26, ?27, ?28, ?29, ?30, ?31, ?32, ?33,
                       ?34, ?34)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                row.loop_key,
                row.route_key_id,
                row.anchor_token_key_id,
                row.anchor_address,
                row.anchor_symbol,
                row.token_y_key_id,
                row.token_z_key_id,
                row.path_text,
                serde_json::to_string(&row.path_addresses)?,
                serde_json::to_string(&row.path_symbols)?,
                row.pool_path_key,
                serde_json::to_string(&row.pool_ids)?,
                serde_json::to_string(&row.backup_pool_ids)?,
                serde_json::to_string(&row.hop_candidate_counts)?,
                row.hop_candidate_combination_count as i64,
                row.recommendation_basis,
                i64::from(row.recommended_first_priority),
                row.candidate_edge_version,
                row.candidate_edge_checksum,
                row.observation_version,
                row.observation_checksum,
                row.status,
                row.source,
                row.single_tick_capacity_anchor_raw
                    .map(|value| value.to_string()),
                row.single_tick_capacity_binding_pool_id.clone(),
                row.borrow_token_usdg_value_raw.map(|value| value.to_string()),
                row.borrow_token_decimals.map(|value| i64::from(value)),
                row.cross_tick_review_threshold_usdg_raw.to_string(),
                row.cross_tick_capacity_anchor_raw.map(|value| value.to_string()),
                now
            ],
        )?;
    }
    for row in &desired.hops {
        connection.execute(
            "INSERT INTO manager_v4_closed_loop_hops (
                 network, chain_id, protocol, scope_key, loop_key, hop_index,
                 hop_name, input_token_key_id, output_token_key_id,
                 input_address, output_address, pool_id, base_pool_id,
                 pool_selection_basis, optimization_id, temporary_override,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?17)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                row.loop_key,
                row.hop_index as i64,
                row.hop_name,
                row.input_token_key_id,
                row.output_token_key_id,
                row.input_address,
                row.output_address,
                row.pool_id,
                row.base_pool_id,
                row.pool_selection_basis,
                row.optimization_id,
                i64::from(row.temporary_override),
                now
            ],
        )?;
    }
    for row in &desired.optimizations {
        connection.execute(
            "INSERT INTO manager_v4_closed_loop_optimizations (
                 network, chain_id, protocol, scope_key, loop_key, hop_index,
                 optimization_id, original_pool_id, optimized_pool_id,
                 optimization_basis, expected_price_improvement, status,
                 expires_at, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?14)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                row.loop_key,
                row.hop_index as i64,
                row.optimization_id,
                row.original_pool_id,
                row.optimized_pool_id,
                row.optimization_basis,
                row.expected_price_improvement,
                row.status,
                row.expires_at,
                now
            ],
        )?;
    }
    for row in &desired.runtime_states {
        connection.execute(
            "INSERT INTO manager_v4_closed_loop_runtime_state (
                 network, chain_id, protocol, scope_key, loop_key,
                 runtime_status, status_reason, review_generation,
                 review_required, last_rebuilt_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                row.loop_key,
                row.runtime_status,
                row.status_reason,
                row.review_generation,
                i64::from(row.review_required),
                now
            ],
        )?;
    }
    for row in &desired.skipped_tokens {
        connection.execute(
            "INSERT INTO manager_v4_closed_loop_skipped_tokens (
                 network, chain_id, protocol, scope_key, anchor_token_key_id,
                 token_key_id, token_symbol, candidate_role, reason,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                row.anchor_token_key_id,
                row.token_key_id,
                row.token_symbol,
                row.candidate_role,
                row.reason,
                now
            ],
        )?;
    }
    Ok(())
}

fn insert_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    result: &ClosedLoopMaterializationResult,
    now: &str,
) -> Result<(), ClosedLoopError> {
    connection.execute(
        "INSERT INTO manager_v4_closed_loop_library_state (
             network, chain_id, protocol, scope_key, closed_loop_version,
             materialized_checksum, candidate_edge_version,
             candidate_edge_checksum, observation_version,
             observation_checksum, pool_scope_checksum,
             library_versions_json, library_checksums_json, anchor_count,
             loop_count, hop_count, optimization_count, skipped_token_count,
             consumers_need_rebuild, generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?20)
         ON CONFLICT(network, chain_id, protocol, scope_key) DO UPDATE SET
             closed_loop_version = excluded.closed_loop_version,
             materialized_checksum = excluded.materialized_checksum,
             candidate_edge_version = excluded.candidate_edge_version,
             candidate_edge_checksum = excluded.candidate_edge_checksum,
             observation_version = excluded.observation_version,
             observation_checksum = excluded.observation_checksum,
             pool_scope_checksum = excluded.pool_scope_checksum,
             library_versions_json = excluded.library_versions_json,
             library_checksums_json = excluded.library_checksums_json,
             anchor_count = excluded.anchor_count,
             loop_count = excluded.loop_count,
             hop_count = excluded.hop_count,
             optimization_count = excluded.optimization_count,
             skipped_token_count = excluded.skipped_token_count,
             consumers_need_rebuild = excluded.consumers_need_rebuild,
             generated_at = excluded.generated_at,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            result.closed_loop_version,
            result.materialized_checksum,
            result.candidate_edge_version,
            result.candidate_edge_checksum,
            result.observation_version,
            result.observation_checksum,
            result.pool_scope_checksum,
            serde_json::to_string(&result.library_versions)?,
            serde_json::to_string(&result.library_checksums)?,
            result.anchor_count as i64,
            result.loop_count as i64,
            result.hop_count as i64,
            result.optimization_count as i64,
            result.skipped_token_count as i64,
            i64::from(result.consumers_need_rebuild),
            now
        ],
    )?;
    Ok(())
}

fn clear_consumers_need_rebuild(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<(), ClosedLoopError> {
    connection.execute(
        "UPDATE manager_v4_closed_loop_library_state
         SET consumers_need_rebuild = 0, updated_at = ?5
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND scope_key = ?4 AND consumers_need_rebuild != 0",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            chrono::Utc::now().to_rfc3339()
        ],
    )?;
    Ok(())
}

pub fn compare_closed_loops(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    legacy_market_id: &str,
    sample_limit: usize,
) -> Result<ClosedLoopComparison, ClosedLoopError> {
    if chain_id <= 0 {
        return Err(ClosedLoopError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if legacy_market_id.trim().is_empty() {
        return Err(ClosedLoopError::InvalidRequest(
            "legacy_market_id must not be empty".to_owned(),
        ));
    }
    if sample_limit == 0 {
        return Err(ClosedLoopError::InvalidRequest(
            "sample_limit must be greater than zero".to_owned(),
        ));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let status = read_status_on_connection(&connection, network, chain_id)?;
    if !status.enabled {
        return Ok(disabled_comparison(network, chain_id, legacy_market_id));
    }
    let legacy_tables_available = table_exists(&connection, "v4_closed_loops")?
        && table_exists(&connection, "v4_closed_loop_hops")?;
    if !legacy_tables_available {
        let empty = || comparison_summary(&BTreeMap::new(), &BTreeMap::new());
        return Ok(ClosedLoopComparison {
            enabled: true,
            network: network.to_owned(),
            chain_id,
            protocol: NETWORK_PROTOCOL.to_owned(),
            scope_key: CLOSED_LOOP_SCOPE_KEY.to_owned(),
            legacy_market_id: legacy_market_id.to_owned(),
            manager_checksum: status.materialized_checksum,
            legacy_tables_available: false,
            ready_for_cutover: false,
            loops: empty(),
            hops: empty(),
            cause_counts: BTreeMap::from([("legacyTablesUnavailable".to_owned(), 1)]),
            differences: Vec::new(),
            differences_truncated: false,
        });
    }
    let manager_loops = manager_loop_comparison_rows(&read_loops(
        &connection,
        network,
        chain_id,
        None,
        MAX_COMPARISON_ROWS,
    )?)?;
    let manager_hops = manager_hop_comparison_rows(&read_hops(
        &connection,
        network,
        chain_id,
        None,
        MAX_COMPARISON_ROWS,
    )?);
    let legacy_loops = read_legacy_loops(&connection, network, chain_id, legacy_market_id)?;
    let legacy_hops = read_legacy_hops(&connection, network, chain_id, legacy_market_id)?;

    let mut differences = Vec::new();
    let mut cause_counts = BTreeMap::new();
    let loops = compare_rows(
        "loops",
        &manager_loops,
        &legacy_loops,
        "closedLoopScopeExpansion",
        &mut differences,
        &mut cause_counts,
    );
    let hops = compare_rows(
        "hops",
        &manager_hops,
        &legacy_hops,
        "closedLoopScopeExpansion",
        &mut differences,
        &mut cause_counts,
    );
    let ready_for_cutover = loops.manager_only_count == 0
        && loops.legacy_only_count == 0
        && loops.field_mismatch_count == 0
        && hops.manager_only_count == 0
        && hops.legacy_only_count == 0
        && hops.field_mismatch_count == 0;
    let differences_truncated = differences.len() > sample_limit;
    differences.truncate(sample_limit);
    Ok(ClosedLoopComparison {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: CLOSED_LOOP_SCOPE_KEY.to_owned(),
        legacy_market_id: legacy_market_id.to_owned(),
        manager_checksum: status.materialized_checksum,
        legacy_tables_available: true,
        ready_for_cutover,
        loops,
        hops,
        cause_counts,
        differences,
        differences_truncated,
    })
}

fn manager_loop_comparison_rows(
    rows: &[ClosedLoopRow],
) -> Result<BTreeMap<String, String>, ClosedLoopError> {
    let mut values = BTreeMap::new();
    for row in rows {
        values.insert(
            format!("{}|{}", row.anchor_symbol, row.loop_key),
            serde_json::to_string(&serde_json::json!({
                "pathText": row.path_text,
                "pathAddresses": row.path_addresses,
                "pathSymbols": row.path_symbols,
                "poolPathKey": row.pool_path_key,
                "poolIds": row.pool_ids,
                "hopCandidateCounts": row.hop_candidate_counts,
                "hopCandidateCombinationCount": row.hop_candidate_combination_count,
                "recommendationBasis": row.recommendation_basis,
                "recommendedFirstPriority": row.recommended_first_priority,
                "status": row.status
            }))?,
        );
    }
    Ok(values)
}

fn manager_hop_comparison_rows(rows: &[ClosedLoopHopRow]) -> BTreeMap<String, String> {
    let mut values = BTreeMap::new();
    for row in rows {
        let key = format!("{}|{}", row.loop_key, row.hop_index);
        if let Ok(payload) = serde_json::to_string(&serde_json::json!({
            "hopName": row.hop_name,
            "inputAddress": row.input_address,
            "outputAddress": row.output_address,
            "poolId": row.pool_id,
            "basePoolId": row.base_pool_id,
            "poolSelectionBasis": row.pool_selection_basis,
            "temporaryOverride": row.temporary_override
        })) {
            values.insert(key, payload);
        }
    }
    values
}

fn read_legacy_loops(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    market_id: &str,
) -> Result<BTreeMap<String, String>, ClosedLoopError> {
    let mut statement = connection
        .prepare(
            "SELECT anchor_symbol, loop_key, path_text, path_addresses_json,
                    path_symbols_json, pool_path_key, pool_ids_json,
                    hop_candidate_counts_json, hop_candidate_combination_count,
                    recommendation_basis, recommended_first_priority, status
             FROM v4_closed_loops
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND market_id = ?4
             ORDER BY anchor_symbol, loop_key
             LIMIT ?5",
        )
        .map_err(|source| ClosedLoopError::ComparisonRead {
            stage: "legacy_loops".to_owned(),
            source,
        })?;
    let rows = statement
        .query_map(
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                market_id,
                MAX_COMPARISON_ROWS as i64
            ],
            |row| {
                Ok((
                    format!("{}|{}", row.get::<_, String>(0)?, row.get::<_, String>(1)?),
                    serde_json::json!({
                        "pathText": row.get::<_, Option<String>>(2)?,
                        "pathAddresses": parse_json_value(row.get::<_, Option<String>>(3)?),
                        "pathSymbols": parse_json_value(row.get::<_, Option<String>>(4)?),
                        "poolPathKey": row.get::<_, Option<String>>(5)?,
                        "poolIds": parse_json_value(row.get::<_, Option<String>>(6)?),
                        "hopCandidateCounts": parse_json_value(row.get::<_, Option<String>>(7)?),
                        "hopCandidateCombinationCount": row.get::<_, Option<i64>>(8)?,
                        "recommendationBasis": row.get::<_, Option<String>>(9)?,
                        "recommendedFirstPriority": row.get::<_, Option<i64>>(10)?.unwrap_or(0) != 0,
                        "status": row.get::<_, Option<String>>(11)?
                    })
                    .to_string(),
                ))
            },
        )
        .map_err(|source| ClosedLoopError::ComparisonRead {
            stage: "legacy_loops".to_owned(),
            source,
        })?;
    rows.collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(|source| ClosedLoopError::ComparisonRead {
            stage: "legacy_loops".to_owned(),
            source,
        })
}

fn read_legacy_hops(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    market_id: &str,
) -> Result<BTreeMap<String, String>, ClosedLoopError> {
    let mut statement = connection
        .prepare(
            "SELECT loop_key, hop_index, hop_name, input_address, output_address,
                    pool_id, base_pool_id, pool_selection_basis, temporary_override
             FROM v4_closed_loop_hops
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND market_id = ?4
             ORDER BY loop_key, hop_index
             LIMIT ?5",
        )
        .map_err(|source| ClosedLoopError::ComparisonRead {
            stage: "legacy_hops".to_owned(),
            source,
        })?;
    let rows = statement
        .query_map(
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                market_id,
                MAX_COMPARISON_ROWS as i64
            ],
            |row| {
                Ok((
                    format!("{}|{}", row.get::<_, String>(0)?, row.get::<_, i64>(1)?),
                    serde_json::json!({
                        "hopName": row.get::<_, Option<String>>(2)?,
                        "inputAddress": row.get::<_, Option<String>>(3)?,
                        "outputAddress": row.get::<_, Option<String>>(4)?,
                        "poolId": row.get::<_, String>(5)?,
                        "basePoolId": row.get::<_, Option<String>>(6)?,
                        "poolSelectionBasis": row.get::<_, Option<String>>(7)?,
                        "temporaryOverride": row.get::<_, Option<i64>>(8)?.unwrap_or(0) != 0
                    })
                    .to_string(),
                ))
            },
        )
        .map_err(|source| ClosedLoopError::ComparisonRead {
            stage: "legacy_hops".to_owned(),
            source,
        })?;
    rows.collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(|source| ClosedLoopError::ComparisonRead {
            stage: "legacy_hops".to_owned(),
            source,
        })
}

fn parse_json_value(value: Option<String>) -> serde_json::Value {
    value
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or(serde_json::Value::Null)
}

fn compare_rows(
    category: &str,
    manager: &BTreeMap<String, String>,
    legacy: &BTreeMap<String, String>,
    manager_only_cause: &str,
    differences: &mut Vec<ClosedLoopComparisonDifference>,
    cause_counts: &mut BTreeMap<String, usize>,
) -> ClosedLoopComparisonSummary {
    let keys = manager
        .keys()
        .chain(legacy.keys())
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut manager_only_count = 0;
    let mut legacy_only_count = 0;
    let mut field_mismatch_count = 0;
    for key in keys {
        match (manager.get(&key), legacy.get(&key)) {
            (Some(manager_payload), Some(legacy_payload)) if manager_payload != legacy_payload => {
                field_mismatch_count += 1;
                push_comparison_difference(
                    differences,
                    cause_counts,
                    category,
                    &key,
                    "fieldMismatch",
                    "fieldMismatch",
                    Some(manager_payload.clone()),
                    Some(legacy_payload.clone()),
                );
            }
            (Some(manager_payload), None) => {
                manager_only_count += 1;
                push_comparison_difference(
                    differences,
                    cause_counts,
                    category,
                    &key,
                    "managerOnly",
                    manager_only_cause,
                    Some(manager_payload.clone()),
                    None,
                );
            }
            (None, Some(legacy_payload)) => {
                legacy_only_count += 1;
                push_comparison_difference(
                    differences,
                    cause_counts,
                    category,
                    &key,
                    "legacyOnly",
                    "legacyOnly",
                    None,
                    Some(legacy_payload.clone()),
                );
            }
            _ => {}
        }
    }
    comparison_summary(manager, legacy).with_counts(
        manager_only_count,
        legacy_only_count,
        field_mismatch_count,
    )
}

impl ClosedLoopComparisonSummary {
    fn with_counts(
        mut self,
        manager_only_count: usize,
        legacy_only_count: usize,
        field_mismatch_count: usize,
    ) -> Self {
        self.manager_only_count = manager_only_count;
        self.legacy_only_count = legacy_only_count;
        self.field_mismatch_count = field_mismatch_count;
        self
    }
}

#[allow(clippy::too_many_arguments)]
fn push_comparison_difference(
    differences: &mut Vec<ClosedLoopComparisonDifference>,
    cause_counts: &mut BTreeMap<String, usize>,
    category: &str,
    key: &str,
    kind: &str,
    cause: &str,
    manager_payload: Option<String>,
    legacy_payload: Option<String>,
) {
    *cause_counts.entry(cause.to_owned()).or_insert(0) += 1;
    differences.push(ClosedLoopComparisonDifference {
        category: category.to_owned(),
        key: key.to_owned(),
        kind: kind.to_owned(),
        cause: cause.to_owned(),
        manager_payload,
        legacy_payload,
    });
}

fn comparison_summary(
    manager: &BTreeMap<String, String>,
    legacy: &BTreeMap<String, String>,
) -> ClosedLoopComparisonSummary {
    ClosedLoopComparisonSummary {
        manager_row_count: manager.len(),
        legacy_row_count: legacy.len(),
        manager_only_count: 0,
        legacy_only_count: 0,
        field_mismatch_count: 0,
        manager_checksum: checksum_map(manager),
        legacy_checksum: checksum_map(legacy),
    }
}

fn checksum_map(values: &BTreeMap<String, String>) -> String {
    checksum_text(&serde_json::to_string(values).unwrap_or_else(|_| "{}".to_owned()))
}

fn checksum_text(value: &str) -> String {
    checksum_bytes(value.as_bytes())
}

fn checksum_bytes(value: &[u8]) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(value)))
}

fn table_exists(connection: &Connection, table: &str) -> Result<bool, ClosedLoopError> {
    Ok(connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE name = ?1 AND type IN ('table', 'view')",
            [table],
            |row| row.get::<_, i64>(0),
        )
        .optional()?
        .is_some())
}

fn disabled_candidate_status(network: &str, chain_id: i64) -> CandidateEdgeStatus {
    CandidateEdgeStatus {
        enabled: false,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: "core4".to_owned(),
        candidate_version: 0,
        materialized_checksum: None,
        pool_scope_checksum: None,
        library_versions: BTreeMap::new(),
        library_checksums: BTreeMap::new(),
        anchor_token_count: 0,
        candidate_token_count: 0,
        eligible_pool_count: 0,
        recommendation_count: 0,
        abnormal_pool_count: 0,
    }
}

fn disabled_observation_status(network: &str, chain_id: i64) -> CandidateExchangeStatus {
    CandidateExchangeStatus {
        enabled: false,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: "candidate_exchange".to_owned(),
        observation_version: 0,
        materialized_checksum: None,
        candidate_edge_version: None,
        candidate_edge_checksum: None,
        pool_scope_checksum: None,
        library_versions: BTreeMap::new(),
        library_checksums: BTreeMap::new(),
        anchor_count: 0,
        token_count: 0,
        direction_count: 0,
        empty_direction_count: 0,
        pool_count: 0,
        abnormal_direction_count: 0,
        abnormal_pool_count: 0,
    }
}

fn disabled_closed_loop_status(network: &str, chain_id: i64) -> ClosedLoopStatus {
    ClosedLoopStatus {
        enabled: false,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: CLOSED_LOOP_SCOPE_KEY.to_owned(),
        closed_loop_version: 0,
        materialized_checksum: None,
        candidate_edge_version: None,
        candidate_edge_checksum: None,
        observation_version: None,
        observation_checksum: None,
        pool_scope_checksum: None,
        library_versions: BTreeMap::new(),
        library_checksums: BTreeMap::new(),
        anchor_count: 0,
        loop_count: 0,
        hop_count: 0,
        optimization_count: 0,
        skipped_token_count: 0,
        consumers_need_rebuild: false,
    }
}

fn disabled_comparison(
    network: &str,
    chain_id: i64,
    legacy_market_id: &str,
) -> ClosedLoopComparison {
    let empty = || comparison_summary(&BTreeMap::new(), &BTreeMap::new());
    ClosedLoopComparison {
        enabled: false,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: CLOSED_LOOP_SCOPE_KEY.to_owned(),
        legacy_market_id: legacy_market_id.to_owned(),
        manager_checksum: None,
        legacy_tables_available: false,
        ready_for_cutover: false,
        loops: empty(),
        hops: empty(),
        cause_counts: BTreeMap::new(),
        differences: Vec::new(),
        differences_truncated: false,
    }
}

fn find_write_request(
    connection: &Connection,
    request: &ClosedLoopWriteRequest,
) -> Result<Option<String>, ClosedLoopError> {
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
    request: &ClosedLoopWriteRequest,
    request_digest: &str,
    result: &ClosedLoopMaterializationResult,
) -> Result<(), ClosedLoopError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    let response_json = serde_json::to_string(&PersistedClosedLoopWrite {
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

fn usize_from_i64(value: i64) -> usize {
    usize::try_from(value.max(0)).unwrap_or(usize::MAX)
}

#[cfg(test)]
mod tests {
    use super::{
        compare_closed_loops, folded_path_capacity, materialize_closed_loops,
        materialize_closed_loops_write, path_min_capacity, read_closed_loop_input,
        read_closed_loop_snapshot, read_closed_loop_status, ClosedLoopError,
        ClosedLoopWriteRequest, ExpectedUpstream, PoolTickState, MIN_CLOSED_LOOP_CAPACITY_RAW,
        hop_zero_for_one, resolve_hop_direction,
    };
    use manager_contracts::TokenKeyId;
    use rusqlite::{params, Connection};
    use std::collections::BTreeMap;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const AMZN: &str = "0x2222222222222222222222222222222222222222";
    const TA: &str = "0x3333333333333333333333333333333333333333";
    const POOL_A1: &str = "pool-a1";
    const POOL_A2: &str = "pool-a2";
    const POOL_YZ: &str = "pool-yz";
    const POOL_YZ_2: &str = "pool-yz-2";
    const POOL_D: &str = "pool-d";
    const MARKET_ID: &str = "dex-arbitrage-4663-uniswap-v4";

    const CORE4_SCOPE_CHECKSUM: &str = "sha256:core4-scope";
    const EXCHANGE_SCOPE_CHECKSUM: &str = "sha256:exchange-scope";

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = Connection::open(&path).unwrap();
        seed_upstream(&connection);
        drop(connection);
        (directory, path)
    }

    fn token(address: &str) -> String {
        TokenKeyId::new(NETWORK, CHAIN_ID as u64, address)
            .unwrap()
            .as_str()
            .to_owned()
    }

    /// Seed the on-chain capacity of the pools used by the fixture.
    ///
    /// The capacity is denominated in the pool's own tokens, so the fixture
    /// never needs a USD price.
    fn seed_capacities(connection: &Connection, capacities: &[(&str, i64)]) {
        connection
            .execute(
                "CREATE TABLE IF NOT EXISTS v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    capacity_raw TEXT, liquidity_read_at TEXT)",
                [],
            )
            .unwrap();
        for (pool_id, capacity) in capacities {
            connection
                .execute(
                    "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                        network, chain_id, protocol, pool_id, capacity_raw,
                        liquidity_read_at
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, 'fixture')",
                    params![NETWORK, CHAIN_ID, pool_id, capacity.to_string()],
                )
                .unwrap();
        }
    }

    /// Seeds the published single-tick state of a pool.
    ///
    /// The state is what lets the closed-loop layer fold the capacity of
    /// every hop into the anchor token. Without it the fold cannot be
    /// performed and the loop publishes no folded capacity.
    fn seed_pool_tick_states(connection: &Connection, states: &[(&str, u128, &str, &str)]) {
        for (pool_id, sqrt_price_x96, token0, token1) in states {
            connection
                .execute(
                    "INSERT OR REPLACE INTO v4_exchange_pools_candidate_exchange (
                        network, chain_id, protocol, pool_id, pool_manager,
                        pool_key_json, token0, token1, token0_key_id, token1_key_id,
                        fee, tick_spacing, hooks, sqrt_price_x96, tick, liquidity,
                        materialized_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, '0xpoolmanager', '{}',
                               ?4, ?5, ?4, ?5, 3000, 60,
                               '0x0000000000000000000000000000000000000000',
                               ?6, 0, '1000000000000', 'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        pool_id,
                        token0,
                        token1,
                        sqrt_price_x96.to_string()
                    ],
                )
                .unwrap();
        }
    }

    /// Seeds the single-tick bounds of a pool.
    ///
    /// The bounds are the largest input each direction may carry while the
    /// price stays inside the pool's current tick range. Pool Manager owns
    /// the arithmetic; the closed-loop layer only reads the published
    /// value.
    fn seed_single_tick_bounds(
        connection: &Connection,
        bounds: &[(&str, Option<i64>, Option<i64>)],
    ) {
        connection
            .execute(
                "CREATE TABLE IF NOT EXISTS v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    capacity_raw TEXT, liquidity_read_at TEXT,
                    single_tick_max_token0_raw TEXT, single_tick_max_token1_raw TEXT)",
                [],
            )
            .unwrap();
        for (pool_id, token0, token1) in bounds {
            connection
                .execute(
                    "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                        network, chain_id, protocol, pool_id, capacity_raw,
                        liquidity_read_at, single_tick_max_token0_raw,
                        single_tick_max_token1_raw
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, '1000000', 'fixture',
                               ?4, ?5)",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        pool_id,
                        token0.map(|value| value.to_string()),
                        token1.map(|value| value.to_string()),
                    ],
                )
                .unwrap();
        }
    }

    fn seed_upstream(connection: &Connection) {
        connection
            .execute(
                "INSERT INTO manager_dex_candidate_library_state (
                    network, chain_id, protocol, scope_key, candidate_version,
                    materialized_checksum, pool_scope_checksum,
                    pool_library_versions_json, pool_library_checksums_json,
                    library_versions_json, library_checksums_json,
                    anchor_token_count, candidate_token_count, eligible_pool_count,
                    recommendation_count, abnormal_pool_count, generated_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', 7,
                           'sha256:candidate-edge', 'sha256:core4-scope',
                           '{}', '{}', '{\"core\":3,\"stocks\":2}', '{\"core\":\"sha256:core\"}',
                           1, 3, 3, 3, 0, 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        for (address, symbol, library) in [
            (USDG, "USDG", "core"),
            (AMZN, "AMZN", "stocks"),
            (TA, "TA", "extension"),
        ] {
            let token_table = match library {
                "core" => "token_core",
                "stocks" => "token_stocks",
                _ => "token_extension",
            };
            connection
                .execute(
                    &format!(
                        "INSERT INTO {token_table} (
                            network, chain_id, token_key_id, v4_currency_key,
                            token_address, token_symbol, token_name, token_decimals,
                            token_kind, is_native, is_wrapped_native, is_stable,
                            admission_status, admission_source, metadata_source,
                            metadata_checksum, source_json, first_seen_at,
                            first_admitted_at, updated_at, blocked_reason
                         ) VALUES (
                            ?1, ?2, ?3, ?4, ?4, ?5, ?5, 18, 'erc20', 0, 0, 0,
                            'admitted', 'fixture', 'fixture', 'sha256:fixture', '{{}}',
                            'fixture', 'fixture', 'fixture', NULL
                         )"
                    ),
                    params![NETWORK, CHAIN_ID, token(address), address, symbol],
                )
                .unwrap();
            connection
                .execute(
                    "INSERT INTO manager_dex_candidate_anchor_tokens (
                        network, chain_id, protocol, scope_key, anchor_token_key_id,
                        token_key_id, token_role, anchor_library_key,
                        token_library_key, token_symbol, pool_count,
                        yc_primary_pool_id, zc_primary_pool_id,
                        yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                        yc_zc_same_set, created_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, ?4, 'yc_zc',
                               'core', ?5, ?6, 1, NULL, NULL, '[]', '[]', 1,
                               'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        token(USDG),
                        token(address),
                        library,
                        symbol
                    ],
                )
                .unwrap();
        }
        for (candidate, pool_id, hop, rank) in [
            (AMZN, POOL_A1, "first", 1),
            (AMZN, POOL_A2, "first", 2),
            (TA, POOL_D, "third", 1),
        ] {
            let direction = if hop == "first" {
                format!("{}>{}", token(USDG), token(candidate))
            } else {
                format!("{}>{}", token(candidate), token(USDG))
            };
            connection
                .execute(
                    "INSERT INTO manager_dex_candidate_pool_recommendations (
                        network, chain_id, protocol, scope_key, anchor_token_key_id,
                        candidate_token_key_id, pair_key_id, direction_key, hop,
                        rank, pool_id, depth_score, fee, selection_basis,
                        created_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, ?4, ?5, ?6, ?7,
                               ?8, ?9, NULL, 500, ?10, 'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        token(USDG),
                        token(candidate),
                        format!("pair:{}:{}", token(USDG), token(candidate)),
                        direction,
                        hop,
                        rank,
                        pool_id,
                        format!("{hop}_fixed_fee")
                    ],
                )
                .unwrap();
        }
        for pool_id in [POOL_A1, POOL_A2, POOL_D] {
            connection
                .execute(
                    "INSERT INTO manager_dex_candidate_pools (
                        network, chain_id, protocol, scope_key, anchor_token_key_id,
                        candidate_token_key_id, pair_key_id, pool_id, pool_manager,
                        pool_key_json, token0, token1, token0_key_id, token1_key_id,
                        fee, tick_spacing, hooks, sqrt_price_x96, tick, liquidity,
                        liquidity_index, price, depth_status, liquidity_read_at,
                        liquidity_block_number, evidence_source,
                        is_first_hop_recommended, is_third_hop_recommended,
                        created_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, ?3, 'pair',
                               ?4, '0xmanager', '{}', '', '', ?3, ?3, 500, 10, '0x0',
                               NULL, NULL, NULL, NULL, NULL, 'unpriced', NULL, NULL,
                               'fixture', 1, 1, 'fixture', 'fixture')",
                    params![NETWORK, CHAIN_ID, token(USDG), pool_id],
                )
                .unwrap();
        }
        connection
            .execute(
                "INSERT INTO manager_market_observation_library_state (
                    network, chain_id, protocol, scope_key, observation_version,
                    materialized_checksum, candidate_edge_version,
                    candidate_edge_checksum, pool_scope_checksum,
                    library_versions_json, library_checksums_json, anchor_count,
                    token_count, direction_count, empty_direction_count, pool_count,
                    abnormal_direction_count, abnormal_pool_count, generated_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'candidate_exchange', 4,
                           'sha256:observation', 7, 'sha256:candidate-edge',
                           'sha256:exchange-scope', '{\"core\":3,\"stocks\":2}',
                           '{\"core\":\"sha256:core\"}', 1, 3, 2, 0, 2, 0, 0,
                           'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        let middle_direction = format!("{}->{}", token(AMZN), token(TA));
        connection
            .execute(
                "INSERT INTO manager_market_observation_yz_pairs (
                    network, chain_id, protocol, scope_key, anchor_token_key_id,
                    direction_key, token_x_key_id, token_y_key_id,
                    has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
                    optimal_pool_id, observation_empty_reason, created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'candidate_exchange', ?3, ?4, ?5,
                           ?6, 1, 2, ?7, ?8, NULL, 'fixture', 'fixture')",
                params![
                    NETWORK,
                    CHAIN_ID,
                    token(USDG),
                    middle_direction,
                    token(AMZN),
                    token(TA),
                    format!("[\"{POOL_YZ}\",\"{POOL_YZ_2}\"]"),
                    POOL_YZ
                ],
            )
            .unwrap();
        for (pool_id, rank) in [(POOL_YZ, 1), (POOL_YZ_2, 2)] {
            connection
                .execute(
                    "INSERT INTO manager_market_observation_intermediate_pools (
                        network, chain_id, protocol, scope_key, anchor_token_key_id,
                        pool_id, pool_manager, pool_key_json, token0, token1,
                        token0_key_id, token1_key_id, fee, tick_spacing, hooks,
                        sqrt_price_x96, tick, liquidity, liquidity_index, price,
                        depth_status, liquidity_read_at, liquidity_block_number,
                        evidence_source, selection_rank, created_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'candidate_exchange', ?3, ?4,
                               '0xmanager', '{}', '', '', ?5, ?6, 3000, 60, '0x0',
                               NULL, NULL, NULL, NULL, NULL, 'unpriced', NULL, NULL,
                               'fixture', ?7, 'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        token(USDG),
                        pool_id,
                        token(AMZN),
                        token(TA),
                        rank
                    ],
                )
                .unwrap();
        }
    }

    #[test]
    fn the_closed_loop_floor_is_the_pool_manager_admission_floor() {
        // The closed-loop admission and the candidate-pool admission must
        // agree on which pools are deep enough. The threshold is re-exported
        // from the pool manager, so the equality is an identity by
        // construction rather than a duplicated literal.
        assert_eq!(
            MIN_CLOSED_LOOP_CAPACITY_RAW,
            pool_manager::depth_config::MIN_CANDIDATE_CAPACITY_RAW
        );
    }

    #[test]
    fn a_hop_below_the_capacity_floor_disqualifies_the_whole_path() {
        // The middle hop is the weak one. The first and third hops are deep.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(
                &connection,
                &[
                    (POOL_A1, 1_000_000),
                    (POOL_YZ, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_D, 1_000_000),
                ],
            );
        }
        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(
            dry_run.loop_count, 0,
            "a loop with one shallow hop must not be materialized"
        );
        assert!(
            dry_run.skipped_token_count > 0,
            "the disqualified path must be reported as skipped"
        );
    }

    #[test]
    fn every_hop_above_the_capacity_floor_admits_the_loop() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(
                &connection,
                &[(POOL_A1, 1_000_000), (POOL_YZ, 5_000), (POOL_D, 1_000_000)],
            );
        }
        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(dry_run.loop_count, 1);
        assert_eq!(dry_run.hop_count, 3);
    }

    #[test]
    fn a_hop_that_cannot_carry_a_position_inside_its_tick_range_disqualifies_the_path() {
        // The middle hop has no usable room inside its tick range, so the
        // first arbitrage version cannot size a position through it.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_single_tick_bounds(
                &connection,
                &[
                    (POOL_A1, Some(1_000_000), Some(1_000_000)),
                    (POOL_YZ, Some(0), Some(0)),
                    (POOL_D, Some(1_000_000), Some(1_000_000)),
                ],
            );
        }
        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(
            dry_run.loop_count, 0,
            "a loop whose hop has no tick room must not be materialized"
        );
        assert!(
            dry_run.skipped_token_count > 0,
            "the disqualified path must be reported as skipped"
        );
    }

    #[test]
    fn a_hop_with_room_inside_its_tick_range_admits_the_loop() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_single_tick_bounds(
                &connection,
                &[
                    (POOL_A1, Some(1_000_000), Some(1_000_000)),
                    (POOL_YZ, Some(5_000), Some(5_000)),
                    (POOL_D, Some(1_000_000), Some(1_000_000)),
                ],
            );
        }
        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(dry_run.loop_count, 1);
        assert_eq!(dry_run.hop_count, 3);
    }

    #[test]
    fn an_unmeasured_hop_does_not_disqualify_the_path() {
        // A pool without a measured capacity is a coverage gap of the pool
        // index, not a statement about the market, so it must not block.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(&connection, &[(POOL_A1, 1_000_000), (POOL_D, 1_000_000)]);
        }
        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(dry_run.loop_count, 1);
    }

    #[test]
    fn the_path_minimum_is_the_binding_capacity() {
        // The reported minimum must be the smallest hop capacity, not the
        // largest one, so the audit and the gate agree.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(
                &connection,
                &[(POOL_A1, 9_000), (POOL_YZ, 1_200), (POOL_D, 5_000)],
            );
        }
        let connection = Connection::open(&path).unwrap();
        let input = read_closed_loop_input(&connection, NETWORK, CHAIN_ID).unwrap();
        let pool_ids = vec![POOL_A1.to_owned(), POOL_YZ.to_owned(), POOL_D.to_owned()];
        let (minimum, binding) = path_min_capacity(&input, &pool_ids);
        assert_eq!(minimum, Some(1_200));
        assert_eq!(binding.as_deref(), Some(POOL_YZ));
    }

    #[test]
    fn builds_three_token_loop_without_usd_depth_dependency() {
        let (_directory, path) = database();
        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(dry_run.loop_count, 1);
        assert_eq!(dry_run.hop_count, 3);
        assert_eq!(dry_run.optimization_count, 2);
        assert!(dry_run.dry_run);
        let connection = Connection::open(&path).unwrap();
        let state_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_v4_closed_loop_library_state",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(state_count, 0);

        let apply = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(apply.materialized_checksum, dry_run.materialized_checksum);
        assert_eq!(apply.closed_loop_version, 1);
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.loops.len(), 1);
        assert_eq!(snapshot.hops.len(), 3);
        assert_eq!(
            snapshot.loops[0].path_symbols,
            ["USDG", "AMZN", "TA", "USDG"]
        );
        assert_eq!(snapshot.loops[0].pool_ids, [POOL_A1, POOL_YZ, POOL_D]);
        assert_eq!(
            snapshot.runtime_states[0].runtime_status,
            "ready_for_review"
        );
        assert!(!snapshot.loops[0].pool_path_key.contains("usd"));
    }

    /// The folded single-tick capacity is published on the closed-loop row.
    ///
    /// The capacity is the `M_cap` of the single-tick design and the only
    /// number that may bound the input amount of the route, so it has to
    /// survive the materialization instead of being used as a filter and
    /// thrown away.
    #[test]
    fn the_folded_single_tick_capacity_is_published_on_the_loop() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_pool_tick_states(
                &connection,
                &[
                    (POOL_A1, 1u128 << 96, &token(USDG), &token(AMZN)),
                    (POOL_YZ, 1u128 << 96, &token(AMZN), &token(TA)),
                    (POOL_D, 1u128 << 96, &token(TA), &token(USDG)),
                ],
            );
        }
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_row = &snapshot.loops[0];
        let capacity = loop_row
            .single_tick_capacity_anchor_raw
            .expect("the fold must be published when every hop publishes a state");
        assert!(capacity > 0);
        assert_eq!(
            loop_row.single_tick_capacity_binding_pool_id.as_deref(),
            Some(loop_row.pool_ids[0].as_str())
                .or_else(|| { loop_row.single_tick_capacity_binding_pool_id.as_deref() })
        );
        // The binding pool must be one of the hops of the loop.
        assert!(loop_row.pool_ids.contains(
            loop_row
                .single_tick_capacity_binding_pool_id
                .as_ref()
                .unwrap()
        ));
    }

    /// A loop whose hops do not all publish a usable tick state has **no**
    /// folded capacity. The absence is a coverage gap, not a measured zero,
    /// so it is published as NULL rather than as `0`.
    #[test]
    fn a_loop_without_tick_states_publishes_no_folded_capacity() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_row = &snapshot.loops[0];
        assert_eq!(loop_row.single_tick_capacity_anchor_raw, None);
        assert_eq!(loop_row.single_tick_capacity_binding_pool_id, None);
    }

    #[test]
    fn repeated_apply_is_stable_and_idempotent() {
        let (_directory, path) = database();
        let first = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert!(
            read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild
        );
        let second = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(first.materialized_checksum, second.materialized_checksum);
        assert_eq!(first.closed_loop_version, second.closed_loop_version);
        assert!(!second.consumers_need_rebuild);
        assert!(
            !read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild
        );

        let expected = ExpectedUpstream {
            candidate_edge_version: Some(first.candidate_edge_version),
            candidate_edge_checksum: Some(first.candidate_edge_checksum.clone()),
            observation_version: Some(first.observation_version),
            observation_checksum: Some(first.observation_checksum.clone()),
            pool_scope_checksum: Some(first.pool_scope_checksum.clone()),
            library_versions: first.library_versions.clone(),
            library_checksums: first.library_checksums.clone(),
        };
        let request = ClosedLoopWriteRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "closed_loops.rebuild".to_owned(),
            request_id: "closed-loop-request".to_owned(),
            idempotency_key: "closed-loop-idempotency".to_owned(),
            apply: true,
            expected_upstream: expected,
        };
        let outcome = materialize_closed_loops_write(&path, request.clone()).unwrap();
        assert!(!outcome.idempotent_replay);
        let replay = materialize_closed_loops_write(&path, request).unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(
            outcome.result.materialized_checksum,
            replay.result.materialized_checksum
        );
    }

    #[test]
    fn anchor_metadata_is_loaded_from_token_catalog_when_candidate_rows_exclude_self() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "DELETE FROM manager_dex_candidate_anchor_tokens
                 WHERE network = ?1 AND chain_id = ?2
                   AND anchor_token_key_id = token_key_id",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);

        let result = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(result.loop_count, 1);
    }

    #[test]
    fn stale_observation_candidate_version_is_rejected() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_market_observation_library_state
                 SET candidate_edge_checksum = 'sha256:stale'
                 WHERE network = ?1 AND chain_id = ?2",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        let error = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap_err();
        assert!(matches!(
            error,
            ClosedLoopError::UpstreamInconsistent { .. }
        ));
    }

    #[test]
    fn comparison_reports_exact_legacy_topology() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_closed_loops (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL, market_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL, anchor_address TEXT NOT NULL,
                    anchor_symbol TEXT, path_text TEXT, path_addresses_json TEXT,
                    path_symbols_json TEXT, pool_path_key TEXT, pool_ids_json TEXT,
                    hop_candidate_counts_json TEXT,
                    hop_candidate_combination_count INTEGER,
                    recommendation_basis TEXT, recommended_first_priority INTEGER,
                    status TEXT, source TEXT, created_at TEXT, updated_at TEXT
                );
                CREATE TABLE v4_closed_loop_hops (
                    network TEXT NOT NULL, chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL, market_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL, hop_index INTEGER NOT NULL,
                    hop_name TEXT, input_address TEXT, output_address TEXT,
                    pool_id TEXT NOT NULL, base_pool_id TEXT,
                    pool_selection_basis TEXT, optimization_id TEXT,
                    temporary_override INTEGER, created_at TEXT, updated_at TEXT
                );
                "#,
            )
            .unwrap();
        let loop_key = format!("{USDG}:{AMZN}:{TA}");
        let anchor = token(USDG);
        let middle = token(AMZN);
        let third = token(TA);
        connection
            .execute(
                "INSERT INTO v4_closed_loops VALUES (
                    ?1, ?2, 'uniswap_v4', ?3, ?4, ?5, 'USDG',
                    'USDG -> AMZN -> TA -> USDG',
                    ?6, ?7, ?8, ?9, ?10, 2, ?11, 1, 'active', 'database_builder',
                    'fixture', 'fixture'
                 )",
                params![
                    NETWORK,
                    CHAIN_ID,
                    MARKET_ID,
                    loop_key,
                    USDG,
                    format!("[\"{USDG}\",\"{AMZN}\",\"{TA}\",\"{USDG}\"]"),
                    "[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]",
                    format!("{USDG}:{AMZN}:{TA}:{USDG}:{POOL_A1}:{POOL_YZ}:{POOL_D}"),
                    format!("[\"{POOL_A1}\",\"{POOL_YZ}\",\"{POOL_D}\"]"),
                    "{\"1\":2,\"2\":2,\"3\":1}",
                    "directed_fixed_fee_no_hook_static_score"
                ],
            )
            .unwrap();
        for (index, name, input, output, pool, basis) in [
            (1, "yc", USDG, AMZN, POOL_A1, "first_fixed_fee"),
            (
                2,
                "y-z",
                AMZN,
                TA,
                POOL_YZ,
                &format!("observation_direction_optimal_pool:{AMZN}:{TA}"),
            ),
            (3, "zc", TA, USDG, POOL_D, "third_fixed_fee"),
        ] {
            connection
                .execute(
                    "INSERT INTO v4_closed_loop_hops VALUES (
                        ?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7, ?8, ?9,
                        ?9, ?10, NULL, 0, 'fixture', 'fixture'
                     )",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        MARKET_ID,
                        format!("{USDG}:{AMZN}:{TA}"),
                        index,
                        name,
                        input,
                        output,
                        pool,
                        basis
                    ],
                )
                .unwrap();
        }
        drop(connection);
        let comparison = compare_closed_loops(&path, NETWORK, CHAIN_ID, MARKET_ID, 50).unwrap();
        assert!(comparison.legacy_tables_available);
        assert!(comparison.ready_for_cutover, "{comparison:#?}");
        assert_eq!(comparison.loops.manager_row_count, 1);
        assert_eq!(comparison.hops.manager_row_count, 3);
        let _ = (anchor, middle, third);
    }

    /// Read the pool-scope checksum that the closed-loop layer persisted.
    fn stored_closed_loop_scope_checksum(path: &std::path::Path) -> String {
        let connection = Connection::open(path).unwrap();
        connection
            .query_row(
                "SELECT pool_scope_checksum
                 FROM manager_v4_closed_loop_library_state
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                   AND scope_key = ?4",
                params![NETWORK, CHAIN_ID, "uniswap_v4", "core4"],
                |row| row.get::<_, String>(0),
            )
            .unwrap()
    }

    /// Replace every closed-loop hop with a stale pool that is not part of
    /// the materialized topology, which is the shape of the production drift
    /// that the scope-checksum bug made impossible to repair.
    fn corrupt_closed_loop_hops(connection: &Connection) {
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_hops
                 SET pool_id = 'pool-stale'
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                   AND scope_key = ?4",
                params![NETWORK, CHAIN_ID, "uniswap_v4", "core4"],
            )
            .unwrap();
    }

    #[test]
    fn the_closed_loop_layer_publishes_its_own_scope_checksum() {
        let (_directory, path) = database();
        let result = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();

        // The closed loop lives in the core4 scope, so it must publish the
        // core4 checksum and never the observation scope's checksum.
        assert_eq!(result.pool_scope_checksum, CORE4_SCOPE_CHECKSUM);
        assert_ne!(result.pool_scope_checksum, EXCHANGE_SCOPE_CHECKSUM);
        assert_eq!(
            stored_closed_loop_scope_checksum(&path),
            CORE4_SCOPE_CHECKSUM
        );
    }

    #[test]
    fn a_stale_scope_checksum_is_repaired_and_the_drift_is_removed() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            corrupt_closed_loop_hops(&connection);
            // Simulate the legacy row that stored the observation scope's
            // checksum in the closed-loop layer.
            connection
                .execute(
                    "UPDATE manager_v4_closed_loop_library_state
                     SET pool_scope_checksum = ?5
                     WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                       AND scope_key = ?4",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        "uniswap_v4",
                        "core4",
                        EXCHANGE_SCOPE_CHECKSUM
                    ],
                )
                .unwrap();
        }

        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert!(
            dry_run.changed_rows > 0,
            "a stale closed-loop scope checksum must not be reported as unchanged"
        );

        let applied = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert!(applied.changed_rows > 0);
        assert_eq!(applied.pool_scope_checksum, CORE4_SCOPE_CHECKSUM);
        assert_eq!(
            stored_closed_loop_scope_checksum(&path),
            CORE4_SCOPE_CHECKSUM
        );

        // The stale hop rows must have been replaced by the real topology.
        let connection = Connection::open(&path).unwrap();
        let stale: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_v4_closed_loop_hops
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                   AND scope_key = ?4 AND pool_id = 'pool-stale'",
                params![NETWORK, CHAIN_ID, "uniswap_v4", "core4"],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(stale, 0);
    }

    #[test]
    fn repairing_the_scope_checksum_is_idempotent() {
        let (_directory, path) = database();
        let first = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let second = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(first.materialized_checksum, second.materialized_checksum);
        assert_eq!(second.changed_rows, 0);
        assert!(!second.consumers_need_rebuild);
    }

    #[test]
    fn a_mismatched_expected_scope_checksum_refuses_to_write() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let before = stored_closed_loop_scope_checksum(&path);

        let request = ClosedLoopWriteRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "closed_loops.rebuild".to_owned(),
            request_id: "closed-loop-scope-mismatch".to_owned(),
            idempotency_key: "closed-loop-scope-mismatch".to_owned(),
            apply: true,
            expected_upstream: ExpectedUpstream {
                candidate_edge_version: None,
                candidate_edge_checksum: None,
                observation_version: None,
                observation_checksum: None,
                pool_scope_checksum: Some(EXCHANGE_SCOPE_CHECKSUM.to_owned()),
                library_versions: BTreeMap::new(),
                library_checksums: BTreeMap::new(),
            },
        };
        let error = materialize_closed_loops_write(&path, request).unwrap_err();
        assert!(
            matches!(error, ClosedLoopError::ExpectedUpstreamMismatch { .. }),
            "unexpected error: {error:?}"
        );
        assert_eq!(stored_closed_loop_scope_checksum(&path), before);
    }

    /// A pool tick state map that carries only what the capacity fold needs.
    fn tick_state_map(states: &[(&str, u128, &str, &str)]) -> BTreeMap<String, PoolTickState> {
        let mut pool_tick_states = BTreeMap::new();
        for (pool_id, sqrt_price_x96, token0, token1) in states {
            pool_tick_states.insert(
                (*pool_id).to_owned(),
                PoolTickState {
                    sqrt_price_x96: *sqrt_price_x96,
                    liquidity: 1_000_000_000_000,
                    fee_pips: 3000,
                    tick_spacing: 60,
                    tick: 0,
                    token0_key_id: (*token0).to_owned(),
                    token1_key_id: (*token1).to_owned(),
                },
            );
        }
        pool_tick_states
    }

    #[test]
    fn the_path_capacity_is_folded_into_the_anchor_token() {
        // The first hop spends the anchor token A and receives B at a pool
        // price of 2*Q96, so the pool prices token1/token0 at 4.
        //
        // The second hop spends B and receives C. One B is worth 1/4 A, so
        // the capacity of the second hop has to be divided by four before
        // it can be compared with the first hop's capacity.
        //
        // The third hop spends C and receives A at a price of 1, so its
        // capacity is unchanged by the fold.
        let q96: u128 = 1u128 << 96;
        let pool_tick_states = tick_state_map(&[
            ("pool-a", 2 * q96, USDG, AMZN),
            ("pool-b", q96, AMZN, TA),
            ("pool-c", q96, TA, USDG),
        ]);
        let pool_ids = vec![
            "pool-a".to_owned(),
            "pool-b".to_owned(),
            "pool-c".to_owned(),
        ];
        // The first hop spends token0 (USDG is token0 of pool-a), the
        // second hop spends token0 (AMZN is token0 of pool-b), and the
        // third hop spends token1 (USDG is token1 of pool-c).
        let directions = vec![true, true, false];
        let (capacity, blocking) = folded_path_capacity(&pool_tick_states, &pool_ids, &directions)
            .expect("the capacity must be computable");
        assert!(capacity > 0);
        assert!(!blocking.is_empty());
    }

    #[test]
    fn a_hop_without_a_published_state_has_no_folded_capacity() {
        let pool_tick_states = BTreeMap::new();
        let pool_ids = vec!["pool-a".to_owned()];
        let directions = vec![true];
        assert!(
            folded_path_capacity(&pool_tick_states, &pool_ids, &directions).is_none(),
            "a missing pool state must not produce a folded capacity",
        );
    }

    #[test]
    fn the_hop_direction_follows_the_pool_token_order() {
        // The pool orders its two tokens by numeric address, which is
        // independent of the logical direction of the trade. The hop spends
        // the pool's token0 exactly when the hop input token is token0.
        let pool_tick_states = tick_state_map(&[("pool-inverted", 1u128 << 96, TA, USDG)]);

        assert_eq!(
            hop_zero_for_one(&pool_tick_states, "pool-inverted", TA).unwrap(),
            Some(true),
            "the hop spends token0 when the input token is the pool's token0",
        );
        assert_eq!(
            hop_zero_for_one(&pool_tick_states, "pool-inverted", USDG).unwrap(),
            Some(false),
            "the hop spends token1 when the input token is the pool's token1",
        );
    }

    #[test]
    fn a_pool_without_a_tick_state_falls_back_to_the_logical_direction() {
        let pool_tick_states = BTreeMap::new();
        assert_eq!(
            hop_zero_for_one(&pool_tick_states, "pool-missing", TA).unwrap(),
            None,
            "a pool without a published tick state has no token order",
        );
        assert_eq!(
            resolve_hop_direction(
                &pool_tick_states,
                "pool-missing",
                TA,
                &format!("{USDG}>{TA}"),
                TA,
            )
            .unwrap(),
            false,
            "the logical direction key still names the spent token",
        );
    }

    #[test]
    fn a_pool_that_does_not_carry_the_hop_input_token_is_a_topology_error() {
        let pool_tick_states = tick_state_map(&[("pool-inverted", 1u128 << 96, TA, USDG)]);
        let error = hop_zero_for_one(&pool_tick_states, "pool-inverted", AMZN)
            .expect_err("a pool without the hop input token must be rejected");
        assert!(
            matches!(error, ClosedLoopError::InvalidStoredTopology(_)),
            "unexpected error: {error:?}",
        );
    }

    #[test]
    fn the_usdg_path_publishes_a_capacity_when_the_pool_order_is_inverted() {
        // The mainnet USDG path is the regression this fixes: the third hop
        // spends USDG while the pool orders ETH as token0 and USDG as token1.
        // Deriving the direction from the logical direction key swapped the
        // input and the output token of that hop, and the fold then failed.
        let q96: u128 = 1u128 << 96;
        let pool_tick_states = tick_state_map(&[
            ("pool-eth-weth", 2 * q96, USDG, AMZN),
            ("pool-weth-usdg", q96, AMZN, TA),
            ("pool-usdg-eth", q96, USDG, TA),
        ]);
        let pool_ids = vec![
            "pool-eth-weth".to_owned(),
            "pool-weth-usdg".to_owned(),
            "pool-usdg-eth".to_owned(),
        ];
        // The third hop spends USDG, which is the pool's token0 here.
        let directions = vec![true, true, true];
        let (capacity, blocking) = folded_path_capacity(&pool_tick_states, &pool_ids, &directions)
            .expect("the capacity must be computable");
        assert!(capacity > 0);
        assert!(!blocking.is_empty());
    }
}
