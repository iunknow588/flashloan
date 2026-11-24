#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the closed-loop rows of two stores, column by column.

The closed-loop tables carry two kinds of columns: the **derived** ones
(the capacity, the direction, the pool path) and the **volatile** ones
(`created_at`, `updated_at`).  A rollback drill claims that the reading is
restored, so the comparison ignores the volatile columns and reports every
derived column that differs.

Usage:
    python compare_closed_loop_rows_20261124.py <before.db> <after.db> [report.json]
"""

import json
import os
import sqlite3
import sys

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"
VOLATILE = {"created_at", "updated_at", "generated_at"}
LOOP_KEY = ("loop_key",)
HOP_KEY = ("loop_key", "hop_index")


def columns_of(connection, table):
    return [row[1] for row in connection.execute("PRAGMA table_info(%s)" % table)]


def read_rows(connection, table, key_columns):
    columns = [c for c in columns_of(connection, table) if c not in VOLATILE]
    order = [c for c in key_columns if c in columns]
    sql = "SELECT %s FROM %s WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4" % (
        ", ".join(columns),
        table,
    )
    if order:
        sql += " ORDER BY %s" % ", ".join(order)
    rows = {}
    for row in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, SCOPE)):
        record = dict(zip(columns, row))
        key = tuple(str(record.get(name)) for name in key_columns)
        rows[key] = record
    return rows, columns


def compare(before_rows, after_rows, columns):
    differences = {}
    before_keys = set(before_rows)
    after_keys = set(after_rows)
    removed = sorted(before_keys - after_keys)
    added = sorted(after_keys - before_keys)
    for column in columns:
        changed = []
        for key in sorted(before_keys & after_keys):
            left = before_rows[key].get(column)
            right = after_rows[key].get(column)
            if left != right:
                changed.append(key)
        if changed:
            differences[column] = len(changed)
    return {
        "rows_before": len(before_rows),
        "rows_after": len(after_rows),
        "removed_keys": len(removed),
        "added_keys": len(added),
        "removed_sample": [list(key) for key in removed[:5]],
        "added_sample": [list(key) for key in added[:5]],
        "differing_columns": differences,
        "identical": not removed and not added and not differences,
    }


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    before_path, after_path = sys.argv[1], sys.argv[2]
    report_path = sys.argv[3] if len(sys.argv) > 3 else None

    report = {"before": os.path.abspath(before_path), "after": os.path.abspath(after_path)}
    for table, key_columns in (
        ("manager_v4_closed_loops", LOOP_KEY),
        ("manager_v4_closed_loop_hops", HOP_KEY),
    ):
        before = sqlite3.connect("file:%s?mode=ro" % before_path, uri=True)
        after = sqlite3.connect("file:%s?mode=ro" % after_path, uri=True)
        try:
            before_rows, before_columns = read_rows(before, table, key_columns)
            after_rows, after_columns = read_rows(after, table, key_columns)
        finally:
            before.close()
            after.close()
        shared = [c for c in before_columns if c in after_columns]
        only_before = [c for c in before_columns if c not in after_columns]
        only_after = [c for c in after_columns if c not in before_columns]
        report[table] = compare(before_rows, after_rows, shared)
        report[table]["columns_only_in_before"] = only_before
        report[table]["columns_only_in_after"] = only_after

    report["identical"] = all(report[table]["identical"] for table in (
        "manager_v4_closed_loops",
        "manager_v4_closed_loop_hops",
    ))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report_path:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
