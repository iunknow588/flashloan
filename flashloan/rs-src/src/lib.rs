use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use serde_json::Value;

mod binance_market;
mod business_builder;
mod config;
mod evidence;
mod execution;
mod hyperrpc;
mod hypersync;
mod liquidity;
mod live_refresh;
mod migration;
mod native_pool_registry;
mod pool_index;
mod runtime_config;
mod runtime_core;
mod schema;
mod sqlite_read;
mod worker_pool;

fn default_database_path() -> String {
    std::env::var("FLASHLOAN_KV_PATH")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| {
            std::env::var("FLASHLOAN_DB_ROOT")
                .ok()
                .filter(|value| !value.trim().is_empty())
                .map(|value| {
                    format!(
                        "{}/local_store.sqlite3",
                        value.trim_end_matches(['/', '\\'])
                    )
                })
                .unwrap_or_else(|| {
                    let cwd = std::env::current_dir().unwrap_or_default();
                    cwd.join("flashloan")
                        .join("db")
                        .join("local_store.sqlite3")
                        .to_string_lossy()
                        .into_owned()
                })
        })
}

fn as_py_error(message: String) -> PyErr {
    PyRuntimeError::new_err(message)
}

#[pyfunction]
#[pyo3(signature = (chain_id, network, database_path))]
fn set_active_runtime(
    py: Python<'_>,
    chain_id: u64,
    network: String,
    database_path: String,
) -> PyObject {
    json_result_to_py(
        py,
        runtime_config::set_active_runtime(chain_id, network, database_path),
    )
}

#[pyfunction]
fn clear_active_runtime(py: Python<'_>) -> PyObject {
    json_result_to_py(py, runtime_config::clear_active_runtime())
}

#[pyfunction]
fn active_runtime(py: Python<'_>) -> PyObject {
    json_result_to_py(py, runtime_config::active_runtime())
}

#[pyfunction]
#[pyo3(signature = (chain_id=None, database_path=None))]
fn resolve_runtime_database(
    py: Python<'_>,
    chain_id: Option<u64>,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    match runtime_config::resolve_runtime_database(chain_id, database_path) {
        Ok((path, mut meta)) => {
            meta["databasePath"] = Value::String(path);
            Ok(json_result_to_py(py, meta))
        }
        Err(message) => Err(as_py_error(message)),
    }
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
#[pyo3(signature = (
    chain_id,
    network,
    database_path,
    protocol="uniswap_v4",
    broadcast_authority="legacy",
    candidate_authority="legacy",
    candidate_exchange_authority="legacy"
))]
fn runtime_core_register(
    py: Python<'_>,
    chain_id: i64,
    network: String,
    database_path: String,
    protocol: &str,
    broadcast_authority: &str,
    candidate_authority: &str,
    candidate_exchange_authority: &str,
) -> PyResult<PyObject> {
    let broadcast_authority = broadcast_authority
        .parse::<manager_contracts::TokenAuthorityMode>()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let candidate_authority = candidate_authority
        .parse::<manager_contracts::TokenAuthorityMode>()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let candidate_exchange_authority = candidate_exchange_authority
        .parse::<manager_contracts::TokenAuthorityMode>()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let core = runtime_core::RuntimeCore::register_with_all_authorities(
        chain_id,
        network,
        database_path,
        protocol,
        candidate_authority,
        broadcast_authority,
        candidate_exchange_authority,
    )
    .map_err(|error| as_py_error(error.to_string()))?;
    Ok(json_result_to_py(
        py,
        serde_json::to_value(core.status()).map_err(|error| as_py_error(error.to_string()))?,
    ))
}

#[pyfunction]
fn runtime_core_status(py: Python<'_>, chain_id: i64) -> PyResult<PyObject> {
    let core =
        runtime_core::RuntimeCore::get(chain_id).map_err(|error| as_py_error(error.to_string()))?;
    Ok(json_result_to_py(
        py,
        serde_json::to_value(core.status()).map_err(|error| as_py_error(error.to_string()))?,
    ))
}

