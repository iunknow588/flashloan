"""Central database path resolution for initialisation and page reads.

All new initialisation/refresh tooling should use ``absolute_database_path`` so
relative values can never accidentally land in a nested runtime directory.
Relative paths are resolved against the current working directory; the old
``core.local_kv.store_path`` remains for legacy callers.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.runtime_paths import resolve_runtime_dir


def default_database_path() -> Path:
    runtime_dir = resolve_runtime_dir()
    return runtime_dir / "local_store.sqlite3"


def absolute_database_path(database_url: str | os.PathLike[str] | None = None) -> Path:
    if database_url:
        resolved = Path(database_url).expanduser()
        return resolved.resolve()
    env_path = os.getenv("FLASHLOAN_KV_PATH", "").strip()
    if env_path:
        resolved = Path(env_path).expanduser()
        return resolved.resolve()
    return default_database_path()


def absolute_database_url(database_url: str | os.PathLike[str] | None = None) -> str:
    return str(absolute_database_path(database_url))


__all__ = [
    "absolute_database_path",
    "absolute_database_url",
    "default_database_path",
]
