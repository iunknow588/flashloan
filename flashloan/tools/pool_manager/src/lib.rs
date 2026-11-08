use rusqlite::{params_from_iter, Connection};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub mod scope;

pub use scope::{
    materialize_scope, materialize_scope_write, read_scope_status, ScopeError,
    ScopeMaterializationResult, ScopeStatus, ScopeWriteOutcome, ScopeWriteRequest,
};

const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";
const MAX_CANDIDATE_FIXED_FEE: i64 = 3000;

fn network_for_chain_id(chain_id: i64) -> String {
    match chain_id {
        4663 => "robinhood".to_string(),
        46630 => "robinhood-testnet".to_string(),
        _ => format!("chain-{chain_id}"),
    }
}

fn canonical_pair_key(base: &str, quote: &str) -> String {
    let base = base.trim().to_lowercase();
    let quote = quote.trim().to_lowercase();
    if base <= quote {
        format!("{base}:{quote}")
    } else {
        format!("{quote}:{base}")
    }
}

fn pair_key_variants(base: &str, quote: &str) -> Vec<String> {
    let canonical = canonical_pair_key(base, quote);
    let reverse = canonical
        .split_once(':')
        .map(|(left, right)| format!("{right}:{left}"))
        .unwrap_or_else(|| canonical.clone());
    vec![canonical, reverse]
}

fn lowered(value: Option<&str>) -> String {
    value.unwrap_or("").trim().to_lowercase()
}

fn is_valid_token_address(value: &str) -> bool {
    let text = value.trim().to_lowercase();
    if !text.starts_with("0x") {
        return false;
    }
    if let Ok(number) = i128::from_str_radix(text.trim_start_matches("0x"), 16) {
        if number == 0 {
            return true;
        }
    }
    text.len() == 42
}

fn is_zero_hook(value: Option<&str>) -> bool {
    let text = lowered(value);
    text.is_empty() || text == ZERO_ADDRESS
}

struct PoolQuality<'a> {
    fee: i64,
    dynamic_fee: i64,
    special_fee: i64,
    hooks: Option<&'a str>,
    token0: Option<&'a str>,
    token1: Option<&'a str>,
    has_liquidity: i64,
    liquidity_index: Option<f64>,
}

fn quality_reasons(pool: PoolQuality<'_>) -> Vec<String> {
    let PoolQuality {
        fee,
        dynamic_fee,
        special_fee,
        hooks,
        token0,
        token1,
        has_liquidity,
        liquidity_index,
    } = pool;
    let mut reasons = Vec::new();
    if fee <= 0 {
        reasons.push("fee_non_positive".to_string());
    }
    if fee > MAX_CANDIDATE_FIXED_FEE {
        reasons.push("fee_too_high".to_string());
    }
    if dynamic_fee != 0 || special_fee != 0 {
        reasons.push("dynamic_or_special_fee".to_string());
    }
    if !is_zero_hook(hooks) {
        reasons.push("non_zero_hook".to_string());
    }
    let token0 = token0.unwrap_or("");
    let token1 = token1.unwrap_or("");
    if !is_valid_token_address(token0) {
        reasons.push("invalid_token0".to_string());
    }
    if !is_valid_token_address(token1) {
        reasons.push("invalid_token1".to_string());
    }
    if token0.trim().to_lowercase() == token1.trim().to_lowercase() && !token0.trim().is_empty() {
        reasons.push("same_token_addresses".to_string());
    }
    if has_liquidity == 0 {
        reasons.push("missing_liquidity".to_string());
    }
    if liquidity_index.unwrap_or(0.0) <= 0.0 {
        reasons.push("missing_positive_liquidity".to_string());
    }
    reasons
}

fn chunks<T: Clone>(values: &[T], size: usize) -> Vec<Vec<T>> {
    values.chunks(size).map(|chunk| chunk.to_vec()).collect()
}

fn params_for_scoped_values(network: &str, chain_id: i64, values: &[String]) -> Vec<String> {
    let mut params = vec![network.to_string(), chain_id.to_string()];
    params.extend(values.iter().cloned());
    params
}

