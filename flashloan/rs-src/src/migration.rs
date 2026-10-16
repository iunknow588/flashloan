use rusqlite::{params, Connection};
use serde_json::{json, Value};

const PAIR_TEMP_DDL: &str = "
CREATE TABLE {table} (
    network TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    anchor_scope TEXT NOT NULL,
    anchor_symbol TEXT NOT NULL,
    anchor_address TEXT NOT NULL,
    counter_address TEXT NOT NULL,
    address_pair TEXT NOT NULL,
    direction_key TEXT NOT NULL,
    pool_count INTEGER NOT NULL DEFAULT 0,
    fixed_fee_liquid_pool_count INTEGER NOT NULL DEFAULT 0,
    priced_pool_count INTEGER NOT NULL DEFAULT 0,
    yc_optimal_pool_id TEXT,
    zc_optimal_pool_id TEXT,
    yc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
    zc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
    price_reference_json TEXT,
    price_outlier_filter TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key),
    FOREIGN KEY (network, chain_id, anchor_address)
        REFERENCES chain_tokens (network, chain_id, token_address),
    FOREIGN KEY (network, chain_id, counter_address)
        REFERENCES chain_tokens (network, chain_id, token_address)
)
";

const POOL_TEMP_DDL: &str = "
CREATE TABLE {table} (
    network TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    anchor_scope TEXT NOT NULL,
    anchor_symbol TEXT NOT NULL,
    address_pair TEXT NOT NULL,
    direction_key TEXT NOT NULL,
    direction_address_pair TEXT,
    pool_id TEXT NOT NULL,
    pool_key_json TEXT,
    pool_manager TEXT,
    token0 TEXT,
    token1 TEXT,
    fee INTEGER,
    fee_label TEXT,
    dynamic_fee INTEGER NOT NULL DEFAULT 0,
    special_fee INTEGER NOT NULL DEFAULT 0,
    tick_spacing INTEGER,
    hooks TEXT,
    sqrt_price_x96 TEXT,
    tick INTEGER,
    has_liquidity INTEGER,
    liquidity TEXT,
    liquidity_index REAL,
    estimated_tvl_usd REAL,
    balances_json TEXT,
    depth_status TEXT,
    depth_band_json TEXT,
    usable_for_observation INTEGER,
    is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
    is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
    hop_recommendation_json TEXT,
    optimal_roles_json TEXT,
    liquidity_read_at TEXT,
    liquidity_block_number INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id),
    FOREIGN KEY (network, chain_id, token0)
        REFERENCES chain_tokens (network, chain_id, token_address),
    FOREIGN KEY (network, chain_id, token1)
        REFERENCES chain_tokens (network, chain_id, token_address)
)
";

const RECOMMENDATION_TEMP_DDL: &str = "
CREATE TABLE {table} (
    network TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    anchor_scope TEXT NOT NULL,
    anchor_symbol TEXT NOT NULL,
    counter_address TEXT NOT NULL,
    direction_key TEXT NOT NULL,
    hop TEXT NOT NULL,
    rank INTEGER NOT NULL,
    pool_id TEXT NOT NULL,
    depth_score REAL,
    price REAL,
    fee INTEGER,
    selection_basis TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, direction_key, hop, pool_id)
)
";

fn execute(connection: &Connection, sql: &str) -> Result<(), String> {
    connection
        .execute_batch(sql)
        .map_err(|error| format!("direction migration SQL failed: {error}"))
}

