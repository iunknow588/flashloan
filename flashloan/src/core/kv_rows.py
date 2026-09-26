"""Row-oriented helpers over the local SQLite KV store."""

from __future__ import annotations

from typing import Any, Iterable

from core.local_kv import clear_collection, count as kv_count, delete, get, put, put_many, scan


def collection_key(*parts: Any) -> str:
    """Build a deterministic key from string/int parts."""

    return "|".join(str(part) for part in parts)


def upsert_row(
    path: str,
    collection: str,
    key: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    put(path, collection, key, row)
    return dict(row)


def upsert_rows(
    path: str,
    collection: str,
    rows: Iterable[tuple[str, dict[str, Any]]],
) -> int:
    return put_many(path, collection, ((key, row) for key, row in rows))


def load_rows(path: str, collection: str, *, prefix: str | None = None) -> list[dict[str, Any]]:
    return [item["value"] for item in scan(path, collection, prefix=prefix)]


def latest_rows(
    path: str,
    collection: str,
    *,
    limit: int = 1,
    key_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Return rows ordered by composite key descending."""

    return [
        item["value"]
        for item in scan(path, collection, prefix=key_prefix, descending=True, limit=limit)
    ]


def delete_key(path: str, collection: str, key: str) -> bool:
    return delete(path, collection, key)


def delete_collection(path: str, collection: str) -> int:
    return clear_collection(path, collection)


def row_count(path: str, collection: str) -> int:
    return kv_count(path, collection)


count = kv_count


def get_row(path: str, collection: str, key: str) -> dict[str, Any] | None:
    value = get(path, collection, key)
    return value if isinstance(value, dict) else None


__all__ = [
    "collection_key",
    "count",
    "delete_collection",
    "delete_key",
    "get_row",
    "latest_rows",
    "load_rows",
    "row_count",
    "upsert_row",
    "upsert_rows",
]
