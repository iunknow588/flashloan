#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Show the size ladder the runtime actually measured for the live routes.

Every current closed loop carries a **measured** best trade amount.  When
that amount is zero the runtime says the route is not profitable at any
probed size.  This probe reads the probe ladder of those routes so the
verdict can be checked against the sizes that were tried: a ladder that only
ever probes dust explains a negative profit with gas, and that is a
different finding from a ladder that measured the whole size space and
still found no arbitrage.

Usage:
    python probe_amount_ladder_20261128.py [--loops N]
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
    parser.add_argument("--loops", type=int, default=6)
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

    shown = 0
    for row in rows:
        loop_key = healer.review_loop_key(row)
        if loop_key not in known_loops:
            continue
        payloads = healer.review_payloads(row)
        calculation = healer._best_amount_calculation(payloads)
        evidence = healer._live_onchain_evidence(payloads)
        probes = healer._probe_rows(evidence) or healer._calculation_probe_rows(calculation)
        value = healer.pnl(row)
        print(f"loop {loop_key}")
        print(f"  reviewedAt {healer.review_measurement_stamp(row)}")
        print(f"  pnl        {None if value is None else value.get('amount')} "
              f"{None if value is None else value.get('currency')}")
        print(f"  status     {calculation.get('bestAmountLadderStatus')}")
        print(f"  bestAmount {calculation.get('bestAmount')} "
              f"measured={calculation.get('bestAmountMeasured')}")
        print(f"  probes     {len(probes)}")
        for probe in probes:
            print(f"    - {json.dumps(probe, ensure_ascii=False)[:600]}")
        if shown == 0:
            print("  payload carriers:")
            for column in ("payload_json", "static_preview_json", "run_arbitrage_json"):
                raw = row.get(column)
                if not raw:
                    continue
                try:
                    import json as _json
                    parsed = _json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    print(f"    {column}: {sorted(parsed.keys())}")
                    if column == "static_preview_json":
                        print("    static_preview:")
                        print(json.dumps({k: v for k, v in parsed.items()
                                          if k != "bestAmountCalculation"},
                                         ensure_ascii=False, indent=2)[:3000])
                    if column == "run_arbitrage_json":
                        print("    run_arbitrage:")
                        print(json.dumps(parsed, ensure_ascii=False, indent=2)[:3000])
            print("  calculation block:")
            print(json.dumps(calculation, ensure_ascii=False, indent=2)[:6000])
            print("  live evidence keys:")
            print(json.dumps(sorted(evidence.keys()), ensure_ascii=False))
            cost_model = evidence.get("costModel")
            if isinstance(cost_model, dict):
                print("  costModel:")
                print(json.dumps(cost_model, ensure_ascii=False, indent=2)[:3000])
        shown += 1
        if shown >= args.loops:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
