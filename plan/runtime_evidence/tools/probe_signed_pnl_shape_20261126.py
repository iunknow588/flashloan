"""Read-only probe: what shape does the published signed PnL have?

The broadcast admission reads the profit the review published.  The raw
value is the profit in the loop's own units; this probe prints it next to
the decimal amount so the reader knows which one can be compared with zero
without guessing.
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

    columns = [r[1] for r in conn.execute("PRAGMA table_info(manager_v4_reviews)")]
    print("=== columns ===")
    print(json.dumps(columns, ensure_ascii=False))

    rows = conn.execute(
        "SELECT loop_key, review_status, reviewed_at, signed_pnl_raw,"
        " signed_pnl_amount, signed_pnl_currency, signed_pnl_unit,"
        " signed_pnl_source, hop_sqrt_price_limits_json"
        " FROM manager_v4_reviews"
        " WHERE signed_pnl_raw IS NOT NULL"
        " ORDER BY reviewed_at DESC LIMIT 6"
    ).fetchall()
    print("=== rows with a published profit ===")
    for row in rows:
        print(json.dumps(dict(row), ensure_ascii=False, indent=2, default=str))

    print("=== request table shape ===")
    request_columns = [
        r[1] for r in conn.execute("PRAGMA table_info(manager_v4_review_broadcast_requests)")
    ]
    print(json.dumps(request_columns, ensure_ascii=False))
    request_rows = conn.execute(
        "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"
    ).fetchone()
    print(f"request rows: {request_rows[0]}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
