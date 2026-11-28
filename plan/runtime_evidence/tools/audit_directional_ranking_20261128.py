#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit the direction layer's ranking and the closed loops built on it.

Every route that is not profitable has two possible causes: the market is
flat, or the system picked the wrong pool.  This audit separates the two by
checking the three links that decide which pool a route uses:

1. **the ranking** -- inside one ``(scope, pair, direction)`` group the
   rank 1 row must be the row with the **highest directional output rate**,
   because the rate is the output per unit of input and a larger rate is a
   better pool.  The rule is the one the direction layer implements
   (``assign_ranks`` in ``directional_pool_manager``).
2. **the wiring** -- every hop of every closed loop must use the rank 1 row
   of **its own scope**, so a loop can never be built on a pool that the
   direction layer did not rank first.
3. **the pruning** -- a pool that was rejected (fee range, liquidity) must
   not be better than the pool that was kept.  A rejected pool that is
   better is a *lost* candidate, and it is reported as such.

On top of the three checks the audit enumerates the **triangles** of the
direction layer's rate graph and reports the ones whose marginal round trip
is positive, together with the ones the closed loops actually cover.  A
positive triangle that no loop covers is a candidate the closed-loop
selection does not reach -- the audit names it, it does not judge it.

Read-only: the store is opened with ``mode=ro`` and ``query_only``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def _symbol_map(connection: sqlite3.Connection) -> dict[str, str]:
    """Map every token key id to its symbol (the token libraries are the
    authority; a token that is not in one keeps its address)."""
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


def _name(symbols: dict[str, str], key_id: str | None) -> str:
    if not key_id:
        return "?"
    if key_id in symbols:
        return symbols[key_id]
    return str(key_id).split(":")[-1][:10]