fn affected_loop_keys_for_pool_ids(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> Result<Vec<String>, String> {
    let mut affected_loop_keys = BTreeSet::new();
    for chunk in chunks(pool_ids, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "SELECT DISTINCT loop_key FROM v4_closed_loop_hops
             WHERE network = ? AND chain_id = ? AND pool_id IN ({placeholders})"
        );
        let params = params_for_scoped_values(network, chain_id, &chunk);
        let mut hop_stmt = connection
            .prepare(&sql)
            .map_err(|error| error.to_string())?;
        let hop_rows = hop_stmt
            .query_map(params_from_iter(params.iter()), |row| {
                row.get::<_, String>(0)
            })
            .map_err(|error| error.to_string())?;
        for loop_key in hop_rows.flatten() {
            affected_loop_keys.insert(loop_key);
        }

        let json_placeholders = chunk
            .iter()
            .map(|_| "pool_ids_json LIKE ?")
            .collect::<Vec<_>>()
            .join(" OR ");
        let json_sql = format!(
            "SELECT DISTINCT loop_key FROM v4_closed_loops
             WHERE network = ? AND chain_id = ? AND ({json_placeholders})"
        );
        let mut params = vec![network.to_string(), chain_id.to_string()];
        params.extend(chunk.iter().map(|pool_id| format!("%{pool_id}%")));
        let mut json_stmt = connection
            .prepare(&json_sql)
            .map_err(|error| error.to_string())?;
        let json_rows = json_stmt
            .query_map(params_from_iter(params.iter()), |row| {
                row.get::<_, String>(0)
            })
            .map_err(|error| error.to_string())?;
        for loop_key in json_rows.flatten() {
            affected_loop_keys.insert(loop_key);
        }
    }
    let mut keys: Vec<String> = affected_loop_keys.into_iter().collect();
    keys.sort();
    Ok(keys)
}

fn audit_internal(connection: &Connection, chain_id: i64) -> Result<Value, String> {
    let network = network_for_chain_id(chain_id);
    let mut stmt = connection
        .prepare(
            "SELECT d.pool_id, d.fee, d.dynamic_fee, d.special_fee, d.hooks,
                    d.token0, d.token1, d.depth_status, d.has_liquidity,
                    d.liquidity_index, d.usable_for_observation,
                    s.evidence_source
             FROM dex_candidate_pools d
             LEFT JOIN v4_exchange_pool_liquidity_snapshots s
               ON s.network = d.network AND s.chain_id = d.chain_id
              AND s.protocol = d.protocol AND s.pool_id = d.pool_id
             WHERE d.network = ? AND d.chain_id = ?
             ORDER BY d.pool_id",
        )
        .map_err(|error| error.to_string())?;

    let rows = stmt
        .query_map(
            params_from_iter([network.as_str(), &chain_id.to_string()]),
            |row| {
                let pool_id: String = row.get(0)?;
                let fee: i64 = row.get(1)?;
                let dynamic_fee: i64 = row.get(2)?;
                let special_fee: i64 = row.get(3)?;
                let hooks: Option<String> = row.get(4)?;
                let token0: Option<String> = row.get(5)?;
                let token1: Option<String> = row.get(6)?;
                let depth_status: Option<String> = row.get(7)?;
                let has_liquidity: i64 = row.get(8)?;
                let liquidity_index: Option<f64> = row.get(9)?;
                let usable_for_observation: Option<i64> = row.get(10)?;
                let evidence_source: Option<String> = row.get(11)?;
                Ok((
                    pool_id.to_string(),
                    fee,
                    dynamic_fee,
                    special_fee,
                    hooks,
                    token0,
                    token1,
                    depth_status,
                    has_liquidity,
                    liquidity_index,
                    usable_for_observation.unwrap_or(0),
                    evidence_source,
                ))
            },
        )
        .map_err(|error| error.to_string())?;

    let mut bad_pool_ids = BTreeSet::new();
    let mut issue_by_pool: BTreeMap<String, Value> = BTreeMap::new();
    let mut candidate_total = 0;
    for row in rows {
        let row = row.map_err(|error| error.to_string())?;
        candidate_total += 1;
        let reasons = quality_reasons(PoolQuality {
            fee: row.1,
            dynamic_fee: row.2,
            special_fee: row.3,
            hooks: row.4.as_deref(),
            token0: row.5.as_deref(),
            token1: row.6.as_deref(),
            has_liquidity: row.8,
            liquidity_index: row.9,
        });
        if !reasons.is_empty() {
            bad_pool_ids.insert(row.0.clone());
            issue_by_pool.insert(
                row.0.clone(),
                json!({
                    "poolId": row.0,
                    "fee": row.1,
                    "dynamicFee": row.2 != 0,
                    "specialFee": row.3 != 0,
                    "depthStatus": row.7,
                    "hasLiquidity": row.8 != 0,
                    "usableForObservation": row.10 != 0,
                    "evidenceSource": row.11,
                    "reasons": reasons,
                }),
            );
        }
    }

    let bad_pool_list: Vec<String> = bad_pool_ids.into_iter().collect();
    let affected_loop_keys =
        affected_loop_keys_for_pool_ids(connection, &network, chain_id, &bad_pool_list)?;
    let affected_recommendations =
        count_affected_recommendations(connection, &network, chain_id, &bad_pool_list)?;

    Ok(json!({
        "network": network,
        "chainId": chain_id,
        "candidateTotal": candidate_total,
        "eligibleTotal": candidate_total - bad_pool_list.len(),
        "ineligibleTotal": bad_pool_list.len(),
        "ineligiblePoolIds": bad_pool_list,
        "reasonsByPool": issue_by_pool,
        "affectedClosedLoopCount": affected_loop_keys.len(),
        "affectedClosedLoopKeys": affected_loop_keys,
        "affectedRecommendationCount": affected_recommendations,
    }))
}

