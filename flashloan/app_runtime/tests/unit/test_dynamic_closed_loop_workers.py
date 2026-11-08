"""Tests for dynamic closed-loop worker lifecycle counters.

The Rust pool owns native review/broadcast threads; the Python bridge only
exposes the counters this test asserts.  Dynamic spawn itself is covered by
``cargo test worker_pool --lib``; this file pins the status contract and the
Python-side max-worker bound.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from dex.runtime import rust_closed_loop


def test_status_reports_lifecycle_counters() -> None:
    status = {
        "createdTotal": 0,
        "active": 0,
        "completed": 0,
        "idleExits": 0,
        "executionQueue": {
            "activeExecutions": 0,
            "queued": 0,
            "running": 0,
            "done": 0,
            "error": 0,
            "skipped": 0,
        },
    }

    assert set(status) >= {
        "createdTotal",
        "active",
        "completed",
        "idleExits",
    }
    assert status["executionQueue"]["activeExecutions"] == 0


def test_python_bridge_passes_dynamic_worker_counters(
    monkeypatch,
) -> None:
    class _FakePool:
        def compact_status_json(self) -> dict:
            return {
                "engine": "rust_native_closed_loop",
                "createdTotal": 12,
                "active": 2,
                "completed": 10,
                "idleExits": 3,
                "executionQueue": {},
            }

        def status_json(self) -> dict:
            return self.compact_status_json()

    monkeypatch.setattr(
        rust_closed_loop,
        "get_native_pool",
        lambda **kwargs: _FakePool(),
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "native_closed_loop_enabled",
        lambda: True,
    )
    payload = rust_closed_loop.native_worker_status(include_payloads=False)

    assert payload["enabled"] is True
    assert payload["createdTotal"] == 12
    assert payload["active"] == 2
    assert payload["completed"] == 10
    assert payload["idleExits"] == 3


def test_worker_pool_constructor_accepts_unified_max_workers(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakePool:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    fake_module = SimpleNamespace(ClosedLoopWorkerPool=_FakePool)
    monkeypatch.setitem(sys.modules, "flashloan_rs", fake_module)
    monkeypatch.setattr(rust_closed_loop, "_POOL", None)
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_DATABASE", {})
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_CHAIN", {})
    monkeypatch.setattr(
        rust_closed_loop,
        "_database_path",
        lambda: "sqlite:///tmp/test.sqlite3",
    )
    monkeypatch.delenv("DEX_CLOSED_LOOP_EXECUTION_WORKERS", raising=False)

    pool = rust_closed_loop.get_native_pool(max_workers=16)
    assert isinstance(pool, _FakePool)
    assert captured["max_workers"] == 16


def test_worker_pool_env_cap_is_32(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakePool:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    fake_module = SimpleNamespace(ClosedLoopWorkerPool=_FakePool)
    monkeypatch.setitem(sys.modules, "flashloan_rs", fake_module)
    monkeypatch.setattr(rust_closed_loop, "_POOL", None)
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_DATABASE", {})
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_CHAIN", {})
    monkeypatch.setattr(
        rust_closed_loop,
        "_database_path",
        lambda: "sqlite:///tmp/test.sqlite3",
    )
    monkeypatch.setenv("DEX_CLOSED_LOOP_MAX_WORKERS", "40")
    monkeypatch.delenv("DEX_CLOSED_LOOP_EXECUTION_WORKERS", raising=False)

    pool = rust_closed_loop.get_native_pool()
    assert isinstance(pool, _FakePool)
    assert captured["max_workers"] == 32
