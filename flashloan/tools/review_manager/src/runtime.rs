use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};

use serde::Serialize;

pub enum ReviewSupervisorCommand<T> {
    Submit(T),
    StopGroup(String),
    StartGroup(String),
    SetWaitDeadline {
        group_key: String,
        wait_until: Option<String>,
        apply: bool,
    },
    Shutdown,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewSupervisorStatus {
    pub running: bool,
}

pub struct ReviewSupervisor<T> {
    sender: Mutex<Option<Sender<ReviewSupervisorCommand<T>>>>,
    dispatcher: Mutex<Option<JoinHandle<()>>>,
    running: Arc<AtomicUsize>,
}

impl<T> ReviewSupervisor<T>
where
    T: Send + 'static,
{
    pub fn start<F>(thread_name: impl Into<String>, handler: F) -> Self
    where
        F: Fn(ReviewSupervisorCommand<T>) + Send + 'static,
    {
        let (sender, receiver) = channel::<ReviewSupervisorCommand<T>>();
        let running = Arc::new(AtomicUsize::new(1));
        let thread_running = Arc::clone(&running);
        let dispatcher = thread::Builder::new()
            .name(thread_name.into())
            .spawn(move || {
                while let Ok(command) = receiver.recv() {
                    let shutdown = matches!(&command, ReviewSupervisorCommand::Shutdown);
                    handler(command);
                    if shutdown {
                        break;
                    }
                }
                thread_running.store(0, Ordering::SeqCst);
            })
            .expect("review manager main thread starts");
        Self {
            sender: Mutex::new(Some(sender)),
            dispatcher: Mutex::new(Some(dispatcher)),
            running,
        }
    }

    pub fn send(&self, command: ReviewSupervisorCommand<T>) -> bool {
        self.sender
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|sender| sender.send(command).is_ok())
    }

    pub fn status(&self) -> ReviewSupervisorStatus {
        ReviewSupervisorStatus {
            running: self.running.load(Ordering::SeqCst) != 0,
        }
    }

    pub fn stop(&self) {
        let _ = self.send(ReviewSupervisorCommand::Shutdown);
        self.sender.lock().unwrap().take();
        if let Some(dispatcher) = self.dispatcher.lock().unwrap().take() {
            let _ = dispatcher.join();
        }
    }
}

pub struct ReviewPermit<'a> {
    runtime: &'a ReviewRuntime,
}

impl Drop for ReviewPermit<'_> {
    fn drop(&mut self) {
        self.runtime.release();
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewRuntimeStatus {
    pub enabled: bool,
    pub workers: usize,
    pub active_reviews: usize,
    pub available_slots: usize,
    pub claimed: u64,
    pub completed: u64,
    pub failed: u64,
}

pub struct ReviewRuntime {
    max_workers: usize,
    available: Mutex<usize>,
    available_changed: Condvar,
    active_reviews: AtomicUsize,
    claimed: AtomicU64,
    completed: AtomicU64,
    failed: AtomicU64,
}

impl ReviewRuntime {
    pub fn new(max_workers: usize) -> Self {
        let max_workers = max_workers.clamp(1, 32);
        Self {
            max_workers,
            available: Mutex::new(max_workers),
            available_changed: Condvar::new(),
            active_reviews: AtomicUsize::new(0),
            claimed: AtomicU64::new(0),
            completed: AtomicU64::new(0),
            failed: AtomicU64::new(0),
        }
    }

    pub fn acquire(&self) -> ReviewPermit<'_> {
        let mut available = self.available.lock().unwrap();
        while *available == 0 {
            available = self.available_changed.wait(available).unwrap();
        }
        *available -= 1;
        self.active_reviews.fetch_add(1, Ordering::SeqCst);
        ReviewPermit { runtime: self }
    }

    fn release(&self) {
        self.active_reviews.fetch_sub(1, Ordering::SeqCst);
        let mut available = self.available.lock().unwrap();
        *available += 1;
        self.available_changed.notify_one();
    }

    pub fn mark_claimed(&self) {
        self.claimed.fetch_add(1, Ordering::SeqCst);
    }

    pub fn mark_completed(&self) {
        self.completed.fetch_add(1, Ordering::SeqCst);
    }

    pub fn mark_failed(&self) {
        self.failed.fetch_add(1, Ordering::SeqCst);
    }

    pub fn status(&self) -> ReviewRuntimeStatus {
        ReviewRuntimeStatus {
            enabled: true,
            workers: self.max_workers,
            active_reviews: self.active_reviews.load(Ordering::SeqCst),
            available_slots: *self.available.lock().unwrap(),
            claimed: self.claimed.load(Ordering::SeqCst),
            completed: self.completed.load(Ordering::SeqCst),
            failed: self.failed.load(Ordering::SeqCst),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{ReviewRuntime, ReviewSupervisor, ReviewSupervisorCommand};
    use std::sync::mpsc::channel;
    use std::time::Duration;

    #[test]
    fn runtime_bounds_review_workers_and_reports_lifecycle_counters() {
        let runtime = ReviewRuntime::new(2);
        let first = runtime.acquire();
        let _second = runtime.acquire();
        assert_eq!(runtime.status().available_slots, 0);
        assert_eq!(runtime.status().active_reviews, 2);

        runtime.mark_claimed();
        runtime.mark_completed();
        drop(first);

        let status = runtime.status();
        assert_eq!(status.available_slots, 1);
        assert_eq!(status.active_reviews, 1);
        assert_eq!(status.claimed, 1);
        assert_eq!(status.completed, 1);
        assert_eq!(status.failed, 0);
    }

    #[test]
    fn supervisor_owns_main_thread_and_command_channel() {
        let (events_tx, events_rx) = channel();
        let supervisor =
            ReviewSupervisor::start("review-manager-test", move |command| match command {
                ReviewSupervisorCommand::Submit(value) => {
                    let _ = events_tx.send(value);
                }
                ReviewSupervisorCommand::Shutdown => {}
                ReviewSupervisorCommand::StopGroup(_) | ReviewSupervisorCommand::StartGroup(_) => {}
                ReviewSupervisorCommand::SetWaitDeadline { .. } => {}
            });
        assert!(supervisor.status().running);
        assert!(supervisor.send(ReviewSupervisorCommand::Submit("task-1")));
        assert_eq!(
            events_rx.recv_timeout(Duration::from_secs(1)).unwrap(),
            "task-1"
        );
        supervisor.stop();
        assert!(!supervisor.status().running);
    }
}
