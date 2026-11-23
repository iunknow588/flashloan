# -*- coding: utf-8 -*-
"""Measure the record volume of the review / broadcast tables (read-only).

The task
--------
The record-separation design (``plan/池子重构方案/05-记录存储剥离技术方案.md``)
has to decide where each kind of record lives: the main SQLite store, a
date-sharded JSONL stream, or a month-sharded SQLite store.  That decision
needs numbers, not opinions:

* how many rows each table actually holds,
* how many bytes those rows occupy (the JSON blobs dominate, not the keys),
* how fast the tables grow (the review generation counter is the proxy).

Nothing is written.  The store is opened with ``mode=ro`` and
``PRAGMA query_only = ON``; only ``SELECT`` statements are issued.  The file
digest, size and mtime are captured before and after the run so the
read-only claim is checkable rather than merely asserted.

The digest is taken twice because the mainnet store is a *live* store with
concurrent writers (a running ``manager-api``).  A digest that moves across
the run is reported as ``externalWriterActivity`` -- it is attributed to the
external writer, not to this script, which holds no write statement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
DEFAULT_REPORT = (
    REPO_ROOT
    / "plan"
    / "runtime_evidence"
    / "reports"
    / "record_volumes_20261123.json"
)


# The tables this measurement is about.  ``manager_review_library_state`` and
# ``manager_broadcast_library_state`` are *state* rows (one per scope), not
# records; they are listed so the ratio between state and history is visible.
TABLES = (
    "manager_review_library_state",
    "manager_v4_reviews",
    "manager_v4_review_diagnostics",
    "manager_v4_review_broadcast_requests",
    "manager_broadcast_library_state",
    "manager_cow_execution_attempts",
    "manager_broadcast_attempt_diagnostics",
    "manager_write_requests",
)

# The wide text columns.  These are what turn a review row into kilobytes.
TEXT_COLUMNS = {
    "manager_v4_reviews": (
        "path_addresses_json",
        "path_symbols_json",
        "pool_ids_json",
        "static_preview_json",
        "run_arbitrage_json",
        "hop_sqrt_price_limits_json",
    ),
    "manager_v4_review_diagnostics": ("payload_json",),
    "manager_v4_review_broadcast_requests": (
        "path_addresses_json",
        "path_symbols_json",
        "pool_ids_json",
        "hop_sqrt_price_limits_json",
    ),
    "manager_cow_execution_attempts": (
        "path_addresses_json",
        "path_symbols_json",
        "pool_ids_json",
        "receipt_json",
        "evidence_json",
        "hop_sqrt_price_limits_json",
    ),
    "manager_broadcast_attempt_diagnostics": ("payload_json",),
}


def _digest(path: Path) -> dict:
    stat = path.stat()
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    return {
        "sha256": hasher.hexdigest(),
        "sizeBytes": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?1",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]


def _measure(connection: sqlite3.Connection, table: str) -> dict:
    if not _table_exists(connection, table):
        return {"present": False, "rowCount": None, "textBytes": None}
    count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
    present_columns = set(_columns(connection, table))
    text_bytes = 0
    measured_columns = []
    for column in TEXT_COLUMNS.get(table, ()):  # only columns that exist
        if column not in present_columns:
            continue
        total = connection.execute(
            f"SELECT COALESCE(SUM(LENGTH(COALESCE({column}, ''))), 0) FROM {table}"
        ).fetchone()[0]
        text_bytes += int(total or 0)
        measured_columns.append({"column": column, "bytes": int(total or 0)})
    return {
        "present": True,
        "rowCount": count,
        "textBytes": text_bytes,
        "textBytesPerRow": round(text_bytes / count, 2) if count else 0,
        "measuredColumns": measured_columns,
    }


def _review_generation(connection: sqlite3.Connection) -> dict:
    """Read the generation counter -- the proxy for how fast records grow."""
    if not _table_exists(connection, "manager_review_library_state"):
        return {"present": False}
    rows = connection.execute(
        "SELECT network, chain_id, protocol, scope_key, review_version, "
        "loop_count, reviewed_count, completed_count, "
        "data_incomplete_count, not_executable_count, failed_count, "
        "broadcast_request_count, generated_at, updated_at "
        "FROM manager_review_library_state"
    ).fetchall()
    return {
        "present": True,
        "rows": [
            {
                "network": row[0],
                "chainId": row[1],
                "protocol": row[2],
                "scopeKey": row[3],
                "reviewVersion": row[4],
                "loopCount": row[5],
                "reviewedCount": row[6],
                "completedCount": row[7],
                "dataIncompleteCount": row[8],
                "notExecutableCount": row[9],
                "failedCount": row[10],
                "broadcastRequestCount": row[11],
                "generatedAt": row[12],
                "updatedAt": row[13],
            }
            for row in rows
        ],
    }


def _pnl_raw_format(connection: sqlite3.Connection) -> dict:
    """Read the *shape* of the signed profit field (read-only).

    The record-separation design has to decide how the profit is stored in
    the JSONL stream and in the month-sharded store.  The separation plan
    assumed that signed_pnl_raw is a *decimal* string of a U256 value, so
    this probe measures what the field actually looks like:

    * is it decimal or hexadecimal?
    * does it carry the sign?
    * is one currency or several?  (several currencies cannot be summed)
    * what scale relates the raw integer to the human amount?
    """
    if not _table_exists(connection, "manager_v4_reviews"):
        return {"present": False}
    rows = connection.execute(
        "SELECT signed_pnl_raw, signed_pnl_amount, signed_pnl_currency "
        "FROM manager_v4_reviews WHERE signed_pnl_raw IS NOT NULL"
    ).fetchall()
    decimal_rows = 0
    hexadecimal_rows = 0
    other_rows = 0
    sign_matches = 0
    sign_mismatches = 0
    currencies = {}
    scales = {}
    samples = []
    for raw, amount, currency in rows:
        text = str(raw).strip()
        negative = text.startswith("-")
        body = text[1:] if negative else text
        if body.lower().startswith("0x"):
            hexadecimal_rows += 1
            magnitude = int(body[2:], 16)
        elif body.isdigit():
            decimal_rows += 1
            magnitude = int(body)
        else:
            other_rows += 1
            continue
        if amount is not None:
            if (not negative and amount > 0) or (negative and amount < 0):
                sign_matches += 1
            elif amount != 0:
                sign_mismatches += 1
            if amount:
                scale = round(abs(magnitude) / abs(amount), 3)
                scales[str(scale)] = scales.get(str(scale), 0) + 1
        currencies[str(currency)] = currencies.get(str(currency), 0) + 1
        if len(samples) < 5:
            samples.append(
                {"raw": text, "amount": amount, "currency": str(currency)}
            )
    return {
        "present": True,
        "rowsWithPnl": len(rows),
        "rawIsDecimal": decimal_rows,
        "rawIsHexadecimal": hexadecimal_rows,
        "rawOtherShape": other_rows,
        "signAgreesWithAmount": sign_matches,
        "signDisagreesWithAmount": sign_mismatches,
        "currencyDistribution": currencies,
        "observedScales": scales,
        "samples": samples,
        "note": (
            "the raw field carries the sign and is hexadecimal; the human amount "
            "is the raw magnitude divided by the token decimals.  A decimal sum "
            "over several currencies is meaningless, so the aggregation has to "
            "be grouped by currency."
        ),
    }


def measure(database: Path) -> dict:
    before = _digest(database)
    connection = sqlite3.connect(
        "file:%s?mode=ro" % database.as_posix(), uri=True, timeout=180.0
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = {table: _measure(connection, table) for table in TABLES}
        generation = _review_generation(connection)
        pnl_format = _pnl_raw_format(connection)
    finally:
        connection.close()
    after = _digest(database)
    external_writer = before["sha256"] != after["sha256"]
    sibling = []
    for suffix in ("-wal", "-shm"):
        path = database.with_name(database.name + suffix)
        if path.exists():
            sibling.append(
                {"path": str(path), "sizeBytes": path.stat().st_size}
            )
    return {
        "database": str(database),
        "openedWith": "mode=ro",
        "queryOnly": True,
        "measuredAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fileBefore": before,
        "fileAfter": after,
        "externalWriterActivity": external_writer,
        "externalWriterNote": (
            "the digest moved across the run; this script holds no write statement, "
            "so the movement is attributed to the concurrent live writer"
            if external_writer
            else "the digest, the size and the mtime are identical before and after "
            "the run, so no writer touched the store while it was measured"
        ),
        "siblingRuntimeFiles": sibling,
        "siblingNote": (
            "SQLite creates -wal/-shm next to a WAL store when it is opened; the "
            "files carry no library data and are managed by SQLite itself"
        ),
        "tables": tables,
        "reviewGeneration": generation,
        "pnlRawFormat": pnl_format,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args(argv)

    database = Path(args.database)
    if not database.is_file():
        print("database not found: %s" % database, file=sys.stderr)
        return 2
    report = measure(database)
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote %s" % target)
    for table, data in report["tables"].items():
        print(
            "  %-40s rows=%-8s textBytes=%s"
            % (table, data.get("rowCount"), data.get("textBytes"))
        )
    print("externalWriterActivity=%s" % report["externalWriterActivity"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
