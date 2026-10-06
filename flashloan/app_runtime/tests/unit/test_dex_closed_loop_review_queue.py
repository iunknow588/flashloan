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
