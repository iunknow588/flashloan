#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the live closed-loop rows with the rehearsed clone's rows.

The live store is opened **read-only**; the clone is the store the
maintenance chain would publish into.  The comparison answers the question
the closed-loop checklist asks: which rows move, and by how much, when the
direction layer is refreshed and the materialization is applied.

Usage:
    python compare_clone_live_closed_loop_20261126.py <live.sqlite3> <clone.sqlite3> [--out <json>]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional


REPO = Path(__file__).resolve().parents[3]
DEFAULT_LIVE = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
DEFAULT_CLONE = REPO / "plan" / "runtime_evidence" / "kernel01_slice_4663.sqlite3"


def open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def columns(connection: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in connection.execute('PRAGMA table_info("%s")' % table)]


def read_loops(connection: sqlite3.Connection) -> Dict[str, Dict[str, object]]:
    available = set(columns(connection, "manager_v4_closed_loops"))
    wanted = [
        "loop_key",
        "anchor_token_key_id",
        "anchor_symbol",
        "directional_pool_ids_json",
        "single_tick_capacity_anchor_raw",
        "borrow_token_usdg_value_raw",
        "borrow_token_decimals",
        "cross_tick_review_threshold_usdg_raw",
        "cross_tick_capacity_anchor_raw",
        "status",
    ]
    binding = [
        name
        for name in (
            "single_tick_capacity_binding_directional_pool_id",
            "single_tick_capacity_binding_pool_id",
        )
        if name in available
    ]
    selected = [name for name in wanted if name in available] + binding
    rows = connection.execute(
        "SELECT %s FROM manager_v4_closed_loops WHERE network = 'robinhood' AND chain_id = 4663"
        % ", ".join('"%s"' % name for name in selected)
    ).fetchall()
    return {str(row[0]): dict(zip(selected, row)) for row in rows}


def read_hop_counts(connection: sqlite3.Connection) -> Dict[str, int]:
    rows = connection.execute(
        "SELECT loop_key, COUNT(*) FROM manager_v4_closed_loop_hops "
        "WHERE network = 'robinhood' AND chain_id = 4663 GROUP BY loop_key"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", default=str(DEFAULT_LIVE))
    parser.add_argument("--clone", default=str(DEFAULT_CLONE))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()

    live_connection = open_readonly(Path(arguments.live))
    clone_connection = open_readonly(Path(arguments.clone))
    try:
        live_loops = read_loops(live_connection)
        clone_loops = read_loops(clone_connection)
        live_hops = read_hop_counts(live_connection)
        clone_hops = read_hop_counts(clone_connection)
    finally:
        live_connection.close()
        clone_connection.close()

    shared = sorted(set(live_loops) & set(clone_loops))
    only_live = sorted(set(live_loops) - set(clone_loops))
    only_clone = sorted(set(clone_loops) - set(live_loops))

    value_changes: List[Dict[str, object]] = []
    capacity_changes: List[Dict[str, object]] = []
    path_changes: List[Dict[str, object]] = []
    for loop_key in shared:
        live_row = live_loops[loop_key]
        clone_row = clone_loops[loop_key]
        live_value = live_row.get("borrow_token_usdg_value_raw")
        clone_value = clone_row.get("borrow_token_usdg_value_raw")
        if live_value != clone_value:
            value_changes.append(
                {
                    "loopKey": loop_key,
                    "anchorSymbol": live_row.get("anchor_symbol"),
                    "liveBorrowTokenUsdgValueRaw": live_value,
                    "cloneBorrowTokenUsdgValueRaw": clone_value,
                    "liveHops": live_hops.get(loop_key),
                    "cloneHops": clone_hops.get(loop_key),
                }
            )
        live_capacity = live_row.get("single_tick_capacity_anchor_raw")
        clone_capacity = clone_row.get("single_tick_capacity_anchor_raw")
        if live_capacity != clone_capacity:
            capacity_changes.append(
                {
                    "loopKey": loop_key,
                    "liveSingleTickCapacityAnchorRaw": live_capacity,
                    "cloneSingleTickCapacityAnchorRaw": clone_capacity,
                }
            )
        live_pools = live_row.get("directional_pool_ids_json")
        clone_pools = clone_row.get("directional_pool_ids_json")
        if live_pools != clone_pools:
            path_changes.append(
                {
                    "loopKey": loop_key,
                    "liveDirectionalPoolIds": live_pools,
                    "cloneDirectionalPoolIds": clone_pools,
                }
            )

    payload = {
        "probe": "compare_clone_live_closed_loop_20261126",
        "readOnly": True,
        "live": arguments.live,
        "clone": arguments.clone,
        "loops": {
            "live": len(live_loops),
            "clone": len(clone_loops),
            "shared": len(shared),
            "onlyInLive": len(only_live),
            "onlyInClone": len(only_clone),
        },
        "hops": {
            "live": sum(live_hops.values()),
            "clone": sum(clone_hops.values()),
        },
        "borrowTokenUsdgValueChanges": len(value_changes),
        "singleTickCapacityChanges": len(capacity_changes),
        "directionalPoolPathChanges": len(path_changes),
        "valueChanges": value_changes,
        "capacityChanges": capacity_changes,
        "pathChanges": path_changes,
        "onlyInLiveLoops": only_live,
        "onlyInCloneLoops": only_clone,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if arguments.out:
        Path(arguments.out).write_text(text, encoding="utf-8")
        print("wrote: %s" % arguments.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
