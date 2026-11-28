#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sample the availability of the store's write lock.

The manager writes the cascade layers into the live store, and the
runtime writes its business rows into the same store.  A writer that
cannot take the write lock fails with ``database is locked``, so the
question the cascade needs answered is not *who* holds the lock but
*how long* it is held and whether it is ever free.

The probe opens its own connection, asks for an immediate write
transaction with a short busy timeout and reports the outcome of every
sample.  It never changes a row: the transaction is rolled back
immediately, so the probe is a writer that writes nothing.

Usage::

    python probe_write_lock_20261128.py --samples 30 --interval 2
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def sample(database: Path, busy_timeout_ms: int) -> dict:
    connection = sqlite3.connect(str(database), timeout=busy_timeout_ms / 1000.0,
                                 isolation_level=None)
    try:
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        started = time.time()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
            return {"locked": False, "waitSeconds": round(time.time() - started, 3)}
        except sqlite3.OperationalError as error:
            return {"locked": True, "waitSeconds": round(time.time() - started, 3),
                    "error": str(error)}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--busy-timeout-ms", type=int, default=500)
    args = parser.parse_args()

    results = []
    for index in range(args.samples):
        result = sample(args.database, args.busy_timeout_ms)
        result["sample"] = index
        result["at"] = time.time()
        results.append(result)
        print(f"{index:02d} locked={result['locked']} wait={result['waitSeconds']}s")
        if index + 1 < args.samples:
            time.sleep(args.interval)

    free = [item for item in results if not item["locked"]]
    print(json.dumps({
        "samples": len(results),
        "freeSamples": len(free),
        "lockedSamples": len(results) - len(free),
        "maxWaitSeconds": max((item["waitSeconds"] for item in free), default=0.0),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
