#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the per-round gap profile of the review stream, per generation.

The question this answers is narrow and factual: how long does a review round
take, and how much of it is dead time between two consecutive reviews.  The
read is read-only (``mode=ro`` plus ``PRAGMA query_only=ON``) so it never
touches the live store.

The round is identified by the closed-loop generation the review row belongs
to.  Every row of one generation belongs to one round, so the round span is
``max(reviewed_at) - min(reviewed_at)`` and the gaps are the differences
between consecutive ``reviewed_at`` values of the same generation.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path("flashloan/db/local_store_4663.sqlite3").resolve()

#: The moment the single-flight snapshot build was deployed.  The runtime
#: that carries it started at 2026-11-27 01:53:52 +08:00.
DEPLOYED_AT = datetime(2026, 11, 26, 17, 53, 52, tzinfo=timezone.utc)


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
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=120)
    connection.execute("PRAGMA query_only=ON")
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(manager_v4_reviews)")}
    except sqlite3.Error as error:
        print(json.dumps({"error": f"table unavailable: {error}"}))
        return 1
    generation = "closed_loop_version"
    if generation not in columns:
        print(json.dumps({"error": f"column {generation} missing", "columns": sorted(columns)}))
        return 1
    rows = connection.execute(
        f"SELECT {generation} AS generation, reviewed_at FROM manager_v4_reviews "
        "WHERE chain_id=4663 AND reviewed_at IS NOT NULL "
        "ORDER BY reviewed_at"
    ).fetchall()
    connection.close()

    rounds: dict[int, list[datetime]] = {}
    for generation_value, reviewed_at in rows:
        stamp = parse(reviewed_at)
        if stamp is None:
            continue
        rounds.setdefault(int(generation_value), []).append(stamp)

    out = []
    for generation_value in sorted(rounds):
        stamps = sorted(rounds[generation_value])
        gaps = [
            (later - earlier).total_seconds()
            for earlier, later in zip(stamps, stamps[1:])
        ]
        span = (stamps[-1] - stamps[0]).total_seconds()
        long_gaps = [gap for gap in gaps if gap > 8.0]
        out.append({
            "generation": generation_value,
            "reviews": len(stamps),
            "first": stamps[0].isoformat(),
            "last": stamps[-1].isoformat(),
            "spanSeconds": round(span, 3),
            "gapCount": len(gaps),
            "gapMedianSeconds": round(statistics.median(gaps), 3) if gaps else None,
            "gapMeanSeconds": round(statistics.fmean(gaps), 3) if gaps else None,
            "gapMaxSeconds": round(max(gaps), 3) if gaps else None,
            "gapsOver8sCount": len(long_gaps),
            "gapsOver8sSeconds": round(sum(long_gaps), 3),
            "gapsOver8sSharePercent": round(100.0 * sum(long_gaps) / span, 3) if span else None,
        })
    # The whole-stream profile.  A closed loop owns exactly one review row and
    # a second review overwrites it, so a round is the interval between two
    # reviews of the same loop -- not the number the row happens to carry.
    # The generation number therefore cannot be used as the round identity.
    per_loop: dict[str, list[datetime]] = {}
    try:
        connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=120)
        connection.execute("PRAGMA query_only=ON")
        for loop_key, reviewed_at in connection.execute(
            "SELECT loop_key, reviewed_at FROM manager_v4_reviews "
            "WHERE chain_id=4663 AND reviewed_at IS NOT NULL"
        ):
            stamp = parse(reviewed_at)
            if stamp is not None:
                per_loop.setdefault(str(loop_key), []).append(stamp)
        connection.close()
    except sqlite3.Error as error:
        out.append({"perLoopError": str(error)})

    cycle = []
    for loop_key, stamps in per_loop.items():
        stamps.sort()
        for earlier, later in zip(stamps, stamps[1:]):
            cycle.append((later - earlier).total_seconds())

    def profile(stamps: list[datetime]) -> dict[str, object]:
        stamps = sorted(stamps)
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        span = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0
        long_gaps = [gap for gap in gaps if gap > 8.0]
        return {
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
        }

    before_stamps = [stamp for stamps in rounds.values() for stamp in stamps if stamp < DEPLOYED_AT]
    after_stamps = [stamp for stamps in rounds.values() for stamp in stamps if stamp >= DEPLOYED_AT]
    out.append({"streamBeforeDeployment": profile(before_stamps)})
    out.append({"streamAfterDeployment": profile(after_stamps)})
    out.append({
        "perLoopReReviewCycle": {
            "pairs": len(cycle),
            "medianSeconds": round(statistics.median(cycle), 3) if cycle else None,
            "meanSeconds": round(statistics.fmean(cycle), 3) if cycle else None,
            "maxSeconds": round(max(cycle), 3) if cycle else None,
            "minSeconds": round(min(cycle), 3) if cycle else None,
        }
    })
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
