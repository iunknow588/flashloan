"""Patch the healer so a missing candidate-pool table cannot end a cycle.

The pool scope is rebuilt while the healer runs.  During that window the
store momentarily carries no ``dex_candidate_pools`` table, and the
replacement report read it without a guard.  The resulting OperationalError
ended the whole healing cycle, so one absent table cost a whole round of
repairs on every other layer too.

This script is a one-shot patcher: it edits the healer in place, keeps the
CRLF line endings intact and verifies the anchors before writing.
"""

from pathlib import Path

HEALER = Path("scripts/mainnet_arbitrage_healer.py")


NEW_QUERY_BLOCK = '''    adequate = ",".join(f"'{status}'" for status in sorted(ADEQUATE_DEPTH_STATUSES))
    eligible_pair_count = 0
    capacity_pair_count = 0
    keys: list[str] = []
    try:
        eligible = all_rows(db,
            "SELECT COUNT(DISTINCT e.token0 || ':' || e.token1) pair_count "
            "FROM v4_exchange_pools e "
            "JOIN v4_exchange_pool_liquidity_snapshots s "
            "  ON s.network=e.network AND s.chain_id=e.chain_id AND s.protocol=e.protocol "
            " AND s.pool_id=e.pool_id "
            "WHERE e.network=? AND e.chain_id=? AND e.protocol='uniswap_v4' "
            "AND e.fee>0 AND e.fee<=? AND e.dynamic_fee=0 AND e.special_fee=0 "
            "AND lower(COALESCE(e.hooks,'')) IN ('', '0x0000000000000000000000000000000000000000') "
            "AND s.has_liquidity=1 "
            f"AND COALESCE(s.depth_status,'') IN ({adequate}) ",
            (NETWORK, CHAIN_ID, MAX_CANDIDATE_FIXED_FEE))
        # A pool can be rejected by two independent policies: the shared fixed-fee
        # ceiling, or the depth band. The two produce different operational
        # consequences, so they must not be reported as one status: a fee
        # rejection means the market has pools but none of them is admissible at
        # all, while a depth rejection means the admissible pools are too thin.
        capacity_rows = all_rows(db,
            "SELECT COUNT(DISTINCT e.token0 || ':' || e.token1) pair_count "
            "FROM v4_exchange_pools e "
            "WHERE e.network=? AND e.chain_id=? AND e.protocol='uniswap_v4' "
            "AND e.fee>0 AND e.fee<=? AND e.dynamic_fee=0 AND e.special_fee=0 "
            "AND lower(COALESCE(e.hooks,'')) IN ('', '0x0000000000000000000000000000000000000000') "
            "AND EXISTS (SELECT 1 FROM v4_exchange_pool_liquidity_snapshots s "
            "  WHERE s.network=e.network AND s.chain_id=e.chain_id AND s.protocol=e.protocol "
            "    AND s.pool_id=e.pool_id AND s.has_liquidity=1)",
            (NETWORK, CHAIN_ID, MAX_CANDIDATE_FIXED_FEE))
        capacity_pair_count = int((capacity_rows[0] if capacity_rows else {}).get("pair_count") or 0)
        eligible_pair_count = int((eligible[0] if eligible else {}).get("pair_count") or 0)
        rows = all_rows(db, "SELECT DISTINCT e.token0 token0, e.token1 token1 "
            "FROM v4_exchange_pools e "
            "JOIN v4_exchange_pool_liquidity_snapshots s "
            "  ON s.network=e.network AND s.chain_id=e.chain_id AND s.protocol=e.protocol "
            " AND s.pool_id=e.pool_id "
            "WHERE e.network=? AND e.chain_id=? AND e.protocol='uniswap_v4' "
            "AND e.fee>0 AND e.fee<=? AND e.dynamic_fee=0 AND e.special_fee=0 "
            "AND lower(COALESCE(e.hooks,'')) IN ('', '0x0000000000000000000000000000000000000000') "
            "AND s.has_liquidity=1 "
            f"AND COALESCE(s.depth_status,'') IN ({adequate}) "
            "AND (SELECT COUNT(*) FROM dex_candidate_pools d "
            "     WHERE d.network=e.network AND d.chain_id=e.chain_id AND d.pool_id=e.pool_id)=0 "
            "LIMIT 20",
            (NETWORK, CHAIN_ID, MAX_CANDIDATE_FIXED_FEE))
    except sqlite3.OperationalError as error:
        if not table_missing(error):
            raise
        # The candidate scope is rebuilt while the healer runs.  For a moment
        # the store carries no candidate pools, so the question this report
        # answers -- which depth-qualified pool sits outside the candidate
        # set -- has no answer yet.  That is a gap of the store, not a
        # failure of the reader: the cycle reports the gap and the rest of
        # the cycle still runs.  Every other error is raised, because a query
        # that fails for another reason says something the reader has to know.
        return {
            "status": "candidate_pool_table_missing",
            "code": "candidate_pool_table_missing",
            "classification": "candidate_pool_table_missing",
            "repairPath": "materialize_candidate_pools",
            "reason": "the store carries no candidate pools",
            "depthCriterion": "pool_manager_capacity_verdict",
            "adequateDepthStatuses": sorted(ADEQUATE_DEPTH_STATUSES),
            "depthQualifiedPairCount": eligible_pair_count,
            "replacementKeyCount": 0,
            "keys": [],
        }
'''


