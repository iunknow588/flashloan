#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clone the slice of the chain store the closed-loop layer reads.

The live chain store is twelve gigabytes, so a full copy is wasteful, and a
copy of a live store is never safe while the runtime writes to it.  This
tool builds a **thin clone**: the schema of every table is copied verbatim,
and only the rows of the tables the closed-loop layer reads are copied.

The result is a store the closed-loop layer can materialize against, and the
materialization writes into the clone only — the live store is opened
read-only and is never written to.

Usage:
    python clone_closed_loop_slice_20261125.py <source.sqlite3> <target.sqlite3> [--measure]
"""

import json
import os
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

# Every table the closed-loop layer reads, plus the tables it publishes into
# and the tables the legacy comparison reads.  The rest of the store — the
# reviews, the broadcast ledger, the swap activity, the tick history — is not
# touched by the closed-loop layer, so it is not cloned.
SLICE_TABLES: Tuple[str, ...] = (
    "manager_dex_candidate_library_state",
    "manager_dex_candidate_anchor_tokens",
    "manager_dex_candidate_pool_recommendations",
    "manager_dex_candidate_pools",
    "manager_market_observation_library_state",
    "manager_market_observation_yz_pairs",
    "manager_market_observation_intermediate_pools",
    "manager_market_observation_liquidity_snapshots",
    "manager_v4_directional_exchange_pools",
    "manager_v4_directional_pool_library_state",
    "manager_v4_closed_loop_library_state",
    "manager_v4_closed_loops",
    "manager_v4_closed_loop_hops",
    "manager_v4_closed_loop_optimizations",
    "manager_v4_closed_loop_runtime_state",
    "manager_v4_closed_loop_skipped_tokens",
    "manager_write_requests",
    "token_core",
    "token_stocks",
    "token_extension",
    "token_test",
    "token_library_state",
    "token_usdg_valuations",
    "v4_exchange_pools",
    "v4_exchange_pools_candidate_exchange",
    "v4_exchange_pools_core4",
    "v4_exchange_pool_liquidity_snapshots",
    "v4_closed_loops",
    "v4_closed_loop_hops",
    "schema_migrations",
)


def open_readonly(path: str) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % path.replace("\\", "/").replace("?", "%3f")
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def schema_of(connection: sqlite3.Connection) -> List[Tuple[str, str]]:
    rows = connection.execute(
        "SELECT type, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"
    )
    return [(row[0], row[1]) for row in rows]


def measure(connection: sqlite3.Connection) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ):
        counts[name] = connection.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    measure_only = "--measure" in arguments
    arguments = [argument for argument in arguments if argument != "--measure"]
    if len(arguments) < 1:
        print(json.dumps({"ok": False, "error": "usage: <source> [target] [--measure]"}))
        return 2
    source = arguments[0]
    target = arguments[1] if len(arguments) > 1 else None
    connection = open_readonly(source)
    try:
        counts = measure(connection)
        if measure_only or target is None:
            payload = {
                "ok": True,
                "source": source,
                "tableCounts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                "sliceTables": list(SLICE_TABLES),
                "sliceRows": sum(counts.get(name, 0) for name in SLICE_TABLES),
                "allRows": sum(counts.values()),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if os.path.exists(target):
            os.remove(target)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(target + suffix):
                os.remove(target + suffix)
        clone = sqlite3.connect(target)
        copied: Dict[str, int] = {}
        try:
            for _kind, sql in schema_of(connection):
                try:
                    clone.execute(sql)
                except sqlite3.OperationalError:
                    # An index of a table that was already created is harmless.
                    pass
            for name in SLICE_TABLES:
                if name not in counts:
                    continue
                rows = connection.execute('SELECT * FROM "%s"' % name).fetchall()
                if rows:
                    placeholders = ",".join("?" for _ in rows[0])
                    clone.executemany(
                        'INSERT INTO "%s" VALUES (%s)' % (name, placeholders), rows
                    )
                copied[name] = len(rows)
            clone.commit()
        finally:
            clone.close()
        payload = {
            "ok": True,
            "source": source,
            "target": target,
            "copiedTables": copied,
            "copiedRows": sum(copied.values()),
            "targetBytes": os.path.getsize(target),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
