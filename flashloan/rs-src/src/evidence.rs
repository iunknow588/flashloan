use rusqlite::{params_from_iter, Transaction};
use serde_json::{json, Value};
use std::collections::BTreeSet;

static JSON_NULL: Value = Value::Null;

const DYNAMIC_FEE_FLAG: i64 = 8_388_608;
const MAX_FIXED_FEE: i64 = 100_000;

fn utc_now() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

fn norm(value: &Option<String>) -> String {
    value.as_deref().unwrap_or("").trim().to_lowercase()
}

fn norm_value(value: &Value) -> String {
    value
        .as_str()
        .unwrap_or("")
        .trim()
        .to_lowercase()
}

fn text_or_empty(value: &Value) -> String {
    value.as_str().unwrap_or("").to_string()
}

fn json_dumps(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "null".to_string())
}

fn json_object_value(value: &Value) -> Value {
    if value.is_object() {
        value.clone()
    } else if let Some(raw) = value.as_str() {
        serde_json::from_str(raw).unwrap_or(Value::Object(Default::default()))
    } else {
        Value::Object(Default::default())
    }
}

fn json_array_value(value: &Value) -> Value {
    if value.is_array() {
        value.clone()
    } else if let Some(raw) = value.as_str() {
        serde_json::from_str(raw).unwrap_or(Value::Array(Default::default()))
    } else {
        Value::Array(Default::default())
    }
}

fn as_i64(value: &Value) -> Option<i64> {
    value.as_i64().or_else(|| {
        value
            .as_str()
            .and_then(|raw| raw.trim().parse::<i64>().ok())
    })
}

fn as_f64(value: &Value) -> Option<f64> {
    value.as_f64().or_else(|| {
        value
            .as_str()
            .and_then(|raw| raw.trim().parse::<f64>().ok())
    })
}

fn bool_value(value: &Value) -> bool {
    match value {
        Value::Bool(item) => *item,
        Value::Number(item) => item.as_f64().map(|item| item != 0.0).unwrap_or(false),
        Value::String(raw) => matches!(
            raw.trim().to_lowercase().as_str(),
            "1" | "true" | "yes"
        ),
        _ => false,
    }
}

fn tri_value(value: &Value) -> Option<Value> {
    match value {
        Value::Null => None,
        Value::Bool(item) => Some(Value::from(if *item { 1 } else { 0 })),
        Value::Number(item) => Some(Value::from(
            if item.as_f64().map(|item| item != 0.0).unwrap_or(false) {
                1
            } else {
                0
            },
        )),
        Value::String(raw) => Some(Value::from(
            if matches!(
                raw.trim().to_lowercase().as_str(),
                "1" | "true" | "yes"
            ) {
                1
            } else {
                0
            },
        )),
        _ => Some(Value::from(1)),
    }
}

fn value_or(value: &Value, default: &Value) -> Value {
    if value.is_null() {
        default.clone()
    } else {
        value.clone()
    }
}

fn execute_many(
    transaction: &Transaction<'_>,
    sql: &str,
    rows: &[Vec<Value>],
) -> Result<(), String> {
    for row in rows {
        let params: Vec<rusqlite::types::Value> =
            row.iter().map(to_sql_value).collect();
        transaction
            .execute(sql, params_from_iter(params.iter()))
            .map_err(|error| format!("SQLite evidence write failed: {error}"))?;
    }
    Ok(())
}

fn to_sql_value(value: &Value) -> rusqlite::types::Value {
    match value {
        Value::Null => rusqlite::types::Value::Null,
        Value::Bool(item) => rusqlite::types::Value::Integer(if *item { 1 } else { 0 }),
        Value::Number(item) => {
            if let Some(value) = item.as_i64() {
                rusqlite::types::Value::Integer(value)
            } else if let Some(value) = item.as_f64() {
                rusqlite::types::Value::Real(value)
            } else {
                rusqlite::types::Value::Text(item.to_string())
            }
        }
        Value::String(item) => rusqlite::types::Value::Text(item.clone()),
        _ => rusqlite::types::Value::Text(json_dumps(value)),
    }
}

