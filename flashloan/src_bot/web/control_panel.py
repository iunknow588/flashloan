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
from db.storage import (
    ensure_database_schema,
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
LATEST_ARBITRAGE_PATH = STATE_DIR / "latest_arbitrage.json"
LATEST_EXECUTABLE_SIGNAL_PATH = STATE_DIR / "latest_executable_signal.json"
LATEST_EXTREMES_PATH = STATE_DIR / "latest_extremes.json"
AAVE_RESERVE_CACHE_PATH = CACHE_DIR / "aave_reserve_assets.json"
DEX_BORROW_TARGET_CACHE_PATH = CACHE_DIR / "dex_borrow_targets.json"
LIQUIDATION_ACCOUNTS_PATH = resolve_env_path("LIQUIDATION_ACCOUNTS_FILE", "runtime/cache/liquidation_accounts.txt", APP_DIR)
OBSERVER_PID_PATH = RUNTIME_DIR / "observer.pid"
STRATEGY_CONFIG_PATH = CONFIG_DIR / "strategy_config.json"
LIQUIDATION_CONFIG_PATH = CONFIG_DIR / "liquidation_config.json"
REPO_ROOT = APP_DIR.parents[1]
DEFAULT_AAVE_RPC_CANDIDATES = [
    "https://api.avax.network/ext/bc/C/rpc",
    "https://rpc.ankr.com/avalanche",
    "https://avalanche-c-chain-rpc.publicnode.com",
]
LIQUIDATION_SCAN_CACHE: dict[str, object] = {
    "updated_at": 0.0,
    "payload": None,
    "running": False,
    "started_at": None,
    "finished_at": None,
}
LIQUIDATION_SCAN_LOCK = threading.Lock()
LIQUIDATION_ACCOUNT_CACHE: dict[str, object] = {"updated_at": 0.0, "accounts": None, "source": None}
LIQUIDATION_DISCOVERY_CACHE: dict[str, object] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
    "last_backfill_at": None,
    "last_backfill_monotonic": 0.0,
    "historical_cursor_at": None,
}
LIQUIDATION_DISCOVERY_LOCK = threading.Lock()
LIQUIDATION_REFRESH_THREAD: Optional[threading.Thread] = None
LIQUIDATION_REFRESH_STOP = threading.Event()

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
DEFAULT_LIQUIDATION_CONFIG = {
    "LIQUIDATION_RETENTION_DAYS": 365,
    "LIQUIDATION_SCAN_INTERVAL_SECONDS": 300,
    "LIQUIDATION_DISCOVERY_INTERVAL_SECONDS": 3600,
}


