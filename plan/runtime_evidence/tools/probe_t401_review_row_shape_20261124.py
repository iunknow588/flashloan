#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump the shape of one published review row (read-only).

The profitDrop measurement needs to know which fields of a published review
carry the loop's profit, its capacity upper bound and the probe ladder, so
the measurement reads the real columns instead of guessing them.

Usage:
    python probe_t401_review_row_shape_20261124.py <store.db> [loop_key]
"""

import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")


def describe(node, path="", depth=0, out=None):
    if out is None:
        out = []
    if depth > 3:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "." + key if path else key
            if isinstance(value, (dict, list)):
                out.append((child, type(value).__name__, len(value)))
                describe(value, child, depth + 1, out)
            else:
                text = str(value)
                out.append((child, type(value).__name__, text[:80]))
    elif isinstance(node, list) and node:
        describe(node[0], path + "[]", depth + 1, out)
    return out


def main():
    store_path = sys.argv[1]
    loop_key = sys.argv[2] if len(sys.argv) > 2 else None
    connection = sqlite3.connect(
        "file:" + os.path.abspath(store_path).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    if loop_key:
        row = connection.execute(
            "SELECT * FROM manager_v4_reviews WHERE loop_key=?", (loop_key,)
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM manager_v4_reviews"
            " WHERE network='robinhood' AND chain_id=4663 AND protocol='uniswap_v4'"
            " ORDER BY loop_key LIMIT 1"
        ).fetchone()
    if row is None:
        print("no review row")
        return
    for column in row.keys():
        value = row[column]
        text = str(value)
        print("%-34s %s" % (column, text[:120] if len(text) > 120 else text))
    print("=== run_arbitrage_json ===")
    try:
        payload = json.loads(row["run_arbitrage_json"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    for path, kind, size in describe(payload):
        print("%-70s %-6s %s" % (path, kind, size))
    connection.close()


if __name__ == "__main__":
    main()
