"""Read-only watcher: wait for the review generation that follows a restart.

The watcher prints the newest review generation of the chain and stops as
soon as it is newer than the generation the caller gave it.  It never writes
to the store: the database is opened `mode=ro` with
`PRAGMA query_only = ON`.

Usage:
    python watch_review_generation_after_d198_20261126.py <generation> [seconds]
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STORE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
NETWORK = "robinhood"


def newest_generation() -> str | None:
    connection = sqlite3.connect(f"file:{STORE.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT MAX(reviewed_at) FROM manager_v4_reviews WHERE network = ?",
            (NETWORK,),
        ).fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: watch_review_generation_after_d198_20261126.py <generation> [seconds]")
        return 2
    baseline = sys.argv[1]
    budget = float(sys.argv[2]) if len(sys.argv) > 2 else 3600.0
    deadline = time.time() + budget
    while time.time() < deadline:
        current = newest_generation()
        if current and current > baseline:
            print(f"NEW_GENERATION {current}", flush=True)
            return 0
        print(f"waiting generation={current} baseline={baseline}", flush=True)
        time.sleep(30)
    print(f"TIMEOUT generation={newest_generation()} baseline={baseline}", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
