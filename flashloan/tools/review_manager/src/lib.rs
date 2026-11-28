use closed_loop_manager::{
    read_closed_loop_snapshot, ClosedLoopError, ClosedLoopHopRow, ClosedLoopRow, ClosedLoopStatus,
};

/// The review clock of a closed loop.
///
/// The clock belongs to the **closed loop**, so the Review Manager
/// re-exports it: the broadcast layer starts the clock of a loop after a
/// confirmed broadcast, and it reaches the same code through the Review
/// Manager instead of a second dependency edge.
pub use closed_loop_manager::{
    advance_review_clocks_on_connection, advance_review_clock_on_connection,
    begin_review_cycle_on_connection,
    load_review_queue_on_connection,
    mark_review_required_on_connection, record_review_schedule_on_connection,
    start_review_clock_on_connection, ClosedLoopError as ClosedLoopClockError,
};
use pool_manager::{
    read_live_pool_parity_states, read_pool_parity_states, rpc_urls_for, PoolParityState,
    SingleTickExecutionState,
    DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS,
};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

pub mod parity;
pub mod records;
pub mod runtime;
pub mod shadow;
pub mod single_tick;
pub mod state_transition_gate;

pub use parity::{
    build_hop_chain, build_hops, chain_fee_matches, decode_chain_live_amounts, default_protocol,
    effective_flash_fee_bps, evaluate_route_parity, evaluate_route_parity_with_review_time_state,
    evaluate_route_parity_with_states, extract_review_time_pool_states, flash_fee,
    legacy_fee_residual, parity_request_from_review_payload, parse_amount, read_recorded_reviews,
    relative_delta, replay_recorded_routes, replay_recorded_routes_with, required_repay,
    resolve_route_pool_ids, route_newest_state_read_at, signed_offchain_profit, simulate_route,
    state_age_seconds, state_postdates_review, EvidenceBreakdown, HopResult, HopState,
    ParityAttribution, ParityDetail, ParityError, ParityRequest, ParityState, ParityVerdict,
    PoolStateAge, RecordedReview, ReplayOutcome, ReplayReport, ReplaySelection,
    ReviewTimePoolState, RouteResult, CHAIN_MIN_PROFIT_RAW, DEFAULT_FLASH_FEE_BPS,
    DEFAULT_PROFIT_RELATIVE_TOLERANCE, LEGACY_ASSUMED_FLASH_FEE_BPS, LIQUIDITY_INDEX_SCALE,
    DirectionMismatch, HopChain, HopDirectionList,
};
pub use runtime::{
    ReviewPermit, ReviewRuntime, ReviewRuntimeStatus, ReviewSupervisor, ReviewSupervisorCommand,
    ReviewSupervisorStatus,
};
pub use shadow::{
    run_shadow_comparison, AnomalyEntry, AnomalyLedger, AnomalyStatus, ChainCallFailure,
    ChainQuoteResult, EthCallTrust, EthCallTrustReason, ShadowAttribution, ShadowDiff,
    ShadowFieldDiff, ShadowHopRequest, ShadowQuote, ShadowReport, ShadowRequest, ShadowSourceTag,
    DEFAULT_SHADOW_TOLERANCE, ROBINHOOD_V4_POOL_MANAGER,
};
pub use single_tick::{
    evaluate_route_single_tick, BoundConclusion, SingleTickHopState, SingleTickHopVerdict,
    SingleTickRouteVerdict,
};
pub use state_transition_gate::{
    evaluate_route_transition, route_transition_price_limits, HopExecutionState,
    HopTransitionVerdict, RouteTransitionVerdict, TransitionOutcome,
};
pub use records::{
    diff_review_generations, list_trade_archive_months, publish_review_records,
    publish_trade_archive, read_review_record_health, read_trade_archive_health,
    read_trade_archive_summary, review_record_dir, store_directory_for, trade_record_dir,
    ArchiveRequest, ArchiveReconcileReport, RecordError, ReviewGenerationContext,
    ReviewRecordEvent, ReviewRecordHealthSnapshot, ReviewRecordLine, ReviewRowSnapshot,
    TradeArchiveHealthSnapshot, TradeArchiveSummary,
};

const NETWORK_PROTOCOL: &str = "uniswap_v4";
const REVIEW_SCOPE_KEY: &str = "core4";
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_SNAPSHOT_LIMIT: usize = 1_000;

/// How long the live pool-state read may take.
///
/// The read is one batched `eth_call` against the Pool Manager, and the
/// answer is the state the review compares its quote against. The timeout
/// is the same one the shadow comparison uses, because both are the same
/// question asked at the same moment.
const LIVE_POOL_STATE_TIMEOUT_SECONDS: u64 = 20;







const MAX_AUTOMATIC_BROADCAST_FUTURE_SKEW_SECONDS: i64 = 30;

/// Whether the stamp of a review is a usable measurement moment.
///
/// The broadcast policy has **no freshness window**: a positive profit
/// is broadcast the moment it is measured, because the measurement and
/// the send are one continuous action.  So the only thing the stamp has
/// to answer is whether it is a moment at all.
///
/// A stamp that cannot be read is not a moment, and a stamp that claims
/// to lie in the **future** is a disagreement between the clocks rather
/// than a measurement -- the round it belongs to cannot have happened
/// yet.  A stamp in the past is a measurement: it is the moment the
/// profit was known, and the send follows it directly.
fn review_stamp_is_usable(reviewed_at: &str) -> bool {
    let Ok(reviewed_at) = chrono::DateTime::parse_from_rfc3339(reviewed_at.trim()) else {
        return false;
    };
    let reviewed_at = reviewed_at.with_timezone(&chrono::Utc);
    // A measurement that claims to happen in the future is a clock skew,
    // not a measurement: the round it belongs to cannot have happened yet.
    // A measurement that lies in the **past** is not skew -- it is simply
    // an old measurement, and whether it is still the current one of its
    // loop is answered by the round it belongs to.
    let ahead_seconds = reviewed_at.signed_duration_since(chrono::Utc::now()).num_seconds();
    if ahead_seconds > MAX_AUTOMATIC_BROADCAST_FUTURE_SKEW_SECONDS {
        return false;
    }
    true
}

#[derive(Debug)]
pub enum ReviewError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    InvalidRequest(String),
    ClosedLoopRead(String),
    MissingClosedLoopState,
    ClosedLoopSnapshotTruncated {
        expected: usize,
        actual: usize,
    },
    UnknownClosedLoop(String),
    DuplicateEvidence(String),
    DuplicateDiagnostic {
        loop_key: String,
        diagnostic_id: String,
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
}

impl fmt::Display for ReviewError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "review storage error: {error}"),
            Self::Json(error) => write!(formatter, "review json error: {error}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid review request: {message}")
            }
            Self::ClosedLoopRead(message) => {
                write!(formatter, "closed loop upstream read failed: {message}")
            }
            Self::MissingClosedLoopState => {
                formatter.write_str("closed loop scope core4 has not been materialized")
            }
            Self::ClosedLoopSnapshotTruncated { expected, actual } => write!(
                formatter,
                "closed loop snapshot is truncated: expected {expected} loops, read {actual}"
            ),
            Self::UnknownClosedLoop(loop_key) => {
                write!(
                    formatter,
                    "review evidence references unknown loop {loop_key}"
                )
            }
            Self::DuplicateEvidence(loop_key) => {
                write!(
                    formatter,
                    "review evidence contains duplicate loop {loop_key}"
                )
            }
            Self::DuplicateDiagnostic {
                loop_key,
                diagnostic_id,
            } => write!(
                formatter,
                "review diagnostic {diagnostic_id} is duplicated for loop {loop_key}"
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
        }
    }
}

impl std::error::Error for ReviewError {}

impl From<rusqlite::Error> for ReviewError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<serde_json::Error> for ReviewError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

