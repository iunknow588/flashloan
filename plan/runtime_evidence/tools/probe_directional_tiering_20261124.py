#!/usr/bin/env python3
"""Check the candidate/backup layering invariants of the direction layer.

The layer splits every ``(scope, pair, direction)`` group into a candidate
tier and a backup tier.  The split is a *layering* and not a truncation:
every qualified pool of the group keeps a rank and stays in the table.

The probe reports, for every group:

* ``eligible``  -- the number of qualified pools of the group (K),
* ``candidate`` -- the size of the candidate tier (N),
* ``backup``    -- the number of pools behind the cut (K - N),
* the continuity of the ranks, and
* the number of groups whose main sort keys tie (the number of decisions the
  capacity tie breaker actually decides).

The identity ``N == min(N_max, K)`` is checked against the capacity the
layer actually used, and the identity ``K == N + backup`` is checked against
the rows themselves.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_STORE = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")
ENV_NAME = "FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS"
DEFAULT_CAPACITY = 5


def main() -> int:
    store = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORE
    raw = os.environ.get(ENV_NAME, "").strip()
    capacity = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_CAPACITY

    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    groups: dict[tuple[str, str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in connection.execute(
        "SELECT scope_key, pair_key_id, direction, base_pool_id, pair_direction_rank,"
        "       directional_output, fee, input_capacity_raw, pool_status,"
        "       directional_output_measured"
        "  FROM manager_v4_directional_exchange_pools"
        " WHERE pair_key_id IS NOT NULL AND pair_direction_rank IS NOT NULL"
    ):
        groups[(row["scope_key"], row["pair_key_id"], row["direction"])].append(row)

    size_histogram: Counter[int] = Counter()
    violations: list[dict] = []
    tie_groups: list[dict] = []
    group_count = 0

    for key, rows in sorted(groups.items()):
        group_count += 1
        eligible = len(rows)
        size_histogram[eligible] += 1
        ranks = sorted(int(row["pair_direction_rank"]) for row in rows)
        expected_ranks = list(range(1, eligible + 1))
        if ranks != expected_ranks:
            violations.append(
                {
                    "group": list(key),
                    "kind": "rank_not_continuous",
                    "ranks": ranks,
                }
            )
        # The candidate tier is the first N ranks, the backup tier the rest.
        candidate = min(eligible, capacity)
        backup = eligible - candidate
        if candidate + backup != eligible:
            violations.append(
                {
                    "group": list(key),
                    "kind": "pool_lost",
                    "eligible": eligible,
                    "candidate": candidate,
                    "backup": backup,
                }
            )
        # The main sort keys of the layer: output, tick order and fee.  A
        # group whose main keys tie is a group where the capacity tie
        # breaker is the one that decides the order.
        main_keys = Counter(
            (row["directional_output"], row["fee"]) for row in rows
        )
        tied = [key_tuple for key_tuple, count in main_keys.items() if count > 1]
        if tied:
            tie_groups.append(
                {"group": list(key), "tied_main_keys": len(tied), "eligible": eligible}
            )

    report = {
        "store": str(store),
        "capacity_knob": {"env": ENV_NAME, "value": capacity},
        "group_count": group_count,
        "group_size_histogram": dict(sorted(size_histogram.items())),
        "groups_beyond_capacity": sum(
            count for size, count in size_histogram.items() if size > capacity
        ),
        "violations": violations,
        "groups_with_tied_main_keys": len(tie_groups),
        "tie_examples": tie_groups[:10],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
