use directional_pool_manager::{
    compare_directional_pools, materialize_directional_pools,
    materialize_directional_pools_for_chain, materialize_directional_pools_write,
    read_directional_pool_snapshot, read_directional_pool_status,
    candidate_tier_size, max_direction_candidate_pools, tier_of_rank,
    DirectionalPoolTier, DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS,
    MAX_DIRECTION_CANDIDATE_POOLS_ENV,
    DirectionalPoolError, DirectionalPoolWriteRequest, ExpectedUpstream,
    MATERIALIZE_OPERATION,
};
use candidate_edge_pool_manager::{price_directional_output, DirectionalPriceInput};
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
    /// The square root price the snapshot carries.
    ///
    /// `None` keeps the fixture default: one unit of the high address
    /// token for one unit of the low address one.  A test that needs
    /// **two pools of the same pair at different prices** sets it, and
    /// then sets the tick that belongs to that price as well -- a price
    /// and a tick that disagree describe no pool that ever existed.
    sqrt_price_x96: Option<String>,
    /// The tick that belongs to `sqrt_price_x96`.
    tick: Option<i64>,
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
            sqrt_price_x96: None,
            tick: None,
        }
    }
}

fn insert_pool(connection: &Connection, spec: &PoolSpec, salt: &str) -> String {
    insert_pool_into(connection, "v4_exchange_pools_candidate_exchange", spec, salt)
}

/// The same insert, but into a scope table of the caller's choosing.  The
/// chain entry materializes every enabled scope, so a test that covers more
/// than one scope needs to seed more than one scope table.
fn insert_pool_into(
    connection: &Connection,
    table: &str,
    spec: &PoolSpec,
    salt: &str,
) -> String {
    let pool_id = format!("0x{}", salt.repeat(64));
    let sql = format!(
        "INSERT INTO {table} (
             network, chain_id, protocol, pool_id, pool_manager,
             pool_key_json, token0, token1, token0_key_id, token1_key_id,
             fee, dynamic_fee, special_fee, tick_spacing, hooks,
             pool_status, materialized_at, updated_at
         ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, '{{}}', ?5, ?6, ?7, ?8,
                   ?9, ?10, 0, 60, ?11, ?12,
                   '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z')"
    );
    connection
        .execute(
            &sql,
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
        let sqrt_price_x96 = spec
            .sqrt_price_x96
            .as_deref()
            .unwrap_or("79228162514264337593543950336");
        let tick = spec.tick.unwrap_or(0);
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                     network, chain_id, protocol, pool_id, has_liquidity,
                     liquidity_index, sqrt_price_x96, tick, liquidity, price,
                     depth_status, liquidity_read_at, liquidity_block_number,
                     evidence_source, single_tick_max_token0_raw,
                     single_tick_max_token1_raw
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, 1, 10.0,
                           ?7, ?8, ?4, 1.0,
                           'deep', '2026-11-04T00:00:00Z', 100, 'fixture', ?5, ?6)",
                params![
                    NETWORK,
                    CHAIN_ID,
                    pool_id,
                    spec.liquidity,
                    spec.token0_capacity,
                    spec.token1_capacity,
                    sqrt_price_x96,
                    tick,
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

/// The reference observation of the fixture pools.
///
/// The fixture publishes `sqrt_price_x96 = 2^96`, `tick = 0` and a
/// `tick_spacing` of 60, so the price of every fixture pool sits in the
/// middle of its tick range and a reference trade of a fraction of the
/// pool's own capacity stays inside that range.
const FIXTURE_SQRT_PRICE_X96: &str = "79228162514264337593543950336";

/// The key id of one fixture token.
fn token_key_id(address: &str) -> String {
    format!("{NETWORK}:{CHAIN_ID}:{address}")
}

/// The pool id the fixture gives to a salt.
fn pool_id_of(salt: &str) -> String {
    format!("0x{}", salt.repeat(64))
}

/// T-D-3: the rank of a direction follows the **candidate edge
/// recommendation order**.
///
/// The candidate edge pool manager ranks the pools of one hop by
///
///   1. the exact single-tick output, descending,
///   2. the tick ordering rank, descending,
///   3. the fee, ascending,
///   4. the pool id, ascending.
///
/// The directional layer ranks the rows of one `(pair_key_id, direction)`
/// group by that group's price key.  The candidate edge layer used to rank
/// the same group itself; it was removed, and the candidate layer now
/// reads the rank this table publishes.  The order of the directional
/// table is therefore the order the candidate layer publishes, and the
/// closed loop can switch over without moving a pool.
///
/// The test recomputes the expected order from that public function and
/// compares it with the published ranks, so it fails as soon as either
/// side changes its ordering key.
#[test]
fn the_rank_follows_the_candidate_edge_recommendation_order() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    // Three pools of the same pair with three different depths.  A deeper
    // pool absorbs a larger reference trade inside its tick range, so it
    // pays a better rate and has to rank first.
    let specs = [("a", "100000000"), ("b", "1000000"), ("c", "100000")];
    for (salt, liquidity) in specs {
        insert_pool(
            &connection,
            &PoolSpec {
                liquidity,
                ..PoolSpec::default()
            },
            salt,
        );
    }
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    let token0_key_id = token_key_id(TOKEN0);
    let token1_key_id = token_key_id(TOKEN1);

    for direction in [0, 1] {
        // Direction 0 spends token1, direction 1 spends token0.
        let input_key_id = if direction == 0 {
            token1_key_id.as_str()
        } else {
            token0_key_id.as_str()
        };

        // The expected order, recomputed from the same price function the
        // candidate edge manager sorts on.
        let mut expected: Vec<(f64, i64, i64, String)> = Vec::new();
        for (salt, liquidity) in specs {
            let pool_id = pool_id_of(salt);
            let price = price_directional_output(
                &DirectionalPriceInput {
                    pool_id: pool_id.clone(),
                    token0_key_id: token0_key_id.clone(),
                    token1_key_id: token1_key_id.clone(),
                    fee: 3_000,
                    tick_spacing: 60,
                    sqrt_price_x96: Some(FIXTURE_SQRT_PRICE_X96.to_owned()),
                    tick: Some(0),
                    liquidity: Some(liquidity.to_owned()),
                },
                input_key_id,
                18,
                18,
            )
            .expect("the fixture pools are priceable");
            expected.push((
                price.output_rate,
                price.tick_ordering_rank.unwrap_or(i64::MIN),
                3_000,
                pool_id,
            ));
        }
        expected.sort_by(|left, right| {
            right
                .0
                .partial_cmp(&left.0)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| right.1.cmp(&left.1))
                .then_with(|| left.2.cmp(&right.2))
                .then_with(|| left.3.cmp(&right.3))
        });

        for (position, (_, _, _, pool_id)) in expected.iter().enumerate() {
            let row = snapshot
                .rows
                .iter()
                .find(|row| row.direction == direction && row.base_pool_id == *pool_id)
                .expect("the pool is projected onto both directions");
            assert_eq!(
                row.pair_direction_rank,
                Some(position as i64 + 1),
                "direction {direction} ranks {pool_id} at {:?}, but the candidate edge recommendation order puts it at {}",
                row.pair_direction_rank,
                position + 1
            );
        }
    }
}

