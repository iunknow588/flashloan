use rusqlite::{params_from_iter, Connection, OpenFlags, OptionalExtension, TransactionBehavior};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub const MIGRATION_ID: &str = "20261103_001_manager_schema";
pub const CANDIDATE_EDGE_MIGRATION_ID: &str = "20261104_001_candidate_edge_schema";
pub const CANDIDATE_EXCHANGE_MIGRATION_ID: &str = "20261104_002_candidate_exchange_schema";
pub const CLOSED_LOOP_MIGRATION_ID: &str = "20261104_003_closed_loop_schema";
pub const REVIEW_MIGRATION_ID: &str = "20261104_004_review_schema";
pub const BROADCAST_MIGRATION_ID: &str = "20261104_005_broadcast_schema";
pub const COMPOSITE_MIGRATION_ID: &str = "20261104_006_composite_schema";
pub const MIGRATION_IDS: &[&str] = &[
    MIGRATION_ID,
    CANDIDATE_EDGE_MIGRATION_ID,
    CANDIDATE_EXCHANGE_MIGRATION_ID,
    CLOSED_LOOP_MIGRATION_ID,
    REVIEW_MIGRATION_ID,
    BROADCAST_MIGRATION_ID,
    COMPOSITE_MIGRATION_ID,
];
pub const LATEST_MIGRATION_ID: &str = COMPOSITE_MIGRATION_ID;

const PREFERRED_LEDGER: &str = "schema_migrations";
const FALLBACK_LEDGER: &str = "manager_schema_migrations";

struct MigrationDefinition {
    id: &'static str,
    sql: &'static str,
}

const MIGRATIONS: &[MigrationDefinition] = &[
    MigrationDefinition {
        id: MIGRATION_ID,
        sql: include_str!("../migrations/20261103_001_manager_schema.sql"),
    },
    MigrationDefinition {
        id: CANDIDATE_EDGE_MIGRATION_ID,
        sql: include_str!("../migrations/20261104_001_candidate_edge_schema.sql"),
    },
    MigrationDefinition {
        id: CANDIDATE_EXCHANGE_MIGRATION_ID,
        sql: include_str!("../migrations/20261104_002_candidate_exchange_schema.sql"),
    },
    MigrationDefinition {
        id: CLOSED_LOOP_MIGRATION_ID,
        sql: include_str!("../migrations/20261104_003_closed_loop_schema.sql"),
    },
    MigrationDefinition {
        id: REVIEW_MIGRATION_ID,
        sql: include_str!("../migrations/20261104_004_review_schema.sql"),
    },
    MigrationDefinition {
        id: BROADCAST_MIGRATION_ID,
        sql: include_str!("../migrations/20261104_005_broadcast_schema.sql"),
    },
    MigrationDefinition {
        id: COMPOSITE_MIGRATION_ID,
        sql: include_str!("../migrations/20261104_006_composite_schema.sql"),
    },
];

const TARGET_TABLES: &[&str] = &[
    "token_core",
    "token_stocks",
    "token_extension",
    "token_test",
    "token_library_state",
    "token_admission_events",
    "manager_write_requests",
    "pool_scope_config",
    "v4_exchange_pools_core4",
    "manager_dex_candidate_library_state",
    "manager_dex_candidate_anchor_tokens",
    "manager_dex_candidate_pool_pairs",
    "manager_dex_candidate_pools",
    "manager_dex_candidate_pool_recommendations",
    "manager_dex_candidate_abnormal_anchor_tokens",
    "manager_dex_candidate_abnormal_pool_pairs",
    "manager_dex_candidate_abnormal_pools",
    "v4_exchange_pools_candidate_exchange",
    "manager_market_observation_library_state",
    "manager_market_observation_yz_pairs",
    "manager_market_observation_intermediate_pools",
    "manager_market_observation_abnormal_yz_pairs",
    "manager_market_observation_abnormal_intermediate_pools",
    "manager_market_observation_liquidity_snapshots",
    "manager_v4_closed_loop_library_state",
    "manager_v4_closed_loops",
    "manager_v4_closed_loop_hops",
    "manager_v4_closed_loop_optimizations",
    "manager_v4_closed_loop_runtime_state",
    "manager_v4_closed_loop_skipped_tokens",
    "manager_review_library_state",
    "manager_v4_reviews",
    "manager_v4_review_diagnostics",
    "manager_v4_review_broadcast_requests",
    "manager_broadcast_library_state",
    "manager_cow_execution_attempts",
    "manager_broadcast_attempt_diagnostics",
    "manager_pipeline_runs",
    "manager_pipeline_steps",
    "manager_pipeline_events",
    "manager_events",
];

