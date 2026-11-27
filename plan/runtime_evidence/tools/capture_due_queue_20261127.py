#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture the due review queue of a closed-loop scope (read only).

The capture is the **baseline** of ``D-224``: the due queue is read with
the **same** statement before and after the refactoring, so the two
answers can be compared key by key.

The tool never writes to the store: it opens the database with
``mode=ro`` and turns ``query_only`` on.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

#: The statement the due queue is read with.  It is the statement of
#: ``read_due_review_loops`` in ``closed_loop_manager/src/lib.rs``.
DUE_SQL = """
SELECT loop_key, runtime_status, next_review_at, {cycle}
  FROM manager_v4_closed_loop_runtime_state
 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
   AND runtime_status IN ('ready_for_review', 'cooldown')
   AND (next_review_at IS NULL
        OR (next_review_at NOT IN (?6, ?7)
            AND (julianday(next_review_at) IS NULL
                 OR julianday(next_review_at) <= julianday(?5))))
 ORDER BY julianday(next_review_at) ASC, loop_key ASC
"""

#: The statement that counts the loops that still wait.
#:
#: A loop waits when it is **not** due: it is parked at a sentinel (it is
#: being measured, or it waits for the receipt of its trade), it is retired,
#: or its clock still runs.  This is the same rule the review queue uses.
WAITING_SQL = """
SELECT COUNT(*)
  FROM manager_v4_closed_loop_runtime_state
 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
   AND runtime_status <> 'retired'
   AND NOT (runtime_status IN ('ready_for_review', 'cooldown')
            AND (next_review_at IS NULL
                 OR (next_review_at NOT IN (?6, ?7)
                     AND (julianday(next_review_at) IS NULL
                          OR julianday(next_review_at) <= julianday(?5)))))
"""

#: The two sentinels: a loop that is being measured, and a loop that waits
#: for the receipt of its trade.
SENTINELS = ("2036-06-06T06:06:00+00:00", "2035-05-05T05:05:00+00:00")

#: The statement that counts the loops that are parked at a sentinel.
PARKED_SQL = """
SELECT COUNT(*)
  FROM manager_v4_closed_loop_runtime_state
 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
   AND next_review_at IN (?5, ?6)
"""

#: The statement that reads every loop of the scope.
ALL_SQL = """
SELECT loop_key, runtime_status, review_required, last_reviewed_at,
       next_review_at, {cycle}
  FROM manager_v4_closed_loop_runtime_state
 WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
 ORDER BY loop_key ASC
"""


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def migration_ledger(con: sqlite3.Connection) -> dict:
    for name in ("schema_migrations", "manager_schema_migrations"):
        found = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?1",
            (name,)).fetchone()
        if found is None:
            continue
        rows = [str(row[0]) for row in con.execute(
            f"SELECT migration_id FROM {name} ORDER BY migration_id")]
        return {"table": name, "count": len(rows), "last": rows[-3:]}
    return {"table": None, "count": 0, "last": []}


def capture(db: Path, network: str, chain_id: int, protocol: str,
            scope_key: str) -> dict:
    """Read the due queue of the scope and describe it."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        columns = table_columns(con, "manager_v4_closed_loop_runtime_state")
        # A store that has not run migration 020 yet has no cycle column, so
        # the statement is built from the columns the store actually has.
        cycle = ("review_cycle_started_at" if "review_cycle_started_at" in columns
                 else "NULL AS review_cycle_started_at")
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        due = [dict(row) for row in con.execute(
            DUE_SQL.format(cycle=cycle),
            (network, chain_id, protocol, scope_key, now, *SENTINELS))]
        waiting = int(con.execute(
            WAITING_SQL, (network, chain_id, protocol, scope_key, now, *SENTINELS)
        ).fetchone()[0])
        parked = int(con.execute(
            PARKED_SQL, (network, chain_id, protocol, scope_key, *SENTINELS)
        ).fetchone()[0])
        every = [dict(row) for row in con.execute(
            ALL_SQL.format(cycle=cycle),
            (network, chain_id, protocol, scope_key))]
        ledger = migration_ledger(con)
    finally:
        con.close()

    keys = [row["loop_key"] for row in due]
    return {
        "capturedAt": now,
        "database": str(db),
        "scope": {"network": network, "chainId": chain_id,
                  "protocol": protocol, "scopeKey": scope_key},
        "ledger": ledger,
        "runtimeStateColumns": sorted(columns),
        "dueCount": len(due),
        "waitingCount": waiting,
        "parkedCount": parked,
        "sentinels": list(SENTINELS),
        "due": due,
        "dueKeys": keys,
        "dueKeysSha256": hashlib.sha256(
            "\n".join(keys).encode("utf-8")).hexdigest(),
        "allCount": len(every),
        "all": every,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--protocol", default="uniswap_v4")
    parser.add_argument("--scope-key", default="core4")
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"the store {args.db} does not exist", file=sys.stderr)
        return 2

    report = capture(args.db, args.network, args.chain_id, args.protocol,
                    args.scope_key)
    report["label"] = args.label

    out = args.out
    if out is None:
        out = (REPO / "plan" / "runtime_evidence" / "diagnostics"
               / "d224_review_queue" / f"due_queue_{args.label}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print(f"label      = {args.label}")
    print(f"ledger     = {report['ledger'].get('table')} "
          f"count={report['ledger'].get('count')} ",
          f"last={report['ledger'].get('last')}")
    print(f"dueCount   = {report['dueCount']}")
    print(f"waiting    = {report['waitingCount']}")
    print(f"parked     = {report['parkedCount']}")
    print(f"allCount   = {report['allCount']}")
    print(f"sha256     = {report['dueKeysSha256']}")
    print(f"written    = {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

