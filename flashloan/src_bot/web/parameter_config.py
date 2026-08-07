from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.env_loader import resolve_env_path
from db.storage_control_parameters import load_control_panel_parameter_map, save_control_panel_parameter_map

SRC_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SRC_ROOT
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
PARAMETER_DIR = RUNTIME_DIR / "parameters"
LEGACY_CONFIG_DIR = RUNTIME_DIR / "config"
LEGACY_CACHE_DIR = RUNTIME_DIR / "cache"


@dataclass(frozen=True)
class PageParameterConfig:
    page_key: str
    namespace: str
    path: Path
    legacy_paths: tuple[Path, ...] = ()


PAGE_PARAMETER_CONFIGS: dict[str, PageParameterConfig] = {
    "config": PageParameterConfig(
        page_key="config",
        namespace="strategy_config",
        path=PARAMETER_DIR / "config.json",
        legacy_paths=(
            PARAMETER_DIR / "strategy_config.json",
            LEGACY_CONFIG_DIR / "strategy_config.json",
        ),
    ),
    "liquidation": PageParameterConfig(
        page_key="liquidation",
        namespace="liquidation_runtime_config",
        path=PARAMETER_DIR / "liquidation.json",
        legacy_paths=(
            PARAMETER_DIR / "liquidation_runtime_config.json",
            LEGACY_CONFIG_DIR / "liquidation_config.json",
        ),
    ),
    "dex_arbitrage": PageParameterConfig(
        page_key="dex_arbitrage",
        namespace="cow_submission_pause_guard",
        path=PARAMETER_DIR / "dex_arbitrage.json",
        legacy_paths=(
            PARAMETER_DIR / "cow_submission_pause_guard.json",
            LEGACY_CACHE_DIR / "cow_submission_pause_guard.json",
        ),
    ),
    "execution": PageParameterConfig(
        page_key="execution",
        namespace="liquidation_pause_guard",
        path=PARAMETER_DIR / "execution.json",
        legacy_paths=(
            PARAMETER_DIR / "liquidation_pause_guard.json",
            LEGACY_CACHE_DIR / "liquidation_pause_guard.json",
        ),
    ),
}

STRATEGY_CONFIG_PAGE = "config"
LIQUIDATION_CONFIG_PAGE = "liquidation"
COW_SUBMISSION_PAGE = "dex_arbitrage"
LIQUIDATION_PAUSE_GUARD_PAGE = "execution"
PAGE_STATE_NAMESPACE = "page_state"

STRATEGY_CONFIG_PATH = PAGE_PARAMETER_CONFIGS[STRATEGY_CONFIG_PAGE].path
LIQUIDATION_CONFIG_PATH = PAGE_PARAMETER_CONFIGS[LIQUIDATION_CONFIG_PAGE].path
COW_SUBMISSION_PAUSE_GUARD_PATH = PAGE_PARAMETER_CONFIGS[COW_SUBMISSION_PAGE].path
LIQUIDATION_PAUSE_GUARD_PATH = PAGE_PARAMETER_CONFIGS[LIQUIDATION_PAUSE_GUARD_PAGE].path

LEGACY_STRATEGY_CONFIG_PATHS = PAGE_PARAMETER_CONFIGS[STRATEGY_CONFIG_PAGE].legacy_paths
LEGACY_LIQUIDATION_CONFIG_PATHS = PAGE_PARAMETER_CONFIGS[LIQUIDATION_CONFIG_PAGE].legacy_paths
LEGACY_COW_SUBMISSION_PAUSE_GUARD_PATHS = PAGE_PARAMETER_CONFIGS[COW_SUBMISSION_PAGE].legacy_paths
LEGACY_LIQUIDATION_PAUSE_GUARD_PATHS = PAGE_PARAMETER_CONFIGS[LIQUIDATION_PAUSE_GUARD_PAGE].legacy_paths


def page_parameter_config(page_key: str) -> PageParameterConfig:
    try:
        return PAGE_PARAMETER_CONFIGS[str(page_key)]
    except KeyError as exc:
        raise KeyError(f"unknown page parameter config: {page_key}") from exc


def ensure_parameter_dir(path: Path | None = None) -> Path:
    directory = path or PARAMETER_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


ensure_parameter_dir(PARAMETER_DIR)


def read_json_parameter(path: Path, *, legacy_paths: tuple[Path, ...] = ()) -> dict | None:
    for index, candidate in enumerate((path, *legacy_paths)):
        try:
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if index > 0 and not path.exists():
                    write_json_parameter(path, data)
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return None


def write_json_parameter(path: Path, payload: dict[str, Any]) -> None:
    ensure_parameter_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def read_page_parameter_file(page_key: str) -> dict | None:
    config = page_parameter_config(page_key)
    return read_json_parameter(config.path, legacy_paths=config.legacy_paths)


def write_page_parameter_file(page_key: str, payload: dict[str, Any]) -> None:
    write_json_parameter(page_parameter_config(page_key).path, payload)


def load_page_parameter_map(database_url: str, page_key: str) -> dict[str, Any]:
    return load_control_panel_parameter_map(database_url, page_parameter_config(page_key).namespace)


def save_page_parameter_map(database_url: str, page_key: str, values: dict[str, Any]) -> dict[str, Any]:
    return save_control_panel_parameter_map(database_url, page_parameter_config(page_key).namespace, values)


def sync_page_parameter_file(page_key: str, payload: dict[str, Any]) -> None:
    write_json_parameter(page_parameter_config(page_key).path, payload)


def load_page_state_parameter_map(database_url: str) -> dict[str, Any]:
    return load_control_panel_parameter_map(database_url, PAGE_STATE_NAMESPACE, connect_timeout=4)


def save_page_state_parameter_map(database_url: str, values: dict[str, Any]) -> dict[str, Any]:
    return save_control_panel_parameter_map(database_url, PAGE_STATE_NAMESPACE, values, connect_timeout=4)
