use candidate_edge_pool_manager::{read_candidate_status, CandidateEdgeError, CandidateEdgeStatus};
use pool_manager::{read_scope_status, ScopeStatus};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

const NETWORK_PROTOCOL: &str = "uniswap_v4";
const CANDIDATE_SCOPE_KEY: &str = "core4";
const EXCHANGE_SCOPE_KEY: &str = "candidate_exchange";
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const MAX_SNAPSHOT_LIMIT: usize = 1_000;
const MAX_COMPARISON_ROWS: usize = 1_000_000;

#[derive(Debug)]
pub enum CandidateExchangeError {
    Sqlite(rusqlite::Error),
    Json(serde_json::Error),
    InvalidRequest(String),
    CandidateEdgeRead(String),
    PoolScopeRead(String),
    ComparisonRead {
        stage: String,
        source: rusqlite::Error,
    },
    MissingCandidateEdgeState,
    MissingPoolScope,
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

impl fmt::Display for CandidateExchangeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "candidate exchange storage error: {error}"),
            Self::Json(error) => write!(formatter, "candidate exchange json error: {error}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid candidate exchange request: {message}")
            }
            Self::CandidateEdgeRead(message) => {
                write!(formatter, "candidate edge upstream read failed: {message}")
            }
            Self::PoolScopeRead(message) => {
                write!(formatter, "pool scope upstream read failed: {message}")
            }
            Self::ComparisonRead { stage, source } => {
                write!(
                    formatter,
                    "candidate exchange comparison {stage} read failed: {source}"
                )
            }
            Self::MissingCandidateEdgeState => {
                formatter.write_str("candidate edge scope core4 has not been materialized")
            }
            Self::MissingPoolScope => {
                formatter.write_str("pool scope candidate_exchange has not been materialized")
            }
            Self::InvalidStoredPool(message) => {
                write!(
                    formatter,
                    "invalid stored candidate exchange pool: {message}"
                )
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

impl std::error::Error for CandidateExchangeError {}

impl From<rusqlite::Error> for CandidateExchangeError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<serde_json::Error> for CandidateExchangeError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExpectedUpstream {
    pub candidate_edge_version: Option<i64>,
    pub candidate_edge_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    #[serde(default)]
    pub library_versions: BTreeMap<String, i64>,
    #[serde(default)]
    pub library_checksums: BTreeMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct CandidateExchangeWriteRequest {
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
pub struct CandidateExchangeMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub apply: bool,
    pub dry_run: bool,
    pub candidate_edge_version: i64,
    pub candidate_edge_checksum: String,
    pub pool_scope_checksum: String,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub anchor_count: usize,
    pub token_count: usize,
    pub direction_count: usize,
    pub empty_direction_count: usize,
    pub pool_count: usize,
    pub abnormal_direction_count: usize,
    pub abnormal_pool_count: usize,
    pub changed_rows: usize,
    pub observation_version: i64,
    pub materialized_checksum: String,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateExchangeWriteOutcome {
    #[serde(flatten)]
    pub result: CandidateExchangeMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateExchangeStatus {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub observation_version: i64,
    pub materialized_checksum: Option<String>,
    pub candidate_edge_version: Option<i64>,
    pub candidate_edge_checksum: Option<String>,
    pub pool_scope_checksum: Option<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub anchor_count: usize,
    pub token_count: usize,
    pub direction_count: usize,
    pub empty_direction_count: usize,
    pub pool_count: usize,
    pub abnormal_direction_count: usize,
    pub abnormal_pool_count: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct YzPairRow {
    pub anchor_token_key_id: String,
    pub direction_key: String,
    pub token_x_key_id: String,
    pub token_y_key_id: String,
    pub has_direct_swap_pool: bool,
    pub direct_swap_pool_count: usize,
    pub pool_ids: Vec<String>,
    pub optimal_pool_id: Option<String>,
    pub observation_empty_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IntermediatePoolRow {
    pub anchor_token_key_id: String,
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
    pub selection_rank: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AbnormalYzPairRow {
    pub anchor_token_key_id: String,
    pub direction_key: String,
    pub token_x_key_id: String,
    pub token_y_key_id: String,
    pub abnormal_pool_count: usize,
    pub pool_ids: Vec<String>,
    pub reasons: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AbnormalIntermediatePoolRow {
    pub anchor_token_key_id: String,
    pub pool_id: String,
    pub pool_status: String,
    pub pool_key_json: String,
    pub token0_key_id: String,
    pub token1_key_id: String,
    pub fee: i64,
    pub tick_spacing: i64,
    pub hooks: String,
    pub reasons: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LiquiditySnapshotRow {
    pub anchor_token_key_id: String,
    pub pool_id: String,
    pub has_liquidity: Option<bool>,
    pub liquidity: Option<String>,
    pub liquidity_index: Option<f64>,
    pub sqrt_price_x96: Option<String>,
    pub price: Option<f64>,
    pub tick: Option<i64>,
    pub depth_status: Option<String>,
    pub liquidity_read_at: Option<String>,
    pub liquidity_block_number: Option<i64>,
    pub evidence_source: Option<String>,
    pub observed_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateExchangeSnapshot {
    pub status: CandidateExchangeStatus,
    pub tokens: Vec<CandidateExchangeTokenRow>,
    pub yz_pairs: Vec<YzPairRow>,
    pub pools: Vec<IntermediatePoolRow>,
    pub abnormal_yz_pairs: Vec<AbnormalYzPairRow>,
    pub abnormal_pools: Vec<AbnormalIntermediatePoolRow>,
    pub liquidity_snapshots: Vec<LiquiditySnapshotRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateExchangeTokenRow {
    pub token_key_id: String,
    pub token_address: String,
    pub token_symbol: String,
    pub token_name: Option<String>,
    pub token_decimals: i64,
    pub token_library_key: String,
    pub admission_status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExchangeComparisonSummary {
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
pub struct ExchangeComparisonDifference {
    pub category: String,
    pub key: String,
    pub kind: String,
    pub cause: String,
    pub manager_payload: Option<String>,
    pub legacy_payload: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateExchangeComparison {
    pub enabled: bool,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub legacy_anchor_scope: String,
    pub manager_checksum: Option<String>,
    pub ready_for_cutover: bool,
    pub yz_pairs: ExchangeComparisonSummary,
    pub pools: ExchangeComparisonSummary,
    pub abnormal_yz_pairs: ExchangeComparisonSummary,
    pub abnormal_pools: ExchangeComparisonSummary,
    pub cause_counts: BTreeMap<String, usize>,
    pub differences: Vec<ExchangeComparisonDifference>,
    pub differences_truncated: bool,
}

#[derive(Debug, Clone)]
struct CatalogToken {
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
    sqrt_price_x96: Option<String>,
    tick: Option<i64>,
    liquidity: Option<String>,
    liquidity_index: Option<f64>,
    price: Option<f64>,
    depth_status: Option<String>,
    liquidity_read_at: Option<String>,
    liquidity_block_number: Option<i64>,
    evidence_source: Option<String>,
    pool_status: String,
}

struct ExchangeInput {
    candidate_edge: CandidateEdgeStatus,
    pool_scope: ScopeStatus,
    catalog: BTreeMap<String, CatalogToken>,
    anchors: BTreeMap<String, BTreeSet<String>>,
    pools: Vec<ScopePool>,
}

#[derive(Debug, Clone, Default, PartialEq)]
struct DesiredExchangeData {
    yz_pairs: Vec<YzPairRow>,
    pools: Vec<IntermediatePoolRow>,
    abnormal_yz_pairs: Vec<AbnormalYzPairRow>,
    abnormal_pools: Vec<AbnormalIntermediatePoolRow>,
    liquidity_snapshots: Vec<LiquiditySnapshotRow>,
    token_count: usize,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ExchangeChecksumPayload<'a> {
    candidate_edge_version: i64,
    candidate_edge_checksum: &'a str,
    pool_scope_checksum: &'a str,
    library_versions: &'a BTreeMap<String, i64>,
    library_checksums: &'a BTreeMap<String, String>,
    yz_pairs: &'a [YzPairRow],
    pools: &'a [IntermediatePoolRow],
    abnormal_yz_pairs: &'a [AbnormalYzPairRow],
    abnormal_pools: &'a [AbnormalIntermediatePoolRow],
    liquidity_snapshots: &'a [LiquiditySnapshotRow],
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedExchangeWrite {
    request_id: String,
    request_digest: String,
    result: CandidateExchangeMaterializationResult,
}

pub fn materialize_candidate_exchanges(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<CandidateExchangeMaterializationResult, CandidateExchangeError> {
    materialize_impl(database_path.as_ref(), network, chain_id, apply, None)
        .map(|(result, _)| result)
}

pub fn materialize_candidate_exchanges_write(
    database_path: impl AsRef<Path>,
    request: CandidateExchangeWriteRequest,
) -> Result<CandidateExchangeWriteOutcome, CandidateExchangeError> {
    let (result, idempotent_replay) = materialize_impl(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        request.apply,
        Some(&request),
    )?;
    Ok(CandidateExchangeWriteOutcome {
        result,
        idempotent_replay,
    })
}

fn materialize_impl(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    request: Option<&CandidateExchangeWriteRequest>,
) -> Result<(CandidateExchangeMaterializationResult, bool), CandidateExchangeError> {
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
        let outcome = materialize_on_connection(
            &transaction,
            database_path,
            network,
            chain_id,
            true,
            prepared_write,
        )?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        materialize_on_connection(
            &connection,
            database_path,
            network,
            chain_id,
            false,
            prepared_write,
        )
    }
}

fn materialize_on_connection(
    connection: &Connection,
    database_path: &Path,
    network: &str,
    chain_id: i64,
    apply: bool,
    prepared_write: Option<(&CandidateExchangeWriteRequest, String)>,
) -> Result<(CandidateExchangeMaterializationResult, bool), CandidateExchangeError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(evidence_json) = find_write_request(connection, request)? {
                let persisted = serde_json::from_str::<PersistedExchangeWrite>(&evidence_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(CandidateExchangeError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok((persisted.result, true));
            }
        }
    }

    let input = read_exchange_input(database_path, connection, network, chain_id)?;
    if let Some((request, _)) = prepared_write.as_ref() {
        validate_expected_upstream(&request.expected_upstream, &input)?;
    }
    let desired = build_exchange_rows(&input)?;
    let materialized_checksum = exchange_checksum(&input, &desired)?;
    let previous = read_status_on_connection(connection, network, chain_id)?;
    let counts_match = previous.enabled
        && previous.direction_count == desired.yz_pairs.len()
        && previous.pool_count == desired.pools.len()
        && previous.abnormal_direction_count == desired.abnormal_yz_pairs.len()
        && previous.abnormal_pool_count == desired.abnormal_pools.len();
    let checksum_changed = previous.materialized_checksum.as_deref()
        != Some(materialized_checksum.as_str())
        || !counts_match;
    let observation_version = if checksum_changed {
        previous.observation_version.saturating_add(1).max(1)
    } else {
        previous.observation_version.max(1)
    };
    let changed_rows = if checksum_changed {
        desired.yz_pairs.len()
            + desired.pools.len()
            + desired.abnormal_yz_pairs.len()
            + desired.abnormal_pools.len()
            + desired.liquidity_snapshots.len()
    } else {
        0
    };
    let empty_direction_count = desired
        .yz_pairs
        .iter()
        .filter(|row| !row.has_direct_swap_pool)
        .count();
    let result = CandidateExchangeMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: EXCHANGE_SCOPE_KEY.to_owned(),
        apply,
        dry_run: !apply,
        candidate_edge_version: input.candidate_edge.candidate_version,
        candidate_edge_checksum: input
            .candidate_edge
            .materialized_checksum
            .clone()
            .ok_or(CandidateExchangeError::MissingCandidateEdgeState)?,
        pool_scope_checksum: input
            .pool_scope
            .materialized_checksum
            .clone()
            .ok_or(CandidateExchangeError::MissingPoolScope)?,
        library_versions: input.pool_scope.library_versions.clone(),
        library_checksums: input.pool_scope.library_checksums.clone(),
        anchor_count: input.anchors.len(),
        token_count: desired.token_count,
        direction_count: desired.yz_pairs.len(),
        empty_direction_count,
        pool_count: desired.pools.len(),
        abnormal_direction_count: desired.abnormal_yz_pairs.len(),
        abnormal_pool_count: desired.abnormal_pools.len(),
        changed_rows,
        observation_version,
        materialized_checksum,
        consumers_need_rebuild: checksum_changed,
    };

    if apply {
        if checksum_changed {
            replace_exchange_rows(connection, network, chain_id, &desired, &result)?;
        }
        if let Some((request, request_digest)) = prepared_write {
            persist_write_request(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

fn validate_operation(network: &str, chain_id: i64) -> Result<(), CandidateExchangeError> {
    if network.trim().is_empty() {
        return Err(CandidateExchangeError::InvalidRequest(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(CandidateExchangeError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    Ok(())
}

fn validate_write_request(
    request: &CandidateExchangeWriteRequest,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<(), CandidateExchangeError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(CandidateExchangeError::InvalidRequest(
            "write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.apply != apply {
        return Err(CandidateExchangeError::InvalidRequest(
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
) -> Result<(), CandidateExchangeError> {
    if value.is_empty() || value.len() > max_length {
        return Err(CandidateExchangeError::InvalidRequest(format!(
            "{label} must contain 1..={max_length} bytes"
        )));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':'))
    {
        return Err(CandidateExchangeError::InvalidRequest(format!(
            "{label} contains unsupported characters"
        )));
    }
    Ok(())
}

fn request_digest(
    request: &CandidateExchangeWriteRequest,
) -> Result<String, CandidateExchangeError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&payload)?))
    ))
}

fn read_exchange_input(
    database_path: &Path,
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ExchangeInput, CandidateExchangeError> {
    let candidate_edge =
        read_candidate_status(database_path, network, chain_id).map_err(map_candidate_edge_read)?;
    if candidate_edge.candidate_version <= 0
        || candidate_edge
            .materialized_checksum
            .as_deref()
            .is_none_or(str::is_empty)
    {
        return Err(CandidateExchangeError::MissingCandidateEdgeState);
    }
    let pool_scope = read_scope_status(database_path, network, chain_id, EXCHANGE_SCOPE_KEY)
        .map_err(|error| CandidateExchangeError::PoolScopeRead(error.to_string()))?;
    if !pool_scope.enabled
        || pool_scope.materialized_checksum.is_none()
        || pool_scope.checksum_matches_config != Some(true)
    {
        return Err(CandidateExchangeError::MissingPoolScope);
    }
    let catalog = read_catalog(connection, network, chain_id)?;
    let anchors = read_anchor_groups(connection, network, chain_id, &catalog)?;
    validate_candidate_edge_state(&candidate_edge, &anchors)?;
    let pools = read_scope_pools(connection, network, chain_id)?;
    Ok(ExchangeInput {
        candidate_edge,
        pool_scope,
        catalog,
        anchors,
        pools,
    })
}

fn map_candidate_edge_read(error: CandidateEdgeError) -> CandidateExchangeError {
    match error {
        CandidateEdgeError::Sqlite(error) => CandidateExchangeError::Sqlite(error),
        CandidateEdgeError::Json(error) => CandidateExchangeError::Json(error),
        CandidateEdgeError::ComparisonRead { source, .. } => CandidateExchangeError::Sqlite(source),
        error => CandidateExchangeError::CandidateEdgeRead(error.to_string()),
    }
}

fn validate_expected_upstream(
    expected: &ExpectedUpstream,
    input: &ExchangeInput,
) -> Result<(), CandidateExchangeError> {
    if let Some(expected_version) = expected.candidate_edge_version {
        if expected_version != input.candidate_edge.candidate_version {
            return Err(CandidateExchangeError::ExpectedUpstreamMismatch {
                field: "candidate_edge_version".to_owned(),
                expected: expected_version.to_string(),
                actual: input.candidate_edge.candidate_version.to_string(),
            });
        }
    }
    if let Some(expected_checksum) = expected.candidate_edge_checksum.as_deref() {
        let actual = input
            .candidate_edge
            .materialized_checksum
            .as_deref()
            .ok_or(CandidateExchangeError::MissingCandidateEdgeState)?;
        if expected_checksum != actual {
            return Err(CandidateExchangeError::ExpectedUpstreamMismatch {
                field: "candidate_edge_checksum".to_owned(),
                expected: expected_checksum.to_owned(),
                actual: actual.to_owned(),
            });
        }
    }
    let actual_pool_scope_checksum = input
        .pool_scope
        .materialized_checksum
        .as_deref()
        .ok_or(CandidateExchangeError::MissingPoolScope)?;
    if let Some(expected_checksum) = expected.pool_scope_checksum.as_deref() {
        if expected_checksum != actual_pool_scope_checksum {
            return Err(CandidateExchangeError::ExpectedUpstreamMismatch {
                field: "pool_scope_checksum".to_owned(),
                expected: expected_checksum.to_owned(),
                actual: actual_pool_scope_checksum.to_owned(),
            });
        }
    }
    for (library_key, expected_version) in &expected.library_versions {
        let actual = input
            .pool_scope
            .library_versions
            .get(library_key)
            .copied()
            .unwrap_or_default();
        if *expected_version != actual {
            return Err(CandidateExchangeError::ExpectedUpstreamMismatch {
                field: format!("library_versions.{library_key}"),
                expected: expected_version.to_string(),
                actual: actual.to_string(),
            });
        }
    }
    for (library_key, expected_checksum) in &expected.library_checksums {
        let actual = input
            .pool_scope
            .library_checksums
            .get(library_key)
            .cloned()
            .unwrap_or_default();
        if expected_checksum != &actual {
            return Err(CandidateExchangeError::ExpectedUpstreamMismatch {
                field: format!("library_checksums.{library_key}"),
                expected: expected_checksum.clone(),
                actual,
            });
        }
    }
    Ok(())
}

fn read_catalog(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, CatalogToken>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT token_key_id, library_key
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'
         ORDER BY token_key_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            CatalogToken {
                library_key: row.get(1)?,
            },
        ))
    })?;
    let mut catalog = BTreeMap::new();
    for row in rows {
        let (token_key_id, token) = row?;
        if catalog.insert(token_key_id.clone(), token).is_some() {
            return Err(CandidateExchangeError::InvalidStoredPool(format!(
                "duplicate admitted token_key_id {token_key_id}"
            )));
        }
    }
    Ok(catalog)
}

fn read_anchor_groups(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    catalog: &BTreeMap<String, CatalogToken>,
) -> Result<BTreeMap<String, BTreeSet<String>>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, token_key_id, anchor_library_key, token_library_key
         FROM manager_dex_candidate_anchor_tokens
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
         ORDER BY anchor_token_key_id, token_key_id",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, CANDIDATE_SCOPE_KEY],
        |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        },
    )?;
    let mut anchors = BTreeMap::<String, BTreeSet<String>>::new();
    for row in rows {
        let (anchor_key_id, token_key_id, anchor_library_key, token_library_key) = row?;
        let anchor = catalog.get(&anchor_key_id).ok_or_else(|| {
            CandidateExchangeError::InvalidStoredPool(format!(
                "candidate anchor {anchor_key_id} is not admitted"
            ))
        })?;
        let token = catalog.get(&token_key_id).ok_or_else(|| {
            CandidateExchangeError::InvalidStoredPool(format!(
                "candidate token {token_key_id} is not admitted"
            ))
        })?;
        if anchor.library_key != anchor_library_key || token.library_key != token_library_key {
            return Err(CandidateExchangeError::InvalidStoredPool(format!(
                "candidate anchor membership library changed for {anchor_key_id}/{token_key_id}"
            )));
        }
        anchors
            .entry(anchor_key_id)
            .or_default()
            .insert(token_key_id);
    }
    if anchors.is_empty() {
        return Err(CandidateExchangeError::MissingCandidateEdgeState);
    }
    Ok(anchors)
}

fn validate_candidate_edge_state(
    status: &CandidateEdgeStatus,
    anchors: &BTreeMap<String, BTreeSet<String>>,
) -> Result<(), CandidateExchangeError> {
    let unique_tokens = anchors.values().flatten().collect::<BTreeSet<_>>().len();
    if anchors.len() != status.anchor_token_count || unique_tokens != status.candidate_token_count {
        return Err(CandidateExchangeError::CandidateEdgeRead(format!(
            "candidate anchor rows do not match state counts: anchors {}/{}, tokens {}/{}",
            anchors.len(),
            status.anchor_token_count,
            unique_tokens,
            status.candidate_token_count
        )));
    }
    Ok(())
}

fn read_scope_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<ScopePool>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT pool_id, pool_manager, pool_key_json, token0, token1,
                token0_key_id, token1_key_id, fee, tick_spacing, hooks,
                sqrt_price_x96, tick, liquidity, liquidity_index, price,
                depth_status, liquidity_read_at, liquidity_block_number,
                evidence_source, pool_status
         FROM v4_exchange_pools_candidate_exchange
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
         ORDER BY pool_id",
    )?;
    let rows = statement.query_map(params![network, chain_id, NETWORK_PROTOCOL], |row| {
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
            sqrt_price_x96: row.get(10)?,
            tick: row.get(11)?,
            liquidity: row.get(12)?,
            liquidity_index: row.get(13)?,
            price: row.get(14)?,
            depth_status: row.get(15)?,
            liquidity_read_at: row.get(16)?,
            liquidity_block_number: row.get(17)?,
            evidence_source: row.get(18)?,
            pool_status: row.get(19)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn build_exchange_rows(
    input: &ExchangeInput,
) -> Result<DesiredExchangeData, CandidateExchangeError> {
    let active_pools = input
        .pools
        .iter()
        .filter(|pool| pool.pool_status == "active")
        .collect::<Vec<_>>();
    let abnormal_pools = input
        .pools
        .iter()
        .filter(|pool| pool.pool_status != "active")
        .collect::<Vec<_>>();
    let mut desired = DesiredExchangeData {
        token_count: input
            .anchors
            .values()
            .flatten()
            .collect::<BTreeSet<_>>()
            .len(),
        ..DesiredExchangeData::default()
    };

    for (anchor_token_key_id, tokens) in &input.anchors {
        for token_key_id in tokens {
            if !input.catalog.contains_key(token_key_id) {
                return Err(CandidateExchangeError::InvalidStoredPool(format!(
                    "unadmitted token {token_key_id} belongs to anchor {anchor_token_key_id}"
                )));
            }
        }
        let mut active_by_pair = BTreeMap::<String, Vec<&ScopePool>>::new();
        for pool in &active_pools {
            if tokens.contains(&pool.token0_key_id) && tokens.contains(&pool.token1_key_id) {
                active_by_pair
                    .entry(canonical_pair_key(&pool.token0_key_id, &pool.token1_key_id))
                    .or_default()
                    .push(*pool);
            }
        }
        let mut abnormal_by_pair = BTreeMap::<String, Vec<&ScopePool>>::new();
        for pool in &abnormal_pools {
            if tokens.contains(&pool.token0_key_id) && tokens.contains(&pool.token1_key_id) {
                abnormal_by_pair
                    .entry(canonical_pair_key(&pool.token0_key_id, &pool.token1_key_id))
                    .or_default()
                    .push(*pool);
            }
        }
        for pools in active_by_pair.values_mut() {
            pools.sort_by(|left, right| {
                pool_score(right)
                    .partial_cmp(&pool_score(left))
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| left.pool_id.cmp(&right.pool_id))
            });
        }
        for pools in abnormal_by_pair.values_mut() {
            pools.sort_by(|left, right| left.pool_id.cmp(&right.pool_id));
        }

        for token_x in tokens {
            for token_y in tokens {
                if token_x == token_y {
                    continue;
                }
                let direction_key = format!("{token_x}->{token_y}");
                let pair_key = canonical_pair_key(token_x, token_y);
                let matched = active_by_pair.get(&pair_key).cloned().unwrap_or_default();
                let pool_ids = matched
                    .iter()
                    .map(|pool| pool.pool_id.clone())
                    .collect::<Vec<_>>();
                let empty_reason = if pool_ids.is_empty() {
                    if abnormal_by_pair.contains_key(&pair_key) {
                        Some("only_abnormal_pools".to_owned())
                    } else {
                        Some("no_reachable_v4_pool".to_owned())
                    }
                } else {
                    None
                };
                desired.yz_pairs.push(YzPairRow {
                    anchor_token_key_id: anchor_token_key_id.clone(),
                    direction_key: direction_key.clone(),
                    token_x_key_id: token_x.clone(),
                    token_y_key_id: token_y.clone(),
                    has_direct_swap_pool: !pool_ids.is_empty(),
                    direct_swap_pool_count: pool_ids.len(),
                    optimal_pool_id: pool_ids.first().cloned(),
                    pool_ids,
                    observation_empty_reason: empty_reason,
                });
                if let Some(abnormal) = abnormal_by_pair.get(&pair_key) {
                    let reasons = abnormal
                        .iter()
                        .flat_map(|pool| abnormal_reasons(pool))
                        .collect::<BTreeSet<_>>()
                        .into_iter()
                        .collect::<Vec<_>>();
                    desired.abnormal_yz_pairs.push(AbnormalYzPairRow {
                        anchor_token_key_id: anchor_token_key_id.clone(),
                        direction_key,
                        token_x_key_id: token_x.clone(),
                        token_y_key_id: token_y.clone(),
                        abnormal_pool_count: abnormal.len(),
                        pool_ids: abnormal.iter().map(|pool| pool.pool_id.clone()).collect(),
                        reasons,
                    });
                }
            }
        }

        let mut anchor_active = active_by_pair
            .values()
            .flatten()
            .copied()
            .collect::<Vec<_>>();
        anchor_active.sort_by(|left, right| {
            pool_score(right)
                .partial_cmp(&pool_score(left))
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| left.pool_id.cmp(&right.pool_id))
        });
        for (index, pool) in anchor_active.into_iter().enumerate() {
            desired
                .pools
                .push(intermediate_pool_row(anchor_token_key_id, pool, index + 1));
            desired
                .liquidity_snapshots
                .push(liquidity_snapshot_row(anchor_token_key_id, pool));
        }
        let mut anchor_abnormal = abnormal_by_pair
            .values()
            .flatten()
            .copied()
            .collect::<Vec<_>>();
        anchor_abnormal.sort_by(|left, right| left.pool_id.cmp(&right.pool_id));
        for pool in anchor_abnormal {
            desired
                .abnormal_pools
                .push(abnormal_intermediate_pool_row(anchor_token_key_id, pool));
            desired
                .liquidity_snapshots
                .push(liquidity_snapshot_row(anchor_token_key_id, pool));
        }
    }
    desired.yz_pairs.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.direction_key)
            .cmp(&(&right.anchor_token_key_id, &right.direction_key))
    });
    desired.pools.sort_by(|left, right| {
        (
            &left.anchor_token_key_id,
            left.selection_rank,
            &left.pool_id,
        )
            .cmp(&(
                &right.anchor_token_key_id,
                right.selection_rank,
                &right.pool_id,
            ))
    });
    desired.abnormal_yz_pairs.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.direction_key)
            .cmp(&(&right.anchor_token_key_id, &right.direction_key))
    });
    desired.abnormal_pools.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.pool_id)
            .cmp(&(&right.anchor_token_key_id, &right.pool_id))
    });
    desired.liquidity_snapshots.sort_by(|left, right| {
        (&left.anchor_token_key_id, &left.pool_id)
            .cmp(&(&right.anchor_token_key_id, &right.pool_id))
    });
    Ok(desired)
}

fn canonical_pair_key(left: &str, right: &str) -> String {
    if left <= right {
        format!("{left}:{right}")
    } else {
        format!("{right}:{left}")
    }
}

fn pool_score(pool: &ScopePool) -> f64 {
    pool.liquidity_index
        .filter(|value| value.is_finite() && *value > 0.0)
        .unwrap_or(0.0)
}

fn abnormal_reasons(pool: &ScopePool) -> Vec<String> {
    let mut reasons = BTreeSet::new();
    if matches!(pool.pool_status.as_str(), "stale") {
        reasons.insert("stale_pool".to_owned());
    }
    if matches!(pool.pool_status.as_str(), "identity_mismatch") {
        reasons.insert("pool_identity_mismatch".to_owned());
    }
    if matches!(pool.pool_status.as_str(), "blocked")
        || pool.hooks != "0x0000000000000000000000000000000000000000"
    {
        reasons.insert("non_zero_hook".to_owned());
    }
    if pool.fee <= 0 || pool.fee > 3_000 {
        reasons.insert("fee_not_fixed_candidate_range".to_owned());
    }
    if pool
        .liquidity_index
        .is_none_or(|value| !value.is_finite() || value <= 0.0)
    {
        reasons.insert("liquidity_index_not_positive".to_owned());
    }
    if reasons.is_empty() {
        reasons.insert(format!("pool_status:{}", pool.pool_status));
    }
    reasons.into_iter().collect()
}

fn intermediate_pool_row(
    anchor_token_key_id: &str,
    pool: &ScopePool,
    selection_rank: usize,
) -> IntermediatePoolRow {
    IntermediatePoolRow {
        anchor_token_key_id: anchor_token_key_id.to_owned(),
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
        selection_rank,
    }
}

fn abnormal_intermediate_pool_row(
    anchor_token_key_id: &str,
    pool: &ScopePool,
) -> AbnormalIntermediatePoolRow {
    AbnormalIntermediatePoolRow {
        anchor_token_key_id: anchor_token_key_id.to_owned(),
        pool_id: pool.pool_id.clone(),
        pool_status: pool.pool_status.clone(),
        pool_key_json: pool.pool_key_json.clone(),
        token0_key_id: pool.token0_key_id.clone(),
        token1_key_id: pool.token1_key_id.clone(),
        fee: pool.fee,
        tick_spacing: pool.tick_spacing,
        hooks: pool.hooks.clone(),
        reasons: abnormal_reasons(pool),
    }
}

fn liquidity_snapshot_row(anchor_token_key_id: &str, pool: &ScopePool) -> LiquiditySnapshotRow {
    LiquiditySnapshotRow {
        anchor_token_key_id: anchor_token_key_id.to_owned(),
        pool_id: pool.pool_id.clone(),
        has_liquidity: Some(pool.pool_status == "active"),
        liquidity: pool.liquidity.clone(),
        liquidity_index: pool.liquidity_index,
        sqrt_price_x96: pool.sqrt_price_x96.clone(),
        price: pool.price,
        tick: pool.tick,
        depth_status: pool.depth_status.clone(),
        liquidity_read_at: pool.liquidity_read_at.clone(),
        liquidity_block_number: pool.liquidity_block_number,
        evidence_source: pool.evidence_source.clone(),
        observed_at: pool
            .liquidity_read_at
            .clone()
            .unwrap_or_else(|| "source_timestamp_unavailable".to_owned()),
    }
}

fn exchange_checksum(
    input: &ExchangeInput,
    desired: &DesiredExchangeData,
) -> Result<String, CandidateExchangeError> {
    let payload = ExchangeChecksumPayload {
        candidate_edge_version: input.candidate_edge.candidate_version,
        candidate_edge_checksum: input
            .candidate_edge
            .materialized_checksum
            .as_deref()
            .ok_or(CandidateExchangeError::MissingCandidateEdgeState)?,
        pool_scope_checksum: input
            .pool_scope
            .materialized_checksum
            .as_deref()
            .ok_or(CandidateExchangeError::MissingPoolScope)?,
        library_versions: &input.pool_scope.library_versions,
        library_checksums: &input.pool_scope.library_checksums,
        yz_pairs: &desired.yz_pairs,
        pools: &desired.pools,
        abnormal_yz_pairs: &desired.abnormal_yz_pairs,
        abnormal_pools: &desired.abnormal_pools,
        liquidity_snapshots: &desired.liquidity_snapshots,
    };
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&payload)?))
    ))
}

