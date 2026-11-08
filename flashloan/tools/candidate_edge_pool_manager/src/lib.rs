use manager_contracts::{normalize_address, PairKeyId, TokenKeyId};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;
use std::str::FromStr;

const PROTOCOL: &str = "uniswap_v4";
const SCOPE_KEY: &str = "core4";
const MAX_RECOMMENDATIONS_PER_HOP: usize = 5;
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_SNAPSHOT_LIMIT: usize = 1_000;
const Q96: f64 = 79_228_162_514_264_337_593_543_950_336.0;

#[derive(Debug)]
pub enum CandidateEdgeError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    ComparisonRead {
        stage: String,
        source: rusqlite::Error,
    },
    InvalidRequest(String),
    MissingPoolScope,
    MissingCoreLibraryState,
    NoAdmittedCoreTokens,
    InvalidStoredPool(String),
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

impl fmt::Display for CandidateEdgeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "candidate edge storage error: {error}"),
            Self::Json(error) => write!(formatter, "candidate edge json error: {error}"),
            Self::ComparisonRead { stage, source } => {
                write!(
                    formatter,
                    "candidate comparison {stage} read failed: {source}"
                )
            }
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid candidate edge request: {message}")
            }
            Self::MissingPoolScope => {
                formatter.write_str("pool scope core4 has not been materialized")
            }
            Self::MissingCoreLibraryState => {
                formatter.write_str("token_core library state is missing")
            }
            Self::NoAdmittedCoreTokens => {
                formatter.write_str("token_core has no admitted anchor tokens")
            }
            Self::InvalidStoredPool(message) => {
                write!(formatter, "invalid stored core4 pool: {message}")
            }
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

impl std::error::Error for CandidateEdgeError {}

