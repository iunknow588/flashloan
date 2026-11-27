#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the middle hop's primary pool, old order against the direction layer.

The migration `20261125_017` made the market observation's primary pool a
**view column** over the direction layer's rank 1.  The base table still
carries the value the writer published -- the old liquidity index order -- so
the two answers can be compared row by row.

The report answers the questions the acceptance document asks:

* how many `(anchor, Y, Z)` triples change their primary pool, and how
  many pools are involved;
* whether any triple **loses** its primary;
* whether the change is concentrated in a few token pairs.

The store is opened **read-only**.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

QUERY = """
    SELECT
        yz.anchor_token_key_id,
        yz.direction_key,
        yz.token_x_key_id,
        yz.token_y_key_id,
        yz.has_direct_swap_pool,
        yz.direct_swap_pool_count,
        yz.pool_ids_json,
        base.optimal_pool_id AS old_primary,
        yz.optimal_pool_id AS new_primary
    FROM manager_market_observation_yz_pairs yz
    JOIN manager_market_observation_yz_pairs_base base
      ON base.network = yz.network
     AND base.chain_id = yz.chain_id
     AND base.protocol = yz.protocol
     AND base.scope_key = yz.scope_key
     AND base.anchor_token_key_id = yz.anchor_token_key_id
     AND base.direction_key = yz.direction_key
    ORDER BY yz.anchor_token_key_id, yz.direction_key
"""


def probe(database_path: Path) -> dict:
    connection = sqlite3.connect(
        "file:" + database_path.resolve().as_posix() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    report = {"schema": "middle-hop-pool-choice-comparison-v1"}
    try:
        rows = [dict(row) for row in connection.execute(QUERY)]
        with_pool = [row for row in rows if row["has_direct_swap_pool"]]
        changed = [row for row in with_pool if row["old_primary"] != row["new_primary"]]
        lost = [row for row in with_pool if row["new_primary"] is None]

        pools = set()
        for row in changed:
            if row["old_primary"]:
                pools.add(row["old_primary"])
            if row["new_primary"]:
                pools.add(row["new_primary"])

        by_pair = {}
        for row in changed:
            key = row["direction_key"]
            by_pair[key] = by_pair.get(key, 0) + 1
        ranked = sorted(by_pair.items(), key=lambda item: -item[1])

        report["population"] = {
            "observationRows": len(rows),
            "rowsWithDirectSwapPool": len(with_pool),
            "rowsWithoutDirectSwapPool": len(rows) - len(with_pool),
        }
        report["change"] = {
            "changedTriples": len(changed),
            "changeRateOfTriplesWithPool": round(
                len(changed) / len(with_pool), 6
            ) if with_pool else 0.0,
            "distinctPoolsInvolved": len(pools),
            "triplesThatLostTheirPrimary": len(lost),
            "unchangedTriples": len(with_pool) - len(changed),
        }
        report["concentration"] = {
            "distinctDirectionGroups": len(by_pair),
            "topGroups": [
                {"directionKey": key, "changedTriples": count}
                for key, count in ranked[:10]
            ],
            "shareOfChangesInTop10Groups": round(
                sum(count for _, count in ranked[:10]) / len(changed), 6
            ) if changed else 0.0,
        }
        report["samples"] = [
            {
                "anchorTokenKeyId": row["anchor_token_key_id"],
                "directionKey": row["direction_key"],
                "oldPrimary": row["old_primary"],
                "newPrimary": row["new_primary"],
                "directSwapPoolCount": row["direct_swap_pool_count"],
                "oldPrimaryWasInThePoolList": (
                    row["old_primary"] in (row["pool_ids_json"] or "")
                ),
                "newPrimaryWasInThePoolList": (
                    row["new_primary"] in (row["pool_ids_json"] or "")
                ),
            }
            for row in changed[:30]
        ]
    finally:
        connection.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    report = probe(Path(arguments.db))
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
