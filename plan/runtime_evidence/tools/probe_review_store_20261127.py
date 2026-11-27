#!/usr/bin/env python3
"""Read-only probe of the review store.

Answers three questions the concurrent review round depends on:
* which index actually serves the review window,
* how many rows the table carries,
* how fresh the newest review is.
"""
import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    db = Path(sys.argv[1] if len(sys.argv) > 1 else
              "flashloan/db/local_store_4663.sqlite3").resolve()
    uri = "file:" + db.as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.execute("PRAGMA query_only=ON")
    out: dict = {"database": str(db)}
    try:
        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='cow_execution_reviews' ORDER BY name").fetchall()
        out["indexes"] = [{"name": r[0], "sql": r[1]} for r in rows]
        out["rowCount"] = connection.execute(
            "SELECT COUNT(*) FROM cow_execution_reviews").fetchone()[0]
        newest = connection.execute(
            "SELECT id, created_at FROM cow_execution_reviews "
            "WHERE network=? AND chain_id=? ORDER BY created_at DESC LIMIT 1",
            ("robinhood", 4663)).fetchone()
        out["newest"] = {"id": newest[0], "createdAt": newest[1]} if newest else None
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM cow_execution_reviews "
            "WHERE network=? AND chain_id=? ORDER BY created_at DESC LIMIT 400",
            ("robinhood", 4663)).fetchall()
        out["planWindow"] = [list(r) for r in plan]
        plan2 = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM cow_execution_reviews "
            "WHERE network=? AND chain_id=? AND created_at > ? "
            "ORDER BY created_at DESC",
            ("robinhood", 4663, "2000-01-01")).fetchall()
        out["planDelta"] = [list(r) for r in plan2]
        plan3 = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM cow_execution_reviews "
            "WHERE network=? AND chain_id=? ORDER BY id DESC LIMIT 400",
            ("robinhood", 4663)).fetchall()
        out["planIdOrder"] = [list(r) for r in plan3]
        import time
        started = time.perf_counter()
        connection.execute(
            "SELECT id,created_at,pair,category,payload_json,summary_json,"
            "selected_profit_raw,selected_profit_amount,selected_profit_amount_symbol,"
            "selected_profit_source,selected_profit_missing_reason "
            "FROM cow_execution_reviews WHERE network=? AND chain_id=? "
            "ORDER BY id DESC LIMIT 400", ("robinhood", 4663)).fetchall()
        out["idOrderWindowSeconds"] = round(time.perf_counter() - started, 4)
        started = time.perf_counter()
        connection.execute(
            "SELECT id,created_at,pair,category,payload_json,summary_json,"
            "selected_profit_raw,selected_profit_amount,selected_profit_amount_symbol,"
            "selected_profit_source,selected_profit_missing_reason "
            "FROM cow_execution_reviews WHERE network=? AND chain_id=? "
            "ORDER BY created_at DESC LIMIT 400", ("robinhood", 4663)).fetchall()
        out["createdAtOrderWindowSeconds"] = round(time.perf_counter() - started, 4)
        for limit in (400, 1000, 2000, 4000):
            started = time.perf_counter()
            connection.execute(
                "SELECT id,created_at,payload_json,summary_json FROM cow_execution_reviews "
                "WHERE network=? AND chain_id=? ORDER BY created_at DESC LIMIT ?",
                ("robinhood", 4663, limit)).fetchall()
            out[f"createdAtOrderLimit{limit}Seconds"] = round(
                time.perf_counter() - started, 4)
        out["createdAtNotUtcOffset"] = connection.execute(
            "SELECT COUNT(*) FROM cow_execution_reviews "
            "WHERE created_at NOT LIKE '%+00:00'").fetchone()[0]
        out["createdAtNullOrEmpty"] = connection.execute(
            "SELECT COUNT(*) FROM cow_execution_reviews "
            "WHERE created_at IS NULL OR created_at=''").fetchone()[0]
        out["createdAtMinMax"] = list(connection.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM cow_execution_reviews").fetchone())
        try:
            counts = connection.execute(
                "SELECT COUNT(*) FROM manager_v4_reviews").fetchone()[0]
            out["managerV4ReviewRows"] = counts
            latest = connection.execute(
                "SELECT MAX(reviewed_at) FROM manager_v4_reviews").fetchone()[0]
            out["managerV4LatestReviewedAt"] = latest
        except sqlite3.Error as error:
            out["managerV4Error"] = str(error)
    finally:
        connection.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
