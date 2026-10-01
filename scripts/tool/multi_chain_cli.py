#!/usr/bin/env python3
"""Unified flashLoan CLI/interactive entrypoint.

This is the multi-chain operations launcher. It replaces the old fixed-testnet
menu as the primary entrypoint while preserving direct single-action mode for
scripts and CI.

Examples:
    python scripts/tool/multi_chain_cli.py
    python scripts/tool/multi_chain_cli.py --chain 46630 --action testnet-menu
    python scripts/tool/multi_chain_cli.py --chain 4663 --action mainnet-preflight
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from chain_config import (
    chain_config,
    contract_project_path,
    set_chain_env,
    supported_chain_configs,
)


ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = Path(__file__).resolve().parent

TEST_TOKEN_TOOL_DIR = ROOT / "test-token_create" / "scripts" / "tool"
if str(TEST_TOKEN_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_TOKEN_TOOL_DIR))

from manual_official_v4_menu import CHOICES as TESTNET_MENU_CHOICES  # noqa: E402


LOCAL_V4_OPERATIONS = [
    (1, "创建代币", "Create tokens", "local:v4-amm:create-tokens"),
    (2, "注入流动性", "Inject liquidity", "local:v4-amm:inject-liquidity"),
    (3, "制造套利机会", "Create arb opportunity", "local:v4-amm:create-arb-opportunity"),
    (4, "运行套利 100", "Run arbitrage 100", "local:v4-amm:run-arb-100"),
]

TESTNET_MENU_EN_LABELS = {
    1: "Replenish liquidity 100 (broadcast)",
    2: "Replenish liquidity 500 (broadcast)",
    3: "Check depth (read-only/no broadcast)",
    4: "Check triangle balance (read-only/no broadcast)",
    5: "Increase TZ imbalance 100 (broadcast)",
    6: "Increase TZ imbalance 500 (broadcast)",
    7: "Check depth and triangle balance (read-only/no broadcast)",
}


def _python(cmd: list[str]) -> list[str]:
    return [sys.executable, *cmd]


def _npm_run(project: Path, script: str, env: dict[str, str] | None = None) -> int:
    npm = shutil.which("npm.cmd") or shutil.which("npm") or ("npm.cmd" if os.name == "nt" else "npm")
    return subprocess.run(
        [npm, "run", script],
        cwd=str(project),
        env=env or os.environ.copy(),
        check=False,
    ).returncode


def run_web(chain_id: int) -> int:
    env = set_chain_env(chain_id)
    cmd = _python([str(ROOT / "flashloan" / "app_runtime" / "run.py")])
    return subprocess.run(cmd, cwd=str(ROOT), env=env, check=False).returncode


def run_mainnet_preflight(chain_id: int, *, readiness: bool = False) -> int:
    env = set_chain_env(chain_id)
    project = contract_project_path(chain_id, "DEX")
    rc = _npm_run(project, "preflight:robinhood:mainnet", env)
    if rc != 0:
        return rc
    if readiness:
        rc = _npm_run(project, "ready:robinhood:mainnet", env)
    return rc


def run_mainnet_readiness(chain_id: int) -> int:
    env = set_chain_env(chain_id)
    project = contract_project_path(chain_id, "DEX")
    return _npm_run(project, "ready:robinhood:mainnet", env)


def run_mainnet_bridge_pairs(chain_id: int) -> int:
    env = set_chain_env(chain_id)
    project = contract_project_path(chain_id, "DEX")
    return _npm_run(project, "preflight:robinhood:mainnet:bridge-pairs", env)


def run_mainnet_refresh_v4_index(chain_id: int) -> int:
    env = set_chain_env(chain_id)
    cmd = _python([str(ROOT / "flashloan" / "src" / "dex" / "tools" / "build_robinhood_mainnet_v4_pool_index.py"), "--scope", "observation"])
    return subprocess.run(cmd, cwd=str(ROOT), env=env, check=False).returncode


def run_testnet_menu(chain_id: int, *, rpc_url: str | None = None, choice: int | None = None) -> int:
    if chain_id != 46630:
        raise SystemExit(f"testnet V4 tool menu requires chain 46630; got {chain_id}")
    cfg = chain_config(chain_id, rpc_url_override=rpc_url)
    env = set_chain_env(chain_id, rpc_url_override=rpc_url)
    args = _python([str(ROOT / "test-token_create" / "scripts" / "tool" / "manual_official_v4_menu.py")])
    args += ["--chain", str(chain_id), "--rpc-url", cfg["rpc_url"]]
    if choice is not None:
        args += ["--choice", str(choice)]
    return subprocess.run(args, cwd=str(ROOT), env=env, check=False).returncode


def run_local_v4_menu() -> int:
    chain_id = 31337
    env = set_chain_env(chain_id)
    project = contract_project_path(46630, "DEX")
    print("本地 V4 菜单（需要先启动 hardhat 节点，端口 8545）：")
    for local_id, cn_label, _, script in LOCAL_V4_OPERATIONS:
        print(f"  {local_id}. {cn_label}")
        print(f"     npm run {script}")
    print("  0. 返回")
    scripts = {str(local_id): script for local_id, _, _, script in LOCAL_V4_OPERATIONS}
    while True:
        raw = input("请输入选项> ").strip()
        if raw == "0":
            return 0
        if raw not in scripts:
            print("未知选项")
            continue
        rc = _npm_run(project, scripts[raw], env)
        if rc != 0:
            return rc


def run_local_v4_operation(choice_id: int) -> int:
    for local_id, _, _, script in LOCAL_V4_OPERATIONS:
        if local_id == choice_id:
            chain_id = 31337
            env = set_chain_env(chain_id)
            project = contract_project_path(46630, "DEX")
            return _npm_run(project, script, env)
    return 2


def run_targeted_tests() -> int:
    tests = [
        "flashloan/app_runtime/tests/unit/test_mainnet_observation_isolation.py",
        "flashloan/app_runtime/tests/unit/test_chain_selector_behavior.py",
        "flashloan/app_runtime/tests/unit/test_startup_behavior.py",
        "flashloan/app_runtime/tests/unit/test_multi_chain_tooling.py",
        "flashloan/app_runtime/tests/functional/test_navigation_flow.py",
        "flashloan/app_runtime/tests/functional/test_web_app_assembly.py",
    ]
    cmd = _python(["-m", "pytest", "-q", *tests])
    return subprocess.run(cmd, cwd=str(ROOT), check=False).returncode


def run_chain_data_audit() -> int:
    cmd = _python(["flashloan/src/market/tools/audit_chain_data_registry.py"])
    return subprocess.run(cmd, cwd=str(ROOT), check=False).returncode


def print_chain_info(chain_id: int) -> int:
    cfg = chain_config(chain_id)
    print(f"chain_id       : {cfg['chain_id']}")
    print(f"chain_key      : {cfg['chain_key']}")
    print(f"network        : {cfg['network']}")
    print(f"display_name   : {cfg['display_name']}")
    print(f"rpc_url        : {cfg['rpc_url']}")
    print(f"rpc_urls       : {', '.join(cfg['rpc_urls'])}")
    print(f"rpc_source     : {cfg['rpc_source']}")
    print(f"hardhat_network: {cfg['hardhat_network']}")
    print(f"foundry_alias  : {cfg['foundry_alias']}")
    print(f"explorer_url   : {cfg['explorer_url']}")
    print(f"chain_json     : {cfg['chain_json']}")
    print(f"contracts_json : {cfg['contracts_json']}")
    print(f"manifest_path  : {cfg['manifest_path']}")
    print(f"contract_project: {cfg['contract_project']}")
    print(f"broadcast_enabled: {cfg['broadcast_enabled']}")
    return 0


def _menu(chain_id: int, *, chinese: bool = False) -> None:
    cfg = chain_config(chain_id)
    is_mainnet = chain_id == 4663
    is_testnet = chain_id == 46630
    is_local = chain_id == 31337
    if chinese:
        print(f"\nflashLoan CLI（链={cfg['chain_id']} {cfg['display_name']}）")
        print("  1. 切换链")
        print("  2. 启动 Web 控制台")
        if is_mainnet:
            print("  3. 主网预检 / 就绪检查")
            print("     3.1 主网预检（只读 / 不广播）")
            print("     3.2 主网就绪检查")
            print("     3.3 主网预检 + 就绪检查")
            print("  4. 主网桥接交易对只读预检")
            print("     4.1 执行主网桥接交易对只读预检")
            print("  5. 刷新主网 V4 池索引（观测范围）")
            print("     5.1 刷新主网 V4 池索引观测范围")
        if is_testnet:
            print("  6. 测试网 V4 二级菜单（输入 6.x 直接执行）")
            for choice in TESTNET_MENU_CHOICES:
                print(f"     6.{choice['id']}. {choice['label']}")
        if is_local:
            print("  7. 本地 V4 二级菜单（输入 7.x 直接执行）")
            for local_id, cn_label, _, _ in LOCAL_V4_OPERATIONS:
                print(f"     7.{local_id}. {cn_label}")
        print("  8. 运行定向测试")
        print("  9. 运行链数据注册表审计")
        print("  10. 查看当前链配置")
        print("  0. 退出")
        return
    print(f"\nflashLoan CLI (chain={cfg['chain_id']} {cfg['display_name']})")
    print("  1. Switch chain")
    print("  2. Start web console")
    if is_mainnet:
        print("  3. Mainnet preflight / readiness")
        print("     3.1 Mainnet preflight (read-only/no broadcast)")
        print("     3.2 Mainnet readiness check")
        print("     3.3 Mainnet preflight + readiness")
        print("  4. Mainnet bridge-pairs read-only preflight")
        print("     4.1 Run mainnet bridge-pairs read-only preflight")
        print("  5. Refresh mainnet V4 pool index (observation scope)")
        print("     5.1 Refresh mainnet V4 pool index observation scope")
    if is_testnet:
        print("  6. Testnet V4 secondary menu (enter 6.x to run directly)")
        for choice in TESTNET_MENU_CHOICES:
            print(f"     6.{choice['id']}. {TESTNET_MENU_EN_LABELS[choice['id']]}")
    if is_local:
        print("  7. Local V4 secondary menu (enter 7.x to run directly)")
        for local_id, _, en_label, _ in LOCAL_V4_OPERATIONS:
            print(f"     7.{local_id}. {en_label}")
    print("  8. Run targeted tests")
    print("  9. Run chain data registry audit")
    print("  10. Show current chain config")
    print("  0. Exit")


def interactive_main(default_chain_id: int, initial_action: str | None = None, *, chinese: bool = False) -> int:
    chain_id = int(default_chain_id)
    actions = {
        "2": run_web,
        "3": lambda c: run_mainnet_preflight(c, readiness=True),
        "4": run_mainnet_bridge_pairs,
        "5": lambda c: run_mainnet_refresh_v4_index(c),
        "8": lambda c: run_targeted_tests(),
        "9": lambda c: run_chain_data_audit(),
        "10": lambda c: print_chain_info(c),
    }
    if initial_action:
        if initial_action == "menu":
            pass
        elif initial_action in {"web", "mainnet-preflight", "mainnet-bridge-pairs", "mainnet-refresh-v4-index", "testnet-menu", "local-menu", "tests", "audit", "chain-info"}:
            mapping = {
                "web": run_web,
                "mainnet-preflight": lambda c: run_mainnet_preflight(c, readiness=True),
                "mainnet-bridge-pairs": run_mainnet_bridge_pairs,
                "mainnet-refresh-v4-index": run_mainnet_refresh_v4_index,
                "testnet-menu": lambda c: run_testnet_menu(c),
                "local-menu": lambda c: run_local_v4_menu(),
                "tests": lambda c: run_targeted_tests(),
                "audit": lambda c: run_chain_data_audit(),
                "chain-info": lambda c: print_chain_info(c),
            }
            return mapping[initial_action](chain_id)
        else:
            print(f"unknown action: {initial_action}", file=sys.stderr)
            return 2

    while True:
        _menu(chain_id, chinese=chinese)
        try:
            raw = input("请输入选项> " if chinese else "choice> ").strip()
        except EOFError:
            return 0
        if raw == "0":
            return 0
        if raw == "1":
            configs = supported_chain_configs()
            print("可用链：" if chinese else "Available chains:")
            for cid, cfg in configs.items():
                print(f"  {cid}. {cfg['display_name']}")
            raw_chain = input("请输入链 ID> " if chinese else "chain id> ").strip()
            candidate = int(raw_chain) if raw_chain.isdigit() else None
            if candidate not in configs:
                print("无效链 ID" if chinese else "invalid chain id")
            else:
                chain_id = candidate
            continue
        if raw.startswith(("3.", "4.", "5.")):
            if chain_id != 4663:
                print("当前链不是主网，不能执行主网专用操作" if chinese else "current chain is not mainnet; mainnet-only operation is unavailable")
                continue
            section, sub_raw = raw.split(".", 1)
            if not sub_raw.isdigit():
                print(f"未知选项：{raw}" if chinese else f"unknown choice: {raw}")
                continue
            choice_id = int(sub_raw)
            if (section == "3" and choice_id not in {1, 2, 3}) or (section in {"4", "5"} and choice_id != 1):
                print(f"未知选项：{raw}" if chinese else f"unknown choice: {raw}")
                continue
            if section == "3":
                if choice_id == 1:
                    run_mainnet_preflight(chain_id, readiness=False)
                elif choice_id == 2:
                    run_mainnet_readiness(chain_id)
                else:
                    run_mainnet_preflight(chain_id, readiness=True)
            elif section == "4":
                run_mainnet_bridge_pairs(chain_id)
            else:
                run_mainnet_refresh_v4_index(chain_id)
            continue
        if raw.startswith("6."):
            if chain_id != 46630:
                print("当前链不是测试网，不能执行测试网二级菜单" if chinese else "current chain is not testnet; testnet submenu is unavailable")
                continue
            sub_raw = raw.split(".", 1)[1]
            if not sub_raw.isdigit():
                print(f"未知选项：{raw}" if chinese else f"unknown choice: {raw}")
                continue
            choice_id = int(sub_raw)
            if not any(choice["id"] == choice_id for choice in TESTNET_MENU_CHOICES):
                print(f"未知选项：{raw}" if chinese else f"unknown choice: {raw}")
                continue
            run_testnet_menu(chain_id, choice=choice_id)
            continue
        if raw == "6":
            if chain_id != 46630:
                print("当前链不是测试网，不能打开测试网二级菜单" if chinese else "current chain is not testnet; testnet menu is unavailable")
                continue
            print("请输入 6.x 直接执行测试网 V4 二级操作" if chinese else "enter 6.x to run a testnet V4 secondary operation directly")
            continue
        if raw.startswith("7."):
            if chain_id != 31337:
                print("当前链不是本地 Hardhat，不能执行本地二级菜单" if chinese else "current chain is not localhardhat; local submenu is unavailable")
                continue
            sub_raw = raw.split(".", 1)[1]
            if not sub_raw.isdigit():
                print(f"未知选项：{raw}" if chinese else f"unknown choice: {raw}")
                continue
            choice_id = int(sub_raw)
            if not any(local_id == choice_id for local_id, _, _, _ in LOCAL_V4_OPERATIONS):
                print(f"未知选项：{raw}" if chinese else f"unknown choice: {raw}")
                continue
            run_local_v4_operation(choice_id)
            continue
        if raw == "7":
            if chain_id != 31337:
                print("当前链不是本地 Hardhat，不能打开本地二级菜单" if chinese else "current chain is not localhardhat; local menu is unavailable")
                continue
            print("请输入 7.x 直接执行本地 V4 二级操作" if chinese else "enter 7.x to run a local V4 secondary operation directly")
            continue
        if raw in {"3", "4", "5"} and chain_id != 4663:
            print("当前链不是主网，不能执行主网专用操作" if chinese else "current chain is not mainnet; mainnet-only operation is unavailable")
            continue
        action = actions.get(raw)
        if not action:
            print(f"未知选项：{raw}" if chinese else f"unknown choice: {raw}")
            continue
        action(chain_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--menu",
        choices=["english", "chinese"],
        default=os.getenv("FLASHLOAN_MENU_LANG", "english"),
        help="interactive menu language; default english unless FLASHLOAN_MENU_LANG is set",
    )
    parser.add_argument(
        "--chain",
        type=int,
        default=int(os.getenv("FLASHLOAN_DEFAULT_CHAIN_ID", "4663")),
        help="registered chain id; 4663 = Robinhood mainnet (default), 46630 = testnet, 31337 = localhost",
    )
    parser.add_argument(
        "--rpc-url",
        default=None,
        help="RPC URL override for the selected chain",
    )
    parser.add_argument(
        "--choice",
        type=int,
        default=None,
        help="pass a menu choice directly to the testnet V4 menu when using --action testnet-menu",
    )
    parser.add_argument(
        "--action",
        choices=[
            "web",
            "mainnet-preflight",
            "mainnet-bridge-pairs",
            "mainnet-refresh-v4-index",
            "testnet-menu",
            "local-menu",
            "tests",
            "audit",
            "chain-info",
            "menu",
        ],
        default=None,
        help="run a single action instead of opening the interactive menu",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    chinese_menu = args.menu == "chinese"
    configs = supported_chain_configs()
    supported_ids = set(configs)
    if args.chain not in supported_ids:
        valid = ", ".join(str(cid) for cid in sorted(configs))
        print(f"error: --chain must be a registered chain id ({valid}); got {args.chain}", file=sys.stderr)
        return 2
    if args.chain == 31337 and args.action not in {None, "menu", "local-menu", "chain-info", "web", "tests", "audit"}:
        print("error: 31337 only supports local/web/tests/audit actions", file=sys.stderr)
        return 2
    if args.action in {"mainnet-preflight", "mainnet-bridge-pairs", "mainnet-refresh-v4-index"} and args.chain != 4663:
        print(f"error: {args.action} requires chain 4663; got {args.chain}", file=sys.stderr)
        return 2
    if args.action == "testnet-menu" and args.chain != 46630:
        print(f"error: testnet-menu requires chain 46630; got {args.chain}", file=sys.stderr)
        return 2
    if args.action == "local-menu" and args.chain != 31337:
        print(f"error: local-menu requires chain 31337; got {args.chain}", file=sys.stderr)
        return 2
    if args.action:
        if args.action == "web":
            return run_web(args.chain)
        if args.action == "mainnet-preflight":
            return run_mainnet_preflight(args.chain, readiness=True)
        if args.action == "mainnet-bridge-pairs":
            return run_mainnet_bridge_pairs(args.chain)
        if args.action == "mainnet-refresh-v4-index":
            return run_mainnet_refresh_v4_index(args.chain)
        if args.action == "testnet-menu":
            return run_testnet_menu(args.chain, rpc_url=args.rpc_url, choice=args.choice)
        if args.action == "local-menu":
            return run_local_v4_menu()
        if args.action == "tests":
            return run_targeted_tests()
        if args.action == "audit":
            return run_chain_data_audit()
        if args.action == "chain-info":
            return print_chain_info(args.chain)
        return interactive_main(args.chain, "menu", chinese=chinese_menu)
    return interactive_main(args.chain, chinese=chinese_menu)


if __name__ == "__main__":
    raise SystemExit(main())
