#!/usr/bin/env python3
"""Count how many closed loops the capacity gate drops today.

The skipped-token table carries the reason a candidate path never became a
closed loop. The two capacity reasons are the ones a backup pool could
rescue: ``path_single_tick_bound_unusable`` and
``path_capacity_below_minimum``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DEFAULT_STORE = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")


def main() -> int:
    store = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORE
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    reasons: Counter[str] = Counter()
    blocking: Counter[str] = Counter()
    capacity_rows: list[dict] = []
    for row in connection.execute(
        "SELECT anchor_token_key_id, token_key_id, candidate_role, reason"
        " FROM manager_v4_closed_loop_skipped_tokens"
    ):
        reason = row["reason"] or ""
        reasons[reason.split(":")[0]] += 1
        if reason.startswith("path_single_tick_bound_unusable") or reason.startswith(
            "path_capacity_below_minimum"
        ):
            blocking[reason.split(":")[1] if ":" in reason else "?"] += 1
            capacity_rows.append(
                {
                    "anchor": row["anchor_token_key_id"],
                    "candidate": row["token_key_id"],
                    "role": row["candidate_role"],
                    "reason": reason,
                }
            )

    report = {
        "store": str(store),
        "skipped_by_reason": dict(reasons.most_common()),
        "skipped_total": sum(reasons.values()),
        "capacity_gate_rows": len(capacity_rows),
        "capacity_gate_blocking_pool": dict(blocking.most_common()),
        "capacity_gate_examples": capacity_rows[:20],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
