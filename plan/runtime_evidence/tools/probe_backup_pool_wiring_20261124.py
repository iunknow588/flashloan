#!/usr/bin/env python3
"""Read-only probe for the T-方闭.01 backup-pool wiring gap.

It answers five questions with live numbers:

1. How many rows does ``manager_v4_closed_loop_optimizations`` hold and what
   is their ``status`` / ``temporary_override`` distribution?
2. How many candidate pools does every ``(anchor, candidate, hop)`` group of
   the candidate-edge recommendation table carry, and how many pools does
   every market-observation direction carry?
3. How many eligible pools does every ``(pair, direction)`` group of the
   directional pool table carry, and how many of those groups exceed the
   candidate capacity knob?
4. How many closed loops were dropped because their capacity gate failed?

The probe never writes: the store is opened read-only.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_STORE = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")


def open_store(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection: sqlite3.Connection, sql: str) -> list[dict]:
    return [dict(row) for row in connection.execute(sql)]


def main() -> int:
    store = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORE
    connection = open_store(store)
    report: dict = {"store": str(store)}

    report["optimizations"] = rows(
        connection,
        "SELECT status, COUNT(*) AS rows "
        "FROM manager_v4_closed_loop_optimizations GROUP BY 1",
    )
    report["hop_temporary_override"] = rows(
        connection,
        "SELECT temporary_override, COUNT(*) AS rows "
        "FROM manager_v4_closed_loop_hops GROUP BY 1",
    )
    report["optimizations_by_hop"] = rows(
        connection,
        "SELECT hop_index, COUNT(*) AS rows, COUNT(DISTINCT loop_key) AS loops "
        "FROM manager_v4_closed_loop_optimizations GROUP BY 1 ORDER BY 1",
    )
    report["closed_loops"] = rows(
        connection,
        "SELECT COUNT(*) AS loops FROM manager_v4_closed_loops",
    )
    report["closed_loop_hops"] = rows(
        connection,
        "SELECT COUNT(*) AS hops FROM manager_v4_closed_loop_hops",
    )

    report["recommendation_group_sizes"] = rows(
        connection,
        "SELECT n AS pools_per_group, COUNT(*) AS groups FROM ("
        "  SELECT anchor_token_key_id, candidate_token_key_id, hop, COUNT(*) AS n"
        "  FROM manager_dex_candidate_pool_recommendations GROUP BY 1, 2, 3"
        ") GROUP BY 1 ORDER BY 1",
    )
    report["recommendation_groups_by_hop"] = rows(
        connection,
        "SELECT hop, COUNT(*) AS groups, MAX(n) AS max_pools FROM ("
        "  SELECT anchor_token_key_id, candidate_token_key_id, hop, COUNT(*) AS n"
        "  FROM manager_dex_candidate_pool_recommendations GROUP BY 1, 2, 3"
        ") GROUP BY 1 ORDER BY 1",
    )

    report["observation_direction_sizes"] = rows(
        connection,
        "SELECT n AS pools_per_direction, COUNT(*) AS directions FROM ("
        "  SELECT anchor_token_key_id, direction_key,"
        "         json_array_length(pool_ids_json) AS n"
        "  FROM manager_market_observation_yz_pairs"
        ") GROUP BY 1 ORDER BY 1",
    )

    report["directional_group_sizes"] = rows(
        connection,
        "SELECT n AS eligible_pools, COUNT(*) AS groups FROM ("
        "  SELECT pair_key_id, direction, COUNT(*) AS n"
        "  FROM manager_v4_directional_exchange_pools"
        "  WHERE pair_direction_rank IS NOT NULL GROUP BY 1, 2"
        ") GROUP BY 1 ORDER BY 1",
    )
    report["directional_groups_over_capacity"] = rows(
        connection,
        "SELECT COUNT(*) AS groups_over_5 FROM ("
        "  SELECT pair_key_id, direction, COUNT(*) AS n"
        "  FROM manager_v4_directional_exchange_pools"
        "  WHERE pair_direction_rank IS NOT NULL GROUP BY 1, 2"
        ") WHERE n > 5",
    )

    report["closed_loop_status"] = rows(
        connection,
        "SELECT status, COUNT(*) AS loops FROM manager_v4_closed_loops GROUP BY 1",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
