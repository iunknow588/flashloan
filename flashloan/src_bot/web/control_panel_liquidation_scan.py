import threading
import time
import sys
from typing import Optional

from web3 import Web3

from core.market_config import liquidation_market_config, liquidation_market_config_for
from core.sensitive_data import redact_sensitive_text
from execution.liquidation_scan import (
    build_user_liquidation_report,
    discover_borrower_addresses,
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
import web.control_panel_liquidation_borrow_pool as borrow_pool_panel
import web.control_panel_liquidation_borrow_pool_payload as borrow_pool_payload_panel
import web.control_panel_liquidation_scan_support as scan_support_panel
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
    build_borrow_pool_scan_summary,
    build_health_summary,
    display_health_rows,
)
from liquidation.scan_selection import (
    mark_selection_finished as _mark_scan_selection_finished,
    select_accounts as _select_scan_accounts,
    tier_accounts as _select_tier_accounts,
    unique_accounts as _select_unique_accounts,
)
from liquidation.scan_orchestrator import build_account_health_reports, run_borrow_health_scan
from liquidation.health_scan_workflow import (
    LiquidationHealthRuntime,
    liquidation_health_payload as build_liquidation_health_payload,
)
from liquidation.account_payload_service import (
    LiquidationAccountPayloadRuntime,
    liquidation_account_cached_payload as build_liquidation_account_cached_payload,
    liquidation_account_payload as build_liquidation_account_payload,
)
from liquidation.scan_payload_service import (
    attach_core_execution_summaries as build_core_execution_summaries,
    build_borrow_pool_blocked_payload,
    build_borrow_pool_display_payload,
    build_pool_tier_payload,
    empty_tier_payload,
    load_core_rows_with_execution,
    page_size_value,
    page_value,
    pagination,
)
from liquidation.borrow_pool_scan_service import (
    BorrowPoolScanRuntime,
    run_borrow_pool_scan_payload,
)
from liquidation.account_backfill_workflow import (
    AccountBackfillRuntime,
    run_account_backfill_once as run_account_backfill_workflow,
)
from liquidation.refresh_service import (
    LiquidationRefreshRuntime,
    start_liquidation_refresh_loop as start_liquidation_refresh_workflow,
)
from web.control_panel_bnb_venus import (
    BNB_CHAIN_ID,
    BNB_MARKET_ID,
    bnb_venus_account_payload,
    bnb_venus_borrow_pool_payload,
)

globals().update({name: value for name, value in vars(liquidation_base).items() if not name.startswith("_")})
_PANEL = sys.modules[__name__]

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
    return scan_support_panel.run_account_backfill_once(_PANEL)


def start_account_backfill_background() -> dict:
    return LIQUIDATION_ACCOUNT_BACKFILL_SERVICE.start_background(run_account_backfill_once)


def discover_and_sync_liquidation_accounts(force_full: bool = False) -> dict:
    return run_liquidation_discovery_workflow(_PANEL, force_full=force_full)


def normalize_liquidation_account_values(values: object) -> list[str]:
    return scan_support_panel.normalize_account_values(_PANEL, values)


def start_liquidation_refresh_loop() -> None:
    scan_support_panel.start_refresh_loop(_PANEL)


def _set_liquidation_refresh_thread(thread) -> None:
    global LIQUIDATION_REFRESH_THREAD
    LIQUIDATION_REFRESH_THREAD = thread


def initialize_liquidation_runtime() -> None:
    scan_support_panel.initialize_runtime(_PANEL)


def scan_context_assets() -> tuple[Optional[str], list[dict], Optional[str]]:
    return scan_support_panel.scan_context_assets(_PANEL)


def liquidation_health_summary(
    rows: list[dict],
    account_count: int,
    account_source: str,
    config: LiquidationScanConfig,
    rpc_url: Optional[str],
    error: Optional[str],
) -> dict:
    return scan_support_panel.health_summary(
        _PANEL,
        rows,
        account_count,
        account_source,
        config,
        rpc_url,
        error,
    )


def liquidation_health_with_scan_state(
    payload: dict,
    ttl_seconds: float,
    *,
    running: bool,
    cache_age_seconds: Optional[float] = None,
    cooldown_remaining_seconds: Optional[float] = None,
) -> dict:
    return scan_support_panel.health_with_scan_state(
        _PANEL,
        payload,
        ttl_seconds,
        running=running,
        cache_age_seconds=cache_age_seconds,
        cooldown_remaining_seconds=cooldown_remaining_seconds,
    )


def liquidation_health_display_rows(rows: list[dict], limit: int) -> list[dict]:
    return scan_support_panel.health_display_rows(_PANEL, rows, limit)


def record_liquidation_health_scan_rows(rows: list[dict]) -> None:
    scan_support_panel.record_health_scan_rows(_PANEL, rows)


def liquidation_account_tier_summary() -> dict:
    return scan_support_panel.account_tier_summary(_PANEL)


def _page_value(value: object, default: int = 1) -> int:
    return scan_support_panel.page_value(_PANEL, value, default)


def _page_size_value(value: object, default: int = 20) -> int:
    return scan_support_panel.page_size_value(_PANEL, value, default)


def _pagination(page: int, page_size: int, total_count: int) -> dict[str, int]:
    return scan_support_panel.pagination(_PANEL, page, page_size, total_count)


