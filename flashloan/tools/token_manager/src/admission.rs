use crate::model::{AdmissionStatus, LibraryKey};
use crate::store::{library_metadata_checksum, StoreError};
use manager_contracts::TokenKeyId;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fmt;
use std::path::Path;

const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_REASON_LENGTH: usize = 1024;
const MAX_EVIDENCE_BYTES: usize = 16 * 1024;

#[derive(Debug)]
pub enum AdmissionError {
    Sqlite(rusqlite::Error),
    Store(StoreError),
    InvalidRequest(String),
    TokenNotFound {
        library: LibraryKey,
        token_key_id: String,
    },
    LibraryStateMissing {
        network: String,
        chain_id: u64,
        library: LibraryKey,
    },
    IdempotencyKeyReused {
        idempotency_key: String,
        existing_request_id: String,
        submitted_request_id: String,
    },
    Json(serde_json::Error),
}

impl fmt::Display for AdmissionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "admission sqlite error: {error}"),
            Self::Store(error) => write!(formatter, "admission storage error: {error}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid admission request: {message}")
            }
            Self::TokenNotFound {
                library,
                token_key_id,
            } => write!(
                formatter,
                "token {token_key_id} is not present in library {}",
                library.as_str()
            ),
            Self::LibraryStateMissing {
                network,
                chain_id,
                library,
            } => write!(
                formatter,
                "library state is missing for {network}:{chain_id}:{}",
                library.as_str()
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
            Self::Json(error) => write!(formatter, "admission json error: {error}"),
        }
    }
}

impl std::error::Error for AdmissionError {}

impl From<rusqlite::Error> for AdmissionError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<StoreError> for AdmissionError {
    fn from(error: StoreError) -> Self {
        Self::Store(error)
    }
}

