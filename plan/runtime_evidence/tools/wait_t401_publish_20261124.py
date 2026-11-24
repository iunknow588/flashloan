#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wait until the review generation is published on the live chain.

The watcher only records; this one exits with a verdict, so the caller can
pick the result up without polling by hand.

Exit codes:
    0  published (review_version advanced and the loop count matches)
    1  gave up (timeout)
    2  the runtime or the store went away

Usage:
    python wait_t401_publish_20261124.py <store.db> [max_minutes] [min_review_version]
"""

import datetime
import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"
EXPECTED_LOOPS = 174


def read_review_state(store_path):
    connection = sqlite3.connect(
        "file:" + os.path.abspath(store_path).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT review_version, closed_loop_version, loop_count, reviewed_count,"
        " completed_count, data_incomplete_count, not_executable_count,"
        " generated_at FROM manager_review_library_state"
        " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
    ).fetchone()
    connection.close()
    return dict(row) if row else {}


def read_resident_workers():
    url = "http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot"
    with urllib.request.urlopen(url, timeout=60) as handle:
        payload = json.loads(handle.read().decode("utf-8", errors="replace"))
    workers = payload.get("scanner", {}).get("closed_loop_rust_workers", {})
    return {
        "residentWorkers": workers.get("residentWorkers"),
        "createdTotal": workers.get("createdTotal"),
        "reviewQueueCompleted": (workers.get("reviewQueue") or {}).get("completed"),
        "maintenanceStatus": (workers.get("maintenance") or {}).get("lastStatus"),
    }


def main():
    store_path = sys.argv[1]
    max_minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    min_review_version = int(sys.argv[3]) if len(sys.argv) > 3 else 25

    deadline = time.time() + max_minutes * 60.0
    last = None
    while time.time() < deadline:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            state = read_review_state(store_path)
        except Exception as error:
            print("%s store read failed: %s" % (stamp, error), flush=True)
            return 2
        try:
            workers = read_resident_workers()
        except Exception as error:
            workers = {"error": str(error)}
        print(json.dumps({"at": stamp, "workers": workers, "review": state},
                         ensure_ascii=False), flush=True)

        version = state.get("review_version") or 0
        loops = state.get("loop_count") or 0
        if version >= min_review_version and loops == EXPECTED_LOOPS:
            print("PUBLISHED review_version=%s loop_count=%s" % (version, loops),
                  flush=True)
            return 0
        last = state
        time.sleep(30)

    print("TIMEOUT last=%s" % json.dumps(last, ensure_ascii=False), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
