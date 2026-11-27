#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rehearse migration `20261125_017` and its rollback against the **real** rows.

The live store is only **read**.  Two scratch stores are built from its rows:

* scratch **A** proves the *write path* -- the migration applies, the view
  answers the direction layer's rank 1 for every row that has a pool and
  answers nothing for the rows that have none, the publisher's
  clear-and-insert cycle still works, and the scoped clear does not reach
  past its own scope;
* scratch **B** proves the *rollback* -- the table comes back, and the
  stored primary it carries is the primary that was **live** (the direction
  layer's rank 1), not the old liquidity index order.

The two scratches are separate on purpose: the write-path rehearsal deletes
and republishes rows, so it cannot also be the evidence for the rollback's
value restoration over the full row set.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
MIGRATIONS = REPO / "flashloan" / "tools" / "manager_db" / "migrations"
UP = MIGRATIONS / "20261125_017_observation_optimal_pool_view.sql"
DOWN = MIGRATIONS / "rollback" / "20261125_017_observation_optimal_pool_view.down.sql"

TABLES = (
    "manager_market_observation_yz_pairs",
    "manager_v4_directional_exchange_pools",
)

KEY = ("network", "chain_id", "protocol", "scope_key", "anchor_token_key_id", "direction_key")

VIEW_QUERY = """
    SELECT
        yz.network,
        yz.chain_id,
        yz.protocol,
        yz.scope_key,
        yz.anchor_token_key_id,
        yz.direction_key,
        yz.token_x_key_id,
        yz.token_y_key_id,
        yz.has_direct_swap_pool,
        yz.optimal_pool_id AS stored,
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
        ) AS derived
    FROM manager_market_observation_yz_pairs yz
"""


def build_scratch(source: Path, scratch: Path) -> None:
    """Copy the two live tables into a scratch store with the pre-migration shape."""
    reader = sqlite3.connect("file:" + source.resolve().as_posix() + "?mode=ro", uri=True)
    reader.row_factory = sqlite3.Row
    writer = sqlite3.connect(str(scratch))
    writer.row_factory = sqlite3.Row
    try:
        for table in TABLES:
            sql = reader.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?1", (table,)
            ).fetchone()["sql"]
            writer.execute(sql)
            columns = [row["name"] for row in reader.execute("SELECT name FROM pragma_table_info(?)", (table,))]
            writer.executemany(
                "INSERT INTO %s (%s) VALUES (%s)"
                % (table, ", ".join(columns), ", ".join("?" * len(columns))),
                [tuple(row[column] for column in columns) for row in reader.execute("SELECT * FROM " + table)],
            )
        writer.commit()
    finally:
        reader.close()
        writer.close()


def objects(connection: sqlite3.Connection) -> list:
    return [
        dict(row)
        for row in connection.execute(
            "SELECT type, name FROM sqlite_master"
            " WHERE name LIKE 'manager_market_observation_yz_pairs%'"
            " ORDER BY type, name"
        )
    ]


def read_view(connection: sqlite3.Connection) -> dict:
    rows = [dict(row) for row in connection.execute(VIEW_QUERY)]
    with_pool = [row for row in rows if row["has_direct_swap_pool"]]
    return {
        "rows": len(rows),
        "rowsWithDirectSwapPool": len(with_pool),
        "withPoolViewAnswersNothing": sum(1 for row in with_pool if row["derived"] is None),
        "withoutPoolViewAnswersSomething": sum(1 for row in rows if not row["has_direct_swap_pool"] and row["derived"]),
        "withPoolViewDiffersFromStored": sum(1 for row in with_pool if row["derived"] != row["stored"]),
    }


def key_of(row: dict) -> tuple:
    return tuple(row[name] for name in KEY)


def snapshot_stored(connection: sqlite3.Connection) -> dict:
    """The stored primary of every row, keyed by the trigger's six keys."""
    return {key_of(row): row["stored"] for row in (dict(r) for r in connection.execute(VIEW_QUERY))}


def insert_row(connection: sqlite3.Connection, scope: str, direction: str, pool: str) -> None:
    connection.execute(
        "INSERT INTO manager_market_observation_yz_pairs ("
        " network, chain_id, protocol, scope_key, anchor_token_key_id,"
        " direction_key, token_x_key_id, token_y_key_id, has_direct_swap_pool,"
        " direct_swap_pool_count, pool_ids_json, optimal_pool_id,"
        " observation_empty_reason, created_at, updated_at"
        ") VALUES ("
        " 'robinhood', 4663, 'uniswap_v4', ?1, 'anchor',"
        " ?2, 'robinhood:4663:0x0', 'robinhood:4663:0xb', 1, 2, ?3, ?4, NULL, 't', 't')",
        (scope, direction, "[" + chr(34) + "pool-a" + chr(34) + "]", pool),
    )


