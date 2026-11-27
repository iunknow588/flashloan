use std::collections::HashSet;
use std::time::Duration;

use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use std::path::Path;

const BUSINESS_DDL_SQL: &str = include_str!("../schema/business_schema.sql");
const BUSINESS_INDEXES_SQL: &str = include_str!("../schema/business_indexes.sql");

pub(crate) fn open_rw(database_path: &str) -> Result<Connection, String> {
    let connection = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| format!("could not open SQLite for schema {database_path}: {error}"))?;
    // A writer that does not wait for the lock loses the race against the
    // other writers of the store, and the migration then reports the loss as
    // a schema failure.  The store is a multi-gigabyte WAL database whose
    // checkpoint can hold the write lock for a while, so the writer waits
    // for its turn instead of failing on the first attempt.
    connection
        .busy_timeout(Duration::from_secs(60))
        .map_err(|error| format!("could not arm the busy timeout for {database_path}: {error}"))?;
    Ok(connection)
}

fn execute_batch(connection: &Connection, sql: &str) -> Result<(), String> {
    connection
        .execute_batch(sql)
        .map_err(|error| format!("SQLite schema batch failed: {error}"))
}

/// The table that carries the on-chain depth observation.
const DEPTH_SNAPSHOT_TABLE: &str = "v4_exchange_pool_liquidity_snapshots";

/// The capacity columns that carry the on-chain depth evidence into the
/// snapshot table.
///
/// The capacity is denominated in raw token units, so it carries no external
/// price dependency. `capacity_binding_raw` is the decimals-normalised
/// binding-side capacity, which is the quantity the depth bands and the
/// kernel admission floor are applied to.
const DEPTH_CAPACITY_COLUMNS: &[(&str, &str)] = &[
    ("capacity_token0_raw", "TEXT"),
    ("capacity_token1_raw", "TEXT"),
    ("capacity_raw", "TEXT"),
    ("capacity_binding_raw", "REAL"),
    ("price_impact_bps", "REAL"),
    ("price_impact_limit_bps", "REAL"),
    ("depth_basis", "TEXT"),
    ("single_tick_max_token0_raw", "TEXT"),
    ("single_tick_max_token1_raw", "TEXT"),
];

/// Adds the capacity columns to the depth snapshot table when they are
/// missing.
///
/// SQLite has no `ADD COLUMN IF NOT EXISTS`, so the column list is inspected
/// first. The operation is idempotent: every column is checked and only the
/// absent ones are added.
///
/// A store that predates the capacity columns cannot carry the measured
/// binding-side capacity at all, so the kernel cannot compute an admission
/// verdict for any of its pools. Adding the columns is therefore a
/// prerequisite of the depth measurement, not an optional extra.
fn add_depth_capacity_columns(connection: &Connection) -> Result<Vec<&'static str>, String> {
    let mut added = Vec::new();
    for (column, column_type) in DEPTH_CAPACITY_COLUMNS {
        let exists: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM pragma_table_info(?1) WHERE name=?2",
                rusqlite::params![DEPTH_SNAPSHOT_TABLE, column],
                |row| row.get(0),
            )
            .map_err(|error| format!("depth column probe failed: {error}"))?;
        if exists > 0 {
            continue;
        }
        connection
            .execute_batch(&format!(
                "ALTER TABLE {DEPTH_SNAPSHOT_TABLE} ADD COLUMN {column} {column_type}"
            ))
            .map_err(|error| format!("depth column add failed for {column}: {error}"))?;
        added.push(*column);
    }
    Ok(added)
}


/// The column types that may be attached to an existing table.
///
/// SQLite can only add a column when its type is one of these affinities and
/// when the column has no non-NULL default that the table cannot supply.
const ADDABLE_COLUMN_TYPES: &[&str] = &[
    "TEXT",
    "INTEGER",
    "REAL",
    "BLOB",
    "NUMERIC",
];

