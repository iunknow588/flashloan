"""Canonical identity helpers for closed-loop runtime rows.

The durable runtime state uses the same address-path key as
``v4_closed_loops.loop_key``.  Unordered symbol groups are only a resident
Rust-worker compatibility identity and must never be used for durable state.
"""

from __future__ import annotations

from typing import Any


def _address_part(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    body = text[2:] if text.startswith("0x") else text
    if len(body) != 40:
        return None
    try:
        bytes.fromhex(body)
    except ValueError:
        return None
    return f"0x{body}"


def _path_parts(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [
        str(value or "").strip().lower()
        for value in values
        if str(value or "").strip()
    ]


def _compact_path(values: Any) -> list[str] | None:
    parts = _path_parts(values)
    if len(parts) == 4:
        if parts[0] != parts[-1]:
            return None
        parts = parts[:3]
        if len(set(parts)) != 3:
            return None
        return parts
    if len(parts) == 3 and len(set(parts)) == 3:
        return parts
    return None


def physical_loop_key_from_addresses(addresses: Any) -> str | None:
    """Return the colon-delimited physical key used by ``v4_closed_loops``."""
    parts = _compact_path(addresses)
    if not parts:
        return None
    addresses = [_address_part(part) for part in parts]
    if any(address is None for address in addresses):
        return None
    return ":".join(address for address in addresses if address is not None)


def normalize_physical_loop_key(value: Any) -> str | None:
    """Normalize a stored address-path key without reordering its direction."""
    text = str(value or "").strip().lower()
    if not text or text.startswith("route:v1:"):
        return None
    parts = [
        part.strip()
        for part in text.replace("|", ":").split(":")
        if part.strip()
    ]
    return physical_loop_key_from_addresses(parts)


def directed_symbol_key_from_symbols(symbols: Any) -> str | None:
    """Return ``anchor|y|z`` with the Y -> Z direction preserved."""
    parts = _compact_path(symbols)
    return "|".join(parts) if parts else None


def worker_group_key_from_symbols(symbols: Any) -> str | None:
    """Return the unordered Y/Z key used only to reuse a Rust worker."""
    parts = _compact_path(symbols)
    if not parts:
        return None
    return "|".join((parts[0], *sorted(parts[1:3])))


def physical_loop_key_from_route(route: dict[str, Any] | None) -> str | None:
    """Return a route's durable physical key from trusted address data."""
    if not isinstance(route, dict):
        return None
    for key in ("loopKey", "loop_key"):
        normalized = normalize_physical_loop_key(route.get(key))
        if normalized:
            return normalized
    for key in ("routeAddresses", "route_addresses", "pathAddresses", "path_addresses"):
        normalized = physical_loop_key_from_addresses(route.get(key))
        if normalized:
            return normalized
    return None


def worker_group_key_from_route(route: dict[str, Any] | None) -> str | None:
    """Return the resident-worker compatibility key for one route."""
    if not isinstance(route, dict):
        return None
    for key in ("routeSymbols", "route_symbols", "symbols", "path_symbols"):
        normalized = worker_group_key_from_symbols(route.get(key))
        if normalized:
            return normalized
    return None


__all__ = [
    "directed_symbol_key_from_symbols",
    "normalize_physical_loop_key",
    "physical_loop_key_from_addresses",
    "physical_loop_key_from_route",
    "worker_group_key_from_route",
    "worker_group_key_from_symbols",
]
