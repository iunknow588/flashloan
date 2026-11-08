"""Read/write helpers for the independent business SQLite tables.

Writes always live here or in the page builders; the Rust core only reads.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Iterable

from core.db.database import Database


_MAX_WRITE_ATTEMPTS = 5
_WRITE_RETRY_DELAY_SECONDS = 0.3


def _connect(database_url: str) -> sqlite3.Connection:
    db = Database.from_url(database_url)
    db.ensure()
    return db.connect()


def _with_busy_retry(operation):
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(_MAX_WRITE_ATTEMPTS):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            message = str(exc or "").lower()
            if "locked" not in message and "busy" not in message:
                raise
            last_error = exc
            time.sleep(_WRITE_RETRY_DELAY_SECONDS * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("sqlite write retry exhausted without an error")


def execute(database_url: str, sql: str, params: Iterable[Any] = ()) -> int:
    def _run() -> int:
        with _connect(database_url) as connection:
            cursor = connection.execute(sql, tuple(params))
            connection.commit()
            return int(cursor.rowcount or 0)

    return _with_busy_retry(_run)


def executemany(database_url: str, sql: str, rows: Iterable[tuple[Any, ...]]) -> int:
    def _run() -> int:
        with _connect(database_url) as connection:
            cursor = connection.executemany(sql, list(rows))
            connection.commit()
            return int(cursor.rowcount or 0)

    return _with_busy_retry(_run)


def replace_rows(database_url: str, sql: str, rows: Iterable[tuple[Any, ...]]) -> int:
    return executemany(database_url, sql, rows)


def query(database_url: str, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with _connect(database_url) as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def query_read_only(
    database_url: str,
    sql: str,
    params: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Run a strict read-only SQL query without enabling WAL or writing meta.

    This is intentionally separate from ``query`` so planning tools can inspect
    a live runtime SQLite file without creating WAL/SHM files or touching the
    database header.
    """
    db = Database.from_url(database_url)
    with db.connect_read_only() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def query_one(database_url: str, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    rows = query(database_url, sql, params)
    return rows[0] if rows else None


def transaction(database_url: str, statements: Iterable[tuple[str, tuple[Any, ...]]]) -> int:
    def _run() -> int:
        count = 0
        with _connect(database_url) as connection:
            for sql, params in statements:
                cursor = connection.execute(sql, params)
                count += int(cursor.rowcount or 0)
            connection.commit()
        return count

    return _with_busy_retry(_run)


def delete_rows(database_url: str, table: str, conditions: str, params: Iterable[Any]) -> int:
    """Delete rows by a trusted internal condition fragment."""

    allowed_tables = {
        "dex_candidate_anchor_tokens",
        "dex_candidate_pool_pairs",
        "dex_candidate_pools",
        "dex_candidate_pool_recommendations",
        "dex_candidate_abnormal_anchor_tokens",
        "dex_candidate_abnormal_pool_pairs",
        "dex_candidate_abnormal_pools",
        "market_observation_yz_pairs",
        "market_observation_abnormal_yz_pairs",
        "market_observation_intermediate_pools",
        "market_observation_abnormal_intermediate_pools",
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
    "query_read_only",
    "query_one",
    "replace_rows",
    "transaction",
]