/// Parses one `CREATE TABLE` statement of the canonical DDL and returns its
/// column names together with their type definitions.
///
/// The parser is deliberately narrow: it only understands the column
/// definitions of the canonical business schema, which are all a name, an
/// optional type and an optional default. A table-level constraint (a
/// primary key, a foreign key or a unique clause) is skipped, because those
/// cannot be attached to an existing table with `ALTER TABLE ADD COLUMN`.
fn parse_create_table_columns(statement: &str) -> Vec<(String, String)> {
    let Some(open) = statement.find('(') else {
        return Vec::new();
    };
    let Some(close) = statement.rfind(')') else {
        return Vec::new();
    };
    if close <= open {
        return Vec::new();
    }
    let body = &statement[open + 1..close];

    // Split on the commas that separate the definitions, ignoring the commas
    // inside a parenthesised default expression.
    let mut definitions: Vec<String> = Vec::new();
    let mut depth = 0usize;
    let mut current = String::new();
    for character in body.chars() {
        match character {
            '(' => {
                depth += 1;
                current.push(character);
            }
            ')' => {
                depth = depth.saturating_sub(1);
                current.push(character);
            }
            ',' if depth == 0 => {
                definitions.push(std::mem::take(&mut current));
            }
            _ => current.push(character),
        }
    }
    definitions.push(current);

    let mut columns = Vec::new();
    for definition in definitions {
        let definition = definition.trim();
        if definition.is_empty() {
            continue;
        }
        let lowered = definition.to_ascii_lowercase();
        if lowered.starts_with("primary key")
            || lowered.starts_with("foreign key")
            || lowered.starts_with("unique")
            || lowered.starts_with("constraint")
            || lowered.starts_with("check")
        {
            continue;
        }
        let mut parts = definition.split_whitespace();
        let Some(name) = parts.next() else {
            continue;
        };
        let name = name.trim_matches(|character| character == '"' || character == '`' || character == '[' || character == ']');
        if name.is_empty() {
            continue;
        }
        // The type is the next token. A column without an explicit type is
        // stored with the BLOB affinity, which SQLite still accepts.
        let column_type = parts.next().unwrap_or("BLOB").to_ascii_uppercase();
        columns.push((name.to_string(), column_type));
    }
    columns
}

/// Brings an existing store up to the shape of the canonical DDL.
///
/// SQLite never alters an existing table, so a store that was created before
/// a column existed keeps its old shape forever. Every writer that expects
/// the newer column then fails with "no column named ...", which turns a
/// schema drift into a runtime defect on that chain only.
///
/// The migration is idempotent and additive: it only ever **adds** a column
/// that the canonical DDL declares and the live table is missing. It never
/// drops or rewrites anything, so it can run on a live store.
fn add_missing_columns_from_ddl(connection: &Connection, ddl: &str) -> Result<Vec<String>, String> {
    let mut added: Vec<String> = Vec::new();
    for statement in ddl.split(';') {
        let statement = statement.trim();
        if statement.is_empty() {
            continue;
        }
        let lowered = statement.to_ascii_lowercase();
        if !lowered.starts_with("create table") {
            continue;
        }
        let Some(table_start) = statement.find('(') else {
            continue;
        };
        let table_name = statement[..table_start]
            .rsplit(' ')
            .next()
            .unwrap_or("")
            .trim()
            .trim_matches(|character| character == '"' || character == '`' || character == '[' || character == ']');
        if table_name.is_empty() {
            continue;
        }
        let mut column_probe = connection
            .prepare(&format!("SELECT name FROM pragma_table_info(?1)"))
            .map_err(|error| format!("column probe failed for {table_name}: {error}"))?;
        let live: HashSet<String> = column_probe
            .query_map(rusqlite::params![table_name], |row| row.get::<_, String>(0))
            .map_err(|error| format!("column probe failed for {table_name}: {error}"))?
            .collect::<Result<HashSet<String>, rusqlite::Error>>()
            .map_err(|error| format!("column probe failed for {table_name}: {error}"))?;
        if live.is_empty() {
            // The table does not exist in this store, so the DDL statement
            // already created it with the full shape.
            continue;
        }
        for (column, column_type) in parse_create_table_columns(statement) {
            if live.contains(&column) {
                continue;
            }
            let type_upper = column_type.trim().to_ascii_uppercase();
            let addable = if type_upper.is_empty() {
                "BLOB"
            } else if ADDABLE_COLUMN_TYPES.contains(&type_upper.as_str()) {
                type_upper.as_str()
            } else if type_upper.starts_with("TEXT") || type_upper.starts_with("VARCHAR") {
                "TEXT"
            } else if type_upper.starts_with("INT") || type_upper.starts_with("BIGINT") {
                "INTEGER"
            } else if type_upper.starts_with("DOUBLE")
                || type_upper.starts_with("REAL")
                || type_upper.starts_with("FLOAT")
            {
                "REAL"
            } else if type_upper.starts_with("DECIMAL") || type_upper.starts_with("NUMERIC") {
                "NUMERIC"
            } else if type_upper.starts_with("BOOL") {
                "INTEGER"
            } else {
                "BLOB"
            };
            connection
                .execute_batch(&format!(
                    "ALTER TABLE {table_name} ADD COLUMN {column} {addable}"
                ))
                .map_err(|error| {
                    format!("column add failed for {table_name}.{column}: {error}")
                })?;
            added.push(format!("{table_name}.{column}"));
        }
    }
    Ok(added)
}

