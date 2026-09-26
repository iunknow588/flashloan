"""Control panel liquidation runtime helpers.

These helpers used to live directly in ``control_panel.py``.  They are kept here
so the panel module can stay focused on app assembly and lifecycle wiring.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from core.config_schema import parse_env_float, parse_env_int
from core.env_loader import load_env_files
from core.runtime_paths import resolve_runtime_dir
from bot.runtime.liquidation_engine import LiquidationEngineConfig


APP_DIR = Path(__file__).resolve().parents[1]
load_env_files(__file__, override=False)
RUNTIME_DIR = resolve_runtime_dir(base_dir=APP_DIR)
CACHE_DIR = RUNTIME_DIR / "cache"
AAVE_RESERVE_CACHE_PATH = CACHE_DIR / "aave_reserve_assets.json"


def _control_panel():
    # Lazy import keeps this module importable from control_panel.py while still
    # letting tests monkeypatch the panel namespace for DB and env helpers.
    from web import control_panel as cp

    return cp


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


def liquidation_market_price_snapshot() -> dict[str, float]:
    max_age, _ = parse_env_float("LIQUIDATION_MARKET_PRICE_MAX_AGE_SECONDS", 120.0)
    cp = _control_panel()
    return cp.price_snapshot_from_extremes(cp.latest_binance_extremes_file(), max_age_seconds=max(1.0, max_age))


LIQUIDATION_PRICE_TRIGGER_STATE: dict[str, object] = {
    "last_snapshot": {},
    "last_changed_assets": [],
    "updated_at": 0.0,
}


def liquidation_price_triggered_assets() -> list[str]:
    current = liquidation_market_price_snapshot()
    if not current:
        return []
    previous = LIQUIDATION_PRICE_TRIGGER_STATE.get("last_snapshot")
    previous_snapshot = previous if isinstance(previous, dict) else {}
    threshold_bps, _ = parse_env_int("LIQUIDATION_PRICE_TRIGGER_BPS", 100, minimum=1)
    changed_assets: list[str] = []
    if not previous_snapshot:
        changed_assets = list(current.keys())
    else:
        for asset, price in current.items():
            prior = previous_snapshot.get(asset)
            if prior is None:
                continue
            try:
                prior_price = float(prior)
                current_price = float(price)
            except (TypeError, ValueError):
                continue
            if prior_price <= 0:
                continue
            change_bps = abs(current_price - prior_price) / prior_price * 10000.0
            if change_bps >= float(threshold_bps):
                changed_assets.append(asset)
    LIQUIDATION_PRICE_TRIGGER_STATE["last_snapshot"] = current
    LIQUIDATION_PRICE_TRIGGER_STATE["last_changed_assets"] = changed_assets
    LIQUIDATION_PRICE_TRIGGER_STATE["updated_at"] = time.monotonic()
    return changed_assets


def liquidation_affected_accounts_for_market_assets(
    assets: list[str],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[str]:
    cp = _control_panel()
    database_url = cp.database_url_or_none()
    if not database_url:
        return []
    limit, _ = parse_env_int("LIQUIDATION_ENGINE_MAX_ACCOUNTS", 100, minimum=1)
    buffer_bps, _ = parse_env_int("LIQUIDATION_PRICE_TRIGGER_BUFFER_BPS", 25, minimum=0)
    variants = cp.asset_variants_for_market_symbols(assets, reserve_cache_path=AAVE_RESERVE_CACHE_PATH)
    price_snapshot = cp.liquidation_market_price_snapshot()
    core_rows = cp.db_load_liquidation_core_opportunity_pool(
        database_url,
        limit=limit,
        **({"market_id": market_id} if market_id is not None else {}),
        **({"chain_id": chain_id} if chain_id is not None else {}),
    )
    related_rows = [
        row
        for row in core_rows
        if set(cp.liquidation_asset_ids_from_pool_rows([row])).intersection(set(variants))
    ]
    triggered = cp.accounts_triggered_by_prices(
        related_rows,
        price_snapshot,
        target_health_factor=1.0,
        buffer_bps=buffer_bps,
    )
    if triggered:
        return triggered[:limit]
    affected = cp.db_load_liquidation_accounts_for_assets(
        database_url,
        variants,
        limit=limit,
        **({"market_id": market_id} if market_id is not None else {}),
        **({"chain_id": chain_id} if chain_id is not None else {}),
    )
    executable = set(cp.liquidation_engine_core_accounts())
    return [account for account in affected if account in executable][:limit]


def liquidation_price_triggered_accounts(
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[str]:
    assets = liquidation_price_triggered_assets()
    if not assets:
        return []
    return liquidation_affected_accounts_for_market_assets(assets, market_id=market_id, chain_id=chain_id)


def liquidation_engine_core_accounts() -> list[str]:
    cp = _control_panel()
    database_url = cp.database_url_or_none()
    if not database_url:
        return []
    config = LiquidationEngineConfig.from_env()
    rows = cp.db_load_liquidation_core_opportunity_pool(
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


__all__ = [
    "AAVE_RESERVE_CACHE_PATH",
    "LIQUIDATION_PRICE_TRIGGER_STATE",
    "liquidation_affected_accounts_for_market_assets",
    "liquidation_engine_autostart_enabled",
    "liquidation_engine_core_accounts",
    "liquidation_engine_enabled",
    "liquidation_market_price_snapshot",
    "liquidation_observer_autostart_enabled",
    "liquidation_price_triggered_accounts",
    "liquidation_price_triggered_assets",
]
