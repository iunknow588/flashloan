import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flask import Flask
from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SRC_ROOT
from core.config_schema import liquidation_config_health as build_liquidation_config_health
from core.env_loader import load_env_files, resolve_env_path
from execution.liquidation_scan import LiquidationScanConfig, load_account_addresses
from market.observer import env_urls
from web.control_panel_liquidation_pause import pause_guard_controls
from db.storage import (
    EXPECTED_SCHEMA_MIGRATION_IDS,
    ensure_database_schema,
    load_schema_migrations,
    load_liquidation_accounts as db_load_liquidation_accounts,
    liquidation_account_registry_stats as db_liquidation_account_registry_stats,
    liquidation_discovery_scan_progress as db_liquidation_discovery_scan_progress,
    load_liquidation_scan_config_library as db_load_liquidation_scan_config_library,
    prune_liquidation_accounts as db_prune_liquidation_accounts,
    record_liquidation_discovery_scan as db_record_liquidation_discovery_scan,
    require_psycopg,
    upsert_liquidation_accounts as db_upsert_liquidation_accounts,
)

load_env_files(__file__)
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
CONFIG_DIR = RUNTIME_DIR / "config"
CACHE_DIR = RUNTIME_DIR / "cache"
LIQUIDATION_CONFIG_PATH = CONFIG_DIR / "liquidation_config.json"
LIQUIDATION_PAUSE_GUARD_PATH = CACHE_DIR / "liquidation_pause_guard.json"
LIQUIDATION_ACCOUNTS_PATH = resolve_env_path("LIQUIDATION_ACCOUNTS_FILE", "runtime/cache/liquidation_accounts.txt", APP_DIR)
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
    raw_primary = os.getenv("AVALANCHE_RPC", os.getenv("AVALANCHE_RPC_URL", "")).strip()
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
        mev_buffer_usd=float(os.getenv("LIQUIDATION_MEV_BUFFER_USD", "0")),
        retry_buffer_usd=float(os.getenv("LIQUIDATION_RETRY_BUFFER_USD", "0")),
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


def liquidation_account_scan_start_days() -> float:
    return max(1.0, float(os.getenv("LIQUIDATION_ACCOUNT_SCAN_START_DAYS", "365")))


def liquidation_block_seconds() -> float:
    return max(0.1, float(os.getenv("LIQUIDATION_BLOCK_SECONDS", "2.0")))


def liquidation_discovery_block_overlap() -> int:
    return max(0, int(os.getenv("LIQUIDATION_DISCOVERY_BLOCK_OVERLAP", "1")))


def liquidation_health_display_limit() -> int:
    return max(1, int(os.getenv("LIQUIDATION_HEALTH_DISPLAY_LIMIT", "200")))


def liquidation_borrow_pool_display_limit() -> int:
    return max(1, min(int(os.getenv("LIQUIDATION_BORROW_POOL_DISPLAY_LIMIT", "100")), 500))


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


def liquidation_executor_owner_address() -> str:
    return os.getenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "").strip()


def liquidation_executor_private_key() -> str:
    return os.getenv("LIQUIDATION_EXECUTION_PRIVATE_KEY", os.getenv("DEPLOYER_PRIVATE_KEY", "")).strip()


def liquidation_self_funded_private_key() -> str:
    return os.getenv("LIQUIDATION_SELF_FUNDED_PRIVATE_KEY", liquidation_executor_private_key()).strip()


def dex_router_address() -> str:
    return os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()


