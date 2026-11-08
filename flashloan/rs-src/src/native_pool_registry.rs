use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard, OnceLock};

use pyo3::prelude::*;
use pyo3::types::PyAny;
use pyo3::Py;
use serde_json::{json, Value};

use crate::json_result_to_py;
use crate::worker_pool::ClosedLoopWorkerPool;

struct NativePoolEntry {
    chain_id: Option<i64>,
    network: Option<String>,
    protocol: Option<String>,
    database_path: String,
    pool: Py<ClosedLoopWorkerPool>,
    created_at: String,
}

impl Clone for NativePoolEntry {
    fn clone(&self) -> Self {
        // Py refs are process-owned; Rust tests do not clone outside the GIL.
        unreachable!("NativePoolEntry must be removed from the registry, not cloned")
    }
}

static NATIVE_POOL_REGISTRY: OnceLock<Mutex<HashMap<String, NativePoolEntry>>> = OnceLock::new();

fn registry_lock() -> MutexGuard<'static, HashMap<String, NativePoolEntry>> {
    NATIVE_POOL_REGISTRY
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

pub(crate) struct NativePoolCommandError {
    pub code: &'static str,
    pub message: String,
    pub http_status: u16,
}

impl NativePoolCommandError {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            code: "INVALID_REQUEST",
            message: message.into(),
            http_status: 400,
        }
    }

    fn unavailable(message: impl Into<String>) -> Self {
        Self {
            code: "RUNTIME_CORE_NATIVE_POOL_UNAVAILABLE",
            message: message.into(),
            http_status: 503,
        }
    }

    fn scope_mismatch(message: impl Into<String>) -> Self {
        Self {
            code: "RUNTIME_CORE_SCOPE_MISMATCH",
            message: message.into(),
            http_status: 409,
        }
    }

    fn transition_failed(message: impl Into<String>) -> Self {
        let message = message.into();
        let normalized = message.to_ascii_lowercase();
        let (code, http_status) = if normalized.contains("closed_loop_group_not_found") {
            ("CLOSED_LOOP_GROUP_NOT_FOUND", 404)
        } else if normalized.contains("closed_loop_group_busy") {
            ("CLOSED_LOOP_GROUP_BUSY", 409)
        } else if normalized.contains("execution_task_not_found") {
            ("EXECUTION_TASK_NOT_FOUND", 404)
        } else if normalized.contains("execution_task_not_waiting") {
            ("EXECUTION_TASK_NOT_WAITING", 409)
        } else if normalized.contains("broadcast executor is not enabled") {
            ("BROADCAST_EXECUTOR_UNAVAILABLE", 503)
        } else if normalized.contains("worker pool is stopped")
            || normalized.contains("native pool")
            || normalized.contains("not configured")
        {
            ("RUNTIME_CORE_NATIVE_POOL_UNAVAILABLE", 503)
        } else {
            ("RUNTIME_CORE_NATIVE_COMMAND_FAILED", 503)
        };
        Self {
            code,
            message,
            http_status,
        }
    }
}

