"""Read-only probe: the gate readings that `F-2` and `F-3` are supposed to move.

`F-2` (the kernel ladder respects the published single-tick capacity) and
`F-3` (the state transition gate prices every hop with the amount that
*reaches* it) both move the reason codes of the review table:

* `F-2` removes `final_input_exceeds_single_tick_capacity`, because the
  ladder may no longer select a size above the published capacity.
* `F-3` removes the `expected_amount_crosses_tick` verdicts that were
  produced by pricing the second and the third hop with the anchored
  principal instead of the output of their predecessor.

The probe therefore reports the review status distribution, the gate reason
distribution, the ladder verdicts of the static preview and the broadcast
request table.  It is anchored on `MAX(reviewed_at)`, so a reading can be
compared with a later one even though the runtime keeps publishing new
generations while the probe runs.

Read-only throughout: the store is opened `mode=ro` with
`PRAGMA query_only = ON`.
"""

from __future__ import annotations

import collections
import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STORE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
REPORT = REPO_ROOT / "plan" / "runtime_evidence" / "reports" / "f2_f3_gate_readings_20261126.json"
NETWORK = "robinhood"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{STORE.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def main() -> int:
    connection = connect()
    generation = connection.execute(
        "SELECT MAX(reviewed_at) FROM manager_v4_reviews WHERE network = ?", (NETWORK,)
    ).fetchone()[0]

    rows = list(
        connection.execute(
            "SELECT loop_key, review_status, review_reason, static_preview_json, "
            "single_tick_capacity_anchor_raw, signed_pnl_raw "
            "FROM manager_v4_reviews WHERE network = ?",
            (NETWORK,),
        )
    )

    status = collections.Counter(row[1] for row in rows)
    reason = collections.Counter(row[2] for row in rows)

    # The gate reasons are the ones the two tasks move.  The prefix is cut
    # so that a reason that names a pool still counts as one verdict.
    gate_prefixes = (
        "state_transition_gate_failed:",
        "single_tick_gate_failed:",
    )
    gate = collections.Counter()
    for row in rows:
        text = row[2] or ""
        for prefix in gate_prefixes:
            if text.startswith(prefix):
                gate[text[len(prefix):].split(":", 1)[0]] += 1
                break

    # The ladder verdicts of the static preview: the preview is the kernel's
    # own account of how the amount was searched.
    ladder = collections.Counter()
    measured = 0
    capacity_overflow = 0
    positive = 0
    for row in rows:
        preview = json.loads(row[3]) if row[3] else {}
        best = preview.get("bestAmountCalculation") or {}
        if best:
            measured += 1 if best.get("bestAmountMeasured") else 0
            status_text = best.get("bestAmountLadderStatus")
            if status_text:
                ladder[status_text] += 1
        if row[5] is not None:
            positive += 1
        capacity = row[4]
        selected = preview.get("selectedAmountRaw")
        if capacity and selected:
            try:
                capacity_value = int(str(capacity), 16)
                selected_value = int(str(selected), 16)
            except ValueError:
                continue
            if selected_value > capacity_value:
                capacity_overflow += 1

    requests = list(
        connection.execute(
            "SELECT request_status, COUNT(*) FROM manager_v4_review_broadcast_requests "
            "WHERE network = ? GROUP BY request_status",
            (NETWORK,),
        )
    )
    attempts = connection.execute(
        "SELECT COUNT(*) FROM manager_cow_execution_attempts"
    ).fetchone()[0]

    report = {
        "probe": "f2_f3_gate_readings",
        "readOnly": True,
        "network": NETWORK,
        "generation": generation,
        "reviewRows": len(rows),
        "reviewStatus": dict(status),
        "gateVerdicts": dict(gate),
        "ladderVerdicts": dict(ladder),
        "ladderMeasured": measured,
        "signedPnlRows": positive,
        "selectedAboveCapacity": capacity_overflow,
        "reasonCounts": dict(reason.most_common(12)),
        "broadcastRequests": {row[0]: row[1] for row in requests},
        "executionAttempts": attempts,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