impl From<ClosedLoopError> for ReviewError {
    fn from(error: ClosedLoopError) -> Self {
        Self::ClosedLoopRead(error.to_string())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpectedUpstream {
    pub closed_loop_version: Option<i64>,
    pub closed_loop_checksum: Option<String>,
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
pub struct ReviewWriteRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub expected_upstream: ExpectedUpstream,
    pub evidence: Vec<ReviewEvidenceInput>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewEvidenceInput {
    pub loop_key: String,
    #[serde(default)]
    pub static_preview: serde_json::Value,
    pub run_arbitrage: Option<RunArbitrageEvidence>,
    #[serde(default)]
    pub diagnostics: Vec<ReviewDiagnosticInput>,
    pub reviewed_at: Option<String>,
    /// The **closed-loop identity** the runtime measured this route under.
    ///
    /// The identity is computed by the closed-loop kernel against the
    /// catalog the closed-loop Manager publishes, and the kernel is the
    /// only place that knows whether the route matched that catalog and
    /// whether the pools it priced are pools the Manager published.
    ///
    /// The Review Manager therefore **stores** the identity instead of
    /// recomputing it. A consumer of the review library reads the identity
    /// from the Manager and never has to rebuild it from the route
    /// addresses, which is what used to make a review with a broken
    /// identity indistinguishable from a review with no identity at all.
    #[serde(default)]
    pub closed_loop_identity: Option<ClosedLoopIdentityEvidence>,
    /// The **generation** the measurement was made under.
    ///
    /// Every closed loop carries its own review, and a review is filed
    /// under the generation it was measured against - not under whatever
    /// generation the catalog happens to be in when the row is written.
    /// The closed-loop catalog is rebuilt continuously, so a generation
    /// that moved on while one loop was being measured must not relabel
    /// that loop's review.
    ///
    /// The field is optional: a measurement that names no generation is
    /// filed under the generation the Review Manager reads, which is what
    /// a review measured without a published catalog gets.
    #[serde(default)]
    pub closed_loop_generation: Option<ClosedLoopGenerationEvidence>,
}

/// The closed-loop generation one measurement was made under.
///
/// The version names the generation and the checksum names its content,
/// so a generation that was rebuilt with the same version but different
/// content is still told apart from the one before it.  Every field is
/// optional: a generation that names only some of them is stored as it is
/// and the missing part is taken from the generation the Review Manager
/// reads.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopGenerationEvidence {
    #[serde(default)]
    pub closed_loop_version: Option<i64>,
    #[serde(default)]
    pub closed_loop_checksum: Option<String>,
    #[serde(default)]
    pub candidate_edge_version: Option<i64>,
    #[serde(default)]
    pub candidate_edge_checksum: Option<String>,
    #[serde(default)]
    pub observation_version: Option<i64>,
    #[serde(default)]
    pub observation_checksum: Option<String>,
}

/// The closed-loop identity of one reviewed route.
///
/// Every field is optional on purpose. A route that was measured against a
/// catalog that published no loop has **no** identity, and a route whose
/// pools the catalog does not publish has an identity that did **not**
/// match. The two are different facts, so the absence of a verdict is
/// stored as an absent value instead of being recorded as a negative one.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopIdentityEvidence {
    /// Whether the route matched the closed-loop catalog.
    #[serde(default)]
    pub closed_loop_identity_status: Option<String>,
    /// Whether every pool the route priced is a pool the closed-loop
    /// Manager published for that hop.
    #[serde(default)]
    pub closed_loop_pool_identity_status: Option<String>,
    /// The diagnosis of an identity mismatch, naming the pool that broke
    /// the match.
    #[serde(default)]
    pub identity_mismatch: Option<String>,
    /// The repair target the identity mismatch names.
    #[serde(default)]
    pub closed_loop_repair_target: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunArbitrageEvidence {
    pub ok: bool,
    pub executable: bool,
    pub block_number: Option<i64>,
    pub signed_pnl: Option<SignedPnl>,
    #[serde(default)]
    pub diagnostics: Vec<ReviewDiagnosticInput>,
    #[serde(flatten)]
    pub raw: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SignedPnl {
    pub raw: String,
    pub amount: f64,
    pub currency: String,
    pub address: Option<String>,
    pub unit: String,
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewDiagnosticInput {
    pub diagnostic_id: Option<String>,
    pub diagnostic_type: ReviewDiagnosticType,
    pub diagnostic_code: Option<String>,
    pub message: Option<String>,
    pub hop_index: Option<usize>,
    pub pool_id: Option<String>,
    pub selector: Option<String>,
    #[serde(default)]
    pub payload: serde_json::Value,
    pub observed_at: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewDiagnosticType {
    #[serde(alias = "SwapNotResolved", alias = "swapNotResolved")]
    SwapNotResolved,
    #[serde(alias = "Revert")]
    Revert,
    #[serde(alias = "QuoteFailed", alias = "quoteFailed")]
    QuoteFailed,
    #[serde(alias = "RpcUnavailable", alias = "rpcUnavailable")]
    RpcUnavailable,
    #[serde(alias = "EvidenceMissing", alias = "evidenceMissing")]
    EvidenceMissing,
    #[serde(alias = "Other")]
    Other,
}

impl ReviewDiagnosticType {
    fn as_str(self) -> &'static str {
        match self {
            Self::SwapNotResolved => "swap_not_resolved",
            Self::Revert => "revert",
            Self::QuoteFailed => "quote_failed",
            Self::RpcUnavailable => "rpc_unavailable",
            Self::EvidenceMissing => "evidence_missing",
            Self::Other => "other",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewRow {
    pub loop_key: String,
    pub closed_loop_version: i64,
    pub closed_loop_checksum: String,
    pub candidate_edge_version: i64,
    pub candidate_edge_checksum: String,
    pub observation_version: i64,
    pub observation_checksum: String,
    pub pool_scope_checksum: String,
    pub anchor_token_key_id: String,
    pub anchor_address: String,
    pub anchor_symbol: String,
    pub path_text: String,
    pub path_addresses: Vec<String>,
    pub path_symbols: Vec<String>,
    pub pool_path_key: String,
    pub pool_ids: Vec<String>,
    pub review_status: String,
    pub page_category: String,
    pub review_reason: Option<String>,
    pub static_preview: serde_json::Value,
    pub run_arbitrage: serde_json::Value,
    pub signed_pnl: Option<SignedPnl>,
    pub reviewed_at: String,
    /// The folded single-tick capacity of the loop, in anchor-token raw
    /// units.
    ///
    /// The value is copied from the closed-loop row, which owns the fold.
    /// The review uses it to prove that the **final** input amount still
    /// fits inside the single-tick capacity, and the broadcast request
    /// carries it so the executor can refuse an amount that was never
    /// proven.
    ///
    /// The value is serialized as a decimal string: a folded capacity
    /// routinely exceeds `u64::MAX`, and a JSON number cannot hold it.
    #[serde(
        serialize_with = "manager_contracts::amount::serialize_optional_u128_as_string",
        deserialize_with = "manager_contracts::amount::deserialize_optional_u128_from_string"
    )]
    pub single_tick_capacity_anchor_raw: Option<u128>,
    /// The hop whose folded capacity is the smallest.
    pub single_tick_capacity_binding_pool_id: Option<String>,
    /// The price limit of each hop, in route order.
    ///
    /// The limits are the current tick boundaries the review proved. A
    /// broadcast payload without all three limits is refused instead of
    /// falling back to the global V4 price bound.
    pub hop_sqrt_price_limits: Vec<String>,
    /// The closed-loop identity the route was measured under.
    ///
    /// The identity is published by the closed-loop Manager and travels
    /// with the review, so a reader of the review library can tell a
    /// matched route, a route that priced pools the catalog does not
    /// publish, and a route measured against an empty catalog apart.
    pub closed_loop_identity: Option<ClosedLoopIdentityEvidence>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewDiagnosticRow {
    pub loop_key: String,
    pub diagnostic_id: String,
    pub diagnostic_type: String,
    pub diagnostic_code: Option<String>,
    pub message: Option<String>,
    pub hop_index: Option<usize>,
    pub pool_id: Option<String>,
    pub selector: Option<String>,
    pub payload: serde_json::Value,
    pub observed_at: String,
}

/// The facts the broadcast layer needs in order to judge a request.
///
/// The review is the only layer that measured `runArbitrage`, so it owns
/// these facts. The broadcast layer reads them instead of re-measuring,
/// which keeps the admission decision in one auditable place.
///
/// Every field is **optional on purpose**: a value that was never
/// measured reads back as absent. The broadcast layer must treat an
/// absent fact as **"not proven"**, never as zero and never as a pass.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastAdmissionFacts {
    /// The review's own status at the moment the request was published.
    ///
    /// Only `completed` may reach the broadcast layer.
    pub review_status: String,
    /// The `runArbitrage` verdict the review recorded.
    ///
    /// `ok` and `executable` are read back **separately** so that a
    /// missing field is distinguishable from a false one.
    pub run_ok: Option<bool>,
    pub run_executable: Option<bool>,
    /// When the review measured the loop.
    ///
    /// The broadcast layer computes the age from this stamp. It is the
    /// only clock reference the freshness rule may use.
    pub reviewed_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastRequestRow {
    pub loop_key: String,
    pub closed_loop_version: i64,
    pub closed_loop_checksum: String,
    pub anchor_token_key_id: String,
    pub path_text: String,
    pub path_addresses: Vec<String>,
    pub path_symbols: Vec<String>,
    pub pool_path_key: String,
    pub pool_ids: Vec<String>,
    pub signed_pnl: SignedPnl,
    pub request_status: String,
    pub request_reason: Option<String>,
    /// The admission facts the broadcast layer needs in order to judge
    /// the request on its own.
    ///
    /// The review is the layer that measured `runArbitrage`, so it owns
    /// these facts. The broadcast layer **must not** re-measure them; it
    /// re-reads them here so that the admission decision is made from
    /// one place against one record.
    ///
    /// The facts are **copied**, never rebuilt: the review wrote them
    /// when it measured the loop, and no later writer may change them
    /// without producing a new review generation.
    pub admission: BroadcastAdmissionFacts,
    /// The folded single-tick capacity of the loop, in anchor-token raw
    /// units.
    ///
    /// The broadcast request carries the bound that the review proved. A
    /// payload whose amount exceeds it was never proven single-tick and
    /// must not be broadcast.
    ///
    /// The value is serialized as a decimal string: a folded capacity
    /// routinely exceeds `u64::MAX`, and a JSON number cannot hold it.
    #[serde(
        serialize_with = "manager_contracts::amount::serialize_optional_u128_as_string",
        deserialize_with = "manager_contracts::amount::deserialize_optional_u128_from_string"
    )]
    pub single_tick_capacity_anchor_raw: Option<u128>,
    /// The hop whose folded capacity is the smallest.
    pub single_tick_capacity_binding_pool_id: Option<String>,
    /// The price limit of each hop, in route order.
    pub hop_sqrt_price_limits: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewStatus {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub review_version: i64,
    pub materialized_checksum: Option<String>,
    pub closed_loop_version: Option<i64>,
    pub closed_loop_checksum: Option<String>,
    pub candidate_edge_version: Option<i64>,
    pub candidate_edge_checksum: Option<String>,
    pub observation_version: Option<i64>,
    pub observation_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub loop_count: usize,
    pub reviewed_count: usize,
    pub completed_count: usize,
    pub data_incomplete_count: usize,
    pub not_executable_count: usize,
    pub failed_count: usize,
    pub broadcast_request_count: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewSnapshot {
    pub status: ReviewStatus,
    pub reviews: Vec<ReviewRow>,
    pub diagnostics: Vec<ReviewDiagnosticRow>,
    pub broadcast_requests: Vec<BroadcastRequestRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewCategoryCounts {
    pub total: usize,
    pub completed: usize,
    pub data_incomplete: usize,
    pub not_executable: usize,
    pub failed: usize,
    pub ready_not_submitted: usize,
    pub confirmed_no_profit: usize,
    pub review_failed: usize,
    pub other: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReviewOutcomeFilter {
    Success,
    Failed,
}

impl ReviewOutcomeFilter {
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "success" => Some(Self::Success),
            "failed" => Some(Self::Failed),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone)]
pub struct ReviewReadQuery<'a> {
    pub loop_key: Option<&'a str>,
    pub category: Option<&'a str>,
    pub review_status: Option<&'a str>,
    pub outcome: Option<ReviewOutcomeFilter>,
    pub offset: usize,
    pub limit: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewPage {
    pub status: ReviewStatus,
    pub category_counts: ReviewCategoryCounts,
    pub filtered_count: usize,
    pub offset: usize,
    pub limit: usize,
    pub reviews: Vec<ReviewRow>,
    pub diagnostics: Vec<ReviewDiagnosticRow>,
    pub broadcast_requests: Vec<BroadcastRequestRow>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub apply: bool,
    pub dry_run: bool,
    pub closed_loop_version: i64,
    pub closed_loop_checksum: String,
    pub candidate_edge_version: i64,
    pub candidate_edge_checksum: String,
    pub observation_version: i64,
    pub observation_checksum: String,
    pub pool_scope_checksum: String,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub loop_count: usize,
    pub reviewed_count: usize,
    pub completed_count: usize,
    pub data_incomplete_count: usize,
    pub not_executable_count: usize,
    pub failed_count: usize,
    pub broadcast_request_count: usize,
    pub changed_rows: usize,
    pub review_version: i64,
    pub materialized_checksum: String,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewWriteOutcome {
    #[serde(flatten)]
    pub result: ReviewMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewRecordStore {
    Manager,
    Legacy,
}

impl ReviewRecordStore {
    fn as_str(self) -> &'static str {
        match self {
            Self::Manager => "manager",
            Self::Legacy => "legacy",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewClearRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub record_store: ReviewRecordStore,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewClearResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub record_store: String,
    pub apply: bool,
    pub dry_run: bool,
    pub cleared_reviews: usize,
    pub cleared_diagnostics: usize,
    pub cleared_broadcast_requests: usize,
    pub remaining_reviews: usize,
    pub remaining_diagnostics: usize,
    pub remaining_broadcast_requests: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewClearOutcome {
    #[serde(flatten)]
    pub result: ReviewClearResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedReviewClearWrite {
    request_id: String,
    request_digest: String,
    result: ReviewClearResult,
}

struct ReviewInput {
    closed_loop: ClosedLoopStatus,
    loops: Vec<ClosedLoopRow>,
    /// The stored direction of every hop of every closed loop.
    ///
    /// The direction is published by the closed-loop Manager, and it is the
    /// direction the hop walks.  The review reads it instead of deriving
    /// the direction from the route, and it cross-checks the two: a hop
    /// whose stored direction contradicts the route is skipped and
    /// reported, never rewritten.
    hop_directions: BTreeMap<String, HopDirectionList>,
    /// The measured pool state of every pool the closed loops name.
    ///
    /// The final single-tick gate needs the pool's price, liquidity, tick
    /// and tick spacing, and it needs them for **every** hop of the route.
    /// The state is read once, through Pool Manager's published API, so the
    /// gate never reads a Pool Manager table directly.
    pool_states: BTreeMap<String, PoolParityState>,
    /// The base pool of every directional pool the closed loops walk.
    ///
    /// The closed loop names **directional** pools and nothing else, so the
    /// base pool of a hop is resolved **through** the direction layer: the
    /// direction row owns the base pool facts.  A directional pool without a
    /// row has no base pool, and the review then cannot prove the hop -- it
    /// never guesses a base pool from the identity alone.
    base_pools: BTreeMap<String, String>,
}

#[derive(Default)]
struct DesiredReviewData {
    reviews: Vec<ReviewRow>,
    diagnostics: Vec<ReviewDiagnosticRow>,
    broadcast_requests: Vec<BroadcastRequestRow>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ReviewStateChecksumPayload<'a> {
    closed_loop_version: i64,
    closed_loop_checksum: &'a str,
    candidate_edge_version: i64,
    candidate_edge_checksum: &'a str,
    observation_version: i64,
    observation_checksum: &'a str,
    pool_scope_checksum: &'a str,
    library_versions: &'a BTreeMap<String, i64>,
    library_checksums: &'a BTreeMap<String, String>,
    reviews: &'a [ReviewContentProjection],
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedReviewWrite {
    request_id: String,
    request_digest: String,
    result: ReviewMaterializationResult,
}

pub fn run_reviews(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    evidence: Vec<ReviewEvidenceInput>,
    apply: bool,
) -> Result<ReviewMaterializationResult, ReviewError> {
    run_impl(
        database_path.as_ref(),
        network,
        chain_id,
        apply,
        &evidence,
        None,
    )
    .map(|outcome| outcome.result)
}

pub fn run_reviews_write(
    database_path: impl AsRef<Path>,
    request: ReviewWriteRequest,
) -> Result<ReviewWriteOutcome, ReviewError> {
    let outcome = run_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        request.apply,
        &request.evidence,
        Some(&request),
    )?;
    Ok(ReviewWriteOutcome {
        result: outcome.result,
        idempotent_replay: outcome.idempotent_replay,
    })
}

pub fn clear_review_records_write(
    database_path: impl AsRef<Path>,
    request: ReviewClearRequest,
) -> Result<ReviewClearOutcome, ReviewError> {
    validate_operation(&request.network, request.chain_id)?;
    if request.operation.trim().is_empty() {
        return Err(ReviewError::InvalidRequest(
            "operation must not be empty".to_owned(),
        ));
    }
    validate_identifier("request_id", &request.request_id, MAX_REQUEST_ID_LENGTH)?;
    validate_identifier(
        "idempotency_key",
        &request.idempotency_key,
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )?;
    let request_digest = clear_request_digest(&request)?;
    let mut connection = open_connection(database_path.as_ref(), request.apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if request.apply {
        ensure_clear_write_ledger(&connection)?;
    }
    if !request.apply {
        let result = prepare_clear_result(&connection, &request)?;
        return Ok(ReviewClearOutcome {
            result,
            idempotent_replay: false,
        });
    }

    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    if let Some(response_json) = find_clear_write_request(&transaction, &request)? {
        let persisted = serde_json::from_str::<PersistedReviewClearWrite>(&response_json)?;
        if persisted.request_digest != request_digest {
            return Err(ReviewError::IdempotencyKeyReused {
                idempotency_key: request.idempotency_key.clone(),
                existing_request_id: persisted.request_id,
                submitted_request_id: request.request_id.clone(),
            });
        }
        return Ok(ReviewClearOutcome {
            result: persisted.result,
            idempotent_replay: true,
        });
    }
    let result = prepare_clear_result(&transaction, &request)?;
    match request.record_store {
        ReviewRecordStore::Manager => {
            delete_from_optional_manager_table(
                &transaction,
                "manager_v4_review_diagnostics",
                &request.network,
                request.chain_id,
            )?;
            delete_from_optional_manager_table(
                &transaction,
                "manager_v4_review_broadcast_requests",
                &request.network,
                request.chain_id,
            )?;
            delete_from_optional_manager_table(
                &transaction,
                "manager_v4_reviews",
                &request.network,
                request.chain_id,
            )?;
            delete_from_optional_manager_table(
                &transaction,
                "manager_review_library_state",
                &request.network,
                request.chain_id,
            )?;
        }
        ReviewRecordStore::Legacy => {
            if sqlite_table_exists(&transaction, "cow_execution_reviews")? {
                transaction.execute(
                    "DELETE FROM cow_execution_reviews WHERE network = ?1 AND chain_id = ?2",
                    params![request.network, request.chain_id],
                )?;
            }
        }
    }
    persist_clear_write(&transaction, &request, &request_digest, &result)?;
    transaction.commit()?;
    Ok(ReviewClearOutcome {
        result,
        idempotent_replay: false,
    })
}

/// The outcome of one materialization pass.
///
/// The audit state is only filled when a generation was really written, so a
/// dry run and a pass that changed nothing both publish no audit line at all.
struct ReviewRunOutcome {
    result: ReviewMaterializationResult,
    idempotent_replay: bool,
    audit: Option<ReviewAuditState>,
}

/// The two review generations the audit stream compares.
///
/// `previous` is the generation that was in the main store before the write
/// and `next` is the generation that was written.  The difference between
/// them is what the audit stream records.
struct ReviewAuditState {
    context: records::ReviewGenerationContext,
    previous: Vec<records::ReviewRowSnapshot>,
    next: Vec<records::ReviewRowSnapshot>,
}

fn run_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    evidence: &[ReviewEvidenceInput],
    request: Option<&ReviewWriteRequest>,
) -> Result<ReviewRunOutcome, ReviewError> {
    validate_operation(network, chain_id)?;
    let prepared_write = match request {
        Some(request) => {
            validate_write_request(request, network, chain_id)?;
            Some((request, request_digest(request)?))
        }
        None => None,
    };

    let mut connection = open_connection(database_path, apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if apply {
        let transaction =
            connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let outcome = run_on_connection(
            &transaction,
            network,
            chain_id,
            true,
            evidence,
            prepared_write,
        )?;
        transaction.commit()?;
        publish_review_audit(database_path, network, chain_id, &outcome);
        Ok(outcome)
    } else {
        run_on_connection(
            &connection,
            network,
            chain_id,
            false,
            evidence,
            prepared_write,
        )
    }
}

fn run_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    apply: bool,
    evidence: &[ReviewEvidenceInput],
    prepared_write: Option<(&ReviewWriteRequest, String)>,
) -> Result<ReviewRunOutcome, ReviewError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(response_json) = find_write_request(connection, request)? {
                let persisted = serde_json::from_str::<PersistedReviewWrite>(&response_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(ReviewError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok(ReviewRunOutcome {
                    result: persisted.result,
                    idempotent_replay: true,
                    audit: None,
                });
            }
        }
    }

    let input = read_review_input(connection, network, chain_id)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &input.closed_loop)?;
    }
    validate_evidence_membership(evidence, &input.loops)?;
    // The reviews the store already holds, and the state this pass leaves
    // behind.  A review belongs to **one** closed loop, so the state of the
    // library is the whole set of reviews the store holds - not the set
    // this pass carried.  The reviews accumulate over time: a loop that
    // was not measured in this pass keeps the review it already has, and a
    // loop that was measured again carries its newest measurement.
    let stored = read_stored_review_projections(connection, network, chain_id)?;
    let desired = build_review_rows(&input, evidence)?;
    let previous = read_status_on_connection(connection, network, chain_id)?;
    let accumulated =
        accumulate_review_state(connection, network, chain_id, &stored, &desired)?;
    let checksum = review_state_checksum(&input, &accumulated)?;
    // The pass changed something when a loop it carried holds a different
    // review than the one it had, or when the accumulated state of the
    // library moved.  One loop's measurement never decides another loop's
    // review, so the comparison is made loop by loop.
    let batch_changed = batch_content_changed(&stored, &desired);
    let counts_changed = previous.enabled
        && (previous.loop_count != input.closed_loop.loop_count
            || previous.reviewed_count != accumulated.reviewed_count
            || previous.completed_count != accumulated.completed_count
            || previous.data_incomplete_count != accumulated.data_incomplete_count
            || previous.not_executable_count != accumulated.not_executable_count
            || previous.failed_count != accumulated.failed_count
            || previous.broadcast_request_count != accumulated.broadcast_request_count);
    let changed = batch_changed || counts_changed;
    let review_version = if changed {
        previous.review_version.saturating_add(1).max(1)
    } else {
        previous.review_version.max(1)
    };
    let changed_rows = if changed {
        desired.reviews.len() + desired.diagnostics.len() + desired.broadcast_requests.len()
    } else {
        0
    };
    let result = ReviewMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: REVIEW_SCOPE_KEY.to_owned(),
        apply,
        dry_run: !apply,
        closed_loop_version: input.closed_loop.closed_loop_version,
        closed_loop_checksum: closed_loop_checksum(&input.closed_loop)?.to_owned(),
        candidate_edge_version: closed_loop_required_version(
            input.closed_loop.candidate_edge_version,
            "candidate_edge_version",
        )?,
        candidate_edge_checksum: closed_loop_required_string(
            input.closed_loop.candidate_edge_checksum.as_deref(),
            "candidate_edge_checksum",
        )?,
        observation_version: closed_loop_required_version(
            input.closed_loop.observation_version,
            "observation_version",
        )?,
        observation_checksum: closed_loop_required_string(
            input.closed_loop.observation_checksum.as_deref(),
            "observation_checksum",
        )?,
        pool_scope_checksum: closed_loop_required_string(
            input.closed_loop.pool_scope_checksum.as_deref(),
            "pool_scope_checksum",
        )?,
        library_versions: input.closed_loop.library_versions.clone(),
        library_checksums: input.closed_loop.library_checksums.clone(),
        loop_count: input.loops.len(),
        reviewed_count: accumulated.reviewed_count,
        completed_count: accumulated.completed_count,
        data_incomplete_count: accumulated.data_incomplete_count,
        not_executable_count: accumulated.not_executable_count,
        failed_count: accumulated.failed_count,
        broadcast_request_count: accumulated.broadcast_request_count,
        changed_rows,
        review_version,
        materialized_checksum: checksum,
        consumers_need_rebuild: changed,
    };

    // The audit stream compares the generation that was in the main store
    // with the generation that is about to be written, so the old rows are
    // read *before* they are replaced.  A pass that changed nothing writes
    // no rows at all, and then there is nothing to compare either.
    //
    // The generation that is about to be written is the **whole** set of
    // reviews the store holds after the pass: the loops this pass carried
    // with their new reviews, and the loops it did not carry with the
    // reviews they already had.  A review stream that reported the loops
    // the pass did not touch as removed would say the opposite of what the
    // store holds.
    let audit = if apply && changed {
        let previous_rows = read_review_rows_for_diff(connection, network, chain_id)?;
        let carried_keys = desired
            .reviews
            .iter()
            .map(|row| row.loop_key.trim().to_lowercase())
            .collect::<BTreeSet<_>>();
        let mut next_rows = previous_rows
            .iter()
            .filter(|snapshot| !carried_keys.contains(&snapshot.loop_key.trim().to_lowercase()))
            .cloned()
            .collect::<Vec<_>>();
        next_rows.extend(desired.reviews.iter().map(review_row_snapshot));
        next_rows.sort_by(|left, right| left.loop_key.cmp(&right.loop_key));
        Some(ReviewAuditState {
            context: records::ReviewGenerationContext {
                network: network.to_owned(),
                chain_id,
                review_version: result.review_version,
                review_checksum: Some(result.materialized_checksum.clone()),
                // The audit stream is written in Beijing time: the record
                // and the name of the file it lands in are read in one
                // clock, so an operator sees the hour of the day the
                // measurement happened in the name of the file.
                observed_at: chrono::Utc::now()
                    .with_timezone(&records::beijing_offset())
                    .to_rfc3339(),
            },
            previous: previous_rows,
            next: next_rows,
        })
    } else {
        None
    };

    if apply {
        if changed {
            upsert_review_rows(connection, network, chain_id, &desired, &result)?;
            // A measurement restarts the review clock of the loop it
            // measured.  The clock belongs to the closed loop, so the
            // write goes through the closed-loop Manager, in the same
            // transaction as the review it belongs to.
            record_review_schedules(connection, network, chain_id, &desired)?;
        }
        if let Some((request, request_digest)) = prepared_write {
            persist_write_request(connection, request, &request_digest, &result)?;
        }
    }
    Ok(ReviewRunOutcome {
        result,
        idempotent_replay: false,
        audit,
    })
}

/// The content of one review, projected so a stored row and a freshly built
/// row can be compared field by field.
///
/// The projection holds the **measured** content of the review only.  The
/// identity of the closed loop is not part of it: the identity belongs to
/// the closed-loop catalog and is written into the row beside the review,
/// so a catalog that moved on does not make an unchanged review look
/// changed.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
struct ReviewContentProjection {
    loop_key: String,
    review_status: String,
    review_reason: Option<String>,
    reviewed_at: String,
    signed_pnl: Option<SignedPnl>,
    static_preview: serde_json::Value,
    run_arbitrage: serde_json::Value,
    pool_ids: Vec<String>,
    hop_sqrt_price_limits: Vec<String>,
    closed_loop_identity: Option<ClosedLoopIdentityEvidence>,
}

/// The state the review library holds after a pass.
///
/// The state is the **whole** set of reviews the store holds: the loops the
/// pass carried with their new reviews, and the loops it did not carry
/// with the reviews they already had.  The reviews accumulate over time,
/// so the state is a sum and never a replacement.
struct AccumulatedReviewState {
    projections: Vec<ReviewContentProjection>,
    reviewed_count: usize,
    completed_count: usize,
    data_incomplete_count: usize,
    not_executable_count: usize,
    failed_count: usize,
    broadcast_request_count: usize,
}

/// The projection of a freshly built review row.
fn projection_of_review_row(row: &ReviewRow) -> ReviewContentProjection {
    ReviewContentProjection {
        loop_key: row.loop_key.clone(),
        review_status: row.review_status.clone(),
        review_reason: row.review_reason.clone(),
        reviewed_at: row.reviewed_at.clone(),
        signed_pnl: row.signed_pnl.clone(),
        static_preview: row.static_preview.clone(),
        run_arbitrage: row.run_arbitrage.clone(),
        pool_ids: row.pool_ids.clone(),
        hop_sqrt_price_limits: row.hop_sqrt_price_limits.clone(),
        closed_loop_identity: normalized_closed_loop_identity(row.closed_loop_identity.clone()),
    }
}

/// The identity of a review, without the shape that says nothing.
///
/// A review that names no verdict at all carries no identity: the four
/// fields are all absent, and the stored row holds four NULL columns for
/// them.  The projection therefore drops the empty identity on both sides,
/// so an unchanged review does not look changed because of the shape of an
/// absent verdict.
fn normalized_closed_loop_identity(
    identity: Option<ClosedLoopIdentityEvidence>,
) -> Option<ClosedLoopIdentityEvidence> {
    identity.filter(|value| {
        value.closed_loop_identity_status.is_some()
            || value.closed_loop_pool_identity_status.is_some()
            || value.identity_mismatch.is_some()
            || value.closed_loop_repair_target.is_some()
    })
}

/// The reviews the store holds for the scope, projected.
fn read_stored_review_projections(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, ReviewContentProjection>, ReviewError> {
    let mut statement = connection.prepare(
        "SELECT loop_key, review_status, review_reason, reviewed_at,
                signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                static_preview_json, run_arbitrage_json, pool_ids_json,
                hop_sqrt_price_limits_json, closed_loop_identity_status,
                closed_loop_pool_identity_status, identity_mismatch,
                closed_loop_repair_target
         FROM manager_v4_reviews
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY],
        |row| {
            Ok(ReviewContentProjection {
                loop_key: row.get::<_, String>(0)?,
                review_status: row.get::<_, String>(1)?,
                review_reason: row.get::<_, Option<String>>(2)?,
                reviewed_at: row.get::<_, String>(3)?,
                signed_pnl: signed_pnl_of_columns(
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, Option<f64>>(5)?,
                    row.get::<_, Option<String>>(6)?,
                    row.get::<_, Option<String>>(7)?,
                    row.get::<_, Option<String>>(8)?,
                    row.get::<_, Option<String>>(9)?,
                ),
                static_preview: json_value_of_column(row.get::<_, Option<String>>(10)?),
                run_arbitrage: json_value_of_column(row.get::<_, Option<String>>(11)?),
                pool_ids: json_strings_of_column(row.get::<_, Option<String>>(12)?),
                hop_sqrt_price_limits: json_strings_of_column(row.get::<_, Option<String>>(13)?),
                closed_loop_identity: normalized_closed_loop_identity(Some(
                    ClosedLoopIdentityEvidence {
                        closed_loop_identity_status: row.get::<_, Option<String>>(14)?,
                        closed_loop_pool_identity_status: row.get::<_, Option<String>>(15)?,
                        identity_mismatch: row.get::<_, Option<String>>(16)?,
                        closed_loop_repair_target: row.get::<_, Option<String>>(17)?,
                    },
                )),
            })
        },
    )?;
    let mut projections = BTreeMap::new();
    for row in rows {
        let projection = row?;
        projections.insert(projection.loop_key.trim().to_lowercase(), projection);
    }
    Ok(projections)
}

/// The signed profit a stored row holds.
fn signed_pnl_of_columns(
    raw: Option<String>,
    amount: Option<f64>,
    currency: Option<String>,
    address: Option<String>,
    unit: Option<String>,
    source: Option<String>,
) -> Option<SignedPnl> {
    raw.map(|raw| SignedPnl {
        raw,
        amount: amount.unwrap_or_default(),
        currency: currency.unwrap_or_default(),
        address,
        unit: unit.unwrap_or_default(),
        source: source.unwrap_or_default(),
    })
}

/// A JSON column read back as a value.
fn json_value_of_column(text: Option<String>) -> serde_json::Value {
    text.and_then(|value| serde_json::from_str(&value).ok())
        .unwrap_or(serde_json::Value::Null)
}

/// A JSON column read back as a list of strings.
fn json_strings_of_column(text: Option<String>) -> Vec<String> {
    text.and_then(|value| serde_json::from_str(&value).ok())
        .unwrap_or_default()
}

/// The state the library holds after the pass.
fn accumulate_review_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    stored: &BTreeMap<String, ReviewContentProjection>,
    desired: &DesiredReviewData,
) -> Result<AccumulatedReviewState, ReviewError> {
    let carried_keys = desired
        .reviews
        .iter()
        .map(|row| row.loop_key.trim().to_lowercase())
        .collect::<BTreeSet<_>>();
    let mut projections = stored
        .iter()
        .filter(|(key, _)| !carried_keys.contains(*key))
        .map(|(_, value)| value.clone())
        .collect::<Vec<_>>();
    projections.extend(desired.reviews.iter().map(projection_of_review_row));
    projections.sort_by(|left, right| left.loop_key.cmp(&right.loop_key));
    let status_count = |status: &str| {
        projections
            .iter()
            .filter(|projection| projection.review_status == status)
            .count()
    };
    // The broadcast requests of the loops the pass carried replace the
    // stored ones; the requests of the loops it did not carry are kept.
    let stored_requests = read_stored_broadcast_request_loop_keys(connection, network, chain_id)?;
    let carried_requests = stored_requests
        .iter()
        .filter(|key| !carried_keys.contains(&key.trim().to_lowercase()))
        .count();
    Ok(AccumulatedReviewState {
        reviewed_count: projections.len(),
        completed_count: status_count("completed"),
        data_incomplete_count: status_count("data_incomplete"),
        not_executable_count: status_count("not_executable"),
        failed_count: status_count("failed"),
        broadcast_request_count: carried_requests + desired.broadcast_requests.len(),
        projections,
    })
}

/// The loops the store holds a broadcast request for.
fn read_stored_broadcast_request_loop_keys(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<String>, ReviewError> {
    let mut statement = connection.prepare(
        "SELECT loop_key FROM manager_v4_review_broadcast_requests
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY],
        |row| row.get::<_, String>(0),
    )?;
    let mut keys = Vec::new();
    for row in rows {
        keys.push(row?);
    }
    Ok(keys)
}

/// Whether a loop the pass carried holds a different review than it had.
fn batch_content_changed(
    stored: &BTreeMap<String, ReviewContentProjection>,
    desired: &DesiredReviewData,
) -> bool {
    desired.reviews.iter().any(|row| {
        let key = row.loop_key.trim().to_lowercase();
        match stored.get(&key) {
            Some(existing) => *existing != projection_of_review_row(row),
            None => true,
        }
    })
}

/// Read the reviews of the current generation in the shape the audit stream
///
/// The filter is the same one `upsert_review_rows` uses, so the rows read
/// here are exactly the rows the write replaces.
fn read_review_rows_for_diff(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<records::ReviewRowSnapshot>, ReviewError> {
    let mut statement = connection.prepare(
        "SELECT loop_key, closed_loop_version, review_status, review_reason,
                signed_pnl_raw, signed_pnl_currency, signed_pnl_unit, signed_pnl_source,
                path_symbols_json, pool_ids_json,
                single_tick_capacity_anchor_raw, single_tick_capacity_binding_pool_id,
                reviewed_at
         FROM manager_v4_reviews
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY loop_key",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<i64>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, Option<String>>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, Option<String>>(7)?,
                row.get::<_, Option<String>>(8)?,
                row.get::<_, Option<String>>(9)?,
                row.get::<_, Option<String>>(10)?,
                row.get::<_, Option<String>>(11)?,
                row.get::<_, Option<String>>(12)?,
            ))
        },
    )?;
    let mut snapshots = Vec::new();
    for row in rows {
        let (
            loop_key,
            closed_loop_version,
            review_status,
            review_reason,
            signed_pnl_raw,
            signed_pnl_currency,
            signed_pnl_unit,
            signed_pnl_source,
            path_symbols_json,
            pool_ids_json,
            capacity_anchor_raw,
            capacity_binding_pool_id,
            reviewed_at,
        ) = row?;
        snapshots.push(records::ReviewRowSnapshot {
            loop_key,
            reviewed_at,
            closed_loop_version,
            review_status,
            review_reason,
            signed_pnl_raw,
            signed_pnl_currency,
            signed_pnl_unit,
            signed_pnl_source,
            path_symbols: optional_json_list(path_symbols_json)?,
            pool_ids: optional_json_list(pool_ids_json)?,
            capacity_anchor_raw,
            capacity_binding_pool_id,
        });
    }
    Ok(snapshots)
}

/// Project a review row onto the fields the audit stream compares.
fn review_row_snapshot(row: &ReviewRow) -> records::ReviewRowSnapshot {
    records::ReviewRowSnapshot {
        loop_key: row.loop_key.clone(),
        // The moment of the measurement travels with the review: the audit
        // record has to name the hour the review belongs to, and the only
        // moment that knows it is the one the review was measured at.
        reviewed_at: Some(row.reviewed_at.clone()),
        closed_loop_version: Some(row.closed_loop_version),
        review_status: Some(row.review_status.clone()),
        review_reason: row.review_reason.clone(),
        signed_pnl_raw: row.signed_pnl.as_ref().map(|pnl| pnl.raw.clone()),
        signed_pnl_currency: row.signed_pnl.as_ref().map(|pnl| pnl.currency.clone()),
        signed_pnl_unit: row.signed_pnl.as_ref().map(|pnl| pnl.unit.clone()),
        signed_pnl_source: row.signed_pnl.as_ref().map(|pnl| pnl.source.clone()),
        path_symbols: Some(row.path_symbols.clone()),
        pool_ids: Some(row.pool_ids.clone()),
        capacity_anchor_raw: row.single_tick_capacity_anchor_raw.map(|value| value.to_string()),
        capacity_binding_pool_id: row.single_tick_capacity_binding_pool_id.clone(),
    }
}

/// Read a JSON list column, keeping *absent* apart from *empty*.
fn optional_json_list(value: Option<String>) -> Result<Option<Vec<String>>, ReviewError> {
    match value {
        Some(value) => Ok(Some(serde_json::from_str::<Vec<String>>(&value)?)),
        None => Ok(None),
    }
}

/// Publish the audit lines of a written generation.
///
/// The audit stream takes part in no decision, so a failure here never fails
/// the review that was already written.  The failure is counted instead, so
/// the loss stays visible in the health snapshot.
fn publish_review_audit(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    outcome: &ReviewRunOutcome,
) {
    let Some(audit) = outcome.audit.as_ref() else {
        return;
    };
    let lines = records::diff_review_generations(&audit.context, &audit.previous, &audit.next);
    if lines.is_empty() {
        return;
    }
    let directory = match records::review_record_dir(database_path, network, chain_id) {
        Ok(directory) => directory,
        Err(error) => {
            records::note_review_publish_failure(&error);
            return;
        }
    };
    if let Err(error) = records::publish_review_records(&directory, lines) {
        records::note_review_publish_failure(&error);
    }
}

/// The reason a closed loop that was never reviewed carries.
const STALE_REASON_NEVER_REVIEWED: &str = "not_reviewed_at_all";

/// The prefix of the reason a closed loop that went quiet carries.
const STALE_REASON_PREFIX: &str = "not_reviewed_since:";

/// The reason a closed loop that publishes no review time point carries.
///
/// A loop without a review time point has no moment to compare its
/// measurement with, so the measurement cannot be proven to be the
/// current one of the loop.
const STALE_REASON_NO_REVIEW_TIME_POINT: &str = "no_review_time_point";

/// The basis every stale judgement is made on.
///
/// The sweep compares **moments**: the moment the closed loop is
/// scheduled at and the moment its round started.  No window of seconds
/// is involved -- an age counted from the measurement would count the
/// time the review itself took, which is not time the market moved.
pub const STALE_MOMENT_BASIS: &str = "loop_review_time_point";

/// The outcome of one stale sweep.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StaleReviewSweepOutcome {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    /// The basis the sweep judged by.
    pub moment_basis: String,
    /// How many closed loops the sweep looked at.
    pub scanned_count: usize,
    /// How many closed loops the sweep marked as stale.
    pub marked_count: usize,
    /// How many broadcast requests the sweep withdrew.
    pub withdrawn_request_count: usize,
    /// The loops the sweep marked.
    pub stale_loop_keys: Vec<String>,
    /// When the sweep ran.
    pub observed_at: String,
}

/// Mark every closed loop whose measurement is not the current one.
///
/// A closed loop that goes quiet must not go **silent**.  The sweep writes
/// the missing statement into the store: the review row of the loop says
/// that no current review arrived, a diagnostic names the reason, and the
/// broadcast request of the loop is withdrawn, so a stale opportunity can
/// never be broadcast.
///
/// The sweep judges by **moments**, never by an age.  A closed loop
/// publishes the moment it is scheduled at and the moment its round
/// started, and those two moments say whether the measurement the store
/// holds is the current one of the loop:
///
/// * a loop that publishes **no** measurement at all is marked, because
///   nothing proves that it was ever measured;
/// * a loop that publishes **no** review time point is marked, because
///   no moment can be compared with anything;
/// * a loop whose round started **after** the measurement the store holds
///   is marked, because that round never published a measurement of its
///   own;
/// * a loop that is **busy** -- it is being measured right now, or it
///   waits for the receipt of its trade -- is not marked: it is working,
///   not quiet;
/// * a loop that still **waits** for its next round is not marked: its
///   measurement is the current one of the loop until that round starts.
///
/// The wait of a loop is **not** a fault: a loop that waits its turn in
/// the queue keeps the measurement it has, and the sweep leaves it alone.
/// The interval of the review cycle is not read here either -- it belongs
/// to the helper that schedules a loop, and the moment it folded into is
/// the moment the loop is scheduled at.
///
/// The sweep touches **only** the loops it marks.  One loop that goes
/// quiet never changes the review of another loop, and a loop that is
/// reviewed again carries its new review over the marker.
///
/// The sweep runs on its own clock, so the store keeps telling the truth
/// even while the review path is unable to publish.
pub fn mark_stale_reviews(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<StaleReviewSweepOutcome, ReviewError> {
    validate_operation(network, chain_id)?;
    let database_path = database_path.as_ref();
    let snapshot = closed_loop_manager::read_closed_loop_snapshot(
        database_path,
        network,
        chain_id,
        None,
        MAX_SNAPSHOT_LIMIT,
    )
    .map_err(|error| ReviewError::ClosedLoopRead(error.to_string()))?;
    if !snapshot.status.enabled {
        return Err(ReviewError::MissingClosedLoopState);
    }
    let observed_at = chrono::Utc::now();
    let observed_at_text = observed_at.to_rfc3339();
    let mut connection = open_connection(database_path, apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    let stored = read_stored_review_projections(&connection, network, chain_id)?;
    let stale = stale_loops(&snapshot.loops, &snapshot.runtime_states, &stored);
    let mut outcome = StaleReviewSweepOutcome {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: REVIEW_SCOPE_KEY.to_owned(),
        moment_basis: STALE_MOMENT_BASIS.to_owned(),
        scanned_count: snapshot.loops.len(),
        marked_count: 0,
        withdrawn_request_count: 0,
        stale_loop_keys: Vec::new(),
        observed_at: observed_at_text.clone(),
    };
    if stale.is_empty() {
        return Ok(outcome);
    }
    let previous_rows = read_review_rows_for_diff(&connection, network, chain_id)?;
    let previous_status = read_status_on_connection(&connection, network, chain_id)?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let mut withdrawn = 0usize;
    for entry in &stale {
        withdrawn += withdraw_broadcast_request(
            &transaction,
            network,
            chain_id,
            &entry.loop_key,
        )?;
        mark_stale_loop(
            &transaction,
            network,
            chain_id,
            entry,
            &snapshot.loops,
            &snapshot.status,
            &observed_at_text,
        )?;
        // The loop has no measurement, so it has to be measured again.  A
        // loop that publishes **no** review time point gets one here; a
        // loop that already publishes one keeps it, because that moment
        // is what orders the queue, and a sweep must never reorder the
        // work of the runtime.
        if entry.schedule_missing {
            closed_loop_manager::mark_review_required_on_connection(
                &transaction,
                network,
                chain_id,
                &entry.loop_key,
            )
            .map_err(|error| ReviewError::ClosedLoopRead(error.to_string()))?;
        }
    }
    // The state of the library names the reviews the store holds, so it is
    // recomputed over the whole store: the marker of one loop changes the
    // counts of the scope, and the checksum has to name the state that is
    // in the store now.
    let stored_after = read_stored_review_projections(&transaction, network, chain_id)?;
    let desired = DesiredReviewData::default();
    let accumulated =
        accumulate_review_state(&transaction, network, chain_id, &stored_after, &desired)?;
    let input = ReviewInput {
        closed_loop: snapshot.status.clone(),
        loops: Vec::new(),
        hop_directions: BTreeMap::new(),
        pool_states: BTreeMap::new(),
        base_pools: BTreeMap::new(),
    };
    let checksum = review_state_checksum(&input, &accumulated)?;
    let result = ReviewMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: REVIEW_SCOPE_KEY.to_owned(),
        apply,
        dry_run: !apply,
        closed_loop_version: snapshot.status.closed_loop_version,
        closed_loop_checksum: closed_loop_checksum(&snapshot.status)?.to_owned(),
        candidate_edge_version: closed_loop_required_version(
            snapshot.status.candidate_edge_version,
            "candidate_edge_version",
        )?,
        candidate_edge_checksum: closed_loop_required_string(
            snapshot.status.candidate_edge_checksum.as_deref(),
            "candidate_edge_checksum",
        )?,
        observation_version: closed_loop_required_version(
            snapshot.status.observation_version,
            "observation_version",
        )?,
        observation_checksum: closed_loop_required_string(
            snapshot.status.observation_checksum.as_deref(),
            "observation_checksum",
        )?,
        pool_scope_checksum: closed_loop_required_string(
            snapshot.status.pool_scope_checksum.as_deref(),
            "pool_scope_checksum",
        )?,
        library_versions: snapshot.status.library_versions.clone(),
        library_checksums: snapshot.status.library_checksums.clone(),
        loop_count: snapshot.loops.len(),
        reviewed_count: accumulated.reviewed_count,
        completed_count: accumulated.completed_count,
        data_incomplete_count: accumulated.data_incomplete_count,
        not_executable_count: accumulated.not_executable_count,
        failed_count: accumulated.failed_count,
        broadcast_request_count: accumulated.broadcast_request_count,
        changed_rows: stale.len(),
        review_version: previous_status.review_version.saturating_add(1).max(1),
        materialized_checksum: checksum.clone(),
        consumers_need_rebuild: true,
    };
    if apply {
        insert_state(&transaction, network, chain_id, &result, &observed_at_text)?;
    }
    transaction.commit()?;
    outcome.marked_count = stale.len();
    outcome.withdrawn_request_count = withdrawn;
    outcome.stale_loop_keys = stale
        .iter()
        .map(|entry| entry.loop_key.clone())
        .collect();
    if apply {
        let next_rows = read_review_rows_for_diff(&connection, network, chain_id)?;
        publish_stale_audit(
            database_path,
            network,
            chain_id,
            &previous_rows,
            &next_rows,
            result.review_version,
            &checksum,
            &observed_at_text,
        );
    }
    Ok(outcome)
}

/// One closed loop whose measurement is not the current one.
struct StaleLoop {
    /// The loop the marker belongs to.
    loop_key: String,
    /// The reason the loop is stale.
    reason: String,
    /// Whether the store already holds a review row of the loop.
    exists: bool,
    /// When the loop was reviewed last, if it ever was.
    last_reviewed_at: Option<String>,
    /// Whether the loop publishes no review time point at all.
    ///
    /// Only such a loop is given one by the sweep: a loop that already
    /// publishes a moment keeps it, so the sweep never reorders the work
    /// of the runtime.
    schedule_missing: bool,
}

/// The closed loops whose measurement is not the current one.
///
/// A loop that carries a marker already is **not** listed again: the
/// marker is the statement, and repeating it would rewrite the row on
/// every sweep without saying anything new.
fn stale_loops(
    loops: &[ClosedLoopRow],
    runtime_states: &[closed_loop_manager::ClosedLoopRuntimeStateRow],
    stored: &BTreeMap<String, ReviewContentProjection>,
) -> Vec<StaleLoop> {
    let schedules = runtime_states
        .iter()
        .map(|state| (state.loop_key.trim().to_lowercase(), state))
        .collect::<BTreeMap<_, _>>();
    let mut stale = Vec::new();
    for closed_loop in loops {
        let key = closed_loop.loop_key.trim().to_lowercase();
        if key.is_empty() {
            continue;
        }
        let schedule = schedules.get(&key).copied();
        // A retired loop is out of the game: it is neither claimed nor
        // measured, so it carries no measurement and none is due.
        if schedule.is_some_and(|state| {
            state.runtime_status.trim() == closed_loop_manager::RUNTIME_STATUS_RETIRED
        }) {
            continue;
        }
        let Some(projection) = stored.get(&key) else {
            stale.push(StaleLoop {
                loop_key: closed_loop.loop_key.clone(),
                reason: STALE_REASON_NEVER_REVIEWED.to_owned(),
                exists: false,
                last_reviewed_at: None,
                schedule_missing: schedule.is_none()
                    || schedule.and_then(|state| state.next_review_at.as_deref()).is_none(),
            });
            continue;
        };
        let reviewed_at = chrono::DateTime::parse_from_rfc3339(projection.reviewed_at.trim())
            .map(|value| value.with_timezone(&chrono::Utc))
            .ok();
        let Some(reviewed_at) = reviewed_at else {
            // A review that never reported a time names no moment, so it
            // can never prove that it is the current measurement of its
            // loop.
            if projection.review_status == "data_incomplete"
                && projection.review_reason.as_deref() == Some(STALE_REASON_NEVER_REVIEWED)
            {
                continue;
            }
            stale.push(StaleLoop {
                loop_key: closed_loop.loop_key.clone(),
                reason: STALE_REASON_NEVER_REVIEWED.to_owned(),
                exists: true,
                last_reviewed_at: Some(projection.reviewed_at.clone()),
                schedule_missing: schedule.is_none()
                    || schedule.and_then(|state| state.next_review_at.as_deref()).is_none(),
            });
            continue;
        };
        // The loop publishes the moment it is scheduled at.  Without one
        // there is no moment to compare the measurement with, so the
        // measurement cannot be proven to be the current one.
        let Some(next_review_at) = schedule.and_then(|state| state.next_review_at.as_deref()) else {
            stale.push(StaleLoop {
                loop_key: closed_loop.loop_key.clone(),
                reason: STALE_REASON_NO_REVIEW_TIME_POINT.to_owned(),
                exists: true,
                last_reviewed_at: Some(projection.reviewed_at.clone()),
                schedule_missing: true,
            });
            continue;
        };
        // A loop that is busy -- it is being measured right now, or it
        // waits for the receipt of its trade -- is not quiet: the round
        // that is running will publish a measurement of its own, and a
        // trade that waits for its receipt has already been made.  The
        // **state** of the loop says so: a claim keeps the moment the
        // loop was scheduled at, so the marker is not there to be read.
        if closed_loop_manager::is_review_sentinel(next_review_at)
            || schedule.is_some_and(|state| {
                matches!(
                    state.runtime_status.trim(),
                    closed_loop_manager::RUNTIME_STATUS_REVIEW_PENDING
                        | closed_loop_manager::RUNTIME_STATUS_BROADCAST_PENDING
                )
            })
        {
            continue;
        }
        // The round of the loop started after the measurement the store
        // holds, and that round never published a measurement of its own:
        // the store holds a measurement that is not the current one.  The
        // round is the loop's own, so the two moments are compared with
        // each other and not with a window of seconds.
        let round_started_at = schedule.and_then(|state| state.review_cycle_started_at.as_deref());
        let round_started_at = round_started_at
            .and_then(|value| chrono::DateTime::parse_from_rfc3339(value.trim()).ok())
            .map(|value| value.with_timezone(&chrono::Utc));
        if let Some(started) = round_started_at {
            if reviewed_at >= started {
                // The measurement the store holds is the measurement of
                // the round that is running: it is the current one.
                continue;
            }
        } else {
            // The loop has no round of its own.  It is either waiting for
            // its next round or waiting its turn in the queue, and the
            // measurement the store holds is the measurement of the last
            // round -- a true statement about the loop.  The loop is
            // working, not quiet, so the sweep leaves it alone.
            continue;
        }
        let reason = format!("{STALE_REASON_PREFIX}{}", projection.reviewed_at.trim());
        if projection.review_status == "data_incomplete"
            && projection.review_reason.as_deref() == Some(reason.as_str())
        {
            continue;
        }
        stale.push(StaleLoop {
            loop_key: closed_loop.loop_key.clone(),
            reason,
            exists: true,
            last_reviewed_at: Some(projection.reviewed_at.clone()),
            schedule_missing: false,
        });
    }
    stale
}

/// Write the marker of one stale loop into the store.
fn mark_stale_loop(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    entry: &StaleLoop,
    loops: &[ClosedLoopRow],
    status: &ClosedLoopStatus,
    observed_at: &str,
) -> Result<(), ReviewError> {
    if entry.exists {
        // The measurement of the loop stays: the marker says that the
        // measurement is old, it does not pretend the loop never ran.
        connection.execute(
            "UPDATE manager_v4_reviews
                SET review_status = 'data_incomplete',
                    review_reason = ?2,
                    updated_at = ?3
              WHERE network = ?1 AND chain_id = ?4 AND protocol = ?5
                AND scope_key = ?6 AND loop_key = ?7",
            params![
                network,
                entry.reason,
                observed_at,
                chain_id,
                NETWORK_PROTOCOL,
                REVIEW_SCOPE_KEY,
                entry.loop_key
            ],
        )?;
    } else {
        let Some(closed_loop) = loops
            .iter()
            .find(|row| row.loop_key.trim().eq_ignore_ascii_case(entry.loop_key.trim()))
        else {
            return Ok(());
        };
        connection.execute(
            "INSERT INTO manager_v4_reviews (
                 network, chain_id, protocol, scope_key, loop_key,
                 closed_loop_version, closed_loop_checksum,
                 candidate_edge_version, candidate_edge_checksum,
                 observation_version, observation_checksum, pool_scope_checksum,
                 anchor_token_key_id, anchor_address, anchor_symbol, path_text,
                 path_addresses_json, path_symbols_json, pool_path_key,
                 pool_ids_json, review_status, review_reason, static_preview_json,
                 run_arbitrage_json, reviewed_at,
                 single_tick_capacity_anchor_raw,
                 single_tick_capacity_binding_pool_id,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23,
                       ?24, ?25, ?26, ?27, ?28, ?28)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                REVIEW_SCOPE_KEY,
                closed_loop.loop_key,
                status.closed_loop_version,
                closed_loop_checksum(status)?,
                closed_loop.candidate_edge_version,
                closed_loop.candidate_edge_checksum,
                closed_loop.observation_version,
                closed_loop.observation_checksum,
                closed_loop_required_string(
                    status.pool_scope_checksum.as_deref(),
                    "pool_scope_checksum",
                )?,
                closed_loop.anchor_token_key_id,
                closed_loop.anchor_address,
                closed_loop.anchor_symbol,
                closed_loop.path_text,
                serde_json::to_string(&closed_loop.path_addresses)?,
                serde_json::to_string(&closed_loop.path_symbols)?,
                closed_loop.pool_path_key,
                serde_json::to_string(&closed_loop.directional_pool_ids)?,
                "data_incomplete",
                entry.reason,
                "{}",
                "{}",
                entry
                    .last_reviewed_at
                    .clone()
                    .unwrap_or_else(|| "unreviewed".to_owned()),
                closed_loop.single_tick_capacity_anchor_raw
                    .map(|value| value.to_string()),
                closed_loop.single_tick_capacity_binding_directional_pool_id.clone(),
                observed_at
            ],
        )?;
    }
    // The diagnostic names the reason of the marker, so the store says
    // **why** the loop went quiet and not only that it did.
    connection.execute(
        "INSERT INTO manager_v4_review_diagnostics (
             network, chain_id, protocol, scope_key, loop_key,
             diagnostic_id, diagnostic_type, diagnostic_code, message,
             hop_index, pool_id, selector, payload_json, observed_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'evidence_missing', 'review_stale',
                   ?7, NULL, NULL, NULL, ?8, ?9)
         ON CONFLICT(network, chain_id, protocol, scope_key, loop_key, diagnostic_id)
         DO UPDATE SET message = excluded.message,
                       payload_json = excluded.payload_json,
                       observed_at = excluded.observed_at",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            REVIEW_SCOPE_KEY,
            entry.loop_key,
            format!("review_stale:{}", entry.loop_key),
            entry.reason,
            serde_json::to_string(&serde_json::json!({
                "momentBasis": STALE_MOMENT_BASIS,
                "lastReviewedAt": entry.last_reviewed_at,
            }))?,
            observed_at
        ],
    )?;
    Ok(())
}

/// Withdraw the broadcast request of one closed loop.
///
/// A request that was generated from a review that is no longer fresh may
/// not be broadcast: the opportunity it names is not the one the market
/// holds now.
fn withdraw_broadcast_request(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
) -> Result<usize, ReviewError> {
    let withdrawn = connection.execute(
        "DELETE FROM manager_v4_review_broadcast_requests
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4 AND loop_key = ?5",
        params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY, loop_key],
    )?;
    Ok(withdrawn)
}

