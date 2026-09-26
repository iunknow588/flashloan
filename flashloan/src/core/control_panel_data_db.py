from __future__ import annotations

from typing import Optional

from core.kv_rows import count, get_row, latest_rows, load_rows
from core.local_kv import meta_get
from core.db.storage_common import EXPECTED_SCHEMA_MIGRATION_IDS
from core.control_panel_data_compact import iso


def _scan_rows(database_url: str, collection: str) -> list[dict]:
    return load_rows(database_url, collection)


def observation_count(database_url: str) -> Optional[int]:
    if not database_url:
        return None
    return count(database_url, "observations")


def database_table_counts(database_url: str) -> Optional[dict]:
    if not database_url:
        return None
    collections = (
        "observations",
        "binance_price_history",
        "binance_candidate_price_history",
        "binance_pair_price_history",
        "binance_window_extremes",
        "arbitrage_simulations",
        "liquidation_accounts",
        "liquidation_discovery_scans",
        "liquidation_account_health_scans",
        "liquidation_borrow_health_pool",
        "liquidation_high_frequency_pool",
        "liquidation_core_opportunity_pool",
        "liquidation_borrow_health_scan_batches",
        "liquidation_scan_config_library",
        "liquidation_failure_samples",
        "schema_migrations",
    )
    counts = {name: count(database_url, name) for name in collections}
    applied = meta_get(database_url, "schema_migrations") or {}
    expected_count = len(EXPECTED_SCHEMA_MIGRATION_IDS)
    applied_count = len(applied)
    counts["schema"] = {
        "latest_migration_at": "",
        "expected_migration_count": expected_count,
        "expected_migration_applied_count": applied_count,
        "up_to_date": applied_count >= expected_count,
    }
    counts["total"] = sum(int(value) for key, value in counts.items() if key != "schema")
    return counts


def available_chart_symbols(database_url: str, limit: int = 500) -> list[str]:
    if not database_url:
        return []
    by_symbol: dict[str, str] = {}
    for row in _scan_rows(database_url, "observations"):
        symbol = str(row.get("symbol") or "")
        observed_at = str(row.get("observed_at") or "")
        if symbol and observed_at >= by_symbol.get(symbol, ""):
            by_symbol[symbol] = observed_at
    for row in _scan_rows(database_url, "binance_price_history"):
        symbol = str(row.get("symbol") or "")
        event_time = str(row.get("event_time") or "")
        if symbol and event_time >= by_symbol.get(symbol, ""):
            by_symbol[symbol] = event_time
    ordered = sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in ordered[:max(0, int(limit))]]


def available_candidate_symbols(database_url: str, limit: int = 500) -> list[str]:
    if not database_url:
        return []
    by_symbol: dict[str, str] = {}
    for row in _scan_rows(database_url, "binance_candidate_price_history"):
        symbol = str(row.get("symbol") or "")
        event_time = str(row.get("event_time") or "")
        if symbol and event_time >= by_symbol.get(symbol, ""):
            by_symbol[symbol] = event_time
    for row in _scan_rows(database_url, "binance_pair_price_history"):
        for symbol_key in ("x_symbol", "y_symbol"):
            symbol = str(row.get(symbol_key) or "")
            event_time = str(row.get("event_time") or "")
            if symbol and event_time >= by_symbol.get(symbol, ""):
                by_symbol[symbol] = event_time
    ordered = sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in ordered[:max(0, int(limit))]]


def recent_velocity_timepoints(database_url: str, limit: int = 200) -> list[dict]:
    if not database_url:
        return []
    rows = latest_rows(database_url, "binance_window_extremes", limit=limit)
    result = []
    for index, row in enumerate(rows):
        top = row.get("top") or []
        bottom = row.get("bottom") or []
        result.append(
            {
                "id": index + 1,
                "observed_at": iso(row.get("observed_at")),
                "window_seconds": float(row.get("window_seconds") or 0),
                "sample_count": int(row.get("sample_count") or 0),
                "top_symbols": [str(item.get("symbol")) for item in top[:5] if item.get("symbol")],
                "bottom_symbols": [str(item.get("symbol")) for item in bottom[:5] if item.get("symbol")],
            }
        )
    return result


