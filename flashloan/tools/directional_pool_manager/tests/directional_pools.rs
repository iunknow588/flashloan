use directional_pool_manager::{
    materialize_directional_pools, materialize_directional_pools_write,
    read_directional_pool_snapshot, read_directional_pool_status,
    DirectionalPoolError, DirectionalPoolWriteRequest, ExpectedUpstream,
    MATERIALIZE_OPERATION,
};
use rusqlite::{params, Connection};

const NETWORK: &str = "robinhood";
const CHAIN_ID: i64 = 4663;
const SCOPE_KEY: &str = "candidate_exchange";
const TOKEN0: &str = "0x1111111111111111111111111111111111111111";
const TOKEN1: &str = "0x2222222222222222222222222222222222222222";
const POOL_MANAGER: &str = "0x3333333333333333333333333333333333333333";
const ZERO_HOOK: &str = "0x0000000000000000000000000000000000000000";
const HOOK: &str = "0x0000000000000000000000000000000000000001";
const DYNAMIC_FEE_FLAG: i64 = 8_388_608;

struct Fixture {
    _directory: tempfile::TempDir,
    path: std::path::PathBuf,
}

/// Build a database that carries one pool scope with a handful of pools.
fn fixture() -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("manager.sqlite3");
    manager_db::migrate(&path).unwrap();
    let connection = Connection::open(&path).unwrap();
    create_snapshot_table(&connection);
    seed_tokens(&connection);
    seed_scope(&connection);
    drop(connection);
    Fixture {
        _directory: directory,
        path,
    }
}

fn create_snapshot_table(connection: &Connection) {
    connection
        .execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS v4_exchange_pool_liquidity_snapshots (
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
                single_tick_max_token0_raw TEXT,
                single_tick_max_token1_raw TEXT,
                PRIMARY KEY (network, chain_id, protocol, pool_id)
            );
            "#,
        )
        .unwrap();
}

fn seed_tokens(connection: &Connection) {
    for (address, symbol) in [(TOKEN0, "TA"), (TOKEN1, "TB")] {
        connection
            .execute(
                "INSERT INTO token_core (
                     network, chain_id, token_key_id, v4_currency_key,
                     token_address, token_symbol, token_name, token_decimals,
                     token_kind, is_native, is_wrapped_native, is_stable,
                     admission_status, admission_source, metadata_source,
                     metadata_checksum, source_json, first_seen_at,
                     first_admitted_at, updated_at, blocked_reason
                 ) VALUES (?1, ?2, ?3, ?4, ?4, ?5, ?5, 18, 'erc20', 0, 0, 0,
                           'admitted', 'fixture', 'fixture', 'sha256:fixture',
                           '{}', '2026-11-04T00:00:00Z',
                           '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z', NULL)",
                params![
                    NETWORK,
                    CHAIN_ID,
                    format!("{NETWORK}:{CHAIN_ID}:{address}"),
                    address,
                    symbol
                ],
            )
            .unwrap();
    }
}

fn seed_scope(connection: &Connection) {
    connection
        .execute(
            r#"INSERT INTO pool_scope_config (
                 network, chain_id, scope_key, pool_table, library_keys_json,
                 library_versions_json, library_checksums_json, filter_mode,
                 excluded_token_key_ids_json, enabled, materialized_checksum,
                 last_rebuilt_at, created_at, updated_at
             ) VALUES (?1, ?2, ?3, 'v4_exchange_pools_candidate_exchange', '[]',
                       '{"core": 7}', '{"core": "sha256:core"}', 'both_end',
                       '[]', 1, 'sha256:scope-fixture',
                       '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z',
                       '2026-11-04T00:00:00Z')"#,
            params![NETWORK, CHAIN_ID, SCOPE_KEY],
        )
        .unwrap();
}

struct PoolSpec {
    fee: i64,
    hooks: String,
    dynamic_fee: i64,
    pool_status: &'static str,
    with_snapshot: bool,
    liquidity: &'static str,
    token0_capacity: Option<&'static str>,
    token1_capacity: Option<&'static str>,
}