/// Publish the audit lines of the markers the sweep wrote.
fn publish_stale_audit(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    previous: &[records::ReviewRowSnapshot],
    next: &[records::ReviewRowSnapshot],
    review_version: i64,
    review_checksum: &str,
    observed_at: &str,
) {
    let context = records::ReviewGenerationContext {
        network: network.to_owned(),
        chain_id,
        review_version,
        review_checksum: Some(review_checksum.to_owned()),
        observed_at: observed_at.to_owned(),
    };
    let lines = records::diff_review_generations(&context, previous, next);
    if lines.is_empty() {
        return;
    }
    let directory = match records::review_record_dir(database_path, network, chain_id) {
        Ok(directory) => directory,
        Err(error) => {
            records::note_review_publish_failure(&error);
            return;
        }
    };
    if let Err(error) = records::publish_review_records(&directory, lines) {
        records::note_review_publish_failure(&error);
    }
}

fn validate_operation(network: &str, chain_id: i64) -> Result<(), ReviewError> {
    if network.trim().is_empty() {
        return Err(ReviewError::InvalidRequest(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(ReviewError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    Ok(())
}

fn validate_write_request(
    request: &ReviewWriteRequest,
    network: &str,
    chain_id: i64,
) -> Result<(), ReviewError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(ReviewError::InvalidRequest(
            "write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.operation.trim().is_empty() {
        return Err(ReviewError::InvalidRequest(
            "operation must not be empty".to_owned(),
        ));
    }
    validate_identifier("request_id", &request.request_id, MAX_REQUEST_ID_LENGTH)?;
    validate_identifier(
        "idempotency_key",
        &request.idempotency_key,
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )
}

fn validate_identifier(name: &str, value: &str, max_length: usize) -> Result<(), ReviewError> {
    if value.trim().is_empty() {
        return Err(ReviewError::InvalidRequest(format!(
            "{name} must not be empty"
        )));
    }
    if value.len() > max_length {
        return Err(ReviewError::InvalidRequest(format!(
            "{name} must not exceed {max_length} bytes"
        )));
    }
    Ok(())
}

fn request_digest(request: &ReviewWriteRequest) -> Result<String, ReviewError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream,
        "evidence": request.evidence
    });
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn read_review_input(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ReviewInput, ReviewError> {
    let snapshot = read_closed_loop_snapshot(
        connection
            .path()
            .map(Path::new)
            .unwrap_or_else(|| Path::new("manager.sqlite3")),
        network,
        chain_id,
        None,
        MAX_SNAPSHOT_LIMIT,
    )?;
    if !snapshot.status.enabled {
        return Err(ReviewError::MissingClosedLoopState);
    }
    if snapshot.status.loop_count > snapshot.loops.len() {
        return Err(ReviewError::ClosedLoopSnapshotTruncated {
            expected: snapshot.status.loop_count,
            actual: snapshot.loops.len(),
        });
    }
    // The base pools are resolved **through** the direction layer.  The
    // closed loop publishes directional pools, so this is the only place
    // where a base pool enters the review, and it enters together with the
    // direction it belongs to.
    //
    // The **whole** direction layer of the chain is read, not only the
    // pools the closed loops publish: the review picks the pool of every
    // hop, and the pool it picks may be one the catalog published as a
    // backup.  A review that could only see the published pools would have
    // no state for the pool it actually priced, and it would have to
    // refuse the route it measured.
    let base_pools = read_base_pools_of_directional_pools(
        connection
            .path()
            .map(Path::new)
            .unwrap_or_else(|| Path::new("manager.sqlite3")),
        network,
        chain_id,
    );
    let pool_ids: Vec<String> = base_pools
        .values()
        .map(|base_pool_id| base_pool_id.trim().to_ascii_lowercase())
        .filter(|base_pool_id| !base_pool_id.is_empty())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    // The pool state is **optional** for the review itself.
    //
    // A missing state does not abort the review: it makes the final
    // single-tick gate unable to prove the route, which blocks that route
    // and only that route. Aborting the whole review would turn a
    // measurement gap into an outage, and the review would no longer
    // report the routes it *can* judge.
    //
    // The state is read from the **chain**, not from the stored snapshot:
    // the gate compares the review's own quote against the state the pool
    // is in, and a stored snapshot answers a question about a moment that
    // is already gone.
    let pool_states = read_current_pool_states(
        connection
            .path()
            .map(Path::new)
            .unwrap_or_else(|| Path::new("manager.sqlite3")),
        network,
        chain_id,
        &pool_ids,
    );
    Ok(ReviewInput {
        closed_loop: snapshot.status,
        loops: snapshot.loops,
        hop_directions: hop_directions_by_loop(&snapshot.hops),
        pool_states,
        base_pools,
    })
}

/// The **current** state of the pools the review judges a route against.
///
/// The review prices a route from the chain and then asks whether the pool
/// still sits where the quote said it sits. The second question has to be
/// asked of the chain as well: a stored snapshot is a measurement, and a
/// measurement ages while the pool trades. Comparing a snapshot that is
/// minutes old against a live quote does not report a market move, it
/// reports the age of the snapshot, and the gate would read it as "the
/// market moved against the route" and refuse a route that is still good.
///
/// Pool Manager owns the chain access and publishes the live reading
/// (pinned to one block), so Review Manager never talks to the chain for a
/// pool state of its own. When the live reading is unavailable the stored
/// snapshot is used, and the review keeps the meaning it always had: a hop
/// whose state cannot be read cannot be proven, so that hop is refused.
fn read_current_pool_states(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> BTreeMap<String, PoolParityState> {
    let into_map = |states: Vec<PoolParityState>| -> BTreeMap<String, PoolParityState> {
        states
            .into_iter()
            .map(|state| (state.pool_id.clone(), state))
            .collect()
    };
    let rpc_urls = rpc_urls_for(chain_id);
    if !rpc_urls.is_empty() {
        if let Ok(states) = read_live_pool_parity_states(
            database_path,
            network,
            chain_id,
            NETWORK_PROTOCOL,
            pool_ids,
            &rpc_urls,
            LIVE_POOL_STATE_TIMEOUT_SECONDS,
        ) {
            return into_map(states);
        }
    }
    read_pool_parity_states(
        database_path,
        network,
        chain_id,
        NETWORK_PROTOCOL,
        pool_ids,
    )
    .map(into_map)
    .unwrap_or_default()
}

/// The base pool of every directional pool of the chain.
///
/// The direction layer row owns the base pool facts, so the resolution goes
/// through it: a directional pool that has no row has no base pool, and the
/// review reports the hop as unprovable instead of guessing.
///
/// The whole layer is read, because the review picks the pool of every hop
/// and the pool it picks is not necessarily one the closed loops publish:
/// the catalog publishes one pool per hop and keeps the rest as backups,
/// and the review is free to price a backup.
fn read_base_pools_of_directional_pools(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> BTreeMap<String, String> {
    let mut base_pools = BTreeMap::new();
    let Ok(connection) = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    ) else {
        return base_pools;
    };
    let Ok(mut statement) = connection.prepare(
        "SELECT directional_pool_id, base_pool_id
         FROM manager_v4_directional_exchange_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = 'uniswap_v4'",
    ) else {
        return base_pools;
    };
    let rows = statement.query_map(params![network, chain_id as i64], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    });
    let Ok(rows) = rows else {
        return base_pools;
    };
    for row in rows.flatten() {
        base_pools.insert(row.0, row.1);
    }
    base_pools
}

/// The stored hop directions of every closed loop, in hop order.
///
/// The hop rows are the closed-loop Manager's own records, so the
/// direction is the Manager's number and not one the review derives.
fn hop_directions_by_loop(hops: &[ClosedLoopHopRow]) -> BTreeMap<String, HopDirectionList> {
    // The hop row stores the direction of the directional pool the hop walks,
    // so the direction is always there: a hop that does not name a
    // directional pool cannot be published at all.
    let mut by_loop: BTreeMap<String, BTreeMap<usize, i64>> = BTreeMap::new();
    for hop in hops {
        // The hop rows number their hops from **one**, the route numbers
        // them from zero, so the row is moved onto the route's index.
        let index = hop.hop_index.saturating_sub(1);
        by_loop
            .entry(hop.loop_key.trim().to_lowercase())
            .or_default()
            .insert(index, hop.hop_direction);
    }
    by_loop
        .into_iter()
        .map(|(loop_key, hops)| {
            let length = hops.keys().max().map(|index| index + 1).unwrap_or(0);
            let directions = (0..length)
                .map(|index| hops.get(&index).copied())
                .collect();
            (loop_key, directions)
        })
        .collect()
}

/// The pools the review priced, in hop order.
///
/// The closed loop fixes the **path**: the three tokens and the order they
/// are walked in.  The pool of every hop is chosen when the route is
/// priced, and the quote the measurement carries names the pools it chose
/// in the order the route walked them.
///
/// Those pools are the pools the route traded and the pools the broadcast
/// will trade, so every gate is judged against them.  A quote that names
/// no pool yields an empty list: the route then cannot be proven against
/// any pool, and the review reports it as not comparable rather than
/// falling back to a pool the route never touched.
fn review_time_pool_ids(payload: &serde_json::Value) -> Vec<String> {
    let Some(pools) = payload
        .get("quote")
        .and_then(|quote| {
            quote
                .get("candidate_pools")
                .or_else(|| quote.get("candidatePools"))
        })
        .and_then(|value| value.as_array())
    else {
        return Vec::new();
    };
    pools
        .iter()
        .filter_map(|pool| {
            pool.get("poolId")
                .or_else(|| pool.get("pool_id"))
                .and_then(|value| value.as_str())
                .map(|value| value.trim().to_ascii_lowercase())
                .filter(|value| !value.is_empty())
        })
        .collect()
}

/// The direction a directional pool was published for.
///
/// The id ends with the direction it belongs to, so the direction is read
/// from the id itself and never guessed from the pool's state.
fn directional_pool_direction(directional_pool_id: &str) -> Option<i64> {
    match directional_pool_id.trim().rsplit(':').next() {
        Some("0") => Some(0),
        Some("1") => Some(1),
        _ => None,
    }
}

/// The base pools the closed loop published for one hop.
///
/// A hop publishes one main pool and keeps the rest as candidates, so the
/// set of pools the hop may trade is the main pool and every candidate.
/// The review may only confirm a pool from that set: a pool the loop
/// never offered is not a pool the closed loop could have walked.
fn hop_option_base_pools(
    closed_loop: &ClosedLoopRow,
    hop_index: usize,
    base_pools: &BTreeMap<String, String>,
) -> BTreeSet<String> {
    let mut options = BTreeSet::new();
    if let Some(directional_pool_id) = closed_loop.directional_pool_ids.get(hop_index) {
        if let Some(base_pool_id) = base_pools.get(directional_pool_id) {
            options.insert(base_pool_id.trim().to_ascii_lowercase());
        }
    }
    let hop_key = match hop_index {
        0 => "first",
        1 => "middle",
        2 => "third",
        _ => return options,
    };
    if let Some(candidates) = closed_loop.backup_directional_pool_ids.get(hop_key) {
        for candidate in candidates {
            if let Some(base_pool_id) = base_pools.get(candidate) {
                options.insert(base_pool_id.trim().to_ascii_lowercase());
            }
        }
    }
    options
}

/// The directional pool the review confirmed for one hop.
///
/// The closed loop publishes its pools as **directional** pools, so the
/// confirmation is the option of that hop whose base pool the review
/// priced and whose direction the hop walked in.
///
/// The option is taken from the **loop's own list** and never from a
/// global search of the direction layer: the same base pool is published
/// under more than one scope (`core4` and `candidate_exchange`), and a
/// global search would name the pool of the wrong scope.
fn confirmed_hop_directional_pool(
    closed_loop: &ClosedLoopRow,
    hop_index: usize,
    priced_base_pool_id: &str,
    hop_direction: Option<i64>,
    base_pools: &BTreeMap<String, String>,
) -> Option<String> {
    let priced = priced_base_pool_id.trim().to_ascii_lowercase();
    let mut options: Vec<&String> = Vec::new();
    if let Some(main_pool) = closed_loop.directional_pool_ids.get(hop_index) {
        options.push(main_pool);
    }
    let hop_key = match hop_index {
        0 => "first",
        1 => "middle",
        2 => "third",
        _ => return None,
    };
    if let Some(backups) = closed_loop.backup_directional_pool_ids.get(hop_key) {
        options.extend(backups.iter());
    }
    options
        .into_iter()
        .find(|directional_pool_id| {
            let base_matches = base_pools
                .get(*directional_pool_id)
                .map(|base_pool_id| base_pool_id.trim().eq_ignore_ascii_case(&priced))
                .unwrap_or(false);
            let direction_matches = hop_direction
                .map(|direction| {
                    directional_pool_direction(directional_pool_id) == Some(direction)
                })
                .unwrap_or(true);
            base_matches && direction_matches
        })
        .map(|directional_pool_id| directional_pool_id.clone())
}

/// The directional pools the review confirmed, in hop order.
///
/// A confirmed pool is a **directional** pool: the base pool the review
/// priced together with the direction it walked in, named by the closed
/// loop that published it.
///
/// `None` means the confirmation failed: one hop names a pool the loop
/// published for another hop, or the hop chain itself is incomplete.
fn confirmed_directional_pools(
    closed_loop: &ClosedLoopRow,
    chain: &HopChain,
    priced_pool_ids: &[String],
    base_pools: &BTreeMap<String, String>,
) -> Option<Vec<String>> {
    // The chain and the quote must describe the same hops: a hop that was
    // skipped leaves the two lists of different lengths, and the route
    // then cannot be confirmed against any pool.
    if chain.hops.len() != priced_pool_ids.len() {
        return None;
    }
    let mut confirmed = Vec::with_capacity(chain.hops.len());
    for (hop_index, hop) in chain.hops.iter().enumerate() {
        // A hop that spends token0 trades the lower address into the higher
        // one, so it walks direction 1; a hop that spends token1 walks
        // direction 0.  The chain already proved this against the route.
        let direction = if hop.zero_for_one { 1 } else { 0 };
        confirmed.push(confirmed_hop_directional_pool(
            closed_loop,
            hop_index,
            &hop.pool_id,
            Some(direction),
            base_pools,
        )?);
    }
    Some(confirmed)
}

/// The path key of the pools the review confirmed.
///
/// The key is the closed loop's own key followed by the confirmed pools,
/// so the path the review walked is readable in one string: the loop it
/// belongs to, and the three pools it walked in it.
///
/// A review that confirmed no pool keeps the catalog's path key, because
/// the catalog's pools are then the only pools the review can name.
fn confirmed_pool_path_key(
    closed_loop_path_key: &str,
    loop_key: &str,
    confirmed_pool_ids: Option<&[String]>,
) -> String {
    let Some(confirmed_pool_ids) = confirmed_pool_ids else {
        return closed_loop_path_key.trim().to_owned();
    };
    let mut key = loop_key.trim().to_owned();
    for pool_id in confirmed_pool_ids {
        key.push(':');
        key.push_str(pool_id.trim());
    }
    key
}

fn validate_evidence_membership(
    evidence: &[ReviewEvidenceInput],
    loops: &[ClosedLoopRow],
) -> Result<(), ReviewError> {
    // The published keys are compared in their normalized spelling, but
    // **never** re-ordered: the funding anchor stays the first hop and the
    // direction of the path is preserved.
    let known = loops
        .iter()
        .map(|row| row.loop_key.trim().to_lowercase())
        .collect::<BTreeSet<_>>();
    let mut seen = BTreeSet::new();
    for item in evidence {
        if item.loop_key.trim().is_empty() {
            return Err(ReviewError::InvalidRequest(
                "evidence.loopKey must not be empty".to_owned(),
            ));
        }
        let evidence_key = item.loop_key.trim().to_lowercase();
        // The evidence must name a closed loop the library published,
        // **exactly**.  A closed loop is the funding anchor plus the
        // directed pool path; a path that starts at an intermediate token
        // is a different closed loop with its own capacity and its own
        // threshold, so it may not borrow the published loop's identity.
        // A miss is reported as ``UnknownClosedLoop`` instead of being
        // repaired by re-ordering the token path.
        if !known.contains(evidence_key.as_str()) {
            return Err(ReviewError::UnknownClosedLoop(item.loop_key.clone()));
        }
        // The same published key may not be filed twice: the second
        // measurement would duplicate the first.
        if !seen.insert(evidence_key) {
            return Err(ReviewError::DuplicateEvidence(item.loop_key.clone()));
        }
    }
    Ok(())
}

/// The three-state execution gate of one closed loop.
///
/// The gate compares the state the candidate ranking priced (**expected**)
/// against the state the route would execute against (**current**) and
/// classifies every hop as favorable, unchanged, adverse or fatal.
///
/// A route may only be broadcast when every hop is favorable or unchanged.
/// An adverse hop is a market conclusion and blocks the route with the
/// stable reason `adverse_price_move`; a fatal hop is a measurement or
/// tick-range failure and blocks it with the stable reason of the
/// underlying check.
///
/// A favorable hop does **not** prove a larger profit. It only publishes a
/// candidate capacity, and the final single-tick gate still has to re-price
/// every hop with the final input amount.
///
/// Every hop is priced with the amount that **reaches** it. The first hop
/// spends the borrowed principal and every later hop spends the output of its
/// predecessor, because the three hops of a closed loop trade three different
/// tokens: the raw amount of the principal is not the raw amount of the
/// second or the third hop, it is a different number in a different unit.
/// The chain is walked over the **expected** states, the states the review
/// priced, and the current state of a hop is then compared against the
/// expected state of the *same* hop with the *same* amount, so the verdict
/// answers "did this hop move" and not "did the route move".
fn evaluate_final_state_transition_gate(
    input: &ReviewInput,
    run: &RunArbitrageEvidence,
    chain: Option<&HopChain>,
) -> RouteTransitionVerdict {
    let amount_in_raw = match final_input_amount_raw(run) {
        Some(amount) if amount > 0 => amount,
        _ => {
            return RouteTransitionVerdict {
                broadcast_allowed: false,
                reason: Some("final_input_amount_unavailable".to_string()),
                hops: Vec::new(),
            }
        }
    };
    // The expected state is the one the candidate ranking recorded. The
    // current state is the one the Manager has stored for the same pool.
    let Ok(run_payload) = serde_json::to_value(run) else {
        return RouteTransitionVerdict {
            broadcast_allowed: false,
            reason: Some("run_arbitrage_evidence_unavailable".to_string()),
            hops: Vec::new(),
        };
    };
    let expected_states = extract_review_time_pool_states(&run_payload);
    let expected_by_pool: BTreeMap<String, ReviewTimePoolState> = expected_states
        .into_iter()
        .map(|state| (state.pool_id.clone(), state))
        .collect();
    // The hop chain is built **once** per closed loop, with the stored
    // direction of every hop and the cross-check against the route.  A hop
    // whose stored direction contradicts the route is not part of the
    // chain, so the route can never be proven executable over it.
    let Some(chain) = chain else {
        return RouteTransitionVerdict {
            broadcast_allowed: false,
            reason: Some("route_state_unavailable".to_string()),
            hops: Vec::new(),
        };
    };

    let mut execution_hops = Vec::with_capacity(chain.hops.len());
    // The amount that reaches the hop being judged. The first hop receives the
    // borrowed principal; every later hop receives the output of its
    // predecessor, so the amount is carried along the chain instead of being
    // repeated for every hop.
    let mut chain_amount_in_raw = amount_in_raw;
    for hop in &chain.hops {
        let Some(current) = input.pool_states.get(&hop.pool_id) else {
            return RouteTransitionVerdict {
                broadcast_allowed: false,
                reason: Some(format!("current_pool_state_unavailable:{}", hop.pool_id)),
                hops: Vec::new(),
            };
        };
        let Some(expected) = expected_by_pool.get(&hop.pool_id) else {
            // The expected state is missing, so the move cannot be
            // classified. "Cannot prove" is not "is false": the hop is
            // fatal and the route may not be broadcast.
            return RouteTransitionVerdict {
                broadcast_allowed: false,
                reason: Some(format!("expected_pool_state_unavailable:{}", hop.pool_id)),
                hops: Vec::new(),
            };
        };
        if expected.tick_spacing != current.tick_spacing {
            return RouteTransitionVerdict {
                broadcast_allowed: false,
                reason: Some(format!("tick_spacing_changed:{}", hop.pool_id)),
                hops: Vec::new(),
            };
        }
        let hop_amount_in_raw = chain_amount_in_raw;
        // The hop is priced with the amount that reaches it, so the expected
        // capacity is the exact single-tick bound of **that** hop for
        // **that** amount, and the classifier can tell an exact capacity
        // change from a price-distance approximation.
        let hop_amount = match hop_expected_amount(expected, hop, hop_amount_in_raw) {
            Ok(amount) => amount,
            Err(HopAmountFailure::CrossesTick) => {
                return RouteTransitionVerdict {
                    broadcast_allowed: false,
                    reason: Some(format!("expected_amount_crosses_tick:{}", hop.pool_id)),
                    hops: Vec::new(),
                }
            }
            Err(HopAmountFailure::Unpriceable) => {
                return RouteTransitionVerdict {
                    broadcast_allowed: false,
                    reason: Some(format!("expected_quote_unavailable:{}", hop.pool_id)),
                    hops: Vec::new(),
                }
            }
        };
        // The next hop spends what this hop produced.
        chain_amount_in_raw = hop_amount.amount_out_raw;
        execution_hops.push(HopExecutionState {
            pool_id: hop.pool_id.clone(),
            expected: SingleTickExecutionState {
                sqrt_price_x96: expected.sqrt_price_x96,
                liquidity: expected.liquidity,
                tick: expected.tick as i32,
                tick_spacing: expected.tick_spacing as i32,
            },
            current: SingleTickExecutionState {
                sqrt_price_x96: current.sqrt_price_x96,
                liquidity: current.liquidity,
                tick: current.tick as i32,
                tick_spacing: current.tick_spacing as i32,
            },
            fee_pips: hop.fee_pips,
            zero_for_one: hop.zero_for_one,
            amount_in_raw: hop_amount_in_raw,
            expected_capacity_raw: hop_amount.expected_capacity_raw,
        });
    }
    evaluate_route_transition(&execution_hops, DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS)
}

/// The per-hop price limits the review publishes for the route.
///
/// The limit of a hop is the boundary of the tick range the **review
/// priced**: the price the runtime measured, and the tick range that price
/// sits in. The contract enforces the limit, so the limit is the proof that
/// the transaction may only trade inside the range the review measured. A
/// market that moved past a limit makes the transaction revert instead of
/// trading outside the range, which is why the limit does not have to be
/// re-measured a second time before the transaction is sent.
///
/// The limits come from the review's own quote and from nothing else. A
/// stored snapshot is a measurement, and a measurement ages: a limit taken
/// from a snapshot would publish a range the review never priced, and the
/// transaction would be bounded by a range nobody measured.
///
/// The list is empty unless **every** hop proved a usable limit. A partial
/// list is not safe to encode: a missing hop would silently receive the
/// global V4 price bound, which is exactly what version 1 forbids.
fn review_price_limits(
    run: Option<&RunArbitrageEvidence>,
    chain: Option<&HopChain>,
) -> Vec<String> {
    let (Some(run), Some(chain)) = (run, chain) else {
        return Vec::new();
    };
    if chain.hops.len() != 3 {
        return Vec::new();
    }
    let Ok(run_payload) = serde_json::to_value(run) else {
        return Vec::new();
    };
    let expected_by_pool: BTreeMap<String, ReviewTimePoolState> =
        extract_review_time_pool_states(&run_payload)
            .into_iter()
            .map(|state| (state.pool_id.clone(), state))
            .collect();
    let mut limits = Vec::with_capacity(chain.hops.len());
    for hop in &chain.hops {
        let Some(expected) = expected_by_pool.get(&hop.pool_id) else {
            return Vec::new();
        };
        if expected.tick_spacing <= 0 || expected.sqrt_price_x96 == 0 {
            return Vec::new();
        }
        let Ok(bounds) = pool_manager::depth::tick_range_bounds(
            expected.tick as i32,
            expected.tick_spacing as i32,
            expected.sqrt_price_x96,
        ) else {
            return Vec::new();
        };
        // The swap runs towards the boundary the direction names: a hop that
        // spends token0 walks the price down, and one that spends token1
        // walks it up.
        let boundary = if hop.zero_for_one {
            bounds.lower_sqrt_price_x96
        } else {
            bounds.upper_sqrt_price_x96
        };
        if boundary == 0 {
            return Vec::new();
        }
        limits.push(boundary.to_string());
    }
    limits
}

/// The amount a hop carries and the exact bound of that hop.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct HopAmount {
    /// The amount that reaches the hop, in raw units.
    amount_in_raw: u128,
    /// The exact single-tick bound of the hop in the expected state.
    expected_capacity_raw: u128,
    /// The amount the hop produces, which the next hop receives.
    amount_out_raw: u128,
}

/// Why a hop could not be priced with the amount that reaches it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum HopAmountFailure {
    /// The hop's expected state does not keep the amount inside its tick
    /// range, so the hop would cross the boundary the version forbids.
    CrossesTick,
    /// The hop's expected state cannot price the amount at all.
    Unpriceable,
}

/// Prices one hop with the amount that **reaches** it.
///
/// The hop is priced with the **expected** state, the state the candidate
/// ranking recorded, because the amount that reaches a hop is the output of
/// its predecessor and the predecessor was priced with the expected state as
/// well. The hop therefore produces the amount the next hop receives.
fn hop_expected_amount(
    expected: &ReviewTimePoolState,
    hop: &HopState,
    amount_in_raw: u128,
) -> Result<HopAmount, HopAmountFailure> {
    let quote = match pool_manager::depth::single_tick_swap_amount_out(
        expected.sqrt_price_x96,
        expected.liquidity,
        hop.fee_pips,
        expected.tick_spacing as i32,
        expected.tick as i32,
        amount_in_raw,
        hop.zero_for_one,
    ) {
        Ok(quote) if quote.inside_tick_range => quote,
        Ok(_) => return Err(HopAmountFailure::CrossesTick),
        Err(_) => return Err(HopAmountFailure::Unpriceable),
    };
    Ok(HopAmount {
        amount_in_raw,
        expected_capacity_raw: quote.max_amount_in_raw,
        amount_out_raw: quote.amount_out_raw,
    })
}

/// The final single-tick gate of one closed loop.
///
/// The gate answers the question the candidate ranking could not: with the
/// **actual** input amount of every hop, does the route stay inside one
/// tick range per hop?
///
/// The answer is fail-closed. A route that cannot be proven single-tick is
/// not executable, and an executable route is the only one that may become
/// a broadcast request. The gate therefore never turns a route into a
/// broadcast request by itself; it can only take one away.
fn evaluate_final_single_tick_gate(
    input: &ReviewInput,
    closed_loop: &ClosedLoopRow,
    run: &RunArbitrageEvidence,
    chain: Option<&HopChain>,
) -> SingleTickRouteVerdict {
    let amount_in_raw = match final_input_amount_raw(run) {
        Some(amount) if amount > 0 => amount,
        Some(_) => {
            return SingleTickRouteVerdict {
                executable: false,
                reason: Some("final_input_amount_unavailable".to_string()),
                hops: Vec::new(),
            }
        }
        None => {
            return SingleTickRouteVerdict {
                executable: false,
                reason: Some("final_input_amount_unavailable".to_string()),
                hops: Vec::new(),
            }
        }
    };
    // The folded single-tick capacity is the path-level bound of the route.
    //
    // The per-hop gate below proves that each hop stays inside its own tick
    // range. The folded capacity is the stronger statement: it is the
    // smallest of the three hop capacities after every hop has been
    // converted into the units of the anchor token, so an input above it
    // cannot be carried by **some** hop of the route even when every
    // individual hop would still fit on its own.
    //
    // A route without a published folded capacity is a coverage gap of the
    // pool index. It is not proof that the route fits, so it is blocked
    // here: the review may not promote an unproven route into a broadcast
    // request.
    let Some(capacity_anchor_raw) = closed_loop.single_tick_capacity_anchor_raw else {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("single_tick_capacity_unavailable".to_string()),
            hops: Vec::new(),
        };
    };
    if capacity_anchor_raw == 0 {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("single_tick_capacity_unusable".to_string()),
            hops: Vec::new(),
        };
    }
    if amount_in_raw > capacity_anchor_raw {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("final_input_exceeds_single_tick_capacity".to_string()),
            hops: Vec::new(),
        };
    }
    // The hop chain is built **once** per closed loop, with the stored
    // direction of every hop and the cross-check against the route.
    let Some(chain) = chain else {
        return SingleTickRouteVerdict {
            executable: false,
            reason: Some("route_state_unavailable".to_string()),
            hops: Vec::new(),
        };
    };
    let extras: Vec<SingleTickHopState> = chain
        .hops
        .iter()
        .map(|hop| {
            let state = input.pool_states.get(&hop.pool_id);
            SingleTickHopState {
                pool_id: hop.pool_id.clone(),
                tick: state.map(|value| value.tick as i32).unwrap_or_default(),
                tick_spacing: state
                    .map(|value| value.tick_spacing as i32)
                    .unwrap_or_default(),
                pool_state_version: state.and_then(|value| value.liquidity_read_at.clone()),
                snapshot_batch: state.and_then(|value| value.materialization_batch.clone()),
            }
        })
        .collect();
    evaluate_route_single_tick(&chain.hops, &extras, amount_in_raw)
}

