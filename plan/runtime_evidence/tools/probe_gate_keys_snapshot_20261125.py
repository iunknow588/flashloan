#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot the production gate keys of a live chain store (read only).

The snapshot is the byte level evidence used by the T-储.01 acceptance: the
value_json and the updated_at of every production gate key have to stay
identical across a pytest run.  The store is opened with mode=ro, so the
snapshot itself can never write.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys


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


def snapshot(db_path: str) -> dict:
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
            row["key"]: {"value": row["value_json"], "updated_at": row["updated_at"]}
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
            "generatedAt": _now(),
            "database": os.path.abspath(db_path),
            "dataVersion": data_version,
            "gateRows": gate_rows,
            "metaRows": meta_rows,
            "rowCounts": counts,
        }
    finally:
        connection.close()


def diff(before: dict, after: dict) -> dict:
    changed = []
    for key in sorted(set(before.get("gateRows", {})) | set(after.get("gateRows", {}))):
        old = before.get("gateRows", {}).get(key)
        new = after.get("gateRows", {}).get(key)
        if old != new:
            changed.append({"key": key, "before": old, "after": new})
    meta_changed = []
    for key in sorted(set(before.get("metaRows", {})) | set(after.get("metaRows", {}))):
        old = before.get("metaRows", {}).get(key)
        new = after.get("metaRows", {}).get(key)
        if old != new:
            meta_changed.append({"key": key, "before": old, "after": new})
    return {
        "gateChangedCount": len(changed),
        "gateChanged": changed,
        "metaChangedCount": len(meta_changed),
        "metaChanged": meta_changed,
        "rowCountsBefore": before.get("rowCounts"),
        "rowCountsAfter": after.get("rowCounts"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--compare", default=None, help="an earlier snapshot json")
    args = parser.parse_args()

    current = snapshot(args.db)
    report = current
    if args.compare:
        with open(os.path.abspath(args.compare), "r", encoding="utf-8") as handle:
            earlier = json.load(handle)
        report = {"current": current, "comparedWith": args.compare, "diff": diff(earlier, current)}
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
