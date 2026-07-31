from contextlib import contextmanager
from datetime import datetime, timezone
import os
import threading
from typing import Iterator

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

_POOL_LOCK = threading.Lock()
_POOLS: dict[str, object] = {}


def require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install psycopg[binary] or run pip install -r requirements.txt"
        ) from exc
    return psycopg


def require_psycopg_pool():
    try:
        from psycopg_pool import ConnectionPool
    except ImportError:
        return None
    return ConnectionPool


def _pool_enabled() -> bool:
    return os.getenv("DATABASE_POOL_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _pool_bounds() -> tuple[int, int]:
    try:
        min_size = int(os.getenv("DATABASE_POOL_MIN_SIZE", "2") or 2)
    except ValueError:
        min_size = 2
    try:
        max_size = int(os.getenv("DATABASE_POOL_MAX_SIZE", "10") or 10)
    except ValueError:
        max_size = 10
    min_size = max(1, min_size)
    max_size = max(min_size, max_size)
    return min_size, max_size


def _pool_key(database_url: str, connect_timeout: int) -> str:
    min_size, max_size = _pool_bounds()
    return f"{database_url}|timeout={int(connect_timeout)}|min={min_size}|max={max_size}"


def get_connection_pool(database_url: str, connect_timeout: int = 8):
    ConnectionPool = require_psycopg_pool()
    if ConnectionPool is None or not _pool_enabled():
        return None
    key = _pool_key(database_url, connect_timeout)
    with _POOL_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            min_size, max_size = _pool_bounds()
            pool = ConnectionPool(
                conninfo=database_url,
                min_size=min_size,
                max_size=max_size,
                kwargs={"connect_timeout": int(connect_timeout)},
                open=True,
            )
            _POOLS[key] = pool
        return pool


@contextmanager
def db_connection(database_url: str, connect_timeout: int = 8) -> Iterator[object]:
    pool = get_connection_pool(database_url, connect_timeout=connect_timeout)
    if pool is not None:
        with pool.connection() as connection:
            yield connection
        return
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as connection:
        yield connection


def dedicated_db_connection(database_url: str, connect_timeout: int = 8):
    psycopg = require_psycopg()
    return psycopg.connect(database_url, connect_timeout=connect_timeout)


def close_connection_pools() -> None:
    with _POOL_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        close = getattr(pool, "close", None)
        if close:
            close()


def utc_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")