def velocity_timepoint_snapshot(database_url: str, snapshot_id: int | None = None) -> Optional[dict]:
    if not database_url:
        return None
    rows = latest_rows(database_url, "binance_window_extremes", limit=snapshot_id or 1)
    if snapshot_id is None:
        rows = latest_rows(database_url, "binance_window_extremes", limit=1)
    elif snapshot_id and rows:
        rows = rows[-1:]
    if not rows:
        return None
    row = rows[0]
    return {
        "id": int(snapshot_id or 1),
        "observed_at": iso(row.get("observed_at")),
        "window_seconds": float(row.get("window_seconds") or 0),
        "sample_count": int(row.get("sample_count") or 0),
        "top": row.get("top") or [],
        "bottom": row.get("bottom") or [],
    }


def recent_observations(database_url: str, symbol: str, limit: int) -> list[dict]:
    if not database_url:
        return []
    symbol = str(symbol)
    rows = [row for row in _scan_rows(database_url, "observations") if str(row.get("symbol")) == symbol]
    rows.sort(key=lambda row: str(row.get("observed_at") or ""))
    rows = rows[-max(0, int(limit)):]
    return [
        {
            "observed_at": iso(row.get("observed_at")),
            "symbol": str(row.get("symbol")),
            "asset": str(row.get("asset")),
            "binance_price": float(row.get("binance_price")),
            "aave_price": float(row.get("aave_price")),
            "diff_percent": float(row.get("diff_percent")),
        }
        for row in rows
    ]


def latest_observation_prices(database_url: str, symbols: list[str]) -> dict[str, dict]:
    if not symbols or not database_url:
        return {}
    symbols = {str(symbol) for symbol in symbols}
    result: dict[str, dict] = {}
    for row in _scan_rows(database_url, "observations"):
        symbol = str(row.get("symbol") or "")
        if symbol not in symbols:
            continue
        if symbol not in result or str(row.get("observed_at") or "") > str(result[symbol].get("observed_at") or ""):
            result[symbol] = {
                "observed_at": iso(row.get("observed_at")),
                "symbol": symbol,
                "asset": str(row.get("asset")),
                "binance_price": float(row.get("binance_price")),
                "aave_price": float(row.get("aave_price")),
                "diff_percent": float(row.get("diff_percent")),
            }
    return result


def latest_observation_prices_at_or_before(database_url: str, symbols: list[str], cutoff_at) -> dict[str, dict]:
    if not symbols or not database_url:
        return {}
    cutoff = str(cutoff_at)
    symbols = {str(symbol) for symbol in symbols}
    result: dict[str, dict] = {}
    for row in _scan_rows(database_url, "observations"):
        symbol = str(row.get("symbol") or "")
        observed_at = str(row.get("observed_at") or "")
        if symbol not in symbols or observed_at > cutoff:
            continue
        if symbol not in result or observed_at > str(result[symbol].get("observed_at") or ""):
            result[symbol] = {
                "observed_at": iso(row.get("observed_at")),
                "symbol": symbol,
                "asset": str(row.get("asset")),
                "binance_price": float(row.get("binance_price")),
                "aave_price": float(row.get("aave_price")),
                "diff_percent": float(row.get("diff_percent")),
            }
    return result


