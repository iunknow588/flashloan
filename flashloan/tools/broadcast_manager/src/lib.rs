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

pub mod admission;
pub mod execution_record;
pub mod runtime;

pub use admission::{
    decide as decide_admission, AdmissionCandidate, AdmissionCheck, AdmissionDecision,
    AdmissionRule, BroadcastAdmissionContext, BroadcastGuardPolicy,
};
pub use execution_record::{record_runtime_attempt, RuntimeAttempt, RuntimeAttemptOutcome};
pub use runtime::{BroadcastRuntimeStatus, BroadcastSupervisor, ExecutionQueue, ExecutionTask};

const NETWORK_PROTOCOL: &str = "uniswap_v4";
const BROADCAST_SCOPE_KEY: &str = "core4";
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_IDENTIFIER_LENGTH: usize = 512;
const MAX_RPC_URL_LENGTH: usize = 2048;
const MAX_SNAPSHOT_LIMIT: usize = 1_000;
const MAX_INTERNAL_LIMIT: usize = 100_000;

/// The nine audit fields that answer "why was this one sent" (`BC-5`).
///
/// Every field is present in every record — a field that could not be
/// measured is `null`, never silently absent. The point of the record
/// is that an operator can reconstruct the decision without reading the
/// code.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastAuditRecord {
    /// 1. The review the request came from.
    pub review_identifier: String,
    /// 2. The signed PnL amount the review measured.
    pub signed_pnl_amount: Option<f64>,
    /// 3. The age of the review, in seconds, at the moment of the
    ///    decision.
    pub review_age_seconds: Option<i64>,
    /// 4. The idempotency key of the submission.
    pub idempotency_key: String,
    /// 5. The chain the request was read under.
    pub chain_id: i64,
    /// 6. The account that signs.
    pub account_identifier: Option<String>,
    /// 7. The limit the amount was judged against.
    pub limit_verdict: String,
    /// 8. When the broadcast happened.
    pub broadcast_at: String,
    /// 9. What became of it.
    pub outcome: String,
    /// The full admission trail.
    ///
    /// This is the evidence behind the nine fields: every rule that was
    /// checked, with its verdict and its reason.
    pub admission: serde_json::Value,
    /// The reason, when the record describes a refusal.
    pub refusal_reason: Option<String>,
}

impl BroadcastAuditRecord {
    /// Build the record of an **admitted** request.
    ///
    /// The nine fields are filled from the decision and the request; the
    /// admission trail is embedded verbatim.
    pub fn admitted(
        operation: &str,
        chain_id: i64,
        loop_key: &str,
        signer_address: Option<&str>,
        signed_pnl_amount: Option<f64>,
        decision: &AdmissionDecision,
        idempotency_key: &str,
        broadcast_at: &str,
    ) -> Self {
        Self {
            review_identifier: format!("{}:{}", operation, loop_key),
            signed_pnl_amount,
            review_age_seconds: decision.review_age_seconds,
            idempotency_key: idempotency_key.to_owned(),
            chain_id,
            account_identifier: signer_address.map(str::to_owned),
            limit_verdict: limit_verdict(decision),
            broadcast_at: broadcast_at.to_owned(),
            outcome: "admitted".to_owned(),
            admission: serde_json::to_value(decision).unwrap_or(serde_json::Value::Null),
            refusal_reason: None,
        }
    }

    /// Build the record of a **refused** request.
    ///
    /// A refusal is recorded exactly like a pass. That is the whole
    /// point of `BC-3`: "it did not send" is a fact about the system,
    /// not the absence of a fact.
    pub fn refused(
        operation: &str,
        chain_id: i64,
        loop_key: &str,
        signer_address: Option<&str>,
        decision: &AdmissionDecision,
        idempotency_key: &str,
        broadcast_at: &str,
        signed_pnl_amount: Option<f64>,
    ) -> Self {
        Self {
            review_identifier: format!("{}:{}", operation, loop_key),
            signed_pnl_amount,
            review_age_seconds: decision.review_age_seconds,
            idempotency_key: idempotency_key.to_owned(),
            chain_id,
            account_identifier: signer_address.map(str::to_owned),
            limit_verdict: limit_verdict(decision),
            broadcast_at: broadcast_at.to_owned(),
            outcome: "refused".to_owned(),
            admission: serde_json::to_value(decision).unwrap_or(serde_json::Value::Null),
            refusal_reason: Some(decision.refusal_reason()),
        }
    }
}

/// The limit verdict recorded in the audit trail.
///
/// It names the rule that decided, so that "the limit" is answerable
/// without re-reading the policy.
fn limit_verdict(decision: &AdmissionDecision) -> String {
    for check in &decision.checks {
        if check.rule == AdmissionRule::AmountWithinLimit {
            return if check.satisfied {
                "within_limit".to_owned()
            } else {
                "limit_not_satisfied".to_owned()
            };
        }
    }
    "not_evaluated".to_owned()
}

