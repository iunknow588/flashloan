#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Count the directed worker groups the live scanner actually discovers.

The review publisher needs one resident worker per directed group of the
closed-loop catalog.  This probe reads the scanner's own view of the world
(the overview paths) and compares it with the catalog in the store, so the
capacity that is actually needed can be read instead of assumed.

Read-only.

Usage:
    python probe_t401_scan_groups_20261124.py <store.db> [runtime_base]
"""

import json
import os
import sqlite3
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"


def get_json(url):
    with urllib.request.urlopen(url, timeout=180) as handle:
        return json.loads(handle.read().decode("utf-8", errors="replace"))


def compact(symbols):
    parts = [
        str(item or "").strip().lower()
        for item in (symbols or [])
        if str(item or "").strip()
    ]
    if len(parts) == 4 and parts[0] == parts[-1]:
        parts = parts[:3]
    if len(parts) != 3 or len(set(parts)) != 3:
        return None
    return "|".join(parts)


def main():
    store_path = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5000"

    overview = get_json(base.rstrip("/") + "/api/dex/overview")
    data = overview.get("data") or overview
    paths = data.get("arbitragePaths") or data.get("arbitrage_paths") or []

    scan_groups = {}
    for path in paths:
        if not isinstance(path, dict):
            continue
        symbols = path.get("routeSymbols") or path.get("route_symbols") or path.get("symbols")
        key = compact(symbols)
        if key:
            scan_groups.setdefault(key, []).append(path)

    connection = sqlite3.connect(
        "file:" + os.path.abspath(store_path).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    catalog = list(
        connection.execute(
            "SELECT loop_key, path_symbols_json FROM manager_v4_closed_loops"
            " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",
            (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
        )
    )
    connection.close()

    catalog_groups = {}
    for row in catalog:
        try:
            symbols = json.loads(row["path_symbols_json"] or "[]")
        except (TypeError, ValueError):
            symbols = []
        key = compact(symbols)
        if key:
            catalog_groups.setdefault(key, []).append(row["loop_key"])

    report = {
        "overviewPaths": len(paths),
        "scanDistinctGroups": len(scan_groups),
        "catalogLoops": len(catalog),
        "catalogDistinctGroups": len(catalog_groups),
        "scanGroupsNotInCatalog": sorted(set(scan_groups) - set(catalog_groups))[:20],
        "catalogGroupsNotScanned": len(set(catalog_groups) - set(scan_groups)),
        "catalogGroupsNotScannedSample": sorted(set(catalog_groups) - set(scan_groups))[:20],
        "scanCoversCatalog": set(scan_groups) >= set(catalog_groups),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
