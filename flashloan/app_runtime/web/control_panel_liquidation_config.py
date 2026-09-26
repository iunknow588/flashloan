from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

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
from core.config_schema import (
    liquidation_config_health as build_liquidation_config_health,
    parse_env_float,
    parse_env_int,
)
from core.env_loader import load_env_files
from core.market_config import (
    liquidation_market_config,
    liquidation_market_config_for,
    supported_market_summaries,
)
from core.market_scan_policy import market_scan_refresh_profile as resolve_market_scan_refresh_profile
from bot.execution.liquidation_scan import LiquidationScanConfig
from bot.liquidation.pause_guard import pause_guard_controls
from core.db.storage import (
    configured_database_url as storage_configured_database_url,
    database_url_or_none as storage_database_url_or_none,
)

load_env_files(__file__, override=False)

logger = logging.getLogger(__name__)

DEFAULT_AAVE_RPC_CANDIDATES = [
    "https://api.avax.network/ext/bc/C/rpc",
    "https://rpc.ankr.com/avalanche",
    "https://avalanche-c-chain-rpc.publicnode.com",
]

DEFAULT_LIQUIDATION_CONFIG = {
    "LIQUIDATION_RETENTION_DAYS": 365,
    "LIQUIDATION_SCAN_INTERVAL_SECONDS": 300,
    "LIQUIDATION_DISCOVERY_INTERVAL_SECONDS": 3600,
    "LIQUIDATION_BORROW_HEALTH_REFRESH_SECONDS": 1800,
    "LIQUIDATION_HIGH_FREQUENCY_REFRESH_SECONDS": 300,
    "LIQUIDATION_CORE_OPPORTUNITY_REFRESH_SECONDS": 1,
    "LIQUIDATION_SCAN_MODE": "timer_driven",
}

DEFAULT_LIQUIDATION_SCAN_VERSION = "2026-08-03.1"


def normalize_liquidation_scan_mode(value: object | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"price", "price_driven", "price_mode", "binance_price", "market_price", "event_driven"}:
        return "price_driven"
    return "timer_driven"


def liquidation_scan_mode() -> str:
    raw = os.getenv("LIQUIDATION_SCAN_MODE")
    if raw is not None:
        return normalize_liquidation_scan_mode(raw)
    config = liquidation_runtime_config()
    return normalize_liquidation_scan_mode(config.get("LIQUIDATION_SCAN_MODE"))


def liquidation_runtime_config() -> dict[str, float | str]:
    config = dict(DEFAULT_LIQUIDATION_CONFIG)
    raw = read_liquidation_runtime_config_parameters()
    if isinstance(raw, dict):
        config.update({key: raw[key] for key in config if key in raw})
    for key in config:
        if os.getenv(key) is not None:
            if key == "LIQUIDATION_SCAN_MODE":
                config[key] = normalize_liquidation_scan_mode(os.getenv(key))
                continue
            value, error = parse_env_float(key, config[key])
            if not error:
                config[key] = value
    return config


def write_liquidation_runtime_config(values: dict) -> dict[str, float | str]:
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
    if "scan_mode" in values:
        current["LIQUIDATION_SCAN_MODE"] = normalize_liquidation_scan_mode(values.get("scan_mode"))
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
    if database_url:
        try:
            stored = load_page_parameter_map(database_url, LIQUIDATION_CONFIG_PAGE)
            if stored:
                try:
                    sync_page_parameter_file(LIQUIDATION_CONFIG_PAGE, stored)
                except Exception:
                    logger.warning("failed to sync liquidation config file from database", exc_info=True)
                    pass
                return stored
            if file_values:
                save_page_parameter_map(database_url, LIQUIDATION_CONFIG_PAGE, file_values)
                try:
                    sync_page_parameter_file(LIQUIDATION_CONFIG_PAGE, file_values)
                except Exception:
                    logger.warning("failed to sync liquidation config file after database migration", exc_info=True)
                    pass
                return file_values
        except Exception:
            logger.warning("failed to load liquidation config from local store, using file values", exc_info=True)
    return file_values


