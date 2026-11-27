"""Read-only probe: what does a profitable `runArbitrage` payload carry?

The state-transition gate proves a route against the pool state the
kernel recorded when it priced the route.  It reads that state from the
quote preview of the `runArbitrage` evidence, so a payload without a
quote preview cannot be proven.  This probe prints the shape of every
profitable evidence row so the missing half is visible.
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

    rows = conn.execute(
        "SELECT loop_key, review_status, review_reason, reviewed_at, run_arbitrage_json"
        " FROM manager_v4_reviews"
        " WHERE run_arbitrage_json LIKE '%\"ok\":true%'"
        " ORDER BY reviewed_at DESC"
    ).fetchall()
    for row in rows:
        run = json.loads(row["run_arbitrage_json"] or "{}")
        quote = run.get("quote") or {}
        candidates = quote.get("candidate_pools") or quote.get("candidatePools") or []
        print("---")
        print(f"loop      : {row['loop_key']}")
        print(f"reviewed  : {row['reviewed_at']} | {row['review_status']} | {row['review_reason']}")
        print(f"top keys  : {sorted(run.keys())}")
        print(f"quote keys: {sorted(quote.keys()) if isinstance(quote, dict) else type(quote)}")
        print(f"candidates: {len(candidates)}")
        for pool in candidates:
            print(
                "   pool    : "
                + json.dumps(
                    {
                        "poolId": pool.get("poolId"),
                        "tick": pool.get("tick"),
                        "tickSpacing": pool.get("tickSpacing"),
                        "liquidity": pool.get("liquidity"),
                        "sqrtPriceX96": pool.get("sqrtPriceX96"),
                        "fee": pool.get("fee"),
                        "hasLiquidityReadAt": bool(pool.get("liquidityReadAt")),
                    },
                    ensure_ascii=False,
                )
            )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
