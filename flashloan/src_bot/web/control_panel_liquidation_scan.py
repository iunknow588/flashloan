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
from db.storage import (
    load_liquidation_borrow_health_scan_batches as db_load_liquidation_borrow_health_scan_batches,
    load_liquidation_core_opportunity_pool as db_load_liquidation_core_opportunity_pool,
    load_liquidation_high_frequency_pool as db_load_liquidation_high_frequency_pool,
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    record_liquidation_account_scan,
    record_liquidation_borrow_health_scan_batch as db_record_liquidation_borrow_health_scan_batch,
)
from web.control_panel_liquidation_base import *

AAVE_RESERVE_SYMBOL_LIMIT = 1000
LIQUIDATION_ACCOUNT_BACKFILL_CACHE: dict[str, object] = {
    "running": False,
    "stop_requested": False,
    "started_at": None,
    "finished_at": None,
    "stage": "idle",
    "last_result": None,
    "progress": {},
    "error": None,
}
LIQUIDATION_ACCOUNT_BACKFILL_LOCK = threading.Lock()
LIQUIDATION_ACCOUNT_BACKFILL_STOP = threading.Event()
LIQUIDATION_ACCOUNT_BACKFILL_THREAD: Optional[threading.Thread] = None


def account_backfill_status_payload() -> dict:
    payload = dict(LIQUIDATION_ACCOUNT_BACKFILL_CACHE)
    payload["running"] = bool(payload.get("running"))
    payload["stop_requested"] = bool(payload.get("stop_requested"))
    return payload


def request_stop_account_backfill() -> dict:
    LIQUIDATION_ACCOUNT_BACKFILL_STOP.set()
    LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stop_requested"] = True
    if not LIQUIDATION_ACCOUNT_BACKFILL_CACHE.get("running"):
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stage"] = "idle"
    return account_backfill_status_payload()


def _set_account_backfill_progress(progress: dict) -> None:
    current_from = int(progress.get("current_from_block") or 0)
    current_to = int(progress.get("current_to_block") or 0)
    start = int(progress.get("from_block") or 0)
    end = int(progress.get("to_block") or 0)
    total = max(1, end - start + 1)
    scanned = max(0, current_to - start + 1)
    percent = max(0.0, min(100.0, scanned / total * 100.0))
    LIQUIDATION_ACCOUNT_BACKFILL_CACHE["progress"] = {
        **progress,
        "percent": round(percent, 2),
        "scanned_blocks": scanned,
        "total_blocks": total,
    }


def run_account_backfill_once() -> dict:
    if not database_url_or_none():
        return {"started": False, "saved": False, "count": 0, "error": "DATABASE_URL is required"}
    if os.getenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true").strip().lower() in {"0", "false", "no"}:
        return {"started": False, "saved": False, "count": 0, "error": "auto discovery disabled"}
    if not LIQUIDATION_ACCOUNT_BACKFILL_LOCK.acquire(blocking=False):
        return {"started": False, "running": True, **account_backfill_status_payload()}

    LIQUIDATION_ACCOUNT_BACKFILL_STOP.clear()
    started_at = datetime.now(timezone.utc)
    LIQUIDATION_ACCOUNT_BACKFILL_CACHE.update(
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
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        if not pool_address:
            raise RuntimeError("missing AAVE_POOL_ADDRESS")
        scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode = liquidation_discovery_window(force_full=True)
        chunk_size = int(os.getenv("LIQUIDATION_BORROW_SCAN_CHUNK_SIZE", "1000"))
        last_error = None
        for rpc_url in aave_rpc_urls():
            if LIQUIDATION_ACCOUNT_BACKFILL_STOP.is_set():
                break
            actual_from_block = 0
            actual_to_block = 0
            try:
                LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stage"] = "resolving-blocks"
                _, actual_from_block, actual_to_block = resolve_discovery_block_range(rpc_url, from_block, to_block)
                LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stage"] = "borrowers"
                discovered = discover_borrower_addresses(
                    rpc_url,
                    pool_address,
                    actual_from_block,
                    to_block=actual_to_block,
                    chunk_size=chunk_size,
                    limit=0,
                    stop_event=LIQUIDATION_ACCOUNT_BACKFILL_STOP,
                    progress_callback=_set_account_backfill_progress,
                ) if actual_from_block <= actual_to_block else []
                LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stage"] = "saving"
                if discovered:
                    db_upsert_liquidation_accounts(
                        configured_database_url(),
                        discovered,
                        source="one-year-gap-fill",
                        active=True,
                        scan_start_at=scan_start_at,
                        scan_end_at=scan_end_at,
                        update_existing=True,
                    )
                LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
                status = "stopped" if LIQUIDATION_ACCOUNT_BACKFILL_STOP.is_set() else "success"
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
                    "registry_window": liquidation_account_registry_window(),
                    "progress": dict(LIQUIDATION_ACCOUNT_BACKFILL_CACHE.get("progress") or {}),
                }
                LIQUIDATION_ACCOUNT_BACKFILL_CACHE["last_result"] = result
                return result
            except Exception as exc:
                last_error = str(exc)
        result = {
            "started": True,
            "saved": False,
            "stopped": bool(LIQUIDATION_ACCOUNT_BACKFILL_STOP.is_set()),
            "count": 0,
            "error": None if LIQUIDATION_ACCOUNT_BACKFILL_STOP.is_set() else (last_error or "unable to backfill borrower addresses"),
            "mode": "one-year-gap-fill",
        }
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["last_result"] = result
        return result
    except Exception as exc:
        result = {"started": True, "saved": False, "count": 0, "error": str(exc), "mode": "one-year-gap-fill"}
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["last_result"] = result
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["error"] = str(exc)
        return result
    finally:
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["running"] = False
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stop_requested"] = bool(LIQUIDATION_ACCOUNT_BACKFILL_STOP.is_set())
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stage"] = "idle"
        LIQUIDATION_ACCOUNT_BACKFILL_LOCK.release()


