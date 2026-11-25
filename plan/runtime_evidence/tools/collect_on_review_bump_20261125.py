#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect the closure readings the moment the review generation moves.

The review generation only advances when the runtime has priced **every**
closed loop, so the readings are only valid in the seconds right after the
advance.  This watcher therefore polls the store read-only and, as soon as
the generation differs from the baseline, runs the two collectors.

Read-only: the store is opened with ``mode=ro`` + ``PRAGMA query_only = ON``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "plan" / "runtime_evidence" / "tools"
EVIDENCE = REPO / "plan" / "runtime_evidence"
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def read_version(database_path: Path) -> tuple[int | None, str | None]:
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT review_version, updated_at FROM manager_review_library_state"
            " WHERE network = 'robinhood' AND chain_id = 4663 AND protocol = 'uniswap_v4'"
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--baseline", type=int, default=115)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--minutes", type=float, default=90.0)
    parser.add_argument("--skip-integrity", action="store_true")
    arguments = parser.parse_args()
    database_path = Path(arguments.db)
    deadline = time.time() + arguments.minutes * 60.0

    while time.time() < deadline:
        version, updated_at = read_version(database_path)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        print(f"{stamp} reviewVersion={version} updatedAt={updated_at}", flush=True)
        if version is not None and version != arguments.baseline:
            print(f"REVIEW VERSION MOVED {arguments.baseline} -> {version}", flush=True)
            break
        time.sleep(arguments.interval)
    else:
        print("the generation did not move within the window", flush=True)
        return 2

    reports = EVIDENCE / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    closure = reports / f"remaining_closure_v{version}_20261125.json"
    result = subprocess.run(
        [sys.executable, str(TOOLS / "probe_remaining_closure_20261125.py"),
         "--db", str(database_path), "--out", str(closure)],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    print(result.stdout, flush=True)
    print(result.stderr, flush=True)

    if not arguments.skip_integrity:
        integrity = EVIDENCE / f"t401_review_integrity_v{version}.json"
        result = subprocess.run(
            [sys.executable,
             str(TOOLS / "collect_t401_review_integrity_20261124.py"),
             str(database_path), str(integrity)],
            cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        )
        print(result.stdout, flush=True)
        print(result.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
