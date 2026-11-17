"""Canonical identity helpers for closed-loop runtime rows.

The durable runtime state uses the same address-path key as
``v4_closed_loops.loop_key``.  Worker identities preserve the same direction
so every closed-loop direction owns an independent runtime state machine.
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
    """Return the direction-preserving resident-worker key for one route."""
    if not isinstance(route, dict):
        return None
    for key in ("routeSymbols", "route_symbols", "symbols", "path_symbols"):
        normalized = worker_group_key_from_symbols(route.get(key))
        if normalized:
            return normalized
    return None


def _cycle_path_parts(value: Any) -> list[str] | None:
    """Return three unique directed-cycle parts from a key or path.

    Accepts a 3-part key, a closed 4-part path, or a 3/4-element list and
    preserves the original order.  The caller selects the separator; this
    helper never decides that a short key is invalid.
    """
    if isinstance(value, (list, tuple)):
        parts = _path_parts(value)
        if len(parts) == 4 and parts[0] == parts[-1]:
            parts = parts[:3]
        if len(parts) == 3 and len(set(parts)) == 3:
            return parts
        return None
    text = str(value or "").strip().lower()
    if not text:
        return None
    parts = [
        str(item).strip()
        for item in text.replace("|", ":").split(":")
        if str(item).strip()
    ]
    if len(parts) == 4 and parts[0] == parts[-1]:
        parts = parts[:3]
    if len(parts) == 3 and len(set(parts)) == 3:
        return parts
    return None


def directed_cycle_rotations(value: Any, *, separator: str = ":") -> tuple[str, ...]:
    """Return the three same-direction rotations ``A:B:C``, ``B:C:A``, ``C:A:B``.

    The reverse direction ``A:C:B`` never appears in the result, so a forward
    and a reverse state machine stay separate while any rotation of the same
    directed triangle maps to the same identity.
    """
    parts = _cycle_path_parts(value)
    if not parts:
        return ()
    return tuple(
        separator.join(parts[offset:] + parts[:offset])
        for offset in range(3)
    )


def canonical_directed_cycle_key(value: Any, *, separator: str = ":") -> str | None:
    """Return one deterministic rotation that represents the directed cycle."""
    rotations = directed_cycle_rotations(value, separator=separator)
    return min(rotations) if rotations else None


def directed_cycle_keys_match(
    left: Any,
    right: Any,
    *,
    separator: str = ":",
) -> bool:
    """Return True when two keys are rotations of the same directed cycle."""
    left_rotations = directed_cycle_rotations(left, separator=separator)
    right_rotations = directed_cycle_rotations(right, separator=separator)
    if not left_rotations or not right_rotations:
        return False
    return right_rotations[0] in left_rotations




def resolve_registered_group_key(
    requested_symbols: Any,
    registered_keys: Any,
) -> str | None:
    """Find the registered worker key that serves the requested route.

    The closed-loop library stores every loop ETH-anchored (``ETH -> Y -> Z
    -> ETH``), while the scanner registers its resident worker under the
    route order it discovered, which is a *rotation* of the same directed
    cycle (``Z -> ETH -> Y``). The worker pool keys its state machine by the
    exact string it was registered with, so a lookup by the request's own
    order can miss the worker even though it addresses the very same
    directed cycle.

    A directed cycle and its rotations are the same closed loop; the
    reverse direction is not. This helper therefore matches a requested
    route against the registered keys by rotation identity and returns the
    key the worker actually runs under.
    """
    requested = _compact_path(requested_symbols)
    if not requested:
        return None
    candidates: list[str] = []
    for value in registered_keys or []:
        key = str(value or "").strip().lower()
        if key and key not in candidates:
            candidates.append(key)
    if not candidates:
        return None
    requested_rotations = {
        "|".join(requested[offset:] + requested[:offset])
        for offset in range(3)
    }
    for candidate in candidates:
        parts = candidate.replace(":", "|").split("|")
        if len(parts) != 3:
            continue
        candidate_rotations = {
            "|".join(parts[offset:] + parts[:offset])
            for offset in range(3)
        }
        if requested_rotations & candidate_rotations:
            return candidate
    return None


__all__ = [
    "canonical_directed_cycle_key",
    "directed_cycle_keys_match",
    "directed_cycle_rotations",
    "directed_symbol_key_from_symbols",
    "normalize_physical_loop_key",
    "physical_loop_key_from_addresses",
    "physical_loop_key_from_route",
    "resolve_registered_group_key",
    "worker_group_key_from_route",
    "worker_group_key_from_symbols",
]
