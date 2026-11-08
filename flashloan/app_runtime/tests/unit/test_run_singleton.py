from __future__ import annotations

import os
import uuid

import pytest

from app_runtime import run as run_module


def test_runtime_singleton_lock_path_under_state(monkeypatch) -> None:
    monkeypatch.setenv("FLASHLOAN_RUNTIME_DIR", r"C:\runtime")
    assert run_module._runtime_singleton_lock_path() == (
        __import__("pathlib").Path(r"C:\runtime") / "state" / "run.singleton.lock"
    )


def test_acquire_runtime_singleton_wait_retries_until_success(monkeypatch) -> None:
    attempts = iter([False, True])
    monkeypatch.setattr(run_module, "acquire_runtime_singleton", lambda: next(attempts))
    monkeypatch.setattr(run_module.time, "sleep", lambda _seconds: None)
    assert run_module.acquire_runtime_singleton_wait(timeout_seconds=1.0) is True


def test_acquire_runtime_singleton_wait_refuses_when_busy(monkeypatch) -> None:
    monkeypatch.setattr(run_module, "acquire_runtime_singleton", lambda: False)
    monkeypatch.setattr(run_module.time, "sleep", lambda _seconds: None)
    assert run_module.acquire_runtime_singleton_wait(timeout_seconds=0.0) is False


def test_acquire_release_tracks_process_local_handle(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(run_module, "_worktree_singleton_key", lambda: uuid.uuid4().hex)
    monkeypatch.setattr(run_module, "_acquire_windows_mutex", lambda _name: sentinel)
    monkeypatch.setattr(run_module, "_release_windows_mutex", lambda _handle: None)
    assert run_module.acquire_runtime_singleton() is True
    assert run_module._SINGLETON_LOCK is sentinel
    run_module.release_runtime_singleton()
    assert run_module._SINGLETON_LOCK is None


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex only")
def test_windows_named_mutex_serializes_instances() -> None:
    name = run_module._SINGLETON_NAME_PREFIX + "test-" + uuid.uuid4().hex
    first = run_module._acquire_windows_mutex(name)
    assert first is not None
    try:
        second = run_module._acquire_windows_mutex(name)
        assert second is None
    finally:
        run_module._release_windows_mutex(first)
    reacquired = run_module._acquire_windows_mutex(name)
    assert reacquired is not None
    run_module._release_windows_mutex(reacquired)


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock only")
def test_posix_lock_file_serializes_instances(tmp_path) -> None:
    lock_path = tmp_path / "run.singleton.lock"
    first = run_module._acquire_posix_lock(lock_path)
    assert first is not None
    try:
        assert run_module._acquire_posix_lock(lock_path) is None
    finally:
        import fcntl

        fcntl.flock(first.fileno(), fcntl.LOCK_UN)
        first.close()


def test_acquire_runtime_singleton_rejects_busy_windows_mutex(monkeypatch) -> None:
    monkeypatch.setattr(run_module, "_worktree_singleton_key", lambda: uuid.uuid4().hex)
    monkeypatch.setattr(run_module, "_acquire_windows_mutex", lambda _name: None)
    assert run_module.acquire_runtime_singleton() is False