#[pyfunction]
fn runtime_core_state(py: Python<'_>, chain_id: i64) -> PyResult<PyObject> {
    let core =
        runtime_core::RuntimeCore::get(chain_id).map_err(|error| as_py_error(error.to_string()))?;
    let snapshot = core
        .state()
        .map_err(|error| as_py_error(error.to_string()))?;
    Ok(json_result_to_py(
        py,
        serde_json::to_value(snapshot.as_ref()).map_err(|error| as_py_error(error.to_string()))?,
    ))
}

#[pyfunction]
#[pyo3(signature = (chain_id, refresh=false))]
fn runtime_core_manager_snapshot(
    py: Python<'_>,
    chain_id: i64,
    refresh: bool,
) -> PyResult<PyObject> {
    let core =
        runtime_core::RuntimeCore::get(chain_id).map_err(|error| as_py_error(error.to_string()))?;
    let snapshot = if refresh {
        core.refresh()
            .map_err(|error| as_py_error(error.to_string()))?
    } else {
        core.state()
            .map_err(|error| as_py_error(error.to_string()))?
    };
    Ok(json_result_to_py(
        py,
        serde_json::to_value(snapshot.as_ref()).map_err(|error| as_py_error(error.to_string()))?,
    ))
}

#[pyfunction]
#[pyo3(signature = (chain_id, command, request_id, idempotency_key, dry_run, options_json=None))]
fn runtime_core_command(
    py: Python<'_>,
    chain_id: i64,
    command: String,
    request_id: String,
    idempotency_key: String,
    dry_run: bool,
    options_json: Option<String>,
) -> PyResult<PyObject> {
    let core =
        runtime_core::RuntimeCore::get(chain_id).map_err(|error| as_py_error(error.to_string()))?;
    if command.trim().eq_ignore_ascii_case("manual_review") {
        if let Err(error) = runtime_core::validate_command_identity(&request_id, &idempotency_key) {
            return Ok(json_result_to_py(
                py,
                serde_json::json!({
                    "ok": false,
                    "command": command,
                    "code": error.code(),
                    "detail": error.to_string(),
                    "upstreamStatus": error.http_status(),
                    "requestId": request_id,
                    "idempotencyKey": idempotency_key,
                    "dryRun": dry_run,
                    "dataSource": "rust_runtime_core",
                }),
            ));
        }
        let status = core.status();
        if status.closed {
            return Ok(json_result_to_py(
                py,
                serde_json::json!({
                    "ok": false,
                    "command": command,
                    "code": "RUNTIME_CORE_CLOSED",
                    "detail": format!("RuntimeCore for chain {chain_id} is closed"),
                    "upstreamStatus": 503,
                    "requestId": request_id,
                    "idempotencyKey": idempotency_key,
                    "dryRun": dry_run,
                    "dataSource": "rust_runtime_core",
                }),
            ));
        }
        match native_pool_registry::execute_manual_review_command(
            py,
            &status.database_path,
            chain_id,
            options_json.as_deref(),
            dry_run,
        ) {
            Ok(mut result) => {
                let event_detail = serde_json::json!({
                    "groupKey": result.get("groupKey").cloned().unwrap_or(Value::Null),
                    "accepted": result.get("accepted").cloned().unwrap_or(Value::Null),
                    "transitionQueued": result
                        .get("transitionQueued")
                        .cloned()
                        .unwrap_or(Value::Null),
                    "processingAsync": result
                        .get("processingAsync")
                        .cloned()
                        .unwrap_or(Value::Null),
                });
                if let Some(object) = result.as_object_mut() {
                    object.insert(
                        "command".to_owned(),
                        Value::String("manual_review".to_owned()),
                    );
                    object.insert(
                        "version".to_owned(),
                        Value::String("rust_runtime_core_v1".to_owned()),
                    );
                    object.insert(
                        "requestId".to_owned(),
                        Value::String(request_id.trim().to_owned()),
                    );
                    object.insert(
                        "idempotencyKey".to_owned(),
                        Value::String(idempotency_key.trim().to_owned()),
                    );
                    object.insert("dryRun".to_owned(), Value::Bool(dry_run));
                    object.insert(
                        "dataSource".to_owned(),
                        Value::String("rust_runtime_core".to_owned()),
                    );
                }
                core.record_command_completed(
                    "manual_review",
                    &request_id,
                    &idempotency_key,
                    dry_run,
                    event_detail,
                );
                return Ok(json_result_to_py(py, result));
            }
            Err(error) => {
                return Ok(json_result_to_py(
                    py,
                    serde_json::json!({
                        "ok": false,
                        "command": command,
                        "code": error.code,
                        "detail": error.message,
                        "upstreamStatus": error.http_status,
                        "requestId": request_id,
                        "idempotencyKey": idempotency_key,
                        "dryRun": dry_run,
                        "dataSource": "rust_runtime_core",
                    }),
                ));
            }
        }
    }
    if command.trim().eq_ignore_ascii_case("manual_broadcast") {
        if let Err(error) = runtime_core::validate_command_identity(&request_id, &idempotency_key) {
            return Ok(json_result_to_py(
                py,
                serde_json::json!({
                    "ok": false,
                    "command": command,
                    "code": error.code(),
                    "detail": error.to_string(),
                    "upstreamStatus": error.http_status(),
                    "requestId": request_id,
                    "idempotencyKey": idempotency_key,
                    "dryRun": dry_run,
                    "dataSource": "rust_runtime_core",
                }),
            ));
        }
        let status = core.status();
        if status.closed {
            return Ok(json_result_to_py(
                py,
                serde_json::json!({
                    "ok": false,
                    "command": command,
                    "code": "RUNTIME_CORE_CLOSED",
                    "detail": format!("RuntimeCore for chain {chain_id} is closed"),
                    "upstreamStatus": 503,
                    "requestId": request_id,
                    "idempotencyKey": idempotency_key,
                    "dryRun": dry_run,
                    "dataSource": "rust_runtime_core",
                }),
            ));
        }
        match native_pool_registry::execute_manual_broadcast_command(
            py,
            &status.database_path,
            chain_id,
            options_json.as_deref(),
            dry_run,
        ) {
            Ok(mut result) => {
                if let Some(object) = result.as_object_mut() {
                    object.insert(
                        "command".to_owned(),
                        Value::String("manual_broadcast".to_owned()),
                    );
                    object.insert(
                        "version".to_owned(),
                        Value::String("rust_runtime_core_v1".to_owned()),
                    );
                    object.insert(
                        "requestId".to_owned(),
                        Value::String(request_id.trim().to_owned()),
                    );
                    object.insert(
                        "idempotencyKey".to_owned(),
                        Value::String(idempotency_key.trim().to_owned()),
                    );
                    object.insert("dryRun".to_owned(), Value::Bool(dry_run));
                    object.insert(
                        "dataSource".to_owned(),
                        Value::String("rust_runtime_core".to_owned()),
                    );
                }
                return Ok(json_result_to_py(py, result));
            }
            Err(error) => {
                return Ok(json_result_to_py(
                    py,
                    serde_json::json!({
                        "ok": false,
                        "command": command,
                        "code": error.code,
                        "detail": error.message,
                        "upstreamStatus": error.http_status,
                        "requestId": request_id,
                        "idempotencyKey": idempotency_key,
                        "dryRun": dry_run,
                        "dataSource": "rust_runtime_core",
                    }),
                ));
            }
        }
    }
    match core.execute_command(&command, &request_id, &idempotency_key, dry_run) {
        Ok(result) => Ok(json_result_to_py(py, result)),
        Err(error) => Ok(json_result_to_py(
            py,
            serde_json::json!({
                "ok": false,
                "command": command,
                "code": error.code(),
                "detail": error.to_string(),
                "upstreamStatus": error.http_status(),
                "requestId": request_id,
                "idempotencyKey": idempotency_key,
                "dryRun": dry_run,
                "dataSource": "rust_runtime_core",
            }),
        )),
    }
}

