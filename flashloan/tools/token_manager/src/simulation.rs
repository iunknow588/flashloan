use crate::classification::ClassificationResult;
use crate::model::LibraryKey;
use crate::sources::{ROBINHOOD_CHAIN_ID, ROBINHOOD_NETWORK};
use crate::store::{write_library, write_library_dry_run, LibraryWriteResult, StoreError};
use manager_db::MigrationReport;
use rusqlite::Connection;
use serde::Serialize;
use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

const LIBRARY_ORDER: [LibraryKey; 4] = [
    LibraryKey::Core,
    LibraryKey::Stocks,
    LibraryKey::Test,
    LibraryKey::Extension,
];

#[derive(Debug)]
pub enum BootstrapSimulationError {
    Migration(manager_db::MigrationError),
    Sqlite(rusqlite::Error),
    Store(StoreError),
    Io(std::io::Error),
    Json(serde_json::Error),
    GateFailed(String),
}

impl fmt::Display for BootstrapSimulationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Migration(error) => write!(formatter, "bootstrap migration failed: {error}"),
            Self::Sqlite(error) => write!(formatter, "bootstrap sqlite check failed: {error}"),
            Self::Store(error) => write!(formatter, "bootstrap token write failed: {error}"),
            Self::Io(error) => write!(formatter, "bootstrap temporary database failed: {error}"),
            Self::Json(error) => {
                write!(formatter, "bootstrap report serialization failed: {error}")
            }
            Self::GateFailed(message) => write!(formatter, "bootstrap gate failed: {message}"),
        }
    }
}

impl std::error::Error for BootstrapSimulationError {}

impl From<manager_db::MigrationError> for BootstrapSimulationError {
    fn from(error: manager_db::MigrationError) -> Self {
        Self::Migration(error)
    }
}

impl From<rusqlite::Error> for BootstrapSimulationError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<StoreError> for BootstrapSimulationError {
    fn from(error: StoreError) -> Self {
        Self::Store(error)
    }
}