fn sql_params(values: &[Value]) -> Vec<rusqlite::types::Value> {
    values.iter().map(to_sql_value).collect()
}

fn field<'a>(row: &'a Value, keys: &[&str]) -> &'a Value {
    for key in keys {
        if let Some(value) = row.get(*key) {
            return value;
        }
    }
    &JSON_NULL
}

fn upsert_chain_tokens(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    rows: &[Value],
) -> Result<(), String> {
    let now = utc_now();
    let mut params = Vec::new();
    for row in rows {
        let address = norm_value(&row.get("token_address").or_else(|| row.get("address")).unwrap_or(&JSON_NULL));
        let symbol = text_or_empty(
            &row.get("token_symbol")
                .or_else(|| row.get("symbol"))
                .unwrap_or(&JSON_NULL),
        )
        .trim()
        .to_uppercase();
        if address.is_empty() || symbol.is_empty() {
            continue;
        }
        let metadata = row
            .get("token_metadata")
            .or_else(|| row.get("metadata"))
            .or_else(|| row.get("token_metadata_json"))
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        let source = row
            .get("source")
            .or_else(|| row.get("source_json"))
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        params.push(vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(address),
            Value::from(symbol),
            value_or(
                row.get("token_name").or_else(|| row.get("name")).unwrap_or(&JSON_NULL),
                &Value::Null,
            ),
            as_i64(
                row.get("token_decimals")
                    .or_else(|| row.get("decimals"))
                    .unwrap_or(&JSON_NULL),
            )
            .map(Value::from)
            .unwrap_or(Value::Null),
            Value::from(
                text_or_empty(
                    row.get("token_kind")
                        .unwrap_or(&Value::from("erc20".to_string())),
                )
                .to_lowercase(),
            ),
            Value::from(if bool_value(row.get("is_native").unwrap_or(&Value::from(0))) {
                1
            } else {
                0
            }),
            Value::from(if bool_value(row.get("is_wrapped_native").unwrap_or(&Value::from(0))) {
                1
            } else {
                0
            }),
            Value::from(if bool_value(row.get("is_stable").unwrap_or(&Value::from(0))) {
                1
            } else {
                0
            }),
            row.get("anchor_symbol")
                .cloned()
                .unwrap_or(Value::Null),
            Value::from(json_dumps(&json_object_value(&metadata))),
            Value::from(json_dumps(&json_object_value(&source))),
            Value::from(now.clone()),
            Value::from(now.clone()),
        ]);
    }
    execute_many(
        transaction,
        "INSERT INTO chain_tokens (
            network, chain_id, token_address, token_symbol, token_name,
            token_decimals, token_kind, is_native, is_wrapped_native,
            is_stable, anchor_symbol, token_metadata_json, source_json,
            first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, token_address) DO UPDATE SET
            token_symbol = excluded.token_symbol,
            token_name = excluded.token_name,
            token_decimals = excluded.token_decimals,
            token_kind = excluded.token_kind,
            is_native = excluded.is_native,
            is_wrapped_native = excluded.is_wrapped_native,
            is_stable = excluded.is_stable,
            anchor_symbol = excluded.anchor_symbol,
            token_metadata_json = excluded.token_metadata_json,
            source_json = excluded.source_json,
            updated_at = excluded.updated_at",
        &params,
    )
}

