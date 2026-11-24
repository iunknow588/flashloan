#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the resident worker groups with the scanner's route groups.

A group can only become a resident worker when the scanner produces a route
for it.  If the scanner's route set is exactly the set of groups that
already hold a worker, the remaining catalog groups can never get their
first worker, and the review generation can never cover the catalog.

Read-only.

Usage:
    python probe_t401_resident_vs_routes_20261124.py [runtime_base]
"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")


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
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
    snapshot = get_json(base.rstrip("/") + "/api/dex/opportunity-scanner/snapshot")
    scanner = snapshot.get("scanner") or {}
    workers = scanner.get("closed_loop_rust_workers") or {}

    resident = set()
    for worker in workers.get("workers") or []:
        if not isinstance(worker, dict):
            continue
        if worker.get("thread_live") is False:
            continue
        key = str(worker.get("group_key") or "").strip().lower()
        if key:
            resident.add(key)

    payload = (snapshot.get("snapshot") or {}).get("payload") or {}
    routes = payload.get("routes") or []
    route_groups = set()
    for route in routes:
        key = compact(
            route.get("routeSymbols")
            if isinstance(route.get("routeSymbols"), list)
            else route.get("symbols")
        )
        if key:
            route_groups.add(key)

    report = {
        "residentWorkerGroups": len(resident),
        "reportedResidentWorkers": workers.get("residentWorkers"),
        "scannerRouteGroups": len(route_groups),
        "scannerRoutes": len(routes),
        "routesOnly": sorted(route_groups - resident)[:20],
        "routesOnlyCount": len(route_groups - resident),
        "residentOnly": sorted(resident - route_groups)[:20],
        "residentOnlyCount": len(resident - route_groups),
        "identical": route_groups == resident,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
