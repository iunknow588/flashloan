use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339()
}

#[derive(Clone, Debug)]
pub struct ExecutionTask {
    pub group_key: String,
    pub chain_id: Option<i64>,
    pub network: Option<String>,
    pub protocol: Option<String>,
    pub protocols: Vec<String>,
    pub config_json: String,
    pub review_payload: Value,
    pub verified_at: String,
    pub priority: i64,
    pub state: String,
    pub attempts: u64,
    pub last_error: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub available_at: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ExecutionPriority {
    priority: i64,
    verified_at: String,
    sequence: u64,
    group_key: String,
}

impl Ord for ExecutionPriority {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .priority
            .cmp(&self.priority)
            .then_with(|| other.verified_at.cmp(&self.verified_at))
            .then_with(|| other.sequence.cmp(&self.sequence))
            .then_with(|| self.group_key.cmp(&other.group_key))
    }
}

impl PartialOrd for ExecutionPriority {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Default)]
struct ExecutionQueueInner {
    tasks: HashMap<String, ExecutionTask>,
    heap: BinaryHeap<ExecutionPriority>,
    sequence: u64,
}

#[derive(Default)]
pub struct ExecutionQueue {
    inner: Mutex<ExecutionQueueInner>,
}

impl ExecutionQueue {
    pub fn enqueue(&self, mut task: ExecutionTask) {
        let mut inner = self.inner.lock().unwrap();
        if let Some(existing) = inner.tasks.get_mut(&task.group_key) {
            if existing.state == "queued" || existing.state == "running" {
                let verified_at = if task.verified_at < existing.verified_at {
                    task.verified_at.clone()
                } else {
                    existing.verified_at.clone()
                };
                let priority = task.priority.min(existing.priority);
                existing.config_json = task.config_json;
                existing.review_payload = task.review_payload;
                existing.verified_at = verified_at;
                existing.priority = priority;
                existing.updated_at = now_iso();
                task = existing.clone();
            } else {
                existing.config_json = task.config_json;
                existing.review_payload = task.review_payload;
                existing.verified_at = task.verified_at.clone();
                existing.priority = task.priority;
                existing.available_at = task.available_at.clone();
                existing.state = task.state.clone();
                existing.updated_at = now_iso();
                task = existing.clone();
            }
        }
        if task.state != "queued" {
            inner.tasks.insert(task.group_key.clone(), task);
            return;
        }
        inner.sequence += 1;
        let entry = ExecutionPriority {
            priority: task.priority,
            verified_at: task.verified_at.clone(),
            sequence: inner.sequence,
            group_key: task.group_key.clone(),
        };
        inner.tasks.insert(task.group_key.clone(), task);
        inner.heap.push(entry);
    }

    pub fn pop_next(&self) -> Option<ExecutionTask> {
        let mut inner = self.inner.lock().unwrap();
        while let Some(entry) = inner.heap.pop() {
            let current = inner.tasks.get(&entry.group_key)?;
            if current.state != "queued"
                || current.available_at.as_deref().is_some_and(|available_at| {
                    chrono::DateTime::parse_from_rfc3339(available_at)
                        .map(|deadline| chrono::Utc::now() < deadline.with_timezone(&chrono::Utc))
                        .unwrap_or(false)
                })
                || current.priority != entry.priority
                || current.verified_at != entry.verified_at
            {
                continue;
            }
            let mut task = current.clone();
            task.state = "running".to_string();
            task.updated_at = now_iso();
            inner.tasks.insert(task.group_key.clone(), task.clone());
            return Some(task);
        }
        None
    }

    pub fn complete(
        &self,
        group_key: &str,
        state: &str,
        last_result: Option<Value>,
        last_error: Option<String>,
    ) {
        let mut inner = self.inner.lock().unwrap();
        if let Some(task) = inner.tasks.get_mut(group_key) {
            task.state = state.to_string();
            task.updated_at = now_iso();
            task.attempts = task.attempts.saturating_add(1);
            if let Some(result) = last_result {
                task.review_payload = result;
            }
            task.last_error = last_error;
        }
    }