const BASE_TABLES: &[&str] = &[
    "token_core",
    "token_stocks",
    "token_extension",
    "token_test",
    "token_library_state",
    "token_admission_events",
    "manager_write_requests",
    "pool_scope_config",
    "v4_exchange_pools_core4",
];

const CANDIDATE_TABLES: &[&str] = &[
    "manager_dex_candidate_library_state",
    "manager_dex_candidate_anchor_tokens",
    "manager_dex_candidate_pool_pairs",
    "manager_dex_candidate_pools",
    "manager_dex_candidate_pool_recommendations",
    "manager_dex_candidate_abnormal_anchor_tokens",
    "manager_dex_candidate_abnormal_pool_pairs",
    "manager_dex_candidate_abnormal_pools",
];

const CANDIDATE_EXCHANGE_TABLES: &[&str] = &[
    "v4_exchange_pools_candidate_exchange",
    "manager_market_observation_library_state",
    "manager_market_observation_yz_pairs",
    "manager_market_observation_intermediate_pools",
    "manager_market_observation_abnormal_yz_pairs",
    "manager_market_observation_abnormal_intermediate_pools",
    "manager_market_observation_liquidity_snapshots",
];

const CLOSED_LOOP_TABLES: &[&str] = &[
    "manager_v4_closed_loop_library_state",
    "manager_v4_closed_loops",
    "manager_v4_closed_loop_hops",
    "manager_v4_closed_loop_optimizations",
    "manager_v4_closed_loop_runtime_state",
    "manager_v4_closed_loop_skipped_tokens",
];

const REVIEW_TABLES: &[&str] = &[
    "manager_review_library_state",
    "manager_v4_reviews",
    "manager_v4_review_diagnostics",
    "manager_v4_review_broadcast_requests",
];

const BROADCAST_TABLES: &[&str] = &[
    "manager_broadcast_library_state",
    "manager_cow_execution_attempts",
    "manager_broadcast_attempt_diagnostics",
];

const COMPOSITE_TABLES: &[&str] = &[
    "manager_pipeline_runs",
    "manager_pipeline_steps",
    "manager_pipeline_events",
    "manager_events",
];

#[derive(Debug)]
pub enum MigrationError {
    Sqlite(rusqlite::Error),
    Io(io::Error),
    ExistingLedgerIncompatible(String),
    MigrationChecksumChanged {
        expected: String,
        actual: String,
    },
    TargetTableIncompatible {
        table: String,
        missing_columns: Vec<String>,
    },
    DatabaseNotReadable(PathBuf),
}

impl fmt::Display for MigrationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "sqlite error: {error}"),
            Self::Io(error) => write!(formatter, "io error: {error}"),
            Self::ExistingLedgerIncompatible(table) => {
                write!(
                    formatter,
                    "existing migration ledger {table} is incompatible"
                )
            }
            Self::MigrationChecksumChanged { expected, actual } => write!(
                formatter,
                "migration checksum mismatch: expected {expected}, actual {actual}"
            ),
            Self::TargetTableIncompatible {
                table,
                missing_columns,
            } => write!(
                formatter,
                "existing target table {table} is incompatible; missing columns: {}",
                missing_columns.join(", ")
            ),
            Self::DatabaseNotReadable(path) => {
                write!(formatter, "database is not readable: {}", path.display())
            }
        }
    }
}

impl std::error::Error for MigrationError {}

impl From<rusqlite::Error> for MigrationError {
    fn from(value: rusqlite::Error) -> Self {
        Self::Sqlite(value)
    }
}

