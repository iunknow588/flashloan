#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch the two numbers that decide whether the review generation lands.

The resident worker count says whether every published closed-loop group has
a worker; the review library version says whether the generation was
published.  Both are polled so the waiting does not need a large snapshot.

Usage:
    python watch_t401_publish_20261124.py <store.db> [seconds]
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
        "maxWorkers": workers.get("maxWorkers"),
        "createdTotal": workers.get("createdTotal"),
        "lastDurationMs": payload.get("scanner", {}).get("last_duration_ms"),
    }


def main():
    store_path = sys.argv[1]
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    while True:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            state = read_review_state(store_path)
        except Exception as error:
            state = {"error": str(error)}
        try:
            workers = read_resident_workers()
        except Exception as error:
            workers = {"error": str(error)}
        print(json.dumps({"at": stamp, "workers": workers, "review": state},
                         ensure_ascii=False),
              flush=True)
        time.sleep(seconds)


if __name__ == "__main__":
    main()
