import os
import logging
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flask import Flask
from web3 import Web3
from core.parameter_config import (
    LIQUIDATION_CONFIG_PAGE,
    LEGACY_LIQUIDATION_CONFIG_PATHS,
    LIQUIDATION_CONFIG_PATH,
    LIQUIDATION_PAUSE_GUARD_PATH,
    read_json_parameter,
    write_json_parameter,
    load_page_parameter_map,
    save_page_parameter_map,
    sync_page_parameter_file,
)

SRC_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SRC_ROOT
logger = logging.getLogger(__name__)
from core.config_schema import (
    liquidation_config_health as build_liquidation_config_health,
    parse_env_float,
    parse_env_int,
)
from core.env_loader import load_env_files, resolve_env_path
from core.runtime_paths import resolve_runtime_dir
from core.market_config import liquidation_market_config, liquidation_market_config_for, supported_market_summaries
from core.market_scan_policy import market_scan_refresh_profile as resolve_market_scan_refresh_profile
from core.sensitive_data import redact_sensitive_text
from bot.execution.liquidation_scan import LiquidationScanConfig, load_account_addresses
from market.observer import env_urls
from bot.liquidation.pause_guard import pause_guard_controls
from core.db.storage import (
    EXPECTED_SCHEMA_MIGRATION_IDS,
    ensure_database_schema,
    load_schema_migrations,
    load_liquidation_accounts as db_load_liquidation_accounts,
    liquidation_account_registry_stats as db_liquidation_account_registry_stats,
    liquidation_discovery_scan_progress as db_liquidation_discovery_scan_progress,
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    prune_liquidation_accounts as db_prune_liquidation_accounts,
    record_liquidation_discovery_scan as db_record_liquidation_discovery_scan,
    configured_database_url as storage_configured_database_url,
    database_url_or_none as storage_database_url_or_none,
    upsert_liquidation_accounts as db_upsert_liquidation_accounts,
)
from web.control_panel_liquidation_config import (
    DEFAULT_AAVE_RPC_CANDIDATES,
    DEFAULT_LIQUIDATION_CONFIG,
    DEFAULT_LIQUIDATION_SCAN_VERSION,
    _append_config_error,
    aave_pool_address,
    aave_rpc_urls,
    configured_database_url,
    database_url_or_none,
    dex_router_address,
    env_bool,
    liquidation_account_scan_start_days,
    liquidation_backfill_interval_seconds,
    liquidation_backfill_window_days,
    liquidation_background_refresh_enabled,
    liquidation_block_seconds,
    liquidation_borrow_pool_display_limit,
    liquidation_config_blocked_reasons,
    liquidation_config_health,
    liquidation_data_provider_address,
    liquidation_discovery_block_overlap,
    liquidation_discovery_interval_seconds,
    liquidation_execution_controls,
    liquidation_executor_address,
    liquidation_executor_owner_address,
    liquidation_executor_private_key,
    liquidation_health_display_limit,
    liquidation_market_payload,
    liquidation_recent_discovery_days,
    liquidation_retention_days,
    liquidation_runtime_config,
    liquidation_scan_config,
    liquidation_scan_interval_seconds,
    liquidation_scan_mode,
    liquidation_scan_refresh_profile,
    liquidation_scan_version,
    liquidation_self_funded_private_key,
    normalize_liquidation_scan_mode,
    protocol_data_provider_address,
    read_liquidation_runtime_config_parameters,
    save_liquidation_runtime_config_parameters,
    write_liquidation_runtime_config,
)

load_env_files(__file__, override=False)
RUNTIME_DIR = resolve_runtime_dir(base_dir=APP_DIR)
LIQUIDATION_ACCOUNTS_PATH = resolve_env_path("LIQUIDATION_ACCOUNTS_FILE", "runtime/cache/liquidation_accounts.txt", APP_DIR)
LIQUIDATION_SCAN_CACHE: dict[str, object] = {
    "updated_at": 0.0,
    "payload": None,
    "running": False,
    "started_at": None,
    "finished_at": None,
    "stage": "idle",
}
LIQUIDATION_SCAN_LOCK = threading.Lock()
LIQUIDATION_ACCOUNT_CACHE: dict[str, object] = {"updated_at": 0.0, "accounts": None, "source": None}
LIQUIDATION_DISCOVERY_CACHE: dict[str, object] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "stage": "idle",
    "last_result": None,
    "last_backfill_at": None,
    "last_backfill_monotonic": 0.0,
    "historical_cursor_at": None,
}
LIQUIDATION_DISCOVERY_LOCK = threading.Lock()
LIQUIDATION_REFRESH_THREAD: Optional[threading.Thread] = None
LIQUIDATION_REFRESH_STOP = threading.Event()
SCHEMA_ENSURE_CACHE: dict[str, object] = {"database_url": None, "checked_at": 0.0, "error": None}
SCHEMA_ENSURE_CACHE_LOCK = threading.Lock()


