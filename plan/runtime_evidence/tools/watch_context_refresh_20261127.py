"""Watch the context snapshot of the running runtime, second by second.

The question the watcher answers is the one a single status read cannot:
**when** the snapshot is rebuilt, and **how many** rebuilds a round
costs.  The old build had no counter for it, so the watcher reads the
stamp the snapshot itself carries and counts the changes.

Usage:

    python plan/runtime_evidence/tools/watch_context_refresh_20261127.py [seconds]
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
STORE = ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
STATUS = "http://127.0.0.1:5000/api/status"


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def scope_of(payload) -> dict:
    """The context cache of the closed-loop scanner."""
    node = payload
    for key in ("dex_opportunity_scanner", "closedLoop", "contextCache"):
        node = node.get(key, {}) if isinstance(node, dict) else {}
    scopes = node.get("scopes") or []
    if not scopes:
        return {}
    scope = scopes[0]
    return {
        "loadedAt": scope.get("loadedAt"),
        "closedLoopGeneration": scope.get("closedLoopGeneration"),
        "generationCheckedAt": scope.get("generationCheckedAt"),
        "poolCount": scope.get("poolCount"),
        "reloads": node.get("reloads"),
    }


def reviews(connection) -> dict:
    """How far the round has come, and which generation it is on."""
    rows = connection.execute(
        """
        SELECT closed_loop_version, COUNT(*), MIN(reviewed_at), MAX(reviewed_at)
        FROM manager_v4_reviews
        WHERE network = 'robinhood' AND chain_id = 4663
        GROUP BY closed_loop_version
        ORDER BY MIN(reviewed_at)
        """
    ).fetchall()
    return {
        str(version): {
            "loops": count,
            "first": first,
            "last": last,
        }
        for version, count, first, last in rows
    }


def directory_generation(connection) -> str:
    """The generation of the closed-loop directory, as the disk carries it."""
    row = connection.execute(
        """
        SELECT closed_loop_version, materialized_checksum
        FROM manager_v4_closed_loop_library_state
        WHERE network = 'robinhood' AND chain_id = 4663
          AND protocol = 'uniswap_v4' AND scope_key = 'core4'
        """
    ).fetchone()
    if row is None:
        return "none"
    return f"{row[0]}:{row[1]}"


def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    connection = sqlite3.connect(f"file:{STORE.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    previous_loaded = None
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(STATUS, timeout=15) as handle:
                payload = json.load(handle)
            scope = scope_of(payload)
            loaded = scope.get("loadedAt")
            marker = ""
            if loaded != previous_loaded:
                marker = "  <== snapshot rebuilt"
                previous_loaded = loaded
            rounds = reviews(connection)
            directory = directory_generation(connection)
            snapshot = scope.get("closedLoopGeneration")
            lag = ""
            if snapshot and directory != "none":
                lag = "  GENERATION-MATCH" if snapshot == directory else "  GENERATION-LAG"
            print(
                f"{now()}  loadedAt={loaded}  gen={scope.get('closedLoopGeneration')} "
                f"reloads={scope.get('reloads')}  checkedAt={scope.get('generationCheckedAt')} "
                f"directory={directory}  rounds={json.dumps(rounds, ensure_ascii=False)}"
                f"{marker}{lag}",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - the watcher must not die
            print(f"{now()}  ERROR {error}", flush=True)
        time.sleep(3)
    connection.close()


if __name__ == "__main__":
    main()
