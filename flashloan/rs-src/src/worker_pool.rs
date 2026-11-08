use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex, RwLock};
use std::thread;
use std::time::Duration;

use broadcast_manager::{BroadcastSupervisor, ExecutionTask};
use num_bigint::BigUint;
use pyo3::prelude::*;
use review_manager::{ReviewRuntime, ReviewSupervisor, ReviewSupervisorCommand};
use serde_json::{json, Value};

use crate::execution;
use crate::json_result_to_py;
use crate::pool_index::{self, V4PoolKey};
use crate::sqlite_read;

type MaintenanceHandler = Py<PyAny>;

#[derive(Clone, Debug)]
struct MaintenanceSubmit {
    chain_scope: String,
    database_path: Option<String>,
    network: Option<String>,
    chain_id: Option<i64>,
    protocol: Option<String>,
    mode: String,
    route_addresses: Vec<String>,
    pool_candidates: Vec<Value>,
    direction_target: u64,
    zero_liquidity_pool_ids: Vec<String>,
}

enum MaintenanceWorkerCommand {
    Submit(MaintenanceSubmit),
    Shutdown,
}

enum BinanceCollectorCommand {
    Shutdown,
}

#[derive(Clone, Debug, Default)]
struct MaintenanceState {
    in_progress: bool,
    queued_count: u64,
    completed_count: u64,
    failed_count: u64,
    last_started_at: Option<String>,
    last_completed_at: Option<String>,
    last_status: Option<String>,
    last_error: Option<String>,
    last_result: Option<Value>,
    subsequent_generation: u64,
    immediate_review_trigger_count: u64,
    last_immediate_review_triggered_at: Option<String>,
    last_immediate_review_woken_groups: Option<String>,
}

#[derive(Clone, Debug, Default)]
struct BinanceCollectorState {
    in_progress: bool,
    collected_count: u64,
    failed_count: u64,
    last_started_at: Option<String>,
    last_completed_at: Option<String>,
    last_status: Option<String>,
    last_error: Option<String>,
    last_result: Option<Value>,
    enabled: bool,
    running: bool,
}

#[derive(Clone)]
struct SubmitCommand {
    group_key: String,
    chain_id: Option<i64>,
    network: Option<String>,
    protocol: Option<String>,
    protocols: Vec<String>,
    mode: String,
    config_json: String,
    database_path: Option<String>,
}

#[derive(Clone, Debug)]
struct WorkerWakeControl {
    generation: u64,
}

#[derive(Clone, Debug)]
enum WorkerControl<T> {
    Submit(T),
    Wake(WorkerWakeControl),
}

#[derive(Clone, Debug)]
struct WorkerState {
    group_key: String,
    chain_id: Option<i64>,
    network: Option<String>,
    protocol: Option<String>,
    protocols: Vec<String>,
    mode: String,
    state: String,
    thread_id: String,
    started_at: Option<String>,
    updated_at: Option<String>,
    probe_count: u64,
    broadcast_count: u64,
    broadcast_failure_count: u64,
    cache_hit: u64,
    queued_count: u64,
    last_error: Option<String>,
    last_result: Option<Value>,
    direction_results: Vec<Value>,
    phase: String,
    phase_started_at: Option<String>,
    backoff_until: Option<String>,
    consecutive_errors: u64,
    last_phase_error: Option<String>,
    last_broadcast_at: Option<String>,
    last_confirmed_at: Option<String>,
    broadcast_history: Vec<Value>,
    broadcast_failure_history: Vec<Value>,
    poll_interval_seconds: u64,
    next_scan_at: Option<String>,
    wake_generation: u64,
}

impl Default for WorkerState {
    fn default() -> Self {
        Self {
            group_key: String::new(),
            chain_id: None,
            network: None,
            protocol: None,
            protocols: Vec::new(),
            mode: "scan".to_string(),
            state: "idle".to_string(),
            thread_id: String::new(),
            started_at: None,
            updated_at: None,
            probe_count: 0,
            broadcast_count: 0,
            broadcast_failure_count: 0,
            cache_hit: 0,
            queued_count: 0,
            last_error: None,
            last_result: None,
            direction_results: Vec::new(),
            phase: "idle".to_string(),
            phase_started_at: None,
            backoff_until: None,
            consecutive_errors: 0,
            last_phase_error: None,
            last_broadcast_at: None,
            last_confirmed_at: None,
            broadcast_history: Vec::new(),
            broadcast_failure_history: Vec::new(),
            poll_interval_seconds: 30,
            next_scan_at: None,
            wake_generation: 0,
        }
    }
}

/// Scope-level prefilter data refreshed in place and shared by every worker in
/// the scope. The cache object remains stable across TTL refreshes so workers
/// never replace the scope-wide pool registry wholesale.
#[derive(Debug, Default)]
struct ContextCacheData {
    pool_count: usize,
    liquidity_count: usize,
    token_count: usize,
    registry: V4PoolRegistry,
    registry_scope: Option<String>,
    loaded_at: Option<String>,
}

#[derive(Debug)]
struct ContextCache {
    data: RwLock<ContextCacheData>,
}

#[derive(Clone, Debug, Default)]
struct PoolRegistryEntry {
    token0: String,
    token1: String,
}

#[derive(Clone, Debug, Default)]
struct LiquidityRegistryEntry {
    has_liquidity: Option<i64>,
}

#[derive(Clone, Debug, Default)]
struct V4PoolRegistry {
    pool_by_id: HashMap<String, PoolRegistryEntry>,
    liquidity_by_id: HashMap<String, LiquidityRegistryEntry>,
    token_index_count: usize,
    pool_ids_by_token: HashMap<String, HashSet<String>>,
}

impl V4PoolRegistry {
    #[cfg(test)]
    fn from_rows(pool_rows: &[Value], liquidity_rows: &[Value], token_rows: &[Value]) -> Self {
        let mut registry = Self::default();
        registry.replace_from_rows(pool_rows, liquidity_rows, token_rows);
        registry
    }

    fn replace_from_rows(
        &mut self,
        pool_rows: &[Value],
        liquidity_rows: &[Value],
        token_rows: &[Value],
    ) {
        self.pool_by_id.clear();
        for row in pool_rows {
            if let Some(pool_id) = row
                .get("pool_id")
                .and_then(Value::as_str)
                .map(|value| value.trim().to_lowercase())
            {
                if !pool_id.is_empty() {
                    self.pool_by_id.insert(
                        pool_id,
                        PoolRegistryEntry {
                            token0: row
                                .get("token0")
                                .and_then(Value::as_str)
                                .map(|value| value.trim().to_lowercase())
                                .unwrap_or_default(),
                            token1: row
                                .get("token1")
                                .and_then(Value::as_str)
                                .map(|value| value.trim().to_lowercase())
                                .unwrap_or_default(),
                        },
                    );
                }
            }
        }
        self.liquidity_by_id.clear();
        for row in liquidity_rows {
            if let Some(pool_id) = row
                .get("pool_id")
                .and_then(Value::as_str)
                .map(|value| value.trim().to_lowercase())
            {
                if !pool_id.is_empty() {
                    self.liquidity_by_id.insert(
                        pool_id,
                        LiquidityRegistryEntry {
                            has_liquidity: row.get("has_liquidity").and_then(Value::as_i64),
                        },
                    );
                }
            }
        }
        let mut token_addresses = HashSet::new();
        for row in token_rows {
            if let Some(address) = row
                .get("token_address")
                .and_then(Value::as_str)
                .map(|value| value.trim().to_lowercase())
            {
                if !address.is_empty() {
                    token_addresses.insert(address);
                }
            }
        }
        self.token_index_count = token_addresses.len();
        self.pool_ids_by_token.clear();
        for (pool_id, pool) in &self.pool_by_id {
            for token in [&pool.token0, &pool.token1] {
                if token.is_empty() {
                    continue;
                }
                self.pool_ids_by_token
                    .entry(token.clone())
                    .or_default()
                    .insert(pool_id.clone());
            }
        }
    }

    fn get_pool_info(&self, pool_id: &str) -> Option<&PoolRegistryEntry> {
        self.pool_by_id.get(&pool_id.trim().to_lowercase())
    }

    fn get_liquidity(&self, pool_id: &str) -> Option<&LiquidityRegistryEntry> {
        self.liquidity_by_id.get(&pool_id.trim().to_lowercase())
    }

    fn route_pool_ids(&self, route_addresses: &[String]) -> Vec<String> {
        let route_set: HashSet<String> = route_addresses
            .iter()
            .map(|address| address.trim().to_lowercase())
            .collect();
        let mut candidate_ids: HashSet<String> = HashSet::new();
        for token in &route_set {
            if let Some(ids) = self.pool_ids_by_token.get(token) {
                candidate_ids.extend(ids.iter().cloned());
            }
        }
        let mut ids: Vec<String> = candidate_ids.into_iter().collect();
        ids.retain(|pool_id| {
            self.get_pool_info(pool_id)
                .map(|pool| route_set.contains(&pool.token0) && route_set.contains(&pool.token1))
                .unwrap_or(false)
        });
        ids.sort();
        ids.dedup();
        ids
    }
}

struct PoolInner {
    review_supervisor: Mutex<Option<ReviewSupervisor<SubmitCommand>>>,
    states: Mutex<HashMap<String, WorkerState>>,
    review_runtime: ReviewRuntime,
    broadcast_supervisor: Mutex<Option<BroadcastSupervisor>>,
    execution_workers: usize,
    execution_queue_enabled: bool,
    stop_flags: Mutex<HashSet<String>>,
    running: Mutex<HashSet<String>>,
    group_senders: Mutex<HashMap<String, Sender<WorkerControl<SubmitCommand>>>>,
    last_commands: Mutex<HashMap<String, SubmitCommand>>,
    contexts: Mutex<HashMap<String, Arc<ContextCache>>>,
    maintenance_handler: Option<MaintenanceHandler>,
    maintenance_interval_seconds: u64,
    maintenance_tx: Mutex<Option<Sender<MaintenanceWorkerCommand>>>,
    maintenance_join: Mutex<Option<thread::JoinHandle<()>>>,
    maintenance_state: Mutex<MaintenanceState>,
    maintenance_scopes: Mutex<HashSet<String>>,
    binance_interval_seconds: u64,
    binance_tx: Mutex<Option<Sender<BinanceCollectorCommand>>>,
    binance_join: Mutex<Option<thread::JoinHandle<()>>>,
    binance_state: Mutex<BinanceCollectorState>,
    shutdown: AtomicBool,
    worker_seq: AtomicU64,
    worker_created_total: AtomicU64,
    worker_completed_total: AtomicU64,
    worker_idle_exits: AtomicU64,
    database_path: Option<String>,
}

#[pyclass(name = "ClosedLoopWorkerPool")]
pub struct ClosedLoopWorkerPool {
    inner: Arc<PoolInner>,
    stopped: bool,
}

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339()
}

const CONTEXT_CACHE_TTL_SECONDS: i64 = 300;

fn maintenance_scope(
    network: Option<&str>,
    chain_id: Option<i64>,
    protocol: Option<&str>,
) -> String {
    [
        network.unwrap_or("").trim().to_lowercase(),
        chain_id.unwrap_or(0).max(0).to_string(),
        protocol.unwrap_or("uniswap_v4").trim().to_lowercase(),
    ]
    .join("|")
}

fn maintenance_due(inner: &PoolInner, scope: &str) -> bool {
    if inner.maintenance_handler.is_none() {
        return false;
    }
    let target_interval = inner.maintenance_interval_seconds.max(1);
    let state = inner.maintenance_state.lock().unwrap();
    if state.in_progress {
        return false;
    }
    let due = match state.last_completed_at.as_deref() {
        Some(value) => chrono::DateTime::parse_from_rfc3339(value)
            .map(|completed| {
                chrono::Utc::now() - completed.with_timezone(&chrono::Utc)
                    > chrono::Duration::seconds(target_interval.max(1) as i64)
            })
            .unwrap_or(true),
        None => true,
    };
    let _ = scope;
    due
}

fn enqueue_maintenance(inner: &PoolInner, command: &SubmitCommand) {
    if inner.maintenance_handler.is_none() {
        return;
    }
    let scope = maintenance_scope(
        command.network.as_deref(),
        command.chain_id,
        command.protocol.as_deref(),
    );
    if !maintenance_due(inner, &scope) {
        return;
    }
    let config: Value = serde_json::from_str(&command.config_json).unwrap_or(Value::Null);
    let mut pool_candidates: Vec<Value> = Vec::new();
    let mut push_keys = |keys: Option<&Value>| {
        if let Some(rows) = keys.and_then(Value::as_array) {
            pool_candidates.extend(rows.iter().cloned());
        }
    };
    if let Some(routes) = config.get("routes").and_then(Value::as_array) {
        for route in routes {
            for key in ["entryKeys", "bridgeKeys", "exitKeys", "poolKeys"] {
                push_keys(route.get(key));
            }
        }
    }
    for key in ["entryKeys", "bridgeKeys", "exitKeys", "poolKeys"] {
        push_keys(config.get(key));
    }
    let payload = MaintenanceSubmit {
        chain_scope: scope,
        database_path: command
            .database_path
            .clone()
            .or_else(|| inner.database_path.clone())
            .or_else(|| active_runtime_database_path(command.chain_id)),
        network: command.network.clone(),
        chain_id: command.chain_id,
        protocol: command.protocol.clone(),
        mode: command.mode.clone(),
        route_addresses: config_route_addresses(&config),
        pool_candidates,
        direction_target: 5,
        zero_liquidity_pool_ids: Vec::new(),
    };
    {
        let mut state = inner.maintenance_state.lock().unwrap();
        if state.in_progress {
            return;
        }
        let accepted = inner
            .maintenance_tx
            .lock()
            .unwrap()
            .as_ref()
            .map(|sender| {
                sender
                    .send(MaintenanceWorkerCommand::Submit(payload))
                    .is_ok()
            })
            .unwrap_or(false);
        if accepted {
            state.queued_count = state.queued_count.saturating_add(1);
        }
    }
}

fn enqueue_zero_liquidity_cleanup(
    inner: &PoolInner,
    command: &SubmitCommand,
    zero_liquidity_pool_ids: Vec<String>,
) {
    if inner.maintenance_handler.is_none() || zero_liquidity_pool_ids.is_empty() {
        return;
    }
    let scope = maintenance_scope(
        command.network.as_deref(),
        command.chain_id,
        command.protocol.as_deref(),
    );
    let payload = MaintenanceSubmit {
        chain_scope: scope,
        database_path: command
            .database_path
            .clone()
            .or_else(|| inner.database_path.clone())
            .or_else(|| active_runtime_database_path(command.chain_id)),
        network: command.network.clone(),
        chain_id: command.chain_id,
        protocol: command.protocol.clone(),
        mode: "zero_liquidity_cleanup".to_string(),
        route_addresses: Vec::new(),
        pool_candidates: Vec::new(),
        direction_target: 5,
        zero_liquidity_pool_ids,
    };
    {
        let mut state = inner.maintenance_state.lock().unwrap();
        if state.in_progress || state.queued_count > 0 {
            return;
        }
        let accepted = inner
            .maintenance_tx
            .lock()
            .unwrap()
            .as_ref()
            .map(|sender| {
                sender
                    .send(MaintenanceWorkerCommand::Submit(payload))
                    .is_ok()
            })
            .unwrap_or(false);
        if accepted {
            state.queued_count = state.queued_count.saturating_add(1);
        }
    }
}

fn run_maintenance_handler(
    inner: &PoolInner,
    payload: &MaintenanceSubmit,
) -> Result<Value, String> {
    let Some(handler) = inner.maintenance_handler.as_ref() else {
        return Err("maintenance handler is not configured".to_string());
    };
    let handler_payload = json!({
        "database_path": payload.database_path,
        "network": payload.network,
        "chain_id": payload.chain_id,
        "protocol": payload.protocol,
        "mode": payload.mode,
        "route_addresses": payload.route_addresses,
        "pool_candidates": payload.pool_candidates,
        "zero_liquidity_pool_ids": payload.zero_liquidity_pool_ids,
        "direction_target": payload.direction_target,
        "maintenance_interval_seconds": inner.maintenance_interval_seconds.max(1),
    });
    let payload_json = serde_json::to_string(&handler_payload)
        .map_err(|error| format!("maintenance payload JSON encoding failed: {error}"))?;
    let result = Python::with_gil(|py| {
        handler
            .bind(py)
            .call1((payload_json,))
            .map(|value| value.unbind())
    })
    .map_err(|error| format!("maintenance handler call failed: {error}"))?;
    let text = Python::with_gil(|py| result.extract::<String>(py))
        .map_err(|error| format!("maintenance handler did not return a JSON string: {error}"))?;
    serde_json::from_str(&text)
        .map_err(|error| format!("maintenance handler returned invalid JSON: {error}"))
}

fn submit_command_scope(command: &SubmitCommand) -> String {
    maintenance_scope(
        command.network.as_deref(),
        command.chain_id,
        command.protocol.as_deref(),
    )
}

fn maintenance_pool_candidates_from_command(command: &SubmitCommand) -> Vec<Value> {
    let config: Value = serde_json::from_str(&command.config_json).unwrap_or(Value::Null);
    let mut pool_candidates: Vec<Value> = Vec::new();
    let mut push_keys = |keys: Option<&Value>| {
        if let Some(rows) = keys.and_then(Value::as_array) {
            pool_candidates.extend(rows.iter().cloned());
        }
    };
    if let Some(routes) = config.get("routes").and_then(Value::as_array) {
        for route in routes {
            for key in ["entryKeys", "bridgeKeys", "exitKeys", "poolKeys"] {
                push_keys(route.get(key));
            }
        }
    }
    for key in ["entryKeys", "bridgeKeys", "exitKeys", "poolKeys"] {
        push_keys(config.get(key));
    }
    pool_candidates
}

fn full_scope_maintenance_payload(
    inner: &PoolInner,
    scope: &str,
    command: Option<&SubmitCommand>,
) -> MaintenanceSubmit {
    let route_addresses = command
        .map(|item| {
            let config: Value = serde_json::from_str(&item.config_json).unwrap_or(Value::Null);
            config_route_addresses(&config)
        })
        .unwrap_or_default();
    let command_network = command.and_then(|item| item.network.clone());
    let command_chain_id = command.and_then(|item| item.chain_id);
    let command_protocol = command.and_then(|item| item.protocol.clone());
    let parts: Vec<&str> = scope.splitn(3, '|').collect();
    MaintenanceSubmit {
        chain_scope: scope.to_string(),
        database_path: command
            .and_then(|item| item.database_path.clone())
            .or_else(|| inner.database_path.clone())
            .or_else(|| active_runtime_database_path(command_chain_id)),
        network: command_network.or_else(|| {
            parts
                .first()
                .map(|value| value.to_string())
                .filter(|value| !value.is_empty())
        }),
        chain_id: command_chain_id
            .or_else(|| parts.get(1).and_then(|value| value.parse::<i64>().ok())),
        protocol: command_protocol.or_else(|| {
            parts
                .get(2)
                .map(|value| value.to_string())
                .filter(|value| !value.is_empty())
                .or_else(|| Some("uniswap_v4".to_string()))
        }),
        mode: "full_scope_refresh".to_string(),
        route_addresses,
        pool_candidates: command
            .map(maintenance_pool_candidates_from_command)
            .unwrap_or_default(),
        direction_target: 5,
        zero_liquidity_pool_ids: Vec::new(),
    }
}

fn registered_maintenance_scopes(inner: &PoolInner) -> Vec<String> {
    let mut scopes: HashSet<String> = inner.maintenance_scopes.lock().unwrap().clone();
    let commands = inner.last_commands.lock().unwrap();
    for command in commands.values() {
        scopes.insert(submit_command_scope(command));
    }
    let mut sorted: Vec<String> = scopes.into_iter().collect();
    sorted.sort();
    sorted
}

