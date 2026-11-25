#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How many closed loops can the bridge price, and from which surfaces?

The bridge in ``flashloan/src/dex/runtime/rust_closed_loop.py`` resolves a
closed-loop hop to a PoolKey and prices the route.  A hop it cannot resolve
is skipped with ``candidate_pool_key_missing``, so the closed loop never
gets priced and the review never completes.

This probe reproduces the bridge's own resolution -- the same rows, the same
PoolKey normalisation, the same per-hop cut and the same group cap -- over
three candidate surfaces:

``live``
    ``dex_candidate_pools`` and ``market_observation_intermediate_pools``.
``manager``
    ``manager_dex_candidate_pools`` and
    ``manager_market_observation_intermediate_pools``.
``live+manager``
    the union of both.

The store is read in read-only mode and never written.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_DB = Path("E:/2026OPC大赛/flashLoan/flashloan/db/local_store_4663.sqlite3")
NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
PER_HOP_CUT = 8
GROUP_CAP = 64


LIVE_QUERIES = (
    f"""
    SELECT token0, token1, fee, tick_spacing, hooks, pool_key_json,
           usable_for_observation, pool_id
    FROM dex_candidate_pools
    WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID} AND protocol = '{PROTOCOL}'
      AND usable_for_observation = 1
    ORDER BY pool_id
    """,
    f"""
    SELECT m.token0, m.token1, m.fee, m.tick_spacing, m.hooks,
           CASE WHEN TRIM(COALESCE(m.pool_key_json, '')) IN ('', '{{}}', 'null')
                THEN p.pool_key_json ELSE m.pool_key_json END AS pool_key_json,
           1 AS usable_for_observation, m.pool_id
    FROM market_observation_intermediate_pools m
    LEFT JOIN v4_exchange_pools p
      ON p.network = m.network AND p.chain_id = m.chain_id
     AND p.protocol = m.protocol AND p.pool_id = m.pool_id
    WHERE m.network = '{NETWORK}' AND m.chain_id = {CHAIN_ID} AND m.protocol = '{PROTOCOL}'
    ORDER BY m.pool_id
    """,
)

MANAGER_QUERIES = (
    f"""
    SELECT token0, token1, fee, tick_spacing, hooks, pool_key_json,
           1 AS usable_for_observation, pool_id
    FROM manager_dex_candidate_pools
    WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID} AND protocol = '{PROTOCOL}'
    """,
    f"""
    SELECT m.token0, m.token1, m.fee, m.tick_spacing, m.hooks,
           CASE WHEN TRIM(COALESCE(m.pool_key_json, '')) IN ('', '{{}}', 'null')
                THEN p.pool_key_json ELSE m.pool_key_json END AS pool_key_json,
           1 AS usable_for_observation, m.pool_id
    FROM manager_market_observation_intermediate_pools m
    LEFT JOIN v4_exchange_pools p
      ON p.network = m.network AND p.chain_id = m.chain_id
     AND p.protocol = m.protocol AND p.pool_id = m.pool_id
    WHERE m.network = '{NETWORK}' AND m.chain_id = {CHAIN_ID} AND m.protocol = '{PROTOCOL}'
    """,
)

LOOPS_QUERY = f"""
SELECT path_addresses_json, pool_ids_json
FROM manager_v4_closed_loops
WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID} AND protocol = '{PROTOCOL}'
"""


def _normalise(row: tuple) -> tuple[tuple[str, str, int, int, str], str] | None:
    token0, token1, fee, tick_spacing, hooks, pool_key_json, usable, pool_id = row
    if not usable:
        return None
    try:
        key = json.loads(pool_key_json) if pool_key_json else None
    except (TypeError, ValueError):
        return None
    if not isinstance(key, dict):
        return None
    currency0 = str(key.get("currency0") or token0 or "").strip()
    currency1 = str(key.get("currency1") or token1 or "").strip()
    try:
        fee = int(key.get("fee", fee))
        tick_spacing = int(key.get("tickSpacing", tick_spacing))
    except (TypeError, ValueError):
        return None
    hooks = str(key.get("hooks") or hooks or "0x" + "0" * 40).strip()
    if not currency0 or not currency1 or fee < 0 or tick_spacing < 0:
        return None
    return (currency0.lower(), currency1.lower(), fee, tick_spacing, hooks.lower()), str(pool_id)


def _build(connection: sqlite3.Connection, queries: tuple[str, ...]) -> tuple[dict, dict]:
    by_edge: dict[tuple[str, str], list[tuple]] = {}
    pool_key: dict[str, tuple] = {}
    for sql in queries:
        for row in connection.execute(sql):
            normalised = _normalise(row)
            if normalised is None:
                continue
            identity, pool_id = normalised
            token0 = str(row[0] or "").strip().lower()
            token1 = str(row[1] or "").strip().lower()
            if not token0 or not token1:
                continue
            edge = (token0, token1) if token0 <= token1 else (token1, token0)
            candidates = by_edge.setdefault(edge, [])
            if identity not in candidates:
                candidates.append(identity)
            pool_key.setdefault(pool_id.lower(), identity)
    return by_edge, pool_key


def _resolve(connection: sqlite3.Connection, queries: tuple[str, ...]) -> dict:
    by_edge, pool_key = _build(connection, queries)
    resolved = 0
    unresolved_edge = 0
    published_pool_not_in_group = 0
    published_pool_unresolvable = 0
    for path_addresses_json, pool_ids_json in connection.execute(LOOPS_QUERY):
        addresses = json.loads(path_addresses_json)
        published = [str(item).lower() for item in json.loads(pool_ids_json)]
        per_hop: list[list[tuple]] = []
        missing = False
        for index in range(3):
            left = addresses[index].lower()
            right = addresses[index + 1].lower()
            edge = (left, right) if left <= right else (right, left)
            candidates = list(by_edge.get(edge, []))
            if not candidates:
                missing = True
                break
            per_hop.append(candidates)
        if missing:
            unresolved_edge += 1
            continue
        groups: list[list[tuple]] = [[]]
        for candidates in per_hop:
            groups = [group + [key] for group in groups for key in candidates[:PER_HOP_CUT]]
            if len(groups) > GROUP_CAP:
                groups = groups[:GROUP_CAP]
        wanted = [pool_key.get(pool_id) for pool_id in published]
        if any(item is None for item in wanted):
            published_pool_unresolvable += 1
            continue
        if any(
            not any(group[index] == wanted[index] for group in groups)
            for index in range(3)
        ):
            published_pool_not_in_group += 1
            continue
        resolved += 1
    return {
        "resolved": resolved,
        "unresolvedEdge": unresolved_edge,
        "publishedPoolNotInGroup": published_pool_not_in_group,
        "publishedPoolUnresolvable": published_pool_unresolvable,
    }


def probe(database_path: Path) -> dict:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        loops = connection.execute(f"SELECT COUNT(*) FROM manager_v4_closed_loops WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID} AND protocol = '{PROTOCOL}'").fetchone()[0]
        return {
            "database": str(database_path),
            "mode": "ro",
            "closedLoops": loops,
            "perHopCut": PER_HOP_CUT,
            "groupCap": GROUP_CAP,
            "live": _resolve(connection, LIVE_QUERIES),
            "manager": _resolve(connection, MANAGER_QUERIES),
            "live+manager": _resolve(connection, LIVE_QUERIES + MANAGER_QUERIES),
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    report = probe(Path(arguments.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        Path(arguments.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
