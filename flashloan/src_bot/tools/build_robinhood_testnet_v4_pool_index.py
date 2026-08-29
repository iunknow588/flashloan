from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from intent_trade.chains.robinhood_testnet.v4_pool_index import (  # noqa: E402
    write_v4_pool_index_from_initialize_logs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Robinhood Testnet Uniswap V4 pool index from PoolManager Initialize logs."
    )
    parser.add_argument("--output", default="", help="Optional output path; defaults to chain-owned v4_pool_index file.")
    parser.add_argument("--rpc-url", default="", help="Optional Robinhood Testnet RPC URL override.")
    parser.add_argument("--pool-manager", default="", help="Optional PoolManager address override.")
    parser.add_argument("--from-block", type=int, default=0)
    parser.add_argument("--to-block", type=int, default=0, help="0 means latest.")
    parser.add_argument("--chunk-size", type=int, default=5_000_000)
    parser.add_argument("--pool-limit", type=int, default=0, help="0 means no selected-pool limit.")
    parser.add_argument("--non-eth-only", action="store_true", help="Store only non-ETH pools after counting all pools.")
    parser.add_argument("--include-metadata", action="store_true", help="Fetch ERC20 symbol/decimals for display labels.")
    parser.add_argument("--skip-recent-swaps", action="store_true", help="Do not mark recent Swap activity.")
    parser.add_argument("--recent-swap-lookback-blocks", type=int, default=20_000)
    args = parser.parse_args()

    payload = write_v4_pool_index_from_initialize_logs(
        output_path=Path(args.output) if args.output else None,
        rpc_url=args.rpc_url or None,
        pool_manager=args.pool_manager or None,
        from_block=max(0, int(args.from_block)),
        to_block=int(args.to_block) if int(args.to_block or 0) > 0 else None,
        chunk_size=max(1, int(args.chunk_size)),
        pool_limit=max(0, int(args.pool_limit)),
        non_eth_only=bool(args.non_eth_only),
        include_metadata=bool(args.include_metadata),
        mark_recent_swaps=not args.skip_recent_swaps,
        recent_swap_lookback_blocks=max(1, int(args.recent_swap_lookback_blocks)),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
