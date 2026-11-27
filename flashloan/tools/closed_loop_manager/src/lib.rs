use candidate_edge_pool_manager::CandidateEdgeStatus;
use candidate_exchange_pool_manager::CandidateExchangeStatus;
use directional_pool_manager::{tier_of_rank, DirectionalPoolTier};
use manager_contracts::{DirectionalPool, PoolDirection, RouteKeyId, TokenKeyId, V4Pool};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;
use std::path::{Path, PathBuf};

pub mod usdg_valuation;

const NETWORK_PROTOCOL: &str = "uniswap_v4";
/// The live market snapshot of every pool.
///
/// The liquidity worker refreshes this table, so its price columns are the
/// freshest reading of the market the store holds.  The base pool tables
/// carry a copy of the same columns, but that copy is only as fresh as the
/// last materialization that wrote it.
const VALUATION_SNAPSHOT_TABLE: &str = "v4_exchange_pool_liquidity_snapshots";

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
/// The scope that supplies the **first and third** hop of a closed loop.
///
/// The candidate edge layer publishes those pools under the `core4` scope,
/// so the direction rows the loop walks for them are the `core4` ones.
const HOP_SIDE_SCOPE_KEY: &str = "core4";
/// The scope that supplies the **middle** hop of a closed loop.
///
/// The market observation layer publishes the Y->Z pools under the
/// `candidate_exchange` scope, so the middle hop walks that projection.
const HOP_MIDDLE_SCOPE_KEY: &str = "candidate_exchange";
/// The environment variable that decides which order names the **primary**
/// pool of a hop.
///
/// The direction layer ranks every pool of a `(pair, direction)` group by the
/// price quality of that direction, and the closed loop walks the group in
/// that order: the primary of a hop is the pool the direction layer ranked
/// first.  The variable is the **kill switch** of that switch-over.
///
/// Absent or empty means **on** 鈥?the direction layer is the authority 鈥?and
/// only an explicit negative value (`0` / `false` / `off` / `no`) keeps the
/// legacy order of the candidate tier.  A typo therefore can never silently
/// disable the switch-over.
pub const DIRECTIONAL_PRIMARY_ORDER_ENV: &str = "FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY";
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

/// Read the primary-order switch from a raw environment value.
///
/// An absent or empty value means **on**: the direction layer is the
/// authority.  Only an explicit negative value turns the switch off, so a
/// malformed value can never silently keep the legacy order alive.
fn directional_primary_order_from_value(value: Option<&str>) -> bool {
    let Some(value) = value.map(str::trim).filter(|value| !value.is_empty()) else {
        return true;
    };
    !matches!(
        value.to_ascii_lowercase().as_str(),
        "0" | "false" | "off" | "no"
    )
}

/// Whether the direction layer names the primary pool of a hop.
///
/// The value is read **once per materialization**, so one run never mixes two
/// orders, and the published rows always agree with the order the run used.
fn directional_primary_order_enabled() -> bool {
    let value = std::env::var(DIRECTIONAL_PRIMARY_ORDER_ENV).ok();
    directional_primary_order_from_value(value.as_deref())
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
    /// The version of the direction layer the loops were built against.
    pub directional_pool_version: i64,
    /// The checksum of the direction layer the loops were built against.
    pub directional_pool_checksum: String,
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
    /// The version of the direction layer the loops were built against.
    pub directional_pool_version: Option<i64>,
    /// The checksum of the direction layer the loops were built against.
    pub directional_pool_checksum: Option<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub anchor_count: usize,
    pub loop_count: usize,
    pub hop_count: usize,
    pub optimization_count: usize,
    pub skipped_token_count: usize,
    /// Whether the consumers still have to rebuild against the generation
    /// the library publishes now.
    ///
    /// The flag is set when the library moved and it is cleared by the
    /// **consumer**, through `acknowledge_consumers_rebuilt`, once the
    /// consumer has rebuilt against the generation that is published now.
    /// It is never cleared by the fact that a later materialization found
    /// the library unchanged: that fact says nothing about the consumers.
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
    /// The directional pools the three hops walk.
    ///
    /// The list has one entry per hop and holds the **directional pool
    /// identity** of the hop, in hop order. It is the same pool the hop's
    /// `directional_pool_id` names, so a consumer can resolve a loop back to
    /// the direction rows it was built from without re-deriving the
    /// direction from the token order.
    pub directional_pool_ids: Vec<String>,
    /// The backup pools of each hop, as **directional** pool ids.
    ///
    /// A backup is a pool the hop could trade instead of the one in effect,
    /// so it is a directional row as well: a candidate without a direction
    /// row cannot be traded and is therefore not a backup.
    pub backup_directional_pool_ids: BTreeMap<String, Vec<String>>,
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
    /// The **directional** pool is named so an operator can see **which**
    /// hop bounds the loop instead of having to re-derive the fold. The
    /// closed loop names no base pool, so the name is a directional one.
    pub single_tick_capacity_binding_directional_pool_id: Option<String>,
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
    pub pool_selection_basis: String,
    /// The directional pool the hop walks.
    ///
    /// The identity is the direction layer's own id, and it resolves to the
    /// row that carries the hop's direction, its tick state and its
    /// capacity. The hop holds **no** base pool: the base pool facts live
    /// inside the directional row, so a consumer that needs the base facts
    /// resolves them through the direction layer and can never pick up a
    /// base pool without its direction.
    pub directional_pool_id: String,
    /// The direction of the hop: `0` is high address to low address,
    /// `1` is low address to high address.
    ///
    /// The value is the direction of the directional pool the hop walks. It
    /// is written from that row, so the stored direction and the numbers
    /// that belong to it can never drift apart.
    pub hop_direction: i64,
    /// The scope of the direction layer the hop's row was published under.
    pub directional_scope_key: String,
    /// The rank the row held inside its (pair, direction) group.
    ///
    /// `None` means the row was not measured, so it holds no rank.
    pub directional_pool_rank: Option<i64>,
    pub optimization_id: Option<String>,
    pub temporary_override: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopOptimizationRow {
    pub loop_key: String,
    pub hop_index: usize,
    pub optimization_id: String,
    /// The directional pool the recommendation named before the
    /// optimization took over.
    pub original_directional_pool_id: String,
    /// The directional pool that took the hop over.
    pub optimized_directional_pool_id: String,
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
    /// The moment the loop was measured the last time.
    ///
    /// The field is `None` for a loop that was never measured: the review
    /// clock of a loop starts with its **first** review, not with the
    /// rebuild that published it.
    pub last_reviewed_at: Option<String>,
    /// The moment the next round of the review of the loop may start.
    ///
    /// The moment is written by the **end** of the round that just
    /// finished -- a confirmed broadcast, or a measurement that found no
    /// profit -- plus the review interval, so every round restarts the
    /// clock of the loop.  It is not the measurement plus the interval:
    /// the time the round itself took is not time the loop waits.
    pub next_review_at: Option<String>,
    /// The seconds the review clock of the loop still has to run.
    ///
    /// The wait is **counted** from the moment the loop is scheduled at:
    /// the loop keeps no timer of its own, so the seconds that are left
    /// are the distance between the moment the next round may start and
    /// now.  Zero means the loop waits for nothing: it is due.
    pub review_wait_seconds: Option<i64>,
    /// The seconds the loop waited already beyond its start moment.
    ///
    /// The start moment is the one the loop is scheduled at, so the
    /// distance between it and now says how long the loop waited for a
    /// round that did not start yet.
    pub review_waited_seconds: Option<i64>,
    /// The moment the review cycle of the loop started.
    ///
    /// The cycle of a loop is the round plus the wait: the round runs
    /// from this moment until it is dealt with -- a confirmed broadcast,
    /// or a measurement that found no profit -- and the wait that
    /// follows is already folded into `next_review_at`.  The moment the
    /// round started is kept beside the loop because it is what says a
    /// measurement belongs to the round that is running now.
    pub review_cycle_started_at: Option<String>,
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
    /// The optimizations that are **in effect**: the hop trades the pool of
    /// the optimization instead of the pool the recommendation named.
    pub optimizations: Vec<ClosedLoopOptimizationRow>,
    /// The optimizations that are still **waiting**: the standby pool of a
    /// hop that the capacity gate has not needed yet.
    pub pending_optimizations: Vec<ClosedLoopOptimizationRow>,
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

/// The published price of one pool, as the USDG valuation reads it.
///
/// The valuation is a property of the **market**, not of a route: it
/// expresses the borrow token in USDG so the cross-tick threshold is
/// comparable. It therefore keeps its own view of the pool universe, the
/// one the base pool tables publish, and it is deliberately **not** narrowed
/// to the direction layer eligible subset: a valuation route that runs
/// through a pool the direction layer does not admit is still a real route,
/// and dropping it would change the USDG value of the borrow token and with
/// it the profit gate.
///
/// The fold does not read this view: the capacity of a hop comes from the
/// directional row the hop walks, so a hop direction and the numbers that
/// belong to it can never drift apart.
#[derive(Debug, Clone)]
struct ValuationPoolState {
    /// The square-root price of the pool, in Q96 fixed point.
    sqrt_price_x96: u128,
    /// The key id of the pool's token0.
    token0_key_id: String,
    /// The key id of the pool's token1.
    token1_key_id: String,
}

/// The direction layer as the closed loop consumes it.
///
/// The closed loop keeps **one** pool collection: the directional
/// projection. The capacity, the single-tick bound and the tick state of a
/// hop all come from the same row, so a hop's direction and the numbers
/// that belong to it can never drift apart. A direction is a pure function
/// of the token order, so a base pool projects onto exactly two rows and
/// the row a hop walks is decided by the token the hop spends.
#[derive(Debug, Clone, Default)]
struct DirectionalPoolIndex {
    /// Every published directional pool, keyed by its own identity.
    pools: BTreeMap<String, DirectionalPool>,
    /// The two rows of every base pool, keyed by (scope, base pool id).
    by_base: BTreeMap<(String, String), Vec<String>>,
    /// The scope each row was published under.
    scope_of: BTreeMap<String, String>,
    /// The rank a row held inside its (pair, direction).
    rank_of: BTreeMap<String, i64>,
    /// The `(pair, direction)` group every ranked row belongs to.
    group_of: BTreeMap<String, (String, i64)>,
    /// The ranked rows of every `(scope, pair, direction)` group, in rank
    /// order.
    ///
    /// The group is the **qualified** set of one direction: the direction
    /// layer never truncates it, so every qualified pool keeps a rank and
    /// stays reachable.  The closed loop reads the group to reach the pools
    /// a truncated candidate list dropped.
    by_group: BTreeMap<(String, String, i64), Vec<String>>,
}

impl DirectionalPoolIndex {
    /// One published row.
    fn row(&self, directional_pool_id: &str) -> Option<&DirectionalPool> {
        self.pools.get(directional_pool_id)
    }

    /// The rows of one base pool inside one scope, with their identities.
    fn rows_of_base(&self, scope_key: &str, base_pool_id: &str) -> Vec<(&str, &DirectionalPool)> {
        self.by_base
            .get(&(scope_key.to_owned(), base_pool_id.to_owned()))
            .map(|ids| {
                ids.iter()
                    .filter_map(|id| self.pools.get(id).map(|pool| (id.as_str(), pool)))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// The `(pair, direction)` group a row belongs to.
    fn group_of_row(&self, directional_pool_id: &str) -> Option<(String, i64)> {
        self.group_of.get(directional_pool_id).cloned()
    }

    /// The ranked rows of one group, in rank order.
    fn rows_of_group(
        &self,
        scope_key: &str,
        pair_key_id: &str,
        direction: i64,
    ) -> Vec<(&str, &DirectionalPool)> {
        self.by_group
            .get(&(
                scope_key.to_owned(),
                pair_key_id.to_owned(),
                direction,
            ))
            .map(|ids| {
                ids.iter()
                    .filter_map(|id| self.pools.get(id).map(|pool| (id.as_str(), pool)))
                    .collect()
            })
            .unwrap_or_default()
    }
}

/// The directional pool one hop walks.
///
/// The hop reads its direction from the row; the row is found by the token
/// the hop spends, because the direction of a hop is decided by which of
/// the pool's two tokens is the input.
#[derive(Debug, Clone)]
struct HopPool {
    directional_pool_id: String,
    scope_key: String,
    direction: PoolDirection,
    rank: Option<i64>,
}

/// One candidate of one hop: a pool the hop may trade in.
///
/// The **candidate tier** comes first: the pools the candidate edge (or the
/// market observation) publishes for this hop, in their own order.  The
/// **backup tier** follows: the pools of the same `(pair, direction)` group
/// of the direction layer that the candidate tier does not name.  The
/// direction layer never truncates its groups, so a pool that the candidate
/// list dropped stays in the group and stays reachable as a backup.
#[derive(Debug, Clone)]
struct HopCandidate {
    /// The optimization that records this candidate, when it is not the
    /// primary of the hop.
    optimization_id: Option<String>,
    /// The selection basis of the candidate.
    basis: String,
    /// The directional row the candidate resolves to.  `None` when the
    /// candidate carries no direction row and therefore cannot be traded.
    hop_pool: Option<HopPool>,
}

/// One hop of a selected path.
#[derive(Debug, Clone)]
struct SelectedHop {
    /// The candidates of the hop, in the order they are tried.
    candidates: Vec<HopCandidate>,
    /// The position of the winning candidate inside `candidates`.
    chosen: usize,
}

impl SelectedHop {
    /// The winning candidate of the hop.
    fn chosen_candidate(&self) -> &HopCandidate {
        &self.candidates[self.chosen]
    }

    /// The directional row the winning candidate resolves to.
    fn hop_pool(&self) -> &HopPool {
        self.chosen_candidate()
            .hop_pool
            .as_ref()
            .expect("the winning candidate always resolves to a direction row")
    }

    /// The **directional** pool the hop used before any optimization.
    ///
    /// The closed loop names directional pools only, so the optimization
    /// row names the direction row of the primary and not its base pool.
    fn primary_directional_pool_id(&self) -> Option<&str> {
        self.candidates
            .first()
            .and_then(|candidate| candidate.hop_pool.as_ref())
            .map(|hop_pool| hop_pool.directional_pool_id.as_str())
    }
}

/// The numbers one path publishes.
#[derive(Debug, Clone, Default)]
struct PathMeasures {
    /// The folded single-tick capacity of the path and the pool that binds
    /// it.
    folded_capacity: Option<(u128, String)>,
    /// The cross-tick ceiling of the path, in the anchor token.
    cross_tick_capacity_anchor_raw: Option<u128>,
    /// The USDG value of the borrow token.
    borrow_token_usdg_value_raw: Option<u128>,
    /// The decimals of the borrow token.
    borrow_token_decimals: Option<u32>,
}

/// The maximum number of combinations one path may try.
///
/// The three candidate lists are short, but the search must stay bounded: a
/// path that passes the gate on no combination at all is dropped, and the
/// search must never become the expensive part of a rebuild.
const MAX_HOP_CANDIDATE_COMBINATIONS: usize = 512;

/// Every combination of the three candidate lists, ordered by how far it
/// deviates from the primary combination.
///
/// The order is `(deviations, first, middle, third)`: a combination that
/// keeps more hops on their primary comes first, so one hop is replaced
/// before two hops are, and the primary combination is always tried first.
/// The order is total, so the search is deterministic.
fn hop_candidate_combinations(
    first: usize,
    middle: usize,
    third: usize,
) -> Vec<(usize, usize, usize)> {
    let mut combinations = Vec::with_capacity(first.saturating_mul(middle).saturating_mul(third));
    for i in 0..first {
        for j in 0..middle {
            for k in 0..third {
                combinations.push((i, j, k));
            }
        }
    }
    combinations.sort_by_key(|(i, j, k)| {
        (
            usize::from(*i > 0) + usize::from(*j > 0) + usize::from(*k > 0),
            *i,
            *j,
            *k,
        )
    });
    combinations.truncate(MAX_HOP_CANDIDATE_COMBINATIONS);
    combinations
}

struct ClosedLoopInput {
    candidate_edge: CandidateEdgeStatus,
    observation: CandidateExchangeStatus,
    anchors: BTreeMap<String, AnchorInput>,
    recommendations: Vec<Recommendation>,
    yz_directions: BTreeMap<(String, String), YzDirection>,
    middle_pools: BTreeMap<(String, String), MiddlePool>,
    candidate_pool_ids: BTreeSet<(String, String)>,
    /// The direction layer: the closed loop's **only** pool collection.
    ///
    /// The capacity, the single-tick bound and the tick state of a hop all
    /// come from one row, so a hop's direction and the numbers that belong
    /// to it cannot drift apart. A base pool projects onto exactly two
    /// rows, and the row a hop walks is decided by the token the hop
    /// spends.
    directional_pools: DirectionalPoolIndex,
    /// The published state of every scope of the direction layer the loop
    /// consumes, keyed by scope key: the version and the checksum.
    ///
    /// The closed loop records them, so a consumer can tell which
    /// projection a loop was built against and whether that projection has
    /// moved since.
    directional_scope_states: BTreeMap<String, (i64, String)>,
    /// The published single-tick state of every pool the valuation routes
    /// through.
    ///
    /// The state is what lets the path capacity be expressed in the
    /// **anchor** token: the bound of a hop is denominated in the token
    /// that hop spends, and the conversion between the tokens of the hops
    /// is read from the pools themselves. It is deliberately the **whole**
    /// pool universe and not the closed loop's path: the valuation is a
    /// property of the market, so narrowing it to the path would change
    /// the USDG value of the borrow token, and with it the cross-tick
    /// threshold comparison, which the design forbids.
    valuation_pool_states: BTreeMap<String, ValuationPoolState>,
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
    /// Whether the direction layer names the **primary** pool of a hop.
    ///
    /// The direction layer ranks the pools of a `(pair, direction)` group by
    /// the price quality of that direction; when the switch is on, the closed
    /// loop walks the group in that order, so the primary of a hop is the pool
    /// the direction layer ranked first.  When the switch is off, the legacy
    /// order of the candidate tier is kept, which is the rollback path.
    directional_primary_order: bool,
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

/// The identity of the direction layer the closed loop was built against.
///
/// The closed loop walks **two** scopes of the direction layer: the
/// candidate edge scope supplies the first and the third hop, the candidate
/// exchange scope supplies the middle one. The two are folded into a single
/// identity, so a consumer can tell which projection a loop was built
/// against, and whether **either** part of that projection has moved since.
///
/// The version is the highest version the two scopes publish and the
/// checksum covers both scopes' version and checksum, so a change in either
/// scope changes the identity.
fn directional_layer_identity(scope_states: &BTreeMap<String, (i64, String)>) -> (i64, String) {
    let version = scope_states
        .values()
        .map(|(version, _)| *version)
        .max()
        .unwrap_or(0);
    let payload = scope_states
        .iter()
        .map(|(scope_key, (version, checksum))| {
            format!("{scope_key}:{version}:{checksum}")
        })
        .collect::<Vec<_>>()
        .join("|");
    (version, checksum_text(&payload))
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
    directional_pool_version: i64,
    directional_pool_checksum: &'a str,
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
    let (directional_pool_version, directional_pool_checksum) =
        directional_layer_identity(&input.directional_scope_states);
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
        directional_pool_version,
        directional_pool_checksum,
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
    // The direction layer is the closed loop's only pool collection. The
    // loop walks two scopes of it: the candidate edge scope supplies the
    // first and the third hop, the candidate exchange scope supplies the
    // middle one.
    let mut directional_pools = DirectionalPoolIndex::default();
    let mut directional_scope_states = BTreeMap::new();
    for scope_key in [HOP_SIDE_SCOPE_KEY, HOP_MIDDLE_SCOPE_KEY] {
        read_directional_pools(
            connection,
            network,
            chain_id,
            scope_key,
            &mut directional_pools,
        )?;
        if let Some((version, checksum)) =
            read_directional_scope_state(connection, network, chain_id, scope_key)?
        {
            directional_scope_states.insert(scope_key.to_owned(), (version, checksum));
        }
    }
    let valuation_pool_states = read_valuation_pool_states(connection, network, chain_id)?;
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
        directional_pools,
        directional_scope_states,
        valuation_pool_states,
        token_decimals,
        usdg_token_key_id,
        cross_tick_review_threshold_usdg_raw: read_cross_tick_review_threshold_usdg_raw(),
        directional_primary_order: directional_primary_order_enabled(),
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
/// TASK-172 / AC-221: the depth snapshot table has exactly one writer -- the
/// pool manager -- which writes the price columns and the bounds in **one**
/// statement. A stored bound therefore always belongs to the price next to
/// it, and it is the only evidence available about the tick room of a pool
/// that publishes no tick state of its own.
///
/// A pool without a published bound is absent from the map. That is a
/// coverage gap of the pool index, not a market conclusion, so such a pool
/// does not block the path.
/// Read the published single-tick state of every candidate pool.
///
/// The state is what lets the path capacity be expressed in the **anchor**
/// token: the bound of a hop is denominated in the token that hop spends,
/// and the conversion between the tokens of the hops is read from the
/// pools themselves.
///
/// A pool without a usable state is absent from the map. That is a
/// coverage gap of the pool index, not a market conclusion.
fn read_valuation_pool_states(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, ValuationPoolState>, ClosedLoopError> {
    // The valuation reads **two** halves of a pool:
    //
    //   * the static facts -- fee, tick spacing and the two token key ids --
    //     come from the base pool tables, which own them;
    //   * the market half -- the price, and the liquidity that makes the
    //     price an observation -- comes from the liquidity snapshot table,
    //     which the liquidity worker refreshes.
    //
    // The split keeps the pool **universe** whole: the static facts still
    // come from the full base pool set, so no pool that carries a fact
    // today drops out of the universe.  What changes is the market half,
    // and it changes to the snapshot the capacity fold already reads -- so
    // the valuation and the fold can no longer describe two different
    // moments.
    let market = read_valuation_market_states(connection, network, chain_id)?;
    let mut facts = BTreeMap::new();
    for table in [
        "v4_exchange_pools_candidate_exchange",
        "v4_exchange_pools_core4",
    ] {
        read_valuation_pool_facts_from_table(connection, network, chain_id, table, &mut facts)?;
    }
    let mut states = BTreeMap::new();
    for (pool_id, fact) in facts {
        // A pool without a market snapshot publishes no valuation.  The
        // absence is a coverage gap of the pool index, never a zero.
        let Some(market_state) = market.get(&pool_id) else {
            continue;
        };
        states.insert(
            pool_id,
            ValuationPoolState {
                sqrt_price_x96: market_state.sqrt_price_x96,
                token0_key_id: fact.token0_key_id,
                token1_key_id: fact.token1_key_id,
            },
        );
    }
    Ok(states)
}

/// The market half of a pool's published state.
///
/// The valuation needs the **price** of a pool, and a price is only an
/// observation when the pool publishes liquidity next to it.
struct ValuationMarketState {
    /// The square-root price of the pool, in Q96 fixed point.
    sqrt_price_x96: u128,
}

/// Read the market half of every pool the snapshot table carries.
///
/// The snapshot table is the store's live market reading: the liquidity
/// worker refreshes it, and the price columns it holds always belong to the
/// liquidity measured next to them.
fn read_valuation_market_states(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, ValuationMarketState>, ClosedLoopError> {
    let mut states = BTreeMap::new();
    if !table_exists(connection, VALUATION_SNAPSHOT_TABLE)? {
        return Ok(states);
    }
    // The market columns are read only when the table carries them.  A
    // store whose snapshot table does not publish them publishes no
    // valuation at all, which is a coverage gap of the pool index rather
    // than a wrong number.
    for column in ["sqrt_price_x96", "tick", "liquidity"] {
        if !table_has_column(connection, VALUATION_SNAPSHOT_TABLE, column)? {
            return Ok(states);
        }
    }
    let mut statement = connection.prepare(&format!(
        "SELECT pool_id, sqrt_price_x96, tick, liquidity
         FROM {VALUATION_SNAPSHOT_TABLE}
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
    ))?;
    let rows = statement.query_map(params![network, chain_id, NETWORK_PROTOCOL], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?,
            row.get::<_, Option<i64>>(2)?,
            row.get::<_, Option<String>>(3)?,
        ))
    })?;
    for row in rows {
        let (pool_id, sqrt_price_x96, tick, liquidity) = row?;
        let Some(sqrt_price_x96) =
            sqrt_price_x96.and_then(|text| text.trim().parse::<u128>().ok())
        else {
            continue;
        };
        if sqrt_price_x96 == 0 {
            continue;
        }
        // The liquidity column stores the raw uint128 liquidity as TEXT, the
        // same representation the pool snapshot table uses.  Reading it as an
        // INTEGER truncates every value above i64::MAX and fails outright on
        // the production store, so the value is parsed from its text form.
        //
        // A pool that publishes an **empty** liquidity has been measured as
        // having none -- the pool manager writes the literal empty string
        // for a pool whose live liquidity is zero -- so it is not an
        // observation and it publishes no state.
        let Some(liquidity) = liquidity.and_then(|text| text.trim().parse::<u128>().ok()) else {
            continue;
        };
        if liquidity <= 0 {
            continue;
        }
        // The tick is not part of the valuation view, but a pool that does
        // not publish one does not publish a usable price either, so it
        // stays out of the map.
        if tick.is_none() {
            continue;
        }
        states.entry(pool_id).or_insert(ValuationMarketState { sqrt_price_x96 });
    }
    Ok(states)
}

/// The static half of a pool's published state.
struct ValuationPoolFacts {
    /// The key id of the pool's token0.
    token0_key_id: String,
    /// The key id of the pool's token1.
    token1_key_id: String,
}

/// Read the static facts of every pool of one scope table.
///
/// The candidate-exchange table wins when both tables carry the same pool:
/// it is the table the closed-loop layer actually consumes, so its facts are
/// the ones that belong to the pools it publishes.
fn read_valuation_pool_facts_from_table(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    table: &str,
    facts: &mut BTreeMap<String, ValuationPoolFacts>,
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
        "SELECT pool_id, fee, tick_spacing, token0_key_id, token1_key_id
         FROM {table}
         WHERE network = ?1 AND chain_id = ?2"
    ))?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<i64>>(1)?,
            row.get::<_, Option<i64>>(2)?,
            row.get::<_, Option<String>>(3)?,
            row.get::<_, Option<String>>(4)?,
        ))
    })?;
    for row in rows {
        let (
            pool_id,
            fee,
            tick_spacing,
            token0_key_id,
            token1_key_id,
        ) = row?;
        // A fact that cannot be used is not published: the valuation needs
        // the fee and the tick spacing to describe the pool, and it needs
        // both token key ids to know which side of the pool it is on.
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
        facts.entry(pool_id).or_insert(ValuationPoolFacts {
            token0_key_id,
            token1_key_id,
        });
    }
    Ok(())
}

/// The table of the direction layer.
const DIRECTIONAL_POOL_TABLE: &str = "manager_v4_directional_exchange_pools";

