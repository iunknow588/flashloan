#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Show which live closed loops carry a review of an older generation.

The Review Manager stamps every review it materializes with the upstream
generation it was measured under: the closed-loop version and checksum, the
candidate-edge version and checksum, the observation version and checksum
and the pool-scope checksum.  A review of a loop that still exists but
carries an **older** generation describes a route that was measured before
the current library was materialized, so it cannot prove the route is
unprofitable **now**.

The healer matches a review to a live loop by its loop key alone.  This
probe adds the generation to the match, so the reviews that only look
current are separated from the ones that are.

Usage:
    python probe_review_generation_20261128.py [--manager-api URL] [--json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

sys.stdout.reconfigure(encoding="utf-8")


def load_healer():
    path = REPO / "scripts" / "mainnet_arbitrage_healer.py"
    spec = importlib.util.spec_from_file_location("mainnet_arbitrage_healer", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manager-api", default="http://127.0.0.1:8091")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    healer = load_healer()
    closed = healer.manager_read_closed_loops(args.manager_api)
    status = closed.get("status") or {}
    generation = {
        "closedLoopVersion": status.get("closedLoopVersion"),
        "closedLoopChecksum": status.get("materializedChecksum"),
        "candidateEdgeVersion": status.get("candidateEdgeVersion"),
        "candidateEdgeChecksum": status.get("candidateEdgeChecksum"),
        "observationVersion": status.get("observationVersion"),
        "observationChecksum": status.get("observationChecksum"),
        "poolScopeChecksum": status.get("poolScopeChecksum"),
    }
    live = {str(row.get("loopKey") or "")
            for row in closed.get("loops") or []
            if str(row.get("loopKey") or "")}
    rows = healer.manager_read_reviews(args.manager_api)

    histogram: Counter = Counter()
    per_loop: dict[str, dict] = {}
    for row in rows:
        loop_key = str(row.get("loopKey") or "")
        if loop_key not in live:
            continue
        state = healer.review_generation_status(row, generation)
        histogram[state["state"]] += 1
        per_loop[loop_key] = {
            "loopKey": loop_key,
            "anchorSymbol": row.get("anchorSymbol"),
            "pathText": row.get("pathText"),
            "reviewedAt": row.get("reviewedAt"),
            "reviewStatus": row.get("reviewStatus"),
            "state": state["state"],
            "mismatches": state["mismatches"],
            "missing": state["missing"],
        }

    covered = {key for key, item in per_loop.items() if item["state"] == "current"}
    report = {
        "generation": generation,
        "liveLoopCount": len(live),
        "reviewRowCount": len(rows),
        "liveLoopRows": len(per_loop),
        "states": dict(histogram),
        "liveLoopsWithoutCurrentReview": sorted(live - covered),
        "rows": sorted(per_loop.values(), key=lambda item: (item["state"], item["pathText"] or "")),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"live loops               : {report['liveLoopCount']}")
    print(f"live-loop review rows    : {report['liveLoopRows']}")
    print(f"states                   : {report['states']}")
    print(f"loops without current rev: {len(report['liveLoopsWithoutCurrentReview'])}")
    for item in report["rows"]:
        if item["state"] == "current":
            continue
        print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
