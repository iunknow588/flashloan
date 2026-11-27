#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explain, pool by pool, why the scope admits or rejects a pool.

The scope materialization (flashloan/tools/pool_manager/src/scope.rs) skips
a pool before it is emitted.  This probe mirrors those skip rules for the
pools the scope-narrowing investigation cares about, so the reason a pool is
missing is read from the same tables the tool reads:

* ``token_not_admitted``  -- a token is not admitted in any library
* ``no_core_anchor``      -- filter mode ``edge_any`` and neither token is a
  fund token of the scope's load mode
* ``depth_ineligible``    -- the depth verdict is not deep/liquid/medium
* ``invalid_pool_key``    -- the pool key cannot be rebuilt

The database is opened read only.

Usage:
    python probe_pool_admission_20261128.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "flashloan/db/local_store_4663.sqlite3"
REPORT = REPO / "plan/runtime_evidence/reports/pool_admission_20261128.json"

sys.stdout.reconfigure(encoding="utf-8")

DIGESTS = [
    "0x24107d152f14", "0x319bac87e616", "0x3bb34a44f1b2",
    "0x647836c4965c", "0x6fa3ee0048e7", "0x84bd4e2d8be1",
    "0xc105b8300ff6", "0xde9f85fdd9e0", "0xe1ae6a4c6ccc",
]


def main() -> int:
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=60000")
    cursor = connection.cursor()

    # The tool reads the admitted rows of every library and keeps the first
    # row per currency key in the order core, stocks, extension, test.  A
    # currency key can carry several catalog rows (one per library), so the
    # selection has to mirror that order -- a plain dict would keep the
    # wrong row and report the wrong library.
    tokens = {}
    token_rows = {}
    for row in cursor.execute(
        "SELECT v4_currency_key, token_key_id, library_key, admission_status "
        "FROM token_library_catalog "
        "WHERE network = 'robinhood' AND chain_id = 4663 "
        "AND admission_status = 'admitted' "
        "ORDER BY v4_currency_key, CASE library_key "
        "WHEN 'core' THEN 0 WHEN 'stocks' THEN 1 "
        "WHEN 'extension' THEN 2 WHEN 'test' THEN 3 ELSE 4 END"
    ):
        token_rows.setdefault(row[0], []).append(
            {"keyId": row[1], "library": row[2], "admission": row[3]})
        tokens.setdefault(row[0], {
            "keyId": row[1], "library": row[2], "admission": row[3],
            "address": None,
        })

    anchor_ids = {
        row[0] for row in cursor.execute(
            "SELECT token_key_id FROM token_library_catalog "
            "WHERE network = 'robinhood' AND chain_id = 4663 "
            "AND admission_status = 'admitted' AND library_key IN ('core','stocks')"
        )
    }

    rows = []
    for digest in DIGESTS:
        pool = cursor.execute(
            "SELECT p.pool_id, p.token0, p.token1, p.fee, p.dynamic_fee, "
            "p.special_fee, p.tick_spacing, p.hooks, p.pool_key_json, "
            "l.depth_status, l.has_liquidity, l.liquidity_index "
            "FROM v4_exchange_pools p "
            "LEFT JOIN v4_exchange_pool_liquidity_snapshots l "
            "  ON l.network = p.network AND l.chain_id = p.chain_id "
            " AND l.protocol = p.protocol AND l.pool_id = p.pool_id "
            "WHERE p.network = 'robinhood' AND p.chain_id = 4663 "
            "AND p.protocol = 'uniswap_v4' AND p.pool_id LIKE ?",
            (f"%{digest}%",),
        ).fetchone()
        if not pool:
            rows.append({"digest": digest, "present": False})
            continue
        (pool_id, token0, token1, fee, dynamic_fee, special_fee,
         tick_spacing, hooks, pool_key_json, depth_status, has_liquidity,
         liquidity_index) = pool
        info0 = tokens.get((token0 or "").lower())
        info1 = tokens.get((token1 or "").lower())
        reasons = []
        if not info0 or not info1:
            reasons.append("token_not_admitted")
        else:
            if info0["admission"] != "admitted" or info1["admission"] != "admitted":
                reasons.append("token_not_admitted")
            elif (info0["keyId"] not in anchor_ids
                  and info1["keyId"] not in anchor_ids):
                reasons.append("no_core_anchor")
        verdict = (depth_status or "").strip().lower()
        if verdict not in ("deep", "liquid", "medium"):
            reasons.append("depth_ineligible")
        if not tick_spacing or not fee:
            reasons.append("invalid_pool_key")
        rows.append({
            "digest": digest,
            "present": True,
            "poolId": pool_id,
            "token0": {"address": token0, "currencyKey": (info0 or {}).get("keyId"),
                       "library": (info0 or {}).get("library"),
                       "admission": (info0 or {}).get("admission"),
                       "catalogRows": token_rows.get((token0 or "").lower(), []),
                       "isAnchor": (info0 or {}).get("keyId") in anchor_ids},
            "token1": {"address": token1, "currencyKey": (info1 or {}).get("keyId"),
                       "library": (info1 or {}).get("library"),
                       "admission": (info1 or {}).get("admission"),
                       "catalogRows": token_rows.get((token1 or "").lower(), []),
                       "isAnchor": (info1 or {}).get("keyId") in anchor_ids},
            "fee": fee,
            "tickSpacing": tick_spacing,
            "hooks": hooks,
            "dynamicFee": dynamic_fee,
            "specialFee": special_fee,
            "depthStatus": depth_status,
            "hasLiquidity": has_liquidity,
            "liquidityIndex": liquidity_index,
            "poolKeyJson": (pool_key_json or "")[:220],
            "skipReasons": reasons,
        })

    connection.close()
    report = {"tool": "probe_pool_admission_20261128",
              "anchorLibraryKeys": ["core", "stocks"],
              "admittedTokenCount": len(anchor_ids),
              "pools": rows}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    for row in rows:
        if not row.get("present"):
            print("%-14s ABSENT" % row["digest"])
            continue
        print("%-14s %-9s %-9s | t0 %-7s %-9s anchor=%-5s | t1 %-7s %-9s anchor=%-5s | %s" % (
            row["digest"], row["depthStatus"], row["token0"]["library"] or "-",
            (row["token0"]["admission"] or "-")[:7], row["token1"]["library"] or "-",
            row["token0"]["isAnchor"], row["token1"]["admission"] or "-",
            row["token1"]["library"] or "-", row["token1"]["isAnchor"],
            ",".join(row["skipReasons"]) or "ADMITTED"))
    print("report -> %s" % REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
