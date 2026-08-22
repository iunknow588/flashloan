from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any, Callable


@dataclass(frozen=True)
class BorrowPoolScanRuntime:
    scan_cache: dict[str, Any]
    scan_lock: Any
    database_url: str
    config: Any
    scope: dict[str, Any]
    market_kwargs: dict[str, Any]
    page_size: int
    risk_page: int
    high_page: int
    core_page: int
    ttl_seconds: float
    force: bool
    load_display: Callable[..., dict]
    blocked_payload: Callable[..., dict]
    run_scan: Callable[..., Any]
    load_borrow_health: Callable[..., list[dict]]
    build_tiers: Callable[..., dict]
    record_batch: Callable[..., dict]
    load_scan_configs: Callable[..., list[dict]]
    build_scan_summary: Callable[..., dict]
    build_pool_summary: Callable[..., dict]
    decision_from_payload: Callable[[dict], dict]
    account_tiers: Callable[[], dict]
    database_fingerprint: Callable[[str | None], str | None]
    error_message: Callable[[object | None], str | None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_suppressed_payload(
    payload: dict,
    *,
    cache: dict[str, Any],
    ttl_seconds: float,
    cache_age_seconds: float,
) -> dict:
    current = dict(payload)
    summary = dict(current.get("summary") or {})
    summary.update(
        {
            "scanned": False,
            "scan_running": bool(cache.get("running")),
            "scan_started_at": cache.get("started_at"),
            "scan_finished_at": cache.get("finished_at"),
            "stage": cache.get("stage") or summary.get("stage") or "idle",
            "scan_interval_seconds": ttl_seconds,
            "scan_cache_age_seconds": max(0.0, cache_age_seconds),
            "scan_cooldown_remaining_seconds": max(0.0, ttl_seconds - cache_age_seconds),
            "scan_suppressed": True,
            "scan_response_source": "cached_scan_suppressed",
            "suppression_reason": "scan interval not reached",
        }
    )
    current["summary"] = summary
    return current


def run_borrow_pool_scan_payload(runtime: BorrowPoolScanRuntime) -> dict:
    cache = runtime.scan_cache
    now = time.monotonic()
    cached_payload = cache.get("borrow_pool_payload")
    updated_at = float(cache.get("borrow_pool_updated_at") or 0.0)
    cache_age_seconds = now - updated_at
    if not runtime.force and (
        runtime.risk_page != 1
        or runtime.high_page != 1
        or runtime.core_page != 1
    ):
        return runtime.load_display(
            page_size=runtime.page_size,
            risk_page=runtime.risk_page,
            high_page=runtime.high_page,
            core_page=runtime.core_page,
            skip_schema=True,
            **runtime.market_kwargs,
        )

    cached_summary = cached_payload.get("summary") if isinstance(cached_payload, dict) else {}
    cache_database_matches = (
        isinstance(cached_summary, dict)
        and cached_summary.get("database_url_fingerprint")
        == runtime.database_fingerprint(runtime.database_url)
        and (
            (
                cached_summary.get("market_id") == runtime.scope["market_id"]
                and cached_summary.get("chain_id") == runtime.scope["chain_id"]
            )
            or (
                not runtime.market_kwargs
                and "market_id" not in cached_summary
                and "chain_id" not in cached_summary
            )
        )
    )
    if (
        not runtime.force
        and isinstance(cached_payload, dict)
        and cache_database_matches
        and cache_age_seconds < runtime.ttl_seconds
    ):
        return build_suppressed_payload(
            cached_payload,
            cache=cache,
            ttl_seconds=runtime.ttl_seconds,
            cache_age_seconds=cache_age_seconds,
        )

    if not runtime.scan_lock.acquire(blocking=False):
        payload = runtime.load_display(
            page_size=runtime.page_size,
            risk_page=runtime.risk_page,
            high_page=runtime.high_page,
            core_page=runtime.core_page,
            **runtime.market_kwargs,
        )
        summary = dict(payload.get("summary") or {})
        summary["scan_running"] = True
        summary["scan_started_at"] = cache.get("started_at")
        summary["stage"] = cache.get("stage") or "debt_pool"
        payload["summary"] = summary
        return payload

    cache["running"] = True
    cache["started_at"] = _now_iso()
    cache["finished_at"] = None
    cache["stage"] = "debt_pool"
    cache["progress"] = {"account_count": 0, "scanned_count": 0}
    accounts: list[str] = []
    rpc_url = None
    error = None
    sync_result: dict[str, int] = {}
    block_number = None
    source_account_count = 0
    scan_selection: dict = {}
    started_at = datetime.now(timezone.utc)
    try:
        scan_result = runtime.run_scan(
            runtime.database_url,
            runtime.config,
            force=runtime.force,
            market_kwargs=runtime.market_kwargs,
            progress=lambda progress: cache.__setitem__("progress", progress),
        )
        source_account_count = scan_result.source_account_count
        accounts = scan_result.selected_accounts
        rpc_url = scan_result.rpc_url
        error = scan_result.error
        sync_result = scan_result.sync_result
        block_number = scan_result.block_number
        scan_selection = scan_result.scan_selection
        cache["last_scan_strategy"] = scan_selection
        rows = runtime.load_borrow_health(
            runtime.database_url,
            limit=runtime.page_size,
            offset=(runtime.risk_page - 1) * runtime.page_size,
            **runtime.market_kwargs,
        )
        tiers = runtime.build_tiers(
            runtime.database_url,
            runtime.page_size,
            high_page=runtime.high_page,
            core_page=runtime.core_page,
            **runtime.market_kwargs,
        )
        finished_at = datetime.now(timezone.utc)
        latest_batch = runtime.record_batch(
            runtime.database_url,
            started_at=started_at,
            finished_at=finished_at,
            status="success" if rpc_url and not error else "error",
            account_count=source_account_count,
            scanned_count=len(accounts) if rpc_url else 0,
            risk_count=tiers["borrow_health_count"],
            error_count=0 if rpc_url and not error else (1 if error else 0),
            entered_count=sync_result.get("entered_count", 0),
            exited_count=sync_result.get("exited_count", 0),
            rpc_url=rpc_url,
            block_number=block_number,
            watch_health_factor=runtime.config.watch_health_factor,
            error=error,
            metadata={
                "tiers": tiers,
                "account_tiers": runtime.account_tiers(),
                "scan_strategy": scan_selection,
                "market": runtime.scope,
            },
            **runtime.market_kwargs,
        )
        scan_payload = {
            "summary": runtime.build_scan_summary(
                database_url_fingerprint=runtime.database_fingerprint(runtime.database_url),
                scope=runtime.scope,
                force=runtime.force,
                source_account_count=source_account_count,
                scanned_count=len(accounts) if rpc_url else 0,
                selected_account_count=len(accounts),
                scan_selection=scan_selection,
                tiers=tiers,
                sync_result=sync_result,
                rpc_url=rpc_url,
                block_number=block_number,
                latest_batch=latest_batch,
                account_tiers=runtime.account_tiers(),
                error=error,
            )
        }
        cache["last_result"] = scan_payload["summary"]
        scan_configs = runtime.load_scan_configs(
            runtime.database_url,
            limit=20,
            **runtime.market_kwargs,
        )
        payload = {
            "rows": rows,
            "tiers": tiers,
            "latest_batch": latest_batch,
            "scan_configs": scan_configs,
            "summary": runtime.build_pool_summary(
                rows,
                scanned=True,
                display_limit=runtime.page_size,
                scan_payload=scan_payload,
            ),
            "pagination": {
                "borrow_health": {
                    "page": runtime.risk_page,
                    "page_size": runtime.page_size,
                    "total_count": tiers["borrow_health_count"],
                    "page_count": max(
                        1,
                        (tiers["borrow_health_count"] + runtime.page_size - 1)
                        // runtime.page_size,
                    ),
                },
                **tiers["pagination"],
            },
        }
        decision = runtime.decision_from_payload(payload)
        payload["debt_pool_decision"] = decision
        cache["last_result"] = {**scan_payload["summary"], "debt_pool_decision": decision}
        cache["borrow_pool_updated_at"] = time.monotonic()
        cache["borrow_pool_payload"] = payload
        return payload
    except Exception as exc:
        safe_error = runtime.error_message(exc)
        try:
            latest_batch = runtime.record_batch(
                runtime.database_url,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                status="error",
                account_count=source_account_count or len(accounts),
                scanned_count=0,
                risk_count=0,
                error_count=1,
                rpc_url=rpc_url,
                block_number=block_number,
                watch_health_factor=runtime.config.watch_health_factor,
                error=safe_error,
                metadata={
                    "account_tiers": runtime.account_tiers(),
                    "scan_strategy": scan_selection,
                },
            )
        except Exception:
            latest_batch = None
        return runtime.blocked_payload(
            configured=True,
            error=safe_error or "borrow pool scan failed",
            page_size=runtime.page_size,
            risk_page=runtime.risk_page,
            high_page=runtime.high_page,
            core_page=runtime.core_page,
            latest_batch=latest_batch,
        )
    finally:
        cache["running"] = False
        cache["finished_at"] = _now_iso()
        cache["stage"] = "idle"
        runtime.scan_lock.release()
