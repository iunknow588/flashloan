import importlib.util
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SRC_ROOT.parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replit_config_runs_active_src_bot_entrypoint():
    config = (REPO_ROOT / ".replit").read_text(encoding="utf-8")
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    launcher = _load_module("test_run_replit", REPO_ROOT / "run_replit.py")

    assert 'run = "python3 run_replit.py"' in config
    assert 'modules = ["python-3.11"]' in config
    assert 'language = "python3"' in config
    assert 'requiredFiles = [".replit", "replit.nix"]' in config
    assert 'run = ["python3", "run_replit.py"]' in config
    assert ".replit" not in [line.strip() for line in gitignore]
    assert ".venv/" in [line.strip() for line in gitignore]
    assert ".node-dependencies-installed" in [line.strip() for line in gitignore]
    assert launcher.SRC_BOT_DIR == SRC_ROOT
    assert launcher.SRC_BOT_RUN == SRC_ROOT / "run.py"
    assert launcher.VENV_DIR == REPO_ROOT / ".venv"
    assert launcher.REQUIREMENTS == SRC_ROOT / "requirements.txt"
    assert launcher.COW_NODE_ADAPTER_DIR == SRC_ROOT / "cow_flashloan" / "node_adapter"
    assert launcher.COW_NODE_PACKAGE_LOCK == launcher.COW_NODE_ADAPTER_DIR / "package-lock.json"


def test_replit_nix_installs_python_runtime():
    config = (REPO_ROOT / "replit.nix").read_text(encoding="utf-8")

    assert "pkgs.python311" in config


def test_legacy_srcs_dex_launcher_delegates_to_active_src_bot_entrypoint():
    legacy_launcher = _load_module(
        "test_legacy_srcs_dex_run",
        REPO_ROOT / "flashloan" / "srcs_dex" / "run.py",
    )

    assert legacy_launcher.SRC_BOT_DIR == SRC_ROOT
    assert legacy_launcher.SRC_BOT_RUN == SRC_ROOT / "run.py"