fn upsert_chain_markets(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    rows: &[Value],
) -> Result<(), String> {
    let now = utc_now();
    let mut params = Vec::new();
    for row in rows {
        let market_id = text_or_empty(
            row.get("market_id")
                .or_else(|| row.get("marketId"))
                .unwrap_or(&JSON_NULL),
        );
        if market_id.is_empty() {
            continue;
        }
        let prefixes = row
            .get("rpc_env_prefixes")
            .or_else(|| row.get("rpc_env_prefixes_json"))
            .cloned()
            .unwrap_or_else(|| Value::Array(Default::default()));
        let market_config = row
            .get("market_config")
            .or_else(|| row.get("market_config_json"))
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        params.push(vec![
            Value::from(market_id),
            Value::from(text_or_empty(
                row.get("canonical_market_id")
                    .or_else(|| row.get("canonicalMarketId"))
                    .or_else(|| row.get("market_id"))
                    .unwrap_or(&JSON_NULL),
            )),
            row.get("legacy_market_id")
                .or_else(|| row.get("legacyMarketId"))
                .cloned()
                .unwrap_or(Value::Null),
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(text_or_empty(
                row.get("feature").unwrap_or(&JSON_NULL),
            )),
            Value::from(text_or_empty(
                row.get("protocol").unwrap_or(&JSON_NULL),
            )),
            row.get("protocol_id")
                .or_else(|| row.get("protocolId"))
                .cloned()
                .unwrap_or(Value::Null),
            row.get("display_name")
                .or_else(|| row.get("displayName"))
                .cloned()
                .unwrap_or(Value::Null),
            row.get("market_type")
                .or_else(|| row.get("marketType"))
                .cloned()
                .unwrap_or(Value::Null),
            row.get("native_symbol")
                .or_else(|| row.get("nativeSymbol"))
                .cloned()
                .unwrap_or(Value::Null),
            Value::from(if bool_value(row.get("observation_only").unwrap_or(&Value::from(0))) {
                1
            } else {
                0
            }),
            Value::from(if bool_value(
                row.get("protocol_supported").unwrap_or(&Value::from(1)),
            ) {
                1
            } else {
                0
            }),
            Value::from(if bool_value(row.get("chain_default").unwrap_or(&Value::from(0))) {
                1
            } else {
                0
            }),
            Value::from(if bool_value(row.get("market_default").unwrap_or(&Value::from(0))) {
                1
            } else {
                0
            }),
            Value::from(json_dumps(&json_array_value(&prefixes))),
            row.get("migration_type")
                .or_else(|| row.get("migrationType"))
                .cloned()
                .unwrap_or(Value::Null),
            Value::from(json_dumps(&json_object_value(&market_config))),
            Value::from(now.clone()),
            Value::from(now.clone()),
        ]);
    }
    execute_many(
        transaction,
        "INSERT INTO chain_markets (
            market_id, canonical_market_id, legacy_market_id, network,
            chain_id, feature, protocol, protocol_id, display_name,
            market_type, native_symbol, observation_only,
            protocol_supported, chain_default, market_default,
            rpc_env_prefixes_json, migration_type, market_config_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            canonical_market_id = excluded.canonical_market_id,
            legacy_market_id = excluded.legacy_market_id,
            network = excluded.network,
            chain_id = excluded.chain_id,
            feature = excluded.feature,
            protocol = excluded.protocol,
            protocol_id = excluded.protocol_id,
            display_name = excluded.display_name,
            market_type = excluded.market_type,
            native_symbol = excluded.native_symbol,
            observation_only = excluded.observation_only,
            protocol_supported = excluded.protocol_supported,
            chain_default = excluded.chain_default,
            market_default = excluded.market_default,
            rpc_env_prefixes_json = excluded.rpc_env_prefixes_json,
            migration_type = excluded.migration_type,
            market_config_json = excluded.market_config_json,
            updated_at = excluded.updated_at",
        &params,
    )
}

