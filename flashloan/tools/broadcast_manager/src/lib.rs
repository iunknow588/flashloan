use manager_contracts::normalize_address;
use review_manager::{
    read_broadcast_requests_on_connection, read_review_status_on_connection, BroadcastRequestRow,
    ReviewError, ReviewStatus, SignedPnl,
};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

pub mod runtime;

pub use runtime::{BroadcastRuntimeStatus, BroadcastSupervisor, ExecutionQueue, ExecutionTask};

const NETWORK_PROTOCOL: &str = "uniswap_v4";
const BROADCAST_SCOPE_KEY: &str = "core4";
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_IDENTIFIER_LENGTH: usize = 512;
const MAX_RPC_URL_LENGTH: usize = 2048;
const MAX_SNAPSHOT_LIMIT: usize = 1_000;
const MAX_INTERNAL_LIMIT: usize = 100_000;

#[derive(Debug)]
pub enum BroadcastError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    InvalidRequest(String),
    ReviewRead(String),
    MissingReviewState,
    ReviewSnapshotTruncated {
        expected: usize,
        actual: usize,
    },
    UnknownRequest(String),
    BroadcastRequestNotEligible {
        loop_key: String,
        request_status: String,
    },
    DuplicateAttemptInput(String),
    DuplicateAttempt(String),
    DuplicateDiagnostic {
        attempt_id: String,
        diagnostic_id: String,
    },
    ExpectedUpstreamMismatch {
        field: String,
        expected: String,
        actual: String,
    },
    AttemptNotFound(String),
    AttemptAlreadyTerminal {
        attempt_id: String,
        status: String,
    },
    IdempotencyKeyReused {
        idempotency_key: String,
        existing_request_id: String,
        submitted_request_id: String,
    },
}

