"""Versioned, reversible migration helpers for JSON-like records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


Migration = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class SchemaMigration:
    from_version: int
    to_version: int
    forward: Migration
    backward: Migration


def migrate_record(record: Mapping[str, Any], *, target_version: int, migrations: tuple[SchemaMigration, ...]) -> dict[str, Any]:
    current = int(record.get("schema_version", 1))
    result = dict(record)
    if current == target_version:
        return result
    direction = 1 if target_version > current else -1
    while current != target_version:
        candidates = [m for m in migrations if (m.from_version == current if direction == 1 else m.to_version == current)]
        if len(candidates) != 1:
            raise ValueError(f"no unique migration path from schema {current} toward {target_version}")
        migration = candidates[0]
        result = migration.forward(result) if direction == 1 else migration.backward(result)
        current = migration.to_version if direction == 1 else migration.from_version
        result["schema_version"] = current
    return result


def migration_fixture_v1_to_v2() -> SchemaMigration:
    def forward(record: dict[str, Any]) -> dict[str, Any]:
        result = dict(record)
        if "market" in result and "market_id" not in result:
            result["market_id"] = result.pop("market")
        return result

    def backward(record: dict[str, Any]) -> dict[str, Any]:
        result = dict(record)
        if "market_id" in result and "market" not in result:
            result["market"] = result.pop("market_id")
        return result

    return SchemaMigration(1, 2, forward, backward)


__all__ = ["SchemaMigration", "migrate_record", "migration_fixture_v1_to_v2"]
