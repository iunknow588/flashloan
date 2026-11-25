#!/usr/bin/env python3
"""Read-only probe: why does the single-tick gate report pool_state_version_drift?

The final single-tick gate compares the ``pool_state_version`` of the hops of
one route.  That value is fed from ``liquidity_read_at`` of the pool's newest
liquidity snapshot.  This probe prints, per blocked route, the timestamps of
every hop pool, so the comparison can be judged on measured data.

The live store is opened **read-only** (``mode=ro`` + ``PRAGMA query_only``).
Nothing is written.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
REVIEWS = "http://127.0.0.1:8091/v1/reviews?chainId=4663&limit=500"


def live_reviews() -> list[dict]:
    with urllib.request.urlopen(REVIEWS, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["reviews"]


def main() -> None:
    rows = live_reviews()
    blocked = []
    for row in rows:
        reason = str(row.get("reviewReason") or "")
        if "single_tick_gate_failed" in reason:
            blocked.append(
                {
                    "loopKey": row.get("loopKey"),
                    "pathText": row.get("pathText"),
                    "reason": reason,
                    "poolIds": row.get("poolIds"),
                    "signedPnlRaw": (row.get("signedPnl") or {}).get("raw"),
                    "signedPnlAmount": (row.get("signedPnl") or {}).get("amount"),
                    "selectedAmountRaw": (row.get("runArbitrage") or {}).get(
                        "selectedAmountRaw"
                    ),
                    "singleTickCapacityAnchorRaw": row.get("singleTickCapacityAnchorRaw"),
                }
            )

    pool_ids: list[str] = []
    for row in blocked:
        for pool_id in row["poolIds"] or []:
            if pool_id not in pool_ids:
                pool_ids.append(pool_id)

    uri = f"file:{DB.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        read_at: dict[str, list[str]] = {}
        chunk = 400
        for start in range(0, len(pool_ids), chunk):
            part = pool_ids[start : start + chunk]
            placeholders = ",".join("?" for _ in part)
            sql = (
                "SELECT pool_id, liquidity_read_at, evidence_source, materialization_batch, "
                "observed_at, refreshed_at "
                "FROM v4_exchange_pool_liquidity_snapshots "
                "WHERE network = 'robinhood' AND chain_id = 4663 "
                f"AND pool_id IN ({placeholders}) "
                "ORDER BY pool_id, rowid"
            )
            for pool_id, stamp, source, batch, observed, refreshed in connection.execute(sql, part):
                read_at.setdefault(pool_id, []).append(
                    f"{stamp} [{source}] batch={batch} observed={observed} refreshed={refreshed}"
                )
    finally:
        connection.close()

    print(f"== routes blocked by the single-tick gate: {len(blocked)} ==")
    for row in blocked:
        print(json.dumps(row, ensure_ascii=False, indent=2))
        for pool_id in row["poolIds"] or []:
            stamps = read_at.get(pool_id, [])
            newest = stamps[-1] if stamps else "<no snapshot row>"
            distinct = sorted({s.split(" [", 1)[0] for s in stamps})
            print(f"    pool {pool_id[:18]}... rows={len(stamps)} distinct={len(distinct)} newest={newest}")
        print()

    print("== distinct liquidity_read_at per pool (route-level comparison) ==")
    for row in blocked:
        stamps = []
        for pool_id in row["poolIds"] or []:
            values = read_at.get(pool_id, [])
            stamps.append(values[-1].split(" [", 1)[0] if values else None)
        print(f"  {row['reason']:<62} {stamps} distinct={len(set(s for s in stamps if s))}")


if __name__ == "__main__":
    main()