def start_account_backfill_background() -> dict:
    global LIQUIDATION_ACCOUNT_BACKFILL_THREAD
    if LIQUIDATION_ACCOUNT_BACKFILL_THREAD is not None and LIQUIDATION_ACCOUNT_BACKFILL_THREAD.is_alive():
        return {"started": False, "running": True, **account_backfill_status_payload()}
    LIQUIDATION_ACCOUNT_BACKFILL_THREAD = threading.Thread(
        target=run_account_backfill_once,
        name="liquidation-account-backfill",
        daemon=True,
    )
    LIQUIDATION_ACCOUNT_BACKFILL_THREAD.start()
    return {"started": True, "running": True, **account_backfill_status_payload()}


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
    LIQUIDATION_DISCOVERY_CACHE["stage"] = "window"
    try:
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        if not pool_address:
            return {"saved": False, "count": 0, "error": "missing AAVE_POOL_ADDRESS"}
        scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode = liquidation_discovery_window(force_full=force_full)
        LIQUIDATION_DISCOVERY_CACHE["stage"] = "borrowers"
        if force_full and scan_end_at <= scan_start_at:
            result = {
                "saved": False,
                "count": 0,
                "skipped": True,
                "reason": "historical backfill complete",
                "mode": mode,
                "stage": LIQUIDATION_DISCOVERY_CACHE.get("stage"),
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
                "stage": LIQUIDATION_DISCOVERY_CACHE.get("stage"),
                "discovery_cursor": registry.get("discovery_cursor"),
                "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                "registry_window": registry,
            }
            LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
            return result
        chunk_size = int(os.getenv("LIQUIDATION_BORROW_SCAN_CHUNK_SIZE", "1000"))
        limit = 0 if force_full or not registry.get("discovery_scan_progress", {}).get("latest_recent_to_block") else min(liquidation_scan_config().max_candidates, int(os.getenv("LIQUIDATION_BORROW_DISCOVERY_LIMIT", "5000")))
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
                    update_existing=True,
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
                    "stage": "borrowers",
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
            "stage": LIQUIDATION_DISCOVERY_CACHE.get("stage"),
            "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
            "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
        }
        LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
        return result
    finally:
        LIQUIDATION_DISCOVERY_CACHE["running"] = False
        LIQUIDATION_DISCOVERY_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_DISCOVERY_CACHE["stage"] = "idle"
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
                liquidation_borrow_pool_scan_payload(force=True)
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
    liquidation_borrow_pool_scan_payload(force=True)
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
        "stage": LIQUIDATION_SCAN_CACHE.get("stage") or "idle",
        "discovery_running": bool(LIQUIDATION_DISCOVERY_CACHE.get("running")),
        "discovery_started_at": LIQUIDATION_DISCOVERY_CACHE.get("started_at"),
        "discovery_finished_at": LIQUIDATION_DISCOVERY_CACHE.get("finished_at"),
        "discovery_stage": LIQUIDATION_DISCOVERY_CACHE.get("stage") or "idle",
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
    summary["stage"] = LIQUIDATION_SCAN_CACHE.get("stage") or summary.get("stage") or "idle"
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
        db_sync_liquidation_borrow_health_pool(database_url, rows, liquidation_scan_config().watch_health_factor)
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


def liquidation_account_tier_summary() -> dict:
    window = liquidation_account_registry_window()
    active = int(window.get("active_count") or 0)
    hot = int(window.get("hot_count") or 0)
    warm = int(window.get("warm_count") or 0)
    cold = int(window.get("cold_count") or 0)
    return {
        "hot_count": hot,
        "warm_count": warm,
        "cold_count": cold,
        "classified_count": hot + warm + cold,
        "active_count": active,
    }


