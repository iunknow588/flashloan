#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure how much of the time the store's write lock is free.

``database is locked`` says a writer could not take the lock.  It does not
say **why**: a writer that holds the lock for one long transaction starves
everybody for the whole time, while a writer that takes the lock again and
again leaves short gaps that every other writer has to catch.  The two need
different fixes, so the probe measures the duty cycle of the lock instead of
guessing it.

The probe takes the write lock in a tight loop with **no** busy timeout: a
failed attempt means the lock was held at that very moment, and a successful
attempt means it was free.  The free samples are the gaps, and the longest gap
between two free samples is the longest window another writer would have to
catch.

Usage::

    python probe_write_lock_duty_20261128.py --seconds 10
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    connection = sqlite3.connect(
        str(args.database), timeout=0.0, isolation_level=None
    )
    connection.execute("PRAGMA busy_timeout=0")
    attempts = 0
    free = 0
    free_at: list[float] = []
    started = time.monotonic()
    deadline = started + args.seconds
    try:
        while time.monotonic() < deadline:
            attempts += 1
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ROLLBACK")
                free += 1
                free_at.append(time.monotonic() - started)
            except sqlite3.OperationalError:
                pass
    finally:
        connection.close()

    gaps = ([free_at[0]] + [
        free_at[index] - free_at[index - 1] for index in range(1, len(free_at))
    ]) if free_at else []
    payload = {
        "seconds": round(time.monotonic() - started, 3),
        "attempts": attempts,
        "freeSamples": free,
        "lockedSamples": attempts - free,
        "freeRatio": round(free / attempts, 6) if attempts else None,
        "attemptsPerSecond": round(attempts / max(1e-9, time.monotonic() - started), 1),
        "longestFreeWindowSeconds": round(max(gaps), 6) if gaps else 0.0,
        "medianFreeWindowSeconds": (
            round(sorted(gaps)[len(gaps) // 2], 6) if gaps else 0.0
        ),
        "firstFreeAtSeconds": round(free_at[0], 3) if free_at else None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    Path(__file__).with_name(
        "probe_write_lock_duty_20261128_result.json"
    ).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