#[pyfunction]
#[pyo3(signature = (chain_id, view, limit=1000, options_json=None))]
fn runtime_core_manager_view(
    py: Python<'_>,
    chain_id: i64,
    view: String,
    limit: usize,
    options_json: Option<String>,
) -> PyResult<PyObject> {
    let core =
        runtime_core::RuntimeCore::get(chain_id).map_err(|error| as_py_error(error.to_string()))?;
    let result = match options_json.as_deref() {
        Some(options) => core.manager_view_with_options(&view, limit, Some(options)),
        None => core.manager_view(&view, limit),
    }
    .map_err(|error| as_py_error(error.to_string()))?;
    Ok(json_result_to_py(py, result))
}

#[pyfunction]
fn runtime_core_close(py: Python<'_>, chain_id: i64) -> PyResult<PyObject> {
    let result = runtime_core::RuntimeCore::close(chain_id)
        .map_err(|error| as_py_error(error.to_string()))?;
    Ok(json_result_to_py(
        py,
        serde_json::to_value(result).map_err(|error| as_py_error(error.to_string()))?,
    ))
}

#[pyfunction]
#[pyo3(signature = (database_path, chain_id=None, network=None, protocol=None, max_workers=16, maintenance_handler=None, maintenance_interval_seconds=600, binance_interval_seconds=300, binance_enabled=false, execution_workers=2, execution_queue_enabled=true))]
fn native_pool_get_or_create(
    py: Python<'_>,
    database_path: String,
    chain_id: Option<i64>,
    network: Option<String>,
    protocol: Option<String>,
    max_workers: usize,
    maintenance_handler: Option<Py<PyAny>>,
    maintenance_interval_seconds: u64,
    binance_interval_seconds: u64,
    binance_enabled: bool,
    execution_workers: usize,
    execution_queue_enabled: bool,
) -> PyResult<PyObject> {
    native_pool_registry::native_pool_get_or_create(
        py,
        database_path,
        chain_id,
        network,
        protocol,
        max_workers,
        maintenance_handler,
        maintenance_interval_seconds,
        binance_interval_seconds,
        binance_enabled,
        execution_workers,
        execution_queue_enabled,
    )
}

