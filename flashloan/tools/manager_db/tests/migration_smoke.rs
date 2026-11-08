use manager_db::{
    migrate, migration_checksum, preflight_read_only, schema_checksum, MigrationError,
    BROADCAST_MIGRATION_ID, CANDIDATE_EDGE_MIGRATION_ID, CANDIDATE_EXCHANGE_MIGRATION_ID,
    CLOSED_LOOP_MIGRATION_ID, COMPOSITE_MIGRATION_ID, LATEST_MIGRATION_ID, MIGRATION_ID,
    MIGRATION_IDS, REVIEW_MIGRATION_ID,
};
use rusqlite::Connection;
use std::path::Path;

const TARGET_TABLES: &[&str] = &[
    "token_core",
    "token_stocks",
    "token_extension",
    "token_test",
    "token_library_state",
    "token_admission_events",
    "manager_write_requests",
    "token_library_catalog",
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

#[test]
fn migration_is_idempotent_and_creates_only_empty_control_schema() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("manager.sqlite3");

    let first = migrate(&path).unwrap();
    let first_checksum = first.checksum.clone();
    assert!(first.applied_now);
    assert_eq!(first.migration_id, LATEST_MIGRATION_ID);
    assert_eq!(first.migration_ids, MIGRATION_IDS);
    assert_eq!(
        first_checksum,
        first.migration_checksums[LATEST_MIGRATION_ID]
    );
    assert_eq!(
        first.migration_checksums[MIGRATION_ID],
        migration_checksum()
    );
    assert_eq!(first.schema_checksum, schema_checksum());
    assert_eq!(first.migrations[0].migration_id, MIGRATION_ID);
    assert_eq!(
        first.migrations[1].migration_id,
        CANDIDATE_EDGE_MIGRATION_ID
    );
    assert_eq!(
        first.migrations[2].migration_id,
        CANDIDATE_EXCHANGE_MIGRATION_ID
    );
    assert_eq!(first.migrations[3].migration_id, CLOSED_LOOP_MIGRATION_ID);
    assert_eq!(first.migrations[4].migration_id, REVIEW_MIGRATION_ID);
    assert_eq!(first.migrations[5].migration_id, BROADCAST_MIGRATION_ID);
    assert_eq!(first.migrations[6].migration_id, COMPOSITE_MIGRATION_ID);

    let second = migrate(&path).unwrap();
    assert!(!second.applied_now);
    assert_eq!(second.checksum, first_checksum);
    assert_eq!(second.schema_checksum, first.schema_checksum);

    let connection = Connection::open(path).unwrap();
    for table in TARGET_TABLES {
        let present: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = ?1 AND type IN ('table', 'view')",
                [table],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(present, 1, "missing {table}");
    }
    for table in [
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
    ] {
        let count: i64 = connection
            .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 0, "M0 must not import data into {table}");
    }
}

#[test]
fn existing_foreign_schema_migration_ledger_uses_manager_fallback() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("foreign-ledger.sqlite3");
    let connection = Connection::open(&path).unwrap();
    connection
        .execute_batch(
            "CREATE TABLE schema_migrations (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES ('legacy-001', 'legacy');",
        )
        .unwrap();
    drop(connection);

    let report = migrate(&path).unwrap();
    assert_eq!(report.migration_table, "manager_schema_migrations");
    assert!(report.applied_now);

    let connection = Connection::open(path).unwrap();
    let legacy_count: i64 = connection
        .query_row("SELECT COUNT(*) FROM schema_migrations", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(legacy_count, 1);
}

#[test]
fn read_only_preflight_does_not_apply_migration() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("preflight.sqlite3");
    Connection::open(&path).unwrap().close().unwrap();
    let report = preflight_read_only(&path).unwrap();
    assert_eq!(report.quick_check, "ok");
    assert!(report.target_tables.iter().all(|table| !table.present));
    assert!(report
        .target_table_compatibility
        .iter()
        .all(|table| table.compatible));
    assert!(report.ready_for_migration);
    assert!(!report.view_present);
    assert_eq!(report.migration_ledger, "schema_migrations");

    let connection = Connection::open(Path::new(&report.path)).unwrap();
    let present: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'token_core'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(present, 0);
}

#[test]
fn incompatible_manager_target_table_blocks_migration_atomically() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("incompatible-manager-table.sqlite3");
    let connection = Connection::open(&path).unwrap();
    connection
        .execute_batch(
            "CREATE TABLE manager_dex_candidate_pools (
                id TEXT PRIMARY KEY
            );",
        )
        .unwrap();
    drop(connection);

    let error = migrate(&path).unwrap_err();
    assert!(matches!(
        error,
        MigrationError::TargetTableIncompatible { .. }
    ));
    let connection = Connection::open(path).unwrap();
    let token_core_present: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type = 'table' AND name = 'token_core'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(token_core_present, 0);
    let ledger: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type = 'table' AND name = 'schema_migrations'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(ledger, 0);
}

#[test]
fn legacy_candidate_tables_are_not_reused_by_manager_shadow_migration() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("legacy-candidate.sqlite3");
    let connection = Connection::open(&path).unwrap();
    connection
        .execute_batch(
            "CREATE TABLE dex_candidate_pools (
                network TEXT,
                chain_id INTEGER,
                marker TEXT
            );
            INSERT INTO dex_candidate_pools VALUES ('robinhood', 4663, 'legacy');",
        )
        .unwrap();
    drop(connection);

    migrate(&path).unwrap();
    let connection = Connection::open(path).unwrap();
    let marker: String = connection
        .query_row("SELECT marker FROM dex_candidate_pools", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(marker, "legacy");
    let manager_present: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type = 'table' AND name = 'manager_dex_candidate_pools'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(manager_present, 1);
}