/// The price order of a direction pool is **fixed**: the direction picks the
/// token a row is ranked for, and the order itself never turns around.
///
/// Direction 0 (high address to low address) spends the high address token
/// and delivers the low address one; direction 1 spends the low address
/// token and delivers the high address one.  The two directions therefore
/// rank the **same** base pools in the **opposite** order: the pool with
/// the best price of the high address token comes **first** in direction 1
/// and **last** in direction 0.
///
/// The other spelling of the very same rule keeps one price series -- the
/// price of the high address token -- and lets the direction choose the
/// sort direction: direction 1 **descends** on that price, direction 0
/// **ascends** on it.  Both spellings have to agree, and the test pins
/// both of them, so a later "simplification" of the sort key cannot flip
/// one direction silently.
#[test]
fn the_direction_picks_the_target_token_and_never_flips_the_order() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    // Three pools of the same pair at three different prices: the high
    // address token is worth 1.00, 1.21 and 1.44 units of the low address
    // one, so the square root price is 1.0, 1.1 and 1.2 times Q96, and
    // the tick is the one that belongs to that price -- a price and a tick
    // that disagree describe no pool that ever existed.
    const Q96: u128 = 79_228_162_514_264_337_593_543_950_336;
    let specs = [
        ("a", Q96, 0i64, 1.00f64),
        ("b", Q96 * 11 / 10, 1906i64, 1.21f64),
        ("c", Q96 * 12 / 10, 3646i64, 1.44f64),
    ];
    for (salt, sqrt_price_x96, tick, _price) in specs {
        insert_pool(
            &connection,
            &PoolSpec {
                sqrt_price_x96: Some(sqrt_price_x96.to_string()),
                tick: Some(tick),
                // The liquidity has to be **realistic**.  The single-tick
                // capacity of a pool is `liquidity * (the price gap)` in
                // **raw** token units, so a fixture liquidity of 1e6
                // leaves a capacity of a few hundred raw units: the
                // reference trade is then a few dozen units and the
                // integer rounding of the quote eats about one percent of
                // the rate.  That would measure the rounding of the
                // fixture, not the price order the test is about.  At 1e18
                // -- the order of magnitude of a mainnet pool -- the
                // rounding is below 1e-13 and the rate is the price.
                liquidity: "1000000000000000000",
                // The stored bound belongs to the fixture's own default
                // observation, which is not the one this pool carries;
                // the published capacity is recomputed, so the stored one
                // is absent here on purpose.
                token0_capacity: None,
                token1_capacity: None,
                ..PoolSpec::default()
            },
            salt,
        );
    }
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    let token0_key_id = token_key_id(TOKEN0);
    let token1_key_id = token_key_id(TOKEN1);

    // The direction decides **which token** the row is about, and the
    // token addresses stay in their canonical order (token0 is the low
    // address one), so direction 0 spends token1 and delivers token0.
    for row in &snapshot.rows {
        let (input, output, zero_for_one) = if row.direction == 0 {
            (token1_key_id.as_str(), token0_key_id.as_str(), false)
        } else {
            (token0_key_id.as_str(), token1_key_id.as_str(), true)
        };
        assert_eq!(
            row.input_token_key_id, input,
            "direction {} must spend the {input} token",
            row.direction
        );
        assert_eq!(
            row.output_token_key_id, output,
            "direction {} must deliver the {output} token",
            row.direction
        );
        assert_eq!(
            row.zero_for_one, zero_for_one,
            "direction {} must keep the swap side it is named after",
            row.direction
        );
    }

    // The order of the direction that delivers the high address token is
    // the price of that token, descending; the order of the direction
    // that delivers the low address token is the **same** price,
    // ascending.  The two orders are therefore exact opposites.
    let order_of = |direction: usize| -> Vec<String> {
        let mut rows: Vec<(i64, String)> = snapshot
            .rows
            .iter()
            .filter(|row| row.direction == direction as i64)
            .map(|row| (row.pair_direction_rank.expect("a ranked row"), row.base_pool_id.clone()))
            .collect();
        rows.sort_by_key(|(rank, _)| *rank);
        rows.into_iter().map(|(_, pool_id)| pool_id).collect()
    };
    let direction1 = order_of(1);
    let direction0 = order_of(0);
    let pool_a = pool_id_of("a");
    let pool_b = pool_id_of("b");
    let pool_c = pool_id_of("c");
    // The high address token is worth more in pool c than in pool b than
    // in pool a, so the direction that delivers it ranks them that way,
    // and the direction that delivers the low address token ranks them
    // the other way round.
    assert_eq!(
        direction1,
        vec![pool_c.clone(), pool_b.clone(), pool_a.clone()],
        "direction 1 delivers the high address token, so it ranks by that price, descending"
    );
    assert_eq!(
        direction0,
        vec![pool_a.clone(), pool_b.clone(), pool_c.clone()],
        "direction 0 delivers the low address token, so it ranks by the same price, ascending"
    );

    // The published rate is the output of the direction per unit of input,
    // so the two directions of one pool are **not** the same number: the
    // one that delivers the high address token is the high address price
    // itself, the other one is its reciprocal, both reduced by the fee.
    let rate_of = |direction: i64, pool_id: &str| -> f64 {
        snapshot
            .rows
            .iter()
            .find(|row| row.direction == direction && row.base_pool_id == pool_id)
            .expect("the pool is projected onto both directions")
            .directional_output
            .expect("a priced row")
    };
    for (pool_id, price) in [(&pool_a, 1.00f64), (&pool_b, 1.21f64), (&pool_c, 1.44f64)] {
        let rate1 = rate_of(1, pool_id);
        let rate0 = rate_of(0, pool_id);
        assert!(
            (rate1 / price - 0.997).abs() < 0.002,
            "direction 1 delivers the high address token: {pool_id} has to publish the price {price} less the fee, not {rate1}"
        );
        assert!(
            (rate0 * price - 0.997).abs() < 0.002,
            "direction 0 delivers the low address token: {pool_id} has to publish the price {} less the fee, not {rate0}",
            1.0 / price
        );
    }
}

