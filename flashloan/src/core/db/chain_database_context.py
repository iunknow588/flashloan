"""Thread-local chain database scoping for per-chain SQLite files.

Web requests enter a chain scope from ``chain_id`` query parameters, while
background scanner/review workers enter a scope from the chain they are
processing.  The database path resolver consults the current scope only when
``FLASHLOAN_CHAIN_DATABASES`` is enabled, so existing single-file behaviour
remains intact until the split files are ready.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


CHAIN_DATA_ROOT = Path(__file__).resolve().parents[2] / "chains"
_SQLITE_CACHE_TTL_SECONDS = 30.0
_SQLITE_CACHE: dict[int, tuple[float, str | None]] = {}
_SQLITE_CACHE_LOCK = threading.Lock()
_LOCAL = threading.local()


@dataclass(frozen=True)
class ChainDatabaseScope:
    chain_id: int
    network: str | None = None
    source: str = "scope"


def chain_databases_enabled() -> bool:
    """Return whether per-chain SQLite resolution is active."""

    raw = os.getenv("FLASHLOAN_CHAIN_DATABASES", "").strip().lower()
    return raw in {"1", "true", "yes", "on"} if raw else False


def current_chain_scope() -> ChainDatabaseScope | None:
    """Return the innermost chain scope for the current thread, if any."""

    stack = getattr(_LOCAL, "stack", None)
    if stack:
        return stack[-1]
    return None


def clear_chain_database_context() -> None:
    """Drop all chain scopes for the current thread."""

    if hasattr(_LOCAL, "stack"):
        _LOCAL.stack = []


def _chain_json(chain_id: int) -> dict[str, Any] | None:
    """Read the public chain.json for one chain, with a short TTL cache."""

    wanted = int(chain_id)
    now = time.monotonic()
    with _SQLITE_CACHE_LOCK:
        cached = _SQLITE_CACHE.get(wanted)
        if cached is not None and now - cached[0] < _SQLITE_CACHE_TTL_SECONDS:
            return cached[1]
    registry_path = CHAIN_DATA_ROOT / "registry.json"
    payload: dict[str, Any] | None = None
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        chains = registry.get("chains") if isinstance(registry, dict) else {}
        row = chains.get(str(wanted)) if isinstance(chains, dict) else None
        directory = str((row or {}).get("directory") or "") if isinstance(row, dict) else ""
        if directory:
            chain_path = CHAIN_DATA_ROOT / directory / "chain.json"
            if chain_path.exists():
                raw = json.loads(chain_path.read_text(encoding="utf-8"))
                payload = raw if isinstance(raw, dict) else None
    except (OSError, ValueError, TypeError):
        payload = None
    with _SQLITE_CACHE_LOCK:
        _SQLITE_CACHE[wanted] = (now, payload)
    return payload


def configured_chain_database_name(chain_id: int) -> str | None:
    """Return the chain-configured SQLite filename from public chain.json."""

    payload = _chain_json(int(chain_id))
    if not isinstance(payload, dict):
        return None
    database = payload.get("database")
    if not isinstance(database, dict):
        database = {}
    raw = str(database.get("sqlite_file") or payload.get("sqlite_database") or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.name != raw:
        return None
    return candidate.name


def configured_chain_database_dir(chain_id: int) -> Path | None:
    """Return the configured SQLite directory for a chain.

    ``sqlite_dir`` is resolved relative to the chain's ``chain.json``.  The
    field is intentionally separate from ``sqlite_file`` so existing tools
    that consume the filename remain compatible.
    """

    wanted = int(chain_id)
    payload = _chain_json(wanted)
    if not isinstance(payload, dict):
        return None
    database = payload.get("database")
    if not isinstance(database, dict):
        return None
    raw = str(database.get("sqlite_dir") or "").strip()
    if not raw:
        return None
    registry_path = CHAIN_DATA_ROOT / "registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        chains = registry.get("chains") if isinstance(registry, dict) else {}
        row = chains.get(str(wanted)) if isinstance(chains, dict) else None
        directory = str((row or {}).get("directory") or "") if isinstance(row, dict) else ""
        if not directory:
            return None
        chain_dir = (CHAIN_DATA_ROOT / directory).resolve()
        return (chain_dir / raw).resolve()
    except (OSError, ValueError, TypeError):
        return None


def chain_database_path(chain_id: int, network: str | None = None) -> Path:
    """Return the SQLite path for one chain inside the database root."""

    from core.db.database_path import database_root_dir

    base_dir = database_root_dir().resolve()
    env_path = os.getenv("FLASHLOAN_KV_PATH", "").strip()
    if env_path:
        try:
            base_dir = Path(env_path).expanduser().resolve().parent
        except (OSError, ValueError):
            pass
    configured_dir = configured_chain_database_dir(chain_id)
    if configured_dir is not None:
        base_dir = configured_dir
    filename = configured_chain_database_name(chain_id)
    if not filename:
        filename = f"local_store_{int(chain_id)}.sqlite3"
    return base_dir / filename


@contextmanager
def chain_database_scope(
    chain_id: int,
    *,
    network: str | None = None,
    source: str = "scope",
) -> Iterator[ChainDatabaseScope]:
    """Enter a chain scope for the current thread and restore it afterwards."""

    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _LOCAL.stack = stack
    scope = ChainDatabaseScope(
        chain_id=int(chain_id),
        network=str(network).lower() if network else None,
        source=str(source or "scope"),
    )
    stack.append(scope)
    try:
        yield scope
    finally:
        stack.pop()
        if not stack:
            _LOCAL.stack = []


__all__ = [
    "CHAIN_DATA_ROOT",
    "ChainDatabaseScope",
    "chain_database_path",
    "chain_database_scope",
    "chain_databases_enabled",
    "clear_chain_database_context",
    "configured_chain_database_name",
    "configured_chain_database_dir",
    "current_chain_scope",
]