#[derive(Debug)]
pub enum BroadcastError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    InvalidRequest(String),
    ReviewRead(String),
    /// The closed loop a broadcast belongs to could not be read.
    ///
    /// The review clock belongs to the closed loop, so a broadcast that
    /// cannot start the clock of its loop cannot be recorded either: the
    /// error names the loop whose state could not be reached.
    ClosedLoopRead(String),
    MissingReviewState,
    /// The loop a runtime attempt belongs to has no review row.
    ///
    /// The attempt row carries the closed-loop identity, and the review
    /// layer owns that identity.  A loop without a review row therefore
    /// has no identity to file the attempt under, and the attempt is
    /// refused instead of being filed against a guess.
    ReviewRowMissing {
        loop_key: String,
    },
    ReviewSnapshotTruncated {
        expected: usize,
        actual: usize,
    },
    UnknownRequest(String),
    BroadcastRequestNotEligible {
        loop_key: String,
        request_status: String,
    },
    /// The request failed the admission decision (`BC-1` / `BC-2`).
    ///
    /// The variant carries **every** rule that did not hold, so that
    /// the caller can record why the request did not leave the system
    /// without re-deriving the judgement.
    BroadcastRequestNotAdmitted {
        loop_key: String,
        reasons: Vec<String>,
        detail: String,
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
            Self::ClosedLoopRead(message) => {
                write!(formatter, "closed loop upstream read failed: {message}")
            }
            Self::MissingReviewState => {
                formatter.write_str("review scope core4 has not been materialized")
            }
            Self::ReviewRowMissing { loop_key } => write!(
                formatter,
                "loop {loop_key} has no review row, so the attempt has no identity to file under"
            ),
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
            Self::BroadcastRequestNotAdmitted {
                loop_key,
                reasons,
                detail,
            } => write!(
                formatter,
                "request {loop_key} was refused by the admission decision;                  unsatisfied rules: {}; {detail}",
                reasons.join(", ")
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
    /// The runtime guardrails this submission is judged against.
    ///
    /// The policy is **runtime configuration**, not an approval. A
    /// deployment that cannot name its chain, its account, its limit or
    /// its window does not get a broadcast — it gets a refusal with a
    /// recorded reason.
    ///
    /// An **absent** policy is a refusal, not a default-allow: the
    /// absence of a guardrail is not evidence that the guardrail passed.
    pub guard_policy: Option<BroadcastGuardPolicy>,
    /// The amount each attempt would move, in anchor-token raw units.
    ///
    /// The mapping is keyed by `loop_key`. An attempt whose loop has no
    /// entry has an **unmeasured** amount, which the limit rule refuses.
    pub attempt_amounts_raw: BTreeMap<String, u128>,
    /// The time the admission decision is made at.
    ///
    /// Supplying it makes the decision reproducible. When absent the
    /// current UTC time is used.
    pub admission_now: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BroadcastTerminalStatus {
    Success,
    Failed,
    /// The transaction was sent and the chain has not answered yet.
    ///
    /// The send is the fact; the receipt is the answer, and the answer
    /// arrives when the chain mines the transaction. A record that waits
    /// for the answer before it is written holds the thread that sent the
    /// transaction, and a held thread cannot send the next opportunity.
    /// The attempt is therefore recorded as pending, and the broadcast
    /// confirmation thread completes it when the answer arrives.
    Pending,
}

impl BroadcastTerminalStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failed => "failed",
            Self::Pending => "pending",
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
    pub record_store: BroadcastRecordStore,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BroadcastRecordStore {
    Manager,
    Legacy,
}

impl BroadcastRecordStore {
    fn as_str(self) -> &'static str {
        match self {
            Self::Manager => "manager",
            Self::Legacy => "legacy",
        }
    }
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
    /// The folded single-tick capacity of the loop, in anchor-token raw
    /// units.
    ///
    /// The bound is copied from the review request. It is the last durable
    /// proof that the amount that was broadcast was inside the capacity the
    /// review proved, so an operator can verify it after the fact.
    pub single_tick_capacity_anchor_raw: Option<u128>,
    /// The hop whose folded capacity is the smallest.
    pub single_tick_capacity_binding_pool_id: Option<String>,
    /// The price limit of each hop, in route order.
    ///
    /// A broadcast attempt without all three limits is refused: the
    /// executor must not fall back to the global V4 price bound.
    pub hop_sqrt_price_limits: Vec<String>,
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
    pub record_store: String,
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

/// Submit attempts without a full write request.
///
/// The caller supplies the **runtime guardrails** explicitly. There is
/// deliberately no default: a submission that cannot say which chain,
/// account, limit and window it runs under is refused by the admission
/// decision, and it says why.
///
/// The amounts are supplied per loop key. A loop with no entry has an
/// **unmeasured** amount, which the limit rule refuses.
pub fn submit_broadcasts(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    attempts: Vec<AttemptCreateInput>,
    apply: bool,
    guard_policy: BroadcastGuardPolicy,
    attempt_amounts_raw: BTreeMap<String, u128>,
    admission_now: Option<String>,
) -> Result<BroadcastMaterializationResult, BroadcastError> {
    // The guardrails travel as explicit parameters, not inside a
    // synthesized request: this entry point carries no idempotency key
    // of its own, and a synthesized request would persist one. The
    // admission decision still runs on exactly the same path — the
    // guardrails are handed to the same call.
    submit_impl_with_guard(
        database_path.as_ref(),
        network,
        chain_id,
        apply,
        &attempts,
        None,
        Some(guard_policy),
        attempt_amounts_raw,
        admission_now,
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
        match request.record_store {
            BroadcastRecordStore::Manager => {
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
            }
            BroadcastRecordStore::Legacy => {
                if sqlite_table_exists(&transaction, "cow_execution_attempts")? {
                    transaction.execute(
                        "DELETE FROM cow_execution_attempts WHERE network = ?1 AND chain_id = ?2",
                        params![request.network, request.chain_id],
                    )?;
                }
            }
        }
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
    let (pending_attempts, cleared_attempts, cleared_diagnostics) = match request.record_store {
        BroadcastRecordStore::Manager => (
            count_scoped_rows(
                connection,
                "manager_cow_execution_attempts",
                &request.network,
                request.chain_id,
                Some("status = 'pending'"),
            )?,
            count_scoped_rows(
                connection,
                "manager_cow_execution_attempts",
                &request.network,
                request.chain_id,
                None,
            )?,
            count_scoped_rows(
                connection,
                "manager_broadcast_attempt_diagnostics",
                &request.network,
                request.chain_id,
                None,
            )?,
        ),
        BroadcastRecordStore::Legacy => {
            if !sqlite_table_exists(connection, "cow_execution_attempts")? {
                (0, 0, 0)
            } else {
                let pending = count_legacy_attempt_rows(
                    connection, &request.network, request.chain_id,
                    Some("category NOT LIKE '%success%' AND category NOT LIKE '%failed%' AND category NOT LIKE '%failure%' AND category NOT LIKE '%revert%'"),
                )?;
                let total = count_legacy_attempt_rows(
                    connection,
                    &request.network,
                    request.chain_id,
                    None,
                )?;
                (pending, total, 0)
            }
        }
    };
    if pending_attempts > 0 {
        return Err(BroadcastError::InvalidRequest(
            "cannot clear broadcast records while pending attempts exist".to_owned(),
        ));
    }
    Ok(BroadcastClearResult {
        network: request.network.clone(),
        chain_id: request.chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: BROADCAST_SCOPE_KEY.to_owned(),
        record_store: request.record_store.as_str().to_owned(),
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
    let (guard_policy, attempt_amounts_raw, admission_now) = match request {
        Some(request) => (
            request.guard_policy.clone(),
            request.attempt_amounts_raw.clone(),
            request.admission_now.clone(),
        ),
        None => (None, BTreeMap::new(), None),
    };
    submit_impl_with_guard(
        database_path,
        network,
        chain_id,
        apply,
        attempts,
        request,
        guard_policy,
        attempt_amounts_raw,
        admission_now,
    )
}

/// The submit path, with the runtime guardrails passed explicitly.
///
/// The guardrails travel separately from the write request because the
/// two entry points differ: the write request carries an idempotency
/// key and is persisted, while the plain entry point carries neither.
/// The admission decision must run on **both** paths, so the guardrails
/// cannot live inside the request alone.
fn submit_impl_with_guard(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    attempts: &[AttemptCreateInput],
    request: Option<&BroadcastSubmitRequest>,
    guard_policy: Option<BroadcastGuardPolicy>,
    attempt_amounts_raw: BTreeMap<String, u128>,
    admission_now: Option<String>,
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
        let outcome = match submit_on_connection(
            &transaction,
            network,
            chain_id,
            true,
            attempts,
            prepared_write,
            guard_policy.as_ref(),
            &attempt_amounts_raw,
            admission_now.as_deref(),
        ) {
            Ok(outcome) => outcome,
            Err(error) => {
                // The transaction is abandoned with the error, so the
                // `BC-3` trail has to be written **outside** it.
                // A refusal that is rolled back with its transaction is
                // not a refusal anybody can find.
                drop(transaction);
                record_refusal_trail(
                    database_path,
                    network,
                    chain_id,
                    &error,
                )?;
                return Err(error);
            }
        };
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
            guard_policy.as_ref(),
            &attempt_amounts_raw,
            admission_now.as_deref(),
        )
    }
}

/// Write the `BC-3` refusal trail on its own connection.
///
/// The trail must survive the rollback of the submission it belongs to.
/// It therefore opens the store again, outside the failed transaction,
/// and writes only when the failure **was** an admission refusal.
///
/// A dry run writes nothing: the refusal is reported to the caller and
/// nothing is persisted.
fn record_refusal_trail(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    error: &BroadcastError,
) -> Result<(), BroadcastError> {
    let BroadcastError::BroadcastRequestNotAdmitted {
        loop_key,
        reasons,
        detail,
    } = error
    else {
        return Ok(());
    };
    let connection = open_connection(database_path, true)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    record_admission_refusal(
        &connection,
        network,
        chain_id,
        loop_key,
        reasons,
        detail,
        &chrono::Utc::now().to_rfc3339(),
    )
}

fn submit_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    apply: bool,
    attempts: &[AttemptCreateInput],
    prepared_write: Option<(&BroadcastSubmitRequest, String)>,
    guard_policy: Option<&BroadcastGuardPolicy>,
    attempt_amounts_raw: &BTreeMap<String, u128>,
    admission_now: Option<&str>,
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
    // ---- BC-3: a refusal is recorded too --------------------------------
    //
    // "It did not send" is a fact about the system, not the absence of
    // one. When the admission decision refuses a request, the refusal
    // is written to the diagnostics table **before** the error is
    // returned, so that an operator can ask "why did nothing go out"
    // and get an answer.
    //
    // The trail is only written for an `apply` submission. A dry run
    // writes nothing at all — that is the whole point of a dry run.
    // The `BC-3` trail for a refusal is written by the caller, on a
    // connection of its own: the refusal aborts this transaction, and a
    // trail written inside it would be rolled back with it.
    let desired = build_attempt_rows(
        &review,
        &requests,
        attempts,
        &now,
        chain_id,
        guard_policy,
        &attempt_amounts_raw,
        admission_now.as_deref(),
        review_manager::automatic_review_interval_seconds_on_connection(connection),
        prepared_write.as_ref().map(|(request, _)| *request),
        prepared_write
            .as_ref()
            .map(|(request, _)| request.idempotency_key.as_str())
            .unwrap_or_default(),
    )?;
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
        publish_trade_archives(database_path, network, chain_id, finalizations);
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

/// Hand the finished attempts to the trade archive.
///
/// The archive writes the row into the monthly store and only then shrinks
/// the main row, so a failure here leaves the record complete in the main
/// store where the next reconciliation pass still finds it.  A failure is
/// counted and never fatal: the broadcast itself already happened, and the
/// record was not lost.
fn publish_trade_archives(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    finalizations: &[AttemptFinalizeInput],
) {
    if finalizations.is_empty() {
        return;
    }
    let requests = finalizations
        .iter()
        .map(|finalization| review_manager::records::ArchiveRequest {
            network: network.to_owned(),
            chain_id,
            protocol: NETWORK_PROTOCOL.to_owned(),
            scope_key: BROADCAST_SCOPE_KEY.to_owned(),
            attempt_id: finalization.attempt_id.clone(),
        })
        .collect();
    if let Err(error) = review_manager::records::publish_trade_archive(
        database_path,
        network,
        chain_id,
        requests,
    ) {
        review_manager::records::note_trade_publish_failure(&error);
    }
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
    chain_id: i64,
    guard_policy: Option<&BroadcastGuardPolicy>,
    attempt_amounts_raw: &BTreeMap<String, u128>,
    admission_now: Option<&str>,
    review_freshness_window_seconds: i64,
    submit_request: Option<&BroadcastSubmitRequest>,
    idempotency_key: &str,
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
        // The **descriptive** fields of the signed PnL are checked here,
        // because a request without a currency or a source is a
        // malformed request, not a judgement.
        //
        // The **measurement** itself is *not* checked here: whether the
        // profit was measured at all is one of the `BC-1` predicates, and
        // it is judged in `admission` so that the refusal carries a
        // named rule and lands in the `BC-3` trail. Folding it into a
        // generic validation error would hide the reason the broadcast
        // did not happen.
        if !validate_signed_pnl_description(&request.signed_pnl) {
            return Err(BroadcastError::InvalidRequest(format!(
                "confirmed request {} has incomplete signed PnL",
                request.loop_key
            )));
        }
        // ---- BC-1: the single admission decision ------------------------
        //
        // The rule set lives in `admission`. It is applied **here**, at
        // the one place where a request becomes an attempt, so that the
        // judgement is made once and is recorded.
        //
        // The request status check above stays: a request that the
        // review consumed or canceled is not a candidate at all, and
        // that is a different fact from "the request failed admission".
        let context = BroadcastAdmissionContext {
            policy: guard_policy.cloned().unwrap_or_default(),
            amount_raw: attempt_amounts_raw.get(&attempt.loop_key).copied(),
            now: admission_now.map(str::to_owned),
            review_freshness_window_seconds: Some(review_freshness_window_seconds),
        };
        let candidate = AdmissionCandidate {
            loop_key: request.loop_key.clone(),
            chain_id,
            request_status: request.request_status.clone(),
            // A stored amount that is not a finite number is not a
            // measurement. It is passed on as "never measured" so that
            // the `BC-1.pnl_measured` rule refuses it with a reason
            // instead of it being silently folded into zero.
            signed_pnl_amount: admission::measured_amount(request.signed_pnl.amount),
            hop_sqrt_price_limits: request.hop_sqrt_price_limits.clone(),
            signer_address: Some(attempt.signer_address.clone()),
            facts: request.admission.clone(),
        };
        let decision = decide_admission(&candidate, &context);
        if !decision.admitted {
            return Err(BroadcastError::BroadcastRequestNotAdmitted {
                loop_key: request.loop_key.clone(),
                reasons: decision.failed_rules.clone(),
                detail: decision.refusal_reason(),
            });
        }
        // The BC-5 record is built **only** for an admitted request. A
        // refusal gets its own trail (`BC-3`) on the refusal path, so
        // that "it did not send" is recorded too.
        let audit = BroadcastAuditRecord::admitted(
            submit_request
                .map(|request| request.operation.as_str())
                .unwrap_or("broadcasts.submit"),
            chain_id,
            &request.loop_key,
            Some(attempt.signer_address.as_str()),
            Some(request.signed_pnl.amount),
            &decision,
            idempotency_key,
            now,
        );
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
            evidence: {
                // The BC-5 audit record is written **into** the attempt's
                // evidence, next to whatever the caller supplied. The
                // caller's own evidence is kept: the audit record is
                // added to it, never substituted for it.
                let mut evidence = normalize_object(&attempt.evidence);
                if !evidence.is_object() {
                    evidence = serde_json::Value::Object(Default::default());
                }
                if let Some(object) = evidence.as_object_mut() {
                    object.insert(
                        "broadcastAudit".to_owned(),
                        serde_json::to_value(audit)?,
                    );
                }
                evidence
            },
            created_at: now.to_owned(),
            updated_at: now.to_owned(),
            terminal_at: None,
            single_tick_capacity_anchor_raw: request.single_tick_capacity_anchor_raw,
            single_tick_capacity_binding_pool_id: request
                .single_tick_capacity_binding_pool_id
                .clone(),
            hop_sqrt_price_limits: request.hop_sqrt_price_limits.clone(),
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
        BroadcastTerminalStatus::Pending => {
            return Err(BroadcastError::InvalidRequest(
                "a pending attempt cannot be finalized: the chain has not answered yet"
                    .to_owned(),
            ));
        }
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

/// Check the **descriptive** half of a signed PnL.
///
/// Whether the amount was measured is deliberately *not* part of this
/// check: that judgement belongs to the `BC-1` predicate set, and it
/// has to be reported as a named refusal rather than as a malformed
/// request.
fn validate_signed_pnl_description(value: &SignedPnl) -> bool {
    !value.raw.trim().is_empty()
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
        "apply": request.apply,
        "recordStore": request.record_store
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

fn sqlite_table_exists(connection: &Connection, table: &str) -> Result<bool, BroadcastError> {
    Ok(connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
        [table],
        |row| row.get::<_, i64>(0),
    )? != 0)
}

fn count_legacy_attempt_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    predicate: Option<&str>,
) -> Result<usize, BroadcastError> {
    let sql = format!(
        "SELECT COUNT(*) FROM cow_execution_attempts WHERE network=?1 AND chain_id=?2{}",
        predicate
            .map(|value| format!(" AND ({value})"))
            .unwrap_or_default(),
    );
    Ok(
        connection.query_row(&sql, params![network, chain_id], |row| row.get::<_, i64>(0))?
            as usize,
    )
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
                evidence_json, created_at, updated_at, terminal_at,
                single_tick_capacity_anchor_raw,
                single_tick_capacity_binding_pool_id,
                hop_sqrt_price_limits_json
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
                single_tick_capacity_anchor_raw: row
                    .get::<_, Option<String>>(34)?
                    .and_then(|value| value.trim().parse::<u128>().ok()),
                single_tick_capacity_binding_pool_id: row
                    .get::<_, Option<String>>(35)?
                    .map(|value| value.trim().to_owned())
                    .filter(|value| !value.is_empty()),
                hop_sqrt_price_limits: parse_optional_json_column(
                    row.get::<_, Option<String>>(36)?,
                )?,
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
             evidence_json, created_at, updated_at, terminal_at,
             single_tick_capacity_anchor_raw,
             single_tick_capacity_binding_pool_id,
             hop_sqrt_price_limits_json
         ) VALUES (
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,
             ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20,
             ?21, ?22, ?23, ?24, ?25, ?26, ?27, ?28, ?29, ?30,
             ?31, ?32, ?33, ?34, ?35, ?36, ?37, ?38, ?39, ?40, ?41
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
            row.terminal_at,
            row.single_tick_capacity_anchor_raw
                .map(|value| value.to_string()),
            row.single_tick_capacity_binding_pool_id.clone(),
            serde_json::to_string(&row.hop_sqrt_price_limits)?,
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

/// Write the `BC-3` refusal trail.
///
/// The refusal is stored in the diagnostics table with the
/// `evidence_incomplete` type and a dedicated diagnostic code, so that
/// a reader can tell "the admission decision refused this" apart from
/// "the attempt was sent and the evidence came back incomplete".
///
/// The diagnostic is keyed by the loop it refused, not by an attempt
/// id: a refused request has no attempt — that is precisely why it was
/// refused.
fn record_admission_refusal(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
    reasons: &[String],
    detail: &str,
    observed_at: &str,
) -> Result<(), BroadcastError> {
    let diagnostic_id = format!("admission-refusal:{loop_key}");
    // The primary key is (..., attempt_id, diagnostic_id). A refusal has
    // no attempt, so the attempt id is the loop key itself — the
    // refusal belongs to the request, and the request is keyed by its
    // loop.
    let row = BroadcastDiagnosticRow {
        attempt_id: loop_key.to_owned(),
        loop_key: loop_key.to_owned(),
        diagnostic_id,
        diagnostic_type: "evidence_incomplete".to_owned(),
        diagnostic_code: Some("BC-1_BC-2_ADMISSION_REFUSED".to_owned()),
        message: Some(detail.to_owned()),
        rpc_url: None,
        tx_hash: None,
        payload: serde_json::json!({
            "unsatisfiedRules": reasons,
            "outcome": "refused",
            "broadcasted": false,
        }),
        observed_at: observed_at.to_owned(),
    };
    insert_diagnostic(connection, network, chain_id, &row)
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
        "apply": request.apply,
        "recordStore": request.record_store
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

fn parse_optional_json_column<T>(value: Option<String>) -> Result<T, rusqlite::Error>
where
    T: serde::de::DeserializeOwned + Default,
{
    match value {
        Some(value) => serde_json::from_str(&value).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(
                value.len(),
                rusqlite::types::Type::Text,
                Box::new(error),
            )
        }),
        None => Ok(T::default()),
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
        BroadcastFinalizeRequest, BroadcastRecordStore, BroadcastSubmitRequest,
        BroadcastGuardPolicy, BroadcastMaterializationResult, BroadcastTerminalStatus,
        ExpectedUpstream,
    };
    use rusqlite::{params, Connection};
    use std::collections::BTreeMap;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const LOOP_KEY: &str = "loop-1";
    const SIGNER: &str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TX_HASH: &str = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    /// The instant the fixture decision is made at.
    const FIXTURE_NOW: &str = "2026-11-04T01:00:00Z";
    /// The review stamp the fixture seeds. It is 60 seconds before
    /// `FIXTURE_NOW`, so it is inside the freshness window of the automatic
    /// review interval.
    const REVIEWED_AT: &str = "2026-11-04T00:59:00Z";

    pub(crate) fn database() -> (tempfile::TempDir, std::path::PathBuf) {
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
    pub(crate) fn database_with_record_root() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let db_directory = directory.path().join("db");
        std::fs::create_dir_all(&db_directory).unwrap();
        let path = seed_store(&db_directory.join("manager.sqlite3"));
        (directory, path)
    }

    pub(crate) fn seed_store(path: &std::path::Path) -> std::path::PathBuf {
        manager_db::migrate(path).unwrap();
        let connection = Connection::open(path).unwrap();
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
                    request_status, request_reason,
                    hop_sqrt_price_limits_json, created_at, updated_at
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
                           'positive executable signed PnL',
                            '[\"100\",\"200\",\"300\"]',
                            'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID, LOOP_KEY],
            )
            .unwrap();
        // The broadcast layer judges a request against the **review row**
        // that measured it. The fixture therefore seeds one, with a
        // review stamp that is inside the freshness window relative to
        // `FIXTURE_NOW`.
        connection
            .execute(
                "INSERT INTO manager_v4_reviews (
                    network, chain_id, protocol, scope_key, loop_key,
                    closed_loop_version, closed_loop_checksum,
                    candidate_edge_version, candidate_edge_checksum,
                    observation_version, observation_checksum,
                    pool_scope_checksum, anchor_token_key_id, anchor_address,
                    anchor_symbol, path_text, path_addresses_json,
                    path_symbols_json, pool_path_key, pool_ids_json,
                    review_status, review_reason, static_preview_json,
                    run_arbitrage_json, signed_pnl_raw, signed_pnl_amount,
                    signed_pnl_currency, signed_pnl_address, signed_pnl_unit,
                    signed_pnl_source, reviewed_at, created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 7,
                           'sha256:closed-loop', 5, 'sha256:candidate-edge',
                           3, 'sha256:observation', 'sha256:pool-scope',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           '0x1111111111111111111111111111111111111111', 'USDG',
                           'USDG -> AMZN -> TA -> USDG',
                           '[]', '[]', 'pool-path:1', '[]',
                           'completed', 'fixture', '{}',
                           '{\"ok\":true,\"executable\":true}',
                           '12.5 USDG', 12.5, 'USDG',
                           '0x1111111111111111111111111111111111111111',
                           'token', 'runArbitrage', ?4, 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID, LOOP_KEY, REVIEWED_AT],
            )
            .unwrap();
        drop(connection);
        path.to_path_buf()
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
            guard_policy: Some(guard_policy()),
            attempt_amounts_raw: BTreeMap::from([(LOOP_KEY.to_owned(), 1_000u128)]),
            admission_now: Some(FIXTURE_NOW.to_owned()),
        }
    }

    /// Submit attempts through the plain entry point.
    ///
    /// The plain entry point carries no idempotency key of its own, so
    /// the fixture supplies the runtime guardrails explicitly. There is
    /// no default: a submission that cannot name its guardrails is
    /// refused.
    fn submit_attempts(
        path: &std::path::Path,
        attempts: Vec<AttemptCreateInput>,
        apply: bool,
    ) -> Result<BroadcastMaterializationResult, BroadcastError> {
        submit_broadcasts(
            path,
            NETWORK,
            CHAIN_ID,
            attempts,
            apply,
            guard_policy(),
            BTreeMap::from([(LOOP_KEY.to_owned(), 1_000u128)]),
            Some(FIXTURE_NOW.to_owned()),
        )
    }

    /// The runtime guardrails the fixture submission is judged against.
    fn guard_policy() -> BroadcastGuardPolicy {
        BroadcastGuardPolicy {
            chain_id: Some(CHAIN_ID),
            signer_address: Some(SIGNER.to_owned()),
            max_amount_raw: Some(1_000_000u128),
            window_start: Some("2020-01-01T00:00:00Z".to_owned()),
            window_end: Some("2099-01-01T00:00:00Z".to_owned()),
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
        submit_attempts(&path, vec![attempt(Some("attempt-1"), 42)], true)
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
        submit_attempts(&path, vec![attempt(None, 42)], true).unwrap();
        let pending_clear = clear_broadcast_records_write(
            &path,
            BroadcastClearRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "broadcasts.clear".to_owned(),
                request_id: "clear-pending".to_owned(),
                idempotency_key: "clear-pending".to_owned(),
                apply: true,
                record_store: BroadcastRecordStore::Manager,
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
                record_store: BroadcastRecordStore::Manager,
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
                record_store: BroadcastRecordStore::Manager,
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
                record_store: BroadcastRecordStore::Manager,
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
        submit_attempts(&path, vec![attempt(None, 42)], true).unwrap();
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

    /// A request without all three price limits is refused. Version 1
    /// executes inside one tick range per hop, so a payload that cannot
    /// carry its own limits must never become a broadcast attempt.
    #[test]
    fn a_request_without_hop_price_limits_is_refused() {
        let (_directory, path) = database();
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute(
                    "UPDATE manager_v4_review_broadcast_requests
                     SET hop_sqrt_price_limits_json = '[\"100\"]'
                     WHERE network = ?1 AND chain_id = ?2",
                    params![NETWORK, CHAIN_ID],
                )
                .unwrap();
        }
        let error = submit_attempts(&path, vec![attempt(None, 7)], true)
            .expect_err("a request without three price limits must be refused");
        // The refusal names the rule that did not hold, so that the
        // reason is answerable without re-deriving the judgement.
        match error {
            BroadcastError::BroadcastRequestNotAdmitted { reasons, .. } => {
                assert!(
                    reasons.iter().any(|reason| reason == "BC-1.price_limits_complete"),
                    "unexpected reasons: {reasons:?}"
                );
            }
            other => panic!("unexpected error: {other:?}"),
        }
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
        let error = submit_attempts(&path, vec![attempt(Some("attempt-1"), 42)], true)
        .unwrap_err();
        assert!(matches!(
            error,
            BroadcastError::BroadcastRequestNotEligible { .. }
        ));
    }

    #[test]
    fn a_finalized_attempt_reaches_the_monthly_trade_store() {
        let (directory, path) = database_with_record_root();
        submit_attempts(&path, vec![attempt(Some("attempt-1"), 42)], true)
        .unwrap();
        finalize_broadcast_attempts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![failed_finalization()],
            true,
        )
        .unwrap();

        // The archiver runs on its own thread.  The reconciliation answers on
        // the same queue, so it returns only after the attempt was archived.
        let report =
            review_manager::records::reconcile_archive_backlog(&path, NETWORK, CHAIN_ID).unwrap();
        assert_eq!(report.failed, 0);

        let trades = directory
            .path()
            .join("db")
            .join("broadcast_transaction_db")
            .join(NETWORK)
            .join(CHAIN_ID.to_string());
        let months: Vec<std::path::PathBuf> = std::fs::read_dir(&trades)
            .unwrap()
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.is_dir())
            .collect();
        assert_eq!(months.len(), 1, "the monthly trade store was not created");
        let month = Connection::open(months[0].join("trades.sqlite3")).unwrap();
        let archived: String = month
            .query_row(
                "SELECT attempt_id FROM manager_cow_execution_attempts
                  WHERE attempt_id = 'attempt-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(archived, "attempt-1");
        drop(month);

        // The main store keeps the identity of the attempt and gives the
        // volume columns back.
        let connection = Connection::open(&path).unwrap();
        let (path_addresses, receipt): (String, String) = connection
            .query_row(
                "SELECT path_addresses_json, receipt_json
                   FROM manager_cow_execution_attempts
                  WHERE attempt_id = 'attempt-1'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(path_addresses, "[]");
        assert_eq!(receipt, "{}");
    }

    // ---- BC-1 / BC-2 / BC-3 / BC-5 acceptance -------------------------
    //
    // The cases below are the acceptance table of `T-复广.02`:
    // six negative scenarios, one positive, and one guardrail case.
    //
    // Every negative case asserts **two** things:
    //
    // 1. no broadcast request was generated, and
    // 2. the refusal names the rule that did not hold.
    //
    // The second half is what separates "the gate is on" from "the code
    // has a check". A refusal without a named rule is not evidence.

    /// Rewrite the fixture review row so that the admission facts differ.
    ///
    /// The fixture review is the layer that measured `runArbitrage`, so
    /// this is how a scenario "measures differently".
    fn set_review_facts(
        path: &std::path::Path,
        review_status: &str,
        run_json: &str,
        reviewed_at: &str,
    ) {
        let connection = Connection::open(path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_reviews
                    SET review_status = ?4, run_arbitrage_json = ?5, reviewed_at = ?6
                  WHERE network = ?1 AND chain_id = ?2 AND loop_key = ?3",
                params![
                    NETWORK,
                    CHAIN_ID,
                    LOOP_KEY,
                    review_status,
                    run_json,
                    reviewed_at
                ],
            )
            .unwrap();
    }

    /// The rules a refusal named.
    fn refusal_rules(error: &BroadcastError) -> Vec<String> {
        match error {
            BroadcastError::BroadcastRequestNotAdmitted { reasons, .. } => reasons.clone(),
            other => panic!("expected an admission refusal, got {other:?}"),
        }
    }

    /// Assert that a refusal named a rule.
    fn assert_refused_with(error: &BroadcastError, rule: &str) {
        let reasons = refusal_rules(error);
        assert!(
            reasons.iter().any(|reason| reason == rule),
            "expected {rule} among {reasons:?}"
        );
    }

    /// Assert that no attempt was created.
    fn assert_no_attempt(path: &std::path::Path) {
        let connection = Connection::open(path).unwrap();
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0, "a refused request must not create an attempt");
    }

    /// N-1: `run.ok = false` refuses the broadcast.
    #[test]
    fn n1_run_not_ok_refuses_the_broadcast() {
        let (_directory, path) = database();
        set_review_facts(
            &path,
            "not_executable",
            "{\"ok\":false,\"executable\":true}",
            REVIEWED_AT,
        );
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a request whose run is not ok must be refused");
        assert_refused_with(&error, "BC-1.run_ok");
        assert_no_attempt(&path);
    }

    /// N-2: `run.executable = false` refuses the broadcast.
    #[test]
    fn n2_run_not_executable_refuses_the_broadcast() {
        let (_directory, path) = database();
        set_review_facts(
            &path,
            "not_executable",
            "{\"ok\":true,\"executable\":false}",
            REVIEWED_AT,
        );
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a request whose run is not executable must be refused");
        assert_refused_with(&error, "BC-1.run_executable");
        assert_no_attempt(&path);
    }

    /// N-3: a **measured zero** profit refuses the broadcast, and is
    /// recorded as a business outcome rather than a defect.
    #[test]
    fn n3_measured_zero_profit_refuses_the_broadcast() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_review_broadcast_requests
                    SET signed_pnl_amount = 0.0, signed_pnl_raw = '0 USDG'
                  WHERE network = ?1 AND chain_id = ?2",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a measured zero profit must be refused");
        assert_refused_with(&error, "BC-1.pnl_positive");
        assert_no_attempt(&path);
    }

    /// N-4: a **measured negative** profit refuses the broadcast.
    #[test]
    fn n4_measured_negative_profit_refuses_the_broadcast() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_review_broadcast_requests
                    SET signed_pnl_amount = -3.5, signed_pnl_raw = '-3.5 USDG'
                  WHERE network = ?1 AND chain_id = ?2",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a measured loss must be refused");
        assert_refused_with(&error, "BC-1.pnl_positive");
        assert_no_attempt(&path);
    }

    /// N-5: an **unmeasured** profit refuses the broadcast, and is never
    /// treated as zero.
    #[test]
    fn n5_unmeasured_profit_refuses_the_broadcast() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        // "The profit was never measured" reaches the broadcast layer as
        // a request row whose stored amount is not a finite number. A
        // `NaN` cannot be stored at all — SQLite turns it into `NULL`
        // and the `NOT NULL` constraint rejects it — so the reachable
        // shape of "no figure" is an infinity.
        //
        // The review layer normally never publishes such a row: it only
        // publishes a request when the PnL was measured and positive.
        // This fixture writes one directly, because the broadcast layer
        // has to refuse it **on its own account** rather than trusting
        // that the review layer was careful.
        connection
            .execute(
                "UPDATE manager_v4_review_broadcast_requests
                    SET signed_pnl_amount = ?4
                  WHERE network = ?1 AND chain_id = ?2",
                params![NETWORK, CHAIN_ID, LOOP_KEY, f64::INFINITY],
            )
            .unwrap();
        drop(connection);
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a request without a measured profit must be refused");
        assert_refused_with(&error, "BC-1.pnl_measured");
        // The refusal must **not** be folded into "zero profit": a
        // missing measurement is not a business outcome, and the two
        // are reported by different rules.
        assert!(
            !refusal_rules(&error).contains(&"BC-1.pnl_positive".to_owned()),
            "an unmeasured profit must not be reported as a non-positive profit"
        );
        assert_no_attempt(&path);
    }

    /// N-6: a review older than the freshness window refuses the
    /// broadcast. The window is the automatic review interval of the
    /// closed loop, not the 600 s observation threshold.
    #[test]
    fn n6_stale_review_refuses_the_broadcast() {
        let (_directory, path) = database();
        // 61 seconds before `FIXTURE_NOW` — one second past the window.
        set_review_facts(
            &path,
            "completed",
            "{\"ok\":true,\"executable\":true}",
            "2026-11-04T00:58:59Z",
        );
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a stale review must be refused");
        assert_refused_with(&error, "BC-1.review_fresh");
        assert_no_attempt(&path);
    }

    /// A review inside the window is admitted, and the 600 s observation
    /// threshold is **not** used as the freshness window.
    #[test]
    fn a_review_just_inside_the_window_is_admitted() {
        let (_directory, path) = database();
        // 59 seconds before `FIXTURE_NOW`.
        set_review_facts(
            &path,
            "completed",
            "{\"ok\":true,\"executable\":true}",
            "2026-11-04T00:59:01Z",
        );
        submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect("a fresh review must be admitted");
    }

    /// A missing run verdict is **not** a pass.
    #[test]
    fn a_missing_run_verdict_is_refused() {
        let (_directory, path) = database();
        set_review_facts(&path, "completed", "{}", REVIEWED_AT);
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a request with no run verdict must be refused");
        assert_refused_with(&error, "BC-1.run_ok");
        assert_refused_with(&error, "BC-1.run_executable");
        assert_no_attempt(&path);
    }

    /// A request whose review row is gone is refused: absent evidence is
    /// not evidence of admissibility.
    #[test]
    fn a_request_without_a_review_row_is_refused() {
        let (_directory, path) = database();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "DELETE FROM manager_v4_reviews
                  WHERE network = ?1 AND chain_id = ?2",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);
        let error = submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("a request without a review row must be refused");
        assert_refused_with(&error, "BC-1.evidence_complete");
        assert_no_attempt(&path);
    }

    /// P-1: a measured, positive and fresh review is admitted, and the
    /// attempt carries the nine `BC-5` fields.
    #[test]
    fn p1_a_positive_fresh_review_carries_the_nine_audit_fields() {
        let (_directory, path) = database();
        submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect("a positive fresh review must be admitted");

        let connection = Connection::open(&path).unwrap();
        // The attempt id is derived from the scope, the loop, the review
        // generation and the nonce when the caller does not name one.
        let expected_attempt_id = format!("core4:{LOOP_KEY}:9:42");
        let evidence_json: String = connection
            .query_row(
                "SELECT evidence_json FROM manager_cow_execution_attempts
                  WHERE attempt_id = ?1",
                params![expected_attempt_id],
                |row| row.get(0),
            )
            .unwrap();
        let evidence: serde_json::Value = serde_json::from_str(&evidence_json).unwrap();
        let audit = evidence
            .get("broadcastAudit")
            .expect("the BC-5 audit record must be attached to the attempt");

        // The nine fields.
        assert!(audit.get("reviewIdentifier").is_some(), "1 review identity");
        assert_eq!(
            audit.get("signedPnlAmount").and_then(|value| value.as_f64()),
            Some(12.5),
            "2 the measured PnL"
        );
        assert_eq!(
            audit.get("reviewAgeSeconds").and_then(|value| value.as_i64()),
            Some(60),
            "3 the review age in seconds"
        );
        assert!(audit.get("idempotencyKey").is_some(), "4 the idempotency key");
        assert_eq!(
            audit.get("chainId").and_then(|value| value.as_i64()),
            Some(CHAIN_ID),
            "5 the chain"
        );
        assert!(audit.get("accountIdentifier").is_some(), "6 the account");
        assert_eq!(
            audit.get("limitVerdict").and_then(|value| value.as_str()),
            Some("within_limit"),
            "7 the limit verdict"
        );
        assert!(audit.get("broadcastAt").is_some(), "8 the broadcast timestamp");
        assert_eq!(
            audit.get("outcome").and_then(|value| value.as_str()),
            Some("admitted"),
            "9 the outcome"
        );

        // The full trail is behind the nine fields.
        let trail = audit
            .get("admission")
            .and_then(|value| value.get("checks"))
            .and_then(|value| value.as_array())
            .expect("the admission trail must be embedded");
        assert!(!trail.is_empty());
    }

    /// BC-3: a refusal is recorded, exactly like a send.
    #[test]
    fn bc3_a_refusal_is_recorded_with_its_reason() {
        let (_directory, path) = database();
        set_review_facts(
            &path,
            "not_executable",
            "{\"ok\":false,\"executable\":false}",
            REVIEWED_AT,
        );
        submit_attempts(&path, vec![attempt(None, 42)], true)
            .expect_err("the request must be refused");

        let connection = Connection::open(&path).unwrap();
        let (code, message, payload): (String, Option<String>, String) = connection
            .query_row(
                "SELECT diagnostic_code, message, payload_json
                   FROM manager_broadcast_attempt_diagnostics
                  WHERE network = ?1 AND chain_id = ?2",
                params![NETWORK, CHAIN_ID],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .expect("the refusal must leave a trail");
        assert_eq!(code, "BC-1_BC-2_ADMISSION_REFUSED");
        let message = message.unwrap_or_default();
        assert!(
            message.contains("BC-1.run_ok"),
            "the trail must name the rule: {message}"
        );
        let payload: serde_json::Value = serde_json::from_str(&payload).unwrap();
        assert_eq!(payload.get("outcome").and_then(|v| v.as_str()), Some("refused"));
        assert_eq!(
            payload.get("broadcasted").and_then(|v| v.as_bool()),
            Some(false),
            "the trail must state that nothing went out"
        );
        let rules = payload
            .get("unsatisfiedRules")
            .and_then(|value| value.as_array())
            .expect("the trail must list the unsatisfied rules");
        assert!(rules.iter().any(|rule| rule == "BC-1.run_ok"));
    }

    /// A dry run leaves **no** trail: it writes nothing at all.
    #[test]
    fn a_dry_run_leaves_no_refusal_trail() {
        let (_directory, path) = database();
        set_review_facts(
            &path,
            "not_executable",
            "{\"ok\":false,\"executable\":false}",
            REVIEWED_AT,
        );
        submit_attempts(&path, vec![attempt(None, 42)], false)
            .expect_err("the request must be refused");
        let connection = Connection::open(&path).unwrap();
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_broadcast_attempt_diagnostics",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0, "a dry run must not write anything");
    }

    /// P-2: the guardrails refuse on their own terms.
    ///
    /// The three sub-cases are checked separately because they have
    /// different meanings: a missing chain refuses, a missing account
    /// refuses, a breached limit refuses, and a closed window defers
    /// this round.
    #[test]
    fn p2_a_missing_chain_refuses_the_broadcast() {
        let (_directory, path) = database();
        let error = submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![attempt(None, 42)],
            true,
            BroadcastGuardPolicy {
                chain_id: None,
                ..guard_policy()
            },
            BTreeMap::from([(LOOP_KEY.to_owned(), 1_000u128)]),
            Some(FIXTURE_NOW.to_owned()),
        )
        .expect_err("a deployment with no chain must be refused");
        assert_refused_with(&error, "BC-2.chain_matches");
        assert_no_attempt(&path);
    }

    /// P-2: an attempt that names a different account is refused.
    #[test]
    fn p2_a_foreign_account_refuses_the_broadcast() {
        let (_directory, path) = database();
        let mut foreign = attempt(None, 42);
        foreign.signer_address = "0xcccccccccccccccccccccccccccccccccccccccc".to_owned();
        let error = submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![foreign],
            true,
            guard_policy(),
            BTreeMap::from([(LOOP_KEY.to_owned(), 1_000u128)]),
            Some(FIXTURE_NOW.to_owned()),
        )
        .expect_err("an attempt naming a foreign account must be refused");
        assert_refused_with(&error, "BC-2.account_matches");
        assert_no_attempt(&path);
    }

    /// P-2: an amount above the limit is refused.
    #[test]
    fn p2_an_amount_above_the_limit_refuses_the_broadcast() {
        let (_directory, path) = database();
        let error = submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![attempt(None, 42)],
            true,
            BroadcastGuardPolicy {
                max_amount_raw: Some(10u128),
                ..guard_policy()
            },
            BTreeMap::from([(LOOP_KEY.to_owned(), 1_000u128)]),
            Some(FIXTURE_NOW.to_owned()),
        )
        .expect_err("an amount above the limit must be refused");
        assert_refused_with(&error, "BC-2.amount_within_limit");
        assert_no_attempt(&path);
    }

    /// P-2: a request outside the time window is not sent this round.
    #[test]
    fn p2_a_request_outside_the_window_is_not_sent_this_round() {
        let (_directory, path) = database();
        let error = submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![attempt(None, 42)],
            true,
            BroadcastGuardPolicy {
                window_start: Some("2020-01-01T00:00:00Z".to_owned()),
                window_end: Some("2020-01-02T00:00:00Z".to_owned()),
                ..guard_policy()
            },
            BTreeMap::from([(LOOP_KEY.to_owned(), 1_000u128)]),
            Some(FIXTURE_NOW.to_owned()),
        )
        .expect_err("a request outside the window must not be sent");
        assert_refused_with(&error, "BC-2.within_window");
        assert_no_attempt(&path);
    }

    /// An **unmeasured** amount is not zero: it cannot be shown to be
    /// inside a limit, so it is refused.
    #[test]
    fn an_unmeasured_amount_is_refused() {
        let (_directory, path) = database();
        let error = submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![attempt(None, 42)],
            true,
            guard_policy(),
            BTreeMap::new(),
            Some(FIXTURE_NOW.to_owned()),
        )
        .expect_err("an unmeasured amount must be refused");
        assert_refused_with(&error, "BC-2.amount_within_limit");
        assert_no_attempt(&path);
    }

    /// A deployment with **no** guardrails at all is refused, not
    /// defaulted open.
    #[test]
    fn a_deployment_without_guardrails_is_refused() {
        let (_directory, path) = database();
        let error = submit_broadcasts(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![attempt(None, 42)],
            true,
            BroadcastGuardPolicy::default(),
            BTreeMap::from([(LOOP_KEY.to_owned(), 1_000u128)]),
            Some(FIXTURE_NOW.to_owned()),
        )
        .expect_err("a deployment without guardrails must be refused");
        assert_refused_with(&error, "BC-2.chain_matches");
        assert_refused_with(&error, "BC-2.account_matches");
        assert_refused_with(&error, "BC-2.amount_within_limit");
        assert_refused_with(&error, "BC-2.within_window");
        assert_no_attempt(&path);
    }
}
