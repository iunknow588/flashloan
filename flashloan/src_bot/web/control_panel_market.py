import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.execution.dex_costs import estimate_symbol_cost
from bot.execution.execution_payload import PayloadConfig, build_execution_payload
from bot.execution.liquidation_scan import health_factor_band
from bot.execution.plan_quotes import quote_execution_plan
from core.config_schema import parse_env_float
from core.env_loader import load_env_files, resolve_env_path
from market.strategy.arbitrage import ArbitrageConfig, simulate_four_route_cycles
from market.observer import ASSETS, DEFAULT_BINANCE_REST_BASES
from market.binance_market.control_panel_config import (
    STRATEGY_DEFAULTS,
    strategy_config as read_strategy_config,
    write_strategy_config as save_strategy_config,
)
from core.parameter_config import STRATEGY_CONFIG_PATH
from web.control_panel_data import (
    aave_reserve_cache as read_aave_reserve_cache,
    borrow_target_universe as read_borrow_target_universe,
    available_candidate_symbols as read_available_candidate_symbols,
    available_chart_symbols as read_available_chart_symbols,
    database_table_counts as read_database_table_counts,
    latest_arbitrage_simulation as read_latest_arbitrage_simulation,
    latest_arbitrage_simulation_file as read_latest_arbitrage_simulation_file,
    latest_binance_extremes as read_latest_binance_extremes,
    latest_binance_extremes_file as read_latest_binance_extremes_file,
    latest_executable_signal as read_latest_executable_signal,
    latest_candidate_price_rows as read_latest_candidate_price_rows,
    latest_observation_prices_at_or_before as read_latest_observation_prices_at_or_before,
    observation_count as read_observation_count,
    recent_aave_pair_prices as read_recent_aave_pair_prices,
    recent_binance_pair_prices as read_recent_binance_pair_prices,
    recent_binance_price_history as read_recent_binance_price_history,
    recent_observations as read_recent_observations,
    recent_velocity_timepoints as read_recent_velocity_timepoints,
    velocity_timepoint_snapshot as read_velocity_timepoint_snapshot,
)
from market.binance_market.opportunity_health import (
    opportunity_health_rows,
    opportunity_health_summary,
)
from market.binance_market.strategy_runtime import (
    arbitrage_config_from_strategy as build_arbitrage_config,
    assert_fresh_execution_plan as build_assert_fresh_execution_plan,
    configured_fee_slippage_percent as read_configured_fee_slippage_percent,
    read_execution_plan_max_age_seconds as read_market_execution_plan_max_age_seconds,
    read_require_binance_ws_for_execution as read_market_require_binance_ws_for_execution,
    read_slippage_bps as read_market_slippage_bps,
    strategy_float,
    strategy_int,
)
from web.control_panel_liquidation_base import database_url_or_none

SRC_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SRC_ROOT
WEB_DIR = Path(__file__).resolve().parent
load_env_files(__file__, override=False)
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
STATE_DIR = RUNTIME_DIR / "state"
CACHE_DIR = RUNTIME_DIR / "cache"
LATEST_ARBITRAGE_PATH = STATE_DIR / "latest_arbitrage.json"
LATEST_EXECUTABLE_SIGNAL_PATH = STATE_DIR / "latest_executable_signal.json"
LATEST_EXTREMES_PATH = STATE_DIR / "latest_extremes.json"
AAVE_RESERVE_CACHE_PATH = CACHE_DIR / "aave_reserve_assets.json"
DEX_BORROW_TARGET_CACHE_PATH = CACHE_DIR / "dex_borrow_targets.json"
LIQUIDATION_SAMPLE_LIBRARY_PATH = RUNTIME_DIR / "samples" / "liquidation_candidates" / "index.json"

SUMMARY_SIDE_LIMIT = 50
SUMMARY_INITIAL_AMOUNT = 100.0
VELOCITY_SIDE_LIMIT = 100
AAVE_RESERVE_SYMBOL_LIMIT = 1000
def validate_symbols(raw_symbols: object) -> list[str]:
    if not isinstance(raw_symbols, list):
        raise ValueError("symbols must be a list")
    symbols = list(dict.fromkeys(str(value).strip().upper() for value in raw_symbols))
    unsupported = [symbol for symbol in symbols if symbol not in ASSETS]
    if unsupported:
        raise ValueError(f"unsupported symbol: {unsupported[0]}")
    if not symbols:
        raise ValueError("select at least one symbol")
    return symbols


def velocity_start_symbols() -> list[str]:
    return selected_symbols or [f"velocity_top_{VELOCITY_SIDE_LIMIT}", f"velocity_bottom_{VELOCITY_SIDE_LIMIT}"]


def strategy_config() -> dict:
    return read_strategy_config(STRATEGY_CONFIG_PATH)


def write_strategy_config(payload: dict) -> dict:
    return save_strategy_config(STRATEGY_CONFIG_PATH, payload)