#[pyfunction]
#[pyo3(signature = (stop=false, chain_id=None, database_path=None))]
fn native_pool_registry_manage(
    py: Python<'_>,
    stop: bool,
    chain_id: Option<i64>,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    native_pool_registry::native_pool_registry_manage(py, stop, chain_id, database_path)
}

#[pyfunction]
fn native_pool_registry_status(py: Python<'_>) -> PyObject {
    native_pool_registry::native_pool_registry_status(py)
}

#[pyfunction]
fn current_schema_version(database_path: String) -> PyResult<String> {
    schema::current_schema_version(&database_path).map_err(as_py_error)
}

#[pyfunction]
fn ensure_database_schema(py: Python<'_>, database_path: String) -> PyResult<PyObject> {
    let payload = schema::ensure_database_schema(&database_path).map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
fn ensure_database_indexes(py: Python<'_>, database_path: String) -> PyResult<PyObject> {
    let payload = schema::ensure_database_indexes(&database_path).map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
fn migrate_business_direction_schema(py: Python<'_>, database_path: String) -> PyResult<PyObject> {
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
    let payload =
        live_refresh::load_v4_scan_state(&connection, &network, chain_id as i64, protocol)
            .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (network, chain_id, last_scanned_block=None, index_from_block=None, index_to_block=None, liquidity_scan_block=None, scanned_pool_count=None, pool_count=None, confirmed_liquidity_pool_count=None, explicit_zero_liquidity_pool_count=None, unscanned_pool_count=None, direction_candidate_limit=1, scan_mode="incremental", database_path=None, protocol="uniswap_v4"))]
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
            as_py_error(format!(
                "anchor_addresses_json must be a JSON object: {error}"
            ))
        })?;
    let universe = match token_universe_json {
        Some(raw) => {
            let values: Vec<String> = serde_json::from_str(&raw).map_err(|error| {
                as_py_error(format!("token_universe_json must be a JSON array: {error}"))
            })?;
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
    let runtime = tokio::runtime::Runtime::new()
        .map_err(|error| as_py_error(format!("tokio runtime failed: {error}")))?;
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
        let sorted_currency0 = currency0.as_deref().map(config::canonical_address);
        let sorted_currency1 = currency1.as_deref().map(config::canonical_address);
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
        let token0 = pool
            .get("token0")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase();
        let token1 = pool
            .get("token1")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase();
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
    let rows = sqlite_read::load_v4_exchange_pool_rows_for_view(
        &connection,
        &network,
        chain_id,
        &protocol,
    )
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
    let rows =
        sqlite_read::load_v4_exchange_pool_pair_counts(&connection, &network, chain_id, &protocol)
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
    let manager =
        sqlite_read::load_v4_exchange_pool_manager(&connection, &network, chain_id, &protocol)
            .map_err(as_py_error)?;
    Ok(json_result_to_py(
        py,
        serde_json::json!({"manager": manager}),
    ))
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
    Ok(json_result_to_py(
        py,
        serde_json::json!({"liquidity": rows}),
    ))
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
    let rows = sqlite_read::load_v4_pool_liquidity_rows_for_view(
        &connection,
        &network,
        chain_id,
        &protocol,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(
        py,
        serde_json::json!({"liquidity": rows}),
    ))
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
    let payload = sqlite_read::load_dex_candidate_pools(
        &connection,
        &network,
        chain_id,
        &protocol,
        &anchor_symbol,
    )
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
    let payload = sqlite_read::load_market_observation(
        &connection,
        &network,
        chain_id,
        &protocol,
        &anchor_symbol,
    )
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
    let payload =
        sqlite_read::load_v4_closed_loop(&connection, &network, chain_id, &protocol, &market_id)
            .map_err(as_py_error)?;
    Ok(json_result_to_py(py, payload))
}

#[pyfunction]
#[pyo3(signature = (source, payload_json, database_path=None, network=None, chain_id=56))]
fn append_bnb_market_observation(
    py: Python<'_>,
    source: String,
    payload_json: String,
    database_path: Option<String>,
    network: Option<String>,
    chain_id: u64,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let network = network.unwrap_or_else(|| "bnb".to_string());
    let payload: Value = serde_json::from_str(&payload_json)
        .map_err(|error| as_py_error(format!("BNB observation payload must be JSON: {error}")))?;
    let result = binance_market::append_bnb_market_observation(
        &path,
        &source,
        &network,
        chain_id as i64,
        &payload,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, result))
}

