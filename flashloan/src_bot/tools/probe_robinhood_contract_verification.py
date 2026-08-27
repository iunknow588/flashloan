from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from intent_trade.chains.robinhood.blockscout import (
    probe_robinhood_blockscout_contract,
    probe_robinhood_official_contracts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run readonly Robinhood Blockscout contract verification probes.")
    parser.add_argument("--address", default="", help="Optional single contract address.")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    report = (
        probe_robinhood_blockscout_contract(args.address, timeout_seconds=args.timeout)
        if args.address.strip()
        else probe_robinhood_official_contracts(timeout_seconds=args.timeout)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
