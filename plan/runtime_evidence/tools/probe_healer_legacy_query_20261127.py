#!/usr/bin/env python3
# Measure the exact legacy review query the healer runs per cycle.
#
# reviews_missing_a_value and missing_pnl_review_candidates both call
# review_rows without a manager API, so they fall back to the legacy
# cow_execution_reviews table.  That query wraps the indexed column in
# datetime() and caps the result at 20000 rows.  This probe runs the
# query exactly as the healer builds it and then parses every
# payload_json it returns, because the healer parses each of them once
# per audit pass.  The read is read-only and query-only.
#
# Usage:
#   probe_healer_legacy_query_20261127.py [--epoch 2026-11-10T19:57:50Z]
import argparse
import json
import sqlite3
import time


DATABASE = "file:E:/2026OPC大赛/flashLoan/flashloan/db/local_store_4663.sqlite3?mode=ro"
QUERY = (
    "SELECT id,attempt_key,category,pair,tx_hash,created_at,payload_json,"
    "selected_profit_raw,selected_profit_amount,selected_profit_amount_symbol,"
    "selected_profit_source,selected_profit_missing_reason "
    "FROM cow_execution_reviews "
    "WHERE network=? AND chain_id=? AND created_at>=datetime('now',?) "
    "AND datetime(created_at)>=datetime(?) ORDER BY created_at DESC LIMIT 20000"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epoch", default="2026-11-10T19:57:50Z")
    args = parser.parse_args()

    con = sqlite3.connect(DATABASE, uri=True)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()
    out: dict = {
        "tableRows": cur.execute("SELECT COUNT(*) FROM cow_execution_reviews").fetchone()[0],
        "rowsInLast24h": cur.execute(
            "SELECT COUNT(*) FROM cow_execution_reviews "
            "WHERE network='robinhood' AND chain_id=4663 "
            "AND created_at>=datetime('now','-24 hours')").fetchone()[0],
    }
    started = time.monotonic()
    rows = cur.execute(QUERY, ("robinhood", 4663, "-24 hours", args.epoch)).fetchall()
    out["querySeconds"] = round(time.monotonic() - started, 3)
    out["rows"] = len(rows)

    started = time.monotonic()
    parsed = 0
    payload_bytes = 0
    for row in rows:
        raw = row[6]
        if not raw:
            continue
        payload_bytes += len(raw)
        try:
            json.loads(raw)
            parsed += 1
        except ValueError:
            pass
    out["payloadsParsed"] = parsed
    out["payloadBytes"] = payload_bytes
    out["parseSeconds"] = round(time.monotonic() - started, 3)
    con.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
