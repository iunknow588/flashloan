import os
from pathlib import Path
from typing import Iterable

SENSITIVE_ENV_MARKERS = (
    "PRIVATE_KEY",
    "SECRET",
    "PASSWORD",
    "DATABASE_URL",
    "JWT",
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def disable_database_alias_enabled() -> bool:
    return _env_flag("FLASHLOAN_DISABLE_DATABASE")


def local_json_only_enabled() -> bool:
    return _env_flag("FLASHLOAN_LOCAL_JSON_ONLY") or disable_database_alias_enabled()


def candidate_env_paths(start_path: str | os.PathLike[str]) -> list[Path]:
    path = Path(start_path).resolve()
    directory = path.parent if path.is_file() else path
    paths: list[Path] = []

    for current in [directory, *directory.parents]:
        env_path = current / ".env"
        if env_path.exists() and env_path.is_file():
            paths.append(env_path)
        test_env_path = current / ".env.test"
        if test_env_path.exists() and test_env_path.is_file():
            paths.append(test_env_path)

    return paths


def find_repo_root(start_path: str | os.PathLike[str]) -> Path | None:
    path = Path(start_path).resolve()
    directory = path.parent if path.is_file() else path
    for current in [directory, *directory.parents]:
        if (current / "contract").is_dir() and (current / "flashloan").is_dir():
            return current
    return None


def load_chain_env_files(
    start_path: str | os.PathLike[str],
    chain_env_paths: Iterable[str | os.PathLike[str]] = (),
    *,
    override: bool = False,
) -> list[Path]:
    """Load chain-specific env files first, then the global app_runtime env.

    The repository convention is:
    - each chain keeps its own independent configuration in the chain directory
      (for example ``contract/robinhood-testnet/DEX/.env``);
    - cross-chain/global configuration lives in ``flashloan/app_runtime/.env``.
    """
    loaded: list[Path] = []
    for chain_env_path in chain_env_paths:
        path = Path(chain_env_path).resolve()
        if path.is_file():
            loaded.extend(load_env_files(path, override=override))

    root = find_repo_root(start_path)
    if root is not None:
        global_env = root / "flashloan" / "app_runtime" / ".env"
        if global_env.is_file():
            loaded.extend(load_env_files(global_env, override=override))
    return loaded


def parse_env_lines(lines: Iterable[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export ") :].strip()
        if "=" not in text:
            continue

        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        values[key] = value

    return values


def load_env_files(
    start_path: str | os.PathLike[str],
    *,
    override: bool = False,
) -> list[Path]:
    loaded: list[Path] = []
    for env_path in candidate_env_paths(start_path):
        values = parse_env_lines(env_path.read_text(encoding="utf-8").splitlines())
        is_test_env = env_path.name == ".env.test"
        for key, value in values.items():
            if key == "DATABASE_URL" and local_json_only_enabled():
                continue
            if is_test_env and _is_sensitive_key(key):
                continue
            if is_test_env and str(value or "").strip() == "":
                continue
            if is_test_env:
                os.environ[key] = value
            elif override:
                os.environ[key] = value
            elif key not in os.environ or str(os.environ.get(key) or "").strip() == "":
                os.environ[key] = value
            else:
                continue
        loaded.append(env_path)
    return loaded


def _is_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in SENSITIVE_ENV_MARKERS)


def resolve_env_path(
    name: str,
    default: str | os.PathLike[str],
    base_dir: str | os.PathLike[str],
) -> Path:
    value = os.getenv(name, str(default)).strip()
    path = Path(value)
    return path if path.is_absolute() else Path(base_dir) / path
