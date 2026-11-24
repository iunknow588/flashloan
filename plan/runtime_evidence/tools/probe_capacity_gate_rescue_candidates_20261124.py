#!/usr/bin/env python3
"""Find the closed-loop paths the capacity gate drops **today** and check
whether a backup pool could rescue them.

A path is "capacity dropped" when it reached the capacity gate — meaning the
middle direction exists and has a pool — and still did not become a closed
loop. For every such path the probe lists the candidate lists of all three
hops, so the rescue potential is a number, not a guess.
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

    skipped_first: dict[str, set[str]] = defaultdict(set)
    skipped_third: dict[str, set[str]] = defaultdict(set)
    reason_by: dict[tuple[str, str, str], str] = {}
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
        reason_by[(anchor, token, row["candidate_role"])] = reason

    # Recommendation lists per (anchor, candidate, hop).
    recommendations: dict[tuple[str, str, str], list[str]] = {}
    for row in connection.execute(
        "SELECT anchor_token_key_id, candidate_token_key_id, hop, pool_id, rank"
        " FROM manager_dex_candidate_pool_recommendations"
        " ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank"
    ):
        key = (row["anchor_token_key_id"], row["candidate_token_key_id"], row["hop"])
        recommendations.setdefault(key, []).append(row["pool_id"])

    # Observation direction lists per (anchor, direction_key).
    observations: dict[tuple[str, str], list[str]] = {}
    for row in connection.execute(
        "SELECT anchor_token_key_id, direction_key, pool_ids_json"
        " FROM manager_market_observation_yz_pairs"
    ):
        observations[(row["anchor_token_key_id"], row["direction_key"])] = json.loads(
            row["pool_ids_json"] or "[]"
        )

    paths: list[dict] = []
    for anchor, first_tokens in skipped_first.items():
        for y_token in sorted(first_tokens):
            for z_token in sorted(skipped_third.get(anchor, ())):
                middle = observations.get((anchor, f"{y_token}->{z_token}"))
                if not middle:
                    continue
                first_list = recommendations.get((anchor, y_token, "first"), [])
                third_list = recommendations.get((anchor, z_token, "third"), [])
                paths.append(
                    {
                        "anchor": anchor,
                        "y": y_token,
                        "z": z_token,
                        "reason_first": reason_by.get((anchor, y_token, "first")),
                        "reason_third": reason_by.get((anchor, z_token, "third")),
                        "first_candidates": first_list,
                        "middle_candidates": middle,
                        "third_candidates": third_list,
                        "first_backups": len(first_list) - 1,
                        "middle_backups": len(middle) - 1,
                        "third_backups": len(third_list) - 1,
                        "any_backup": max(
                            len(first_list) - 1, len(middle) - 1, len(third_list) - 1
                        ),
                    }
                )

    rescueable = [path for path in paths if path["any_backup"] > 0]
    report = {
        "store": str(store),
        "capacity_dropped_paths": len(paths),
        "paths_with_a_backup": len(rescueable),
        "paths": paths,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
