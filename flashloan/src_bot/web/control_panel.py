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
    load_liquidation_borrow_health_scan_batches as db_load_liquidation_borrow_health_scan_batches,
    load_schema_migrations,
    load_liquidation_accounts as db_load_liquidation_accounts,
    load_liquidation_borrow_health_pool as db_load_liquidation_borrow_health_pool,
    load_liquidation_core_opportunity_pool as db_load_liquidation_core_opportunity_pool,
    load_liquidation_high_frequency_pool as db_load_liquidation_high_frequency_pool,
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    liquidation_account_registry_stats as db_liquidation_account_registry_stats,
    liquidation_discovery_scan_progress as db_liquidation_discovery_scan_progress,
    prune_liquidation_accounts as db_prune_liquidation_accounts,
    record_liquidation_discovery_scan as db_record_liquidation_discovery_scan,
    record_liquidation_account_scan,
    record_liquidation_borrow_health_scan_batch as db_record_liquidation_borrow_health_scan_batch,
    require_psycopg,
    sync_liquidation_borrow_health_pool as db_sync_liquidation_borrow_health_pool,
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
LIQUIDATION_ACCOUNT_TEMPLATE_PATH = WEB_DIR / "templates" / "liquidation_account.html"
EXCHANGE_MATRIX_TEMPLATE_PATH = WEB_DIR / "templates" / "exchange_matrix.html"
OPPORTUNITY_HEALTH_TEMPLATE_PATH = WEB_DIR / "templates" / "opportunity_health.html"
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
observer_supervisor_thread: Optional[threading.Thread] = None
observer_supervisor_stop = threading.Event()
observer_supervisor_lock = threading.Lock()
observer_supervisor_state = {
    "enabled": False,
    "heartbeat_at": 0.0,
    "restart_count": 0,
    "last_restart_at": 0.0,
    "next_restart_at": 0.0,
    "last_exit_code": None,
    "last_error": None,
}
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
OBSERVER_START_TIMEOUT_SECONDS = int(os.getenv("OBSERVER_START_TIMEOUT_SECONDS", "60"))
OBSERVER_SUPERVISOR_INTERVAL_SECONDS = float(os.getenv("OBSERVER_SUPERVISOR_INTERVAL_SECONDS", "5"))
OBSERVER_RESTART_BASE_DELAY_SECONDS = float(os.getenv("OBSERVER_RESTART_BASE_DELAY_SECONDS", "1"))
OBSERVER_RESTART_MAX_DELAY_SECONDS = float(os.getenv("OBSERVER_RESTART_MAX_DELAY_SECONDS", "30"))

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
            "state": state, "stage": stage,
            "percent": max(0, min(int(percent), 100)),
            "started_at": observer_start_progress.get("started_at"),
        }
    )


def observer_start_elapsed_seconds() -> Optional[float]:
    started_at = observer_start_progress.get("started_at")
    if not started_at:
        return None
    try:
        parsed = datetime.fromisoformat(str(started_at))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def clear_stale_observer_start() -> bool:
    global observer_starting, observer_start_error
    elapsed = observer_start_elapsed_seconds()
    if not observer_starting or elapsed is None or elapsed <= OBSERVER_START_TIMEOUT_SECONDS:
        return False
    observer_starting = False
    if not quick_observer_running():
        observer_start_error = (
            f"机会观察启动超过 {OBSERVER_START_TIMEOUT_SECONDS} 秒仍未完成；请检查 logs/observer_stderr.log 后重试。"
        )
        set_observer_progress("error", observer_start_error, 0)
        set_control_status("error", "启动机会观察", observer_start_error, 0)
    return True


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
            f"{action}执行失败：数据库正在被其他会话锁定。"
            "这可能是远程应用、数据库控制台查询窗口，也可能是本应用启动后的后台初始化、账户发现、健康扫描或机会观察写入。"
            "请先停止机会观察，等待后台扫描结束，关闭数据库控制台查询窗口后重试。"
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


