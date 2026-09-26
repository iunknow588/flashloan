from __future__ import annotations

import os
from pathlib import Path

from core.env_loader import resolve_env_path

SRC_ROOT = Path(__file__).resolve().parents[1]


def default_runtime_dir() -> Path:
    return SRC_ROOT / "runtime"


def resolve_runtime_dir(*, base_dir: Path | str | None = None) -> Path:
    base = Path(base_dir) if base_dir is not None else SRC_ROOT
    return resolve_env_path("FLASHLOAN_RUNTIME_DIR", default_runtime_dir(), base)


def resolve_log_dir(
    *,
    runtime_dir: Path | None = None,
    base_dir: Path | str | None = None,
) -> Path:
    if runtime_dir is None:
        runtime_dir = resolve_runtime_dir(base_dir=base_dir)
    base = Path(base_dir) if base_dir is not None else runtime_dir
    return resolve_env_path("FLASHLOAN_LOG_DIR", runtime_dir / "logs", base)