impl From<rusqlite::Error> for CandidateEdgeError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<serde_json::Error> for CandidateEdgeError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpectedUpstream {
    pub pool_scope_checksum: Option<String>,
    #[serde(default)]
    pub library_versions: BTreeMap<String, i64>,
    #[serde(default)]
    pub library_checksums: BTreeMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct CandidateEdgeWriteRequest {
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
pub struct CandidateEdgeMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub apply: bool,
    pub dry_run: bool,
    pub pool_scope_checksum: String,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub source_pool_count: usize,
    pub eligible_pool_count: usize,
    pub abnormal_pool_count: usize,
    pub anchor_token_count: usize,
    pub candidate_token_count: usize,
    pub recommendation_count: usize,
    pub skip_reason_counts: BTreeMap<String, usize>,
    pub changed_rows: usize,
    pub candidate_version: i64,
    pub materialized_checksum: String,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateEdgeWriteOutcome {
    #[serde(flatten)]
    pub result: CandidateEdgeMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateEdgeStatus {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub candidate_version: i64,
    pub materialized_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub anchor_token_count: usize,
    pub candidate_token_count: usize,
    pub eligible_pool_count: usize,
    pub recommendation_count: usize,
    pub abnormal_pool_count: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateAnchorTokenRow {
    pub anchor_token_key_id: String,
    pub token_key_id: String,
    pub token_role: String,
    pub anchor_library_key: String,
    pub token_library_key: String,
    pub token_symbol: String,
    pub pool_count: usize,
    pub yc_primary_pool_id: Option<String>,
    pub zc_primary_pool_id: Option<String>,
    pub yc_backup_pool_ids: Vec<String>,
    pub zc_backup_pool_ids: Vec<String>,
    pub yc_zc_same_set: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateTokenRow {
    pub token_key_id: String,
    pub token_address: String,
    pub token_symbol: String,
    pub token_name: Option<String>,
    pub token_decimals: i64,
    pub token_library_key: String,
    pub admission_status: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidatePoolPairRow {
    pub anchor_token_key_id: String,
    pub candidate_token_key_id: String,
    pub pair_key_id: String,
    pub first_direction_key: String,
    pub third_direction_key: String,
    pub pool_count: usize,
    pub first_primary_pool_id: Option<String>,
    pub third_primary_pool_id: Option<String>,
    pub first_backup_pool_ids: Vec<String>,
    pub third_backup_pool_ids: Vec<String>,
    pub selection_basis: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidatePoolRow {
    pub anchor_token_key_id: String,
    pub candidate_token_key_id: String,
    pub pair_key_id: String,
    pub pool_id: String,
    pub pool_manager: String,
    pub pool_key_json: String,
    pub token0: String,
    pub token1: String,
    pub token0_key_id: String,
    pub token1_key_id: String,
    pub fee: i64,
    pub tick_spacing: i64,
    pub hooks: String,
    pub sqrt_price_x96: Option<String>,
    pub tick: Option<i64>,
    pub liquidity: Option<String>,
    pub liquidity_index: Option<f64>,
    pub price: Option<f64>,
    pub depth_status: Option<String>,
    pub liquidity_read_at: Option<String>,
    pub liquidity_block_number: Option<i64>,
    pub evidence_source: Option<String>,
    pub is_first_hop_recommended: bool,
    pub is_third_hop_recommended: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateRecommendationRow {
    pub anchor_token_key_id: String,
    pub candidate_token_key_id: String,
    pub pair_key_id: String,
    pub direction_key: String,
    pub hop: String,
    pub rank: usize,
    pub pool_id: String,
    pub depth_score: f64,
    pub fee: i64,
    pub selection_basis: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AbnormalCandidateTokenRow {
    pub anchor_token_key_id: String,
    pub token_key_id: String,
    pub anchor_library_key: String,
    pub token_library_key: String,
    pub token_symbol: String,
    pub pool_count: usize,
    pub reasons: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AbnormalCandidatePairRow {
    pub anchor_token_key_id: String,
    pub candidate_token_key_id: String,
    pub pair_key_id: String,
    pub pool_count: usize,
    pub reasons: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AbnormalCandidatePoolRow {
    pub anchor_token_key_id: String,
    pub candidate_token_key_id: String,
    pub pair_key_id: String,
    pub pool_id: String,
    pub pool_status: String,
    pub pool_key_json: String,
    pub token0_key_id: String,
    pub token1_key_id: String,
    pub fee: i64,
    pub tick_spacing: i64,
    pub hooks: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateEdgeSnapshot {
    pub status: CandidateEdgeStatus,
    pub tokens: Vec<CandidateTokenRow>,
    pub anchor_tokens: Vec<CandidateAnchorTokenRow>,
    pub pool_pairs: Vec<CandidatePoolPairRow>,
    pub pools: Vec<CandidatePoolRow>,
    pub recommendations: Vec<CandidateRecommendationRow>,
    pub abnormal_anchor_tokens: Vec<AbnormalCandidateTokenRow>,
    pub abnormal_pool_pairs: Vec<AbnormalCandidatePairRow>,
    pub abnormal_pools: Vec<AbnormalCandidatePoolRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateComparisonSummary {
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
pub struct CandidateComparisonDifference {
    pub category: String,
    pub key: String,
    pub kind: String,
    pub manager_payload: Option<String>,
    pub legacy_payload: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateEdgeComparison {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub legacy_anchor_scope: String,
    pub manager_checksum: Option<String>,
    pub ready_for_cutover: bool,
    pub anchor_tokens: CandidateComparisonSummary,
    pub pool_pairs: CandidateComparisonSummary,
    pub pools: CandidateComparisonSummary,
    pub recommendations: CandidateComparisonSummary,
    pub unresolved_legacy_anchors: Vec<String>,
    pub cause_counts: BTreeMap<String, usize>,
    pub differences: Vec<CandidateComparisonDifference>,
    pub differences_truncated: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedCandidateWrite {
    request_id: String,
    request_digest: String,
    result: CandidateEdgeMaterializationResult,
}

#[derive(Debug, Clone)]
struct CatalogToken {
    token_key_id: String,
    token_address: String,
    token_symbol: String,
    token_decimals: u8,
    library_key: String,
}

#[derive(Debug, Clone)]
struct ScopePool {
    pool_id: String,
    pool_manager: String,
    pool_key_json: String,
    token0: String,
    token1: String,
    token0_key_id: String,
    token1_key_id: String,
    fee: i64,
    tick_spacing: i64,
    hooks: String,
    pool_status: String,
    sqrt_price_x96: Option<String>,
    tick: Option<i64>,
    liquidity: Option<String>,
    liquidity_index: Option<f64>,
    price: Option<f64>,
    depth_status: Option<String>,
    liquidity_read_at: Option<String>,
    liquidity_block_number: Option<i64>,
    evidence_source: Option<String>,
}

struct CandidateInput {
    pool_scope_checksum: String,
    library_versions: BTreeMap<String, i64>,
    library_checksums: BTreeMap<String, String>,
    catalog: BTreeMap<String, CatalogToken>,
    anchors: BTreeSet<String>,
    pools: Vec<ScopePool>,
}

#[derive(Default)]
struct DesiredCandidateData {
    anchor_tokens: Vec<CandidateAnchorTokenRow>,
    pool_pairs: Vec<CandidatePoolPairRow>,
    pools: Vec<CandidatePoolRow>,
    recommendations: Vec<CandidateRecommendationRow>,
    abnormal_anchor_tokens: Vec<AbnormalCandidateTokenRow>,
    abnormal_pool_pairs: Vec<AbnormalCandidatePairRow>,
    abnormal_pools: Vec<AbnormalCandidatePoolRow>,
    skip_reason_counts: BTreeMap<String, usize>,
    anchor_count: usize,
    candidate_token_count: usize,
}

#[derive(Debug, Clone)]
struct Recommendation {
    pool_id: String,
    score: f64,
    fee: i64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CandidateChecksumPayload<'a> {
    pool_scope_checksum: &'a str,
    library_versions: &'a BTreeMap<String, i64>,
    library_checksums: &'a BTreeMap<String, String>,
    anchor_tokens: &'a [CandidateAnchorTokenRow],
    pool_pairs: &'a [CandidatePoolPairRow],
    pools: &'a [CandidatePoolRow],
    recommendations: &'a [CandidateRecommendationRow],
    abnormal_anchor_tokens: &'a [AbnormalCandidateTokenRow],
    abnormal_pool_pairs: &'a [AbnormalCandidatePairRow],
    abnormal_pools: &'a [AbnormalCandidatePoolRow],
}

pub fn materialize_candidate_edges(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<CandidateEdgeMaterializationResult, CandidateEdgeError> {
    materialize_impl(database_path.as_ref(), network, chain_id, apply, None)
        .map(|(result, _)| result)
}

pub fn materialize_candidate_edges_write(
    database_path: impl AsRef<Path>,
    request: CandidateEdgeWriteRequest,
) -> Result<CandidateEdgeWriteOutcome, CandidateEdgeError> {
    let (result, idempotent_replay) = materialize_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        request.apply,
        Some(&request),
    )?;
    Ok(CandidateEdgeWriteOutcome {
        result,
        idempotent_replay,
    })
}

fn materialize_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    request: Option<&CandidateEdgeWriteRequest>,
) -> Result<(CandidateEdgeMaterializationResult, bool), CandidateEdgeError> {
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
    prepared_write: Option<(&CandidateEdgeWriteRequest, String)>,
) -> Result<(CandidateEdgeMaterializationResult, bool), CandidateEdgeError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(evidence_json) = find_write_request(connection, request)? {
                let persisted = serde_json::from_str::<PersistedCandidateWrite>(&evidence_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(CandidateEdgeError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok((persisted.result, true));
            }
        }
    }

    let input = read_candidate_input(connection, network, chain_id)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &input)?;
    }
    let desired = build_candidate_rows(chain_id, &input)?;
    let materialized_checksum = candidate_checksum(&input, &desired)?;
    let previous = read_candidate_status_on_connection(connection, network, chain_id)?;
    let checksum_changed =
        previous.materialized_checksum.as_deref() != Some(materialized_checksum.as_str());
    let candidate_version = if checksum_changed {
        previous.candidate_version.saturating_add(1).max(1)
    } else {
        previous.candidate_version.max(1)
    };
    let changed_rows = if checksum_changed {
        desired.anchor_tokens.len()
            + desired.pool_pairs.len()
            + desired.pools.len()
            + desired.recommendations.len()
            + desired.abnormal_anchor_tokens.len()
            + desired.abnormal_pool_pairs.len()
            + desired.abnormal_pools.len()
    } else {
        0
    };
    let result = CandidateEdgeMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: PROTOCOL.to_owned(),
        scope_key: SCOPE_KEY.to_owned(),
        apply,
        dry_run: !apply,
        pool_scope_checksum: input.pool_scope_checksum.clone(),
        library_versions: input.library_versions.clone(),
        library_checksums: input.library_checksums.clone(),
        source_pool_count: input.pools.len(),
        eligible_pool_count: desired.pools.len(),
        abnormal_pool_count: desired.abnormal_pools.len(),
        anchor_token_count: desired.anchor_count,
        candidate_token_count: desired.candidate_token_count,
        recommendation_count: desired.recommendations.len(),
        skip_reason_counts: desired.skip_reason_counts.clone(),
        changed_rows,
        candidate_version,
        materialized_checksum,
        consumers_need_rebuild: checksum_changed,
    };

    if apply {
        replace_candidate_rows(
            connection,
            network,
            chain_id,
            &desired,
            &result,
            candidate_version,
        )?;
        if let Some((request, request_digest)) = prepared_write {
            persist_write_request(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

fn validate_operation(network: &str, chain_id: i64) -> Result<(), CandidateEdgeError> {
    if network.trim().is_empty() {
        return Err(CandidateEdgeError::InvalidRequest(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(CandidateEdgeError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    Ok(())
}

fn validate_write_request(
    request: &CandidateEdgeWriteRequest,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<(), CandidateEdgeError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(CandidateEdgeError::InvalidRequest(
            "write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.apply != apply {
        return Err(CandidateEdgeError::InvalidRequest(
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
) -> Result<(), CandidateEdgeError> {
    if value.is_empty() || value.len() > max_length {
        return Err(CandidateEdgeError::InvalidRequest(format!(
            "{label} must contain 1..={max_length} bytes"
        )));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':'))
    {
        return Err(CandidateEdgeError::InvalidRequest(format!(
            "{label} contains unsupported characters"
        )));
    }
    Ok(())
}

fn request_digest(request: &CandidateEdgeWriteRequest) -> Result<String, CandidateEdgeError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    sha256_json(&payload)
}

fn find_write_request(
    connection: &Connection,
    request: &CandidateEdgeWriteRequest,
) -> Result<Option<String>, CandidateEdgeError> {
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
    request: &CandidateEdgeWriteRequest,
    request_digest: &str,
    result: &CandidateEdgeMaterializationResult,
) -> Result<(), CandidateEdgeError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    let response_json = serde_json::to_string(&PersistedCandidateWrite {
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

fn read_candidate_input(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<CandidateInput, CandidateEdgeError> {
    let mut library_versions = BTreeMap::new();
    let mut library_checksums = BTreeMap::new();
    let mut state_statement = connection.prepare(
        "SELECT library_key, library_version, metadata_checksum
         FROM token_library_state
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY library_key",
    )?;
    let state_rows = state_statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    for row in state_rows {
        let (library_key, version, checksum) = row?;
        library_versions.insert(library_key.clone(), version);
        library_checksums.insert(library_key, checksum);
    }
    if !library_versions.contains_key("core") {
        return Err(CandidateEdgeError::MissingCoreLibraryState);
    }

    let (pool_scope_checksum, enabled): (String, bool) = connection
        .query_row(
            "SELECT materialized_checksum, enabled
             FROM pool_scope_config
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = ?3",
            params![network, chain_id, SCOPE_KEY],
            |row| Ok((row.get(0)?, row.get::<_, i64>(1)? != 0)),
        )
        .optional()?
        .ok_or(CandidateEdgeError::MissingPoolScope)?;
    if !enabled {
        return Err(CandidateEdgeError::MissingPoolScope);
    }

    let mut catalog = BTreeMap::<String, CatalogToken>::new();
    let mut catalog_statement = connection.prepare(
        "SELECT token_key_id, token_address, token_symbol, token_decimals,
                library_key
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'
         ORDER BY token_key_id,
                  CASE library_key
                      WHEN 'core' THEN 0
                      WHEN 'stocks' THEN 1
                      WHEN 'test' THEN 2
                      ELSE 3
                  END",
    )?;
    let catalog_rows = catalog_statement.query_map(params![network, chain_id], |row| {
        Ok(CatalogToken {
            token_key_id: row.get(0)?,
            token_address: row.get(1)?,
            token_symbol: row.get(2)?,
            token_decimals: row.get(3)?,
            library_key: row.get(4)?,
        })
    })?;
    for row in catalog_rows {
        let token = row?;
        catalog.entry(token.token_key_id.clone()).or_insert(token);
    }
    let anchors = catalog
        .values()
        .filter(|token| token.library_key == "core")
        .map(|token| token.token_key_id.clone())
        .collect::<BTreeSet<_>>();
    if anchors.is_empty() {
        return Err(CandidateEdgeError::NoAdmittedCoreTokens);
    }

    let mut pool_statement = connection.prepare(
        "SELECT pool_id, pool_manager, pool_key_json, token0, token1,
                token0_key_id, token1_key_id, fee, tick_spacing, hooks,
                pool_status, sqrt_price_x96, tick, liquidity, liquidity_index,
                price, depth_status, liquidity_read_at, liquidity_block_number,
                evidence_source
         FROM v4_exchange_pools_core4
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
         ORDER BY pool_id",
    )?;
    let pool_rows = pool_statement.query_map(params![network, chain_id, PROTOCOL], |row| {
        Ok(ScopePool {
            pool_id: row.get(0)?,
            pool_manager: row.get(1)?,
            pool_key_json: row.get(2)?,
            token0: row.get(3)?,
            token1: row.get(4)?,
            token0_key_id: row.get(5)?,
            token1_key_id: row.get(6)?,
            fee: row.get(7)?,
            tick_spacing: row.get(8)?,
            hooks: row.get(9)?,
            pool_status: row.get(10)?,
            sqrt_price_x96: row.get(11)?,
            tick: row.get(12)?,
            liquidity: row.get(13)?,
            liquidity_index: row.get(14)?,
            price: row.get(15)?,
            depth_status: row.get(16)?,
            liquidity_read_at: row.get(17)?,
            liquidity_block_number: row.get(18)?,
            evidence_source: row.get(19)?,
        })
    })?;
    let pools = pool_rows.collect::<Result<Vec<_>, _>>()?;

    Ok(CandidateInput {
        pool_scope_checksum,
        library_versions,
        library_checksums,
        catalog,
        anchors,
        pools,
    })
}

fn validate_expected_upstream(
    expected: &ExpectedUpstream,
    input: &CandidateInput,
) -> Result<(), CandidateEdgeError> {
    if let Some(expected_checksum) = expected.pool_scope_checksum.as_deref() {
        if expected_checksum != input.pool_scope_checksum {
            return Err(CandidateEdgeError::ExpectedUpstreamMismatch {
                field: "poolScopeChecksum".to_owned(),
                expected: expected_checksum.to_owned(),
                actual: input.pool_scope_checksum.clone(),
            });
        }
    }
    for (library_key, expected_version) in &expected.library_versions {
        let actual = input.library_versions.get(library_key);
        if actual != Some(expected_version) {
            return Err(CandidateEdgeError::ExpectedUpstreamMismatch {
                field: format!("libraryVersions.{library_key}"),
                expected: expected_version.to_string(),
                actual: actual
                    .map(ToString::to_string)
                    .unwrap_or_else(|| "missing".to_owned()),
            });
        }
    }
    for (library_key, expected_checksum) in &expected.library_checksums {
        let actual = input.library_checksums.get(library_key);
        if actual != Some(expected_checksum) {
            return Err(CandidateEdgeError::ExpectedUpstreamMismatch {
                field: format!("libraryChecksums.{library_key}"),
                expected: expected_checksum.clone(),
                actual: actual.cloned().unwrap_or_else(|| "missing".to_owned()),
            });
        }
    }
    Ok(())
}

fn build_candidate_rows(
    chain_id: i64,
    input: &CandidateInput,
) -> Result<DesiredCandidateData, CandidateEdgeError> {
    let mut desired = DesiredCandidateData::default();
    let mut groups = BTreeMap::<(String, String), Vec<ScopePool>>::new();
    for pool in &input.pools {
        if let Err(reason) = validate_scope_pool(pool, &input.catalog) {
            *desired.skip_reason_counts.entry(reason).or_insert(0) += 1;
            continue;
        }
        let token0_is_anchor = input.anchors.contains(&pool.token0_key_id);
        let token1_is_anchor = input.anchors.contains(&pool.token1_key_id);
        if !token0_is_anchor && !token1_is_anchor {
            *desired
                .skip_reason_counts
                .entry("no_core_anchor".to_owned())
                .or_insert(0) += 1;
            continue;
        }
        if token0_is_anchor && pool.token0_key_id != pool.token1_key_id {
            groups
                .entry((pool.token0_key_id.clone(), pool.token1_key_id.clone()))
                .or_default()
                .push(pool.clone());
        }
        if token1_is_anchor && pool.token0_key_id != pool.token1_key_id {
            groups
                .entry((pool.token1_key_id.clone(), pool.token0_key_id.clone()))
                .or_default()
                .push(pool.clone());
        }
    }

    let mut candidate_token_ids = BTreeSet::new();
    for ((anchor_token_key_id, candidate_token_key_id), pools) in groups {
        let Some(anchor) = input.catalog.get(&anchor_token_key_id) else {
            continue;
        };
        let Some(candidate) = input.catalog.get(&candidate_token_key_id) else {
            continue;
        };
        let pair_key_id = PairKeyId::new(
            chain_id as u64,
            &anchor.token_address,
            &candidate.token_address,
        )
        .map_err(|error| CandidateEdgeError::InvalidStoredPool(error.to_string()))?
        .as_str()
        .to_owned();
        let first_direction_key = format!("{anchor_token_key_id}>{candidate_token_key_id}");
        let third_direction_key = format!("{candidate_token_key_id}>{anchor_token_key_id}");

        let active_pools = pools
            .iter()
            .filter(|pool| pool.pool_status == "active")
            .cloned()
            .collect::<Vec<_>>();
        if !active_pools.is_empty() {
            let first_recommendations = recommend_direction(
                &active_pools,
                &anchor_token_key_id,
                &candidate_token_key_id,
                &input.catalog,
            );
            let third_recommendations = recommend_direction(
                &active_pools,
                &candidate_token_key_id,
                &anchor_token_key_id,
                &input.catalog,
            );
            let first_ids = first_recommendations
                .iter()
                .map(|item| item.pool_id.clone())
                .collect::<Vec<_>>();
            let third_ids = third_recommendations
                .iter()
                .map(|item| item.pool_id.clone())
                .collect::<Vec<_>>();
            let same_set = first_ids == third_ids;
            desired.anchor_tokens.push(CandidateAnchorTokenRow {
                anchor_token_key_id: anchor_token_key_id.clone(),
                token_key_id: candidate_token_key_id.clone(),
                token_role: "yc_zc".to_owned(),
                anchor_library_key: anchor.library_key.clone(),
                token_library_key: candidate.library_key.clone(),
                token_symbol: candidate.token_symbol.clone(),
                pool_count: active_pools.len(),
                yc_primary_pool_id: first_ids.first().cloned(),
                zc_primary_pool_id: third_ids.first().cloned(),
                yc_backup_pool_ids: first_ids.iter().skip(1).cloned().collect(),
                zc_backup_pool_ids: third_ids.iter().skip(1).cloned().collect(),
                yc_zc_same_set: same_set,
            });
            candidate_token_ids.insert(candidate_token_key_id.clone());
            desired.pool_pairs.push(CandidatePoolPairRow {
                anchor_token_key_id: anchor_token_key_id.clone(),
                candidate_token_key_id: candidate_token_key_id.clone(),
                pair_key_id: pair_key_id.clone(),
                first_direction_key: first_direction_key.clone(),
                third_direction_key: third_direction_key.clone(),
                pool_count: active_pools.len(),
                first_primary_pool_id: first_ids.first().cloned(),
                third_primary_pool_id: third_ids.first().cloned(),
                first_backup_pool_ids: first_ids.iter().skip(1).cloned().collect(),
                third_backup_pool_ids: third_ids.iter().skip(1).cloned().collect(),
                selection_basis: "direction_static_snapshot_then_fee_pool_id".to_owned(),
            });

            let first_recommended = first_recommendations
                .iter()
                .map(|item| item.pool_id.as_str())
                .collect::<BTreeSet<_>>();
            let third_recommended = third_recommendations
                .iter()
                .map(|item| item.pool_id.as_str())
                .collect::<BTreeSet<_>>();
            for pool in &active_pools {
                desired.pools.push(CandidatePoolRow {
                    anchor_token_key_id: anchor_token_key_id.clone(),
                    candidate_token_key_id: candidate_token_key_id.clone(),
                    pair_key_id: pair_key_id.clone(),
                    pool_id: pool.pool_id.clone(),
                    pool_manager: pool.pool_manager.clone(),
                    pool_key_json: pool.pool_key_json.clone(),
                    token0: pool.token0.clone(),
                    token1: pool.token1.clone(),
                    token0_key_id: pool.token0_key_id.clone(),
                    token1_key_id: pool.token1_key_id.clone(),
                    fee: pool.fee,
                    tick_spacing: pool.tick_spacing,
                    hooks: pool.hooks.clone(),
                    sqrt_price_x96: pool.sqrt_price_x96.clone(),
                    tick: pool.tick,
                    liquidity: pool.liquidity.clone(),
                    liquidity_index: pool.liquidity_index,
                    price: pool.price,
                    depth_status: pool.depth_status.clone(),
                    liquidity_read_at: pool.liquidity_read_at.clone(),
                    liquidity_block_number: pool.liquidity_block_number,
                    evidence_source: pool.evidence_source.clone(),
                    is_first_hop_recommended: first_recommended.contains(pool.pool_id.as_str()),
                    is_third_hop_recommended: third_recommended.contains(pool.pool_id.as_str()),
                });
            }
            for (hop, direction_key, recommendations) in [
                ("first", &first_direction_key, &first_recommendations),
                ("third", &third_direction_key, &third_recommendations),
            ] {
                for (offset, recommendation) in recommendations.iter().enumerate() {
                    desired.recommendations.push(CandidateRecommendationRow {
                        anchor_token_key_id: anchor_token_key_id.clone(),
                        candidate_token_key_id: candidate_token_key_id.clone(),
                        pair_key_id: pair_key_id.clone(),
                        direction_key: direction_key.clone(),
                        hop: hop.to_owned(),
                        rank: offset + 1,
                        pool_id: recommendation.pool_id.clone(),
                        depth_score: recommendation.score,
                        fee: recommendation.fee,
                        selection_basis: "direction_static_snapshot_then_fee_pool_id".to_owned(),
                    });
                }
            }
        }

        let abnormal_pools = pools
            .iter()
            .filter(|pool| pool.pool_status != "active")
            .collect::<Vec<_>>();
        if !abnormal_pools.is_empty() {
            let reasons = abnormal_pools
                .iter()
                .map(|pool| format!("pool_status_{}", pool.pool_status))
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>();
            desired
                .abnormal_anchor_tokens
                .push(AbnormalCandidateTokenRow {
                    anchor_token_key_id: anchor_token_key_id.clone(),
                    token_key_id: candidate_token_key_id.clone(),
                    anchor_library_key: anchor.library_key.clone(),
                    token_library_key: candidate.library_key.clone(),
                    token_symbol: candidate.token_symbol.clone(),
                    pool_count: abnormal_pools.len(),
                    reasons: reasons.clone(),
                });
            desired.abnormal_pool_pairs.push(AbnormalCandidatePairRow {
                anchor_token_key_id: anchor_token_key_id.clone(),
                candidate_token_key_id: candidate_token_key_id.clone(),
                pair_key_id: pair_key_id.clone(),
                pool_count: abnormal_pools.len(),
                reasons,
            });
            for pool in abnormal_pools {
                desired.abnormal_pools.push(AbnormalCandidatePoolRow {
                    anchor_token_key_id: anchor_token_key_id.clone(),
                    candidate_token_key_id: candidate_token_key_id.clone(),
                    pair_key_id: pair_key_id.clone(),
                    pool_id: pool.pool_id.clone(),
                    pool_status: pool.pool_status.clone(),
                    pool_key_json: pool.pool_key_json.clone(),
                    token0_key_id: pool.token0_key_id.clone(),
                    token1_key_id: pool.token1_key_id.clone(),
                    fee: pool.fee,
                    tick_spacing: pool.tick_spacing,
                    hooks: pool.hooks.clone(),
                    reason: format!("pool_status_{}", pool.pool_status),
                });
            }
        }
    }
    desired.anchor_count = input.anchors.len();
    desired.candidate_token_count = candidate_token_ids.len();
    desired.anchor_tokens.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.token_key_id)
            .cmp(&(&right.anchor_token_key_id, &right.token_key_id))
    });
    desired.pool_pairs.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.candidate_token_key_id)
            .cmp(&(&right.anchor_token_key_id, &right.candidate_token_key_id))
    });
    desired.pools.sort_by(|left, right| {
        (&left.pair_key_id, &left.pool_id).cmp(&(&right.pair_key_id, &right.pool_id))
    });
    desired.recommendations.sort_by(|left, right| {
        (&left.pair_key_id, &left.hop, left.rank, &left.pool_id).cmp(&(
            &right.pair_key_id,
            &right.hop,
            right.rank,
            &right.pool_id,
        ))
    });
    desired.abnormal_anchor_tokens.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.token_key_id)
            .cmp(&(&right.anchor_token_key_id, &right.token_key_id))
    });
    desired.abnormal_pool_pairs.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.candidate_token_key_id)
            .cmp(&(&right.anchor_token_key_id, &right.candidate_token_key_id))
    });
    desired.abnormal_pools.sort_by(|left, right| {
        (&left.pair_key_id, &left.pool_id).cmp(&(&right.pair_key_id, &right.pool_id))
    });
    Ok(desired)
}

fn validate_scope_pool(
    pool: &ScopePool,
    catalog: &BTreeMap<String, CatalogToken>,
) -> Result<(), String> {
    normalize_address(&pool.token0).map_err(|_| "invalid_token0".to_owned())?;
    normalize_address(&pool.token1).map_err(|_| "invalid_token1".to_owned())?;
    if pool.token0_key_id == pool.token1_key_id {
        return Err("same_token".to_owned());
    }
    if !catalog.contains_key(&pool.token0_key_id) || !catalog.contains_key(&pool.token1_key_id) {
        return Err("token_not_admitted".to_owned());
    }
    if pool.fee <= 0 || pool.tick_spacing == 0 {
        return Err("invalid_pool_key".to_owned());
    }
    Ok(())
}

fn recommend_direction(
    pools: &[ScopePool],
    token_in_key_id: &str,
    token_out_key_id: &str,
    catalog: &BTreeMap<String, CatalogToken>,
) -> Vec<Recommendation> {
    let mut recommendations = pools
        .iter()
        .map(|pool| Recommendation {
            pool_id: pool.pool_id.clone(),
            score: directional_score(pool, token_in_key_id, token_out_key_id, catalog),
            fee: pool.fee,
        })
        .collect::<Vec<_>>();
    recommendations.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| left.fee.cmp(&right.fee))
            .then_with(|| left.pool_id.cmp(&right.pool_id))
    });
    recommendations.truncate(MAX_RECOMMENDATIONS_PER_HOP);
    recommendations
}

fn directional_score(
    pool: &ScopePool,
    token_in_key_id: &str,
    token_out_key_id: &str,
    catalog: &BTreeMap<String, CatalogToken>,
) -> f64 {
    let fee_factor = 1.0 - (pool.fee.max(0) as f64 / 1_000_000.0);
    let token_in = catalog.get(token_in_key_id);
    let token_out = catalog.get(token_out_key_id);
    let sqrt_price = pool
        .sqrt_price_x96
        .as_deref()
        .and_then(parse_integerish)
        .map(|value| value / Q96)
        .unwrap_or(0.0);
    if sqrt_price > 0.0 {
        let token0_price = sqrt_price * sqrt_price;
        if token0_price.is_finite() && token0_price > 0.0 {
            let mut rate = if token_in_key_id == pool.token0_key_id {
                token0_price
            } else {
                1.0 / token0_price
            };
            if let (Some(token_in), Some(token_out)) = (token_in, token_out) {
                let exponent =
                    i32::from(token_out.token_decimals) - i32::from(token_in.token_decimals);
                rate *= 10_f64.powi(exponent);
            }
            if rate.is_finite() && rate > 0.0 {
                return rate.max(0.0) * fee_factor;
            }
        }
    }
    if let Some(price) = pool.price.filter(|value| value.is_finite() && *value > 0.0) {
        let rate = if token_in_key_id == pool.token1_key_id {
            1.0 / price
        } else {
            price
        };
        return rate.max(0.0) * fee_factor;
    }
    pool.liquidity_index
        .filter(|value| value.is_finite() && *value > 0.0)
        .unwrap_or(fee_factor)
}

fn parse_integerish(value: &str) -> Option<f64> {
    let value = value.trim();
    if let Some(hex) = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
    {
        if hex.is_empty() {
            return None;
        }
        let mut parsed = 0.0;
        for digit in hex.bytes() {
            let value = (digit as char).to_digit(16)? as f64;
            parsed = parsed * 16.0 + value;
        }
        Some(parsed)
    } else {
        value.parse::<f64>().ok()
    }
}

fn candidate_checksum(
    input: &CandidateInput,
    desired: &DesiredCandidateData,
) -> Result<String, CandidateEdgeError> {
    sha256_json(&CandidateChecksumPayload {
        pool_scope_checksum: &input.pool_scope_checksum,
        library_versions: &input.library_versions,
        library_checksums: &input.library_checksums,
        anchor_tokens: &desired.anchor_tokens,
        pool_pairs: &desired.pool_pairs,
        pools: &desired.pools,
        recommendations: &desired.recommendations,
        abnormal_anchor_tokens: &desired.abnormal_anchor_tokens,
        abnormal_pool_pairs: &desired.abnormal_pool_pairs,
        abnormal_pools: &desired.abnormal_pools,
    })
}

fn sha256_json<T: Serialize>(value: &T) -> Result<String, CandidateEdgeError> {
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(value)?))
    ))
}

fn replace_candidate_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    result: &CandidateEdgeMaterializationResult,
    candidate_version: i64,
) -> Result<(), CandidateEdgeError> {
    for table in [
        "manager_dex_candidate_pool_recommendations",
        "manager_dex_candidate_pools",
        "manager_dex_candidate_pool_pairs",
        "manager_dex_candidate_anchor_tokens",
        "manager_dex_candidate_abnormal_pools",
        "manager_dex_candidate_abnormal_pool_pairs",
        "manager_dex_candidate_abnormal_anchor_tokens",
    ] {
        connection.execute(
            &format!(
                "DELETE FROM {table}
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                   AND scope_key = ?4"
            ),
            params![network, chain_id, PROTOCOL, SCOPE_KEY],
        )?;
    }
    let now = chrono::Utc::now().to_rfc3339();
    insert_anchor_tokens(connection, network, chain_id, desired, &now)?;
    insert_pool_pairs(connection, network, chain_id, desired, &now)?;
    insert_candidate_pools(connection, network, chain_id, desired, &now)?;
    insert_recommendations(connection, network, chain_id, desired, &now)?;
    insert_abnormal_anchor_tokens(connection, network, chain_id, desired, &now)?;
    insert_abnormal_pool_pairs(connection, network, chain_id, desired, &now)?;
    insert_abnormal_pools(connection, network, chain_id, desired, &now)?;
    write_candidate_state(
        connection,
        network,
        chain_id,
        result,
        candidate_version,
        &now,
    )?;
    Ok(())
}

fn insert_anchor_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    let mut statement = connection.prepare(
        "INSERT INTO manager_dex_candidate_anchor_tokens (
             network, chain_id, protocol, scope_key, anchor_token_key_id,
             token_key_id, token_role, anchor_library_key, token_library_key,
             token_symbol, pool_count, yc_primary_pool_id, zc_primary_pool_id,
             yc_backup_pool_ids_json, zc_backup_pool_ids_json, yc_zc_same_set,
             created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?17, ?17)",
    )?;
    for row in &desired.anchor_tokens {
        statement.execute(params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            row.anchor_token_key_id,
            row.token_key_id,
            row.token_role,
            row.anchor_library_key,
            row.token_library_key,
            row.token_symbol,
            row.pool_count,
            row.yc_primary_pool_id,
            row.zc_primary_pool_id,
            serde_json::to_string(&row.yc_backup_pool_ids)?,
            serde_json::to_string(&row.zc_backup_pool_ids)?,
            i64::from(row.yc_zc_same_set),
            now
        ])?;
    }
    Ok(())
}

fn insert_pool_pairs(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    let mut statement = connection.prepare(
        "INSERT INTO manager_dex_candidate_pool_pairs (
             network, chain_id, protocol, scope_key, anchor_token_key_id,
             candidate_token_key_id, pair_key_id, first_direction_key,
             third_direction_key, pool_count, first_primary_pool_id,
             third_primary_pool_id, first_backup_pool_ids_json,
             third_backup_pool_ids_json, selection_basis, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?16)",
    )?;
    for row in &desired.pool_pairs {
        statement.execute(params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            row.anchor_token_key_id,
            row.candidate_token_key_id,
            row.pair_key_id,
            row.first_direction_key,
            row.third_direction_key,
            row.pool_count,
            row.first_primary_pool_id,
            row.third_primary_pool_id,
            serde_json::to_string(&row.first_backup_pool_ids)?,
            serde_json::to_string(&row.third_backup_pool_ids)?,
            row.selection_basis,
            now
        ])?;
    }
    Ok(())
}