impl From<std::io::Error> for BootstrapSimulationError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<serde_json::Error> for BootstrapSimulationError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapMigrationEvidence {
    pub first: MigrationReport,
    pub second: MigrationReport,
    pub first_applied_now: bool,
    pub second_was_noop: bool,
    pub checksum_stable: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibrarySimulationEvidence {
    pub library: LibraryKey,
    pub proposed_writes: usize,
    pub dry_run: LibraryWriteResult,
    pub first_apply: LibraryWriteResult,
    pub repeated_apply: LibraryWriteResult,
    pub rows_before_dry_run: i64,
    pub events_before_dry_run: i64,
    pub rows_after_dry_run: i64,
    pub events_after_dry_run: i64,
    pub rows_after_first_apply: i64,
    pub events_after_first_apply: i64,
    pub rows_after_repeated_apply: i64,
    pub events_after_repeated_apply: i64,
    pub dry_run_equals_first_apply: bool,
    pub dry_run_persisted_no_rows: bool,
    pub repeated_apply_was_idempotent: bool,
    pub checksum_stable: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapSimulationGates {
    pub temp_database_only: bool,
    pub second_migration_was_noop: bool,
    pub migration_checksum_stable: bool,
    pub dry_runs_persisted_no_rows: bool,
    pub all_dry_run_results_match_first_apply: bool,
    pub all_repeated_applies_are_idempotent: bool,
    pub all_library_checksums_stable: bool,
    pub primary_library_duplicate_count: usize,
    pub pending_or_blocked_downstream_visible_count: usize,
    pub quick_check_passed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapSimulationReport {
    pub milestone: &'static str,
    pub task: &'static str,
    pub mode: &'static str,
    pub network: &'static str,
    pub chain_id: u64,
    pub source_checkpoint: String,
    pub temporary_database: String,
    pub temporary_database_disposed: bool,
    pub migration: BootstrapMigrationEvidence,
    pub proposed_memberships: usize,
    pub admission_memberships: BTreeMap<String, BTreeMap<String, usize>>,
    pub libraries: Vec<LibrarySimulationEvidence>,
    pub final_row_counts: BTreeMap<String, i64>,
    pub final_event_count: i64,
    pub final_library_state_count: i64,
    pub quick_check: String,
    pub gates: BootstrapSimulationGates,
    pub passed: bool,
}

pub fn simulate_bootstrap_in_temporary_database(
    classification: &ClassificationResult,
    source_checkpoint: &str,
) -> Result<BootstrapSimulationReport, BootstrapSimulationError> {
    let directory = tempfile::tempdir()?;
    let database_path = directory.path().join("manager-bootstrap-sim.sqlite3");
    let mut report = simulate_bootstrap_at_path(&database_path, classification, source_checkpoint)?;
    report.temporary_database = "[temporary database disposed]".to_owned();
    report.temporary_database_disposed = true;
    drop(directory);
    Ok(report)
}

fn simulate_bootstrap_at_path(
    database_path: &Path,
    classification: &ClassificationResult,
    source_checkpoint: &str,
) -> Result<BootstrapSimulationReport, BootstrapSimulationError> {
    let first_migration = manager_db::migrate(database_path)?;
    let second_migration = manager_db::migrate(database_path)?;
    let migration = BootstrapMigrationEvidence {
        first_applied_now: first_migration.applied_now,
        second_was_noop: !second_migration.applied_now,
        checksum_stable: first_migration.checksum == second_migration.checksum,
        first: first_migration,
        second: second_migration,
    };

    let mut connection = Connection::open(database_path)?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    let writes_by_library = classification.writes_by_library();
    let mut dry_runs = BTreeMap::new();

    for library in LIBRARY_ORDER {
        let writes = writes_by_library.get(&library).cloned().unwrap_or_default();
        let rows_before_dry_run = library_row_count(&connection, library)?;
        let events_before_dry_run = event_count(&connection)?;
        let dry_run = write_library_dry_run(
            &mut connection,
            library,
            ROBINHOOD_NETWORK,
            ROBINHOOD_CHAIN_ID,
            writes.clone(),
            Some(source_checkpoint),
            "token-manager:bootstrap-simulate",
            "bootstrap dry-run",
        )?;
        let rows_after_dry_run = library_row_count(&connection, library)?;
        let events_after_dry_run = event_count(&connection)?;
        let dry_run_persisted_no_rows = rows_after_dry_run == rows_before_dry_run
            && events_after_dry_run == events_before_dry_run;
        dry_runs.insert(
            library,
            (
                dry_run,
                rows_before_dry_run,
                events_before_dry_run,
                rows_after_dry_run,
                events_after_dry_run,
                dry_run_persisted_no_rows,
            ),
        );
    }

    let mut libraries = Vec::with_capacity(LIBRARY_ORDER.len());
    for library in LIBRARY_ORDER {
        let writes = writes_by_library.get(&library).cloned().unwrap_or_default();
        let first_apply = write_library(
            &mut connection,
            library,
            ROBINHOOD_NETWORK,
            ROBINHOOD_CHAIN_ID,
            writes.clone(),
            Some(source_checkpoint),
            "token-manager:bootstrap-simulate",
            "bootstrap first apply",
        )?;
        let rows_after_first_apply = library_row_count(&connection, library)?;
        let events_after_first_apply = event_count(&connection)?;

        let repeated_apply = write_library(
            &mut connection,
            library,
            ROBINHOOD_NETWORK,
            ROBINHOOD_CHAIN_ID,
            writes,
            Some(source_checkpoint),
            "token-manager:bootstrap-simulate",
            "bootstrap repeated apply",
        )?;
        let rows_after_repeated_apply = library_row_count(&connection, library)?;
        let events_after_repeated_apply = event_count(&connection)?;
        let (
            dry_run,
            rows_before_dry_run,
            events_before_dry_run,
            rows_after_dry_run,
            events_after_dry_run,
            dry_run_persisted_no_rows,
        ) = dry_runs.remove(&library).ok_or_else(|| {
            BootstrapSimulationError::GateFailed(format!(
                "missing dry-run evidence for {}",
                library.as_str()
            ))
        })?;

        let dry_run_equals_first_apply = dry_run == first_apply;
        let repeated_apply_was_idempotent = repeated_apply.inserted == 0
            && repeated_apply.updated == 0
            && repeated_apply.unchanged == proposals_for_library(classification, library)
            && repeated_apply.events == 0
            && repeated_apply.version_after == first_apply.version_after
            && rows_after_repeated_apply == rows_after_first_apply
            && events_after_repeated_apply == events_after_first_apply;
        let checksum_stable = dry_run.metadata_checksum == first_apply.metadata_checksum
            && first_apply.metadata_checksum == repeated_apply.metadata_checksum;

        libraries.push(LibrarySimulationEvidence {
            library,
            proposed_writes: proposals_for_library(classification, library),
            dry_run,
            first_apply,
            repeated_apply,
            rows_before_dry_run,
            events_before_dry_run,
            rows_after_dry_run,
            events_after_dry_run,
            rows_after_first_apply,
            events_after_first_apply,
            rows_after_repeated_apply,
            events_after_repeated_apply,
            dry_run_equals_first_apply,
            dry_run_persisted_no_rows,
            repeated_apply_was_idempotent,
            checksum_stable,
        });
    }

    let final_row_counts = row_counts(&connection)?;
    let final_event_count = event_count(&connection)?;
    let final_library_state_count = library_state_count(&connection)?;
    let quick_check: String = connection.query_row("PRAGMA quick_check", [], |row| row.get(0))?;
    let admission_memberships = admission_memberships(classification);
    let primary_library_duplicate_count = primary_library_duplicate_count(classification);
    let pending_or_blocked_downstream_visible_count =
        pending_or_blocked_downstream_visible_count(&connection)?;
    let gates = BootstrapSimulationGates {
        temp_database_only: true,
        second_migration_was_noop: migration.second_was_noop,
        migration_checksum_stable: migration.checksum_stable,
        dry_runs_persisted_no_rows: libraries
            .iter()
            .all(|library| library.dry_run_persisted_no_rows),
        all_dry_run_results_match_first_apply: libraries
            .iter()
            .all(|library| library.dry_run_equals_first_apply),
        all_repeated_applies_are_idempotent: libraries
            .iter()
            .all(|library| library.repeated_apply_was_idempotent),
        all_library_checksums_stable: libraries.iter().all(|library| library.checksum_stable),
        primary_library_duplicate_count,
        pending_or_blocked_downstream_visible_count,
        quick_check_passed: quick_check == "ok",
    };
    validate_gates(&gates)?;

    Ok(BootstrapSimulationReport {
        milestone: "M1",
        task: "D1.1.7",
        mode: "temporary_database_bootstrap_dry_run_apply",
        network: ROBINHOOD_NETWORK,
        chain_id: ROBINHOOD_CHAIN_ID,
        source_checkpoint: source_checkpoint.to_owned(),
        temporary_database: database_path.display().to_string(),
        temporary_database_disposed: false,
        migration,
        proposed_memberships: libraries
            .iter()
            .map(|library| library.proposed_writes)
            .sum(),
        admission_memberships,
        libraries,
        final_row_counts,
        final_event_count,
        final_library_state_count,
        quick_check,
        gates,
        passed: true,
    })
}

fn proposals_for_library(classification: &ClassificationResult, library: LibraryKey) -> usize {
    classification
        .tokens
        .iter()
        .map(|token| token.to_token_writes_for_library(library).len())
        .sum()
}

fn row_counts(connection: &Connection) -> Result<BTreeMap<String, i64>, rusqlite::Error> {
    LIBRARY_ORDER
        .into_iter()
        .map(|library| {
            Ok((
                library.as_str().to_owned(),
                library_row_count(connection, library)?,
            ))
        })
        .collect()
}

fn library_row_count(connection: &Connection, library: LibraryKey) -> Result<i64, rusqlite::Error> {
    connection.query_row(
        &format!(
            "SELECT COUNT(*) FROM {} WHERE network = ?1 AND chain_id = ?2",
            library.table_name()
        ),
        rusqlite::params![ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID],
        |row| row.get(0),
    )
}

fn event_count(connection: &Connection) -> Result<i64, rusqlite::Error> {
    connection.query_row(
        "SELECT COUNT(*) FROM token_admission_events
         WHERE network = ?1 AND chain_id = ?2",
        rusqlite::params![ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID],
        |row| row.get(0),
    )
}

fn library_state_count(connection: &Connection) -> Result<i64, rusqlite::Error> {
    connection.query_row(
        "SELECT COUNT(*) FROM token_library_state
         WHERE network = ?1 AND chain_id = ?2",
        rusqlite::params![ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID],
        |row| row.get(0),
    )
}

fn admission_memberships(
    classification: &ClassificationResult,
) -> BTreeMap<String, BTreeMap<String, usize>> {
    let mut result = LIBRARY_ORDER
        .into_iter()
        .map(|library| (library.as_str().to_owned(), BTreeMap::new()))
        .collect::<BTreeMap<_, _>>();
    for token in &classification.tokens {
        for membership in &token.memberships {
            *result
                .entry(membership.library.as_str().to_owned())
                .or_default()
                .entry(membership.admission_status.as_str().to_owned())
                .or_default() += 1;
        }
    }
    result
}

fn primary_library_duplicate_count(classification: &ClassificationResult) -> usize {
    let mut primary_libraries = BTreeMap::<String, Vec<LibraryKey>>::new();
    for token in &classification.tokens {
        for membership in &token.memberships {
            if membership.library != LibraryKey::Extension {
                primary_libraries
                    .entry(token.token.token_key_id.to_string())
                    .or_default()
                    .push(membership.library);
            }
        }
    }
    primary_libraries
        .values()
        .filter(|libraries| libraries.len() > 1)
        .count()
}

fn pending_or_blocked_downstream_visible_count(
    connection: &Connection,
) -> Result<usize, rusqlite::Error> {
    let mut count = 0_usize;
    for library in LIBRARY_ORDER {
        count += connection.query_row(
            &format!(
                "SELECT COUNT(*) FROM {}
                 WHERE network = ?1 AND chain_id = ?2
                   AND admission_status IN ('pending_review', 'blocked')",
                library.table_name()
            ),
            rusqlite::params![ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID],
            |row| row.get::<_, usize>(0),
        )?;
    }
    Ok(count)
}

fn validate_gates(gates: &BootstrapSimulationGates) -> Result<(), BootstrapSimulationError> {
    let failed = [
        ("tempDatabaseOnly", gates.temp_database_only),
        ("secondMigrationWasNoop", gates.second_migration_was_noop),
        ("migrationChecksumStable", gates.migration_checksum_stable),
        ("dryRunsPersistedNoRows", gates.dry_runs_persisted_no_rows),
        (
            "allDryRunResultsMatchFirstApply",
            gates.all_dry_run_results_match_first_apply,
        ),
        (
            "allRepeatedAppliesAreIdempotent",
            gates.all_repeated_applies_are_idempotent,
        ),
        (
            "allLibraryChecksumsStable",
            gates.all_library_checksums_stable,
        ),
        (
            "primaryLibraryDuplicateCountIsZero",
            gates.primary_library_duplicate_count == 0,
        ),
        (
            "pendingOrBlockedDownstreamVisibleCountIsZero",
            gates.pending_or_blocked_downstream_visible_count == 0,
        ),
        ("quickCheckPassed", gates.quick_check_passed),
    ]
    .into_iter()
    .filter_map(|(name, passed)| (!passed).then_some(name))
    .collect::<Vec<_>>();
    if failed.is_empty() {
        Ok(())
    } else {
        Err(BootstrapSimulationError::GateFailed(failed.join(", ")))
    }
}

#[cfg(test)]
mod tests {
    use super::simulate_bootstrap_at_path;
    use crate::classification::{ClassificationReason, ClassifiedToken, LibraryMembership};
    use crate::metadata::{MetadataSourceKind, NormalizedTokenMetadata};
    use crate::model::{AdmissionStatus, LibraryKey};
    use manager_contracts::TokenKeyId;

    const CORE: &str = "0x1111111111111111111111111111111111111111";
    const STOCK: &str = "0x2222222222222222222222222222222222222222";
    const EXTENSION: &str = "0x3333333333333333333333333333333333333333";

    fn classified(
        address: &str,
        symbol: &str,
        memberships: Vec<(LibraryKey, AdmissionStatus)>,
    ) -> ClassifiedToken {
        ClassifiedToken {
            token: NormalizedTokenMetadata {
                source: MetadataSourceKind::ChainTokens,
                source_locator: format!("fixture:{address}"),
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
                admission_source: "fixture".to_owned(),
                metadata_source: "fixture".to_owned(),
                metadata_checksum: format!("sha256:{symbol:0>64}"),
                source_json: "{}".to_owned(),
                first_seen_at: None,
            },
            primary_library: memberships
                .iter()
                .find_map(|(library, _)| (*library != LibraryKey::Extension).then_some(*library)),
            memberships: memberships
                .iter()
                .map(|(library, admission_status)| LibraryMembership {
                    library: *library,
                    admission_status: *admission_status,
                    reason: if *library == LibraryKey::Extension {
                        ClassificationReason::ExtensionMember
                    } else {
                        ClassificationReason::CoreAllowlist
                    },
                })
                .collect(),
            extension_anchors: Vec::new(),
            source_inputs: vec![MetadataSourceKind::ChainTokens],
        }
    }

    #[test]
    fn temporary_bootstrap_is_dry_run_safe_and_idempotent() {
        let classification = crate::classification::ClassificationResult {
            tokens: vec![
                classified(
                    CORE,
                    "USDG",
                    vec![
                        (LibraryKey::Core, AdmissionStatus::Admitted),
                        (LibraryKey::Extension, AdmissionStatus::CandidateOnly),
                    ],
                ),
                classified(
                    STOCK,
                    "AAPL",
                    vec![
                        (LibraryKey::Stocks, AdmissionStatus::Admitted),
                        (LibraryKey::Extension, AdmissionStatus::CandidateOnly),
                    ],
                ),
                classified(
                    EXTENSION,
                    "EXT",
                    vec![(LibraryKey::Extension, AdmissionStatus::CandidateOnly)],
                ),
            ],
            issues: Vec::new(),
            duplicate_symbols: Vec::new(),
            source_overrides: 0,
        };
        let directory = tempfile::tempdir().unwrap();
        let report = simulate_bootstrap_at_path(
            &directory.path().join("manager.sqlite3"),
            &classification,
            "sha256:fixture",
        )
        .unwrap();

        assert!(report.passed);
        assert_eq!(report.proposed_memberships, 5);
        assert_eq!(report.final_row_counts["core"], 1);
        assert_eq!(report.final_row_counts["stocks"], 1);
        assert_eq!(report.final_row_counts["test"], 0);
        assert_eq!(report.final_row_counts["extension"], 3);
        assert!(report
            .libraries
            .iter()
            .all(|library| library.dry_run_equals_first_apply));
        assert!(report
            .libraries
            .iter()
            .all(|library| library.repeated_apply_was_idempotent));
    }

    #[test]
    fn primary_conflict_fails_the_simulation() {
        let classification = crate::classification::ClassificationResult {
            tokens: vec![classified(
                CORE,
                "USDG",
                vec![
                    (LibraryKey::Core, AdmissionStatus::Admitted),
                    (LibraryKey::Stocks, AdmissionStatus::Admitted),
                ],
            )],
            issues: Vec::new(),
            duplicate_symbols: Vec::new(),
            source_overrides: 0,
        };
        let directory = tempfile::tempdir().unwrap();
        let error = simulate_bootstrap_at_path(
            &directory.path().join("manager.sqlite3"),
            &classification,
            "sha256:fixture",
        )
        .unwrap_err();

        assert!(error.to_string().contains("already has primary membership"));
    }
}
