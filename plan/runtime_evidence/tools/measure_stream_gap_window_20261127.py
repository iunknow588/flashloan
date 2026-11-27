#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gap profile of the review stream inside a clean time window.

``manager_v4_reviews`` keeps **one row per closed loop** and a second review
overwrites it, so the history of a round is erased as the round runs.  The
legacy ``cow_execution_reviews`` table is append-only, so it is the only
table that still holds the true sequence of review moments and therefore the
only one that can measure the dead time between two consecutive reviews.

The window is a command-line argument so the same script can be pointed at a
quiet stretch of the stream; a stretch that overlaps another reader of the
store measures that reader, not the review loop.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path("flashloan/db/local_store_4663.sqlite3").resolve()


def parse(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--end", default="",
                        help="ISO timestamp the window ends at (default: now)")
    arguments = parser.parse_args()

    end = parse(arguments.end) if arguments.end else datetime.now(timezone.utc)
    if end is None:
        print(json.dumps({"error": f"unparsable --end: {arguments.end}"}))
        return 1
    start = end - timedelta(minutes=arguments.minutes)

    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=120)
    connection.execute("PRAGMA query_only=ON")
    rows = connection.execute(
        "SELECT created_at FROM cow_execution_reviews "
        "WHERE network='robinhood' AND chain_id=4663 "
        "AND datetime(created_at)>=datetime(?) AND datetime(created_at)<datetime(?) "
        "ORDER BY datetime(created_at)",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    connection.close()

    stamps = [stamp for (value,) in rows if (stamp := parse(value)) is not None]
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    span = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0
    long_gaps = [gap for gap in gaps if gap > 8.0]
    print(json.dumps({
        "windowStart": start.isoformat(),
        "windowEnd": end.isoformat(),
        "windowMinutes": arguments.minutes,
        "reviews": len(stamps),
        "first": stamps[0].isoformat() if stamps else None,
        "last": stamps[-1].isoformat() if stamps else None,
        "spanSeconds": round(span, 3),
        "gapCount": len(gaps),
        "gapMedianSeconds": round(statistics.median(gaps), 3) if gaps else None,
        "gapMeanSeconds": round(statistics.fmean(gaps), 3) if gaps else None,
        "gapMaxSeconds": round(max(gaps), 3) if gaps else None,
        "gapsOver8sCount": len(long_gaps),
        "gapsOver8sSeconds": round(sum(long_gaps), 3),
        "gapsOver8sSharePercent": round(100.0 * sum(long_gaps) / span, 3) if span else None,
        "reviewsPerMinute": round(len(stamps) / arguments.minutes, 3) if arguments.minutes else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
