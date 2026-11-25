#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Whole-store snapshot of a live chain store (read only).

§probe_live_db_writes_20261125.py§ compares the production gate keys, the
§kv_meta§ rows and two row counts.  That is enough to see a *known* write,
but it cannot answer the stronger question the T-储.01 acceptance asks:
**did the test suite write anything at all into the production store?**

This probe therefore walks **every** table of the store and records

* the row count, and
* a sha256 over the table content,

plus a row level snapshot of §kv_entries§ and §kv_meta§ (the two tables the
KV layer writes into) so a rewrite that stores the same value is still
visible.

The store is opened with §mode=ro§, so the snapshot itself can never write.
The report is a **before** / **after** pair; the diff is computed by
§--compare§ against an earlier snapshot file.

Because the live opportunity console writes the production store while it
runs, a table that changes during the window is **not** evidence of a test
write.  The reading that counts is the **differential** one: the same
measurement over a window **without** pytest (the silent control) produces
the same set of changed tables.

Usage:
    python probe_full_store_snapshot_20261125.py --db <path>
                                                  [--out <json>]
                                                  [--compare <json>]
                                                  [--tables <a,b,c>]
                                                  [--skip-content <a,b,c>]
                                                  [--quiet]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import sys


#: Named read-only probes that survive the ``content-max-rows`` cap.  The
#: live opportunity console appends to the review tables continuously, so a
#: content digest of the whole table changes during every window.  These
#: probes look at the **rows a test suite leaves behind** instead: the local
#: attempt keys, and the rows that carry chain id 0 (the chain scope a test
#: fixture uses).  They stay at zero for a window without pytest.
WATCH_QUERIES = {
    "kv_entries_parameters": (
        "SELECT key, value_json, updated_at FROM kv_entries "
        "WHERE collection = 'parameters' ORDER BY key"
    ),
    "reviews_local_attempt": (
        "SELECT attempt_key, chain_id, market_id, pair, updated_at "
        "FROM cow_execution_reviews WHERE attempt_key LIKE 'attempt|local|%' "
        "ORDER BY attempt_key"
    ),
    "attempts_local_attempt": (
        "SELECT attempt_key, chain_id, market_id, pair, updated_at "
        "FROM cow_execution_attempts WHERE attempt_key LIKE 'attempt|local|%' "
        "ORDER BY attempt_key"
    ),
    "reviews_chain_zero": (
        "SELECT attempt_key, chain_id, market_id, pair, updated_at "
        "FROM cow_execution_reviews WHERE chain_id = 0 ORDER BY attempt_key"
    ),
    "attempts_chain_zero": (
        "SELECT attempt_key, chain_id, market_id, pair, updated_at "
        "FROM cow_execution_attempts WHERE chain_id = 0 ORDER BY attempt_key"
    ),
}


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _ro_connection(db_path: str) -> sqlite3.Connection:
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=60.0)
    connection.row_factory = sqlite3.Row
    return connection


