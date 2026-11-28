#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sample the write lock together with the checkpoint activity of the store.

The store is a multi-gigabyte WAL database.  A writer that cannot take the
write lock fails with ``database is locked``, and two very different causes
look the same from the outside: a long write transaction and a checkpoint
that has to reset the WAL.  This probe samples both at the same moment, so
the two causes can be told apart:

* every sample asks for an immediate write transaction (and rolls it back
  at once, so the probe never changes a row), and
* every sample records the modification time and the size of the database
  file, because a checkpoint is the only thing that moves the database file
  while the WAL keeps its size.

Usage::

    python probe_write_lock_with_checkpoint_20261128.py --samples 60 --interval 1
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
    connection = sqlite3.connect(
        str(database), timeout=busy_timeout_ms / 1000.0, isolation_level=None
    )
    try:
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        started = time.time()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
            locked = False
            error = None
        except sqlite3.OperationalError as failure:
            locked = True
            error = str(failure)
        waited = round(time.time() - started, 3)
    finally:
        connection.close()
    stat = database.stat()
    wal = database.with_name(database.name + "-wal")
    wal_stat = wal.stat() if wal.exists() else None
    return {
        "locked": locked,
        "waitSeconds": waited,
        "error": error,
        "databaseBytes": stat.st_size,
        "databaseMtime": stat.st_mtime,
        "walBytes": wal_stat.st_size if wal_stat else None,
        "walMtime": wal_stat.st_mtime if wal_stat else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--busy-timeout-ms", type=int, default=500)
    args = parser.parse_args()

    results = []
    for index in range(args.samples):
        result = sample(args.database, args.busy_timeout_ms)
        result["sample"] = index
        result["at"] = time.time()
        results.append(result)
        print(
            f"{index:03d} locked={result['locked']} wait={result['waitSeconds']}s "
            f"db={result['databaseBytes']} wal={result['walBytes']}",
            flush=True,
        )
        if index + 1 < args.samples:
            time.sleep(args.interval)

    free = [item for item in results if not item["locked"]]
    checkpoints = [
        item for index, item in enumerate(results) if index and item["databaseMtime"] != results[index - 1]["databaseMtime"]
    ]
    payload = {
        "samples": len(results),
        "freeSamples": len(free),
        "lockedSamples": len(results) - len(free),
        "checkpointSamples": len(checkpoints),
        "freeWhileCheckpointing": sum(1 for item in checkpoints if not item["locked"]),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    Path(__file__).with_name(
        "probe_write_lock_with_checkpoint_20261128_result.json"
    ).write_text(
        json.dumps({"summary": payload, "samples_detail": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
