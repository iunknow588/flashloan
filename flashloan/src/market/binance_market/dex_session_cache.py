"""Process-local TTL cache for live DEX quote and route-analysis snapshots.

The cache is strictly in-memory. It stores recent quoter results, route
analysis and contract ``keysets/directionsets`` snapshots keyed by chain, pool
identity, direction, amount and the on-chain price fingerprint. A changed
fingerprint or an expired TTL invalidates the entry immediately. The cache
never writes back to SQLite business tables and is not a source of truth for
static pages; it only avoids repeating identical live RPC work inside one
process lifetime.
"""

from __future__ import annotations

from copy import deepcopy
import os
import threading
import time
from typing import Any, Callable


_SESSION_CACHE: dict[tuple[str, tuple[Any, ...]], dict[str, Any]] = {}
_SESSION_CACHE_LOCK = threading.Lock()


def session_cache_ttl_seconds() -> float:
    raw = os.getenv("DEX_SESSION_CACHE_TTL_SECONDS", "5")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 5.0


def session_fingerprint(
    *,
    block_number: int | str | None,
    sqrt_price_x96: int | str | None,
    pool_ids: list[str] | tuple[str, ...] | None = None,
    direction: list[bool] | tuple[bool, ...] | None = None,
) -> tuple[Any, ...]:
    """Build the state fingerprint that invalidates a cached session value."""
    return (
        int(block_number) if block_number not in (None, "") else None,
        str(sqrt_price_x96 or ""),
        tuple(sorted({str(pool_id or "").lower() for pool_id in (pool_ids or []) if str(pool_id or "").strip()})),
        tuple(bool(value) for value in (direction or [])),
    )


def _session_key(kind: str, key_parts: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]]:
    return (str(kind), key_parts)


def cache_session_payload(
    *,
    kind: str,
    key_parts: tuple[Any, ...],
    payload: dict[str, Any] | list[Any] | tuple[Any, ...],
    ttl_seconds: float | None = None,
) -> None:
    """Store a private copy of a quote/route-analysis snapshot."""
    snapshot = {
        "cached_at": time.time(),
        "ttl_seconds": session_cache_ttl_seconds() if ttl_seconds is None else max(0.0, float(ttl_seconds)),
        "payload": deepcopy(payload),
    }
    key = _session_key(kind, key_parts)
    with _SESSION_CACHE_LOCK:
        _SESSION_CACHE[key] = snapshot


def cached_session_payload(
    *,
    kind: str,
    key_parts: tuple[Any, ...],
) -> dict[str, Any] | list[Any] | tuple[Any, ...] | None:
    """Return a deepcopy of a live session snapshot, or ``None``."""
    key = _session_key(kind, key_parts)
    with _SESSION_CACHE_LOCK:
        stored = _SESSION_CACHE.get(key)
    if not isinstance(stored, dict) or "payload" not in stored:
        return None
    raw_ttl = stored.get("ttl_seconds")
    ttl = float(raw_ttl) if raw_ttl is not None else session_cache_ttl_seconds()
    if ttl <= 0 or time.time() - float(stored.get("cached_at") or 0) > ttl:
        with _SESSION_CACHE_LOCK:
            _SESSION_CACHE.pop(key, None)
        return None
    return deepcopy(stored["payload"])


def session_cached_call(
    *,
    kind: str,
    key_parts: tuple[Any, ...],
    builder: Callable[[], dict[str, Any] | list[Any] | tuple[Any, ...]],
    ttl_seconds: float | None = None,
) -> dict[str, Any] | list[Any] | tuple[Any, ...]:
    """Return cached value when fresh, otherwise run and store ``builder``."""
    cached = cached_session_payload(kind=kind, key_parts=key_parts)
    if cached is not None:
        return cached
    payload = builder()
    cache_session_payload(
        kind=kind,
        key_parts=key_parts,
        payload=payload,
        ttl_seconds=ttl_seconds,
    )
    return payload


def clear_dex_session_cache() -> int:
    """Drop all in-memory session entries. Never touches SQLite."""
    with _SESSION_CACHE_LOCK:
        removed = len(_SESSION_CACHE)
        _SESSION_CACHE.clear()
    return removed


__all__ = [
    "_SESSION_CACHE",
    "_SESSION_CACHE_LOCK",
    "cache_session_payload",
    "cached_session_payload",
    "clear_dex_session_cache",
    "session_cache_ttl_seconds",
    "session_cached_call",
    "session_fingerprint",
]
