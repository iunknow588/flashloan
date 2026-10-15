"""Direction symmetry checks for static database-first build outputs."""

from __future__ import annotations

import json
from typing import Any


def _text(value: object) -> str:
    return str(value or "").strip().lower()


def _json_loads(value: object) -> Any:
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _json_array_loads(value: object) -> list[Any] | None:
    parsed = _json_loads(value if value not in (None, "") else "[]")
    return parsed if isinstance(parsed, list) else None


def _normalized(direction_key: object) -> tuple[str, str] | None:
    text = _text(direction_key)
    parts = [part for part in text.split(":") if part]
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])


def _mirror(direction_key: object) -> str:
    pair = _normalized(direction_key)
    return f"{pair[1]}:{pair[0]}" if pair else ""


def _loop_direction(loop_key: object) -> tuple[str, str, str] | None:
    text = _text(loop_key)
    parts = [part for part in text.split(":") if part]
    if len(parts) == 3:
        return (parts[0], parts[1], parts[2])
    if len(parts) == 4 and parts[0] == "anchor":
        return (parts[1], parts[2], parts[3])
    return None


def direction_symmetry_violations(
    *,
    candidate_pairs: list[dict[str, Any]],
    observation_directions: list[dict[str, Any]],
    loop_keys: list[object],
) -> list[dict[str, Any]]:
    """Return every direction whose mirror has pools while it is empty.

    Direction may choose a different optimal/backup pool, but a physical pair
    either has depth in both directions or in neither.  This catches any stale
    build that accidentally reintroduces a one-sided direction gate.
    """
    violations: list[dict[str, Any]] = []

    candidate_by_direction = {
        _text(row.get("direction_key")): row
        for row in candidate_pairs
        if _text(row.get("direction_key"))
    }
    for direction_key, row in candidate_by_direction.items():
        mirror_key = _mirror(direction_key)
        if not direction_key or not mirror_key:
            continue
        has_pool = bool(
            row.get("yc_optimal_pool_id")
            or row.get("zc_optimal_pool_id")
        )
        mirror_row = candidate_by_direction.get(mirror_key)
        if mirror_row is None:
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_optimal_pool": has_pool,
                    "mirror_has_optimal_pool": None,
                }
            )
            continue
        mirror_has_pool = bool(
            mirror_row.get("yc_optimal_pool_id")
            or mirror_row.get("zc_optimal_pool_id")
        )
        if has_pool != mirror_has_pool:
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_optimal_pool": has_pool,
                    "mirror_has_optimal_pool": mirror_has_pool,
                }
            )
        forward_backup_ids = _json_array_loads(row.get("yc_backup_pool_ids_json"))
        reverse_backup_ids = _json_array_loads(mirror_row.get("zc_backup_pool_ids_json"))
        if forward_backup_ids is None or reverse_backup_ids is None:
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "invalid_backup_json": True,
                }
            )
        elif len(forward_backup_ids) != len(reverse_backup_ids):
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "backup_pool_count": len(forward_backup_ids),
                    "mirror_backup_pool_count": len(reverse_backup_ids),
                }
            )

    observation_by_direction = {
        _text(row.get("direction_key")): row
        for row in observation_directions
        if _text(row.get("direction_key"))
    }
    for direction_key, row in observation_by_direction.items():
        mirror_key = _mirror(direction_key)
        if not direction_key or not mirror_key:
            continue
        has_pool = bool(row.get("has_direct_swap_pool"))
        mirror_row = observation_by_direction.get(mirror_key)
        if mirror_row is None:
            violations.append(
                {
                    "table": "market_observation_yz_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_direct_swap_pool": has_pool,
                    "mirror_has_direct_swap_pool": None,
                }
            )
            continue
        mirror_has_pool = bool(mirror_row.get("has_direct_swap_pool"))
        pool_count = row.get("direct_swap_pool_count")
        mirror_pool_count = mirror_row.get("direct_swap_pool_count")
        if has_pool != mirror_has_pool or pool_count != mirror_pool_count:
            violations.append(
                {
                    "table": "market_observation_yz_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_direct_swap_pool": has_pool,
                    "mirror_has_direct_swap_pool": mirror_has_pool,
                    "direct_swap_pool_count": pool_count,
                    "mirror_direct_swap_pool_count": mirror_pool_count,
                }
            )

    loop_pairs: set[tuple[str, str, str]] = set()
    for loop_key in loop_keys:
        pair = _loop_direction(loop_key)
        if pair:
            loop_pairs.add(pair)
    for pair in sorted(loop_pairs):
        mirror = (pair[0], pair[2], pair[1])
        if mirror not in loop_pairs:
            violations.append(
                {
                    "table": "v4_closed_loops",
                    "direction_key": ":".join(pair[1:]),
                    "mirror_direction_key": ":".join(mirror[1:]),
                    "has_loop": True,
                    "mirror_has_loop": False,
                }
            )
    return violations


__all__ = ["direction_symmetry_violations"]
