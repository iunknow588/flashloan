import sqlite3

from core.db import storage_common
from core.local_kv import get, put


def test_db_connection_yields_sqlite_connection(tmp_path):
    kv_path = str(tmp_path / "conn.sqlite3")

    with storage_common.db_connection(kv_path) as connection:
        assert isinstance(connection, sqlite3.Connection)
        row = connection.execute("SELECT 1 AS value").fetchone()
        assert row["value"] == 1

    assert kv_path and True


def test_local_kv_read_write_roundtrip(tmp_path):
    kv_path = str(tmp_path / "kv.sqlite3")

    put(kv_path, "parameters", "ns|key", {"value": 42})

    assert get(kv_path, "parameters", "ns|key") == {"value": 42}
