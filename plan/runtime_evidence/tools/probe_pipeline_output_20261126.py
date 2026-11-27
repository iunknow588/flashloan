#!/usr/bin/env python3
"""Read the live pipeline output (read only).

The probe opens the production store with @mode=ro@ and turns on
@PRAGMA query_only@, so it cannot write a single byte.  It answers three
questions:

  1. which tables carry the review and the broadcast records;
  2. how many records each of them holds and how fresh the newest one is;
  3. which migration the store is standing on.

The findings are printed as JSON so the reading can be pasted into the
task documents.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STORE = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

REVIEW_TABLES = ("manager_v4_reviews",)
BROADCAST_TABLES = ("manager_v4_broadcast_requests", "manager_v4_broadcasts")


def open_read_only() -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % STORE.as_posix()
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def count_and_newest(
    connection: sqlite3.Connection, table: str, time_column: str
) -> dict:
    if table not in table_names(connection):
        return {"present": False}
    total = connection.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    newest = connection.execute(
        "SELECT MAX(%s) FROM %s" % (time_column, table)
    ).fetchone()[0]
    return {"present": True, "rows": total, "newest_%s" % time_column: newest}


def main() -> None:
    connection = open_read_only()
    try:
        names = table_names(connection)
        finding = {
            "store": str(STORE),
            "mode": "ro + PRAGMA query_only = ON",
            "table_count": len(names),
            "review_tables": [
                name for name in names if "review" in name or "closed_loop" in name
            ],
            "broadcast_tables": [name for name in names if "broadcast" in name],
        }
        for table in REVIEW_TABLES:
            finding[table] = count_and_newest(connection, table, "created_at")
        for table in BROADCAST_TABLES:
            finding[table] = count_and_newest(connection, table, "created_at")
        try:
            finding["migrations"] = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM schema_migrations ORDER BY name DESC LIMIT 5"
                ).fetchall()
            ]
        except sqlite3.Error as error:
            finding["migrations"] = "unreadable: %s" % error
        print(json.dumps(finding, ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
