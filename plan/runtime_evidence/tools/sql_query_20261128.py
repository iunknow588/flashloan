#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run one read-only query against the manager store.

The investigation needs many small reads of the live store.  This helper
keeps them **read-only** by construction: the store is opened with
``mode=ro`` and ``PRAGMA query_only`` is set before the query runs.

Usage:
    python sql_query_20261128.py --sql "SELECT ..." [--limit 200]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--sql", required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    rows = connection.execute(args.sql).fetchmany(args.limit)
    print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