fn finish_maintenance_submit(
    inner: &PoolInner,
    scope: &str,
    is_zero_liquidity_cleanup: bool,
    result: Result<Value, String>,
) {
    let trigger_immediate_review = result
        .as_ref()
        .ok()
        .and_then(|result_payload| result_payload.get("triggerImmediateReview"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    {
        let mut state = inner.maintenance_state.lock().unwrap();
        state.in_progress = false;
        match result {
            Ok(result_payload) => {
                let ok = result_payload
                    .get("ok")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                state.last_completed_at = Some(now_iso());
                state.last_status = Some(if ok { "ok" } else { "error" }.to_string());
                state.last_error = None;
                state.last_result = Some(result_payload);
                if ok {
                    state.completed_count = state.completed_count.saturating_add(1);
                    if !is_zero_liquidity_cleanup {
                        state.subsequent_generation = state.subsequent_generation.saturating_add(1);
                    }
                    if trigger_immediate_review {
                        state.immediate_review_trigger_count =
                            state.immediate_review_trigger_count.saturating_add(1);
                        state.last_immediate_review_triggered_at = Some(now_iso());
                    }
                } else {
                    state.failed_count = state.failed_count.saturating_add(1);
                }
            }
            Err(error) => {
                state.last_completed_at = Some(now_iso());
                state.last_status = Some("error".to_string());
                state.last_error = Some(error);
                state.last_result = None;
                state.failed_count = state.failed_count.saturating_add(1);
            }
        }
    }
    if trigger_immediate_review {
        let woken = wake_scope_groups(inner, scope);
        let mut state = inner.maintenance_state.lock().unwrap();
        state.last_immediate_review_woken_groups = Some(woken.to_string());
    }
}

fn compact_maintenance_result(result: &Value) -> Value {
    let mut cleaned_columns = Vec::new();
    if let Some(cleaned) = result.get("cleaned").and_then(Value::as_object) {
        for (column, count) in cleaned.iter() {
            if let Some(count) = count.as_i64() {
                cleaned_columns.push(json!({
                    "column": column,
                    "cleaned": count,
                }));
            }
        }
    }
    cleaned_columns.sort_by(|left, right| {
        left.get("column")
            .and_then(Value::as_str)
            .unwrap_or("")
            .cmp(right.get("column").and_then(Value::as_str).unwrap_or(""))
    });
    json!({
        "ok": result.get("ok"),
        "mode": result.get("mode"),
        "phase": result.get("phase"),
        "database": result.get("database"),
        "network": result.get("network"),
        "chainId": result.get("chainId"),
        "protocol": result.get("protocol"),
        "generatedAt": result.get("generatedAt"),
        "cleanedPoolCount": result.get("cleanedPoolCount"),
        "cleaned": Value::Array(cleaned_columns),
    })
}

fn execute_maintenance_submit(inner: &PoolInner, payload: MaintenanceSubmit) {
    let scope = payload.chain_scope.clone();
    let is_zero_liquidity_cleanup = payload.mode == "zero_liquidity_cleanup";
    {
        let mut state = inner.maintenance_state.lock().unwrap();
        state.in_progress = true;
        state.last_started_at = Some(now_iso());
    }
    let result = run_maintenance_handler(inner, &payload);
    finish_maintenance_submit(inner, &scope, is_zero_liquidity_cleanup, result);
}

fn run_timed_maintenance_scopes(inner: &PoolInner) -> usize {
    if inner.maintenance_handler.is_none() {
        return 0;
    }
    if inner.maintenance_state.lock().unwrap().in_progress {
        return 0;
    }
    let scopes = registered_maintenance_scopes(inner);
    let mut executed = 0;
    for scope in scopes {
        if !maintenance_due(inner, &scope) {
            continue;
        }
        let command = inner
            .last_commands
            .lock()
            .unwrap()
            .values()
            .find(|command| submit_command_scope(command) == scope)
            .cloned();
        let payload = full_scope_maintenance_payload(inner, &scope, command.as_ref());
        execute_maintenance_submit(inner, payload);
        executed += 1;
    }
    executed
}

fn wake_scope_groups(inner: &PoolInner, scope: &str) -> usize {
    let commands: Vec<SubmitCommand> = {
        let last_commands = inner.last_commands.lock().unwrap();
        last_commands
            .values()
            .filter(|command| {
                maintenance_scope(
                    command.network.as_deref(),
                    command.chain_id,
                    command.protocol.as_deref(),
                ) == scope
            })
            .cloned()
            .collect()
    };
    if commands.is_empty() {
        return 0;
    }
    let senders = inner.group_senders.lock().unwrap();
    let mut woken = 0;
    for command in commands {
        if let Some(sender) = senders.get(&command.group_key) {
            if sender.send(WorkerControl::Submit(command)).is_ok() {
                woken += 1;
            }
        }
    }
    woken
}

fn maintenance_worker_loop(inner: Arc<PoolInner>, receiver: Receiver<MaintenanceWorkerCommand>) {
    let check_interval = Duration::from_secs(inner.maintenance_interval_seconds.max(1).min(60));
    loop {
        match receiver.recv_timeout(check_interval) {
            Ok(MaintenanceWorkerCommand::Shutdown) => break,
            Ok(MaintenanceWorkerCommand::Submit(payload)) => {
                {
                    let mut state = inner.maintenance_state.lock().unwrap();
                    state.queued_count = state.queued_count.saturating_sub(1);
                }
                execute_maintenance_submit(&inner, payload);
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                run_timed_maintenance_scopes(&inner);
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
}

fn spawn_maintenance_worker(
    inner: Arc<PoolInner>,
    receiver: Receiver<MaintenanceWorkerCommand>,
) -> thread::JoinHandle<()> {
    thread::Builder::new()
        .name("rust-closed-loop-sqlite-maintenance".to_string())
        .spawn(move || maintenance_worker_loop(inner, receiver))
        .expect("closed-loop SQLite maintenance thread starts")
}

fn binance_collector_loop(inner: Arc<PoolInner>, receiver: Receiver<BinanceCollectorCommand>) {
    // BNB market collection is intentionally decoupled from strategy/execution
    // today. The snapshot is only persisted for future compatibility; no
    // pipeline consumes it yet, and a later fix will decide the final cadence
    // and consumption semantics.
    loop {
        if inner.shutdown.load(Ordering::SeqCst) {
            break;
        }
        let interval = inner.binance_interval_seconds.max(60);
        match receiver.recv_timeout(Duration::from_secs(interval)) {
            Ok(BinanceCollectorCommand::Shutdown) => break,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
        if inner.shutdown.load(Ordering::SeqCst) {
            break;
        }
        let database_path = inner.database_path.clone();
        let started_at = now_iso();
        {
            let mut state = inner.binance_state.lock().unwrap();
            state.in_progress = true;
            state.running = true;
            state.last_started_at = Some(started_at);
        }
        let result = match database_path {
            Some(path) => crate::binance_market::collect_bnb_market_snapshot(&path),
            None => Err("BNB market collector has no database path configured".to_string()),
        };
        {
            let mut state = inner.binance_state.lock().unwrap();
            state.in_progress = false;
            state.running = false;
            state.last_completed_at = Some(now_iso());
            match result {
                Ok(payload) => {
                    let ok = payload.get("ok").and_then(Value::as_bool).unwrap_or(false);
                    state.last_status = Some(if ok { "ok" } else { "error" }.to_string());
                    state.last_error = None;
                    state.last_result = Some(payload);
                    if ok {
                        state.collected_count = state.collected_count.saturating_add(1);
                    } else {
                        state.failed_count = state.failed_count.saturating_add(1);
                    }
                }
                Err(error) => {
                    state.last_status = Some("error".to_string());
                    state.last_error = Some(error);
                    state.last_result = None;
                    state.failed_count = state.failed_count.saturating_add(1);
                }
            }
        }
    }
}

fn spawn_binance_collector(
    inner: Arc<PoolInner>,
    receiver: Receiver<BinanceCollectorCommand>,
) -> thread::JoinHandle<()> {
    thread::Builder::new()
        .name("rust-closed-loop-bnb-market-collector".to_string())
        .spawn(move || binance_collector_loop(inner, receiver))
        .expect("closed-loop BNB market collector thread starts")
}

fn active_runtime_database_path(chain_id: Option<i64>) -> Option<String> {
    crate::runtime_config::resolve_runtime_database(chain_id.map(|value| value.max(0) as u64), None)
        .ok()
        .map(|(path, _)| path)
}

impl ContextCacheData {
    fn replace_from_rows(
        &mut self,
        pool_rows: &[Value],
        liquidity_rows: &[Value],
        token_rows: &[Value],
        registry_scope: &str,
    ) {
        self.pool_count = pool_rows.len();
        self.liquidity_count = liquidity_rows.len();
        self.token_count = token_rows.len();
        self.registry
            .replace_from_rows(pool_rows, liquidity_rows, token_rows);
        self.registry_scope = Some(registry_scope.to_string());
        self.loaded_at = Some(now_iso());
    }
}

fn load_context_registry_rows(
    database_path: &str,
    network: &str,
    chain_id: u64,
    protocol: &str,
) -> Option<(Vec<Value>, Vec<Value>, Vec<Value>)> {
    let connection = match sqlite_read::open_readonly(database_path) {
        Ok(connection) => connection,
        Err(_) => return None,
    };
    let pool_rows =
        sqlite_read::load_v4_exchange_pool_registry_rows(&connection, network, chain_id, protocol)
            .unwrap_or_default();
    let liquidity_rows =
        sqlite_read::load_v4_pool_liquidity_registry_rows(&connection, network, chain_id, protocol)
            .unwrap_or_default();
    let token_rows = sqlite_read::load_chain_token_registry_rows(&connection, network, chain_id)
        .unwrap_or_default();
    Some((pool_rows, liquidity_rows, token_rows))
}

fn context_cache_is_stale(data: &ContextCacheData) -> bool {
    data.loaded_at
        .as_deref()
        .and_then(|value| chrono::DateTime::parse_from_rfc3339(value).ok())
        .map(|loaded_at| {
            chrono::Utc::now() - loaded_at.with_timezone(&chrono::Utc)
                > chrono::Duration::seconds(CONTEXT_CACHE_TTL_SECONDS)
        })
        .unwrap_or(true)
}

fn load_context_cache(
    inner: &PoolInner,
    command: &SubmitCommand,
) -> Option<(Arc<ContextCache>, bool)> {
    let database_path = command
        .database_path
        .clone()
        .or_else(|| inner.database_path.clone())
        .or_else(|| active_runtime_database_path(command.chain_id))?;
    let network = command.network.clone()?;
    let protocol = command
        .protocol
        .clone()
        .unwrap_or_else(|| "uniswap_v4".to_string());
    let chain_id = command.chain_id.unwrap_or(0).max(0) as u64;
    let registry_scope = maintenance_scope(Some(&network), Some(chain_id as i64), Some(&protocol));
    let mut contexts = inner.contexts.lock().unwrap();
    if let Some(cached) = contexts.get(&registry_scope).cloned() {
        let stale = {
            let data = cached.data.read().unwrap();
            context_cache_is_stale(&data)
        };
        if !stale {
            return Some((cached, true));
        }
        if let Some((pool_rows, liquidity_rows, token_rows)) =
            load_context_registry_rows(&database_path, &network, chain_id, &protocol)
        {
            cached.data.write().unwrap().replace_from_rows(
                &pool_rows,
                &liquidity_rows,
                &token_rows,
                &registry_scope,
            );
            return Some((cached, false));
        }
        return Some((cached, true));
    }
    let (pool_rows, liquidity_rows, token_rows) =
        load_context_registry_rows(&database_path, &network, chain_id, &protocol)?;
    let mut data = ContextCacheData::default();
    data.replace_from_rows(&pool_rows, &liquidity_rows, &token_rows, &registry_scope);
    let cache = Arc::new(ContextCache {
        data: RwLock::new(data),
    });
    contexts.insert(registry_scope, Arc::clone(&cache));
    Some((cache, false))
}

fn apply_selected_route_overrides(
    payload: &mut Value,
    route_payload: &Value,
    route_config: &Value,
) {
    payload["selectedRoutePayload"] = route_payload.clone();
    payload["selectedAmountRaw"] = route_payload
        .get("selectedAmountRaw")
        .cloned()
        .unwrap_or(Value::Null);
    payload["selectedProfitRaw"] = route_payload
        .get("selectedProfitRaw")
        .cloned()
        .unwrap_or(Value::Null);
    payload["minProfitRaw"] = route_config
        .get("minProfitRaw")
        .cloned()
        .unwrap_or(Value::Null);
    payload["selectedMinProfitRaw"] = route_config
        .get("minProfitRaw")
        .cloned()
        .unwrap_or(Value::Null);
    payload["token"] = route_config.get("token").cloned().unwrap_or(Value::Null);
}

fn mark_selected_route_direction(payload: &mut Value, best_index: usize) {
    if let Some(routes) = payload.get_mut("routes").and_then(Value::as_array_mut) {
        if let Some(route) = routes.get_mut(best_index) {
            route["directionSelected"] = json!(true);
        }
    }
}

fn is_rpc_busy_error(error: &str) -> bool {
    let lowered = error.to_lowercase();
    [
        "network is busy",
        "please try again in a moment",
        "too many requests",
        "rate limit",
        "rate limited",
        "request rate",
        "429",
        "service unavailable",
        "temporarily unavailable",
        "overloaded",
        "server busy",
        "busy, please",
    ]
    .iter()
    .any(|marker| lowered.contains(marker))
}

fn route_error_kind(error: &str) -> &'static str {
    let lowered = error.to_lowercase();
    if lowered.contains("out of gas")
        || lowered.contains("outofgas")
        || lowered.contains("gas required exceeds")
        || lowered.contains("intrinsic gas too low")
        || lowered.contains("gas estimation")
    {
        "gas_estimation_failed"
    } else if lowered.contains("timeout") || lowered.contains("timed out") {
        "rpc_verification_timeout"
    } else if is_rpc_busy_error(error) {
        "rpc_busy"
    } else {
        "rpc_verification_failed"
    }
}

fn is_retryable_rpc_error(error: &str) -> bool {
    let lowered = error.to_lowercase();
    [
        "rpc request failed",
        "error sending request",
        "connection refused",
        "connection reset",
        "connection timeout",
        "connection aborted",
        "timed out",
        "timeout",
        "handshake operation timed out",
        "tls handshake",
        "endpoint unreachable",
        "http client failed",
        "tokio runtime failed",
        "response was not json",
        "network is busy",
        "please try again in a moment",
        "too many requests",
        "rate limit",
        "rate limited",
        "request rate",
        "429",
        "service unavailable",
        "temporarily unavailable",
        "overloaded",
        "server busy",
        "busy, please",
    ]
    .iter()
    .any(|marker| lowered.contains(marker))
}

fn run_preview_with_rpc_failover(
    rpc_urls: &[String],
    mut call: impl FnMut(&str) -> Result<Value, String>,
) -> Result<(Value, String, usize), (String, String, usize)> {
    if rpc_urls.is_empty() {
        return match call("") {
            Ok(value) => Ok((value, String::new(), 0)),
            Err(error) => Err((error, String::new(), 0)),
        };
    }
    for (index, url) in rpc_urls.iter().enumerate() {
        match call(url) {
            Ok(value) => return Ok((value, url.clone(), index)),
            Err(error) => {
                if !is_retryable_rpc_error(&error) || index + 1 == rpc_urls.len() {
                    return Err((error, url.clone(), index));
                }
            }
        }
    }
    unreachable!("non-empty rpc_urls loop must return")
}

fn preview_official_v4_with_rpc_failover(
    rpc_urls: &[String],
    config: &Value,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<(Value, String, usize), (String, String, usize)> {
    run_preview_with_rpc_failover(rpc_urls, |url| {
        execution::official_v4_preview_and_ladder_inner(
            config,
            url,
            arb_address,
            call_from,
            timeout_seconds,
        )
    })
}

fn preview_adaptive_official_v4_with_rpc_failover(
    rpc_urls: &[String],
    config: &Value,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<(Value, String, usize), (String, String, usize)> {
    run_preview_with_rpc_failover(rpc_urls, |url| {
        execution::official_v4_adaptive_best_amount_single_route_inner(
            config,
            url,
            arb_address,
            call_from,
            timeout_seconds,
        )
    })
}

fn config_rpc_urls(config: &Value, fallback_url: &str) -> Vec<String> {
    config
        .get("rpcUrls")
        .and_then(Value::as_array)
        .map(|urls| {
            urls.iter()
                .filter_map(|url| url.as_str().map(|value| value.trim().to_string()))
                .filter(|value| !value.is_empty())
                .collect::<Vec<_>>()
        })
        .filter(|urls| !urls.is_empty())
        .unwrap_or_else(|| vec![fallback_url.to_string()])
}

fn backoff_seconds(consecutive_errors: u64) -> u64 {
    let step = consecutive_errors.min(7).saturating_sub(1);
    5u64.saturating_mul(2u64.saturating_pow(step as u32))
}

fn wait_until_ready(inner: &PoolInner, group_key: &str) {
    let target = {
        let states = inner.states.lock().unwrap();
        states
            .get(group_key)
            .and_then(|state| state.backoff_until.clone())
    };
    if let Some(target) = target {
        if let Ok(target) = chrono::DateTime::parse_from_rfc3339(&target) {
            let target = target.with_timezone(&chrono::Utc);
            while chrono::Utc::now() < target {
                if inner.shutdown.load(Ordering::SeqCst)
                    || inner.stop_flags.lock().unwrap().contains(group_key)
                {
                    return;
                }
                std::thread::sleep(Duration::from_secs(1));
            }
        }
    }
}

fn direction_key(route: &Value) -> String {
    let mut parts = Vec::new();
    if let Some(symbols) = route.get("routeSymbols").and_then(Value::as_array) {
        for item in symbols {
            if let Some(text) = item.as_str() {
                parts.push(text.to_lowercase());
            }
        }
    }
    if parts.is_empty() {
        if let Some(addresses) = route.get("routeAddresses").and_then(Value::as_array) {
            for item in addresses {
                if let Some(text) = item.as_str() {
                    parts.push(text.to_lowercase());
                }
            }
        }
    }
    parts.join("|")
}

fn config_route_addresses(config: &Value) -> Vec<String> {
    if let Some(routes) = config.get("routes").and_then(Value::as_array) {
        for route in routes {
            if let Some(addresses) = route.get("routeAddresses").and_then(Value::as_array) {
                return addresses
                    .iter()
                    .filter_map(|item| item.as_str().map(|value| value.trim().to_lowercase()))
                    .filter(|value| !value.is_empty())
                    .collect();
            }
        }
    }
    if let Some(addresses) = config.get("routeAddresses").and_then(Value::as_array) {
        return addresses
            .iter()
            .filter_map(|item| item.as_str().map(|value| value.trim().to_lowercase()))
            .filter(|value| !value.is_empty())
            .collect();
    }
    Vec::new()
}

fn per_direction_results(payload: &Value) -> Vec<Value> {
    let mut snapshots = Vec::new();
    if let Some(routes) = payload.get("routes").and_then(Value::as_array) {
        for route in routes {
            let selected = route
                .get("directionSelected")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let mut snapshot = json!({
                "direction": direction_key(route),
                "routeSymbols": route.get("routeSymbols").cloned().unwrap_or(Value::Null),
                "routeAddresses": route.get("routeAddresses").cloned().unwrap_or(Value::Null),
                "selected": selected,
                "status": route.get("status").cloned().unwrap_or(Value::Null),
                "route": route.clone(),
            });
            if selected {
                snapshot["broadcast"] = payload.get("broadcast").cloned().unwrap_or(Value::Null);
                snapshot["broadcastError"] = payload
                    .get("broadcastError")
                    .cloned()
                    .unwrap_or(Value::Null);
                snapshot["broadcastExecuted"] = payload
                    .get("broadcastExecuted")
                    .cloned()
                    .unwrap_or(Value::Null);
                snapshot["broadcastSkipped"] = payload
                    .get("broadcastSkipped")
                    .cloned()
                    .unwrap_or(Value::Null);
                snapshot["selectedAmountRaw"] = payload
                    .get("selectedAmountRaw")
                    .cloned()
                    .unwrap_or(Value::Null);
                snapshot["selectedProfitRaw"] = payload
                    .get("selectedProfitRaw")
                    .cloned()
                    .unwrap_or(Value::Null);
                snapshot["minProfitRaw"] =
                    payload.get("minProfitRaw").cloned().unwrap_or(Value::Null);
            }
            snapshots.push(snapshot);
        }
    } else if payload.get("routeSymbols").is_some() || payload.get("routeAddresses").is_some() {
        snapshots.push(json!({
            "direction": direction_key(payload),
            "routeSymbols": payload.get("routeSymbols").cloned().unwrap_or(Value::Null),
            "routeAddresses": payload.get("routeAddresses").cloned().unwrap_or(Value::Null),
            "selected": true,
            "status": payload.get("status").cloned().unwrap_or(Value::Null),
            "broadcast": payload.get("broadcast").cloned().unwrap_or(Value::Null),
            "route": payload.clone(),
        }));
    }
    snapshots
}

fn should_retry_inner_single_call_no_profit(result: &Value) -> bool {
    if result.get("status").and_then(Value::as_str) != Some("not_profitable_after_costs") {
        return false;
    }
    let attempts = result
        .get("attempts")
        .and_then(Value::as_array)
        .map(|rows| rows.as_slice())
        .unwrap_or(&[]);
    let amount_search_no_profit = attempts.iter().any(|row| {
        row.get("error")
            .and_then(Value::as_str)
            .map(|text| text.contains("NoProfitableRoute"))
            .unwrap_or(false)
    });
    if let Some(pool_preview) = result.get("poolPreview") {
        let preview_no_profit = pool_preview
            .get("error")
            .and_then(Value::as_str)
            .map(|text| text.contains("NoProfitableRoute"))
            .unwrap_or(false);
        return pool_preview
            .get("ok")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            && !preview_no_profit
            && amount_search_no_profit;
    }
    amount_search_no_profit
}

fn maybe_broadcast(
    payload: &mut Value,
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    timeout_seconds: u64,
    stop_requested: &dyn Fn() -> bool,
) -> Result<(), String> {
    let broadcast_enabled = config
        .get("broadcastEnabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !broadcast_enabled
        || payload.get("status").and_then(Value::as_str) != Some("profitable_amount_found")
    {
        return Ok(());
    }
    if stop_requested() {
        payload["broadcastSkipped"] = json!("group_stopped");
        payload["stopRequested"] = json!(true);
        return Ok(());
    }
    let selected_amount_raw = payload
        .get("selectedAmountRaw")
        .and_then(Value::as_str)
        .ok_or_else(|| "profitable ladder returned no selectedAmountRaw".to_string())?;
    let min_profit_raw = config
        .get("minProfitRaw")
        .and_then(Value::as_str)
        .or_else(|| payload.get("minProfitRaw").and_then(Value::as_str))
        .ok_or_else(|| "broadcast config is missing minProfitRaw".to_string())?;
    let private_key = config
        .get("broadcastPrivateKey")
        .and_then(Value::as_str)
        .ok_or_else(|| "broadcast config is missing broadcastPrivateKey".to_string())?;
    let chain_id = config
        .get("chainId")
        .and_then(Value::as_u64)
        .ok_or_else(|| "broadcast config is missing chainId".to_string())?;
    let relay_urls = config
        .get("relayUrls")
        .and_then(Value::as_str)
        .map(str::to_string);
    let selected_amount =
        execution::parse_big_uint(&json!(selected_amount_raw)).map_err(|error| {
            format!("selectedAmountRaw {selected_amount_raw} is not a uint256: {error}")
        })?;
    let min_profit = execution::parse_big_uint(&json!(min_profit_raw))
        .map_err(|error| format!("minProfitRaw {min_profit_raw} is not a uint256: {error}"))?;
    let mut broadcast_config = config.clone();
    if broadcast_config.get("entryKeys").is_none() {
        if let Some(route_payload) = payload.get("selectedRoutePayload") {
            let selected_keys = route_payload
                .get("selectedKeys")
                .and_then(Value::as_array)
                .ok_or_else(|| "profitable route payload has no selectedKeys".to_string())?
                .clone();
            let selected_directions = route_payload
                .get("selectedDirections")
                .and_then(Value::as_array)
                .ok_or_else(|| "profitable route payload has no selectedDirections".to_string())?
                .clone();
            if selected_keys.len() != 3 || selected_directions.len() != 3 {
                return Err(
                    "profitable route must carry exactly 3 selected keys and directions"
                        .to_string(),
                );
            }
            broadcast_config["entryKeys"] = json!([selected_keys[0].clone()]);
            broadcast_config["entryDirections"] = json!([selected_directions[0].clone()]);
            broadcast_config["bridgeKeys"] = json!([selected_keys[1].clone()]);
            broadcast_config["bridgeDirections"] = json!([selected_directions[1].clone()]);
            broadcast_config["exitKeys"] = json!([selected_keys[2].clone()]);
            broadcast_config["exitDirections"] = json!([selected_directions[2].clone()]);
        }
    }
    if let Some(selected_route) = payload.get("selectedRoutePayload") {
        if let Some(token) = selected_route.get("token").and_then(Value::as_str) {
            broadcast_config["token"] = json!(token);
        }
    }
    if let Some(token) = payload.get("token").and_then(Value::as_str) {
        broadcast_config["token"] = json!(token);
    }
    let broadcast = execution::broadcast_v4_best_pools_inner(
        &broadcast_config,
        &selected_amount,
        &min_profit,
        rpc_url,
        arb_address,
        private_key,
        chain_id,
        relay_urls.as_deref(),
        timeout_seconds,
    )?;
    payload["broadcastExecuted"] = json!(broadcast
        .get("ok")
        .and_then(Value::as_bool)
        .unwrap_or(false));
    payload["broadcast"] = broadcast;
    Ok(())
}

fn run_retryable_rpc_with_failover(
    rpc_urls: &[String],
    mut call: impl FnMut(&str) -> Result<(), String>,
) -> Result<(String, usize), (String, String, usize)> {
    if rpc_urls.is_empty() {
        return match call("") {
            Ok(()) => Ok((String::new(), 0)),
            Err(error) => Err((error, String::new(), 0)),
        };
    }
    let mut failover_count = 0usize;
    let mut last_error = String::new();
    for (index, url) in rpc_urls.iter().enumerate() {
        match call(url) {
            Ok(()) => return Ok((url.clone(), failover_count)),
            Err(error) => {
                if !is_retryable_rpc_error(&error) || index + 1 == rpc_urls.len() {
                    return Err((error, url.clone(), failover_count));
                }
                last_error = error;
                failover_count += 1;
            }
        }
    }
    Err((last_error, String::new(), failover_count))
}

fn maybe_broadcast_with_rpc_failover(
    payload: &mut Value,
    config: &Value,
    preferred_rpc_url: &str,
    arb_address: &str,
    timeout_seconds: u64,
    stop_requested: &dyn Fn() -> bool,
) -> Result<(), String> {
    let broadcast_enabled = config
        .get("broadcastEnabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !broadcast_enabled
        || payload.get("status").and_then(Value::as_str) != Some("profitable_amount_found")
    {
        return Ok(());
    }
    if stop_requested() {
        payload["broadcastSkipped"] = json!("group_stopped");
        payload["stopRequested"] = json!(true);
        return Ok(());
    }
    let primary_rpc_url = if preferred_rpc_url.trim().is_empty() {
        config
            .get("rpcUrl")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        preferred_rpc_url.to_string()
    };
    let mut rpc_urls = config_rpc_urls(config, &primary_rpc_url);
    if let Some(index) = rpc_urls.iter().position(|url| url == &primary_rpc_url) {
        if index != 0 {
            let selected = rpc_urls.remove(index);
            rpc_urls.insert(0, selected);
        }
    }
    let mut attempted_rpc_urls: Vec<String> = Vec::new();
    let result = run_retryable_rpc_with_failover(&rpc_urls, |url| {
        attempted_rpc_urls.push(url.to_string());
        maybe_broadcast(
            payload,
            config,
            url,
            arb_address,
            timeout_seconds,
            stop_requested,
        )
    });
    let broadcast_rpc_metadata = match &result {
        Ok((selected_rpc_url, failover_count)) => json!({
            "attemptedRpcUrls": attempted_rpc_urls,
            "selectedRpcUrl": selected_rpc_url.clone(),
            "rpcFailoverCount": failover_count.clone(),
        }),
        Err((_, selected_rpc_url, failover_count)) => json!({
            "attemptedRpcUrls": attempted_rpc_urls,
            "selectedRpcUrl": selected_rpc_url.clone(),
            "rpcFailoverCount": failover_count.clone(),
        }),
    };
    payload["broadcastRpcMetadata"] = broadcast_rpc_metadata;
    match result {
        Ok(_) => Ok(()),
        Err((error, _, _)) => Err(error),
    }
}

fn record_broadcast_failure(payload: &mut Value, error: String) {
    let error_value = json!({
        "error": error,
        "rawError": error,
        "errorKey": "broadcast_failed",
        "disposition": "VERIFY_CHAIN_STATE",
        "revertData": "",
        "revertSelector": "",
        "rpcMethod": "eth_sendRawTransaction",
        "broadcastChannel": "not_broadcast",
        "submitted": false,
        "txHash": Value::Null,
    });
    payload["broadcast"] = json!({
        "ok": false,
        "attempted": true,
        "failed": true,
        "status": "broadcast_failed",
        "error": error_value.get("error").cloned().unwrap_or(Value::Null),
        "rawError": error_value.get("rawError").cloned().unwrap_or(Value::Null),
        "errorKey": error_value.get("errorKey").cloned().unwrap_or(Value::Null),
        "disposition": error_value.get("disposition").cloned().unwrap_or(Value::Null),
        "revertData": error_value.get("revertData").cloned().unwrap_or(Value::Null),
        "revertSelector": error_value.get("revertSelector").cloned().unwrap_or(Value::Null),
        "rpcMethod": error_value.get("rpcMethod").cloned().unwrap_or(Value::Null),
        "broadcastChannel": error_value.get("broadcastChannel").cloned().unwrap_or(Value::Null),
        "submitted": false,
        "txHash": Value::Null,
    });
    payload["broadcastError"] = error_value;
    payload["broadcastExecuted"] = json!(false);
}

fn is_broadcast_failure_payload(payload: &Value) -> bool {
    payload
        .get("broadcastError")
        .and_then(Value::as_object)
        .is_some()
        || payload
            .get("broadcastError")
            .and_then(Value::as_str)
            .is_some()
        || payload.get("broadcastSkipped").and_then(Value::as_str) == Some("broadcast_failed")
        || payload
            .get("broadcast")
            .and_then(Value::as_object)
            .map(|broadcast| {
                broadcast.get("ok").and_then(Value::as_bool) == Some(false)
                    && broadcast.get("attempted").and_then(Value::as_bool) == Some(true)
            })
            .unwrap_or(false)
}

fn pool_key_from_value(value: &Value) -> Option<V4PoolKey> {
    let currency0 = value.get("currency0")?.as_str()?.to_string();
    let currency1 = value.get("currency1")?.as_str()?.to_string();
    let fee = value.get("fee")?.as_u64()?;
    let tick_spacing = value.get("tickSpacing")?.as_i64()? as i32;
    let hooks = value
        .get("hooks")
        .and_then(Value::as_str)
        .unwrap_or("0x0000000000000000000000000000000000000000")
        .to_string();
    Some(V4PoolKey {
        currency0,
        currency1,
        fee,
        tick_spacing,
        hooks,
    })
}

fn pool_has_live_liquidity(registry: &V4PoolRegistry, pool_id: &str) -> bool {
    let Some(liquidity) = registry.get_liquidity(pool_id) else {
        return true;
    };
    match liquidity.has_liquidity {
        Some(1) => true,
        Some(0) => false,
        _ => true,
    }
}

fn registry_zero_liquidity_pool_ids(registry: &V4PoolRegistry) -> Vec<String> {
    let mut ids: Vec<String> = registry
        .liquidity_by_id
        .iter()
        .filter_map(|(pool_id, row)| {
            let is_zero = row.has_liquidity.map(|value| value == 0).unwrap_or(false);
            if is_zero {
                Some(pool_id.clone())
            } else {
                None
            }
        })
        .collect();
    ids.sort();
    ids
}

fn prefilter_pool_key_section(
    config: &mut Value,
    registry: &V4PoolRegistry,
    keys_key: &str,
    directions_key: &str,
) -> usize {
    let keys = config
        .get(keys_key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let directions = config
        .get(directions_key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if keys.is_empty() || keys.len() != directions.len() {
        return 0;
    }
    let mut kept_keys = Vec::with_capacity(keys.len());
    let mut kept_directions = Vec::with_capacity(keys.len());
    let mut dropped = 0usize;
    for (index, key) in keys.iter().enumerate() {
        let pool_id = pool_key_from_value(key)
            .and_then(|parsed| pool_index::pool_id(&parsed).ok())
            .unwrap_or_default();
        let keep = pool_id.is_empty() || pool_has_live_liquidity(registry, &pool_id);
        if keep {
            kept_keys.push(key.clone());
            kept_directions.push(directions[index].clone());
        } else {
            dropped += 1;
        }
    }
    if kept_keys.is_empty() {
        return 0;
    }
    config[keys_key] = Value::Array(kept_keys);
    config[directions_key] = Value::Array(kept_directions);
    dropped
}

fn prefilter_config_pool_keys(
    config: &mut Value,
    registry: &V4PoolRegistry,
) -> (usize, usize, usize) {
    let entry_dropped =
        prefilter_pool_key_section(config, registry, "entryKeys", "entryDirections");
    let bridge_dropped =
        prefilter_pool_key_section(config, registry, "bridgeKeys", "bridgeDirections");
    let exit_dropped = prefilter_pool_key_section(config, registry, "exitKeys", "exitDirections");
    (entry_dropped, bridge_dropped, exit_dropped)
}

fn run_native_review(
    config_json: &str,
    pool_registry: Option<&V4PoolRegistry>,
    stop_requested: &dyn Fn() -> bool,
    on_phase: &dyn Fn(&str) -> (),
) -> Result<Value, String> {
    let mut config: Value = serde_json::from_str(config_json)
        .map_err(|error| format!("closed-loop config JSON is invalid: {error}"))?;
    let mut prefilter_counts = (0usize, 0usize, 0usize);
    if let Some(registry) = pool_registry {
        prefilter_counts = prefilter_config_pool_keys(&mut config, registry);
    }
    let rpc_url = config
        .get("rpcUrl")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let rpc_urls = config_rpc_urls(&config, &rpc_url);
    let mut selected_rpc_url = rpc_url.clone();
    let arb_address = config
        .get("arbAddress")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if rpc_url.trim().is_empty() || arb_address.trim().is_empty() {
        return Err("closed-loop config is missing rpcUrl or arbAddress".to_string());
    }
    let call_from = config
        .get("callFrom")
        .and_then(Value::as_str)
        .map(str::to_string);
    let timeout_seconds = config
        .get("timeoutSeconds")
        .and_then(Value::as_u64)
        .unwrap_or(20);
    on_phase("reviewing");
    let mut payload = if let Some(routes) = config.get("routes").and_then(Value::as_array) {
        let mut route_results = Vec::with_capacity(routes.len());
        let mut best_route: Option<(BigUint, Value, Value, usize, String, usize)> = None;
        for route in routes {
            let mut route_config = config.clone();
            for field in [
                "token",
                "entryKeys",
                "entryDirections",
                "bridgeKeys",
                "bridgeDirections",
                "exitKeys",
                "exitDirections",
                "initialAmountRaw",
                "minProfitRaw",
                "minProbeAmountRaw",
                "maxProbeAmountRaw",
                "maxProbeAttempts",
                "searchMode",
            ] {
                if let Some(value) = route.get(field) {
                    route_config[field] = value.clone();
                }
            }
            if let Some(registry) = pool_registry {
                let (entry_dropped, bridge_dropped, exit_dropped) =
                    prefilter_config_pool_keys(&mut route_config, registry);
                prefilter_counts.0 += entry_dropped;
                prefilter_counts.1 += bridge_dropped;
                prefilter_counts.2 += exit_dropped;
            }
            let original_search_mode = route_config
                .get("searchMode")
                .and_then(Value::as_str)
                .unwrap_or("inner_single_call")
                .to_string();
            let mut retried_search_mode: Option<String> = None;
            let (mut result, mut route_rpc_url, mut route_rpc_failover_count) =
                match preview_official_v4_with_rpc_failover(
                    &rpc_urls,
                    &route_config,
                    &arb_address,
                    call_from.as_deref(),
                    timeout_seconds,
                ) {
                    Ok((result, selected_url, failover_count)) => {
                        (result, selected_url, failover_count)
                    }
                    Err((error, selected_url, failover_count)) => {
                        if route_error_kind(&error) == "gas_estimation_failed"
                            && original_search_mode == "inner_single_call"
                        {
                            let mut retry_config = route_config.clone();
                            retry_config["searchMode"] = json!("external_ladder");
                            match preview_official_v4_with_rpc_failover(
                                &rpc_urls,
                                &retry_config,
                                &arb_address,
                                call_from.as_deref(),
                                timeout_seconds,
                            ) {
                                Ok((mut retry_payload, retry_url, retry_failover_count)) => {
                                    retried_search_mode = Some("external_ladder".to_string());
                                    retry_payload["retriedFrom"] =
                                        json!("inner_single_call_gas_estimation_failed");
                                    retry_payload["originalError"] = json!(error);
                                    retry_payload["primaryRpcMetadata"] = json!({
                                        "attemptedRpcUrls": rpc_urls,
                                        "selectedRpcUrl": selected_url,
                                        "rpcFailoverCount": failover_count,
                                    });
                                    (retry_payload, retry_url, retry_failover_count)
                                }
                                Err((retry_text, retry_url, retry_failover_count)) => {
                                    retried_search_mode = Some("external_ladder".to_string());
                                    (
                                        json!({
                                            "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
                                            "status": "route_error",
                                            "error": error,
                                            "errorKind": route_error_kind(&error),
                                            "retrySearchMode": "external_ladder",
                                            "retryError": retry_text,
                                            "routeSymbols": route_config.get("routeSymbols").cloned().unwrap_or(Value::Null),
                                            "routeAddresses": route_config.get("routeAddresses").cloned().unwrap_or(Value::Null),
                                            "primaryRpcMetadata": json!({
                                                "attemptedRpcUrls": rpc_urls,
                                                "selectedRpcUrl": selected_url,
                                                "rpcFailoverCount": failover_count,
                                            }),
                                        }),
                                        retry_url,
                                        retry_failover_count,
                                    )
                                }
                            }
                        } else {
                            (
                                json!({
                                    "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
                                    "status": "route_error",
                                    "error": error,
                                    "errorKind": route_error_kind(&error),
                                    "routeSymbols": route_config.get("routeSymbols").cloned().unwrap_or(Value::Null),
                                    "routeAddresses": route_config.get("routeAddresses").cloned().unwrap_or(Value::Null),
                                }),
                                selected_url,
                                failover_count,
                            )
                        }
                    }
                };
            if retried_search_mode.is_none()
                && original_search_mode == "inner_single_call"
                && should_retry_inner_single_call_no_profit(&result)
            {
                let mut retry_config = route_config.clone();
                retry_config["searchMode"] = json!("external_ladder");
                match preview_official_v4_with_rpc_failover(
                    &rpc_urls,
                    &retry_config,
                    &arb_address,
                    call_from.as_deref(),
                    timeout_seconds,
                ) {
                    Ok((mut retry_payload, retry_url, retry_failover_count)) => {
                        retried_search_mode = Some("external_ladder".to_string());
                        retry_payload["retriedFrom"] =
                            json!("inner_single_call_no_profitable_route");
                        retry_payload["originalResult"] = result.clone();
                        retry_payload["primaryRpcMetadata"] = json!({
                            "attemptedRpcUrls": rpc_urls,
                            "selectedRpcUrl": route_rpc_url,
                            "rpcFailoverCount": route_rpc_failover_count,
                        });
                        result = retry_payload;
                        route_rpc_url = retry_url;
                        route_rpc_failover_count = retry_failover_count;
                    }
                    Err((retry_text, retry_url, retry_failover_count)) => {
                        retried_search_mode = Some("external_ladder".to_string());
                        result = json!({
                            "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
                            "status": "route_error",
                            "error": "NoProfitableRoute",
                            "errorKind": route_error_kind("NoProfitableRoute"),
                            "retrySearchMode": "external_ladder",
                            "retryError": retry_text,
                            "routeSymbols": route_config.get("routeSymbols").cloned().unwrap_or(Value::Null),
                            "routeAddresses": route_config.get("routeAddresses").cloned().unwrap_or(Value::Null),
                            "originalResult": result.clone(),
                            "primaryRpcMetadata": json!({
                                "attemptedRpcUrls": rpc_urls,
                                "selectedRpcUrl": route_rpc_url,
                                "rpcFailoverCount": route_rpc_failover_count,
                            }),
                        });
                        route_rpc_url = retry_url;
                        route_rpc_failover_count = retry_failover_count;
                    }
                }
            }
            if retried_search_mode.as_deref() == Some("external_ladder") {
                result["attemptedSearchModes"] = json!(["inner_single_call", "external_ladder"]);
            }
            result["rpcMetadata"] = json!({
                "attemptedRpcUrls": rpc_urls,
                "selectedRpcUrl": route_rpc_url,
                "rpcFailoverCount": route_rpc_failover_count,
            });
            if result.get("routeSymbols").is_none() {
                result["routeSymbols"] = route_config
                    .get("routeSymbols")
                    .cloned()
                    .unwrap_or(Value::Null);
            }
            if result.get("routeAddresses").is_none() {
                result["routeAddresses"] = route_config
                    .get("routeAddresses")
                    .cloned()
                    .unwrap_or(Value::Null);
            }
            let route_index = route_results.len();
            let profitable =
                result.get("status").and_then(Value::as_str) == Some("profitable_amount_found");
            if profitable {
                let profit_raw = result
                    .get("selectedProfitRaw")
                    .and_then(Value::as_str)
                    .and_then(|value| execution::parse_big_uint(&json!(value)).ok())
                    .unwrap_or_default();
                if best_route
                    .as_ref()
                    .map(|(best_profit, _, _, _, _, _)| profit_raw > *best_profit)
                    .unwrap_or(true)
                {
                    best_route = Some((
                        profit_raw,
                        result.clone(),
                        route_config,
                        route_index,
                        route_rpc_url,
                        route_rpc_failover_count,
                    ));
                }
            }
            route_results.push(result);
        }
        let profitable = best_route.is_some();
        let route_errors = route_results
            .iter()
            .filter(|result| result.get("status").and_then(Value::as_str) == Some("route_error"))
            .map(|result| {
                json!({
                    "routeSymbols": result.get("routeSymbols").cloned().unwrap_or(Value::Null),
                    "error": result.get("error").cloned().unwrap_or(Value::Null),
                    "errorKind": result.get("errorKind").cloned().unwrap_or(Value::Null),
                })
            })
            .collect::<Vec<_>>();
        let mut payload = json!({
            "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
            "status": if profitable { "profitable_amount_found" } else { "not_profitable_after_costs" },
            "routes": route_results,
            "routeErrors": route_errors,
        });
        if let Some((
            _profit,
            route_payload,
            route_config,
            best_index,
            best_rpc_url,
            best_rpc_failover_count,
        )) = best_route
        {
            mark_selected_route_direction(&mut payload, best_index);
            apply_selected_route_overrides(&mut payload, &route_payload, &route_config);
            payload["rpcMetadata"] = json!({
                "attemptedRpcUrls": rpc_urls,
                "selectedRpcUrl": best_rpc_url,
                "rpcFailoverCount": best_rpc_failover_count,
            });
            selected_rpc_url = best_rpc_url;
        } else {
            payload["rpcMetadata"] = json!({
                "attemptedRpcUrls": rpc_urls,
                "selectedRpcUrl": Value::Null,
                "rpcFailoverCount": Value::Null,
            });
        }
        payload
    } else if config.get("entryKeys").is_some() {
        match preview_official_v4_with_rpc_failover(
            &rpc_urls,
            &config,
            &arb_address,
            call_from.as_deref(),
            timeout_seconds,
        ) {
            Ok((mut preview_payload, selected_url, failover_count)) => {
                preview_payload["rpcMetadata"] = json!({
                    "attemptedRpcUrls": rpc_urls,
                    "selectedRpcUrl": selected_url,
                    "rpcFailoverCount": failover_count,
                });
                selected_rpc_url = selected_url;
                preview_payload
            }
            Err((error, selected_url, failover_count)) => {
                return Err(format!(
                    "{error} [rpcFailover selectedRpcUrl={selected_url}, rpcFailoverCount={failover_count}]"
                ));
            }
        }
    } else {
        match preview_adaptive_official_v4_with_rpc_failover(
            &rpc_urls,
            &config,
            &arb_address,
            call_from.as_deref(),
            timeout_seconds,
        ) {
            Ok((mut preview_payload, selected_url, failover_count)) => {
                preview_payload["rpcMetadata"] = json!({
                    "attemptedRpcUrls": rpc_urls,
                    "selectedRpcUrl": selected_url,
                    "rpcFailoverCount": failover_count,
                });
                selected_rpc_url = selected_url;
                preview_payload
            }
            Err((error, selected_url, failover_count)) => {
                return Err(format!(
                    "{error} [rpcFailover selectedRpcUrl={selected_url}, rpcFailoverCount={failover_count}]"
                ));
            }
        }
    };
    payload["poolPrefilter"] = json!({
        "method": "registry_liquidity_prefilter",
        "droppedEntryPoolKeys": prefilter_counts.0,
        "droppedBridgePoolKeys": prefilter_counts.1,
        "droppedExitPoolKeys": prefilter_counts.2,
        "totalDroppedPoolKeys": prefilter_counts.0 + prefilter_counts.1 + prefilter_counts.2,
    });
    let defer_broadcast = config
        .get("deferBroadcast")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if defer_broadcast {
        payload["broadcastDeferred"] = json!(true);
        payload["executionQueueRequested"] = json!(config
            .get("broadcastEnabled")
            .and_then(Value::as_bool)
            .unwrap_or(false));
    } else {
        on_phase("broadcasting");
        if let Err(broadcast_error) = maybe_broadcast_with_rpc_failover(
            &mut payload,
            &config,
            &selected_rpc_url,
            &arb_address,
            timeout_seconds,
            stop_requested,
        ) {
            record_broadcast_failure(&mut payload, broadcast_error);
        }
    }
    on_phase("confirming");
    Ok(payload)
}

fn probe_count_from_result(payload: &Value) -> u64 {
    let mut count = payload
        .get("attempts")
        .and_then(Value::as_array)
        .map(|rows| rows.len() as u64)
        .unwrap_or(0);
    if let Some(routes) = payload.get("routes").and_then(Value::as_array) {
        for route in routes {
            count += route
                .get("attempts")
                .and_then(Value::as_array)
                .map(|rows| rows.len() as u64)
                .unwrap_or(0);
        }
    }
    count
}

fn broadcast_count_from_result(payload: &Value) -> u64 {
    for key in ["broadcastExecuted", "autoExecuted"] {
        if let Some(value) = payload.get(key) {
            if let Some(count) = value.as_u64() {
                return count;
            }
            if value.as_bool() == Some(true) {
                return 1;
            }
        }
    }
    if let Some(auto) = payload.get("auto_execute").and_then(Value::as_object) {
        if let Some(value) = auto.get("autoExecuted").and_then(Value::as_u64) {
            return value;
        }
    }
    0
}

fn clear_stop_flag(inner: &PoolInner, group_key: &str) {
    inner.stop_flags.lock().unwrap().remove(group_key);
}

fn set_stop_flag(inner: &PoolInner, group_key: &str) {
    inner
        .stop_flags
        .lock()
        .unwrap()
        .insert(group_key.to_string());
}

fn should_route_to_execution_queue(payload: &Value, command: &SubmitCommand) -> bool {
    let Ok(config) = serde_json::from_str::<Value>(&command.config_json) else {
        return false;
    };
    config.get("deferBroadcast").and_then(Value::as_bool) == Some(true)
        && config.get("broadcastEnabled").and_then(Value::as_bool) == Some(true)
        && payload.get("status").and_then(Value::as_str) == Some("profitable_amount_found")
}

fn enqueue_execution_from_review(
    inner: &PoolInner,
    command: &SubmitCommand,
    payload: &Value,
) -> bool {
    if !inner.execution_queue_enabled || !should_route_to_execution_queue(payload, command) {
        return false;
    }
    let observed_at = now_iso();
    let available_at = observed_at.clone();
    let config_value: Value =
        serde_json::from_str(&command.config_json).unwrap_or_else(|_| Value::Null);
    let manual_broadcast = config_value
        .get("manualBroadcast")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let task = ExecutionTask {
        group_key: command.group_key.clone(),
        chain_id: command.chain_id,
        network: command.network.clone(),
        protocol: command.protocol.clone(),
        protocols: command.protocols.clone(),
        config_json: command.config_json.clone(),
        review_payload: payload.clone(),
        verified_at: observed_at.clone(),
        priority: 1,
        state: if manual_broadcast {
            "waiting".to_string()
        } else {
            "queued".to_string()
        },
        attempts: 0,
        last_error: None,
        created_at: observed_at.clone(),
        updated_at: observed_at,
        available_at: Some(available_at),
    };
    if let Some(supervisor) = inner.broadcast_supervisor.lock().unwrap().as_ref() {
        supervisor.enqueue(task);
        return true;
    }
    false
}

fn run_execution_task(inner: &PoolInner, task: &ExecutionTask) -> Result<Value, String> {
    let mut config: Value = serde_json::from_str(&task.config_json)
        .map_err(|error| format!("execution task config JSON is invalid: {error}"))?;
    let rpc_url = config
        .get("rpcUrl")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let arb_address = config
        .get("arbAddress")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if rpc_url.trim().is_empty() || arb_address.trim().is_empty() {
        return Err("execution config is missing rpcUrl or arbAddress".to_string());
    }
    let timeout_seconds = config
        .get("timeoutSeconds")
        .and_then(Value::as_u64)
        .unwrap_or(20);
    if let Some(rpc_metadata) = task
        .review_payload
        .get("rpcMetadata")
        .and_then(Value::as_object)
    {
        if let Some(selected_rpc_url) = rpc_metadata
            .get("selectedRpcUrl")
            .and_then(Value::as_str)
            .filter(|url| !url.trim().is_empty())
        {
            config["rpcUrl"] = json!(selected_rpc_url);
        }
    }
    let mut payload = task.review_payload.clone();
    let stop_requested = || inner.stop_flags.lock().unwrap().contains(&task.group_key);
    let preferred_rpc_url = config
        .get("rpcUrl")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if let Err(broadcast_error) = maybe_broadcast_with_rpc_failover(
        &mut payload,
        &config,
        &preferred_rpc_url,
        &arb_address,
        timeout_seconds,
        &stop_requested,
    ) {
        record_broadcast_failure(&mut payload, broadcast_error);
    }
    payload["executedBy"] = json!(now_iso());
    Ok(payload)
}

fn record_broadcast_into_state(inner: &PoolInner, group_key: &str, payload: &Value) {
    let mut states = inner.states.lock().unwrap();
    let state = states.entry(group_key.to_string()).or_default();
    state.broadcast_count += broadcast_count_from_result(payload);
    if let Some(broadcast) = payload.get("broadcast").and_then(Value::as_object) {
        let tx_hash = broadcast
            .get("txHash")
            .and_then(Value::as_str)
            .map(|value| value.trim().to_string())
            .unwrap_or_default();
        if !tx_hash.is_empty() {
            let mut history_item = payload.clone();
            history_item["observedAt"] = json!(now_iso());
            state.broadcast_history.push(history_item);
            const BROADCAST_HISTORY_LIMIT: usize = 64;
            if state.broadcast_history.len() > BROADCAST_HISTORY_LIMIT {
                let overflow = state.broadcast_history.len() - BROADCAST_HISTORY_LIMIT;
                state.broadcast_history.drain(..overflow);
            }
            state.last_broadcast_at = Some(now_iso());
            let receipt_status = broadcast
                .get("receipt")
                .and_then(Value::as_object)
                .and_then(|receipt| receipt.get("status"))
                .and_then(Value::as_str)
                .map(|value| value.trim().to_lowercase())
                .unwrap_or_default();
            if receipt_status == "0x1" || receipt_status == "confirmed" {
                state.last_confirmed_at = Some(now_iso());
            }
        }
    }
    if is_broadcast_failure_payload(payload) {
        let mut history_item = payload.clone();
        history_item["observedAt"] = json!(now_iso());
        state.broadcast_failure_history.push(history_item);
        const BROADCAST_HISTORY_LIMIT: usize = 64;
        if state.broadcast_failure_history.len() > BROADCAST_HISTORY_LIMIT {
            let overflow = state.broadcast_failure_history.len() - BROADCAST_HISTORY_LIMIT;
            state.broadcast_failure_history.drain(..overflow);
        }
        state.broadcast_failure_count += 1;
    }
    state.last_result = Some(payload.clone());
    state.updated_at = Some(now_iso());
}

fn worker_loop(
    inner: Arc<PoolInner>,
    group_key: String,
    receiver: Receiver<WorkerControl<SubmitCommand>>,
    thread_id: String,
) {
    let mut poll_interval = Duration::from_secs(30);
    let mut pending_command: Option<SubmitCommand> = None;
    let mut last_wake_generation: u64 = 0;
    let mut idle_since: Option<std::time::Instant> = None;
    let mut idle_exited = false;
    loop {
        if inner.shutdown.load(Ordering::SeqCst) {
            break;
        }

        // A stopped thread stays alive as its own sleeping state machine. It
        // must not scan or broadcast until StartGroup clears the stop flag.
        if inner.stop_flags.lock().unwrap().contains(&group_key) {
            {
                let mut states = inner.states.lock().unwrap();
                let state = states.entry(group_key.clone()).or_default();
                state.group_key = group_key.clone();
                state.thread_id = thread_id.clone();
                state.state = "stopped".to_string();
                state.updated_at = Some(now_iso());
            }
            // Sleep as a stopped state machine. StartGroup sends a command
            // through the retained sender so this thread wakes promptly; the
            // command is held until the stop flag is cleared.
            match receiver.recv_timeout(poll_interval.min(Duration::from_secs(30))) {
                Ok(WorkerControl::Submit(command)) => pending_command = Some(command),
                Ok(WorkerControl::Wake(wake)) => {
                    last_wake_generation = last_wake_generation.max(wake.generation);
                }
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            }
            continue;
        }

        let command = if let Some(command) = pending_command.take() {
            command
        } else {
            match receiver.recv_timeout(poll_interval) {
                Ok(WorkerControl::Submit(command)) => command,
                Ok(WorkerControl::Wake(wake)) => {
                    last_wake_generation = last_wake_generation.max(wake.generation);
                    wait_until_ready(&inner, &group_key);
                    if inner.shutdown.load(Ordering::SeqCst)
                        || inner.stop_flags.lock().unwrap().contains(&group_key)
                    {
                        continue;
                    }
                    let current_generation = inner
                        .states
                        .lock()
                        .unwrap()
                        .get(&group_key)
                        .map_or(0, |state| state.wake_generation);
                    last_wake_generation = last_wake_generation.max(current_generation);
                    match inner.last_commands.lock().unwrap().get(&group_key).cloned() {
                        Some(command) => command,
                        None => continue,
                    }
                }
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                    let stored = inner.last_commands.lock().unwrap().get(&group_key).cloned();
                    match stored {
                        Some(command) => command,
                        None => {
                            match idle_since {
                                Some(started) if started.elapsed() >= Duration::from_secs(60) => {
                                    idle_exited = true;
                                    break;
                                }
                                Some(_) => {}
                                None => idle_since = Some(std::time::Instant::now()),
                            }
                            std::thread::sleep(poll_interval.min(Duration::from_secs(30)));
                            continue;
                        }
                    }
                }
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            }
        };
        idle_since = None;
        if inner.shutdown.load(Ordering::SeqCst) {
            break;
        }
        if let Ok(config) = serde_json::from_str::<Value>(&command.config_json) {
            if let Some(seconds) = config.get("scanIntervalSeconds").and_then(Value::as_u64) {
                poll_interval = Duration::from_secs(seconds.clamp(5, 600));
            }
        }
        wait_until_ready(&inner, &group_key);
        {
            let current_generation = inner
                .states
                .lock()
                .unwrap()
                .get(&group_key)
                .map_or(0, |state| state.wake_generation);
            last_wake_generation = last_wake_generation.max(current_generation);
        }
        if inner.shutdown.load(Ordering::SeqCst) {
            break;
        }
        if inner.stop_flags.lock().unwrap().contains(&group_key) {
            continue;
        }
        {
            let mut states = inner.states.lock().unwrap();
            let state = states.entry(group_key.clone()).or_default();
            state.group_key = group_key.clone();
            state.chain_id = command.chain_id;
            state.network = command.network.clone();
            state.protocol = command.protocol.clone();
            state.protocols = command.protocols.clone();
            state.mode = command.mode.clone();
            state.state = "running".to_string();
            state.thread_id = thread_id.clone();
            state.started_at = Some(now_iso());
            state.updated_at = Some(now_iso());
            state.queued_count += 1;
            state.poll_interval_seconds = poll_interval.as_secs();
            state.next_scan_at = None;
        }
        let context_cache = load_context_cache(&inner, &command);
        let stop_requested = || inner.stop_flags.lock().unwrap().contains(&group_key);
        let mark_phase = |phase: &str| {
            let mut states = inner.states.lock().unwrap();
            let state = states.entry(group_key.clone()).or_default();
            state.phase = phase.to_string();
            state.phase_started_at = Some(now_iso());
            state.updated_at = Some(now_iso());
        };
        let _permit = inner.review_runtime.acquire();
        if inner.shutdown.load(Ordering::SeqCst)
            || inner.stop_flags.lock().unwrap().contains(&group_key)
        {
            continue;
        }
        let result = match context_cache.as_ref() {
            Some((cache, _)) => {
                let data = cache.data.read().unwrap();
                run_native_review(
                    &command.config_json,
                    Some(&data.registry),
                    &stop_requested,
                    &mark_phase,
                )
            }
            None => run_native_review(&command.config_json, None, &stop_requested, &mark_phase),
        };
        let result = result.map(|payload| {
            let direction_results = per_direction_results(&payload);
            (payload, direction_results)
        });
        drop(_permit);
        {
            let mut states = inner.states.lock().unwrap();
            let state = states.entry(group_key.clone()).or_default();
            state.updated_at = Some(now_iso());
            match result {
                Ok((mut payload, direction_results)) => {
                    inner.review_runtime.mark_completed();
                    if enqueue_execution_from_review(&inner, &command, &payload) {
                        payload["executionQueued"] = json!(true);
                        payload["executionQueueAt"] = json!(now_iso());
                    }
                    state.phase = "idle".to_string();
                    state.phase_started_at = None;
                    state.backoff_until = None;
                    state.consecutive_errors = 0;
                    state.last_phase_error = None;
                    state.state = "idle".to_string();
                    state.last_error = None;
                    state.next_scan_at = Some(
                        (chrono::Utc::now()
                            + chrono::Duration::seconds(state.poll_interval_seconds as i64))
                        .to_rfc3339(),
                    );
                    state.probe_count += probe_count_from_result(&payload);
                    state.broadcast_count += broadcast_count_from_result(&payload);
                    if let Some(broadcast) = payload.get("broadcast").and_then(Value::as_object) {
                        let tx_hash = broadcast
                            .get("txHash")
                            .and_then(Value::as_str)
                            .map(|value| value.trim().to_string())
                            .unwrap_or_default();
                        if !tx_hash.is_empty() {
                            let mut history_item = payload.clone();
                            history_item["observedAt"] = json!(now_iso());
                            state.broadcast_history.push(history_item);
                            const BROADCAST_HISTORY_LIMIT: usize = 64;
                            if state.broadcast_history.len() > BROADCAST_HISTORY_LIMIT {
                                let overflow =
                                    state.broadcast_history.len() - BROADCAST_HISTORY_LIMIT;
                                state.broadcast_history.drain(..overflow);
                            }
                            state.last_broadcast_at = Some(now_iso());
                            let receipt_status = broadcast
                                .get("receipt")
                                .and_then(Value::as_object)
                                .and_then(|receipt| receipt.get("status"))
                                .and_then(Value::as_str)
                                .map(|value| value.trim().to_lowercase())
                                .unwrap_or_default();
                            if receipt_status == "0x1" || receipt_status == "confirmed" {
                                state.last_confirmed_at = Some(now_iso());
                            }
                        }
                    }
                    if is_broadcast_failure_payload(&payload) {
                        let mut history_item = payload.clone();
                        history_item["observedAt"] = json!(now_iso());
                        state.broadcast_failure_history.push(history_item);
                        const BROADCAST_HISTORY_LIMIT: usize = 64;
                        if state.broadcast_failure_history.len() > BROADCAST_HISTORY_LIMIT {
                            let overflow =
                                state.broadcast_failure_history.len() - BROADCAST_HISTORY_LIMIT;
                            state.broadcast_failure_history.drain(..overflow);
                        }
                        state.broadcast_failure_count += 1;
                    }
                    if let Some((cache, cache_hit)) = &context_cache {
                        if *cache_hit {
                            state.cache_hit += 1;
                        }
                        let data = cache.data.read().unwrap();
                        let config_value: Value =
                            serde_json::from_str(&command.config_json).unwrap_or(Value::Null);
                        let route_addresses = config_route_addresses(&config_value);
                        let group_pool_ids = data.registry.route_pool_ids(&route_addresses);
                        let pool_group_key = config_value
                            .get("poolGroupKey")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string();
                        let worker_context = json!({
                            "poolCount": data.pool_count,
                            "liquidityCount": data.liquidity_count,
                            "tokenCount": data.token_count,
                            "poolIndexCount": data.registry.pool_by_id.len(),
                            "liquidityIndexCount": data.registry.liquidity_by_id.len(),
                            "tokenIndexCount": data.registry.token_index_count,
                            "poolGroupKey": pool_group_key,
                            "groupPoolIds": group_pool_ids,
                            "routeAddressCount": route_addresses.len(),
                            "loadedAt": data.loaded_at.clone(),
                            "cacheHit": *cache_hit,
                            "registryScope": data.registry_scope.clone(),
                            "cacheTtlSeconds": CONTEXT_CACHE_TTL_SECONDS,
                            "databasePath": command.database_path.clone(),
                            "network": command.network.clone(),
                            "protocol": command.protocol.clone(),
                        });
                        payload["workerContext"] = worker_context;
                    }
                    state.direction_results = direction_results;
                    state.last_result = Some(payload);
                }
                Err(error) => {
                    inner.review_runtime.mark_failed();
                    state.phase = "backoff".to_string();
                    state.phase_started_at = Some(now_iso());
                    state.consecutive_errors = state.consecutive_errors.saturating_add(1);
                    let delay = backoff_seconds(state.consecutive_errors);
                    let next =
                        (chrono::Utc::now() + chrono::Duration::seconds(delay as i64)).to_rfc3339();
                    state.backoff_until = Some(next);
                    state.last_phase_error = Some(error.clone());
                    state.state = "idle".to_string();
                    state.last_error = Some(error);
                    state.next_scan_at = state.backoff_until.clone();
                }
            }
            if inner.stop_flags.lock().unwrap().contains(&group_key)
                || inner.shutdown.load(Ordering::SeqCst)
            {
                state.state = "stopped".to_string();
                state.updated_at = Some(now_iso());
                state.next_scan_at = None;
            }
        }
        if !inner.stop_flags.lock().unwrap().contains(&group_key)
            && !inner.shutdown.load(Ordering::SeqCst)
        {
            enqueue_maintenance(&inner, &command);
            if let Some((cache, _)) = &context_cache {
                let data = cache.data.read().unwrap();
                let zero_pool_ids = registry_zero_liquidity_pool_ids(&data.registry);
                if !zero_pool_ids.is_empty() {
                    enqueue_zero_liquidity_cleanup(&inner, &command, zero_pool_ids);
                }
            }
        }
    }
    inner.group_senders.lock().unwrap().remove(&group_key);
    inner.running.lock().unwrap().remove(&group_key);
    inner.worker_completed_total.fetch_add(1, Ordering::SeqCst);
    if idle_exited {
        inner.worker_idle_exits.fetch_add(1, Ordering::SeqCst);
    }
}

fn spawn_group_worker(
    inner: Arc<PoolInner>,
    group_key: String,
    receiver: Receiver<WorkerControl<SubmitCommand>>,
) {
    let sequence = inner.worker_seq.fetch_add(1, Ordering::SeqCst);
    let thread_id = format!("rust-closed-loop-{sequence}");
    inner.worker_created_total.fetch_add(1, Ordering::SeqCst);
    let worker_inner = Arc::clone(&inner);
    let worker_group = group_key.clone();
    thread::Builder::new()
        .name(thread_id.clone())
        .spawn(move || {
            worker_loop(worker_inner, worker_group, receiver, thread_id);
        })
        .expect("closed-loop worker thread starts");
}

fn send_worker_control(
    inner: &PoolInner,
    group_key: &str,
    control: WorkerControl<SubmitCommand>,
) -> bool {
    inner
        .group_senders
        .lock()
        .unwrap()
        .get(group_key)
        .is_some_and(|sender| sender.send(control).is_ok())
}

fn set_review_wait_deadline_inner(
    inner: &Arc<PoolInner>,
    group_key: &str,
    wait_until: Option<String>,
    apply: bool,
) -> Result<Value, String> {
    let normalized_key = group_key.trim().to_lowercase();
    if normalized_key.is_empty() {
        return Err("group_key is required".to_string());
    }
    let resolved_wait_until = match wait_until.as_deref() {
        Some(value) => chrono::DateTime::parse_from_rfc3339(value)
            .map_err(|error| format!("wait_until must be RFC3339: {error}"))?
            .with_timezone(&chrono::Utc)
            .to_rfc3339(),
        None => now_iso(),
    };
    if !inner
        .group_senders
        .lock()
        .unwrap()
        .contains_key(&normalized_key)
    {
        return Err(format!(
            "closed_loop_group_worker_not_running:{normalized_key}"
        ));
    }
    let previous_wait_until = {
        let states = inner.states.lock().unwrap();
        let state = states
            .get(&normalized_key)
            .ok_or_else(|| format!("closed_loop_group_not_found:{normalized_key}"))?;
        if state.state != "idle" {
            return Err(format!(
                "closed_loop_group_busy:{normalized_key}:{}",
                state.state
            ));
        }
        state
            .backoff_until
            .clone()
            .or_else(|| state.next_scan_at.clone())
    };
    let mut result = json!({
        "accepted": true,
        "group_key": normalized_key,
        "action": "set_wait_deadline",
        "waitUntil": resolved_wait_until,
        "previousWaitUntil": previous_wait_until,
        "waitingDeadlineCleared": true,
        "dryRun": !apply,
    });
    if apply {
        let generation = {
            let mut states = inner.states.lock().unwrap();
            let state = states
                .get_mut(&normalized_key)
                .ok_or_else(|| format!("closed_loop_group_not_found:{normalized_key}"))?;
            state.backoff_until = Some(resolved_wait_until.clone());
            state.next_scan_at = Some(resolved_wait_until.clone());
            state.phase = "waiting".to_string();
            state.updated_at = Some(now_iso());
            state.wake_generation = state.wake_generation.saturating_add(1);
            state.wake_generation
        };
        send_worker_control(
            &inner,
            &normalized_key,
            WorkerControl::Wake(WorkerWakeControl { generation }),
        );
        result["generation"] = json!(generation);
    }
    Ok(result)
}

fn dispatch_review_command(
    inner: &Arc<PoolInner>,
    command: ReviewSupervisorCommand<SubmitCommand>,
) {
    match command {
        ReviewSupervisorCommand::Shutdown => {}
        ReviewSupervisorCommand::StopGroup(group_key) => {
            set_stop_flag(&inner, &group_key);
            {
                let mut states = inner.states.lock().unwrap();
                if let Some(state) = states.get_mut(&group_key) {
                    state.state = "stopped".to_string();
                    state.updated_at = Some(now_iso());
                }
            }
            inner.running.lock().unwrap().remove(&group_key);
        }
        ReviewSupervisorCommand::SetWaitDeadline {
            group_key,
            wait_until,
            apply,
        } => {
            let _ = set_review_wait_deadline_inner(inner, &group_key, wait_until, apply);
        }
        ReviewSupervisorCommand::StartGroup(group_key) => {
            let start_command = {
                let mut last_commands = inner.last_commands.lock().unwrap();
                let existing = last_commands.get(&group_key).cloned();
                if existing.is_none() {
                    last_commands.remove(&group_key);
                }
                existing
            };
            if let Some(command) = start_command {
                clear_stop_flag(&inner, &group_key);
                {
                    let mut states = inner.states.lock().unwrap();
                    if let Some(state) = states.get_mut(&group_key) {
                        state.backoff_until = None;
                        state.consecutive_errors = 0;
                        state.phase = "idle".to_string();
                        state.phase_started_at = None;
                    }
                }
                let mut senders = inner.group_senders.lock().unwrap();
                if let Some(sender) = senders.get(&group_key) {
                    let _ = sender.send(WorkerControl::Submit(command));
                } else {
                    let (sender, receiver) = channel();
                    senders.insert(group_key.clone(), sender);
                    let sender = senders.get(&group_key).cloned();
                    if let Some(sender) = sender {
                        let _ = sender.send(WorkerControl::Submit(command));
                    }
                    let worker_inner = Arc::clone(&inner);
                    let worker_group = group_key.clone();
                    // Only a brand-new worker thread is spawned here.
                    // Existing workers keep their own receiver and sleep
                    // state machine; group_senders was kept while stopped.
                    spawn_group_worker(worker_inner, worker_group, receiver);
                }
                drop(senders);
                inner.running.lock().unwrap().insert(group_key);
            }
        }
        ReviewSupervisorCommand::Submit(command) => {
            let group_key = command.group_key.clone();
            {
                let mut last_commands = inner.last_commands.lock().unwrap();
                last_commands.insert(group_key.clone(), command.clone());
            }
            let mut should_spawn = false;
            {
                let mut senders = inner.group_senders.lock().unwrap();
                if let Some(sender) = senders.get(&group_key) {
                    let _ = sender.send(WorkerControl::Submit(command));
                } else {
                    let (sender, receiver) = channel();
                    senders.insert(group_key.clone(), sender);
                    let sender = senders.get(&group_key).cloned();
                    if let Some(sender) = sender {
                        let _ = sender.send(WorkerControl::Submit(command));
                    }
                    should_spawn = true;
                    let worker_inner = Arc::clone(&inner);
                    let worker_group = group_key.clone();
                    spawn_group_worker(worker_inner, worker_group, receiver);
                }
            }
            if should_spawn {
                let mut running = inner.running.lock().unwrap();
                running.insert(group_key.clone());
            }
        }
    }
}

impl ClosedLoopWorkerPool {
    pub(crate) fn start(
        max_workers: usize,
        database_path: Option<String>,
        maintenance_handler: Option<MaintenanceHandler>,
        maintenance_interval_seconds: u64,
        binance_interval_seconds: u64,
        binance_enabled: bool,
        execution_workers: usize,
        execution_queue_enabled: bool,
    ) -> Self {
        let concurrency_limit = max_workers.clamp(1, 32);
        let execution_worker_count = execution_workers.clamp(1, 8);
        let database_path = database_path.or_else(|| active_runtime_database_path(None));
        let (binance_tx, binance_rx) = if binance_enabled {
            let (tx, rx) = channel();
            (Some(tx), Some(rx))
        } else {
            (None, None)
        };
        let inner = Arc::new(PoolInner {
            review_supervisor: Mutex::new(None),
            states: Mutex::new(HashMap::new()),
            review_runtime: ReviewRuntime::new(concurrency_limit),
            broadcast_supervisor: Mutex::new(None),
            execution_workers: execution_worker_count,
            execution_queue_enabled,
            stop_flags: Mutex::new(HashSet::new()),
            running: Mutex::new(HashSet::new()),
            group_senders: Mutex::new(HashMap::new()),
            last_commands: Mutex::new(HashMap::new()),
            contexts: Mutex::new(HashMap::new()),
            maintenance_handler,
            maintenance_interval_seconds: maintenance_interval_seconds.max(1),
            maintenance_tx: Mutex::new(None),
            maintenance_join: Mutex::new(None),
            maintenance_state: Mutex::new(MaintenanceState::default()),
            maintenance_scopes: Mutex::new(HashSet::new()),
            binance_interval_seconds: binance_interval_seconds.max(60),
            binance_tx: Mutex::new(binance_tx),
            binance_join: Mutex::new(None),
            binance_state: Mutex::new(BinanceCollectorState::default()),
            shutdown: AtomicBool::new(false),
            worker_seq: AtomicU64::new(0),
            worker_created_total: AtomicU64::new(0),
            worker_completed_total: AtomicU64::new(0),
            worker_idle_exits: AtomicU64::new(0),
            database_path,
        });
        if inner.maintenance_handler.is_some() {
            let (maintenance_tx, maintenance_rx) = channel();
            inner.maintenance_tx.lock().unwrap().replace(maintenance_tx);
            let maintenance_inner = Arc::clone(&inner);
            let maintenance_join = spawn_maintenance_worker(maintenance_inner, maintenance_rx);
            inner
                .maintenance_join
                .lock()
                .unwrap()
                .replace(maintenance_join);
        }
        if binance_enabled {
            let collector_inner = Arc::clone(&inner);
            let binance_join = spawn_binance_collector(
                collector_inner,
                binance_rx.expect("BNB collector receiver exists when enabled"),
            );
            inner.binance_join.lock().unwrap().replace(binance_join);
            inner.binance_state.lock().unwrap().enabled = true;
        }
        if execution_queue_enabled {
            let handler_inner = Arc::downgrade(&inner);
            let skip_inner = Arc::downgrade(&inner);
            let supervisor = BroadcastSupervisor::start(
                execution_worker_count,
                "rust-broadcast-manager-exec",
                move |task| {
                    let inner = handler_inner
                        .upgrade()
                        .ok_or_else(|| "closed-loop worker pool was dropped".to_string())?;
                    let result = run_execution_task(&inner, task);
                    if let Ok(payload) = &result {
                        record_broadcast_into_state(&inner, &task.group_key, payload);
                    }
                    result
                },
                move |task| {
                    let inner = skip_inner.upgrade()?;
                    if inner.stop_flags.lock().unwrap().contains(&task.group_key) {
                        Some("group_stopped".to_string())
                    } else {
                        None
                    }
                },
            );
            inner
                .broadcast_supervisor
                .lock()
                .unwrap()
                .replace(supervisor);
        }
        let dispatcher_inner = Arc::downgrade(&inner);
        let supervisor = ReviewSupervisor::start("review-manager-main", move |command| {
            if let Some(inner) = dispatcher_inner.upgrade() {
                dispatch_review_command(&inner, command);
            }
        });
        inner.review_supervisor.lock().unwrap().replace(supervisor);
        Self {
            inner,
            stopped: false,
        }
    }

    fn send_command(&self, command: ReviewSupervisorCommand<SubmitCommand>) -> bool {
        if let Some(supervisor) = self.inner.review_supervisor.lock().unwrap().as_ref() {
            return supervisor.send(command);
        }
        false
    }

    fn submit_inner(
        &self,
        group_key: &str,
        config_json: &str,
        chain_id: Option<i64>,
        protocols: Vec<String>,
        mode: &str,
        _priority: i64,
        database_path: Option<String>,
        network: Option<String>,
        protocol: Option<String>,
    ) -> Result<Value, String> {
        if self.stopped {
            return Err("closed-loop worker pool is stopped".to_string());
        }
        let normalized_key = group_key.trim().to_lowercase();
        let normalized_key = if normalized_key.is_empty() {
            "fallback".to_string()
        } else {
            normalized_key
        };
        let normalized_protocols = protocols
            .into_iter()
            .map(|value| value.trim().to_lowercase())
            .filter(|value| !value.is_empty())
            .collect::<Vec<String>>();
        let normalized_mode = match mode.trim().to_lowercase().as_str() {
            "broadcast" => "broadcast".to_string(),
            "deep" => "deep".to_string(),
            _ => "scan".to_string(),
        };
        let command = SubmitCommand {
            group_key: normalized_key.clone(),
            chain_id,
            network,
            protocol,
            protocols: normalized_protocols,
            mode: normalized_mode,
            config_json: config_json.to_string(),
            database_path,
        };
        {
            let mut states = self.inner.states.lock().unwrap();
            let state = states.entry(normalized_key.clone()).or_default();
            state.group_key = normalized_key.clone();
            state.chain_id = chain_id;
            state.network = command.network.clone();
            state.protocol = command.protocol.clone();
            state.state = "queued".to_string();
            state.updated_at = Some(now_iso());
        }
        self.inner.review_runtime.mark_claimed();
        self.send_command(ReviewSupervisorCommand::Submit(command));
        Ok(json!({
            "accepted": true,
            "group_key": normalized_key,
            "engine": "rust_native_closed_loop",
        }))
    }

    fn status_value_inner(&self, include_payloads: bool, include_workers: bool) -> Value {
        let states = self.inner.states.lock().unwrap();
        // The pool is a one-group-per-resident-thread state machine, so the
        // reported worker count is the number of group states (resident
        // threads), not a scheduler concurrency cap.
        let resident_workers = states.len();
        let workers: Vec<Value> = if include_workers {
            let mut workers: Vec<Value> = states
                .values()
                .map(|state| worker_state_value(state, include_payloads))
                .collect();
            workers.sort_by(|left, right| {
                left.get("group_key")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .cmp(right.get("group_key").and_then(Value::as_str).unwrap_or(""))
            });
            let live_group_keys: HashSet<String> = self
                .inner
                .group_senders
                .lock()
                .unwrap()
                .keys()
                .cloned()
                .collect();
            for worker in &mut workers {
                let group_key = worker
                    .get("group_key")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                worker["thread_live"] = json!(live_group_keys.contains(&group_key));
            }
            workers
        } else {
            Vec::new()
        };
        let running_groups = self.inner.running.lock().unwrap().len();
        let pending_tasks: usize = self.inner.group_senders.lock().unwrap().len();
        let review_runtime = self.inner.review_runtime.status();
        let active_concurrency = review_runtime.active_reviews;
        let concurrency_limit = review_runtime.available_slots + active_concurrency;
        let review_main_running = self
            .inner
            .review_supervisor
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|supervisor| supervisor.status().running);
        let worker_created_total = self.inner.worker_created_total.load(Ordering::SeqCst);
        let worker_completed_total = self.inner.worker_completed_total.load(Ordering::SeqCst);
        let worker_idle_exits = self.inner.worker_idle_exits.load(Ordering::SeqCst);
        let mut maintenance_scopes: Vec<String> = self
            .inner
            .maintenance_scopes
            .lock()
            .unwrap()
            .iter()
            .cloned()
            .collect();
        maintenance_scopes.sort();
        let maintenance = self.inner.maintenance_state.lock().unwrap();
        let maintenance_last_result = if include_payloads {
            maintenance.last_result.clone()
        } else {
            None
        };
        let maintenance_result_summary = maintenance
            .last_result
            .as_ref()
            .map(compact_maintenance_result);
        let mut maintenance_status = json!({
            "inProgress": maintenance.in_progress,
            "queuedCount": maintenance.queued_count,
            "completedCount": maintenance.completed_count,
            "failedCount": maintenance.failed_count,
            "registeredScopes": maintenance_scopes,
            "lastStartedAt": maintenance.last_started_at,
            "lastCompletedAt": maintenance.last_completed_at,
            "lastStatus": maintenance.last_status,
            "lastError": maintenance.last_error,
            "lastResult": maintenance_last_result,
            "resultSummary": maintenance_result_summary,
            "immediateReviewTriggerCount": maintenance.immediate_review_trigger_count,
            "lastImmediateReviewTriggeredAt": maintenance.last_immediate_review_triggered_at,
            "lastImmediateReviewWokenGroups": maintenance.last_immediate_review_woken_groups,
        });
        if !include_payloads {
            maintenance_status
                .as_object_mut()
                .unwrap()
                .remove("lastResult");
        }
        drop(maintenance);
        let binance_interval_seconds = self.inner.binance_interval_seconds;
        let binance = self.inner.binance_state.lock().unwrap();
        let binance_last_result = if include_payloads {
            binance.last_result.clone()
        } else {
            None
        };
        let mut binance_status = json!({
            "enabled": binance.enabled,
            "running": binance.running,
            "inProgress": binance.in_progress,
            "intervalSeconds": binance_interval_seconds,
            "collectedCount": binance.collected_count,
            "failedCount": binance.failed_count,
            "lastStartedAt": binance.last_started_at,
            "lastCompletedAt": binance.last_completed_at,
            "lastStatus": binance.last_status,
            "lastError": binance.last_error,
            "lastResult": binance_last_result,
        });
        if !include_payloads {
            binance_status.as_object_mut().unwrap().remove("lastResult");
        }
        drop(binance);
        let context_cache_scopes: Vec<Value> = self
            .inner
            .contexts
            .lock()
            .unwrap()
            .values()
            .map(|cache| {
                let data = cache.data.read().unwrap();
                json!({
                    "registryScope": data.registry_scope.clone(),
                    "poolCount": data.pool_count,
                    "liquidityCount": data.liquidity_count,
                    "tokenCount": data.token_count,
                    "loadedAt": data.loaded_at.clone(),
                })
            })
            .collect();
        let summary_counts: std::collections::BTreeMap<String, u64> = {
            let mut counts = std::collections::BTreeMap::new();
            for state in states.values() {
                *counts.entry(state.state.clone()).or_insert(0) += 1;
            }
            counts
        };
        drop(states);
        let broadcast_status = self
            .inner
            .broadcast_supervisor
            .lock()
            .unwrap()
            .as_ref()
            .map(|supervisor| supervisor.status(if include_payloads { 5 } else { 0 }));
        json!({
            "engine": "rust_native_closed_loop",
            "maxWorkers": resident_workers,
            "residentWorkers": resident_workers,
            "concurrencyLimit": concurrency_limit,
            "activeConcurrency": active_concurrency,
            "createdTotal": worker_created_total,
            "active": active_concurrency,
            "completed": worker_completed_total,
            "idleExits": worker_idle_exits,
            "runningGroups": running_groups,
            "pendingTasks": pending_tasks,
            "workers": workers,
            "summary": summary_counts,
            "maintenance": maintenance_status,
            "contextCache": {
                "scopeCount": context_cache_scopes.len(),
                "scopeTtlSeconds": CONTEXT_CACHE_TTL_SECONDS,
                "scopes": context_cache_scopes,
            },
            "binanceCollector": binance_status,
            "executionQueue": {
                "enabled": self.inner.execution_queue_enabled,
                "workers": self.inner.execution_workers,
                "activeExecutions": broadcast_status
                    .as_ref()
                    .map_or(0, |status| status.active_executions),
                "queued": broadcast_status.as_ref().map_or(0, |status| status.queued),
                "running": broadcast_status.as_ref().map_or(0, |status| status.running),
                "done": broadcast_status.as_ref().map_or(0, |status| status.done),
                "error": broadcast_status.as_ref().map_or(0, |status| status.error),
                "skipped": broadcast_status.as_ref().map_or(0, |status| status.skipped),
                "waiting": broadcast_status.as_ref().map_or(0, |status| status.waiting),
                "recent": broadcast_status
                    .as_ref()
                    .map_or_else(Vec::new, |status| status.recent.clone()),
            },
            "reviewQueue": {
                "enabled": review_main_running,
                "mainThreadRunning": review_main_running,
                "running": active_concurrency,
                "claimed": review_runtime.claimed,
                "completed": review_runtime.completed,
                "failed": review_runtime.failed,
                "isolated": {
                    "available": false,
                    "reason": "native_isolated_count_not_exposed",
                },
                "skipped": {
                    "available": false,
                    "reason": "native_skipped_count_not_exposed",
                },
            },
        })
    }

    fn status_value(&self) -> Value {
        self.status_value_inner(true, true)
    }

    fn compact_status_value(&self) -> Value {
        self.status_value_inner(false, true)
    }

    fn summary_status_value(&self) -> Value {
        self.status_value_inner(false, false)
    }
}

fn worker_state_value(state: &WorkerState, include_payloads: bool) -> Value {
    let recent_failures: Vec<Value> = state
        .broadcast_failure_history
        .iter()
        .rev()
        .take(5)
        .map(|item| {
            let broadcast = item.get("broadcast").and_then(Value::as_object);
            json!({
                "observedAt": item.get("observedAt").cloned().unwrap_or(Value::Null),
                "status": item.get("status").cloned().unwrap_or(Value::Null),
                "error": item
                    .get("broadcastError")
                    .cloned()
                    .or_else(|| broadcast.and_then(|value| value.get("error")).cloned())
                    .unwrap_or(Value::Null),
                "profitable": item
                    .get("profitable_amount_found")
                    .cloned()
                    .unwrap_or(Value::Null),
                "selectedAmountRaw": item
                    .get("selectedAmountRaw")
                    .cloned()
                    .unwrap_or(Value::Null),
                "selectedProfitRaw": item
                    .get("selectedProfitRaw")
                    .cloned()
                    .unwrap_or(Value::Null),
                "minProfitRaw": item.get("minProfitRaw").cloned().unwrap_or(Value::Null),
            })
        })
        .collect();
    let last_failure_at = state
        .broadcast_failure_history
        .last()
        .and_then(|item| item.get("observedAt"))
        .cloned();
    let mut worker = json!({
        "group_key": state.group_key,
        "chain_id": state.chain_id,
        "network": state.network,
        "protocol": state.protocol,
        "protocols": state.protocols,
        "mode": state.mode,
        "state": state.state,
        "thread_id": state.thread_id,
        "started_at": state.started_at,
        "updated_at": state.updated_at,
        "probe_count": state.probe_count,
        "broadcast_count": state.broadcast_count,
        "broadcast_failure_count": state.broadcast_failure_count,
        "last_broadcast_failure_at": last_failure_at,
        "broadcast_failure_recent": recent_failures,
        "cache_hit": state.cache_hit,
        "queued_count": state.queued_count,
        "last_error": state.last_error,
        "phase": state.phase,
        "phase_started_at": state.phase_started_at,
        "backoff_until": state.backoff_until,
        "wake_generation": state.wake_generation,
        "consecutive_errors": state.consecutive_errors,
        "last_phase_error": state.last_phase_error,
        "last_broadcast_at": state.last_broadcast_at,
        "last_confirmed_at": state.last_confirmed_at,
        "poll_interval_seconds": state.poll_interval_seconds,
        "next_scan_at": state.next_scan_at,
    });
    let waiting_for_review =
        state.state == "idle" && (state.backoff_until.is_some() || state.next_scan_at.is_some());
    worker["review_wait_ready"] = json!(waiting_for_review);
    if !include_payloads {
        if let Some(context) = state
            .last_result
            .as_ref()
            .and_then(|result| result.get("workerContext"))
        {
            worker["prefilterContext"] = json!({
                "registryScope": context.get("registryScope"),
                "poolCount": context.get("poolCount"),
                "liquidityCount": context.get("liquidityCount"),
                "tokenCount": context.get("tokenCount"),
                "loadedAt": context.get("loadedAt"),
                "cacheHit": context.get("cacheHit"),
                "cacheTtlSeconds": context.get("cacheTtlSeconds"),
            });
        }
    }
    if include_payloads {
        worker["last_result"] = state.last_result.clone().unwrap_or(Value::Null);
        worker["broadcast_history"] = json!(state.broadcast_history.clone());
        worker["broadcast_failure_history"] = json!(state.broadcast_failure_history.clone());
        worker["direction_results"] = json!(state
            .direction_results
            .iter()
            .map(|item| {
                let mut item = item.clone();
                item["checkedAt"] = state
                    .updated_at
                    .clone()
                    .map(Value::String)
                    .unwrap_or(Value::Null);
                item
            })
            .collect::<Vec<_>>());
    }
    worker
}

#[pymethods]
impl ClosedLoopWorkerPool {
    #[new]
    #[pyo3(signature = (max_workers=3, database_path=None, maintenance_handler=None, maintenance_interval_seconds=600, binance_interval_seconds=300, binance_enabled=false, execution_workers=2, execution_queue_enabled=true))]
    fn new(
        max_workers: usize,
        database_path: Option<String>,
        maintenance_handler: Option<MaintenanceHandler>,
        maintenance_interval_seconds: u64,
        binance_interval_seconds: u64,
        binance_enabled: bool,
        execution_workers: usize,
        execution_queue_enabled: bool,
    ) -> Self {
        Self::start(
            max_workers,
            database_path,
            maintenance_handler,
            maintenance_interval_seconds,
            binance_interval_seconds,
            binance_enabled,
            execution_workers,
            execution_queue_enabled,
        )
    }

    #[pyo3(signature = (group_key, config_json, chain_id=None, protocols=None, mode="deep", priority=0, database_path=None, network=None, protocol=None))]
    fn submit(
        &self,
        py: Python<'_>,
        group_key: String,
        config_json: String,
        chain_id: Option<i64>,
        protocols: Option<Vec<String>>,
        mode: &str,
        priority: i64,
        database_path: Option<String>,
        network: Option<String>,
        protocol: Option<String>,
    ) -> PyResult<PyObject> {
        let result = self
            .submit_inner(
                &group_key,
                &config_json,
                chain_id,
                protocols.unwrap_or_default(),
                mode,
                priority,
                database_path,
                network,
                protocol,
            )
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        Ok(json_result_to_py(py, result))
    }

    #[pyo3(signature = (network=None, chain_id=None, protocol=None))]
    fn register_maintenance_scope(
        &self,
        py: Python<'_>,
        network: Option<String>,
        chain_id: Option<i64>,
        protocol: Option<String>,
    ) -> PyObject {
        let scope = maintenance_scope(network.as_deref(), chain_id, protocol.as_deref());
        let mut scopes = self.inner.maintenance_scopes.lock().unwrap();
        let registered = scopes.insert(scope);
        json_result_to_py(
            py,
            json!({
                "accepted": registered,
                "scope": maintenance_scope(
                    network.as_deref(),
                    chain_id,
                    protocol.as_deref(),
                ),
            }),
        )
    }

    fn status_json(&self, py: Python<'_>) -> PyObject {
        let payload = py.allow_threads(|| self.status_value());
        json_result_to_py(py, payload)
    }

    fn compact_status_json(&self, py: Python<'_>) -> PyObject {
        let payload = py.allow_threads(|| self.compact_status_value());
        json_result_to_py(py, payload)
    }

    fn summary_status_json(&self, py: Python<'_>) -> PyObject {
        let payload = py.allow_threads(|| self.summary_status_value());
        json_result_to_py(py, payload)
    }

    #[pyo3(signature = (group_key, wait_until=None, dry_run=false))]
    fn wake_waiting_for_review(
        &self,
        py: Python<'_>,
        group_key: String,
        wait_until: Option<String>,
        dry_run: bool,
    ) -> PyResult<PyObject> {
        if self.stopped {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "closed-loop worker pool is stopped",
            ));
        }
        let normalized_key = group_key.trim().to_lowercase();
        if normalized_key.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "group_key is required",
            ));
        }
        let preview =
            set_review_wait_deadline_inner(&self.inner, &normalized_key, wait_until.clone(), false)
                .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        if dry_run {
            let mut result = preview;
            result["accepted"] = json!(true);
            result["transitionQueued"] = json!(false);
            result["wouldTransition"] = json!(true);
            return Ok(json_result_to_py(py, result));
        }
        if !self.send_command(ReviewSupervisorCommand::SetWaitDeadline {
            group_key: normalized_key.clone(),
            wait_until,
            apply: true,
        }) {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "review state transition queue is unavailable",
            ));
        }
        let mut result = preview;
        result["accepted"] = json!(true);
        result["transitionQueued"] = json!(true);
        result["dryRun"] = json!(false);
        Ok(json_result_to_py(py, result))
    }

    #[pyo3(signature = (group_key, wait_until=None, dry_run=false))]
    fn wake_waiting_for_broadcast(
        &self,
        py: Python<'_>,
        group_key: String,
        wait_until: Option<String>,
        dry_run: bool,
    ) -> PyResult<PyObject> {
        if self.stopped {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "closed-loop worker pool is stopped",
            ));
        }
        let normalized_key = group_key.trim().to_lowercase();
        if normalized_key.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "group_key is required",
            ));
        }
        let supervisor_guard = self.inner.broadcast_supervisor.lock().unwrap();
        let Some(supervisor) = supervisor_guard.as_ref() else {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "broadcast executor is not enabled",
            ));
        };
        let outcome = supervisor
            .set_wait_until(&normalized_key, wait_until.as_deref(), !dry_run)
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        Ok(json_result_to_py(py, outcome))
    }

    fn start_group(&self, py: Python<'_>, group_key: String) -> PyResult<PyObject> {
        let normalized_key = group_key.trim().to_lowercase();
        if normalized_key.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "group_key is required",
            ));
        }
        if self.stopped {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "closed-loop worker pool is stopped",
            ));
        }
        self.send_command(ReviewSupervisorCommand::StartGroup(normalized_key.clone()));
        Ok(json_result_to_py(
            py,
            json!({
                "accepted": true,
                "group_key": normalized_key,
                "action": "start",
            }),
        ))
    }

    fn stop_group(&self, py: Python<'_>, group_key: String) -> PyResult<PyObject> {
        let normalized_key = group_key.trim().to_lowercase();
        if normalized_key.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "group_key is required",
            ));
        }
        self.send_command(ReviewSupervisorCommand::StopGroup(normalized_key.clone()));
        Ok(json_result_to_py(
            py,
            json!({
                "accepted": true,
                "group_key": normalized_key,
                "action": "stop",
            }),
        ))
    }

    fn summary(&self, py: Python<'_>) -> PyObject {
        let payload = self.status_value();
        json_result_to_py(
            py,
            payload.get("summary").cloned().unwrap_or_else(|| json!({})),
        )
    }

    fn stop(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        self.inner.shutdown.store(true, Ordering::SeqCst);
        self.inner.group_senders.lock().unwrap().clear();
        {
            let maintenance_tx = self.inner.maintenance_tx.lock().unwrap().take();
            if let Some(sender) = maintenance_tx {
                let _ = sender.send(MaintenanceWorkerCommand::Shutdown);
            }
        }
        {
            let binance_tx = self.inner.binance_tx.lock().unwrap().take();
            if let Some(sender) = binance_tx {
                let _ = sender.send(BinanceCollectorCommand::Shutdown);
            }
        }
        if let Some(supervisor) = self.inner.broadcast_supervisor.lock().unwrap().take() {
            supervisor.request_shutdown();
        }
        if let Some(supervisor) = self.inner.review_supervisor.lock().unwrap().take() {
            supervisor.stop();
        }
        // Detach the maintenance worker instead of joining it: it may be
        // inside a Python callback that needs the GIL, so joining from
        // Python deadlocks, and waiting for a long callback would make
        // stop() unbounded. Dropping the sender makes it exit on disconnect.
        self.inner.maintenance_join.lock().unwrap().take();
        self.inner.binance_join.lock().unwrap().take();
    }
}

