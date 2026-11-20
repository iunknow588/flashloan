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
            rusqlite::types::ValueRef::Real(value) => serde_json::Number::from_f64(value)
                .map(Value::Number)
                .unwrap_or(Value::Null),
            rusqlite::types::ValueRef::Text(value) => {
                Value::String(String::from_utf8_lossy(value).into_owned())
            }
            rusqlite::types::ValueRef::Blob(value) => {
                Value::String(format!("0x{}", hex::encode(value)))
            }
        };
        map.insert(name, value);
    }
    Ok(Value::Object(map))
}

fn rows(
    connection: &Connection,
    sql: &str,
    params: &[&dyn rusqlite::ToSql],
) -> Result<Vec<Value>, String> {
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
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
        ],
    )
}

pub fn load_v4_exchange_pool_registry_rows(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT pool_id, token0, token1, pool_manager \
         FROM v4_exchange_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
        ],
    )
}

#[cfg(test)]
pub fn load_chain_tokens(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT network, chain_id, token_address, token_symbol, token_name, \
         token_decimals, token_kind, is_native, is_wrapped_native, \
         is_stable, anchor_symbol, token_metadata_json, source_json, \
         first_seen_at, updated_at \
         FROM chain_tokens \
         WHERE network = ?1 AND chain_id = ?2 \
         ORDER BY token_address",
        &[&network.to_string(), &(chain_id as i64)],
    )
}

/// Whether the Token Manager authority table is present in this database.
///
/// The Token Manager owns the token truth of every chain. Its
/// `token_library_catalog` view is the only place a token's symbol and
/// decimals are decided, so the kernel reads its tokens from there. A
/// database that has not been migrated yet simply has no token truth to
/// read, and the kernel reports that instead of guessing.
fn token_library_catalog_exists(connection: &Connection) -> bool {
    connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master \
             WHERE type IN ('table','view') AND name='token_library_catalog'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0)
        > 0
}

/// The tokens of one chain, as the Token Manager publishes them.
///
/// Every admitted token of every library is returned. The kernel needs the
/// full token set of the chain, because a route may borrow any token the
/// Token Manager admitted, not only the core four. A token that the Token
/// Manager blocked or left pending is deliberately left out: an
/// unadmitted token is not a tradeable token of the system.
fn load_token_manager_rows(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT token_address, token_symbol, token_name, token_decimals, \
         token_kind, is_native, is_wrapped_native, is_stable, \
         admission_status \
         FROM token_library_catalog \
         WHERE network = ?1 AND chain_id = ?2 \
           AND admission_status = 'admitted' \
         ORDER BY token_address",
        &[&network.to_string(), &(chain_id as i64)],
    )
}

pub fn load_chain_token_registry_rows(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<Value>, String> {
    if token_library_catalog_exists(connection) {
        return load_token_manager_rows(connection, network, chain_id);
    }
    rows(
        connection,
        "SELECT token_address \
         FROM chain_tokens \
         WHERE network = ?1 AND chain_id = ?2 \
         ORDER BY token_address",
        &[&network.to_string(), &(chain_id as i64)],
    )
}

/// Read the decimals of every token of one chain.
///
/// The decimals are part of the **initialization snapshot**: the runtime
/// reads them once, when it builds its in-memory object, and every later
/// processing step takes them from that object. A raw profit is only a
/// number until the decimals of the token it is denominated in are known,
/// so the kernel needs them without asking the database again.
///
/// The read is chain-scoped: the network and the chain id both take part
/// in the query, so the decimals of one chain can never be applied to a
/// route of another chain.
pub fn load_chain_token_decimals_rows(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<Value>, String> {
    if token_library_catalog_exists(connection) {
        return load_token_manager_rows(connection, network, chain_id);
    }
    rows(
        connection,
        "SELECT token_address, token_symbol, token_decimals \
         FROM chain_tokens \
         WHERE network = ?1 AND chain_id = ?2 \
         ORDER BY token_address",
        &[&network.to_string(), &(chain_id as i64)],
    )
}

pub fn load_v4_exchange_pool_rows_for_view(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT network, chain_id, protocol, pool_id, pool_manager, token0, token1, \
         fee, hooks, tick_spacing \
         FROM v4_exchange_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
        ],
    )
}

