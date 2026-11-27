#!/usr/bin/env python3
'''Measure why the reviews outside the closed-loop catalog carry no value.

The cascade asks the review layer to give **every** review a concrete PnL
value.  This probe answers the question that decides whether that is
possible at all: can the routes of the value-less reviews still be priced?

The store is opened **read-only** (``mode=ro`` plus ``PRAGMA query_only=ON``)
and the script writes **nothing** into it.  The report goes to
``plan/runtime_evidence/reports/``.
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "flashloan/db/local_store_4663.sqlite3"
OUT = REPO / "plan/runtime_evidence/reports/departed_review_value_20261128.json"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def main() -> int:
    connection = connect()
    cursor = connection.cursor()

    total = cursor.execute("select count(*) from manager_v4_reviews").fetchone()[0]
    by_status = {
        status: count
        for status, count in cursor.execute(
            "select review_status, count(*) from manager_v4_reviews group by 1"
        )
    }
    rows = list(
        cursor.execute(
            "select loop_key, pool_ids_json, path_symbols_json, closed_loop_version,"
            "       review_reason, closed_loop_identity_status,"
            "       closed_loop_pool_identity_status, identity_mismatch"
            "  from manager_v4_reviews where signed_pnl_amount is null"
            " order by loop_key"
        )
    )

    pool_ids: set[str] = set()
    for _key, pool_ids_json, *_rest in rows:
        pool_ids.update(json.loads(pool_ids_json))

    in_candidate_scope = sum(
        1
        for pool_id in pool_ids
        if cursor.execute(
            "select count(*) from manager_dex_candidate_pools where pool_id = ?",
            (pool_id,),
        ).fetchone()[0]
    )

    hashes = sorted({pool_id.split(":")[5] for pool_id in pool_ids})
    pool_liveness = {}
    for digest in hashes:
        in_core = cursor.execute(
            "select count(*) from v4_exchange_pools_core4 where pool_id like ?",
            (f"%{digest}%",),
        ).fetchone()[0]
        in_candidate = cursor.execute(
            "select count(*) from v4_exchange_pools_candidate_exchange where pool_id like ?",
            (f"%{digest}%",),
        ).fetchone()[0]
        pool_liveness[digest] = {
            "inCore4": in_core,
            "inCandidateExchange": in_candidate,
            "alive": (in_core + in_candidate) > 0,
        }

    loops = []
    fully_repriceable = 0
    for key, pool_ids_json, symbols, version, reason, identity, pool_identity, mismatch in rows:
        alive = 0
        lost = []
        for pool_id in json.loads(pool_ids_json):
            digest = pool_id.split(":")[5]
            if pool_liveness[digest]["alive"]:
                alive += 1
            else:
                lost.append(digest)
        hops = len(json.loads(pool_ids_json))
        if alive == hops:
            fully_repriceable += 1
        loops.append(
            {
                "loopKey": key,
                "pathSymbols": json.loads(symbols),
                "closedLoopVersion": version,
                "reviewReason": reason,
                "identityStatus": identity,
                "poolIdentityStatus": pool_identity,
                "identityMismatch": mismatch,
                "hops": hops,
                "hopsWithLivePool": alive,
                "lostPoolHashes": lost,
                "repriceable": alive == hops,
            }
        )

    library_state = cursor.execute(
        "select loop_count, reviewed_count, completed_count, data_incomplete_count,"
        "       not_executable_count, failed_count, closed_loop_version"
        "  from manager_review_library_state"
    ).fetchone()

    report = {
        "generatedAt": "2026-11-28",
        "store": str(DB),
        "readOnly": True,
        "reviews": {"total": total, "byStatus": by_status, "withoutValue": len(rows)},
        "withoutValue": {
            "closedLoopVersions": sorted({row[3] for row in rows}),
            "distinctPoolIds": len(pool_ids),
            "poolIdsInCurrentCandidateScope": in_candidate_scope,
            "distinctPoolHashes": len(hashes),
            "poolHashesStillInACatalog": sum(1 for v in pool_liveness.values() if v["alive"]),
            "fullyRepriceableLoops": fully_repriceable,
            "loops": loops,
        },
        "libraryState": {
            "loopCount": library_state[0],
            "reviewedCount": library_state[1],
            "completedCount": library_state[2],
            "dataIncompleteCount": library_state[3],
            "notExecutableCount": library_state[4],
            "failedCount": library_state[5],
            "closedLoopVersion": library_state[6],
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "withoutValue"}, ensure_ascii=False, indent=2))
    print("withoutValue:", json.dumps({k: v for k, v in report["withoutValue"].items() if k != "loops"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