/// The ranking compares **price and fee together**.
///
/// The published rate is the output of the swap, and the swap already
/// carries the fee -- the fee is taken off the input before the curve is
/// walked.  Two pools at the **same** price are therefore separated by
/// their fee, and the **lower** fee comes first -- in **both** directions,
/// because a lower fee improves the rate of a direction in the same way
/// wherever it sits.
///
/// This is the second half of the criterion for a direction problem: a
/// difference between two rates has to be explainable by the **price** and
/// the **fixed fee** of the two pools.  A third explanation -- a dynamic
/// fee -- is impossible, because a dynamic fee pool is not projected at
/// all (see the next test).
#[test]
fn the_lower_fixed_fee_wins_between_pools_at_the_same_price() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    // The same price, the same depth, two fixed fee tiers.
    insert_pool(
        &connection,
        &PoolSpec {
            fee: 3_000,
            liquidity: "1000000000000000000",
            ..PoolSpec::default()
        },
        "a",
    );
    insert_pool(
        &connection,
        &PoolSpec {
            fee: 500,
            liquidity: "1000000000000000000",
            ..PoolSpec::default()
        },
        "b",
    );
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    let pool_a = pool_id_of("a");
    let pool_b = pool_id_of("b");
    for direction in [0, 1] {
        let rank_of = |pool_id: &str| -> i64 {
            snapshot
                .rows
                .iter()
                .find(|row| row.direction == direction && row.base_pool_id == pool_id)
                .expect("both pools are projected onto both directions")
                .pair_direction_rank
                .expect("a ranked row")
        };
        assert_eq!(
            rank_of(&pool_b),
            1,
            "direction {direction}: the lower fee pays more, so it comes first"
        );
        assert_eq!(rank_of(&pool_a), 2);

        // The two rates differ by the fee and by nothing else.
        let rate_of = |pool_id: &str| -> f64 {
            snapshot
                .rows
                .iter()
                .find(|row| row.direction == direction && row.base_pool_id == pool_id)
                .expect("both pools are projected onto both directions")
                .directional_output
                .expect("a priced row")
        };
        let ratio = rate_of(&pool_b) / rate_of(&pool_a);
        let expected = (1_000_000.0 - 500.0) / (1_000_000.0 - 3_000.0);
        assert!(
            (ratio - expected).abs() < 0.0005,
            "direction {direction}: the rate difference has to be the fee difference +             ({expected}), not {ratio}"
        );
    }
}

