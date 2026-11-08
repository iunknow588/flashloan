from __future__ import annotations

from pathlib import Path

from core.db.chain_database_context import chain_database_scope
from core.db.database_path import absolute_database_path


def test_absolute_database_path_resolves_relative_paths_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = absolute_database_path("src/runtime/local_store.sqlite3")
    assert resolved == (tmp_path / "src/runtime/local_store.sqlite3").resolve()


def test_absolute_database_path_keeps_absolute_input(tmp_path):
    target = (tmp_path / "local_store.sqlite3").resolve()
    assert absolute_database_path(str(target)) == target


def test_absolute_database_path_defaults_to_default_chain_database(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)
    monkeypatch.delenv("FLASHLOAN_RUNTIME_DIR", raising=False)
    resolved = absolute_database_path()
    assert resolved.is_absolute()
    assert resolved.name == "local_store_4663.sqlite3"
    assert resolved == (Path(__file__).resolve().parents[3] / "db" / "local_store_4663.sqlite3").resolve()


def test_absolute_database_path_resolves_env_relative_path_against_cwd(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLASHLOAN_KV_PATH", "custom/local_store.sqlite3")
    assert absolute_database_path() == (
        tmp_path / "custom/local_store.sqlite3"
    ).resolve()


def test_absolute_database_path_uses_chain_scope_when_enabled(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)
    monkeypatch.delenv("FLASHLOAN_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("FLASHLOAN_CHAIN_DATABASES", "1")
    with chain_database_scope(46630, network="robinhood-testnet", source="test"):
        resolved = absolute_database_path()
    assert resolved.name == "local_store_46630.sqlite3"
    assert resolved == (
        Path(__file__).resolve().parents[3] / "db" / "local_store_46630.sqlite3"
    ).resolve()


def test_chain_database_path_uses_configured_sqlite_dir(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)

    from core.db.chain_database_context import chain_database_path

    database_root = Path(__file__).resolve().parents[3] / "db"
    assert chain_database_path(46630) == (database_root / "local_store_46630.sqlite3").resolve()
    assert chain_database_path(4663) == (database_root / "local_store_4663.sqlite3").resolve()
    assert chain_database_path(56) == (database_root / "local_store_bnb.sqlite3").resolve()


def test_absolute_database_path_uses_chain_scope_without_flag(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)
    monkeypatch.delenv("FLASHLOAN_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("FLASHLOAN_CHAIN_DATABASES", raising=False)
    with chain_database_scope(46630, network="robinhood-testnet", source="test"):
        resolved = absolute_database_path()
    assert resolved.name == "local_store_46630.sqlite3"