/// The final input amount the chain was asked to execute with.
///
/// The candidate ranking prices a hop with a **search** amount, which is
/// only a screening input. The final review has to use the amount the
/// chain would actually be asked for, and that amount is the one the
/// `runArbitrage` evidence carries.
///
/// Several spellings are accepted because the evidence is produced by the
/// runtime, not by this module: the raw hex amount is the canonical form,
/// and the decimal form is the fallback.
fn final_input_amount_raw(run: &RunArbitrageEvidence) -> Option<u128> {
    const AMOUNT_KEYS: [&str; 6] = [
        "selectedAmountRaw",
        "initialAmountRaw",
        "amountInRaw",
        "amountRaw",
        "selectedAmount",
        "initialAmount",
    ];
    for key in AMOUNT_KEYS {
        let Some(value) = run.raw.get(key) else {
            continue;
        };
        if let Some(amount) = parse_raw_amount(value) {
            if amount > 0 {
                return Some(amount);
            }
        }
    }
    None
}

/// Parses one raw amount, in hex, decimal or string form.
fn parse_raw_amount(value: &serde_json::Value) -> Option<u128> {
    match value {
        serde_json::Value::String(text) => {
            let text = text.trim();
            if text.is_empty() {
                return None;
            }
            if let Some(hex) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
                return u128::from_str_radix(hex, 16).ok();
            }
            text.parse::<u128>().ok()
        }
        serde_json::Value::Number(number) => number
            .as_u64()
            .map(|value| u128::from(value))
            .or_else(|| number.to_string().parse::<u128>().ok()),
        _ => None,
    }
}

/// Whether the amount ladder really measured the route.
///
/// A route may only be declared profitable when the ladder **probed** it.
/// The profit of a route that was never probed is the rounding of a dust
/// amount: the contract prices it, the fee it charges is smaller than one
/// unit, and the remainder reads as a profit that was never earned.
///
/// The ladder publishes the measurement, so the review reads it and never
/// assumes it: a preview without a measured amount is not a measurement.
fn ladder_measured(static_preview: &serde_json::Value) -> bool {
    let Some(calculation) = static_preview
        .get("bestAmountCalculation")
        .and_then(|value| value.as_object())
    else {
        return false;
    };
    let measured = calculation
        .get("bestAmountMeasured")
        .and_then(|value| value.as_bool())
        .unwrap_or(false);
    let probes = calculation
        .get("probeCount")
        .and_then(|value| value.as_u64())
        .unwrap_or(0);
    measured && probes > 0
}

/// The reason the ladder did not measure the route.
///
/// The status is the ladder's own diagnosis, so the reason names the
/// block that stopped it instead of a generic word.
fn ladder_unmeasured_reason(static_preview: &serde_json::Value) -> String {
    static_preview
        .get("bestAmountCalculation")
        .and_then(|value| value.get("bestAmountLadderStatus"))
        .and_then(|value| value.as_str())
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_owned())
}

fn build_review_rows(
    input: &ReviewInput,
    evidence: &[ReviewEvidenceInput],
) -> Result<DesiredReviewData, ReviewError> {
    // The evidence is indexed by the **exact published loop key**. One
    // measurement belongs to the closed loop it names; a rotation names a
    // different closed loop and gets no evidence of its own, so it is not
    // silently credited with the measurement of its rotation.
    let evidence_by_loop = evidence
        .iter()
        .map(|item| (item.loop_key.trim().to_lowercase(), item))
        .collect::<BTreeMap<_, _>>();
    let mut desired = DesiredReviewData::default();
    for closed_loop in &input.loops {
        // A closed loop that was **not measured in this pass** keeps the
        // review it already has: the write touches the loops it carries
        // and nothing else.  One loop's measurement therefore never
        // decides another loop's review - not by blanking it, and not by
        // gating the whole write on it.
        let Some(item) = evidence_by_loop
            .get(&closed_loop.loop_key.trim().to_lowercase())
            .copied()
        else {
            continue;
        };
        let run_arbitrage = item.run_arbitrage.as_ref();
        let run_json = match run_arbitrage {
            Some(value) => serde_json::to_value(value)?,
            None => serde_json::Value::Null,
        };
        let static_preview = normalize_object(&item.static_preview);
        let reviewed_at = item
            .reviewed_at
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or("unreported")
            .to_owned();

        let mut diagnostics = BTreeMap::new();
        for diagnostic in &item.diagnostics {
            insert_diagnostic(&mut diagnostics, &closed_loop.loop_key, diagnostic.clone())?;
        }
        if let Some(run) = run_arbitrage {
            for diagnostic in &run.diagnostics {
                insert_diagnostic(&mut diagnostics, &closed_loop.loop_key, diagnostic.clone())?;
            }
        }

        let signed_pnl = run_arbitrage
            .and_then(|value| value.signed_pnl.as_ref())
            .filter(|value| validate_signed_pnl(value));
        // The hop chain is built **once** per closed loop: the pools the
        // review priced, with the stored direction of every hop,
        // cross-checked against the route the kernel measured.
        //
        // The closed loop fixes the path, and the **review** fixes the
        // pools: the quote the measurement carries names the pool of every
        // hop, and those are the pools the route traded and the pools the
        // broadcast will trade.  The chain is therefore built over the
        // priced pools, and the catalog's own pool of a hop is never
        // substituted for a pool the review did not measure.
        //
        // A hop whose stored direction contradicts the route is **skipped**
        // and reported.  It is never rewritten, and it never voids the
        // review of the whole closed loop: the remaining hops are judged
        // normally, and the route simply cannot be proven executable over
        // the hop that drifted.
        let priced_pool_ids = review_time_pool_ids(&run_json);
        let hop_chain = build_hop_chain(
            &closed_loop.path_addresses,
            &input.pool_states,
            &priced_pool_ids,
            input
                .hop_directions
                .get(&closed_loop.loop_key.trim().to_lowercase()),
        );
        // The pools the review confirmed: the priced pool of every hop,
        // with the direction it was walked in.  The confirmation is what
        // the broadcast trades, so it is published with the review and it
        // is what the broadcast compares the route against.
        let confirmed_pool_ids = hop_chain
            .as_ref()
            .and_then(|chain| {
                confirmed_directional_pools(
                    closed_loop,
                    chain,
                    &priced_pool_ids,
                    &input.base_pools,
                )
            });
        // The pools the review confirms must be pools the closed loop
        // published for that hop: the main pool, or one of the candidates
        // the hop keeps as a backup.  A pool outside that set is not a
        // pool the closed loop could have walked, so the review may not
        // confirm it and the route may never be broadcast.
        //
        // A hop that published **no** option at all is the same case: the
        // loop never offered a pool for that hop, so nothing the route
        // priced can be confirmed.  The guard is closed on both counts,
        // because a review that confirms an unproven pool is exactly the
        // failure the confirmation exists to prevent.
        let pool_outside_options = priced_pool_ids
            .iter()
            .enumerate()
            .find_map(|(hop_index, pool_id)| {
                let options = hop_option_base_pools(closed_loop, hop_index, &input.base_pools);
                if options.contains(pool_id.trim()) {
                    None
                } else {
                    Some((hop_index, pool_id.clone()))
                }
            });
        // A hop that published no option at all cannot be confirmed, and
        // it is reported apart from a foreign pool: the first is a gap in
        // the catalog, the second is a pool the route invented.
        let hop_without_options = priced_pool_ids
            .iter()
            .enumerate()
            .find_map(|(hop_index, _)| {
                let options = hop_option_base_pools(closed_loop, hop_index, &input.base_pools);
                if options.is_empty() {
                    Some(hop_index)
                } else {
                    None
                }
            });
        // A hop that trades one of its candidates instead of its main pool
        // is a **substitution**, and it is recorded: the main pool no
        // longer carries the trade, and the candidate did.
        //
        // The chain and the quote must describe the same hops, or the hop
        // index of a chain hop is not the hop of the route: a skipped hop
        // shifts the list and the substitution would be filed against the
        // wrong hop.
        if let Some(chain) = hop_chain
            .as_ref()
            .filter(|chain| chain.hops.len() == priced_pool_ids.len())
        {
            let main_pools = &closed_loop.directional_pool_ids;
            for (hop_index, hop) in chain.hops.iter().enumerate() {
                let Some(main_directional_pool_id) = main_pools.get(hop_index) else {
                    continue;
                };
                let main_base_pool_id = input
                    .base_pools
                    .get(main_directional_pool_id)
                    .map(|value| value.trim().to_ascii_lowercase())
                    .unwrap_or_default();
                if main_base_pool_id.is_empty()
                    || main_base_pool_id == hop.pool_id.trim().to_ascii_lowercase()
                {
                    continue;
                }
                insert_candidate_substitution_diagnostic(
                    &mut diagnostics,
                    &closed_loop.loop_key,
                    hop_index,
                    &hop.pool_id,
                    &main_base_pool_id,
                )?;
            }
        }
        if let Some(chain) = hop_chain.as_ref() {
            for mismatch in &chain.direction_mismatches {
                insert_direction_mismatch_diagnostic(
                    &mut diagnostics,
                    &closed_loop.loop_key,
                    mismatch,
                )?;
            }
        }
        // The final single-tick gate runs **before** the verdict is formed.
        //
        // The upstream `runArbitrage` evidence says the chain accepted the
        // route; the gate says the route may be executed under the rules of
        // version 1. A route that crosses a tick, drifts away from its pool
        // state version, or simply cannot be proven single-tick is
        // **not executable**, whatever the upstream evidence says, and it
        // never becomes a broadcast request.
        let single_tick_verdict = run_arbitrage
            .filter(|run| run.ok && run.executable)
            .map(|run| {
                evaluate_final_single_tick_gate(input, closed_loop, run, hop_chain.as_ref())
            });
        let single_tick_blocked = single_tick_verdict
            .as_ref()
            .filter(|verdict| !verdict.executable);
        if let Some(verdict) = single_tick_blocked {
            insert_single_tick_diagnostic(&mut diagnostics, &closed_loop.loop_key, verdict)?;
        }
        // The three-state execution gate runs **before** the verdict is
        // formed, next to the single-tick gate.
        //
        // The single-tick gate proves that the route fits inside one tick
        // range per hop. The transition gate proves that the market has not
        // moved against the route since the candidate ranking priced it.
        // Both are fail-closed: a route that cannot be proven executable may
        // never become a broadcast request.
        let transition_verdict = run_arbitrage
            .filter(|run| run.ok && run.executable)
            .map(|run| {
                evaluate_final_state_transition_gate(input, run, hop_chain.as_ref())
            });
        // The market-move classification is **evidence**, not a gate.
        //
        // The review priced the route against the chain and publishes the
        // proof of it: the three per-hop price limits are the tick ranges
        // the contract enforces. A market that moved past a limit makes
        // the transaction revert on chain, so the trade costs gas and never
        // principal. Refusing the route here instead would refuse a route
        // that is still good, and it would refuse it over a **measurement**:
        // the gate compares the quote with a reading taken at another
        // moment, and the difference between the two is at least as much
        // the age of the reading as it is a move of the market.
        //
        // The classification is therefore recorded and published, and the
        // route continues to the broadcast request.
        let transition_blocked = transition_verdict
            .as_ref()
            .filter(|verdict| !verdict.broadcast_allowed);
        if let Some(verdict) = transition_blocked {
            insert_transition_diagnostic(&mut diagnostics, &closed_loop.loop_key, verdict)?;
        }
        // The price limits are part of the execution proof, not advisory
        // metadata. A route with fewer than three limits cannot be encoded
        // as a bounded three-hop swap, so it must never become a broadcast
        // request.
        //
        // They are the tick ranges of the prices **the review priced**, so
        // they are available whenever the review measured the route, and
        // they do not depend on a second reading taken at another moment.
        let hop_sqrt_price_limits = review_price_limits(run_arbitrage, hop_chain.as_ref());
        let hop_sqrt_price_limits = if hop_sqrt_price_limits.is_empty() {
            transition_verdict
                .as_ref()
                .map(route_transition_price_limits)
                .unwrap_or_default()
        } else {
            hop_sqrt_price_limits
        };
        let (review_status, review_reason) = match run_arbitrage {
            None => {
                insert_synthetic_missing_diagnostic(
                    &mut diagnostics,
                    &closed_loop.loop_key,
                    "runArbitrage evidence is missing",
                )?;
                (
                    "data_incomplete",
                    Some("runArbitrage evidence is missing".to_owned()),
                )
            }
            Some(_) if signed_pnl.is_none() => {
                insert_synthetic_missing_diagnostic(
                    &mut diagnostics,
                    &closed_loop.loop_key,
                    "signed PnL is missing or incomplete",
                )?;
                (
                    "data_incomplete",
                    Some("signed PnL is missing or incomplete".to_owned()),
                )
            }
            Some(run) if run.ok && run.executable => {
                match (single_tick_blocked, transition_blocked) {
                    // The route traded a pool the closed loop never
                    // published for that hop, so the route is not the
                    // closed loop: the review may not confirm it and it
                    // may never be broadcast.
                    (_, _) if hop_without_options.is_some() => (
                        "not_executable",
                        Some(format!(
                            "loop_options_unavailable:hop_{}",
                            hop_without_options.unwrap_or_default()
                        )),
                    ),
                    (_, _) if pool_outside_options.is_some() => (
                        "not_executable",
                        Some(format!(
                            "pool_not_in_loop_options:hop_{}:{}",
                            pool_outside_options
                                .as_ref()
                                .map(|(hop_index, _)| *hop_index)
                                .unwrap_or_default(),
                            pool_outside_options
                                .as_ref()
                                .map(|(_, pool_id)| pool_id.trim().to_owned())
                                .unwrap_or_default()
                        )),
                    ),
                    (Some(verdict), _) => (
                        "not_executable",
                        Some(format!(
                            "single_tick_gate_failed:{}",
                            verdict
                                .reason
                                .clone()
                                .unwrap_or_else(|| "unknown".to_owned())
                        )),
                    ),
                    // The market-move classification does not decide the
                    // verdict: the route keeps the verdict its own
                    // measurement gave it, and the price limits it
                    // publishes are what the contract enforces on chain.
                    // The classification travels with the review as a
                    // diagnostic, so the movement is on the record.
                    // The ladder arm does not read the market-move
                    // classification at all: that classification is
                    // evidence and it travels with the review as a
                    // diagnostic.  A route the ladder never probed is an
                    // incomplete measurement whatever the market did, so
                    // the two questions are answered independently.
                    (_, _) if !ladder_measured(&static_preview) => (
                        "data_incomplete",
                        Some(format!(
                            "ladder_not_measured:{}",
                            ladder_unmeasured_reason(&static_preview)
                        )),
                    ),
                    // The gates passed, so the only thing left is the
                    // measurement itself: a route the ladder never
                    // probed has no measured profit, and a profit that
                    // was never measured is the rounding of a dust
                    // amount.  Such a route is **not** a positive
                    // opportunity, so it is filed as an incomplete
                    // measurement and it never becomes a broadcast
                    // request.
                    (_, _) => ("completed", None),
                }
            }
            Some(run) if !run.ok => (
                "not_executable",
                Some("runArbitrage returned ok=false".to_owned()),
            ),
            Some(_) => (
                "not_executable",
                Some("runArbitrage returned executable=false".to_owned()),
            ),
        };

        let page_category = review_page_category(review_status, signed_pnl).to_owned();
        // The row is filed under the generation the measurement was made
        // under.  The closed-loop catalog is rebuilt continuously, so the
        // generation the Review Manager reads now is often not the one the
        // route was priced against; relabelling the review would make an
        // old measurement look like a fresh one.
        let generation = item.closed_loop_generation.as_ref();
        let current_closed_loop_checksum = closed_loop_checksum(&input.closed_loop)?.to_owned();
        let mut row = ReviewRow {
            loop_key: closed_loop.loop_key.clone(),
            closed_loop_version: generation
                .and_then(|value| value.closed_loop_version)
                .unwrap_or(input.closed_loop.closed_loop_version),
            closed_loop_checksum: generation
                .and_then(|value| value.closed_loop_checksum.clone())
                .unwrap_or(current_closed_loop_checksum),
            candidate_edge_version: generation
                .and_then(|value| value.candidate_edge_version)
                .unwrap_or(closed_loop.candidate_edge_version),
            candidate_edge_checksum: generation
                .and_then(|value| value.candidate_edge_checksum.clone())
                .unwrap_or_else(|| closed_loop.candidate_edge_checksum.clone()),
            observation_version: generation
                .and_then(|value| value.observation_version)
                .unwrap_or(closed_loop.observation_version),
            observation_checksum: generation
                .and_then(|value| value.observation_checksum.clone())
                .unwrap_or_else(|| closed_loop.observation_checksum.clone()),
            pool_scope_checksum: input
                .closed_loop
                .pool_scope_checksum
                .clone()
                .ok_or(ReviewError::MissingClosedLoopState)?,
            anchor_token_key_id: closed_loop.anchor_token_key_id.clone(),
            anchor_address: closed_loop.anchor_address.clone(),
            anchor_symbol: closed_loop.anchor_symbol.clone(),
            path_text: closed_loop.path_text.clone(),
            path_addresses: closed_loop.path_addresses.clone(),
            path_symbols: closed_loop.path_symbols.clone(),
            // The review publishes the pools **it** measured, which are
            // the directional pools of the priced base pool and the
            // direction the hop walked in.  The catalog's own pool of a
            // hop is a recommendation: the review is the point where the
            // pool of a hop is confirmed, and the broadcast trades the
            // confirmed pool.
            pool_path_key: confirmed_pool_path_key(
                &closed_loop.pool_path_key,
                &closed_loop.loop_key,
                confirmed_pool_ids.as_deref(),
            ),
            pool_ids: confirmed_pool_ids
                .clone()
                .unwrap_or_else(|| closed_loop.directional_pool_ids.clone()),
            review_status: review_status.to_owned(),
            page_category,
            review_reason,
            static_preview,
            run_arbitrage: run_json,
            signed_pnl: signed_pnl.cloned(),
            reviewed_at,
            single_tick_capacity_anchor_raw: closed_loop.single_tick_capacity_anchor_raw,
            single_tick_capacity_binding_pool_id: closed_loop
                .single_tick_capacity_binding_directional_pool_id
                .clone(),
            hop_sqrt_price_limits: hop_sqrt_price_limits.clone(),
            // The identity travels with the review. It is **copied**, never
            // rebuilt: the closed-loop kernel measured it against the
            // catalog the closed-loop Manager publishes, and the Review
            // Manager has no way to measure it again.
            closed_loop_identity: item.closed_loop_identity.clone(),
        };
        // The broadcast policy has **no freshness window**: a positive
        // profit is broadcast the moment it is measured.  The only
        // question the stamp has to answer is whether it is a usable
        // moment at all -- a stamp that cannot be read, or one that
        // claims to lie in the future, is not a measurement.
        let stamp_is_usable = review_stamp_is_usable(&row.reviewed_at);
        if review_status == "completed" {
            if let Some(pnl) = &row.signed_pnl {
                if pnl.amount > 0.0
                    && stamp_is_usable
                    && row.hop_sqrt_price_limits.len() == 3
                    // The broadcast trades the pools the review confirmed,
                    // so a request without a confirmed pool per hop is
                    // not a request anybody may execute.
                    && confirmed_pool_ids
                        .as_ref()
                        .is_some_and(|pool_ids| pool_ids.len() == 3)
                {
                    desired.broadcast_requests.push(BroadcastRequestRow {
                        loop_key: row.loop_key.clone(),
                        closed_loop_version: row.closed_loop_version,
                        closed_loop_checksum: row.closed_loop_checksum.clone(),
                        anchor_token_key_id: row.anchor_token_key_id.clone(),
                        path_text: row.path_text.clone(),
                        path_addresses: row.path_addresses.clone(),
                        path_symbols: row.path_symbols.clone(),
                        pool_path_key: row.pool_path_key.clone(),
                        pool_ids: row.pool_ids.clone(),
                        signed_pnl: pnl.clone(),
                        request_status: "generated".to_owned(),
                        request_reason: Some(
                            "completed review with positive executable signed PnL".to_owned(),
                        ),
                        admission: BroadcastAdmissionFacts {
                            review_status: row.review_status.clone(),
                            run_ok: run_ok_of(&row.run_arbitrage),
                            run_executable: run_executable_of(&row.run_arbitrage),
                            reviewed_at: row.reviewed_at.clone(),
                        },
                        single_tick_capacity_anchor_raw: row.single_tick_capacity_anchor_raw,
                        single_tick_capacity_binding_pool_id: row
                            .single_tick_capacity_binding_pool_id
                            .clone(),
                        hop_sqrt_price_limits: row.hop_sqrt_price_limits.clone(),
                    });
                } else if pnl.amount > 0.0 {
                    row.review_reason = Some(if row.hop_sqrt_price_limits.len() != 3 {
                        "positive review has no complete per-hop price limits and cannot trigger automatic broadcast"
                                .to_owned()
                    } else if confirmed_pool_ids
                        .as_ref()
                        .is_none_or(|pool_ids| pool_ids.len() != 3)
                    {
                        "positive review confirmed no pool for every hop and cannot trigger automatic broadcast"
                            .to_owned()
                    } else {
                        "positive review carries no usable measurement moment and cannot \
                         trigger automatic broadcast"
                            .to_owned()
                    });
                }
            }
        }
        desired.diagnostics.extend(diagnostics.into_values());
        desired.reviews.push(row);
    }
    desired
        .reviews
        .sort_by(|left, right| left.loop_key.cmp(&right.loop_key));
    desired.diagnostics.sort_by(|left, right| {
        (&left.loop_key, &left.diagnostic_id).cmp(&(&right.loop_key, &right.diagnostic_id))
    });
    desired
        .broadcast_requests
        .sort_by(|left, right| left.loop_key.cmp(&right.loop_key));
    Ok(desired)
}

fn review_page_category(review_status: &str, signed_pnl: Option<&SignedPnl>) -> &'static str {
    match review_status {
        "completed" if signed_pnl.is_some_and(|pnl| pnl.amount > 0.0) => "ready_not_submitted",
        "completed" => "confirmed_no_profit",
        "data_incomplete" => "data_incomplete",
        "not_executable" | "failed" => "review_failed",
        _ => "other",
    }
}

fn insert_diagnostic(
    diagnostics: &mut BTreeMap<String, ReviewDiagnosticRow>,
    loop_key: &str,
    input: ReviewDiagnosticInput,
) -> Result<(), ReviewError> {
    let payload = normalize_object(&input.payload);
    let diagnostic_id = input
        .diagnostic_id
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| {
            let mut identity = serde_json::Map::new();
            identity.insert(
                "loopKey".to_owned(),
                serde_json::Value::String(loop_key.to_owned()),
            );
            identity.insert(
                "type".to_owned(),
                serde_json::Value::String(input.diagnostic_type.as_str().to_owned()),
            );
            identity.insert(
                "code".to_owned(),
                option_string(input.diagnostic_code.clone()),
            );
            identity.insert(
                "hopIndex".to_owned(),
                input.hop_index.map_or(serde_json::Value::Null, |value| {
                    serde_json::Value::Number(value.into())
                }),
            );
            identity.insert("poolId".to_owned(), option_string(input.pool_id.clone()));
            identity.insert("selector".to_owned(), option_string(input.selector.clone()));
            identity.insert("payload".to_owned(), payload.clone());
            checksum_bytes(&serde_json::to_vec(&identity).unwrap_or_default())
        });
    let row = ReviewDiagnosticRow {
        loop_key: loop_key.to_owned(),
        diagnostic_id: diagnostic_id.clone(),
        diagnostic_type: input.diagnostic_type.as_str().to_owned(),
        diagnostic_code: input.diagnostic_code,
        message: input.message,
        hop_index: input.hop_index,
        pool_id: input.pool_id,
        selector: input.selector,
        payload,
        observed_at: input
            .observed_at
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| "unreported".to_owned()),
    };
    if diagnostics.insert(diagnostic_id.clone(), row).is_some() {
        return Err(ReviewError::DuplicateDiagnostic {
            loop_key: loop_key.to_owned(),
            diagnostic_id,
        });
    }
    Ok(())
}

/// Records the per-hop evidence of a failed single-tick gate.
///
/// The diagnostic carries the **whole** per-hop picture, not only the hop
/// that failed: the initialized tick, the actual output, the distance to
/// the next tick and the bound conclusion of **every** hop. A reviewer
/// therefore sees the whole route and not just the single hop that stopped
/// it.
fn insert_single_tick_diagnostic(
    diagnostics: &mut BTreeMap<String, ReviewDiagnosticRow>,
    loop_key: &str,
    verdict: &SingleTickRouteVerdict,
) -> Result<(), ReviewError> {
    let reason = verdict
        .reason
        .clone()
        .unwrap_or_else(|| "unknown".to_owned());
    insert_diagnostic(
        diagnostics,
        loop_key,
        ReviewDiagnosticInput {
            diagnostic_id: Some(format!("single-tick-gate:{loop_key}")),
            diagnostic_type: ReviewDiagnosticType::Other,
            diagnostic_code: Some("SingleTickGateFailed".to_owned()),
            message: Some(format!(
                "the route failed the final single-tick gate: {reason}"
            )),
            hop_index: None,
            pool_id: None,
            selector: None,
            payload: serde_json::json!({
                "reason": reason,
                "executable": verdict.executable,
                "hops": single_tick_hop_payload(verdict),
            }),
            observed_at: None,
        },
    )
}

/// Records the three-state transition verdict as a diagnostic.
///
/// The payload carries the whole picture the plan requires: the old and the
/// new price, the price limit, the candidate capacity, the capacity basis
/// and the stable reason code.
fn insert_transition_diagnostic(
    diagnostics: &mut BTreeMap<String, ReviewDiagnosticRow>,
    loop_key: &str,
    verdict: &RouteTransitionVerdict,
) -> Result<(), ReviewError> {
    let reason = verdict
        .reason
        .clone()
        .unwrap_or_else(|| "unknown".to_owned());
    insert_diagnostic(
        diagnostics,
        loop_key,
        ReviewDiagnosticInput {
            diagnostic_id: Some(format!("state-transition-gate:{loop_key}")),
            diagnostic_type: ReviewDiagnosticType::Other,
            diagnostic_code: Some("StateTransitionGateFailed".to_owned()),
            message: Some(format!(
                "the route failed the three-state execution gate: {reason}"
            )),
            hop_index: None,
            pool_id: None,
            selector: None,
            payload: serde_json::json!({
                "reason": reason,
                "broadcastAllowed": verdict.broadcast_allowed,
                "hops": transition_hop_payload(verdict),
            }),
            observed_at: None,
        },
    )
}