fn insert_candidate_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    let mut statement = connection.prepare(
        "INSERT INTO manager_dex_candidate_pools (
             network, chain_id, protocol, scope_key, anchor_token_key_id,
             candidate_token_key_id, pair_key_id, pool_id, pool_manager,
             pool_key_json, token0, token1, token0_key_id, token1_key_id,
             fee, tick_spacing, hooks, sqrt_price_x96, tick, liquidity,
             liquidity_index, price, depth_status, liquidity_read_at,
             liquidity_block_number, evidence_source, is_first_hop_recommended,
             is_third_hop_recommended, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23,
                   ?24, ?25, ?26, ?27, ?28, ?29, ?29)",
    )?;
    for row in &desired.pools {
        statement.execute(params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            row.anchor_token_key_id,
            row.candidate_token_key_id,
            row.pair_key_id,
            row.pool_id,
            row.pool_manager,
            row.pool_key_json,
            row.token0,
            row.token1,
            row.token0_key_id,
            row.token1_key_id,
            row.fee,
            row.tick_spacing,
            row.hooks,
            row.sqrt_price_x96,
            row.tick,
            row.liquidity,
            row.liquidity_index,
            row.price,
            row.depth_status,
            row.liquidity_read_at,
            row.liquidity_block_number,
            row.evidence_source,
            i64::from(row.is_first_hop_recommended),
            i64::from(row.is_third_hop_recommended),
            now
        ])?;
    }
    Ok(())
}