impl From<io::Error> for MigrationError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationRecord {
    pub migration_id: String,
    pub checksum: String,
    pub applied_now: bool,
    pub applied_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationReport {
    pub migration_id: String,
    pub migration_ids: Vec<String>,
    pub checksum: String,
    pub schema_checksum: String,
    pub migration_checksums: BTreeMap<String, String>,
    pub migration_table: String,
    pub applied_now: bool,
    pub applied_at: Option<String>,
    pub migrations: Vec<MigrationRecord>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreflightReport {
    pub path: String,
    pub file_size_bytes: u64,
    pub quick_check: String,
    pub migration_ledger: String,
    pub target_tables: Vec<TablePresence>,
    pub target_table_compatibility: Vec<TableCompatibility>,
    pub ready_for_migration: bool,
    pub view_present: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TablePresence {
    pub name: String,
    pub present: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TableCompatibility {
    pub name: String,
    pub present: bool,
    pub compatible: bool,
    pub missing_columns: Vec<String>,
}

pub fn migration_checksum() -> String {
    checksum_for(MIGRATIONS[0].sql)
}

pub fn migration_checksums() -> BTreeMap<String, String> {
    MIGRATIONS
        .iter()
        .map(|migration| (migration.id.to_owned(), checksum_for(migration.sql)))
        .collect()
}

pub fn schema_checksum() -> String {
    let payload = migration_checksums()
        .into_iter()
        .map(|(id, checksum)| format!("{id}:{checksum}"))
        .collect::<Vec<_>>()
        .join("\n");
    checksum_for(&payload)
}

pub fn migrate(database_path: &Path) -> Result<MigrationReport, MigrationError> {
    let mut connection = Connection::open(database_path)?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let ledger = select_ledger(&transaction)?;
    ensure_ledger(&transaction, &ledger)?;

    let mut records = Vec::with_capacity(MIGRATIONS.len());
    for migration in MIGRATIONS {
        let expected_checksum = checksum_for(migration.sql);
        let existing: Option<(String, String)> = transaction
            .query_row(
                &format!("SELECT checksum, applied_at FROM {ledger} WHERE migration_id = ?1"),
                [migration.id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        if let Some((existing_checksum, applied_at)) = existing {
            if existing_checksum != expected_checksum {
                return Err(MigrationError::MigrationChecksumChanged {
                    expected: expected_checksum,
                    actual: existing_checksum,
                });
            }
            records.push(MigrationRecord {
                migration_id: migration.id.to_owned(),
                checksum: expected_checksum,
                applied_now: false,
                applied_at: Some(applied_at),
            });
            continue;
        }

        ensure_migration_tables_compatible(&transaction, migration.id)?;
        transaction.execute_batch(migration.sql)?;
        transaction.execute(
            &format!(
                "INSERT INTO {ledger} (migration_id, checksum, applied_at, result_json)
                 VALUES (?1, ?2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), '{{}}')"
            ),
            (migration.id, &expected_checksum),
        )?;
        let applied_at: String = transaction.query_row(
            &format!("SELECT applied_at FROM {ledger} WHERE migration_id = ?1"),
            [migration.id],
            |row| row.get(0),
        )?;
        records.push(MigrationRecord {
            migration_id: migration.id.to_owned(),
            checksum: expected_checksum,
            applied_now: true,
            applied_at: Some(applied_at),
        });
    }
    transaction.commit()?;

    let latest = records
        .last()
        .expect("the manager migration list is never empty");
    Ok(MigrationReport {
        migration_id: latest.migration_id.clone(),
        migration_ids: records
            .iter()
            .map(|record| record.migration_id.clone())
            .collect(),
        checksum: latest.checksum.clone(),
        schema_checksum: schema_checksum(),
        migration_checksums: migration_checksums(),
        migration_table: ledger,
        applied_now: records.iter().any(|record| record.applied_now),
        applied_at: latest.applied_at.clone(),
        migrations: records,
    })
}

pub fn preflight_read_only(database_path: &Path) -> Result<PreflightReport, MigrationError> {
    if !database_path.exists() {
        return Err(MigrationError::DatabaseNotReadable(
            database_path.to_path_buf(),
        ));
    }
    let connection = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|_| MigrationError::DatabaseNotReadable(database_path.to_path_buf()))?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    let quick_check: String = connection.query_row("PRAGMA quick_check", [], |row| row.get(0))?;
    let ledger = detect_ledger(&connection)?;
    let mut target_tables = Vec::with_capacity(TARGET_TABLES.len());
    for table in TARGET_TABLES {
        target_tables.push(TablePresence {
            name: (*table).to_owned(),
            present: table_exists(&connection, table)?,
        });
    }
    let mut target_table_compatibility = Vec::with_capacity(TARGET_TABLES.len());
    for table in TARGET_TABLES {
        let present = table_exists(&connection, table)?;
        let missing_columns = if present {
            missing_required_columns(&connection, table)?
        } else {
            Vec::new()
        };
        target_table_compatibility.push(TableCompatibility {
            name: (*table).to_owned(),
            present,
            compatible: !present || missing_columns.is_empty(),
            missing_columns,
        });
    }
    let ready_for_migration = target_table_compatibility
        .iter()
        .all(|table| table.compatible);
    let file_size_bytes = fs::metadata(database_path)?.len();
    Ok(PreflightReport {
        path: database_path.display().to_string(),
        file_size_bytes,
        quick_check,
        migration_ledger: ledger,
        target_tables,
        target_table_compatibility,
        ready_for_migration,
        view_present: table_exists(&connection, "token_library_catalog")?,
    })
}

fn checksum_for(sql: &str) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(sql.as_bytes())))
}

fn select_ledger(connection: &Connection) -> Result<String, MigrationError> {
    if !table_exists(connection, PREFERRED_LEDGER)? {
        return Ok(PREFERRED_LEDGER.to_owned());
    }
    if ledger_has_expected_columns(connection, PREFERRED_LEDGER)?
        && ledger_has_only_known_migrations(connection, PREFERRED_LEDGER)?
    {
        return Ok(PREFERRED_LEDGER.to_owned());
    }
    Ok(FALLBACK_LEDGER.to_owned())
}

fn detect_ledger(connection: &Connection) -> Result<String, MigrationError> {
    if table_exists(connection, FALLBACK_LEDGER)? {
        return Ok(FALLBACK_LEDGER.to_owned());
    }
    if table_exists(connection, PREFERRED_LEDGER)? {
        if ledger_has_expected_columns(connection, PREFERRED_LEDGER)?
            && ledger_has_only_known_migrations(connection, PREFERRED_LEDGER)?
        {
            return Ok(PREFERRED_LEDGER.to_owned());
        }
        return Ok(FALLBACK_LEDGER.to_owned());
    }
    Ok(PREFERRED_LEDGER.to_owned())
}

fn ledger_has_only_known_migrations(
    connection: &Connection,
    table: &str,
) -> Result<bool, MigrationError> {
    let placeholders = MIGRATION_IDS
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!("SELECT COUNT(*) FROM {table} WHERE migration_id NOT IN ({placeholders})");
    let foreign_rows: u64 =
        connection.query_row(&sql, params_from_iter(MIGRATION_IDS.iter()), |row| {
            row.get(0)
        })?;
    Ok(foreign_rows == 0)
}

fn ensure_ledger(connection: &Connection, table: &str) -> Result<(), MigrationError> {
    if table_exists(connection, table)? {
        if ledger_has_expected_columns(connection, table)? {
            return Ok(());
        }
        return Err(MigrationError::ExistingLedgerIncompatible(table.to_owned()));
    }
    let ddl = match table {
        PREFERRED_LEDGER => {
            "CREATE TABLE schema_migrations (
                migration_id TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}'
            )"
        }
        FALLBACK_LEDGER => {
            "CREATE TABLE manager_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}'
            )"
        }
        _ => unreachable!("ledger name is a fixed internal enum"),
    };
    connection.execute_batch(ddl)?;
    Ok(())
}