OLD_BLOCKED_STEP = '''    if replacement["status"] != "replacement_available":
        steps.append({"step": "pool-swap-blocked", "blocked": True,
                      "code": POOL_DEPTH_INSUFFICIENT_CODE,
                      "classification": depth.get("classification"),
                      "repairPath": depth.get("repairPath"),
                      "reason": replacement["reason"],
                      "action": ("pool swap cannot restore a meaningful best trade amount; "
                                 "the token pair must be dropped or a deeper venue added")})
'''


NEW_BLOCKED_STEP = '''    if replacement["status"] != "replacement_available":
        # The blocked reason is the report's own: a store whose candidate
        # scope is being rebuilt is not a market whose depth ran out, and the
        # two must not carry the same code.
        scope_missing = replacement["status"] == "candidate_pool_table_missing"
        steps.append({"step": "pool-swap-blocked", "blocked": True,
                      "code": replacement.get("code") or POOL_DEPTH_INSUFFICIENT_CODE,
                      "classification": depth.get("classification"),
                      "repairPath": depth.get("repairPath"),
                      "reason": replacement["reason"],
                      "action": ("the candidate pool scope is being rebuilt; the pool swap "
                                 "is deferred until the store carries the candidate pools again")
                      if scope_missing else
                      ("pool swap cannot restore a meaningful best trade amount; "
                       "the token pair must be dropped or a deeper venue added")})
'''


def patch() -> None:
    raw = HEALER.read_bytes()
    text = raw.decode("utf-8")
    if "\r\n" in text:
        newline = "\r\n"
    else:
        newline = "\n"
    lines = text.split(newline)

    start = next((i for i, line in enumerate(lines)
                  if line.startswith('    adequate = ",".join(')), None)
    if start is None:
        raise SystemExit("anchor not found: the replacement report header")
    anchor = next((i for i, line in enumerate(lines)
                   if "AND (SELECT COUNT(*) FROM dex_candidate_pools d " in line), None)
    if anchor is None:
        raise SystemExit("anchor not found: the candidate-pool subquery")
    end = next((i for i in range(anchor, len(lines))
                if lines[i].strip() == "(NETWORK, CHAIN_ID, MAX_CANDIDATE_FIXED_FEE))"), None)
    if end is None:
        raise SystemExit("anchor not found: the candidate-pool query parameters")

    new_block = NEW_QUERY_BLOCK.rstrip("\n").split("\n")
    lines[start:end + 1] = new_block

    blocked = next((i for i, line in enumerate(lines)
                    if line.strip() == 'if replacement["status"] != "replacement_available":'), None)
    if blocked is None:
        raise SystemExit("anchor not found: the pool-swap-blocked step")
    blocked_end = next((i for i in range(blocked, len(lines))
                         if "deeper venue added" in lines[i]), None)
    if blocked_end is None:
        raise SystemExit("anchor not found: the pool-swap-blocked step body")
    lines[blocked:blocked_end + 1] = NEW_BLOCKED_STEP.rstrip("\n").split("\n")

    text = newline.join(lines)

    HEALER.write_bytes(text.encode("utf-8"))
    print(f"patched {HEALER} ({len(new_block)} lines replaced)")


if __name__ == "__main__":
    patch()
