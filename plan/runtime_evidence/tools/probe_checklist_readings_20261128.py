"""Check the readings that the open checklist items ask for.

Every reading is taken from the live store (read only) or from the live
manager API, and every number is printed so the checklist can be ticked
against a measurement instead of an opinion.
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.request
from collections import Counter
from pathlib import Path

DB = Path("flashloan/db/local_store_4663.sqlite3")
MANAGER = "http://127.0.0.1:8091"
CHAIN_ID = 4663

TOKENS = (
    "state_transition_gate_failed",
    "expected_pool_state_unavailable",
    "expected_amount_crosses_tick",
    "final_input_exceeds_single_tick_capacity",
    "searchCeilingRaw",
    "searchCeilingBasis",
)


def query(sql: str, params: tuple = ()) -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def current_loop_keys() -> list[str]:
    url = f"{MANAGER}/v1/closed-loops?chainId={CHAIN_ID}&limit=500&offset=0"
    with urllib.request.urlopen(url, timeout=180) as response:
        payload = json.load(response)
    data = payload.get("data") or payload
    return [str(item.get("loopKey") or "") for item in (data.get("runtimeStates") or [])]


def main() -> None:
    report: dict[str, object] = {}
    loops = current_loop_keys()
    report["currentLoopCount"] = len(loops)

    rows = query(
        "SELECT loop_key, pool_ids_json, review_status, review_reason, "
        "signed_pnl_amount, run_arbitrage_json, reviewed_at "
        "FROM manager_v4_reviews")
    current = [row for row in rows if str(row.get("loop_key") or "") in set(loops)]
    report["reviewRows"] = len(rows)
    report["currentDirectoryReviewRows"] = len(current)

    # -- the pool ids of every current review --------------------------
    hop_counts: Counter[int] = Counter()
    directional = 0
    not_directional = []
    for row in current:
        try:
            pool_ids = json.loads(str(row.get("pool_ids_json") or "[]"))
        except (TypeError, ValueError):
            pool_ids = []
        hop_counts[len(pool_ids)] += 1
        if pool_ids and all(str(item).startswith("dpool:v1:") for item in pool_ids):
            directional += 1
        else:
            not_directional.append(str(row.get("loop_key"))[:24])
    report["poolIds"] = {
        "hopCountHistogram": dict(sorted(hop_counts.items())),
        "directionalRows": directional,
        "notDirectionalRows": not_directional,
    }

    # -- the gate tokens ------------------------------------------------
    blob = "\n".join(str(row.get("run_arbitrage_json") or "") for row in rows)
    report["tokenCounts"] = {token: blob.count(token) for token in TOKENS}

    # -- the requests table ---------------------------------------------
    report["broadcastRequestRows"] = query(
        "SELECT COUNT(*) n FROM manager_v4_review_broadcast_requests")[0]["n"]
    report["positiveReviewRows"] = query(
        "SELECT COUNT(*) n FROM manager_v4_reviews WHERE signed_pnl_amount > 0")[0]["n"]
    report["positiveRowsInCurrentDirectory"] = sum(
        1 for row in current if (row.get("signed_pnl_amount") or 0) > 0)

    # -- the reason histogram of the current directory ------------------
    reasons: Counter[str] = Counter()
    for row in current:
        reasons[f"{row.get('review_status')} / {row.get('review_reason')}"] += 1
    report["currentDirectoryReasons"] = dict(reasons)

    # -- the freshest review -------------------------------------------
    fresh = query("SELECT MAX(reviewed_at) mx, COUNT(*) n FROM manager_v4_reviews")[0]
    report["freshestReview"] = fresh

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
