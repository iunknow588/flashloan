use rusqlite::{params_from_iter, Transaction};
use serde_json::{json, Value};
use std::collections::BTreeSet;

static JSON_NULL: Value = Value::Null;

const DYNAMIC_FEE_FLAG: i64 = 8_388_608;
const MAX_FIXED_FEE: i64 = 100_000;

/// The highest fee a pool may carry to enter the pool library.
///
/// The library is the trading universe of the orchestrator. A pool whose
/// fee is above this ceiling is not a pool the orchestrator may trade, and
/// a pool that is not tradeable does not belong in the library at all: it
/// only enlarges the index, the depth measurement load and the backup
/// retention set without ever being a candidate.
///
/// The ceiling is expressed in hundredths of a bip, so 30000 is 3.00%.
const MAX_LIBRARY_FEE: i64 = 30_000;

/// Whether a pool may enter the pool library.
///
/// A pool qualifies only when it carries a **fixed** fee inside the
/// ceiling. A dynamic-fee pool is excluded because its rate is decided by
/// a hook at swap time, and a pool above the ceiling is excluded because
/// the orchestrator will never trade it.
fn pool_enters_library(fee: Option<i64>) -> bool {
    match fee {
        Some(value) => value > 0 && value <= MAX_LIBRARY_FEE && value & DYNAMIC_FEE_FLAG == 0,
        None => false,
    }
}

fn utc_now() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

fn norm(value: &Option<String>) -> String {
    value.as_deref().unwrap_or("").trim().to_lowercase()
}