impl From<serde_json::Error> for AdmissionError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone)]
pub struct AdmissionChangeRequest {
    pub network: String,
    pub chain_id: u64,
    pub library: LibraryKey,
    pub token_key_id: String,
    pub target_status: AdmissionStatus,
    pub reason: String,
    pub evidence: Value,
    pub request_id: String,
    pub idempotency_key: String,
    pub dry_run: bool,
    pub actor: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AdmissionChangeResult {
    pub network: String,
    pub chain_id: u64,
    pub library: LibraryKey,
    pub token_key_id: String,
    pub previous_status: AdmissionStatus,
    pub new_status: AdmissionStatus,
    pub changed: bool,
    pub dry_run: bool,
    pub version_before: i64,
    pub version_after: i64,
    pub metadata_checksum: String,
    pub event_id: Option<String>,
    pub request_id: String,
    pub idempotency_key: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AdmissionChangeOutcome {
    #[serde(flatten)]
    pub result: AdmissionChangeResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct IdempotencyMetadata {
    request_id: String,
    idempotency_key: String,
    request_digest: String,
    dry_run: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct WriteRequestEvidence {
    reason: String,
    target_status: AdmissionStatus,
    evidence: Value,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedWriteRecord {
    idempotency: IdempotencyMetadata,
    request: WriteRequestEvidence,
    result: AdmissionChangeResult,
}

#[derive(Debug)]
struct MutableToken {
    admission_status: AdmissionStatus,
    first_admitted_at: Option<String>,
}

#[derive(Debug)]
struct MutableLibraryState {
    version: i64,
    metadata_checksum: String,
}

pub fn change_admission(
    database_path: impl AsRef<Path>,
    request: AdmissionChangeRequest,
) -> Result<AdmissionChangeOutcome, AdmissionError> {
    let request = validate_request(request)?;
    let request_digest = request_digest(&request)?;
    let mut connection = Connection::open(database_path)?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;

    if let Some(evidence_json) = find_idempotency_record(
        &transaction,
        &request.network,
        request.chain_id,
        &request.idempotency_key,
    )? {
        let persisted = serde_json::from_str::<PersistedWriteRecord>(&evidence_json)?;
        if persisted.idempotency.request_digest != request_digest {
            return Err(AdmissionError::IdempotencyKeyReused {
                idempotency_key: request.idempotency_key.clone(),
                existing_request_id: persisted.idempotency.request_id,
                submitted_request_id: request.request_id,
            });
        }
        transaction.rollback()?;
        return Ok(AdmissionChangeOutcome {
            result: persisted.result,
            idempotent_replay: true,
        });
    }

    let token = load_mutable_token(&transaction, &request)?;
    let state = load_library_state(&transaction, &request)?;
    let now = chrono::Utc::now().to_rfc3339();
    let changed = token.admission_status != request.target_status;
    let version_after = state.version + i64::from(changed);
    let event_id = (!request.dry_run)
        .then(|| random_event_id(&transaction))
        .transpose()?;

    if changed {
        update_token(
            &transaction,
            &request,
            token.first_admitted_at.as_deref(),
            &now,
        )?;
        refresh_library_state(&transaction, &request, version_after, &now)?;
    }

    let metadata_checksum = if changed {
        library_metadata_checksum(
            &transaction,
            request.library,
            &request.network,
            request.chain_id,
        )?
    } else {
        state.metadata_checksum.clone()
    };
    let result = AdmissionChangeResult {
        network: request.network.clone(),
        chain_id: request.chain_id,
        library: request.library,
        token_key_id: request.token_key_id.clone(),
        previous_status: token.admission_status,
        new_status: request.target_status,
        changed,
        dry_run: request.dry_run,
        version_before: state.version,
        version_after,
        metadata_checksum,
        event_id: event_id.clone(),
        request_id: request.request_id.clone(),
        idempotency_key: request.idempotency_key.clone(),
    };

    if !request.dry_run {
        let record = PersistedWriteRecord {
            idempotency: IdempotencyMetadata {
                request_id: request.request_id.clone(),
                idempotency_key: request.idempotency_key.clone(),
                request_digest,
                dry_run: false,
            },
            request: WriteRequestEvidence {
                reason: request.reason.clone(),
                target_status: request.target_status,
                evidence: request.evidence.clone(),
            },
            result: result.clone(),
        };
        insert_write_event(
            &transaction,
            &request,
            token.admission_status,
            event_id.as_deref().expect("apply event id exists"),
            &now,
            &record,
        )?;
        transaction.commit()?;
    } else {
        transaction.rollback()?;
    }

    Ok(AdmissionChangeOutcome {
        result,
        idempotent_replay: false,
    })
}

fn validate_request(
    mut request: AdmissionChangeRequest,
) -> Result<AdmissionChangeRequest, AdmissionError> {
    if request.network.trim().is_empty() || request.chain_id == 0 {
        return Err(AdmissionError::InvalidRequest(
            "network and chain_id are required".to_owned(),
        ));
    }
    if !matches!(
        request.target_status,
        AdmissionStatus::Admitted | AdmissionStatus::Blocked
    ) {
        return Err(AdmissionError::InvalidRequest(
            "target_status must be admitted or blocked".to_owned(),
        ));
    }
    let parsed = request
        .token_key_id
        .parse::<TokenKeyId>()
        .map_err(|error| AdmissionError::InvalidRequest(format!("token_key_id: {error}")))?;
    if parsed.network() != request.network
        || parsed.chain_id() != request.chain_id
        || parsed.as_str() != request.token_key_id
    {
        return Err(AdmissionError::InvalidRequest(
            "token_key_id does not match network and chain_id".to_owned(),
        ));
    }
    validate_request_token("X-Request-Id", &request.request_id, MAX_REQUEST_ID_LENGTH)?;
    validate_request_token(
        "Idempotency-Key",
        &request.idempotency_key,
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )?;
    request.reason = request.reason.trim().to_owned();
    if request.reason.is_empty() || request.reason.len() > MAX_REASON_LENGTH {
        return Err(AdmissionError::InvalidRequest(format!(
            "reason must contain 1..={MAX_REASON_LENGTH} bytes"
        )));
    }
    let evidence_bytes = serde_json::to_vec(&request.evidence)?.len();
    if evidence_bytes > MAX_EVIDENCE_BYTES {
        return Err(AdmissionError::InvalidRequest(format!(
            "evidence exceeds {MAX_EVIDENCE_BYTES} bytes"
        )));
    }
    request.actor = request.actor.trim().to_owned();
    if request.actor.is_empty() {
        request.actor = "manager_api".to_owned();
    }
    Ok(request)
}

fn validate_request_token(
    header: &str,
    value: &str,
    maximum_length: usize,
) -> Result<(), AdmissionError> {
    if value.is_empty() || value.len() > maximum_length {
        return Err(AdmissionError::InvalidRequest(format!(
            "{header} must contain 1..={maximum_length} characters"
        )));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':'))
    {
        return Err(AdmissionError::InvalidRequest(format!(
            "{header} contains unsupported characters"
        )));
    }
    Ok(())
}

fn request_digest(request: &AdmissionChangeRequest) -> Result<String, AdmissionError> {
    let payload = json!({
        "network": request.network,
        "chainId": request.chain_id,
        "libraryKey": request.library,
        "tokenKeyId": request.token_key_id,
        "targetStatus": request.target_status,
        "reason": request.reason,
        "evidence": request.evidence,
        "dryRun": request.dry_run
    });
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&payload)?))
    ))
}

