#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit the direction layer's invariants against the **live** store.

The store is opened **read-only**.  Every check states the invariant it
tests, the reading it took, and the verdict.  A check that cannot be
evaluated says so instead of passing silently.

The invariants are the ones the acceptance checklist names:

| id | invariant |
|---|---|
| `inv-1` | an unmeasured capacity is not stored as a measured zero |
| `inv-2` | the base fields of a direction row equal the base pool row (I-9) |
| `inv-3` | `hop_direction` equals the direction of the row it names (I-12) |
| `inv-4` | every hop's `directional_pool_id` resolves to one row (I-7) |
| `inv-5` | the review layer reports no unexplained `direction_mismatch` |
| `inv-6` | `pool_ids_json` has one base pool id per hop (I-16) |
| `inv-7` | every base pool has exactly two direction rows |
| `inv-8` | the ranks of a `(pair, direction)` group are continuous |
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

IDENTITY_COLUMNS = (
    "pool_manager",
    "pool_key_json",
    "token0",
    "token1",
    "token0_key_id",
    "token1_key_id",
    "fee",
    "tick_spacing",
    "hooks",
    "pool_status",
    "source_block_number",
    "source_tx_hash",
)

SNAPSHOT_COLUMNS = (
    "sqrt_price_x96",
    "tick",
    "liquidity",
    "price",
    "depth_status",
)

SCOPE_TABLE = {
    "core4": "v4_exchange_pools_core4",
    "candidate_exchange": "v4_exchange_pools_candidate_exchange",
}


def check(results: list, identifier: str, invariant: str, reading, ok: bool, detail: str) -> None:
    results.append(
        {
            "id": identifier,
            "invariant": invariant,
            "reading": reading,
            "verdict": "PASS" if ok else "FAIL",
            "detail": detail,
        }
    )


