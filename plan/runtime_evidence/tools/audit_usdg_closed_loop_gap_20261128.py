#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Why does the closed loop table skip USDG as the first and the last hop?

The candidate token library carries USDG and the scope carries pools for
USDG, yet every closed loop row reports USDG as skipped.  This audit traces
the one chain that decides the outcome and reports where it breaks:

    scope pools  ->  candidate edge layer (anchor x candidate pairs)
                 ->  candidate pool recommendations (hop 'first' / 'third')
                 ->  closed loop builder

Read-only: the store is opened with ``mode=ro`` and ``query_only``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
SYMBOL = "USDG"


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _symbols(connection: sqlite3.Connection) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for table in ("token_core", "token_stocks", "token_extension", "token_test"):
        try:
            rows = connection.execute(
                f"SELECT token_key_id, token_symbol FROM {table}"
            ).fetchall()
        except sqlite3.Error:
            continue
        for key_id, symbol in rows:
            if key_id and symbol:
                symbols.setdefault(key_id, symbol)
    return symbols


def _tables(connection: sqlite3.Connection, pattern: str) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? "
        "ORDER BY name",
        (pattern,),
    ).fetchall()
    return [row[0] for row in rows]


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return []
    return [row[1] for row in rows]


def _dump_schema(connection: sqlite3.Connection) -> dict[str, list[str]]:
    names = _tables(connection, "%")
    wanted = [
        name
        for name in names
        if any(
            key in name
            for key in (
                "candidate",
                "directional",
                "closed_loop",
                "observation",
                "exchange_pools",
                "library_state",
            )
        )
    ]
    return {name: _columns(connection, name) for name in wanted}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    connection = _connect(args.database)
    if args.schema_only:
        print(json.dumps(_dump_schema(connection), ensure_ascii=False, indent=2))
        return 0

    symbols = _symbols(connection)
    keys = [key for key, value in symbols.items() if value.upper() == SYMBOL]
    print(f"{SYMBOL} token keys: {keys}")
    if not keys:
        print("no token key carries the symbol")
        return 1
    report: dict[str, object] = {"symbol": SYMBOL, "tokenKeys": keys}
    report.update(_audit(connection, keys, symbols))
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.json_out:
        args.json_out.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {args.json_out}")
    else:
        print(text)
    return 0


def _rows(connection: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(sql, args).fetchall()]


