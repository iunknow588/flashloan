from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from db.storage_cow_tokens import load_cow_supported_tokens, replace_cow_supported_tokens
from execution.cow_flashloan_capabilities import assess_cow_flashloan_sdk_plan
from execution.cow_routes import SUPPORTED_COW_NETWORKS, CowToken, build_token_registry, cow_account_config, cow_network_config, evaluate_cow_route, load_cow_token_list, rank_cow_routes, resolve_token
from market.observer_common import DEFAULT_BINANCE_REST_BASES, env_urls, fetch_json, write_json_atomic
from market.observer_state import PriceState
from market.velocity_candidates import base_token_symbol, top_bottom_from_extremes
from strategy.arbitrage import ArbitrageConfig
from strategy.limits import (
    DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT,
    DEFAULT_COW_NETWORK_DISPLAY_LIMIT,
    DEFAULT_COW_TRADE_FEE_SIDE_COUNT,
    DEFAULT_EXECUTION_SLIPPAGE_BPS,
    DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    DEFAULT_MIN_COW_SPREAD_PERCENT,
    DEFAULT_MIN_SIDE_CHANGE_PERCENT,
    DEFAULT_MIN_TOKEN_PRICE_USD,
)

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AAVE_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"
DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH = SRC_ROOT / "runtime" / "state" / "binance_market_snapshot.json"
DEFAULT_COW_TOKEN_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "cow_supported_tokens.json"
DEFAULT_COW_TEST_NETWORK = "avalanche"
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
WRAPPED_SYMBOL_ALIASES = {
    "ETH": ("WETH",),
    "AVAX": ("WAVAX", "WAVAX.E"),
    "MATIC": ("WPOL", "WMATIC", "POL"),
    "POL": ("WPOL", "WMATIC", "MATIC"),
    "BNB": ("WBNB",),
    "XDAI": ("WXDAI", "WDAI"),
}
_COW_TOKEN_CACHE_LOCK = threading.Lock()
_COW_TOKEN_MEMORY_CACHE: dict[str, dict[str, Any]] = {}
STABLE_ROUTE_CANDIDATES = (
    ("stable_usdc_to_y_to_x_to_usdc", ("USDC", "y", "x", "USDC")),
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


def _serialize_decimal_candidates(
    candidates: list[dict[str, Any]],
    value_key: str,
) -> list[dict[str, Any]]:
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


def _stable_symbol(symbol: str) -> bool:
    return str(symbol or "").upper() in {"USDC", "USDT"}


def _cow_symbol_candidates(symbol: str) -> tuple[str, ...]:
    value = str(symbol or "").strip().upper()
    aliases = WRAPPED_SYMBOL_ALIASES.get(value, ())
    return (value, *aliases)


def _resolve_cow_market_symbol(symbol: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in _cow_symbol_candidates(symbol):
        try:
            token = resolve_token(candidate, registry)
            return {
                "symbol": token.symbol,
                "address": token.address,
                "decimals": token.decimals,
                "source": token.source,
                "matched_from": symbol,
            }
        except Exception:
            continue
    return None


def _token_to_dict(token: CowToken | dict[str, Any]) -> dict[str, Any]:
    if isinstance(token, CowToken):
        return {
            "symbol": token.symbol,
            "address": token.address,
            "decimals": token.decimals,
            "source": token.source,
        }
    return {
        "symbol": str(token.get("symbol") or "").upper(),
        "address": str(token.get("address") or "").lower(),
        "decimals": int(token.get("decimals") or 0),
        "source": str(token.get("source") or ""),
    }


def _tokens_to_registry(tokens: list[dict[str, Any]]) -> dict[str, CowToken]:
    by_address: dict[str, CowToken] = {}
    for item in tokens:
        try:
            token = CowToken(
                str(item["symbol"]).upper(),
                str(item["address"]).lower(),
                int(item["decimals"]),
                str(item.get("source") or "cow_supported_cache"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        by_address.setdefault(token.address, token)
    symbol_counts: dict[str, int] = {}
    for token in by_address.values():
        symbol_counts[token.symbol.upper()] = symbol_counts.get(token.symbol.upper(), 0) + 1
    registry = dict(by_address)
    for token in by_address.values():
        if symbol_counts.get(token.symbol.upper()) == 1:
            registry[token.symbol.upper()] = token
    return registry


def _dedupe_tokens_by_address(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen: set[str] = set()
    for token in tokens:
        address = str(token.get("address") or "").strip().lower()
        if not address or address in seen:
            continue
        copied = dict(token)
        copied["address"] = address
        deduped.append(copied)
        seen.add(address)
    return deduped


def _read_cow_token_file(cache_path: Path, network: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    networks = payload.get("networks") if isinstance(payload, dict) else {}
    item = networks.get(network) if isinstance(networks, dict) else None
    tokens = item.get("tokens") if isinstance(item, dict) else None
    return tokens if isinstance(tokens, list) else []


def _write_cow_token_file(cache_path: Path, *, network: str, chain_id: int, tokens: list[dict[str, Any]]) -> None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    networks = payload.setdefault("networks", {})
    networks[network] = {
        "network": network,
        "chain_id": int(chain_id),
        "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "token_count": len(tokens),
        "tokens": tokens,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _memory_cow_tokens(network: str) -> list[dict[str, Any]]:
    with _COW_TOKEN_CACHE_LOCK:
        item = _COW_TOKEN_MEMORY_CACHE.get(network)
        if not item:
            return []
        return list(item.get("tokens") or [])


def _store_memory_cow_tokens(network: str, tokens: list[dict[str, Any]], *, source: str) -> None:
    with _COW_TOKEN_CACHE_LOCK:
        _COW_TOKEN_MEMORY_CACHE[network] = {
            "tokens": list(tokens),
            "source": source,
            "loaded_at": time.time(),
        }


def refresh_cow_supported_token_cache(
    *,
    cow_network: str | None = DEFAULT_COW_TEST_NETWORK,
    database_url: str | None = None,
    cache_path: Path = DEFAULT_COW_TOKEN_CACHE_PATH,
) -> dict[str, Any]:
    network_config = cow_network_config(network=cow_network)
    tokens = _dedupe_tokens_by_address(
        [_token_to_dict(token) for token in load_cow_token_list(network=network_config.network)]
    )
    if database_url:
        replace_cow_supported_tokens(
            database_url,
            network=network_config.network,
            chain_id=network_config.chain_id,
            tokens=tokens,
        )
    _write_cow_token_file(cache_path, network=network_config.network, chain_id=network_config.chain_id, tokens=tokens)
    _store_memory_cow_tokens(network_config.network, tokens, source="refresh")
    return {
        "network": network_config.network,
        "chain_id": network_config.chain_id,
        "token_count": len(tokens),
        "database_saved": bool(database_url),
        "file_cache": str(cache_path),
        "source": "cow_token_list",
    }


def load_cow_supported_token_registry(
    *,
    cow_network: str | None = DEFAULT_COW_TEST_NETWORK,
    database_url: str | None = None,
    cache_path: Path = DEFAULT_COW_TOKEN_CACHE_PATH,
    allow_live_fallback: bool = True,
) -> dict[str, Any]:
    network_config = cow_network_config(network=cow_network)
    source = "memory"
    tokens = _memory_cow_tokens(network_config.network)
    if not tokens and database_url:
        try:
            tokens = load_cow_supported_tokens(database_url, network=network_config.network)
            source = "database"
        except Exception:
            tokens = []
    if not tokens:
        tokens = _read_cow_token_file(cache_path, network_config.network)
        source = "file"
    if not tokens and allow_live_fallback:
        refreshed = refresh_cow_supported_token_cache(
            cow_network=network_config.network,
            database_url=database_url,
            cache_path=cache_path,
        )
        tokens = _memory_cow_tokens(network_config.network)
        source = f"live_refresh:{refreshed['source']}"
    if tokens:
        _store_memory_cow_tokens(network_config.network, tokens, source=source)
    return {
        "network": network_config.network,
        "chain_id": network_config.chain_id,
        "source": source if tokens else "empty",
        "token_count": len(tokens),
        "tokens": tokens,
        "registry": _tokens_to_registry(tokens),
    }


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
            registry = token_cache.get("registry") if isinstance(token_cache, dict) else {}
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible_rows, _ = _eligible_market_rows(
        rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
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
            selection_rule = "买入：前期窗口价、当前窗口价、CoW 查询价三者取最低价作为目标价，中间价作为可接受滑点价"
        elif not _stable_symbol(from_key) and not _stable_symbol(to_key):
            phase = "start"
            price_basis = "pre_change_binance_cross"
            rule = "cross tokens at the best output ratio among previous, current, and query ratios"
            target_role = "network_optimized_reference"
            target_price_usd = None
            selection_rule = "中间兑换：前期窗口兑换比例、当前窗口兑换比例、CoW 查询比例三者取最高比例，力争换到最多目标代币"
        elif not _stable_symbol(from_key) and _stable_symbol(to_key):
            phase = "current"
            price_basis = "current_binance_sell_high"
            rule = "sell target token at the highest of previous, current, and query prices"
            target_role = "sell_price_floor"
            target_price_usd = None
            selection_rule = "卖出：前期窗口价、当前窗口价、CoW 查询价三者取最高价作为目标价，中间价作为可接受滑点价"
        else:
            phase = "current"
            price_basis = "stable_reference"
            rule = "stable reference conversion"
            target_role = "stable_reference"
            target_price_usd = None
            selection_rule = "稳定币参考：按 1:1 参考"

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
            cow_parameter_rule = "sellAmountBeforeFee 固定输入；buyAmountAfterFee/minBuyAmountAfterFee 用可接受滑点价换算"
            price_compare_rule = "买入使用三价最低作为目标，三价中间值作为可接受滑点"
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
            cow_parameter_rule = "sellAmountBeforeFee 固定输入；buyAmountAfterFee/minBuyAmountAfterFee 用可接受滑点价换算"
            price_compare_rule = "卖出使用三价最高作为目标，三价中间值作为可接受滑点"
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
            cow_parameter_rule = "sellAmountBeforeFee 固定输入；中间 hop 用最高兑换比例做目标，用中间兑换比例做可接受滑点"
            price_compare_rule = "中间兑换使用三种兑换比例中的最高值，实际路由交给 CoW/network 优化"
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
        "priority_reason": "buy_loser_then_gainer",
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
        _route_candidate(strategy, route, x, y, index, config.notional_usd, slippage_bps=slippage_bps)
        for index, (strategy, route) in enumerate(STABLE_ROUTE_CANDIDATES, start=1)
    ]
    row.update(
        {
            "strategy": None,
            "route_results": route_results,
            "route_results_full": route_results,
            "route_count": len(route_results),
            "full_route_count": len(route_results),
            "borrow_symbol": None,
            "swap_symbol": None,
        }
    )
    return row


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


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


def cow_network_options() -> dict[str, Any]:
    networks = []
    for name, config in SUPPORTED_COW_NETWORKS.items():
        account = cow_account_config(config.network)
        networks.append(
            {
                "network": name,
                "label": COW_NETWORK_LABELS.get(name, name),
                "chain_id": config.chain_id,
                "testnet": config.testnet,
                "quote_api": config.quote_api,
                "owner": account.owner,
                "owner_source": account.owner_source,
            }
        )
    return {
        "default_network": DEFAULT_COW_TEST_NETWORK,
        "networks": networks,
    }


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
    side_limit: int = 5,
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


def needs_binance_rest_snapshot(extremes: dict[str, Any] | None, *, side_limit: int = 5) -> bool:
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


def read_binance_market_snapshot(path: Path = DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH) -> dict[str, Any] | None:
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
) -> None:
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
    side_limit: int = 5,
    snapshot_path: Path = DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH,
    current_rows: list[dict[str, Any]] | None = None,
    persist_snapshot: bool = True,
    max_previous_age_seconds: float | None = DEFAULT_BINANCE_MARKET_PREVIOUS_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    if not needs_binance_rest_snapshot(realtime_extremes, side_limit=side_limit):
        selected = dict(realtime_extremes or {})
        selected.setdefault("market_state_source", "realtime_extremes")
        return selected
    snapshot = read_binance_market_snapshot(snapshot_path)
    previous_snapshot = _previous_window_snapshot(
        snapshot,
        max_age_seconds=max_previous_age_seconds,
    )
    try:
        selected = build_binance_rest_market_snapshot(
            side_limit=side_limit,
            previous_snapshot=previous_snapshot,
            current_rows=current_rows,
        )
        selected["market_state_source"] = "rest_interval_live"
        selected["fallback_reason"] = (
            "latest_extremes_insufficient"
            if previous_snapshot is not None
            else "previous_window_missing_or_stale"
        )
        if persist_snapshot:
            write_binance_market_snapshot(selected, snapshot_path)
        return selected
    except Exception:
        if snapshot and not needs_binance_rest_snapshot(snapshot, side_limit=side_limit):
            selected = dict(snapshot)
            selected["market_state_source"] = "snapshot_file_stale_fallback"
            selected["fallback_reason"] = "rest_interval_refresh_failed"
            return selected
        raise


def _cow_route_specs(pair: dict[str, Any], amount: str | int | float | Decimal) -> list[dict[str, Any]]:
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
                pass
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
    buy_loser_path = ["USDC", y, x, "USDC"]
    return [
        {
            "name": f"{pair.get('rank', 0)}_buy_loser_{y}_then_gainer_{x}",
            "path": buy_loser_path,
            "amount": amount,
            "pair_rank": pair.get("rank"),
            "pair": pair.get("pair"),
            "priority_reason": "buy_loser_then_gainer",
            "edge_hint_percent": pair.get("edge_hint_percent"),
            "binance_execution_plan": _binance_execution_plan(buy_loser_path, x_row, y_row, amount, slippage_bps=slippage_bps),
        },
    ]


def _cow_cost_summary(result: dict[str, Any], *, final_delta_amount: str | None) -> dict[str, Any]:
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
        "profit_amount": final_delta_amount,
        "profit_symbol": result.get("final_symbol"),
    }


def _cow_min_profit_usd_for_amount(amount: Any) -> Decimal:
    notional = _decimal_value(amount)
    if notional is None or notional <= 0:
        notional = Decimal("1000")
    return notional * DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT / Decimal("100")


def _cow_own_limit_order_intent(plan: dict[str, Any] | None, *, min_profit_usd: Decimal) -> dict[str, Any]:
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
    profit_ready = profit_amount is not None and profit_amount >= min_profit_usd
    stable_final = _stable_symbol(str(plan.get("final_symbol") or ""))
    ready = bool(steps) and not missing and profit_ready and stable_final
    reason = "ready" if ready else (
        "missing_limit_parameters" if missing else (
            "own_limit_profit_below_threshold" if not profit_ready else "non_stable_final_symbol"
        )
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


def _cow_order_submission_enabled() -> bool:
    raw = os.getenv("COW_ORDER_SUBMISSION_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _attach_cow_flashloan_sdk_plan(
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
                router_address=os.getenv("COW_FLASHLOAN_ROUTER_ADDRESS", "").strip() or None,
                lender_address=os.getenv("COW_FLASHLOAN_LENDER_ADDRESS", "").strip() or None,
                borrower_address=os.getenv("COW_FLASHLOAN_BORROWER_ADDRESS", "").strip() or None,
                settlement_calldata=os.getenv("COW_FLASHLOAN_SETTLEMENT_CALLDATA", "").strip() or None,
            ),
            "required_probe_methods": [
                "getSwapQuoteParams",
                "TradingSdk.getQuoteOnly",
                "getOrderPostingSettings",
            ],
            "required_submission_method": (
                "IFlashLoanRouter.flashLoanAndSettle(loans, abi.encodeCall(settle))"
                if len(steps) >= 3 and route and route[0] == route[-1]
                else "blocked: flashloan arbitrage requires a closed three-hop solver path"
            ),
            "settlement_boundary": "official CoW settlement/solver path; no custom CoW contract deployment",
        }
        result["cow_flashloan_sdk_error"] = None
    except Exception as exc:
        result["cow_flashloan_sdk_plan"] = None
        result["cow_flashloan_sdk_error"] = str(exc)


def _execution_plan_rows(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("symbol") or "").upper(): item
        for item in plan.get("market_prices") or []
        if isinstance(item, dict) and item.get("symbol")
    }


def _cow_query_price_for_step(step: dict[str, Any], hop: dict[str, Any] | None) -> Decimal | None:
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


def _cow_query_rate_for_step(step: dict[str, Any], hop: dict[str, Any] | None) -> Decimal | None:
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


def _apply_cow_quote_targets(plan: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or not plan.get("available"):
        return plan
    enriched = deepcopy(plan)
    rows = _execution_plan_rows(enriched)
    hops = [hop for hop in result.get("hops") or [] if isinstance(hop, dict)]
    for index, step in enumerate(enriched.get("steps") or []):
        if not isinstance(step, dict):
            continue
        hop = hops[index] if index < len(hops) else None
        role = str(step.get("target_role") or "")
        from_symbol = str(step.get("from_symbol") or "").upper()
        to_symbol = str(step.get("to_symbol") or "").upper()
        sell_amount = _decimal_value(hop.get("sell_amount") if hop else None) or _decimal_value(step.get("input_amount"))
        if sell_amount is None or sell_amount <= 0:
            continue
        query_price = _cow_query_price_for_step(step, hop)
        query_rate = _cow_query_rate_for_step(step, hop)
        target_price_candidate = None
        acceptable_price_candidate = None
        target_rate_candidate = None
        acceptable_rate_candidate = None
        price_candidates: list[dict[str, Any]] = []
        rate_candidates: list[dict[str, Any]] = []
        if role == "buy_price_ceiling":
            price_candidates = _market_price_candidates(to_symbol, rows, query_price=query_price)
            target_price_candidate = _select_decimal_candidate(price_candidates, "price", "min")
            acceptable_price_candidate = _select_decimal_candidate(price_candidates, "price", "median") or target_price_candidate
            target_value = target_price_candidate.get("price") if target_price_candidate else None
            acceptable_value = acceptable_price_candidate.get("price") if acceptable_price_candidate else None
            target_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=target_value)
            min_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=acceptable_value)
            step.update(
                {
                    "query_price_usd_per_token": _decimal_text(query_price),
                    "target_price_usd_per_token": _decimal_text(target_value),
                    "acceptable_price_usd_per_token": _decimal_text(acceptable_value),
                    "selected_target_price_usd_per_token": _decimal_text(target_value),
                    "acceptable_slippage_price_usd_per_token": _decimal_text(acceptable_value),
                    "selected_target_exchange_rate": _decimal_text((target_amount / sell_amount) if target_amount is not None else None),
                    "acceptable_slippage_exchange_rate": _decimal_text((min_amount / sell_amount) if min_amount is not None else None),
                    "target_output_amount": _decimal_text(target_amount),
                    "min_output_amount": _decimal_text(min_amount),
                    "selection_rule": "买入：三价取最低价作为目标价，中间价作为可接受滑点价",
                }
            )
        elif role == "sell_price_floor":
            price_candidates = _market_price_candidates(from_symbol, rows, query_price=query_price)
            target_price_candidate = _select_decimal_candidate(price_candidates, "price", "max")
            acceptable_price_candidate = _select_decimal_candidate(price_candidates, "price", "median") or target_price_candidate
            target_value = target_price_candidate.get("price") if target_price_candidate else None
            acceptable_value = acceptable_price_candidate.get("price") if acceptable_price_candidate else None
            target_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=target_value)
            min_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=acceptable_value)
            step.update(
                {
                    "query_price_usd_per_token": _decimal_text(query_price),
                    "target_price_usd_per_token": _decimal_text(target_value),
                    "acceptable_price_usd_per_token": _decimal_text(acceptable_value),
                    "selected_target_price_usd_per_token": _decimal_text(target_value),
                    "acceptable_slippage_price_usd_per_token": _decimal_text(acceptable_value),
                    "selected_target_exchange_rate": _decimal_text((target_amount / sell_amount) if target_amount is not None else None),
                    "acceptable_slippage_exchange_rate": _decimal_text((min_amount / sell_amount) if min_amount is not None else None),
                    "target_output_amount": _decimal_text(target_amount),
                    "min_output_amount": _decimal_text(min_amount),
                    "selection_rule": "卖出：三价取最高价作为目标价，中间价作为可接受滑点价",
                }
            )
        elif role == "network_optimized_reference":
            rate_candidates = _exchange_rate_candidates(from_symbol, to_symbol, rows, query_rate=query_rate)
            target_rate_candidate = _select_decimal_candidate(rate_candidates, "rate", "max")
            acceptable_rate_candidate = target_rate_candidate
            target_value = target_rate_candidate.get("rate") if target_rate_candidate else None
            acceptable_value = acceptable_rate_candidate.get("rate") if acceptable_rate_candidate else None
            target_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=target_value)
            min_amount = _amount_from_price_rule(role=role, sell_amount=sell_amount, price_or_rate=acceptable_value)
            step.update(
                {
                    "query_exchange_rate": _decimal_text(query_rate),
                    "target_exchange_rate": _decimal_text(target_value),
                    "min_exchange_rate": _decimal_text(acceptable_value),
                    "selected_target_exchange_rate": _decimal_text(target_value),
                    "acceptable_slippage_exchange_rate": _decimal_text(acceptable_value),
                    "target_output_amount": _decimal_text(target_amount),
                    "min_output_amount": _decimal_text(min_amount),
                    "selection_rule": "中间兑换：三种兑换比例取最高比例作为目标，中间比例作为可接受滑点",
                }
            )
        else:
            target_amount = _decimal_value(step.get("target_output_amount"))
            min_amount = _decimal_value(step.get("min_output_amount"))
        step["price_candidates"] = _serialize_decimal_candidates(price_candidates, "price")
        step["rate_candidates"] = _serialize_decimal_candidates(rate_candidates, "rate")
        step["selected_target_source"] = _selected_source(target_price_candidate or target_rate_candidate)
        step["selected_acceptable_source"] = _selected_source(acceptable_price_candidate or acceptable_rate_candidate)
        step["query_sell_amount_before_fee"] = _decimal_text(sell_amount)
        step["query_buy_amount_after_fee"] = hop.get("buy_amount") if hop else None
        sdk_parameters = dict(step.get("cow_sdk_parameters") or {})
        sdk_parameters.update(
            {
                "quote_kind": "sell",
                "sell_token_symbol": from_symbol,
                "buy_token_symbol": to_symbol,
                "sell_amount_before_fee": _decimal_text(sell_amount),
                "target_buy_amount_after_fee": _decimal_text(target_amount),
                "min_buy_amount_after_fee": _decimal_text(min_amount),
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


def _apply_cow_quote_analysis(plan: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or not plan.get("available"):
        return plan
    enriched = deepcopy(plan)
    rows = _execution_plan_rows(enriched)
    hops = [hop for hop in result.get("hops") or [] if isinstance(hop, dict)]
    for index, step in enumerate(enriched.get("steps") or []):
        if not isinstance(step, dict):
            continue
        hop = hops[index] if index < len(hops) else None
        role = str(step.get("target_role") or "")
        from_symbol = str(step.get("from_symbol") or "").upper()
        to_symbol = str(step.get("to_symbol") or "").upper()
        sell_amount = _decimal_value(hop.get("sell_amount") if hop else None) or _decimal_value(step.get("input_amount"))
        if sell_amount is None or sell_amount <= 0:
            continue
        query_price = _cow_query_price_for_step(step, hop)
        query_rate = _cow_query_rate_for_step(step, hop)
        price_candidates: list[dict[str, Any]] = []
        rate_candidates: list[dict[str, Any]] = []
        if role == "buy_price_ceiling":
            price_candidates = _market_price_candidates(to_symbol, rows, query_price=query_price)
            target_value = _decimal_value(step.get("target_price_usd_per_token"))
            acceptable_value = _decimal_value(step.get("acceptable_price_usd_per_token"))
            step["query_price_usd_per_token"] = _decimal_text(query_price)
            step["query_price_position"] = _decimal_rank_analysis(price_candidates, "price")
            step["query_window_timing"] = _query_window_timing(price_candidates, "price", query_value=query_price)
            step["query_guard_analysis"] = _query_guard_analysis(
                role=role,
                query_value=query_price,
                target_value=target_value,
                acceptable_value=acceptable_value,
            )
        elif role == "sell_price_floor":
            price_candidates = _market_price_candidates(from_symbol, rows, query_price=query_price)
            target_value = _decimal_value(step.get("target_price_usd_per_token"))
            acceptable_value = _decimal_value(step.get("acceptable_price_usd_per_token"))
            step["query_price_usd_per_token"] = _decimal_text(query_price)
            step["query_price_position"] = _decimal_rank_analysis(price_candidates, "price")
            step["query_window_timing"] = _query_window_timing(price_candidates, "price", query_value=query_price)
            step["query_guard_analysis"] = _query_guard_analysis(
                role=role,
                query_value=query_price,
                target_value=target_value,
                acceptable_value=acceptable_value,
            )
        elif role == "network_optimized_reference":
            rate_candidates = _exchange_rate_candidates(from_symbol, to_symbol, rows, query_rate=query_rate)
            target_value = _decimal_value(step.get("target_exchange_rate"))
            acceptable_value = _decimal_value(step.get("min_exchange_rate"))
            step["query_exchange_rate"] = _decimal_text(query_rate)
            step["query_rate_position"] = _decimal_rank_analysis(rate_candidates, "rate")
            step["query_window_timing"] = _query_window_timing(rate_candidates, "rate", query_value=query_rate)
            step["query_guard_analysis"] = _query_guard_analysis(
                role=role,
                query_value=query_rate,
                target_value=target_value,
                acceptable_value=acceptable_value,
            )
        step["price_candidates"] = _serialize_decimal_candidates(price_candidates, "price")
        step["rate_candidates"] = _serialize_decimal_candidates(rate_candidates, "rate")
        step["query_sell_amount_before_fee"] = _decimal_text(sell_amount)
        step["query_buy_amount_after_fee"] = hop.get("buy_amount") if hop else None
        sdk_parameters = dict(step.get("cow_sdk_parameters") or {})
        sdk_parameters["query_buy_amount_after_fee"] = hop.get("buy_amount") if hop else None
        step["cow_sdk_parameters"] = sdk_parameters
        step["cow_limit_enabled"] = True
    enriched["quote_parameter_selection"] = "own_window_prices_guard_order_parameters; query_quote_only_for_position_analysis"
    return enriched


def _cow_execution_precheck(result: dict[str, Any]) -> dict[str, Any]:
    plan = result.get("binance_execution_plan") if isinstance(result, dict) else None
    steps = plan.get("steps") if isinstance(plan, dict) else []
    hops = [hop for hop in result.get("hops") or [] if isinstance(hop, dict)]
    quote_error = _cow_quote_error_info(result.get("error"))
    quote_error_type = quote_error.get("type")
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
    final_delta = _decimal_value(result.get("final_delta_amount"))
    input_amount = _decimal_value(result.get("input_amount"))
    min_profit_usd = _cow_min_profit_usd_for_amount(result.get("input_amount"))
    own_limit_order_intent = _cow_own_limit_order_intent(plan, min_profit_usd=min_profit_usd)
    cow_sdk_plan = result.get("cow_flashloan_sdk_plan") if isinstance(result.get("cow_flashloan_sdk_plan"), dict) else None
    cow_sdk_error = result.get("cow_flashloan_sdk_error")
    profit_positive = final_delta is not None and final_delta > 0
    drawdown_amount = -final_delta if final_delta is not None and final_delta < 0 else Decimal("0")
    drawdown_percent = (
        drawdown_amount / input_amount * Decimal("100")
        if input_amount is not None and input_amount > 0
        else None
    )
    profit_above_auto_threshold = (
        final_delta is not None
        and final_delta >= min_profit_usd
        and _stable_symbol(str(result.get("final_symbol") or ""))
    )
    price_guards_passed = bool(hop_checks) and all(item["price_guard_passed"] for item in hop_checks)
    blocking_cause_counts: dict[str, int] = {}
    for item in hop_checks:
        cause = item.get("failure_cause")
        if cause:
            blocking_cause_counts[cause] = blocking_cause_counts.get(cause, 0) + 1
    reasons = []
    if not route_supported:
        reasons.append("CoW 不支持该路径中的一个或多个代币")
    if not quote_available:
        reasons.append(result.get("error") or "尚未拿到可用 CoW 报价")
    if not price_guards_passed:
        reasons.append("至少一个 hop 的 CoW 查询输出低于按滑点价计算的最低接收量")
    if not price_guards_passed and blocking_cause_counts:
        reasons.append(f"主要阻断原因：{max(blocking_cause_counts, key=blocking_cause_counts.get)}")
    if not profit_positive:
        reasons.append(f"最终 CoW 盈利不为正：{result.get('final_delta_amount') or '-'} {result.get('final_symbol') or ''}".strip())
        if drawdown_amount > 0:
            reasons.append(
                f"回撤检查：本次报价相对输入回撤 {_decimal_text(drawdown_amount)} "
                f"{result.get('final_symbol') or ''} ({_decimal_text(drawdown_percent)}%)".strip()
            )
    elif not profit_above_auto_threshold:
        reasons.append(
            f"手续费后净利润低于自动执行阈值：{result.get('final_delta_amount') or '-'} "
            f"{result.get('final_symbol') or ''} < {_decimal_text(min_profit_usd)}U".strip()
        )
    normalized_reasons = []
    if not route_supported:
        normalized_reasons.append("cow_route_has_unsupported_token")
    if not quote_available:
        normalized_reasons.append(quote_error.get("display") or "cow_quote_unavailable")
        if quote_error_type:
            normalized_reasons.append(f"quote_error_type:{quote_error_type}")
        normalized_reasons.append("quote_missing_skip_price_profit_drawdown_checks")
    if quote_available and not price_guards_passed:
        normalized_reasons.append("at_least_one_hop_query_output_below_own_minimum")
        if blocking_cause_counts:
            normalized_reasons.append(f"primary_blocker:{max(blocking_cause_counts, key=blocking_cause_counts.get)}")
    if quote_available and not profit_positive:
        normalized_reasons.append(f"cow_final_profit_not_positive:{result.get('final_delta_amount') or '-'} {result.get('final_symbol') or ''}".strip())
        if drawdown_amount > 0:
            normalized_reasons.append(
                f"cow_quote_drawdown:{_decimal_text(drawdown_amount)} "
                f"{result.get('final_symbol') or ''} ({_decimal_text(drawdown_percent)}%)".strip()
            )
    elif quote_available and not profit_above_auto_threshold:
        normalized_reasons.append(
            f"profit_below_auto_threshold:{result.get('final_delta_amount') or '-'} "
            f"{result.get('final_symbol') or ''} < {_decimal_text(min_profit_usd)}U".strip()
        )
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
        and limit_intent_ready
        and cow_sdk_plan is not None
        and not cow_sdk_error
        and bool(flashloan_capability)
        and flashloan_submission_safe
    )
    if cow_sdk_flashloan_ready:
        reasons.append("cow_flashloan_sdk_intent_ready")
    elif route_supported and limit_intent_ready:
        capability_blockers = flashloan_capability.get("blockers") or []
        if capability_blockers:
            reasons.append(f"flashLoanAndSettle_required:{','.join(capability_blockers)}")
        else:
            reasons.append(f"cow_flashloan_sdk_plan_required:{cow_sdk_error or 'missing_sdk_plan'}")
    checks_passed = (
        route_supported
        and quote_available
        and price_guards_passed
        and profit_above_auto_threshold
        and limit_intent_ready
        and cow_sdk_flashloan_ready
    )
    order_submission_enabled = _cow_order_submission_enabled()
    if checks_passed and not order_submission_enabled:
        status = "checks_passed_order_disabled"
        reasons.append("报价、价格保护、盈利检查通过；真实下单模块尚未开放")
    elif not route_supported:
        status = "unsupported"
    elif not quote_available:
        status = "quote_unavailable"
    elif not price_guards_passed:
        status = "price_guard_failed"
    elif not profit_positive:
        status = "not_profitable"
    elif not profit_above_auto_threshold:
        status = "profit_below_threshold"
    elif not cow_sdk_flashloan_ready:
        status = "cow_flashloan_sdk_plan_required"
    else:
        status = "blocked"
    if checks_passed:
        status = "limit_order_ready_to_submit" if order_submission_enabled else "limit_order_ready_not_submitted"
        if not order_submission_enabled and "own_limit_order_ready_but_submission_adapter_disabled" not in reasons:
            reasons.append("own_limit_order_ready_but_submission_adapter_disabled")
    return {
        "status": status,
        "checks_passed": checks_passed,
        "can_submit_order": checks_passed and order_submission_enabled,
        "order_submission_enabled": order_submission_enabled,
        "auto_execute_requested": checks_passed,
        "auto_execute_min_profit_usd": _decimal_text(min_profit_usd),
        "auto_execute_min_profit_percent": _decimal_text(DEFAULT_COW_AUTO_EXECUTE_MIN_PROFIT_PERCENT),
        "auto_execute_blocked": checks_passed and not order_submission_enabled,
        "route_supported": route_supported,
        "quote_available": quote_available,
        "own_limit_order_ready": limit_intent_ready,
        "own_limit_order_intent": own_limit_order_intent,
        "cow_sdk_flashloan_ready": cow_sdk_flashloan_ready,
        "cow_flashloan_sdk_plan": cow_sdk_plan,
        "cow_flashloan_sdk_error": cow_sdk_error,
        "flashloan_capability": flashloan_capability,
        "quote_price_guards_passed": price_guards_passed,
        "price_guards_passed": price_guards_passed,
        "profit_positive": profit_positive,
        "profit_above_auto_threshold": profit_above_auto_threshold,
        "drawdown_amount": _decimal_text(drawdown_amount),
        "drawdown_percent": _decimal_text(drawdown_percent),
        "pure_profit_amount": result.get("final_delta_amount"),
        "final_delta_amount": result.get("final_delta_amount"),
        "final_symbol": result.get("final_symbol"),
        "hop_checks": hop_checks,
        "blocking_cause_counts": blocking_cause_counts,
        "quote_error_type": quote_error_type,
        "quote_error_display": quote_error.get("display"),
        "quote_error_retryable": quote_error.get("retryable"),
        "quote_error_raw": quote_error.get("raw"),
        "reasons": reasons,
    }


def _cow_route_support(spec: dict[str, Any], registry: dict[str, Any], *, cow_network: str) -> dict[str, Any]:
    path = [str(item or "").upper() for item in spec.get("path") or []]
    unsupported = []
    tokens = []
    for symbol in path:
        try:
            token = resolve_token(symbol, registry)
            tokens.append(
                {
                    "symbol": token.symbol,
                    "address": token.address,
                    "decimals": token.decimals,
                    "source": token.source,
                }
            )
        except Exception:
            unsupported.append(symbol)
    unsupported = list(dict.fromkeys(unsupported))
    supported = not unsupported and bool(path)
    return {
        "pair": spec.get("pair"),
        "pair_rank": spec.get("pair_rank"),
        "priority_reason": spec.get("priority_reason"),
        "path": path,
        "cow_network": cow_network,
        "supported": supported,
        "status": "supported" if supported else "unsupported_tokens",
        "unsupported_tokens": unsupported,
        "tokens": tokens,
        "error": None if supported else f"unsupported CoW tokens on {cow_network}: {', '.join(unsupported)}",
    }


def build_cow_route_precheck(
    market_state: dict[str, Any],
    *,
    amount: str | int | float | Decimal = "1000",
    quote_limit: int = 25,
    cow_network: str | None = DEFAULT_COW_TEST_NETWORK,
    aave_cache_path: Path = DEFAULT_AAVE_CACHE_PATH,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    network_config = cow_network_config(network=cow_network)
    selected_pairs = list(market_state.get("pairs") or [])[: max(1, int(quote_limit))]
    route_specs = [spec for pair in selected_pairs for spec in _cow_route_specs(pair, amount)]
    token_registry = registry if registry is not None else build_token_registry(
        aave_cache_path=aave_cache_path,
        include_cow_token_list=True,
        cow_network=network_config.network,
    )
    routes = [
        _cow_route_support(spec, token_registry, cow_network=network_config.network)
        for spec in route_specs
    ]
    return {
        "cow_network": network_config.network,
        "cow_chain_id": network_config.chain_id,
        "cow_testnet": network_config.testnet,
        "selected_pair_count": len(selected_pairs),
        "route_count": len(routes),
        "supported_route_count": sum(1 for item in routes if item["supported"]),
        "unsupported_route_count": sum(1 for item in routes if not item["supported"]),
        "routes": routes,
    }


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


def build_cow_network_market_claims(
    extremes: dict[str, Any] | None,
    network_token_caches: dict[str, dict[str, Any]],
    *,
    limit: int = DEFAULT_COW_NETWORK_DISPLAY_LIMIT,
    min_spread_percent: float = 1.0,
    min_side_change_percent: float = DEFAULT_MIN_SIDE_CHANGE_PERCENT,
    min_token_price_usd: float = DEFAULT_MIN_TOKEN_PRICE_USD,
    threshold_detail: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    min_side_change_percent = DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT
    market_rows = _basket_rows_from_extremes(extremes)
    eligible_market_rows, excluded_market_rows = _eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    summaries = []
    for network, token_cache in network_token_caches.items():
        config = cow_network_config(network=network)
        if config.testnet:
            continue
        registry = token_cache.get("registry") if isinstance(token_cache, dict) else {}
        supported_rows, unsupported_rows = _cow_supported_market_rows(eligible_market_rows, registry or {})
        top_rows, bottom_rows = _rank_supported_extremes(
            supported_rows,
            limit,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
        )
        summaries.append(
            {
                "network": config.network,
                "label": COW_NETWORK_LABELS.get(config.network, config.network),
                "chain_id": config.chain_id,
                "token_cache_source": token_cache.get("source") if isinstance(token_cache, dict) else None,
                "token_cache_count": token_cache.get("token_count") if isinstance(token_cache, dict) else 0,
                "supported_symbol_count": len(supported_rows),
                "unsupported_symbol_count": len(unsupported_rows),
                "market_eligible_symbol_count": len(eligible_market_rows),
                "market_excluded_symbol_count": len(excluded_market_rows),
                "min_side_change_percent": float(min_side_change_percent),
                "min_token_price_usd": float(min_token_price_usd),
                "min_spread_percent": max(0.0, float(min_spread_percent)),
                "threshold_detail": threshold_detail or {},
                "pair_count": _candidate_pair_count(top_rows, bottom_rows, min_spread_percent),
                "top": top_rows,
                "bottom": bottom_rows,
            }
        )
    return sorted(summaries, key=lambda item: (-int(item.get("supported_symbol_count") or 0), item.get("network") or ""))


def build_cow_supported_market_overview(
    extremes: dict[str, Any] | None,
    network_token_caches: dict[str, dict[str, Any]],
    *,
    limit: int = 50,
    min_side_change_percent: float = DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    min_token_price_usd: float = DEFAULT_MIN_TOKEN_PRICE_USD,
    threshold_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    min_side_change_percent = DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT
    market_rows = _basket_rows_from_extremes(extremes)
    top_eligible_market_rows, top_excluded_market_rows = _eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
        side="top",
    )
    bottom_eligible_market_rows, bottom_excluded_market_rows = _eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
        side="bottom",
    )
    all_eligible_market_rows, all_excluded_market_rows = _eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    top_supported_rows, top_unsupported_rows, top_eligible_symbols = _cow_supported_union_market_rows(
        top_eligible_market_rows,
        network_token_caches,
    )
    bottom_supported_rows, bottom_unsupported_rows, bottom_eligible_symbols = _cow_supported_union_market_rows(
        bottom_eligible_market_rows,
        network_token_caches,
    )
    all_supported_rows, all_unsupported_rows, all_eligible_symbols = _cow_supported_union_market_rows(
        all_eligible_market_rows,
        network_token_caches,
    )
    top_rows, _ = _rank_supported_extremes(
        top_supported_rows,
        max(1, int(limit)),
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    _, bottom_rows = _rank_supported_extremes(
        bottom_supported_rows,
        max(1, int(limit)),
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    return {
        "limit": max(1, int(limit)),
        "source_market_row_count": len(market_rows),
        "eligible_symbol_count": len(all_eligible_symbols),
        "supported_symbol_count": len(all_supported_rows),
        "unsupported_symbol_count": len(all_unsupported_rows),
        "market_eligible_symbol_count": len(all_eligible_market_rows),
        "market_excluded_symbol_count": len(all_excluded_market_rows),
        "market_excluded_reason_counts": _market_filter_reason_counts(all_excluded_market_rows),
        "top_filter": {
            "source_market_row_count": len(market_rows),
            "eligible_symbol_count": len(top_eligible_symbols),
            "supported_symbol_count": len(top_supported_rows),
            "unsupported_symbol_count": len(top_unsupported_rows),
            "market_eligible_symbol_count": len(top_eligible_market_rows),
            "market_excluded_symbol_count": len(top_excluded_market_rows),
            "market_excluded_reason_counts": _market_filter_reason_counts(top_excluded_market_rows),
        },
        "bottom_filter": {
            "source_market_row_count": len(market_rows),
            "eligible_symbol_count": len(bottom_eligible_symbols),
            "supported_symbol_count": len(bottom_supported_rows),
            "unsupported_symbol_count": len(bottom_unsupported_rows),
            "market_eligible_symbol_count": len(bottom_eligible_market_rows),
            "market_excluded_symbol_count": len(bottom_excluded_market_rows),
            "market_excluded_reason_counts": _market_filter_reason_counts(bottom_excluded_market_rows),
        },
        "min_side_change_percent": float(min_side_change_percent),
        "min_token_price_usd": float(min_token_price_usd),
        "threshold_detail": threshold_detail or {},
        "top": top_rows,
        "bottom": bottom_rows,
    }


def build_binance_market_state(
    extremes: dict[str, Any] | None,
    *,
    aave_symbols: list[str],
    arbitrage_config: ArbitrageConfig,
    top_limit: int = 5,
    bottom_limit: int = 5,
    pair_side_limit: int = 5,
    cow_display_limit: int | None = None,
    slippage_bps: int = DEFAULT_EXECUTION_SLIPPAGE_BPS,
    cow_network: str | None = None,
    min_spread_percent: float = 0.0,
    min_side_change_percent: float = DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT,
    min_token_price_usd: float = DEFAULT_MIN_TOKEN_PRICE_USD,
    threshold_detail: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if cow_network:
        pair_side_limit = 1
        cow_display_limit = 1
    market_rows = _basket_rows_from_extremes(extremes)
    eligible_market_rows, excluded_market_rows = _eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    raw_top_all, raw_bottom_all = _rank_supported_extremes(
        _price_filtered_market_rows(
            market_rows,
            min_token_price_usd=min_token_price_usd,
        ),
        max(top_limit, bottom_limit, pair_side_limit),
        min_side_change_percent=0.0,
        min_token_price_usd=min_token_price_usd,
    )
    raw_top = raw_top_all[: max(1, int(top_limit))]
    raw_bottom = raw_bottom_all[: max(1, int(bottom_limit))]
    network_display_limit = max(1, int(cow_display_limit if cow_display_limit is not None else top_limit))
    cow_filter = None
    if cow_network:
        network_config = cow_network_config(network=cow_network)
        token_registry = registry if registry is not None else build_token_registry(
            aave_cache_path=DEFAULT_AAVE_CACHE_PATH,
            include_cow_token_list=True,
            cow_network=network_config.network,
        )
        supported_rows, unsupported_rows = _cow_supported_market_rows(
            eligible_market_rows,
            token_registry,
        )
        top_all, bottom_all = _rank_supported_extremes(
            supported_rows,
            max(top_limit, bottom_limit, pair_side_limit),
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
        )
        cow_filter = {
            "network": network_config.network,
            "chain_id": network_config.chain_id,
            "enabled": True,
            "supported_symbol_count": len(supported_rows),
            "unsupported_symbol_count": len(unsupported_rows),
            "market_eligible_symbol_count": len(eligible_market_rows),
            "market_excluded_symbol_count": len(excluded_market_rows),
            "min_side_change_percent": float(min_side_change_percent),
            "min_token_price_usd": float(min_token_price_usd),
            "cow_display_limit": network_display_limit,
            "min_spread_percent": max(0.0, float(min_spread_percent)),
            "threshold_detail": threshold_detail or {},
        }
    else:
        top_all, bottom_all = raw_top_all, raw_bottom_all
        cow_filter = {
            "enabled": False,
            "market_eligible_symbol_count": len(eligible_market_rows),
            "market_excluded_symbol_count": len(excluded_market_rows),
            "min_side_change_percent": float(min_side_change_percent),
            "min_token_price_usd": float(min_token_price_usd),
            "cow_display_limit": network_display_limit,
            "min_spread_percent": max(0.0, float(min_spread_percent)),
            "threshold_detail": threshold_detail or {},
        }
    top = top_all[: network_display_limit]
    bottom = bottom_all[: network_display_limit]
    pair_top = top_all[: max(1, int(pair_side_limit))]
    pair_bottom = bottom_all[: max(1, int(pair_side_limit))]
    basket_rows = top_all + bottom_all
    if isinstance(extremes, dict) and isinstance(extremes.get("basket"), list):
        basket_top, basket_bottom = top_bottom_from_extremes(extremes, side_limit=1000)
        basket_rows = basket_top + basket_bottom
    by_symbol = _rows_by_symbol(basket_rows)
    aave_rows = []
    for symbol in dict.fromkeys(str(item).upper() for item in aave_symbols if item):
        row = by_symbol.get(symbol)
        aave_rows.append(
            {
                "symbol": symbol,
                "change_percent": row.get("change_percent") if row else None,
                "start_price": row.get("start_price") if row else None,
                "current_price": row.get("current_price") if row else None,
                "price_source": row.get("price_source") if row else None,
                "window_ready": bool(row.get("window_ready")) if row else False,
                "tracked": row is not None,
            }
        )
    pairs = []
    min_spread = max(0.0, float(min_spread_percent))
    for x in pair_top:
        for y in pair_bottom:
            if x["symbol"] == y["symbol"]:
                continue
            spread = (
                float(x["change_percent"]) - float(y["change_percent"])
                if x["change_percent"] is not None and y["change_percent"] is not None
                else None
            )
            if spread is None or spread <= min_spread:
                continue
            row = _pair_quote_candidates(x, y, arbitrage_config, slippage_bps=slippage_bps)
            row["grid_rank"] = len(pairs) + 1
            row["rank"] = row["grid_rank"]
            pairs.append(row)
    ranked_pairs = pairs
    return {
        "observed_at": extremes.get("observed_at") if isinstance(extremes, dict) else None,
        "window_seconds": extremes.get("window_seconds") if isinstance(extremes, dict) else None,
        "sample_count": extremes.get("sample_count") if isinstance(extremes, dict) else 0,
        "observation_universe_size": extremes.get("observation_universe_size") if isinstance(extremes, dict) else 0,
        "price_source": extremes.get("price_source") if isinstance(extremes, dict) else None,
        "market_state_source": extremes.get("market_state_source") if isinstance(extremes, dict) else None,
        "fallback_reason": extremes.get("fallback_reason") if isinstance(extremes, dict) else None,
        "top_limit": top_limit,
        "bottom_limit": bottom_limit,
        "raw_top": raw_top,
        "raw_bottom": raw_bottom,
        "pair_count": len(pairs),
        "top": top,
        "bottom": bottom,
        "aave_rows": aave_rows,
        "pairs": ranked_pairs,
        "best": None,
        "notional_usd": arbitrage_config.notional_usd,
        "slippage_bps": max(0, min(int(slippage_bps), 5000)),
        "cow_filter": cow_filter,
        "quote_verified": False,
        "estimation_available": False,
        "candidate_basis": "binance_token_names_only",
    }


def build_cow_quote_verification(
    market_state: dict[str, Any],
    *,
    amount: str | int | float | Decimal = "1000",
    quote_limit: int = 5,
    owner: str | None = None,
    cow_network: str | None = DEFAULT_COW_TEST_NETWORK,
    price_quality: str = "fast",
    valid_for: int = 60,
    quote_timeout_seconds: int | float = 8,
    aave_cache_path: Path = DEFAULT_AAVE_CACHE_PATH,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    network_config = cow_network_config(network=cow_network)
    account_config = cow_account_config(network_config.network)
    requested_owner = str(owner or "").strip()
    owner = requested_owner or account_config.owner
    owner_source = "request.owner" if requested_owner else account_config.owner_source
    ranked_pairs = list(market_state.get("pairs") or [])
    selected_pairs = ranked_pairs[:1]
    route_specs = [spec for pair in selected_pairs for spec in _cow_route_specs(pair, amount)]
    token_registry = registry if registry is not None else build_token_registry(
        aave_cache_path=aave_cache_path,
        include_cow_token_list=True,
        cow_network=network_config.network,
    )
    precheck_routes = [
        _cow_route_support(spec, token_registry, cow_network=network_config.network)
        for spec in route_specs
    ]
    precheck_by_route = {
        (item.get("pair"), item.get("pair_rank"), item.get("priority_reason")): item
        for item in precheck_routes
    }
    results = []
    for spec in route_specs:
        support = precheck_by_route.get((spec.get("pair"), spec.get("pair_rank"), spec.get("priority_reason")), {})
        if support.get("supported"):
            result = evaluate_cow_route(
                spec,
                registry=token_registry,
                default_amount=amount,
                owner=owner,
                cow_network=network_config.network,
                price_quality=price_quality,
                valid_for=valid_for,
                quote_timeout_seconds=quote_timeout_seconds,
            )
        else:
            path = spec.get("path") or []
            result = {
                "name": spec.get("name"),
                "path": path,
                "input_amount": str(amount),
                "input_symbol": path[0] if path else None,
                "final_symbol": path[-1] if path else None,
                "viable": False,
                "error": support.get("error") or "CoW token support precheck failed",
                "hops": [],
            }
        result["pair"] = spec.get("pair")
        result["pair_rank"] = spec.get("pair_rank")
        result["priority_reason"] = spec.get("priority_reason")
        result["edge_hint_percent"] = spec.get("edge_hint_percent")
        result["cow_support"] = support
        final_amount = _decimal_or_none(result.get("final_amount"))
        input_amount = _decimal_or_none(result.get("input_amount"))
        result["final_delta_amount"] = (
            str(final_amount - input_amount)
            if final_amount is not None and input_amount is not None
            else None
        )
        result["binance_execution_plan"] = _apply_cow_quote_analysis(spec.get("binance_execution_plan"), result)
        _attach_cow_flashloan_sdk_plan(result, result.get("binance_execution_plan"), token_registry)
        result["execution_precheck"] = _cow_execution_precheck(result)
        result["costs"] = _cow_cost_summary(result, final_delta_amount=result["final_delta_amount"])
        result["quote_verified"] = True
        results.append(result)
    ranking = rank_cow_routes(results)
    opportunities = [
        item
        for item in ranking
        if (item.get("execution_precheck") or {}).get("checks_passed")
    ]
    return {
        "observed_at": market_state.get("observed_at"),
        "amount": str(amount),
        "owner": owner,
        "owner_source": owner_source,
        "cow_network": network_config.network,
        "cow_chain_id": network_config.chain_id,
        "cow_testnet": network_config.testnet,
        "price_quality": price_quality,
        "valid_for": valid_for,
        "selected_pair_count": len(selected_pairs),
        "route_count": len(route_specs),
        "supported_route_count": sum(1 for item in precheck_routes if item["supported"]),
        "unsupported_route_count": sum(1 for item in precheck_routes if not item["supported"]),
        "viable_count": sum(1 for item in ranking if item.get("viable")),
        "opportunity_count": len(opportunities),
        "precheck": {
            "routes": precheck_routes,
        },
        "best": opportunities[0] if opportunities else (ranking[0] if ranking else None),
        "best_opportunity": opportunities[0] if opportunities else None,
        "opportunities": opportunities,
        "ranking": ranking,
    }
