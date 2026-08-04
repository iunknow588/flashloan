import json
from pathlib import Path
from typing import Optional

from db.storage import EXPECTED_SCHEMA_MIGRATION_IDS, require_psycopg
from web.control_panel_data_compact import (
    compact_extremes_payload,
    fetch_one,
    iso,
    latest_arbitrage_from_db,
    latest_extremes_from_db,
    parse_observed_age_seconds,
)


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
    symbols = list(dict.fromkeys(item.get("binance_symbol") for item in assets if item.get("binance_symbol")))
    return {
        "refreshed_at": data.get("refreshed_at"),
        "pool_address": data.get("pool_address"),
        "asset_count": len(assets),
        "symbols": symbols,
    }


def stable_target_cache(cache_path: Path) -> Optional[dict]:
    data = read_json(cache_path)
    if not data:
        return None
    assets = data.get("assets") or []
    symbols = list(dict.fromkeys(item.get("binance_symbol") for item in assets if item.get("binance_symbol")))
    by_source: dict[str, list[str]] = {}
    for item in assets:
        symbol = item.get("binance_symbol")
        for source in [*(item.get("via_borrows") or []), *(item.get("via_stables") or [])]:
            by_source.setdefault(str(source), [])
            if symbol and symbol not in by_source[str(source)]:
                by_source[str(source)].append(str(symbol))
    return {
        "refreshed_at": data.get("refreshed_at"),
        "router_address": data.get("router_address"),
        "asset_count": len(assets),
        "symbols": symbols,
        "by_source": by_source,
        "source": data.get("source"),
    }


