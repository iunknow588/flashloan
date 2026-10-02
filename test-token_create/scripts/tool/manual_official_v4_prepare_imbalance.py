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


REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_TOOL_DIR = REPO_ROOT / "scripts" / "tool"
if str(SHARED_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_TOOL_DIR))

from chain_config import chain_config, set_chain_env  # noqa: E402


DIAGNOSE_SCRIPT = REPO_ROOT / "test-token_create" / "scripts" / "tool" / "testnet-v4-official-diagnose-live.js"
REPLENISH_SCRIPT = REPO_ROOT / "test-token_create" / "scripts" / "tool" / "testnet-v4-official-inject-liquidity-live.js"
MAKE_ARB_SCRIPT = REPO_ROOT / "test-token_create" / "scripts" / "tool" / "testnet-v4-official-make-arb-opportunity-live.js"


def env_for(cfg: dict, broadcast: bool) -> dict:
    env = set_chain_env(
        cfg["chain_id"],
        rpc_url_override=cfg["rpc_url"],
        manifest_override=cfg["manifest_path"],
        hardhat_network_override=cfg["hardhat_network"],
    )
    return env


def run_node(args, cfg, broadcast, extra_env=None):
    env = env_for(cfg, broadcast)
    if extra_env:
        env.update(extra_env)
    print(f"\n>>> node {' '.join(map(str, args))}\n", flush=True)
    return subprocess.run(
        [os.environ.get("NODE", "node"), *map(str, args)],
        cwd=cfg["contract_project"],
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
            "https://rpc.testnet.chain.robinhood.com/rpc",
        ),
        help="Robinhood testnet RPC URL",
    )
    parser.add_argument("--chain", type=int, default=int(os.getenv("FLASHLOAN_CHAIN_ID", os.getenv("FLASHLOAN_DEFAULT_CHAIN_ID", "46630"))))
    parser.add_argument("--manifest-path", default=os.getenv("FLASHLOAN_MANIFEST_PATH", ""))
    parser.add_argument("--hardhat-network", default=os.getenv("FLASHLOAN_HARDHAT_NETWORK", ""))
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
    cfg = chain_config(
        args.chain,
        rpc_url_override=args.rpc_url,
        manifest_override=args.manifest_path or None,
        hardhat_network_override=args.hardhat_network or None,
    )

    print("== step 1: read-only depth diagnosis ==")
    diag = run_node(
        [str(DIAGNOSE_SCRIPT), "--mode", "depth"],
        cfg,
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
            cfg,
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
        cfg,
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