/// Records a hop whose stored direction contradicts the route.
///
/// The diagnostic names the hop and the pool, so the drift is locatable
/// from the review library alone: the stored direction, the direction the
/// route describes and the hop index travel with it.  The hop is skipped
/// by the gates, so the route cannot be proven executable over it.
fn insert_direction_mismatch_diagnostic(
    diagnostics: &mut BTreeMap<String, ReviewDiagnosticRow>,
    loop_key: &str,
    mismatch: &DirectionMismatch,
) -> Result<(), ReviewError> {
    insert_diagnostic(
        diagnostics,
        loop_key,
        ReviewDiagnosticInput {
            diagnostic_id: Some(format!(
                "direction-mismatch:{loop_key}:{}",
                mismatch.hop_index
            )),
            // The diagnostic **table** fixes the set of diagnostic types, so
            // the precise marker travels in the code, exactly like the two
            // execution gates do.
            diagnostic_type: ReviewDiagnosticType::Other,
            diagnostic_code: Some("DirectionMismatch".to_owned()),
            message: Some(format!(
                "the stored direction of hop {} contradicts the route: the catalog \
                 stores direction {} and the route trades direction {}",
                mismatch.hop_index,
                mismatch.stored_direction,
                mismatch.derived_direction
            )),
            hop_index: Some(mismatch.hop_index),
            pool_id: Some(mismatch.pool_id.clone()),
            selector: None,
            payload: serde_json::json!({
                "hopIndex": mismatch.hop_index,
                "poolId": mismatch.pool_id,
                "storedDirection": mismatch.stored_direction,
                "derivedDirection": mismatch.derived_direction,
            }),
            observed_at: None,
        },
    )
}

/// Records the hop that traded one of its candidates instead of its main
/// pool.
///
/// The closed loop publishes one main pool per hop and keeps the rest as
/// candidates.  When the main pool no longer carries the trade, a
/// candidate does, and the review says so: the operator reads which pool
/// the arbitrage actually ran on, and the candidate substitution is never
/// invisible.
fn insert_candidate_substitution_diagnostic(
    diagnostics: &mut BTreeMap<String, ReviewDiagnosticRow>,
    loop_key: &str,
    hop_index: usize,
    priced_pool_id: &str,
    main_pool_id: &str,
) -> Result<(), ReviewError> {
    insert_diagnostic(
        diagnostics,
        loop_key,
        ReviewDiagnosticInput {
            diagnostic_id: Some(format!("candidate-substitution:{loop_key}:{hop_index}")),
            diagnostic_type: ReviewDiagnosticType::Other,
            diagnostic_code: Some("CandidateSubstitution".to_owned()),
            message: Some(format!(
                "hop {} traded the candidate pool {priced_pool_id} instead of its main pool \
                 {main_pool_id}",
                hop_index + 1
            )),
            hop_index: Some(hop_index),
            pool_id: Some(priced_pool_id.to_owned()),
            selector: None,
            payload: serde_json::json!({
                "hopIndex": hop_index,
                "pricedPoolId": priced_pool_id,
                "mainPoolId": main_pool_id,
            }),
            observed_at: None,
        },
    )
}

/// Serialises the per-hop verdicts of the transition gate.
///
/// The raw amounts are 128-bit values, which JSON numbers cannot carry
/// without losing precision, so they are written as decimal **strings**.
fn transition_hop_payload(verdict: &RouteTransitionVerdict) -> Vec<serde_json::Value> {
    verdict
        .hops
        .iter()
        .map(|hop| {
            serde_json::json!({
                "poolId": hop.pool_id,
                "amountInRaw": hop.amount_in_raw.to_string(),
                "outcome": hop.outcome.as_str(),
                "reason": hop.reason,
                "broadcastAllowed": hop.broadcast_allowed,
                "sqrtPriceLimitX96": hop.sqrt_price_limit_x96,
                "boundarySqrtPriceX96": hop.boundary_sqrt_price_x96,
                "expectedAmountOutRaw": hop.expected_amount_out_raw,
                "currentAmountOutRaw": hop.current_amount_out_raw,
                "expectedCapacityRaw": hop.expected_capacity_raw,
                "currentCapacityRaw": hop.current_capacity_raw,
                "candidateCapacityRaw": hop.candidate_capacity_raw,
                "capacityBasis": hop.capacity_basis,
                "capacityRatioNumerator": hop.capacity_ratio_numerator,
                "capacityRatioDenominator": hop.capacity_ratio_denominator,
                "capacityMultiplierBps": hop.capacity_multiplier_bps,
                "expectedSqrtPriceX96": hop.expected_sqrt_price_x96,
                "currentSqrtPriceX96": hop.current_sqrt_price_x96,
            })
        })
        .collect()
}

/// Serialises the per-hop verdicts of the gate.
///
/// The raw amounts are 128-bit values, which JSON numbers cannot carry
/// without losing precision, so they are written as decimal **strings**.
/// The tick distances and the conclusions are ordinary numbers.
fn single_tick_hop_payload(verdict: &SingleTickRouteVerdict) -> Vec<serde_json::Value> {
    verdict
        .hops
        .iter()
        .map(|hop| {
            serde_json::json!({
                "poolId": hop.pool_id,
                "initializedTick": hop.initialized_tick,
                "nextTick": hop.next_tick,
                "nextTickDistance": hop.next_tick_distance,
                "amountInRaw": hop.amount_in_raw.to_string(),
                "amountOutRaw": hop.amount_out_raw.map(|value| value.to_string()),
                "maxAmountInRaw": hop.max_amount_in_raw.map(|value| value.to_string()),
                "headroomRaw": hop.headroom_raw.map(|value| value.to_string()),
                "boundConclusion": hop.bound_conclusion.as_str(),
                "ok": hop.ok,
                "reason": hop.reason,
            })
        })
        .collect()
}

fn insert_synthetic_missing_diagnostic(
    diagnostics: &mut BTreeMap<String, ReviewDiagnosticRow>,
    loop_key: &str,
    message: &str,
) -> Result<(), ReviewError> {
    insert_diagnostic(
        diagnostics,
        loop_key,
        ReviewDiagnosticInput {
            diagnostic_id: Some(format!("evidence-missing:{loop_key}")),
            diagnostic_type: ReviewDiagnosticType::EvidenceMissing,
            diagnostic_code: Some("EvidenceMissing".to_owned()),
            message: Some(message.to_owned()),
            hop_index: None,
            pool_id: None,
            selector: None,
            payload: empty_object(),
            observed_at: None,
        },
    )
}

fn normalize_object(value: &serde_json::Value) -> serde_json::Value {
    if value.is_null() {
        empty_object()
    } else {
        value.clone()
    }
}

fn empty_object() -> serde_json::Value {
    serde_json::Value::Object(serde_json::Map::new())
}

fn option_string(value: Option<String>) -> serde_json::Value {
    value.map_or(serde_json::Value::Null, serde_json::Value::String)
}

/// Read the `runArbitrage` `ok` flag back out of the stored evidence.
///
/// The flag is **optional** on purpose. A stored payload that carries no
/// `ok` field was never proven to have succeeded, so it reads back as
/// absent rather than as `false`. The broadcast layer treats an absent
/// flag as "not proven" — never as a pass.
fn run_ok_of(run_arbitrage: &serde_json::Value) -> Option<bool> {
    run_arbitrage
        .get("ok")
        .and_then(serde_json::Value::as_bool)
}

/// Read the `runArbitrage` `executable` flag back out of the stored
/// evidence.
///
/// See [`run_ok_of`] for why this is optional rather than a plain
/// `bool`.
fn run_executable_of(run_arbitrage: &serde_json::Value) -> Option<bool> {
    run_arbitrage
        .get("executable")
        .and_then(serde_json::Value::as_bool)
}

fn validate_signed_pnl(value: &SignedPnl) -> bool {
    value.amount.is_finite()
        && !value.raw.trim().is_empty()
        && !value.currency.trim().is_empty()
        && !value.unit.trim().is_empty()
        && !value.source.trim().is_empty()
}

fn validate_expected_upstream(
    expected: &ExpectedUpstream,
    actual: &ClosedLoopStatus,
) -> Result<(), ReviewError> {
    compare_optional_version(
        expected.closed_loop_version,
        actual.closed_loop_version,
        "closed_loop_version",
    )?;
    compare_optional_string(
        expected.closed_loop_checksum.as_deref(),
        actual.materialized_checksum.as_deref(),
        "closed_loop_checksum",
    )?;
    compare_optional_version(
        expected.candidate_edge_version,
        actual.candidate_edge_version.unwrap_or_default(),
        "candidate_edge_version",
    )?;
    compare_optional_string(
        expected.candidate_edge_checksum.as_deref(),
        actual.candidate_edge_checksum.as_deref(),
        "candidate_edge_checksum",
    )?;
    compare_optional_version(
        expected.observation_version,
        actual.observation_version.unwrap_or_default(),
        "observation_version",
    )?;
    compare_optional_string(
        expected.observation_checksum.as_deref(),
        actual.observation_checksum.as_deref(),
        "observation_checksum",
    )?;
    compare_optional_string(
        expected.pool_scope_checksum.as_deref(),
        actual.pool_scope_checksum.as_deref(),
        "pool_scope_checksum",
    )?;
    for (key, expected_version) in &expected.library_versions {
        let actual_version = actual
            .library_versions
            .get(key)
            .copied()
            .unwrap_or_default();
        if *expected_version != actual_version {
            return Err(ReviewError::ExpectedUpstreamMismatch {
                field: format!("library_versions.{key}"),
                expected: expected_version.to_string(),
                actual: actual_version.to_string(),
            });
        }
    }
    for (key, expected_checksum) in &expected.library_checksums {
        let actual_checksum = actual
            .library_checksums
            .get(key)
            .cloned()
            .unwrap_or_default();
        if expected_checksum != &actual_checksum {
            return Err(ReviewError::ExpectedUpstreamMismatch {
                field: format!("library_checksums.{key}"),
                expected: expected_checksum.clone(),
                actual: actual_checksum,
            });
        }
    }
    Ok(())
}

fn compare_optional_version(
    expected: Option<i64>,
    actual: i64,
    field: &str,
) -> Result<(), ReviewError> {
    if let Some(expected) = expected {
        if expected != actual {
            return Err(ReviewError::ExpectedUpstreamMismatch {
                field: field.to_owned(),
                expected: expected.to_string(),
                actual: actual.to_string(),
            });
        }
    }
    Ok(())
}

fn compare_optional_string(
    expected: Option<&str>,
    actual: Option<&str>,
    field: &str,
) -> Result<(), ReviewError> {
    if let Some(expected) = expected {
        let actual = actual.unwrap_or_default();
        if expected != actual {
            return Err(ReviewError::ExpectedUpstreamMismatch {
                field: field.to_owned(),
                expected: expected.to_owned(),
                actual: actual.to_owned(),
            });
        }
    }
    Ok(())
}

fn closed_loop_checksum(status: &ClosedLoopStatus) -> Result<&str, ReviewError> {
    status
        .materialized_checksum
        .as_deref()
        .ok_or(ReviewError::MissingClosedLoopState)
}

fn closed_loop_required_version(value: Option<i64>, field: &str) -> Result<i64, ReviewError> {
    value.ok_or_else(|| ReviewError::InvalidRequest(format!("{field} is missing")))
}

fn closed_loop_required_string(value: Option<&str>, field: &str) -> Result<String, ReviewError> {
    value
        .map(str::to_owned)
        .ok_or_else(|| ReviewError::InvalidRequest(format!("{field} is missing")))
}

/// The checksum of the reviews the library holds.
///
/// The checksum covers the **whole** set of reviews, not the set one pass
/// carried: the reviews accumulate, so a checksum over the accumulated set
/// is the only one that names the state the library is in.  The upstream
/// fields are part of it, so a closed-loop generation that moved on is told
/// apart from a review that changed.
fn review_state_checksum(
    input: &ReviewInput,
    accumulated: &AccumulatedReviewState,
) -> Result<String, ReviewError> {
    let payload = ReviewStateChecksumPayload {
        closed_loop_version: input.closed_loop.closed_loop_version,
        closed_loop_checksum: closed_loop_checksum(&input.closed_loop)?,
        candidate_edge_version: closed_loop_required_version(
            input.closed_loop.candidate_edge_version,
            "candidate_edge_version",
        )?,
        candidate_edge_checksum: input
            .closed_loop
            .candidate_edge_checksum
            .as_deref()
            .ok_or_else(|| {
                ReviewError::InvalidRequest("candidate_edge_checksum is missing".to_owned())
            })?,
        observation_version: closed_loop_required_version(
            input.closed_loop.observation_version,
            "observation_version",
        )?,
        observation_checksum: input
            .closed_loop
            .observation_checksum
            .as_deref()
            .ok_or_else(|| {
                ReviewError::InvalidRequest("observation_checksum is missing".to_owned())
            })?,
        pool_scope_checksum: input
            .closed_loop
            .pool_scope_checksum
            .as_deref()
            .ok_or_else(|| {
                ReviewError::InvalidRequest("pool_scope_checksum is missing".to_owned())
            })?,
        library_versions: &input.closed_loop.library_versions,
        library_checksums: &input.closed_loop.library_checksums,
        reviews: &accumulated.projections,
    };
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn open_connection(path: &Path, writable: bool) -> Result<Connection, ReviewError> {
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
) -> Result<ReviewStatus, ReviewError> {
    let stored = connection
        .query_row(
            "SELECT review_version, materialized_checksum,
                    closed_loop_version, closed_loop_checksum,
                    candidate_edge_version, candidate_edge_checksum,
                    observation_version, observation_checksum,
                    pool_scope_checksum, library_versions_json,
                    library_checksums_json, loop_count, reviewed_count,
                    completed_count, data_incomplete_count,
                    not_executable_count, failed_count, broadcast_request_count
             FROM manager_review_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4",
            params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, String>(9)?,
                    row.get::<_, String>(10)?,
                    row.get::<_, i64>(11)?,
                    row.get::<_, i64>(12)?,
                    row.get::<_, i64>(13)?,
                    row.get::<_, i64>(14)?,
                    row.get::<_, i64>(15)?,
                    row.get::<_, i64>(16)?,
                    row.get::<_, i64>(17)?,
                ))
            },
        )
        .optional()?;
    let Some((
        review_version,
        materialized_checksum,
        closed_loop_version,
        closed_loop_checksum,
        candidate_edge_version,
        candidate_edge_checksum,
        observation_version,
        observation_checksum,
        pool_scope_checksum,
        library_versions_json,
        library_checksums_json,
        loop_count,
        reviewed_count,
        completed_count,
        data_incomplete_count,
        not_executable_count,
        failed_count,
        broadcast_request_count,
    )) = stored
    else {
        return Ok(disabled_review_status(network, chain_id));
    };
    Ok(ReviewStatus {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: REVIEW_SCOPE_KEY.to_owned(),
        review_version,
        materialized_checksum: Some(materialized_checksum),
        closed_loop_version: Some(closed_loop_version),
        closed_loop_checksum: Some(closed_loop_checksum),
        candidate_edge_version: Some(candidate_edge_version),
        candidate_edge_checksum: Some(candidate_edge_checksum),
        observation_version: Some(observation_version),
        observation_checksum: Some(observation_checksum),
        pool_scope_checksum: Some(pool_scope_checksum),
        library_versions: serde_json::from_str(&library_versions_json)?,
        library_checksums: serde_json::from_str(&library_checksums_json)?,
        loop_count: usize_from_i64(loop_count),
        reviewed_count: usize_from_i64(reviewed_count),
        completed_count: usize_from_i64(completed_count),
        data_incomplete_count: usize_from_i64(data_incomplete_count),
        not_executable_count: usize_from_i64(not_executable_count),
        failed_count: usize_from_i64(failed_count),
        broadcast_request_count: usize_from_i64(broadcast_request_count),
    })
}

pub fn read_review_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<ReviewStatus, ReviewError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_status_on_connection(&connection, network, chain_id)
}

pub fn read_review_status_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ReviewStatus, ReviewError> {
    read_status_on_connection(connection, network, chain_id)
}

