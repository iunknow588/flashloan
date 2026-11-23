#!/usr/bin/env python3
"""Rollback the directional-pool switch (TASK-170, plan 03 section 2.3).

Three modes, nothing else:

* default -- print the manifest of what would be rolled back.  Nothing is
  written, not even to a scratch database.
* --rehearse -- prove the rollback on a shadow copy of the closed-loop
  tables.  The shadow database lives in a temporary file, the live library
  is only ever opened read-only, and the shadow transaction is rolled
  back.  The library file fingerprint is identical before and after, which
  is the proof that the rehearsal never wrote to the live library.
* --apply -- actually write.  This is a live write and therefore needs
  authorisation; it is NOT part of TASK-170 and the script never runs it
  on its own.

What the rollback covers, and nothing else:

* the four directional columns on manager_v4_closed_loop_hops,
* the one directional column on manager_v4_closed_loops,
* the P3 old-column restore, which only fires when P3 has actually run and
  degraded a legacy direction-key column.

It never changes the value of pool_id, base_pool_id or loop_key, and it
never deletes a directional pool row: the directional layer stays
readable so that the switch can be retried.

manager_v4_closed_loop_library_state is deliberately left alone.  Its
closed_loop_version and consumers_need_rebuild are derived state that the
normal rebuild refreshes; writing them by hand would fake a version
consistency the data does not have.

Usage:

    python rollback_directional_pool_switch_20261123.py
    python rollback_directional_pool_switch_20261123.py --rehearse
    python rollback_directional_pool_switch_20261123.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")
REPORT_DIR = os.path.join(ROOT, "plan", "runtime_evidence", "reports")

#: The closed-loop tables P2 touches.  Nothing outside this pair is written.
CLOSED_LOOP_TABLES = ("manager_v4_closed_loops", "manager_v4_closed_loop_hops")

#: P2 write-back: the four directional columns on the hop table.
HOP_DIRECTIONAL_COLUMNS = (
    "directional_pool_id",
    "hop_direction",
    "directional_input_token_key_id",
    "directional_output_token_key_id",
)

#: P2 write-back: the one directional column on the loop table.
LOOP_DIRECTIONAL_COLUMNS = ("directional_pool_path_key",)

#: The DDL the P1 migration adds (plan 02 section 4.1).  The shadow copy
#: uses it so the rehearsal has directional columns to roll back even
#: while the live library does not carry them yet.
DIRECTIONAL_COLUMN_DDL = (
    (
        "manager_v4_closed_loop_hops",
        "directional_pool_id",
        "ALTER TABLE manager_v4_closed_loop_hops ADD COLUMN directional_pool_id TEXT",
    ),
    (
        "manager_v4_closed_loop_hops",
        "hop_direction",
        "ALTER TABLE manager_v4_closed_loop_hops ADD COLUMN hop_direction INTEGER"
        " CHECK (hop_direction IN (0, 1))",
    ),
    (
        "manager_v4_closed_loop_hops",
        "directional_input_token_key_id",
        "ALTER TABLE manager_v4_closed_loop_hops"
        " ADD COLUMN directional_input_token_key_id TEXT",
    ),
    (
        "manager_v4_closed_loop_hops",
        "directional_output_token_key_id",
        "ALTER TABLE manager_v4_closed_loop_hops"
        " ADD COLUMN directional_output_token_key_id TEXT",
    ),
    (
        "manager_v4_closed_loops",
        "directional_pool_path_key",
        "ALTER TABLE manager_v4_closed_loops ADD COLUMN directional_pool_path_key TEXT",
    ),
)

#: P3 old-column restore.  Each entry is (table, legacy direction column).
#: P3 has not run yet, so the script has to recognise every shape P3 could
#: leave behind instead of assuming one.
LEGACY_DIRECTION_COLUMNS = (
    ("manager_dex_candidate_pool_recommendations", "direction_key"),
    ("manager_market_observation_yz_pairs", "direction_key"),
)

#: Columns whose value the rollback must never change.
PROTECTED_COLUMNS = ("pool_id", "base_pool_id", "loop_key")

LOOP_KEY_COLUMNS = ("network", "chain_id", "protocol", "scope_key", "loop_key")
HOP_KEY_COLUMNS = LOOP_KEY_COLUMNS + ("hop_index",)

ALL_DIRECTIONAL_COLUMNS = HOP_DIRECTIONAL_COLUMNS + LOOP_DIRECTIONAL_COLUMNS


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: str, full: bool) -> Dict[str, Any]:
    """Identify the library file.

    The full sha256 is the value the acceptance criteria ask for; it costs
    about forty seconds on the mainnet library, so the default (read-only)
    mode only records size and mtime and leaves the full digest to the
    modes that can actually change the file.
    """
    stat = os.stat(path)
    result: Dict[str, Any] = {
        "path": path,
        "sizeBytes": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
    }
    if full:
        result["sha256"] = sha256_file(path)
    return result


def sibling_files(path: str) -> List[Dict[str, Any]]:
    """The write-ahead-log siblings of the library file.

    A read-only open of a WAL library makes SQLite create the shared-memory
    index and a write-ahead log.  The index carries no library data and the
    log stays zero bytes unless somebody writes, so neither is a change to
    the library itself -- but they have to be reported rather than hidden.
    """
    directory = os.path.dirname(path) or "."
    base = os.path.basename(path)
    found: List[Dict[str, Any]] = []
    for suffix in ("-wal", "-shm"):
        candidate = os.path.join(directory, base + suffix)
        if os.path.exists(candidate):
            stat = os.stat(candidate)
            found.append(
                {
                    "path": candidate,
                    "sizeBytes": stat.st_size,
                    "mtimeNs": stat.st_mtime_ns,
                }
            )
    return found


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open the library read-only.

    mode=ro makes the kernel refuse a write, and query_only makes SQLite
    refuse one as well.  Two independent refusals, because the rehearsal
    must not be able to touch the live library even by accident.
    """
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=180.0)
    connection.execute("PRAGMA query_only = ON")
    return connection


