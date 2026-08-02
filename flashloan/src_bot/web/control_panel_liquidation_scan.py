import threading
import time
import sys
from datetime import datetime, timezone
from typing import Optional

from web3 import Web3

from core.sensitive_data import redact_sensitive_text
from execution.liquidation_scan import (
    build_user_liquidation_report,
    health_factor_band,
    load_account_addresses,
    load_reserve_assets_for_scan,
    scan_account_health,
    watched_health_rows,
)
from db.storage_liquidation_pool import (
    load_liquidation_borrow_health_scan_batches as db_load_liquidation_borrow_health_scan_batches,
    load_liquidation_core_opportunity_pool as db_load_liquidation_core_opportunity_pool,
    load_liquidation_high_frequency_pool as db_load_liquidation_high_frequency_pool,
    record_liquidation_borrow_health_scan_batch as db_record_liquidation_borrow_health_scan_batch,
)
from db.storage_liquidation import (
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    record_liquidation_account_scan,
)
import web.control_panel_liquidation_base as liquidation_base
from web.liquidation_account_backfill import AccountBackfillService
from web.debt_pool_workflow import decision_from_borrow_pool_payload
from web.liquidation_discovery_service import build_discovery_window_result
from web.liquidation_discovery_workflow import (
    discover_and_sync_liquidation_accounts as run_liquidation_discovery_workflow,
)
from web.liquidation_scan_presenter import (
    account_tier_summary as build_account_tier_summary,
    attach_scan_state,
    build_borrow_pool_summary,
    build_health_summary,
    display_health_rows,
)
from web.liquidation_scan_summary_service import (
    build_liquidation_account_summary,
    build_liquidation_health_summary,
)

globals().update({name: value for name, value in vars(liquidation_base).items() if not name.startswith("_")})

AAVE_RESERVE_SYMBOL_LIMIT = 1000
LIQUIDATION_ACCOUNT_BACKFILL_SERVICE = AccountBackfillService()
LIQUIDATION_ACCOUNT_BACKFILL_CACHE = LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.cache
LIQUIDATION_ACCOUNT_BACKFILL_LOCK = LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.lock
LIQUIDATION_ACCOUNT_BACKFILL_STOP = LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.stop_event


def _scan_error_message(error: object | None) -> str | None:
    if error is None:
        return None
    return redact_sensitive_text(error)


def account_backfill_status_payload() -> dict:
    return LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.status_payload()


def request_stop_account_backfill() -> dict:
    return LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.request_stop()


def _set_account_backfill_progress(progress: dict) -> None:
    LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.set_progress(progress)


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
        chunk_size, _ = parse_env_int("LIQUIDATION_BORROW_SCAN_CHUNK_SIZE", 1000, minimum=1)
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
                last_error = _scan_error_message(exc)
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
        safe_error = _scan_error_message(exc)
        result = {"started": True, "saved": False, "count": 0, "error": safe_error, "mode": "one-year-gap-fill"}
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["last_result"] = result
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["error"] = safe_error
        return result
    finally:
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["running"] = False
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stop_requested"] = bool(LIQUIDATION_ACCOUNT_BACKFILL_STOP.is_set())
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_ACCOUNT_BACKFILL_CACHE["stage"] = "idle"
        LIQUIDATION_ACCOUNT_BACKFILL_LOCK.release()


def start_account_backfill_background() -> dict:
    return LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.start_background(run_account_backfill_once)


def discover_and_sync_liquidation_accounts(force_full: bool = False) -> dict:
    return run_liquidation_discovery_workflow(sys.modules[__name__], force_full=force_full)


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
            last_error = _scan_error_message(exc)
    return None, [], last_error or "unable to load reserve assets"


def liquidation_health_summary(
    rows: list[dict],
    account_count: int,
    account_source: str,
    config: LiquidationScanConfig,
    rpc_url: Optional[str],
    error: Optional[str],
) -> dict:
    return build_health_summary(
        rows,
        account_count=account_count,
        account_source=account_source,
        config=config,
        rpc_url=rpc_url,
        error=error,
        registry_window=liquidation_account_registry_window(),
        scan_cache=LIQUIDATION_SCAN_CACHE,
        discovery_cache=LIQUIDATION_DISCOVERY_CACHE,
        retention_days=liquidation_retention_days(),
        discovery_interval_seconds=liquidation_discovery_interval_seconds(),
        backfill_interval_seconds=liquidation_backfill_interval_seconds(),
        recent_discovery_days=liquidation_recent_discovery_days(),
        backfill_window_days=liquidation_backfill_window_days(),
    )


def liquidation_health_with_scan_state(
    payload: dict,
    ttl_seconds: float,
    *,
    running: bool,
    cache_age_seconds: Optional[float] = None,
    cooldown_remaining_seconds: Optional[float] = None,
) -> dict:
    return attach_scan_state(
        payload,
        ttl_seconds,
        scan_cache=LIQUIDATION_SCAN_CACHE,
        running=running,
        cache_age_seconds=cache_age_seconds,
        cooldown_remaining_seconds=cooldown_remaining_seconds,
    )


def liquidation_health_display_rows(rows: list[dict], limit: int) -> list[dict]:
    return display_health_rows(rows, limit=limit, band=health_factor_band)


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
    return build_account_tier_summary(liquidation_account_registry_window())


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
    scan_summary = dict((scan_payload or {}).get("summary") or {})
    return build_borrow_pool_summary(
        rows,
        config=liquidation_scan_config(),
        display_limit=liquidation_borrow_pool_display_limit(),
        scan_cache=LIQUIDATION_SCAN_CACHE,
        scan_interval_seconds=liquidation_scan_interval_seconds(),
        account_tiers=scan_summary.get("account_tiers") or liquidation_account_tier_summary(),
        scanned=scanned,
        scan_payload=scan_payload,
    )