impl Drop for ClosedLoopWorkerPool {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn v4_pool_registry_indexes_by_id_and_token() {
        let pool_rows = json!([
            {
                "pool_id": "0xAAA",
                "token0": "0xaaa",
                "token1": "0xbbb",
                "fee": 3000,
            },
            {
                "pool_id": "0xBBB",
                "token0": "0xbbb",
                "token1": "0xccc",
                "fee": 3000,
            },
            {
                "pool_id": "0xCCC",
                "token0": "0xaaa",
                "token1": "0xccc",
                "fee": 3000,
            },
            {
                "pool_id": "0xDDD",
                "token0": "0xddd",
                "token1": "0xeee",
                "fee": 3000,
            },
        ]);
        let liquidity_rows = json!([
            {"pool_id": "0xAAA", "has_liquidity": 1, "liquidity": "100"},
            {"pool_id": "0xBBB", "has_liquidity": 1, "liquidity": "200"},
            {"pool_id": "0xCCC", "has_liquidity": 0, "liquidity": "0"},
        ]);
        let token_rows = json!([
            {"token_address": "0xaaa", "token_symbol": "AAA"},
            {"token_address": "0xbbb", "token_symbol": "BBB"},
            {"token_address": "0xccc", "token_symbol": "CCC"},
        ]);
        let rows = |value: &Value| value.as_array().cloned().unwrap_or_default();
        let registry = V4PoolRegistry::from_rows(
            &rows(&pool_rows),
            &rows(&liquidity_rows),
            &rows(&token_rows),
        );

