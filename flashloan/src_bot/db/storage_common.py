from datetime import datetime, timezone

OBSERVER_ADVISORY_LOCK_ID = 2026072801
SCHEMA_ADVISORY_LOCK_ID = 2026073102
SCHEMA_MIGRATIONS = (
    (
        "20260730_liquidation_runtime_schema",
        "Split liquidation account registry, discovery progress, and health scan history.",
    ),
    (
        "20260730_liquidation_execution_attempts",
        "Record liquidation execution attempts, blocking reasons, preflight data, and receipts.",
    ),
    (
        "20260730_liquidation_failure_samples",
        "Archive failed or blocked liquidation payloads as reproducible review samples.",
    ),
    (
        "20260731_liquidation_borrow_health_pool",
        "Store active borrower health records below the liquidation watch threshold.",
    ),
    (
        "20260731_liquidation_profit_oriented_pools",
        "Track liquidation scan batches, high-frequency borrower pool, and core opportunity pool.",
    ),
    (
        "20260731_liquidation_scan_config_library",
        "Persist reusable liquidation scan configuration snapshots for account, borrow, and opportunity pools.",
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
