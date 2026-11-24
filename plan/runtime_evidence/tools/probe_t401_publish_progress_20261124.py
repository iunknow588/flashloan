#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the current state of the mainnet review publication (read-only).

The ``T-复.01`` evidence needs the review generation the Rust production
publisher writes.  This probe reports whether that generation has landed,
so the wait is observed rather than assumed.

Usage:
    python probe_t401_publish_progress_20261124.py <store.db>
"""

import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663


def main():
    store_path = sys.argv[1]
    connection = sqlite3.connect(
        "file:" + os.path.abspath(store_path).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row

    state = connection.execute(
        "SELECT * FROM manager_review_library_state WHERE network=? AND chain_id=?",
        (NETWORK, CHAIN_ID),
    ).fetchone()
    upstream = connection.execute(
        "SELECT * FROM manager_v4_closed_loop_library_state WHERE network=? AND chain_id=?",
        (NETWORK, CHAIN_ID),
    ).fetchone()
    counts = connection.execute(
        "SELECT review_status, COUNT(*) n FROM manager_v4_reviews"
        " WHERE network=? AND chain_id=? GROUP BY 1",
        (NETWORK, CHAIN_ID),
    ).fetchall()
    versions = connection.execute(
        "SELECT closed_loop_version, COUNT(*) n FROM manager_v4_reviews"
        " WHERE network=? AND chain_id=? GROUP BY 1",
        (NETWORK, CHAIN_ID),
    ).fetchall()
    total = connection.execute(
        "SELECT COUNT(*) FROM manager_v4_reviews WHERE network=? AND chain_id=?",
        (NETWORK, CHAIN_ID),
    ).fetchone()[0]
    reviewed_at = connection.execute(
        "SELECT MIN(reviewed_at) lo, MAX(reviewed_at) hi FROM manager_v4_reviews"
        " WHERE network=? AND chain_id=?",
        (NETWORK, CHAIN_ID),
    ).fetchone()
    requests = connection.execute(
        "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"
        " WHERE network=? AND chain_id=?",
        (NETWORK, CHAIN_ID),
    ).fetchone()[0]
    connection.close()

    payload = {
        "reviewLibraryState": dict(state) if state else None,
        "closedLoopLibraryState": dict(upstream) if upstream else None,
        "reviewRows": total,
        "reviewStatusCounts": {row["review_status"]: row["n"] for row in counts},
        "reviewClosedLoopVersions": {str(row["closed_loop_version"]): row["n"] for row in versions},
        "reviewedAtRange": {"min": reviewed_at["lo"], "max": reviewed_at["hi"]},
        "broadcastRequests": requests,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
