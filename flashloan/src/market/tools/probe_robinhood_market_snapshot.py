"""Read Robinhood V3 pool state and quote round trips without broadcasting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.market_snapshot import build_robinhood_market_snapshot


DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "evidence" / "robinhood" / "market_snapshot" / "latest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--amount-usdg", default="1000")
    parser.add_argument("--flashloan-fee-bps", type=int, default=None)
    parser.add_argument("--gas-units", type=int, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_robinhood_market_snapshot(
        rpc_url=args.rpc_url or None,
        amount_usdg=args.amount_usdg,
        flashloan_fee_bps=args.flashloan_fee_bps,
        gas_units_override=args.gas_units,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "ok": True,
        "output": str(args.output),
        "network": report["network"],
        "chainId": report["chainId"],
        "blockNumber": report["blockNumber"],
        "validPoolCount": report["validPoolCount"],
        "quotedRouteCount": sum(1 for item in report["routes"] if "amountOutRaw" in item),
        "bestRoute": report["bestRoute"],
        "costModel": report["costModel"],
        "executionEligible": report["arbitrageAssessment"]["executionEligible"],
        "flashloanStatus": report["arbitrageAssessment"]["flashloanStatus"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