fn upsert_v4_exchange_pools(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    rows: &[Value],
) -> Result<usize, String> {
    let now = utc_now();
    let mut params = Vec::new();
    let mut count = 0;
    for row in rows {
        let pool_id = norm_value(
            row.get("pool_id")
                .or_else(|| row.get("poolId"))
                .or_else(|| row.get("pool"))
                .unwrap_or(&JSON_NULL),
        );
        let token0 = norm_value(
            row.get("token0")
                .or_else(|| row.get("currency0"))
                .unwrap_or(&JSON_NULL),
        );
        let token1 = norm_value(
            row.get("token1")
                .or_else(|| row.get("currency1"))
                .unwrap_or(&JSON_NULL),
        );
        if pool_id.is_empty() || token0.is_empty() || token1.is_empty() {
            continue;
        }
        let address_pair = format!("{token0}:{token1}");
        let fee = as_i64(row.get("fee").unwrap_or(&JSON_NULL));
        let dynamic_fee = fee.map(|value| value & DYNAMIC_FEE_FLAG != 0).unwrap_or(false);
        let special_fee = fee.is_none()
            || fee == Some(0)
            || dynamic_fee
            || fee.unwrap_or(0) > MAX_FIXED_FEE;
        let fee_label = if dynamic_fee {
            Value::from("dynamic".to_string())
        } else if special_fee {
            Value::from("special".to_string())
        } else {
            fee.map(Value::from).unwrap_or(Value::Null)
        };
        let pool_key = row
            .get("pool_key")
            .or_else(|| row.get("poolKey"))
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        let hooks = norm_value(
            row.get("hooks")
                .or_else(|| row.get("hook"))
                .unwrap_or(&JSON_NULL),
        );
        params.push(vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(protocol.to_string()),
            Value::from(pool_id),
            Value::from(text_or_empty(
                row.get("pool_identity_mode")
                    .or_else(|| row.get("poolIdentityMode"))
                    .unwrap_or(&Value::from("v4_pool_id".to_string())),
            )),
            Value::from(norm_value(
                row.get("pool_manager")
                    .or_else(|| row.get("poolManager"))
                    .unwrap_or(&JSON_NULL),
            )),
            Value::from(json_dumps(&json_object_value(&pool_key))),
            Value::from(token0),
            Value::from(token1),
            fee.map(Value::from).unwrap_or(Value::Null),
            fee_label,
            Value::from(if dynamic_fee { 1 } else { 0 }),
            Value::from(if special_fee { 1 } else { 0 }),
            as_i64(
                row.get("tick_spacing")
                    .or_else(|| row.get("tickSpacing"))
                    .unwrap_or(&JSON_NULL),
            )
            .map(Value::from)
            .unwrap_or(Value::Null),
            Value::from(hooks),
            Value::from(address_pair),
            Value::from(now.clone()),
            Value::from(now.clone()),
        ]);
        count += 1;
    }
    execute_many(
        transaction,
        "INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, pool_key_json, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
            pool_identity_mode = excluded.pool_identity_mode,
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
            updated_at = excluded.updated_at",
        &params,
    )?;
    Ok(count)
}

