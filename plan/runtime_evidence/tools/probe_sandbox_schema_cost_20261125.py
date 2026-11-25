#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure how long it takes to materialise a full sandbox store.

The test suite needs an implicit default store that carries the same schema
as the live chain store, otherwise a read of a business table would fail
with "no such table" instead of returning an empty result.  This probe
reports the wall clock cost of that materialisation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(1, str(REPO_ROOT / "flashloan"))
    sys.path.insert(2, str(REPO_ROOT / "flashloan" / "src"))

    from core.db.storage_schema import ensure_database_schema

    directory = tempfile.mkdtemp(prefix="sandbox_schema_cost_")
    sandbox = Path(directory) / "local_store_sandbox.sqlite3"
    started = time.monotonic()
    ensure_database_schema(str(sandbox))
    elapsed = time.monotonic() - started
    size = sandbox.stat().st_size if sandbox.exists() else -1
    report = {
        "sandbox": str(sandbox),
        "seconds": round(elapsed, 3),
        "sizeBytes": size,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
