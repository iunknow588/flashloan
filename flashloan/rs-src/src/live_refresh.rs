use rusqlite::{params, Connection};
use serde_json::{json, Value};

const V4_LIVE_REFRESH_DOMAIN: &str = "v4_live_refresh";
const V4_LIVE_REFRESH_PROTOCOL: &str = "uniswap_v4";

fn utc_now() -> String {
    chrono::Utc::now().to_rfc3339().replace("+00:00", "Z")
}

fn completeness_int(value: &Value, key: &str, default: i64) -> i64 {
    value
        .get(key)
        .and_then(Value::as_i64)
        .unwrap_or(default)
}

fn completeness_string(value: &Value, key: &str, default: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_string()
}

fn load_initialization_row(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    domain: &str,
) -> Result<Option<Value>, String> {
    let mut statement = connection
        .prepare(
            "SELECT status, completeness_json, error_json, refreshed_at
             FROM chain_business_initialization
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND domain = ?4",
        )
        .map_err(|error| format!("could not query V4 scan state: {error}"))?;
    let mut rows = statement
        .query_map(
            params![network, chain_id, protocol, domain],
            |row| {
                Ok(json!({
                    "status": row.get::<_, String>(0)?,
                    "completeness_json": row.get::<_, Option<String>>(1)?,
                    "error_json": row.get::<_, Option<String>>(2)?,
                    "refreshed_at": row.get::<_, Option<String>>(3)?,
                }))
            },
        )
        .map_err(|error| format!("could not query V4 scan state: {error}"))?;
    let row = rows.next().transpose().map_err(|error| format!("could not read V4 scan state: {error}"))?;
    Ok(row)
}

pub fn load_v4_scan_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
) -> Result<Value, String> {
    let row = load_initialization_row(
        connection,
        network,
        chain_id,
        protocol,
        V4_LIVE_REFRESH_DOMAIN,
    )?;
    let mut completeness = json!({});
    let refreshed_at = if let Some(row) = row {
        if let Some(raw) = row.get("completeness_json").and_then(Value::as_str) {
            if let Ok(parsed) = serde_json::from_str::<Value>(raw) {
                if parsed.is_object() {
                    completeness = parsed;
                }
            }
        }
        row.get("refreshed_at")
            .and_then(Value::as_str)
            .map(str::to_string)
    } else {
        None
    };
    Ok(json!({
        "last_scanned_block": completeness_int(&completeness, "last_scanned_block", 0),
        "index_from_block": completeness_int(&completeness, "index_from_block", 0),
        "index_to_block": completeness_int(&completeness, "index_to_block", 0),
        "liquidity_scan_block": completeness_int(&completeness, "liquidity_scan_block", 0),
        "scanned_pool_count": completeness_int(&completeness, "scanned_pool_count", 0),
        "pool_count": completeness_int(&completeness, "pool_count", 0),
        "confirmed_liquidity_pool_count": completeness_int(
            &completeness,
            "confirmed_liquidity_pool_count",
            0,
        ),
        "explicit_zero_liquidity_pool_count": completeness_int(
            &completeness,
            "explicit_zero_liquidity_pool_count",
            0,
        ),
        "unscanned_pool_count": completeness_int(&completeness, "unscanned_pool_count", 0),
        "direction_candidate_limit": completeness_int(&completeness, "direction_candidate_limit", 5),
        "scan_mode": completeness_string(&completeness, "scan_mode", "full"),
        "refreshed_at": refreshed_at,
    }))
}

#[allow(clippy::too_many_arguments)]
pub fn update_v4_scan_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    last_scanned_block: Option<i64>,
    index_from_block: Option<i64>,
    index_to_block: Option<i64>,
    liquidity_scan_block: Option<i64>,
    scanned_pool_count: Option<i64>,
    pool_count: Option<i64>,
    confirmed_liquidity_pool_count: Option<i64>,
    explicit_zero_liquidity_pool_count: Option<i64>,
    unscanned_pool_count: Option<i64>,
    direction_candidate_limit: i64,
    scan_mode: &str,
) -> Result<Value, String> {
    let previous = load_v4_scan_state(connection, network, chain_id, protocol)?;
    let mut completeness = json!({});
    for key in [
        "last_scanned_block",
        "index_from_block",
        "index_to_block",
        "liquidity_scan_block",
        "scanned_pool_count",
        "pool_count",
        "confirmed_liquidity_pool_count",
        "explicit_zero_liquidity_pool_count",
        "unscanned_pool_count",
    ] {
        if let Some(value) = previous.get(key) {
            completeness[key] = value.clone();
        }
    }
    if let Some(value) = previous.get("direction_candidate_limit") {
        completeness["direction_candidate_limit"] = value.clone();
    }
    if let Some(value) = previous.get("scan_mode") {
        completeness["scan_mode"] = value.clone();
    }
    if let Some(value) = last_scanned_block {
        completeness["last_scanned_block"] = json!(value);
    }
    if let Some(value) = index_from_block {
        completeness["index_from_block"] = json!(value);
    }
    if let Some(value) = index_to_block {
        completeness["index_to_block"] = json!(value);
    }
    if let Some(value) = liquidity_scan_block {
        completeness["liquidity_scan_block"] = json!(value);
    }
    if let Some(value) = scanned_pool_count {
        completeness["scanned_pool_count"] = json!(value);
    }
    if let Some(value) = pool_count {
        completeness["pool_count"] = json!(value);
    }
    if let Some(value) = confirmed_liquidity_pool_count {
        completeness["confirmed_liquidity_pool_count"] = json!(value);
    }
    if let Some(value) = explicit_zero_liquidity_pool_count {
        completeness["explicit_zero_liquidity_pool_count"] = json!(value);
    }
    if let Some(value) = unscanned_pool_count {
        completeness["unscanned_pool_count"] = json!(value);
    }
    completeness["direction_candidate_limit"] = json!(direction_candidate_limit.max(1));
    completeness["scan_mode"] = json!(scan_mode);

    let completeness_json = serde_json::to_string(&completeness)
        .map_err(|error| format!("could not encode V4 scan state: {error}"))?;
    let now = utc_now();
    connection
        .execute(
            "INSERT INTO chain_business_initialization (
                network, chain_id, protocol, domain, status,
                completeness_json, error_json, initialized_at, refreshed_at
             ) VALUES (?1, ?2, ?3, ?4, 'ok', ?5, NULL, ?6, ?6)
             ON CONFLICT(network, chain_id, protocol, domain) DO UPDATE SET
                status = excluded.status,
                completeness_json = excluded.completeness_json,
                error_json = excluded.error_json,
                initialized_at = COALESCE(
                    chain_business_initialization.initialized_at,
                    excluded.initialized_at
                ),
                refreshed_at = excluded.refreshed_at",
            params![
                network,
                chain_id,
                protocol,
                V4_LIVE_REFRESH_DOMAIN,
                completeness_json,
                now
            ],
        )
        .map_err(|error| format!("could not upsert V4 scan state: {error}"))?;
    let mut payload = completeness;
    payload["refreshed_at"] = json!(now);
    Ok(payload)
}

