#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the direction layer's state out of the **live** store, read-only.

The probe answers three questions the acceptance checklist asks for:

* how many direction rows are published, and what version and checksum
  does the layer carry;
* what does the closed loop layer carry (its version must not move while
  only the direction layer is refreshed);
* what does the scope configuration say (the enumeration order).

The store is opened with a `mode=ro` URI and `PRAGMA query_only = ON`,
so the probe cannot write to the production database even by accident.
An optional `--dump` writes the direction rows to a JSON file: that is
the snapshot the refresh can be rolled back from.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def read_scope_configuration(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT network, chain_id, scope_key, pool_table, enabled,"
        " materialized_checksum, last_rebuilt_at"
        " FROM pool_scope_config"
        " ORDER BY network, chain_id, scope_key"
    ).fetchall()
    return [dict(row) for row in rows]


def read_directional_rows(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM manager_v4_directional_exchange_pools"
        " ORDER BY scope_key, base_pool_id, direction"
    ).fetchall()
    return [dict(row) for row in rows]


def read_library_state(connection: sqlite3.Connection, table: str) -> list[dict]:
    exists = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()[0]
    if not exists:
        return []
    rows = connection.execute(f"SELECT * FROM {table}").fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(DEFAULT_DB))
    parser.add_argument(
        "--dump", default=None, help="write the direction rows to this JSON file"
    )
    parser.add_argument(
        "--output", default=None, help="write the readings to this JSON file"
    )
    arguments = parser.parse_args()

    database = Path(arguments.database)
    connection = open_read_only(database)
    try:
        rows = read_directional_rows(connection)
        by_scope: dict[str, int] = {}
        for row in rows:
            scope = row["scope_key"]
            by_scope[scope] = by_scope.get(scope, 0) + 1
        materialized_at = sorted(
            {row["materialized_at"] for row in rows if row.get("materialized_at")}
        )
        readings = {
            "database": str(database),
            "readOnly": True,
            "queryOnly": True,
            "directionalRowCount": len(rows),
            "directionalRowsByScope": by_scope,
            "directionalMaterializedAt": materialized_at,
            "directionalLibraryState": read_library_state(
                connection, "manager_v4_directional_pool_library_state"
            ),
            "closedLoopLibraryState": read_library_state(
                connection, "manager_v4_closed_loop_library_state"
            ),
            "scopeConfiguration": read_scope_configuration(connection),
        }
    finally:
        connection.close()

    if arguments.dump:
        dump_path = Path(arguments.dump)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(
            json.dumps(
                {
                    "database": str(database),
                    "takenAt": "2026-11-26",
                    "rowCount": len(rows),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        readings["dump"] = str(dump_path)

    text = json.dumps(readings, ensure_ascii=False, indent=1)
    if arguments.output:
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
