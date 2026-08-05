import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from execution.dex_costs import estimate_symbol_cost
from execution.execution_payload import PayloadConfig, build_execution_payload
from execution.liquidation_scan import health_factor_band
from execution.plan_quotes import quote_execution_plan
from core.config_schema import parse_env_float
from core.env_loader import load_env_files, resolve_env_path
from strategy.arbitrage import ArbitrageConfig, simulate_four_route_cycles
from strategy.limits import resolve_min_paper_profit_usd
from market.observer import ASSETS, DEFAULT_BINANCE_REST_BASES
from web.control_panel_config import (
    STRATEGY_DEFAULTS,
    strategy_config as read_strategy_config,
    write_strategy_config as save_strategy_config,
)
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
from web.control_panel_liquidation_base import configured_database_url

SRC_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SRC_ROOT
WEB_DIR = Path(__file__).resolve().parent
load_env_files(__file__, override=False)
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
STATE_DIR = RUNTIME_DIR / "state"
CONFIG_DIR = RUNTIME_DIR / "config"
CACHE_DIR = RUNTIME_DIR / "cache"
STRATEGY_CONFIG_PATH = CONFIG_DIR / "strategy_config.json"
LATEST_ARBITRAGE_PATH = STATE_DIR / "latest_arbitrage.json"
LATEST_EXECUTABLE_SIGNAL_PATH = STATE_DIR / "latest_executable_signal.json"
LATEST_EXTREMES_PATH = STATE_DIR / "latest_extremes.json"
AAVE_RESERVE_CACHE_PATH = CACHE_DIR / "aave_reserve_assets.json"
DEX_BORROW_TARGET_CACHE_PATH = CACHE_DIR / "dex_borrow_targets.json"
LIQUIDATION_SAMPLE_LIBRARY_PATH = RUNTIME_DIR / "samples" / "liquidation_candidates" / "index.json"

SUMMARY_SIDE_LIMIT = 5
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
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return save_strategy_config(STRATEGY_CONFIG_PATH, payload)


def safe_latest(fetcher) -> Optional[dict]:
    try:
        return fetcher()
    except Exception:
        return None


def latest_binance_extremes() -> Optional[dict]:
    return read_latest_binance_extremes(configured_database_url(), LATEST_EXTREMES_PATH)


def latest_binance_extremes_file() -> Optional[dict]:
    return read_latest_binance_extremes_file(LATEST_EXTREMES_PATH)


def latest_arbitrage_simulation() -> Optional[dict]:
    return read_latest_arbitrage_simulation(configured_database_url(), LATEST_ARBITRAGE_PATH)


def latest_arbitrage_simulation_file() -> Optional[dict]:
    return read_latest_arbitrage_simulation_file(LATEST_ARBITRAGE_PATH)


def liquidation_sample_manifest() -> Optional[dict]:
    try:
        if not LIQUIDATION_SAMPLE_LIBRARY_PATH.exists():
            return None
        data = json.loads(LIQUIDATION_SAMPLE_LIBRARY_PATH.read_text(encoding="utf-8"))
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
    return read_observation_count(configured_database_url())


def database_table_counts() -> Optional[dict]:
    return read_database_table_counts(configured_database_url())


def recent_observations(symbol: str, limit: int) -> list[dict]:
    return read_recent_observations(configured_database_url(), symbol, limit)


def recent_binance_price_history(symbol: str, limit: int) -> list[dict]:
    return read_recent_binance_price_history(configured_database_url(), symbol, limit)


