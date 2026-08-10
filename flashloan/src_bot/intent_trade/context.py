from __future__ import annotations

from decimal import Decimal
from typing import Any

from config.intent_trade import intent_costs
from intent_trade.builder import DEFAULT_INTENT_BORROW_SYMBOL, _decimal_text, _decimal_value


def _total_cost_usdc(costs: dict[str, Decimal]) -> Decimal:
    return sum(
        costs[key]
        for key in (
            "route_trade_fee_amount",
            "flashloan_fee_amount",
            "fee_reserve_amount",
            "gas_reserve_amount",
            "other_known_costs_amount",
        )
    )


def bind_cow_intent_context(
    intent: dict[str, Any],
    *,
    requested_amount: Any,
    input_symbol: Any,
    final_symbol: Any,
    owner: str | None,
    cow_network: str,
    cow_chain_id: int,
) -> dict[str, Any]:
    bound = dict(intent)
    requested_decimal = _decimal_value(requested_amount)
    requested_text = _decimal_text(requested_decimal)
    initial_symbol = str(input_symbol or bound.get("initial_symbol") or DEFAULT_INTENT_BORROW_SYMBOL).strip().upper()
    output_symbol = str(final_symbol or input_symbol or bound.get("final_symbol") or DEFAULT_INTENT_BORROW_SYMBOL).strip().upper()
    bound["requested_quote_amount"] = requested_text
    if requested_text is not None:
        bound["initial_amount"] = requested_text
        bound["borrow_token_amount"] = requested_text
        profit_amount = _decimal_value(bound.get("min_pure_profit_amount")) or Decimal("0")
        costs = intent_costs(bound.get("link_name") or "intent_trade", requested_decimal)
        total_cost = _total_cost_usdc(costs)
        x_amount = profit_amount + total_cost
        min_final = requested_decimal + x_amount
        fee_components = dict(bound.get("fee_components") if isinstance(bound.get("fee_components"), dict) else {})
        fee_components.update(
            {
                "trade_fee_percent_per_side": _decimal_text(costs["trade_fee_percent"]),
                "route_trade_fee_percent": _decimal_text(costs["route_trade_fee_percent"]),
                "route_trade_fee_usdc": _decimal_text(costs["route_trade_fee_amount"]),
                "flashloan_fee_percent": _decimal_text(costs["flashloan_fee_percent"]),
                "flashloan_fee_usdc": _decimal_text(costs["flashloan_fee_amount"]),
                "fee_reserve_percent": _decimal_text(costs["fee_reserve_percent"]),
                "fee_reserve_usdc": _decimal_text(costs["fee_reserve_amount"]),
                "gas_reserve_usdc": _decimal_text(costs["gas_reserve_amount"]),
                "other_known_costs_usdc": _decimal_text(costs["other_known_costs_amount"]),
                "total_cost_usdc": _decimal_text(total_cost),
                "expected_profit_usdc": _decimal_text(profit_amount),
                "required_gap_usdc": _decimal_text(x_amount),
            }
        )
        bound["fee_components"] = fee_components
        bound["x_amount"] = _decimal_text(x_amount)
        bound["x_ratio"] = _decimal_text(x_amount / requested_decimal) if requested_decimal > 0 else None
        bound["x_percent"] = _decimal_text(x_amount / requested_decimal * Decimal("100")) if requested_decimal > 0 else None
        bound["min_final_amount"] = _decimal_text(min_final)
        bound["target_token_amount"] = _decimal_text(min_final)
        direct_protocol = dict(bound.get("direct_onchain_protocol") if isinstance(bound.get("direct_onchain_protocol"), dict) else {})
        if direct_protocol:
            direct_protocol["borrow_symbol"] = initial_symbol or DEFAULT_INTENT_BORROW_SYMBOL
            direct_protocol["route_path"] = bound.get("route_path") if isinstance(bound.get("route_path"), list) else direct_protocol.get("route_path")
            bound["direct_onchain_protocol"] = direct_protocol
    bound["initial_symbol"] = initial_symbol or DEFAULT_INTENT_BORROW_SYMBOL
    bound["final_symbol"] = output_symbol or DEFAULT_INTENT_BORROW_SYMBOL
    bound["owner"] = owner
    bound["cow_network"] = cow_network
    bound["cow_chain_id"] = cow_chain_id
    bound["token_scope"] = {
        **(bound.get("token_scope") if isinstance(bound.get("token_scope"), dict) else {}),
        "input_symbol": bound["initial_symbol"],
        "output_symbol": bound["final_symbol"],
    }
    bound["cow_sdk_order_intent"] = {
        "sell_amount_before_fee": bound.get("initial_amount"),
        "sell_symbol": bound["initial_symbol"],
        "minimum_final_buy_amount_after_all_costs": bound.get("min_final_amount"),
        "buy_symbol": bound["final_symbol"],
    }
    bound["ready"] = bool(bound.get("initial_amount")) and bool(bound["initial_symbol"]) and bool(bound["final_symbol"])
    return bound


_bind_cow_intent_context = bind_cow_intent_context