fn find_idempotency_record(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    idempotency_key: &str,
) -> Result<Option<String>, AdmissionError> {
    Ok(connection
        .query_row(
            "SELECT evidence_json
             FROM token_admission_events
             WHERE network = ?1 AND chain_id = ?2
               AND json_valid(evidence_json)
               AND json_extract(evidence_json, '$.idempotency.idempotencyKey') = ?3
             ORDER BY created_at, event_id
             LIMIT 1",
            params![network, chain_id, idempotency_key],
            |row| row.get(0),
        )
        .optional()?)
}

fn load_mutable_token(
    connection: &Connection,
    request: &AdmissionChangeRequest,
) -> Result<MutableToken, AdmissionError> {
    let query = format!(
        "SELECT admission_status, first_admitted_at
         FROM {}
         WHERE network = ?1 AND chain_id = ?2 AND token_key_id = ?3",
        request.library.table_name()
    );
    let row = connection
        .query_row(
            &query,
            params![request.network, request.chain_id, request.token_key_id],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?)),
        )
        .optional()?
        .ok_or_else(|| AdmissionError::TokenNotFound {
            library: request.library,
            token_key_id: request.token_key_id.clone(),
        })?;
    let admission_status = AdmissionStatus::from_storage(&row.0).ok_or_else(|| {
        AdmissionError::InvalidRequest(format!("unknown stored admission status {}", row.0))
    })?;
    Ok(MutableToken {
        admission_status,
        first_admitted_at: row.1,
    })
}

fn load_library_state(
    connection: &Connection,
    request: &AdmissionChangeRequest,
) -> Result<MutableLibraryState, AdmissionError> {
    connection
        .query_row(
            "SELECT library_version, metadata_checksum
             FROM token_library_state
             WHERE network = ?1 AND chain_id = ?2 AND library_key = ?3",
            params![request.network, request.chain_id, request.library.as_str()],
            |row| {
                Ok(MutableLibraryState {
                    version: row.get(0)?,
                    metadata_checksum: row.get(1)?,
                })
            },
        )
        .optional()?
        .ok_or_else(|| AdmissionError::LibraryStateMissing {
            network: request.network.clone(),
            chain_id: request.chain_id,
            library: request.library,
        })
}

fn update_token(
    connection: &Connection,
    request: &AdmissionChangeRequest,
    first_admitted_at: Option<&str>,
    now: &str,
) -> Result<(), AdmissionError> {
    let query = format!(
        "UPDATE {}
         SET admission_status = ?1,
             admission_source = ?2,
             first_admitted_at = ?3,
             updated_at = ?4,
             blocked_reason = ?5
         WHERE network = ?6 AND chain_id = ?7 AND token_key_id = ?8",
        request.library.table_name()
    );
    let first_admitted_at = match request.target_status {
        AdmissionStatus::Admitted => first_admitted_at.unwrap_or(now),
        _ => first_admitted_at.unwrap_or_default(),
    };
    connection.execute(
        &query,
        params![
            request.target_status.as_str(),
            "manager_api",
            (!first_admitted_at.is_empty()).then_some(first_admitted_at),
            now,
            (request.target_status == AdmissionStatus::Blocked).then(|| request.reason.clone()),
            request.network,
            request.chain_id,
            request.token_key_id
        ],
    )?;
    Ok(())
}

