"""Market row filtering, ranking, and COW support helpers."""

from __future__ import annotations

from typing import Any

from dex.cow_flashloan.routes import cow_network_config
from market.velocity_candidates import base_token_symbol, top_bottom_from_extremes
from market.strategy.limits import (
    DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    DEFAULT_MIN_TOKEN_PRICE_USD,
)
from market.binance_market.cow_tokens import (
    _market_registry_for_network,
    _resolve_cow_market_symbol,
)


def _rows_by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("symbol") or "").upper(): row
        for row in rows
        if isinstance(row, dict) and row.get("symbol")
    }


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cow_flashloan_control_mode() -> str:
    return "intent"


def _stable_symbol(symbol: str) -> bool:
    return str(symbol or "").upper() in {"USDC", "USDT"}


def _basket_rows_from_extremes(extremes: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(extremes, dict):
        return []
    basket = extremes.get("basket")
    if isinstance(basket, list) and basket:
        source_rows = [_market_row_from_item(item) for item in basket if isinstance(item, dict)]
        rows = []
        seen: set[str] = set()
        for row in source_rows:
            if not row:
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol or symbol in seen:
                continue
            rows.append(row)
            seen.add(symbol)
        return rows
    else:
        top_rows, bottom_rows = top_bottom_from_extremes(extremes, side_limit=1000)
    rows = []
    seen: set[str] = set()
    for row in [*top_rows, *bottom_rows]:
        symbol = str(row.get("symbol") or "")
        if not symbol or symbol in seen:
            continue
        rows.append(row)
        seen.add(symbol)
    return rows


def _market_row_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(item.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    current_price = item.get("current_price") if item.get("current_price") is not None else item.get("end_price")
    return {
        "rank": item.get("rank") or 0,
        "side": item.get("side") or "basket",
        "symbol": symbol,
        "base_symbol": str(item.get("base_symbol") or base_token_symbol(symbol)).strip().upper(),
        "change_percent": _safe_float(item.get("change_percent")),
        "start_price": _safe_float(item.get("start_price")),
        "end_price": _safe_float(item.get("end_price") if item.get("end_price") is not None else current_price),
        "current_price": _safe_float(current_price),
        "price_source": item.get("price_source"),
        "window_ready": bool(item.get("window_ready", True)),
    }


def _market_row_price_ok(row: dict[str, Any], min_token_price_usd: float) -> bool:
    threshold = max(0.0, float(min_token_price_usd))
    prices = [
        _safe_float(row.get("start_price")),
        _safe_float(row.get("current_price") if row.get("current_price") is not None else row.get("end_price")),
    ]
    observed_prices = [price for price in prices if price is not None]
    return bool(observed_prices) and min(observed_prices) >= threshold


def _market_row_change_ok(row: dict[str, Any], min_side_change_percent: float, side: str | None = None) -> bool:
    change = _safe_float(row.get("change_percent"))
    if change is None:
        return False
    threshold = max(0.0, float(min_side_change_percent))
    if side == "top":
        return change > threshold
    if side == "bottom":
        return change < -threshold
    return change > threshold or change < -threshold


def _eligible_market_rows(
    rows: list[dict[str, Any]],
    *,
    min_side_change_percent: float = DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    min_token_price_usd: float = DEFAULT_MIN_TOKEN_PRICE_USD,
    side: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible = []
    excluded = []
    for row in rows:
        copied = dict(row)
        reasons = []
        if not _market_row_change_ok(copied, min_side_change_percent, side=side):
            reasons.append("side_change_below_threshold")
        if not _market_row_price_ok(copied, min_token_price_usd):
            reasons.append("price_below_threshold")
        if reasons:
            copied["market_filter_reasons"] = reasons
            excluded.append(copied)
        else:
            eligible.append(copied)
    return eligible, excluded


def _market_filter_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("market_filter_reasons") or []:
            key = str(reason or "").strip() or "unknown"
            counts[key] = counts.get(key, 0) + 1
    return counts


def _price_filtered_market_rows(
    rows: list[dict[str, Any]],
    *,
    min_token_price_usd: float = DEFAULT_MIN_TOKEN_PRICE_USD,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if _market_row_price_ok(row, min_token_price_usd)
    ]


def _cow_supported_market_rows(
    rows: list[dict[str, Any]],
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    supported = []
    unsupported = []
    for row in rows:
        token = _resolve_cow_market_symbol(str(row.get("base_symbol") or ""), registry)
        copied = dict(row)
        if token is None:
            copied["cow_supported"] = False
            unsupported.append(copied)
            continue
        copied["cow_supported"] = True
        copied["cow_base_symbol"] = token["symbol"]
        copied["cow_token_address"] = token["address"]
        copied["cow_token_source"] = token["source"]
        copied["binance_base_symbol"] = copied.get("base_symbol")
        copied["base_symbol"] = token["symbol"]
        supported.append(copied)
    return supported, unsupported


def _cow_supported_union_market_rows(
    rows: list[dict[str, Any]],
    network_token_caches: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    supported: dict[str, dict[str, Any]] = {}
    unsupported: list[dict[str, Any]] = []
    eligible_symbols: set[str] = set()
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        base_symbol = str(row.get("base_symbol") or "").upper()
        if not symbol or not base_symbol:
            continue
        eligible_symbols.add(symbol)
        support_networks = []
        cow_symbols = []
        for network, token_cache in network_token_caches.items():
            config = cow_network_config(network=network)
            if config.testnet:
                continue
            registry = _market_registry_for_network(
                config.network,
                token_cache.get("registry") if isinstance(token_cache, dict) else {},
            )
            token = _resolve_cow_market_symbol(base_symbol, registry or {})
            if token is None:
                continue
            support_networks.append(config.network)
            cow_symbols.append(token["symbol"])
        if not support_networks:
            unsupported.append(dict(row))
            continue
        copied = dict(row)
        copied["cow_supported"] = True
        copied["cow_networks"] = support_networks
        copied["cow_network_count"] = len(support_networks)
        copied["cow_base_symbols"] = list(dict.fromkeys(cow_symbols))
        supported[symbol] = copied
    return list(supported.values()), unsupported, eligible_symbols


def _rank_supported_extremes(
    rows: list[dict[str, Any]],
    limit: int,
    *,
    min_side_change_percent: float = DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    min_token_price_usd: float = DEFAULT_MIN_TOKEN_PRICE_USD,
    enforce_side_move: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if enforce_side_move:
        eligible_rows, _ = _eligible_market_rows(
            rows,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
        )
    else:
        eligible_rows = _price_filtered_market_rows(rows, min_token_price_usd=min_token_price_usd)
    top = sorted(
        (row for row in eligible_rows if _safe_float(row.get("change_percent")) is not None and float(row.get("change_percent") or 0) > 0),
        key=lambda row: float(row.get("change_percent") or 0),
        reverse=True,
    )[: max(1, int(limit))]
    bottom = sorted(
        (row for row in eligible_rows if _safe_float(row.get("change_percent")) is not None and float(row.get("change_percent") or 0) < 0),
        key=lambda row: float(row.get("change_percent") or 0),
    )[: max(1, int(limit))]
    for index, row in enumerate(top, start=1):
        row["rank"] = index
        row["side"] = "top"
    for index, row in enumerate(bottom, start=1):
        row["rank"] = index
        row["side"] = "bottom"
    return top, bottom


__all__ = [
    "_basket_rows_from_extremes",
    "_cow_flashloan_control_mode",
    "_cow_supported_market_rows",
    "_cow_supported_union_market_rows",
    "_eligible_market_rows",
    "_market_filter_reason_counts",
    "_market_row_change_ok",
    "_market_row_from_item",
    "_market_row_price_ok",
    "_price_filtered_market_rows",
    "_rank_supported_extremes",
    "_rows_by_symbol",
    "_safe_float",
    "_stable_symbol",
]