/// A rate difference between two **ranked** pools can only come from the
/// price and the fixed fee.  A **dynamic fee** pool is not projected at
/// all, so it can never be the pool that "looks better" and shifts the
/// order -- no matter how good its price is.
///
/// That is what makes a rate difference diagnosable: the explanation is
/// the price, the fixed fee, or a defect.  A defect shows up in the
/// **order** -- the pool that comes later must not be the better one --
/// and that is exactly what the direction test above pins.
#[test]
fn a_dynamic_fee_pool_is_not_ranked_however_good_its_price_is() {
    const Q96: u128 = 79_228_162_514_264_337_593_543_950_336;
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    // Two fixed fee pools, and one pool with the **best** price that is
    // charged a dynamic fee.
    insert_pool(
        &connection,
        &PoolSpec {
            liquidity: "1000000000000000000",
            ..PoolSpec::default()
        },
        "a",
    );
    insert_pool(
        &connection,
        &PoolSpec {
            liquidity: "1000000000000000000",
            sqrt_price_x96: Some((Q96 * 11 / 10).to_string()),
            tick: Some(1906),
            ..PoolSpec::default()
        },
        "b",
    );
    insert_pool(
        &connection,
        &PoolSpec {
            liquidity: "1000000000000000000",
            sqrt_price_x96: Some((Q96 * 12 / 10).to_string()),
            tick: Some(3646),
            fee: DYNAMIC_FEE_FLAG,
            ..PoolSpec::default()
        },
        "c",
    );
    drop(connection);

    let result = materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true)
        .unwrap();
    assert_eq!(result.base_pool_count, 3);
    assert_eq!(result.excluded_pool_count, 1);
    // The dynamic fee pool has no row at all, so it has no rank.
    assert_eq!(result.directional_pool_count, 4);

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    let pool_c = pool_id_of("c");
    assert!(
        snapshot.rows.iter().all(|row| row.base_pool_id != pool_c),
        "a dynamic fee pool must not reach the directional layer"
    );
    let pool_a = pool_id_of("a");
    let pool_b = pool_id_of("b");
    for direction in [0, 1] {
        let ranks: Vec<(i64, String)> = snapshot
            .rows
            .iter()
            .filter(|row| row.direction == direction)
            .map(|row| {
                (
                    row.pair_direction_rank.expect("a ranked row"),
                    row.base_pool_id.clone(),
                )
            })
            .collect();
        assert_eq!(ranks.len(), 2);
        let mut ranks = ranks;
        ranks.sort_by_key(|(rank, _)| *rank);
        // The better price of the high address token is the better pool
        // in direction 1 and the worse pool in direction 0.
        let expected = if direction == 1 {
            vec![pool_b.clone(), pool_a.clone()]
        } else {
            vec![pool_a.clone(), pool_b.clone()]
        };
        let published: Vec<String> = ranks.into_iter().map(|(_, pool_id)| pool_id).collect();
        assert_eq!(published, expected);
    }
}

/// T-D-14: the published capacity is **recomputed**, never read from the
/// stored `single_tick_max_token*_raw` column.
///
/// The stored bound belongs to the observation the pool manager published
/// **with** it.  A row whose price came from a different observation would
/// therefore carry a bound of another on-chain moment.  The directional
/// layer recomputes the bound from the observation the row itself holds, so
/// a drifted stored column can never reach the closed loop.
#[test]
fn the_capacity_is_recomputed_and_not_read_from_the_stored_column() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    // The stored bounds deliberately disagree with the observation the row
    // carries: they are the leftovers of an earlier on-chain moment.
    insert_pool(
        &connection,
        &PoolSpec {
            token0_capacity: Some("999999999"),
            token1_capacity: Some("888888888"),
            ..PoolSpec::default()
        },
        "a",
    );
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();
    let expected = pool_manager::depth::recompute_single_tick_bounds(
        79_228_162_514_264_337_593_543_950_336,
        1_000_000,
        3_000,
        60,
        0,
    );
    let direction0 = snapshot.rows.iter().find(|row| row.direction == 0).unwrap();
    let direction1 = snapshot.rows.iter().find(|row| row.direction == 1).unwrap();

    // Direction 0 spends token1, so it carries the token1 bound; direction
    // 1 spends token0.
    assert_eq!(
        direction0.input_capacity_raw.as_deref(),
        expected.1.map(|value| value.to_string()).as_deref()
    );
    assert_eq!(
        direction1.input_capacity_raw.as_deref(),
        expected.0.map(|value| value.to_string()).as_deref()
    );
    // The drifted stored columns are not what was published.
    assert_ne!(direction0.input_capacity_raw.as_deref(), Some("888888888"));
    assert_ne!(direction1.input_capacity_raw.as_deref(), Some("999999999"));
}

