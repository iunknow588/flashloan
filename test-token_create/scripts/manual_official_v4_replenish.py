#!/usr/bin/env python3
"""Python entrypoint for manual official V4 LP-depth replenishment.

Delegates to the Hardhat/ethers.js implementation so contract artifacts and
Uniswap V4 calldata stay unchanged.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEX_DIR = ROOT / "contract" / "robinhood-testnet" / "DEX"
SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-inject-liquidity-live.js"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", default=os.getenv("ROBINHOOD_TESTNET_REPLENISH_LIQUIDITY", "2000"))
    parser.add_argument("--rpc-url", default=os.getenv("ROBINHOOD_TESTNET_RPC_URL", "https://robinhood-sepolia-rpc.publicnode.com"))
    parser.add_argument("--broadcast", action="store_true")
    args = parser.parse_args()

    node_args = [
        str(SCRIPT),
        "--amount",
        args.amount,
    ]
    if args.broadcast:
        node_args.append("--broadcast")

    env = os.environ.copy()
    env["HARDHAT_NETWORK"] = "robinhoodTestnet"
    env["ROBINHOOD_TESTNET_RPC_URL"] = args.rpc_url
    env["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST"] = "true" if args.broadcast else "false"

    result = subprocess.run(
        [os.environ.get("NODE", "node"), *node_args],
        cwd=DEX_DIR,
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
