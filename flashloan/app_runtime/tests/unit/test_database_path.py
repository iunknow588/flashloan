from __future__ import annotations

from pathlib import Path

from core.db.database_path import absolute_database_path


def test_absolute_database_path_resolves_relative_paths_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = absolute_database_path("src/runtime/local_store.sqlite3")
    assert resolved == (tmp_path / "src/runtime/local_store.sqlite3").resolve()


def test_absolute_database_path_keeps_absolute_input(tmp_path):
    target = (tmp_path / "local_store.sqlite3").resolve()
    assert absolute_database_path(str(target)) == target


def test_absolute_database_path_defaults_to_runtime_store(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)
    monkeypatch.delenv("FLASHLOAN_RUNTIME_DIR", raising=False)
    resolved = absolute_database_path()
    assert resolved.is_absolute()
    assert resolved.name == "local_store.sqlite3"
    assert resolved.parent.name == "runtime"


def test_absolute_database_path_resolves_env_relative_path_against_cwd(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLASHLOAN_KV_PATH", "custom/local_store.sqlite3")
    assert absolute_database_path() == (
        tmp_path / "custom/local_store.sqlite3"
    ).resolve()
