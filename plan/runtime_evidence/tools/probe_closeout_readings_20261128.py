#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the live store (read only) and report the readings the open boxes ask for.

The plan keeps a box open until a **reading** satisfies it.  This probe
collects the readings that the still-open boxes name (coverage, generation,
review completeness, broadcast state), so a box can be closed on evidence
instead of on assumption.

Nothing here writes: the store is opened ``mode=ro`` and ``query_only`` is on.

Usage::

    python probe_closeout_readings_20261128.py [--db <path>] [--json <path>]
    python probe_closeout_readings_20261128.py --watch 2   # sample twice
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"


def open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def rows(connection: sqlite3.Connection, sql: str, params: tuple = ()):
    return [dict(r) for r in connection.execute(sql, params).fetchall()]


def scalar(connection: sqlite3.Connection, sql: str, params: tuple = ()):
    row = connection.execute(sql, params).fetchone()
    return None if row is None else row[0]


def sample(connection: sqlite3.Connection) -> dict:
    out: dict = {}

    # --- the closed loop catalog ---------------------------------------
    out["loopStatus"] = rows(
        connection,
        "SELECT status, COUNT(*) AS n FROM manager_v4_closed_loops GROUP BY status",
    )
    out["closedLoopLibrary"] = rows(
        connection,
        "SELECT * FROM manager_review_library_state WHERE scope_key = 'core4'",
    )
    out["generation"] = scalar(
        connection,
        "SELECT closed_loop_version FROM manager_review_library_state WHERE scope_key = 'core4'",
    )
    generation = out["generation"]

    # --- the reviews of that generation ---------------------------------
    out["reviewStatusByGeneration"] = rows(
        connection,
        "SELECT closed_loop_version, review_status, COUNT(*) AS n, "
        "SUM(CASE WHEN signed_pnl_raw IS NULL THEN 1 ELSE 0 END) AS null_pnl "
        "FROM manager_v4_reviews GROUP BY closed_loop_version, review_status "
        "ORDER BY closed_loop_version DESC, n DESC LIMIT 12",
    )
    out["reviewsInGeneration"] = scalar(
        connection,
        "SELECT COUNT(*) FROM manager_v4_reviews WHERE closed_loop_version = ?",
        (generation,),
    )
    out["reviewPnlInGeneration"] = rows(
        connection,
        "SELECT review_status, COUNT(*) AS n, "
        "SUM(CASE WHEN signed_pnl_raw IS NULL THEN 1 ELSE 0 END) AS null_pnl, "
        "SUM(CASE WHEN signed_pnl_raw > 0 THEN 1 ELSE 0 END) AS positive, "
        "SUM(CASE WHEN signed_pnl_raw < 0 THEN 1 ELSE 0 END) AS negative "
        "FROM manager_v4_reviews WHERE closed_loop_version = ? GROUP BY review_status",
        (generation,),
    )
    out["reviewFreshness"] = rows(
        connection,
        "SELECT MAX(reviewed_at) AS max_reviewed_at, MAX(updated_at) AS max_updated_at, "
        "MAX(created_at) AS max_created_at, COUNT(*) AS n FROM manager_v4_reviews",
    )

    # --- the resident worker state --------------------------------------
    out["runtimeState"] = rows(
        connection,
        "SELECT state, COUNT(*) AS n FROM v4_closed_loop_runtime_state GROUP BY state",
    )
    out["runtimeGeneration"] = rows(
        connection,
        "SELECT generation, COUNT(*) AS n FROM v4_closed_loop_runtime_state GROUP BY generation "
        "ORDER BY generation DESC LIMIT 8",
    )
    out["runtimeFreshness"] = rows(
        connection,
        "SELECT MAX(updated_at) AS max_updated_at, MAX(heartbeat_at) AS max_heartbeat_at, "
        "MAX(persisted_at) AS max_persisted_at, MAX(finished_at) AS max_finished_at "
        "FROM v4_closed_loop_runtime_state",
    )
    out["runtimeErrors"] = rows(
        connection,
        "SELECT last_error, COUNT(*) AS n FROM v4_closed_loop_runtime_state "
        "WHERE last_error IS NOT NULL AND last_error <> '' GROUP BY last_error "
        "ORDER BY n DESC LIMIT 8",
    )

    # --- coverage: active catalog  vs  registered worker state ---------
    coverage = rows(
        connection,
        "SELECT COUNT(*) AS active_loops, "
        "SUM(CASE WHEN s.loop_key IS NOT NULL THEN 1 ELSE 0 END) AS with_state "
        "FROM manager_v4_closed_loops l "
        "LEFT JOIN (SELECT DISTINCT loop_key FROM v4_closed_loop_runtime_state) s "
        "  ON s.loop_key = l.loop_key WHERE l.status = 'active'",
    )
    out["workerCoverage"] = coverage
    out["staleStateRows"] = scalar(
        connection,
        "SELECT COUNT(*) FROM v4_closed_loop_runtime_state s "
        "WHERE NOT EXISTS (SELECT 1 FROM manager_v4_closed_loops l "
        "WHERE l.loop_key = s.loop_key AND l.status = 'active')",
    )
    out["usdgCoverage"] = rows(
        connection,
        "SELECT COUNT(*) AS usdg_loops, "
        "SUM(CASE WHEN s.loop_key IS NOT NULL THEN 1 ELSE 0 END) AS with_state "
        "FROM manager_v4_closed_loops l "
        "LEFT JOIN (SELECT DISTINCT loop_key FROM v4_closed_loop_runtime_state) s "
        "  ON s.loop_key = l.loop_key "
        "WHERE l.status = 'active' AND l.path_addresses_json LIKE ?",
        ("%" + USDG + "%",),
    )

    # --- broadcast / execution ------------------------------------------
    out["broadcastRequests"] = scalar(
        connection, "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"
    )
    out["executionAttempts"] = scalar(
        connection, "SELECT COUNT(*) FROM manager_cow_execution_attempts"
    )
    for name in ("manager_broadcast_library_state", "manager_v4_review_diagnostics"):
        present = scalar(
            connection,
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        )
        out[name] = (
            scalar(connection, "SELECT COUNT(*) FROM %s" % name) if present else None
        )
    out["errorIsolation"] = rows(
        connection,
        "SELECT disposition, COUNT(*) AS n FROM v4_closed_loop_error_isolation GROUP BY disposition",
    )
    out["sampledAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--json", default="")
    parser.add_argument("--watch", type=int, default=0,
                        help="sample N times, 60s apart")
    args = parser.parse_args()

    db = Path(args.db)
    samples = []
    connection = open_readonly(db)
    try:
        for index in range(max(1, args.watch)):
            samples.append(sample(connection))
            if index + 1 < max(1, args.watch):
                time.sleep(60)
    finally:
        connection.close()

    report = {"db": str(db), "samples": samples}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + chr(10),
            encoding="utf-8",
            newline=chr(10),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