    pub fn summary(&self) -> (usize, usize, usize, usize, usize, usize) {
        let inner = self.inner.lock().unwrap();
        let mut queued = 0;
        let mut running = 0;
        let mut done = 0;
        let mut error = 0;
        let mut skipped = 0;
        let mut waiting = 0;
        for task in inner.tasks.values() {
            match task.state.as_str() {
                "queued" => queued += 1,
                "running" => running += 1,
                "done" => done += 1,
                "error" => error += 1,
                "skipped" => skipped += 1,
                "waiting" => waiting += 1,
                _ => {}
            }
        }
        (queued, running, done, error, skipped, waiting)
    }

    pub fn set_wait_until(
        &self,
        group_key: &str,
        wait_until: Option<&str>,
        apply: bool,
    ) -> Result<Value, String> {
        let normalized_key = group_key.trim().to_lowercase();
        if normalized_key.is_empty() {
            return Err("group_key is required".to_string());
        }
        let resolved_wait_until = match wait_until {
            Some(value) if !value.trim().is_empty() => {
                chrono::DateTime::parse_from_rfc3339(value.trim())
                    .map_err(|error| format!("wait_until must be RFC3339: {error}"))?
                    .with_timezone(&chrono::Utc)
                    .to_rfc3339()
            }
            _ => now_iso(),
        };
        let mut inner = self.inner.lock().unwrap();
        let task = inner
            .tasks
            .get_mut(&normalized_key)
            .ok_or_else(|| format!("execution_task_not_found:{normalized_key}"))?;
        if task.state != "waiting" {
            return Err(format!(
                "execution_task_not_waiting:{normalized_key}:{}",
                task.state
            ));
        }
        if apply {
            let (priority, verified_at, group_key) = {
                task.state = "queued".to_string();
                task.available_at = Some(resolved_wait_until.clone());
                task.updated_at = now_iso();
                (
                    task.priority,
                    task.verified_at.clone(),
                    task.group_key.clone(),
                )
            };
            inner.sequence += 1;
            let sequence = inner.sequence;
            inner.heap.push(ExecutionPriority {
                priority,
                verified_at,
                sequence,
                group_key,
            });
        }
        Ok(json!({
            "accepted": true,
            "group_key": normalized_key,
            "action": "set_wait_until",
            "waitUntil": resolved_wait_until,
            "previousState": "waiting",
            "state": if apply { "queued" } else { "waiting" },
            "dryRun": !apply,
        }))
    }