/// T-D-12 / I-10: the in-memory rows and the table rows are the **same
/// set**.
///
/// The closed loop reads the directional pool layer from the in-memory
/// objects, so a row that is in the table but not in memory (or the other
/// way round) is a pool the closed loop cannot see or a pool it would see
/// twice.  The test reads the table back directly and compares the two
/// sets, and it also checks that every base pool appears exactly twice.
#[test]
fn the_in_memory_rows_and_the_table_rows_are_the_same_set() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    insert_pool(
        &connection,
        &PoolSpec {
            liquidity: "100000",
            ..PoolSpec::default()
        },
        "b",
    );
    // A pool that carries no rank still belongs to the layer: "not ranked"
    // is not "not published".
    insert_pool(
        &connection,
        &PoolSpec {
            pool_status: "blocked",
            ..PoolSpec::default()
        },
        "c",
    );
    drop(connection);
    let result =
        materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 10)
            .unwrap();

    // The table is the source of truth, so read it back directly.
    let connection = Connection::open(&fixture.path).unwrap();
    let mut statement = connection
        .prepare(
            "SELECT base_pool_id, direction
             FROM manager_v4_directional_exchange_pools
             WHERE network = ?1 AND chain_id = ?2 AND protocol = 'uniswap_v4'
               AND scope_key = ?3
             ORDER BY base_pool_id, direction",
        )
        .unwrap();
    let table_rows: Vec<(String, i64)> = statement
        .query_map(params![NETWORK, CHAIN_ID, SCOPE_KEY], |row| {
            Ok((row.get(0)?, row.get(1)?))
        })
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    drop(statement);
    drop(connection);

    let mut memory_rows: Vec<(String, i64)> = snapshot
        .rows
        .iter()
        .map(|row| (row.base_pool_id.clone(), row.direction))
        .collect();
    memory_rows.sort();

    // No row is missing and none is extra.
    assert_eq!(memory_rows, table_rows);
    // Every base pool appears exactly twice, once per direction.
    assert_eq!(table_rows.len(), result.base_pool_count * 2);
    assert_eq!(snapshot.rows.len(), result.directional_pool_count);
    assert_eq!(snapshot.status.directional_pool_count, result.directional_pool_count);
}

/// DPO-9 / T-D-13: only an admissible base pool may carry a **rank**.
///
/// The standing convention of the system is that the off-chain arithmetic
/// may only price a **fixed rate** pool without a **hook**.  The pool
/// manager owns that verdict and publishes it in `pool_status`; the
/// directional layer inherits it.
///
/// The layer projects a little more than it ranks: a pool that is not
/// active is still published, so that the coverage gap is visible, but it
/// carries **no rank**.  A rank is the only thing the closed loop selects
/// by, so "no rank" is what keeps an inadmissible pool out of the
/// selection.
///
/// The test therefore pins the invariant that actually protects the closed
/// loop: every ranked row comes from an active, hook-free, non-dynamic-fee
/// base pool.
#[test]
fn only_an_admissible_base_pool_may_carry_a_rank() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    // The admissible pool: active, fixed fee, no hook.
    insert_pool(&connection, &PoolSpec::default(), "a");
    // A dynamic fee pool and a hook pool are not projected at all.
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
    // A pool that is not active is published but never ranked.
    insert_pool(
        &connection,
        &PoolSpec {
            pool_status: "blocked",
            ..PoolSpec::default()
        },
        "d",
    );
    insert_pool(
        &connection,
        &PoolSpec {
            pool_status: "invalid",
            ..PoolSpec::default()
        },
        "e",
    );
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let snapshot =
        read_directional_pool_snapshot(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, None, 20)
            .unwrap();

    let mut ranked = 0usize;
    for row in &snapshot.rows {
        if row.pair_direction_rank.is_none() {
            continue;
        }
        ranked += 1;
        assert_eq!(
            row.pool_status, "active",
            "a ranked row must come from an active base pool"
        );
        assert!(
            hooks_is_absent(&row.hooks),
            "a ranked row must come from a pool without a hook"
        );
        assert_eq!(
            row.fee & DYNAMIC_FEE_FLAG,
            0,
            "a ranked row must come from a pool with a fixed fee"
        );
        assert!(
            row.fee > 0 && row.fee <= 30_000,
            "a ranked row must come from a pool inside the fixed rate \
             candidate window, got fee {}",
            row.fee
        );
    }
    // Only the one admissible pool is ranked, on both of its directions.
    assert_eq!(ranked, 2);
}

