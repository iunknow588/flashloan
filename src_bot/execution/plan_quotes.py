from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3

from core.sensitive_data import redact_sensitive_text
from execution.dex_costs import (
    ROUTER_ABI,
    TOKEN_COSTS,
    TRADER_JOE_V2_ROUTER,
    USDC,
    USDC_DECIMALS,
)


@dataclass(frozen=True)
class QuoteToken:
    symbol: str
    token_address: str
    decimals: int


def normalize_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if text == "USDC":
        return "USDC"
    return text if text.endswith("USDT") else f"{text}USDT"


def quote_token(symbol: str) -> QuoteToken:
    normalized = normalize_symbol(symbol)
    if normalized == "USDC":
        return QuoteToken("USDC", USDC, USDC_DECIMALS)
    config = TOKEN_COSTS.get(normalized)
    if config is None:
        raise ValueError(f"unsupported executable symbol: {symbol}")
    return QuoteToken(normalized, config.token_address, config.decimals)


def to_units(value: float, decimals: int) -> int:
    if value <= 0:
        raise ValueError("amount must be positive")
    return int(round(value * (10**decimals)))


def from_units(value: int, decimals: int) -> float:
    return value / float(10**decimals)


def build_path(from_token: QuoteToken, to_token: QuoteToken) -> list[str]:
    if from_token.symbol == to_token.symbol:
        raise ValueError(f"refusing same-token route: {from_token.symbol}")
    if from_token.symbol == "USDC" or to_token.symbol == "USDC":
        path = [from_token.token_address, to_token.token_address]
    else:
        path = [from_token.token_address, USDC, to_token.token_address]
    return [Web3.to_checksum_address(address) for address in path]


def quote_exact_in_step(
    router: Any,
    step: dict,
    slippage_bps: int,
    input_amount: float | None = None,
) -> dict:
    from_token = quote_token(step["from_symbol"])
    to_token = quote_token(step["to_symbol"])
    actual_input_amount = float(input_amount if input_amount is not None else step["input_amount"])
    amount_in = to_units(actual_input_amount, from_token.decimals)
    path = build_path(from_token, to_token)
    amounts = [int(value) for value in router.functions.getAmountsOut(amount_in, path).call()]
    output_units = amounts[-1]
    output_amount = from_units(output_units, to_token.decimals)
    paper_output = float(step.get("output_amount") or 0)
    deviation_percent = (
        (output_amount / paper_output - 1.0) * 100.0
        if paper_output > 0
        else None
    )
    return {
        "rank": int(step.get("rank", 0)),
        "action": step.get("action"),
        "from_symbol": from_token.symbol,
        "to_symbol": to_token.symbol,
        "input_amount": actual_input_amount,
        "paper_input_amount": float(step.get("input_amount") or 0),
        "paper_output_amount": paper_output,
        "quoted_output_amount": output_amount,
        "min_output_amount": output_amount * max(0, 10000 - slippage_bps) / 10000,
        "deviation_percent": deviation_percent,
        "path_symbols": [
            from_token.symbol,
            *([] if len(path) == 2 else ["USDC"]),
            to_token.symbol,
        ],
        "path": path,
        "viable": output_amount > 0,
    }


def quote_exact_out_step(router: Any, step: dict, slippage_bps: int) -> dict:
    from_token = quote_token(step["from_symbol"])
    to_token = quote_token(step["to_symbol"])
    output_amount = float(step["output_amount"])
    amount_out = to_units(output_amount, to_token.decimals)
    path = build_path(from_token, to_token)
    amounts = [int(value) for value in router.functions.getAmountsIn(amount_out, path).call()]
    input_units = amounts[0]
    input_amount = from_units(input_units, from_token.decimals)
    paper_input = float(step.get("input_amount") or 0)
    deviation_percent = (
        (input_amount / paper_input - 1.0) * 100.0
        if paper_input > 0
        else None
    )
    return {
        "rank": int(step.get("rank", 0)),
        "action": step.get("action"),
        "from_symbol": from_token.symbol,
        "to_symbol": to_token.symbol,
        "paper_input_amount": paper_input,
        "quoted_input_amount": input_amount,
        "max_input_amount": input_amount * (10000 + max(0, slippage_bps)) / 10000,
        "output_amount": output_amount,
        "deviation_percent": deviation_percent,
        "path_symbols": [
            from_token.symbol,
            *([] if len(path) == 2 else ["USDC"]),
            to_token.symbol,
        ],
        "path": path,
        "viable": input_amount > 0,
    }


