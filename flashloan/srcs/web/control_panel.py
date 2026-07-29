import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution.dex_costs import estimate_symbol_cost, parse_trade_usd_amounts
from core.env_loader import load_env_files, resolve_env_path
from execution.execution_payload import PayloadConfig, build_execution_payload
from market.aave_reserve_cache import load_aave_reserve_symbol_list
from market.observer import ASSETS
from execution.plan_quotes import quote_execution_plan
from strategy.arbitrage import ArbitrageConfig, simulate_four_route_cycles
from web.control_panel_config import (
    strategy_config as read_strategy_config,
    unified_sampling_profile,
    write_strategy_config as save_strategy_config,
)
from web.control_panel_data import (
    aave_reserve_cache as read_aave_reserve_cache,
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
from web.control_panel_stats import testnet_trade_stats as read_testnet_trade_stats, trade_stats as read_trade_stats
from db.storage import ensure_database_schema, require_psycopg


WEB_DIR = Path(__file__).resolve().parent
APP_DIR = SRC_ROOT
load_env_files(__file__)

RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
STATE_DIR = RUNTIME_DIR / "state"
CONFIG_DIR = RUNTIME_DIR / "config"
CACHE_DIR = RUNTIME_DIR / "cache"
LOG_DIR = RUNTIME_DIR / "logs"
OBSERVER_PATH = APP_DIR / "market" / "observer.py"
TEMPLATE_PATH = WEB_DIR / "templates" / "control_panel.html"
LATEST_ARBITRAGE_PATH = STATE_DIR / "latest_arbitrage.json"
LATEST_EXECUTABLE_SIGNAL_PATH = STATE_DIR / "latest_executable_signal.json"
LATEST_EXTREMES_PATH = STATE_DIR / "latest_extremes.json"
AAVE_RESERVE_CACHE_PATH = CACHE_DIR / "aave_reserve_assets.json"
OBSERVER_PID_PATH = RUNTIME_DIR / "observer.pid"
STRATEGY_CONFIG_PATH = CONFIG_DIR / "strategy_config.json"
REPO_ROOT = APP_DIR.parents[1]
DEFAULT_AAVE_RPC_CANDIDATES = [
    "https://api.avax.network/ext/bc/C/rpc",
    "https://rpc.ankr.com/avalanche",
    "https://avalanche-c-chain-rpc.publicnode.com",
]

app = Flask(__name__)
observer_process: Optional[subprocess.Popen] = None
selected_symbols: list[str] = []
discovered_observer_pid: Optional[int] = None
discovered_observer_checked_at = 0.0
observer_starting = False
observer_start_error: Optional[str] = None
observer_start_lock = threading.Lock()
observer_start_progress = {
    "state": "stopped",
    "stage": "未启动",
    "percent": 0,
    "started_at": None,
}
control_status = {
    "state": "stopped",
    "stage": "",
    "message": "",
    "percent": 0,
    "updated_at": 0.0,
    "ttl_seconds": 0.0,
}

VELOCITY_SIDE_LIMIT = 100
SUMMARY_SIDE_LIMIT = 5
SUMMARY_INITIAL_AMOUNT = 100.0
AAVE_RESERVE_SYMBOL_LIMIT = 1000

def configured_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("缺少 DATABASE_URL。请先在 .env 或系统环境变量中配置数据库连接。")
    return database_url


def aave_rpc_urls() -> list[str]:
    raw_primary = os.getenv("AVALANCHE_RPC", "").strip()
    raw_fallbacks = os.getenv("AVALANCHE_RPCS", "").strip()
    candidates: list[str] = []
    for raw in [raw_primary, raw_fallbacks, ",".join(DEFAULT_AAVE_RPC_CANDIDATES)]:
        for part in raw.replace("\n", ",").split(","):
            candidate = part.strip().rstrip("/")
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def is_observer_running() -> bool:
    if observer_process is not None and observer_process.poll() is None:
        return True
    pid = read_observer_pid()
    return pid is not None and observer_process_exists(pid)


def observer_pid() -> Optional[int]:
    if observer_process is not None and observer_process.poll() is None:
        return observer_process.pid
    return read_observer_pid()


def process_exists(pid: int) -> bool:
    if os.name == "nt":
        return windows_process_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def observer_process_exists(pid: int) -> bool:
    if not process_exists(pid):
        return False
    if os.name == "nt":
        try:
            command = (
                "Get-CimInstance Win32_Process -Filter \"ProcessId = "
                f"{int(pid)}\" | Select-Object -First 1 -ExpandProperty CommandLine"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return "observer.py" in result.stdout.replace("\\", "/")
        except Exception:
            return False
    try:
        command_line = Path(f"/proc/{int(pid)}/cmdline").read_text(encoding="utf-8", errors="ignore")
        return "observer.py" in command_line
    except OSError:
        return False


def windows_process_exists(pid: int) -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, int(pid))
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def discover_observer_pid() -> Optional[int]:
    global discovered_observer_checked_at, discovered_observer_pid
    now = time.monotonic()
    if now - discovered_observer_checked_at < 3.0:
        if discovered_observer_pid is None or observer_process_exists(discovered_observer_pid):
            return discovered_observer_pid
        discovered_observer_pid = None
    if discovered_observer_pid is not None and observer_process_exists(discovered_observer_pid):
        return discovered_observer_pid
    discovered_observer_checked_at = now
    if os.name != "nt":
        return None
    try:
        command = (
            "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
            "Where-Object { $_.CommandLine -like '*market*observer.py*' } | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        raw = result.stdout.strip().splitlines()
        pid = int(raw[0].strip()) if raw else None
        discovered_observer_pid = pid if pid and observer_process_exists(pid) else None
        if discovered_observer_pid is not None:
            write_observer_pid(discovered_observer_pid)
        return discovered_observer_pid
    except Exception:
        discovered_observer_pid = None
        return None


def read_observer_pid() -> Optional[int]:
    try:
        if not OBSERVER_PID_PATH.exists():
            return discover_observer_pid()
        pid = int(OBSERVER_PID_PATH.read_text(encoding="utf-8").strip())
        if observer_process_exists(pid):
            return pid
        OBSERVER_PID_PATH.unlink(missing_ok=True)
    except (OSError, ValueError):
        return None
    return None


def quick_observer_pid() -> Optional[int]:
    if observer_process is not None and observer_process.poll() is None:
        return observer_process.pid
    try:
        if not OBSERVER_PID_PATH.exists():
            return None
        pid = int(OBSERVER_PID_PATH.read_text(encoding="utf-8").strip())
        if observer_process_exists(pid):
            return pid
        OBSERVER_PID_PATH.unlink(missing_ok=True)
    except (OSError, ValueError):
        return None
    return None


def quick_observer_running() -> bool:
    return quick_observer_pid() is not None


def set_observer_progress(state: str, stage: str, percent: int) -> None:
    observer_start_progress.update(
        {
            "state": state,
            "stage": stage,
            "percent": max(0, min(int(percent), 100)),
            "started_at": observer_start_progress.get("started_at"),
        }
    )


def set_control_status(
    state: str,
    stage: str,
    message: str,
    percent: int,
    ttl_seconds: float = 0.0,
) -> None:
    control_status.update(
        {
            "state": state,
            "stage": stage,
            "message": message,
            "percent": max(0, min(int(percent), 100)),
            "updated_at": time.monotonic(),
            "ttl_seconds": max(0.0, float(ttl_seconds)),
        }
    )


def database_lock_message(action: str, exc: Exception) -> str:
    detail = str(exc)
    lowered = detail.lower()
    lock_markers = (
        "deadlock detected",
        "lock timeout",
        "could not obtain lock",
        "accessexclusivelock",
        "blocked by process",
        "canceling statement due to statement timeout",
    )
    if any(marker in lowered for marker in lock_markers):
        return (
            f"{action}执行失败：数据库可能被远程应用或其他连接锁定。"
            "请先关闭远程数据库访问链接、控制台查询窗口或其他写入进程后重试。"
            f" 原始错误：{detail}"
        )
    return f"{action}执行失败：{detail}"


def control_status_payload() -> Optional[dict]:
    updated_at = float(control_status.get("updated_at") or 0)
    ttl_seconds = float(control_status.get("ttl_seconds") or 0)
    if not updated_at:
        return None
    if ttl_seconds and time.monotonic() - updated_at > ttl_seconds:
        return None
    return {
        "state": control_status.get("state") or "stopped",
        "stage": control_status.get("stage") or "",
        "message": control_status.get("message") or "",
        "percent": int(control_status.get("percent") or 0),
    }


def observer_progress_payload(running: bool, starting: bool, latest_extremes: Optional[dict]) -> dict:
    progress = dict(observer_start_progress)
    if observer_start_error:
        progress.update({"state": "error", "stage": observer_start_error, "percent": 0})
    elif starting:
        progress["state"] = "initializing"
    elif running and latest_extremes:
        progress.update({"state": "running", "stage": "轻量模式：正在采集速度窗口", "percent": 100})
    elif running:
        progress.update({"state": "initializing", "stage": "等待第一个速度窗口", "percent": max(progress.get("percent", 0), 85)})
    else:
        progress.update({"state": "stopped", "stage": "观察器未运行", "percent": 0})
    return progress


def system_monitor_payload(
    running: bool,
    starting: bool,
    latest_extremes: Optional[dict],
    control_status_current: Optional[dict],
    reserve_cache: Optional[dict],
) -> dict:
    observer = observer_progress_payload(running, starting, latest_extremes)
    action = (control_status_current or {}).get("message") or "暂无控制操作记录"
    observer_stage = observer.get("stage") or "未知"
    symbols = displayed_symbols(running or starting)
    reserve_count = int((reserve_cache or {}).get("asset_count") or 0)
    window_seconds = None
    sample_count = None
    observed_at = None
    if latest_extremes:
        window_seconds = latest_extremes.get("window_seconds")
        sample_count = latest_extremes.get("sample_count")
        observed_at = latest_extremes.get("observed_at")
    control_state = (control_status_current or {}).get("state")
    if control_state == "error":
        state = "error"
    elif control_state == "initializing":
        state = "initializing"
    else:
        state = "running" if running else ("initializing" if starting else "stopped")
    return {
        "state": state,
        "action": action,
        "observer_stage": observer_stage,
        "symbol_count": len(symbols),
        "aave_reserve_count": reserve_count,
        "window_seconds": window_seconds,
        "sample_count": sample_count,
        "observed_at": observed_at,
        "percent": int((control_status_current or {}).get("percent") or observer.get("percent") or 0),
    }


def displayed_symbols(running: bool) -> list[str]:
    if not running:
        return []
    return selected_symbols or velocity_start_symbols()


def write_observer_pid(pid: int) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    OBSERVER_PID_PATH.write_text(str(pid), encoding="utf-8")


def build_observer_env() -> tuple[dict, list[str]]:
    env = os.environ.copy()
    config = strategy_config()
    profile = unified_sampling_profile(config)
    window_seconds = str(profile["seconds"])
    rpc_urls = aave_rpc_urls()
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    reserve_limit = int(os.getenv("AAVE_RESERVE_SYMBOL_LIMIT", str(AAVE_RESERVE_SYMBOL_LIMIT)))
    reserve_symbols = load_aave_reserve_symbol_list(
        rpc_urls,
        pool_address,
        limit=reserve_limit,
        exclude_stables=True,
    ) if pool_address else []
    tracked_symbols = [*reserve_symbols, "USDCUSDT"]
    env["SYMBOLS"] = ",".join(tracked_symbols if reserve_symbols else ASSETS.keys())
    env["BINANCE_SYMBOL_SELECTION"] = "velocity"
    env["BINANCE_TOP_SYMBOL_LIMIT"] = "0"
    env["BINANCE_VELOCITY_SIDE_LIMIT"] = str(VELOCITY_SIDE_LIMIT)
    env["BINANCE_CANDIDATE_DB_SIDE_LIMIT"] = str(VELOCITY_SIDE_LIMIT)
    env["BINANCE_WS_CHUNK_SIZE"] = "200"
    env["BINANCE_CHANGE_WINDOW_SECONDS"] = window_seconds
    env["BINANCE_VELOCITY_MIN_CHANGE_PERCENT"] = str(config.get("BINANCE_VELOCITY_MIN_CHANGE_PERCENT", 0.2))
    env["SAMPLE_SECONDS"] = window_seconds
    env["OBSERVATION_WRITE_SECONDS"] = window_seconds
    env["AAVE_POLL_SECONDS"] = window_seconds
    env["BINANCE_PAIR_HISTORY_WRITES"] = "false"
    env["AAVE_VERIFICATION_ENABLED"] = "true"
    env["AAVE_RESERVE_SYMBOL_LIMIT"] = str(reserve_limit)
    env["OBSERVATION_DB_WRITES"] = "true"
    env["REPORT_ONLY_ALERTS"] = "true"
    env["SKIP_DATABASE_SCHEMA"] = "true"
    env["OBSERVER_REQUIRE_DB_LOCK"] = "true"
    env["AVALANCHE_RPC"] = rpc_urls[0]
    env["AVALANCHE_RPCS"] = ",".join(rpc_urls)
    if pool_address:
        env["AAVE_POOL_ADDRESS"] = pool_address
    for key, value in config.items():
        env[key] = str(value)
    return env, tracked_symbols if reserve_symbols else list(ASSETS.keys())


def start_observer_background() -> None:
    global observer_process, selected_symbols, observer_start_error, observer_starting
    try:
        if quick_observer_running():
            set_observer_progress("running", "观察器已在运行", 100)
            return
        env, symbols = build_observer_env()
        set_observer_progress("initializing", "检查数据库配置", 10)
        database_url = configured_database_url()
        with observer_start_lock:
            if not observer_starting:
                return
        set_observer_progress("initializing", "准备币安速度监控", 35)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stdout = open(LOG_DIR / "observer_stdout.log", "ab", buffering=0)
        stderr = open(LOG_DIR / "observer_stderr.log", "ab", buffering=0)
        set_observer_progress("initializing", "启动观察器进程", 60)
        process = subprocess.Popen(
            [sys.executable, str(OBSERVER_PATH)],
            cwd=str(APP_DIR),
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
        with observer_start_lock:
            observer_process = process
            selected_symbols = symbols
            observer_start_error = None
        write_observer_pid(process.pid)
        set_observer_progress("initializing", "等待第一个速度窗口", 80)
    except Exception as exc:
        with observer_start_lock:
            observer_start_error = str(exc)
            selected_symbols = []
        set_observer_progress("error", str(exc), 0)
    finally:
        with observer_start_lock:
            observer_starting = False


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


def latest_executable_signal() -> Optional[dict]:
    return read_latest_executable_signal(LATEST_EXECUTABLE_SIGNAL_PATH)


def aave_reserve_cache() -> Optional[dict]:
    return read_aave_reserve_cache(AAVE_RESERVE_CACHE_PATH)


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
    try:
        return max(0.0, float(os.getenv("FEE_SLIPPAGE_PERCENT", os.getenv("ALERT_DIFF_PERCENT", "0.30"))))
    except ValueError:
        return 0.30


def latest_reference_price(symbol: str) -> float:
    rows = recent_observations(symbol, 1)
    if not rows:
        raise RuntimeError(f"No observations found for {symbol}")
    return float(rows[-1]["aave_price"])


def read_slippage_bps() -> int:
    return int(strategy_config()["EXECUTION_SLIPPAGE_BPS"])


def read_execution_plan_max_age_seconds() -> float:
    return float(strategy_config()["EXECUTION_PLAN_MAX_AGE_SECONDS"])


def arbitrage_config_from_strategy() -> ArbitrageConfig:
    config = strategy_config()
    return ArbitrageConfig(
        notional_usd=float(config["ARBITRAGE_NOTIONAL_USD"]),
        trade_fee_percent=float(config["ARBITRAGE_TRADE_FEE_PERCENT"]),
        flashloan_fee_percent=float(config["ARBITRAGE_FLASHLOAN_FEE_PERCENT"]),
        min_window_spread_percent=float(config["ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT"]),
        min_paper_profit_usd=float(config["ARBITRAGE_MIN_PAPER_PROFIT_USD"]),
        fee_reserve_percent=float(config["ARBITRAGE_FEE_RESERVE_PERCENT"]),
        basket_size=int(config["ARBITRAGE_BASKET_SIZE"]),
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


@app.get("/")
def index():
    chart_options = "\n".join(f'<option value="{escape(symbol)}">{escape(symbol)}</option>' for symbol in ASSETS)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__CHART_OPTIONS__", chart_options)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/favicon.ico")
def favicon():
    return Response(status=204)


@app.get("/api/status")
def status():
    running = quick_observer_running()
    binance_extremes = safe_latest(latest_binance_extremes_file)
    control_status_current = control_status_payload()
    reserve_cache = safe_latest(aave_reserve_cache)
    return jsonify(
        {
            "running": running,
            "starting": observer_starting,
            "start_error": observer_start_error,
            "observer_progress": observer_progress_payload(running, observer_starting, binance_extremes),
            "control_status": control_status_current,
            "system_monitor": system_monitor_payload(
                running,
                observer_starting,
                binance_extremes,
                control_status_current,
                reserve_cache,
            ),
            "pid": quick_observer_pid() if running else None,
            "symbols": displayed_symbols(running or observer_starting),
            "binance_extremes": binance_extremes,
            "arbitrage_simulation": safe_latest(latest_arbitrage_simulation_file),
            "executable_signal": safe_latest(latest_executable_signal),
            "aave_reserve_cache": reserve_cache,
            "strategy_config": strategy_config(),
            "sampling_profile": unified_sampling_profile(strategy_config()),
        }
    )


@app.get("/api/db-summary")
def db_summary():
    return jsonify(
        {
            "rows": observation_count(),
            "db_counts": database_table_counts(),
            "trade_stats": safe_latest(lambda: read_trade_stats(configured_database_url())),
            "testnet_trade_stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT)),
        }
    )


@app.get("/api/velocity-timepoints")
def velocity_timepoints():
    try:
        limit = max(1, min(int(request.args.get("limit", "200")), 500))
        rows = recent_velocity_timepoints(limit)
    except Exception as exc:
        if "does not exist" in str(exc):
            return jsonify({"timepoints": []})
        return jsonify({"error": str(exc), "timepoints": []}), 400
    return jsonify({"timepoints": rows})


@app.get("/api/velocity-summary")
def velocity_summary():
    try:
        raw_id = request.args.get("id", "").strip()
        snapshot_id = int(raw_id) if raw_id else None
        snapshot = velocity_timepoint_snapshot(snapshot_id)
        if not snapshot and snapshot_id is not None:
            snapshot = velocity_timepoint_snapshot(None)
        if not snapshot:
            return jsonify({"error": "no velocity timepoint found", "rows": []}), 404
        return jsonify(build_velocity_summary(snapshot))
    except Exception as exc:
        if "does not exist" in str(exc):
            return jsonify({"error": "initialize database and collect velocity windows first", "rows": []})
        return jsonify({"error": str(exc), "rows": []}), 400


@app.get("/api/strategy-config")
def get_strategy_config():
    config = strategy_config()
    return jsonify({"config": config, "sampling_profile": unified_sampling_profile(config), "running": is_observer_running()})


@app.post("/api/strategy-config")
def post_strategy_config():
    try:
        config = write_strategy_config(request.get_json(silent=True) or {})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"config": config, "sampling_profile": unified_sampling_profile(config), "restart_required": is_observer_running()})


@app.get("/api/trade-stats")
def get_trade_stats():
    return jsonify({"stats": safe_latest(lambda: read_trade_stats(configured_database_url()))})


@app.get("/api/testnet-trade-stats")
def get_testnet_trade_stats():
    return jsonify({"stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT))})


@app.get("/api/observations")
def observations():
    symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
    try:
        limit = max(2, min(int(request.args.get("limit", "120")), 1000))
        rows = recent_observations(symbol, limit) if symbol in ASSETS else []
        mode = "aave_observations" if rows else "binance_price_history"
        if not rows:
            rows = recent_binance_price_history(symbol, limit)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(
        {
            "symbol": symbol,
            "limit": limit,
            "mode": mode,
            "supports_aave": symbol in ASSETS and mode == "aave_observations",
            "supports_dex_costs": symbol in ASSETS and mode == "aave_observations",
            "fee_slippage_percent": configured_fee_slippage_percent(),
            "rows": rows,
        }
    )


@app.get("/api/aave-pair-prices")
def aave_pair_prices():
    x_symbol = request.args.get("x", "").strip().upper()
    y_symbol = request.args.get("y", "").strip().upper()
    if not x_symbol or not y_symbol:
        simulation = safe_latest(latest_arbitrage_simulation_file) or {}
        x_symbol = x_symbol or str(simulation.get("x_symbol") or simulation.get("a_symbol") or "").upper()
        y_symbol = y_symbol or str(simulation.get("y_symbol") or simulation.get("b_symbol") or "").upper()
    if x_symbol not in ASSETS or y_symbol not in ASSETS:
        return jsonify({"error": "x and y must be mapped Aave symbols", "rows": []}), 400
    try:
        limit = max(2, min(int(request.args.get("limit", "120")), 1000))
        rows = recent_aave_pair_prices(x_symbol, y_symbol, limit)
    except Exception as exc:
        return jsonify({"error": str(exc), "rows": []}), 400
    return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": rows})


@app.get("/api/binance-pair-prices")
def binance_pair_prices():
    x_symbol = request.args.get("x", "").strip().upper()
    y_symbol = request.args.get("y", "").strip().upper()
    if not x_symbol or not y_symbol or x_symbol == y_symbol:
        return jsonify({"error": "select two different symbols", "rows": []}), 400
    try:
        limit = max(2, min(int(request.args.get("limit", "120")), 1000))
        rows = recent_binance_pair_prices(x_symbol, y_symbol, limit)
    except Exception as exc:
        if "does not exist" in str(exc):
            return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": []})
        return jsonify({"error": str(exc), "rows": []}), 400
    return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": rows})


@app.get("/api/pair-route-profits")
def pair_route_profits():
    x_symbol = request.args.get("x", "").strip().upper()
    y_symbol = request.args.get("y", "").strip().upper()
    if not x_symbol or not y_symbol or x_symbol == y_symbol:
        return jsonify({"error": "select two different symbols", "routes": []}), 400
    try:
        initial_amount = max(0.000001, float(request.args.get("initial", "100")))
        rows = latest_candidate_price_rows([x_symbol, y_symbol])
        if x_symbol not in rows or y_symbol not in rows:
            return jsonify(
                {
                    "x_symbol": x_symbol,
                    "y_symbol": y_symbol,
                    "initial_amount": initial_amount,
                    "routes": [],
                    "error": "route profit needs both symbols in binance_candidate_price_history",
                }
            )
        routes = simulate_four_route_cycles(
            rows[x_symbol],
            rows[y_symbol],
            arbitrage_config_from_strategy(),
            initial_amount,
        )
    except Exception as exc:
        if "does not exist" in str(exc):
            return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "initial_amount": 100, "routes": []})
        return jsonify({"error": str(exc), "routes": []}), 400
    return jsonify(
        {
            "x_symbol": x_symbol,
            "y_symbol": y_symbol,
            "initial_amount": initial_amount,
            "prices": {"x": rows[x_symbol], "y": rows[y_symbol]},
            "routes": routes,
        }
    )


@app.get("/api/chart-symbols")
def chart_symbols():
    try:
        limit = max(len(ASSETS), min(int(request.args.get("limit", "500")), 1000))
        symbols = available_candidate_symbols(limit)
    except Exception as exc:
        if "does not exist" not in str(exc):
            return jsonify({"error": str(exc), "symbols": list(ASSETS.keys())}), 400
        symbols = list(ASSETS.keys())
    return jsonify({"symbols": symbols, "aave_symbols": list(ASSETS.keys())})


@app.get("/api/binance-extremes/latest")
def binance_extremes_latest():
    return jsonify({"extremes": safe_latest(latest_binance_extremes)})


@app.get("/api/arbitrage/latest")
def arbitrage_latest():
    return jsonify({"simulation": safe_latest(latest_arbitrage_simulation)})


@app.get("/api/trigger/latest")
def trigger_latest():
    return jsonify({"trigger": safe_latest(latest_arbitrage_simulation)})


@app.get("/api/executable-signal/latest")
def executable_signal_latest():
    return jsonify({"executable_signal": safe_latest(latest_executable_signal)})


@app.get("/api/execution-plan/quote")
def execution_plan_quote():
    try:
        simulation = latest_arbitrage_simulation()
        if not simulation or not simulation.get("execution_plan"):
            return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
        assert_fresh_execution_plan(simulation)
        router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
        last_error = None
        quote = None
        for rpc_url in aave_rpc_urls():
            try:
                quote = quote_execution_plan(
                    simulation["execution_plan"],
                    rpc_url=rpc_url,
                    router_address=router,
                    slippage_bps=read_slippage_bps(),
                )
                break
            except Exception as exc:
                last_error = exc
        if quote is None:
            raise last_error or RuntimeError("all AAVE RPC candidates failed")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"quote": quote})