/// Whether a pool carries a hook.
///
/// The mirror of the layer's own helper, so the assertion reads the same
/// way the rule does.
fn hooks_is_absent(hooks: &str) -> bool {
    let normalized = hooks.trim().to_ascii_lowercase();
    normalized.is_empty() || normalized == "0x0000000000000000000000000000000000000000"
}

#[test]
fn the_candidate_capacity_is_a_knob_and_not_a_threshold() {
    // The capacity is the one knob of the layer: it decides how many pools
    // of a group are **candidates**, and everything behind that count is a
    // backup.  It is read from the environment, so an operator can widen
    // the candidate list without a rebuild.
    assert_eq!(
        MAX_DIRECTION_CANDIDATE_POOLS_ENV, "FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS",
        "the knob has a stable name"
    );
    assert_eq!(
        DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS, 5,
        "the default capacity is five pools per direction"
    );
    assert!(
        max_direction_candidate_pools() > 0,
        "the capacity is always a positive number of pools"
    );
}

#[test]
fn the_candidate_tier_is_never_wider_than_the_group_it_belongs_to() {
    let capacity = max_direction_candidate_pools();
    for eligible in 0..=16usize {
        let tier_size = candidate_tier_size(eligible);
        assert_eq!(
            tier_size,
            eligible.min(capacity),
            "the tier of a group of {eligible} pools holds {tier_size} pools"
        );
        assert!(
            tier_size <= eligible,
            "the tier is never wider than the group"
        );
    }
}

#[test]
fn the_ranks_split_the_group_into_a_candidate_and_a_backup_tier() {
    // The split is a **layering** and not a truncation: every pool of the
    // group keeps a tier, and the backup tier stays reachable.
    for eligible in 1..=16usize {
        let tier_size = candidate_tier_size(eligible);
        for rank in 1..=eligible as i64 {
            let tier = tier_of_rank(rank, eligible);
            if rank <= tier_size as i64 {
                assert_eq!(
                    tier,
                    DirectionalPoolTier::Candidate,
                    "rank {rank} of a group of {eligible} is a candidate"
                );
            } else {
                assert_eq!(
                    tier,
                    DirectionalPoolTier::Backup,
                    "rank {rank} of a group of {eligible} is a backup"
                );
            }
        }
    }
}

#[test]
fn a_rank_outside_the_group_is_a_backup() {
    // A rank that the group does not hold is never a candidate: the tier
    // of a row is decided by the group it belongs to.
    assert_eq!(
        tier_of_rank(0, 4),
        DirectionalPoolTier::Backup,
        "the rank zero is not a rank"
    );
    assert_eq!(
        tier_of_rank(-1, 4),
        DirectionalPoolTier::Backup,
        "a negative rank is not a rank"
    );
    assert_eq!(
        tier_of_rank(99, 4),
        DirectionalPoolTier::Backup,
        "a rank beyond the group is a backup"
    );
}

// --- the comparison of the published layer against its base pools -------
//
// The directional layer is a projection: one base pool becomes two
// directional rows and every fact about the pool itself is copied.  A row
// that no longer agrees with the pool it was projected from is serving a
// stale fact, and the sibling comparison cannot see it -- both rows can be
// stale in the same way.

#[test]
fn a_fresh_projection_compares_clean_against_its_base_pools() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    let comparison =
        compare_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, 100).unwrap();
    assert_eq!(comparison.base_pool_mismatches, 0);
    assert_eq!(comparison.rows_without_base_pool, 0);
    assert_eq!(comparison.projected_base_pool_count, 1);
    assert_eq!(comparison.directional_pool_count, 2);
    assert_eq!(comparison.direction_mismatches, 0);
    assert!(comparison.base_pool_mismatch_samples.is_empty());
    assert!(
        comparison.ready_for_cutover,
        "a fresh projection is ready: {comparison:#?}"
    );
}

