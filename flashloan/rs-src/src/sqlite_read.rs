use rusqlite::{Connection, OpenFlags, Row};
use serde_json::Value;

pub fn open_readonly(database_path: &str) -> Result<Connection, String> {
    Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| format!("could not open SQLite read-only {database_path}: {error}"))
}

fn row_to_value(row: &Row<'_>) -> rusqlite::Result<Value> {
    let mut map = serde_json::Map::new();
    let statement = row.as_ref();
    for index in 0..statement.column_count() {
        let name = statement.column_name(index)?.to_string();
        let value = match row.get_ref(index)? {
            rusqlite::types::ValueRef::Null => Value::Null,
            rusqlite::types::ValueRef::Integer(value) => Value::from(value),
            rusqlite::types::ValueRef::Real(value) => serde_json::Number::from_f64(value).map(Value::Number).unwrap_or(Value::Null),
            rusqlite::types::ValueRef::Text(value) => Value::String(String::from_utf8_lossy(value).into_owned()),
            rusqlite::types::ValueRef::Blob(value) => {
                Value::String(format!("0x{}", hex::encode(value)))
            }
        };
        map.insert(name, value);
    }
    Ok(Value::Object(map))
}

fn rows(connection: &Connection, sql: &str, params: &[&dyn rusqlite::ToSql]) -> Result<Vec<Value>, String> {
    let mut statement = connection
        .prepare(sql)
        .map_err(|error| format!("SQLite prepare failed: {error}"))?;
    let rows = statement
        .query_map(params, row_to_value)
        .map_err(|error| format!("SQLite query failed: {error}"))?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|error| format!("SQLite row iteration failed: {error}"))
}

pub fn load_v4_exchange_pools(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT network, chain_id, protocol, pool_id, pool_identity_mode, pool_manager, \
         pool_key_json, token0, token1, fee, fee_label, dynamic_fee, special_fee, \
         tick_spacing, hooks, address_pair, created_at, updated_at \
         FROM v4_exchange_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string()],
    )
}

pub fn load_v4_pool_liquidity(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT network, chain_id, protocol, pool_id, has_liquidity, liquidity, \
         liquidity_index, estimated_tvl_usd, balances_json, depth_status, depth_band_json, \
         depth_score_usd, sqrt_price_x96, price, tick, liquidity_read_at, \
         liquidity_block_number, recent_swap_active, observed_at, refreshed_at \
         FROM v4_exchange_pool_liquidity_snapshots \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string()],
    )
}

pub fn load_v4_exchange_pool_liquidity_for_pool(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
    pool_id: &str,
) -> Result<Option<Value>, String> {
    let results = rows(
        connection,
        "SELECT * FROM v4_exchange_pool_liquidity_snapshots \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND pool_id = ?4 \
         LIMIT 1",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &pool_id.to_lowercase()],
    )?;
    let first = results.into_iter().next();
    Ok(first)
}

pub fn load_dex_candidate_pools(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
    anchor_symbol: &str,
) -> Result<Value, String> {
    let anchor_tokens = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, \
         token_address, pool_count, has_yc_optimal_pool, has_zc_optimal_pool, \
         yc_optimal_pool_id, zc_optimal_pool_id, yc_zc_same_set, created_at, updated_at \
         FROM dex_candidate_anchor_tokens \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY token_address",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    let pairs = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, anchor_address, \
         counter_address, address_pair, pool_count, fixed_fee_liquid_pool_count, \
         priced_pool_count, yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json, \
         price_outlier_filter, created_at, updated_at \
         FROM dex_candidate_pool_pairs \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY counter_address",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    let pools = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, \
         pool_id, pool_key_json, pool_manager, token0, token1, fee, fee_label, dynamic_fee, \
         special_fee, tick_spacing, hooks, sqrt_price_x96, tick, has_liquidity, liquidity, \
         liquidity_index, estimated_tvl_usd, balances_json, depth_status, depth_band_json, \
         usable_for_observation, is_first_hop_recommended, is_third_hop_recommended, \
         hop_recommendation_json, optimal_roles_json, liquidity_read_at, \
         liquidity_block_number, created_at, updated_at \
         FROM dex_candidate_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY address_pair, pool_id",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    let recommendations = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, \
         hop, rank, pool_id, depth_score, price, fee, selection_basis, created_at, updated_at \
         FROM dex_candidate_pool_recommendations \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY counter_address, hop, rank",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    Ok(serde_json::json!({
        "anchorTokens": anchor_tokens,
        "pairs": pairs,
        "pools": pools,
        "recommendations": recommendations,
    }))
}

pub fn load_market_observation(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
    anchor_symbol: &str,
) -> Result<Value, String> {
    let pairs = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key, \
         token_x_address, token_y_address, address_pair, has_direct_swap_pool, \
         direct_swap_pool_count, pool_ids_json, observation_empty_reason, created_at, updated_at \
         FROM market_observation_yz_pairs \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY direction_key",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    let pools = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id, \
         pool_identity_mode, pool_manager, pool_key_json, token0, token1, fee, fee_label, \
         dynamic_fee, special_fee, tick_spacing, hooks, address_pair, created_at, updated_at \
         FROM market_observation_intermediate_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY pool_id",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    let liquidity = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id, \
         has_liquidity, liquidity, liquidity_index, estimated_tvl_usd, balances_json, \
         depth_status, depth_band_json, sqrt_price_x96, tick, liquidity_read_at, \
         liquidity_block_number, recent_swap_active, observed_at, refreshed_at \
         FROM market_observation_liquidity_snapshots \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY pool_id",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    Ok(serde_json::json!({
        "pairs": pairs,
        "pools": pools,
        "liquidity": liquidity,
    }))
}

