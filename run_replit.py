#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parent
SRC_BOT_DIR = REPO_ROOT / "flashloan" / "src_bot"
SRC_BOT_RUN = SRC_BOT_DIR / "run.py"


def load_src_bot_run_module() -> ModuleType:
    if not SRC_BOT_RUN.exists():
        raise RuntimeError(f"active runtime entrypoint missing: {SRC_BOT_RUN}")
    if str(SRC_BOT_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_BOT_DIR))
    spec = importlib.util.spec_from_file_location("flashloan_src_bot_run", SRC_BOT_RUN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load active runtime entrypoint: {SRC_BOT_RUN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_src_bot_run_module()
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
