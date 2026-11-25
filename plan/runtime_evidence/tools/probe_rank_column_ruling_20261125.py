#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the "rank" column of the candidate-edge recommendation table.

The stage two of "T-方储.01" planned to *drop* "rank" from
"manager_dex_candidate_pool_recommendations".  That plan was written when
the column still carried the *legacy* order ("liquidity_index" proxy).
After the stage two first segment the candidate-edge layer publishes the
direction layer's order, so the column now carries a *derived* ordinal.

This probe measures, read-only, whether the column can still be dropped:

* is "rank" still what the closed-loop layer sorts by;
* is "rank" dense and unique inside a group (the primary key needs it);
* can "depth_score" replace it (same order, same uniqueness, same nullability);
* is the live table already written by the new producer.

The store is opened read-only ("mode=ro" plus "PRAGMA query_only = ON"),
so the probe never writes to the live chain store.

Usage:
    python probe_rank_column_ruling_20261125.py [store] [report.json]
"""

import json
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any, Dict, List

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
DEFAULT_STORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "flashloan",
    "db",
    "local_store_4663.sqlite3",
)
RECOMMENDATION_TABLE = "manager_dex_candidate_pool_recommendations"
DIRECTIONAL_TABLE = "manager_v4_directional_exchange_pools"
NEW_SELECTION_BASIS = "directional_pair_direction_rank"


def open_readonly(path: str) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % path.replace("\\", "/").replace("?", "%3f")
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?1",
        (name,),
    ).fetchone()
    return row is not None


def _rows_by(connection: sqlite3.Connection, sql: str) -> Dict[str, Any]:
    return {str(row[0]): row[1] for row in connection.execute(sql)}


def _primary_key_columns(connection: sqlite3.Connection) -> List[str]:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?1",
        (RECOMMENDATION_TABLE,),
    ).fetchone()
    if row is None or not row[0]:
        return []
    text = row[0]
    start = text.upper().find("PRIMARY KEY")
    if start < 0:
        return []
    body = text[start + len("PRIMARY KEY"):]
    end = body.find(")")
    if end < 0:
        return []
    keys = [
        part.strip()
        for part in body[:end].replace("(", " ").split(",")
        if part.strip()
    ]
    return keys


def _groups(connection: sqlite3.Connection) -> Dict[Any, List[Any]]:
    groups: Dict[Any, List[Any]] = defaultdict(list)
    rows = connection.execute(
        "SELECT anchor_token_key_id, candidate_token_key_id, hop, rank, depth_score"
        " FROM %s WHERE network=?1 AND chain_id=?2 AND protocol=?3"
        " ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank"
        % RECOMMENDATION_TABLE,
        (NETWORK, CHAIN_ID, PROTOCOL),
    )
    for anchor, candidate, hop, rank, depth in rows:
        groups[(anchor, candidate, hop)].append((rank, depth))
    return groups


def _rank_shape(groups: Dict[Any, List[Any]]) -> Dict[str, Any]:
    dense = 0
    gaps = 0
    ties = 0
    starts_at_one = 0
    for members in groups.values():
        ranks = [rank for rank, _ in members]
        if len(set(ranks)) != len(ranks):
            ties += 1
        if ranks and ranks[0] == 1:
            starts_at_one += 1
        if ranks == list(range(1, len(ranks) + 1)):
            dense += 1
        else:
            gaps += 1
    return {
        "groups": len(groups),
        "dense_one_to_n": dense,
        "groups_with_gaps": gaps,
        "groups_with_ties": ties,
        "groups_starting_at_one": starts_at_one,
    }


def _depth_score_shape(groups: Dict[Any, List[Any]]) -> Dict[str, Any]:
    equal_to_rank = 0
    differs = 0
    ties = 0
    null_groups = 0
    for members in groups.values():
        depths = [depth for _, depth in members]
        if any(depth is None for depth in depths):
            null_groups += 1
            continue
        if len(set(depths)) != len(depths):
            ties += 1
        for rank, depth in members:
            if depth is None:
                continue
            if float(depth) == float(rank):
                equal_to_rank += 1
            else:
                differs += 1
    return {
        "groups_with_null_depth": null_groups,
        "groups_with_depth_ties": ties,
        "rows_depth_equals_rank": equal_to_rank,
        "rows_depth_differs_from_rank": differs,
    }


def _order_equivalence(groups: Dict[Any, List[Any]]) -> Dict[str, Any]:
    same_order = 0
    different_order = 0
    incomparable = 0
    for members in groups.values():
        depths = [depth for _, depth in members]
        if any(depth is None for depth in depths):
            incomparable += 1
            continue
        by_rank = sorted(members, key=lambda item: item[0])
        by_depth = sorted(members, key=lambda item: item[1])
        if by_rank == by_depth:
            same_order += 1
        else:
            different_order += 1
    return {
        "same_order": same_order,
        "different_order": different_order,
        "incomparable_null_depth": incomparable,
    }


def probe(connection: sqlite3.Connection) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    if not table_exists(connection, RECOMMENDATION_TABLE):
        report["error"] = "%s is missing" % RECOMMENDATION_TABLE
        return report

    report["columns"] = [
        row[1]
        for row in connection.execute("PRAGMA table_info(%s)" % RECOMMENDATION_TABLE)
    ]
    report["primary_key"] = _primary_key_columns(connection)
    report["rank_in_primary_key"] = "rank" in report["primary_key"]
    report["total_rows"] = connection.execute(
        "SELECT COUNT(*) FROM %s" % RECOMMENDATION_TABLE
    ).fetchone()[0]
    report["by_scope"] = _rows_by(
        connection,
        "SELECT scope_key, COUNT(*) FROM %s GROUP BY scope_key ORDER BY 2 DESC"
        % RECOMMENDATION_TABLE,
    )
    report["selection_basis"] = _rows_by(
        connection,
        "SELECT selection_basis, COUNT(*) FROM %s GROUP BY selection_basis"
        " ORDER BY 2 DESC" % RECOMMENDATION_TABLE,
    )
    report["last_write"] = _rows_by(
        connection,
        "SELECT MAX(updated_at), MIN(created_at) FROM %s" % RECOMMENDATION_TABLE,
    )
    report["live_table_written_by_new_producer"] = (
        NEW_SELECTION_BASIS in report["selection_basis"]
    )

    groups = _groups(connection)
    report["group_count"] = len(groups)
    report["rank_shape"] = _rank_shape(groups)
    report["depth_score_shape"] = _depth_score_shape(groups)
    report["order_equivalence"] = _order_equivalence(groups)
    report["zero_rank_rows"] = connection.execute(
        "SELECT COUNT(*) FROM %s WHERE rank = 0" % RECOMMENDATION_TABLE
    ).fetchone()[0]
    report["null_depth_score_rows"] = connection.execute(
        "SELECT COUNT(*) FROM %s WHERE depth_score IS NULL" % RECOMMENDATION_TABLE
    ).fetchone()[0]

    if table_exists(connection, DIRECTIONAL_TABLE):
        report["directional_rows"] = connection.execute(
            "SELECT COUNT(*) FROM %s" % DIRECTIONAL_TABLE
        ).fetchone()[0]
        report["directional_rank_duplicate_groups"] = connection.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT pair_key_id, direction, scope_key, COUNT(*) AS n"
            "  FROM %s WHERE pair_direction_rank IS NOT NULL"
            "  GROUP BY pair_key_id, direction, scope_key"
            "  HAVING n <> COUNT(DISTINCT pair_direction_rank))" % DIRECTIONAL_TABLE,
        ).fetchone()[0]
    else:
        report["directional_rows"] = None

    return report


def main() -> int:
    store = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STORE
    output = sys.argv[2] if len(sys.argv) > 2 else None
    connection = open_readonly(store)
    try:
        report = probe(connection)
    finally:
        connection.close()
    payload = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
