"""Show how far the review rows trail the measurement of their loop.

The healer reads the review rows of the store and compares them with the
moments the closed loops publish.  This probe prints both sides for every
live loop, so the difference between the measurement and the moment the row
landed is visible.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main(database: Path) -> None:
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    states = con.execute(
        "SELECT loop_key, runtime_status, status_reason, last_reviewed_at,"
        "       next_review_at, review_cycle_started_at, updated_at"
        "  FROM manager_v4_closed_loop_runtime_state").fetchall()
    for state in states:
        key = state["loop_key"]
        rows = con.execute(
            "SELECT review_status, review_reason, reviewed_at, created_at,"
            "       signed_pnl_amount, signed_pnl_currency"
            "  FROM manager_v4_reviews WHERE loop_key = ?"
            " ORDER BY reviewed_at DESC LIMIT 3", (key,)).fetchall()
        print("loop ...%s" % key[-12:])
        print("   state: status=%s reason=%s" % (state["runtime_status"],
                                                state["status_reason"]))
        print("          last=%s next=%s cycle=%s upd=%s" % (
            state["last_reviewed_at"], state["next_review_at"],
            state["review_cycle_started_at"], state["updated_at"]))
        for row in rows:
            print("   review: %s pnl=%s %s reviewed=%s created=%s (%s)" % (
                row["review_status"], row["signed_pnl_amount"],
                row["signed_pnl_currency"], row["reviewed_at"],
                row["created_at"], row["review_reason"]))
    print("== newest execution reviews (the runtime writes them at once) ==")
    for row in con.execute(
            "SELECT id, attempt_key, pair, category, created_at, updated_at,"
            "       selected_profit_amount, selected_profit_source"
            "  FROM cow_execution_reviews ORDER BY id DESC LIMIT 8"):
        print("   exec: %s %s %s created=%s updated=%s pnl=%s (%s)" % (
            row["id"], row["pair"], row["category"], row["created_at"],
            row["updated_at"], row["selected_profit_amount"],
            row["selected_profit_source"]))
    print("== the moments the loops are scheduled at, as they are stored ==")
    for row in con.execute(
            "SELECT loop_key, runtime_status, next_review_at, updated_at"
            "  FROM manager_v4_closed_loop_runtime_state ORDER BY loop_key"):
        print("   %s %-16s next=%-40s upd=%s" % (
            row["loop_key"][-10:], row["runtime_status"],
            row["next_review_at"], row["updated_at"]))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1
               else "flashloan/db/local_store_4663.sqlite3"))