def env_bool(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() not in {"0", "false", "no", "off"}


def liquidation_execution_controls() -> dict:
    max_debt_raw = os.getenv("LIQUIDATION_MAX_DEBT_TO_COVER", "0").strip()
    min_profit_raw = os.getenv("LIQUIDATION_MIN_PROFIT_BASE", "0").strip()
    config_health = build_liquidation_config_health()
    config_blocked_reasons = liquidation_config_blocked_reasons(config_health)
    pause_guard = pause_guard_controls(
        LIQUIDATION_PAUSE_GUARD_PATH,
        enabled=env_bool("LIQUIDATION_AUTO_PAUSE_ENABLED", True),
        threshold=int(os.getenv("LIQUIDATION_AUTO_PAUSE_FAILURE_THRESHOLD", "3") or 3),
    )
    return {
        "execution_enabled": env_bool("LIQUIDATION_EXECUTION_ENABLED", False),
        "require_static_call": env_bool("LIQUIDATION_REQUIRE_STATIC_CALL", True),
        "self_funded_ready": bool(
            liquidation_self_funded_private_key() and os.getenv("AAVE_POOL_ADDRESS", "").strip()
        ),
        "owner_configured": bool(liquidation_executor_owner_address()),
        "private_key_configured": bool(liquidation_executor_private_key()),
        "flashloan_executor_configured": bool(
            liquidation_executor_private_key()
            and liquidation_executor_owner_address()
            and liquidation_executor_address()
        ),
        "max_debt_to_cover": int(max_debt_raw or 0),
        "min_profit_base": int(min_profit_raw or 0),
        "max_gas_cost_usd": float(os.getenv("LIQUIDATION_MAX_GAS_COST_USD", "0") or 0),
        "mev_buffer_usd": float(os.getenv("LIQUIDATION_MEV_BUFFER_USD", "0") or 0),
        "retry_buffer_usd": float(os.getenv("LIQUIDATION_RETRY_BUFFER_USD", "0") or 0),
        "min_operator_net_profit_usd": float(os.getenv("LIQUIDATION_MIN_OPERATOR_NET_PROFIT_USD", "0") or 0),
        "allow_fallback_close_factor": env_bool("LIQUIDATION_ALLOW_FALLBACK_CLOSE_FACTOR", False),
        "allow_fallback_flashloan_premium": env_bool("LIQUIDATION_ALLOW_FALLBACK_FLASHLOAN_PREMIUM", False),
        "slippage_bps": int(os.getenv("LIQUIDATION_SWAP_SLIPPAGE_BPS", os.getenv("EXECUTION_SLIPPAGE_BPS", "50"))),
        "priority_fee_gwei": float(os.getenv("LIQUIDATION_EXECUTION_PRIORITY_FEE_GWEI", "1.5")),
        "tx_timeout_seconds": int(os.getenv("LIQUIDATION_EXECUTION_TIMEOUT_SECONDS", "180")),
        "max_payload_age_seconds": int(os.getenv("LIQUIDATION_MAX_PAYLOAD_AGE_SECONDS", "30")),
        "max_quote_age_seconds": int(os.getenv("LIQUIDATION_MAX_QUOTE_AGE_SECONDS", "15")),
        "config_valid": bool(config_health.get("valid")),
        "config_errors": list(config_health.get("errors") or []),
        "config_warnings": list(config_health.get("warnings") or []),
        "config_blocked_reasons": config_blocked_reasons,
        "chain_id": config_health.get("chain_id"),
        "expected_chain_id": config_health.get("expected_chain_id"),
        **pause_guard,
    }


def liquidation_config_health(chain_id: int | None = None) -> dict:
    return build_liquidation_config_health(chain_id=chain_id)


def liquidation_config_blocked_reasons(config_health: dict) -> list[str]:
    reasons: list[str] = []
    for check in config_health.get("checks") or []:
        if check.get("ok") or check.get("severity") != "error":
            continue
        name = str(check.get("name") or "")
        message = str(check.get("message") or "")
        if name == "LIQUIDATION_EXECUTOR_ADDRESS":
            reason = "missing_executor" if "missing" in message else "invalid_executor"
        elif name == "LIQUIDATION_EXECUTOR_OWNER_ADDRESS":
            reason = "missing_owner" if "missing" in message else "invalid_owner"
        elif name == "LIQUIDATION_EXECUTION_PRIVATE_KEY":
            reason = "private_key_mismatch"
        elif name == "CHAIN_ID":
            reason = "chain_id_mismatch"
        elif name == "AAVE_POOL_ADDRESS":
            reason = "missing_pool" if "missing" in message else "invalid_pool"
        else:
            reason = "config_invalid"
        if reason not in reasons:
            reasons.append(reason)
    return reasons


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
            accounts = db_load_liquidation_accounts(database_url)
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


def schema_status_payload() -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "up_to_date": False, "expected_migrations": list(EXPECTED_SCHEMA_MIGRATION_IDS), "applied_migrations": []}
    try:
        ensure_database_schema(database_url)
        migrations = load_schema_migrations(database_url)
        applied_ids = {str(row.get("migration_id")) for row in migrations}
        missing = [migration_id for migration_id in EXPECTED_SCHEMA_MIGRATION_IDS if migration_id not in applied_ids]
        return {
            "configured": True,
            "up_to_date": not missing,
            "expected_migrations": list(EXPECTED_SCHEMA_MIGRATION_IDS),
            "applied_migrations": migrations,
            "missing_migrations": missing,
        }
    except Exception as exc:
        return {"configured": True, "up_to_date": False, "error": str(exc), "expected_migrations": list(EXPECTED_SCHEMA_MIGRATION_IDS), "applied_migrations": []}


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