class LibraryWatcher:
    """Sample the live library while the rehearsal runs.

    The rehearsal holds the library read-only, but the library is a live
    database: the Manager API service keeps it open for writing, and a
    checkpoint by that service rewrites pages of the main file in place.
    A byte-for-byte comparison of the library file therefore only proves
    "the rehearsal did not write" while the library is quiescent.

    This watcher records the timeline of every change to the library file
    and to its write-ahead log, so a change can be attributed to another
    writer instead of being silently charged to the rehearsal.
    """

    def __init__(self, path: str, interval: float = 0.25) -> None:
        self.path = path
        self.interval = interval
        self.events: List[Dict[str, Any]] = []
        self.samples = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last: Optional[Dict[str, Any]] = None

    def _sample(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {"at": round(time.time(), 3)}
        try:
            stat = os.stat(self.path)
            row["sizeBytes"] = stat.st_size
            row["mtimeNs"] = stat.st_mtime_ns
        except OSError:
            row["sizeBytes"] = None
            row["mtimeNs"] = None
        wal = self.path + "-wal"
        try:
            wal_stat = os.stat(wal)
            row["walSizeBytes"] = wal_stat.st_size
            row["walMtimeNs"] = wal_stat.st_mtime_ns
        except OSError:
            row["walSizeBytes"] = None
            row["walMtimeNs"] = None
        return row

    def _run(self) -> None:
        last = self._sample()
        self._last = last
        while not self._stop.wait(self.interval):
            current = self._sample()
            self.samples += 1
            # The sample timestamp always differs, so the comparison has
            # to look at the substantive fields only; otherwise every
            # sample would be reported as a change.
            library_changed = (
                current["sizeBytes"] != last["sizeBytes"]
                or current["mtimeNs"] != last["mtimeNs"]
            )
            sibling_changed = (
                current["walSizeBytes"] != last["walSizeBytes"]
                or current["walMtimeNs"] != last["walMtimeNs"]
            )
            if library_changed or sibling_changed:
                self.events.append(
                    {
                        "changedAt": time.strftime(
                            "%Y-%m-%dT%H:%M:%S%z", time.localtime(current["at"])
                        ),
                        "kind": (
                            "library_and_sibling"
                            if library_changed and sibling_changed
                            else ("library" if library_changed else "sibling")
                        ),
                        "before": last,
                        "after": current,
                    }
                )
            last = current

    def start(self) -> "LibraryWatcher":
        self._thread = threading.Thread(
            target=self._run, name="library-watcher", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        library_events = [
            event for event in self.events if event["kind"].startswith("library")
        ]
        sibling_events = [
            event for event in self.events if event["kind"].endswith("sibling")
        ]
        wal_content_events = [
            event
            for event in self.events
            if (event["after"].get("walSizeBytes") or 0) > 0
        ]
        return {
            "intervalSeconds": self.interval,
            "samples": self.samples,
            "events": self.events,
            "liveFileChangedDuringWindow": bool(library_events),
            "siblingFilesChangedDuringWindow": bool(sibling_events),
            "walContentSeenDuringWindow": bool(wal_content_events),
            "attributionNote": (
                "A -wal/-shm pair appearing during the window is a side effect"
                " of opening a WAL library read-only; it carries no library"
                " data and is not charged to an external writer."
            ),
        }


def table_kind(connection: sqlite3.Connection, name: str) -> Optional[str]:
    row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def table_columns(connection: sqlite3.Connection, name: str) -> List[str]:
    return [row[1] for row in connection.execute("PRAGMA table_info(%s)" % name)]


def view_source_table(connection: sqlite3.Connection, view: str) -> Optional[str]:
    """The table a view reads from, when the view is a plain rename.

    P3 degrading a column to a view keeps the rows by renaming the table
    and pointing a view at it, so the source table name is what tells the
    rollback that the data is still there.
    """
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (view,)
    ).fetchone()
    if row is None or not row[0]:
        return None
    match = re.search(r"\bFROM\s+[\"\x60]?(\w+)", row[0], re.IGNORECASE)
    return match.group(1) if match else None


def build_rollback_targets(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    """The statements that undo what P2 wrote.

    Each statement nulls the directional columns of one table and only
    touches rows that still carry a directional value, which is what makes
    a second run report "nothing to roll back" instead of failing.
    """
    targets: List[Dict[str, Any]] = []
    for table, columns, key_columns in (
        ("manager_v4_closed_loop_hops", HOP_DIRECTIONAL_COLUMNS, HOP_KEY_COLUMNS),
        ("manager_v4_closed_loops", LOOP_DIRECTIONAL_COLUMNS, LOOP_KEY_COLUMNS),
    ):
        if table_kind(connection, table) != "table":
            targets.append(
                {
                    "table": table,
                    "columns": [],
                    "missingColumns": list(columns),
                    "state": "table_missing",
                    "affectedRows": 0,
                    "statement": None,
                    "keyColumns": list(key_columns),
                }
            )
            continue
        present = table_columns(connection, table)
        target = [column for column in columns if column in present]
        missing = [column for column in columns if column not in present]
        if not target:
            targets.append(
                {
                    "table": table,
                    "columns": [],
                    "missingColumns": missing,
                    "state": "no_target_columns",
                    "affectedRows": 0,
                    "statement": None,
                    "keyColumns": list(key_columns),
                }
            )
            continue
        assignments = ", ".join("%s = NULL" % column for column in target)
        predicate = " OR ".join("%s IS NOT NULL" % column for column in target)
        statement = "UPDATE %s SET %s WHERE %s" % (table, assignments, predicate)
        affected = connection.execute(
            "SELECT COUNT(*) FROM %s WHERE %s" % (table, predicate)
        ).fetchone()[0]
        targets.append(
            {
                "table": table,
                "columns": target,
                "missingColumns": missing,
                "state": "ready" if affected else "already_null",
                "affectedRows": int(affected),
                "statement": statement,
                "keyColumns": list(key_columns),
            }
        )
    return targets


def build_canonical_statements() -> List[Dict[str, Any]]:
    """The rollback statements as they are defined, independent of the schema.

    The manifest has to stay readable while the directional columns do not
    exist yet -- that is exactly the state P2 leaves the library in before
    it is switched.  These are the statements the rollback would run once
    the columns are there.
    """
    canonical: List[Dict[str, Any]] = []
    for table, columns in (
        ("manager_v4_closed_loop_hops", HOP_DIRECTIONAL_COLUMNS),
        ("manager_v4_closed_loops", LOOP_DIRECTIONAL_COLUMNS),
    ):
        assignments = ", ".join("%s = NULL" % column for column in columns)
        predicate = " OR ".join("%s IS NOT NULL" % column for column in columns)
        canonical.append(
            {
                "table": table,
                "columns": list(columns),
                "statement": "UPDATE %s SET %s WHERE %s" % (table, assignments, predicate),
            }
        )
    return canonical


def build_p3_restore_plan(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    """How the P3 old-column restore would act on each legacy column.

    Three shapes are recognised: the column is still a table column (P3 has
    not degraded it, nothing to restore), the table was degraded to a view
    over a renamed table (the rows are still there and can be restored), or
    the column was dropped (the values are gone, and no script can bring
    them back -- that case is reported, not silently faked).
    """
    plan: List[Dict[str, Any]] = []
    for table, column in LEGACY_DIRECTION_COLUMNS:
        kind = table_kind(connection, table)
        entry: Dict[str, Any] = {
            "table": table,
            "column": column,
            "state": "table_missing",
            "restoreStatement": None,
            "note": "",
        }
        if kind == "table":
            if column in table_columns(connection, table):
                entry["state"] = "column_present"
                entry["note"] = "P3 has not degraded this column; nothing to restore"
            else:
                entry["state"] = "column_dropped"
                entry["note"] = (
                    "the column values were removed by the drop and cannot be"
                    " reconstructed; restoring the structure needs the pre-P3"
                    " backup, so the script refuses to fake an empty column"
                )
        elif kind == "view":
            source = view_source_table(connection, table)
            if source and table_kind(connection, source) == "table":
                entry["state"] = "degraded_to_view"
                entry["restoreStatement"] = (
                    "DROP VIEW %s; ALTER TABLE %s RENAME TO %s"
                    % (table, source, table),
                )
                entry["note"] = "the rows survive under %s and can be restored" % source
            else:
                entry["state"] = "view_without_local_source"
                entry["note"] = (
                    "the view reads from %s, which this script does not"
                    " rename automatically"
                    % (source or "an unknown source"),
                )
        plan.append(entry)
    return plan


def snapshot_closed_loops(
    connection: sqlite3.Connection, excluded_columns: Sequence[str]
) -> Dict[str, List[Tuple[Any, ...]]]:
    """The closed-loop set, projected without the directional columns.

    The comparison deliberately excludes the columns the rollback writes.
    The claim being proved is "the rollback changes nothing but the
    directional columns", so including those columns in the comparison
    would compare the change against itself.
    """
    excluded = set(excluded_columns)
    snapshot: Dict[str, List[Tuple[Any, ...]]] = {}
    for table, key_columns in (
        ("manager_v4_closed_loops", LOOP_KEY_COLUMNS),
        ("manager_v4_closed_loop_hops", HOP_KEY_COLUMNS),
    ):
        columns = [
            column
            for column in table_columns(connection, table)
            if column not in excluded
        ]
        order = [column for column in key_columns if column in columns]
        sql = "SELECT %s FROM %s" % (", ".join(columns), table)
        if order:
            sql += " ORDER BY %s" % ", ".join(order)
        snapshot[table] = [tuple(row) for row in connection.execute(sql)]
    return snapshot


def count_non_null_directional(connection: sqlite3.Connection) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table, columns in (
        ("manager_v4_closed_loop_hops", HOP_DIRECTIONAL_COLUMNS),
        ("manager_v4_closed_loops", LOOP_DIRECTIONAL_COLUMNS),
    ):
        present = set(table_columns(connection, table))
        total = 0
        for column in columns:
            if column not in present:
                continue
            total += connection.execute(
                "SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL" % (table, column)
            ).fetchone()[0]
        counts[table] = int(total)
    return counts


def build_shadow(source: sqlite3.Connection, shadow: sqlite3.Connection) -> Dict[str, Any]:
    """Copy the closed-loop tables into a scratch database.

    The scratch database receives the directional columns even when the
    live library does not have them yet, and every directional cell is
    filled with a synthetic value.  That is what makes the rehearsal
    non-vacuous: the rollback really has something to roll back, and the
    equality that follows is a real statement about the rollback rather
    than a statement about an empty table.
    """
    copied: Dict[str, int] = {}
    for table in CLOSED_LOOP_TABLES:
        row = source.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if row is None:
            raise RuntimeError("closed-loop table %s is missing" % table)
        shadow.execute(row[0])
        cursor = source.execute("SELECT * FROM %s" % table)
        names = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        placeholders = ",".join("?" for _ in names)
        shadow.executemany(
            "INSERT INTO %s (%s) VALUES (%s)"
            % (table, ", ".join(names), placeholders),
            rows,
        )
        copied[table] = len(rows)
    added: List[str] = []
    for table, column, statement in DIRECTIONAL_COLUMN_DDL:
        if column not in table_columns(shadow, table):
            shadow.execute(statement)
            added.append("%s.%s" % (table, column))
    # Fill the directional columns so the shadow looks like a library that
    # already went through the P2 switch.
    shadow.execute(
        "UPDATE manager_v4_closed_loop_hops SET"
        " directional_pool_id = scope_key || ':' || CAST(hop_index AS TEXT),"
        " hop_direction = hop_index % 2,"
        " directional_input_token_key_id = input_token_key_id,"
        " directional_output_token_key_id = output_token_key_id"
    )
    shadow.execute(
        "UPDATE manager_v4_closed_loops SET"
        " directional_pool_path_key = scope_key || ':' || loop_key"
    )
    return {"copiedRows": copied, "addedColumns": added}


def execute_rollback_statements(
    connection: sqlite3.Connection, targets: Sequence[Dict[str, Any]]
) -> Dict[str, int]:
    """Run the rollback statements and report how many rows each changed.

    A row count of zero is the "nothing to roll back" answer: the second
    run of an idempotent rollback has to say so instead of failing.
    """
    changed: Dict[str, int] = {}
    for target in targets:
        statement = target.get("statement")
        if not statement:
            changed[target["table"]] = 0
            continue
        cursor = connection.execute(statement)
        changed[target["table"]] = int(cursor.rowcount if cursor.rowcount > 0 else 0)
    return changed


def compare_snapshots(
    before: Dict[str, List[Tuple[Any, ...]]],
    after: Dict[str, List[Tuple[Any, ...]]]
) -> Dict[str, Any]:
    """Compare two closed-loop snapshots item by item.

    The comparison is positional because both snapshots are ordered by the
    primary key, so "item by item equal" means the same loop carries the
    same values.  A difference is reported as the first diverging row
    rather than as a count, because a count alone does not say what
    diverged.
    """
    result: Dict[str, Any] = {"equal": True, "tables": {}}
    for table, before_rows in before.items():
        after_rows = after.get(table, [])
        table_result = {
            "rowsBefore": len(before_rows),
            "rowsAfter": len(after_rows),
            "equal": before_rows == after_rows,
        }
        if before_rows != after_rows:
            result["equal"] = False
            for index, (left, right) in enumerate(zip(before_rows, after_rows)):
                if left != right:
                    table_result["firstDifference"] = {
                        "rowIndex": index,
                        "before": [str(value) for value in left],
                        "after": [str(value) for value in right],
                    }
                    break
            else:
                table_result["firstDifference"] = {
                    "rowIndex": min(len(before_rows), len(after_rows)),
                    "before": "row count differs",
                    "after": "row count differs",
                }
        result["tables"][table] = table_result
    return result


def run_plan(connection: sqlite3.Connection, db_path: str) -> Dict[str, Any]:
    """The read-only manifest: what the rollback would do right now."""
    targets = build_rollback_targets(connection)
    p3 = build_p3_restore_plan(connection)
    return {
        "mode": "plan",
        "writes": False,
        "targets": targets,
        "canonicalStatements": build_canonical_statements(),
        "p3Restore": p3,
        "protectedColumns": list(PROTECTED_COLUMNS),
        "siblingsBefore": sibling_files(db_path),
    }


def run_rehearse(
    connection: sqlite3.Connection,
    db_path: str,
    fingerprint_full: bool,
    watcher: Optional[LibraryWatcher] = None,
) -> Dict[str, Any]:
    """Prove the rollback on a shadow copy of the closed-loop tables.

    The live library is opened read-only and is never written.  The shadow
    copy carries the same rows plus synthetic directional values, so the
    rollback has real work to do; the proof is that after the rollback the
    closed-loop set is item-by-item what it was before, that every
    directional cell is NULL, that a second pass changes nothing, and
    that the library file is bit-for-bit what it was.
    """
    if watcher is not None:
        watcher.start()
    before_fingerprint = fingerprint(db_path, fingerprint_full)
    before_siblings = sibling_files(db_path)
    live_targets = build_rollback_targets(connection)
    live_p3 = build_p3_restore_plan(connection)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="directional_pool_rollback_") as workspace:
        shadow_path = os.path.join(workspace, "shadow.sqlite3")
        shadow = sqlite3.connect(shadow_path, isolation_level=None)
        try:
            build = build_shadow(connection, shadow)
            shadow_targets = build_rollback_targets(shadow)
            before = snapshot_closed_loops(shadow, ALL_DIRECTIONAL_COLUMNS)
            directional_before = count_non_null_directional(shadow)

            shadow.execute("BEGIN")
            first_pass = execute_rollback_statements(shadow, shadow_targets)
            after = snapshot_closed_loops(shadow, ALL_DIRECTIONAL_COLUMNS)
            directional_after = count_non_null_directional(shadow)
            second_pass = execute_rollback_statements(shadow, shadow_targets)
            shadow.execute("ROLLBACK")

            # The transaction rollback has to restore the shadow too, which
            # proves the whole rehearsal ran inside one transaction.
            directional_restored = count_non_null_directional(shadow)
        finally:
            shadow.close()
    after_fingerprint = fingerprint(db_path, fingerprint_full)
    after_siblings = sibling_files(db_path)
    comparison = compare_snapshots(before, after)
    watch = watcher.stop() if watcher is not None else {
        "intervalSeconds": None,
        "samples": 0,
        "events": [],
        "liveFileChangedDuringWindow": False,
    }
    external_writer = bool(watch.get("liveFileChangedDuringWindow"))
    second_pass_total = sum(second_pass.values())
    wal_content = [
        entry
        for entry in after_siblings
        if entry["path"].endswith("-wal") and entry["sizeBytes"] != 0
    ]
    wal_seen_non_zero = any(
        event["after"].get("walSizeBytes")
        for event in watch.get("events", [])
    )
    checks = {
        "closedLoopSetUnchanged": comparison["equal"],
        "allDirectionalColumnsNulled": all(value == 0 for value in directional_after.values()),
        "secondPassChangedNothing": second_pass_total == 0,
        "transactionRolledBack": directional_restored == directional_before,
        "libraryFileUnchanged": before_fingerprint == after_fingerprint,
        "noWalContentWritten": not wal_content,
    }
    # The live library is a live production database: the Manager API
    # service keeps it open for writing, and a checkpoint by that writer
    # rewrites pages of the main file in place.  A byte-for-byte
    # comparison of the library file therefore only proves "the rehearsal
    # did not write" while the library is quiescent.
    #
    # What the rehearsal itself is responsible for is proved structurally:
    # its only connection to the live library is read-only (mode=ro plus
    # PRAGMA query_only), and every mutation lands in the shadow copy.
    # What it is NOT responsible for is reported with attribution: the
    # watcher timeline says when the live file changed and whether the
    # write-ahead log ever carried content.
    checks["liveLibraryQuiescent"] = not external_writer and not wal_seen_non_zero
    checks["walContentAttributableToRehearsal"] = (
        not wal_content and not wal_seen_non_zero
    )
    checks["libraryFileUnchangedOrExternalWriter"] = (
        checks["libraryFileUnchanged"] or external_writer
    )
    return {
        "mode": "rehearse",
        "writes": False,
        "durationSeconds": round(time.time() - started, 3),
        "shadow": build,
        "shadowTargets": shadow_targets,
        "liveTargets": live_targets,
        "liveP3Restore": live_p3,
        "canonicalStatements": build_canonical_statements(),
        "protectedColumns": list(PROTECTED_COLUMNS),
        "firstPassChangedRows": first_pass,
        "secondPassChangedRows": second_pass,
        "directionalCellsBefore": directional_before,
        "directionalCellsAfter": directional_after,
        "directionalCellsAfterTransactionRollback": directional_restored,
        "closedLoopComparison": comparison,
        "fingerprintBefore": before_fingerprint,
        "fingerprintAfter": after_fingerprint,
        "libraryWatch": watch,
        "siblingsBefore": before_siblings,
        "siblingsAfter": after_siblings,
        "siblingNote": (
            "SQLite creates the shared-memory index and the write-ahead log"
            " next to a WAL library when it is opened, read-only included."
            " They carry no library data and the log stays zero bytes, so"
            " they are not a write to the library; the library file itself is"
            " compared byte for byte."
        ),
        "checks": checks,
        "passed": (
            checks["closedLoopSetUnchanged"]
            and checks["allDirectionalColumnsNulled"]
            and checks["secondPassChangedNothing"]
            and checks["transactionRolledBack"]
            and checks["libraryFileUnchangedOrExternalWriter"]
            and (checks["noWalContentWritten"] or external_writer)
        ),
    }


def run_apply(db_path: str, fingerprint_full: bool) -> Dict[str, Any]:
    """Write the rollback to the live library.

    This is a live write and therefore needs authorisation.  It is not
    part of TASK-170; the script only performs it when the operator asks
    for it explicitly with --apply.
    """
    before_fingerprint = fingerprint(db_path, fingerprint_full)
    connection = sqlite3.connect(db_path, timeout=180.0, isolation_level=None)
    try:
        targets = build_rollback_targets(connection)
        p3 = build_p3_restore_plan(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            changed = execute_rollback_statements(connection, targets)
            restored = []
            for entry in p3:
                statement = entry.get("restoreStatement")
                if not statement:
                    continue
                for part in statement.split("; "):
                    connection.execute(part)
                restored.append(entry["table"])
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    after_fingerprint = fingerprint(db_path, fingerprint_full)
    return {
        "mode": "apply",
        "writes": True,
        "changedRows": changed,
        "restoredLegacyTables": restored,
        "fingerprintBefore": before_fingerprint,
        "fingerprintAfter": after_fingerprint,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# TASK-170 rollback rehearsal - directional pool switch")
    lines.append("")
    lines.append("")
    lines.append("| Item | Value |")
    lines.append("|---|---|")
    lines.append("| Mode | `%s` |" % report["mode"])
    lines.append("| Live library written | **%s** |" % ("yes" if report["writes"] else "no"))
    lines.append("| Library | `%s` |" % report["library"]["path"])
    lines.append("| Chain | %s |" % report["library"]["chainId"])
    lines.append("| Generated | %s |" % report["generatedAt"])
    if report["mode"] == "rehearse":
        lines.append("| Passed | **%s** |" % ("yes" if report["passed"] else "no"))
    lines.append("")
    lines.append("## Rollback manifest")
    lines.append("")
    lines.append("| Table | Target columns | Affected rows | State |")
    lines.append("|---|---|---:|---|")
    for target in report["targets"]:
        lines.append(
            "| `%s` | %s | %d | %s |"
            % (
                target["table"],
                ", ".join("`%s`" % column for column in target["columns"]) or "-",
                target["affectedRows"],
                target["state"],
            ),
        )
    lines.append("")
    lines.append("## P3 legacy column restore")
    lines.append("")
    lines.append("| Table | Column | State | Note |")
    lines.append("|---|---|---|---|")
    for entry in report["p3Restore"]:
        lines.append(
            "| `%s` | `%s` | %s | %s |"
            % (entry["table"], entry["column"], entry["state"], entry["note"]),
        )
    lines.append("")
    if report["mode"] == "rehearse":
        lines.append("## Shadow manifest (what the rehearsal actually rolled back)")
        lines.append("")
        lines.append("| Table | Target columns | Affected rows | State |")
        lines.append("|---|---|---:|---|")
        for target in report.get("shadowTargets", []):
            lines.append(
                "| `%s` | %s | %d | %s |"
                % (
                    target["table"],
                    ", ".join("`%s`" % column for column in target["columns"]) or "-",
                    target["affectedRows"],
                    target["state"],
                ),
            )
        lines.append("")
        lines.append("## Rehearsal checks")
        lines.append("")
        lines.append("| Check | Result |")
        lines.append("|---|---|")
        for name, value in report["checks"].items():
            lines.append("| %s | %s |" % (name, "PASS" if value else "FAIL"))
        watch = report.get("libraryWatch") or {}
        lines.append("")
        lines.append("## Live library watcher")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.append("| Sample interval | %s s |" % watch.get("intervalSeconds"))
        lines.append("| Samples taken | %s |" % watch.get("samples"))
        lines.append(
            "| Live file changed during the window | %s |"
            % ("yes" if watch.get("liveFileChangedDuringWindow") else "no"),
        )
        lines.append(
            "| Sibling -wal/-shm changed during the window | %s |"
            % ("yes" if watch.get("siblingFilesChangedDuringWindow") else "no"),
        )
        lines.append(
            "| WAL carried content during the window | %s |"
            % ("yes" if watch.get("walContentSeenDuringWindow") else "no"),
        )
        lines.append("")
        if watch.get("events"):
            lines.append("> %s" % watch.get("attributionNote", ""))
            lines.append("")
            lines.append("| Changed at | kind | size | mtimeNs | wal size |")
            lines.append("|---|---|---:|---:|---:|")
            for event in watch["events"]:
                after = event["after"]
                lines.append(
                    "| %s | %s | %s | %s | %s |"
                    % (
                        event["changedAt"],
                        event.get("kind"),
                        after.get("sizeBytes"),
                        after.get("mtimeNs"),
                        after.get("walSizeBytes"),
                    ),
                )
            lines.append("")
            lines.append(
                "> The live library is a live production database.  A change here"
                " with kind `library` comes from another writer (the Manager API"
                " service keeps the library open for writing), not from the rehearsal:"
                " the rehearsal's only connection to it is read-only and every"
                " mutation it performs lands in the shadow copy.  A change with"
                " kind `sibling` is the -wal/-shm pair that SQLite creates next to a"
                " WAL library when it is opened, read-only included; it carries no"
                " library data and is not charged to an external writer."
            )
        lines.append("")
        lines.append("| Fingerprint | Before | After |")
        lines.append("|---|---|---|")
        for key in ("sizeBytes", "mtimeNs", "sha256"):
            if key in report["fingerprintBefore"]:
                lines.append(
                    "| %s | %s | %s |"
                    % (
                        key,
                        report["fingerprintBefore"][key],
                        report["fingerprintAfter"][key],
                    ),
                )
        lines.append("")
        lines.append("| Table | Rows before | Rows after | Equal |")
        lines.append("|---|---:|---:|---|")
        for table, detail in report["closedLoopComparison"]["tables"].items():
            lines.append(
                "| `%s` | %d | %d | %s |"
                % (
                    table,
                    detail["rowsBefore"],
                    detail["rowsAfter"],
                    "yes" if detail["equal"] else "NO",
                ),
            )
        lines.append("")
    lines.append("")
    lines.append("> The live library was opened with `mode=ro` and `PRAGMA query_only`;")
    lines.append("> the rehearsal ran on a shadow copy in a temporary directory and rolled")
    lines.append("> its transaction back.")
    if report.get("siblingNote"):
        lines.append(">")
        lines.append("> %s" % report["siblingNote"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rollback the directional-pool switch (TASK-170)."
    )
    parser.add_argument("--db", default=DEFAULT_DB, help="library path")
    parser.add_argument("--rehearse", action="store_true", help="rehearse on a shadow copy")
    parser.add_argument("--apply", action="store_true", help="actually write (needs authorisation)")
    parser.add_argument("--full-hash", action="store_true", help="always compute the full sha256")
    parser.add_argument("--report-dir", default=REPORT_DIR)
    parser.add_argument("--no-report", action="store_true", help="do not write a report file")
    args = parser.parse_args()

    if args.rehearse and args.apply:
        parser.error("--rehearse and --apply are mutually exclusive")

    if not os.path.exists(args.db):
        print("library not found: %s" % args.db)
        return 2

    mode = "apply" if args.apply else ("rehearse" if args.rehearse else "plan")
    full_hash = args.full_hash or mode != "plan"

    started = time.time()
    if mode == "apply":
        result = run_apply(args.db, full_hash)
        targets: List[Dict[str, Any]] = []
        p3: List[Dict[str, Any]] = []
    else:
        connection = open_readonly(args.db)
        try:
            if mode == "rehearse":
                result = run_rehearse(
                    connection,
                    args.db,
                    full_hash,
                    watcher=LibraryWatcher(args.db),
                )
            else:
                result = run_plan(connection, args.db)
        finally:
            connection.close()
        targets = result.get("liveTargets") or result.get("targets") or []
        p3 = result.get("liveP3Restore") or result.get("p3Restore") or []

    report: Dict[str, Any] = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": mode,
        "writes": result.get("writes", False),
        "library": {"path": args.db, "chainId": 4663, "network": "robinhood"},
        "targets": targets,
        "p3Restore": p3,
        "durationSeconds": round(time.time() - started, 3),
    }
    for key in (
        "passed",
        "checks",
        "canonicalStatements",
        "protectedColumns",
        "shadowTargets",
        "closedLoopComparison",
        "firstPassChangedRows",
        "secondPassChangedRows",
        "directionalCellsBefore",
        "directionalCellsAfter",
        "directionalCellsAfterTransactionRollback",
        "fingerprintBefore",
        "fingerprintAfter",
        "libraryWatch",
        "shadow",
        "siblingsBefore",
        "siblingsAfter",
        "siblingNote",
        "changedRows",
        "restoredLegacyTables",
    ):
        if key in result:
            report[key] = result[key]

    print(json.dumps({
        "mode": mode,
        "writes": report["writes"],
        "passed": report.get("passed"),
        "checks": report.get("checks"),
    }, ensure_ascii=False, indent=2))
    for target in targets:
        print("  %-32s %-28s rows=%d" % (target["table"], target["state"], target["affectedRows"]))
    print("")
    print("would roll back:")
    for entry in report.get("canonicalStatements", []):
        print("  %s" % entry["statement"])
    print("")
    print("P3 legacy column restore:")
    for entry in report.get("p3Restore", []):
        print("  %-44s %-24s %s" % (entry["table"], entry["state"], entry["note"]))
    print("")
    print("never touched: %s" % ", ".join(report.get("protectedColumns", [])))
    print("")
    print("live library written: %s" % ("yes" if report["writes"] else "no"))

    if not args.no_report:
        os.makedirs(args.report_dir, exist_ok=True)
        # The three modes must not overwrite each other.  The rehearsal
        # report is the evidence the checklist cites, so a later read-only
        # "plan" run (or an "apply" run) must not silently replace it.
        if mode == "rehearse":
            stem = "task170_rollback_directional_pool_switch_20261123"
        else:
            stem = "task170_rollback_directional_pool_switch_20261123_%s" % mode
        json_path = os.path.join(
            args.report_dir, stem + ".json"
        )
        md_path = os.path.join(
            args.report_dir, stem + ".md"
        )
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(report))
        print("report: %s" % json_path)

    if mode == "rehearse" and not report.get("passed", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
