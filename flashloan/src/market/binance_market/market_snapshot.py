from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from typing import Any

from core.db.storage_cow_tokens import load_cow_supported_tokens, replace_cow_supported_tokens
from dex.cow_flashloan.capabilities import assess_cow_flashloan_sdk_plan
from dex.cow_flashloan.order_submission import (
    cow_order_submission_adapter_available,
    cow_order_submission_enabled,
    cow_order_submission_network_supported,
    cow_order_submission_requested,
    cow_order_submission_sdk_install_hint,
    cow_order_submission_sdk_ready,
    cow_order_submission_sdk_status,
    cow_order_submission_signer_ready,
    cow_order_submission_signer_status,
)
from dex.intent_trade import bind_cow_intent_context, build_triangular_onchain_intent_trade
from dex.cow_flashloan.routes import SUPPORTED_COW_NETWORKS, CowToken, build_token_registry, cow_account_config, cow_network_config, evaluate_cow_route, load_cow_token_list, rank_cow_routes, resolve_token
from dex.cow_flashloan.network_scope import supported_cow_networks
from market.observer_common import DEFAULT_BINANCE_REST_BASES, env_urls, fetch_json, write_json_atomic
from core.json_io import read_scoped_json, write_scoped_json
from market.observer_state import PriceState
from market.velocity_candidates import base_token_symbol, top_bottom_from_extremes
from market.strategy.arbitrage import ArbitrageConfig, build_pair_strategy_analysis
from market.strategy.limits import (
    DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT,
    DEFAULT_COW_NETWORK_DISPLAY_LIMIT,
    DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
    DEFAULT_EXECUTION_SLIPPAGE_BPS,
    DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    DEFAULT_MIN_COW_SPREAD_PERCENT,
    DEFAULT_MIN_SIDE_CHANGE_PERCENT,
    DEFAULT_MIN_TOKEN_PRICE_USD,
    DEFAULT_BINANCE_RAW_SIDE_LIMIT,
)
from market.binance_market.cow_tokens import (
    DEFAULT_COW_TEST_NETWORK,
    DEFAULT_COW_TOKEN_CACHE_PATH,
    _bnb_static_market_registry,
    _market_registry_for_network,
    _resolve_cow_market_symbol,
    load_cow_supported_token_registry,
    refresh_cow_supported_token_cache,
)
from market.binance_market.pool_support import (
    DEFAULT_AVALANCHE_V3_POOL_CACHE_PATH,
    DEFAULT_BNB_POOL_CACHE_PATH,
    WRAPPED_SYMBOL_ALIASES,
    _effective_pool_count,
    _filter_pairs_by_avalanche_pool_route_support,
    _filter_pairs_by_pool_route_support,
    _filter_rows_by_avalanche_pool_symbol_support,
    _filter_rows_by_pool_symbol_support,
    _pool_address_text,
    _pool_pair_info,
    _pool_route_support,
    _pool_symbol,
    _symbol_alias_set,
)
from market.binance_market.cow_legacy_compat import (
    __legacy_cow_route_specs,
    __legacy_cow_cost_summary,
    __legacy_cow_min_profit_usd_for_amount,
    __legacy_cow_pure_profit_intent,
    __legacy_cow_own_limit_order_intent,
    __legacy_cow_sdk_result_snapshot,
    __legacy_cow_order_submission_requested,
    __legacy_cow_order_submission_adapter_available,
    __legacy_cow_order_submission_sdk_ready,
    __legacy_cow_order_submission_sdk_status,
    __legacy_cow_order_submission_enabled,
    __legacy_attach_cow_flashloan_sdk_plan,
    __legacy_execution_plan_rows,
    __legacy_cow_query_price_for_step,
    __legacy_cow_query_rate_for_step,
    __legacy_cow_quote_step_context,
    __legacy_apply_cow_quote_targets,
    __legacy_apply_cow_quote_analysis,
    __legacy_cow_execution_precheck,
)

from market.binance_market import pool_support as pool_support_module
from market.binance_market.cow_math import (
    _amount_from_price_rule,
    _decimal_percent_delta,
    _decimal_rank_analysis,
    _decimal_or_none,
    _decimal_text,
    _decimal_value,
    _exchange_rate_candidates,
    _market_price_candidates,
    _query_guard_analysis,
    _query_window_timing,
    _select_decimal_candidate,
    _selected_decimal_text,
    _selected_source,
    _serialize_decimal_candidates,
    cost_adjusted_cow_thresholds,
)
from market.binance_market.state_service import select_market_state
from market.binance_market.submission_readiness import build_submission_readiness
from market.binance_market.candidate_service import build_pair_candidates