fn upsert_v4_liquidity_snapshots(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    rows: &[Value],
) -> Result<usize, String> {
    let now = utc_now();
    let mut params = Vec::new();
    let mut count = 0;
    for row in rows {
        let pool_id = norm_value(
            row.get("pool_id")
                .or_else(|| row.get("poolId"))
                .or_else(|| row.get("pool"))
                .unwrap_or(&JSON_NULL),
        );
        if pool_id.is_empty() {
            continue;
        }
        let has_liquidity = row
            .get("has_liquidity")
            .or_else(|| row.get("hasLiquidity"))
            .cloned()
            .unwrap_or(Value::Null);
        let recent_swap_active = row
            .get("recent_swap_active")
            .or_else(|| row.get("recentSwapActive"))
            .cloned()
            .unwrap_or(Value::Null);
        let liquidity_value = row
            .get("liquidity")
            .cloned()
            .unwrap_or(Value::Null);
        let liquidity_index = as_f64(
            row.get("liquidity_index")
                .or_else(|| row.get("liquidityIndex"))
                .unwrap_or(&JSON_NULL),
        )
        .or_else(|| {
            if !liquidity_value.is_null() {
                liquidity_value.as_str().and_then(|raw| raw.parse::<f64>().ok())
            } else {
                None
            }
        })
        .map(Value::from)
        .unwrap_or(Value::Null);
        let tvl = as_f64(
            row.get("estimated_tvl_usd")
                .or_else(|| row.get("estimatedTvlUsd"))
                .or_else(|| row.get("tvl_usd"))
                .unwrap_or(&JSON_NULL),
        )
        .or_else(|| {
            row.get("balances")
                .and_then(|value| value.get("tvlUsd"))
                .and_then(as_f64)
        })
        .map(Value::from)
        .unwrap_or(Value::Null);
        let balances = row
            .get("balances")
            .or_else(|| row.get("balances_json"))
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        let depth_band = row
            .get("depth_band")
            .or_else(|| row.get("depthBand"))
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        let observed_at = row
            .get("observed_at")
            .or_else(|| row.get("observedAt"))
            .cloned()
            .unwrap_or_else(|| Value::from(now.clone()));
        let refreshed_at = row
            .get("refreshed_at")
            .or_else(|| row.get("refreshedAt"))
            .cloned()
            .unwrap_or_else(|| Value::from(now.clone()));
        let evidence_source = row
            .get("evidence_source")
            .or_else(|| row.get("evidenceSource"))
            .or_else(|| row.get("liquidity_source"))
            .or_else(|| row.get("liquiditySource"))
            .or_else(|| row.get("source"))
            .cloned()
            .unwrap_or(Value::Null);
        params.push(vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(protocol.to_string()),
            Value::from(pool_id),
            tri_value(&has_liquidity).unwrap_or(Value::Null),
            Value::from(text_or_empty(&liquidity_value)),
            liquidity_index,
            tvl,
            Value::from(json_dumps(&json_object_value(&balances))),
            Value::from(text_or_empty(
                row.get("depth_status")
                    .or_else(|| row.get("depthStatus"))
                    .unwrap_or(&JSON_NULL),
            )),
            Value::from(json_dumps(&json_object_value(&depth_band))),
            as_f64(
                row.get("depth_score_usd")
                    .or_else(|| row.get("depthScoreUsd"))
                    .unwrap_or(&JSON_NULL),
            )
            .map(Value::from)
            .unwrap_or(Value::Null),
            Value::from(text_or_empty(
                row.get("sqrt_price_x96")
                    .or_else(|| row.get("sqrtPriceX96"))
                    .or_else(|| row.get("sqrtPrice"))
                    .unwrap_or(&JSON_NULL),
            )),
            as_f64(row.get("price").unwrap_or(&JSON_NULL))
                .map(Value::from)
                .unwrap_or(Value::Null),
            as_i64(row.get("tick").unwrap_or(&JSON_NULL))
                .map(Value::from)
                .unwrap_or(Value::Null),
            row.get("liquidity_read_at")
                .or_else(|| row.get("liquidityReadAt"))
                .cloned()
                .unwrap_or(Value::Null),
            as_i64(
                row.get("liquidity_block_number")
                    .or_else(|| row.get("liquidityBlockNumber"))
                    .unwrap_or(&JSON_NULL),
            )
            .map(Value::from)
            .unwrap_or(Value::Null),
            tri_value(&recent_swap_active).unwrap_or(Value::Null),
            observed_at,
            refreshed_at,
            {
                let normalized = norm_value(&evidence_source);
                if normalized.is_empty() {
                    Value::Null
                } else {
                    Value::from(normalized)
                }
            },
        ]);
        count += 1;
    }
    execute_many(
        transaction,
        "INSERT INTO v4_exchange_pool_liquidity_snapshots (
            network, chain_id, protocol, pool_id, has_liquidity, liquidity,
            liquidity_index, estimated_tvl_usd, balances_json, depth_status,
            depth_band_json, depth_score_usd, sqrt_price_x96, price, tick,
            liquidity_read_at, liquidity_block_number, recent_swap_active,
            observed_at, refreshed_at, evidence_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
            has_liquidity = CASE
                WHEN excluded.has_liquidity IS NULL
                THEN v4_exchange_pool_liquidity_snapshots.has_liquidity
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.has_liquidity
                ELSE excluded.has_liquidity
            END,
            liquidity = CASE
                WHEN excluded.liquidity IS NULL OR excluded.liquidity = ''
                THEN v4_exchange_pool_liquidity_snapshots.liquidity
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.liquidity
                ELSE excluded.liquidity
            END,
            liquidity_index = CASE
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.liquidity_index
                ELSE COALESCE(excluded.liquidity_index, v4_exchange_pool_liquidity_snapshots.liquidity_index)
            END,
            estimated_tvl_usd = CASE
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.estimated_tvl_usd
                ELSE COALESCE(excluded.estimated_tvl_usd, v4_exchange_pool_liquidity_snapshots.estimated_tvl_usd)
            END,
            balances_json = COALESCE(excluded.balances_json, v4_exchange_pool_liquidity_snapshots.balances_json),
            depth_status = CASE
                WHEN excluded.depth_status IS NULL OR excluded.depth_status = ''
                THEN v4_exchange_pool_liquidity_snapshots.depth_status
                ELSE excluded.depth_status
            END,
            depth_band_json = COALESCE(excluded.depth_band_json, v4_exchange_pool_liquidity_snapshots.depth_band_json),
            depth_score_usd = COALESCE(excluded.depth_score_usd, v4_exchange_pool_liquidity_snapshots.depth_score_usd),
            sqrt_price_x96 = CASE
                WHEN excluded.sqrt_price_x96 IS NULL OR excluded.sqrt_price_x96 = ''
                THEN v4_exchange_pool_liquidity_snapshots.sqrt_price_x96
                ELSE excluded.sqrt_price_x96
            END,
            price = COALESCE(excluded.price, v4_exchange_pool_liquidity_snapshots.price),
            tick = COALESCE(excluded.tick, v4_exchange_pool_liquidity_snapshots.tick),
            liquidity_read_at = COALESCE(excluded.liquidity_read_at, v4_exchange_pool_liquidity_snapshots.liquidity_read_at),
            liquidity_block_number = COALESCE(excluded.liquidity_block_number, v4_exchange_pool_liquidity_snapshots.liquidity_block_number),
            recent_swap_active = CASE
                WHEN excluded.recent_swap_active IS NULL
                THEN v4_exchange_pool_liquidity_snapshots.recent_swap_active
                ELSE excluded.recent_swap_active
            END,
            observed_at = COALESCE(excluded.observed_at, v4_exchange_pool_liquidity_snapshots.observed_at),
            refreshed_at = excluded.refreshed_at,
            evidence_source = COALESCE(excluded.evidence_source, v4_exchange_pool_liquidity_snapshots.evidence_source)",
        &params,
    )?;
    Ok(count)
}