def liquidation_pool_tier_payload(database_url: str, limit: int) -> dict:
    high_frequency = db_load_liquidation_high_frequency_pool(database_url, limit=limit)
    core = db_load_liquidation_core_opportunity_pool(database_url, limit=limit)
    return {
        "high_frequency_rows": high_frequency,
        "core_opportunity_rows": core,
        "high_frequency_count": len(high_frequency),
        "core_opportunity_count": len(core),
    }


def prioritized_liquidation_accounts(database_url: str, accounts: list[str]) -> list[str]:
    account_set = set(accounts)
    ordered: list[str] = []

    def add(value: object) -> None:
        account = str(value or "")
        if account and account in account_set and account not in ordered:
            ordered.append(account)

    try:
        for row in db_load_liquidation_core_opportunity_pool(database_url, limit=len(accounts)):
            add(row.get("account"))
        for row in db_load_liquidation_high_frequency_pool(database_url, limit=len(accounts)):
            add(row.get("account"))
    except Exception:
        return accounts

    for account in accounts:
        add(account)
    return ordered


def latest_liquidation_borrow_pool_batch(database_url: str) -> Optional[dict]:
    batches = db_load_liquidation_borrow_health_scan_batches(database_url, limit=1)
    return batches[0] if batches else None


def liquidation_borrow_pool_summary(rows: list[dict], *, scanned: bool = False, scan_payload: Optional[dict] = None) -> dict:
    config = liquidation_scan_config()
    scan_summary = dict((scan_payload or {}).get("summary") or {})
    worst_row = rows[0] if rows else None
    account_tiers = scan_summary.get("account_tiers") or liquidation_account_tier_summary()
    latest_batch = scan_summary.get("latest_batch")
    return {
        "count": len(rows),
        "display_limit": liquidation_borrow_pool_display_limit(),
        "watch_health_factor": config.watch_health_factor,
        "worst_account": worst_row.get("account") if worst_row else None,
        "worst_health_factor": worst_row.get("health_factor") if worst_row else None,
        "scanned": scanned,
        "scan_running": bool(LIQUIDATION_SCAN_CACHE.get("running")),
        "scan_started_at": LIQUIDATION_SCAN_CACHE.get("started_at"),
        "scan_finished_at": LIQUIDATION_SCAN_CACHE.get("finished_at"),
        "stage": scan_summary.get("stage") or LIQUIDATION_SCAN_CACHE.get("stage") or "idle",
        "scan_interval_seconds": liquidation_scan_interval_seconds(),
        "source_account_count": scan_summary.get("account_count"),
        "scanned_account_count": scan_summary.get("scanned_count"),
        "risk_count": scan_summary.get("risk_count", len(rows)),
        "entered_count": scan_summary.get("entered_count"),
        "exited_count": scan_summary.get("exited_count"),
        "block_number": scan_summary.get("block_number"),
        "latest_batch": latest_batch,
        "account_tiers": account_tiers,
        "risk_pool_conversion_rate": (float(len(rows)) / float(account_tiers.get("active_count") or scan_summary.get("account_count") or 1)),
        "error": scan_summary.get("error"),
    }


def liquidation_borrow_pool_payload() -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"rows": [], "summary": {"configured": False, "error": "DATABASE_URL is required"}}
    try:
        ensure_database_schema(database_url)
        rows = db_load_liquidation_borrow_health_pool(database_url, limit=liquidation_borrow_pool_display_limit())
        tiers = liquidation_pool_tier_payload(database_url, liquidation_borrow_pool_display_limit())
        latest_batch = latest_liquidation_borrow_pool_batch(database_url)
        scan_configs = db_load_liquidation_scan_config_library(database_url, limit=20)
        summary = liquidation_borrow_pool_summary(rows, scan_payload={"summary": {"latest_batch": latest_batch}})
        return {"rows": rows, "tiers": tiers, "latest_batch": latest_batch, "scan_configs": scan_configs, "summary": summary}
    except Exception as exc:
        return {"rows": [], "summary": {"configured": True, "error": str(exc)}}