@app.get("/api/execution-plan/payload")
def execution_plan_payload():
    try:
        simulation = latest_arbitrage_simulation()
        if not simulation or not simulation.get("execution_plan"):
            return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
        assert_fresh_execution_plan(simulation)
        router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
        last_error = None
        quote = None
        for rpc_url in aave_rpc_urls():
            try:
                quote = quote_execution_plan(
                    simulation["execution_plan"],
                    rpc_url=rpc_url,
                    router_address=router,
                    slippage_bps=read_slippage_bps(),
                )
                break
            except Exception as exc:
                last_error = exc
        if quote is None:
            raise last_error or RuntimeError("all AAVE RPC candidates failed")
        payload = build_execution_payload(
            simulation["execution_plan"],
            quote,
            PayloadConfig(
                min_profit_usdc=float(request.args.get("min_profit_usdc", "0")),
                deadline_seconds=int(request.args.get("deadline_seconds", "600")),
            ),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"payload": payload})


@app.get("/api/dex-costs")
def dex_costs():
    symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
    if symbol not in ASSETS:
        return jsonify({"error": f"unsupported symbol: {symbol}"}), 400
    try:
        amounts = parse_trade_usd_amounts(os.getenv("DEX_COST_USD_AMOUNTS"))
        reference_price = latest_reference_price(symbol)
        router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
        last_error = None
        costs = None
        for rpc_url in aave_rpc_urls():
            try:
                costs = [estimate_symbol_cost(rpc_url, symbol, amount, reference_price, router) for amount in amounts]
                break
            except Exception as exc:
                last_error = exc
        if costs is None:
            raise last_error or RuntimeError("all AAVE RPC candidates failed")
        payload = [
            {
                "amount_usd": quote.amount_usd,
                "buy_cost_percent": quote.buy_cost_percent,
                "sell_cost_percent": quote.sell_cost_percent,
                "roundtrip_cost_percent": quote.roundtrip_cost_percent,
                "buy_price_usd": quote.buy_price_usd,
                "sell_price_usd": quote.sell_price_usd,
                "token_amount": quote.token_amount,
            }
            for quote in costs
            if quote is not None
        ]
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"symbol": symbol, "dex_name": "Trader Joe V2", "reference_price_usd": reference_price, "costs": payload})