fn upsert_initialization(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    domain: &str,
    status: &str,
    completeness: &Value,
) -> Result<(), String> {
    let now = utc_now();
    transaction
        .execute(
            "INSERT INTO chain_business_initialization (
                network, chain_id, protocol, domain, status,
                completeness_json, error_json, initialized_at, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, domain) DO UPDATE SET
                status = excluded.status,
                completeness_json = excluded.completeness_json,
                error_json = excluded.error_json,
                initialized_at = COALESCE(chain_business_initialization.initialized_at, excluded.initialized_at),
                refreshed_at = excluded.refreshed_at",
            params_from_iter(sql_params(&[
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
                Value::from(domain.to_string()),
                Value::from(status.to_string()),
                Value::from(json_dumps(completeness)),
                Value::Null,
                Value::from(now.clone()),
                Value::from(now.clone()),
            ]).iter()),
        )
        .map_err(|error| format!("chain_business_initialization upsert failed: {error}"))?;
    Ok(())
}

fn clear_v4_exchange_evidence(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    protocol: &str,
) -> Result<(), String> {
    for table in [
        "v4_exchange_pool_liquidity_snapshots",
        "v4_exchange_pools",
    ] {
        let sql = format!(
            "DELETE FROM {table} WHERE network = ? AND chain_id = ? AND protocol = ?"
        );
        transaction
            .execute(
                &sql,
                params_from_iter(sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ]).iter()),
            )
            .map_err(|error| format!("{table} clear failed: {error}"))?;
    }
    transaction
        .execute(
            "DELETE FROM dex_surface_snapshots WHERE network = ? AND chain_id = ? AND protocol = ?",
            params_from_iter(sql_params(&[
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
            ]).iter()),
        )
        .map_err(|error| format!("dex_surface_snapshots clear failed: {error}"))?;
    Ok(())
}

