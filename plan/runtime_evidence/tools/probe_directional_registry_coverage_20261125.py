#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the directional layer's coverage of the base-pool tables.

The runtime's in-memory pool registry is going to hold **directional**
pools.  Before that change lands, this probe answers the question that
decides whether the change is safe: does the directional layer cover
every base pool the runtime currently loads, and does it carry every
field the runtime reads?

The store is opened **read-only** (`mode=ro` + `PRAGMA query_only = ON`).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

BASE_TABLES = (
    "v4_exchange_pools_core4",
    "v4_exchange_pools_candidate_exchange",
)


def open_ro(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def scalar(connection: sqlite3.Connection, sql: str, params: tuple) -> object:
    cursor = connection.execute(sql, params)
    row = cursor.fetchone()
    return None if row is None else row[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--protocol", default="uniswap_v4")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    connection = open_ro(args.db)
    network = args.network
    chain_id = args.chain_id
    protocol = args.protocol

    report: dict = {
        "database": str(args.db),
        "network": network,
        "chain_id": chain_id,
        "protocol": protocol,
        "read_only": True,
    }

    # --- 1. how many base pools the runtime loads today -----------------
    base_rows: list = []
    for table in BASE_TABLES:
        count = scalar(
            connection,
            f"SELECT COUNT(*) FROM {table} WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
            (network, chain_id, protocol),
        )
        cursor = connection.execute(
            f"SELECT pool_id, token0, token1, pool_manager, fee, tick_spacing, hooks "
            f"FROM {table} WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
            (network, chain_id, protocol),
        )
        rows = cursor.fetchall()
        base_rows.extend(rows)
        report[f"base_table::{table}"] = {"rows": count}
    report["base_total_rows"] = len(base_rows)
    base_ids = {str(row[0]).lower() for row in base_rows}
    report["base_distinct_pool_ids"] = len(base_ids)

    # --- 2. the directional layer ---------------------------------------
    cursor = connection.execute(
        "SELECT base_pool_id, direction, directional_pool_id, pool_manager, token0, token1, "
        "fee, tick_spacing, hooks, pool_key_json, scope_key, pair_direction_rank, "
        "input_token_key_id, output_token_key_id, zero_for_one, pool_status "
        "FROM manager_v4_directional_exchange_pools "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
        (network, chain_id, protocol),
    )
    directional_rows = cursor.fetchall()
    report["directional_rows"] = len(directional_rows)
    directional_base_ids = {str(row[0]).lower() for row in directional_rows}
    report["directional_distinct_base_pool_ids"] = len(directional_base_ids)
    report["directional_scopes"] = sorted({str(row[10]) for row in directional_rows})

    # --- 3. coverage -----------------------------------------------------
    covered = base_ids & directional_base_ids
    uncovered = base_ids - directional_base_ids
    extra = directional_base_ids - base_ids
    report["base_covered_by_directional"] = len(covered)
    report["base_not_covered_by_directional"] = len(uncovered)
    report["directional_base_ids_without_base_row"] = len(extra)
    report["coverage_ratio"] = (
        round(len(covered) / len(base_ids), 6) if base_ids else None
    )

    # --- 4. per-base-pool direction multiplicity -------------------------
    by_base: dict = {}
    for row in directional_rows:
        by_base.setdefault(str(row[0]).lower(), []).append(int(row[1]))
    single = sum(1 for dirs in by_base.values() if len(dirs) != 2)
    report["base_pools_without_exactly_two_directions"] = single
    report["direction_multiplicity_histogram"] = {
        str(count): sum(1 for dirs in by_base.values() if len(dirs) == count)
        for count in sorted({len(dirs) for dirs in by_base.values()})
    }

    # --- 5. field-level agreement on the covered set ---------------------
    base_by_id = {str(row[0]).lower(): row for row in base_rows}
    disagree = {"pool_manager": 0, "token0": 0, "token1": 0, "fee": 0,
                "tick_spacing": 0, "hooks": 0}

    def normalize(value: object) -> str:
        text = str(value).strip().lower()
        if text in ("0x0", "0x00", "0x0000"):
            return "0x" + "0" * 40
        return text

    for row in directional_rows:
        key = str(row[0]).lower()
        base = base_by_id.get(key)
        if base is None:
            continue
        pairs = (
            ("pool_manager", 3, 3),
            ("token0", 1, 4),
            ("token1", 2, 5),
            ("fee", 4, 6),
            ("tick_spacing", 5, 7),
            ("hooks", 6, 8),
        )
        for name, base_index, dir_index in pairs:
            left = normalize(base[base_index])
            right = normalize(row[dir_index])
            if left != right:
                disagree[name] += 1
    report["identity_disagreements_on_covered"] = disagree

    # --- 6. the snapshot columns the directional layer carries ---------
    columns = [
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(manager_v4_directional_exchange_pools)"
        ).fetchall()
    ]
    wanted = (
        "sqrt_price_x96",
        "tick",
        "liquidity",
        "price",
        "depth_status",
        "depth_capacity_raw",
        "input_capacity_raw",
        "input_capacity_measured",
        "directional_output",
        "directional_output_measured",
    )
    report["directional_columns_present"] = {
        name: (name in columns) for name in wanted
    }

    # --- 7. the liquidity snapshot table the runtime reads today --------
    snapshot_count = scalar(
        connection,
        "SELECT COUNT(*) FROM v4_exchange_pool_liquidity_snapshots "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
        (network, chain_id, protocol),
    )
    report["liquidity_snapshot_rows"] = snapshot_count
    snapshot_columns = [
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(v4_exchange_pool_liquidity_snapshots)"
        ).fetchall()
    ]
    report["liquidity_snapshot_columns"] = snapshot_columns

    # --- 8. what the closed-loop catalog actually needs ------------------
    hop_cursor = connection.execute(
        "SELECT hop_direction, base_pool_id, directional_pool_id, directional_scope_key, "
        "directional_pool_rank, pool_selection_basis "
        "FROM manager_v4_closed_loop_hops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
        (network, chain_id, protocol),
    )
    hops = hop_cursor.fetchall()
    report["closed_loop_hop_rows"] = len(hops)
    hop_base_ids = {str(row[1]).lower() for row in hops}
    report["closed_loop_distinct_base_pool_ids"] = len(hop_base_ids)
    report["closed_loop_hops_covered_by_directional"] = len(
        hop_base_ids & directional_base_ids
    )
    report["closed_loop_hops_missing_from_directional"] = len(
        hop_base_ids - directional_base_ids
    )
    report["closed_loop_missing_sample"] = sorted(
        hop_base_ids - directional_base_ids
    )[:10]

    # the scope the hops live in must be the scope the directional rows live in
    hop_scopes = sorted({str(row[3]) for row in hops})
    report["closed_loop_hop_scopes"] = hop_scopes
    for scope in hop_scopes:
        scope_ids = {
            str(row[0]).lower()
            for row in directional_rows
            if str(row[10]) == scope
        }
        scope_hops = {
            str(row[1]).lower() for row in hops if str(row[3]) == scope
        }
        report[f"scope::{scope}"] = {
            "directional_rows": sum(
                1 for row in directional_rows if str(row[10]) == scope
            ),
            "hop_base_pool_ids": len(scope_hops),
            "hop_base_pool_ids_covered": len(scope_hops & scope_ids),
            "hop_base_pool_ids_missing": len(scope_hops - scope_ids),
        }

    # --- 9. the liquidity snapshot coverage of the hop base pools -------
    snapshot_ids = {
        str(row[0]).lower()
        for row in connection.execute(
            "SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots "
            "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
            (network, chain_id, protocol),
        ).fetchall()
    }
    report["hop_base_pool_ids_in_liquidity_snapshot"] = len(
        hop_base_ids & snapshot_ids
    )
    report["hop_base_pool_ids_missing_from_liquidity_snapshot"] = len(
        hop_base_ids - snapshot_ids
    )
    report["base_pool_ids_in_liquidity_snapshot"] = len(
        base_ids & snapshot_ids
    )

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
