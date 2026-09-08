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
from market.binance_market.cow_surface import (
    _cow_min_profit_usd_for_amount,
    _cow_order_submission_adapter_available,
    _cow_order_submission_requested,
    _cow_order_submission_sdk_status,
    _cow_own_limit_order_intent,
    _cow_query_price_for_step,
    _cow_query_rate_for_step,
    _execution_plan_rows,
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


def _cow_order_submission_adapter_available_compat() -> bool:
    from market.binance_market import service

    compatibility = getattr(service, "_cow_order_submission_adapter_available", None)
    if callable(compatibility) and getattr(compatibility, "__module__", "").startswith("market.binance_market.cow_surface"):
        return __legacy_cow_order_submission_adapter_available()
    if callable(compatibility):
        return bool(compatibility())
    return _cow_order_submission_adapter_available()


def _cow_order_submission_sdk_status_compat() -> dict[str, Any]:
    from market.binance_market import service

    compatibility = getattr(service, "_cow_order_submission_sdk_status", None)
    if callable(compatibility) and getattr(compatibility, "__module__", "").startswith("market.binance_market.cow_surface"):
        return __legacy_cow_order_submission_sdk_status()
    if callable(compatibility):
        return compatibility()
    return _cow_order_submission_sdk_status()
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



def _binance_execution_plan(*args, **kwargs):
    from market.binance_market import service
    return service._binance_execution_plan(*args, **kwargs)



def _candidate_route_symbols(*args, **kwargs):
    from market.binance_market import service
    return service._candidate_route_symbols(*args, **kwargs)



def _cow_flashloan_control_mode(*args, **kwargs):
    from market.binance_market import service
    return service._cow_flashloan_control_mode(*args, **kwargs)



def _cow_quote_error_info(*args, **kwargs):
    from market.binance_market import service
    return service._cow_quote_error_info(*args, **kwargs)



def _stable_symbol(*args, **kwargs):
    from market.binance_market import service
    return service._stable_symbol(*args, **kwargs)



def __legacy_cow_route_specs(pair: dict[str, Any], amount: str | int | float | Decimal) -> list[dict[str, Any]]:
    x = str(pair.get("x_base_symbol") or "").upper()
    y = str(pair.get("y_base_symbol") or "").upper()
    if not x or not y or x == y:
        return []
    existing_plans = {
        str(route.get("priority_reason") or ""): route.get("binance_execution_plan")
        for route in pair.get("route_results") or []
        if isinstance(route, dict)
    }
    slippage_bps = DEFAULT_EXECUTION_SLIPPAGE_BPS
    for plan in existing_plans.values():
        if isinstance(plan, dict) and plan.get("slippage_bps") is not None:
            try:
                slippage_bps = int(plan["slippage_bps"])
                break
            except (TypeError, ValueError):
                slippage_bps = DEFAULT_EXECUTION_SLIPPAGE_BPS
    x_row = {
        "base_symbol": x,
        "start_price": pair.get("x_start_price"),
        "current_price": pair.get("x_current_price"),
    }
    y_row = {
        "base_symbol": y,
        "start_price": pair.get("y_start_price"),
        "current_price": pair.get("y_current_price"),
    }
    specs = []
    for strategy, route_template, priority_reason in STABLE_ROUTE_CANDIDATES:
        path = _candidate_route_symbols(route_template, x_row, y_row)
        if not path:
            continue
        specs.append(
            {
                "name": f"{pair.get('rank', 0)}_{strategy}_{'_'.join(path)}",
                "path": path,
                "amount": amount,
                "pair_rank": pair.get("rank"),
                "pair": pair.get("pair"),
                "priority_reason": priority_reason,
                "edge_hint_percent": pair.get("edge_hint_percent"),
                "binance_execution_plan": _binance_execution_plan(path, x_row, y_row, amount, slippage_bps=slippage_bps),
            }
        )
    return specs

def __legacy_cow_cost_summary(result: dict[str, Any], *, final_delta_amount: str | None) -> dict[str, Any]:
    fee_amounts = [
        {
            "hop": hop.get("hop"),
            "sell_symbol": hop.get("sell_symbol"),
            "fee_amount": hop.get("fee_amount"),
            "fee_amount_units": hop.get("fee_amount_units"),
        }
        for hop in result.get("hops") or []
        if isinstance(hop, dict)
    ]
    return {
        "native_balance_before": None,
        "native_balance_after": None,
        "native_balance_source": "not_checked_in_quote_stage",
        "quote_api_gas_used": 0,
        "user_order_submission_gas_used": 0,
        "settlement_gas_payer": "solver",
        "approval_gas_status": "requires_allowance_check_before_execution",
        "execution_gas_used": None,
        "execution_gas_source": "available_after_onchain_receipt",
        "cow_fee_amounts": fee_amounts,
        "profit_amount": None,
        "profit_symbol": None,
        "profit_prediction_disabled": True,
        "profit_source": "sdk_settlement_receipt_only",
        "quoted_final_amount": result.get("final_amount"),
        "quoted_final_symbol": result.get("final_symbol"),
    }

def __legacy_cow_min_profit_usd_for_amount(amount: Any) -> Decimal:
    notional = _decimal_value(amount)
    if notional is None or notional <= 0:
        notional = Decimal("1000")
    return notional * DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT / Decimal("100")

def __legacy_cow_pure_profit_intent(
    *,
    amount: Any,
    input_symbol: Any,
    final_symbol: Any,
    path: Any,
    owner: str | None,
    cow_network: str,
    cow_chain_id: int,
    threshold_detail: dict[str, Any] | None,
    market_state: dict[str, Any] | None,
    link_name: Any | None = None,
) -> dict[str, Any]:
    threshold_detail = threshold_detail or {}
    expected_profit = _decimal_value(threshold_detail.get("min_pure_profit_amount"))
    if expected_profit is None:
        expected_profit = _decimal_value(threshold_detail.get("min_profit_usd"))
    if expected_profit is None:
        configured_min_profit_percent = _decimal_value(threshold_detail.get("min_profit_percent")) or DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT
        notional = _decimal_value(amount)
        if notional is None or notional <= 0:
            notional = Decimal("1000")
        expected_profit = notional * configured_min_profit_percent / Decimal("100")
    route_label = str(link_name or "").strip() or "->".join(
        str(part or "").strip().upper() for part in (path or []) if str(part or "").strip()
    )
    state = market_state if isinstance(market_state, dict) else {}
    result = build_triangular_onchain_intent_trade(
        route_label,
        expected_profit,
        state.get("cow_top") or state.get("top") or [],
        state.get("cow_bottom") or state.get("bottom") or [],
    )
    return bind_cow_intent_context(
        result,
        requested_amount=amount,
        input_symbol=input_symbol,
        final_symbol=final_symbol,
        owner=owner,
        cow_network=cow_network,
        cow_chain_id=cow_chain_id,
    )

def __legacy_cow_own_limit_order_intent(plan: dict[str, Any] | None, *, min_profit_usd: Decimal) -> dict[str, Any]:
    if not isinstance(plan, dict) or not plan.get("available"):
        return {
            "ready": False,
            "reason": "plan_unavailable",
            "expected_profit_amount": None,
            "expected_profit_percent": None,
            "steps": [],
        }
    profit_amount = _decimal_value(plan.get("profit_amount"))
    profit_percent = _decimal_value(plan.get("profit_percent"))
    steps = []
    missing = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        sdk = step.get("cow_sdk_parameters") if isinstance(step.get("cow_sdk_parameters"), dict) else {}
        sell_amount = _decimal_value(sdk.get("sell_amount_before_fee"))
        min_buy = _decimal_value(sdk.get("min_buy_amount_after_fee") or step.get("min_output_amount"))
        target_buy = _decimal_value(sdk.get("target_buy_amount_after_fee") or step.get("target_output_amount"))
        role = str(step.get("target_role") or "")
        step_ready = sell_amount is not None and sell_amount > 0 and min_buy is not None and min_buy > 0
        if role in {"buy_price_ceiling", "sell_price_floor", "network_optimized_reference"} and not step_ready:
            missing.append(str(step.get("step") or len(steps) + 1))
        steps.append(
            {
                "step": step.get("step"),
                "from_symbol": step.get("from_symbol"),
                "to_symbol": step.get("to_symbol"),
                "target_role": role,
                "sell_amount_before_fee": _decimal_text(sell_amount),
                "target_buy_amount_after_fee": _decimal_text(target_buy),
                "min_buy_amount_after_fee": _decimal_text(min_buy),
                "selected_target_source": step.get("selected_target_source"),
                "selected_acceptable_source": step.get("selected_acceptable_source"),
                "target_price_usd_per_token": step.get("selected_target_price_usd_per_token") or step.get("target_price_usd_per_token"),
                "acceptable_price_usd_per_token": step.get("acceptable_slippage_price_usd_per_token") or step.get("acceptable_price_usd_per_token"),
                "target_exchange_rate": step.get("selected_target_exchange_rate") or step.get("target_exchange_rate"),
                "min_exchange_rate": step.get("acceptable_slippage_exchange_rate") or step.get("min_exchange_rate"),
                "ready": step_ready,
                "rule": step.get("price_compare_rule") or step.get("selection_rule"),
            }
        )
    stable_final = _stable_symbol(str(plan.get("final_symbol") or ""))
    ready = bool(steps) and not missing and stable_final
    reason = "ready" if ready else (
        "missing_limit_parameters" if missing else "non_stable_final_symbol"
    )
    return {
        "ready": ready,
        "reason": reason,
        "missing_steps": missing,
        "expected_profit_amount": _decimal_text(profit_amount),
        "expected_profit_percent": _decimal_text(profit_percent),
        "min_profit_usd": _decimal_text(min_profit_usd),
        "final_symbol": plan.get("final_symbol"),
        "order_mode": "own_window_price_limit_intent",
        "quote_dependency": "quote_is_analysis_only; own_limit_params_remain_authoritative",
        "cow_sdk_boundary": "official CoW Flash Loans SDK order hooks are required before any submission",
        "steps": steps,
    }

def __legacy_cow_sdk_result_snapshot(result: dict[str, Any], precheck: dict[str, Any]) -> dict[str, Any]:
    submission = result.get("cow_submission_result") if isinstance(result.get("cow_submission_result"), dict) else {}
    return {
        "status": str(precheck.get("status") or "quote_precheck"),
        "controller": "cow_sdk",
        "control_mode": _cow_flashloan_control_mode(),
        "route_hop_constraints_enforced": bool(precheck.get("route_hop_constraints_enforced")),
        "route_hop_constraints_passed": bool(precheck.get("route_hop_constraints_passed")),
        "ready": bool(precheck.get("checks_passed")),
        "intent_ready": bool(precheck.get("intent_mode_ready")),
        "quote_available": bool(precheck.get("quote_available")),
        "route_supported": bool(precheck.get("route_supported")),
        "quote_error_type": precheck.get("quote_error_type"),
        "quote_error_display": precheck.get("quote_error_display"),
        "quote_error_raw": precheck.get("quote_error_raw"),
        "reason_count": len(precheck.get("reasons") or []),
        "submission_attempted": bool(precheck.get("submission_attempted") or submission.get("submitted") or submission.get("order_id")),
        "submission_status": submission.get("status") or precheck.get("submission_status"),
        "submission_order_id": submission.get("order_id") or precheck.get("submission_order_id"),
        "submission_tx_hash": submission.get("tx_hash") or precheck.get("submission_tx_hash"),
        "submission_error": submission.get("error"),
        "submission_blocked_reason": submission.get("blocked_reason"),
    }

def __legacy_cow_order_submission_requested() -> bool:
    return cow_order_submission_requested()

def __legacy_cow_order_submission_adapter_available() -> bool:
    return cow_order_submission_adapter_available()

def __legacy_cow_order_submission_sdk_ready() -> bool:
    return cow_order_submission_sdk_ready()

def __legacy_cow_order_submission_sdk_status() -> dict[str, Any]:
    return cow_order_submission_sdk_status()

def __legacy_cow_order_submission_enabled() -> bool:
    return cow_order_submission_enabled()

def __legacy_attach_cow_flashloan_sdk_plan(
    result: dict[str, Any],
    plan: dict[str, Any] | None,
    registry: dict[str, CowToken],
) -> None:
    if not isinstance(plan, dict) or not plan.get("available"):
        result["cow_flashloan_sdk_plan"] = None
        result["cow_flashloan_sdk_error"] = "available CoW SDK limit plan is required"
        return
    try:
        route = [str(item or "").upper() for item in plan.get("route") or []]
        tokens = []
        for symbol in route:
            token = resolve_token(symbol, registry)
            tokens.append(
                {
                    "symbol": token.symbol,
                    "address": token.address,
                    "decimals": token.decimals,
                    "source": token.source,
                }
            )
        steps = [
            {
                "step": step.get("step") or index + 1,
                "from_symbol": step.get("from_symbol"),
                "to_symbol": step.get("to_symbol"),
                "sell_amount_before_fee": (step.get("cow_sdk_parameters") or {}).get("sell_amount_before_fee")
                or step.get("query_sell_amount_before_fee")
                or step.get("input_amount"),
                "target_buy_amount_after_fee": (step.get("cow_sdk_parameters") or {}).get("target_buy_amount_after_fee")
                or step.get("target_output_amount"),
                "min_buy_amount_after_fee": (step.get("cow_sdk_parameters") or {}).get("min_buy_amount_after_fee")
                or step.get("min_output_amount"),
                "selected_target_source": step.get("selected_target_source"),
                "selected_acceptable_source": step.get("selected_acceptable_source"),
                "rule": step.get("selection_rule") or step.get("price_compare_rule"),
            }
            for index, step in enumerate(plan.get("steps") or [])
            if isinstance(step, dict)
        ]
        result["cow_flashloan_sdk_plan"] = {
            "sdk": "@cowprotocol/sdk-flash-loans",
            "flow": "AaveCollateralSwapSdk",
            "route": route,
            "tokens": tokens,
            "steps": steps,
            "single_solver_order_count": 1 if len(steps) >= 3 and route and route[0] == route[-1] else 0,
            "diagnostic_hop_count": len(steps),
            "single_step_order_count": len(steps),
            "submission_status": "not_submitted",
            "flashloan_capability": assess_cow_flashloan_sdk_plan(
                route=route,
                steps=steps,
                tokens=tokens,
                hops=result.get("hops") or [],
            ),
            "required_probe_methods": [
                "getSwapQuoteParams",
                "TradingSdk.getQuoteOnly",
                "getOrderPostingSettings",
            ],
            "required_submission_method": (
                "TradingSdk.getQuote + AaveCollateralSwapSdk.getOrderPostingSettings + quoteAndPost.postSwapOrderFromQuote"
                if len(steps) >= 3 and route and route[0] == route[-1]
                else "blocked: flashloan arbitrage requires a closed three-hop solver path"
            ),
            "settlement_boundary": "official CoW solver settlement; no self-deployed contract required for current intent mode",
        }
        result["cow_flashloan_sdk_error"] = None
    except Exception as exc:
        result["cow_flashloan_sdk_plan"] = None
        result["cow_flashloan_sdk_error"] = str(exc)

def __legacy_execution_plan_rows(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("symbol") or "").upper(): item
        for item in plan.get("market_prices") or []
        if isinstance(item, dict) and item.get("symbol")
    }