        assert_eq!(registry.pool_by_id.len(), 4);
        assert_eq!(
            registry.pool_ids_by_token.get("0xaaa").map(|ids| ids.len()),
            Some(2)
        );
        assert_eq!(
            registry.get_pool_info("0xAAA").map(|pool| &pool.token0),
            Some(&"0xaaa".to_string())
        );
        assert_eq!(
            registry.get_pool_info("0xAAA").map(|pool| &pool.token1),
            Some(&"0xbbb".to_string())
        );
        assert_eq!(
            registry.get_liquidity("0xBBB").map(|row| row.has_liquidity),
            Some(Some(1))
        );
        assert_eq!(registry.token_index_count, 3);
        assert!(registry.get_pool_info("0xmissing").is_none());

        let route = vec![
            "0xaaa".to_string(),
            "0xbbb".to_string(),
            "0xccc".to_string(),
            "0xaaa".to_string(),
        ];
        let route_ids = registry.route_pool_ids(&route);
        assert_eq!(route_ids, vec!["0xaaa", "0xbbb", "0xccc"]);
        assert_eq!(
            registry.route_pool_ids(&["0xddd".to_string(), "0xccc".to_string()]),
            Vec::<String>::new()
        );
    }

    #[test]
    fn prefilter_drops_known_empty_liquidity_pool_keys() {
        let token_a = "0x0000000000000000000000000000000000000001";
        let token_b = "0x0000000000000000000000000000000000000002";
        let token_c = "0x0000000000000000000000000000000000000003";
        let zero_hooks = "0x0000000000000000000000000000000000000000";
        let entry_key = json!({
            "currency0": token_a,
            "currency1": token_b,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": zero_hooks,
        });
        let bridge_key = json!({
            "currency0": token_a,
            "currency1": token_b,
            "fee": 500,
            "tickSpacing": 10,
            "hooks": zero_hooks,
        });
        let exit_key = json!({
            "currency0": token_b,
            "currency1": token_c,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": zero_hooks,
        });
        let pool_id_from = |key: &Value| {
            let parsed = pool_key_from_value(key).unwrap();
            pool_index::pool_id(&parsed).unwrap()
        };
        let entry_pool_id = pool_id_from(&entry_key);
        let bridge_pool_id = pool_id_from(&bridge_key);
        let exit_pool_id = pool_id_from(&exit_key);

        let pool_rows = json!([
            {
                "pool_id": entry_pool_id,
                "token0": token_a,
                "token1": token_b,
                "fee": 3000,
            },
            {
                "pool_id": bridge_pool_id,
                "token0": token_a,
                "token1": token_b,
                "fee": 500,
            },
            {
                "pool_id": exit_pool_id,
                "token0": token_b,
                "token1": token_c,
                "fee": 3000,
            },
        ]);
        let liquidity_rows = json!([
            {"pool_id": entry_pool_id, "has_liquidity": 1, "liquidity": "100"},
            {"pool_id": bridge_pool_id, "has_liquidity": 0, "liquidity": "0"},
            {"pool_id": exit_pool_id, "has_liquidity": 1, "liquidity": "200"},
        ]);
        let rows = |value: &Value| value.as_array().cloned().unwrap_or_default();
        let registry = V4PoolRegistry::from_rows(&rows(&pool_rows), &rows(&liquidity_rows), &[]);
        let mut config = json!({
            "entryKeys": [entry_key],
            "entryDirections": [true],
            "bridgeKeys": [bridge_key, {
                "currency0": token_a,
                "currency1": token_c,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": zero_hooks,
            }],
            "bridgeDirections": [true, true],
            "exitKeys": [exit_key],
            "exitDirections": [true],
        });

        let (entry_dropped, bridge_dropped, exit_dropped) =
            prefilter_config_pool_keys(&mut config, &registry);
        assert_eq!((entry_dropped, bridge_dropped, exit_dropped), (0, 1, 0));
        assert_eq!(config["entryKeys"].as_array().unwrap().len(), 1);
        assert_eq!(config["bridgeKeys"].as_array().unwrap().len(), 1);
        assert_eq!(config["exitKeys"].as_array().unwrap().len(), 1);
        assert_eq!(
            config["bridgeKeys"][0]["fee"],
            json!(3000),
            "unknown liquidity must be kept fail-closed"
        );
    }

