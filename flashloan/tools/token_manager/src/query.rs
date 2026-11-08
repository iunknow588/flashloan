use crate::model::{AdmissionStatus, LibraryKey};
use crate::sources::{open_read_only, SourceError};
use rusqlite::{params, OptionalExtension};
use serde::Serialize;
use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

const ALL_LIBRARIES: [LibraryKey; 4] = [
    LibraryKey::Core,
    LibraryKey::Stocks,
    LibraryKey::Extension,
    LibraryKey::Test,
];

#[derive(Debug)]
pub enum TokenQueryError {
    Source(SourceError),
    Sqlite(rusqlite::Error),
    InvalidSymbol(String),
    InvalidStoredValue(String),
    LibraryStateMissing {
        network: String,
        chain_id: u64,
        library: LibraryKey,
    },
}

impl fmt::Display for TokenQueryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Source(error) => write!(formatter, "token query source error: {error}"),
            Self::Sqlite(error) => write!(formatter, "token query sqlite error: {error}"),
            Self::InvalidSymbol(symbol) => write!(formatter, "invalid token symbol: {symbol}"),
            Self::InvalidStoredValue(message) => {
                write!(formatter, "invalid stored token value: {message}")
            }
            Self::LibraryStateMissing {
                network,
                chain_id,
                library,
            } => write!(
                formatter,
                "token library state is missing for {network}:{chain_id}:{}",
                library.as_str()
            ),
        }
    }
}

impl std::error::Error for TokenQueryError {}

impl From<SourceError> for TokenQueryError {
    fn from(error: SourceError) -> Self {
        Self::Source(error)
    }
}

