#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Do the two sources of *historical positive* reviews agree?

The healer used to find the routes that were profitable before by
scanning the whole legacy record table -- including the payload text of
every row.  That scan is the one that costs minutes, and the record table
is *record* data: the runtime writes it, nothing decides on it.

The Manager library holds the same reviews as in-memory objects: it is
published over the manager API and it carries the loop key and the signed
P&L of every review.  This script asks whether the two sources name the
same candidates, so the swap from the scan to the library can be shown to
keep the answer instead of merely being cheap.

The legacy scan is slow by construction, so it is the reason this script
runs in the background.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mainnet_arbitrage_healer as healer  # noqa: E402


DB = Path("flashloan/db/local_store_4663.sqlite3").resolve()
MANAGER_API = "http://127.0.0.1:8091"


def legacy_candidates(limit: int) -> tuple[list[dict], float]:
    """The candidates the legacy scan names, and how long it took."""
    started = time.monotonic()
    candidates = healer.historical_positive_candidates(DB, limit, None, None)
    return candidates, time.monotonic() - started


def manager_candidates(limit: int) -> tuple[list[dict], float]:
    """The candidates the Manager library names, and how long it took."""
    started = time.monotonic()
    candidates = healer.manager_historical_positive_candidates(
        DB, limit, None, MANAGER_API)
    return candidates, time.monotonic() - started


def main() -> int:
    limit = 5
    report: dict = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    loops = {row["loop_key"] for row in healer.manager_loop_rows(MANAGER_API)}
    report["currentCatalogLoopCount"] = len(loops)

    manager_rows = healer.manager_review_rows(DB, None, MANAGER_API)
    report["managerLibraryRows"] = len(manager_rows)
    report["managerLibraryPositives"] = sum(
        1 for row in manager_rows
        if (value := healer.pnl(row)) is not None and value["amount"] > 0)

    manager_result, manager_seconds = manager_candidates(limit)
    report["managerSeconds"] = round(manager_seconds, 3)
    report["managerCandidates"] = [
        {"loopKey": item["loopKey"], "profit": item["historicalProfit"],
         "currency": item["historicalCurrency"],
         "reviewedAt": item["historicalReviewedAt"]}
        for item in manager_result]

    legacy_result, legacy_seconds = legacy_candidates(limit)
    report["legacySeconds"] = round(legacy_seconds, 3)
    report["legacyCandidates"] = [
        {"loopKey": item["loopKey"], "profit": item["historicalProfit"],
         "currency": item["historicalCurrency"],
         "reviewedAt": item["historicalReviewedAt"]}
        for item in legacy_result]

    manager_keys = {item["loopKey"] for item in manager_result}
    legacy_keys = {item["loopKey"] for item in legacy_result}
    report["sameCandidates"] = manager_keys == legacy_keys
    report["onlyInLegacy"] = sorted(legacy_keys - manager_keys)
    report["onlyInManager"] = sorted(manager_keys - legacy_keys)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