def _table_names(connection: sqlite3.Connection) -> list:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def _digest_rows(rows) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(tuple(row)).encode("utf-8", "replace"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _row_digest(value, updated_at) -> str:
    digest = hashlib.sha256()
    digest.update(str(value).encode("utf-8", "replace"))
    digest.update(b"\x00")
    digest.update(str(updated_at).encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


def _table_digest(connection: sqlite3.Connection, table: str) -> str:
    digest = hashlib.sha256()
    quoted = table.replace('"', '""')
    # ``ORDER BY rowid`` keeps the scan in the storage order without paying
    # for a sort of every column of the row.  A WITHOUT ROWID table has no
    # rowid, so it falls back to the natural scan order.
    try:
        connection.execute('SELECT rowid FROM "%s" LIMIT 1' % quoted).fetchone()
        order = "rowid"
    except sqlite3.Error:
        order = ""
    statement = 'SELECT * FROM "%s"' % quoted
    if order:
        statement += " ORDER BY rowid"
    cursor = connection.execute(statement)
    while True:
        chunk = cursor.fetchmany(500)
        if not chunk:
            break
        for row in chunk:
            digest.update(repr(tuple(row)).encode("utf-8", "replace"))
            digest.update(b"\x1e")
    return digest.hexdigest()


def snapshot(db_path: str, *, skip_content=(), tables=None, content_max_rows=20000) -> dict:
    connection = _ro_connection(db_path)
    try:
        names = _table_names(connection)
        if tables:
            wanted = {name.strip() for name in tables.split(",") if name.strip()}
            names = [name for name in names if name in wanted]
        table_state = {}
        for name in names:
            try:
                count = int(
                    connection.execute(
                        'SELECT COUNT(*) FROM "%s"' % name.replace('"', '""')
                    ).fetchone()[0]
                )
            except sqlite3.Error as error:
                table_state[name] = {"count": None, "error": str(error)}
                continue
            entry = {"count": count}
            if name in skip_content:
                entry["content"] = "skipped"
            elif content_max_rows is not None and count > content_max_rows:
                # A table with more rows than the cap only gets its row
                # count: the live opportunity console appends to the review
                # tables continuously, so a content digest of those tables
                # would change during **every** window and would say nothing
                # about the test suite.
                entry["content"] = "count-only"
            else:
                try:
                    entry["sha256"] = _table_digest(connection, name)
                    entry["content"] = "digest"
                except sqlite3.Error as error:
                    entry["sha256"] = None
                    entry["content"] = "error"
                    entry["error"] = str(error)
            table_state[name] = entry

        kv_rows = connection.execute(
            "SELECT collection, key, value_json, updated_at FROM kv_entries "
            "ORDER BY collection, key"
        ).fetchall()
        meta_rows = connection.execute(
            "SELECT meta_key, value_json FROM kv_meta ORDER BY meta_key"
        ).fetchall()
        watch = {}
        for name, statement in WATCH_QUERIES.items():
            try:
                rows = connection.execute(statement).fetchall()
            except sqlite3.Error as error:
                watch[name] = {"count": None, "error": str(error)}
                continue
            watch[name] = {"count": len(rows), "sha256": _digest_rows(rows)}
        return {
            "generatedAt": _now(),
            "database": os.path.abspath(db_path),
            "tableCount": len(table_state),
            "tables": table_state,
            "kvEntriesCount": len(kv_rows),
            "kvEntriesSha256": _digest_rows(kv_rows),
            # The row level map keeps a **digest** of the value instead of the
            # value itself: a rewrite that stores the same bytes but moves
            # ``updated_at`` still shows up, and the evidence file stays small
            # enough to be committed.
            "kvEntriesRows": {
                "%s|%s" % (row["collection"], row["key"]): {
                    "v": _row_digest(row["value_json"], row["updated_at"]),
                    "updated_at": row["updated_at"],
                }
                for row in kv_rows
            },
            "kvMetaCount": len(meta_rows),
            "kvMetaSha256": _digest_rows(meta_rows),
            "kvMetaRows": {
                row["meta_key"]: _row_digest(row["value_json"], "")
                for row in meta_rows
            },
            "watch": watch,
        }
    finally:
        connection.close()


def diff(before: dict, after: dict) -> dict:
    before_tables = before.get("tables", {})
    after_tables = after.get("tables", {})
    changed_tables = []
    count_changed = []
    digest_changed = []
    for name in sorted(set(before_tables) | set(after_tables)):
        old = before_tables.get(name, {})
        new = after_tables.get(name, {})
        old_count = old.get("count")
        new_count = new.get("count")
        old_digest = old.get("sha256")
        new_digest = new.get("sha256")
        if old_count != new_count:
            count_changed.append(
                {"table": name, "before": old_count, "after": new_count}
            )
        if old_digest is not None and new_digest is not None and old_digest != new_digest:
            digest_changed.append(
                {"table": name, "before": old_digest, "after": new_digest}
            )
        if old_count != new_count or (
            old_digest is not None
            and new_digest is not None
            and old_digest != new_digest
        ):
            changed_tables.append(name)

    kv_changed = []
    before_kv = before.get("kvEntriesRows", {})
    after_kv = after.get("kvEntriesRows", {})
    for key in sorted(set(before_kv) | set(after_kv)):
        if before_kv.get(key) != after_kv.get(key):
            kv_changed.append(
                {"key": key, "before": before_kv.get(key), "after": after_kv.get(key)}
            )

    meta_changed = []
    before_meta = before.get("kvMetaRows", {})
    after_meta = after.get("kvMetaRows", {})
    for key in sorted(set(before_meta) | set(after_meta)):
        if before_meta.get(key) != after_meta.get(key):
            meta_changed.append(
                {"key": key, "before": before_meta.get(key), "after": after_meta.get(key)}
            )

    window = None
    try:
        window = round(
            (
                datetime.datetime.fromisoformat(after["generatedAt"])
                - datetime.datetime.fromisoformat(before["generatedAt"])
            ).total_seconds(),
            3,
        )
    except (KeyError, TypeError, ValueError):
        window = None

    return {
        "windowSeconds": window,
        "changedTableCount": len(changed_tables),
        "changedTables": changed_tables,
        "countChanged": count_changed,
        "digestChanged": digest_changed,
        "kvEntriesChangedCount": len(kv_changed),
        "kvEntriesChanged": kv_changed,
        "kvMetaChangedCount": len(meta_changed),
        "kvMetaChanged": meta_changed,
        "kvEntriesSha256Before": before.get("kvEntriesSha256"),
        "kvEntriesSha256After": after.get("kvEntriesSha256"),
        "kvMetaSha256Before": before.get("kvMetaSha256"),
        "kvMetaSha256After": after.get("kvMetaSha256"),
        "watchBefore": before.get("watch", {}),
        "watchAfter": after.get("watch", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--compare", default=None, help="an earlier snapshot json")
    parser.add_argument("--tables", default=None, help="comma separated table whitelist")
    parser.add_argument(
        "--skip-content",
        default="",
        help="comma separated tables whose content digest is skipped",
    )
    parser.add_argument(
        "--content-max-rows",
        type=int,
        default=20000,
        help="tables with more rows than this get a row count only",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    skip = {name.strip() for name in args.skip_content.split(",") if name.strip()}
    current = snapshot(
        args.db,
        skip_content=skip,
        tables=args.tables,
        content_max_rows=args.content_max_rows,
    )
    report = current
    if args.compare:
        with open(os.path.abspath(args.compare), "r", encoding="utf-8") as handle:
            earlier = json.load(handle)
        report = {
            "current": current,
            "comparedWith": args.compare,
            "diff": diff(earlier, current),
        }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    if not args.quiet:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