impl From<rusqlite::Error> for TokenQueryError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenView {
    pub token_key_id: String,
    pub v4_currency_key: String,
    pub token_address: String,
    pub symbol: String,
    pub name: Option<String>,
    pub decimals: u8,
    pub token_kind: String,
    pub is_native: bool,
    pub is_wrapped_native: bool,
    pub is_stable: bool,
    pub admission_status: AdmissionStatus,
    pub admission_source: String,
    pub metadata_source: String,
    pub metadata_checksum: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenLibraryPage {
    pub network: String,
    pub chain_id: u64,
    pub library_key: LibraryKey,
    pub library_version: i64,
    pub library_token_count: i64,
    pub library_admitted_count: i64,
    pub metadata_checksum: String,
    pub source_checkpoint: Option<String>,
    pub tokens: Vec<TokenView>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenLibraryState {
    pub network: String,
    pub chain_id: u64,
    pub library_key: LibraryKey,
    pub library_version: i64,
    pub token_count: i64,
    pub admitted_count: i64,
    pub metadata_checksum: String,
    pub source_checkpoint: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenLibraryMembership {
    pub library_key: LibraryKey,
    pub admission_status: AdmissionStatus,
    pub admission_source: String,
    pub metadata_source: String,
    pub metadata_checksum: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResolvedToken {
    pub token_key_id: String,
    pub v4_currency_key: String,
    pub token_address: String,
    pub symbol: String,
    pub name: Option<String>,
    pub decimals: u8,
    pub token_kind: String,
    pub is_native: bool,
    pub is_wrapped_native: bool,
    pub is_stable: bool,
    pub memberships: Vec<TokenLibraryMembership>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum TokenSymbolResolution {
    Unique { token: ResolvedToken },
    NotFound,
    Ambiguous { candidates: Vec<ResolvedToken> },
}

pub fn read_library_tokens(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
    library: LibraryKey,
    admission_status: Option<AdmissionStatus>,
) -> Result<TokenLibraryPage, TokenQueryError> {
    let connection = open_read_only(database_path)?;
    let (
        library_version,
        library_token_count,
        library_admitted_count,
        metadata_checksum,
        source_checkpoint,
    ) = connection
        .query_row(
            "SELECT library_version, token_count, admitted_count,
                        metadata_checksum, source_checkpoint
                 FROM token_library_state
                 WHERE network = ?1 AND chain_id = ?2 AND library_key = ?3",
            params![network, chain_id, library.as_str()],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                ))
            },
        )
        .optional()?
        .ok_or_else(|| TokenQueryError::LibraryStateMissing {
            network: network.to_owned(),
            chain_id,
            library,
        })?;

    let base_query = format!(
        "SELECT token_key_id, v4_currency_key, token_address, token_symbol,
                token_name, token_decimals, token_kind, is_native,
                is_wrapped_native, is_stable, admission_status, admission_source,
                metadata_source, metadata_checksum
         FROM {}
         WHERE network = ?1 AND chain_id = ?2",
        library.table_name()
    );
    let mut tokens = Vec::new();
    if let Some(admission_status) = admission_status {
        let query =
            format!("{base_query} AND admission_status = ?3 ORDER BY token_symbol, token_key_id");
        let mut statement = connection.prepare(&query)?;
        let rows = statement.query_map(
            params![network, chain_id, admission_status.as_str()],
            map_token_view,
        )?;
        for row in rows {
            tokens.push(row?);
        }
    } else {
        let query = format!("{base_query} ORDER BY token_symbol, token_key_id");
        let mut statement = connection.prepare(&query)?;
        let rows = statement.query_map(params![network, chain_id], map_token_view)?;
        for row in rows {
            tokens.push(row?);
        }
    }

    Ok(TokenLibraryPage {
        network: network.to_owned(),
        chain_id,
        library_key: library,
        library_version,
        library_token_count,
        library_admitted_count,
        metadata_checksum,
        source_checkpoint,
        tokens,
    })
}

pub fn read_library_state(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
    library: LibraryKey,
) -> Result<TokenLibraryState, TokenQueryError> {
    let connection = open_read_only(database_path)?;
    connection
        .query_row(
            "SELECT library_version, token_count, admitted_count,
                    metadata_checksum, source_checkpoint
             FROM token_library_state
             WHERE network = ?1 AND chain_id = ?2 AND library_key = ?3",
            params![network, chain_id, library.as_str()],
            |row| {
                Ok(TokenLibraryState {
                    network: network.to_owned(),
                    chain_id,
                    library_key: library,
                    library_version: row.get(0)?,
                    token_count: row.get(1)?,
                    admitted_count: row.get(2)?,
                    metadata_checksum: row.get(3)?,
                    source_checkpoint: row.get(4)?,
                })
            },
        )
        .optional()?
        .ok_or_else(|| TokenQueryError::LibraryStateMissing {
            network: network.to_owned(),
            chain_id,
            library,
        })
}

pub fn resolve_token_symbol(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
    symbol: &str,
    required_status: Option<AdmissionStatus>,
) -> Result<TokenSymbolResolution, TokenQueryError> {
    let symbol = symbol.trim().to_ascii_uppercase();
    if symbol.is_empty() || crate::metadata::is_placeholder_symbol(&symbol) {
        return Err(TokenQueryError::InvalidSymbol(symbol));
    }

    let connection = open_read_only(database_path)?;
    let mut candidates = BTreeMap::<String, ResolvedToken>::new();
    for library in ALL_LIBRARIES {
        let base_query = format!(
            "SELECT token_key_id, v4_currency_key, token_address, token_symbol,
                    token_name, token_decimals, token_kind, is_native,
                    is_wrapped_native, is_stable, admission_status, admission_source,
                    metadata_source, metadata_checksum
             FROM {}
             WHERE network = ?1 AND chain_id = ?2 AND token_symbol = ?3",
            library.table_name()
        );
        if let Some(required_status) = required_status {
            let query = format!("{base_query} AND admission_status = ?4 ORDER BY token_key_id");
            let mut statement = connection.prepare(&query)?;
            let rows = statement.query_map(
                params![network, chain_id, symbol, required_status.as_str()],
                map_token_view,
            )?;
            for row in rows {
                add_resolution_membership(&mut candidates, library, row?);
            }
        } else {
            let query = format!("{base_query} ORDER BY token_key_id");
            let mut statement = connection.prepare(&query)?;
            let rows = statement.query_map(params![network, chain_id, symbol], map_token_view)?;
            for row in rows {
                add_resolution_membership(&mut candidates, library, row?);
            }
        }
    }

    let candidates = candidates.into_values().collect::<Vec<_>>();
    Ok(match candidates.len() {
        0 => TokenSymbolResolution::NotFound,
        1 => TokenSymbolResolution::Unique {
            token: candidates.into_iter().next().expect("length checked"),
        },
        _ => TokenSymbolResolution::Ambiguous { candidates },
    })
}

fn map_token_view(row: &rusqlite::Row<'_>) -> rusqlite::Result<TokenView> {
    let admission_status: String = row.get(10)?;
    Ok(TokenView {
        token_key_id: row.get(0)?,
        v4_currency_key: row.get(1)?,
        token_address: row.get(2)?,
        symbol: row.get(3)?,
        name: row.get(4)?,
        decimals: stored_decimals(row.get(5)?)?,
        token_kind: row.get(6)?,
        is_native: row.get::<_, i64>(7)? != 0,
        is_wrapped_native: row.get::<_, i64>(8)? != 0,
        is_stable: row.get::<_, i64>(9)? != 0,
        admission_status: stored_status(&admission_status)?,
        admission_source: row.get(11)?,
        metadata_source: row.get(12)?,
        metadata_checksum: row.get(13)?,
    })
}

fn stored_status(value: &str) -> rusqlite::Result<AdmissionStatus> {
    AdmissionStatus::from_storage(value).ok_or_else(|| {
        rusqlite::Error::FromSqlConversionFailure(
            10,
            rusqlite::types::Type::Text,
            Box::new(TokenQueryError::InvalidStoredValue(format!(
                "unknown admission_status {value}"
            ))),
        )
    })
}

fn stored_decimals(value: i64) -> rusqlite::Result<u8> {
    u8::try_from(value).map_err(|_| {
        rusqlite::Error::FromSqlConversionFailure(
            5,
            rusqlite::types::Type::Integer,
            Box::new(TokenQueryError::InvalidStoredValue(format!(
                "token_decimals is outside u8: {value}"
            ))),
        )
    })
}

fn add_resolution_membership(
    candidates: &mut BTreeMap<String, ResolvedToken>,
    library: LibraryKey,
    token: TokenView,
) {
    let membership = TokenLibraryMembership {
        library_key: library,
        admission_status: token.admission_status,
        admission_source: token.admission_source,
        metadata_source: token.metadata_source,
        metadata_checksum: token.metadata_checksum,
    };
    candidates
        .entry(token.token_key_id.clone())
        .and_modify(|candidate| {
            if !candidate
                .memberships
                .iter()
                .any(|existing| existing.library_key == library)
            {
                candidate.memberships.push(membership.clone());
            }
        })
        .or_insert_with(|| ResolvedToken {
            token_key_id: token.token_key_id,
            v4_currency_key: token.v4_currency_key,
            token_address: token.token_address,
            symbol: token.symbol,
            name: token.name,
            decimals: token.decimals,
            token_kind: token.token_kind,
            is_native: token.is_native,
            is_wrapped_native: token.is_wrapped_native,
            is_stable: token.is_stable,
            memberships: vec![membership],
        });
}

#[cfg(test)]
mod tests {
    use super::{read_library_tokens, resolve_token_symbol, TokenSymbolResolution};
    use crate::model::{AdmissionStatus, LibraryKey, TokenWrite};
    use crate::store::write_library;
    use manager_contracts::TokenKeyId;
    use rusqlite::Connection;

    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const USDE: &str = "0x2222222222222222222222222222222222222222";

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
            metadata_checksum: format!("sha256:{symbol:0>64}"),
            source_json: Some("{}".to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    fn database_with_tokens() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = Connection::open(&path).unwrap();
        write_library(
            &mut connection,
            LibraryKey::Core,
            "robinhood",
            4663,
            vec![fixture(USDG, "USDG", AdmissionStatus::Admitted)],
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
                fixture(USDG, "USDG", AdmissionStatus::CandidateOnly),
                fixture(USDE, "USDG", AdmissionStatus::CandidateOnly),
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
    fn library_query_filters_status_and_uses_persisted_state() {
        let (_directory, database) = database_with_tokens();
        let page = read_library_tokens(
            &database,
            "robinhood",
            4663,
            LibraryKey::Core,
            Some(AdmissionStatus::Admitted),
        )
        .unwrap();

        assert_eq!(page.library_key, LibraryKey::Core);
        assert_eq!(page.library_version, 1);
        assert_eq!(page.tokens.len(), 1);
        assert_eq!(page.tokens[0].symbol, "USDG");
        assert_eq!(page.source_checkpoint.as_deref(), Some("fixture:v1"));
    }

    #[test]
    fn symbol_resolution_deduplicates_overlap_but_rejects_distinct_addresses() {
        let (_directory, database) = database_with_tokens();
        let unique = resolve_token_symbol(
            &database,
            "robinhood",
            4663,
            "usdg",
            Some(AdmissionStatus::Admitted),
        )
        .unwrap();
        let TokenSymbolResolution::Unique { token } = unique else {
            panic!("expected unique admitted result");
        };
        assert_eq!(token.token_address, USDG);
        assert_eq!(token.memberships.len(), 1);

        let ambiguity = resolve_token_symbol(&database, "robinhood", 4663, "USDG", None).unwrap();
        let TokenSymbolResolution::Ambiguous { candidates } = ambiguity else {
            panic!("expected ambiguity across distinct addresses");
        };
        assert_eq!(candidates.len(), 2);
        assert!(candidates
            .iter()
            .any(|candidate| candidate.token_address == USDG && candidate.memberships.len() == 2));
        assert!(candidates
            .iter()
            .any(|candidate| candidate.token_address == USDE));
    }

    #[test]
    fn missing_symbol_returns_not_found_and_invalid_symbol_is_explicit() {
        let (_directory, database) = database_with_tokens();
        assert_eq!(
            resolve_token_symbol(&database, "robinhood", 4663, "NOPE", None).unwrap(),
            TokenSymbolResolution::NotFound
        );
        assert!(
            resolve_token_symbol(&database, "robinhood", 4663, "0x12345678", None)
                .unwrap_err()
                .to_string()
                .contains("invalid token symbol")
        );
    }
}