def _borrow_pool_payload_with_suppression(
    payload: dict,
    ttl_seconds: float,
    *,
    cache_age_seconds: float,
) -> dict:
    current = dict(payload)
    summary = dict(current.get("summary") or {})
    summary.update(
        {
            "scanned": False,
            "scan_running": bool(LIQUIDATION_SCAN_CACHE.get("running")),
            "scan_started_at": LIQUIDATION_SCAN_CACHE.get("started_at"),
            "scan_finished_at": LIQUIDATION_SCAN_CACHE.get("finished_at"),
            "stage": LIQUIDATION_SCAN_CACHE.get("stage") or summary.get("stage") or "idle",
            "scan_interval_seconds": ttl_seconds,
            "scan_cache_age_seconds": max(0.0, cache_age_seconds),
            "scan_cooldown_remaining_seconds": max(0.0, ttl_seconds - cache_age_seconds),
            "scan_suppressed": True,
            "suppression_reason": "scan interval not reached",
        }
    )
    current["summary"] = summary
    return current


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
        payload = {"rows": rows, "tiers": tiers, "latest_batch": latest_batch, "scan_configs": scan_configs, "summary": summary}
        payload["debt_pool_decision"] = decision_from_borrow_pool_payload(payload)
        return payload
    except Exception as exc:
        return {"rows": [], "summary": {"configured": True, "error": _scan_error_message(exc)}}


def liquidation_borrow_pool_scan_payload(force: bool = False) -> dict:
    now = time.monotonic()
    ttl_seconds = liquidation_scan_interval_seconds()
    cached_payload = LIQUIDATION_SCAN_CACHE.get("borrow_pool_payload")
    updated_at = float(LIQUIDATION_SCAN_CACHE.get("borrow_pool_updated_at") or 0.0)
    cache_age_seconds = now - updated_at
    if not force and isinstance(cached_payload, dict) and cache_age_seconds < ttl_seconds:
        return _borrow_pool_payload_with_suppression(
            cached_payload,
            ttl_seconds,
            cache_age_seconds=cache_age_seconds,
        )
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
    LIQUIDATION_SCAN_CACHE["stage"] = "debt_pool"
    LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": 0, "scanned_count": 0}
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
        LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": len(accounts), "scanned_count": 0}
        if not accounts:
            error = "database liquidation account table is empty"
        for candidate in aave_rpc_urls() if accounts else []:
            try:
                LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": len(accounts), "scanned_count": 0, "rpc_url": candidate}
                rows = scan_account_health(accounts, os.getenv("AAVE_POOL_ADDRESS", "").strip(), candidate, config)
                LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": len(accounts), "scanned_count": len(accounts), "rpc_url": candidate}
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
                error = _scan_error_message(exc)
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
        LIQUIDATION_SCAN_CACHE["last_result"] = scan_payload["summary"]
        scan_configs = db_load_liquidation_scan_config_library(database_url, limit=20)
        payload = {
            "rows": rows,
            "tiers": tiers,
            "latest_batch": latest_batch,
            "scan_configs": scan_configs,
            "summary": liquidation_borrow_pool_summary(rows, scanned=True, scan_payload=scan_payload),
        }
        decision = decision_from_borrow_pool_payload(payload)
        payload["debt_pool_decision"] = decision
        LIQUIDATION_SCAN_CACHE["last_result"] = {**scan_payload["summary"], "debt_pool_decision": decision}
        LIQUIDATION_SCAN_CACHE["borrow_pool_updated_at"] = time.monotonic()
        LIQUIDATION_SCAN_CACHE["borrow_pool_payload"] = payload
        return payload
    except Exception as exc:
        safe_error = _scan_error_message(exc)
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
                error=safe_error,
                metadata={"account_tiers": liquidation_account_tier_summary()},
            )
        except Exception:
            latest_batch = None
        return {"rows": [], "latest_batch": latest_batch, "summary": {"configured": True, "error": safe_error, "latest_batch": latest_batch}}
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
    LIQUIDATION_SCAN_CACHE["stage"] = "health"
    LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": 0, "scanned_count": 0}
    accounts: list[str] = []
    account_source = "none"
    auto_discovered = False
    rows: list[dict] = []
    rpc_url = None
    error = None
    try:
        accounts, account_source = load_liquidation_account_registry(force=force)
        LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": len(accounts), "scanned_count": 0, "source": account_source}
        if not accounts and os.getenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true").strip().lower() not in {"0", "false", "no"}:
            discovery = discover_and_sync_liquidation_accounts(force_full=force)
            if discovery.get("saved"):
                accounts, account_source = load_liquidation_account_registry(force=True)
                LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": len(accounts), "scanned_count": 0, "source": account_source}
                auto_discovered = True
                rpc_url = str(discovery.get("rpc_url") or "") or None
                error = None
                account_source = "database"
            elif discovery.get("error"):
                error = _scan_error_message(discovery.get("error"))
            if not accounts and not error:
                error = "liquidation account registry is empty"
        if accounts:
            for candidate in aave_rpc_urls():
                try:
                    LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": len(accounts), "scanned_count": 0, "rpc_url": candidate, "source": account_source}
                    rows = scan_account_health(accounts, os.getenv("AAVE_POOL_ADDRESS", "").strip(), candidate, config)
                    LIQUIDATION_SCAN_CACHE["progress"] = {"account_count": len(accounts), "scanned_count": len(accounts), "rpc_url": candidate, "source": account_source}
                    record_liquidation_health_scan_rows(rows)
                    rpc_url = candidate
                    error = None
                    break
                except Exception as exc:
                    error = _scan_error_message(exc)
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
        LIQUIDATION_SCAN_CACHE["last_result"] = payload["summary"]
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
