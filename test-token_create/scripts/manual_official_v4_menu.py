#!/usr/bin/env python3
"""Interactive menu for common Robinhood official V4 testnet operations.

Every menu item wraps the existing Hardhat/ethers.js scripts so V4 calldata,
artifacts and broadcast guards stay unchanged.

Examples:
    python test-token_create/scripts/manual_official_v4_menu.py
    python test-token_create/scripts/manual_official_v4_menu.py --choice 3
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEX_DIR = ROOT / "contract" / "robinhood-testnet" / "DEX"
REPLENISH_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-inject-liquidity-live.js"
MAKE_ARB_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-make-arb-opportunity-live.js"
DIAGNOSE_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-diagnose-live.js"
MANIFEST_PATH = ROOT / "test-token_create" / "config" / "testnet-v4-amm" / "manifest.json"


CHOICES = [
    {
        "id": 1,
        "label": "增加流动性 100（广播）",
        "script": REPLENISH_SCRIPT,
        "amount": "100",
        "broadcast": True,
        "env_gate": "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST",
    },
    {
        "id": 2,
        "label": "增加流动性 500（广播）",
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


def print_menu():
    print("Robinhood 测试网官方 V4 常用操作")
    for choice in CHOICES:
        print(f"  {choice['id']}. {choice['label']}")
    print("  0. 退出")


def run_choice(choice, rpc_url):
    node_args = [str(choice["script"])]
    if choice.get("amount"):
        node_args.extend(["--amount", choice["amount"]])
    if choice.get("mode"):
        node_args.extend(["--mode", choice["mode"]])
    if choice.get("broadcast"):
        node_args.append("--broadcast")

    env = os.environ.copy()
    env["HARDHAT_NETWORK"] = "robinhoodTestnet"
    env["ROBINHOOD_TESTNET_RPC_URL"] = rpc_url
    if choice.get("env_gate"):
        env[choice["env_gate"]] = "true" if choice.get("broadcast") else "false"

    print(f"\n>>> {choice['label']}")
    print(f">>> {'广播' if choice.get('broadcast') else '只读/不广播'} 模式，RPC={rpc_url}\n")
    return subprocess.run(
        [os.environ.get("NODE", "node"), *node_args],
        cwd=DEX_DIR,
        env=env,
    ).returncode


def print_result_summary(choice_id, rc):
    print(f"\n[执行完成] 编号={choice_id} 退出码={rc}")
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        print("[执行完成] 无法读取 manifest 结果摘要")
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
    parser.add_argument("--choice", type=int, help="直接执行某个菜单编号，不进入交互菜单")
    parser.add_argument(
        "--rpc-url",
        default=os.getenv(
            "ROBINHOOD_TESTNET_RPC_URL",
            "https://robinhood-sepolia-rpc.publicnode.com",
        ),
        help="Robinhood 测试网 RPC 地址",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.choice:
        for choice in CHOICES:
            if choice["id"] == args.choice:
                rc = run_choice(choice, args.rpc_url)
                print_result_summary(choice["id"], rc)
                return rc
        print(f"未知菜单编号: {args.choice}", file=sys.stderr)
        return 2

    print_menu()
    while True:
        raw = input("\n请输入编号: ").strip()
        if raw == "0":
            return 0
        try:
            choice_id = int(raw)
        except ValueError:
            print("请输入数字编号。")
            continue
        matched = [choice for choice in CHOICES if choice["id"] == choice_id]
        if not matched:
            print("没有这个编号。")
            continue
        rc = run_choice(matched[0], args.rpc_url)
        print_result_summary(matched[0]["id"], rc)


if __name__ == "__main__":
    raise SystemExit(main())