def recent_aave_pair_prices(x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    return read_recent_aave_pair_prices(configured_database_url(), x_symbol, y_symbol, limit)


def recent_binance_pair_prices(x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    return read_recent_binance_pair_prices(configured_database_url(), x_symbol, y_symbol, limit)


def latest_candidate_price_rows(symbols: list[str]) -> dict[str, dict]:
    return read_latest_candidate_price_rows(configured_database_url(), symbols)


def recent_velocity_timepoints(limit: int = 200) -> list[dict]:
    return read_recent_velocity_timepoints(configured_database_url(), limit)


def velocity_timepoint_snapshot(snapshot_id: int | None = None) -> Optional[dict]:
    return read_velocity_timepoint_snapshot(configured_database_url(), snapshot_id)


def available_chart_symbols(limit: int = 500) -> list[str]:
    symbols = read_available_chart_symbols(configured_database_url(), limit)
    merged = list(dict.fromkeys([*ASSETS.keys(), *symbols]))
    return merged[:limit]


def available_candidate_symbols(limit: int = 500) -> list[str]:
    symbols = read_available_candidate_symbols(configured_database_url(), limit)
    merged = list(dict.fromkeys([*symbols, *ASSETS.keys()]))
    return merged[:limit]


def configured_fee_slippage_percent() -> float:
    fallback = parse_env_float("ALERT_DIFF_PERCENT", 0.30, minimum=0)[0]
    value, error = parse_env_float("FEE_SLIPPAGE_PERCENT", fallback, minimum=0)
    return fallback if error else value


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
    default = float(STRATEGY_DEFAULTS[key])
    raw = (config or strategy_config()).get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _strategy_int(
    key: str,
    config: dict | None = None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = int(_strategy_float(key, config))
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def read_slippage_bps() -> int:
    return _strategy_int("EXECUTION_SLIPPAGE_BPS", minimum=0, maximum=5000)


def read_execution_plan_max_age_seconds() -> float:
    return float(_strategy_int("EXECUTION_PLAN_MAX_AGE_SECONDS", minimum=1))


def arbitrage_config_from_strategy() -> ArbitrageConfig:
    config = strategy_config()
    notional_usd = _strategy_float("ARBITRAGE_NOTIONAL_USD", config, minimum=0.0)
    target_profit_percent = _strategy_float(
        "ARBITRAGE_TARGET_PROFIT_PERCENT",
        config,
        minimum=0.0,
    )
    configured_min_paper_profit_usd = _strategy_float("ARBITRAGE_MIN_PAPER_PROFIT_USD", config, minimum=0.0)
    return ArbitrageConfig(
        notional_usd=notional_usd,
        trade_fee_percent=_strategy_float("ARBITRAGE_TRADE_FEE_PERCENT", config, minimum=0.0),
        flashloan_fee_percent=_strategy_float("ARBITRAGE_FLASHLOAN_FEE_PERCENT", config, minimum=0.0),
        min_window_spread_percent=_strategy_float("ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT", config, minimum=0.0),
        min_paper_profit_usd=resolve_min_paper_profit_usd(
            configured_min_paper_profit_usd,
            notional_usd=notional_usd,
            target_profit_percent=target_profit_percent,
        ),
        fee_reserve_percent=_strategy_float("ARBITRAGE_FEE_RESERVE_PERCENT", config, minimum=0.0),
        basket_size=_strategy_int("ARBITRAGE_BASKET_SIZE", config, minimum=1, maximum=10),
        min_up_change_percent=_strategy_float("TRIGGER_MIN_UP_CHANGE_PERCENT", config, minimum=0.0),
        min_down_change_percent=_strategy_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", config, minimum=0.0),
    )


def build_velocity_summary(snapshot: dict, side_limit: int = SUMMARY_SIDE_LIMIT) -> dict:
    top = snapshot.get("top", [])[:side_limit]
    bottom = snapshot.get("bottom", [])[:side_limit]
    symbols = [row["symbol"] for row in [*top, *bottom] if row.get("symbol")]
    end_at = datetime.fromisoformat(str(snapshot["observed_at"]).replace("Z", "+00:00"))
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    start_at = end_at - timedelta(seconds=float(snapshot["window_seconds"]))
    aave_start_prices = read_latest_observation_prices_at_or_before(configured_database_url(), symbols, start_at)
    aave_end_prices = read_latest_observation_prices_at_or_before(configured_database_url(), symbols, end_at)
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
        "summary_note": None if aave_row_count else "当前时间点的前后5组币种没有Aave映射，所以结果为NULL。",
    }


def read_require_binance_ws_for_execution() -> bool:
    return os.getenv("ARBITRAGE_REQUIRE_BINANCE_WS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def assert_fresh_execution_plan(simulation: dict) -> None:
    if not simulation.get("signal"):
        reasons = ", ".join(simulation.get("blocked_reasons") or ["signal is false"])
        raise RuntimeError(f"execution plan is blocked: {reasons}")

    if read_require_binance_ws_for_execution() and simulation.get("price_source") != "ws":
        raise RuntimeError("execution plan is blocked: Binance WebSocket price source is required")

    observed_at = str(simulation.get("observed_at", "")).replace("Z", "+00:00")
    try:
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RuntimeError("execution plan is blocked: invalid observed_at") from exc

    age_seconds = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    max_age = read_execution_plan_max_age_seconds()
    if age_seconds > max_age:
        raise RuntimeError(
            f"execution plan is blocked: stale plan age={age_seconds:.1f}s max={max_age:.1f}s"
        )

def opportunity_health_rows(extremes: Optional[dict], config: Optional[dict] = None) -> list[dict]:
    if not extremes:
        return []
    basket = extremes.get("basket")
    if not isinstance(basket, list) or not basket:
        return []
    config = config or strategy_config()
    up_threshold = _strategy_float("TRIGGER_MIN_UP_CHANGE_PERCENT", config, minimum=0.0001)
    down_threshold = _strategy_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", config, minimum=0.0001)
    rows: list[dict] = []
    for index, item in enumerate(basket, start=1):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        raw_change = item.get("change_percent")
        try:
            change_percent = float(raw_change) if raw_change is not None else None
        except (TypeError, ValueError):
            change_percent = None
        current_price = item.get("current_price")
        start_price = item.get("start_price")
        window_ready = bool(item.get("window_ready"))
        price_source = item.get("price_source")
        threshold = up_threshold if (change_percent or 0.0) >= 0 else down_threshold
        health_score = None
        health_gap_percent = None
        if change_percent is not None:
            health_score = abs(change_percent) / threshold * 100 if threshold > 0 else 0.0
            health_gap_percent = abs(change_percent) - threshold
        if not window_ready:
            status = "watching"
        elif health_score is None:
            status = "watching"
        elif health_score >= 130:
            status = "selected"
        elif health_score >= 100:
            status = "candidate"
        elif health_score >= 70:
            status = "watching"
        else:
            status = "healthy"
        rows.append(
            {
                "rank": index,
                "symbol": symbol,
                "change_percent": change_percent,
                "health_score": round(health_score, 2) if health_score is not None else None,
                "health_gap_percent": round(health_gap_percent, 4) if health_gap_percent is not None else None,
                "trigger_threshold_percent": threshold,
                "status": status,
                "window_ready": window_ready,
                "current_price": current_price,
                "start_price": start_price,
                "price_source": price_source,
                "window_seconds": extremes.get("window_seconds"),
                "observed_at": extremes.get("observed_at"),
            }
        )
    rows.sort(
        key=lambda row: (
            row["health_score"] is None,
            -(float(row["health_score"]) if row["health_score"] is not None else -1.0),
            -abs(float(row["change_percent"]) if row["change_percent"] is not None else 0.0),
            row["symbol"],
        )
    )
    for position, row in enumerate(rows, start=1):
        row["rank"] = position
    return rows


def opportunity_health_summary(rows: list[dict], config: Optional[dict] = None) -> dict:
    config = config or strategy_config()
    monitor_window_seconds = _strategy_float("BINANCE_CHANGE_WINDOW_SECONDS", config, minimum=0.0)
    trigger_up = _strategy_float("TRIGGER_MIN_UP_CHANGE_PERCENT", config, minimum=0.0)
    trigger_down = _strategy_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", config, minimum=0.0)
    candidate_count = sum(1 for row in rows if row.get("status") in {"candidate", "selected"})
    selected_count = sum(1 for row in rows if row.get("status") == "selected")
    watched_count = sum(1 for row in rows if row.get("status") == "watching")
    healthy_count = sum(1 for row in rows if row.get("status") == "healthy")
    best_row = rows[0] if rows else None
    return {
        "total": len(rows),
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "watched_count": watched_count,
        "healthy_count": healthy_count,
        "monitor_window_seconds": monitor_window_seconds,
        "trigger_up_percent": trigger_up,
        "trigger_down_percent": trigger_down,
        "best_symbol": best_row.get("symbol") if best_row else None,
        "best_health_score": best_row.get("health_score") if best_row else None,
        "best_status": best_row.get("status") if best_row else None,
    }
