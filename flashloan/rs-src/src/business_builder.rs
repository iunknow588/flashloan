use rusqlite::{params_from_iter, Connection, Row, Transaction};
use rusqlite::types::Value;
use serde_json::{json, Value as JsonValue};
use std::collections::{BTreeMap, BTreeSet};

const DYNAMIC_FEE_FLAG: i64 = 8_388_608;
const MAX_FIXED_FEE: i64 = 100_000;
const MAX_USABLE_CANDIDATE_FIXED_FEE: i64 = 3000;
const MAX_DIRECTION_CANDIDATE_POOLS: usize = 5;
const MAX_OBSERVATION_POOLS_PER_DIRECTION: usize = 3;
const MIN_OBSERVATION_POOL_DEPTH_USD: f64 = 1000.0;
const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

#[derive(Clone)]
struct PoolRow {
    pool_id: String,
    pool_identity_mode: Option<String>,
    pool_manager: Option<String>,
    pool_key_json: Option<String>,
    token0: Option<String>,
    token1: Option<String>,
    fee: Option<i64>,
    fee_label: Option<String>,
    dynamic_fee: Option<i64>,
    special_fee: Option<i64>,
    tick_spacing: Option<i64>,
    hooks: Option<String>,
    address_pair: Option<String>,
    sqrt_price_x96: Option<String>,
    tick: Option<i64>,
    has_liquidity: Option<i64>,
    liquidity: Option<String>,
    liquidity_index: Option<f64>,
    estimated_tvl_usd: Option<f64>,
    balances_json: Option<String>,
    depth_status: Option<String>,
    depth_band_json: Option<String>,
    depth_score_usd: Option<f64>,
    price: Option<f64>,
    liquidity_read_at: Option<String>,
    liquidity_block_number: Option<i64>,
    recent_swap_active: Option<i64>,
    observed_at: Option<String>,
    refreshed_at: Option<String>,
    evidence_source: Option<String>,
}

fn utc_now() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

fn norm(value: &Option<String>) -> String {
    value.as_deref().unwrap_or("").trim().to_lowercase()
}

fn norm_str(value: &str) -> String {
    value.trim().to_lowercase()
}

fn json_dumps(value: &JsonValue) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "null".to_string())
}

fn json_object(value: &Option<String>) -> JsonValue {
    match value.as_deref() {
        Some(raw) => serde_json::from_str(raw).unwrap_or(JsonValue::Object(Default::default())),
        None => JsonValue::Object(Default::default()),
    }
}

fn json_array(value: &Option<String>) -> JsonValue {
    match value.as_deref() {
        Some(raw) => serde_json::from_str(raw).unwrap_or(JsonValue::Array(Default::default())),
        None => JsonValue::Array(Default::default()),
    }
}

fn as_number(value: Option<f64>) -> Value {
    value.map(Value::from).unwrap_or(Value::Null)
}

fn as_int(value: Option<i64>) -> Value {
    value.map(Value::from).unwrap_or(Value::Null)
}

fn as_text(value: Option<String>) -> Value {
    value.map(Value::Text).unwrap_or(Value::Null)
}

fn as_text_or_empty(value: Option<String>) -> Value {
    Value::Text(value.unwrap_or_default())
}

fn as_tri(value: Option<i64>) -> Value {
    value.map(|item| Value::from(item != 0)).unwrap_or(Value::Null)
}

fn v_bool(value: bool) -> Value {
    Value::from(if value { 1 } else { 0 })
}

fn is_unresolved_placeholder_symbol(symbol: &str) -> bool {
    let normalized = symbol.trim().to_uppercase();
    if normalized.len() != 10 || !normalized.starts_with("0X") {
        return false;
    }
    normalized[2..].chars().all(|item| item.is_ascii_hexdigit())
}

fn is_fixed_fee(fee: Option<i64>, max_fixed_fee: i64) -> bool {
    match fee {
        Some(value) => {
            value > 0
                && value <= max_fixed_fee
                && value & DYNAMIC_FEE_FLAG == 0
        }
        None => false,
    }
}

fn has_no_hook(hooks: &Option<String>) -> bool {
    let normalized = norm(hooks);
    normalized.is_empty() || normalized == ZERO_ADDRESS
}

fn has_depth(pool: &PoolRow) -> bool {
    if let Some(has_liquidity) = pool.has_liquidity {
        return has_liquidity != 0 || pool.liquidity_index.unwrap_or(0.0) > 0.0;
    }
    pool.liquidity_index.unwrap_or(0.0) > 0.0
        || pool.estimated_tvl_usd.unwrap_or(0.0) > 0.0
}

fn pool_depth(pool: &PoolRow) -> f64 {
    let mut depth = pool.liquidity_index.unwrap_or(0.0);
    depth = depth.max(pool.estimated_tvl_usd.unwrap_or(0.0));
    depth = depth.max(pool.depth_score_usd.unwrap_or(0.0));
    depth
}

fn balances_tvl(balances_json: &Option<String>) -> f64 {
    match json_object(balances_json) {
        JsonValue::Object(map) => {
            let tvl = map
                .get("tvlUsd")
                .or_else(|| map.get("tvl_usd"))
                .and_then(JsonValue::as_f64)
                .unwrap_or(0.0);
            tvl
        }
        _ => 0.0,
    }
}

fn has_confirmed_liquidity(pool: &PoolRow) -> bool {
    let evidence_source = norm(&pool.evidence_source);
    let has_liquidity = pool.has_liquidity;
    if evidence_source == "pool_manager_extsload_live_state_batch"
        && has_liquidity == Some(1)
    {
        let raw_liquidity = pool
            .liquidity
            .as_deref()
            .and_then(|value| value.parse::<f64>().ok())
            .unwrap_or(0.0);
        if raw_liquidity > 0.0
            || pool.liquidity_index.unwrap_or(0.0) > 0.0
            || pool.estimated_tvl_usd.unwrap_or(0.0) > 0.0
        {
            return true;
        }
    }
    let depth_usd = pool
        .estimated_tvl_usd
        .unwrap_or(0.0)
        .max(pool.depth_score_usd.unwrap_or(0.0))
        .max(balances_tvl(&pool.balances_json));
    if depth_usd >= 1000.0 {
        return true;
    }
    let mut liquidity_index = pool.liquidity_index.unwrap_or(0.0);
    let raw_liquidity = pool
        .liquidity
        .as_deref()
        .and_then(|value| value.parse::<f64>().ok())
        .unwrap_or(0.0);
    if raw_liquidity > 0.0 {
        liquidity_index = liquidity_index.max(raw_liquidity / 10f64.powi(18));
    }
    liquidity_index >= 1.0
}

fn parse_sqrt_price(raw: &Option<String>) -> f64 {
    let value = norm(raw);
    if value.is_empty() {
        return 0.0;
    }
    let parsed = if let Ok(number) = value.parse::<u128>() {
        number
    } else {
        let hex_part = value.strip_prefix("0x").unwrap_or(&value);
        match u128::from_str_radix(hex_part, 16) {
            Ok(number) => number,
            Err(_) => return 0.0,
        }
    };
    if parsed == 0 {
        return 0.0;
    }
    (parsed as f64) / ((1u128 << 96) as f64)
}

fn fee_factor(fee: Option<i64>) -> f64 {
    let fee = fee.unwrap_or(0) as f64;
    (1.0 - fee / 1_000_000.0).max(0.0)
}

fn score_pool_by_direction(pool: &PoolRow, token_in: &str, token_out: &str) -> f64 {
    let token0 = norm(&pool.token0);
    let token1 = norm(&pool.token1);
    if token_in.is_empty()
        || token_out.is_empty()
        || {
            let mut expected = BTreeSet::new();
            expected.insert(token_in.to_string());
            expected.insert(token_out.to_string());
            let mut actual = BTreeSet::new();
            actual.insert(token0.clone());
            actual.insert(token1.clone());
            expected != actual
        }
    {
        return 0.0;
    }
    let sqrt_price = parse_sqrt_price(&pool.sqrt_price_x96);
    let fee_factor = fee_factor(pool.fee);
    if sqrt_price > 0.0 {
        let price = sqrt_price * sqrt_price;
        let mut rate = if token_in == token0 {
            price
        } else {
            1.0 / price
        };
        let decimals_out = 18;
        let decimals_in = 18;
        rate *= 10f64.powi(decimals_out - decimals_in);
        let raw_liquidity = pool
            .liquidity
            .as_deref()
            .and_then(|value| value.parse::<f64>().ok())
            .unwrap_or(0.0);
        if raw_liquidity > 0.0 {
            let capacity = if token0 == token_in {
                raw_liquidity / sqrt_price
            } else {
                raw_liquidity * sqrt_price
            };
            if capacity > 0.0 {
                rate = rate.min(capacity / 10f64.powi(decimals_in));
            }
        }
        return rate * fee_factor;
    }
    if let Some(reference_price) = pool.price {
        if reference_price > 0.0 {
            let price = if token_in == token1 {
                1.0 / reference_price
            } else {
                reference_price
            };
            return price * fee_factor;
        }
    }
    pool_depth(pool)
}

fn pool_id_from_row(pool: &PoolRow) -> String {
    norm(&Some(pool.pool_id.clone()))
}

fn sort_key_score(pool: &PoolRow, token_in: &str, token_out: &str) -> (i64, i64, String) {
    let score = score_pool_by_direction(pool, token_in, token_out);
    let score_rank = if score.is_nan() || score.is_infinite() {
        i64::MAX
    } else {
        ((score * 1e9) as i64).min(i64::MAX)
    };
    (-score_rank, pool.fee.unwrap_or(0), pool_id_from_row(pool))
}

fn select_recommendations(
    pools: &[PoolRow],
    token_in: &str,
    token_out: &str,
) -> Vec<PoolRow> {
    let mut confirmed: Vec<&PoolRow> = pools
        .iter()
        .filter(|pool| has_confirmed_liquidity(pool))
        .collect();
    confirmed.sort_by_key(|pool| sort_key_score(pool, token_in, token_out));
    let mut selected = Vec::new();
    let mut seen = BTreeSet::new();
    for pool in confirmed {
        let pool_id = pool_id_from_row(pool);
        if seen.insert(pool_id) {
            selected.push(pool.clone());
            if selected.len() >= MAX_DIRECTION_CANDIDATE_POOLS {
                break;
            }
        }
    }
    selected
}