pub fn load_v4_closed_loop(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
    market_id: &str,
) -> Result<Value, String> {
    let loops = rows(
        connection,
        "SELECT network, chain_id, protocol, market_id, loop_key, anchor_address, \
         anchor_symbol, path_text, path_addresses_json, path_symbols_json, pool_path_key, \
         pool_ids_json, hop_candidate_counts_json, hop_candidate_combination_count, \
         recommendation_basis, recommended_first_priority, status, source, created_at, updated_at \
         FROM v4_closed_loops \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND market_id = ?4 \
         ORDER BY loop_key",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &market_id.to_string()],
    )?;
    let hops = rows(
        connection,
        "SELECT network, chain_id, protocol, market_id, loop_key, hop_index, hop_name, \
         input_address, output_address, pool_id, base_pool_id, pool_selection_basis, \
         optimization_id, temporary_override, created_at, updated_at \
         FROM v4_closed_loop_hops \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND market_id = ?4 \
         ORDER BY loop_key, hop_index",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &market_id.to_string()],
    )?;
    let optimizations = rows(
        connection,
        "SELECT network, chain_id, protocol, market_id, loop_key, hop_index, \
         optimization_id, original_pool_id, optimized_pool_id, optimization_basis, \
         expected_price_improvement, status, created_at, expires_at, updated_at \
         FROM v4_closed_loop_optimizations \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND market_id = ?4 \
         ORDER BY loop_key, hop_index, optimization_id",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &market_id.to_string()],
    )?;
    Ok(serde_json::json!({
        "loops": loops,
        "hops": hops,
        "optimizations": optimizations,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;

    fn temporary_database() -> (tempfile::TempDir, String) {
        let dir = tempfile::tempdir().expect("temporary dir");
        let path = dir.path().join("readonly-test.sqlite3");
        let path_text = path.to_string_lossy().into_owned();
        let connection = Connection::open(&path).expect("open test database");
        connection
            .execute_batch(
                "
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_identity_mode TEXT NOT NULL DEFAULT 'v4_pool_id',
                    pool_manager TEXT,
                    pool_key_json TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    fee_label TEXT,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    address_pair TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                INSERT INTO v4_exchange_pools VALUES (
                    'robinhood', 4663, 'uniswap_v4',
                    '0x1111111111111111111111111111111111111111111111111111111111111111',
                    'v4_pool_id', '0x8366a39cc670b4001a1121b8f6a443a643e40951',
                    '{}', '0x0000000000000000000000000000000000000000',
                    '0x5fc5360d0400a0fd4f2af552add042d716f1d168',
                    3000, 3000, 0, 0, 60,
                    '0x0000000000000000000000000000000000000000',
                    '0x0000000000000000000000000000000000000000:0x5fc5360d0400a0fd4f2af552add042d716f1d168',
                    '2026-10-09T00:00:00.000Z', '2026-10-09T00:00:00.000Z'
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
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
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                ",
            )
            .expect("create test tables");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    "robinhood", 4663_i64, "uniswap_v4",
                    "0x1111111111111111111111111111111111111111111111111111111111111111",
                    1, "1000000000000000000", 1_000_000.0_f64, 1_000.0_f64,
                    "{}", "liquid", "{}", 1_000.0_f64,
                    "79228162514264337593543950336", 1.0_f64, 0,
                    "2026-10-09T00:00:00.000Z", 4663_i64, 1,
                    "2026-10-09T00:00:00.000Z", "2026-10-09T00:00:00.000Z"
                ],
            )
            .expect("insert liquidity");
        (dir, path_text)
    }

    #[test]
    fn sqlite_read_only_loads_exchange_pools() {
        let (_dir, path) = temporary_database();
        let connection = open_readonly(&path).expect("read-only open");
        let pools = load_v4_exchange_pools(&connection, "robinhood", 4663, "uniswap_v4").expect("load pools");
        assert_eq!(pools.len(), 1);
        assert_eq!(
            pools[0]["pool_id"].as_str().unwrap(),
            "0x1111111111111111111111111111111111111111111111111111111111111111"
        );
        assert_eq!(pools[0]["fee"].as_i64().unwrap(), 3000);
        let liquidity = load_v4_pool_liquidity(&connection, "robinhood", 4663, "uniswap_v4").expect("load liquidity");
        assert_eq!(liquidity.len(), 1);
        assert_eq!(liquidity[0]["has_liquidity"].as_i64().unwrap(), 1);
    }

    #[test]
    fn sqlite_read_only_does_not_allow_writes() {
        let (_dir, path) = temporary_database();
        let connection = open_readonly(&path).expect("read-only open");
        let result = connection.execute(
            "INSERT INTO v4_exchange_pools VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params!["robinhood", 4663_i64, "uniswap_v4", "0x2", "v4_pool_id", "", "{}", "", "", 0, 0, 0, 0, 0, "", "", "2026-10-09", "2026-10-09"],
        );
        assert!(result.is_err());
    }
}