def _liquidation_activity_payload(label: str, cache: dict, lock: threading.Lock) -> dict:
    running = bool(cache.get("running"))
    if running and hasattr(lock, "locked") and not lock.locked():
        cache["running"] = False
        cache["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cache["stage"] = "idle"
        running = False
    stage = str(cache.get("stage") or "idle")
    return {
        "label": label,
        "running": running,
        "stage": stage,
        "started_at": cache.get("started_at"),
        "finished_at": cache.get("finished_at"),
        "text": f"{label}：{stage}" if running else f"{label}：空闲",
    }


def background_activity_payload(running: Optional[bool] = None, starting: Optional[bool] = None) -> dict:
    observer_running = quick_observer_running() if running is None else bool(running)
    observer_starting_current = bool(globals().get("observer_starting")) if starting is None else bool(starting)
    discovery = _liquidation_activity_payload("账户池发现扫描", LIQUIDATION_DISCOVERY_CACHE, LIQUIDATION_DISCOVERY_LOCK)
    health_scan = _liquidation_activity_payload("债务/健康池扫描", LIQUIDATION_SCAN_CACHE, LIQUIDATION_SCAN_LOCK)
    account_backfill = _liquidation_activity_payload("账户池一年查漏补缺", LIQUIDATION_ACCOUNT_BACKFILL_CACHE, LIQUIDATION_ACCOUNT_BACKFILL_LOCK)
    tasks: list[dict] = []
    if observer_starting_current:
        tasks.append({"label": "机会观察启动", "running": True, "stage": "starting", "text": "机会观察：启动中"})
    if observer_running:
        tasks.append({"label": "机会观察", "running": True, "stage": "running", "text": "机会观察：运行中"})
    for item in (discovery, health_scan, account_backfill):
        if item["running"]:
            tasks.append(item)
    return {
        "active": bool(tasks),
        "observer_running": observer_running,
        "observer_starting": observer_starting_current,
        "liquidation_discovery": discovery,
        "liquidation_health_scan": health_scan,
        "liquidation_account_backfill": account_backfill,
        "tasks": tasks,
        "summary": "；".join(str(item.get("text") or item.get("label")) for item in tasks),
    }


def observer_progress_payload(running: bool, starting: bool, latest_extremes: Optional[dict]) -> dict:
    progress = dict(observer_start_progress)
    elapsed = observer_start_elapsed_seconds()
    if observer_start_error:
        progress.update({"state": "error", "stage": observer_start_error, "percent": 0})
    elif starting:
        if elapsed is not None and elapsed > OBSERVER_START_TIMEOUT_SECONDS:
            progress.update({"state": "error", "stage": f"启动超过 {OBSERVER_START_TIMEOUT_SECONDS} 秒，请检查观察器日志或停止后重试", "percent": 0})
        else:
            progress["stage"] = progress.get("stage") or "正在启动机会观察进程"
            progress["percent"] = max(progress.get("percent", 0), 5)
            progress["state"] = "initializing"
    elif running and latest_extremes:
        progress.update({"state": "running", "stage": "机会观察运行中：正在采集市场窗口", "percent": 100})
    elif running:
        stage = "观察进程已启动，等待第一个市场窗口"
        if elapsed is not None and elapsed > OBSERVER_START_TIMEOUT_SECONDS:
            stage = f"观察进程已启动，但 {OBSERVER_START_TIMEOUT_SECONDS} 秒内未产出市场窗口；通常是外部行情源、数据库写入或网络连接阻塞"
        progress.update({"state": "initializing", "stage": stage, "percent": max(progress.get("percent", 0), 85)})
    else:
        progress.update({"state": "stopped", "stage": "机会观察未运行", "percent": 0})
    if elapsed is not None:
        progress["elapsed_seconds"] = round(elapsed, 1)
    return progress


def system_monitor_payload(
    running: bool,
    starting: bool,
    latest_extremes: Optional[dict],
    control_status_current: Optional[dict],
    reserve_cache: Optional[dict],
    background_activity: Optional[dict] = None,
) -> dict:
    observer = observer_progress_payload(running, starting, latest_extremes)
    background = background_activity or background_activity_payload(running, starting)
    liquidation_busy = any(
        bool((background.get(key) or {}).get("running"))
        for key in ("liquidation_discovery", "liquidation_health_scan", "liquidation_account_backfill")
    )
    control_state = (control_status_current or {}).get("state")
    control_stage = (control_status_current or {}).get("stage")
    stale_start_status = (
        control_state == "initializing"
        and control_stage == "启动机会观察"
        and not starting
        and not running
    )
    if liquidation_busy:
        action = f"后台清算扫描中：{background.get('summary') or '等待扫描完成'}"
    elif stale_start_status:
        action = "机会观察未运行；最近一次启动未保持运行，请检查 runtime/logs/observer_stderr.log"
    else:
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
    if stale_start_status:
        state = "stopped"
    elif control_state == "error" or observer.get("state") == "error":
        state = "error"
    elif liquidation_busy:
        state = "initializing"
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
        "background_activity": background,
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


def observer_supervisor_delay(restart_count: int) -> float:
    base = max(0.1, float(OBSERVER_RESTART_BASE_DELAY_SECONDS))
    cap = max(base, float(OBSERVER_RESTART_MAX_DELAY_SECONDS))
    return min(cap, base * (2 ** max(0, int(restart_count) - 1)))


def observer_supervisor_payload() -> dict:
    with observer_supervisor_lock:
        state = dict(observer_supervisor_state)
    heartbeat_at = float(state.get("heartbeat_at") or 0.0)
    return {
        **state,
        "healthy": bool(state.get("enabled")) and quick_observer_running(),
        "heartbeat_age_seconds": round(time.monotonic() - heartbeat_at, 1) if heartbeat_at else None,
    }


def launch_observer_process(env: dict, symbols: list[str]) -> subprocess.Popen:
    global observer_process, selected_symbols, observer_start_error
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout = open(LOG_DIR / "observer_stdout.log", "ab", buffering=0)
    stderr = open(LOG_DIR / "observer_stderr.log", "ab", buffering=0)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(APP_DIR) if not existing_pythonpath else f"{APP_DIR}{os.pathsep}{existing_pythonpath}"
    process = subprocess.Popen(
        [sys.executable, "-m", "market.observer"],
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
    return process


def start_observer_supervisor() -> None:
    global observer_supervisor_thread
    with observer_supervisor_lock:
        observer_supervisor_state["enabled"] = True
        observer_supervisor_state["heartbeat_at"] = time.monotonic()
    observer_supervisor_stop.clear()
    if observer_supervisor_thread is not None and observer_supervisor_thread.is_alive():
        return
    observer_supervisor_thread = threading.Thread(
        target=observer_supervisor_loop,
        name="observer-supervisor",
        daemon=True,
    )
    observer_supervisor_thread.start()


def stop_observer_supervisor() -> None:
    observer_supervisor_stop.set()
    with observer_supervisor_lock:
        observer_supervisor_state["enabled"] = False
        observer_supervisor_state["next_restart_at"] = 0.0


def observer_supervisor_once(now: float | None = None) -> dict:
    now = time.monotonic() if now is None else float(now)
    with observer_supervisor_lock:
        enabled = bool(observer_supervisor_state.get("enabled"))
    if not enabled:
        return {"action": "disabled"}
    if observer_starting:
        with observer_supervisor_lock:
            observer_supervisor_state["heartbeat_at"] = now
        return {"action": "starting"}
    if quick_observer_running():
        with observer_supervisor_lock:
            observer_supervisor_state["heartbeat_at"] = now
            observer_supervisor_state["last_error"] = None
        return {"action": "healthy"}

    exit_code = observer_process.poll() if observer_process is not None else None
    with observer_supervisor_lock:
        restart_count = int(observer_supervisor_state.get("restart_count") or 0) + 1
        next_restart_at = float(observer_supervisor_state.get("next_restart_at") or 0.0)
        if next_restart_at and now < next_restart_at:
            return {"action": "backoff", "next_restart_at": next_restart_at}
        observer_supervisor_state["restart_count"] = restart_count
        observer_supervisor_state["last_exit_code"] = exit_code
        observer_supervisor_state["last_error"] = f"observer exited code={exit_code}"

    delay = observer_supervisor_delay(restart_count)
    try:
        env, symbols = build_observer_env()
        process = launch_observer_process(env, symbols)
        with observer_supervisor_lock:
            observer_supervisor_state["heartbeat_at"] = now
            observer_supervisor_state["last_restart_at"] = now
            observer_supervisor_state["next_restart_at"] = now + delay
            observer_supervisor_state["last_error"] = None
        set_observer_progress("initializing", f"机会观察已自动重启 pid={process.pid}", 85)
        set_control_status("initializing", "自动恢复机会观察", f"观察进程崩溃后已自动重启，pid={process.pid}", 85, ttl_seconds=60)
        return {"action": "restarted", "pid": process.pid, "delay": delay}
    except Exception as exc:
        with observer_supervisor_lock:
            observer_supervisor_state["next_restart_at"] = now + delay
            observer_supervisor_state["last_error"] = str(exc)
        set_observer_progress("error", f"机会观察自动重启失败：{exc}", 0)
        return {"action": "restart_failed", "error": str(exc), "delay": delay}


def observer_supervisor_loop() -> None:
    while not observer_supervisor_stop.wait(max(0.5, float(OBSERVER_SUPERVISOR_INTERVAL_SECONDS))):
        observer_supervisor_once()


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
            set_observer_progress("running", "机会观察已在运行", 100)
            return
        set_observer_progress("initializing", "检查数据库配置", 10)
        configured_database_url()
        with observer_start_lock:
            if not observer_starting:
                return
        set_observer_progress("initializing", "加载 Aave 储备与市场交集", 25)
        env, symbols = build_observer_env()
        set_observer_progress("initializing", "准备机会观察进程", 45)
        set_observer_progress("initializing", "启动机会观察进程", 60)
        process = launch_observer_process(env, symbols)
        time.sleep(0.5)
        if process.poll() is not None:
            OBSERVER_PID_PATH.unlink(missing_ok=True)
            message = f"机会观察进程启动后立即退出，退出码 {process.returncode}；请检查 runtime/logs/observer_stderr.log"
            with observer_start_lock:
                observer_process = None
                observer_start_error = message
                selected_symbols = []
            set_observer_progress("error", message, 0)
            set_control_status("error", "启动机会观察", message, 0)
            return
        start_observer_supervisor()
        set_control_status("initializing", "启动机会观察", "观察进程已启动，等待第一个市场窗口", 80, ttl_seconds=OBSERVER_START_TIMEOUT_SECONDS + 30)
        set_observer_progress("initializing", "等待第一个市场窗口", 80)
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