fn representative_pool_rows(pools: &[PoolRow]) -> Vec<PoolRow> {
    let mut ordered: Vec<&PoolRow> = pools.iter().collect();
    ordered.sort_by_key(|pool| {
        let depth = pool_depth(pool);
        let depth_rank = if depth.is_nan() || depth.is_infinite() {
            i64::MAX
        } else {
            ((depth * 1e9) as i64).min(i64::MAX)
        };
        (-depth_rank, pool.fee.unwrap_or(0), pool_id_from_row(pool))
    });
    let mut selected = Vec::new();
    let mut seen = BTreeSet::new();
    for pool in ordered {
        if seen.insert(pool_id_from_row(pool)) {
            selected.push(pool.clone());
            if selected.len() >= MAX_DIRECTION_CANDIDATE_POOLS {
                break;
            }
        }
    }
    selected
}

fn load_token_symbols(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, String>, String> {
    let mut statement = connection
        .prepare(
            "SELECT token_address, token_symbol FROM chain_tokens
             WHERE network = ? AND chain_id = ?",
        )
        .map_err(|error| format!("chain_tokens prepare failed: {error}"))?;
    let rows = statement
        .query_map(params_from_iter([network, &chain_id.to_string()]), |row| {
            let address: Option<String> = row.get(0).ok();
            let symbol: Option<String> = row.get(1).ok();
            Ok((address, symbol))
        })
        .map_err(|error| format!("chain_tokens query failed: {error}"))?;
    let mut symbols = BTreeMap::new();
    for row in rows {
        let (address, symbol) = row.map_err(|error| format!("chain_tokens row failed: {error}"))?;
        let address = norm(&address);
        let symbol = symbol.unwrap_or_default().trim().to_uppercase();
        if address.is_empty() || symbol.is_empty() || is_unresolved_placeholder_symbol(&symbol) {
            continue;
        }
        symbols.insert(address, symbol);
    }
    Ok(symbols)
}

fn query_scope_pools(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    token_filter: Option<&[String]>,
) -> Result<Vec<PoolRow>, String> {
    let mut sql = String::from(
        "SELECT p.pool_id, p.pool_identity_mode, p.pool_manager, p.pool_key_json,
                p.token0, p.token1, p.fee, p.fee_label, p.dynamic_fee,
                p.special_fee, p.tick_spacing, p.hooks,
                l.has_liquidity, l.liquidity, l.liquidity_index,
                l.estimated_tvl_usd, l.balances_json, l.depth_status,
                l.depth_band_json, l.depth_score_usd, l.sqrt_price_x96,
                l.price, l.tick, l.liquidity_read_at, l.liquidity_block_number,
                l.recent_swap_active, l.observed_at, l.refreshed_at,
                l.evidence_source
         FROM v4_exchange_pools p
         LEFT JOIN v4_exchange_pool_liquidity_snapshots l
           ON l.network = p.network AND l.chain_id = p.chain_id
          AND l.protocol = p.protocol AND l.pool_id = p.pool_id
         WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?",
    );
    let mut params: Vec<rusqlite::types::Value> = vec![
        Value::from(network.to_string()),
        Value::from(chain_id),
        Value::from(protocol.to_string()),
    ];
    if let Some(tokens) = token_filter {
        if tokens.is_empty() {
            return Ok(Vec::new());
        }
        let placeholders = vec!["?"; tokens.len()].join(",");
        sql.push_str(&format!(
            " AND (p.token0 IN ({placeholders}) OR p.token1 IN ({placeholders}))"
        ));
        for token in tokens {
            params.push(Value::from(token.clone()));
        }
        for token in tokens {
            params.push(Value::from(token.clone()));
        }
    }
    sql.push_str(" ORDER BY p.pool_id");
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("v4_exchange_pools prepare failed: {error}"))?;
    let rows = statement
        .query_map(params_from_iter(params.iter()), map_pool_row)
        .map_err(|error| format!("v4_exchange_pools query failed: {error}"))?;
    let mut pools = Vec::new();
    for row in rows {
        pools.push(row.map_err(|error| format!("v4_exchange_pools row failed: {error}"))?);
    }
    Ok(pools)
}

fn map_pool_row(row: &Row<'_>) -> rusqlite::Result<PoolRow> {
    Ok(PoolRow {
        pool_id: row.get(0)?,
        pool_identity_mode: row.get(1)?,
        pool_manager: row.get(2)?,
        pool_key_json: row.get(3)?,
        token0: row.get(4)?,
        token1: row.get(5)?,
        fee: row.get(6)?,
        fee_label: row.get(7)?,
        dynamic_fee: row.get(8)?,
        special_fee: row.get(9)?,
        tick_spacing: row.get(10)?,
        hooks: row.get(11)?,
        has_liquidity: row.get(12)?,
        liquidity: row.get(13)?,
        liquidity_index: row.get(14)?,
        estimated_tvl_usd: row.get(15)?,
        balances_json: row.get(16)?,
        depth_status: row.get(17)?,
        depth_band_json: row.get(18)?,
        depth_score_usd: row.get(19)?,
        sqrt_price_x96: row.get(20)?,
        price: row.get(21)?,
        tick: row.get(22)?,
        liquidity_read_at: row.get(23)?,
        liquidity_block_number: row.get(24)?,
        recent_swap_active: row.get(25)?,
        observed_at: row.get(26)?,
        refreshed_at: row.get(27)?,
        evidence_source: row.get(28)?,
        address_pair: None,
    })
}

fn pools_by_edge(pools: &[PoolRow]) -> BTreeMap<String, Vec<PoolRow>> {
    let mut by_edge: BTreeMap<String, Vec<PoolRow>> = BTreeMap::new();
    for pool in pools {
        let token0 = norm(&pool.token0);
        let token1 = norm(&pool.token1);
        if token0.is_empty() || token1.is_empty() {
            continue;
        }
        by_edge
            .entry(format!("{token0}:{token1}"))
            .or_default()
            .push(pool.clone());
    }
    by_edge
}

fn pools_for_token_pair(
    pools_by_edge: &BTreeMap<String, Vec<PoolRow>>,
    token_x: &str,
    token_y: &str,
) -> Vec<PoolRow> {
    let x = norm_str(token_x);
    let y = norm_str(token_y);
    if x.is_empty() || y.is_empty() {
        return Vec::new();
    }
    pools_by_edge
        .get(&format!("{x}:{y}"))
        .or_else(|| pools_by_edge.get(&format!("{y}:{x}")))
        .cloned()
        .unwrap_or_default()
}

fn fee_label_value(fee: Option<i64>) -> Value {
    let dynamic_fee = matches!(fee, Some(value) if value & DYNAMIC_FEE_FLAG != 0);
    let special_fee = fee.is_none()
        || fee == Some(0)
        || dynamic_fee
        || fee.unwrap_or(0) > MAX_FIXED_FEE;
    if dynamic_fee {
        Value::Text("dynamic".to_string())
    } else if special_fee {
        Value::Text("special".to_string())
    } else {
        Value::from(fee.unwrap_or(0))
    }
}

fn pool_write_values(
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    pool: &PoolRow,
    canonical_pair: &str,
    is_first: bool,
    is_third: bool,
    now: &str,
) -> Vec<rusqlite::types::Value> {
    let pool_id = pool_id_from_row(pool);
    let pool_direction_key = norm(&pool.address_pair);
    let direction_key = if pool_direction_key.is_empty() {
        canonical_pair.to_string()
    } else {
        pool_direction_key
    };
    let token0 = norm(&pool.token0);
    let token1 = norm(&pool.token1);
    let canonical = if token0.is_empty() || token1.is_empty() {
        canonical_pair.to_string()
    } else {
        format!("{token0}:{token1}")
    };
    let dynamic_fee = matches!(pool.fee, Some(value) if value & DYNAMIC_FEE_FLAG != 0);
    let special_fee = pool.fee.is_none()
        || pool.fee == Some(0)
        || dynamic_fee
        || pool.fee.unwrap_or(0) > MAX_FIXED_FEE;
    vec![
        Value::from(network.to_string()),
        Value::from(chain_id),
        Value::from(protocol.to_string()),
        Value::from(anchor_scope.to_string()),
        Value::from(anchor_symbol.to_string()),
        Value::from(canonical.clone()),
        Value::from(direction_key.clone()),
        Value::from(direction_key),
        Value::from(pool_id),
        Value::Text(json_dumps(&json_object(&pool.pool_key_json))),
        Value::Text(norm(&pool.pool_manager)),
        Value::from(token0.clone()),
        Value::from(token1.clone()),
        as_int(pool.fee),
        fee_label_value(pool.fee),
        v_bool(dynamic_fee),
        v_bool(special_fee),
        as_int(pool.tick_spacing),
        Value::Text(norm(&pool.hooks)),
        Value::Text(norm(&pool.sqrt_price_x96)),
        as_int(pool.tick),
        as_tri(pool.has_liquidity),
        Value::Text(pool.liquidity.clone().unwrap_or_default()),
        as_number(pool.liquidity_index),
        as_number(pool.estimated_tvl_usd),
        Value::Text(json_dumps(&json_object(&pool.balances_json))),
        Value::Text(pool.depth_status.clone().unwrap_or_default()),
        Value::Text(json_dumps(&json_object(&pool.depth_band_json))),
        v_bool(true),
        v_bool(is_first),
        v_bool(is_third),
        Value::Text(json_dumps(&json!({"first": is_first, "third": is_third}))),
        Value::Text(json_dumps(&json!({"yc": is_first, "zc": is_third}))),
        as_text(pool.liquidity_read_at.clone()),
        as_int(pool.liquidity_block_number),
        Value::from(now.to_string()),
        Value::from(now.to_string()),
    ]
}

