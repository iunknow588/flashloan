use closed_loop_manager::{
    read_closed_loop_snapshot, ClosedLoopError, ClosedLoopRow, ClosedLoopStatus,
};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

pub mod runtime;

pub use runtime::{
    ReviewPermit, ReviewRuntime, ReviewRuntimeStatus, ReviewSupervisor, ReviewSupervisorCommand,
    ReviewSupervisorStatus,
};

const NETWORK_PROTOCOL: &str = "uniswap_v4";
const REVIEW_SCOPE_KEY: &str = "core4";
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_SNAPSHOT_LIMIT: usize = 1_000;

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

struct ReviewInput {
    closed_loop: ClosedLoopStatus,
    loops: Vec<ClosedLoopRow>,
}

#[derive(Default)]
struct DesiredReviewData {
    reviews: Vec<ReviewRow>,
    diagnostics: Vec<ReviewDiagnosticRow>,
    broadcast_requests: Vec<BroadcastRequestRow>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ReviewChecksumPayload<'a> {
    closed_loop_version: i64,
    closed_loop_checksum: &'a str,
    candidate_edge_version: i64,
    candidate_edge_checksum: &'a str,
    observation_version: i64,
    observation_checksum: &'a str,
    pool_scope_checksum: &'a str,
    library_versions: &'a BTreeMap<String, i64>,
    library_checksums: &'a BTreeMap<String, String>,
    reviews: &'a [ReviewRow],
    diagnostics: &'a [ReviewDiagnosticRow],
    broadcast_requests: &'a [BroadcastRequestRow],
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
    .map(|(result, _)| result)
}

pub fn run_reviews_write(
    database_path: impl AsRef<Path>,
    request: ReviewWriteRequest,
) -> Result<ReviewWriteOutcome, ReviewError> {
    let (result, idempotent_replay) = run_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        request.apply,
        &request.evidence,
        Some(&request),
    )?;
    Ok(ReviewWriteOutcome {
        result,
        idempotent_replay,
    })
}

