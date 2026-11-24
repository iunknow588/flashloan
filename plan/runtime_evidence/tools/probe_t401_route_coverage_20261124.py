#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the overview's arbitrage paths with the scanner's own route list.

The review publisher needs one resident worker per closed-loop group of the
catalog.  A group only gets a worker when the scanner produces a route for
it, so the difference between the overview and the scanner payload is the
set of groups that can never reach a review.

Read-only.

Usage:
    python probe_t401_route_coverage_20261124.py <store.db> [runtime_base]
"""

import json
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


def group_key(route):
    symbols = (
        route.get("routeSymbols")
        if isinstance(route.get("routeSymbols"), list)
        else route.get("symbols")
    )
    return compact(symbols)


def main():
    store_path = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5000"

    overview = get_json(base.rstrip("/") + "/api/dex/overview")
    data = overview.get("data") or overview
    paths = data.get("arbitragePaths") or data.get("arbitrage_paths") or []
    overview_groups = {}
    for path in paths:
        key = group_key(path)
        if key:
            overview_groups.setdefault(key, []).append(path)

    snapshot = get_json(base.rstrip("/") + "/api/dex/opportunity-scanner/snapshot")
    payload = (snapshot.get("snapshot") or {}).get("payload") or {}
    routes = payload.get("routes") or []
    scanner_groups = {}
    for route in routes:
        key = group_key(route)
        if key:
            scanner_groups.setdefault(key, []).append(route)

    connection = sqlite3.connect(
        "file:" + store_path.replace("\\", "/") + "?mode=ro",
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

    missing = sorted(set(catalog_groups) - set(scanner_groups))
    report = {
        "overviewPaths": len(paths),
        "overviewGroups": len(overview_groups),
        "scannerRoutes": len(routes),
        "scannerGroups": len(scanner_groups),
        "catalogLoops": len(catalog),
        "catalogGroups": len(catalog_groups),
        "catalogGroupsWithoutScannerRoute": len(missing),
        "catalogGroupsWithoutScannerRouteSample": missing[:20],
        "overviewGroupsWithoutScannerRoute": len(set(overview_groups) - set(scanner_groups)),
        "scannerGroupsNotInCatalog": len(set(scanner_groups) - set(catalog_groups)),
        "scannerGroupsNotInCatalogSample": sorted(set(scanner_groups) - set(catalog_groups))[:20],
        "catalogGroupsWithScannerRoute": len(set(catalog_groups) & set(scanner_groups)),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # How many catalog loops are covered by the scanner's current routes?
    covered_loops = set()
    for key in set(catalog_groups) & set(scanner_groups):
        covered_loops.update(catalog_groups[key])
    report["catalogLoopsWithScannerRoute"] = len(covered_loops)
    report["catalogLoopsWithoutScannerRoute"] = len(catalog) - len(covered_loops)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