    pub fn recent(&self, limit: usize) -> Vec<Value> {
        let inner = self.inner.lock().unwrap();
        let mut rows: Vec<&ExecutionTask> = inner.tasks.values().collect();
        rows.sort_by(|left, right| right.updated_at.cmp(&left.updated_at));
        rows.into_iter()
            .take(limit)
            .map(|task| {
                json!({
                    "group_key": task.group_key,
                    "chain_id": task.chain_id,
                    "network": task.network,
                    "protocol": task.protocol,
                    "protocols": task.protocols,
                    "state": task.state,
                    "priority": task.priority,
                    "verified_at": task.verified_at,
                    "attempts": task.attempts,
                    "last_error": task.last_error,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                    "available_at": task.available_at,
                })
            })
            .collect()
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastRuntimeStatus {
    pub enabled: bool,
    pub workers: usize,
    pub active_executions: usize,
    pub queued: usize,
    pub running: usize,
    pub done: usize,
    pub error: usize,
    pub skipped: usize,
    pub waiting: usize,
    pub recent: Vec<Value>,
}

type BroadcastTaskHandler =
    Arc<dyn Fn(&ExecutionTask) -> Result<Value, String> + Send + Sync + 'static>;
type BroadcastSkipHandler = Arc<dyn Fn(&ExecutionTask) -> Option<String> + Send + Sync + 'static>;

pub struct BroadcastSupervisor {
    queue: Arc<ExecutionQueue>,
    worker_count: usize,
    active_executions: Arc<AtomicUsize>,
    shutdown: Arc<AtomicBool>,
    workers: Mutex<Vec<JoinHandle<()>>>,
}

impl BroadcastSupervisor {
    pub fn start<H, S>(
        worker_count: usize,
        thread_prefix: impl Into<String>,
        handler: H,
        should_skip: S,
    ) -> Self
    where
        H: Fn(&ExecutionTask) -> Result<Value, String> + Send + Sync + 'static,
        S: Fn(&ExecutionTask) -> Option<String> + Send + Sync + 'static,
    {
        let worker_count = worker_count.clamp(1, 8);
        let queue = Arc::new(ExecutionQueue::default());
        let active_executions = Arc::new(AtomicUsize::new(0));
        let shutdown = Arc::new(AtomicBool::new(false));
        let handler: BroadcastTaskHandler = Arc::new(handler);
        let should_skip: BroadcastSkipHandler = Arc::new(should_skip);
        let thread_prefix = thread_prefix.into();
        let mut workers = Vec::with_capacity(worker_count);
        for index in 0..worker_count {
            let worker_queue = Arc::clone(&queue);
            let worker_active = Arc::clone(&active_executions);
            let worker_shutdown = Arc::clone(&shutdown);
            let worker_handler = Arc::clone(&handler);
            let worker_skip = Arc::clone(&should_skip);
            let thread_name = format!("{thread_prefix}-{index}");
            let worker = thread::Builder::new()
                .name(thread_name)
                .spawn(move || {
                    while !worker_shutdown.load(AtomicOrdering::SeqCst) {
                        let Some(task) = worker_queue.pop_next() else {
                            thread::sleep(Duration::from_millis(100));
                            continue;
                        };
                        if worker_shutdown.load(AtomicOrdering::SeqCst) {
                            worker_queue.complete(
                                &task.group_key,
                                "skipped",
                                None,
                                Some("pool_shutdown".to_string()),
                            );
                            break;
                        }
                        if let Some(reason) = worker_skip(&task) {
                            worker_queue.complete(&task.group_key, "skipped", None, Some(reason));
                            continue;
                        }
                        worker_active.fetch_add(1, AtomicOrdering::SeqCst);
                        let result = worker_handler(&task);
                        worker_active.fetch_sub(1, AtomicOrdering::SeqCst);
                        match result {
                            Ok(payload) => {
                                worker_queue.complete(&task.group_key, "done", Some(payload), None);
                            }
                            Err(error) => {
                                worker_queue.complete(&task.group_key, "error", None, Some(error));
                            }
                        }
                    }
                })
                .expect("broadcast manager worker thread starts");
            workers.push(worker);
        }
        Self {
            queue,
            worker_count,
            active_executions,
            shutdown,
            workers: Mutex::new(workers),
        }
    }

    pub fn enqueue(&self, task: ExecutionTask) {
        self.queue.enqueue(task);
    }

    pub fn status(&self, recent_limit: usize) -> BroadcastRuntimeStatus {
        let (queued, running, done, error, skipped, waiting) = self.queue.summary();
        BroadcastRuntimeStatus {
            enabled: true,
            workers: self.worker_count,
            active_executions: self.active_executions.load(AtomicOrdering::SeqCst),
            queued,
            running,
            done,
            error,
            skipped,
            waiting,
            recent: self.queue.recent(recent_limit),
        }
    }

    pub fn stop(&self) {
        self.request_shutdown();
        if let Ok(mut workers) = self.workers.lock() {
            for worker in workers.drain(..) {
                let _ = worker.join();
            }
        }
    }

    pub fn request_shutdown(&self) {
        self.shutdown.store(true, AtomicOrdering::SeqCst);
    }

    pub fn queue(&self) -> Arc<ExecutionQueue> {
        Arc::clone(&self.queue)
    }

    pub fn set_wait_until(
        &self,
        group_key: &str,
        wait_until: Option<&str>,
        apply: bool,
    ) -> Result<Value, String> {
        self.queue.set_wait_until(group_key, wait_until, apply)
    }
}

#[cfg(test)]
mod tests {
    use super::{BroadcastSupervisor, ExecutionQueue, ExecutionTask};
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    fn execution_task(group_key: &str, verified_at: &str) -> ExecutionTask {
        let observed_at = super::now_iso();
        let available_at = observed_at.clone();
        ExecutionTask {
            group_key: group_key.to_string(),
            chain_id: Some(46630),
            network: Some("robinhood-testnet".to_string()),
            protocol: Some("uniswap_v4".to_string()),
            protocols: vec!["uniswap_v4".to_string()],
            config_json: "{}".to_string(),
            review_payload: json!({"status": "profitable_amount_found"}),
            verified_at: verified_at.to_string(),
            priority: 1,
            state: "queued".to_string(),
            attempts: 0,
            last_error: None,
            created_at: observed_at.clone(),
            updated_at: observed_at,
            available_at: Some(available_at),
        }
    }

    #[test]
    fn execution_queue_orders_by_verified_at() {
        let queue = ExecutionQueue::default();
        queue.enqueue(execution_task("anchor|b|c", "2026-10-27T02:00:00Z"));
        queue.enqueue(execution_task("anchor|a|b", "2026-10-27T01:00:00Z"));
        let first = queue.pop_next().expect("earlier task pops first");
        let second = queue.pop_next().expect("later task pops second");
        assert_eq!(first.group_key, "anchor|a|b");
        assert_eq!(second.group_key, "anchor|b|c");
        assert!(queue.pop_next().is_none());
    }

    #[test]
    fn execution_queue_keeps_earliest_verified_at_for_queued_group() {
        let queue = ExecutionQueue::default();
        queue.enqueue(execution_task("anchor|a|b", "2026-10-27T02:00:00Z"));
        queue.enqueue(execution_task("anchor|a|b", "2026-10-27T01:00:00Z"));
        let task = queue.pop_next().expect("queued task pops");
        assert_eq!(task.group_key, "anchor|a|b");
        assert_eq!(task.verified_at, "2026-10-27T01:00:00Z");
        assert!(queue.pop_next().is_none(), "same group must not duplicate");
    }

    #[test]
    fn execution_queue_reenqueues_after_done_with_new_verified_at() {
        let queue = ExecutionQueue::default();
        queue.enqueue(execution_task("anchor|a|b", "2026-10-27T01:00:00Z"));
        let task = queue.pop_next().expect("task pops");
        assert_eq!(task.state, "running");
        queue.complete(&task.group_key, "done", None, None);
        queue.enqueue(execution_task("anchor|a|b", "2026-10-27T04:00:00Z"));
        let reenqueued = queue.pop_next().expect("done group is reenqueued");
        assert_eq!(reenqueued.verified_at, "2026-10-27T04:00:00Z");
    }

    #[test]
    fn waiting_execution_task_wakes_only_once_to_queued() {
        let queue = ExecutionQueue::default();
        let mut task = execution_task("anchor|wait|b", "2026-10-27T05:00:00Z");
        task.state = "waiting".to_string();
        task.available_at = Some("2026-11-06T00:00:00Z".to_string());
        queue.enqueue(task);

        assert!(queue.pop_next().is_none(), "waiting task must not pop");
        let dry = queue
            .set_wait_until("anchor|wait|b", Some("2026-11-06T01:00:00Z"), false)
            .expect("dry run validates waiting task");
        assert_eq!(dry["state"], json!("waiting"));
        assert!(queue.pop_next().is_none(), "dry run must not wake task");

        queue
            .set_wait_until("anchor|wait|b", Some("2026-11-06T01:00:00Z"), true)
            .expect("manual broadcast wake");
        let woken = queue.pop_next().expect("woken task pops");
        assert_eq!(woken.group_key, "anchor|wait|b");
        assert_eq!(woken.state, "running");
        assert!(queue.pop_next().is_none(), "wake must not duplicate task");

        assert!(matches!(
            queue.set_wait_until("anchor|wait|b", None, true),
            Err(error) if error.starts_with("execution_task_not_waiting")
        ));
    }

    #[test]
    fn supervisor_owns_bounded_workers_and_terminal_queue_state() {
        let calls = Arc::new(AtomicUsize::new(0));
        let worker_calls = Arc::clone(&calls);
        let supervisor = BroadcastSupervisor::start(
            1,
            "rust-broadcast-manager-test",
            move |task| {
                worker_calls.fetch_add(1, Ordering::SeqCst);
                Ok(json!({"groupKey": task.group_key, "ok": true}))
            },
            |_| None,
        );
        supervisor.enqueue(execution_task("anchor|a|b", "2026-10-27T01:00:00Z"));
        let mut status = supervisor.status(5);
        for _ in 0..100 {
            if status.done == 1 {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
            status = supervisor.status(5);
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert_eq!(status.running, 0);
        assert_eq!(status.done, 1);
        assert_eq!(status.active_executions, 0);
        supervisor.stop();
    }
}
