#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect the scanner's own observation snapshot.

The scanner turns the observation into routes.  The closed-loop Manager turns
the same database into the published catalog.  This probe prints the
observation's own counts so the two can be compared.

Read-only.

Usage:
    python probe_t401_observation_snapshot_20261124.py [snapshot.json]
"""

import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT = (
    r"E:\2026OPC大赛\flashLoan\flashloan\src\dex\runtime\cache"
    r"\dex_opportunity_snapshot.json"
)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    with open(path, "r", encoding="utf-8") as handle:
        snapshot = json.load(handle)

    print("top keys:", json.dumps(sorted(snapshot.keys()), ensure_ascii=False))
    summary = snapshot.get("summary") or {}
    print("summary:", json.dumps(
        {key: summary.get(key) for key in sorted(summary)}, ensure_ascii=False))

    groups = snapshot.get("candidateObservationGroups") or []
    print("candidateObservationGroups:", len(groups))
    if groups:
        print("group keys:", json.dumps(sorted(groups[0].keys()), ensure_ascii=False))

    routes = snapshot.get("closedLoopRoutes") or []
    print("closedLoopRoutes:", len(routes))
    if routes:
        print("route keys:", json.dumps(sorted(routes[0].keys()), ensure_ascii=False))

    pairs = snapshot.get("pairs") or []
    print("pairs:", len(pairs))
    if pairs:
        print("pair keys:", json.dumps(sorted(pairs[0].keys()), ensure_ascii=False))
        states = Counter(
            str((pair.get("observationState") if isinstance(pair, dict) else None))
            for pair in pairs
        )
        print("pair observationState:", json.dumps(states, ensure_ascii=False))
        reachable = Counter(
            str((pair.get("observationReachable") if isinstance(pair, dict) else None))
            for pair in pairs
        )
        print("pair observationReachable:", json.dumps(reachable, ensure_ascii=False))
        empty = Counter(
            str((pair.get("observationEmptyReason") if isinstance(pair, dict) else None))
            for pair in pairs
        )
        print("pair observationEmptyReason:", json.dumps(empty, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