pub(crate) fn execute_manual_review_command(
    py: Python<'_>,
    database_path: &str,
    chain_id: i64,
    options_json: Option<&str>,
    dry_run: bool,
) -> Result<Value, NativePoolCommandError> {
    let options = match options_json {
        Some(raw) if !raw.trim().is_empty() => {
            serde_json::from_str::<Value>(raw).map_err(|error| {
                NativePoolCommandError::invalid(format!("invalid options JSON: {error}"))
            })?
        }
        _ => json!({}),
    };
    let object = options
        .as_object()
        .ok_or_else(|| NativePoolCommandError::invalid("command options must be an object"))?;
    if let Some(field) = object.keys().find(|field| {
        !matches!(
            field.as_str(),
            "groupKey" | "routeSymbols" | "routeAddresses"
        )
    }) {
        return Err(NativePoolCommandError::invalid(format!(
            "{field} is not supported by manual_review"
        )));
    }
    let group_key = object
        .get("groupKey")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| NativePoolCommandError::invalid("groupKey is required"))?
        .to_lowercase();
    for field in ["routeSymbols", "routeAddresses"] {
        if let Some(value) = object.get(field) {
            let rows = value.as_array().ok_or_else(|| {
                NativePoolCommandError::invalid(format!("{field} must be an array"))
            })?;
            if rows.iter().any(|row| row.as_str().is_none()) {
                return Err(NativePoolCommandError::invalid(format!(
                    "{field} must contain only strings"
                )));
            }
        }
    }

    let key = database_path.trim().to_lowercase();
    if key.is_empty() {
        return Err(NativePoolCommandError::invalid("databasePath is required"));
    }
    let pool = {
        let registry = registry_lock();
        let entry = registry.get(&key).ok_or_else(|| {
            NativePoolCommandError::unavailable(format!(
                "native pool is not registered for database path {database_path}"
            ))
        })?;
        if entry
            .chain_id
            .is_some_and(|registered| registered != chain_id)
        {
            return Err(NativePoolCommandError::scope_mismatch(format!(
                "native pool chain id {} does not match RuntimeCore chain id {chain_id}",
                entry.chain_id.unwrap_or_default()
            )));
        }
        entry.pool.clone_ref(py)
    };

    let transition = pool
        .into_bound(py)
        .call_method1(
            "wake_waiting_for_review",
            (group_key.clone(), Option::<String>::None, dry_run),
        )
        .map_err(|error| NativePoolCommandError::transition_failed(error.to_string()))?;
    let encoded = py
        .import("json")
        .and_then(|module| module.call_method1("dumps", (transition,)))
        .and_then(|value| value.extract::<String>())
        .map_err(|error| {
            NativePoolCommandError::transition_failed(format!(
                "native review transition returned non-JSON output: {error}"
            ))
        })?;
    let mut result = serde_json::from_str::<Value>(&encoded).map_err(|error| {
        NativePoolCommandError::transition_failed(format!(
            "native review transition returned invalid JSON: {error}"
        ))
    })?;
    let result_object = result.as_object_mut().ok_or_else(|| {
        NativePoolCommandError::transition_failed("native review transition must return an object")
    })?;
    result_object.insert("ok".to_owned(), Value::Bool(true));
    result_object.insert(
        "mode".to_owned(),
        Value::String("native_closed_loop_group_manual_review".to_owned()),
    );
    result_object.insert("chainId".to_owned(), json!(chain_id));
    result_object.insert("groupKey".to_owned(), Value::String(group_key));
    let accepted = result_object
        .get("accepted")
        .cloned()
        .unwrap_or(Value::Bool(true));
    result_object.insert("accepted".to_owned(), accepted);
    result_object.insert("processingAsync".to_owned(), Value::Bool(!dry_run));
    result_object.insert("stateReadRequired".to_owned(), Value::Bool(true));
    Ok(result)
}

pub(crate) fn execute_manual_broadcast_command(
    py: Python<'_>,
    database_path: &str,
    chain_id: i64,
    options_json: Option<&str>,
    dry_run: bool,
) -> Result<Value, NativePoolCommandError> {
    let options = match options_json {
        Some(raw) if !raw.trim().is_empty() => {
            serde_json::from_str::<Value>(raw).map_err(|error| {
                NativePoolCommandError::invalid(format!("invalid options JSON: {error}"))
            })?
        }
        _ => json!({}),
    };
    let object = options
        .as_object()
        .ok_or_else(|| NativePoolCommandError::invalid("command options must be an object"))?;
    if let Some(field) = object.keys().find(|field| {
        !matches!(
            field.as_str(),
            "groupKey" | "routeSymbols" | "routeAddresses"
        )
    }) {
        return Err(NativePoolCommandError::invalid(format!(
            "{field} is not supported by manual_broadcast"
        )));
    }
    let group_key = object
        .get("groupKey")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| NativePoolCommandError::invalid("groupKey is required"))?
        .to_lowercase();
    for field in ["routeSymbols", "routeAddresses"] {
        if let Some(value) = object.get(field) {
            let rows = value.as_array().ok_or_else(|| {
                NativePoolCommandError::invalid(format!("{field} must be an array"))
            })?;
            if rows.iter().any(|row| row.as_str().is_none()) {
                return Err(NativePoolCommandError::invalid(format!(
                    "{field} must contain only strings"
                )));
            }
        }
    }

    let key = database_path.trim().to_lowercase();
    if key.is_empty() {
        return Err(NativePoolCommandError::invalid("databasePath is required"));
    }
    let pool = {
        let registry = registry_lock();
        let entry = registry.get(&key).ok_or_else(|| {
            NativePoolCommandError::unavailable(format!(
                "native pool is not registered for database path {database_path}"
            ))
        })?;
        if entry
            .chain_id
            .is_some_and(|registered| registered != chain_id)
        {
            return Err(NativePoolCommandError::scope_mismatch(format!(
                "native pool chain id {} does not match RuntimeCore chain id {chain_id}",
                entry.chain_id.unwrap_or_default()
            )));
        }
        entry.pool.clone_ref(py)
    };

    let transition = pool
        .into_bound(py)
        .call_method1(
            "wake_waiting_for_broadcast",
            (group_key.clone(), Option::<String>::None, dry_run),
        )
        .map_err(|error| NativePoolCommandError::transition_failed(error.to_string()))?;
    let encoded = py
        .import("json")
        .and_then(|module| module.call_method1("dumps", (transition,)))
        .and_then(|value| value.extract::<String>())
        .map_err(|error| {
            NativePoolCommandError::transition_failed(format!(
                "native broadcast transition returned non-JSON output: {error}"
            ))
        })?;
    let mut result = serde_json::from_str::<Value>(&encoded).map_err(|error| {
        NativePoolCommandError::transition_failed(format!(
            "native broadcast transition returned invalid JSON: {error}"
        ))
    })?;
    let result_object = result.as_object_mut().ok_or_else(|| {
        NativePoolCommandError::transition_failed(
            "native broadcast transition must return an object",
        )
    })?;
    result_object.insert("ok".to_owned(), Value::Bool(true));
    result_object.insert(
        "mode".to_owned(),
        Value::String("native_closed_loop_group_manual_broadcast".to_owned()),
    );
    result_object.insert("chainId".to_owned(), json!(chain_id));
    result_object.insert("groupKey".to_owned(), Value::String(group_key));
    let accepted = result_object
        .get("accepted")
        .cloned()
        .unwrap_or(Value::Bool(true));
    result_object.insert("accepted".to_owned(), accepted);
    result_object.insert("processingAsync".to_owned(), Value::Bool(!dry_run));
    result_object.insert("stateReadRequired".to_owned(), Value::Bool(true));
    Ok(result)
}