fn open_connection(path: &Path, writable: bool) -> Result<Connection, CandidateExchangeError> {
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
) -> Result<CandidateExchangeStatus, CandidateExchangeError> {
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
            params![network, chain_id, NETWORK_PROTOCOL, EXCHANGE_SCOPE_KEY],
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
        return Ok(disabled_status(network, chain_id));
    };
    Ok(CandidateExchangeStatus {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: EXCHANGE_SCOPE_KEY.to_owned(),
        observation_version,
        materialized_checksum: Some(materialized_checksum),
        candidate_edge_version: Some(candidate_edge_version),
        candidate_edge_checksum: Some(candidate_edge_checksum),
        pool_scope_checksum: Some(pool_scope_checksum),
        library_versions: serde_json::from_str(&library_versions_json).unwrap_or_default(),
        library_checksums: serde_json::from_str(&library_checksums_json).unwrap_or_default(),
        anchor_count: usize_from_i64(anchor_count),
        token_count: usize_from_i64(token_count),
        direction_count: usize_from_i64(direction_count),
        empty_direction_count: usize_from_i64(empty_direction_count),
        pool_count: usize_from_i64(pool_count),
        abnormal_direction_count: usize_from_i64(abnormal_direction_count),
        abnormal_pool_count: usize_from_i64(abnormal_pool_count),
    })
}