def __legacy_cow_query_price_for_step(step: dict[str, Any], hop: dict[str, Any] | None) -> Decimal | None:
    if not hop:
        return None
    sell_amount = _decimal_value(hop.get("sell_amount"))
    buy_amount = _decimal_value(hop.get("buy_amount"))
    if sell_amount is None or buy_amount is None or sell_amount <= 0 or buy_amount <= 0:
        return None
    from_symbol = str(step.get("from_symbol") or "").upper()
    to_symbol = str(step.get("to_symbol") or "").upper()
    if _stable_symbol(from_symbol) and not _stable_symbol(to_symbol):
        return sell_amount / buy_amount
    if not _stable_symbol(from_symbol) and _stable_symbol(to_symbol):
        return buy_amount / sell_amount
    return None

def __legacy_cow_query_rate_for_step(step: dict[str, Any], hop: dict[str, Any] | None) -> Decimal | None:
    if not hop:
        return None
    rate = _decimal_value(hop.get("exchange_rate"))
    if rate is not None and rate > 0:
        return rate
    sell_amount = _decimal_value(hop.get("sell_amount"))
    buy_amount = _decimal_value(hop.get("buy_amount"))
    if sell_amount is None or buy_amount is None or sell_amount <= 0 or buy_amount <= 0:
        return None
    return buy_amount / sell_amount

