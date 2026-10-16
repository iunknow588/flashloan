use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use serde_json::Value;

mod config;
mod business_builder;
mod evidence;
mod hyperrpc;
mod hypersync;
mod liquidity;
mod live_refresh;
mod migration;
mod pool_index;
mod schema;
mod sqlite_read;

fn default_database_path() -> String {
    std::env::var("FLASHLOAN_KV_PATH")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| {
            std::env::var("FLASHLOAN_RUNTIME_DIR")
                .ok()
                .filter(|value| !value.trim().is_empty())
                .map(|value| format!("{}/local_store.sqlite3", value.trim_end_matches(['/', '\\'])))
                .unwrap_or_else(|| {
                    let cwd = std::env::current_dir().unwrap_or_default();
                    cwd.join("flashloan")
                        .join("src")
                        .join("runtime")
                        .join("local_store.sqlite3")
                        .to_string_lossy()
                        .into_owned()
                })
        })
}

fn as_py_error(message: String) -> PyErr {
    PyRuntimeError::new_err(message)
}

fn json_to_py(py: Python<'_>, value: Value) -> PyObject {
    let encoded = serde_json::to_string(&value).unwrap_or_else(|_| "null".to_string());
    pyo3::types::PyModule::import(py, "json")
        .and_then(|module| module.call_method1("loads", (encoded,)))
        .unwrap_or_else(|_| py.None().into_bound(py))
        .unbind()
}

fn json_result_to_py(py: Python<'_>, value: Value) -> PyObject {
    json_to_py(py, value)
}

#[pyfunction]
fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[pyfunction]
fn current_schema_version(database_path: String) -> PyResult<String> {
    schema::current_schema_version(&database_path).map_err(as_py_error)
}

#[pyfunction]
fn ensure_database_schema(
    py: Python<'_>,
    database_path: String,
) -> PyResult<PyObject> {
    let payload = schema::ensure_database_schema(&database_path).map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
fn ensure_database_indexes(
    py: Python<'_>,
    database_path: String,
) -> PyResult<PyObject> {
    let payload = schema::ensure_database_indexes(&database_path).map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
fn migrate_business_direction_schema(
    py: Python<'_>,
    database_path: String,
) -> PyResult<PyObject> {
    let payload = schema::migrate_direction_schema(&database_path).map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, database_path=None, protocol="uniswap_v4"))]
fn load_v4_scan_state(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    database_path: Option<String>,
    protocol: &str,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = schema::open_rw(&path).map_err(as_py_error)?;
    let payload = live_refresh::load_v4_scan_state(
        &connection,
        &network,
        chain_id as i64,
        protocol,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, last_scanned_block=None, index_from_block=None, index_to_block=None, liquidity_scan_block=None, scanned_pool_count=None, pool_count=None, confirmed_liquidity_pool_count=None, explicit_zero_liquidity_pool_count=None, unscanned_pool_count=None, direction_candidate_limit=5, scan_mode="incremental", database_path=None, protocol="uniswap_v4"))]
fn update_v4_scan_state(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    last_scanned_block: Option<u64>,
    index_from_block: Option<u64>,
    index_to_block: Option<u64>,
    liquidity_scan_block: Option<u64>,
    scanned_pool_count: Option<u64>,
    pool_count: Option<u64>,
    confirmed_liquidity_pool_count: Option<u64>,
    explicit_zero_liquidity_pool_count: Option<u64>,
    unscanned_pool_count: Option<u64>,
    direction_candidate_limit: u64,
    scan_mode: &str,
    database_path: Option<String>,
    protocol: &str,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = schema::open_rw(&path).map_err(as_py_error)?;
    let to_i64 = |value: Option<u64>| value.map(|item| item as i64);
    let payload = live_refresh::update_v4_scan_state(
        &connection,
        &network,
        chain_id as i64,
        protocol,
        to_i64(last_scanned_block),
        to_i64(index_from_block),
        to_i64(index_to_block),
        to_i64(liquidity_scan_block),
        to_i64(scanned_pool_count),
        to_i64(pool_count),
        to_i64(confirmed_liquidity_pool_count),
        to_i64(explicit_zero_liquidity_pool_count),
        to_i64(unscanned_pool_count),
        direction_candidate_limit as i64,
        scan_mode,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, onchain_payload, database_path=None, protocol="uniswap_v4"))]
