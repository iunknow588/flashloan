use crate::model::{AdmissionStatus, LibraryKey, TokenWrite};
use manager_contracts::normalize_address;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fmt;

#[derive(Debug)]
pub enum StoreError {
    Sqlite(rusqlite::Error),
    InvalidToken(String),
    InvalidSourceJson(String),
    TokenLibraryConflict {
        token_key_id: String,
        target_library: String,
        existing_library: String,
    },
}

impl fmt::Display for StoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "token storage error: {error}"),
            Self::InvalidToken(message) => write!(formatter, "invalid token: {message}"),
            Self::InvalidSourceJson(message) => {
                write!(formatter, "invalid token source_json: {message}")
            }
            Self::TokenLibraryConflict {
                token_key_id,
                target_library,
                existing_library,
            } => write!(
                formatter,
                "token {token_key_id} already has primary membership in library \
                 {existing_library}, cannot add primary membership {target_library}; \
                 extension overlap is allowed, primary libraries are not"
            ),
        }
    }
}

impl std::error::Error for StoreError {}

impl From<rusqlite::Error> for StoreError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryWriteResult {
    pub network: String,
    pub chain_id: u64,
    pub library: LibraryKey,
    pub inserted: usize,
    pub updated: usize,
    pub unchanged: usize,
    pub events: usize,
    pub version_before: i64,
    pub version_after: i64,
    pub metadata_checksum: String,
    pub source_checkpoint: Option<String>,
}