/// Read the published state of one scope of the direction layer.
///
/// The state carries the version and the checksum of the projection. The
/// closed loop records them, so a consumer can tell which projection a
/// loop was built against and whether that projection has moved since.
fn read_directional_scope_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<Option<(i64, String)>, ClosedLoopError> {
    let stored = connection
        .query_row(
            "SELECT directional_pool_version, materialized_checksum
             FROM manager_v4_directional_pool_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4",
            params![network, chain_id, NETWORK_PROTOCOL, scope_key],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()?;
    Ok(stored)
}

/// Read every published directional pool of one scope.
///
/// The rows are the closed loop's only pool collection from here on: the
/// tick state, the single-tick bound and the depth capacity of a hop all
/// come from the row of the direction the hop trades in.
fn read_directional_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    index: &mut DirectionalPoolIndex,
) -> Result<(), ClosedLoopError> {
    if !table_exists(connection, DIRECTIONAL_POOL_TABLE)? {
        // The direction layer has not been materialized yet. That is a
        // coverage gap of the pool index, not a market conclusion.
        return Ok(());
    }
    let has_depth_capacity = table_has_column(connection, DIRECTIONAL_POOL_TABLE, "depth_capacity_raw")?;
    let depth_capacity_expression = if has_depth_capacity {
        "depth_capacity_raw"
    } else {
        "NULL"
    };
    // The pair identity is what groups the rows of one direction.  A table
    // that predates the column simply has no group, and the closed loop
    // then reaches its backups through the candidate list alone.
    let has_pair_key = table_has_column(connection, DIRECTIONAL_POOL_TABLE, "pair_key_id")?;
    let pair_key_expression = if has_pair_key { "pair_key_id" } else { "NULL" };
    let sql = format!(
        "SELECT base_pool_id, direction, directional_pool_id, pool_manager,
                pool_key_json, token0, token1, token0_key_id, token1_key_id,
                fee, tick_spacing, hooks, sqrt_price_x96, tick, liquidity,
                input_capacity_raw, input_capacity_measured, directional_output,
                directional_output_measured, pair_direction_rank, pool_status,
                {depth_capacity_expression}, {pair_key_expression}
         FROM {DIRECTIONAL_POOL_TABLE}
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY base_pool_id, direction"
    );
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, scope_key],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, String>(6)?,
                row.get::<_, String>(7)?,
                row.get::<_, String>(8)?,
                row.get::<_, i64>(9)?,
                row.get::<_, i64>(10)?,
                row.get::<_, String>(11)?,
                row.get::<_, Option<String>>(12)?,
                row.get::<_, Option<i64>>(13)?,
                row.get::<_, Option<String>>(14)?,
                row.get::<_, Option<String>>(15)?,
                row.get::<_, bool>(16)?,
                row.get::<_, Option<f64>>(17)?,
                row.get::<_, bool>(18)?,
                row.get::<_, Option<i64>>(19)?,
                row.get::<_, String>(20)?,
                row.get::<_, Option<String>>(21)?,
                row.get::<_, Option<String>>(22)?,
            ))
        },
    )?;
    for row in rows {
        let (
            base_pool_id,
            direction,
            directional_pool_id,
            pool_manager,
            pool_key_json,
            token0,
            token1,
            token0_key_id,
            token1_key_id,
            fee,
            tick_spacing,
            hooks,
            sqrt_price_x96,
            tick,
            liquidity,
            input_capacity_raw,
            input_capacity_measured,
            directional_output,
            directional_output_measured,
            pair_direction_rank,
            pool_status,
            depth_capacity_raw,
            pair_key_id,
        ) = row?;
        let Some(direction) = PoolDirection::from_raw(direction) else {
            return Err(ClosedLoopError::InvalidStoredTopology(format!(
                "directional pool {directional_pool_id} has direction {direction}, expected 0 or 1"
            )));
        };
        // The directional pool is built through its constructor, so the
        // base pool enters the object **together with** the direction it
        // is traded in.  There is no way to hold the base pool alone.
        let mut pool = DirectionalPool::of_pool(
            V4Pool {
                base_pool_id: base_pool_id.clone(),
                pool_manager,
                pool_key_json,
                token0,
                token1,
                token0_key_id,
                token1_key_id,
                fee,
                tick_spacing,
                hooks,
                sqrt_price_x96,
                tick,
                liquidity,
                depth_capacity_raw,
            },
            direction,
        );
        pool.input_capacity_raw = input_capacity_raw;
        pool.input_capacity_measured = input_capacity_measured;
        pool.directional_output = directional_output;
        pool.directional_output_measured = directional_output_measured;
        pool.pair_direction_rank = pair_direction_rank;
        pool.pool_status = pool_status;
        index
            .by_base
            .entry((scope_key.to_owned(), base_pool_id))
            .or_default()
            .push(directional_pool_id.clone());
        index.scope_of.insert(directional_pool_id.clone(), scope_key.to_owned());
        if let Some(rank) = pool.pair_direction_rank {
            index.rank_of.insert(directional_pool_id.clone(), rank);
        }
        if let Some(pair_key_id) = pair_key_id {
            index
                .group_of
                .insert(directional_pool_id.clone(), (pair_key_id, direction.raw()));
        }
        index.pools.insert(directional_pool_id, pool);
    }
    // The groups are built after the rows, so the order inside a group can
    // be the rank order rather than the row order of the table.
    let mut by_group: BTreeMap<(String, String, i64), Vec<String>> = BTreeMap::new();
    for (directional_pool_id, (pair_key_id, direction)) in &index.group_of {
        // Only a ranked row is qualified: a row without a rank was never
        // priced, and an unpriced pool is not a candidate of the group.
        if !index.rank_of.contains_key(directional_pool_id) {
            continue;
        }
        let Some(scope_key) = index.scope_of.get(directional_pool_id) else {
            continue;
        };
        by_group
            .entry((scope_key.clone(), pair_key_id.clone(), *direction))
            .or_default()
            .push(directional_pool_id.clone());
    }
    for ids in by_group.values_mut() {
        ids.sort_by_key(|id| index.rank_of.get(id).copied().unwrap_or(i64::MAX));
    }
    index.by_group = by_group;
    Ok(())
}

/// Whether a table carries a column.
fn table_has_column(
    connection: &Connection,
    table: &str,
    column: &str,
) -> Result<bool, ClosedLoopError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let names = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(names.iter().any(|name| name == column))
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
    directional_pools: &DirectionalPoolIndex,
    hop_directional_pool_ids: &[String],
) -> (Option<u128>, Option<String>) {
    // The bounds of the hops are denominated in the token each hop spends: the
    // first hop spends the anchor token, the middle hop spends Y and the
    // third hop spends Z. Taking the minimum of three numbers in three
    // different units is a **unit error**, so the bounds are folded into the
    // anchor token before they are compared.
    //
    // The fold needs the price of every hop. When every pool publishes a
    // usable state, the folded capacity is the only value the gate uses.
    // A legacy row may still publish a raw zero bound before tick states
    // are rolled out. That zero is retained as a directional unusability
    // signal, but raw positive bounds are never compared across tokens.
    if let Some((capacity, blocking_pool)) =
        folded_path_capacity(directional_pools, hop_directional_pool_ids)
    {
        return (Some(capacity), Some(blocking_pool));
    }
    // The fallback judges "this direction has no room" by the bound the
    // **row of that direction** publishes. The row's bound is recomputed by
    // the direction layer from the very observation the row carries, so it
    // is bound to the price next to it, and it is already the bound of the
    // direction the hop trades in: no parallel array of directions is
    // needed to pick the right side.
    //
    // A row that publishes no bound at all stays out of the verdict: the
    // missing measurement is a coverage gap of the pool index, not a
    // market conclusion. A bound that was measured as zero is a measured
    // fact and disqualifies the hop.
    let mut unusable_pool = None;
    for directional_pool_id in hop_directional_pool_ids {
        let Some(pool) = directional_pools.row(directional_pool_id) else {
            continue;
        };
        let bound = pool
            .input_capacity_raw
            .as_deref()
            .map(str::trim)
            .and_then(|text| text.parse::<u128>().ok());
        if bound == Some(0) {
            unusable_pool = Some(directional_pool_id.to_owned());
            break;
        }
    }
    (unusable_pool.clone().map(|_| 0), unusable_pool)
}

/// The usable single-tick state of one directional pool.
///
/// The state is read from the row, so it always belongs to the direction
/// the row carries. A row whose observation cannot be used publishes no
/// state: the capacity of a hop with an unusable price or liquidity is not
/// computable, and publishing a zero would read as "measured, and there is
/// no room".
struct UsableTickState {
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    tick_spacing: i32,
    tick: i32,
}

/// The tick state of one directional pool, when it is usable.
fn usable_tick_state(pool: &DirectionalPool) -> Option<UsableTickState> {
    let sqrt_price_x96 = pool
        .sqrt_price_x96()
        .map(str::trim)
        .and_then(|text| text.parse::<u128>().ok())?;
    if sqrt_price_x96 == 0 {
        return None;
    }
    // The liquidity column stores the raw uint128 liquidity as TEXT, the
    // same representation the pool snapshot table uses. Reading it as an
    // INTEGER truncates every value above i64::MAX and fails outright on
    // the production store, so the value is parsed from its text form.
    let liquidity = pool
        .liquidity()
        .map(str::trim)
        .and_then(|text| text.parse::<u128>().ok())?;
    if liquidity == 0 {
        return None;
    }
    let tick = pool.tick()?;
    let fee = pool.fee();
    if fee <= 0 {
        return None;
    }
    let tick_spacing = pool.tick_spacing();
    if tick_spacing <= 0 {
        return None;
    }
    Some(UsableTickState {
        sqrt_price_x96,
        liquidity,
        fee_pips: fee as u32,
        tick_spacing: tick_spacing as i32,
        tick: tick as i32,
    })
}