impl Default for PoolSpec {
    fn default() -> Self {
        Self {
            fee: 3_000,
            hooks: ZERO_HOOK.to_owned(),
            dynamic_fee: 0,
            pool_status: "active",
            with_snapshot: true,
            liquidity: "1000000",
            // TASK-172 / AC-221: the depth snapshot table has exactly one
            // writer -- the pool manager -- which writes the price columns
            // and the single-tick bounds in **one** statement, so a stored
            // bound always belongs to the price next to it.
            //
            // The fixture therefore seeds the bound the row's own
            // observation implies: `sqrt_price_x96 = 2^96`, `tick = 0`,
            // `liquidity = 1000000`, `fee = 3000` and `tick_spacing = 60`
            // give a bound of 3015 raw units in both directions. A fixture
            // that seeded an arbitrary pair would describe a row whose
            // bound belongs to a different moment, which is exactly the
            // defect the single-writer rule removed.
            token0_capacity: Some("3015"),
            token1_capacity: Some("3015"),
        }
    }
}

fn insert_pool(connection: &Connection, spec: &PoolSpec, salt: &str) -> String {
    let pool_id = format!("0x{}", salt.repeat(64));
    connection
        .execute(
            "INSERT INTO v4_exchange_pools_candidate_exchange (
                 network, chain_id, protocol, pool_id, pool_manager,
                 pool_key_json, token0, token1, token0_key_id, token1_key_id,
                 fee, dynamic_fee, special_fee, tick_spacing, hooks,
                 pool_status, materialized_at, updated_at
             ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, '{}', ?5, ?6, ?7, ?8,
                       ?9, ?10, 0, 60, ?11, ?12,
                       '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z')",
            params![
                NETWORK,
                CHAIN_ID,
                pool_id,
                POOL_MANAGER,
                TOKEN0,
                TOKEN1,
                format!("{NETWORK}:{CHAIN_ID}:{TOKEN0}"),
                format!("{NETWORK}:{CHAIN_ID}:{TOKEN1}"),
                spec.fee,
                spec.dynamic_fee,
                spec.hooks,
                spec.pool_status,
            ],
        )
        .unwrap();
    if spec.with_snapshot {
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                     network, chain_id, protocol, pool_id, has_liquidity,
                     liquidity_index, sqrt_price_x96, tick, liquidity, price,
                     depth_status, liquidity_read_at, liquidity_block_number,
                     evidence_source, single_tick_max_token0_raw,
                     single_tick_max_token1_raw
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, 1, 10.0,
                           '79228162514264337593543950336', 0, ?4, 1.0,
                           'deep', '2026-11-04T00:00:00Z', 100, 'fixture', ?5, ?6)",
                params![
                    NETWORK,
                    CHAIN_ID,
                    pool_id,
                    spec.liquidity,
                    spec.token0_capacity,
                    spec.token1_capacity,
                ],
            )
            .unwrap();
    }
    pool_id
}

fn write_request(
    idempotency_key: &str,
    apply: bool,
    expected_upstream: ExpectedUpstream,
) -> DirectionalPoolWriteRequest {
    DirectionalPoolWriteRequest {
        network: NETWORK.to_owned(),
        chain_id: CHAIN_ID,
        scope_key: SCOPE_KEY.to_owned(),
        operation: MATERIALIZE_OPERATION.to_owned(),
        request_id: format!("req-{idempotency_key}"),
        idempotency_key: idempotency_key.to_owned(),
        apply,
        expected_upstream,
    }
}

#[test]
fn one_base_pool_projects_onto_exactly_two_rows() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);

    let result = materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, false)
        .unwrap();
    assert_eq!(result.base_pool_count, 1);
    assert_eq!(result.directional_pool_count, 2);
    assert_eq!(result.direction0_count, 1);
    assert_eq!(result.direction1_count, 1);
    assert_eq!(result.excluded_pool_count, 0);
    assert!(result.dry_run);
}