def save_liquidation_runtime_config_parameters(values: dict) -> bool:
    database_url = database_url_or_none()
    if not database_url:
        return False
    try:
        save_page_parameter_map(database_url, LIQUIDATION_CONFIG_PAGE, values)
        try:
            sync_page_parameter_file(LIQUIDATION_CONFIG_PAGE, values)
        except Exception:
            logger.warning("failed to sync liquidation config file after database save", exc_info=True)
            pass
        return True
    except Exception:
        return False


def liquidation_scan_version() -> str:
    return os.getenv("LIQUIDATION_SCAN_VERSION", DEFAULT_LIQUIDATION_SCAN_VERSION).strip() or DEFAULT_LIQUIDATION_SCAN_VERSION


def liquidation_scan_refresh_profile() -> dict[str, float]:
    market = liquidation_market_config()
    profile = resolve_market_scan_refresh_profile(
        market.market_id,
        feature=market.feature,
        database_url=database_url_or_none(),
    )
    configured = read_liquidation_runtime_config_parameters()

    def configured_interval(key: str, fallback: float, minimum: float) -> float:
        raw = os.getenv(key)
        if raw is not None:
            value, error = parse_env_float(key, fallback)
            if not error:
                return max(minimum, float(value))
        if key in configured:
            try:
                return max(minimum, float(configured[key]))
            except (TypeError, ValueError):
                pass
        return max(minimum, float(fallback))

    return {
        "borrow_health_refresh_seconds": configured_interval(
            "LIQUIDATION_BORROW_HEALTH_REFRESH_SECONDS",
            profile["borrow_health_refresh_seconds"],
            30.0,
        ),
        "high_frequency_refresh_seconds": configured_interval(
            "LIQUIDATION_HIGH_FREQUENCY_REFRESH_SECONDS",
            profile["high_frequency_refresh_seconds"],
            30.0,
        ),
        "core_opportunity_refresh_seconds": configured_interval(
            "LIQUIDATION_CORE_OPPORTUNITY_REFRESH_SECONDS",
            profile["core_opportunity_refresh_seconds"],
            1.0,
        ),
    }


def configured_database_url() -> str:
    return storage_configured_database_url()


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


def liquidation_market_payload(market_id: str | None = None, chain_id: int | None = None) -> dict:
    market = (
        liquidation_market_config_for(market_id=market_id, chain_id=chain_id)
        if market_id is not None or chain_id is not None
        else liquidation_market_config()
    )
    database_url = database_url_or_none()
    active = market.as_dict()
    return {
        "market_id": active.get("market_id"),
        "chain_id": active.get("chain_id"),
        "active": active,
        "supported_markets": supported_market_summaries(database_url or None),
    }


def aave_pool_address() -> str:
    return (
        os.getenv("LIQUIDATION_POOL_ADDRESS", os.getenv("AAVE_POOL_ADDRESS", "")).strip()
        or liquidation_market_config().pool_address
    )


def liquidation_scan_config() -> LiquidationScanConfig:
    runtime_config = liquidation_runtime_config()
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
        scan_mode=normalize_liquidation_scan_mode(runtime_config.get("LIQUIDATION_SCAN_MODE")),
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
        "execution_enabled": bool(config_health.get("execution_enabled")),
        "require_static_call": env_bool("LIQUIDATION_REQUIRE_STATIC_CALL", True),
        "require_fork_simulation": env_bool("LIQUIDATION_REQUIRE_FORK_SIMULATION", True),
        "self_funded_ready": False,
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


def liquidation_config_health(market_id: str | None = None, chain_id: int | None = None) -> dict:
    return build_liquidation_config_health(market_id=market_id, chain_id=chain_id)


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
    return storage_database_url_or_none()