/// The depth capacity of a pool, when it is measured.
///
/// The capacity is a raw uint128 amount stored as TEXT. Casting it to an
/// INTEGER clamps every value above i64::MAX to i64::MAX, which would
/// silently promote an oversized pool instead of rejecting it, so the text
/// form is parsed instead. A capacity of zero is not a capacity: it is the
/// absence of a measurement, and the old reader skipped it as well.
fn parse_depth_capacity(value: Option<&str>) -> Option<u128> {
    let text = value?.trim();
    if text.is_empty() {
        return None;
    }
    let capacity = text.parse::<u128>().ok()?;
    if capacity == 0 {
        return None;
    }
    Some(capacity)
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
    directional_pools: &DirectionalPoolIndex,
    hop_directional_pool_ids: &[String],
) -> Option<(u128, String)> {
    if hop_directional_pool_ids.is_empty() {
        return None;
    }

    // The hop spends a token and receives another one. The row carries both
    // the direction and the token order, so the hop's input and output
    // tokens are read from the row itself: there is no parallel array of
    // directions that could disagree with the row.
    let mut hops: Vec<pool_manager::single_tick_capacity::CapacityHop> =
        Vec::with_capacity(hop_directional_pool_ids.len());
    for directional_pool_id in hop_directional_pool_ids {
        let pool = directional_pools.row(directional_pool_id)?;
        let state = usable_tick_state(pool)?;
        hops.push(pool_manager::single_tick_capacity::CapacityHop {
            // The capacity binds a **directional** pool, so the pool the
            // fold names is the directional one the hop walks.
            pool_id: directional_pool_id.to_owned(),
            token_in: pool.input_token_key_id().to_owned(),
            token_out: pool.output_token_key_id().to_owned(),
            sqrt_price_x96: state.sqrt_price_x96,
            liquidity: state.liquidity,
            fee_pips: state.fee_pips,
            tick_spacing: state.tick_spacing,
            tick: state.tick,
            zero_for_one: pool.direction.zero_for_one(),
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
    directional_pools: &DirectionalPoolIndex,
    hop_directional_pool_ids: &[String],
) -> Option<u128> {
    if hop_directional_pool_ids.is_empty() {
        return None;
    }
    let mut hops: Vec<pool_manager::single_tick_capacity::DepthCapacityHop> =
        Vec::with_capacity(hop_directional_pool_ids.len());
    for directional_pool_id in hop_directional_pool_ids {
        let pool = directional_pools.row(directional_pool_id)?;
        let state = usable_tick_state(pool)?;
        let Some(capacity) = parse_depth_capacity(pool.depth_capacity_raw()) else {
            return None;
        };
        hops.push(pool_manager::single_tick_capacity::DepthCapacityHop {
            pool_id: pool.base_pool_id().to_owned(),
            token_in: pool.input_token_key_id().to_owned(),
            token_out: pool.output_token_key_id().to_owned(),
            sqrt_price_x96: state.sqrt_price_x96,
            zero_for_one: pool.direction.zero_for_one(),
            depth_capacity_raw: capacity,
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
        .valuation_pool_states
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
/// The directional pool one hop walks.
///
/// The direction of a hop is a **pure function** of the token order: a base
/// pool projects onto exactly two rows, and the row a hop walks is the one
/// whose input token is the token the hop spends. The direction is therefore
/// **read**, never inferred from a logical direction key.
///
/// The row's output token is compared with the hop's output token. A row
/// that trades the other way is a topology error and is reported as such,
/// instead of being silently accepted and folded the wrong way round.
///
/// A pool the direction layer does not publish is a topology error too: the
/// direction layer is the closed loop's upstream, and a hop that walks a
/// pool the projection does not know cannot be priced from it. The
/// materialization stops and the published loops stay as they are.
fn resolve_hop_directional_pool(
    directional_pools: &DirectionalPoolIndex,
    scope_key: &str,
    base_pool_id: &str,
    token_in: &str,
    token_out: &str,
) -> Result<HopPool, ClosedLoopError> {
    let rows = directional_pools.rows_of_base(scope_key, base_pool_id);
    if rows.is_empty() {
        return Err(ClosedLoopError::InvalidStoredTopology(format!(
            "the direction layer publishes no pool {base_pool_id} in scope {scope_key}"
        )));
    }
    let token_in = token_in.trim();
    let mut chosen: Option<(&str, &DirectionalPool)> = None;
    for (directional_pool_id, row) in &rows {
        if row.input_token_key_id().trim() == token_in {
            chosen = Some((directional_pool_id, row));
            break;
        }
    }
    let Some((directional_pool_id, row)) = chosen else {
        return Err(ClosedLoopError::InvalidStoredTopology(format!(
            "hop pool {base_pool_id} does not carry the hop input token {token_in}"
        )));
    };
    if row.output_token_key_id().trim() != token_out.trim() {
        return Err(ClosedLoopError::InvalidStoredTopology(format!(
            "hop pool {base_pool_id} trades {} but the hop expects {token_out}",
            row.output_token_key_id()
        )));
    }
    Ok(HopPool {
        directional_pool_id: directional_pool_id.to_owned(),
        scope_key: scope_key.to_owned(),
        direction: row.direction,
        rank: row.pair_direction_rank,
    })
}

/// Build the ordered candidate list of one hop.
///
/// The **candidate tier** is the list the candidate edge (or the market
/// observation) publishes for the hop, in their own order: its first entry
/// is the hop's primary.  The **backup tier** follows and holds the pools of
/// the same `(pair, direction)` group of the direction layer that the
/// candidate tier does not name 鈥?the pools a truncation would have
/// dropped.  The direction layer never truncates, so the group keeps every
/// qualified pool and the backup tier is what makes them reachable.
///
/// The primary must resolve to a direction row: a hop whose primary has none
/// is a topology error and aborts the materialization.  A backup without a
/// direction row is not tradeable and is skipped.
///
/// When the direction layer is the authority of the primary order, the whole
/// candidate list is re-ordered by the direction rank before the hop walks
/// it.  The re-order is a **pure permutation**: the membership of the list
/// does not change, so no pool becomes reachable that the list could not
/// reach before, and no gate moves.
fn build_hop_candidates(
    input: &ClosedLoopInput,
    scope_key: &str,
    candidate_tier: &[(String, String, Option<String>)],
    token_in: &str,
    token_out: &str,
    backup_id_prefix: &str,
) -> Result<Vec<HopCandidate>, ClosedLoopError> {
    let mut candidates: Vec<HopCandidate> = Vec::with_capacity(candidate_tier.len());
    let mut named: BTreeSet<String> = BTreeSet::new();
    for (position, (pool_id, basis, optimization_id)) in candidate_tier.iter().enumerate() {
        let resolved = resolve_hop_directional_pool(
            &input.directional_pools,
            scope_key,
            pool_id,
            token_in,
            token_out,
        );
        named.insert(pool_id.clone());
        if position == 0 {
            // The primary of a hop must resolve: a hop whose primary has no
            // direction row is a topology error, not a missing backup.
            let hop_pool = resolved?;
            candidates.push(HopCandidate {
                optimization_id: optimization_id.clone(),
                basis: basis.clone(),
                hop_pool: Some(hop_pool),
            });
            continue;
        }
        candidates.push(HopCandidate {
            optimization_id: optimization_id.clone(),
            basis: basis.clone(),
            hop_pool: resolved.ok(),
        });
    }
    // The backup tier: the rest of the direction group of the primary.
    let Some(primary) = candidates.first().and_then(|candidate| candidate.hop_pool.as_ref()) else {
        return Ok(candidates);
    };
    let Some((pair_key_id, direction)) = input
        .directional_pools
        .group_of_row(&primary.directional_pool_id)
    else {
        return Ok(candidates);
    };
    let group = input
        .directional_pools
        .rows_of_group(scope_key, &pair_key_id, direction);
    let eligible = group.len();
    for (directional_pool_id, row) in group {
        let base_pool_id = row.base_pool_id().to_owned();
        if named.contains(&base_pool_id) {
            continue;
        }
        let Some(rank) = row.pair_direction_rank else {
            continue;
        };
        // The tier of the pool inside its own group: the candidate tier of
        // the direction layer keeps the first `N` ranks, and everything
        // beyond them is the backup tier 鈥?the tail a truncation dropped.
        let tier = match tier_of_rank(rank, eligible) {
            DirectionalPoolTier::Candidate => "candidate",
            DirectionalPoolTier::Backup => "backup",
        };
        candidates.push(HopCandidate {
            optimization_id: Some(format!("{backup_id_prefix}-{tier}-rank-{rank}")),
            basis: format!("directional_{tier}_tier_rank:{rank}"),
            hop_pool: Some(HopPool {
                directional_pool_id: directional_pool_id.to_owned(),
                scope_key: scope_key.to_owned(),
                direction: row.direction,
                rank: Some(rank),
            }),
        });
    }
    if input.directional_primary_order {
        order_hop_candidates_by_direction_rank(&mut candidates, backup_id_prefix);
    }
    Ok(candidates)
}

/// Order the candidates of one hop by the direction layer's own ranking.
///
/// The direction layer ranks every pool of a `(pair, direction)` group by the
/// price quality of that direction, and the closed loop walks the group in
/// that order: the **primary** of a hop is the pool the direction layer
/// ranked first.
///
/// The order is a **pure permutation** of the candidate list, so the
/// membership does not change and no gate moves.  Three properties hold:
///
/// * A pool that carries no rank was never priced, so it stays behind every
///   ranked pool.
/// * A pool without a direction row has no rank either, so the primary of a
///   hop always resolves to a direction row.
/// * The sort is stable, so two pools of the same rank keep the order the
///   candidate tier gave them.
///
/// The optimization id of a candidate names its **position** in the list the
/// hop walks (`{prefix}-rank-{n}`), because that is the order the candidate
/// is tried in.  The direction rank itself is published on the hop row and
/// in the basis of the candidate, so nothing is lost by the renumbering.
fn order_hop_candidates_by_direction_rank(candidates: &mut [HopCandidate], prefix: &str) {
    candidates.sort_by(|left, right| {
        let left_rank = left.hop_pool.as_ref().and_then(|pool| pool.rank);
        let right_rank = right.hop_pool.as_ref().and_then(|pool| pool.rank);
        match (left_rank, right_rank) {
            (Some(left_rank), Some(right_rank)) => left_rank.cmp(&right_rank),
            (Some(_), None) => std::cmp::Ordering::Less,
            (None, Some(_)) => std::cmp::Ordering::Greater,
            (None, None) => std::cmp::Ordering::Equal,
        }
    });
    for (position, candidate) in candidates.iter_mut().enumerate() {
        let rank = candidate.hop_pool.as_ref().and_then(|pool| pool.rank);
        candidate.optimization_id = if position == 0 {
            // The primary of the hop is not an optimization: it is the pool
            // the direction layer named.
            None
        } else {
            Some(format!("{prefix}-rank-{}", position + 1))
        };
        if position == 0 {
            // The basis of the primary answers "why this pool", and the
            // answer is now the rank the direction layer gave it.  A primary
            // without a rank means the group held no priced pool at all, and
            // then the basis of the candidate tier still holds the truth.
            if let Some(rank) = rank {
                candidate.basis = format!("directional_pair_direction_rank:{rank}");
            }
        }
    }
}

/// The verdict of the path level capacity gate for one combination.
///
/// `None` means the combination passes the gate.  `Some((reason, blocking))`
/// names the reason and the pool that disqualified the combination.
///
/// The gate itself is unchanged: the folded single-tick bound must not be a
/// measured zero, and the folded path capacity must not fall below the
/// shared floor.  The backup wiring only changes **which pools** are tried.
fn capacity_gate_verdict(
    input: &ClosedLoopInput,
    hop_directional_pool_ids: &[String],
) -> Option<(String, String)> {
    let (path_min_tick_bound_raw, path_tick_blocking_pool) =
        path_min_single_tick_bound(&input.directional_pools, hop_directional_pool_ids);
    if path_min_tick_bound_raw.is_some_and(|bound| bound == 0) {
        let blocking = path_tick_blocking_pool
            .as_deref()
            .unwrap_or("unknown")
            .to_owned();
        return Some((format!("path_single_tick_bound_unusable:{blocking}"), blocking));
    }
    let (path_min_capacity_raw, path_blocking_pool) =
        path_min_capacity(&input.directional_pools, hop_directional_pool_ids);
    if path_min_capacity_raw.is_some_and(|capacity| capacity < MIN_CLOSED_LOOP_CAPACITY_RAW) {
        let blocking = path_blocking_pool
            .as_deref()
            .unwrap_or("unknown")
            .to_owned();
        return Some((format!("path_capacity_below_minimum:{blocking}"), blocking));
    }
    None
}

/// Measure one combination of a path.
fn measure_path(
    input: &ClosedLoopInput,
    anchor_token_key_id: &str,
    hop_directional_pool_ids: &[String],
) -> PathMeasures {
    // The folded capacity is the authoritative single-tick bound of the
    // loop.  It is captured separately from the legacy filter below,
    // because the filter may fall back to a per-hop bound when the fold
    // cannot be performed, and that fallback is **not** a folded capacity
    // in anchor units.
    let folded_capacity =
        folded_path_capacity(&input.directional_pools, hop_directional_pool_ids);
    // The cross-tick ceiling is the **depth** capacity of the path, folded
    // into the anchor token with the same cumulative-rate fold.
    let cross_tick_capacity_anchor_raw = folded_path_depth_capacity(
        &input.directional_pools,
        hop_directional_pool_ids,
    );
    // The borrow token is the anchor token: it is the token the loop spends
    // first and receives last, so its USDG value is what values the profit
    // of the closed loop.
    let borrow_token_usdg_value_raw = usdg_valuation_for_token(input, anchor_token_key_id)
        .map(|valuation| valuation.value_usdg_raw);
    let borrow_token_decimals = input.token_decimals.get(anchor_token_key_id).copied();
    PathMeasures {
        folded_capacity,
        cross_tick_capacity_anchor_raw,
        borrow_token_usdg_value_raw,
        borrow_token_decimals,
    }
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
    directional_pools: &DirectionalPoolIndex,
    hop_directional_pool_ids: &[String],
) -> (Option<u128>, Option<String>) {
    let mut minimum: Option<u128> = None;
    let mut blocking_pool: Option<String> = None;
    for directional_pool_id in hop_directional_pool_ids {
        let Some(pool) = directional_pools.row(directional_pool_id) else {
            continue;
        };
        let Some(capacity) = parse_depth_capacity(pool.depth_capacity_raw()) else {
            continue;
        };
        // The blocking pool is named the way the loop names its pools:
        // by the **directional** pool the hop walks.
        let pool_id = directional_pool_id.to_owned();
        if capacity < MIN_CLOSED_LOOP_CAPACITY_RAW {
            return (Some(capacity), Some(pool_id));
        }
        if minimum.is_none_or(|current| capacity < current) {
            minimum = Some(capacity);
            blocking_pool = Some(pool_id);
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
                // The direction of every hop is **read** from the direction
                // layer, never inferred from the logical direction key.
                //
                // The logical key names the trade (`A -> B`), while the pool
                // orders its two tokens by numeric address. Those two orders
                // are independent, so a hop that logically spends `A` may
                // still spend the pool's `token1`. Deriving the direction
                // from the logical key swaps the input and the output token
                // of the hop, and the cumulative fold then accumulates the
                // inverse rate of that hop, which destroys the capacity of
                // the whole loop.
                //
                // The first and the third hop come from the candidate edge
                // scope, the middle hop from the candidate exchange scope:
                // each hop walks the projection of the layer that published
                // its pool.
                // The candidate tiers of the three hops.
                //
                // The candidate tier of a hop is the list the candidate edge
                // (or the market observation) publishes for it; the backup
                // tier is the rest of the direction group of the direction
                // layer, which is what keeps a truncated pool reachable.
                let first_tier = first_options
                    .iter()
                    .enumerate()
                    .map(|(position, recommendation)| {
                        let optimization_id = if position == 0 {
                            None
                        } else {
                            Some(format!("first-rank-{}", recommendation.rank))
                        };
                        (
                            recommendation.pool_id.clone(),
                            recommendation.selection_basis.clone(),
                            optimization_id,
                        )
                    })
                    .collect::<Vec<_>>();
                let middle_basis = format!(
                    "observation_direction_optimal_pool:{}:{}",
                    address_from_token_key_id(y_token),
                    address_from_token_key_id(z_token)
                );
                let middle_tier = std::iter::once((
                    middle_pool_id.to_owned(),
                    middle_basis.clone(),
                    None,
                ))
                .chain(
                    yz.pool_ids
                        .iter()
                        .enumerate()
                        .filter(|(index, pool_id)| *index > 0 || pool_id.as_str() != middle_pool_id)
                        .map(|(index, pool_id)| {
                            let optimization_id = if index == 0 {
                                "middle-rank-1".to_owned()
                            } else {
                                format!("middle-rank-{}", index + 1)
                            };
                            (
                                pool_id.clone(),
                                "observation_direction_pool_rank".to_owned(),
                                Some(optimization_id),
                            )
                        }),
                )
                .collect::<Vec<_>>();
                let third_tier = third_options
                    .iter()
                    .enumerate()
                    .map(|(position, recommendation)| {
                        let optimization_id = if position == 0 {
                            None
                        } else {
                            Some(format!("third-rank-{}", recommendation.rank))
                        };
                        (
                            recommendation.pool_id.clone(),
                            recommendation.selection_basis.clone(),
                            optimization_id,
                        )
                    })
                    .collect::<Vec<_>>();
                let first_candidates = build_hop_candidates(
                    input,
                    HOP_SIDE_SCOPE_KEY,
                    &first_tier,
                    anchor_token_key_id,
                    y_token,
                    "first",
                )?;
                let middle_candidates = build_hop_candidates(
                    input,
                    HOP_MIDDLE_SCOPE_KEY,
                    &middle_tier,
                    y_token,
                    z_token,
                    "middle",
                )?;
                let third_candidates = build_hop_candidates(
                    input,
                    HOP_SIDE_SCOPE_KEY,
                    &third_tier,
                    z_token,
                    anchor_token_key_id,
                    "third",
                )?;
                // The search over the candidate combinations.
                //
                // The primary combination is always tried first, so a path
                // that passes the gate on its primaries is published exactly
                // as before.  A path that fails may still be published with a
                // backup pool in one of its hops: the backup tier holds the
                // pools the direction layer keeps for that (pair, direction)
                // after the candidate tier was cut.
                let mut selection: Option<(SelectedHop, SelectedHop, SelectedHop)> = None;
                // The backups that were tried and blocked the path.  They are
                // recorded as expired: the pool was used and did not work.
                let mut expired: Vec<(usize, String)> = Vec::new();
                let mut first_failure: Option<String> = None;
                for (i, j, k) in hop_candidate_combinations(
                    first_candidates.len(),
                    middle_candidates.len(),
                    third_candidates.len(),
                ) {
                    let hop_pools = [
                        first_candidates[i].hop_pool.clone(),
                        middle_candidates[j].hop_pool.clone(),
                        third_candidates[k].hop_pool.clone(),
                    ];
                    if hop_pools.iter().any(|pool| pool.is_none()) {
                        // A candidate without a direction row cannot be
                        // traded, so the combination is not a candidate at
                        // all.
                        continue;
                    }
                    let hop_directional_pool_ids = hop_pools
                        .iter()
                        .map(|pool| {
                            pool.as_ref()
                                .expect("the combination was checked above")
                                .directional_pool_id
                                .clone()
                        })
                        .collect::<Vec<_>>();
                    let Some((reason, blocking)) =
                        capacity_gate_verdict(input, &hop_directional_pool_ids)
                    else {
                        selection = Some((
                            SelectedHop {
                                candidates: first_candidates.clone(),
                                chosen: i,
                            },
                            SelectedHop {
                                candidates: middle_candidates.clone(),
                                chosen: j,
                            },
                            SelectedHop {
                                candidates: third_candidates.clone(),
                                chosen: k,
                            },
                        ));
                        break;
                    };
                    if first_failure.is_none() {
                        first_failure = Some(reason.clone());
                    }
                    // The backup that blocked the path is the one that was
                    // tried and did not work.
                    let chosen = [i, j, k];
                    for (hop_index, candidates) in [
                        (0usize, &first_candidates),
                        (1usize, &middle_candidates),
                        (2usize, &third_candidates),
                    ] {
                        let Some(candidate) = candidates.get(chosen[hop_index]) else {
                            continue;
                        };
                        // The gate names the pool that blocked the path by
                        // its **directional** identity, so the candidate is
                        // matched the same way: a candidate that resolves to
                        // no direction row cannot be the blocking one.
                        let candidate_directional_pool_id = candidate
                            .hop_pool
                            .as_ref()
                            .map(|hop_pool| hop_pool.directional_pool_id.as_str());
                        if candidate_directional_pool_id != Some(blocking.as_str()) {
                            continue;
                        }
                        if let Some(optimization_id) = &candidate.optimization_id {
                            expired.push((hop_index + 1, optimization_id.clone()));
                        }
                    }
                }
                let Some((first_hop, middle_hop, third_hop)) = selection else {
                    // No combination passed the gate.  The reason of the
                    // primary combination is the one that is reported, so
                    // the operator sees why the path as published failed.
                    let reason = first_failure
                        .unwrap_or_else(|| "no_candidate_combination_passed".to_owned());
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
                };
                let hop_pools = [
                    first_hop.hop_pool().clone(),
                    middle_hop.hop_pool().clone(),
                    third_hop.hop_pool().clone(),
                ];
                let hop_directional_pool_ids = hop_pools
                    .iter()
                    .map(|hop| hop.directional_pool_id.clone())
                    .collect::<Vec<_>>();
                let measures = measure_path(input, anchor_token_key_id, &hop_directional_pool_ids);
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
                    &first_hop,
                    &middle_hop,
                    &third_hop,
                    &hop_pools,
                    &measures,
                    &expired,
                    &(input.observation.observation_version.max(1)),
                    input.cross_tick_review_threshold_usdg_raw,
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
    first_hop: &SelectedHop,
    middle_hop: &SelectedHop,
    third_hop: &SelectedHop,
    hop_pools: &[HopPool; 3],
    measures: &PathMeasures,
    expired: &[(usize, String)],
    observation_version: &i64,
    cross_tick_review_threshold_usdg_raw: u128,
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
    // The loop holds **directional** pools and nothing else.  The three
    // entries are the pools the hops walk, in hop order, and each of them
    // carries the base pool facts inside its own direction row.
    let directional_pool_ids = hop_pools
        .iter()
        .map(|hop| hop.directional_pool_id.clone())
        .collect::<Vec<_>>();
    let mut pool_path_parts = path_addresses.clone();
    pool_path_parts.extend(directional_pool_ids.iter().cloned());
    let mut hop_candidate_counts = BTreeMap::new();
    hop_candidate_counts.insert("1".to_owned(), first_hop.candidates.len());
    hop_candidate_counts.insert("2".to_owned(), middle_hop.candidates.len());
    hop_candidate_counts.insert("3".to_owned(), third_hop.candidates.len());
    // The backups of a hop are the candidates that are not in effect.  When a
    // backup took over, the primary of the hop is a backup as well: the hop
    // trades a different pool than the one the recommendation named.
    //
    // A backup is named by its **directional** pool id: a candidate without
    // a direction row cannot be traded in the hop's direction, so it is not
    // a backup at all.
    let backups_of = |hop: &SelectedHop| -> Vec<String> {
        hop.candidates
            .iter()
            .enumerate()
            .filter(|(position, _)| *position != hop.chosen)
            .filter_map(|(_, candidate)| {
                candidate
                    .hop_pool
                    .as_ref()
                    .map(|hop_pool| hop_pool.directional_pool_id.clone())
            })
            .collect::<Vec<_>>()
    };
    let first_backups = backups_of(first_hop);
    let middle_backups = backups_of(middle_hop);
    let third_backups = backups_of(third_hop);
    let mut backup_directional_pool_ids = BTreeMap::new();
    backup_directional_pool_ids.insert("first".to_owned(), first_backups);
    backup_directional_pool_ids.insert("middle".to_owned(), middle_backups);
    backup_directional_pool_ids.insert("third".to_owned(), third_backups);
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
        directional_pool_ids,
        backup_directional_pool_ids,
        hop_candidate_counts: hop_candidate_counts.clone(),
        // The count follows the legacy contract: it multiplies the two side
        // hops, the ones the candidate edge publishes.  The middle hop is
        // published by the market observation and is not part of the
        // product, and keeping the definition aligned is what lets the
        // manager rows and the legacy rows be compared field by field.
        hop_candidate_combination_count: first_hop.candidates.len() * third_hop.candidates.len(),
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
        single_tick_capacity_anchor_raw: measures
            .folded_capacity
            .as_ref()
            .map(|(capacity, _)| *capacity),
        single_tick_capacity_binding_directional_pool_id: measures
            .folded_capacity
            .as_ref()
            .map(|(_, binding_pool)| binding_pool.clone()),
        borrow_token_usdg_value_raw: measures.borrow_token_usdg_value_raw,
        borrow_token_decimals: measures.borrow_token_decimals,
        cross_tick_review_threshold_usdg_raw,
        cross_tick_capacity_anchor_raw: measures.cross_tick_capacity_anchor_raw,
    });

    for (hop_index, hop_name, input_token, output_token, hop_pool, basis) in [
        (
            1usize,
            "yc",
            anchor,
            y,
            &hop_pools[0],
            first_hop.chosen_candidate().basis.clone(),
        ),
        (
            2,
            "y-z",
            y,
            z,
            &hop_pools[1],
            middle_hop.chosen_candidate().basis.clone(),
        ),
        (
            3,
            "zc",
            z,
            anchor,
            &hop_pools[2],
            third_hop.chosen_candidate().basis.clone(),
        ),
    ] {
        // The hop carries the pool that is in effect and the pool the
        // recommendation named.  They differ when an optimization took over
        // the hop, and the hop then names the optimization that took over.
        let selected = match hop_index {
            1 => first_hop,
            2 => middle_hop,
            _ => third_hop,
        };
        let chosen = selected.chosen_candidate();
        desired.hops.push(ClosedLoopHopRow {
            loop_key: loop_key.clone(),
            hop_index,
            hop_name: hop_name.to_owned(),
            input_token_key_id: input_token.token_key_id.clone(),
            output_token_key_id: output_token.token_key_id.clone(),
            input_address: address_from_token_key_id(&input_token.token_key_id),
            output_address: address_from_token_key_id(&output_token.token_key_id),
            pool_selection_basis: basis,
            directional_pool_id: hop_pool.directional_pool_id.clone(),
            hop_direction: hop_pool.direction.raw(),
            directional_scope_key: hop_pool.scope_key.clone(),
            directional_pool_rank: hop_pool.rank,
            optimization_id: chosen.optimization_id.clone(),
            temporary_override: chosen.optimization_id.is_some(),
        });
    }

    // The optimization rows of the three hops.
    //
    // Every candidate that is not the primary of its hop is an optimization
    // the hop could take.  The row carries the **state** of that
    // optimization: `candidate` while it waits, `temporary` while it is in
    // effect, and `expired` once it was tried and blocked the path.  The
    // state is what makes the table a consumed table rather than a written
    // one.
    for (hop_index, hop) in [
        (1usize, first_hop),
        (2usize, middle_hop),
        (3usize, third_hop),
    ] {
        let Some(original_directional_pool_id) = hop.primary_directional_pool_id() else {
            // The primary of the hop has no direction row, so the hop cannot
            // be traded at all and there is nothing to optimize.
            continue;
        };
        let original_directional_pool_id = original_directional_pool_id.to_owned();
        for (position, candidate) in hop.candidates.iter().enumerate() {
            if position == 0 {
                continue;
            }
            let Some(optimization_id) = candidate.optimization_id.clone() else {
                continue;
            };
            // An optimization that has no direction row cannot be traded, so
            // it is not an optimization of this hop.
            let Some(optimized_directional_pool_id) = candidate
                .hop_pool
                .as_ref()
                .map(|hop_pool| hop_pool.directional_pool_id.clone())
            else {
                continue;
            };
            let status = if position == hop.chosen {
                // The optimization is in effect: the hop trades this pool
                // instead of the pool the recommendation named.
                "temporary"
            } else if expired
                .iter()
                .any(|(expired_hop, expired_id)| {
                    *expired_hop == hop_index && expired_id == &optimization_id
                })
            {
                // The optimization was tried and did not pass the gate.
                "expired"
            } else {
                "candidate"
            };
            desired.optimizations.push(ClosedLoopOptimizationRow {
                loop_key: loop_key.clone(),
                hop_index,
                optimization_id,
                original_directional_pool_id: original_directional_pool_id.clone(),
                optimized_directional_pool_id,
                optimization_basis: candidate.basis.clone(),
                expected_price_improvement: None,
                status: status.to_owned(),
                expires_at: None,
            });
        }
    }
    desired.runtime_states.push(ClosedLoopRuntimeStateRow {
        loop_key,
        runtime_status: "ready_for_review".to_owned(),
        status_reason: None,
        review_generation: 1,
        review_required: true,
        last_rebuilt_at: chrono::Utc::now().to_rfc3339(),
        last_reviewed_at: None,
        next_review_at: None,
        review_wait_seconds: None,
        review_waited_seconds: None,
        review_cycle_started_at: None,
    });
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
    let (directional_pool_version, directional_pool_checksum) =
        directional_layer_identity(&input.directional_scope_states);
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
        directional_pool_version,
        directional_pool_checksum: &directional_pool_checksum,
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
    let connection = Connection::open_with_flags(path, flags)?;
    if writable {
        // A writer **waits** for the lock instead of failing at once.
        // The runtime measures a loop in one connection while the Manager
        // writes the result in another, and the two meet on the same file.
        // Without the wait the second one reports "database is locked" the
        // moment the first one holds the lock, and the request is lost --
        // a manual review that never starts, for no reason but a lock.
        connection.busy_timeout(std::time::Duration::from_secs(60))?;
    }
    Ok(connection)
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
                    , directional_pool_version, directional_pool_checksum
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
                    row.get::<_, i64>(15)?,
                    row.get::<_, String>(16)?,
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
        directional_pool_version,
        directional_pool_checksum,
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
        directional_pool_version: Some(directional_pool_version),
        directional_pool_checksum: Some(directional_pool_checksum),
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
        pending_optimizations: read_pending_optimizations(
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
                backup_directional_pool_ids_json, hop_candidate_counts_json,
                hop_candidate_combination_count, recommendation_basis,
                recommended_first_priority, candidate_edge_version,
                candidate_edge_checksum, observation_version,
                observation_checksum, status, source,
                single_tick_capacity_anchor_raw,
                single_tick_capacity_binding_directional_pool_id,
                borrow_token_usdg_value_raw,
                borrow_token_decimals,
                cross_tick_review_threshold_usdg_raw,
                cross_tick_capacity_anchor_raw,
                directional_pool_ids_json
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
                backup_directional_pool_ids: serde_json::from_str(&row.get::<_, String>(11)?)
                    .unwrap_or_default(),
                hop_candidate_counts: serde_json::from_str(&row.get::<_, String>(12)?)
                    .unwrap_or_default(),
                hop_candidate_combination_count: usize_from_i64(row.get(13)?),
                recommendation_basis: row.get(14)?,
                recommended_first_priority: row.get::<_, i64>(15)? != 0,
                candidate_edge_version: row.get(16)?,
                candidate_edge_checksum: row.get(17)?,
                observation_version: row.get(18)?,
                observation_checksum: row.get(19)?,
                status: row.get(20)?,
                source: row.get(21)?,
                single_tick_capacity_anchor_raw: row
                    .get::<_, Option<String>>(22)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                single_tick_capacity_binding_directional_pool_id: row
                    .get::<_, Option<String>>(23)?
                    .map(|value| value.trim().to_owned())
                    .filter(|value| !value.is_empty()),
                borrow_token_usdg_value_raw: row
                    .get::<_, Option<String>>(24)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                borrow_token_decimals: row
                    .get::<_, Option<i64>>(25)?
                    .and_then(|value| u32::try_from(value).ok()),
                cross_tick_review_threshold_usdg_raw: row
                    .get::<_, Option<String>>(26)?
                    .and_then(|value| value.trim().parse::<u128>().ok())
                    .unwrap_or(0),
                cross_tick_capacity_anchor_raw: row
                    .get::<_, Option<String>>(27)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                // The column was added by migration `20261124_016`, so a row
                // that predates it 鈥?or a fixture that never wrote it 鈥?
                // carries NULL.  Reading it as a plain `String` turns that
                // NULL into a hard error, which is exactly what the
                // nullable columns around it already avoid.
                directional_pool_ids: row
                    .get::<_, Option<String>>(28)?
                    .and_then(|value| serde_json::from_str(&value).ok())
                    .unwrap_or_default(),
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
                h.pool_selection_basis, h.optimization_id, h.temporary_override,
                h.directional_pool_id, h.hop_direction,
                h.directional_scope_key, h.directional_pool_rank
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
                pool_selection_basis: row.get(7)?,
                optimization_id: row.get(8)?,
                temporary_override: row.get::<_, i64>(9)? != 0,
                directional_pool_id: row.get(10)?,
                hop_direction: row.get(11)?,
                directional_scope_key: row.get(12)?,
                directional_pool_rank: row.get(13)?,
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
    // Only the optimizations that are **in effect** are consumed: a
    // `temporary` one replaced the pool of its hop and an `accepted` one was
    // adopted for good.  A `candidate` is still waiting and an `expired` one
    // was tried and did not pass the gate, so neither of them describes a
    // hop the loop actually trades.
    let mut statement = connection.prepare(
        "SELECT o.loop_key, o.hop_index, o.optimization_id,
                o.original_directional_pool_id, o.optimized_directional_pool_id,
                o.optimization_basis,
                o.expected_price_improvement, o.status, o.expires_at
         FROM manager_v4_closed_loop_optimizations o
         JOIN manager_v4_closed_loops l
           ON l.network = o.network AND l.chain_id = o.chain_id
          AND l.protocol = o.protocol AND l.scope_key = o.scope_key
          AND l.loop_key = o.loop_key
         WHERE o.network = ?1 AND o.chain_id = ?2 AND o.protocol = ?3
           AND o.scope_key = ?4
           AND o.status IN ('temporary', 'accepted')
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
                original_directional_pool_id: row.get(3)?,
                optimized_directional_pool_id: row.get(4)?,
                optimization_basis: row.get(5)?,
                expected_price_improvement: row.get(6)?,
                status: row.get(7)?,
                expires_at: row.get(8)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// The optimizations that are still waiting to be taken.
///
/// They are the standby pool of every hop: the pool the hop would trade if
/// the capacity gate disqualified the pool it uses now.
fn read_pending_optimizations(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<ClosedLoopOptimizationRow>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT o.loop_key, o.hop_index, o.optimization_id,
                o.original_directional_pool_id, o.optimized_directional_pool_id,
                o.optimization_basis,
                o.expected_price_improvement, o.status, o.expires_at
         FROM manager_v4_closed_loop_optimizations o
         JOIN manager_v4_closed_loops l
           ON l.network = o.network AND l.chain_id = o.chain_id
          AND l.protocol = o.protocol AND l.scope_key = o.scope_key
          AND l.loop_key = o.loop_key
         WHERE o.network = ?1 AND o.chain_id = ?2 AND o.protocol = ?3
           AND o.scope_key = ?4
           AND o.status = 'candidate'
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
                original_directional_pool_id: row.get(3)?,
                optimized_directional_pool_id: row.get(4)?,
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
                , r.last_reviewed_at, r.next_review_at, r.review_cycle_started_at
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
                last_reviewed_at: row.get(6)?,
                next_review_at: row.get(7)?,
                review_wait_seconds: row
                    .get::<_, Option<String>>(7)?
                    .as_deref()
                    .map(seconds_until_moment),
                review_waited_seconds: row
                    .get::<_, Option<String>>(7)?
                    .as_deref()
                    .map(seconds_since_moment),
                review_cycle_started_at: row.get(8)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// The period after which the review of a closed loop goes stale, in seconds.
///
/// A review is a **measurement**: it describes the market of the moment it
/// was taken, so it goes stale and the loop has to be measured again.  The
/// interval is how long a measurement is trusted.
///
/// The default is one minute: the broadcast gate only accepts a review that
/// is at most two minutes old, so a loop that is measured less often than
/// that could never be traded on a fresh measurement.
pub const DEFAULT_REVIEW_INTERVAL_SECONDS: i64 = 60;

/// The environment variable that overrides the review interval.
pub const REVIEW_INTERVAL_SECONDS_ENV: &str = "CLOSED_LOOP_REVIEW_INTERVAL_SECONDS";

/// The namespace of the parameter page the review interval lives on.
///
/// The interval is a **configuration parameter**, so it is read from the
/// parameter store of the running system and not from the environment of
/// the process: changing it does not need a restart.
pub const REVIEW_INTERVAL_PARAMETER_NAMESPACE: &str = "strategy_config";

/// The key of the review interval inside the parameter page.
///
/// The interval is **one** parameter: it is the automatic review interval
/// of every closed loop and at the same time the length of the review cycle
/// of a loop -- the time between the start of a review round and the end of
/// the round (a confirmed broadcast, or a review that found no profit).
pub const REVIEW_INTERVAL_PARAMETER_KEY: &str = "CLOSED_LOOP_REVIEW_INTERVAL_SECONDS";

/// The spacing of the review time points of a fresh catalog, in seconds.
///
/// The loops of a fresh catalog do not all become due in the same
/// second: their review time points are spread by this spacing, so the
/// first round of the reviews does not hit the RPC provider with every
/// loop at once.
pub const DEFAULT_REVIEW_START_SPACING_SECONDS: i64 = 2;

/// The key of the spacing of the review time points inside the parameter
/// page.
pub const REVIEW_START_SPACING_PARAMETER_KEY: &str =
    "CLOSED_LOOP_REVIEW_START_SPACING_SECONDS";

/// The state of a closed loop that has to be measured again.
///
/// A loop in this state is **due**: the measurement it needs is missing, so
/// the next round of the review has to start.  This is the review state of
/// the loop.
pub const RUNTIME_STATUS_READY_FOR_REVIEW: &str = "ready_for_review";

/// The state of a closed loop whose review clock is running.
///
/// The loop has been dealt with -- a broadcast confirmed its profit, or a
/// review found no profit at all -- and its clock counts down to the moment
/// it is due again.  The remaining seconds are published in
/// `review_wait_seconds`.
///
/// The name is the one the schema gives the state: the loop is cooling
/// down until the clock of its next round runs out.
pub const RUNTIME_STATUS_COOLDOWN: &str = "cooldown";

/// The state of a closed loop whose review round is running.
///
/// The loop claimed its round: the review is being measured right now,
/// and the clock of the cycle is already running.  The claim is what
/// keeps two workers from measuring the same loop at the same time.
pub const RUNTIME_STATUS_REVIEW_PENDING: &str = "review_pending";

/// The state of a closed loop that waits for the receipt of its trade.
///
/// The loop traded: its review found a profit and the transaction was
/// sent.  What is left is the answer of the chain, and the chain is
/// asked by the broadcast confirmation, not by the review.  So the loop
/// has **no** scheduled review while it waits, and the moment it is
/// scheduled at says so.
pub const RUNTIME_STATUS_BROADCAST_PENDING: &str = "broadcast_pending";

/// The moment a loop that is **being reviewed** is parked at.
///
/// The loop has no scheduled review while its round runs: the round
/// ends with a measurement, and the moment it ends is when the next
/// round is scheduled.  So the loop is parked at a moment far ahead,
/// and the moment is a **marker** that tells the two busy situations
/// apart.
///
/// The marker is a **specific** moment and not NULL: NULL is taken by
/// "the loop was never measured", which means the very opposite.
pub const REVIEW_PENDING_SENTINEL_AT: &str = "2036-06-06T06:06:00+00:00";

/// The seconds since the epoch of the review sentinel.
///
/// The value fits an `i32` -- it is below the limit of 2038-01-19 --
/// so a reader that counts in `i32` does not overflow on it.
pub const REVIEW_PENDING_SENTINEL_EPOCH: i64 = 2_096_345_160;

/// The moment a loop that **waits for its receipt** is parked at.
///
/// The loop traded and the chain has not answered yet, so the loop has
/// no scheduled review either.  It is parked at a moment of its own,
/// so the queue tells the two busy situations apart without reading
/// the state of the loop.
pub const BROADCAST_PENDING_SENTINEL_AT: &str = "2035-05-05T05:05:00+00:00";

/// The seconds since the epoch of the broadcast sentinel.
///
/// The value fits an `i32` -- it is below the limit of 2038-01-19 --
/// so a reader that counts in `i32` does not overflow on it.
pub const BROADCAST_PENDING_SENTINEL_EPOCH: i64 = 2_061_954_300;

/// The shape of a moment the store writes.
///
/// A scheduled moment is written in **RFC 3339**, so it starts with the
/// date and carries the `T` that separates it from the time.  SQLite
/// reads a few shapes more than that -- `2026-11-27 17:10:35` is a date
/// for it -- while the store reads **RFC 3339 only**, so a moment that is
/// not written in that shape would make the two sides disagree about
/// when a loop is due.  The shape is therefore checked **explicitly**: a
/// moment that is not written in it is **due now**, on both sides.
pub const RFC3339_MOMENT_GLOB: &str = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*";

/// Whether a moment is one of the two sentinels.
///
/// A sentinel is a marker, so it is **excluded** from the due set on
/// its own: the loop it belongs to has no scheduled review, no matter
/// how far ahead the marker is.
pub fn is_review_sentinel(moment: &str) -> bool {
    let moment = moment.trim();
    moment == REVIEW_PENDING_SENTINEL_AT || moment == BROADCAST_PENDING_SENTINEL_AT
}

/// The sentinel of a state that has no scheduled review.
///
/// A state that is not listed here has a scheduled review, so it is
/// scheduled at a real moment.
pub fn review_sentinel_for_status(status: &str) -> Option<&'static str> {
    match status {
        RUNTIME_STATUS_REVIEW_PENDING => Some(REVIEW_PENDING_SENTINEL_AT),
        RUNTIME_STATUS_BROADCAST_PENDING => Some(BROADCAST_PENDING_SENTINEL_AT),
        _ => None,
    }
}

/// Whether a loop in this state may start a round of the review.
///
/// The state decides whether a loop is **work**: a loop that waits for
/// its clock and a loop that is due may start a round, and every other
/// state is either busy or out of the game.
pub fn is_claimable_status(status: &str) -> bool {
    status == RUNTIME_STATUS_READY_FOR_REVIEW || status == RUNTIME_STATUS_COOLDOWN
}

/// The state of a closed loop that is not traded any more.
///
/// A retired loop is not claimed and not measured: it keeps the state it
/// was retired with.
pub const RUNTIME_STATUS_RETIRED: &str = "retired";

/// The review interval the store uses, in seconds.
///
/// The interval is a parameter and never a gate: a shorter interval makes
/// the store measure a loop more often, it does not refuse anything.
///
/// The interval belongs to the **one** place that schedules a closed
/// loop: [`reset_review_start_on_connection`].  Nothing else may read
/// it.  A judgement that counted an age -- "the review is older than
/// the interval" -- would count the time the review itself took, and a
/// review that took a minute to measure would look older than a review
/// that took a second, although both are the current measurement of
/// their loop.  Every other question is answered by **moments**: the
/// moment the loop is scheduled at and the moment its round started.
fn review_interval_seconds() -> i64 {
    std::env::var(REVIEW_INTERVAL_SECONDS_ENV)
        .ok()
        .and_then(|value| value.trim().parse::<i64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_REVIEW_INTERVAL_SECONDS)
}

/// The review interval the store uses, read from the configuration
/// parameters.
///
/// The parameter store lives in the same file as the closed loops, so the
/// interval is read from there and every loop picks a new value up on its
/// next cycle.  The environment variable is the fallback for a store that
/// carries no parameter yet, and the default is the fallback for both.
fn review_interval_seconds_on_connection(connection: &Connection) -> i64 {
    read_parameter_seconds(
        connection,
        REVIEW_INTERVAL_PARAMETER_NAMESPACE,
        REVIEW_INTERVAL_PARAMETER_KEY,
    )
    .unwrap_or_else(review_interval_seconds)
}

/// The spacing of the review time points of a fresh catalog.
///
/// The spacing is a parameter like the interval itself, so it is read
/// from the store the closed loops live in and a changed value is in
/// force the next time a catalog is published.
pub fn review_start_spacing_seconds_on_connection(connection: &Connection) -> i64 {
    read_parameter_seconds(
        connection,
        REVIEW_INTERVAL_PARAMETER_NAMESPACE,
        REVIEW_START_SPACING_PARAMETER_KEY,
    )
    .unwrap_or(DEFAULT_REVIEW_START_SPACING_SECONDS)
}

/// Read one numeric parameter out of the parameter store.
///
/// The parameter store keeps every value as a JSON document, and the value
/// itself is carried inside that document as a JSON text, so a parameter is
/// read in two steps: the document, then the value it carries.  A store that
/// does not carry the parameter at all answers with nothing, and the caller
/// falls back to its own default.
fn read_parameter_seconds(
    connection: &Connection,
    namespace: &str,
    key: &str,
) -> Option<i64> {
    let stored: Option<String> = connection
        .query_row(
            "SELECT value_json FROM kv_entries
              WHERE collection = 'parameters' AND key = ?1",
            params![format!("{namespace}|{key}")],
            |row| row.get(0),
        )
        .ok();
    let document = stored?;
    let parsed: serde_json::Value = serde_json::from_str(&document).ok()?;
    let raw = parsed.get("value_json")?;
    let value = match raw {
        serde_json::Value::String(text) => serde_json::from_str::<serde_json::Value>(text).ok()?,
        other => other.clone(),
    };
    let seconds = value.as_i64()?;
    if seconds > 0 {
        Some(seconds)
    } else {
        None
    }
}

/// Claim the review round of a closed loop.
///
/// The claim starts the round of the review of the loop: the moment the
/// round starts is written down, and the loop waits for the round to end.
/// The round that ends resets the review start time of the loop, so the
/// moment written here is the one the loop falls back on if the round
/// never ends at all.
///
/// The claim is also the lock of the loop: a loop whose round is already
/// running is not claimed a second time, so two workers never measure
/// the same loop at the same time.  A loop that still waits for its
/// clock is not claimed either -- it is not due.
///
/// The answer tells whether the round was started: `false` means the
/// loop was not due, or that it is already being measured.
pub fn begin_review_cycle_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    reason: &str,
) -> Result<bool, ClosedLoopError> {
    let row: Option<(String, Option<String>)> = connection
        .query_row(
            "SELECT runtime_status, next_review_at
               FROM manager_v4_closed_loop_runtime_state
              WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                AND scope_key = ?4 AND loop_key = ?5",
            params![network, chain_id, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY, loop_key],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .ok();
    let Some((status, due_at)) = row else {
        return Ok(false);
    };
    if status == RUNTIME_STATUS_REVIEW_PENDING {
        // The round of this loop is already running.
        return Ok(false);
    }
    if status == RUNTIME_STATUS_BROADCAST_PENDING {
        // The loop traded and waits for the receipt of its transaction.
        return Ok(false);
    }
    if due_at.as_deref().is_some_and(is_review_sentinel) {
        // The loop is parked at a sentinel: it is either being measured
        // or it waits for the receipt of its trade.  Either way it has
        // no round to start, whatever its state says.
        return Ok(false);
    }
    if status == RUNTIME_STATUS_RETIRED {
        return Ok(false);
    }
    if status == RUNTIME_STATUS_COOLDOWN {
        let remaining = due_at.as_deref().map(remaining_review_seconds).unwrap_or(0);
        if remaining > 0 {
            // The clock of the loop still runs, so the loop is not due.
            return Ok(false);
        }
    }
    let now = chrono::Utc::now();
    let updated = connection.execute(
        "UPDATE manager_v4_closed_loop_runtime_state
            SET runtime_status = ?5, status_reason = ?6,
                review_cycle_started_at = ?7, next_review_at = ?8,
                review_required = 0, updated_at = ?9
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4 AND loop_key = ?10",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            RUNTIME_STATUS_REVIEW_PENDING,
            reason,
            now.to_rfc3339(),
            REVIEW_PENDING_SENTINEL_AT,
            now.to_rfc3339(),
            loop_key
        ],
    )?;
    Ok(updated > 0)
}

/// Record that a closed loop was measured.
///
/// The write ends the review round of the loop: the measurement is kept
/// and the review start time of the loop is reset, so the next round of
/// the review starts one review interval after the **round ended** -- not
/// after the measurement.  The time the round itself took is not time
/// the loop waits.
pub fn record_review_schedule_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    reviewed_at: &str,
    reason: &str,
) -> Result<bool, ClosedLoopError> {
    reset_review_start_on_connection(
        connection,
        network,
        chain_id,
        loop_key,
        ReviewStartReset::AfterRound,
        Some(reviewed_at),
        reason,
    )
}

/// Mark that a closed loop has to be measured again.
///
/// A loop whose review went stale has no fresh measurement, so the store
/// says so: the loop waits for a review, and the moment it is scheduled
/// at is **now** -- the round of the review may start at once.
///
/// A manual review asks for the same thing: the review time point of the
/// loop is the moment the request was made, so the loop is due right away
/// and the round of the review starts with it.
pub fn mark_review_required_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
) -> Result<bool, ClosedLoopError> {
    reset_review_start_on_connection(
        connection,
        network,
        chain_id,
        loop_key,
        ReviewStartReset::Manual,
        None,
        "review_required",
    )
}

/// How the review start time of a closed loop is reset.
///
/// The loop keeps **one** time point: the moment its next round of the
/// review may start.  The main thread of the review compares that moment
/// with the clock and starts the round of the loop when the moment has
/// come.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReviewStartReset {
    /// The end of a normal round: a confirmed broadcast, or a review that
    /// found no profit.
    ///
    /// The round is over, so the next round starts one review interval
    /// after the moment the round ended.
    AfterRound,
    /// A manual review.
    ///
    /// The review was asked for by hand, so the next round starts at once.
    Manual,
    /// A loop that traded and waits for the receipt of its transaction.
    ///
    /// The loop has no scheduled review while it waits, so it is parked
    /// at the broadcast sentinel.
    BroadcastPending,
}

/// Reset the review start time of a closed loop.
///
/// This is the one place the review start time of a loop is set, so the
/// state machine has a single rule for it:
///
/// * the end of a normal round schedules the next round one review
///   interval later, and
/// * a manual review schedules it at once.
/// * a loop that waits for its receipt is parked at the broadcast
///   sentinel, because it has no scheduled review at all.
///
/// The loop keeps no timer of its own: the moment that is written here is
/// the moment the main thread of the review compares with the clock, and
/// everything an operator sees -- how long the loop still waits, how long
/// it waited already -- is counted from that moment when it is read.
pub fn reset_review_start_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    reset: ReviewStartReset,
    last_reviewed_at: Option<&str>,
    reason: &str,
) -> Result<bool, ClosedLoopError> {
    let interval = review_interval_seconds_on_connection(connection);
    let now = chrono::Utc::now();
    let (start_at, status, required) = match reset {
        ReviewStartReset::AfterRound => (
            (now + chrono::Duration::seconds(interval)).to_rfc3339(),
            RUNTIME_STATUS_COOLDOWN,
            0,
        ),
        ReviewStartReset::Manual => (
            now.to_rfc3339(),
            RUNTIME_STATUS_READY_FOR_REVIEW,
            1,
        ),
        ReviewStartReset::BroadcastPending => (
            BROADCAST_PENDING_SENTINEL_AT.to_owned(),
            RUNTIME_STATUS_BROADCAST_PENDING,
            0,
        ),
    };
    let updated = connection.execute(
        "UPDATE manager_v4_closed_loop_runtime_state
            SET runtime_status = ?5, status_reason = ?6,
                next_review_at = ?7, review_required = ?8,
                last_reviewed_at = COALESCE(?9, last_reviewed_at),
                updated_at = ?10
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4 AND loop_key = ?11",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            status,
            reason,
            start_at,
            required,
            last_reviewed_at.map(|value| value.to_owned()),
            now.to_rfc3339(),
            loop_key
        ],
    )?;
    Ok(updated > 0)
}

/// Reset the review start time of a closed loop in a store.
///
/// The wrapper opens the store and does the write, so a caller that only
/// knows the path -- the runtime, for one -- does not have to open a
/// connection of its own and does not have to know how the store is
/// opened.  The rule the write follows is the one of
/// [`reset_review_start_on_connection`]: this function only opens the
/// door.
pub fn reset_review_start(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    reset: ReviewStartReset,
    last_reviewed_at: Option<&str>,
    reason: &str,
) -> Result<bool, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), true)?;
    reset_review_start_on_connection(
        &connection,
        network,
        chain_id,
        loop_key,
        reset,
        last_reviewed_at,
        reason,
    )
}

/// The runtime status of one closed loop, as the store holds it.
///
/// The status is read before a loop is released, so the caller knows
/// whether the loop waits for the receipt of its trade -- it is parked at
/// the broadcast sentinel -- or whether the round that measured it has
/// already scheduled the next one.  A loop the store does not know reads
/// as `None`.
pub fn read_runtime_status_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
) -> Result<Option<String>, ClosedLoopError> {
    let status = connection
        .query_row(
            "SELECT runtime_status
               FROM manager_v4_closed_loop_runtime_state
              WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                AND scope_key = ?4 AND loop_key = ?5",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                loop_key
            ],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    Ok(status)
}