pub fn ensure_database_schema(database_path: &str) -> Result<Value, String> {
    let path = Path::new(database_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| {
            format!(
                "could not create schema directory {}: {error}",
                parent.display()
            )
        })?;
    }
    let connection = open_rw(database_path)?;
    execute_batch(&connection, BUSINESS_DDL_SQL)?;
    // A store created before the capacity columns existed keeps its old
    // shape: `CREATE TABLE IF NOT EXISTS` never alters an existing table.
    // Without the columns the store cannot hold the measured capacity, so
    // the kernel has no admission input at all.
    let added_depth_columns = add_depth_capacity_columns(&connection)?;
    // The canonical DDL is the authority on the store's shape. A store that
    // predates a column keeps its old shape forever, so every missing column
    // is attached here, before any writer can fail on it.
    let added_columns = add_missing_columns_from_ddl(&connection, BUSINESS_DDL_SQL)?;
    execute_batch(&connection, "PRAGMA journal_mode=WAL")?;
    connection
        .execute(
            "CREATE TABLE IF NOT EXISTS kv_entries (
                collection TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (collection, key)
            )",
            [],
        )
        .map_err(|error| format!("SQLite kv_entries create failed: {error}"))?;
    connection
        .execute(
            "CREATE TABLE IF NOT EXISTS kv_meta (
                meta_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            )",
            [],
        )
        .map_err(|error| format!("SQLite kv_meta create failed: {error}"))?;
    connection
        .execute(
            "INSERT OR REPLACE INTO kv_meta (meta_key, value_json) VALUES ('schema_version', '1')",
            [],
        )
        .map_err(|error| format!("SQLite kv_meta schema_version failed: {error}"))?;
    connection
        .execute(
            "INSERT OR REPLACE INTO kv_meta (meta_key, value_json) VALUES (?1, ?2)",
            [
                "business_schema_version",
                &serde_json::to_string("directional_pairs_v1")
                    .map_err(|error| error.to_string())?,
            ],
        )
        .map_err(|error| format!("SQLite kv_meta business_schema_version failed: {error}"))?;
    Ok(json!({
        "ok": true,
        "databasePath": database_path,
        "schemaVersion": "directional_pairs_v1",
        "ddlStatementCount": BUSINESS_DDL_SQL.trim().split(';').filter(|part| !part.trim().is_empty()).count(),
        "addedDepthCapacityColumns": added_depth_columns,
        "addedColumns": added_columns,
    }))
}

pub fn ensure_database_indexes(database_path: &str) -> Result<Value, String> {
    let connection = open_rw(database_path)?;
    execute_batch(&connection, BUSINESS_INDEXES_SQL)?;
    Ok(json!({
        "ok": true,
        "databasePath": database_path,
        "indexStatementCount": BUSINESS_INDEXES_SQL.trim().split(';').filter(|part| !part.trim().is_empty()).count(),
    }))
}

