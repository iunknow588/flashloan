#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch the live review table for a fixed window and report every profit.

The window exists because "no route is profitable" has two very different
causes: the market really is flat, or the pool selection / ranking is wrong.
The probe therefore does not only count the profit, it records **when** every
review was measured, so a flat window can be compared with the review
cadence, and it re-reads the single-tick evidence of the newest review so a
routing or ranking fault can be told apart from a missing measurement.

Read-only: the store is opened with ``mode=ro`` and ``query_only``; nothing
is written to the database, no review is requested and no broadcast is
attempted.  The only file this probe writes is its own JSONL trace.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def _raw(value: object) -> int | None:
    """Read a signed raw amount (hex string, decimal string or int)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return None


def _moment(value: object) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _single_tick_evidence(preview: object) -> dict[str, object]:
    """Read the single-tick / cross-tick evidence of one review."""
    if not isinstance(preview, dict):
        return {}
    calculation = preview.get("bestAmountCalculation")
    if not isinstance(calculation, dict):
        return {}
    return {
        key: calculation.get(key)
        for key in (
            "singleTickBestAmountRaw",
            "singleTickProfitable",
            "crossTickStarted",
            "crossTickReason",
            "crossTickBestAmountRaw",
            "crossTickBestAmountBasis",
            "bestAmountSelectionBasis",
        )
    }


def sample(connection: sqlite3.Connection, window_start: dt.datetime) -> dict[str, object]:
    """Read one sample of the live review state."""
    rows = connection.execute(
        "SELECT loop_key, anchor_symbol, review_status, signed_pnl_raw,"
        " signed_pnl_currency, reviewed_at, static_preview_json"
        " FROM manager_v4_reviews ORDER BY reviewed_at DESC"
    ).fetchall()

    in_window = []
    newest = None
    positive_all_time = []
    for row in rows:
        reviewed_at = row[5]
        moment = _moment(reviewed_at)
        raw = _raw(row[3])
        if raw is not None and raw > 0:
            positive_all_time.append(
                {
                    "loopKey": row[0],
                    "anchorSymbol": row[1],
                    "reviewStatus": row[2],
                    "signedPnlRaw": row[3],
                    "signedPnlCurrency": row[4],
                    "reviewedAt": reviewed_at,
                }
            )
        if newest is None:
            newest = {
                "loopKey": row[0],
                "anchorSymbol": row[1],
                "reviewStatus": row[2],
                "signedPnlRaw": row[3],
                "signedPnlCurrency": row[4],
                "reviewedAt": reviewed_at,
                "singleTick": _single_tick_evidence(
                    json.loads(row[6]) if row[6] else None
                ),
            }
        if moment is not None and moment >= window_start:
            in_window.append(
                {
                    "loopKey": row[0],
                    "anchorSymbol": row[1],
                    "reviewStatus": row[2],
                    "signedPnlRaw": row[3],
                    "signedPnlCurrency": row[4],
                    "reviewedAt": reviewed_at,
                }
            )

    positives = [row for row in in_window if (_raw(row["signedPnlRaw"]) or 0) > 0]
    negatives = [row for row in in_window if (_raw(row["signedPnlRaw"]) or 0) < 0]
    attempts = 0
    try:
        attempts = int(
            connection.execute(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts"
            ).fetchone()[0]
        )
    except sqlite3.Error:
        attempts = -1

    return {
        "measuredAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "windowStart": window_start.isoformat(),
        "reviewsInWindow": len(in_window),
        "positiveInWindow": len(positives),
        "negativeInWindow": len(negatives),
        "nonPositiveInWindow": len(in_window) - len(positives),
        "positives": positives,
        "newestReview": newest,
        "positiveReviewsAllTime": len(positive_all_time),
        "positiveReviewsAllTimeRows": positive_all_time[:10],
        "broadcastAttempts": attempts,
        "totalReviews": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--interval", type=float, default=60.0)
    arguments = parser.parse_args()

    database = Path(arguments.db)
    connection = sqlite3.connect(
        "file:" + database.resolve().as_posix() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")

    window_start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=arguments.minutes * 60)
    samples = []
    deadline = time.monotonic() + arguments.minutes * 60
    while True:
        samples.append(sample(connection, window_start))
        if time.monotonic() >= deadline:
            break
        time.sleep(min(arguments.interval, max(1.0, deadline - time.monotonic())))
    connection.close()

    report = {
        "windowMinutes": arguments.minutes,
        "intervalSeconds": arguments.interval,
        "windowStart": window_start.isoformat(),
        "windowEnd": dt.datetime.now(dt.timezone.utc).isoformat(),
        "samples": samples,
        "summary": {
            # Every sample reads the **same** window, so the counts overlap
            # and must not be summed; the last sample is the whole window.
            "reviewsMeasuredInWindow": samples[-1]["reviewsInWindow"] if samples else 0,
            "positiveInWindow": samples[-1]["positiveInWindow"] if samples else 0,
            "negativeInWindow": samples[-1]["negativeInWindow"] if samples else 0,
            "anyPositiveAtAll": any(item["positiveInWindow"] for item in samples),
            "positiveReviewsAllTime": samples[-1]["positiveReviewsAllTime"] if samples else 0,
            "broadcastAttempts": samples[-1]["broadcastAttempts"] if samples else -1,
        },
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8", newline="\n")
        print("wrote:", target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
