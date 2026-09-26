from __future__ import annotations

import sqlite3
import threading
import time

from core import local_kv
from core.kv_rows import collection_key, latest_rows, load_rows, upsert_row
from core.db.storage_liquidation_legacy import try_acquire_observer_lock


def test_put_get_delete_count_and_scan(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    local_kv.put(path, "parameters", "page|market", {"enabled": True, "nested": {"value": 1}})
    local_kv.put(path, "parameters", "page|other", {"enabled": False})
    local_kv.put(path, "observations", "obs|1", {"symbol": "AVAX"})

    assert local_kv.get(path, "parameters", "page|market") == {"enabled": True, "nested": {"value": 1}}
    assert local_kv.count(path, "parameters") == 2
    assert local_kv.count(path, "observations") == 1
    assert [row["value"]["symbol"] for row in local_kv.scan(path, "observations")] == ["AVAX"]

    assert local_kv.delete(path, "parameters", "page|market") is True
    assert local_kv.delete(path, "parameters", "page|market") is False
    assert local_kv.count(path, "parameters") == 1


def test_scan_prefix_and_descending_order(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    for key in ("b|1", "a|2", "b|0"):
        local_kv.put(path, "series", key, {"key": key})

    rows = local_kv.scan(path, "series", prefix="b|")
    assert [row["key"] for row in rows] == ["b|0", "b|1"]

    descending = local_kv.scan(path, "series", prefix="b|", descending=True)
    assert [row["key"] for row in descending] == ["b|1", "b|0"]


def test_put_many_and_clear_collection(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    local_kv.put_many(path, "rows", [("1", {"id": 1}), ("2", {"id": 2})])

    assert local_kv.count(path, "rows") == 2
    assert local_kv.clear_collection(path, "rows") == 2
    assert local_kv.count(path, "rows") == 0


def test_meta_round_trip(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    local_kv.meta_put(path, "schema_migrations", {"applied": ["one"]})

    assert local_kv.meta_get(path, "schema_migrations") == {"applied": ["one"]}


def test_meta_insert_if_absent_and_delete_if_matches(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    owner = {"owner": "pid:1", "lock_id": 1234}

    assert local_kv.meta_insert_if_absent(path, "locks|observer|1234", owner) is True
    assert local_kv.meta_insert_if_absent(path, "locks|observer|1234", {"owner": "other"}) is False
    assert local_kv.meta_get(path, "locks|observer|1234") == owner

    assert local_kv.meta_delete_if_matches(path, "locks|observer|1234", {"owner": "other"}) is False
    assert local_kv.meta_delete_if_matches(path, "locks|observer|1234", owner) is True
    assert local_kv.meta_get(path, "locks|observer|1234") is None


def test_store_path_honors_flashloan_kv_path(monkeypatch, tmp_path):
    env_path = tmp_path / "env.sqlite3"
    monkeypatch.setenv("FLASHLOAN_KV_PATH", str(env_path))

    assert local_kv.store_path() == env_path.resolve()


def test_store_path_resolves_relative_env_under_runtime_dir(monkeypatch):
    monkeypatch.setenv("FLASHLOAN_KV_PATH", "relative_store.sqlite3")

    assert local_kv.store_path() == local_kv._default_store_path().parent / "relative_store.sqlite3"


def test_wal_is_enabled(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    local_kv.ensure_store(path)

    connection = sqlite3.connect(str(path))
    try:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        connection.close()
    assert str(mode).lower() == "wal"


def test_writes_are_thread_safe(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    local_kv.ensure_store(path)

    def write_range(start):
        for index in range(start, start + 20):
            local_kv.put(path, "threads", str(index), {"index": index})

    threads = [threading.Thread(target=write_range, args=(offset * 20,)) for offset in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert local_kv.count(path, "threads") == 80


def test_kv_row_helpers(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    upsert_row(path, "observations", collection_key("2026-01-01", "AVAX"), {"symbol": "AVAX", "price": 1.0})
    upsert_row(path, "observations", collection_key("2026-01-02", "AVAX"), {"symbol": "AVAX", "price": 2.0})

    rows = load_rows(path, "observations")
    assert {row["price"] for row in rows} == {1.0, 2.0}
    latest = latest_rows(path, "observations", limit=1)
    assert latest[0]["price"] == 2.0


def test_observer_lock_releases_on_close(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    first = try_acquire_observer_lock(path, lock_id=7001)
    assert first is not None

    second = try_acquire_observer_lock(path, lock_id=7001)
    assert second is None

    first.close()
    third = try_acquire_observer_lock(path, lock_id=7001)
    assert third is not None
    third.close()


def test_observer_lock_stale_entry_is_replaced(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    key = "observer_lock_7002"
    local_kv.meta_put(path, key, {"hint": "crashed-process", "acquired_at": time.time() - 999, "lock_id": 7002})

    acquired = try_acquire_observer_lock(path, lock_id=7002, stale_seconds=60)
    assert acquired is not None
    acquired.close()
