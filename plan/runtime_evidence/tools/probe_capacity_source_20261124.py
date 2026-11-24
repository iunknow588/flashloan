#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Locate the source of the closed-loop capacity change.

The closed loop used to fold the hop capacities from the **pool tables**
(`v4_exchange_pools_candidate_exchange`, then `v4_exchange_pools_core4`).
It now folds them from the **direction rows** of the direction layer, and
those rows carry the state the depth snapshot table publishes.

This probe compares, hop by hop, the two state sources and reports how far
apart they are, so the capacity change can be attributed to the data source
instead of to the fold arithmetic.

Usage:
    python probe_capacity_source_20261124.py <store.db> [report.json]
"""

import json
import os
import sqlite3
import sys

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"
POOL_TABLES = [
    "v4_exchange_pools_candidate_exchange",
    "v4_exchange_pools_core4",
]
DIRECTIONAL_TABLE = "manager_v4_directional_exchange_pools"


def parse_int(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def table_columns(connection, table):
    try:
        rows = connection.execute("PRAGMA table_info(%s)" % table).fetchall()
    except sqlite3.Error:
        return set()
    return {row[1] for row in rows}


def read_pool_tables(connection):
    """The legacy tick state: the candidate-exchange table wins, the core
    table fills the pools it does not publish."""
    states = {}
    for table in POOL_TABLES:
        columns = table_columns(connection, table)
        required = {
            "pool_id",
            "sqrt_price_x96",
            "tick",
            "liquidity",
            "fee",
            "tick_spacing",
            "token0_key_id",
            "token1_key_id",
        }
        if not required.issubset(columns):
            continue
        rows = connection.execute(
            "SELECT pool_id, sqrt_price_x96, tick, liquidity, fee, tick_spacing,"
            "       token0_key_id, token1_key_id"
            "  FROM %s WHERE network = ?1 AND chain_id = ?2" % table,
            (NETWORK, CHAIN_ID),
        ).fetchall()
        for row in rows:
            if row[0] in states:
                continue
            states[row[0]] = {
                "source_table": table,
                "sqrt_price_x96": parse_int(row[1]),
                "tick": row[2],
                "liquidity": parse_int(row[3]),
                "fee": row[4],
                "tick_spacing": row[5],
                "token0_key_id": row[6],
                "token1_key_id": row[7],
            }
    return states


def read_directional_rows(connection):
    columns = table_columns(connection, DIRECTIONAL_TABLE)
    if not columns:
        return {}
    rows = connection.execute(
        "SELECT directional_pool_id, scope_key, base_pool_id, direction,"
        "       sqrt_price_x96, tick, liquidity, fee, tick_spacing,"
        "       token0_key_id, token1_key_id, input_capacity_raw, depth_capacity_raw"
        "  FROM %s WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
        " ORDER BY scope_key, base_pool_id, direction" % DIRECTIONAL_TABLE,
        (NETWORK, CHAIN_ID, PROTOCOL),
    ).fetchall()
    rows_by_id = {}
    for row in rows:
        rows_by_id[row[0]] = {
            "scope_key": row[1],
            "base_pool_id": row[2],
            "direction": row[3],
            "sqrt_price_x96": parse_int(row[4]),
            "tick": row[5],
            "liquidity": parse_int(row[6]),
            "fee": row[7],
            "tick_spacing": row[8],
            "token0_key_id": row[9],
            "token1_key_id": row[10],
            "input_capacity_raw": row[11],
            "depth_capacity_raw": row[12],
        }
    return rows_by_id


def read_hops(connection):
    rows = connection.execute(
        "SELECT h.loop_key, h.hop_index, h.pool_id, h.base_pool_id,"
        "       h.directional_pool_id, h.hop_direction, h.directional_scope_key"
        "  FROM manager_v4_closed_loop_hops h"
        " WHERE h.network = ?1 AND h.chain_id = ?2 AND h.protocol = ?3 AND h.scope_key = ?4"
        " ORDER BY h.loop_key, h.hop_index",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
    ).fetchall()
    return rows


def snapshot_cardinality(connection):
    total = connection.execute(
        "SELECT COUNT(*) FROM v4_exchange_pool_liquidity_snapshots"
    ).fetchone()[0]
    scoped = connection.execute(
        "SELECT COUNT(*) FROM v4_exchange_pool_liquidity_snapshots"
        " WHERE network = ?1 AND chain_id = ?2",
        (NETWORK, CHAIN_ID),
    ).fetchone()[0]
    distinct = connection.execute(
        "SELECT COUNT(DISTINCT pool_id) FROM v4_exchange_pool_liquidity_snapshots"
        " WHERE network = ?1 AND chain_id = ?2",
        (NETWORK, CHAIN_ID),
    ).fetchone()[0]
    duplicated = connection.execute(
        "SELECT COUNT(*) FROM (SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots"
        " WHERE network = ?1 AND chain_id = ?2 GROUP BY pool_id HAVING COUNT(*) > 1)" ,
        (NETWORK, CHAIN_ID),
    ).fetchone()[0]
    return {
        "rows_total": total,
        "rows_scoped": scoped,
        "distinct_pools_scoped": distinct,
        "pools_with_more_than_one_row": duplicated,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    store_path = sys.argv[1]
    report_path = sys.argv[2] if len(sys.argv) > 2 else None

    connection = sqlite3.connect("file:%s?mode=ro" % store_path, uri=True)
    connection.row_factory = sqlite3.Row

    report = {"store": os.path.abspath(store_path)}
    report["snapshot_cardinality"] = snapshot_cardinality(connection)

    legacy = read_pool_tables(connection)
    directional = read_directional_rows(connection)
    hops = read_hops(connection)

    scope_counts = {}
    for row in directional.values():
        scope_counts[row["scope_key"]] = scope_counts.get(row["scope_key"], 0) + 1
    report["directional_rows_by_scope"] = scope_counts

    same = 0
    differ = 0
    missing_legacy = 0
    missing_directional = 0
    liquidity_ratio = []
    price_ratio = []
    tick_delta = {}
    differing_detail = []
    for row in hops:
        directional_pool_id = row[4]
        if not directional_pool_id:
            missing_directional += 1
            continue
        new = directional.get(directional_pool_id)
        if new is None:
            missing_directional += 1
            continue
        old = legacy.get(row[3])
        if old is None:
            missing_legacy += 1
            continue
        fields_equal = (
            old["sqrt_price_x96"] == new["sqrt_price_x96"]
            and old["tick"] == new["tick"]
            and old["liquidity"] == new["liquidity"]
            and old["fee"] == new["fee"]
            and old["tick_spacing"] == new["tick_spacing"]
        )
        if fields_equal:
            same += 1
            continue
        differ += 1
        if old["liquidity"] and new["liquidity"]:
            liquidity_ratio.append(new["liquidity"] / old["liquidity"])
        if old["sqrt_price_x96"] and new["sqrt_price_x96"]:
            price_ratio.append(new["sqrt_price_x96"] / old["sqrt_price_x96"])
        if old["tick"] is not None and new["tick"] is not None:
            delta = new["tick"] - old["tick"]
            tick_delta[str(delta)] = tick_delta.get(str(delta), 0) + 1
        if len(differing_detail) < 40:
            differing_detail.append(
                {
                    "loop_key": row[0],
                    "hop_index": row[1],
                    "base_pool_id": row[3],
                    "directional_pool_id": directional_pool_id,
                    "scope": new["scope_key"],
                    "legacy_table": old["source_table"],
                    "legacy_sqrt_price_x96": old["sqrt_price_x96"],
                    "new_sqrt_price_x96": new["sqrt_price_x96"],
                    "legacy_tick": old["tick"],
                    "new_tick": new["tick"],
                    "legacy_liquidity": old["liquidity"],
                    "new_liquidity": new["liquidity"],
                }
            )

    report["hop_state_comparison"] = {
        "hops": len(hops),
        "identical_state": same,
        "differing_state": differ,
        "missing_legacy_state": missing_legacy,
        "missing_directional_row": missing_directional,
    }

    def summarize(values):
        if not values:
            return None
        values = sorted(values)
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
        return {
            "count": len(values),
            "min": values[0],
            "median": median,
            "max": values[-1],
        }

    report["hop_state_comparison"]["liquidity_ratio_new_over_legacy"] = summarize(liquidity_ratio)
    report["hop_state_comparison"]["sqrt_price_ratio_new_over_legacy"] = summarize(price_ratio)
    report["hop_state_comparison"]["tick_delta_histogram"] = dict(
        sorted(tick_delta.items(), key=lambda item: int(item[0]))
    )
    report["differing_detail_sample"] = differing_detail

    connection.close()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report_path:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
