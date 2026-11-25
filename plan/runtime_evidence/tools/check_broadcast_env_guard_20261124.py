#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the official-V4 broadcast env guard still bites after the relaxation.

The guard was narrowed: the **mainnet official-V4 arb** switch may now be
enabled in the operator runtime env (the shipped .env.example template has
carried it as true since the mainnet broadcast was opened by default).
Every other combination must still be rejected.

The check never touches the real env files: it copies them into a temporary
directory, points the guard's file list at the copies, and asserts the
verdict for each combination.

Usage:
    python check_broadcast_env_guard_20261124.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"E:\2026OPC大赛\flashLoan"
MODULE_PATH = os.path.join(
    ROOT,
    r"flashloan\app_runtime\tests\unit\test_robinhood_official_v4_config_consistency.py",
)
MAINNET_KEY = "ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"
COMPOSER_KEY = "ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST"


def load_module():
    spec = importlib.util.spec_from_file_location("official_v4_guard", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verdict(module, operator, shared_test, testnet_dex):
    """Return the offenders the guard reports for the given env contents."""
    module.SHARED_ENV_FILES = [Path(operator), Path(shared_test), Path(testnet_dex)]
    module.OPERATOR_RUNTIME_ENV = Path(operator)
    try:
        module.test_shared_runtime_env_never_enables_forbidden_official_v4_broadcast_gates()
    except AssertionError as error:
        return "REJECTED"
    return "ACCEPTED"


def main():
    module = load_module()
    failures = []
    cases = [
        # (operator .env, .env.test, testnet DEX .env, expected verdict, label)
        ("", "", "", "ACCEPTED", "干净配置"),
        (MAINNET_KEY + "=true", "", "", "ACCEPTED", "operator .env 放开主网 arb（**新允许**）"),
        ("", MAINNET_KEY + "=true", "", "REJECTED", ".env.test 放开主网 arb"),
        ("", "", MAINNET_KEY + "=true", "REJECTED", "测试网 DEX .env 放开主网 arb"),
        (COMPOSER_KEY + "=true", "", "", "REJECTED", "operator .env 放开 composer"),
        ("", COMPOSER_KEY + "=true", "", "REJECTED", ".env.test 放开 composer"),
    ]
    workspace = tempfile.mkdtemp(prefix="broadcast_guard_")
    try:
        operator = os.path.join(workspace, "operator.env")
        shared_test = os.path.join(workspace, "shared.env.test")
        testnet_dex = os.path.join(workspace, "testnet_dex.env")
        for label, (op_text, test_text, dex_text, expected, title) in enumerate(cases):
            with open(operator, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(op_text + "\n")
            with open(shared_test, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(test_text + "\n")
            with open(testnet_dex, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(dex_text + "\n")
            actual = verdict(module, operator, shared_test, testnet_dex)
            ok = actual == expected
            print("%s %-58s 期望=%-8s 实际=%s" % ("OK " if ok else "BAD", title, expected, actual))
            if not ok:
                failures.append(title)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    if failures:
        raise SystemExit("[guard] unexpected verdicts: %s" % ", ".join(failures))
    print("[guard] every combination keeps the expected verdict")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
