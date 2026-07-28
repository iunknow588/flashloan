import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from db.storage import require_psycopg


def read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def latest_binance_extremes(database_url: str, latest_path: Path) -> Optional[dict]:
    data = read_json(latest_path)
    if data:
        compact = compact_extremes_payload(data)
        if compact:
            return compact
    return latest_extremes_from_db(database_url)


def latest_binance_extremes_file(latest_path: Path) -> Optional[dict]:
    data = read_json(latest_path)
    return compact_extremes_payload(data) if data else None


def latest_arbitrage_simulation_file(latest_path: Path) -> Optional[dict]:
    return read_json(latest_path)


def latest_arbitrage_simulation(database_url: str, latest_path: Path) -> Optional[dict]:
    return read_json(latest_path) or latest_arbitrage_from_db(database_url)


def latest_executable_signal(latest_path: Path) -> Optional[dict]:
    data = read_json(latest_path)
    if not data:
        return None
    signal = data.get("signal")
    return {
        "raw_candidate_count": int(data.get("raw_candidate_count") or 0),
        "executable_candidate_count": int(data.get("executable_candidate_count") or 0),
        "executable_symbols": data.get("executable_symbols") or [],
        "signal": signal if isinstance(signal, dict) else None,
    }


def aave_reserve_cache(cache_path: Path) -> Optional[dict]:
    data = read_json(cache_path)
    if not data:
        return None
    assets = data.get("assets") or []
    return {
        "refreshed_at": data.get("refreshed_at"),
        "pool_address": data.get("pool_address"),
        "asset_count": len(assets),
        "symbols": [item.get("binance_symbol") for item in assets if item.get("binance_symbol")],
    }


def observation_count(database_url: str) -> Optional[int]:
    try:
        row = fetch_one(database_url, "SELECT COUNT(*) FROM observations")
        return int(row[0]) if row else 0
    except Exception:
        return None


def database_table_counts(database_url: str) -> Optional[dict]:
    query = """
        SELECT
            (SELECT COUNT(*) FROM observations) AS observations,
            (SELECT COUNT(*) FROM binance_price_history) AS binance_price_history,
            (SELECT COUNT(*) FROM binance_window_extremes) AS binance_window_extremes,
            (SELECT COUNT(*) FROM arbitrage_simulations) AS arbitrage_simulations
    """
    try:
        row = fetch_one(database_url, query)
        if not row:
            return None
        counts = {
            "observations": int(row[0] or 0),
            "binance_price_history": int(row[1] or 0),
            "binance_window_extremes": int(row[2] or 0),
            "arbitrage_simulations": int(row[3] or 0),
        }
        counts["total"] = sum(counts.values())
        return counts
    except Exception:
        return None


def available_chart_symbols(database_url: str, limit: int = 500) -> list[str]:
    query = """
        SELECT symbol
        FROM (
            SELECT symbol, MAX(observed_at) AS latest_at
            FROM observations
            GROUP BY symbol
            UNION ALL
            SELECT symbol, MAX(event_time) AS latest_at
            FROM binance_price_history
            GROUP BY symbol
        ) source
        GROUP BY symbol
        ORDER BY MAX(latest_at) DESC, symbol
        LIMIT %s
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
    return [str(row[0]) for row in rows]


def recent_observations(database_url: str, symbol: str, limit: int) -> list[dict]:
    query = """
        SELECT observed_at, symbol, asset, binance_price, aave_price, diff_percent
        FROM observations WHERE symbol = %s ORDER BY observed_at DESC LIMIT %s
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (symbol, limit))
            rows = cursor.fetchall()
    rows.reverse()
    return [
        {
            "observed_at": iso(row[0]),
            "symbol": str(row[1]),
            "asset": str(row[2]),
            "binance_price": float(row[3]),
            "aave_price": float(row[4]),
            "diff_percent": float(row[5]),
        }
        for row in rows
    ]


def recent_aave_pair_prices(database_url: str, x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    query = """
        WITH x_rows AS (
            SELECT observed_at, aave_price,
                   row_number() OVER (ORDER BY observed_at DESC) AS rn
            FROM observations
            WHERE symbol = %s
            ORDER BY observed_at DESC
            LIMIT %s
        ),
        y_rows AS (
            SELECT observed_at, aave_price,
                   row_number() OVER (ORDER BY observed_at DESC) AS rn
            FROM observations
            WHERE symbol = %s
            ORDER BY observed_at DESC
            LIMIT %s
        )
        SELECT x_rows.observed_at, x_rows.aave_price, y_rows.aave_price
        FROM x_rows
        JOIN y_rows ON x_rows.rn = y_rows.rn
        ORDER BY x_rows.rn DESC
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (x_symbol, limit, y_symbol, limit))
            rows = cursor.fetchall()
    result = []
    for observed_at, x_price, y_price in rows:
        x_value = float(x_price)
        y_value = float(y_price)
        result.append(
            {
                "observed_at": iso(observed_at),
                "x_symbol": x_symbol,
                "y_symbol": y_symbol,
                "x_usdc_price": x_value,
                "y_usdc_price": y_value,
                "x_y_price": x_value / y_value if y_value > 0 else None,
            }
        )
    return result


def recent_binance_price_history(database_url: str, symbol: str, limit: int) -> list[dict]:
    query = """
        SELECT observed_at, symbol, price, event_time, source
        FROM binance_price_history
        WHERE symbol = %s
        ORDER BY event_time DESC
        LIMIT %s
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (symbol, limit))
            rows = cursor.fetchall()
    rows.reverse()
    return [
        {
            "observed_at": iso(row[3] or row[0]),
            "symbol": str(row[1]),
            "asset": str(row[1]).replace("USDT", ""),
            "binance_price": float(row[2]),
            "aave_price": None,
            "diff_percent": None,
            "source": str(row[4]),
        }
        for row in rows
    ]


