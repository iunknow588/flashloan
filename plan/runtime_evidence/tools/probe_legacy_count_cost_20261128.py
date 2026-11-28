#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure how expensive the legacy review count is on the live store.

`review_manager::count_legacy_review_rows` runs

    SELECT COUNT(*) FROM cow_execution_reviews WHERE network = ? AND chain_id = ?

The table holds 125k rows and ~18 GB of overflow pages (see
`measure_table_sizes_20261128_result.json`).  This probe answers two
questions **without writing to the store**:

1. what does the query planner do with that statement, and
2. how long the statement actually takes.

The count is run under a progress handler that aborts after a wall-clock
budget, so a scan that would take minutes is reported as "did not finish"
instead of hanging the investigation.

Usage:
    python probe_legacy_count_cost_20261128.py [--budget-seconds 5]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

COUNT_SQL = (
    "SELECT COUNT(*) FROM cow_execution_reviews "
    "WHERE network = ? AND chain_id = ?"
)


def timed_count(connection: sqlite3.Connection, network: str, chain_id: int, budget: float):
    """Run the count under a wall-clock budget; report what happened."""
    deadline = time.monotonic() + budget
    state = {"aborted": False, "steps": 0}

    def progress():
        state["steps"] += 1
        if time.monotonic() > deadline:
            state["aborted"] = True
            return 1  # non-zero aborts the statement
        return 0

    connection.set_progress_handler(progress, 10000)
    started = time.monotonic()
    error = None
    value = None
    try:
        value = connection.execute(COUNT_SQL, (network, chain_id)).fetchone()[0]
    except sqlite3.OperationalError as exc:
        error = str(exc)
    finally:
        connection.set_progress_handler(None, 0)
    elapsed = time.monotonic() - started
    return {
        "count": value,
        "elapsedSeconds": round(elapsed, 3),
        "abortedByBudget": state["aborted"],
        "progressSteps": state["steps"],
        "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--budget-seconds", type=float, default=5.0)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")

    plan = [
        dict(row)
        for row in connection.execute("EXPLAIN QUERY PLAN " + COUNT_SQL,
                                     (args.network, args.chain_id))
    ]
    indexes = [
        dict(row)
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'cow_execution_reviews'"
        )
    ]
    table = {
        "present": connection.execute(
            "SELECT (SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='cow_execution_reviews') AS present"
        ).fetchone()["present"]
    }
    result = timed_count(connection, args.network, args.chain_id, args.budget_seconds)

    report = {
        "tool": "probe_legacy_count_cost_20261128",
        "database": str(args.database),
        "statement": COUNT_SQL,
        "network": args.network,
        "chainId": args.chain_id,
        "tableExists": table["present"],
        "queryPlan": plan,
        "indexes": indexes,
        "measurement": result,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    Path(__file__).with_name("probe_legacy_count_cost_20261128_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
