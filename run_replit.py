#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parent
SRC_BOT_DIR = REPO_ROOT / "flashloan" / "src_bot"
SRC_BOT_RUN = SRC_BOT_DIR / "run.py"
REQUIREMENTS = SRC_BOT_DIR / "requirements.txt"
VENV_DIR = REPO_ROOT / ".venv"
COW_NODE_ADAPTER_DIR = SRC_BOT_DIR / "cow_flashloan" / "node_adapter"
COW_NODE_PACKAGE_LOCK = COW_NODE_ADAPTER_DIR / "package-lock.json"
COW_NODE_MODULES = COW_NODE_ADAPTER_DIR / "node_modules"
REQUIRED_PYTHON_IMPORTS = ("flask", "psycopg", "psycopg_pool", "web3", "websockets", "dotenv")


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


def _pip_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PIP_USER"] = "0"
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONUSERBASE", None)
    return env


def _venv_dependencies_available(python: Path) -> bool:
    imports = "; ".join(f"import {name}" for name in REQUIRED_PYTHON_IMPORTS)
    result = subprocess.run(
        [str(python), "-c", imports],
        env=_pip_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _install_requirements(python: Path) -> None:
    print(f"Installing Python dependencies into {python} from {REQUIREMENTS}", flush=True)
    subprocess.check_call(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-user",
            "-r",
            str(REQUIREMENTS),
        ],
        env=_pip_env(),
    )


def ensure_project_venv() -> bool:
    python = _venv_python()
    created = False
    if not python.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
        created = True
    if _requirements_need_install() or not _venv_dependencies_available(python):
        _install_requirements(python)
        if not _venv_dependencies_available(python):
            raise RuntimeError(
                "Project virtual environment was prepared, but required Python packages are still missing. "
                f"Run `{python} -m pip install --no-user -r {REQUIREMENTS}` and retry."
            )
        _install_stamp().write_text("ok\n", encoding="utf-8")
        return True
    return created


def _missing_runtime_imports() -> list[str]:
    missing: list[str] = []
    for module_name in REQUIRED_PYTHON_IMPORTS:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(module_name)
    return missing


def ensure_runtime_dependencies_loaded() -> None:
    missing = _missing_runtime_imports()
    if not missing:
        return
    python = _venv_python()
    print(
        "Current Python process is missing required packages: "
        f"{', '.join(missing)}. executable={sys.executable}; venv_python={python}",
        file=sys.stderr,
        flush=True,
    )
    _install_requirements(python)
    os.execv(str(python), [str(python), str(Path(__file__).resolve())])


def _node_install_stamp() -> Path:
    return COW_NODE_ADAPTER_DIR / ".node-dependencies-installed"


def _node_dependencies_need_install() -> bool:
    stamp = _node_install_stamp()
    if not COW_NODE_MODULES.exists() or not stamp.exists():
        return True
    try:
        return COW_NODE_PACKAGE_LOCK.stat().st_mtime > stamp.stat().st_mtime
    except OSError:
        return True


def ensure_cow_node_adapter_dependencies() -> None:
    if not COW_NODE_ADAPTER_DIR.exists():
        return
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm is required to install CoW SDK dependencies")
    if _node_dependencies_need_install():
        subprocess.check_call([npm, "install"], cwd=str(COW_NODE_ADAPTER_DIR))
        _node_install_stamp().write_text("ok\n", encoding="utf-8")


def reexec_inside_project_venv() -> None:
    python = _venv_python()
    prepared = ensure_project_venv()
    if _in_project_venv():
        if prepared:
            os.execv(str(python), [str(python), str(Path(__file__).resolve())])
        return
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
    ensure_runtime_dependencies_loaded()
    ensure_cow_node_adapter_dependencies()
    module = load_src_bot_run_module()
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
