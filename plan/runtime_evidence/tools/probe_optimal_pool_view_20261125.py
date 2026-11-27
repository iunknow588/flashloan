#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report the production schema state of the `optimal_pool_id` demotion.

`T-方储.01` demoted `optimal_pool_id` from a stored column to a **view**
(migration `20261125_017_observation_optimal_pool_view`).  The live store
may still carry the old column, or the migration may not have been applied at
all.  This probe answers both questions **read-only**.

Read-only: the store is opened with `mode=ro` + `PRAGMA query_only`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def probe(database_path: Path) -> dict:
    uri = "file:" + database_path.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        has_table = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            " AND name = 'schema_migrations'"
        ).fetchone()[0]
        migrations = []
        if has_table:
            migrations = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM schema_migrations ORDER BY rowid DESC LIMIT 8"
                )
            ]
        objects = [
            dict(row)
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master"
                " WHERE sql LIKE '%optimal_pool_id%'"
                " ORDER BY type, name"
            )
        ]
        columns = []
        for table in (
            "manager_v4_closed_loop_hops",
            "manager_dex_candidate_pools",
            "manager_market_observation_intermediate_pools",
        ):
            try:
                rows = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT name, type FROM pragma_table_info(?)"
                        " WHERE name = 'optimal_pool_id'",
                        (table,),
                    )
                ]
            except sqlite3.Error as error:
                rows = [{"error": str(error)}]
            columns.append({"table": table, "optimal_pool_id": rows})
    finally:
        connection.close()
    return {
        "schema": "optimal-pool-view-probe-v1",
        "mode": "read-only",
        "database": str(database_path).replace(chr(92), "/"),
        "hasSchemaMigrationsTable": bool(has_table),
        "appliedMigrations": migrations,
        "objectsMentioningOptimalPoolId": objects,
        "columns": columns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    report = probe(Path(arguments.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print("wrote:", target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
