#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch the review generation and collect the integrity reading at once.

The automatic-broadcast gate is 120 seconds wide.  A reading taken minutes
after the generation was published therefore measures the *aged* reviews and
says nothing about the gate.  So the collection is started **the moment** a
new generation appears: the reading then answers "would the gate admit the
review set it was meant to admit?".

Usage:
    python watch_and_collect_t401_20261124.py <store.db> <out.json> [seconds]
"""

import datetime
import os
import sqlite3
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"E:\2026OPC大赛\flashLoan"
COLLECTOR = os.path.join(ROOT, r"plan\runtime_evidence\tools\collect_t401_review_integrity_20261124.py")

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
    out_path = sys.argv[2]
    interval = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    previous = None
    while True:
        try:
            state = read_state(store_path)
        except Exception as error:  # noqa: BLE001 - the watcher must survive locks
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            print(f"{stamp} read-error: {error}", flush=True)
            time.sleep(interval)
            continue
        marker = (state.get("review_version"), state.get("generated_at"))
        if marker != previous:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            print(f"{stamp} generation seen: {state}", flush=True)
            first = previous is None
            previous = marker
            if first:
                continue
            print(f"{stamp} collecting the fresh generation ...", flush=True)
            started = time.time()
            result = subprocess.run(
                [sys.executable, COLLECTOR, store_path, out_path],
                cwd=ROOT,
                capture_output=True,
            )
            elapsed = time.time() - started
            print(
                f"{stamp} collector exit={result.returncode} elapsed={elapsed:.1f}s",
                flush=True,
            )
            sys.stdout.write(result.stdout.decode("utf-8", errors="replace"))
            sys.stdout.write(result.stderr.decode("utf-8", errors="replace"))
            sys.stdout.flush()
        time.sleep(interval)


if __name__ == "__main__":
    main()
