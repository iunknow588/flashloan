#!/usr/bin/env python3
"""Capture the cascade state of chain 4663 as one JSON reading.

The reading is the before/after evidence of the scope repair: the scope
policy, the scope sizes, the candidate library, the closed-loop catalog and
the review library, all read from the production store in one place so a
change can be measured instead of asserted.

Usage:
  capture_cascade_state_20261127.py <label>
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path


DB = Path("flashloan/db/local_store_4663.sqlite3").resolve()
MANAGER_API = "http://127.0.0.1:8091"
NETWORK = "robinhood"
CHAIN_ID = 4663


def api(path: str) -> dict:
    with urllib.request.urlopen(MANAGER_API + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def scalar(connection, sql: str, params=()) -> object:
    row = connection.execute(sql, params).fetchone()
    return row[0] if row else None


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "reading"
    connection = sqlite3.connect("file:" + DB.as_posix() + "?mode=ro", uri=True,
                                 timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    out: dict = {"label": label, "capturedAt": scalar(connection, "SELECT datetime('now')")}
    try:
        out["scopes"] = [dict(row) for row in connection.execute(
            "SELECT scope_key,pool_table,library_keys_json,filter_mode,enabled,"
            "materialized_checksum,last_rebuilt_at FROM pool_scope_config "
            "WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID))]
        out["poolCounts"] = {
            "core4": scalar(connection,
                "SELECT COUNT(*) FROM v4_exchange_pools_core4 "
                "WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)),
            "core4Active": scalar(connection,
                "SELECT COUNT(*) FROM v4_exchange_pools_core4 "
                "WHERE network=? AND chain_id=? AND pool_status='active'",
                (NETWORK, CHAIN_ID)),
            "candidateExchange": scalar(connection,
                "SELECT COUNT(*) FROM v4_exchange_pools_candidate_exchange "
                "WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)),
            "candidateExchangeActive": scalar(connection,
                "SELECT COUNT(*) FROM v4_exchange_pools_candidate_exchange "
                "WHERE network=? AND chain_id=? AND pool_status='active'",
                (NETWORK, CHAIN_ID)),
            "candidatePools": scalar(connection,
                "SELECT COUNT(*) FROM manager_dex_candidate_pools "
                "WHERE network=? AND chain_id=? AND scope_key='core4'",
                (NETWORK, CHAIN_ID)),
            "directionalPools": scalar(connection,
                "SELECT COUNT(*) FROM manager_v4_directional_exchange_pools "
                "WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)),
        }
        out["closedLoops"] = {
            "loops": scalar(connection,
                "SELECT COUNT(*) FROM manager_v4_closed_loops "
                "WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)),
            "hops": scalar(connection,
                "SELECT COUNT(*) FROM manager_v4_closed_loop_hops "
                "WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)),
            "invalidHopDirectionalReference": scalar(connection,
                "SELECT COUNT(*) FROM manager_v4_closed_loop_hops h "
                "WHERE h.network=? AND h.chain_id=? AND h.scope_key='core4' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM manager_v4_directional_exchange_pools p "
                "WHERE p.network=h.network AND p.chain_id=h.chain_id "
                "AND p.protocol=h.protocol AND p.scope_key=h.directional_scope_key "
                "AND p.directional_pool_id=h.directional_pool_id "
                "AND p.direction=h.hop_direction AND p.pool_status='active')",
                (NETWORK, CHAIN_ID)),
        }
        out["reviews"] = {
            "rows": scalar(connection, "SELECT COUNT(*) FROM manager_v4_reviews"),
            "latestReviewedAt": scalar(connection,
                "SELECT MAX(reviewed_at) FROM manager_v4_reviews"),
        }
        out["broadcasts"] = {
            "reviewBroadcastRequests": scalar(connection,
                "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"),
            "cowExecutionAttempts": scalar(connection,
                "SELECT COUNT(*) FROM manager_cow_execution_attempts"),
        }
    finally:
        connection.close()
    try:
        loops = api(f"/v1/closed-loops?chainId={CHAIN_ID}")
        data = loops.get("data") or {}
        out["managerClosedLoops"] = {
            "loopCount": (data.get("status") or {}).get("loopCount"),
            "consumersNeedRebuild": (data.get("status") or {}).get("consumersNeedRebuild"),
            "materializedChecksum": (data.get("status") or {}).get("materializedChecksum"),
        }
    except Exception as error:  # noqa: BLE001 - the reading reports failures
        out["managerClosedLoops"] = {"error": str(error)}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
