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

from core.db.database import Database
from core.db.database_path import resolve_database_path

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
        "20261025_cow_execution_review_table",
        "Split review-class CoW attempts into cow_execution_reviews; cow_execution_attempts keeps submission and execution records.",
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
    (
        "20261009_rs_hypersync_v4_pool_index",
        "Add Robinhood V4 HyperSync/HyperRPC pool index and read-only Rust bridge metadata.",
    ),
    (
        "20261009_chain_business_tables",
        "Create global chain registry, chain config, chain info, chain tokens, contracts, files, flows, markets, initialization tables.",
    ),
    (
        "20261009_v4_exchange_pools",
        "Create v4_exchange_pools and v4_exchange_pool_liquidity_snapshots.",
    ),
    (
        "20261009_v4_closed_loop_tables",
        "Create v4_closed_loops, v4_closed_loop_hops, and v4_closed_loop_optimizations.",
    ),
    (
        "20261009_dex_candidate_tables",
        "Create dex_candidate_anchor_tokens, dex_candidate_pool_pairs, dex_candidate_pools, and dex_candidate_pool_recommendations.",
    ),
    (
        "20261009_market_observation_tables",
        "Create market_observation_yz_pairs, market_observation_intermediate_pools, and market_observation_liquidity_snapshots.",
    ),
    (
        "20261016_dex_surface_snapshots",
        "Persist compact DEX overview snapshots in SQLite for fast static pages.",
    ),
)
EXPECTED_SCHEMA_MIGRATION_IDS = tuple(migration_id for migration_id, _ in SCHEMA_MIGRATIONS)

def database_url(database_url: str | None = None) -> str | None:
    """Return the local KV store path."""

    return str(resolve_database_path(database_url))


def configured_database_url(database_url: str | None = None) -> str:
    """Resolve and create the local KV store."""

    resolved = Database.from_url(database_url)
    resolved.ensure()
    return str(resolved.path)


@contextmanager
def db_connection(database_url: str, connect_timeout: int = 8) -> Iterator[sqlite3.Connection]:
    """Yield a local SQLite connection with WAL mode enabled."""

    resolved = Database.from_url(database_url).ensure()
    with Database(
        resolved,
        connect_timeout=max(1.0, float(connect_timeout or 1)),
    ).connection() as connection:
        yield connection


def dedicated_db_connection(database_url: str, connect_timeout: int = 8):
    resolved = Database.from_url(database_url).ensure()
    return Database(
        resolved,
        connect_timeout=max(1.0, float(connect_timeout or 1)),
    ).connect()


def utc_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


__all__ = [
    "EXPECTED_SCHEMA_MIGRATION_IDS",
    "SCHEMA_MIGRATIONS",
    "configured_database_url",
    "database_url",
    "db_connection",
    "dedicated_db_connection",
    "utc_from_ms",
]
