#!/usr/bin/env python3
"""test-token_create primary launcher: multi-chain Chinese menu."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIRS = [
    ROOT / "scripts" / "tool",
    Path(__file__).resolve().parent / "scripts" / "tool",
]
for TOOL_DIR in reversed(TOOL_DIRS):
    if str(TOOL_DIR) not in sys.path:
        sys.path.insert(0, str(TOOL_DIR))

os.environ.setdefault("FLASHLOAN_MENU_LANG", "chinese")

from multi_chain_cli import main as cli_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(cli_main())
