from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from strategy.arbitrage import ArbitrageConfig
from strategy.limits import (
    DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT,
    DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
    DEFAULT_EXECUTION_SLIPPAGE_BPS,
    DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    DEFAULT_MIN_COW_SPREAD_PERCENT,
    DEFAULT_MIN_TOKEN_PRICE_USD,
)


def _decimal_value(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def cost_adjusted_cow_thresholds(
    *,
    requested_min_spread_percent: float = DEFAULT_MIN_COW_SPREAD_PERCENT,
    amount: str | int | float | Decimal = "1000",
    arbitrage_config: ArbitrageConfig | None = None,
    slippage_bps: int = DEFAULT_EXECUTION_SLIPPAGE_BPS,
    min_profit_percent: Decimal = DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT,
) -> dict[str, Any]:
    notional = _decimal_value(amount)
    if notional is None or notional <= 0:
        configured_notional = getattr(arbitrage_config, "notional_usd", None) if arbitrage_config else None
        notional = _decimal_value(configured_notional) or Decimal("1000")
    trade_fee_percent = _decimal_value(getattr(arbitrage_config, "trade_fee_percent", 0) if arbitrage_config else 0) or Decimal("0")
    flashloan_fee_percent = _decimal_value(getattr(arbitrage_config, "flashloan_fee_percent", 0) if arbitrage_config else 0) or Decimal("0")
    fee_reserve_percent = _decimal_value(getattr(arbitrage_config, "fee_reserve_percent", 0) if arbitrage_config else 0) or Decimal("0")
    requested_spread = _decimal_value(requested_min_spread_percent) or Decimal(str(DEFAULT_MIN_COW_SPREAD_PERCENT))
    configured_min_profit_percent = max(Decimal("0"), _decimal_value(min_profit_percent) or DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT)
    min_profit_usd = notional * configured_min_profit_percent / Decimal("100")
    route_trade_fee_percent = trade_fee_percent * Decimal(DEFAULT_COW_TRADE_FEE_SIDE_COUNT)
    route_cost_percent = route_trade_fee_percent + flashloan_fee_percent + fee_reserve_percent + configured_min_profit_percent
    adjusted_spread = max(Decimal("0"), route_cost_percent)
    side_threshold = Decimal(str(DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT))
    return {
        "requested_min_spread_percent": _decimal_text(requested_spread),
        "adjusted_min_spread_percent": _decimal_text(adjusted_spread),
        "min_window_spread_percent": _decimal_text(adjusted_spread),
        "min_side_change_percent": _decimal_text(side_threshold),
        "min_token_price_usd": _decimal_text(Decimal(str(DEFAULT_MIN_TOKEN_PRICE_USD))),
        "amount": _decimal_text(notional),
        "trade_fee_percent_per_hop": _decimal_text(trade_fee_percent),
        "trade_fee_side_count": DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
        "route_hop_count": DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
        "route_trade_fee_percent": _decimal_text(route_trade_fee_percent),
        "flashloan_fee_percent": _decimal_text(flashloan_fee_percent),
        "fee_reserve_percent": _decimal_text(fee_reserve_percent),
        "slippage_bps": max(0, min(int(slippage_bps), 5000)),
        "slippage_percent": None,
        "slippage_model": "dynamic_target_minus_acceptable_price",
        "min_profit_usd": _decimal_text(min_profit_usd),
        "min_profit_percent": _decimal_text(configured_min_profit_percent),
        "route_cost_floor_percent": _decimal_text(route_cost_percent),
        "threshold_rule": f"gain - loss > dynamic_min_window_spread_percent; side display > {side_threshold}%",
    }


def _median_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _market_price_candidates(
    symbol: str,
    rows: dict[str, dict[str, Any]],
    *,
    query_price: Decimal | None,
) -> list[dict[str, Any]]:
    key = str(symbol or "").upper()
    row = rows.get(key) or {}
    candidates = []
    for source, value in (
        ("previous_window", _decimal_value(row.get("start_price"))),
        ("current_window", _decimal_value(row.get("current_price"))),
        ("query_quote", query_price),
    ):
        if value is not None and value > 0:
            candidates.append({"source": source, "price": value})
    return candidates


def _exchange_rate_candidates(
    from_symbol: str,
    to_symbol: str,
    rows: dict[str, dict[str, Any]],
    *,
    query_rate: Decimal | None,
) -> list[dict[str, Any]]:
    from_key = str(from_symbol or "").upper()
    to_key = str(to_symbol or "").upper()
    from_row = rows.get(from_key) or {}
    to_row = rows.get(to_key) or {}
    candidates = []
    for source, from_price, to_price in (
        ("previous_window", _decimal_value(from_row.get("start_price")), _decimal_value(to_row.get("start_price"))),
        ("current_window", _decimal_value(from_row.get("current_price")), _decimal_value(to_row.get("current_price"))),
        ("query_quote", None, None),
    ):
        if source == "query_quote":
            rate = query_rate
        elif from_price is not None and to_price is not None and to_price > 0:
            rate = from_price / to_price
        else:
            rate = None
        if rate is not None and rate > 0:
            candidates.append({"source": source, "rate": rate})
    return candidates


def _serialize_decimal_candidates(candidates: list[dict[str, Any]], value_key: str) -> list[dict[str, Any]]:
    return [
        {
            **{key: value for key, value in item.items() if key != value_key},
            value_key: _decimal_text(item.get(value_key)),
        }
        for item in candidates
    ]


def _select_decimal_candidate(
    candidates: list[dict[str, Any]],
    value_key: str,
    mode: str,
) -> dict[str, Any] | None:
    valid = [item for item in candidates if isinstance(item.get(value_key), Decimal) and item[value_key] > 0]
    if not valid:
        return None
    if mode == "min":
        return min(valid, key=lambda item: item[value_key])
    if mode == "max":
        return max(valid, key=lambda item: item[value_key])
    target_value = _median_decimal([item[value_key] for item in valid])
    for item in valid:
        if item[value_key] == target_value:
            return item
    return None


def _selected_decimal_text(candidate: dict[str, Any] | None, value_key: str) -> str | None:
    if not candidate:
        return None
    return _decimal_text(candidate.get(value_key))


def _selected_source(candidate: dict[str, Any] | None) -> str | None:
    return str(candidate.get("source")) if candidate else None


def _decimal_rank_analysis(
    candidates: list[dict[str, Any]],
    value_key: str,
    *,
    query_source: str = "query_quote",
) -> dict[str, Any]:
    valid = [
        item
        for item in candidates
        if isinstance(item.get(value_key), Decimal) and item[value_key] > 0
    ]
    if not valid:
        return {"source": query_source, "rank": None, "total": 0, "position": None, "ordered_sources": []}
    ordered = sorted(valid, key=lambda item: item[value_key])
    ordered_sources = [
        {
            "source": item.get("source"),
            value_key: _decimal_text(item.get(value_key)),
        }
        for item in ordered
    ]
    query_index = next((index for index, item in enumerate(ordered) if item.get("source") == query_source), None)
    if query_index is None:
        return {"source": query_source, "rank": None, "total": len(ordered), "position": None, "ordered_sources": ordered_sources}
    labels = {0: "lowest", len(ordered) - 1: "highest"}
    return {
        "source": query_source,
        "rank": query_index + 1,
        "total": len(ordered),
        "position": labels.get(query_index, "middle"),
        "ordered_sources": ordered_sources,
    }


def _decimal_percent_delta(actual: Decimal | None, expected: Decimal | None) -> str | None:
    if actual is None or expected is None or expected == 0:
        return None
    return _decimal_text((actual - expected) / expected * Decimal("100"))


def _query_window_timing(
    candidates: list[dict[str, Any]],
    value_key: str,
    *,
    query_value: Decimal | None,
) -> dict[str, Any]:
    previous = next(
        (
            item.get(value_key)
            for item in candidates
            if item.get("source") == "previous_window" and isinstance(item.get(value_key), Decimal)
        ),
        None,
    )
    current = next(
        (
            item.get(value_key)
            for item in candidates
            if item.get("source") == "current_window" and isinstance(item.get(value_key), Decimal)
        ),
        None,
    )
    if query_value is None or previous is None or current is None:
        return {
            "available": False,
            "basis": value_key,
            "reason": "missing_query_or_window_value",
            "closer_to": None,
            "timing_vs_current_window": None,
        }
    previous_distance = abs(query_value - previous)
    current_distance = abs(query_value - current)
    closer_to = "previous_window" if previous_distance <= current_distance else "current_window"
    if current == previous:
        timing = "flat_window"
    elif current > previous:
        timing = "lagging" if query_value < current else "leading"
    else:
        timing = "lagging" if query_value > current else "leading"
    return {
        "available": True,
        "basis": value_key,
        "previous_window": _decimal_text(previous),
        "current_window": _decimal_text(current),
        "query_quote": _decimal_text(query_value),
        "closer_to": closer_to,
        "timing_vs_current_window": timing,
        "query_vs_previous_percent": _decimal_percent_delta(query_value, previous),
        "query_vs_current_percent": _decimal_percent_delta(query_value, current),
        "previous_to_current_percent": _decimal_percent_delta(current, previous),
        "interpretation": (
            "CoW quote is closer to the earlier Binance window"
            if closer_to == "previous_window"
            else "CoW quote is closer to the current Binance window"
        ),
    }


def _query_guard_analysis(
    *,
    role: str,
    query_value: Decimal | None,
    target_value: Decimal | None,
    acceptable_value: Decimal | None,
) -> dict[str, Any]:
    if query_value is None or target_value is None:
        return {"status": "missing_query", "favorable": False, "reason": "missing query or target value"}
    if role == "buy_price_ceiling":
        favorable = query_value <= target_value
        acceptable = acceptable_value is not None and query_value <= acceptable_value
        if favorable:
            status = "better_than_target"
            reason = "query price is below or equal to own buy target"
        elif acceptable:
            status = "within_slippage_guard"
            reason = "query price is above target but within own acceptable buy price"
        else:
            status = "worse_than_guard"
            reason = "query price is above own acceptable buy price"
    elif role == "sell_price_floor":
        favorable = query_value >= target_value
        acceptable = acceptable_value is not None and query_value >= acceptable_value
        if favorable:
            status = "better_than_target"
            reason = "query price is above or equal to own sell target"
        elif acceptable:
            status = "within_slippage_guard"
            reason = "query price is below target but within own acceptable sell price"
        else:
            status = "worse_than_guard"
            reason = "query price is below own acceptable sell price"
    elif role == "network_optimized_reference":
        favorable = query_value >= target_value
        acceptable = acceptable_value is not None and query_value >= acceptable_value
        if favorable:
            status = "better_than_target"
            reason = "query exchange rate is above or equal to own target rate"
        elif acceptable:
            status = "within_slippage_guard"
            reason = "query exchange rate is within own rate guard"
        else:
            status = "worse_than_guard"
            reason = "query exchange rate is below own target rate"
    else:
        favorable = False
        acceptable = True
        status = "not_applicable"
        reason = "stable reference step"
    return {
        "status": status,
        "favorable": favorable,
        "acceptable": acceptable,
        "reason": reason,
        "query_value": _decimal_text(query_value),
        "target_value": _decimal_text(target_value),
        "acceptable_value": _decimal_text(acceptable_value),
    }


def _amount_from_price_rule(
    *,
    role: str,
    sell_amount: Decimal,
    price_or_rate: Decimal | None,
) -> Decimal | None:
    if price_or_rate is None or price_or_rate <= 0 or sell_amount <= 0:
        return None
    if role == "buy_price_ceiling":
        return sell_amount / price_or_rate
    if role == "sell_price_floor":
        return sell_amount * price_or_rate
    if role == "network_optimized_reference":
        return sell_amount * price_or_rate
    return None
