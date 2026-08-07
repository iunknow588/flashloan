import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flask import Flask
from web3 import Web3
from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from web.parameter_config import (
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
from core.config_schema import (
    liquidation_config_health as build_liquidation_config_health,
    parse_env_float,
    parse_env_int,
)
from core.env_loader import load_env_files, resolve_env_path
from core.market_config import liquidation_market_config, supported_market_summaries
from core.sensitive_data import redact_sensitive_text
from execution.liquidation_scan import LiquidationScanConfig, load_account_addresses
from market.observer import env_urls
from web.control_panel_liquidation_pause import pause_guard_controls
from db.storage import (
    EXPECTED_SCHEMA_MIGRATION_IDS,
    ensure_database_schema,
    load_schema_migrations,
    load_liquidation_accounts as db_load_liquidation_accounts,
    liquidation_account_registry_stats as db_liquidation_account_registry_stats,
    liquidation_discovery_scan_progress as db_liquidation_discovery_scan_progress,
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    prune_liquidation_accounts as db_prune_liquidation_accounts,
    record_liquidation_discovery_scan as db_record_liquidation_discovery_scan,
    require_psycopg,
    upsert_liquidation_accounts as db_upsert_liquidation_accounts,
)

load_env_files(__file__, override=False)
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
LIQUIDATION_ACCOUNTS_PATH = resolve_env_path("LIQUIDATION_ACCOUNTS_FILE", "runtime/cache/liquidation_accounts.txt", APP_DIR)
DEFAULT_AAVE_RPC_CANDIDATES = [
    "https://api.avax.network/ext/bc/C/rpc",
    "https://rpc.ankr.com/avalanche",
    "https://avalanche-c-chain-rpc.publicnode.com",
]
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

DEFAULT_LIQUIDATION_CONFIG = {
    "LIQUIDATION_RETENTION_DAYS": 365,
    "LIQUIDATION_SCAN_INTERVAL_SECONDS": 300,
    "LIQUIDATION_DISCOVERY_INTERVAL_SECONDS": 3600,
    "LIQUIDATION_BORROW_HEALTH_REFRESH_SECONDS": 1800,
    "LIQUIDATION_HIGH_FREQUENCY_REFRESH_SECONDS": 300,
    "LIQUIDATION_CORE_OPPORTUNITY_REFRESH_SECONDS": 1,
}

DEFAULT_LIQUIDATION_SCAN_VERSION = "2026-08-03.1"


def liquidation_runtime_config() -> dict[str, float]:
    config = dict(DEFAULT_LIQUIDATION_CONFIG)
    raw = read_liquidation_runtime_config_parameters()
    if isinstance(raw, dict):
        config.update({key: raw[key] for key in config if key in raw})
    for key in config:
        if os.getenv(key) is not None:
            value, error = parse_env_float(key, config[key])
            if not error:
                config[key] = value
    return config


def write_liquidation_runtime_config(values: dict) -> dict[str, float]:
    current = liquidation_runtime_config()
    if "retention_days" in values:
        retention_days = int(values.get("retention_days") or current["LIQUIDATION_RETENTION_DAYS"])
        current["LIQUIDATION_RETENTION_DAYS"] = 30 if retention_days <= 31 else 365
    if "scan_interval_seconds" in values:
        current["LIQUIDATION_SCAN_INTERVAL_SECONDS"] = max(30.0, float(values.get("scan_interval_seconds") or 300))
    if "discovery_interval_seconds" in values:
        current["LIQUIDATION_DISCOVERY_INTERVAL_SECONDS"] = max(30.0, float(values.get("discovery_interval_seconds") or 3600))
    if "borrow_health_refresh_seconds" in values:
        current["LIQUIDATION_BORROW_HEALTH_REFRESH_SECONDS"] = max(30.0, float(values.get("borrow_health_refresh_seconds") or 1800))
    if "high_frequency_refresh_seconds" in values:
        current["LIQUIDATION_HIGH_FREQUENCY_REFRESH_SECONDS"] = max(30.0, float(values.get("high_frequency_refresh_seconds") or 300))
    if "core_opportunity_refresh_seconds" in values:
        current["LIQUIDATION_CORE_OPPORTUNITY_REFRESH_SECONDS"] = max(1.0, float(values.get("core_opportunity_refresh_seconds") or 1))
    if not save_liquidation_runtime_config_parameters(current):
        write_json_parameter(LIQUIDATION_CONFIG_PATH, current)
    for key, value in current.items():
        os.environ[str(key)] = str(value)
    return current


def read_liquidation_runtime_config_parameters() -> dict:
    database_url = database_url_or_none()
    file_values: dict[str, float] = {}
    raw = read_json_parameter(LIQUIDATION_CONFIG_PATH, legacy_paths=LEGACY_LIQUIDATION_CONFIG_PATHS) or {}
    if isinstance(raw, dict):
        file_values = {key: raw[key] for key in DEFAULT_LIQUIDATION_CONFIG if key in raw}
    if database_url and not database_unavailable_reason(database_url):
        try:
            stored = load_page_parameter_map(database_url, LIQUIDATION_CONFIG_PAGE)
            if stored:
                try:
                    sync_page_parameter_file(LIQUIDATION_CONFIG_PAGE, stored)
                except Exception:
                    pass
                return stored
            if file_values:
                save_page_parameter_map(database_url, LIQUIDATION_CONFIG_PAGE, file_values)
                try:
                    sync_page_parameter_file(LIQUIDATION_CONFIG_PAGE, file_values)
                except Exception:
                    pass
                return file_values
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
    return file_values


def save_liquidation_runtime_config_parameters(values: dict) -> bool:
    database_url = database_url_or_none()
    if not database_url or database_unavailable_reason(database_url):
        return False
    try:
        save_page_parameter_map(database_url, LIQUIDATION_CONFIG_PAGE, values)
        try:
            sync_page_parameter_file(LIQUIDATION_CONFIG_PAGE, values)
        except Exception:
            pass
        return True
    except Exception as exc:
        if is_database_unavailable_error(exc):
            mark_database_unavailable(database_url, exc)
        return False


def liquidation_scan_version() -> str:
    return os.getenv("LIQUIDATION_SCAN_VERSION", DEFAULT_LIQUIDATION_SCAN_VERSION).strip() or DEFAULT_LIQUIDATION_SCAN_VERSION


def liquidation_scan_refresh_profile() -> dict[str, float]:
    config = liquidation_runtime_config()
    return {
        "borrow_health_refresh_seconds": max(30.0, float(config["LIQUIDATION_BORROW_HEALTH_REFRESH_SECONDS"])),
        "high_frequency_refresh_seconds": max(30.0, float(config["LIQUIDATION_HIGH_FREQUENCY_REFRESH_SECONDS"])),
        "core_opportunity_refresh_seconds": max(1.0, float(config["LIQUIDATION_CORE_OPPORTUNITY_REFRESH_SECONDS"])),
    }

def configured_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("缺少 DATABASE_URL。请先在 .env 或系统环境变量中配置数据库连接。")
    return database_url


def aave_rpc_urls() -> list[str]:
    market = liquidation_market_config()
    if market.rpc_urls:
        return list(market.rpc_urls)
    raw_primary = os.getenv("AVALANCHE_RPC", os.getenv("AVALANCHE_RPC_URL", "")).strip()
    raw_fallbacks = os.getenv("AVALANCHE_RPCS", "").strip()
    candidates: list[str] = []
    for raw in [raw_primary, raw_fallbacks, ",".join(DEFAULT_AAVE_RPC_CANDIDATES)]:
        for part in raw.replace("\n", ",").split(","):
            candidate = part.strip().rstrip("/")
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def liquidation_market_payload() -> dict:
    return {
        "active": liquidation_market_config().as_dict(),
        "supported_markets": supported_market_summaries(),
    }


def aave_pool_address() -> str:
    return liquidation_market_config().pool_address


def liquidation_scan_config() -> LiquidationScanConfig:
    wide_scan_seconds, _ = parse_env_float("LIQUIDATION_WIDE_SCAN_SECONDS", 1800)
    near_scan_seconds, _ = parse_env_float("LIQUIDATION_NEAR_SCAN_SECONDS", 0.2)
    borrow_health_refresh_seconds, _ = parse_env_float("LIQUIDATION_BORROW_HEALTH_REFRESH_SECONDS", 1800)
    high_frequency_refresh_seconds, _ = parse_env_float("LIQUIDATION_HIGH_FREQUENCY_REFRESH_SECONDS", 300)
    core_opportunity_refresh_seconds, _ = parse_env_float("LIQUIDATION_CORE_OPPORTUNITY_REFRESH_SECONDS", 1)
    warning_health_factor, _ = parse_env_float("LIQUIDATION_WARNING_HEALTH_FACTOR", 1.05)
    liquidation_health_factor, _ = parse_env_float("LIQUIDATION_TRIGGER_HEALTH_FACTOR", 1.0)
    max_candidates, _ = parse_env_int("LIQUIDATION_MAX_CANDIDATES", 5000)
    liquidation_bonus_percent, _ = parse_env_float("LIQUIDATION_BONUS_PERCENT", 5.0)
    flashloan_fee_percent, _ = parse_env_float("LIQUIDATION_FLASHLOAN_FEE_PERCENT", 0.05)
    dex_slippage_percent, _ = parse_env_float("LIQUIDATION_DEX_SLIPPAGE_PERCENT", 0.10)
    gas_cost_usd, _ = parse_env_float("LIQUIDATION_GAS_COST_USD", 0)
    mev_buffer_usd, _ = parse_env_float("LIQUIDATION_MEV_BUFFER_USD", 0)
    retry_buffer_usd, _ = parse_env_float("LIQUIDATION_RETRY_BUFFER_USD", 0)
    min_operator_net_profit_usd, _ = parse_env_float("LIQUIDATION_MIN_OPERATOR_NET_PROFIT_USD", 1.0, minimum=0)
    watch_health_factor, _ = parse_env_float("LIQUIDATION_WATCH_HEALTH_FACTOR", 1.5)
    close_factor, _ = parse_env_float("LIQUIDATION_CLOSE_FACTOR", 0.5)
    parallel_workers, _ = parse_env_int("LIQUIDATION_SCAN_PARALLEL_WORKERS", 8)
    batch_size, _ = parse_env_int("LIQUIDATION_SCAN_BATCH_SIZE", 100)
    return LiquidationScanConfig(
        wide_scan_seconds=wide_scan_seconds,
        near_scan_seconds=near_scan_seconds,
        borrow_health_refresh_seconds=borrow_health_refresh_seconds,
        high_frequency_refresh_seconds=high_frequency_refresh_seconds,
        core_opportunity_refresh_seconds=core_opportunity_refresh_seconds,
        warning_health_factor=warning_health_factor,
        liquidation_health_factor=liquidation_health_factor,
        max_candidates=max_candidates,
        liquidation_bonus_percent=liquidation_bonus_percent,
        flashloan_fee_percent=flashloan_fee_percent,
        dex_slippage_percent=dex_slippage_percent,
        gas_cost_usd=gas_cost_usd,
        mev_buffer_usd=mev_buffer_usd,
        retry_buffer_usd=retry_buffer_usd,
        min_operator_net_profit_usd=min_operator_net_profit_usd,
        watch_health_factor=watch_health_factor,
        close_factor=close_factor,
        parallel_workers=parallel_workers,
        batch_size=batch_size,
        multicall3_address=liquidation_market_config().multicall3_address,
    )


def liquidation_scan_interval_seconds() -> float:
    config = liquidation_runtime_config()
    return max(30.0, float(config["LIQUIDATION_SCAN_INTERVAL_SECONDS"]))


def liquidation_discovery_interval_seconds() -> float:
    config = liquidation_runtime_config()
    return max(
        liquidation_scan_interval_seconds(),
        float(config["LIQUIDATION_DISCOVERY_INTERVAL_SECONDS"]),
    )


def liquidation_backfill_interval_seconds() -> float:
    backfill_seconds, _ = parse_env_float("LIQUIDATION_BACKFILL_INTERVAL_SECONDS", 3600)
    return max(
        liquidation_discovery_interval_seconds(),
        backfill_seconds,
    )


def liquidation_recent_discovery_days() -> float:
    days, _ = parse_env_float("LIQUIDATION_RECENT_DISCOVERY_DAYS", 7)
    return max(1.0, days)


def liquidation_backfill_window_days() -> float:
    days, _ = parse_env_float("LIQUIDATION_BACKFILL_WINDOW_DAYS", 7)
    return max(1.0, days)


def liquidation_account_scan_start_days() -> float:
    days, _ = parse_env_float("LIQUIDATION_ACCOUNT_SCAN_START_DAYS", 365)
    return max(1.0, days)


def liquidation_block_seconds() -> float:
    seconds, _ = parse_env_float("LIQUIDATION_BLOCK_SECONDS", 2.0)
    return max(0.1, seconds)


def liquidation_discovery_block_overlap() -> int:
    blocks, _ = parse_env_int("LIQUIDATION_DISCOVERY_BLOCK_OVERLAP", 1)
    return max(0, blocks)


def liquidation_health_display_limit() -> int:
    limit, _ = parse_env_int("LIQUIDATION_HEALTH_DISPLAY_LIMIT", 200)
    return max(1, limit)


def liquidation_borrow_pool_display_limit() -> int:
    limit, _ = parse_env_int("LIQUIDATION_BORROW_POOL_DISPLAY_LIMIT", 100)
    return max(1, min(limit, 500))


def liquidation_background_refresh_enabled() -> bool:
    return os.getenv("LIQUIDATION_BACKGROUND_REFRESH", "true").strip().lower() not in {"0", "false", "no"}


def liquidation_retention_days() -> int:
    config = liquidation_runtime_config()
    configured = max(30, int(config["LIQUIDATION_RETENTION_DAYS"]))
    if configured <= 31:
        return 30
    return 365


def protocol_data_provider_address() -> str:
    return liquidation_market_config().protocol_data_provider_address


def liquidation_data_provider_address() -> str:
    return liquidation_market_config().liquidation_data_provider_address


def liquidation_executor_address() -> str:
    return liquidation_market_config().executor_address


def liquidation_executor_owner_address() -> str:
    return liquidation_market_config().executor_owner_address


def liquidation_executor_private_key() -> str:
    return os.getenv("LIQUIDATION_EXECUTION_PRIVATE_KEY", os.getenv("DEPLOYER_PRIVATE_KEY", "")).strip()


def liquidation_self_funded_private_key() -> str:
    return os.getenv("LIQUIDATION_SELF_FUNDED_PRIVATE_KEY", liquidation_executor_private_key()).strip()


def dex_router_address() -> str:
    return liquidation_market_config().dex_router_address or os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()


def env_bool(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() not in {"0", "false", "no", "off"}


def _append_config_error(errors: list[str], reasons: list[str], error: str | None) -> None:
    if not error:
        return
    if error not in errors:
        errors.append(error)
    if "config_invalid" not in reasons:
        reasons.append("config_invalid")


def liquidation_execution_controls() -> dict:
    config_health = build_liquidation_config_health()
    config_errors = list(config_health.get("errors") or [])
    config_blocked_reasons = liquidation_config_blocked_reasons(config_health)
    max_debt_to_cover, max_debt_error = parse_env_int("LIQUIDATION_MAX_DEBT_TO_COVER", 0, minimum=0)
    min_profit_base, min_profit_error = parse_env_int("LIQUIDATION_MIN_PROFIT_BASE", 0, minimum=0)
    max_gas_cost_usd, max_gas_error = parse_env_float("LIQUIDATION_MAX_GAS_COST_USD", 0, minimum=0)
    mev_buffer_usd, mev_error = parse_env_float("LIQUIDATION_MEV_BUFFER_USD", 0, minimum=0)
    retry_buffer_usd, retry_error = parse_env_float("LIQUIDATION_RETRY_BUFFER_USD", 0, minimum=0)
    min_operator_net_profit_usd, min_operator_error = parse_env_float(
        "LIQUIDATION_MIN_OPERATOR_NET_PROFIT_USD",
        1.0,
        minimum=0,
    )
    slippage_bps, slippage_error = parse_env_int(
        "LIQUIDATION_SWAP_SLIPPAGE_BPS",
        os.getenv("EXECUTION_SLIPPAGE_BPS", "50"),
        minimum=0,
    )
    priority_fee_gwei, priority_fee_error = parse_env_float(
        "LIQUIDATION_EXECUTION_PRIORITY_FEE_GWEI",
        1.5,
        minimum=0,
    )
    tx_timeout_seconds, tx_timeout_error = parse_env_int(
        "LIQUIDATION_EXECUTION_TIMEOUT_SECONDS",
        180,
        minimum=1,
    )
    fork_simulation_timeout_seconds, fork_simulation_timeout_error = parse_env_int(
        "LIQUIDATION_FORK_SIMULATION_TIMEOUT_SECONDS",
        180,
        minimum=1,
    )
    max_payload_age_seconds, payload_age_error = parse_env_int(
        "LIQUIDATION_MAX_PAYLOAD_AGE_SECONDS",
        30,
        minimum=1,
    )
    max_quote_age_seconds, quote_age_error = parse_env_int(
        "LIQUIDATION_MAX_QUOTE_AGE_SECONDS",
        15,
        minimum=1,
    )
    min_deadline_remaining_seconds, min_deadline_error = parse_env_int(
        "LIQUIDATION_MIN_DEADLINE_REMAINING_SECONDS",
        60,
        minimum=0,
    )
    auto_pause_threshold, auto_pause_error = parse_env_int(
        "LIQUIDATION_AUTO_PAUSE_FAILURE_THRESHOLD",
        3,
        minimum=1,
    )
    for error in (
        max_debt_error,
        min_profit_error,
        max_gas_error,
        mev_error,
        retry_error,
        min_operator_error,
        slippage_error,
        priority_fee_error,
        tx_timeout_error,
        fork_simulation_timeout_error,
        payload_age_error,
        quote_age_error,
        min_deadline_error,
        auto_pause_error,
    ):
        _append_config_error(config_errors, config_blocked_reasons, error)
    pause_guard = pause_guard_controls(
        LIQUIDATION_PAUSE_GUARD_PATH,
        enabled=env_bool("LIQUIDATION_AUTO_PAUSE_ENABLED", True),
        threshold=auto_pause_threshold,
        database_url=database_url_or_none(),
    )
    return {
        "execution_enabled": env_bool("LIQUIDATION_EXECUTION_ENABLED", True),
        "require_static_call": env_bool("LIQUIDATION_REQUIRE_STATIC_CALL", True),
        "require_fork_simulation": env_bool("LIQUIDATION_REQUIRE_FORK_SIMULATION", True),
        "self_funded_ready": bool(
            liquidation_self_funded_private_key() and aave_pool_address()
        ),
        "owner_configured": bool(liquidation_executor_owner_address()),
        "private_key_configured": bool(liquidation_executor_private_key()),
        "flashloan_executor_configured": bool(
            liquidation_executor_private_key()
            and liquidation_executor_owner_address()
            and liquidation_executor_address()
        ),
        "max_debt_to_cover": max_debt_to_cover,
        "min_profit_base": min_profit_base,
        "max_gas_cost_usd": max_gas_cost_usd,
        "mev_buffer_usd": mev_buffer_usd,
        "retry_buffer_usd": retry_buffer_usd,
        "min_operator_net_profit_usd": min_operator_net_profit_usd,
        "allow_fallback_close_factor": env_bool("LIQUIDATION_ALLOW_FALLBACK_CLOSE_FACTOR", False),
        "allow_fallback_flashloan_premium": env_bool("LIQUIDATION_ALLOW_FALLBACK_FLASHLOAN_PREMIUM", False),
        "slippage_bps": slippage_bps,
        "priority_fee_gwei": priority_fee_gwei,
        "tx_timeout_seconds": tx_timeout_seconds,
        "fork_simulation_timeout_seconds": fork_simulation_timeout_seconds,
        "max_payload_age_seconds": max_payload_age_seconds,
        "max_quote_age_seconds": max_quote_age_seconds,
        "min_deadline_remaining_seconds": min_deadline_remaining_seconds,
        "config_valid": bool(config_health.get("valid")) and not config_errors,
        "config_errors": config_errors,
        "config_warnings": list(config_health.get("warnings") or []),
        "config_blocked_reasons": config_blocked_reasons,
        "chain_id": config_health.get("chain_id"),
        "expected_chain_id": config_health.get("expected_chain_id"),
        **pause_guard,
    }


def liquidation_config_health(chain_id: int | None = None) -> dict:
    return build_liquidation_config_health(chain_id=chain_id)


def liquidation_config_blocked_reasons(config_health: dict) -> list[str]:
    reasons: list[str] = []
    for check in config_health.get("checks") or []:
        if check.get("ok") or check.get("severity") != "error":
            continue
        name = str(check.get("name") or "")
        message = str(check.get("message") or "")
        if name == "LIQUIDATION_EXECUTOR_ADDRESS":
            reason = "missing_executor" if "missing" in message else "invalid_executor"
        elif name == "LIQUIDATION_EXECUTOR_OWNER_ADDRESS":
            reason = "missing_owner" if "missing" in message else "invalid_owner"
        elif name == "LIQUIDATION_EXECUTION_PRIVATE_KEY":
            reason = "private_key_mismatch"
        elif name == "CHAIN_ID":
            reason = "chain_id_mismatch"
        elif name in {"AAVE_POOL_ADDRESS", "LIQUIDATION_POOL_ADDRESS"}:
            reason = "missing_pool" if "missing" in message else "invalid_pool"
        else:
            reason = "config_invalid"
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def database_url_or_none() -> Optional[str]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    return database_url or None


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
        return {"total_count": 0, "active_count": 0, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": liquidation_retention_days()}


def schema_status_payload() -> dict:
    database_url = database_url_or_none()
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
        return {
            "latest_recent_scan_end_at": None,
            "earliest_backfill_scan_start_at": None,
            "latest_recent_to_block": None,
            "earliest_backfill_from_block": None,
            "success_count": 0,
            "error_count": 0,
            "scanned_block_count": 0,
        }


def liquidation_scan_config_library(
    limit: int = 100,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "configs": []}
    try:
        ensure_database_schema(database_url)
        kwargs = {}
        if market_id is not None:
            kwargs["market_id"] = market_id
        if chain_id is not None:
            kwargs["chain_id"] = chain_id
        return {"configured": True, "configs": db_load_liquidation_scan_config_library(database_url, limit=limit, **kwargs)}
    except Exception as exc:
        return {"configured": True, "configs": [], "error": redact_sensitive_text(exc)}


def liquidation_scan_config_payload(config_key: str) -> dict:
    configs = liquidation_scan_config_library(limit=100).get("configs") or []
    for item in configs:
        if item.get("config_key") == config_key:
            payload = item.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


def resolve_discovery_block_range(rpc_url: str, from_block: int, to_block: Optional[int]) -> tuple[int, int, int]:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    latest_block = int(w3.eth.block_number)
    start_block = max(0, latest_block + int(from_block)) if int(from_block) < 0 else max(0, int(from_block))
    if to_block is None:
        end_block = latest_block
    else:
        raw_to_block = int(to_block)
        end_block = max(0, latest_block + raw_to_block) if raw_to_block < 0 else min(latest_block, raw_to_block)
    return latest_block, start_block, end_block


def record_liquidation_discovery_window(
    *,
    mode: str,
    status: str,
    rpc_url: str,
    pool_address: str,
    from_block: int,
    to_block: int,
    scan_start_at: datetime,
    scan_end_at: datetime,
    discovered_count: int = 0,
    error: Optional[str] = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> None:
    database_url = database_url_or_none()
    if not database_url:
        return
    try:
        ensure_database_schema(database_url)
        db_record_liquidation_discovery_scan(
            database_url,
            mode=mode,
            status=status,
            rpc_url=rpc_url,
            pool_address=pool_address,
            from_block=from_block,
            to_block=to_block,
            scan_start_at=scan_start_at,
            scan_end_at=scan_end_at,
            discovered_count=discovered_count,
            error=error,
            market_id=market_id,
            chain_id=chain_id,
        )
    except Exception:
        pass


def discovery_window_continuity_error(mode: str, from_block: int, to_block: int, progress: dict) -> Optional[str]:
    if from_block > to_block:
        return None
    if mode == "recent":
        latest_to = progress.get("latest_recent_to_block")
        if latest_to is None:
            return None
        if int(from_block) <= int(latest_to) + 1:
            return None
        return f"recent scan gap: previous to_block {latest_to}, next from_block {from_block}"
    if mode == "historical-backfill":
        earliest_from = progress.get("earliest_backfill_from_block")
        if earliest_from is None:
            return None
        if int(to_block) >= int(earliest_from) - 1:
            return None
        return f"historical backfill gap: next to_block {to_block}, previous from_block {earliest_from}"
    return None


def sync_liquidation_accounts_to_database(
    accounts: list[str],
    source: str = "manual",
    scan_start_at: Optional[datetime] = None,
    scan_end_at: Optional[datetime] = None,
    update_existing: bool = True,
) -> None:
    database_url = database_url_or_none()
    if not database_url or not accounts:
        return
    ensure_database_schema(database_url)
    db_upsert_liquidation_accounts(
        database_url,
        accounts,
        source=source,
        active=True,
        scan_start_at=scan_start_at,
        scan_end_at=scan_end_at,
        update_existing=update_existing,
    )
    db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())


def parse_iso_datetime(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def liquidation_discovery_window(force_full: bool = False) -> tuple[datetime, datetime, int, Optional[int], int, dict, str]:
    now = datetime.now(timezone.utc)
    registry = liquidation_account_registry_window()
    pool_address = aave_pool_address()
    progress = liquidation_discovery_progress(pool_address)
    config_cursor = liquidation_scan_config_payload("liquidation_discovery_scans.latest_success")
    if not config_cursor:
        config_cursor = liquidation_scan_config_payload("liquidation_discovery_scans.latest")
    if progress.get("latest_recent_to_block") is None and config_cursor.get("status") == "success":
        try:
            progress["latest_recent_to_block"] = int(config_cursor.get("to_block"))
            progress["latest_recent_scan_end_at"] = config_cursor.get("scan_end_at")
        except (TypeError, ValueError):
            pass
    overlap_blocks = liquidation_discovery_block_overlap()
    latest_recent_to_block = progress.get("latest_recent_to_block")
    latest_end = parse_iso_datetime(progress.get("latest_recent_scan_end_at")) or parse_iso_datetime(registry.get("latest_scan_end_at"))
    start_anchor = now - timedelta(days=liquidation_account_scan_start_days())
    mode = "recent"

    if force_full or latest_recent_to_block is None:
        scan_start_at = start_anchor
        scan_end_at = now
        now_for_blocks = datetime.now(timezone.utc)
        lookback_blocks = max(1, int(max(0.0, (now_for_blocks - scan_start_at).total_seconds()) / liquidation_block_seconds()))
        from_block = -lookback_blocks
        to_block = None
        registry["discovery_scan_progress"] = progress
        registry["discovery_cursor"] = {
            "source": "full-year-bootstrap" if latest_recent_to_block is None else "full-year-rescan",
            "mode": mode,
            "scan_days": liquidation_account_scan_start_days(),
            "overlap_blocks": overlap_blocks,
            "next_from_block": from_block,
            "next_to_block": to_block,
        }
        return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode

    if latest_recent_to_block is not None and not force_full:
        scan_start_at = max(start_anchor, latest_end) if latest_end else now - timedelta(days=liquidation_recent_discovery_days())
        scan_end_at = now
        from_block = max(0, int(latest_recent_to_block) + 1 - overlap_blocks)
        to_block = None
        lookback_blocks = 0
        registry["discovery_scan_progress"] = progress
        registry["discovery_cursor"] = {
            "source": "block-ledger",
            "direction": "forward-from-start",
            "mode": mode,
            "overlap_blocks": overlap_blocks,
            "previous_latest_to_block": int(latest_recent_to_block),
            "next_from_block": from_block,
            "next_to_block": None,
        }
        return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode

    scan_start_at = start_anchor
    scan_end_at = now
    lookback_blocks = max(1, int(max(0.0, (now - scan_start_at).total_seconds()) / liquidation_block_seconds()))
    from_block = -lookback_blocks
    to_block = None
    registry["discovery_scan_progress"] = progress
    registry["discovery_cursor"] = {
        "source": "full-year-bootstrap",
        "mode": mode,
        "scan_days": liquidation_account_scan_start_days(),
        "overlap_blocks": overlap_blocks,
        "next_from_block": from_block,
        "next_to_block": to_block,
    }
    return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode
