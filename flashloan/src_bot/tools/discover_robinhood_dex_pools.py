from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.robinhood_dex_discovery import scan_uniswap_v3_pool_created
from intent_trade.chains.robinhood import config as robinhood


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a sourced Robinhood DEX factory for Uniswap V3 PoolCreated events.")
    parser.add_argument("--factory", required=True, help="Factory address from an independently verified source.")
    parser.add_argument("--source-url", required=True, help="Official/protocol/Blockscout source URL for the factory.")
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, required=True)
    parser.add_argument("--rpc-url", default="", help="Optional Robinhood RPC URL override.")
    parser.add_argument("--chunk-size", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--output", default="", help="Optional JSON evidence output path.")
    args = parser.parse_args()
    rpc_url = args.rpc_url.strip() or robinhood.rpc_urls_from_env()[0]
    report = scan_uniswap_v3_pool_created(
        rpc_url,
        factory_address=args.factory,
        source_url=args.source_url,
        from_block=args.from_block,
        to_block=args.to_block,
        chunk_size=args.chunk_size,
        timeout_seconds=args.timeout,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
