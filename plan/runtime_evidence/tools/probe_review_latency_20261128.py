#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure how long the runtime takes to answer a review request.

The healer asks the runtime for a review and gives up after 120 seconds.
The runtime may well need longer than that, and then the healer never
sees the answer: it cannot tell a busy worker from a missing one, so it
never runs the worker refresh that a missing worker needs.

This probe asks the runtime for a review of the live loops that carry a
review of an **older generation** -- the loops the healer cannot measure
right now -- and records how long the answer takes and what it says.

Nothing is written to the store.  The request is the same request the
healer sends in every cycle.

Usage:
    python probe_review_latency_20261128.py [--limit N] [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

sys.stdout.reconfigure(encoding="utf-8")

DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def load_healer():
    path = REPO / "scripts" / "mainnet_arbitrage_healer.py"
    spec = importlib.util.spec_from_file_location("mainnet_arbitrage_healer", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def post(base: str, path: str, body: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {"ok": 200 <= response.status < 300, "status": response.status,
                    "elapsedSeconds": round(time.monotonic() - started, 3),
                    "body": raw[:400]}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": error.code,
                "elapsedSeconds": round(time.monotonic() - started, 3),
                "body": raw[:400]}
    except Exception as error:  # noqa: BLE001 - the probe reports every failure
        return {"ok": False, "status": None,
                "elapsedSeconds": round(time.monotonic() - started, 3),
                "body": str(error)[:400]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager-api", default="http://127.0.0.1:8091")
    parser.add_argument("--runtime-api", default="http://127.0.0.1:5000")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    healer = load_healer()
    candidates = healer.stale_generation_review_candidates(
        DB, args.limit, None, args.manager_api)
    results = []
    for candidate in candidates:
        answer = post(
            args.runtime_api,
            f"/api/dex/closed-loop-groups/manual-review?chain_id={healer.CHAIN_ID}",
            {"routeSymbols": candidate["routeSymbols"],
             "routeAddresses": candidate["routeAddresses"],
             "mode": "deep", "dryRun": True},
            args.timeout)
        results.append({"path": candidate.get("path"), "answer": answer})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    print(json.dumps({"summary": [
        {"path": item["path"], "status": item["answer"]["status"],
         "elapsedSeconds": item["answer"]["elapsedSeconds"],
         "body": item["answer"]["body"][:200]} for item in results]},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
