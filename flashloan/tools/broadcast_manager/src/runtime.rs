use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering as AtomicOrdering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339()
}

#[derive(Clone, Debug)]
pub struct ExecutionTask {
    /// The key the queue files the task under.
    ///
    /// The queue holds one entry per key, so the key decides what a new
    /// task replaces.  A **broadcast request** names the group it belongs
    /// to, and the newest request for a group replaces the older one: a
    /// group has exactly one current review.  A **broadcast confirmation**
    /// names the transaction it waits for, and every transaction has to be
    /// confirmed, so two confirmations of the same group never replace one
    /// another.  An empty key means the group key, which is what every
    /// request that is not a confirmation uses.
    pub task_key: String,
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

/// The key the queue files a task under.
///
/// The task names its own key so that a caller can decide what a new task
/// replaces: the group for a request, the transaction for a confirmation.
pub fn queue_key(task: &ExecutionTask) -> String {
    let key = task.task_key.trim();
    if key.is_empty() {
        task.group_key.clone()
    } else {
        key.to_string()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ExecutionPriority {
    priority: i64,
    verified_at: String,
    sequence: u64,
    task_key: String,
}

impl Ord for ExecutionPriority {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .priority
            .cmp(&self.priority)
            .then_with(|| other.verified_at.cmp(&self.verified_at))
            .then_with(|| other.sequence.cmp(&self.sequence))
            .then_with(|| self.task_key.cmp(&other.task_key))
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

impl ExecutionQueueInner {
    /// The key the task of a group is filed under.
    ///
    /// The queue is keyed by the key the task named, so a group is
    /// resolved by looking for the task that names it.  The direct hit
    /// comes first: a task whose own key is the group.
    fn key_for_group(&self, normalized_group_key: &str) -> Option<String> {
        if self.tasks.contains_key(normalized_group_key) {
            return Some(normalized_group_key.to_string());
        }
        self.tasks
            .values()
            .find(|task| task.group_key.trim().to_lowercase() == normalized_group_key)
            .map(queue_key)
    }
}

pub struct ExecutionQueue {
    inner: Mutex<ExecutionQueueInner>,
    /// The wake-up signal of the queue.
    ///
    /// A worker that finds the queue empty waits on this condition instead
    /// of polling it.  The producer (the review side, which turns a
    /// positive-profit measurement into a broadcast task) and the manual
    /// execution path (which sets the processing wait time) both signal
    /// it, so a task that arrives is picked up at once rather than at the
    /// next poll tick.
    signal: Condvar,
}

impl Default for ExecutionQueue {
    fn default() -> Self {
        Self {
            inner: Mutex::new(ExecutionQueueInner::default()),
            signal: Condvar::new(),
        }
    }
}

/// How long a worker waits when nothing is due.
///
/// This is a **safety net**, not the wake-up mechanism: the wait ends as
/// soon as the queue is signalled.  The bound only keeps a worker honest
/// if a signal is ever missed, and it is also the ceiling for the wait
/// that ends at a task's processing deadline.
const WORKER_IDLE_WAIT_MILLIS: u64 = 250;

impl ExecutionQueue {
    /// Take the lock of the queue.
    ///
    /// A poisoned lock is recovered rather than propagated: the queue
    /// holds plain data, and a panic in one worker must not take the
    /// broadcast thread down with it.
    fn lock(&self) -> MutexGuard<'_, ExecutionQueueInner> {
        self.inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

impl ExecutionQueue {
    pub fn enqueue(&self, mut task: ExecutionTask) {
        let mut inner = self.lock();
        let key = queue_key(&task);
        if let Some(existing) = inner.tasks.get_mut(&key) {
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
                // A task that is already waiting keeps its place in the
                // queue, but the fresh statement of when it is due wins:
                // a confirmation that was not answered yet comes back
                // with a later deadline, and the queue has to honour it.
                if task.available_at.is_some() {
                    existing.available_at = task.available_at.clone();
                }
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
            inner.tasks.insert(key, task);
            return;
        }
        inner.sequence += 1;
        let entry = ExecutionPriority {
            priority: task.priority,
            verified_at: task.verified_at.clone(),
            sequence: inner.sequence,
            task_key: key.clone(),
        };
        inner.tasks.insert(key, task);
        inner.heap.push(entry);
        // The producer woke the broadcast thread: a task that arrives is
        // picked up at once instead of at the next poll tick.
        self.signal.notify_all();
    }

    pub fn pop_next(&self) -> Option<ExecutionTask> {
        let mut inner = self.lock();
        self.pop_next_locked(&mut inner).0
    }

    /// Take the next task that is due, and say when the next one is due.
    ///
    /// A task whose processing wait time has not arrived yet is **kept**:
    /// it goes back into the queue and the deadline is reported to the
    /// caller, so the broadcast thread sleeps until that moment instead of
    /// losing the task.  Dropping it would make a manual execution that
    /// sets a wait time in the future disappear without a trace.
    fn pop_next_locked(
        &self,
        inner: &mut ExecutionQueueInner,
    ) -> (Option<ExecutionTask>, Option<chrono::DateTime<chrono::Utc>>) {
        let mut deferred: Vec<ExecutionPriority> = Vec::new();
        let mut next_deadline: Option<chrono::DateTime<chrono::Utc>> = None;
        while let Some(entry) = inner.heap.pop() {
            let Some(current) = inner.tasks.get(&entry.task_key) else {
                continue;
            };
            if current.state != "queued"
                || current.priority != entry.priority
                || current.verified_at != entry.verified_at
            {
                continue;
            }
            let deadline = current.available_at.as_deref().and_then(|value| {
                chrono::DateTime::parse_from_rfc3339(value)
                    .ok()
                    .map(|parsed| parsed.with_timezone(&chrono::Utc))
            });
            if let Some(deadline) = deadline {
                if chrono::Utc::now() < deadline {
                    next_deadline = Some(match next_deadline {
                        Some(seen) if seen <= deadline => seen,
                        _ => deadline,
                    });
                    deferred.push(entry);
                    continue;
                }
            }
            let mut task = current.clone();
            task.state = "running".to_string();
            task.updated_at = now_iso();
            inner.tasks.insert(queue_key(&task), task.clone());
            for item in deferred {
                inner.heap.push(item);
            }
            return (Some(task), next_deadline);
        }
        for item in deferred {
            inner.heap.push(item);
        }
        (None, next_deadline)
    }

    /// Wait for the next task that is due.
    ///
    /// The wait ends as soon as the queue is signalled: a task that
    /// arrives from the review side, or a manual execution that sets the
    /// processing wait time, wakes the worker at once.  A task that is not
    /// due yet ends the wait at its own deadline, so a deferred task is
    /// executed when its time comes instead of being lost.
    pub fn take_next(&self, shutdown: &AtomicBool) -> Option<ExecutionTask> {
        let mut guard = self.lock();
        loop {
            if shutdown.load(AtomicOrdering::SeqCst) {
                return None;
            }
            let (task, next_deadline) = self.pop_next_locked(&mut guard);
            if let Some(task) = task {
                return Some(task);
            }
            let wait = match next_deadline {
                Some(deadline) => {
                    let delta = deadline - chrono::Utc::now();
                    delta
                        .to_std()
                        .unwrap_or(Duration::from_millis(0))
                        .min(Duration::from_millis(WORKER_IDLE_WAIT_MILLIS))
                }
                None => Duration::from_millis(WORKER_IDLE_WAIT_MILLIS),
            };
            let (woken, _timeout) = self
                .signal
                .wait_timeout(guard, wait)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            guard = woken;
        }
    }

    /// Wake every worker that is waiting for a task.
    pub fn wake(&self) {
        self.signal.notify_all();
    }

    pub fn complete(
        &self,
        key: &str,
        state: &str,
        last_result: Option<Value>,
        last_error: Option<String>,
    ) {
        let mut inner = self.lock();
        if let Some(task) = inner.tasks.get_mut(key) {
            task.state = state.to_string();
            task.updated_at = now_iso();
            task.attempts = task.attempts.saturating_add(1);
            if let Some(result) = last_result {
                task.review_payload = result;
            }
            task.last_error = last_error;
        }
        // A finished task frees a broadcast worker: the others may pick up
        // the next task without waiting for the idle timeout.
        self.signal.notify_all();
    }

    pub fn summary(&self) -> (usize, usize, usize, usize, usize, usize) {
        let inner = self.lock();
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
        let mut inner = self.lock();
        // The manual execution path names a **group**, while the queue is
        // keyed by whatever the task named.  The group is resolved to the
        // key the task is filed under, so the manual path keeps working
        // whether the task is a request for the group or a confirmation
        // of a transaction in it.
        let key = inner
            .key_for_group(&normalized_key)
            .ok_or_else(|| format!("execution_task_not_found:{normalized_key}"))?;
        let task = inner
            .tasks
            .get_mut(&key)
            .ok_or_else(|| format!("execution_task_not_found:{normalized_key}"))?;
        if task.state != "waiting" {
            return Err(format!(
                "execution_task_not_waiting:{normalized_key}:{}",
                task.state
            ));
        }
        if apply {
            let (priority, verified_at, task_key) = {
                task.state = "queued".to_string();
                task.available_at = Some(resolved_wait_until.clone());
                task.updated_at = now_iso();
                (
                    task.priority,
                    task.verified_at.clone(),
                    key.clone(),
                )
            };
            inner.sequence += 1;
            let sequence = inner.sequence;
            inner.heap.push(ExecutionPriority {
                priority,
                verified_at,
                sequence,
                task_key,
            });
            // The manual execution path woke the broadcast thread: the
            // task is due now, or it is due at the wait time the caller
            // named, and either way the worker learns about it at once.
            self.signal.notify_all();
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
        let inner = self.lock();
        let mut rows: Vec<&ExecutionTask> = inner.tasks.values().collect();
        rows.sort_by(|left, right| right.updated_at.cmp(&left.updated_at));
        rows.into_iter()
            .take(limit)
            .map(|task| {
                json!({
                    "task_key": queue_key(task),
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
        // The **confirmation main thread**.  It waits for the broadcast
        // messages and starts a worker for each of them, so the number
        // of workers follows the number of messages: a transaction that
        // waits for its answer never holds the answer of another
        // transaction.
        //
        // The wait is dynamic: the thread sleeps on the queue's own
        // condition and wakes when a message arrives.
        let dispatcher_queue = Arc::clone(&queue);
        let dispatcher_active = Arc::clone(&active_executions);
        let dispatcher_shutdown = Arc::clone(&shutdown);
        let dispatcher_handler = Arc::clone(&handler);
        let dispatcher_skip = Arc::clone(&should_skip);
        let dispatcher_prefix = thread_prefix.clone();
        let dispatcher = thread::Builder::new()
            .name(format!("{dispatcher_prefix}-main"))
            .spawn(move || {
                let mut sequence = 0usize;
                while !dispatcher_shutdown.load(AtomicOrdering::SeqCst) {
                    let Some(task) = dispatcher_queue.take_next(&dispatcher_shutdown) else {
                        break;
                    };
                    let key = queue_key(&task);
                    if dispatcher_shutdown.load(AtomicOrdering::SeqCst) {
                        dispatcher_queue.complete(
                            &key,
                            "skipped",
                            None,
                            Some("pool_shutdown".to_string()),
                        );
                        break;
                    }
                    // The bound keeps a burst of messages from starting
                    // an unbounded number of workers.
                    while dispatcher_active.load(AtomicOrdering::SeqCst) >= worker_count {
                        if dispatcher_shutdown.load(AtomicOrdering::SeqCst) {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(WORKER_IDLE_WAIT_MILLIS));
                    }
                    if dispatcher_shutdown.load(AtomicOrdering::SeqCst) {
                        dispatcher_queue.complete(
                            &key,
                            "skipped",
                            None,
                            Some("pool_shutdown".to_string()),
                        );
                        break;
                    }
                    let worker_queue = Arc::clone(&dispatcher_queue);
                    let worker_active = Arc::clone(&dispatcher_active);
                    let worker_shutdown = Arc::clone(&dispatcher_shutdown);
                    let worker_handler = Arc::clone(&dispatcher_handler);
                    let worker_skip = Arc::clone(&dispatcher_skip);
                    let worker_key = key.clone();
                    sequence += 1;
                    let thread_name = format!("{dispatcher_prefix}-worker-{sequence}");
                    let started = thread::Builder::new()
                        .name(thread_name)
                        .spawn(move || {
                            let key = worker_key;
                            if worker_shutdown.load(AtomicOrdering::SeqCst) {
                                worker_queue.complete(
                                    &key,
                                    "skipped",
                                    None,
                                    Some("pool_shutdown".to_string()),
                                );
                                return;
                            }
                            if let Some(reason) = worker_skip(&task) {
                                worker_queue.complete(&key, "skipped", None, Some(reason));
                                return;
                            }
                            worker_active.fetch_add(1, AtomicOrdering::SeqCst);
                            let result = worker_handler(&task);
                            worker_active.fetch_sub(1, AtomicOrdering::SeqCst);
                            match result {
                                Ok(payload) => {
                                    worker_queue.complete(&key, "done", Some(payload), None);
                                }
                                Err(error) => {
                                    worker_queue.complete(&key, "error", None, Some(error));
                                }
                            }
                        });
                    if started.is_err() {
                        dispatcher_queue.complete(
                            &key,
                            "error",
                            None,
                            Some("the broadcast confirmation worker thread could not be started".to_string()),
                        );
                    }
                }
            })
            .expect("broadcast confirmation main thread starts");
        let mut workers = Vec::with_capacity(1);
        workers.push(dispatcher);
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

    /// Wake the broadcast thread.
    ///
    /// The review side calls this after it produced a positive-profit
    /// measurement, so the broadcast workers look at the queue at once
    /// instead of at their next idle timeout.
    pub fn wake(&self) {
        self.queue.wake();
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
    use std::sync::Mutex;
    use std::time::Duration;

    fn execution_task(group_key: &str, verified_at: &str) -> ExecutionTask {
        let observed_at = super::now_iso();
        let available_at = observed_at.clone();
        ExecutionTask {
            task_key: String::new(),
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
    fn a_task_whose_wait_time_has_not_arrived_is_kept_in_the_queue() {
        let queue = ExecutionQueue::default();
        let mut task = execution_task("anchor|deferred|b", "2026-11-06T05:00:00Z");
        task.state = "waiting".to_string();
        queue.enqueue(task);

        // A manual execution that sets a wait time in the future queues the
        // task, and the task is not due yet.
        let deadline = (chrono::Utc::now() + chrono::Duration::seconds(30)).to_rfc3339();
        queue
            .set_wait_until("anchor|deferred|b", Some(deadline.as_str()), true)
            .expect("manual wait time is accepted");

        assert!(queue.pop_next().is_none(), "the task is not due yet");
        let (queued, _, _, _, _, _) = queue.summary();
        assert_eq!(queued, 1, "the deferred task must stay in the queue");

        // The task is still reachable: it is taken as soon as its wait time
        // arrives.  Before the fix the heap entry was dropped together with
        // the task, and the task was never executed at all.
        let mut task = execution_task("anchor|due|b", "2026-11-06T05:00:00Z");
        task.state = "waiting".to_string();
        queue.enqueue(task);
        let now = chrono::Utc::now().to_rfc3339();
        queue
            .set_wait_until("anchor|due|b", Some(now.as_str()), true)
            .expect("manual wait time is accepted");
        assert!(
            queue.pop_next().is_some(),
            "a due task must be taken from the queue"
        );
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

    #[test]
    fn a_request_replaces_the_request_of_the_same_group_and_a_confirmation_does_not() {
        // A group has one current review, so a new request for the group
        // replaces the older one.
        let queue = ExecutionQueue::default();
        let mut first = execution_task("anchor|a|b", "2026-10-27T01:00:00Z");
        first.review_payload = json!({"round": 1});
        queue.enqueue(first);
        let mut second = execution_task("anchor|a|b", "2026-10-27T02:00:00Z");
        second.review_payload = json!({"round": 2});
        queue.enqueue(second);
        let taken = queue.pop_next().expect("the request is taken");
        assert_eq!(taken.review_payload, json!({"round": 2}));

        // A confirmation names the transaction it waits for, so the
        // confirmation of a transaction that is already on its way is not
        // replaced by the confirmation of a later transaction.
        let queue = ExecutionQueue::default();
        let mut first = execution_task("anchor|a|b", "2026-10-27T01:00:00Z");
        first.task_key = "confirm|robinhood|4663|0xaaa".to_string();
        queue.enqueue(first);
        let mut second = execution_task("anchor|a|b", "2026-10-27T02:00:00Z");
        second.task_key = "confirm|robinhood|4663|0xbbb".to_string();
        queue.enqueue(second);
        let (queued, _, _, _, _, _) = queue.summary();
        assert_eq!(queued, 2, "both confirmations wait for their answer");
    }

    #[test]
    fn the_confirmation_main_thread_starts_a_worker_for_every_message() {
        // The main thread waits for the messages and starts a worker for
        // each of them, so a message that waits for a long answer does not
        // hold the answer of another message.
        let handled = Arc::new(Mutex::new(Vec::<String>::new()));
        let handled_by_workers = Arc::clone(&handled);
        let supervisor = BroadcastSupervisor::start(
            2,
            "rust-broadcast-confirmation-test",
            move |task| {
                handled_by_workers
                    .lock()
                    .unwrap()
                    .push(task.task_key.clone());
                Ok(json!({"ok": true}))
            },
            |_| None,
        );
        for index in 0..3 {
            let mut task = execution_task(&format!("anchor|a|{index}"), "2026-10-27T01:00:00Z");
            task.task_key = format!("confirm|robinhood|4663|0x{index:03x}");
            supervisor.enqueue(task);
        }
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        loop {
            if handled.lock().unwrap().len() == 3 {
                break;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "every message reached a confirmation worker"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        supervisor.stop();
    }

    #[test]
    fn an_enqueued_task_reaches_a_broadcast_worker() {
        let calls = Arc::new(AtomicUsize::new(0));
        let worker_calls = Arc::clone(&calls);
        let supervisor = BroadcastSupervisor::start(
            1,
            "rust-broadcast-manager-wake-test",
            move |_task| {
                worker_calls.fetch_add(1, Ordering::SeqCst);
                Ok(json!({"ok": true}))
            },
            |_| None,
        );
        supervisor.enqueue(execution_task("anchor|wake|b", "2026-11-06T01:00:00Z"));
        let mut status = supervisor.status(5);
        for _ in 0..100 {
            if status.done == 1 {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
            status = supervisor.status(5);
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert_eq!(status.done, 1);
        supervisor.stop();
    }

    #[test]
    fn a_manual_wait_time_runs_the_task_when_it_arrives() {
        let calls = Arc::new(AtomicUsize::new(0));
        let worker_calls = Arc::clone(&calls);
        let supervisor = BroadcastSupervisor::start(
            1,
            "rust-broadcast-manager-manual-test",
            move |_task| {
                worker_calls.fetch_add(1, Ordering::SeqCst);
                Ok(json!({"ok": true}))
            },
            |_| None,
        );
        let mut task = execution_task("anchor|manual|b", "2026-11-06T01:00:00Z");
        task.state = "waiting".to_string();
        supervisor.enqueue(task);

        // A waiting task is not executed: it waits for the manual wake.
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(calls.load(Ordering::SeqCst), 0);

        let deadline = (chrono::Utc::now() + chrono::Duration::seconds(1)).to_rfc3339();
        supervisor
            .set_wait_until("anchor|manual|b", Some(deadline.as_str()), true)
            .expect("manual wait time is accepted");
        for _ in 0..120 {
            if calls.load(Ordering::SeqCst) > 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        assert_eq!(
            calls.load(Ordering::SeqCst),
            1,
            "the manual wait time must run the task when it arrives"
        );
        supervisor.stop();
    }
}
