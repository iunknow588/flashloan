#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Do the reviews without a measured PnL use the pools the bridge could not resolve?

The bridge could only resolve a subset of the closed loops before the
manager mirrors were read as well.  A loop whose hop cannot be resolved is
skipped, so it never gets priced and its review carries no signed PnL.  This
probe asks whether the two sets are the same rows -- i.e. whether the
missing PnL was caused by the resolution gap or by the chain.

The store is read in read-only mode and never written.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plan" / "runtime_evidence" / "tools"))

import probe_bridge_coverage_20261125 as bridge  # noqa: E402


DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def main() -> int:
    database_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True,
    )
    connection.execute("PRAGMA query_only = ON")
    try:
        _, live_pool_key = bridge._build(connection, bridge.LIVE_QUERIES)
        _, manager_pool_key = bridge._build(connection, bridge.MANAGER_QUERIES)
        combined = dict(live_pool_key)
        combined.update(manager_pool_key)

        published = set()
        for row in connection.execute(bridge.LOOPS_QUERY):
            for pool_id in json.loads(row[1] or "[]"):
                published.add(str(pool_id).lower())

        live_unresolvable = sorted(
            pool_id for pool_id in published if pool_id not in live_pool_key
        )

        reviews = connection.execute(
            "SELECT loop_key, review_status, review_reason, pool_ids_json"
            " FROM manager_v4_reviews"
        ).fetchall()
    finally:
        connection.close()

    live_only = set(live_unresolvable)
    report = {
        "database": str(database_path),
        "mode": "ro",
        "publishedPools": len(published),
        "liveUnresolvablePools": len(live_only),
        "liveUnresolvablePoolIds": sorted(live_only),
        "statusCounts": {},
        "statusTouchingUnresolvable": {},
        "detail": [],
    }
    for loop_key, status, reason, pool_ids_json in reviews:
        report["statusCounts"][status] = report["statusCounts"].get(status, 0) + 1
        pool_ids = {str(value).lower() for value in json.loads(pool_ids_json or "[]")}
        hit = sorted(pool_ids & live_only)
        report["statusTouchingUnresolvable"].setdefault(status, 0)
        if hit:
            report["statusTouchingUnresolvable"][status] += 1
        report["detail"].append({
            "loopKey": loop_key,
            "reviewStatus": status,
            "reviewReason": reason,
            "touchesLiveUnresolvable": bool(hit),
            "unresolvablePoolIds": hit,
        })

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
