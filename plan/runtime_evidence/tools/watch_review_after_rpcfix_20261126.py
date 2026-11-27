#!/usr/bin/env python3
"""Watch the review table until the pricing path produces something new.

The watcher polls the live store **read-only** and prints one JSON line per
poll, so the run can be stopped as soon as the review status moves away from
`data_incomplete` or the rows stop being rewritten.

Usage:

    python plan/runtime_evidence/tools/watch_review_after_rpcfix_20261126.py [minutes]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STORE = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def snapshot() -> dict[str, object]:
    uri = "file:" + STORE.as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        counts = dict(
            connection.execute(
                "SELECT review_status, COUNT(*) FROM manager_v4_reviews"
                " GROUP BY review_status"
            ).fetchall()
        )
        updated = connection.execute(
            "SELECT MAX(updated_at) FROM manager_v4_reviews"
        ).fetchone()[0]
        reviewed = connection.execute(
            "SELECT MAX(reviewed_at) FROM manager_v4_reviews"
        ).fetchone()[0]
        version = None
        try:
            row = connection.execute(
                "SELECT review_version FROM manager_review_library_state"
            ).fetchone()
            version = row[0] if row else None
        except sqlite3.Error:
            version = None
        diagnostics = dict(
            connection.execute(
                "SELECT diagnostic_code, COUNT(*) FROM manager_v4_review_diagnostics"
                " GROUP BY diagnostic_code"
            ).fetchall()
        )
        return {
            "at": time.strftime("%H:%M:%S"),
            "status_counts": counts,
            "updated_at_max": updated,
            "reviewed_at_max": reviewed,
            "review_version": version,
            "diagnostics": diagnostics,
        }
    finally:
        connection.close()


def main() -> None:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    deadline = time.time() + minutes * 60
    first = snapshot()
    print(json.dumps(first, ensure_ascii=False), flush=True)
    while time.time() < deadline:
        time.sleep(15)
        current = snapshot()
        print(json.dumps(current, ensure_ascii=False), flush=True)
        if current["status_counts"] != {"data_incomplete": 174}:
            print("MOVED", flush=True)
            return
        if current["updated_at_max"] != first["updated_at_max"]:
            print("REWRITTEN", flush=True)
            return


if __name__ == "__main__":
    main()