def _rate(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def audit(database: Path) -> dict[str, object]:
    connection = sqlite3.connect(
        "file:" + database.resolve().as_posix() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    symbols = _symbol_map(connection)
    try:
        pools = [
            dict(row)
            for row in connection.execute(
                "SELECT scope_key, pair_key_id, direction, base_pool_id,"
                " directional_pool_id, pair_direction_rank, pool_status,"
                " directional_output, directional_output_measured, fee,"
                " depth_status, pool_status_reason, input_token_key_id,"
                " output_token_key_id"
                " FROM manager_v4_directional_exchange_pools"
            )
        ]
        hops = [
            dict(row)
            for row in connection.execute(
                "SELECT h.loop_key, h.hop_index, h.directional_scope_key,"
                " h.hop_direction, h.directional_pool_id, h.directional_pool_rank,"
                " h.input_token_key_id, h.output_token_key_id"
                " FROM manager_v4_closed_loop_hops h"
            )
        ]
    finally:
        connection.close()

    report: dict[str, object] = {
        "database": str(database),
        "pools": len(pools),
        "hops": len(hops),
    }

    # 1. the ranking: rank 1 must carry the highest rate of its group.  The
    #    group is the scope's own group, because the direction layer ranks
    #    every scope on its own.
    groups: dict[tuple[str, str, int], list[dict[str, object]]] = {}
    for pool in pools:
        if pool["pool_status"] != "active" or not pool["directional_output_measured"]:
            continue
        key = (str(pool["scope_key"]), str(pool["pair_key_id"]), int(pool["direction"]))
        groups.setdefault(key, []).append(pool)

    ranking_violations = []
    rank_shape_violations = []
    for key, members in groups.items():
        ranked = [row for row in members if row["pair_direction_rank"] is not None]
        ranks = sorted(int(row["pair_direction_rank"]) for row in ranked)
        if ranks and ranks != list(range(1, len(ranks) + 1)):
            rank_shape_violations.append(
                {
                    "scope": key[0],
                    "pair": key[1],
                    "direction": key[2],
                    "ranks": ranks,
                }
            )
        rates = [(row, _rate(row["directional_output"])) for row in ranked]
        rated = [(row, rate) for row, rate in rates if rate is not None]
        if not rated:
            continue
        top = min(rated, key=lambda item: int(item[0]["pair_direction_rank"]))
        best = max(rated, key=lambda item: item[1])
        if best[1] > (top[1] or 0.0) + 1e-12:
            ranking_violations.append(
                {
                    "scope": key[0],
                    "pair": key[1],
                    "direction": key[2],
                    "rank1": {
                        "directionalPoolId": top[0]["directional_pool_id"],
                        "rate": top[1],
                    },
                    "betterPool": {
                        "directionalPoolId": best[0]["directional_pool_id"],
                        "rate": best[1],
                        "rank": best[0]["pair_direction_rank"],
                    },
                }
            )
    report["ranking"] = {
        "groups": len(groups),
        "rank1IsTheHighestRate": len(ranking_violations) == 0,
        "rankingViolations": ranking_violations,
        "ranksAreContinuous": len(rank_shape_violations) == 0,
        "rankShapeViolations": rank_shape_violations,
    }

    # 2. the wiring: every hop must use the rank 1 pool of its scope.
    rank1_of = {}
    for pool in pools:
        if pool["pair_direction_rank"] == 1:
            rank1_of[
                (
                    str(pool["scope_key"]),
                    str(pool["input_token_key_id"]),
                    str(pool["output_token_key_id"]),
                    int(pool["direction"]),
                )
            ] = pool
    wiring_violations = []
    for hop in hops:
        key = (
            str(hop["directional_scope_key"]),
            str(hop["input_token_key_id"]),
            str(hop["output_token_key_id"]),
            int(hop["hop_direction"]),
        )
        best = rank1_of.get(key)
        if best is None:
            wiring_violations.append(
                {"loopKey": hop["loop_key"], "hopIndex": hop["hop_index"],
                 "reason": "no_rank1_row_for_the_hop_direction"}
            )
        elif best["directional_pool_id"] != hop["directional_pool_id"]:
            wiring_violations.append(
                {
                    "loopKey": hop["loop_key"],
                    "hopIndex": hop["hop_index"],
                    "reason": "hop_does_not_use_the_rank1_pool",
                    "hopPool": hop["directional_pool_id"],
                    "rank1Pool": best["directional_pool_id"],
                }
            )
    report["wiring"] = {
        "hops": len(hops),
        "everyHopUsesItsScopeRank1": len(wiring_violations) == 0,
        "violations": wiring_violations,
    }

    # 3. the pruning: a rejected but priced pool must not beat the kept one.
    pruning_losses = []
    by_group: dict[tuple[str, str, int], list[dict[str, object]]] = {}
    for pool in pools:
        key = (
            str(pool["scope_key"]),
            str(pool["pair_key_id"]),
            int(pool["direction"]),
        )
        by_group.setdefault(key, []).append(pool)
    for key, members in by_group.items():
        kept = [
            row
            for row in members
            if row["pool_status"] == "active" and row["directional_output_measured"]
        ]
        rejected = [
            row
            for row in members
            if row["pool_status"] != "active" and row["directional_output_measured"]
        ]
        if not kept or not rejected:
            continue
        kept_best = max(_rate(row["directional_output"]) or 0.0 for row in kept)
        for row in rejected:
            rate = _rate(row["directional_output"]) or 0.0
            if rate > kept_best:
                pruning_losses.append(
                    {
                        "scope": key[0],
                        "pair": key[1],
                        "direction": key[2],
                        "rejectedPool": row["directional_pool_id"],
                        "rejectedRate": rate,
                        "keptRate": kept_best,
                        "reason": row["pool_status_reason"],
                    }
                )
    report["pruning"] = {
        "rejectedButPricedPools": sum(
            1
            for pool in pools
            if pool["pool_status"] != "active" and pool["directional_output_measured"]
        ),
        "noRejectedPoolBeatsTheKeptOne": len(pruning_losses) == 0,
        "lostCandidates": pruning_losses,
    }

    # 4. the triangles of the rate graph, and the ones a loop covers.
    best_edge: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}
    for pool in pools:
        if pool["pool_status"] != "active" or not pool["directional_output_measured"]:
            continue
        rate = _rate(pool["directional_output"])
        if rate is None:
            continue
        key = (str(pool["input_token_key_id"]), str(pool["output_token_key_id"]))
        if key not in best_edge or rate > best_edge[key][0]:
            best_edge[key] = (rate, pool)

    triangles: dict[tuple[str, ...], tuple[float, str, str, str]] = {}
    for (first, second) in best_edge:
        for (middle_in, third) in best_edge:
            if middle_in != second:
                continue
            for (third_out, anchor) in best_edge:
                if third_out != third or anchor != first:
                    continue
                if len({first, second, third}) != 3:
                    continue
                product = (
                    best_edge[(first, second)][0]
                    * best_edge[(second, third)][0]
                    * best_edge[(third, first)][0]
                )
                key = tuple(sorted((first, second, third)))
                if key not in triangles or product > triangles[key][0]:
                    triangles[key] = (product, first, second, third)

    covered: set[frozenset[str]] = set()
    try:
        connection = sqlite3.connect(
            "file:" + database.resolve().as_posix() + "?mode=ro", uri=True
        )
        for row in connection.execute("SELECT path_text FROM manager_v4_closed_loops"):
            parts = [part.strip() for part in str(row[0] or "").split("->")]
            covered.add(frozenset(parts))
        connection.close()
    except sqlite3.Error:
        pass

    triangle_rows = []
    for key, (product, first, second, third) in sorted(
        triangles.items(), key=lambda item: -item[1][0]
    ):
        legs = []
        for source, target in ((first, second), (second, third), (third, first)):
            rate, pool = best_edge[(source, target)]
            legs.append(
                {
                    "from": _name(symbols, source),
                    "to": _name(symbols, target),
                    "rate": rate,
                    "scope": pool["scope_key"],
                    "fee": pool["fee"],
                    "directionalPoolId": pool["directional_pool_id"],
                }
            )
        names = frozenset(_name(symbols, part) for part in (first, second, third))
        triangle_rows.append(
            {
                "marginalRoundTrip": product,
                "marginalRoundTripPercent": (product - 1.0) * 100.0,
                "tokens": sorted(names),
                "legs": legs,
                "coveredByAClosedLoop": names in covered,
            }
        )
    report["triangles"] = {
        "edges": len(best_edge),
        "triangles": len(triangle_rows),
        "positiveTriangles": sum(
            1 for row in triangle_rows if row["marginalRoundTrip"] > 1.0
        ),
        "positiveTrianglesCovered": sum(
            1
            for row in triangle_rows
            if row["marginalRoundTrip"] > 1.0 and row["coveredByAClosedLoop"]
        ),
        "rows": triangle_rows,
    }

    report["verdict"] = {
        "rankingIsSound": bool(report["ranking"]["rank1IsTheHighestRate"])
        and bool(report["ranking"]["ranksAreContinuous"]),
        "everyHopUsesTheRank1Pool": bool(report["wiring"]["everyHopUsesItsScopeRank1"]),
        "noRejectedPoolBeatsTheKeptOne": bool(
            report["pruning"]["noRejectedPoolBeatsTheKeptOne"]
        ),
        "positiveTriangles": report["triangles"]["positiveTriangles"],
        "positiveTrianglesCovered": report["triangles"]["positiveTrianglesCovered"],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    report = audit(Path(arguments.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8", newline="\n")
        print("wrote:", target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
