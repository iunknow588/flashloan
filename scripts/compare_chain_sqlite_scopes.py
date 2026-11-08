#!/usr/bin/env python3
"""Read-only scope comparison between chain-split SQLite databases.

The tool never opens a writable connection and never writes to either input
database.  It reports the 46630 (or chosen chain) row counts for submission
records, candidate YC/ZC tables, market observation, closed loops and pool
liquidity state, and optionally fingerprints the ``cow_execution_attempts``
rows so operators can prove whether a split copied them verbatim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from typing import Any


CHAIN_UNIVERSE_TABLES = (
    "cow_execution_attempts",
    "cow_execution_reviews",
    "dex_candidate_anchor_tokens",
    "dex_candidate_pool_pairs",
    "dex_candidate_pools",
    "market_observation_yz_pairs",
    "market_observation_intermediate_pools",
    "v4_closed_loop_hops",
    "v4_closed_loops",
)

LINK_TABLES = (
    "v4_exchange_pools",
    "v4_exchange_pool_liquidity_snapshots",
)


def _connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def _scope_count(
    con: sqlite3.Connection,
    table: str,
    *,
    network: str,
    chain_id: int,
) -> int:
    columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
    if "network" in columns and "chain_id" in columns:
        row = con.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE network = ? AND chain_id = ?',
            (network, chain_id),
        ).fetchone()
    else:
        row = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    return int((row or (0,))[0])


def _attempt_fingerprint(
    con: sqlite3.Connection,
    *,
    network: str,
    chain_id: int,
) -> str | None:
    columns = [row[1] for row in con.execute("PRAGMA table_info(cow_execution_attempts)")]
    if not columns:
        return None
    rows = con.execute(
        """
        SELECT * FROM cow_execution_attempts
        WHERE network = ? AND chain_id = ?
        ORDER BY id
        """,
        (network, chain_id),
    ).fetchall()
    digests = [
        hashlib.sha256(
            json.dumps(list(row), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        for row in rows
    ]
    return hashlib.sha256("".join(digests).encode("utf-8")).hexdigest()


def _liquidity_state(con: sqlite3.Connection, *, network: str, chain_id: int) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT COALESCE(CAST(has_liquidity AS INTEGER), -1) AS state, COUNT(*) AS count
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ?
        GROUP BY state
        """,
        (network, chain_id),
    ).fetchall()
    states = {"null": 0, "zero": 0, "positive": 0}
    for state, count in rows:
        if state == 0:
            states["zero"] += int(count)
        elif state == 1:
            states["positive"] += int(count)
        else:
            states["null"] += int(count)
    return states


def _scope_report(con: sqlite3.Connection, *, network: str, chain_id: int) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for table in CHAIN_UNIVERSE_TABLES:
        report[table] = _scope_count(con, table, network=network, chain_id=chain_id)
    for table in LINK_TABLES:
        report[table] = _scope_count(con, table, network=network, chain_id=chain_id)
    report["pool_liquidity"] = _liquidity_state(con, network=network, chain_id=chain_id)
    report["cow_execution_attempts_fingerprint"] = _attempt_fingerprint(
        con,
        network=network,
        chain_id=chain_id,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True, help="Read-only source SQLite path.")
    parser.add_argument("--right", required=True, help="Read-only comparison SQLite path.")
    parser.add_argument("--network", default="robinhood-testnet")
    parser.add_argument("--chain-id", type=int, default=46630)
    args = parser.parse_args()

    with _connect(args.left) as left, _connect(args.right) as right:
        report = {
            "network": args.network,
            "chainId": args.chain_id,
            "left": {
                "path": args.left,
                "scope": _scope_report(left, network=args.network, chain_id=args.chain_id),
            },
            "right": {
                "path": args.right,
                "scope": _scope_report(right, network=args.network, chain_id=args.chain_id),
            },
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
