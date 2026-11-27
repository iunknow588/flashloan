"""Watch the live store for the first broadcast the review produced.

The chain is: the review publishes a positive-profit request -> the
runtime reads the proof back -> the broadcast worker signs and sends ->
the record lands in the main store and, after the archiver runs, in the
monthly trade store.  This watcher follows that chain and prints the
first sighting of every step, so a deployment can be judged by what the
store holds rather than by what it is expected to hold.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path


DB = Path("flashloan/db/local_store_4663.sqlite3").resolve()
MONTHLY_ROOT = Path("flashloan/db/broadcast_transaction_db").resolve()


def count(connection: sqlite3.Connection, table: str) -> int:
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return -1


def rows(connection: sqlite3.Connection, table: str, columns: str) -> list[dict[str, object]]:
    try:
        return [dict(row) for row in connection.execute(f"SELECT {columns} FROM {table}").fetchall()]
    except sqlite3.Error:
        return []


def main() -> int:
    deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 5400)
    seen: dict[str, bool] = {}
    while time.time() < deadline:
        connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        requests = count(connection, "manager_v4_review_broadcast_requests")
        attempts = count(connection, "manager_cow_execution_attempts")
        state = count(connection, "manager_broadcast_library_state")
        if requests > 0 and not seen.get("requests"):
            seen["requests"] = True
            print(f"[{time.strftime('%H:%M:%S')}] broadcast requests published: {requests}", flush=True)
            for row in rows(
                connection,
                "manager_v4_review_broadcast_requests",
                "loop_key, request_status, signed_pnl_raw, signed_pnl_amount, created_at",
            ):
                print("   " + json.dumps(row, ensure_ascii=False, default=str), flush=True)
        if attempts > 0 and not seen.get("attempts"):
            seen["attempts"] = True
            print(f"[{time.strftime('%H:%M:%S')}] broadcast attempts recorded: {attempts}", flush=True)
            for row in rows(
                connection,
                "manager_cow_execution_attempts",
                "attempt_id, loop_key, status, signed_pnl_raw, nonce, rpc_url, created_at",
            ):
                print("   " + json.dumps(row, ensure_ascii=False, default=str), flush=True)
        if state > 0 and not seen.get("state"):
            seen["state"] = True
            print(f"[{time.strftime('%H:%M:%S')}] broadcast library state published: {state}", flush=True)
        monthly = sorted(MONTHLY_ROOT.glob("**/trades.sqlite3")) if MONTHLY_ROOT.exists() else []
        if monthly and not seen.get("monthly"):
            seen["monthly"] = True
            print(f"[{time.strftime('%H:%M:%S')}] monthly trade store: {[str(p) for p in monthly]}", flush=True)
        connection.close()
        if len(seen) == 4:
            print(f"[{time.strftime('%H:%M:%S')}] the whole chain is on the store", flush=True)
            return 0
        time.sleep(45)
    print(f"[{time.strftime('%H:%M:%S')}] watch window over; seen: {sorted(seen)}", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