fn count_affected_recommendations(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
) -> Result<usize, String> {
    let mut count = 0;
    for chunk in chunks(pool_ids, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "SELECT COUNT(*) FROM dex_candidate_pool_recommendations
             WHERE network = ? AND chain_id = ? AND pool_id IN ({placeholders})"
        );
        let params = params_for_scoped_values(network, chain_id, &chunk);
        let value: i64 = connection
            .query_row(&sql, params_from_iter(params.iter()), |row| row.get(0))
            .map_err(|error| error.to_string())?;
        count += value as usize;
    }
    Ok(count)
}

pub fn audit(store: &str, chain_id: i64) -> Result<Value, String> {
    let connection = Connection::open_with_flags(
        store,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    audit_internal(&connection, chain_id)
}

fn delete_values(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
    values: &[String],
    column: &str,
) -> Result<usize, String> {
    if values.is_empty() {
        return Ok(0);
    }
    let mut count = 0;
    for chunk in chunks(values, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "DELETE FROM {table}
             WHERE network = ? AND chain_id = ? AND {column} IN ({placeholders})"
        );
        let params = params_for_scoped_values(network, chain_id, &chunk);
        count += connection
            .execute(&sql, params_from_iter(params.iter()))
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(count)
}

fn delete_values_scoped(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
    anchor_symbol: &str,
    values: &[String],
    column: &str,
) -> Result<usize, String> {
    if values.is_empty() {
        return Ok(0);
    }
    let mut count = 0;
    for chunk in chunks(values, 500) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "DELETE FROM {table}
             WHERE network = ? AND chain_id = ? AND anchor_symbol = ?
               AND {column} IN ({placeholders})"
        );
        let mut params = vec![
            network.to_string(),
            chain_id.to_string(),
            anchor_symbol.to_string(),
        ];
        params.extend(chunk.iter().cloned());
        count += connection
            .execute(&sql, params_from_iter(params.iter()))
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(count)
}

fn existing_candidate_pool_ids(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    anchor_symbol: &str,
    pair_variants: &[String],
) -> Result<Vec<String>, String> {
    let placeholders = pair_variants
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT pool_id FROM dex_candidate_pools
         WHERE network = ? AND chain_id = ? AND anchor_symbol = ?
           AND address_pair IN ({placeholders})
         ORDER BY pool_id"
    );
    let mut params = vec![
        network.to_string(),
        chain_id.to_string(),
        anchor_symbol.to_string(),
    ];
    params.extend(pair_variants.iter().cloned());
    let mut stmt = connection
        .prepare(&sql)
        .map_err(|error| error.to_string())?;
    let rows = stmt
        .query_map(params_from_iter(params.iter()), |row| {
            row.get::<_, String>(0)
        })
        .map_err(|error| error.to_string())?;
    let mut ids = Vec::new();
    for row in rows {
        ids.push(row.map_err(|error| error.to_string())?);
    }
    ids.sort();
    ids.dedup();
    Ok(ids)
}