    #[test]
    fn zero_liquidity_registry_ids_collect_only_explicit_empty_pools() {
        let liquidity_rows = json!([
            {"pool_id": "0xaaa", "has_liquidity": 1, "liquidity": "100"},
            {"pool_id": "0xbbb", "has_liquidity": 0, "liquidity": "0"},
            {"pool_id": "0xccc", "has_liquidity": null, "liquidity": "0"},
            {"pool_id": "0xddd", "has_liquidity": "0", "liquidity": "0"},
        ]);
        let rows = |value: &Value| value.as_array().cloned().unwrap_or_default();
        let registry = V4PoolRegistry::from_rows(&[], &rows(&liquidity_rows), &[]);
        let ids = registry_zero_liquidity_pool_ids(&registry);
        assert_eq!(ids, vec!["0xbbb"]);
    }

    #[test]
    fn selected_route_overrides_carry_token_min_profit_and_selected_amounts() {
        let route_payload = json!({
            "status": "profitable_amount_found",
            "selectedAmountRaw": "0x10",
            "selectedProfitRaw": "0x20",
        });
        let route_config = json!({
            "token": "0x1111111111111111111111111111111111111111",
            "minProfitRaw": "0x01",
        });
        let mut payload = json!({"status": "profitable_amount_found"});
        apply_selected_route_overrides(&mut payload, &route_payload, &route_config);
        assert_eq!(
            payload["token"],
            json!("0x1111111111111111111111111111111111111111")
        );
        assert_eq!(payload["minProfitRaw"], json!("0x01"));
        assert_eq!(payload["selectedMinProfitRaw"], json!("0x01"));
        assert_eq!(payload["selectedAmountRaw"], json!("0x10"));
        assert_eq!(payload["selectedProfitRaw"], json!("0x20"));
        assert_eq!(
            payload["selectedRoutePayload"]["status"],
            json!("profitable_amount_found")
        );
    }