/// The runtime status of one closed loop, read from a store.
///
/// The wrapper opens the store and does the read, so a caller that only
/// knows the path does not have to open a connection of its own.  The store
/// is opened the way [`reset_review_start`] opens it, so the reader and the
/// writer always see the same file.
pub fn read_runtime_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    loop_key: &str,
) -> Result<Option<String>, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), true)?;
    read_runtime_status_on_connection(&connection, network, chain_id, loop_key)
}

/// Start the review clock of a closed loop.
///
/// The clock is the wait between two review rounds of a loop.  It is
/// started when the loop has been dealt with: a broadcast that confirmed a
/// profit, or a review that found no profit at all.  From that moment the
/// loop waits, and when the wait is over the loop is due again and the
/// next round of the review starts.
///
/// The clock publishes the moment the loop is due again and the number of
/// seconds that are left, so the state of the loop says how long it still
/// waits.
pub fn start_review_clock_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    reason: &str,
) -> Result<bool, ClosedLoopError> {
    reset_review_start_on_connection(
        connection,
        network,
        chain_id,
        loop_key,
        ReviewStartReset::AfterRound,
        None,
        reason,
    )
}

/// Advance the review clock of a closed loop.
///
/// The clock is advanced against the moment the loop is due again.  While
/// that moment is in the future the loop keeps waiting and the remaining
/// seconds are published; when the moment has come the loop is due again:
/// its state becomes the review state and it waits for the next round of the
/// review.
///
/// The remaining seconds are returned; zero means the loop is due.
pub fn advance_review_clock_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
) -> Result<i64, ClosedLoopError> {
    let row: Option<(String, Option<String>)> = connection
        .query_row(
            "SELECT runtime_status, next_review_at
               FROM manager_v4_closed_loop_runtime_state
              WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                AND scope_key = ?4 AND loop_key = ?5",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                loop_key
            ],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .ok();
    let Some((status, due_at)) = row else {
        return Ok(0);
    };
    if due_at.as_deref().is_some_and(is_review_sentinel) {
        // The loop is parked at a sentinel: it is being measured, or it
        // waits for the receipt of its trade.  It has no clock, so there
        // is no wait to publish.
        return Ok(0);
    }
    if status != RUNTIME_STATUS_COOLDOWN && status != RUNTIME_STATUS_REVIEW_PENDING {
        // A loop that does not wait has no clock to advance: it is due
        // already, or it was never measured.
        return Ok(0);
    }
    let remaining = due_at
        .as_deref()
        .map(remaining_review_seconds)
        .unwrap_or(0);
    let now = chrono::Utc::now();
    if remaining > 0 {
        // The loop still waits, and its wait is counted from the moment
        // it is scheduled at: there is nothing to write, so no row of the
        // scope is touched in this pass.
        return Ok(remaining);
    }
    // The clock ran out: the loop is due again, so its state becomes the
    // review state and the next round of the review may start.  A round
    // that was still running when its moment came did not finish at all,
    // so the loop is measured again.  The moment the loop was scheduled
    // at stays: it says how long the loop waited, and the queue of the
    // due loops is ordered by it.
    connection.execute(
        "UPDATE manager_v4_closed_loop_runtime_state
            SET runtime_status = ?5, status_reason = ?6,
                review_required = 1, review_cycle_started_at = NULL,
                updated_at = ?7
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4 AND loop_key = ?8",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            RUNTIME_STATUS_READY_FOR_REVIEW,
            "review_clock_elapsed",
            now.to_rfc3339(),
            loop_key
        ],
    )?;
    Ok(0)
}

/// Advance the review clocks of every closed loop of a scope.
///
/// The number of loops that became due is returned, so the caller can see
/// how many review rounds the scope is waiting for.
///
/// The pass is **one** statement: the loops whose review time point has
/// come are turned into the review state in one write, and the loops that
/// still wait are not touched at all.  A pass that walks the loops one by
/// one would write a row for every loop of the scope on every round of the
/// healer, and the loop keeps no timer of its own, so there is nothing to
/// refresh.
pub fn advance_review_clocks_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<usize, ClosedLoopError> {
    let now = chrono::Utc::now().to_rfc3339();
    let updated = connection.execute(
        "UPDATE manager_v4_closed_loop_runtime_state
            SET runtime_status = ?5, status_reason = ?6,
                review_required = 1, review_cycle_started_at = NULL,
                updated_at = ?7
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4
            AND runtime_status IN (?8, ?9)
            AND next_review_at IS NOT NULL
            AND next_review_at NOT IN (?10, ?11)
            AND (next_review_at NOT GLOB ?12
                 OR strftime('%s', next_review_at) IS NULL
                 OR CAST(strftime('%s', next_review_at) AS INTEGER)
                    <= CAST(strftime('%s', ?7) AS INTEGER))",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            RUNTIME_STATUS_READY_FOR_REVIEW,
            "review_clock_elapsed",
            now,
            RUNTIME_STATUS_COOLDOWN,
            RUNTIME_STATUS_REVIEW_PENDING,
            REVIEW_PENDING_SENTINEL_AT,
            BROADCAST_PENDING_SENTINEL_AT,
            RFC3339_MOMENT_GLOB,
        ],
    )?;
    Ok(updated)
}

/// The seconds that are left until a loop is due again.
fn remaining_review_seconds(next_review_at: &str) -> i64 {
    seconds_until_moment(next_review_at)
}

/// The seconds between now and a moment.
///
/// The wait of a loop is **counted** from the moment the loop is
/// scheduled at: the loop keeps no timer of its own, so the distance
/// between that moment and now is what the loop still waits.
fn seconds_until_moment(moment: &str) -> i64 {
    let due = match chrono::DateTime::parse_from_rfc3339(moment.trim()) {
        Ok(moment) => moment.with_timezone(&chrono::Utc),
        Err(_) => return 0,
    };
    let remaining = (due - chrono::Utc::now()).num_seconds();
    if remaining > 0 {
        remaining
    } else {
        0
    }
}

/// The seconds between a moment and now.
///
/// The wait a loop spent already is counted from the same moment: it
/// says how long the loop waited for a round that did not start yet.
fn seconds_since_moment(moment: &str) -> i64 {
    let due = match chrono::DateTime::parse_from_rfc3339(moment.trim()) {
        Ok(moment) => moment.with_timezone(&chrono::Utc),
        Err(_) => return 0,
    };
    let waited = (chrono::Utc::now() - due).num_seconds();
    if waited > 0 {
        waited
    } else {
        0
    }
}

/// The review clock of one closed loop.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopReviewClock {
    /// The loop the clock belongs to.
    pub loop_key: String,
    /// The state of the loop: the review state, or the state of a loop
    /// whose clock is running.
    pub runtime_status: String,
    /// Whether the loop waits for a review.
    pub review_required: bool,
    /// The seconds the clock of the loop still has to run.
    ///
    /// The wait is **counted** from the moment the loop is scheduled at:
    /// the loop keeps no timer of its own, so the seconds that are left
    /// are the distance between the moment the next round may start and
    /// now.  Zero means the loop waits for nothing: it is due.
    pub review_wait_seconds: Option<i64>,
    /// The seconds the loop waited already beyond its scheduled moment.
    ///
    /// The scheduled moment is the one the loop is measured against, so
    /// the distance between it and now says how long the loop waited for
    /// a round that did not start yet.
    pub review_waited_seconds: Option<i64>,
    /// The moment the loop is due again.
    pub next_review_at: Option<String>,
    /// The moment the review cycle of the loop started.
    ///
    /// The cycle is the round of the review: it starts when the round is
    /// claimed and it is as long as the automatic review interval.  The
    /// moment it started decides when the next round is due.
    pub review_cycle_started_at: Option<String>,
}

/// Read the review clock of every closed loop of a scope.
pub fn read_review_clocks(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<Vec<ClosedLoopReviewClock>, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    let mut statement = connection.prepare(
        "SELECT loop_key, runtime_status, review_required,
                next_review_at, review_cycle_started_at
         FROM manager_v4_closed_loop_runtime_state
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND scope_key = ?4
         ORDER BY loop_key",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY],
        |row| {
            Ok(ClosedLoopReviewClock {
                loop_key: row.get(0)?,
                runtime_status: row.get(1)?,
                review_required: row.get::<_, i64>(2)? != 0,
                review_wait_seconds: row
                    .get::<_, Option<String>>(3)?
                    .as_deref()
                    .map(seconds_until_moment),
                review_waited_seconds: row
                    .get::<_, Option<String>>(3)?
                    .as_deref()
                    .map(seconds_since_moment),
                next_review_at: row.get(3)?,
                review_cycle_started_at: row.get(4)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// One closed loop that is due for a round of the review.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopReviewDue {
    /// The loop that is due.
    pub loop_key: String,
    /// The state of the loop.
    pub runtime_status: String,
    /// The moment the loop was scheduled at.
    ///
    /// This is the one time point the loop keeps: the main thread of the
    /// review compares it with the clock, and the queue of the due loops
    /// is ordered by it.
    pub next_review_at: Option<String>,
    /// The seconds the loop waited already beyond its scheduled moment.
    pub review_waited_seconds: i64,
    /// The moment the round of the review started.
    pub review_cycle_started_at: Option<String>,
}

/// Read the closed loops of a scope that are due for a round of the review.
///
/// The queue is the work list of the main thread of the review: it holds
/// every loop whose scheduled moment has come, ordered by that moment, so
/// the loop that waited the longest comes first and the loops do not all
/// start in the same second.
///
/// A loop whose round is still running is not in the queue: it is being
/// measured, so it must not be measured a second time.
pub fn read_due_review_loops(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<Vec<ClosedLoopReviewDue>, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    let now = chrono::Utc::now().to_rfc3339();
    let now_epoch = chrono::Utc::now().timestamp();
    let mut statement = connection.prepare(
        "SELECT loop_key, runtime_status, next_review_at, review_cycle_started_at
         FROM manager_v4_closed_loop_runtime_state
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND scope_key = ?4
           AND runtime_status IN ('ready_for_review', 'cooldown')
           AND (next_review_at IS NULL
                OR (next_review_at NOT IN (?6, ?7)
                    AND (next_review_at NOT GLOB ?8
                         OR strftime('%s', next_review_at) IS NULL
                         OR CAST(strftime('%s', next_review_at) AS INTEGER)
                            <= CAST(strftime('%s', ?5) AS INTEGER))))
         ORDER BY CASE WHEN next_review_at GLOB ?8
                       THEN CAST(strftime('%s', next_review_at) AS INTEGER)
                       ELSE -9223372036854775808 END ASC, loop_key ASC",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            now,
            REVIEW_PENDING_SENTINEL_AT,
            BROADCAST_PENDING_SENTINEL_AT,
            RFC3339_MOMENT_GLOB
        ],
        |row| {
            let next_review_at: Option<String> = row.get(2)?;
            Ok(ClosedLoopReviewDue {
                loop_key: row.get(0)?,
                runtime_status: row.get(1)?,
                review_waited_seconds: next_review_at
                    .as_deref()
                    .and_then(moment_epoch)
                    .map(|due| now_epoch.saturating_sub(due).max(0))
                    .unwrap_or(0),
                next_review_at,
                review_cycle_started_at: row.get(3)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// The number of closed loops of a scope that still wait for their round.
///
/// The count is what the queue of the due loops leaves out: the main
/// thread of the review walks the queue and stops at the first loop that
/// is not due, so the loops that still wait are never looked at.
pub fn count_waiting_review_loops(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<usize, ClosedLoopError> {
    let queue = read_review_queue(database_path, network, chain_id)?;
    let due = queue.due_entries(chrono::Utc::now().timestamp()).len();
    Ok(queue.len().saturating_sub(due))
}

/// One entry of the review queue.
///
/// The entry is a **loop** and the moment the loop is scheduled at.  The
/// moment says when the loop is due, and it is the only thing the queue
/// is ordered by.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewSlot {
    /// The loop the entry belongs to.
    pub loop_key: String,
    /// The state of the loop.
    pub runtime_status: String,
    /// The moment the loop is scheduled at, as it is written.
    pub due_at: Option<String>,
    /// The moment the loop is scheduled at, in seconds since the epoch.
    ///
    /// A loop that is **busy** -- it is being measured, or it waits for
    /// the receipt of its trade -- carries the largest value there is, so
    /// it sorts **last** for ever.  The marker it carries in the store is
    /// a date, but the queue does not count on the date: the date only
    /// says which of the two busy situations the loop is in.
    ///
    /// A loop that was never measured carries the smallest value there
    /// is, so it is **due at once**: a loop that has no review time point
    /// must not stay in the queue for ever.
    pub due_epoch: i64,
    /// Whether the loop has no scheduled review at all.
    pub is_sentinel: bool,
    /// The moment the round of the review of the loop started.
    ///
    /// The moment is **not** the moment the loop is scheduled at: it is
    /// the moment the round that is running (or that ran) began.  The
    /// sentinel that parks a busy loop replaces the scheduled moment, so
    /// without this the queue would erase the trace of a round that
    /// started and never ended -- and a watchdog that has to find such a
    /// round would have nothing to look at.
    pub review_cycle_started_at: Option<String>,
}

/// The review queue of a scope.
///
/// The queue is **one** queue: a loop that waits for a review and a loop
/// that waits for the receipt of its trade both have an entry, and the
/// entry of a busy loop carries the marker of its situation.
///
/// The queue is ordered by the moment a loop is scheduled at, so the main
/// thread of the review looks at the **first** entry only: the first
/// entry that is not due means that no entry behind it is due either.
#[derive(Debug, Default)]
pub struct ReviewQueue {
    /// The order of the entries: (the moment, the loop).
    order: BTreeSet<(i64, String)>,
    /// The entry of every loop.
    slots: HashMap<String, ReviewSlot>,
}

impl ReviewQueue {
    /// Put a loop into the queue.
    ///
    /// The queue holds **one** entry per loop, so a loop that is
    /// scheduled again **replaces** its entry: the second result of a
    /// loop covers the first one.
    pub fn insert(&mut self, slot: ReviewSlot) {
        self.remove(&slot.loop_key);
        let mut slot = slot;
        if slot.is_sentinel {
            // A parked loop sorts **last** for ever: the marker it
            // carries in the store is a date, but the queue does not
            // count on the date.
            slot.due_epoch = i64::MAX;
        }
        self.order.insert((slot.due_epoch, slot.loop_key.clone()));
        self.slots.insert(slot.loop_key.clone(), slot);
    }

    /// Take a loop out of the queue.
    pub fn remove(&mut self, loop_key: &str) -> Option<ReviewSlot> {
        let slot = self.slots.remove(loop_key)?;
        self.order.remove(&(slot.due_epoch, slot.loop_key.clone()));
        Some(slot)
    }

    /// The entry of a loop.
    pub fn get(&self, loop_key: &str) -> Option<&ReviewSlot> {
        self.slots.get(loop_key)
    }

    /// The first entry of the queue: the loop that is due first.
    pub fn first(&self) -> Option<&ReviewSlot> {
        let (_, loop_key) = self.order.iter().next()?;
        self.slots.get(loop_key)
    }

    /// The number of entries of the queue.
    pub fn len(&self) -> usize {
        self.slots.len()
    }

    /// Whether the queue is empty.
    pub fn is_empty(&self) -> bool {
        self.slots.is_empty()
    }

    /// The entries of the queue, in order.
    pub fn iter(&self) -> impl Iterator<Item = &ReviewSlot> {
        self.order
            .iter()
            .filter_map(|(_, loop_key)| self.slots.get(loop_key))
    }

    /// The loops that are due at the given moment.
    ///
    /// The walk stops at the **first** entry that is not due: the queue
    /// is ordered, so nothing behind it is due either.  A loop that has
    /// no scheduled review is never due, whatever its marker says.
    pub fn due_entries(&self, now_epoch: i64) -> Vec<&ReviewSlot> {
        let mut due = Vec::new();
        for (moment, loop_key) in &self.order {
            let Some(slot) = self.slots.get(loop_key) else {
                continue;
            };
            if *moment > now_epoch {
                // The queue is ordered, so nothing behind this entry
                // is due either.
                break;
            }
            if slot.is_sentinel || !is_claimable_status(&slot.runtime_status) {
                // A parked loop is busy -- it is being measured, or it
                // waits for the receipt of its trade -- and a loop that
                // may not start a round is not work: neither of them
                // is due.
                continue;
            }
            due.push(slot);
        }
        due
    }

    /// Take the loops that are due at the given moment out of the queue.
    pub fn take_due(&mut self, now_epoch: i64) -> Vec<ReviewSlot> {
        let due: Vec<String> = self
            .due_entries(now_epoch)
            .into_iter()
            .map(|slot| slot.loop_key.clone())
            .collect();
        due.into_iter()
            .filter_map(|loop_key| self.remove(&loop_key))
            .collect()
    }

    /// The moment the next loop becomes due.
    ///
    /// The moment is read from the **first** entry of the queue, so the
    /// answer is the moment the main thread of the review waits for.
    pub fn next_due_epoch(&self) -> Option<i64> {
        self.first().map(|slot| slot.due_epoch)
    }
}

/// The moment a loop is scheduled at, in seconds since the epoch.
///
/// A moment that cannot be read is treated as "due now": a loop whose
/// moment is unreadable must not stay in the queue for ever.
fn moment_epoch(moment: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(moment.trim())
        .ok()
        .map(|moment| moment.with_timezone(&chrono::Utc).timestamp())
}

/// Read the review queue of a scope.
///
/// The queue is built from the store, so it is a **derived** index: the
/// store stays the truth, and the queue can be rebuilt from it at any
/// time.
pub fn load_review_queue_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ReviewQueue, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT loop_key, runtime_status, next_review_at, review_cycle_started_at
           FROM manager_v4_closed_loop_runtime_state
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4
            AND runtime_status <> ?5",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            RUNTIME_STATUS_RETIRED
        ],
        |row| {
            let loop_key: String = row.get(0)?;
            let runtime_status: String = row.get(1)?;
            let due_at: Option<String> = row.get(2)?;
            let review_cycle_started_at: Option<String> = row.get(3)?;
            let is_sentinel = due_at.as_deref().is_some_and(is_review_sentinel);
            let due_epoch = if is_sentinel {
                // A loop that has no scheduled review sorts **last** for
                // ever: the marker in the store is a date, but the queue
                // does not count on the date.
                i64::MAX
            } else {
                due_at.as_deref().and_then(moment_epoch).unwrap_or(i64::MIN)
            };
            Ok(ReviewSlot {
                loop_key,
                runtime_status,
                due_at,
                due_epoch,
                is_sentinel,
                review_cycle_started_at,
            })
        },
    )?;
    let mut queue = ReviewQueue::default();
    for slot in rows {
        queue.insert(slot?);
    }
    Ok(queue)
}

/// Read the review queue of a scope from the store.
pub fn read_review_queue(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<ReviewQueue, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    load_review_queue_on_connection(&connection, network, chain_id)
}

/// The fingerprint of the review queue of a scope.
///
/// The fingerprint answers one question: **is the resident queue still
/// the queue of the store?**  Every write that touches the schedule of a
/// loop -- the claim of a round, the restart of a clock, the park at a
/// sentinel -- bumps the moment the row was updated, so the fingerprint
/// changes with every one of them.  A resident queue that carries the
/// fingerprint of the moment it was loaded can therefore tell whether it
/// may still be served.
///
/// The fingerprint is read with **one** row, so the check costs the same
/// whether the scope holds ten loops or ten thousand.
pub fn review_queue_fingerprint_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<String, ClosedLoopError> {
    let row: (i64, Option<String>) = connection.query_row(
        "SELECT COUNT(*), MAX(updated_at)
           FROM manager_v4_closed_loop_runtime_state
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4",
        params![network, chain_id, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    Ok(format!(
        "{}|{}",
        row.0,
        row.1.as_deref().unwrap_or_default().trim()
    ))
}

/// Read the fingerprint of the review queue of a scope.
pub fn read_review_queue_fingerprint(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<String, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    review_queue_fingerprint_on_connection(&connection, network, chain_id)
}

/// The review queue of a scope, together with the fingerprint of the
/// store it was read from.
///
/// The two are read **together**, inside one snapshot of the store, so
/// they cannot disagree: the fingerprint says what the store looked like
/// when the queue was read, and a queue that carries a **different**
/// fingerprint than the one the store shows now is a queue that must be
/// read again.
///
/// The pair is what makes a **resident** queue possible: the resident
/// copy is served as long as the fingerprint holds, and the fingerprint
/// costs **one row**, so the check does not grow with the scope.
#[derive(Debug)]
pub struct ResidentReviewQueue {
    /// The fingerprint of the store at the moment the queue was read.
    pub fingerprint: String,
    /// The queue itself.
    pub queue: ReviewQueue,
}

/// Read the review queue of a scope and the fingerprint of the store.
pub fn read_resident_review_queue(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<ResidentReviewQueue, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_resident_review_queue_on_connection(&connection, network, chain_id)
}

/// Read the review queue of a scope and the fingerprint of the store,
/// on a connection the caller holds.
///
/// The two are read inside **one** transaction, so they are two views of
/// the same moment of the store: a write that lands between the two
/// reads cannot make the fingerprint describe a queue that was never
/// read.
pub fn read_resident_review_queue_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ResidentReviewQueue, ClosedLoopError> {
    let transaction = connection.unchecked_transaction()?;
    let fingerprint = review_queue_fingerprint_on_connection(connection, network, chain_id)?;
    let queue = load_review_queue_on_connection(connection, network, chain_id)?;
    transaction.commit()?;
    Ok(ResidentReviewQueue { fingerprint, queue })
}

/// The loops of a scope that are due, taken from the queue.
///
/// The queue is ordered by the moment the loops are scheduled at, so the
/// walk stops at the **first** entry that is not due: the cost of one
/// round is the number of loops that are due and **not** the number of
/// loops the scope holds.  That is the difference between a scan that
/// grows with the scope and a scan that grows with the work.
pub fn due_review_loops_from_queue(
    queue: &ReviewQueue,
    now_epoch: i64,
) -> Vec<ClosedLoopReviewDue> {
    queue
        .due_entries(now_epoch)
        .into_iter()
        .map(|slot| ClosedLoopReviewDue {
            loop_key: slot.loop_key.clone(),
            runtime_status: slot.runtime_status.clone(),
            next_review_at: slot.due_at.clone(),
            review_waited_seconds: now_epoch.saturating_sub(slot.due_epoch).max(0),
            review_cycle_started_at: slot.review_cycle_started_at.clone(),
        })
        .collect()
}

/// The number of entries the walk over a queue touched.
///
/// The number is what makes the prefix truncation **observable**: a round
/// that had to walk the whole queue touched every entry, and a round that
/// stopped at the first entry that is not due touched the due entries and
/// one more.
pub fn review_queue_scan_cost(queue: &ReviewQueue, now_epoch: i64) -> usize {
    let mut touched = 0usize;
    for (moment, _) in &queue.order {
        touched += 1;
        if *moment > now_epoch {
            break;
        }
    }
    touched
}

/// One entry of the review queue, as an operator sees it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopReviewQueueEntry {
    /// The loop the entry belongs to.
    pub loop_key: String,
    /// The state of the loop.
    pub runtime_status: String,
    /// The moment the loop is scheduled at.
    pub next_review_at: Option<String>,
    /// Whether the loop is parked at a sentinel.
    ///
    /// A parked loop is busy: it is either being measured, or it waits
    /// for the receipt of its trade.  The two situations are told apart
    /// by the state of the loop, because the sentinel itself is only a
    /// marker.
    pub is_sentinel: bool,
    /// The seconds the loop waits before it is due again.
    ///
    /// Zero means the loop is due now.
    pub review_wait_seconds: i64,
}

/// The review queue of a scope, as an operator sees it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopReviewQueue {
    /// The entries of the queue, ordered by the moment the loops are
    /// scheduled at.
    pub entries: Vec<ClosedLoopReviewQueueEntry>,
    /// The moment the next loop becomes due.
    ///
    /// The moment is `None` when the next loop has no moment at all -- it
    /// was never measured -- or when the queue holds no loop that is left.
    /// `due_count` tells the two apart.
    pub next_due_at: Option<String>,
    /// The seconds until the next loop becomes due.
    ///
    /// Zero means a loop is due now.
    pub next_due_in_seconds: i64,
    /// The number of loops that are due now.
    pub due_count: usize,
    /// The number of loops that still wait.
    pub waiting_count: usize,
    /// The number of entries of the queue.
    pub length: usize,
}

/// The view of a review queue at a given moment.
///
/// The view is what the main thread of the review works from: the
/// entries are ordered by the moment the loops are scheduled at, so the
/// walk stops at the **first** entry that is not due.
pub fn review_queue_view(queue: &ReviewQueue, now_epoch: i64) -> ClosedLoopReviewQueue {
    let entries: Vec<ClosedLoopReviewQueueEntry> = queue
        .iter()
        .map(|slot| ClosedLoopReviewQueueEntry {
            loop_key: slot.loop_key.clone(),
            runtime_status: slot.runtime_status.clone(),
            next_review_at: slot.due_at.clone(),
            is_sentinel: slot.is_sentinel,
            review_wait_seconds: if slot.is_sentinel {
                0
            } else {
                slot.due_epoch.saturating_sub(now_epoch).max(0)
            },
        })
        .collect();
    let due_count = queue.due_entries(now_epoch).len();
    // The first entry that is not parked is the next loop that is due: a
    // parked loop sorts last, so it never hides a loop that is due.
    let next = queue.iter().find(|slot| !slot.is_sentinel);
    let (next_due_at, next_due_in_seconds) = match next {
        Some(slot) => (
            slot.due_at.clone(),
            slot.due_epoch.saturating_sub(now_epoch).max(0),
        ),
        None => (None, 0),
    };
    ClosedLoopReviewQueue {
        length: entries.len(),
        due_count,
        waiting_count: entries.len().saturating_sub(due_count),
        entries,
        next_due_at,
        next_due_in_seconds,
    }
}

/// Read the review queue of a scope and turn it into its view.
pub fn read_review_queue_view(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<ClosedLoopReviewQueue, ClosedLoopError> {
    let queue = read_review_queue(database_path, network, chain_id)?;
    Ok(review_queue_view(&queue, chrono::Utc::now().timestamp()))
}
/// Set the wait of a closed loop.
///
/// A wait of **zero** is what a manual review asks for: the loop waits for
/// nothing, so the next round of the review starts at once.  A wait of
/// more than zero starts the clock of the loop with that interval.
pub fn set_review_wait(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    wait_seconds: i64,
) -> Result<bool, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), true)?;
    if wait_seconds <= 0 {
        return mark_review_required_on_connection(&connection, network, chain_id, loop_key);
    }
    let updated = connection.execute(
        "UPDATE manager_v4_closed_loop_runtime_state
            SET runtime_status = ?5, status_reason = ?6,
                next_review_at = ?7, review_required = 0,
                review_cycle_started_at = ?8, updated_at = ?9
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4 AND loop_key = ?10",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            RUNTIME_STATUS_COOLDOWN,
            "manual_review_wait",
            (chrono::Utc::now() + chrono::Duration::seconds(wait_seconds)).to_rfc3339(),
            chrono::Utc::now().to_rfc3339(),
            chrono::Utc::now().to_rfc3339(),
            loop_key
        ],
    )?;
    Ok(updated > 0)
}

/// Advance the review clocks of every closed loop of a scope.
///
/// The number of loops that became due is returned.
pub fn advance_review_clocks(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<usize, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), true)?;
    advance_review_clocks_on_connection(&connection, network, chain_id)
}

