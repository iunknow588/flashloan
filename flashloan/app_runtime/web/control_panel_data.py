from pathlib import Path
from typing import Optional

from core.control_panel_data_cache import (
    aave_reserve_cache as read_aave_reserve_cache,
    borrow_target_universe as read_borrow_target_universe,
    read_json,
    stable_target_cache as read_stable_target_cache,
)
from core.control_panel_data_compact import (
    compact_extremes_payload,
    latest_arbitrage_from_db,
    latest_extremes_from_db,
)
from core.control_panel_data_db import (
    available_candidate_symbols as read_available_candidate_symbols,
    available_chart_symbols as read_available_chart_symbols,
    database_table_counts as read_database_table_counts,
    latest_candidate_price_rows as read_latest_candidate_price_rows,
    latest_observation_prices_at_or_before as read_latest_observation_prices_at_or_before,
    latest_observation_prices as read_latest_observation_prices,
    observation_count as read_observation_count,
    recent_aave_pair_prices as read_recent_aave_pair_prices,
    recent_binance_pair_prices as read_recent_binance_pair_prices,
    recent_binance_price_history as read_recent_binance_price_history,
    recent_observations as read_recent_observations,
    recent_velocity_timepoints as read_recent_velocity_timepoints,
    velocity_timepoint_snapshot as read_velocity_timepoint_snapshot,
)


def latest_binance_extremes(database_url: str | None, latest_path: Path) -> Optional[dict]:
    data = read_json(latest_path)
    if data:
        compact = compact_extremes_payload(data)
        if compact:
            return compact
    return latest_extremes_from_db(database_url) if database_url else None


def latest_binance_extremes_file(latest_path: Path) -> Optional[dict]:
    data = read_json(latest_path)
    return compact_extremes_payload(data) if data else None


def latest_arbitrage_simulation_file(latest_path: Path) -> Optional[dict]:
    return read_json(latest_path)


def latest_arbitrage_simulation(database_url: str | None, latest_path: Path) -> Optional[dict]:
    if not database_url:
        return read_json(latest_path)
    return read_json(latest_path) or latest_arbitrage_from_db(database_url)


def latest_executable_signal(latest_path: Path) -> Optional[dict]:
    data = read_json(latest_path)
    if not data:
        return None
    signal = data.get("signal")
    return {
        "raw_candidate_count": int(data.get("raw_candidate_count") or 0),
        "executable_candidate_count": int(data.get("executable_candidate_count") or 0),
        "executable_symbols": data.get("executable_symbols") or [],
        "signal": signal if isinstance(signal, dict) else None,
    }


def aave_reserve_cache(cache_path: Path) -> Optional[dict]:
    return read_aave_reserve_cache(cache_path)


def borrow_target_universe(aave_cache_path: Path, stable_target_cache_path: Path) -> Optional[dict]:
    return read_borrow_target_universe(aave_cache_path, stable_target_cache_path)


def stable_target_cache(cache_path: Path) -> Optional[dict]:
    return read_stable_target_cache(cache_path)


def observation_count(database_url: str) -> Optional[int]:
    return read_observation_count(database_url)


def database_table_counts(database_url: str) -> Optional[dict]:
    return read_database_table_counts(database_url)


def available_chart_symbols(database_url: str, limit: int = 500) -> list[str]:
    return read_available_chart_symbols(database_url, limit)


def available_candidate_symbols(database_url: str, limit: int = 500) -> list[str]:
    return read_available_candidate_symbols(database_url, limit)


def recent_velocity_timepoints(database_url: str, limit: int = 200) -> list[dict]:
    return read_recent_velocity_timepoints(database_url, limit)


def velocity_timepoint_snapshot(database_url: str, snapshot_id: int | None = None) -> Optional[dict]:
    return read_velocity_timepoint_snapshot(database_url, snapshot_id)


def recent_observations(database_url: str, symbol: str, limit: int) -> list[dict]:
    return read_recent_observations(database_url, symbol, limit)


def latest_observation_prices(database_url: str, symbols: list[str]) -> dict[str, dict]:
    return read_latest_observation_prices(database_url, symbols)


def latest_observation_prices_at_or_before(database_url: str, symbols: list[str], cutoff_at) -> dict[str, dict]:
    return read_latest_observation_prices_at_or_before(database_url, symbols, cutoff_at)


def recent_aave_pair_prices(database_url: str, x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    return read_recent_aave_pair_prices(database_url, x_symbol, y_symbol, limit)


def recent_binance_pair_prices(database_url: str, x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    return read_recent_binance_pair_prices(database_url, x_symbol, y_symbol, limit)


def latest_candidate_price_rows(database_url: str, symbols: list[str]) -> dict[str, dict]:
    return read_latest_candidate_price_rows(database_url, symbols)


def recent_binance_price_history(database_url: str, symbol: str, limit: int) -> list[dict]:
    return read_recent_binance_price_history(database_url, symbol, limit)
