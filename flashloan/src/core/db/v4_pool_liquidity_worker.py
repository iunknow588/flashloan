"""Persistent incremental cursor for the V4 pool liquidity worker.

The Rust worker pool owns the fixed ``rust-closed-loop-sqlite-maintenance``
thread.  The cursor kept here tells that thread where the last incremental
pool-liquidity batch stopped, so every maintenance cycle only refreshes the
next candidate-eligible pool slice instead of re-scanning all pools.

The complete Robinhood V4 worker set is intentionally persisted in the same
``chain_business_initialization`` table so the process can answer "who owns
which incremental cursor" after a restart.  Pool liquidity is the fixed first
stage; candidate YC/ZC, market observation, Dex overview and closed-loop
builds each run as their own maintenance slot and only proceed after the
stage they depend on reaches a complete state.
"""

from __future__ import annotations

import json
from typing import Any

from core.db.storage_chain_core import load_initialization, upsert_initialization


V4_POOL_LIQUIDITY_WORKER_DOMAIN = "v4_pool_liquidity_worker"
V4_POOL_LIQUIDITY_PROTOCOL = "uniswap_v4"
DEFAULT_V4_POOL_LIQUIDITY_BATCH_SIZE = 512


# Fixed worker slots for the Robinhood V4 maintenance pipeline.  Each slot is
# owned by its own thread and keeps its own cursor, so the pool-index refresh
# (mostly idle), candidate YC/ZC rebuild, market observation, Dex overview and
# closed-loop rebuild cannot block one another or re-scan a stage they already
# completed.
V4_MAINTENANCE_WORKER_DOMAINS = (
    "v4_pool_liquidity_worker",
    "v4_candidate_yc_zc_worker",
    "v4_market_observation_worker",
    "v4_dex_overview_worker",
    "v4_closed_loop_worker",
    "v4_onchain_review_worker",
    "v4_broadcast_execution_worker",
)

MAINTENANCE_WORKER_ORDER = (
    "v4_pool_liquidity_worker",
    "v4_candidate_yc_zc_worker",
    "v4_market_observation_worker",
    "v4_dex_overview_worker",
    "v4_closed_loop_worker",
)


def worker_cursor_domain(worker: str) -> str:
    """Normalize a worker slot name to its persisted initialization domain."""
    text = str(worker or "").strip().lower().replace("-", "_")
    if text not in V4_MAINTENANCE_WORKER_DOMAINS:
        return text or "v4_missing_worker"
    return text


def _completeness(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    raw = row.get("completeness_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def load_v4_pool_liquidity_cursor(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = V4_POOL_LIQUIDITY_PROTOCOL,
) -> dict[str, Any]:
    """Return the persisted offset of the incremental liquidity sweep."""
    row = load_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=V4_POOL_LIQUIDITY_WORKER_DOMAIN,
    )
    completeness = _completeness(row)
    refreshed_at = row.get("refreshed_at") if isinstance(row, dict) else None
    return {
        "cursor": _int(completeness.get("cursor")),
        "cursor_pool_id": str(completeness.get("cursor_pool_id") or "").strip().lower(),
        "pool_count": _int(completeness.get("pool_count")),
        "scanned_pool_count": _int(completeness.get("scanned_pool_count")),
        "remaining_pool_count": _int(completeness.get("remaining_pool_count")),
        "batch_size": _int(
            completeness.get("batch_size"),
            DEFAULT_V4_POOL_LIQUIDITY_BATCH_SIZE,
        ),
        "last_block_number": _int(completeness.get("last_block_number")),
        "last_scanned_at": completeness.get("last_scanned_at") or None,
        "mode": str(completeness.get("mode") or "idle"),
        "error": completeness.get("error") or None,
        "refreshed_at": refreshed_at,
    }


def save_v4_pool_liquidity_cursor(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = V4_POOL_LIQUIDITY_PROTOCOL,
    cursor: int = 0,
    cursor_pool_id: str | None = None,
    pool_count: int = 0,
    scanned_pool_count: int = 0,
    remaining_pool_count: int = 0,
    batch_size: int = DEFAULT_V4_POOL_LIQUIDITY_BATCH_SIZE,
    last_scanned_at: str | None = None,
    last_block_number: int = 0,
    mode: str = "idle",
    error: str | None = None,
) -> dict[str, Any]:
    """Persist one incremental sweep checkpoint."""
    completeness = {
        "cursor": max(0, int(cursor)),
        "cursor_pool_id": str(cursor_pool_id or "").strip().lower(),
        "pool_count": max(0, int(pool_count)),
        "scanned_pool_count": max(0, int(scanned_pool_count)),
        "remaining_pool_count": max(0, int(remaining_pool_count)),
        "batch_size": max(1, int(batch_size)),
        "last_scanned_at": last_scanned_at,
        "last_block_number": max(0, int(last_block_number)),
        "mode": mode,
        "error": error,
    }
    upsert_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=V4_POOL_LIQUIDITY_WORKER_DOMAIN,
        status="ok" if not error else "error",
        completeness=completeness,
        error={"message": error} if error else None,
    )
    return completeness


def load_worker_cursor(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = V4_POOL_LIQUIDITY_PROTOCOL,
    worker: str,
) -> dict[str, Any]:
    """Return the persisted cursor for any named V4 maintenance worker."""
    domain = worker_cursor_domain(worker)
    row = load_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=domain,
    )
    completeness = _completeness(row)
    refreshed_at = row.get("refreshed_at") if isinstance(row, dict) else None
    return {
        "worker": domain,
        "cursor": _int(completeness.get("cursor")),
        "cursor_pool_id": str(completeness.get("cursor_pool_id") or "").strip().lower(),
        "pool_count": _int(completeness.get("pool_count")),
        "scanned_pool_count": _int(completeness.get("scanned_pool_count")),
        "remaining_pool_count": _int(completeness.get("remaining_pool_count")),
        "batch_size": _int(
            completeness.get("batch_size"),
            DEFAULT_V4_POOL_LIQUIDITY_BATCH_SIZE,
        ),
        "last_block_number": _int(completeness.get("last_block_number")),
        "last_scanned_at": completeness.get("last_scanned_at") or None,
        "mode": str(completeness.get("mode") or "idle"),
        "error": completeness.get("error") or None,
        "refreshed_at": refreshed_at,
    }


def save_worker_cursor(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = V4_POOL_LIQUIDITY_PROTOCOL,
    worker: str,
    cursor: int = 0,
    cursor_pool_id: str | None = None,
    pool_count: int = 0,
    scanned_pool_count: int = 0,
    remaining_pool_count: int = 0,
    batch_size: int = DEFAULT_V4_POOL_LIQUIDITY_BATCH_SIZE,
    last_scanned_at: str | None = None,
    last_block_number: int = 0,
    mode: str = "idle",
    error: str | None = None,
) -> dict[str, Any]:
    """Persist one checkpoint for any named V4 maintenance worker."""
    domain = worker_cursor_domain(worker)
    completeness = {
        "cursor": max(0, int(cursor)),
        "cursor_pool_id": str(cursor_pool_id or "").strip().lower(),
        "pool_count": max(0, int(pool_count)),
        "scanned_pool_count": max(0, int(scanned_pool_count)),
        "remaining_pool_count": max(0, int(remaining_pool_count)),
        "batch_size": max(1, int(batch_size)),
        "last_scanned_at": last_scanned_at,
        "last_block_number": max(0, int(last_block_number)),
        "mode": mode,
        "error": error,
    }
    upsert_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=domain,
        status="ok" if not error else "error",
        completeness=completeness,
        error={"message": error} if error else None,
    )
    return completeness


__all__ = [
    "DEFAULT_V4_POOL_LIQUIDITY_BATCH_SIZE",
    "MAINTENANCE_WORKER_ORDER",
    "V4_MAINTENANCE_WORKER_DOMAINS",
    "V4_POOL_LIQUIDITY_PROTOCOL",
    "V4_POOL_LIQUIDITY_WORKER_DOMAIN",
    "load_worker_cursor",
    "load_v4_pool_liquidity_cursor",
    "save_worker_cursor",
    "save_v4_pool_liquidity_cursor",
    "worker_cursor_domain",
]
