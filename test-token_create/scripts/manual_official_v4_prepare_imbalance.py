#!/usr/bin/env python3
"""Reusable orchestration for adding LP depth before creating an imbalance.

Flow:
    1. read-only depth diagnosis
    2. optional broadcast liquidity replenishment
    3. broadcast imbalance creation (with on-chain staticCall preflight inside the JS script)

The liquidity replenisher and imbalance maker remain separate operations; this
script only sequences them for a convenient "depth first, imbalance second" run.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEX_DIR = ROOT / "contract" / "robinhood-testnet" / "DEX"
DIAGNOSE_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-diagnose-live.js"
REPLENISH_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-inject-liquidity-live.js"
MAKE_ARB_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-make-arb-opportunity-live.js"


def env_for(rpc_url: str, broadcast: bool) -> dict:
    env = os.environ.copy()
    env["HARDHAT_NETWORK"] = "robinhoodTestnet"
    env["ROBINHOOD_TESTNET_RPC_URL"] = rpc_url
    return env


def run_node(args, rpc_url, broadcast, extra_env=None):
    env = env_for(rpc_url, broadcast)
    if extra_env:
        env.update(extra_env)
    print(f"\n>>> node {' '.join(map(str, args))}\n", flush=True)
    return subprocess.run(
        [os.environ.get("NODE", "node"), *map(str, args)],
        cwd=DEX_DIR,
        env=env,
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-amount",
        default=os.getenv("ROBINHOOD_TESTNET_MAKE_ARB_AMOUNT", "100"),
        help="TZ amount used by the imbalance maker (default: 100)",
    )
    parser.add_argument(
        "--replenish-amount",
        default=os.getenv("ROBINHOOD_TESTNET_REPLENISH_LIQUIDITY", "500"),
        help="Anchor token amount per pair used by the liquidity replenisher (default: 500)",
    )
    parser.add_argument(
        "--rpc-url",
        default=os.getenv(
            "ROBINHOOD_TESTNET_RPC_URL",
            "https://robinhood-sepolia-rpc.publicnode.com",
        ),
        help="Robinhood testnet RPC URL",
    )
    parser.add_argument(
        "--broadcast",
        action="store_true",
        help="Allow on-chain transactions; without it the flow is read-only/preparation only",
    )
    parser.add_argument(
        "--skip-replenish",
        action="store_true",
        help="Skip the liquidity replenishment step",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("== step 1: read-only depth diagnosis ==")
    diag = run_node(
        [str(DIAGNOSE_SCRIPT), "--mode", "depth"],
        args.rpc_url,
        args.broadcast,
    )
    if diag.returncode != 0:
        print("depth diagnosis failed; stopping.", file=sys.stderr)
        return diag.returncode

    if not args.skip_replenish:
        print("\n== step 2: replenish LP depth ==")
        replenish_args = [str(REPLENISH_SCRIPT), "--amount", args.replenish_amount]
        if args.broadcast:
            replenish_args.append("--broadcast")
        replenish = run_node(
            replenish_args,
            args.rpc_url,
            args.broadcast,
            {"ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST": "true" if args.broadcast else "false"},
        )
        if replenish.returncode != 0:
            print("replenish step failed; stopping before imbalance maker.", file=sys.stderr)
            return replenish.returncode
    else:
        print("\n== step 2 skipped: --skip-replenish ==")

    print("\n== step 3: create imbalance (with staticCall preflight) ==")
    make_args = [str(MAKE_ARB_SCRIPT), "--amount", args.target_amount]
    if args.broadcast:
        make_args.append("--broadcast")
    make_arb = run_node(
        make_args,
        args.rpc_url,
        args.broadcast,
        {"ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_MAKE_ARB_BROADCAST": "true" if args.broadcast else "false"},
    )
    if make_arb.returncode != 0:
        print("imbalance maker step failed.", file=sys.stderr)
        return make_arb.returncode

    print("\nresult: OK. LP depth checked and imbalance maker completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