#[test]
fn a_stale_projection_is_detected_against_the_base_pool_row() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    let pool_id = insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    // The base pool changes after the layer was published: the projection
    // is now stale.  The two directional rows still agree with each other,
    // so only the base comparison can see this.
    let connection = Connection::open(&fixture.path).unwrap();
    connection
        .execute(
            "UPDATE v4_exchange_pools_candidate_exchange SET fee = 500
             WHERE network = ?1 AND chain_id = ?2 AND pool_id = ?3",
            params![NETWORK, CHAIN_ID, pool_id],
        )
        .unwrap();
    drop(connection);

    let comparison =
        compare_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, 100).unwrap();
    // Both directions of the pool carry the stale fee.
    assert_eq!(comparison.base_pool_mismatches, 2);
    assert_eq!(comparison.rows_without_base_pool, 0);
    assert_eq!(
        comparison.base_pool_mismatch_samples.len(),
        2,
        "the report names the difference: {comparison:#?}"
    );
    for sample in &comparison.base_pool_mismatch_samples {
        assert_eq!(sample.differing_columns, vec!["fee".to_owned()]);
        assert_eq!(sample.base_pool_id, pool_id);
    }
    assert!(
        !comparison.ready_for_cutover,
        "a stale projection is not ready: {comparison:#?}"
    );
    // The sibling comparison is still clean, which is the whole point.
    assert_eq!(comparison.projection_mismatches, 0);
    assert_eq!(comparison.direction_mismatches, 0);
}

#[test]
fn a_projection_whose_base_pool_is_gone_is_detected() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    let pool_id = insert_pool(&connection, &PoolSpec::default(), "a");
    drop(connection);
    materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, true).unwrap();

    // The base pool leaves the scope universe while the projection stays.
    let connection = Connection::open(&fixture.path).unwrap();
    connection
        .execute(
            "DELETE FROM v4_exchange_pools_candidate_exchange
             WHERE network = ?1 AND chain_id = ?2 AND pool_id = ?3",
            params![NETWORK, CHAIN_ID, pool_id],
        )
        .unwrap();
    drop(connection);

    let comparison =
        compare_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, 100).unwrap();
    assert_eq!(comparison.rows_without_base_pool, 2);
    assert_eq!(comparison.base_pool_mismatches, 0);
    assert!(
        !comparison.ready_for_cutover,
        "an orphaned projection is not ready: {comparison:#?}"
    );
}

#[test]
fn the_chain_entry_materializes_every_enabled_scope_in_scope_key_order() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    // A second enabled scope, whose pool table holds one pool of its own.
    connection
        .execute_batch(
            // The core4 scope table is part of the manager schema, so it is
            // already there; the statement only makes sure the test does
            // not depend on a table the schema does not own.
            "CREATE TABLE IF NOT EXISTS v4_exchange_pools_core4 AS
             SELECT * FROM v4_exchange_pools_candidate_exchange WHERE 0",
        )
        .unwrap();
    // The second scope owns a pool of its own, so the chain entry has
    // something to materialize in it.
    insert_pool_into(
        &connection,
        "v4_exchange_pools_core4",
        &PoolSpec::default(),
        "b",
    );
    connection
        .execute(
            r#"INSERT INTO pool_scope_config (
                 network, chain_id, scope_key, pool_table, library_keys_json,
                 library_versions_json, library_checksums_json, filter_mode,
                 excluded_token_key_ids_json, enabled, materialized_checksum,
                 last_rebuilt_at, created_at, updated_at
             ) VALUES (?1, ?2, 'core4', 'v4_exchange_pools_core4', '[]',
                       '{"core": 7}', '{"core": "sha256:core"}', 'both_end',
                       '[]', 1, 'sha256:scope-fixture',
                       '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z',
                       '2026-11-04T00:00:00Z')"#,
            params![NETWORK, CHAIN_ID],
        )
        .unwrap();
    // A third scope that is switched **off**: it must not be materialized.
    // It owns a table and a pool of its own, so the skip is caused by
    // "enabled = 0" and not by a missing table.
    connection
        .execute_batch(
            // A scope table of the same shape as the ones the schema owns,
            // so the disabled scope is skipped because it is switched off
            // and not because its table is missing.
            "CREATE TABLE IF NOT EXISTS v4_exchange_pools_disabled_scope AS
             SELECT * FROM v4_exchange_pools_candidate_exchange WHERE 0",
        )
        .unwrap();
    insert_pool_into(
        &connection,
        "v4_exchange_pools_disabled_scope",
        &PoolSpec::default(),
        "c",
    );
    connection
        .execute(
            r#"INSERT INTO pool_scope_config (
                 network, chain_id, scope_key, pool_table, library_keys_json,
                 library_versions_json, library_checksums_json, filter_mode,
                 excluded_token_key_ids_json, enabled, materialized_checksum,
                 last_rebuilt_at, created_at, updated_at
             ) VALUES (?1, ?2, 'disabled_scope', 'v4_exchange_pools_disabled_scope', '[]',
                       '{"core": 7}', '{"core": "sha256:core"}', 'both_end',
                       '[]', 0, 'sha256:scope-fixture',
                       '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z',
                       '2026-11-04T00:00:00Z')"#,
            params![NETWORK, CHAIN_ID],
        )
        .unwrap();
    drop(connection);

    let dry_run = materialize_directional_pools_for_chain(&fixture.path, NETWORK, CHAIN_ID, false)
        .unwrap();
    assert_eq!(
        dry_run.scope_keys,
        vec!["candidate_exchange".to_owned(), "core4".to_owned()],
        "the scopes are enumerated in scope key order and the disabled scope is not listed"
    );
    assert_eq!(dry_run.scopes.len(), 2);
    assert!(dry_run.failed_scopes.is_empty());
    assert!(dry_run.dry_run);
    assert!(!dry_run.apply);

    let applied = materialize_directional_pools_for_chain(&fixture.path, NETWORK, CHAIN_ID, true)
        .unwrap();
    assert_eq!(applied.scopes.len(), 2);
    assert!(applied.failed_scopes.is_empty());
    let applied_scope_keys: Vec<&str> = applied
        .scopes
        .iter()
        .map(|scope| scope.scope_key.as_str())
        .collect();
    assert_eq!(
        applied_scope_keys,
        vec!["candidate_exchange", "core4"],
        "the per-scope results follow the enumerated order"
    );

    // The per-scope entry and the chain entry must publish the same
    // numbers for the same scope: the chain entry **is** the per-scope
    // call, one scope after another.
    let single = materialize_directional_pools(&fixture.path, NETWORK, CHAIN_ID, SCOPE_KEY, false)
        .unwrap();
    let chain_scope = applied
        .scopes
        .iter()
        .find(|scope| scope.scope_key == SCOPE_KEY)
        .unwrap();
    assert_eq!(chain_scope.directional_pool_count, single.directional_pool_count);
    assert_eq!(chain_scope.direction0_count, single.direction0_count);
    assert_eq!(chain_scope.direction1_count, single.direction1_count);
    assert_eq!(chain_scope.materialized_checksum, single.materialized_checksum);

    // The disabled scope published no rows.
    let connection = Connection::open(&fixture.path).unwrap();
    let disabled_rows: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_v4_directional_exchange_pools
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = 'disabled_scope'",
            params![NETWORK, CHAIN_ID],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(disabled_rows, 0);

    // The enabled second scope published its own pool: the chain entry
    // materialized a scope, it did not only enumerate scope names.
    let core4_rows: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_v4_directional_exchange_pools
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = 'core4'",
            params![NETWORK, CHAIN_ID],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(
        core4_rows, 2,
        "one base pool projects onto exactly two directional rows"
    );

    // A disabled scope publishes nothing at all -- not even a row that
    // belongs to another scope.
    let disabled_pool_id = format!("0x{}", "c".repeat(64));
    let leaked_rows: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_v4_directional_exchange_pools
             WHERE network = ?1 AND chain_id = ?2 AND base_pool_id = ?3",
            params![NETWORK, CHAIN_ID, disabled_pool_id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(leaked_rows, 0);
}

