#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure what migration `20261125_017` would change in the live store.

The migration turns `manager_market_observation_yz_pairs.optimal_pool_id` from
a stored column into a view column derived from the directional layer's rank 1.
Before applying it, answer two questions **read-only**:

1. would the view answer anything different from the stored column?
2. do the published hops still depend on the stored column at all?

Read-only: `mode=ro` + `PRAGMA query_only = ON`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def probe(database_path: Path) -> dict:
    uri = "file:" + database_path.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    yz.scope_key,
                    yz.anchor_token_key_id,
                    yz.direction_key,
                    yz.token_x_key_id,
                    yz.token_y_key_id,
                    yz.has_direct_swap_pool,
                    yz.direct_swap_pool_count,
                    yz.pool_ids_json,
                    yz.optimal_pool_id AS stored_optimal_pool_id,
                    (
                        SELECT d.base_pool_id
                        FROM manager_v4_directional_exchange_pools d
                        WHERE d.scope_key = yz.scope_key
                          AND d.input_token_key_id = yz.token_x_key_id
                          AND d.output_token_key_id = yz.token_y_key_id
                          AND d.pool_status = 'active'
                          AND d.pair_direction_rank IS NOT NULL
                        ORDER BY d.pair_direction_rank ASC, d.base_pool_id ASC
                        LIMIT 1
                    ) AS view_optimal_pool_id
                FROM manager_market_observation_yz_pairs yz
                """
            )
        ]
        bases = [
            dict(row)
            for row in connection.execute(
                "SELECT pool_selection_basis, COUNT(*) AS hops"
                " FROM manager_v4_closed_loop_hops"
                " GROUP BY pool_selection_basis ORDER BY hops DESC"
            )
        ]
        directional_counts = [
            dict(row)
            for row in connection.execute(
                "SELECT scope_key, COUNT(*) AS rows, COUNT(DISTINCT base_pool_id) AS pools"
                " FROM manager_v4_directional_exchange_pools"
                " GROUP BY scope_key"
            )
        ]
    finally:
        connection.close()

    differing = []
    unresolved = 0
    unresolved_with_pool = 0
    differing_with_pool = 0
    with_pool = 0
    for row in rows:
        stored = row["stored_optimal_pool_id"]
        derived = row["view_optimal_pool_id"]
        has_pool = bool(row["has_direct_swap_pool"])
        if has_pool:
            with_pool += 1
        if derived is None:
            unresolved += 1
            if has_pool:
                unresolved_with_pool += 1
            continue
        if stored != derived:
            if has_pool:
                differing_with_pool += 1
            differing.append(
                {
                    "scopeKey": row["scope_key"],
                    "directionKey": row["direction_key"],
                    "tokenX": row["token_x_key_id"],
                    "tokenY": row["token_y_key_id"],
                    "stored": stored,
                    "viewWouldAnswer": derived,
                }
            )
    return {
        "schema": "optimal-pool-view-impact-v1",
        "mode": "read-only",
        "database": str(database_path).replace(chr(92), "/"),
        "observationRows": len(rows),
        "rowsWithDirectSwapPool": with_pool,
        "rowsWhereTheViewIsUnresolved": unresolved,
        "rowsWithDirectSwapPoolWhereTheViewIsUnresolved": unresolved_with_pool,
        "rowsWhereTheViewWouldDiffer": len(differing),
        "rowsWithDirectSwapPoolWhereTheViewWouldDiffer": differing_with_pool,
        "differingSamples": differing[:20],
        "hopSelectionBasisDistribution": bases,
        "directionalPoolRows": directional_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    report = probe(Path(arguments.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print("wrote:", target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
