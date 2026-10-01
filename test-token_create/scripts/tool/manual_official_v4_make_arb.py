#!/usr/bin/env python3
"""Python entrypoint for manually creating an official V4 arbitrage opportunity."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_TOOL_DIR = REPO_ROOT / "scripts" / "tool"
if str(SHARED_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_TOOL_DIR))

from chain_config import chain_config, set_chain_env  # noqa: E402


SCRIPT = REPO_ROOT / "test-token_create" / "scripts" / "tool" / "testnet-v4-official-make-arb-opportunity-live.js"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", default=os.getenv("ROBINHOOD_TESTNET_MAKE_ARB_AMOUNT", "0.001"))
    parser.add_argument("--rpc-url", default=os.getenv("ROBINHOOD_TESTNET_RPC_URL", "https://rpc.testnet.chain.robinhood.com"))
    parser.add_argument("--chain", type=int, default=int(os.getenv("FLASHLOAN_CHAIN_ID", os.getenv("FLASHLOAN_DEFAULT_CHAIN_ID", "46630"))))
    parser.add_argument("--manifest-path", default=os.getenv("FLASHLOAN_MANIFEST_PATH", ""))
    parser.add_argument("--hardhat-network", default=os.getenv("FLASHLOAN_HARDHAT_NETWORK", ""))
    parser.add_argument("--broadcast", action="store_true")
    args = parser.parse_args()

    cfg = chain_config(
        args.chain,
        rpc_url_override=args.rpc_url,
        manifest_override=args.manifest_path or None,
        hardhat_network_override=args.hardhat_network or None,
    )
    node_args = [
        str(SCRIPT),
        "--amount",
        args.amount,
    ]
    if args.broadcast:
        node_args.append("--broadcast")

    env = set_chain_env(
        args.chain,
        rpc_url_override=args.rpc_url,
        manifest_override=args.manifest_path or None,
        hardhat_network_override=args.hardhat_network or None,
    )
    env["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_MAKE_ARB_BROADCAST"] = "true" if args.broadcast else "false"

    result = subprocess.run(
        [os.environ.get("NODE", "node"), *node_args],
        cwd=cfg["contract_project"],
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
