#!/usr/bin/env python3
"""Interactive menu for common Robinhood official V4 operations.

Every menu item wraps the existing Hardhat/ethers.js scripts so V4 calldata,
artifacts and broadcast guards stay unchanged.

The menu is now chain-parameterized. It defaults to Robinhood Testnet but can be
pointed at an arbitrary chain/project/manifest via CLI flags or the shared
``FLASHLOAN_CHAIN_ID`` / ``FLASHLOAN_MANIFEST_PATH`` environment variables.

Examples:
    python test-token_create/scripts/tool/manual_official_v4_menu.py
    python test-token_create/scripts/tool/manual_official_v4_menu.py --chain 46630 --choice 3
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = REPO_ROOT / "test-token_create" / "scripts" / "tool"
SHARED_TOOL_DIR = REPO_ROOT / "scripts" / "tool"
if str(SHARED_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_TOOL_DIR))

from chain_config import chain_config, set_chain_env  # noqa: E402


REPLENISH_SCRIPT = TOOL_DIR / "testnet-v4-official-inject-liquidity-live.js"
MAKE_ARB_SCRIPT = TOOL_DIR / "testnet-v4-official-make-arb-opportunity-live.js"
DIAGNOSE_SCRIPT = TOOL_DIR / "testnet-v4-official-diagnose-live.js"


CHOICES = [
    {
        "id": 1,
        "label": "补充流动性 100（广播）",
        "script": REPLENISH_SCRIPT,
        "amount": "100",
        "broadcast": True,
        "env_gate": "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST",
    },
    {
        "id": 2,
        "label": "补充流动性 500（广播）",
        "script": REPLENISH_SCRIPT,
        "amount": "500",
        "broadcast": True,
        "env_gate": "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST",
    },
    {
        "id": 3,
        "label": "检测深度（只读/不广播）",
        "script": DIAGNOSE_SCRIPT,
        "mode": "depth",
    },
    {
        "id": 4,
        "label": "检测三角平衡（只读/不广播）",
        "script": DIAGNOSE_SCRIPT,
        "mode": "balance",
    },
    {
        "id": 5,
        "label": "增加 TZ 不平衡性 100（广播）",
        "script": MAKE_ARB_SCRIPT,
        "amount": "100",
        "broadcast": True,
        "env_gate": "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_MAKE_ARB_BROADCAST",
    },
    {
        "id": 6,
        "label": "增加 TZ 不平衡性 500（广播）",
        "script": MAKE_ARB_SCRIPT,
        "amount": "500",
        "broadcast": True,
        "env_gate": "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_MAKE_ARB_BROADCAST",
    },
    {
        "id": 7,
        "label": "检测深度与三角平衡（只读/不广播）",
        "script": DIAGNOSE_SCRIPT,
        "mode": "all",
    },
]


def print_menu(cfg):
    print(f"{cfg['display_name']} V4 常用操作（链={cfg['chain_id']}）")
    for choice in CHOICES:
        print(f"  {choice['id']}. {choice['label']}")
    print("  0. 退出")


def run_choice(choice, cfg):
    node_args = [str(choice["script"])]
    if choice.get("amount"):
        node_args.extend(["--amount", choice["amount"]])
    if choice.get("mode"):
        node_args.extend(["--mode", choice["mode"]])
    if choice.get("broadcast"):
        node_args.append("--broadcast")

    env = set_chain_env(
        cfg["chain_id"],
        rpc_url_override=cfg["rpc_url"],
        manifest_override=cfg["manifest_path"],
        hardhat_network_override=cfg["hardhat_network"],
    )
    if choice.get("env_gate"):
        env[choice["env_gate"]] = "true" if choice.get("broadcast") else "false"

    print(f"\n>>> {choice['label']}")
    print(f">>> {'广播' if choice.get('broadcast') else '只读/不广播'} 模式，RPC={cfg['rpc_url']}\n")
    return subprocess.run(
        [os.environ.get("NODE", "node"), *node_args],
        cwd=str(cfg["contract_project"]),
        env=env,
    ).returncode


def print_result_summary(choice_id, manifesto, rc):
    print(f"\n[done] choice={choice_id} exit_code={rc}")
    try:
        manifest = json.loads(Path(manifesto).read_text(encoding="utf-8"))
    except Exception:
        print("[done] manifest 摘要不可用")
        return
    official = manifest.get("officialV4") or {}
    if choice_id in (5, 6):
        last = official.get("lastMakeArb") or {}
        if last:
            print(f"  lastMakeArb.txHash={last.get('txHash')}")
            print(f"  amountIn={last.get('amountIn')}")
            print(f"  runAt={last.get('runAt')}")
            route = last.get("route") or {}
            if route:
                print(f"  route.directions={route.get('directions')}")
                print(f"  route.keysCount={len(route.get('keys') or [])}")
    elif choice_id in (1, 2):
        last = official.get("lastReplenish") or {}
        if last:
            print(f"  lastReplenish.txHash={last.get('txHash')}")
            print(f"  liquidityToAdd={last.get('liquidityToAdd')}")
            print(f"  runAt={last.get('runAt')}")
    else:
        print("  只读诊断完成，未广播交易")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chain",
        type=int,
        default=int(os.getenv("FLASHLOAN_CHAIN_ID", os.getenv("FLASHLOAN_DEFAULT_CHAIN_ID", "46630"))),
        help="chain id; default 46630 (Robinhood Testnet)",
    )
    parser.add_argument(
        "--rpc-url",
        default=os.getenv("FLASHLOAN_RPC_URL", os.getenv("ROBINHOOD_TESTNET_RPC_URL", "")),
        help="RPC URL override",
    )
    parser.add_argument(
        "--manifest-path",
        default=os.getenv("FLASHLOAN_MANIFEST_PATH", ""),
        help="manifest.json path override",
    )
    parser.add_argument(
        "--dex-dir",
        default=os.getenv("FLASHLOAN_CONTRACT_PROJECT", ""),
        help="Hardhat DEX project directory override",
    )
    parser.add_argument(
        "--hardhat-network",
        default=os.getenv("FLASHLOAN_HARDHAT_NETWORK", ""),
        help="Hardhat network name override",
    )
    parser.add_argument("--choice", type=int, help="run a menu id directly instead of entering interactive menu")
    return parser.parse_args()


def resolve_cfg(args):
    cfg = chain_config(
        args.chain,
        rpc_url_override=args.rpc_url or None,
        manifest_override=args.manifest_path or None,
        hardhat_network_override=args.hardhat_network or None,
    )
    if args.dex_dir:
        cfg["contract_project"] = Path(args.dex_dir).expanduser().resolve()
    if not cfg["contract_project"].exists():
        raise SystemExit(f"contract project does not exist: {cfg['contract_project']}")
    if not cfg["manifest_path"].exists():
        raise SystemExit(
            f"manifest does not exist for chain {args.chain}: {cfg['manifest_path']}\n"
            "use --manifest-path or FLASHLOAN_MANIFEST_PATH to point at the chain manifest"
        )
    return cfg


def main() -> int:
    args = parse_args()
    cfg = resolve_cfg(args)
    if args.choice:
        for choice in CHOICES:
            if choice["id"] == args.choice:
                rc = run_choice(choice, cfg)
                print_result_summary(choice["id"], cfg["manifest_path"], rc)
                return rc
        print(f"未知菜单编号：{args.choice}", file=sys.stderr)
        return 2

    print_menu(cfg)
    while True:
        raw = input("\n请输入菜单编号> ").strip()
        if raw == "0":
            return 0
        try:
            choice_id = int(raw)
        except ValueError:
            print("请输入数字编号")
            continue
        matched = [choice for choice in CHOICES if choice["id"] == choice_id]
        if not matched:
            print("没有这个编号")
            continue
        rc = run_choice(matched[0], cfg)
        print_result_summary(matched[0]["id"], cfg["manifest_path"], rc)


if __name__ == "__main__":
    raise SystemExit(main())