def borrow_target_universe(aave_cache_path: Path, stable_target_cache_path: Path) -> Optional[dict]:
    borrow_cache = aave_reserve_cache(aave_cache_path)
    target_cache = stable_target_cache(stable_target_cache_path)
    if not borrow_cache or not target_cache:
        return None
    borrow_symbols = borrow_cache.get("symbols") or []
    target_symbols = target_cache.get("symbols") or []
    target_by_borrow = target_cache.get("by_source") or {}
    borrow_rows = [
        {
            "borrow_symbol": symbol,
            "borrow_name": symbol.replace("USDT", "") if isinstance(symbol, str) else symbol,
            "target_count": len(target_by_borrow.get(symbol, [])),
            "target_symbols": target_by_borrow.get(symbol, []),
        }
        for symbol in borrow_symbols
    ]
    return {
        "borrow_count": len(borrow_symbols),
        "target_count": len(target_symbols),
        "borrow_symbols": borrow_symbols,
        "target_symbols": target_symbols,
        "borrow_rows": borrow_rows,
        "borrow_cache_refreshed_at": borrow_cache.get("refreshed_at"),
        "target_cache_refreshed_at": target_cache.get("refreshed_at"),
        "target_by_borrow": target_by_borrow,
        "target_source": target_cache.get("source"),
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
            (SELECT COUNT(*) FROM binance_candidate_price_history) AS binance_candidate_price_history,
            (SELECT COUNT(*) FROM binance_pair_price_history) AS binance_pair_price_history,
            (SELECT COUNT(*) FROM binance_window_extremes) AS binance_window_extremes,
            (SELECT COUNT(*) FROM arbitrage_simulations) AS arbitrage_simulations,
            (SELECT COUNT(*) FROM liquidation_accounts) AS liquidation_accounts,
            (SELECT COUNT(*) FROM liquidation_discovery_scans) AS liquidation_discovery_scans,
            (SELECT COUNT(*) FROM liquidation_account_health_scans) AS liquidation_account_health_scans,
            (SELECT COUNT(*) FROM liquidation_borrow_health_pool) AS liquidation_borrow_health_pool,
            (SELECT COUNT(*) FROM liquidation_high_frequency_pool) AS liquidation_high_frequency_pool,
            (SELECT COUNT(*) FROM liquidation_core_opportunity_pool) AS liquidation_core_opportunity_pool,
            (SELECT COUNT(*) FROM liquidation_borrow_health_scan_batches) AS liquidation_borrow_health_scan_batches,
            (SELECT COUNT(*) FROM liquidation_scan_config_library) AS liquidation_scan_config_library,
            (SELECT COUNT(*) FROM liquidation_failure_samples) AS liquidation_failure_samples,
            (SELECT COUNT(*) FROM schema_migrations) AS schema_migrations,
            (
                SELECT COALESCE(MAX(applied_at)::TEXT, '')
                FROM schema_migrations
            ) AS latest_schema_migration_at,
            (
                SELECT COUNT(*)
                FROM schema_migrations
                WHERE migration_id = ANY(%s)
            ) AS expected_schema_migrations_applied
    """
    try:
        row = fetch_one(database_url, query, (list(EXPECTED_SCHEMA_MIGRATION_IDS),))
        if not row:
            return None
        counts = {
            "observations": int(row[0] or 0),
            "binance_price_history": int(row[1] or 0),
            "binance_candidate_price_history": int(row[2] or 0),
            "binance_pair_price_history": int(row[3] or 0),
            "binance_window_extremes": int(row[4] or 0),
            "arbitrage_simulations": int(row[5] or 0),
            "liquidation_accounts": int(row[6] or 0),
            "liquidation_discovery_scans": int(row[7] or 0),
            "liquidation_account_health_scans": int(row[8] or 0),
            "liquidation_borrow_health_pool": int(row[9] or 0),
            "liquidation_high_frequency_pool": int(row[10] or 0),
            "liquidation_core_opportunity_pool": int(row[11] or 0),
            "liquidation_borrow_health_scan_batches": int(row[12] or 0),
            "liquidation_scan_config_library": int(row[13] or 0),
            "liquidation_failure_samples": int(row[14] or 0),
            "schema_migrations": int(row[15] or 0),
        }
        table_total = sum(counts.values())
        expected_count = len(EXPECTED_SCHEMA_MIGRATION_IDS)
        applied_count = int(row[17] or 0)
        counts["schema"] = {
            "latest_migration_at": str(row[16] or ""),
            "expected_migration_count": expected_count,
            "expected_migration_applied_count": applied_count,
            "up_to_date": applied_count == expected_count,
        }
        counts["total"] = table_total
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


def available_candidate_symbols(database_url: str, limit: int = 500) -> list[str]:
    query = """
        SELECT symbol
        FROM (
            SELECT symbol, MAX(event_time) AS latest_at
            FROM binance_candidate_price_history
            GROUP BY symbol
            UNION ALL
            SELECT x_symbol AS symbol, MAX(event_time) AS latest_at
            FROM binance_pair_price_history
            GROUP BY x_symbol
            UNION ALL
            SELECT y_symbol AS symbol, MAX(event_time) AS latest_at
            FROM binance_pair_price_history
            GROUP BY y_symbol
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


def recent_velocity_timepoints(database_url: str, limit: int = 200) -> list[dict]:
    query = """
        SELECT id, observed_at, window_seconds, sample_count, top_json, bottom_json
        FROM binance_window_extremes
        ORDER BY observed_at DESC
        LIMIT %s
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
    result = []
    for row_id, observed_at, window_seconds, sample_count, top_json, bottom_json in rows:
        top = decode_extreme_json(top_json)
        bottom = decode_extreme_json(bottom_json)
        result.append(
            {
                "id": int(row_id),
                "observed_at": iso(observed_at),
                "window_seconds": float(window_seconds),
                "sample_count": int(sample_count),
                "top_symbols": [str(item.get("symbol")) for item in top[:5] if item.get("symbol")],
                "bottom_symbols": [str(item.get("symbol")) for item in bottom[:5] if item.get("symbol")],
            }
        )
    return result


def velocity_timepoint_snapshot(database_url: str, snapshot_id: int | None = None) -> Optional[dict]:
    if snapshot_id is None:
        query = """
            SELECT id, observed_at, window_seconds, sample_count, top_json, bottom_json
            FROM binance_window_extremes
            ORDER BY observed_at DESC
            LIMIT 1
        """
        params = ()
    else:
        query = """
            SELECT id, observed_at, window_seconds, sample_count, top_json, bottom_json
            FROM binance_window_extremes
            WHERE id = %s
        """
        params = (snapshot_id,)
    row = fetch_one(database_url, query, params)
    if not row:
        return None
    row_id, observed_at, window_seconds, sample_count, top_json, bottom_json = row
    return {
        "id": int(row_id),
        "observed_at": iso(observed_at),
        "window_seconds": float(window_seconds),
        "sample_count": int(sample_count),
        "top": decode_extreme_json(top_json),
        "bottom": decode_extreme_json(bottom_json),
    }


def decode_extreme_json(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(
                {
                    "symbol": str(item["symbol"]).upper(),
                    "change_percent": float(item["change_percent"]),
                    "start_price": float(item["start_price"]),
                    "end_price": float(item["end_price"]),
                    "start_ms": item.get("start_ms"),
                    "end_ms": item.get("end_ms"),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


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


def latest_observation_prices(database_url: str, symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    query = """
        SELECT DISTINCT ON (symbol)
               observed_at, symbol, asset, binance_price, aave_price, diff_percent
        FROM observations
        WHERE symbol = ANY(%s)
        ORDER BY symbol, observed_at DESC
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (symbols,))
            rows = cursor.fetchall()
    result = {}
    for observed_at, symbol, asset, binance_price, aave_price, diff_percent in rows:
        result[str(symbol)] = {
            "observed_at": iso(observed_at),
            "symbol": str(symbol),
            "asset": str(asset),
            "binance_price": float(binance_price),
            "aave_price": float(aave_price),
            "diff_percent": float(diff_percent),
        }
    return result


def latest_observation_prices_at_or_before(database_url: str, symbols: list[str], cutoff_at) -> dict[str, dict]:
    if not symbols:
        return {}
    query = """
        SELECT DISTINCT ON (symbol)
               observed_at, symbol, asset, binance_price, aave_price, diff_percent
        FROM observations
        WHERE symbol = ANY(%s) AND observed_at <= %s
        ORDER BY symbol, observed_at DESC
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (symbols, cutoff_at))
            rows = cursor.fetchall()
    result = {}
    for observed_at, symbol, asset, binance_price, aave_price, diff_percent in rows:
        result[str(symbol)] = {
            "observed_at": iso(observed_at),
            "symbol": str(symbol),
            "asset": str(asset),
            "binance_price": float(binance_price),
            "aave_price": float(aave_price),
            "diff_percent": float(diff_percent),
        }
    return result


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


def recent_binance_pair_prices(database_url: str, x_symbol: str, y_symbol: str, limit: int) -> list[dict]:
    query = """
        SELECT observed_at, x_symbol, y_symbol, x_usdc_price, y_usdc_price, x_y_price, event_time, source
        FROM binance_pair_price_history
        WHERE (x_symbol = %s AND y_symbol = %s) OR (x_symbol = %s AND y_symbol = %s)
        ORDER BY event_time DESC
        LIMIT %s
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (x_symbol, y_symbol, y_symbol, x_symbol, limit))
            rows = cursor.fetchall()
    rows.reverse()
    result = []
    for observed_at, stored_x, stored_y, stored_x_price, stored_y_price, stored_pair_price, event_time, source in rows:
        stored_x = str(stored_x)
        stored_y = str(stored_y)
        stored_x_price = float(stored_x_price)
        stored_y_price = float(stored_y_price)
        if stored_x == x_symbol and stored_y == y_symbol:
            x_price = stored_x_price
            y_price = stored_y_price
            pair_price = float(stored_pair_price)
        else:
            x_price = stored_y_price
            y_price = stored_x_price
            pair_price = x_price / y_price if y_price > 0 else None
        result.append(
            {
                "observed_at": iso(event_time or observed_at),
                "x_symbol": x_symbol,
                "y_symbol": y_symbol,
                "x_usdc_price": x_price,
                "y_usdc_price": y_price,
                "x_y_price": pair_price,
                "source": str(source),
            }
        )
    return result


def latest_candidate_price_rows(database_url: str, symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    query = """
        SELECT DISTINCT ON (symbol)
               observed_at, symbol, price_usdc, change_percent, rank_side,
               rank_position, event_time, source
        FROM binance_candidate_price_history
        WHERE symbol = ANY(%s)
        ORDER BY symbol, event_time DESC
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (symbols,))
            rows = cursor.fetchall()
    result = {}
    for observed_at, symbol, price_usdc, change_percent, rank_side, rank_position, event_time, source in rows:
        end_price = float(price_usdc)
        change = float(change_percent)
        start_divisor = 1 + change / 100
        start_price = end_price / start_divisor if start_divisor > 0 else end_price
        result[str(symbol)] = {
            "observed_at": iso(event_time or observed_at),
            "symbol": str(symbol),
            "start_price": start_price,
            "end_price": end_price,
            "change_percent": change,
            "rank_side": str(rank_side),
            "rank_position": int(rank_position),
            "source": str(source),
        }
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
