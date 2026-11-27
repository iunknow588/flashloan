#!/usr/bin/env python3
'''Measure what the scope narrowing of 2026-11-26 did to the pool catalog.

The earlier probe (``probe_departed_review_value_20261128.py``) only looked
at the **scope** tables (``v4_exchange_pools_core4`` and
``v4_exchange_pools_candidate_exchange``).  It therefore reported the pools
of the withdrawn libraries as "gone from the market catalog", while the
master pool table (``v4_exchange_pools``) still held every one of them.

This probe answers the question that was actually asked: **did the pools
disappear, or did the scope stop covering them?**  It reads the master pool
table, both scope tables, the candidate scope, the liquidity snapshots, the
persisted scope configuration and the write-request ledger of every scope
rebuild.

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
OUT = REPO / "plan/runtime_evidence/reports/scope_narrowing_20261128.json"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    row = cursor.execute(
        "select count(*) from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return bool(row[0])


def count(cursor: sqlite3.Cursor, sql: str, args: tuple = ()) -> int:
    return int(cursor.execute(sql, args).fetchone()[0])


def main() -> int:
    connection = connect()
    cursor = connection.cursor()

    tables = [row[0] for row in cursor.execute(
        "select name from sqlite_master where type = 'table' order by 1")]

    # ------------------------------------------------------------------
    # 1. The scope configuration the store carries right now.
    # ------------------------------------------------------------------
    scope_config: list[dict] = []
    if table_exists(cursor, "pool_scope_config"):
        for row in cursor.execute(
            "select network, chain_id, scope_key, pool_table, library_keys_json"
            "  from pool_scope_config order by scope_key"
        ):
            scope_config.append({
                "network": row[0], "chainId": row[1], "scopeKey": row[2],
                "poolTable": row[3],
                "libraryKeys": json.loads(row[4] or "[]"),
            })

    # ------------------------------------------------------------------
    # 2. The row counts of the master pool table and of the scope tables.
    # ------------------------------------------------------------------
    counts: dict[str, int] = {}
    for name in ("v4_exchange_pools", "v4_exchange_pools_core4",
                 "v4_exchange_pools_candidate_exchange",
                 "manager_dex_candidate_pools",
                 "v4_exchange_pool_liquidity_snapshots"):
        if table_exists(cursor, name):
            counts[name] = count(cursor, f"select count(*) from {name}")

    # ------------------------------------------------------------------
    # 3. The value-less reviews and the pools their routes run through.
    # ------------------------------------------------------------------
    reviews = list(cursor.execute(
        "select loop_key, pool_ids_json, path_symbols_json, closed_loop_version,"
        "       review_reason, created_at"
        "  from manager_v4_reviews where signed_pnl_amount is null"
        " order by loop_key"
    ))

    pool_ids: set[str] = set()
    for row in reviews:
        pool_ids.update(json.loads(row[1] or "[]"))

    digests = sorted({pool_id.split(":")[5] for pool_id in pool_ids})

    # The libraries a token is **admitted** in.  The catalog tables hold
    # candidate rows as well, so only an admitted row makes a token a
    # member of a library.
    admitted_libraries: dict[str, set[str]] = {}
    for library, table in (("core", "token_core"), ("stocks", "token_stocks"),
                           ("extension", "token_extension"), ("test", "token_test")):
        if not table_exists(cursor, table):
            continue
        for (address,) in cursor.execute(
            f"select token_address from {table} where network = 'robinhood'"
            " and chain_id = 4663 and admission_status = 'admitted'"
        ):
            admitted_libraries.setdefault(str(address).lower(), set()).add(library)

    liveness: dict[str, dict] = {}
    for digest in digests:
        pool_row = cursor.execute(
            "select token0, token1 from v4_exchange_pools where pool_id like ? limit 1",
            (f"%{digest}%",),
        ).fetchone()
        token_libraries = sorted({
            library
            for token in (pool_row or ("", ""))
            for library in admitted_libraries.get(str(token).lower(), set())
        })
        entry = {
            "inMasterPoolTable": count(
                cursor, "select count(*) from v4_exchange_pools where pool_id like ?",
                (f"%{digest}%",)),
            "inCore4Scope": count(
                cursor,
                "select count(*) from v4_exchange_pools_core4 where pool_id like ?",
                (f"%{digest}%",)),
            "inCandidateExchangeScope": count(
                cursor,
                "select count(*) from v4_exchange_pools_candidate_exchange"
                " where pool_id like ?", (f"%{digest}%",)),
            "inCandidateScope": count(
                cursor,
                "select count(*) from manager_dex_candidate_pools where pool_id like ?",
                (f"%{digest}%",)),
            "liquiditySnapshotRows": count(
                cursor,
                "select count(*) from v4_exchange_pool_liquidity_snapshots"
                " where pool_id like ?", (f"%{digest}%",)),
        }
        entry["aliveInPoolTable"] = entry["inMasterPoolTable"] > 0
        entry["coveredByAnyScope"] = (
            entry["inCore4Scope"] + entry["inCandidateExchangeScope"] > 0)
        entry["tokenLibraries"] = token_libraries
        liveness[digest] = entry

    # ------------------------------------------------------------------
    # 4. The per-route reading: can every hop still be priced?
    # ------------------------------------------------------------------
    routes = []
    for loop_key, pool_ids_json, symbols_json, version, reason, created_at in reviews:
        hops = json.loads(pool_ids_json or "[]")
        symbols = json.loads(symbols_json or "[]")
        rows = []
        for index, pool_id in enumerate(hops):
            digest = pool_id.split(":")[5]
            entry = liveness.get(digest, {})
            rows.append({
                "hop": index + 1,
                "poolHash": digest,
                "symbol": symbols[index] if index < len(symbols) else None,
                "inMasterPoolTable": entry.get("inMasterPoolTable", 0),
                "coveredByAnyScope": entry.get("coveredByAnyScope", False),
            })
        routes.append({
            "loopKey": loop_key,
            "closedLoopVersion": version,
            "reviewReason": reason,
            "createdAt": created_at,
            "hops": rows,
            "hopsInPoolTable": sum(1 for row in rows
                                   if row["inMasterPoolTable"] > 0),
            "hopsCoveredByScope": sum(1 for row in rows
                                      if row["coveredByAnyScope"]),
        })

    # ------------------------------------------------------------------
    # 5. The ledger of every scope rebuild the store ever accepted.
    # ------------------------------------------------------------------
    rebuilds: list[dict] = []
    if table_exists(cursor, "manager_write_requests"):
        for row in cursor.execute(
            "select created_at, request_id, request_json, response_json"
            "  from manager_write_requests where operation like 'pools.scope%%'"
            "   or request_json like '%%pools.scope%%'"
            " order by created_at"
        ):
            try:
                request = json.loads(row[2] or "{}")
            except (TypeError, ValueError):
                request = {}
            try:
                response = json.loads(row[3] or "{}")
            except (TypeError, ValueError):
                response = {}
            result = response.get("result")
            if not isinstance(result, dict):
                result = {}
            rebuilds.append({
                "createdAt": row[0],
                "requestId": row[1],
                "scopeKey": request.get("scopeKey"),
                "requestedTokenLoadMode": request.get("tokenLoadMode"),
                "appliedTokenLoadMode": result.get("tokenLoadMode"),
                "libraryKeys": result.get("libraryKeys"),
                "deletedRows": result.get("deletedRows"),
                "changedRows": result.get("changedRows"),
            })

    # ------------------------------------------------------------------
    # 6. The token libraries themselves.
    # ------------------------------------------------------------------
    token_libraries: dict[str, dict] = {}
    for name in ("token_core", "token_stocks", "token_extension", "token_test"):
        if not table_exists(cursor, name):
            continue
        token_libraries[name] = {
            "rows": count(cursor, f"select count(*) from {name}"),
            "admitted": count(
                cursor,
                f"select count(*) from {name} where admission_status = 'admitted'"),
        }

    report = {
        "probe": "scope_narrowing_20261128",
        "database": str(DB),
        "readOnly": True,
        "tables": tables,
        "scopeConfig": scope_config,
        "rowCounts": counts,
        "tokenLibraries": token_libraries,
        "valueLessReviews": {
            "count": len(reviews),
            "distinctPoolHashes": len(digests),
            "poolLiveness": liveness,
            "routes": routes,
            "routesWithEveryHopInPoolTable": sum(
                1 for route in routes
                if route["hopsInPoolTable"] == len(route["hops"])),
            "routesWithEveryHopCoveredByScope": sum(
                1 for route in routes
                if route["hopsCoveredByScope"] == len(route["hops"])),
            "routesWithEveryHopInPoolTableAndCoveredByScope": sum(
                1 for route in routes
                if route["hopsInPoolTable"] == len(route["hops"])
                and route["hopsCoveredByScope"] == len(route["hops"])),
        },
        "scopeRebuildLedger": rebuilds,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps({
        "scopeConfig": scope_config,
        "rowCounts": counts,
        "tokenLibraries": token_libraries,
        "valueLessReviews": {
            "count": len(reviews),
            "distinctPoolHashes": len(digests),
            "aliveInPoolTable": sum(
                1 for entry in liveness.values() if entry["aliveInPoolTable"]),
            "coveredByAnyScope": sum(
                1 for entry in liveness.values() if entry["coveredByAnyScope"]),
            "routesWithEveryHopInPoolTable": report["valueLessReviews"]
                ["routesWithEveryHopInPoolTable"],
            "routesWithEveryHopCoveredByScope": report["valueLessReviews"]
                ["routesWithEveryHopCoveredByScope"],
            "routesWithEveryHopInPoolTableAndCoveredByScope": report[
                "valueLessReviews"]["routesWithEveryHopInPoolTableAndCoveredByScope"],
        },
        "scopeRebuilds": len(rebuilds),
        "report": str(OUT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