def quote_value_usdc(router: Any, symbol: str, amount: float) -> float:
    if amount == 0:
        return 0.0
    token = quote_token(symbol)
    if token.symbol == "USDC":
        return amount
    usdc = quote_token("USDC")
    units = to_units(abs(amount), token.decimals)
    path = build_path(token, usdc)
    amounts = [int(value) for value in router.functions.getAmountsOut(units, path).call()]
    value = from_units(amounts[-1], usdc.decimals)
    return value if amount > 0 else -value


def quote_execution_plan(
    execution_plan: dict,
    rpc_url: str,
    router_address: str = TRADER_JOE_V2_ROUTER,
    slippage_bps: int = 50,
) -> dict:
    if not execution_plan:
        raise ValueError("execution_plan is required")

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    router = w3.eth.contract(
        address=Web3.to_checksum_address(router_address),
        abi=ROUTER_ABI,
    )
    slippage_bps = max(0, min(int(slippage_bps), 5000))

    buy_steps = []
    sell_steps = []
    repay_steps = []
    errors = []
    bought_by_rank: dict[int, float] = {}
    mid_output_by_rank: dict[int, float] = {}
    mid_symbol_by_rank: dict[int, str] = {}

    for step in execution_plan.get("buy_steps", []):
        try:
            quoted = quote_exact_in_step(router, step, slippage_bps)
            buy_steps.append(quoted)
            bought_by_rank[quoted["rank"]] = quoted["quoted_output_amount"]
        except Exception as exc:
            errors.append(step_error("buy_steps", step, exc))

    for step in execution_plan.get("sell_steps", []):
        try:
            input_amount = bought_by_rank.get(int(step.get("rank", 0)))
            quoted = quote_exact_in_step(router, step, slippage_bps, input_amount=input_amount)
            sell_steps.append(quoted)
            mid_output_by_rank[quoted["rank"]] = quoted["quoted_output_amount"]
            mid_symbol_by_rank[quoted["rank"]] = quoted["to_symbol"]
        except Exception as exc:
            errors.append(step_error("sell_steps", step, exc))

    for step in execution_plan.get("repay_steps", []):
        try:
            repay_steps.append(quote_exact_out_step(router, step, slippage_bps))
        except Exception as exc:
            errors.append(step_error("repay_steps", step, exc))

    profit_legs = []
    total_available_value_usdc = 0.0
    total_repay_value_usdc = 0.0
    quoted_profit_usdc = 0.0
    for repay_step in repay_steps:
        rank = int(repay_step.get("rank", 0))
        profit_symbol = repay_step["from_symbol"]
        available_input = mid_output_by_rank.get(rank, 0.0)
        if mid_symbol_by_rank.get(rank) != profit_symbol:
            errors.append(
                {
                    "section": "repay_steps",
                    "rank": rank,
                    "action": repay_step.get("action"),
                    "from_symbol": profit_symbol,
                    "to_symbol": repay_step.get("to_symbol"),
                    "error": "repay input token does not match previous step output token",
                }
            )
            continue

        required_input = float(repay_step["quoted_input_amount"])
        profit_input_amount = available_input - required_input
        try:
            available_value_usdc = quote_value_usdc(router, profit_symbol, available_input)
            repay_value_usdc = quote_value_usdc(router, profit_symbol, required_input)
            profit_usdc = quote_value_usdc(router, profit_symbol, profit_input_amount)
        except Exception as exc:
            errors.append(step_error("repay_profit", repay_step, exc))
            continue

        total_available_value_usdc += available_value_usdc
        total_repay_value_usdc += repay_value_usdc
        quoted_profit_usdc += profit_usdc
        profit_legs.append(
            {
                "rank": rank,
                "profit_symbol": profit_symbol,
                "available_input_amount": available_input,
                "required_input_amount": required_input,
                "profit_input_amount": profit_input_amount,
                "profit_usdc": profit_usdc,
                "viable": profit_input_amount > 0,
            }
        )

    return {
        "dex_name": "Trader Joe V2",
        "router_address": router_address,
        "slippage_bps": slippage_bps,
        "buy_steps": buy_steps,
        "sell_steps": sell_steps,
        "repay_steps": repay_steps,
        "profit_legs": profit_legs,
        "total_sell_usdc": total_available_value_usdc,
        "total_repay_usdc": total_repay_value_usdc,
        "total_available_value_usdc": total_available_value_usdc,
        "total_repay_input_value_usdc": total_repay_value_usdc,
        "quoted_profit_usdc": quoted_profit_usdc,
        "errors": errors,
        "viable": not errors and bool(profit_legs) and all(leg["viable"] for leg in profit_legs),
    }


def step_error(section: str, step: dict, exc: Exception) -> dict:
    return {
        "section": section,
        "rank": step.get("rank"),
        "action": step.get("action"),
        "from_symbol": step.get("from_symbol"),
        "to_symbol": step.get("to_symbol"),
        "error": redact_sensitive_text(exc),
    }