/// Claim the review round of one closed loop.
///
/// The claim starts the cycle of the loop and is the lock of the loop:
/// a loop whose round is already running is not claimed again.
pub fn begin_review_cycle(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    reason: &str,
) -> Result<bool, ClosedLoopError> {
    let connection = open_connection(database_path.as_ref(), true)?;
    begin_review_cycle_on_connection(&connection, network, chain_id, loop_key, reason)
}

/// Resolve the closed loop of a route.
///
/// The route is the list of the symbols the loop walks, in order.  A route
/// that no published loop walks has no loop, so nothing is resolved and
/// the caller reports that instead of guessing.
pub fn resolve_loop_key_by_path_symbols(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    symbols: &[String],
) -> Result<Option<String>, ClosedLoopError> {
    let wanted: Vec<String> = symbols
        .iter()
        .map(|symbol| symbol.trim().to_uppercase())
        .filter(|symbol| !symbol.is_empty())
        .collect();
    if wanted.is_empty() {
        return Ok(None);
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let mut statement = connection.prepare(
        "SELECT loop_key, path_symbols_json FROM manager_v4_closed_loops
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
            ))
        },
    )?;
    for row in rows {
        let (loop_key, symbols_json) = row?;
        let published: Vec<String> = symbols_json
            .as_deref()
            .and_then(|raw| serde_json::from_str::<Vec<String>>(raw).ok())
            .unwrap_or_default()
            .iter()
            .map(|symbol| symbol.trim().to_uppercase())
            .collect();
        if published == wanted {
            return Ok(Some(loop_key));
        }
    }
    Ok(None)
}

/// The review schedule of one closed loop.
#[derive(Debug, Clone, Default)]
struct ReviewSchedule {
    last_reviewed_at: Option<String>,
    next_review_at: Option<String>,
    review_required: bool,
    runtime_status: Option<String>,
    review_cycle_started_at: Option<String>,
}

