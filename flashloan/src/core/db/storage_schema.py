from __future__ import annotations

from typing import Any

from core.db.storage_common import EXPECTED_SCHEMA_MIGRATION_IDS, SCHEMA_MIGRATIONS
from core.db.database_path import absolute_database_path
from core.local_kv import ensure_store, meta_get, meta_put
from core.db.storage_business_schema import ensure_business_schema


def ensure_database_schema(database_url: str) -> None:
    """Create the local KV store and mark all schema migrations as applied."""

    resolved = absolute_database_path(database_url)
    ensure_store(str(resolved))
    ensure_business_schema(str(resolved))
    applied = meta_get(str(resolved), "schema_migrations") or {}
    now = "local"
    for migration_id, description in SCHEMA_MIGRATIONS:
        applied[str(migration_id)] = {
            "migration_id": str(migration_id),
            "description": str(description),
            "applied_at": now,
        }
    meta_put(str(resolved), "schema_migrations", applied)


def ensure_local_store_schema(database_url: str) -> None:
    """Alias for ``ensure_database_schema`` using local KV naming."""

    ensure_database_schema(database_url)


def record_schema_migrations(cursor) -> None:
    """Compatibility no-op; migrations are recorded in ensure_database_schema."""

    return None


def load_schema_migrations(database_url: str) -> list[dict[str, Any]]:
    """Return applied local schema migrations."""

    resolved = absolute_database_path(database_url)
    ensure_database_schema(str(resolved))
    applied = meta_get(str(resolved), "schema_migrations") or {}
    return [dict(value) for value in applied.values()]


def schema_migration_report(database_url: str) -> dict[str, Any]:
    """Return a machine-readable migration health report without rewriting rows."""

    resolved = absolute_database_path(database_url)
    ensure_database_schema(str(resolved))
    applied = meta_get(str(resolved), "schema_migrations") or {}
    applied_ids = {str(key) for key in applied}
    expected_ids = set(EXPECTED_SCHEMA_MIGRATION_IDS)
    return {
        "database": str(resolved),
        "expected_count": len(expected_ids),
        "applied_count": len(applied_ids),
        "missing": sorted(expected_ids - applied_ids),
        "extra": sorted(applied_ids - expected_ids),
        "complete": applied_ids >= expected_ids,
    }


def ensure_deduplication_constraints(cursor) -> None:
    return None


def ensure_schema_columns(cursor) -> None:
    return None


def ensure_liquidation_market_indexes(cursor) -> None:
    return None


def ensure_liquidation_composite_identity_constraints(cursor) -> None:
    return None


def ensure_liquidation_discovery_scan_constraints(cursor) -> None:
    return None


def ensure_liquidation_scan_config_constraints(cursor) -> None:
    return None


__all__ = [
    "EXPECTED_SCHEMA_MIGRATION_IDS",
    "ensure_database_schema",
    "ensure_local_store_schema",
    "load_schema_migrations",
    "record_schema_migrations",
    "schema_migration_report",
]
