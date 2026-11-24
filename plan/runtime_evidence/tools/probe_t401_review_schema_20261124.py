#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List the review-related tables and their columns (read-only).

The T-复.01 checklist needs the **ladder** rows of a published review to be
queryable per rung, and it needs the review rows to carry the signed PnL of
the loop.  This probe answers which tables hold that, so the profitDrop
measurement is built on the real shape of the data instead of an assumption.

Usage:
    python probe_t401_review_schema_20261124.py <store.db>
"""

import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")


def main():
    store_path = sys.argv[1]
    connection = sqlite3.connect(
        "file:" + os.path.abspath(store_path).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    names = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND (name LIKE '%review%' OR name LIKE '%ladder%' OR name LIKE '%hop%')"
            " ORDER BY name"
        )
    ]
    for name in names:
        columns = [
            (row["name"], row["type"])
            for row in connection.execute("PRAGMA table_info(%s)" % name)
        ]
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM %s" % name
            ).fetchone()[0]
        except sqlite3.Error:
            count = "?"
        print("== %s (rows=%s)" % (name, count))
        print("   " + ", ".join("%s %s" % (column, kind) for column, kind in columns))
    connection.close()


if __name__ == "__main__":
    main()
