"""Probe: how often does the closed-loop Manager publish a new generation?

The review publish gate files **one** generation of reviews for the whole
chain, so the sweep that collects the evidence has to fit inside the
lifetime of one upstream generation.  This probe reads the mainnet store
read-only and reports the generation counters and their timestamps.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


MAINNET = Path("E:/2026OPC大赛/flashLoan/flashloan/db/local_store_4663.sqlite3")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else MAINNET
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    print("== manager tables ==")
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name LIKE 'manager%' ORDER BY name"
    ):
        count = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  {name}: {count}")
    print("== closed loop library state ==")
    for row in connection.execute(
        "SELECT network, chain_id, protocol, scope_key, closed_loop_version, "
        "materialized_checksum, candidate_edge_version, candidate_edge_checksum, "
        "observation_version, observation_checksum, pool_scope_checksum, "
        "anchor_count, loop_count, hop_count, consumers_need_rebuild, "
        "generated_at, updated_at "
        "FROM manager_v4_closed_loop_library_state"
    ):
        print("  ", row)
    print("== closed loop rows (updated_at spread) ==")
    for row in connection.execute(
        "SELECT MIN(updated_at), MAX(updated_at), COUNT(*) FROM manager_v4_closed_loops"
    ):
        print("  ", row)
    print("== review library generations ==")
    for row in connection.execute(
        "SELECT closed_loop_version, COUNT(*), MIN(updated_at), MAX(updated_at) "
        "FROM manager_v4_reviews GROUP BY closed_loop_version ORDER BY closed_loop_version"
    ):
        print("  ", row)
    print("== upstream library states ==")
    for table in (
        "manager_market_observation_library_state",
        "manager_dex_candidate_library_state",
        "manager_review_library_state",
        "manager_v4_directional_pool_library_state",
    ):
        for row in connection.execute(f"SELECT * FROM {table}"):
            print(f"  {table}: {row}")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
