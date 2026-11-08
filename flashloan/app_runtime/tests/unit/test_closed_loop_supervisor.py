from __future__ import annotations

import threading
import time
import sys

from dex.runtime.closed_loop_supervisor import BroadcastSupervisor, ReviewSupervisor
from dex.runtime.dex_closed_loop_review_queue import ClosedLoopReviewQueue, ClosedLoopReviewTask


def test_review_supervisor_isolates_one_worker_failure_and_continues():
    queue = ClosedLoopReviewQueue()
    calls: list[str] = []

    def refresh(task: ClosedLoopReviewTask):
        calls.append(task.group_key)
        if task.group_key == "bad":
            raise RuntimeError("route failed")
        return {"ok": True, "group": task.group_key}

    supervisor = ReviewSupervisor(
        refresh_fn=refresh,
        queue=queue,
        max_workers=2,
        poll_seconds=0.01,
    )
    queue.submit_group(group_key="bad")
    queue.submit_group(group_key="good")
    supervisor.start()
    try:
        deadline = time.time() + 2
        while time.time() < deadline and queue.summary()["completed"] < 1:
            time.sleep(0.01)
    finally:
        supervisor.stop()

    assert set(calls) == {"bad", "good"}
    summary = queue.summary()
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert supervisor.status()["running"] is False


def test_review_supervisor_has_one_resident_consumer_and_bounded_workers():
    queue = ClosedLoopReviewQueue()
    started = threading.Event()
    release = threading.Event()

    def refresh(_task: ClosedLoopReviewTask):
        started.set()
        release.wait(10)
        return {"ok": True}

    supervisor = ReviewSupervisor(
        refresh_fn=refresh,
        queue=queue,
        max_workers=1,
        poll_seconds=0.01,
    )
    queue.submit_group(group_key="a")
    queue.submit_group(group_key="b")
    supervisor.start()
    try:
        assert started.wait(1)
        deadline = time.time() + 1
        while time.time() < deadline and queue.metrics()["backpressure"] == 0:
            time.sleep(0.01)
        status = supervisor.status()
        assert status["running"] is True
        assert status["workerCount"] == 1
        assert status["runningWorkers"] == 1
        assert status["queue"]["backpressure"] > 0
    finally:
        release.set()
        supervisor.stop()


def test_review_supervisor_heartbeat_and_stall_detection():
    queue = ClosedLoopReviewQueue()
    started = threading.Event()
    release = threading.Event()

    def refresh(_task: ClosedLoopReviewTask):
        started.set()
        release.wait(10)
        return {"ok": True}

    supervisor = ReviewSupervisor(
        refresh_fn=refresh,
        queue=queue,
        max_workers=1,
        poll_seconds=0.01,
        stall_seconds=0.1,
    )
    queue.submit_group(group_key="a")
    queue.submit_group(group_key="b")
    supervisor.start()
    try:
        assert started.wait(1)
        supervisor._last_progress_snapshot = None
        supervisor._last_progress_at = time.monotonic() - 60
        heartbeat_before = supervisor._heartbeat_at
        progress_before = supervisor._last_progress_at
        status = supervisor.status()
        assert supervisor._heartbeat_at == heartbeat_before
        assert supervisor._last_progress_at == progress_before
        assert status["running"] is True
        watch = supervisor.revive_if_stalled(now=time.monotonic())
        assert watch["stallDetected"] is True
        assert watch["reason"] == "review_queue_not_consumed"
    finally:
        release.set()
        supervisor.stop()


def test_review_supervisor_restarts_after_thread_exit():
    queue = ClosedLoopReviewQueue()

    def refresh(_task: ClosedLoopReviewTask):
        return {"ok": True}

    supervisor = ReviewSupervisor(
        refresh_fn=refresh,
        queue=queue,
        max_workers=1,
        poll_seconds=0.01,
        stall_seconds=0.1,
    )
    supervisor._loop = lambda: None  # make the first consumer thread exit immediately
    supervisor.start()
    thread = supervisor._thread
    assert thread is not None
    thread.join(1)
    assert not thread.is_alive()

    watch = supervisor.revive_if_stalled(now=time.monotonic())
    assert watch["stallDetected"] is True
    assert watch["restarted"] is True
    assert watch["reason"] == "review_supervisor_thread_exited"
    assert watch["restartCount"] == 1
    assert supervisor.status()["stallEvents"] == 1
    supervisor.stop()


def test_broadcast_supervisor_only_observes_execution_queue():
    observed: list[int] = []

    def status_fn():
        observed.append(len(observed) + 1)
        return {"executionQueue": {"queued": len(observed)}}

    supervisor = BroadcastSupervisor(status_fn=status_fn, poll_seconds=0.01)
    supervisor.start()
    try:
        deadline = time.time() + 1
        while time.time() < deadline and not observed:
            time.sleep(0.01)
    finally:
        supervisor.stop()

    assert observed
    status = supervisor.status()
    assert status["running"] is False
    assert status["broadcastOnlyThroughExecutionQueue"] is True
    assert status["lastStatus"]["executionQueue"]["queued"] == observed[-1]