fn candidate_write_rows(
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    anchor_address: &str,
    token_addresses: &[String],
    pools_by_edge: &BTreeMap<String, Vec<PoolRow>>,
    now: &str,
) -> (
    Vec<Vec<rusqlite::types::Value>>,
    Vec<Vec<rusqlite::types::Value>>,
    Vec<Vec<rusqlite::types::Value>>,
    Vec<Vec<rusqlite::types::Value>>,
) {
    let mut token_rows = Vec::new();
    let mut pair_rows = Vec::new();
    let mut pool_rows = Vec::new();
    let mut recommendation_rows = Vec::new();
    let anchor = norm_str(anchor_address);
    for counter in token_addresses {
        let counter = norm_str(counter);
        if counter == anchor {
            continue;
        }
        let pools = pools_for_token_pair(pools_by_edge, &anchor, &counter);
        let fixed: Vec<PoolRow> = pools
            .iter()
            .filter(|pool| {
                is_fixed_fee(pool.fee, MAX_USABLE_CANDIDATE_FIXED_FEE)
                    && has_no_hook(&pool.hooks)
                    && has_confirmed_liquidity(pool)
            })
            .cloned()
            .collect();
        if fixed.is_empty() {
            continue;
        }
        let forward_key = format!("{anchor}:{counter}");
        let reverse_key = format!("{counter}:{anchor}");
        let forward_recommendations = select_recommendations(&fixed, &anchor, &counter);
        let reverse_recommendations = select_recommendations(&fixed, &counter, &anchor);
        let forward_pool_id = forward_recommendations
            .first()
            .map(pool_id_from_row)
            .unwrap_or_default();
        let reverse_pool_id = reverse_recommendations
            .first()
            .map(pool_id_from_row)
            .unwrap_or_default();
        let forward_backups: Vec<JsonValue> = forward_recommendations
            .iter()
            .skip(1)
            .map(pool_id_from_row)
            .map(JsonValue::from)
            .collect();
        let reverse_backups: Vec<JsonValue> = reverse_recommendations
            .iter()
            .skip(1)
            .map(pool_id_from_row)
            .map(JsonValue::from)
            .collect();
        token_rows.push(vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(protocol.to_string()),
            Value::from(anchor_scope.to_string()),
            Value::from(anchor_symbol.to_string()),
            Value::from("yc_zc".to_string()),
            Value::from(counter.clone()),
            Value::from(pools.len() as i64),
            v_bool(!forward_pool_id.is_empty()),
            v_bool(!reverse_pool_id.is_empty()),
            Value::from(forward_pool_id.clone()),
            Value::from(reverse_pool_id.clone()),
            Value::Text(json_dumps(&JsonValue::Array(forward_backups))),
            Value::Text(json_dumps(&JsonValue::Array(reverse_backups))),
            v_bool(forward_pool_id == reverse_pool_id),
            Value::from(now.to_string()),
            Value::from(now.to_string()),
        ]);
        for (direction_key, pair_anchor, pair_counter, yc_id, zc_id, yc_backups, zc_backups) in [
            (
                forward_key.clone(),
                anchor.clone(),
                counter.clone(),
                forward_pool_id.clone(),
                String::new(),
                forward_recommendations
                    .iter()
                    .skip(1)
                    .map(pool_id_from_row)
                    .collect::<Vec<_>>(),
                Vec::<String>::new(),
            ),
            (
                reverse_key.clone(),
                counter.clone(),
                anchor.clone(),
                String::new(),
                reverse_pool_id.clone(),
                Vec::<String>::new(),
                reverse_recommendations
                    .iter()
                    .skip(1)
                    .map(pool_id_from_row)
                    .collect::<Vec<_>>(),
            ),
        ] {
            pair_rows.push(vec![
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
                Value::from(anchor_scope.to_string()),
                Value::from(anchor_symbol.to_string()),
                Value::from(pair_anchor),
                Value::from(pair_counter),
                Value::from(direction_key.clone()),
                Value::from(direction_key),
                Value::from(pools.len() as i64),
                Value::from(fixed.len() as i64),
                Value::from(
                    pools.iter().filter(|pool| has_depth(pool)).count() as i64,
                ),
                Value::from(yc_id),
                Value::from(zc_id),
                Value::Text(json_dumps(&JsonValue::Object(Default::default()))),
                Value::Text(json_dumps(&JsonValue::Array(
                    yc_backups.into_iter().map(JsonValue::from).collect(),
                ))),
                Value::Text(json_dumps(&JsonValue::Array(
                    zc_backups.into_iter().map(JsonValue::from).collect(),
                ))),
                Value::from("fixed_fee_no_hook_low_fee".to_string()),
                Value::from(now.to_string()),
                Value::from(now.to_string()),
            ]);
        }
        let first_recommended_ids: BTreeSet<String> = forward_recommendations
            .iter()
            .map(pool_id_from_row)
            .collect();
        let third_recommended_ids: BTreeSet<String> = reverse_recommendations
            .iter()
            .map(pool_id_from_row)
            .collect();
        let first_best_pool_id = forward_pool_id.clone();
        let third_best_pool_id = reverse_pool_id.clone();
        for pool in &fixed {
            let pool_id = pool_id_from_row(pool);
            if pool_id.is_empty() {
                continue;
            }
            let token0 = norm(&pool.token0);
            let token1 = norm(&pool.token1);
            let canonical_pair = if token0.is_empty() || token1.is_empty() {
                forward_key.clone()
            } else {
                format!("{token0}:{token1}")
            };
            let is_first = first_recommended_ids.contains(&pool_id);
            let is_third = third_recommended_ids.contains(&pool_id);
            pool_rows.push(pool_write_values(
                network,
                chain_id,
                protocol,
                anchor_scope,
                anchor_symbol,
                pool,
                &canonical_pair,
                is_first,
                is_third,
                now,
            ));
            if is_first && pool_id == first_best_pool_id {
                let _ = &pool_id;
            }
            if is_third && pool_id == third_best_pool_id {
                let _ = &pool_id;
            }
        }
        for (hop, direction_key, in_token, out_token, rec_pools) in [
            ("first", forward_key, anchor.clone(), counter.clone(), &forward_recommendations),
            ("third", reverse_key, counter.clone(), anchor.clone(), &reverse_recommendations),
        ] {
            for (rank, rec_pool) in rec_pools.iter().enumerate() {
                let rec_pool_id = pool_id_from_row(rec_pool);
                recommendation_rows.push(vec![
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(anchor_scope.to_string()),
                    Value::from(anchor_symbol.to_string()),
                    Value::from(if hop == "first" { counter.clone() } else { anchor.clone() }),
                    Value::from(direction_key.clone()),
                    Value::from(hop.to_string()),
                    Value::from((rank + 1) as i64),
                    Value::from(rec_pool_id),
                    as_number(Some(pool_depth(rec_pool))),
                    as_number(rec_pool.price),
                    as_int(rec_pool.fee),
                    Value::from(format!(
                        "direction_static_score_{hop}_hop:{in_token}:{out_token}"
                    )),
                    Value::from(now.to_string()),
                    Value::from(now.to_string()),
                ]);
            }
        }
    }
    (token_rows, pair_rows, pool_rows, recommendation_rows)
}

fn candidate_abnormal_write_rows(
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    anchor_address: &str,
    token_addresses: &[String],
    pools_by_edge: &BTreeMap<String, Vec<PoolRow>>,
    now: &str,
) -> (
    Vec<Vec<rusqlite::types::Value>>,
    Vec<Vec<rusqlite::types::Value>>,
    Vec<Vec<rusqlite::types::Value>>,
) {
    let mut token_rows = Vec::new();
    let mut pair_rows = Vec::new();
    let mut pool_rows = Vec::new();
    let anchor = norm_str(anchor_address);
    for counter in token_addresses {
        let counter = norm_str(counter);
        if counter == anchor {
            continue;
        }
        let pools = pools_for_token_pair(pools_by_edge, &anchor, &counter);
        if pools.is_empty() {
            continue;
        }
        token_rows.push(vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(protocol.to_string()),
            Value::from(anchor_scope.to_string()),
            Value::from(anchor_symbol.to_string()),
            Value::from("yc_zc_abnormal".to_string()),
            Value::from(counter.clone()),
            Value::from(pools.len() as i64),
            v_bool(false),
            v_bool(false),
            Value::Text(String::new()),
            Value::Text(String::new()),
            v_bool(true),
            Value::from(now.to_string()),
            Value::from(now.to_string()),
        ]);
        for direction_key in [format!("{anchor}:{counter}"), format!("{counter}:{anchor}")] {
            let (pair_anchor, pair_counter) = if direction_key.starts_with(&anchor) {
                (anchor.clone(), counter.clone())
            } else {
                (counter.clone(), anchor.clone())
            };
            pair_rows.push(vec![
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
                Value::from(anchor_scope.to_string()),
                Value::from(anchor_symbol.to_string()),
                Value::from(pair_anchor),
                Value::from(pair_counter),
                Value::from(direction_key.clone()),
                Value::from(direction_key),
                Value::from(pools.len() as i64),
                Value::from(0),
                Value::from(pools.iter().filter(|pool| has_depth(pool)).count() as i64),
                Value::Text(String::new()),
                Value::Text(String::new()),
                Value::Text(json_dumps(&JsonValue::Object(Default::default()))),
                Value::from("abnormal_pool".to_string()),
                Value::from(now.to_string()),
                Value::from(now.to_string()),
            ]);
        }
        for pool in &pools {
            let pool_id = pool_id_from_row(pool);
            if pool_id.is_empty() {
                continue;
            }
            let token0 = norm(&pool.token0);
            let token1 = norm(&pool.token1);
            let canonical_pair = if token0.is_empty() || token1.is_empty() {
                format!("{anchor}:{counter}")
            } else {
                format!("{token0}:{token1}")
            };
            pool_rows.push(pool_write_values(
                network,
                chain_id,
                protocol,
                anchor_scope,
                anchor_symbol,
                pool,
                &canonical_pair,
                false,
                false,
                now,
            ));
        }
    }
    (token_rows, pair_rows, pool_rows)
}

fn delete_candidate_anchor(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_symbol: &str,
) -> Result<(), String> {
    for table in [
        "dex_candidate_pool_recommendations",
        "dex_candidate_pools",
        "dex_candidate_pool_pairs",
        "dex_candidate_anchor_tokens",
        "dex_candidate_abnormal_pools",
        "dex_candidate_abnormal_pool_pairs",
        "dex_candidate_abnormal_anchor_tokens",
    ] {
        let sql = format!(
            "DELETE FROM {table}
             WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?"
        );
        transaction
            .execute(
                &sql,
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(anchor_symbol.to_string()),
                ]),
            )
            .map_err(|error| format!("{table} delete failed: {error}"))?;
    }
    Ok(())
}

