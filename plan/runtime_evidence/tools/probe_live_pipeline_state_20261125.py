"""Read-only snapshot of the live manager library states (chain 4663).

Every query runs against ``mode=ro`` with ``PRAGMA query_only = ON`` so the
live 13.7 GB store is never written.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
NETWORK = "robinhood"
CHAIN_ID = 4663

STATE_TABLES = (
    "manager_v4_directional_pool_library_state",
    "manager_v4_closed_loop_library_state",
    "manager_review_library_state",
    "manager_broadcast_library_state",
    "manager_candidate_edge_library_state",
    "manager_candidate_exchange_library_state",
)


def main() -> None:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only = ON")
    cur = conn.cursor()

    names = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    print(f"db = {DB}")
    for table in STATE_TABLES:
        if table not in names:
            print(f"\n== {table}: TABLE MISSING ==")
            continue
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
        rows = cur.execute(
            f"SELECT * FROM {table} WHERE network = ? AND chain_id = ?",
            (NETWORK, CHAIN_ID),
        ).fetchall()
        print(f"\n== {table} (rows={len(rows)}) ==")
        print(f"   cols = {cols}")
        for row in rows:
            print("   ", dict(zip(cols, row)))

    print("\n== review counts ==")
    try:
        total = cur.execute(
            "SELECT COUNT(*) FROM manager_v4_reviews WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        ).fetchone()[0]
        print(f"   manager_v4_reviews = {total}")
        for row in cur.execute(
            "SELECT status, COUNT(*) FROM manager_v4_reviews "
            "WHERE network=? AND chain_id=? GROUP BY status ORDER BY 2 DESC",
            (NETWORK, CHAIN_ID),
        ):
            print(f"     status={row[0]} n={row[1]}")
    except Exception as exc:  # pragma: no cover
        print("   ERROR", exc)

    print("\n== closed loop / hop freshness ==")
    for table in ("manager_v4_closed_loops", "manager_v4_closed_loop_hops"):
        if table not in names:
            print(f"   {table}: MISSING")
            continue
        try:
            n = cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE network=? AND chain_id=?",
                (NETWORK, CHAIN_ID),
            ).fetchone()[0]
            stamps = cur.execute(
                f"SELECT MIN(updated_at), MAX(updated_at) FROM {table} "
                "WHERE network=? AND chain_id=?",
                (NETWORK, CHAIN_ID),
            ).fetchone()
            print(f"   {table}: rows={n} updated_at={stamps}")
        except Exception as exc:  # pragma: no cover
            print(f"   {table}: ERROR {exc}")

    print("\n== candidate surface freshness (live vs manager) ==")
    for table in (
        "dex_candidate_pools",
        "manager_dex_candidate_pools",
        "market_observation_intermediate_pools",
        "manager_market_observation_intermediate_pools",
        "manager_v4_directional_exchange_pools",
    ):
        if table not in names:
            print(f"   {table}: MISSING")
            continue
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
        stamp = next(
            (c for c in ("updated_at", "materialized_at", "created_at") if c in cols), None
        )
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        span = ""
        if stamp:
            lo, hi = cur.execute(f"SELECT MIN({stamp}), MAX({stamp}) FROM {table}").fetchone()
            span = f" {stamp}={lo} .. {hi}"
        print(f"   {table}: rows={n}{span}")

    conn.close()


if __name__ == "__main__":
    sys.exit(main())