fn table_columns(connection: &Connection, table: &str) -> Result<Vec<String>, String> {
    let mut statement = connection
        .prepare("SELECT name FROM pragma_table_info(?1) ORDER BY cid")
        .map_err(|error| format!("could not inspect table {table}: {error}"))?;
    let columns = statement
        .query_map(params![table], |row| row.get::<_, String>(0))
        .map_err(|error| format!("could not inspect table {table}: {error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("could not inspect table {table}: {error}"))?;
    Ok(columns)
}

fn primary_key_columns(connection: &Connection, table: &str) -> Result<Vec<String>, String> {
    let mut statement = connection
        .prepare("SELECT name FROM pragma_table_info(?1) WHERE pk > 0 ORDER BY pk, cid")
        .map_err(|error| format!("could not inspect primary key for {table}: {error}"))?;
    let columns = statement
        .query_map(params![table], |row| row.get::<_, String>(0))
        .map_err(|error| format!("could not inspect primary key for {table}: {error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("could not inspect primary key for {table}: {error}"))?;
    Ok(columns)
}

fn ensure_column(
    connection: &Connection,
    table: &str,
    column: &str,
    definition: &str,
) -> Result<(), String> {
    let columns = table_columns(connection, table)?;
    if columns.iter().any(|name| name == column) {
        return Ok(());
    }
    connection
        .execute(
            &format!(
                "ALTER TABLE {table} ADD COLUMN {column} {definition}"
            ),
            [],
        )
        .map_err(|error| format!("could not add {table}.{column}: {error}"))?;
    Ok(())
}

fn rebuild_pair_table(connection: &Connection, table: &str) -> Result<(), String> {
    if primary_key_columns(connection, table)?.iter().any(|name| name == "direction_key") {
        return Ok(());
    }
    let temp = format!("{table}_directional_v1");
    execute(
        connection,
        &format!("DROP TABLE IF EXISTS {temp};\n{}", PAIR_TEMP_DDL.replace("{table}", &temp)),
    )?;
    execute(
        connection,
        &format!(
            "INSERT OR REPLACE INTO {temp} (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                anchor_address, counter_address, address_pair, direction_key,
                pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
                yc_optimal_pool_id, zc_optimal_pool_id,
                yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                price_reference_json, price_outlier_filter, created_at, updated_at
            )
            SELECT
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                anchor_address, counter_address, address_pair, direction_key,
                pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
                yc_optimal_pool_id, zc_optimal_pool_id,
                yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                price_reference_json, price_outlier_filter, created_at, updated_at
            FROM {table};"
        ),
    )?;
    execute(connection, &format!("DROP TABLE {table};\nALTER TABLE {temp} RENAME TO {table};"))?;
    Ok(())
}

fn rebuild_pool_table(connection: &Connection, table: &str) -> Result<(), String> {
    if primary_key_columns(connection, table)?.iter().any(|name| name == "direction_key") {
        return Ok(());
    }
    let temp = format!("{table}_directional_v1");
    execute(
        connection,
        &format!("DROP TABLE IF EXISTS {temp};\n{}", POOL_TEMP_DDL.replace("{table}", &temp)),
    )?;
    execute(
        connection,
        &format!(
            "INSERT OR IGNORE INTO {temp} (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair, pool_id,
                pool_key_json, pool_manager, token0, token1, fee, fee_label,
                dynamic_fee, special_fee, tick_spacing, hooks, sqrt_price_x96,
                tick, has_liquidity, liquidity, liquidity_index, estimated_tvl_usd,
                balances_json, depth_status, depth_band_json,
                usable_for_observation, is_first_hop_recommended,
                is_third_hop_recommended, hop_recommendation_json,
                optimal_roles_json, liquidity_read_at, liquidity_block_number,
                created_at, updated_at
            )
            SELECT
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair, pool_id,
                pool_key_json, pool_manager, token0, token1, fee, fee_label,
                dynamic_fee, special_fee, tick_spacing, hooks, sqrt_price_x96,
                tick, has_liquidity, liquidity, liquidity_index, estimated_tvl_usd,
                balances_json, depth_status, depth_band_json,
                usable_for_observation, is_first_hop_recommended,
                is_third_hop_recommended, hop_recommendation_json,
                optimal_roles_json, liquidity_read_at, liquidity_block_number,
                created_at, updated_at
            FROM {table};"
        ),
    )?;
    execute(connection, &format!("DROP TABLE {table};\nALTER TABLE {temp} RENAME TO {table};"))?;
    Ok(())
}

fn rebuild_recommendation_table(connection: &Connection) -> Result<(), String> {
    let table = "dex_candidate_pool_recommendations";
    if primary_key_columns(connection, table)?.iter().any(|name| name == "direction_key") {
        return Ok(());
    }
    let temp = format!("{table}_directional_v1");
    execute(
        connection,
        &format!("DROP TABLE IF EXISTS {temp};\n{}", RECOMMENDATION_TEMP_DDL.replace("{table}", &temp)),
    )?;
    execute(
        connection,
        &format!(
            "INSERT OR IGNORE INTO {temp} (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                counter_address, direction_key, hop, rank, pool_id, depth_score,
                price, fee, selection_basis, created_at, updated_at
            )
            SELECT
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                counter_address, direction_key, hop, rank, pool_id, depth_score,
                price, fee, selection_basis, created_at, updated_at
            FROM {table};"
        ),
    )?;
    execute(connection, &format!("DROP TABLE {table};\nALTER TABLE {temp} RENAME TO {table};"))?;
    Ok(())
}

fn backfill_observation_rows(connection: &Connection, table: &str) -> Result<usize, String> {
    let has_column = if table == "market_observation_yz_pairs" {
        "has_direct_swap_pool"
    } else {
        "has_non_normal_swap_pool"
    };
    let count_column = if table == "market_observation_yz_pairs" {
        "direct_swap_pool_count"
    } else {
        "non_normal_swap_pool_count"
    };
    let mut select = connection
        .prepare(&format!(
            "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                    direction_key, token_x_address, token_y_address,
                    address_pair, pool_ids_json, optimal_pool_id
             FROM {table}
             ORDER BY anchor_symbol, direction_key"
        ))
        .map_err(|error| format!("could not prepare observation backfill for {table}: {error}"))?;
    let rows = select
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, String>(6)?,
                row.get::<_, String>(7)?,
                row.get::<_, String>(8)?,
                row.get::<_, Option<String>>(9)?,
                row.get::<_, Option<String>>(10)?,
            ))
        })
        .map_err(|error| format!("could not read observation rows for {table}: {error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("could not read observation rows for {table}: {error}"))?;
    let mut updates = 0usize;
    let mut statement = connection
        .prepare(&format!(
            "UPDATE {table}
             SET address_pair = ?, pool_ids_json = ?, optimal_pool_id = ?,
                 {has_column} = ?, {count_column} = ?
             WHERE network = ? AND chain_id = ? AND protocol = ?
               AND anchor_scope = ? AND anchor_symbol = ? AND direction_key = ?"
        ))
        .map_err(|error| format!("could not prepare observation update for {table}: {error}"))?;
    for (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key, token_x, token_y, address_pair, pool_ids_json, optimal_pool_id) in rows {
        let direction_pair = format!("{}:{}", token_x.to_lowercase(), token_y.to_lowercase());
        let raw_pool_ids_json = pool_ids_json.clone().unwrap_or_else(|| "[]".to_string());
        let parsed: Value = serde_json::from_str(&raw_pool_ids_json)
            .unwrap_or(Value::Array(vec![]));
        let pool_ids = match parsed {
            Value::Array(values) => values.into_iter().take(1).collect::<Vec<_>>(),
            _ => vec![],
        };
        let encoded_pool_ids = serde_json::to_string(&pool_ids).map_err(|error| error.to_string())?;
        let raw_optimal_pool_id = optimal_pool_id.as_deref().unwrap_or("").to_string();
        let optimal = match optimal_pool_id.as_deref().filter(|value| !value.trim().is_empty()) {
            Some(value) => value.to_string(),
            None => pool_ids
                .first()
                .and_then(|value| value.as_str())
                .unwrap_or("")
                .to_string(),
        };
        if address_pair == direction_pair
            && raw_optimal_pool_id == optimal
            && raw_pool_ids_json == encoded_pool_ids
        {
            continue;
        }
        statement
            .execute(params![
                direction_pair,
                encoded_pool_ids,
                optimal,
                i64::from(!pool_ids.is_empty()),
                pool_ids.len() as i64,
                network,
                chain_id,
                protocol,
                anchor_scope,
                anchor_symbol,
                direction_key,
            ])
            .map_err(|error| format!("could not update observation rows for {table}: {error}"))?;
        updates += 1;
    }
    Ok(updates)
}

pub fn migrate_direction_schema(connection: &Connection) -> Result<Value, String> {
    execute(connection, "PRAGMA foreign_keys=OFF; PRAGMA busy_timeout=5000;")?;

    for (table, column, definition) in [
        ("dex_candidate_anchor_tokens", "yc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_anchor_tokens", "zc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_abnormal_anchor_tokens", "yc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_abnormal_anchor_tokens", "zc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_pool_pairs", "direction_key", "TEXT"),
        ("dex_candidate_pool_pairs", "yc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_pool_pairs", "zc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_abnormal_pool_pairs", "direction_key", "TEXT"),
        ("dex_candidate_abnormal_pool_pairs", "yc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_abnormal_pool_pairs", "zc_backup_pool_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("dex_candidate_pools", "direction_key", "TEXT"),
        ("dex_candidate_pools", "direction_address_pair", "TEXT"),
        ("dex_candidate_pool_recommendations", "direction_key", "TEXT"),
        ("dex_candidate_abnormal_pools", "direction_key", "TEXT"),
        ("dex_candidate_abnormal_pools", "direction_address_pair", "TEXT"),
        ("market_observation_yz_pairs", "optimal_pool_id", "TEXT"),
        ("market_observation_abnormal_yz_pairs", "optimal_pool_id", "TEXT"),
        ("v4_exchange_pool_liquidity_snapshots", "evidence_source", "TEXT"),
        ("market_observation_liquidity_snapshots", "evidence_source", "TEXT"),
    ] {
        ensure_column(connection, table, column, definition)?;
    }

    for (table, yc_column, zc_column) in [
        ("dex_candidate_pool_pairs", "yc_optimal_pool_id", "zc_optimal_pool_id"),
        ("dex_candidate_abnormal_pool_pairs", "yc_optimal_pool_id", "zc_optimal_pool_id"),
    ] {
        let forward = format!(
            "SELECT
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                lower(anchor_address), lower(counter_address),
                lower(anchor_address) || ':' || lower(counter_address),
                lower(anchor_address) || ':' || lower(counter_address),
                pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
                {yc_column}, '',
                '[]', '[]', price_reference_json, price_outlier_filter, created_at, updated_at
            FROM {table}
            WHERE direction_key IS NULL OR direction_key = ''
               OR address_pair != direction_key"
        );
        let reverse = format!(
            "SELECT
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                lower(counter_address), lower(anchor_address),
                lower(counter_address) || ':' || lower(anchor_address),
                lower(counter_address) || ':' || lower(anchor_address),
                pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
                '', {zc_column},
                '[]', '[]', price_reference_json, price_outlier_filter, created_at, updated_at
            FROM {table}
            WHERE direction_key IS NULL OR direction_key = ''
               OR address_pair != direction_key"
        );
        execute(
            connection,
            &format!(
                "INSERT OR REPLACE INTO {table} (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    anchor_address, counter_address, address_pair, direction_key,
                    pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
                    {yc_column}, {zc_column},
                    yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                    price_reference_json, price_outlier_filter, created_at, updated_at
                )
                {forward}
                UNION ALL
                {reverse};"
            ),
        )?;
    }

    for table in ["dex_candidate_pools", "dex_candidate_abnormal_pools"] {
        execute(
            connection,
            &format!(
                "INSERT OR REPLACE INTO {table} (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    address_pair, direction_key, direction_address_pair, pool_id,
                    pool_key_json, pool_manager, token0, token1, fee, fee_label,
                    dynamic_fee, special_fee, tick_spacing, hooks, sqrt_price_x96,
                    tick, has_liquidity, liquidity, liquidity_index, estimated_tvl_usd,
                    balances_json, depth_status, depth_band_json,
                    usable_for_observation, is_first_hop_recommended,
                    is_third_hop_recommended, hop_recommendation_json,
                    optimal_roles_json, liquidity_read_at, liquidity_block_number,
                    created_at, updated_at
                )
                SELECT
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    lower(token0) || ':' || lower(token1),
                    lower(token0) || ':' || lower(token1),
                    lower(token0) || ':' || lower(token1),
                    pool_id, pool_key_json, pool_manager, token0, token1, fee, fee_label,
                    dynamic_fee, special_fee, tick_spacing, hooks, sqrt_price_x96,
                    tick, has_liquidity, liquidity, liquidity_index, estimated_tvl_usd,
                    balances_json, depth_status, depth_band_json,
                    usable_for_observation, is_first_hop_recommended,
                    is_third_hop_recommended, hop_recommendation_json,
                    optimal_roles_json, liquidity_read_at, liquidity_block_number,
                    created_at, updated_at
                FROM {table}
                WHERE token0 IS NOT NULL AND token1 IS NOT NULL
                  AND (direction_key IS NULL OR direction_key = ''
                       OR address_pair != direction_key
                       OR direction_address_pair IS NULL
                       OR direction_address_pair != direction_key);"
            ),
        )?;
    }

    execute(
        connection,
        "UPDATE dex_candidate_pool_recommendations
         SET direction_key = CASE WHEN lower(hop) = 'third'
             THEN lower(counter_address) || ':' || lower((
                 SELECT anchor_address FROM dex_candidate_pool_pairs
                 WHERE network = dex_candidate_pool_recommendations.network
                   AND chain_id = dex_candidate_pool_recommendations.chain_id
                   AND protocol = dex_candidate_pool_recommendations.protocol
                   AND anchor_scope = dex_candidate_pool_recommendations.anchor_scope
                   AND anchor_symbol = dex_candidate_pool_recommendations.anchor_symbol
                 ORDER BY anchor_address LIMIT 1
             ))
             ELSE lower((
                 SELECT anchor_address FROM dex_candidate_pool_pairs
                 WHERE network = dex_candidate_pool_recommendations.network
                   AND chain_id = dex_candidate_pool_recommendations.chain_id
                   AND protocol = dex_candidate_pool_recommendations.protocol
                   AND anchor_scope = dex_candidate_pool_recommendations.anchor_scope
                   AND anchor_symbol = dex_candidate_pool_recommendations.anchor_symbol
                 ORDER BY anchor_address LIMIT 1
             )) || ':' || lower(counter_address)
         END
         WHERE direction_key IS NULL OR direction_key = '';",
    )?;
    execute(
        connection,
        "DELETE FROM dex_candidate_pool_recommendations
         WHERE rowid NOT IN (
             SELECT MIN(rowid)
             FROM dex_candidate_pool_recommendations
             GROUP BY network, chain_id, protocol, anchor_scope, anchor_symbol,
                      counter_address, direction_key, hop
         );",
    )?;

    for table in ["dex_candidate_pool_pairs", "dex_candidate_abnormal_pool_pairs"] {
        execute(
            connection,
            &format!(
                "DELETE FROM {table}
                 WHERE direction_key IS NULL OR direction_key = ''
                    OR address_pair != direction_key;"
            ),
        )?;
    }
    for table in ["dex_candidate_pools", "dex_candidate_abnormal_pools"] {
        execute(
            connection,
            &format!(
                "DELETE FROM {table}
                 WHERE direction_key IS NULL OR direction_key = ''
                    OR address_pair != direction_key
                    OR direction_address_pair IS NULL
                    OR direction_address_pair != direction_key;"
            ),
        )?;
    }
    execute(
        connection,
        "DELETE FROM dex_candidate_pool_recommendations
         WHERE direction_key IS NULL OR direction_key = '';",
    )?;

    execute(
        connection,
        "UPDATE dex_candidate_pools
         SET address_pair = lower(token0) || ':' || lower(token1),
             direction_key = lower(token0) || ':' || lower(token1),
             direction_address_pair = lower(token0) || ':' || lower(token1),
             is_first_hop_recommended = CASE WHEN EXISTS (
                 SELECT 1 FROM dex_candidate_pool_recommendations rec
                 WHERE rec.network = dex_candidate_pools.network
                   AND rec.chain_id = dex_candidate_pools.chain_id
                   AND rec.protocol = dex_candidate_pools.protocol
                   AND rec.anchor_scope = dex_candidate_pools.anchor_scope
                   AND rec.anchor_symbol = dex_candidate_pools.anchor_symbol
                   AND rec.direction_key IN (
                       lower(dex_candidate_pools.token0) || ':' || lower(dex_candidate_pools.token1),
                       lower(dex_candidate_pools.token1) || ':' || lower(dex_candidate_pools.token0)
                   )
                   AND rec.hop = 'first' AND rec.pool_id = dex_candidate_pools.pool_id
             ) THEN 1 ELSE 0 END,
             is_third_hop_recommended = CASE WHEN EXISTS (
                 SELECT 1 FROM dex_candidate_pool_recommendations rec
                 WHERE rec.network = dex_candidate_pools.network
                   AND rec.chain_id = dex_candidate_pools.chain_id
                   AND rec.protocol = dex_candidate_pools.protocol
                   AND rec.anchor_scope = dex_candidate_pools.anchor_scope
                   AND rec.anchor_symbol = dex_candidate_pools.anchor_symbol
                   AND rec.direction_key IN (
                       lower(dex_candidate_pools.token0) || ':' || lower(dex_candidate_pools.token1),
                       lower(dex_candidate_pools.token1) || ':' || lower(dex_candidate_pools.token0)
                   )
                   AND rec.hop = 'third' AND rec.pool_id = dex_candidate_pools.pool_id
             ) THEN 1 ELSE 0 END,
             hop_recommendation_json = '{\"first\":' || CASE WHEN EXISTS (
                 SELECT 1 FROM dex_candidate_pool_recommendations rec
                 WHERE rec.network = dex_candidate_pools.network
                   AND rec.chain_id = dex_candidate_pools.chain_id
                   AND rec.protocol = dex_candidate_pools.protocol
                   AND rec.anchor_scope = dex_candidate_pools.anchor_scope
                   AND rec.anchor_symbol = dex_candidate_pools.anchor_symbol
                   AND rec.direction_key IN (
                       lower(dex_candidate_pools.token0) || ':' || lower(dex_candidate_pools.token1),
                       lower(dex_candidate_pools.token1) || ':' || lower(dex_candidate_pools.token0)
                   )
                   AND rec.hop = 'first' AND rec.pool_id = dex_candidate_pools.pool_id
             ) THEN 1 ELSE 0 END || ',\"third\":' || CASE WHEN EXISTS (
                 SELECT 1 FROM dex_candidate_pool_recommendations rec
                 WHERE rec.network = dex_candidate_pools.network
                   AND rec.chain_id = dex_candidate_pools.chain_id
                   AND rec.protocol = dex_candidate_pools.protocol
                   AND rec.anchor_scope = dex_candidate_pools.anchor_scope
                   AND rec.anchor_symbol = dex_candidate_pools.anchor_symbol
                   AND rec.direction_key IN (
                       lower(dex_candidate_pools.token0) || ':' || lower(dex_candidate_pools.token1),
                       lower(dex_candidate_pools.token1) || ':' || lower(dex_candidate_pools.token0)
                   )
                   AND rec.hop = 'third' AND rec.pool_id = dex_candidate_pools.pool_id
             ) THEN 1 ELSE 0 END || '}',
             optimal_roles_json = '{\"yc\":' || CASE WHEN EXISTS (
                 SELECT 1 FROM dex_candidate_pool_recommendations rec
                 WHERE rec.network = dex_candidate_pools.network
                   AND rec.chain_id = dex_candidate_pools.chain_id
                   AND rec.protocol = dex_candidate_pools.protocol
                   AND rec.anchor_scope = dex_candidate_pools.anchor_scope
                   AND rec.anchor_symbol = dex_candidate_pools.anchor_symbol
                   AND rec.direction_key IN (
                       lower(dex_candidate_pools.token0) || ':' || lower(dex_candidate_pools.token1),
                       lower(dex_candidate_pools.token1) || ':' || lower(dex_candidate_pools.token0)
                   )
                   AND rec.hop = 'first' AND rec.pool_id = dex_candidate_pools.pool_id
             ) THEN 1 ELSE 0 END || ',\"zc\":' || CASE WHEN EXISTS (
                 SELECT 1 FROM dex_candidate_pool_recommendations rec
                 WHERE rec.network = dex_candidate_pools.network
                   AND rec.chain_id = dex_candidate_pools.chain_id
                   AND rec.protocol = dex_candidate_pools.protocol
                   AND rec.anchor_scope = dex_candidate_pools.anchor_scope
                   AND rec.anchor_symbol = dex_candidate_pools.anchor_symbol
                   AND rec.direction_key IN (
                       lower(dex_candidate_pools.token0) || ':' || lower(dex_candidate_pools.token1),
                       lower(dex_candidate_pools.token1) || ':' || lower(dex_candidate_pools.token0)
                   )
                   AND rec.hop = 'third' AND rec.pool_id = dex_candidate_pools.pool_id
             ) THEN 1 ELSE 0 END || '}'
         WHERE token0 IS NOT NULL AND token1 IS NOT NULL;",
    )?;

    rebuild_pair_table(connection, "dex_candidate_pool_pairs")?;
    rebuild_pair_table(connection, "dex_candidate_abnormal_pool_pairs")?;
    rebuild_pool_table(connection, "dex_candidate_pools")?;
    rebuild_pool_table(connection, "dex_candidate_abnormal_pools")?;
    rebuild_recommendation_table(connection)?;

    let observation_updates = backfill_observation_rows(connection, "market_observation_yz_pairs")?;
    let abnormal_observation_updates =
        backfill_observation_rows(connection, "market_observation_abnormal_yz_pairs")?;

    let mut integrity_errors: Vec<String> = vec![];
    {
        let mut groups = connection
            .prepare(
                "SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                        COUNT(DISTINCT direction_key) AS direction_count
                 FROM market_observation_yz_pairs
                 GROUP BY network, chain_id, protocol, anchor_scope, anchor_symbol
                 ORDER BY anchor_symbol",
            )
            .map_err(|error| format!("could not prepare observation integrity check: {error}"))?;
        let rows = groups
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                ))
            })
            .map_err(|error| format!("could not read observation integrity groups: {error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("could not read observation integrity groups: {error}"))?;
        for (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_count) in rows {
            let token_count: i64 = connection
                .query_row(
                    "SELECT COUNT(*) FROM dex_candidate_anchor_tokens
                     WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
                       AND anchor_scope = ?4 AND anchor_symbol = ?5",
                    params![network, chain_id, protocol, anchor_scope, anchor_symbol],
                    |row| row.get(0),
                )
                .map_err(|error| format!("could not count anchor tokens for integrity: {error}"))?;
            let expected = token_count * (token_count - 1);
            if direction_count != expected {
                integrity_errors.push(format!(
                    "{anchor_symbol}: expected {expected} directions, found {direction_count}"
                ));
            }
        }
    }
    if !integrity_errors.is_empty() {
        return Err(format!(
            "directional migration incomplete: {}",
            integrity_errors.join("; ")
        ));
    }

    Ok(json!({
        "ok": true,
        "migrated": "directional_pairs_v1",
        "observationUpdates": observation_updates,
        "abnormalObservationUpdates": abnormal_observation_updates,
    }))
}
