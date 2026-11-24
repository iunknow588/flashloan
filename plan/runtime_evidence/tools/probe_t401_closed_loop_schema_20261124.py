#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the closed-loop catalog of one or two stores (read-only).

The profitDrop measurement compares the **pre-switch** catalog with the
**current** one, so it needs to know that both stores carry the same tables
and columns.  This probe prints the schema and the version of each store.

Usage:
    python probe_t401_closed_loop_schema_20261124.py <store.db> [store2.db]
"""

import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")


def main():
    for path in sys.argv[1:]:
        connection = sqlite3.connect(
            "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        print("===== %s" % os.path.basename(path))
        for table in ("manager_v4_closed_loops", "manager_v4_closed_loop_hops"):
            try:
                columns = [
                    (row["name"], row["type"])
                    for row in connection.execute("PRAGMA table_info(%s)" % table)
                ]
            except sqlite3.Error as error:
                print("  %s: %s" % (table, error))
                continue
            count = connection.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            print("  -- %s (rows=%d)" % (table, count))
            print("     " + ", ".join(name for name, _ in columns))
        try:
            state = connection.execute(
                "SELECT closed_loop_version, materialized_checksum, loop_count, hop_count,"
                " anchor_count, directional_pool_version, consumers_need_rebuild"
                " FROM manager_v4_closed_loop_library_state"
            ).fetchone()
            print("  state: %s" % (dict(state) if state else None))
        except sqlite3.Error as error:
            print("  state: %s" % error)
        connection.close()


if __name__ == "__main__":
    main()