#[test]
fn the_two_rows_of_one_base_pool_differ_only_in_the_direction_fields() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    assert_eq!(snapshot.rows.len(), 2);
    let low = snapshot
        .rows
        .iter()
        .find(|row| row.direction == 0)
        .expect("direction 0 row");
    let high = snapshot
        .rows
        .iter()
        .find(|row| row.direction == 1)
        .expect("direction 1 row");

    // The identity of the base pool is the same.
    assert_eq!(low.base_pool_id, high.base_pool_id);
    assert_eq!(low.pool_key_json, high.pool_key_json);
    assert_eq!(low.fee, high.fee);
    assert_eq!(low.pair_key_id, high.pair_key_id);
    assert_eq!(low.pool_status, high.pool_status);
    // The direction fields are the mirror image of each other.
    assert_eq!(low.input_token_key_id, high.output_token_key_id);
    assert_eq!(low.output_token_key_id, high.input_token_key_id);
    assert!(!low.zero_for_one);
    assert!(high.zero_for_one);
    assert_ne!(low.directional_pool_id, high.directional_pool_id);
}

#[test]
fn the_capacity_comes_from_the_input_side_of_the_direction() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    let direction0 = snapshot.rows.iter().find(|row| row.direction == 0).unwrap();
    let direction1 = snapshot.rows.iter().find(|row| row.direction == 1).unwrap();

    // The capacity is **recomputed** from the observation the row carries
    // (TASK-172 / AC-221), so it is the bound that the row's own price
    // implies -- not a number that was published next to a different price.
    let expected = pool_manager::depth::recompute_single_tick_bounds(
        79_228_162_514_264_337_593_543_950_336,
        1_000_000,
        3_000,
        60,
        0,
    );
    // Direction 0 spends token1, so it carries the token1 bound.
    assert_eq!(
        direction0.input_capacity_raw.as_deref(),
        expected.1.map(|value| value.to_string()).as_deref()
    );
    assert!(direction0.input_capacity_measured);
    // Direction 1 spends token0, so it carries the token0 bound.
    assert_eq!(
        direction1.input_capacity_raw.as_deref(),
        expected.0.map(|value| value.to_string()).as_deref()
    );
    assert!(direction1.input_capacity_measured);
}

#[test]
fn an_unmeasured_capacity_is_not_a_measured_zero() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    let spec = PoolSpec {
        with_snapshot: false,
        ..PoolSpec::default()
    };
    insert_pool(&connection, &spec, "a");
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    assert_eq!(snapshot.rows.len(), 2);
    for row in &snapshot.rows {
        assert_eq!(row.input_capacity_raw, None);
        assert!(!row.input_capacity_measured);
        assert_eq!(row.directional_output, None);
        assert!(!row.directional_output_measured);
        // An unpriced row has no rank: "not measured" and "measured as
        // zero" are different facts.
        assert_eq!(row.pair_direction_rank, None);
    }
}

#[test]
fn the_ranks_are_continuous_inside_a_pair_and_a_direction() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    insert_pool(
        &connection,
        &PoolSpec {
            // A deeper pool pays less price impact for the same trade, so
            // it is the better pool in both directions.
            liquidity: "100000000",
            ..PoolSpec::default()
        },
        "b",
    );
    insert_pool(
        &connection,
        &PoolSpec {
            liquidity: "100000",
            ..PoolSpec::default()
        },
        "c",
    );
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    for direction in [0, 1] {
        let mut ranks: Vec<i64> = snapshot
            .rows
            .iter()
            .filter(|row| row.direction == direction)
            .map(|row| row.pair_direction_rank.expect("ranked row"))
            .collect();
        ranks.sort_unstable();
        assert_eq!(ranks, vec![1, 2, 3]);
    }
    // The deepest pool pays the least price impact, so it ranks first in
    // both directions.
    let first_direction0 = snapshot
        .rows
        .iter()
        .find(|row| row.direction == 0 && row.pair_direction_rank == Some(1))
        .unwrap();
    assert_eq!(first_direction0.liquidity.as_deref(), Some("100000000"));
}

#[test]
fn a_dynamic_fee_pool_and_a_hook_pool_are_not_projected() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    insert_pool(
        &connection,
        &PoolSpec {
            fee: DYNAMIC_FEE_FLAG,
            ..PoolSpec::default()
        },
        "b",
    );
    insert_pool(
        &connection,
        &PoolSpec {
            hooks: HOOK.to_owned(),
            ..PoolSpec::default()
        },
        "c",
    );
    drop(connection);

    let result = materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, false)
        .unwrap();
    assert_eq!(result.base_pool_count, 3);
    assert_eq!(result.excluded_pool_count, 2);
    assert_eq!(result.directional_pool_count, 2);
}