#[derive(Debug, Clone)]
struct StoredToken {
    v4_currency_key: String,
    token_address: String,
    token_symbol: String,
    token_name: Option<String>,
    token_decimals: i64,
    token_kind: String,
    is_native: bool,
    is_wrapped_native: bool,
    is_stable: bool,
    admission_status: String,
    admission_source: String,
    metadata_source: String,
    metadata_checksum: String,
    source_json: String,
    first_seen_at: String,
    first_admitted_at: Option<String>,
    blocked_reason: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CanonicalTokenRow {
    token_key_id: String,
    v4_currency_key: String,
    token_address: String,
    token_symbol: String,
    token_name: Option<String>,
    token_decimals: i64,
    token_kind: String,
    is_native: bool,
    is_wrapped_native: bool,
    is_stable: bool,
    admission_status: String,
    admission_source: String,
    metadata_source: String,
    metadata_checksum: String,
}

#[allow(clippy::too_many_arguments)]
pub fn write_library(
    connection: &mut Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
    writes: Vec<TokenWrite>,
    source_checkpoint: Option<&str>,
    actor: &str,
    reason: &str,
) -> Result<LibraryWriteResult, StoreError> {
    write_library_inner(
        connection,
        library,
        network,
        chain_id,
        writes,
        source_checkpoint,
        actor,
        reason,
        true,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn write_library_dry_run(
    connection: &mut Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
    writes: Vec<TokenWrite>,
    source_checkpoint: Option<&str>,
    actor: &str,
    reason: &str,
) -> Result<LibraryWriteResult, StoreError> {
    write_library_inner(
        connection,
        library,
        network,
        chain_id,
        writes,
        source_checkpoint,
        actor,
        reason,
        false,
    )
}

#[allow(clippy::too_many_arguments)]
fn write_library_inner(
    connection: &mut Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
    writes: Vec<TokenWrite>,
    source_checkpoint: Option<&str>,
    actor: &str,
    reason: &str,
    commit: bool,
) -> Result<LibraryWriteResult, StoreError> {
    let prepared = prepare_writes(network, chain_id, writes)?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let now = chrono::Utc::now().to_rfc3339();
    let version_before = read_library_version(&transaction, network, chain_id, library)?;

    let mut inserted = 0_usize;
    let mut updated = 0_usize;
    let mut unchanged = 0_usize;
    let mut events = 0_usize;

    for write in &prepared {
        let token_key_id = write.token_key_id.as_str();
        let existing_libraries = {
            let mut statement = transaction.prepare(
                "SELECT library_key
                 FROM token_library_catalog
                 WHERE network = ?1 AND chain_id = ?2 AND token_key_id = ?3
                 ORDER BY library_key",
            )?;
            let rows = statement.query_map(params![network, chain_id, token_key_id], |row| {
                row.get::<_, String>(0)
            })?;
            rows.collect::<Result<Vec<_>, _>>()?
        };

        if let Some(existing_library) = existing_libraries.iter().find(|existing_library| {
            existing_library.as_str() != "extension"
                && library != LibraryKey::Extension
                && existing_library.as_str() != library.as_str()
        }) {
            return Err(StoreError::TokenLibraryConflict {
                token_key_id: token_key_id.to_owned(),
                target_library: library.as_str().to_owned(),
                existing_library: existing_library.clone(),
            });
        }

        let existing = load_token(&transaction, library, network, chain_id, token_key_id)?;
        let first_seen_at = existing
            .as_ref()
            .map(|row| row.first_seen_at.clone())
            .or_else(|| write.first_seen_at.clone())
            .unwrap_or_else(|| now.clone());
        let first_admitted_at = existing
            .as_ref()
            .and_then(|row| row.first_admitted_at.clone())
            .or_else(|| (write.admission_status == AdmissionStatus::Admitted).then(|| now.clone()));

        if existing
            .as_ref()
            .is_some_and(|row| same_semantic_token(row, write, first_admitted_at.as_deref()))
        {
            unchanged += 1;
            continue;
        }

        upsert_token(
            &transaction,
            library,
            network,
            chain_id,
            write,
            &first_seen_at,
            first_admitted_at.as_deref(),
            &now,
        )?;

        let previous_status = existing
            .as_ref()
            .map(|row| row.admission_status.as_str())
            .unwrap_or_default();
        let previous_status = (!previous_status.is_empty()).then_some(previous_status);
        insert_admission_event(
            &transaction,
            network,
            chain_id,
            token_key_id,
            library,
            previous_status,
            write.admission_status,
            reason,
            actor,
            source_checkpoint,
            &now,
        )?;

        if existing.is_some() {
            updated += 1;
        } else {
            inserted += 1;
        }
        events += 1;
    }

    let changed_count = inserted + updated;
    let mut version_after = version_before;
    if changed_count > 0 {
        version_after += 1;
    }
    if version_before == 0 && version_after == 0 {
        version_after = 1;
    }

    let table = library.table_name();
    let token_count: i64 = transaction.query_row(
        &format!("SELECT COUNT(*) FROM {table} WHERE network = ?1 AND chain_id = ?2"),
        params![network, chain_id],
        |row| row.get(0),
    )?;
    let admitted_count: i64 = transaction.query_row(
        &format!(
            "SELECT COUNT(*) FROM {table}
             WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'"
        ),
        params![network, chain_id],
        |row| row.get(0),
    )?;
    let metadata_checksum = library_metadata_checksum(&transaction, library, network, chain_id)?;

    transaction.execute(
        "INSERT INTO token_library_state (
             network, chain_id, library_key, library_version, token_count,
             admitted_count, metadata_checksum, source_checkpoint,
             generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
         ON CONFLICT(network, chain_id, library_key) DO UPDATE SET
             library_version = excluded.library_version,
             token_count = excluded.token_count,
             admitted_count = excluded.admitted_count,
             metadata_checksum = excluded.metadata_checksum,
             source_checkpoint = excluded.source_checkpoint,
             generated_at = excluded.generated_at,
             updated_at = excluded.updated_at",
        params![
            network,
            chain_id,
            library.as_str(),
            version_after,
            token_count,
            admitted_count,
            metadata_checksum,
            source_checkpoint,
            now,
            now
        ],
    )?;
    let result = LibraryWriteResult {
        network: network.to_owned(),
        chain_id,
        library,
        inserted,
        updated,
        unchanged,
        events,
        version_before,
        version_after,
        metadata_checksum,
        source_checkpoint: source_checkpoint.map(str::to_owned),
    };
    if commit {
        transaction.commit()?;
    } else {
        transaction.rollback()?;
    }
    Ok(result)
}

pub fn library_metadata_checksum(
    connection: &Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
) -> Result<String, StoreError> {
    let query = format!(
        "SELECT token_key_id, v4_currency_key, token_address, token_symbol,
                token_name, token_decimals, token_kind, is_native,
                is_wrapped_native, is_stable, admission_status,
                admission_source, metadata_source, metadata_checksum
         FROM {}
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY token_key_id",
        library.table_name()
    );
    let mut statement = connection.prepare(&query)?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok(CanonicalTokenRow {
            token_key_id: row.get(0)?,
            v4_currency_key: row.get(1)?,
            token_address: row.get(2)?,
            token_symbol: row.get(3)?,
            token_name: row.get(4)?,
            token_decimals: row.get(5)?,
            token_kind: row.get(6)?,
            is_native: row.get::<_, i64>(7)? != 0,
            is_wrapped_native: row.get::<_, i64>(8)? != 0,
            is_stable: row.get::<_, i64>(9)? != 0,
            admission_status: row.get(10)?,
            admission_source: row.get(11)?,
            metadata_source: row.get(12)?,
            metadata_checksum: row.get(13)?,
        })
    })?;
    let rows = rows.collect::<Result<Vec<_>, _>>()?;
    let payload = serde_json::to_vec(&rows)
        .map_err(|error| StoreError::InvalidSourceJson(error.to_string()))?;
    let digest = Sha256::digest(payload);
    Ok(format!("sha256:{}", hex::encode(digest)))
}

fn prepare_writes(
    network: &str,
    chain_id: u64,
    writes: Vec<TokenWrite>,
) -> Result<Vec<TokenWrite>, StoreError> {
    let mut seen = HashSet::with_capacity(writes.len());
    let mut prepared = Vec::with_capacity(writes.len());

    for mut write in writes {
        if write.network != network || write.chain_id != chain_id {
            return Err(StoreError::InvalidToken(format!(
                "{} belongs to {}:{}, not {network}:{chain_id}",
                write.token_key_id, write.network, write.chain_id
            )));
        }
        if write.token_key_id.network() != network || write.token_key_id.chain_id() != chain_id {
            return Err(StoreError::InvalidToken(format!(
                "{} does not match requested network and chain",
                write.token_key_id
            )));
        }

        let token_address = normalize_address(&write.token_address).map_err(|error| {
            StoreError::InvalidToken(format!("{}: {error}", write.token_address))
        })?;
        if token_address != write.token_key_id.currency_key() {
            return Err(StoreError::InvalidToken(format!(
                "{} address does not match token_key_id currency key",
                write.token_key_id
            )));
        }
        let v4_currency_key = normalize_address(&write.v4_currency_key).map_err(|error| {
            StoreError::InvalidToken(format!("{}: {error}", write.v4_currency_key))
        })?;
        if v4_currency_key != token_address {
            return Err(StoreError::InvalidToken(format!(
                "{} v4_currency_key must equal normalized token address",
                write.token_key_id
            )));
        }
        if write.token_decimals > 36 {
            return Err(StoreError::InvalidToken(format!(
                "{} token_decimals must be between 0 and 36",
                write.token_key_id
            )));
        }
        if write.metadata_checksum.trim().is_empty() {
            return Err(StoreError::InvalidToken(format!(
                "{} metadata_checksum is required",
                write.token_key_id
            )));
        }

        let source_json = write.source_json.as_deref().unwrap_or("{}");
        let source_json_value = serde_json::from_str::<serde_json::Value>(source_json)
            .map_err(|error| StoreError::InvalidSourceJson(error.to_string()))?;
        write.token_address = token_address;
        write.v4_currency_key = v4_currency_key;
        write.source_json = Some(
            serde_json::to_string(&source_json_value)
                .map_err(|error| StoreError::InvalidSourceJson(error.to_string()))?,
        );

        if !seen.insert(write.token_key_id.as_str().to_owned()) {
            return Err(StoreError::InvalidToken(format!(
                "{} appears more than once in the same library write",
                write.token_key_id
            )));
        }
        prepared.push(write);
    }

    Ok(prepared)
}

fn read_library_version(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    library: LibraryKey,
) -> Result<i64, StoreError> {
    Ok(connection
        .query_row(
            "SELECT library_version
             FROM token_library_state
             WHERE network = ?1 AND chain_id = ?2 AND library_key = ?3",
            params![network, chain_id, library.as_str()],
            |row| row.get(0),
        )
        .optional()?
        .unwrap_or(0))
}

fn load_token(
    connection: &Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
    token_key_id: &str,
) -> Result<Option<StoredToken>, StoreError> {
    let query = format!(
        "SELECT v4_currency_key, token_address, token_symbol, token_name,
                token_decimals, token_kind, is_native, is_wrapped_native,
                is_stable, admission_status, admission_source,
                metadata_source, metadata_checksum, source_json,
                first_seen_at, first_admitted_at, blocked_reason
         FROM {}
         WHERE network = ?1 AND chain_id = ?2 AND token_key_id = ?3",
        library.table_name()
    );
    Ok(connection
        .query_row(&query, params![network, chain_id, token_key_id], |row| {
            Ok(StoredToken {
                v4_currency_key: row.get(0)?,
                token_address: row.get(1)?,
                token_symbol: row.get(2)?,
                token_name: row.get(3)?,
                token_decimals: row.get(4)?,
                token_kind: row.get(5)?,
                is_native: row.get::<_, i64>(6)? != 0,
                is_wrapped_native: row.get::<_, i64>(7)? != 0,
                is_stable: row.get::<_, i64>(8)? != 0,
                admission_status: row.get(9)?,
                admission_source: row.get(10)?,
                metadata_source: row.get(11)?,
                metadata_checksum: row.get(12)?,
                source_json: row.get(13)?,
                first_seen_at: row.get(14)?,
                first_admitted_at: row.get(15)?,
                blocked_reason: row.get(16)?,
            })
        })
        .optional()?)
}

fn same_semantic_token(
    existing: &StoredToken,
    write: &TokenWrite,
    first_admitted_at: Option<&str>,
) -> bool {
    existing.v4_currency_key == write.v4_currency_key
        && existing.token_address == write.token_address
        && existing.token_symbol == write.token_symbol
        && existing.token_name == write.token_name
        && existing.token_decimals == i64::from(write.token_decimals)
        && existing.token_kind == write.token_kind
        && existing.is_native == write.is_native
        && existing.is_wrapped_native == write.is_wrapped_native
        && existing.is_stable == write.is_stable
        && existing.admission_status == write.admission_status.as_str()
        && existing.admission_source == write.admission_source
        && existing.metadata_source == write.metadata_source
        && existing.metadata_checksum == write.metadata_checksum
        && existing.source_json == write.source_json.as_deref().unwrap_or("{}")
        && existing.first_admitted_at.as_deref() == first_admitted_at
        && existing.blocked_reason == write.blocked_reason
}

#[allow(clippy::too_many_arguments)]
fn upsert_token(
    connection: &Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
    write: &TokenWrite,
    first_seen_at: &str,
    first_admitted_at: Option<&str>,
    updated_at: &str,
) -> Result<(), StoreError> {
    let query = format!(
        "INSERT INTO {} (
             network, chain_id, token_key_id, v4_currency_key, token_address,
             token_symbol, token_name, token_decimals, token_kind, is_native,
             is_wrapped_native, is_stable, admission_status, admission_source,
             metadata_source, metadata_checksum, source_json, first_seen_at,
             first_admitted_at, updated_at, blocked_reason
         ) VALUES (
             ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13,
             ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21
         )
         ON CONFLICT(network, chain_id, token_key_id) DO UPDATE SET
             v4_currency_key = excluded.v4_currency_key,
             token_address = excluded.token_address,
             token_symbol = excluded.token_symbol,
             token_name = excluded.token_name,
             token_decimals = excluded.token_decimals,
             token_kind = excluded.token_kind,
             is_native = excluded.is_native,
             is_wrapped_native = excluded.is_wrapped_native,
             is_stable = excluded.is_stable,
             admission_status = excluded.admission_status,
             admission_source = excluded.admission_source,
             metadata_source = excluded.metadata_source,
             metadata_checksum = excluded.metadata_checksum,
             source_json = excluded.source_json,
             first_seen_at = excluded.first_seen_at,
             first_admitted_at = excluded.first_admitted_at,
             updated_at = excluded.updated_at,
             blocked_reason = excluded.blocked_reason",
        library.table_name()
    );
    connection.execute(
        &query,
        params![
            network,
            chain_id,
            write.token_key_id.as_str(),
            write.v4_currency_key,
            write.token_address,
            write.token_symbol,
            write.token_name,
            write.token_decimals,
            write.token_kind,
            write.is_native,
            write.is_wrapped_native,
            write.is_stable,
            write.admission_status.as_str(),
            write.admission_source,
            write.metadata_source,
            write.metadata_checksum,
            write.source_json.as_deref().unwrap_or("{}"),
            first_seen_at,
            first_admitted_at,
            updated_at,
            write.blocked_reason
        ],
    )?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn insert_admission_event(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    token_key_id: &str,
    library: LibraryKey,
    previous_status: Option<&str>,
    new_status: AdmissionStatus,
    reason: &str,
    actor: &str,
    source_checkpoint: Option<&str>,
    created_at: &str,
) -> Result<(), StoreError> {
    let event_id: String =
        connection.query_row("SELECT lower(hex(randomblob(16)))", [], |row| row.get(0))?;
    let evidence_json = serde_json::json!({
        "sourceCheckpoint": source_checkpoint,
    })
    .to_string();
    connection.execute(
        "INSERT INTO token_admission_events (
             event_id, network, chain_id, token_key_id, library_key,
             previous_status, new_status, reason, actor, evidence_json,
             created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
        params![
            event_id,
            network,
            chain_id,
            token_key_id,
            library.as_str(),
            previous_status,
            new_status.as_str(),
            reason,
            actor,
            evidence_json,
            created_at
        ],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        library_metadata_checksum, write_library, write_library_dry_run, LibraryWriteResult,
        StoreError,
    };
    use crate::model::{AdmissionStatus, LibraryKey, TokenWrite};
    use manager_contracts::TokenKeyId;
    use rusqlite::Connection;

    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const USDE: &str = "0x2222222222222222222222222222222222222222";
    const STOCK: &str = "0x3333333333333333333333333333333333333333";
    const TZ: &str = "0x4444444444444444444444444444444444444444";

    fn test_connection() -> (tempfile::TempDir, Connection) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = Connection::open(path).unwrap();
        (directory, connection)
    }

