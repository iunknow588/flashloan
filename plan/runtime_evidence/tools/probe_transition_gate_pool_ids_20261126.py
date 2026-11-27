"""Read-only probe: why does the state-transition gate say the state is unavailable?

The gate compares the pool state the **review** recorded against the pool
state the **Manager** has for the same pool.  It looks the pool up by id, so
the two sides have to name the same pool the same way.  This probe prints
both sides for every review that measured a profitable route, so a missing
state can be told apart from a pool the two sides simply name differently.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    db_path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "flashloan/db/local_store_4663.sqlite3"
    ).resolve()
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=60)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row

    diagnostic_columns = [
        r[1] for r in conn.execute("PRAGMA table_info(manager_v4_review_diagnostics)")
    ]
    print("=== diagnostic columns ===")
    print(json.dumps(diagnostic_columns, ensure_ascii=False))

    rows = conn.execute(
        "SELECT loop_key, review_status, review_reason, reviewed_at,"
        " pool_ids_json, path_addresses_json, hop_sqrt_price_limits_json,"
        " run_arbitrage_json"
        " FROM manager_v4_reviews"
        " WHERE run_arbitrage_json LIKE '%\"ok\":true%'"
        " ORDER BY reviewed_at DESC"
    ).fetchall()

    for row in rows:
        run = json.loads(row["run_arbitrage_json"] or "{}")
        quote = run.get("quote") or {}
        candidates = quote.get("candidate_pools") or quote.get("candidatePools") or []
        candidate_ids = [str(pool.get("poolId") or pool.get("pool_id") or "").lower() for pool in candidates]
        try:
            manager_ids = [str(value).lower() for value in json.loads(row["pool_ids_json"] or "[]")]
        except json.JSONDecodeError:
            manager_ids = []
        print("---")
        print(f"loop           : {row['loop_key']}")
        print(f"status/reason  : {row['review_status']} | {row['review_reason']}")
        print(f"reviewed_at    : {row['reviewed_at']}")
        print(f"manager pool ids: {manager_ids}")
        print(f"candidate pool ids: {candidate_ids}")
        print(f"overlap        : {sorted(set(manager_ids) & set(candidate_ids))}")
        print(f"limits         : {row['hop_sqrt_price_limits_json']}")
        diagnostics = conn.execute(
            "SELECT * FROM manager_v4_review_diagnostics WHERE loop_key = ?1",
            (row["loop_key"],),
        ).fetchall()
        for diagnostic in diagnostics:
            item = dict(diagnostic)
            print("  diagnostic    : " + json.dumps(item, ensure_ascii=False, default=str)[:1200])

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
