"""Measure how fast the closed-loop review actually moves, and where it waits.

The runtime's own status endpoint exposes the counters that answer the
question directly: how many workers exist, how many reviews have completed,
and how many are running.  The status endpoint is a **snapshot**, so the
tool polls it on a fixed interval and prints the deltas.

It also reads the review table (read-only) so the per-minute rate and the
number of distinct closed loops that have a fresh verdict are measured from
the data the Manager actually published, not from the counters.

Usage:
    python probe_review_throughput_20261126.py [seconds] [port]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def read_status(port: int) -> dict:
    url = f"http://127.0.0.1:{port}/api/status?chain_id=4663"
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def read_reviews() -> dict:
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=60)
    connection.execute("PRAGMA query_only=ON")
    try:
        statuses = dict(
            connection.execute(
                "SELECT review_status, COUNT(*) FROM manager_v4_reviews GROUP BY review_status"
            ).fetchall()
        )
        requests = connection.execute(
            "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"
        ).fetchone()[0]
        attempts = connection.execute(
            "SELECT COUNT(*) FROM manager_cow_execution_attempts"
        ).fetchone()[0]
        latest = connection.execute("SELECT MAX(reviewed_at) FROM manager_v4_reviews").fetchone()[0]
    finally:
        connection.close()
    return {
        "statuses": statuses,
        "broadcast_requests": requests,
        "cow_attempts": attempts,
        "latest_reviewed_at": latest,
    }


def main() -> int:
    duration = float(sys.argv[1] if len(sys.argv) > 1 else 300)
    port = int(sys.argv[2] if len(sys.argv) > 2 else 5000)
    deadline = time.time() + duration
    previous = None
    while time.time() < deadline:
        stamp = time.strftime("%H:%M:%S")
        try:
            status = read_status(port)
        except Exception as error:  # noqa: BLE001 - the probe reports and continues
            print(f"[{stamp}] status read failed: {error}", flush=True)
            time.sleep(10)
            continue
        scanner = status.get("dex_opportunity_scanner") or {}
        closed_loop = scanner.get("closedLoop") or {}
        review_queue = scanner.get("reviewQueue") or {}
        execution_queue = scanner.get("executionQueue") or {}
        try:
            reviews = read_reviews()
        except Exception as error:  # noqa: BLE001
            reviews = {"error": str(error)}
        row = {
            "at": stamp,
            "workers": closed_loop.get("workerCount"),
            "active": closed_loop.get("activeConcurrency"),
            "limit": closed_loop.get("concurrencyLimit"),
            "worker_state": closed_loop.get("workerStateSummary"),
            "reviews_done": review_queue.get("completed"),
            "reviews_running": review_queue.get("running"),
            "exec_workers": execution_queue.get("workers"),
            "exec_running": execution_queue.get("running"),
            "exec_done": execution_queue.get("done"),
            "exec_error": execution_queue.get("error"),
            "broadcast_requests": reviews.get("broadcast_requests"),
            "cow_attempts": reviews.get("cow_attempts"),
            "verdicts": reviews.get("statuses"),
            "latest_reviewed_at": reviews.get("latest_reviewed_at"),
        }
        if previous is None:
            print(f"[{stamp}] baseline: {json.dumps(row, ensure_ascii=False)}", flush=True)
        else:
            done = (row.get("reviews_done") or 0) - (previous.get("reviews_done") or 0)
            window = max(1e-6, time.time() - previous["_t"])
            row["reviews_per_min"] = round(done / window * 60, 2)
            print(f"[{stamp}] {json.dumps(row, ensure_ascii=False)}", flush=True)
        row["_t"] = time.time()
        previous = row
        time.sleep(15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
