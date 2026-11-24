#!/usr/bin/env python3
"""Compare the direction layer's (pair, direction) groups with the candidate
lists the closed loop actually uses.

For every published closed-loop hop the probe resolves the hop's direction
group in ``manager_v4_directional_exchange_pools`` and reports

* how many eligible pools the group holds (the *qualified* count), and
* how many of them the closed loop never saw (the *dropped* count).

The second number is the concrete size of the "truncation throws the tail
away" gap: those pools are ranked by the direction layer but are absent from
the candidate list the closed loop was built from.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_STORE = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")


def main() -> int:
    store = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORE
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    report: dict = {"store": str(store)}

    scopes = connection.execute(
        "SELECT scope_key, COUNT(*) AS rows, COUNT(DISTINCT pair_key_id) AS pairs,"
        " COUNT(DISTINCT base_pool_id) AS pools"
        " FROM manager_v4_directional_exchange_pools GROUP BY 1 ORDER BY 1"
    ).fetchall()
    report["directional_scopes"] = [dict(row) for row in scopes]

    # The direction group of every published hop.
    hops = connection.execute(
        "SELECT h.loop_key, h.hop_index, h.pool_id, h.hop_direction,"
        "       h.directional_scope_key, h.directional_pool_id"
        " FROM manager_v4_closed_loop_hops h"
        " WHERE h.directional_pool_id IS NOT NULL"
    ).fetchall()

    group_cache: dict[tuple[str, str, int], list[tuple[int, str]]] = {}
    for hop in hops:
        scope = hop["directional_scope_key"]
        direction = hop["hop_direction"]
        pool_id = hop["pool_id"]
        if scope is None or direction is None:
            continue
        key = (scope, pool_id, direction)
        if key in group_cache:
            continue
        row = connection.execute(
            "SELECT pair_key_id FROM manager_v4_directional_exchange_pools"
            " WHERE scope_key = ?1 AND base_pool_id = ?2 AND direction = ?3 LIMIT 1",
            (scope, pool_id, direction),
        ).fetchone()
        if row is None:
            continue
        group_cache[key] = [
            (rank, base)
            for rank, base in connection.execute(
                "SELECT pair_direction_rank, base_pool_id"
                " FROM manager_v4_directional_exchange_pools"
                " WHERE scope_key = ?1 AND pair_key_id = ?2 AND direction = ?3"
                "   AND pair_direction_rank IS NOT NULL"
                " ORDER BY pair_direction_rank",
                (scope, row["pair_key_id"], direction),
            )
        ]

    # The candidate list the closed loop was built from, per hop.
    used_lists: dict[tuple[str, int], list[str]] = {}
    for loop in connection.execute(
        "SELECT loop_key, backup_pool_ids_json, hop_candidate_counts_json"
        " FROM manager_v4_closed_loops"
    ):
        backups = json.loads(loop["backup_pool_ids_json"] or "{}")
        counts = json.loads(loop["hop_candidate_counts_json"] or "{}")
        used_lists[loop["loop_key"]] = [backups, counts]

    per_hop_counts: dict[int, list[int]] = {1: [], 2: [], 3: []}
    dropped_total = 0
    dropped_examples: list[dict] = []
    used_rank_outside = 0
    for hop in hops:
        scope = hop["directional_scope_key"]
        direction = hop["hop_direction"]
        pool_id = hop["pool_id"]
        if scope is None or direction is None:
            continue
        key = (scope, pool_id, direction)
        group = group_cache.get(key)
        if not group:
            continue
        hop_index = hop["hop_index"]
        per_hop_counts.setdefault(hop_index, []).append(len(group))
        rank_of_used = next(
            (rank for rank, base in group if base == pool_id),
            None,
        )
        if rank_of_used is None:
            used_rank_outside += 1
            continue
        backups, counts = used_lists.get(hop["loop_key"], [{}, {}])
        hop_name = {1: "first", 2: "middle", 3: "third"}[hop_index]
        candidate_count = int(counts.get(str(hop_index), 0) or 0)
        backup_count = len(backups.get(hop_name, []) or [])
        seen = candidate_count
        dropped = len(group) - seen
        if dropped > 0:
            dropped_total += dropped
            if len(dropped_examples) < 12:
                dropped_examples.append(
                    {
                        "loop_key": hop["loop_key"],
                        "hop_index": hop_index,
                        "scope_key": scope,
                        "direction": direction,
                        "group_size": len(group),
                        "candidate_count": candidate_count,
                        "backup_count": backup_count,
                        "used_pool_id": pool_id,
                        "used_rank": rank_of_used,
                        "dropped": dropped,
                        "group": [f"{rank}:{base}" for rank, base in group],
                    }
                )

    report["hop_group_size_distribution"] = {
        str(hop_index): {
            "hops": len(values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": round(sum(values) / len(values), 3) if values else None,
        }
        for hop_index, values in sorted(per_hop_counts.items())
    }
    report["used_pool_not_in_group"] = used_rank_outside
    report["dropped_total_occurrences"] = dropped_total
    report["dropped_examples"] = dropped_examples

    print(json.dumps(report, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
