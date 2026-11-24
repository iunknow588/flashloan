#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report the loops whose amount ladder never ran because of the capacity.

The kernel refuses to price a route whose published single-tick capacity is
smaller than the smallest size the amount ladder is allowed to probe.  The
route then carries no measured amount at all, and the review reads as a
measurement gap.  This probe reports how far off the capacity is and which
layer published it.
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
    rows = connection.execute(
        "SELECT r.loop_key AS loop_key, r.review_status AS review_status,"
        " r.review_reason AS review_reason,"
        " r.single_tick_capacity_anchor_raw AS capacity_raw,"
        " r.single_tick_capacity_binding_pool_id AS capacity_pool,"
        " r.static_preview_json AS preview,"
        " c.single_tick_capacity_anchor_raw AS loop_capacity_raw,"
        " c.borrow_token_decimals AS decimals"
        " FROM manager_v4_reviews r"
        " LEFT JOIN manager_v4_closed_loops c"
        "   ON c.network = r.network AND c.chain_id = r.chain_id"
        "  AND c.protocol = r.protocol AND c.scope_key = r.scope_key"
        "  AND c.loop_key = r.loop_key"
    ).fetchall()
    blocked = []
    for row in rows:
        try:
            preview = json.loads(row["preview"] or "{}")
        except Exception:
            continue
        if preview.get("status") != "single_tick_capacity_below_min_probe":
            continue
        blocked.append(
            {
                "loopKey": row["loop_key"],
                "reviewStatus": row["review_status"],
                "reviewReason": row["review_reason"],
                "reviewCapacityRaw": row["capacity_raw"],
                "loopCapacityRaw": row["loop_capacity_raw"],
                "capacityPool": row["capacity_pool"],
                "decimals": row["decimals"],
            }
        )
    print(f"reviews={len(rows)} capacityBlocked={len(blocked)}")
    for item in blocked:
        print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
