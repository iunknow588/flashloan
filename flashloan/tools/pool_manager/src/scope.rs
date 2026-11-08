use manager_contracts::{normalize_address, V4PoolKey, V4_DYNAMIC_FEE_FLAG, V4_MAX_FEE};
use rusqlite::{params, params_from_iter, Connection, OpenFlags, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

const CORE4_SCOPE_KEY: &str = "core4";
const CANDIDATE_EXCHANGE_SCOPE_KEY: &str = "candidate_exchange";
const CORE4_POOL_TABLE: &str = "v4_exchange_pools_core4";
const CANDIDATE_EXCHANGE_POOL_TABLE: &str = "v4_exchange_pools_candidate_exchange";
const PROTOCOL: &str = "uniswap_v4";
const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";
const MAX_CANDIDATE_FIXED_FEE: i64 = 3_000;
const MAX_REQUEST_ID_LENGTH: usize = 128;
const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 256;
const ALL_TOKEN_LIBRARY_KEYS: &[&str] = &["core", "stocks", "extension", "test"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ScopeDefinition {
    key: &'static str,
    pool_table: &'static str,
    filter_mode: &'static str,
    library_keys: &'static [&'static str],
}

const CORE4_SCOPE: ScopeDefinition = ScopeDefinition {
    key: CORE4_SCOPE_KEY,
    pool_table: CORE4_POOL_TABLE,
    filter_mode: "edge_any",
    library_keys: &["core"],
};

const CANDIDATE_EXCHANGE_SCOPE: ScopeDefinition = ScopeDefinition {
    key: CANDIDATE_EXCHANGE_SCOPE_KEY,
    pool_table: CANDIDATE_EXCHANGE_POOL_TABLE,
    filter_mode: "both_end",
    library_keys: &["core", "stocks", "extension", "test"],
};

fn scope_definition(scope_key: &str) -> Option<ScopeDefinition> {
    match scope_key {
        CORE4_SCOPE_KEY => Some(CORE4_SCOPE),
        CANDIDATE_EXCHANGE_SCOPE_KEY => Some(CANDIDATE_EXCHANGE_SCOPE),
        _ => None,
    }
}

#[derive(Debug)]
pub enum ScopeError {
    Sqlite(rusqlite::Error),
    UnsupportedScope(String),
    MissingLibraryState(String),
    NoAdmittedAnchorTokens,
    InvalidRawPool(String),
    IdempotencyKeyReused {
        idempotency_key: String,
        existing_request_id: String,
        submitted_request_id: String,
    },
    Json(serde_json::Error),
}

impl fmt::Display for ScopeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "scope storage error: {error}"),
            Self::UnsupportedScope(scope) => write!(
                formatter,
                "scope {scope} is not implemented; expected core4 or candidate_exchange"
            ),
            Self::MissingLibraryState(library_key) => {
                write!(formatter, "{library_key} token library state is missing")
            }
            Self::NoAdmittedAnchorTokens => {
                formatter.write_str("scope has no admitted anchor tokens")
            }
            Self::InvalidRawPool(message) => write!(formatter, "invalid raw pool: {message}"),
            Self::IdempotencyKeyReused {
                idempotency_key,
                existing_request_id,
                submitted_request_id,
            } => write!(
                formatter,
                "Idempotency-Key {idempotency_key} was first used by request \
                 {existing_request_id} and cannot be reused by {submitted_request_id}"
            ),
            Self::Json(error) => write!(formatter, "scope json error: {error}"),
        }
    }
}

impl std::error::Error for ScopeError {}

impl From<rusqlite::Error> for ScopeError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<serde_json::Error> for ScopeError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScopeMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub scope_key: String,
    pub pool_table: String,
    pub filter_mode: String,
    pub library_keys: Vec<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub apply: bool,
    pub dry_run: bool,
    pub source_pool_count: usize,
    pub eligible_pool_count: usize,
    pub skipped_pool_count: usize,
    pub skip_reason_counts: BTreeMap<String, usize>,
    pub active_pool_count: usize,
    pub isolated_pool_count: usize,
    pub status_counts: BTreeMap<String, usize>,
    pub inserted_rows: usize,
    pub updated_rows: usize,
    pub deleted_rows: usize,
    pub changed_rows: usize,
    pub materialized_checksum: String,
    pub consumers_need_rebuild: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScopeStatus {
    pub network: String,
    pub chain_id: i64,
    pub scope_key: String,
    pub pool_table: String,
    pub filter_mode: String,
    pub enabled: bool,
    pub library_keys: Vec<String>,
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub materialized_checksum: Option<String>,
    pub live_checksum: String,
    pub pool_count: usize,
    pub active_pool_count: usize,
    pub isolated_pool_count: usize,
    pub status_counts: BTreeMap<String, usize>,
    pub checksum_matches_config: Option<bool>,
}

