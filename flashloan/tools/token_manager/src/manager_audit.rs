use crate::model::LibraryKey;
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

const MAX_RECENT_EVENTS: u32 = 100;

#[derive(Debug)]
pub enum ManagerAuditError {
    InvalidRequest(String),
    DataIntegrity(String),
    Sqlite(rusqlite::Error),
}

impl fmt::Display for ManagerAuditError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidRequest(message) => write!(formatter, "invalid audit request: {message}"),
            Self::DataIntegrity(message) => {
                write!(formatter, "audit data integrity error: {message}")
            }
            Self::Sqlite(error) => write!(formatter, "audit sqlite error: {error}"),
        }
    }
}

impl std::error::Error for ManagerAuditError {}

impl From<rusqlite::Error> for ManagerAuditError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenStatusCounts {
    pub total: i64,
    pub admitted: i64,
    pub blocked: i64,
    pub candidate_only: i64,
    pub pending_review: i64,
}

impl TokenStatusCounts {
    fn add_assign(&mut self, other: &Self) {
        self.total += other.total;
        self.admitted += other.admitted;
        self.blocked += other.blocked;
        self.candidate_only += other.candidate_only;
        self.pending_review += other.pending_review;
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenLibraryStateAudit {
    pub library_version: i64,
    pub token_count: i64,
    pub admitted_count: i64,
    pub metadata_checksum: String,
    pub source_checkpoint: Option<String>,
    pub generated_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenLibraryAudit {
    pub library_key: LibraryKey,
    pub counts: TokenStatusCounts,
    pub state: Option<TokenLibraryStateAudit>,
    pub state_matches_rows: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DuplicateTokenSymbol {
    pub symbol: String,
    pub token_key_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PrimaryLibraryConflictAudit {
    pub token_key_id: String,
    pub library_keys: Vec<LibraryKey>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AdmissionSourceCount {
    pub library_key: LibraryKey,
    pub admission_source: String,
    pub token_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AdmissionEventAudit {
    pub event_id: String,
    pub token_key_id: String,
    pub library_key: String,
    pub previous_status: Option<String>,
    pub new_status: String,
    pub reason: String,
    pub actor: String,
    pub evidence: Value,
    pub created_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenManagerAudit {
    pub network: String,
    pub chain_id: u64,
    pub membership_count: i64,
    pub distinct_token_count: i64,
    pub status_totals: TokenStatusCounts,
    pub libraries: Vec<TokenLibraryAudit>,
    pub duplicate_symbols: Vec<DuplicateTokenSymbol>,
    pub primary_conflicts: Vec<PrimaryLibraryConflictAudit>,
    pub admission_sources: Vec<AdmissionSourceCount>,
    pub admission_event_count: i64,
    pub recent_events: Vec<AdmissionEventAudit>,
    pub recent_event_limit: u32,
}

pub fn audit_manager_database(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
    recent_event_limit: u32,
) -> Result<TokenManagerAudit, ManagerAuditError> {
    if network.trim().is_empty() || chain_id == 0 {
        return Err(ManagerAuditError::InvalidRequest(
            "network and chain_id are required".to_owned(),
        ));
    }
    if recent_event_limit > MAX_RECENT_EVENTS {
        return Err(ManagerAuditError::InvalidRequest(format!(
            "recent_event_limit must not exceed {MAX_RECENT_EVENTS}"
        )));
    }

    let connection = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;

    let mut libraries = Vec::with_capacity(4);
    let mut status_totals = TokenStatusCounts::default();
    for library in [
        LibraryKey::Core,
        LibraryKey::Stocks,
        LibraryKey::Extension,
        LibraryKey::Test,
    ] {
        let counts = load_status_counts(&connection, network, chain_id, library)?;
        status_totals.add_assign(&counts);
        let state = load_library_state(&connection, network, chain_id, library)?;
        let state_matches_rows = state.as_ref().map(|state| {
            state.token_count == counts.total && state.admitted_count == counts.admitted
        });
        libraries.push(TokenLibraryAudit {
            library_key: library,
            counts,
            state,
            state_matches_rows,
        });
    }

    let distinct_token_count = connection.query_row(
        "SELECT COUNT(DISTINCT token_key_id)
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2",
        params![network, chain_id],
        |row| row.get(0),
    )?;
    let duplicate_symbols = load_duplicate_symbols(&connection, network, chain_id)?;
    let primary_conflicts = load_primary_conflicts(&connection, network, chain_id)?;
    let admission_sources = load_admission_sources(&connection, network, chain_id)?;
    let admission_event_count = connection.query_row(
        "SELECT COUNT(*)
         FROM token_admission_events
         WHERE network = ?1 AND chain_id = ?2",
        params![network, chain_id],
        |row| row.get(0),
    )?;
    let recent_events = load_recent_events(&connection, network, chain_id, recent_event_limit)?;

    Ok(TokenManagerAudit {
        network: network.to_owned(),
        chain_id,
        membership_count: status_totals.total,
        distinct_token_count,
        status_totals,
        libraries,
        duplicate_symbols,
        primary_conflicts,
        admission_sources,
        admission_event_count,
        recent_events,
        recent_event_limit,
    })
}

fn load_status_counts(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    library: LibraryKey,
) -> Result<TokenStatusCounts, ManagerAuditError> {
    let query = format!(
        "SELECT admission_status, COUNT(*)
         FROM {}
         WHERE network = ?1 AND chain_id = ?2
         GROUP BY admission_status
         ORDER BY admission_status",
        library.table_name()
    );
    let mut statement = connection.prepare(&query)?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    })?;
    let mut counts = TokenStatusCounts::default();
    for row in rows {
        let (status, count) = row?;
        match status.as_str() {
            "admitted" => counts.admitted = count,
            "blocked" => counts.blocked = count,
            "candidate_only" => counts.candidate_only = count,
            "pending_review" => counts.pending_review = count,
            other => {
                return Err(ManagerAuditError::DataIntegrity(format!(
                    "library {} contains unknown admission status {other}",
                    library.as_str()
                )));
            }
        }
        counts.total += count;
    }
    Ok(counts)
}

fn load_library_state(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    library: LibraryKey,
) -> Result<Option<TokenLibraryStateAudit>, ManagerAuditError> {
    Ok(connection
        .query_row(
            "SELECT library_version, token_count, admitted_count, metadata_checksum,
                    source_checkpoint, generated_at, updated_at
             FROM token_library_state
             WHERE network = ?1 AND chain_id = ?2 AND library_key = ?3",
            params![network, chain_id, library.as_str()],
            |row| {
                Ok(TokenLibraryStateAudit {
                    library_version: row.get(0)?,
                    token_count: row.get(1)?,
                    admitted_count: row.get(2)?,
                    metadata_checksum: row.get(3)?,
                    source_checkpoint: row.get(4)?,
                    generated_at: row.get(5)?,
                    updated_at: row.get(6)?,
                })
            },
        )
        .optional()?)
}

fn load_duplicate_symbols(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<DuplicateTokenSymbol>, ManagerAuditError> {
    let mut statement = connection.prepare(
        "SELECT token_symbol, token_key_id
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY token_symbol, token_key_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut by_symbol = BTreeMap::<String, Vec<String>>::new();
    for row in rows {
        let (symbol, token_key_id) = row?;
        by_symbol.entry(symbol).or_default().push(token_key_id);
    }
    Ok(by_symbol
        .into_iter()
        .filter_map(|(symbol, mut token_key_ids)| {
            token_key_ids.sort();
            token_key_ids.dedup();
            (token_key_ids.len() > 1).then_some(DuplicateTokenSymbol {
                symbol,
                token_key_ids,
            })
        })
        .collect())
}

fn load_primary_conflicts(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<PrimaryLibraryConflictAudit>, ManagerAuditError> {
    let mut statement = connection.prepare(
        "SELECT token_key_id, library_key
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2
           AND library_key IN ('core', 'stocks', 'test')
         ORDER BY token_key_id, library_key",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut by_token = BTreeMap::<String, Vec<LibraryKey>>::new();
    for row in rows {
        let (token_key_id, library_key) = row?;
        let library = LibraryKey::from_key(&library_key).ok_or_else(|| {
            ManagerAuditError::DataIntegrity(format!("unknown library key {library_key}"))
        })?;
        by_token.entry(token_key_id).or_default().push(library);
    }
    Ok(by_token
        .into_iter()
        .filter_map(|(token_key_id, mut library_keys)| {
            library_keys.sort();
            library_keys.dedup();
            (library_keys.len() > 1).then_some(PrimaryLibraryConflictAudit {
                token_key_id,
                library_keys,
            })
        })
        .collect())
}

fn load_admission_sources(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<AdmissionSourceCount>, ManagerAuditError> {
    let mut statement = connection.prepare(
        "SELECT library_key, admission_source, COUNT(*)
         FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2
         GROUP BY library_key, admission_source
         ORDER BY library_key, COUNT(*) DESC, admission_source",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, i64>(2)?,
        ))
    })?;
    let mut sources = Vec::new();
    for row in rows {
        let (library_key, admission_source, token_count) = row?;
        sources.push(AdmissionSourceCount {
            library_key: LibraryKey::from_key(&library_key).ok_or_else(|| {
                ManagerAuditError::DataIntegrity(format!("unknown library key {library_key}"))
            })?,
            admission_source,
            token_count,
        });
    }
    Ok(sources)
}

fn load_recent_events(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    limit: u32,
) -> Result<Vec<AdmissionEventAudit>, ManagerAuditError> {
    let mut statement = connection.prepare(
        "SELECT event_id, token_key_id, library_key, previous_status, new_status,
                reason, actor, evidence_json, created_at
         FROM token_admission_events
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY created_at DESC, event_id DESC
         LIMIT ?3",
    )?;
    let rows = statement.query_map(params![network, chain_id, limit], |row| {
        let evidence_json = row.get::<_, String>(7)?;
        Ok(AdmissionEventAudit {
            event_id: row.get(0)?,
            token_key_id: row.get(1)?,
            library_key: row.get(2)?,
            previous_status: row.get(3)?,
            new_status: row.get(4)?,
            reason: row.get(5)?,
            actor: row.get(6)?,
            evidence: serde_json::from_str(&evidence_json).unwrap_or(Value::String(evidence_json)),
            created_at: row.get(8)?,
        })
    })?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(ManagerAuditError::from)
}

#[cfg(test)]
mod tests {
    use super::audit_manager_database;
    use crate::model::{AdmissionStatus, LibraryKey, TokenWrite};
    use crate::store::write_library;
    use manager_contracts::TokenKeyId;
    use rusqlite::Connection;

    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const STOCK: &str = "0x2222222222222222222222222222222222222222";
    const EXTENSION: &str = "0x3333333333333333333333333333333333333333";

    fn fixture(address: &str, symbol: &str, status: AdmissionStatus) -> TokenWrite {
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
            admission_status: status,
            admission_source: "fixture".to_owned(),
            metadata_source: "fixture".to_owned(),
            metadata_checksum: format!("sha256:{address}"),
            source_json: Some("{}".to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = Connection::open(&path).unwrap();
        write_library(
            &mut connection,
            LibraryKey::Core,
            "robinhood",
            4663,
            vec![fixture(USDG, "USDG", AdmissionStatus::Blocked)],
            Some("fixture:v1"),
            "test",
            "fixture",
        )
        .unwrap();
        write_library(
            &mut connection,
            LibraryKey::Stocks,
            "robinhood",
            4663,
            vec![fixture(STOCK, "USDG", AdmissionStatus::Admitted)],
            Some("fixture:v1"),
            "test",
            "fixture",
        )
        .unwrap();
        write_library(
            &mut connection,
            LibraryKey::Extension,
            "robinhood",
            4663,
            vec![
                fixture(USDG, "USDG", AdmissionStatus::Blocked),
                fixture(EXTENSION, "EX", AdmissionStatus::PendingReview),
            ],
            Some("fixture:v1"),
            "test",
            "fixture",
        )
        .unwrap();
        drop(connection);
        (directory, path)
    }

    #[test]
    fn audit_reports_library_state_duplicates_sources_and_recent_events_read_only() {
        let (_directory, path) = database();
        let before = std::fs::metadata(&path).unwrap().len();
        let report = audit_manager_database(&path, "robinhood", 4663, 10).unwrap();
        let after = std::fs::metadata(&path).unwrap().len();

        assert_eq!(report.libraries.len(), 4);
        assert_eq!(report.membership_count, 4);
        assert_eq!(report.distinct_token_count, 3);
        assert_eq!(report.status_totals.admitted, 1);
        assert_eq!(report.status_totals.blocked, 2);
        assert_eq!(report.status_totals.pending_review, 1);
        assert_eq!(report.duplicate_symbols.len(), 1);
        assert_eq!(report.duplicate_symbols[0].symbol, "USDG");
        assert_eq!(report.duplicate_symbols[0].token_key_ids.len(), 2);
        assert!(report.primary_conflicts.is_empty());
        assert!(report
            .admission_sources
            .iter()
            .any(|source| source.admission_source == "fixture"));
        assert_eq!(report.admission_event_count, 4);
        assert_eq!(report.recent_events.len(), 4);
        assert!(report.recent_events[0].evidence.is_object());
        assert_eq!(before, after);
    }

    #[test]
    fn audit_rejects_invalid_recent_event_limit() {
        let (_directory, path) = database();
        let error = audit_manager_database(&path, "robinhood", 4663, 101).unwrap_err();
        assert!(error.to_string().contains("must not exceed 100"));
    }
}