fn merge_v4_pool_index(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    onchain_payload: String,
    database_path: Option<String>,
    protocol: &str,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = schema::open_rw(&path).map_err(as_py_error)?;
    let onchain_payload: Value =
        serde_json::from_str(&onchain_payload).map_err(|error| as_py_error(error.to_string()))?;
    let payload = live_refresh::merge_v4_pool_index(
        &connection,
        &network,
        chain_id as i64,
        protocol,
        &onchain_payload,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, anchor_scope, market_id, anchors_json, anchor_addresses_json, token_universe_json=None, database_path=None))]
fn build_v4_anchor_data(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    anchor_scope: String,
    market_id: String,
    anchors_json: String,
    anchor_addresses_json: String,
    token_universe_json: Option<String>,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let mut connection = schema::open_rw(&path).map_err(as_py_error)?;
    let anchors: Vec<String> = serde_json::from_str(&anchors_json)
        .map_err(|error| as_py_error(format!("anchors_json must be a JSON array: {error}")))?;
    let addresses: std::collections::BTreeMap<String, String> =
        serde_json::from_str(&anchor_addresses_json).map_err(|error| {
            as_py_error(format!("anchor_addresses_json must be a JSON object: {error}"))
        })?;
    let universe = match token_universe_json {
        Some(raw) => {
            let values: Vec<String> = serde_json::from_str(&raw)
                .map_err(|error| as_py_error(format!("token_universe_json must be a JSON array: {error}")))?;
            Some(values)
        }
        None => None,
    };
    let payload = business_builder::build_all_anchor_data(
        &mut connection,
        &network,
        chain_id as i64,
        &protocol,
        &anchor_scope,
        &market_id,
        &anchors,
        &addresses,
        universe.as_deref(),
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (database_path, network, chain_id, protocol, token_rows_json, market_rows_json, pool_rows_json, liquidity_rows_json, index_liquidity_rows_json, replace, initialize_log_count))]
fn import_v4_evidence(
    py: Python<'_>,
    database_path: String,
    network: String,
    chain_id: u64,
    protocol: String,
    token_rows_json: String,
    market_rows_json: String,
    pool_rows_json: String,
    liquidity_rows_json: String,
    index_liquidity_rows_json: String,
    replace: bool,
    initialize_log_count: u64,
) -> PyResult<PyObject> {
    let parse_rows = |raw: &str| -> PyResult<Vec<Value>> {
        serde_json::from_str(raw)
            .map_err(|error| as_py_error(format!("evidence rows must be a JSON array: {error}")))
    };
    let token_rows = parse_rows(&token_rows_json)?;
    let market_rows = parse_rows(&market_rows_json)?;
    let pool_rows = parse_rows(&pool_rows_json)?;
    let liquidity_rows = parse_rows(&liquidity_rows_json)?;
    let index_liquidity_rows = parse_rows(&index_liquidity_rows_json)?;
    let payload = evidence::import_v4_evidence_payloads(
        &database_path,
        &network,
        chain_id as i64,
        &protocol,
        &token_rows,
        &market_rows,
        &pool_rows,
        &liquidity_rows,
        &index_liquidity_rows,
        replace,
        initialize_log_count as i64,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (database_path, network, chain_id, protocol, delete_unknown=false))]
