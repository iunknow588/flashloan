#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch the review generation of the live store until it moves.

The review generation is the generation counter of the review Manager's
published surface.  It stops moving while the closed loop's hops cannot be
priced, so the counter is the single read that says whether a fix reached
the running system.  The store is read in read-only mode.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import time
from pathlib import Path


DEFAULT_DB = Path("E:/2026OPC大赛/flashLoan/flashloan/db/local_store_4663.sqlite3")
NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"


def read_state(database_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            """
            SELECT review_version, closed_loop_version, candidate_edge_version,
                   observation_version, loop_count, completed_count,
                   data_incomplete_count, not_executable_count, failed_count,
                   broadcast_request_count, updated_at
            FROM manager_review_library_state
            WHERE network = ? AND chain_id = ? AND protocol = ?
            """,
            (NETWORK, CHAIN_ID, PROTOCOL),
        ).fetchone()
        if row is None:
            return {}
        names = [
            "reviewVersion",
            "closedLoopVersion",
            "candidateEdgeVersion",
            "observationVersion",
            "loopCount",
            "completedCount",
            "dataIncompleteCount",
            "notExecutableCount",
            "failedCount",
            "broadcastRequestCount",
            "updatedAt",
        ]
        return dict(zip(names, row))
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--minutes", type=float, default=20.0)
    arguments = parser.parse_args()
    database_path = Path(arguments.db)
    deadline = time.time() + arguments.minutes * 60.0
    baseline = read_state(database_path).get("reviewVersion")
    lines: list[str] = []
    while time.time() < deadline:
        state = read_state(database_path)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{stamp} " + json.dumps(state, ensure_ascii=False)
        lines.append(line)
        print(line, flush=True)
        current = state.get("reviewVersion")
        if baseline is not None and current is not None and current != baseline:
            lines.append(f"{stamp} REVIEW VERSION MOVED {baseline} -> {current}")
            print(lines[-1], flush=True)
            break
        time.sleep(arguments.interval)
    if arguments.out:
        Path(arguments.out).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
