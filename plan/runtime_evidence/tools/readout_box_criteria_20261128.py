"""Read the production store read-only and test each open box's own criterion.

The completion analysis may only tick a box when the box's own wording is
satisfied by a measured reading.  This script states, for each candidate box,
the reading that decides it and the reading that was measured.
"""

import json
import sqlite3
from pathlib import Path

STORE = Path("flashloan/db/local_store_4663.sqlite3")
OUT = Path("plan/runtime_evidence/diagnostics/box_criteria_20261128.json")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def scalar(connection: sqlite3.Connection, sql: str, params=()) -> object:
    row = connection.execute(sql, params).fetchone()
    return None if row is None else row[0]


def main() -> None:
    connection = connect()
    out: dict[str, object] = {}

    # --- L265: the review row's pool_ids_json must carry direction pool ids.
    rows = connection.execute(
        "SELECT review_id, pool_ids_json, static_preview_json FROM manager_v4_reviews"
    ).fetchall()
    shaped = 0
    shaped_detail = []
    for review_id, pool_ids_json, static_preview in rows:
        ids = json.loads(pool_ids_json or "[]")
        if not ids:
            continue
        if all(isinstance(i, str) and i.startswith("dpool:v1:") for i in ids):
            shaped += 1
            if len(shaped_detail) < 4:
                shaped_detail.append({"reviewId": review_id, "poolIds": ids})
    out["L265_pool_ids_direction_shape"] = {
        "criterion": "复核行 pool_ids_json == 方向池 id (dpool:v1:…:<base>:<dir>)",
        "rowsWithPoolIds": shaped,
        "totalRows": len(rows),
        "sample": shaped_detail,
    }

    # --- L266 / L361 / L367: the gate reasons, counted over the whole table.
    reason_rows = connection.execute(
        "SELECT payload_json FROM manager_broadcast_attempt_diagnostics"
    ).fetchall()
    gate_reasons: dict[str, int] = {}
    for (payload,) in reason_rows:
        try:
            decoded = json.loads(payload or "{}")
        except json.JSONDecodeError:
            continue
        for key, value in decoded.items():
            if not isinstance(value, str):
                continue
            for marker in (
                "state_transition_gate_failed",
                "expected_pool_state_unavailable",
                "expected_amount_crosses_tick",
                "final_input_exceeds_single_tick_capacity",
            ):
                if marker in value:
                    gate_reasons[marker] = gate_reasons.get(marker, 0) + 1
    out["gate_reasons"] = {
        "diagnosticRows": len(reason_rows),
        "counts": gate_reasons,
    }

    # --- L346: the ladder payload must carry the search ceiling.
    ceiling_rows = connection.execute(
        "SELECT COUNT(*) FROM manager_v4_reviews "
        "WHERE static_preview_json LIKE '%searchCeilingRaw%' "
        "AND static_preview_json LIKE '%searchCeilingBasis%'"
    ).fetchone()[0]
    total_reviews = scalar(connection, "SELECT COUNT(*) FROM manager_v4_reviews")
    out["L346_search_ceiling"] = {
        "criterion": "阶梯载荷出现 searchCeilingRaw / searchCeilingBasis",
        "rowsWithCeiling": ceiling_rows,
        "totalRows": total_reviews,
    }

    # --- L267 / L268: broadcast requests and execution attempts.
    out["L267_L268_broadcast"] = {
        "criterion": "灰尘行不进广播请求表 / 首笔真实广播落执行表",
        "reviewBroadcastRequests": scalar(
            connection, "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"
        ),
        "cowExecutionAttempts": scalar(
            connection, "SELECT COUNT(*) FROM manager_cow_execution_attempts"
        ),
    }

    # --- The current scope: what the eight live loops actually measured.
    current = connection.execute(
        "SELECT review_id, signed_pnl_amount, signed_pnl_raw, review_status, reviewed_at, "
        "static_preview_json FROM manager_v4_reviews WHERE scope_key = 'core4' "
        "ORDER BY reviewed_at DESC LIMIT 20"
    ).fetchall()
    scope = []
    for review_id, pnl, raw, status, reviewed_at, preview in current:
        keys = sorted(json.loads(preview or "{}").keys())
        scope.append(
            {
                "reviewId": review_id,
                "signedPnl": pnl,
                "reviewStatus": status,
                "reviewedAt": reviewed_at,
                "staticPreviewKeys": keys,
            }
        )
    out["current_scope"] = {
        "scopeKey": "core4",
        "rows": scope,
    }

    # --- The rows whose review reason says the route could not be built.
    dust = connection.execute(
        "SELECT COUNT(*) FROM manager_v4_reviews "
        "WHERE signed_pnl_amount > 0 AND review_reason LIKE '%loop_options_unavailable%'"
    ).fetchone()[0]
    out["dust_rows"] = {
        "criterion": "灰尘那一行不再进 manager_v4_review_broadcast_requests",
        "positiveLoopOptionsUnavailable": dust,
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
