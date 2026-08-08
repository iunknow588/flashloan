import threading
import time
import sys
import hashlib
from datetime import datetime, timezone
from typing import Optional

from web3 import Web3

from core.market_config import liquidation_market_config, liquidation_market_config_for
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
    load_liquidation_borrow_health_pool as db_load_liquidation_borrow_health_pool,
    load_liquidation_core_opportunity_pool as db_load_liquidation_core_opportunity_pool,
    load_liquidation_high_frequency_pool as db_load_liquidation_high_frequency_pool,
    load_liquidation_pool_counts as db_load_liquidation_pool_counts,
    load_liquidation_account_pool_snapshot as db_load_liquidation_account_pool_snapshot,
    record_liquidation_borrow_health_scan_batch as db_record_liquidation_borrow_health_scan_batch,
    sync_liquidation_borrow_health_pool as db_sync_liquidation_borrow_health_pool,
)
from db.storage_liquidation_attempts import (
    load_latest_liquidation_execution_attempts_for_accounts as db_load_latest_liquidation_execution_attempts_for_accounts,
    load_recent_liquidation_execution_attempts as db_load_recent_liquidation_execution_attempts,
)
from db.storage_liquidation import (
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    record_liquidation_account_scan,
)
import web.control_panel_liquidation_base as liquidation_base
from debt_pool import decision_from_borrow_pool_payload
from liquidation import (
    AccountBackfillService,
    build_discovery_window_result,
    build_liquidation_account_summary,
    build_liquidation_health_summary,
)
from liquidation.discovery_workflow import (
    discover_and_sync_liquidation_accounts as run_liquidation_discovery_workflow,
)
from liquidation.scan_presenter import (
    account_tier_summary as build_account_tier_summary,
    attach_scan_state,
    build_borrow_pool_summary,
    build_health_summary,
    display_health_rows,
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
        pool_address = aave_pool_address()
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
                liquidation_borrow_pool_scan_payload(force=False)
                last_backfill_at = float(LIQUIDATION_DISCOVERY_CACHE.get("last_backfill_monotonic") or 0.0)
                if time.monotonic() - last_backfill_at >= liquidation_backfill_interval_seconds():
                    discover_and_sync_liquidation_accounts(force_full=True)
            except Exception:
                pass
            LIQUIDATION_REFRESH_STOP.wait(liquidation_borrow_pool_scan_cooldown_seconds())

    LIQUIDATION_REFRESH_THREAD = threading.Thread(target=runner, name="liquidation-refresh", daemon=True)
    LIQUIDATION_REFRESH_THREAD.start()


def initialize_liquidation_runtime() -> None:
    discover_and_sync_liquidation_accounts(force_full=False)
    load_liquidation_account_registry(force=True)
    liquidation_borrow_pool_scan_payload(force=True)
    start_liquidation_refresh_loop()


def scan_context_assets() -> tuple[Optional[str], list[dict], Optional[str]]:
    pool_address = aave_pool_address()
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
        reports = [
            {
                "account": row.get("account"),
                "summary": {
                    "health_factor": row.get("health_factor"),
                    "status": row.get("status"),
                    "health_factor_band": row.get("health_factor_band"),
                    "candidate_count": len(row.get("liquidation_candidates") or []),
                },
                "liquidation_profit": row.get("liquidation_profit"),
            }
            for row in rows
        ]
        config = liquidation_scan_config()
        db_sync_liquidation_borrow_health_pool(
            database_url,
            rows,
            config.watch_health_factor,
            account_reports=reports,
            min_operator_net_profit_usd=config.min_operator_net_profit_usd,
        )
        db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
    except Exception:
        pass


def liquidation_account_tier_summary() -> dict:
    return build_account_tier_summary(liquidation_account_registry_window())


def _page_value(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _page_size_value(value: object, default: int = 20) -> int:
    try:
        return max(1, min(int(value or default), 100))
    except (TypeError, ValueError):
        return default


def _pagination(page: int, page_size: int, total_count: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "page_count": max(1, (total_count + page_size - 1) // page_size),
    }


def _empty_tier_payload(page_size: int = 20, *, high_page: int = 1, core_page: int = 1) -> dict:
    return {
        "borrow_health_count": 0,
        "high_frequency_rows": [],
        "core_opportunity_rows": [],
        "high_frequency_count": 0,
        "core_opportunity_count": 0,
        "pagination": {
            "high_frequency": _pagination(_page_value(high_page), _page_size_value(page_size), 0),
            "core_opportunity": _pagination(_page_value(core_page), _page_size_value(page_size), 0),
        },
    }


def _database_url_fingerprint(database_url: str | None) -> str | None:
    if not database_url:
        return None
    return hashlib.sha256(database_url.encode("utf-8")).hexdigest()[:12]


def _scan_market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, object]:
    market = (
        liquidation_market_config_for(market_id, chain_id=chain_id)
        if market_id is not None or chain_id is not None
        else liquidation_market_config()
    )
    return {
        "market_id": market.market_id,
        "chain_id": market.chain_id,
        "network": market.network,
        "protocol": market.protocol,
    }


def _market_call_kwargs(market_id: str | None = None, chain_id: int | None = None) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if market_id is not None:
        kwargs["market_id"] = market_id
    if chain_id is not None:
        kwargs["chain_id"] = chain_id
    return kwargs


def _database_block_reason(configured: bool, error: str | None) -> str:
    if not configured:
        return "database_unconfigured"
    text = str(error or "").lower()
    if "endpoint has been disabled" in text:
        return "database_endpoint_disabled"
    if "terminating connection due to administrator command" in text:
        return "database_connection_terminated"
    return "database_unavailable"


def _database_next_action(reason: str) -> str:
    return {
        "database_unconfigured": "配置有效 DATABASE_URL 后重启 UI，再执行账户扫描。",
        "database_endpoint_disabled": "当前数据库端点已被服务商禁用；启用端点或替换 DATABASE_URL 后重启 UI，再继续扫描。",
        "database_connection_terminated": "数据库连接被管理端终止；确认端点状态后重启 UI，再继续扫描。",
        "database_unavailable": "数据库当前不可用或连接已失效；恢复数据库端点后重启 UI，再继续扫描。",
    }.get(reason, "恢复数据库连接后重启 UI，再继续扫描。")


def _borrow_pool_blocked_payload(
    *,
    configured: bool,
    error: str,
    page_size: int = 20,
    risk_page: int = 1,
    high_page: int = 1,
    core_page: int = 1,
    latest_batch: Optional[dict] = None,
) -> dict:
    page_size = _page_size_value(page_size)
    tiers = _empty_tier_payload(page_size, high_page=high_page, core_page=core_page)
    reason = _database_block_reason(configured, error)
    next_action = _database_next_action(reason)
    summary = {
        "configured": configured,
        "db_ready": False,
        "scan_blocked": True,
        "scan_blocked_reason": reason,
        "next_action": next_action,
        "error": error,
        "scanned": False,
        "scan_running": False,
        "stage": "database",
        "count": 0,
        "display_limit": page_size,
        "latest_batch": latest_batch,
    }
    payload = {
        "rows": [],
        "tiers": tiers,
        "latest_batch": latest_batch,
        "summary": summary,
        "pagination": {
            "borrow_health": _pagination(_page_value(risk_page), page_size, 0),
            **tiers["pagination"],
        },
    }
    payload["debt_pool_decision"] = decision_from_borrow_pool_payload(payload)
    return payload


def _core_execution_summary(row: dict, latest_attempt: Optional[dict] = None) -> dict:
    blocked_reasons = [str(reason) for reason in (row.get("blocked_reasons") or []) if reason]
    profit = float(row.get("estimated_operator_net_profit_usd") or 0.0)
    assessment = row.get("profit_assessment") if isinstance(row.get("profit_assessment"), dict) else {}
    if "account_not_liquidatable" in blocked_reasons:
        value_state = "not_liquidatable"
    elif profit < 1.0 or "profit_below_minimum" in blocked_reasons:
        value_state = "manual_test_under_1u"
    elif not row.get("best_debt_asset") or not row.get("best_collateral_asset") or "no_liquidation_candidate" in blocked_reasons:
        value_state = "no_executable_candidate"
    else:
        value_state = "worth_executing"

    if row.get("auto_execution_blocked"):
        execution_status = "blocked"
    elif not row.get("best_debt_asset") or not row.get("best_collateral_asset"):
        execution_status = "no_candidate"
    elif not row.get("quote_viable"):
        execution_status = "waiting_quote"
    elif str(row.get("static_call_status") or "").lower() in {"error", "failed"}:
        execution_status = "preflight_failed"
    elif str(row.get("static_call_status") or "").lower() == "passed":
        execution_status = "preflight_passed"
    else:
        execution_status = "waiting_preflight"

    latest = None
    latest_is_current = False
    if isinstance(latest_attempt, dict):
        row_scanned_at = _parse_core_execution_time(row.get("last_scanned_at"))
        attempt_created_at = _parse_core_execution_time(latest_attempt.get("created_at"))
        latest_is_current = not (row_scanned_at and attempt_created_at and attempt_created_at < row_scanned_at)
        latest = {
            "state": latest_attempt.get("state"),
            "execution_phase": latest_attempt.get("execution_phase"),
            "tx_hash": latest_attempt.get("tx_hash"),
            "created_at": latest_attempt.get("created_at"),
            "stale": not latest_is_current,
        }
    return {
        "value_state": value_state,
        "value_usd": profit,
        "above_auto_profit_threshold": bool(
            assessment.get("above_auto_profit_threshold", row.get("above_auto_profit_threshold"))
        ),
        "auto_execution_allowed": not bool(row.get("auto_execution_blocked")),
        "execution_status": execution_status,
        "execution_result": ((latest or {}).get("state") if latest_is_current else None) or "not_submitted",
        "blocked_reasons": blocked_reasons,
        "latest_attempt": latest,
    }


def _parse_core_execution_time(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _attach_core_execution_summaries(
    database_url: str,
    rows: list[dict],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict]:
    if not rows:
        return rows
    latest_by_account: dict[str, dict] = {}
    row_accounts = [str(row.get("account") or "") for row in rows if str(row.get("account") or "").strip()]
    try:
        attempts = db_load_latest_liquidation_execution_attempts_for_accounts(
            database_url,
            row_accounts,
            **_market_call_kwargs(market_id, chain_id),
        )
    except TypeError:
        if market_id is not None or chain_id is not None:
            raise
        try:
            attempts = db_load_latest_liquidation_execution_attempts_for_accounts(database_url, row_accounts)
        except Exception:
            attempts = []
    except Exception:
        attempts = []
    if not attempts:
        try:
            attempts = db_load_recent_liquidation_execution_attempts(
                database_url,
                limit=max(100, len(rows) * 5),
                **_market_call_kwargs(market_id, chain_id),
            )
        except TypeError:
            if market_id is not None or chain_id is not None:
                raise
            try:
                attempts = db_load_recent_liquidation_execution_attempts(database_url, limit=max(100, len(rows) * 5))
            except Exception:
                attempts = []
        except Exception:
            attempts = []
    try:
        for attempt in attempts:
            account = str(attempt.get("account") or "").lower()
            if account and account not in latest_by_account:
                latest_by_account[account] = attempt
    except Exception:
        latest_by_account = {}
    enriched: list[dict] = []
    for row in rows:
        item = dict(row)
        item["execution"] = _core_execution_summary(
            item,
            latest_by_account.get(str(item.get("account") or "").lower()),
        )
        enriched.append(item)
    return enriched


def liquidation_core_rows_with_execution(
    database_url: str,
    limit: int,
    offset: int = 0,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict]:
    market_kwargs = _market_call_kwargs(market_id, chain_id)
    try:
        rows = db_load_liquidation_core_opportunity_pool(
            database_url,
            limit=limit,
            offset=max(0, int(offset)),
            **market_kwargs,
        )
    except TypeError:
        if int(offset) != 0 or market_id is not None or chain_id is not None:
            raise
        rows = db_load_liquidation_core_opportunity_pool(database_url, limit=limit)
    return _attach_core_execution_summaries(database_url, rows, **market_kwargs)


def liquidation_pool_tier_payload(
    database_url: str,
    limit: int,
    *,
    high_page: int = 1,
    core_page: int = 1,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    market_kwargs = _market_call_kwargs(market_id, chain_id)
    page_size = _page_size_value(limit)
    high_page = _page_value(high_page)
    core_page = _page_value(core_page)
    high_frequency = db_load_liquidation_high_frequency_pool(
        database_url,
        limit=page_size,
        offset=(high_page - 1) * page_size,
        **market_kwargs,
    )
    core = liquidation_core_rows_with_execution(
        database_url,
        limit=page_size,
        offset=(core_page - 1) * page_size,
        **market_kwargs,
    )
    counts = db_load_liquidation_pool_counts(database_url, **market_kwargs)
    return {
        "borrow_health_count": counts["borrow_health_count"],
        "high_frequency_rows": high_frequency,
        "core_opportunity_rows": core,
        "high_frequency_count": counts["high_frequency_count"],
        "core_opportunity_count": counts["core_opportunity_count"],
        "pagination": {
            "high_frequency": _pagination(high_page, page_size, counts["high_frequency_count"]),
            "core_opportunity": _pagination(core_page, page_size, counts["core_opportunity_count"]),
        },
    }


def _unique_accounts(accounts: list[str]) -> list[str]:
    ordered: list[str] = []
    for account in accounts:
        item = str(account or "").strip()
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def _tier_accounts(
    database_url: str,
    accounts: list[str],
    loader,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[str]:
    account_set = set(accounts)
    ordered: list[str] = []

    def add(value: object) -> None:
        account = str(value or "").strip()
        if account and account in account_set and account not in ordered:
            ordered.append(account)

    market_kwargs = _market_call_kwargs(market_id, chain_id)
    try:
        rows = loader(database_url, limit=len(accounts), **market_kwargs)
    except TypeError:
        if market_id is not None or chain_id is not None:
            raise
        rows = loader(database_url, limit=len(accounts))
    for row in rows:
        add(row.get("account"))
    return ordered


def select_liquidation_scan_accounts(
    database_url: str,
    accounts: list[str],
    config: LiquidationScanConfig,
    *,
    force: bool = False,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    source_accounts = _unique_accounts(accounts)
    if not source_accounts:
        return {
            "selected_accounts": [],
            "strategy": "empty_account_pool",
            "included_tiers": [],
            "account_count": 0,
            "selected_account_count": 0,
            "core_account_count": 0,
            "high_frequency_account_count": 0,
            "core_due": False,
            "high_frequency_due": False,
            "borrow_health_due": False,
        }

    try:
        core_accounts = _tier_accounts(
            database_url,
            source_accounts,
            db_load_liquidation_core_opportunity_pool,
            market_id=market_id,
            chain_id=chain_id,
        )
        high_accounts = _tier_accounts(
            database_url,
            source_accounts,
            db_load_liquidation_high_frequency_pool,
            market_id=market_id,
            chain_id=chain_id,
        )
    except Exception:
        core_accounts = []
        high_accounts = []

    now = time.monotonic()
    last_core = float(LIQUIDATION_SCAN_CACHE.get("last_core_scan_monotonic") or 0.0)
    last_high = float(LIQUIDATION_SCAN_CACHE.get("last_high_frequency_scan_monotonic") or 0.0)
    last_borrow = float(LIQUIDATION_SCAN_CACHE.get("last_borrow_health_scan_monotonic") or 0.0)
    core_due = force or now - last_core >= max(0.1, float(config.core_opportunity_refresh_seconds))
    high_due = force or now - last_high >= max(0.1, float(config.high_frequency_refresh_seconds))
    borrow_due = force or now - last_borrow >= max(0.1, float(config.borrow_health_refresh_seconds))

    included_tiers: list[str]
    strategy: str
    selected: list[str]
    if borrow_due:
        included_tiers = ["core_opportunity", "high_frequency", "borrow_health"]
        strategy = "borrow_health_full"
        selected = source_accounts
    elif high_due:
        included_tiers = ["core_opportunity", "high_frequency"]
        strategy = "high_frequency_refresh"
        selected = _unique_accounts(core_accounts + high_accounts)
    elif core_due:
        included_tiers = ["core_opportunity"]
        strategy = "core_opportunity_refresh"
        selected = list(core_accounts)
    else:
        included_tiers = []
        strategy = "cooldown"
        selected = []

    if not selected:
        if high_accounts:
            included_tiers = ["high_frequency"]
            strategy = "fallback_high_frequency"
            selected = list(high_accounts)
        else:
            included_tiers = ["borrow_health"]
            strategy = "fallback_borrow_health"
            selected = list(source_accounts)

    return {
        "selected_accounts": selected,
        "strategy": strategy,
        "included_tiers": included_tiers,
        "account_count": len(source_accounts),
        "selected_account_count": len(selected),
        "core_account_count": len(core_accounts),
        "high_frequency_account_count": len(high_accounts),
        "core_due": core_due,
        "high_frequency_due": high_due,
        "borrow_health_due": borrow_due,
    }


def _mark_liquidation_scan_selection_finished(selection: dict) -> None:
    now = time.monotonic()
    tiers = set(selection.get("included_tiers") or [])
    if "core_opportunity" in tiers:
        LIQUIDATION_SCAN_CACHE["last_core_scan_monotonic"] = now
    if "high_frequency" in tiers:
        LIQUIDATION_SCAN_CACHE["last_high_frequency_scan_monotonic"] = now
    if "borrow_health" in tiers:
        LIQUIDATION_SCAN_CACHE["last_borrow_health_scan_monotonic"] = now


def prioritized_liquidation_accounts(database_url: str, accounts: list[str]) -> list[str]:
    selection = select_liquidation_scan_accounts(
        database_url,
        accounts,
        liquidation_scan_config(),
        force=True,
    )
    return list(selection.get("selected_accounts") or _unique_accounts(accounts))


def latest_liquidation_borrow_pool_batch(
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> Optional[dict]:
    batches = db_load_liquidation_borrow_health_scan_batches(
        database_url,
        limit=1,
        **_market_call_kwargs(market_id, chain_id),
    )
    return batches[0] if batches else None


def liquidation_borrow_pool_summary(
    rows: list[dict],
    *,
    scanned: bool = False,
    scan_payload: Optional[dict] = None,
    display_limit: Optional[int] = None,
) -> dict:
    scan_summary = dict((scan_payload or {}).get("summary") or {})
    config = liquidation_scan_config()
    return build_borrow_pool_summary(
        rows,
        config=config,
        display_limit=_page_size_value(display_limit, liquidation_borrow_pool_display_limit()),
        scan_cache=LIQUIDATION_SCAN_CACHE,
        scan_interval_seconds=liquidation_borrow_pool_scan_cooldown_seconds(config),
        account_tiers=scan_summary.get("account_tiers") or liquidation_account_tier_summary(),
        scanned=scanned,
        scan_payload=scan_payload,
    )


def liquidation_borrow_pool_scan_cooldown_seconds(config: LiquidationScanConfig | None = None) -> float:
    current = config or liquidation_scan_config()
    return max(
        0.1,
        min(
            float(liquidation_scan_interval_seconds()),
            float(current.core_opportunity_refresh_seconds),
        ),
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
            "scan_response_source": "cached_scan_suppressed",
            "suppression_reason": "scan interval not reached",
        }
    )
    current["summary"] = summary
    return current


def liquidation_borrow_pool_payload(
    page: int = 1,
    page_size: int = 20,
    *,
    risk_page: int | None = None,
    high_page: int | None = None,
    core_page: int | None = None,
    skip_schema: bool = False,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return _borrow_pool_blocked_payload(
            configured=False,
            error="DATABASE_URL is required",
            page_size=page_size,
            risk_page=_page_value(risk_page if risk_page is not None else page),
            high_page=_page_value(high_page if high_page is not None else page),
            core_page=_page_value(core_page if core_page is not None else page),
        )
    try:
        scope = _scan_market_scope(market_id, chain_id)
        market_kwargs = _market_call_kwargs(market_id, chain_id)
        page_size = _page_size_value(page_size)
        risk_page = _page_value(risk_page if risk_page is not None else page)
        high_page = _page_value(high_page if high_page is not None else page)
        core_page = _page_value(core_page if core_page is not None else page)
        if not skip_schema:
            ensure_database_schema(database_url)
        rows = db_load_liquidation_borrow_health_pool(
            database_url,
            limit=page_size,
            offset=(risk_page - 1) * page_size,
            **market_kwargs,
        )
        tiers = liquidation_pool_tier_payload(
            database_url,
            page_size,
            high_page=high_page,
            core_page=core_page,
            **market_kwargs,
        )
        latest_batch = latest_liquidation_borrow_pool_batch(database_url, **market_kwargs)
        scan_configs = db_load_liquidation_scan_config_library(database_url, limit=20, **market_kwargs)
        summary = liquidation_borrow_pool_summary(
            rows,
            display_limit=page_size,
            scan_payload={"summary": {"latest_batch": latest_batch, "pool_counts": tiers}},
        )
        summary.update(
            {
                "configured": True,
                "db_ready": True,
                "scan_blocked": False,
                "scan_blocked_reason": None,
                "next_action": None,
                "database_url_fingerprint": _database_url_fingerprint(database_url),
                "market_id": scope["market_id"],
                "chain_id": scope["chain_id"],
                "network": scope["network"],
                "protocol": scope["protocol"],
                "scan_response_source": "database_display",
            }
        )
        payload = {
            "rows": rows,
            "tiers": tiers,
            "latest_batch": latest_batch,
            "scan_configs": scan_configs,
            "summary": summary,
            "pagination": {
                "borrow_health": _pagination(risk_page, page_size, tiers["borrow_health_count"]),
                **tiers["pagination"],
            },
        }
        payload["debt_pool_decision"] = decision_from_borrow_pool_payload(payload)
        return payload
    except Exception as exc:
        return _borrow_pool_blocked_payload(
            configured=True,
            error=_scan_error_message(exc) or "database unavailable",
            page_size=page_size,
            risk_page=risk_page,
            high_page=high_page,
            core_page=core_page,
        )


def liquidation_borrow_pool_scan_payload(
    force: bool = False,
    page: int = 1,
    page_size: int = 20,
    *,
    risk_page: int | None = None,
    high_page: int | None = None,
    core_page: int | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    page_size = _page_size_value(page_size)
    risk_page = _page_value(risk_page if risk_page is not None else page)
    high_page = _page_value(high_page if high_page is not None else page)
    core_page = _page_value(core_page if core_page is not None else page)
    database_url = database_url_or_none()
    if not database_url:
        return _borrow_pool_blocked_payload(
            configured=False,
            error="DATABASE_URL is required",
            page_size=page_size,
            risk_page=risk_page,
            high_page=high_page,
            core_page=core_page,
        )
    config = liquidation_scan_config()
    scope = _scan_market_scope(market_id, chain_id)
    market_kwargs = _market_call_kwargs(market_id, chain_id)
    now = time.monotonic()
    ttl_seconds = liquidation_borrow_pool_scan_cooldown_seconds(config)
    cached_payload = LIQUIDATION_SCAN_CACHE.get("borrow_pool_payload")
    updated_at = float(LIQUIDATION_SCAN_CACHE.get("borrow_pool_updated_at") or 0.0)
    cache_age_seconds = now - updated_at
    if not force and (risk_page != 1 or high_page != 1 or core_page != 1):
        return liquidation_borrow_pool_payload(
            page_size=page_size,
            risk_page=risk_page,
            high_page=high_page,
            core_page=core_page,
            skip_schema=True,
            **market_kwargs,
        )
    cached_summary = cached_payload.get("summary") if isinstance(cached_payload, dict) else {}
    cache_database_matches = (
        isinstance(cached_summary, dict)
        and cached_summary.get("database_url_fingerprint") == _database_url_fingerprint(database_url)
        and (
            (cached_summary.get("market_id") == scope["market_id"] and cached_summary.get("chain_id") == scope["chain_id"])
            or (market_id is None and chain_id is None and "market_id" not in cached_summary and "chain_id" not in cached_summary)
        )
    )
    if not force and isinstance(cached_payload, dict) and cache_database_matches and cache_age_seconds < ttl_seconds:
        return _borrow_pool_payload_with_suppression(
            cached_payload,
            ttl_seconds,
            cache_age_seconds=cache_age_seconds,
        )
    if not LIQUIDATION_SCAN_LOCK.acquire(blocking=False):
        payload = liquidation_borrow_pool_payload(
            page_size=page_size,
            risk_page=risk_page,
            high_page=high_page,
            core_page=core_page,
            **market_kwargs,
        )
        summary = dict(payload.get("summary") or {})
        summary["scan_running"] = True
        summary["scan_started_at"] = LIQUIDATION_SCAN_CACHE.get("started_at")
        summary["stage"] = LIQUIDATION_SCAN_CACHE.get("stage") or "debt_pool"
        payload["summary"] = summary
        return payload
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
    source_account_count = 0
    scan_selection: dict = {}
    started_at = datetime.now(timezone.utc)
    try:
        ensure_database_schema(database_url)
        source_accounts = db_load_liquidation_accounts(database_url, **market_kwargs)
        source_account_count = len(_unique_accounts(source_accounts))
        scan_selection = select_liquidation_scan_accounts(
            database_url,
            source_accounts,
            config,
            force=force,
            **market_kwargs,
        )
        accounts = list(scan_selection.get("selected_accounts") or [])
        LIQUIDATION_SCAN_CACHE["last_scan_strategy"] = scan_selection
        LIQUIDATION_SCAN_CACHE["progress"] = {
            "account_count": source_account_count,
            "selected_account_count": len(accounts),
            "scanned_count": 0,
            "scan_strategy": scan_selection.get("strategy"),
        }
        if not accounts:
            error = "database liquidation account table is empty"
        for candidate in aave_rpc_urls() if accounts else []:
            try:
                LIQUIDATION_SCAN_CACHE["progress"] = {
                    "account_count": source_account_count,
                    "selected_account_count": len(accounts),
                    "scanned_count": 0,
                    "rpc_url": candidate,
                    "scan_strategy": scan_selection.get("strategy"),
                }
                rows = scan_account_health(accounts, aave_pool_address(), candidate, config)
                LIQUIDATION_SCAN_CACHE["progress"] = {
                    "account_count": source_account_count,
                    "selected_account_count": len(accounts),
                    "scanned_count": len(accounts),
                    "rpc_url": candidate,
                    "scan_strategy": scan_selection.get("strategy"),
                }
                try:
                    block_number = int(Web3(Web3.HTTPProvider(candidate, request_kwargs={"timeout": 8})).eth.block_number)
                except Exception:
                    block_number = None
                reports = [
                    {
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
                    for row in rows
                ]
                sync_result = db_sync_liquidation_borrow_health_pool(
                    database_url,
                    rows,
                    config.watch_health_factor,
                    account_reports=reports,
                    min_operator_net_profit_usd=config.min_operator_net_profit_usd,
                    **market_kwargs,
                )
                db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
                rpc_url = candidate
                error = None
                _mark_liquidation_scan_selection_finished(scan_selection)
                break
            except Exception as exc:
                error = _scan_error_message(exc)
        rows = db_load_liquidation_borrow_health_pool(
            database_url,
            limit=page_size,
            offset=(risk_page - 1) * page_size,
            **market_kwargs,
        )
        tiers = liquidation_pool_tier_payload(
            database_url,
            page_size,
            high_page=high_page,
            core_page=core_page,
            **market_kwargs,
        )
        finished_at = datetime.now(timezone.utc)
        latest_batch = db_record_liquidation_borrow_health_scan_batch(
            database_url,
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
            watch_health_factor=config.watch_health_factor,
            error=error,
            metadata={
                "tiers": tiers,
                "account_tiers": liquidation_account_tier_summary(),
                "scan_strategy": scan_selection,
                "market": scope,
            },
            **market_kwargs,
        )
        scan_payload = {
            "summary": {
                "configured": True,
                "db_ready": True,
                "scan_blocked": False,
                "scan_blocked_reason": None,
                "next_action": None,
                "database_url_fingerprint": _database_url_fingerprint(database_url),
                "market_id": scope["market_id"],
                "chain_id": scope["chain_id"],
                "network": scope["network"],
                "protocol": scope["protocol"],
                "scan_response_source": "chain_scan",
                "manual_force_scan": bool(force),
                "account_count": source_account_count,
                "scanned_count": len(accounts) if rpc_url else 0,
                "selected_account_count": len(accounts),
                "scan_strategy": scan_selection.get("strategy"),
                "scan_included_tiers": scan_selection.get("included_tiers") or [],
                "core_due": scan_selection.get("core_due"),
                "high_frequency_due": scan_selection.get("high_frequency_due"),
                "borrow_health_due": scan_selection.get("borrow_health_due"),
                "core_account_count": scan_selection.get("core_account_count"),
                "high_frequency_account_count": scan_selection.get("high_frequency_account_count"),
                "risk_count": tiers["borrow_health_count"],
                "entered_count": sync_result.get("entered_count", 0),
                "exited_count": sync_result.get("exited_count", 0),
                "rpc_url": rpc_url,
                "block_number": block_number,
                "latest_batch": latest_batch,
                "account_tiers": liquidation_account_tier_summary(),
                "pool_counts": tiers,
                "stage": "debt_pool",
                "error": error,
            }
        }
        LIQUIDATION_SCAN_CACHE["last_result"] = scan_payload["summary"]
        scan_configs = db_load_liquidation_scan_config_library(database_url, limit=20, **market_kwargs)
        payload = {
            "rows": rows,
            "tiers": tiers,
            "latest_batch": latest_batch,
            "scan_configs": scan_configs,
            "summary": liquidation_borrow_pool_summary(
                rows,
                scanned=True,
                display_limit=page_size,
                scan_payload=scan_payload,
            ),
            "pagination": {
                "borrow_health": _pagination(risk_page, page_size, tiers["borrow_health_count"]),
                **tiers["pagination"],
            },
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
                account_count=source_account_count or len(accounts),
                scanned_count=0,
                risk_count=0,
                error_count=1,
                rpc_url=rpc_url,
                block_number=block_number,
                watch_health_factor=config.watch_health_factor,
                error=safe_error,
                metadata={"account_tiers": liquidation_account_tier_summary(), "scan_strategy": scan_selection},
            )
        except Exception:
            latest_batch = None
        return _borrow_pool_blocked_payload(
            configured=True,
            error=safe_error or "borrow pool scan failed",
            page_size=page_size,
            risk_page=risk_page,
            high_page=high_page,
            core_page=core_page,
            latest_batch=latest_batch,
        )
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
                    rows = scan_account_health(accounts, aave_pool_address(), candidate, config)
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
        aave_pool_address(),
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
        "pool_address": aave_pool_address(),
        "protocol_data_provider_address": protocol_data_provider_address() or None,
        "liquidation_data_provider_address": liquidation_data_provider_address() or None,
        "reserve_asset_count": len(reserve_assets),
        "error": asset_error,
    }
    return report


def liquidation_account_cached_payload(
    account: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    checksum = Web3.to_checksum_address(str(account or "").strip())
    database_url = database_url_or_none()
    if not database_url:
        return {"found": False, "cached": True, "account": checksum, "error": "DATABASE_URL is required"}
    return db_load_liquidation_account_pool_snapshot(database_url, checksum, market_id=market_id, chain_id=chain_id)