#[derive(Debug, Clone)]
pub struct ScopeWriteRequest {
    pub network: String,
    pub chain_id: i64,
    pub scope_key: String,
    pub operation: String,
    pub request_id: String,
    pub idempotency_key: String,
    pub apply: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScopeWriteOutcome {
    #[serde(flatten)]
    pub result: ScopeMaterializationResult,
    pub idempotent_replay: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedScopeWrite {
    request_id: String,
    request_digest: String,
    result: ScopeMaterializationResult,
}

#[derive(Debug, Clone)]
struct TokenDescriptor {
    token_key_id: String,
    v4_currency_key: String,
}

type LibraryState = (BTreeMap<String, i64>, BTreeMap<String, String>);

#[derive(Debug, Clone)]
struct RawPool {
    pool_id: String,
    pool_manager: String,
    pool_key_json: String,
    token0: String,
    token1: String,
    fee: i64,
    dynamic_fee: i64,
    special_fee: i64,
    tick_spacing: Option<i64>,
    hooks: String,
    has_liquidity: Option<i64>,
    liquidity_index: Option<f64>,
    sqrt_price_x96: Option<String>,
    tick: Option<i64>,
    liquidity: Option<String>,
    price: Option<f64>,
    depth_status: Option<String>,
    liquidity_read_at: Option<String>,
    liquidity_block_number: Option<i64>,
    evidence_source: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
struct CanonicalScopeRow {
    pool_id: String,
    pool_manager: String,
    pool_key_json: String,
    token0: String,
    token1: String,
    token0_key_id: String,
    token1_key_id: String,
    fee: i64,
    dynamic_fee: i64,
    special_fee: i64,
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
    source_block_number: Option<i64>,
    source_tx_hash: Option<String>,
    pool_status: String,
}

enum MaterializedPool {
    Emitted(Box<CanonicalScopeRow>),
    Skipped(&'static str),
}

pub fn materialize_scope(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    apply: bool,
) -> Result<ScopeMaterializationResult, ScopeError> {
    materialize_scope_impl(database_path, network, chain_id, scope_key, apply, None)
        .map(|(result, _)| result)
}

pub fn materialize_scope_write(
    database_path: impl AsRef<Path>,
    request: ScopeWriteRequest,
) -> Result<ScopeWriteOutcome, ScopeError> {
    let (result, idempotent_replay) = materialize_scope_impl(
        database_path,
        &request.network,
        request.chain_id,
        &request.scope_key,
        request.apply,
        Some(&request),
    )?;
    Ok(ScopeWriteOutcome {
        result,
        idempotent_replay,
    })
}

fn materialize_scope_impl(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    apply: bool,
    write_request: Option<&ScopeWriteRequest>,
) -> Result<(ScopeMaterializationResult, bool), ScopeError> {
    let scope_key = scope_key.trim().to_ascii_lowercase();
    let scope = scope_definition(&scope_key)
        .ok_or_else(|| ScopeError::UnsupportedScope(scope_key.clone()))?;
    if network.trim().is_empty() {
        return Err(ScopeError::InvalidRawPool(
            "network must not be empty".to_owned(),
        ));
    }
    if chain_id <= 0 {
        return Err(ScopeError::InvalidRawPool(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    let prepared_write = match write_request {
        Some(request) => {
            validate_scope_write_request(request, network, chain_id, &scope_key, apply)?;
            Some((request, scope_request_digest(request, &scope_key)?))
        }
        None => None,
    };

    let mut connection = open_connection(database_path.as_ref(), apply)?;
    connection.busy_timeout(std::time::Duration::from_secs(60))?;
    if apply {
        let transaction =
            connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let outcome = materialize_scope_on_connection(
            &transaction,
            network,
            chain_id,
            &scope_key,
            scope,
            true,
            prepared_write,
        )?;
        transaction.commit()?;
        Ok(outcome)
    } else {
        materialize_scope_on_connection(
            &connection,
            network,
            chain_id,
            &scope_key,
            scope,
            false,
            prepared_write,
        )
    }
}

fn materialize_scope_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    scope: ScopeDefinition,
    apply: bool,
    prepared_write: Option<(&ScopeWriteRequest, String)>,
) -> Result<(ScopeMaterializationResult, bool), ScopeError> {
    if apply {
        if let Some((request, request_digest)) = prepared_write.as_ref() {
            if let Some(evidence_json) = find_scope_write_request(connection, request)? {
                let persisted = serde_json::from_str::<PersistedScopeWrite>(&evidence_json)?;
                if &persisted.request_digest != request_digest {
                    return Err(ScopeError::IdempotencyKeyReused {
                        idempotency_key: request.idempotency_key.clone(),
                        existing_request_id: persisted.request_id,
                        submitted_request_id: request.request_id.clone(),
                    });
                }
                return Ok((persisted.result, true));
            }
        }
    }

    let (library_versions, library_checksums) =
        read_library_states(connection, network, chain_id, scope.library_keys)?;
    let anchors = read_anchor_tokens(connection, network, chain_id, scope)?;
    if scope.filter_mode == "edge_any" && anchors.is_empty() {
        return Err(ScopeError::NoAdmittedAnchorTokens);
    }
    let admitted_tokens =
        read_admitted_tokens(connection, network, chain_id, ALL_TOKEN_LIBRARY_KEYS)?;
    let raw_pools = read_raw_pools(connection, network, chain_id)?;

    let mut desired_by_id = BTreeMap::<String, CanonicalScopeRow>::new();
    let mut skipped = 0_usize;
    let mut skip_reason_counts = BTreeMap::<String, usize>::new();
    for raw in &raw_pools {
        let row = match materialize_raw_pool(raw, &anchors, &admitted_tokens, scope.filter_mode)? {
            MaterializedPool::Emitted(row) => *row,
            MaterializedPool::Skipped(reason) => {
                skipped += 1;
                *skip_reason_counts.entry(reason.to_owned()).or_insert(0) += 1;
                continue;
            }
        };
        if desired_by_id.insert(row.pool_id.clone(), row).is_some() {
            return Err(ScopeError::InvalidRawPool(format!(
                "duplicate pool_id {} in source table",
                raw.pool_id
            )));
        }
    }
    let desired_rows = desired_by_id.values().cloned().collect::<Vec<_>>();
    let existing_by_id = read_scope_rows(connection, network, chain_id, scope)?;
    let (inserted_rows, updated_rows, deleted_rows) = diff_rows(&existing_by_id, &desired_by_id);
    let changed_rows = inserted_rows + updated_rows + deleted_rows;
    let materialized_checksum = scope_checksum(&desired_rows);
    let status_counts = count_statuses(&desired_rows);
    let active_pool_count = status_counts.get("active").copied().unwrap_or(0);
    if apply && changed_rows > 0 {
        let now = chrono::Utc::now().to_rfc3339();
        for row in &desired_rows {
            if existing_by_id.get(&row.pool_id) != Some(row) {
                upsert_scope_row(connection, network, chain_id, scope, row, &now)?;
            }
        }
        let stale_ids = existing_by_id
            .keys()
            .filter(|pool_id| !desired_by_id.contains_key(*pool_id))
            .cloned()
            .collect::<Vec<_>>();
        delete_scope_rows(connection, network, chain_id, scope, &stale_ids)?;
    }
    if apply {
        write_scope_config(
            connection,
            network,
            chain_id,
            scope,
            &library_versions,
            &library_checksums,
            &materialized_checksum,
        )?;
    }

    let result = ScopeMaterializationResult {
        network: network.to_owned(),
        chain_id,
        scope_key: scope_key.to_owned(),
        pool_table: scope.pool_table.to_owned(),
        filter_mode: scope.filter_mode.to_owned(),
        library_keys: scope
            .library_keys
            .iter()
            .map(|key| (*key).to_owned())
            .collect(),
        library_versions,
        library_checksums,
        apply,
        dry_run: !apply,
        source_pool_count: raw_pools.len(),
        eligible_pool_count: desired_rows.len(),
        skipped_pool_count: skipped,
        skip_reason_counts,
        active_pool_count,
        isolated_pool_count: desired_rows.len().saturating_sub(active_pool_count),
        status_counts,
        inserted_rows,
        updated_rows,
        deleted_rows,
        changed_rows,
        materialized_checksum,
        consumers_need_rebuild: changed_rows > 0,
    };
    if apply {
        if let Some((request, request_digest)) = prepared_write {
            persist_scope_write_request(connection, request, &request_digest, &result)?;
        }
    }
    Ok((result, false))
}

pub fn read_scope_status(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<ScopeStatus, ScopeError> {
    let scope_key = scope_key.trim().to_ascii_lowercase();
    let scope = scope_definition(&scope_key)
        .ok_or_else(|| ScopeError::UnsupportedScope(scope_key.clone()))?;
    let connection = open_connection(database_path.as_ref(), false)?;
    let config = connection
        .query_row(
            "SELECT pool_table, library_keys_json, library_versions_json,
                    library_checksums_json, filter_mode, enabled,
                    materialized_checksum
             FROM pool_scope_config
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = ?3",
            params![network, chain_id, scope_key],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)? != 0,
                    row.get::<_, Option<String>>(6)?,
                ))
            },
        )
        .optional()?;
    let rows = if let Some((pool_table, _, _, _, _, _, _)) = config.as_ref() {
        read_scope_rows_with_table(&connection, network, chain_id, pool_table)?
    } else {
        read_scope_rows(&connection, network, chain_id, scope)?
    };
    let values = rows.values().cloned().collect::<Vec<_>>();
    let live_checksum = scope_checksum(&values);
    let status_counts = count_statuses(&values);
    let active_pool_count = status_counts.get("active").copied().unwrap_or(0);
    match config {
        Some((
            pool_table,
            library_keys_json,
            library_versions_json,
            library_checksums_json,
            filter_mode,
            enabled,
            materialized_checksum,
        )) => Ok(ScopeStatus {
            network: network.to_owned(),
            chain_id,
            scope_key,
            pool_table,
            filter_mode,
            enabled,
            library_keys: serde_json::from_str(&library_keys_json).unwrap_or_default(),
            library_versions: serde_json::from_str(&library_versions_json).unwrap_or_default(),
            library_checksums: serde_json::from_str(&library_checksums_json).unwrap_or_default(),
            checksum_matches_config: materialized_checksum
                .as_ref()
                .map(|checksum| checksum == &live_checksum),
            materialized_checksum,
            live_checksum,
            pool_count: values.len(),
            active_pool_count,
            isolated_pool_count: values.len().saturating_sub(active_pool_count),
            status_counts,
        }),
        None => Ok(ScopeStatus {
            network: network.to_owned(),
            chain_id,
            scope_key,
            pool_table: scope.pool_table.to_owned(),
            filter_mode: scope.filter_mode.to_owned(),
            enabled: false,
            library_keys: scope
                .library_keys
                .iter()
                .map(|key| (*key).to_owned())
                .collect(),
            library_versions: BTreeMap::new(),
            library_checksums: BTreeMap::new(),
            materialized_checksum: None,
            live_checksum,
            pool_count: values.len(),
            active_pool_count,
            isolated_pool_count: values.len().saturating_sub(active_pool_count),
            status_counts,
            checksum_matches_config: None,
        }),
    }
}

fn open_connection(path: &Path, writable: bool) -> Result<Connection, ScopeError> {
    let flags = if writable {
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI
    } else {
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI
    };
    Ok(Connection::open_with_flags(path, flags)?)
}

fn read_library_states(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    library_keys: &[&str],
) -> Result<LibraryState, ScopeError> {
    let mut versions = BTreeMap::new();
    let mut checksums = BTreeMap::new();
    for library_key in library_keys {
        let state = connection
            .query_row(
                "SELECT library_version, metadata_checksum
                 FROM token_library_state
                 WHERE network = ?1 AND chain_id = ?2 AND library_key = ?3",
                params![network, chain_id, library_key],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()?
            .ok_or_else(|| ScopeError::MissingLibraryState((*library_key).to_owned()))?;
        versions.insert((*library_key).to_owned(), state.0);
        checksums.insert((*library_key).to_owned(), state.1);
    }
    Ok((versions, checksums))
}

fn read_anchor_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope: ScopeDefinition,
) -> Result<BTreeSet<String>, ScopeError> {
    if scope.filter_mode == "both_end" {
        return Ok(BTreeSet::new());
    }
    let mut statement = connection.prepare(
        "SELECT token_key_id
         FROM token_core
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'
         ORDER BY token_key_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| row.get::<_, String>(0))?;
    Ok(rows.collect::<Result<BTreeSet<_>, _>>()?)
}

fn read_admitted_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    library_keys: &[&str],
) -> Result<BTreeMap<String, TokenDescriptor>, ScopeError> {
    let placeholders = library_keys
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT v4_currency_key, token_key_id, library_key
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'
           AND library_key IN ({placeholders})
         ORDER BY v4_currency_key,
                  CASE library_key
                      WHEN 'core' THEN 0
                      WHEN 'stocks' THEN 1
                      WHEN 'extension' THEN 2
                      WHEN 'test' THEN 3
                      ELSE 4
                  END,
                  token_key_id"
    );
    let mut statement = connection.prepare(&sql)?;
    let mut query_params = vec![network.to_owned(), chain_id.to_string()];
    query_params.extend(library_keys.iter().map(|key| (*key).to_owned()));
    let rows = statement.query_map(params_from_iter(query_params), |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    let mut tokens = BTreeMap::<String, TokenDescriptor>::new();
    let mut conflicts = BTreeSet::new();
    for row in rows {
        let (v4_currency_key, token_key_id, _) = row?;
        if conflicts.contains(&v4_currency_key) {
            continue;
        }
        match tokens.get(&v4_currency_key) {
            Some(existing) if existing.token_key_id != token_key_id => {
                tokens.remove(&v4_currency_key);
                conflicts.insert(v4_currency_key);
            }
            Some(_) => {}
            None => {
                tokens.insert(
                    v4_currency_key.clone(),
                    TokenDescriptor {
                        token_key_id,
                        v4_currency_key,
                    },
                );
            }
        }
    }
    Ok(tokens)
}

fn read_raw_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<RawPool>, ScopeError> {
    let mut statement = connection.prepare(
        "SELECT p.pool_id, COALESCE(p.pool_manager, ''),
                COALESCE(p.pool_key_json, '{}'), COALESCE(p.token0, ''),
                COALESCE(p.token1, ''), COALESCE(p.fee, 0),
                COALESCE(p.dynamic_fee, 0), COALESCE(p.special_fee, 0),
                p.tick_spacing, COALESCE(p.hooks, ''),
                l.has_liquidity, l.liquidity_index, l.sqrt_price_x96, l.tick,
                l.liquidity, l.price, l.depth_status, l.liquidity_read_at,
                l.liquidity_block_number, l.evidence_source
         FROM v4_exchange_pools p
         LEFT JOIN v4_exchange_pool_liquidity_snapshots l
           ON l.network = p.network AND l.chain_id = p.chain_id
          AND l.protocol = p.protocol AND l.pool_id = p.pool_id
         WHERE p.network = ?1 AND p.chain_id = ?2 AND p.protocol = ?3
         ORDER BY p.pool_id",
    )?;
    let rows = statement.query_map(params![network, chain_id, PROTOCOL], |row| {
        Ok(RawPool {
            pool_id: row.get(0)?,
            pool_manager: row.get(1)?,
            pool_key_json: row.get(2)?,
            token0: row.get(3)?,
            token1: row.get(4)?,
            fee: row.get(5)?,
            dynamic_fee: row.get(6)?,
            special_fee: row.get(7)?,
            tick_spacing: row.get(8)?,
            hooks: row.get(9)?,
            has_liquidity: row.get(10)?,
            liquidity_index: row.get(11)?,
            sqrt_price_x96: row.get(12)?,
            tick: row.get(13)?,
            liquidity: row.get(14)?,
            price: row.get(15)?,
            depth_status: row.get(16)?,
            liquidity_read_at: row.get(17)?,
            liquidity_block_number: row.get(18)?,
            evidence_source: row.get(19)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn validate_scope_write_request(
    request: &ScopeWriteRequest,
    network: &str,
    chain_id: i64,
    scope_key: &str,
    apply: bool,
) -> Result<(), ScopeError> {
    if request.network != network || request.chain_id != chain_id {
        return Err(ScopeError::InvalidRawPool(
            "scope write request network or chain_id does not match the operation".to_owned(),
        ));
    }
    if request.scope_key.trim().to_ascii_lowercase() != scope_key {
        return Err(ScopeError::InvalidRawPool(
            "scope write request scope_key does not match the operation".to_owned(),
        ));
    }
    if request.apply != apply {
        return Err(ScopeError::InvalidRawPool(
            "scope write request apply flag does not match the operation".to_owned(),
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

fn validate_write_token(label: &str, value: &str, max_length: usize) -> Result<(), ScopeError> {
    if value.is_empty() || value.len() > max_length {
        return Err(ScopeError::InvalidRawPool(format!(
            "{label} must contain 1..={max_length} bytes"
        )));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':'))
    {
        return Err(ScopeError::InvalidRawPool(format!(
            "{label} contains unsupported characters"
        )));
    }
    Ok(())
}

fn scope_request_digest(
    request: &ScopeWriteRequest,
    scope_key: &str,
) -> Result<String, ScopeError> {
    let payload = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "scopeKey": scope_key,
        "operation": request.operation,
        "apply": request.apply
    });
    Ok(format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&payload)?))
    ))
}

fn find_scope_write_request(
    connection: &Connection,
    request: &ScopeWriteRequest,
) -> Result<Option<String>, ScopeError> {
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

fn persist_scope_write_request(
    connection: &Connection,
    request: &ScopeWriteRequest,
    request_digest: &str,
    result: &ScopeMaterializationResult,
) -> Result<(), ScopeError> {
    let request_json = serde_json::json!({
        "network": request.network,
        "chainId": request.chain_id,
        "scopeKey": request.scope_key,
        "operation": request.operation,
        "requestId": request.request_id,
        "apply": request.apply
    });
    let response_json = serde_json::to_string(&PersistedScopeWrite {
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

fn materialize_raw_pool(
    raw: &RawPool,
    anchors: &BTreeSet<String>,
    tokens: &BTreeMap<String, TokenDescriptor>,
    filter_mode: &str,
) -> Result<MaterializedPool, ScopeError> {
    let token0 = match normalize_address(&raw.token0) {
        Ok(value) => value,
        Err(_) => return Ok(MaterializedPool::Skipped("invalid_token_address")),
    };
    let token1 = match normalize_address(&raw.token1) {
        Ok(value) => value,
        Err(_) => return Ok(MaterializedPool::Skipped("invalid_token_address")),
    };
    let (Some(descriptor0), Some(descriptor1)) = (tokens.get(&token0), tokens.get(&token1)) else {
        return Ok(MaterializedPool::Skipped("token_not_admitted"));
    };
    match filter_mode {
        "edge_any" => {
            if !anchors.contains(&descriptor0.token_key_id)
                && !anchors.contains(&descriptor1.token_key_id)
            {
                return Ok(MaterializedPool::Skipped("no_core_anchor"));
            }
        }
        "both_end" => {}
        _ => {
            return Err(ScopeError::UnsupportedScope(format!(
                "unsupported pool filter mode {filter_mode}"
            )));
        }
    }

    let mut ordered = [
        (
            descriptor0.v4_currency_key.clone(),
            descriptor0.token_key_id.clone(),
        ),
        (
            descriptor1.v4_currency_key.clone(),
            descriptor1.token_key_id.clone(),
        ),
    ];
    ordered.sort();
    let tick_spacing = raw.tick_spacing.and_then(|value| i32::try_from(value).ok());
    let fee = u32::try_from(raw.fee).ok();
    let hooks = normalize_address(&raw.hooks).unwrap_or_else(|_| raw.hooks.clone());
    let Some(tick_spacing) = tick_spacing else {
        return Ok(MaterializedPool::Skipped("invalid_pool_key"));
    };
    let Some(fee) = fee else {
        return Ok(MaterializedPool::Skipped("invalid_pool_key"));
    };
    let Ok(pool_key) = V4PoolKey {
        currency0: ordered[0].0.clone(),
        currency1: ordered[1].0.clone(),
        fee,
        tick_spacing,
        hooks,
    }
    .normalized() else {
        return Ok(MaterializedPool::Skipped("invalid_pool_key"));
    };
    let recomputed_pool_id = pool_key
        .pool_id()
        .map_err(|error| ScopeError::InvalidRawPool(format!("{}: {error}", raw.pool_id)))?;
    let stored_pool_id = raw.pool_id.trim().to_ascii_lowercase();
    if stored_pool_id.is_empty() {
        return Ok(MaterializedPool::Skipped("invalid_pool_id"));
    }
    let pool_manager = normalize_address(&raw.pool_manager).unwrap_or_default();
    let mut quality_reasons = Vec::new();
    if pool_manager.is_empty() {
        quality_reasons.push("missing_pool_manager");
    }
    if fee == 0 || raw.fee > MAX_CANDIDATE_FIXED_FEE {
        quality_reasons.push("fee_not_fixed_candidate_range");
    }
    if raw.dynamic_fee != 0 || fee & V4_DYNAMIC_FEE_FLAG != 0 {
        quality_reasons.push("dynamic_fee");
    }
    if raw.special_fee != 0 || fee > V4_MAX_FEE.min(manager_contracts::V4_SPECIAL_FEE_THRESHOLD) {
        quality_reasons.push("special_fee");
    }
    if pool_key.hooks != ZERO_ADDRESS {
        quality_reasons.push("non_zero_hook");
    }
    if raw.has_liquidity != Some(1) {
        quality_reasons.push("liquidity_not_confirmed");
    }
    if raw.liquidity_index.unwrap_or(0.0) <= 0.0 {
        quality_reasons.push("liquidity_index_not_positive");
    }
    let pool_status = if stored_pool_id != recomputed_pool_id {
        "identity_mismatch"
    } else if quality_reasons.contains(&"non_zero_hook") {
        "blocked"
    } else if !quality_reasons.is_empty() {
        "invalid"
    } else {
        "active"
    };
    let source = serde_json::from_str::<Value>(&raw.pool_key_json).unwrap_or(Value::Null);
    let source_block_number = source.get("initializeBlockNumber").and_then(Value::as_i64);
    let source_tx_hash = source
        .get("initializeTransactionHash")
        .and_then(Value::as_str)
        .map(str::to_owned);

    Ok(MaterializedPool::Emitted(Box::new(CanonicalScopeRow {
        pool_id: stored_pool_id,
        pool_manager,
        pool_key_json: serde_json::to_string(&pool_key)
            .map_err(|error| ScopeError::InvalidRawPool(error.to_string()))?,
        token0: pool_key.currency0,
        token1: pool_key.currency1,
        token0_key_id: ordered[0].1.clone(),
        token1_key_id: ordered[1].1.clone(),
        fee: i64::from(fee),
        dynamic_fee: i64::from(raw.dynamic_fee != 0),
        special_fee: i64::from(raw.special_fee != 0),
        tick_spacing: i64::from(tick_spacing),
        hooks: pool_key.hooks,
        sqrt_price_x96: raw.sqrt_price_x96.clone(),
        tick: raw.tick,
        liquidity: raw.liquidity.clone(),
        liquidity_index: raw.liquidity_index,
        price: raw.price,
        depth_status: raw.depth_status.clone(),
        liquidity_read_at: raw.liquidity_read_at.clone(),
        liquidity_block_number: raw.liquidity_block_number,
        evidence_source: raw.evidence_source.clone(),
        source_block_number,
        source_tx_hash,
        pool_status: pool_status.to_owned(),
    })))
}

fn read_scope_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope: ScopeDefinition,
) -> Result<BTreeMap<String, CanonicalScopeRow>, ScopeError> {
    read_scope_rows_with_table(connection, network, chain_id, scope.pool_table)
}

fn read_scope_rows_with_table(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_table: &str,
) -> Result<BTreeMap<String, CanonicalScopeRow>, ScopeError> {
    let sql = format!(
        "SELECT pool_id, pool_manager, pool_key_json, token0, token1,
                token0_key_id, token1_key_id, fee, dynamic_fee, special_fee,
                tick_spacing, hooks, sqrt_price_x96, tick, liquidity,
                liquidity_index, price, depth_status, liquidity_read_at,
                liquidity_block_number, evidence_source, source_block_number,
                source_tx_hash, pool_status
         FROM {pool_table}
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
         ORDER BY pool_id"
    );
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(params![network, chain_id, PROTOCOL], |row| {
        Ok(CanonicalScopeRow {
            pool_id: row.get(0)?,
            pool_manager: row.get(1)?,
            pool_key_json: row.get(2)?,
            token0: row.get(3)?,
            token1: row.get(4)?,
            token0_key_id: row.get(5)?,
            token1_key_id: row.get(6)?,
            fee: row.get(7)?,
            dynamic_fee: row.get(8)?,
            special_fee: row.get(9)?,
            tick_spacing: row.get(10)?,
            hooks: row.get(11)?,
            sqrt_price_x96: row.get(12)?,
            tick: row.get(13)?,
            liquidity: row.get(14)?,
            liquidity_index: row.get(15)?,
            price: row.get(16)?,
            depth_status: row.get(17)?,
            liquidity_read_at: row.get(18)?,
            liquidity_block_number: row.get(19)?,
            evidence_source: row.get(20)?,
            source_block_number: row.get(21)?,
            source_tx_hash: row.get(22)?,
            pool_status: row.get(23)?,
        })
    })?;
    let mut values = BTreeMap::new();
    for row in rows {
        let row = row?;
        values.insert(row.pool_id.clone(), row);
    }
    Ok(values)
}

fn diff_rows(
    existing: &BTreeMap<String, CanonicalScopeRow>,
    desired: &BTreeMap<String, CanonicalScopeRow>,
) -> (usize, usize, usize) {
    let inserted = desired
        .keys()
        .filter(|pool_id| !existing.contains_key(*pool_id))
        .count();
    let updated = desired
        .iter()
        .filter(|(pool_id, row)| {
            existing
                .get(*pool_id)
                .is_some_and(|existing_row| existing_row != *row)
        })
        .count();
    let deleted = existing
        .keys()
        .filter(|pool_id| !desired.contains_key(*pool_id))
        .count();
    (inserted, updated, deleted)
}

fn upsert_scope_row(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope: ScopeDefinition,
    row: &CanonicalScopeRow,
    now: &str,
) -> Result<(), ScopeError> {
    let sql = format!(
        "INSERT INTO {} (
             network, chain_id, protocol, pool_id, pool_manager, pool_key_json,
             token0, token1, token0_key_id, token1_key_id, fee, dynamic_fee,
             special_fee, tick_spacing, hooks, sqrt_price_x96, tick, liquidity,
             liquidity_index, price, depth_status, liquidity_read_at,
             liquidity_block_number, evidence_source, source_block_number,
             source_tx_hash, pool_status, materialized_at, updated_at
         ) VALUES (
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13,
             ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?24, ?25,
             ?26, ?27, ?28, ?29
         )
         ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
             pool_manager = excluded.pool_manager,
             pool_key_json = excluded.pool_key_json,
             token0 = excluded.token0,
             token1 = excluded.token1,
             token0_key_id = excluded.token0_key_id,
             token1_key_id = excluded.token1_key_id,
             fee = excluded.fee,
             dynamic_fee = excluded.dynamic_fee,
             special_fee = excluded.special_fee,
             tick_spacing = excluded.tick_spacing,
             hooks = excluded.hooks,
             sqrt_price_x96 = excluded.sqrt_price_x96,
             tick = excluded.tick,
             liquidity = excluded.liquidity,
             liquidity_index = excluded.liquidity_index,
             price = excluded.price,
             depth_status = excluded.depth_status,
             liquidity_read_at = excluded.liquidity_read_at,
             liquidity_block_number = excluded.liquidity_block_number,
             evidence_source = excluded.evidence_source,
             source_block_number = excluded.source_block_number,
             source_tx_hash = excluded.source_tx_hash,
             pool_status = excluded.pool_status,
             materialized_at = excluded.materialized_at,
             updated_at = excluded.updated_at",
        scope.pool_table
    );
    connection.execute(
        &sql,
        params![
            network,
            chain_id,
            PROTOCOL,
            row.pool_id,
            row.pool_manager,
            row.pool_key_json,
            row.token0,
            row.token1,
            row.token0_key_id,
            row.token1_key_id,
            row.fee,
            row.dynamic_fee,
            row.special_fee,
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
            row.source_block_number,
            row.source_tx_hash,
            row.pool_status,
            now,
            now
        ],
    )?;
    Ok(())
}

fn delete_scope_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope: ScopeDefinition,
    pool_ids: &[String],
) -> Result<(), ScopeError> {
    for chunk in pool_ids.chunks(500) {
        if chunk.is_empty() {
            continue;
        }
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "DELETE FROM {}
             WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id IN ({placeholders})",
            scope.pool_table
        );
        let mut values = vec![
            network.to_owned(),
            chain_id.to_string(),
            PROTOCOL.to_owned(),
        ];
        values.extend(chunk.iter().cloned());
        connection.execute(&sql, params_from_iter(values))?;
    }
    Ok(())
}

fn write_scope_config(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope: ScopeDefinition,
    library_versions: &BTreeMap<String, i64>,
    library_checksums: &BTreeMap<String, String>,
    materialized_checksum: &str,
) -> Result<(), ScopeError> {
    let now = chrono::Utc::now().to_rfc3339();
    connection.execute(
        "INSERT INTO pool_scope_config (
             network, chain_id, scope_key, pool_table, library_keys_json,
             library_versions_json, library_checksums_json, filter_mode,
             excluded_token_key_ids_json, enabled, materialized_checksum,
             last_rebuilt_at, created_at, updated_at
         ) VALUES (
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, '[]', 1, ?9, ?10, ?10, ?10
         )
         ON CONFLICT(network, chain_id, scope_key) DO UPDATE SET
             pool_table = excluded.pool_table,
             library_keys_json = excluded.library_keys_json,
             library_versions_json = excluded.library_versions_json,
             library_checksums_json = excluded.library_checksums_json,
             filter_mode = excluded.filter_mode,
             excluded_token_key_ids_json = excluded.excluded_token_key_ids_json,
             enabled = excluded.enabled,
             materialized_checksum = excluded.materialized_checksum,
             last_rebuilt_at = excluded.last_rebuilt_at,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            scope.key,
            scope.pool_table,
            serde_json::to_string(
                &scope
                    .library_keys
                    .iter()
                    .map(|key| (*key).to_owned())
                    .collect::<Vec<_>>()
            )
            .map_err(|error| ScopeError::InvalidRawPool(error.to_string()))?,
            serde_json::to_string(library_versions)
                .map_err(|error| ScopeError::InvalidRawPool(error.to_string()))?,
            serde_json::to_string(library_checksums)
                .map_err(|error| ScopeError::InvalidRawPool(error.to_string()))?,
            scope.filter_mode,
            materialized_checksum,
            now
        ],
    )?;
    Ok(())
}

fn scope_checksum(rows: &[CanonicalScopeRow]) -> String {
    let payload = serde_json::to_vec(rows).unwrap_or_default();
    format!("sha256:{}", hex::encode(Sha256::digest(payload)))
}

fn count_statuses(rows: &[CanonicalScopeRow]) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for row in rows {
        *counts.entry(row.pool_status.clone()).or_insert(0) += 1;
    }
    counts
}

#[cfg(test)]
mod tests {
    use super::{
        materialize_scope, materialize_scope_write, read_scope_status, ScopeError,
        ScopeWriteRequest,
    };
    use manager_contracts::V4PoolKey;
    use rusqlite::{params, Connection};
    use serde_json::json;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const ETH: &str = "0x0000000000000000000000000000000000000000";
    const WETH: &str = "0x1111111111111111111111111111111111111111";
    const USDG: &str = "0x2222222222222222222222222222222222222222";
    const USDE: &str = "0x3333333333333333333333333333333333333333";
    const STOCK: &str = "0x4444444444444444444444444444444444444444";
    const EXTENSION: &str = "0x5555555555555555555555555555555555555555";
    const BLOCKED: &str = "0x6666666666666666666666666666666666666666";
    const TEST_TZ: &str = "0x8888888888888888888888888888888888888888";
    const ZERO_HOOK: &str = "0x0000000000000000000000000000000000000000";
    const POOL_MANAGER: &str = "0x7777777777777777777777777777777777777777";

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = Connection::open(&path).unwrap();
        create_business_tables(&connection);
        seed_tokens(&connection);
        (directory, path)
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
        for (address, symbol, table, status) in [
            (ETH, "ETH", "token_core", "admitted"),
            (WETH, "WETH", "token_core", "admitted"),
            (USDG, "USDG", "token_core", "admitted"),
            (USDE, "USDE", "token_core", "admitted"),
            (STOCK, "AMZN", "token_stocks", "admitted"),
            (EXTENSION, "TA", "token_extension", "admitted"),
            (BLOCKED, "BLOCKED", "token_extension", "blocked"),
            (TEST_TZ, "TZ", "token_test", "admitted"),
        ] {
            let sql = format!(
                "INSERT INTO {table} (
                    network, chain_id, token_key_id, v4_currency_key,
                    token_address, token_symbol, token_name, token_decimals,
                    token_kind, is_native, is_wrapped_native, is_stable,
                    admission_status, admission_source, metadata_source,
                    metadata_checksum, source_json, first_seen_at,
                    first_admitted_at, updated_at, blocked_reason
                 ) VALUES (
                    ?1, ?2, ?3, ?4, ?4, ?5, ?5, 18, 'erc20', 0, 0, 0,
                    ?6, 'fixture', 'fixture', 'sha256:fixture', '{{}}',
                    '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z',
                    '2026-11-04T00:00:00Z', NULL
                 )"
            );
            connection
                .execute(
                    &sql,
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
        for (library_key, version, token_count, admitted_count) in [
            ("core", 7, 4, 4),
            ("stocks", 1, 1, 1),
            ("extension", 1, 2, 1),
            ("test", 1, 1, 1),
        ] {
            connection
                .execute(
                    "INSERT INTO token_library_state (
                        network, chain_id, library_key, library_version,
                        token_count, admitted_count, metadata_checksum,
                        source_checkpoint, generated_at, updated_at
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'fixture',
                               '2026-11-04T00:00:00Z',
                               '2026-11-04T00:00:00Z')",
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

    fn pool_key(currency0: &str, currency1: &str, fee: u32, tick_spacing: i32) -> V4PoolKey {
        let (currency0, currency1) = if currency0 < currency1 {
            (currency0.to_owned(), currency1.to_owned())
        } else {
            (currency1.to_owned(), currency0.to_owned())
        };
        V4PoolKey {
            currency0,
            currency1,
            fee,
            tick_spacing,
            hooks: ZERO_HOOK.to_owned(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn insert_pool(
        connection: &Connection,
        token0: &str,
        token1: &str,
        fee: u32,
        tick_spacing: i32,
        dynamic_fee: i64,
        special_fee: i64,
        hooks: &str,
        has_liquidity: Option<i64>,
        liquidity_index: Option<f64>,
        force_pool_id: Option<&str>,
    ) -> V4PoolKey {
        let mut key = pool_key(token0, token1, fee, tick_spacing);
        key.hooks = hooks.to_owned();
        let pool_id = force_pool_id
            .map(str::to_owned)
            .unwrap_or_else(|| key.pool_id().unwrap());
        let key_json = json!({
            "currency0": key.currency0,
            "currency1": key.currency1,
            "fee": key.fee,
            "tickSpacing": key.tick_spacing,
            "hooks": key.hooks,
            "initializeBlockNumber": 123,
            "initializeTransactionHash": format!("0x{}", "ab".repeat(32))
        });
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, pool_manager,
                    pool_key_json, token0, token1, fee, dynamic_fee,
                    special_fee, tick_spacing, hooks
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7,
                           ?8, ?9, ?10, ?11, ?12)",
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
                    special_fee,
                    i64::from(tick_spacing),
                    hooks
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, has_liquidity,
                    liquidity_index
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5)",
                params![NETWORK, CHAIN_ID, pool_id, has_liquidity, liquidity_index],
            )
            .unwrap();
        key
    }

    fn seed_pools(connection: &Connection) {
        insert_pool(
            connection,
            ETH,
            STOCK,
            500,
            10,
            0,
            0,
            ZERO_HOOK,
            Some(1),
            Some(0.01),
            None,
        );
        insert_pool(
            connection,
            USDG,
            EXTENSION,
            3000,
            60,
            0,
            0,
            ZERO_HOOK,
            Some(1),
            Some(1.0),
            None,
        );
        insert_pool(
            connection,
            USDE,
            WETH,
            100,
            1,
            0,
            0,
            ZERO_HOOK,
            Some(1),
            Some(0.001),
            None,
        );
        insert_pool(
            connection,
            STOCK,
            EXTENSION,
            3000,
            60,
            0,
            0,
            ZERO_HOOK,
            Some(1),
            Some(1.0),
            None,
        );
        insert_pool(
            connection,
            USDG,
            BLOCKED,
            3000,
            60,
            0,
            0,
            ZERO_HOOK,
            Some(1),
            Some(1.0),
            None,
        );
        insert_pool(
            connection,
            ETH,
            STOCK,
            1000,
            20,
            1,
            0,
            ZERO_HOOK,
            Some(1),
            Some(1.0),
            None,
        );
        insert_pool(
            connection,
            ETH,
            EXTENSION,
            500,
            10,
            0,
            0,
            ZERO_HOOK,
            Some(0),
            Some(0.0),
            None,
        );
        insert_pool(
            connection,
            USDG,
            STOCK,
            500,
            10,
            0,
            0,
            "0x0000000000000000000000000000000000000001",
            Some(1),
            Some(1.0),
            None,
        );
        insert_pool(
            connection,
            ETH,
            USDE,
            500,
            10,
            0,
            0,
            ZERO_HOOK,
            Some(1),
            Some(1.0),
            Some(&format!("0x{}", "00".repeat(32))),
        );
        insert_pool(
            connection,
            USDG,
            STOCK,
            700,
            -8_388_609,
            0,
            0,
            ZERO_HOOK,
            Some(1),
            Some(1.0),
            Some(&format!("0x{}", "11".repeat(32))),
        );
    }

    #[test]
    fn core4_scope_is_edge_any_and_dry_run_matches_apply() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        seed_pools(&connection);
        drop(connection);

        let dry = materialize_scope(&database, NETWORK, CHAIN_ID, "core4", false).unwrap();
        assert_eq!(dry.library_keys, vec!["core"]);
        assert_eq!(dry.filter_mode, "edge_any");
        assert_eq!(dry.source_pool_count, 10);
        assert_eq!(dry.eligible_pool_count, 7);
        assert_eq!(dry.skipped_pool_count, 3);
        assert_eq!(dry.skip_reason_counts.get("no_core_anchor"), Some(&1));
        assert_eq!(dry.skip_reason_counts.get("token_not_admitted"), Some(&1));
        assert_eq!(dry.skip_reason_counts.get("invalid_pool_key"), Some(&1));
        assert_eq!(dry.active_pool_count, 3);
        assert_eq!(dry.status_counts.get("invalid"), Some(&2));
        assert_eq!(dry.status_counts.get("blocked"), Some(&1));
        assert_eq!(dry.status_counts.get("identity_mismatch"), Some(&1));
        let connection = Connection::open(&database).unwrap();
        let before: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_exchange_pools_core4", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(before, 0);
        drop(connection);

        let applied = materialize_scope(&database, NETWORK, CHAIN_ID, "core4", true).unwrap();
        assert_eq!(applied.materialized_checksum, dry.materialized_checksum);
        assert_eq!(applied.changed_rows, 7);
        let repeated = materialize_scope(&database, NETWORK, CHAIN_ID, "core4", true).unwrap();
        assert_eq!(repeated.changed_rows, 0);
        assert!(!repeated.consumers_need_rebuild);
        assert_eq!(repeated.materialized_checksum, dry.materialized_checksum);

        let status = read_scope_status(&database, NETWORK, CHAIN_ID, "core4").unwrap();
        assert!(status.enabled);
        assert_eq!(status.pool_count, 7);
        assert_eq!(status.active_pool_count, 3);
        assert_eq!(status.checksum_matches_config, Some(true));
        assert_eq!(status.library_versions["core"], 7);

        let connection = Connection::open(&database).unwrap();
        let config: (String, String, String) = connection
            .query_row(
                "SELECT pool_table, library_keys_json, filter_mode
                 FROM pool_scope_config WHERE network = ?1 AND chain_id = ?2
                   AND scope_key = 'core4'",
                params![NETWORK, CHAIN_ID],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(
            config,
            (
                "v4_exchange_pools_core4".to_owned(),
                "[\"core\"]".to_owned(),
                "edge_any".to_owned()
            )
        );
        let active_with_unadmitted: i64 = connection
            .query_row(
                "SELECT COUNT(*)
                 FROM v4_exchange_pools_core4 p
                 WHERE p.pool_status = 'active'
                   AND (
                       NOT EXISTS (
                           SELECT 1 FROM token_library_catalog t
                           WHERE t.network = p.network AND t.chain_id = p.chain_id
                             AND t.token_key_id = p.token0_key_id
                             AND t.admission_status = 'admitted'
                       )
                       OR NOT EXISTS (
                           SELECT 1 FROM token_library_catalog t
                           WHERE t.network = p.network AND t.chain_id = p.chain_id
                             AND t.token_key_id = p.token1_key_id
                             AND t.admission_status = 'admitted'
                       )
                   )",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(active_with_unadmitted, 0);
    }

    #[test]
    fn candidate_exchange_scope_includes_pools_with_two_non_core_admitted_tokens() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        seed_pools(&connection);
        drop(connection);

        let dry =
            materialize_scope(&database, NETWORK, CHAIN_ID, "candidate_exchange", false).unwrap();
        assert_eq!(
            dry.library_keys,
            vec!["core", "stocks", "extension", "test"]
        );
        assert_eq!(dry.filter_mode, "both_end");
        assert_eq!(dry.pool_table, "v4_exchange_pools_candidate_exchange");
        assert_eq!(dry.source_pool_count, 10);
        assert_eq!(dry.eligible_pool_count, 8);
        assert_eq!(dry.skipped_pool_count, 2);
        assert_eq!(dry.skip_reason_counts.get("token_not_admitted"), Some(&1));
        assert_eq!(dry.skip_reason_counts.get("invalid_pool_key"), Some(&1));
        assert_eq!(dry.active_pool_count, 4);

        let applied =
            materialize_scope(&database, NETWORK, CHAIN_ID, "candidate_exchange", true).unwrap();
        assert_eq!(applied.materialized_checksum, dry.materialized_checksum);
        let connection = Connection::open(&database).unwrap();
        let non_core_pair: i64 = connection
            .query_row(
                "SELECT COUNT(*)
                 FROM v4_exchange_pools_candidate_exchange
                 WHERE token0_key_id IN (?1, ?2)
                   AND token1_key_id IN (?1, ?2)
                   AND pool_status = 'active'",
                params![
                    format!("{NETWORK}:{CHAIN_ID}:{STOCK}"),
                    format!("{NETWORK}:{CHAIN_ID}:{EXTENSION}")
                ],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(non_core_pair, 1);
        let core_table_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_exchange_pools_core4", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(core_table_count, 0);
    }

    #[test]
    fn unsupported_scope_is_rejected_without_writes() {
        let (_directory, database) = database();
        let error = materialize_scope(&database, NETWORK, CHAIN_ID, "full", true).unwrap_err();
        assert!(error
            .to_string()
            .contains("expected core4 or candidate_exchange"));
    }

    #[test]
    fn scope_write_replays_persisted_result_and_keeps_one_record() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        seed_pools(&connection);
        drop(connection);

        let request = ScopeWriteRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            scope_key: "core4".to_owned(),
            operation: "pools.scope.rebuild".to_owned(),
            request_id: "scope-write-1".to_owned(),
            idempotency_key: "scope-idem-1".to_owned(),
            apply: true,
        };
        let first = materialize_scope_write(&database, request.clone()).unwrap();
        assert!(!first.idempotent_replay);
        assert_eq!(first.result.changed_rows, 7);

        let replay = materialize_scope_write(
            &database,
            ScopeWriteRequest {
                request_id: "scope-write-2".to_owned(),
                ..request.clone()
            },
        )
        .unwrap();
        assert!(replay.idempotent_replay);
        assert_eq!(replay.result, first.result);

        let connection = Connection::open(&database).unwrap();
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 1);

        let response_json: String = connection
            .query_row(
                "SELECT response_json FROM manager_write_requests
                 WHERE idempotency_key = 'scope-idem-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let mut response: serde_json::Value = serde_json::from_str(&response_json).unwrap();
        response["requestDigest"] = json!("sha256:tampered");
        connection
            .execute(
                "UPDATE manager_write_requests
                 SET request_digest = 'sha256:tampered', response_json = ?1
                 WHERE idempotency_key = 'scope-idem-1'",
                params![response.to_string()],
            )
            .unwrap();
        drop(connection);

        let error = materialize_scope_write(
            &database,
            ScopeWriteRequest {
                request_id: "scope-write-conflict".to_owned(),
                ..request
            },
        )
        .unwrap_err();
        assert!(matches!(error, ScopeError::IdempotencyKeyReused { .. }));
    }

    #[test]
    fn scope_write_rolls_back_pool_and_config_when_idempotency_insert_fails() {
        let (_directory, database) = database();
        let connection = Connection::open(&database).unwrap();
        seed_pools(&connection);
        connection
            .execute_batch(
                "CREATE TRIGGER fail_manager_write_request
                 BEFORE INSERT ON manager_write_requests
                 BEGIN
                     SELECT RAISE(ABORT, 'forced idempotency insert failure');
                 END;",
            )
            .unwrap();
        drop(connection);

        let error = materialize_scope_write(
            &database,
            ScopeWriteRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                scope_key: "core4".to_owned(),
                operation: "pools.scope.rebuild".to_owned(),
                request_id: "scope-atomic-failure".to_owned(),
                idempotency_key: "scope-atomic-failure-idem".to_owned(),
                apply: true,
            },
        )
        .unwrap_err();
        assert!(matches!(error, ScopeError::Sqlite(_)));

        let connection = Connection::open(database).unwrap();
        let pool_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_exchange_pools_core4", [], |row| {
                row.get(0)
            })
            .unwrap();
        let config_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM pool_scope_config", [], |row| {
                row.get(0)
            })
            .unwrap();
        let request_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(pool_count, 0);
        assert_eq!(config_count, 0);
        assert_eq!(request_count, 0);
    }
}
