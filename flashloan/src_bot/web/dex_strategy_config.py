from __future__ import annotations

from pathlib import Path
from typing import Any

from market.binance_market.dex_strategy_registry import (
    default_dex_strategy_key,
    dex_strategy_definitions,
    dex_strategy_definition,
    dex_strategy_keys,
    resolve_dex_strategy_key,
)
from web.parameter_config import (
    DEX_STRATEGY_CONFIG_PATH,
    LEGACY_DEX_STRATEGY_CONFIG_PATHS,
    read_json_parameter,
    write_json_parameter,
)


STRATEGY_CONFIG_PATH = DEX_STRATEGY_CONFIG_PATH


def _read_strategy_payload(config_path: Path) -> dict[str, Any]:
    legacy_paths = LEGACY_DEX_STRATEGY_CONFIG_PATHS if config_path == STRATEGY_CONFIG_PATH else ()
    payload = read_json_parameter(config_path, legacy_paths=legacy_paths)
    return payload if isinstance(payload, dict) else {}


def dex_strategy_config(config_path: Path = STRATEGY_CONFIG_PATH) -> dict[str, Any]:
    payload = _read_strategy_payload(config_path)
    resolved = resolve_dex_strategy_key(
        payload.get("active_strategy")
        or payload.get("selected_strategy")
        or payload.get("strategy")
        or payload.get("dex_strategy")
    )
    return {
        "active_strategy": resolved,
        "selected_strategy": resolved,
        "strategy": resolved,
    }


def write_dex_strategy_config(payload: dict[str, Any], config_path: Path = STRATEGY_CONFIG_PATH) -> dict[str, Any]:
    current = dex_strategy_config(config_path)
    resolved = resolve_dex_strategy_key(
        payload.get("active_strategy")
        or payload.get("selected_strategy")
        or payload.get("strategy")
        or payload.get("dex_strategy")
        or current.get("active_strategy")
    )
    sanitized = {
        "active_strategy": resolved,
        "selected_strategy": resolved,
        "strategy": resolved,
    }
    write_json_parameter(config_path, sanitized)
    return sanitized


def dex_strategy_state(config_path: Path = STRATEGY_CONFIG_PATH) -> dict[str, Any]:
    config = dex_strategy_config(config_path)
    selected = config["active_strategy"]
    definition = dex_strategy_definition(selected)
    return {
        "config": config,
        "active_strategy": selected,
        "active_strategy_definition": definition,
        "default_strategy": default_dex_strategy_key(),
        "strategies": dex_strategy_definitions(),
        "strategy_keys": dex_strategy_keys(),
        "strategy_count": len(dex_strategy_keys()),
    }

