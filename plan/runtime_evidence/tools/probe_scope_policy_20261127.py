#!/usr/bin/env python3
"""Read-only probe of the scope policy conflict.

The baseline audit demands a core4 scope whose anchor libraries are exactly
[core, stocks].  The production scope carries [core, stocks, test], and the
scope tool's own default is core_stocks_test.  This probe measures what the
extra `test` library actually contributes before anybody narrows the scope.
"""
import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    db = Path(sys.argv[1] if len(sys.argv) > 1 else
              "flashloan/db/local_store_4663.sqlite3").resolve()
    connection = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True,
                                 timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    out: dict = {"database": str(db)}
    try:
        out["scopeConfig"] = [dict(row) for row in connection.execute(
            "SELECT scope_key,pool_table,library_keys_json,filter_mode,enabled,"
            "materialized_checksum,last_rebuilt_at FROM pool_scope_config "
            "WHERE network='robinhood' AND chain_id=4663")]
        out["testLibraryTokens"] = [dict(row) for row in connection.execute(
            "SELECT token_key_id,token_symbol,token_name,token_decimals,"
            "admission_status FROM token_test "
            "WHERE network='robinhood' AND chain_id=4663 LIMIT 20")]
        out["core4PoolsWithTestToken"] = connection.execute(
            "SELECT COUNT(*) FROM v4_exchange_pools_core4 p "
            "WHERE p.network='robinhood' AND p.chain_id=4663 AND ("
            "p.token0_key_id IN (SELECT token_key_id FROM token_test "
            "WHERE network='robinhood' AND chain_id=4663) OR "
            "p.token1_key_id IN (SELECT token_key_id FROM token_test "
            "WHERE network='robinhood' AND chain_id=4663))").fetchone()[0]
        out["core4PoolCount"] = connection.execute(
            "SELECT COUNT(*) FROM v4_exchange_pools_core4 "
            "WHERE network='robinhood' AND chain_id=4663").fetchone()[0]
        # Closed loops that route through a test-library token.
        out["closedLoopsAnchoredOnTestToken"] = connection.execute(
            "SELECT COUNT(*) FROM manager_v4_closed_loops "
            "WHERE network='robinhood' AND chain_id=4663 AND anchor_token_key_id IN ("
            "SELECT token_key_id FROM token_test WHERE network='robinhood' AND chain_id=4663)"
        ).fetchone()[0]
        out["closedLoopsWithTestTokenInPath"] = connection.execute(
            "SELECT COUNT(DISTINCT loop_key) FROM manager_v4_closed_loop_hops "
            "WHERE network='robinhood' AND chain_id=4663 AND ("
            "input_token_key_id IN (SELECT token_key_id FROM token_test "
            "WHERE network='robinhood' AND chain_id=4663) OR "
            "output_token_key_id IN (SELECT token_key_id FROM token_test "
            "WHERE network='robinhood' AND chain_id=4663))").fetchone()[0]
        out["closedLoopCount"] = connection.execute(
            "SELECT COUNT(*) FROM manager_v4_closed_loops "
            "WHERE network='robinhood' AND chain_id=4663").fetchone()[0]
        out["candidateScopePoolsWithTestToken"] = connection.execute(
            "SELECT COUNT(*) FROM v4_exchange_pools_candidate_exchange "
            "WHERE network='robinhood' AND chain_id=4663 AND ("
            "token0_key_id IN (SELECT token_key_id FROM token_test "
            "WHERE network='robinhood' AND chain_id=4663) OR "
            "token1_key_id IN (SELECT token_key_id FROM token_test "
            "WHERE network='robinhood' AND chain_id=4663))").fetchone()[0]
        out["candidateScopePoolCount"] = connection.execute(
            "SELECT COUNT(*) FROM v4_exchange_pools_candidate_exchange "
            "WHERE network='robinhood' AND chain_id=4663").fetchone()[0]
    finally:
        connection.close()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
