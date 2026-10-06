from __future__ import annotations

import heapq
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


SCAN_PRIORITY = 1
USER_PRIORITY = 0

_MODE_DEPTH = {
    "scan": 0,
    "deep": 1,
    "broadcast": 2,
}


def normalize_closed_loop_group_key(group_key: str | None) -> str:
    value = str(group_key or "").strip().lower()
    return value or "fallback"


@dataclass
class ClosedLoopReviewTask:
    group_key: str
    chain_id: int | None = None
    protocols: tuple[str, ...] = ()
    mode: str = "scan"
    priority: int = SCAN_PRIORITY
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    updated_at: float | None = None
    error: str | None = None
    error_stack: list[dict[str, Any]] | None = None
    result: dict[str, Any] | None = None


class ClosedLoopReviewQueue:
    """Priority queue for closed-loop group refreshes.

    Both the periodic scanner and the single-route review flow submit tasks
    here.  A user-triggered deep review simply uses the same pipeline with a
    higher priority and deeper mode, so there is only one review scheduler.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, ClosedLoopReviewTask] = {}
        self._heap: list[tuple[int, int, str]] = []
        self._sequence = 0

    def submit_group(
        self,
        *,
        group_key: str | None,
        chain_id: int | None = None,
        protocols: list[str] | tuple[str, ...] | None = None,
        mode: str = "scan",
        priority: int | None = None,
    ) -> ClosedLoopReviewTask:
        key = normalize_closed_loop_group_key(group_key)
        normalized_protocols = tuple(
            sorted(str(item).strip().lower() for item in (protocols or []) if str(item).strip())
        )
        requested_priority = SCAN_PRIORITY if priority is None else int(priority)
        requested_mode = str(mode).strip().lower() or "scan"
        if requested_mode not in _MODE_DEPTH:
            requested_mode = "scan"
        with self._lock:
            existing = self._tasks.get(key)
            if existing is not None:
                if requested_priority < existing.priority:
                    existing.priority = requested_priority
                if _MODE_DEPTH[requested_mode] > _MODE_DEPTH[existing.mode]:
                    existing.mode = requested_mode
                existing.state = "queued"
                existing.error = None
                existing.error_stack = None
                existing.result = None
                existing.updated_at = time.time()
                self._push(existing)
                return existing
            task = ClosedLoopReviewTask(
                group_key=key,
                chain_id=chain_id,
                protocols=normalized_protocols,
                mode=requested_mode,
                priority=requested_priority,
            )
            self._tasks[key] = task
            self._push(task)
            return task

    def pop_next(self) -> ClosedLoopReviewTask | None:
        with self._lock:
            while self._heap:
                priority, _seq, key = heapq.heappop(self._heap)
                task = self._tasks.get(key)
                if task is None:
                    continue
                if task.state != "queued" or task.priority != priority:
                    continue
                task.state = "running"
                task.started_at = time.time()
                task.updated_at = task.started_at
                return task
            return None

    def mark_completed(self, task: ClosedLoopReviewTask, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            task.state = "done"
            task.updated_at = time.time()
            task.result = result or {}

    def mark_error(
        self,
        task: ClosedLoopReviewTask,
        error: Exception | str,
        error_stack: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._lock:
            task.state = "error"
            task.updated_at = time.time()
            task.error = str(error)
            task.error_stack = error_stack

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for task in self._tasks.values():
                rows.append(
                    {
                        "group_key": task.group_key,
                        "chain_id": task.chain_id,
                        "protocols": list(task.protocols),
                        "mode": task.mode,
                        "priority": task.priority,
                        "state": task.state,
                        "created_at": task.created_at,
                        "started_at": task.started_at,
                        "updated_at": task.updated_at,
                        "error": task.error,
                        "errorStack": task.error_stack or [],
                    }
                )
            return rows

    def get(self, group_key: str | None) -> ClosedLoopReviewTask | None:
        with self._lock:
            return self._tasks.get(normalize_closed_loop_group_key(group_key))

    def summary(self) -> dict[str, int]:
        with self._lock:
            counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
            for task in self._tasks.values():
                counts[task.state] = counts.get(task.state, 0) + 1
            counts["total"] = len(self._tasks)
            return counts

    def _push(self, task: ClosedLoopReviewTask) -> None:
        heapq.heappush(self._heap, (task.priority, self._sequence, task.group_key))
        self._sequence += 1


class FixedRefreshPipeline:
    """Shared worker that runs the fixed closed-loop refresh flow."""

    def __init__(
        self,
        *,
        refresh_fn: Callable[[ClosedLoopReviewTask], dict[str, Any]],
        queue: ClosedLoopReviewQueue | None = None,
    ) -> None:
        self.queue = queue or ClosedLoopReviewQueue()
        self.refresh_fn = refresh_fn
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def _worker_count(self) -> int:
        raw = os.getenv("DEX_CLOSED_LOOP_GROUP_WORKERS", os.getenv("DEX_CLOSED_LOOP_WORKERS", "3"))
        try:
            return max(1, min(8, int(raw)))
        except (TypeError, ValueError):
            return 1

    def start(self) -> None:
        with self._lock:
            if any(thread.is_alive() for thread in self._threads):
                return
            self._stop.clear()
            self._threads = []
            for index in range(self._worker_count()):
                thread = threading.Thread(
                    target=self._loop,
                    daemon=True,
                    name=f"fixed-closed-loop-refresh-{index}",
                )
                self._threads.append(thread)
                thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=2.0)

    def run_once(self) -> ClosedLoopReviewTask | None:
        task = self.queue.pop_next()
        if task is None:
            return None
        try:
            result = self.refresh_fn(task)
        except Exception as exc:
            self.queue.mark_error(
                task,
                exc,
                error_stack=[
                    {
                        "module": "DEX",
                        "code": 3099,
                        "flow": "closed_loop_review",
                        "nodeId": task.group_key,
                        "message": str(exc)[:500],
                    }
                ],
            )
            return task
        self.queue.mark_completed(task, result)
        return task

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.run_once() is None:
                self._stop.wait(timeout=0.5)


__all__ = [
    "ClosedLoopReviewQueue",
    "ClosedLoopReviewTask",
    "FixedRefreshPipeline",
    "SCAN_PRIORITY",
    "USER_PRIORITY",
    "normalize_closed_loop_group_key",
]