#[pyfunction]
#[pyo3(signature = (database_path=None, source=None, limit=200))]
fn load_bnb_market_observations(
    py: Python<'_>,
    database_path: Option<String>,
    source: Option<String>,
    limit: u64,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let result =
        binance_market::load_bnb_market_observations(&path, source.as_deref(), limit as i64)
            .map_err(as_py_error)?;
    Ok(json_result_to_py(py, result))
}

#[pyfunction]
#[pyo3(signature = (symbol, price_usd, observed_at, payload_json, database_path=None))]
fn append_bnb_market_snapshot(
    py: Python<'_>,
    symbol: String,
    price_usd: String,
    observed_at: String,
    payload_json: String,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let payload: Value = serde_json::from_str(&payload_json)
        .map_err(|error| as_py_error(format!("BNB snapshot payload must be JSON: {error}")))?;
    let result = binance_market::append_bnb_market_snapshot(
        &path,
        &symbol,
        &price_usd,
        &observed_at,
        &payload,
    )
    .map_err(as_py_error)?;
    Ok(json_result_to_py(py, result))
}

#[pyfunction]
#[pyo3(signature = (database_path=None, symbol=None, limit=100))]
fn load_bnb_market_snapshots(
    py: Python<'_>,
    database_path: Option<String>,
    symbol: Option<String>,
    limit: u64,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let result = binance_market::load_bnb_market_snapshots(&path, symbol.as_deref(), limit as i64)
        .map_err(as_py_error)?;
    Ok(json_result_to_py(py, result))
}

#[pyfunction]
#[pyo3(signature = (database_path=None))]
fn collect_bnb_market_snapshot(
    py: Python<'_>,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let path = database_path.unwrap_or_else(default_database_path);
    let result = binance_market::collect_bnb_market_snapshot(&path).map_err(as_py_error)?;
    Ok(json_result_to_py(py, result))
}