fn prune_v4_evidence(
    py: Python<'_>,
    database_path: String,
    network: String,
    chain_id: u64,
    protocol: String,
    delete_unknown: bool,
) -> PyResult<PyObject> {
    let payload = evidence::prune_v4_non_executable_evidence(
        &database_path,
        &network,
        chain_id as i64,
        &protocol,
        delete_unknown,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
fn initialize_topic() -> PyResult<String> {
    Ok(pool_index::initialize_topic())
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, pool_manager, data_source="hyperrpc", from_block=0, to_block=None, database_path=None, currency0=None, currency1=None))]
fn build_v4_pool_index(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    pool_manager: String,
    data_source: &str,
    from_block: u64,
    to_block: Option<u64>,
    database_path: Option<String>,
    currency0: Option<String>,
    currency1: Option<String>,
) -> PyResult<PyObject> {
    let runtime = tokio::runtime::Runtime::new().map_err(|error| as_py_error(format!("tokio runtime failed: {error}")))?;
    let payload = runtime.block_on(async move {
        let source = data_source.to_lowercase();
        let config = config::ChainDataSourceConfig::from_env(&network, chain_id, &pool_manager)
            .map_err(as_py_error)?;
        let client = reqwest::Client::builder()
            .build()
            .map_err(|error| as_py_error(format!("HTTP client failed: {error}")))?;
        let latest = match to_block {
            Some(value) => value,
            None => hyperrpc::get_block_number(&client, &config.hyperrpc_url)
                .await
                .map_err(as_py_error)?,
        };
        let topic = pool_index::initialize_topic();
        let sorted_currency0 = currency0
            .as_deref()
            .map(config::canonical_address);
        let sorted_currency1 = currency1
            .as_deref()
            .map(config::canonical_address);
        let (filter0, filter1) = match (sorted_currency0, sorted_currency1) {
            (Some(first), Some(second)) if first == second => {
                return Err(as_py_error(
                    "currency0 and currency1 must differ for filtered pool scan".to_string(),
                ))
            }
            (Some(first), Some(second)) if first > second => (Some(second), Some(first)),
            (first, second) => (first, second),
        };
        let logs = if source == "hypersync" {
            hypersync::query_all_logs(
                &client,
                &config.hypersync_url,
                &config.envio_api_token,
                &config.pool_manager,
                &topic,
                filter0.as_deref(),
                filter1.as_deref(),
                from_block,
                latest,
            )
            .await
            .map_err(as_py_error)?
        } else {
            hyperrpc::get_logs_filtered(
                &client,
                &config.hyperrpc_url,
                &config.pool_manager,
                &topic,
                filter0.as_deref(),
                filter1.as_deref(),
                from_block,
                latest,
            )
            .await
            .map_err(as_py_error)?
        };
        let mut rows = Vec::with_capacity(logs.len());
        let mut pool_ids = std::collections::BTreeSet::new();
        for log in logs {
            match pool_index::decode_initialize_log(&log, &config.pool_manager) {
                Ok(row) => {
                    pool_ids.insert(row.pool_id.clone());
                    rows.push(pool_index::row_to_json(&row, &config.pool_manager));
                }
                Err(error) => {
                    return Err(as_py_error(format!(
                        "Initialize log decode failed at block {}: {error}",
                        log.block_number
                    )))
                }
            }
        }
        let path = database_path.unwrap_or_else(default_database_path);
        if path.is_empty() {
            return Err(as_py_error("SQLite database path is empty".to_string()));
        }
        Ok::<Value, PyErr>(serde_json::json!({
            "ok": true,
            "network": network,
            "chainId": chain_id,
            "protocol": "uniswap_v4",
            "dataSource": source,
            "fromBlock": from_block,
            "toBlock": latest,
            "initializeLogCount": rows.len(),
            "poolCount": pool_ids.len(),
            "pools": rows,
            "databasePath": path,
        }))
    })?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, token_a, token_b, database_path=None))]
