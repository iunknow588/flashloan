"""Read-only check of the live parity-state table (T-池复.01 materialization_batch)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"


def main() -> None:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only = ON")
    cur = conn.cursor()
    names = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in sorted(n for n in names if "parity" in n):
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
        print(f"\n== {table} ==")
        print(f"   cols = {cols}")
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"   rows = {n}")
        except Exception as exc:
            print("   ERROR", exc)
            continue
        if "materialization_batch" in cols:
            rows = cur.execute(
                f"SELECT materialization_batch, COUNT(*) FROM {table} "
                "GROUP BY materialization_batch ORDER BY 2 DESC LIMIT 8"
            ).fetchall()
            print("   materialization_batch groups:")
            for row in rows:
                print("     ", row)
        else:
            print("   materialization_batch: COLUMN ABSENT")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