fn run_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    evidence: &[ReviewEvidenceInput],
    request: Option<&ReviewWriteRequest>,
) -> Result<(ReviewMaterializationResult, bool), ReviewError> {
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
) -> Result<(ReviewMaterializationResult, bool), ReviewError> {
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
                return Ok((persisted.result, true));
            }
        }
    }

    let input = read_review_input(connection, network, chain_id)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &input.closed_loop)?;
    }
    validate_evidence_membership(evidence, &input.loops)?;
    let desired = build_review_rows(&input, evidence)?;
    let checksum = review_checksum(&input, &desired)?;
    let previous = read_status_on_connection(connection, network, chain_id)?;
    let counts_match = previous.enabled
        && previous.loop_count == input.closed_loop.loop_count
        && previous.reviewed_count == evidence.len()
        && previous.completed_count
            == desired
                .reviews
                .iter()
                .filter(|row| row.review_status == "completed")
                .count()
        && previous.data_incomplete_count
            == desired
                .reviews
                .iter()
                .filter(|row| row.review_status == "data_incomplete")
                .count()
        && previous.not_executable_count
            == desired
                .reviews
                .iter()
                .filter(|row| row.review_status == "not_executable")
                .count()
        && previous.failed_count
            == desired
                .reviews
                .iter()
                .filter(|row| row.review_status == "failed")
                .count()
        && previous.broadcast_request_count == desired.broadcast_requests.len();
    let changed =
        previous.materialized_checksum.as_deref() != Some(checksum.as_str()) || !counts_match;
    let review_version = if changed {
        previous.review_version.saturating_add(1).max(1)
    } else {
        previous.review_version.max(1)
    };
    let completed_count = status_count(&desired.reviews, "completed");
    let data_incomplete_count = status_count(&desired.reviews, "data_incomplete");
    let not_executable_count = status_count(&desired.reviews, "not_executable");
    let failed_count = status_count(&desired.reviews, "failed");
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
        reviewed_count: evidence.len(),
        completed_count,
        data_incomplete_count,
        not_executable_count,
        failed_count,
        broadcast_request_count: desired.broadcast_requests.len(),
        changed_rows,
        review_version,
        materialized_checksum: checksum,
        consumers_need_rebuild: changed,
    };

    if apply {
        if changed {
            replace_review_rows(connection, network, chain_id, &desired, &result)?;
        }
        if let Some((request, request_digest)) = prepared_write {
            persist_write_request(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

fn status_count(rows: &[ReviewRow], status: &str) -> usize {
    rows.iter()
        .filter(|row| row.review_status == status)
        .count()
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
    Ok(ReviewInput {
        closed_loop: snapshot.status,
        loops: snapshot.loops,
    })
}

fn validate_evidence_membership(
    evidence: &[ReviewEvidenceInput],
    loops: &[ClosedLoopRow],
) -> Result<(), ReviewError> {
    let known = loops
        .iter()
        .map(|row| row.loop_key.as_str())
        .collect::<BTreeSet<_>>();
    let mut seen = BTreeSet::new();
    for item in evidence {
        if item.loop_key.trim().is_empty() {
            return Err(ReviewError::InvalidRequest(
                "evidence.loopKey must not be empty".to_owned(),
            ));
        }
        if !known.contains(item.loop_key.as_str()) {
            return Err(ReviewError::UnknownClosedLoop(item.loop_key.clone()));
        }
        if !seen.insert(item.loop_key.as_str()) {
            return Err(ReviewError::DuplicateEvidence(item.loop_key.clone()));
        }
    }
    Ok(())
}

fn build_review_rows(
    input: &ReviewInput,
    evidence: &[ReviewEvidenceInput],
) -> Result<DesiredReviewData, ReviewError> {
    let evidence_by_loop = evidence
        .iter()
        .map(|item| (item.loop_key.as_str(), item))
        .collect::<BTreeMap<_, _>>();
    let mut desired = DesiredReviewData::default();
    for closed_loop in &input.loops {
        let item = evidence_by_loop.get(closed_loop.loop_key.as_str()).copied();
        let run_arbitrage = item.and_then(|value| value.run_arbitrage.as_ref());
        let run_json = match run_arbitrage {
            Some(value) => serde_json::to_value(value)?,
            None => serde_json::Value::Null,
        };
        let static_preview = item
            .map(|value| normalize_object(&value.static_preview))
            .unwrap_or_else(empty_object);
        let reviewed_at = item
            .and_then(|value| value.reviewed_at.as_deref())
            .filter(|value| !value.trim().is_empty())
            .unwrap_or("unreported")
            .to_owned();

        let mut diagnostics = BTreeMap::new();
        if let Some(item) = item {
            for diagnostic in &item.diagnostics {
                insert_diagnostic(&mut diagnostics, &closed_loop.loop_key, diagnostic.clone())?;
            }
        }
        if let Some(run) = run_arbitrage {
            for diagnostic in &run.diagnostics {
                insert_diagnostic(&mut diagnostics, &closed_loop.loop_key, diagnostic.clone())?;
            }
        }

        let signed_pnl = run_arbitrage
            .and_then(|value| value.signed_pnl.as_ref())
            .filter(|value| validate_signed_pnl(value));
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
            Some(run) if run.ok && run.executable => ("completed", None),
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
        let row = ReviewRow {
            loop_key: closed_loop.loop_key.clone(),
            closed_loop_version: input.closed_loop.closed_loop_version,
            closed_loop_checksum: closed_loop_checksum(&input.closed_loop)?.to_owned(),
            candidate_edge_version: closed_loop.candidate_edge_version,
            candidate_edge_checksum: closed_loop.candidate_edge_checksum.clone(),
            observation_version: closed_loop.observation_version,
            observation_checksum: closed_loop.observation_checksum.clone(),
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
            pool_path_key: closed_loop.pool_path_key.clone(),
            pool_ids: closed_loop.pool_ids.clone(),
            review_status: review_status.to_owned(),
            page_category,
            review_reason,
            static_preview,
            run_arbitrage: run_json,
            signed_pnl: signed_pnl.cloned(),
            reviewed_at,
        };
        if review_status == "completed" {
            if let Some(pnl) = &row.signed_pnl {
                if pnl.amount > 0.0 {
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

fn review_checksum(
    input: &ReviewInput,
    desired: &DesiredReviewData,
) -> Result<String, ReviewError> {
    let payload = ReviewChecksumPayload {
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
        reviews: &desired.reviews,
        diagnostics: &desired.diagnostics,
        broadcast_requests: &desired.broadcast_requests,
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
                signed_pnl_source, reviewed_at
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
    let mut statement = connection.prepare(
        "SELECT loop_key, closed_loop_version, closed_loop_checksum,
                anchor_token_key_id, path_text, path_addresses_json,
                path_symbols_json, pool_path_key, pool_ids_json, signed_pnl_raw,
                signed_pnl_amount, signed_pnl_currency, signed_pnl_address,
                signed_pnl_unit, signed_pnl_source, request_status, request_reason
         FROM manager_v4_review_broadcast_requests
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR loop_key = ?5)
         ORDER BY loop_key, created_at
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
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn replace_review_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredReviewData,
    result: &ReviewMaterializationResult,
) -> Result<(), ReviewError> {
    let now = chrono::Utc::now().to_rfc3339();
    for table in [
        "manager_v4_review_diagnostics",
        "manager_v4_review_broadcast_requests",
        "manager_v4_reviews",
    ] {
        connection.execute(
            &format!(
                "DELETE FROM {table}
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                   AND scope_key = ?4"
            ),
            params![network, chain_id, NETWORK_PROTOCOL, REVIEW_SCOPE_KEY],
        )?;
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
                 signed_pnl_source, reviewed_at, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23,
                       ?24, ?25, ?26, ?27, ?28, ?29, ?30, ?31, ?32, ?32)",
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
                 request_reason, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?22)",
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

fn parse_json_value(value: String) -> Result<serde_json::Value, rusqlite::Error> {
    serde_json::from_str(&value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            value.len(),
            rusqlite::types::Type::Text,
            Box::new(error),
        )
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
        read_review_page, read_review_snapshot, run_reviews, run_reviews_write, ExpectedUpstream,
        ReviewDiagnosticInput, ReviewDiagnosticType, ReviewError, ReviewEvidenceInput,
        ReviewOutcomeFilter, ReviewReadQuery, ReviewWriteRequest, RunArbitrageEvidence, SignedPnl,
    };
    use rusqlite::{params, Connection};
    use std::collections::BTreeMap;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const LOOP_KEY: &str = "loop-1";

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = Connection::open(&path).unwrap();
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
                    pool_ids_json, backup_pool_ids_json, hop_candidate_counts_json,
                    hop_candidate_combination_count, recommendation_basis,
                    recommended_first_priority, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, status, source, created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 'route:v1',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           '0x1111111111111111111111111111111111111111', 'USDG',
                           'robinhood:4663:0x2222222222222222222222222222222222222222',
                           'robinhood:4663:0x3333333333333333333333333333333333333333',
                           'USDG -> AMZN -> TA -> USDG',
                           '[\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x3333333333333333333333333333333333333333\",\"0x1111111111111111111111111111111111111111\"]',
                           '[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]', 'pool-path:1',
                           '[\"pool-a\",\"pool-yz\",\"pool-d\"]', '{}', '{\"1\":1,\"2\":1,\"3\":1}',
                           1, 'fixture', 1, 7, 'sha256:candidate-edge', 4,
                           'sha256:observation', 'active', 'fixture', 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID, LOOP_KEY],
            )
            .unwrap();
        drop(connection);
        (directory, path)
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
            static_preview: serde_json::json!({"ok": ok, "executable": executable}),
            run_arbitrage: Some(RunArbitrageEvidence {
                ok,
                executable,
                block_number: Some(100),
                signed_pnl,
                diagnostics,
                raw: BTreeMap::from([(
                    "gasEstimate".to_owned(),
                    serde_json::Value::String("120000".to_owned()),
                )]),
            }),
            diagnostics: Vec::new(),
            reviewed_at: Some("2026-11-04T00:00:00Z".to_owned()),
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

    #[test]
    fn positive_executable_review_generates_broadcast_request_only() {
        let (_directory, path) = database();
        let result = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(12.5)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        assert_eq!(result.completed_count, 1);
        assert_eq!(result.broadcast_request_count, 1);
        let snapshot = read_review_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.reviews[0].review_status, "completed");
        assert_eq!(snapshot.broadcast_requests[0].request_status, "generated");
        assert_eq!(snapshot.broadcast_requests[0].signed_pnl.amount, 12.5);
        assert_eq!(snapshot.broadcast_requests[0].pool_ids[1], "pool-yz");

        let repeated = run_reviews(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence(Some(signed_pnl(12.5)), true, true, Vec::new())],
            true,
        )
        .unwrap();
        assert_eq!(repeated.review_version, result.review_version);
        assert_eq!(repeated.changed_rows, 0);
        assert!(!repeated.consumers_need_rebuild);
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
}
