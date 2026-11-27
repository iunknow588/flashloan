"""Read-only probe: what did the diagnostics say about the profitable loops?

Seven loops reported `runArbitrage ok=true` with a positive signed PnL.
Six of them were later overwritten by the stale marker, so the review status
no longer says what the review itself concluded.  The diagnostics table keeps
the verdict, so this probe reads the diagnostics of exactly those loops and
also reports their per-hop price limits.
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

    print("=== manager_v4_review_diagnostics columns ===")
    print(
        json.dumps(
            [r[1] for r in conn.execute("PRAGMA table_info(manager_v4_review_diagnostics)")],
            ensure_ascii=False,
        )
    )

    rows = conn.execute(
        "SELECT loop_key, review_status, review_reason, reviewed_at, "
        "hop_sqrt_price_limits_json, single_tick_capacity_anchor_raw, "
        "single_tick_capacity_binding_pool_id, closed_loop_identity_status, "
        "identity_mismatch, run_arbitrage_json "
        "FROM manager_v4_reviews"
    ).fetchall()

    profitable = []
    for row in rows:
        payload = json.loads(row["run_arbitrage_json"]) if row["run_arbitrage_json"] else {}
        if payload.get("ok"):
            profitable.append(row)

    print(f"=== {len(profitable)} profitable loops ===")
    for row in profitable:
        limits = row["hop_sqrt_price_limits_json"]
        limit_count = None
        if limits:
            try:
                parsed = json.loads(limits)
                limit_count = len(parsed) if isinstance(parsed, list) else "not-a-list"
            except json.JSONDecodeError:
                limit_count = "unparsable"
        print(
            json.dumps(
                {
                    "loop_key": row["loop_key"],
                    "review_status": row["review_status"],
                    "review_reason": row["review_reason"],
                    "reviewed_at": row["reviewed_at"],
                    "hop_sqrt_price_limits_count": limit_count,
                    "capacity_anchor_raw": row["single_tick_capacity_anchor_raw"],
                    "capacity_binding_pool_id": row["single_tick_capacity_binding_pool_id"],
                    "closed_loop_identity_status": row["closed_loop_identity_status"],
                    "identity_mismatch": row["identity_mismatch"],
                },
                ensure_ascii=False,
            )
        )
        diagnostics = conn.execute(
            "SELECT * FROM manager_v4_review_diagnostics WHERE loop_key = ?1",
            (row["loop_key"],),
        ).fetchall()
        for diagnostic in diagnostics:
            print("    diag: " + json.dumps(dict(diagnostic), ensure_ascii=False, default=str))
        if not diagnostics:
            print("    diag: (none)")

    print("=== diagnostics summary over all loops ===")
    all_diags = conn.execute(
        "SELECT diagnostic_id, COUNT(*) AS n FROM manager_v4_review_diagnostics "
        "GROUP BY diagnostic_id ORDER BY n DESC"
    ).fetchall()
    for row in all_diags:
        print(f"{row['n']:5d}  {row['diagnostic_id']}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
