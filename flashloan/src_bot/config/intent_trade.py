from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from strategy.limits import (
    DEFAULT_ARBITRAGE_FEE_RESERVE_PERCENT,
    DEFAULT_ARBITRAGE_FLASHLOAN_FEE_PERCENT,
    DEFAULT_ARBITRAGE_TRADE_FEE_PERCENT,
    DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
)


def _decimal_value(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def env_decimal_first(names: list[str], default: str = "0") -> Decimal:
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


def intent_env_names(link_name: Any, suffix: str) -> list[str]:
    slug = _env_slug(link_name)
    return [f"COW_INTENT_{slug}_{suffix}", f"COW_FLASHLOAN_PURE_INTENT_{suffix}"]


def intent_costs(link_name: Any, borrow_amount: Decimal) -> dict[str, Decimal]:
    trade_fee_percent = env_decimal_first(
        [*intent_env_names(link_name, "TRADE_FEE_PERCENT"), "ARBITRAGE_TRADE_FEE_PERCENT"],
        str(DEFAULT_ARBITRAGE_TRADE_FEE_PERCENT),
    )
    flashloan_fee_percent = env_decimal_first(
        [*intent_env_names(link_name, "FLASHLOAN_FEE_PERCENT"), "ARBITRAGE_FLASHLOAN_FEE_PERCENT"],
        str(DEFAULT_ARBITRAGE_FLASHLOAN_FEE_PERCENT),
    )
    fee_reserve_percent = env_decimal_first(
        [*intent_env_names(link_name, "FEE_RESERVE_PERCENT"), "ARBITRAGE_FEE_RESERVE_PERCENT"],
        str(DEFAULT_ARBITRAGE_FEE_RESERVE_PERCENT),
    )
    gas_reserve_amount = env_decimal_first(intent_env_names(link_name, "GAS_RESERVE_USDC"), "0")
    other_known_costs_amount = env_decimal_first(intent_env_names(link_name, "OTHER_KNOWN_COSTS_USDC"), "0")
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