pub fn read_review_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    loop_key: Option<&str>,
    limit: usize,
) -> Result<ReviewSnapshot, ReviewError> {
    if chain_id <= 0 {
        return Err(ReviewError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if limit == 0 || limit > MAX_SNAPSHOT_LIMIT {
        return Err(ReviewError::InvalidRequest(format!(
            "limit must be between 1 and {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    if loop_key.is_some_and(str::is_empty) {
        return Err(ReviewError::InvalidRequest(
            "loop_key must not be empty".to_owned(),
        ));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let status = read_status_on_connection(&connection, network, chain_id)?;
    Ok(ReviewSnapshot {
        status,
        reviews: read_reviews(&connection, network, chain_id, loop_key, limit)?,
        diagnostics: read_diagnostics(&connection, network, chain_id, loop_key, limit)?,
        broadcast_requests: read_broadcast_requests(
            &connection,
            network,
            chain_id,
            loop_key,
            limit,
        )?,
    })
}

pub fn read_review_page(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    query: ReviewReadQuery<'_>,
) -> Result<ReviewPage, ReviewError> {
    if chain_id <= 0 {
        return Err(ReviewError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if query.limit == 0 || query.limit > MAX_SNAPSHOT_LIMIT {
        return Err(ReviewError::InvalidRequest(format!(
            "limit must be between 1 and {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    if query.offset.saturating_add(query.limit) > MAX_SNAPSHOT_LIMIT {
        return Err(ReviewError::InvalidRequest(format!(
            "offset plus limit must not exceed {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    if query.loop_key.is_some_and(str::is_empty) {
        return Err(ReviewError::InvalidRequest(
            "loop_key must not be empty".to_owned(),
        ));
    }
    if let Some(category) = query.category {
        if !matches!(
            category,
            "ready_not_submitted"
                | "confirmed_no_profit"
                | "data_incomplete"
                | "review_failed"
                | "other"
        ) {
            return Err(ReviewError::InvalidRequest(format!(
                "unknown review category {category}"
            )));
        }
    }
    if let Some(status) = query.review_status {
        if !matches!(
            status,
            "completed" | "data_incomplete" | "not_executable" | "failed"
        ) {
            return Err(ReviewError::InvalidRequest(format!(
                "unknown review status {status}"
            )));
        }
    }

    let connection = open_connection(database_path.as_ref(), false)?;
    let status = read_status_on_connection(&connection, network, chain_id)?;
    let all_reviews = read_reviews(
        &connection,
        network,
        chain_id,
        query.loop_key,
        MAX_SNAPSHOT_LIMIT,
    )?;
    let category_counts = review_category_counts(&all_reviews);
    let mut reviews = all_reviews
        .into_iter()
        .filter(|row| {
            query
                .category
                .is_none_or(|category| row.page_category == category)
                && query
                    .review_status
                    .is_none_or(|review_status| row.review_status == review_status)
                && query.outcome.is_none_or(|outcome| match outcome {
                    ReviewOutcomeFilter::Success => row.review_status == "completed",
                    ReviewOutcomeFilter::Failed => row.review_status != "completed",
                })
        })
        .collect::<Vec<_>>();
    let filtered_count = reviews.len();
    reviews.sort_by(|left, right| {
        right
            .reviewed_at
            .cmp(&left.reviewed_at)
            .then_with(|| left.loop_key.cmp(&right.loop_key))
    });
    let reviews = reviews
        .into_iter()
        .skip(query.offset)
        .take(query.limit)
        .collect::<Vec<_>>();
    let selected_loops = reviews
        .iter()
        .map(|row| row.loop_key.as_str())
        .collect::<BTreeSet<_>>();
    let mut diagnostics = read_diagnostics(
        &connection,
        network,
        chain_id,
        query.loop_key,
        MAX_SNAPSHOT_LIMIT,
    )?;
    diagnostics.retain(|row| selected_loops.contains(row.loop_key.as_str()));
    let mut broadcast_requests = read_broadcast_requests(
        &connection,
        network,
        chain_id,
        query.loop_key,
        MAX_SNAPSHOT_LIMIT,
    )?;
    broadcast_requests.retain(|row| selected_loops.contains(row.loop_key.as_str()));
    Ok(ReviewPage {
        status,
        category_counts,
        filtered_count,
        offset: query.offset,
        limit: query.limit,
        reviews,
        diagnostics,
        broadcast_requests,
    })
}

fn review_category_counts(rows: &[ReviewRow]) -> ReviewCategoryCounts {
    let mut counts = ReviewCategoryCounts {
        total: rows.len(),
        completed: 0,
        data_incomplete: 0,
        not_executable: 0,
        failed: 0,
        ready_not_submitted: 0,
        confirmed_no_profit: 0,
        review_failed: 0,
        other: 0,
    };
    for row in rows {
        match row.review_status.as_str() {
            "completed" => counts.completed += 1,
            "data_incomplete" => counts.data_incomplete += 1,
            "not_executable" => counts.not_executable += 1,
            "failed" => counts.failed += 1,
            _ => {}
        }
        match row.page_category.as_str() {
            "ready_not_submitted" => counts.ready_not_submitted += 1,
            "confirmed_no_profit" => counts.confirmed_no_profit += 1,
            "data_incomplete" => {}
            "review_failed" => counts.review_failed += 1,
            _ => counts.other += 1,
        }
    }
    counts
}

pub fn read_broadcast_requests_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: Option<&str>,
    limit: usize,
) -> Result<Vec<BroadcastRequestRow>, ReviewError> {
    if limit == 0 {
        return Err(ReviewError::InvalidRequest(
            "limit must be greater than zero".to_owned(),
        ));
    }
    read_broadcast_requests(connection, network, chain_id, loop_key, limit)
}

/// Read the broadcast requests the store holds for one chain.
///
/// The function opens the store itself, so a caller that only wants the
/// requests does not have to own a connection.  The store is opened
/// read-only: reading the requests never writes anything.
///
/// The runtime needs the requests because the **review** is the layer that
/// proves a route executable, and the proof carries the three per-hop
/// price limits the executor needs.  The request row is the only place
/// those limits are written, so the broadcast task is built from what is
/// read here and never from a second derivation.
pub fn read_broadcast_requests_for_chain(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    limit: usize,
) -> Result<Vec<BroadcastRequestRow>, ReviewError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_broadcast_requests_on_connection(&connection, network, chain_id, None, limit)
}

/// Read the closed-loop identity columns of a review row.
///
/// The identity is **optional**. A review row whose identity columns are
/// all NULL was measured against a catalog that published no loop, or it
/// was written before the identity was published at all. Both cases read
/// back as an absent identity, never as a "matched" verdict.
fn read_closed_loop_identity(
    closed_loop_identity_status: Option<String>,
    closed_loop_pool_identity_status: Option<String>,
    identity_mismatch: Option<String>,
    closed_loop_repair_target: Option<String>,
) -> Option<ClosedLoopIdentityEvidence> {
    let closed_loop_identity_status = closed_loop_identity_status
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let closed_loop_pool_identity_status = closed_loop_pool_identity_status
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let identity_mismatch = identity_mismatch
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let closed_loop_repair_target = closed_loop_repair_target
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    if closed_loop_identity_status.is_none()
        && closed_loop_pool_identity_status.is_none()
        && identity_mismatch.is_none()
        && closed_loop_repair_target.is_none()
    {
        return None;
    }
    Some(ClosedLoopIdentityEvidence {
        closed_loop_identity_status,
        closed_loop_pool_identity_status,
        identity_mismatch,
        closed_loop_repair_target,
    })
}

fn read_reviews(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: Option<&str>,
    limit: usize,
) -> Result<Vec<ReviewRow>, ReviewError> {
    let mut statement = connection.prepare(
        "SELECT loop_key, closed_loop_version, closed_loop_checksum,
                candidate_edge_version, candidate_edge_checksum,
                observation_version, observation_checksum, pool_scope_checksum,
                anchor_token_key_id, anchor_address, anchor_symbol, path_text,
                path_addresses_json, path_symbols_json, pool_path_key,
                pool_ids_json, review_status, review_reason, static_preview_json,
                run_arbitrage_json, signed_pnl_raw, signed_pnl_amount,
                signed_pnl_currency, signed_pnl_address, signed_pnl_unit,
                signed_pnl_source, reviewed_at,
                single_tick_capacity_anchor_raw,
                single_tick_capacity_binding_pool_id,
                hop_sqrt_price_limits_json,
                closed_loop_identity_status,
                closed_loop_pool_identity_status,
                identity_mismatch,
                closed_loop_repair_target
         FROM manager_v4_reviews
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR loop_key = ?5)
         ORDER BY loop_key
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            REVIEW_SCOPE_KEY,
            loop_key,
            limit as i64
        ],
        |row| {
            let signed_pnl_raw: Option<String> = row.get(20)?;
            let signed_pnl = signed_pnl_raw.map(|raw| {
                Ok::<_, rusqlite::Error>(SignedPnl {
                    raw,
                    amount: row.get::<_, Option<f64>>(21)?.unwrap_or_default(),
                    currency: row.get::<_, Option<String>>(22)?.unwrap_or_default(),
                    address: row.get(23)?,
                    unit: row.get::<_, Option<String>>(24)?.unwrap_or_default(),
                    source: row.get::<_, Option<String>>(25)?.unwrap_or_default(),
                })
            });
            let signed_pnl = signed_pnl.transpose()?;
            let review_status: String = row.get(16)?;
            let page_category =
                review_page_category(&review_status, signed_pnl.as_ref()).to_owned();
            Ok(ReviewRow {
                loop_key: row.get(0)?,
                closed_loop_version: row.get(1)?,
                closed_loop_checksum: row.get(2)?,
                candidate_edge_version: row.get(3)?,
                candidate_edge_checksum: row.get(4)?,
                observation_version: row.get(5)?,
                observation_checksum: row.get(6)?,
                pool_scope_checksum: row.get(7)?,
                anchor_token_key_id: row.get(8)?,
                anchor_address: row.get(9)?,
                anchor_symbol: row.get(10)?,
                path_text: row.get(11)?,
                path_addresses: parse_json_column(row.get(12)?)?,
                path_symbols: parse_json_column(row.get(13)?)?,
                pool_path_key: row.get(14)?,
                pool_ids: parse_json_column(row.get(15)?)?,
                review_status,
                page_category,
                review_reason: row.get(17)?,
                static_preview: parse_json_value(row.get(18)?)?,
                run_arbitrage: parse_json_value(row.get(19)?)?,
                signed_pnl,
                reviewed_at: row.get(26)?,
                single_tick_capacity_anchor_raw: row
                    .get::<_, Option<String>>(27)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                single_tick_capacity_binding_pool_id: row
                    .get::<_, Option<String>>(28)?
                    .map(|value| value.trim().to_owned())
                    .filter(|value| !value.is_empty()),
                hop_sqrt_price_limits: parse_optional_json_column(
                    row.get::<_, Option<String>>(29)?,
                )?,
                closed_loop_identity: read_closed_loop_identity(
                    row.get::<_, Option<String>>(30)?,
                    row.get::<_, Option<String>>(31)?,
                    row.get::<_, Option<String>>(32)?,
                    row.get::<_, Option<String>>(33)?,
                ),
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_diagnostics(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: Option<&str>,
    limit: usize,
) -> Result<Vec<ReviewDiagnosticRow>, ReviewError> {
    let mut statement = connection.prepare(
        "SELECT loop_key, diagnostic_id, diagnostic_type, diagnostic_code,
                message, hop_index, pool_id, selector, payload_json, observed_at
         FROM manager_v4_review_diagnostics
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR loop_key = ?5)
         ORDER BY loop_key, diagnostic_id
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            REVIEW_SCOPE_KEY,
            loop_key,
            limit as i64
        ],
        |row| {
            Ok(ReviewDiagnosticRow {
                loop_key: row.get(0)?,
                diagnostic_id: row.get(1)?,
                diagnostic_type: row.get(2)?,
                diagnostic_code: row.get(3)?,
                message: row.get(4)?,
                hop_index: row.get::<_, Option<i64>>(5)?.map(usize_from_i64),
                pool_id: row.get(6)?,
                selector: row.get(7)?,
                payload: parse_json_value(row.get(8)?)?,
                observed_at: row.get(9)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_broadcast_requests(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: Option<&str>,
    limit: usize,
) -> Result<Vec<BroadcastRequestRow>, ReviewError> {
    // The admission facts live on the review row, not on the request row:
    // the review is the layer that measured `runArbitrage`. The join is a
    // LEFT JOIN on purpose — a request whose review row is gone reads
    // back with absent facts, and the broadcast layer then refuses it
    // instead of admitting it on incomplete evidence.
    let mut statement = connection.prepare(
        "SELECT r.loop_key, r.closed_loop_version, r.closed_loop_checksum,
                r.anchor_token_key_id, r.path_text, r.path_addresses_json,
                r.path_symbols_json, r.pool_path_key, r.pool_ids_json, r.signed_pnl_raw,
                r.signed_pnl_amount, r.signed_pnl_currency, r.signed_pnl_address,
                r.signed_pnl_unit, r.signed_pnl_source, r.request_status, r.request_reason,
                r.single_tick_capacity_anchor_raw,
                r.single_tick_capacity_binding_pool_id,
                r.hop_sqrt_price_limits_json,
                v.review_status, v.reviewed_at, v.run_arbitrage_json
         FROM manager_v4_review_broadcast_requests AS r
         LEFT JOIN manager_v4_reviews AS v
           ON v.network = r.network
          AND v.chain_id = r.chain_id
          AND v.protocol = r.protocol
          AND v.scope_key = r.scope_key
          AND v.loop_key = r.loop_key
         WHERE r.network = ?1 AND r.chain_id = ?2 AND r.protocol = ?3 AND r.scope_key = ?4
           AND (?5 IS NULL OR r.loop_key = ?5)
         ORDER BY r.loop_key, r.created_at
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            REVIEW_SCOPE_KEY,
            loop_key,
            limit as i64
        ],
        |row| {
            Ok(BroadcastRequestRow {
                loop_key: row.get(0)?,
                closed_loop_version: row.get(1)?,
                closed_loop_checksum: row.get(2)?,
                anchor_token_key_id: row.get(3)?,
                path_text: row.get(4)?,
                path_addresses: parse_json_column(row.get(5)?)?,
                path_symbols: parse_json_column(row.get(6)?)?,
                pool_path_key: row.get(7)?,
                pool_ids: parse_json_column(row.get(8)?)?,
                signed_pnl: SignedPnl {
                    raw: row.get(9)?,
                    amount: row.get(10)?,
                    currency: row.get(11)?,
                    address: row.get(12)?,
                    unit: row.get(13)?,
                    source: row.get(14)?,
                },
                request_status: row.get(15)?,
                request_reason: row.get(16)?,
                single_tick_capacity_anchor_raw: row
                    .get::<_, Option<String>>(17)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                single_tick_capacity_binding_pool_id: row
                    .get::<_, Option<String>>(18)?
                    .map(|value| value.trim().to_owned())
                    .filter(|value| !value.is_empty()),
                hop_sqrt_price_limits: parse_optional_json_column(
                    row.get::<_, Option<String>>(19)?,
                )?,
                admission: {
                    // The joined evidence is parsed once and read twice.
                    // A review row that is absent, or whose stored payload
                    // is not an object, yields absent facts — never a
                    // fabricated `false`.
                    let run_arbitrage: serde_json::Value =
                        parse_optional_json_column(row.get::<_, Option<String>>(22)?)?;
                    BroadcastAdmissionFacts {
                        review_status: row
                            .get::<_, Option<String>>(20)?
                            .map(|value| value.trim().to_owned())
                            .unwrap_or_default(),
                        run_ok: run_ok_of(&run_arbitrage),
                        run_executable: run_executable_of(&run_arbitrage),
                        reviewed_at: row.get::<_, Option<String>>(21)?.unwrap_or_default(),
                    }
                },
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// Write the reviews of the loops this pass carried.
///
/// The write is **per closed loop**: only the loops the evidence names are
/// replaced, and every other loop keeps the review it already has.  A
/// review is therefore never decided by another loop's measurement - not by
/// being blanked, and not by the write being held back until the whole
/// catalog was measured.
///
/// The rows of the loops that were written accumulate over time: a loop
/// that is measured again carries its newest measurement, and the history
/// of every measurement stays in the review record stream.
fn upsert_review_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredReviewData,
    result: &ReviewMaterializationResult,
) -> Result<(), ReviewError> {
    let now = chrono::Utc::now().to_rfc3339();
    let loop_keys = desired
        .reviews
        .iter()
        .map(|row| row.loop_key.clone())
        .collect::<Vec<_>>();
    for table in [
        "manager_v4_review_diagnostics",
        "manager_v4_review_broadcast_requests",
        "manager_v4_reviews",
    ] {
        for loop_key in &loop_keys {
            connection.execute(
                &format!(
                    "DELETE FROM {table}
                     WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                       AND scope_key = ?4 AND loop_key = ?5"
                ),
                params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY, loop_key],
            )?;
        }
    }
    insert_state(connection, network, chain_id, result, &now)?;
    for row in &desired.reviews {
        let (
            signed_raw,
            signed_amount,
            signed_currency,
            signed_address,
            signed_unit,
            signed_source,
        ) = row
            .signed_pnl
            .as_ref()
            .map_or((None, None, None, None, None, None), |pnl| {
                (
                    Some(pnl.raw.as_str()),
                    Some(pnl.amount),
                    Some(pnl.currency.as_str()),
                    pnl.address.as_deref(),
                    Some(pnl.unit.as_str()),
                    Some(pnl.source.as_str()),
                )
            });
        // The identity is optional. A review measured against a catalog
        // that published no loop carries none, and the columns stay NULL
        // instead of being filled with a default verdict.
        let identity = row.closed_loop_identity.clone().unwrap_or_default();
        connection.execute(
            "INSERT INTO manager_v4_reviews (
                 network, chain_id, protocol, scope_key, loop_key,
                 closed_loop_version, closed_loop_checksum,
                 candidate_edge_version, candidate_edge_checksum,
                 observation_version, observation_checksum, pool_scope_checksum,
                 anchor_token_key_id, anchor_address, anchor_symbol, path_text,
                 path_addresses_json, path_symbols_json, pool_path_key,
                 pool_ids_json, review_status, review_reason, static_preview_json,
                 run_arbitrage_json, signed_pnl_raw, signed_pnl_amount,
                 signed_pnl_currency, signed_pnl_address, signed_pnl_unit,
                 signed_pnl_source, reviewed_at,
                 single_tick_capacity_anchor_raw,
                 single_tick_capacity_binding_pool_id,
                 hop_sqrt_price_limits_json,
                 closed_loop_identity_status,
                 closed_loop_pool_identity_status,
                 identity_mismatch,
                 closed_loop_repair_target,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23,
                       ?24, ?25, ?26, ?27, ?28, ?29, ?30, ?31, ?32, ?33, ?34,
                       ?35, ?36, ?37, ?38, ?39, ?39)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                REVIEW_SCOPE_KEY,
                row.loop_key,
                row.closed_loop_version,
                row.closed_loop_checksum,
                row.candidate_edge_version,
                row.candidate_edge_checksum,
                row.observation_version,
                row.observation_checksum,
                row.pool_scope_checksum,
                row.anchor_token_key_id,
                row.anchor_address,
                row.anchor_symbol,
                row.path_text,
                serde_json::to_string(&row.path_addresses)?,
                serde_json::to_string(&row.path_symbols)?,
                row.pool_path_key,
                serde_json::to_string(&row.pool_ids)?,
                row.review_status,
                row.review_reason,
                serde_json::to_string(&row.static_preview)?,
                serde_json::to_string(&row.run_arbitrage)?,
                signed_raw,
                signed_amount,
                signed_currency,
                signed_address,
                signed_unit,
                signed_source,
                row.reviewed_at,
                row.single_tick_capacity_anchor_raw
                    .map(|value| value.to_string()),
                row.single_tick_capacity_binding_pool_id.clone(),
                serde_json::to_string(&row.hop_sqrt_price_limits)?,
                identity.closed_loop_identity_status.as_deref(),
                identity.closed_loop_pool_identity_status.as_deref(),
                identity.identity_mismatch.as_deref(),
                identity.closed_loop_repair_target.as_deref(),
                now
            ],
        )?;
    }
    for row in &desired.diagnostics {
        connection.execute(
            "INSERT INTO manager_v4_review_diagnostics (
                 network, chain_id, protocol, scope_key, loop_key,
                 diagnostic_id, diagnostic_type, diagnostic_code, message,
                 hop_index, pool_id, selector, payload_json, observed_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                REVIEW_SCOPE_KEY,
                row.loop_key,
                row.diagnostic_id,
                row.diagnostic_type,
                row.diagnostic_code,
                row.message,
                row.hop_index.map(|value| value as i64),
                row.pool_id,
                row.selector,
                serde_json::to_string(&row.payload)?,
                row.observed_at
            ],
        )?;
    }
    for row in &desired.broadcast_requests {
        connection.execute(
            "INSERT INTO manager_v4_review_broadcast_requests (
                 network, chain_id, protocol, scope_key, loop_key,
                 closed_loop_version, closed_loop_checksum, anchor_token_key_id,
                 path_text, path_addresses_json, path_symbols_json,
                 pool_path_key, pool_ids_json, signed_pnl_raw,
                 signed_pnl_amount, signed_pnl_currency, signed_pnl_address,
                 signed_pnl_unit, signed_pnl_source, request_status,
                 request_reason, single_tick_capacity_anchor_raw,
                 single_tick_capacity_binding_pool_id, hop_sqrt_price_limits_json,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23,
                       ?24, ?25, ?25)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                REVIEW_SCOPE_KEY,
                row.loop_key,
                row.closed_loop_version,
                row.closed_loop_checksum,
                row.anchor_token_key_id,
                row.path_text,
                serde_json::to_string(&row.path_addresses)?,
                serde_json::to_string(&row.path_symbols)?,
                row.pool_path_key,
                serde_json::to_string(&row.pool_ids)?,
                row.signed_pnl.raw,
                row.signed_pnl.amount,
                row.signed_pnl.currency,
                row.signed_pnl.address,
                row.signed_pnl.unit,
                row.signed_pnl.source,
                row.request_status,
                row.request_reason,
                row.single_tick_capacity_anchor_raw
                    .map(|value| value.to_string()),
                row.single_tick_capacity_binding_pool_id.clone(),
                serde_json::to_string(&row.hop_sqrt_price_limits)?,
                now
            ],
        )?;
    }
    Ok(())
}

/// Restart the review clock of every loop this pass measured.
///
/// The clock belongs to the **closed loop**, so the write goes through the
/// closed-loop Manager: the moment of the measurement and the moment it is
/// due again are stored beside the loop, and a rebuild of the catalog
/// carries them over instead of resetting them.
///
/// The round **overrides** the one record the loop has in the queue:
///
/// * a round that found a **profit** parks the loop at the broadcast
///   sentinel, because the loop has traded and waits for the receipt of
///   its transaction -- it has no scheduled review while it waits;
/// * a round that found **no profit** starts the clock again, so the
///   loop is due one interval later.
///
/// The park is not a dead end: the broadcast layer puts the loop back in
/// the queue when the attempt reaches its terminal state, and the runtime
/// puts it back when the broadcast never left.  Without that release a
/// profitable loop would wait for ever.
fn record_review_schedules(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredReviewData,
) -> Result<(), ReviewError> {
    for row in &desired.reviews {
        // The clock of a loop is restarted by the **moment** the review
        // names.  Whether the row carries such a moment is asked of the
        // moment itself: the scheduling helper is not called here, so no
        // interval is rebuilt to answer a question about a timestamp.
        if chrono::DateTime::parse_from_rfc3339(row.reviewed_at.trim()).is_err() {
            // A review that names no moment cannot restart a clock: the
            // row keeps the measurement it has, and the loop waits for a
            // review that names one.
            continue;
        }
        match review_round_outcome(row) {
            ReviewRoundOutcome::Profit => {
                // The round paid, so the loop trades.  It waits for the
                // receipt of its transaction, and a loop that waits has
                // no scheduled review: it is parked at the broadcast
                // sentinel until the attempt is terminal.
                closed_loop_manager::reset_review_start_on_connection(
                    connection,
                    network,
                    chain_id,
                    &row.loop_key,
                    closed_loop_manager::ReviewStartReset::BroadcastPending,
                    Some(&row.reviewed_at),
                    "measured_with_profit",
                )
                .map_err(|error| ReviewError::ClosedLoopRead(error.to_string()))?;
            }
            ReviewRoundOutcome::NoProfit => {
                // The round found no profit, so the loop waits for the
                // next round: the clock starts again.
                closed_loop_manager::record_review_schedule_on_connection(
                    connection,
                    network,
                    chain_id,
                    &row.loop_key,
                    &row.reviewed_at,
                    "measured_without_profit",
                )
                .map_err(|error| ReviewError::ClosedLoopRead(error.to_string()))?;
            }
        }
    }
    Ok(())
}

/// What a round of the review found.
enum ReviewRoundOutcome {
    /// The round found a profit that may be traded.
    Profit,
    /// The round found no profit: the loop has no room to trade, so it
    /// waits for the next round all the same.
    NoProfit,
}

/// The outcome of one round of the review.
fn review_round_outcome(row: &ReviewRow) -> ReviewRoundOutcome {
    let profitable = row.review_status == "completed"
        && row
            .signed_pnl
            .as_ref()
            .map(|pnl| pnl.amount > 0.0)
            .unwrap_or(false);
    if profitable {
        ReviewRoundOutcome::Profit
    } else {
        ReviewRoundOutcome::NoProfit
    }
}

fn insert_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    result: &ReviewMaterializationResult,
    now: &str,
) -> Result<(), ReviewError> {
    connection.execute(
        "INSERT INTO manager_review_library_state (
             network, chain_id, protocol, scope_key, review_version,
             materialized_checksum, closed_loop_version, closed_loop_checksum,
             candidate_edge_version, candidate_edge_checksum,
             observation_version, observation_checksum, pool_scope_checksum,
             library_versions_json, library_checksums_json, loop_count,
             reviewed_count, completed_count, data_incomplete_count,
             not_executable_count, failed_count, broadcast_request_count,
             generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?23)
         ON CONFLICT(network, chain_id, protocol, scope_key) DO UPDATE SET
             review_version = excluded.review_version,
             materialized_checksum = excluded.materialized_checksum,
             closed_loop_version = excluded.closed_loop_version,
             closed_loop_checksum = excluded.closed_loop_checksum,
             candidate_edge_version = excluded.candidate_edge_version,
             candidate_edge_checksum = excluded.candidate_edge_checksum,
             observation_version = excluded.observation_version,
             observation_checksum = excluded.observation_checksum,
             pool_scope_checksum = excluded.pool_scope_checksum,
             library_versions_json = excluded.library_versions_json,
             library_checksums_json = excluded.library_checksums_json,
             loop_count = excluded.loop_count,
             reviewed_count = excluded.reviewed_count,
             completed_count = excluded.completed_count,
             data_incomplete_count = excluded.data_incomplete_count,
             not_executable_count = excluded.not_executable_count,
             failed_count = excluded.failed_count,
             broadcast_request_count = excluded.broadcast_request_count,
             generated_at = excluded.generated_at,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            REVIEW_SCOPE_KEY,
            result.review_version,
            result.materialized_checksum,
            result.closed_loop_version,
            result.closed_loop_checksum,
            result.candidate_edge_version,
            result.candidate_edge_checksum,
            result.observation_version,
            result.observation_checksum,
            result.pool_scope_checksum,
            serde_json::to_string(&result.library_versions)?,
            serde_json::to_string(&result.library_checksums)?,
            result.loop_count as i64,
            result.reviewed_count as i64,
            result.completed_count as i64,
            result.data_incomplete_count as i64,
            result.not_executable_count as i64,
            result.failed_count as i64,
            result.broadcast_request_count as i64,
            now
        ],
    )?;
    Ok(())
}

fn find_write_request(
    connection: &Connection,
    request: &ReviewWriteRequest,
) -> Result<Option<String>, ReviewError> {
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
    request: &ReviewWriteRequest,
    request_digest: &str,
    result: &ReviewMaterializationResult,
) -> Result<(), ReviewError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream,
        "evidence": request.evidence
    });
    let response_json = serde_json::to_string(&PersistedReviewWrite {
        request_id: request.request_id.clone(),
        request_digest: request_digest.to_owned(),
        result: result.clone(),
    })?;
    if sqlite_table_exists(connection, "manager_write_requests")? {
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
    }
    Ok(())
}

fn disabled_review_status(network: &str, chain_id: i64) -> ReviewStatus {
    ReviewStatus {
        enabled: false,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: REVIEW_SCOPE_KEY.to_owned(),
        review_version: 0,
        materialized_checksum: None,
        closed_loop_version: None,
        closed_loop_checksum: None,
        candidate_edge_version: None,
        candidate_edge_checksum: None,
        observation_version: None,
        observation_checksum: None,
        pool_scope_checksum: None,
        library_versions: BTreeMap::new(),
        library_checksums: BTreeMap::new(),
        loop_count: 0,
        reviewed_count: 0,
        completed_count: 0,
        data_incomplete_count: 0,
        not_executable_count: 0,
        failed_count: 0,
        broadcast_request_count: 0,
    }
}

fn parse_json_column<T>(value: String) -> Result<T, rusqlite::Error>
where
    T: serde::de::DeserializeOwned,
{
    serde_json::from_str(&value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            value.len(),
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })
}

fn parse_optional_json_column<T>(value: Option<String>) -> Result<T, rusqlite::Error>
where
    T: serde::de::DeserializeOwned + Default,
{
    match value {
        Some(value) => parse_json_column(value),
        None => Ok(T::default()),
    }
}

fn parse_json_value(value: String) -> Result<serde_json::Value, rusqlite::Error> {
    serde_json::from_str(&value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            value.len(),
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })
}

fn sqlite_table_exists(connection: &Connection, table: &str) -> Result<bool, ReviewError> {
    let exists = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
            params![table],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0);
    Ok(exists > 0)
}

fn count_scoped_rows(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
) -> Result<usize, ReviewError> {
    let count: i64 = connection.query_row(
        &format!(
            "SELECT COUNT(*) FROM {table}
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        ),
        params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY],
        |row| row.get(0),
    )?;
    Ok(usize_from_i64(count))
}

fn count_legacy_review_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<usize, ReviewError> {
    if !sqlite_table_exists(connection, "cow_execution_reviews")? {
        return Ok(0);
    }
    let count: i64 = connection.query_row(
        "SELECT COUNT(*) FROM cow_execution_reviews WHERE network = ?1 AND chain_id = ?2",
        params![network, chain_id],
        |row| row.get(0),
    )?;
    Ok(usize_from_i64(count))
}

fn clear_request_digest(request: &ReviewClearRequest) -> Result<String, ReviewError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply,
        "recordStore": request.record_store.as_str()
    });
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn ensure_clear_write_ledger(connection: &Connection) -> Result<(), ReviewError> {
    connection.execute_batch(
        "CREATE TABLE IF NOT EXISTS manager_write_requests (
             network TEXT NOT NULL,
             chain_id INTEGER NOT NULL,
             operation TEXT NOT NULL,
             idempotency_key TEXT NOT NULL,
             request_id TEXT NOT NULL,
             request_digest TEXT NOT NULL,
             request_json TEXT NOT NULL,
             response_json TEXT NOT NULL,
             created_at TEXT NOT NULL,
             PRIMARY KEY (network, chain_id, operation, idempotency_key)
         )",
    )?;
    Ok(())
}

fn find_clear_write_request(
    connection: &Connection,
    request: &ReviewClearRequest,
) -> Result<Option<String>, ReviewError> {
    if !sqlite_table_exists(connection, "manager_write_requests")? {
        return Ok(None);
    }
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

fn persist_clear_write(
    connection: &Connection,
    request: &ReviewClearRequest,
    request_digest: &str,
    result: &ReviewClearResult,
) -> Result<(), ReviewError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "recordStore": request.record_store.as_str()
    });
    let response_json = serde_json::to_string(&PersistedReviewClearWrite {
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

fn delete_from_optional_manager_table(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
) -> Result<(), ReviewError> {
    if !sqlite_table_exists(connection, table)? {
        return Ok(());
    }
    connection.execute(
        &format!(
            "DELETE FROM {table}
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        ),
        params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY],
    )?;
    Ok(())
}

fn prepare_clear_result(
    connection: &Connection,
    request: &ReviewClearRequest,
) -> Result<ReviewClearResult, ReviewError> {
    let (cleared_reviews, cleared_diagnostics, cleared_broadcast_requests) =
        match request.record_store {
            ReviewRecordStore::Manager => (
                count_scoped_rows(
                    connection,
                    "manager_v4_reviews",
                    &request.network,
                    request.chain_id,
                )?,
                count_scoped_rows(
                    connection,
                    "manager_v4_review_diagnostics",
                    &request.network,
                    request.chain_id,
                )?,
                count_scoped_rows(
                    connection,
                    "manager_v4_review_broadcast_requests",
                    &request.network,
                    request.chain_id,
                )?,
            ),
            ReviewRecordStore::Legacy => (
                count_legacy_review_rows(connection, &request.network, request.chain_id)?,
                0,
                0,
            ),
        };
    Ok(ReviewClearResult {
        network: request.network.clone(),
        chain_id: request.chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: REVIEW_SCOPE_KEY.to_owned(),
        record_store: request.record_store.as_str().to_owned(),
        apply: request.apply,
        dry_run: !request.apply,
        cleared_reviews,
        cleared_diagnostics,
        cleared_broadcast_requests,
        remaining_reviews: if request.apply { 0 } else { cleared_reviews },
        remaining_diagnostics: if request.apply {
            0
        } else {
            cleared_diagnostics
        },
        remaining_broadcast_requests: if request.apply {
            0
        } else {
            cleared_broadcast_requests
        },
    })
}

fn checksum_bytes(value: &[u8]) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(value)))
}

fn usize_from_i64(value: i64) -> usize {
    usize::try_from(value.max(0)).unwrap_or(usize::MAX)
}

#[cfg(test)]
mod tests {
    use super::{
        clear_review_records_write, mark_stale_reviews, read_review_page, read_review_snapshot,
        run_reviews, run_reviews_write, ExpectedUpstream, ReviewClearRequest,
        ReviewDiagnosticInput, ReviewDiagnosticType, ReviewError, ReviewEvidenceInput,
        ReviewOutcomeFilter, ClosedLoopIdentityEvidence, ReviewReadQuery, ReviewRecordStore,
        ReviewWriteRequest, RunArbitrageEvidence, SignedPnl, STALE_MOMENT_BASIS,
    };
    use super::{hop_expected_amount, HopAmountFailure, HopState, ReviewTimePoolState};
    use chrono::Timelike;
    use rusqlite::{params, Connection};
    use std::collections::BTreeMap;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const LOOP_KEY: &str = "loop-1";

    /// A pool that charges 90% and sits at price one, so a hop that
    /// receives an amount returns roughly a tenth of it.
    fn expensive_hop(pool_id: &str) -> (ReviewTimePoolState, HopState) {
        let expected = ReviewTimePoolState {
            pool_id: pool_id.to_owned(),
            token0: "0xaaa".to_owned(),
            token1: "0xbbb".to_owned(),
            fee: 900_000,
            tick_spacing: 60,
            hooks: "0x0000000000000000000000000000000000000000".to_owned(),
            sqrt_price_x96: 1u128 << 96,
            tick: 0,
            liquidity: 100_000_000_000_000_000_000,
            liquidity_read_at: None,
        };
        let hop = HopState {
            pool_id: pool_id.to_owned(),
            token_in: "0xaaa".to_owned(),
            token_out: "0xbbb".to_owned(),
            sqrt_price_x96: 1u128 << 96,
            liquidity: 100_000_000_000_000_000_000,
            fee_pips: 900_000,
            zero_for_one: true,
        };
        (expected, hop)
    }

    #[test]
    fn a_hop_is_priced_with_the_amount_that_reaches_it() {
        // The amount that reaches a hop is the output of its predecessor,
        // not the principal the loop was funded with. The state transition
        // gate therefore prices the second hop with what the first hop
        // produced; with a 90% fee that is a tenth of the principal.
        let (expected, hop) = expensive_hop("pool-hop-0");
        let principal = 1_000_000_000_000_000u128;

        let first = hop_expected_amount(&expected, &hop, principal)
            .expect("the first hop is priced with the principal");
        assert_eq!(first.amount_in_raw, principal);
        assert!(
            first.amount_out_raw <= principal / 10,
            "the 90% fee must leave at most a tenth: {}",
            first.amount_out_raw
        );
        assert!(
            first.amount_out_raw > principal / 20,
            "the hop must still produce a usable amount: {}",
            first.amount_out_raw
        );

        let second = hop_expected_amount(&expected, &hop, first.amount_out_raw)
            .expect("the second hop is priced with the output of the first");
        assert_eq!(
            second.amount_in_raw, first.amount_out_raw,
            "the second hop must receive what the first hop produced"
        );
        assert!(
            second.amount_in_raw < principal / 5,
            "the second hop must not be priced with the principal: {}",
            second.amount_in_raw
        );
    }

    #[test]
    fn a_hop_that_crosses_its_tick_range_is_refused() {
        let (expected, hop) = expensive_hop("pool-hop-0");
        let principal = 1_000_000_000_000_000u128;
        let capacity = pool_manager::depth::single_tick_swap_amount_out(
            expected.sqrt_price_x96,
            expected.liquidity,
            hop.fee_pips,
            expected.tick_spacing as i32,
            expected.tick as i32,
            principal,
            hop.zero_for_one,
        )
        .expect("the hop is priceable")
        .max_amount_in_raw;

        assert_eq!(
            hop_expected_amount(&expected, &hop, capacity + 1).unwrap_err(),
            HopAmountFailure::CrossesTick,
            "one unit above the single-tick bound must be refused"
        );
        assert!(
            hop_expected_amount(&expected, &hop, capacity).is_ok(),
            "the bound itself must still be priceable"
        );
    }

    #[test]
    fn a_hop_without_a_price_is_refused() {
        let (mut expected, hop) = expensive_hop("pool-hop-0");
        expected.sqrt_price_x96 = 0;

        assert_eq!(
            hop_expected_amount(&expected, &hop, 1_000_000_000_000_000u128).unwrap_err(),
            HopAmountFailure::Unpriceable,
            "a pool without a price cannot price any amount"
        );
    }

    #[test]
    fn a_rotation_of_a_directed_cycle_is_a_different_closed_loop() {
        // The closed-loop identity is the funding anchor plus the directed
        // pool path. A rotation of that path starts at an intermediate
        // token, so it names a different closed loop with its own capacity
        // and its own threshold. It must never share the identity of the
        // loop it is a rotation of.
        let anchor = "0x0000000000000000000000000000000000000000:0xaaa:0xbbb";
        let rotated = "0xaaa:0xbbb:0x0000000000000000000000000000000000000000";
        let reverse = "0x0000000000000000000000000000000000000000:0xbbb:0xaaa";

        assert_ne!(anchor, rotated);
        assert_ne!(anchor, reverse);
        assert_ne!(rotated, reverse);
    }

