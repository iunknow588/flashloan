from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

from core.db.storage_common import db_connection
from core.db.storage_cow_execution import (
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_EXECUTION_REVIEW_TABLE,
    COW_EXECUTION_SUBMISSION_TABLE,
    clear_cow_execution_attempts_sqlite,
    load_cow_execution_attempt_detail,
    load_recent_cow_execution_attempts,
    load_recent_cow_execution_attempts_sqlite_grouped,
    record_cow_execution_attempts,
)


def _observed_at(idx: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=idx)
    ).replace(microsecond=0).isoformat()


def _review_attempt(idx: int = 1) -> dict:
    return {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "pair": "TZ -> TB -> TA -> TZ",
        "state": "quote_required",
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "observed_at": _observed_at(idx),
    }


def _submission_attempt(state: str, idx: int) -> dict:
    return {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "pair": "ETH -> USDC -> ETH",
        "state": state,
        "execution_phase": state,
        "checks_passed": True,
        "can_submit_order": True,
        "tx_hash": "0x" + str(idx) * 64,
        "observed_at": _observed_at(idx),
    }


def _table_count(database_url: str, table: str) -> int:
    with db_connection(database_url) as connection:
        row = connection.execute(
            f"SELECT COUNT(*) AS c FROM {table}"
        ).fetchone()
    return int(row["c"] or 0)


def test_record_routes_reviews_and_submissions_to_separate_tables(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    ids = record_cow_execution_attempts(
        path,
        [
            _review_attempt(idx=1),
            _submission_attempt(COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS, idx=2),
            _submission_attempt(COW_ATTEMPT_CATEGORY_EXECUTION_FAILED, idx=3),
        ],
    )

    assert len(ids) == 3
    assert _table_count(path, COW_EXECUTION_SUBMISSION_TABLE) == 2
    assert _table_count(path, COW_EXECUTION_REVIEW_TABLE) == 1

    grouped = load_recent_cow_execution_attempts_sqlite_grouped(path, limit=10)
    assert len(grouped[COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE]) == 1
    assert len(grouped[COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS]) == 1
    assert len(grouped[COW_ATTEMPT_CATEGORY_EXECUTION_FAILED]) == 1
    assert load_cow_execution_attempt_detail(path, ids[0]) is not None
    assert load_cow_execution_attempt_detail(path, ids[1]) is not None


def test_legacy_review_rows_are_migrated_to_review_table(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    connection = sqlite3.connect(str(path))
    connection.execute(
        f"""
        CREATE TABLE {COW_EXECUTION_SUBMISSION_TABLE} (
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
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    payload = _review_attempt(idx=1)
    connection.execute(
        f"""
        INSERT INTO {COW_EXECUTION_SUBMISSION_TABLE} (
            id, attempt_key, network, chain_id, market_id, pair, tx_hash,
            created_at, category, payload_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "legacy-review-key",
            payload["network"],
            payload["chain_id"],
            payload["market_id"],
            payload["pair"],
            "",
            payload["observed_at"],
            COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
            json.dumps(payload, ensure_ascii=True, sort_keys=True),
            "",
        ),
    )
    connection.commit()
    connection.close()

    rows = load_recent_cow_execution_attempts(path, limit=10)

    assert len(rows) == 1
    assert rows[0]["review_category"] == COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    assert _table_count(path, COW_EXECUTION_SUBMISSION_TABLE) == 0
    assert _table_count(path, COW_EXECUTION_REVIEW_TABLE) == 1


def test_clear_by_record_kind_only_touches_its_table(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    record_cow_execution_attempts(
        path,
        [
            _review_attempt(idx=1),
            _submission_attempt(COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS, idx=2),
        ],
    )

    cleared = clear_cow_execution_attempts_sqlite(
        path,
        categories=[COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE],
    )

    assert cleared == 1
    assert _table_count(path, COW_EXECUTION_REVIEW_TABLE) == 0
    assert _table_count(path, COW_EXECUTION_SUBMISSION_TABLE) == 1
    rows = load_recent_cow_execution_attempts(path, limit=10)
    assert len(rows) == 1
    assert rows[0]["review_category"] == COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS

    assert clear_cow_execution_attempts_sqlite(path) == 1
    assert load_recent_cow_execution_attempts(path, limit=10) == []