fn refresh_library_state(
    connection: &Connection,
    request: &AdmissionChangeRequest,
    version_after: i64,
    now: &str,
) -> Result<(), AdmissionError> {
    let table = request.library.table_name();
    let token_count: i64 = connection.query_row(
        &format!("SELECT COUNT(*) FROM {table} WHERE network = ?1 AND chain_id = ?2"),
        params![request.network, request.chain_id],
        |row| row.get(0),
    )?;
    let admitted_count: i64 = connection.query_row(
        &format!(
            "SELECT COUNT(*) FROM {table}
             WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'"
        ),
        params![request.network, request.chain_id],
        |row| row.get(0),
    )?;
    let checksum = library_metadata_checksum(
        connection,
        request.library,
        &request.network,
        request.chain_id,
    )?;
    connection.execute(
        "UPDATE token_library_state
         SET library_version = ?1,
             token_count = ?2,
             admitted_count = ?3,
             metadata_checksum = ?4,
             generated_at = ?5,
             updated_at = ?5
         WHERE network = ?6 AND chain_id = ?7 AND library_key = ?8",
        params![
            version_after,
            token_count,
            admitted_count,
            checksum,
            now,
            request.network,
            request.chain_id,
            request.library.as_str()
        ],
    )?;
    Ok(())
}

fn random_event_id(connection: &Connection) -> Result<String, AdmissionError> {
    connection
        .query_row("SELECT lower(hex(randomblob(16)))", [], |row| row.get(0))
        .map_err(AdmissionError::from)
}