pub fn read_candidate_exchange_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<CandidateExchangeStatus, CandidateExchangeError> {
    let connection = open_connection(database_path.as_ref(), false)?;
    read_status_on_connection(&connection, network, chain_id)
}

pub fn read_candidate_exchange_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<CandidateExchangeSnapshot, CandidateExchangeError> {
    if chain_id <= 0 {
        return Err(CandidateExchangeError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if limit == 0 || limit > MAX_SNAPSHOT_LIMIT {
        return Err(CandidateExchangeError::InvalidRequest(format!(
            "limit must be between 1 and {MAX_SNAPSHOT_LIMIT}"
        )));
    }
    if anchor_token_key_id.is_some_and(str::is_empty) {
        return Err(CandidateExchangeError::InvalidRequest(
            "anchor_token_key_id must not be empty".to_owned(),
        ));
    }
    let connection = open_connection(database_path.as_ref(), false)?;
    let status = read_status_on_connection(&connection, network, chain_id)?;
    Ok(CandidateExchangeSnapshot {
        status,
        tokens: read_exchange_tokens(&connection, network, chain_id, anchor_token_key_id, limit)?,
        yz_pairs: read_yz_pairs(&connection, network, chain_id, anchor_token_key_id, limit)?,
        pools: read_intermediate_pools(&connection, network, chain_id, anchor_token_key_id, limit)?,
        abnormal_yz_pairs: read_abnormal_yz_pairs(
            &connection,
            network,
            chain_id,
            anchor_token_key_id,
            limit,
        )?,
        abnormal_pools: read_abnormal_intermediate_pools(
            &connection,
            network,
            chain_id,
            anchor_token_key_id,
            limit,
        )?,
        liquidity_snapshots: read_liquidity_snapshots(
            &connection,
            network,
            chain_id,
            anchor_token_key_id,
            limit,
        )?,
    })
}

fn read_exchange_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<CandidateExchangeTokenRow>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT DISTINCT catalog.token_key_id, catalog.v4_currency_key,
                catalog.token_symbol, catalog.token_name, catalog.token_decimals,
                catalog.library_key, catalog.admission_status
         FROM token_library_catalog catalog
         WHERE catalog.network = ?1 AND catalog.chain_id = ?2
           AND (
             EXISTS (
               SELECT 1 FROM manager_market_observation_yz_pairs yz
               WHERE yz.network = catalog.network
                 AND yz.chain_id = catalog.chain_id
                 AND yz.protocol = ?3
                 AND yz.scope_key = ?4
                 AND (?5 IS NULL OR yz.anchor_token_key_id = ?5)
                 AND (yz.anchor_token_key_id = catalog.token_key_id
                      OR yz.token_x_key_id = catalog.token_key_id
                      OR yz.token_y_key_id = catalog.token_key_id)
             )
             OR EXISTS (
               SELECT 1 FROM manager_market_observation_abnormal_yz_pairs abnormal_yz
               WHERE abnormal_yz.network = catalog.network
                 AND abnormal_yz.chain_id = catalog.chain_id
                 AND abnormal_yz.protocol = ?3
                 AND abnormal_yz.scope_key = ?4
                 AND (?5 IS NULL OR abnormal_yz.anchor_token_key_id = ?5)
                 AND (abnormal_yz.anchor_token_key_id = catalog.token_key_id
                      OR abnormal_yz.token_x_key_id = catalog.token_key_id
                      OR abnormal_yz.token_y_key_id = catalog.token_key_id)
             )
             OR EXISTS (
               SELECT 1 FROM manager_market_observation_intermediate_pools pools
               WHERE pools.network = catalog.network
                 AND pools.chain_id = catalog.chain_id
                 AND pools.protocol = ?3
                 AND pools.scope_key = ?4
                 AND (?5 IS NULL OR pools.anchor_token_key_id = ?5)
                 AND (pools.anchor_token_key_id = catalog.token_key_id
                      OR pools.token0_key_id = catalog.token_key_id
                      OR pools.token1_key_id = catalog.token_key_id)
             )
             OR EXISTS (
               SELECT 1 FROM manager_market_observation_abnormal_intermediate_pools abnormal_pools
               WHERE abnormal_pools.network = catalog.network
                 AND abnormal_pools.chain_id = catalog.chain_id
                 AND abnormal_pools.protocol = ?3
                 AND abnormal_pools.scope_key = ?4
                 AND (?5 IS NULL OR abnormal_pools.anchor_token_key_id = ?5)
                 AND (abnormal_pools.anchor_token_key_id = catalog.token_key_id
                      OR abnormal_pools.token0_key_id = catalog.token_key_id
                      OR abnormal_pools.token1_key_id = catalog.token_key_id)
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
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            EXCHANGE_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(CandidateExchangeTokenRow {
                token_key_id: row.get(0)?,
                token_address: row.get(1)?,
                token_symbol: row.get(2)?,
                token_name: row.get(3)?,
                token_decimals: row.get(4)?,
                token_library_key: row.get(5)?,
                admission_status: row.get(6)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

pub fn compare_candidate_exchanges(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    legacy_anchor_scope: &str,
    sample_limit: usize,
) -> Result<CandidateExchangeComparison, CandidateExchangeError> {
    if chain_id <= 0 {
        return Err(CandidateExchangeError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if legacy_anchor_scope.trim().is_empty() {
        return Err(CandidateExchangeError::InvalidRequest(
            "legacy_anchor_scope must not be empty".to_owned(),
        ));
    }
    if sample_limit == 0 {
        return Err(CandidateExchangeError::InvalidRequest(
            "sample_limit must be greater than zero".to_owned(),
        ));
    }
    let database_path = database_path.as_ref();
    let connection = open_connection(database_path, false)?;
    let status = read_status_on_connection(&connection, network, chain_id)?;
    if !status.enabled {
        return Ok(disabled_comparison(network, chain_id, legacy_anchor_scope));
    }
    let anchor_symbols = read_anchor_symbols(&connection, network, chain_id)?;
    let manager_yz_rows = read_yz_pairs(&connection, network, chain_id, None, MAX_COMPARISON_ROWS)?;
    let manager_pool_rows =
        read_intermediate_pools(&connection, network, chain_id, None, MAX_COMPARISON_ROWS)?;
    let manager_abnormal_yz_rows =
        read_abnormal_yz_pairs(&connection, network, chain_id, None, MAX_COMPARISON_ROWS)?;
    let manager_abnormal_pool_rows = read_abnormal_intermediate_pools(
        &connection,
        network,
        chain_id,
        None,
        MAX_COMPARISON_ROWS,
    )?;
    let manager_yz = manager_yz_comparison_rows(&manager_yz_rows, &anchor_symbols)?;
    let manager_pools = manager_pool_comparison_rows(&manager_pool_rows, &anchor_symbols)?;
    let manager_abnormal_yz =
        manager_abnormal_yz_comparison_rows(&manager_abnormal_yz_rows, &anchor_symbols)?;
    let manager_abnormal_pools =
        manager_abnormal_pool_comparison_rows(&manager_abnormal_pool_rows, &anchor_symbols)?;

    let legacy_yz = read_legacy_yz_rows(&connection, network, chain_id, legacy_anchor_scope)?;
    let legacy_pools = read_legacy_pool_rows(&connection, network, chain_id, legacy_anchor_scope)?;
    let legacy_abnormal_yz =
        read_legacy_abnormal_yz_rows(&connection, network, chain_id, legacy_anchor_scope)?;
    let legacy_abnormal_pools =
        read_legacy_abnormal_pool_rows(&connection, network, chain_id, legacy_anchor_scope)?;

    let mut differences = Vec::new();
    let mut cause_counts = BTreeMap::new();
    let yz_pairs = compare_rows(
        "yzPairs",
        &manager_yz,
        &legacy_yz,
        false,
        &mut differences,
        &mut cause_counts,
    );
    let pools = compare_rows(
        "pools",
        &manager_pools,
        &legacy_pools,
        true,
        &mut differences,
        &mut cause_counts,
    );
    let abnormal_yz_pairs = compare_rows(
        "abnormalYzPairs",
        &manager_abnormal_yz,
        &legacy_abnormal_yz,
        false,
        &mut differences,
        &mut cause_counts,
    );
    let abnormal_pools = compare_rows(
        "abnormalPools",
        &manager_abnormal_pools,
        &legacy_abnormal_pools,
        false,
        &mut differences,
        &mut cause_counts,
    );
    let ready_for_cutover = yz_pairs.manager_only_count == 0
        && yz_pairs.legacy_only_count == 0
        && yz_pairs.field_mismatch_count == 0
        && pools.legacy_only_count == 0
        && pools.field_mismatch_count == 0
        && abnormal_yz_pairs.manager_only_count == 0
        && abnormal_yz_pairs.legacy_only_count == 0
        && abnormal_yz_pairs.field_mismatch_count == 0
        && abnormal_pools.manager_only_count == 0
        && abnormal_pools.legacy_only_count == 0
        && abnormal_pools.field_mismatch_count == 0;
    let differences_truncated = differences.len() > sample_limit;
    differences.truncate(sample_limit);
    Ok(CandidateExchangeComparison {
        enabled: true,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: EXCHANGE_SCOPE_KEY.to_owned(),
        legacy_anchor_scope: legacy_anchor_scope.to_owned(),
        manager_checksum: status.materialized_checksum,
        ready_for_cutover,
        yz_pairs,
        pools,
        abnormal_yz_pairs,
        abnormal_pools,
        cause_counts,
        differences,
        differences_truncated,
    })
}

fn disabled_comparison(
    network: &str,
    chain_id: i64,
    legacy_anchor_scope: &str,
) -> CandidateExchangeComparison {
    let empty = || ExchangeComparisonSummary {
        manager_row_count: 0,
        legacy_row_count: 0,
        manager_only_count: 0,
        legacy_only_count: 0,
        field_mismatch_count: 0,
        manager_checksum: checksum_text("[]"),
        legacy_checksum: checksum_text("[]"),
    };
    CandidateExchangeComparison {
        enabled: false,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: EXCHANGE_SCOPE_KEY.to_owned(),
        legacy_anchor_scope: legacy_anchor_scope.to_owned(),
        manager_checksum: None,
        ready_for_cutover: false,
        yz_pairs: empty(),
        pools: empty(),
        abnormal_yz_pairs: empty(),
        abnormal_pools: empty(),
        cause_counts: BTreeMap::new(),
        differences: Vec::new(),
        differences_truncated: false,
    }
}

fn read_anchor_symbols(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT DISTINCT anchor_token_key_id, token_symbol
         FROM manager_dex_candidate_anchor_tokens
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND anchor_token_key_id = token_key_id
         ORDER BY anchor_token_key_id, token_symbol",
    )?;
    let rows = statement.query_map(
        params![network, chain_id, NETWORK_PROTOCOL, CANDIDATE_SCOPE_KEY],
        |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
    )?;
    let mut symbols = BTreeMap::new();
    for row in rows {
        let (anchor, symbol) = row?;
        symbols.insert(anchor, symbol);
    }
    Ok(symbols)
}

fn manager_yz_comparison_rows(
    rows: &[YzPairRow],
    anchor_symbols: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut values = BTreeMap::new();
    for row in rows {
        let anchor = comparison_anchor_symbol(anchor_symbols, &row.anchor_token_key_id)?;
        let token_x = address_from_token_key_id(&row.token_x_key_id)?;
        let token_y = address_from_token_key_id(&row.token_y_key_id)?;
        values.insert(
            format!("{anchor}|{token_x}:{token_y}"),
            serde_json::to_string(&serde_json::json!({
                "hasDirectSwapPool": row.has_direct_swap_pool,
                "directSwapPoolCount": row.direct_swap_pool_count,
                "poolIds": row.pool_ids,
                "optimalPoolId": row.optimal_pool_id,
                "observationEmptyReason": row.observation_empty_reason
            }))?,
        );
    }
    Ok(values)
}

fn manager_pool_comparison_rows(
    rows: &[IntermediatePoolRow],
    anchor_symbols: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut values = BTreeMap::new();
    for row in rows {
        let anchor = comparison_anchor_symbol(anchor_symbols, &row.anchor_token_key_id)?;
        values.insert(
            format!("{anchor}|{}", row.pool_id),
            serde_json::to_string(&serde_json::json!({
                "poolManager": row.pool_manager,
                "poolKeyJson": row.pool_key_json,
                "token0KeyId": row.token0_key_id,
                "token1KeyId": row.token1_key_id,
                "fee": row.fee,
                "tickSpacing": row.tick_spacing,
                "hooks": row.hooks
            }))?,
        );
    }
    Ok(values)
}

fn manager_abnormal_yz_comparison_rows(
    rows: &[AbnormalYzPairRow],
    anchor_symbols: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut values = BTreeMap::new();
    for row in rows {
        let anchor = comparison_anchor_symbol(anchor_symbols, &row.anchor_token_key_id)?;
        let token_x = address_from_token_key_id(&row.token_x_key_id)?;
        let token_y = address_from_token_key_id(&row.token_y_key_id)?;
        values.insert(
            format!("{anchor}|{token_x}:{token_y}"),
            serde_json::to_string(&serde_json::json!({
                "abnormalPoolCount": row.abnormal_pool_count,
                "poolIds": row.pool_ids
            }))?,
        );
    }
    Ok(values)
}

fn manager_abnormal_pool_comparison_rows(
    rows: &[AbnormalIntermediatePoolRow],
    anchor_symbols: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut values = BTreeMap::new();
    for row in rows {
        let anchor = comparison_anchor_symbol(anchor_symbols, &row.anchor_token_key_id)?;
        values.insert(
            format!("{anchor}|{}", row.pool_id),
            serde_json::to_string(&serde_json::json!({
                "poolKeyJson": row.pool_key_json,
                "token0KeyId": row.token0_key_id,
                "token1KeyId": row.token1_key_id,
                "fee": row.fee,
                "tickSpacing": row.tick_spacing,
                "hooks": row.hooks
            }))?,
        );
    }
    Ok(values)
}

fn comparison_anchor_symbol<'a>(
    symbols: &'a BTreeMap<String, String>,
    anchor_token_key_id: &str,
) -> Result<&'a str, CandidateExchangeError> {
    symbols
        .get(anchor_token_key_id)
        .map(String::as_str)
        .ok_or_else(|| {
            CandidateExchangeError::InvalidStoredPool(format!(
                "missing display symbol for anchor {anchor_token_key_id}"
            ))
        })
}

fn address_from_token_key_id(token_key_id: &str) -> Result<&str, CandidateExchangeError> {
    token_key_id
        .rsplit_once(':')
        .map(|(_, address)| address)
        .ok_or_else(|| {
            CandidateExchangeError::InvalidStoredPool(format!(
                "token_key_id {token_key_id} does not contain an address suffix"
            ))
        })
}

fn read_legacy_yz_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_scope: &str,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut statement = connection
        .prepare(
            "SELECT anchor_symbol, direction_key, has_direct_swap_pool,
                    direct_swap_pool_count, pool_ids_json, optimal_pool_id,
                    observation_empty_reason
             FROM market_observation_yz_pairs
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND anchor_scope = ?4
             ORDER BY anchor_symbol, direction_key
             LIMIT ?5",
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_yz_pairs".to_owned(),
            source,
        })?;
    let rows = statement
        .query_map(
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                anchor_scope,
                MAX_COMPARISON_ROWS as i64
            ],
            |row| {
                Ok((
                    format!("{}|{}", row.get::<_, String>(0)?, row.get::<_, String>(1)?),
                    serde_json::json!({
                        "hasDirectSwapPool": row.get::<_, i64>(2)? != 0,
                        "directSwapPoolCount": row.get::<_, i64>(3)?,
                        "poolIds": parse_json_array(row.get::<_, String>(4)?),
                        "optimalPoolId": row.get::<_, Option<String>>(5)?,
                        "observationEmptyReason": row.get::<_, Option<String>>(6)?
                    })
                    .to_string(),
                ))
            },
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_yz_pairs".to_owned(),
            source,
        })?;
    rows.collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_yz_pairs".to_owned(),
            source,
        })
}

