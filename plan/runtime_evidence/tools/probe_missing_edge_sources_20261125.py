#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where can the closed loop's unresolvable edges find their pool?

The bridge in ``flashloan/src/dex/runtime/rust_closed_loop.py`` resolves a
closed-loop hop to a PoolKey through two **live** surfaces:
``dex_candidate_pools`` and ``market_observation_intermediate_pools``.
Neither surface carries a pool key for every row, and the candidate query
does not join ``v4_exchange_pools`` the way the observation query does.

This probe answers one question per missing edge: **which table does hold
the row, and does that row carry a pool key?**  It reads the live store in
read-only mode and never writes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_DB = Path("E:/2026OPC大赛/flashLoan/flashloan/db/local_store_4663.sqlite3")
NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"


def _rows(connection: sqlite3.Connection, sql: str, args: tuple) -> list[tuple]:
    return connection.execute(sql, args).fetchall()


def _pair_rows(connection: sqlite3.Connection, table: str, left: str, right: str) -> list[tuple]:
    return _rows(
        connection,
        f"""
        SELECT pool_id,
               TRIM(COALESCE(pool_key_json, '')) = '' AS pool_key_empty,
               TRIM(COALESCE(pool_key_json, '')) IN ('{{}}', 'null') AS pool_key_placeholder,
               tick_spacing
        FROM {table}
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND ((lower(token0) = ? AND lower(token1) = ?)
            OR (lower(token0) = ? AND lower(token1) = ?))
        ORDER BY pool_id
        """,
        (NETWORK, CHAIN_ID, PROTOCOL, left, right, right, left),
    )


def probe(database_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        closed_loop_edges = _rows(
            connection,
            """
            SELECT DISTINCT lower(input_address) AS left, lower(output_address) AS right
            FROM manager_v4_closed_loop_hops
            WHERE network = ? AND chain_id = ? AND protocol = ?
            ORDER BY left, right
            """,
            (NETWORK, CHAIN_ID, PROTOCOL),
        )
        bridge_edges: set[tuple[str, str]] = set()
        for table in ("dex_candidate_pools", "market_observation_intermediate_pools"):
            for left, right in _rows(
                connection,
                f"""
                SELECT DISTINCT lower(token0) AS left, lower(token1) AS right
                FROM {table}
                WHERE network = ? AND chain_id = ? AND protocol = ?
                """,
                (NETWORK, CHAIN_ID, PROTOCOL),
            ):
                bridge_edges.add((left, right) if left <= right else (right, left))

        report = {
            "database": str(database_path),
            "mode": "ro",
            "closedLoopEdges": len(closed_loop_edges),
            "bridgeEdges": len(bridge_edges),
            "edges": [],
        }
        for left, right in closed_loop_edges:
            key = (left, right) if left <= right else (right, left)
            if key in bridge_edges:
                continue
            surfaces = {}
            for label, table in (
                ("dexCandidatePools", "dex_candidate_pools"),
                ("observationSurface", "market_observation_intermediate_pools"),
                ("v4ExchangePools", "v4_exchange_pools"),
                ("v4ExchangePoolsCore4", "v4_exchange_pools_core4"),
                ("v4ExchangePoolsCandidateExchange", "v4_exchange_pools_candidate_exchange"),
                ("managerMarketObservationIntermediate", "manager_market_observation_intermediate_pools"),
                ("managerDexCandidatePools", "manager_dex_candidate_pools"),
                ("directionalExchangePools", "manager_v4_directional_exchange_pools"),
            ):
                try:
                    surfaces[label] = [
                        {
                            "poolId": row[0],
                            "poolKeyEmpty": bool(row[1]),
                            "poolKeyPlaceholder": bool(row[2]),
                            "tickSpacing": row[3],
                        }
                        for row in _pair_rows(connection, table, key[0], key[1])
                    ]
                except sqlite3.Error as error:
                    surfaces[label] = f"ERROR: {error}"
            report["edges"].append({"left": key[0], "right": key[1], "surfaces": surfaces})
        report["unresolvableEdgeCount"] = len(report["edges"])
        return report
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Live store path (read only).")
    parser.add_argument("--out", default="", help="Optional JSON output path.")
    arguments = parser.parse_args()
    report = probe(Path(arguments.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        Path(arguments.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