def liquidation_borrow_pool_scan_payload(force: bool = True) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"rows": [], "summary": {"configured": False, "error": "DATABASE_URL is required"}}
    if not LIQUIDATION_SCAN_LOCK.acquire(blocking=False):
        payload = liquidation_borrow_pool_payload()
        summary = dict(payload.get("summary") or {})
        summary["scan_running"] = True
        summary["scan_started_at"] = LIQUIDATION_SCAN_CACHE.get("started_at")
        summary["stage"] = LIQUIDATION_SCAN_CACHE.get("stage") or "debt_pool"
        payload["summary"] = summary
        return payload
    config = liquidation_scan_config()
    LIQUIDATION_SCAN_CACHE["running"] = True
    LIQUIDATION_SCAN_CACHE["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LIQUIDATION_SCAN_CACHE["finished_at"] = None
    LIQUIDATION_SCAN_CACHE["stage"] = "health"
    LIQUIDATION_SCAN_CACHE["stage"] = "debt_pool"
    accounts: list[str] = []
    rows: list[dict] = []
    rpc_url = None
    error = None
    sync_result: dict[str, int] = {}
    block_number = None
    started_at = datetime.now(timezone.utc)
    try:
        ensure_database_schema(database_url)
        accounts = db_load_liquidation_accounts(database_url)
        accounts = prioritized_liquidation_accounts(database_url, accounts)
        if not accounts:
            error = "database liquidation account table is empty"
        for candidate in aave_rpc_urls() if accounts else []:
            try:
                rows = scan_account_health(accounts, os.getenv("AAVE_POOL_ADDRESS", "").strip(), candidate, config)
                try:
                    block_number = int(Web3(Web3.HTTPProvider(candidate, request_kwargs={"timeout": 8})).eth.block_number)
                except Exception:
                    block_number = None
                sync_result = db_sync_liquidation_borrow_health_pool(database_url, rows, config.watch_health_factor)
                for row in rows:
                    report = {
                        "account": row.get("account"),
                        "summary": {
                            "health_factor": row.get("health_factor"),
                            "status": row.get("status"),
                            "health_factor_band": row.get("health_factor_band"),
                            "candidate_count": len(row.get("liquidation_candidates") or []),
                            "total_collateral_base": row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                            "total_debt_base": row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                        },
                        "liquidation_profit": row.get("liquidation_profit"),
                    }
                    record_liquidation_account_scan(database_url, report)
                db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
                rpc_url = candidate
                error = None
                break
            except Exception as exc:
                error = str(exc)
        rows = db_load_liquidation_borrow_health_pool(database_url, limit=liquidation_borrow_pool_display_limit())
        tiers = liquidation_pool_tier_payload(database_url, liquidation_borrow_pool_display_limit())
        finished_at = datetime.now(timezone.utc)
        latest_batch = db_record_liquidation_borrow_health_scan_batch(
            database_url,
            started_at=started_at,
            finished_at=finished_at,
            status="success" if rpc_url and not error else "error",
            account_count=len(accounts),
            scanned_count=len(accounts) if rpc_url else 0,
            risk_count=len(rows),
            error_count=0 if rpc_url and not error else (1 if error else 0),
            entered_count=sync_result.get("entered_count", 0),
            exited_count=sync_result.get("exited_count", 0),
            rpc_url=rpc_url,
            block_number=block_number,
            watch_health_factor=config.watch_health_factor,
            error=error,
            metadata={"tiers": tiers, "account_tiers": liquidation_account_tier_summary()},
        )
        scan_payload = {
            "summary": {
                "account_count": len(accounts),
                "scanned_count": len(accounts) if rpc_url else 0,
                "risk_count": len(rows),
                "entered_count": sync_result.get("entered_count", 0),
                "exited_count": sync_result.get("exited_count", 0),
                "rpc_url": rpc_url,
                "block_number": block_number,
                "latest_batch": latest_batch,
                "account_tiers": liquidation_account_tier_summary(),
                "stage": "debt_pool",
                "error": error,
            }
        }
        scan_configs = db_load_liquidation_scan_config_library(database_url, limit=20)
        return {
            "rows": rows,
            "tiers": tiers,
            "latest_batch": latest_batch,
            "scan_configs": scan_configs,
            "summary": liquidation_borrow_pool_summary(rows, scanned=True, scan_payload=scan_payload),
        }
    except Exception as exc:
        try:
            latest_batch = db_record_liquidation_borrow_health_scan_batch(
                database_url,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                status="error",
                account_count=len(accounts),
                scanned_count=0,
                risk_count=0,
                error_count=1,
                rpc_url=rpc_url,
                block_number=block_number,
                watch_health_factor=config.watch_health_factor,
                error=str(exc),
                metadata={"account_tiers": liquidation_account_tier_summary()},
            )
        except Exception:
            latest_batch = None
        return {"rows": [], "latest_batch": latest_batch, "summary": {"configured": True, "error": str(exc), "latest_batch": latest_batch}}
    finally:
        LIQUIDATION_SCAN_CACHE["running"] = False
        LIQUIDATION_SCAN_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_SCAN_CACHE["stage"] = "idle"
        LIQUIDATION_SCAN_LOCK.release()


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
        LIQUIDATION_SCAN_CACHE["stage"] = "idle"
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
        LIQUIDATION_SCAN_CACHE["stage"] = "idle"
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