#[test]
fn a_scope_that_fails_is_reported_and_does_not_hide_the_other_scope() {
    let fixture = fixture();
    let connection = Connection::open(&fixture.path).unwrap();
    insert_pool(&connection, &PoolSpec::default(), "a");
    // An **enabled** scope whose pool table is not there.  The chain
    // entry has to name the failure instead of swallowing it, and it has
    // to keep going: the scope that can be materialized is published.
    connection
        .execute(
            r#"INSERT INTO pool_scope_config (
                 network, chain_id, scope_key, pool_table, library_keys_json,
                 library_versions_json, library_checksums_json, filter_mode,
                 excluded_token_key_ids_json, enabled, materialized_checksum,
                 last_rebuilt_at, created_at, updated_at
             ) VALUES (?1, ?2, 'broken_scope', 'v4_exchange_pools_broken_scope', '[]',
                       '{"core": 7}', '{"core": "sha256:core"}', 'both_end',
                       '[]', 1, 'sha256:scope-fixture',
                       '2026-11-04T00:00:00Z', '2026-11-04T00:00:00Z',
                       '2026-11-04T00:00:00Z')"#,
            params![NETWORK, CHAIN_ID],
        )
        .unwrap();
    drop(connection);

    let result = materialize_directional_pools_for_chain(&fixture.path, NETWORK, CHAIN_ID, true)
        .unwrap();
    assert_eq!(
        result.scope_keys,
        vec!["broken_scope".to_owned(), "candidate_exchange".to_owned()],
        "the scopes are enumerated in scope key order, the failing one included"
    );
    assert_eq!(result.failed_scopes.len(), 1, "the failed scope is reported");
    assert_eq!(result.failed_scopes[0].scope_key, "broken_scope");
    assert!(
        !result.failed_scopes[0].error.is_empty(),
        "a failure is reported with its reason, not as a bare name"
    );
    // The scope that could be materialized is published: the failure of
    // one scope does not hide the work of another.
    assert_eq!(result.scopes.len(), 1);
    assert_eq!(result.scopes[0].scope_key, SCOPE_KEY);

    // The failed scope published nothing.
    let connection = Connection::open(&fixture.path).unwrap();
    let broken_rows: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM manager_v4_directional_exchange_pools
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = 'broken_scope'",
            params![NETWORK, CHAIN_ID],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(broken_rows, 0);
}