SRC_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AAVE_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"
DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH = SRC_ROOT / "runtime" / "state" / "binance_market_snapshot.json"
DEFAULT_COW_FLASHLOAN_CONTROL_MODE = "intent"
DEFAULT_BINANCE_MARKET_PREVIOUS_MAX_AGE_SECONDS = 120.0
COW_NETWORK_LABELS = {
    "ethereum": "Ethereum",
    "gnosis": "Gnosis",
    "arbitrum_one": "Arbitrum One",
    "base": "Base",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "bnb": "BNB Chain",
    "linea": "Linea",
    "plasma": "Plasma",
    "ink": "Ink",
    "sepolia": "Sepolia",
}


STABLE_ROUTE_CANDIDATES = (
    (
        "stable_usdc_to_y_to_x_to_usdc",
        ("USDC", "y", "x", "USDC"),
        "buy_loser_then_gainer",
    ),
    (
        "stable_usdc_to_x_to_y_to_usdc",
        ("USDC", "x", "y", "USDC"),
        "reverse_check",
    ),
)


def _safe_float(*args, **kwargs):
    from market.binance_market import service
    return service._safe_float(*args, **kwargs)


def _binance_price_row(item: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(item.get("symbol") or "").strip().upper()
    if not symbol.endswith("USDT") or symbol == "USDCUSDT":
        return None
    try:
        current_price = float(item.get("price") or item.get("lastPrice") or 0)
    except (TypeError, ValueError):
        return None
    if current_price <= 0:
        return None
    return {
        "symbol": symbol,
        "current_price": current_price,
        "price_source": "rest_interval",
    }

def fetch_binance_current_price_rows(rest_bases: list[str] | None = None) -> list[dict[str, Any]]:
    bases = rest_bases or env_urls("BINANCE_REST_BASES", DEFAULT_BINANCE_REST_BASES, "https://")
    payload: object = []
    for base in bases:
        try:
            payload = fetch_json(f"{base}/api/v3/ticker/price")
            break
        except Exception:
            payload = []
    if not isinstance(payload, list):
        return []
    return [
        row
        for item in payload
        if isinstance(item, dict)
        for row in [_binance_price_row(item)]
        if row is not None
    ]

def _iso_to_event_ms(value: Any) -> int | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)

def _snapshot_event_ms(snapshot: dict[str, Any] | None) -> int | None:
    if not isinstance(snapshot, dict):
        return None
    return _iso_to_event_ms(snapshot.get("observed_at"))

def _current_event_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)

async def _build_window_extremes_from_rows(
    *,
    previous_snapshot: dict[str, Any] | None,
    current_rows: list[dict[str, Any]],
    side_limit: int,
) -> dict[str, Any]:
    state = PriceState()
    previous_ms = _snapshot_event_ms(previous_snapshot)
    current_ms = _current_event_ms()
    previous_by_symbol = {}
    earliest_previous_ms = previous_ms
    if isinstance(previous_snapshot, dict):
        for item in previous_snapshot.get("basket") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper()
            price = _safe_float(item.get("current_price") if item.get("current_price") is not None else item.get("end_price"))
            event_ms = int(item.get("end_ms") or previous_ms or max(0, current_ms - 1))
            if symbol and price and price > 0:
                previous_by_symbol[symbol] = (event_ms, price)
                earliest_previous_ms = event_ms if earliest_previous_ms is None else min(earliest_previous_ms, event_ms)
    symbols = []
    for item in current_rows:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        price = _safe_float(item.get("current_price"))
        if not symbol or price is None or price <= 0:
            continue
        symbols.append(symbol)
        if symbol in previous_by_symbol:
            event_ms, previous_price = previous_by_symbol[symbol]
            await state.update_binance(symbol, previous_price, event_ms, "rest_interval")
        await state.update_binance(symbol, price, current_ms, "rest_interval")
    actual_window_seconds = max(0.001, (current_ms - (earliest_previous_ms or current_ms)) / 1000)
    snapshot = await state.window_extremes(
        symbols,
        window_seconds=actual_window_seconds + 5.0,
        limit=side_limit,
        source="rest_interval",
    )
    snapshot["window_seconds"] = actual_window_seconds
    return snapshot

