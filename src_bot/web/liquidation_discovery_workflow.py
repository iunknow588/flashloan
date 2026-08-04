import os
import time
from datetime import datetime, timezone
from typing import Any

from core.sensitive_data import redact_sensitive_text
from execution.external_liquidation_index import fetch_external_borrower_accounts, merge_candidate_accounts


def discover_and_sync_liquidation_accounts(ctx: Any, force_full: bool = False) -> dict:
    if not ctx.database_url_or_none():
        return {"saved": False, "count": 0, "error": "DATABASE_URL is required"}
    if os.getenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true").strip().lower() in {"0", "false", "no"}:
        return {"saved": False, "count": 0, "error": "auto discovery disabled"}
    if not ctx.LIQUIDATION_DISCOVERY_LOCK.acquire(blocking=False):
        result = dict(ctx.LIQUIDATION_DISCOVERY_CACHE.get("last_result") or {})
        result["running"] = True
        return result

    ctx.LIQUIDATION_DISCOVERY_CACHE["running"] = True
    ctx.LIQUIDATION_DISCOVERY_CACHE["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ctx.LIQUIDATION_DISCOVERY_CACHE["finished_at"] = None
    ctx.LIQUIDATION_DISCOVERY_CACHE["stage"] = "window"
    ctx.LIQUIDATION_DISCOVERY_CACHE["progress"] = {}
    try:
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        if not pool_address:
            return {"saved": False, "count": 0, "error": "missing AAVE_POOL_ADDRESS"}
        scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode = (
            ctx.liquidation_discovery_window(force_full=force_full)
        )
        ctx.LIQUIDATION_DISCOVERY_CACHE["stage"] = "borrowers"
        result = ctx.build_discovery_window_result(
            force_full=force_full,
            scan_start_at=scan_start_at,
            scan_end_at=scan_end_at,
            interval_seconds=ctx.liquidation_discovery_interval_seconds(),
            registry=registry,
            mode=mode,
            from_block=from_block,
            to_block=to_block,
            lookback_blocks=lookback_blocks,
        )
        if result.get("skipped"):
            result["stage"] = ctx.LIQUIDATION_DISCOVERY_CACHE.get("stage")
            result["discovery_cursor"] = registry.get("discovery_cursor")
            ctx.LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
            return result
        return _discover_with_rpc_candidates(
            ctx,
            force_full=force_full,
            pool_address=pool_address,
            scan_start_at=scan_start_at,
            scan_end_at=scan_end_at,
            from_block=from_block,
            to_block=to_block,
            lookback_blocks=lookback_blocks,
            registry=registry,
            mode=mode,
        )
    finally:
        ctx.LIQUIDATION_DISCOVERY_CACHE["running"] = False
        ctx.LIQUIDATION_DISCOVERY_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ctx.LIQUIDATION_DISCOVERY_CACHE["stage"] = "idle"
        ctx.LIQUIDATION_DISCOVERY_LOCK.release()


def _discover_with_rpc_candidates(
    ctx: Any,
    *,
    force_full: bool,
    pool_address: str,
    scan_start_at: datetime,
    scan_end_at: datetime,
    from_block: int,
    to_block: int,
    lookback_blocks: int,
    registry: dict,
    mode: str,
) -> dict:
    try:
        chunk_size = _positive_int_env("LIQUIDATION_BORROW_SCAN_CHUNK_SIZE", 1000)
        limit = _borrow_discovery_limit(ctx, force_full, registry)
    except ValueError as exc:
        return _configuration_error_result(ctx, redact_sensitive_text(exc))
    external_index = _fetch_external_index_candidates(
        pool_address=pool_address,
        from_block=from_block,
        to_block=to_block,
        limit=limit or ctx.liquidation_scan_config().max_candidates,
    )
    external_index_summary = _external_index_summary(external_index)
    last_error = None
    for candidate in ctx.aave_rpc_urls():
        actual_from_block = 0
        actual_to_block = 0
        try:
            _, actual_from_block, actual_to_block = ctx.resolve_discovery_block_range(candidate, from_block, to_block)
            progress_base = {
                "rpc_url": candidate,
                "from_block": actual_from_block,
                "to_block": actual_to_block,
                "chunk_size": chunk_size,
                "limit": limit,
                "external_index": external_index_summary,
            }
            ctx.LIQUIDATION_DISCOVERY_CACHE["progress"] = {
                **progress_base,
            }
            onchain_discovered = _discover_candidate_accounts(
                ctx,
                candidate,
                pool_address,
                actual_from_block,
                actual_to_block,
                chunk_size,
                limit,
                progress_callback=lambda progress: ctx.LIQUIDATION_DISCOVERY_CACHE.update(
                    {"progress": {**progress_base, **progress}}
                ),
            )
            discovered = merge_candidate_accounts(
                onchain_discovered,
                external_index.get("accounts") or [],
                limit=limit or ctx.liquidation_scan_config().max_candidates,
            )
            return _sync_discovered_accounts(
                ctx,
                force_full=force_full,
                pool_address=pool_address,
                rpc_url=candidate,
                discovered=discovered,
                onchain_discovered=onchain_discovered,
                external_index=external_index,
                scan_start_at=scan_start_at,
                scan_end_at=scan_end_at,
                from_block=from_block,
                to_block=to_block,
                actual_from_block=actual_from_block,
                actual_to_block=actual_to_block,
                lookback_blocks=lookback_blocks,
                registry=registry,
                mode=mode,
            )
        except Exception as exc:
            last_error = redact_sensitive_text(exc)
            if actual_from_block <= actual_to_block:
                ctx.record_liquidation_discovery_window(
                    mode=mode,
                    status="error",
                    rpc_url=candidate,
                    pool_address=pool_address,
                    from_block=actual_from_block,
                    to_block=actual_to_block,
                    scan_start_at=scan_start_at,
                    scan_end_at=scan_end_at,
                    discovered_count=0,
                    error=last_error,
                )
    result = {
        "saved": False,
        "count": 0,
        "error": last_error or "unable to discover borrower addresses",
        "mode": mode,
        "stage": ctx.LIQUIDATION_DISCOVERY_CACHE.get("stage"),
        "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
        "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
        "external_index": _external_index_summary(external_index),
        "external_index_count": int(external_index.get("count") or 0),
        "external_index_enabled": bool(external_index.get("enabled")),
        "external_index_configured": bool(external_index.get("configured")),
        "requires_onchain_verification": True,
    }
    ctx.LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
    return result


def _borrow_discovery_limit(ctx: Any, force_full: bool, registry: dict) -> int:
    if force_full or not registry.get("discovery_scan_progress", {}).get("latest_recent_to_block"):
        return 0
    configured_limit = _nonnegative_int_env("LIQUIDATION_BORROW_DISCOVERY_LIMIT", 5000)
    return min(ctx.liquidation_scan_config().max_candidates, configured_limit)


def _positive_int_env(name: str, default: int) -> int:
    value = _nonnegative_int_env(name, default)
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def _nonnegative_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")
    return value


def _configuration_error_result(ctx: Any, error: str) -> dict:
    result = {
        "saved": False,
        "count": 0,
        "error": error,
        "stage": "configuration",
        "requires_onchain_verification": True,
    }
    ctx.LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
    return result


def _discover_candidate_accounts(
    ctx: Any,
    rpc_url: str,
    pool_address: str,
    actual_from_block: int,
    actual_to_block: int,
    chunk_size: int,
    limit: int,
    progress_callback=None,
) -> list[str]:
    if actual_from_block > actual_to_block:
        return []
    return ctx.discover_borrower_addresses(
        rpc_url,
        pool_address,
        actual_from_block,
        to_block=actual_to_block,
        chunk_size=chunk_size,
        limit=limit,
        progress_callback=progress_callback,
    )


def _fetch_external_index_candidates(
    *,
    pool_address: str,
    from_block: int | None,
    to_block: int | None,
    limit: int,
) -> dict[str, Any]:
    result = fetch_external_borrower_accounts(
        pool_address=pool_address,
        from_block=from_block,
        to_block=to_block,
    )
    accounts = list(result.get("accounts") or [])
    if limit > 0 and len(accounts) > limit:
        result = dict(result)
        result["accounts"] = accounts[:limit]
        result["count"] = len(result["accounts"])
    return result


def _external_index_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    data = result or {}
    return {
        "enabled": bool(data.get("enabled")),
        "configured": bool(data.get("configured")),
        "source": str(data.get("source") or "external-index-coarse"),
        "count": int(data.get("count") or 0),
        "error": data.get("error"),
        "requires_onchain_verification": True,
    }


def _sync_discovered_accounts(ctx: Any, **data: Any) -> dict:
    discovered = data["discovered"]
    external_index = data.get("external_index") or {}
    source = "auto-discovery"
    if int(external_index.get("count") or 0) > 0:
        source = "auto-discovery+external-index-coarse"
    ctx.sync_liquidation_accounts_to_database(
        discovered,
        source=source,
        scan_start_at=data["scan_start_at"],
        scan_end_at=data["scan_end_at"],
        update_existing=True,
    )
    ctx.LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
    progress = dict((data["registry"].get("discovery_scan_progress") or {}))
    continuity_error = ctx.discovery_window_continuity_error(
        data["mode"],
        data["actual_from_block"],
        data["actual_to_block"],
        progress,
    )
    if continuity_error:
        result = _continuity_skip_result(ctx, continuity_error, **data)
    else:
        ctx.record_liquidation_discovery_window(
            mode=data["mode"],
            status="success",
            rpc_url=data["rpc_url"],
            pool_address=data["pool_address"],
            from_block=data["actual_from_block"],
            to_block=data["actual_to_block"],
            scan_start_at=data["scan_start_at"],
            scan_end_at=data["scan_end_at"],
            discovered_count=len(discovered),
        )
        result = _success_result(ctx, **data)
    ctx.LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
    return result


def _continuity_skip_result(ctx: Any, reason: str, **data: Any) -> dict:
    external_summary = _external_index_summary(data.get("external_index"))
    onchain_count = len(data.get("onchain_discovered") or [])
    return {
        "saved": False,
        "count": len(data["discovered"]),
        "skipped": True,
        "reason": reason,
        "mode": data["mode"],
        "rpc_url": data["rpc_url"],
        "from_block": data["from_block"],
        "to_block": data["to_block"],
        "actual_from_block": data["actual_from_block"],
        "actual_to_block": data["actual_to_block"],
        "lookback_blocks": data["lookback_blocks"],
        "discovery_cursor": data["registry"].get("discovery_cursor"),
        "scan_start_at": data["scan_start_at"].isoformat(timespec="seconds"),
        "scan_end_at": data["scan_end_at"].isoformat(timespec="seconds"),
        "registry_window": data["registry"],
        "candidate_source_counts": {
            "onchain_borrow_logs": onchain_count,
            "external_index_coarse": external_summary["count"],
        },
        "onchain_log_count": onchain_count,
        "external_index_count": external_summary["count"],
        "external_index_enabled": external_summary["enabled"],
        "external_index_configured": external_summary["configured"],
        "external_index": external_summary,
        "requires_onchain_verification": True,
    }


def _success_result(ctx: Any, **data: Any) -> dict:
    external_summary = _external_index_summary(data.get("external_index"))
    onchain_count = len(data.get("onchain_discovered") or [])
    if data["force_full"]:
        now = datetime.now(timezone.utc)
        ctx.LIQUIDATION_DISCOVERY_CACHE["last_backfill_at"] = now.isoformat(timespec="seconds")
        ctx.LIQUIDATION_DISCOVERY_CACHE["last_backfill_monotonic"] = time.monotonic()
        ctx.LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = data["scan_start_at"].isoformat(timespec="seconds")
    return {
        "saved": True,
        "count": len(data["discovered"]),
        "rpc_url": data["rpc_url"],
        "mode": data["mode"],
        "from_block": data["from_block"],
        "to_block": data["to_block"],
        "actual_from_block": data["actual_from_block"],
        "actual_to_block": data["actual_to_block"],
        "lookback_blocks": data["lookback_blocks"],
        "discovery_cursor": data["registry"].get("discovery_cursor"),
        "retention_days": ctx.liquidation_retention_days(),
        "recent_discovery_days": ctx.liquidation_recent_discovery_days(),
        "backfill_window_days": ctx.liquidation_backfill_window_days(),
        "stage": "borrowers",
        "scan_start_at": data["scan_start_at"].isoformat(timespec="seconds"),
        "scan_end_at": data["scan_end_at"].isoformat(timespec="seconds"),
        "registry_window": ctx.liquidation_account_registry_window(),
        "candidate_source_counts": {
            "onchain_borrow_logs": onchain_count,
            "external_index_coarse": external_summary["count"],
        },
        "onchain_log_count": onchain_count,
        "external_index_count": external_summary["count"],
        "external_index_enabled": external_summary["enabled"],
        "external_index_configured": external_summary["configured"],
        "external_index": external_summary,
        "requires_onchain_verification": True,
    }
