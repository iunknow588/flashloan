from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_chain_env_files  # noqa: E402
from dex.intent_trade.chains.robinhood.v4_pool_index import (  # noqa: E402
    build_v4_pool_index_from_initialize_logs,
    build_v4_pool_index_from_observation_pool_ids,
    keep_token_addresses_from_payload,
    mark_pool_liquidity_for_pool_index,
    mark_recent_swap_activity_for_pool_index,
    write_v4_pool_index_from_initialize_logs,
    write_v4_pool_index_from_observation_pool_ids,
)


def main() -> int:
    load_chain_env_files(Path(__file__).resolve())
    parser = argparse.ArgumentParser(
        description="Build Robinhood Mainnet Uniswap V4 pool index evidence."
    )
    parser.add_argument(
        "--scope",
        choices=["observation", "full"],
        default="observation",
        help="observation (default) decodes only poolIds already referenced by market observation; "
        "full scans PoolManager Initialize logs from from-block.",
    )
    parser.add_argument("--output", default="", help="Optional output path; defaults to chain-owned v4_pool_index file.")
    parser.add_argument("--rpc-url", default="", help="Optional Robinhood Mainnet RPC URL override.")
    parser.add_argument("--pool-manager", default="", help="Optional PoolManager address override.")
    parser.add_argument("--from-block", type=int, default=0)
    parser.add_argument("--to-block", type=int, default=0, help="0 means latest.")
    parser.add_argument("--chunk-size", type=int, default=5_000_000)
    parser.add_argument("--pool-batch-size", type=int, default=200)
    parser.add_argument("--pool-ids", default="", help="Comma-separated poolIds to scan for recent Swap activity; empty means all pools in the index.")
    parser.add_argument("--pool-limit", type=int, default=0, help="0 means no selected-pool limit (full scan only).")
    parser.add_argument("--non-eth-only", action="store_true", help="Store only non-ETH pools after counting all pools (full scan only).")
    parser.add_argument("--include-metadata", action="store_true", help="Fetch ERC20 symbol/decimals for display labels (full scan only).")
    parser.add_argument("--eth-pair-candidate", default="", help="Path to eth_pair_tokens JSON; keep only ETH pools and bridge pools touching candidate tokens (full scan only).")
    parser.add_argument("--skip-recent-swaps", action="store_true", help="Do not mark recent Swap activity.")
    parser.add_argument("--recent-swap-lookback-blocks", type=int, default=0, help="Explicit lookback blocks; defaults to 60 days at the configured blocks/day.")
    parser.add_argument("--recent-swap-lookback-days", type=int, default=60, help="Lookback window in days for recent Swap activity.")
    parser.add_argument("--mark-existing-index", action="store_true", help="Refresh an existing v4_pool_index/latest.json with recent Swap activity without re-decoding Initialize logs.")
    parser.add_argument("--mark-existing-index-liquidity", action="store_true", help="Refresh an existing v4_pool_index/latest.json with live PoolManager liquidity without re-decoding Initialize logs.")
    parser.add_argument("--no-write", action="store_true", help="Print evidence without writing to disk.")
    args = parser.parse_args()

    kwargs = {
        "output_path": Path(args.output) if args.output else None,
        "rpc_url": args.rpc_url or None,
        "pool_manager": args.pool_manager or None,
    }

    if args.mark_existing_index_liquidity:
        payload = mark_pool_liquidity_for_pool_index(
            rpc_url=args.rpc_url or None,
            pool_manager=args.pool_manager or None,
            output_path=Path(args.output) if args.output else None,
            pool_ids=[item.strip() for item in args.pool_ids.split(",") if item.strip()] if args.pool_ids else None,
        )
    elif args.mark_existing_index:
        payload = mark_recent_swap_activity_for_pool_index(
            rpc_url=args.rpc_url or None,
            pool_manager=args.pool_manager or None,
            output_path=Path(args.output) if args.output else None,
            recent_swap_lookback_days=max(1, int(args.recent_swap_lookback_days)),
            recent_swap_lookback_blocks=max(1, int(args.recent_swap_lookback_blocks)) if int(args.recent_swap_lookback_blocks or 0) > 0 else None,
            chunk_size=max(1, int(args.chunk_size)),
            pool_batch_size=max(1, int(args.pool_batch_size)),
            pool_ids=[item.strip() for item in args.pool_ids.split(",") if item.strip()] if args.pool_ids else None,
        )
    elif args.scope == "full":
        keep_token_addresses = None
        if args.eth_pair_candidate:
            candidate_payload = {}
            try:
                candidate_payload = json.loads(Path(args.eth_pair_candidate).read_text(encoding="utf-8-sig"))
            except Exception as exc:
                raise SystemExit(f"failed to read --eth-pair-candidate: {exc}")
            if not isinstance(candidate_payload, dict):
                raise SystemExit("--eth-pair-candidate must contain a JSON object")
            keep_token_addresses = keep_token_addresses_from_payload(candidate_payload)
            if not keep_token_addresses:
                raise SystemExit("--eth-pair-candidate did not yield any non-zero token addresses")
        kwargs.update(
            {
                "from_block": max(0, int(args.from_block)),
                "to_block": int(args.to_block) if int(args.to_block or 0) > 0 else None,
                "chunk_size": max(1, int(args.chunk_size)),
                "pool_limit": max(0, int(args.pool_limit)),
                "non_eth_only": bool(args.non_eth_only),
                "include_metadata": bool(args.include_metadata),
                "keep_token_addresses": keep_token_addresses,
                "mark_recent_swaps": not args.skip_recent_swaps,
                "recent_swap_lookback_blocks": max(1, int(args.recent_swap_lookback_blocks)) if int(args.recent_swap_lookback_blocks or 0) > 0 else None,
                "recent_swap_lookback_days": max(1, int(args.recent_swap_lookback_days)),
            }
        )
    else:
        kwargs.update(
            {
                "mark_recent_swaps": not args.skip_recent_swaps,
                "recent_swap_lookback_blocks": max(1, int(args.recent_swap_lookback_blocks)) if int(args.recent_swap_lookback_blocks or 0) > 0 else None,
                "recent_swap_lookback_days": max(1, int(args.recent_swap_lookback_days)),
            }
        )

    if args.no_write:
        if args.scope == "full":
            payload = build_v4_pool_index_from_initialize_logs(
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k != "output_path" and k != "recent_swap_lookback_days"
                }
            )
        else:
            payload = build_v4_pool_index_from_observation_pool_ids(**{k: v for k, v in kwargs.items() if k != "output_path"})
    else:
        if args.scope == "full":
            payload = write_v4_pool_index_from_initialize_logs(
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k != "recent_swap_lookback_days"
                }
            )
        else:
            payload = write_v4_pool_index_from_observation_pool_ids(**kwargs)

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
