use broadcast_manager::{read_broadcast_status, BroadcastStatus};
use candidate_edge_pool_manager::{read_candidate_status, CandidateEdgeStatus};
use candidate_exchange_pool_manager::{read_candidate_exchange_status, CandidateExchangeStatus};
use closed_loop_manager::{read_closed_loop_status, ClosedLoopStatus};
use pool_manager::{read_scope_status, ScopeStatus};
use review_manager::{read_review_status, ReviewStatus};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;
use token_manager::{read_library_state, LibraryKey, TokenLibraryState, TokenQueryError};

mod overview;
pub use overview::*;

const NETWORK_PROTOCOL: &str = "uniswap_v4";
const PIPELINE_KEY: &str = "core4";
const SCOPE_KEY: &str = "core4";
const MAX_IDENTIFIER_LENGTH: usize = 256;
const MAX_ERROR_LENGTH: usize = 4_096;
const MAX_RUN_LIMIT: usize = 200;
const LIBRARY_KEYS: [LibraryKey; 4] = [
    LibraryKey::Core,
    LibraryKey::Stocks,
    LibraryKey::Extension,
    LibraryKey::Test,
];

#[derive(Debug)]
pub enum CompositeError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    InvalidRequest(String),
    UpstreamRead {
        manager: &'static str,
        message: String,
    },
    UpstreamInconsistent {
        issues: Vec<ConsistencyIssue>,
    },
    MissingDependency(String),
    ExpectedDependencyMismatch {
        field: String,
        expected: String,
        actual: String,
    },
    RunNotFound(String),
    StepNotFound {
        run_id: String,
        step_id: String,
    },
    StepNotRunning {
        step_id: String,
        status: String,
    },
    StepAlreadyFinal {
        step_id: String,
        status: String,
    },
    StepOutOfOrder {
        step_id: String,
        blocking_step_id: String,
        blocking_status: String,
    },
    OutputNotVerified {
        step_id: String,
        field: String,
        expected: String,
        actual: String,
    },
    PendingBroadcastAttempts(usize),
    IdempotencyKeyReused {
        idempotency_key: String,
        existing_request_id: String,
        submitted_request_id: String,
    },
}

