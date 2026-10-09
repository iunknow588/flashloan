"""Read/write helpers for the independent business SQLite tables.

Writes always live here or in the page builders; the Rust core only reads.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from core.local_kv import ensure_store, store_path


def _connect(database_url: str) -> sqlite3.Connection:
    path = store_path(database_url)
    ensure_store(path)
    connection = sqlite3.connect(str(path), timeout=8.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def execute(database_url: str, sql: str, params: Iterable[Any] = ()) -> int:
    with _connect(database_url) as connection:
        cursor = connection.execute(sql, tuple(params))
        connection.commit()
        return int(cursor.rowcount or 0)


def executemany(database_url: str, sql: str, rows: Iterable[tuple[Any, ...]]) -> int:
    with _connect(database_url) as connection:
        cursor = connection.executemany(sql, list(rows))
        connection.commit()
        return int(cursor.rowcount or 0)


def replace_rows(database_url: str, sql: str, rows: Iterable[tuple[Any, ...]]) -> int:
    return executemany(database_url, sql, rows)


def query(database_url: str, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with _connect(database_url) as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def query_one(database_url: str, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    rows = query(database_url, sql, params)
    return rows[0] if rows else None


def transaction(database_url: str, statements: Iterable[tuple[str, tuple[Any, ...]]]) -> int:
    count = 0
    with _connect(database_url) as connection:
        for sql, params in statements:
            cursor = connection.execute(sql, params)
            count += int(cursor.rowcount or 0)
        connection.commit()
    return count


def delete_rows(database_url: str, table: str, conditions: str, params: Iterable[Any]) -> int:
    """Delete rows by a trusted internal condition fragment."""

    allowed_tables = {
        "dex_candidate_anchor_tokens",
        "dex_candidate_pool_pairs",
        "dex_candidate_pools",
        "dex_candidate_pool_recommendations",
        "market_observation_yz_pairs",
        "market_observation_intermediate_pools",
        "market_observation_liquidity_snapshots",
        "v4_closed_loops",
        "v4_closed_loop_hops",
        "v4_closed_loop_optimizations",
    }
    if table not in allowed_tables:
        raise ValueError(f"unsupported delete table: {table}")
    return execute(database_url, f"DELETE FROM {table} WHERE {conditions}", params)


__all__ = [
    "execute",
    "executemany",
    "delete_rows",
    "query",
    "query_one",
    "replace_rows",
    "transaction",
]