fn ledger_has_expected_columns(
    connection: &Connection,
    table: &str,
) -> Result<bool, MigrationError> {
    let sql = match table {
        PREFERRED_LEDGER => "PRAGMA table_info(schema_migrations)",
        FALLBACK_LEDGER => "PRAGMA table_info(manager_schema_migrations)",
        _ => unreachable!("ledger name is a fixed internal enum"),
    };
    let mut statement = connection.prepare(sql)?;
    let names = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(["migration_id", "checksum", "applied_at", "result_json"]
        .iter()
        .all(|required| names.iter().any(|name| name == required)))
}

fn ensure_migration_tables_compatible(
    connection: &Connection,
    migration_id: &str,
) -> Result<(), MigrationError> {
    let tables = match migration_id {
        MIGRATION_ID => BASE_TABLES,
        CANDIDATE_EDGE_MIGRATION_ID => CANDIDATE_TABLES,
        CANDIDATE_EXCHANGE_MIGRATION_ID => CANDIDATE_EXCHANGE_TABLES,
        CLOSED_LOOP_MIGRATION_ID => CLOSED_LOOP_TABLES,
        REVIEW_MIGRATION_ID => REVIEW_TABLES,
        BROADCAST_MIGRATION_ID => BROADCAST_TABLES,
        COMPOSITE_MIGRATION_ID => COMPOSITE_TABLES,
        _ => unreachable!("migration id is a fixed internal enum"),
    };
    for table in tables {
        if !table_exists(connection, table)? {
            continue;
        }
        let missing_columns = missing_required_columns(connection, table)?;
        if !missing_columns.is_empty() {
            return Err(MigrationError::TargetTableIncompatible {
                table: (*table).to_owned(),
                missing_columns,
            });
        }
    }
    Ok(())
}