#[pyfunction]
#[pyo3(signature = (database_path, chain_id=None, network=None, protocol=None, max_workers=16, maintenance_handler=None, maintenance_interval_seconds=600, binance_interval_seconds=300, binance_enabled=false, execution_workers=2, execution_queue_enabled=true))]
pub(crate) fn native_pool_get_or_create(
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
    let key = database_path.trim().to_lowercase();
    if key.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "database_path is required for native pool registry",
        ));
    }
    let mut registry = registry_lock();
    if let Some(entry) = registry.get(&key) {
        return Ok(entry.pool.clone_ref(py).into_any());
    }

    let pool = ClosedLoopWorkerPool::start(
        max_workers,
        Some(database_path.clone()),
        maintenance_handler,
        maintenance_interval_seconds,
        binance_interval_seconds,
        binance_enabled,
        execution_workers,
        execution_queue_enabled,
    );
    let created_at = chrono::Utc::now().to_rfc3339();
    let pool_object = Py::new(py, pool)?;
    registry.insert(
        key.clone(),
        NativePoolEntry {
            chain_id,
            network,
            protocol,
            database_path,
            pool: pool_object,
            created_at,
        },
    );
    Ok(registry
        .get(&key)
        .expect("native pool entry was just inserted")
        .pool
        .clone_ref(py)
        .into_any())
}

#[pyfunction]
#[pyo3(signature = (stop=false, chain_id=None, database_path=None))]
pub(crate) fn native_pool_registry_manage(
    py: Python<'_>,
    stop: bool,
    chain_id: Option<i64>,
    database_path: Option<String>,
) -> PyResult<PyObject> {
    let mut registry = registry_lock();
    let removed_keys = registry
        .keys()
        .filter(|key| {
            let entry = &registry[*key];
            chain_id.is_none_or(|expected| entry.chain_id == Some(expected))
                && database_path
                    .as_deref()
                    .is_none_or(|path| entry.database_path == path)
        })
        .cloned()
        .collect::<Vec<_>>();
    let mut errors = Vec::new();
    for key in &removed_keys {
        let entry = registry
            .remove(key)
            .expect("removed key still exists in native pool registry");
        if stop {
            if let Err(error) = stop_pool(py, &entry.pool) {
                errors.push(json!({
                    "chainId": entry.chain_id,
                    "databasePath": entry.database_path,
                    "error": error,
                }));
            }
        }
    }
    let remaining = registry.len();
    Ok(json_result_to_py(
        py,
        json!({
            "ok": errors.is_empty(),
            "stopped": stop,
            "removed": removed_keys.len(),
            "remaining": remaining,
            "errors": errors,
        }),
    ))
}

#[pyfunction]
pub(crate) fn native_pool_registry_status(py: Python<'_>) -> PyObject {
    let registry = registry_lock();
    let mut entries = registry
        .values()
        .map(|entry| {
            json!({
                "chainId": entry.chain_id,
                "network": entry.network,
                "protocol": entry.protocol,
                "databasePath": entry.database_path,
                "createdAt": entry.created_at,
            })
        })
        .collect::<Vec<Value>>();
    entries.sort_by_key(|entry| {
        format!(
            "{}:{}",
            entry
                .get("chainId")
                .and_then(Value::as_i64)
                .unwrap_or_default(),
            entry
                .get("databasePath")
                .and_then(Value::as_str)
                .unwrap_or_default()
        )
    });
    json_result_to_py(
        py,
        json!({
            "count": entries.len(),
            "entries": entries,
            "registry": "rust_native_pool_registry",
        }),
    )
}

fn stop_pool(py: Python<'_>, pool: &Py<ClosedLoopWorkerPool>) -> Result<(), String> {
    pool.clone_ref(py)
        .into_any()
        .into_bound(py)
        .call_method0("stop")
        .map(|_| ())
        .map_err(|error| error.to_string())
}
