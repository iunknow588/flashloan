from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from execution.cow_routes import cow_account_config, cow_network_config
from strategy.limits import (
    DEFAULT_ARBITRAGE_FEE_RESERVE_PERCENT,
    DEFAULT_ARBITRAGE_FLASHLOAN_FEE_PERCENT,
    DEFAULT_ARBITRAGE_TRADE_FEE_PERCENT,
    DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT,
    DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
)


DEFAULT_INTENT_BORROW_AMOUNT = Decimal("1000")
DEFAULT_INTENT_BORROW_SYMBOL = "USDC"
DEFAULT_INTENT_NETWORK = "avalanche"


def _decimal_value(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _env_decimal_first(names: list[str], default: str = "0") -> Decimal:
    for name in names:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            continue
        value = _decimal_value(raw)
        if value is not None and value >= 0:
            return value
    return Decimal(str(default))


def _env_slug(value: Any) -> str:
    text = str(value or "").upper()
    text = re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
    return text or "DEFAULT"


def _normalize_token_symbols(tokens: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in tokens or []:
        if isinstance(item, dict):
            candidates = [
                item.get("base_symbol"),
                item.get("symbol"),
                item.get("token_symbol"),
            ]
        else:
            candidates = [item]
        for symbol in candidates:
            text = str(symbol or "").strip().upper()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
    return result


def _normalize_link_tokens(link_name: Any) -> list[str]:
    text = str(link_name or "").strip().upper()
    if not text:
        return []
    for separator in ("->", "=>", ">", ",", "|", "/"):
        text = text.replace(separator, " ")
    parts = [part.strip().upper() for part in text.split() if part.strip()]
    return parts


def _route_path_from_link_name(
    link_name: Any,
    rising_tokens: list[str],
    falling_tokens: list[str],
) -> list[str]:
    parts = _normalize_link_tokens(link_name)
    if len(parts) >= 4 and parts[0] == parts[-1]:
        return parts[:4]
    if str(link_name or "").strip().lower() == "reverse_check":
        middle_one = rising_tokens[0] if rising_tokens else (falling_tokens[0] if falling_tokens else None)
        middle_two = falling_tokens[0] if falling_tokens else middle_one
    else:
        middle_one = falling_tokens[0] if falling_tokens else (rising_tokens[0] if rising_tokens else None)
        middle_two = rising_tokens[0] if rising_tokens else middle_one
    if middle_one and middle_two:
        return [DEFAULT_INTENT_BORROW_SYMBOL, middle_one, middle_two, DEFAULT_INTENT_BORROW_SYMBOL]
    return [DEFAULT_INTENT_BORROW_SYMBOL]


def _route_label(link_name: Any, route_path: list[str]) -> str:
    text = str(link_name or "").strip()
    if text:
        return text
    if route_path:
        return "->".join(route_path)
    return "intent_trade"


def _route_env_names(link_name: Any, suffix: str) -> list[str]:
    slug = _env_slug(link_name)
    return [f"COW_INTENT_{slug}_{suffix}", f"COW_FLASHLOAN_PURE_INTENT_{suffix}"]


def _intent_costs(link_name: Any, borrow_amount: Decimal) -> dict[str, Decimal]:
    trade_fee_percent = _env_decimal_first(
        [*_route_env_names(link_name, "TRADE_FEE_PERCENT"), "ARBITRAGE_TRADE_FEE_PERCENT"],
        str(DEFAULT_ARBITRAGE_TRADE_FEE_PERCENT),
    )
    flashloan_fee_percent = _env_decimal_first(
        [*_route_env_names(link_name, "FLASHLOAN_FEE_PERCENT"), "ARBITRAGE_FLASHLOAN_FEE_PERCENT"],
        str(DEFAULT_ARBITRAGE_FLASHLOAN_FEE_PERCENT),
    )
    fee_reserve_percent = _env_decimal_first(
        [*_route_env_names(link_name, "FEE_RESERVE_PERCENT"), "ARBITRAGE_FEE_RESERVE_PERCENT"],
        str(DEFAULT_ARBITRAGE_FEE_RESERVE_PERCENT),
    )
    gas_reserve_amount = _env_decimal_first(_route_env_names(link_name, "GAS_RESERVE_USDC"), "0")
    other_known_costs_amount = _env_decimal_first(_route_env_names(link_name, "OTHER_KNOWN_COSTS_USDC"), "0")
    route_trade_fee_percent = trade_fee_percent * Decimal(DEFAULT_COW_TRADE_FEE_SIDE_COUNT)
    route_trade_fee_amount = borrow_amount * route_trade_fee_percent / Decimal("100")
    flashloan_fee_amount = borrow_amount * flashloan_fee_percent / Decimal("100")
    fee_reserve_amount = borrow_amount * fee_reserve_percent / Decimal("100")
    return {
        "trade_fee_percent": trade_fee_percent,
        "route_trade_fee_percent": route_trade_fee_percent,
        "route_trade_fee_amount": route_trade_fee_amount,
        "flashloan_fee_percent": flashloan_fee_percent,
        "flashloan_fee_amount": flashloan_fee_amount,
        "fee_reserve_percent": fee_reserve_percent,
        "fee_reserve_amount": fee_reserve_amount,
        "gas_reserve_amount": gas_reserve_amount,
        "other_known_costs_amount": other_known_costs_amount,
    }


def _intent_network() -> tuple[str, int, bool]:
    try:
        config = cow_network_config(network=os.getenv("COW_FLASHLOAN_INTENT_NETWORK", DEFAULT_INTENT_NETWORK))
    except Exception:
        config = cow_network_config(network=DEFAULT_INTENT_NETWORK)
    return config.network, config.chain_id, bool(config.testnet)


def _build_token_scope(
    *,
    route_path: list[str],
    rising_tokens: list[str],
    falling_tokens: list[str],
) -> dict[str, Any]:
    scope_tokens: list[str] = []
    for symbol in (*rising_tokens, *falling_tokens, DEFAULT_INTENT_BORROW_SYMBOL):
        text = str(symbol or "").strip().upper()
        if text and text not in scope_tokens:
            scope_tokens.append(text)
    return {
        "input_symbol": DEFAULT_INTENT_BORROW_SYMBOL,
        "output_symbol": DEFAULT_INTENT_BORROW_SYMBOL,
        "tokens": scope_tokens[:10],
        "token_count": min(len(scope_tokens), 10),
        "scope_role": "solver_owned_token_universe_only",
        "rising_tokens": rising_tokens,
        "falling_tokens": falling_tokens,
        "route_path": route_path,
    }


def _route_direction(route_path: list[str], rising_tokens: list[str], falling_tokens: list[str], link_name: Any) -> str:
    if len(route_path) >= 4:
        first_mid = route_path[1]
        second_mid = route_path[2]
        if first_mid in falling_tokens and second_mid in rising_tokens:
            return "buy_loser_then_gainer"
        if first_mid in rising_tokens and second_mid in falling_tokens:
            return "reverse_check"
    if str(link_name or "").strip().lower() == "reverse_check":
        return "reverse_check"
    return "buy_loser_then_gainer"


def build_cow_intent_trade(
    link_name: Any,
    expected_profit: Any,
    rising_tokens: Any,
    falling_tokens: Any,
) -> dict[str, Any]:
    rising = _normalize_token_symbols(rising_tokens)
    falling = _normalize_token_symbols(falling_tokens)
    route_path = _route_path_from_link_name(link_name, rising, falling)
    route_label = _route_label(link_name, route_path)

    network, chain_id, testnet = _intent_network()
    account = cow_account_config(network)

    profit_amount = _decimal_value(expected_profit)
    if profit_amount is None:
        profit_amount = DEFAULT_INTENT_BORROW_AMOUNT * DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT / Decimal("100")

    borrow_amount = _env_decimal_first(_route_env_names(link_name, "BORROW_AMOUNT"), str(DEFAULT_INTENT_BORROW_AMOUNT))
    borrow_symbol = route_path[0] if len(route_path) >= 2 else DEFAULT_INTENT_BORROW_SYMBOL
    target_symbol = route_path[-1] if len(route_path) >= 2 else borrow_symbol
    costs = _intent_costs(route_label, borrow_amount)
    total_cost_usdc = (
        costs["route_trade_fee_amount"]
        + costs["flashloan_fee_amount"]
        + costs["fee_reserve_amount"]
        + costs["gas_reserve_amount"]
        + costs["other_known_costs_amount"]
    )
    x_amount = profit_amount + total_cost_usdc
    min_final_amount = borrow_amount + profit_amount
    target_token_amount = borrow_amount + x_amount

    token_scope = _build_token_scope(
        route_path=route_path,
        rising_tokens=rising,
        falling_tokens=falling,
    )

    fee_components = {
        "trade_fee_percent_per_side": _decimal_text(costs["trade_fee_percent"]),
        "trade_fee_side_count": DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
        "route_trade_fee_percent": _decimal_text(costs["route_trade_fee_percent"]),
        "route_trade_fee_usdc": _decimal_text(costs["route_trade_fee_amount"]),
        "flashloan_fee_percent": _decimal_text(costs["flashloan_fee_percent"]),
        "flashloan_fee_usdc": _decimal_text(costs["flashloan_fee_amount"]),
        "fee_reserve_percent": _decimal_text(costs["fee_reserve_percent"]),
        "fee_reserve_usdc": _decimal_text(costs["fee_reserve_amount"]),
        "gas_reserve_usdc": _decimal_text(costs["gas_reserve_amount"]),
        "other_known_costs_usdc": _decimal_text(costs["other_known_costs_amount"]),
        "total_cost_usdc": _decimal_text(total_cost_usdc),
        "expected_profit_usdc": _decimal_text(profit_amount),
        "required_gap_usdc": _decimal_text(x_amount),
    }

    intent = {
        "version": 3,
        "mode": "pure_profit_final_amount_intent",
        "link_name": route_label,
        "route_path": route_path,
        "route_hop_count": max(0, len(route_path) - 1),
        "route_direction": _route_direction(route_path, rising, falling, link_name),
        "control_mode": "intent",
        "enabled": os.getenv("COW_FLASHLOAN_PURE_INTENT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"},
        "controller": "upper_layer_intent_only",
        "success_controller": "cow_sdk",
        "swap_flow_controller": "cow_sdk_solver",
        "initial_amount": _decimal_text(borrow_amount),
        "initial_symbol": borrow_symbol,
        "final_symbol": target_symbol,
        "requested_quote_amount": _decimal_text(borrow_amount),
        "expected_profit_amount": _decimal_text(profit_amount),
        "formula": "solver_owned_token_scope_only",
        "x_definition": "borrow_amount + fee_budget + expected_profit_floor",
        "x_amount": _decimal_text(x_amount),
        "x_ratio": _decimal_text(x_amount / borrow_amount) if borrow_amount > 0 else None,
        "x_percent": _decimal_text(x_amount / borrow_amount * Decimal("100")) if borrow_amount > 0 else None,
        "baseline_percent": "100",
        "total_required_percent": "100",
        "min_final_amount": _decimal_text(min_final_amount),
        "min_pure_profit_amount": _decimal_text(profit_amount),
        "principal_source": "fixed_1000u_intent_principal",
        "borrow_token_name": borrow_symbol,
        "borrow_token_amount": _decimal_text(borrow_amount),
        "target_token_name": target_symbol,
        "target_token_amount": _decimal_text(target_token_amount),
        "fee_components": fee_components,
        "cow_sdk_order_intent": {
            "sell_amount_before_fee": _decimal_text(borrow_amount),
            "sell_symbol": borrow_symbol,
            "minimum_final_buy_amount_after_all_costs": _decimal_text(min_final_amount),
            "buy_symbol": target_symbol,
        },
        "token_scope": token_scope,
        "owner": account.owner,
        "cow_network": network,
        "cow_chain_id": chain_id,
        "control_surface": {
            "default_mode": "intent",
            "current_mode": "intent",
            "intent_mode": "intent",
            "custom_mode_available": False,
        },
        "submission_boundary": "caller_provides_token_scope_and_min_final_amount; intent_only_submission",
        "ready": borrow_amount > 0 and bool(borrow_symbol) and bool(target_symbol) and bool(route_path),
        "cost_model": {
            "network": network,
            "chain_id": chain_id,
            "testnet": testnet,
            "trade_fee_percent": _decimal_text(costs["trade_fee_percent"]),
            "flashloan_fee_percent": _decimal_text(costs["flashloan_fee_percent"]),
            "fee_reserve_percent": _decimal_text(costs["fee_reserve_percent"]),
            "gas_reserve_usdc": _decimal_text(costs["gas_reserve_amount"]),
            "other_known_costs_usdc": _decimal_text(costs["other_known_costs_amount"]),
        },
    }
    return intent
