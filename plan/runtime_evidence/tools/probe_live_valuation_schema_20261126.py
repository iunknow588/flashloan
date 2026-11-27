"""Read-only probe: the tables the T-闭.02 valuation A/B reads.

The probe opens the live store in read-only mode and never writes:
`mode=ro` plus `PRAGMA query_only = ON`.  It prints the column list of
the four tables the A/B needs, so the Rust probe can be written against
the real layout instead of against an assumed one.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STORE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"

TABLES = (
    "v4_exchange_pools_candidate_exchange",
    "v4_exchange_pools_core4",
    "v4_exchange_pool_liquidity_snapshots",
    "manager_v4_closed_loops",
    "token_library_catalog",
)


def main() -> int:
    store = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STORE
    if not store.is_file():
        print(f"store not found: {store}")
        return 2
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        for table in TABLES:
            exists = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name = ?1",
                (table,),
            ).fetchone()[0]
            if not exists:
                print(f"== {table}: ABSENT")
                continue
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            print(f"== {table}: {len(columns)} columns")
            print("   " + ", ".join(columns))
        # The valuation A/B needs one row per pool: the new market reader
        # takes the **first** row it sees for a pool and issues no ORDER BY,
        # so a pool with several snapshot rows would be valued from an
        # arbitrary one of them.
        for table in (
            "v4_exchange_pool_liquidity_snapshots",
            "v4_exchange_pools_candidate_exchange",
            "v4_exchange_pools_core4",
        ):
            rows, pools = connection.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT pool_id) FROM {table}"
            ).fetchone()
            print(f"== {table}: rows={rows} distinct_pools={pools}")

        # The pools the valuation A/B drops: the snapshot table publishes an
        # **empty** liquidity for them (the pool manager writes the empty
        # string for a pool whose live liquidity is zero), while the base
        # pool tables still carry an older, non-zero liquidity.  The
        # direction layer is the third reader, and the capacity fold uses
        # it, so its liquidity decides whether these pools are usable at
        # all.
        dropped = [
            "0x21e190d0a42ad1b36f957bf2490eef78825d13a579d3a326bf10aadc1d634f4f",
            "0x22f0fef26dfe059f1bc407e4166baf60dccba732349412b2d64bb7e65eb8d8d4",
            "0xaee7f2e61946d4e2a096f44dc686c5b6ba8b2ce0857fa167f2e95c3d2eeb62b8",
            "0xfa29c8b263aee15ed167cbe5e90ea9d89e6eec484f7de64a3465ad6b1ac1a7f6",
        ]
        for pool_id in dropped:
            base_rows = []
            for table in (
                "v4_exchange_pools_candidate_exchange",
                "v4_exchange_pools_core4",
            ):
                row = connection.execute(
                    f"SELECT sqrt_price_x96, tick, liquidity, liquidity_index, depth_status FROM {table} WHERE pool_id = ?1",
                    (pool_id,),
                ).fetchone()
                if row is not None:
                    base_rows.append((table, row))
            directional = connection.execute(
                "SELECT directional_pool_id, direction, sqrt_price_x96, tick, liquidity, depth_capacity_raw, pool_status FROM manager_v4_directional_exchange_pools WHERE base_pool_id = ?1",
                (pool_id,),
            ).fetchall()
            print(f"== dropped pool {pool_id}")
            for table, row in base_rows:
                print(f"   base[{table}] price={row[0]} tick={row[1]} liq={row[2]!r} liqIdx={row[3]} depth={row[4]}")
            if not base_rows:
                print("   base: (absent)")
            for row in directional:
                print(f"   dir {row[0]} dir={row[1]} price={row[2]} tick={row[3]} liq={row[4]!r} depthCap={row[5]} status={row[6]}")
            if not directional:
                print("   dir: (absent)")
            hops = connection.execute(
                "SELECT COUNT(*) FROM manager_v4_closed_loop_hops WHERE pool_id = ?1 OR base_pool_id = ?1",
                (pool_id,),
            ).fetchone()[0]
            print(f"   publishedHopRows: {hops}")
            pairs = connection.execute(
                "SELECT token0_key_id, token1_key_id FROM v4_exchange_pools_candidate_exchange WHERE pool_id = ?1",
                (pool_id,),
            ).fetchone()
            if pairs is not None:
                print(f"   tokens: {pairs[0]} | {pairs[1]}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
