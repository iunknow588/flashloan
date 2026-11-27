#!/usr/bin/env python3
"""Watch the V4 pool-liquidity maintenance slot until it leaves `error`.

The slot is the **first** stage of the maintenance chain, so while it is in
`error` the chain has no complete upstream and every stage behind it is
gated.  The watcher polls the live store **read-only** and prints one JSON
line per poll: the slot status, the moment it was last written, and the
error text when there is one.

Usage:

    python plan/runtime_evidence/tools/watch_liquidity_slot_20261126.py [minutes]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STORE = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
DOMAIN = "v4_pool_liquidity_worker"


def snapshot() -> dict[str, object]:
    uri = "file:" + STORE.as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        row = connection.execute(
            "SELECT status, completeness_json, error_json, refreshed_at"
            " FROM chain_business_initialization WHERE domain = ?",
            (DOMAIN,),
        ).fetchone()
        if row is None:
            return {"at": time.strftime("%H:%M:%S"), "status": None}
        status, completeness, error, refreshed = row
        try:
            completeness = json.loads(completeness or "{}")
        except (TypeError, ValueError):
            completeness = {}
        try:
            error = json.loads(error or "{}")
        except (TypeError, ValueError):
            error = {}
        message = str(error.get("message") or "")
        return {
            "at": time.strftime("%H:%M:%S"),
            "status": status,
            "mode": completeness.get("mode"),
            "refreshed_at": refreshed,
            "scanned_pool_count": completeness.get("scanned_pool_count"),
            "snapshot_rows_written": completeness.get("snapshot_rows_written"),
            "endpoint_attempts": message.count("; ") + 1 if message else 0,
            "error": message[:160],
        }
    finally:
        connection.close()


def main() -> None:
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        state = snapshot()
        print(json.dumps(state, ensure_ascii=False), flush=True)
        if state.get("status") == "ok":
            print("liquidity slot recovered", flush=True)
            return
        time.sleep(20)
    print("watcher deadline reached", flush=True)


if __name__ == "__main__":
    main()

