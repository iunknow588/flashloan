from __future__ import annotations

import argparse
import json

from core.robinhood_chain_health import probe_robinhood_rpc


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

