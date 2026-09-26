from __future__ import annotations

import json

from core.kv_rows import collection_key, latest_rows, upsert_row, upsert_rows
from core.local_kv import ensure_store


def _row_key(*parts) -> str:
    return collection_key(*parts)


def append_binance_price_history(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return
    items = [
        (
            _row_key(row["event_time"], row["symbol"], row["source"], index),
            {
                "observed_at": row["observed_at"],
                "symbol": row["symbol"],
                "price": float(row["price"]),
                "event_time": row["event_time"],
                "source": row["source"],
            },
        )
        for index, row in enumerate(rows)
    ]
    upsert_rows(database_url, "binance_price_history", items)


def append_binance_candidate_price_history(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return
    items = [
        (
            _row_key(row["event_time"], row["symbol"], row["source"], index),
            {
                "observed_at": row["observed_at"],
                "symbol": row["symbol"],
                "price_usdc": float(row["price_usdc"]),
                "source_price": float(row["source_price"]),
                "usdc_usdt_price": float(row["usdc_usdt_price"]),
                "change_percent": float(row["change_percent"]),
                "rank_side": row["rank_side"],
                "rank_position": int(row["rank_position"]),
                "event_time": row["event_time"],
                "source": row["source"],
            },
        )
        for index, row in enumerate(rows)
    ]
    upsert_rows(database_url, "binance_candidate_price_history", items)


def append_binance_pair_price_history(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return
    items = [
        (
            _row_key(row["event_time"], row["x_symbol"], row["y_symbol"], row["source"], index),
            {
                "observed_at": row["observed_at"],
                "x_symbol": row["x_symbol"],
                "y_symbol": row["y_symbol"],
                "x_usdc_price": float(row["x_usdc_price"]),
                "y_usdc_price": float(row["y_usdc_price"]),
                "x_y_price": float(row["x_y_price"]),
                "window_seconds": float(row["window_seconds"]),
                "event_time": row["event_time"],
                "source": row["source"],
            },
        )
        for index, row in enumerate(rows)
    ]
    upsert_rows(database_url, "binance_pair_price_history", items)


def append_observations(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return
    items = [
        (
            _row_key(row["binance_event_time"], row["aave_block"], row["symbol"], index),
            {
                "observed_at": row["observed_at"],
                "symbol": row["symbol"],
                "asset": row["asset"],
                "binance_price": float(row["binance_price"]),
                "binance_event_time": row["binance_event_time"],
                "aave_price": float(row["aave_price"]),
                "aave_block": int(row["aave_block"]),
                "diff_percent": float(row["diff_percent"]),
                "binance_age_seconds": float(row["binance_age_seconds"]),
                "aave_age_seconds": float(row["aave_age_seconds"]),
            },
        )
        for index, row in enumerate(rows)
    ]
    upsert_rows(database_url, "observations", items)


def append_binance_extremes(database_url: str, extremes: dict) -> None:
    observed_at = extremes["observed_at"]
    upsert_row(
        database_url,
        "binance_window_extremes",
        _row_key(observed_at),
        {
            "observed_at": observed_at,
            "window_seconds": float(extremes["window_seconds"]),
            "sample_count": int(extremes["sample_count"]),
            "top": extremes.get("top") or [],
            "bottom": extremes.get("bottom") or [],
        },
    )


def append_arbitrage_simulation(database_url: str, simulation: dict) -> None:
    upsert_row(
        database_url,
        "arbitrage_simulations",
        _row_key(simulation["observed_at"], simulation.get("a_symbol", ""), simulation.get("b_symbol", "")),
        dict(simulation),
    )


def latest_binance_extremes_row(database_url: str) -> dict | None:
    rows = latest_rows(database_url, "binance_window_extremes", limit=1)
    return rows[0] if rows else None


def latest_arbitrage_simulation_row(database_url: str) -> dict | None:
    rows = latest_rows(database_url, "arbitrage_simulations", limit=1)
    return rows[0] if rows else None


def ensure_market_schema(database_url: str) -> None:
    ensure_store(database_url)


__all__ = [
    "append_arbitrage_simulation",
    "append_binance_candidate_price_history",
    "append_binance_extremes",
    "append_binance_pair_price_history",
    "append_binance_price_history",
    "append_observations",
    "ensure_market_schema",
    "latest_arbitrage_simulation_row",
    "latest_binance_extremes_row",
]