def test_broadcast_supervisor_restarts_after_thread_exit():
    def status_fn():
        return {"executionQueue": {"queued": 0}}

    supervisor = BroadcastSupervisor(status_fn=status_fn, poll_seconds=0.01)
    supervisor._loop = lambda: None
    supervisor.start()
    thread = supervisor._thread
    assert thread is not None
    thread.join(1)
    assert not thread.is_alive()

    watch = supervisor.revive_if_stalled(now=time.monotonic())
    assert watch["stallDetected"] is True
    assert watch["restarted"] is True
    assert watch["reason"] == "broadcast_supervisor_thread_exited"
    supervisor.stop()


def test_scanner_builds_review_supervisor_when_env_enabled_and_native_disabled(
    tmp_path,
    monkeypatch,
) -> None:
    from dex.runtime import dex_opportunity_scanner_engine as engine

    monkeypatch.setenv("DEX_CLOSED_LOOP_SUPERVISOR_ENABLED", "1")
    monkeypatch.delenv("DEX_CLOSED_LOOP_REVIEW_WORKERS", raising=False)
    monkeypatch.setattr(engine, "native_closed_loop_enabled", lambda: False)

    scanner = engine.DexOpportunityScanner(cache_path=None)
    assert scanner.closed_loop_review_supervisor is not None
    assert scanner.closed_loop_broadcast_supervisor is None
    assert scanner.status()["closed_loop_supervisors"]["enabled"] is True
    scanner._watch_closed_loop_supervisors()
    status = scanner.status()
    supervisors = status["closed_loop_supervisors"]
    assert supervisors["lastSupervisorWatch"]["watchedAt"] is not None
    assert "reviewStallDetected" in supervisors
    assert "stallDetected" in status["closed_loop_review_queue"]["last_supervisor_watch"]
    scanner.stop()


def test_scanner_does_not_build_python_broadcast_supervisor_when_native_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    from dex.runtime import dex_opportunity_scanner_engine as engine

    monkeypatch.setenv("DEX_CLOSED_LOOP_SUPERVISOR_ENABLED", "1")
    monkeypatch.setattr(engine, "native_closed_loop_enabled", lambda: True)

    scanner = engine.DexOpportunityScanner(cache_path=None)
    assert scanner.closed_loop_review_supervisor is None
    assert scanner.closed_loop_broadcast_supervisor is None
    status = scanner.status()["closed_loop_supervisors"]
    assert status["enabled"] is False
    assert status["broadcast"]["mode"] == "native_rust_manager"
    scanner.stop()


def test_scanner_watchdog_exposes_runtime_state_summary_and_stuck_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    from dex.runtime import dex_opportunity_scanner_engine as engine

    monkeypatch.setenv("DEX_CLOSED_LOOP_SUPERVISOR_ENABLED", "1")
    monkeypatch.setattr(engine, "native_closed_loop_enabled", lambda: True)
    calls = {}

    def fake_recover(*args, **kwargs):
        calls["recover"] = True
        return {"failed": 2, "records": [{"loop_key": "a"}, {"loop_key": "b"}], "error": None}

    def fake_summary(*args, **kwargs):
        calls["summary"] = True
        return {
            "available": True,
            "states": {"execution_success": 3, "execution_failed": 4},
            "missingBroadcast": 2,
            "terminalExecution": 7,
        }

    class FakeConsumer:
        recover_stuck_execution_loops = staticmethod(fake_recover)
        runtime_state_summary = staticmethod(fake_summary)

    monkeypatch.setitem(sys.modules, "dex.runtime.closed_loop_consumer", FakeConsumer())

    scanner = engine.DexOpportunityScanner(cache_path=None)
    watch = scanner._watch_closed_loop_supervisors()
    assert calls.get("recover") is True
    assert calls.get("summary") is True
    assert watch["stuckExecutionRecovered"]["failed"] == 2
    assert watch["loopStateSummary"]["terminalExecution"] == 7
    status = scanner.status()
    assert status["closed_loop_review_queue"]["loopStateSummary"]["missingBroadcast"] == 2
    assert status["closed_loop_review_queue"]["stuckExecutionRecovered"] == 2
    scanner.stop()


def test_scanner_defaults_to_rust_watchdog_without_env_gate(
    tmp_path,
    monkeypatch,
) -> None:
    from dex.runtime import dex_opportunity_scanner_engine as engine

    monkeypatch.delenv("DEX_CLOSED_LOOP_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.setattr(engine, "native_closed_loop_enabled", lambda: True)

    scanner = engine.DexOpportunityScanner(cache_path=None)
    assert scanner.closed_loop_review_supervisor is None
    assert scanner.closed_loop_broadcast_supervisor is None
    status = scanner.status()
    assert status["closed_loop_supervisors"]["enabled"] is False
    assert status["closed_loop_supervisors"]["pythonFallbackEnabled"] is False
    assert status["execution_backend"] == "rust"
    assert status["closed_loop_review_queue"]["executionBackend"] == "rust"
    scanner.stop()


def test_scanner_can_disable_watchdog_when_explicitly_off(
    tmp_path,
    monkeypatch,
) -> None:
    from dex.runtime import dex_opportunity_scanner_engine as engine

    monkeypatch.setenv("DEX_CLOSED_LOOP_SUPERVISOR_ENABLED", "0")
    monkeypatch.setattr(engine, "native_closed_loop_enabled", lambda: True)

    scanner = engine.DexOpportunityScanner(cache_path=None)
    assert scanner.closed_loop_review_supervisor is None
    assert scanner.closed_loop_broadcast_supervisor is None
    assert scanner.status()["closed_loop_supervisors"]["enabled"] is False
    scanner.stop()
