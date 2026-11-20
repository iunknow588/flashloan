"""Canonical identity helpers for closed-loop runtime rows.

The durable runtime state uses the same address-path key as
``v4_closed_loops.loop_key``.  Worker identities preserve the same direction
so every closed-loop direction owns an independent runtime state machine.

Identity arbitration is **not** a Python responsibility (MIO-7 / AC-214).
The closed-loop Manager owns the identity of every loop, and the kernel
publishes it.  This module therefore offers two strictly separated kinds of
helper:

* **Format normalization** -- lower-casing, ``0x`` prefixing and dropping the
  closing anchor of a route path.  These functions only make two strings
  comparable; they never decide that two different loops are the same.
* **Exact resolution** -- an ordered, exact lookup against a published set.
  A miss is a miss.  Nothing here rotates, re-anchors, or otherwise
  re-orders a token path to make a lookup succeed.

The rotation helpers that used to live here (``directed_cycle_rotations``,
``canonical_directed_cycle_key``, ``directed_cycle_keys_match``) were
deleted on purpose: they made the healer borrow the identity, capacity and
pool set of a *different* closed loop.  The task plan records the removal
and the regression that replaces it.
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
    """Return the direction-preserving worker key ``anchor|Y|Z``."""
    return directed_symbol_key_from_symbols(symbols)


def physical_loop_key_from_route(route: dict[str, Any] | None) -> str | None:
    """Return the **published** physical key a route carries.

    Only the identity fields are read.  The route addresses are *not* a
    fallback here: assembling a key out of them would invent an identity
    that the closed-loop Manager never published, and that loop's capacity,
    pool set and review threshold with it (MIO-7 / AC-214).

    Use :func:`physical_route_key_from_route` when the caller needs the
    **route's own** address path, which is a routing concern and not a
    closed-loop identity.
    """
    if not isinstance(route, dict):
        return None
    return normalize_physical_loop_key(route.get("loopKey"))


def physical_route_key_from_route(route: dict[str, Any] | None) -> str | None:
    """Return the ordered address path of one route.

    This is the **route's own** identity: the ordered hop addresses the
    scanner discovered or the kernel config describes.  It is used to match
    a route against the route config that belongs to it, and it is *not* the
    closed-loop identity the Manager owns.

    The published key wins when the route carries one, so a route that was
    already identified keeps that identity.
    """
    if not isinstance(route, dict):
        return None
    published = normalize_physical_loop_key(route.get("loopKey"))
    if published:
        return published
    for key in ("routeAddresses", "route_addresses", "pathAddresses", "path_addresses"):
        normalized = physical_loop_key_from_addresses(route.get(key))
        if normalized:
            return normalized
    return None


def physical_loop_key_from_database_row(row: dict[str, Any] | None) -> str | None:
    """Read the stored physical loop key from one database row."""
    if not isinstance(row, dict):
        return None
    return normalize_physical_loop_key(row.get("loop_key"))


def worker_group_key_from_route(route: dict[str, Any] | None) -> str | None:
    """Return the direction-preserving resident-worker key for one route."""
    if not isinstance(route, dict):
        return None
    for key in ("routeSymbols", "route_symbols", "symbols", "path_symbols"):
        normalized = worker_group_key_from_symbols(route.get(key))
        if normalized:
            return normalized
    return None


def resolve_registered_group_key(
    requested_symbols: Any,
    registered_keys: Any,
) -> str | None:
    """Find the registered worker key that serves the requested route.

    The lookup is **exact and ordered**.  The worker pool keys its state
    machine by the exact string the worker was registered with, and the
    scanner registers a worker under the route order it actually
    discovered.  A request therefore only addresses that worker when its
    own ordered token path equals the registered key.

    A different starting point of the same triangle is a **different**
    worker with a **different** state machine and a **different** closed
    loop identity, so it must not borrow the registered key of its
    rotation.  A request that matches no registered key is a miss: the
    caller reports it (``closed_loop_group_not_found``) instead of
    guessing.

    This replaces the rotation-aware resolver that was deleted with
    TASK-149 (AC-214).  The identity of a closed loop is published by the
    closed-loop Manager and reaches the runtime through the kernel; the
    runtime never re-derives it.
    """
    requested = _compact_path(requested_symbols)
    if not requested:
        return None
    requested_key = "|".join(requested)
    for value in registered_keys or []:
        key = str(value or "").strip().lower()
        if not key:
            continue
        parts = [
            part.strip()
            for part in key.replace(":", "|").split("|")
            if part.strip()
        ]
        if len(parts) != 3:
            continue
        if "|".join(parts) == requested_key:
            return key
    return None


__all__ = [
    "directed_symbol_key_from_symbols",
    "normalize_physical_loop_key",
    "physical_loop_key_from_addresses",
    "physical_loop_key_from_database_row",
    "physical_loop_key_from_route",
    "physical_route_key_from_route",
    "resolve_registered_group_key",
    "worker_group_key_from_route",
    "worker_group_key_from_symbols",
]
