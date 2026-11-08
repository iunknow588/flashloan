from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def test_bnb_shared_database_path_and_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASHLOAN_BNB_SHARED_DATABASE", "1")
    monkeypatch.setenv("FLASHLOAN_BNB_DATABASE_PATH", str(tmp_path / "local_store_bnb.sqlite3"))
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)

    from core.db.bnb_database import bnb_shared_database_path, ensure_bnb_shared_schema
    from core.rs_bridge import _bnb_database_path

    path = bnb_shared_database_path()
    assert path == tmp_path / "local_store_bnb.sqlite3"
    assert _bnb_database_path() == str(path.resolve())
    override = tmp_path / "override.sqlite3"
    assert _bnb_database_path(str(override)) == str(override.resolve())

    report = ensure_bnb_shared_schema()
    assert report["ok"] is True
    connection = sqlite3.connect(report["database"])
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "bnb_market_observations" in tables
        assert "bnb_market_snapshots" in tables
    finally:
        connection.close()


def test_bnb_shared_collector_status_without_start(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASHLOAN_BNB_SHARED_DATABASE", "1")
    monkeypatch.setenv("FLASHLOAN_BNB_DATABASE_PATH", str(tmp_path / "bnb.sqlite3"))

    from core.db.bnb_database import bnb_shared_collector_status

    status = bnb_shared_collector_status()
    assert status["enabled"] is False
    assert status["running"] is False
    assert status["database"] == str((tmp_path / "bnb.sqlite3").resolve())


def test_bnb_shared_database_disabled_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASHLOAN_BNB_SHARED_DATABASE", "0")
    monkeypatch.setenv("FLASHLOAN_KV_PATH", str(tmp_path / "chain.sqlite3"))

    from core.rs_bridge import _bnb_database_path

    assert _bnb_database_path() == str((tmp_path / "chain.sqlite3").resolve())


def test_ensure_bnb_shared_collector_respects_disabled_switch(monkeypatch):
    monkeypatch.setenv("FLASHLOAN_BNB_SHARED_DATABASE", "0")

    from core.db import bnb_database

    calls: list[str] = []
    monkeypatch.setattr(bnb_database, "_COLLECTOR", None)
    monkeypatch.setattr(
        bnb_database,
        "ensure_bnb_shared_schema",
        lambda *args, **kwargs: calls.append("schema"),
    )

    collector = bnb_database.ensure_bnb_shared_collector_running()

    assert calls == []
    assert collector.state.enabled is False
    assert collector.status()["running"] is False
    assert bnb_database._COLLECTOR is None