fn missing_required_columns(
    connection: &Connection,
    table: &str,
) -> Result<Vec<String>, MigrationError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let names = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(required_columns(table)
        .iter()
        .filter(|required| !names.iter().any(|name| name == *required))
        .map(|required| (*required).to_owned())
        .collect())
}

fn required_columns(table: &str) -> &'static [&'static str] {
    match table {
        "token_core" | "token_stocks" | "token_extension" | "token_test" => &[
            "token_key_id",
            "v4_currency_key",
            "admission_status",
            "metadata_checksum",
        ],
        "token_library_state" => &["library_key", "library_version", "metadata_checksum"],
        "token_admission_events" => &["event_id", "token_key_id", "new_status"],
        "manager_write_requests" => &[
            "operation",
            "idempotency_key",
            "request_digest",
            "response_json",
        ],
        "pool_scope_config" => &["scope_key", "pool_table", "materialized_checksum"],
        "v4_exchange_pools_core4" => &[
            "pool_manager",
            "pool_key_json",
            "token0_key_id",
            "token1_key_id",
            "pool_status",
        ],
        "manager_dex_candidate_library_state" => &[
            "scope_key",
            "candidate_version",
            "materialized_checksum",
            "pool_scope_checksum",
        ],
        "manager_dex_candidate_anchor_tokens" => {
            &["anchor_token_key_id", "token_key_id", "token_library_key"]
        }
        "manager_dex_candidate_pool_pairs" | "manager_dex_candidate_abnormal_pool_pairs" => {
            &["candidate_token_key_id", "pair_key_id"]
        }
        "manager_dex_candidate_pools" => &[
            "anchor_token_key_id",
            "candidate_token_key_id",
            "pair_key_id",
            "pool_key_json",
        ],
        "manager_dex_candidate_pool_recommendations" => {
            &["pair_key_id", "direction_key", "hop", "rank"]
        }
        "manager_dex_candidate_abnormal_anchor_tokens" => {
            &["anchor_token_key_id", "token_key_id", "reasons_json"]
        }
        "manager_dex_candidate_abnormal_pools" => &[
            "anchor_token_key_id",
            "pair_key_id",
            "pool_status",
            "reason",
        ],
        "v4_exchange_pools_candidate_exchange" => &[
            "pool_manager",
            "pool_key_json",
            "token0_key_id",
            "token1_key_id",
            "pool_status",
        ],
        "manager_market_observation_library_state" => &[
            "observation_version",
            "materialized_checksum",
            "candidate_edge_version",
            "candidate_edge_checksum",
            "pool_scope_checksum",
        ],
        "manager_market_observation_yz_pairs" | "manager_market_observation_abnormal_yz_pairs" => {
            &["anchor_token_key_id", "direction_key", "token_x_key_id"]
        }
        "manager_market_observation_intermediate_pools"
        | "manager_market_observation_abnormal_intermediate_pools" => {
            &["anchor_token_key_id", "pool_id", "pool_key_json"]
        }
        "manager_market_observation_liquidity_snapshots" => {
            &["anchor_token_key_id", "pool_id", "has_liquidity"]
        }
        "manager_v4_closed_loop_library_state" => &[
            "closed_loop_version",
            "materialized_checksum",
            "candidate_edge_version",
            "candidate_edge_checksum",
            "observation_version",
            "observation_checksum",
        ],
        "manager_v4_closed_loops" => &[
            "loop_key",
            "route_key_id",
            "anchor_token_key_id",
            "pool_path_key",
            "pool_ids_json",
        ],
        "manager_v4_closed_loop_hops" => &[
            "loop_key",
            "hop_index",
            "input_token_key_id",
            "output_token_key_id",
            "pool_id",
        ],
        "manager_v4_closed_loop_optimizations" => &[
            "loop_key",
            "hop_index",
            "optimization_id",
            "optimized_pool_id",
        ],
        "manager_v4_closed_loop_runtime_state" => {
            &["loop_key", "runtime_status", "review_generation"]
        }
        "manager_v4_closed_loop_skipped_tokens" => &[
            "anchor_token_key_id",
            "token_key_id",
            "candidate_role",
            "reason",
        ],
        "manager_review_library_state" => &[
            "review_version",
            "materialized_checksum",
            "closed_loop_version",
            "closed_loop_checksum",
        ],
        "manager_v4_reviews" => &[
            "loop_key",
            "closed_loop_version",
            "review_status",
            "signed_pnl_raw",
            "signed_pnl_source",
        ],
        "manager_v4_review_diagnostics" => &[
            "loop_key",
            "diagnostic_id",
            "diagnostic_type",
            "payload_json",
        ],
        "manager_v4_review_broadcast_requests" => &[
            "loop_key",
            "closed_loop_version",
            "signed_pnl_raw",
            "request_status",
        ],
        "manager_broadcast_library_state" => &[
            "broadcast_version",
            "materialized_checksum",
            "review_version",
            "review_checksum",
            "closed_loop_version",
            "closed_loop_checksum",
        ],
        "manager_cow_execution_attempts" => &[
            "attempt_id",
            "loop_key",
            "review_version",
            "review_checksum",
            "signed_pnl_raw",
            "signer_address",
            "nonce",
            "rpc_url",
            "status",
            "evidence_json",
        ],
        "manager_broadcast_attempt_diagnostics" => &[
            "attempt_id",
            "loop_key",
            "diagnostic_id",
            "diagnostic_type",
            "payload_json",
        ],
        "manager_pipeline_runs" => &[
            "run_id",
            "status",
            "requested_steps_json",
            "upstream_snapshot_json",
            "current_step_index",
        ],
        "manager_pipeline_steps" => &[
            "run_id",
            "step_index",
            "step_id",
            "step_name",
            "depends_on_json",
            "status",
            "attempt_count",
        ],
        "manager_pipeline_events" => &[
            "event_id",
            "run_id",
            "event_type",
            "actor",
            "request_id",
            "payload_json",
        ],
        "manager_events" => &[
            "event_id",
            "source_manager",
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "request_id",
        ],
        _ => &[],
    }
}

fn table_exists(connection: &Connection, table: &str) -> Result<bool, MigrationError> {
    let exists: Option<i64> = connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE name = ?1 AND type IN ('table', 'view')",
            [table],
            |row| row.get(0),
        )
        .optional()?;
    Ok(exists.is_some())
}
