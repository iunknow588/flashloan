#!/usr/bin/env python3
"""Watch the review pipeline until a generation is published.

The watcher polls the live store **read-only** and prints one JSON line per
poll: the review status counts, the newest review timestamp, and the
reviewer's own error text when there is one.  It stops as soon as the
review status leaves `data_incomplete`.

Usage:

    python plan/runtime_evidence/tools/watch_review_pipeline_20261126.py [minutes]
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
        codes = dict(
            connection.execute(
                "SELECT diagnostic_code, COUNT(*)"
                " FROM manager_v4_review_diagnostics GROUP BY diagnostic_code"
            ).fetchall()
        )
        sample = connection.execute(
            "SELECT static_preview_json FROM manager_v4_reviews"
            " ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        reason = None
        if sample and sample[0]:
            try:
                preview = json.loads(sample[0])
                reason = (
                    (preview.get("bestAmountCalculation") or {}).get(
                        "bestAmountMissingReason"
                    )
                )
            except (TypeError, ValueError):
                reason = None
        return {
            "at": time.strftime("%H:%M:%S"),
            "status_counts": counts,
            "updated_at_max": updated,
            "diagnostics": codes,
            "latest_missing_reason": reason,
        }
    finally:
        connection.close()


def main() -> None:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    deadline = time.time() + minutes * 60
    print(json.dumps(snapshot(), ensure_ascii=False), flush=True)
    while time.time() < deadline:
        time.sleep(20)
        current = snapshot()
        print(json.dumps(current, ensure_ascii=False), flush=True)
        if set(current["status_counts"]) != {"data_incomplete"}:
            print("PUBLISHED", flush=True)
            return


if __name__ == "__main__":
    main()