pub fn migrate_direction_schema(database_path: &str) -> Result<Value, String> {
    let connection = open_rw(database_path)?;
    crate::migration::migrate_direction_schema(&connection)
}

pub fn current_schema_version(database_path: &str) -> Result<String, String> {
    let connection = open_rw(database_path)?;
    let value: Option<String> = connection
        .query_row(
            "SELECT value_json FROM kv_meta WHERE meta_key = 'business_schema_version'",
            [],
            |row| row.get(0),
        )
        .ok();
    Ok(value
        .and_then(|encoded| serde_json::from_str(&encoded).ok())
        .unwrap_or_else(|| "directional_pairs_v1".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schema_ddl_is_embedded_and_non_empty() {
        assert!(!BUSINESS_DDL_SQL.trim().is_empty());
        assert!(BUSINESS_DDL_SQL.contains("CREATE TABLE IF NOT EXISTS v4_exchange_pools"));
        assert!(!BUSINESS_INDEXES_SQL.trim().is_empty());
        assert!(BUSINESS_INDEXES_SQL.contains("idx_closed_loop_hops_pool"));
    }

    /// A store created before the capacity columns existed cannot hold the
    /// measured binding-side capacity, so the kernel has no admission input
    /// for any of its pools. The schema owner has to add the columns to an
    /// existing store, not only to a fresh one.
    #[test]
    fn legacy_depth_snapshot_table_gains_the_capacity_columns() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("legacy-depth.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
                    pool_id TEXT NOT NULL,
                    has_liquidity INTEGER,
                    liquidity TEXT,
                    liquidity_index REAL,
                    estimated_tvl_usd REAL,
                    balances_json TEXT,
                    depth_status TEXT,
                    depth_band_json TEXT,
                    depth_score_usd REAL,
                    sqrt_price_x96 TEXT,
                    price REAL,
                    tick INTEGER,
                    liquidity_read_at TEXT,
                    liquidity_block_number INTEGER,
                    recent_swap_active INTEGER,
                    observed_at TEXT NOT NULL,
                    refreshed_at TEXT NOT NULL,
                    evidence_source TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );",
            )
            .unwrap();
        drop(connection);

        let payload = ensure_database_schema(path.to_str().unwrap()).unwrap();
        let added = payload["addedDepthCapacityColumns"]
            .as_array()
            .expect("added column list")
            .len();
        assert_eq!(added, DEPTH_CAPACITY_COLUMNS.len());

        let connection = Connection::open(&path).unwrap();
        for (column, _) in DEPTH_CAPACITY_COLUMNS {
            let exists: i64 = connection
                .query_row(
                    "SELECT COUNT(*) FROM pragma_table_info(?1) WHERE name=?2",
                    rusqlite::params![DEPTH_SNAPSHOT_TABLE, column],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(exists, 1, "capacity column {column} is missing");
        }

        // The migration is idempotent: a second run adds nothing.
        let second = ensure_database_schema(path.to_str().unwrap()).unwrap();
        assert_eq!(second["addedDepthCapacityColumns"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn schema_creation_does_not_require_direction_columns() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("legacy.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE dex_candidate_pool_pairs (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    anchor_scope TEXT NOT NULL,
                    anchor_symbol TEXT NOT NULL,
                    address_pair TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair)
                );",
            )
            .unwrap();
        drop(connection);

        let payload = ensure_database_schema(path.to_str().unwrap()).unwrap();
        assert_eq!(payload["ok"], true);
        let connection = Connection::open(&path).unwrap();
        let missing: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_candidate_pairs_direction'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(missing, 0);
    }

    #[test]
    fn index_creation_runs_after_legacy_columns_exist() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("directional.sqlite3");
        ensure_database_schema(path.to_str().unwrap()).unwrap();

        let payload = ensure_database_indexes(path.to_str().unwrap()).unwrap();
        assert_eq!(payload["ok"], true);
        let connection = Connection::open(&path).unwrap();
        let created: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_candidate_pairs_direction'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(created, 1);
    }
}
