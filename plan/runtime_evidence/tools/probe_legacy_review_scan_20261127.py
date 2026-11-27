#!/usr/bin/env python3
"""Measure the cost of the legacy review scan the healer report runs.

The healer report reads the legacy review table with a window filter that
wraps the indexed column in ``datetime()``.  A wrapped column is not
sargable, so the whole table is read.  This probe measures the wrapped
query against the two variants that keep the index usable, and it prints
the query plan of each one.  The read is read-only and query-only.

Usage:
  probe_legacy_review_scan_20261127.py [--hours 24] [--limit 5000]
"""
import argparse
import json
import sqlite3
import time


DATABASE = "file:E:/2026OPC大赛/flashLoan/flashloan/db/local_store_4663.sqlite3?mode=ro"
COLUMNS = (
    "id,attempt_key,category,pair,tx_hash,created_at,payload_json,"
    "selected_profit_raw,selected_profit_amount,selected_profit_amount_symbol,"
    "selected_profit_source,selected_profit_missing_reason"
)


def timed(cur: sqlite3.Cursor, label: str, sql: str, params: tuple) -> dict:
    started = time.monotonic()
    rows = cur.execute(sql, params).fetchall()
    elapsed = time.monotonic() - started
    plan = [str(row) for row in cur.execute("EXPLAIN QUERY PLAN " + sql, params)]
    return {"label": label, "rows": len(rows), "elapsedSeconds": round(elapsed, 3),
            "plan": plan}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()

    con = sqlite3.connect(DATABASE, uri=True)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()
    out: dict = {
        "tableRows": cur.execute("SELECT COUNT(*) FROM cow_execution_reviews").fetchone()[0],
        "measurements": [],
    }
    base = (f"SELECT {COLUMNS} FROM cow_execution_reviews "
            "WHERE network=? AND chain_id=? AND created_at>=datetime('now',?) ")
    wrapped = base + "AND datetime(created_at)>=datetime(?) ORDER BY created_at DESC LIMIT ?"
    sargable = base + "ORDER BY created_at DESC LIMIT ?"
    by_id = base + "ORDER BY id DESC LIMIT ?"
    window = f"-{args.hours} hours"
    out["measurements"].append(timed(
        cur, "datetime(created_at) filter (current healer query)", wrapped,
        ("robinhood", 4663, window, window, args.limit)))
    out["measurements"].append(timed(
        cur, "sargable created_at filter, ORDER BY created_at DESC", sargable,
        ("robinhood", 4663, window, args.limit)))
    out["measurements"].append(timed(
        cur, "sargable created_at filter, ORDER BY id DESC", by_id,
        ("robinhood", 4663, window, args.limit)))
    con.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
