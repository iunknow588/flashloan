"""Central database path resolution for initialisation and page reads.

The resolution rule is intentionally single-sourced here: an explicit
``database_url`` wins, then the per-chain SQLite file derived from the current
chain scope, then ``FLASHLOAN_KV_PATH``, then the default chain's SQLite file
from the chain registry.  The old cross-chain ``flashloan/db/local_store.sqlite3``
is no longer the implicit default.  Relative database URLs resolve against the
current working directory so they can never accidentally land in a nested
runtime directory.  ``core.local_kv.store_path`` and the other storage helpers
delegate here.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.db.chain_database_context import (
    chain_database_path,
    current_chain_scope,
)


def database_root_dir() -> Path:
    """Return the single local SQLite root: ``flashloan/db``."""

    # database_path.py lives at flashloan/src/core/db/.
    return Path(__file__).resolve().parents[3] / "db"


def default_chain_database_path() -> Path:
    """Return the SQLite file for the registry-default DEX chain."""

    from core.chain_data_registry import default_chain_scope

    scope = default_chain_scope("dex_arbitrage")
    chain_id = int(scope.get("chain_id") or 0)
    network = str(scope.get("network") or "").strip()
    if not chain_id:
        raise ValueError("default DEX chain has no chain_id")
    return chain_database_path(chain_id, network).resolve()


def default_database_path() -> Path:
    """Return the default chain's SQLite path, with a legacy fallback."""

    try:
        return default_chain_database_path()
    except Exception:
        return database_root_dir() / "local_store.sqlite3"


def resolve_database_path(database_url: str | os.PathLike[str] | None = None) -> Path:
    """Return the single authoritative SQLite path for this process."""

    if database_url:
        resolved = Path(database_url).expanduser()
        return resolved.resolve()
    scope = current_chain_scope()
    if scope is not None:
        return chain_database_path(scope.chain_id, scope.network).resolve()
    env_path = os.getenv("FLASHLOAN_KV_PATH", "").strip()
    if env_path:
        resolved = Path(env_path).expanduser()
        return resolved.resolve()
    return default_database_path()


def absolute_database_path(
    database_url: str | os.PathLike[str] | None = None,
) -> Path:
    """Backward-compatible alias for ``resolve_database_path``."""

    return resolve_database_path(database_url)


def absolute_database_url(database_url: str | os.PathLike[str] | None = None) -> str:
    return str(resolve_database_path(database_url))


__all__ = [
    "absolute_database_path",
    "absolute_database_url",
    "default_database_path",
    "database_root_dir",
    "resolve_database_path",
]