pub fn merge_v4_pool_index(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    onchain_payload: &Value,
) -> Result<Value, String> {
    let mut pools_by_id: Vec<(String, Value)> = vec![];
    let mut existing = connection
        .prepare(
            "SELECT pool_id, pool_manager, pool_key_json, token0, token1, fee,
                    tick_spacing, hooks
             FROM v4_exchange_pools
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
        )
        .map_err(|error| format!("could not query SQLite pools for merge: {error}"))?;
    let existing_rows = existing
        .query_map(
            params![network, chain_id, protocol],
            |row| {
                let pool_key: Option<String> = row.get(2)?;
                let pool_key_value = pool_key
                    .and_then(|raw| serde_json::from_str(&raw).ok())
                    .unwrap_or(Value::Null);
                Ok(json!({
                    "poolId": row.get::<_, String>(0)?,
                    "pool_id": row.get::<_, String>(0)?,
                    "poolManager": row.get::<_, Option<String>>(1)?,
                    "poolKey": pool_key_value,
                    "token0": row.get::<_, Option<String>>(3)?,
                    "token1": row.get::<_, Option<String>>(4)?,
                    "fee": row.get::<_, Option<i64>>(5)?,
                    "tickSpacing": row.get::<_, Option<i64>>(6)?,
                    "hooks": row.get::<_, Option<String>>(7)?,
                }))
            },
        )
        .map_err(|error| format!("could not query SQLite pools for merge: {error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("could not query SQLite pools for merge: {error}"))?;
    for pool in existing_rows {
        if let Some(pool_id) = pool.get("poolId").and_then(Value::as_str) {
            pools_by_id.push((pool_id.to_lowercase(), pool));
        }
    }

    let mut max_block = 0i64;
    for raw in onchain_payload
        .get("pools")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
    {
        if !raw.is_object() {
            continue;
        }
        let pool_id = raw
            .get("poolId")
            .or_else(|| raw.get("pool_id"))
            .or_else(|| raw.get("pool"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_lowercase();
        if pool_id.is_empty() {
            continue;
        }
        pools_by_id.retain(|(id, _)| id != &pool_id);
        pools_by_id.push((pool_id, raw.clone()));
        for key in ["generatedBlockNumber", "initializeBlockNumber"] {
            if let Some(block) = raw.get(key).and_then(Value::as_i64) {
                max_block = max_block.max(block);
            }
        }
    }
    for (_, pool) in &pools_by_id {
        for key in ["generatedBlockNumber", "initializeBlockNumber"] {
            if let Some(block) = pool.get(key).and_then(Value::as_i64) {
                max_block = max_block.max(block);
            }
        }
    }
    let merged: Vec<Value> = pools_by_id.into_iter().map(|(_, pool)| pool).collect();
    let generated_block = onchain_payload
        .get("generatedBlockNumber")
        .and_then(Value::as_i64)
        .unwrap_or(max_block);
    Ok(json!({
        "pools": merged,
        "poolCount": merged.len(),
        "generatedBlockNumber": generated_block,
        "generatedAt": onchain_payload.get("generatedAt"),
        "source": "onchain_initialize_logs_plus_sqlite_pools",
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_state_round_trips() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("scan.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE chain_business_initialization (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL,
                    status TEXT NOT NULL,
                    completeness_json TEXT NOT NULL,
                    error_json TEXT,
                    initialized_at TEXT,
                    refreshed_at TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, domain)
                );",
            )
            .unwrap();
        update_v4_scan_state(
            &connection,
            "robinhood",
            4663,
            V4_LIVE_REFRESH_PROTOCOL,
            Some(1200),
            Some(1000),
            Some(1200),
            Some(1200),
            Some(42),
            Some(50),
            Some(30),
            Some(2),
            Some(18),
            5,
            "incremental",
        )
        .unwrap();
        let state = load_v4_scan_state(&connection, "robinhood", 4663, V4_LIVE_REFRESH_PROTOCOL)
            .unwrap();
        assert_eq!(state["last_scanned_block"], 1200);
        assert_eq!(state["confirmed_liquidity_pool_count"], 30);
        assert_eq!(state["scan_mode"], "incremental");
    }
}
