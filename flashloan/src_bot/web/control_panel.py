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

from eth_account import Account
from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.config_schema import parse_env_float, parse_env_int
from execution.dex_costs import estimate_symbol_cost, parse_trade_usd_amounts
from core.env_loader import load_env_files, resolve_env_path
from core.sensitive_data import redact_sensitive_text
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
import web.control_panel_liquidation_base as liquidation_base
import web.control_panel_liquidation_audit as liquidation_audit
import web.control_panel_liquidation_scan as liquidation_scan_module
import web.control_panel_liquidation_execute as liquidation_execute
import web.control_panel_market as market_panel
from web.control_panel_app import create_control_panel_app
from web.observer_runtime_service import ObserverRuntimeService

for _module in (
    liquidation_base,
    liquidation_audit,
    liquidation_scan_module,
    liquidation_execute,
    market_panel,
):
    globals().update({name: value for name, value in vars(_module).items() if not name.startswith("_")})
from db.storage_common import EXPECTED_SCHEMA_MIGRATION_IDS, require_psycopg
from db.storage_liquidation import (
    load_liquidation_accounts as db_load_liquidation_accounts,
    load_liquidation_accounts_page as db_load_liquidation_accounts_page,
    load_latest_liquidation_account_reports as db_load_latest_liquidation_account_reports,
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    liquidation_account_registry_stats as db_liquidation_account_registry_stats,
    liquidation_discovery_scan_progress as db_liquidation_discovery_scan_progress,
    prune_liquidation_accounts as db_prune_liquidation_accounts,
    record_liquidation_account_scan,
    record_liquidation_discovery_scan as db_record_liquidation_discovery_scan,
    upsert_liquidation_accounts as db_upsert_liquidation_accounts,
)
from db.storage_liquidation_pool import (
    load_liquidation_accounts_for_assets as db_load_liquidation_accounts_for_assets,
    load_liquidation_borrow_health_scan_batches as db_load_liquidation_borrow_health_scan_batches,
    load_liquidation_borrow_health_pool as db_load_liquidation_borrow_health_pool,
    load_liquidation_core_opportunity_pool as db_load_liquidation_core_opportunity_pool,
    load_liquidation_high_frequency_pool as db_load_liquidation_high_frequency_pool,
    record_liquidation_borrow_health_scan_batch as db_record_liquidation_borrow_health_scan_batch,
    sync_liquidation_borrow_health_pool as db_sync_liquidation_borrow_health_pool,
)
from db.storage_schema import ensure_database_schema, load_schema_migrations
from runtime.cow_arbitrage_daemon import cow_quote_daemon_enabled, ensure_cow_quote_daemon_running
from runtime.liquidation_daemon import market_status_payload, read_daemon_status
from runtime.liquidation_engine import LiquidationEngine, LiquidationEngineConfig, LiquidationEngineDependencies
from runtime.liquidation_market_bridge import (
    asset_variants_for_market_symbols,
    binance_symbols_for_liquidation_assets,
    liquidation_asset_ids_from_pool_rows,
    price_snapshot_from_extremes,
)
from runtime.liquidation_price_trigger import accounts_triggered_by_prices

_BASE_ENSURE_DATABASE_SCHEMA = ensure_database_schema


def ensure_database_schema_cached(database_url: str, *, ttl_seconds: float = 300.0, force: bool = False) -> None:
    if ensure_database_schema is not _BASE_ENSURE_DATABASE_SCHEMA:
        ensure_database_schema(database_url)
        return
    liquidation_base.ensure_database_schema_cached(database_url, ttl_seconds=ttl_seconds, force=force)


WEB_DIR = Path(__file__).resolve().parent
APP_DIR = SRC_ROOT
load_env_files(__file__, override=False)

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
BINANCE_MARKET_TEMPLATE_PATH = WEB_DIR / "templates" / "binance_market.html"
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
observer_runtime_service = ObserverRuntimeService()
observer_supervisor_state = observer_runtime_service.supervisor_state
liquidation_engine_instance: Optional[LiquidationEngine] = None
liquidation_engine_thread: Optional[threading.Thread] = None
liquidation_engine_lock = threading.Lock()
_scan_initialize_liquidation_runtime = liquidation_scan_module.initialize_liquidation_runtime
observer_start_progress = observer_runtime_service.observer_start_progress
control_status = observer_runtime_service.control_status

