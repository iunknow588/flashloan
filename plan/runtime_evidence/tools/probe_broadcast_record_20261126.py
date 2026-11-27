#!/usr/bin/env python3
"""Read the live store and report whether a broadcast was ever recorded.

The probe opens the store **read-only** (`mode=ro` plus
`PRAGMA query_only = ON`) and never writes: the question it answers is
"did the deployment ever record a broadcast?", and answering that
question must not change the thing being measured.

Usage:

    python plan/runtime_evidence/tools/probe_broadcast_record_20261126.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STORE = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

TABLES = (
    "manager_cow_execution_attempts",
    "manager_broadcast_attempt_diagnostics",
    "manager_broadcast_library_state",
    "manager_v4_reviews",
    "manager_v4_review_broadcast_requests",
)


def main() -> None:
    uri = "file:" + STORE.as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    report: dict[str, object] = {"store": STORE.as_posix()}
    for table in TABLES:
        try:
            report[table] = connection.execute(
                "SELECT COUNT(*) FROM %s" % table
            ).fetchone()[0]
        except sqlite3.Error as error:
            report[table] = "ERR %s" % error
    try:
        report["review_status_counts"] = connection.execute(
            "SELECT review_status, COUNT(*) FROM manager_v4_reviews"
            " GROUP BY review_status ORDER BY review_status"
        ).fetchall()
    except sqlite3.Error as error:
        report["review_status_counts"] = "ERR %s" % error
    try:
        report["migrations"] = [
            row[0]
            for row in connection.execute(
                "SELECT migration_id FROM schema_migrations ORDER BY migration_id"
            )
        ][-4:]
    except sqlite3.Error as error:
        report["migrations"] = "ERR %s" % error
    print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