def ensure_database_schema_cached(database_url: str, *, ttl_seconds: float = 300.0, force: bool = False) -> None:
    if not database_url:
        return
    now = time.monotonic()
    with SCHEMA_ENSURE_CACHE_LOCK:
        cached_url = SCHEMA_ENSURE_CACHE.get("database_url")
        checked_at = float(SCHEMA_ENSURE_CACHE.get("checked_at") or 0.0)
        cached_error = SCHEMA_ENSURE_CACHE.get("error")
        if (
            not force
            and cached_url == database_url
            and cached_error is None
            and now - checked_at < max(1.0, float(ttl_seconds))
        ):
            return
    try:
        ensure_database_schema(database_url)
    except Exception as exc:
        with SCHEMA_ENSURE_CACHE_LOCK:
            SCHEMA_ENSURE_CACHE.update({"database_url": database_url, "checked_at": now, "error": redact_sensitive_text(exc)})
        raise
    with SCHEMA_ENSURE_CACHE_LOCK:
        SCHEMA_ENSURE_CACHE.update({"database_url": database_url, "checked_at": now, "error": None})


def load_liquidation_account_registry(force: bool = False) -> tuple[list[str], str]:
    now = time.monotonic()
    ttl_seconds = liquidation_scan_interval_seconds()
    cached_accounts = LIQUIDATION_ACCOUNT_CACHE.get("accounts")
    updated_at = float(LIQUIDATION_ACCOUNT_CACHE.get("updated_at") or 0.0)
    cached_source = str(LIQUIDATION_ACCOUNT_CACHE.get("source") or "")
    if not force and isinstance(cached_accounts, list) and now - updated_at < ttl_seconds:
        return list(cached_accounts), cached_source or "cache"

    accounts: list[str] = []
    source = "none"
    database_url = database_url_or_none()
    if database_url:
        try:
            ensure_database_schema(database_url)
            db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
            accounts = db_load_liquidation_accounts(database_url)
            source = "database"
        except Exception:
            logger.warning("failed to load liquidation accounts from database", exc_info=True)
            accounts = []
            source = "database-error"
    if not accounts:
        file_accounts = load_account_addresses(LIQUIDATION_ACCOUNTS_PATH)
        if file_accounts:
            accounts = file_accounts
            source = "file-fallback" if source != "none" else "file"
    LIQUIDATION_ACCOUNT_CACHE["updated_at"] = now
    LIQUIDATION_ACCOUNT_CACHE["accounts"] = list(accounts)
    LIQUIDATION_ACCOUNT_CACHE["source"] = source
    return list(accounts), source


def liquidation_account_registry_window(market_id: str | None = None, chain_id: int | None = None) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"total_count": 0, "active_count": 0, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": liquidation_retention_days()}
    try:
        ensure_database_schema(database_url)
        try:
            return db_liquidation_account_registry_stats(
                database_url,
                retained_days=liquidation_retention_days(),
                market_id=market_id,
                chain_id=chain_id,
            )
        except TypeError:
            if market_id is not None or chain_id is not None:
                raise
            return db_liquidation_account_registry_stats(
                database_url,
                retained_days=liquidation_retention_days(),
            )
    except Exception:
        logger.warning("failed to load liquidation account registry window", exc_info=True)
        return {"total_count": 0, "active_count": 0, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": liquidation_retention_days()}


def schema_status_payload() -> dict:
    # Resolve from storage at the boundary so facade context injection cannot
    # leak a stale request/test resolver into schema inspection.
    database_url = storage_database_url_or_none()
    if not database_url:
        return {"configured": False, "up_to_date": False, "expected_migrations": list(EXPECTED_SCHEMA_MIGRATION_IDS), "applied_migrations": []}
    try:
        ensure_database_schema(database_url)
        migrations = load_schema_migrations(database_url)
        applied_ids = {str(row.get("migration_id")) for row in migrations}
        missing = [migration_id for migration_id in EXPECTED_SCHEMA_MIGRATION_IDS if migration_id not in applied_ids]
        return {
            "configured": True,
            "up_to_date": not missing,
            "expected_migrations": list(EXPECTED_SCHEMA_MIGRATION_IDS),
            "applied_migrations": migrations,
            "missing_migrations": missing,
        }
    except Exception as exc:
        return {"configured": True, "up_to_date": False, "error": redact_sensitive_text(exc), "expected_migrations": list(EXPECTED_SCHEMA_MIGRATION_IDS), "applied_migrations": []}


def liquidation_discovery_progress(
    pool_address: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    database_url = database_url_or_none()
    if not database_url or not pool_address:
        return {
            "latest_recent_scan_end_at": None,
            "earliest_backfill_scan_start_at": None,
            "latest_recent_to_block": None,
            "earliest_backfill_from_block": None,
            "success_count": 0,
            "error_count": 0,
            "scanned_block_count": 0,
        }
    try:
        ensure_database_schema(database_url)
        return db_liquidation_discovery_scan_progress(
            database_url,
            pool_address,
            market_id=market_id,
            chain_id=chain_id,
        )
    except Exception:
        logger.warning("failed to load liquidation discovery progress", exc_info=True)
        return {
            "latest_recent_scan_end_at": None,
            "earliest_backfill_scan_start_at": None,
            "latest_recent_to_block": None,
            "earliest_backfill_from_block": None,
            "success_count": 0,
            "error_count": 0,
            "scanned_block_count": 0,
        }


from web.control_panel_liquidation_discovery import (
    discovery_window_continuity_error,
    liquidation_discovery_window,
    liquidation_scan_config_library,
    liquidation_scan_config_payload,
    parse_iso_datetime,
    record_liquidation_discovery_window,
    resolve_discovery_block_range,
    sync_liquidation_accounts_to_database,
)