fn find_pools_between_tokens(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    token_a: String,
    token_b: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let mut first = config::canonical_address(&token_a);
    let mut second = config::canonical_address(&token_b);
    if first == second {
        return Err(PyValueError::new_err("token_a and token_b must differ"));
    }
    if first > second {
        std::mem::swap(&mut first, &mut second);
    }
    let pools = sqlite_read::load_v4_exchange_pools(&connection, &network, chain_id, "uniswap_v4")
        .map_err(as_py_error)?;
    let mut matched = Vec::new();
    for pool in pools {
        let token0 = pool.get("token0").and_then(Value::as_str).unwrap_or_default().to_lowercase();
        let token1 = pool.get("token1").and_then(Value::as_str).unwrap_or_default().to_lowercase();
        if token0 == first && token1 == second {
            matched.push(pool);
        }
    }
    let payload = serde_json::json!({
        "ok": true,
        "network": network,
        "chainId": chain_id,
        "tokenA": token_a,
        "tokenB": token_b,
        "currency0": first,
        "currency1": second,
        "poolCount": matched.len(),
        "pools": matched,
    });
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, database_path=None))]
fn load_v4_exchange_pools(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let rows = sqlite_read::load_v4_exchange_pools(&connection, &network, chain_id, &protocol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, serde_json::json!({"pools": rows})))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, database_path=None))]
fn load_v4_exchange_pool_rows_for_view(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let rows = sqlite_read::load_v4_exchange_pool_rows_for_view(&connection, &network, chain_id, &protocol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, serde_json::json!({"pools": rows})))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, database_path=None))]
fn load_v4_exchange_pool_pair_counts(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let rows = sqlite_read::load_v4_exchange_pool_pair_counts(&connection, &network, chain_id, &protocol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, serde_json::json!({"pairs": rows})))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, database_path=None))]
fn load_v4_exchange_pool_manager(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let manager = sqlite_read::load_v4_exchange_pool_manager(&connection, &network, chain_id, &protocol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, serde_json::json!({"manager": manager})))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, database_path=None))]
fn load_v4_pool_liquidity(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let rows = sqlite_read::load_v4_pool_liquidity(&connection, &network, chain_id, &protocol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, serde_json::json!({"liquidity": rows})))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, database_path=None))]
fn load_v4_pool_liquidity_rows_for_view(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let rows = sqlite_read::load_v4_pool_liquidity_rows_for_view(&connection, &network, chain_id, &protocol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, serde_json::json!({"liquidity": rows})))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, anchor_symbol, database_path=None))]
fn load_dex_candidate_pools(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    anchor_symbol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let payload = sqlite_read::load_dex_candidate_pools(&connection, &network, chain_id, &protocol, &anchor_symbol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, anchor_symbol, database_path=None))]
fn load_market_observation(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    anchor_symbol: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let payload = sqlite_read::load_market_observation(&connection, &network, chain_id, &protocol, &anchor_symbol)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, protocol, market_id, database_path=None))]
fn load_v4_closed_loop(
    py: Python<'_>,
    network: String,
    chain_id: u64,
    protocol: String,
    market_id: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let connection = sqlite_read::open_readonly(&path).map_err(as_py_error)?;
    let payload = sqlite_read::load_v4_closed_loop(&connection, &network, chain_id, &protocol, &market_id)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pymodule]
fn flashloan_rs(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_function(wrap_pyfunction!(current_schema_version, module)?)?;
    module.add_function(wrap_pyfunction!(ensure_database_schema, module)?)?;
    module.add_function(wrap_pyfunction!(ensure_database_indexes, module)?)?;
    module.add_function(wrap_pyfunction!(migrate_business_direction_schema, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_scan_state, module)?)?;
    module.add_function(wrap_pyfunction!(update_v4_scan_state, module)?)?;
    module.add_function(wrap_pyfunction!(merge_v4_pool_index, module)?)?;
    module.add_function(wrap_pyfunction!(build_v4_anchor_data, module)?)?;
    module.add_function(wrap_pyfunction!(import_v4_evidence, module)?)?;
    module.add_function(wrap_pyfunction!(prune_v4_evidence, module)?)?;
    module.add_function(wrap_pyfunction!(initialize_topic, module)?)?;
    module.add_function(wrap_pyfunction!(build_v4_pool_index, module)?)?;
    module.add_function(wrap_pyfunction!(find_pools_between_tokens, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_exchange_pools, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_exchange_pool_rows_for_view, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_exchange_pool_pair_counts, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_exchange_pool_manager, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_pool_liquidity, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_pool_liquidity_rows_for_view, module)?)?;
    module.add_function(wrap_pyfunction!(load_dex_candidate_pools, module)?)?;
    module.add_function(wrap_pyfunction!(load_market_observation, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_closed_loop, module)?)?;
    Ok(())
}
