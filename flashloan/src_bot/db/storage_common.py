from contextlib import contextmanager
from datetime import datetime, timezone
from collections import deque
import logging
import os
import threading
import time
from typing import Callable, Iterator, TypeVar

from core.config_schema import parse_env_int
from core.env_loader import local_json_only_enabled

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
    (
        "20260803_liquidation_market_namespace",
        "Add chain, market, protocol, executor, source RPC, and source block columns for future multi-market liquidation isolation.",
    ),
    (
        "20260804_cow_supported_tokens",
        "Persist CoW supported token universe per network for DEX arbitrage filtering.",
    ),
    (
        "20260805_cow_execution_attempts",
        "Record CoW quote and execution-precheck outcomes for DEX arbitrage review.",
    ),
    (
        "20260807_control_panel_parameters",
        "Persist UI/runtime control parameters in the application database.",
    ),
    (
        "20260817_robinhood_dex_observation",
        "Add Robinhood DEX observation attempts and seed market-specific three-pool scan policies.",
    ),
    (
        "20260818_chain_market_registry",
        "Persist chain/market registry, scan policies, and current DEX arbitrage candidates.",
    ),
)
EXPECTED_SCHEMA_MIGRATION_IDS = tuple(migration_id for migration_id, _ in SCHEMA_MIGRATIONS)

_POOL_LOCK = threading.Lock()
_POOLS: dict[str, object] = {}
_DATABASE_UNAVAILABLE_LOCK = threading.Lock()
_DATABASE_UNAVAILABLE_UNTIL: dict[str, tuple[float, str]] = {}
_TRANSACTION_EVENTS_LOCK = threading.Lock()
_TRANSACTION_EVENTS: deque[dict[str, object]] = deque(maxlen=500)
_LOGGER = logging.getLogger(__name__)
_TransactionResult = TypeVar("_TransactionResult")


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


def database_url_or_none(database_url: str | None = None) -> str | None:
    if local_json_only_enabled():
        return None
    return str(database_url or os.getenv("DATABASE_URL", "")).strip() or None


def configured_database_url(database_url: str | None = None) -> str:
    resolved = database_url_or_none(database_url)
    if resolved:
        return resolved
    if local_json_only_enabled():
        raise RuntimeError("DATABASE_URL is disabled because FLASHLOAN_LOCAL_JSON_ONLY is enabled.")
    raise RuntimeError("DATABASE_URL is missing. Set it in .env or the environment.")


def _pool_enabled() -> bool:
    return os.getenv("DATABASE_POOL_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _pool_bounds() -> tuple[int, int]:
    min_size = parse_env_int("DATABASE_POOL_MIN_SIZE", 2, minimum=1)[0]
    max_size = parse_env_int("DATABASE_POOL_MAX_SIZE", 10, minimum=1)[0]
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


def _drop_connection_pool(database_url: str, connect_timeout: int = 8) -> None:
    key = _pool_key(database_url, connect_timeout)
    with _POOL_LOCK:
        pool = _POOLS.pop(key, None)
    close = getattr(pool, "close", None)
    if close:
        close()


def _pool_connection_context(pool, connect_timeout: int):
    try:
        return pool.connection(timeout=int(connect_timeout))
    except TypeError:
        return pool.connection()


def _is_connection_termination_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "terminating connection due to administrator command",
        "server closed the connection unexpectedly",
        "connection already closed",
        "connection is closed",
        "could not receive data from server",
        "could not send data to server",
        "ssl connection has been closed unexpectedly",
    )
    return any(marker in text for marker in markers)


def is_database_unavailable_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "database unavailable",
        "the endpoint has been disabled",
        "enable it using the api and retry",
        "server closed the connection unexpectedly",
        "terminating connection due to administrator command",
        "connection already closed",
        "connection is closed",
        "could not receive data from server",
        "could not send data to server",
        "could not connect to server",
        "connection refused",
        "connection timeout expired",
        "timeout expired",
        "network is unreachable",
        "no route to host",
        "ssl connection has been closed unexpectedly",
    )
    return isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(
        marker in text for marker in markers
    )


def _database_unavailable_cooldown_seconds() -> int:
    value, _ = parse_env_int("DATABASE_UNAVAILABLE_COOLDOWN_SECONDS", 300, minimum=30)
    return max(30, min(int(value), 3600))


def mark_database_unavailable(database_url: str, exc: BaseException | str) -> None:
    reason = str(exc).splitlines()[0][:240] if exc else "database unavailable"
    until = time.monotonic() + _database_unavailable_cooldown_seconds()
    with _DATABASE_UNAVAILABLE_LOCK:
        _DATABASE_UNAVAILABLE_UNTIL[database_url] = (until, reason)
    _drop_connection_pool(database_url)


def database_unavailable_reason(database_url: str | None) -> str | None:
    if not database_url:
        return None
    with _DATABASE_UNAVAILABLE_LOCK:
        cached = _DATABASE_UNAVAILABLE_UNTIL.get(database_url)
        if not cached:
            return None
        until, reason = cached
        if until <= time.monotonic():
            _DATABASE_UNAVAILABLE_UNTIL.pop(database_url, None)
            return None
        return reason or "database temporarily unavailable"


