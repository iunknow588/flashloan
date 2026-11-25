#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only probe: does the pytest process write into the live chain store?

The live mainnet chain database is also written by the running opportunity
console, so a single before/after comparison cannot attribute a commit.  This
probe therefore

* snapshots the store **read-only** (``mode=ro``) before and after the window,
* samples ``PRAGMA data_version`` while the window is open, and
* keeps a byte-level snapshot of the production gate namespaces so a rewrite
  that stores the same value is still visible.

A *silent control* window (same sampler, no subprocess) separates background
writers from the process under test.

Usage:
    python probe_live_db_writes_20261125.py --db <path> --label <name>
                                            [--out <json>] [--command "..."]
                                            [--interval 0.4]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import time


GATE_PREFIXES = (
    "cow_submission_pause_guard",
    "liquidation_pause_guard",
    "page_state",
)


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _ro_connection(db_path: str) -> sqlite3.Connection:
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    return connection


def _file_state(db_path: str) -> dict:
    state = {}
    for suffix in ("", "-wal", "-shm"):
        path = db_path + suffix
        if os.path.exists(path):
            info = os.stat(path)
            state[suffix or "main"] = {
                "size": info.st_size,
                "mtimeNs": info.st_mtime_ns,
            }
    return state


def _snapshot(db_path: str) -> dict:
    """Read the commit counter plus the production gate rows (read-only)."""
    connection = _ro_connection(db_path)
    try:
        data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
        rows = connection.execute(
            "SELECT key, value_json, updated_at FROM kv_entries "
            "WHERE collection = 'parameters' AND ("
            + " OR ".join("key LIKE ?" for _ in GATE_PREFIXES)
            + ") ORDER BY key",
            tuple(prefix + "%" for prefix in GATE_PREFIXES),
        ).fetchall()
        gate_rows = {
            row["key"]: {
                "value": row["value_json"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }
        meta_rows = {
            row["meta_key"]: row["value_json"]
            for row in connection.execute(
                "SELECT meta_key, value_json FROM kv_meta ORDER BY meta_key"
            ).fetchall()
        }
        counts = {}
        for table in ("cow_execution_reviews", "cow_execution_attempts"):
            try:
                counts[table] = int(
                    connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                )
            except sqlite3.Error:
                counts[table] = None
        return {
            "dataVersion": data_version,
            "gateRows": gate_rows,
            "metaRows": meta_rows,
            "rowCounts": counts,
        }
    finally:
        connection.close()


def _diff_gate_rows(before: dict, after: dict) -> dict:
    changed = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changed.append({"key": key, "before": old, "after": new})
    return {"changedKeys": changed, "changedCount": len(changed)}


def _sample(db_path: str, interval: float, stop_at: float, samples: list) -> None:
    connection = _ro_connection(db_path)
    try:
        while time.monotonic() < stop_at:
            try:
                version = int(connection.execute("PRAGMA data_version").fetchone()[0])
            except sqlite3.Error:
                version = None
            samples.append(version)
            time.sleep(interval)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--command", default=None)
    parser.add_argument(
        "--command-file",
        default=None,
        help="read the shell command from a file (avoids quoting problems)",
    )
    parser.add_argument("--interval", type=float, default=0.4)
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    before = _snapshot(db_path)
    files_before = _file_state(db_path)
    samples = []
    started = time.monotonic()
    stop_at = started + 1.0
    command_line = args.command
    if args.command_file:
        with open(os.path.abspath(args.command_file), "r", encoding="utf-8") as handle:
            command_line = handle.read().strip()
    command = None
    if command_line:
        command = subprocess.run(
            command_line,
            shell=True,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
            capture_output=True,
            text=True,
            errors="replace",
        )
        stop_at = time.monotonic() + 1.0
    else:
        stop_at = started + 30.0
    _sample(db_path, args.interval, stop_at, samples)
    after = _snapshot(db_path)
    files_after = _file_state(db_path)

    versions = [value for value in samples if value is not None]
    increments = 0
    for previous, current in zip(versions, versions[1:]):
        if current != previous:
            increments += 1
    report = {
        "label": args.label,
        "generatedAt": _now(),
        "database": db_path,
        "command": command_line,
        "commandExitCode": None if command is None else command.returncode,
        "commandStdoutTail": None if command is None else command.stdout[-2000:],
        "commandStderrTail": None if command is None else command.stderr[-2000:],
        "windowSeconds": round(time.monotonic() - started, 3),
        "sampleCount": len(versions),
        "observedDataVersionIncrements": increments,
        "dataVersionBefore": before["dataVersion"],
        "dataVersionAfter": after["dataVersion"],
        "fileStateBefore": files_before,
        "fileStateAfter": files_after,
        "gateDiff": _diff_gate_rows(before["gateRows"], after["gateRows"]),
        "metaDiff": _diff_gate_rows(before["metaRows"], after["metaRows"]),
        "rowCountsBefore": before["rowCounts"],
        "rowCountsAfter": after["rowCounts"],
        "gateKeyCount": len(before["gateRows"]),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
