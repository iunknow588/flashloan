import json
from datetime import datetime, timezone

OBSERVER_ADVISORY_LOCK_ID = 2026072801


def require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install psycopg[binary] or run pip install -r requirements.txt"
        ) from exc
    return psycopg


def utc_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


def ensure_database_schema(database_url: str) -> None:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id BIGSERIAL PRIMARY KEY,
                    observed_at TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    binance_price DOUBLE PRECISION NOT NULL,
                    binance_event_time TIMESTAMPTZ NOT NULL,
                    aave_price DOUBLE PRECISION NOT NULL,
                    aave_block BIGINT NOT NULL,
                    diff_percent DOUBLE PRECISION NOT NULL,
                    binance_age_seconds DOUBLE PRECISION NOT NULL,
                    aave_age_seconds DOUBLE PRECISION NOT NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_symbol_time "
                "ON observations(symbol, observed_at)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS binance_window_extremes (
                    id BIGSERIAL PRIMARY KEY,
                    observed_at TIMESTAMPTZ NOT NULL,
                    window_seconds DOUBLE PRECISION NOT NULL,
                    sample_count INTEGER NOT NULL,
                    top_symbol_1 TEXT,
                    top_change_percent_1 DOUBLE PRECISION,
                    top_symbol_2 TEXT,
                    top_change_percent_2 DOUBLE PRECISION,
                    bottom_symbol_1 TEXT,
                    bottom_change_percent_1 DOUBLE PRECISION,
                    bottom_symbol_2 TEXT,
                    bottom_change_percent_2 DOUBLE PRECISION,
                    top_json TEXT,
                    bottom_json TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS binance_price_history (
                    id BIGSERIAL PRIMARY KEY,
                    observed_at TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    price DOUBLE PRECISION NOT NULL,
                    event_time TIMESTAMPTZ NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_binance_price_history_symbol_time "
                "ON binance_price_history(symbol, event_time)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_binance_window_extremes_time "
                "ON binance_window_extremes(observed_at)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS arbitrage_simulations (
                    id BIGSERIAL PRIMARY KEY,
                    observed_at TIMESTAMPTZ NOT NULL,
                    window_seconds DOUBLE PRECISION NOT NULL,
                    sample_count INTEGER NOT NULL,
                    a_symbol TEXT NOT NULL,
                    b_symbol TEXT NOT NULL,
                    a_change_percent DOUBLE PRECISION NOT NULL,
                    b_change_percent DOUBLE PRECISION NOT NULL,
                    a_start_price DOUBLE PRECISION NOT NULL,
                    a_end_price DOUBLE PRECISION NOT NULL,
                    b_start_price DOUBLE PRECISION NOT NULL,
                    b_end_price DOUBLE PRECISION NOT NULL,
                    notional_usd DOUBLE PRECISION NOT NULL,
                    trade_fee_percent DOUBLE PRECISION NOT NULL,
                    flashloan_fee_percent DOUBLE PRECISION NOT NULL,
                    borrowed_b DOUBLE PRECISION NOT NULL,
                    a_bought DOUBLE PRECISION NOT NULL,
                    usdt_after_selling_a DOUBLE PRECISION NOT NULL,
                    b_rebought DOUBLE PRECISION NOT NULL,
                    b_to_repay DOUBLE PRECISION NOT NULL,
                    profit_b DOUBLE PRECISION NOT NULL,
                    profit_usd DOUBLE PRECISION NOT NULL,
                    paper_route_profit_usd DOUBLE PRECISION,
                    candidate_score_usd DOUBLE PRECISION,
                    profit_percent DOUBLE PRECISION NOT NULL,
                    candidate_score_percent DOUBLE PRECISION,
                    gross_relative_edge_percent DOUBLE PRECISION NOT NULL,
                    window_spread_percent DOUBLE PRECISION,
                    min_window_spread_percent DOUBLE PRECISION,
                    min_paper_profit_usd DOUBLE PRECISION,
                    fee_reserve_percent DOUBLE PRECISION,
                    fee_reserve_usd DOUBLE PRECISION,
                    net_signal_profit_usd DOUBLE PRECISION,
                    signal BOOLEAN,
                    blocked_reasons_json TEXT,
                    profitable BOOLEAN NOT NULL,
                    candidate_pair_count INTEGER,
                    evaluated_strategy_count INTEGER,
                    best_strategy TEXT,
                    m1_profit_usd DOUBLE PRECISION,
                    m2_profit_usd DOUBLE PRECISION,
                    selected_signed_profit_usd DOUBLE PRECISION,
                    selected_direction_score_usd DOUBLE PRECISION,
                    selected_expected_profit_usd DOUBLE PRECISION,
                    route_symbols_json TEXT,
                    borrow_symbol TEXT,
                    swap_symbol TEXT,
                    strategy TEXT,
                    execution_plan_json TEXT
                )
                """
            )
            ensure_schema_columns(cursor)
            ensure_deduplication_constraints(cursor)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_arbitrage_simulations_time "
                "ON arbitrage_simulations(observed_at)"
            )


def ensure_schema_columns(cursor) -> None:
    migrations = {
        "observations": {
            "observed_at": "TIMESTAMPTZ",
            "symbol": "TEXT",
            "asset": "TEXT",
            "binance_price": "DOUBLE PRECISION",
            "binance_event_time": "TIMESTAMPTZ",
            "aave_price": "DOUBLE PRECISION",
            "aave_block": "BIGINT",
            "diff_percent": "DOUBLE PRECISION",
            "binance_age_seconds": "DOUBLE PRECISION",
            "aave_age_seconds": "DOUBLE PRECISION",
        },
        "binance_window_extremes": {
            "observed_at": "TIMESTAMPTZ",
            "window_seconds": "DOUBLE PRECISION",
            "sample_count": "INTEGER",
            "top_symbol_1": "TEXT",
            "top_change_percent_1": "DOUBLE PRECISION",
            "top_start_price_1": "DOUBLE PRECISION",
            "top_end_price_1": "DOUBLE PRECISION",
            "top_start_time_1": "TIMESTAMPTZ",
            "top_end_time_1": "TIMESTAMPTZ",
            "top_symbol_2": "TEXT",
            "top_change_percent_2": "DOUBLE PRECISION",
            "top_start_price_2": "DOUBLE PRECISION",
            "top_end_price_2": "DOUBLE PRECISION",
            "top_start_time_2": "TIMESTAMPTZ",
            "top_end_time_2": "TIMESTAMPTZ",
            "bottom_symbol_1": "TEXT",
            "bottom_change_percent_1": "DOUBLE PRECISION",
            "bottom_start_price_1": "DOUBLE PRECISION",
            "bottom_end_price_1": "DOUBLE PRECISION",
            "bottom_start_time_1": "TIMESTAMPTZ",
            "bottom_end_time_1": "TIMESTAMPTZ",
            "bottom_symbol_2": "TEXT",
            "bottom_change_percent_2": "DOUBLE PRECISION",
            "bottom_start_price_2": "DOUBLE PRECISION",
            "bottom_end_price_2": "DOUBLE PRECISION",
            "bottom_start_time_2": "TIMESTAMPTZ",
            "bottom_end_time_2": "TIMESTAMPTZ",
            "top_json": "TEXT",
            "bottom_json": "TEXT",
        },
        "binance_price_history": {
            "observed_at": "TIMESTAMPTZ",
            "symbol": "TEXT",
            "price": "DOUBLE PRECISION",
            "event_time": "TIMESTAMPTZ",
            "source": "TEXT",
        },
        "arbitrage_simulations": {
            "observed_at": "TIMESTAMPTZ",
            "window_seconds": "DOUBLE PRECISION",
            "sample_count": "INTEGER",
            "a_symbol": "TEXT",
            "b_symbol": "TEXT",
            "a_change_percent": "DOUBLE PRECISION",
            "b_change_percent": "DOUBLE PRECISION",
            "a_start_price": "DOUBLE PRECISION",
            "a_end_price": "DOUBLE PRECISION",
            "b_start_price": "DOUBLE PRECISION",
            "b_end_price": "DOUBLE PRECISION",
            "notional_usd": "DOUBLE PRECISION",
            "trade_fee_percent": "DOUBLE PRECISION",
            "flashloan_fee_percent": "DOUBLE PRECISION",
            "borrowed_b": "DOUBLE PRECISION",
            "a_bought": "DOUBLE PRECISION",
            "usdt_after_selling_a": "DOUBLE PRECISION",
            "b_rebought": "DOUBLE PRECISION",
            "b_to_repay": "DOUBLE PRECISION",
            "profit_b": "DOUBLE PRECISION",
            "profit_usd": "DOUBLE PRECISION",
            "paper_route_profit_usd": "DOUBLE PRECISION",
            "candidate_score_usd": "DOUBLE PRECISION",
            "profit_percent": "DOUBLE PRECISION",
            "candidate_score_percent": "DOUBLE PRECISION",
            "gross_relative_edge_percent": "DOUBLE PRECISION",
            "window_spread_percent": "DOUBLE PRECISION",
            "min_window_spread_percent": "DOUBLE PRECISION",
            "min_paper_profit_usd": "DOUBLE PRECISION",
            "fee_reserve_percent": "DOUBLE PRECISION",
            "fee_reserve_usd": "DOUBLE PRECISION",
            "net_signal_profit_usd": "DOUBLE PRECISION",
            "signal": "BOOLEAN",
            "blocked_reasons_json": "TEXT",
            "profitable": "BOOLEAN",
            "candidate_pair_count": "INTEGER",
            "evaluated_strategy_count": "INTEGER",
            "best_strategy": "TEXT",
            "m1_profit_usd": "DOUBLE PRECISION",
            "m2_profit_usd": "DOUBLE PRECISION",
            "selected_signed_profit_usd": "DOUBLE PRECISION",
            "selected_direction_score_usd": "DOUBLE PRECISION",
            "selected_expected_profit_usd": "DOUBLE PRECISION",
            "route_symbols_json": "TEXT",
            "borrow_symbol": "TEXT",
            "swap_symbol": "TEXT",
            "strategy": "TEXT",
            "execution_plan_json": "TEXT",
        },
    }
    for table, columns in migrations.items():
        for column, column_type in columns.items():
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}")


def ensure_deduplication_constraints(cursor) -> None:
    cursor.execute(
        """
        DELETE FROM binance_price_history keep
        USING binance_price_history dup
        WHERE keep.id > dup.id
          AND keep.symbol = dup.symbol
          AND keep.event_time = dup.event_time
          AND keep.source = dup.source
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_binance_price_history_symbol_event_source
        ON binance_price_history(symbol, event_time, source)
        """
    )
    cursor.execute(
        """
        DELETE FROM observations keep
        USING observations dup
        WHERE keep.id > dup.id
          AND keep.symbol = dup.symbol
          AND keep.binance_event_time = dup.binance_event_time
          AND keep.aave_block = dup.aave_block
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_symbol_event_block
        ON observations(symbol, binance_event_time, aave_block)
        """
    )


def try_acquire_observer_lock(database_url: str, lock_id: int = OBSERVER_ADVISORY_LOCK_ID):
    psycopg = require_psycopg()
    connection = psycopg.connect(database_url, connect_timeout=8)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
            locked = bool(cursor.fetchone()[0])
        if locked:
            return connection
    except Exception:
        connection.close()
        raise
    connection.close()
    return None


def append_binance_price_history(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return

    values = [
        (
            row["observed_at"],
            row["symbol"],
            float(row["price"]),
            row["event_time"],
            row["source"],
        )
        for row in rows
    ]
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO binance_price_history (
                    observed_at, symbol, price, event_time, source
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (symbol, event_time, source) DO NOTHING
                """,
                values,
            )


