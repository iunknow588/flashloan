"""Minimal local SQLite-backed KV store for the application.

The store keeps JSON values under ``(collection, key)`` in a single SQLite
database.  It replaces the previous Postgres-backed storage layer while
preserving the public storage helper signatures.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from core.runtime_paths import resolve_runtime_dir

_WRITE_LOCK = threading.RLock()
_SCHEMA_VERSION = 1


def _default_store_path() -> Path:
    runtime_dir = resolve_runtime_dir()
    return runtime_dir / "local_store.sqlite3"


def store_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the local KV database path."""

    if path:
        resolved = Path(path)
        return resolved if resolved.is_absolute() else _default_store_path().parent / resolved
    env_path = os.getenv("FLASHLOAN_KV_PATH", "").strip()
    if env_path:
        resolved = Path(env_path).expanduser()
        return resolved if resolved.is_absolute() else _default_store_path().parent / resolved
    return _default_store_path()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    _ensure_schema(connection)
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_entries (
            collection TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (collection, key)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_kv_entries_collection_key "
        "ON kv_entries(collection, key)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_meta (
            meta_key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO kv_meta (meta_key, value_json)
        VALUES ('schema_version', ?)
        """,
        (json.dumps(_SCHEMA_VERSION),),
    )
    connection.commit()


def ensure_store(path: str | os.PathLike[str] | None = None) -> Path:
    """Create the KV database file and parent directory if needed."""

    resolved = store_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with _connect(resolved):
        pass
    return resolved


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _decode(value_json: str | None) -> Any:
    if value_json is None:
        return None
    try:
        return json.loads(value_json)
    except (TypeError, json.JSONDecodeError):
        return None


def put(
    path: str | os.PathLike[str] | None,
    collection: str,
    key: str,
    value: Any,
    *,
    updated_at: str | None = None,
) -> str:
    """Store a JSON value and return the stored ``updated_at``."""

    resolved = store_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    encoded = _encode(value)
    timestamp = updated_at or _utc_now()
    with _WRITE_LOCK:
        with _connect(resolved) as connection:
            connection.execute(
                """
                INSERT INTO kv_entries (collection, key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(collection, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (str(collection), str(key), encoded, timestamp),
            )
            connection.commit()
    return timestamp


def put_many(
    path: str | os.PathLike[str] | None,
    collection: str,
    items: Iterator[tuple[str, Any]],
    *,
    updated_at: str | None = None,
) -> int:
    """Store many ``(key, value)`` entries in one transaction."""

    resolved = store_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    timestamp = updated_at or _utc_now()
    rows = [(str(collection), str(key), _encode(value), timestamp) for key, value in items]
    if not rows:
        return 0
    with _WRITE_LOCK:
        with _connect(resolved) as connection:
            connection.executemany(
                """
                INSERT INTO kv_entries (collection, key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(collection, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            connection.commit()
    return len(rows)


def get(
    path: str | os.PathLike[str] | None,
    collection: str,
    key: str,
) -> Any:
    """Return decoded JSON value for one key, or ``None``."""

    with _connect(store_path(path)) as connection:
        row = connection.execute(
            "SELECT value_json FROM kv_entries WHERE collection = ? AND key = ?",
            (str(collection), str(key)),
        ).fetchone()
    return _decode(row["value_json"] if row else None)


def delete(
    path: str | os.PathLike[str] | None,
    collection: str,
    key: str,
) -> bool:
    """Delete one key; return whether a row was removed."""

    with _WRITE_LOCK:
        with _connect(store_path(path)) as connection:
            cursor = connection.execute(
                "DELETE FROM kv_entries WHERE collection = ? AND key = ?",
                (str(collection), str(key)),
            )
            connection.commit()
            return cursor.rowcount > 0


def scan(
    path: str | os.PathLike[str] | None,
    collection: str,
    *,
    prefix: str | None = None,
    limit: int | None = None,
    descending: bool = False,
) -> list[dict[str, Any]]:
    """Return rows in a collection as dicts with decoded ``value``."""

    sql = "SELECT key, value_json, updated_at FROM kv_entries WHERE collection = ?"
    params: list[Any] = [str(collection)]
    if prefix:
        sql += " AND key LIKE ?"
        params.append(f"{prefix}%")
    sql += " ORDER BY key"
    if descending:
        sql += " DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with _connect(store_path(path)) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [
        {
            "key": str(row["key"]),
            "value": _decode(row["value_json"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]


def count(path: str | os.PathLike[str] | None, collection: str) -> int:
    """Return the number of entries in a collection."""

    with _connect(store_path(path)) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM kv_entries WHERE collection = ?",
            (str(collection),),
        ).fetchone()
    return int(row["n"] or 0) if row else 0


def clear_collection(path: str | os.PathLike[str] | None, collection: str) -> int:
    """Delete every entry in a collection."""

    with _WRITE_LOCK:
        with _connect(store_path(path)) as connection:
            cursor = connection.execute(
                "DELETE FROM kv_entries WHERE collection = ?",
                (str(collection),),
            )
            connection.commit()
            return cursor.rowcount


def meta_get(path: str | os.PathLike[str] | None, key: str) -> Any:
    with _connect(store_path(path)) as connection:
        row = connection.execute(
            "SELECT value_json FROM kv_meta WHERE meta_key = ?",
            (str(key),),
        ).fetchone()
    return _decode(row["value_json"] if row else None)


def meta_put(path: str | os.PathLike[str] | None, key: str, value: Any) -> None:
    with _WRITE_LOCK:
        with _connect(store_path(path)) as connection:
            connection.execute(
                """
                INSERT INTO kv_meta (meta_key, value_json)
                VALUES (?, ?)
                ON CONFLICT(meta_key) DO UPDATE SET value_json = excluded.value_json
                """,
                (str(key), _encode(value)),
            )
            connection.commit()


def meta_insert_if_absent(
    path: str | os.PathLike[str] | None,
    key: str,
    value: Any,
) -> bool:
    """Insert a meta key only if it is absent; return whether it was inserted."""

    with _WRITE_LOCK:
        with _connect(store_path(path)) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO kv_meta (meta_key, value_json)
                VALUES (?, ?)
                """,
                (str(key), _encode(value)),
            )
            connection.commit()
            return cursor.rowcount > 0


def meta_delete_if_matches(
    path: str | os.PathLike[str] | None,
    key: str,
    value: Any,
) -> bool:
    """Delete a meta key only when its current value matches ``value``."""

    with _WRITE_LOCK:
        with _connect(store_path(path)) as connection:
            cursor = connection.execute(
                "DELETE FROM kv_meta WHERE meta_key = ? AND value_json = ?",
                (str(key), _encode(value)),
            )
            connection.commit()
            return cursor.rowcount > 0


__all__ = [
    "clear_collection",
    "count",
    "delete",
    "ensure_store",
    "get",
    "meta_delete_if_matches",
    "meta_get",
    "meta_insert_if_absent",
    "meta_put",
    "put",
    "put_many",
    "scan",
    "store_path",
]