    #[test]
    fn per_direction_results_keep_both_directions_and_selected_broadcast() {
        let payload = json!({
            "status": "profitable_amount_found",
            "selectedAmountRaw": "0x10",
            "selectedProfitRaw": "0x02",
            "minProfitRaw": "0x01",
            "broadcast": {"ok": true, "txHash": "0xabc"},
            "broadcastError": "broadcast_config_missing",
            "broadcastExecuted": true,
            "routes": [
                {
                    "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
                    "routeAddresses": ["0x1", "0x2", "0x3", "0x1"],
                    "status": "profitable_amount_found",
                    "directionSelected": true
                },
                {
                    "routeSymbols": ["AAA", "CCC", "BBB", "AAA"],
                    "routeAddresses": ["0x1", "0x3", "0x2", "0x1"],
                    "status": "not_profitable_after_costs"
                }
            ]
        });
        let snapshots = per_direction_results(&payload);
        assert_eq!(snapshots.len(), 2);
        assert_eq!(snapshots[0]["direction"], json!("aaa|bbb|ccc|aaa"));
        assert_eq!(snapshots[1]["direction"], json!("aaa|ccc|bbb|aaa"));
        assert_eq!(snapshots[0]["selected"], json!(true));
        assert_eq!(snapshots[1]["selected"], json!(false));
        assert_eq!(snapshots[0]["broadcast"]["txHash"], json!("0xabc"));
        assert_eq!(
            snapshots[0]["broadcastError"],
            json!("broadcast_config_missing")
        );
        assert_eq!(snapshots[0]["broadcastExecuted"], json!(true));
        assert_eq!(snapshots[0]["minProfitRaw"], json!("0x01"));
        assert!(snapshots[1].get("broadcast").is_none());
        assert!(snapshots[1].get("broadcastError").is_none());
    }

