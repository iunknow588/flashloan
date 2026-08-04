from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web.control_panel_data import read_json


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _base_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    for quote in ("USDT", "USDC", "USD"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)]
    return value


def binance_symbols_for_liquidation_assets(asset_ids: list[str], reserve_assets: list[dict[str, Any]]) -> list[str]:
    lookup: dict[str, str] = {}
    for asset in reserve_assets:
        binance_symbol = str(asset.get("binance_symbol") or "").strip().upper()
        if not binance_symbol:
            continue
        for key in ("token_symbol", "symbol", "token_address", "asset_address", "binance_symbol"):
            value = str(asset.get(key) or "").strip()
            if value:
                lookup[value.upper()] = binance_symbol
                lookup[value.lower()] = binance_symbol

    aliases = {
        "AVAX": "AVAXUSDT",
        "WAVAX": "AVAXUSDT",
        "ETH": "ETHUSDT",
        "WETH": "ETHUSDT",
        "WETH.E": "ETHUSDT",
        "BTC": "BTCUSDT",
        "WBTC": "BTCUSDT",
        "BTC.B": "BTCUSDT",
        "USDC": "USDCUSDT",
        "USDC.E": "USDCUSDT",
    }
    symbols: list[str] = []

    def add(symbol: str) -> None:
        item = str(symbol or "").strip().upper()
        if item and item not in symbols:
            symbols.append(item)

    for asset_id in asset_ids:
        raw = str(asset_id or "").strip()
        if not raw:
            continue
        upper = raw.upper()
        if upper.endswith("USDT"):
            add(upper)
            continue
        mapped = lookup.get(upper) or lookup.get(raw.lower()) or aliases.get(upper)
        if mapped:
            add(mapped)
    return symbols


def liquidation_asset_ids_from_pool_rows(rows: list[dict[str, Any]]) -> list[str]:
    assets: list[str] = []

    def add(value: Any) -> None:
        item = str(value or "").strip()
        if item and item not in assets:
            assets.append(item)

    for row in rows:
        if not isinstance(row, dict):
            continue
        add(row.get("best_debt_asset"))
        add(row.get("best_collateral_asset"))
        report = row.get("report") if isinstance(row.get("report"), dict) else row
        for candidate in list(report.get("liquidation_candidates") or []):
            if not isinstance(candidate, dict):
                continue
            add(candidate.get("debt_asset") or candidate.get("debt_symbol") or candidate.get("debt_token_symbol"))
            add(candidate.get("collateral_asset") or candidate.get("collateral_symbol") or candidate.get("collateral_token_symbol"))
        for position in list(report.get("positions") or []):
            if not isinstance(position, dict):
                continue
            debt = 0.0
            collateral = 0.0
            try:
                debt = float(position.get("debt_value_base") or position.get("total_debt_amount") or 0.0)
                collateral = float(position.get("collateral_value_base") or position.get("collateral_amount") or 0.0)
            except (TypeError, ValueError):
                pass
            if debt > 0 or collateral > 0:
                add(position.get("symbol") or position.get("token_symbol"))
                add(position.get("token_address"))
    return assets


def asset_variants_for_market_symbols(symbols: list[str], *, reserve_cache_path: Path | None = None) -> list[str]:
    variants: list[str] = []

    def add(value: Any) -> None:
        item = str(value or "").strip()
        if item and item not in variants:
            variants.append(item)

    reserve_assets = []
    if reserve_cache_path is not None:
        cache = read_json(reserve_cache_path)
        reserve_assets = list((cache or {}).get("assets") or [])

    lookup: dict[str, list[str]] = {}
    for asset in reserve_assets:
        binance_symbol = str(asset.get("binance_symbol") or "").strip().upper()
        if not binance_symbol:
            continue
        items = lookup.setdefault(binance_symbol, [])
        for key in ("token_symbol", "symbol", "token_address", "asset_address"):
            value = str(asset.get(key) or "").strip()
            if value and value not in items:
                items.append(value)

    wrapped_aliases = {
        "AVAX": ["WAVAX"],
        "ETH": ["WETH", "WETH.e"],
        "BTC": ["WBTC", "BTC.b"],
    }
    for symbol in symbols:
        market_symbol = str(symbol or "").strip().upper()
        base = _base_symbol(market_symbol)
        add(market_symbol)
        add(base)
        for value in lookup.get(market_symbol, []):
            add(value)
        for value in wrapped_aliases.get(base, []):
            add(value)
    return variants


def price_snapshot_from_extremes(
    extremes: dict[str, Any] | None,
    *,
    max_age_seconds: float = 120.0,
    now: datetime | None = None,
) -> dict[str, float]:
    if not isinstance(extremes, dict):
        return {}
    observed_at = _parse_iso(extremes.get("observed_at"))
    current = now or datetime.now(timezone.utc)
    if observed_at and (current - observed_at).total_seconds() > float(max_age_seconds):
        return {}

    snapshot: dict[str, float] = {}
    for row in list(extremes.get("basket") or []) + list(extremes.get("top") or []) + list(extremes.get("bottom") or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        try:
            price = float(row.get("current_price") or row.get("end_price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        if symbol and price > 0:
            snapshot[symbol] = price
    return snapshot