fn replace_candidate_tables(
    transaction: &Transaction<'_>,
    token_rows: &[Vec<rusqlite::types::Value>],
    pair_rows: &[Vec<rusqlite::types::Value>],
    pool_rows: &[Vec<rusqlite::types::Value>],
    recommendation_rows: &[Vec<rusqlite::types::Value>],
) -> Result<(), String> {
    execute_many(
        transaction,
        "INSERT INTO dex_candidate_anchor_tokens (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            token_role, token_address, pool_count, has_yc_optimal_pool,
            has_zc_optimal_pool, yc_optimal_pool_id, zc_optimal_pool_id,
            yc_backup_pool_ids_json, zc_backup_pool_ids_json,
            yc_zc_same_set, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, token_address)
        DO UPDATE SET pool_count = excluded.pool_count,
            has_yc_optimal_pool = excluded.has_yc_optimal_pool,
            has_zc_optimal_pool = excluded.has_zc_optimal_pool,
            yc_optimal_pool_id = excluded.yc_optimal_pool_id,
            zc_optimal_pool_id = excluded.zc_optimal_pool_id,
            yc_backup_pool_ids_json = excluded.yc_backup_pool_ids_json,
            zc_backup_pool_ids_json = excluded.zc_backup_pool_ids_json,
            yc_zc_same_set = excluded.yc_zc_same_set,
            updated_at = excluded.updated_at",
        token_rows,
    )?;
    execute_many(
        transaction,
        "INSERT INTO dex_candidate_pool_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            anchor_address, counter_address, address_pair, direction_key, pool_count,
            fixed_fee_liquid_pool_count, priced_pool_count,
            yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
            yc_backup_pool_ids_json, zc_backup_pool_ids_json,
            price_outlier_filter, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
        DO UPDATE SET pool_count = excluded.pool_count,
            fixed_fee_liquid_pool_count = excluded.fixed_fee_liquid_pool_count,
            priced_pool_count = excluded.priced_pool_count,
            yc_optimal_pool_id = excluded.yc_optimal_pool_id,
            zc_optimal_pool_id = excluded.zc_optimal_pool_id,
            yc_backup_pool_ids_json = excluded.yc_backup_pool_ids_json,
            zc_backup_pool_ids_json = excluded.zc_backup_pool_ids_json,
            price_reference_json = excluded.price_reference_json,
            price_outlier_filter = excluded.price_outlier_filter,
            updated_at = excluded.updated_at",
        pair_rows,
    )?;
    execute_many(
        transaction,
        "INSERT INTO dex_candidate_pools (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            address_pair, direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0,
            token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
            hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
            liquidity_index, estimated_tvl_usd, balances_json, depth_status,
            depth_band_json, usable_for_observation, is_first_hop_recommended,
            is_third_hop_recommended, hop_recommendation_json,
            optimal_roles_json, liquidity_read_at, liquidity_block_number,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
        DO UPDATE SET pool_key_json = excluded.pool_key_json,
            pool_manager = excluded.pool_manager,
            token0 = excluded.token0,
            token1 = excluded.token1,
            fee = excluded.fee,
            fee_label = excluded.fee_label,
            dynamic_fee = excluded.dynamic_fee,
            special_fee = excluded.special_fee,
            tick_spacing = excluded.tick_spacing,
            hooks = excluded.hooks,
            sqrt_price_x96 = excluded.sqrt_price_x96,
            tick = excluded.tick,
            has_liquidity = excluded.has_liquidity,
            liquidity = excluded.liquidity,
            liquidity_index = excluded.liquidity_index,
            estimated_tvl_usd = excluded.estimated_tvl_usd,
            balances_json = excluded.balances_json,
            depth_status = excluded.depth_status,
            depth_band_json = excluded.depth_band_json,
            usable_for_observation = excluded.usable_for_observation,
            is_first_hop_recommended = excluded.is_first_hop_recommended,
            is_third_hop_recommended = excluded.is_third_hop_recommended,
            hop_recommendation_json = excluded.hop_recommendation_json,
            optimal_roles_json = excluded.optimal_roles_json,
            liquidity_read_at = excluded.liquidity_read_at,
            liquidity_block_number = excluded.liquidity_block_number,
            updated_at = excluded.updated_at",
        pool_rows,
    )?;
    execute_many(
        transaction,
        "INSERT INTO dex_candidate_pool_recommendations (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            counter_address, direction_key, hop, rank, pool_id, depth_score, price, fee,
            selection_basis, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, direction_key, hop, pool_id)
        DO UPDATE SET rank = excluded.rank,
            depth_score = excluded.depth_score,
            price = excluded.price,
            fee = excluded.fee,
            selection_basis = excluded.selection_basis,
            updated_at = excluded.updated_at",
        recommendation_rows,
    )?;
    Ok(())
}

fn replace_candidate_abnormal_tables(
    transaction: &Transaction<'_>,
    token_rows: &[Vec<rusqlite::types::Value>],
    pair_rows: &[Vec<rusqlite::types::Value>],
    pool_rows: &[Vec<rusqlite::types::Value>],
) -> Result<(), String> {
    execute_many(
        transaction,
        "INSERT INTO dex_candidate_abnormal_anchor_tokens (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            token_role, token_address, pool_count, has_yc_optimal_pool,
            has_zc_optimal_pool, yc_optimal_pool_id, zc_optimal_pool_id,
            yc_zc_same_set, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, token_address)
        DO UPDATE SET pool_count = excluded.pool_count,
            has_yc_optimal_pool = excluded.has_yc_optimal_pool,
            has_zc_optimal_pool = excluded.has_zc_optimal_pool,
            yc_optimal_pool_id = excluded.yc_optimal_pool_id,
            zc_optimal_pool_id = excluded.zc_optimal_pool_id,
            yc_zc_same_set = excluded.yc_zc_same_set,
            updated_at = excluded.updated_at",
        token_rows,
    )?;
    execute_many(
        transaction,
        "INSERT INTO dex_candidate_abnormal_pool_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            anchor_address, counter_address, address_pair, direction_key, pool_count,
            fixed_fee_liquid_pool_count, priced_pool_count,
            yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
            price_outlier_filter, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
        DO UPDATE SET pool_count = excluded.pool_count,
            fixed_fee_liquid_pool_count = excluded.fixed_fee_liquid_pool_count,
            priced_pool_count = excluded.priced_pool_count,
            yc_optimal_pool_id = excluded.yc_optimal_pool_id,
            zc_optimal_pool_id = excluded.zc_optimal_pool_id,
            price_reference_json = excluded.price_reference_json,
            price_outlier_filter = excluded.price_outlier_filter,
            updated_at = excluded.updated_at",
        pair_rows,
    )?;
    execute_many(
        transaction,
        "INSERT INTO dex_candidate_abnormal_pools (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            address_pair, direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0,
            token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
            hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
            liquidity_index, estimated_tvl_usd, balances_json, depth_status,
            depth_band_json, usable_for_observation, is_first_hop_recommended,
            is_third_hop_recommended, hop_recommendation_json,
            optimal_roles_json, liquidity_read_at, liquidity_block_number,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
        DO UPDATE SET pool_key_json = excluded.pool_key_json,
            pool_manager = excluded.pool_manager,
            token0 = excluded.token0,
            token1 = excluded.token1,
            fee = excluded.fee,
            fee_label = excluded.fee_label,
            dynamic_fee = excluded.dynamic_fee,
            special_fee = excluded.special_fee,
            tick_spacing = excluded.tick_spacing,
            hooks = excluded.hooks,
            sqrt_price_x96 = excluded.sqrt_price_x96,
            tick = excluded.tick,
            has_liquidity = excluded.has_liquidity,
            liquidity = excluded.liquidity,
            liquidity_index = excluded.liquidity_index,
            estimated_tvl_usd = excluded.estimated_tvl_usd,
            balances_json = excluded.balances_json,
            depth_status = excluded.depth_status,
            depth_band_json = excluded.depth_band_json,
            usable_for_observation = excluded.usable_for_observation,
            is_first_hop_recommended = excluded.is_first_hop_recommended,
            is_third_hop_recommended = excluded.is_third_hop_recommended,
            hop_recommendation_json = excluded.hop_recommendation_json,
            optimal_roles_json = excluded.optimal_roles_json,
            liquidity_read_at = excluded.liquidity_read_at,
            liquidity_block_number = excluded.liquidity_block_number,
            updated_at = excluded.updated_at",
        pool_rows,
    )?;
    Ok(())
}

fn execute_many(
    transaction: &Transaction<'_>,
    sql: &str,
    rows: &[Vec<rusqlite::types::Value>],
) -> Result<(), String> {
    for row in rows {
        transaction
            .execute(sql, params_from_iter(row.iter()))
            .map_err(|error| format!("SQLite executemany failed: {error}"))?;
    }
    Ok(())
}

fn delete_market_observation_anchor(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_symbol: &str,
) -> Result<(), String> {
    for table in [
        "market_observation_yz_pairs",
        "market_observation_abnormal_yz_pairs",
        "market_observation_intermediate_pools",
        "market_observation_abnormal_intermediate_pools",
        "market_observation_liquidity_snapshots",
    ] {
        let sql = format!(
            "DELETE FROM {table}
             WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?"
        );
        transaction
            .execute(
                &sql,
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(anchor_symbol.to_string()),
                ]),
            )
            .map_err(|error| format!("{table} delete failed: {error}"))?;
    }
    Ok(())
}

fn observation_pool_ids(pools: &[PoolRow], token_x: &str, token_y: &str) -> Vec<String> {
    let mut scored: Vec<&PoolRow> = pools
        .iter()
        .filter(|pool| !pool_id_from_row(pool).is_empty())
        .collect();
    scored.sort_by_key(|pool| {
        let score = score_pool_by_direction(pool, token_x, token_y);
        let score_rank = if score.is_nan() || score.is_infinite() {
            i64::MAX
        } else {
            ((score * 1e9) as i64).min(i64::MAX)
        };
        (-score_rank, pool_id_from_row(pool))
    });
    let mut selected = Vec::new();
    let mut seen = BTreeSet::new();
    for pool in scored {
        if !has_confirmed_liquidity(pool) {
            continue;
        }
        let pool_id = pool_id_from_row(pool);
        if seen.insert(pool_id.clone()) {
            selected.push(pool_id);
            if selected.len() >= MAX_OBSERVATION_POOLS_PER_DIRECTION {
                break;
            }
        }
    }
    selected
}

