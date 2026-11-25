"""Reproduce, read-only, exactly what ``_candidate_pool_key_groups_from_database`` sees.

The bridge (flashloan/src/dex/runtime/rust_closed_loop.py) resolves candidate
pool keys from two legacy surfaces and **drops** a row whose ``pool_key_json``
carries no ``tickSpacing`` while the row's own ``tick_spacing`` column is
NULL.  This probe replays that normalisation so the visible edge coverage can
be compared against the closed-loop hop edges.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"


def normalise(row: dict) -> dict | None:
    """Mirror of the bridge's ``_normalize_key``."""
    raw = row.get("pool_key_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = None
    if not isinstance(raw, dict):
        raw = {}
    currency0 = str(raw.get("currency0") or row.get("token0") or "").strip()
    currency1 = str(raw.get("currency1") or row.get("token1") or "").strip()
    try:
        fee = int(raw.get("fee", row.get("fee") if raw.get("fee") is None else raw.get("fee")))
        tick_spacing = int(raw.get("tickSpacing", row.get("tick_spacing")))
    except (TypeError, ValueError):
        return None
    hooks = str(
        raw.get("hooks") or row.get("hooks") or "0x" + "0" * 40
    ).strip()
    if not currency0 or not currency1 or fee < 0 or tick_spacing < 0:
        return None
    return {
        "currency0": currency0,
        "currency1": currency1,
        "fee": fee,
        "tickSpacing": tick_spacing,
        "hooks": hooks,
    }


def identity(key: dict) -> tuple | None:
    c0 = str(key.get("currency0") or "").strip().lower()
    c1 = str(key.get("currency1") or "").strip().lower()
    try:
        fee = int(key.get("fee"))
        spacing = int(key.get("tickSpacing"))
    except (TypeError, ValueError):
        return None
    hooks = str(key.get("hooks") or "0x" + "0" * 40).strip().lower()
    if not c0 or not c1:
        return None
    return (c0, c1, fee, spacing, hooks)


def main() -> None:
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only = ON")
    cur = conn.cursor()

    candidate_rows = cur.execute(
        "SELECT token0, token1, fee, tick_spacing, hooks, pool_key_json "
        "FROM dex_candidate_pools "
        "WHERE network = ? AND chain_id = ? AND protocol = ? "
        "AND usable_for_observation = 1 ORDER BY pool_id",
        (NETWORK, CHAIN_ID, PROTOCOL),
    ).fetchall()
    observation_rows = cur.execute(
        "SELECT m.token0, m.token1, m.fee, m.tick_spacing, m.hooks, "
        "CASE WHEN TRIM(COALESCE(m.pool_key_json, '')) IN ('', '{}', 'null') "
        "     THEN p.pool_key_json ELSE m.pool_key_json END AS pool_key_json "
        "FROM market_observation_intermediate_pools m "
        "LEFT JOIN v4_exchange_pools p ON p.network = m.network AND p.chain_id = m.chain_id "
        "  AND p.protocol = m.protocol AND p.pool_id = m.pool_id "
        "WHERE m.network = ? AND m.chain_id = ? AND m.protocol = ? ORDER BY m.pool_id",
        (NETWORK, CHAIN_ID, PROTOCOL),
    ).fetchall()

    keys_by_edge: dict[tuple[str, str], list[dict]] = {}
    dropped = 0
    for row in [*candidate_rows, *observation_rows]:
        raw = {
            "token0": row[0],
            "token1": row[1],
            "fee": row[2],
            "tick_spacing": row[3],
            "hooks": row[4],
            "pool_key_json": row[5],
        }
        t0 = str(raw["token0"] or "").strip().lower()
        t1 = str(raw["token1"] or "").strip().lower()
        if not t0 or not t1:
            continue
        key = normalise(raw)
        if key is None:
            dropped += 1
            continue
        ident = identity(key)
        if ident is None:
            continue
        edge = (t0, t1) if t0 <= t1 else (t1, t0)
        bucket = keys_by_edge.setdefault(edge, [])
        if any(identity(existing) == ident for existing in bucket):
            continue
        bucket.append(key)

    visible = set(keys_by_edge)
    print(f"candidate rows read = {len(candidate_rows)}")
    print(f"observation rows read = {len(observation_rows)}")
    print(f"rows dropped by normalisation = {dropped}")
    print(f"edges visible to the bridge = {len(visible)}")

    hop_edges = {
        tuple(sorted((str(a).lower(), str(b).lower())))
        for a, b in cur.execute(
            "SELECT DISTINCT input_address, output_address FROM manager_v4_closed_loop_hops"
        )
    }
    missing = sorted(hop_edges - visible)
    print(f"\nclosed-loop distinct hop edges = {len(hop_edges)}")
    print(f"hop edges NOT resolvable by the bridge = {len(missing)}")
    for edge in missing:
        print(f"   {edge[0]} <-> {edge[1]}")

    extra = sorted(visible - hop_edges)
    print(f"\nbridge-visible edges the closed loop does not use = {len(extra)}")
    for edge in extra:
        print(f"   {edge[0]} <-> {edge[1]}  (n={len(keys_by_edge[edge])})")
    conn.close()


if __name__ == "__main__":
    main()
