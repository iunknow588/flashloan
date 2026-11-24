#!/usr/bin/env python3
"""Measure whether the backup pools of the capacity-dropped paths could
rescue them.

For every hop of every capacity-dropped path the probe lists the candidate
pools together with the direction layer's measured capacity of the hop's
direction. A backup can only rescue the path when its own capacity is above
the closed-loop floor.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_STORE = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")
FLOOR = 100


def main() -> int:
    store = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORE
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    skipped_first: dict[str, set[str]] = defaultdict(set)
    skipped_third: dict[str, set[str]] = defaultdict(set)
    for row in connection.execute(
        "SELECT anchor_token_key_id, token_key_id, candidate_role, reason"
        " FROM manager_v4_closed_loop_skipped_tokens"
    ):
        reason = row["reason"] or ""
        if not reason.startswith("path_single_tick_bound_unusable") and not reason.startswith(
            "path_capacity_below_minimum"
        ):
            continue
        anchor = row["anchor_token_key_id"]
        token = row["token_key_id"]
        if row["candidate_role"] == "first":
            skipped_first[anchor].add(token)
        else:
            skipped_third[anchor].add(token)

    recommendations: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in connection.execute(
        "SELECT anchor_token_key_id, candidate_token_key_id, hop, pool_id"
        " FROM manager_dex_candidate_pool_recommendations"
        " ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank"
    ):
        key = (row["anchor_token_key_id"], row["candidate_token_key_id"], row["hop"])
        recommendations[key].append(row["pool_id"])

    observations: dict[tuple[str, str], list[str]] = {}
    for row in connection.execute(
        "SELECT anchor_token_key_id, direction_key, pool_ids_json"
        " FROM manager_market_observation_yz_pairs"
    ):
        observations[(row["anchor_token_key_id"], row["direction_key"])] = json.loads(
            row["pool_ids_json"] or "[]"
        )

    def direction_capacity(scope: str, base_pool_id: str, token_in: str) -> dict | None:
        row = connection.execute(
            "SELECT d.directional_pool_id, d.direction, d.pair_direction_rank,"
            "       d.input_capacity_raw, d.depth_capacity_raw, d.input_token_key_id"
            " FROM manager_v4_directional_exchange_pools d"
            " WHERE d.scope_key = ?1 AND d.base_pool_id = ?2 AND d.input_token_key_id = ?3",
            (scope, base_pool_id, token_in),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    paths: list[dict] = []
    for anchor, first_tokens in skipped_first.items():
        for y_token in sorted(first_tokens):
            for z_token in sorted(skipped_third.get(anchor, ())):
                middle = observations.get((anchor, f"{y_token}->{z_token}"))
                if not middle:
                    continue
                hops = {
                    1: ("core4", recommendations.get((anchor, y_token, "first"), []), anchor),
                    2: ("candidate_exchange", middle, y_token),
                    3: ("core4", recommendations.get((anchor, z_token, "third"), []), z_token),
                }
                detail: dict[str, list[dict]] = {}
                rescuable = False
                for hop_index, (scope, pools, token_in) in hops.items():
                    entries = []
                    for rank, pool_id in enumerate(pools, start=1):
                        capacity = direction_capacity(scope, pool_id, token_in)
                        measured = None
                        if capacity is not None:
                            measured = capacity.get("depth_capacity_raw")
                        entries.append(
                            {
                                "rank": rank,
                                "pool_id": pool_id,
                                "depth_capacity_raw": measured,
                                "input_capacity_raw": (
                                    capacity.get("input_capacity_raw") if capacity else None
                                ),
                                "direction_row": bool(capacity),
                                "above_floor": (
                                    measured is not None and int(measured) >= FLOOR
                                ),
                            }
                        )
                    if any(entry["above_floor"] for entry in entries[1:]):
                        rescuable = True
                    detail[str(hop_index)] = entries
                paths.append(
                    {
                        "anchor": anchor,
                        "y": y_token,
                        "z": z_token,
                        "hops": detail,
                        "has_usable_backup": rescuable,
                    }
                )

    report = {
        "store": str(store),
        "capacity_floor_raw": FLOOR,
        "paths": len(paths),
        "paths_with_usable_backup": sum(1 for path in paths if path["has_usable_backup"]),
        "detail": paths,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