def rehearse_write_path(source: Path, scratch: Path) -> dict:
    build_scratch(source, scratch)
    connection = sqlite3.connect(str(scratch))
    connection.row_factory = sqlite3.Row
    report = {}
    try:
        report["beforeMigration"] = read_view(connection)
        report["beforeMigration"]["objects"] = objects(connection)
        report["beforeMigration"]["distinctTriggerKeys"] = connection.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT network, chain_id, protocol, scope_key,"
            " anchor_token_key_id, direction_key FROM manager_market_observation_yz_pairs)"
        ).fetchone()[0]

        connection.executescript(UP.read_text(encoding="utf-8"))
        connection.commit()
        report["afterMigration"] = read_view(connection)
        report["afterMigration"]["objects"] = objects(connection)

        # A row of another scope: the scoped clear must not reach it.
        insert_row(connection, "core4", "robinhood:4663:0x0->robinhood:4663:0xb", "pool-a")
        connection.commit()

        before = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs_base"
        ).fetchone()[0]
        matched = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs"
            " WHERE network = 'robinhood' AND chain_id = 4663"
            " AND protocol = 'uniswap_v4' AND scope_key = 'candidate_exchange'"
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM manager_market_observation_yz_pairs"
            " WHERE network = 'robinhood' AND chain_id = 4663"
            " AND protocol = 'uniswap_v4' AND scope_key = 'candidate_exchange'"
        )
        connection.commit()
        after = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs_base"
        ).fetchone()[0]
        other_scope_survived = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs_base WHERE scope_key = 'core4'"
        ).fetchone()[0]

        insert_row(connection, "candidate_exchange", "robinhood:4663:0x0->robinhood:4663:0xb", "pool-a")
        connection.commit()
        republished = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs_base"
        ).fetchone()[0]
        visible = connection.execute(
            "SELECT COUNT(*) FROM manager_market_observation_yz_pairs WHERE scope_key = 'candidate_exchange'"
        ).fetchone()[0]
        report["publisherCycle"] = {
            "rowsBeforeClear": before,
            "viewRowsMatchedByScopedClear": matched,
            "baseRowsAfterScopedClear": after,
            "clearRemovedExactlyTheMatchedRows": after == before - matched,
            "otherScopeRowsSurvived": other_scope_survived,
            "otherScopeUntouchable": other_scope_survived == 1,
            "rowsAfterRepublish": republished,
            "republishedRowIsVisibleThroughTheView": visible == 1,
        }
    finally:
        connection.close()
    return report


def rehearse_rollback(source: Path, scratch: Path) -> dict:
    build_scratch(source, scratch)
    connection = sqlite3.connect(str(scratch))
    connection.row_factory = sqlite3.Row
    report = {}
    try:
        live = snapshot_stored(connection)
        connection.executescript(UP.read_text(encoding="utf-8"))
        connection.commit()
        derived = {key_of(row): row["derived"] for row in (dict(r) for r in connection.execute(VIEW_QUERY))}

        connection.executescript(DOWN.read_text(encoding="utf-8"))
        connection.commit()
        restored = snapshot_stored(connection)
        report["afterRollback"] = read_view(connection)
        report["afterRollback"]["objects"] = objects(connection)

        with_answer = [key for key, value in derived.items() if value is not None]
        without_answer = [key for key, value in derived.items() if value is None]
        report["rollbackValue"] = {
            "rowsWithADirectionLayerAnswer": len(with_answer),
            "rowsWithoutADirectionLayerAnswer": len(without_answer),
            "rowsThatCarryTheLivePrimary": sum(1 for key in with_answer if restored.get(key) == derived[key]),
            "rowsThatKeptTheOldStoredValue": sum(1 for key in without_answer if restored.get(key) == live[key]),
            "rowsThatLostTheirRow": sum(1 for key in live if key not in restored),
        }
        report["rollbackValue"]["livePrimaryFullyRestored"] = all(
            restored.get(key) == derived[key] for key in with_answer
        )
        report["rollbackValue"]["noRowLost"] = all(key in restored for key in live)
    finally:
        connection.close()
    return report


def rehearse(source: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="optimal_pool_view_rehearsal_") as folder:
        report = {
            "schema": "optimal-pool-view-rehearsal-v2",
            "source": str(source),
        }
        report["writePath"] = rehearse_write_path(source, Path(folder) / "scratch_write.sqlite3")
        report["rollbackPath"] = rehearse_rollback(source, Path(folder) / "scratch_rollback.sqlite3")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    report = rehearse(Path(arguments.db))
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
