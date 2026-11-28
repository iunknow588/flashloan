#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read which live closed loops have a resident review worker.

The healer asks the runtime for a review of a route.  The runtime answers
with one of three things: the review, closed_loop_group_busy (the worker
exists and is running) or closed_loop_group_not_found (the worker pool
never registered that group key).

The third answer can only be answered by the runtime: the worker has to be
registered under the exact ordered route of the loop.  This probe reads
the two lists and compares them, so the gap is named instead of guessed:

* the live loops the closed-loop Manager publishes (the routes that exist
  and are traded right now), and
* the resident workers the runtime scanner reports (the groups that can be
  reviewed right now).

Nothing is written and nothing is asked of the runtime beyond the two
reads.

Usage:
    python probe_worker_coverage_20261128.py [--manager-api URL] [--runtime-api URL]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
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


def group_key_of(symbols: list[str]) -> str:
    """Return the group key the worker pool keys a route by."""
    return "|".join(str(symbol).strip().lower() for symbol in (symbols or [])[:3])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager-api", default="http://127.0.0.1:8091")
    parser.add_argument("--runtime-api", default="http://127.0.0.1:5000")
    args = parser.parse_args()

    healer = load_healer()
    loops = healer.manager_read_closed_loops(args.manager_api).get("loops") or []
    registered = healer.registered_closed_loop_group_keys(args.runtime_api)
    registered_keys = {str(key).strip().lower() for key in registered}

    rows = []
    covered = 0
    missing = []
    for loop in loops:
        symbols = loop.get("routeSymbols") or loop.get("pathSymbols") or []
        key = group_key_of(list(symbols))
        has_worker = key in registered_keys
        covered += 1 if has_worker else 0
        if not has_worker:
            missing.append(key)
        rows.append({
            "loopKey": loop.get("loopKey") or loop.get("loop_key"),
            "groupKey": key,
            "path": loop.get("pathText") or loop.get("path_text"),
            "residentWorker": has_worker,
        })

    print(json.dumps({
        "liveLoops": len(loops),
        "registeredWorkers": len(registered_keys),
        "covered": covered,
        "withoutResidentWorker": len(missing),
        "missingGroupKeys": sorted(set(missing)),
        "registeredKeysSample": sorted(registered_keys)[:12],
        "rows": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