def build_binance_rest_market_snapshot(
    *,
    side_limit: int = DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    previous_snapshot: dict[str, Any] | None = None,
    current_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    limit = max(1, int(side_limit))
    rows = current_rows or fetch_binance_current_price_rows()
    snapshot = asyncio.run(
        _build_window_extremes_from_rows(
            previous_snapshot=previous_snapshot,
            current_rows=rows,
            side_limit=limit,
        )
    )
    snapshot.update(
        {
        "price_source": "rest_interval",
        "market_state_source": "rest_fallback",
        "fallback_reason": "latest_extremes_insufficient",
        }
    )
    return snapshot

def _market_basket_symbol_count(extremes: dict[str, Any] | None) -> int:
    if not isinstance(extremes, dict):
        return 0
    seen: set[str] = set()
    basket = extremes.get("basket")
    if not isinstance(basket, list):
        return 0
    for item in basket:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            seen.add(symbol)
    return len(seen)

def needs_binance_rest_snapshot(
    extremes: dict[str, Any] | None,
    *,
    side_limit: int = DEFAULT_BINANCE_RAW_SIDE_LIMIT,
) -> bool:
    if not isinstance(extremes, dict):
        return True
    if extremes.get("price_source") == "binance_rest_24h":
        return True
    top, bottom = top_bottom_from_extremes(extremes, side_limit=side_limit)
    if len(top) < side_limit or len(bottom) < side_limit:
        return True
    basket_symbol_count = _market_basket_symbol_count(extremes)
    try:
        observation_universe_size = int(extremes.get("observation_universe_size") or 0)
    except (TypeError, ValueError):
        observation_universe_size = 0
    if basket_symbol_count < side_limit * 2:
        return True
    if observation_universe_size > 0 and basket_symbol_count < observation_universe_size:
        return True
    return False

def read_binance_market_snapshot(
    path: Path = DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH,
    *,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if expected_scope is not None:
        try:
            return read_scoped_json(path, expected_scope)
        except ValueError:
            return None
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None

def write_binance_market_snapshot(
    snapshot: dict[str, Any],
    path: Path = DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH,
    *,
    expected_scope: dict[str, Any] | None = None,
) -> None:
    if expected_scope is not None:
        write_scoped_json(path, snapshot, expected_scope)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(str(path), snapshot)

def _snapshot_age_seconds(snapshot: dict[str, Any] | None) -> float | None:
    event_ms = _snapshot_event_ms(snapshot)
    if event_ms is None:
        return None
    return max(0.0, time.time() - (event_ms / 1000))

def _previous_window_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    max_age_seconds: float | None = DEFAULT_BINANCE_MARKET_PREVIOUS_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("price_source") != "rest_interval":
        return None
    if max_age_seconds is None:
        return snapshot
    age = _snapshot_age_seconds(snapshot)
    if age is None or age > max(0.0, float(max_age_seconds)):
        return None
    return snapshot

def select_binance_market_extremes(
    realtime_extremes: dict[str, Any] | None,
    *,
    side_limit: int = DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    snapshot_path: Path = DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH,
    current_rows: list[dict[str, Any]] | None = None,
    persist_snapshot: bool = True,
    max_previous_age_seconds: float | None = DEFAULT_BINANCE_MARKET_PREVIOUS_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    return select_market_state(
        realtime_extremes,
        side_limit=side_limit,
        snapshot_path=snapshot_path,
        max_previous_age_seconds=max_previous_age_seconds,
        needs_refresh=lambda state, limit: needs_binance_rest_snapshot(state, side_limit=limit),
        read_snapshot=read_binance_market_snapshot,
        previous_snapshot=lambda snapshot, max_age: _previous_window_snapshot(
            snapshot,
            max_age_seconds=max_age,
        ),
        build_snapshot=lambda previous, limit: build_binance_rest_market_snapshot(
            side_limit=limit,
            previous_snapshot=previous,
            current_rows=current_rows,
        ),
        persist_snapshot=(
            write_binance_market_snapshot
            if persist_snapshot
            else lambda _selected, _path: None
        ),
    )

def _candidate_pair_count(top_rows: list[dict[str, Any]], bottom_rows: list[dict[str, Any]], min_spread_percent: float) -> int:
    count = 0
    min_spread = max(0.0, float(min_spread_percent))
    for x in top_rows:
        for y in bottom_rows:
            if x.get("symbol") == y.get("symbol"):
                continue
            spread = (
                float(x["change_percent"]) - float(y["change_percent"])
                if x.get("change_percent") is not None and y.get("change_percent") is not None
                else None
            )
            if spread is not None and spread > min_spread:
                count += 1
    return count