def _audit(
    connection: sqlite3.Connection, keys: list[str], symbols: dict[str, str]
) -> dict[str, object]:
    marks = ",".join("?" for _ in keys)
    report: dict[str, object] = {}

    # 1. the token libraries: where does the symbol live?
    libraries = {}
    for table in ("token_core", "token_stocks", "token_extension", "token_test"):
        try:
            rows = connection.execute(
                f"SELECT token_key_id, token_symbol FROM {table} "
                f"WHERE token_key_id IN ({marks})",
                tuple(keys),
            ).fetchall()
        except sqlite3.Error:
            continue
        for key_id, symbol in rows:
            libraries.setdefault(table, []).append({"key": key_id, "symbol": symbol})
    report["tokenLibraries"] = libraries

    # 2. the scope: which pools carry the token, and are they usable?
    scope: dict[str, list[dict]] = {}
    for table in ("v4_exchange_pools_core4", "v4_exchange_pools_candidate_exchange"):
        try:
            scope[table] = _rows(
                connection,
                f"SELECT pool_id, token0_key_id, token1_key_id, fee, special_fee, "
                f"pool_status, status_reason, depth_status, materialized_at, updated_at "
                f"FROM {table} WHERE token0_key_id IN ({marks}) "
                f"OR token1_key_id IN ({marks}) ORDER BY pool_id",
                tuple(keys) * 2,
            )
        except sqlite3.Error as error:
            scope[table] = [{"error": str(error)}]
    report["scopePools"] = scope

    # 3. the direction layer: is the token ranked there?
    report["directionLayer"] = _rows(
        connection,
        "SELECT scope_key, base_pool_id, direction, directional_pool_id, pair_key_id, "
        "input_token_key_id, output_token_key_id, fee, pair_direction_rank, pool_status, "
        "pool_status_reason, depth_status, directional_output, directional_output_measured, "
        "input_capacity_raw, input_capacity_measured, materialized_at "
        "FROM manager_v4_directional_exchange_pools "
        f"WHERE input_token_key_id IN ({marks}) OR output_token_key_id IN ({marks}) "
        "ORDER BY scope_key, pair_key_id, direction, pair_direction_rank",
        tuple(keys) * 2,
    )

    # 4. the candidate edge layer: the pairs, the pools, the recommendations
    report["candidateAnchorTokens"] = _rows(
        connection,
        "SELECT scope_key, anchor_token_key_id, token_key_id, token_symbol, token_role, "
        "pool_count, yc_primary_pool_id, zc_primary_pool_id, yc_zc_same_set, "
        "created_at, updated_at FROM manager_dex_candidate_anchor_tokens "
        f"WHERE token_key_id IN ({marks}) OR anchor_token_key_id IN ({marks}) "
        "ORDER BY scope_key, anchor_token_key_id",
        tuple(keys) * 2,
    )
    report["candidatePoolPairs"] = _rows(
        connection,
        "SELECT scope_key, anchor_token_key_id, candidate_token_key_id, pair_key_id, "
        "first_direction_key, third_direction_key, pool_count, first_primary_pool_id, "
        "third_primary_pool_id, selection_basis, created_at, updated_at "
        "FROM manager_dex_candidate_pool_pairs "
        f"WHERE candidate_token_key_id IN ({marks}) OR anchor_token_key_id IN ({marks}) "
        "ORDER BY scope_key, anchor_token_key_id",
        tuple(keys) * 2,
    )
    report["candidateRecommendations"] = _rows(
        connection,
        "SELECT scope_key, anchor_token_key_id, candidate_token_key_id, direction_key, hop, "
        "rank, pool_id, fee, selection_basis, created_at, updated_at "
        "FROM manager_dex_candidate_pool_recommendations "
        f"WHERE candidate_token_key_id IN ({marks}) OR anchor_token_key_id IN ({marks}) "
        "ORDER BY scope_key, anchor_token_key_id, hop, rank",
        tuple(keys) * 2,
    )
    report["candidatePools"] = _rows(
        connection,
        "SELECT scope_key, anchor_token_key_id, candidate_token_key_id, pool_id, fee, "
        "depth_status, is_first_hop_recommended, is_third_hop_recommended, "
        "created_at, updated_at FROM manager_dex_candidate_pools "
        f"WHERE candidate_token_key_id IN ({marks}) OR anchor_token_key_id IN ({marks}) "
        "ORDER BY scope_key, anchor_token_key_id, pool_id",
        tuple(keys) * 2,
    )
    report["candidateAbnormalPools"] = _rows(
        connection,
        "SELECT scope_key, anchor_token_key_id, candidate_token_key_id, pool_id, "
        "pool_status, reason, fee, created_at, updated_at "
        "FROM manager_dex_candidate_abnormal_pools "
        f"WHERE candidate_token_key_id IN ({marks}) OR anchor_token_key_id IN ({marks}) "
        "ORDER BY scope_key, anchor_token_key_id, pool_id",
        tuple(keys) * 2,
    )

    # 5. the closed loop layer: the verdict
    report["closedLoopSkippedTokens"] = _rows(
        connection,
        "SELECT scope_key, anchor_token_key_id, token_key_id, token_symbol, "
        "candidate_role, reason, created_at, updated_at "
        "FROM manager_v4_closed_loop_skipped_tokens "
        f"WHERE token_key_id IN ({marks}) OR anchor_token_key_id IN ({marks}) "
        "ORDER BY scope_key, candidate_role, reason",
        tuple(keys) * 2,
    )
    report["closedLoopAnchors"] = _rows(
        connection,
        "SELECT scope_key, anchor_token_key_id, anchor_symbol, COUNT(*) AS loops "
        "FROM manager_v4_closed_loops "
        f"WHERE anchor_token_key_id IN ({marks}) GROUP BY scope_key, anchor_token_key_id, "
        "anchor_symbol",
        tuple(keys),
    )

    # 6. the versions: who was built from what, and when
    states = {}
    for label, table in (
        ("candidateEdge", "manager_dex_candidate_library_state"),
        ("observation", "manager_market_observation_library_state"),
        ("directionalPool", "manager_v4_directional_pool_library_state"),
        ("closedLoop", "manager_v4_closed_loop_library_state"),
    ):
        try:
            states[label] = _rows(connection, f"SELECT * FROM {table}")
        except sqlite3.Error as error:
            states[label] = [{"error": str(error)}]
    report["libraryStates"] = states
    report["directionCoverage"] = _direction_coverage(connection)
    return report


def _direction_coverage(connection: sqlite3.Connection) -> dict[str, object]:
    """For every pool the scope calls active, does the direction layer rank it?

    A pool the scope calls active but the direction layer never ranked is a
    pool the candidate edge layer cannot recommend: its group is published
    with an empty recommendation set.  The audit names every such pair, so
    a guard that refuses to publish an empty group can be checked against
    the live store first.
    """
    scopes = ("core4", "candidate_exchange")
    report: dict[str, object] = {}
    for scope in scopes:
        table = f"v4_exchange_pools_{scope}"
        try:
            scope_pools = connection.execute(
                f"SELECT pool_id, token0_key_id, token1_key_id, pool_status, fee, "
                f"depth_status FROM {table} WHERE pool_status = 'active'"
            ).fetchall()
            ranked = connection.execute(
                "SELECT input_token_key_id, output_token_key_id, pair_direction_rank "
                "FROM manager_v4_directional_exchange_pools WHERE scope_key = ?1 "
                "AND pair_direction_rank IS NOT NULL",
                (scope,),
            ).fetchall()
        except sqlite3.Error as error:
            report[scope] = {"error": str(error)}
            continue
        rank_pairs = {
            frozenset((row[0], row[1])) for row in ranked if row[0] and row[1]
        }
        pairs: dict[frozenset[str], list[dict]] = {}
        for row in scope_pools:
            pair = frozenset((row[1], row[2]))
            pairs.setdefault(pair, []).append(
                {
                    "poolId": row[0][:14],
                    "fee": row[4],
                    "depthStatus": row[5],
                    "ranked": pair in rank_pairs,
                }
            )
        unranked = {
            "|".join(sorted(pair)): pools
            for pair, pools in pairs.items()
            if pair not in rank_pairs
        }
        report[scope] = {
            "activePoolCount": len(scope_pools),
            "activePairCount": len(pairs),
            "unrankedActivePairCount": len(unranked),
            "unrankedActivePairs": unranked,
        }
    return report


if __name__ == "__main__":
    raise SystemExit(main())
