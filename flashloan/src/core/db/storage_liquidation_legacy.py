from __future__ import annotations

import os
import socket
import time
from typing import Any

from core.kv_rows import collection_key, count, load_rows, upsert_row
from core.local_kv import ensure_store, meta_delete_if_matches, meta_get, meta_insert_if_absent


def _market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    return {
        "market_id": str(market_id or "default"),
        "chain_id": int(chain_id) if chain_id is not None else 0,
        "network": str(market_id or "local"),
        "protocol": "aave",
    }


def record_liquidation_discovery_scan(
    database_url: str,
    *,
    mode: str,
    status: str,
    rpc_url: str | None = None,
    pool_address: str | None = None,
    from_block: int,
    to_block: int,
    scan_start_at,
    scan_end_at,
    discovered_count: int = 0,
    error: str | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> int:
    ensure_store(database_url)
    scope = _market_scope(market_id, chain_id)
    scan_id = count(database_url, "liquidation_discovery_scans") + 1
    upsert_row(
        database_url,
        "liquidation_discovery_scans",
        str(scan_id),
        {
            "id": scan_id,
            **scope,
            "mode": mode,
            "status": status,
            "rpc_url": rpc_url,
            "pool_address": pool_address,
            "from_block": int(from_block),
            "to_block": int(to_block),
            "scan_start_at": str(scan_start_at),
            "scan_end_at": str(scan_end_at),
            "discovered_count": int(discovered_count),
            "error": error,
            "created_at": time.time(),
        },
    )
    return scan_id


def liquidation_discovery_scan_progress(
    database_url: str,
    pool_address: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    scope = _market_scope(market_id, chain_id)
    rows = load_rows(database_url, "liquidation_discovery_scans")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == scope["market_id"]]
    if chain_id is not None:
        rows = [row for row in rows if int(row.get("chain_id") or 0) == scope["chain_id"]]
    rows = [row for row in rows if str(row.get("pool_address") or "") == str(pool_address or "")]
    return {
        "latest_recent_scan_end_at": rows[-1].get("scan_end_at") if rows else None,
        "earliest_backfill_scan_start_at": rows[0].get("scan_start_at") if rows else None,
        "latest_recent_to_block": rows[-1].get("to_block") if rows else None,
        "earliest_backfill_from_block": rows[0].get("from_block") if rows else None,
        "success_count": sum(1 for row in rows if row.get("status") == "success"),
        "error_count": sum(1 for row in rows if row.get("error")),
        "scanned_block_count": sum(int(row.get("to_block") or 0) - int(row.get("from_block") or 0) + 1 for row in rows),
    }


def record_liquidation_scan_config_snapshot(
    database_url: str,
    config: dict[str, Any],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    ensure_store(database_url)
    scope = _market_scope(market_id, chain_id)
    key = collection_key(scope["market_id"], scope["chain_id"], config.get("scan_config_key") or time.time())
    payload = {**scope, "config": config, "recorded_at": time.time()}
    upsert_row(database_url, "liquidation_scan_config_library", key, payload)
    return payload


def load_liquidation_scan_config_library(
    database_url: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = load_rows(database_url, "liquidation_scan_config_library")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == str(market_id)]
    if chain_id is not None:
        rows = [row for row in rows if int(row.get("chain_id") or 0) == int(chain_id)]
    rows.sort(key=lambda row: str(row.get("recorded_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]


def rebuild_liquidation_scan_config_library(database_url: str) -> dict[str, Any]:
    rows = load_rows(database_url, "liquidation_scan_config_library")
    return {"count": len(rows), "rows": rows[:20]}


def upsert_liquidation_accounts(
    database_url: str,
    accounts: list[str],
    *,
    source: str = "manual",
    market_id: str | None = None,
    chain_id: int | None = None,
) -> int:
    ensure_store(database_url)
    scope = _market_scope(market_id, chain_id)
    written = 0
    for account in accounts:
        account = str(account or "").strip()
        if not account:
            continue
        key = collection_key(scope["market_id"], scope["chain_id"], account.lower())
        upsert_row(
            database_url,
            "liquidation_accounts",
            key,
            {
                **scope,
                "account": account,
                "source": source,
                "active": True,
                "updated_at": time.time(),
            },
        )
        written += 1
    return written


def load_liquidation_accounts(database_url: str) -> list[str]:
    return [str(row.get("account") or "") for row in load_rows(database_url, "liquidation_accounts") if row.get("account")]


def load_liquidation_accounts_page(
    database_url: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = load_rows(database_url, "liquidation_accounts")
    rows.sort(key=lambda row: str(row.get("account") or ""))
    return rows[int(offset):int(offset) + int(limit)]


def liquidation_account_registry_stats(
    database_url: str,
    retained_days: int = 365,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    rows = load_rows(database_url, "liquidation_accounts")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == str(market_id)]
    if chain_id is not None:
        rows = [row for row in rows if int(row.get("chain_id") or 0) == int(chain_id)]
    active = [row for row in rows if bool(row.get("active"))]
    return {
        "total_count": len(rows),
        "active_count": len(active),
        "earliest_scan_start_at": None,
        "latest_scan_end_at": None,
        "retained_days": int(retained_days),
    }


def try_acquire_observer_lock(database_url: str, lock_id: int = 2026072801, *, stale_seconds: float = 300.0):
    """Return a lightweight lock object or None when already held.

    A stale lock older than ``stale_seconds`` is replaced so a crashed observer
    does not permanently block startup.
    """

    ensure_store(database_url)
    lock_key = f"observer_lock_{lock_id}"
    owner = {
        "hint": f"pid:{os.getpid()}@host:{socket.gethostname()}",
        "acquired_at": time.time(),
        "lock_id": int(lock_id),
    }
    if not meta_insert_if_absent(database_url, lock_key, owner):
        current = meta_get(database_url, lock_key)
        if not isinstance(current, dict):
            return None
        try:
            acquired_at = float(current.get("acquired_at") or 0.0)
        except (TypeError, ValueError):
            acquired_at = 0.0
        if time.time() - acquired_at <= max(0.0, float(stale_seconds)):
            return None
        if not meta_delete_if_matches(database_url, lock_key, current):
            return None
        if not meta_insert_if_absent(database_url, lock_key, owner):
            return None

    class _LocalLock:
        def close(self) -> None:
            meta_delete_if_matches(database_url, lock_key, owner)

    return _LocalLock()


def _json_or_default(value: Any, default: Any) -> Any:
    return value if value is not None else default


__all__ = [
    "load_liquidation_accounts",
    "load_liquidation_accounts_page",
    "load_liquidation_scan_config_library",
    "liquidation_account_registry_stats",
    "liquidation_discovery_scan_progress",
    "rebuild_liquidation_scan_config_library",
    "record_liquidation_discovery_scan",
    "record_liquidation_scan_config_snapshot",
    "try_acquire_observer_lock",
    "upsert_liquidation_accounts",
]