fn insert_recommendations(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    let mut statement = connection.prepare(
        "INSERT INTO manager_dex_candidate_pool_recommendations (
             network, chain_id, protocol, scope_key, anchor_token_key_id,
             candidate_token_key_id, pair_key_id, direction_key, hop, rank,
             pool_id, depth_score, fee, selection_basis, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?15)",
    )?;
    for row in &desired.recommendations {
        statement.execute(params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            row.anchor_token_key_id,
            row.candidate_token_key_id,
            row.pair_key_id,
            row.direction_key,
            row.hop,
            row.rank,
            row.pool_id,
            row.depth_score,
            row.fee,
            row.selection_basis,
            now
        ])?;
    }
    Ok(())
}

fn insert_abnormal_anchor_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    let mut statement = connection.prepare(
        "INSERT INTO manager_dex_candidate_abnormal_anchor_tokens (
             network, chain_id, protocol, scope_key, anchor_token_key_id,
             token_key_id, anchor_library_key, token_library_key, token_symbol,
             pool_count, reasons_json, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?12)",
    )?;
    for row in &desired.abnormal_anchor_tokens {
        statement.execute(params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            row.anchor_token_key_id,
            row.token_key_id,
            row.anchor_library_key,
            row.token_library_key,
            row.token_symbol,
            row.pool_count,
            serde_json::to_string(&row.reasons)?,
            now
        ])?;
    }
    Ok(())
}

fn insert_abnormal_pool_pairs(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    let mut statement = connection.prepare(
        "INSERT INTO manager_dex_candidate_abnormal_pool_pairs (
             network, chain_id, protocol, scope_key, anchor_token_key_id,
             candidate_token_key_id, pair_key_id, pool_count, reasons_json,
             created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10)",
    )?;
    for row in &desired.abnormal_pool_pairs {
        statement.execute(params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            row.anchor_token_key_id,
            row.candidate_token_key_id,
            row.pair_key_id,
            row.pool_count,
            serde_json::to_string(&row.reasons)?,
            now
        ])?;
    }
    Ok(())
}

fn insert_abnormal_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredCandidateData,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    let mut statement = connection.prepare(
        "INSERT INTO manager_dex_candidate_abnormal_pools (
             network, chain_id, protocol, scope_key, anchor_token_key_id,
             candidate_token_key_id, pair_key_id, pool_id, pool_status,
             pool_key_json, token0_key_id, token1_key_id, fee, tick_spacing,
             hooks, reason, created_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?17, ?17)",
    )?;
    for row in &desired.abnormal_pools {
        statement.execute(params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            row.anchor_token_key_id,
            row.candidate_token_key_id,
            row.pair_key_id,
            row.pool_id,
            row.pool_status,
            row.pool_key_json,
            row.token0_key_id,
            row.token1_key_id,
            row.fee,
            row.tick_spacing,
            row.hooks,
            row.reason,
            now
        ])?;
    }
    Ok(())
}

fn write_candidate_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    result: &CandidateEdgeMaterializationResult,
    candidate_version: i64,
    now: &str,
) -> Result<(), CandidateEdgeError> {
    connection.execute(
        "INSERT INTO manager_dex_candidate_library_state (
             network, chain_id, protocol, scope_key, candidate_version,
             materialized_checksum, pool_scope_checksum,
             pool_library_versions_json, pool_library_checksums_json,
             library_versions_json, library_checksums_json, anchor_token_count,
             candidate_token_count, eligible_pool_count, recommendation_count,
             abnormal_pool_count, generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                   ?13, ?14, ?15, ?16, ?17, ?17)
         ON CONFLICT(network, chain_id, protocol, scope_key) DO UPDATE SET
             candidate_version = excluded.candidate_version,
             materialized_checksum = excluded.materialized_checksum,
             pool_scope_checksum = excluded.pool_scope_checksum,
             pool_library_versions_json = excluded.pool_library_versions_json,
             pool_library_checksums_json = excluded.pool_library_checksums_json,
             library_versions_json = excluded.library_versions_json,
             library_checksums_json = excluded.library_checksums_json,
             anchor_token_count = excluded.anchor_token_count,
             candidate_token_count = excluded.candidate_token_count,
             eligible_pool_count = excluded.eligible_pool_count,
             recommendation_count = excluded.recommendation_count,
             abnormal_pool_count = excluded.abnormal_pool_count,
             generated_at = excluded.generated_at,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            PROTOCOL,
            SCOPE_KEY,
            candidate_version,
            result.materialized_checksum,
            result.pool_scope_checksum,
            serde_json::to_string(&result.library_versions)?,
            serde_json::to_string(&result.library_checksums)?,
            serde_json::to_string(&result.library_versions)?,
            serde_json::to_string(&result.library_checksums)?,
            result.anchor_token_count,
            result.candidate_token_count,
            result.eligible_pool_count,
            result.recommendation_count,
            result.abnormal_pool_count,
            now
        ],
    )?;
    Ok(())
}

pub fn read_candidate_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<CandidateEdgeStatus, CandidateEdgeError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_candidate_status_on_connection(&connection, network, chain_id)
}

fn read_candidate_status_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<CandidateEdgeStatus, CandidateEdgeError> {
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
            params![network, chain_id, PROTOCOL, SCOPE_KEY],
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
    match stored {
        Some((
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
        )) => Ok(CandidateEdgeStatus {
            enabled: true,
            network: network.to_owned(),
            chain_id,
            protocol: PROTOCOL.to_owned(),
            scope_key: SCOPE_KEY.to_owned(),
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
        }),
        None => Ok(CandidateEdgeStatus {
            enabled: false,
            network: network.to_owned(),
            chain_id,
            protocol: PROTOCOL.to_owned(),
            scope_key: SCOPE_KEY.to_owned(),
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
        }),
    }
}

pub fn read_candidate_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<CandidateEdgeSnapshot, CandidateEdgeError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_candidate_snapshot_on_connection(
        &connection,
        network,
        chain_id,
        anchor_token_key_id,
        Some(limit.clamp(1, MAX_SNAPSHOT_LIMIT) as i64),
    )
}