pub(crate) fn import_v4_evidence_payloads(
    database_path: &str,
    network: &str,
    chain_id: i64,
    protocol: &str,
    token_rows: &[Value],
    market_rows: &[Value],
    pool_rows: &[Value],
    liquidity_rows: &[Value],
    index_liquidity_rows: &[Value],
    replace: bool,
    initialize_log_count: i64,
) -> Result<Value, String> {
    let mut connection = crate::schema::open_rw(database_path)?;
    let transaction = connection
        .transaction()
        .map_err(|error| format!("evidence transaction failed: {error}"))?;
    if replace {
        clear_v4_exchange_evidence(&transaction, network, chain_id, protocol)?;
    }
    upsert_chain_tokens(&transaction, network, chain_id, token_rows)?;
    upsert_chain_markets(&transaction, network, chain_id, market_rows)?;
    let pool_count = upsert_v4_exchange_pools(&transaction, network, chain_id, protocol, pool_rows)?;
    let liquidity_count =
        upsert_v4_liquidity_snapshots(&transaction, network, chain_id, protocol, liquidity_rows)?;
    let index_liquidity_count = upsert_v4_liquidity_snapshots(
        &transaction,
        network,
        chain_id,
        protocol,
        index_liquidity_rows,
    )?;
    upsert_initialization(
        &transaction,
        network,
        chain_id,
        protocol,
        "v4_exchange_pool_index",
        "complete",
        &json!({
            "initialize_log_count": initialize_log_count,
            "pool_count": pool_count,
        }),
    )?;
    transaction
        .commit()
        .map_err(|error| format!("evidence transaction commit failed: {error}"))?;
    Ok(json!({
        "ok": true,
        "network": network,
        "chainId": chain_id,
        "v4PoolCount": pool_count,
        "tokenCount": token_rows.len(),
        "liquidityRowCount": liquidity_count,
        "indexLiquidityRowCount": index_liquidity_count,
        "source": "onchain_evidence_payload",
        "detail": "raw V4 tables imported directly from in-memory evidence; JSON files are not read",
    }))
}