fn norm_value(value: &Value) -> String {
    value.as_str().unwrap_or("").trim().to_lowercase()
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

fn bool_value(value: &Value) -> bool {
    match value {
        Value::Bool(item) => *item,
        Value::Number(item) => item.as_f64().map(|item| item != 0.0).unwrap_or(false),
        Value::String(raw) => matches!(raw.trim().to_lowercase().as_str(), "1" | "true" | "yes"),
        _ => false,
    }
}

/// Interpret a tri-state liquidity observation as a boolean.
///
/// The store keeps the observation as `NULL` (never measured), `0` (the
/// pool manager reported no liquidity) or `1` (liquidity confirmed).  The
/// admission rule treats a missing measurement as a coverage gap and an
/// explicit zero as a rejection, so the two states must not collapse.
#[cfg_attr(not(test), allow(dead_code))]
fn tri_bool(value: &Value) -> Option<bool> {
    match value {
        Value::Null => None,
        Value::Bool(item) => Some(*item),
        Value::Number(item) => Some(item.as_f64().map(|raw| raw != 0.0).unwrap_or(false)),
        Value::String(raw) => Some(matches!(
            raw.trim().to_lowercase().as_str(),
            "1" | "true" | "yes"
        )),
        _ => Some(true),
    }
}

#[cfg_attr(not(test), allow(dead_code))]
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
            if matches!(raw.trim().to_lowercase().as_str(), "1" | "true" | "yes") {
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
        let params: Vec<rusqlite::types::Value> = row.iter().map(to_sql_value).collect();
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

fn upsert_chain_tokens(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: i64,
    rows: &[Value],
) -> Result<(), String> {
    let now = utc_now();
    let mut params = Vec::new();
    for row in rows {
        let address = norm_value(
            &row.get("token_address")
                .or_else(|| row.get("address"))
                .unwrap_or(&JSON_NULL),
        );
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
        let existing = transaction
            .query_row(
                "SELECT token_metadata_json, source_json
                 FROM chain_tokens
                 WHERE network = ? AND chain_id = ?
                   AND lower(token_address) = ?",
                params_from_iter(
                    sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(address.clone()),
                    ])
                    .iter(),
                ),
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .ok();
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
        let mut merged_metadata = json_object_value(&metadata);
        let mut merged_source = json_object_value(&source);
        if let Some((raw_existing_metadata, raw_existing_source)) = existing {
            let existing_metadata = json_object_value(&Value::String(raw_existing_metadata));
            let incoming_category = merged_metadata
                .get("category")
                .and_then(Value::as_str)
                .map(str::trim)
                .unwrap_or("");
            if incoming_category.is_empty() {
                if let (Some(meta_object), Some(existing_map)) = (
                    merged_metadata.as_object_mut(),
                    existing_metadata.as_object(),
                ) {
                    for (keep_key, value) in existing_map {
                        let should_keep = meta_object
                            .get(keep_key)
                            .map(|current| {
                                current.is_null()
                                    || current.as_str().map(str::trim).unwrap_or("").is_empty()
                            })
                            .unwrap_or(true);
                        let existing_text = value.as_str().unwrap_or("");
                        if should_keep && !value.is_null() && !existing_text.trim().is_empty() {
                            meta_object.insert(keep_key.clone(), value.clone());
                        }
                    }
                }
            }
            if merged_source
                .as_object()
                .map(|value| value.is_empty())
                .unwrap_or(true)
            {
                merged_source = json_object_value(&Value::String(raw_existing_source));
            }
        }
        params.push(vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(address),
            Value::from(symbol),
            value_or(
                row.get("token_name")
                    .or_else(|| row.get("name"))
                    .unwrap_or(&JSON_NULL),
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
            Value::from(
                if bool_value(row.get("is_native").unwrap_or(&Value::from(0))) {
                    1
                } else {
                    0
                },
            ),
            Value::from(
                if bool_value(row.get("is_wrapped_native").unwrap_or(&Value::from(0))) {
                    1
                } else {
                    0
                },
            ),
            Value::from(
                if bool_value(row.get("is_stable").unwrap_or(&Value::from(0))) {
                    1
                } else {
                    0
                },
            ),
            row.get("anchor_symbol").cloned().unwrap_or(Value::Null),
            Value::from(json_dumps(&merged_metadata)),
            Value::from(json_dumps(&merged_source)),
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
            Value::from(text_or_empty(row.get("feature").unwrap_or(&JSON_NULL))),
            Value::from(text_or_empty(row.get("protocol").unwrap_or(&JSON_NULL))),
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
            Value::from(
                if bool_value(row.get("observation_only").unwrap_or(&Value::from(0))) {
                    1
                } else {
                    0
                },
            ),
            Value::from(
                if bool_value(row.get("protocol_supported").unwrap_or(&Value::from(1))) {
                    1
                } else {
                    0
                },
            ),
            Value::from(
                if bool_value(row.get("chain_default").unwrap_or(&Value::from(0))) {
                    1
                } else {
                    0
                },
            ),
            Value::from(
                if bool_value(row.get("market_default").unwrap_or(&Value::from(0))) {
                    1
                } else {
                    0
                },
            ),
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
        // A pool whose fee is outside the library ceiling never enters the
        // pool table. The rule is a **storage** rule, not a ranking rule:
        // the pool is not in the library, so it can never be a candidate, a
        // recommendation or a backup.
        let fee = as_i64(
            row.get("fee")
                .or_else(|| row.get("feePips"))
                .unwrap_or(&JSON_NULL),
        );
        if !pool_enters_library(fee) {
            continue;
        }
        let address_pair = format!("{token0}:{token1}");
        let dynamic_fee = fee
            .map(|value| value & DYNAMIC_FEE_FLAG != 0)
            .unwrap_or(false);
        let special_fee =
            fee.is_none() || fee == Some(0) || dynamic_fee || fee.unwrap_or(0) > MAX_FIXED_FEE;
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

/// Attach the kernel admission verdict to a depth-band document.
///
/// The verdict is owned by the Rust kernel and read from the shared depth
/// policy, so publishing it on every surface keeps one decision per pool
/// instead of a decision that only exists on the candidate surface.
#[cfg_attr(not(test), allow(dead_code))]
fn attach_kernel_admission_verdict(
    depth_band: Value,
    depth_status: Option<&str>,
    has_liquidity: Option<bool>,
) -> Value {
    let document = json_object_value(&depth_band);
    let document_raw = json_dumps(&document);
    let verdict = crate::depth_verdict::admit_candidate_pool_with_liquidity(
        depth_status,
        Some(document_raw.as_str()),
        has_liquidity,
    );
    let mut document = match document {
        Value::Object(map) => map,
        _ => serde_json::Map::new(),
    };
    document.insert(
        "kernelAdmitted".to_string(),
        Value::Bool(verdict.admitted),
    );
    document.insert(
        "kernelAdmissionReason".to_string(),
        Value::String(verdict.reason),
    );
    if let Some(band) = verdict.depth_band {
        document.insert("kernelDepthBand".to_string(), Value::String(band));
    }
    if let Some(capacity) = verdict.capacity_binding_raw {
        document.insert(
            "kernelCapacityBindingRaw".to_string(),
            serde_json::Number::from_f64(capacity)
                .map(Value::Number)
                .unwrap_or(Value::Null),
        );
    }
    Value::Object(document)
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
    // TASK-172 / AC-221: the depth snapshot table is **not** cleared here.
    //
    // The evidence channel no longer owns that table. Its single writer is
    // the Rust pool manager, which writes the price columns and the
    // single-tick bounds in **one** statement, so every row is
    // self-consistent. When the evidence channel also wrote the table, it
    // overwrote the price columns and left the bounds alone, which fused
    // two different on-chain moments into one row and made the published
    // bound stale relative to its own price.
    //
    // Clearing the table here would delete the pool manager's materialised
    // rows and leave the table empty until the next pool-manager refresh,
    // so the pool manager's rows are deliberately left untouched.
    //
    // The rows this channel *did* write still have to go, because the
    // snapshot table carries a foreign key onto the pool table and the
    // pools are deleted below.  They are told apart by their
    // ``materialization_batch``: the pool manager stamps every row it
    // writes with the batch of the run that wrote it, and a row without a
    // batch is a row this channel wrote (or a row that predates the
    // batch column).  Deleting exactly those rows is a **row scoped**
    // removal, so it cannot wipe the pool manager's state.
    let evidence_rows_removed = transaction
        .execute(
            concat!(
                "DELETE FROM v4_exchange_pool_liquidity_snapshots ",
                "WHERE network = ? AND chain_id = ? AND protocol = ? ",
                "AND (materialization_batch IS NULL OR materialization_batch = '')",
            ),
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
        )
        .map_err(|error| {
            format!("v4_exchange_pool_liquidity_snapshots evidence clear failed: {error}")
        })?;
    let _ = evidence_rows_removed;
    for table in [
        "v4_closed_loop_optimizations",
        "v4_closed_loop_hops",
        "v4_closed_loops",
    ] {
        let sql =
            format!("DELETE FROM {table} WHERE network = ? AND chain_id = ? AND protocol = ?");
        transaction
            .execute(
                &sql,
                params_from_iter(
                    sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                    ])
                    .iter(),
                ),
            )
            .map_err(|error| format!("{table} clear failed: {error}"))?;
    }
    // The pool rows are the **parent** of the depth snapshot rows: the
    // snapshot table carries a foreign key onto the pool table, and the pool
    // manager only ever UPDATEs a snapshot row -- it never inserts one --
    // so a pool row has to exist for the manager to be able to materialise
    // a snapshot at all.
    //
    // A pool row that still carries a pool-manager snapshot row therefore
    // cannot be removed here.  Removing it would either fail on the foreign
    // key or orphan the manager's measurement.  The pool rows that no
    // pool-manager snapshot references are removed as before, so the clear
    // still resets the pool index this channel owns.
    let pool_rows_removed = transaction
        .execute(
            concat!(
                "DELETE FROM v4_exchange_pools ",
                "WHERE network = ? AND chain_id = ? AND protocol = ? ",
                "AND NOT EXISTS ( ",
                "  SELECT 1 FROM v4_exchange_pool_liquidity_snapshots s ",
                "   WHERE s.network = v4_exchange_pools.network ",
                "     AND s.chain_id = v4_exchange_pools.chain_id ",
                "     AND s.protocol = v4_exchange_pools.protocol ",
                "     AND s.pool_id = v4_exchange_pools.pool_id ",
                ")",
            ),
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
        )
        .map_err(|error| format!("v4_exchange_pools clear failed: {error}"))?;
    let _ = pool_rows_removed;
    transaction
        .execute(
            "DELETE FROM dex_surface_snapshots WHERE network = ? AND chain_id = ? AND protocol = ?",
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
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
    let pool_count =
        upsert_v4_exchange_pools(&transaction, network, chain_id, protocol, pool_rows)?;
    // TASK-172 / AC-221: the evidence channel **does not** write the depth
    // snapshot table.
    //
    // The table is the materialised boundary of the pool manager's in-memory
    // pool state. Its single writer is the Rust pool manager, which writes
    // the price columns and the single-tick bounds in one statement, so a
    // row always describes exactly one on-chain moment.
    //
    // This channel used to write the price columns only, which left the
    // bounds of an older moment next to a newer price: the row then carried
    // two different observations at once, and every consumer of the bound
    // read a stale number. The evidence rows are therefore **dropped**
    // rather than merged: the pool manager already measures the same
    // on-chain state, and it is the only writer that can materialise a
    // complete row.
    let liquidity_count = liquidity_rows.len();
    let index_liquidity_count = index_liquidity_rows.len();
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
        "liquidityRowsWritten": 0,
        "source": "onchain_evidence_payload",
        "detail": "raw V4 tables imported directly from in-memory evidence; JSON files are not read",
        "depthSnapshotWriter": "pool_manager",
        "depthSnapshotNote": "the depth snapshot table is not written by the evidence channel; its single writer is the Rust pool manager (TASK-172 / AC-221)",
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
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
            |row| row.get(0),
        )
        .map_err(|error| format!("liquidity count failed: {error}"))?;
    let before_pools: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = ?",
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
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
                params_from_iter(
                    sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                    ])
                    .iter(),
                ),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("zero liquidity query failed: {error}"))?;
        for row in rows {
            let pool_id =
                norm(&row.map_err(|error| format!("zero liquidity row failed: {error}"))?);
            if !pool_id.is_empty() {
                removable_pool_ids.insert(pool_id);
            }
        }
    }
    let deleted_zero = transaction
        .execute(
            "DELETE FROM v4_exchange_pool_liquidity_snapshots
             WHERE network = ? AND chain_id = ? AND protocol = ? AND has_liquidity = 0",
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
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
                params_from_iter(
                    sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                    ])
                    .iter(),
                ),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("unknown liquidity query failed: {error}"))?;
        for row in rows {
            let pool_id =
                norm(&row.map_err(|error| format!("unknown liquidity row failed: {error}"))?);
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
                    params_from_iter(
                        sql_params(&[
                            Value::from(network.to_string()),
                            Value::from(chain_id),
                            Value::from(protocol.to_string()),
                        ])
                        .iter(),
                    ),
                    |row| row.get::<_, Option<String>>(0),
                )
                .map_err(|error| format!("snapshot query failed: {error}"))?;
            let mut ids = BTreeSet::new();
            for row in rows {
                let pool_id = norm(&row.map_err(|error| format!("snapshot row failed: {error}"))?);
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
                params_from_iter(
                    sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                    ])
                    .iter(),
                ),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("pool query failed: {error}"))?;
        for row in rows {
            let pool_id = norm(&row.map_err(|error| format!("pool row failed: {error}"))?);
            if !pool_id.is_empty() && !snapshot_pool_ids.contains(&pool_id) {
                removable_pool_ids.insert(pool_id);
            }
        }
        deleted_unknown = transaction
            .execute(
                "DELETE FROM v4_exchange_pool_liquidity_snapshots
                 WHERE network = ? AND chain_id = ? AND protocol = ? AND has_liquidity IS NULL",
                params_from_iter(
                    sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                    ])
                    .iter(),
                ),
            )
            .map_err(|error| format!("unknown liquidity delete failed: {error}"))?;
    }
    // A pool whose fee is above the library ceiling does not belong in the
    // pool table. The ceiling is a **storage** rule, so the rows that
    // entered the table before the rule was enforced are removed here.
    //
    // The removal also drops the matching liquidity snapshot, because a
    // snapshot without its pool is an orphan that the pool index would keep
    // reporting as a coverage gap.
    let mut removed_by_fee = 0usize;
    {
        let mut statement = transaction
            .prepare(
                "SELECT pool_id FROM v4_exchange_pools
                 WHERE network = ? AND chain_id = ? AND protocol = ?
                   AND (fee IS NULL OR fee <= 0 OR fee > ?4
                        OR (fee & ?5) != 0)",
            )
            .map_err(|error| format!("library fee select failed: {error}"))?;
        let rows = statement
            .query_map(
                params_from_iter(
                    sql_params(&[
                        Value::from(network.to_string()),
                        Value::from(chain_id),
                        Value::from(protocol.to_string()),
                        Value::from(MAX_LIBRARY_FEE),
                        Value::from(DYNAMIC_FEE_FLAG),
                    ])
                    .iter(),
                ),
                |row| row.get::<_, Option<String>>(0),
            )
            .map_err(|error| format!("library fee query failed: {error}"))?;
        for row in rows {
            let pool_id = norm(&row.map_err(|error| format!("library fee row failed: {error}"))?);
            if !pool_id.is_empty() {
                removable_pool_ids.insert(pool_id);
            }
        }
    }
    let mut deleted_pools = 0usize;
    let removable_pool_ids = removable_pool_ids.into_iter().collect::<Vec<_>>();
    for chunk in removable_pool_ids.chunks(500) {
        let placeholders = vec!["?"; chunk.len()].join(",");
        let mut params = vec![
            Value::from(network.to_string()),
            Value::from(chain_id),
            Value::from(protocol.to_string()),
        ];
        params.extend(chunk.iter().cloned().map(Value::from));

        // The children have to go first: the closed-loop tables reference
        // the pool table, and the snapshot table references it as well. The
        // removal order is therefore deepest child first. Chunks stay below
        // SQLite's common 999-variable limit.
        for (table, column) in [
            ("v4_closed_loop_optimizations", "optimized_pool_id"),
            ("v4_closed_loop_optimizations", "original_pool_id"),
            ("v4_closed_loop_hops", "base_pool_id"),
            ("v4_closed_loop_hops", "pool_id"),
            ("v4_exchange_pool_liquidity_snapshots", "pool_id"),
        ] {
            let sql = format!(
                "DELETE FROM {table}
                 WHERE network = ? AND chain_id = ? AND protocol = ?
                   AND {column} IN ({placeholders})"
            );
            transaction
                .execute(&sql, params_from_iter(sql_params(&params).iter()))
                .map_err(|error| format!("{table} delete failed: {error}"))?;
        }
        let sql = format!(
            "DELETE FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = ?
               AND pool_id IN ({placeholders})"
        );
        deleted_pools += transaction
            .execute(&sql, params_from_iter(sql_params(&params).iter()))
            .map_err(|error| format!("pool delete failed: {error}"))?;
    }
    if !removable_pool_ids.is_empty() {
        removed_by_fee = removable_pool_ids.len();
    }
    let after_liquidity: i64 = transaction
        .query_row(
            count_sql,
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
            |row| row.get(0),
        )
        .map_err(|error| format!("after liquidity count failed: {error}"))?;
    let after_pools: i64 = transaction
        .query_row(
            "SELECT COUNT(*) FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = ?",
            params_from_iter(
                sql_params(&[
                    Value::from(network.to_string()),
                    Value::from(chain_id),
                    Value::from(protocol.to_string()),
                ])
                .iter(),
            ),
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
        "removedByLibraryFeeCount": removed_by_fee,
        "libraryFeeCeiling": MAX_LIBRARY_FEE,
        "afterLiquidityCount": after_liquidity,
        "afterPoolCount": after_pools,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    /// The pool library only holds pools the orchestrator may trade, so
    /// the fee ceiling is a **storage** rule and not a ranking rule.
    #[test]
    fn the_library_only_accepts_a_fixed_fee_inside_the_ceiling() {
        assert!(pool_enters_library(Some(500)));
        assert!(pool_enters_library(Some(30_000)));
        // Above the ceiling the pool is not tradeable, so it is not stored.
        assert!(!pool_enters_library(Some(30_001)));
        assert!(!pool_enters_library(Some(100_000)));
        // A dynamic-fee pool carries no reproducible rate.
        assert!(!pool_enters_library(Some(DYNAMIC_FEE_FLAG)));
        assert!(!pool_enters_library(Some(DYNAMIC_FEE_FLAG | 3000)));
        // A pool without a fee cannot be priced.
        assert!(!pool_enters_library(None));
        assert!(!pool_enters_library(Some(0)));
    }

    /// The chain scoped liquidity snapshot is the only row every consumer of
    /// a physical pool can reach.  The kernel verdict has to travel with it,
    /// otherwise a pool that is missing from an anchor scoped surface reads
    /// as an unresolved coverage gap instead of an admission decision.
    #[test]
    fn the_chain_scoped_depth_document_carries_the_kernel_verdict() {
        let document = attach_kernel_admission_verdict(
            json!({"label": "deep", "level": "deep"}),
            Some("deep"),
            Some(true),
        );
        assert_eq!(document.get("kernelAdmitted"), Some(&Value::Bool(true)));
        assert!(document.get("kernelAdmissionReason").is_some());
        // The measured facts stay untouched next to the verdict.
        assert_eq!(document.get("label"), Some(&Value::from("deep")));
    }

    /// An explicit zero liquidity is a rejection, whatever the label says.
    #[test]
    fn the_chain_scoped_depth_document_rejects_an_explicit_zero_liquidity() {
        let document = attach_kernel_admission_verdict(
            json!({"label": "deep"}),
            Some("deep"),
            Some(false),
        );
        assert_eq!(document.get("kernelAdmitted"), Some(&Value::Bool(false)));
        assert_eq!(
            document.get("kernelAdmissionReason"),
            Some(&Value::from("explicit_zero_liquidity"))
        );
    }

    /// A never measured liquidity stays a coverage gap, not a rejection.
    #[test]
    fn the_chain_scoped_depth_document_keeps_a_missing_liquidity_unresolved() {
        let document =
            attach_kernel_admission_verdict(json!({"label": "deep"}), Some("deep"), None);
        assert_eq!(document.get("kernelAdmitted"), Some(&Value::Bool(true)));
    }

    /// The tri-state liquidity observation must not collapse: a missing
    /// measurement and an explicit zero are different market facts.
    #[test]
    fn the_liquidity_observation_keeps_its_three_states() {
        assert_eq!(tri_bool(&Value::Null), None);
        assert_eq!(tri_bool(&Value::from(0)), Some(false));
        assert_eq!(tri_bool(&Value::from(1)), Some(true));
        assert_eq!(tri_bool(&Value::from("true")), Some(true));
        assert_eq!(tri_bool(&Value::from("no")), Some(false));
    }

    #[test]
    fn tri_converts_common_evidence_values() {
        assert_eq!(tri_value(&Value::from(true)), Some(Value::from(1)));
        assert_eq!(tri_value(&Value::from("1")), Some(Value::from(1)));
        assert_eq!(tri_value(&Value::from("no")), Some(Value::from(0)));
        assert_eq!(tri_value(&Value::Null), None);
    }

    #[test]
    fn clear_v4_exchange_evidence_delete_order_removes_pool_children_first() {
        let mut connection = Connection::open_in_memory().expect("open in-memory");
        connection
            .execute_batch(
                r#"
                CREATE TABLE chain_tokens (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    token_address TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, token_address)
                );
                CREATE TABLE dex_surface_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol)
                );
                CREATE TABLE v4_closed_loops (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL,
                    anchor_address TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, market_id, loop_key),
                    FOREIGN KEY (network, chain_id, anchor_address)
                        REFERENCES chain_tokens (network, chain_id, token_address)
                );
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    token0 TEXT NOT NULL,
                    token1 TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, pool_id),
                    FOREIGN KEY (network, chain_id, token0)
                        REFERENCES chain_tokens (network, chain_id, token_address),
                    FOREIGN KEY (network, chain_id, token1)
                        REFERENCES chain_tokens (network, chain_id, token_address)
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    materialization_batch TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id),
                    FOREIGN KEY (network, chain_id, protocol, pool_id)
                        REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_closed_loop_hops (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL,
                    hop_index INTEGER NOT NULL,
                    pool_id TEXT NOT NULL,
                    base_pool_id TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, market_id, loop_key, hop_index),
                    FOREIGN KEY (network, chain_id, protocol, market_id, loop_key)
                        REFERENCES v4_closed_loops (network, chain_id, protocol, market_id, loop_key),
                    FOREIGN KEY (network, chain_id, protocol, pool_id)
                        REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id),
                    FOREIGN KEY (network, chain_id, protocol, base_pool_id)
                        REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_closed_loop_optimizations (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL,
                    hop_index INTEGER NOT NULL,
                    optimization_id TEXT NOT NULL,
                    optimized_pool_id TEXT NOT NULL,
                    PRIMARY KEY (network, chain_id, protocol, market_id, loop_key, hop_index, optimization_id),
                    FOREIGN KEY (network, chain_id, protocol, market_id, loop_key, hop_index)
                        REFERENCES v4_closed_loop_hops (
                            network, chain_id, protocol, market_id, loop_key, hop_index
                        ),
                    FOREIGN KEY (network, chain_id, protocol, optimized_pool_id)
                        REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id)
                );
                "#,
            )
            .expect("build schema");
        connection
            .execute(
                "INSERT INTO chain_tokens (network, chain_id, token_address, token_symbol) VALUES ('n', 1, '0x1', 'A')",
                [],
            )
            .expect("insert token0");
        connection
            .execute(
                "INSERT INTO chain_tokens (network, chain_id, token_address, token_symbol) VALUES ('n', 1, '0x2', 'B')",
                [],
            )
            .expect("insert token1");
        for pool_id in ["0x0", "0x1"] {
            connection
                .execute(
                    "INSERT INTO v4_exchange_pools VALUES ('n', 1, 'p', ?, '0x1', '0x2')",
                    [pool_id],
                )
                .expect("insert pool");
            connection
                .execute(
                    "INSERT INTO v4_exchange_pool_liquidity_snapshots \
                 (network, chain_id, protocol, pool_id) \
                 VALUES ('n', 1, 'p', ?)",
                    [pool_id],
                )
                .expect("insert snapshot");
        }
        // TASK-172 / AC-221: a row the pool manager wrote.  It carries the
        // batch of the run that wrote it, so the evidence channel's clear
        // must leave it alone even though it clears its own rows.
        connection
            .execute(
                "INSERT INTO v4_exchange_pools VALUES ('n', 1, 'p', '0x2', '0x1', '0x2')",
                [],
            )
            .expect("insert pool manager pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots \
                 (network, chain_id, protocol, pool_id, materialization_batch) \
                 VALUES ('n', 1, 'p', '0x2', 'pool-manager-batch')",
                [],
            )
            .expect("insert pool manager snapshot");
        connection
            .execute(
                "INSERT INTO v4_closed_loops (network, chain_id, protocol, market_id, loop_key, anchor_address) VALUES ('n', 1, 'p', 'm', '0xloop', '0x1')",
                [],
            )
            .expect("insert loop");
        connection
            .execute(
                "INSERT INTO v4_closed_loop_hops (network, chain_id, protocol, market_id, loop_key, hop_index, pool_id, base_pool_id) VALUES ('n', 1, 'p', 'm', '0xloop', 0, '0x0', '0x1')",
                [],
            )
            .expect("insert hop");
        connection
            .execute(
                "INSERT INTO v4_closed_loop_optimizations (network, chain_id, protocol, market_id, loop_key, hop_index, optimization_id, optimized_pool_id) VALUES ('n', 1, 'p', 'm', '0xloop', 0, 'opt', '0x1')",
                [],
            )
            .expect("insert optimization");

        let transaction = connection.transaction().expect("begin transaction");
        clear_v4_exchange_evidence(&transaction, "n", 1, "p")
            .expect("clear did not follow FK order");
        transaction.commit().expect("commit clear");

        let pool_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM v4_exchange_pools WHERE network = 'n' AND chain_id = 1 AND protocol = 'p'",
                [],
                |row| row.get(0),
            )
            .expect("count pools");
        let loop_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM v4_closed_loops WHERE network = 'n' AND chain_id = 1 AND protocol = 'p'",
                [],
                |row| row.get(0),
            )
            .expect("count loops");
        // TASK-172 / AC-221: the pool rows this channel owns and that no
        // pool-manager snapshot references are gone.  The pool row the pool
        // manager's snapshot points at has to stay: the snapshot table
        // carries a foreign key onto it, and the pool manager only UPDATEs
        // a snapshot row, so without its pool row the manager could never
        // materialise a measurement again.
        assert_eq!(
            pool_count, 1,
            "only the pool row that carries a pool manager snapshot survives"
        );
        assert_eq!(loop_count, 0);

        // TASK-172 / AC-221: the evidence channel's own rows are gone, and
        // the pool manager's row survived the clear.  The evidence channel
        // does not own the depth snapshot table, so it must not wipe the
        // pool manager's materialised state -- but it must remove the rows
        // it wrote, because the snapshot table carries a foreign key onto
        // the pool table it deletes below.
        let snapshot_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM v4_exchange_pool_liquidity_snapshots \
                 WHERE network = 'n' AND chain_id = 1 AND protocol = 'p'",
                [],
                |row| row.get(0),
            )
            .expect("count snapshots");
        assert_eq!(
            snapshot_count, 1,
            "the clear must remove the evidence channel's own rows and keep \
             the pool manager's row"
        );
        let remaining_pool: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM v4_exchange_pools \
                 WHERE network = 'n' AND chain_id = 1 AND protocol = 'p' AND pool_id = '0x2'",
                [],
                |row| row.get(0),
            )
            .expect("count the pool manager pool");
        assert_eq!(
            remaining_pool, 1,
            "the pool manager's pool must survive the evidence clear"
        );
    }
}