def append_observations(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return

    values = [
        (
            row["observed_at"],
            row["symbol"],
            row["asset"],
            float(row["binance_price"]),
            row["binance_event_time"],
            float(row["aave_price"]),
            int(row["aave_block"]),
            float(row["diff_percent"]),
            float(row["binance_age_seconds"]),
            float(row["aave_age_seconds"]),
        )
        for row in rows
    ]
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO observations (
                    observed_at, symbol, asset, binance_price, binance_event_time,
                    aave_price, aave_block, diff_percent,
                    binance_age_seconds, aave_age_seconds
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, binance_event_time, aave_block) DO NOTHING
                """,
                values,
            )


def append_binance_extremes(database_url: str, extremes: dict) -> None:
    top = extremes.get("top", [])
    bottom = extremes.get("bottom", [])

    def item(items: list[dict], index: int, key: str):
        return items[index].get(key) if len(items) > index else None

    def item_time(items: list[dict], index: int, key: str):
        value = item(items, index, key)
        return utc_from_ms(value) if value is not None else None

    values = (
        extremes["observed_at"],
        float(extremes["window_seconds"]),
        int(extremes["sample_count"]),
        item(top, 0, "symbol"),
        item(top, 0, "change_percent"),
        item(top, 0, "start_price"),
        item(top, 0, "end_price"),
        item_time(top, 0, "start_ms"),
        item_time(top, 0, "end_ms"),
        item(top, 1, "symbol"),
        item(top, 1, "change_percent"),
        item(top, 1, "start_price"),
        item(top, 1, "end_price"),
        item_time(top, 1, "start_ms"),
        item_time(top, 1, "end_ms"),
        item(bottom, 0, "symbol"),
        item(bottom, 0, "change_percent"),
        item(bottom, 0, "start_price"),
        item(bottom, 0, "end_price"),
        item_time(bottom, 0, "start_ms"),
        item_time(bottom, 0, "end_ms"),
        item(bottom, 1, "symbol"),
        item(bottom, 1, "change_percent"),
        item(bottom, 1, "start_price"),
        item(bottom, 1, "end_price"),
        item_time(bottom, 1, "start_ms"),
        item_time(bottom, 1, "end_ms"),
        json.dumps(top, ensure_ascii=True, separators=(",", ":")),
        json.dumps(bottom, ensure_ascii=True, separators=(",", ":")),
    )
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO binance_window_extremes (
                    observed_at, window_seconds, sample_count,
                    top_symbol_1, top_change_percent_1,
                    top_start_price_1, top_end_price_1,
                    top_start_time_1, top_end_time_1,
                    top_symbol_2, top_change_percent_2,
                    top_start_price_2, top_end_price_2,
                    top_start_time_2, top_end_time_2,
                    bottom_symbol_1, bottom_change_percent_1,
                    bottom_start_price_1, bottom_end_price_1,
                    bottom_start_time_1, bottom_end_time_1,
                    bottom_symbol_2, bottom_change_percent_2,
                    bottom_start_price_2, bottom_end_price_2,
                    bottom_start_time_2, bottom_end_time_2,
                    top_json, bottom_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                values,
            )


def append_arbitrage_simulation(database_url: str, simulation: dict) -> None:
    values = (
        simulation["observed_at"],
        float(simulation["window_seconds"]),
        int(simulation["sample_count"]),
        simulation["a_symbol"],
        simulation["b_symbol"],
        float(simulation["a_change_percent"]),
        float(simulation["b_change_percent"]),
        float(simulation.get("a_start_price", 0)),
        float(simulation.get("a_end_price", 0)),
        float(simulation.get("b_start_price", 0)),
        float(simulation.get("b_end_price", 0)),
        float(simulation["notional_usd"]),
        float(simulation["trade_fee_percent"]),
        float(simulation["flashloan_fee_percent"]),
        float(simulation["borrowed_b"]),
        float(simulation["a_bought"]),
        float(simulation["usdt_after_selling_a"]),
        float(simulation["b_rebought"]),
        float(simulation["b_to_repay"]),
        float(simulation["profit_b"]),
        float(simulation["profit_usd"]),
        float(simulation.get("paper_route_profit_usd") or simulation.get("profit_usd") or 0),
        float(simulation.get("candidate_score_usd") or 0),
        float(simulation["profit_percent"]),
        float(simulation.get("candidate_score_percent") or 0),
        float(simulation["gross_relative_edge_percent"]),
        float(simulation.get("window_spread_percent") or 0),
        float(simulation.get("min_window_spread_percent") or 0),
        float(simulation.get("min_paper_profit_usd") or 0),
        float(simulation.get("fee_reserve_percent") or 0),
        float(simulation.get("fee_reserve_usd") or 0),
        float(simulation.get("net_signal_profit_usd") or simulation.get("profit_usd") or 0),
        bool(simulation.get("signal", simulation.get("profitable"))),
        json.dumps(simulation.get("blocked_reasons") or [], ensure_ascii=True, separators=(",", ":")),
        bool(simulation["profitable"]),
        int(simulation.get("candidate_pair_count") or 0),
        int(simulation.get("evaluated_strategy_count") or 0),
        simulation.get("best_strategy"),
        float(simulation.get("m1_profit_usd") or 0),
        float(simulation.get("m2_profit_usd") or 0),
        float(simulation.get("selected_signed_profit_usd") or 0),
        float(simulation.get("selected_direction_score_usd") or 0),
        float(simulation.get("selected_expected_profit_usd") or 0),
        json.dumps(simulation.get("route_symbols") or [], ensure_ascii=True, separators=(",", ":")),
        simulation.get("borrow_symbol"),
        simulation.get("swap_symbol"),
        simulation.get("strategy"),
        json.dumps(simulation.get("execution_plan"), ensure_ascii=True, separators=(",", ":"))
        if simulation.get("execution_plan")
        else None,
    )
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO arbitrage_simulations (
                    observed_at, window_seconds, sample_count, a_symbol, b_symbol,
                    a_change_percent, b_change_percent, a_start_price, a_end_price,
                    b_start_price, b_end_price, notional_usd, trade_fee_percent,
                    flashloan_fee_percent, borrowed_b, a_bought,
                    usdt_after_selling_a, b_rebought, b_to_repay, profit_b,
                    profit_usd, paper_route_profit_usd, candidate_score_usd,
                    profit_percent, candidate_score_percent, gross_relative_edge_percent,
                    window_spread_percent, min_window_spread_percent, min_paper_profit_usd,
                    fee_reserve_percent, fee_reserve_usd, net_signal_profit_usd,
                    signal, blocked_reasons_json, profitable,
                    candidate_pair_count, evaluated_strategy_count, best_strategy,
                    m1_profit_usd, m2_profit_usd,
                    selected_signed_profit_usd, selected_direction_score_usd,
                    selected_expected_profit_usd,
                    route_symbols_json, borrow_symbol, swap_symbol,
                    strategy, execution_plan_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                values,
            )
