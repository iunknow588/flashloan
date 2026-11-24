#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the closed-loop catalog with the market-observation coverage.

The scanner can only produce a route for a loop whose market observation
exists.  A loop without observation data never becomes a worker group, so it
never gets review evidence, so the review generation cannot cover the
catalog.  This probe reads that coverage directly.

Read-only.

Usage:
    python probe_t401_observation_coverage_20261124.py <store.db>
"""

import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"


def main():
    path = sys.argv[1]
    connection = sqlite3.connect(
        "file:" + path.replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row

    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    print("tables:")
    print(json.dumps(
        [name for name in tables
         if any(key in name.lower()
                for key in ("observ", "candidate", "sequence", "edge",
                            "closed_loop", "review", "liquidity"))],
        ensure_ascii=False,
        indent=1,
    ))

    catalog = {
        str(row[0]).strip().lower()
        for row in connection.execute(
            "SELECT loop_key FROM manager_v4_closed_loops"
            " WHERE network=? AND chain_id=? AND protocol=?",
            (NETWORK, CHAIN_ID, PROTOCOL),
        )
    }
    print("catalog loops:", len(catalog))

    for table in tables:
        if not any(key in table.lower() for key in ("observ", "candidate", "sequence")):
            continue
        columns = [
            row[1]
            for row in connection.execute('PRAGMA table_info("%s")' % table)
        ]
        print("\n== %s ==" % table)
        print("columns:", json.dumps(columns, ensure_ascii=False))
        try:
            count = connection.execute(
                'SELECT COUNT(*) FROM "%s"' % table
            ).fetchone()[0]
        except Exception as error:
            print("count failed:", error)
            continue
        print("rows:", count)
        for column in columns:
            if not any(key in column.lower()
                       for key in ("loop", "group", "key", "sequence", "symbol")):
                continue
            try:
                distinct = connection.execute(
                    'SELECT COUNT(DISTINCT "%s") FROM "%s"' % (column, table)
                ).fetchone()[0]
            except Exception:
                continue
            print("  distinct %s = %s" % (column, distinct))

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