fn observation_pair_rows(
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    directions: &[ObservationDirection],
    abnormal: bool,
    now: &str,
) -> Vec<Vec<rusqlite::types::Value>> {
    let has_flag = if abnormal {
        "has_non_normal_swap_pool"
    } else {
        "has_direct_swap_pool"
    };
    let count_flag = if abnormal {
        "non_normal_swap_pool_count"
    } else {
        "direct_swap_pool_count"
    };
    let _ = (has_flag, count_flag);
    let mut rows = Vec::new();
    for direction in directions {
        let pool_ids = if abnormal {
            direction
                .all_pools
                .iter()
                .map(pool_id_from_row)
                .collect::<Vec<_>>()
        } else {
            observation_pool_ids(&direction.all_pools, &direction.token_x, &direction.token_y)
        };
        let optimal_pool_id = pool_ids.first().cloned().unwrap_or_default();
        let empty_reason = if pool_ids.is_empty() {
            if !direction.empty_reason.is_empty() {
                direction.empty_reason.clone()
            } else if direction.all_pools.is_empty() {
                "no_reachable_v4_pool".to_string()
            } else {
                "no_confirmed_liquidity".to_string()
            }
        } else {
            String::new()
        };
        rows.push(vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(protocol.to_string()),
            Value::from(anchor_scope.to_string()),
            Value::from(anchor_symbol.to_string()),
            Value::from(format!(
                "{}:{}",
                direction.token_x, direction.token_y
            )),
            Value::from(direction.token_x.clone()),
            Value::from(direction.token_y.clone()),
            Value::from(format!("{}:{}", direction.token_x, direction.token_y)),
            v_bool(!pool_ids.is_empty()),
            Value::from(pool_ids.len() as i64),
            Value::Text(json_dumps(&JsonValue::Array(
                pool_ids.into_iter().map(JsonValue::from).collect(),
            ))),
            Value::from(optimal_pool_id),
            Value::from(empty_reason),
            Value::from(now.to_string()),
            Value::from(now.to_string()),
        ]);
    }
    rows
}

struct ObservationDirection {
    token_x: String,
    token_y: String,
    all_pools: Vec<PoolRow>,
    empty_reason: String,
}

fn observation_pool_write_row(
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    pool: &PoolRow,
    address_pair: &str,
    now: &str,
) -> Vec<rusqlite::types::Value> {
    let token0 = norm(&pool.token0);
    let token1 = norm(&pool.token1);
    let address_pair = if address_pair.is_empty() {
        format!("{token0}:{token1}")
    } else {
        address_pair.to_string()
    };
    vec![
        Value::from(network.to_string()),
        Value::from(chain_id),
        Value::from(protocol.to_string()),
        Value::from(anchor_scope.to_string()),
        Value::from(anchor_symbol.to_string()),
        Value::from(pool_id_from_row(pool)),
        {
            let mode = norm(&pool.pool_identity_mode);
            Value::from(if mode.is_empty() {
                "v4_pool_id".to_string()
            } else {
                mode
            })
        },
        Value::Text(norm(&pool.pool_manager)),
        Value::Text(json_dumps(&json_object(&pool.pool_key_json))),
        Value::from(token0.clone()),
        Value::from(token1.clone()),
        as_int(pool.fee),
        fee_label_value(pool.fee),
        v_bool(matches!(pool.fee, Some(value) if value & DYNAMIC_FEE_FLAG != 0)),
        v_bool(
            pool.fee.is_none()
                || pool.fee == Some(0)
                || matches!(pool.fee, Some(value) if value & DYNAMIC_FEE_FLAG != 0)
                || pool.fee.unwrap_or(0) > MAX_FIXED_FEE
        ),
        as_int(pool.tick_spacing),
        Value::Text(norm(&pool.hooks)),
        Value::from(address_pair),
        Value::from(now.to_string()),
        Value::from(now.to_string()),
    ]
}

fn observation_liquidity_write_row(
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    pool: &PoolRow,
    now: &str,
) -> Vec<rusqlite::types::Value> {
    vec![
        Value::from(network.to_string()),
        Value::from(chain_id),
        Value::from(protocol.to_string()),
        Value::from(anchor_scope.to_string()),
        Value::from(anchor_symbol.to_string()),
        Value::from(pool_id_from_row(pool)),
        as_tri(pool.has_liquidity),
        Value::Text(pool.liquidity.clone().unwrap_or_default()),
        as_number(pool.liquidity_index),
        as_number(pool.estimated_tvl_usd),
        Value::Text(json_dumps(&json_object(&pool.balances_json))),
        Value::Text(pool.depth_status.clone().unwrap_or_default()),
        Value::Text(json_dumps(&json_object(&pool.depth_band_json))),
        Value::Text(norm(&pool.sqrt_price_x96)),
        as_int(pool.tick),
        as_text(pool.liquidity_read_at.clone()),
        as_int(pool.liquidity_block_number),
        as_tri(pool.recent_swap_active),
        Value::from(
            pool.observed_at
                .clone()
                .unwrap_or_else(|| now.to_string()),
        ),
        Value::from(
            pool.refreshed_at
                .clone()
                .unwrap_or_else(|| now.to_string()),
        ),
        Value::from(norm(&pool.evidence_source)),
    ]
}

fn replace_market_observation_tables(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    directions: &[ObservationDirection],
    pools: &[PoolRow],
    liquidity: &[PoolRow],
    abnormal: bool,
) -> Result<(), String> {
    let now = utc_now();
    let pair_rows = observation_pair_rows(
        network,
        chain_id,
        protocol,
        anchor_scope,
        anchor_symbol,
        directions,
        abnormal,
        &now,
    );
    let pair_table = if abnormal {
        "market_observation_abnormal_yz_pairs"
    } else {
        "market_observation_yz_pairs"
    };
    let has_flag = if abnormal {
        "has_non_normal_swap_pool"
    } else {
        "has_direct_swap_pool"
    };
    let count_flag = if abnormal {
        "non_normal_swap_pool_count"
    } else {
        "direct_swap_pool_count"
    };
    execute_many(
        transaction,
        &format!(
            "INSERT INTO {pair_table} (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                direction_key, token_x_address, token_y_address, address_pair,
                {has_flag}, {count_flag}, pool_ids_json,
                optimal_pool_id, observation_empty_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
            DO UPDATE SET token_x_address = excluded.token_x_address,
                token_y_address = excluded.token_y_address,
                address_pair = excluded.address_pair,
                {has_flag} = excluded.{has_flag},
                {count_flag} = excluded.{count_flag},
                pool_ids_json = excluded.pool_ids_json,
                optimal_pool_id = excluded.optimal_pool_id,
                observation_empty_reason = excluded.observation_empty_reason,
                updated_at = excluded.updated_at"
        ),
        &pair_rows,
    )?;
    let mut pool_rows = Vec::new();
    for pool in pools {
        pool_rows.push(observation_pool_write_row(
            network,
            chain_id,
            protocol,
            anchor_scope,
            anchor_symbol,
            pool,
            "",
            &now,
        ));
    }
    let pool_table = if abnormal {
        "market_observation_abnormal_intermediate_pools"
    } else {
        "market_observation_intermediate_pools"
    };
    execute_many(
        transaction,
        &format!(
            "INSERT INTO {pool_table} (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id, pool_identity_mode, pool_manager, pool_key_json,
                token0, token1, fee, fee_label, dynamic_fee, special_fee,
                tick_spacing, hooks, address_pair, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            DO UPDATE SET pool_identity_mode = excluded.pool_identity_mode,
                pool_manager = excluded.pool_manager,
                pool_key_json = excluded.pool_key_json,
                token0 = excluded.token0,
                token1 = excluded.token1,
                fee = excluded.fee,
                fee_label = excluded.fee_label,
                dynamic_fee = excluded.dynamic_fee,
                special_fee = excluded.special_fee,
                tick_spacing = excluded.tick_spacing,
                hooks = excluded.hooks,
                address_pair = excluded.address_pair,
                updated_at = excluded.updated_at"
        ),
        &pool_rows,
    )?;
    if !abnormal {
        let mut liquidity_rows = Vec::new();
        for pool in liquidity {
            liquidity_rows.push(observation_liquidity_write_row(
                network,
                chain_id,
                protocol,
                anchor_scope,
                anchor_symbol,
                pool,
                &now,
            ));
        }
        execute_many(
            transaction,
            "INSERT INTO market_observation_liquidity_snapshots (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                pool_id, has_liquidity, liquidity, liquidity_index,
                estimated_tvl_usd, balances_json, depth_status, depth_band_json,
                sqrt_price_x96, tick, liquidity_read_at, liquidity_block_number,
                recent_swap_active, observed_at, refreshed_at, evidence_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            DO UPDATE SET has_liquidity = excluded.has_liquidity,
                liquidity = excluded.liquidity,
                liquidity_index = excluded.liquidity_index,
                estimated_tvl_usd = excluded.estimated_tvl_usd,
                balances_json = excluded.balances_json,
                depth_status = excluded.depth_status,
                depth_band_json = excluded.depth_band_json,
                sqrt_price_x96 = excluded.sqrt_price_x96,
                tick = excluded.tick,
                liquidity_read_at = excluded.liquidity_read_at,
                liquidity_block_number = excluded.liquidity_block_number,
                recent_swap_active = excluded.recent_swap_active,
                observed_at = excluded.observed_at,
                refreshed_at = excluded.refreshed_at,
                evidence_source = excluded.evidence_source",
            &liquidity_rows,
        )?;
    }
    Ok(())
}

