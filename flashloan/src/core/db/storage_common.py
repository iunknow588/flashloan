"""Local KV storage facade.

This module is kept as the compatibility boundary for older callers.  It no
longer talks to PostgreSQL; ``database_url`` arguments are interpreted as the
local SQLite KV store path.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from core.local_kv import ensure_store, store_path

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

def database_url_or_none(database_url: str | None = None) -> str | None:
    """Return the local KV store path."""

    return str(store_path(database_url))


def configured_database_url(database_url: str | None = None) -> str:
    """Resolve and create the local KV store."""

    resolved = store_path(database_url)
    ensure_store(resolved)
    return str(resolved)


@contextmanager
def db_connection(database_url: str, connect_timeout: int = 8) -> Iterator[sqlite3.Connection]:
    """Yield a local SQLite connection with WAL mode enabled."""

    resolved = store_path(database_url)
    ensure_store(resolved)
    connection = sqlite3.connect(str(resolved), timeout=max(1.0, float(connect_timeout or 1)))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def dedicated_db_connection(database_url: str, connect_timeout: int = 8):
    resolved = store_path(database_url)
    ensure_store(resolved)
    connection = sqlite3.connect(str(resolved), timeout=max(1.0, float(connect_timeout or 1)))
    connection.row_factory = sqlite3.Row
    return connection


def utc_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


__all__ = [
    "EXPECTED_SCHEMA_MIGRATION_IDS",
    "SCHEMA_MIGRATIONS",
    "configured_database_url",
    "database_url_or_none",
    "db_connection",
    "dedicated_db_connection",
    "utc_from_ms",
]