    #[test]
    fn the_review_manager_exposes_no_rotation_capability() {
        // The rotation helpers were deleted: a rotation-aware membership
        // test let a review borrow the capacity, pool set and threshold of
        // a closed loop the Manager published under another key.
        let source = include_str!("lib.rs");
        for name in [
            "directed_cycle_rotations",
            "directed_cycle_identity",
            "directed_cycle_keys_match",
        ] {
            assert!(
                !source.contains(&format!("fn {name}(")),
                "the review manager still defines {name}()"
            );
        }
    }

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = seed_store(&directory.path().join("manager.sqlite3"));
        (directory, path)
    }

    /// The same fixture, but with the store inside a `db` directory.
    ///
    /// The record layer derives its root from the directory the store lives
    /// in, so the fixture has to use the layout of the running system: the
    /// record root then stays inside the fixture instead of next to the
    /// temporary root.
    fn database_with_record_root() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let db_directory = directory.path().join("db");
        std::fs::create_dir_all(&db_directory).unwrap();
        let path = seed_store(&db_directory.join("manager.sqlite3"));
        (directory, path)
    }

    fn seed_store(path: &std::path::Path) -> std::path::PathBuf {
        manager_db::migrate(path).unwrap();
        let connection = Connection::open(path).unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_closed_loop_library_state (
                    network, chain_id, protocol, scope_key, closed_loop_version,
                    materialized_checksum, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, pool_scope_checksum,
                    library_versions_json, library_checksums_json, anchor_count,
                    loop_count, hop_count, optimization_count, skipped_token_count,
                    consumers_need_rebuild, generated_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', 9, 'sha256:closed-loop',
                           7, 'sha256:candidate-edge', 4, 'sha256:observation',
                           'sha256:pool-scope', '{\"core\":3}', '{\"core\":\"sha256:core\"}',
                           1, 1, 3, 0, 0, 0, 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_closed_loops (
                    network, chain_id, protocol, scope_key, loop_key, route_key_id,
                    anchor_token_key_id, anchor_address, anchor_symbol,
                    token_y_key_id, token_z_key_id, path_text,
                    path_addresses_json, path_symbols_json, pool_path_key,
                    directional_pool_ids_json, backup_directional_pool_ids_json,
                    hop_candidate_counts_json,
                    hop_candidate_combination_count, recommendation_basis,
                    recommended_first_priority, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, status, source,
                    single_tick_capacity_anchor_raw,
                    single_tick_capacity_binding_directional_pool_id,
                    created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 'route:v1',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           '0x1111111111111111111111111111111111111111', 'USDG',
                           'robinhood:4663:0x2222222222222222222222222222222222222222',
                           'robinhood:4663:0x3333333333333333333333333333333333333333',
                           'USDG -> AMZN -> TA -> USDG',
                           '[\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x3333333333333333333333333333333333333333\",\"0x1111111111111111111111111111111111111111\"]',
                           '[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]', 'pool-path:1',
                           '[\"dpool:v1:robinhood:4663:core4:pool-a:1\",\"dpool:v1:robinhood:4663:core4:pool-yz:1\",\"dpool:v1:robinhood:4663:core4:pool-d:1\"]', '{}', '{\"1\":1,\"2\":1,\"3\":1}',
                           1, 'fixture', 1, 7, 'sha256:candidate-edge', 4,
                           'sha256:observation', 'active', 'fixture',
                           '1000000000000000000000000',
                           'dpool:v1:robinhood:4663:core4:pool-yz:1',
                           'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID, LOOP_KEY],
            )
            .unwrap();
        seed_pool_states(&connection);
        seed_directional_pools(&connection);
        seed_runtime_state(&connection);
        drop(connection);
        path.to_path_buf()
    }

    /// The runtime state of the fixture's closed loop.
    ///
    /// The running system **always** publishes the runtime state of a
    /// closed loop: it names the moment the loop is scheduled at and the
    /// moment its round started.  Those two moments are what every
    /// freshness judgement is made of, so the fixture has to carry them
    /// -- without them the fixture would describe a closed loop that no
    /// runtime ever measured.
    fn seed_runtime_state(connection: &Connection) {
        // The round of the loop started a while ago and the loop waits for
        // its next round: a measurement taken after that moment belongs
        // to the round, and a measurement taken before it does not.
        let round_started_at = "2026-01-01T00:00:00Z";
        let next_review_at = "2099-01-01T00:00:00Z";
        connection
            .execute(
                "INSERT INTO manager_v4_closed_loop_runtime_state (
                    network, chain_id, protocol, scope_key, loop_key,
                    runtime_status, status_reason, review_generation,
                    review_required, last_rebuilt_at, updated_at,
                    last_reviewed_at, next_review_at, review_cycle_started_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 'cooldown',
                           'fixture', 1, 0, 'fixture', 'fixture', ?4, ?5, ?6)",
                params![NETWORK, CHAIN_ID, LOOP_KEY, round_started_at, next_review_at, round_started_at],
            )
            .unwrap();
    }

    /// The identity of one directional pool of the fixture's scope.
    ///
    /// The directional pool id is the base pool id **plus** the direction,
    /// so a consumer that holds it cannot drop the direction and keep the
    /// pool.
    fn directional_pool_id(base_pool_id: &str, direction: i64) -> String {
        format!("dpool:v1:{NETWORK}:{CHAIN_ID}:core4:{base_pool_id}:{direction}")
    }

    /// The direction layer rows of the fixture's three pools.
    ///
    /// The closed loop walks **directional** pools, so the direction layer
    /// is the only place where a base pool enters the review: the review
    /// resolves the base pool of a hop through the row of its direction.
    fn seed_directional_pools(connection: &Connection) {
        let pools = [
            (
                "pool-a",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            ),
            (
                "pool-yz",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
            ),
            (
                "pool-d",
                "0x3333333333333333333333333333333333333333",
                "0x1111111111111111111111111111111111111111",
            ),
        ];
        for (base_pool_id, token0, token1) in pools {
            connection
                .execute(
                    "INSERT INTO manager_v4_directional_exchange_pools (
                        network, chain_id, protocol, scope_key, base_pool_id,
                        direction, directional_pool_id, pool_manager,
                        pool_key_json, token0, token1, token0_key_id,
                        token1_key_id, fee, tick_spacing, hooks, pair_key_id,
                        input_token_key_id, output_token_key_id, zero_for_one,
                        pool_status, pair_direction_rank, materialized_at,
                        updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 1, ?4,
                               '0xpoolmanager', '{}', ?5, ?6, ?7, ?8, 3000,
                               60, '0x0', 'pair:fixture', ?7, ?8, 1, 'active',
                               1, 'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        base_pool_id,
                        directional_pool_id(base_pool_id, 1),
                        token0,
                        token1,
                        format!("robinhood:4663:{token0}"),
                        format!("robinhood:4663:{token1}"),
                    ],
                )
                .unwrap();
        }
    }

    /// Seeds a backup directional pool for the fixture's first hop.
    ///
    /// A hop publishes one main pool and keeps the candidates it did not
    /// choose as backups, so the review is allowed to price one of those
    /// backups instead of the main pool: the closed loop then trades a
    /// candidate, and the review has to say so.
    fn seed_first_hop_backup(connection: &Connection) {
        connection
            .execute(
                "INSERT INTO manager_v4_directional_exchange_pools (
                    network, chain_id, protocol, scope_key, base_pool_id,
                    direction, directional_pool_id, pool_manager,
                    pool_key_json, token0, token1, token0_key_id,
                    token1_key_id, fee, tick_spacing, hooks, pair_key_id,
                    input_token_key_id, output_token_key_id, zero_for_one,
                    pool_status, pair_direction_rank, materialized_at,
                    updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', 'pool-b', 1, ?3,
                           '0xpoolmanager', '{}',
                           '0x1111111111111111111111111111111111111111',
                           '0x2222222222222222222222222222222222222222',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           'robinhood:4663:0x2222222222222222222222222222222222222222',
                           3000, 60, '0x0', 'pair:fixture',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           'robinhood:4663:0x2222222222222222222222222222222222222222',
                           1, 'active', 2, 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID, directional_pool_id("pool-b", 1)],
            )
            .unwrap();
        connection
            .execute(
                "UPDATE manager_v4_closed_loops
                 SET backup_directional_pool_ids_json = ?1
                 WHERE network = ?2 AND chain_id = ?3 AND loop_key = ?4",
                params![
                    serde_json::json!({
                        "first": [directional_pool_id("pool-b", 1)],
                        "middle": [],
                        "third": [],
                    })
                    .to_string(),
                    NETWORK,
                    CHAIN_ID,
                    LOOP_KEY
                ],
            )
            .unwrap();
        seed_pool_state(
            connection,
            "pool-b",
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
        );
    }

    /// Seeds the measured pool state of the route's three pools.
    ///
    /// The state is the input of the final single-tick gate: without it the
    /// gate cannot prove anything and every route would be blocked, so the
    /// fixture has to carry a usable price, liquidity, tick and tick
    /// spacing.
    fn seed_pool_states(connection: &Connection) {
        connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    token0 TEXT NOT NULL,
                    token1 TEXT NOT NULL,
                    fee INTEGER NOT NULL,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    hooks TEXT NOT NULL DEFAULT '',
                    tick_spacing INTEGER
                );
                CREATE TABLE IF NOT EXISTS v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    depth_status TEXT NOT NULL,
                    liquidity TEXT,
                    has_liquidity INTEGER,
                    tick INTEGER,
                    sqrt_price_x96 TEXT,
                    liquidity_read_at TEXT,
                    evidence_source TEXT,
                    capacity_raw TEXT,
                    capacity_binding_raw REAL,
                    price_impact_bps REAL,
                    balances_json TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );",
            )
            .unwrap();
        // The route is USDG -> AMZN -> TA -> USDG over pool-a, pool-yz and
        // pool-d, so the pools sit between the consecutive route tokens.
        let pools = [
            (
                "pool-a",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            ),
            (
                "pool-yz",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
            ),
            (
                "pool-d",
                "0x3333333333333333333333333333333333333333",
                "0x1111111111111111111111111111111111111111",
            ),
        ];
        for (pool_id, token0, token1) in pools {
            seed_pool_state(connection, pool_id, token0, token1);
        }
    }

    /// Seeds the measured state of one pool.
    ///
    /// The state is what the route is priced against, so a pool the
    /// review names has to carry one: a pool without a state cannot be
    /// priced, and a route over it cannot be proven.
    fn seed_pool_state(connection: &Connection, pool_id: &str, token0: &str, token1: &str) {
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, token0, token1,
                    fee, dynamic_fee, special_fee, hooks, tick_spacing
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, 3000, 0, 0, '', 60)",
                params![NETWORK, CHAIN_ID, pool_id, token0, token1],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, depth_status,
                    liquidity, has_liquidity, tick, sqrt_price_x96,
                    liquidity_read_at, evidence_source
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, 'ok',
                           '100000000000000000000', 1, 0,
                           '79228162514264337593543950336',
                           'block:100', 'fixture')",
                params![NETWORK, CHAIN_ID, pool_id],
            )
            .unwrap();
    }

    fn insert_review_fixture(
        connection: &Connection,
        loop_key: &str,
        review_status: &str,
        signed_pnl_amount: Option<f64>,
        reviewed_at: &str,
    ) {
        let signed_pnl_raw = signed_pnl_amount.map(|amount| format!("{amount} USDG"));
        connection
            .execute(
                "INSERT INTO manager_v4_reviews (
                    network, chain_id, protocol, scope_key, loop_key,
                    closed_loop_version, closed_loop_checksum,
                    candidate_edge_version, candidate_edge_checksum,
                    observation_version, observation_checksum, pool_scope_checksum,
                    anchor_token_key_id, anchor_address, anchor_symbol,
                    path_text, path_addresses_json, path_symbols_json,
                    pool_path_key, pool_ids_json, review_status, review_reason,
                    static_preview_json, run_arbitrage_json, signed_pnl_raw,
                    signed_pnl_amount, signed_pnl_currency, signed_pnl_address,
                    signed_pnl_unit, signed_pnl_source, reviewed_at, created_at,
                    updated_at
                 ) VALUES (
                    ?1, ?2, 'uniswap_v4', 'core4', ?3,
                    9, 'sha256:closed-loop', 7, 'sha256:candidate-edge',
                    4, 'sha256:observation', 'sha256:pool-scope',
                    'robinhood:4663:0x1111111111111111111111111111111111111111',
                    '0x1111111111111111111111111111111111111111', 'USDG',
                    ?4, '[\"0x1111111111111111111111111111111111111111\"]',
                    '[\"USDG\"]', 'pool-path:fixture', '[\"pool-a\"]',
                    ?5, NULL, '{\"ok\":true}', '{\"ok\":true,\"executable\":true}',
                    ?6, ?7, ?8,
                    '0x1111111111111111111111111111111111111111',
                    'token', 'runArbitrage', ?9, ?9, ?9
                 )",
                params![
                    NETWORK,
                    CHAIN_ID,
                    loop_key,
                    format!("USDG -> {loop_key}"),
                    review_status,
                    signed_pnl_raw,
                    signed_pnl_amount,
                    signed_pnl_amount.map(|_| "USDG".to_owned()),
                    reviewed_at,
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_review_diagnostics (
                    network, chain_id, protocol, scope_key, loop_key,
                    diagnostic_id, diagnostic_type, diagnostic_code, message,
                    payload_json, observed_at
                 ) VALUES (
                    ?1, ?2, 'uniswap_v4', 'core4', ?3, ?4, 'other',
                    'fixture', 'fixture diagnostic', '{}', ?5
                 )",
                params![
                    NETWORK,
                    CHAIN_ID,
                    loop_key,
                    format!("diag-{loop_key}"),
                    reviewed_at
                ],
            )
            .unwrap();
        if signed_pnl_amount.is_some() {
            connection
                .execute(
                    "INSERT INTO manager_v4_review_broadcast_requests (
                        network, chain_id, protocol, scope_key, loop_key,
                        closed_loop_version, closed_loop_checksum,
                        anchor_token_key_id, path_text, path_addresses_json,
                        path_symbols_json, pool_path_key, pool_ids_json,
                        signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                        signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                        request_status, created_at, updated_at
                     ) VALUES (
                        ?1, ?2, 'uniswap_v4', 'core4', ?3, 9,
                        'sha256:closed-loop',
                        'robinhood:4663:0x1111111111111111111111111111111111111111',
                        ?4, '[\"0x1111111111111111111111111111111111111111\"]',
                        '[\"USDG\"]', 'pool-path:fixture', '[\"pool-a\"]',
                        ?5, ?6, 'USDG',
                        '0x1111111111111111111111111111111111111111',
                        'token', 'runArbitrage', 'generated', ?7, ?7
                     )",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        loop_key,
                        format!("USDG -> {loop_key}"),
                        signed_pnl_raw,
                        signed_pnl_amount,
                        reviewed_at,
                    ],
                )
                .unwrap();
        }
    }

    fn seed_review_page_rows(path: &std::path::Path) {
        let connection = Connection::open(path).unwrap();
        insert_review_fixture(
            &connection,
            "loop-positive",
            "completed",
            Some(12.5),
            "2026-11-04T00:00:06Z",
        );
        insert_review_fixture(
            &connection,
            "loop-zero",
            "completed",
            Some(0.0),
            "2026-11-04T00:00:05Z",
        );
        insert_review_fixture(
            &connection,
            "loop-data-incomplete",
            "data_incomplete",
            None,
            "2026-11-04T00:00:04Z",
        );
        insert_review_fixture(
            &connection,
            "loop-not-executable",
            "not_executable",
            Some(-1.0),
            "2026-11-04T00:00:03Z",
        );
        insert_review_fixture(
            &connection,
            "loop-failed",
            "failed",
            None,
            "2026-11-04T00:00:02Z",
        );
    }

    fn review_query<'a>() -> ReviewReadQuery<'a> {
        ReviewReadQuery {
            loop_key: None,
            category: None,
            review_status: None,
            outcome: None,
            offset: 0,
            limit: 100,
        }
    }

    /// The review-time state of one candidate pool.
    ///
    /// The values match the state the fixture stores for the pool, so the
    /// three-state gate classifies the route as unchanged.
    fn candidate_pool_fixture(pool_id: &str, token0: &str, token1: &str) -> serde_json::Value {
        serde_json::json!({
            "poolId": pool_id,
            "token0": token0,
            "token1": token1,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
            "sqrtPriceX96": "79228162514264337593543950336",
            "tick": 0,
            "liquidity": "100000000000000000000",
            "liquidityReadAt": "block:100",
        })
    }

    fn signed_pnl(amount: f64) -> SignedPnl {
        SignedPnl {
            raw: format!("{amount} USDG"),
            amount,
            currency: "USDG".to_owned(),
            address: Some("0x1111111111111111111111111111111111111111".to_owned()),
            unit: "token".to_owned(),
            source: "runArbitrage".to_owned(),
        }
    }

    fn evidence(
        signed_pnl: Option<SignedPnl>,
        ok: bool,
        executable: bool,
        diagnostics: Vec<ReviewDiagnosticInput>,
    ) -> ReviewEvidenceInput {
        ReviewEvidenceInput {
            loop_key: LOOP_KEY.to_owned(),
            closed_loop_generation: None,
            // The preview carries the **measured** amount ladder, exactly
            // like the runtime publishes it.  A review may only declare a
            // profit the ladder actually measured, so a fixture without a
            // measured ladder would file every route as an incomplete
            // measurement.
            static_preview: serde_json::json!({
                "ok": ok,
                "executable": executable,
                "bestAmountCalculation": {
                    "bestAmountLadderStatus": "profitable_amount_found",
                    "bestAmountMeasured": true,
                    "probeCount": 3,
                },
            }),
            run_arbitrage: Some(RunArbitrageEvidence {
                ok,
                executable,
                block_number: Some(100),
                signed_pnl,
                diagnostics,
                raw: BTreeMap::from([
                    (
                        "gasEstimate".to_owned(),
                        serde_json::Value::String("120000".to_owned()),
                    ),
                    // The amount the chain was actually asked to execute
                    // with. The final single-tick gate prices every hop
                    // from this amount, so the fixture has to carry it.
                    (
                        "selectedAmountRaw".to_owned(),
                        serde_json::Value::String("1000000000000000".to_owned()),
                    ),
                    // The candidate pools carry the state the ranking
                    // priced. The three-state execution gate compares it
                    // against the current state, so the fixture has to
                    // publish it exactly like the runtime does.
                    (
                        "quote".to_owned(),
                        serde_json::json!({ "candidate_pools": route_pools() }),
                    ),
                ]),
            }),
            diagnostics: Vec::new(),
            reviewed_at: Some("2026-11-04T00:00:00Z".to_owned()),
            closed_loop_identity: None,
        }
    }

    fn expected(path: &std::path::Path) -> ExpectedUpstream {
        let status = super::read_review_status(path, NETWORK, CHAIN_ID).unwrap();
        let closed = closed_loop_manager::read_closed_loop_status(path, NETWORK, CHAIN_ID).unwrap();
        ExpectedUpstream {
            closed_loop_version: Some(closed.closed_loop_version),
            closed_loop_checksum: closed.materialized_checksum,
            candidate_edge_version: closed.candidate_edge_version,
            candidate_edge_checksum: closed.candidate_edge_checksum,
            observation_version: closed.observation_version,
            observation_checksum: closed.observation_checksum,
            pool_scope_checksum: closed.pool_scope_checksum,
            library_versions: closed.library_versions,
            library_checksums: closed.library_checksums,
        }
        .tap(|_| assert_eq!(status.review_version, 0))
    }

    /// A measurement that found a profit parks the loop it measured.
    ///
    /// The clock belongs to the **closed loop**, so the review manager
    /// writes it through the closed-loop Manager: the moment of the
    /// measurement is stored beside the loop, and the loop stops waiting
    /// for a review.
    ///
    /// A round that found a profit means the loop **trades**: it waits
    /// for the receipt of its transaction, so it has no scheduled review
    /// and is parked at the broadcast sentinel.  The broadcast layer puts
    /// the loop back into the queue when the attempt is terminal.
    #[test]
    fn a_profitable_review_parks_the_loop_at_the_broadcast_sentinel() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "INSERT OR REPLACE INTO manager_v4_closed_loop_runtime_state (
                         network, chain_id, protocol, scope_key, loop_key,
                         runtime_status, status_reason, review_generation,
                         review_required, last_rebuilt_at, last_reviewed_at,
                         next_review_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3,
                               'ready_for_review', NULL, 1, 1, 'fixture',
                               NULL, NULL, 'fixture')",
                    params![NETWORK, CHAIN_ID, LOOP_KEY],
                )
                .unwrap();
        }
        run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(12.5)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        let connection = Connection::open(&path).unwrap();
        let clock: (Option<String>, Option<String>, i64, String) = connection
            .query_row(
                "SELECT last_reviewed_at, next_review_at, review_required, runtime_status
                 FROM manager_v4_closed_loop_runtime_state
                 WHERE network = ?1 AND chain_id = ?2 AND scope_key = 'core4'
                   AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, LOOP_KEY],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(clock.0.as_deref(), Some("2026-11-04T00:00:00Z"));
        assert_eq!(
            clock.1.as_deref(),
            Some(closed_loop_manager::BROADCAST_PENDING_SENTINEL_AT),
            "a loop that waits for its receipt has no scheduled review",
        );
        assert_eq!(clock.2, 0, "the loop is not waiting for a review");
        assert_eq!(
            clock.3, "broadcast_pending",
            "a loop that waits for its receipt says so in its state",
        );
    }

    /// A measurement without a profit restarts the review clock of the
    /// loop it measured.
    ///
    /// The loop found no room to trade, so it waits for the next round:
    /// the clock starts again and the loop is due one interval later.
    #[test]
    fn a_review_without_profit_restarts_the_review_clock_of_the_loop() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "INSERT OR REPLACE INTO manager_v4_closed_loop_runtime_state (
                         network, chain_id, protocol, scope_key, loop_key,
                         runtime_status, status_reason, review_generation,
                         review_required, last_rebuilt_at, last_reviewed_at,
                         next_review_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3,
                               'ready_for_review', NULL, 1, 1, 'fixture',
                               NULL, NULL, 'fixture')",
                    params![NETWORK, CHAIN_ID, LOOP_KEY],
                )
                .unwrap();
        }
        run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(-12.5)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        let connection = Connection::open(&path).unwrap();
        let clock: (Option<String>, Option<String>, i64, String) = connection
            .query_row(
                "SELECT last_reviewed_at, next_review_at, review_required, runtime_status
                 FROM manager_v4_closed_loop_runtime_state
                 WHERE network = ?1 AND chain_id = ?2 AND scope_key = 'core4'
                   AND loop_key = ?3",
                params![NETWORK, CHAIN_ID, LOOP_KEY],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(clock.0.as_deref(), Some("2026-11-04T00:00:00Z"));
        let due = clock
            .1
            .as_deref()
            .expect("the round that ended schedules the next round");
        assert!(
            due.starts_with(&chrono::Utc::now().to_rfc3339()[..13]),
            "the next round starts one interval after the round ended, got {due}",
        );
        assert_eq!(clock.2, 0, "the loop is not waiting for a review");
        assert_eq!(clock.3, "cooldown", "the loop waits for its next round");
    }

    /// The evidence of a route that priced a given set of pools.
    ///
    /// The pools are the ones the quote names, in the order the route
    /// walked them: the review confirms those pools and the broadcast
    /// trades them, so a fixture has to be able to price a pool other
    /// than the one the catalog published.
    fn evidence_pricing(
        pools: Vec<serde_json::Value>,
        signed_pnl: Option<SignedPnl>,
        ok: bool,
        executable: bool,
    ) -> ReviewEvidenceInput {
        let mut item = evidence(signed_pnl, ok, executable, Vec::new());
        let quote = item
            .run_arbitrage
            .as_mut()
            .unwrap()
            .raw
            .get_mut("quote")
            .unwrap();
        quote["candidate_pools"] = serde_json::Value::Array(pools);
        item
    }

    /// The three pools the fixture's route walks over.
    fn route_pools() -> Vec<serde_json::Value> {
        vec![
            candidate_pool_fixture(
                "pool-a",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            ),
            candidate_pool_fixture(
                "pool-yz",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
            ),
            candidate_pool_fixture(
                "pool-d",
                "0x3333333333333333333333333333333333333333",
                "0x1111111111111111111111111111111111111111",
            ),
        ]
    }

    trait Tap: Sized {
        fn tap(self, callback: impl FnOnce(&Self)) -> Self {
            callback(&self);
            self
        }
    }

    impl<T> Tap for T {}

    #[test]
    fn missing_or_diagnostic_only_pnl_is_data_incomplete() {
        let (_directory, path) = database();
        let diagnostic = ReviewDiagnosticInput {
            diagnostic_id: None,
            diagnostic_type: ReviewDiagnosticType::SwapNotResolved,
            diagnostic_code: Some("SwapNotResolved".to_owned()),
            message: Some("no executable swap".to_owned()),
            hop_index: Some(2),
            pool_id: Some("pool-yz".to_owned()),
            selector: Some("0xdeadbeef".to_owned()),
            payload: serde_json::json!({"reason": "NoSwap"}),
            observed_at: Some("2026-11-04T00:00:00Z".to_owned()),
        };
        let result = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(None, true, true, vec![diagnostic])],
            true,
        )
        .unwrap();
        assert_eq!(result.data_incomplete_count, 1);
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.broadcast_request_count, 0);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "data_incomplete");
        assert!(snapshot.reviews[0].signed_pnl.is_none());
        assert!(snapshot
            .diagnostics
            .iter()
            .any(|row| row.diagnostic_type == "swap_not_resolved"));
        assert!(snapshot
            .diagnostics
            .iter()
            .any(|row| row.diagnostic_type == "evidence_missing"));
    }

    #[test]
    fn review_page_classifies_filters_and_keeps_related_rows_on_page() {
        let (_directory, path) = database();
        seed_review_page_rows(&path);

        let page = read_review_page(&path, NETWORK, CHAIN_ID, review_query()).unwrap();
        assert_eq!(page.category_counts.total, 5);
        assert_eq!(page.category_counts.completed, 2);
        assert_eq!(page.category_counts.data_incomplete, 1);
        assert_eq!(page.category_counts.not_executable, 1);
        assert_eq!(page.category_counts.failed, 1);
        assert_eq!(page.category_counts.ready_not_submitted, 1);
        assert_eq!(page.category_counts.confirmed_no_profit, 1);
        assert_eq!(page.category_counts.review_failed, 2);
        assert_eq!(page.category_counts.other, 0);
        assert_eq!(page.filtered_count, 5);
        assert_eq!(page.reviews[0].loop_key, "loop-positive");
        assert_eq!(page.reviews[0].page_category, "ready_not_submitted");
        assert_eq!(page.reviews[1].page_category, "confirmed_no_profit");
        assert_eq!(page.reviews[2].page_category, "data_incomplete");
        assert_eq!(page.reviews[3].page_category, "review_failed");

        let success = read_review_page(
            &path,
            NETWORK,
            CHAIN_ID,
            ReviewReadQuery {
                outcome: Some(ReviewOutcomeFilter::Success),
                ..review_query()
            },
        )
        .unwrap();
        assert_eq!(success.filtered_count, 2);
        assert!(success
            .reviews
            .iter()
            .all(|row| row.review_status == "completed"));

        let failed = read_review_page(
            &path,
            NETWORK,
            CHAIN_ID,
            ReviewReadQuery {
                outcome: Some(ReviewOutcomeFilter::Failed),
                category: Some("review_failed"),
                ..review_query()
            },
        )
        .unwrap();
        assert_eq!(failed.filtered_count, 2);
        assert_eq!(
            failed
                .reviews
                .iter()
                .map(|row| row.loop_key.as_str())
                .collect::<Vec<_>>(),
            vec!["loop-not-executable", "loop-failed"]
        );

        let data_incomplete = read_review_page(
            &path,
            NETWORK,
            CHAIN_ID,
            ReviewReadQuery {
                category: Some("data_incomplete"),
                ..review_query()
            },
        )
        .unwrap();
        assert_eq!(data_incomplete.filtered_count, 1);
        assert_eq!(data_incomplete.reviews[0].review_status, "data_incomplete");
        assert!(data_incomplete.reviews[0].signed_pnl.is_none());

        let second_page = read_review_page(
            &path,
            NETWORK,
            CHAIN_ID,
            ReviewReadQuery {
                offset: 1,
                limit: 1,
                ..review_query()
            },
        )
        .unwrap();
        assert_eq!(second_page.filtered_count, 5);
        assert_eq!(second_page.reviews[0].loop_key, "loop-zero");
        assert_eq!(second_page.diagnostics.len(), 1);
        assert_eq!(second_page.diagnostics[0].loop_key, "loop-zero");
        assert_eq!(second_page.broadcast_requests.len(), 1);
        assert_eq!(second_page.broadcast_requests[0].loop_key, "loop-zero");
    }

    /// The final single-tick gate blocks a route whose hop crosses the
    /// next tick, and the blocked route never reaches the broadcast
    /// manager.
    ///
    /// The upstream evidence says the chain accepted the route; the gate
    /// says the route may not be executed under the rules of version 1.
    /// The gate is the last word, because the broadcast manager reads the
    /// broadcast requests that the review writes.
    /// A route whose final input amount is larger than the folded
    /// single-tick capacity is blocked, even when every individual hop would
    /// still fit inside its own tick range.
    ///
    /// The folded capacity is the stronger statement: it is the smallest of
    /// the hop capacities after every hop has been converted into the units
    /// of the anchor token, so an input above it cannot be carried by
    /// **some** hop of the route.
    #[test]
    fn a_route_above_the_folded_capacity_is_blocked() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "UPDATE manager_v4_closed_loops
                     SET single_tick_capacity_anchor_raw = '1000'
                     WHERE network = ?1 AND chain_id = ?2",
                    params![NETWORK, CHAIN_ID],
                )
                .unwrap();
        }
        let result = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(12.5)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "not_executable");
        assert!(snapshot.reviews[0]
            .review_reason
            .as_deref()
            .unwrap_or_default()
            .contains("final_input_exceeds_single_tick_capacity"));
        assert!(snapshot.broadcast_requests.is_empty());
    }

    /// A route without a published folded capacity is a coverage gap, not
    /// proof that the route fits, so it is blocked.
    #[test]
    fn a_route_without_a_folded_capacity_is_blocked() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "UPDATE manager_v4_closed_loops
                     SET single_tick_capacity_anchor_raw = NULL
                     WHERE network = ?1 AND chain_id = ?2",
                    params![NETWORK, CHAIN_ID],
                )
                .unwrap();
        }
        let result = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(12.5)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert!(snapshot.reviews[0]
            .review_reason
            .as_deref()
            .unwrap_or_default()
            .contains("single_tick_capacity_unavailable"));
    }

    /// The broadcast request carries the bound that the review proved.
    #[test]
    fn the_broadcast_request_carries_the_proved_capacity() {
        let (_directory, path) = database();
        let mut positive = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        positive.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![positive], true).unwrap();
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let request = &snapshot.broadcast_requests[0];
        assert_eq!(
            request.single_tick_capacity_anchor_raw,
            Some(1_000_000_000_000_000_000_000_000)
        );
        assert_eq!(
            request.single_tick_capacity_binding_pool_id.as_deref(),
            // The binding pool follows the closed loop, and the closed loop
            // names its pools by their **directional** identity.
            Some("dpool:v1:robinhood:4663:core4:pool-yz:1")
        );
    }

    /// The reviewed route carries the three price limits into the broadcast
    /// request. They are the executable form of the single-tick proof: the
    /// executor must build every swap with its own limit instead of the
    /// global V4 price bound.
    #[test]
    fn the_broadcast_request_carries_the_proved_hop_price_limits() {
        let (_directory, path) = database();
        let mut positive = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        positive.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![positive], true).unwrap();
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        let limits = &snapshot.broadcast_requests[0].hop_sqrt_price_limits;
        assert_eq!(limits.len(), 3);
        for limit in limits {
            assert!(!limit.trim().is_empty());
            assert!(limit.parse::<u128>().is_ok());
        }
        assert_eq!(
            snapshot.reviews[0].hop_sqrt_price_limits, *limits,
            "the review and the broadcast request must carry the same limits"
        );
    }

    #[test]
    fn a_cross_tick_route_is_blocked_at_the_review_stage() {
        let (_directory, path) = database();
        // A position that leaves every hop's tick range.
        let mut crossing = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        crossing.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        // The canonical spelling of a raw amount is hexadecimal, and a hex
        // amount also survives the JSON round trip without losing precision.
        crossing.run_arbitrage.as_mut().unwrap().raw.insert(
            "selectedAmountRaw".to_owned(),
            serde_json::Value::String("0x56bc75e2d63100000".to_owned()),
        );
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![crossing], true).unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(
            result.broadcast_request_count, 0,
            "a blocked route must never become a broadcast request"
        );
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "not_executable");
        assert!(snapshot.reviews[0]
            .review_reason
            .as_deref()
            .unwrap_or_default()
            .starts_with("single_tick_gate_failed:"));
        assert!(
            snapshot.broadcast_requests.is_empty(),
            "the broadcast manager must not receive a non-executable route"
        );
        // The diagnostic carries the whole per-hop picture.
        let diagnostic = snapshot
            .diagnostics
            .iter()
            .find(|row| row.diagnostic_code.as_deref() == Some("SingleTickGateFailed"))
            .expect("the gate must record a diagnostic");
        let hops = diagnostic
            .payload
            .get("hops")
            .and_then(|value| value.as_array())
            .expect("the diagnostic carries the hops");
        // Every hop of the closed loop carries a verdict, so the
        // diagnostic covers the middle hop as well and not only the two
        // ends of the route.
        assert_eq!(hops.len(), 3);
        assert_eq!(
            hops[1].get("poolId").and_then(|value| value.as_str()),
            Some("pool-yz")
        );
        let first = &hops[0];
        assert_eq!(
            first.get("initializedTick").and_then(|v| v.as_i64()),
            Some(0)
        );
        assert_eq!(
            first.get("boundConclusion").and_then(|v| v.as_str()),
            Some("crosses_next_tick")
        );
        assert!(first
            .get("nextTickDistance")
            .and_then(|v| v.as_i64())
            .is_some());
    }

    /// The hops of the fixture's closed loop, with the stored direction of
    /// every hop.
    ///
    /// The hop rows are the closed-loop Manager's own records, and the
    /// direction is the one the hop walks.  The fixture route spends token0
    /// in every hop, so the route describes direction 1 in all three of
    /// them; a stored direction of 0 therefore contradicts the route.
    fn seed_hop_directions(path: &std::path::Path, directions: [i64; 3]) {
        let connection = Connection::open(path).unwrap();
        let hops = [
            (
                1usize,
                "yc",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
                "pool-a",
            ),
            (
                2usize,
                "y-z",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
                "pool-yz",
            ),
            (
                3usize,
                "zc",
                "0x3333333333333333333333333333333333333333",
                "0x1111111111111111111111111111111111111111",
                "pool-d",
            ),
        ];
        for (index, (hop_index, hop_name, input, output, base_pool_id)) in
            hops.iter().enumerate()
        {
            connection
                .execute(
                    "INSERT INTO manager_v4_closed_loop_hops (
                        network, chain_id, protocol, scope_key, loop_key,
                        hop_index, hop_name, input_token_key_id,
                        output_token_key_id, input_address, output_address,
                        pool_selection_basis, temporary_override,
                        directional_pool_id, hop_direction, directional_scope_key,
                        created_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, ?4, ?5, ?6, ?7,
                               ?8, ?9, 'fixture', 0, ?10, 1, 'core4',
                               'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        LOOP_KEY,
                        *hop_index as i64,
                        hop_name,
                        format!("robinhood:4663:{input}"),
                        format!("robinhood:4663:{output}"),
                        input,
                        output,
                        directional_pool_id(base_pool_id, 1),
                    ],
                )
                .unwrap();
            let _ = index;
        }
        // The stored directions are written after the rows exist, so the
        // fixture can name a direction that contradicts the route.
        for (hop_index, direction) in [1usize, 2usize, 3usize]
            .iter()
            .zip(directions.iter())
        {
            connection
                .execute(
                    "UPDATE manager_v4_closed_loop_hops SET hop_direction = ?1 \
                     WHERE network = ?2 AND chain_id = ?3 AND protocol = 'uniswap_v4' \
                       AND scope_key = 'core4' AND loop_key = ?4 AND hop_index = ?5",
                    params![direction, NETWORK, CHAIN_ID, LOOP_KEY, *hop_index as i64],
                )
                .unwrap();
        }
    }

    /// A hop whose stored direction contradicts the route is skipped and
    /// reported, and it never becomes a broadcast request.
    #[test]
    fn a_direction_mismatch_is_reported_and_blocks_the_broadcast() {
        let (_directory, path) = database();
        // The third hop is stored as direction 0 while the route trades it
        // in direction 1, so the two contradict each other.
        seed_hop_directions(&path, [1, 1, 0]);
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        // The review of the loop is **not** voided: the row exists and the
        // measurement is kept.
        assert_eq!(result.loop_count, 1);
        assert_eq!(
            result.broadcast_request_count, 0,
            "a drifted hop must never become a broadcast request"
        );
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews.len(), 1);
        assert_eq!(
            snapshot.reviews[0].signed_pnl.as_ref().unwrap().amount,
            12.5
        );
        assert!(
            snapshot.broadcast_requests.is_empty(),
            "the broadcast manager must not receive a route with a drifted hop"
        );
        // The mismatch is recorded and locatable: the hop index and the
        // pool name travel with it.
        let diagnostic = snapshot
            .diagnostics
            .iter()
            .find(|row| row.diagnostic_code.as_deref() == Some("DirectionMismatch"))
            .expect("the drifted hop must record a diagnostic");
        assert_eq!(diagnostic.hop_index, Some(2));
        assert_eq!(diagnostic.pool_id.as_deref(), Some("pool-d"));
        assert_eq!(diagnostic.diagnostic_type, "other");
        assert_eq!(
            diagnostic
                .payload
                .get("storedDirection")
                .and_then(|value| value.as_i64()),
            Some(0)
        );
        assert_eq!(
            diagnostic
                .payload
                .get("derivedDirection")
                .and_then(|value| value.as_i64()),
            Some(1)
        );
    }

    /// A route whose stored directions agree with the route is measured
    /// normally: the hop chain is complete and the broadcast request is
    /// produced exactly as before.
    #[test]
    fn a_stored_direction_that_agrees_with_the_route_changes_nothing() {
        let (_directory, path) = database();
        seed_hop_directions(&path, [1, 1, 1]);
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 1);
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
        assert!(snapshot
            .diagnostics
            .iter()
            .all(|row| row.diagnostic_code.as_deref() != Some("DirectionMismatch")));
    }

    /// A route that stays inside every hop's tick range keeps the original
    /// profit, gas and fee semantics: it is still completed and it still
    /// produces a broadcast request.
    #[test]
    fn a_single_tick_route_keeps_its_profit_gas_and_fee_semantics() {
        let (_directory, path) = database();
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 1);
        assert_eq!(result.not_executable_count, 0);
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
        assert_eq!(
            snapshot.reviews[0].signed_pnl.as_ref().unwrap().amount,
            12.5
        );
        // The gas estimate travels with the evidence and is not rewritten
        // by the gate.
        let run = snapshot.reviews[0]
            .run_arbitrage
            .get("gasEstimate")
            .and_then(|value| value.as_str());
        assert_eq!(run, Some("120000"));
        assert_eq!(snapshot.broadcast_requests.len(), 1);
        assert_eq!(snapshot.broadcast_requests[0].signed_pnl.amount, 12.5);
    }

    /// A route whose pool state cannot be read cannot be proven
    /// single-tick, so it is blocked rather than promoted. A measurement
    /// gap fails closed; it never fails open.
    /// A legacy-only database does not contain the Manager idempotency
    /// ledger. Review clear must still remove the regenerable legacy rows.
    #[test]
    fn clear_review_records_works_without_manager_write_ledger() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("legacy.sqlite3");
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute_batch(
                    "CREATE TABLE cow_execution_reviews (
                         id INTEGER PRIMARY KEY, network TEXT, chain_id INTEGER
                     );
                     INSERT INTO cow_execution_reviews(network, chain_id)
                     VALUES ('robinhood-testnet', 46630);",
                )
                .unwrap();
        }
        let outcome = clear_review_records_write(
            &path,
            ReviewClearRequest {
                network: "robinhood-testnet".to_owned(),
                chain_id: 46630,
                operation: "review.clear".to_owned(),
                request_id: "clear-legacy".to_owned(),
                idempotency_key: "clear-legacy-key".to_owned(),
                apply: true,
                record_store: ReviewRecordStore::Legacy,
            },
        )
        .unwrap();
        assert_eq!(outcome.result.cleared_reviews, 1);
        assert_eq!(outcome.result.remaining_reviews, 0);
        let connection = Connection::open(&path).unwrap();
        let reviews: i64 = connection
            .query_row("SELECT COUNT(*) FROM cow_execution_reviews", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(reviews, 0);
    }

    /// Clearing review records is dry-run safe and only removes the
    /// regenerable review rows. Token and pool catalogs are left untouched.
    #[test]
    fn clear_review_records_is_dry_run_safe_and_preserves_catalogs() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute_batch(
                    "CREATE TABLE chain_tokens (token_key TEXT PRIMARY KEY, symbol TEXT);
                     INSERT INTO chain_tokens(token_key, symbol) VALUES ('token-a', 'A');
                     CREATE TABLE cow_execution_reviews (
                         id INTEGER PRIMARY KEY, network TEXT, chain_id INTEGER
                     );
                     INSERT INTO cow_execution_reviews(network, chain_id)
                     VALUES ('robinhood', 4663);",
                )
                .unwrap();
        }
        let request = ReviewClearRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "review.clear".to_owned(),
            request_id: "clear-review".to_owned(),
            idempotency_key: "clear-review-key".to_owned(),
            apply: false,
            record_store: ReviewRecordStore::Legacy,
        };
        let dry = clear_review_records_write(&path, request.clone()).unwrap();
        assert_eq!(dry.result.cleared_reviews, 1);
        assert_eq!(dry.result.remaining_reviews, 1);
        {
            let connection = Connection::open(&path).unwrap();
            let reviews: i64 = connection
                .query_row("SELECT COUNT(*) FROM cow_execution_reviews", [], |row| {
                    row.get(0)
                })
                .unwrap();
            let tokens: i64 = connection
                .query_row("SELECT COUNT(*) FROM chain_tokens", [], |row| row.get(0))
                .unwrap();
            let pools: i64 = connection
                .query_row("SELECT COUNT(*) FROM v4_exchange_pools", [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(reviews, 1);
            assert_eq!(tokens, 1);
            assert_eq!(pools, 3);
        }

        let mut apply = request;
        apply.apply = true;
        let cleared = clear_review_records_write(&path, apply.clone()).unwrap();
        assert_eq!(cleared.result.cleared_reviews, 1);
        assert_eq!(cleared.result.remaining_reviews, 0);
        assert!(!cleared.idempotent_replay);
        let replay = clear_review_records_write(&path, apply).unwrap();
        assert!(replay.idempotent_replay);
        {
            let connection = Connection::open(&path).unwrap();
            let reviews: i64 = connection
                .query_row("SELECT COUNT(*) FROM cow_execution_reviews", [], |row| {
                    row.get(0)
                })
                .unwrap();
            let tokens: i64 = connection
                .query_row("SELECT COUNT(*) FROM chain_tokens", [], |row| row.get(0))
                .unwrap();
            let pools: i64 = connection
                .query_row("SELECT COUNT(*) FROM v4_exchange_pools", [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(reviews, 0);
            assert_eq!(tokens, 1);
            assert_eq!(pools, 3);
        }
    }

    #[test]
    fn a_route_without_a_usable_pool_state_is_blocked() {
        let (directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute("DELETE FROM v4_exchange_pool_liquidity_snapshots", [])
                .unwrap();
        }
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
        drop(directory);
    }

    /// A route whose hops are priced from two different pool state
    /// versions describes two different markets, so it is blocked.
    #[test]
    fn a_drifted_pool_state_version_blocks_the_route_at_the_review_stage() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "UPDATE v4_exchange_pool_liquidity_snapshots
                     SET liquidity_read_at = 'block:101'
                     WHERE pool_id = 'pool-d'",
                    [],
                )
                .unwrap();
        }
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
    }

    /// A route whose evidence carries no actual input amount cannot be
    /// priced, so it is blocked rather than executed on a guess.
    #[test]
    fn a_route_without_a_final_input_amount_is_blocked() {
        let (_directory, path) = database();
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        fresh
            .run_arbitrage
            .as_mut()
            .unwrap()
            .raw
            .remove("selectedAmountRaw");
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
    }

    /// A review may confirm a **candidate** of a hop instead of the hop's
    /// main pool: the hop published the candidate as its backup, so the
    /// pool is one the closed loop could have walked.  The substitution is
    /// recorded, because the main pool no longer carries the trade.
    #[test]
    fn a_review_confirming_a_candidate_pool_passes_and_records_the_substitution() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            seed_first_hop_backup(&connection);
        }
        let pools = vec![
            candidate_pool_fixture(
                "pool-b",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            ),
            candidate_pool_fixture(
                "pool-yz",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
            ),
            candidate_pool_fixture(
                "pool-d",
                "0x3333333333333333333333333333333333333333",
                "0x1111111111111111111111111111111111111111",
            ),
        ];
        let mut fresh = evidence_pricing(pools, Some(signed_pnl(12.5)), true, true);
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 1);
        assert_eq!(result.not_executable_count, 0);
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
        assert!(snapshot
            .diagnostics
            .iter()
            .any(|row| row.diagnostic_code.as_deref() == Some("CandidateSubstitution")));
        // The review publishes the **directional** pool it confirmed, so
        // the broadcast trades the pool of the review and not the pool of
        // the catalog.
        assert_eq!(
            snapshot.reviews[0].pool_ids,
            vec![
                directional_pool_id("pool-b", 1),
                directional_pool_id("pool-yz", 1),
                directional_pool_id("pool-d", 1),
            ]
        );
    }

    /// A pool the closed loop never published for that hop is not a pool
    /// the loop could have walked, so the review may not confirm it and
    /// the route may never be broadcast.
    #[test]
    fn a_review_pricing_a_pool_outside_the_loop_options_is_not_executable() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            // The pool is a real pool of the layer, but the loop never
            // published it for the first hop.
            seed_pool_state(
                &connection,
                "pool-stray",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            );
        }
        let pools = vec![
            candidate_pool_fixture(
                "pool-stray",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            ),
            candidate_pool_fixture(
                "pool-yz",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
            ),
            candidate_pool_fixture(
                "pool-d",
                "0x3333333333333333333333333333333333333333",
                "0x1111111111111111111111111111111111111111",
            ),
        ];
        let mut fresh = evidence_pricing(pools, Some(signed_pnl(12.5)), true, true);
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "not_executable");
        assert!(snapshot.reviews[0]
            .review_reason
            .as_deref()
            .unwrap_or_default()
            .starts_with("pool_not_in_loop_options:hop_0:"));
    }

    /// A hop the closed loop published no pool for cannot be confirmed:
    /// the route may have priced any pool, and a review that confirms an
    /// option the loop never offered proves nothing.
    #[test]
    fn a_hop_without_a_published_option_cannot_be_confirmed() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "UPDATE manager_v4_closed_loops
                     SET directional_pool_ids_json = '[]',
                         backup_directional_pool_ids_json = '{}'
                     WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                    params![NETWORK, CHAIN_ID, LOOP_KEY],
                )
                .unwrap();
        }
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.not_executable_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "not_executable");
        assert_eq!(
            snapshot.reviews[0].review_reason.as_deref(),
            Some("loop_options_unavailable:hop_0")
        );
    }

    /// A profit the amount ladder never measured is not a profit: the
    /// route is filed as an incomplete measurement and it never becomes a
    /// broadcast request.
    #[test]
    fn a_review_without_a_measured_ladder_is_data_incomplete() {
        let (_directory, path) = database();
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        fresh.static_preview["bestAmountCalculation"] = serde_json::json!({
            "bestAmountLadderStatus": "no_profitable_amount_found",
            "bestAmountMeasured": false,
            "probeCount": 0,
        });
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(result.completed_count, 0);
        assert_eq!(result.data_incomplete_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "data_incomplete");
        assert!(snapshot.reviews[0]
            .review_reason
            .as_deref()
            .unwrap_or_default()
            .starts_with("ladder_not_measured:"));
    }

    #[test]
    fn positive_executable_review_generates_broadcast_request_only() {
        let (_directory, path) = database();
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        let result = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh.clone()], true).unwrap();
        assert_eq!(result.completed_count, 1);
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
        assert_eq!(snapshot.broadcast_requests[0].request_status, "generated");
        assert_eq!(snapshot.broadcast_requests[0].signed_pnl.amount, 12.5);
        // The request carries the pools of the reviewed loop, and the loop
        // names them by their **directional** identity.
        assert_eq!(
            snapshot.broadcast_requests[0].pool_ids[1],
            "dpool:v1:robinhood:4663:core4:pool-yz:1"
        );

        let repeated = run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        assert_eq!(repeated.review_version, result.review_version);
        assert_eq!(repeated.changed_rows, 0);
        assert!(!repeated.consumers_need_rebuild);
    }

    #[test]
    fn a_positive_review_generates_a_broadcast_request_whatever_round_it_belongs_to() {
        // The broadcast policy has **no freshness window**: the
        // measurement and the send are one continuous action, so the
        // profit that was measured is the profit that is traded.  The
        // moment the loop is scheduled at and the moment its round
        // started say nothing about the age of the measurement, and they
        // are not read here any more.
        let (_directory, path) = database();
        let mut old = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        old.reviewed_at = Some("2020-01-01T00:00:00Z".to_owned());
        let result = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![old],
            true,
        )
        .unwrap();
        assert_eq!(result.completed_count, 1);
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
        assert_eq!(snapshot.broadcast_requests.len(), 1);
        assert_eq!(snapshot.broadcast_requests[0].request_status, "generated");
        assert_eq!(snapshot.broadcast_requests[0].signed_pnl.amount, 12.5);
    }

    #[test]
    fn a_closed_loop_that_went_quiet_is_marked_with_an_explicit_error() {
        let (_directory, path) = database();
        // A round that found no profit ends the round of the loop, so the
        // loop waits for its next round at a **real** moment -- and the
        // moment the round started stays the moment it started.  The
        // measurement of 2020 belongs to a round that ended long before
        // the round that is running now began, so the store holds a
        // measurement that is not the current one of its loop.
        let mut old = evidence(Some(signed_pnl(-12.5)), true, true, Vec::new());
        old.reviewed_at = Some("2020-01-01T00:00:00Z".to_owned());
        run_reviews(&path, NETWORK, CHAIN_ID, vec![old], true).unwrap();
        let outcome = mark_stale_reviews(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(outcome.scanned_count, 1);
        assert_eq!(outcome.marked_count, 1);
        assert_eq!(outcome.stale_loop_keys, vec![LOOP_KEY.to_owned()]);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "data_incomplete");
        assert_eq!(
            snapshot.reviews[0].review_reason.as_deref(),
            Some("not_reviewed_since:2020-01-01T00:00:00Z")
        );
        // A loop that went quiet may never be broadcast.
        assert!(snapshot.broadcast_requests.is_empty());
        // The diagnostic names the reason of the marker.
        assert!(snapshot
            .diagnostics
            .iter()
            .any(|row| row.diagnostic_code.as_deref() == Some("review_stale")));
    }

    #[test]
    fn the_sweep_judges_by_moments_and_not_by_a_window() {
        // The sweep used to ask "is the measurement older than the
        // window", and the window was a number of seconds.  That counted
        // the time the review itself took -- the time the loop was busy is
        // not time the market moved -- and it marked healthy loops stale
        // on every round.  The sweep now compares the moments of the loop
        // with each other, so there is no window to name any more.
        assert_eq!(
            STALE_MOMENT_BASIS, "loop_review_time_point",
            "the sweep must judge by the moments of the closed loop"
        );
    }

    #[test]
    fn a_closed_loop_that_was_never_reviewed_is_marked_as_such() {
        let (_directory, path) = database();
        let outcome = mark_stale_reviews(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(outcome.scanned_count, 1);
        assert_eq!(outcome.marked_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "data_incomplete");
        assert_eq!(
            snapshot.reviews[0].review_reason.as_deref(),
            Some("not_reviewed_at_all")
        );
        assert_eq!(snapshot.reviews[0].reviewed_at, "unreviewed");
    }

    #[test]
    fn a_fresh_review_is_never_marked_as_stale() {
        let (_directory, path) = database();
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        let outcome = mark_stale_reviews(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(outcome.scanned_count, 1);
        assert_eq!(outcome.marked_count, 0);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
    }

    #[test]
    fn a_stale_marker_is_written_once_and_never_rewritten() {
        let (_directory, path) = database();
        let mut old = evidence(Some(signed_pnl(-12.5)), true, true, Vec::new());
        old.reviewed_at = Some("2020-01-01T00:00:00Z".to_owned());
        run_reviews(&path, NETWORK, CHAIN_ID, vec![old], true).unwrap();
        let first = mark_stale_reviews(&path, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(first.marked_count, 1);
        let second = mark_stale_reviews(&path, NETWORK, CHAIN_ID, true).unwrap();
        // The marker is the statement, and repeating it says nothing new.
        assert_eq!(second.marked_count, 0);
    }

    #[test]
    fn a_review_that_arrives_after_the_marker_replaces_it() {
        let (_directory, path) = database();
        let mut old = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        old.reviewed_at = Some("2020-01-01T00:00:00Z".to_owned());
        run_reviews(&path, NETWORK, CHAIN_ID, vec![old], true).unwrap();
        mark_stale_reviews(&path, NETWORK, CHAIN_ID, true).unwrap();
        let mut fresh = evidence(Some(signed_pnl(12.5)), true, true, Vec::new());
        fresh.reviewed_at = Some(chrono::Utc::now().to_rfc3339());
        run_reviews(&path, NETWORK, CHAIN_ID, vec![fresh], true).unwrap();
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
        assert_eq!(snapshot.reviews[0].review_reason, None);
    }

    #[test]
    fn non_positive_completed_review_does_not_generate_broadcast_request() {
        let (_directory, path) = database();
        let result = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(0.0)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        assert_eq!(result.completed_count, 1);
        assert_eq!(result.broadcast_request_count, 0);
    }

    #[test]
    fn write_is_idempotent_and_dry_run_does_not_persist() {
        let (_directory, path) = database();
        let request = ReviewWriteRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "reviews.run".to_owned(),
            request_id: "review-dry".to_owned(),
            idempotency_key: "review-idem".to_owned(),
            apply: false,
            expected_upstream: expected(&path),
            evidence: vec![evidence(Some(signed_pnl(1.0)), true, true, Vec::new())],
        };
        let dry = run_reviews_write(&path, request.clone()).unwrap();
        assert!(dry.result.dry_run);
        assert!(!dry.idempotent_replay);
        let connection = Connection::open(&path).unwrap();
        let review_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_v4_reviews", [], |row| {
                row.get(0)
            })
            .unwrap();
        let request_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(review_count, 0);
        assert_eq!(request_count, 0);

        let apply_request = ReviewWriteRequest {
            request_id: "review-apply".to_owned(),
            apply: true,
            ..request
        };
        let first = run_reviews_write(&path, apply_request.clone()).unwrap();
        assert!(!first.idempotent_replay);
        let replay = run_reviews_write(
            &path,
            ReviewWriteRequest {
                request_id: "review-replay".to_owned(),
                ..apply_request
            },
        )
        .unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, first.result);
    }

    #[test]
    fn stale_closed_loop_version_is_rejected_without_writes() {
        let (_directory, path) = database();
        let mut upstream = expected(&path);
        upstream.closed_loop_version = Some(8);
        let error = run_reviews_write(
            &path,
            ReviewWriteRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "reviews.run".to_owned(),
                request_id: "review-stale".to_owned(),
                idempotency_key: "review-stale-idem".to_owned(),
                apply: true,
                expected_upstream: upstream,
                evidence: vec![evidence(Some(signed_pnl(1.0)), true, true, Vec::new())],
            },
        )
        .unwrap_err();
        assert!(matches!(
            error,
            ReviewError::ExpectedUpstreamMismatch { .. }
        ));
        let connection = Connection::open(path).unwrap();
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_review_library_state",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
    }

    /// The kernel publishes the review evidence. The shape it publishes has
    /// to satisfy this DTO **exactly**, because the review is filed under
    /// these numbers: a missing field rejects the whole review, and a
    /// derived one publishes a profit that was never measured.
    ///
    /// This test pins the boundary from the JSON side, because that is
    /// where the two layers actually meet.
    #[test]
    fn the_kernel_published_evidence_shape_is_accepted() {
        let payload = serde_json::json!({
            "loopKey": LOOP_KEY,
            "staticPreview": {"ok": true},
            "runArbitrage": {
                "ok": true,
                "executable": true,
                "blockNumber": 100,
                "signedPnl": {
                    "raw": "0x8ac7230489e80000",
                    "amount": 10.0,
                    "currency": "ETH",
                    "address": "0x1111111111111111111111111111111111111111",
                    "unit": "raw",
                    "source": "native_rust_ladder",
                },
                "diagnostics": [],
            },
            "diagnostics": [],
            "reviewedAt": "2026-11-17T00:00:00Z",
        });
        let parsed: ReviewEvidenceInput =
            serde_json::from_value(payload).expect("the kernel shape must be accepted");
        assert_eq!(parsed.loop_key, LOOP_KEY);
        let run = parsed.run_arbitrage.expect("the run arbitrage is required");
        assert!(run.ok);
        assert!(run.executable);
        assert_eq!(run.block_number, Some(100));
        let signed = run.signed_pnl.expect("the signed profit is required");
        assert_eq!(signed.raw, "0x8ac7230489e80000");
        assert_eq!(signed.amount, 10.0);
        assert_eq!(signed.currency, "ETH");
        assert_eq!(signed.unit, "raw");
        assert_eq!(signed.source, "native_rust_ladder");
        assert!(super::validate_signed_pnl(&signed));
    }

    /// A signed profit without the human amount is **not** reviewable: the
    /// amount is what the review is judged by, so a raw-only profit cannot
    /// be filed as a measured one.
    #[test]
    fn a_signed_profit_without_the_human_amount_is_not_accepted() {
        let payload = serde_json::json!({
            "loopKey": LOOP_KEY,
            "runArbitrage": {
                "ok": true,
                "executable": true,
                "signedPnl": {
                    "raw": "0x8ac7230489e80000",
                    "currency": "ETH",
                    "address": "0x1111111111111111111111111111111111111111",
                    "unit": "raw",
                    "source": "native_rust_ladder",
                },
            },
        });
        let parsed: Result<ReviewEvidenceInput, _> = serde_json::from_value(payload);
        assert!(
            parsed.is_err(),
            "the human amount of the signed profit is mandatory"
        );
    }
    // -----------------------------------------------------------------
    // The closed-loop identity is published by the Review Manager.
    //
    // The identity of a reviewed route is computed by the closed-loop
    // kernel against the catalog the closed-loop Manager publishes. The
    // Review Manager owns the review record, so it is the Review Manager
    // that stores and publishes the identity: a consumer of the review
    // library must never have to rebuild the identity outside the
    // Manager, and must be able to tell a matched route, a route that
    // priced pools the catalog does not publish, and a route measured
    // against an empty catalog apart.
    // -----------------------------------------------------------------

    #[test]
    fn the_review_manager_publishes_the_closed_loop_identity_of_a_review() {
        let (directory, path) = database();
        let mut evidence = evidence(Some(signed_pnl(1.0)), true, true, Vec::new());
        evidence.closed_loop_identity = Some(ClosedLoopIdentityEvidence {
            closed_loop_identity_status: Some("matched".to_owned()),
            closed_loop_pool_identity_status: Some("verified".to_owned()),
            identity_mismatch: None,
            closed_loop_repair_target: None,
        });

        let outcome = run_reviews_write(
            &path,
            ReviewWriteRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "review.run".to_owned(),
                request_id: "request-identity".to_owned(),
                idempotency_key: "idempotency-identity".to_owned(),
                apply: true,
                expected_upstream: expected(&path),
                evidence: vec![evidence],
            },
        )
        .unwrap();
        assert!(!outcome.idempotent_replay);

        let page = read_review_page(
            &path,
            NETWORK,
            CHAIN_ID,
            ReviewReadQuery {
                loop_key: None,
                category: None,
                review_status: None,
                outcome: None,
                offset: 0,
                limit: 50,
            },
        )
        .unwrap();
        let row = page
            .reviews
            .iter()
            .find(|row| row.loop_key == LOOP_KEY)
            .expect("the reviewed loop is published");
        let identity = row
            .closed_loop_identity
            .as_ref()
            .expect("the review publishes the identity it was measured under");
        assert_eq!(
            identity.closed_loop_identity_status.as_deref(),
            Some("matched")
        );
        assert_eq!(
            identity.closed_loop_pool_identity_status.as_deref(),
            Some("verified")
        );
        assert!(identity.identity_mismatch.is_none());

        drop(directory);
    }

    #[test]
    fn a_review_without_an_identity_publishes_no_identity() {
        // The absence of an identity is a **coverage gap**, not a verdict.
        // A review measured against a catalog that published no loop must
        // read back with no identity at all, so it can never be mistaken
        // for a route that matched.
        let (directory, path) = database();

        run_reviews_write(
            &path,
            ReviewWriteRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "review.run".to_owned(),
                request_id: "request-no-identity".to_owned(),
                idempotency_key: "idempotency-no-identity".to_owned(),
                apply: true,
                expected_upstream: expected(&path),
                evidence: vec![evidence(Some(signed_pnl(1.0)), true, true, Vec::new())],
            },
        )
        .unwrap();

        let page = read_review_page(
            &path,
            NETWORK,
            CHAIN_ID,
            ReviewReadQuery {
                loop_key: None,
                category: None,
                review_status: None,
                outcome: None,
                offset: 0,
                limit: 50,
            },
        )
        .unwrap();
        let row = page
            .reviews
            .iter()
            .find(|row| row.loop_key == LOOP_KEY)
            .expect("the reviewed loop is published");
        assert!(
            row.closed_loop_identity.is_none(),
            "a review without an identity must not publish one: {:?}",
            row.closed_loop_identity
        );

        drop(directory);
    }

    #[test]
    fn the_review_manager_publishes_the_diagnosis_of_a_broken_identity() {
        // A route that priced pools the closed-loop catalog does not
        // publish is a **different** fact from a route that matched. The
        // diagnosis has to travel with the review, otherwise a reader has
        // to re-derive it and cannot tell a pool-identity failure from a
        // missing loop.
        let (directory, path) = database();
        let mut evidence = evidence(Some(signed_pnl(1.0)), true, true, Vec::new());
        evidence.closed_loop_identity = Some(ClosedLoopIdentityEvidence {
            closed_loop_identity_status: Some("mismatch".to_owned()),
            closed_loop_pool_identity_status: Some("mismatch".to_owned()),
            identity_mismatch: Some("manager_closed_loop_pool_identity_mismatch".to_owned()),
            closed_loop_repair_target: Some("pool-0x84bd4e".to_owned()),
        });

        run_reviews_write(
            &path,
            ReviewWriteRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "review.run".to_owned(),
                request_id: "request-broken-identity".to_owned(),
                idempotency_key: "idempotency-broken-identity".to_owned(),
                apply: true,
                expected_upstream: expected(&path),
                evidence: vec![evidence],
            },
        )
        .unwrap();

        let page = read_review_page(
            &path,
            NETWORK,
            CHAIN_ID,
            ReviewReadQuery {
                loop_key: None,
                category: None,
                review_status: None,
                outcome: None,
                offset: 0,
                limit: 50,
            },
        )
        .unwrap();
        let row = page
            .reviews
            .iter()
            .find(|row| row.loop_key == LOOP_KEY)
            .expect("the reviewed loop is published");
        let identity = row
            .closed_loop_identity
            .as_ref()
            .expect("the review publishes the diagnosis of its identity");
        assert_eq!(
            identity.closed_loop_identity_status.as_deref(),
            Some("mismatch")
        );
        assert_eq!(
            identity.identity_mismatch.as_deref(),
            Some("manager_closed_loop_pool_identity_mismatch")
        );
        assert_eq!(
            identity.closed_loop_repair_target.as_deref(),
            Some("pool-0x84bd4e")
        );

        drop(directory);
    }

    #[tokio::test]
    async fn a_written_generation_reaches_the_review_audit_stream() {
        let (directory, path) = database_with_record_root();
        let outcome = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(1.0)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        assert_eq!(outcome.completed_count, 1);

        // The audit stream lands next to the db directory, one file per
        // closed loop and hour inside the folder of its day.
        let records = directory
            .path()
            .join("db")
            .join(super::records::REVIEW_DIR_NAME)
            .join(NETWORK)
            .join(CHAIN_ID.to_string());
        super::records::drain_review_records(&records).await.unwrap();
        // The record is filed under the hour the **review** was measured in,
        // in Beijing time, so the file is looked up in the same clock and
        // not under the hour the writer happened to run in.
        let measured = chrono::DateTime::parse_from_rfc3339("2026-11-04T00:00:00Z")
            .unwrap()
            .with_timezone(&super::records::beijing_offset());
        let content = std::fs::read_to_string(super::records::review_file_path(
            &records,
            measured.date_naive(),
            measured.hour(),
            LOOP_KEY,
        ))
        .unwrap();
        let line: serde_json::Value =
            serde_json::from_str(content.lines().next().unwrap()).unwrap();
        assert_eq!(line["schema"], "review_record.v1");
        assert_eq!(line["event"], "upsert");
        assert_eq!(line["loopKey"], LOOP_KEY);
        assert_eq!(line["reviewVersion"], 1);
    }

    #[tokio::test]
    async fn a_dry_run_never_reaches_the_review_audit_stream() {
        let (directory, path) = database_with_record_root();
        let outcome = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(1.0)), true, true, Vec::new())],
            false,
        )
        .unwrap();
        assert!(outcome.dry_run);

        let records = directory
            .path()
            .join("db")
            .join(super::records::REVIEW_DIR_NAME)
            .join(NETWORK)
            .join(CHAIN_ID.to_string());
        assert!(
            !records.exists(),
            "the dry run wrote into the review audit stream"
        );
    }
}