@app.post("/api/start")
def start():
    global observer_starting, observer_start_error, selected_symbols
    if quick_observer_running():
        set_observer_progress("running", "观察器已在运行", 100)
        set_control_status("success", "启动观察器", "启动观察器已经执行", 100)
        return jsonify({"running": True, "starting": False, "pid": quick_observer_pid(), "symbols": velocity_start_symbols()})
    with observer_start_lock:
        if observer_starting:
            return jsonify({"running": False, "starting": True, "symbols": velocity_start_symbols()}), 202
        try:
            configured_database_url()
        except Exception as exc:
            observer_start_error = str(exc)
            set_observer_progress("error", str(exc), 0)
            set_control_status("error", "启动观察器", f"启动观察器执行失败：{exc}", 0)
            return jsonify({"error": str(exc)}), 400
        _, symbols = build_observer_env()
        observer_starting = True
        observer_start_error = None
        selected_symbols = symbols
        observer_start_progress["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        set_control_status("initializing", "启动观察器", "启动观察器已经执行", 5)
        set_observer_progress("initializing", "已提交启动请求", 5)
    threading.Thread(
        target=start_observer_background,
        name="observer-starter",
        daemon=True,
    ).start()
    return jsonify({"running": False, "starting": True, "symbols": selected_symbols}), 202


@app.post("/api/init")
def init_database():
    set_control_status("initializing", "初始化数据库", "初始化数据库已经执行", 25)
    try:
        ensure_database_schema(configured_database_url())
        counts = database_table_counts()
    except Exception as exc:
        message = database_lock_message("初始化数据库", exc)
        set_control_status("error", "初始化数据库", message, 0)
        return jsonify({"error": message}), 400
    set_control_status("success", "初始化数据库", "初始化数据库已经执行", 100)
    return jsonify({"initialized": True, "rows": observation_count(), "db_counts": counts})


@app.post("/api/stop")
def stop():
    global observer_process, selected_symbols, observer_starting
    with observer_start_lock:
        observer_starting = False
        set_observer_progress("stopped", "已提交停止请求", 0)
    set_control_status("initializing", "停止观察器", "停止观察器已经执行", 25)
    if is_observer_running():
        if observer_process is not None and observer_process.poll() is None:
            observer_process.terminate()
            try:
                observer_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                observer_process.kill()
                observer_process.wait(timeout=3)
        else:
            pid = read_observer_pid()
            if pid is not None:
                os.kill(pid, signal.SIGTERM)
    observer_process = None
    selected_symbols = []
    OBSERVER_PID_PATH.unlink(missing_ok=True)
    set_control_status("success", "停止观察器", "停止观察器已经执行", 100)
    return jsonify({"running": False})


@app.post("/api/clear")
def clear():
    set_control_status("initializing", "清空数据库", "清空数据库已经执行", 25)
    try:
        ensure_database_schema(configured_database_url())
        psycopg = require_psycopg()
        with psycopg.connect(configured_database_url(), connect_timeout=8) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '3s'")
                cursor.execute("SET LOCAL statement_timeout = '10s'")
                cursor.execute(
                    "TRUNCATE TABLE observations, binance_price_history, "
                    "binance_candidate_price_history, binance_pair_price_history, "
                    "binance_window_extremes, arbitrage_simulations RESTART IDENTITY"
                )
    except Exception as exc:
        message = database_lock_message("清空数据库", exc)
        set_control_status("error", "清空数据库", message, 0)
        return jsonify({"error": message}), 400
    set_control_status("success", "清空数据库", "清空数据库已经执行", 100)
    return jsonify({"cleared": True, "rows": 0})


@app.post("/api/clear-files")
def clear_files():
    set_control_status("initializing", "清空文件", "清空文件已经执行", 25)
    deleted, errors = [], []
    for path in [APP_DIR / "observations.csv", LATEST_ARBITRAGE_PATH, LATEST_EXTREMES_PATH, LATEST_EXECUTABLE_SIGNAL_PATH]:
        if path.exists():
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    if errors:
        set_control_status("error", "清空文件", f"清空文件部分失败：{len(errors)} 个错误", 0)
    else:
        set_control_status("success", "清空文件", f"清空文件已经执行，删除 {len(deleted)} 个文件", 100)
    return jsonify({"deleted": deleted, "errors": errors}), 400 if errors else 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