fn read_legacy_pool_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_scope: &str,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut statement = connection
        .prepare(
            "SELECT anchor_symbol, pool_id, pool_manager, pool_key_json,
                    token0, token1, fee, tick_spacing, hooks
             FROM market_observation_intermediate_pools
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND anchor_scope = ?4
             ORDER BY anchor_symbol, pool_id
             LIMIT ?5",
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_pools".to_owned(),
            source,
        })?;
    let rows = statement
        .query_map(
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                anchor_scope,
                MAX_COMPARISON_ROWS as i64
            ],
            |row| {
                let token0: String = row.get(4)?;
                let token1: String = row.get(5)?;
                Ok((
                    format!("{}|{}", row.get::<_, String>(0)?, row.get::<_, String>(1)?),
                    serde_json::json!({
                        "poolManager": row.get::<_, Option<String>>(2)?,
                        "poolKeyJson": row.get::<_, Option<String>>(3)?,
                        "token0KeyId": format!("{network}:{chain_id}:{token0}"),
                        "token1KeyId": format!("{network}:{chain_id}:{token1}"),
                        "fee": row.get::<_, Option<i64>>(6)?,
                        "tickSpacing": row.get::<_, Option<i64>>(7)?,
                        "hooks": row.get::<_, Option<String>>(8)?
                    })
                    .to_string(),
                ))
            },
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_pools".to_owned(),
            source,
        })?;
    rows.collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_pools".to_owned(),
            source,
        })
}

