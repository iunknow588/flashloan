use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use std::path::Path;

const BUSINESS_DDL_SQL: &str = include_str!("../schema/business_schema.sql");
const BUSINESS_INDEXES_SQL: &str = include_str!("../schema/business_indexes.sql");

pub(crate) fn open_rw(database_path: &str) -> Result<Connection, String> {
    Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| format!("could not open SQLite for schema {database_path}: {error}"))
}

fn execute_batch(connection: &Connection, sql: &str) -> Result<(), String> {
    connection
        .execute_batch(sql)
        .map_err(|error| format!("SQLite schema batch failed: {error}"))
}

pub fn ensure_database_schema(database_path: &str) -> Result<Value, String> {
    let path = Path::new(database_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("could not create schema directory {}: {error}", parent.display()))?;
    }
    let connection = open_rw(database_path)?;
    execute_batch(&connection, BUSINESS_DDL_SQL)?;
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
                &serde_json::to_string("directional_pairs_v1").map_err(|error| error.to_string())?,
            ],
        )
        .map_err(|error| format!("SQLite kv_meta business_schema_version failed: {error}"))?;
    Ok(json!({
        "ok": true,
        "databasePath": database_path,
        "schemaVersion": "directional_pairs_v1",
        "ddlStatementCount": BUSINESS_DDL_SQL.trim().split(';').filter(|part| !part.trim().is_empty()).count(),
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
