#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check which read-only URI form opens the live chain store reliably.

The live mainnet store lives under a path that contains non-ASCII characters,
so a read-only connection has to survive the percent-encoding of the path.
This probe opens the store with both URI forms and reports which one works,
then proves that a write is rejected inside SQLite.

The probe never issues a statement that could modify the database: the
connection is opened with the read-only mode, so the attempted write is
rejected by SQLite itself.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_DB = (
    Path(__file__).resolve().parents[3]
    / "flashloan"
    / "db"
    / "local_store_4663.sqlite3"
)


def _open(uri: str) -> sqlite3.Connection:
    connection = sqlite3.connect(uri, uri=True, timeout=15.0)
    connection.row_factory = sqlite3.Row
    return connection


def _probe(label: str, uri: str) -> dict:
    report = {"label": label, "uri": uri, "opened": False}
    try:
        connection = _open(uri)
    except sqlite3.Error as exc:
        report["openError"] = f"{type(exc).__name__}: {exc}"
        return report
    try:
        report["opened"] = True
        report["kvMetaRows"] = int(
            connection.execute("SELECT COUNT(*) FROM kv_meta").fetchone()[0]
        )
        report["reviewRows"] = int(
            connection.execute("SELECT COUNT(*) FROM cow_execution_reviews").fetchone()[0]
        )
        try:
            connection.execute(
                "INSERT INTO kv_meta (meta_key, value_json) VALUES ('probe', 'probe')"
            )
        except sqlite3.Error as exc:
            report["writeError"] = f"{type(exc).__name__}: {exc}"
        else:
            report["writeError"] = None
    finally:
        connection.close()
    return report


def main() -> int:
    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB).resolve()
    absolute = os.path.abspath(str(db_path)).replace("\\", "/")
    reports = [
        _probe("raw-forward-slash", "file:" + absolute + "?mode=ro"),
        _probe("pathlib-as-uri", db_path.as_uri() + "?mode=ro"),
    ]
    print(
        json.dumps(
            {"database": str(db_path), "results": reports},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