def recent_aave_pair_prices(database_url: str, x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    if not database_url:
        return []
    x_rows = [row for row in _scan_rows(database_url, "observations") if str(row.get("symbol")) == str(x_symbol)]
    y_rows = [row for row in _scan_rows(database_url, "observations") if str(row.get("symbol")) == str(y_symbol)]
    x_rows.sort(key=lambda row: str(row.get("observed_at") or ""))
    y_rows.sort(key=lambda row: str(row.get("observed_at") or ""))
    x_rows = x_rows[-max(0, int(limit)):]
    y_rows = y_rows[-max(0, int(limit)):]
    result = []
    for x_row, y_row in zip(x_rows, y_rows):
        x_value = float(x_row.get("aave_price"))
        y_value = float(y_row.get("aave_price"))
        result.append(
            {
                "observed_at": iso(x_row.get("observed_at")),
                "x_symbol": x_symbol,
                "y_symbol": y_symbol,
                "x_usdc_price": x_value,
                "y_usdc_price": y_value,
                "x_y_price": x_value / y_value if y_value > 0 else None,
            }
        )
    return result


def recent_binance_pair_prices(database_url: str, x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    if not database_url:
        return []
    x_symbol, y_symbol = str(x_symbol), str(y_symbol)
    rows = [
        row
        for row in _scan_rows(database_url, "binance_pair_price_history")
        if (str(row.get("x_symbol")) == x_symbol and str(row.get("y_symbol")) == y_symbol)
        or (str(row.get("x_symbol")) == y_symbol and str(row.get("y_symbol")) == x_symbol)
    ]
    rows.sort(key=lambda row: str(row.get("event_time") or ""))
    rows = rows[-max(0, int(limit)):]
    result = []
    for row in rows:
        stored_x, stored_y = str(row.get("x_symbol")), str(row.get("y_symbol"))
        stored_x_price, stored_y_price = float(row.get("x_usdc_price")), float(row.get("y_usdc_price"))
        if stored_x == x_symbol and stored_y == y_symbol:
            x_price, y_price = stored_x_price, stored_y_price
            pair_price = float(row.get("x_y_price")) if row.get("x_y_price") is not None else None
        else:
            x_price, y_price = stored_y_price, stored_x_price
            pair_price = x_price / y_price if y_price > 0 else None
        result.append(
            {
                "observed_at": iso(row.get("event_time") or row.get("observed_at")),
                "x_symbol": x_symbol,
                "y_symbol": y_symbol,
                "x_usdc_price": x_price,
                "y_usdc_price": y_price,
                "x_y_price": pair_price,
                "source": str(row.get("source")),
            }
        )
    return result


def latest_candidate_price_rows(database_url: str, symbols: list[str]) -> dict[str, dict]:
    if not database_url:
        return {}
    symbols = {str(symbol) for symbol in symbols}
    result: dict[str, dict] = {}
    for row in _scan_rows(database_url, "binance_candidate_price_history"):
        symbol = str(row.get("symbol") or "")
        if symbol not in symbols:
            continue
        if symbol not in result or str(row.get("event_time") or "") > str(result[symbol].get("event_time") or ""):
            result[symbol] = dict(row)
    for row in _scan_rows(database_url, "binance_pair_price_history"):
        for symbol_key in ("x_symbol", "y_symbol"):
            symbol = str(row.get(symbol_key) or "")
            if symbol not in symbols:
                continue
            if symbol not in result or str(row.get("event_time") or "") > str(result[symbol].get("event_time") or ""):
                result[symbol] = dict(row)
    return result


def recent_binance_price_history(database_url: str, symbol: str, limit: int) -> list[dict]:
    if not database_url:
        return []
    symbol = str(symbol)
    rows = [row for row in _scan_rows(database_url, "binance_price_history") if str(row.get("symbol")) == symbol]
    rows.sort(key=lambda row: str(row.get("event_time") or ""))
    rows = rows[-max(0, int(limit)):]
    return [
        {
            "observed_at": iso(row.get("observed_at")),
            "symbol": str(row.get("symbol")),
            "price": float(row.get("price")),
            "event_time": str(row.get("event_time")),
            "source": str(row.get("source")),
        }
        for row in rows
    ]


__all__ = [
    "available_candidate_symbols",
    "available_chart_symbols",
    "database_table_counts",
    "latest_candidate_price_rows",
    "latest_observation_prices",
    "latest_observation_prices_at_or_before",
    "observation_count",
    "recent_aave_pair_prices",
    "recent_binance_pair_prices",
    "recent_binance_price_history",
    "recent_observations",
    "recent_velocity_timepoints",
    "velocity_timepoint_snapshot",
]