/// The review schedules the store holds.
///
/// The schedules are read **before** the catalog is replaced, because a
/// rebuild publishes the topology again and must not reset the review
/// clock of a loop that was measured.
fn read_review_schedules(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, ReviewSchedule>, ClosedLoopError> {
    let mut statement = connection.prepare(
        "SELECT loop_key, last_reviewed_at, next_review_at, review_required,
                runtime_status, review_cycle_started_at
         FROM manager_v4_closed_loop_runtime_state
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND scope_key = ?4",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, i64>(3)? != 0,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, Option<String>>(5)?,
            ))
        },
    )?;
    let mut schedules = BTreeMap::new();
    for row in rows {
        let (
            loop_key,
            last_reviewed_at,
            next_review_at,
            review_required,
            runtime_status,
            review_cycle_started_at,
        ) = row?;
        schedules.insert(
            loop_key,
            ReviewSchedule {
                last_reviewed_at,
                next_review_at,
                review_required,
                runtime_status,
                review_cycle_started_at,
            },
        );
    }
    Ok(schedules)
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
    // The review clock of a loop **survives** the rebuild.  A rebuild
    // publishes the topology again; it does not measure the loop again, so
    // it must not claim that the loop is waiting for a review it already
    // has, and it must not forget when the loop was measured.
    let schedules = read_review_schedules(connection, network, chain_id)?;
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
                 backup_directional_pool_ids_json, hop_candidate_counts_json,
                 hop_candidate_combination_count, recommendation_basis,
                 recommended_first_priority, candidate_edge_version,
                 candidate_edge_checksum, observation_version,
                 observation_checksum, status, source,
                 single_tick_capacity_anchor_raw,
                 single_tick_capacity_binding_directional_pool_id,
                 borrow_token_usdg_value_raw,
                 borrow_token_decimals,
                 cross_tick_review_threshold_usdg_raw,
                 cross_tick_capacity_anchor_raw,
                 directional_pool_ids_json,
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
                serde_json::to_string(&row.backup_directional_pool_ids)?,
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
                row.single_tick_capacity_binding_directional_pool_id.clone(),
                row.borrow_token_usdg_value_raw.map(|value| value.to_string()),
                row.borrow_token_decimals.map(|value| i64::from(value)),
                row.cross_tick_review_threshold_usdg_raw.to_string(),
                row.cross_tick_capacity_anchor_raw.map(|value| value.to_string()),
                serde_json::to_string(&row.directional_pool_ids)?,
                now
            ],
        )?;
    }
    for row in &desired.hops {
        connection.execute(
            "INSERT INTO manager_v4_closed_loop_hops (
                 network, chain_id, protocol, scope_key, loop_key, hop_index,
                 hop_name, input_token_key_id, output_token_key_id,
                 input_address, output_address,
                 pool_selection_basis, optimization_id, temporary_override,
                 directional_pool_id, hop_direction, directional_scope_key,
                 directional_pool_rank,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?19)",
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
                row.pool_selection_basis,
                row.optimization_id,
                i64::from(row.temporary_override),
                row.directional_pool_id.clone(),
                row.hop_direction,
                row.directional_scope_key.clone(),
                row.directional_pool_rank,
                now
            ],
        )?;
    }
    for row in &desired.optimizations {
        connection.execute(
            "INSERT INTO manager_v4_closed_loop_optimizations (
                 network, chain_id, protocol, scope_key, loop_key, hop_index,
                 optimization_id, original_directional_pool_id,
                 optimized_directional_pool_id,
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
                row.original_directional_pool_id,
                row.optimized_directional_pool_id,
                row.optimization_basis,
                row.expected_price_improvement,
                row.status,
                row.expires_at,
                now
            ],
        )?;
    }
    // The review time points of a **fresh** catalog are staggered: the
    // loops do not all become due in the same second, so the first round
    // of the reviews does not hit the RPC provider with every loop at
    // once.  The position of a loop in the catalog is its place in that
    // order, and the spacing is a parameter of the running system.
    let spacing = review_start_spacing_seconds_on_connection(connection);
    let mut positions: Vec<(&String, usize)> = desired
        .runtime_states
        .iter()
        .enumerate()
        .map(|(position, row)| (&row.loop_key, position))
        .collect();
    positions.sort_by(|left, right| left.0.cmp(right.0));
    let positions: BTreeMap<&String, usize> =
        positions.into_iter().map(|(key, position)| (key, position)).collect();
    for row in &desired.runtime_states {
        let schedule = schedules.get(&row.loop_key);
        let last_reviewed_at = schedule
            .and_then(|value| value.last_reviewed_at.clone())
            .or_else(|| row.last_reviewed_at.clone());
        let carried = schedule
            .and_then(|value| value.next_review_at.clone())
            .or_else(|| row.next_review_at.clone());
        // A loop that is published for the first time is scheduled at
        // once: its review time point is the moment of the rebuild plus
        // the spacing times its position in the catalog.
        let next_review_at = match carried {
            Some(moment) => Some(moment),
            None => {
                let position = positions.get(&row.loop_key).copied().unwrap_or(0) as i64;
                Some(
                    (chrono::Utc::now() + chrono::Duration::seconds(position * spacing))
                        .to_rfc3339(),
                )
            }
        };
        let review_required = schedule.map_or(row.review_required, |value| value.review_required);
        // The state of a loop belongs to the **loop**, so a rebuild that
        // publishes the topology again keeps the state and the clock of a
        // loop that is still published.  A loop that is published for the
        // first time waits for its first review.
        let runtime_status = schedule
            .and_then(|value| value.runtime_status.clone())
            .unwrap_or_else(|| row.runtime_status.clone());
        let review_cycle_started_at = schedule
            .and_then(|value| value.review_cycle_started_at.clone())
            .or_else(|| row.review_cycle_started_at.clone());
        connection.execute(
            "INSERT INTO manager_v4_closed_loop_runtime_state (
                 network, chain_id, protocol, scope_key, loop_key,
                 runtime_status, status_reason, review_generation,
                 review_required, last_rebuilt_at, last_reviewed_at,
                 next_review_at, review_cycle_started_at,
                 updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                CLOSED_LOOP_SCOPE_KEY,
                row.loop_key,
                runtime_status,
                row.status_reason,
                row.review_generation,
                i64::from(review_required),
                now,
                last_reviewed_at,
                next_review_at,
                review_cycle_started_at,
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
             directional_pool_version, directional_pool_checksum,
             library_versions_json, library_checksums_json, anchor_count,
             loop_count, hop_count, optimization_count, skipped_token_count,
             consumers_need_rebuild, generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22,
                   ?22)
         ON CONFLICT(network, chain_id, protocol, scope_key) DO UPDATE SET
             closed_loop_version = excluded.closed_loop_version,
             materialized_checksum = excluded.materialized_checksum,
             candidate_edge_version = excluded.candidate_edge_version,
             candidate_edge_checksum = excluded.candidate_edge_checksum,
             observation_version = excluded.observation_version,
             observation_checksum = excluded.observation_checksum,
             pool_scope_checksum = excluded.pool_scope_checksum,
             directional_pool_version = excluded.directional_pool_version,
             directional_pool_checksum = excluded.directional_pool_checksum,
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
            result.directional_pool_version,
            result.directional_pool_checksum,
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

/// Acknowledge that the consumers rebuilt against one generation of the
/// closed-loop library.
///
/// The flag `consumers_need_rebuild` says that the library moved and that
/// the consumers may still hold the generation before it.  **Only the
/// consumer knows when it rebuilt**, so only the consumer may clear the
/// flag, and the acknowledgement carries the generation the consumer
/// rebuilt against.
///
/// The flag is cleared **only** when that generation is the one the library
/// publishes now.  An acknowledgement of an older generation says nothing
/// about the current one, and a flag that a stale acknowledgement clears is
/// a flag that lies: the consumers would be told to rebuild a generation
/// they have already rebuilt, while a directory that moved again would be
/// published as if nobody had to rebuild it at all.
///
/// The answer is whether the acknowledgement changed the stored flag.
pub fn acknowledge_consumers_rebuilt(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    closed_loop_version: i64,
    materialized_checksum: Option<&str>,
) -> Result<bool, ClosedLoopError> {
    validate_operation(network, chain_id)?;
    let connection = open_connection(database_path.as_ref(), true)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    acknowledge_consumers_rebuilt_on_connection(
        &connection,
        network,
        chain_id,
        closed_loop_version,
        materialized_checksum,
    )
}

fn acknowledge_consumers_rebuilt_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    closed_loop_version: i64,
    materialized_checksum: Option<&str>,
) -> Result<bool, ClosedLoopError> {
    let changed = connection.execute(
        "UPDATE manager_v4_closed_loop_library_state
         SET consumers_need_rebuild = 0, updated_at = ?5
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND scope_key = ?4 AND consumers_need_rebuild != 0
           AND closed_loop_version = ?6
           AND (?7 IS NULL OR materialized_checksum = ?7)",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            CLOSED_LOOP_SCOPE_KEY,
            chrono::Utc::now().to_rfc3339(),
            closed_loop_version,
            materialized_checksum,
        ],
    )?;
    Ok(changed > 0)
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
    // The pool identity is **not** part of this comparison.  The manager rows
    // name directional pools and the legacy rows name base pools, so the two
    // layers no longer describe the pool in the same terms and a field by
    // field comparison would report a difference for every row.  The route
    // shape is compared here, and the drift of the pool identity belongs to
    // the direction layer's own integrity audit.
    let mut values = BTreeMap::new();
    for row in rows {
        values.insert(
            format!("{}|{}", row.anchor_symbol, row.loop_key),
            serde_json::to_string(&serde_json::json!({
                "pathText": row.path_text,
                "pathAddresses": row.path_addresses,
                "pathSymbols": row.path_symbols,
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
    // The hop's pool identity is left out for the same reason as the loop's:
    // the manager hop names a directional pool and the legacy hop names a
    // base pool.  The hop's **shape** is compared here.
    let mut values = BTreeMap::new();
    for row in rows {
        let key = format!("{}|{}", row.loop_key, row.hop_index);
        if let Ok(payload) = serde_json::to_string(&serde_json::json!({
            "hopName": row.hop_name,
            "inputAddress": row.input_address,
            "outputAddress": row.output_address,
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
        directional_pool_version: None,
        directional_pool_checksum: None,
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
        acknowledge_consumers_rebuilt, build_closed_loop_rows, compare_closed_loops,
        directional_primary_order_from_value,
        folded_path_capacity, materialize_closed_loops,
        materialize_closed_loops_write, path_min_capacity, read_closed_loop_input,
        read_closed_loop_snapshot, read_closed_loop_status, ClosedLoopError,
        ClosedLoopWriteRequest, ExpectedUpstream, MIN_CLOSED_LOOP_CAPACITY_RAW,
        DesiredClosedLoopData, DirectionalPoolIndex, resolve_hop_directional_pool,
        advance_review_clock_on_connection, review_interval_seconds,
        review_interval_seconds_on_connection, set_review_wait,
        start_review_clock_on_connection, REVIEW_INTERVAL_PARAMETER_KEY,
        REVIEW_INTERVAL_PARAMETER_NAMESPACE,
        HOP_MIDDLE_SCOPE_KEY, HOP_SIDE_SCOPE_KEY,
        path_min_single_tick_bound,
        hop_candidate_combinations, read_hops, read_optimizations,
        read_pending_optimizations, CLOSED_LOOP_SCOPE_KEY, NETWORK_PROTOCOL,
        MAX_HOP_CANDIDATE_COMBINATIONS,
        read_valuation_pool_states,
        mark_review_required_on_connection,
        record_review_schedule_on_connection,
        begin_review_cycle_on_connection,
        reset_review_start_on_connection, ReviewStartReset,
        is_review_sentinel, review_sentinel_for_status,
        REVIEW_PENDING_SENTINEL_AT, REVIEW_PENDING_SENTINEL_EPOCH,
        BROADCAST_PENDING_SENTINEL_AT, BROADCAST_PENDING_SENTINEL_EPOCH,
        RUNTIME_STATUS_COOLDOWN, RUNTIME_STATUS_REVIEW_PENDING,
        RUNTIME_STATUS_BROADCAST_PENDING,
        ReviewQueue, ReviewSlot, moment_epoch,
        read_review_queue, load_review_queue_on_connection, read_review_queue_view,
        count_waiting_review_loops, read_due_review_loops, RUNTIME_STATUS_RETIRED,
        read_resident_review_queue, read_review_queue_fingerprint,
        review_queue_scan_cost, open_connection,
    };
    use manager_contracts::directional_pool::{DirectionalPool, PoolDirection, V4Pool};
    use manager_contracts::TokenKeyId;
    use rusqlite::{params, Connection};
    use std::collections::{BTreeMap, BTreeSet};
    use chrono::Timelike;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const AMZN: &str = "0x2222222222222222222222222222222222222222";
    const TA: &str = "0x3333333333333333333333333333333333333333";
    const POOL_A1: &str = "pool-a1";
    const POOL_A2: &str = "pool-a2";
    const POOL_YZ: &str = "pool-yz";
    const POOL_YZ_2: &str = "pool-yz-2";
    const POOL_YZ_3: &str = "pool-yz-3";
    const POOL_YZ_REVERSE: &str = "pool-yz-reverse";
    const POOL_TA_FIRST: &str = "pool-ta-first";
    const POOL_AMZN_THIRD: &str = "pool-amzn-third";
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
        seed_directional_pools(&connection);
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
    ///
    /// The shape of the liquidity snapshot table in the fixture.
    ///
    /// The table carries the **market** columns -- price, tick and
    /// liquidity -- next to the capacity columns, because the USDG
    /// valuation reads the market half of this table and the capacity fold
    /// reads the same rows.
    fn snapshot_table_sql() -> String {
        "CREATE TABLE IF NOT EXISTS v4_exchange_pool_liquidity_snapshots (
            network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
            capacity_raw TEXT, liquidity_read_at TEXT,
            single_tick_max_token0_raw TEXT, single_tick_max_token1_raw TEXT,
            sqrt_price_x96 TEXT, tick INTEGER, liquidity TEXT)"
            .to_owned()
    }

    fn seed_capacities(connection: &Connection, capacities: &[(&str, i64)]) {
        connection
            .execute(
                &snapshot_table_sql(),
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
            // The closed loop reads the depth capacity from the directional
            // row the hop walks, so the row carries the same number the
            // snapshot publishes.
            connection
                .execute(
                    "UPDATE manager_v4_directional_exchange_pools SET depth_capacity_raw = ?1 WHERE network = ?2 AND chain_id = ?3 AND protocol = 'uniswap_v4' AND base_pool_id = ?4",
                    params![capacity.to_string(), NETWORK, CHAIN_ID, pool_id],
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
            // The USDG valuation reads the market half of the snapshot
            // table, so the published price is written there as well: the
            // valuation and the capacity fold then describe the same
            // observation.
            connection
                .execute(&snapshot_table_sql(), [])
                .unwrap();
            connection
                .execute(
                    "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                        network, chain_id, protocol, pool_id, capacity_raw,
                        liquidity_read_at, sqrt_price_x96, tick, liquidity
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, '1000000000000',
                               'fixture', ?4, 0, '1000000000000')",
                    params![NETWORK, CHAIN_ID, pool_id, sqrt_price_x96.to_string()],
                )
                .unwrap();
            // The tick state of a hop comes from the directional row the hop
            // walks, so the row carries the same price the pool table
            // publishes.
            connection
                .execute(
                    "UPDATE manager_v4_directional_exchange_pools SET sqrt_price_x96 = ?1, tick = 0, liquidity = '1000000000000' WHERE network = ?2 AND chain_id = ?3 AND protocol = 'uniswap_v4' AND base_pool_id = ?4",
                    params![sqrt_price_x96.to_string(), NETWORK, CHAIN_ID, pool_id],
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
                &snapshot_table_sql(),
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
            // The single-tick bound belongs to the **direction**, so each
            // row of the base pool takes the bound of the token it spends:
            // direction 0 spends token1, direction 1 spends token0.
            for (direction, bound) in [(0i64, token1), (1i64, token0)] {
                connection
                    .execute(
                        "UPDATE manager_v4_directional_exchange_pools SET input_capacity_raw = ?1, input_capacity_measured = ?2, depth_capacity_raw = '1000000' WHERE network = ?3 AND chain_id = ?4 AND protocol = 'uniswap_v4' AND base_pool_id = ?5 AND direction = ?6",
                        params![
                            bound.map(|value| value.to_string()),
                            i64::from(bound.is_some()),
                            NETWORK,
                            CHAIN_ID,
                            pool_id,
                            direction,
                        ],
                    )
                    .unwrap();
            }
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
        //
        // The hop is tried against **every** candidate of its tier first, so
        // the hop only disqualifies the path when no candidate of it reaches
        // the floor.  Both pools of the middle hop are therefore weak here:
        // the primary and its backup alike.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(
                &connection,
                &[
                    (POOL_A1, 1_000_000),
                    (POOL_YZ, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_2, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
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

    /// The middle hop trades **AMZN into TA**, so the direction it walks is
    /// the one that spends `token0`.  The fixture names the two sides of
    /// the hop symbolically, and this is the helper that resolves the
    /// symbolic name into the hop direction.
    const MIDDLE_DIRECTION: i64 = 1;

    /// Seed one more pool of the middle direction into the direction layer.
    ///
    /// The pool is **not** named by the market observation, so the hop
    /// reaches it through the backup tier of its `(pair, direction)` group
    /// and not through the candidate list.  This is the pool a truncation
    /// dropped: the direction layer kept it, the candidate list did not.
    fn seed_direction_layer_backup(
        connection: &Connection,
        base_pool_id: &str,
        token0_key_id: &str,
        token1_key_id: &str,
        rank: i64,
    ) {
        for direction in 0i64..=1 {
            let (input, output, zero_for_one) = if direction == 0 {
                (token1_key_id.to_owned(), token0_key_id.to_owned(), 0i64)
            } else {
                (token0_key_id.to_owned(), token1_key_id.to_owned(), 1i64)
            };
            let directional_pool_id = format!(
                "dpool:v1:{NETWORK}:{CHAIN_ID}:candidate_exchange:{base_pool_id}:{direction}"
            );
            connection
                .execute(
                    "INSERT INTO manager_v4_directional_exchange_pools (network, chain_id, protocol, scope_key, base_pool_id, direction, directional_pool_id, pool_manager, pool_key_json, token0, token1, token0_key_id, token1_key_id, fee, tick_spacing, hooks, pair_key_id, input_token_key_id, output_token_key_id, zero_for_one, pool_status, pair_direction_rank, materialized_at, updated_at) VALUES (?1, ?2, 'uniswap_v4', 'candidate_exchange', ?3, ?4, ?5, '0xpoolmanager', '{}', ?6, ?7, ?6, ?7, 3000, 60, '0x0', ?8, ?9, ?10, ?11, 'active', ?12, 'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        base_pool_id,
                        direction,
                        directional_pool_id,
                        token0_key_id,
                        token1_key_id,
                        format!("pair:{token0_key_id}:{token1_key_id}"),
                        input,
                        output,
                        zero_for_one,
                        rank
                    ],
                )
                .unwrap();
        }
    }

    /// Give the directional rows of a base pool their rank inside their
    /// `(pair, direction)` group.
    ///
    /// `None` clears the rank, which is the state of a pool whose direction
    /// was never priced.
    fn seed_direction_rank(connection: &Connection, base_pool_id: &str, rank: Option<i64>) {
        connection
            .execute(
                "UPDATE manager_v4_directional_exchange_pools SET pair_direction_rank = ?1
                 WHERE network = ?2 AND chain_id = ?3 AND protocol = 'uniswap_v4'
                   AND base_pool_id = ?4",
                params![rank, NETWORK, CHAIN_ID, base_pool_id],
            )
            .unwrap();
    }

    /// The pools every hop of the published data may trade, keyed by hop.
    ///
    /// The set is the **membership** of the candidate list: the primary of the
    /// hop plus every optimization the hop may take over to.
    fn pools_of_every_hop(desired: &DesiredClosedLoopData) -> BTreeMap<usize, BTreeSet<String>> {
        let mut pools: BTreeMap<usize, BTreeSet<String>> = BTreeMap::new();
        for hop in &desired.hops {
            pools
                .entry(hop.hop_index)
                .or_default()
                .insert(base_pool_of(&hop.directional_pool_id).to_owned());
        }
        for optimization in &desired.optimizations {
            pools
                .entry(optimization.hop_index)
                .or_default()
                .insert(base_pool_of(&optimization.optimized_directional_pool_id).to_owned());
        }
        pools
    }

    /// The base pool of a directional pool id.
    ///
    /// The id carries the base pool and the direction inseparably --
    /// `dpool:v1:<network>:<chain>:<scope>:<base pool>:<direction>` -- so the
    /// base pool of a hop is read out of the hop's own identity, and never
    /// from a field of its own.
    fn base_pool_of(directional_pool_id: &str) -> &str {
        directional_pool_id
            .rsplit(':')
            .nth(1)
            .expect("a directional pool id names its base pool")
    }

    /// The directional pool id of a base pool in a scope.
    fn directional_pool_id_of(scope_key: &str, base_pool_id: &str, direction: i64) -> String {
        format!("dpool:v1:{NETWORK}:{CHAIN_ID}:{scope_key}:{base_pool_id}:{direction}")
    }

    #[test]
    fn the_primary_order_switch_is_read_from_the_environment() {
        // An absent or empty value means **on**: the direction layer is the
        // authority.  Only an explicit negative value turns the switch off,
        // so a typo can never silently keep the legacy order alive.
        assert!(directional_primary_order_from_value(None));
        assert!(directional_primary_order_from_value(Some("")));
        assert!(directional_primary_order_from_value(Some("   ")));
        assert!(directional_primary_order_from_value(Some("1")));
        assert!(directional_primary_order_from_value(Some("on")));
        assert!(directional_primary_order_from_value(Some("TRUE")));
        assert!(!directional_primary_order_from_value(Some("0")));
        assert!(!directional_primary_order_from_value(Some("false")));
        assert!(!directional_primary_order_from_value(Some("off")));
        assert!(!directional_primary_order_from_value(Some("NO")));
    }

    #[test]
    fn the_direction_rank_decides_the_primary_of_a_hop() {
        // The candidate edge names `POOL_A1` first, but the direction layer
        // ranks `POOL_A2` first inside their `(pair, direction)` group.  The
        // direction layer is the authority of the primary, so the hop trades
        // the pool it ranked first.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_direction_rank(&connection, POOL_A2, Some(1));
            seed_direction_rank(&connection, POOL_A1, Some(2));
        }
        let applied = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(applied.loop_count, 1);

        let connection = Connection::open(&path).unwrap();
        let hops = read_hops(&connection, NETWORK, CHAIN_ID, None, 64).unwrap();
        let first = hops
            .iter()
            .find(|hop| hop.hop_index == 1)
            .expect("the first hop of the published loop");
        assert_eq!(
            base_pool_of(&first.directional_pool_id), POOL_A2,
            "the hop trades the pool the direction layer ranked first"
        );
        assert_eq!(
            first.directional_pool_id,
            directional_pool_id_of(HOP_SIDE_SCOPE_KEY, POOL_A2, first.hop_direction),
            "the hop names the directional pool of the pool the direction layer ranked first"
        );
        assert_eq!(first.directional_pool_rank, Some(1));
        assert_eq!(
            first.pool_selection_basis, "directional_pair_direction_rank:1",
            "the basis of the hop names the authority that picked the pool"
        );
        assert!(!first.temporary_override, "the primary is not an override");
    }

    #[test]
    fn the_legacy_order_is_kept_when_the_switch_is_off() {
        // The switch is the rollback path of the switch-over: with it off,
        // the candidate tier keeps naming the primary of the hop.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_direction_rank(&connection, POOL_A2, Some(1));
            seed_direction_rank(&connection, POOL_A1, Some(2));
        }
        let connection = Connection::open(&path).unwrap();
        let mut input = read_closed_loop_input(&connection, NETWORK, CHAIN_ID).unwrap();
        input.directional_primary_order = false;
        let desired = build_closed_loop_rows(&input).unwrap();
        let first = desired
            .hops
            .iter()
            .find(|hop| hop.hop_index == 1)
            .expect("the first hop of the published loop");
        assert_eq!(
            base_pool_of(&first.directional_pool_id), POOL_A1,
            "the legacy candidate tier keeps naming the primary"
        );
        assert_eq!(
            first.pool_selection_basis, "first_fixed_fee",
            "the basis of the legacy order is kept"
        );
    }

    #[test]
    fn the_reordering_keeps_the_membership_of_the_candidate_list() {
        // The re-order is a pure permutation: the pools a hop may trade are
        // the same with the switch on and with the switch off, so no pool
        // becomes reachable that the list could not reach before.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_direction_rank(&connection, POOL_A2, Some(1));
            seed_direction_rank(&connection, POOL_A1, Some(2));
            seed_direction_rank(&connection, POOL_YZ_2, Some(2));
            seed_direction_rank(&connection, POOL_YZ, Some(1));
        }
        let connection = Connection::open(&path).unwrap();
        let mut input = read_closed_loop_input(&connection, NETWORK, CHAIN_ID).unwrap();
        let switched_on = build_closed_loop_rows(&input).unwrap();
        input.directional_primary_order = false;
        let switched_off = build_closed_loop_rows(&input).unwrap();
        assert_eq!(
            pools_of_every_hop(&switched_on),
            pools_of_every_hop(&switched_off),
            "the membership of the candidate list does not change"
        );
        assert!(!pools_of_every_hop(&switched_on).is_empty());
    }

    #[test]
    fn a_pool_without_a_rank_stays_behind_the_ranked_pools() {
        // A row without a rank was never priced, so it is not a candidate of
        // its group and it never becomes the primary of a hop.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_direction_rank(&connection, POOL_A1, None);
            seed_direction_rank(&connection, POOL_A2, Some(1));
        }
        let connection = Connection::open(&path).unwrap();
        let input = read_closed_loop_input(&connection, NETWORK, CHAIN_ID).unwrap();
        let desired = build_closed_loop_rows(&input).unwrap();
        let first = desired
            .hops
            .iter()
            .find(|hop| hop.hop_index == 1)
            .expect("the first hop of the published loop");
        assert_eq!(
            base_pool_of(&first.directional_pool_id), POOL_A2,
            "the ranked pool is the primary"
        );
        assert_eq!(first.directional_pool_rank, Some(1));
        let alternatives = desired
            .optimizations
            .iter()
            .filter(|optimization| optimization.hop_index == 1)
            .map(|optimization| base_pool_of(&optimization.optimized_directional_pool_id))
            .collect::<Vec<_>>();
        assert_eq!(
            alternatives,
            vec![POOL_A1],
            "the pool without a rank stays an alternative of the hop"
        );
    }

    #[test]
    fn a_hop_whose_primary_fails_the_capacity_gate_is_taken_over_by_its_next_candidate() {
        // The primary of the middle hop is too shallow, so the hop trades
        // the pool behind it.  The hop keeps naming the pool the
        // recommendation named, so the takeover is visible on the row.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(
                &connection,
                &[
                    (POOL_A1, 1_000_000),
                    (POOL_A2, 1_000_000),
                    (POOL_YZ, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_2, 1_000_000),
                    (POOL_D, 1_000_000),
                ],
            );
        }
        let applied = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(applied.loop_count, 1, "the path is published by its backup");

        let connection = Connection::open(&path).unwrap();
        let hops = read_hops(&connection, NETWORK, CHAIN_ID, None, 64).unwrap();
        let middle = hops
            .iter()
            .find(|hop| hop.hop_index == 2)
            .expect("the middle hop of the published loop");
        assert_eq!(
            base_pool_of(&middle.directional_pool_id),
            POOL_YZ_2,
            "the hop names the directional pool of the backup"
        );
        assert_eq!(
            middle.optimization_id.as_deref(),
            Some("middle-rank-2"),
            "the hop names the optimization that took it over"
        );
        assert!(middle.temporary_override, "the takeover is a temporary one");
        assert_eq!(middle.hop_direction, MIDDLE_DIRECTION);
    }

    #[test]
    fn a_backup_of_the_direction_layer_takes_over_a_hop_the_candidate_list_could_not_serve() {
        // The candidate list of the middle hop is exhausted and both of its
        // pools are too shallow.  The direction layer still holds a third
        // pool of the same `(pair, direction)` group, and that pool is the
        // one that carries the hop.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_direction_layer_backup(
                &connection,
                POOL_YZ_3,
                &token(AMZN),
                &token(TA),
                6,
            );
            seed_capacities(
                &connection,
                &[
                    (POOL_A1, 1_000_000),
                    (POOL_A2, 1_000_000),
                    (POOL_YZ, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_2, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_3, 1_000_000),
                    (POOL_D, 1_000_000),
                ],
            );
        }
        let applied = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(applied.loop_count, 1, "the path is published by the backup");

        let connection = Connection::open(&path).unwrap();
        let hops = read_hops(&connection, NETWORK, CHAIN_ID, None, 64).unwrap();
        let middle = hops
            .iter()
            .find(|hop| hop.hop_index == 2)
            .expect("the middle hop of the published loop");
        assert_eq!(
            base_pool_of(&middle.directional_pool_id),
            POOL_YZ_3,
            "the hop names the directional pool of the backup"
        );
        assert_eq!(
            middle.optimization_id.as_deref(),
            Some("middle-rank-3"),
            "the backup of the group is an optimization of its own, and its id names the position it is tried in"
        );
        assert!(middle.temporary_override, "the takeover is a temporary one");
    }

    #[test]
    fn an_optimization_that_failed_the_gate_is_recorded_as_expired() {
        // The middle hop tries its candidate list first and only then the
        // backup of the group.  The candidate that was tried and did not
        // pass is recorded as expired, so the state of the table tells which
        // pools were already ruled out.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_direction_layer_backup(
                &connection,
                POOL_YZ_3,
                &token(AMZN),
                &token(TA),
                6,
            );
            seed_capacities(
                &connection,
                &[
                    (POOL_A1, 1_000_000),
                    (POOL_A2, 1_000_000),
                    (POOL_YZ, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_2, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_3, 1_000_000),
                    (POOL_D, 1_000_000),
                ],
            );
        }
        let applied = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(applied.loop_count, 1);

        let connection = Connection::open(&path).unwrap();
        let statuses = read_all_optimization_statuses(&connection);
        assert_eq!(
            statuses.get("middle-rank-2").map(String::as_str),
            Some("expired"),
            "the candidate that was tried and did not pass is expired"
        );
        assert_eq!(
            statuses.get("middle-rank-3").map(String::as_str),
            Some("temporary"),
            "the optimization in effect is temporary"
        );
        assert_eq!(
            statuses.get("first-rank-2").map(String::as_str),
            Some("candidate"),
            "the pool that was never tried is still a candidate"
        );
    }

    /// The state of every optimization row of the scope, keyed by the
    /// optimization id.
    fn read_all_optimization_statuses(connection: &Connection) -> BTreeMap<String, String> {
        let mut statement = connection
            .prepare(
                "SELECT optimization_id, status FROM manager_v4_closed_loop_optimizations
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
            )
            .unwrap();
        let rows = statement
            .query_map(
                params![NETWORK, CHAIN_ID, NETWORK_PROTOCOL, CLOSED_LOOP_SCOPE_KEY],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .unwrap();
        rows.map(|row| row.unwrap()).collect()
    }

    #[test]
    fn only_the_optimizations_in_effect_are_consumed_and_the_rest_stand_by() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_direction_layer_backup(
                &connection,
                POOL_YZ_3,
                &token(AMZN),
                &token(TA),
                6,
            );
            seed_capacities(
                &connection,
                &[
                    (POOL_A1, 1_000_000),
                    (POOL_A2, 1_000_000),
                    (POOL_YZ, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_2, (MIN_CLOSED_LOOP_CAPACITY_RAW - 1) as i64),
                    (POOL_YZ_3, 1_000_000),
                    (POOL_D, 1_000_000),
                ],
            );
        }
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();

        let connection = Connection::open(&path).unwrap();
        let consumed = read_optimizations(&connection, NETWORK, CHAIN_ID, None, 64).unwrap();
        assert_eq!(
            consumed.len(),
            1,
            "only the optimization in effect is consumed"
        );
        assert_eq!(consumed[0].optimization_id, "middle-rank-3");
        assert_eq!(consumed[0].status, "temporary");
        assert_eq!(
            base_pool_of(&consumed[0].original_directional_pool_id),
            POOL_YZ
        );
        assert_eq!(
            base_pool_of(&consumed[0].optimized_directional_pool_id),
            POOL_YZ_3
        );

        let pending = read_pending_optimizations(&connection, NETWORK, CHAIN_ID, None, 64).unwrap();
        let pending_ids = pending
            .iter()
            .map(|row| row.optimization_id.as_str())
            .collect::<Vec<_>>();
        assert_eq!(
            pending_ids,
            vec!["first-rank-2"],
            "the pool that was never tried stands by"
        );
        assert!(
            pending.iter().all(|row| row.status == "candidate"),
            "a standby optimization is a candidate"
        );
    }

    #[test]
    fn the_candidate_combinations_are_tried_from_the_primary_outward() {
        // The order of the search is the contract: the primary combination
        // is tried first, then the combinations that deviate from it in one
        // hop, and only then the ones that deviate in two.
        let combinations = hop_candidate_combinations(2, 3, 2);
        assert_eq!(combinations[0], (0, 0, 0), "the primary is tried first");
        let deviations = combinations
            .iter()
            .map(|(i, j, k)| usize::from(*i > 0) + usize::from(*j > 0) + usize::from(*k > 0))
            .collect::<Vec<_>>();
        let mut sorted = deviations.clone();
        sorted.sort_unstable();
        assert_eq!(deviations, sorted, "the deviations never decrease");
        assert_eq!(combinations.len(), 12, "every combination is reachable");
        assert_eq!(
            combinations[1..4],
            vec![(0, 0, 1), (0, 1, 0), (0, 2, 0)],
            "one deviation is tried before two"
        );
    }

    #[test]
    fn the_search_over_the_combinations_stays_bounded() {
        let combinations = hop_candidate_combinations(64, 64, 64);
        assert_eq!(
            combinations.len(),
            MAX_HOP_CANDIDATE_COMBINATIONS,
            "the search never grows without a bound"
        );
        assert_eq!(
            combinations[0], (0, 0, 0),
            "the primary is tried first even when the search is cut"
        );
    }

    /// Seeds the reverse direction of the middle hop, so the same anchor
    /// carries a second, opposite directed loop.

    /// Seed the direction layer the closed loop consumes.
    ///
    /// The closed loop walks the projection of the layer that published its
    /// pool: the candidate edge scope supplies the first and the third hop,
    /// the candidate exchange scope supplies the middle one. Every base pool
    /// projects onto **both** of its directions, so the row a hop walks is
    /// the one whose input token is the token the hop spends.
    ///
    /// The rows carry the base identity and the token order; the measured
    /// quantities are written by the seeding helpers, so a test decides
    /// which pool published what.
    fn seed_directional_pools(connection: &Connection) {
        let pools = vec![
            ("core4", POOL_A1, token(USDG), token(AMZN)),
            ("core4", POOL_A2, token(USDG), token(AMZN)),
            ("core4", POOL_D, token(TA), token(USDG)),
            ("core4", POOL_TA_FIRST, token(USDG), token(TA)),
            ("core4", POOL_AMZN_THIRD, token(AMZN), token(USDG)),
            ("candidate_exchange", POOL_YZ, token(AMZN), token(TA)),
            ("candidate_exchange", POOL_YZ_2, token(AMZN), token(TA)),
            ("candidate_exchange", POOL_YZ_REVERSE, token(TA), token(AMZN)),
        ];
        for (scope_key, base_pool_id, token0, token1) in pools {
            for direction in 0i64..=1 {
                // Direction 0 spends token1 and receives token0, direction 1
                // spends token0 and receives token1.
                let (input, output, zero_for_one) = if direction == 0 {
                    (token1.clone(), token0.clone(), 0i64)
                } else {
                    (token0.clone(), token1.clone(), 1i64)
                };
                // The identity is built in the shape the contract produces.
                // The fixture names its pools symbolically, and a symbolic
                // name is not a valid base pool id, so the identity is
                // assembled here instead of through the contract.
                let directional_pool_id = format!("dpool:v1:{NETWORK}:{CHAIN_ID}:{scope_key}:{base_pool_id}:{direction}");
                connection.execute("INSERT INTO manager_v4_directional_exchange_pools (network, chain_id, protocol, scope_key, base_pool_id, direction, directional_pool_id, pool_manager, pool_key_json, token0, token1, token0_key_id, token1_key_id, fee, tick_spacing, hooks, pair_key_id, input_token_key_id, output_token_key_id, zero_for_one, pool_status, pair_direction_rank, materialized_at, updated_at) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, '0xpoolmanager', '{}', ?7, ?8, ?7, ?8, 3000, 60, '0x0', ?9, ?10, ?11, ?12, 'active', 1, 'fixture', 'fixture')", params![NETWORK, CHAIN_ID, scope_key, base_pool_id, direction, directional_pool_id, token0, token1, format!("pair:{token0}:{token1}"), input, output, zero_for_one]).unwrap();
            }
        }
        for scope_key in ["core4", "candidate_exchange"] {
            connection.execute("INSERT INTO manager_v4_directional_pool_library_state (network, chain_id, protocol, scope_key, directional_pool_version, materialized_checksum, pool_scope_version, pool_scope_checksum, snapshot_checksum, library_versions_json, library_checksums_json, base_pool_count, directional_pool_count, direction0_count, direction1_count, eligible_pair_count, measured_capacity_count, measured_output_count, excluded_pool_count, generated_at, updated_at) VALUES (?1, ?2, 'uniswap_v4', ?3, 1, 'sha256:directional-fixture', 1, 'sha256:scope-fixture', 'sha256:snapshot-fixture', '{}', '{}', 0, 0, 0, 0, 0, 0, 0, 0, 'fixture', 'fixture')", params![NETWORK, CHAIN_ID, scope_key]).unwrap();
        }
    }
    ///
    /// The reverse loop needs its own first and third hop pools, because the
    /// pools are part of the loop's identity: two loops that share a pool
    /// are not two independent loops.
    fn seed_the_reverse_direction(connection: &Connection) {
        let reverse_direction = format!("{}->{}", token(TA), token(AMZN));
        connection
            .execute(
                "INSERT INTO manager_market_observation_yz_pairs (
                    network, chain_id, protocol, scope_key, anchor_token_key_id,
                    direction_key, token_x_key_id, token_y_key_id,
                    has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
                    optimal_pool_id, observation_empty_reason, created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'candidate_exchange', ?3, ?4, ?5,
                           ?6, 1, 1, ?7, ?8, NULL, 'fixture', 'fixture')",
                params![
                    NETWORK,
                    CHAIN_ID,
                    token(USDG),
                    reverse_direction,
                    token(TA),
                    token(AMZN),
                    format!("[\"{POOL_YZ_REVERSE}\"]"),
                    POOL_YZ_REVERSE
                ],
            )
            .unwrap();
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
                           'fixture', 1, 'fixture', 'fixture')",
                params![
                    NETWORK,
                    CHAIN_ID,
                    token(USDG),
                    POOL_YZ_REVERSE,
                    token(TA),
                    token(AMZN)
                ],
            )
            .unwrap();
        // The first hop of the reverse loop is USDG -> TA, the third hop is
        // AMZN -> USDG. Both need their own pool so the reverse loop does
        // not share a pool with the forward loop.
        for (candidate, pool_id, hop) in [
            (TA, POOL_TA_FIRST, "first"),
            (AMZN, POOL_AMZN_THIRD, "third"),
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
                               1, ?8, NULL, 500, 'fixture', 'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        token(USDG),
                        token(candidate),
                        format!("pair:{}:{}", token(USDG), token(candidate)),
                        direction,
                        hop,
                        pool_id
                    ],
                )
                .unwrap();
        }
        // The reverse loop's own first and third hop pools have to exist
        // in the candidate pool library, otherwise the recommendation
        // cannot be resolved to a pool.
        for pool_id in [POOL_TA_FIRST, POOL_AMZN_THIRD] {
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
    }

    /// One funding anchor may carry two directed closed loops: X-Y-Z-X and
    /// X-Z-Y-X.  They are two independent loops, and the Manager has to
    /// publish them as two records: a different loop key, a different pool
    /// set, and the two middle directions pointing at each other.
    ///
    /// Merging them into one record would let a route that runs X-Y-Z-X be
    /// bounded by the capacity that was proved for X-Z-Y-X, so the
    /// separation is a correctness requirement, not a formatting one.
    #[test]
    fn one_anchor_publishes_two_independent_directed_closed_loops() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(
                &connection,
                &[
                    (POOL_A1, 1_000_000),
                    (POOL_YZ, 5_000),
                    (POOL_D, 1_000_000),
                    (POOL_TA_FIRST, 1_000_000),
                    (POOL_YZ_REVERSE, 6_000),
                    (POOL_AMZN_THIRD, 1_000_000),
                ],
            );
            seed_the_reverse_direction(&connection);
        }
        let dry_run = materialize_closed_loops(&path, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(
            dry_run.loop_count, 2,
            "one anchor with two directed loops publishes two loops"
        );

        let applied = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(applied.loop_count, 2);
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loops = snapshot.loops;
        assert_eq!(loops.len(), 2, "both loops are published");

        // The two records are distinct: a different key, and a different
        // pool set. A shared pool set would mean one loop published twice.
        let first = &loops[0];
        let second = &loops[1];
        assert_ne!(
            first.loop_key, second.loop_key,
            "the two directions must not share a loop key"
        );
        assert_ne!(
            first.directional_pool_ids, second.directional_pool_ids,
            "the two directions must not share a pool set"
        );
        assert_eq!(
            first.anchor_token_key_id, second.anchor_token_key_id,
            "both loops share the anchor"
        );

        // The two middle directions point at each other: Y->Z and Z->Y.
        let forward = loops
            .iter()
            .find(|row| row.token_y_key_id == token(AMZN) && row.token_z_key_id == token(TA))
            .expect("the forward X-Y-Z-X loop is published");
        let reverse = loops
            .iter()
            .find(|row| row.token_y_key_id == token(TA) && row.token_z_key_id == token(AMZN))
            .expect("the reverse X-Z-Y-X loop is published");
        assert_ne!(
            forward.loop_key, reverse.loop_key,
            "the two directions are two different closed loops"
        );
        assert_eq!(
            forward.path_addresses.first(),
            forward.path_addresses.last(),
            "the forward loop closes on its anchor"
        );
        assert_eq!(
            reverse.path_addresses.first(),
            reverse.path_addresses.last(),
            "the reverse loop closes on its anchor"
        );
        assert_eq!(
            forward.path_addresses[1], reverse.path_addresses[2],
            "the two directions swap their middle tokens"
        );
        assert_eq!(
            forward.path_addresses[2], reverse.path_addresses[1],
            "the two directions swap their middle tokens"
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
        // first arbitrage version cannot size a position through it.  The
        // backup of the hop is equally tight, so no combination of the
        // candidates of the hop can be sized.
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_single_tick_bounds(
                &connection,
                &[
                    (POOL_A1, Some(1_000_000), Some(1_000_000)),
                    (POOL_YZ, Some(0), Some(0)),
                    (POOL_YZ_2, Some(0), Some(0)),
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
        // The hops are resolved through the direction layer, exactly as the
        // materialization resolves them.
        let hop_directional_pool_ids = vec![
            resolve_hop_directional_pool(&input.directional_pools, HOP_SIDE_SCOPE_KEY, POOL_A1, &token(USDG), &token(AMZN)).unwrap().directional_pool_id,
            resolve_hop_directional_pool(&input.directional_pools, HOP_MIDDLE_SCOPE_KEY, POOL_YZ, &token(AMZN), &token(TA)).unwrap().directional_pool_id,
            resolve_hop_directional_pool(&input.directional_pools, HOP_SIDE_SCOPE_KEY, POOL_D, &token(TA), &token(USDG)).unwrap().directional_pool_id,
        ];
        let (minimum, binding) = path_min_capacity(&input.directional_pools, &hop_directional_pool_ids);
        assert_eq!(minimum, Some(1_200));
        // The blocking pool is named by its **directional** identity, so it
        // always carries the direction the hop trades in.
        assert_eq!(binding.as_deref(), Some(hop_directional_pool_ids[1].as_str()));
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
        assert_eq!(
            snapshot.loops[0]
                .directional_pool_ids
                .iter()
                .map(|id| base_pool_of(id).to_owned())
                .collect::<Vec<_>>(),
            [POOL_A1, POOL_YZ, POOL_D]
        );
        assert_eq!(
            snapshot.runtime_states[0].runtime_status,
            "ready_for_review"
        );
        assert!(!snapshot.loops[0].pool_path_key.contains("usd"));
    }

    /// A measurement restarts the review clock of the loop it measured.
    ///
    /// The clock belongs to the loop, so the catalog publishes it: the
    /// moment of the measurement and the moment the next round of the
    /// review may start.  A rebuild of the catalog publishes the topology
    /// again and must not reset that clock -- a loop that was measured
    /// does not become a loop that waits for a review.
    #[test]
    fn a_measurement_restarts_the_review_clock_of_the_loop() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        let measured_at = "2026-11-27T01:00:00+00:00";
        {
            let connection = Connection::open(&path).unwrap();
            assert!(record_review_schedule_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                measured_at,
                "measured",
            )
            .unwrap());
        }
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(state.last_reviewed_at.as_deref(), Some(measured_at));
        let due = state
            .next_review_at
            .as_deref()
            .expect("the loop is scheduled again after a measurement");
        assert!(
            due.starts_with(&chrono::Utc::now().to_rfc3339()[..13]),
            "the next round starts one interval after the round ended, got {due}",
        );
        let wait = state.review_wait_seconds.expect("the wait is counted");
        assert!(
            (review_interval_seconds() - 5..=review_interval_seconds() + 5).contains(&wait),
            "the loop waits the review interval, got {wait}",
        );
        assert!(!state.review_required);
        assert_eq!(
            state.runtime_status, "cooldown",
            "the clock of the loop is running after a measurement",
        );

        let scheduled = state.next_review_at.clone();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(state.last_reviewed_at.as_deref(), Some(measured_at));
        assert_eq!(
            state.next_review_at, scheduled,
            "a rebuild keeps the review time point of the loop",
        );
        assert!(
            !state.review_required,
            "a rebuild publishes the topology, it does not ask for a review again"
        );
        assert_eq!(
            state.runtime_status, "cooldown",
            "a rebuild keeps the state of the loop",
        );
    }

    /// A loop whose measurement went stale waits for a new one.
    ///
    /// The moment of the last measurement stays: it says how old the
    /// measurement is.  The review time point of the loop becomes the
    /// moment of the request, because the loop is due **now**.
    #[test]
    fn a_stale_measurement_clears_the_schedule_of_the_loop() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        {
            let connection = Connection::open(&path).unwrap();
            record_review_schedule_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                "2026-11-27T01:00:00+00:00",
                "measured",
            )
            .unwrap();
            assert!(mark_review_required_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
            )
            .unwrap());
        }
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(
            state.last_reviewed_at.as_deref(),
            Some("2026-11-27T01:00:00+00:00")
        );
        let due = state
            .next_review_at
            .as_deref()
            .expect("a manual review schedules the loop");
        assert!(
            due.starts_with(&chrono::Utc::now().to_rfc3339()[..13]),
            "the manual review starts the next round at once, got {due}",
        );
        assert!(state.review_required);
        assert_eq!(
            state.runtime_status, "ready_for_review",
            "a loop that waits for a review is in the review state",
        );
        assert_eq!(state.review_wait_seconds, Some(0));
    }

    /// The review clock of a loop runs out and the loop is due again.
    ///
    /// The clock is the wait between two review rounds: it is started when
    /// the loop has been dealt with, it runs while the loop waits, and when
    /// it runs out the state of the loop becomes the review state and the
    /// next round of the review may start.
    #[test]
    fn the_review_clock_runs_out_and_the_loop_becomes_due_again() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        {
            let connection = Connection::open(&path).unwrap();
            assert!(start_review_clock_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                "broadcast_confirmed",
            )
            .unwrap());
        }
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(state.runtime_status, "cooldown");
        assert!(!state.review_required);
        let wait = state.review_wait_seconds.expect("the clock publishes its wait");
        assert!(
            (review_interval_seconds() - 5..=review_interval_seconds() + 5).contains(&wait),
            "the clock of a loop waits the interval, got {wait}",
        );

        // The clock runs out: the loop is due again, so the state of the
        // loop becomes the review state and it waits for nothing.
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "UPDATE manager_v4_closed_loop_runtime_state
                        SET next_review_at = '2026-11-27T00:00:00+00:00'
                      WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                    params![NETWORK, CHAIN_ID, loop_key],
                )
                .unwrap();
            assert_eq!(
                advance_review_clock_on_connection(&connection, NETWORK, CHAIN_ID, &loop_key)
                    .unwrap(),
                0,
                "a clock that ran out leaves nothing to wait for",
            );
        }
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(state.runtime_status, "ready_for_review");
        assert!(state.review_required);
        assert_eq!(state.review_wait_seconds, Some(0));
        assert_eq!(
            state.next_review_at.as_deref(),
            Some("2026-11-27T00:00:00+00:00"),
            "the moment the loop was scheduled at stays: it says how long it waited",
        );
    }

    /// A manual review sets the wait of the loop to zero.
    ///
    /// A manual review asks for the next round of the review at once, so
    /// the loop waits for nothing: the clock is not started, the loop is
    /// due.
    #[test]
    fn a_manual_review_sets_the_wait_of_the_loop_to_zero() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        assert!(set_review_wait(&path, NETWORK, CHAIN_ID, &loop_key, 0).unwrap());
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(state.runtime_status, "ready_for_review");
        assert_eq!(state.review_wait_seconds, Some(0));
        assert!(state.review_required);
        let due = state
            .next_review_at
            .as_deref()
            .expect("a manual review schedules the loop");
        assert!(
            due.starts_with(&chrono::Utc::now().to_rfc3339()[..13]),
            "the manual review starts the next round at once, got {due}",
        );
    }

    /// The round that ends schedules the next round one interval later.
    ///
    /// The loop keeps one time point: the moment its next round of the
    /// review may start.  A round that ends -- a measurement that found no
    /// profit, or a broadcast that confirmed one -- resets that moment
    /// to one review interval after the moment the round ended.
    #[test]
    fn the_round_that_ends_schedules_the_next_round_one_interval_later() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        {
            let connection = Connection::open(&path).unwrap();
            assert!(begin_review_cycle_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                "review_round_started",
            )
            .unwrap());
            let (status, due, started): (String, Option<String>, Option<String>) = connection
                .query_row(
                    "SELECT runtime_status, next_review_at, review_cycle_started_at
                       FROM manager_v4_closed_loop_runtime_state
                      WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                    params![NETWORK, CHAIN_ID, loop_key],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .unwrap();
            assert_eq!(status, "review_pending");
            assert!(started.is_some(), "the round publishes when it started");
            assert!(due.is_some(), "the loop keeps its review time point");
        }
        // The round ends with a measurement.
        {
            let connection = Connection::open(&path).unwrap();
            assert!(record_review_schedule_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                &chrono::Utc::now().to_rfc3339(),
                "measured_without_profit",
            )
            .unwrap());
        }
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(state.runtime_status, "cooldown");
        let wait = state.review_wait_seconds.expect("the wait is counted");
        assert!(
            (review_interval_seconds() - 5..=review_interval_seconds() + 5).contains(&wait),
            "the next round starts one interval after the round ended, got {wait}",
        );
    }

    /// A loop is claimed once per round.
    ///
    /// The claim is the lock of the loop: a round that is already running
    /// is not claimed again, and a loop that still waits for its clock is
    /// not due and not claimed either.
    #[test]
    fn a_loop_is_claimed_once_per_round() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        assert!(begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
        assert!(
            !begin_review_cycle_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                "review_round_started",
            )
            .unwrap(),
            "a round that is already running is not claimed again",
        );
        // The round ends and the loop waits for the next round.
        assert!(record_review_schedule_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            &chrono::Utc::now().to_rfc3339(),
            "measured_without_profit",
        )
        .unwrap());
        assert!(
            !begin_review_cycle_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                "review_round_started",
            )
            .unwrap(),
            "a loop that still waits for its clock is not due",
        );
    }

    /// The next round starts when the cycle ran out.
    ///
    /// The review time point of the loop passes, the loop becomes due
    /// again, and the round it ran is cleared: the next round starts a
    /// new one.
    #[test]
    fn the_next_round_starts_when_the_cycle_ran_out() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        assert!(begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
        assert!(record_review_schedule_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            &chrono::Utc::now().to_rfc3339(),
            "measured_without_profit",
        )
        .unwrap());
        // The cycle runs out.
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_runtime_state
                    SET next_review_at = '2026-11-27T00:00:00+00:00'
                  WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, loop_key],
            )
            .unwrap();
        assert_eq!(
            advance_review_clock_on_connection(&connection, NETWORK, CHAIN_ID, &loop_key)
                .unwrap(),
            0,
        );
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let state = &snapshot.runtime_states[0];
        assert_eq!(state.runtime_status, "ready_for_review");
        assert!(state.review_required);
        assert_eq!(state.review_cycle_started_at, None);
        assert_eq!(
            state.next_review_at.as_deref(),
            Some("2026-11-27T00:00:00+00:00"),
            "the moment the loop was scheduled at stays: it says how long it waited",
        );
        // The loop is due, so the next round may claim it.
        assert!(begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
    }
    /// The state of a closed loop, as the store holds it.
    fn runtime_state_of(
        connection: &Connection,
        loop_key: &str,
    ) -> (String, Option<String>, Option<String>) {
        connection
            .query_row(
                "SELECT runtime_status, next_review_at, review_cycle_started_at
                   FROM manager_v4_closed_loop_runtime_state
                  WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                    AND scope_key = ?4 AND loop_key = ?5",
                params![
                    NETWORK,
                    CHAIN_ID,
                    NETWORK_PROTOCOL,
                    CLOSED_LOOP_SCOPE_KEY,
                    loop_key
                ],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap()
    }

    /// An entry of the review queue, built by hand.
    fn review_slot(loop_key: &str, due_at: Option<&str>, is_sentinel: bool) -> ReviewSlot {
        let due_epoch = if is_sentinel {
            i64::MAX
        } else {
            due_at.and_then(moment_epoch).unwrap_or(i64::MIN)
        };
        ReviewSlot {
            loop_key: loop_key.to_owned(),
            runtime_status: RUNTIME_STATUS_COOLDOWN.to_owned(),
            due_at: due_at.map(|value| value.to_owned()),
            due_epoch,
            is_sentinel,
            review_cycle_started_at: None,
        }
    }

    /// A loop that is being measured is parked at the review sentinel.
    ///
    /// The loop has no scheduled review while its round runs, so the
    /// queue must not offer it again.  The marker says the loop is busy,
    /// and the state of the loop says which busy it is.
    #[test]
    fn a_claimed_loop_is_parked_at_the_review_sentinel() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        assert!(begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
        let (status, due_at, cycle_started_at) = runtime_state_of(&connection, &loop_key);
        assert_eq!(status, RUNTIME_STATUS_REVIEW_PENDING);
        assert_eq!(due_at.as_deref(), Some(REVIEW_PENDING_SENTINEL_AT));
        assert!(cycle_started_at.is_some(), "the round keeps its start moment");
        // The loop is not claimed a second time while its round runs.
        assert!(!begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
    }

    /// A loop that waits for the receipt of its trade is parked at the
    /// broadcast sentinel.
    #[test]
    fn a_loop_that_waits_for_its_receipt_is_parked_at_the_broadcast_sentinel() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        assert!(reset_review_start_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            ReviewStartReset::BroadcastPending,
            None,
            "broadcast_sent",
        )
        .unwrap());
        let (status, due_at, _) = runtime_state_of(&connection, &loop_key);
        assert_eq!(status, RUNTIME_STATUS_BROADCAST_PENDING);
        assert_eq!(due_at.as_deref(), Some(BROADCAST_PENDING_SENTINEL_AT));
        // A loop that waits for its receipt is not claimed for a review.
        assert!(!begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
    }

    /// The two sentinels tell the two busy situations apart.
    #[test]
    fn the_two_sentinels_tell_the_two_busy_situations_apart() {
        assert_ne!(REVIEW_PENDING_SENTINEL_AT, BROADCAST_PENDING_SENTINEL_AT);
        assert_ne!(
            REVIEW_PENDING_SENTINEL_EPOCH,
            BROADCAST_PENDING_SENTINEL_EPOCH
        );
        assert!(is_review_sentinel(REVIEW_PENDING_SENTINEL_AT));
        assert!(is_review_sentinel(BROADCAST_PENDING_SENTINEL_AT));
        assert!(!is_review_sentinel("2026-11-27T00:00:00+00:00"));
        assert_eq!(
            review_sentinel_for_status(RUNTIME_STATUS_REVIEW_PENDING),
            Some(REVIEW_PENDING_SENTINEL_AT)
        );
        assert_eq!(
            review_sentinel_for_status(RUNTIME_STATUS_BROADCAST_PENDING),
            Some(BROADCAST_PENDING_SENTINEL_AT)
        );
        assert_eq!(review_sentinel_for_status(RUNTIME_STATUS_COOLDOWN), None);
        // Both sentinels fit an i32, so a reader that counts in i32 does
        // not overflow on them.
        assert!(REVIEW_PENDING_SENTINEL_EPOCH <= i32::MAX as i64);
        assert!(BROADCAST_PENDING_SENTINEL_EPOCH <= i32::MAX as i64);
    }

    /// A writer **waits** for the lock instead of failing at once.
    ///
    /// The runtime measures a loop in one connection while the Manager writes
    /// the result in another, and the two meet on the same file.  A writer
    /// that does not wait reports "database is locked" the moment the other
    /// one holds the lock, and the request is lost -- a manual review that
    /// never starts, for no reason but a lock.
    #[test]
    fn a_writer_waits_for_the_lock_instead_of_failing_at_once() {
        let (_directory, path) = database();
        let holder = open_connection(&path, true).expect("the store opens");
        holder
            .execute_batch("BEGIN EXCLUSIVE")
            .expect("the holder takes the write lock");
        let waiter_path = path.clone();
        let waiter = std::thread::spawn(move || {
            let connection = open_connection(&waiter_path, true).expect("the store opens");
            connection
                .execute_batch("BEGIN IMMEDIATE")
                .map(|_| true)
                .unwrap_or(false)
        });
        // The waiter waits for the lock: it has not given up yet.
        std::thread::sleep(std::time::Duration::from_millis(300));
        assert!(
            !waiter.is_finished(),
            "the writer waits for the lock instead of failing at once"
        );
        holder
            .execute_batch("ROLLBACK")
            .expect("the holder releases the lock");
        assert!(
            waiter.join().expect("the waiter finishes"),
            "the writer gets the lock once it is free"
        );
    }
    /// The queue is ordered by the moment a loop is scheduled at.
    #[test]
    fn the_review_queue_is_ordered_by_the_review_moment() {
        let mut queue = ReviewQueue::default();
        for (loop_key, moment) in [
            ("loop-c", "2026-11-27T00:30:00+00:00"),
            ("loop-a", "2026-11-27T00:10:00+00:00"),
            ("loop-b", "2026-11-27T00:20:00+00:00"),
        ] {
            queue.insert(review_slot(loop_key, Some(moment), false));
        }
        let order: Vec<&str> = queue.iter().map(|slot| slot.loop_key.as_str()).collect();
        assert_eq!(order, vec!["loop-a", "loop-b", "loop-c"]);
        assert_eq!(queue.first().unwrap().loop_key, "loop-a");
        assert_eq!(queue.len(), 3);
    }

    /// One entry per loop: the second result covers the first one.
    #[test]
    fn a_loop_is_covered_by_one_entry() {
        let mut queue = ReviewQueue::default();
        queue.insert(review_slot("loop-a", Some("2026-11-27T00:10:00+00:00"), false));
        queue.insert(review_slot("loop-a", Some("2026-11-27T00:40:00+00:00"), false));
        assert_eq!(queue.len(), 1);
        let entry = queue.get("loop-a").unwrap();
        assert_eq!(
            entry.due_at.as_deref(),
            Some("2026-11-27T00:40:00+00:00"),
            "the second result covers the first one"
        );
        assert_eq!(queue.first().unwrap().loop_key, "loop-a");
    }

    /// The walk stops at the first loop that is not due.
    #[test]
    fn the_walk_stops_at_the_first_loop_that_is_not_due() {
        let mut queue = ReviewQueue::default();
        queue.insert(review_slot("loop-a", Some("2026-11-27T00:10:00+00:00"), false));
        queue.insert(review_slot("loop-b", Some("2026-11-27T00:20:00+00:00"), false));
        queue.insert(review_slot("loop-c", Some("2026-11-27T00:30:00+00:00"), false));
        let now = moment_epoch("2026-11-27T00:15:00+00:00").unwrap();
        let due: Vec<&str> = queue
            .due_entries(now)
            .into_iter()
            .map(|slot| slot.loop_key.as_str())
            .collect();
        assert_eq!(due, vec!["loop-a"]);
        let now = moment_epoch("2026-11-27T00:25:00+00:00").unwrap();
        let due: Vec<&str> = queue
            .due_entries(now)
            .into_iter()
            .map(|slot| slot.loop_key.as_str())
            .collect();
        assert_eq!(due, vec!["loop-a", "loop-b"]);
    }

    /// A sentinel is never due.
    #[test]
    fn a_sentinel_is_never_due() {
        let mut queue = ReviewQueue::default();
        queue.insert(review_slot(
            "loop-a",
            Some(REVIEW_PENDING_SENTINEL_AT),
            true,
        ));
        queue.insert(review_slot(
            "loop-b",
            Some(BROADCAST_PENDING_SENTINEL_AT),
            true,
        ));
        assert!(queue.due_entries(i64::MAX).is_empty());
        assert!(queue.take_due(i64::MAX).is_empty());
        assert_eq!(queue.len(), 2);
        // A sentinel sorts last, so it never hides a loop that is due.
        queue.insert(review_slot(
            "loop-c",
            Some("2026-11-27T00:10:00+00:00"),
            false,
        ));
        let due: Vec<&str> = queue
            .due_entries(i64::MAX)
            .into_iter()
            .map(|slot| slot.loop_key.as_str())
            .collect();
        assert_eq!(due, vec!["loop-c"]);
    }

    /// The queue is read from the store.
    #[test]
    fn the_queue_is_read_from_the_store() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        assert!(begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
        let queue = load_review_queue_on_connection(&connection, NETWORK, CHAIN_ID).unwrap();
        let entry = queue.get(&loop_key).unwrap();
        assert!(entry.is_sentinel);
        assert_eq!(entry.due_epoch, i64::MAX);
        assert_eq!(entry.due_at.as_deref(), Some(REVIEW_PENDING_SENTINEL_AT));
        assert!(queue
            .due_entries(i64::MAX)
            .iter()
            .all(|slot| slot.loop_key != loop_key));
        // A loop that was never measured has no review time point, so it
        // is due at once.
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_runtime_state
                    SET next_review_at = NULL
                  WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, loop_key],
            )
            .unwrap();
        let queue = read_review_queue(&path, NETWORK, CHAIN_ID).unwrap();
        let entry = queue.get(&loop_key).unwrap();
        assert!(!entry.is_sentinel);
        assert_eq!(entry.due_epoch, i64::MIN);
    }

    /// The review interval is read from the configuration parameters.
    ///
    /// Put a closed loop into the runtime state by hand.
    fn insert_runtime_state(
        connection: &Connection,
        loop_key: &str,
        runtime_status: &str,
        next_review_at: Option<&str>,
    ) {
        connection
            .execute(
                "INSERT INTO manager_v4_closed_loop_runtime_state (
                     network, chain_id, protocol, scope_key, loop_key,
                     runtime_status, status_reason, review_generation,
                     review_required, last_rebuilt_at, updated_at,
                     last_reviewed_at, next_review_at, review_cycle_started_at
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'test', 0, 0, ?7, ?7,
                           NULL, ?8, NULL)",
                params![
                    NETWORK,
                    CHAIN_ID,
                    NETWORK_PROTOCOL,
                    CLOSED_LOOP_SCOPE_KEY,
                    loop_key,
                    runtime_status,
                    "2026-11-27T00:00:00+00:00",
                    next_review_at
                ],
            )
            .unwrap();
    }

    /// The queue and the store agree on the loops that are due.
    ///
    /// The due set is computed **twice**: once by the store and once by
    /// the queue.  The two answers have to be the same, otherwise the
    /// main thread of the review would work from a different list than
    /// the one the store published.
    #[test]
    fn the_queue_and_the_store_agree_on_the_due_loops() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let seeded = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        // The seeded loop waits, so it never shows up in the due set.
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_runtime_state
                    SET runtime_status = 'cooldown',
                        next_review_at = '2099-01-01T00:00:00+00:00'
                  WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, seeded],
            )
            .unwrap();
        // One loop per situation.
        insert_runtime_state(&connection, "loop-due-null", "ready_for_review", None);
        insert_runtime_state(
            &connection,
            "loop-due-past",
            "cooldown",
            Some("2026-01-01T00:00:00+00:00"),
        );
        insert_runtime_state(
            &connection,
            "loop-waits",
            "cooldown",
            Some("2099-01-01T00:00:00+00:00"),
        );
        insert_runtime_state(
            &connection,
            "loop-measured",
            RUNTIME_STATUS_REVIEW_PENDING,
            Some(REVIEW_PENDING_SENTINEL_AT),
        );
        insert_runtime_state(
            &connection,
            "loop-broadcast",
            RUNTIME_STATUS_BROADCAST_PENDING,
            Some(BROADCAST_PENDING_SENTINEL_AT),
        );
        insert_runtime_state(
            &connection,
            "loop-retired",
            RUNTIME_STATUS_RETIRED,
            Some("2026-01-01T00:00:00+00:00"),
        );
        // A moment that is not written in RFC 3339 is **due now** on both
        // sides: SQLite reads a few shapes more than the store does, so
        // the shape is checked on purpose.
        insert_runtime_state(
            &connection,
            "loop-loose-shape",
            "cooldown",
            Some("2099-01-01 00:00:00"),
        );
        let due_from_store: Vec<String> = read_due_review_loops(&path, NETWORK, CHAIN_ID)
            .unwrap()
            .into_iter()
            .map(|row| row.loop_key)
            .collect();
        let queue = read_review_queue(&path, NETWORK, CHAIN_ID).unwrap();
        let due_from_queue: Vec<String> = queue
            .due_entries(chrono::Utc::now().timestamp())
            .into_iter()
            .map(|slot| slot.loop_key.clone())
            .collect();
        assert_eq!(
            due_from_queue,
            vec!["loop-due-null", "loop-loose-shape", "loop-due-past"]
        );
        assert_eq!(
            due_from_store, due_from_queue,
            "the queue and the store have to name the same due loops"
        );
        // A retired loop is out of the queue, and a parked loop is in it
        // but never due.
        assert!(queue.get("loop-retired").is_none());
        assert!(queue.get("loop-measured").unwrap().is_sentinel);
        assert!(queue.get("loop-broadcast").unwrap().is_sentinel);
        assert_eq!(queue.len(), 7);
        assert_eq!(
            count_waiting_review_loops(&path, NETWORK, CHAIN_ID).unwrap(),
            4
        );
    }

    /// The resident queue carries the fingerprint of the store.
    ///
    /// The fingerprint is what tells a resident copy whether it may still
    /// be served: every write that touches the schedule of a loop bumps
    /// the moment the row was updated, so the fingerprint follows the
    /// store.
    #[test]
    fn the_resident_queue_carries_the_fingerprint_of_the_store() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let resident = read_resident_review_queue(&path, NETWORK, CHAIN_ID).unwrap();
        assert!(!resident.fingerprint.is_empty());
        assert_eq!(
            resident.fingerprint,
            read_review_queue_fingerprint(&path, NETWORK, CHAIN_ID).unwrap(),
            "the fingerprint and the queue are two views of the same moment",
        );
        assert_eq!(
            resident.queue.len(),
            read_review_queue(&path, NETWORK, CHAIN_ID).unwrap().len(),
            "the resident queue holds the loops of the scope",
        );
        // A write that touches the schedule of a loop changes the
        // fingerprint, so a resident copy that fell behind is noticed.
        let connection = Connection::open(&path).unwrap();
        let loop_key = resident.queue.first().unwrap().loop_key.clone();
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_runtime_state
                    SET updated_at = '2099-01-01T00:00:00+00:00'
                  WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, loop_key],
            )
            .unwrap();
        assert_ne!(
            resident.fingerprint,
            read_review_queue_fingerprint(&path, NETWORK, CHAIN_ID).unwrap(),
            "the fingerprint follows the store",
        );
    }

    /// The queue and the store agree on the due loops **to the second**.
    ///
    /// The two answers are computed twice: once by the store and once by
    /// the queue.  The store compares the moments with the **epoch
    /// second** they fall into, and the queue counts in epoch seconds as
    /// well, so a loop that is due in one of them is due in the other --
    /// even when the two moments carry different fractions of a second.
    #[test]
    fn the_queue_and_the_store_agree_on_the_due_loops_to_the_second() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let seeded = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_runtime_state
                    SET runtime_status = 'cooldown',
                        next_review_at = '2099-01-01T00:00:00+00:00'
                  WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, seeded],
            )
            .unwrap();
        // The moment is anchored to the **start** of the current second, and
        // the fraction is a quarter of a second: a moment that falls in the
        // same second as the moment of the test, whatever the fraction of
        // the second the test happens to run in.  Anchoring at the start of
        // the second is what makes the assertion hold on every run -- a
        // moment taken half a second after "now" lands in the **next**
        // second half of the time, and the two answers then disagree for a
        // reason that has nothing to do with the precision under test.
        let now = chrono::Utc::now();
        let start_of_second = now.with_nanosecond(0).unwrap();
        let same_second = (start_of_second + chrono::Duration::milliseconds(250))
            .to_rfc3339_opts(chrono::SecondsFormat::Nanos, true);
        insert_runtime_state(&connection, "loop-same-second", "cooldown", Some(&same_second));
        let due_from_store: Vec<String> = read_due_review_loops(&path, NETWORK, CHAIN_ID)
            .unwrap()
            .into_iter()
            .map(|row| row.loop_key)
            .collect();
        let queue = read_review_queue(&path, NETWORK, CHAIN_ID).unwrap();
        // The same moment is used on **both** sides: the store reads its own
        // "now" when it is asked, and the queue is asked about the moment
        // the test took, so the two answers are about the same second.
        let due_from_queue: Vec<String> = queue
            .due_entries(now.timestamp())
            .into_iter()
            .map(|slot| slot.loop_key.clone())
            .collect();
        assert_eq!(
            due_from_store, due_from_queue,
            "the queue and the store have to name the same due loops",
        );
        assert!(
            due_from_queue.contains(&"loop-same-second".to_owned()),
            "a loop that falls into the second of the moment is due, got {due_from_queue:?}",
        );
    }

    /// The walk over the queue stops at the first loop that is not due.
    ///
    /// The number of entries the walk touched is the cost of one round:
    /// it is the number of loops that are due and **one** more, not the
    /// number of loops the scope holds.
    #[test]
    fn the_walk_over_the_queue_touches_the_due_loops_and_one_more() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let seeded = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_runtime_state
                    SET runtime_status = 'cooldown',
                        next_review_at = '2099-01-01T00:00:00+00:00'
                  WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, seeded],
            )
            .unwrap();
        insert_runtime_state(
            &connection,
            "loop-due-first",
            "cooldown",
            Some("2026-01-01T00:00:00+00:00"),
        );
        insert_runtime_state(
            &connection,
            "loop-due-second",
            "cooldown",
            Some("2026-01-02T00:00:00+00:00"),
        );
        insert_runtime_state(
            &connection,
            "loop-waits",
            "cooldown",
            Some("2099-01-01T00:00:00+00:00"),
        );
        let queue = read_review_queue(&path, NETWORK, CHAIN_ID).unwrap();
        let now = chrono::Utc::now().timestamp();
        assert_eq!(
            review_queue_scan_cost(&queue, now),
            3,
            "the walk touches the due loops and the first one that is not due",
        );
        assert_eq!(queue.due_entries(now).len(), 2);
    }

    /// The queue is published as a view.
    #[test]
    fn the_queue_is_published_as_a_view() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        let connection = Connection::open(&path).unwrap();
        assert!(begin_review_cycle_on_connection(
            &connection,
            NETWORK,
            CHAIN_ID,
            &loop_key,
            "review_round_started",
        )
        .unwrap());
        let view = read_review_queue_view(&path, NETWORK, CHAIN_ID).unwrap();
        assert_eq!(view.length, 1);
        assert_eq!(view.due_count, 0, "a parked loop is never due");
        assert_eq!(view.waiting_count, 1);
        assert_eq!(view.next_due_in_seconds, 0);
        assert_eq!(view.next_due_at.as_deref(), None);
        let entry = &view.entries[0];
        assert_eq!(entry.loop_key, loop_key);
        assert_eq!(entry.runtime_status, RUNTIME_STATUS_REVIEW_PENDING);
        assert!(entry.is_sentinel);
        assert_eq!(entry.next_review_at.as_deref(), Some(REVIEW_PENDING_SENTINEL_AT));
        assert_eq!(entry.review_wait_seconds, 0);
    }
    /// The interval is a parameter, so it is read from the store the
    /// parameters live in and a changed value is in force on the next
    /// cycle -- without a restart of the process.
    #[test]
    fn the_review_interval_is_read_from_the_configuration_parameters() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        // The parameter store of the running system keeps the value inside
        // a JSON document, so a parameter is read in two steps.
        connection
            .execute(
                "CREATE TABLE IF NOT EXISTS kv_entries (
                     collection TEXT NOT NULL,
                     key TEXT NOT NULL,
                     value_json TEXT NOT NULL,
                     updated_at TEXT NOT NULL,
                     PRIMARY KEY (collection, key)
                 )",
                [],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO kv_entries (collection, key, value_json, updated_at)
                 VALUES ('parameters', ?1, ?2, 'fixture')",
                params![
                    format!(
                        "{}|{}",
                        REVIEW_INTERVAL_PARAMETER_NAMESPACE,
                        REVIEW_INTERVAL_PARAMETER_KEY
                    ),
                    r#"{"namespace":"strategy_config","parameter_key":"CLOSED_LOOP_REVIEW_INTERVAL_SECONDS","value_json":"30"}"#,
                ],
            )
            .unwrap();
        assert_eq!(review_interval_seconds_on_connection(&connection), 30);
        drop(connection);

        // The clock of a loop follows the parameter.
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let loop_key = snapshot.loops[0].loop_key.clone();
        {
            let connection = Connection::open(&path).unwrap();
            assert!(start_review_clock_on_connection(
                &connection,
                NETWORK,
                CHAIN_ID,
                &loop_key,
                "broadcast_confirmed",
            )
            .unwrap());
        }
        let snapshot = read_closed_loop_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let wait = snapshot.runtime_states[0]
            .review_wait_seconds
            .expect("the clock publishes its wait");
        assert!(
            (25..=30).contains(&wait),
            "the clock follows the parameter, got {wait}",
        );
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
        // The binding pool is one of the **directional** pools of the loop,
        // so the capacity always names a direction and not a bare pool.
        let binding = loop_row
            .single_tick_capacity_binding_directional_pool_id
            .as_ref()
            .expect("the binding pool of the capacity must be published");
        assert!(
            loop_row.directional_pool_ids.contains(binding),
            "the binding pool must be one of the directional pools of the loop: \
             binding={binding:?} pools={:?}",
            loop_row.directional_pool_ids
        );
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
        assert_eq!(
            loop_row.single_tick_capacity_binding_directional_pool_id,
            None
        );
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
        // The second run found the library unchanged, so it did not move the
        // generation.  It says nothing about the consumers, though: nobody
        // has told the Manager that they rebuilt, so the flag stays set.
        assert!(
            read_closed_loop_status(&path, NETWORK, CHAIN_ID)
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

    /// The flag says that the consumers still have to rebuild.  Only the
    /// consumer knows when it rebuilt, so only the consumer may clear the
    /// flag, and it clears it by naming the generation it rebuilt against.
    #[test]
    fn an_acknowledgement_of_the_current_generation_clears_the_flag() {
        let (_directory, path) = database();
        let first = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert!(
            read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild
        );

        let cleared = acknowledge_consumers_rebuilt(
            &path,
            NETWORK,
            CHAIN_ID,
            first.closed_loop_version,
            Some(&first.materialized_checksum),
        )
        .unwrap();
        assert!(cleared);
        assert!(
            !read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild
        );
    }

    /// An acknowledgement of a generation the library does not publish
    /// clears nothing: the consumers are behind, and a flag that a stale
    /// acknowledgement clears is a flag that lies.
    #[test]
    fn an_acknowledgement_of_another_generation_clears_nothing() {
        let (_directory, path) = database();
        let first = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();

        assert!(!acknowledge_consumers_rebuilt(
            &path,
            NETWORK,
            CHAIN_ID,
            first.closed_loop_version + 1,
            Some(&first.materialized_checksum),
        )
        .unwrap());
        assert!(!acknowledge_consumers_rebuilt(
            &path,
            NETWORK,
            CHAIN_ID,
            first.closed_loop_version,
            Some("sha256:another-checksum"),
        )
        .unwrap());
        assert!(
            read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild,
            "the flag must survive an acknowledgement that names another \
             generation: the consumers are still behind"
        );
    }

    /// A library that moved on after the measurement leaves the flag set.
    /// The acknowledgement of the generation the consumers rebuilt against
    /// says nothing about the generation the library publishes now.
    #[test]
    fn an_acknowledgement_of_a_generation_the_library_left_clears_nothing() {
        let (_directory, path) = database();
        let first = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert!(
            acknowledge_consumers_rebuilt(
                &path,
                NETWORK,
                CHAIN_ID,
                first.closed_loop_version,
                Some(&first.materialized_checksum),
            )
            .unwrap()
        );

        // The market moved: the library publishes another generation, so the
        // consumers have to rebuild again.
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "DELETE FROM manager_dex_candidate_pool_recommendations
                     WHERE network = ?1 AND chain_id = ?2 AND pool_id = ?3",
                    params![NETWORK, CHAIN_ID, POOL_A2],
                )
                .unwrap();
        }
        let second = materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_ne!(second.closed_loop_version, first.closed_loop_version);
        assert!(
            read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild
        );

        assert!(!acknowledge_consumers_rebuilt(
            &path,
            NETWORK,
            CHAIN_ID,
            first.closed_loop_version,
            Some(&first.materialized_checksum),
        )
        .unwrap());
        assert!(
            read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild,
            "the flag must survive the acknowledgement of the generation the \
             library has already left"
        );
        assert!(acknowledge_consumers_rebuilt(
            &path,
            NETWORK,
            CHAIN_ID,
            second.closed_loop_version,
            Some(&second.materialized_checksum),
        )
        .unwrap());
        assert!(
            !read_closed_loop_status(&path, NETWORK, CHAIN_ID)
                .unwrap()
                .consumers_need_rebuild
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
            // The basis of a hop names the authority that picked the pool.
            // Under the direction layer that authority is the rank of the
            // pool inside its `(pair, direction)` group, so the legacy rows
            // of the fixture carry the same label as the manager rows.
            (1, "yc", USDG, AMZN, POOL_A1, "directional_pair_direction_rank:1"),
            (
                2,
                "y-z",
                AMZN,
                TA,
                POOL_YZ,
                "directional_pair_direction_rank:1",
            ),
            (3, "zc", TA, USDG, POOL_D, "directional_pair_direction_rank:1"),
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
                 SET directional_pool_id = 'dpool-stale'
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
                   AND scope_key = ?4 AND directional_pool_id = 'dpool-stale'",
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

    /// A directional pool index that carries only what the fold needs.
    ///
    /// The entries are `(base_pool_id, sqrt_price_x96, token0, token1,
    /// direction)`. Every base pool is projected onto **both** of its
    /// directions, exactly as the direction layer publishes it, so the row
    /// a hop walks is the one whose input token is the token the hop spends.
    fn directional_index(
        scope_key: &str,
        pools: &[(&str, u128, &str, &str, i64)],
    ) -> DirectionalPoolIndex {
        directional_index_with_prices(
            scope_key,
            &pools
                .iter()
                .map(|(base, price, token0, token1, direction)| {
                    (*base, Some(*price), *token0, *token1, *direction)
                })
                .collect::<Vec<_>>(),
        )
    }

    /// The direction layer rows of a scope, with the price each row
    /// publishes.
    ///
    /// A row that publishes no price is a **coverage gap** of the pool
    /// index, and the gap has to be expressible: the capacity fold reads a
    /// row without a price as no room, never as a price of zero.
    fn directional_index_with_prices(
        scope_key: &str,
        pools: &[(&str, Option<u128>, &str, &str, i64)],
    ) -> DirectionalPoolIndex {
        let mut index = DirectionalPoolIndex::default();
        for (base_pool_id, sqrt_price_x96, token0, token1, direction) in pools {
            let direction = PoolDirection::from_raw(*direction).expect("direction must be 0 or 1");
            let directional_pool_id = format!("{scope_key}:{base_pool_id}:{direction}");
            let mut pool = DirectionalPool::of_pool(
                V4Pool {
                    base_pool_id: (*base_pool_id).to_owned(),
                    pool_manager: "0xpoolmanager".to_owned(),
                    pool_key_json: "{}".to_owned(),
                    token0: (*token0).to_owned(),
                    token1: (*token1).to_owned(),
                    token0_key_id: (*token0).to_owned(),
                    token1_key_id: (*token1).to_owned(),
                    fee: 3000,
                    tick_spacing: 60,
                    hooks: "0x0".to_owned(),
                    sqrt_price_x96: sqrt_price_x96.map(|price| price.to_string()),
                    tick: Some(0),
                    liquidity: Some("1000000000000".to_owned()),
                    depth_capacity_raw: Some("1000000000000".to_owned()),
                },
                direction,
            );
            pool.input_capacity_raw = Some("1000000000000".to_owned());
            pool.input_capacity_measured = true;
            pool.directional_output = Some(1.0);
            pool.directional_output_measured = true;
            pool.pair_direction_rank = Some(1);
            pool.pool_status = "active".to_owned();
            index
                .by_base
                .entry((scope_key.to_owned(), (*base_pool_id).to_owned()))
                .or_default()
                .push(directional_pool_id.clone());
            index
                .scope_of
                .insert(directional_pool_id.clone(), scope_key.to_owned());
            index.rank_of.insert(directional_pool_id.clone(), 1);
            index.pools.insert(directional_pool_id, pool);
        }
        index
    }

    /// The fold as it was implemented **before** the direction layer.
    ///
    /// The reference implementation is kept verbatim on purpose: the
    /// equivalence of the refactor is the claim that the same tick state
    /// produces the same folded capacity, no matter whether the hop's
    /// direction is read from a parallel array or from the row the hop
    /// walks. The reference takes the pool state and a parallel direction
    /// array; the implementation under test takes the directional rows.
    fn legacy_folded_path_capacity(
        pool_tick_states: &BTreeMap<String, LegacyTickState>,
        pool_ids: &[String],
        directions: &[bool],
    ) -> Option<(u128, String)> {
        if pool_ids.is_empty() || pool_ids.len() != directions.len() {
            return None;
        }
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
        let anchor_token = hops.first()?.token_in.clone();
        let capacity = pool_manager::single_tick_capacity::compute_loop_capacity(
            &anchor_token,
            &hops,
        )
        .ok()?;
        Some((capacity.capacity_anchor_raw, capacity.binding_pool_id))
    }

    /// The pool state the legacy fold read.
    #[derive(Debug, Clone)]
    struct LegacyTickState {
        sqrt_price_x96: u128,
        liquidity: u128,
        fee_pips: u32,
        tick_spacing: i32,
        tick: i32,
        token0_key_id: String,
        token1_key_id: String,
    }

    /// A legacy pool state map that carries only what the fold needs.
    fn legacy_tick_state_map(
        states: &[(&str, u128, &str, &str)],
    ) -> BTreeMap<String, LegacyTickState> {
        let mut pool_tick_states = BTreeMap::new();
        for (pool_id, sqrt_price_x96, token0, token1) in states {
            pool_tick_states.insert(
                (*pool_id).to_owned(),
                LegacyTickState {
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
        // The first hop spends token0 (USDG is token0 of pool-a), the
        // second hop spends token0 (AMZN is token0 of pool-b), and the
        // third hop spends token1 (USDG is token1 of pool-c).
        let index = directional_index(
            "core4",
            &[
                ("pool-a", 2 * q96, USDG, AMZN, 1),
                ("pool-b", q96, AMZN, TA, 1),
                ("pool-c", q96, TA, USDG, 0),
            ],
        );
        let hop_directional_pool_ids = vec![
            "core4:pool-a:1".to_owned(),
            "core4:pool-b:1".to_owned(),
            "core4:pool-c:0".to_owned(),
        ];
        let (capacity, blocking) = folded_path_capacity(&index, &hop_directional_pool_ids)
            .expect("the capacity must be computable");
        assert!(capacity > 0);
        assert!(!blocking.is_empty());
    }

    #[test]
    fn a_hop_without_a_published_state_has_no_folded_capacity() {
        let index = DirectionalPoolIndex::default();
        assert!(
            folded_path_capacity(&index, &["core4:pool-a:1".to_owned()]).is_none(),
            "a missing pool state must not produce a folded capacity",
        );
    }

    #[test]
    fn the_hop_direction_is_read_from_the_directional_row() {
        // The pool orders its two tokens by numeric address, which is
        // independent of the logical direction of the trade. The hop spends
        // the pool's token0 exactly when the hop input token is token0, and
        // the row that says so is the row the hop walks.
        let index = directional_index("core4", &[("pool-inverted", 1u128 << 96, TA, USDG, 0)]);

        let hop = resolve_hop_directional_pool(&index, "core4", "pool-inverted", USDG, TA)
            .expect("the hop resolves to the row of its direction");
        assert_eq!(hop.direction, PoolDirection::HighToLow);
        assert_eq!(hop.directional_pool_id, "core4:pool-inverted:0");
        assert!(!hop.direction.zero_for_one(),
            "direction 0 spends token1, so the swap is not zero-for-one");
    }

    #[test]
    fn a_pool_the_direction_layer_does_not_publish_is_a_topology_error() {
        // The direction layer is the closed loop's upstream. A hop that walks
        // a pool the projection does not know cannot be priced from it, so
        // the materialization stops instead of guessing the direction.
        let index = directional_index("core4", &[("pool-known", 1u128 << 96, TA, USDG, 0)]);
        let error = resolve_hop_directional_pool(&index, "core4", "pool-missing", TA, USDG)
            .expect_err("a pool the direction layer does not publish must be rejected");
        assert!(
            matches!(error, ClosedLoopError::InvalidStoredTopology(_)),
            "unexpected error: {error:?}",
        );
    }

    #[test]
    fn a_row_that_trades_the_other_way_is_a_topology_error() {
        // The row is found by the token the hop spends, and its output token
        // is compared with the token the hop expects. A row that trades the
        // other way would fold the path backwards.
        let index = directional_index("core4", &[("pool-inverted", 1u128 << 96, TA, USDG, 0)]);
        let error = resolve_hop_directional_pool(&index, "core4", "pool-inverted", TA, AMZN)
            .expect_err("a row that trades the other way must be rejected");
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
        // The third hop spends USDG, which is the pool's token0 here.
        let index = directional_index(
            "core4",
            &[
                ("pool-eth-weth", 2 * q96, USDG, AMZN, 1),
                ("pool-weth-usdg", q96, AMZN, TA, 1),
                ("pool-usdg-eth", q96, USDG, TA, 1),
            ],
        );
        let hop_directional_pool_ids = vec![
            "core4:pool-eth-weth:1".to_owned(),
            "core4:pool-weth-usdg:1".to_owned(),
            "core4:pool-usdg-eth:1".to_owned(),
        ];
        let (capacity, blocking) = folded_path_capacity(&index, &hop_directional_pool_ids)
            .expect("the capacity must be computable");
        assert!(capacity > 0);
        assert!(!blocking.is_empty());
    }

    /// The fold is a **pure function** of the tick state.
    ///
    /// The refactor changed where the hop's direction and its tick state come
    /// from: the parallel arrays of pool states and directions are replaced
    /// by the directional row the hop walks. The arithmetic must not move
    /// with them, so the same tick state has to produce the very same folded
    /// capacity through the old path and through the new one.
    #[test]
    fn the_folded_capacity_is_equal_to_the_legacy_fold() {
        let q96: u128 = 1u128 << 96;
        let states = [
            ("pool-a", 2 * q96, USDG, AMZN),
            ("pool-b", q96, AMZN, TA),
            ("pool-c", q96, TA, USDG),
        ];
        // The legacy path: the pool states and a parallel direction array.
        let legacy_states = legacy_tick_state_map(&states);
        let pool_ids = vec!["pool-a".to_owned(), "pool-b".to_owned(), "pool-c".to_owned()];
        let directions = vec![true, true, false];
        let legacy = legacy_folded_path_capacity(&legacy_states, &pool_ids, &directions)
            .expect("the legacy fold must be computable");
        // The new path: the directional rows the hops walk.
        let index = directional_index(
            "core4",
            &[
                ("pool-a", 2 * q96, USDG, AMZN, 1),
                ("pool-b", q96, AMZN, TA, 1),
                ("pool-c", q96, TA, USDG, 0),
            ],
        );
        let hop_directional_pool_ids = vec![
            "core4:pool-a:1".to_owned(),
            "core4:pool-b:1".to_owned(),
            "core4:pool-c:0".to_owned(),
        ];
        let current = folded_path_capacity(&index, &hop_directional_pool_ids)
            .expect("the fold must be computable");
        assert_eq!(
            current.0, legacy.0,
            "the same tick state must fold into the same capacity",
        );
        // The fold names the binding pool by its **directional** identity
        // now, so the two bindings are compared through the base pool they
        // name: the binding **pool** must not move.
        assert_eq!(
            base_pool_of(&current.1),
            legacy.1.as_str(),
            "the binding pool must not move"
        );
    }

    /// The direction of a hop is a **pure function** of the token order.
    ///
    /// The same pair of tokens always yields the same direction: there is no
    /// randomness and no read of the database in the decision.
    #[test]
    fn the_direction_of_a_hop_never_flips() {
        let index = directional_index("core4", &[("pool-a", 1u128 << 96, USDG, AMZN, 0)]);
        let first = resolve_hop_directional_pool(&index, "core4", "pool-a", AMZN, USDG)
            .expect("the hop resolves");
        let second = resolve_hop_directional_pool(&index, "core4", "pool-a", AMZN, USDG)
            .expect("the hop resolves");
        assert_eq!(first.direction, second.direction);
        assert_eq!(first.directional_pool_id, second.directional_pool_id);
    }

    /// The single-tick bound belongs to the **direction** of the row.
    ///
    /// Direction 0 takes the bound of the token it spends (token1) and
    /// direction 1 the bound of token0. The bound is read from the row the
    /// hop walks, so a hop can never be judged against the bound of the
    /// other direction.
    #[test]
    fn the_single_tick_bound_is_taken_from_the_direction_of_the_row() {
        // The row publishes no price, so the fold cannot be performed and
        // the bound decides whether the hop has any room.
        let mut index =
            directional_index_with_prices("core4", &[("pool-a", None, USDG, AMZN, 0)]);
        let pool = index
            .pools
            .get_mut("core4:pool-a:0")
            .expect("the row is published");
        pool.input_capacity_raw = Some("0".to_owned());
        let (bound, blocking) = path_min_single_tick_bound(&index, &["core4:pool-a:0".to_owned()]);
        assert_eq!(
            bound,
            Some(0),
            "a measured zero bound means the direction has no room",
        );
        // The blocking pool is named by the directional row the hop walks.
        assert_eq!(blocking.as_deref(), Some("core4:pool-a:0"));

        // The other direction of the same base pool publishes room, so it
        // is not disqualified.
        let mut index =
            directional_index_with_prices("core4", &[("pool-a", None, USDG, AMZN, 1)]);
        let pool = index
            .pools
            .get_mut("core4:pool-a:1")
            .expect("the row is published");
        pool.input_capacity_raw = Some("5000".to_owned());
        let (bound, blocking) = path_min_single_tick_bound(&index, &["core4:pool-a:1".to_owned()]);
        assert_eq!(bound, None, "a hop with room is not disqualified");
        assert_eq!(blocking, None);
    }

    /// Every hop carries the identity of the directional row it walks.
    ///
    /// The direction, the pool and the identity of a hop come from **one**
    /// row of the direction layer, so the hop and the numbers that belong
    /// to it cannot drift apart.
    #[test]
    fn every_hop_carries_the_direction_layer_identity() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_capacities(
                &connection,
                &[(POOL_A1, 1_000_000), (POOL_YZ, 5_000), (POOL_D, 1_000_000)],
            );
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
        assert_eq!(snapshot.hops.len(), 3, "the loop has three hops");
        let connection = Connection::open(&path).unwrap();
        for hop in &snapshot.hops {
            let directional_pool_id = hop.directional_pool_id.as_str();
            let row = connection
                .query_row(
                    "SELECT base_pool_id, direction, input_token_key_id, output_token_key_id FROM manager_v4_directional_exchange_pools WHERE directional_pool_id = ?1",
                    params![directional_pool_id],
                    |row| {
                        Ok((
                            row.get::<_, String>(0)?,
                            row.get::<_, i64>(1)?,
                            row.get::<_, String>(2)?,
                            row.get::<_, String>(3)?,
                        ))
                    },
                )
                .expect("the hop resolves to exactly one directional row");
            assert_eq!(
                row.0,
                base_pool_of(directional_pool_id),
                "I-8/I-11: the hop base pool is the row base pool",
            );
            assert_eq!(
                row.1,
                hop.hop_direction,
                "I-12: the stored direction is the direction of the row",
            );
            assert_eq!(
                row.2, hop.input_token_key_id,
                "the row spends the token the hop spends",
            );
            assert_eq!(
                row.3, hop.output_token_key_id,
                "I-7: the row receives the token the hop receives",
            );
        }
        for row in &snapshot.loops {
            assert_eq!(
                row.directional_pool_ids.len(),
                3,
                "I-16: the loop names one directional pool per hop",
            );
            assert_eq!(
                row.loop_key,
                format!(
                    "{}:{}:{}",
                    row.anchor_address,
                    row.path_addresses[1],
                    row.path_addresses[2],
                ),
                "T-10: the loop key is still the anchor and the two middle tokens",
            );
        }
    }

    /// The closed loop records the direction layer it was built against.
    ///
    /// The consumer can then tell which projection the loops describe and
    /// whether that projection has moved since.
    #[test]
    fn the_closed_loop_state_records_the_direction_layer_identity() {
        let (_directory, path) = database();
        materialize_closed_loops(&path, NETWORK, CHAIN_ID, true).unwrap();
        let status = read_closed_loop_status(&path, NETWORK, CHAIN_ID).unwrap();
        assert_eq!(
            status.directional_pool_version,
            Some(1),
            "I-15: the direction layer version is recorded",
        );
        let checksum = status
            .directional_pool_checksum
            .expect("I-15: the direction layer checksum is recorded");
        assert!(
            checksum.starts_with("sha256:"),
            "I-15: the recorded identity is a checksum, got {checksum}",
        );
    }

    /// The USDG valuation reads two halves of a pool, and each half has to
    /// be there.
    ///
    /// The market half -- price, tick and liquidity -- comes from the
    /// liquidity snapshot table, and the static half -- fee, tick spacing
    /// and the two token key ids -- comes from the base pool tables.  A
    /// pool that is missing either half publishes no valuation at all: the
    /// absence is a coverage gap, never a zero.
    #[test]
    fn a_pool_without_a_market_snapshot_publishes_no_valuation() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        seed_pool_tick_states(
            &connection,
            &[(POOL_A1, 1u128 << 96, &token(USDG), &token(AMZN))],
        );
        let states = read_valuation_pool_states(&connection, NETWORK, CHAIN_ID).unwrap();
        assert!(
            states.contains_key(POOL_A1),
            "the pool publishes a state while it has both halves"
        );

        // The market half goes away: the valuation must stop quoting a
        // price the store no longer holds.
        connection
            .execute(
                "UPDATE v4_exchange_pool_liquidity_snapshots
                 SET sqrt_price_x96 = NULL, tick = NULL, liquidity = NULL
                 WHERE pool_id = ?1",
                params![POOL_A1],
            )
            .unwrap();
        let states = read_valuation_pool_states(&connection, NETWORK, CHAIN_ID).unwrap();
        assert!(
            !states.contains_key(POOL_A1),
            "a pool without a market snapshot publishes no valuation"
        );
    }

    #[test]
    fn a_pool_without_static_facts_publishes_no_valuation() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        seed_pool_tick_states(
            &connection,
            &[(POOL_A1, 1u128 << 96, &token(USDG), &token(AMZN))],
        );
        let states = read_valuation_pool_states(&connection, NETWORK, CHAIN_ID).unwrap();
        assert!(
            states.contains_key(POOL_A1),
            "the pool publishes a state while it has both halves"
        );

        // The static half goes away: the pool keeps its price but no
        // longer says which tokens it pairs.
        connection
            .execute(
                "DELETE FROM v4_exchange_pools_candidate_exchange WHERE pool_id = ?1",
                params![POOL_A1],
            )
            .unwrap();
        let states = read_valuation_pool_states(&connection, NETWORK, CHAIN_ID).unwrap();
        assert!(
            !states.contains_key(POOL_A1),
            "a pool without static facts publishes no valuation"
        );
    }
}
