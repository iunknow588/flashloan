"""Measure one review round of the running runtime, read-only.

The question the script answers is the one the round duration does not
answer by itself: a round that takes minutes may be slow because the
reviews are slow, or because the round spends its time **waiting**.

The script never writes to the store: it opens the database in read-only
mode and turns query_only on.

Usage:

    python plan/runtime_evidence/tools/diagnose_round_timing_20261127.py
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
STORE = ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"


def parse(value):
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def open_store():
    if not STORE.exists():
        sys.exit(f"the store is missing: {STORE}")
    connection = sqlite3.connect(f"file:{STORE.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def rounds(connection):
    """The rounds of the store, one row per closed-loop generation.

    A generation is the directory the reviews were run against, so the
    reviews of one generation are one round.
    """
    rows = connection.execute(
        """
        SELECT closed_loop_version, COUNT(*), MIN(reviewed_at), MAX(reviewed_at)
        FROM manager_v4_reviews
        WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
        GROUP BY closed_loop_version
        ORDER BY MIN(reviewed_at)
        """,
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
    ).fetchall()
    found = []
    for version, count, first, last in rows:
        start = parse(first)
        end = parse(last)
        if start is None or end is None:
            continue
        found.append(
            {
                "version": version,
                "loops": count,
                "first": start,
                "last": end,
                "span_seconds": (end - start).total_seconds(),
            }
        )
    return found


def gap_profile(connection, version):
    """Where the round spent its time, gap by gap.

    The gaps are the intervals between two consecutive reviews of the
    round.  A gap that is long is a **wait**, not a review: the workers
    were not measuring, they were waiting for something.
    """
    rows = connection.execute(
        """
        SELECT reviewed_at FROM manager_v4_reviews
        WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
          AND closed_loop_version = ?5
        ORDER BY reviewed_at
        """,
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE, version),
    ).fetchall()
    stamps = [parse(row[0]) for row in rows]
    stamps = [stamp for stamp in stamps if stamp is not None]
    gaps = []
    for before, after in zip(stamps, stamps[1:]):
        gaps.append((after - before).total_seconds())
    if not gaps:
        return {"reviews": len(stamps), "gaps": 0}
    ordered = sorted(gaps)
    total = sum(gaps)
    over = [gap for gap in gaps if gap > 8.0]
    return {
        "reviews": len(stamps),
        "gaps": len(gaps),
        "median_gap_seconds": ordered[len(ordered) // 2],
        "mean_gap_seconds": total / len(gaps),
        "max_gap_seconds": max(gaps),
        "gaps_over_8s": len(over),
        "seconds_in_gaps_over_8s": sum(over),
        "share_in_gaps_over_8s": (sum(over) / total) if total else 0.0,
    }


def broadcast_state(connection):
    requests = connection.execute(
        """
        SELECT COUNT(*) FROM manager_v4_review_broadcast_requests
        WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
        """,
        (NETWORK, CHAIN_ID, PROTOCOL),
    ).fetchone()[0]
    return {"review_broadcast_requests": requests}


def positive_reviews(connection):
    """The reviews the chain itself called profitable.

    The verdict is the one the contract gave: the run produced a
    positive profit.  Whether the request may be broadcast is a
    **separate** question, answered by the engineering gates.
    """
    rows = connection.execute(
        """
        SELECT loop_key, anchor_symbol, reviewed_at, review_status, review_reason,
               signed_pnl_raw, signed_pnl_amount, signed_pnl_currency
        FROM manager_v4_reviews
        WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
          AND signed_pnl_amount > 0
        ORDER BY reviewed_at
        """,
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
    ).fetchall()
    found = []
    for row in rows:
        found.append(
            {
                "loop_key": row[0],
                "anchor": row[1],
                "reviewed_at": row[2],
                "status": row[3],
                "reason": row[4],
                "signed_pnl_raw": row[5],
                "signed_pnl_amount": row[6],
                "currency": row[7],
            }
        )
    return found


def main():
    connection = open_store()
    try:
        found = rounds(connection)
        if not found:
            sys.exit("the store carries no review round")
        # The round with the most reviews is the one that ran in full.
        biggest = max(found, key=lambda entry: entry["loops"])
        latest = found[-1]
        profile = gap_profile(connection, biggest["version"])
        positives = positive_reviews(connection)
        report = {
            "store": str(STORE),
            "rounds": [
                {
                    "version": entry["version"],
                    "loops": entry["loops"],
                    "first": entry["first"].isoformat(),
                    "last": entry["last"].isoformat(),
                    "span_seconds": round(entry["span_seconds"], 1),
                }
                for entry in found
            ],
            "biggest_round": {
                "version": biggest["version"],
                "loops": biggest["loops"],
                "first": biggest["first"].isoformat(),
                "last": biggest["last"].isoformat(),
                "span_seconds": round(biggest["span_seconds"], 1),
                "gap_profile": {
                    key: (round(value, 3) if isinstance(value, float) else value)
                    for key, value in profile.items()
                },
            },
            "latest_round": {
                "version": latest["version"],
                "loops": latest["loops"],
                "first": latest["first"].isoformat(),
                "last": latest["last"].isoformat(),
                "span_seconds": round(latest["span_seconds"], 1),
            },
            "broadcast": broadcast_state(connection),
            "positive_reviews": positives,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
