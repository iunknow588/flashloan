"""Supervisors for bounded closed-loop review and broadcast observation."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import os
import threading
import time
from typing import Any, Callable

from dex.runtime.dex_closed_loop_review_queue import (
    ClosedLoopReviewQueue,
    ClosedLoopReviewTask,
)


def _bounded_workers(value: int | str | None, default: int = 16) -> int:
    raw = default if value is None else value
    try:
        return max(1, min(16, int(raw)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class ReviewSupervisor:
    """A resident queue consumer with bounded per-route workers."""

    def __init__(
        self,
        *,
        refresh_fn: Callable[[ClosedLoopReviewTask], dict[str, Any]],
        queue: ClosedLoopReviewQueue | None = None,
        max_workers: int | str | None = None,
        poll_seconds: float = 0.25,
        stall_seconds: float | None = None,
    ) -> None:
        self.queue = queue or ClosedLoopReviewQueue()
        self.refresh_fn = refresh_fn
        self.max_workers = _bounded_workers(
            max_workers if max_workers is not None else os.getenv("DEX_CLOSED_LOOP_REVIEW_WORKERS", "16")
        )
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.stall_seconds = max(
            1.0,
            float(
                stall_seconds
                if stall_seconds is not None
                else _env_float("DEX_CLOSED_LOOP_REVIEW_STALL_SECONDS", 60.0)
            ),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[Future[Any], ClosedLoopReviewTask] = {}
        self._lock = threading.RLock()
        self._supervisor_errors = 0
        self._heartbeat_at = time.monotonic()
        self._last_progress_at = time.monotonic()
        self._last_progress_snapshot: tuple[int, int, int, int, int] | None = None
        self._ever_started = False
        self._restart_count = 0
        self._stall_events = 0
        self._last_stall_at: float | None = None
        self._stall_reason: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            if self._ever_started:
                self._restart_count += 1
            self._ever_started = True
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="review-worker",
            )
            self._heartbeat_at = time.monotonic()
            self._last_progress_at = time.monotonic()
            self._thread = threading.Thread(
                target=self._loop,
                name="review-supervisor",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
            executor = self._executor
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout)))
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=False)

    def run_once(self) -> bool:
        """Reap completed workers and dispatch at most one new task."""
        self._reap_finished()
        self._mark_heartbeat()
        self._mark_progress_if_changed()
        with self._lock:
            executor = self._executor
            running = len(self._futures)
        if executor is None or running >= self.max_workers:
            if executor is not None and running >= self.max_workers:
                self.queue.record_backpressure()
                self._mark_progress_if_changed()
            return False
        task = self.queue.pop_next()
        if task is None:
            return False
        try:
            future = executor.submit(self._run_task, task)
        except Exception as exc:  # executor failure must not kill the supervisor
            self.queue.mark_error(task, exc)
            with self._lock:
                self._supervisor_errors += 1
            self._mark_progress_if_changed()
            return True
        with self._lock:
            self._futures[future] = task
        self._mark_progress_if_changed()
        return True

    def status(self) -> dict[str, Any]:
        """Read-only status snapshot; only the main loop advances lifecycle clocks."""
        self._reap_finished()
        now = time.monotonic()
        with self._lock:
            thread = self._thread
            running = len(self._futures)
            supervisor_errors = self._supervisor_errors
            heartbeat_at = self._heartbeat_at
            last_progress_at = self._last_progress_at
            restart_count = self._restart_count
            stall_events = self._stall_events
            last_stall_at = self._last_stall_at
            stall_reason = self._stall_reason
        summary = self.queue.summary()
        stale = self.queue.stale_audit(
            queued_age_seconds=self.stall_seconds,
            running_age_seconds=max(self.stall_seconds * 5.0, 300.0),
        )
        queue_busy = int(summary.get("queued") or 0) + int(summary.get("running") or 0) > 0
        heartbeat_age = max(0.0, now - heartbeat_at)
        progress_age = max(0.0, now - last_progress_at)
        stall_detected = (
            (thread is not None and not thread.is_alive())
            or (queue_busy and progress_age >= self.stall_seconds)
            or int(stale.get("staleQueuedCount") or 0) > 0
            or int(stale.get("staleRunningCount") or 0) > 0
        )
        return {
            "running": bool(thread and thread.is_alive()),
            "workerCount": self.max_workers,
            "runningWorkers": running,
            "queue": summary,
            "supervisorErrors": supervisor_errors,
            "heartbeatAgeSeconds": heartbeat_age,
            "lastHeartbeatAtMonotonic": heartbeat_at,
            "lastProgressAtMonotonic": last_progress_at,
            "progressAgeSeconds": progress_age,
            "stallDetected": stall_detected,
            "stallSeconds": self.stall_seconds,
            "stallEvents": stall_events,
            "restartCount": restart_count,
            "lastStallAtMonotonic": last_stall_at,
            "stallReason": stall_reason,
            "staleQueue": stale,
        }

    def _run_task(self, task: ClosedLoopReviewTask) -> None:
        try:
            result = self.refresh_fn(task)
        except Exception as exc:  # one route failure is isolated to one task
            self.queue.mark_error(task, exc)
            self._mark_progress_if_changed()
            return
        self.queue.mark_completed(task, result)
        self._mark_progress_if_changed()

    def _reap_finished(self) -> None:
        with self._lock:
            finished = [future for future in self._futures if future.done()]
            for future in finished:
                self._futures.pop(future, None)
                try:
                    future.result()
                except Exception:
                    self._supervisor_errors += 1

    def _mark_heartbeat(self) -> None:
        with self._lock:
            self._heartbeat_at = time.monotonic()

    def _mark_progress_if_changed(self) -> None:
        summary = self.queue.summary()
        # Backpressure polling is not consumer progress: a worker that is
        # permanently stuck must not keep the progress clock fresh.
        signature = (
            int(summary.get("claimed") or 0),
            int(summary.get("completed") or 0),
            int(summary.get("failed") or 0),
            int(summary.get("skipped") or 0),
        )
        with self._lock:
            if self._last_progress_snapshot is not None and signature == self._last_progress_snapshot:
                return
            self._last_progress_snapshot = signature
            self._last_progress_at = time.monotonic()

    def revive_if_stalled(self, now: float | None = None) -> dict[str, Any]:
        """Detect a stopped/stalled supervisor and start a replacement consumer."""
        current = now if now is not None else time.monotonic()
        if self._stop.is_set():
            return {"stallDetected": False, "restarted": False, "reason": "stopped"}
        with self._lock:
            thread = self._thread
            ever_started = self._ever_started
        if thread is not None and not thread.is_alive() and ever_started:
            with self._lock:
                self._stall_events += 1
                self._last_stall_at = current
                self._stall_reason = "review_supervisor_thread_exited"
            self.start()
            self._mark_progress_if_changed()
            return {
                "stallDetected": True,
                "restarted": True,
                "reason": self._stall_reason,
                "restartCount": self._restart_count,
            }
        summary = self.queue.summary()
        queue_busy = int(summary.get("queued") or 0) + int(summary.get("running") or 0) > 0
        with self._lock:
            progress_age = max(0.0, current - self._last_progress_at)
        if queue_busy and progress_age >= self.stall_seconds:
            with self._lock:
                self._stall_events += 1
                self._last_stall_at = current
                self._stall_reason = "review_queue_not_consumed"
            stale = self.queue.stale_audit(
                queued_age_seconds=self.stall_seconds,
                running_age_seconds=max(self.stall_seconds * 5.0, 300.0),
                now=current,
            )
            for snapshot in stale.get("staleQueued") or []:
                task = self.queue.get(snapshot.get("group_key"))
                if task is not None and task.state == "queued":
                    self.queue.mark_stalled(task, reason="review_stall_queued_too_long")
            return {
                "stallDetected": True,
                "restarted": False,
                "reason": self._stall_reason,
                "stalledTasks": int(stale.get("staleQueuedCount") or 0),
            }
        return {"stallDetected": False, "restarted": False, "reason": None}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                progressed = self.run_once()
            except Exception:
                with self._lock:
                    self._supervisor_errors += 1
                progressed = False
            if not progressed:
                self._stop.wait(self.poll_seconds)


class BroadcastSupervisor:
    """Resident, observation-only supervisor for the native execution queue."""

    def __init__(
        self,
        *,
        status_fn: Callable[[], dict[str, Any]],
        poll_seconds: float = 1.0,
        stall_seconds: float | None = None,
    ) -> None:
        self.status_fn = status_fn
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.stall_seconds = max(
            1.0,
            float(
                stall_seconds
                if stall_seconds is not None
                else _env_float("DEX_CLOSED_LOOP_BROADCAST_STALL_SECONDS", 60.0)
            ),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._last_status: dict[str, Any] = {}
        self._errors = 0
        self._heartbeat_at = time.monotonic()
        self._restart_count = 0
        self._stall_events = 0
        self._last_stall_at: float | None = None
        self._stall_reason: str | None = None
        self._ever_started = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            if self._ever_started:
                self._restart_count += 1
            self._ever_started = True
            self._heartbeat_at = time.monotonic()
            self._thread = threading.Thread(
                target=self._loop,
                name="broadcast-supervisor",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout)))

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            thread = self._thread
            heartbeat_at = self._heartbeat_at
            return {
                "running": bool(thread and thread.is_alive()),
                "lastStatus": dict(self._last_status),
                "errors": self._errors,
                "heartbeatAgeSeconds": max(0.0, now - heartbeat_at),
                "lastHeartbeatAtMonotonic": heartbeat_at,
                "stallDetected": bool(thread and not thread.is_alive()),
                "stallSeconds": self.stall_seconds,
                "stallEvents": self._stall_events,
                "restartCount": self._restart_count,
                "lastStallAtMonotonic": self._last_stall_at,
                "stallReason": self._stall_reason,
                "broadcastOnlyThroughExecutionQueue": True,
            }

    def revive_if_stalled(self, now: float | None = None) -> dict[str, Any]:
        current = now if now is not None else time.monotonic()
        if self._stop.is_set():
            return {"stallDetected": False, "restarted": False, "reason": "stopped"}
        with self._lock:
            thread = self._thread
            ever_started = self._ever_started
        if thread is not None and not thread.is_alive() and ever_started:
            with self._lock:
                self._stall_events += 1
                self._last_stall_at = current
                self._stall_reason = "broadcast_supervisor_thread_exited"
            self.start()
            return {
                "stallDetected": True,
                "restarted": True,
                "reason": self._stall_reason,
            }
        return {"stallDetected": False, "restarted": False, "reason": None}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                status = self.status_fn()
                with self._lock:
                    self._last_status = dict(status or {})
                    self._heartbeat_at = time.monotonic()
            except Exception:
                with self._lock:
                    self._errors += 1
            self._stop.wait(self.poll_seconds)


__all__ = ["BroadcastSupervisor", "ReviewSupervisor"]