impl fmt::Display for CompositeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "composite storage error: {error}"),
            Self::Json(error) => write!(formatter, "composite json error: {error}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid composite request: {message}")
            }
            Self::UpstreamRead { manager, message } => {
                write!(formatter, "{manager} status read failed: {message}")
            }
            Self::UpstreamInconsistent { issues } => write!(
                formatter,
                "pipeline upstream versions are inconsistent: {}",
                serde_json::to_string(issues).unwrap_or_else(|_| "[]".to_owned())
            ),
            Self::MissingDependency(dependency) => {
                write!(formatter, "pipeline dependency is not materialized: {dependency}")
            }
            Self::ExpectedDependencyMismatch {
                field,
                expected,
                actual,
            } => write!(
                formatter,
                "pipeline dependency {field} changed: expected {expected}, actual {actual}"
            ),
            Self::RunNotFound(run_id) => write!(formatter, "pipeline run {run_id} does not exist"),
            Self::StepNotFound { run_id, step_id } => {
                write!(formatter, "pipeline step {step_id} does not exist in run {run_id}")
            }
            Self::StepNotRunning { step_id, status } => {
                write!(formatter, "pipeline step {step_id} is not running; current status is {status}")
            }
            Self::StepAlreadyFinal { step_id, status } => {
                write!(formatter, "pipeline step {step_id} is already terminal with status {status}")
            }
            Self::StepOutOfOrder {
                step_id,
                blocking_step_id,
                blocking_status,
            } => write!(
                formatter,
                "pipeline step {step_id} is blocked by {blocking_step_id} with status {blocking_status}"
            ),
            Self::OutputNotVerified {
                step_id,
                field,
                expected,
                actual,
            } => write!(
                formatter,
                "pipeline step {step_id} output {field} is not verified: expected {expected}, actual {actual}"
            ),
            Self::PendingBroadcastAttempts(count) => write!(
                formatter,
                "broadcast step cannot complete while {count} attempts are pending"
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

impl std::error::Error for CompositeError {}

impl From<rusqlite::Error> for CompositeError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<serde_json::Error> for CompositeError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PipelineStepName {
    PoolScope,
    CandidateEdge,
    CandidateExchange,
    ClosedLoop,
    Review,
    Broadcast,
}

impl PipelineStepName {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::PoolScope => "pool_scope",
            Self::CandidateEdge => "candidate_edge",
            Self::CandidateExchange => "candidate_exchange",
            Self::ClosedLoop => "closed_loop",
            Self::Review => "review",
            Self::Broadcast => "broadcast",
        }
    }

    fn from_storage(value: &str) -> Option<Self> {
        match value {
            "pool_scope" => Some(Self::PoolScope),
            "candidate_edge" => Some(Self::CandidateEdge),
            "candidate_exchange" => Some(Self::CandidateExchange),
            "closed_loop" => Some(Self::ClosedLoop),
            "review" => Some(Self::Review),
            "broadcast" => Some(Self::Broadcast),
            _ => None,
        }
    }

    fn order(self) -> i64 {
        match self {
            Self::PoolScope => 0,
            Self::CandidateEdge => 1,
            Self::CandidateExchange => 2,
            Self::ClosedLoop => 3,
            Self::Review => 4,
            Self::Broadcast => 5,
        }
    }

    fn dependencies(self) -> &'static [&'static str] {
        match self {
            Self::PoolScope => &["token_libraries"],
            Self::CandidateEdge => &["token_libraries", "pool_scope"],
            Self::CandidateExchange => &["token_libraries", "pool_scope", "candidate_edge"],
            Self::ClosedLoop => &["candidate_edge", "candidate_exchange"],
            Self::Review => &["closed_loop"],
            Self::Broadcast => &["closed_loop", "review"],
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PipelineRunStatus {
    Running,
    Completed,
    Failed,
    Idle,
}

impl PipelineRunStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Idle => "idle",
        }
    }

    fn from_storage(value: &str) -> Option<Self> {
        match value {
            "running" => Some(Self::Running),
            "completed" => Some(Self::Completed),
            "failed" => Some(Self::Failed),
            "idle" => Some(Self::Idle),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PipelineStepStatus {
    Pending,
    Running,
    Completed,
    Failed,
    Idle,
}

impl PipelineStepStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Idle => "idle",
        }
    }

    fn from_storage(value: &str) -> Option<Self> {
        match value {
            "pending" => Some(Self::Pending),
            "running" => Some(Self::Running),
            "completed" => Some(Self::Completed),
            "failed" => Some(Self::Failed),
            "idle" => Some(Self::Idle),
            _ => None,
        }
    }

    fn is_successful(self) -> bool {
        matches!(self, Self::Completed | Self::Idle)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepCompletionStatus {
    Completed,
    Failed,
    Idle,
}

impl StepCompletionStatus {
    fn as_step_status(self) -> PipelineStepStatus {
        match self {
            Self::Completed => PipelineStepStatus::Completed,
            Self::Failed => PipelineStepStatus::Failed,
            Self::Idle => PipelineStepStatus::Idle,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenLibraryRef {
    pub enabled: bool,
    pub version: i64,
    pub checksum: String,
    pub token_count: usize,
    pub admitted_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PoolScopeRef {
    pub enabled: bool,
    pub materialized_checksum: Option<String>,
    pub live_checksum: String,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub active_pool_count: usize,
}

impl PoolScopeRef {
    fn effective_checksum(&self) -> Option<&str> {
        self.materialized_checksum
            .as_deref()
            .filter(|value| !value.is_empty())
            .or_else(|| (!self.live_checksum.is_empty()).then_some(self.live_checksum.as_str()))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateEdgeRef {
    pub enabled: bool,
    pub version: i64,
    pub checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub anchor_count: usize,
    pub pool_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateExchangeRef {
    pub enabled: bool,
    pub version: i64,
    pub checksum: Option<String>,
    pub candidate_edge_version: Option<i64>,
    pub candidate_edge_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub direction_count: usize,
    pub pool_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopRef {
    pub enabled: bool,
    pub version: i64,
    pub checksum: Option<String>,
    pub candidate_edge_version: Option<i64>,
    pub candidate_edge_checksum: Option<String>,
    pub observation_version: Option<i64>,
    pub observation_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    pub loop_count: usize,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewRef {
    pub enabled: bool,
    pub version: i64,
    pub checksum: Option<String>,
    pub closed_loop_version: Option<i64>,
    pub closed_loop_checksum: Option<String>,
    pub completed_count: usize,
    pub data_incomplete_count: usize,
    pub broadcast_request_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastRef {
    pub enabled: bool,
    pub version: i64,
    pub checksum: Option<String>,
    pub review_version: Option<i64>,
    pub review_checksum: Option<String>,
    pub closed_loop_version: Option<i64>,
    pub closed_loop_checksum: Option<String>,
    pub pending_count: usize,
    pub success_count: usize,
    pub failed_count: usize,
    pub terminal_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpstreamSnapshot {
    pub token_libraries: BTreeMap<String, TokenLibraryRef>,
    pub pool_scope: PoolScopeRef,
    pub candidate_edge: CandidateEdgeRef,
    pub candidate_exchange: CandidateExchangeRef,
    pub closed_loop: ClosedLoopRef,
    pub review: ReviewRef,
    pub broadcast: BroadcastRef,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConsistencyIssue {
    pub component: String,
    pub field: String,
    pub expected: String,
    pub actual: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpstreamAssessment {
    pub fingerprint: String,
    pub consistent: bool,
    pub issues: Vec<ConsistencyIssue>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StepDependencySnapshot {
    pub token_libraries: BTreeMap<String, TokenLibraryRef>,
    pub pool_scope: Option<PoolScopeRef>,
    pub candidate_edge: Option<CandidateEdgeRef>,
    pub candidate_exchange: Option<CandidateExchangeRef>,
    pub closed_loop: Option<ClosedLoopRef>,
    pub review: Option<ReviewRef>,
    #[serde(default)]
    pub fingerprint: String,
}

#[derive(Debug, Clone)]
pub struct PipelineRefreshRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub requested_steps: Vec<PipelineStepName>,
}

#[derive(Debug, Clone)]
pub struct PipelineBeginStepRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub run_id: String,
    pub step_id: String,
}

#[derive(Debug, Clone)]
pub struct PipelineCompleteStepRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub run_id: String,
    pub step_id: String,
    pub status: StepCompletionStatus,
    pub result: serde_json::Value,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineStepRow {
    pub run_id: String,
    pub step_index: usize,
    pub step_id: String,
    pub step_name: PipelineStepName,
    pub depends_on: Vec<String>,
    pub status: PipelineStepStatus,
    pub attempt_count: usize,
    pub dependency_snapshot: Option<StepDependencySnapshot>,
    pub result: serde_json::Value,
    pub last_error: Option<String>,
    pub started_at: Option<String>,
    pub last_attempted_at: Option<String>,
    pub completed_at: Option<String>,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineRunRow {
    pub run_id: String,
    pub status: PipelineRunStatus,
    pub requested_steps: Vec<PipelineStepName>,
    pub upstream_snapshot: UpstreamSnapshot,
    pub upstream_fingerprint: String,
    pub current_step_index: usize,
    pub total_steps: usize,
    pub last_completed_step_id: Option<String>,
    pub last_error: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineEventRow {
    pub event_id: String,
    pub run_id: String,
    pub step_id: Option<String>,
    pub event_type: String,
    pub actor: String,
    pub request_id: String,
    pub payload: serde_json::Value,
    pub created_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineSnapshot {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub pipeline_key: String,
    pub latest_run: Option<PipelineRunRow>,
    pub steps: Vec<PipelineStepRow>,
    pub events: Vec<PipelineEventRow>,
    pub current_step: Option<PipelineStepRow>,
    pub checkpoint_step_id: Option<String>,
    pub upstream: UpstreamSnapshot,
    pub upstream_assessment: UpstreamAssessment,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineRunDetail {
    pub run: PipelineRunRow,
    pub steps: Vec<PipelineStepRow>,
    pub events: Vec<PipelineEventRow>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineRefreshResult {
    pub run: PipelineRunRow,
    pub steps: Vec<PipelineStepRow>,
    pub upstream_assessment: UpstreamAssessment,
    pub apply: bool,
    pub dry_run: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineRefreshOutcome {
    #[serde(flatten)]
    pub result: PipelineRefreshResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineStepResult {
    pub run: PipelineRunRow,
    pub step: PipelineStepRow,
    pub steps: Vec<PipelineStepRow>,
    pub upstream_assessment: UpstreamAssessment,
    pub apply: bool,
    pub dry_run: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PipelineStepOutcome {
    #[serde(flatten)]
    pub result: PipelineStepResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedRefreshWrite {
    request_id: String,
    request_digest: String,
    outcome: PipelineRefreshResult,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedStepWrite {
    request_id: String,
    request_digest: String,
    outcome: PipelineStepResult,
}

pub fn read_pipeline_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<PipelineSnapshot, CompositeError> {
    validate_identity(network, chain_id)?;
    let database_path = database_path.as_ref();
    let connection = open_connection(database_path, false)?;
    let latest_run = read_latest_run(&connection, network, chain_id)?;
    let (steps, events) = if let Some(run) = latest_run.as_ref() {
        (
            read_steps(&connection, network, chain_id, &run.run_id)?,
            read_events(&connection, network, chain_id, &run.run_id, MAX_RUN_LIMIT)?,
        )
    } else {
        (Vec::new(), Vec::new())
    };
    let current_step = steps
        .iter()
        .find(|step| step.status == PipelineStepStatus::Running)
        .or_else(|| {
            steps
                .iter()
                .find(|step| step.status == PipelineStepStatus::Pending)
        })
        .cloned();
    let checkpoint_step_id = steps
        .iter()
        .rev()
        .find(|step| step.status.is_successful())
        .map(|step| step.step_id.clone());
    let upstream = build_upstream_snapshot(database_path, network, chain_id)?;
    let upstream_assessment = assess_upstream(&upstream);
    Ok(PipelineSnapshot {
        enabled: latest_run.is_some(),
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        pipeline_key: PIPELINE_KEY.to_owned(),
        latest_run,
        steps,
        events,
        current_step,
        checkpoint_step_id,
        upstream,
        upstream_assessment,
    })
}

pub fn read_pipeline_run(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    run_id: &str,
) -> Result<(PipelineRunRow, Vec<PipelineStepRow>, Vec<PipelineEventRow>), CompositeError> {
    validate_identity(network, chain_id)?;
    validate_identifier("runId", run_id)?;
    let connection = open_connection(database_path.as_ref(), false)?;
    let run = read_run(&connection, network, chain_id, run_id)?;
    Ok((
        run.clone(),
        read_steps(&connection, network, chain_id, &run.run_id)?,
        read_events(&connection, network, chain_id, &run.run_id, MAX_RUN_LIMIT)?,
    ))
}

pub fn read_pipeline_run_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    run_id: &str,
) -> Result<PipelineRunDetail, CompositeError> {
    let (run, steps, events) = read_pipeline_run(database_path, network, chain_id, run_id)?;
    Ok(PipelineRunDetail { run, steps, events })
}

pub fn read_pipeline_runs(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    limit: usize,
) -> Result<Vec<PipelineRunRow>, CompositeError> {
    validate_identity(network, chain_id)?;
    if limit == 0 || limit > MAX_RUN_LIMIT {
        return Err(CompositeError::InvalidRequest(format!(
            "limit must be between 1 and {MAX_RUN_LIMIT}"
        )));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let mut statement = connection.prepare(
        "SELECT run_id
         FROM manager_pipeline_runs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pipeline_key = ?4
         ORDER BY created_at DESC, run_id DESC
         LIMIT ?5",
    )?;
    let run_ids = statement
        .query_map(
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                PIPELINE_KEY,
                to_i64(limit)
            ],
            |row| row.get::<_, String>(0),
        )?
        .collect::<Result<Vec<_>, _>>()?;
    run_ids
        .into_iter()
        .map(|run_id| read_run(&connection, network, chain_id, &run_id))
        .collect()
}

pub fn refresh_pipeline_write(
    database_path: impl AsRef<Path>,
    request: PipelineRefreshRequest,
) -> Result<PipelineRefreshOutcome, CompositeError> {
    validate_refresh_request(&request)?;
    let request_digest = refresh_request_digest(&request)?;
    let run_id = generated_run_id(&request.request_id, &request_digest);
    let mut connection = open_connection(database_path.as_ref(), request.apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if request.apply {
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let outcome =
            refresh_on_connection(&transaction, &run_id, &request, &request_digest, true)?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        refresh_on_connection(&connection, &run_id, &request, &request_digest, false)
    }
}

pub fn begin_pipeline_step_write(
    database_path: impl AsRef<Path>,
    request: PipelineBeginStepRequest,
) -> Result<PipelineStepOutcome, CompositeError> {
    validate_step_request_identity(&request)?;
    let request_digest = begin_request_digest(&request)?;
    let mut connection = open_connection(database_path.as_ref(), request.apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if request.apply {
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let outcome = begin_step_on_connection(
            &transaction,
            &request,
            &request_digest,
            database_path.as_ref(),
            true,
        )?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        begin_step_on_connection(
            &connection,
            &request,
            &request_digest,
            database_path.as_ref(),
            false,
        )
    }
}

pub fn complete_pipeline_step_write(
    database_path: impl AsRef<Path>,
    request: PipelineCompleteStepRequest,
) -> Result<PipelineStepOutcome, CompositeError> {
    validate_complete_request(&request)?;
    let request_digest = complete_request_digest(&request)?;
    let mut connection = open_connection(database_path.as_ref(), request.apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if request.apply {
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let outcome = complete_step_on_connection(
            &transaction,
            &request,
            &request_digest,
            database_path.as_ref(),
            true,
        )?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        complete_step_on_connection(
            &connection,
            &request,
            &request_digest,
            database_path.as_ref(),
            false,
        )
    }
}

fn refresh_on_connection(
    connection: &Connection,
    run_id: &str,
    request: &PipelineRefreshRequest,
    request_digest: &str,
    apply: bool,
) -> Result<PipelineRefreshOutcome, CompositeError> {
    let network = request.network.as_str();
    let chain_id = request.chain_id;
    let requested_steps = request.requested_steps.as_slice();
    if apply {
        if let Some((existing_request_id, existing_digest, response_json)) = find_write_request(
            connection,
            network,
            chain_id,
            request.operation.as_str(),
            request.idempotency_key.as_str(),
        )? {
            if existing_digest != request_digest {
                return Err(CompositeError::IdempotencyKeyReused {
                    idempotency_key: request.idempotency_key.clone(),
                    existing_request_id,
                    submitted_request_id: request.request_id.clone(),
                });
            }
            let persisted = serde_json::from_str::<PersistedRefreshWrite>(&response_json)?;
            return Ok(PipelineRefreshOutcome {
                result: persisted.outcome,
                idempotent_replay: true,
            });
        }
    }

    let upstream = build_upstream_snapshot_from_connection(connection, network, chain_id)?;
    let assessment = assess_upstream(&upstream);
    if !assessment.consistent {
        return Err(CompositeError::UpstreamInconsistent {
            issues: assessment.issues,
        });
    }
    let now = chrono::Utc::now().to_rfc3339();
    let steps = requested_steps
        .iter()
        .enumerate()
        .map(|(index, step_name)| PipelineStepRow {
            run_id: run_id.to_owned(),
            step_index: index,
            step_id: format!("{run_id}:{index}:{}", step_name.as_str()),
            step_name: *step_name,
            depends_on: step_name
                .dependencies()
                .iter()
                .map(|value| (*value).to_owned())
                .collect(),
            status: PipelineStepStatus::Pending,
            attempt_count: 0,
            dependency_snapshot: None,
            result: empty_object(),
            last_error: None,
            started_at: None,
            last_attempted_at: None,
            completed_at: None,
            updated_at: now.clone(),
        })
        .collect::<Vec<_>>();
    let run = PipelineRunRow {
        run_id: run_id.to_owned(),
        status: PipelineRunStatus::Running,
        requested_steps: requested_steps.to_vec(),
        upstream_snapshot: upstream,
        upstream_fingerprint: assessment.fingerprint.clone(),
        current_step_index: 0,
        total_steps: steps.len(),
        last_completed_step_id: None,
        last_error: None,
        created_at: now.clone(),
        updated_at: now,
        completed_at: None,
    };

    if apply {
        insert_run(connection, network, chain_id, &run)?;
        for step in &steps {
            insert_step(connection, network, chain_id, step)?;
        }
        insert_event(
            connection,
            network,
            chain_id,
            run_id,
            None,
            "pipeline.created",
            "manager_api",
            request.request_id.as_str(),
            serde_json::json!({
                "steps": steps.iter().map(|step| step.step_name).collect::<Vec<_>>(),
                "upstreamFingerprint": assessment.fingerprint
            }),
        )?;
        let result = PipelineRefreshResult {
            run: run.clone(),
            steps: steps.clone(),
            upstream_assessment: assessment.clone(),
            apply,
            dry_run: !apply,
        };
        persist_refresh_write(connection, request, request_digest, &result)?;
        return Ok(PipelineRefreshOutcome {
            result,
            idempotent_replay: false,
        });
    }

    Ok(PipelineRefreshOutcome {
        result: PipelineRefreshResult {
            run,
            steps,
            upstream_assessment: assessment,
            apply,
            dry_run: !apply,
        },
        idempotent_replay: false,
    })
}

fn begin_step_on_connection(
    connection: &Connection,
    request: &PipelineBeginStepRequest,
    request_digest: &str,
    database_path: &Path,
    apply: bool,
) -> Result<PipelineStepOutcome, CompositeError> {
    if apply {
        if let Some(outcome) =
            replay_step_write::<PipelineBeginStepRequest>(connection, request, request_digest)?
        {
            return Ok(outcome);
        }
    }
    let run = read_run(
        connection,
        &request.network,
        request.chain_id,
        &request.run_id,
    )?;
    let mut steps = read_steps(
        connection,
        &request.network,
        request.chain_id,
        &request.run_id,
    )?;
    let position = step_position(&steps, &request.step_id)?;
    if steps[position].status.is_successful() {
        return Err(CompositeError::StepAlreadyFinal {
            step_id: request.step_id.clone(),
            status: steps[position].status.as_str().to_owned(),
        });
    }
    if let Some(blocker) = steps
        .iter()
        .take(position)
        .find(|step| !step.status.is_successful())
    {
        return Err(CompositeError::StepOutOfOrder {
            step_id: request.step_id.clone(),
            blocking_step_id: blocker.step_id.clone(),
            blocking_status: blocker.status.as_str().to_owned(),
        });
    }
    let upstream = build_upstream_snapshot(database_path, &request.network, request.chain_id)?;
    let assessment = assess_upstream(&upstream);
    if !assessment.consistent {
        return Err(CompositeError::UpstreamInconsistent {
            issues: assessment.issues,
        });
    }
    let dependencies = capture_step_dependencies(steps[position].step_name, &upstream)?;
    let now = chrono::Utc::now().to_rfc3339();
    let attempt_count = steps[position].attempt_count.saturating_add(1);
    let previous_status = steps[position].status;
    steps[position].status = PipelineStepStatus::Running;
    steps[position].attempt_count = attempt_count;
    steps[position].dependency_snapshot = Some(dependencies);
    steps[position].last_error = None;
    steps[position].started_at = Some(now.clone());
    steps[position].last_attempted_at = Some(now.clone());
    steps[position].completed_at = None;
    steps[position].updated_at = now.clone();
    let mut updated_run = run;
    updated_run.status = PipelineRunStatus::Running;
    updated_run.current_step_index = steps[position].step_index;
    updated_run.last_error = None;
    updated_run.completed_at = None;
    updated_run.updated_at = now;

    if apply {
        update_step(
            connection,
            &request.network,
            request.chain_id,
            &steps[position],
        )?;
        update_run(connection, &request.network, request.chain_id, &updated_run)?;
        insert_event(
            connection,
            &request.network,
            request.chain_id,
            &request.run_id,
            Some(request.step_id.as_str()),
            "pipeline.step_started",
            "manager_api",
            request.request_id.as_str(),
            serde_json::json!({
                "previousStatus": previous_status,
                "attemptCount": attempt_count,
                "dependencyFingerprint": steps[position]
                    .dependency_snapshot
                    .as_ref()
                    .map(|value| value.fingerprint.as_str())
            }),
        )?;
    }
    let result = PipelineStepResult {
        run: updated_run,
        step: steps[position].clone(),
        steps: steps.clone(),
        upstream_assessment: assessment,
        apply,
        dry_run: !apply,
    };
    if apply {
        persist_step_write(connection, request, request_digest, &result)?;
    }
    Ok(PipelineStepOutcome {
        result,
        idempotent_replay: false,
    })
}

fn complete_step_on_connection(
    connection: &Connection,
    request: &PipelineCompleteStepRequest,
    request_digest: &str,
    database_path: &Path,
    apply: bool,
) -> Result<PipelineStepOutcome, CompositeError> {
    if apply {
        if let Some(outcome) =
            replay_step_write::<PipelineCompleteStepRequest>(connection, request, request_digest)?
        {
            return Ok(outcome);
        }
    }
    let run = read_run(
        connection,
        &request.network,
        request.chain_id,
        &request.run_id,
    )?;
    let mut steps = read_steps(
        connection,
        &request.network,
        request.chain_id,
        &request.run_id,
    )?;
    let position = step_position(&steps, &request.step_id)?;
    if steps[position].status != PipelineStepStatus::Running {
        return Err(CompositeError::StepNotRunning {
            step_id: request.step_id.clone(),
            status: steps[position].status.as_str().to_owned(),
        });
    }
    let dependency_snapshot = steps[position]
        .dependency_snapshot
        .as_ref()
        .ok_or_else(|| CompositeError::MissingDependency("step dependency snapshot".to_owned()))?;
    let upstream = build_upstream_snapshot(database_path, &request.network, request.chain_id)?;
    let assessment = assess_upstream(&upstream);
    if !assessment.consistent {
        return Err(CompositeError::UpstreamInconsistent {
            issues: assessment.issues,
        });
    }
    let current_dependencies = capture_step_dependencies(steps[position].step_name, &upstream)?;
    if dependency_snapshot.fingerprint != current_dependencies.fingerprint {
        return Err(CompositeError::ExpectedDependencyMismatch {
            field: "dependencyFingerprint".to_owned(),
            expected: dependency_snapshot.fingerprint.clone(),
            actual: current_dependencies.fingerprint,
        });
    }
    if request.status == StepCompletionStatus::Completed {
        validate_step_output(steps[position].step_name, &request.step_id, &upstream)?;
    }
    if request.status == StepCompletionStatus::Failed && request.error.is_none() {
        return Err(CompositeError::InvalidRequest(
            "failed step completion requires error".to_owned(),
        ));
    }
    let now = chrono::Utc::now().to_rfc3339();
    steps[position].status = request.status.as_step_status();
    steps[position].result = normalize_object(&request.result);
    steps[position].last_error = request.error.clone();
    steps[position].updated_at = now.clone();
    if steps[position].status.is_successful()
        || steps[position].status == PipelineStepStatus::Failed
    {
        steps[position].completed_at = Some(now.clone());
    }
    let mut updated_run = run;
    updated_run.updated_at = now.clone();
    match steps[position].status {
        PipelineStepStatus::Failed => {
            updated_run.status = PipelineRunStatus::Failed;
            updated_run.last_error = request.error.clone();
            updated_run.current_step_index = steps[position].step_index;
        }
        PipelineStepStatus::Completed | PipelineStepStatus::Idle => {
            updated_run.last_completed_step_id = Some(steps[position].step_id.clone());
            updated_run.last_error = None;
            if let Some(next) = steps
                .iter()
                .find(|step| step.status == PipelineStepStatus::Pending)
            {
                updated_run.status = PipelineRunStatus::Running;
                updated_run.current_step_index = next.step_index;
                updated_run.completed_at = None;
            } else {
                updated_run.status = PipelineRunStatus::Completed;
                updated_run.current_step_index = steps.len();
                updated_run.completed_at = Some(now);
            }
        }
        PipelineStepStatus::Pending | PipelineStepStatus::Running => unreachable!(),
    }

    if apply {
        update_step(
            connection,
            &request.network,
            request.chain_id,
            &steps[position],
        )?;
        update_run(connection, &request.network, request.chain_id, &updated_run)?;
        insert_event(
            connection,
            &request.network,
            request.chain_id,
            &request.run_id,
            Some(request.step_id.as_str()),
            match request.status {
                StepCompletionStatus::Completed => "pipeline.step_completed",
                StepCompletionStatus::Failed => "pipeline.step_failed",
                StepCompletionStatus::Idle => "pipeline.step_idle",
            },
            "manager_api",
            request.request_id.as_str(),
            serde_json::json!({
                "attemptCount": steps[position].attempt_count,
                "result": steps[position].result,
                "error": steps[position].last_error
            }),
        )?;
    }
    let result = PipelineStepResult {
        run: updated_run,
        step: steps[position].clone(),
        steps,
        upstream_assessment: assessment,
        apply,
        dry_run: !apply,
    };
    if apply {
        persist_step_write(connection, request, request_digest, &result)?;
    }
    Ok(PipelineStepOutcome {
        result,
        idempotent_replay: false,
    })
}

trait StepWriteIdentity {
    fn network(&self) -> &str;
    fn chain_id(&self) -> i64;
    fn operation(&self) -> &str;
    fn idempotency_key(&self) -> &str;
    fn request_id(&self) -> &str;
}

impl StepWriteIdentity for PipelineBeginStepRequest {
    fn network(&self) -> &str {
        &self.network
    }
    fn chain_id(&self) -> i64 {
        self.chain_id
    }
    fn operation(&self) -> &str {
        &self.operation
    }
    fn idempotency_key(&self) -> &str {
        &self.idempotency_key
    }
    fn request_id(&self) -> &str {
        &self.request_id
    }
}

impl StepWriteIdentity for PipelineCompleteStepRequest {
    fn network(&self) -> &str {
        &self.network
    }
    fn chain_id(&self) -> i64 {
        self.chain_id
    }
    fn operation(&self) -> &str {
        &self.operation
    }
    fn idempotency_key(&self) -> &str {
        &self.idempotency_key
    }
    fn request_id(&self) -> &str {
        &self.request_id
    }
}

fn replay_step_write<T: StepWriteIdentity>(
    connection: &Connection,
    request: &T,
    request_digest: &str,
) -> Result<Option<PipelineStepOutcome>, CompositeError> {
    let Some((existing_request_id, existing_digest, response_json)) = find_write_request(
        connection,
        request.network(),
        request.chain_id(),
        request.operation(),
        request.idempotency_key(),
    )?
    else {
        return Ok(None);
    };
    if existing_digest != request_digest {
        return Err(CompositeError::IdempotencyKeyReused {
            idempotency_key: request.idempotency_key().to_owned(),
            existing_request_id,
            submitted_request_id: request.request_id().to_owned(),
        });
    }
    let persisted = serde_json::from_str::<PersistedStepWrite>(&response_json)?;
    Ok(Some(PipelineStepOutcome {
        result: persisted.outcome,
        idempotent_replay: true,
    }))
}

fn persist_refresh_write(
    connection: &Connection,
    request: &PipelineRefreshRequest,
    request_digest: &str,
    result: &PipelineRefreshResult,
) -> Result<(), CompositeError> {
    let response_json = serde_json::to_string(&PersistedRefreshWrite {
        request_id: request.request_id.clone(),
        request_digest: request_digest.to_owned(),
        outcome: result.clone(),
    })?;
    let request_json = serde_json::json!({
        "chainId": request.chain_id,
        "requestedSteps": request.requested_steps,
        "apply": request.apply
    });
    insert_write_request(
        connection,
        &request.network,
        request.chain_id,
        &request.operation,
        &request.idempotency_key,
        &request.request_id,
        request_digest,
        &request_json.to_string(),
        &response_json,
    )
}

fn persist_step_write<T: StepWriteIdentity>(
    connection: &Connection,
    request: &T,
    request_digest: &str,
    result: &PipelineStepResult,
) -> Result<(), CompositeError> {
    let response_json = serde_json::to_string(&PersistedStepWrite {
        request_id: request.request_id().to_owned(),
        request_digest: request_digest.to_owned(),
        outcome: result.clone(),
    })?;
    let request_json = serde_json::to_string(&result.step.result)?;
    insert_write_request(
        connection,
        request.network(),
        request.chain_id(),
        request.operation(),
        request.idempotency_key(),
        request.request_id(),
        request_digest,
        &request_json,
        &response_json,
    )
}

#[allow(clippy::too_many_arguments)]
fn insert_write_request(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    operation: &str,
    idempotency_key: &str,
    request_id: &str,
    request_digest: &str,
    request_json: &str,
    response_json: &str,
) -> Result<(), CompositeError> {
    connection.execute(
        "INSERT INTO manager_write_requests (
             network, chain_id, operation, idempotency_key, request_id,
             request_digest, request_json, response_json, created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            network,
            chain_id,
            operation,
            idempotency_key,
            request_id,
            request_digest,
            request_json,
            response_json,
            chrono::Utc::now().to_rfc3339()
        ],
    )?;
    Ok(())
}

fn find_write_request(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    operation: &str,
    idempotency_key: &str,
) -> Result<Option<(String, String, String)>, CompositeError> {
    Ok(connection
        .query_row(
            "SELECT request_id, request_digest, response_json
             FROM manager_write_requests
             WHERE network = ?1 AND chain_id = ?2 AND operation = ?3
               AND idempotency_key = ?4",
            params![network, chain_id, operation, idempotency_key],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .optional()?)
}

fn build_upstream_snapshot(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<UpstreamSnapshot, CompositeError> {
    let connection = open_connection(database_path, false)?;
    build_upstream_snapshot_from_connection(&connection, network, chain_id)
}

fn build_upstream_snapshot_from_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<UpstreamSnapshot, CompositeError> {
    let database_path = connection.path().ok_or_else(|| {
        CompositeError::InvalidRequest("database connection path is unavailable".to_owned())
    })?;
    let database_path = Path::new(database_path);
    build_upstream_snapshot_from_path(database_path, network, chain_id)
}

fn build_upstream_snapshot_from_path(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<UpstreamSnapshot, CompositeError> {
    let mut token_libraries = BTreeMap::new();
    for library in LIBRARY_KEYS {
        let reference = match read_library_state(database_path, network, chain_id as u64, library) {
            Ok(state) => token_library_ref(state),
            Err(TokenQueryError::LibraryStateMissing { .. }) => TokenLibraryRef {
                enabled: false,
                version: 0,
                checksum: String::new(),
                token_count: 0,
                admitted_count: 0,
            },
            Err(error) => {
                return Err(CompositeError::UpstreamRead {
                    manager: "token_manager",
                    message: error.to_string(),
                });
            }
        };
        token_libraries.insert(library.as_str().to_owned(), reference);
    }
    let pool_scope = read_scope_status(database_path, network, chain_id, SCOPE_KEY)
        .map(pool_scope_ref)
        .map_err(|error| CompositeError::UpstreamRead {
            manager: "pool_manager",
            message: error.to_string(),
        })?;
    let candidate_edge = read_candidate_status(database_path, network, chain_id)
        .map(candidate_edge_ref)
        .map_err(|error| CompositeError::UpstreamRead {
            manager: "candidate_edge_pool_manager",
            message: error.to_string(),
        })?;
    let candidate_exchange = read_candidate_exchange_status(database_path, network, chain_id)
        .map(candidate_exchange_ref)
        .map_err(|error| CompositeError::UpstreamRead {
            manager: "candidate_exchange_pool_manager",
            message: error.to_string(),
        })?;
    let closed_loop = read_closed_loop_status(database_path, network, chain_id)
        .map(closed_loop_ref)
        .map_err(|error| CompositeError::UpstreamRead {
            manager: "closed_loop_manager",
            message: error.to_string(),
        })?;
    let review = read_review_status(database_path, network, chain_id)
        .map(review_ref)
        .map_err(|error| CompositeError::UpstreamRead {
            manager: "review_manager",
            message: error.to_string(),
        })?;
    let broadcast = read_broadcast_status(database_path, network, chain_id)
        .map(broadcast_ref)
        .map_err(|error| CompositeError::UpstreamRead {
            manager: "broadcast_manager",
            message: error.to_string(),
        })?;
    Ok(UpstreamSnapshot {
        token_libraries,
        pool_scope,
        candidate_edge,
        candidate_exchange,
        closed_loop,
        review,
        broadcast,
    })
}

fn token_library_ref(state: TokenLibraryState) -> TokenLibraryRef {
    TokenLibraryRef {
        enabled: true,
        version: state.library_version,
        checksum: state.metadata_checksum,
        token_count: usize_from_i64(state.token_count),
        admitted_count: usize_from_i64(state.admitted_count),
    }
}

fn pool_scope_ref(status: ScopeStatus) -> PoolScopeRef {
    PoolScopeRef {
        enabled: status.enabled,
        materialized_checksum: status.materialized_checksum,
        live_checksum: status.live_checksum,
        library_versions: status.library_versions,
        library_checksums: status.library_checksums,
        active_pool_count: status.active_pool_count,
    }
}

fn candidate_edge_ref(status: CandidateEdgeStatus) -> CandidateEdgeRef {
    CandidateEdgeRef {
        enabled: status.enabled,
        version: status.candidate_version,
        checksum: status.materialized_checksum,
        pool_scope_checksum: status.pool_scope_checksum,
        library_versions: status.library_versions,
        library_checksums: status.library_checksums,
        anchor_count: status.anchor_token_count,
        pool_count: status.eligible_pool_count,
    }
}

fn candidate_exchange_ref(status: CandidateExchangeStatus) -> CandidateExchangeRef {
    CandidateExchangeRef {
        enabled: status.enabled,
        version: status.observation_version,
        checksum: status.materialized_checksum,
        candidate_edge_version: status.candidate_edge_version,
        candidate_edge_checksum: status.candidate_edge_checksum,
        pool_scope_checksum: status.pool_scope_checksum,
        library_versions: status.library_versions,
        library_checksums: status.library_checksums,
        direction_count: status.direction_count,
        pool_count: status.pool_count,
    }
}

fn closed_loop_ref(status: ClosedLoopStatus) -> ClosedLoopRef {
    ClosedLoopRef {
        enabled: status.enabled,
        version: status.closed_loop_version,
        checksum: status.materialized_checksum,
        candidate_edge_version: status.candidate_edge_version,
        candidate_edge_checksum: status.candidate_edge_checksum,
        observation_version: status.observation_version,
        observation_checksum: status.observation_checksum,
        pool_scope_checksum: status.pool_scope_checksum,
        loop_count: status.loop_count,
        consumers_need_rebuild: status.consumers_need_rebuild,
    }
}

fn review_ref(status: ReviewStatus) -> ReviewRef {
    ReviewRef {
        enabled: status.enabled,
        version: status.review_version,
        checksum: status.materialized_checksum,
        closed_loop_version: status.closed_loop_version,
        closed_loop_checksum: status.closed_loop_checksum,
        completed_count: status.completed_count,
        data_incomplete_count: status.data_incomplete_count,
        broadcast_request_count: status.broadcast_request_count,
    }
}

fn broadcast_ref(status: BroadcastStatus) -> BroadcastRef {
    BroadcastRef {
        enabled: status.enabled,
        version: status.broadcast_version,
        checksum: status.materialized_checksum,
        review_version: status.review_version,
        review_checksum: status.review_checksum,
        closed_loop_version: status.closed_loop_version,
        closed_loop_checksum: status.closed_loop_checksum,
        pending_count: status.pending_count,
        success_count: status.success_count,
        failed_count: status.failed_count,
        terminal_count: status.terminal_count,
    }
}

fn assess_upstream(snapshot: &UpstreamSnapshot) -> UpstreamAssessment {
    let mut issues = Vec::new();
    check_enabled_libraries(snapshot, &mut issues);
    if snapshot.pool_scope.enabled {
        check_library_map(
            "pool_scope",
            &snapshot.pool_scope.library_versions,
            &snapshot.pool_scope.library_checksums,
            snapshot,
            &mut issues,
        );
    }
    let scope_checksum = snapshot.pool_scope.effective_checksum();
    if snapshot.candidate_edge.enabled {
        check_option_value(
            "candidate_edge",
            "poolScopeChecksum",
            scope_checksum,
            snapshot.candidate_edge.pool_scope_checksum.as_deref(),
            &mut issues,
        );
        check_library_map(
            "candidate_edge",
            &snapshot.candidate_edge.library_versions,
            &snapshot.candidate_edge.library_checksums,
            snapshot,
            &mut issues,
        );
    }
    if snapshot.candidate_exchange.enabled {
        check_option_value(
            "candidate_exchange",
            "candidateEdgeVersion",
            snapshot
                .candidate_edge
                .enabled
                .then_some(snapshot.candidate_edge.version),
            snapshot.candidate_exchange.candidate_edge_version,
            &mut issues,
        );
        check_option_value(
            "candidate_exchange",
            "candidateEdgeChecksum",
            snapshot.candidate_edge.checksum.as_deref(),
            snapshot
                .candidate_exchange
                .candidate_edge_checksum
                .as_deref(),
            &mut issues,
        );
        check_option_value(
            "candidate_exchange",
            "poolScopeChecksum",
            scope_checksum,
            snapshot.candidate_exchange.pool_scope_checksum.as_deref(),
            &mut issues,
        );
        check_library_map(
            "candidate_exchange",
            &snapshot.candidate_exchange.library_versions,
            &snapshot.candidate_exchange.library_checksums,
            snapshot,
            &mut issues,
        );
    }
    if snapshot.closed_loop.enabled {
        check_option_value(
            "closed_loop",
            "candidateEdgeVersion",
            snapshot
                .candidate_edge
                .enabled
                .then_some(snapshot.candidate_edge.version),
            snapshot.closed_loop.candidate_edge_version,
            &mut issues,
        );
        check_option_value(
            "closed_loop",
            "candidateEdgeChecksum",
            snapshot.candidate_edge.checksum.as_deref(),
            snapshot.closed_loop.candidate_edge_checksum.as_deref(),
            &mut issues,
        );
        check_option_value(
            "closed_loop",
            "observationVersion",
            snapshot
                .candidate_exchange
                .enabled
                .then_some(snapshot.candidate_exchange.version),
            snapshot.closed_loop.observation_version,
            &mut issues,
        );
        check_option_value(
            "closed_loop",
            "observationChecksum",
            snapshot.candidate_exchange.checksum.as_deref(),
            snapshot.closed_loop.observation_checksum.as_deref(),
            &mut issues,
        );
        check_option_value(
            "closed_loop",
            "poolScopeChecksum",
            scope_checksum,
            snapshot.closed_loop.pool_scope_checksum.as_deref(),
            &mut issues,
        );
        if snapshot.closed_loop.consumers_need_rebuild {
            issues.push(ConsistencyIssue {
                component: "closed_loop".to_owned(),
                field: "consumersNeedRebuild".to_owned(),
                expected: "false".to_owned(),
                actual: "true".to_owned(),
            });
        }
    }
    if snapshot.review.enabled {
        check_option_value(
            "review",
            "closedLoopVersion",
            snapshot
                .closed_loop
                .enabled
                .then_some(snapshot.closed_loop.version),
            snapshot.review.closed_loop_version,
            &mut issues,
        );
        check_option_value(
            "review",
            "closedLoopChecksum",
            snapshot.closed_loop.checksum.as_deref(),
            snapshot.review.closed_loop_checksum.as_deref(),
            &mut issues,
        );
    }
    if snapshot.broadcast.enabled {
        check_option_value(
            "broadcast",
            "reviewVersion",
            snapshot.review.enabled.then_some(snapshot.review.version),
            snapshot.broadcast.review_version,
            &mut issues,
        );
        check_option_value(
            "broadcast",
            "reviewChecksum",
            snapshot.review.checksum.as_deref(),
            snapshot.broadcast.review_checksum.as_deref(),
            &mut issues,
        );
        check_option_value(
            "broadcast",
            "closedLoopVersion",
            snapshot
                .closed_loop
                .enabled
                .then_some(snapshot.closed_loop.version),
            snapshot.broadcast.closed_loop_version,
            &mut issues,
        );
        check_option_value(
            "broadcast",
            "closedLoopChecksum",
            snapshot.closed_loop.checksum.as_deref(),
            snapshot.broadcast.closed_loop_checksum.as_deref(),
            &mut issues,
        );
    }
    let fingerprint = snapshot_fingerprint(snapshot);
    UpstreamAssessment {
        fingerprint,
        consistent: issues.is_empty(),
        issues,
    }
}

fn check_enabled_libraries(snapshot: &UpstreamSnapshot, issues: &mut Vec<ConsistencyIssue>) {
    for library in LIBRARY_KEYS {
        let key = library.as_str();
        if snapshot
            .token_libraries
            .get(key)
            .is_none_or(|reference| !reference.enabled)
        {
            issues.push(ConsistencyIssue {
                component: "token_libraries".to_owned(),
                field: format!("{key}.enabled"),
                expected: "true".to_owned(),
                actual: "false".to_owned(),
            });
        }
    }
}

fn check_library_map(
    component: &str,
    versions: &BTreeMap<String, i64>,
    checksums: &BTreeMap<String, String>,
    snapshot: &UpstreamSnapshot,
    issues: &mut Vec<ConsistencyIssue>,
) {
    for (key, version) in versions {
        if let Some(current) = snapshot.token_libraries.get(key) {
            if current.version != *version {
                issues.push(ConsistencyIssue {
                    component: component.to_owned(),
                    field: format!("libraryVersions.{key}"),
                    expected: current.version.to_string(),
                    actual: version.to_string(),
                });
            }
        }
    }
    for (key, checksum) in checksums {
        if let Some(current) = snapshot.token_libraries.get(key) {
            if current.checksum != *checksum {
                issues.push(ConsistencyIssue {
                    component: component.to_owned(),
                    field: format!("libraryChecksums.{key}"),
                    expected: current.checksum.clone(),
                    actual: checksum.clone(),
                });
            }
        }
    }
}

fn check_option_value<T: PartialEq + ToString>(
    component: &str,
    field: &str,
    expected: Option<T>,
    actual: Option<T>,
    issues: &mut Vec<ConsistencyIssue>,
) {
    if expected != actual {
        issues.push(ConsistencyIssue {
            component: component.to_owned(),
            field: field.to_owned(),
            expected: expected.map_or_else(|| "<none>".to_owned(), |value| value.to_string()),
            actual: actual.map_or_else(|| "<none>".to_owned(), |value| value.to_string()),
        });
    }
}

fn capture_step_dependencies(
    step_name: PipelineStepName,
    snapshot: &UpstreamSnapshot,
) -> Result<StepDependencySnapshot, CompositeError> {
    let dependencies = step_name.dependencies();
    if dependencies.contains(&"token_libraries") {
        for library in LIBRARY_KEYS {
            if snapshot
                .token_libraries
                .get(library.as_str())
                .is_none_or(|reference| !reference.enabled)
            {
                return Err(CompositeError::MissingDependency(format!(
                    "token library {}",
                    library.as_str()
                )));
            }
        }
    }
    let pool_scope = include_pool_scope(dependencies, snapshot)?;
    let candidate_edge = include_candidate_edge(dependencies, snapshot)?;
    let candidate_exchange = include_candidate_exchange(dependencies, snapshot)?;
    let closed_loop = include_closed_loop(dependencies, snapshot)?;
    let review = include_review(dependencies, snapshot)?;
    let mut result = StepDependencySnapshot {
        token_libraries: if dependencies.contains(&"token_libraries") {
            snapshot.token_libraries.clone()
        } else {
            BTreeMap::new()
        },
        pool_scope,
        candidate_edge,
        candidate_exchange,
        closed_loop,
        review,
        fingerprint: String::new(),
    };
    result.fingerprint = checksum_json(&result)?;
    Ok(result)
}

fn include_pool_scope(
    dependencies: &[&str],
    snapshot: &UpstreamSnapshot,
) -> Result<Option<PoolScopeRef>, CompositeError> {
    if !dependencies.contains(&"pool_scope") {
        return Ok(None);
    }
    if !snapshot.pool_scope.enabled || snapshot.pool_scope.effective_checksum().is_none() {
        return Err(CompositeError::MissingDependency(
            "pool scope core4".to_owned(),
        ));
    }
    Ok(Some(snapshot.pool_scope.clone()))
}

fn include_candidate_edge(
    dependencies: &[&str],
    snapshot: &UpstreamSnapshot,
) -> Result<Option<CandidateEdgeRef>, CompositeError> {
    if !dependencies.contains(&"candidate_edge") {
        return Ok(None);
    }
    if !snapshot.candidate_edge.enabled || snapshot.candidate_edge.checksum.is_none() {
        return Err(CompositeError::MissingDependency(
            "candidate edge library".to_owned(),
        ));
    }
    Ok(Some(snapshot.candidate_edge.clone()))
}

fn include_candidate_exchange(
    dependencies: &[&str],
    snapshot: &UpstreamSnapshot,
) -> Result<Option<CandidateExchangeRef>, CompositeError> {
    if !dependencies.contains(&"candidate_exchange") {
        return Ok(None);
    }
    if !snapshot.candidate_exchange.enabled || snapshot.candidate_exchange.checksum.is_none() {
        return Err(CompositeError::MissingDependency(
            "candidate exchange library".to_owned(),
        ));
    }
    Ok(Some(snapshot.candidate_exchange.clone()))
}

fn include_closed_loop(
    dependencies: &[&str],
    snapshot: &UpstreamSnapshot,
) -> Result<Option<ClosedLoopRef>, CompositeError> {
    if !dependencies.contains(&"closed_loop") {
        return Ok(None);
    }
    if !snapshot.closed_loop.enabled || snapshot.closed_loop.checksum.is_none() {
        return Err(CompositeError::MissingDependency(
            "closed-loop library".to_owned(),
        ));
    }
    Ok(Some(snapshot.closed_loop.clone()))
}

fn include_review(
    dependencies: &[&str],
    snapshot: &UpstreamSnapshot,
) -> Result<Option<ReviewRef>, CompositeError> {
    if !dependencies.contains(&"review") {
        return Ok(None);
    }
    if !snapshot.review.enabled || snapshot.review.checksum.is_none() {
        return Err(CompositeError::MissingDependency(
            "review library".to_owned(),
        ));
    }
    Ok(Some(snapshot.review.clone()))
}

fn validate_step_output(
    step_name: PipelineStepName,
    step_id: &str,
    snapshot: &UpstreamSnapshot,
) -> Result<(), CompositeError> {
    match step_name {
        PipelineStepName::PoolScope => {
            require_output(
                step_id,
                "poolScope.enabled",
                true,
                snapshot.pool_scope.enabled,
            )?;
            require_output(
                step_id,
                "poolScope.materializedChecksum",
                true,
                snapshot.pool_scope.effective_checksum().is_some(),
            )
        }
        PipelineStepName::CandidateEdge => {
            require_output(
                step_id,
                "candidateEdge.enabled",
                true,
                snapshot.candidate_edge.enabled,
            )?;
            require_output(
                step_id,
                "candidateEdge.checksum",
                true,
                snapshot.candidate_edge.checksum.is_some(),
            )
        }
        PipelineStepName::CandidateExchange => {
            require_output(
                step_id,
                "candidateExchange.enabled",
                true,
                snapshot.candidate_exchange.enabled,
            )?;
            require_output(
                step_id,
                "candidateExchange.checksum",
                true,
                snapshot.candidate_exchange.checksum.is_some(),
            )
        }
        PipelineStepName::ClosedLoop => {
            require_output(
                step_id,
                "closedLoop.enabled",
                true,
                snapshot.closed_loop.enabled,
            )?;
            require_output(
                step_id,
                "closedLoop.checksum",
                true,
                snapshot.closed_loop.checksum.is_some(),
            )
        }
        PipelineStepName::Review => {
            require_output(step_id, "review.enabled", true, snapshot.review.enabled)?;
            require_output(
                step_id,
                "review.checksum",
                true,
                snapshot.review.checksum.is_some(),
            )
        }
        PipelineStepName::Broadcast => {
            require_output(
                step_id,
                "broadcast.enabled",
                true,
                snapshot.broadcast.enabled,
            )?;
            require_output(
                step_id,
                "broadcast.checksum",
                true,
                snapshot.broadcast.checksum.is_some(),
            )?;
            if snapshot.broadcast.pending_count > 0 {
                return Err(CompositeError::PendingBroadcastAttempts(
                    snapshot.broadcast.pending_count,
                ));
            }
            Ok(())
        }
    }
}

fn require_output(
    step_id: &str,
    field: &str,
    expected: bool,
    actual: bool,
) -> Result<(), CompositeError> {
    if expected == actual {
        return Ok(());
    }
    Err(CompositeError::OutputNotVerified {
        step_id: step_id.to_owned(),
        field: field.to_owned(),
        expected: expected.to_string(),
        actual: actual.to_string(),
    })
}

fn insert_run(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    run: &PipelineRunRow,
) -> Result<(), CompositeError> {
    connection.execute(
        "INSERT INTO manager_pipeline_runs (
             network, chain_id, protocol, pipeline_key, run_id, status,
             requested_steps_json, upstream_snapshot_json,
             upstream_fingerprint, current_step_index, total_steps,
             last_completed_step_id, last_error, created_at, updated_at,
             completed_at
         ) VALUES (
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14,
             ?15, ?16
         )",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            PIPELINE_KEY,
            run.run_id,
            run.status.as_str(),
            serde_json::to_string(&run.requested_steps)?,
            serde_json::to_string(&run.upstream_snapshot)?,
            run.upstream_fingerprint,
            to_i64(run.current_step_index),
            to_i64(run.total_steps),
            run.last_completed_step_id,
            run.last_error,
            run.created_at,
            run.updated_at,
            run.completed_at
        ],
    )?;
    Ok(())
}

fn update_run(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    run: &PipelineRunRow,
) -> Result<(), CompositeError> {
    connection.execute(
        "UPDATE manager_pipeline_runs
         SET status = ?5, current_step_index = ?6, last_completed_step_id = ?7,
             last_error = ?8, updated_at = ?9, completed_at = ?10
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pipeline_key = ?4 AND run_id = ?11",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            PIPELINE_KEY,
            run.status.as_str(),
            to_i64(run.current_step_index),
            run.last_completed_step_id,
            run.last_error,
            run.updated_at,
            run.completed_at,
            run.run_id
        ],
    )?;
    Ok(())
}

fn insert_step(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    step: &PipelineStepRow,
) -> Result<(), CompositeError> {
    connection.execute(
        "INSERT INTO manager_pipeline_steps (
             network, chain_id, protocol, pipeline_key, run_id, step_index,
             step_id, step_name, depends_on_json, status, attempt_count,
             dependency_snapshot_json, result_json, last_error, started_at,
             last_attempted_at, completed_at, updated_at
         ) VALUES (
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14,
             ?15, ?16, ?17, ?18
         )",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            PIPELINE_KEY,
            step.run_id,
            to_i64(step.step_index),
            step.step_id,
            step.step_name.as_str(),
            serde_json::to_string(&step.depends_on)?,
            step.status.as_str(),
            to_i64(step.attempt_count),
            step.dependency_snapshot
                .as_ref()
                .map(serde_json::to_string)
                .transpose()?,
            serde_json::to_string(&step.result)?,
            step.last_error,
            step.started_at,
            step.last_attempted_at,
            step.completed_at,
            step.updated_at
        ],
    )?;
    Ok(())
}

fn update_step(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    step: &PipelineStepRow,
) -> Result<(), CompositeError> {
    connection.execute(
        "UPDATE manager_pipeline_steps
         SET status = ?6, attempt_count = ?7, dependency_snapshot_json = ?8,
             result_json = ?9, last_error = ?10, started_at = ?11,
             last_attempted_at = ?12, completed_at = ?13, updated_at = ?14
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pipeline_key = ?4 AND run_id = ?5 AND step_id = ?15",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            PIPELINE_KEY,
            step.run_id,
            step.status.as_str(),
            to_i64(step.attempt_count),
            step.dependency_snapshot
                .as_ref()
                .map(serde_json::to_string)
                .transpose()?,
            serde_json::to_string(&step.result)?,
            step.last_error,
            step.started_at,
            step.last_attempted_at,
            step.completed_at,
            step.updated_at,
            step.step_id
        ],
    )?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn insert_event(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    run_id: &str,
    step_id: Option<&str>,
    event_type: &str,
    actor: &str,
    request_id: &str,
    payload: serde_json::Value,
) -> Result<(), CompositeError> {
    let created_at = chrono::Utc::now().to_rfc3339();
    let event_id = event_id(run_id, step_id, event_type, request_id, &payload)?;
    let payload_json = serde_json::to_string(&payload)?;
    connection.execute(
        "INSERT INTO manager_pipeline_events (
             network, chain_id, protocol, pipeline_key, event_id, run_id,
             step_id, event_type, actor, request_id, payload_json, created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            PIPELINE_KEY,
            event_id,
            run_id,
            step_id,
            event_type,
            actor,
            request_id,
            payload_json,
            created_at
        ],
    )?;
    connection.execute(
        "INSERT INTO manager_events (
             network, chain_id, event_id, source_manager, event_type,
             aggregate_type, aggregate_id, actor, request_id, payload_json,
             created_at
         ) VALUES (?1, ?2, ?3, 'composite_manager', ?4, 'pipeline_run', ?5,
                   ?6, ?7, ?8, ?9)",
        params![
            network,
            chain_id,
            event_id,
            event_type,
            run_id,
            actor,
            request_id,
            payload_json,
            created_at
        ],
    )?;
    Ok(())
}

fn event_id(
    run_id: &str,
    step_id: Option<&str>,
    event_type: &str,
    request_id: &str,
    payload: &serde_json::Value,
) -> Result<String, CompositeError> {
    let digest = checksum_json(&serde_json::json!({
        "runId": run_id,
        "stepId": step_id,
        "eventType": event_type,
        "requestId": request_id,
        "payload": payload
    }))?;
    Ok(format!("pipeline-event:{}", &digest["sha256:".len()..]))
}

fn read_latest_run(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Option<PipelineRunRow>, CompositeError> {
    let run_id = connection
        .query_row(
            "SELECT run_id
             FROM manager_pipeline_runs
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND pipeline_key = ?4
             ORDER BY created_at DESC, run_id DESC
             LIMIT 1",
            params![network, chain_id, NETWORK_PROTOCOL, PIPELINE_KEY],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    run_id
        .map(|value| read_run(connection, network, chain_id, &value))
        .transpose()
}

fn read_run(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    run_id: &str,
) -> Result<PipelineRunRow, CompositeError> {
    connection
        .query_row(
            "SELECT status, requested_steps_json, upstream_snapshot_json,
                    upstream_fingerprint, current_step_index, total_steps,
                    last_completed_step_id, last_error, created_at, updated_at,
                    completed_at
             FROM manager_pipeline_runs
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND pipeline_key = ?4 AND run_id = ?5",
            params![network, chain_id, NETWORK_PROTOCOL, PIPELINE_KEY, run_id],
            |row| {
                let status: String = row.get(0)?;
                let requested_steps_json: String = row.get(1)?;
                let upstream_snapshot_json: String = row.get(2)?;
                Ok((
                    status,
                    requested_steps_json,
                    upstream_snapshot_json,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, Option<String>>(6)?,
                    row.get::<_, Option<String>>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, String>(9)?,
                    row.get::<_, Option<String>>(10)?,
                ))
            },
        )
        .optional()?
        .map(
            |(
                status,
                requested_steps_json,
                upstream_snapshot_json,
                upstream_fingerprint,
                current_step_index,
                total_steps,
                last_completed_step_id,
                last_error,
                created_at,
                updated_at,
                completed_at,
            )|
             -> Result<PipelineRunRow, rusqlite::Error> {
                let status = PipelineRunStatus::from_storage(&status).ok_or_else(|| {
                    rusqlite::Error::InvalidColumnType(
                        0,
                        "status".to_owned(),
                        rusqlite::types::Type::Text,
                    )
                })?;
                let requested_steps =
                    serde_json::from_str::<Vec<PipelineStepName>>(&requested_steps_json)
                        .map_err(json_to_sql_error)?;
                let upstream_snapshot =
                    serde_json::from_str::<UpstreamSnapshot>(&upstream_snapshot_json)
                        .map_err(json_to_sql_error)?;
                Ok(PipelineRunRow {
                    run_id: run_id.to_owned(),
                    status,
                    requested_steps,
                    upstream_snapshot,
                    upstream_fingerprint,
                    current_step_index: usize_from_i64(current_step_index),
                    total_steps: usize_from_i64(total_steps),
                    last_completed_step_id,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at,
                })
            },
        )
        .transpose()?
        .ok_or_else(|| CompositeError::RunNotFound(run_id.to_owned()))
}

fn read_steps(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    run_id: &str,
) -> Result<Vec<PipelineStepRow>, CompositeError> {
    let mut statement = connection.prepare(
        "SELECT step_index, step_id, step_name, depends_on_json, status,
                attempt_count, dependency_snapshot_json, result_json,
                last_error, started_at, last_attempted_at, completed_at,
                updated_at
         FROM manager_pipeline_steps
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pipeline_key = ?4 AND run_id = ?5
         ORDER BY step_index",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, PIPELINE_KEY, run_id],
        map_step_row,
    )?;
    let mut steps = rows.collect::<Result<Vec<_>, _>>()?;
    for step in &mut steps {
        step.run_id = run_id.to_owned();
    }
    Ok(steps)
}

fn map_step_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<PipelineStepRow> {
    let name: String = row.get(2)?;
    let status: String = row.get(4)?;
    let depends_on_json: String = row.get(3)?;
    let dependency_snapshot_json: Option<String> = row.get(6)?;
    let result_json: String = row.get(7)?;
    Ok(PipelineStepRow {
        run_id: String::new(),
        step_index: usize_from_i64(row.get(0)?),
        step_id: row.get(1)?,
        step_name: PipelineStepName::from_storage(&name).ok_or_else(|| {
            rusqlite::Error::InvalidColumnType(
                2,
                "step_name".to_owned(),
                rusqlite::types::Type::Text,
            )
        })?,
        depends_on: serde_json::from_str(&depends_on_json).map_err(json_to_sql_error)?,
        status: PipelineStepStatus::from_storage(&status).ok_or_else(|| {
            rusqlite::Error::InvalidColumnType(4, "status".to_owned(), rusqlite::types::Type::Text)
        })?,
        attempt_count: usize_from_i64(row.get(5)?),
        dependency_snapshot: dependency_snapshot_json
            .map(|value| serde_json::from_str(&value).map_err(json_to_sql_error))
            .transpose()?,
        result: serde_json::from_str(&result_json).map_err(json_to_sql_error)?,
        last_error: row.get(8)?,
        started_at: row.get(9)?,
        last_attempted_at: row.get(10)?,
        completed_at: row.get(11)?,
        updated_at: row.get(12)?,
    })
}

fn read_events(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    run_id: &str,
    limit: usize,
) -> Result<Vec<PipelineEventRow>, CompositeError> {
    let mut statement = connection.prepare(
        "SELECT event_id, run_id, step_id, event_type, actor, request_id,
                payload_json, created_at
         FROM manager_pipeline_events
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pipeline_key = ?4 AND run_id = ?5
         ORDER BY created_at, event_id
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            PIPELINE_KEY,
            run_id,
            to_i64(limit)
        ],
        |row| {
            let payload_json: String = row.get(6)?;
            Ok(PipelineEventRow {
                event_id: row.get(0)?,
                run_id: row.get(1)?,
                step_id: row.get(2)?,
                event_type: row.get(3)?,
                actor: row.get(4)?,
                request_id: row.get(5)?,
                payload: serde_json::from_str(&payload_json).map_err(json_to_sql_error)?,
                created_at: row.get(7)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn step_position(steps: &[PipelineStepRow], step_id: &str) -> Result<usize, CompositeError> {
    steps
        .iter()
        .position(|step| step.step_id == step_id)
        .ok_or_else(|| CompositeError::StepNotFound {
            run_id: steps
                .first()
                .map_or_else(String::new, |step| step.run_id.clone()),
            step_id: step_id.to_owned(),
        })
}

fn open_connection(database_path: &Path, writable: bool) -> Result<Connection, CompositeError> {
    let flags = if writable {
        OpenFlags::SQLITE_OPEN_READ_WRITE
    } else {
        OpenFlags::SQLITE_OPEN_READ_ONLY
    };
    Ok(Connection::open_with_flags(database_path, flags)?)
}

fn validate_identity(network: &str, chain_id: i64) -> Result<(), CompositeError> {
    if network.trim().is_empty() {
        return Err(CompositeError::InvalidRequest(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(CompositeError::InvalidRequest(
            "chainId must be greater than zero".to_owned(),
        ));
    }
    Ok(())
}

fn validate_identifier(field: &str, value: &str) -> Result<(), CompositeError> {
    if value.trim().is_empty() {
        return Err(CompositeError::InvalidRequest(format!(
            "{field} must not be empty"
        )));
    }
    if value.len() > MAX_IDENTIFIER_LENGTH {
        return Err(CompositeError::InvalidRequest(format!(
            "{field} must not exceed {MAX_IDENTIFIER_LENGTH} characters"
        )));
    }
    Ok(())
}

fn validate_refresh_request(request: &PipelineRefreshRequest) -> Result<(), CompositeError> {
    validate_identity(&request.network, request.chain_id)?;
    validate_identifier("operation", &request.operation)?;
    validate_identifier("requestId", &request.request_id)?;
    validate_identifier("idempotencyKey", &request.idempotency_key)?;
    if request.requested_steps.is_empty() {
        return Err(CompositeError::InvalidRequest(
            "requestedSteps must contain at least one step".to_owned(),
        ));
    }
    let mut unique = BTreeSet::new();
    let mut previous_order = None;
    for step in &request.requested_steps {
        if !unique.insert(*step) {
            return Err(CompositeError::InvalidRequest(format!(
                "requestedSteps contains duplicate {}",
                step.as_str()
            )));
        }
        if previous_order.is_some_and(|value| value >= step.order()) {
            return Err(CompositeError::InvalidRequest(
                "requestedSteps must follow the fixed pipeline order".to_owned(),
            ));
        }
        previous_order = Some(step.order());
    }
    Ok(())
}

fn validate_step_request_identity(
    request: &PipelineBeginStepRequest,
) -> Result<(), CompositeError> {
    validate_identity(&request.network, request.chain_id)?;
    validate_identifier("operation", &request.operation)?;
    validate_identifier("requestId", &request.request_id)?;
    validate_identifier("idempotencyKey", &request.idempotency_key)?;
    validate_identifier("runId", &request.run_id)?;
    validate_identifier("stepId", &request.step_id)
}

fn validate_complete_request(request: &PipelineCompleteStepRequest) -> Result<(), CompositeError> {
    validate_identity(&request.network, request.chain_id)?;
    validate_identifier("operation", &request.operation)?;
    validate_identifier("requestId", &request.request_id)?;
    validate_identifier("idempotencyKey", &request.idempotency_key)?;
    validate_identifier("runId", &request.run_id)?;
    validate_identifier("stepId", &request.step_id)?;
    if request
        .error
        .as_ref()
        .is_some_and(|value| value.len() > MAX_ERROR_LENGTH)
    {
        return Err(CompositeError::InvalidRequest(format!(
            "error must not exceed {MAX_ERROR_LENGTH} characters"
        )));
    }
    Ok(())
}

fn refresh_request_digest(request: &PipelineRefreshRequest) -> Result<String, CompositeError> {
    checksum_json(&serde_json::json!({
        "chainId": request.chain_id,
        "requestedSteps": request.requested_steps,
        "apply": request.apply
    }))
}

fn begin_request_digest(request: &PipelineBeginStepRequest) -> Result<String, CompositeError> {
    checksum_json(&serde_json::json!({
        "chainId": request.chain_id,
        "runId": request.run_id,
        "stepId": request.step_id,
        "apply": request.apply
    }))
}

fn complete_request_digest(
    request: &PipelineCompleteStepRequest,
) -> Result<String, CompositeError> {
    checksum_json(&serde_json::json!({
        "chainId": request.chain_id,
        "runId": request.run_id,
        "stepId": request.step_id,
        "status": request.status,
        "result": request.result,
        "error": request.error,
        "apply": request.apply
    }))
}

fn generated_run_id(request_id: &str, request_digest: &str) -> String {
    let payload = format!("{request_id}\n{request_digest}");
    format!(
        "pipeline:{}",
        &checksum_bytes(payload.as_bytes())["sha256:".len()..][..24]
    )
}

fn snapshot_fingerprint(snapshot: &UpstreamSnapshot) -> String {
    checksum_json(snapshot).unwrap_or_else(|_| "sha256:unavailable".to_owned())
}

fn checksum_json<T: Serialize>(value: &T) -> Result<String, CompositeError> {
    Ok(checksum_bytes(&serde_json::to_vec(value)?))
}

fn checksum_bytes(value: &[u8]) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(value)))
}

fn json_to_sql_error(error: serde_json::Error) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(error))
}

fn normalize_object(value: &serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::Object(_) => value.clone(),
        serde_json::Value::Null => empty_object(),
        other => serde_json::json!({"value": other}),
    }
}

fn empty_object() -> serde_json::Value {
    serde_json::Value::Object(Default::default())
}

fn to_i64(value: usize) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

fn usize_from_i64(value: i64) -> usize {
    usize::try_from(value.max(0)).unwrap_or(usize::MAX)
}

#[cfg(test)]
mod tests {
    use super::{
        begin_pipeline_step_write, complete_pipeline_step_write, read_pipeline_status,
        refresh_pipeline_write, PipelineBeginStepRequest, PipelineCompleteStepRequest,
        PipelineRefreshRequest, PipelineRunStatus, PipelineStepName, PipelineStepStatus,
        StepCompletionStatus,
    };
    use rusqlite::{params, Connection};
    use token_manager::{write_library, AdmissionStatus, LibraryKey, TokenWrite};

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = Connection::open(&path).unwrap();
        for library in [
            LibraryKey::Core,
            LibraryKey::Stocks,
            LibraryKey::Extension,
            LibraryKey::Test,
        ] {
            write_library(
                &mut connection,
                library,
                NETWORK,
                CHAIN_ID as u64,
                vec![token(library)],
                Some("fixture:v1"),
                "test",
                "fixture",
            )
            .unwrap();
        }
        drop(connection);
        seed_scope(&path, "sha256:scope-v1");
        (directory, path)
    }

    fn token(library: LibraryKey) -> TokenWrite {
        let address = match library {
            LibraryKey::Core => "0x1111111111111111111111111111111111111111",
            LibraryKey::Stocks => "0x2222222222222222222222222222222222222222",
            LibraryKey::Extension => "0x3333333333333333333333333333333333333333",
            LibraryKey::Test => "0x4444444444444444444444444444444444444444",
        };
        TokenWrite {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID as u64,
            token_key_id: manager_contracts::TokenKeyId::new(NETWORK, CHAIN_ID as u64, address)
                .unwrap(),
            v4_currency_key: address.to_owned(),
            token_address: address.to_owned(),
            token_symbol: format!("T-{}", library.as_str()),
            token_name: Some(format!("T-{}", library.as_str())),
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: false,
            admission_status: AdmissionStatus::Admitted,
            admission_source: "fixture".to_owned(),
            metadata_source: "fixture".to_owned(),
            metadata_checksum: format!("sha256:{}", library.as_str()),
            source_json: Some("{}".to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    fn seed_scope(path: &std::path::Path, checksum: &str) {
        let connection = Connection::open(path).unwrap();
        connection
            .execute(
                "INSERT INTO pool_scope_config (
                    network, chain_id, scope_key, pool_table,
                    library_keys_json, library_versions_json,
                    library_checksums_json, filter_mode,
                    excluded_token_key_ids_json, enabled,
                    materialized_checksum, last_rebuilt_at, created_at, updated_at
                 ) VALUES (?1, ?2, 'core4', 'v4_exchange_pools_core4',
                           '[\"core\"]', '{}', '{}', 'edge_any', '[]', 1,
                           ?3, 'fixture', 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID, checksum],
            )
            .unwrap();
    }

    fn refresh_request(dry_run: bool) -> PipelineRefreshRequest {
        PipelineRefreshRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "pipeline.refresh".to_owned(),
            request_id: if dry_run {
                "refresh-dry".to_owned()
            } else {
                "refresh-apply".to_owned()
            },
            idempotency_key: if dry_run {
                "refresh-dry-idem".to_owned()
            } else {
                "refresh-apply-idem".to_owned()
            },
            apply: !dry_run,
            requested_steps: vec![
                PipelineStepName::CandidateEdge,
                PipelineStepName::CandidateExchange,
            ],
        }
    }

    fn begin_request(run_id: &str, step_id: &str, request_id: &str) -> PipelineBeginStepRequest {
        PipelineBeginStepRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "pipeline.step.begin".to_owned(),
            request_id: request_id.to_owned(),
            idempotency_key: format!("{request_id}-idem"),
            apply: true,
            run_id: run_id.to_owned(),
            step_id: step_id.to_owned(),
        }
    }

    fn complete_request(
        run_id: &str,
        step_id: &str,
        status: StepCompletionStatus,
        request_id: &str,
    ) -> PipelineCompleteStepRequest {
        PipelineCompleteStepRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "pipeline.step.complete".to_owned(),
            request_id: request_id.to_owned(),
            idempotency_key: format!("{request_id}-idem"),
            apply: true,
            run_id: run_id.to_owned(),
            step_id: step_id.to_owned(),
            status,
            result: serde_json::json!({"source": "fixture"}),
            error: (status == StepCompletionStatus::Failed).then(|| "fixture failure".to_owned()),
        }
    }

    #[test]
    fn refresh_dry_run_is_non_persistent_and_apply_is_idempotent() {
        let (_directory, path) = database();
        let dry = refresh_pipeline_write(&path, refresh_request(true)).unwrap();
        assert!(dry.result.dry_run);
        assert!(!dry.idempotent_replay);
        let connection = Connection::open(&path).unwrap();
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_pipeline_runs", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 0);
        drop(connection);

        let first = refresh_pipeline_write(&path, refresh_request(false)).unwrap();
        assert_eq!(first.result.run.status, PipelineRunStatus::Running);
        assert_eq!(first.result.steps.len(), 2);
        let replay = refresh_pipeline_write(&path, refresh_request(false)).unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result.run, first.result.run);

        let connection = Connection::open(&path).unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_pipeline_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(event_count, 1);
        let generic_event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_events", [], |row| row.get(0))
            .unwrap();
        assert_eq!(generic_event_count, 1);
    }

    #[test]
    fn stale_dependency_between_begin_and_complete_is_rejected() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request(false)).unwrap();
        let run_id = refresh.result.run.run_id;
        let step_id = refresh.result.steps[0].step_id.clone();
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_id, "begin-1")).unwrap();

        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE token_library_state
                 SET library_version = library_version + 1,
                     metadata_checksum = 'sha256:changed'
                 WHERE network = ?1 AND chain_id = ?2 AND library_key = 'core'",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);

        let error = complete_pipeline_step_write(
            &path,
            complete_request(
                &run_id,
                &step_id,
                StepCompletionStatus::Completed,
                "complete-1",
            ),
        )
        .unwrap_err();
        assert!(matches!(
            error,
            super::CompositeError::ExpectedDependencyMismatch { .. }
                | super::CompositeError::UpstreamInconsistent { .. }
        ));
    }

    #[test]
    fn failed_step_can_be_retried_from_its_checkpoint() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request(false)).unwrap();
        let run_id = refresh.result.run.run_id;
        let step_id = refresh.result.steps[0].step_id.clone();
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_id, "begin-1")).unwrap();
        let failed = complete_pipeline_step_write(
            &path,
            complete_request(
                &run_id,
                &step_id,
                StepCompletionStatus::Failed,
                "complete-failed",
            ),
        )
        .unwrap();
        assert_eq!(failed.result.run.status, PipelineRunStatus::Failed);
        assert_eq!(failed.result.step.status, PipelineStepStatus::Failed);

        let retry =
            begin_pipeline_step_write(&path, begin_request(&run_id, &step_id, "begin-retry"))
                .unwrap();
        assert_eq!(retry.result.run.status, PipelineRunStatus::Running);
        assert_eq!(retry.result.step.status, PipelineStepStatus::Running);
        assert_eq!(retry.result.step.attempt_count, 2);
        assert_eq!(retry.result.run.last_completed_step_id, None);
    }

    #[test]
    fn pipeline_status_reports_current_checkpoint_and_upstream_fingerprint() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request(false)).unwrap();
        let snapshot = read_pipeline_status(&path, NETWORK, CHAIN_ID).unwrap();
        assert!(snapshot.enabled);
        assert_eq!(
            snapshot.latest_run.unwrap().run_id,
            refresh.result.run.run_id
        );
        assert_eq!(
            snapshot.current_step.unwrap().step_name,
            PipelineStepName::CandidateEdge
        );
        assert!(snapshot
            .upstream_assessment
            .fingerprint
            .starts_with("sha256:"));
    }

    #[test]
    fn empty_pipeline_and_successful_step_replay_are_rejected() {
        let (_directory, path) = database();
        let mut empty = refresh_request(true);
        empty.requested_steps.clear();
        assert!(matches!(
            refresh_pipeline_write(&path, empty).unwrap_err(),
            super::CompositeError::InvalidRequest(_)
        ));

        let refresh = refresh_pipeline_write(&path, refresh_request(false)).unwrap();
        let run_id = refresh.result.run.run_id;
        let step_id = refresh.result.steps[0].step_id.clone();
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_id, "idle-begin")).unwrap();
        let idle = complete_pipeline_step_write(
            &path,
            complete_request(
                &run_id,
                &step_id,
                StepCompletionStatus::Idle,
                "idle-complete",
            ),
        )
        .unwrap();
        assert_eq!(idle.result.step.status, PipelineStepStatus::Idle);
        assert!(matches!(
            begin_pipeline_step_write(&path, begin_request(&run_id, &step_id, "idle-replay"))
                .unwrap_err(),
            super::CompositeError::StepAlreadyFinal { .. }
        ));
    }
}
