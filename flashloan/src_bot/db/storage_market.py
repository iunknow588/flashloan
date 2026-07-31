import json

from db.storage_common import db_connection, require_psycopg, utc_from_ms
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
    with db_connection(database_url, connect_timeout=8) as connection:
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


def append_binance_candidate_price_history(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return

    values = [
        (
            row["observed_at"],
            row["symbol"],
            float(row["price_usdc"]),
            float(row["source_price"]),
            float(row["usdc_usdt_price"]),
            float(row["change_percent"]),
            row["rank_side"],
            int(row["rank_position"]),
            row["event_time"],
            row["source"],
        )
        for row in rows
    ]
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO binance_candidate_price_history (
                    observed_at, symbol, price_usdc, source_price, usdc_usdt_price,
                    change_percent, rank_side, rank_position, event_time, source
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, event_time, source) DO NOTHING
                """,
                values,
            )


def append_binance_pair_price_history(database_url: str, rows: list[dict]) -> None:
    if not rows:
        return

    values = [
        (
            row["observed_at"],
            row["x_symbol"],
            row["y_symbol"],
            float(row["x_usdc_price"]),
            float(row["y_usdc_price"]),
            float(row["x_y_price"]),
            float(row["window_seconds"]),
            row["event_time"],
            row["source"],
        )
        for row in rows
    ]
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO binance_pair_price_history (
                    observed_at, x_symbol, y_symbol, x_usdc_price, y_usdc_price,
                    x_y_price, window_seconds, event_time, source
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (x_symbol, y_symbol, event_time, source) DO NOTHING
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
    with db_connection(database_url, connect_timeout=8) as connection:
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
    with db_connection(database_url, connect_timeout=8) as connection:
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
    with db_connection(database_url, connect_timeout=8) as connection:
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
