from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from execution.plan_quotes import build_path, from_units, quote_token, to_units


class DynamicStrategy(IntEnum):
    S1_FORWARD = 0
    S1_REVERSE = 1
    S2_FORWARD = 2
    S2_REVERSE = 3


@dataclass(frozen=True)
class DynamicQuoteConfig:
    amount_x_units: int
    amount_y_units: int
    premium_bps: int = 5
    min_profit_usdc_units: int = 1


def route_symbols(strategy: DynamicStrategy, x_symbol: str, y_symbol: str) -> list[str]:
    x = quote_token(x_symbol).symbol if quote_token(x_symbol).symbol == "USDC" else x_symbol
    y = quote_token(y_symbol).symbol if quote_token(y_symbol).symbol == "USDC" else y_symbol
    if strategy == DynamicStrategy.S1_FORWARD:
        return [x, "USDC", y, x]
    if strategy == DynamicStrategy.S1_REVERSE:
        return [y, x, "USDC", y]
    if strategy == DynamicStrategy.S2_FORWARD:
        return [x, y, "USDC", x]
    return [y, "USDC", x, y]


def amount_out(router: Any, amount_in: int, from_symbol: str, to_symbol: str) -> int:
    from_token = quote_token(from_symbol)
    to_token = quote_token(to_symbol)
    path = build_path(from_token, to_token)
    return int(router.functions.getAmountsOut(amount_in, path).call()[-1])


def amount_in(router: Any, amount_out_units: int, from_symbol: str, to_symbol: str) -> int:
    from_token = quote_token(from_symbol)
    to_token = quote_token(to_symbol)
    path = build_path(from_token, to_token)
    return int(router.functions.getAmountsIn(amount_out_units, path).call()[0])


def value_usdc_units(router: Any, symbol: str, amount_units: int) -> int:
    token = quote_token(symbol)
    if token.symbol == "USDC":
        return amount_units
    return amount_out(router, amount_units, token.symbol, "USDC")


def quote_strategy(
    router: Any,
    x_symbol: str,
    y_symbol: str,
    strategy: DynamicStrategy,
    config: DynamicQuoteConfig,
) -> dict:
    symbols = route_symbols(strategy, x_symbol, y_symbol)
    borrow_symbol = symbols[0]
    borrow_token = quote_token(borrow_symbol)
    amount = config.amount_x_units if quote_token(borrow_symbol).token_address == quote_token(x_symbol).token_address else config.amount_y_units
    if amount <= 0:
        return {"strategy": strategy.name, "viable": False, "error": "borrow amount must be positive"}

    owed = amount + (amount * config.premium_bps) // 10000
    try:
        first_out = amount_out(router, amount, symbols[0], symbols[1])
        second_out = amount_out(router, first_out, symbols[1], symbols[2])
        required_input = amount_in(router, owed, symbols[2], symbols[3])
        if second_out <= required_input:
            return {
                "strategy": strategy.name,
                "route_symbols": symbols,
                "borrow_symbol": borrow_token.symbol,
                "borrow_amount_units": str(amount),
                "owed_units": str(owed),
                "viable": False,
                "error": "route cannot repay premium-adjusted debt",
            }
        profit_units = second_out - required_input
        profit_usdc_units = value_usdc_units(router, symbols[2], profit_units)
    except Exception as exc:
        return {
            "strategy": strategy.name,
            "route_symbols": symbols,
            "borrow_symbol": borrow_token.symbol,
            "borrow_amount_units": str(amount),
            "viable": False,
            "error": str(exc),
        }

    profit_token = quote_token(symbols[2])
    return {
        "strategy": strategy.name,
        "route_symbols": symbols,
        "borrow_symbol": borrow_token.symbol,
        "borrow_amount_units": str(amount),
        "owed_units": str(owed),
        "profit_symbol": profit_token.symbol,
        "profit_units": str(profit_units),
        "profit_amount": from_units(profit_units, profit_token.decimals),
        "profit_usdc_units": str(profit_usdc_units),
        "profit_usdc": from_units(profit_usdc_units, quote_token("USDC").decimals),
        "viable": profit_usdc_units >= config.min_profit_usdc_units,
    }


def quote_dynamic_candidate(router: Any, candidate: dict, config: DynamicQuoteConfig) -> dict:
    x_symbol = candidate["x_symbol"]
    y_symbol = candidate["y_symbol"]
    quotes = [
        quote_strategy(router, x_symbol, y_symbol, strategy, config)
        for strategy in DynamicStrategy
    ]
    viable = [quote for quote in quotes if quote.get("viable")]
    best = max(viable, key=lambda quote: int(quote["profit_usdc_units"])) if viable else None
    return {
        "x_symbol": x_symbol,
        "y_symbol": y_symbol,
        "dex_quote_verified": best is not None,
        "net_profit_verified": False,
        "executable_signal": False,
        "best_quote": best,
        "quotes": quotes,
        "blocked_reasons": []
        if best
        else ["no_viable_dex_quote"],
        "next_required_stage": "gas_slippage_net_profit",
    }


def token_amount_units_for_usd(router: Any, symbol: str, usd_amount: float) -> int:
    if usd_amount <= 0:
        raise ValueError("usd amount must be positive")
    usdc_units = to_units(usd_amount, quote_token("USDC").decimals)
    return amount_out(router, usdc_units, "USDC", symbol)
