from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any


@dataclass(frozen=True)
class LiquidationHealthRuntime:
    scan_cache: dict[str, Any]
    scan_lock: Any
    scan_interval_seconds: Any
    scan_config: Any
    load_account_registry: Any
    auto_discovery_enabled: Any
    discover_accounts: Any
    rpc_urls: Any
    pool_address: Any
    scan_account_health: Any
    record_scan_rows: Any
    error_message: Any
    display_limit: Any
    watched_health_rows: Any
    display_rows: Any
    summary: Any
    attach_scan_state: Any


def liquidation_health_payload(
    *,
    runtime: LiquidationHealthRuntime,
    force: bool = False,
) -> dict[str, Any]:
    scan_cache = runtime.scan_cache
    now = time.monotonic()
    ttl_seconds = runtime.scan_interval_seconds()
    cached_payload = scan_cache.get("payload")
    updated_at = float(scan_cache.get("updated_at") or 0.0)
    cache_age_seconds = now - updated_at
    if not force and cached_payload and cache_age_seconds < ttl_seconds:
        return runtime.attach_scan_state(
            cached_payload,
            ttl_seconds,
            running=bool(scan_cache.get("running")),
            cache_age_seconds=cache_age_seconds,
            cooldown_remaining_seconds=ttl_seconds - cache_age_seconds,
        )
    if not runtime.scan_lock.acquire(blocking=False):
        if cached_payload:
            return runtime.attach_scan_state(
                cached_payload,
                ttl_seconds,
                running=True,
                cache_age_seconds=cache_age_seconds if updated_at else None,
                cooldown_remaining_seconds=0.0,
            )
        return {
            "rows": [],
            "summary": {
                "scan_running": True,
                "scan_started_at": scan_cache.get("started_at"),
                "scan_finished_at": scan_cache.get("finished_at"),
                "scan_interval_seconds": ttl_seconds,
                "account_count": 0,
                "scanned_count": 0,
            },
        }

    config = runtime.scan_config()
    scan_cache["running"] = True
    scan_cache["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    scan_cache["finished_at"] = None
    scan_cache["stage"] = "health"
    scan_cache["progress"] = {"account_count": 0, "scanned_count": 0}
    accounts: list[str] = []
    account_source = "none"
    auto_discovered = False
    rows: list[dict[str, Any]] = []
    rpc_url = None
    error = None
    try:
        accounts, account_source = runtime.load_account_registry(force=force)
        scan_cache["progress"] = {
            "account_count": len(accounts),
            "scanned_count": 0,
            "source": account_source,
        }
        if not accounts and runtime.auto_discovery_enabled():
            discovery = runtime.discover_accounts(force_full=force)
            if discovery.get("saved"):
                accounts, account_source = runtime.load_account_registry(force=True)
                scan_cache["progress"] = {
                    "account_count": len(accounts),
                    "scanned_count": 0,
                    "source": account_source,
                }
                auto_discovered = True
                rpc_url = str(discovery.get("rpc_url") or "") or None
                error = None
                account_source = "database"
            elif discovery.get("error"):
                error = runtime.error_message(discovery.get("error"))
            if not accounts and not error:
                error = "liquidation account registry is empty"
        if accounts:
            for candidate in runtime.rpc_urls():
                try:
                    scan_cache["progress"] = {
                        "account_count": len(accounts),
                        "scanned_count": 0,
                        "rpc_url": candidate,
                        "source": account_source,
                    }
                    rows = runtime.scan_account_health(
                        accounts,
                        runtime.pool_address(),
                        candidate,
                        config,
                    )
                    scan_cache["progress"] = {
                        "account_count": len(accounts),
                        "scanned_count": len(accounts),
                        "rpc_url": candidate,
                        "source": account_source,
                    }
                    runtime.record_scan_rows(rows)
                    rpc_url = candidate
                    error = None
                    break
                except Exception as exc:
                    error = runtime.error_message(exc)
        if not accounts and not error:
            error = "liquidation account registry is empty"
        display_limit = runtime.display_limit()
        watched_rows = runtime.watched_health_rows(rows, config.watch_health_factor)
        payload = {
            "rows": runtime.display_rows(rows, display_limit),
            "watched_rows": watched_rows[:display_limit],
            "summary": runtime.summary(
                rows,
                len(accounts),
                account_source,
                config,
                rpc_url,
                error,
            )
            | {
                "auto_discovered": auto_discovered,
                "watch_health_factor": config.watch_health_factor,
                "display_limit": display_limit,
                "displayed_count": min(len(rows), display_limit),
                "scan_interval_seconds": ttl_seconds,
                "watch_count": len(watched_rows),
            },
        }
        scan_cache["last_result"] = payload["summary"]
        scan_cache["running"] = False
        scan_cache["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        scan_cache["stage"] = "idle"
        payload = runtime.attach_scan_state(
            payload,
            ttl_seconds,
            running=False,
            cache_age_seconds=0.0,
            cooldown_remaining_seconds=ttl_seconds,
        )
        scan_cache["updated_at"] = time.monotonic()
        scan_cache["payload"] = payload
        return payload
    finally:
        scan_cache["running"] = False
        if not scan_cache.get("finished_at"):
            scan_cache["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        scan_cache["stage"] = "idle"
        runtime.scan_lock.release()
