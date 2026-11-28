#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""List the current-loop reviews that carry no concrete best trade amount.

The production verdict of the healer turns red with
``review_best_amount_missing``.  This probe answers the question the
verdict does not: **which** reviews are the failing ones, what the review
manager recorded as their failure, and whether the loop they belong to is
still live.

The healer module is imported, so the probe reads the reviews with the very
same functions the verdict uses (``review_rows``, ``best_amount_state``,
``review_loop_key``).  Nothing is recomputed here.

Usage:
    python probe_review_best_amount_20261128.py [--manager-api URL]
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manager-api", default="http://127.0.0.1:8091")
    parser.add_argument("--hours", type=int, default=6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    healer = load_healer()
    db = healer.assert_chain_database(
        REPO / "flashloan/db" / healer.CHAIN_DATABASES[healer.CHAIN_ID], healer.CHAIN_ID)

    rows = healer.review_rows(db, args.hours, manager_api=args.manager_api)
    known_loops = {
        str(row.get("loopKey") or "")
        for row in healer.manager_read_closed_loops(args.manager_api).get("loops") or []
        if str(row.get("loopKey") or "")
    }

    failures = []
    for row in rows:
        state = healer.best_amount_state(row)
        if not state.get("missing") and state.get("bestAmount") is not None:
            continue
        loop_key = healer.review_loop_key(row)
        failures.append({
            "reviewId": row.get("id"),
            "loopKey": loop_key,
            "currentLoop": loop_key in known_loops if loop_key else False,
            "status": state.get("status"),
            "reviewFailureKind": state.get("reviewFailureKind"),
            "reviewFailureDetail": state.get("reviewFailureDetail"),
            "initialProbeAmount": state.get("initialProbeAmount"),
            "requestedSymbol": state.get("requestedSymbol"),
            "probeCount": state.get("probeCount"),
            "measured": state.get("measured"),
            "confirmed": state.get("confirmed"),
            "reviewStatus": row.get("review_status"),
            "reviewReason": row.get("review_reason"),
            "reviewedAt": healer.review_measurement_stamp(row),
        })

    current = [item for item in failures if item["currentLoop"]]
    # The current-loop reviews that **do** carry an amount are reported with
    # the same detail, so the ladder of the live routes can be read without a
    # second query: the amount, the size of the ladder that produced it and
    # the profit the review published.
    live = []
    for row in rows:
        loop_key = healer.review_loop_key(row)
        if loop_key not in known_loops:
            continue
        state = healer.best_amount_state(row)
        value = healer.pnl(row)
        live.append({
            "reviewId": row.get("id"),
            "loopKey": loop_key,
            "bestAmount": state.get("bestAmount"),
            "bestAmountRaw": state.get("bestAmountRaw"),
            "measured": state.get("measured"),
            "confirmed": state.get("confirmed"),
            "missing": state.get("missing"),
            "status": state.get("status"),
            "probeCount": state.get("probeCount"),
            "ladderIncomplete": state.get("ladderIncomplete"),
            "pnlAmount": None if value is None else value.get("amount"),
            "pnlCurrency": None if value is None else value.get("currency"),
            "reviewedAt": healer.review_measurement_stamp(row),
        })
    payload = {
        "reviewRows": len(rows),
        "knownLoops": len(known_loops),
        "failures": len(failures),
        "currentLoopFailures": len(current),
        "byStatus": {},
        "byFailureKind": {},
        "samples": current[:20],
        "currentLoopReviews": live,
    }
    for item in failures:
        for bucket, key in (("byStatus", item["status"]), ("byFailureKind", item["reviewFailureKind"])):
            name = str(key or "unknown")
            payload[bucket][name] = payload[bucket].get(name, 0) + 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"review rows      : {payload['reviewRows']}")
        print(f"known loops      : {payload['knownLoops']}")
        print(f"missing amount   : {payload['failures']} (current loop {payload['currentLoopFailures']})")
        print(f"by status        : {payload['byStatus']}")
        print(f"by failure kind  : {payload['byFailureKind']}")
        for item in payload["samples"]:
            print(json.dumps(item, ensure_ascii=False))
        print(f"current-loop reviews ({len(live)}):")
        for item in live:
            print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