pub fn load_v4_exchange_pool_pair_counts(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT token0, token1, \
         COUNT(DISTINCT CASE WHEN COALESCE(pool_id, '') <> '' THEN pool_id \
             ELSE token0 || ':' || token1 || ':' || COALESCE(CAST(fee AS TEXT), '') END) AS pool_count \
         FROM v4_exchange_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         GROUP BY token0, token1 \
         ORDER BY token0, token1",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string()],
    )
}

pub fn load_v4_exchange_pool_manager(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Option<Value>, String> {
    let results = rows(
        connection,
        "SELECT pool_manager \
         FROM v4_exchange_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id LIMIT 1",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
        ],
    )?;
    Ok(results.into_iter().next())
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
         liquidity_block_number, recent_swap_active, observed_at, refreshed_at, \
         evidence_source \
         FROM v4_exchange_pool_liquidity_snapshots \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
        ],
    )
}

pub fn load_v4_pool_liquidity_registry_rows(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT pool_id, has_liquidity \
         FROM v4_exchange_pool_liquidity_snapshots \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
        ],
    )
}

pub fn load_v4_pool_liquidity_rows_for_view(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT pool_id, has_liquidity, liquidity, liquidity_index, \
         estimated_tvl_usd, depth_status, sqrt_price_x96, tick, \
         liquidity_read_at, liquidity_block_number, recent_swap_active, \
         observed_at, refreshed_at, evidence_source \
         FROM v4_exchange_pool_liquidity_snapshots \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 \
         ORDER BY pool_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
        ],
    )
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
         yc_optimal_pool_id, zc_optimal_pool_id, yc_backup_pool_ids_json, \
         zc_backup_pool_ids_json, yc_zc_same_set, created_at, updated_at \
         FROM dex_candidate_anchor_tokens \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY token_address",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &anchor_symbol.to_string(),
        ],
    )?;
    let pairs = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, anchor_address, \
         counter_address, address_pair, direction_key, pool_count, fixed_fee_liquid_pool_count, \
         priced_pool_count, yc_optimal_pool_id, zc_optimal_pool_id, \
         yc_backup_pool_ids_json, zc_backup_pool_ids_json, price_reference_json, \
         price_outlier_filter, created_at, updated_at \
         FROM dex_candidate_pool_pairs \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY counter_address",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &anchor_symbol.to_string(),
        ],
    )?;
    let pools = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, \
         direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0, token1, fee, fee_label, dynamic_fee, \
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
         direction_key, hop, rank, pool_id, depth_score, price, fee, selection_basis, created_at, updated_at \
         FROM dex_candidate_pool_recommendations \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY counter_address, hop, rank",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    let abnormal_anchor_tokens = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, \
         token_address, pool_count, has_yc_optimal_pool, has_zc_optimal_pool, \
         yc_optimal_pool_id, zc_optimal_pool_id, yc_zc_same_set, created_at, updated_at \
         FROM dex_candidate_abnormal_anchor_tokens \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY token_address",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &anchor_symbol.to_string(),
        ],
    )?;
    let abnormal_pairs = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, anchor_address, \
         counter_address, address_pair, direction_key, pool_count, fixed_fee_liquid_pool_count, \
         priced_pool_count, yc_optimal_pool_id, zc_optimal_pool_id, \
         price_reference_json, price_outlier_filter, created_at, updated_at \
         FROM dex_candidate_abnormal_pool_pairs \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY counter_address",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &anchor_symbol.to_string(),
        ],
    )?;
    let abnormal_pools = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, \
         direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0, token1, fee, fee_label, dynamic_fee, \
         special_fee, tick_spacing, hooks, sqrt_price_x96, tick, has_liquidity, liquidity, \
         liquidity_index, estimated_tvl_usd, balances_json, depth_status, depth_band_json, \
         usable_for_observation, is_first_hop_recommended, is_third_hop_recommended, \
         hop_recommendation_json, optimal_roles_json, liquidity_read_at, \
         liquidity_block_number, created_at, updated_at \
         FROM dex_candidate_abnormal_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY address_pair, pool_id",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    Ok(serde_json::json!({
        "anchorTokens": anchor_tokens,
        "pairs": pairs,
        "pools": pools,
        "recommendations": recommendations,
        "abnormalAnchorTokens": abnormal_anchor_tokens,
        "abnormalPairs": abnormal_pairs,
        "abnormalPools": abnormal_pools,
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
         direct_swap_pool_count, pool_ids_json, optimal_pool_id, observation_empty_reason, created_at, updated_at \
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
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &anchor_symbol.to_string(),
        ],
    )?;
    let liquidity = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id, \
         has_liquidity, liquidity, liquidity_index, estimated_tvl_usd, balances_json, \
         depth_status, depth_band_json, sqrt_price_x96, tick, liquidity_read_at, \
         liquidity_block_number, recent_swap_active, observed_at, refreshed_at, \
         evidence_source \
         FROM market_observation_liquidity_snapshots \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY pool_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &anchor_symbol.to_string(),
        ],
    )?;
    let abnormal_pairs = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key, \
         token_x_address, token_y_address, address_pair, has_non_normal_swap_pool, \
         non_normal_swap_pool_count, pool_ids_json, optimal_pool_id, observation_empty_reason, created_at, updated_at \
         FROM market_observation_abnormal_yz_pairs \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY direction_key",
        &[&network.to_string(), &(chain_id as i64), &protocol.to_string(), &anchor_symbol.to_string()],
    )?;
    let abnormal_pools = rows(
        connection,
        "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id, \
         pool_identity_mode, pool_manager, pool_key_json, token0, token1, fee, fee_label, \
         dynamic_fee, special_fee, tick_spacing, hooks, address_pair, created_at, updated_at \
         FROM market_observation_abnormal_intermediate_pools \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND anchor_symbol = ?4 \
         ORDER BY pool_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &anchor_symbol.to_string(),
        ],
    )?;
    Ok(serde_json::json!({
        "pairs": pairs,
        "pools": pools,
        "liquidity": liquidity,
        "abnormalPairs": abnormal_pairs,
        "abnormalPools": abnormal_pools,
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
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &market_id.to_string(),
        ],
    )?;
    let hops = rows(
        connection,
        "SELECT network, chain_id, protocol, market_id, loop_key, hop_index, hop_name, \
         input_address, output_address, pool_id, base_pool_id, pool_selection_basis, \
         optimization_id, temporary_override, created_at, updated_at \
         FROM v4_closed_loop_hops \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND market_id = ?4 \
         ORDER BY loop_key, hop_index",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &market_id.to_string(),
        ],
    )?;
    let optimizations = rows(
        connection,
        "SELECT network, chain_id, protocol, market_id, loop_key, hop_index, \
         optimization_id, original_pool_id, optimized_pool_id, optimization_basis, \
         expected_price_improvement, status, created_at, expires_at, updated_at \
         FROM v4_closed_loop_optimizations \
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND market_id = ?4 \
         ORDER BY loop_key, hop_index, optimization_id",
        &[
            &network.to_string(),
            &(chain_id as i64),
            &protocol.to_string(),
            &market_id.to_string(),
        ],
    )?;
    Ok(serde_json::json!({
        "loops": loops,
        "hops": hops,
        "optimizations": optimizations,
    }))
}

