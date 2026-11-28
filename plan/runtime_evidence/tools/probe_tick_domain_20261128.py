#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the searched tick domain with the domain that was left out.

The kernel measures a route with two searches:

* the **single tick** search starts at the amount the single-tick capacity
  produces and searches inside the folded single-tick capacity;
* the **cross tick** search continues from the single-tick optimum and is
  bounded by the folded **depth** capacity instead.

The cross-tick search is only started when the single-tick search found a
profitable size.  A route where every single-tick size is unprofitable
therefore never reaches the larger domain, and the negative result only
covers the domain that was searched.

This probe reads both ceilings from the closed-loop catalog and the measured
ladder from the review library, so the size of the domain that was **not**
searched is a number instead of an assumption.

Usage:
    python probe_tick_domain_20261128.py [--manager-api URL] [--json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
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


def raw_to_amount(raw: str | None, decimals: int | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = int(text, 16) if text.startswith(("0x", "0X")) else int(text)
    except ValueError:
        return None
    if decimals is None:
        return float(value)
    return value / (10 ** int(decimals))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manager-api", default="http://127.0.0.1:8091")
    parser.add_argument("--hours", type=int, default=6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    healer = load_healer()
    db = healer.assert_chain_database(
        REPO / "flashloan/db" / healer.CHAIN_DATABASES[healer.CHAIN_ID], healer.CHAIN_ID)

    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=60000")
    loops = {}
    for row in connection.execute(
            "SELECT loop_key, anchor_symbol, single_tick_capacity_anchor_raw, "
            "cross_tick_capacity_anchor_raw, borrow_token_decimals, "
            "cross_tick_review_threshold_usdg_raw "
            "FROM manager_v4_closed_loops "
            "WHERE network = 'robinhood' AND chain_id = 4663 AND protocol = 'uniswap_v4' "
            "AND scope_key = 'core4'"):
        loops[row[0]] = {
            "anchorSymbol": row[1],
            "singleTickCapacityRaw": row[2],
            "crossTickCapacityRaw": row[3],
            "borrowTokenDecimals": row[4],
            "crossTickThresholdUsdgRaw": row[5],
        }

    rows = healer.review_rows(db, args.hours, manager_api=args.manager_api)
    known_loops = {
        str(row.get("loopKey") or "")
        for row in healer.manager_read_closed_loops(args.manager_api).get("loops") or []
        if str(row.get("loopKey") or "")
    }

    report = []
    for row in rows:
        loop_key = healer.review_loop_key(row)
        if loop_key not in known_loops:
            continue
        loop = loops.get(loop_key) or {}
        decimals = loop.get("borrowTokenDecimals")
        calculation = healer._best_amount_calculation(healer.review_payloads(row))
        initial_raw = calculation.get("initialAmountRaw") or calculation.get("initialProbeAmount")
        best_raw = calculation.get("bestAmountRaw") or calculation.get("bestAmount")
        probes = calculation.get("probeRecords") or []
        probed = [raw_to_amount(item.get("amountRaw"), decimals) for item in probes]
        probed = [value for value in probed if value is not None]
        single_tick = raw_to_amount(loop.get("singleTickCapacityRaw"), decimals)
        cross_tick = raw_to_amount(loop.get("crossTickCapacityRaw"), decimals)
        initial = raw_to_amount(initial_raw, decimals)
        best = raw_to_amount(best_raw, decimals)
        report.append({
            "loopKey": loop_key,
            "anchorSymbol": loop.get("anchorSymbol"),
            "status": calculation.get("bestAmountLadderStatus"),
            "initialAmountRaw": initial_raw,
            "initialAmount": initial,
            "bestAmountRaw": best_raw,
            "bestAmount": best,
            "bestGreaterThanInitial": (None if best is None or initial is None
                                        else best > initial),
            "probeCount": len(probes),
            "largestProbedAmount": max(probed) if probed else None,
            "smallestProbedAmount": min(probed) if probed else None,
            "singleTickCapacityRaw": loop.get("singleTickCapacityRaw"),
            "singleTickCapacity": single_tick,
            "crossTickCapacityRaw": loop.get("crossTickCapacityRaw"),
            "crossTickCapacity": cross_tick,
            "crossTickCapacityOverSingleTick": (None if single_tick is None or cross_tick is None
                                                else cross_tick / single_tick
                                                if single_tick else None),
            "crossTickThresholdUsdgRaw": loop.get("crossTickThresholdUsdgRaw"),
            "pnl": healer.pnl(row),
        })

    negatives = [item for item in report
                 if item["pnl"] is not None and float(item["pnl"]["amount"]) <= 0]
    summary = {
        "currentLoopReviews": len(report),
        "nonPositiveReviews": len(negatives),
        "bestGreaterThanInitial": sum(1 for item in negatives
                                      if item["bestGreaterThanInitial"] is True),
        "bestNotGreaterThanInitial": sum(1 for item in negatives
                                         if item["bestGreaterThanInitial"] is False),
        "crossTickLargerThanSingleTick": sum(1 for item in report
                                              if item["crossTickCapacityOverSingleTick"]
                                              not in (None,)
                                              and item["crossTickCapacityOverSingleTick"] > 1.0),
        "rows": report,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"current-loop reviews      : {summary['currentLoopReviews']}")
        print(f"non-positive reviews      : {summary['nonPositiveReviews']}")
        print(f"best > initial (tick)     : {summary['bestGreaterThanInitial']}")
        print(f"best <= initial (tick)    : {summary['bestNotGreaterThanInitial']}")
        print(f"cross-tick ceiling larger : {summary['crossTickLargerThanSingleTick']}")
        for item in report:
            print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
