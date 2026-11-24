#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Poll the review library state and log only when it changes.

Usage:
    python watch_t401_review_version_20261124.py <store.db> [seconds]
"""

import datetime
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"


def read_state(store_path):
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


def main():
    store_path = sys.argv[1]
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    previous = None
    while True:
        try:
            state = read_state(store_path)
        except Exception as error:  # noqa: BLE001 - watcher must survive transient db locks
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            print(f"{stamp} read-error: {error}", flush=True)
            time.sleep(interval)
            continue
        marker = (state.get("review_version"), state.get("generated_at"))
        if marker != previous:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            print(f"{stamp} {state}", flush=True)
            previous = marker
        time.sleep(interval)


if __name__ == "__main__":
    main()
