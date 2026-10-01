import json
from typing import Any

from core.market_observation_compact import (
    _compact_candidate_group,
    _compact_observation_pair,
    _compact_observation_sequence,
    _pool_registry_key,
)
from core.market_observation_io import (
    DICT_KEYS,
    LIST_KEYS,
    PERSISTED_DICT_KEYS,
    PERSISTED_LIST_KEYS,
    _stamp,
    _text,
)


def _row_identity(row: dict[str, Any]) -> str:
    for key in (
        "routeKey",
        "pathText",
        "addressPair",
        "symbolPair",
        "anchorSymbol",
        "pool",
        "poolId",
        "address",
    ):
        value = _text(row.get(key))
        if value:
            return f"{key}:{value.lower()}"
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _merge_rows(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in [*existing, *incoming]:
        if not isinstance(row, dict):
            continue
        key = _row_identity(row)
        if key not in merged:
            order.append(key)
        merged[key] = {**merged.get(key, {}), **row}
    return [merged[key] for key in order]


def _merge_list_values(existing: list[Any], incoming: list[Any], *, key: str) -> list[Any]:
    if key == "candidateObservationGroups":
        existing = [
            _compact_candidate_group(row)
            for row in existing
            if isinstance(row, dict)
        ]
        incoming = [
            _compact_candidate_group(row)
            for row in incoming
            if isinstance(row, dict)
        ]
        return _merge_rows(existing, incoming)
    if key == "observationSequences":
        existing = [
            _compact_observation_sequence(row)
            for row in existing
            if isinstance(row, dict)
        ]
        incoming = [
            _compact_observation_sequence(row)
            for row in incoming
            if isinstance(row, dict)
        ]
        return _merge_rows(existing, incoming)
    if key == "chainSpecificObservationPairs":
        existing = [
            _compact_observation_pair(row)
            for row in existing
            if isinstance(row, dict)
        ]
        incoming = [
            _compact_observation_pair(row)
            for row in incoming
            if isinstance(row, dict)
        ]
        return _merge_rows(existing, incoming)
    if any(isinstance(row, dict) for row in [*existing, *incoming]):
        return _merge_rows(
            [row for row in existing if isinstance(row, dict)],
            [row for row in incoming if isinstance(row, dict)],
        )
    values: list[Any] = []
    seen: set[str] = set()
    for value in [*existing, *incoming]:
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        values.append(value)
    return values


def _list_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(payload.get(key) or [])
        for key in LIST_KEYS
        if isinstance(payload.get(key), list)
    }


def _merge_pool_registry(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for row in [*(existing or {}).values(), *(incoming or {}).values()]:
        if not isinstance(row, dict):
            continue
        key = _pool_registry_key(row)
        if key:
            merged[key] = {**merged.get(key, {}), **row}
    return merged


def merge_market_observation_static(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {
        **existing,
        **incoming,
        "first_generated_at": existing.get("first_generated_at") or existing.get("generated_at") or incoming.get("generated_at"),
        "generated_at": _stamp(),
        "refresh_mode": "incremental",
        "previous_path": existing.get("path"),
    }
    for key in LIST_KEYS:
        if key not in PERSISTED_LIST_KEYS:
            merged.pop(key, None)
    for key in DICT_KEYS:
        if key not in PERSISTED_DICT_KEYS:
            merged.pop(key, None)
    for key in PERSISTED_LIST_KEYS:
        existing_rows = existing.get(key) if isinstance(existing.get(key), list) else []
        incoming_rows = incoming.get(key) if isinstance(incoming.get(key), list) else []
        if existing_rows or incoming_rows:
            merged[key] = _merge_list_values(existing_rows, incoming_rows, key=key)
    if existing.get("poolRegistry") or incoming.get("poolRegistry"):
        merged["poolRegistry"] = _merge_pool_registry(
            existing.get("poolRegistry") if isinstance(existing.get("poolRegistry"), dict) else {},
            incoming.get("poolRegistry") if isinstance(incoming.get("poolRegistry"), dict) else {},
        )
    merged["list_counts"] = _list_counts(merged)
    return merged