pub fn apply_cleanup(store: &str, chain_id: i64) -> Result<Value, String> {
    let connection = Connection::open(store).map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    let audit = audit_internal(&connection, chain_id)?;
    let network = network_for_chain_id(chain_id);
    let bad_pool_ids: Vec<String> = audit["ineligiblePoolIds"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|value| value.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let loop_keys: Vec<String> = audit["affectedClosedLoopKeys"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(|value| value.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let mut cleanup = serde_json::Map::new();
    if !bad_pool_ids.is_empty() {
        cleanup.insert(
            "candidatePoolsDeleted".to_string(),
            json!(delete_values(
                &connection,
                "dex_candidate_pools",
                &network,
                chain_id,
                &bad_pool_ids,
                "pool_id",
            )?),
        );
        cleanup.insert(
            "recommendationsDeleted".to_string(),
            json!(delete_values(
                &connection,
                "dex_candidate_pool_recommendations",
                &network,
                chain_id,
                &bad_pool_ids,
                "pool_id",
            )?),
        );
    }
    cleanup.insert("consumersNeedRebuild".to_string(), json!(loop_keys.len()));
    Ok(json!({
        "audit": audit,
        "cleanup": cleanup,
    }))
}

#[derive(serde::Serialize)]
struct RawEligiblePool {
    pool_id: String,
    token0: String,
    token1: String,
    fee: i64,
    depth_status: String,
    liquidity_index: Option<f64>,
}

fn eligible_raw_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    limit: usize,
) -> Result<(Vec<RawEligiblePool>, Vec<String>), String> {
    let sql = r#"
        SELECT e.pool_id, e.token0, e.token1, e.fee,
               s.depth_status, s.liquidity_index, s.has_liquidity
        FROM v4_exchange_pools e
        JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = e.network AND s.chain_id = e.chain_id
         AND s.protocol = e.protocol AND s.pool_id = e.pool_id
        WHERE e.network = ? AND e.chain_id = ? AND e.protocol = 'uniswap_v4'
          AND ((lower(e.token0) = ? AND lower(e.token1) = ?)
            OR (lower(e.token0) = ? AND lower(e.token1) = ?))
          AND length(e.token0) = 42 AND length(e.token1) = 42
          AND e.fee > 0 AND e.fee <= 3000
          AND e.dynamic_fee = 0 AND e.special_fee = 0
          AND lower(COALESCE(e.hooks, '')) IN ('', '0x0000000000000000000000000000000000000000')
          AND s.has_liquidity = 1
          AND COALESCE(s.liquidity_index, 0) > 0
        ORDER BY COALESCE(s.liquidity_index, 0) DESC, e.pool_id
        LIMIT ?
    "#;
    let base = base.trim().to_lowercase();
    let quote = quote.trim().to_lowercase();
    let mut stmt = connection.prepare(sql).map_err(|error| error.to_string())?;
    let rows = stmt
        .query_map(
            params_from_iter([
                network,
                &chain_id.to_string(),
                base.as_str(),
                quote.as_str(),
                quote.as_str(),
                base.as_str(),
                &limit.to_string(),
            ]),
            |row| {
                Ok(RawEligiblePool {
                    pool_id: row.get(0)?,
                    token0: row.get(1)?,
                    token1: row.get(2)?,
                    fee: row.get(3)?,
                    depth_status: row.get(4)?,
                    liquidity_index: row.get(5)?,
                })
            },
        )
        .map_err(|error| error.to_string())?;
    let mut pools = Vec::new();
    let mut pool_ids = Vec::new();
    for row in rows {
        let row = row.map_err(|error| error.to_string())?;
        pool_ids.push(row.pool_id.clone());
        pools.push(row);
    }
    Ok((pools, pool_ids))
}

fn upsert_eligible_pool_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    pool_group: &str,
    pools: &[RawEligiblePool],
) -> Result<usize, String> {
    let pair = canonical_pair_key(base, quote);
    let protocol = "uniswap_v4".to_string();
    let anchor_scope = "v4".to_string();
    let pool_group = pool_group.to_string();
    let fee_label = "".to_string();
    let zero_hook = ZERO_ADDRESS.to_string();
    let empty_text = "".to_string();
    let empty_json = "{}".to_string();
    let hop_json = "{\"first\":false,\"third\":false}".to_string();
    let none_text = Option::<String>::None;
    let none_i64 = Option::<i64>::None;
    let none_f64 = Option::<f64>::None;
    let mut insert_stmt = connection
        .prepare(
            r#"
            INSERT OR REPLACE INTO dex_candidate_pools (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair,
                pool_id, pool_key_json, pool_manager, token0, token1,
                fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                liquidity_index, estimated_tvl_usd, balances_json,
                depth_status, depth_band_json, usable_for_observation,
                is_first_hop_recommended, is_third_hop_recommended,
                hop_recommendation_json, optimal_roles_json,
                liquidity_read_at, liquidity_block_number,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            )
            "#,
        )
        .map_err(|error| error.to_string())?;
    let mut inserted = 0;
    for pool in pools {
        let fee = pool.fee;
        let params: Vec<&dyn rusqlite::ToSql> = vec![
            &network,
            &chain_id,
            &protocol,
            &anchor_scope,
            &pool_group,
            &pair,
            &pair,
            &pair,
            &pool.pool_id,
            &none_text,
            &none_text,
            &pool.token0,
            &pool.token1,
            &fee,
            &fee_label,
            &0i64,
            &0i64,
            &none_i64,
            &zero_hook,
            &none_text,
            &none_i64,
            &1i64,
            &empty_text,
            &pool.liquidity_index,
            &none_f64,
            &empty_json,
            &pool.depth_status,
            &empty_json,
            &1i64,
            &0i64,
            &0i64,
            &hop_json,
            &empty_json,
            &none_text,
            &none_i64,
        ];
        inserted += insert_stmt
            .execute(params_from_iter(params))
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(inserted)
}

pub fn refresh_pair(
    store: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    pool_group: &str,
    limit: usize,
    apply: bool,
) -> Result<Value, String> {
    let network = network_for_chain_id(chain_id);
    let connection = if apply {
        Connection::open(store).map_err(|error| error.to_string())?
    } else {
        Connection::open_with_flags(
            store,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .map_err(|error| error.to_string())?
    };
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    let pair_variants = pair_key_variants(base, quote);
    let existing_ids =
        existing_candidate_pool_ids(&connection, &network, chain_id, pool_group, &pair_variants)?;
    let (pools, eligible_ids) =
        eligible_raw_pools(&connection, &network, chain_id, base, quote, limit.max(1))?;
    let eligible_set: BTreeSet<&String> = eligible_ids.iter().collect();
    let existing_set: BTreeSet<&String> = existing_ids.iter().collect();
    let stale_ids: Vec<String> = existing_ids
        .iter()
        .filter(|pool_id| !eligible_set.contains(pool_id))
        .cloned()
        .collect();
    let missing_ids: Vec<String> = eligible_ids
        .iter()
        .filter(|pool_id| !existing_set.contains(pool_id))
        .cloned()
        .collect();
    let affected_keys =
        affected_loop_keys_for_pool_ids(&connection, &network, chain_id, &stale_ids)?;

    let mut inserted = 0;
    let mut deleted = 0;
    if apply {
        inserted = upsert_eligible_pool_rows(
            &connection,
            &network,
            chain_id,
            base,
            quote,
            pool_group,
            &pools,
        )?;
        deleted = delete_values_scoped(
            &connection,
            "dex_candidate_pools",
            &network,
            chain_id,
            pool_group,
            &stale_ids,
            "pool_id",
        )?;
        let deleted_recommendations = delete_values_scoped(
            &connection,
            "dex_candidate_pool_recommendations",
            &network,
            chain_id,
            pool_group,
            &stale_ids,
            "pool_id",
        )?;
        deleted += deleted_recommendations;
    }

    Ok(json!({
        "network": network,
        "chainId": chain_id,
        "poolGroup": pool_group,
        "base": base.to_lowercase(),
        "quote": quote.to_lowercase(),
        "pairKey": canonical_pair_key(base, quote),
        "apply": apply,
        "limit": limit.max(1),
        "existingPoolCount": existing_ids.len(),
        "eligiblePoolCount": pools.len(),
        "eligiblePoolIds": eligible_ids,
        "stalePoolCount": stale_ids.len(),
        "stalePoolIds": stale_ids,
        "missingPoolCount": missing_ids.len(),
        "missingPoolIds": missing_ids,
        "inserted": inserted,
        "deletedRows": deleted,
        "consumersNeedRebuild": affected_keys.len(),
        "affectedClosedLoopKeys": affected_keys,
    }))
}

pub fn refresh_key(
    store: &str,
    chain_id: i64,
    key_id: &str,
    pool_group: &str,
    limit: usize,
    apply: bool,
) -> Result<Value, String> {
    let key_id = key_id.trim();
    let (base, quote) = key_id
        .split_once(':')
        .ok_or_else(|| format!("key_id must use <tokenX>:<tokenY>, got {key_id:?}"))?;
    if !is_valid_token_address(base) || !is_valid_token_address(quote) {
        return Err(format!(
            "key_id contains an invalid token address: {key_id}"
        ));
    }
    if base.trim().to_lowercase() == quote.trim().to_lowercase() {
        return Err("key_id must contain two distinct token addresses".to_string());
    }
    let mut payload = refresh_pair(store, chain_id, base, quote, pool_group, limit, apply)?;
    let eligible_count = payload
        .get("eligiblePoolCount")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if let Some(object) = payload.as_object_mut() {
        object.insert("keyId".to_string(), json!(canonical_pair_key(base, quote)));
        object.insert("ready".to_string(), json!(eligible_count > 0));
    }
    Ok(payload)
}

pub fn add_pair(
    store: &str,
    chain_id: i64,
    base: &str,
    quote: &str,
    pool_group: &str,
    limit: usize,
    apply: bool,
) -> Result<Value, String> {
    let network = network_for_chain_id(chain_id);
    let connection = if apply {
        Connection::open(store).map_err(|error| error.to_string())?
    } else {
        Connection::open_with_flags(
            store,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
        )
        .map_err(|error| error.to_string())?
    };
    connection
        .busy_timeout(std::time::Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    let (pools, pool_ids) =
        eligible_raw_pools(&connection, &network, chain_id, base, quote, limit.max(1))?;

    let mut inserted = 0;
    if apply {
        inserted = upsert_eligible_pool_rows(
            &connection,
            &network,
            chain_id,
            base,
            quote,
            pool_group,
            &pools,
        )?;
    }

    Ok(json!({
        "network": network,
        "chainId": chain_id,
        "base": base.to_lowercase(),
        "quote": quote.to_lowercase(),
        "limit": limit.max(1),
        "apply": apply,
        "eligiblePoolCount": pools.len(),
        "eligiblePoolIds": pool_ids,
        "inserted": inserted,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    #[test]
    fn quality_ignores_usd_depth_labels_but_requires_onchain_liquidity() {
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let reasons = quality_reasons(PoolQuality {
            fee: 3000,
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(ZERO_ADDRESS),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(1000.0),
        });
        assert!(reasons.is_empty());
        assert_eq!(
            quality_reasons(PoolQuality {
                fee: 3000,
                dynamic_fee: 0,
                special_fee: 0,
                hooks: Some(ZERO_ADDRESS),
                token0: Some(&token0),
                token1: Some(&token1),
                has_liquidity: 1,
                liquidity_index: Some(1000.0),
            }),
            Vec::<String>::new()
        );
        assert!(quality_reasons(PoolQuality {
            fee: 3000,
            dynamic_fee: 0,
            special_fee: 0,
            hooks: Some(ZERO_ADDRESS),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(0.0),
        })
        .iter()
        .any(|reason| reason == "missing_positive_liquidity"));
    }

    #[test]
    fn rejects_hook_and_high_fee() {
        let token0 = format!("0x{}", "1".repeat(40));
        let token1 = format!("0x{}", "2".repeat(40));
        let reasons = quality_reasons(PoolQuality {
            fee: 10_000,
            dynamic_fee: 1,
            special_fee: 1,
            hooks: Some("0x0000000000000000000000000000000000000001"),
            token0: Some(&token0),
            token1: Some(&token1),
            has_liquidity: 1,
            liquidity_index: Some(1000.0),
        });
        assert!(reasons.contains(&"fee_too_high".to_string()));
        assert!(reasons.contains(&"dynamic_or_special_fee".to_string()));
        assert!(reasons.contains(&"non_zero_hook".to_string()));
    }

    fn temp_store() -> (NamedTempFile, Connection) {
        let store = NamedTempFile::new().expect("create temp store");
        let path = store.path().to_string_lossy().into_owned();
        let connection = Connection::open(path).expect("open temp store");
        (store, connection)
    }

    fn create_pool_tables(connection: &Connection) {
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_manager TEXT,
                    pool_key_json TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    dynamic_fee INTEGER,
                    special_fee INTEGER,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    depth_status TEXT,
                    liquidity_index REAL,
                    has_liquidity INTEGER,
                    evidence_source TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE dex_candidate_pools (
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
                    PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
                );
                CREATE TABLE dex_candidate_pool_recommendations (
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
                );
                CREATE TABLE v4_closed_loops (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    loop_key TEXT NOT NULL,
                    pool_ids_json TEXT,
                    PRIMARY KEY (network, chain_id, loop_key)
                );
                CREATE TABLE v4_closed_loop_hops (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    loop_key TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, loop_key, pool_id)
                );
                "#,
            )
            .expect("create test schema");
    }

    fn seed_raw_pool(
        connection: &Connection,
        pool_id: &str,
        token0: &str,
        token1: &str,
        depth_status: &str,
        has_liquidity: i64,
        fee: i64,
    ) {
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, token0, token1, fee,
                    dynamic_fee, special_fee, hooks
                 ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    "4663".to_string(),
                    "uniswap_v4".to_string(),
                    pool_id.to_string(),
                    token0.to_string(),
                    token1.to_string(),
                    fee.to_string(),
                    ZERO_ADDRESS.to_string(),
                ]),
            )
            .expect("seed raw pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, depth_status, liquidity_index, has_liquidity
                 ) VALUES (?, ?, ?, ?, ?, ?, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    "4663".to_string(),
                    "uniswap_v4".to_string(),
                    pool_id.to_string(),
                    depth_status.to_string(),
                    "1000.0".to_string(),
                    has_liquidity.to_string(),
                ]),
            )
            .expect("seed snapshot");
    }

    #[test]
    fn add_pair_accepts_positive_onchain_liquidity_without_usd_depth_labels() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(
            &connection,
            "deep-pool",
            &token_a,
            &token_b,
            "deep",
            1,
            3000,
        );
        seed_raw_pool(
            &connection,
            "thin-pool",
            &token_a,
            &token_b,
            "thin",
            1,
            3000,
        );

        let result =
            add_pair(&store, 4663, &token_a, &token_b, "TEST", 5, false).expect("add-pair dry run");
        assert_eq!(result["eligiblePoolIds"][0], "deep-pool");
        assert_eq!(result["eligiblePoolCount"], 2);

        let write =
            add_pair(&store, 4663, &token_a, &token_b, "TEST", 5, true).expect("add-pair apply");
        assert_eq!(write["inserted"], 2);
        let inserted = connection
            .query_row(
                "SELECT COUNT(*) FROM dex_candidate_pools WHERE anchor_symbol = 'TEST'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .expect("count candidates");
        assert_eq!(inserted, 2);
    }

    #[test]
    fn refresh_key_reports_hop_ready_and_does_not_write_closed_loops() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(
            &connection,
            "unpriced-live-pool",
            &token_a,
            &token_b,
            "unpriced",
            1,
            3000,
        );

        let dry = refresh_key(
            &store,
            4663,
            &format!("{token_a}:{token_b}"),
            "TEST",
            5,
            false,
        )
        .expect("refresh-key dry run");
        assert_eq!(dry["keyId"], canonical_pair_key(&token_a, &token_b));
        assert_eq!(dry["eligiblePoolIds"][0], "unpriced-live-pool");
        assert_eq!(dry["ready"], true);

        let applied = refresh_key(
            &store,
            4663,
            &format!("{token_a}:{token_b}"),
            "TEST",
            5,
            true,
        )
        .expect("refresh-key apply");
        assert_eq!(applied["inserted"], 1);
        let loops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loops", [], |row| row.get(0))
            .expect("count loops");
        let hops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loop_hops", [], |row| {
                row.get(0)
            })
            .expect("count hops");
        assert_eq!(loops, 0);
        assert_eq!(hops, 0);
    }

    #[test]
    fn refresh_pair_never_writes_closed_loop_tables() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(
            &connection,
            "deep-pool",
            &token_a,
            &token_b,
            "deep",
            1,
            3000,
        );
        seed_raw_pool(&connection, "old-pool", &token_a, &token_b, "thin", 0, 3000);
        let now = "2026-11-02T00:00:00Z";
        connection
            .execute(
                "INSERT INTO dex_candidate_pools (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    address_pair, direction_key, direction_address_pair, pool_id,
                    token0, token1, fee, dynamic_fee, special_fee, hooks,
                    depth_status, has_liquidity, liquidity_index, created_at, updated_at
                 ) VALUES (?, ?, 'uniswap_v4', 'v4', 'TEST', ?, ?, ?, 'old-pool',
                           ?, ?, 3000, 0, 0, ?, 'thin', 1, 1000.0, ?, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    "4663".to_string(),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    token_a.clone(),
                    token_b.clone(),
                    ZERO_ADDRESS.to_string(),
                    now.to_string(),
                    now.to_string(),
                ]),
            )
            .expect("insert old candidate");
        connection
            .execute(
                "INSERT INTO v4_closed_loops (network, chain_id, loop_key, pool_ids_json)
                 VALUES (?, 4663, 'loop-old', ?)",
                params_from_iter([network_for_chain_id(4663), "%old-pool%".to_string()]),
            )
            .expect("insert consumer loop");
        connection
            .execute(
                "INSERT INTO v4_closed_loop_hops (network, chain_id, loop_key, pool_id)
                 VALUES (?, 4663, 'loop-old', 'old-pool')",
                params_from_iter([network_for_chain_id(4663)]),
            )
            .expect("insert consumer hop");

        let dry = refresh_pair(&store, 4663, &token_a, &token_b, "TEST", 5, false)
            .expect("refresh dry run");
        assert_eq!(dry["existingPoolCount"], 1);
        assert_eq!(dry["stalePoolIds"][0], "old-pool");
        assert_eq!(dry["consumersNeedRebuild"], 1);

        let applied =
            refresh_pair(&store, 4663, &token_a, &token_b, "TEST", 5, true).expect("refresh apply");
        assert_eq!(applied["deletedRows"], 1);
        assert_eq!(applied["consumersNeedRebuild"], 1);
        let loops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loops", [], |row| row.get(0))
            .expect("count loops");
        let hops: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_closed_loop_hops", [], |row| {
                row.get(0)
            })
            .expect("count hops");
        assert_eq!(loops, 1);
        assert_eq!(hops, 1);
    }

    #[test]
    fn cleanup_output_does_not_claim_closed_loop_delete_count() {
        let token_a = format!("0x{}", "1".repeat(40));
        let token_b = format!("0x{}", "2".repeat(40));
        let (_store, connection) = temp_store();
        let store = _store.path().to_string_lossy().into_owned();
        create_pool_tables(&connection);
        seed_raw_pool(&connection, "bad-pool", &token_a, &token_b, "thin", 0, 3000);
        connection
            .execute(
                "INSERT INTO dex_candidate_pools (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    address_pair, direction_key, direction_address_pair, pool_id,
                    token0, token1, fee, dynamic_fee, special_fee, hooks,
                    depth_status, has_liquidity, liquidity_index, created_at, updated_at
                 ) VALUES (?, 4663, 'uniswap_v4', 'v4', 'TEST', ?, ?, ?, 'bad-pool',
                           ?, ?, 3000, 0, 0, ?, 'thin', 0, 0.0, ?, ?)",
                params_from_iter([
                    network_for_chain_id(4663),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    canonical_pair_key(&token_a, &token_b),
                    token_a,
                    token_b,
                    ZERO_ADDRESS.to_string(),
                    "2026-11-02T00:00:00Z".to_string(),
                    "2026-11-02T00:00:00Z".to_string(),
                ]),
            )
            .expect("insert bad candidate");

        let result = apply_cleanup(&store, 4663).expect("cleanup apply");
        assert!(result["cleanup"]["candidatePoolsDeleted"].as_i64().unwrap() >= 1);
        assert!(result["cleanup"].get("closedLoopsDeleted").is_none());
        assert!(result["cleanup"]["consumersNeedRebuild"].is_number());
    }
}
