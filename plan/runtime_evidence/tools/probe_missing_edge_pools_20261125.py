"""For every closed-loop hop edge the bridge cannot resolve, locate the pool
the closed loop actually publishes and check it against the live surfaces.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"


def main() -> None:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only = ON")
    cur = conn.cursor()

    hops = cur.execute(
        "SELECT loop_key, hop_index, input_address, output_address, pool_id, base_pool_id, "
        "       directional_pool_id, directional_scope_key, directional_pool_rank, "
        "       pool_selection_basis "
        "FROM manager_v4_closed_loop_hops "
        "WHERE scope_key = 'core4'"
    ).fetchall()

    live_cand = {
        str(r[0]).lower()
        for r in cur.execute(
            "SELECT DISTINCT pool_id FROM dex_candidate_pools "
            "WHERE chain_id = 4663 AND usable_for_observation = 1"
        )
    }
    live_obs = {
        str(r[0]).lower()
        for r in cur.execute(
            "SELECT DISTINCT pool_id FROM market_observation_intermediate_pools "
            "WHERE chain_id = 4663"
        )
    }
    live_base = {
        str(r[0]).lower()
        for r in cur.execute("SELECT DISTINCT pool_id FROM v4_exchange_pools")
    }

    print(f"live dex_candidate_pools pool_ids = {len(live_cand)}")
    print(f"live observation pool_ids        = {len(live_obs)}")
    print(f"live v4_exchange_pools pool_ids   = {len(live_base)}")

    seen: set[tuple[str, str]] = set()
    for row in hops:
        (
            loop_key,
            hop_index,
            inp,
            outp,
            pool_id,
            base_pool_id,
            dp_id,
            dp_scope,
            dp_rank,
            basis,
        ) = row
        edge = tuple(sorted((str(inp).lower(), str(outp).lower())))
        if edge in seen:
            continue
        seen.add(edge)
        pool = str(base_pool_id or pool_id or "").lower()
        in_cand = pool in live_cand
        in_obs = pool in live_obs
        in_base = pool in live_base
        print(
            f"edge {edge[0][:12]}..<->{edge[1][:12]}.. pool={pool[:14]}.. "
            f"cand={in_cand} obs={in_obs} base={in_base} "
            f"scope={dp_scope} rank={dp_rank} basis={basis} loops={loop_key}/{hop_index}"
        )
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