def compact_extreme(items: list[dict], index: int) -> dict:
    if len(items) <= index:
        return {"symbol": None, "change_percent": None}
    change_percent = items[index].get("change_percent")
    return {
        "symbol": items[index].get("symbol"),
        "change_percent": float(change_percent) if change_percent is not None else None,
    }


def compact_extremes_payload(data: dict) -> Optional[dict]:
    top, bottom = data.get("top") or [], data.get("bottom") or []
    if not top or not bottom:
        return None
    return {
        "observed_at": data.get("observed_at"),
        "window_seconds": float(data.get("window_seconds", 0)),
        "sample_count": int(data.get("sample_count", 0)),
        "price_source": data.get("price_source"),
        "a": compact_extreme(top, 0),
        "top_2": compact_extreme(top, 1),
        "b": compact_extreme(bottom, 0),
        "bottom_2": compact_extreme(bottom, 1),
    }


def latest_extremes_from_db(database_url: str) -> Optional[dict]:
    query = """
        SELECT observed_at, window_seconds, sample_count,
               top_symbol_1, top_change_percent_1, top_symbol_2, top_change_percent_2,
               bottom_symbol_1, bottom_change_percent_1, bottom_symbol_2, bottom_change_percent_2
        FROM binance_window_extremes ORDER BY observed_at DESC LIMIT 1
    """
    row = fetch_one(database_url, query)
    if not row:
        return None
    return {
        "observed_at": iso(row[0]),
        "window_seconds": float(row[1]),
        "sample_count": int(row[2]),
        "a": {"symbol": row[3], "change_percent": float(row[4]) if row[4] is not None else None},
        "top_2": {"symbol": row[5], "change_percent": float(row[6]) if row[6] is not None else None},
        "b": {"symbol": row[7], "change_percent": float(row[8]) if row[8] is not None else None},
        "bottom_2": {"symbol": row[9], "change_percent": float(row[10]) if row[10] is not None else None},
    }


def latest_arbitrage_from_db(database_url: str) -> Optional[dict]:
    columns = [
        "observed_at", "window_seconds", "sample_count", "a_symbol", "b_symbol",
        "a_change_percent", "b_change_percent", "a_start_price", "a_end_price",
        "b_start_price", "b_end_price", "notional_usd", "trade_fee_percent",
        "flashloan_fee_percent", "borrowed_b", "a_bought", "usdt_after_selling_a",
        "b_rebought", "b_to_repay", "profit_b", "profit_usd",
        "paper_route_profit_usd", "candidate_score_usd",
        "profit_percent", "candidate_score_percent",
        "gross_relative_edge_percent", "window_spread_percent", "min_window_spread_percent",
        "min_paper_profit_usd", "fee_reserve_percent", "fee_reserve_usd",
        "net_signal_profit_usd", "signal", "blocked_reasons_json",
        "candidate_pair_count", "evaluated_strategy_count", "best_strategy",
        "m1_profit_usd", "m2_profit_usd",
        "selected_signed_profit_usd", "selected_direction_score_usd",
        "selected_expected_profit_usd",
        "route_symbols_json", "borrow_symbol", "swap_symbol",
        "profitable", "strategy", "execution_plan_json",
    ]
    row = fetch_one(database_url, f"SELECT {', '.join(columns)} FROM arbitrage_simulations ORDER BY observed_at DESC LIMIT 1")
    if not row:
        return None
    data = dict(zip(columns, row))
    data["observed_at"] = iso(data["observed_at"])
    data["sample_count"] = int(data["sample_count"])
    data["profitable"] = bool(data["profitable"])
    decode_json_fields(data)
    for key, value in list(data.items()):
        if key not in string_like_arbitrage_keys():
            data[key] = float(value) if value is not None else None
    for int_key in ("candidate_pair_count", "evaluated_strategy_count"):
        if data.get(int_key) is not None:
            data[int_key] = int(data[int_key])
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


def fetch_one(database_url: str, query: str, params: tuple = ()) -> Optional[tuple]:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()


def iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def parse_observed_age_seconds(observed_at: str) -> float:
    observed_at = str(observed_at).replace("Z", "+00:00")
    observed = datetime.fromisoformat(observed_at)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