pub(crate) fn prune_v4_non_executable_evidence(
    database_path: &str,
    network: &str,
    chain_id: i64,
    protocol: &str,
    delete_unknown: bool,
) -> Result<Value, String> {
    let mut connection = crate::schema::open_rw(database_path)?;
    let count_sql = "SELECT COUNT(*) FROM v4_exchange_pool_liquidity_snapshots
                     WHERE network = ? AND chain_id = ? AND protocol = ?";
    let before_liquidity: i64 = connection
        .query_row(
            count_sql,
            params_from_iter(sql_params(&[
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
            ]).iter()),
            |row| row.get(0),
        )
        .map_err(|error| format!("liquidity count failed: {error}"))?;
    let before_pools: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = ?",
            params_from_iter(sql_params(&[
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
            ]).iter()),
            |row| row.get(0),
        )
        .map_err(|error| format!("pool count failed: {error}"))?;
    let transaction = connection
        .transaction()
        .map_err(|error| format!("prune transaction failed: {error}"))?;
    let mut removable_pool_ids = BTreeSet::new();
    {
        let mut statement = transaction
            .prepare(
                "SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND has_liquidity = 0",
            )
            .map_err(|error| format!("zero liquidity select failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter(sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ]).iter()),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("zero liquidity query failed: {error}"))?;
        for row in rows {
            let pool_id = norm(
                &row.map_err(|error| format!("zero liquidity row failed: {error}"))?,
            );
            if !pool_id.is_empty() {
                removable_pool_ids.insert(pool_id);
            }
        }
    }
    let deleted_zero = transaction
        .execute(
            "DELETE FROM v4_exchange_pool_liquidity_snapshots
             WHERE network = ? AND chain_id = ? AND protocol = ? AND has_liquidity = 0",
            params_from_iter(sql_params(&[
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
            ]).iter()),
        )
        .map_err(|error| format!("zero liquidity delete failed: {error}"))?;
    let mut deleted_unknown = 0;
    if delete_unknown {
        let mut statement = transaction
            .prepare(
                "SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND has_liquidity IS NULL",
            )
            .map_err(|error| format!("unknown liquidity select failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter(sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ]).iter()),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("unknown liquidity query failed: {error}"))?;
        for row in rows {
            let pool_id = norm(
                &row.map_err(|error| format!("unknown liquidity row failed: {error}"))?,
            );
            if !pool_id.is_empty() {
                removable_pool_ids.insert(pool_id);
            }
        }
        let snapshot_pool_ids: BTreeSet<String> = {
            let mut statement = transaction
                .prepare(
                    "SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots
                     WHERE network = ? AND chain_id = ? AND protocol = ?",
                )
                .map_err(|error| format!("snapshot select failed: {error}"))?;
            let rows = statement
                .query_map(
                    params_from_iter(sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                    ]).iter()),
                    |row| row.get::<_, Option<String>>(0),
                )
                .map_err(|error| format!("snapshot query failed: {error}"))?;
            let mut ids = BTreeSet::new();
            for row in rows {
                let pool_id = norm(
                    &row.map_err(|error| format!("snapshot row failed: {error}"))?,
                );
                if !pool_id.is_empty() {
                    ids.insert(pool_id);
                }
            }
            ids
        };
        let mut statement = transaction
            .prepare(
                "SELECT pool_id FROM v4_exchange_pools
                 WHERE network = ? AND chain_id = ? AND protocol = ?",
            )
            .map_err(|error| format!("pool select failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter(sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ]).iter()),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("pool query failed: {error}"))?;
        for row in rows {
            let pool_id = norm(
                &row.map_err(|error| format!("pool row failed: {error}"))?,
            );
            if !pool_id.is_empty() && !snapshot_pool_ids.contains(&pool_id) {
                removable_pool_ids.insert(pool_id);
            }
        }
        deleted_unknown = transaction
            .execute(
                "DELETE FROM v4_exchange_pool_liquidity_snapshots
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND has_liquidity IS NULL",
                params_from_iter(sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ]).iter()),
            )
            .map_err(|error| format!("unknown liquidity delete failed: {error}"))?;
    }
    let deleted_pools = if removable_pool_ids.is_empty() {
        0
    } else {
        let placeholders = vec!["?"; removable_pool_ids.len()].join(",");
        let sql = format!(
            "DELETE FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = ?
               AND pool_id IN ({placeholders})"
        );
        let mut params = vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(protocol.to_string()),
        ];
        params.extend(removable_pool_ids.iter().cloned().map(Value::from));
        transaction
            .execute(&sql, params_from_iter(sql_params(&params).iter()))
            .map_err(|error| format!("pool delete failed: {error}"))?
    };
    let after_liquidity: i64 = transaction
        .query_row(
            count_sql,
            params_from_iter(sql_params(&[
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
            ]).iter()),
            |row| row.get(0),
        )
        .map_err(|error| format!("after liquidity count failed: {error}"))?;
    let after_pools: i64 = transaction
        .query_row(
            "SELECT COUNT(*) FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = ?",
            params_from_iter(sql_params(&[
                Value::from(network.to_string()),
                Value::from(chain_id),
                Value::from(protocol.to_string()),
            ]).iter()),
            |row| row.get(0),
        )
        .map_err(|error| format!("after pool count failed: {error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("prune transaction commit failed: {error}"))?;
    Ok(json!({
        "beforeLiquidityCount": before_liquidity,
        "beforePoolCount": before_pools,
        "deletedZeroLiquidityCount": deleted_zero,
        "deletedUnknownLiquidityCount": deleted_unknown,
        "deletedPoolCount": deleted_pools,
        "afterLiquidityCount": after_liquidity,
        "afterPoolCount": after_pools,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tri_converts_common_evidence_values() {
        assert_eq!(tri_value(&Value::from(true)), Some(Value::from(1)));
        assert_eq!(tri_value(&Value::from("1")), Some(Value::from(1)));
        assert_eq!(tri_value(&Value::from("no")), Some(Value::from(0)));
        assert_eq!(tri_value(&Value::Null), None);
    }
}