fn read_candidate_snapshot_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: Option<i64>,
) -> Result<CandidateEdgeSnapshot, CandidateEdgeError> {
    let status = read_candidate_status_on_connection(connection, network, chain_id)?;
    let anchor_token_key_id = anchor_token_key_id
        .map(str::trim)
        .filter(|value| !value.is_empty());

    let mut anchor_statement = connection.prepare(
        "SELECT anchor_token_key_id, token_key_id, token_role,
                anchor_library_key, token_library_key, token_symbol, pool_count,
                yc_primary_pool_id, zc_primary_pool_id,
                yc_backup_pool_ids_json, zc_backup_pool_ids_json, yc_zc_same_set
         FROM manager_dex_candidate_anchor_tokens
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, token_key_id
         LIMIT ?6",
    )?;
    let anchor_tokens = anchor_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            |row| {
                Ok(CandidateAnchorTokenRow {
                    anchor_token_key_id: row.get(0)?,
                    token_key_id: row.get(1)?,
                    token_role: row.get(2)?,
                    anchor_library_key: row.get(3)?,
                    token_library_key: row.get(4)?,
                    token_symbol: row.get(5)?,
                    pool_count: usize_from_i64(row.get(6)?),
                    yc_primary_pool_id: row.get(7)?,
                    zc_primary_pool_id: row.get(8)?,
                    yc_backup_pool_ids: serde_json::from_str(&row.get::<_, String>(9)?)
                        .unwrap_or_default(),
                    zc_backup_pool_ids: serde_json::from_str(&row.get::<_, String>(10)?)
                        .unwrap_or_default(),
                    yc_zc_same_set: row.get::<_, i64>(11)? != 0,
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;

    let mut pair_statement = connection.prepare(
        "SELECT anchor_token_key_id, candidate_token_key_id, pair_key_id,
                first_direction_key, third_direction_key, pool_count,
                first_primary_pool_id, third_primary_pool_id,
                first_backup_pool_ids_json, third_backup_pool_ids_json,
                selection_basis
         FROM manager_dex_candidate_pool_pairs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, candidate_token_key_id
         LIMIT ?6",
    )?;
    let pool_pairs = pair_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            |row| {
                Ok(CandidatePoolPairRow {
                    anchor_token_key_id: row.get(0)?,
                    candidate_token_key_id: row.get(1)?,
                    pair_key_id: row.get(2)?,
                    first_direction_key: row.get(3)?,
                    third_direction_key: row.get(4)?,
                    pool_count: usize_from_i64(row.get(5)?),
                    first_primary_pool_id: row.get(6)?,
                    third_primary_pool_id: row.get(7)?,
                    first_backup_pool_ids: serde_json::from_str(&row.get::<_, String>(8)?)
                        .unwrap_or_default(),
                    third_backup_pool_ids: serde_json::from_str(&row.get::<_, String>(9)?)
                        .unwrap_or_default(),
                    selection_basis: row.get(10)?,
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;

    let mut token_statement = connection.prepare(
        "SELECT DISTINCT catalog.token_key_id, catalog.v4_currency_key,
                catalog.token_symbol, catalog.token_name, catalog.token_decimals,
                catalog.library_key, catalog.admission_status
         FROM token_library_catalog catalog
         WHERE catalog.network = ?1 AND catalog.chain_id = ?2
           AND (
             EXISTS (
               SELECT 1 FROM manager_dex_candidate_anchor_tokens anchors
               WHERE anchors.network = catalog.network
                 AND anchors.chain_id = catalog.chain_id
                 AND anchors.protocol = ?3
                 AND anchors.scope_key = ?4
                 AND (?5 IS NULL OR anchors.anchor_token_key_id = ?5)
                 AND (anchors.anchor_token_key_id = catalog.token_key_id
                      OR anchors.token_key_id = catalog.token_key_id)
             )
             OR EXISTS (
               SELECT 1 FROM manager_dex_candidate_pool_pairs pairs
               WHERE pairs.network = catalog.network
                 AND pairs.chain_id = catalog.chain_id
                 AND pairs.protocol = ?3
                 AND pairs.scope_key = ?4
                 AND (?5 IS NULL OR pairs.anchor_token_key_id = ?5)
                 AND (pairs.anchor_token_key_id = catalog.token_key_id
                      OR pairs.candidate_token_key_id = catalog.token_key_id)
             )
             OR EXISTS (
               SELECT 1 FROM manager_dex_candidate_abnormal_pool_pairs abnormal_pairs
               WHERE abnormal_pairs.network = catalog.network
                 AND abnormal_pairs.chain_id = catalog.chain_id
                 AND abnormal_pairs.protocol = ?3
                 AND abnormal_pairs.scope_key = ?4
                 AND (?5 IS NULL OR abnormal_pairs.anchor_token_key_id = ?5)
                 AND (abnormal_pairs.anchor_token_key_id = catalog.token_key_id
                      OR abnormal_pairs.candidate_token_key_id = catalog.token_key_id)
             )
             OR EXISTS (
               SELECT 1 FROM manager_dex_candidate_abnormal_anchor_tokens abnormal_anchors
               WHERE abnormal_anchors.network = catalog.network
                 AND abnormal_anchors.chain_id = catalog.chain_id
                 AND abnormal_anchors.protocol = ?3
                 AND abnormal_anchors.scope_key = ?4
                 AND (?5 IS NULL OR abnormal_anchors.anchor_token_key_id = ?5)
                 AND (abnormal_anchors.anchor_token_key_id = catalog.token_key_id
                      OR abnormal_anchors.token_key_id = catalog.token_key_id)
             )
           )
         ORDER BY CASE catalog.library_key
                      WHEN 'core' THEN 0
                      WHEN 'stocks' THEN 1
                      WHEN 'test' THEN 2
                      ELSE 3
                  END,
                  catalog.token_key_id
         LIMIT ?6",
    )?;
    let tokens = token_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            |row| {
                Ok(CandidateTokenRow {
                    token_key_id: row.get(0)?,
                    token_address: row.get(1)?,
                    token_symbol: row.get(2)?,
                    token_name: row.get(3)?,
                    token_decimals: row.get(4)?,
                    token_library_key: row.get(5)?,
                    admission_status: row.get(6)?,
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;

    let mut pool_statement = connection.prepare(
        "SELECT anchor_token_key_id, candidate_token_key_id, pair_key_id,
                pool_id, pool_manager, pool_key_json, token0, token1,
                token0_key_id, token1_key_id, fee, tick_spacing, hooks,
                sqrt_price_x96, tick, liquidity, liquidity_index, price,
                depth_status, liquidity_read_at, liquidity_block_number,
                evidence_source, is_first_hop_recommended,
                is_third_hop_recommended
         FROM manager_dex_candidate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, candidate_token_key_id, pool_id
         LIMIT ?6",
    )?;
    let pools = pool_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            map_candidate_pool,
        )?
        .collect::<Result<Vec<_>, _>>()?;

    let mut recommendation_statement = connection.prepare(
        "SELECT anchor_token_key_id, candidate_token_key_id, pair_key_id,
                direction_key, hop, rank, pool_id, depth_score, fee,
                selection_basis
         FROM manager_dex_candidate_pool_recommendations
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank
         LIMIT ?6",
    )?;
    let recommendations = recommendation_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            |row| {
                Ok(CandidateRecommendationRow {
                    anchor_token_key_id: row.get(0)?,
                    candidate_token_key_id: row.get(1)?,
                    pair_key_id: row.get(2)?,
                    direction_key: row.get(3)?,
                    hop: row.get(4)?,
                    rank: usize_from_i64(row.get(5)?),
                    pool_id: row.get(6)?,
                    depth_score: row.get(7)?,
                    fee: row.get(8)?,
                    selection_basis: row.get(9)?,
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;

    let mut abnormal_anchor_statement = connection.prepare(
        "SELECT anchor_token_key_id, token_key_id, anchor_library_key,
                token_library_key, token_symbol, pool_count, reasons_json
         FROM manager_dex_candidate_abnormal_anchor_tokens
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, token_key_id
         LIMIT ?6",
    )?;
    let abnormal_anchor_tokens = abnormal_anchor_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            |row| {
                Ok(AbnormalCandidateTokenRow {
                    anchor_token_key_id: row.get(0)?,
                    token_key_id: row.get(1)?,
                    anchor_library_key: row.get(2)?,
                    token_library_key: row.get(3)?,
                    token_symbol: row.get(4)?,
                    pool_count: usize_from_i64(row.get(5)?),
                    reasons: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default(),
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;

    let mut abnormal_pair_statement = connection.prepare(
        "SELECT anchor_token_key_id, candidate_token_key_id, pair_key_id,
                pool_count, reasons_json
         FROM manager_dex_candidate_abnormal_pool_pairs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, candidate_token_key_id
         LIMIT ?6",
    )?;
    let abnormal_pool_pairs = abnormal_pair_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            |row| {
                Ok(AbnormalCandidatePairRow {
                    anchor_token_key_id: row.get(0)?,
                    candidate_token_key_id: row.get(1)?,
                    pair_key_id: row.get(2)?,
                    pool_count: usize_from_i64(row.get(3)?),
                    reasons: serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default(),
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;

    let mut abnormal_pool_statement = connection.prepare(
        "SELECT anchor_token_key_id, candidate_token_key_id, pair_key_id,
                pool_id, pool_status, pool_key_json, token0_key_id,
                token1_key_id, fee, tick_spacing, hooks, reason
         FROM manager_dex_candidate_abnormal_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, candidate_token_key_id, pool_id
         LIMIT ?6",
    )?;
    let abnormal_pools = abnormal_pool_statement
        .query_map(
            params![
                network,
                chain_id,
                PROTOCOL,
                SCOPE_KEY,
                anchor_token_key_id,
                limit
            ],
            |row| {
                Ok(AbnormalCandidatePoolRow {
                    anchor_token_key_id: row.get(0)?,
                    candidate_token_key_id: row.get(1)?,
                    pair_key_id: row.get(2)?,
                    pool_id: row.get(3)?,
                    pool_status: row.get(4)?,
                    pool_key_json: row.get(5)?,
                    token0_key_id: row.get(6)?,
                    token1_key_id: row.get(7)?,
                    fee: row.get(8)?,
                    tick_spacing: row.get(9)?,
                    hooks: row.get(10)?,
                    reason: row.get(11)?,
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;

    Ok(CandidateEdgeSnapshot {
        status,
        tokens,
        anchor_tokens,
        pool_pairs,
        pools,
        recommendations,
        abnormal_anchor_tokens,
        abnormal_pool_pairs,
        abnormal_pools,
    })
}

pub fn compare_candidate_edges(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    legacy_anchor_scope: &str,
    sample_limit: usize,
) -> Result<CandidateEdgeComparison, CandidateEdgeError> {
    validate_operation(network, chain_id)?;
    let legacy_anchor_scope = legacy_anchor_scope.trim();
    if legacy_anchor_scope.is_empty() {
        return Err(CandidateEdgeError::InvalidRequest(
            "legacyAnchorScope must not be empty".to_owned(),
        ));
    }

    let connection = open_connection(database_path.as_ref(), false)?;
    let manager = read_candidate_snapshot_on_connection(
        &connection,
        network,
        chain_id,
        None,
        Some(i64::MAX),
    )?;
    let input = read_candidate_input(&connection, network, chain_id)?;
    let mut core_symbols = BTreeMap::<String, BTreeSet<String>>::new();
    for token in input
        .catalog
        .values()
        .filter(|token| token.library_key == "core")
    {
        core_symbols
            .entry(token.token_symbol.clone())
            .or_default()
            .insert(token.token_address.clone());
    }
    let unresolved_core_symbols = core_symbols
        .iter()
        .filter(|(_, addresses)| addresses.len() != 1)
        .map(|(symbol, _)| symbol.clone())
        .collect::<BTreeSet<_>>();
    let mut manager_anchors = BTreeMap::new();
    for row in &manager.anchor_tokens {
        let anchor_symbol = input
            .catalog
            .get(&row.anchor_token_key_id)
            .map(|token| token.token_symbol.as_str())
            .ok_or_else(|| {
                CandidateEdgeError::InvalidRequest(format!(
                    "candidate anchor token {} is absent from the admitted catalog",
                    row.anchor_token_key_id
                ))
            })?;
        let candidate_address = token_key_address(&row.token_key_id)?;
        let key = format!("{}|{}|{}", anchor_symbol, candidate_address, row.token_role);
        manager_anchors.insert(
            key,
            json_payload(serde_json::json!({
                "poolCount": row.pool_count,
                "ycPrimaryPoolId": row.yc_primary_pool_id,
                "zcPrimaryPoolId": row.zc_primary_pool_id,
                "ycBackupPoolIds": row.yc_backup_pool_ids,
                "zcBackupPoolIds": row.zc_backup_pool_ids,
                "ycZcSameSet": row.yc_zc_same_set,
            })),
        );
    }

    let mut manager_pairs = BTreeMap::new();
    for row in &manager.pool_pairs {
        let anchor_address = token_key_address(&row.anchor_token_key_id)?;
        let candidate_address = token_key_address(&row.candidate_token_key_id)?;
        manager_pairs.insert(
            format!("{anchor_address}:{candidate_address}"),
            json_payload(serde_json::json!({
                "poolCount": row.pool_count,
                "primaryPoolId": row.first_primary_pool_id,
                "backupPoolIds": row.first_backup_pool_ids,
            })),
        );
        manager_pairs.insert(
            format!("{candidate_address}:{anchor_address}"),
            json_payload(serde_json::json!({
                "poolCount": row.pool_count,
                "primaryPoolId": row.third_primary_pool_id,
                "backupPoolIds": row.third_backup_pool_ids,
            })),
        );
    }

    let mut manager_pools = BTreeMap::new();
    for row in &manager.pools {
        let anchor_address = token_key_address(&row.anchor_token_key_id)?;
        let candidate_address = token_key_address(&row.candidate_token_key_id)?;
        manager_pools.insert(
            format!("{anchor_address}|{candidate_address}|{}", row.pool_id),
            json_payload(serde_json::json!({
                "poolManager": row.pool_manager,
                "poolKey": canonical_json_text(&row.pool_key_json),
                "token0": row.token0,
                "token1": row.token1,
                "fee": row.fee,
                "tickSpacing": row.tick_spacing,
                "hooks": row.hooks,
                "dynamicFee": false,
                "specialFee": false,
                "firstHopRecommended": row.is_first_hop_recommended,
                "thirdHopRecommended": row.is_third_hop_recommended,
                "usableForObservation": true,
            })),
        );
    }

    let mut manager_recommendations = BTreeMap::new();
    for row in &manager.recommendations {
        let anchor_address = token_key_address(&row.anchor_token_key_id)?;
        let candidate_address = token_key_address(&row.candidate_token_key_id)?;
        manager_recommendations.insert(
            format!(
                "{anchor_address}|{candidate_address}|{}|{}|{}",
                row.hop, row.rank, row.pool_id
            ),
            json_payload(serde_json::json!({"fee": row.fee})),
        );
    }

    let mut legacy_anchors = BTreeMap::new();
    let mut anchor_statement = connection.prepare(
        "SELECT anchor_symbol, token_address, token_role, pool_count,
                NULLIF(yc_optimal_pool_id, ''), NULLIF(zc_optimal_pool_id, ''),
                yc_backup_pool_ids_json, zc_backup_pool_ids_json, yc_zc_same_set
         FROM dex_candidate_anchor_tokens
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND anchor_scope = ?4
         ORDER BY anchor_symbol, token_address, token_role",
    )?;
    let mut legacy_anchor_rows = anchor_statement
        .query_map(
            params![network, chain_id, PROTOCOL, legacy_anchor_scope],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, Option<String>>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, i64>(8)? != 0,
                ))
            },
        )
        .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
        .map_err(|source| CandidateEdgeError::ComparisonRead {
            stage: "anchorTokens".to_owned(),
            source,
        })?;
    legacy_anchor_rows.sort();
    for (
        anchor_symbol,
        token_address,
        token_role,
        pool_count,
        yc_primary,
        zc_primary,
        yc_backups,
        zc_backups,
        same_set,
    ) in legacy_anchor_rows
    {
        if !core_symbols.contains_key(&anchor_symbol) {
            continue;
        }
        let token_address = comparison_address(&token_address)?;
        legacy_anchors.insert(
            format!("{anchor_symbol}|{token_address}|{token_role}"),
            json_payload(serde_json::json!({
                "poolCount": pool_count,
                "ycPrimaryPoolId": yc_primary,
                "zcPrimaryPoolId": zc_primary,
                "ycBackupPoolIds": canonical_json_value(&yc_backups),
                "zcBackupPoolIds": canonical_json_value(&zc_backups),
                "ycZcSameSet": same_set,
            })),
        );
    }

    let mut legacy_pairs = BTreeMap::new();
    let mut pair_statement = connection.prepare(
        "SELECT anchor_symbol, anchor_address, counter_address, direction_key,
                COALESCE(NULLIF(yc_optimal_pool_id, ''),
                         NULLIF(zc_optimal_pool_id, '')),
                CASE WHEN yc_optimal_pool_id IS NOT NULL
                          AND yc_optimal_pool_id <> ''
                     THEN yc_backup_pool_ids_json
                     ELSE zc_backup_pool_ids_json END,
                pool_count
         FROM dex_candidate_pool_pairs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND anchor_scope = ?4
         ORDER BY anchor_symbol, direction_key",
    )?;
    let pair_rows = pair_statement
        .query_map(
            params![network, chain_id, PROTOCOL, legacy_anchor_scope],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, i64>(6)?,
                ))
            },
        )
        .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
        .map_err(|source| CandidateEdgeError::ComparisonRead {
            stage: "poolPairs".to_owned(),
            source,
        })?;
    for (anchor_symbol, anchor_address, counter_address, direction_key, primary, backups, count) in
        pair_rows
    {
        if !core_symbols.contains_key(&anchor_symbol) {
            continue;
        }
        let anchor_address = comparison_address(&anchor_address)?;
        let counter_address = comparison_address(&counter_address)?;
        let Some(core_address) = unique_core_address(&core_symbols, &anchor_symbol) else {
            continue;
        };
        let Some((direction_from, direction_to)) = direction_key.split_once(':') else {
            continue;
        };
        if ![direction_from, direction_to].contains(&core_address.as_str()) {
            continue;
        }
        if ![direction_from, direction_to].contains(&anchor_address.as_str())
            || ![direction_from, direction_to].contains(&counter_address.as_str())
        {
            continue;
        }
        legacy_pairs.insert(
            direction_key,
            json_payload(serde_json::json!({
                "poolCount": count,
                "primaryPoolId": primary,
                "backupPoolIds": canonical_json_value(&backups),
            })),
        );
    }

    let mut legacy_pools = BTreeMap::new();
    let mut pool_statement = connection.prepare(
        "SELECT anchor_symbol, pool_id, pool_manager, pool_key_json,
                token0, token1, fee, tick_spacing, hooks, dynamic_fee,
                special_fee, is_first_hop_recommended,
                is_third_hop_recommended, usable_for_observation
         FROM dex_candidate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND anchor_scope = ?4
         ORDER BY anchor_symbol, pool_id, address_pair",
    )?;
    let pool_rows = pool_statement
        .query_map(
            params![network, chain_id, PROTOCOL, legacy_anchor_scope],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, i64>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, i64>(9)? != 0,
                    row.get::<_, i64>(10)? != 0,
                    row.get::<_, i64>(11)? != 0,
                    row.get::<_, i64>(12)? != 0,
                    row.get::<_, Option<i64>>(13)?.unwrap_or(0) != 0,
                ))
            },
        )
        .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
        .map_err(|source| CandidateEdgeError::ComparisonRead {
            stage: "pools".to_owned(),
            source,
        })?;
    for (
        anchor_symbol,
        pool_id,
        pool_manager,
        pool_key_json,
        token0,
        token1,
        fee,
        tick_spacing,
        hooks,
        dynamic_fee,
        special_fee,
        first_hop,
        third_hop,
        usable,
    ) in pool_rows
    {
        let Some(anchor_address) = unique_core_address(&core_symbols, &anchor_symbol) else {
            continue;
        };
        let token0 = comparison_address(&token0)?;
        let token1 = comparison_address(&token1)?;
        let candidate_address = if token0 == anchor_address {
            token1.clone()
        } else if token1 == anchor_address {
            token0.clone()
        } else {
            legacy_pools.insert(
                format!("{anchor_address}|?|{pool_id}"),
                json_payload(serde_json::json!({
                    "anchorAddressMissingFromPool": true,
                    "token0": token0,
                    "token1": token1,
                })),
            );
            continue;
        };
        legacy_pools.insert(
            format!("{anchor_address}|{candidate_address}|{pool_id}"),
            json_payload(serde_json::json!({
                "poolManager": pool_manager.unwrap_or_default(),
                "poolKey": pool_key_json.map(|value| canonical_json_text(&value)),
                "token0": token0,
                "token1": token1,
                "fee": fee,
                "tickSpacing": tick_spacing,
                "hooks": hooks,
                "dynamicFee": dynamic_fee,
                "specialFee": special_fee,
                "firstHopRecommended": first_hop,
                "thirdHopRecommended": third_hop,
                "usableForObservation": usable,
            })),
        );
    }

    let mut legacy_recommendations = BTreeMap::new();
    let mut recommendation_statement = connection.prepare(
        "SELECT anchor_symbol, counter_address, hop, rank, pool_id, fee
         FROM dex_candidate_pool_recommendations
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND anchor_scope = ?4
         ORDER BY anchor_symbol, counter_address, hop, rank, pool_id",
    )?;
    let recommendation_rows = recommendation_statement
        .query_map(
            params![network, chain_id, PROTOCOL, legacy_anchor_scope],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, Option<i64>>(5)?,
                ))
            },
        )
        .and_then(|rows| rows.collect::<Result<Vec<_>, _>>())
        .map_err(|source| CandidateEdgeError::ComparisonRead {
            stage: "recommendations".to_owned(),
            source,
        })?;
    for (anchor_symbol, counter_address, hop, rank, pool_id, fee) in recommendation_rows {
        let Some(anchor_address) = unique_core_address(&core_symbols, &anchor_symbol) else {
            continue;
        };
        let counter_address = comparison_address(&counter_address)?;
        legacy_recommendations.insert(
            format!("{anchor_address}|{counter_address}|{hop}|{rank}|{pool_id}"),
            json_payload(serde_json::json!({"fee": fee})),
        );
    }

    let sample_limit = sample_limit.clamp(1, 500);
    let mut differences = Vec::new();
    let mut cause_counts = BTreeMap::new();
    let anchor_tokens = summarize_comparison(
        "anchorTokens",
        &manager_anchors,
        &legacy_anchors,
        sample_limit,
        &mut differences,
        &mut cause_counts,
    );
    let pool_pairs = summarize_comparison(
        "poolPairs",
        &manager_pairs,
        &legacy_pairs,
        sample_limit,
        &mut differences,
        &mut cause_counts,
    );
    let pools = summarize_comparison(
        "pools",
        &manager_pools,
        &legacy_pools,
        sample_limit,
        &mut differences,
        &mut cause_counts,
    );
    let recommendations = summarize_comparison(
        "recommendations",
        &manager_recommendations,
        &legacy_recommendations,
        sample_limit,
        &mut differences,
        &mut cause_counts,
    );
    let total_differences = anchor_tokens.manager_only_count
        + anchor_tokens.legacy_only_count
        + anchor_tokens.field_mismatch_count
        + pool_pairs.manager_only_count
        + pool_pairs.legacy_only_count
        + pool_pairs.field_mismatch_count
        + pools.manager_only_count
        + pools.legacy_only_count
        + pools.field_mismatch_count
        + recommendations.manager_only_count
        + recommendations.legacy_only_count
        + recommendations.field_mismatch_count;
    differences.sort_by(|left, right| {
        left.category
            .cmp(&right.category)
            .then_with(|| left.key.cmp(&right.key))
            .then_with(|| left.kind.cmp(&right.kind))
    });
    let unresolved_legacy_anchors = unresolved_core_symbols.into_iter().collect::<Vec<_>>();
    if !unresolved_legacy_anchors.is_empty() {
        cause_counts.insert(
            "unresolvedLegacyAnchor".to_owned(),
            unresolved_legacy_anchors.len(),
        );
    }
    let manager_checksum = manager.status.materialized_checksum.clone();
    let ready_for_cutover = manager.status.enabled
        && manager_checksum.is_some()
        && total_differences == 0
        && unresolved_legacy_anchors.is_empty()
        && anchor_tokens.legacy_row_count > 0
        && pools.legacy_row_count > 0;

    Ok(CandidateEdgeComparison {
        enabled: manager.status.enabled,
        network: network.to_owned(),
        chain_id,
        protocol: PROTOCOL.to_owned(),
        scope_key: SCOPE_KEY.to_owned(),
        legacy_anchor_scope: legacy_anchor_scope.to_owned(),
        manager_checksum,
        ready_for_cutover,
        anchor_tokens,
        pool_pairs,
        pools,
        recommendations,
        unresolved_legacy_anchors,
        cause_counts,
        differences_truncated: total_differences > differences.len(),
        differences,
    })
}

fn summarize_comparison(
    category: &str,
    manager: &BTreeMap<String, String>,
    legacy: &BTreeMap<String, String>,
    sample_limit: usize,
    differences: &mut Vec<CandidateComparisonDifference>,
    cause_counts: &mut BTreeMap<String, usize>,
) -> CandidateComparisonSummary {
    let keys = manager
        .keys()
        .chain(legacy.keys())
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut manager_only_count = 0;
    let mut legacy_only_count = 0;
    let mut field_mismatch_count = 0;
    for key in keys {
        let manager_payload = manager.get(&key);
        let legacy_payload = legacy.get(&key);
        let (kind, manager_payload, legacy_payload) = match (manager_payload, legacy_payload) {
            (Some(manager_value), Some(legacy_value)) if manager_value != legacy_value => {
                field_mismatch_count += 1;
                increment_cause(cause_counts, "fieldMismatch");
                (
                    "fieldMismatch",
                    Some(manager_value.clone()),
                    Some(legacy_value.clone()),
                )
            }
            (Some(manager_value), None) => {
                manager_only_count += 1;
                increment_cause(cause_counts, "managerOnly");
                ("managerOnly", Some(manager_value.clone()), None)
            }
            (None, Some(legacy_value)) => {
                legacy_only_count += 1;
                increment_cause(cause_counts, "legacyOnly");
                ("legacyOnly", None, Some(legacy_value.clone()))
            }
            _ => continue,
        };
        if differences.len() < sample_limit {
            differences.push(CandidateComparisonDifference {
                category: category.to_owned(),
                key,
                kind: kind.to_owned(),
                manager_payload,
                legacy_payload,
            });
        }
    }
    CandidateComparisonSummary {
        manager_row_count: manager.len(),
        legacy_row_count: legacy.len(),
        manager_only_count,
        legacy_only_count,
        field_mismatch_count,
        manager_checksum: comparison_checksum(manager),
        legacy_checksum: comparison_checksum(legacy),
    }
}

fn increment_cause(counts: &mut BTreeMap<String, usize>, cause: &str) {
    *counts.entry(cause.to_owned()).or_default() += 1;
}

fn comparison_checksum(rows: &BTreeMap<String, String>) -> String {
    let mut digest = Sha256::new();
    for (key, payload) in rows {
        digest.update(key.as_bytes());
        digest.update([0]);
        digest.update(payload.as_bytes());
        digest.update([b'\n']);
    }
    format!("sha256:{}", hex::encode(digest.finalize()))
}

fn token_key_address(token_key_id: &str) -> Result<String, CandidateEdgeError> {
    TokenKeyId::from_str(token_key_id)
        .map(|token| token.currency_key().to_owned())
        .map_err(|error| CandidateEdgeError::InvalidRequest(error.to_string()))
}

fn comparison_address(value: &str) -> Result<String, CandidateEdgeError> {
    normalize_address(value).map_err(|error| {
        CandidateEdgeError::InvalidRequest(format!(
            "legacy candidate comparison contains an invalid address: {error}"
        ))
    })
}

fn unique_core_address(
    core_symbols: &BTreeMap<String, BTreeSet<String>>,
    symbol: &str,
) -> Option<String> {
    let addresses = core_symbols.get(symbol)?;
    if addresses.len() == 1 {
        addresses.iter().next().cloned()
    } else {
        None
    }
}

fn json_payload(value: serde_json::Value) -> String {
    serde_json::to_string(&value).unwrap_or_default()
}

fn canonical_json_text(value: &str) -> String {
    serde_json::from_str::<serde_json::Value>(value)
        .map(|parsed| parsed.to_string())
        .unwrap_or_else(|_| value.to_owned())
}

fn canonical_json_value(value: &str) -> serde_json::Value {
    serde_json::from_str(value).unwrap_or_else(|_| serde_json::Value::String(value.to_owned()))
}

fn map_candidate_pool(row: &rusqlite::Row<'_>) -> rusqlite::Result<CandidatePoolRow> {
    Ok(CandidatePoolRow {
        anchor_token_key_id: row.get(0)?,
        candidate_token_key_id: row.get(1)?,
        pair_key_id: row.get(2)?,
        pool_id: row.get(3)?,
        pool_manager: row.get(4)?,
        pool_key_json: row.get(5)?,
        token0: row.get(6)?,
        token1: row.get(7)?,
        token0_key_id: row.get(8)?,
        token1_key_id: row.get(9)?,
        fee: row.get(10)?,
        tick_spacing: row.get(11)?,
        hooks: row.get(12)?,
        sqrt_price_x96: row.get(13)?,
        tick: row.get(14)?,
        liquidity: row.get(15)?,
        liquidity_index: row.get(16)?,
        price: row.get(17)?,
        depth_status: row.get(18)?,
        liquidity_read_at: row.get(19)?,
        liquidity_block_number: row.get(20)?,
        evidence_source: row.get(21)?,
        is_first_hop_recommended: row.get::<_, i64>(22)? != 0,
        is_third_hop_recommended: row.get::<_, i64>(23)? != 0,
    })
}

fn open_connection(path: &Path, writable: bool) -> Result<Connection, CandidateEdgeError> {
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

#[cfg(test)]
mod tests {
    use super::{
        compare_candidate_edges, materialize_candidate_edges, materialize_candidate_edges_write,
        read_candidate_snapshot, CandidateEdgeError, CandidateEdgeWriteRequest, ExpectedUpstream,
    };
    use rusqlite::{params, Connection};

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const TA: &str = "0x2222222222222222222222222222222222222222";
    const TB: &str = "0x3333333333333333333333333333333333333333";
    const TZ: &str = "0x4444444444444444444444444444444444444444";
    const POOL_MANAGER: &str = "0x5555555555555555555555555555555555555555";
    const ZERO_HOOK: &str = "0x0000000000000000000000000000000000000000";

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = Connection::open(&path).unwrap();
        insert_token(&connection, "core", USDG, "USDG", "admitted");
        insert_token(&connection, "extension", TA, "TA", "admitted");
        insert_token(&connection, "extension", TB, "TB", "admitted");
        insert_token(&connection, "test", TZ, "TZ", "admitted");
        for (library, version) in [("core", 3_i64), ("extension", 2), ("test", 1)] {
            connection
                .execute(
                    "INSERT INTO token_library_state (
                        network, chain_id, library_key, library_version,
                        token_count, admitted_count, metadata_checksum,
                        source_checkpoint, generated_at, updated_at
                     ) VALUES (?1, ?2, ?3, ?4, 1, 1, ?5, 'fixture',
                               '2026-11-04T00:00:00Z',
                               '2026-11-04T00:00:00Z')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        library,
                        version,
                        format!("sha256:{library}-fixture")
                    ],
                )
                .unwrap();
        }
        connection
            .execute(
                "INSERT INTO pool_scope_config (
                    network, chain_id, scope_key, pool_table, library_keys_json,
                    library_versions_json, library_checksums_json, filter_mode,
                    excluded_token_key_ids_json, enabled, materialized_checksum,
                    last_rebuilt_at, created_at, updated_at
                 ) VALUES (?1, ?2, 'core4', 'v4_exchange_pools_core4',
                           '[\"core\"]', '{\"core\":3}',
                           '{\"core\":\"sha256:core-fixture\"}', 'edge_any',
                           '[]', 1, 'sha256:pool-fixture',
                           '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z',
                           '2026-11-04T00:00:00Z')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        connection
            .execute_batch(
                "CREATE TABLE v4_closed_loops (
                    network TEXT, chain_id INTEGER, loop_key TEXT
                 );
                 CREATE TABLE market_observation_yz_pairs (
                    network TEXT, chain_id INTEGER, direction_key TEXT
                 );
                 CREATE TABLE dex_candidate_pools (
                    network TEXT, chain_id INTEGER, marker TEXT
                 );",
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO dex_candidate_pools VALUES (?1, ?2, 'legacy-only')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        (directory, path)
    }

    fn insert_token(
        connection: &Connection,
        library: &str,
        address: &str,
        symbol: &str,
        status: &str,
    ) {
        let table = match library {
            "core" => "token_core",
            "extension" => "token_extension",
            "test" => "token_test",
            _ => panic!("unsupported fixture library"),
        };
        connection
            .execute(
                &format!(
                    "INSERT INTO {table} (
                        network, chain_id, token_key_id, v4_currency_key,
                        token_address, token_symbol, token_name, token_decimals,
                        token_kind, is_native, is_wrapped_native, is_stable,
                        admission_status, admission_source, metadata_source,
                        metadata_checksum, source_json, first_seen_at,
                        first_admitted_at, updated_at, blocked_reason
                     ) VALUES (?1, ?2, ?3, ?4, ?4, ?5, ?5, 18, 'erc20', 0, 0,
                               0, ?6, 'fixture', 'fixture', 'sha256:fixture',
                               '{{}}', '2026-11-04T00:00:00Z',
                               '2026-11-04T00:00:00Z',
                               '2026-11-04T00:00:00Z', NULL)"
                ),
                params![
                    NETWORK,
                    CHAIN_ID,
                    format!("{NETWORK}:{CHAIN_ID}:{address}"),
                    address,
                    symbol,
                    status
                ],
            )
            .unwrap();
    }

    #[allow(clippy::too_many_arguments)]
    fn insert_scope_pool(
        connection: &Connection,
        pool_id: &str,
        token0: &str,
        token1: &str,
        status: &str,
        fee: i64,
        sqrt_price_x96: Option<&str>,
        liquidity_index: Option<f64>,
    ) {
        connection
            .execute(
                "INSERT INTO v4_exchange_pools_core4 (
                    network, chain_id, protocol, pool_id, pool_manager,
                    pool_key_json, token0, token1, token0_key_id, token1_key_id,
                    fee, dynamic_fee, special_fee, tick_spacing, hooks,
                    sqrt_price_x96, tick, liquidity, liquidity_index, price,
                    depth_status, liquidity_read_at, liquidity_block_number,
                    evidence_source, source_block_number, source_tx_hash,
                    pool_status, materialized_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, '{}', ?5, ?6, ?7,
                           ?8, ?9, 0, 0, 10, ?10, ?11, 0, '1000000000000000',
                           ?12, NULL, 'thin', '2026-11-04T00:00:00Z', 100,
                           'fixture', 100, NULL, ?13,
                           '2026-11-04T00:00:00Z',
                           '2026-11-04T00:00:00Z')",
                params![
                    NETWORK,
                    CHAIN_ID,
                    pool_id,
                    POOL_MANAGER,
                    token0,
                    token1,
                    format!("{NETWORK}:{CHAIN_ID}:{token0}"),
                    format!("{NETWORK}:{CHAIN_ID}:{token1}"),
                    fee,
                    ZERO_HOOK,
                    sqrt_price_x96,
                    liquidity_index,
                    status
                ],
            )
            .unwrap();
    }

    fn replace_with_legacy_candidate_schema(connection: &Connection) {
        connection
            .execute_batch(
                "DROP TABLE dex_candidate_pools;
                 CREATE TABLE dex_candidate_anchor_tokens (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    token_role TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    pool_count INTEGER NOT NULL,
                    yc_optimal_pool_id TEXT,
                    zc_optimal_pool_id TEXT,
                    yc_backup_pool_ids_json TEXT NOT NULL,
                    zc_backup_pool_ids_json TEXT NOT NULL,
                    yc_zc_same_set INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                 );
                 CREATE TABLE dex_candidate_pool_pairs (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    anchor_address TEXT NOT NULL,
                    counter_address TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    pool_count INTEGER NOT NULL,
                    yc_optimal_pool_id TEXT,
                    zc_optimal_pool_id TEXT,
                    yc_backup_pool_ids_json TEXT NOT NULL,
                    zc_backup_pool_ids_json TEXT NOT NULL
                 );
                 CREATE TABLE dex_candidate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    address_pair TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_key_json TEXT,
                    pool_manager TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    dynamic_fee INTEGER NOT NULL,
                    special_fee INTEGER NOT NULL,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    is_first_hop_recommended INTEGER NOT NULL,
                    is_third_hop_recommended INTEGER NOT NULL,
                    usable_for_observation INTEGER
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
                    selection_basis TEXT
                 );",
            )
            .unwrap();
    }

    fn token_address(token_key_id: &str) -> &str {
        token_key_id.rsplit_once(':').unwrap().1
    }

    fn symbol_for(address: &str) -> &'static str {
        match address {
            USDG => "USDG",
            TA => "TA",
            TB => "TB",
            _ => panic!("unknown fixture token"),
        }
    }

    fn seed_matching_legacy_candidate_rows(
        connection: &Connection,
        database: &std::path::Path,
        legacy_anchor_scope: &str,
    ) {
        let snapshot = read_candidate_snapshot(database, NETWORK, CHAIN_ID, None, 100).unwrap();
        for row in &snapshot.anchor_tokens {
            connection
                .execute(
                    "INSERT INTO dex_candidate_anchor_tokens (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        token_role, token_address, pool_count, yc_optimal_pool_id,
                        zc_optimal_pool_id, yc_backup_pool_ids_json,
                        zc_backup_pool_ids_json, yc_zc_same_set, created_at, updated_at
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7, ?8,
                               ?9, ?10, ?11, ?12, 'fixture', 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        legacy_anchor_scope,
                        symbol_for(token_address(&row.anchor_token_key_id)),
                        row.token_role,
                        token_address(&row.token_key_id),
                        row.pool_count as i64,
                        row.yc_primary_pool_id,
                        row.zc_primary_pool_id,
                        serde_json::to_string(&row.yc_backup_pool_ids).unwrap(),
                        serde_json::to_string(&row.zc_backup_pool_ids).unwrap(),
                        row.yc_zc_same_set as i64
                    ],
                )
                .unwrap();
        }
        for row in &snapshot.pool_pairs {
            let anchor = token_address(&row.anchor_token_key_id);
            let candidate = token_address(&row.candidate_token_key_id);
            let anchor_symbol = symbol_for(anchor);
            connection
                .execute(
                    "INSERT INTO dex_candidate_pool_pairs (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        anchor_address, counter_address, direction_key, pool_count,
                        yc_optimal_pool_id, zc_optimal_pool_id,
                        yc_backup_pool_ids_json, zc_backup_pool_ids_json
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7, ?8,
                               ?9, '', ?10, '[]')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        legacy_anchor_scope,
                        anchor_symbol,
                        anchor,
                        candidate,
                        format!("{anchor}:{candidate}"),
                        row.pool_count as i64,
                        row.first_primary_pool_id,
                        serde_json::to_string(&row.first_backup_pool_ids).unwrap()
                    ],
                )
                .unwrap();
            connection
                .execute(
                    "INSERT INTO dex_candidate_pool_pairs (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        anchor_address, counter_address, direction_key, pool_count,
                        yc_optimal_pool_id, zc_optimal_pool_id,
                        yc_backup_pool_ids_json, zc_backup_pool_ids_json
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7, ?8,
                               '', ?9, '[]', ?10)",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        legacy_anchor_scope,
                        anchor_symbol,
                        candidate,
                        anchor,
                        format!("{candidate}:{anchor}"),
                        row.pool_count as i64,
                        row.third_primary_pool_id,
                        serde_json::to_string(&row.third_backup_pool_ids).unwrap()
                    ],
                )
                .unwrap();
        }
        for row in &snapshot.pools {
            let anchor = token_address(&row.anchor_token_key_id);
            connection
                .execute(
                    "INSERT INTO dex_candidate_pools (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        address_pair, direction_key, pool_id, pool_key_json,
                        pool_manager, token0, token1, fee, dynamic_fee, special_fee,
                        tick_spacing, hooks, is_first_hop_recommended,
                        is_third_hop_recommended, usable_for_observation
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?5, ?6, ?7,
                               ?8, ?9, ?10, ?11, 0, 0, ?12, ?13, ?14, ?15, 1)",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        legacy_anchor_scope,
                        symbol_for(anchor),
                        format!("{}:{}", row.token0, row.token1),
                        row.pool_id,
                        row.pool_key_json,
                        row.pool_manager,
                        row.token0,
                        row.token1,
                        row.fee,
                        row.tick_spacing,
                        row.hooks,
                        row.is_first_hop_recommended as i64,
                        row.is_third_hop_recommended as i64
                    ],
                )
                .unwrap();
        }
        for row in &snapshot.recommendations {
            let anchor = token_address(&row.anchor_token_key_id);
            let candidate = token_address(&row.candidate_token_key_id);
            connection
                .execute(
                    "INSERT INTO dex_candidate_pool_recommendations (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        counter_address, direction_key, hop, rank, pool_id,
                        depth_score, price, fee, selection_basis
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7, ?8,
                               ?9, 0, NULL, ?10, 'fixture')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        legacy_anchor_scope,
                        symbol_for(anchor),
                        candidate,
                        if row.hop == "first" {
                            format!("{anchor}:{candidate}")
                        } else {
                            format!("{candidate}:{anchor}")
                        },
                        row.hop,
                        row.rank as i64,
                        row.pool_id,
                        row.fee
                    ],
                )
                .unwrap();
        }
    }

    fn request(apply: bool) -> CandidateEdgeWriteRequest {
        CandidateEdgeWriteRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "candidate-edge.rebuild".to_owned(),
            request_id: "candidate-write-1".to_owned(),
            idempotency_key: "candidate-idem-1".to_owned(),
            apply,
            expected_upstream: ExpectedUpstream::default(),
        }
    }

    #[test]
    fn extension_ta_and_tb_are_admitted_without_usd_depth_and_rebuild_is_stable() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        insert_scope_pool(
            &connection,
            "pool-usdg-ta",
            USDG,
            TA,
            "active",
            500,
            Some("79228162514264337593543950336"),
            Some(0.25),
        );
        insert_scope_pool(
            &connection,
            "pool-usdg-tb",
            USDG,
            TB,
            "active",
            3000,
            None,
            Some(0.1),
        );
        drop(connection);

        let dry = materialize_candidate_edges(&database, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(dry.eligible_pool_count, 2);
        assert_eq!(dry.candidate_token_count, 2);
        assert_eq!(dry.recommendation_count, 4);
        assert!(dry.skip_reason_counts.is_empty());
        let connection = Connection::open(&database).unwrap();
        let candidate_pools: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_dex_candidate_pools",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(candidate_pools, 0);
        drop(connection);

        let applied = materialize_candidate_edges(&database, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(applied.materialized_checksum, dry.materialized_checksum);
        assert_eq!(applied.changed_rows, 10);
        let repeated = materialize_candidate_edges(&database, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(repeated.changed_rows, 0);
        assert!(!repeated.consumers_need_rebuild);
        assert_eq!(repeated.candidate_version, applied.candidate_version);

        let connection = Connection::open(&database).unwrap();
        let extension_only: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_dex_candidate_anchor_tokens
                 WHERE token_library_key = 'extension'
                   AND token_symbol IN ('TA', 'TB')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(extension_only, 2);
        let tz_in_extension: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM token_extension WHERE token_symbol = 'TZ'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(tz_in_extension, 0);
        drop(connection);
        let snapshot = read_candidate_snapshot(&database, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.tokens.len(), 3);
        assert!(snapshot
            .tokens
            .iter()
            .any(|row| row.token_symbol == "USDG" && row.token_address == USDG));
        assert!(snapshot
            .tokens
            .iter()
            .any(|row| row.token_symbol == "TA" && row.token_address == TA));
        assert!(snapshot
            .tokens
            .iter()
            .any(|row| row.token_symbol == "TB" && row.token_address == TB));
        let connection = Connection::open(&database).unwrap();
        let yz_rows: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM market_observation_yz_pairs",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let loop_rows: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loops", [], |row| row.get(0))
            .unwrap();
        let legacy_marker: String = connection
            .query_row("SELECT marker FROM dex_candidate_pools", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(yz_rows, 0);
        assert_eq!(loop_rows, 0);
        assert_eq!(legacy_marker, "legacy-only");
    }

    #[test]
    fn abnormal_pool_is_isolated_from_candidate_recommendations() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        insert_scope_pool(
            &connection,
            "pool-invalid",
            USDG,
            TA,
            "invalid",
            500,
            None,
            Some(0.1),
        );
        drop(connection);

        let result = materialize_candidate_edges(&database, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(result.eligible_pool_count, 0);
        assert_eq!(result.abnormal_pool_count, 1);
        assert_eq!(result.recommendation_count, 0);
        let snapshot = read_candidate_snapshot(&database, NETWORK, CHAIN_ID, None, 100).unwrap();
        assert_eq!(snapshot.abnormal_pools.len(), 1);
        assert_eq!(snapshot.abnormal_pools[0].pool_status, "invalid");
        assert!(snapshot.pools.is_empty());
    }

    #[test]
    fn upstream_mismatch_rejects_before_writing() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        insert_scope_pool(
            &connection,
            "pool-usdg-ta",
            USDG,
            TA,
            "active",
            500,
            None,
            Some(0.1),
        );
        drop(connection);

        let mut write = request(true);
        write.expected_upstream.pool_scope_checksum = Some("sha256:stale".to_owned());
        let error = materialize_candidate_edges_write(&database, write).unwrap_err();
        assert!(matches!(
            error,
            CandidateEdgeError::ExpectedUpstreamMismatch { .. }
        ));
        let connection = Connection::open(database).unwrap();
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_dex_candidate_pools",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn comparison_is_read_only_and_reaches_cutover_readiness_for_matching_rows() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        insert_scope_pool(
            &connection,
            "pool-usdg-ta",
            USDG,
            TA,
            "active",
            500,
            Some("79228162514264337593543950336"),
            Some(0.25),
        );
        drop(connection);
        let applied = materialize_candidate_edges(&database, NETWORK, CHAIN_ID, true).unwrap();

        let connection = Connection::open(&database).unwrap();
        replace_with_legacy_candidate_schema(&connection);
        seed_matching_legacy_candidate_rows(&connection, &database, "legacy-core4");
        drop(connection);
        let before = std::fs::read(&database).unwrap();

        let comparison =
            compare_candidate_edges(&database, NETWORK, CHAIN_ID, "legacy-core4", 50).unwrap();
        let after = std::fs::read(&database).unwrap();
        assert_eq!(before, after);
        assert!(comparison.enabled);
        assert_eq!(
            comparison.manager_checksum,
            Some(applied.materialized_checksum)
        );
        assert_eq!(comparison.anchor_tokens.field_mismatch_count, 0);
        assert_eq!(comparison.pool_pairs.field_mismatch_count, 0);
        assert_eq!(comparison.pools.field_mismatch_count, 0);
        assert_eq!(comparison.recommendations.field_mismatch_count, 0);
        assert!(comparison.cause_counts.is_empty(), "{comparison:#?}");
        assert!(comparison.differences.is_empty(), "{comparison:#?}");
        assert!(comparison.ready_for_cutover);
    }

    #[test]
    fn comparison_reports_legacy_pool_field_mismatch_without_writing() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        insert_scope_pool(
            &connection,
            "pool-usdg-ta",
            USDG,
            TA,
            "active",
            500,
            None,
            Some(0.1),
        );
        drop(connection);
        materialize_candidate_edges(&database, NETWORK, CHAIN_ID, true).unwrap();

        let connection = Connection::open(&database).unwrap();
        replace_with_legacy_candidate_schema(&connection);
        seed_matching_legacy_candidate_rows(&connection, &database, "legacy-core4");
        connection
            .execute(
                "UPDATE dex_candidate_pools SET fee = 3000 WHERE pool_id = 'pool-usdg-ta'",
                [],
            )
            .unwrap();
        drop(connection);

        let comparison =
            compare_candidate_edges(&database, NETWORK, CHAIN_ID, "legacy-core4", 50).unwrap();
        assert!(!comparison.ready_for_cutover);
        assert_eq!(comparison.pools.field_mismatch_count, 1);
        assert_eq!(comparison.cause_counts["fieldMismatch"], 1);
        assert_eq!(comparison.differences.len(), 1);
        assert_eq!(comparison.differences[0].category, "pools");
        assert_eq!(comparison.differences[0].kind, "fieldMismatch");
    }

    #[test]
    fn write_is_idempotent_and_dry_run_does_not_persist_idempotency() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        insert_scope_pool(
            &connection,
            "pool-usdg-ta",
            USDG,
            TA,
            "active",
            500,
            None,
            Some(0.1),
        );
        drop(connection);

        let dry = materialize_candidate_edges_write(&database, request(false)).unwrap();
        assert!(!dry.idempotent_replay);
        let connection = Connection::open(&database).unwrap();
        let requests: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(requests, 0);
        drop(connection);

        let first = materialize_candidate_edges_write(&database, request(true)).unwrap();
        assert!(!first.idempotent_replay);
        let replay = materialize_candidate_edges_write(
            &database,
            CandidateEdgeWriteRequest {
                request_id: "candidate-write-2".to_owned(),
                ..request(true)
            },
        )
        .unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, first.result);
    }
}
