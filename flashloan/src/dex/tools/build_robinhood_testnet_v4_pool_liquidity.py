from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (  # noqa: E402
    write_v4_pool_liquidity_from_contract_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Robinhood Testnet Uniswap V4 poolId liquidity cache from local JSON evidence."
    )
    parser.add_argument("--contract-metadata", default="", help="Optional contract/robinhood-testnet/DEX/config/contracts.json path.")
    parser.add_argument("--preflight-report", default="", help="Optional preflight report.json path.")
    parser.add_argument("--output", default="", help="Optional output path; defaults to chain-owned v4_pool_liquidity file.")
    parser.add_argument("--pool-limit", type=int, default=80)
    parser.add_argument("--rpc-url", default="", help="Optional Robinhood Testnet RPC URL override.")
    parser.add_argument("--skip-rpc-decode", action="store_true", help="Keep poolId-only rows without PoolKey/token metadata.")
    args = parser.parse_args()

    payload = write_v4_pool_liquidity_from_contract_evidence(
        output_path=Path(args.output) if args.output else None,
        contract_metadata_path=Path(args.contract_metadata) if args.contract_metadata else None,
        preflight_report_path=Path(args.preflight_report) if args.preflight_report else None,
        pool_limit=args.pool_limit,
        decode_pool_keys=not args.skip_rpc_decode,
        rpc_url=args.rpc_url or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
