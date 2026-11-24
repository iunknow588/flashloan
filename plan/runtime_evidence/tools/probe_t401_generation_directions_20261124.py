#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Show how the hop directions and the capacity anchor differ per generation.

The profit probe reads three closed-loop generations.  This probe answers
the question that makes its numbers readable: what the direction column and
the capacity anchor actually hold in each of them.

Usage:
    python probe_t401_generation_directions_20261124.py <store.db> [...]
"""

import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE_KEY = "core4"


def read(store_path):
    connection = sqlite3.connect(
        "file:" + os.path.abspath(store_path).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    version = connection.execute(
        "SELECT closed_loop_version, materialized_checksum"
        " FROM manager_v4_closed_loop_library_state"
        " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY),
    ).fetchone()
    hops = connection.execute(
        "SELECT hop_direction, COUNT(*) AS n FROM manager_v4_closed_loop_hops"
        " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?"
        " GROUP BY hop_direction",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY),
    ).fetchall()
    loops = connection.execute(
        "SELECT COUNT(*) AS n, SUM(single_tick_capacity_anchor_raw IS NULL) AS null_capacity,"
        " SUM(single_tick_capacity_anchor_raw = '0') AS zero_capacity"
        " FROM manager_v4_closed_loops"
        " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY),
    ).fetchone()
    columns = [
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(manager_v4_closed_loops)"
        ).fetchall()
    ]
    extra = {}
    if "directional_pool_version" in columns:
        row = connection.execute(
            "SELECT directional_pool_version FROM manager_v4_closed_loops"
            " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=? LIMIT 1",
            (NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY),
        ).fetchone()
        extra["directional_pool_version"] = row[0] if row else None
    connection.close()
    return {
        "store": os.path.basename(store_path),
        "closedLoopVersion": version["closed_loop_version"] if version else None,
        "materializedChecksum": version["materialized_checksum"] if version else None,
        "directionalPoolVersion": extra.get("directional_pool_version"),
        "loops": loops["n"] if loops else 0,
        "capacityNull": loops["null_capacity"] if loops else 0,
        "capacityZero": loops["zero_capacity"] if loops else 0,
        "hopDirections": {
            str(row["hop_direction"]): row["n"] for row in hops
        },
    }


def main():
    report = []
    for store_path in sys.argv[1:]:
        report.append(read(store_path))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
