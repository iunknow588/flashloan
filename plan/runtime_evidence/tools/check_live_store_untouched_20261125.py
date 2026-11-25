#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove that the live store carries no trace of the ``T-广.01`` fixture run.

The broadcast refusal paths have to be *driven* to be believed, and driving
them writes a refusal trail (``BC-3``).  Those writes must not land in the
live store.  The probe run therefore used a **separate** fixture file; this
script reads the live store read-only and shows that none of the fixture's
rows are there.

Usage:
    python check_live_store_untouched_20261125.py [live.db]
"""

import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"E:\2026OPC大赛\flashLoan"
DEFAULT_LIVE = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")

PROBES = [
    ("attempt rows", "SELECT COUNT(*) FROM manager_cow_execution_attempts"),
    (
        "attempts on a fixture loop key",
        "SELECT COUNT(*) FROM manager_cow_execution_attempts WHERE loop_key LIKE 'fixture:%'",
    ),
    ("refusal trail rows", "SELECT COUNT(*) FROM manager_broadcast_attempt_diagnostics"),
    ("broadcast library state rows", "SELECT COUNT(*) FROM manager_broadcast_library_state"),
    (
        "broadcast requests on a fixture loop key",
        "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests WHERE loop_key LIKE 'fixture:%'",
    ),
    (
        "any row mentioning the fixture rpc",
        "SELECT COUNT(*) FROM manager_cow_execution_attempts WHERE rpc_url LIKE '%example.invalid%'",
    ),
]


def main():
    live = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LIVE)
    connection = sqlite3.connect("file:" + live.replace("\\", "/") + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    readings = {}
    for label, sql in PROBES:
        readings[label] = connection.execute(sql).fetchone()[0]
    connection.close()

    # The fixture names are the only rows the probe could have written.
    # Every one of them must be zero for the live store to be untouched.
    fixture_only = [
        label
        for label in readings
        if label != "attempt rows"
        and label != "refusal trail rows"
        and label != "broadcast library state rows"
    ]
    verdict = {
        "liveStore": live,
        "mode": "read-only",
        "queryOnly": True,
        "readings": readings,
        "fixtureRowsInLiveStore": sum(readings[label] for label in fixture_only),
        "liveBroadcastAttemptRows": readings["attempt rows"],
        "liveRefusalTrailRows": readings["refusal trail rows"],
        "liveBroadcastLibraryStateRows": readings["broadcast library state rows"],
    }
    verdict["passed"] = (
        verdict["fixtureRowsInLiveStore"] == 0
        and verdict["liveBroadcastAttemptRows"] == 0
        and verdict["liveRefusalTrailRows"] == 0
        and verdict["liveBroadcastLibraryStateRows"] == 0
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