@contextmanager
def db_connection(database_url: str, connect_timeout: int = 8) -> Iterator[object]:
    pool = None
    # Short UI probes should not wait for a pool's background replenishment;
    # connect directly and let the caller's fallback circuit handle failure.
    if int(connect_timeout) > 1:
        try:
            pool = get_connection_pool(database_url, connect_timeout=connect_timeout)
        except Exception:
            pool = None
    if pool is not None:
        try:
            pool_context = _pool_connection_context(pool, connect_timeout)
            connection = pool_context.__enter__()
        except Exception:
            _drop_connection_pool(database_url, connect_timeout=connect_timeout)
        else:
            try:
                yield connection
            except BaseException as exc:
                pool_context.__exit__(type(exc), exc, exc.__traceback__)
                if _is_connection_termination_error(exc):
                    _drop_connection_pool(database_url, connect_timeout=connect_timeout)
                raise
            else:
                pool_context.__exit__(None, None, None)
            return
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as connection:
        yield connection


def db_transaction(connection: object, operation: str | None = None):
    return _db_transaction(connection, operation=operation)


def run_db_transaction(
    database_url: str,
    operation: str,
    callback: Callable[[object], _TransactionResult],
    *,
    connect_timeout: int = 8,
    max_retries: int = 2,
    retry_backoff_ms: int = 50,
) -> _TransactionResult:
    """Run a callback in an isolated transaction with bounded retry.

    Each retry obtains a fresh connection and transaction. This is important
    for PostgreSQL deadlocks and serialization failures because the failed
    transaction cannot safely be reused by the caller.
    """

    retry_limit = max(0, int(max_retries))
    backoff_seconds = max(0.0, float(retry_backoff_ms) / 1000.0)
    for attempt in range(retry_limit + 1):
        try:
            with db_connection(database_url, connect_timeout=connect_timeout) as connection:
                with db_transaction(connection, operation=operation):
                    return callback(connection)
        except BaseException as exc:
            retryable = _transaction_error_kind(exc) in {"deadlock", "serialization_failure"}
            if not retryable or attempt >= retry_limit:
                raise
            _record_transaction_event(
                operation=str(operation or "unspecified"),
                outcome="retry",
                duration_ms=0.0,
                error=exc,
                attempt=attempt + 1,
            )
            if backoff_seconds:
                time.sleep(backoff_seconds * (attempt + 1))
    raise RuntimeError("database transaction retry loop exited unexpectedly")


def _transaction_timeout_ms(name: str) -> int:
    value, _ = parse_env_int(name, 0, minimum=0)
    return max(0, min(int(value), 3_600_000))


def _configure_transaction_limits(connection: object) -> None:
    """Apply optional per-transaction limits without coupling tests to psycopg."""
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        return
    lock_timeout = _transaction_timeout_ms("DATABASE_LOCK_TIMEOUT_MS")
    statement_timeout = _transaction_timeout_ms("DATABASE_STATEMENT_TIMEOUT_MS")
    if lock_timeout:
        execute(f"SET LOCAL lock_timeout = '{lock_timeout}ms'")
    if statement_timeout:
        execute(f"SET LOCAL statement_timeout = '{statement_timeout}ms'")


def _transaction_error_kind(exc: BaseException) -> str:
    text = str(exc).lower()
    if "deadlock detected" in text:
        return "deadlock"
    if "lock timeout" in text or "canceling statement due to lock timeout" in text:
        return "lock_timeout"
    if "statement timeout" in text or "canceling statement due to user request" in text:
        return "statement_timeout"
    if "serialization failure" in text or "could not serialize access" in text:
        return "serialization_failure"
    return "transaction_error"


def _record_transaction_event(
    *,
    operation: str,
    outcome: str,
    duration_ms: float,
    error: BaseException | None = None,
    attempt: int | None = None,
) -> None:
    event: dict[str, object] = {
        "operation": operation,
        "outcome": outcome,
        "duration_ms": round(duration_ms, 3),
        "error_kind": None,
        "error": None,
        "retryable": False,
        "attempt": attempt,
    }
    if error is not None:
        kind = _transaction_error_kind(error)
        event["error_kind"] = kind
        event["error"] = str(error).splitlines()[0][:240]
        event["retryable"] = kind in {"deadlock", "serialization_failure"}
    with _TRANSACTION_EVENTS_LOCK:
        _TRANSACTION_EVENTS.append(event)
    if error is not None:
        _LOGGER.warning("db transaction %s", event)


def database_transaction_events(limit: int = 100) -> list[dict[str, object]]:
    with _TRANSACTION_EVENTS_LOCK:
        events = list(_TRANSACTION_EVENTS)
    return [dict(event) for event in events[-max(1, int(limit)):]]


def clear_database_transaction_events() -> None:
    with _TRANSACTION_EVENTS_LOCK:
        _TRANSACTION_EVENTS.clear()


@contextmanager
def _db_transaction(connection: object, operation: str | None = None) -> Iterator[object]:
    operation_name = str(operation or "unspecified").strip() or "unspecified"
    transaction = getattr(connection, "transaction", None)
    if not callable(transaction):
        yield connection
        return
    started = time.perf_counter()
    try:
        with transaction():
            _configure_transaction_limits(connection)
            yield connection
    except BaseException as exc:
        _record_transaction_event(
            operation=operation_name,
            outcome="rollback",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            error=exc,
        )
        raise
    else:
        _record_transaction_event(
            operation=operation_name,
            outcome="commit",
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )


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