def safe_latest(fetcher) -> Optional[dict]:
    try:
        return fetcher()
    except Exception:
        return None


def latest_binance_extremes() -> Optional[dict]:
    return read_latest_binance_extremes(database_url_or_none(), LATEST_EXTREMES_PATH)


def latest_binance_extremes_file() -> Optional[dict]:
    return read_latest_binance_extremes_file(LATEST_EXTREMES_PATH)


def latest_arbitrage_simulation() -> Optional[dict]:
    return read_latest_arbitrage_simulation(database_url_or_none(), LATEST_ARBITRAGE_PATH)


def latest_arbitrage_simulation_file() -> Optional[dict]:
    return read_latest_arbitrage_simulation_file(LATEST_ARBITRAGE_PATH)


def liquidation_sample_manifest(library_path: Optional[Path] | None = None) -> Optional[dict]:
    path = library_path if library_path is not None else LIQUIDATION_SAMPLE_LIBRARY_PATH
    try:
        if path is None or not Path(path).exists():
            return None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def latest_executable_signal() -> Optional[dict]:
    return read_latest_executable_signal(LATEST_EXECUTABLE_SIGNAL_PATH)


def aave_reserve_cache() -> Optional[dict]:
    return read_aave_reserve_cache(AAVE_RESERVE_CACHE_PATH)


def borrow_target_universe() -> Optional[dict]:
    return read_borrow_target_universe(AAVE_RESERVE_CACHE_PATH, DEX_BORROW_TARGET_CACHE_PATH)


def observation_count() -> Optional[int]:
    return read_observation_count(database_url_or_none())


def database_table_counts() -> Optional[dict]:
    return read_database_table_counts(database_url_or_none())


def recent_observations(symbol: str, limit: int) -> list[dict]:
    return read_recent_observations(database_url_or_none(), symbol, limit)


def recent_binance_price_history(symbol: str, limit: int) -> list[dict]:
    return read_recent_binance_price_history(database_url_or_none(), symbol, limit)


