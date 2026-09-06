from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.chain_health import probe_robinhood_rpc


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Robinhood Chain readonly RPC health checks.")
    parser.add_argument("--rpc-url", default="", help="Optional RPC URL override.")
    parser.add_argument("--testnet", action="store_true", help="Probe Robinhood testnet instead of mainnet.")
    parser.add_argument("--timeout", type=float, default=8.0, help="JSON-RPC timeout in seconds.")
    args = parser.parse_args()

    report = probe_robinhood_rpc(
        args.rpc_url or None,
        testnet=args.testnet,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