VELOCITY_SIDE_LIMIT = 100
SUMMARY_SIDE_LIMIT = 5
SUMMARY_INITIAL_AMOUNT = 100.0
AAVE_RESERVE_SYMBOL_LIMIT = 1000
OBSERVER_START_TIMEOUT_SECONDS = parse_env_int("OBSERVER_START_TIMEOUT_SECONDS", 60, minimum=1)[0]
OBSERVER_SUPERVISOR_INTERVAL_SECONDS = parse_env_float("OBSERVER_SUPERVISOR_INTERVAL_SECONDS", 5, minimum=0.5)[0]
OBSERVER_RESTART_BASE_DELAY_SECONDS = parse_env_float("OBSERVER_RESTART_BASE_DELAY_SECONDS", 1, minimum=0.1)[0]
OBSERVER_RESTART_MAX_DELAY_SECONDS = parse_env_float("OBSERVER_RESTART_MAX_DELAY_SECONDS", 30, minimum=0.1)[0]

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
            command_line = result.stdout.replace("\\", "/")
            return "observer.py" in command_line or "market.observer" in command_line
        except Exception:
            return False
    try:
        command_line = Path(f"/proc/{int(pid)}/cmdline").read_text(encoding="utf-8", errors="ignore")
        return "observer.py" in command_line or "market.observer" in command_line
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
            "Where-Object { $_.CommandLine -like '*observer.py*' -or $_.CommandLine -like '*market.observer*' } | "
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
    return read_observer_pid()


def quick_observer_running() -> bool:
    return quick_observer_pid() is not None


def set_observer_progress(state: str, stage: str, percent: int) -> None:
    observer_runtime_service.set_observer_progress(state, stage, percent)


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
    observer_runtime_service.set_control_status(state, stage, message, percent, ttl_seconds=ttl_seconds)


def database_lock_message(action: str, exc: Exception) -> str:
    detail = redact_sensitive_text(exc)
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


LIQUIDATION_STAGE_LABELS = {
    "idle": "空闲",
    "window": "计算扫描窗口",
    "resolving-blocks": "解析区块范围",
    "borrowers": "发现借款账户",
    "saving": "保存账户",
    "debt_pool": "扫描债务池",
    "health": "扫描健康度",
    "starting": "启动中",
    "running": "运行中",
}


def _iso_elapsed_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        started_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())


def _liquidation_activity_percent(stage: str, progress: dict) -> int:
    if {"from_block", "to_block", "current_to_block"}.issubset(progress):
        from_block = int(progress.get("from_block") or 0)
        to_block = int(progress.get("to_block") or 0)
        current_to = int(progress.get("current_to_block") or from_block)
        total = max(1, to_block - from_block + 1)
        scanned = max(0, min(total, current_to - from_block + 1))
        return max(5, min(99, int(scanned / total * 100)))
    if "scanned_count" in progress or "account_count" in progress:
        scanned = int(progress.get("scanned_count") or 0)
        total = int(progress.get("account_count") or 0)
        if total:
            return max(5, min(99, int(scanned / total * 100)))
        return 15 if stage in {"debt_pool", "health"} else 10
    return {
        "window": 8,
        "resolving-blocks": 12,
        "borrowers": 20,
        "saving": 90,
        "debt_pool": 15,
        "health": 15,
    }.get(stage, 5)


