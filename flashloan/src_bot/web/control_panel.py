import os
import json
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
from eth_account import Account
from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution.dex_costs import estimate_symbol_cost, parse_trade_usd_amounts
from core.env_loader import load_env_files, resolve_env_path
from execution.execution_payload import PayloadConfig, build_execution_payload
from execution.liquidation_scan import (
    LiquidationScanConfig,
    build_user_liquidation_report,
    discover_borrower_addresses,
    health_factor_band,
    load_account_addresses,
    load_reserve_assets_for_scan,
    scan_account_health,
    watched_health_rows,
)
from execution.liquidation_payload import LiquidationExecutionPayloadConfig, build_liquidation_execution_payload
from market.aave_reserve_cache import load_aave_reserve_assets
from market.observer import ASSETS, DEFAULT_BINANCE_REST_BASES, env_urls, resolve_aave_binance_overlap_symbols
from execution.plan_quotes import quote_execution_plan
from strategy.arbitrage import ArbitrageConfig, simulate_four_route_cycles
from web.control_panel_config import (
    strategy_config as read_strategy_config,
    unified_sampling_profile,
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
from web.control_panel_stats import testnet_trade_stats as read_testnet_trade_stats, trade_stats as read_trade_stats
from web.control_panel_liquidation_base import *
from web.control_panel_liquidation_audit import *
from web.control_panel_liquidation_scan import *
from web.control_panel_liquidation_execute import *
from web.control_panel_market import *
from web.control_panel_liquidation_context import install_liquidation_context
from web.control_panel_liquidation_routes import register_liquidation_routes
from web.control_panel_control_routes import register_control_routes
from web.control_panel_data_routes import register_data_routes
from web.control_panel_page_routes import register_page_routes
from db.storage import (
    EXPECTED_SCHEMA_MIGRATION_IDS,
    ensure_database_schema,
    load_schema_migrations,
    load_liquidation_accounts as db_load_liquidation_accounts,
    liquidation_account_registry_stats as db_liquidation_account_registry_stats,
    liquidation_discovery_scan_progress as db_liquidation_discovery_scan_progress,
    prune_liquidation_accounts as db_prune_liquidation_accounts,
    record_liquidation_discovery_scan as db_record_liquidation_discovery_scan,
    record_liquidation_account_scan,
    require_psycopg,
    upsert_liquidation_accounts as db_upsert_liquidation_accounts,
)


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
LIQUIDATION_TEMPLATE_PATH = WEB_DIR / "templates" / "liquidation_panel.html"
EXCHANGE_MATRIX_TEMPLATE_PATH = WEB_DIR / "templates" / "exchange_matrix.html"
LATEST_ARBITRAGE_PATH = STATE_DIR / "latest_arbitrage.json"
LATEST_EXECUTABLE_SIGNAL_PATH = STATE_DIR / "latest_executable_signal.json"
LATEST_EXTREMES_PATH = STATE_DIR / "latest_extremes.json"
AAVE_RESERVE_CACHE_PATH = CACHE_DIR / "aave_reserve_assets.json"
DEX_BORROW_TARGET_CACHE_PATH = CACHE_DIR / "dex_borrow_targets.json"
LIQUIDATION_SAMPLE_LIBRARY_PATH = RUNTIME_DIR / "samples" / "liquidation_candidates" / "index.json"
LIQUIDATION_ACCOUNTS_PATH = resolve_env_path("LIQUIDATION_ACCOUNTS_FILE", "runtime/cache/liquidation_accounts.txt", APP_DIR)
OBSERVER_PID_PATH = RUNTIME_DIR / "observer.pid"
STRATEGY_CONFIG_PATH = CONFIG_DIR / "strategy_config.json"
LIQUIDATION_CONFIG_PATH = CONFIG_DIR / "liquidation_config.json"
REPO_ROOT = APP_DIR.parents[1]

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


def velocity_start_symbols() -> list[str]:
    return selected_symbols or [f"velocity_top_{VELOCITY_SIDE_LIMIT}", f"velocity_bottom_{VELOCITY_SIDE_LIMIT}"]


def restrict_extremes_to_symbols(extremes: Optional[dict], symbols: list[str]) -> Optional[dict]:
    if not extremes or not symbols:
        return extremes
    allowed = {symbol for symbol in symbols if isinstance(symbol, str) and symbol.endswith("USDT")}
    basket = extremes.get("basket")
    if not allowed or not isinstance(basket, list):
        return extremes
    filtered_basket = [
        row
        for row in basket
        if isinstance(row, dict) and str(row.get("symbol", "")).upper() in allowed
    ]
    filtered = dict(extremes)
    filtered["basket"] = filtered_basket
    filtered["observation_universe_size"] = len(allowed)
    filtered["sample_count"] = sum(1 for row in filtered_basket if row.get("current_price") is not None)
    filtered["gainer_count"] = sum(
        1
        for row in filtered_basket
        if row.get("window_ready") and float(row.get("change_percent") or 0.0) > 0
    )
    filtered["loser_count"] = sum(
        1
        for row in filtered_basket
        if row.get("window_ready") and float(row.get("change_percent") or 0.0) < 0
    )
    filtered["market_divergence_index"] = (
        filtered["gainer_count"] * filtered["loser_count"] / len(allowed)
    ) if allowed else 0.0
    return filtered


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
    reserve_assets = load_aave_reserve_assets(
        rpc_urls,
        pool_address,
        limit=reserve_limit,
        exclude_stables=False,
    ) if pool_address else []
    reserve_symbols = list(
        dict.fromkeys(
            str(asset.get("binance_symbol", "")).upper()
            for asset in reserve_assets
            if asset.get("binance_symbol")
        )
    )
    tracked_symbols = [*reserve_symbols, "USDCUSDT"]
    env["SYMBOLS"] = ",".join(tracked_symbols if reserve_symbols else ASSETS.keys())
    env["BINANCE_SYMBOL_SELECTION"] = "aave_binance_overlap"
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
    env["BINANCE_SYMBOL_SELECTION"] = "aave_binance_overlap"
    env["BINANCE_TOP_SYMBOL_LIMIT"] = "0"
    try:
        rest_bases = env_urls("BINANCE_REST_BASES", DEFAULT_BINANCE_REST_BASES, "https://")
        display_symbols = resolve_aave_binance_overlap_symbols(rest_bases, int(env["BINANCE_TOP_SYMBOL_LIMIT"]))
    except Exception:
        display_symbols = []
    fallback_symbols = tracked_symbols if reserve_symbols else list(ASSETS.keys())
    return env, display_symbols or fallback_symbols


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


def render_control_panel() -> str:
    chart_options = "\n".join(f'<option value="{escape(symbol)}">{escape(symbol)}</option>' for symbol in ASSETS)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__CHART_OPTIONS__", chart_options)


install_liquidation_context(sys.modules[__name__])
register_page_routes(app, sys.modules[__name__])
register_liquidation_routes(app, sys.modules[__name__])
register_data_routes(app, sys.modules[__name__])
register_control_routes(app, sys.modules[__name__])


if __name__ == "__main__":
    initialize_liquidation_runtime()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
