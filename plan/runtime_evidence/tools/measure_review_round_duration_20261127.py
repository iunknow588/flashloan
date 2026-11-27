"""Measure how long one review round takes on the mainnet catalog.

The sweep used to judge a closed loop by the **age** of its measurement:
``now - reviewed_at > 60 s`` meant "the loop went quiet".  That age is
not the time the market moved -- it is the time the round itself took,
from the moment the route was measured until the measurement was written
back.  The tool reads the production store (read only) and reports the
two moments that separate the two things:

* ``last_reviewed_at`` -- the moment the route was measured;
* ``next_review_at``    -- the moment the loop is scheduled at, written
  by the helper that sets the review time point: the **end of the
  round** plus the review interval.

From the two the round duration follows, and with it the number of loops
the old window would have declared quiet.

Usage::

    python measure_review_round_duration_20261127.py \
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


def _seconds(later: dt.datetime | None, earlier: dt.datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def measure(database: Path, network: str, chain_id: int,
             interval_seconds: float, window_seconds: float) -> dict:
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = list(connection.execute(
            """SELECT loop_key, runtime_status, last_reviewed_at, next_review_at,
                      review_cycle_started_at
                 FROM manager_v4_closed_loop_runtime_state
                WHERE network = ?1 AND chain_id = ?2
                ORDER BY loop_key""", (network, chain_id)))
    finally:
        connection.close()

    rounds: list[dict] = []
    for loop_key, status, measured_at, scheduled_at, cycle_started_at in rows:
        measured = _moment(measured_at)
        scheduled = _moment(scheduled_at)
        # The scheduling helper writes ``end of the round + interval``.
        # The end of the round is therefore the scheduled moment minus the
        # interval, and the round duration is the distance from the
        # measurement to that end.
        round_end = scheduled - dt.timedelta(seconds=interval_seconds) \
            if scheduled is not None else None
        duration = _seconds(round_end, measured)
        rounds.append({
            "loopKey": loop_key,
            "runtimeStatus": status,
            "lastReviewedAt": measured_at,
            "nextReviewAt": scheduled_at,
            "reviewCycleStartedAt": cycle_started_at,
            "roundDurationSeconds": round(duration, 3) if duration is not None else None,
            # The old judgement: the age of the measurement measured from
            # the end of the round.  Every loop whose round took longer
            # than the window was declared quiet the moment the sweep ran.
            "oldWindowVerdict": (
                "stale" if duration is not None and duration > window_seconds
                else ("unknown" if duration is None else "current")),
            # The moment judgement: the measurement belongs to the round
            # that started at ``review_cycle_started_at``.
            "momentVerdict": (
                "current"
                if measured is not None and cycle_started_at is not None
                and measured >= (_moment(cycle_started_at) or measured)
                else ("waiting_for_its_round" if cycle_started_at is None
                      else "round_not_measured")),
        })

    durations = [row["roundDurationSeconds"] for row in rounds
                 if row["roundDurationSeconds"] is not None]
    return {
        "database": str(database),
        "network": network,
        "chainId": chain_id,
        "reviewIntervalSeconds": interval_seconds,
        "oldWindowSeconds": window_seconds,
        "loopCount": len(rounds),
        "roundDurationSeconds": {
            "min": round(min(durations), 3) if durations else None,
            "max": round(max(durations), 3) if durations else None,
            "mean": round(sum(durations) / len(durations), 3) if durations else None,
        },
        "oldWindowWouldDeclareStale": sum(
            1 for row in rounds if row["oldWindowVerdict"] == "stale"),
        "momentVerdictCounts": {
            verdict: sum(1 for row in rounds if row["momentVerdict"] == verdict)
            for verdict in sorted({row["momentVerdict"] for row in rounds})
        },
        "loops": rounds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path,
                        default=Path("flashloan/db/local_store_4663.sqlite3"))
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--window-seconds", type=float, default=60.0)
    arguments = parser.parse_args()
    import json
    print(json.dumps(
        measure(arguments.database, arguments.network, arguments.chain_id,
                arguments.interval_seconds, arguments.window_seconds),
        ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