pub(crate) fn build_candidate_anchor(
    connection: &mut Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    anchor_address: &str,
    token_addresses: Option<&[String]>,
) -> Result<JsonValue, String> {
    let anchor = norm_str(anchor_address);
    if anchor.is_empty() {
        return Err(format!("anchor symbol {anchor_symbol} has no chain token address"));
    }
    let pools = query_scope_pools(
        connection,
        network,
        chain_id,
        protocol,
        Some(&[anchor.clone()]),
    )?;
    let normal_pools: Vec<PoolRow> = pools
        .iter()
        .filter(|pool| {
            is_fixed_fee(pool.fee, MAX_USABLE_CANDIDATE_FIXED_FEE)
                && has_no_hook(&pool.hooks)
        })
        .cloned()
        .collect();
    let abnormal_pools: Vec<PoolRow> = pools
        .iter()
        .filter(|pool| {
            !(is_fixed_fee(pool.fee, MAX_USABLE_CANDIDATE_FIXED_FEE)
                && has_no_hook(&pool.hooks))
        })
        .cloned()
        .collect();
    let pools_by_edge = pools_by_edge(&normal_pools);
    let mut abnormal_pools_by_edge = crate::business_builder::pools_by_edge(&abnormal_pools);
    for edge_pools in abnormal_pools_by_edge.values_mut() {
        *edge_pools = representative_pool_rows(edge_pools);
    }
    let resolved_tokens = match token_addresses {
        Some(values) => {
            let mut tokens = values
                .iter()
                .map(|item| norm_str(item))
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>();
            let normalized_len = values
                .iter()
                .map(|item| norm_str(item))
                .collect::<BTreeSet<_>>()
                .len();
            if values.len() != normalized_len {
                return Err("duplicate token addresses in YC/ZC set".to_string());
            }
            tokens.retain(|address| {
                let edge = if anchor.as_str() <= address.as_str() {
                    format!("{anchor}:{address}")
                } else {
                    format!("{address}:{anchor}")
                };
                pools_by_edge.contains_key(&edge)
            });
            tokens
        }
        None => {
            let mut token_set = BTreeSet::new();
            for (edge, _) in &pools_by_edge {
                let parts: Vec<&str> = edge.split(':').collect();
                if parts.len() != 2 {
                    continue;
                }
                if parts[0] == anchor {
                    token_set.insert(parts[1].to_string());
                } else if parts[1] == anchor {
                    token_set.insert(parts[0].to_string());
                }
            }
            token_set.into_iter().collect()
        }
    };
    let (token_rows, pair_rows, pool_rows, recommendation_rows) = candidate_write_rows(
        network,
        chain_id,
        protocol,
        anchor_scope,
        anchor_symbol,
        &anchor,
        &resolved_tokens,
        &pools_by_edge,
        &utc_now(),
    );
    let mut abnormal_token_set = BTreeSet::new();
    for (edge, _) in &abnormal_pools_by_edge {
        let parts: Vec<&str> = edge.split(':').collect();
        if parts.len() == 2 {
            if parts[0] == anchor {
                abnormal_token_set.insert(parts[1].to_string());
            } else if parts[1] == anchor {
                abnormal_token_set.insert(parts[0].to_string());
            }
        }
    }
    let abnormal_token_addresses = abnormal_token_set.into_iter().collect::<Vec<_>>();
    let (abnormal_token_rows, abnormal_pair_rows, abnormal_pool_rows) =
        candidate_abnormal_write_rows(
            network,
            chain_id,
            protocol,
            anchor_scope,
            anchor_symbol,
            &anchor,
            &abnormal_token_addresses,
            &abnormal_pools_by_edge,
            &utc_now(),
        );
    {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("candidate transaction failed: {error}"))?;
        delete_candidate_anchor(
            &transaction,
            network,
            chain_id,
            protocol,
            anchor_symbol,
        )?;
        replace_candidate_tables(
            &transaction,
            &token_rows,
            &pair_rows,
            &pool_rows,
            &recommendation_rows,
        )?;
        replace_candidate_abnormal_tables(
            &transaction,
            &abnormal_token_rows,
            &abnormal_pair_rows,
            &abnormal_pool_rows,
        )?;
        transaction
            .commit()
            .map_err(|error| format!("candidate transaction commit failed: {error}"))?;
    }
    Ok(json!({
        "anchorSymbol": anchor_symbol,
        "anchorAddress": anchor,
        "tokenCount": token_rows.len(),
        "pairCount": pair_rows.len(),
        "poolCount": pool_rows.len(),
        "recommendationCount": recommendation_rows.len(),
        "counts": {
            "tokens": token_rows.len(),
            "pairs": pair_rows.len(),
            "pools": pool_rows.len(),
            "recommendations": recommendation_rows.len(),
        },
        "abnormalTokenCount": abnormal_token_rows.len(),
        "abnormalPairCount": abnormal_pair_rows.len(),
        "abnormalPoolCount": abnormal_pool_rows.len(),
        "abnormalCounts": {
            "tokens": abnormal_token_rows.len(),
            "pairs": abnormal_pair_rows.len(),
            "pools": abnormal_pool_rows.len(),
        },
    }))
}

pub(crate) fn build_market_observation_anchor(
    connection: &mut Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    anchor_symbol: &str,
    token_addresses: Option<&[String]>,
) -> Result<JsonValue, String> {
    let resolved_tokens = match token_addresses {
        Some(values) => {
            let mut tokens = values
                .iter()
                .map(|item| norm_str(item))
                .collect::<Vec<_>>();
            tokens.sort();
            tokens.dedup();
            tokens
        }
        None => {
            let mut statement = connection
                .prepare(
                    "SELECT token_address FROM dex_candidate_anchor_tokens
                     WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
                     ORDER BY token_address",
                )
                .map_err(|error| format!("candidate anchor tokens prepare failed: {error}"))?;
            let rows = statement
                .query_map(
                    params_from_iter([
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                        Value::from(anchor_symbol.to_string()),
                    ]),
                    |row| row.get::<_, Option<String>>(0),
                )
                .map_err(|error| format!("candidate anchor tokens query failed: {error}"))?;
            let mut tokens = Vec::new();
            for row in rows {
                let address = row
                    .map_err(|error| format!("candidate anchor tokens row failed: {error}"))?;
                let normalized = norm(&address);
                if !normalized.is_empty() {
                    tokens.push(normalized);
                }
            }
            tokens
        }
    };
    if resolved_tokens.is_empty() {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("observation delete transaction failed: {error}"))?;
        delete_market_observation_anchor(
            &transaction,
            network,
            chain_id,
            protocol,
            anchor_symbol,
        )?;
        transaction
            .commit()
            .map_err(|error| format!("observation delete commit failed: {error}"))?;
        return Ok(json!({
            "anchorSymbol": anchor_symbol,
            "tokenCount": 0,
            "directionCount": 0,
            "noPoolDirectionCount": 0,
            "poolCount": 0,
            "counts": {},
            "abnormalDirectionCount": 0,
            "abnormalPoolCount": 0,
            "abnormalCounts": {},
        }));
    }
    let pools = query_scope_pools(
        connection,
        network,
        chain_id,
        protocol,
        Some(&resolved_tokens),
    )?;
    let token_set: BTreeSet<String> = resolved_tokens.iter().cloned().collect();
    let mut normal_by_edge: BTreeMap<String, Vec<PoolRow>> = BTreeMap::new();
    let mut abnormal_by_edge: BTreeMap<String, Vec<PoolRow>> = BTreeMap::new();
    for pool in pools {
        let token0 = norm(&pool.token0);
        let token1 = norm(&pool.token1);
        if !token_set.contains(&token0) || !token_set.contains(&token1) {
            continue;
        }
        let is_normal = is_fixed_fee(pool.fee, MAX_USABLE_CANDIDATE_FIXED_FEE)
            && has_no_hook(&pool.hooks);
        let edge = format!("{token0}:{token1}");
        if is_normal {
            normal_by_edge.entry(edge).or_default().push(pool);
        } else {
            abnormal_by_edge.entry(edge).or_default().push(pool);
        }
    }
    let mut directions = Vec::new();
    let mut abnormal_directions = Vec::new();
    let mut pool_rows = Vec::new();
    let mut abnormal_pool_rows = Vec::new();
    let mut seen_pool_ids = BTreeSet::new();
    let mut seen_abnormal_pool_ids = BTreeSet::new();
    for token_x in &resolved_tokens {
        for token_y in &resolved_tokens {
            if token_x == token_y {
                continue;
            }
            let matched = pools_for_token_pair(&normal_by_edge, token_x, token_y);
            let confirmed: Vec<PoolRow> = matched
                .iter()
                .filter(|pool| has_confirmed_liquidity(pool))
                .cloned()
                .collect();
            directions.push(ObservationDirection {
                token_x: token_x.clone(),
                token_y: token_y.clone(),
                all_pools: confirmed.clone(),
                empty_reason: if confirmed.is_empty() {
                    "no_reachable_v4_pool".to_string()
                } else {
                    String::new()
                },
            });
            for pool in confirmed {
                let pool_id = pool_id_from_row(&pool);
                if seen_pool_ids.insert(pool_id) {
                    pool_rows.push(pool);
                }
            }
            let abnormal_matched =
                pools_for_token_pair(&abnormal_by_edge, token_x, token_y);
            if !abnormal_matched.is_empty() {
                abnormal_directions.push(ObservationDirection {
                    token_x: token_x.clone(),
                    token_y: token_y.clone(),
                    all_pools: abnormal_matched.clone(),
                    empty_reason: String::new(),
                });
            }
            for pool in abnormal_matched {
                let pool_id = pool_id_from_row(&pool);
                if seen_abnormal_pool_ids.insert(pool_id) {
                    abnormal_pool_rows.push(pool);
                }
            }
        }
    }
    let mut liquidity_rows = pool_rows.clone();
    liquidity_rows.extend(abnormal_pool_rows.iter().cloned());
    {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("observation transaction failed: {error}"))?;
        delete_market_observation_anchor(
            &transaction,
            network,
            chain_id,
            protocol,
            anchor_symbol,
        )?;
        replace_market_observation_tables(
            &transaction,
            network,
            chain_id,
            protocol,
            anchor_scope,
            anchor_symbol,
            &directions,
            &pool_rows,
            &liquidity_rows,
            false,
        )?;
        replace_market_observation_tables(
            &transaction,
            network,
            chain_id,
            protocol,
            anchor_scope,
            anchor_symbol,
            &abnormal_directions,
            &abnormal_pool_rows,
            &[],
            true,
        )?;
        transaction
            .commit()
            .map_err(|error| format!("observation transaction commit failed: {error}"))?;
    }
    Ok(json!({
        "anchorSymbol": anchor_symbol,
        "tokenCount": resolved_tokens.len(),
        "directionCount": directions.len(),
        "noPoolDirectionCount": directions
            .iter()
            .filter(|direction| direction.all_pools.is_empty())
            .count(),
        "poolCount": pool_rows.len(),
        "counts": {
            "pairs": directions.len(),
            "pools": pool_rows.len(),
            "liquidity": liquidity_rows.len(),
        },
        "abnormalDirectionCount": abnormal_directions.len(),
        "abnormalPoolCount": abnormal_pool_rows.len(),
        "abnormalCounts": {
            "pairs": abnormal_directions.len(),
            "pools": abnormal_pool_rows.len(),
        },
    }))
}

