"""Read the closed-loop library state beside the runtime's own snapshot.

The question the tool answers is the **consumer acknowledgement**: the
closed-loop Manager sets ``consumers_need_rebuild`` when the directory
moves, and the consumer clears it once it rebuilt against the generation
the library publishes now.  The tool prints the two facts that have to
agree, so the flag can be read instead of estimated:

* ``library``   -- the generation the library publishes now, whether it says
  the consumers still have to rebuild, and when the directory was written;
* ``measured``  -- the generation the reviews were **measured under**, with
  the newest measurement of each generation.  A review row carries the
  generation of the catalog the measurement was made against, so the
  newest generation that carries rows is the generation the consumers hold;
* ``runtime``   -- the RuntimeCore's own state, its snapshot generation and
  whether it is refusing the snapshot.

Usage:
    python probe_consumer_ack_20261126.py [port] [database]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from pathlib import Path


def fetch(url: str, timeout: float = 20.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def read_library(database: str) -> tuple:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        library = connection.execute(
            """SELECT closed_loop_version, materialized_checksum,
                       consumers_need_rebuild, generated_at, updated_at
                 FROM manager_v4_closed_loop_library_state
                 WHERE network = 'robinhood' AND chain_id = 4663
                   AND scope_key = 'core4'"""
        ).fetchone()
        measured = connection.execute(
            """SELECT closed_loop_version, COUNT(*), MIN(reviewed_at), MAX(reviewed_at)
                 FROM manager_v4_reviews
                 WHERE network = 'robinhood' AND chain_id = 4663
                 GROUP BY closed_loop_version
                 ORDER BY MAX(reviewed_at) DESC"""
        ).fetchall()
    finally:
        connection.close()
    return library, measured


def main() -> int:
    port = int(sys.argv[1] if len(sys.argv) > 1 else 5000)
    database = (
        sys.argv[2]
        if len(sys.argv) > 2
        else str(
            Path(__file__).resolve().parents[3]
            / "flashloan"
            / "db"
            / "local_store_4663.sqlite3"
        )
    )
    library, measured = read_library(database)
    print("== closed loop library ==")
    if library is None:
        print("  (no library state)")
    else:
        print(f"  version: {library[0]}")
        print(f"  checksum: {library[1]}")
        print(f"  consumersNeedRebuild: {library[2]}")
        print(f"  generatedAt: {library[3]}")
        print(f"  updatedAt: {library[4]}")
    print("== reviews per measured generation ==")
    for version, rows, first, last in measured:
        print(f"  generation {version}: {rows} rows, {first} .. {last}")
    sys.stdout.flush()
    print("== runtime core ==")
    try:
        status = fetch(f"http://127.0.0.1:{port}/api/status?chain_id=4663")
    except Exception as error:  # noqa: BLE001 - the probe reports, it does not raise
        print(f"  (status unreadable: {error})")
        sys.stdout.flush()
        return 0
    core = (status.get("chain_runtime") or {}).get("runtimeCore") or {}
    print(f"  state: {core.get('state')!r}")
    print(f"  generation: {core.get('generation')!r}")
    print(f"  lastError: {core.get('lastError')!r}")
    events = (status.get("chain_runtime") or {}).get("recentEvents") or []
    for event in events[:6]:
        print(f"  event: {event.get('type')} {event.get('createdAt')}")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