fn read_legacy_abnormal_yz_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_scope: &str,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut statement = connection
        .prepare(
            "SELECT anchor_symbol, direction_key, non_normal_swap_pool_count,
                    pool_ids_json
             FROM market_observation_abnormal_yz_pairs
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND anchor_scope = ?4
             ORDER BY anchor_symbol, direction_key
             LIMIT ?5",
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_abnormal_yz_pairs".to_owned(),
            source,
        })?;
    let rows = statement
        .query_map(
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                anchor_scope,
                MAX_COMPARISON_ROWS as i64
            ],
            |row| {
                Ok((
                    format!("{}|{}", row.get::<_, String>(0)?, row.get::<_, String>(1)?),
                    serde_json::json!({
                        "abnormalPoolCount": row.get::<_, i64>(2)?,
                        "poolIds": parse_json_array(row.get::<_, String>(3)?)
                    })
                    .to_string(),
                ))
            },
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_abnormal_yz_pairs".to_owned(),
            source,
        })?;
    rows.collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_abnormal_yz_pairs".to_owned(),
            source,
        })
}

fn read_legacy_abnormal_pool_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_scope: &str,
) -> Result<BTreeMap<String, String>, CandidateExchangeError> {
    let mut statement = connection
        .prepare(
            "SELECT anchor_symbol, pool_id, pool_manager, pool_key_json,
                    token0, token1, fee, tick_spacing, hooks
             FROM market_observation_abnormal_intermediate_pools
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND anchor_scope = ?4
             ORDER BY anchor_symbol, pool_id
             LIMIT ?5",
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_abnormal_pools".to_owned(),
            source,
        })?;
    let rows = statement
        .query_map(
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                anchor_scope,
                MAX_COMPARISON_ROWS as i64
            ],
            |row| {
                let token0: String = row.get(4)?;
                let token1: String = row.get(5)?;
                Ok((
                    format!("{}|{}", row.get::<_, String>(0)?, row.get::<_, String>(1)?),
                    serde_json::json!({
                        "poolKeyJson": row.get::<_, Option<String>>(3)?,
                        "token0KeyId": format!("{network}:{chain_id}:{token0}"),
                        "token1KeyId": format!("{network}:{chain_id}:{token1}"),
                        "fee": row.get::<_, Option<i64>>(6)?,
                        "tickSpacing": row.get::<_, Option<i64>>(7)?,
                        "hooks": row.get::<_, Option<String>>(8)?
                    })
                    .to_string(),
                ))
            },
        )
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_abnormal_pools".to_owned(),
            source,
        })?;
    rows.collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(|source| CandidateExchangeError::ComparisonRead {
            stage: "legacy_abnormal_pools".to_owned(),
            source,
        })
}

fn parse_json_array(value: String) -> serde_json::Value {
    serde_json::from_str(&value).unwrap_or_else(|_| serde_json::Value::Array(Vec::new()))
}

fn compare_rows(
    category: &str,
    manager: &BTreeMap<String, String>,
    legacy: &BTreeMap<String, String>,
    allow_manager_pool_expansion: bool,
    differences: &mut Vec<ExchangeComparisonDifference>,
    cause_counts: &mut BTreeMap<String, usize>,
) -> ExchangeComparisonSummary {
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
                    if allow_manager_pool_expansion {
                        "legacyUsdDepthGateRemoved"
                    } else {
                        "managerScopeExpansion"
                    },
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
    ExchangeComparisonSummary {
        manager_row_count: manager.len(),
        legacy_row_count: legacy.len(),
        manager_only_count,
        legacy_only_count,
        field_mismatch_count,
        manager_checksum: checksum_map(manager),
        legacy_checksum: checksum_map(legacy),
    }
}

#[allow(clippy::too_many_arguments)]
fn push_comparison_difference(
    differences: &mut Vec<ExchangeComparisonDifference>,
    cause_counts: &mut BTreeMap<String, usize>,
    category: &str,
    key: &str,
    kind: &str,
    cause: &str,
    manager_payload: Option<String>,
    legacy_payload: Option<String>,
) {
    *cause_counts.entry(cause.to_owned()).or_insert(0) += 1;
    differences.push(ExchangeComparisonDifference {
        category: category.to_owned(),
        key: key.to_owned(),
        kind: kind.to_owned(),
        cause: cause.to_owned(),
        manager_payload,
        legacy_payload,
    });
}

