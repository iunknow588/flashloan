#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parent
SRC_BOT_DIR = REPO_ROOT / "flashloan" / "src_bot"
SRC_BOT_RUN = SRC_BOT_DIR / "run.py"
REQUIREMENTS = SRC_BOT_DIR / "requirements.txt"
VENV_DIR = REPO_ROOT / ".venv"


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _in_project_venv() -> bool:
    try:
        return Path(sys.executable).resolve() == _venv_python().resolve()
    except OSError:
        return False


def _install_stamp() -> Path:
    return VENV_DIR / ".requirements-installed"


def _requirements_need_install() -> bool:
    stamp = _install_stamp()
    if not stamp.exists():
        return True
    try:
        return REQUIREMENTS.stat().st_mtime > stamp.stat().st_mtime
    except OSError:
        return True


def ensure_project_venv() -> None:
    python = _venv_python()
    if not python.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    if _requirements_need_install():
        subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
        _install_stamp().write_text("ok\n", encoding="utf-8")


def reexec_inside_project_venv() -> None:
    if _in_project_venv():
        return
    ensure_project_venv()
    python = _venv_python()
    os.execv(str(python), [str(python), str(Path(__file__).resolve())])


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
    reexec_inside_project_venv()
    module = load_src_bot_run_module()
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
