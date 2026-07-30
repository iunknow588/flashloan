import threading
import time
from datetime import datetime, timezone
from typing import Optional

from web3 import Web3

from execution.liquidation_scan import (
    build_user_liquidation_report,
    health_factor_band,
    load_account_addresses,
    load_reserve_assets_for_scan,
    scan_account_health,
    watched_health_rows,
)
from db.storage import record_liquidation_account_scan
from web.control_panel_liquidation_base import *

AAVE_RESERVE_SYMBOL_LIMIT = 1000


def discover_and_sync_liquidation_accounts(force_full: bool = False) -> dict:
    if not database_url_or_none():
        return {"saved": False, "count": 0, "error": "DATABASE_URL is required"}
    if os.getenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true").strip().lower() in {"0", "false", "no"}:
        return {"saved": False, "count": 0, "error": "auto discovery disabled"}
    if not LIQUIDATION_DISCOVERY_LOCK.acquire(blocking=False):
        result = dict(LIQUIDATION_DISCOVERY_CACHE.get("last_result") or {})
        result["running"] = True
        return result

    LIQUIDATION_DISCOVERY_CACHE["running"] = True
    LIQUIDATION_DISCOVERY_CACHE["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LIQUIDATION_DISCOVERY_CACHE["finished_at"] = None
    try:
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        if not pool_address:
            return {"saved": False, "count": 0, "error": "missing AAVE_POOL_ADDRESS"}
        scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode = liquidation_discovery_window(force_full=force_full)
        if force_full and scan_end_at <= scan_start_at:
            result = {
                "saved": False,
                "count": 0,
                "skipped": True,
                "reason": "historical backfill complete",
                "mode": mode,
                "discovery_cursor": registry.get("discovery_cursor"),
                "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                "registry_window": registry,
            }
            LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
            return result
        if not force_full and (scan_end_at - scan_start_at).total_seconds() < liquidation_discovery_interval_seconds():
            result = {
                "saved": False,
                "count": 0,
                "skipped": True,
                "reason": "discovery interval not reached",
                "mode": mode,
                "discovery_cursor": registry.get("discovery_cursor"),
                "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                "registry_window": registry,
            }
            LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
            return result
        chunk_size = int(os.getenv("LIQUIDATION_BORROW_SCAN_CHUNK_SIZE", "1000"))
        limit = min(liquidation_scan_config().max_candidates, int(os.getenv("LIQUIDATION_BORROW_DISCOVERY_LIMIT", "5000")))
        last_error = None
        for candidate in aave_rpc_urls():
            actual_from_block = 0
            actual_to_block = 0
            try:
                _, actual_from_block, actual_to_block = resolve_discovery_block_range(candidate, from_block, to_block)
                if actual_from_block > actual_to_block:
                    discovered = []
                else:
                    discovered = discover_borrower_addresses(
                        candidate,
                        pool_address,
                        actual_from_block,
                        to_block=actual_to_block,
                        chunk_size=chunk_size,
                        limit=limit,
                    )
                sync_liquidation_accounts_to_database(
                    discovered,
                    source="auto-discovery",
                    scan_start_at=scan_start_at,
                    scan_end_at=scan_end_at,
                    update_existing=False,
                )
                LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
                progress = dict((registry.get("discovery_scan_progress") or {}))
                continuity_error = discovery_window_continuity_error(mode, actual_from_block, actual_to_block, progress)
                if continuity_error:
                    result = {
                        "saved": False,
                        "count": len(discovered),
                        "skipped": True,
                        "reason": continuity_error,
                        "mode": mode,
                        "rpc_url": candidate,
                        "from_block": from_block,
                        "to_block": to_block,
                        "actual_from_block": actual_from_block,
                        "actual_to_block": actual_to_block,
                        "lookback_blocks": lookback_blocks,
                        "discovery_cursor": registry.get("discovery_cursor"),
                        "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                        "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                        "registry_window": registry,
                    }
                    LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
                    return result
                record_liquidation_discovery_window(
                    mode=mode,
                    status="success",
                    rpc_url=candidate,
                    pool_address=pool_address,
                    from_block=actual_from_block,
                    to_block=actual_to_block,
                    scan_start_at=scan_start_at,
                    scan_end_at=scan_end_at,
                    discovered_count=len(discovered),
                )
                result = {
                    "saved": True,
                    "count": len(discovered),
                    "rpc_url": candidate,
                    "mode": mode,
                    "from_block": from_block,
                    "to_block": to_block,
                    "actual_from_block": actual_from_block,
                    "actual_to_block": actual_to_block,
                    "lookback_blocks": lookback_blocks,
                    "discovery_cursor": registry.get("discovery_cursor"),
                    "retention_days": liquidation_retention_days(),
                    "recent_discovery_days": liquidation_recent_discovery_days(),
                    "backfill_window_days": liquidation_backfill_window_days(),
                    "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                    "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                    "registry_window": liquidation_account_registry_window(),
                }
                if force_full:
                    LIQUIDATION_DISCOVERY_CACHE["last_backfill_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    LIQUIDATION_DISCOVERY_CACHE["last_backfill_monotonic"] = time.monotonic()
                    LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = scan_start_at.isoformat(timespec="seconds")
                LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
                return result
            except Exception as exc:
                last_error = str(exc)
                if actual_from_block <= actual_to_block:
                    record_liquidation_discovery_window(
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
            "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
            "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
        }
        LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
        return result
    finally:
        LIQUIDATION_DISCOVERY_CACHE["running"] = False
        LIQUIDATION_DISCOVERY_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_DISCOVERY_LOCK.release()


def normalize_liquidation_account_values(values: object) -> list[str]:
    if isinstance(values, str):
        raw_items = values.replace("\r", "\n").replace(",", "\n").splitlines()
    elif isinstance(values, list):
        raw_items = values
    else:
        raw_items = []
    normalized: list[str] = []
    for item in raw_items:
        try:
            checksum = Web3.to_checksum_address(str(item).strip())
        except ValueError:
            continue
        if checksum not in normalized:
            normalized.append(checksum)
    return normalized


def start_liquidation_refresh_loop() -> None:
    global LIQUIDATION_REFRESH_THREAD
    if LIQUIDATION_REFRESH_THREAD is not None and LIQUIDATION_REFRESH_THREAD.is_alive():
        return
    if not liquidation_background_refresh_enabled():
        return

    def runner() -> None:
        while not LIQUIDATION_REFRESH_STOP.is_set():
            try:
                discover_and_sync_liquidation_accounts(force_full=False)
                liquidation_health_payload(force=True)
                last_backfill_at = float(LIQUIDATION_DISCOVERY_CACHE.get("last_backfill_monotonic") or 0.0)
                if time.monotonic() - last_backfill_at >= liquidation_backfill_interval_seconds():
                    discover_and_sync_liquidation_accounts(force_full=True)
            except Exception:
                pass
            LIQUIDATION_REFRESH_STOP.wait(liquidation_scan_interval_seconds())

    LIQUIDATION_REFRESH_THREAD = threading.Thread(target=runner, name="liquidation-refresh", daemon=True)
    LIQUIDATION_REFRESH_THREAD.start()


def initialize_liquidation_runtime() -> None:
    discover_and_sync_liquidation_accounts(force_full=False)
    load_liquidation_account_registry(force=True)
    start_liquidation_refresh_loop()


def scan_context_assets() -> tuple[Optional[str], list[dict], Optional[str]]:
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    if not pool_address:
        return None, [], "missing AAVE_POOL_ADDRESS"
    last_error: Optional[str] = None
    for candidate in aave_rpc_urls():
        try:
            assets = load_reserve_assets_for_scan(candidate, pool_address, limit=AAVE_RESERVE_SYMBOL_LIMIT)
            if assets:
                return candidate, assets, None
        except Exception as exc:
            last_error = str(exc)
    return None, [], last_error or "unable to load reserve assets"


def liquidation_health_summary(
    rows: list[dict],
    account_count: int,
    account_source: str,
    config: LiquidationScanConfig,
    rpc_url: Optional[str],
    error: Optional[str],
) -> dict:
    liquidatable_count = sum(1 for row in rows if row.get("status") == "liquidatable")
    warning_count = sum(1 for row in rows if row.get("status") == "warning")
    healthy_count = sum(1 for row in rows if row.get("status") == "healthy")
    worst_row = rows[0] if rows else None
    return {
        "account_source": account_source,
        "source_ready": account_count > 0,
        "account_count": account_count,
        "scanned_count": len(rows),
        "liquidatable_count": liquidatable_count,
        "warning_count": warning_count,
        "healthy_count": healthy_count,
        "warning_health_factor": config.warning_health_factor,
        "liquidation_health_factor": config.liquidation_health_factor,
        "wide_scan_seconds": config.wide_scan_seconds,
        "near_scan_seconds": config.near_scan_seconds,
        "rpc_url": rpc_url,
        "error": error,
        "worst_account": worst_row.get("account") if worst_row else None,
        "worst_health_factor": worst_row.get("health_factor") if worst_row else None,
        "retention_days": liquidation_retention_days(),
        "registry_window": liquidation_account_registry_window(),
        "scan_running": bool(LIQUIDATION_SCAN_CACHE.get("running")),
        "scan_started_at": LIQUIDATION_SCAN_CACHE.get("started_at"),
        "scan_finished_at": LIQUIDATION_SCAN_CACHE.get("finished_at"),
        "discovery_running": bool(LIQUIDATION_DISCOVERY_CACHE.get("running")),
        "discovery_started_at": LIQUIDATION_DISCOVERY_CACHE.get("started_at"),
        "discovery_finished_at": LIQUIDATION_DISCOVERY_CACHE.get("finished_at"),
        "discovery_last_result": LIQUIDATION_DISCOVERY_CACHE.get("last_result"),
        "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
        "backfill_interval_seconds": liquidation_backfill_interval_seconds(),
        "last_backfill_at": LIQUIDATION_DISCOVERY_CACHE.get("last_backfill_at"),
        "historical_cursor_at": LIQUIDATION_DISCOVERY_CACHE.get("historical_cursor_at"),
        "recent_discovery_days": liquidation_recent_discovery_days(),
        "backfill_window_days": liquidation_backfill_window_days(),
    }


def liquidation_health_with_scan_state(
    payload: dict,
    ttl_seconds: float,
    *,
    running: bool,
    cache_age_seconds: Optional[float] = None,
    cooldown_remaining_seconds: Optional[float] = None,
) -> dict:
    current = dict(payload)
    summary = dict(current.get("summary") or {})
    summary["scan_running"] = running
    summary["scan_started_at"] = LIQUIDATION_SCAN_CACHE.get("started_at")
    summary["scan_finished_at"] = LIQUIDATION_SCAN_CACHE.get("finished_at")
    summary["scan_interval_seconds"] = ttl_seconds
    if cache_age_seconds is not None:
        summary["scan_cache_age_seconds"] = max(0.0, cache_age_seconds)
    if cooldown_remaining_seconds is not None:
        summary["scan_cooldown_remaining_seconds"] = max(0.0, cooldown_remaining_seconds)
    current["summary"] = summary
    return current


def liquidation_health_display_rows(rows: list[dict], limit: int) -> list[dict]:
    ranked = []
    for row in rows:
        item = dict(row)
        try:
            item["health_factor_band"] = item.get("health_factor_band") or health_factor_band(float(item.get("health_factor")))
        except (TypeError, ValueError):
            pass
        ranked.append(item)
    ranked.sort(
        key=lambda row: (
            1 if row.get("status") == "error" else 0,
            float(row.get("health_factor", 10.0)) if isinstance(row.get("health_factor"), (int, float)) else 10.0,
            str(row.get("account") or ""),
        )
    )
    return ranked[:limit]


def record_liquidation_health_scan_rows(rows: list[dict]) -> None:
    database_url = database_url_or_none()
    if not database_url or not rows:
        return
    try:
        for row in rows:
            report = {
                "account": row.get("account"),
                "summary": {
                    "health_factor": row.get("health_factor"),
                    "status": row.get("status"),
                    "health_factor_band": row.get("health_factor_band"),
                    "candidate_count": len(row.get("liquidation_candidates") or []),
                },
                "liquidation_profit": row.get("liquidation_profit"),
            }
            record_liquidation_account_scan(database_url, report)
        db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
    except Exception:
        pass


def liquidation_health_payload(force: bool = False) -> dict:
    now = time.monotonic()
    ttl_seconds = liquidation_scan_interval_seconds()
    cached_payload = LIQUIDATION_SCAN_CACHE.get("payload")
    updated_at = float(LIQUIDATION_SCAN_CACHE.get("updated_at") or 0.0)
    cache_age_seconds = now - updated_at
    if not force and cached_payload and cache_age_seconds < ttl_seconds:
        return liquidation_health_with_scan_state(
            cached_payload,  # type: ignore[arg-type]
            ttl_seconds,
            running=bool(LIQUIDATION_SCAN_CACHE.get("running")),
            cache_age_seconds=cache_age_seconds,
            cooldown_remaining_seconds=ttl_seconds - cache_age_seconds,
        )
    if not LIQUIDATION_SCAN_LOCK.acquire(blocking=False):
        if cached_payload:
            return liquidation_health_with_scan_state(
                cached_payload,  # type: ignore[arg-type]
                ttl_seconds,
                running=True,
                cache_age_seconds=cache_age_seconds if updated_at else None,
                cooldown_remaining_seconds=0.0,
            )
        return {
            "rows": [],
            "summary": {
                "scan_running": True,
                "scan_started_at": LIQUIDATION_SCAN_CACHE.get("started_at"),
                "scan_finished_at": LIQUIDATION_SCAN_CACHE.get("finished_at"),
                "scan_interval_seconds": ttl_seconds,
                "account_count": 0,
                "scanned_count": 0,
            },
        }

    config = liquidation_scan_config()
    LIQUIDATION_SCAN_CACHE["running"] = True
    LIQUIDATION_SCAN_CACHE["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LIQUIDATION_SCAN_CACHE["finished_at"] = None
    accounts: list[str] = []
    account_source = "none"
    auto_discovered = False
    rows: list[dict] = []
    rpc_url = None
    error = None
    try:
        accounts, account_source = load_liquidation_account_registry(force=force)
        if not accounts and os.getenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true").strip().lower() not in {"0", "false", "no"}:
            discovery = discover_and_sync_liquidation_accounts(force_full=force)
            if discovery.get("saved"):
                accounts, account_source = load_liquidation_account_registry(force=True)
                auto_discovered = True
                rpc_url = str(discovery.get("rpc_url") or "") or None
                error = None
                account_source = "database"
            elif discovery.get("error"):
                error = str(discovery.get("error"))
            if not accounts and not error:
                error = "liquidation account registry is empty"
        if accounts:
            for candidate in aave_rpc_urls():
                try:
                    rows = scan_account_health(accounts, os.getenv("AAVE_POOL_ADDRESS", "").strip(), candidate, config)
                    record_liquidation_health_scan_rows(rows)
                    rpc_url = candidate
                    error = None
                    break
                except Exception as exc:
                    error = str(exc)
        if not accounts and not error:
            error = "liquidation account registry is empty"
        display_limit = liquidation_health_display_limit()
        watched_rows = watched_health_rows(rows, config.watch_health_factor)
        payload = {
            "rows": liquidation_health_display_rows(rows, display_limit),
            "watched_rows": watched_rows[:display_limit],
            "summary": liquidation_health_summary(rows, len(accounts), account_source, config, rpc_url, error) | {
                "auto_discovered": auto_discovered,
                "watch_health_factor": config.watch_health_factor,
                "display_limit": display_limit,
                "displayed_count": min(len(rows), display_limit),
                "scan_interval_seconds": ttl_seconds,
                "watch_count": len(watched_rows),
            },
        }
        LIQUIDATION_SCAN_CACHE["running"] = False
        LIQUIDATION_SCAN_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = liquidation_health_with_scan_state(
            payload,
            ttl_seconds,
            running=False,
            cache_age_seconds=0.0,
            cooldown_remaining_seconds=ttl_seconds,
        )
        LIQUIDATION_SCAN_CACHE["updated_at"] = time.monotonic()
        LIQUIDATION_SCAN_CACHE["payload"] = payload
        return payload
    finally:
        LIQUIDATION_SCAN_CACHE["running"] = False
        if not LIQUIDATION_SCAN_CACHE.get("finished_at"):
            LIQUIDATION_SCAN_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_SCAN_LOCK.release()


def liquidation_account_payload(account: str) -> dict:
    raw_account = account.strip()
    if not raw_account:
        raise ValueError("account is required")
    checksum = Web3.to_checksum_address(raw_account)
    rpc_url, reserve_assets, asset_error = scan_context_assets()
    if not rpc_url:
        raise RuntimeError(asset_error or "unable to resolve rpc_url")
    report = build_user_liquidation_report(
        checksum,
        rpc_url,
        os.getenv("AAVE_POOL_ADDRESS", "").strip(),
        reserve_assets,
        protocol_data_provider_address(),
        liquidation_data_provider_address(),
        liquidation_scan_config(),
    )
    try:
        database_url = database_url_or_none()
        if database_url:
            record_liquidation_account_scan(database_url, report)
            db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
    except Exception:
        pass
    report["context"] = {
        "rpc_url": rpc_url,
        "pool_address": os.getenv("AAVE_POOL_ADDRESS", "").strip(),
        "protocol_data_provider_address": protocol_data_provider_address() or None,
        "liquidation_data_provider_address": liquidation_data_provider_address() or None,
        "reserve_asset_count": len(reserve_assets),
        "error": asset_error,
    }
    return report