    #[test]
    fn mark_selected_route_direction_writes_into_serialized_route_copy() {
        let mut payload = json!({
            "status": "profitable_amount_found",
            "routes": [
                {"status": "profitable_amount_found"},
                {"status": "not_profitable_after_costs"}
            ],
            "routeErrors": [],
        });
        mark_selected_route_direction(&mut payload, 0);
        assert_eq!(payload["routes"][0]["directionSelected"], json!(true));
        assert!(payload["routes"][1].get("directionSelected").is_none());
    }

    #[test]
    fn per_direction_results_keep_concrete_unprofitable_route_payload() {
        let payload = json!({
            "status": "not_profitable_after_costs",
            "routes": [
                {
                    "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
                    "status": "not_profitable_after_costs",
                    "reportedNetProfitRaw": "-0x10",
                    "poolPreviewDetailed": {"netProductDeltaRaw": "-0x20"}
                },
                {
                    "routeSymbols": ["AAA", "CCC", "BBB", "AAA"],
                    "status": "not_profitable_after_costs",
                    "reportedNetProfitRaw": "-0x08",
                    "poolPreviewDetailed": {"netProductDeltaRaw": "-0x10"}
                }
            ]
        });
        let snapshots = per_direction_results(&payload);
        assert_eq!(snapshots.len(), 2);
        assert_eq!(
            snapshots[0]["route"]["reportedNetProfitRaw"],
            json!("-0x10")
        );
        assert_eq!(
            snapshots[1]["route"]["reportedNetProfitRaw"],
            json!("-0x08")
        );
    }

    #[test]
    fn helper_counts_are_parsed_from_native_result() {
        let payload = json!({
            "attempts": [1, 2, 3],
            "autoExecuted": 2,
        });
        let bool_payload = json!({
            "broadcastExecuted": true,
        });
        assert_eq!(probe_count_from_result(&payload), 3);
        assert_eq!(broadcast_count_from_result(&payload), 2);
        assert_eq!(
            broadcast_count_from_result(&json!({"auto_execute": {"autoExecuted": 1}})),
            1
        );
        assert_eq!(broadcast_count_from_result(&bool_payload), 1);
    }

    #[test]
    fn route_error_kind_classifies_gas_failures() {
        assert_eq!(
            route_error_kind("execution reverted: out of gas"),
            "gas_estimation_failed"
        );
        assert_eq!(
            route_error_kind("gas required exceeds allowance"),
            "gas_estimation_failed"
        );
        assert_eq!(
            route_error_kind("RPC error: timed out"),
            "rpc_verification_timeout"
        );
        assert_eq!(
            route_error_kind("NoProfitableRoute"),
            "rpc_verification_failed"
        );
        assert_eq!(
            route_error_kind("official_v4:previewBestPool:eth_call: the network is busy, please try again in a moment"),
            "rpc_busy"
        );
        assert_eq!(
            route_error_kind("eth_call RPC request failed: error sending request"),
            "rpc_verification_failed"
        );
    }

    #[test]
    fn backoff_seconds_doubles_up_to_cap() {
        assert_eq!(backoff_seconds(1), 5);
        assert_eq!(backoff_seconds(2), 10);
        assert_eq!(backoff_seconds(3), 20);
        assert_eq!(backoff_seconds(6), 160);
        assert_eq!(backoff_seconds(7), 320);
        assert_eq!(backoff_seconds(20), 320);
    }

    #[test]
    fn inner_amount_search_no_profit_requests_external_ladder_retry() {
        let payload = json!({
            "status": "not_profitable_after_costs",
            "poolPreview": {"ok": true},
            "attempts": [{"amountRaw": "0xde0b6b3a7640000", "error": "NoProfitableRoute"}],
        });
        assert!(should_retry_inner_single_call_no_profit(&payload));
    }

    #[test]
    fn inner_pool_preview_no_profit_does_not_retry_external_ladder() {
        let payload = json!({
            "status": "not_profitable_after_costs",
            "poolPreview": {"ok": false, "error": "NoProfitableRoute"},
            "attempts": [{"amountRaw": "0xde0b6b3a7640000", "error": "NoProfitableRoute"}],
        });
        assert!(!should_retry_inner_single_call_no_profit(&payload));
    }

    #[test]
    fn inner_profitable_or_other_failure_does_not_trigger_no_profit_retry() {
        let profitable = json!({"status": "profitable_amount_found"});
        let rpc_failed = json!({"status": "route_error", "error": "rpc failed"});
        let pool_only = json!({
            "status": "not_profitable_after_costs",
            "poolPreview": {"ok": true, "netProduct": "0x1"},
            "attempts": [],
        });
        assert!(!should_retry_inner_single_call_no_profit(&profitable));
        assert!(!should_retry_inner_single_call_no_profit(&rpc_failed));
        assert!(!should_retry_inner_single_call_no_profit(&pool_only));
    }