impl fmt::Display for BroadcastError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "broadcast storage error: {error}"),
            Self::Json(error) => write!(formatter, "broadcast json error: {error}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid broadcast request: {message}")
            }
            Self::ReviewRead(message) => {
                write!(formatter, "review manager upstream read failed: {message}")
            }
            Self::MissingReviewState => {
                formatter.write_str("review scope core4 has not been materialized")
            }
            Self::ReviewSnapshotTruncated { expected, actual } => write!(
                formatter,
                "review broadcast requests are truncated: expected {expected}, read {actual}"
            ),
            Self::UnknownRequest(loop_key) => {
                write!(
                    formatter,
                    "attempt references unknown review broadcast request {loop_key}"
                )
            }
            Self::BroadcastRequestNotEligible {
                loop_key,
                request_status,
            } => write!(
                formatter,
                "request {loop_key} is not eligible for broadcast; current status is {request_status}"
            ),
            Self::DuplicateAttemptInput(attempt_id) => {
                write!(
                    formatter,
                    "attempt input contains duplicate attempt_id {attempt_id}"
                )
            }
            Self::DuplicateAttempt(attempt_id) => {
                write!(
                    formatter,
                    "attempt {attempt_id} already exists in the broadcast library"
                )
            }
            Self::DuplicateDiagnostic {
                attempt_id,
                diagnostic_id,
            } => write!(
                formatter,
                "diagnostic {diagnostic_id} is duplicated for attempt {attempt_id}"
            ),
            Self::ExpectedUpstreamMismatch {
                field,
                expected,
                actual,
            } => write!(
                formatter,
                "upstream {field} changed: expected {expected}, actual {actual}"
            ),
            Self::AttemptNotFound(attempt_id) => {
                write!(formatter, "attempt {attempt_id} does not exist")
            }
            Self::AttemptAlreadyTerminal {
                attempt_id,
                status,
            } => write!(
                formatter,
                "attempt {attempt_id} is already terminal with status {status}"
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

impl std::error::Error for BroadcastError {}

impl From<rusqlite::Error> for BroadcastError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<serde_json::Error> for BroadcastError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

impl From<ReviewError> for BroadcastError {
    fn from(error: ReviewError) -> Self {
        Self::ReviewRead(error.to_string())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpectedUpstream {
    pub review_version: Option<i64>,
    pub review_checksum: Option<String>,
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AttemptCreateInput {
    pub attempt_id: Option<String>,
    pub loop_key: String,
    pub signer_address: String,
    pub nonce: i64,
    pub gas_limit: Option<i64>,
    pub gas_price_wei: Option<String>,
    pub max_fee_per_gas_wei: Option<String>,
    pub max_priority_fee_per_gas_wei: Option<String>,
    pub rpc_url: String,
    pub rpc_name: Option<String>,
    #[serde(default)]
    pub evidence: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct BroadcastSubmitRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub expected_upstream: ExpectedUpstream,
    pub attempts: Vec<AttemptCreateInput>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BroadcastTerminalStatus {
    Success,
    Failed,
}

impl BroadcastTerminalStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastDiagnosticInput {
    pub diagnostic_id: Option<String>,
    pub diagnostic_type: BroadcastDiagnosticType,
    pub diagnostic_code: Option<String>,
    pub message: Option<String>,
    pub rpc_url: Option<String>,
    pub tx_hash: Option<String>,
    #[serde(default)]
    pub payload: serde_json::Value,
    pub observed_at: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BroadcastDiagnosticType {
    RpcUnavailable,
    GasEstimateFailed,
    Revert,
    NonceTooLow,
    Replaced,
    Timeout,
    BroadcastFailed,
    EvidenceIncomplete,
    Other,
}

impl BroadcastDiagnosticType {
    fn as_str(self) -> &'static str {
        match self {
            Self::RpcUnavailable => "rpc_unavailable",
            Self::GasEstimateFailed => "gas_estimate_failed",
            Self::Revert => "revert",
            Self::NonceTooLow => "nonce_too_low",
            Self::Replaced => "replaced",
            Self::Timeout => "timeout",
            Self::BroadcastFailed => "broadcast_failed",
            Self::EvidenceIncomplete => "evidence_incomplete",
            Self::Other => "other",
        }
    }
}

#[derive(Debug, Clone)]
pub struct BroadcastFinalizeRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
    pub expected_upstream: ExpectedUpstream,
    pub finalizations: Vec<AttemptFinalizeInput>,
}

#[derive(Debug, Clone)]
pub struct BroadcastClearRequest {
    pub network: String,
    pub chain_id: i64,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AttemptFinalizeInput {
    pub attempt_id: String,
    pub status: BroadcastTerminalStatus,
    pub tx_hash: Option<String>,
    #[serde(default = "empty_object")]
    pub receipt: serde_json::Value,
    pub status_reason: Option<String>,
    #[serde(default)]
    pub diagnostics: Vec<BroadcastDiagnosticInput>,
    #[serde(default)]
    pub evidence: serde_json::Value,
    pub finalized_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AttemptRow {
    pub attempt_id: String,
    pub loop_key: String,
    pub closed_loop_version: i64,
    pub closed_loop_checksum: String,
    pub review_version: i64,
    pub review_checksum: String,
    pub anchor_token_key_id: String,
    pub path_text: String,
    pub path_addresses: Vec<String>,
    pub path_symbols: Vec<String>,
    pub pool_path_key: String,
    pub pool_ids: Vec<String>,
    pub signed_pnl: SignedPnl,
    pub signer_address: String,
    pub nonce: i64,
    pub gas_limit: Option<i64>,
    pub gas_price_wei: Option<String>,
    pub max_fee_per_gas_wei: Option<String>,
    pub max_priority_fee_per_gas_wei: Option<String>,
    pub rpc_url: String,
    pub rpc_name: Option<String>,
    pub tx_hash: Option<String>,
    pub status: String,
    pub status_reason: Option<String>,
    pub receipt: serde_json::Value,
    pub evidence: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
    pub terminal_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastDiagnosticRow {
    pub attempt_id: String,
    pub loop_key: String,
    pub diagnostic_id: String,
    pub diagnostic_type: String,
    pub diagnostic_code: Option<String>,
    pub message: Option<String>,
    pub rpc_url: Option<String>,
    pub tx_hash: Option<String>,
    pub payload: serde_json::Value,
    pub observed_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastStatus {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub broadcast_version: i64,
    pub materialized_checksum: Option<String>,
    pub review_version: Option<i64>,
    pub review_checksum: Option<String>,
    pub closed_loop_version: Option<i64>,
    pub closed_loop_checksum: Option<String>,
    pub request_count: usize,
    pub attempt_count: usize,
    pub pending_count: usize,
    pub success_count: usize,
    pub failed_count: usize,
    pub terminal_count: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastSnapshot {
    pub status: BroadcastStatus,
    pub attempts: Vec<AttemptRow>,
    pub diagnostics: Vec<BroadcastDiagnosticRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastClearResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub apply: bool,
    pub dry_run: bool,
    pub cleared_attempts: usize,
    pub cleared_diagnostics: usize,
    pub remaining_attempts: usize,
    pub remaining_diagnostics: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastClearOutcome {
    #[serde(flatten)]
    pub result: BroadcastClearResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub apply: bool,
    pub dry_run: bool,
    pub review_version: i64,
    pub review_checksum: String,
    pub closed_loop_version: i64,
    pub closed_loop_checksum: String,
    pub request_count: usize,
    pub attempt_count: usize,
    pub pending_count: usize,
    pub success_count: usize,
    pub failed_count: usize,
    pub terminal_count: usize,
    pub changed_rows: usize,
    pub broadcast_version: i64,
    pub materialized_checksum: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastWriteOutcome {
    #[serde(flatten)]
    pub result: BroadcastMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastFinalizeOutcome {
    #[serde(flatten)]
    pub result: BroadcastMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BroadcastChecksumPayload<'a> {
    review_version: i64,
    review_checksum: &'a str,
    closed_loop_version: i64,
    closed_loop_checksum: &'a str,
    attempts: &'a [AttemptRow],
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedSubmitWrite {
    request_id: String,
    request_digest: String,
    result: BroadcastMaterializationResult,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedFinalizeWrite {
    request_id: String,
    request_digest: String,
    result: BroadcastMaterializationResult,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedClearWrite {
    request_id: String,
    request_digest: String,
    result: BroadcastClearResult,
}

pub fn submit_broadcasts(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    attempts: Vec<AttemptCreateInput>,
    apply: bool,
) -> Result<BroadcastMaterializationResult, BroadcastError> {
    submit_impl(
        database_path.as_ref(),
        network,
        chain_id,
        apply,
        &attempts,
        None,
    )
    .map(|(result, _)| result)
}

pub fn submit_broadcasts_write(
    database_path: impl AsRef<Path>,
    request: BroadcastSubmitRequest,
) -> Result<BroadcastWriteOutcome, BroadcastError> {
    let (result, idempotent_replay) = submit_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        request.apply,
        &request.attempts,
        Some(&request),
    )?;
    Ok(BroadcastWriteOutcome {
        result,
        idempotent_replay,
    })
}

pub fn finalize_broadcast_attempts(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    finalizations: Vec<AttemptFinalizeInput>,
    apply: bool,
) -> Result<BroadcastMaterializationResult, BroadcastError> {
    finalize_impl(
        database_path.as_ref(),
        network,
        chain_id,
        apply,
        &finalizations,
        None,
    )
    .map(|(result, _)| result)
}

pub fn finalize_broadcast_attempts_write(
    database_path: impl AsRef<Path>,
    request: BroadcastFinalizeRequest,
) -> Result<BroadcastFinalizeOutcome, BroadcastError> {
    let (result, idempotent_replay) = finalize_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        request.apply,
        &request.finalizations,
        Some(&request),
    )?;
    Ok(BroadcastFinalizeOutcome {
        result,
        idempotent_replay,
    })
}

pub fn clear_broadcast_records_write(
    database_path: impl AsRef<Path>,
    request: BroadcastClearRequest,
) -> Result<BroadcastClearOutcome, BroadcastError> {
    let (result, idempotent_replay) = clear_impl(database_path.as_ref(), &request)?;
    Ok(BroadcastClearOutcome {
        result,
        idempotent_replay,
    })
}

fn clear_impl(
    database_path: &Path,
    request: &BroadcastClearRequest,
) -> Result<(BroadcastClearResult, bool), BroadcastError> {
    validate_operation(&request.network, request.chain_id)?;
    if request.operation.trim().is_empty() {
        return Err(BroadcastError::InvalidRequest(
            "operation must not be empty".to_owned(),
        ));
    }
    validate_identifier("request_id", &request.request_id, MAX_REQUEST_ID_LENGTH)?;
    validate_identifier(
        "idempotency_key",
        &request.idempotency_key,
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )?;
    let request_digest = clear_request_digest(request)?;
    let mut connection = open_connection(database_path, request.apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;

    if request.apply {
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(response_json) = find_write_request(&transaction, request)? {
            let persisted = serde_json::from_str::<PersistedClearWrite>(&response_json)?;
            if persisted.request_digest != request_digest {
                return Err(BroadcastError::IdempotencyKeyReused {
                    idempotency_key: request.idempotency_key.clone(),
                    existing_request_id: persisted.request_id,
                    submitted_request_id: request.request_id.clone(),
                });
            }
            return Ok((persisted.result, true));
        }

        let result = prepare_clear_result(&transaction, request)?;
        transaction.execute(
            "DELETE FROM manager_broadcast_attempt_diagnostics
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
            params![
                request.network,
                request.chain_id,
                NETWORK_PROTOCOL,
                BROADCAST_SCOPE_KEY
            ],
        )?;
        transaction.execute(
            "DELETE FROM manager_cow_execution_attempts
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
            params![
                request.network,
                request.chain_id,
                NETWORK_PROTOCOL,
                BROADCAST_SCOPE_KEY
            ],
        )?;
        transaction.execute(
            "DELETE FROM manager_broadcast_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
            params![
                request.network,
                request.chain_id,
                NETWORK_PROTOCOL,
                BROADCAST_SCOPE_KEY
            ],
        )?;
        persist_clear_write(&transaction, request, &request_digest, &result)?;
        transaction.commit()?;
        return Ok((result, false));
    }

    let result = prepare_clear_result(&connection, request)?;
    Ok((result, false))
}

fn prepare_clear_result(
    connection: &Connection,
    request: &BroadcastClearRequest,
) -> Result<BroadcastClearResult, BroadcastError> {
    let pending_attempts = count_scoped_rows(
        connection,
        "manager_cow_execution_attempts",
        &request.network,
        request.chain_id,
        Some("status = 'pending'"),
    )?;
    if pending_attempts > 0 {
        return Err(BroadcastError::InvalidRequest(
            "cannot clear broadcast records while pending attempts exist".to_owned(),
        ));
    }
    let cleared_attempts = count_scoped_rows(
        connection,
        "manager_cow_execution_attempts",
        &request.network,
        request.chain_id,
        None,
    )?;
    let cleared_diagnostics = count_scoped_rows(
        connection,
        "manager_broadcast_attempt_diagnostics",
        &request.network,
        request.chain_id,
        None,
    )?;
    Ok(BroadcastClearResult {
        network: request.network.clone(),
        chain_id: request.chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: BROADCAST_SCOPE_KEY.to_owned(),
        apply: request.apply,
        dry_run: !request.apply,
        cleared_attempts,
        cleared_diagnostics,
        remaining_attempts: if request.apply { 0 } else { cleared_attempts },
        remaining_diagnostics: if request.apply {
            0
        } else {
            cleared_diagnostics
        },
    })
}

fn submit_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    attempts: &[AttemptCreateInput],
    request: Option<&BroadcastSubmitRequest>,
) -> Result<(BroadcastMaterializationResult, bool), BroadcastError> {
    validate_operation(network, chain_id)?;
    let prepared_write = match request {
        Some(request) => {
            validate_submit_request(request, network, chain_id)?;
            Some((request, submit_request_digest(request)?))
        }
        None => None,
    };

    let mut connection = open_connection(database_path, apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if apply {
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let outcome = submit_on_connection(
            &transaction,
            network,
            chain_id,
            true,
            attempts,
            prepared_write,
        )?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        submit_on_connection(
            &connection,
            network,
            chain_id,
            false,
            attempts,
            prepared_write,
        )
    }
}

fn submit_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    apply: bool,
    attempts: &[AttemptCreateInput],
    prepared_write: Option<(&BroadcastSubmitRequest, String)>,
) -> Result<(BroadcastMaterializationResult, bool), BroadcastError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(response_json) = find_write_request(connection, *request)? {
                let persisted = serde_json::from_str::<PersistedSubmitWrite>(&response_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(BroadcastError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok((persisted.result, true));
            }
        }
    }

    let review = read_review_upstream(connection, network, chain_id)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &review)?;
    }
    let requests = read_broadcast_requests_on_connection(
        connection,
        network,
        chain_id,
        None,
        MAX_INTERNAL_LIMIT,
    )?;
    if review.broadcast_request_count > requests.len() {
        return Err(BroadcastError::ReviewSnapshotTruncated {
            expected: review.broadcast_request_count,
            actual: requests.len(),
        });
    }
    let now = chrono::Utc::now().to_rfc3339();
    let desired = build_attempt_rows(&review, &requests, attempts, &now)?;
    let existing_attempts = read_attempts(
        connection,
        network,
        chain_id,
        None,
        None,
        0,
        MAX_INTERNAL_LIMIT,
    )?;
    validate_no_attempt_conflicts(&existing_attempts, &desired)?;
    let mut all_attempts = existing_attempts;
    all_attempts.extend(desired.clone());
    all_attempts.sort_by(|left, right| left.attempt_id.cmp(&right.attempt_id));

    let counts = attempt_counts(&all_attempts);
    let checksum = broadcast_checksum(&review, &all_attempts)?;
    let previous = read_status_on_connection(connection, network, chain_id)?;
    let materialized_changed = previous.materialized_checksum.as_deref() != Some(checksum.as_str());
    let broadcast_version = if materialized_changed {
        previous.broadcast_version.saturating_add(1).max(1)
    } else {
        previous.broadcast_version.max(1)
    };
    let result = BroadcastMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: BROADCAST_SCOPE_KEY.to_owned(),
        apply,
        dry_run: !apply,
        review_version: review.review_version,
        review_checksum: required_upstream_checksum(
            review.materialized_checksum.as_deref(),
            "review_checksum",
        )?,
        closed_loop_version: required_upstream_version(
            review.closed_loop_version,
            "closed_loop_version",
        )?,
        closed_loop_checksum: required_upstream_checksum(
            review.closed_loop_checksum.as_deref(),
            "closed_loop_checksum",
        )?,
        request_count: review.broadcast_request_count,
        attempt_count: counts.attempts,
        pending_count: counts.pending,
        success_count: counts.success,
        failed_count: counts.failed,
        terminal_count: counts.terminal,
        changed_rows: desired.len(),
        broadcast_version,
        materialized_checksum: checksum,
    };

    if apply {
        for row in &desired {
            insert_attempt(connection, network, chain_id, row)?;
        }
        upsert_broadcast_state(connection, network, chain_id, &result, &now)?;
        if let Some((request, request_digest)) = prepared_write {
            persist_submit_write(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

fn finalize_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    finalizations: &[AttemptFinalizeInput],
    request: Option<&BroadcastFinalizeRequest>,
) -> Result<(BroadcastMaterializationResult, bool), BroadcastError> {
    validate_operation(network, chain_id)?;
    let prepared_write = match request {
        Some(request) => {
            validate_finalize_request(request, network, chain_id)?;
            Some((request, finalize_request_digest(request)?))
        }
        None => None,
    };

    let mut connection = open_connection(database_path, apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if apply {
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let outcome = finalize_on_connection(
            &transaction,
            network,
            chain_id,
            true,
            finalizations,
            prepared_write,
        )?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        finalize_on_connection(
            &connection,
            network,
            chain_id,
            false,
            finalizations,
            prepared_write,
        )
    }
}

fn finalize_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    apply: bool,
    finalizations: &[AttemptFinalizeInput],
    prepared_write: Option<(&BroadcastFinalizeRequest, String)>,
) -> Result<(BroadcastMaterializationResult, bool), BroadcastError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(response_json) = find_write_request(connection, *request)? {
                let persisted = serde_json::from_str::<PersistedFinalizeWrite>(&response_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(BroadcastError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok((persisted.result, true));
            }
        }
    }

    let review = read_review_upstream(connection, network, chain_id)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &review)?;
    }
    let existing_attempts = read_attempts(
        connection,
        network,
        chain_id,
        None,
        None,
        0,
        MAX_INTERNAL_LIMIT,
    )?;
    let mut by_attempt = existing_attempts
        .into_iter()
        .map(|row| (row.attempt_id.clone(), row))
        .collect::<BTreeMap<_, _>>();
    validate_finalization_identity(finalizations)?;
    let now = chrono::Utc::now().to_rfc3339();
    let mut diagnostics = Vec::new();
    for finalization in finalizations {
        validate_finalization_input(finalization)?;
        let attempt = by_attempt
            .get_mut(&finalization.attempt_id)
            .ok_or_else(|| BroadcastError::AttemptNotFound(finalization.attempt_id.clone()))?;
        if attempt.status != "pending" {
            return Err(BroadcastError::AttemptAlreadyTerminal {
                attempt_id: attempt.attempt_id.clone(),
                status: attempt.status.clone(),
            });
        }
        attempt.status = finalization.status.as_str().to_owned();
        attempt.tx_hash = finalization.tx_hash.as_deref().map(str::to_owned);
        attempt.receipt = normalize_object(&finalization.receipt);
        attempt.evidence = normalize_object(&finalization.evidence);
        attempt.status_reason = finalization
            .status_reason
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned);
        attempt.updated_at = chronological_finalized_at(&finalization.finalized_at, &now)?;
        attempt.terminal_at = Some(chronological_finalized_at(
            &finalization.finalized_at,
            &now,
        )?);
        diagnostics.extend(build_diagnostics(attempt, &finalization.diagnostics)?);
    }
    let all_attempts = by_attempt.into_values().collect::<Vec<_>>();
    let counts = attempt_counts(&all_attempts);
    let checksum = broadcast_checksum(&review, &all_attempts)?;
    let previous = read_status_on_connection(connection, network, chain_id)?;
    let materialized_changed = previous.materialized_checksum.as_deref() != Some(checksum.as_str());
    let broadcast_version = if materialized_changed {
        previous.broadcast_version.saturating_add(1).max(1)
    } else {
        previous.broadcast_version.max(1)
    };
    let result = BroadcastMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: BROADCAST_SCOPE_KEY.to_owned(),
        apply,
        dry_run: !apply,
        review_version: review.review_version,
        review_checksum: required_upstream_checksum(
            review.materialized_checksum.as_deref(),
            "review_checksum",
        )?,
        closed_loop_version: required_upstream_version(
            review.closed_loop_version,
            "closed_loop_version",
        )?,
        closed_loop_checksum: required_upstream_checksum(
            review.closed_loop_checksum.as_deref(),
            "closed_loop_checksum",
        )?,
        request_count: review.broadcast_request_count,
        attempt_count: counts.attempts,
        pending_count: counts.pending,
        success_count: counts.success,
        failed_count: counts.failed,
        terminal_count: counts.terminal,
        changed_rows: finalizations.len() + diagnostics.len(),
        broadcast_version,
        materialized_checksum: checksum,
    };

    if apply {
        for finalized in finalizations {
            update_attempt_terminal(connection, network, chain_id, finalized)?;
        }
        for diagnostic in &diagnostics {
            insert_diagnostic(connection, network, chain_id, diagnostic)?;
        }
        upsert_broadcast_state(connection, network, chain_id, &result, &now)?;
        if let Some((request, request_digest)) = prepared_write {
            persist_finalize_write(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

pub fn read_broadcast_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<BroadcastStatus, BroadcastError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_status_on_connection(&connection, network, chain_id)
}

pub fn read_broadcast_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    attempt_id: Option<&str>,
    limit: usize,
) -> Result<BroadcastSnapshot, BroadcastError> {
    read_broadcast_snapshot_page(database_path, network, chain_id, attempt_id, None, 0, limit)
}

pub fn read_broadcast_snapshot_page(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    attempt_id: Option<&str>,
    status_filter: Option<&str>,
    offset: usize,
    limit: usize,
) -> Result<BroadcastSnapshot, BroadcastError> {
    if chain_id <= 0 {
        return Err(BroadcastError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if limit == 0 || limit > MAX_SNAPSHOT_LIMIT {
        return Err(BroadcastError::InvalidRequest(format!(
            "limit must be between 1 and {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    if attempt_id.is_some_and(|value| value.trim().is_empty()) {
        return Err(BroadcastError::InvalidRequest(
            "attempt_id must not be empty".to_owned(),
        ));
    }
    if status_filter.is_some_and(|value| value.trim().is_empty()) {
        return Err(BroadcastError::InvalidRequest(
            "status must not be empty".to_owned(),
        ));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let status = read_status_on_connection(&connection, network, chain_id)?;
    Ok(BroadcastSnapshot {
        status,
        attempts: read_attempts(
            &connection,
            network,
            chain_id,
            attempt_id,
            status_filter.as_deref(),
            offset,
            limit,
        )?,
        diagnostics: read_diagnostics(
            &connection,
            network,
            chain_id,
            attempt_id,
            status_filter.as_deref(),
            MAX_SNAPSHOT_LIMIT,
        )?,
    })
}

trait WriteIdentity {
    fn network(&self) -> &str;
    fn chain_id(&self) -> i64;
    fn operation(&self) -> &str;
    fn idempotency_key(&self) -> &str;
}

impl WriteIdentity for BroadcastSubmitRequest {
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
}

impl WriteIdentity for BroadcastFinalizeRequest {
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
}

impl WriteIdentity for BroadcastClearRequest {
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
}

fn find_write_request(
    connection: &Connection,
    request: &impl WriteIdentity,
) -> Result<Option<String>, BroadcastError> {
    Ok(connection
        .query_row(
            "SELECT response_json
             FROM manager_write_requests
             WHERE network = ?1 AND chain_id = ?2 AND operation = ?3
               AND idempotency_key = ?4",
            params![
                request.network(),
                request.chain_id(),
                request.operation(),
                request.idempotency_key()
            ],
            |row| row.get(0),
        )
        .optional()?)
}

fn read_review_upstream(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ReviewStatus, BroadcastError> {
    let review = read_review_status_on_connection(connection, network, chain_id)?;
    if !review.enabled {
        return Err(BroadcastError::MissingReviewState);
    }
    Ok(review)
}

fn validate_operation(network: &str, chain_id: i64) -> Result<(), BroadcastError> {
    if network.trim().is_empty() {
        return Err(BroadcastError::InvalidRequest(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(BroadcastError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    Ok(())
}

fn validate_submit_request(
    request: &BroadcastSubmitRequest,
    network: &str,
    chain_id: i64,
) -> Result<(), BroadcastError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(BroadcastError::InvalidRequest(
            "write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.operation.trim().is_empty() {
        return Err(BroadcastError::InvalidRequest(
            "operation must not be empty".to_owned(),
        ));
    }
    validate_identifier("request_id", &request.request_id, MAX_REQUEST_ID_LENGTH)?;
    validate_identifier(
        "idempotency_key",
        &request.idempotency_key,
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )?;
    if request.attempts.is_empty() {
        return Err(BroadcastError::InvalidRequest(
            "attempts must contain at least one item".to_owned(),
        ));
    }
    Ok(())
}

fn validate_finalize_request(
    request: &BroadcastFinalizeRequest,
    network: &str,
    chain_id: i64,
) -> Result<(), BroadcastError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(BroadcastError::InvalidRequest(
            "write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.operation.trim().is_empty() {
        return Err(BroadcastError::InvalidRequest(
            "operation must not be empty".to_owned(),
        ));
    }
    validate_identifier("request_id", &request.request_id, MAX_REQUEST_ID_LENGTH)?;
    validate_identifier(
        "idempotency_key",
        &request.idempotency_key,
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )?;
    if request.finalizations.is_empty() {
        return Err(BroadcastError::InvalidRequest(
            "finalizations must contain at least one item".to_owned(),
        ));
    }
    Ok(())
}

fn validate_identifier(name: &str, value: &str, max_length: usize) -> Result<(), BroadcastError> {
    if value.trim().is_empty() {
        return Err(BroadcastError::InvalidRequest(format!(
            "{name} must not be empty"
        )));
    }
    if value.len() > max_length {
        return Err(BroadcastError::InvalidRequest(format!(
            "{name} must not exceed {max_length} bytes"
        )));
    }
    Ok(())
}

fn validate_submit_attempts(attempts: &[AttemptCreateInput]) -> Result<(), BroadcastError> {
    let mut seen = BTreeSet::new();
    for attempt in attempts {
        if attempt.loop_key.trim().is_empty() {
            return Err(BroadcastError::InvalidRequest(
                "attempt.loopKey must not be empty".to_owned(),
            ));
        }
        if attempt.nonce < 0 {
            return Err(BroadcastError::InvalidRequest(
                "attempt.nonce must be greater than or equal to zero".to_owned(),
            ));
        }
        normalize_address(&attempt.signer_address).map_err(|_| {
            BroadcastError::InvalidRequest(
                "attempt.signerAddress is not a valid address".to_owned(),
            )
        })?;
        validate_gas_field("gasLimit", attempt.gas_limit, None)?;
        validate_wei("gasPriceWei", attempt.gas_price_wei.as_deref())?;
        validate_wei("maxFeePerGasWei", attempt.max_fee_per_gas_wei.as_deref())?;
        validate_wei(
            "maxPriorityFeePerGasWei",
            attempt.max_priority_fee_per_gas_wei.as_deref(),
        )?;
        validate_rpc_url(&attempt.rpc_url)?;
        if let Some(attempt_id) = attempt.attempt_id.as_deref() {
            validate_identifier("attemptId", attempt_id, MAX_IDENTIFIER_LENGTH)?;
            if !seen.insert(attempt_id.to_owned()) {
                return Err(BroadcastError::DuplicateAttemptInput(attempt_id.to_owned()));
            }
        }
    }
    Ok(())
}

fn validate_gas_field(
    name: &str,
    value: Option<i64>,
    _unused: Option<()>,
) -> Result<(), BroadcastError> {
    if value.is_some_and(|value| value < 0) {
        return Err(BroadcastError::InvalidRequest(format!(
            "{name} must be greater than or equal to zero"
        )));
    }
    Ok(())
}

fn validate_wei(name: &str, value: Option<&str>) -> Result<(), BroadcastError> {
    if let Some(value) = value {
        let value = value.trim();
        if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(BroadcastError::InvalidRequest(format!(
                "{name} must be a non-negative decimal integer"
            )));
        }
    }
    Ok(())
}

fn validate_rpc_url(value: &str) -> Result<(), BroadcastError> {
    let value = value.trim();
    if value.len() > MAX_RPC_URL_LENGTH
        || !(value.starts_with("http://") || value.starts_with("https://"))
    {
        return Err(BroadcastError::InvalidRequest(
            "rpcUrl must be an http(s) URL and must not exceed 2048 bytes".to_owned(),
        ));
    }
    Ok(())
}

fn build_attempt_rows(
    review: &ReviewStatus,
    requests: &[BroadcastRequestRow],
    attempts: &[AttemptCreateInput],
    now: &str,
) -> Result<Vec<AttemptRow>, BroadcastError> {
    validate_submit_attempts(attempts)?;
    let requests_by_loop = requests
        .iter()
        .map(|request| (request.loop_key.as_str(), request))
        .collect::<BTreeMap<_, _>>();
    let mut rows = Vec::with_capacity(attempts.len());
    let mut seen_ids = BTreeSet::new();
    for attempt in attempts {
        let request = requests_by_loop
            .get(attempt.loop_key.as_str())
            .ok_or_else(|| BroadcastError::UnknownRequest(attempt.loop_key.clone()))?;
        if request.request_status != "generated" && request.request_status != "confirmed" {
            return Err(BroadcastError::BroadcastRequestNotEligible {
                loop_key: request.loop_key.clone(),
                request_status: request.request_status.clone(),
            });
        }
        if !validate_signed_pnl(&request.signed_pnl) {
            return Err(BroadcastError::InvalidRequest(format!(
                "confirmed request {} has incomplete signed PnL",
                request.loop_key
            )));
        }
        if request.signed_pnl.amount <= 0.0 {
            return Err(BroadcastError::BroadcastRequestNotEligible {
                loop_key: request.loop_key.clone(),
                request_status: format!("non_positive_pnl:{}", request.signed_pnl.amount),
            });
        }
        let attempt_id = attempt_id_for(attempt, review.review_version)?;
        if !seen_ids.insert(attempt_id.clone()) {
            return Err(BroadcastError::DuplicateAttemptInput(attempt_id));
        }
        let signer_address = normalize_address(&attempt.signer_address).map_err(|_| {
            BroadcastError::InvalidRequest(
                "attempt.signerAddress is not a valid address".to_owned(),
            )
        })?;
        rows.push(AttemptRow {
            attempt_id,
            loop_key: request.loop_key.clone(),
            closed_loop_version: request.closed_loop_version,
            closed_loop_checksum: request.closed_loop_checksum.clone(),
            review_version: review.review_version,
            review_checksum: required_upstream_checksum(
                review.materialized_checksum.as_deref(),
                "review_checksum",
            )?,
            anchor_token_key_id: request.anchor_token_key_id.clone(),
            path_text: request.path_text.clone(),
            path_addresses: request.path_addresses.clone(),
            path_symbols: request.path_symbols.clone(),
            pool_path_key: request.pool_path_key.clone(),
            pool_ids: request.pool_ids.clone(),
            signed_pnl: request.signed_pnl.clone(),
            signer_address,
            nonce: attempt.nonce,
            gas_limit: attempt.gas_limit,
            gas_price_wei: attempt.gas_price_wei.clone(),
            max_fee_per_gas_wei: attempt.max_fee_per_gas_wei.clone(),
            max_priority_fee_per_gas_wei: attempt.max_priority_fee_per_gas_wei.clone(),
            rpc_url: attempt.rpc_url.trim().to_owned(),
            rpc_name: attempt.rpc_name.clone(),
            tx_hash: None,
            status: "pending".to_owned(),
            status_reason: None,
            receipt: serde_json::Value::Object(Default::default()),
            evidence: normalize_object(&attempt.evidence),
            created_at: now.to_owned(),
            updated_at: now.to_owned(),
            terminal_at: None,
        });
    }
    rows.sort_by(|left, right| left.attempt_id.cmp(&right.attempt_id));
    Ok(rows)
}

fn attempt_id_for(
    attempt: &AttemptCreateInput,
    review_version: i64,
) -> Result<String, BroadcastError> {
    if let Some(attempt_id) = attempt.attempt_id.as_deref() {
        let attempt_id = attempt_id.trim();
        validate_identifier("attemptId", attempt_id, MAX_IDENTIFIER_LENGTH)?;
        return Ok(attempt_id.to_owned());
    }
    Ok(format!(
        "{}:{}:{}:{}",
        BROADCAST_SCOPE_KEY, attempt.loop_key, review_version, attempt.nonce
    ))
}

fn validate_no_attempt_conflicts(
    existing: &[AttemptRow],
    desired: &[AttemptRow],
) -> Result<(), BroadcastError> {
    let existing_ids = existing
        .iter()
        .map(|row| row.attempt_id.as_str())
        .collect::<BTreeSet<_>>();
    for row in desired {
        if existing_ids.contains(row.attempt_id.as_str()) {
            return Err(BroadcastError::DuplicateAttempt(row.attempt_id.clone()));
        }
    }
    Ok(())
}

fn validate_finalization_identity(
    finalizations: &[AttemptFinalizeInput],
) -> Result<(), BroadcastError> {
    let mut seen = BTreeSet::new();
    for finalization in finalizations {
        validate_identifier("attemptId", &finalization.attempt_id, MAX_IDENTIFIER_LENGTH)?;
        if !seen.insert(finalization.attempt_id.as_str()) {
            return Err(BroadcastError::DuplicateAttemptInput(
                finalization.attempt_id.clone(),
            ));
        }
    }
    Ok(())
}

fn validate_finalization_input(finalization: &AttemptFinalizeInput) -> Result<(), BroadcastError> {
    validate_identifier("attemptId", &finalization.attempt_id, MAX_IDENTIFIER_LENGTH)?;
    match finalization.status {
        BroadcastTerminalStatus::Success => {
            if finalization
                .tx_hash
                .as_deref()
                .map(str::trim)
                .is_none_or(str::is_empty)
            {
                return Err(BroadcastError::InvalidRequest(
                    "success finalization requires txHash".to_owned(),
                ));
            }
        }
        BroadcastTerminalStatus::Failed => {}
    }
    if let Some(tx_hash) = finalization.tx_hash.as_deref() {
        if tx_hash.trim().is_empty() {
            return Err(BroadcastError::InvalidRequest(
                "txHash must not be empty when present".to_owned(),
            ));
        }
    }
    for diagnostic in &finalization.diagnostics {
        validate_identifier(
            "diagnostic.diagnosticType",
            diagnostic.diagnostic_type.as_str(),
            MAX_IDENTIFIER_LENGTH,
        )?;
        if let Some(rpc_url) = diagnostic.rpc_url.as_deref() {
            validate_rpc_url(rpc_url)?;
        }
    }
    Ok(())
}

fn chronological_finalized_at(value: &Option<String>, now: &str) -> Result<String, BroadcastError> {
    Ok(value
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(now)
        .to_owned())
}

fn build_diagnostics(
    attempt: &AttemptRow,
    inputs: &[BroadcastDiagnosticInput],
) -> Result<Vec<BroadcastDiagnosticRow>, BroadcastError> {
    let mut rows = Vec::with_capacity(inputs.len());
    let mut seen = BTreeSet::new();
    for input in inputs {
        let diagnostic_id = input
            .diagnostic_id
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| {
                let mut identity = serde_json::Map::new();
                identity.insert(
                    "attemptId".to_owned(),
                    serde_json::Value::String(attempt.attempt_id.clone()),
                );
                identity.insert(
                    "type".to_owned(),
                    serde_json::Value::String(input.diagnostic_type.as_str().to_owned()),
                );
                identity.insert(
                    "code".to_owned(),
                    option_string(input.diagnostic_code.clone()),
                );
                identity.insert("rpcUrl".to_owned(), option_string(input.rpc_url.clone()));
                identity.insert("txHash".to_owned(), option_string(input.tx_hash.clone()));
                identity.insert("payload".to_owned(), normalize_object(&input.payload));
                checksum_bytes(&serde_json::to_vec(&identity).unwrap_or_default())
            });
        if !seen.insert(diagnostic_id.clone()) {
            return Err(BroadcastError::DuplicateDiagnostic {
                attempt_id: attempt.attempt_id.clone(),
                diagnostic_id,
            });
        }
        rows.push(BroadcastDiagnosticRow {
            attempt_id: attempt.attempt_id.clone(),
            loop_key: attempt.loop_key.clone(),
            diagnostic_id,
            diagnostic_type: input.diagnostic_type.as_str().to_owned(),
            diagnostic_code: input.diagnostic_code.clone(),
            message: input.message.clone(),
            rpc_url: input
                .rpc_url
                .clone()
                .or_else(|| (attempt.status == "failed").then(|| attempt.rpc_url.clone())),
            tx_hash: input.tx_hash.clone().or_else(|| attempt.tx_hash.clone()),
            payload: normalize_object(&input.payload),
            observed_at: input
                .observed_at
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .unwrap_or("unreported")
                .to_owned(),
        });
    }
    Ok(rows)
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
    actual: &ReviewStatus,
) -> Result<(), BroadcastError> {
    compare_optional_version(
        expected.review_version,
        actual.review_version,
        "review_version",
    )?;
    compare_optional_string(
        expected.review_checksum.as_deref(),
        actual.materialized_checksum.as_deref(),
        "review_checksum",
    )?;
    compare_optional_version(
        expected.closed_loop_version,
        actual.closed_loop_version.unwrap_or_default(),
        "closed_loop_version",
    )?;
    compare_optional_string(
        expected.closed_loop_checksum.as_deref(),
        actual.closed_loop_checksum.as_deref(),
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
            return Err(BroadcastError::ExpectedUpstreamMismatch {
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
            return Err(BroadcastError::ExpectedUpstreamMismatch {
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
) -> Result<(), BroadcastError> {
    if let Some(expected) = expected {
        if expected != actual {
            return Err(BroadcastError::ExpectedUpstreamMismatch {
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
) -> Result<(), BroadcastError> {
    if let Some(expected) = expected {
        let actual = actual.unwrap_or_default();
        if expected != actual {
            return Err(BroadcastError::ExpectedUpstreamMismatch {
                field: field.to_owned(),
                expected: expected.to_owned(),
                actual: actual.to_owned(),
            });
        }
    }
    Ok(())
}

fn submit_request_digest(request: &BroadcastSubmitRequest) -> Result<String, BroadcastError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream,
        "attempts": request.attempts
    });
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn finalize_request_digest(request: &BroadcastFinalizeRequest) -> Result<String, BroadcastError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream,
        "finalizations": request.finalizations
    });
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn clear_request_digest(request: &BroadcastClearRequest) -> Result<String, BroadcastError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply
    });
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn count_scoped_rows(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
    predicate: Option<&str>,
) -> Result<usize, BroadcastError> {
    let sql = format!(
        "SELECT COUNT(*) FROM {table}
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4{}",
        predicate
            .map(|value| format!(" AND ({value})"))
            .unwrap_or_default()
    );
    let count: i64 = connection.query_row(
        &sql,
        params![network, chain_id, NETWORK_PROTOCOL, BROADCAST_SCOPE_KEY],
        |row| row.get(0),
    )?;
    Ok(usize_from_i64(count))
}

#[derive(Default)]
struct AttemptCounts {
    attempts: usize,
    pending: usize,
    success: usize,
    failed: usize,
    terminal: usize,
}

fn attempt_counts(rows: &[AttemptRow]) -> AttemptCounts {
    let mut counts = AttemptCounts {
        attempts: rows.len(),
        ..AttemptCounts::default()
    };
    for row in rows {
        match row.status.as_str() {
            "pending" => counts.pending += 1,
            "success" => {
                counts.success += 1;
                counts.terminal += 1;
            }
            "failed" => {
                counts.failed += 1;
                counts.terminal += 1;
            }
            _ => {}
        }
    }
    counts
}

fn broadcast_checksum(
    review: &ReviewStatus,
    attempts: &[AttemptRow],
) -> Result<String, BroadcastError> {
    let review_checksum =
        required_upstream_checksum(review.materialized_checksum.as_deref(), "review_checksum")?;
    let closed_loop_checksum = required_upstream_checksum(
        review.closed_loop_checksum.as_deref(),
        "closed_loop_checksum",
    )?;
    let payload = BroadcastChecksumPayload {
        review_version: review.review_version,
        review_checksum: &review_checksum,
        closed_loop_version: required_upstream_version(
            review.closed_loop_version,
            "closed_loop_version",
        )?,
        closed_loop_checksum: &closed_loop_checksum,
        attempts,
    };
    Ok(checksum_bytes(&serde_json::to_vec(&payload)?))
}

fn required_upstream_version(value: Option<i64>, field: &str) -> Result<i64, BroadcastError> {
    value.ok_or_else(|| BroadcastError::InvalidRequest(format!("{field} is missing")))
}

fn required_upstream_checksum(value: Option<&str>, field: &str) -> Result<String, BroadcastError> {
    value
        .map(str::to_owned)
        .ok_or_else(|| BroadcastError::InvalidRequest(format!("{field} is missing")))
}

fn open_connection(path: &Path, writable: bool) -> Result<Connection, BroadcastError> {
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
) -> Result<BroadcastStatus, BroadcastError> {
    let stored = connection
        .query_row(
            "SELECT broadcast_version, materialized_checksum, review_version,
                    review_checksum, closed_loop_version, closed_loop_checksum,
                    request_count, attempt_count, pending_count, success_count,
                    failed_count, terminal_count
             FROM manager_broadcast_library_state
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4",
            params![network, chain_id, NETWORK_PROTOCOL, BROADCAST_SCOPE_KEY],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, i64>(7)?,
                    row.get::<_, i64>(8)?,
                    row.get::<_, i64>(9)?,
                    row.get::<_, i64>(10)?,
                    row.get::<_, i64>(11)?,
                ))
            },
        )
        .optional()?;
    let Some((
        broadcast_version,
        materialized_checksum,
        review_version,
        review_checksum,
        closed_loop_version,
        closed_loop_checksum,
        request_count,
        attempt_count,
        pending_count,
        success_count,
        failed_count,
        terminal_count,
    )) = stored
    else {
        return Ok(BroadcastStatus {
            enabled: false,
            network: network.to_owned(),
            chain_id,
            protocol: NETWORK_PROTOCOL.to_owned(),
            scope_key: BROADCAST_SCOPE_KEY.to_owned(),
            broadcast_version: 0,
            materialized_checksum: None,
            review_version: None,
            review_checksum: None,
            closed_loop_version: None,
            closed_loop_checksum: None,
            request_count: 0,
            attempt_count: 0,
            pending_count: 0,
            success_count: 0,
            failed_count: 0,
            terminal_count: 0,
        });
    };
    Ok(BroadcastStatus {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: BROADCAST_SCOPE_KEY.to_owned(),
        broadcast_version,
        materialized_checksum: Some(materialized_checksum),
        review_version: Some(review_version),
        review_checksum: Some(review_checksum),
        closed_loop_version: Some(closed_loop_version),
        closed_loop_checksum: Some(closed_loop_checksum),
        request_count: usize_from_i64(request_count),
        attempt_count: usize_from_i64(attempt_count),
        pending_count: usize_from_i64(pending_count),
        success_count: usize_from_i64(success_count),
        failed_count: usize_from_i64(failed_count),
        terminal_count: usize_from_i64(terminal_count),
    })
}

fn read_attempts(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    attempt_id: Option<&str>,
    status: Option<&str>,
    offset: usize,
    limit: usize,
) -> Result<Vec<AttemptRow>, BroadcastError> {
    let mut statement = connection.prepare(
        "SELECT attempt_id, loop_key, closed_loop_version, closed_loop_checksum,
                review_version, review_checksum, anchor_token_key_id, path_text,
                path_addresses_json, path_symbols_json, pool_path_key, pool_ids_json,
                signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                signer_address, nonce, gas_limit, gas_price_wei,
                max_fee_per_gas_wei, max_priority_fee_per_gas_wei, rpc_url,
                rpc_name, tx_hash, status, status_reason, receipt_json,
                evidence_json, created_at, updated_at, terminal_at
         FROM manager_cow_execution_attempts
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR attempt_id = ?5)
           AND (?6 IS NULL OR status = ?6)
         ORDER BY attempt_id
         LIMIT ?7 OFFSET ?8",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            BROADCAST_SCOPE_KEY,
            attempt_id,
            status,
            sql_limit(limit),
            sql_limit(offset)
        ],
        |row| {
            Ok(AttemptRow {
                attempt_id: row.get(0)?,
                loop_key: row.get(1)?,
                closed_loop_version: row.get(2)?,
                closed_loop_checksum: row.get(3)?,
                review_version: row.get(4)?,
                review_checksum: row.get(5)?,
                anchor_token_key_id: row.get(6)?,
                path_text: row.get(7)?,
                path_addresses: parse_json_column(row.get(8)?)?,
                path_symbols: parse_json_column(row.get(9)?)?,
                pool_path_key: row.get(10)?,
                pool_ids: parse_json_column(row.get(11)?)?,
                signed_pnl: SignedPnl {
                    raw: row.get(12)?,
                    amount: row.get(13)?,
                    currency: row.get(14)?,
                    address: row.get(15)?,
                    unit: row.get(16)?,
                    source: row.get(17)?,
                },
                signer_address: row.get(18)?,
                nonce: row.get(19)?,
                gas_limit: row.get(20)?,
                gas_price_wei: row.get(21)?,
                max_fee_per_gas_wei: row.get(22)?,
                max_priority_fee_per_gas_wei: row.get(23)?,
                rpc_url: row.get(24)?,
                rpc_name: row.get(25)?,
                tx_hash: row.get(26)?,
                status: row.get(27)?,
                status_reason: row.get(28)?,
                receipt: parse_json_value(row.get(29)?)?,
                evidence: parse_json_value(row.get(30)?)?,
                created_at: row.get(31)?,
                updated_at: row.get(32)?,
                terminal_at: row.get(33)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_diagnostics(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    attempt_id: Option<&str>,
    status: Option<&str>,
    limit: usize,
) -> Result<Vec<BroadcastDiagnosticRow>, BroadcastError> {
    let mut statement = connection.prepare(
        "SELECT d.attempt_id, d.loop_key, d.diagnostic_id, d.diagnostic_type,
                d.diagnostic_code, d.message, d.rpc_url, d.tx_hash, d.payload_json,
                d.observed_at
         FROM manager_broadcast_attempt_diagnostics d
         WHERE d.network = ?1 AND d.chain_id = ?2 AND d.protocol = ?3
           AND d.scope_key = ?4
           AND (?5 IS NULL OR d.attempt_id = ?5)
           AND (?6 IS NULL OR EXISTS (
               SELECT 1
               FROM manager_cow_execution_attempts a
               WHERE a.network = d.network
                 AND a.chain_id = d.chain_id
                 AND a.protocol = d.protocol
                 AND a.scope_key = d.scope_key
                 AND a.attempt_id = d.attempt_id
                 AND a.status = ?6
           ))
         ORDER BY d.attempt_id, d.diagnostic_id
         LIMIT ?7",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            BROADCAST_SCOPE_KEY,
            attempt_id,
            status,
            sql_limit(limit)
        ],
        |row| {
            Ok(BroadcastDiagnosticRow {
                attempt_id: row.get(0)?,
                loop_key: row.get(1)?,
                diagnostic_id: row.get(2)?,
                diagnostic_type: row.get(3)?,
                diagnostic_code: row.get(4)?,
                message: row.get(5)?,
                rpc_url: row.get(6)?,
                tx_hash: row.get(7)?,
                payload: parse_json_value(row.get(8)?)?,
                observed_at: row.get(9)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn insert_attempt(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    row: &AttemptRow,
) -> Result<(), BroadcastError> {
    connection.execute(
        "INSERT INTO manager_cow_execution_attempts (
             network, chain_id, protocol, scope_key, attempt_id, loop_key,
             closed_loop_version, closed_loop_checksum, review_version,
             review_checksum, anchor_token_key_id, path_text,
             path_addresses_json, path_symbols_json, pool_path_key,
             pool_ids_json, signed_pnl_raw, signed_pnl_amount,
             signed_pnl_currency, signed_pnl_address, signed_pnl_unit,
             signed_pnl_source, signer_address, nonce, gas_limit,
             gas_price_wei, max_fee_per_gas_wei, max_priority_fee_per_gas_wei,
             rpc_url, rpc_name, tx_hash, status, status_reason, receipt_json,
             evidence_json, created_at, updated_at, terminal_at
         ) VALUES (
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,
             ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20,
             ?21, ?22, ?23, ?24, ?25, ?26, ?27, ?28, ?29, ?30,
             ?31, ?32, ?33, ?34, ?35, ?36, ?37, ?38
         )",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            BROADCAST_SCOPE_KEY,
            row.attempt_id,
            row.loop_key,
            row.closed_loop_version,
            row.closed_loop_checksum,
            row.review_version,
            row.review_checksum,
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
            row.signer_address,
            row.nonce,
            row.gas_limit,
            row.gas_price_wei,
            row.max_fee_per_gas_wei,
            row.max_priority_fee_per_gas_wei,
            row.rpc_url,
            row.rpc_name,
            row.tx_hash,
            row.status,
            row.status_reason,
            serde_json::to_string(&row.receipt)?,
            serde_json::to_string(&row.evidence)?,
            row.created_at,
            row.updated_at,
            row.terminal_at
        ],
    )?;
    Ok(())
}

fn update_attempt_terminal(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    finalization: &AttemptFinalizeInput,
) -> Result<(), BroadcastError> {
    let now = chrono::Utc::now().to_rfc3339();
    let terminal_at = chronological_finalized_at(&finalization.finalized_at, &now)?;
    connection.execute(
        "UPDATE manager_cow_execution_attempts
         SET tx_hash = ?6,
             status = ?7,
             status_reason = ?8,
             receipt_json = ?9,
             evidence_json = ?10,
             updated_at = ?11,
             terminal_at = ?12
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND scope_key = ?4 AND attempt_id = ?5",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            BROADCAST_SCOPE_KEY,
            finalization.attempt_id,
            finalization.tx_hash,
            finalization.status.as_str(),
            finalization
                .status_reason
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty()),
            serde_json::to_string(&normalize_object(&finalization.receipt))?,
            serde_json::to_string(&normalize_object(&finalization.evidence))?,
            terminal_at,
            terminal_at
        ],
    )?;
    Ok(())
}

fn insert_diagnostic(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    row: &BroadcastDiagnosticRow,
) -> Result<(), BroadcastError> {
    connection.execute(
        "INSERT INTO manager_broadcast_attempt_diagnostics (
             network, chain_id, protocol, scope_key, attempt_id, loop_key,
             diagnostic_id, diagnostic_type, diagnostic_code, message,
             rpc_url, tx_hash, payload_json, observed_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            BROADCAST_SCOPE_KEY,
            row.attempt_id,
            row.loop_key,
            row.diagnostic_id,
            row.diagnostic_type,
            row.diagnostic_code,
            row.message,
            row.rpc_url,
            row.tx_hash,
            serde_json::to_string(&row.payload)?,
            row.observed_at
        ],
    )?;
    Ok(())
}

fn upsert_broadcast_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    result: &BroadcastMaterializationResult,
    now: &str,
) -> Result<(), BroadcastError> {
    connection.execute(
        "INSERT INTO manager_broadcast_library_state (
             network, chain_id, protocol, scope_key, broadcast_version,
             materialized_checksum, review_version, review_checksum,
             closed_loop_version, closed_loop_checksum, request_count,
             attempt_count, pending_count, success_count, failed_count,
             terminal_count, generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11,
                   ?12, ?13, ?14, ?15, ?16, ?17, ?18)
         ON CONFLICT(network, chain_id, protocol, scope_key) DO UPDATE SET
             broadcast_version = excluded.broadcast_version,
             materialized_checksum = excluded.materialized_checksum,
             review_version = excluded.review_version,
             review_checksum = excluded.review_checksum,
             closed_loop_version = excluded.closed_loop_version,
             closed_loop_checksum = excluded.closed_loop_checksum,
             request_count = excluded.request_count,
             attempt_count = excluded.attempt_count,
             pending_count = excluded.pending_count,
             success_count = excluded.success_count,
             failed_count = excluded.failed_count,
             terminal_count = excluded.terminal_count,
             generated_at = excluded.generated_at,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            BROADCAST_SCOPE_KEY,
            result.broadcast_version,
            result.materialized_checksum,
            result.review_version,
            result.review_checksum,
            result.closed_loop_version,
            result.closed_loop_checksum,
            result.request_count as i64,
            result.attempt_count as i64,
            result.pending_count as i64,
            result.success_count as i64,
            result.failed_count as i64,
            result.terminal_count as i64,
            now,
            now
        ],
    )?;
    Ok(())
}

fn persist_submit_write(
    connection: &Connection,
    request: &BroadcastSubmitRequest,
    request_digest: &str,
    result: &BroadcastMaterializationResult,
) -> Result<(), BroadcastError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream,
        "attempts": request.attempts
    });
    let response_json = serde_json::to_string(&PersistedSubmitWrite {
        request_id: request.request_id.clone(),
        request_digest: request_digest.to_owned(),
        result: result.clone(),
    })?;
    insert_write_request(
        connection,
        request.network.as_str(),
        request.chain_id,
        request.operation.as_str(),
        request.idempotency_key.as_str(),
        request.request_id.as_str(),
        request_digest,
        &request_json.to_string(),
        &response_json,
    )
}

fn persist_finalize_write(
    connection: &Connection,
    request: &BroadcastFinalizeRequest,
    request_digest: &str,
    result: &BroadcastMaterializationResult,
) -> Result<(), BroadcastError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream,
        "finalizations": request.finalizations
    });
    let response_json = serde_json::to_string(&PersistedFinalizeWrite {
        request_id: request.request_id.clone(),
        request_digest: request_digest.to_owned(),
        result: result.clone(),
    })?;
    insert_write_request(
        connection,
        request.network.as_str(),
        request.chain_id,
        request.operation.as_str(),
        request.idempotency_key.as_str(),
        request.request_id.as_str(),
        request_digest,
        &request_json.to_string(),
        &response_json,
    )
}

fn persist_clear_write(
    connection: &Connection,
    request: &BroadcastClearRequest,
    request_digest: &str,
    result: &BroadcastClearResult,
) -> Result<(), BroadcastError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply
    });
    let response_json = serde_json::to_string(&PersistedClearWrite {
        request_id: request.request_id.clone(),
        request_digest: request_digest.to_owned(),
        result: result.clone(),
    })?;
    insert_write_request(
        connection,
        request.network.as_str(),
        request.chain_id,
        request.operation.as_str(),
        request.idempotency_key.as_str(),
        request.request_id.as_str(),
        request_digest,
        &request_json.to_string(),
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
) -> Result<(), BroadcastError> {
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

fn option_string(value: Option<String>) -> serde_json::Value {
    value.map_or(serde_json::Value::Null, serde_json::Value::String)
}

fn checksum_bytes(value: &[u8]) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(value)))
}

fn usize_from_i64(value: i64) -> usize {
    usize::try_from(value.max(0)).unwrap_or(usize::MAX)
}

fn sql_limit(value: usize) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

#[cfg(test)]
mod tests {
    use super::{
        clear_broadcast_records_write, finalize_broadcast_attempts,
        finalize_broadcast_attempts_write, read_broadcast_snapshot, read_broadcast_snapshot_page,
        submit_broadcasts, submit_broadcasts_write, AttemptCreateInput, AttemptFinalizeInput,
        BroadcastClearRequest, BroadcastDiagnosticInput, BroadcastDiagnosticType, BroadcastError,
        BroadcastFinalizeRequest, BroadcastSubmitRequest, BroadcastTerminalStatus,
        ExpectedUpstream,
    };
    use rusqlite::{params, Connection};

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const LOOP_KEY: &str = "loop-1";
    const SIGNER: &str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TX_HASH: &str = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "INSERT INTO manager_review_library_state (
                    network, chain_id, protocol, scope_key, review_version,
                    materialized_checksum, closed_loop_version,
                    closed_loop_checksum, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, pool_scope_checksum,
                    library_versions_json, library_checksums_json, loop_count,
                    reviewed_count, completed_count, data_incomplete_count,
                    not_executable_count, failed_count, broadcast_request_count,
                    generated_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', 9, 'sha256:review',
                           7, 'sha256:closed-loop', 5, 'sha256:candidate-edge',
                           3, 'sha256:observation', 'sha256:pool-scope',
                           '{\"core\":3}', '{\"core\":\"sha256:core\"}',
                           1, 1, 1, 0, 0, 0, 1, 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_review_broadcast_requests (
                    network, chain_id, protocol, scope_key, loop_key,
                    closed_loop_version, closed_loop_checksum,
                    anchor_token_key_id, path_text, path_addresses_json,
                    path_symbols_json, pool_path_key, pool_ids_json,
                    signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                    signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                    request_status, request_reason, created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 7,
                           'sha256:closed-loop',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           'USDG -> AMZN -> TA -> USDG',
                           '[\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x3333333333333333333333333333333333333333\",\"0x1111111111111111111111111111111111111111\"]',
                           '[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]', 'pool-path:1',
                           '[\"pool-a\",\"pool-yz\",\"pool-d\"]',
                           '12.5 USDG', 12.5, 'USDG',
                           '0x1111111111111111111111111111111111111111',
                           'token', 'runArbitrage', 'generated',
                           'positive executable signed PnL', 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID, LOOP_KEY],
            )
            .unwrap();
        drop(connection);
        (directory, path)
    }

    fn attempt(attempt_id: Option<&str>, nonce: i64) -> AttemptCreateInput {
        AttemptCreateInput {
            attempt_id: attempt_id.map(str::to_owned),
            loop_key: LOOP_KEY.to_owned(),
            signer_address: SIGNER.to_owned(),
            nonce,
            gas_limit: Some(500_000),
            gas_price_wei: Some("1000000000".to_owned()),
            max_fee_per_gas_wei: None,
            max_priority_fee_per_gas_wei: None,
            rpc_url: "https://rpc.example.invalid".to_owned(),
            rpc_name: Some("fixture-primary".to_owned()),
            evidence: serde_json::json!({"source": "fixture"}),
        }
    }

    fn expected(path: &std::path::Path) -> ExpectedUpstream {
        let status = review_manager::read_review_status(path, NETWORK, CHAIN_ID).unwrap();
        ExpectedUpstream {
            review_version: Some(status.review_version),
            review_checksum: status.materialized_checksum,
            closed_loop_version: status.closed_loop_version,
            closed_loop_checksum: status.closed_loop_checksum,
            candidate_edge_version: status.candidate_edge_version,
            candidate_edge_checksum: status.candidate_edge_checksum,
            observation_version: status.observation_version,
            observation_checksum: status.observation_checksum,
            pool_scope_checksum: status.pool_scope_checksum,
            library_versions: status.library_versions,
            library_checksums: status.library_checksums,
        }
    }

    fn submit_request(
        path: &std::path::Path,
        request_id: &str,
        idempotency_key: &str,
        apply: bool,
    ) -> BroadcastSubmitRequest {
        BroadcastSubmitRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "broadcasts.submit".to_owned(),
            request_id: request_id.to_owned(),
            idempotency_key: idempotency_key.to_owned(),
            apply,
            expected_upstream: expected(path),
            attempts: vec![attempt(Some("attempt-1"), 42)],
        }
    }

    fn failed_finalization() -> AttemptFinalizeInput {
        AttemptFinalizeInput {
            attempt_id: "attempt-1".to_owned(),
            status: BroadcastTerminalStatus::Failed,
            tx_hash: Some(TX_HASH.to_owned()),
            receipt: serde_json::json!({"status": "0x0"}),
            status_reason: Some("rpc rejected transaction".to_owned()),
            diagnostics: vec![BroadcastDiagnosticInput {
                diagnostic_id: Some("diag-1".to_owned()),
                diagnostic_type: BroadcastDiagnosticType::RpcUnavailable,
                diagnostic_code: Some("RPC_TIMEOUT".to_owned()),
                message: Some("primary RPC timed out".to_owned()),
                rpc_url: Some("https://rpc.example.invalid".to_owned()),
                tx_hash: Some(TX_HASH.to_owned()),
                payload: serde_json::json!({"attempt": 1}),
                observed_at: Some("2026-11-04T01:00:00Z".to_owned()),
            }],
            evidence: serde_json::json!({"receipt": "timeout"}),
            finalized_at: Some("2026-11-04T01:00:01Z".to_owned()),
        }
    }

    fn failed_finalization_for(attempt_id: &str) -> AttemptFinalizeInput {
        let mut finalization = failed_finalization();
        finalization.attempt_id = attempt_id.to_owned();
        finalization.diagnostics[0].diagnostic_id = Some(format!("diag-{attempt_id}"));
        finalization
    }

    #[test]
    fn submit_dry_run_is_non_persistent_and_apply_is_idempotent() {
        let (_directory, path) = database();
        let dry =
            submit_broadcasts_write(&path, submit_request(&path, "dry", "idem-1", false)).unwrap();
        assert!(dry.result.dry_run);
        assert_eq!(dry.result.pending_count, 1);
        assert!(!dry.idempotent_replay);

        let connection = Connection::open(&path).unwrap();
        let attempt_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let request_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(attempt_count, 0);
        assert_eq!(request_count, 0);
        drop(connection);

        let first =
            submit_broadcasts_write(&path, submit_request(&path, "apply", "idem-1", true)).unwrap();
        assert_eq!(first.result.pending_count, 1);
        assert!(!first.idempotent_replay);
        let replay =
            submit_broadcasts_write(&path, submit_request(&path, "replay", "idem-1", true))
                .unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, first.result);

        let snapshot = read_broadcast_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.attempts.len(), 1);
        assert_eq!(snapshot.attempts[0].status, "pending");
        assert_eq!(snapshot.attempts[0].nonce, 42);
        assert_eq!(snapshot.status.pending_count, 1);
    }

    #[test]
    fn failed_finalization_preserves_rpc_nonce_hash_and_diagnostic() {
        let (_directory, path) = database();
        submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![attempt(Some("attempt-1"), 42)],
            true,
        )
        .unwrap();
        let result = finalize_broadcast_attempts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![failed_finalization()],
            true,
        )
        .unwrap();
        assert_eq!(result.pending_count, 0);
        assert_eq!(result.failed_count, 1);
        assert_eq!(result.terminal_count, 1);

        let snapshot = read_broadcast_snapshot(&path, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.attempts[0].status, "failed");
        assert_eq!(snapshot.attempts[0].nonce, 42);
        assert_eq!(snapshot.attempts[0].tx_hash.as_deref(), Some(TX_HASH));
        assert_eq!(snapshot.attempts[0].rpc_url, "https://rpc.example.invalid");
        assert_eq!(snapshot.diagnostics.len(), 1);
        assert_eq!(snapshot.diagnostics[0].diagnostic_type, "rpc_unavailable");
        assert_eq!(snapshot.diagnostics[0].tx_hash.as_deref(), Some(TX_HASH));
    }

    #[test]
    fn clear_is_dry_run_safe_idempotent_and_rejects_pending_work() {
        let (_directory, path) = database();
        submit_broadcasts(&path, NETWORK, CHAIN_ID, vec![attempt(None, 42)], true).unwrap();
        let pending_clear = clear_broadcast_records_write(
            &path,
            BroadcastClearRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "broadcasts.clear".to_owned(),
                request_id: "clear-pending".to_owned(),
                idempotency_key: "clear-pending".to_owned(),
                apply: true,
            },
        );
        assert!(matches!(
            pending_clear.unwrap_err(),
            BroadcastError::InvalidRequest(_)
        ));
        finalize_broadcast_attempts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![failed_finalization_for("core4:loop-1:9:42")],
            true,
        )
        .unwrap();

        let dry = clear_broadcast_records_write(
            &path,
            BroadcastClearRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "broadcasts.clear".to_owned(),
                request_id: "clear-dry".to_owned(),
                idempotency_key: "clear-dry".to_owned(),
                apply: false,
            },
        )
        .unwrap();
        assert!(dry.result.dry_run);
        assert_eq!(dry.result.cleared_attempts, 1);
        assert_eq!(dry.result.remaining_attempts, 1);
        assert_eq!(
            read_broadcast_snapshot(&path, NETWORK, CHAIN_ID, None, 100)
                .unwrap()
                .attempts
                .len(),
            1
        );

        let applied = clear_broadcast_records_write(
            &path,
            BroadcastClearRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "broadcasts.clear".to_owned(),
                request_id: "clear-apply".to_owned(),
                idempotency_key: "clear-apply".to_owned(),
                apply: true,
            },
        )
        .unwrap();
        assert_eq!(applied.result.cleared_attempts, 1);
        assert_eq!(applied.result.remaining_attempts, 0);
        assert!(!applied.idempotent_replay);

        let replay = clear_broadcast_records_write(
            &path,
            BroadcastClearRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "broadcasts.clear".to_owned(),
                request_id: "clear-replay".to_owned(),
                idempotency_key: "clear-apply".to_owned(),
                apply: true,
            },
        )
        .unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, applied.result);
    }

    #[test]
    fn status_filter_is_applied_before_offset_and_limit() {
        let (_directory, path) = database();
        let mut submit = submit_request(&path, "mixed-status", "mixed-status", true);
        submit.attempts = vec![
            attempt(Some("attempt-1"), 41),
            attempt(Some("attempt-2"), 42),
            attempt(Some("attempt-3"), 43),
        ];
        submit_broadcasts_write(&path, submit).unwrap();
        finalize_broadcast_attempts_write(
            &path,
            BroadcastFinalizeRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "broadcasts.finalize".to_owned(),
                request_id: "mixed-status-finalize".to_owned(),
                idempotency_key: "mixed-status-finalize".to_owned(),
                apply: true,
                expected_upstream: expected(&path),
                finalizations: vec![
                    failed_finalization_for("attempt-2"),
                    failed_finalization_for("attempt-3"),
                ],
            },
        )
        .unwrap();

        let first_failed =
            read_broadcast_snapshot_page(&path, NETWORK, CHAIN_ID, None, Some("failed"), 0, 1)
                .unwrap();
        assert_eq!(first_failed.attempts.len(), 1);
        assert_eq!(first_failed.attempts[0].attempt_id, "attempt-2");

        let second_failed =
            read_broadcast_snapshot_page(&path, NETWORK, CHAIN_ID, None, Some("failed"), 1, 1)
                .unwrap();
        assert_eq!(second_failed.attempts.len(), 1);
        assert_eq!(second_failed.attempts[0].attempt_id, "attempt-3");

        let pending =
            read_broadcast_snapshot_page(&path, NETWORK, CHAIN_ID, None, Some("pending"), 0, 1)
                .unwrap();
        assert_eq!(pending.attempts.len(), 1);
        assert_eq!(pending.attempts[0].attempt_id, "attempt-1");
        assert!(pending.diagnostics.is_empty());
    }

    #[test]
    fn success_requires_hash_and_terminal_attempt_cannot_be_finalized_twice() {
        let (_directory, path) = database();
        submit_broadcasts(&path, NETWORK, CHAIN_ID, vec![attempt(None, 42)], true).unwrap();
        let missing_hash = AttemptFinalizeInput {
            attempt_id: "core4:loop-1:9:42".to_owned(),
            status: BroadcastTerminalStatus::Success,
            tx_hash: None,
            receipt: serde_json::json!({"status": "0x1"}),
            status_reason: None,
            diagnostics: Vec::new(),
            evidence: serde_json::json!({}),
            finalized_at: None,
        };
        assert!(matches!(
            finalize_broadcast_attempts(&path, NETWORK, CHAIN_ID, vec![missing_hash], true)
                .unwrap_err(),
            BroadcastError::InvalidRequest(_)
        ));

        let success = AttemptFinalizeInput {
            attempt_id: "core4:loop-1:9:42".to_owned(),
            status: BroadcastTerminalStatus::Success,
            tx_hash: Some(TX_HASH.to_owned()),
            receipt: serde_json::json!({"status": "0x1"}),
            status_reason: None,
            diagnostics: Vec::new(),
            evidence: serde_json::json!({"receiptStatus": "0x1"}),
            finalized_at: None,
        };
        let result = finalize_broadcast_attempts_write(
            &path,
            BroadcastFinalizeRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "broadcasts.finalize".to_owned(),
                request_id: "finalize-1".to_owned(),
                idempotency_key: "finalize-idem-1".to_owned(),
                apply: true,
                expected_upstream: expected(&path),
                finalizations: vec![success.clone()],
            },
        )
        .unwrap();
        assert_eq!(result.result.success_count, 1);
        assert!(!result.idempotent_replay);

        let error =
            finalize_broadcast_attempts(&path, NETWORK, CHAIN_ID, vec![success], true).unwrap_err();
        assert!(matches!(
            error,
            BroadcastError::AttemptAlreadyTerminal { .. }
        ));
    }

    #[test]
    fn stale_review_version_is_rejected_before_write() {
        let (_directory, path) = database();
        let mut request = submit_request(&path, "stale", "stale-idem", true);
        request.expected_upstream.review_version = Some(8);
        let error = submit_broadcasts_write(&path, request).unwrap_err();
        assert!(matches!(
            error,
            BroadcastError::ExpectedUpstreamMismatch { .. }
        ));
        let connection = Connection::open(path).unwrap();
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn only_generated_or_confirmed_requests_are_eligible() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_review_broadcast_requests SET request_status = 'failed'",
                [],
            )
            .unwrap();
        drop(connection);
        let error = submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![attempt(Some("attempt-1"), 42)],
            true,
        )
        .unwrap_err();
        assert!(matches!(
            error,
            BroadcastError::BroadcastRequestNotEligible { .. }
        ));
    }
}
