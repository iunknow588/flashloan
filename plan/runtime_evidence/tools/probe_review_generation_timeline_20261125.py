"""Read-only probe: review generation timeline in the live store.

Answers one question: after the runtime restart, did the review Manager
publish a **new** generation, and if not, what is the last recorded
publish attempt and why did it stop?

The probe is strictly read-only: the store is opened with mode=ro and
PRAGMA query_only = ON.
"""

import json
import sqlite3
import sys
from pathlib import Path


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def main() -> int:
    store = Path(sys.argv[1] if len(sys.argv) > 1 else "flashloan/db/local_store_4663.sqlite3")
    conn = _ro(store)
    out: dict = {"store": str(store)}

    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    out["review_tables"] = [t for t in tables if "review" in t.lower()]
    out["operation_tables"] = [t for t in tables if "operation" in t.lower()]

    for table in out["review_tables"]:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")]
        count = conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        out.setdefault("review_table_counts", {})[table] = {
            "rows": count,
            "columns": cols,
        }

    for table in ("manager_review_library_state", "manager_v4_review_library_state"):
        if table not in tables:
            continue
        cols = [row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")]
        rows = conn.execute("SELECT * FROM " + table).fetchall()
        out["library_state"] = {
            "table": table,
            "columns": cols,
            "rows": [dict(zip(cols, row)) for row in rows],
        }

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
