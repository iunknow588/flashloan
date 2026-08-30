from __future__ import annotations

from copy import deepcopy
import threading
from typing import Any


_DEX_SURFACE_CACHE: dict[tuple[str, int | None, tuple[str, ...]], dict[str, Any]] = {}
_DEX_SURFACE_CACHE_LOCK = threading.Lock()


def _protocols_key(protocols: list[str] | None) -> tuple[str, ...]:
    return tuple(sorted({str(protocol or "").strip().lower() for protocol in (protocols or []) if str(protocol or "").strip()}))


def _surface_cache_key(
    surface: str,
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[str, int | None, tuple[str, ...]]:
    return (surface, chain_id, _protocols_key(protocols))


def _cache_surface_payload(
    surface: str,
    payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool = False,
) -> None:
    key = _surface_cache_key(surface, chain_id=chain_id, protocols=protocols)
    with _DEX_SURFACE_CACHE_LOCK:
        _DEX_SURFACE_CACHE[key] = deepcopy(payload)


def _cached_surface_payload(
    surface: str,
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool = False,
) -> dict[str, Any] | None:
    key = _surface_cache_key(surface, chain_id=chain_id, protocols=protocols)
    with _DEX_SURFACE_CACHE_LOCK:
        payload = _DEX_SURFACE_CACHE.get(key)
    return deepcopy(payload) if payload is not None else None


def _surface_payload_has_framework_data(surface: str, payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    if surface == "dex_market_observation":
        return any(
            payload.get(key)
            for key in (
                "closedLoopRoutes",
                "observationSequences",
                "candidateObservationGroups",
                "directPools",
                "indirectRoutes",
            )
        )
    if surface == "dex_overview":
        return bool(payload.get("arbitragePaths") or payload.get("watchPairs"))
    if surface == "dex_quote_routes":
        return bool(payload.get("routes"))
    if surface == "dex_submission_review":
        return bool(payload.get("attempts") or payload.get("groups"))
    return True


def _cached_framework_surface_payload(
    surface: str,
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool = False,
) -> dict[str, Any] | None:
    payload = _cached_surface_payload(
        surface,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if _surface_payload_has_framework_data(surface, payload):
        return payload
    return None


__all__ = [
    "_DEX_SURFACE_CACHE",
    "_DEX_SURFACE_CACHE_LOCK",
    "_cache_surface_payload",
    "_cached_framework_surface_payload",
    "_cached_surface_payload",
    "_protocols_key",
    "_surface_cache_key",
    "_surface_payload_has_framework_data",
]
