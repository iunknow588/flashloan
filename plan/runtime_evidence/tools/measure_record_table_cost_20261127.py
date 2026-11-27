#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where the record layer's time goes, measured on the production store.

The review record table is append only and grows by a round every minute.
It is *record* data: the runtime never decides on it, it only writes it.
The question this script answers is therefore not "how big is the table"
but "which read of it costs the runtime minutes, and does that read serve
a decision at all".

The script reports three things:

* the size of every table that matters, taken from its own row count and
  the bytes it stores;
* the cost of the **decision** reads -- the Manager library and the scope
  index -- which the healer uses to drive the cascade;
* the cost of the **record** reads -- the full scans that pull the whole
  payload text of the history -- which no decision may depend on.

Everything is read only.  The store is 18 GB and it is the live store of
the mainnet chain, so it is opened with ``mode=ro`` and ``query_only``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


DB = Path("flashloan/db/local_store_4663.sqlite3").resolve()
NETWORK = "robinhood"
CHAIN_ID = 4663


def open_ro() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=300)
    connection.execute("PRAGMA query_only=ON")
    return connection


def timed(label: str, statement: str, params: tuple = ()) -> dict:
    """Run one statement and time it, with the row count it produced."""
    connection = open_ro()
    try:
        started = time.monotonic()
        rows = connection.execute(statement, params).fetchall()
        elapsed = time.monotonic() - started
        return {"label": label, "seconds": round(elapsed, 3), "rows": len(rows)}
    finally:
        connection.close()


def table_sizes() -> list[dict]:
    """The stored size of the tables the record layer writes.

    ``dbstat`` is not compiled into every SQLite build, so the size is
    measured from the rows themselves: the count and the bytes of the
    payload text.  For the record table that is the truth that matters --
    the payload text is what every scan has to read past.
    """
    connection = open_ro()
    try:
        rows = []
        for table, payload_column in (("cow_execution_reviews", "payload_json"),
                                      ("manager_v4_reviews", "payload_json"),
                                      ("cow_execution_attempts", "payload_json")):
            try:
                count, payload_bytes = connection.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(LENGTH({payload_column})),0) "
                    f"FROM {table}"
                ).fetchone()
            except sqlite3.Error as error:
                rows.append({"table": table, "error": str(error)})
                continue
            rows.append({"table": table, "rows": count,
                         "payloadGiB": round(payload_bytes / (1024 ** 3), 3),
                         "averagePayloadBytes": int(payload_bytes / count) if count else 0})
        return rows
    finally:
        connection.close()


def row_counts() -> list[dict]:
    """The row count of the tables the record layer writes."""
    connection = open_ro()
    try:
        rows = []
        for table in ("cow_execution_reviews", "manager_v4_reviews",
                      "manager_v4_review_broadcast_requests", "manager_cow_execution_attempts"):
            try:
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error as error:
                count = f"unavailable: {error}"
            rows.append({"table": table, "rows": count})
        return rows
    finally:
        connection.close()


def main() -> int:
    report: dict = {"database": str(DB), "sizes": [], "rowCounts": [], "reads": []}
    report["sizes"] = table_sizes()
    report["rowCounts"] = row_counts()

    # The reads the cascade *decides* on.  Every one of them has to be
    # cheap, because the healer runs them once per cycle.
    report["reads"].append(timed(
        "decision.manager_library",
        "SELECT COUNT(*) FROM manager_v4_reviews"))
    report["reads"].append(timed(
        "decision.scope_index_window",
        "SELECT id,created_at,pair,category FROM cow_execution_reviews "
        "WHERE network=? AND chain_id=? ORDER BY created_at DESC LIMIT 400",
        (NETWORK, CHAIN_ID)))
    report["reads"].append(timed(
        "decision.scope_index_new_rows",
        "SELECT id,created_at,pair,category FROM cow_execution_reviews "
        "WHERE network=? AND chain_id=? AND created_at > ? "
        "ORDER BY created_at DESC LIMIT 400",
        (NETWORK, CHAIN_ID, "2026-11-26 00:00:00")))

    # The reads that pull the *history*.  These are the ones that cost
    # minutes, and they are the ones the cascade must not depend on.
    report["reads"].append(timed(
        "record.full_scan_with_payload",
        "SELECT id,created_at,category,selected_profit_amount,payload_json "
        "FROM cow_execution_reviews WHERE network=? AND chain_id=? "
        "AND datetime(created_at)>=datetime('now','-24 hours') "
        "AND selected_profit_amount IS NOT NULL",
        (NETWORK, CHAIN_ID)))
    report["reads"].append(timed(
        "record.full_scan_payload_bytes",
        "SELECT COUNT(*), SUM(LENGTH(payload_json)) FROM cow_execution_reviews "
        "WHERE network=? AND chain_id=?",
        (NETWORK, CHAIN_ID)))
    report["reads"].append(timed(
        "record.datetime_predicate_blocks_index",
        "SELECT COUNT(*) FROM cow_execution_reviews "
        "WHERE network=? AND chain_id=? AND datetime(created_at)>=datetime('now','-24 hours')",
        (NETWORK, CHAIN_ID)))
    report["reads"].append(timed(
        "record.raw_predicate_uses_index",
        "SELECT COUNT(*) FROM cow_execution_reviews "
        "WHERE network=? AND chain_id=? AND created_at>=datetime('now','-24 hours')",
        (NETWORK, CHAIN_ID)))

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
