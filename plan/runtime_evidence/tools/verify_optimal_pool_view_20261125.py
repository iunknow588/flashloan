#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the live store **after** the migration `20261125_017` was applied.

The store is opened **read-only**.  The probe answers four questions:

1. did the ledger record the migration, and does the schema carry the view
   and its three writers?
2. did every row survive the migration, and does the view answer the
   direction layer's rank 1 for every row that has a pool?
3. does any row lose its primary because of the migration?
4. does the closed loop still read a primary for every hop it needs?

The fourth question is the one that matters for the live system: the closed
loop manager requires `optimal_pool_id` to be present, so a view that
answers nothing for a row the loop needs would break the review.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

VIEW_ANSWER = """
    SELECT
        yz.has_direct_swap_pool,
        yz.optimal_pool_id AS view_answer,
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
        ) AS direction_layer_rank1
    FROM manager_market_observation_yz_pairs yz
"""

STORED_ANSWER = """
    SELECT optimal_pool_id FROM manager_market_observation_yz_pairs_base
"""


def probe(database_path: Path) -> dict:
    connection = sqlite3.connect(
        "file:" + database_path.resolve().as_posix() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    report = {"database": str(database_path)}
    try:
        report["ledger"] = {
            row["migration_id"]: row["applied_at"]
            for row in connection.execute(
                "SELECT migration_id, applied_at FROM schema_migrations"
                " ORDER BY applied_at"
            )
        }
        report["schemaObjects"] = [
            dict(row)
            for row in connection.execute(
                "SELECT type, name FROM sqlite_master"
                " WHERE name LIKE 'manager_market_observation_yz_pairs%'"
                " ORDER BY type, name"
            )
        ]

        report["rows"] = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs"
        ).fetchone()[0]
        report["storedRows"] = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs_base"
        ).fetchone()[0]

        answers = [dict(row) for row in connection.execute(VIEW_ANSWER)]
        with_pool = [row for row in answers if row["has_direct_swap_pool"]]
        report["view"] = {
            "rowsWithDirectSwapPool": len(with_pool),
            "viewAnswerIsTheDirectionLayerRank1": sum(
                1 for row in with_pool if row["view_answer"] == row["direction_layer_rank1"]
            ),
            "viewAnswersNothingForARowWithPool": sum(
                1 for row in with_pool if row["view_answer"] is None
            ),
            "rowsWithoutDirectSwapPool": len(answers) - len(with_pool),
            "rowsWithoutDirectSwapPoolThatTheViewStillAnswers": sum(
                1
                for row in answers
                if not row["has_direct_swap_pool"] and row["view_answer"] is not None
            ),
        }
        report["view"]["everyRowWithPoolIsAnsweredByTheDirectionLayer"] = (
            report["view"]["viewAnswerIsTheDirectionLayerRank1"]
            == report["view"]["rowsWithDirectSwapPool"]
            and report["view"]["viewAnswersNothingForARowWithPool"] == 0
        )

        stored = [row["optimal_pool_id"] for row in connection.execute(STORED_ANSWER)]
        report["storedColumn"] = {
            "rows": len(stored),
            "rowsWithAStoredPrimary": sum(1 for value in stored if value is not None),
        }

        report["closedLoop"] = {
            "hops": connection.execute(
                "SELECT COUNT(*) FROM manager_v4_closed_loop_hops"
            ).fetchone()[0],
            "hopsWhoseObservationRowHasNoPrimary": connection.execute(
                "SELECT COUNT(*) FROM manager_v4_closed_loop_hops h"
                " JOIN manager_market_observation_yz_pairs yz"
                "   ON yz.scope_key = h.scope_key"
                "  AND yz.direction_key = h.direction_key"
                " WHERE yz.optimal_pool_id IS NULL"
            ).fetchone()[0],
        }
        report["closedLoop"]["everyHopStillHasAPrimary"] = (
            report["closedLoop"]["hopsWhoseObservationRowHasNoPrimary"] == 0
        )
    finally:
        connection.close()
    return report


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