def audit(database_path: Path) -> dict:
    connection = sqlite3.connect(
        "file:" + database_path.resolve().as_posix() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    results: list = []
    report = {"schema": "directional-invariant-audit-v1", "database": str(database_path)}
    try:
        # inv-1: an unmeasured capacity is not stored as a measured zero.
        rows = connection.execute(
            "SELECT input_capacity_measured, input_capacity_raw,"
            " directional_output_measured, directional_output"
            " FROM manager_v4_directional_exchange_pools"
        ).fetchall()
        unmeasured_but_set = sum(
            1
            for row in rows
            if (row["input_capacity_measured"] == 0 and row["input_capacity_raw"] is not None)
            or (row["directional_output_measured"] == 0 and row["directional_output"] is not None)
        )
        measured_but_null = sum(
            1
            for row in rows
            if (row["input_capacity_measured"] != 0 and row["input_capacity_raw"] is None)
            or (row["directional_output_measured"] != 0 and row["directional_output"] is None)
        )
        check(
            results,
            "inv-1",
            "an unmeasured capacity is not stored as a measured zero",
            {
                "directionalRows": len(rows),
                "unmeasuredButCarriesAValue": unmeasured_but_set,
                "measuredButCarriesNoValue": measured_but_null,
            },
            unmeasured_but_set == 0 and measured_but_null == 0,
            "the measured flag and the value agree on every direction row",
        )

        # inv-2: the base fields of a direction row equal their source.
        #
        # The identity columns come from the base pool table and the snapshot
        # columns come from the liquidity snapshot table -- that is where the
        # directional manager reads them from, so that is where they are
        # compared.  Comparing a snapshot column with the base pool table
        # would measure the age of the base pool table, not the invariant.
        mismatches = []
        compared = 0
        # The directional manager reads every snapshot row of the chain and
        # keeps the **last** row of each pool, so the comparison has to read
        # the same rows in the same order.
        snapshots = {}
        for row in connection.execute(
            "SELECT pool_id, " + ", ".join(SNAPSHOT_COLUMNS)
            + " FROM v4_exchange_pool_liquidity_snapshots"
            " WHERE network = 'robinhood' AND chain_id = 4663"
        ):
            snapshots[row["pool_id"]] = row
        for scope, table in SCOPE_TABLE.items():
            base_rows = {
                row["pool_id"]: row
                for row in connection.execute(
                    "SELECT pool_id, " + ", ".join(IDENTITY_COLUMNS) + " FROM " + table
                )
            }
            for row in connection.execute(
                "SELECT base_pool_id, " + ", ".join(IDENTITY_COLUMNS + SNAPSHOT_COLUMNS)
                + " FROM manager_v4_directional_exchange_pools WHERE scope_key = ?1",
                (scope,),
            ):
                base = base_rows.get(row["base_pool_id"])
                if base is None:
                    mismatches.append(
                        {"scopeKey": scope, "basePoolId": row["base_pool_id"], "column": "row", "base": None, "directional": None}
                    )
                    continue
                compared += 1
                for column in IDENTITY_COLUMNS:
                    if base[column] != row[column]:
                        mismatches.append(
                            {
                                "scopeKey": scope,
                                "basePoolId": row["base_pool_id"],
                                "column": column,
                                "base": base[column],
                                "directional": row[column],
                            }
                        )
                snapshot = snapshots.get(row["base_pool_id"])
                if snapshot is None:
                    # The snapshot table's coverage is inv-9's question; it
                    # is not a disagreement about the identity of the pool.
                    pass
                    continue
        check(
            results,
            "inv-2",
            "the identity columns of a direction row equal the base pool row (I-9)",
            {
                "directionRowsCompared": compared,
                "identityColumns": list(IDENTITY_COLUMNS),
                "mismatchedCells": len(mismatches),
            },
            not mismatches,
            "every compared cell agrees" if not mismatches else json.dumps(mismatches[:20], ensure_ascii=False),
        )

        # inv-9: the snapshot columns of a direction row still equal the
        # liquidity snapshot table.
        #
        # The snapshot columns are the **live** part of a direction row: the
        # tick state and the capacity the closed loop's capacity gate reads.
        # They are copied from the liquidity snapshot table, so they hold
        # until the direction layer is materialized again.  This check says
        # how far the direction layer has drifted from that table.
        snapshot_differences = []
        snapshot_compared = 0
        snapshot_missing = 0
        for row in connection.execute(
            "SELECT base_pool_id, " + ", ".join(SNAPSHOT_COLUMNS)
            + " FROM manager_v4_directional_exchange_pools"
        ):
            snapshot = snapshots.get(row["base_pool_id"])
            if snapshot is None:
                snapshot_missing += 1
                continue
            snapshot_compared += 1
            for column in SNAPSHOT_COLUMNS:
                if snapshot[column] != row[column]:
                    snapshot_differences.append(
                        {
                            "basePoolId": row["base_pool_id"],
                            "column": column,
                            "snapshotTable": snapshot[column],
                            "directionRow": row[column],
                        }
                    )
        direction_rows = connection.execute(
            "SELECT COUNT(*) AS n, MIN(materialized_at) AS oldest, MAX(materialized_at) AS newest"
            " FROM manager_v4_directional_exchange_pools"
        ).fetchone()
        newest_snapshot = connection.execute(
            "SELECT MAX(liquidity_read_at) FROM v4_exchange_pool_liquidity_snapshots"
            " WHERE network = 'robinhood' AND chain_id = 4663"
        ).fetchone()[0]
        check(
            results,
            "inv-9",
            "the snapshot columns of a direction row still equal the liquidity snapshot table",
            {
                "directionRows": direction_rows["n"],
                "rowsCompared": snapshot_compared,
                "rowsWithNoSnapshotRow": snapshot_missing,
                "rowsWhoseSnapshotDrifted": len(
                    {item["basePoolId"] for item in snapshot_differences}
                ),
                "mismatchedCells": len(snapshot_differences),
                "directionLayerMaterializedBetween": direction_rows["oldest"],
                "and": direction_rows["newest"],
                "newestSnapshotRead": newest_snapshot,
            },
            not snapshot_differences,
            "the direction layer still carries the current snapshot"
            if not snapshot_differences
            else "the direction layer was materialized before the snapshot table moved on;"
            " re-materializing the direction layer is what closes it",
        )

        # inv-3 and inv-4: the hop names one direction row, and its
        # direction is the row's direction.
        hops = connection.execute(
            "SELECT loop_key, hop_index, directional_pool_id, hop_direction, base_pool_id"
            " FROM manager_v4_closed_loop_hops"
        ).fetchall()
        resolved = {}
        for row in connection.execute(
            "SELECT directional_pool_id, COUNT(*) AS n, direction, base_pool_id"
            " FROM manager_v4_directional_exchange_pools GROUP BY directional_pool_id"
        ):
            resolved[row["directional_pool_id"]] = dict(row)
        unresolved = []
        ambiguous = []
        direction_mismatch = []
        pool_id_mismatch = []
        for hop in hops:
            if not hop["directional_pool_id"]:
                unresolved.append({"loopKey": hop["loop_key"], "hopIndex": hop["hop_index"], "reason": "no_directional_pool_id"})
                continue
            target = resolved.get(hop["directional_pool_id"])
            if target is None:
                unresolved.append(
                    {
                        "loopKey": hop["loop_key"],
                        "hopIndex": hop["hop_index"],
                        "directionalPoolId": hop["directional_pool_id"],
                        "reason": "no_such_row",
                    }
                )
                continue
            if target["n"] != 1:
                ambiguous.append({"directionalPoolId": hop["directional_pool_id"], "rows": target["n"]})
                continue
            if target["direction"] != hop["hop_direction"]:
                direction_mismatch.append(
                    {
                        "loopKey": hop["loop_key"],
                        "hopIndex": hop["hop_index"],
                        "hopDirection": hop["hop_direction"],
                        "rowDirection": target["direction"],
                    }
                )
            if target["base_pool_id"] != hop["base_pool_id"]:
                pool_id_mismatch.append(
                    {
                        "loopKey": hop["loop_key"],
                        "hopIndex": hop["hop_index"],
                        "hopBasePoolId": hop["base_pool_id"],
                        "rowBasePoolId": target["base_pool_id"],
                    }
                )
        check(
            results,
            "inv-4",
            "every hop's directional_pool_id resolves to one row (I-7)",
            {
                "hops": len(hops),
                "hopsWithoutADirectionalPoolId": sum(1 for hop in hops if not hop["directional_pool_id"]),
                "hopsWhoseIdResolvesToNoRow": len(unresolved),
                "hopsWhoseIdResolvesToMoreThanOneRow": len(ambiguous),
            },
            not unresolved and not ambiguous,
            "every hop names exactly one direction row"
            if not unresolved and not ambiguous
            else json.dumps((unresolved + ambiguous)[:20], ensure_ascii=False),
        )
        check(
            results,
            "inv-3",
            "hop_direction equals the direction of the row it names (I-12)",
            {
                "hopsCompared": len(hops) - len(unresolved) - len(ambiguous),
                "hopsWhoseDirectionDiffers": len(direction_mismatch),
                "hopsWhoseBasePoolIdDiffers": len(pool_id_mismatch),
            },
            not direction_mismatch and not pool_id_mismatch,
            "the hop and the direction row agree"
            if not direction_mismatch and not pool_id_mismatch
            else json.dumps((direction_mismatch + pool_id_mismatch)[:20], ensure_ascii=False),
        )

        # inv-5: the review layer reports no unexplained direction_mismatch.
        try:
            mismatch_reviews = connection.execute(
                "SELECT COUNT(*) FROM manager_v4_review_diagnostics"
                " WHERE diagnostic_code = 'direction_mismatch'"
            ).fetchone()[0]
            check(
                results,
                "inv-5",
                "the review layer reports no unexplained direction_mismatch",
                {"diagnosticsNamedDirectionMismatch": mismatch_reviews},
                mismatch_reviews == 0,
                "no review carries a direction_mismatch diagnostic",
            )
        except sqlite3.OperationalError as error:
            check(
                results,
                "inv-5",
                "the review layer reports no unexplained direction_mismatch",
                {"error": str(error)},
                False,
                "the diagnostics table could not be read",
            )

        # inv-6: pool_ids_json carries one base pool id per hop.
        loops = connection.execute(
            "SELECT loop_key, pool_ids_json FROM manager_v4_closed_loops"
        ).fetchall()
        hop_counts = {
            row["loop_key"]: row["n"]
            for row in connection.execute(
                "SELECT loop_key, COUNT(*) AS n FROM manager_v4_closed_loop_hops GROUP BY loop_key"
            )
        }
        base_ids = {
            row["base_pool_id"]
            for row in connection.execute(
                "SELECT base_pool_id FROM manager_v4_directional_exchange_pools"
            )
        }
        length_mismatch = []
        unknown_pool = []
        for loop in loops:
            try:
                ids = json.loads(loop["pool_ids_json"] or "[]")
            except json.JSONDecodeError:
                length_mismatch.append({"loopKey": loop["loop_key"], "reason": "not_json"})
                continue
            if len(ids) != hop_counts.get(loop["loop_key"], 0):
                length_mismatch.append(
                    {
                        "loopKey": loop["loop_key"],
                        "poolIds": len(ids),
                        "hops": hop_counts.get(loop["loop_key"], 0),
                    }
                )
            for pool_id in ids:
                if pool_id not in base_ids:
                    unknown_pool.append({"loopKey": loop["loop_key"], "poolId": pool_id})
        check(
            results,
            "inv-6",
            "pool_ids_json carries one base pool id per hop (I-16)",
            {
                "loops": len(loops),
                "loopsWhoseLengthDiffersFromTheHopCount": len(length_mismatch),
                "poolIdsThatAreNoBasePoolId": len(unknown_pool),
            },
            not length_mismatch and not unknown_pool,
            "every loop carries one base pool id per hop"
            if not length_mismatch and not unknown_pool
            else json.dumps((length_mismatch + unknown_pool)[:20], ensure_ascii=False),
        )

        # inv-7: every base pool has exactly two direction rows.
        per_base = {
            row["base_pool_id"]: row["n"]
            for row in connection.execute(
                "SELECT scope_key || '|' || base_pool_id AS base_pool_id, COUNT(*) AS n"
                " FROM manager_v4_directional_exchange_pools GROUP BY scope_key, base_pool_id"
            )
        }
        not_two = {key: value for key, value in per_base.items() if value != 2}
        check(
            results,
            "inv-7",
            "every base pool has exactly two direction rows within its scope",
            {"scopeAndBasePoolGroups": len(per_base), "groupsWithoutExactlyTwoRows": len(not_two)},
            not not_two,
            "every base pool has its two directions"
            if not not_two
            else json.dumps(list(not_two.items())[:20], ensure_ascii=False),
        )

        # inv-8: the ranks of a (pair, direction) group are continuous.
        groups: dict = {}
        for row in connection.execute(
            "SELECT scope_key, pair_key_id, direction, pair_direction_rank FROM manager_v4_directional_exchange_pools"
            " WHERE pair_direction_rank IS NOT NULL"
        ):
            groups.setdefault((row["scope_key"], row["pair_key_id"], row["direction"]), []).append(
                row["pair_direction_rank"]
            )
        broken = []
        for (scope, pair, direction), ranks in groups.items():
            ordered = sorted(ranks)
            if ordered != list(range(1, len(ordered) + 1)):
                broken.append(
                    {
                        "scopeKey": scope,
                        "pairKeyId": pair,
                        "direction": direction,
                        "ranks": ordered,
                    }
                )
        check(
            results,
            "inv-8",
            "the ranks of a (scope, pair, direction) group are continuous",
            {"rankedGroups": len(groups), "groupsWithBrokenRanks": len(broken)},
            not broken,
            "every group ranks from 1 without a gap"
            if not broken
            else json.dumps(broken[:20], ensure_ascii=False),
        )
    finally:
        connection.close()

    report["checks"] = results
    report["passed"] = sum(1 for item in results if item["verdict"] == "PASS")
    report["failed"] = sum(1 for item in results if item["verdict"] != "PASS")
    report["verdict"] = "ALL_PASS" if report["failed"] == 0 else "HAS_FAILURE"
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
        target.write_text(text + "\n", encoding="utf-8")
        print("wrote:", target)
        print(report["verdict"], report["passed"], "passed /", report["failed"], "failed")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