def __legacy_cow_quote_step_context(
    step: dict[str, Any],
    rows: dict[str, dict[str, Any]],
    hop: dict[str, Any] | None,
) -> dict[str, Any] | None:
    role = str(step.get("target_role") or "")
    from_symbol = str(step.get("from_symbol") or "").upper()
    to_symbol = str(step.get("to_symbol") or "").upper()
    sell_amount = _decimal_value(hop.get("sell_amount") if hop else None) or _decimal_value(step.get("input_amount"))
    if sell_amount is None or sell_amount <= 0:
        return None
    query_price = _cow_query_price_for_step(step, hop)
    query_rate = _cow_query_rate_for_step(step, hop)
    price_candidates: list[dict[str, Any]] = []
    rate_candidates: list[dict[str, Any]] = []
    target_price_candidate = None
    acceptable_price_candidate = None
    target_rate_candidate = None
    acceptable_rate_candidate = None
    target_value = None
    acceptable_value = None
    target_amount = None
    min_amount = None
    query_field = None
    quote_fields: dict[str, Any] = {}
    selection_rule = ""

    if role == "buy_price_ceiling":
        price_candidates = _market_price_candidates(to_symbol, rows, query_price=query_price)
        target_price_candidate = _select_decimal_candidate(price_candidates, "price", "min")
        acceptable_price_candidate = _select_decimal_candidate(price_candidates, "price", "median") or target_price_candidate
        target_value = target_price_candidate.get("price") if target_price_candidate else None
        acceptable_value = acceptable_price_candidate.get("price") if acceptable_price_candidate else None
        target_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=target_value)
        min_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=acceptable_value)
        query_field = "query_price_usd_per_token"
        selection_rule = "买入：三价取最低价作为目标价，中间价作为可接受滑点价"
        quote_fields = {
            "query_price_usd_per_token": _decimal_text(query_price),
            "target_price_usd_per_token": _decimal_text(target_value),
            "acceptable_price_usd_per_token": _decimal_text(acceptable_value),
            "selected_target_price_usd_per_token": _decimal_text(target_value),
            "acceptable_slippage_price_usd_per_token": _decimal_text(acceptable_value),
            "selected_target_exchange_rate": _decimal_text((target_amount / sell_amount) if target_amount is not None else None),
            "acceptable_slippage_exchange_rate": _decimal_text((min_amount / sell_amount) if min_amount is not None else None),
            "target_output_amount": _decimal_text(target_amount),
            "min_output_amount": _decimal_text(min_amount),
            "selection_rule": selection_rule,
        }
    elif role == "sell_price_floor":
        price_candidates = _market_price_candidates(from_symbol, rows, query_price=query_price)
        target_price_candidate = _select_decimal_candidate(price_candidates, "price", "max")
        acceptable_price_candidate = _select_decimal_candidate(price_candidates, "price", "median") or target_price_candidate
        target_value = target_price_candidate.get("price") if target_price_candidate else None
        acceptable_value = acceptable_price_candidate.get("price") if acceptable_price_candidate else None
        target_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=target_value)
        min_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=acceptable_value)
        query_field = "query_price_usd_per_token"
        selection_rule = "卖出：三价取最高价作为目标价，中间价作为可接受滑点价"
        quote_fields = {
            "query_price_usd_per_token": _decimal_text(query_price),
            "target_price_usd_per_token": _decimal_text(target_value),
            "acceptable_price_usd_per_token": _decimal_text(acceptable_value),
            "selected_target_price_usd_per_token": _decimal_text(target_value),
            "acceptable_slippage_price_usd_per_token": _decimal_text(acceptable_value),
            "selected_target_exchange_rate": _decimal_text((target_amount / sell_amount) if target_amount is not None else None),
            "acceptable_slippage_exchange_rate": _decimal_text((min_amount / sell_amount) if min_amount is not None else None),
            "target_output_amount": _decimal_text(target_amount),
            "min_output_amount": _decimal_text(min_amount),
            "selection_rule": selection_rule,
        }
    elif role == "network_optimized_reference":
        rate_candidates = _exchange_rate_candidates(from_symbol, to_symbol, rows, query_rate=query_rate)
        target_rate_candidate = _select_decimal_candidate(rate_candidates, "rate", "max")
        acceptable_rate_candidate = target_rate_candidate
        target_value = target_rate_candidate.get("rate") if target_rate_candidate else None
        acceptable_value = acceptable_rate_candidate.get("rate") if acceptable_rate_candidate else None
        target_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=target_value)
        min_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=acceptable_value)
        query_field = "query_exchange_rate"
        selection_rule = "中间兑换：三种兑换比例取最高比例作为目标，中间比例作为可接受滑点"
        quote_fields = {
            "query_exchange_rate": _decimal_text(query_rate),
            "target_exchange_rate": _decimal_text(target_value),
            "min_exchange_rate": _decimal_text(acceptable_value),
            "selected_target_exchange_rate": _decimal_text(target_value),
            "acceptable_slippage_exchange_rate": _decimal_text(acceptable_value),
            "target_output_amount": _decimal_text(target_amount),
            "min_output_amount": _decimal_text(min_amount),
            "selection_rule": selection_rule,
        }
    else:
        target_amount = _decimal_value(step.get("target_output_amount"))
        min_amount = _decimal_value(step.get("min_output_amount"))

    return {
        "role": role,
        "from_symbol": from_symbol,
        "to_symbol": to_symbol,
        "sell_amount": sell_amount,
        "query_price": query_price,
        "query_rate": query_rate,
        "query_field": query_field,
        "quote_fields": quote_fields,
        "price_candidates": _serialize_decimal_candidates(price_candidates, "price"),
        "rate_candidates": _serialize_decimal_candidates(rate_candidates, "rate"),
        "selected_target_source": _selected_source(target_price_candidate or target_rate_candidate),
        "selected_acceptable_source": _selected_source(acceptable_price_candidate or acceptable_rate_candidate),
        "target_value": target_value,
        "acceptable_value": acceptable_value,
        "target_amount": target_amount,
        "min_amount": min_amount,
        "selection_rule": selection_rule,
    }

