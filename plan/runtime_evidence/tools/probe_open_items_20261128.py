"""Read the live store for the open-item analysis.

Read-only: the store is opened with ``mode=ro`` and ``query_only`` so the
probe cannot change a byte of the production database.
"""

import json
import sqlite3
from pathlib import Path

DB = Path("flashloan/db/local_store_4663.sqlite3")


def rows(sql: str, params: tuple = ()) -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def main() -> None:
    tables = [row["name"] for row in rows(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    review_like = [t for t in tables
                   if "review" in t or "broadcast" in t or "arbitrage" in t]
    report: dict[str, object] = {
        "tableCount": len(tables),
        "reviewBroadcastTables": review_like,
    }
    counts: dict[str, int] = {}
    for table in review_like:
        try:
            counts[table] = rows(f"SELECT COUNT(*) n FROM {table}")[0]["n"]
        except sqlite3.Error as error:
            counts[table] = f"error: {error}"
    report["rowCounts"] = counts
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