#[test]
fn a_blocked_pool_is_projected_but_it_never_takes_a_rank() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(
        &connection,
        &PoolSpec {
            pool_status: "blocked",
            ..PoolSpec::default()
        },
        "a",
    );
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    assert_eq!(snapshot.rows.len(), 2);
    for row in &snapshot.rows {
        assert_eq!(row.pool_status, "blocked");
        assert_eq!(row.pair_direction_rank, None);
    }
}

#[test]
fn the_materialization_is_idempotent() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);

    let first = materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true)
        .unwrap();
    assert_eq!(first.changed_rows, 2);
    let second = materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true)
        .unwrap();
    assert_eq!(second.changed_rows, 0);
    assert_eq!(second.materialized_checksum, first.materialized_checksum);
    assert_eq!(second.directional_pool_version, first.directional_pool_version);
    assert!(!second.consumers_need_rebuild);
}

#[test]
fn a_dry_run_writes_nothing() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);

    let result = materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, false)
        .unwrap();
    assert!(result.dry_run);
    assert_eq!(result.changed_rows, 2);

    let connection = Connection::open(&fixture.path).unwrap();
    let rows: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_v4_directional_exchange_pools",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(rows, 0);
    let requests: i64 = connection
        .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(requests, 0);
}

#[test]
fn the_idempotency_key_replays_the_first_result() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);

    let request = write_request("key-1", true, ExpectedUpstream::default());
    let first = materialize_directional_pools_write(&fixture.path, request).unwrap();
    assert!(!first.idempotent_replay);

    let replay = materialize_directional_pools_write(
        &fixture.path,
        write_request("key-1", true, ExpectedUpstream::default()),
    )
    .unwrap();
    assert!(replay.idempotent_replay);
    assert_eq!(replay.result.materialized_checksum, first.result.materialized_checksum);
    assert_eq!(replay.result.changed_rows, first.result.changed_rows);
}

#[test]
fn a_reused_idempotency_key_with_a_different_body_is_refused() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);

    materialize_directional_pools_write(
        &fixture.path,
        write_request("key-1", true, ExpectedUpstream::default()),
    )
    .unwrap();

    let reused = materialize_directional_pools_write(
        &fixture.path,
        write_request(
            "key-1",
            true,
            ExpectedUpstream {
                pool_scope_checksum: Some("sha256:something-else".to_owned()),
                ..ExpectedUpstream::default()
            },
        ),
    );
    assert!(matches!(
        reused,
        Err(DirectionalPoolError::IdempotencyKeyReused { .. })
    ));
}

#[test]
fn the_status_reports_what_the_layer_published() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);

    let empty = read_directional_pool_status(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY).unwrap();
    assert!(!empty.enabled);
    assert_eq!(empty.directional_pool_count, 0);

    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();
    let status = read_directional_pool_status(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY).unwrap();
    assert!(status.enabled);
    assert_eq!(status.base_pool_count, 1);
    assert_eq!(status.directional_pool_count, 2);
    assert_eq!(status.direction0_count, 1);
    assert_eq!(status.direction1_count, 1);
    assert_eq!(status.directional_pool_version, 1);
    assert!(status.materialized_checksum.is_some());
}

#[test]
fn the_snapshot_can_be_read_per_direction() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let direction0 =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, Some(0), 10)
            .unwrap();
    assert_eq!(direction0.rows.len(), 1);
    assert_eq!(direction0.rows[0].direction, 0);
    assert!(read_directional_pool_snapshot(
        &fixture.path,
        NETWORK,
        CHAIN_ID,
        SCOPE_KEY,
        Some(2),
        10
    )
    .is_err());
}

#[test]
fn an_expected_upstream_mismatch_stops_the_write() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);

    let outcome = materialize_directional_pools_write(
        &fixture.path,
        write_request(
            "key-1",
            true,
            ExpectedUpstream {
                pool_scope_checksum: Some("sha256:stale".to_owned()),
                ..ExpectedUpstream::default()
            },
        ),
    );
    assert!(matches!(
        outcome,
        Err(DirectionalPoolError::InvalidRequest(_))
    ));
}