def recent_aave_pair_prices(x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    return read_recent_aave_pair_prices(database_url_or_none(), x_symbol, y_symbol, limit)


def recent_binance_pair_prices(x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    return read_recent_binance_pair_prices(database_url_or_none(), x_symbol, y_symbol, limit)


def latest_candidate_price_rows(symbols: list[str]) -> dict[str, dict]:
    return read_latest_candidate_price_rows(database_url_or_none(), symbols)


def recent_velocity_timepoints(limit: int = 200) -> list[dict]:
    return read_recent_velocity_timepoints(database_url_or_none(), limit)


def velocity_timepoint_snapshot(snapshot_id: int | None = None) -> Optional[dict]:
    return read_velocity_timepoint_snapshot(database_url_or_none(), snapshot_id)


def available_chart_symbols(limit: int = 500) -> list[str]:
    symbols = read_available_chart_symbols(database_url_or_none(), limit)
    merged = list(dict.fromkeys([*ASSETS.keys(), *symbols]))
    return merged[:limit]


def available_candidate_symbols(limit: int = 500) -> list[str]:
    symbols = read_available_candidate_symbols(database_url_or_none(), limit)
    merged = list(dict.fromkeys([*symbols, *ASSETS.keys()]))
    return merged[:limit]


def configured_fee_slippage_percent() -> float:
    return read_configured_fee_slippage_percent()


def latest_reference_price(symbol: str) -> float:
    rows = recent_observations(symbol, 1)
    if not rows:
        raise RuntimeError(f"No observations found for {symbol}")
    return float(rows[-1]["aave_price"])


def _strategy_float(
    key: str,
    config: dict | None = None,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    return strategy_float(key, config or strategy_config(), minimum=minimum, maximum=maximum)


def _strategy_int(
    key: str,
    config: dict | None = None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    return strategy_int(key, config or strategy_config(), minimum=minimum, maximum=maximum)


def read_slippage_bps() -> int:
    return read_market_slippage_bps(strategy_config())


def read_execution_plan_max_age_seconds() -> float:
    return read_market_execution_plan_max_age_seconds(strategy_config())


def arbitrage_config_from_strategy() -> ArbitrageConfig:
    return build_arbitrage_config(strategy_config())


def build_velocity_summary(snapshot: dict, side_limit: int = SUMMARY_SIDE_LIMIT) -> dict:
    top = snapshot.get("top", [])[:side_limit]
    bottom = snapshot.get("bottom", [])[:side_limit]
    symbols = [row["symbol"] for row in [*top, *bottom] if row.get("symbol")]
    end_at = datetime.fromisoformat(str(snapshot["observed_at"]).replace("Z", "+00:00"))
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    start_at = end_at - timedelta(seconds=float(snapshot["window_seconds"]))
    aave_start_prices = read_latest_observation_prices_at_or_before(database_url_or_none(), symbols, start_at)
    aave_end_prices = read_latest_observation_prices_at_or_before(database_url_or_none(), symbols, end_at)
    rows = []
    aave_row_count = 0
    aave_symbols: set[str] = set()
    route_order = [
        "strategy_1_forward_x_to_usdc_to_y_to_x",
        "strategy_1_reverse_y_to_x_to_usdc_to_y",
        "strategy_2_forward_x_to_y_to_usdc_to_x",
        "strategy_2_reverse_y_to_usdc_to_x_to_y",
    ]
    route_labels = {
        "strategy_1_forward_x_to_usdc_to_y_to_x": "1",
        "strategy_1_reverse_y_to_x_to_usdc_to_y": "2",
        "strategy_2_forward_x_to_y_to_usdc_to_x": "3",
        "strategy_2_reverse_y_to_usdc_to_x_to_y": "4",
    }

    def display_leg(route: dict | None, step_index: int) -> str | None:
        if not route:
            return None
        steps = route.get("route_steps") or []
        if step_index < len(steps):
            step = steps[step_index]
            return f"{step['to_symbol']} {float(step['output_amount']):.6f}"
        return None

    for top_row in top:
        for bottom_row in bottom:
            x_symbol = top_row["symbol"]
            y_symbol = bottom_row["symbol"]
            x_start_row = aave_start_prices.get(x_symbol)
            x_end_row = aave_end_prices.get(x_symbol)
            y_start_row = aave_start_prices.get(y_symbol)
            y_end_row = aave_end_prices.get(y_symbol)
            x_start_price = float(x_start_row["aave_price"]) if x_start_row else None
            x_end_price = float(x_end_row["aave_price"]) if x_end_row else None
            y_start_price = float(y_start_row["aave_price"]) if y_start_row else None
            y_end_price = float(y_end_row["aave_price"]) if y_end_row else None
            x_change_percent = (x_end_price / x_start_price - 1) * 100 if x_start_price and x_end_price is not None else None
            y_change_percent = (y_end_price / y_start_price - 1) * 100 if y_start_price and y_end_price is not None else None
            has_aave = None not in {x_start_price, x_end_price, y_start_price, y_end_price}
            routes = []
            if has_aave:
                aave_row_count += 1
                aave_symbols.update([x_symbol, y_symbol])
                config = arbitrage_config_from_strategy()
                x = {
                    "symbol": x_symbol,
                    "start_price": x_start_price,
                    "end_price": x_end_price,
                    "change_percent": x_change_percent,
                }
                y = {
                    "symbol": y_symbol,
                    "start_price": y_start_price,
                    "end_price": y_end_price,
                    "change_percent": y_change_percent,
                }
                routes = simulate_four_route_cycles(x, y, config, SUMMARY_INITIAL_AMOUNT)
            route_map = {route["strategy"]: route for route in routes}
            for strategy_name in route_order:
                route = route_map.get(strategy_name)
                rows.append(
                    {
                        "pair": f"{x_symbol} / {y_symbol}",
                        "path_no": route_labels[strategy_name],
                        "x_symbol": x_symbol,
                        "y_symbol": y_symbol,
                        "start_token": route["initial_symbol"] if route else "NULL",
                        "start_amount": SUMMARY_INITIAL_AMOUNT if route else None,
                        "first_hop": display_leg(route, 0),
                        "second_hop": display_leg(route, 1),
                        "third_hop": display_leg(route, 2),
                        "fourth_hop": f"{route['route_symbols'][-1]} {float(route['remaining_amount']):.6f}" if route else None,
                        "profit_percent": route["profit_percent"] if route else None,
                        "x_change_percent": x_change_percent,
                        "y_change_percent": y_change_percent,
                        "x_start_price": x_start_price,
                        "x_end_price": x_end_price,
                        "y_start_price": y_start_price,
                        "y_end_price": y_end_price,
                        "x_aave_price": x_end_price,
                        "y_aave_price": y_end_price,
                        "aave_ratio": x_end_price / y_end_price if x_end_price is not None and y_end_price is not None else None,
                        "unavailable_reason": "same symbol" if x_symbol == y_symbol else ("no aave data" if not has_aave else None),
                        "initial_amount": SUMMARY_INITIAL_AMOUNT,
                    }
                )
    return {
        "id": snapshot["id"],
        "observed_at": snapshot["observed_at"],
        "window_seconds": snapshot["window_seconds"],
        "sample_count": snapshot["sample_count"],
        "top": top,
        "bottom": bottom,
        "rows": rows,
        "expected_rows": len(top) * len(bottom) * 4,
        "side_limit": side_limit,
        "initial_amount": SUMMARY_INITIAL_AMOUNT,
        "aave_row_count": aave_row_count,
        "aave_symbols": sorted(aave_symbols),
        "summary_note": None if aave_row_count else "当前时间点的前后50组币种没有Aave映射，所以结果为NULL。",
    }


def read_require_binance_ws_for_execution() -> bool:
    return read_market_require_binance_ws_for_execution()


def assert_fresh_execution_plan(simulation: dict) -> None:
    build_assert_fresh_execution_plan(simulation, strategy_config())