    #[test]
    fn status_reports_resident_workers_consistent_with_group_count() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, true);
        let config = r#"{"rpcUrl":"http://127.0.0.1:9","arbAddress":"0x0000000000000000000000000000000000000000"}"#;
        for group in ["worker-a", "worker-b"] {
            pool.submit_inner(
                group,
                config,
                Some(46630),
                vec!["uniswap_v4".to_string()],
                "deep",
                0,
                None,
                Some("robinhood-testnet".to_string()),
                Some("uniswap_v4".to_string()),
            )
            .expect("submit accepts task");
        }

        let status = loop {
            let status = pool.status_value();
            if status["workers"]
                .as_array()
                .map(|rows| rows.len())
                .unwrap_or(0)
                >= 2
            {
                break status;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        };
        let workers = status["workers"]
            .as_array()
            .map(|rows| rows.len())
            .unwrap_or(0);
        let resident = status["residentWorkers"].as_u64().unwrap_or(0) as usize;
        let max_workers = status["maxWorkers"].as_u64().unwrap_or(0) as usize;
        assert_eq!(workers, 2);
        assert_eq!(resident, workers);
        assert_eq!(max_workers, workers);
        assert!(status["runningGroups"].as_u64().unwrap_or(0) as usize <= resident);
        pool.stop();
    }

    #[test]
    fn compact_status_omits_heavy_worker_payload_fields() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, true);
        pool.submit_inner(
            "compact-status-group",
            r#"{"rpcUrl":"http://127.0.0.1:9","arbAddress":"0x0000000000000000000000000000000000000000"}"#,
            Some(46630),
            vec!["uniswap_v4".to_string()],
            "deep",
            0,
            None,
            Some("robinhood-testnet".to_string()),
            Some("uniswap_v4".to_string()),
        )
        .expect("submit accepts task");

        let compact = pool.compact_status_value();
        let worker = &compact["workers"][0];
        assert_eq!(worker["group_key"], json!("compact-status-group"));
        assert!(worker.get("last_result").is_none());
        assert!(worker.get("broadcast_history").is_none());
        assert!(worker.get("broadcast_failure_history").is_none());
        assert!(worker.get("direction_results").is_none());
        assert!(worker.get("updated_at").is_some());

        {
            let mut maintenance = pool.inner.maintenance_state.lock().unwrap();
            maintenance.last_result = Some(json!({
                "database": "memory.db",
                "mode": "zero_liquidity_pool_cleanup",
                "phase": "completed",
                "ok": true,
                "cleanedPoolCount": 42,
                "cleaned": {
                    "v4_exchange_pools": 9,
                },
                "poolIds": ["pool-1", "pool-2"],
            }));
        }
        let compact_with_maintenance = pool.compact_status_value();
        assert!(compact_with_maintenance["maintenance"]
            .get("lastResult")
            .is_none());
        assert_eq!(
            compact_with_maintenance["maintenance"]["resultSummary"]["cleanedPoolCount"],
            json!(42)
        );
        assert!(compact_with_maintenance["contextCache"]
            .get("scopeCount")
            .is_some());
        let summary = pool.summary_status_value();
        assert_eq!(summary["residentWorkers"], json!(1));
        assert_eq!(summary["workers"], json!([]));
        assert!(summary["maintenance"].get("lastResult").is_none());

        let full = pool.status_value();
        assert!(full["workers"][0].get("last_result").is_some());
        assert!(full["workers"][0].get("broadcast_history").is_some());
        assert!(full["workers"][0]
            .get("broadcast_failure_history")
            .is_some());
        assert!(full["workers"][0].get("direction_results").is_some());
        assert!(full["maintenance"].get("lastResult").is_some());
        assert_eq!(
            full["maintenance"]["lastResult"]["cleanedPoolCount"],
            json!(42)
        );
        pool.stop();
    }

    #[test]
    fn context_cache_refresh_keeps_stable_scope_object() {
        let cache = Arc::new(ContextCache {
            data: RwLock::new(ContextCacheData::default()),
        });
        let cache_identity = Arc::as_ptr(&cache);
        let first_pool = json!({
            "pool_id": "0x01",
            "token0": "0xaaa",
            "token1": "0xbbb",
        });
        cache.data.write().unwrap().replace_from_rows(
            &[first_pool],
            &[json!({"pool_id": "0x01", "has_liquidity": 1})],
            &[json!({"token_address": "0xaaa"})],
            "robinhood|4663|uniswap_v4",
        );
        assert!(Arc::ptr_eq(&cache, &cache));
        assert_eq!(Arc::as_ptr(&cache), cache_identity);
        assert_eq!(
            cache
                .data
                .read()
                .unwrap()
                .registry
                .get_pool_info("0x01")
                .map(|pool| pool.token1.as_str()),
            Some("0xbbb")
        );

        let replacement_pool = json!({
            "pool_id": "0x02",
            "token0": "0xccc",
            "token1": "0xddd",
        });
        cache.data.write().unwrap().replace_from_rows(
            &[replacement_pool],
            &[json!({"pool_id": "0x02", "has_liquidity": 0})],
            &[json!({"token_address": "0xccc"})],
            "robinhood|4663|uniswap_v4",
        );

        let data = cache.data.read().unwrap();
        assert_eq!(data.pool_count, 1);
        assert_eq!(data.registry.pool_by_id.len(), 1);
        assert!(data.registry.get_pool_info("0x01").is_none());
        assert!(data.registry.get_pool_info("0x02").is_some());
    }

    #[test]
    fn compact_worker_status_exposes_prefilter_context_without_payload() {
        let mut state = WorkerState::default();
        state.group_key = "prefilter-context-group".to_string();
        state.last_result = Some(json!({
            "method": "previewBestPool",
            "workerContext": {
                "registryScope": "robinhood|4663|uniswap_v4",
                "poolCount": 111,
                "liquidityCount": 97,
                "tokenCount": 23,
                "loadedAt": "2026-11-07T00:00:00Z",
                "cacheHit": true,
                "cacheTtlSeconds": 300,
            }
        }));

        let compact = worker_state_value(&state, false);
        let context = &compact["prefilterContext"];
        assert_eq!(context["registryScope"], json!("robinhood|4663|uniswap_v4"));
        assert_eq!(context["poolCount"], json!(111));
        assert_eq!(context["cacheHit"], json!(true));
        assert!(compact.get("last_result").is_none());

        let full = worker_state_value(&state, true);
        assert_eq!(
            full["last_result"]["workerContext"]["poolCount"],
            json!(111)
        );
    }

    #[test]
    fn worker_status_includes_broadcast_failure_history_with_payloads() {
        let mut state = WorkerState::default();
        state.group_key = "failure-history-group".to_string();
        let mut failed = json!({
            "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
            "status": "profitable_amount_found",
            "selectedAmountRaw": "0x0f4240",
            "selectedProfitRaw": "0x0493e0",
            "broadcastError": "eth_sendRawTransaction RPC request failed",
        });
        failed["observedAt"] = json!("2026-10-20T00:00:00+00:00");
        state.broadcast_failure_history.push(failed);
        state.broadcast_failure_count = 1;

        let full = worker_state_value(&state, true);
        let history = full["broadcast_failure_history"].as_array().unwrap();
        assert_eq!(history.len(), 1);
        assert_eq!(
            history[0].get("broadcastError").and_then(Value::as_str),
            Some("eth_sendRawTransaction RPC request failed")
        );
        assert_eq!(
            history[0].get("status").and_then(Value::as_str),
            Some("profitable_amount_found")
        );
        let compact = worker_state_value(&state, false);
        assert!(compact.get("broadcast_failure_history").is_none());
        assert_eq!(compact["broadcast_failure_count"], json!(1));
        assert_eq!(
            compact["last_broadcast_failure_at"],
            json!("2026-10-20T00:00:00+00:00")
        );
        let recent = compact["broadcast_failure_recent"].as_array().unwrap();
        assert_eq!(recent.len(), 1);
        assert_eq!(
            recent[0].get("error").and_then(Value::as_str),
            Some("eth_sendRawTransaction RPC request failed")
        );
        assert!(recent[0]
            .get("profitable")
            .and_then(Value::as_bool)
            .is_none());
        assert!(recent[0]["profitable"].is_null());
    }

    #[test]
    fn maintenance_scope_normalizes_network_chain_protocol() {
        assert_eq!(
            maintenance_scope(Some("Robinhood-Testnet"), Some(46630), Some("Uniswap_V4"),),
            "robinhood-testnet|46630|uniswap_v4"
        );
        assert_eq!(maintenance_scope(None, None, None), "|0|uniswap_v4");
    }

    #[test]
    fn should_route_to_execution_queue_requires_gate_and_profit() {
        let command = SubmitCommand {
            group_key: "anchor|a|b".to_string(),
            chain_id: Some(46630),
            network: None,
            protocol: None,
            protocols: Vec::new(),
            mode: "deep".to_string(),
            config_json: r#"{"deferBroadcast":true,"broadcastEnabled":true}"#.to_string(),
            database_path: None,
        };
        assert!(should_route_to_execution_queue(
            &json!({"status": "profitable_amount_found"}),
            &command,
        ));
        assert!(!should_route_to_execution_queue(
            &json!({"status": "not_profitable_after_costs"}),
            &command,
        ));
        let gated_off = SubmitCommand {
            config_json: r#"{"deferBroadcast":true,"broadcastEnabled":false}"#.to_string(),
            ..command.clone()
        };
        assert!(!should_route_to_execution_queue(
            &json!({"status": "profitable_amount_found"}),
            &gated_off,
        ));
    }

    #[test]
    fn execution_queue_status_reports_counters() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, false);
        let status = pool.status_value();
        assert_eq!(status["executionQueue"]["enabled"], json!(false));
        assert_eq!(status["executionQueue"]["workers"], json!(2));
        assert_eq!(status["executionQueue"]["queued"], json!(0));
        pool.stop();
    }

    #[test]
    fn review_queue_reports_lifecycle_counters() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, false);
        for _ in 0..3 {
            pool.inner.review_runtime.mark_claimed();
        }
        for _ in 0..2 {
            pool.inner.review_runtime.mark_completed();
        }
        pool.inner.review_runtime.mark_failed();
        let status = pool.status_value();
        assert_eq!(status["reviewQueue"]["enabled"], json!(true));
        assert_eq!(status["reviewQueue"]["claimed"], json!(3));
        assert_eq!(status["reviewQueue"]["completed"], json!(2));
        assert_eq!(status["reviewQueue"]["failed"], json!(1));
        assert_eq!(status["reviewQueue"]["isolated"]["available"], json!(false));
        assert_eq!(status["reviewQueue"]["skipped"]["available"], json!(false));
        pool.stop();
    }

    #[test]
    fn worker_lifecycle_counters_are_exposed_after_spawn() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, false);
        let initial = pool.status_value();
        assert_eq!(initial["createdTotal"], json!(0));
        assert_eq!(initial["active"], json!(0));
        assert_eq!(initial["completed"], json!(0));
        assert_eq!(initial["idleExits"], json!(0));

        pool.submit_inner(
            "lifecycle-counter-group",
            "{}",
            Some(46630),
            vec!["uniswap_v4".to_string()],
            "deep",
            0,
            None,
            Some("robinhood-testnet".to_string()),
            Some("uniswap_v4".to_string()),
        )
        .expect("submit accepts task");
        let mut created = 0;
        for _ in 0..50 {
            std::thread::sleep(std::time::Duration::from_millis(10));
            created = pool
                .status_value()
                .get("createdTotal")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            if created > 0 {
                break;
            }
        }
        assert!(created >= 1, "group worker spawn must bump createdTotal");
        pool.stop();
    }

    #[test]
    fn full_scope_payload_builds_from_registered_scope_without_command() {
        let inner = Arc::new(PoolInner {
            review_supervisor: Mutex::new(None),
            states: Mutex::new(HashMap::new()),
            review_runtime: ReviewRuntime::new(16),
            broadcast_supervisor: Mutex::new(None),
            execution_workers: 2,
            execution_queue_enabled: true,
            stop_flags: Mutex::new(HashSet::new()),
            running: Mutex::new(HashSet::new()),
            group_senders: Mutex::new(HashMap::new()),
            last_commands: Mutex::new(HashMap::new()),
            contexts: Mutex::new(HashMap::new()),
            maintenance_handler: None,
            maintenance_interval_seconds: 600,
            maintenance_tx: Mutex::new(None),
            maintenance_join: Mutex::new(None),
            maintenance_state: Mutex::new(MaintenanceState::default()),
            maintenance_scopes: Mutex::new(HashSet::new()),
            binance_interval_seconds: 300,
            binance_tx: Mutex::new(None),
            binance_join: Mutex::new(None),
            binance_state: Mutex::new(BinanceCollectorState::default()),
            shutdown: AtomicBool::new(false),
            worker_seq: AtomicU64::new(0),
            worker_created_total: AtomicU64::new(0),
            worker_completed_total: AtomicU64::new(0),
            worker_idle_exits: AtomicU64::new(0),
            database_path: Some("db.sqlite3".to_string()),
        });
        inner
            .maintenance_scopes
            .lock()
            .unwrap()
            .insert("robinhood-testnet|46630|uniswap_v4".to_string());
        let payload =
            full_scope_maintenance_payload(&inner, "robinhood-testnet|46630|uniswap_v4", None);
        assert_eq!(payload.mode, "full_scope_refresh");
        assert_eq!(payload.chain_id, Some(46630));
        assert_eq!(payload.network.as_deref(), Some("robinhood-testnet"));
        assert_eq!(payload.protocol.as_deref(), Some("uniswap_v4"));
        assert!(payload.route_addresses.is_empty());
        assert_eq!(payload.database_path.as_deref(), Some("db.sqlite3"));
    }

    #[test]
    fn registered_maintenance_scopes_includes_command_scopes() {
        let inner = Arc::new(PoolInner {
            review_supervisor: Mutex::new(None),
            states: Mutex::new(HashMap::new()),
            review_runtime: ReviewRuntime::new(16),
            broadcast_supervisor: Mutex::new(None),
            execution_workers: 2,
            execution_queue_enabled: true,
            stop_flags: Mutex::new(HashSet::new()),
            running: Mutex::new(HashSet::new()),
            group_senders: Mutex::new(HashMap::new()),
            last_commands: Mutex::new(HashMap::new()),
            contexts: Mutex::new(HashMap::new()),
            maintenance_handler: None,
            maintenance_interval_seconds: 600,
            maintenance_tx: Mutex::new(None),
            maintenance_join: Mutex::new(None),
            maintenance_state: Mutex::new(MaintenanceState::default()),
            maintenance_scopes: Mutex::new(HashSet::new()),
            binance_interval_seconds: 300,
            binance_tx: Mutex::new(None),
            binance_join: Mutex::new(None),
            binance_state: Mutex::new(BinanceCollectorState::default()),
            shutdown: AtomicBool::new(false),
            worker_seq: AtomicU64::new(0),
            worker_created_total: AtomicU64::new(0),
            worker_completed_total: AtomicU64::new(0),
            worker_idle_exits: AtomicU64::new(0),
            database_path: None,
        });
        inner
            .maintenance_scopes
            .lock()
            .unwrap()
            .insert("robinhood-testnet|46630|uniswap_v4".to_string());
        let command = SubmitCommand {
            group_key: "eth|a|b".to_string(),
            chain_id: Some(46630),
            network: Some("robinhood-testnet".to_string()),
            protocol: Some("uniswap_v4".to_string()),
            protocols: vec!["uniswap_v4".to_string()],
            mode: "deep".to_string(),
            config_json: r#"{"routes":[{"routeSymbols":["ETH","A","B","ETH"],"entryKeys":[{"poolId":"0x1"}]}]}"#
                .to_string(),
            database_path: Some("db.sqlite3".to_string()),
        };
        inner
            .last_commands
            .lock()
            .unwrap()
            .insert("eth|a|b".to_string(), command.clone());
        let scopes = registered_maintenance_scopes(&inner);
        assert_eq!(scopes, vec!["robinhood-testnet|46630|uniswap_v4"]);
        let payload = full_scope_maintenance_payload(
            &inner,
            "robinhood-testnet|46630|uniswap_v4",
            Some(&command),
        );
        assert_eq!(payload.mode, "full_scope_refresh");
        assert!(!payload.pool_candidates.is_empty());
    }

    #[test]
    fn status_reports_maintenance_defaults_when_handler_disabled() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, true);
        let status = pool.status_value();
        let maintenance = &status["maintenance"];
        assert_eq!(maintenance["inProgress"], json!(false));
        assert_eq!(maintenance["queuedCount"], json!(0));
        assert_eq!(maintenance["completedCount"], json!(0));
        assert_eq!(maintenance["failedCount"], json!(0));
        assert_eq!(maintenance["lastStatus"], json!(null));
        pool.stop();
    }

    #[test]
    fn missing_rpc_config_records_native_worker_error() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, true);
        let accepted = pool
            .submit_inner(
                "unit-test-group",
                "{}",
                Some(46630),
                vec!["uniswap_v4".to_string()],
                "deep",
                0,
                None,
                None,
                None,
            )
            .expect("submit accepts task");
        assert_eq!(accepted["accepted"], json!(true));

        let mut status = pool.status_value();
        let mut attempts = 0;
        while attempts < 100 {
            std::thread::sleep(std::time::Duration::from_millis(20));
            status = pool.status_value();
            let worker = status["workers"]
                .as_array()
                .and_then(|rows| {
                    rows.iter()
                        .find(|row| row["group_key"] == "unit-test-group")
                })
                .cloned()
                .unwrap_or_default();
            if worker.get("last_error").and_then(Value::as_str).is_some() {
                break;
            }
            attempts += 1;
        }
        let worker = status["workers"]
            .as_array()
            .and_then(|rows| {
                rows.iter()
                    .find(|row| row["group_key"] == "unit-test-group")
            })
            .expect("worker state exists");
        let state = worker["state"].as_str().unwrap_or("");
        assert!(
            state == "error" || state == "idle",
            "unexpected worker state: {state}"
        );
        let error = worker["last_error"].as_str().unwrap_or("");
        assert!(
            error.contains("rpcUrl") || error.contains("arbAddress"),
            "unexpected error: {error}"
        );
        assert_eq!(worker["phase"].as_str().unwrap_or(""), "backoff");
        assert_eq!(worker["consecutive_errors"].as_u64().unwrap_or(0), 1);
        assert!(worker
            .get("backoff_until")
            .and_then(Value::as_str)
            .is_some());
        pool.stop();
    }

    #[test]
    fn stop_pending_group_marks_latest_result_as_stopped_without_broadcast() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, true);
        let accepted = pool
            .submit_inner(
                "stop-race-group",
                r#"{"rpcUrl":"http://127.0.0.1:9","arbAddress":"0x"}"#,
                Some(46630),
                vec!["uniswap_v4".to_string()],
                "deep",
                0,
                None,
                Some("robinhood-testnet".to_string()),
                Some("uniswap_v4".to_string()),
            )
            .expect("submit accepts task");
        assert_eq!(accepted["accepted"], json!(true));
        pool.send_command(ReviewSupervisorCommand::StopGroup(
            "stop-race-group".to_string(),
        ));
        pool.stop();
        let status = pool.status_value();
        let worker = status["workers"]
            .as_array()
            .and_then(|rows| {
                rows.iter()
                    .find(|row| row["group_key"] == "stop-race-group")
            })
            .expect("worker state exists");
        assert_eq!(
            worker["state"].as_str().unwrap_or(""),
            "stopped",
            "stop must win over a pending run"
        );
        if let Some(last_result) = worker.get("last_result") {
            assert!(
                last_result.get("broadcast").is_none()
                    && last_result.get("broadcastExecuted").is_none(),
                "no broadcast may slip through after stop"
            );
        }
    }

    #[test]
    fn stopped_group_keeps_thread_and_start_reuses_same_worker() {
        let mut pool = ClosedLoopWorkerPool::start(1, None, None, 600, 300, false, 2, true);
        let config = r#"{"rpcUrl":"http://127.0.0.1:9","arbAddress":"0x0000000000000000000000000000000000000000"}"#;
        pool.submit_inner(
            "resilient-group",
            config,
            Some(46630),
            vec!["uniswap_v4".to_string()],
            "deep",
            0,
            None,
            Some("robinhood-testnet".to_string()),
            Some("uniswap_v4".to_string()),
        )
        .expect("submit accepts task");

        let first_thread_id = {
            let mut attempts = 0;
            let status = loop {
                let status = pool.status_value();
                let worker = status["workers"]
                    .as_array()
                    .and_then(|rows| {
                        rows.iter()
                            .find(|row| row["group_key"] == "resilient-group")
                    })
                    .cloned()
                    .unwrap_or_default();
                if worker
                    .get("thread_id")
                    .and_then(Value::as_str)
                    .is_some_and(|value| !value.is_empty())
                    || (worker.get("last_error").and_then(Value::as_str).is_some()
                        && attempts > 100)
                {
                    break status;
                }
                std::thread::sleep(std::time::Duration::from_millis(20));
                attempts += 1;
            };
            status["workers"]
                .as_array()
                .and_then(|rows| {
                    rows.iter()
                        .find(|row| row["group_key"] == "resilient-group")
                })
                .and_then(|worker| {
                    worker
                        .get("thread_id")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .expect("worker thread starts")
        };

        pool.send_command(ReviewSupervisorCommand::StopGroup(
            "resilient-group".to_string(),
        ));
        let mut seen_stopped = false;
        for _ in 0..100 {
            let worker = pool.status_value()["workers"]
                .as_array()
                .and_then(|rows| {
                    rows.iter()
                        .find(|row| row["group_key"] == "resilient-group")
                })
                .cloned()
                .unwrap_or_default();
            if worker.get("state").and_then(Value::as_str) == Some("stopped") {
                seen_stopped = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert!(seen_stopped, "stopped group keeps its worker state");

        pool.send_command(ReviewSupervisorCommand::StartGroup(
            "resilient-group".to_string(),
        ));
        let mut second_thread_id = None;
        for _ in 0..100 {
            let worker = pool.status_value()["workers"]
                .as_array()
                .and_then(|rows| {
                    rows.iter()
                        .find(|row| row["group_key"] == "resilient-group")
                })
                .cloned()
                .unwrap_or_default();
            second_thread_id = worker
                .get("thread_id")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_string);
            if second_thread_id.is_some() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert_eq!(
            second_thread_id.as_deref(),
            Some(first_thread_id.as_str()),
            "start must wake the existing worker instead of creating a new thread"
        );
        pool.stop();
    }

    #[test]
    fn maybe_broadcast_skips_when_stop_is_requested_between_review_and_broadcast() {
        let token_a = "0x".to_owned() + &"1".repeat(40);
        let token_b = "0x".to_owned() + &"2".repeat(40);
        let token_c = "0x".to_owned() + &"3".repeat(40);
        let zero_hooks = "0x".to_owned() + &"0".repeat(40);
        let config = json!({
            "broadcastEnabled": true,
            "broadcastPrivateKey": "0x".to_owned() + &"1".repeat(64),
            "chainId": 46630,
            "minProfitRaw": "0x01",
            "token": token_a.clone(),
            "entryKeys": [json!({"currency0": token_a.clone(), "currency1": token_b.clone(), "fee": 3000, "tickSpacing": 60, "hooks": zero_hooks.clone()})],
            "entryDirections": [true],
            "bridgeKeys": [json!({"currency0": token_b.clone(), "currency1": token_c.clone(), "fee": 3000, "tickSpacing": 60, "hooks": zero_hooks.clone()})],
            "bridgeDirections": [true],
            "exitKeys": [json!({"currency0": token_a, "currency1": token_c, "fee": 3000, "tickSpacing": 60, "hooks": zero_hooks})],
            "exitDirections": [true],
        });
        let mut payload = json!({
            "status": "profitable_amount_found",
            "selectedAmountRaw": "0x0f4240",
            "selectedProfitRaw": "0x0493e0",
        });

        maybe_broadcast(
            &mut payload,
            &config,
            "http://127.0.0.1:9",
            &("0x".to_owned() + &"a".repeat(40)),
            5,
            &|| true,
        )
        .expect("stop request must skip and not error");

        assert_eq!(
            payload.get("broadcastSkipped").and_then(Value::as_str),
            Some("group_stopped")
        );
        assert_eq!(payload.get("stopRequested"), Some(&json!(true)));
        assert!(
            payload.get("broadcast").is_none(),
            "stop must prevent broadcast"
        );
        assert_eq!(payload.get("broadcastExecuted"), None);
    }

    #[test]
    fn broadcast_failure_keeps_profitable_review_payload() {
        let mut payload = json!({
            "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
            "status": "profitable_amount_found",
            "selectedAmountRaw": "0x0f4240",
            "selectedProfitRaw": "0x0493e0",
            "routes": [],
        });

        record_broadcast_failure(
            &mut payload,
            "eth_sendRawTransaction RPC request failed".to_string(),
        );

        assert_eq!(
            payload.get("status").and_then(Value::as_str),
            Some("profitable_amount_found")
        );
        assert_eq!(
            payload.get("selectedProfitRaw").and_then(Value::as_str),
            Some("0x0493e0")
        );
        let broadcast_error = payload.get("broadcastError").unwrap();
        assert_eq!(
            broadcast_error.get("error").and_then(Value::as_str),
            Some("eth_sendRawTransaction RPC request failed")
        );
        assert_eq!(
            broadcast_error.get("rawError").and_then(Value::as_str),
            Some("eth_sendRawTransaction RPC request failed")
        );
        assert_eq!(
            broadcast_error.get("rpcMethod").and_then(Value::as_str),
            Some("eth_sendRawTransaction")
        );
        assert_eq!(broadcast_error.get("submitted"), Some(&json!(false)));
        assert_eq!(
            broadcast_error.get("errorKey").and_then(Value::as_str),
            Some("broadcast_failed")
        );
        let broadcast = payload.get("broadcast").unwrap();
        assert_eq!(broadcast.get("ok").and_then(Value::as_bool), Some(false));
        assert_eq!(
            broadcast.get("attempted").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            broadcast.get("status").and_then(Value::as_str),
            Some("broadcast_failed")
        );
        assert_eq!(
            broadcast.get("error").and_then(Value::as_str),
            Some("eth_sendRawTransaction RPC request failed")
        );
        assert_eq!(
            payload.get("broadcastExecuted").and_then(Value::as_bool),
            Some(false)
        );
        assert!(is_broadcast_failure_payload(&payload));
    }

    #[test]
    fn rpc_transport_error_detection_is_scoped_to_transport_failures() {
        assert!(is_retryable_rpc_error(
            "eth_call RPC request failed: error sending request for url (http://rpc.local)"
        ));
        assert!(is_retryable_rpc_error(
            "eth_call RPC response was not JSON: invalid body"
        ));
        assert!(is_retryable_rpc_error(
            "official_v4:previewBestPool:eth_call: the network is busy, please try again in a moment"
        ));
        assert!(is_retryable_rpc_error(
            "eth_call rate limit exceeded: 429 too many requests"
        ));
        assert!(!is_retryable_rpc_error(
            "execution reverted: NoProfitableRoute()"
        ));
        assert!(!is_retryable_rpc_error(
            "execution reverted: ArbitrageNotProfitable(1,2)"
        ));
    }

    #[test]
    fn preview_failover_switches_only_on_retryable_rpc_errors() {
        let urls = vec!["http://primary".to_string(), "http://backup".to_string()];
        let mut calls: Vec<String> = Vec::new();
        let result = run_preview_with_rpc_failover(&urls, |url| {
            calls.push(url.to_string());
            if url.ends_with("primary") {
                Err("eth_call RPC request failed: error sending request".to_string())
            } else {
                Ok(json!({"ok": true, "from": url}))
            }
        });
        let Ok((payload, selected_url, failover_count)) = result else {
            panic!("transport error must fall over to backup RPC");
        };
        assert_eq!(calls, vec!["http://primary", "http://backup"]);
        assert_eq!(selected_url, "http://backup");
        assert_eq!(failover_count, 1);
        assert_eq!(payload["from"], json!("http://backup"));
    }

    #[test]
    fn preview_failover_switches_on_server_busy_response() {
        let urls = vec!["http://primary".to_string(), "http://backup".to_string()];
        let result = run_preview_with_rpc_failover(&urls, |url| {
            if url.ends_with("primary") {
                Err("official_v4:previewBestPool:eth_call: the network is busy, please try again in a moment".to_string())
            } else {
                Ok(json!({"ok": true, "from": url}))
            }
        });
        let Ok((payload, selected_url, failover_count)) = result else {
            panic!("server busy must fall over to backup RPC");
        };
        assert_eq!(selected_url, "http://backup");
        assert_eq!(failover_count, 1);
        assert_eq!(payload["from"], json!("http://backup"));
    }

    #[test]
    fn preview_failover_does_not_switch_on_contract_revert() {
        let urls = vec!["http://primary".to_string(), "http://backup".to_string()];
        let result = run_preview_with_rpc_failover(&urls, |url| {
            assert!(url.ends_with("primary"), "revert must not switch RPC");
            Err("execution reverted: NoProfitableRoute()".to_string())
        });
        let Err((error, selected_url, failover_count)) = result else {
            panic!("contract revert must be returned as an error");
        };
        assert_eq!(selected_url, "http://primary");
        assert_eq!(failover_count, 0);
        assert!(error.contains("NoProfitableRoute"));
    }

    #[test]
    fn preview_failover_returns_last_error_when_all_rpcs_fail() {
        let urls = vec!["http://primary".to_string(), "http://backup".to_string()];
        let result = run_preview_with_rpc_failover(&urls, |url| {
            Err(format!("eth_call RPC request failed: {url}"))
        });
        let Err((error, selected_url, failover_count)) = result else {
            panic!("all transport failures must remain an error");
        };
        assert_eq!(selected_url, "http://backup");
        assert_eq!(failover_count, 1);
        assert!(error.contains("http://backup"));
    }

    #[test]
    fn retryable_rpc_helper_switches_on_transport_error() {
        let urls = vec!["http://primary".to_string(), "http://backup".to_string()];
        let mut calls: Vec<String> = Vec::new();
        let result = run_retryable_rpc_with_failover(&urls, |url| {
            calls.push(url.to_string());
            if url.ends_with("primary") {
                Err("eth_gasPrice RPC request failed: error sending request".to_string())
            } else {
                Ok(())
            }
        });
        let Ok((selected_url, failover_count)) = result else {
            panic!("transport error must fall over to backup RPC");
        };
        assert_eq!(calls, vec!["http://primary", "http://backup"]);
        assert_eq!(selected_url, "http://backup");
        assert_eq!(failover_count, 1);
    }

    #[test]
    fn retryable_rpc_helper_does_not_switch_on_contract_revert() {
        let urls = vec!["http://primary".to_string(), "http://backup".to_string()];
        let mut calls: Vec<String> = Vec::new();
        let result = run_retryable_rpc_with_failover(&urls, |url| {
            calls.push(url.to_string());
            Err("execution reverted: ArbitrageNotProfitable(1,2)".to_string())
        });
        let Err((error, selected_url, failover_count)) = result else {
            panic!("contract revert must not switch RPC");
        };
        assert_eq!(calls, vec!["http://primary"]);
        assert_eq!(selected_url, "http://primary");
        assert_eq!(failover_count, 0);
        assert!(error.contains("ArbitrageNotProfitable"));
    }

    #[test]
    fn retryable_rpc_helper_returns_last_error_when_all_rpcs_fail() {
        let urls = vec!["http://primary".to_string(), "http://backup".to_string()];
        let mut calls: Vec<String> = Vec::new();
        let result = run_retryable_rpc_with_failover(&urls, |url| {
            calls.push(url.to_string());
            Err(format!("eth_gasPrice RPC request failed: {url}"))
        });
        let Err((error, selected_url, failover_count)) = result else {
            panic!("all transport failures must remain an error");
        };
        assert_eq!(calls, vec!["http://primary", "http://backup"]);
        assert_eq!(selected_url, "http://backup");
        assert_eq!(failover_count, 1);
        assert!(error.contains("http://backup"));
    }

    #[test]
    fn config_rpc_urls_falls_back_to_single_url() {
        assert_eq!(
            config_rpc_urls(&json!({}), "http://primary"),
            vec!["http://primary"]
        );
        assert_eq!(
            config_rpc_urls(&json!({"rpcUrls": []}), "http://primary"),
            vec!["http://primary"]
        );
        assert_eq!(
            config_rpc_urls(
                &json!({"rpcUrls": ["http://a", "  ", "http://b"]}),
                "http://primary"
            ),
            vec!["http://a", "http://b"]
        );
    }
}
