#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Time the three materialization stages with a **dry run**.

``composite-manager materialize-arbitrage`` without ``--apply`` opens the
store read-only and computes the rows each stage would write.  Timing that
run measures the cost of the candidate-edge, candidate-exchange and
closed-loop materialization without touching the live store.

The report also records **what** the dry run would have written, which is
how a stale mirror shows up as a non-zero ``changedRows``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
BINARY = REPO / "flashloan" / "tools" / "target" / "release" / "composite-manager.exe"
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def run_once(database_path: Path) -> tuple[float, dict]:
    started = time.perf_counter()
    result = subprocess.run(
        [str(BINARY), "materialize-arbitrage", "--db", str(database_path),
         "--chain-id", "4663"],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        raise SystemExit(f"the dry run failed: {result.stderr.strip()}")
    return elapsed, json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    database_path = Path(arguments.db)

    timings = []
    report = None
    for index in range(arguments.runs):
        elapsed, report = run_once(database_path)
        timings.append(round(elapsed, 3))

    stages = {}
    for key in ("candidateEdges", "candidateExchanges", "closedLoops"):
        section = (report or {}).get(key) or {}
        stages[key] = {
            field: section.get(field)
            for field in ("apply", "dry_run", "scope_key", "changedRows",
                          "consumersNeedRebuild", "candidateVersion",
                          "closedLoopVersion", "loopCount", "hopCount",
                          "optimizationCount", "skippedTokenCount",
                          "poolCount", "directionCount", "materializedChecksum")
            if field in section
        }

    result = {
        "schema": "materialization-timing-v1",
        "generatedAt": datetime.datetime.now().astimezone().isoformat(),
        "mode": "dry-run (the store is opened read-only; nothing is written)",
        "binary": str(BINARY).replace("\\", "/"),
        "database": str(database_path).replace("\\", "/"),
        "runs": arguments.runs,
        "elapsedSeconds": {
            "samples": timings,
            "minSeconds": min(timings),
            "maxSeconds": max(timings),
            "note": (
                "the first run after a restart pays for a cold page cache;"
                " later runs reuse it"
            ),
        },
        "stages": stages,
        "poolScopePreflightCount": (report or {}).get("poolScopePreflightCount"),
        "applied": (report or {}).get("applied"),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.out:
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print("wrote:", target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
