from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from web3 import Web3


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from bot.execution.venus_abis import VENUS_CORE_COMPTROLLER_ADDRESS
from bot.execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR, build_venus_market_static_table, write_venus_market_static_table
from dex.intent_trade.chains.bnb.quote import _connect
from dex.intent_trade.chains.bnb import config as bnb


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the BNB Venus Core market static table.")
    parser.add_argument("--rpc-url", action="append", default=[], help="BNB HTTP RPC. Can be passed more than once.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--comptroller", default=VENUS_CORE_COMPTROLLER_ADDRESS)
    parser.add_argument("--oracle", default="")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(__file__)
    args = parse_args(list(argv or sys.argv[1:]))
    w3, rpc_url = _connect(args.rpc_url or list(bnb.rpc_urls_from_env()))
    table = build_venus_market_static_table(
        w3,
        comptroller_address=args.comptroller,
        oracle_address=args.oracle,
        rpc_url=rpc_url,
        limit=args.limit,
    )
    paths = write_venus_market_static_table(table, args.output_dir)
    print(json.dumps({"ok": True, "market_count": table["market_count"], "paths": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