def liquidation_scan_config_library(limit: int = 100) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "configs": []}
    try:
        ensure_database_schema(database_url)
        return {"configured": True, "configs": db_load_liquidation_scan_config_library(database_url, limit=limit)}
    except Exception as exc:
        return {"configured": True, "configs": [], "error": str(exc)}


def liquidation_scan_config_payload(config_key: str) -> dict:
    configs = liquidation_scan_config_library(limit=100).get("configs") or []
    for item in configs:
        if item.get("config_key") == config_key:
            payload = item.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


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
    registry = liquidation_account_registry_window()
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    progress = liquidation_discovery_progress(pool_address)
    config_cursor = liquidation_scan_config_payload("liquidation_discovery_scans.latest_success")
    if not config_cursor:
        config_cursor = liquidation_scan_config_payload("liquidation_discovery_scans.latest")
    if progress.get("latest_recent_to_block") is None and config_cursor.get("status") == "success":
        try:
            progress["latest_recent_to_block"] = int(config_cursor.get("to_block"))
            progress["latest_recent_scan_end_at"] = config_cursor.get("scan_end_at")
        except (TypeError, ValueError):
            pass
    overlap_blocks = liquidation_discovery_block_overlap()
    latest_recent_to_block = progress.get("latest_recent_to_block")
    latest_end = parse_iso_datetime(progress.get("latest_recent_scan_end_at")) or parse_iso_datetime(registry.get("latest_scan_end_at"))
    start_anchor = now - timedelta(days=liquidation_account_scan_start_days())
    window = timedelta(days=liquidation_backfill_window_days())
    scan_start_at = start_anchor if force_full or latest_end is None else max(start_anchor, latest_end)
    scan_end_at = min(now, scan_start_at + window)
    mode = "recent"
    if latest_recent_to_block is not None and not force_full:
        from_block = max(0, int(latest_recent_to_block) + 1 - overlap_blocks)
        to_block = None
        lookback_blocks = 0
        registry["discovery_scan_progress"] = progress
        registry["discovery_cursor"] = {
            "source": "block-ledger",
            "direction": "forward-from-start",
            "mode": mode,
            "overlap_blocks": overlap_blocks,
            "previous_latest_to_block": int(latest_recent_to_block),
            "next_from_block": from_block,
            "next_to_block": None,
        }
        return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode
    seconds = max(0.0, (scan_end_at - scan_start_at).total_seconds())
    now_for_blocks = datetime.now(timezone.utc)
    start_lookback_blocks = max(1, int(max(0.0, (now_for_blocks - scan_start_at).total_seconds()) / liquidation_block_seconds()))
    end_lookback_blocks = int(max(0.0, (now_for_blocks - scan_end_at).total_seconds()) / liquidation_block_seconds())
    from_block = -start_lookback_blocks
    to_block = None if end_lookback_blocks <= 0 else -end_lookback_blocks
    lookback_blocks = max(1, start_lookback_blocks - end_lookback_blocks)
    registry["discovery_scan_progress"] = progress
    registry["discovery_cursor"] = {
        "source": "time-bootstrap",
        "mode": mode,
        "overlap_blocks": overlap_blocks,
        "next_from_block": from_block,
        "next_to_block": to_block,
    }
    return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode
