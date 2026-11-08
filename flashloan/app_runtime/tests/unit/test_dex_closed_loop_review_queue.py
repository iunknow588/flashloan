from __future__ import annotations

import time

from dex.runtime.dex_closed_loop_review_queue import (
    ClosedLoopReviewQueue,
    FixedRefreshPipeline,
    SCAN_PRIORITY,
    USER_PRIORITY,
    normalize_closed_loop_group_key,
)


def test_normalize_group_key_lowercases_addresses():
    assert normalize_closed_loop_group_key("0xAbCdEf") == "0xabcdef"
    assert normalize_closed_loop_group_key("  ") == "fallback"


def test_submit_group_creates_scan_task():
    queue = ClosedLoopReviewQueue()
    task = queue.submit_group(
        group_key="0xabc",
        chain_id=46630,
        protocols=["uniswap_v4"],
        mode="scan",
    )
    assert task.state == "queued"
    assert task.priority == SCAN_PRIORITY
    assert task.protocols == ("uniswap_v4",)
    summary = queue.summary()
    assert summary["queued"] == 1


def test_get_returns_single_task_or_none():
    queue = ClosedLoopReviewQueue()
    assert queue.get("0xabc") is None
    task = queue.submit_group(group_key="0xabc")
    assert queue.get("0xABC") is task


def test_user_refresh_promotes_existing_group_to_deep():
    queue = ClosedLoopReviewQueue()
    queue.submit_group(group_key="0xabc", mode="scan")
    promoted = queue.submit_group(
        group_key="0xabc",
        mode="deep",
        priority=USER_PRIORITY,
    )
    assert promoted.priority == USER_PRIORITY
    assert promoted.mode == "deep"
    assert len(queue.snapshot()) == 1


def test_pop_next_only_returns_queued_highest_priority():
    queue = ClosedLoopReviewQueue()
    queue.submit_group(group_key="a", priority=SCAN_PRIORITY)
    queue.submit_group(group_key="b", priority=USER_PRIORITY)
    first = queue.pop_next()
    second = queue.pop_next()
    assert first is not None and first.group_key == "b"
    assert second is not None and second.group_key == "a"
    assert queue.pop_next() is None


def test_fixed_pipeline_runs_once_and_marks_completed():
    calls = []

    def refresh_fn(task):
        calls.append(task.group_key)
        return {"ok": True, "group_key": task.group_key, "updated_at": time.time()}

    pipeline = FixedRefreshPipeline(refresh_fn=refresh_fn)
    pipeline.queue.submit_group(group_key="0xabc")
    done = pipeline.run_once()
    assert done is not None
    assert done.state == "done"
    assert done.result["ok"] is True
    assert calls == ["0xabc"]


def test_fixed_pipeline_marks_error_without_swallowing():
    def refresh_fn(task):
        raise RuntimeError("rpc down")

    pipeline = FixedRefreshPipeline(refresh_fn=refresh_fn)
    pipeline.queue.submit_group(group_key="0xabc")
    failed = pipeline.run_once()
    assert failed is not None
    assert failed.state == "error"
    assert "rpc down" in failed.error
    assert failed.error_stack is not None
    assert failed.error_stack[0]["module"] == "DEX"
    assert failed.error_stack[0]["code"] == 3099


def test_fixed_pipeline_start_uses_configured_group_workers(monkeypatch):
    monkeypatch.setenv("DEX_CLOSED_LOOP_GROUP_WORKERS", "2")

    def refresh_fn(task):
        time.sleep(0.01)
        return {"ok": True, "group_key": task.group_key}

    pipeline = FixedRefreshPipeline(refresh_fn=refresh_fn)
    pipeline.start()
    assert len(pipeline._threads) >= 2
    assert all(thread.daemon for thread in pipeline._threads)
    pipeline.stop()


def test_review_queue_metrics_track_claim_complete_error_skip():
    queue = ClosedLoopReviewQueue()
    queue.submit_group(group_key="a")
    queue.submit_group(group_key="b")
    queue.submit_group(group_key="c", priority=USER_PRIORITY)
    queue.submit_group(group_key="d", priority=USER_PRIORITY)

    first = queue.pop_next()
    second = queue.pop_next()
    assert first is not None and second is not None
    queue.mark_completed(first, {"ok": True})
    queue.mark_error(second, RuntimeError("boom"))
    assert queue.metrics()["claimed"] == 2
    assert queue.metrics()["completed"] == 1
    assert queue.metrics()["failed"] == 1

    skipped = queue.pop_next()
    assert skipped is not None
    queue.mark_skipped(skipped, reason="scope_isolated")
    assert skipped.state == "skipped"
    assert queue.metrics()["skipped"] == 1
    queue.record_backpressure()
    assert queue.metrics()["backpressure"] == 1

    summary = queue.summary()
    assert summary["claimed"] == 3
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert summary["skipped"] == 1
    assert summary["backpressure"] == 1
    assert summary["total"] == 4


def test_review_queue_heartbeat_updates_running_task():
    queue = ClosedLoopReviewQueue()
    task = queue.submit_group(group_key="a")
    popped = queue.pop_next()
    assert popped is task
    assert task.last_heartbeat_at is None
    queue.touch_heartbeat(task)
    assert task.last_heartbeat_at is not None
    queue.mark_completed(task, {"ok": True})
    assert queue.metrics()["completed"] == 1


def test_review_queue_stale_audit_flags_queued_and_running():
    queue = ClosedLoopReviewQueue()
    running = queue.submit_group(group_key="arb")
    queued = queue.submit_group(group_key="aza")
    popped = queue.pop_next()
    assert popped is running
    queued.updated_at = time.time() - 500
    running.last_heartbeat_at = time.time() - 500

    audit = queue.stale_audit(
        queued_age_seconds=10,
        running_age_seconds=10,
    )
    assert audit["staleQueuedCount"] == 1
    assert audit["staleRunningCount"] == 1
    assert audit["staleQueued"][0]["group_key"] == "aza"
    assert audit["staleRunning"][0]["group_key"] == "arb"


def test_review_queue_mark_stalled_and_requeue_preserves_audit_counts():
    queue = ClosedLoopReviewQueue()
    task = queue.submit_group(group_key="a")
    task.updated_at = time.time() - 500

    audit = queue.stale_audit(queued_age_seconds=10)
    assert audit["staleQueuedCount"] == 1

    queue.mark_stalled(task, reason="review_stall_queued_too_long")
    assert task.state == "stalled"
    assert task.stall_count == 1
    assert task.error == "review_stall_queued_too_long"
    assert queue.summary()["stalled"] == 1
    assert queue.summary()["stalled_total"] == 1

    queue.requeue(task)
    assert task.state == "queued"
    assert task.error is None
    assert task.stall_count == 1
    assert queue.metrics()["requeued"] == 1
    assert queue.summary()["queued"] == 1
    requeued_audit = queue.stale_audit(queued_age_seconds=10)
    assert requeued_audit["staleQueuedCount"] == 0
