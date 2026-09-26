import json
from datetime import datetime, timezone
from typing import Any, Optional

from core.kv_rows import latest_rows, load_rows


def compact_extreme(items: list[dict], index: int) -> dict:
    if len(items) <= index:
        return {"symbol": None, "change_percent": None}
    change_percent = items[index].get("change_percent")
    return {
        "symbol": items[index].get("symbol"),
        "change_percent": float(change_percent) if change_percent is not None else None,
    }


def compact_basket_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            symbol = str(item["symbol"]).upper()
            raw_change_percent = item.get("change_percent")
            raw_start_price = item.get("start_price")
            raw_current_price = item.get("current_price")
            if raw_current_price is None:
                raw_current_price = item.get("end_price")
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            {
                "symbol": symbol,
                "change_percent": float(raw_change_percent) if raw_change_percent is not None else None,
                "start_price": float(raw_start_price) if raw_start_price is not None else None,
                "current_price": float(raw_current_price) if raw_current_price is not None else None,
                "price_source": item.get("price_source"),
                "end_ms": item.get("end_ms"),
                "window_ready": bool(item.get("window_ready")),
            }
        )
    return rows


def compact_extremes_payload(data: dict) -> Optional[dict]:
    top, bottom = data.get("top") or [], data.get("bottom") or []
    basket = compact_basket_rows(data.get("basket") or [])
    if not top and not bottom and not basket:
        return None
    return {
        "observed_at": data.get("observed_at"),
        "window_seconds": float(data.get("window_seconds", 0)),
        "sample_count": int(data.get("sample_count", 0)),
        "gainer_count": int(data.get("gainer_count", 0)),
        "loser_count": int(data.get("loser_count", 0)),
        "observation_universe_size": int(data.get("observation_universe_size", 0)),
        "market_divergence_index": float(data.get("market_divergence_index", 0.0)),
        "price_source": data.get("price_source"),
        "a": compact_extreme(top, 0),
        "top_2": compact_extreme(top, 1),
        "b": compact_extreme(bottom, 0),
        "bottom_2": compact_extreme(bottom, 1),
        "basket": basket,
    }


def latest_extremes_from_db(database_url: str) -> Optional[dict]:
    rows = latest_rows(database_url, "binance_window_extremes", limit=1)
    if not rows:
        return None
    return compact_extremes_payload(rows[0])


def latest_arbitrage_from_db(database_url: str) -> Optional[dict]:
    rows = latest_rows(database_url, "arbitrage_simulations", limit=1)
    if not rows:
        return None
    data = dict(rows[0])
    decode_json_fields(data)
    return data


def decode_json_fields(data: dict) -> None:
    for source, target, default in [
        ("execution_plan_json", "execution_plan", None),
        ("blocked_reasons_json", "blocked_reasons", []),
        ("route_symbols_json", "route_symbols", []),
    ]:
        value = data.pop(source, None)
        if not value:
            if target == "execution_plan":
                data[target] = None
            continue
        try:
            data[target] = json.loads(value)
        except json.JSONDecodeError:
            data[target] = default


def string_like_arbitrage_keys() -> set[str]:
    return {
        "observed_at", "a_symbol", "b_symbol", "profitable", "sample_count",
        "strategy", "best_strategy", "borrow_symbol", "swap_symbol",
        "execution_plan", "signal", "blocked_reasons", "route_symbols",
    }


def iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def parse_observed_age_seconds(observed_at: str) -> float:
    observed_at = str(observed_at).replace("Z", "+00:00")
    observed = datetime.fromisoformat(observed_at)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()


def load_json_rows(database_url: str, collection: str) -> list[dict[str, Any]]:
    return load_rows(database_url, collection)


__all__ = [
    "compact_extreme",
    "compact_extremes_payload",
    "decode_json_fields",
    "iso",
    "latest_arbitrage_from_db",
    "latest_extremes_from_db",
    "parse_observed_age_seconds",
    "string_like_arbitrage_keys",
]
