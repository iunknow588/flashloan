import os
from pathlib import Path

from core.config_schema import parse_env_float
from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from web.parameter_config import (
    LEGACY_STRATEGY_CONFIG_PATHS,
    STRATEGY_CONFIG_PATH,
    STRATEGY_CONFIG_PAGE,
    read_json_parameter,
    write_json_parameter,
    load_page_parameter_map,
    save_page_parameter_map,
    sync_page_parameter_file,
)
from strategy.limits import strategy_defaults
from strategy.movement_thresholds import enforce_min_paper_profit_usd


STRATEGY_DEFAULTS = strategy_defaults()

MIN_SAMPLING_SECONDS = 0.2


def _database_url_or_none() -> str | None:
    return os.getenv("DATABASE_URL", "").strip() or None


def unified_sampling_profile(config: dict) -> dict:
    seconds = max(MIN_SAMPLING_SECONDS, float(config.get("BINANCE_CHANGE_WINDOW_SECONDS", STRATEGY_DEFAULTS["BINANCE_CHANGE_WINDOW_SECONDS"])))
    return {
        "name": "统一采样周期",
        "seconds": seconds,
        "binance_change_window_seconds": seconds,
        "sample_seconds": seconds,
        "observation_write_seconds": seconds,
        "aave_poll_seconds": seconds,
        "min_change_percent": max(0.0, float(config.get("BINANCE_VELOCITY_MIN_CHANGE_PERCENT", STRATEGY_DEFAULTS["BINANCE_VELOCITY_MIN_CHANGE_PERCENT"]))),
        "applies_to": ["Binance速度窗", "Aave轮询", "Aave写库"],
    }


def strategy_config(config_path: Path) -> dict:
    config = dict(STRATEGY_DEFAULTS)
    saved = read_strategy_config_parameters(config_path)
    for key, default in STRATEGY_DEFAULTS.items():
        if key in saved:
            try:
                value = float(saved[key])
                config[key] = int(value) if isinstance(default, int) else value
            except (TypeError, ValueError):
                pass
        elif os.getenv(key):
            value, error = parse_env_float(key, default)
            if not error:
                config[key] = int(value) if isinstance(default, int) else value
    return sanitize_strategy_config(config)


def write_strategy_config(config_path: Path, payload: dict) -> dict:
    current = strategy_config(config_path)
    for key in STRATEGY_DEFAULTS:
        if key in payload:
            current[key] = payload[key]
    sanitized = sanitize_strategy_config(current)
    if not save_strategy_config_parameters(sanitized):
        write_json_parameter(config_path, sanitized)
    return sanitized


def read_strategy_config_parameters(config_path: Path) -> dict:
    database_url = _database_url_or_none()
    legacy_paths = LEGACY_STRATEGY_CONFIG_PATHS if config_path == STRATEGY_CONFIG_PATH else ()
    file_values = read_json_parameter(config_path, legacy_paths=legacy_paths) or {}
    if database_url and not database_unavailable_reason(database_url):
        try:
            stored = load_page_parameter_map(database_url, STRATEGY_CONFIG_PAGE)
            if stored:
                try:
                    sync_page_parameter_file(STRATEGY_CONFIG_PAGE, stored)
                except Exception:
                    pass
                return stored
            if file_values:
                save_page_parameter_map(database_url, STRATEGY_CONFIG_PAGE, file_values)
                try:
                    sync_page_parameter_file(STRATEGY_CONFIG_PAGE, file_values)
                except Exception:
                    pass
                return file_values
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
    return file_values


def save_strategy_config_parameters(values: dict) -> bool:
    database_url = _database_url_or_none()
    if not database_url or database_unavailable_reason(database_url):
        return False
    try:
        save_page_parameter_map(database_url, STRATEGY_CONFIG_PAGE, values)
        try:
            sync_page_parameter_file(STRATEGY_CONFIG_PAGE, values)
        except Exception:
            pass
        return True
    except Exception as exc:
        if is_database_unavailable_error(exc):
            mark_database_unavailable(database_url, exc)
        return False


def sanitize_strategy_config(values: dict) -> dict:
    config = dict(STRATEGY_DEFAULTS)
    for key, default in STRATEGY_DEFAULTS.items():
        try:
            value = float(values.get(key, default))
        except (TypeError, ValueError):
            value = float(default)
        config[key] = int(value) if isinstance(default, int) else max(0.0, value)
    config["ARBITRAGE_BASKET_SIZE"] = max(1, min(int(config["ARBITRAGE_BASKET_SIZE"]), 10))
    config["ARBITRAGE_MIN_PAPER_PROFIT_USD"] = enforce_min_paper_profit_usd(config["ARBITRAGE_MIN_PAPER_PROFIT_USD"])
    config["ARBITRAGE_ROUTE_TRADE_FEE_HOPS"] = max(1, int(config["ARBITRAGE_ROUTE_TRADE_FEE_HOPS"]))
    config["EXECUTION_SLIPPAGE_BPS"] = max(0, min(int(config["EXECUTION_SLIPPAGE_BPS"]), 5000))
    config["EXECUTION_PLAN_MAX_AGE_SECONDS"] = max(1, int(config["EXECUTION_PLAN_MAX_AGE_SECONDS"]))
    config["BINANCE_CHANGE_WINDOW_SECONDS"] = max(MIN_SAMPLING_SECONDS, float(config["BINANCE_CHANGE_WINDOW_SECONDS"]))
    config["BINANCE_VELOCITY_MIN_CHANGE_PERCENT"] = max(0.0, float(config["BINANCE_VELOCITY_MIN_CHANGE_PERCENT"]))
    return config


def read_json(path: Path) -> dict | None:
    return read_json_parameter(path)