def _liquidation_activity_payload(label: str, cache: dict, lock: threading.Lock) -> dict:
    running = bool(cache.get("running"))
    if running and hasattr(lock, "locked") and not lock.locked():
        cache["running"] = False
        cache["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cache["stage"] = "idle"
        running = False
    stage = str(cache.get("stage") or "idle")
    stage_label = LIQUIDATION_STAGE_LABELS.get(stage, stage)
    progress = dict(cache.get("progress") or {})
    last_result = dict(cache.get("last_result") or {})
    detail = _liquidation_activity_detail(stage, progress, last_result)
    elapsed_seconds = _iso_elapsed_seconds(cache.get("started_at")) if running else None
    percent = _liquidation_activity_percent(stage, progress) if running else 0
    text = f"{label}：{stage_label}"
    if detail:
        text = f"{text}（{detail}）"
    feedback = text if running else f"{label}：空闲"
    if running and elapsed_seconds is not None:
        feedback = f"{feedback}，已用 {elapsed_seconds:.1f}s"
    return {
        "label": label,
        "running": running,
        "stage": stage,
        "stage_label": stage_label,
        "progress": progress,
        "last_result": last_result,
        "detail": detail,
        "feedback": feedback,
        "percent": percent,
        "elapsed_seconds": elapsed_seconds,
        "started_at": cache.get("started_at"),
        "finished_at": cache.get("finished_at"),
        "text": text if running else f"{label}：空闲",
    }


def _liquidation_activity_detail(stage: str, progress: dict, last_result: dict) -> str:
    if progress:
        if {"from_block", "to_block", "current_to_block"}.issubset(progress):
            from_block = int(progress.get("from_block") or 0)
            to_block = int(progress.get("to_block") or 0)
            current_to = int(progress.get("current_to_block") or from_block)
            total = max(1, to_block - from_block + 1)
            scanned = max(0, min(total, current_to - from_block + 1))
            percent = scanned / total * 100.0
            found = int(progress.get("discovered_count") or 0)
            return f"区块 {current_to}/{to_block}，{percent:.1f}%，发现 {found} 个账户"
        if "scanned_count" in progress or "account_count" in progress:
            scanned = int(progress.get("scanned_count") or 0)
            total = int(progress.get("account_count") or 0)
            if total:
                return f"账户 {scanned}/{total}"
            return f"已扫 {scanned} 个账户"
    error = redact_sensitive_text(last_result.get("error") or "")
    if stage == "borrowers":
        return "正在建立账户池，等待首次发现结果"
    if error == "database liquidation account table is empty":
        return "账户库为空，等待发现扫描写入账户"
    if error == "liquidation account registry is empty":
        return "账户池为空，等待发现扫描写入账户"
    if error:
        return error
    count = last_result.get("count")
    if count is not None and stage != "idle":
        return f"已发现 {int(count or 0)} 个账户"
    return ""


def background_activity_payload(running: Optional[bool] = None, starting: Optional[bool] = None) -> dict:
    observer_running = quick_observer_running() if running is None else bool(running)
    observer_starting_current = bool(globals().get("observer_starting")) if starting is None else bool(starting)
    daemon_status = liquidation_daemon_status_payload()
    discovery = _liquidation_activity_payload("账户池发现扫描", LIQUIDATION_DISCOVERY_CACHE, LIQUIDATION_DISCOVERY_LOCK)
    health_scan = _liquidation_activity_payload("债务/健康池扫描", LIQUIDATION_SCAN_CACHE, LIQUIDATION_SCAN_LOCK)
    account_backfill = _liquidation_activity_payload("账户池一年查漏补缺", LIQUIDATION_ACCOUNT_BACKFILL_CACHE, LIQUIDATION_ACCOUNT_BACKFILL_LOCK)
    tasks: list[dict] = []
    if observer_starting_current:
        tasks.append({"label": "机会观察启动", "running": True, "stage": "starting", "stage_label": "启动中", "text": "机会观察：启动中", "feedback": "机会观察：启动中", "percent": 10})
    if observer_running:
        tasks.append({"label": "机会观察", "running": True, "stage": "running", "stage_label": "运行中", "text": "机会观察：运行中", "feedback": "机会观察：运行中", "percent": 100})
    if daemon_status.get("state") in {"starting", "running", "degraded"}:
        market = daemon_status.get("market") if isinstance(daemon_status.get("market"), dict) else {}
        subscribed = market.get("subscribed_symbols") or []
        tasks.append(
            {
                "label": "清算守护进程",
                "running": daemon_status.get("state") in {"running", "degraded"},
                "stage": daemon_status.get("state"),
                "stage_label": daemon_status.get("state"),
                "text": f"清算守护进程：{daemon_status.get('state')} | Binance {len(subscribed)}",
                "feedback": f"清算守护进程：{daemon_status.get('state')} | Binance {len(subscribed)}",
                "percent": 100 if daemon_status.get("state") == "running" else 60,
            }
        )
    for item in (discovery, health_scan, account_backfill):
        if item["running"]:
            tasks.append(item)
    return {
        "active": bool(tasks),
        "observer_running": observer_running,
        "observer_starting": observer_starting_current,
        "observer_supervisor": observer_supervisor_payload(),
        "liquidation_daemon": daemon_status,
        "liquidation_discovery": discovery,
        "liquidation_health_scan": health_scan,
        "liquidation_account_backfill": account_backfill,
        "tasks": tasks,
        "summary": "；".join(str(item.get("feedback") or item.get("text") or item.get("label")) for item in tasks),
    }


def liquidation_daemon_status_payload() -> dict:
    daemon_status = read_daemon_status()
    if daemon_status.get("state") not in {"", None, "stale"}:
        return daemon_status
    engine = liquidation_engine_instance
    observer = observer_supervisor_payload()
    quick_running = quick_observer_running()
    if quick_running:
        fallback_symbols = ",".join(displayed_symbols(True) or velocity_start_symbols())
        observer = {
            **observer,
            "enabled": True,
            "healthy": True,
            "state": "running",
            "pid": quick_observer_pid(),
            "env_symbols": observer.get("env_symbols") or fallback_symbols,
            "display_symbols": observer.get("display_symbols") or fallback_symbols,
        }
    market_snapshot = liquidation_market_price_snapshot()
    market = market_status_payload(
        observer.get("env_symbols"),
        observer.get("display_symbols"),
        market_snapshot,
    )
    engine_payload = {
        "started": bool(engine),
        "mode": engine.config.mode if engine else None,
        "auto_execute": bool(engine and engine.config.auto_execute),
        "manual_test_completed": bool(engine and engine.config.manual_test_completed),
        "last_market_snapshot": market_snapshot,
    }
    observer_healthy = bool(observer.get("healthy"))
    market_healthy = bool(market.get("fresh")) or not bool(market.get("subscribed_symbols"))
    state = "running" if engine and observer_healthy and market_healthy else "degraded" if engine else "stopped"
    return {
        "state": state,
        "source": "ui_runtime",
        "daemon_file_state": daemon_status,
        "pid": os.getpid(),
        "observer": observer,
        "engine": engine_payload,
        "market": market,
        "stale": False,
        "running": state in {"running", "degraded"},
        "last_error": None,
        "updated_at": time.time(),
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
    liquidation_tasks = [
        item
        for item in (background.get("tasks") or [])
        if str(item.get("label") or "").startswith(("账户池", "债务/健康"))
    ]
    primary_liquidation_task = liquidation_tasks[0] if liquidation_tasks else {}
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
    percent = int((control_status_current or {}).get("percent") or observer.get("percent") or 0)
    if liquidation_busy:
        percent = max(percent, int(primary_liquidation_task.get("percent") or 5))
    return {
        "state": state,
        "action": action,
        "observer_stage": observer_stage,
        "background_stage": primary_liquidation_task.get("stage_label") or primary_liquidation_task.get("stage"),
        "background_detail": primary_liquidation_task.get("detail") or "",
        "background_elapsed_seconds": primary_liquidation_task.get("elapsed_seconds"),
        "symbol_count": len(symbols),
        "aave_reserve_count": reserve_count,
        "window_seconds": window_seconds,
        "sample_count": sample_count,
        "observed_at": observed_at,
        "percent": percent,
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
    payload = observer_runtime_service.supervisor_payload()
    payload["healthy"] = bool(payload.get("enabled")) and quick_observer_running()
    return payload


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
    observer_runtime_service.mark_supervisor_heartbeat()
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
    observer_runtime_service.reset_runtime_state()


def observer_supervisor_once(now: float | None = None) -> dict:
    now = time.monotonic() if now is None else float(now)
    with observer_supervisor_lock:
        enabled = bool(observer_runtime_service.supervisor_state.get("enabled"))
    if not enabled:
        return {"action": "disabled"}
    if observer_starting:
        observer_runtime_service.mark_supervisor_heartbeat(now)
        return {"action": "starting"}
    if quick_observer_running():
        observer_runtime_service.mark_supervisor_heartbeat(now)
        observer_runtime_service.update_supervisor_state(last_error=None)
        return {"action": "healthy"}

    exit_code = observer_process.poll() if observer_process is not None else None
    with observer_supervisor_lock:
        restart_count = int(observer_runtime_service.supervisor_state.get("restart_count") or 0) + 1
        next_restart_at = float(observer_runtime_service.supervisor_state.get("next_restart_at") or 0.0)
        if next_restart_at and now < next_restart_at:
            return {"action": "backoff", "next_restart_at": next_restart_at}
        observer_runtime_service.update_supervisor_state(
            restart_count=restart_count,
            last_exit_code=exit_code,
            last_error=f"observer exited code={exit_code}",
        )

    delay = observer_supervisor_delay(restart_count)
    try:
        env, symbols = build_observer_env()
        process = launch_observer_process(env, symbols)
        observer_runtime_service.mark_supervisor_heartbeat(now)
        observer_runtime_service.update_supervisor_state(
            last_restart_at=now,
            next_restart_at=now + delay,
            last_error=None,
        )
        set_observer_progress("initializing", f"机会观察已自动重启 pid={process.pid}", 85)
        set_control_status("initializing", "自动恢复机会观察", f"观察进程崩溃后已自动重启，pid={process.pid}", 85, ttl_seconds=60)
        return {"action": "restarted", "pid": process.pid, "delay": delay}
    except Exception as exc:
        message = redact_sensitive_text(exc)
        observer_runtime_service.update_supervisor_state(next_restart_at=now + delay, last_error=message)
        set_observer_progress("error", f"机会观察自动重启失败：{message}", 0)
        return {"action": "restart_failed", "error": message, "delay": delay}


def observer_supervisor_loop() -> None:
    while not observer_supervisor_stop.wait(max(0.5, float(OBSERVER_SUPERVISOR_INTERVAL_SECONDS))):
        observer_supervisor_once()


def build_observer_env() -> tuple[dict, list[str]]:
    env = os.environ.copy()
    config = strategy_config()
    profile = unified_sampling_profile(config)
    window_seconds = str(profile["seconds"])
    rpc_urls = aave_rpc_urls()
    pool_address = aave_pool_address()
    market = liquidation_market_payload().get("active") or {}
    reserve_limit = parse_env_int("AAVE_RESERVE_SYMBOL_LIMIT", AAVE_RESERVE_SYMBOL_LIMIT, minimum=0)[0]
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
    liquidation_symbols: list[str] = []
    try:
        database_url = database_url_or_none()
        if database_url:
            related_rows = [
                *db_load_liquidation_core_opportunity_pool(database_url, limit=500),
                *db_load_liquidation_high_frequency_pool(database_url, limit=500),
                *db_load_liquidation_borrow_health_pool(database_url, limit=500),
                *db_load_latest_liquidation_account_reports(database_url, limit=500),
            ]
            liquidation_symbols = binance_symbols_for_liquidation_assets(
                liquidation_asset_ids_from_pool_rows(related_rows),
                reserve_assets,
            )
    except Exception:
        liquidation_symbols = []
    tracked_symbols = list(dict.fromkeys([*(liquidation_symbols or reserve_symbols), "USDCUSDT"]))
    env["BINANCE_SYMBOL_SELECTION"] = "explicit"
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
    env["AAVE_VERIFICATION_ENABLED"] = "false"
    env["AAVE_RESERVE_SYMBOL_LIMIT"] = str(reserve_limit)
    env["OBSERVATION_DB_WRITES"] = "true"
    env["REPORT_ONLY_ALERTS"] = "true"
    env["SKIP_DATABASE_SCHEMA"] = "true"
    env["OBSERVER_REQUIRE_DB_LOCK"] = "true"
    env["LIQUIDATION_RPC"] = rpc_urls[0]
    env["LIQUIDATION_RPCS"] = ",".join(rpc_urls)
    network_prefix = str(market.get("network") or "avalanche").upper().replace("-", "_")
    env[f"{network_prefix}_RPC"] = rpc_urls[0]
    env[f"{network_prefix}_RPCS"] = ",".join(rpc_urls)
    if pool_address:
        env["LIQUIDATION_POOL_ADDRESS"] = pool_address
        env["AAVE_POOL_ADDRESS"] = pool_address
    for key, value in config.items():
        env[key] = str(value)
    env["SYMBOLS"] = ",".join(tracked_symbols if (liquidation_symbols or reserve_symbols) else ASSETS.keys())
    env["BINANCE_SYMBOL_SELECTION"] = "explicit"
    env["BINANCE_TOP_SYMBOL_LIMIT"] = "0"
    display_symbols = liquidation_symbols or reserve_symbols
    if not display_symbols:
        try:
            rest_bases = env_urls("BINANCE_REST_BASES", DEFAULT_BINANCE_REST_BASES, "https://")
            display_symbols = resolve_aave_binance_overlap_symbols(rest_bases, int(env["BINANCE_TOP_SYMBOL_LIMIT"]))
        except Exception:
            display_symbols = []
    fallback_symbols = tracked_symbols if (liquidation_symbols or reserve_symbols) else list(ASSETS.keys())
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
        message = redact_sensitive_text(exc)
        with observer_start_lock:
            observer_start_error = message
            selected_symbols = []
        set_observer_progress("error", message, 0)
    finally:
        with observer_start_lock:
            observer_starting = False


def liquidation_engine_enabled() -> bool:
    return os.getenv("LIQUIDATION_ENGINE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def liquidation_engine_autostart_enabled() -> bool:
    return any(
        os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}
        for name in (
            "LIQUIDATION_ENGINE_ENABLED",
            "LIQUIDATION_EXECUTION_ENABLED",
            "LIQUIDATION_AUTO_EXECUTE",
        )
    )


def liquidation_observer_autostart_enabled() -> bool:
    raw = os.getenv("LIQUIDATION_OBSERVER_AUTOSTART")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return liquidation_engine_autostart_enabled()


def start_observer_runtime_async() -> dict:
    global observer_start_error, observer_starting, selected_symbols
    if quick_observer_running():
        return {"enabled": True, "started": False, "reason": "already_running", "pid": quick_observer_pid()}
    with observer_start_lock:
        clear_stale_observer_start()
        if observer_starting:
            return {"enabled": True, "started": False, "reason": "already_starting"}
        try:
            configured_database_url()
        except Exception as exc:
            message = redact_sensitive_text(exc)
            observer_start_error = message
            set_observer_progress("error", message, 0)
            set_control_status("error", "start observer", message, 0)
            return {"enabled": True, "started": False, "reason": "database_config_error", "error": message}
        observer_starting = True
        observer_start_error = None
        selected_symbols = velocity_start_symbols()
        observer_start_progress["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        set_observer_progress("initializing", "observer autostart requested", 5)
        set_control_status(
            "initializing",
            "start observer",
            "observer autostart requested",
            5,
            ttl_seconds=OBSERVER_START_TIMEOUT_SECONDS + 30,
        )
    threading.Thread(target=start_observer_background, name="observer-autostarter", daemon=True).start()
    return {"enabled": True, "started": True, "symbols": selected_symbols}


def liquidation_market_price_snapshot() -> dict[str, float]:
    max_age, _ = parse_env_float("LIQUIDATION_MARKET_PRICE_MAX_AGE_SECONDS", 120.0)
    return price_snapshot_from_extremes(latest_binance_extremes_file(), max_age_seconds=max(1.0, max_age))


def liquidation_affected_accounts_for_market_assets(assets: list[str]) -> list[str]:
    database_url = database_url_or_none()
    if not database_url:
        return []
    limit, _ = parse_env_int("LIQUIDATION_ENGINE_MAX_ACCOUNTS", 100, minimum=1)
    buffer_bps, _ = parse_env_int("LIQUIDATION_PRICE_TRIGGER_BUFFER_BPS", 25, minimum=0)
    variants = asset_variants_for_market_symbols(assets, reserve_cache_path=AAVE_RESERVE_CACHE_PATH)
    price_snapshot = liquidation_market_price_snapshot()
    core_rows = db_load_liquidation_core_opportunity_pool(database_url, limit=limit)
    related_rows = [
        row
        for row in core_rows
        if set(liquidation_asset_ids_from_pool_rows([row])).intersection(set(variants))
    ]
    triggered = accounts_triggered_by_prices(
        related_rows,
        price_snapshot,
        target_health_factor=1.0,
        buffer_bps=buffer_bps,
    )
    if triggered:
        return triggered[:limit]
    affected = db_load_liquidation_accounts_for_assets(database_url, variants, limit=limit)
    executable = set(liquidation_engine_core_accounts())
    return [account for account in affected if account in executable][:limit]


def liquidation_engine_core_accounts() -> list[str]:
    database_url = database_url_or_none()
    if not database_url:
        return []
    config = LiquidationEngineConfig.from_env()
    rows = db_load_liquidation_core_opportunity_pool(
        database_url,
        limit=config.max_accounts_per_tick,
    )
    accounts: list[str] = []
    for row in rows:
        account = str(row.get("account") or "").strip()
        if not account:
            continue
        if row.get("auto_execution_blocked"):
            continue
        if not row.get("best_collateral_asset") or not row.get("best_debt_asset"):
            continue
        if not bool(row.get("quote_viable")):
            continue
        accounts.append(account)
    return list(dict.fromkeys(accounts))


def start_liquidation_engine_runtime(*, force: bool = False) -> dict:
    global liquidation_engine_instance, liquidation_engine_thread
    if not force and not liquidation_engine_enabled():
        return {"enabled": False, "started": False, "reason": "LIQUIDATION_ENGINE_ENABLED is false"}
    with liquidation_engine_lock:
        if liquidation_engine_thread is not None and liquidation_engine_thread.is_alive():
            return {"enabled": True, "started": False, "reason": "already_running"}
        dependencies = LiquidationEngineDependencies(
            load_accounts=liquidation_engine_core_accounts,
            build_payload=liquidation_execution_payload_for_account,
            simulate_static_call=simulate_liquidation_static_call,
            submit=execute_flashloan_liquidation_transaction,
            record_attempt=record_liquidation_execution_attempt_safely,
            load_controls=liquidation_execution_controls,
            load_price_snapshot=liquidation_market_price_snapshot,
            load_price_events=lambda: [],
            load_affected_accounts=liquidation_affected_accounts_for_market_assets,
        )
        liquidation_engine_instance = LiquidationEngine(dependencies, LiquidationEngineConfig.from_env())
        liquidation_engine_thread = liquidation_engine_instance.run_in_thread(name="liquidation-engine")
        return {
            "enabled": True,
            "started": True,
            "mode": liquidation_engine_instance.config.mode,
            "auto_execute": liquidation_engine_instance.config.auto_execute,
            "manual_test_completed": liquidation_engine_instance.config.manual_test_completed,
        }


def initialize_liquidation_runtime() -> None:
    if liquidation_engine_autostart_enabled():
        try:
            start_liquidation_engine_runtime(force=True)
        except Exception as exc:
            set_control_status("error", "启动清算引擎", redact_sensitive_text(exc), 0)
    if liquidation_observer_autostart_enabled():
        try:
            start_observer_runtime_async()
        except Exception as exc:
            set_control_status("error", "start observer", redact_sensitive_text(exc), 0)
    ui_scan_enabled = os.getenv("LIQUIDATION_UI_SCAN_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if ui_scan_enabled:
        _scan_initialize_liquidation_runtime()
    else:
        try:
            load_liquidation_account_registry(force=True)
        except Exception as exc:
            set_control_status("error", "读取清算数据库", redact_sensitive_text(exc), 0)


def initialize_cow_arbitrage_runtime() -> None:
    if not cow_quote_daemon_enabled():
        return
    try:
        ensure_cow_quote_daemon_running(database_url_provider=database_url_or_none)
    except Exception as exc:
        set_control_status("error", "start CoW quote daemon", redact_sensitive_text(exc), 0)


def render_control_panel() -> str:
    chart_options = "\n".join(f'<option value="{escape(symbol)}">{escape(symbol)}</option>' for symbol in ASSETS)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__CHART_OPTIONS__", chart_options)


app = create_control_panel_app(sys.modules[__name__])


if __name__ == "__main__":
    initialize_liquidation_runtime()
    initialize_cow_arbitrage_runtime()
    app.run(host="0.0.0.0", port=parse_env_int("PORT", 5000, minimum=1)[0])
