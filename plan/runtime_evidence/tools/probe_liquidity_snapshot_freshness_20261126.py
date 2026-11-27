#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read how fresh the liquidity snapshots are, read-only.

The runtime reports the liquidity worker as ``complete`` while the
snapshot rows themselves stop advancing.  The reason is visible in the
code: ``missing_after`` counts the pools that have **no row**, not the
pools whose row is **stale**.  As long as the rows exist, the count is
zero and the worker keeps saying ``complete``.

This probe turns that story into readings that can be recomputed:

* how many snapshot rows exist, how old the newest one is, and how the
  rows spread over the days (the age distribution);
* whether the pool index itself moved on (the index can advance while
  the snapshots do not);
* what the worker cursor claims, next to the snapshot reading.

The store is opened with a ``mode=ro`` URI and ``PRAGMA query_only = ON``,
so the probe cannot write to the production database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return bool(row[0])


def read_snapshot_freshness(
    connection: sqlite3.Connection, table: str, extra_columns: tuple[str, ...] = ()
) -> dict:
    """Row count, newest read timestamp, newest block, and age spread."""
    if not table_exists(connection, table):
        return {"table": table, "exists": False}

    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    has_liquidity_read_at = "liquidity_read_at" in columns
    has_block = "liquidity_block_number" in columns
    has_pool = "pool_id" in columns

    readings: dict = {"table": table, "exists": True}

    row = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    readings["rowCount"] = row["n"]

    if has_pool:
        row = connection.execute(
            f"SELECT COUNT(DISTINCT pool_id) AS n FROM {table}"
        ).fetchone()
        readings["distinctPoolCount"] = row["n"]

    if has_liquidity_read_at:
        row = connection.execute(
            f"SELECT MIN(liquidity_read_at) AS oldest, MAX(liquidity_read_at) AS newest"
            f" FROM {table}"
        ).fetchone()
        readings["oldestLiquidityReadAt"] = row["oldest"]
        readings["newestLiquidityReadAt"] = row["newest"]
        # Age spread: how many rows sit on each calendar day.
        rows = connection.execute(
            f"SELECT substr(liquidity_read_at, 1, 10) AS day, COUNT(*) AS n"
            f" FROM {table} WHERE liquidity_read_at IS NOT NULL"
            " GROUP BY day ORDER BY day"
        ).fetchall()
        readings["rowsPerDay"] = {row["day"]: row["n"] for row in rows}

    if has_block:
        row = connection.execute(
            f"SELECT MIN(liquidity_block_number) AS oldest,"
            f" MAX(liquidity_block_number) AS newest FROM {table}"
        ).fetchone()
        readings["oldestLiquidityBlockNumber"] = row["oldest"]
        readings["newestLiquidityBlockNumber"] = row["newest"]

    for column in extra_columns:
        if column in columns:
            row = connection.execute(
                f"SELECT MAX({column}) AS newest FROM {table}"
            ).fetchone()
            readings[f"newest_{column}"] = row["newest"]

    return readings


def read_worker_cursor(
    connection: sqlite3.Connection, domain: str
) -> dict | None:
    """The maintenance cursor a worker domain published last."""
    if not table_exists(connection, "chain_business_initialization"):
        return None
    row = connection.execute(
        "SELECT network, chain_id, protocol, domain, status, completeness_json,"
        " error_json, initialized_at, refreshed_at"
        " FROM chain_business_initialization"
        " WHERE domain = ? ORDER BY refreshed_at DESC LIMIT 1",
        (domain,),
    ).fetchone()
    if row is None:
        return None
    readings = dict(row)
    try:
        completeness = json.loads(row["completeness_json"] or "{}")
    except (TypeError, ValueError):
        completeness = {"_unparsed": row["completeness_json"]}
    readings["completeness"] = completeness
    return readings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(DEFAULT_DB))
    parser.add_argument("--output", default=None)
    arguments = parser.parse_args()

    database = Path(arguments.database)
    connection = open_read_only(database)
    try:
        readings = {
            "database": str(database),
            "readOnly": True,
            "queryOnly": True,
            "snapshots": {
                "v4ExchangePool": read_snapshot_freshness(
                    connection,
                    "v4_exchange_pool_liquidity_snapshots",
                    extra_columns=("observed_at", "refreshed_at", "materialization_batch"),
                ),
                "managerMarketObservation": read_snapshot_freshness(
                    connection,
                    "manager_market_observation_liquidity_snapshots",
                    extra_columns=("observed_at", "updated_at"),
                ),
            },
            "poolIndex": read_snapshot_freshness(
                connection, "v4_exchange_pools", extra_columns=("updated_at",)
            ),
            "workerCursors": {
                domain: read_worker_cursor(connection, domain)
                for domain in (
                    "v4_pool_liquidity_worker",
                    "v4_live_refresh",
                    "v4_candidate_yc_zc_worker",
                    "v4_market_observation_worker",
                    "v4_directional_pool_worker",
                    "v4_closed_loop_worker",
                )
            },
            "semantics": {
                "missingAfter": (
                    "counts pools WITHOUT a snapshot row, not stale rows;"
                    " a stopped writer leaves the rows in place, so the count"
                    " stays 0 and the worker keeps reporting mode=complete"
                ),
                "modeRule": (
                    "robinhood_v4_idle_maintenance.py:1491 --"
                    " mode = 'complete' if missing_after == 0 and wrapped"
                ),
                "workerCompleteRule": (
                    "v4_maintenance_workers.py:196-212 -- mode in"
                    " {complete, ...} AND refreshed_at present AND no error"
                ),
            },
        }
    finally:
        connection.close()

    text = json.dumps(readings, ensure_ascii=False, indent=1)
    if arguments.output:
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