#[derive(Clone)]
struct RecommendationRow {
    direction_key: String,
    hop: String,
    pool_id: String,
    selection_basis: Option<String>,
}

struct ObservationPairRow {
    direction_key: String,
    optimal_pool_id: String,
    pool_ids: Vec<String>,
}

fn loop_rows(
    network: &str,
    chain_id: i64,
    protocol: &str,
    market_id: &str,
    anchor_address: &str,
    anchor_symbol: &str,
    token_addresses: &[String],
    recommendations: &[RecommendationRow],
    observation_pairs: &[ObservationPairRow],
    observation_pool_ids: &BTreeSet<String>,
    token_symbols: &BTreeMap<String, String>,
) -> (
    Vec<Vec<rusqlite::types::Value>>,
    Vec<Vec<rusqlite::types::Value>>,
) {
    let anchor = norm_str(anchor_address);
    let mut first_by_direction: BTreeMap<String, Vec<RecommendationRow>> = BTreeMap::new();
    let mut third_by_direction: BTreeMap<String, Vec<RecommendationRow>> = BTreeMap::new();
    for rec in recommendations {
        if rec.hop == "first" {
            first_by_direction
                .entry(rec.direction_key.clone())
                .or_default()
                .push(rec.clone());
        } else if rec.hop == "third" {
            third_by_direction
                .entry(rec.direction_key.clone())
                .or_default()
                .push(rec.clone());
        }
    }
    let mut optimal_pool_by_direction = BTreeMap::new();
    for pair in observation_pairs {
        let optimal = if pair.optimal_pool_id.is_empty() {
            pair.pool_ids.first().cloned().unwrap_or_default()
        } else {
            pair.optimal_pool_id.clone()
        };
        optimal_pool_by_direction.insert(pair.direction_key.clone(), optimal);
    }
    let now = utc_now();
    let mut loops = Vec::new();
    let mut hops = Vec::new();
    let mut seen_loop_keys = BTreeSet::new();
    for y_address in token_addresses {
        let y_symbol = token_symbols
            .get(y_address)
            .cloned()
            .unwrap_or_default();
        if y_address == &anchor
            || y_symbol.is_empty()
            || is_unresolved_placeholder_symbol(&y_symbol)
        {
            continue;
        }
        for z_address in token_addresses {
            let z_symbol = token_symbols
                .get(z_address)
                .cloned()
                .unwrap_or_default();
            if z_address == &anchor
                || z_address == y_address
                || z_symbol.is_empty()
                || is_unresolved_placeholder_symbol(&z_symbol)
            {
                continue;
            }
            let first_options = first_by_direction
                .get(&format!("{anchor}:{y_address}"))
                .cloned()
                .unwrap_or_default();
            let third_options = third_by_direction
                .get(&format!("{z_address}:{anchor}"))
                .cloned()
                .unwrap_or_default();
            if first_options.is_empty() || third_options.is_empty() {
                continue;
            }
            let middle_key = format!("{y_address}:{z_address}");
            let middle_pool_id = optimal_pool_by_direction
                .get(&middle_key)
                .cloned()
                .unwrap_or_default();
            if middle_pool_id.is_empty() || !observation_pool_ids.contains(&middle_pool_id) {
                continue;
            }
            for first in first_options.iter().take(1) {
                for third in third_options.iter().take(1) {
                    let loop_key_short =
                        format!("{anchor}:{y_address}:{z_address}");
                    if !seen_loop_keys.insert(loop_key_short.clone()) {
                        continue;
                    }
                    let pool_path = vec![
                        first.pool_id.clone(),
                        middle_pool_id.clone(),
                        third.pool_id.clone(),
                    ];
                    let token_path = vec![
                        anchor.clone(),
                        y_address.clone(),
                        z_address.clone(),
                        anchor.clone(),
                    ];
                    let path_symbols = vec![
                        anchor_symbol.to_string(),
                        y_symbol.clone(),
                        z_symbol.clone(),
                        anchor_symbol.to_string(),
                    ];
                    let mut pool_path_key = token_path.join(":");
                    for pool_id in &pool_path {
                        pool_path_key.push(':');
                        pool_path_key.push_str(pool_id);
                    }
                    loops.push(vec![
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                        Value::from(market_id.to_string()),
                        Value::from(loop_key_short.clone()),
                        Value::from(anchor.clone()),
                        Value::from(anchor_symbol.to_string()),
                        Value::from(path_symbols.join(" -> ")),
                        Value::Text(json_dumps(&JsonValue::Array(
                            token_path
                                .iter()
                                .map(|item| JsonValue::from(item.clone()))
                                .collect(),
                        ))),
                        Value::Text(json_dumps(&JsonValue::Array(
                            path_symbols
                                .iter()
                                .map(|item| JsonValue::from(item.clone()))
                                .collect(),
                        ))),
                        Value::from(pool_path_key),
                        Value::Text(json_dumps(&JsonValue::Array(
                            pool_path
                                .iter()
                                .map(|item| JsonValue::from(item.clone()))
                                .collect(),
                        ))),
                        Value::Text(json_dumps(&json!({
                            "1": first_options.len(),
                            "2": 1,
                            "3": third_options.len(),
                        }))),
                        Value::from((first_options.len() * third_options.len()) as i64),
                        Value::from("directed_fixed_fee_no_hook_static_score".to_string()),
                        Value::from(1),
                        Value::from("active".to_string()),
                        Value::from("database_builder".to_string()),
                        Value::from(now.clone()),
                        Value::from(now.clone()),
                    ]);
                    for (hop_index, input_address, output_address, pool_id, basis) in [
                        (
                            1,
                            anchor.clone(),
                            y_address.clone(),
                            first.pool_id.clone(),
                            first
                                .selection_basis
                                .clone()
                                .unwrap_or_else(|| "first_hop_recommended".to_string()),
                        ),
                        (
                            2,
                            y_address.clone(),
                            z_address.clone(),
                            middle_pool_id.clone(),
                            format!("observation_direction_optimal_pool:{y_address}:{z_address}"),
                        ),
                        (
                            3,
                            z_address.clone(),
                            anchor.clone(),
                            third.pool_id.clone(),
                            third
                                .selection_basis
                                .clone()
                                .unwrap_or_else(|| "third_hop_recommended".to_string()),
                        ),
                    ] {
                        let hop_name = match hop_index {
                            1 => "yc",
                            2 => "y-z",
                            _ => "zc",
                        };
                        hops.push(vec![
                            Value::from(network.to_string()),
                            Value::from(chain_id),
                            Value::from(protocol.to_string()),
                            Value::from(market_id.to_string()),
                            Value::from(loop_key_short.clone()),
                            Value::from(hop_index),
                            Value::from(hop_name.to_string()),
                            Value::from(input_address),
                            Value::from(output_address),
                            Value::from(pool_id.clone()),
                            Value::from(pool_id),
                            Value::from(basis),
                            Value::Null,
                            Value::from(0),
                            Value::from(now.clone()),
                            Value::from(now.clone()),
                        ]);
                    }
                }
            }
        }
    }
    (loops, hops)
}

fn delete_closed_loops_anchor(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    market_id: &str,
    anchor_symbol: &str,
) -> Result<(), String> {
    for table in ["v4_closed_loop_optimizations", "v4_closed_loop_hops"] {
        let sql = format!(
            "DELETE FROM {table}
             WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
               AND loop_key IN (
                   SELECT loop_key FROM v4_closed_loops
                   WHERE network = ? AND chain_id = ? AND protocol = ?
                     AND market_id = ? AND anchor_symbol = ?
               )"
        );
        transaction
            .execute(
                &sql,
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(market_id.to_string()),
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(market_id.to_string()),
                    Value::from(anchor_symbol.to_string()),
                ]),
            )
            .map_err(|error| format!("{table} delete failed: {error}"))?;
    }
    let sql = "DELETE FROM v4_closed_loops
               WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
                 AND anchor_symbol = ?";
    transaction
        .execute(
            sql,
            params_from_iter([
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
                Value::from(market_id.to_string()),
                Value::from(anchor_symbol.to_string()),
            ]),
        )
        .map_err(|error| format!("v4_closed_loops delete failed: {error}"))?;
    Ok(())
}

fn replace_v4_closed_loop_tables(
    transaction: &Transaction<'_>,
    loops: &[Vec<rusqlite::types::Value>],
    hops: &[Vec<rusqlite::types::Value>],
) -> Result<(), String> {
    execute_many(
        transaction,
        "INSERT INTO v4_closed_loops (
            network, chain_id, protocol, market_id, loop_key,
            anchor_address, anchor_symbol, path_text, path_addresses_json,
            path_symbols_json, pool_path_key, pool_ids_json,
            hop_candidate_counts_json, hop_candidate_combination_count,
            recommendation_basis, recommended_first_priority, status,
            source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, market_id, loop_key)
        DO UPDATE SET anchor_address = excluded.anchor_address,
            anchor_symbol = excluded.anchor_symbol,
            path_text = excluded.path_text,
            path_addresses_json = excluded.path_addresses_json,
            path_symbols_json = excluded.path_symbols_json,
            pool_path_key = excluded.pool_path_key,
            pool_ids_json = excluded.pool_ids_json,
            hop_candidate_counts_json = excluded.hop_candidate_counts_json,
            hop_candidate_combination_count = excluded.hop_candidate_combination_count,
            recommendation_basis = excluded.recommendation_basis,
            recommended_first_priority = excluded.recommended_first_priority,
            status = excluded.status,
            source = excluded.source,
            updated_at = excluded.updated_at",
        loops,
    )?;
    execute_many(
        transaction,
        "INSERT INTO v4_closed_loop_hops (
            network, chain_id, protocol, market_id, loop_key, hop_index,
            hop_name, input_address, output_address, pool_id, base_pool_id,
            pool_selection_basis, optimization_id, temporary_override,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, market_id, loop_key, hop_index)
        DO UPDATE SET hop_name = excluded.hop_name,
            input_address = excluded.input_address,
            output_address = excluded.output_address,
            pool_id = excluded.pool_id,
            base_pool_id = excluded.base_pool_id,
            pool_selection_basis = excluded.pool_selection_basis,
            optimization_id = excluded.optimization_id,
            temporary_override = excluded.temporary_override,
            updated_at = excluded.updated_at",
        hops,
    )?;
    Ok(())
}

