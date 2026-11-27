"""Read-only probe: the reviews that *did* report a profit.

`runArbitrage` reported `ok=true` for a handful of routes with a positive
signed PnL.  Those are the only rows that may become a broadcast.  This
probe prints them in full and cross-tabs the review status against the
`runArbitrage` verdict so we can see exactly where the profit is lost.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
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

    columns = [r[1] for r in conn.execute("PRAGMA table_info(manager_v4_reviews)")]
    print("=== columns ===")
    print(json.dumps(columns, ensure_ascii=False))

    rows = conn.execute(
        "SELECT * FROM manager_v4_reviews"
    ).fetchall()

    cross: Counter[str] = Counter()
    positive: list[dict[str, object]] = []
    for row in rows:
        payload = row["run_arbitrage_json"]
        ok = False
        error = None
        pnl = None
        parsed: dict[str, object] = {}
        if payload:
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                parsed = {}
        ok = bool(parsed.get("ok"))
        error = parsed.get("error")
        pnl = parsed.get("signedPnl")
        cross[f"{row['review_status']} | ok={ok} | error={error}"] += 1
        if ok:
            positive.append(
                {
                    "loop_key": row["loop_key"] if "loop_key" in columns else None,
                    "review_status": row["review_status"],
                    "review_reason": row["review_reason"],
                    "reviewed_at": row["reviewed_at"] if "reviewed_at" in columns else None,
                    "signed_pnl_raw": row["signed_pnl_raw"],
                    "signed_pnl_currency": row["signed_pnl_currency"],
                    "signed_pnl_amount": row["signed_pnl_amount"] if "signed_pnl_amount" in columns else None,
                    "runArbitrage": parsed,
                }
            )

    print("=== status x ok x error ===")
    for name, count in sorted(cross.items()):
        print(f"{count:5d}  {name}")

    print("=== positive / ok=true rows ===")
    for item in positive:
        print(json.dumps(item, ensure_ascii=False, indent=2, default=str))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