#[pymodule]
fn flashloan_rs(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(set_active_runtime, module)?)?;
    module.add_function(wrap_pyfunction!(clear_active_runtime, module)?)?;
    module.add_function(wrap_pyfunction!(active_runtime, module)?)?;
    module.add_function(wrap_pyfunction!(resolve_runtime_database, module)?)?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_function(wrap_pyfunction!(runtime_core_register, module)?)?;
    module.add_function(wrap_pyfunction!(runtime_core_status, module)?)?;
    module.add_function(wrap_pyfunction!(runtime_core_state, module)?)?;
    module.add_function(wrap_pyfunction!(runtime_core_manager_snapshot, module)?)?;
    module.add_function(wrap_pyfunction!(runtime_core_command, module)?)?;
    module.add_function(wrap_pyfunction!(runtime_core_manager_view, module)?)?;
    module.add_function(wrap_pyfunction!(runtime_core_close, module)?)?;
    module.add_function(wrap_pyfunction!(native_pool_get_or_create, module)?)?;
    module.add_function(wrap_pyfunction!(native_pool_registry_manage, module)?)?;
    module.add_function(wrap_pyfunction!(native_pool_registry_status, module)?)?;
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
    module.add_function(wrap_pyfunction!(
        load_v4_exchange_pool_rows_for_view,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(load_v4_exchange_pool_pair_counts, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_exchange_pool_manager, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_pool_liquidity, module)?)?;
    module.add_function(wrap_pyfunction!(
        load_v4_pool_liquidity_rows_for_view,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(load_dex_candidate_pools, module)?)?;
    module.add_function(wrap_pyfunction!(load_market_observation, module)?)?;
    module.add_function(wrap_pyfunction!(load_v4_closed_loop, module)?)?;
    module.add_function(wrap_pyfunction!(append_bnb_market_observation, module)?)?;
    module.add_function(wrap_pyfunction!(load_bnb_market_observations, module)?)?;
    module.add_function(wrap_pyfunction!(append_bnb_market_snapshot, module)?)?;
    module.add_function(wrap_pyfunction!(load_bnb_market_snapshots, module)?)?;
    module.add_function(wrap_pyfunction!(collect_bnb_market_snapshot, module)?)?;
    module.add_function(wrap_pyfunction!(
        execution::resolve_private_key_hex,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        execution::sign_typed_transaction_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        execution::build_v4_quote_calldata,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        execution::build_v4_official_calldata,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        execution::preview_v4_arb_best_amount,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        execution::run_v4_arb_best_amount_ladder,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(execution::preview_v4_best_pool, module)?)?;
    module.add_function(wrap_pyfunction!(
        execution::preview_v4_best_pool_detailed,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(execution::read_v4_pool_states, module)?)?;
    module.add_function(wrap_pyfunction!(
        execution::run_v4_preview_and_ladder,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        execution::broadcast_v4_best_pools,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        execution::quote_v4_exact_input_single,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(execution::submit_v4_route, module)?)?;
    module.add_function(wrap_pyfunction!(execution::load_v4_receipt, module)?)?;
    module.add_function(wrap_pyfunction!(execution::get_v4_pending_nonce, module)?)?;
    module.add_class::<worker_pool::ClosedLoopWorkerPool>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::runtime_core_register;
    use pyo3::prelude::*;
    use pyo3::types::PyModule;

    #[test]
    fn runtime_core_register_six_argument_call_defaults_candidate_exchange_to_legacy() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let directory = tempfile::tempdir().expect("temporary directory");
            let database_path = directory.path().join("manager.sqlite3");
            let module = PyModule::new(py, "runtime_core_register_compat").expect("module");
            module
                .add_function(
                    wrap_pyfunction!(runtime_core_register, &module)
                        .expect("wrapped runtime_core_register"),
                )
                .expect("registered runtime_core_register");

            let status = module
                .getattr("runtime_core_register")
                .expect("runtime_core_register")
                .call1((
                    46631_i64,
                    "robinhood",
                    database_path.to_string_lossy().into_owned(),
                    "uniswap_v4",
                    "legacy",
                    "legacy",
                ))
                .expect("six-argument register");
            let candidate_exchange_authority: String = status
                .get_item("candidateExchangeAuthority")
                .expect("candidateExchangeAuthority")
                .extract()
                .expect("candidate exchange authority string");
            assert_eq!(candidate_exchange_authority, "legacy");

            super::runtime_core::RuntimeCore::close(46631).expect("close");
        });
    }
}