/// Read the closed-loop catalog the Manager published for one chain.
///
/// The Manager owns the closed-loop library, so its rows carry the
/// identity of every loop and the folded single-tick capacity that bounds
/// it. The runtime reads them once, into its in-memory object, and every
/// later processing step works on that object.
///
/// The projection carries `path_addresses_json` because the catalog is
/// keyed by the **ordered address path** of the loop. Without that
/// column the catalog has no way to tell which loop a route belongs to,
/// every row falls out of the identity, and the closed-loop capacity
/// never reaches the execution path.
pub fn load_manager_closed_loop_rows(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<Value>, String> {
    rows(
        connection,
        "SELECT loop_key, path_addresses_json, path_symbols_json, pool_ids_json, \
         single_tick_capacity_anchor_raw, single_tick_capacity_binding_pool_id, \
         borrow_token_usdg_value_raw, borrow_token_decimals, \
         cross_tick_review_threshold_usdg_raw, cross_tick_capacity_anchor_raw \
         FROM manager_v4_closed_loops \
         WHERE network = ?1 AND chain_id = ?2 \
         ORDER BY loop_key",
        &[&network.to_string(), &(chain_id as i64)],
    )
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
                    evidence_source TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE chain_tokens (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    token_address TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    token_name TEXT,
                    token_decimals INTEGER,
                    token_kind TEXT NOT NULL DEFAULT 'erc20',
                    is_native INTEGER NOT NULL DEFAULT 0,
                    is_wrapped_native INTEGER NOT NULL DEFAULT 0,
                    is_stable INTEGER NOT NULL DEFAULT 0,
                    anchor_symbol TEXT,
                    token_metadata_json TEXT,
                    source_json TEXT,
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, token_address)
                );
                ",
            )
            .expect("create test tables");
        connection
            .execute(
                "INSERT INTO chain_tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    "robinhood",
                    4663_i64,
                    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
                    "TEST",
                    "Test Token",
                    6_i64,
                    "erc20",
                    0_i64,
                    0_i64,
                    0_i64,
                    "TST",
                    "{}",
                    "{}",
                    "2026-10-09T00:00:00.000Z",
                    "2026-10-09T00:00:00.000Z"
                ],
            )
            .expect("insert chain token");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    "robinhood", 4663_i64, "uniswap_v4",
                    "0x1111111111111111111111111111111111111111111111111111111111111111",
                    1, "1000000000000000000", 1_000_000.0_f64, 1_000.0_f64,
                    "{}", "liquid", "{}", 1_000.0_f64,
                    "79228162514264337593543950336", 1.0_f64, 0,
                    "2026-10-09T00:00:00.000Z", 4663_i64, 1,
                    "2026-10-09T00:00:00.000Z", "2026-10-09T00:00:00.000Z",
                    "pool_manager_extsload_live_state_batch"
                ],
            )
            .expect("insert liquidity");
        (dir, path_text)
    }

    #[test]
    fn sqlite_read_only_loads_exchange_pools() {
        let (_dir, path) = temporary_database();
        let connection = open_readonly(&path).expect("read-only open");
        let pools = load_v4_exchange_pools(&connection, "robinhood", 4663, "uniswap_v4")
            .expect("load pools");
        assert_eq!(pools.len(), 1);
        assert_eq!(
            pools[0]["pool_id"].as_str().unwrap(),
            "0x1111111111111111111111111111111111111111111111111111111111111111"
        );
        assert_eq!(pools[0]["fee"].as_i64().unwrap(), 3000);
        let liquidity = load_v4_pool_liquidity(&connection, "robinhood", 4663, "uniswap_v4")
            .expect("load liquidity");
        assert_eq!(liquidity.len(), 1);
        assert_eq!(liquidity[0]["has_liquidity"].as_i64().unwrap(), 1);
        let tokens = load_chain_tokens(&connection, "robinhood", 4663).expect("load chain tokens");
        assert_eq!(tokens.len(), 1);
        assert_eq!(
            tokens[0]["token_address"].as_str().unwrap(),
            "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
        );
        assert_eq!(tokens[0]["token_decimals"].as_i64().unwrap(), 6);
    }

    /// The closed-loop catalog is keyed by the ordered address path of the
    /// loop, so the Manager rows must carry `path_addresses_json`. When the
    /// projection dropped that column, every row fell out of the identity,
    /// the catalog stayed empty, and the published single-tick capacity
    /// never reached the execution path.
    #[test]
    fn the_closed_loop_rows_carry_the_address_path_the_catalog_is_keyed_by() {
        let dir = tempfile::tempdir().expect("temporary dir");
        let path = dir.path().join("closed-loop-rows.sqlite3");
        let connection = Connection::open(&path).expect("open test database");
        connection
            .execute_batch(
                "
                CREATE TABLE manager_v4_closed_loops (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    loop_key TEXT NOT NULL,
                    path_addresses_json TEXT,
                    path_symbols_json TEXT,
                    pool_ids_json TEXT,
                    single_tick_capacity_anchor_raw TEXT,
                    single_tick_capacity_binding_pool_id TEXT,
                    borrow_token_usdg_value_raw TEXT,
                    borrow_token_decimals INTEGER,
                    cross_tick_review_threshold_usdg_raw TEXT,
                    cross_tick_capacity_anchor_raw TEXT
                );
                INSERT INTO manager_v4_closed_loops VALUES (
                    'robinhood', 4663,
                    '0xanchor:0xmid:0xend',
                    '[\"0xanchor\",\"0xmid\",\"0xend\",\"0xanchor\"]',
                    '[\"ETH\",\"WETH\",\"USDG\",\"ETH\"]',
                    '[\"pool-a\",\"pool-b\",\"pool-c\"]',
                    '10735155545429035', 'pool-b',
                    '1000000', 6,
                    '500', '999'
                );
                ",
            )
            .expect("create closed-loop table");
        let rows = load_manager_closed_loop_rows(&connection, "robinhood", 4663)
            .expect("load closed-loop rows");
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0]["path_addresses_json"].as_str().unwrap(),
            "[\"0xanchor\",\"0xmid\",\"0xend\",\"0xanchor\"]"
        );

        // The catalog must be able to resolve this row. Without the
        // address path the identity is dropped and the capacity is lost.
        let catalog =
            crate::closed_loop_identity::catalog_from_rows(&rows);
        let addresses = vec![
            "0xanchor".to_string(),
            "0xmid".to_string(),
            "0xend".to_string(),
            "0xanchor".to_string(),
        ];
        let identity = catalog
            .identity_for(&addresses)
            .expect("the published loop must resolve");
        assert_eq!(identity.loop_key, "0xanchor:0xmid:0xend");
        assert_eq!(
            identity.single_tick_capacity_anchor_raw,
            Some(10_735_155_545_429_035)
        );
        assert_eq!(identity.pool_ids.len(), 3);
    }

    #[test]
    fn sqlite_read_keeps_unknown_liquidity_as_null() {
        let dir = tempfile::tempdir().expect("temporary dir");
        let path = dir.path().join("unknown-liquidity.sqlite3");
        let path_text = path.to_string_lossy().into_owned();
        let connection = Connection::open(&path).expect("open test database");
        connection
            .execute_batch(
                "
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
                    evidence_source TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                ",
            )
            .expect("create liquidity table");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    "robinhood", 4663_i64, "uniswap_v4",
                    "0x2222222222222222222222222222222222222222222222222222222222222222",
                    Option::<i64>::None, "", Option::<f64>::None, Option::<f64>::None,
                    "{}", "", "{}", Option::<f64>::None,
                    "", Option::<f64>::None, Option::<i64>::None,
                    Option::<String>::None, Option::<i64>::None,
                    Option::<i64>::None,
                    "2026-10-09T00:00:00.000Z", "2026-10-09T00:00:00.000Z",
                    Option::<String>::None
                ],
            )
            .expect("insert unknown liquidity row");
        drop(connection);

        let connection = open_readonly(&path_text).expect("open read-only");
        let liquidity = load_v4_pool_liquidity(&connection, "robinhood", 4663, "uniswap_v4")
            .expect("load liquidity");
        assert_eq!(liquidity.len(), 1);
        assert!(liquidity[0]["has_liquidity"].is_null());
        assert!(liquidity[0]["recent_swap_active"].is_null());
        assert!(liquidity[0]["liquidity_read_at"].is_null());
    }

    #[test]
    fn token_decimals_come_from_the_token_manager_authority() {
        let dir = tempfile::tempdir().expect("temporary dir");
        let path = dir.path().join("token-authority.sqlite3");
        let path_text = path.to_string_lossy().into_owned();
        let connection = Connection::open(&path).expect("open test database");
        connection
            .execute_batch(
                "
                CREATE TABLE chain_tokens (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    token_address TEXT NOT NULL,
                    token_symbol TEXT,
                    token_name TEXT,
                    token_decimals INTEGER,
                    token_kind TEXT,
                    is_native INTEGER,
                    is_wrapped_native INTEGER,
                    is_stable INTEGER,
                    anchor_symbol TEXT,
                    token_metadata_json TEXT,
                    source_json TEXT,
                    first_seen_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (network, chain_id, token_address)
                );
                CREATE TABLE token_library_catalog (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    token_key_id TEXT NOT NULL,
                    v4_currency_key TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    token_name TEXT,
                    token_decimals INTEGER NOT NULL,
                    token_kind TEXT NOT NULL,
                    is_native INTEGER NOT NULL DEFAULT 0,
                    is_wrapped_native INTEGER NOT NULL DEFAULT 0,
                    is_stable INTEGER NOT NULL DEFAULT 0,
                    admission_status TEXT NOT NULL,
                    admission_source TEXT NOT NULL,
                    metadata_source TEXT NOT NULL,
                    metadata_checksum TEXT NOT NULL,
                    source_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    first_admitted_at TEXT,
                    updated_at TEXT NOT NULL,
                    blocked_reason TEXT,
                    PRIMARY KEY (network, chain_id, token_key_id)
                );
                ",
            )
            .expect("create token tables");
        // The compatibility projection is stale: it carries a placeholder
        // symbol and no decimals at all.
        connection
            .execute(
                "INSERT INTO chain_tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    "robinhood",
                    4663_i64,
                    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
                    "UNRESOLVED:0X5FC5360D",
                    "UNRESOLVED:0X5FC5360D",
                    Option::<i64>::None,
                    "stable",
                    0_i64,
                    0_i64,
                    1_i64,
                    "USDG",
                    "{}",
                    "{}",
                    "2026-10-09T00:00:00.000Z",
                    "2026-10-09T00:00:00.000Z"
                ],
            )
            .expect("insert stale projection row");
        // The Token Manager knows the truth about the same token.
        connection
            .execute(
                "INSERT INTO token_library_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    "robinhood",
                    4663_i64,
                    "robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168",
                    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
                    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
                    "USDG",
                    "USDG",
                    6_i64,
                    "stable",
                    0_i64,
                    0_i64,
                    1_i64,
                    "admitted",
                    "bootstrap:core4_allowlist",
                    "fixed_core4_allowlist",
                    "sha256:test",
                    "{}",
                    "2026-10-09T00:00:00.000Z",
                    Option::<String>::None,
                    "2026-10-09T00:00:00.000Z",
                    Option::<String>::None
                ],
            )
            .expect("insert token manager row");
        drop(connection);

        let connection = open_readonly(&path_text).expect("open read-only");
        let decimals = load_chain_token_decimals_rows(&connection, "robinhood", 4663)
            .expect("load token decimals");
        assert_eq!(decimals.len(), 1);
        assert_eq!(
            decimals[0]["token_address"].as_str().unwrap(),
            "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
        );
        assert_eq!(decimals[0]["token_decimals"].as_i64().unwrap(), 6);
        assert_eq!(decimals[0]["token_symbol"].as_str().unwrap(), "USDG");

        let registry = load_chain_token_registry_rows(&connection, "robinhood", 4663)
            .expect("load token registry rows");
        assert_eq!(registry.len(), 1);
        assert_eq!(
            registry[0]["token_address"].as_str().unwrap(),
            "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
        );
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
