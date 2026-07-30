from datetime import datetime, timezone

OBSERVER_ADVISORY_LOCK_ID = 2026072801
SCHEMA_MIGRATIONS = (
    (
        "20260730_liquidation_runtime_schema",
        "Split liquidation account registry, discovery progress, and health scan history.",
    ),
    (
        "20260730_liquidation_execution_attempts",
        "Record liquidation execution attempts, blocking reasons, preflight data, and receipts.",
    ),
)
EXPECTED_SCHEMA_MIGRATION_IDS = tuple(migration_id for migration_id, _ in SCHEMA_MIGRATIONS)


def require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install psycopg[binary] or run pip install -r requirements.txt"
        ) from exc
    return psycopg


def utc_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")