def liquidation_runtime_config() -> dict[str, float]:
    config = dict(DEFAULT_LIQUIDATION_CONFIG)
    if LIQUIDATION_CONFIG_PATH.exists():
        try:
            raw = json.loads(LIQUIDATION_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                config.update({key: raw[key] for key in config if key in raw})
        except Exception:
            pass
    for key in config:
        if os.getenv(key) is not None:
            try:
                config[key] = float(os.getenv(key, str(config[key])))
            except ValueError:
                pass
    return config


def write_liquidation_runtime_config(values: dict) -> dict[str, float]:
    current = liquidation_runtime_config()
    if "retention_days" in values:
        retention_days = int(values.get("retention_days") or current["LIQUIDATION_RETENTION_DAYS"])
        current["LIQUIDATION_RETENTION_DAYS"] = 30 if retention_days <= 31 else 365
    if "scan_interval_seconds" in values:
        current["LIQUIDATION_SCAN_INTERVAL_SECONDS"] = max(30.0, float(values.get("scan_interval_seconds") or 300))
    if "discovery_interval_seconds" in values:
        current["LIQUIDATION_DISCOVERY_INTERVAL_SECONDS"] = max(30.0, float(values.get("discovery_interval_seconds") or 3600))
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LIQUIDATION_CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current

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


def liquidation_scan_config() -> LiquidationScanConfig:
    return LiquidationScanConfig(
        wide_scan_seconds=float(os.getenv("LIQUIDATION_WIDE_SCAN_SECONDS", "1800")),
        near_scan_seconds=float(os.getenv("LIQUIDATION_NEAR_SCAN_SECONDS", "0.2")),
        warning_health_factor=float(os.getenv("LIQUIDATION_WARNING_HEALTH_FACTOR", "1.05")),
        liquidation_health_factor=float(os.getenv("LIQUIDATION_TRIGGER_HEALTH_FACTOR", "1.0")),
        max_candidates=int(os.getenv("LIQUIDATION_MAX_CANDIDATES", "5000")),
        liquidation_bonus_percent=float(os.getenv("LIQUIDATION_BONUS_PERCENT", "5.0")),
        flashloan_fee_percent=float(os.getenv("LIQUIDATION_FLASHLOAN_FEE_PERCENT", "0.05")),
        dex_slippage_percent=float(os.getenv("LIQUIDATION_DEX_SLIPPAGE_PERCENT", "0.10")),
        gas_cost_usd=float(os.getenv("LIQUIDATION_GAS_COST_USD", "0")),
        watch_health_factor=float(os.getenv("LIQUIDATION_WATCH_HEALTH_FACTOR", "1.5")),
        close_factor=float(os.getenv("LIQUIDATION_CLOSE_FACTOR", "0.5")),
    )


def liquidation_scan_interval_seconds() -> float:
    config = liquidation_runtime_config()
    return max(30.0, float(config["LIQUIDATION_SCAN_INTERVAL_SECONDS"]))


def liquidation_discovery_interval_seconds() -> float:
    config = liquidation_runtime_config()
    return max(
        liquidation_scan_interval_seconds(),
        float(config["LIQUIDATION_DISCOVERY_INTERVAL_SECONDS"]),
    )


def liquidation_backfill_interval_seconds() -> float:
    return max(
        liquidation_discovery_interval_seconds(),
        float(os.getenv("LIQUIDATION_BACKFILL_INTERVAL_SECONDS", "3600")),
    )


def liquidation_recent_discovery_days() -> float:
    return max(1.0, float(os.getenv("LIQUIDATION_RECENT_DISCOVERY_DAYS", "7")))


def liquidation_backfill_window_days() -> float:
    return max(1.0, float(os.getenv("LIQUIDATION_BACKFILL_WINDOW_DAYS", "7")))


def liquidation_block_seconds() -> float:
    return max(0.1, float(os.getenv("LIQUIDATION_BLOCK_SECONDS", "2.0")))


def liquidation_background_refresh_enabled() -> bool:
    return os.getenv("LIQUIDATION_BACKGROUND_REFRESH", "true").strip().lower() not in {"0", "false", "no"}


def liquidation_retention_days() -> int:
    config = liquidation_runtime_config()
    configured = max(30, int(config["LIQUIDATION_RETENTION_DAYS"]))
    if configured <= 31:
        return 30
    return 365


def protocol_data_provider_address() -> str:
    return os.getenv("AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS", "").strip()


def liquidation_data_provider_address() -> str:
    return os.getenv("AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS", "").strip()


def liquidation_executor_address() -> str:
    return os.getenv("LIQUIDATION_EXECUTOR_ADDRESS", "").strip()


def dex_router_address() -> str:
    return os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()


def database_url_or_none() -> Optional[str]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    return database_url or None


def load_liquidation_account_registry(force: bool = False) -> tuple[list[str], str]:
    now = time.monotonic()
    ttl_seconds = liquidation_scan_interval_seconds()
    cached_accounts = LIQUIDATION_ACCOUNT_CACHE.get("accounts")
    updated_at = float(LIQUIDATION_ACCOUNT_CACHE.get("updated_at") or 0.0)
    cached_source = str(LIQUIDATION_ACCOUNT_CACHE.get("source") or "")
    if not force and isinstance(cached_accounts, list) and now - updated_at < ttl_seconds:
        return list(cached_accounts), cached_source or "cache"

    accounts: list[str] = []
    source = "none"
    database_url = database_url_or_none()
    if database_url:
        try:
            ensure_database_schema(database_url)
            db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
            accounts = db_load_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
            source = "database"
        except Exception:
            accounts = []
            source = "database-error"
    if not accounts:
        file_accounts = load_account_addresses(LIQUIDATION_ACCOUNTS_PATH)
        if file_accounts:
            accounts = file_accounts
            source = "file-fallback" if source != "none" else "file"
    LIQUIDATION_ACCOUNT_CACHE["updated_at"] = now
    LIQUIDATION_ACCOUNT_CACHE["accounts"] = list(accounts)
    LIQUIDATION_ACCOUNT_CACHE["source"] = source
    return list(accounts), source


def liquidation_account_registry_window() -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"total_count": 0, "active_count": 0, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": liquidation_retention_days()}
    try:
        ensure_database_schema(database_url)
        return db_liquidation_account_registry_stats(database_url, retained_days=liquidation_retention_days())
    except Exception:
        return {"total_count": 0, "active_count": 0, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": liquidation_retention_days()}


def liquidation_discovery_progress(pool_address: str) -> dict:
    database_url = database_url_or_none()
    if not database_url or not pool_address:
        return {
            "latest_recent_scan_end_at": None,
            "earliest_backfill_scan_start_at": None,
            "latest_recent_to_block": None,
            "earliest_backfill_from_block": None,
            "success_count": 0,
            "error_count": 0,
            "scanned_block_count": 0,
        }
    try:
        ensure_database_schema(database_url)
        return db_liquidation_discovery_scan_progress(database_url, pool_address)
    except Exception:
        return {
            "latest_recent_scan_end_at": None,
            "earliest_backfill_scan_start_at": None,
            "latest_recent_to_block": None,
            "earliest_backfill_from_block": None,
            "success_count": 0,
            "error_count": 0,
            "scanned_block_count": 0,
        }


def resolve_discovery_block_range(rpc_url: str, from_block: int, to_block: Optional[int]) -> tuple[int, int, int]:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    latest_block = int(w3.eth.block_number)
    start_block = max(0, latest_block + int(from_block)) if int(from_block) < 0 else max(0, int(from_block))
    if to_block is None:
        end_block = latest_block
    else:
        raw_to_block = int(to_block)
        end_block = max(0, latest_block + raw_to_block) if raw_to_block < 0 else min(latest_block, raw_to_block)
    return latest_block, start_block, end_block


def record_liquidation_discovery_window(
    *,
    mode: str,
    status: str,
    rpc_url: str,
    pool_address: str,
    from_block: int,
    to_block: int,
    scan_start_at: datetime,
    scan_end_at: datetime,
    discovered_count: int = 0,
    error: Optional[str] = None,
) -> None:
    database_url = database_url_or_none()
    if not database_url:
        return
    try:
        ensure_database_schema(database_url)
        db_record_liquidation_discovery_scan(
            database_url,
            mode=mode,
            status=status,
            rpc_url=rpc_url,
            pool_address=pool_address,
            from_block=from_block,
            to_block=to_block,
            scan_start_at=scan_start_at,
            scan_end_at=scan_end_at,
            discovered_count=discovered_count,
            error=error,
        )
    except Exception:
        pass


def discovery_window_continuity_error(mode: str, from_block: int, to_block: int, progress: dict) -> Optional[str]:
    if from_block > to_block:
        return None
    if mode == "recent":
        latest_to = progress.get("latest_recent_to_block")
        if latest_to is None:
            return None
        if int(from_block) <= int(latest_to) + 1:
            return None
        return f"recent scan gap: previous to_block {latest_to}, next from_block {from_block}"
    if mode == "historical-backfill":
        earliest_from = progress.get("earliest_backfill_from_block")
        if earliest_from is None:
            return None
        if int(to_block) >= int(earliest_from) - 1:
            return None
        return f"historical backfill gap: next to_block {to_block}, previous from_block {earliest_from}"
    return None


def sync_liquidation_accounts_to_database(
    accounts: list[str],
    source: str = "manual",
    scan_start_at: Optional[datetime] = None,
    scan_end_at: Optional[datetime] = None,
    update_existing: bool = True,
) -> None:
    database_url = database_url_or_none()
    if not database_url or not accounts:
        return
    ensure_database_schema(database_url)
    db_upsert_liquidation_accounts(
        database_url,
        accounts,
        source=source,
        active=True,
        scan_start_at=scan_start_at,
        scan_end_at=scan_end_at,
        update_existing=update_existing,
    )
    db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())


def parse_iso_datetime(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def liquidation_discovery_window(force_full: bool = False) -> tuple[datetime, datetime, int, Optional[int], int, dict, str]:
    now = datetime.now(timezone.utc)
    retained_days = liquidation_retention_days()
    registry = liquidation_account_registry_window()
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    progress = liquidation_discovery_progress(pool_address)
    latest_end = parse_iso_datetime(progress.get("latest_recent_scan_end_at")) or parse_iso_datetime(registry.get("latest_scan_end_at"))
    earliest_start = parse_iso_datetime(progress.get("earliest_backfill_scan_start_at")) or parse_iso_datetime(registry.get("earliest_scan_start_at"))
    historical_cursor = parse_iso_datetime(LIQUIDATION_DISCOVERY_CACHE.get("historical_cursor_at")) or earliest_start
    retention_start = now - timedelta(days=retained_days)
    recent_start = now - timedelta(days=liquidation_recent_discovery_days())
    if force_full:
        anchor = historical_cursor or earliest_start or recent_start
        scan_end_at = min(anchor, recent_start)
        scan_start_at = max(retention_start, scan_end_at - timedelta(days=liquidation_backfill_window_days()))
        mode = "historical-backfill"
    else:
        scan_start_at = max(recent_start, latest_end) if latest_end is not None else recent_start
        scan_end_at = now
        mode = "recent"
    seconds = max(0.0, (scan_end_at - scan_start_at).total_seconds())
    now_for_blocks = datetime.now(timezone.utc)
    start_lookback_blocks = max(1, int(max(0.0, (now_for_blocks - scan_start_at).total_seconds()) / liquidation_block_seconds()))
    end_lookback_blocks = int(max(0.0, (now_for_blocks - scan_end_at).total_seconds()) / liquidation_block_seconds())
    from_block = -start_lookback_blocks
    to_block = None if end_lookback_blocks <= 0 else -end_lookback_blocks
    lookback_blocks = max(1, start_lookback_blocks - end_lookback_blocks)
    registry["discovery_scan_progress"] = progress
    return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode


def discover_and_sync_liquidation_accounts(force_full: bool = False) -> dict:
    if not database_url_or_none():
        return {"saved": False, "count": 0, "error": "DATABASE_URL is required"}
    if os.getenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true").strip().lower() in {"0", "false", "no"}:
        return {"saved": False, "count": 0, "error": "auto discovery disabled"}
    if not LIQUIDATION_DISCOVERY_LOCK.acquire(blocking=False):
        result = dict(LIQUIDATION_DISCOVERY_CACHE.get("last_result") or {})
        result["running"] = True
        return result

    LIQUIDATION_DISCOVERY_CACHE["running"] = True
    LIQUIDATION_DISCOVERY_CACHE["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LIQUIDATION_DISCOVERY_CACHE["finished_at"] = None
    try:
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        if not pool_address:
            return {"saved": False, "count": 0, "error": "missing AAVE_POOL_ADDRESS"}
        scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode = liquidation_discovery_window(force_full=force_full)
        if force_full and scan_end_at <= scan_start_at:
            result = {
                "saved": False,
                "count": 0,
                "skipped": True,
                "reason": "historical backfill complete",
                "mode": mode,
                "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                "registry_window": registry,
            }
            LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
            return result
        if not force_full and (scan_end_at - scan_start_at).total_seconds() < liquidation_discovery_interval_seconds():
            result = {
                "saved": False,
                "count": 0,
                "skipped": True,
                "reason": "discovery interval not reached",
                "mode": mode,
                "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                "registry_window": registry,
            }
            LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
            return result
        chunk_size = int(os.getenv("LIQUIDATION_BORROW_SCAN_CHUNK_SIZE", "1000"))
        limit = min(liquidation_scan_config().max_candidates, int(os.getenv("LIQUIDATION_BORROW_DISCOVERY_LIMIT", "5000")))
        last_error = None
        for candidate in aave_rpc_urls():
            actual_from_block = 0
            actual_to_block = 0
            try:
                _, actual_from_block, actual_to_block = resolve_discovery_block_range(candidate, from_block, to_block)
                if actual_from_block > actual_to_block:
                    discovered = []
                else:
                    discovered = discover_borrower_addresses(
                        candidate,
                        pool_address,
                        actual_from_block,
                        to_block=actual_to_block,
                        chunk_size=chunk_size,
                        limit=limit,
                    )
                sync_liquidation_accounts_to_database(
                    discovered,
                    source="auto-discovery",
                    scan_start_at=scan_start_at,
                    scan_end_at=scan_end_at,
                    update_existing=False,
                )
                LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
                progress = dict((registry.get("discovery_scan_progress") or {}))
                continuity_error = discovery_window_continuity_error(mode, actual_from_block, actual_to_block, progress)
                if continuity_error:
                    result = {
                        "saved": False,
                        "count": len(discovered),
                        "skipped": True,
                        "reason": continuity_error,
                        "mode": mode,
                        "rpc_url": candidate,
                        "from_block": from_block,
                        "to_block": to_block,
                        "actual_from_block": actual_from_block,
                        "actual_to_block": actual_to_block,
                        "lookback_blocks": lookback_blocks,
                        "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                        "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                        "registry_window": registry,
                    }
                    LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
                    return result
                record_liquidation_discovery_window(
                    mode=mode,
                    status="success",
                    rpc_url=candidate,
                    pool_address=pool_address,
                    from_block=actual_from_block,
                    to_block=actual_to_block,
                    scan_start_at=scan_start_at,
                    scan_end_at=scan_end_at,
                    discovered_count=len(discovered),
                )
                result = {
                    "saved": True,
                    "count": len(discovered),
                    "rpc_url": candidate,
                    "mode": mode,
                    "from_block": from_block,
                    "to_block": to_block,
                    "actual_from_block": actual_from_block,
                    "actual_to_block": actual_to_block,
                    "lookback_blocks": lookback_blocks,
                    "retention_days": liquidation_retention_days(),
                    "recent_discovery_days": liquidation_recent_discovery_days(),
                    "backfill_window_days": liquidation_backfill_window_days(),
                    "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
                    "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
                    "registry_window": liquidation_account_registry_window(),
                }
                if force_full:
                    LIQUIDATION_DISCOVERY_CACHE["last_backfill_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    LIQUIDATION_DISCOVERY_CACHE["last_backfill_monotonic"] = time.monotonic()
                    LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = scan_start_at.isoformat(timespec="seconds")
                LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
                return result
            except Exception as exc:
                last_error = str(exc)
                if actual_from_block <= actual_to_block:
                    record_liquidation_discovery_window(
                        mode=mode,
                        status="error",
                        rpc_url=candidate,
                        pool_address=pool_address,
                        from_block=actual_from_block,
                        to_block=actual_to_block,
                        scan_start_at=scan_start_at,
                        scan_end_at=scan_end_at,
                        discovered_count=0,
                        error=last_error,
                    )
        result = {
            "saved": False,
            "count": 0,
            "error": last_error or "unable to discover borrower addresses",
            "mode": mode,
            "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
            "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
        }
        LIQUIDATION_DISCOVERY_CACHE["last_result"] = result
        return result
    finally:
        LIQUIDATION_DISCOVERY_CACHE["running"] = False
        LIQUIDATION_DISCOVERY_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_DISCOVERY_LOCK.release()


def normalize_liquidation_account_values(values: object) -> list[str]:
    if isinstance(values, str):
        raw_items = values.replace("\r", "\n").replace(",", "\n").splitlines()
    elif isinstance(values, list):
        raw_items = values
    else:
        raw_items = []
    normalized: list[str] = []
    for item in raw_items:
        try:
            checksum = Web3.to_checksum_address(str(item).strip())
        except ValueError:
            continue
        if checksum not in normalized:
            normalized.append(checksum)
    return normalized


def start_liquidation_refresh_loop() -> None:
    global LIQUIDATION_REFRESH_THREAD
    if LIQUIDATION_REFRESH_THREAD is not None and LIQUIDATION_REFRESH_THREAD.is_alive():
        return
    if not liquidation_background_refresh_enabled():
        return

    def runner() -> None:
        while not LIQUIDATION_REFRESH_STOP.is_set():
            try:
                discover_and_sync_liquidation_accounts(force_full=False)
                liquidation_health_payload(force=True)
                last_backfill_at = float(LIQUIDATION_DISCOVERY_CACHE.get("last_backfill_monotonic") or 0.0)
                if time.monotonic() - last_backfill_at >= liquidation_backfill_interval_seconds():
                    discover_and_sync_liquidation_accounts(force_full=True)
            except Exception:
                pass
            LIQUIDATION_REFRESH_STOP.wait(liquidation_scan_interval_seconds())

    LIQUIDATION_REFRESH_THREAD = threading.Thread(target=runner, name="liquidation-refresh", daemon=True)
    LIQUIDATION_REFRESH_THREAD.start()


def initialize_liquidation_runtime() -> None:
    discover_and_sync_liquidation_accounts(force_full=False)
    load_liquidation_account_registry(force=True)
    start_liquidation_refresh_loop()


def scan_context_assets() -> tuple[Optional[str], list[dict], Optional[str]]:
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    if not pool_address:
        return None, [], "missing AAVE_POOL_ADDRESS"
    last_error: Optional[str] = None
    for candidate in aave_rpc_urls():
        try:
            assets = load_reserve_assets_for_scan(candidate, pool_address, limit=AAVE_RESERVE_SYMBOL_LIMIT)
            if assets:
                return candidate, assets, None
        except Exception as exc:
            last_error = str(exc)
    return None, [], last_error or "unable to load reserve assets"


def liquidation_health_summary(
    rows: list[dict],
    account_count: int,
    account_source: str,
    config: LiquidationScanConfig,
    rpc_url: Optional[str],
    error: Optional[str],
) -> dict:
    liquidatable_count = sum(1 for row in rows if row.get("status") == "liquidatable")
    warning_count = sum(1 for row in rows if row.get("status") == "warning")
    healthy_count = sum(1 for row in rows if row.get("status") == "healthy")
    worst_row = rows[0] if rows else None
    return {
        "account_source": account_source,
        "source_ready": account_count > 0,
        "account_count": account_count,
        "scanned_count": len(rows),
        "liquidatable_count": liquidatable_count,
        "warning_count": warning_count,
        "healthy_count": healthy_count,
        "warning_health_factor": config.warning_health_factor,
        "liquidation_health_factor": config.liquidation_health_factor,
        "wide_scan_seconds": config.wide_scan_seconds,
        "near_scan_seconds": config.near_scan_seconds,
        "rpc_url": rpc_url,
        "error": error,
        "worst_account": worst_row.get("account") if worst_row else None,
        "worst_health_factor": worst_row.get("health_factor") if worst_row else None,
        "retention_days": liquidation_retention_days(),
        "registry_window": liquidation_account_registry_window(),
        "scan_running": bool(LIQUIDATION_SCAN_CACHE.get("running")),
        "scan_started_at": LIQUIDATION_SCAN_CACHE.get("started_at"),
        "scan_finished_at": LIQUIDATION_SCAN_CACHE.get("finished_at"),
        "discovery_running": bool(LIQUIDATION_DISCOVERY_CACHE.get("running")),
        "discovery_started_at": LIQUIDATION_DISCOVERY_CACHE.get("started_at"),
        "discovery_finished_at": LIQUIDATION_DISCOVERY_CACHE.get("finished_at"),
        "discovery_last_result": LIQUIDATION_DISCOVERY_CACHE.get("last_result"),
        "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
        "backfill_interval_seconds": liquidation_backfill_interval_seconds(),
        "last_backfill_at": LIQUIDATION_DISCOVERY_CACHE.get("last_backfill_at"),
        "historical_cursor_at": LIQUIDATION_DISCOVERY_CACHE.get("historical_cursor_at"),
        "recent_discovery_days": liquidation_recent_discovery_days(),
        "backfill_window_days": liquidation_backfill_window_days(),
    }


def liquidation_health_with_scan_state(
    payload: dict,
    ttl_seconds: float,
    *,
    running: bool,
    cache_age_seconds: Optional[float] = None,
    cooldown_remaining_seconds: Optional[float] = None,
) -> dict:
    current = dict(payload)
    summary = dict(current.get("summary") or {})
    summary["scan_running"] = running
    summary["scan_started_at"] = LIQUIDATION_SCAN_CACHE.get("started_at")
    summary["scan_finished_at"] = LIQUIDATION_SCAN_CACHE.get("finished_at")
    summary["scan_interval_seconds"] = ttl_seconds
    if cache_age_seconds is not None:
        summary["scan_cache_age_seconds"] = max(0.0, cache_age_seconds)
    if cooldown_remaining_seconds is not None:
        summary["scan_cooldown_remaining_seconds"] = max(0.0, cooldown_remaining_seconds)
    current["summary"] = summary
    return current


def record_liquidation_health_scan_rows(rows: list[dict]) -> None:
    database_url = database_url_or_none()
    if not database_url or not rows:
        return
    try:
        for row in rows:
            report = {
                "account": row.get("account"),
                "summary": {
                    "health_factor": row.get("health_factor"),
                    "status": row.get("status"),
                    "health_factor_band": row.get("health_factor_band"),
                    "candidate_count": len(row.get("liquidation_candidates") or []),
                },
                "liquidation_profit": row.get("liquidation_profit"),
            }
            record_liquidation_account_scan(database_url, report)
        db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
    except Exception:
        pass


def liquidation_health_payload(force: bool = False) -> dict:
    now = time.monotonic()
    ttl_seconds = liquidation_scan_interval_seconds()
    cached_payload = LIQUIDATION_SCAN_CACHE.get("payload")
    updated_at = float(LIQUIDATION_SCAN_CACHE.get("updated_at") or 0.0)
    cache_age_seconds = now - updated_at
    if not force and cached_payload and cache_age_seconds < ttl_seconds:
        return liquidation_health_with_scan_state(
            cached_payload,  # type: ignore[arg-type]
            ttl_seconds,
            running=bool(LIQUIDATION_SCAN_CACHE.get("running")),
            cache_age_seconds=cache_age_seconds,
            cooldown_remaining_seconds=ttl_seconds - cache_age_seconds,
        )
    if not LIQUIDATION_SCAN_LOCK.acquire(blocking=False):
        if cached_payload:
            return liquidation_health_with_scan_state(
                cached_payload,  # type: ignore[arg-type]
                ttl_seconds,
                running=True,
                cache_age_seconds=cache_age_seconds if updated_at else None,
                cooldown_remaining_seconds=0.0,
            )
        return {
            "rows": [],
            "summary": {
                "scan_running": True,
                "scan_started_at": LIQUIDATION_SCAN_CACHE.get("started_at"),
                "scan_finished_at": LIQUIDATION_SCAN_CACHE.get("finished_at"),
                "scan_interval_seconds": ttl_seconds,
                "account_count": 0,
                "scanned_count": 0,
            },
        }

    config = liquidation_scan_config()
    LIQUIDATION_SCAN_CACHE["running"] = True
    LIQUIDATION_SCAN_CACHE["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LIQUIDATION_SCAN_CACHE["finished_at"] = None
    accounts: list[str] = []
    account_source = "none"
    auto_discovered = False
    rows: list[dict] = []
    rpc_url = None
    error = None
    try:
        accounts, account_source = load_liquidation_account_registry(force=force)
        if not accounts and os.getenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true").strip().lower() not in {"0", "false", "no"}:
            discovery = discover_and_sync_liquidation_accounts(force_full=force)
            if discovery.get("saved"):
                accounts, account_source = load_liquidation_account_registry(force=True)
                auto_discovered = True
                rpc_url = str(discovery.get("rpc_url") or "") or None
                error = None
                account_source = "database"
            elif discovery.get("error"):
                error = str(discovery.get("error"))
            if not accounts and not error:
                error = "liquidation account registry is empty"
        if accounts:
            for candidate in aave_rpc_urls():
                try:
                    rows = scan_account_health(accounts, os.getenv("AAVE_POOL_ADDRESS", "").strip(), candidate, config)
                    record_liquidation_health_scan_rows(rows)
                    rpc_url = candidate
                    error = None
                    break
                except Exception as exc:
                    error = str(exc)
        if not accounts and not error:
            error = "liquidation account registry is empty"
        payload = {
            "rows": watched_health_rows(rows, config.watch_health_factor)[:50],
            "summary": liquidation_health_summary(rows, len(accounts), account_source, config, rpc_url, error) | {
                "auto_discovered": auto_discovered,
                "watch_health_factor": config.watch_health_factor,
                "scan_interval_seconds": ttl_seconds,
                "watch_count": sum(1 for row in rows if isinstance(row.get("health_factor"), (int, float)) and float(row["health_factor"]) < config.watch_health_factor),
            },
        }
        LIQUIDATION_SCAN_CACHE["running"] = False
        LIQUIDATION_SCAN_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = liquidation_health_with_scan_state(
            payload,
            ttl_seconds,
            running=False,
            cache_age_seconds=0.0,
            cooldown_remaining_seconds=ttl_seconds,
        )
        LIQUIDATION_SCAN_CACHE["updated_at"] = time.monotonic()
        LIQUIDATION_SCAN_CACHE["payload"] = payload
        return payload
    finally:
        LIQUIDATION_SCAN_CACHE["running"] = False
        if not LIQUIDATION_SCAN_CACHE.get("finished_at"):
            LIQUIDATION_SCAN_CACHE["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        LIQUIDATION_SCAN_LOCK.release()


def liquidation_account_payload(account: str) -> dict:
    raw_account = account.strip()
    if not raw_account:
        raise ValueError("account is required")
    checksum = Web3.to_checksum_address(raw_account)
    rpc_url, reserve_assets, asset_error = scan_context_assets()
    if not rpc_url:
        raise RuntimeError(asset_error or "unable to resolve rpc_url")
    report = build_user_liquidation_report(
        checksum,
        rpc_url,
        os.getenv("AAVE_POOL_ADDRESS", "").strip(),
        reserve_assets,
        protocol_data_provider_address(),
        liquidation_data_provider_address(),
        liquidation_scan_config(),
    )
    try:
        database_url = database_url_or_none()
        if database_url:
            record_liquidation_account_scan(database_url, report)
            db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
    except Exception:
        pass
    report["context"] = {
        "rpc_url": rpc_url,
        "pool_address": os.getenv("AAVE_POOL_ADDRESS", "").strip(),
        "protocol_data_provider_address": protocol_data_provider_address() or None,
        "liquidation_data_provider_address": liquidation_data_provider_address() or None,
        "reserve_asset_count": len(reserve_assets),
        "error": asset_error,
    }
    return report


def liquidation_execution_payload_for_account(
    account: str,
    deadline_seconds: int = 300,
    allow_zero_min_out: bool = False,
) -> dict:
    executor_address = liquidation_executor_address()
    if not executor_address:
        raise RuntimeError("missing LIQUIDATION_EXECUTOR_ADDRESS")
    report = liquidation_account_payload(account)
    deadline = int(time.time()) + max(30, int(deadline_seconds))
    payload = build_liquidation_execution_payload(
        report,
        executor_address=executor_address,
        router_address=dex_router_address(),
        deadline=deadline,
        config=LiquidationExecutionPayloadConfig(allow_zero_min_collateral_out=allow_zero_min_out),
    )
    payload["account_report"] = report
    return payload


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


def opportunity_health_rows(extremes: Optional[dict], config: Optional[dict] = None) -> list[dict]:
    if not extremes:
        return []
    basket = extremes.get("basket")
    if not isinstance(basket, list) or not basket:
        return []
    config = config or strategy_config()
    up_threshold = max(0.0001, float(config.get("TRIGGER_MIN_UP_CHANGE_PERCENT", 1.0)))
    down_threshold = max(0.0001, float(config.get("TRIGGER_MIN_DOWN_CHANGE_PERCENT", 1.0)))
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
    monitor_window_seconds = float(config.get("BINANCE_CHANGE_WINDOW_SECONDS", 1.0))
    trigger_up = float(config.get("TRIGGER_MIN_UP_CHANGE_PERCENT", 1.0))
    trigger_down = float(config.get("TRIGGER_MIN_DOWN_CHANGE_PERCENT", 1.0))
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
    config = strategy_config()
    symbols = displayed_symbols(running or observer_starting)
    binance_extremes = restrict_extremes_to_symbols(binance_extremes, symbols)
    opportunity_health = opportunity_health_rows(binance_extremes, config)
    liquidation_health = liquidation_health_payload()
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
            "symbols": symbols,
            "binance_extremes": binance_extremes,
            "opportunity_health": opportunity_health,
            "opportunity_health_summary": opportunity_health_summary(opportunity_health, config),
            "liquidation_health": liquidation_health,
            "arbitrage_simulation": safe_latest(latest_arbitrage_simulation_file),
            "executable_signal": safe_latest(latest_executable_signal),
            "aave_reserve_cache": reserve_cache,
            "borrow_target_universe": safe_latest(borrow_target_universe),
            "strategy_config": config,
            "sampling_profile": unified_sampling_profile(config),
        }
    )


@app.get("/api/liquidation-health")
def liquidation_health_api():
    force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
    return jsonify(liquidation_health_payload(force=force))


@app.post("/api/liquidation-discovery")
def liquidation_discovery_api():
    payload = request.get_json(silent=True) or {}
    force_full = request.args.get("full", "").strip().lower() in {"1", "true", "yes"} or bool(payload.get("full"))
    result = discover_and_sync_liquidation_accounts(force_full=force_full)
    LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
    return jsonify(result)


@app.get("/api/liquidation-settings")
def liquidation_settings_api():
    config = liquidation_runtime_config()
    return jsonify(
        {
            "retention_days": liquidation_retention_days(),
            "scan_interval_seconds": liquidation_scan_interval_seconds(),
            "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
            "raw": config,
        }
    )


@app.post("/api/liquidation-settings")
def update_liquidation_settings_api():
    payload = request.get_json(silent=True) or {}
    config = write_liquidation_runtime_config(payload)
    LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
    LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
    return jsonify(
        {
            "saved": True,
            "retention_days": liquidation_retention_days(),
            "scan_interval_seconds": liquidation_scan_interval_seconds(),
            "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
            "raw": config,
        }
    )


@app.get("/api/liquidation/account")
def liquidation_account_api():
    account = request.args.get("account", "").strip()
    if not account:
        return jsonify({"error": "account is required"}), 400
    try:
        return jsonify(liquidation_account_payload(account))
    except Exception as exc:
        return jsonify({"error": str(exc), "account": account}), 400


@app.get("/api/liquidation/account/payload")
def liquidation_account_payload_api():
    account = request.args.get("account", "").strip()
    if not account:
        return jsonify({"error": "account is required"}), 400
    try:
        deadline_seconds = int(request.args.get("deadline_seconds", "300"))
        allow_zero_min_out = request.args.get("allow_zero_min_out", "").strip().lower() in {"1", "true", "yes"}
        return jsonify(
            liquidation_execution_payload_for_account(
                account,
                deadline_seconds=deadline_seconds,
                allow_zero_min_out=allow_zero_min_out,
            )
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "account": account}), 400


@app.post("/api/liquidation/accounts")
def liquidation_accounts_api():
    payload = request.get_json(silent=True) or {}
    raw_accounts = payload.get("accounts")
    accounts = normalize_liquidation_account_values(raw_accounts)
    if not accounts:
        return jsonify({"error": "accounts is required"}), 400
    try:
        database_url = database_url_or_none()
        if database_url:
            ensure_database_schema(database_url)
            db_upsert_liquidation_accounts(database_url, accounts, source=str(payload.get("source") or "manual"), active=True)
            db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())
        else:
            return jsonify({"error": "DATABASE_URL is required"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
    LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
    return jsonify({"saved": True, "count": len(accounts), "source": "database", "accounts": accounts})


@app.get("/api/opportunity-health")
def opportunity_health_api():
    running = quick_observer_running()
    symbols = displayed_symbols(running or observer_starting)
    binance_extremes = restrict_extremes_to_symbols(safe_latest(latest_binance_extremes_file), symbols)
    config = strategy_config()
    rows = opportunity_health_rows(binance_extremes, config)
    return jsonify(
        {
            "rows": rows,
            "summary": opportunity_health_summary(rows, config),
            "sampling_profile": unified_sampling_profile(config),
            "binance_extremes": binance_extremes,
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
    initialize_liquidation_runtime()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
