from __future__ import annotations

from typing import Any


QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD")


def base_token_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    for suffix in QUOTE_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _candidate_row(item: dict[str, Any], side: str, rank: int) -> dict[str, Any] | None:
    symbol = str(item.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    return {
        "rank": rank,
        "side": side,
        "symbol": symbol,
        "base_symbol": base_token_symbol(symbol),
        "change_percent": _float_or_none(item.get("change_percent")),
        "start_price": _float_or_none(item.get("start_price")),
        "end_price": _float_or_none(item.get("end_price") if item.get("end_price") is not None else item.get("current_price")),
        "current_price": _float_or_none(item.get("current_price") if item.get("current_price") is not None else item.get("end_price")),
        "price_source": item.get("price_source"),
        "window_ready": bool(item.get("window_ready", True)),
    }


def _rows_from_explicit_extremes(extremes: dict[str, Any], side: str, side_limit: int) -> list[dict[str, Any]]:
    items = extremes.get(side) or []
    if not isinstance(items, list):
        return []
    rows = []
    for index, item in enumerate(items[:side_limit], start=1):
        if isinstance(item, dict):
            row = _candidate_row(item, side, index)
            if row:
                rows.append(row)
    return rows


def _rows_from_compact_extremes(extremes: dict[str, Any], side: str, side_limit: int) -> list[dict[str, Any]]:
    keys = ("a", "top_2") if side == "top" else ("b", "bottom_2")
    rows = []
    for item in (extremes.get(key) for key in keys):
        if isinstance(item, dict) and item.get("symbol"):
            row = _candidate_row(item, side, len(rows) + 1)
            if row:
                rows.append(row)
    return rows[:side_limit]


def top_bottom_from_extremes(extremes: dict[str, Any] | None, side_limit: int = 5) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(extremes, dict):
        return [], []
    limit = max(1, int(side_limit))
    top = _rows_from_explicit_extremes(extremes, "top", limit)
    bottom = _rows_from_explicit_extremes(extremes, "bottom", limit)
    basket = extremes.get("basket") or []
    if isinstance(basket, list) and (len(top) < limit or len(bottom) < limit):
        basket_rows = [
            row
            for item in basket
            if isinstance(item, dict)
            for row in [_candidate_row(item, "basket", 0)]
            if row and row["change_percent"] is not None
        ]
        if len(top) < limit:
            gainers = sorted(
                (row for row in basket_rows if float(row["change_percent"] or 0.0) > 0),
                key=lambda row: float(row["change_percent"] or 0.0),
                reverse=True,
            )
            top = _unique_by_symbol([*top, *gainers], "top", limit)
        if len(bottom) < limit:
            losers = sorted(
                (row for row in basket_rows if float(row["change_percent"] or 0.0) < 0),
                key=lambda row: float(row["change_percent"] or 0.0),
            )
            bottom = _unique_by_symbol([*bottom, *losers], "bottom", limit)
    if not top:
        top = _rows_from_compact_extremes(extremes, "top", limit)
    if not bottom:
        bottom = _rows_from_compact_extremes(extremes, "bottom", limit)
    return top[:limit], bottom[:limit]


def _unique_by_symbol(rows: list[dict[str, Any]], side: str, limit: int) -> list[dict[str, Any]]:
    selected = []
    seen: set[str] = set()
    for row in rows:
        symbol = row.get("symbol")
        if not symbol or symbol in seen:
            continue
        copied = dict(row)
        copied["side"] = side
        copied["rank"] = len(selected) + 1
        selected.append(copied)
        seen.add(str(symbol))
        if len(selected) >= limit:
            break
    return selected


def build_velocity_candidate_pairs(extremes: dict[str, Any] | None, side_limit: int = 5) -> dict[str, Any]:
    top, bottom = top_bottom_from_extremes(extremes, side_limit=side_limit)
    pairs = []
    for x in top:
        for y in bottom:
            if x["symbol"] == y["symbol"]:
                continue
            pairs.append(
                {
                    "rank": len(pairs) + 1,
                    "pair": f"{x['symbol']} / {y['symbol']}",
                    "x_symbol": x["symbol"],
                    "y_symbol": y["symbol"],
                    "x_change_percent": x["change_percent"],
                    "y_change_percent": y["change_percent"],
                    "cow_path": ["USDC", x["base_symbol"], y["base_symbol"], "USDC"],
                    "cow_reverse_path": ["USDC", y["base_symbol"], x["base_symbol"], "USDC"],
                    "needs_quote": True,
                }
            )
    return {
        "observed_at": extremes.get("observed_at") if isinstance(extremes, dict) else None,
        "window_seconds": extremes.get("window_seconds") if isinstance(extremes, dict) else None,
        "sample_count": extremes.get("sample_count") if isinstance(extremes, dict) else 0,
        "observation_universe_size": extremes.get("observation_universe_size") if isinstance(extremes, dict) else 0,
        "price_source": extremes.get("price_source") if isinstance(extremes, dict) else None,
        "side_limit": max(1, int(side_limit)),
        "top": top,
        "bottom": bottom,
        "candidate_count": len(pairs),
        "pairs": pairs,
    }