def _empty_tier_payload(page_size: int = 20, *, high_page: int = 1, core_page: int = 1) -> dict:
    return scan_support_panel.empty_tier_payload(
        _PANEL,
        page_size,
        high_page=high_page,
        core_page=core_page,
    )


def _database_url_fingerprint(database_url: str | None) -> str | None:
    return borrow_pool_panel.database_url_fingerprint(database_url)


def _scan_market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, object]:
    return borrow_pool_panel.scan_market_scope(_PANEL, market_id, chain_id)


def _market_call_kwargs(market_id: str | None = None, chain_id: int | None = None) -> dict[str, object]:
    return borrow_pool_panel.market_call_kwargs(market_id, chain_id)


def _is_bnb_venus_scope(market_id: str | None = None, chain_id: int | None = None) -> bool:
    return borrow_pool_panel.is_bnb_venus_scope(_PANEL, market_id, chain_id)


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
    return build_borrow_pool_blocked_payload(
        configured=configured,
        error=error,
        decision_from_payload=decision_from_borrow_pool_payload,
        page_size=page_size,
        risk_page=risk_page,
        high_page=high_page,
        core_page=core_page,
        latest_batch=latest_batch,
    )


def _attach_core_execution_summaries(
    database_url: str,
    rows: list[dict],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict]:
    return borrow_pool_panel.attach_core_execution_summaries(
        _PANEL,
        database_url,
        rows,
        market_id=market_id,
        chain_id=chain_id,
    )


def liquidation_core_rows_with_execution(
    database_url: str,
    limit: int,
    offset: int = 0,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict]:
    return borrow_pool_panel.core_rows_with_execution(
        _PANEL,
        database_url,
        limit,
        offset,
        market_id=market_id,
        chain_id=chain_id,
    )


def liquidation_pool_tier_payload(
    database_url: str,
    limit: int,
    *,
    high_page: int = 1,
    core_page: int = 1,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    return borrow_pool_panel.pool_tier_payload(
        _PANEL,
        database_url,
        limit,
        high_page=high_page,
        core_page=core_page,
        market_id=market_id,
        chain_id=chain_id,
    )


def _unique_accounts(accounts: list[str]) -> list[str]:
    return _select_unique_accounts(accounts)


def _tier_accounts(
    database_url: str,
    accounts: list[str],
    loader,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[str]:
    return borrow_pool_panel.tier_accounts(
        _PANEL,
        database_url,
        accounts,
        loader,
        market_id=market_id,
        chain_id=chain_id,
    )


def select_liquidation_scan_accounts(
    database_url: str,
    accounts: list[str],
    config: LiquidationScanConfig,
    *,
    force: bool = False,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    return borrow_pool_panel.select_scan_accounts(
        _PANEL,
        database_url,
        accounts,
        config,
        force=force,
        market_id=market_id,
        chain_id=chain_id,
    )


def _mark_liquidation_scan_selection_finished(selection: dict) -> None:
    _mark_scan_selection_finished(selection, scan_cache=LIQUIDATION_SCAN_CACHE)


def _liquidation_scan_block_number(rpc_url: str) -> int | None:
    return borrow_pool_panel.liquidation_scan_block_number(rpc_url)


def prioritized_liquidation_accounts(database_url: str, accounts: list[str]) -> list[str]:
    return borrow_pool_panel.prioritized_accounts(_PANEL, database_url, accounts)


def latest_liquidation_borrow_pool_batch(
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> Optional[dict]:
    return borrow_pool_panel.latest_borrow_pool_batch(
        _PANEL,
        database_url,
        market_id=market_id,
        chain_id=chain_id,
    )


def liquidation_borrow_pool_summary(
    rows: list[dict],
    *,
    scanned: bool = False,
    scan_payload: Optional[dict] = None,
    display_limit: Optional[int] = None,
) -> dict:
    return borrow_pool_panel.borrow_pool_summary(
        _PANEL,
        rows,
        scanned=scanned,
        scan_payload=scan_payload,
        display_limit=display_limit,
    )


def liquidation_borrow_pool_scan_cooldown_seconds(config: LiquidationScanConfig | None = None) -> float:
    return borrow_pool_panel.borrow_pool_scan_cooldown_seconds(_PANEL, config)


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
    return borrow_pool_payload_panel.borrow_pool_payload(
        _PANEL,
        page=page,
        page_size=page_size,
        risk_page=risk_page,
        high_page=high_page,
        core_page=core_page,
        skip_schema=skip_schema,
        market_id=market_id,
        chain_id=chain_id,
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
    return borrow_pool_payload_panel.borrow_pool_scan_payload(
        _PANEL,
        force=force,
        page=page,
        page_size=page_size,
        risk_page=risk_page,
        high_page=high_page,
        core_page=core_page,
        market_id=market_id,
        chain_id=chain_id,
    )


def liquidation_health_payload(force: bool = False) -> dict:
    return scan_support_panel.health_payload(_PANEL, force=force)


def liquidation_account_payload(
    account: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    return scan_support_panel.account_payload(
        _PANEL,
        account,
        market_id=market_id,
        chain_id=chain_id,
    )


def liquidation_account_cached_payload(
    account: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    return scan_support_panel.cached_account_payload(
        _PANEL,
        account,
        market_id=market_id,
        chain_id=chain_id,
    )
