#!/usr/bin/env python3
"""Count the rows that tie on the direction layer's main order keys.

The main order of a (pair, direction) group is

    directional_output desc -> tick ordering rank desc -> fee asc

and the base pool id is the last deterministic key. The ruling requires one
more key between the fee and the pool id: when the main keys tie, the pool
with the larger direction capacity wins. This probe counts the rows where
that new key would decide the order, i.e. the rows whose order the new key
can change.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_STORE = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")


def main() -> int:
    store = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORE
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    groups: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    for row in connection.execute(
        "SELECT scope_key, pair_key_id, direction, base_pool_id, fee, tick,"
        "       directional_output, input_capacity_raw"
        " FROM manager_v4_directional_exchange_pools"
        " WHERE pair_direction_rank IS NOT NULL"
    ):
        tick = row["tick"]
        tick_rank = None if tick is None else (-tick if row["direction"] == 0 else tick)
        groups[(row["scope_key"], row["pair_key_id"], row["direction"])].append(
            {
                "base_pool_id": row["base_pool_id"],
                "fee": row["fee"],
                "tick_rank": tick_rank,
                "directional_output": row["directional_output"],
                "capacity": row["input_capacity_raw"],
                "rank": None,
            }
        )

    tied_groups = 0
    tied_rows = 0
    capacity_decides = 0
    examples: list[dict] = []
    for key, members in groups.items():
        buckets: dict[tuple, list[dict]] = defaultdict(list)
        for member in members:
            buckets[(member["directional_output"], member["tick_rank"], member["fee"])].append(
                member
            )
        for bucket_key, bucket in buckets.items():
            if len(bucket) < 2:
                continue
            tied_groups += 1
            tied_rows += len(bucket)
            capacities = {
                (member["capacity"] or "").strip() for member in bucket
            }
            if len(capacities) > 1:
                capacity_decides += 1
                if len(examples) < 10:
                    examples.append(
                        {
                            "scope_key": key[0],
                            "pair_key_id": key[1],
                            "direction": key[2],
                            "members": bucket,
                        }
                    )

    report = {
        "store": str(store),
        "groups": len(groups),
        "tied_buckets": tied_groups,
        "rows_in_ties": tied_rows,
        "buckets_where_capacity_decides": capacity_decides,
        "examples": examples,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
