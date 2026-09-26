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
from market.binance_market import pool_support as pool_support_module
from market.binance_market.dex_market_row_helpers import (
    _basket_rows_from_extremes,
    _cow_flashloan_control_mode,
    _cow_supported_market_rows,
    _cow_supported_union_market_rows,
    _eligible_market_rows,
    _market_filter_reason_counts,
    _price_filtered_market_rows,
    _rank_supported_extremes,
    _rows_by_symbol,
    _safe_float,
    _stable_symbol,
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

from market.binance_market.market_snapshot import (
    _binance_price_row,
    _build_window_extremes_from_rows,
    _candidate_pair_count,
    _current_event_ms,
    _iso_to_event_ms,
    _market_basket_symbol_count,
    _previous_window_snapshot,
    _snapshot_age_seconds,
    _snapshot_event_ms,
    build_binance_rest_market_snapshot,
    fetch_binance_current_price_rows,
    needs_binance_rest_snapshot,
    read_binance_market_snapshot,
    select_binance_market_extremes,
    write_binance_market_snapshot,
)

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


def _load_avalanche_v3_pool_filter(cache_path: Path = DEFAULT_AVALANCHE_V3_POOL_CACHE_PATH) -> dict[str, Any]:
    return pool_support_module._load_avalanche_v3_pool_filter(cache_path)


def _load_bnb_pool_filter(cache_path: Path = DEFAULT_BNB_POOL_CACHE_PATH) -> dict[str, Any]:
    return pool_support_module._load_bnb_pool_filter(cache_path)


def _pool_filter_for_network(network: str | None) -> dict[str, Any] | None:
    selected = str(network or "").strip().lower()
    if selected == "avalanche":
        return _load_avalanche_v3_pool_filter()
    if selected == "bnb":
        return _load_bnb_pool_filter()
    return None


def _route_price(row: dict[str, Any] | None, phase: str) -> Decimal | None:
    if row is None:
        return None
    key = "start_price" if phase == "start" else "current_price"
    return _decimal_value(row.get(key))


def _binance_execution_plan(
    symbols: list[str],
    x: dict[str, Any],
    y: dict[str, Any],
    amount: str | int | float | Decimal,
    *,
    slippage_bps: int = DEFAULT_EXECUTION_SLIPPAGE_BPS,
) -> dict[str, Any]:
    initial_amount = _decimal_value(amount)
    slippage = max(0, min(int(slippage_bps), 5000))
    slippage_factor = Decimal(10000 - slippage) / Decimal(10000)
    rows = {
        str(x.get("base_symbol") or "").upper(): x,
        str(y.get("base_symbol") or "").upper(): y,
    }
    if initial_amount is None or initial_amount <= 0 or len(symbols) < 2:
        return {"available": False, "reason": "invalid_initial_amount", "slippage_bps": slippage}

    current_amount = initial_amount
    steps = []
    for index, (from_symbol, to_symbol) in enumerate(zip(symbols, symbols[1:]), start=1):
        from_key = str(from_symbol or "").upper()
        to_key = str(to_symbol or "").upper()
        target_price_candidate = None
        acceptable_price_candidate = None
        target_rate_candidate = None
        acceptable_rate_candidate = None
        price_candidates: list[dict[str, Any]] = []
        rate_candidates: list[dict[str, Any]] = []
        if _stable_symbol(from_key) and not _stable_symbol(to_key):
            phase = "current"
            price_basis = "current_binance_buy_low"
            rule = "buy target token at the lowest of previous, current, and query prices"
            target_role = "buy_price_ceiling"
            target_price_usd = None
            selection_rule = "use lowest eligible price as target for CoW query"
        elif not _stable_symbol(from_key) and not _stable_symbol(to_key):
            phase = "start"
            price_basis = "pre_change_binance_cross"
            rule = "cross tokens at the best output ratio among previous, current, and query ratios"
            target_role = "network_optimized_reference"
            target_price_usd = None
            selection_rule = "use highest cross ratio as target for CoW query"
        elif not _stable_symbol(from_key) and _stable_symbol(to_key):
            phase = "current"
            price_basis = "current_binance_sell_high"
            rule = "sell target token at the highest of previous, current, and query prices"
            target_role = "sell_price_floor"
            target_price_usd = None
            selection_rule = "use highest eligible sell price as target for CoW query"
        else:
            phase = "current"
            price_basis = "stable_reference"
            rule = "stable reference conversion"
            target_role = "stable_reference"
            target_price_usd = None
            selection_rule = "stable reference: target near 1:1"

        from_price = Decimal("1") if _stable_symbol(from_key) else _route_price(rows.get(from_key), phase)
        to_price = Decimal("1") if _stable_symbol(to_key) else _route_price(rows.get(to_key), phase)
        if from_price is None or to_price is None or from_price <= 0 or to_price <= 0:
            return {
                "available": False,
                "reason": f"missing_{price_basis}_price",
                "route": symbols,
                "initial_amount": _decimal_text(initial_amount),
                "initial_symbol": symbols[0],
                "slippage_bps": slippage,
                "steps": steps,
            }

        if target_role == "buy_price_ceiling":
            price_candidates = _market_price_candidates(to_key, rows, query_price=None)
            target_price_candidate = _select_decimal_candidate(price_candidates, "price", "min")
            acceptable_price_candidate = _select_decimal_candidate(price_candidates, "price", "median") or target_price_candidate
            target_price_usd = target_price_candidate.get("price") if target_price_candidate else to_price
            acceptable_price_usd = acceptable_price_candidate.get("price") if acceptable_price_candidate else target_price_usd / slippage_factor
            target_output = current_amount / target_price_usd
            min_output = current_amount / acceptable_price_usd
            target_exchange_rate = target_output / current_amount if current_amount > 0 else None
            min_exchange_rate = min_output / current_amount if current_amount > 0 else None
            cow_parameter_rule = "fixed sellAmountBeforeFee with accepted slippage for buy targets"
            price_compare_rule = "涔板叆浣跨敤涓変环鏈€浣庝綔涓虹洰鏍囷紝涓変环涓棿鍊间綔涓哄彲鎺ュ彈婊戠偣"
        elif target_role == "sell_price_floor":
            price_candidates = _market_price_candidates(from_key, rows, query_price=None)
            target_price_candidate = _select_decimal_candidate(price_candidates, "price", "max")
            acceptable_price_candidate = _select_decimal_candidate(price_candidates, "price", "median") or target_price_candidate
            target_price_usd = target_price_candidate.get("price") if target_price_candidate else from_price
            acceptable_price_usd = acceptable_price_candidate.get("price") if acceptable_price_candidate else target_price_usd * slippage_factor
            target_output = current_amount * target_price_usd
            min_output = current_amount * acceptable_price_usd
            target_exchange_rate = target_output / current_amount if current_amount > 0 else None
            min_exchange_rate = min_output / current_amount if current_amount > 0 else None
            cow_parameter_rule = "fixed sellAmountBeforeFee with accepted slippage for buy targets"
            price_compare_rule = "鍗栧嚭浣跨敤涓変环鏈€楂樹綔涓虹洰鏍囷紝涓変环涓棿鍊间綔涓哄彲鎺ュ彈婊戠偣"
        elif target_role == "network_optimized_reference":
            rate_candidates = _exchange_rate_candidates(from_key, to_key, rows, query_rate=None)
            target_rate_candidate = _select_decimal_candidate(rate_candidates, "rate", "max")
            acceptable_rate_candidate = target_rate_candidate
            target_exchange_rate = target_rate_candidate.get("rate") if target_rate_candidate else from_price / to_price
            min_exchange_rate = acceptable_rate_candidate.get("rate") if acceptable_rate_candidate else target_exchange_rate * slippage_factor
            target_output = current_amount * target_exchange_rate
            min_output = current_amount * min_exchange_rate
            target_price_usd = None
            acceptable_price_usd = None
            cow_parameter_rule = "sellAmountBeforeFee 鍥哄畾杈撳叆锛涗腑闂?hop 鐢ㄦ渶楂樺厬鎹㈡瘮渚嬪仛鐩爣锛岀敤涓棿鍏戞崲姣斾緥鍋氬彲鎺ュ彈婊戠偣"
            price_compare_rule = "涓棿鍏戞崲浣跨敤涓夌鍏戞崲姣斾緥涓殑鏈€楂樺€硷紝瀹為檯璺敱浜ょ粰 CoW/network 浼樺寲"
        else:
            target_output = current_amount * from_price / to_price
            min_output = target_output * slippage_factor
            target_exchange_rate = target_output / current_amount if current_amount > 0 else None
            min_exchange_rate = min_output / current_amount if current_amount > 0 else None
            target_price_usd = None
            acceptable_price_usd = None
            cow_parameter_rule = "stable reference"
            price_compare_rule = "stable reference"
        cow_sdk_parameters = {
            "quote_kind": "sell",
            "sell_token_symbol": from_key,
            "buy_token_symbol": to_key,
            "sell_amount_before_fee": _decimal_text(current_amount),
            "target_buy_amount_after_fee": _decimal_text(target_output),
            "min_buy_amount_after_fee": _decimal_text(min_output) if target_role in {"buy_price_ceiling", "sell_price_floor", "network_optimized_reference"} else None,
            "target_exchange_rate": _decimal_text(target_exchange_rate),
            "min_exchange_rate": _decimal_text(min_exchange_rate) if target_role in {"buy_price_ceiling", "sell_price_floor", "network_optimized_reference"} else None,
            "target_price_usd_per_token": _decimal_text(target_price_usd),
            "acceptable_price_usd_per_token": _decimal_text(acceptable_price_usd),
            "selected_target_source": _selected_source(target_price_candidate or target_rate_candidate),
            "selected_acceptable_source": _selected_source(acceptable_price_candidate or acceptable_rate_candidate),
            "limit_enabled": target_role in {"buy_price_ceiling", "sell_price_floor", "network_optimized_reference"},
        }
        steps.append(
            {
                "step": index,
                "from_symbol": from_key,
                "to_symbol": to_key,
                "input_amount": _decimal_text(current_amount),
                "target_output_amount": _decimal_text(target_output),
                "min_output_amount": _decimal_text(min_output),
                "target_exchange_rate": _decimal_text(target_exchange_rate),
                "min_exchange_rate": _decimal_text(min_exchange_rate),
                "input_price_usd": _decimal_text(from_price),
                "output_price_usd": _decimal_text(to_price),
                "price_basis": price_basis,
                "price_phase": phase,
                "rule": rule,
                "target_role": target_role,
                "target_price_usd_per_token": _decimal_text(target_price_usd),
                "acceptable_price_usd_per_token": _decimal_text(acceptable_price_usd),
                "price_candidates": _serialize_decimal_candidates(price_candidates, "price"),
                "rate_candidates": _serialize_decimal_candidates(rate_candidates, "rate"),
                "selected_target_source": _selected_source(target_price_candidate or target_rate_candidate),
                "selected_target_price_usd_per_token": _selected_decimal_text(target_price_candidate, "price"),
                "selected_target_exchange_rate": _selected_decimal_text(target_rate_candidate, "rate") or _decimal_text(target_exchange_rate),
                "selected_acceptable_source": _selected_source(acceptable_price_candidate or acceptable_rate_candidate),
                "acceptable_slippage_price_usd_per_token": _selected_decimal_text(acceptable_price_candidate, "price"),
                "acceptable_slippage_exchange_rate": _selected_decimal_text(acceptable_rate_candidate, "rate") or _decimal_text(min_exchange_rate),
                "cow_parameter_rule": cow_parameter_rule,
                "price_compare_rule": price_compare_rule,
                "selection_rule": selection_rule,
                "cow_sdk_parameters": cow_sdk_parameters,
                "cow_limit_enabled": target_role in {"buy_price_ceiling", "sell_price_floor", "network_optimized_reference"},
                "slippage_bps": slippage,
                "slippage_percent": _decimal_text(Decimal(slippage) / Decimal(100)),
            }
        )
        current_amount = min_output

    profit_amount = current_amount - initial_amount if _stable_symbol(symbols[0]) and _stable_symbol(symbols[-1]) else None
    profit_percent = profit_amount / initial_amount * Decimal(100) if profit_amount is not None and initial_amount > 0 else None
    return {
        "available": True,
        "route": symbols,
        "initial_amount": _decimal_text(initial_amount),
        "initial_symbol": symbols[0],
        "final_target_amount": _decimal_text(current_amount),
        "final_symbol": symbols[-1],
        "profit_amount": _decimal_text(profit_amount),
        "profit_percent": _decimal_text(profit_percent),
        "slippage_bps": slippage,
        "slippage_factor": _decimal_text(slippage_factor),
        "slippage_percent": _decimal_text(Decimal(slippage) / Decimal(100)),
        "market_prices": [
            {
                "symbol": symbol,
                "start_price": _decimal_text(_decimal_value(row.get("start_price"))),
                "current_price": _decimal_text(_decimal_value(row.get("current_price"))),
                "change_percent": row.get("change_percent"),
                "price_source": row.get("price_source"),
            }
            for symbol, row in rows.items()
        ],
        "pricing_rules": [
            "USDC -> token uses current Binance price with slippage",
            "token -> token uses pre-change Binance prices with slippage",
            "token -> USDC uses current Binance price with slippage",
        ],
        "steps": steps,
    }


def _route_text(route: dict[str, Any] | None, fallback: list[str]) -> str:
    symbols = None
    if isinstance(route, dict):
        symbols = route.get("route_symbols") or route.get("route")
    if not isinstance(symbols, list) or not symbols:
        symbols = fallback
    return " -> ".join(str(symbol) for symbol in symbols if symbol)


def _candidate_route_symbols(route: tuple[str, ...], x: dict[str, Any], y: dict[str, Any]) -> list[str]:
    rows = {"x": x, "y": y}
    symbols = []
    for item in route:
        symbols.append(item if item in {"USDC", "USDT"} else rows[item]["base_symbol"])
    return symbols


def _route_candidate(
    strategy: str,
    route: tuple[str, ...],
    priority_reason: str,
    x: dict[str, Any],
    y: dict[str, Any],
    route_no: int,
    initial_amount: str | int | float | Decimal,
    slippage_bps: int = DEFAULT_EXECUTION_SLIPPAGE_BPS,
) -> dict[str, Any]:
    symbols = _candidate_route_symbols(route, x, y)
    edge_hint_percent = _min_abs_change_percent(x, y)
    execution_plan = _binance_execution_plan(symbols, x, y, initial_amount, slippage_bps=slippage_bps)
    return {
        "route_no": route_no,
        "strategy": strategy,
        "route": symbols,
        "route_text": _route_text({"route": symbols}, []),
        "hop_count": max(0, len(symbols) - 1),
        "route_depth_label": f"{max(0, len(symbols) - 1)} hop",
        "steps": [],
        "initial_symbol": symbols[0] if symbols else None,
        "initial_amount": str(initial_amount),
        "remaining_amount": None,
        "profit_amount": None,
        "profit_percent": None,
        "owed_amount_with_premium": None,
        "net_after_flashloan_amount": None,
        "net_after_flashloan_percent": None,
        "net_after_flashloan_usd": None,
        "profitable": False,
        "quote_required": True,
        "estimation_available": False,
        "candidate_basis": "stablecoin_closed_route_requires_cow_or_dex_quote",
        "edge_hint_percent": edge_hint_percent,
        "priority_reason": priority_reason,
        "binance_execution_plan": execution_plan,
    }


def _min_abs_change_percent(x: dict[str, Any], y: dict[str, Any]) -> float | None:
    x_change = _safe_float(x.get("change_percent"))
    y_change = _safe_float(y.get("change_percent"))
    if x_change is None or y_change is None:
        return None
    return min(abs(x_change), abs(y_change))


def _pair_quote_candidates(
    x: dict[str, Any],
    y: dict[str, Any],
    config: ArbitrageConfig,
    slippage_bps: int = DEFAULT_EXECUTION_SLIPPAGE_BPS,
) -> dict[str, Any]:
    fallback_route = ["USDC", y["base_symbol"], x["base_symbol"], "USDC"]
    row = {
        "rank": 0,
        "pair": f"{x['symbol']} / {y['symbol']}",
        "x_symbol": x["symbol"],
        "y_symbol": y["symbol"],
        "x_base_symbol": x["base_symbol"],
        "y_base_symbol": y["base_symbol"],
        "x_change_percent": x["change_percent"],
        "y_change_percent": y["change_percent"],
        "x_start_price": x.get("start_price"),
        "x_current_price": x.get("current_price"),
        "y_start_price": y.get("start_price"),
        "y_current_price": y.get("current_price"),
        "window_spread_percent": (
            float(x["change_percent"]) - float(y["change_percent"])
            if x["change_percent"] is not None and y["change_percent"] is not None
            else None
        ),
        "edge_hint_percent": _min_abs_change_percent(x, y),
        "candidate_basis": "binance_token_names_only",
        "trigger_source": "binance_realtime_symbols",
        "route": fallback_route,
        "route_text": _route_text(None, fallback_route),
        "route_results": [],
        "route_results_full": [],
        "route_count": 0,
        "full_route_count": 0,
        "quote_verified": False,
        "quote_required": True,
        "estimation_available": False,
        "profit_usd": None,
        "profit_percent": None,
        "best_route": None,
        "best_route_no": None,
        "best_strategy": None,
        "profitable": False,
        "blocked_reasons": ["requires_cow_or_dex_quote"],
    }
    route_results = [
        _route_candidate(strategy, route, priority_reason, x, y, index, config.notional_usd, slippage_bps=slippage_bps)
        for index, (strategy, route, priority_reason) in enumerate(STABLE_ROUTE_CANDIDATES, start=1)
    ]
    row.update(
        {
            "strategy": None,
            "route_results": route_results,
            "route_results_full": route_results,
            "route_count": len(route_results),
            "full_route_count": len(route_results),
            "strategy_analysis": build_pair_strategy_analysis(
                x,
                y,
                config,
                initial_amount=config.notional_usd,
            ),
            "borrow_symbol": None,
            "swap_symbol": None,
        }
    )
    return row


def _cow_quote_error_info(error: Any) -> dict[str, Any]:
    text = str(error or "").strip()
    lowered = text.lower()
    if not text:
        return {"type": None, "display": None, "retryable": False, "raw": None}
    if "403 error" in lowered and ("cloudfront" in lowered or "request blocked" in lowered):
        return {
            "type": "quote_api_http_403_cloudfront_request_blocked",
            "display": "CoW quote API HTTP 403: CloudFront request blocked",
            "retryable": True,
            "raw": text,
        }
    if "timed out" in lowered or "timeout" in lowered:
        return {
            "type": "quote_api_timeout",
            "display": "CoW quote API timeout",
            "retryable": True,
            "raw": text,
        }
    if "ssl" in lowered:
        return {
            "type": "quote_api_ssl_error",
            "display": "CoW quote API SSL error",
            "retryable": True,
            "raw": text,
        }
    if "http" in lowered or "<html" in lowered or "<!doctype" in lowered:
        return {
            "type": "quote_api_http_error",
            "display": "CoW quote API HTTP error",
            "retryable": True,
            "raw": text,
        }
    return {
        "type": "quote_api_error",
        "display": text[:240],
        "retryable": True,
        "raw": text,
    }




from market.binance_market.cow_surface import (
    _apply_cow_quote_analysis,
    _apply_cow_quote_targets,
    _attach_cow_flashloan_sdk_plan,
    _cow_cost_summary,
    _cow_execution_precheck,
    _cow_min_profit_usd_for_amount,
    _cow_order_submission_adapter_available,
    _cow_order_submission_enabled,
    _cow_order_submission_requested,
    _cow_order_submission_sdk_ready,
    _cow_order_submission_sdk_status,
    _cow_own_limit_order_intent,
    _cow_pure_profit_intent,
    _cow_query_price_for_step,
    _cow_query_rate_for_step,
    _cow_route_specs,
    _cow_sdk_result_snapshot,
    build_cow_network_market_claims,
    build_cow_route_precheck,
    build_cow_supported_market_overview,
    _execution_plan_rows,
)
from market.binance_market.quote_service import build_binance_market_state, build_cow_quote_verification
from market.binance_market.service_cow_network import _cow_route_support, cow_network_options
