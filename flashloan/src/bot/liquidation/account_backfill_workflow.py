from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class AccountBackfillRuntime:
    cache: dict[str, Any]
    lock: Any
    stop_event: Any
    status_payload: Callable[[], dict]
    database_url: Callable[[], str | None]
    auto_discovery_enabled: Callable[[], bool]
    pool_address: Callable[[], str | None]
    discovery_window: Callable[..., tuple]
    account_registry_window: Callable[[], dict]
    chunk_size: Callable[[], int]
    rpc_urls: Callable[[], list[str]]
    resolve_block_range: Callable[[str, int, int | None], tuple[int, int, int]]
    discover_borrowers: Callable[..., list[str]]
    configured_database_url: Callable[[], str]
    upsert_accounts: Callable[..., Any]
    invalidate_account_cache: Callable[[], None]
    set_progress: Callable[[dict], None]
    error_message: Callable[[object | None], str | None]


def run_account_backfill_once(runtime: AccountBackfillRuntime) -> dict:
    if not runtime.database_url():
        return {"started": False, "saved": False, "count": 0, "error": "local KV store unavailable"}
    if not runtime.auto_discovery_enabled():
        return {"started": False, "saved": False, "count": 0, "error": "auto discovery disabled"}
    if not runtime.lock.acquire(blocking=False):
        return {"started": False, "running": True, **runtime.status_payload()}

    runtime.stop_event.clear()
    started_at = datetime.now(timezone.utc)
    runtime.cache.update(
        {
            "running": True,
            "stop_requested": False,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": None,
            "stage": "window",
            "progress": {},
            "error": None,
        }
    )
    try:
        pool_address = runtime.pool_address()
        if not pool_address:
            raise RuntimeError("missing AAVE_POOL_ADDRESS")
        (
            scan_start_at,
            scan_end_at,
            from_block,
            to_block,
            lookback_blocks,
            registry,
            mode,
        ) = runtime.discovery_window(force_full=True)
        chunk_size = runtime.chunk_size()
        last_error = None
        for rpc_url in runtime.rpc_urls():
            if runtime.stop_event.is_set():
                break
            actual_from_block = 0
            actual_to_block = 0
            try:
                runtime.cache["stage"] = "resolving-blocks"
                _, actual_from_block, actual_to_block = runtime.resolve_block_range(
                    rpc_url,
                    from_block,
                    to_block,
                )
                runtime.cache["stage"] = "borrowers"
                discovered = (
                    runtime.discover_borrowers(
                        rpc_url,
                        pool_address,
                        actual_from_block,
                        to_block=actual_to_block,
                        chunk_size=chunk_size,
                        limit=0,
                        stop_event=runtime.stop_event,
                        progress_callback=runtime.set_progress,
                    )
                    if actual_from_block <= actual_to_block
                    else []
                )
                runtime.cache["stage"] = "saving"
                if discovered:
                    runtime.upsert_accounts(
                        runtime.configured_database_url(),
                        discovered,
                        source="one-year-gap-fill",
                        active=True,
                        scan_start_at=scan_start_at,
                        scan_end_at=scan_end_at,
                        update_existing=True,
                    )
                runtime.invalidate_account_cache()
                status = "stopped" if runtime.stop_event.is_set() else "success"
                result = {
                    "started": True,
                    "saved": True,
                    "status": status,
                    "stopped": status == "stopped",
                    "count": len(discovered),
                    "rpc_url": rpc_url,
                    "mode": "one-year-gap-fill",
                    "from_block": from_block,
                    "to_block": to_block,
                    "actual_from_block": actual_from_block,
                    "actual_to_block": actual_to_block,
                    "lookback_blocks": lookback_blocks,
                    "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                    "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                    "registry_window": runtime.account_registry_window(),
                    "progress": dict(runtime.cache.get("progress") or {}),
                }
                runtime.cache["last_result"] = result
                return result
            except Exception as exc:
                last_error = runtime.error_message(exc)
        result = {
            "started": True,
            "saved": False,
            "stopped": bool(runtime.stop_event.is_set()),
            "count": 0,
            "error": None if runtime.stop_event.is_set() else (
                last_error or "unable to backfill borrower addresses"
            ),
            "mode": "one-year-gap-fill",
        }
        runtime.cache["last_result"] = result
        return result
    except Exception as exc:
        safe_error = runtime.error_message(exc)
        result = {
            "started": True,
            "saved": False,
            "count": 0,
            "error": safe_error,
            "mode": "one-year-gap-fill",
        }
        runtime.cache["last_result"] = result
        runtime.cache["error"] = safe_error
        return result
    finally:
        runtime.cache["running"] = False
        runtime.cache["stop_requested"] = bool(runtime.stop_event.is_set())
        runtime.cache["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        runtime.cache["stage"] = "idle"
        runtime.lock.release()