pub(crate) fn build_closed_loops_anchor(
    connection: &mut Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    market_id: &str,
    anchor_symbol: &str,
    anchor_address: &str,
) -> Result<JsonValue, String> {
    let mut recommendations = Vec::new();
    {
        let mut statement = connection
            .prepare(
                "SELECT counter_address, direction_key, hop, rank, pool_id,
                        depth_score, price, fee, selection_basis
                 FROM dex_candidate_pool_recommendations
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
                 ORDER BY counter_address, hop, rank",
            )
            .map_err(|error| format!("candidate recommendations prepare failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(anchor_symbol.to_string()),
                ]),
                |row| {
                    let direction_key: Option<String> = row.get(1).ok();
                    let hop: Option<String> = row.get(2).ok();
                    let pool_id: Option<String> = row.get(4).ok();
                    let selection_basis: Option<String> = row.get(8).ok();
                    Ok(RecommendationRow {
                        direction_key: norm(&direction_key),
                        hop: norm(&hop),
                        pool_id: norm(&pool_id),
                        selection_basis,
                    })
                },
            )
            .map_err(|error| format!("candidate recommendations query failed: {error}"))?;
        for row in rows {
            recommendations.push(
                row.map_err(|error| format!("candidate recommendations row failed: {error}"))?,
            );
        }
    }
    let mut observation_pairs = Vec::new();
    {
        let mut statement = connection
            .prepare(
                "SELECT direction_key, token_x_address, token_y_address,
                        optimal_pool_id, pool_ids_json
                 FROM market_observation_yz_pairs
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
                 ORDER BY direction_key",
            )
            .map_err(|error| format!("observation pairs prepare failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(anchor_symbol.to_string()),
                ]),
                |row| {
                    let direction_key: Option<String> = row.get(0).ok();
                    let optimal_pool_id: Option<String> = row.get(3).ok();
                    let pool_ids_json: Option<String> = row.get(4).ok();
                    let pool_ids = match json_array(&pool_ids_json) {
                        JsonValue::Array(items) => items
                            .iter()
                            .filter_map(|item| item.as_str())
                            .map(|item| norm_str(item))
                            .collect(),
                        _ => Vec::new(),
                    };
                    Ok(ObservationPairRow {
                        direction_key: norm(&direction_key),
                        optimal_pool_id: norm(&optimal_pool_id),
                        pool_ids,
                    })
                },
            )
            .map_err(|error| format!("observation pairs query failed: {error}"))?;
        for row in rows {
            observation_pairs.push(
                row.map_err(|error| format!("observation pairs row failed: {error}"))?,
            );
        }
    }
    let mut observation_pool_ids = BTreeSet::new();
    {
        let mut statement = connection
            .prepare(
                "SELECT pool_id FROM market_observation_intermediate_pools
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
                 ORDER BY pool_id",
            )
            .map_err(|error| format!("observation pools prepare failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(anchor_symbol.to_string()),
                ]),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("observation pools query failed: {error}"))?;
        for row in rows {
            let pool_id = norm(
                &row.map_err(|error| format!("observation pools row failed: {error}"))?,
            );
            if !pool_id.is_empty() {
                observation_pool_ids.insert(pool_id);
            }
        }
    }
    let mut token_addresses = Vec::new();
    {
        let mut statement = connection
            .prepare(
                "SELECT token_address FROM dex_candidate_anchor_tokens
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
                 ORDER BY token_address",
            )
            .map_err(|error| format!("candidate tokens prepare failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                    Value::from(anchor_symbol.to_string()),
                ]),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("candidate tokens query failed: {error}"))?;
        for row in rows {
            let address = norm(
                &row.map_err(|error| format!("candidate tokens row failed: {error}"))?,
            );
            if !address.is_empty() {
                token_addresses.push(address);
            }
        }
    }
    if token_addresses.is_empty() {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("closed loop delete transaction failed: {error}"))?;
        delete_closed_loops_anchor(
            &transaction,
            network,
            chain_id,
            protocol,
            market_id,
            anchor_symbol,
        )?;
        transaction
            .commit()
            .map_err(|error| format!("closed loop delete commit failed: {error}"))?;
        return Ok(json!({
            "anchorSymbol": anchor_symbol,
            "anchorAddress": "",
            "loopCount": 0,
            "hopCount": 0,
            "counts": {},
        }));
    }
    let token_symbols = load_token_symbols(connection, network, chain_id)?;
    let anchor = norm_str(anchor_address);
    let (loops, hops) = loop_rows(
        network,
        chain_id,
        protocol,
        market_id,
        &anchor,
        anchor_symbol,
        &token_addresses,
        &recommendations,
        &observation_pairs,
        &observation_pool_ids,
        &token_symbols,
    );
    {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("closed loop transaction failed: {error}"))?;
        delete_closed_loops_anchor(
            &transaction,
            network,
            chain_id,
            protocol,
            market_id,
            anchor_symbol,
        )?;
        replace_v4_closed_loop_tables(
            &transaction,
            &loops,
            &hops,
        )?;
        transaction
            .commit()
            .map_err(|error| format!("closed loop transaction commit failed: {error}"))?;
    }
    Ok(json!({
        "anchorSymbol": anchor_symbol,
        "anchorAddress": anchor,
        "loopCount": loops.len(),
        "hopCount": hops.len(),
        "counts": {
            "loops": loops.len(),
            "hops": hops.len(),
        },
    }))
}

fn delete_all_business_rows(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
) -> Result<(), String> {
    let tables = [
        "dex_candidate_anchor_tokens",
        "dex_candidate_pool_pairs",
        "dex_candidate_pools",
        "dex_candidate_pool_recommendations",
        "dex_candidate_abnormal_anchor_tokens",
        "dex_candidate_abnormal_pool_pairs",
        "dex_candidate_abnormal_pools",
        "market_observation_yz_pairs",
        "market_observation_abnormal_yz_pairs",
        "market_observation_intermediate_pools",
        "market_observation_abnormal_intermediate_pools",
        "market_observation_liquidity_snapshots",
        "v4_closed_loop_optimizations",
        "v4_closed_loop_hops",
        "v4_closed_loops",
    ];
    for table in tables {
        let sql = format!(
            "DELETE FROM {table} WHERE network = ? AND chain_id = ? AND protocol = ?"
        );
        connection
            .execute(
                &sql,
                params_from_iter([
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ]),
            )
            .map_err(|error| format!("{table} scope delete failed: {error}"))?;
    }
    Ok(())
}

fn load_candidate_token_addresses(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_symbol: &str,
) -> Result<Vec<String>, String> {
    let mut statement = connection
        .prepare(
            "SELECT token_address FROM dex_candidate_anchor_tokens
             WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
             ORDER BY token_address",
        )
        .map_err(|error| format!("candidate tokens prepare failed: {error}"))?;
    let rows = statement
        .query_map(
            params_from_iter([
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
                Value::from(anchor_symbol.to_string()),
            ]),
            |row| row.get::<_, Option<String>>(0),
        )
        .map_err(|error| format!("candidate tokens query failed: {error}"))?;
    let mut addresses = Vec::new();
    for row in rows {
        let address = norm(
            &row.map_err(|error| format!("candidate tokens row failed: {error}"))?,
        );
        if !address.is_empty() {
            addresses.push(address);
        }
    }
    Ok(addresses)
}

pub(crate) fn build_all_anchor_data(
    connection: &mut Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    anchor_scope: &str,
    market_id: &str,
    anchors: &[String],
    anchor_addresses: &BTreeMap<String, String>,
    token_universe: Option<&[String]>,
) -> Result<JsonValue, String> {
    delete_all_business_rows(connection, network, chain_id, protocol)?;
    let universe = match token_universe {
        Some(values) => {
            let mut tokens = values
                .iter()
                .map(|item| norm_str(item))
                .collect::<Vec<_>>();
            tokens.sort();
            tokens.dedup();
            Some(tokens)
        }
        None => None,
    };
    let mut reports = Vec::new();
    for anchor_symbol in anchors {
        let anchor_symbol_upper = anchor_symbol.trim().to_uppercase();
        let anchor_address = anchor_addresses
            .get(&anchor_symbol_upper)
            .cloned()
            .unwrap_or_default();
        let candidate = build_candidate_anchor(
            connection,
            network,
            chain_id,
            protocol,
            anchor_scope,
            &anchor_symbol_upper,
            &anchor_address,
            universe.as_deref(),
        )?;
        let observation_tokens = load_candidate_token_addresses(
            connection,
            network,
            chain_id,
            protocol,
            &anchor_symbol_upper,
        )?;
        let observation = build_market_observation_anchor(
            connection,
            network,
            chain_id,
            protocol,
            anchor_scope,
            &anchor_symbol_upper,
            Some(&observation_tokens),
        )?;
        let loop_report = build_closed_loops_anchor(
            connection,
            network,
            chain_id,
            protocol,
            market_id,
            &anchor_symbol_upper,
            &anchor_address,
        )?;
        reports.push(json!({
            "anchorSymbol": anchor_symbol_upper,
            "candidate": candidate,
            "observation": observation,
            "loop": loop_report,
        }));
    }
    Ok(json!({
        "ok": true,
        "network": network,
        "chainId": chain_id,
        "protocol": protocol,
        "anchors": reports,
        "generatedAt": utc_now(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unresolved_placeholder_rule_is_generic() {
        assert!(is_unresolved_placeholder_symbol("0X102154E7"));
        assert!(is_unresolved_placeholder_symbol("0x60042b65"));
        assert!(!is_unresolved_placeholder_symbol("TZ"));
        assert!(!is_unresolved_placeholder_symbol("ETH"));
        assert!(!is_unresolved_placeholder_symbol("0XABCDEF12F"));
    }

    #[test]
    fn fixed_fee_rule_excludes_dynamic_and_high_fee() {
        assert!(is_fixed_fee(Some(3000), MAX_USABLE_CANDIDATE_FIXED_FEE));
        assert!(is_fixed_fee(Some(100), MAX_USABLE_CANDIDATE_FIXED_FEE));
        assert!(!is_fixed_fee(Some(3001), MAX_USABLE_CANDIDATE_FIXED_FEE));
        assert!(!is_fixed_fee(Some(8_388_608 | 3000), MAX_USABLE_CANDIDATE_FIXED_FEE));
        assert!(!is_fixed_fee(None, MAX_USABLE_CANDIDATE_FIXED_FEE));
    }
}
