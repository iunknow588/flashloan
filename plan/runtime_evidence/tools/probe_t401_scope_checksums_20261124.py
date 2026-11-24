#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print every layer's pool-scope checksum and the scope it belongs to.

``pool_scope_checksum`` identifies the **scope** of a materialization.  The
`core4` scope and the `candidate_exchange` scope are different pool tables
with different selection modes, so their checksums are expected to differ.
This probe prints the scope next to the checksum so a comparison can be
checked before it is made.
"""

import os
import sqlite3
import sys

REPO = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
DB = os.path.join(REPO, "flashloan", "db", "local_store_4663.sqlite3")

TABLES = [
    "manager_v4_closed_loop_library_state",
    "manager_v4_review_library_state",
    "manager_v4_candidate_edge_library_state",
    "manager_v4_candidate_exchange_library_state",
    "manager_v4_direction_pool_library_state",
    "manager_v4_directional_pool_library_state",
]


def main() -> int:
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for table in TABLES:
        if table not in names:
            continue
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if "pool_scope_checksum" not in columns:
            continue
        picked = [
            column
            for column in (
                "scope_key",
                "network",
                "chain_id",
                "protocol",
                "pool_scope_checksum",
                "materialized_checksum",
                "closed_loop_version",
                "candidate_edge_version",
                "observation_version",
            )
            if column in columns
        ]
        rows = connection.execute(
            f"SELECT {', '.join(picked)} FROM {table}"
        ).fetchall()
        print(f"== {table} ({len(rows)} rows)")
        for row in rows:
            print("   ", dict(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