fn insert_write_event(
    connection: &Connection,
    request: &AdmissionChangeRequest,
    previous_status: AdmissionStatus,
    event_id: &str,
    created_at: &str,
    record: &PersistedWriteRecord,
) -> Result<(), AdmissionError> {
    let evidence_json = serde_json::to_string(record)?;
    connection.execute(
        "INSERT INTO token_admission_events (
             event_id, network, chain_id, token_key_id, library_key,
             previous_status, new_status, reason, actor, evidence_json, created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
        params![
            event_id,
            request.network,
            request.chain_id,
            request.token_key_id,
            request.library.as_str(),
            previous_status.as_str(),
            request.target_status.as_str(),
            request.reason,
            request.actor,
            evidence_json,
            created_at
        ],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{change_admission, AdmissionChangeRequest, AdmissionError};
    use crate::model::{AdmissionStatus, LibraryKey, TokenWrite};
    use crate::store::write_library;
    use manager_contracts::TokenKeyId;
    use rusqlite::Connection;
    use serde_json::json;

    const USDG: &str = "0x1111111111111111111111111111111111111111";

    fn fixture(status: AdmissionStatus) -> TokenWrite {
        TokenWrite {
            network: "robinhood".to_owned(),
            chain_id: 4663,
            token_key_id: TokenKeyId::new("robinhood", 4663, USDG).unwrap(),
            v4_currency_key: USDG.to_owned(),
            token_address: USDG.to_owned(),
            token_symbol: "USDG".to_owned(),
            token_name: Some("USDG".to_owned()),
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: true,
            admission_status: status,
            admission_source: "fixture".to_owned(),
            metadata_source: "fixture".to_owned(),
            metadata_checksum: format!("sha256:{USDG:0>64}"),
            source_json: Some("{}".to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    fn database(status: AdmissionStatus) -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = Connection::open(&path).unwrap();
        write_library(
            &mut connection,
            LibraryKey::Core,
            "robinhood",
            4663,
            vec![fixture(status)],
            Some("fixture:v1"),
            "test",
            "fixture",
        )
        .unwrap();
        drop(connection);
        (directory, path)
    }

    fn request(target: AdmissionStatus, dry_run: bool) -> AdmissionChangeRequest {
        AdmissionChangeRequest {
            network: "robinhood".to_owned(),
            chain_id: 4663,
            library: LibraryKey::Core,
            token_key_id: format!("robinhood:4663:{USDG}"),
            target_status: target,
            reason: "operator verified".to_owned(),
            evidence: json!({"ticket":"OP-1"}),
            request_id: "request-1".to_owned(),
            idempotency_key: "idem-1".to_owned(),
            dry_run,
            actor: "test".to_owned(),
        }
    }

    #[test]
    fn dry_run_reports_change_without_persisting_status_version_or_event() {
        let (_directory, path) = database(AdmissionStatus::Admitted);
        let outcome = change_admission(&path, request(AdmissionStatus::Blocked, true)).unwrap();

        assert!(outcome.result.changed);
        assert!(outcome.result.dry_run);
        assert_eq!(outcome.result.version_before, 1);
        assert_eq!(outcome.result.version_after, 2);
        assert!(outcome.result.event_id.is_none());

        let connection = Connection::open(path).unwrap();
        let status: String = connection
            .query_row("SELECT admission_status FROM token_core", [], |row| {
                row.get(0)
            })
            .unwrap();
        let version: i64 = connection
            .query_row(
                "SELECT library_version FROM token_library_state WHERE library_key = 'core'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(status, "admitted");
        assert_eq!(version, 1);
        assert_eq!(event_count, 1);
    }

    #[test]
    fn apply_is_atomic_and_same_idempotency_key_replays_first_result() {
        let (_directory, path) = database(AdmissionStatus::Admitted);
        let request = request(AdmissionStatus::Blocked, false);
        let first = change_admission(&path, request.clone()).unwrap();
        assert!(first.result.changed);
        assert!(!first.idempotent_replay);
        assert_eq!(first.result.version_before, 1);
        assert_eq!(first.result.version_after, 2);
        assert!(first.result.event_id.is_some());

        let replay = change_admission(&path, request).unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, first.result);

        let connection = Connection::open(path).unwrap();
        let (status, blocked_reason): (String, Option<String>) = connection
            .query_row(
                "SELECT admission_status, blocked_reason FROM token_core",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        let version: i64 = connection
            .query_row(
                "SELECT library_version FROM token_library_state WHERE library_key = 'core'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        let evidence: String = connection
            .query_row(
                "SELECT evidence_json FROM token_admission_events
                 ORDER BY created_at DESC, event_id DESC LIMIT 1",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(status, "blocked");
        assert_eq!(blocked_reason.as_deref(), Some("operator verified"));
        assert_eq!(version, 2);
        assert_eq!(event_count, 2);
        assert!(evidence.contains("\"requestId\":\"request-1\""));
        assert!(evidence.contains("\"idempotencyKey\":\"idem-1\""));
    }

    #[test]
    fn same_key_with_different_digest_is_rejected() {
        let (_directory, path) = database(AdmissionStatus::Admitted);
        change_admission(&path, request(AdmissionStatus::Blocked, false)).unwrap();
        let mut changed = request(AdmissionStatus::Blocked, false);
        changed.reason = "different intent".to_owned();
        let error = change_admission(&path, changed).unwrap_err();
        assert!(matches!(error, AdmissionError::IdempotencyKeyReused { .. }));
    }

    #[test]
    fn noop_apply_persists_idempotency_without_incrementing_version() {
        let (_directory, path) = database(AdmissionStatus::Admitted);
        let request = request(AdmissionStatus::Admitted, false);
        let first = change_admission(&path, request.clone()).unwrap();
        assert!(!first.result.changed);
        assert_eq!(first.result.version_before, 1);
        assert_eq!(first.result.version_after, 1);
        assert!(first.result.event_id.is_some());

        let replay = change_admission(&path, request).unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, first.result);

        let connection = Connection::open(path).unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(event_count, 2);
    }

    #[test]
    fn missing_token_is_rejected_before_event_creation() {
        let (_directory, path) = database(AdmissionStatus::Admitted);
        let mut request = request(AdmissionStatus::Blocked, false);
        request.token_key_id =
            "robinhood:4663:0x2222222222222222222222222222222222222222".to_owned();
        request.idempotency_key = "missing-token".to_owned();
        let error = change_admission(&path, request).unwrap_err();
        assert!(matches!(error, AdmissionError::TokenNotFound { .. }));
    }
}