    fn token_write(address: &str, symbol: &str, admission_status: AdmissionStatus) -> TokenWrite {
        TokenWrite {
            network: "robinhood".to_owned(),
            chain_id: 4663,
            token_key_id: TokenKeyId::new("robinhood", 4663, address).unwrap(),
            v4_currency_key: address.to_owned(),
            token_address: address.to_owned(),
            token_symbol: symbol.to_owned(),
            token_name: Some(symbol.to_owned()),
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: false,
            admission_status,
            admission_source: "unit-test:allowlist".to_owned(),
            metadata_source: "unit-test".to_owned(),
            metadata_checksum: format!("sha256:{symbol:0>64}"),
            source_json: Some(r#"{"fixture":"unit-test"}"#.to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    fn write(
        connection: &mut Connection,
        library: LibraryKey,
        tokens: Vec<TokenWrite>,
    ) -> Result<LibraryWriteResult, StoreError> {
        write_library(
            connection,
            library,
            "robinhood",
            4663,
            tokens,
            Some("fixture:v1"),
            "unit-test",
            "bootstrap",
        )
    }

    #[test]
    fn initial_write_materializes_rows_state_and_events() {
        let (_directory, mut connection) = test_connection();
        let result = write(
            &mut connection,
            LibraryKey::Core,
            vec![
                token_write(USDG, "USDG", AdmissionStatus::Admitted),
                token_write(USDE, "USDE", AdmissionStatus::Admitted),
            ],
        )
        .unwrap();

        assert_eq!(result.inserted, 2);
        assert_eq!(result.updated, 0);
        assert_eq!(result.unchanged, 0);
        assert_eq!(result.events, 2);
        assert_eq!(result.version_before, 0);
        assert_eq!(result.version_after, 1);
        assert!(result.metadata_checksum.starts_with("sha256:"));

        let token_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_core", [], |row| row.get(0))
            .unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        let state: (i64, i64, i64, String) = connection
            .query_row(
                "SELECT library_version, token_count, admitted_count, metadata_checksum
                 FROM token_library_state
                 WHERE library_key = 'core'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .unwrap();
        assert_eq!(token_count, 2);
        assert_eq!(event_count, 2);
        assert_eq!(state, (1, 2, 2, result.metadata_checksum));
    }

    #[test]
    fn identical_write_is_idempotent() {
        let (_directory, mut connection) = test_connection();
        let tokens = vec![
            token_write(USDG, "USDG", AdmissionStatus::Admitted),
            token_write(USDE, "USDE", AdmissionStatus::Admitted),
        ];
        let first = write(&mut connection, LibraryKey::Core, tokens.clone()).unwrap();
        let second = write(&mut connection, LibraryKey::Core, tokens).unwrap();

        assert_eq!(second.inserted, 0);
        assert_eq!(second.updated, 0);
        assert_eq!(second.unchanged, 2);
        assert_eq!(second.events, 0);
        assert_eq!(second.version_after, first.version_after);
        assert_eq!(second.metadata_checksum, first.metadata_checksum);

        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(event_count, 2);
    }

    #[test]
    fn dry_run_returns_apply_checksum_without_persisting_rows() {
        let (_directory, mut connection) = test_connection();
        let tokens = vec![
            token_write(USDG, "USDG", AdmissionStatus::Admitted),
            token_write(USDE, "USDE", AdmissionStatus::Admitted),
        ];
        let dry_run = write_library_dry_run(
            &mut connection,
            LibraryKey::Core,
            "robinhood",
            4663,
            tokens.clone(),
            Some("fixture:v1"),
            "unit-test",
            "bootstrap",
        )
        .unwrap();

        let token_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_core", [], |row| row.get(0))
            .unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(token_count, 0);
        assert_eq!(event_count, 0);

        let apply = write(&mut connection, LibraryKey::Core, tokens).unwrap();
        assert_eq!(dry_run.inserted, apply.inserted);
        assert_eq!(dry_run.metadata_checksum, apply.metadata_checksum);
        assert_eq!(dry_run.version_after, apply.version_after);
    }

    #[test]
    fn admission_change_increments_version_and_writes_event() {
        let (_directory, mut connection) = test_connection();
        let first = write(
            &mut connection,
            LibraryKey::Core,
            vec![token_write(USDG, "USDG", AdmissionStatus::Admitted)],
        )
        .unwrap();

        let mut blocked = token_write(USDG, "USDG", AdmissionStatus::Blocked);
        blocked.blocked_reason = Some("operator review".to_owned());
        let second = write(&mut connection, LibraryKey::Core, vec![blocked]).unwrap();

        assert_eq!(second.updated, 1);
        assert_eq!(second.events, 1);
        assert_eq!(second.version_before, first.version_after);
        assert_eq!(second.version_after, first.version_after + 1);
        assert_ne!(second.metadata_checksum, first.metadata_checksum);

        let event: (Option<String>, String, String) = connection
            .query_row(
                "SELECT previous_status, new_status, reason
                 FROM token_admission_events
                 ORDER BY created_at DESC, rowid DESC
                 LIMIT 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(
            event,
            (
                Some("admitted".to_owned()),
                "blocked".to_owned(),
                "bootstrap".to_owned()
            )
        );
    }

    #[test]
    fn extension_membership_can_overlap_with_all_primary_libraries() {
        let (_directory, mut connection) = test_connection();
        write(
            &mut connection,
            LibraryKey::Core,
            vec![token_write(USDG, "USDG", AdmissionStatus::Admitted)],
        )
        .unwrap();
        write(
            &mut connection,
            LibraryKey::Stocks,
            vec![token_write(STOCK, "STOCK", AdmissionStatus::Admitted)],
        )
        .unwrap();
        write(
            &mut connection,
            LibraryKey::Test,
            vec![token_write(TZ, "TZ", AdmissionStatus::Admitted)],
        )
        .unwrap();

        let result = write(
            &mut connection,
            LibraryKey::Extension,
            vec![
                token_write(USDG, "USDG", AdmissionStatus::Admitted),
                token_write(STOCK, "STOCK", AdmissionStatus::CandidateOnly),
                token_write(TZ, "TZ", AdmissionStatus::CandidateOnly),
            ],
        )
        .unwrap();
        assert_eq!(result.inserted, 3);

        let extension_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_extension", [], |row| row.get(0))
            .unwrap();
        assert_eq!(extension_count, 3);
    }

    #[test]
    fn primary_libraries_are_mutually_exclusive() {
        let (_directory, mut connection) = test_connection();
        write(
            &mut connection,
            LibraryKey::Core,
            vec![token_write(USDG, "USDG", AdmissionStatus::Admitted)],
        )
        .unwrap();
        write(
            &mut connection,
            LibraryKey::Stocks,
            vec![token_write(STOCK, "STOCK", AdmissionStatus::Admitted)],
        )
        .unwrap();
        write(
            &mut connection,
            LibraryKey::Test,
            vec![token_write(TZ, "TZ", AdmissionStatus::Admitted)],
        )
        .unwrap();

        assert!(matches!(
            write(
                &mut connection,
                LibraryKey::Stocks,
                vec![token_write(USDG, "USDG", AdmissionStatus::Admitted)],
            )
            .unwrap_err(),
            StoreError::TokenLibraryConflict { .. }
        ));
        assert!(matches!(
            write(
                &mut connection,
                LibraryKey::Test,
                vec![token_write(STOCK, "STOCK", AdmissionStatus::Admitted)],
            )
            .unwrap_err(),
            StoreError::TokenLibraryConflict { .. }
        ));
        assert!(matches!(
            write(
                &mut connection,
                LibraryKey::Core,
                vec![token_write(TZ, "TZ", AdmissionStatus::Admitted)],
            )
            .unwrap_err(),
            StoreError::TokenLibraryConflict { .. }
        ));

        let stock_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_stocks", [], |row| row.get(0))
            .unwrap();
        let test_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_test", [], |row| row.get(0))
            .unwrap();
        let core_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_core", [], |row| row.get(0))
            .unwrap();
        assert_eq!(stock_count, 1);
        assert_eq!(test_count, 1);
        assert_eq!(core_count, 1);
    }

    #[test]
    fn inconsistent_token_identity_is_rejected() {
        let (_directory, mut connection) = test_connection();
        let mut token = token_write(USDG, "USDG", AdmissionStatus::Admitted);
        token.token_address = USDE.to_owned();
        token.v4_currency_key = USDE.to_owned();

        let error = write(&mut connection, LibraryKey::Core, vec![token]).unwrap_err();
        assert!(matches!(error, StoreError::InvalidToken(_)));
        let token_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_core", [], |row| row.get(0))
            .unwrap();
        assert_eq!(token_count, 0);
    }

    #[test]
    fn library_checksum_is_order_independent_and_deterministic() {
        let (_directory, mut connection) = test_connection();
        write(
            &mut connection,
            LibraryKey::Test,
            vec![
                token_write(TZ, "TZ", AdmissionStatus::Admitted),
                token_write(USDE, "USDE", AdmissionStatus::CandidateOnly),
            ],
        )
        .unwrap();
        let first =
            library_metadata_checksum(&connection, LibraryKey::Test, "robinhood", 4663).unwrap();
        let second =
            library_metadata_checksum(&connection, LibraryKey::Test, "robinhood", 4663).unwrap();
        assert_eq!(first, second);

        let other_directory = tempfile::tempdir().unwrap();
        let other_path = other_directory.path().join("manager.sqlite3");
        manager_db::migrate(&other_path).unwrap();
        let mut other_connection = Connection::open(other_path).unwrap();
        write(
            &mut other_connection,
            LibraryKey::Test,
            vec![
                token_write(USDE, "USDE", AdmissionStatus::CandidateOnly),
                token_write(TZ, "TZ", AdmissionStatus::Admitted),
            ],
        )
        .unwrap();
        let rebuilt =
            library_metadata_checksum(&other_connection, LibraryKey::Test, "robinhood", 4663)
                .unwrap();
        assert_eq!(first, rebuilt);
    }
}
