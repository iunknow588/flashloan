"""Read-only snapshot of the review / broadcast write path.

The production store is ~17 GB, so the database is opened through a URI
with ``mode=ro`` and ``PRAGMA query_only=ON``.  Nothing here writes.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def snapshot(database_path: str) -> dict:
    uri = f"file:{Path(database_path).as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.execute("PRAGMA query_only=ON")
    try:
        payload: dict = {}
        for label, sql in (
            (
                "reviews_by_status",
                "SELECT review_status, COUNT(*) FROM manager_v4_reviews GROUP BY review_status",
            ),
            (
                "reviews_total",
                "SELECT COUNT(*) FROM manager_v4_reviews",
            ),
            (
                "reviews_latest_reviewed_at",
                "SELECT MAX(reviewed_at) FROM manager_v4_reviews",
            ),
            (
                "reviews_data_incomplete_reasons",
                "SELECT COALESCE(review_reason,''), COUNT(*) FROM manager_v4_reviews "
                "WHERE review_status='data_incomplete' GROUP BY review_reason ORDER BY 2 DESC LIMIT 12",
            ),
            (
                "broadcast_requests",
                "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests",
            ),
            (
                "write_requests_review_publish",
                "SELECT COUNT(*) FROM manager_write_requests WHERE operation='review_publish'",
            ),
            (
                "write_requests_last_review_publish",
                "SELECT request_id, created_at FROM manager_write_requests "
                "WHERE operation='review_publish' ORDER BY created_at DESC LIMIT 3",
            ),
            (
                "write_requests_review_publish_recent",
                "SELECT request_id, idempotency_key, created_at FROM manager_write_requests "
                "WHERE operation='review_publish' AND created_at >= '2026-11-26' "
                "ORDER BY created_at DESC LIMIT 5",
            ),
            (
                "reviews_stale_marked",
                "SELECT COUNT(*) FROM manager_v4_reviews "
                "WHERE review_reason LIKE 'not_reviewed_since:%'",
            ),
            (
                "reviews_never_reviewed",
                "SELECT COUNT(*) FROM manager_v4_reviews "
                "WHERE review_reason = 'not_reviewed_at_all'",
            ),
            (
                "reviews_incomplete_other_reasons",
                "SELECT review_reason, COUNT(*) FROM manager_v4_reviews "
                "WHERE review_status = 'data_incomplete' "
                "AND review_reason NOT LIKE 'not_reviewed_since:%' "
                "GROUP BY review_reason ORDER BY 2 DESC LIMIT 12",
            ),
            (
                "reviews_not_executable_reasons",
                "SELECT review_reason, COUNT(*) FROM manager_v4_reviews "
                "WHERE review_status = 'not_executable' "
                "GROUP BY review_reason ORDER BY 2 DESC LIMIT 12",
            ),
            (
                "reviews_reviewed_at_span",
                "SELECT MIN(reviewed_at), MAX(reviewed_at) FROM manager_v4_reviews",
            ),
            (
                "review_diagnostics_total",
                "SELECT COUNT(*) FROM manager_v4_review_diagnostics",
            ),
            (
                "review_diagnostics_by_type",
                "SELECT diagnostic_type, diagnostic_code, COUNT(*) "
                "FROM manager_v4_review_diagnostics GROUP BY diagnostic_type, diagnostic_code "
                "ORDER BY 3 DESC LIMIT 12",
            ),
            (
                "stale_window_in_use",
                "SELECT COUNT(*) FROM manager_v4_review_diagnostics "
                "WHERE diagnostic_code = 'review_stale'",
            ),
            (
                "migration_ledger",
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('schema_migrations', 'manager_schema_migrations')",
            ),
            (
                "migration_water",
                "SELECT * FROM schema_migrations ORDER BY rowid DESC LIMIT 3",
            ),
            (
                "broadcast_library_state",
                "SELECT COUNT(*) FROM manager_broadcast_library_state",
            ),
            (
                "broadcast_attempts",
                "SELECT COUNT(*) FROM manager_broadcast_attempts",
            ),
        ):
            try:
                payload[label] = connection.execute(sql).fetchall()
            except sqlite3.Error as exc:
                payload[label] = f"error: {exc}"
        return payload
    finally:
        connection.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "flashloan/db/local_store_4663.sqlite3"
    print(json.dumps(snapshot(target), ensure_ascii=False, indent=2, default=str))