def __legacy_apply_cow_quote_targets(plan: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or not plan.get("available"):
        return plan
    enriched = deepcopy(plan)
    rows = _execution_plan_rows(enriched)
    hops = [hop for hop in result.get("hops") or [] if isinstance(hop, dict)]
    for index, step in enumerate(enriched.get("steps") or []):
        if not isinstance(step, dict):
            continue
        hop = hops[index] if index < len(hops) else None
        context = __legacy_cow_quote_step_context(step, rows, hop)
        if context is None:
            continue
        step.update(context["quote_fields"])
        step["price_candidates"] = context["price_candidates"]
        step["rate_candidates"] = context["rate_candidates"]
        step["selected_target_source"] = context["selected_target_source"]
        step["selected_acceptable_source"] = context["selected_acceptable_source"]
        step["query_sell_amount_before_fee"] = _decimal_text(context["sell_amount"])
        step["query_buy_amount_after_fee"] = hop.get("buy_amount") if hop else None
        sdk_parameters = dict(step.get("cow_sdk_parameters") or {})
        sdk_parameters.update(
            {
                "quote_kind": "sell",
                "sell_token_symbol": context["from_symbol"],
                "buy_token_symbol": context["to_symbol"],
                "sell_amount_before_fee": _decimal_text(context["sell_amount"]),
                "target_buy_amount_after_fee": context["quote_fields"].get("target_output_amount")
                or _decimal_text(context["target_amount"]),
                "min_buy_amount_after_fee": context["quote_fields"].get("min_output_amount")
                or _decimal_text(context["min_amount"]),
                "query_buy_amount_after_fee": hop.get("buy_amount") if hop else None,
                "selected_target_source": step.get("selected_target_source"),
                "selected_acceptable_source": step.get("selected_acceptable_source"),
                "target_price_usd_per_token": step.get("target_price_usd_per_token"),
                "acceptable_price_usd_per_token": step.get("acceptable_price_usd_per_token"),
                "target_exchange_rate": step.get("target_exchange_rate"),
                "min_exchange_rate": step.get("min_exchange_rate"),
                "limit_enabled": True,
            }
        )
        step["cow_sdk_parameters"] = sdk_parameters
        step["cow_limit_enabled"] = True
    enriched["quote_parameter_selection"] = "CoW 查询价已加入前期窗口价、当前窗口价一起比较"
    return enriched

def __legacy_apply_cow_quote_analysis(plan: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or not plan.get("available"):
        return plan
    enriched = deepcopy(plan)
    rows = _execution_plan_rows(enriched)
    hops = [hop for hop in result.get("hops") or [] if isinstance(hop, dict)]
    for index, step in enumerate(enriched.get("steps") or []):
        if not isinstance(step, dict):
            continue
        hop = hops[index] if index < len(hops) else None
        context = __legacy_cow_quote_step_context(step, rows, hop)
        if context is None:
            continue
        role = context["role"]
        if context["query_field"] == "query_price_usd_per_token":
            price_candidates = _market_price_candidates(context["to_symbol"] if role == "buy_price_ceiling" else context["from_symbol"], rows, query_price=context["query_price"])
            target_value = _decimal_value(step.get("selected_target_price_usd_per_token") or step.get("target_price_usd_per_token"))
            acceptable_value = _decimal_value(step.get("acceptable_slippage_price_usd_per_token") or step.get("acceptable_price_usd_per_token"))
            step["query_price_usd_per_token"] = _decimal_text(context["query_price"])
            step["query_price_position"] = _decimal_rank_analysis(price_candidates, "price")
            step["query_window_timing"] = _query_window_timing(price_candidates, "price", query_value=context["query_price"])
            step["query_guard_analysis"] = _query_guard_analysis(
                role=role,
                query_value=context["query_price"],
                target_value=target_value,
                acceptable_value=acceptable_value,
            )
        elif context["query_field"] == "query_exchange_rate":
            rate_candidates = _exchange_rate_candidates(context["from_symbol"], context["to_symbol"], rows, query_rate=context["query_rate"])
            target_value = _decimal_value(step.get("selected_target_exchange_rate") or step.get("target_exchange_rate"))
            acceptable_value = _decimal_value(step.get("acceptable_slippage_exchange_rate") or step.get("min_exchange_rate"))
            step["query_exchange_rate"] = _decimal_text(context["query_rate"])
            step["query_rate_position"] = _decimal_rank_analysis(rate_candidates, "rate")
            step["query_window_timing"] = _query_window_timing(rate_candidates, "rate", query_value=context["query_rate"])
            step["query_guard_analysis"] = _query_guard_analysis(
                role=role,
                query_value=context["query_rate"],
                target_value=target_value,
                acceptable_value=acceptable_value,
            )
        step["price_candidates"] = context["price_candidates"]
        step["rate_candidates"] = context["rate_candidates"]
        step["query_sell_amount_before_fee"] = _decimal_text(context["sell_amount"])
        step["query_buy_amount_after_fee"] = hop.get("buy_amount") if hop else None
        sdk_parameters = dict(step.get("cow_sdk_parameters") or {})
        sdk_parameters["query_buy_amount_after_fee"] = hop.get("buy_amount") if hop else None
        step["cow_sdk_parameters"] = sdk_parameters
        step["cow_limit_enabled"] = True
    enriched["quote_parameter_selection"] = "own_window_prices_guard_order_parameters; query_quote_only_for_position_analysis"
    return enriched

def __legacy_cow_execution_precheck(result: dict[str, Any]) -> dict[str, Any]:
    plan = result.get("binance_execution_plan") if isinstance(result, dict) else None
    steps = plan.get("steps") if isinstance(plan, dict) else []
    hops = [hop for hop in result.get("hops") or [] if isinstance(hop, dict)]
    control_mode = _cow_flashloan_control_mode()
    quote_error = _cow_quote_error_info(result.get("error"))
    quote_error_type = quote_error.get("type")
    pure_intent = result.get("cow_flashloan_intent") if isinstance(result.get("cow_flashloan_intent"), dict) else {}
    hop_checks = []
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        hop = hops[index] if index < len(hops) else None
        sdk = step.get("cow_sdk_parameters") or {}
        actual_buy = _decimal_value(step.get("query_buy_amount_after_fee") or (hop or {}).get("buy_amount"))
        min_buy = _decimal_value(sdk.get("min_buy_amount_after_fee") or step.get("min_output_amount"))
        target_buy = _decimal_value(sdk.get("target_buy_amount_after_fee") or step.get("target_output_amount"))
        sell_amount = _decimal_value(sdk.get("sell_amount_before_fee") or step.get("query_sell_amount_before_fee") or step.get("input_amount"))
        price_guard_passed = actual_buy is not None and min_buy is not None and actual_buy >= min_buy
        target_met = actual_buy is not None and target_buy is not None and actual_buy >= target_buy
        query_guard = step.get("query_guard_analysis") if isinstance(step.get("query_guard_analysis"), dict) else {}
        failure_cause = None
        if not price_guard_passed:
            if actual_buy is None and quote_error_type:
                failure_cause = quote_error_type
            elif actual_buy is None:
                failure_cause = "missing_query_output"
            else:
                failure_cause = (
                    "query_worse_than_own_guard"
                    if query_guard.get("status") == "worse_than_guard"
                    else "actual_output_below_own_minimum"
                )
        hop_checks.append(
            {
                "hop": step.get("step") or index + 1,
                "path": f"{step.get('from_symbol') or '-'} -> {step.get('to_symbol') or '-'}",
                "sell_symbol": step.get("from_symbol"),
                "buy_symbol": step.get("to_symbol"),
                "sell_amount_before_fee": _decimal_text(sell_amount),
                "query_buy_amount_after_fee": _decimal_text(actual_buy),
                "target_buy_amount_after_fee": _decimal_text(target_buy),
                "min_buy_amount_after_fee": _decimal_text(min_buy),
                "price_guard_passed": price_guard_passed,
                "target_met": target_met,
                "query_guard_status": query_guard.get("status"),
                "query_guard_reason": query_guard.get("reason"),
                "query_position": step.get("query_price_position") or step.get("query_rate_position"),
                "failure_cause": failure_cause,
                "selected_target_source": step.get("selected_target_source"),
                "selected_acceptable_source": step.get("selected_acceptable_source"),
                "rule": step.get("selection_rule") or step.get("price_compare_rule"),
                "status": "pass" if price_guard_passed else ("not_checked" if actual_buy is None else "fail"),
            }
        )
    route_supported = bool((result.get("cow_support") or {}).get("supported"))
    quote_available = bool(result.get("viable")) and bool(hops)
    min_profit_usd = _decimal_value(pure_intent.get("min_pure_profit_amount")) or _cow_min_profit_usd_for_amount(result.get("input_amount"))
    min_final_amount = _decimal_value(pure_intent.get("min_final_amount"))
    own_limit_order_intent = _cow_own_limit_order_intent(plan, min_profit_usd=min_profit_usd)
    cow_sdk_plan = result.get("cow_flashloan_sdk_plan") if isinstance(result.get("cow_flashloan_sdk_plan"), dict) else None
    cow_sdk_error = result.get("cow_flashloan_sdk_error")
    route_hop_constraints_enforced = False
    quote_delta_amount = None
    quote_delta_positive = None
    quote_delta_offset_amount = None
    quote_delta_offset_percent = None
    quote_delta_above_diagnostic_floor = None
    pure_intent_ready = bool(pure_intent.get("enabled", True)) and bool(pure_intent.get("ready"))
    route_hop_constraints_passed = False
    price_guards_passed = False
    blocking_cause_counts: dict[str, int] = {}
    for item in hop_checks:
        cause = item.get("failure_cause")
        if cause:
            blocking_cause_counts[cause] = blocking_cause_counts.get(cause, 0) + 1
    reasons = []
    if not route_supported:
        reasons.append("CoW 不支持该路径中的一个或多个代币")
    quote_probe_notes = ["upper_profit_prediction_disabled_intent_only"]
    normalized_reasons = []
    if not route_supported:
        normalized_reasons.append("cow_route_has_unsupported_token")
    if normalized_reasons:
        reasons = normalized_reasons
    limit_intent_ready = bool(own_limit_order_intent.get("ready"))
    flashloan_capability = (
        cow_sdk_plan.get("flashloan_capability")
        if isinstance(cow_sdk_plan, dict) and isinstance(cow_sdk_plan.get("flashloan_capability"), dict)
        else {}
    )
    flashloan_submission_safe = bool(flashloan_capability.get("submission_safe"))
    cow_sdk_flashloan_ready = (
        route_supported
        and pure_intent_ready
        and cow_sdk_plan is not None
        and not cow_sdk_error
        and bool(flashloan_capability)
        and flashloan_submission_safe
    )
    if route_supported and pure_intent_ready:
        reasons.append("cow_intent_ready")
    if cow_sdk_flashloan_ready:
        reasons.append("cow_flashloan_sdk_intent_ready")
    elif route_supported and pure_intent_ready:
        capability_blockers = flashloan_capability.get("blockers") or []
        if capability_blockers:
            reasons.append(f"diagnostic_cow_sdk_intent_route_required:{','.join(capability_blockers)}")
        else:
            reasons.append(f"diagnostic_cow_flashloan_sdk_plan_required:{cow_sdk_error or 'missing_sdk_plan'}")
    intent_mode_ready = route_supported and pure_intent_ready
    checks_passed = intent_mode_ready
    order_submission_requested = _cow_order_submission_requested()
    order_submission_adapter_available = _cow_order_submission_adapter_available_compat()
    order_submission_sdk_status = _cow_order_submission_sdk_status_compat()
    order_submission_sdk_ready = bool(order_submission_sdk_status.get("ready"))
    order_submission_network_supported = cow_order_submission_network_supported(
        result.get("cow_network") or result.get("network") or DEFAULT_COW_TEST_NETWORK
    )
    order_submission_signer_ready = cow_order_submission_signer_ready()
    order_submission_signer_status = cow_order_submission_signer_status()
    quote_verified = bool(result.get("quote_verified"))
    order_submission_enabled = (
        order_submission_requested
        and order_submission_adapter_available
        and order_submission_sdk_ready
        and order_submission_network_supported
        and order_submission_signer_ready
    )
    quote_submission_blocked = bool(quote_error_type and not quote_available)
    submission_gate = build_submission_readiness(
        checks_passed=checks_passed,
        route_supported=route_supported,
        quote_available=quote_available,
        quote_verified=quote_verified,
        submission_requested=order_submission_requested,
        adapter_available=order_submission_adapter_available,
        sdk_ready=order_submission_sdk_ready,
        network_supported=order_submission_network_supported,
        signer_ready=order_submission_signer_ready,
        quote_submission_blocked=quote_submission_blocked,
        quote_error_type=quote_error_type,
        existing_reasons=reasons,
    )
    if not route_supported:
        status = "unsupported"
    elif not pure_intent_ready:
        status = "intent_not_ready"
    elif checks_passed and not order_submission_sdk_ready:
        status = "cow_flashloan_sdk_install_required"
        if "cow_flashloan_sdk_install_required" not in reasons:
            reasons.append("cow_flashloan_sdk_install_required")
        reasons.append(cow_order_submission_sdk_install_hint(order_submission_sdk_status))
    else:
        status = "blocked"
    if checks_passed:
        if not quote_verified:
            status = "quote_not_verified"
        elif order_submission_enabled:
            status = "limit_order_ready_to_submit"
        if not order_submission_enabled:
            disabled_reasons = {
                "quote_not_verified": not quote_verified,
                "order_submission_switch_off": not order_submission_requested,
                "order_submission_adapter_unavailable": not order_submission_adapter_available,
                "cow_flashloan_sdk_install_required": not order_submission_sdk_ready,
                "order_submission_network_unsupported": not order_submission_network_supported,
                "order_submission_signer_not_ready": not order_submission_signer_ready,
            }
            for reason, active in disabled_reasons.items():
                if active and reason not in reasons:
                    reasons.append(reason)
            for reason, active in disabled_reasons.items():
                if active:
                    status = reason
                    break
    submission_ready = bool(submission_gate.get("ready"))
    return {
        "control_mode": control_mode,
        "control_mode_default": DEFAULT_COW_FLASHLOAN_CONTROL_MODE,
        "status": status,
        "checks_passed": checks_passed,
        "quote_verified": quote_verified,
        "quote_rule_verified": quote_verified,
        "submission_rule_verified": submission_ready,
        "can_submit_order": submission_ready,
        "order_submission_requested": order_submission_requested,
        "order_submission_enabled": order_submission_enabled,
        "order_submission_adapter_available": order_submission_adapter_available,
        "order_submission_sdk_ready": order_submission_sdk_ready,
        "order_submission_sdk_status": order_submission_sdk_status,
        "order_submission_network_supported": order_submission_network_supported,
        "order_submission_signer_ready": order_submission_signer_ready,
        "order_submission_signer_status": order_submission_signer_status,
        "auto_execute_requested": checks_passed,
        "auto_execute_min_profit_usd": _decimal_text(min_profit_usd),
        "auto_execute_min_profit_percent": _decimal_text(DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT),
        "min_final_amount": _decimal_text(min_final_amount),
        "auto_execute_blocked": checks_passed and not submission_ready,
        "route_supported": route_supported,
        "quote_available": quote_available,
        "quote_submission_blocked": quote_submission_blocked,
        "profit_prediction_disabled": True,
        "profit_prediction_mode": "disabled_intent_only_sdk_settlement",
        "pure_intent_ready": pure_intent_ready,
        "intent_mode_ready": intent_mode_ready,
        "route_hop_constraints_enforced": route_hop_constraints_enforced,
        "route_hop_constraints_passed": route_hop_constraints_passed,
        "cow_flashloan_intent": pure_intent,
        "own_limit_order_ready": limit_intent_ready,
        "own_limit_order_intent": own_limit_order_intent,
        "cow_sdk_flashloan_ready": cow_sdk_flashloan_ready,
        "flashloan_sdk_gate_enforced": False,
        "runtime_sdk_gate_enforced": True,
        "cow_flashloan_sdk_plan": cow_sdk_plan,
        "cow_flashloan_sdk_error": cow_sdk_error,
        "flashloan_capability": flashloan_capability,
        "quote_price_guards_passed": price_guards_passed,
        "price_guards_passed": price_guards_passed,
        "quote_delta_amount": _decimal_text(quote_delta_amount),
        "quote_delta_positive": quote_delta_positive,
        "quote_delta_above_diagnostic_floor": quote_delta_above_diagnostic_floor,
        "quote_result_gate_enforced": False,
        "quote_probe_notes": quote_probe_notes,
        "quote_delta_offset_amount": _decimal_text(quote_delta_offset_amount),
        "quote_delta_offset_percent": _decimal_text(quote_delta_offset_percent),
        "profit_positive": quote_delta_positive,
        "profit_above_auto_threshold": quote_delta_above_diagnostic_floor,
        "local_profit_gate_enforced": False,
        "local_profit_diagnostic_reasons": quote_probe_notes,
        "drawdown_amount": _decimal_text(quote_delta_offset_amount),
        "drawdown_percent": _decimal_text(quote_delta_offset_percent),
        "intent_final_amount_met": None,
        "pure_profit_amount": None,
        "final_delta_amount": None,
        "final_symbol": result.get("final_symbol"),
        "hop_checks": hop_checks,
        "blocking_cause_counts": blocking_cause_counts,
        "quote_error_type": quote_error_type,
        "quote_error_display": quote_error.get("display"),
        "quote_error_retryable": quote_error.get("retryable"),
        "quote_error_raw": quote_error.get("raw"),
        "reasons": reasons,
        "submission_gate": submission_gate,
    }

