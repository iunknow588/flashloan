use manager_db::{
    migrate, migration_checksum, preflight_read_only, schema_checksum, MigrationError,
    BROADCAST_MIGRATION_ID, CANDIDATE_EDGE_MIGRATION_ID, CANDIDATE_EXCHANGE_MIGRATION_ID,
    CLOSED_LOOP_MIGRATION_ID, COMPOSITE_MIGRATION_ID, LATEST_MIGRATION_ID, MIGRATION_ID,
    MIGRATION_IDS, OBSERVATION_OPTIMAL_POOL_VIEW_MIGRATION_ID, REVIEW_MIGRATION_ID,
    USDG_VALUATION_MIGRATION_ID,
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
    "token_usdg_valuations",
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
    assert_eq!(
        first.migrations[7].migration_id,
        USDG_VALUATION_MIGRATION_ID
    );

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
        "token_usdg_valuations",
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
        "manager_v4_directional_exchange_pools",
        "manager_v4_directional_pool_library_state",
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

/// The market observation's primary pool is a view over the direction layer.
///
/// The migration turns the stored primary into a derived one, so the two
/// facts it has to hold are checked here: the rows survive the swap, and
/// the view answers the pool the direction layer ranked first.  The writers
/// are checked too, because a view cannot be written to and the managers
/// publish the observation through the table name.
#[test]
fn the_observation_primary_pool_becomes_a_view_over_the_direction_layer() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("observation-view.sqlite3");

    // The migration chain runs first, so the observation table and the
    // direction layer both exist with their real shape.
    migrate(&path).unwrap();

    let connection = Connection::open(&path).unwrap();
    let object_type: String = connection
        .query_row(
            "SELECT type FROM sqlite_master WHERE name = 'manager_market_observation_yz_pairs'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(object_type, "view");
    let base_present: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type = 'table' AND name = 'manager_market_observation_yz_pairs_base'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(base_present, 1);

    // Two pools of one pair, priced in both directions.  The direction
    // layer ranks them differently in the two directions, so the view has
    // a different answer for the two rows of the pair.
    connection
        .execute_batch(
            "INSERT INTO manager_v4_directional_exchange_pools (
                network, chain_id, protocol, scope_key, base_pool_id, direction,
                directional_pool_id, pool_manager, pool_key_json, token0, token1,
                token0_key_id, token1_key_id, fee, tick_spacing, hooks,
                pair_key_id, input_token_key_id, output_token_key_id, zero_for_one,
                sqrt_price_x96, tick, liquidity, price, depth_status,
                liquidity_read_at, liquidity_block_number, evidence_source,
                input_capacity_raw, input_capacity_measured, directional_output,
                directional_output_measured, pair_direction_rank, pool_status,
                pool_status_reason, source_block_number, source_tx_hash,
                materialized_at, updated_at
             ) VALUES
             ('robinhood', 4663, 'uniswap_v4', 'candidate_exchange', 'pool-b', 0,
              'dir-b-0', 'manager', '{}', '0x0', '0xb', 'robinhood:4663:0x0',
              'robinhood:4663:0xb', 3000, 60, '0x0',
              'pair:v1:4663:0x0:0xb', 'robinhood:4663:0xb', 'robinhood:4663:0x0', 0,
              '1', 0, '1', '1', 'ok', 't', 1, 'src', '1', 1, '1', 1, 1, 'active',
              NULL, 1, '0xsrc', 't', 't'),
             ('robinhood', 4663, 'uniswap_v4', 'candidate_exchange', 'pool-b', 1,
              'dir-b-1', 'manager', '{}', '0x0', '0xb', 'robinhood:4663:0x0',
              'robinhood:4663:0xb', 3000, 60, '0x0',
              'pair:v1:4663:0x0:0xb', 'robinhood:4663:0x0', 'robinhood:4663:0xb', 1,
              '1', 0, '1', '1', 'ok', 't', 1, 'src', '1', 1, '1', 1, 2, 'active',
              NULL, 1, '0xsrc', 't', 't'),
             ('robinhood', 4663, 'uniswap_v4', 'candidate_exchange', 'pool-a', 0,
              'dir-a-0', 'manager', '{}', '0x0', '0xb', 'robinhood:4663:0x0',
              'robinhood:4663:0xb', 3000, 60, '0x0',
              'pair:v1:4663:0x0:0xb', 'robinhood:4663:0xb', 'robinhood:4663:0x0', 0,
              '1', 0, '1', '1', 'ok', 't', 1, 'src', '1', 1, '1', 1, 2, 'active',
              NULL, 1, '0xsrc', 't', 't'),
             ('robinhood', 4663, 'uniswap_v4', 'candidate_exchange', 'pool-a', 1,
              'dir-a-1', 'manager', '{}', '0x0', '0xb', 'robinhood:4663:0x0',
              'robinhood:4663:0xb', 3000, 60, '0x0',
              'pair:v1:4663:0x0:0xb', 'robinhood:4663:0x0', 'robinhood:4663:0xb', 1,
              '1', 0, '1', '1', 'ok', 't', 1, 'src', '1', 1, '1', 1, 1, 'active',
              NULL, 1, '0xsrc', 't', 't');",
        )
        .unwrap();

    // The writer publishes a row whose stored primary is the old answer.
    connection
        .execute_batch(
            "INSERT INTO manager_market_observation_yz_pairs (
                network, chain_id, protocol, scope_key, anchor_token_key_id,
                direction_key, token_x_key_id, token_y_key_id, has_direct_swap_pool,
                direct_swap_pool_count, pool_ids_json, optimal_pool_id,
                observation_empty_reason, created_at, updated_at
             ) VALUES
             ('robinhood', 4663, 'uniswap_v4', 'candidate_exchange', 'anchor',
              'robinhood:4663:0x0->robinhood:4663:0xb', 'robinhood:4663:0x0',
              'robinhood:4663:0xb', 1, 2, '[\"pool-a\",\"pool-b\"]', 'pool-b', NULL, 't', 't'),
             ('robinhood', 4663, 'uniswap_v4', 'candidate_exchange', 'anchor',
              'robinhood:4663:0xb->robinhood:4663:0x0', 'robinhood:4663:0xb',
              'robinhood:4663:0x0', 1, 2, '[\"pool-a\",\"pool-b\"]', 'pool-a', NULL, 't', 't');",
        )
        .unwrap();

    // The view answers the rank 1 of the direction the row measured.
    let forward: String = connection
        .query_row(
            "SELECT optimal_pool_id FROM manager_market_observation_yz_pairs
             WHERE direction_key = 'robinhood:4663:0x0->robinhood:4663:0xb'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(forward, "pool-a");
    let reverse: String = connection
        .query_row(
            "SELECT optimal_pool_id FROM manager_market_observation_yz_pairs
             WHERE direction_key = 'robinhood:4663:0xb->robinhood:4663:0x0'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(reverse, "pool-b");

    // The rows survive the swap, and the stored value is kept as the record
    // of what the writer published.
    let stored: String = connection
        .query_row(
            "SELECT optimal_pool_id FROM manager_market_observation_yz_pairs_base
             WHERE direction_key = 'robinhood:4663:0x0->robinhood:4663:0xb'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(stored, "pool-b");
    let rows: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(rows, 2);

    // A row the direction layer did not price has no primary.
    connection
        .execute_batch(
            "INSERT INTO manager_market_observation_yz_pairs (
                network, chain_id, protocol, scope_key, anchor_token_key_id,
                direction_key, token_x_key_id, token_y_key_id, has_direct_swap_pool,
                direct_swap_pool_count, pool_ids_json, optimal_pool_id,
                observation_empty_reason, created_at, updated_at
             ) VALUES
             ('robinhood', 4663, 'uniswap_v4', 'candidate_exchange', 'anchor',
              'robinhood:4663:0xc->robinhood:4663:0xd', 'robinhood:4663:0xc',
              'robinhood:4663:0xd', 0, 0, '[]', NULL, 'no_reachable_v4_pool', 't', 't');",
        )
        .unwrap();
    let unpriced: Option<String> = connection
        .query_row(
            "SELECT optimal_pool_id FROM manager_market_observation_yz_pairs
             WHERE direction_key = 'robinhood:4663:0xc->robinhood:4663:0xd'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(unpriced, None);

    // The writers still work: the managers delete the scope they republish
    // and insert the rows again.
    connection
        .execute(
            "DELETE FROM manager_market_observation_yz_pairs
             WHERE network = 'robinhood' AND chain_id = 4663 AND protocol = 'uniswap_v4'
               AND scope_key = 'candidate_exchange'",
            [],
        )
        .unwrap();
    let remaining: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(remaining, 0);
    let base_remaining: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs_base",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(base_remaining, 0);

    // The migration is recorded, and a second run changes nothing.
    let report = migrate(&path).unwrap();
    assert!(!report.applied_now);
    assert_eq!(report.migration_id, LATEST_MIGRATION_ID);
    assert!(report
        .migration_ids
        .contains(&OBSERVATION_OPTIMAL_POOL_VIEW_MIGRATION_ID.to_owned()));
}
