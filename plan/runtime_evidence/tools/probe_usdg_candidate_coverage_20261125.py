"""Read-only probe: USDG edge coverage across the candidate / observation surfaces.

Live-store discipline: the database is opened ``mode=ro`` with
``PRAGMA query_only = ON`` so this probe can never write.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"

USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"


def ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only = ON")
    return conn


def main() -> None:
    conn = ro()
    cur = conn.cursor()
    print(f"db = {DB}")
    print(f"usdg = {USDG}")

    print("\n== schema: dex_candidate_pools ==")
    for row in cur.execute("PRAGMA table_info(dex_candidate_pools)"):
        print("  ", row[1], row[2])
    print("\n== schema: market_observation_intermediate_pools ==")
    for row in cur.execute("PRAGMA table_info(market_observation_intermediate_pools)"):
        print("  ", row[1], row[2])
    print("\n== schema: v4_exchange_pools ==")
    for row in cur.execute("PRAGMA table_info(v4_exchange_pools)"):
        print("  ", row[1], row[2])

    def count(sql: str, args: tuple = ()) -> int:
        return int(cur.execute(sql, args).fetchone()[0])

    print("\n== row counts ==")
    for table in (
        "dex_candidate_pools",
        "market_observation_intermediate_pools",
        "v4_exchange_pools",
    ):
        try:
            total = count(f"SELECT COUNT(*) FROM {table}")
        except Exception as exc:  # pragma: no cover - diagnostics only
            print(f"  {table}: ERROR {exc}")
            continue
        usdg_rows = count(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE LOWER(token0) = ? OR LOWER(token1) = ?",
            (USDG, USDG),
        )
        print(f"  {table}: total={total} usdg_rows={usdg_rows}")

    print("\n== dex_candidate_pools: usdg rows (detail) ==")
    try:
        cols = [r[1] for r in cur.execute("PRAGMA table_info(dex_candidate_pools)")]
        wanted = [c for c in ("pool_id", "token0", "token1", "network", "chain_id",
                              "protocol", "usable_for_observation", "fee",
                              "tick_spacing", "hooks", "anchor_scope") if c in cols]
        sql = (
            f"SELECT {', '.join(wanted)} FROM dex_candidate_pools "
            "WHERE LOWER(token0) = ? OR LOWER(token1) = ? ORDER BY pool_id"
        )
        rows = cur.execute(sql, (USDG, USDG)).fetchall()
        print(f"  rows={len(rows)} cols={wanted}")
        for row in rows[:60]:
            print("  ", row)
    except Exception as exc:  # pragma: no cover
        print("  ERROR", exc)

    print("\n== market_observation_intermediate_pools: usdg rows ==")
    try:
        cols = [r[1] for r in cur.execute("PRAGMA table_info(market_observation_intermediate_pools)")]
        wanted = [c for c in ("pool_id", "token0", "token1", "network", "chain_id",
                              "protocol", "fee", "tick_spacing", "hooks",
                              "pool_key_json") if c in cols]
        sql = (
            f"SELECT {', '.join(wanted)} FROM market_observation_intermediate_pools "
            "WHERE LOWER(token0) = ? OR LOWER(token1) = ? ORDER BY pool_id"
        )
        rows = cur.execute(sql, (USDG, USDG)).fetchall()
        print(f"  rows={len(rows)} cols={wanted}")
        for row in rows[:60]:
            print("  ", row)
    except Exception as exc:  # pragma: no cover
        print("  ERROR", exc)

    print("\n== v4_exchange_pools: usdg rows ==")
    try:
        cols = [r[1] for r in cur.execute("PRAGMA table_info(v4_exchange_pools)")]
        wanted = [c for c in ("pool_id", "token0", "token1", "network", "chain_id",
                              "protocol", "fee", "tick_spacing", "hooks",
                              "pool_key_json") if c in cols]
        sql = (
            f"SELECT {', '.join(wanted)} FROM v4_exchange_pools "
            "WHERE LOWER(token0) = ? OR LOWER(token1) = ? ORDER BY pool_id"
        )
        rows = cur.execute(sql, (USDG, USDG)).fetchall()
        print(f"  rows={len(rows)} cols={wanted}")
        for row in rows[:60]:
            print("  ", row)
    except Exception as exc:  # pragma: no cover
        print("  ERROR", exc)

    print("\n== distinct token pairs per surface (chain 4663) ==")
    for table in ("dex_candidate_pools", "market_observation_intermediate_pools"):
        try:
            n = count(
                f"SELECT COUNT(*) FROM (SELECT DISTINCT token0, token1 FROM {table} "
                "WHERE chain_id = 4663)"
            )
            print(f"  {table}: distinct_pairs={n}")
        except Exception as exc:  # pragma: no cover
            print(f"  {table}: ERROR {exc}")

    print("\n== USDG neighbours per surface (distinct) ==")
    for table in (
        "dex_candidate_pools",
        "manager_dex_candidate_pools",
        "market_observation_intermediate_pools",
        "manager_market_observation_intermediate_pools",
        "v4_exchange_pools",
        "manager_v4_exchange_pools",
    ):
        try:
            rows = cur.execute(
                f"SELECT DISTINCT CASE WHEN LOWER(token0) = ? THEN LOWER(token1) "
                f"ELSE LOWER(token0) END AS other FROM {table} "
                "WHERE LOWER(token0) = ? OR LOWER(token1) = ? ORDER BY other",
                (USDG, USDG, USDG),
            ).fetchall()
            others = [r[0] for r in rows]
            print(f"  {table}: neighbours={len(others)}")
            for other in others:
                print(f"      {other}")
        except Exception as exc:  # pragma: no cover
            print(f"  {table}: ERROR {exc}")

    print("\n== manager_* table row counts ==")
    for table in (
        "manager_dex_candidate_pools",
        "manager_market_observation_intermediate_pools",
        "manager_v4_exchange_pools",
        "manager_v4_directional_exchange_pools",
        "manager_v4_directional_pools",
    ):
        try:
            print(f"  {table}: rows={count(f'SELECT COUNT(*) FROM {table}')}")
        except Exception as exc:  # pragma: no cover
            print(f"  {table}: ERROR {exc}")

    print("\n== candidate rows whose pool_key_json / tick_spacing cannot normalise ==")
    rows = cur.execute(
        "SELECT pool_id, token0, token1, fee, tick_spacing, "
        "       CASE WHEN TRIM(COALESCE(pool_key_json,'')) IN ('','{}','null') "
        "            THEN 1 ELSE 0 END AS key_blank, "
        "       anchor_scope, usable_for_observation, length(COALESCE(pool_key_json,'')) AS key_len "
        "FROM dex_candidate_pools "
        "WHERE chain_id = 4663 AND usable_for_observation = 1 "
        "ORDER BY token1, pool_id"
    ).fetchall()
    blank = [r for r in rows if r[6] == 1]
    null_ts = [r for r in rows if r[4] is None]
    print(f"  usable rows={len(rows)} blank_pool_key={len(blank)} null_tick_spacing={len(null_ts)}")
    for row in blank[:20]:
        print("   blank:", row)
    for row in null_ts[:20]:
        print("   nullts:", row)

    print("\n== freshness + anchor_scope per surface ==")
    for table in (
        "dex_candidate_pools",
        "manager_dex_candidate_pools",
        "market_observation_intermediate_pools",
        "manager_market_observation_intermediate_pools",
    ):
        try:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
            stamp = "updated_at" if "updated_at" in cols else (
                "created_at" if "created_at" in cols else None)
            total = count(f"SELECT COUNT(*) FROM {table}")
            newest = ""
            oldest = ""
            if stamp:
                newest = str(
                    cur.execute(f"SELECT MAX({stamp}) FROM {table}").fetchone()[0] or ""
                )
                oldest = str(
                    cur.execute(f"SELECT MIN({stamp}) FROM {table}").fetchone()[0] or ""
                )
            scopes = []
            if "anchor_scope" in cols:
                scopes = [
                    (str(r[0]), int(r[1]))
                    for r in cur.execute(
                        f"SELECT anchor_scope, COUNT(*) FROM {table} "
                        "GROUP BY anchor_scope ORDER BY 2 DESC"
                    )
                ]
            print(f"  {table}: rows={total} {stamp}={oldest} .. {newest}")
            for scope, n in scopes:
                print(f"      scope={scope} rows={n}")
        except Exception as exc:  # pragma: no cover
            print(f"  {table}: ERROR {exc}")

    print("\n== manager_dex_candidate_pools: the three missing USDG edges ==")
    missing = (
        "0x0000000000000000000000000000000000000000",
        "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
        "0x117cc2133c37b721f49de2a7a74833232b3b4c0c",
    )
    for other in missing:
        for table in ("dex_candidate_pools", "manager_dex_candidate_pools",
                      "market_observation_intermediate_pools",
                      "manager_market_observation_intermediate_pools"):
            try:
                n = count(
                    f"SELECT COUNT(*) FROM {table} WHERE "
                    "(LOWER(token0)=? AND LOWER(token1)=?) OR "
                    "(LOWER(token0)=? AND LOWER(token1)=?)",
                    (USDG, other, other, USDG),
                )
            except Exception as exc:  # pragma: no cover
                n = f"ERROR {exc}"
            print(f"  edge USDG/{other[:10]}.. in {table}: {n}")

    print("\n== closed-loop hop edges vs live candidate surface ==")
    try:
        hop_cols = [r[1] for r in cur.execute("PRAGMA table_info(manager_v4_closed_loop_hops)")]
        print(f"  hop cols: {hop_cols}")
        hops = cur.execute(
            "SELECT DISTINCT LOWER(input_address), LOWER(output_address), scope_key "
            "FROM manager_v4_closed_loop_hops"
        ).fetchall()
        cand = {
            (str(a).lower(), str(b).lower())
            for a, b in cur.execute(
                "SELECT token0, token1 FROM dex_candidate_pools "
                "WHERE chain_id = 4663 AND usable_for_observation = 1"
            )
        }
        obs = {
            (str(a).lower(), str(b).lower())
            for a, b in cur.execute(
                "SELECT token0, token1 FROM market_observation_intermediate_pools "
                "WHERE chain_id = 4663"
            )
        }
        mgmt = {
            (str(a).lower(), str(b).lower())
            for a, b in cur.execute(
                "SELECT token0, token1 FROM manager_dex_candidate_pools"
            )
        }
        mgmt_obs = {
            (str(a).lower(), str(b).lower())
            for a, b in cur.execute(
                "SELECT token0, token1 FROM manager_market_observation_intermediate_pools"
            )
        }

        def norm(edge: tuple[str, str]) -> tuple[str, str]:
            return tuple(sorted(edge))  # type: ignore[return-value]

        hop_edges = {norm((a, b)) for a, b, _ in hops}
        live = {norm(e) for e in cand | obs}
        stale = {norm(e) for e in mgmt | mgmt_obs}
        uncovered = sorted(hop_edges - live)
        print(f"  distinct hop edges={len(hop_edges)}")
        print(f"  live candidate+observation edges={len(live)}")
        print(f"  stale manager edges={len(stale)}")
        print(f"  hop edges NOT covered by live surface={len(uncovered)}")
        for edge in uncovered:
            print(f"      {edge[0]} <-> {edge[1]}   in_stale={edge in stale}")

        print("\n== per-edge pool inventory for the 7 uncovered edges ==")
        for edge in uncovered:
            left, right = edge
            print(f"  edge {left[:12]} <-> {right[:12]}")
            for table in (
                "dex_candidate_pools",
                "manager_dex_candidate_pools",
                "market_observation_intermediate_pools",
                "manager_market_observation_intermediate_pools",
            ):
                try:
                    rows = cur.execute(
                        f"SELECT pool_id, usable_for_observation, anchor_scope, fee, "
                        f"tick_spacing, length(COALESCE(pool_key_json,'')) "
                        f"FROM {table} WHERE "
                        "(LOWER(token0)=? AND LOWER(token1)=?) OR "
                        "(LOWER(token0)=? AND LOWER(token1)=?) LIMIT 4",
                        (left, right, right, left),
                    ).fetchall()
                except Exception as exc:  # pragma: no cover
                    rows = [f"ERROR {exc}"]
                print(f"      {table}: {rows}")
            try:
                rows = cur.execute(
                    "SELECT base_pool_id, direction, scope_key, pool_status, "
                    "pool_status_reason, depth_status, pair_direction_rank "
                    "FROM manager_v4_directional_exchange_pools WHERE "
                    "(LOWER(token0)=? AND LOWER(token1)=?) OR "
                    "(LOWER(token0)=? AND LOWER(token1)=?) LIMIT 4",
                    (left, right, right, left),
                ).fetchall()
            except Exception as exc:  # pragma: no cover
                rows = [f"ERROR {exc}"]
            print(f"      directional: {rows}")
    except Exception as exc:  # pragma: no cover
        print("  ERROR", exc)

    print("\n== directional table freshness ==")
    try:
        print("  rows=", count("SELECT COUNT(*) FROM manager_v4_directional_exchange_pools"))
        print("  updated_at=", cur.execute(
            "SELECT MIN(updated_at), MAX(updated_at) FROM manager_v4_directional_exchange_pools"
        ).fetchone())
        print("  by scope:")
        for row in cur.execute(
            "SELECT scope_key, COUNT(*), MAX(updated_at) "
            "FROM manager_v4_directional_exchange_pools GROUP BY scope_key"
        ):
            print("   ", row)
    except Exception as exc:  # pragma: no cover
        print("  ERROR", exc)

    conn.close()


if __name__ == "__main__":
    main()
