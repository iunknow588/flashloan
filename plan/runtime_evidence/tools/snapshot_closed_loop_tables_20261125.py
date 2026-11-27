#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot the closed-loop tables so two states can be compared row by row.

The store is opened **read-only**.  The snapshot holds the identity of every
loop, every hop and every optimization, so a later snapshot can be diffed
against it and the difference can be read as a list of concrete changes
instead of a checksum.

Usage:
    python snapshot_closed_loop_tables_20261125.py --out <file.json>
    python snapshot_closed_loop_tables_20261125.py --diff <a.json> <b.json>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

LOOPS = (
    "network, chain_id, protocol, scope_key, loop_key, anchor_token_key_id, "
    "token_y_key_id, token_z_key_id, path_text, pool_ids_json, "
    "backup_pool_ids_json, hop_candidate_counts_json, status, "
    "single_tick_capacity_anchor_raw, single_tick_capacity_binding_pool_id, "
    "directional_pool_ids_json, observation_version, candidate_edge_version"
)

HOPS = (
    "network, chain_id, protocol, scope_key, loop_key, hop_index, "
    "input_token_key_id, output_token_key_id, pool_id, base_pool_id, "
    "pool_selection_basis, optimization_id, directional_pool_id, hop_direction, "
    "directional_pool_rank"
)

OPTIMIZATIONS = (
    "network, chain_id, protocol, scope_key, loop_key, optimization_id, "
    "hop_index, original_pool_id, optimized_pool_id, optimization_basis, status"
)


def rows(connection: sqlite3.Connection, table: str, columns: str) -> list:
    return [
        dict(row)
        for row in connection.execute("SELECT " + columns + " FROM " + table)
    ]


def snapshot(database_path: Path) -> dict:
    connection = sqlite3.connect(
        "file:" + database_path.resolve().as_posix() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        report = {
            "schema": "closed-loop-snapshot-v1",
            "database": str(database_path),
            "state": {
                row["scope_key"]: dict(row)
                for row in connection.execute(
                    "SELECT scope_key, closed_loop_version, materialized_checksum,"
                    " loop_count, hop_count, optimization_count,"
                    " skipped_token_count, consumers_need_rebuild,"
                    " directional_pool_version FROM manager_v4_closed_loop_library_state"
                )
            },
            "loops": rows(connection, "manager_v4_closed_loops", LOOPS),
            "hops": rows(connection, "manager_v4_closed_loop_hops", HOPS),
            "optimizations": rows(
                connection, "manager_v4_closed_loop_optimizations", OPTIMIZATIONS
            ),
        }
    finally:
        connection.close()
    return report


def key_of(row: dict, fields: tuple) -> tuple:
    return tuple(row.get(field) for field in fields)


def diff(before: dict, after: dict) -> dict:
    result = {"schema": "closed-loop-snapshot-diff-v1"}

    result["state"] = {"before": before.get("state"), "after": after.get("state")}

    for name, fields in (
        ("loops", ("scope_key", "loop_key")),
        ("hops", ("scope_key", "loop_key", "hop_index")),
        ("optimizations", ("scope_key", "loop_key", "hop_index", "optimization_id")),
    ):
        left = {key_of(row, fields): row for row in before.get(name, [])}
        right = {key_of(row, fields): row for row in after.get(name, [])}
        added = [right[key] for key in right if key not in left]
        removed = [left[key] for key in left if key not in right]
        changed = []
        for key in right:
            if key not in left:
                continue
            differing = {
                field: {"before": left[key].get(field), "after": right[key].get(field)}
                for field in right[key]
                if left[key].get(field) != right[key].get(field)
            }
            if differing:
                changed.append({"key": dict(zip(fields, key)), "fields": differing})
        result[name] = {
            "rowsBefore": len(left),
            "rowsAfter": len(right),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "changedRows": changed[:200],
        }

    capacity_before = {
        row["loop_key"]: row.get("single_tick_capacity_anchor_raw")
        for row in before.get("loops", [])
    }
    capacity_after = {
        row["loop_key"]: row.get("single_tick_capacity_anchor_raw")
        for row in after.get("loops", [])
    }
    shared = set(capacity_before) & set(capacity_after)
    differences = [
        {
            "loopKey": key,
            "before": capacity_before[key],
            "after": capacity_after[key],
        }
        for key in sorted(shared)
        if capacity_before[key] != capacity_after[key]
    ]
    result["capacity"] = {
        "sharedLoops": len(shared),
        "loopsWhoseCapacityChanged": len(differences),
        "differences": differences[:200],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    parser.add_argument("--diff", nargs=2, default=None)
    arguments = parser.parse_args()

    if arguments.diff:
        before = json.loads(Path(arguments.diff[0]).read_text(encoding="utf-8"))
        after = json.loads(Path(arguments.diff[1]).read_text(encoding="utf-8"))
        report = diff(before, after)
    else:
        report = snapshot(Path(arguments.db))

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