fn checksum_map(values: &BTreeMap<String, String>) -> String {
    checksum_text(&serde_json::to_string(values).unwrap_or_else(|_| "{}".to_owned()))
}

fn checksum_text(value: &str) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(value.as_bytes())))
}

fn disabled_status(network: &str, chain_id: i64) -> CandidateExchangeStatus {
    CandidateExchangeStatus {
        enabled: false,
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: EXCHANGE_SCOPE_KEY.to_owned(),
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

fn read_yz_pairs(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<YzPairRow>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, direction_key, token_x_key_id,
                token_y_key_id, has_direct_swap_pool, direct_swap_pool_count,
                pool_ids_json, optimal_pool_id, observation_empty_reason
         FROM manager_market_observation_yz_pairs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, direction_key
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            EXCHANGE_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(YzPairRow {
                anchor_token_key_id: row.get(0)?,
                direction_key: row.get(1)?,
                token_x_key_id: row.get(2)?,
                token_y_key_id: row.get(3)?,
                has_direct_swap_pool: row.get::<_, i64>(4)? != 0,
                direct_swap_pool_count: usize_from_i64(row.get(5)?),
                pool_ids: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default(),
                optimal_pool_id: row.get(7)?,
                observation_empty_reason: row.get(8)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_intermediate_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<IntermediatePoolRow>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, pool_id, pool_manager, pool_key_json,
                token0, token1, token0_key_id, token1_key_id, fee,
                tick_spacing, hooks, sqrt_price_x96, tick, liquidity,
                liquidity_index, price, depth_status, liquidity_read_at,
                liquidity_block_number, evidence_source, selection_rank
         FROM manager_market_observation_intermediate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, selection_rank, pool_id
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            EXCHANGE_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(IntermediatePoolRow {
                anchor_token_key_id: row.get(0)?,
                pool_id: row.get(1)?,
                pool_manager: row.get(2)?,
                pool_key_json: row.get(3)?,
                token0: row.get(4)?,
                token1: row.get(5)?,
                token0_key_id: row.get(6)?,
                token1_key_id: row.get(7)?,
                fee: row.get(8)?,
                tick_spacing: row.get(9)?,
                hooks: row.get(10)?,
                sqrt_price_x96: row.get(11)?,
                tick: row.get(12)?,
                liquidity: row.get(13)?,
                liquidity_index: row.get(14)?,
                price: row.get(15)?,
                depth_status: row.get(16)?,
                liquidity_read_at: row.get(17)?,
                liquidity_block_number: row.get(18)?,
                evidence_source: row.get(19)?,
                selection_rank: usize_from_i64(row.get(20)?),
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_abnormal_yz_pairs(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<AbnormalYzPairRow>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, direction_key, token_x_key_id,
                token_y_key_id, abnormal_pool_count, pool_ids_json, reasons_json
         FROM manager_market_observation_abnormal_yz_pairs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, direction_key
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            EXCHANGE_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(AbnormalYzPairRow {
                anchor_token_key_id: row.get(0)?,
                direction_key: row.get(1)?,
                token_x_key_id: row.get(2)?,
                token_y_key_id: row.get(3)?,
                abnormal_pool_count: usize_from_i64(row.get(4)?),
                pool_ids: serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_default(),
                reasons: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default(),
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_abnormal_intermediate_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<AbnormalIntermediatePoolRow>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, pool_id, pool_status, pool_key_json,
                token0_key_id, token1_key_id, fee, tick_spacing, hooks, reasons_json
         FROM manager_market_observation_abnormal_intermediate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, pool_id
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            EXCHANGE_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(AbnormalIntermediatePoolRow {
                anchor_token_key_id: row.get(0)?,
                pool_id: row.get(1)?,
                pool_status: row.get(2)?,
                pool_key_json: row.get(3)?,
                token0_key_id: row.get(4)?,
                token1_key_id: row.get(5)?,
                fee: row.get(6)?,
                tick_spacing: row.get(7)?,
                hooks: row.get(8)?,
                reasons: serde_json::from_str(&row.get::<_, String>(9)?).unwrap_or_default(),
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_liquidity_snapshots(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_token_key_id: Option<&str>,
    limit: usize,
) -> Result<Vec<LiquiditySnapshotRow>, CandidateExchangeError> {
    let mut statement = connection.prepare(
        "SELECT anchor_token_key_id, pool_id, has_liquidity, liquidity,
                liquidity_index, sqrt_price_x96, price, tick, depth_status,
                liquidity_read_at, liquidity_block_number, evidence_source, observed_at
         FROM manager_market_observation_liquidity_snapshots
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
           AND (?5 IS NULL OR anchor_token_key_id = ?5)
         ORDER BY anchor_token_key_id, pool_id
         LIMIT ?6",
    )?;
    let rows = statement.query_map(
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            EXCHANGE_SCOPE_KEY,
            anchor_token_key_id,
            limit as i64
        ],
        |row| {
            Ok(LiquiditySnapshotRow {
                anchor_token_key_id: row.get(0)?,
                pool_id: row.get(1)?,
                has_liquidity: row.get::<_, Option<i64>>(2)?.map(|value| value != 0),
                liquidity: row.get(3)?,
                liquidity_index: row.get(4)?,
                sqrt_price_x96: row.get(5)?,
                price: row.get(6)?,
                tick: row.get(7)?,
                depth_status: row.get(8)?,
                liquidity_read_at: row.get(9)?,
                liquidity_block_number: row.get(10)?,
                evidence_source: row.get(11)?,
                observed_at: row.get(12)?,
            })
        },
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn replace_exchange_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    desired: &DesiredExchangeData,
    result: &CandidateExchangeMaterializationResult,
) -> Result<(), CandidateExchangeError> {
    let now = chrono::Utc::now().to_rfc3339();
    for table in [
        "manager_market_observation_yz_pairs",
        "manager_market_observation_intermediate_pools",
        "manager_market_observation_abnormal_yz_pairs",
        "manager_market_observation_abnormal_intermediate_pools",
        "manager_market_observation_liquidity_snapshots",
    ] {
        connection.execute(
            &format!(
                "DELETE FROM {table}
                 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                   AND scope_key = ?4"
            ),
            params![network, chain_id, NETWORK_PROTOCOL, EXCHANGE_SCOPE_KEY],
        )?;
    }
    insert_state(connection, network, chain_id, result, &now)?;
    for row in &desired.yz_pairs {
        connection.execute(
            "INSERT INTO manager_market_observation_yz_pairs (
                 network, chain_id, protocol, scope_key, anchor_token_key_id,
                 direction_key, token_x_key_id, token_y_key_id,
                 has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
                 optimal_pool_id, observation_empty_reason, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?14)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                EXCHANGE_SCOPE_KEY,
                row.anchor_token_key_id,
                row.direction_key,
                row.token_x_key_id,
                row.token_y_key_id,
                i64::from(row.has_direct_swap_pool),
                row.direct_swap_pool_count as i64,
                serde_json::to_string(&row.pool_ids)?,
                row.optimal_pool_id,
                row.observation_empty_reason,
                now
            ],
        )?;
    }
    for row in &desired.pools {
        connection.execute(
            "INSERT INTO manager_market_observation_intermediate_pools (
                 network, chain_id, protocol, scope_key, anchor_token_key_id,
                 pool_id, pool_manager, pool_key_json, token0, token1,
                 token0_key_id, token1_key_id, fee, tick_spacing, hooks,
                 sqrt_price_x96, tick, liquidity, liquidity_index, price,
                 depth_status, liquidity_read_at, liquidity_block_number,
                 evidence_source, selection_rank, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23,
                       ?24, ?25, ?26, ?26)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                EXCHANGE_SCOPE_KEY,
                row.anchor_token_key_id,
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
                row.selection_rank as i64,
                now
            ],
        )?;
    }
    for row in &desired.abnormal_yz_pairs {
        connection.execute(
            "INSERT INTO manager_market_observation_abnormal_yz_pairs (
                 network, chain_id, protocol, scope_key, anchor_token_key_id,
                 direction_key, token_x_key_id, token_y_key_id,
                 abnormal_pool_count, pool_ids_json, reasons_json, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?12)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                EXCHANGE_SCOPE_KEY,
                row.anchor_token_key_id,
                row.direction_key,
                row.token_x_key_id,
                row.token_y_key_id,
                row.abnormal_pool_count as i64,
                serde_json::to_string(&row.pool_ids)?,
                serde_json::to_string(&row.reasons)?,
                now
            ],
        )?;
    }
    for row in &desired.abnormal_pools {
        connection.execute(
            "INSERT INTO manager_market_observation_abnormal_intermediate_pools (
                 network, chain_id, protocol, scope_key, anchor_token_key_id,
                 pool_id, pool_status, pool_key_json, token0_key_id,
                 token1_key_id, fee, tick_spacing, hooks, reasons_json,
                 created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?15)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                EXCHANGE_SCOPE_KEY,
                row.anchor_token_key_id,
                row.pool_id,
                row.pool_status,
                row.pool_key_json,
                row.token0_key_id,
                row.token1_key_id,
                row.fee,
                row.tick_spacing,
                row.hooks,
                serde_json::to_string(&row.reasons)?,
                now
            ],
        )?;
    }
    for row in &desired.liquidity_snapshots {
        connection.execute(
            "INSERT INTO manager_market_observation_liquidity_snapshots (
                 network, chain_id, protocol, scope_key, anchor_token_key_id,
                 pool_id, has_liquidity, liquidity, liquidity_index,
                 sqrt_price_x96, price, tick, depth_status, liquidity_read_at,
                 liquidity_block_number, evidence_source, observed_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, ?16, ?17, ?18)",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                EXCHANGE_SCOPE_KEY,
                row.anchor_token_key_id,
                row.pool_id,
                row.has_liquidity.map(i64::from),
                row.liquidity,
                row.liquidity_index,
                row.sqrt_price_x96,
                row.price,
                row.tick,
                row.depth_status,
                row.liquidity_read_at,
                row.liquidity_block_number,
                row.evidence_source,
                row.observed_at,
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
    result: &CandidateExchangeMaterializationResult,
    now: &str,
) -> Result<(), CandidateExchangeError> {
    connection.execute(
        "INSERT INTO manager_market_observation_library_state (
             network, chain_id, protocol, scope_key, observation_version,
             materialized_checksum, candidate_edge_version,
             candidate_edge_checksum, pool_scope_checksum,
             library_versions_json, library_checksums_json, anchor_count,
             token_count, direction_count, empty_direction_count, pool_count,
             abnormal_direction_count, abnormal_pool_count, generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13,
                   ?14, ?15, ?16, ?17, ?18, ?19, ?19)
         ON CONFLICT(network, chain_id, protocol, scope_key) DO UPDATE SET
             observation_version = excluded.observation_version,
             materialized_checksum = excluded.materialized_checksum,
             candidate_edge_version = excluded.candidate_edge_version,
             candidate_edge_checksum = excluded.candidate_edge_checksum,
             pool_scope_checksum = excluded.pool_scope_checksum,
             library_versions_json = excluded.library_versions_json,
             library_checksums_json = excluded.library_checksums_json,
             anchor_count = excluded.anchor_count,
             token_count = excluded.token_count,
             direction_count = excluded.direction_count,
             empty_direction_count = excluded.empty_direction_count,
             pool_count = excluded.pool_count,
             abnormal_direction_count = excluded.abnormal_direction_count,
             abnormal_pool_count = excluded.abnormal_pool_count,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            EXCHANGE_SCOPE_KEY,
            result.observation_version,
            result.materialized_checksum,
            result.candidate_edge_version,
            result.candidate_edge_checksum,
            result.pool_scope_checksum,
            serde_json::to_string(&result.library_versions)?,
            serde_json::to_string(&result.library_checksums)?,
            result.anchor_count as i64,
            result.token_count as i64,
            result.direction_count as i64,
            result.empty_direction_count as i64,
            result.pool_count as i64,
            result.abnormal_direction_count as i64,
            result.abnormal_pool_count as i64,
            now
        ],
    )?;
    Ok(())
}

fn find_write_request(
    connection: &Connection,
    request: &CandidateExchangeWriteRequest,
) -> Result<Option<String>, CandidateExchangeError> {
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
    request: &CandidateExchangeWriteRequest,
    request_digest: &str,
    result: &CandidateExchangeMaterializationResult,
) -> Result<(), CandidateExchangeError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply,
        "expectedUpstream": request.expected_upstream
    });
    let response_json = serde_json::to_string(&PersistedExchangeWrite {
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
        compare_candidate_exchanges, materialize_candidate_exchanges,
        materialize_candidate_exchanges_write, read_candidate_exchange_snapshot,
        CandidateExchangeError, CandidateExchangeWriteRequest, ExpectedUpstream,
    };
    use manager_contracts::V4PoolKey;
    use pool_manager::materialize_scope;
    use rusqlite::{params, Connection};
    use serde_json::json;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const ANCHOR: &str = "0x2222222222222222222222222222222222222222";
    const STOCK: &str = "0x4444444444444444444444444444444444444444";
    const EXT_A: &str = "0x5555555555555555555555555555555555555555";
    const EXT_B: &str = "0x6666666666666666666666666666666666666666";
    const ZERO_HOOK: &str = "0x0000000000000000000000000000000000000000";
    const HOOK: &str = "0x0000000000000000000000000000000000000001";
    const POOL_MANAGER: &str = "0x7777777777777777777777777777777777777777";

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = Connection::open(&path).unwrap();
        create_business_tables(&connection);
        seed_tokens(&connection);
        seed_raw_pools(&connection);
        drop(connection);
        materialize_scope(&path, NETWORK, CHAIN_ID, "candidate_exchange", true).unwrap();
        let connection = Connection::open(&path).unwrap();
        seed_candidate_edge_state(&connection);
        create_consumer_markers(&connection);
        (directory, path)
    }

    fn token_key_id(address: &str) -> String {
        format!("{NETWORK}:{CHAIN_ID}:{address}")
    }

    fn create_business_tables(connection: &Connection) {
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_manager TEXT,
                    pool_key_json TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    has_liquidity INTEGER,
                    liquidity_index REAL,
                    sqrt_price_x96 TEXT,
                    tick INTEGER,
                    liquidity TEXT,
                    price REAL,
                    depth_status TEXT,
                    liquidity_read_at TEXT,
                    liquidity_block_number INTEGER,
                    evidence_source TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                "#,
            )
            .unwrap();
    }

    fn seed_tokens(connection: &Connection) {
        for (address, symbol, table) in [
            (ANCHOR, "USDG", "token_core"),
            (STOCK, "AMZN", "token_stocks"),
            (EXT_A, "TA", "token_extension"),
            (EXT_B, "TB", "token_extension"),
        ] {
            let sql = format!(
                "INSERT INTO {table} (
                    network, chain_id, token_key_id, v4_currency_key,
                    token_address, token_symbol, token_name, token_decimals,
                    token_kind, is_native, is_wrapped_native, is_stable,
                    admission_status, admission_source, metadata_source,
                    metadata_checksum, source_json, first_seen_at,
                    first_admitted_at, updated_at, blocked_reason
                 ) VALUES (?1, ?2, ?3, ?4, ?4, ?5, ?5, 18, 'erc20', 0, 0, 0,
                           'admitted', 'fixture', 'fixture', 'sha256:fixture',
                           '{{}}', '2026-11-04T00:00:00Z',
                           '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z', NULL)"
            );
            connection
                .execute(
                    &sql,
                    params![NETWORK, CHAIN_ID, token_key_id(address), address, symbol],
                )
                .unwrap();
        }
        for (library_key, version, token_count, admitted_count) in [
            ("core", 5, 1, 1),
            ("stocks", 2, 1, 1),
            ("extension", 4, 2, 2),
            ("test", 1, 0, 0),
        ] {
            connection
                .execute(
                    "INSERT INTO token_library_state (
                        network, chain_id, library_key, library_version,
                        token_count, admitted_count, metadata_checksum,
                        source_checkpoint, generated_at, updated_at
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'fixture',
                               '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z')",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        library_key,
                        version,
                        token_count,
                        admitted_count,
                        format!("sha256:{library_key}-fixture")
                    ],
                )
                .unwrap();
        }
    }

    fn pool_key(token0: &str, token1: &str, fee: u32, tick_spacing: i32, hooks: &str) -> V4PoolKey {
        let (currency0, currency1) = if token0 < token1 {
            (token0.to_owned(), token1.to_owned())
        } else {
            (token1.to_owned(), token0.to_owned())
        };
        V4PoolKey {
            currency0,
            currency1,
            fee,
            tick_spacing,
            hooks: hooks.to_owned(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn insert_pool(
        connection: &Connection,
        token0: &str,
        token1: &str,
        fee: u32,
        tick_spacing: i32,
        hooks: &str,
        dynamic_fee: i64,
        has_liquidity: Option<i64>,
        liquidity_index: Option<f64>,
    ) -> String {
        let key = pool_key(token0, token1, fee, tick_spacing, hooks);
        let pool_id = key.pool_id().unwrap();
        let key_json = json!({
            "currency0": key.currency0,
            "currency1": key.currency1,
            "fee": key.fee,
            "tickSpacing": key.tick_spacing,
            "hooks": key.hooks,
            "initializeBlockNumber": 100,
            "initializeTransactionHash": format!("0x{}", "ab".repeat(32))
        });
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, pool_manager,
                    pool_key_json, token0, token1, fee, dynamic_fee,
                    special_fee, tick_spacing, hooks
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7, ?8, ?9,
                           0, ?10, ?11)",
                params![
                    NETWORK,
                    CHAIN_ID,
                    pool_id,
                    POOL_MANAGER,
                    key_json.to_string(),
                    key.currency0,
                    key.currency1,
                    i64::from(fee),
                    dynamic_fee,
                    i64::from(tick_spacing),
                    hooks
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, has_liquidity,
                    liquidity_index, sqrt_price_x96, liquidity, price,
                    depth_status, liquidity_read_at, liquidity_block_number,
                    evidence_source
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5,
                           '79228162514264337593543950336', '1000000', 1.0,
                           'unpriced', '2026-11-04T00:00:00Z', 100, 'fixture')",
                params![NETWORK, CHAIN_ID, pool_id, has_liquidity, liquidity_index],
            )
            .unwrap();
        pool_id
    }

    fn seed_raw_pools(connection: &Connection) {
        insert_pool(
            connection,
            ANCHOR,
            STOCK,
            500,
            10,
            ZERO_HOOK,
            0,
            Some(1),
            Some(10.0),
        );
        insert_pool(
            connection,
            STOCK,
            EXT_A,
            3_000,
            60,
            ZERO_HOOK,
            0,
            Some(1),
            Some(5.0),
        );
        insert_pool(
            connection,
            ANCHOR,
            EXT_B,
            3_000,
            60,
            ZERO_HOOK,
            1,
            Some(1),
            Some(3.0),
        );
        insert_pool(
            connection,
            EXT_A,
            EXT_B,
            500,
            10,
            ZERO_HOOK,
            0,
            Some(0),
            Some(0.0),
        );
        insert_pool(
            connection,
            ANCHOR,
            EXT_A,
            500,
            10,
            HOOK,
            0,
            Some(1),
            Some(4.0),
        );
    }

    fn seed_candidate_edge_state(connection: &Connection) {
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
                           'sha256:candidate-edge-fixture', 'sha256:core4-fixture',
                           '{}', '{}', '{}', '{}', 1, 4, 2, 4, 0,
                           '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        for (address, library_key) in [
            (ANCHOR, "core"),
            (STOCK, "stocks"),
            (EXT_A, "extension"),
            (EXT_B, "extension"),
        ] {
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
                        token_key_id(ANCHOR),
                        token_key_id(address),
                        library_key,
                        if address == ANCHOR { "USDG" } else { "TOKEN" }
                    ],
                )
                .unwrap();
        }
    }

    fn create_consumer_markers(connection: &Connection) {
        connection
            .execute_batch(
                "CREATE TABLE market_observation_yz_pairs (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    token_x_address TEXT NOT NULL,
                    token_y_address TEXT NOT NULL,
                    address_pair TEXT NOT NULL,
                    has_direct_swap_pool INTEGER NOT NULL,
                    direct_swap_pool_count INTEGER NOT NULL,
                    pool_ids_json TEXT NOT NULL,
                    optimal_pool_id TEXT,
                    observation_empty_reason TEXT,
                    marker TEXT
                 );
                 INSERT INTO market_observation_yz_pairs (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    direction_key, token_x_address, token_y_address, address_pair,
                    has_direct_swap_pool, direct_swap_pool_count, pool_ids_json, marker
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'legacy', 'USDG',
                           'legacy', '', '', '', 0, 0, '[]', 'legacy-only');
                 CREATE TABLE market_observation_intermediate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_manager TEXT,
                    pool_key_json TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    tick_spacing INTEGER,
                    hooks TEXT
                 );
                 CREATE TABLE market_observation_abnormal_yz_pairs (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    direction_key TEXT NOT NULL,
                    non_normal_swap_pool_count INTEGER NOT NULL,
                    pool_ids_json TEXT NOT NULL
                 );
                 CREATE TABLE market_observation_abnormal_intermediate_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_manager TEXT,
                    pool_key_json TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    tick_spacing INTEGER,
                    hooks TEXT
                 );
                 CREATE TABLE v4_closed_loops (
                    network TEXT, chain_id INTEGER, loop_key TEXT
                 );
                 CREATE TABLE cow_execution_attempts (
                    network TEXT, chain_id INTEGER, attempt_id TEXT
                 );",
            )
            .unwrap();
    }

    fn expected(path: &std::path::Path) -> ExpectedUpstream {
        let status = super::read_candidate_exchange_status(path, NETWORK, CHAIN_ID).unwrap();
        let candidate =
            candidate_edge_pool_manager::read_candidate_status(path, NETWORK, CHAIN_ID).unwrap();
        let scope =
            pool_manager::read_scope_status(path, NETWORK, CHAIN_ID, "candidate_exchange").unwrap();
        ExpectedUpstream {
            candidate_edge_version: Some(candidate.candidate_version),
            candidate_edge_checksum: candidate.materialized_checksum,
            pool_scope_checksum: scope.materialized_checksum,
            library_versions: scope.library_versions,
            library_checksums: scope.library_checksums,
        }
        .tap(|_| {
            assert_eq!(status.observation_version, 0);
        })
    }

    trait Tap: Sized {
        fn tap(self, callback: impl FnOnce(&Self)) -> Self {
            callback(&self);
            self
        }
    }

    impl<T> Tap for T {}

    fn seed_matching_legacy_rows(
        connection: &Connection,
        path: &std::path::Path,
        anchor_scope: &str,
    ) {
        let snapshot =
            read_candidate_exchange_snapshot(path, NETWORK, CHAIN_ID, None, 100).unwrap();
        for row in &snapshot.yz_pairs {
            let direction = row
                .direction_key
                .split_once("->")
                .map(|(left, right)| {
                    format!(
                        "{}:{}",
                        left.rsplit_once(':').unwrap().1,
                        right.rsplit_once(':').unwrap().1
                    )
                })
                .unwrap();
            connection
                .execute(
                    "INSERT INTO market_observation_yz_pairs (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        direction_key, token_x_address, token_y_address, address_pair,
                        has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
                        optimal_pool_id, observation_empty_reason
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, 'USDG', ?4,
                               '', '', '', ?5, ?6, ?7, ?8, ?9)",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        anchor_scope,
                        direction,
                        i64::from(row.has_direct_swap_pool),
                        row.direct_swap_pool_count as i64,
                        serde_json::to_string(&row.pool_ids).unwrap(),
                        row.optimal_pool_id,
                        row.observation_empty_reason
                    ],
                )
                .unwrap();
        }
        for (index, row) in snapshot.pools.iter().enumerate() {
            if index > 0 {
                continue;
            }
            connection
                .execute(
                    "INSERT INTO market_observation_intermediate_pools (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        pool_id, pool_manager, pool_key_json, token0, token1,
                        fee, tick_spacing, hooks
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, 'USDG', ?4, ?5, ?6,
                               ?7, ?8, ?9, ?10, ?11)",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        anchor_scope,
                        row.pool_id,
                        row.pool_manager,
                        row.pool_key_json,
                        row.token0,
                        row.token1,
                        row.fee,
                        row.tick_spacing,
                        row.hooks
                    ],
                )
                .unwrap();
        }
        for row in &snapshot.abnormal_yz_pairs {
            let direction = row
                .direction_key
                .split_once("->")
                .map(|(left, right)| {
                    format!(
                        "{}:{}",
                        left.rsplit_once(':').unwrap().1,
                        right.rsplit_once(':').unwrap().1
                    )
                })
                .unwrap();
            connection
                .execute(
                    "INSERT INTO market_observation_abnormal_yz_pairs (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        direction_key, non_normal_swap_pool_count, pool_ids_json
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, 'USDG', ?4, ?5, ?6)",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        anchor_scope,
                        direction,
                        row.abnormal_pool_count as i64,
                        serde_json::to_string(&row.pool_ids).unwrap()
                    ],
                )
                .unwrap();
        }
        for row in &snapshot.abnormal_pools {
            let (pool_manager, pool_key_json, token0, token1, fee, tick_spacing, hooks): (
                String,
                String,
                String,
                String,
                i64,
                i64,
                String,
            ) = connection
                .query_row(
                    "SELECT pool_manager, pool_key_json, token0, token1,
                            fee, tick_spacing, hooks
                     FROM v4_exchange_pools_candidate_exchange
                     WHERE network = ?1 AND chain_id = ?2 AND protocol = 'uniswap_v4'
                       AND pool_id = ?3",
                    params![NETWORK, CHAIN_ID, row.pool_id],
                    |row| {
                        Ok((
                            row.get(0)?,
                            row.get(1)?,
                            row.get(2)?,
                            row.get(3)?,
                            row.get(4)?,
                            row.get(5)?,
                            row.get(6)?,
                        ))
                    },
                )
                .unwrap();
            connection
                .execute(
                    "INSERT INTO market_observation_abnormal_intermediate_pools (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        pool_id, pool_manager, pool_key_json, token0, token1,
                        fee, tick_spacing, hooks
                     ) VALUES (?1, ?2, 'uniswap_v4', ?3, 'USDG', ?4, ?5, ?6,
                               ?7, ?8, ?9, ?10, ?11)",
                    params![
                        NETWORK,
                        CHAIN_ID,
                        anchor_scope,
                        row.pool_id,
                        pool_manager,
                        pool_key_json,
                        token0,
                        token1,
                        fee,
                        tick_spacing,
                        hooks
                    ],
                )
                .unwrap();
        }
    }

    #[test]
    fn rebuilds_all_candidate_exchange_directions_without_usd_depth_gate() {
        let (_directory, database) = database();
        let dry = materialize_candidate_exchanges(&database, NETWORK, CHAIN_ID, false).unwrap();
        assert_eq!(dry.anchor_count, 1);
        assert_eq!(dry.token_count, 4);
        assert_eq!(dry.direction_count, 12);
        assert_eq!(dry.pool_count, 2);
        assert_eq!(dry.abnormal_pool_count, 3);
        assert_eq!(dry.abnormal_direction_count, 6);
        assert_eq!(dry.empty_direction_count, 8);
        assert_eq!(dry.library_versions["extension"], 4);
        assert!(dry.consumers_need_rebuild);

        let connection = Connection::open(&database).unwrap();
        let before: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_market_observation_yz_pairs",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(before, 0);
        drop(connection);

        let applied = materialize_candidate_exchanges(&database, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(applied.materialized_checksum, dry.materialized_checksum);
        assert_eq!(applied.changed_rows, 28);
        let repeated = materialize_candidate_exchanges(&database, NETWORK, CHAIN_ID, true).unwrap();
        assert_eq!(repeated.changed_rows, 0);
        assert!(!repeated.consumers_need_rebuild);
        assert_eq!(repeated.observation_version, applied.observation_version);

        let snapshot = read_candidate_exchange_snapshot(
            &database,
            NETWORK,
            CHAIN_ID,
            Some(&token_key_id(ANCHOR)),
            100,
        )
        .unwrap();
        assert!(snapshot.status.enabled);
        assert_eq!(snapshot.tokens.len(), 4);
        assert!(snapshot
            .tokens
            .iter()
            .any(|row| row.token_symbol == "USDG" && row.token_address == ANCHOR));
        assert!(snapshot
            .tokens
            .iter()
            .any(|row| row.token_symbol == "AMZN" && row.token_address == STOCK));
        assert!(snapshot
            .tokens
            .iter()
            .any(|row| row.token_symbol == "TA" && row.token_address == EXT_A));
        assert!(snapshot
            .tokens
            .iter()
            .any(|row| row.token_symbol == "TB" && row.token_address == EXT_B));
        assert_eq!(snapshot.yz_pairs.len(), 12);
        assert_eq!(snapshot.pools.len(), 2);
        assert_eq!(snapshot.abnormal_yz_pairs.len(), 6);
        assert_eq!(snapshot.abnormal_pools.len(), 3);
        assert_eq!(snapshot.liquidity_snapshots.len(), 5);
        let non_core_yz = snapshot
            .yz_pairs
            .iter()
            .find(|row| {
                row.token_x_key_id == token_key_id(STOCK)
                    && row.token_y_key_id == token_key_id(EXT_A)
            })
            .unwrap();
        assert!(non_core_yz.has_direct_swap_pool);
        assert_eq!(non_core_yz.direct_swap_pool_count, 1);

        let connection = Connection::open(database).unwrap();
        let marker: String = connection
            .query_row(
                "SELECT marker FROM market_observation_yz_pairs",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(marker, "legacy-only");
        for table in ["v4_closed_loops", "cow_execution_attempts"] {
            let count: i64 = connection
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(count, 0);
        }
    }

    #[test]
    fn write_is_idempotent_and_dry_run_does_not_persist_idempotency() {
        let (_directory, database) = database();
        let upstream = expected(&database);
        let request = CandidateExchangeWriteRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "candidate-exchange.rebuild".to_owned(),
            request_id: "exchange-dry".to_owned(),
            idempotency_key: "exchange-idem".to_owned(),
            apply: false,
            expected_upstream: upstream.clone(),
        };
        let dry = materialize_candidate_exchanges_write(&database, request.clone()).unwrap();
        assert!(!dry.idempotent_replay);
        let connection = Connection::open(&database).unwrap();
        let request_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(request_count, 0);
        drop(connection);

        let apply_request = CandidateExchangeWriteRequest {
            request_id: "exchange-apply-1".to_owned(),
            apply: true,
            ..request
        };
        let first =
            materialize_candidate_exchanges_write(&database, apply_request.clone()).unwrap();
        assert!(!first.idempotent_replay);
        let replay = materialize_candidate_exchanges_write(
            &database,
            CandidateExchangeWriteRequest {
                request_id: "exchange-apply-2".to_owned(),
                ..apply_request
            },
        )
        .unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, first.result);
    }

    #[test]
    fn stale_candidate_edge_or_pool_scope_is_rejected_without_writes() {
        let (_directory, database) = database();
        let mut upstream = expected(&database);
        upstream.candidate_edge_checksum = Some("sha256:stale".to_owned());
        let error = materialize_candidate_exchanges_write(
            &database,
            CandidateExchangeWriteRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "candidate-exchange.rebuild".to_owned(),
                request_id: "exchange-stale".to_owned(),
                idempotency_key: "exchange-stale-idem".to_owned(),
                apply: true,
                expected_upstream: upstream,
            },
        )
        .unwrap_err();
        assert!(matches!(
            error,
            CandidateExchangeError::ExpectedUpstreamMismatch { .. }
        ));
        let connection = Connection::open(database).unwrap();
        let count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_market_observation_library_state",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn comparison_reports_usd_gate_expansion_and_is_read_only() {
        let (_directory, database) = database();
        materialize_candidate_exchanges(&database, NETWORK, CHAIN_ID, true).unwrap();
        let connection = Connection::open(&database).unwrap();
        seed_matching_legacy_rows(&connection, &database, "legacy-no-usd");
        drop(connection);
        let before = std::fs::read(&database).unwrap();
        let comparison =
            compare_candidate_exchanges(&database, NETWORK, CHAIN_ID, "legacy-no-usd", 100)
                .unwrap();
        let after = std::fs::read(&database).unwrap();
        assert_eq!(before, after);
        assert!(comparison.enabled);
        assert_eq!(comparison.yz_pairs.field_mismatch_count, 0);
        assert_eq!(comparison.pools.manager_only_count, 1);
        assert_eq!(comparison.cause_counts["legacyUsdDepthGateRemoved"], 1);
        assert!(comparison.ready_for_cutover);
        assert_eq!(comparison.differences.len(), 1);
        assert_eq!(comparison.differences[0].category, "pools");
        assert_eq!(comparison.differences[0].cause, "legacyUsdDepthGateRemoved");
    }
}
