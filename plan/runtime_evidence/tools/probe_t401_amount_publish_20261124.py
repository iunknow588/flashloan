#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch the review library for the published best-trade-amount evidence.

The Rust publisher used to drop the amount evidence on the way into the
Manager review, so every stored review looked like a review that never
measured a trade amount.  The publisher now carries it, and this probe
reports when the library actually holds a generation that has it.
"""

import json
import os
import sqlite3
import sys

REPO = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
DB = os.path.join(REPO, "flashloan", "db", "local_store_4663.sqlite3")


def main() -> int:
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    state = connection.execute(
        "SELECT review_version, closed_loop_version, loop_count, reviewed_count,"
        " generated_at FROM manager_review_library_state"
    ).fetchone()
    print("library state:", json.dumps(dict(state), ensure_ascii=False))
    rows = connection.execute(
        "SELECT loop_key, review_status, static_preview_json, run_arbitrage_json"
        " FROM manager_v4_reviews"
    ).fetchall()
    with_block = 0
    measured = 0
    ladder = 0
    sample = None
    for row in rows:
        try:
            preview = json.loads(row["static_preview_json"] or "{}")
        except Exception:
            continue
        block = preview.get("bestAmountCalculation")
        if not isinstance(block, dict):
            continue
        with_block += 1
        if block.get("bestAmountMeasured") is True:
            measured += 1
        probes = block.get("probeRecords")
        if isinstance(probes, list) and probes:
            ladder += 1
            if sample is None:
                sample = {
                    "loopKey": row["loop_key"],
                    "reviewStatus": row["review_status"],
                    "calculation": block,
                }
    print(
        f"reviews={len(rows)} withBestAmountCalculation={with_block} "
        f"measured={measured} withLadder={ladder}"
    )
    if sample is not None:
        print("sample:", json.dumps(sample, ensure_ascii=False)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
