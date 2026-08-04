import os
from pathlib import Path
from typing import Iterable


def candidate_env_paths(start_path: str | os.PathLike[str]) -> list[Path]:
    path = Path(start_path).resolve()
    directory = path.parent if path.is_file() else path
    paths: list[Path] = []

    for current in [directory, *directory.parents]:
        env_path = current / ".env"
        if env_path.exists() and env_path.is_file():
            paths.append(env_path)

    return paths


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
        for key, value in values.items():
            if override:
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)
        loaded.append(env_path)
    return loaded


def resolve_env_path(
    name: str,
    default: str | os.PathLike[str],
    base_dir: str | os.PathLike[str],
) -> Path:
    value = os.getenv(name, str(default)).strip()
    path = Path(value)
    return path if path.is_absolute() else Path(base_dir) / path
