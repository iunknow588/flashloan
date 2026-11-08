from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from core.kv_rows import collection_key
from core.local_kv import ensure_store
from core.db.storage_common import db_connection
from core.db.cow_execution_mapping import (
    SELECTED_PROFIT_CAMEL_KEYS,
    SELECTED_PROFIT_SNAKE_KEYS,
    selected_profit_mapping,
)
from core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row
from core.db.storage_cow_policy import (
    COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
    _category_within_retention,
    _decorate_attempt_review,
    _parse_datetime,
    cow_execution_attempt_category,
)

DEFAULT_COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_RETENTION_LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE = "not_executable"
COW_ATTEMPT_CATEGORY_EXECUTION_FAILED = "execution_failed"
COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS = "execution_success"
_COW_ATTEMPT_TABLE = "cow_execution_attempts"
_COW_REVIEW_TABLE = "cow_execution_reviews"
_COW_TABLES = (_COW_ATTEMPT_TABLE, _COW_REVIEW_TABLE)
COW_EXECUTION_SUBMISSION_TABLE = _COW_ATTEMPT_TABLE
COW_EXECUTION_REVIEW_TABLE = _COW_REVIEW_TABLE
_SELECTED_PROFIT_COLUMNS = (
    ("selected_profit_raw", "TEXT"),
    ("selected_profit_amount", "REAL"),
    ("selected_profit_amount_symbol", "TEXT"),
    ("selected_profit_source", "TEXT"),
    ("selected_profit_missing_reason", "TEXT"),
)
_COW_TABLE_ENSURED: set[str] = set()
_COW_TABLE_ENSURED_LOCK = threading.Lock()


def ensure_cow_execution_attempts_table(database_url: str) -> None:
    resolved_key = str(Path(database_url).resolve())
    if resolved_key in _COW_TABLE_ENSURED:
        return
    with _COW_TABLE_ENSURED_LOCK:
        if resolved_key in _COW_TABLE_ENSURED:
            return
        _ensure_cow_execution_attempts_table_unlocked(database_url)
        _COW_TABLE_ENSURED.add(resolved_key)


def _ensure_cow_execution_attempts_table_unlocked(database_url: str) -> None:
    ensure_store(database_url)
    with db_connection(database_url) as connection:
        for table in _COW_TABLES:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_key TEXT NOT NULL UNIQUE,
                    network TEXT NOT NULL DEFAULT '',
                    chain_id INTEGER NOT NULL DEFAULT 0,
                    market_id TEXT NOT NULL DEFAULT '',
                    pair TEXT NOT NULL DEFAULT '',
                    tx_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_scope_created "
                f"ON {table}(network, chain_id, category, created_at DESC)"
            )
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_market_created "
                f"ON {table}(market_id, created_at DESC)"
            )
            columns = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "summary_json" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN summary_json TEXT NOT NULL DEFAULT ''"
                )
            for column, column_type in _SELECTED_PROFIT_COLUMNS:
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
                    )
        _migrate_review_rows_to_review_table(connection)


def _migrate_review_rows_to_review_table(connection) -> None:
    """Move legacy review-class rows out of the submission table.

    ``cow_execution_attempts`` now stores only execution/submission records;
    ``not_executable`` review records live in ``cow_execution_reviews``.
    """
    rows = connection.execute(
        f"""
        SELECT id, attempt_key, network, chain_id, market_id, pair, tx_hash,
               created_at, category, payload_json, summary_json, updated_at
        FROM {_COW_ATTEMPT_TABLE}
        WHERE category = ?
        ORDER BY id
        """,
        (COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,),
    ).fetchall()
    for row in rows:
        key = str(row["attempt_key"] or "")
        if not key:
            continue
        existing = connection.execute(
            f"SELECT id FROM {_COW_REVIEW_TABLE} WHERE attempt_key = ?",
            (key,),
        ).fetchone()
        values = (
            str(row["network"] or ""),
            int(row["chain_id"] or 0),
            str(row["market_id"] or ""),
            str(row["pair"] or ""),
            str(row["tx_hash"] or ""),
            str(row["created_at"] or ""),
            str(row["category"] or ""),
            str(row["payload_json"] or "{}"),
            str(row["summary_json"] or ""),
            str(row["updated_at"] or ""),
        )
        if existing is None:
            connection.execute(
                f"""
                INSERT INTO {_COW_REVIEW_TABLE} (
                    id, attempt_key, network, chain_id, market_id, pair, tx_hash,
                    created_at, category, payload_json, summary_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row["id"]),
                    key,
                    *values,
                ),
            )
        else:
            connection.execute(
                f"""
                UPDATE {_COW_REVIEW_TABLE}
                SET network = ?, chain_id = ?, market_id = ?, pair = ?, tx_hash = ?,
                    created_at = ?, category = ?, payload_json = ?, summary_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (*values, int(existing["id"])),
            )
        connection.execute(
            f"DELETE FROM {_COW_ATTEMPT_TABLE} WHERE id = ? AND category = ?",
            (int(row["id"]), COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE),
        )


def _cow_table_for_category(category: str | None) -> str:
    if str(category or "").strip().lower() == COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE:
        return _COW_REVIEW_TABLE
    return _COW_ATTEMPT_TABLE


def _cow_tables_for_categories(categories: list[str] | None) -> list[str]:
    if not categories:
        return list(_COW_TABLES)
    return sorted({_cow_table_for_category(item) for item in categories if str(item or "").strip()})
