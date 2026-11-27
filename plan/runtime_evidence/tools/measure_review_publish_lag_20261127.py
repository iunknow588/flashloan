"""Measure how long the review of a closed loop takes to reach the store.

The runtime broadcast path used to judge a published broadcast request by
its **age**: a request older than 120 seconds was refused.  The age of a
request counts the time the review itself took -- from the moment the
route was measured until the moment the measurement was written back --
and that time is not time the market moved.

This tool reads the production store (read only) and reports the lag of
the reviews of the current catalogue, together with the window the old
judgement would have refused.

Usage::

    python measure_review_publish_lag_20261127.py \
        --database flashloan/db/local_store_4663.sqlite3
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path


def _moment(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def measure(database: Path, network: str, chain_id: int, window: float) -> None:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = list(connection.execute(
            """SELECT r.loop_key, r.reviewed_at, r.created_at, r.review_status,
                      r.signed_pnl_amount, s.next_review_at
                 FROM manager_v4_reviews r
                 LEFT JOIN manager_v4_closed_loop_runtime_state s
                   ON s.loop_key = r.loop_key AND s.network = r.network
                  AND s.chain_id = r.chain_id
                WHERE r.network = ?1 AND r.chain_id = ?2
                ORDER BY r.created_at DESC""",
            (network, chain_id)))
    finally:
        connection.close()

    lags: list[float] = []
    schedule_gaps: list[float] = []
    current = 0
    print("%-22s %-34s %-34s %9s %9s %s" % (
        "loop", "measured_at", "written_at", "lag[s]", "next-gap[s]", "status"))
    for loop_key, reviewed_at, created_at, status, pnl, next_review_at in rows:
        measured = _moment(reviewed_at)
        written = _moment(created_at)
        if measured is None or written is None:
            continue
        lag = (written - measured).total_seconds()
        gap = None
        scheduled = _moment(next_review_at)
        if scheduled is not None:
            gap = (scheduled - written).total_seconds()
            schedule_gaps.append(gap)
        lags.append(lag)
        current += 1
        if current <= 12:
            print("%-22s %-34s %-34s %9.3f %9s %s" % (
                loop_key[-18:], reviewed_at, created_at, lag,
                "%.3f" % gap if gap is not None else "-", status))
    if not lags:
        print("no review rows carry a measured moment")
        return
    print()
    print("reviews measured            : %d" % len(lags))
    print("lag min / mean / max [s]   : %.3f / %.3f / %.3f" % (
        min(lags), sum(lags) / len(lags), max(lags)))
    print("the old %ds window refuses  : %d" % (
        window, sum(1 for lag in lags if lag > window)))
    if schedule_gaps:
        print("next_review_at - written   : %.3f / %.3f / %.3f (min/mean/max)" % (
            min(schedule_gaps), sum(schedule_gaps) / len(schedule_gaps),
            max(schedule_gaps)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--window", type=float, default=120.0)
    arguments = parser.parse_args()
    measure(arguments.database, arguments.network, arguments.chain_id, arguments.window)


if __name__ == "__main__":
    main()
