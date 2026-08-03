from typing import Any

from db.storage_common import SCHEMA_ADVISORY_LOCK_ID, SCHEMA_MIGRATIONS, db_connection, require_psycopg
from db.storage_liquidation_schema import (
    LIQUIDATION_BORROW_HEALTH_POOL_COLUMNS,
    LIQUIDATION_BORROW_HEALTH_SCAN_BATCH_COLUMNS,
    LIQUIDATION_CORE_OPPORTUNITY_POOL_COLUMNS,
    LIQUIDATION_EXECUTION_NAMESPACE_COLUMNS,
    LIQUIDATION_FAILURE_SAMPLE_COLUMNS,
    LIQUIDATION_HIGH_FREQUENCY_POOL_COLUMNS,
    LIQUIDATION_MARKET_NAMESPACE_COLUMNS,
    LIQUIDATION_SCAN_CONFIG_LIBRARY_COLUMNS,
    LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
    create_liquidation_borrow_health_scan_batch_schema,
    create_liquidation_borrow_health_pool_schema,
    create_liquidation_core_opportunity_pool_schema,
    create_liquidation_failure_sample_schema,
    create_liquidation_high_frequency_pool_schema,
    create_liquidation_scan_config_library_schema,
)
def ensure_database_schema(database_url: str) -> None:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (SCHEMA_ADVISORY_LOCK_ID,))
            lock_row = cursor.fetchone()
            lock_acquired = bool(lock_row and lock_row[0])
            if not lock_acquired:
                return
            try:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        migration_id TEXT PRIMARY KEY,
                        description TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
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
                    """
                    CREATE TABLE IF NOT EXISTS binance_candidate_price_history (
                        id BIGSERIAL PRIMARY KEY,
                        observed_at TIMESTAMPTZ NOT NULL,
                        symbol TEXT NOT NULL,
                        price_usdc DOUBLE PRECISION NOT NULL,
                        source_price DOUBLE PRECISION NOT NULL,
                        usdc_usdt_price DOUBLE PRECISION NOT NULL,
                        change_percent DOUBLE PRECISION NOT NULL,
                        rank_side TEXT NOT NULL,
                        rank_position INTEGER NOT NULL,
                        event_time TIMESTAMPTZ NOT NULL,
                        source TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS binance_pair_price_history (
                        id BIGSERIAL PRIMARY KEY,
                        observed_at TIMESTAMPTZ NOT NULL,
                        x_symbol TEXT NOT NULL,
                        y_symbol TEXT NOT NULL,
                        x_usdc_price DOUBLE PRECISION NOT NULL,
                        y_usdc_price DOUBLE PRECISION NOT NULL,
                        x_y_price DOUBLE PRECISION NOT NULL,
                        window_seconds DOUBLE PRECISION NOT NULL,
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
                    "CREATE INDEX IF NOT EXISTS idx_binance_candidate_price_history_symbol_time "
                    "ON binance_candidate_price_history(symbol, event_time)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_binance_pair_price_history_pair_time "
                    "ON binance_pair_price_history(x_symbol, y_symbol, event_time)"
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
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS liquidation_accounts (
                        market_id TEXT NOT NULL DEFAULT 'avalanche-aave-v3',
                        chain_id INTEGER NOT NULL DEFAULT 43114,
                        network TEXT NOT NULL DEFAULT 'avalanche',
                        protocol TEXT NOT NULL DEFAULT 'aave_v3',
                        source_rpc TEXT,
                        source_block BIGINT,
                        account TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'manual',
                        active BOOLEAN NOT NULL DEFAULT TRUE,
                        scan_start_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        scan_end_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_scanned_at TIMESTAMPTZ,
                        last_health_factor DOUBLE PRECISION,
                        last_status TEXT,
                        last_health_factor_band TEXT,
                        last_candidate_count INTEGER,
                        last_total_collateral_base DOUBLE PRECISION,
                        last_total_debt_base DOUBLE PRECISION,
                        activity_tier TEXT,
                        last_summary_json TEXT,
                        last_report_json TEXT
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS liquidation_discovery_scans (
                        id BIGSERIAL PRIMARY KEY,
                        market_id TEXT NOT NULL DEFAULT 'avalanche-aave-v3',
                        chain_id INTEGER NOT NULL DEFAULT 43114,
                        network TEXT NOT NULL DEFAULT 'avalanche',
                        protocol TEXT NOT NULL DEFAULT 'aave_v3',
                        source_rpc TEXT,
                        source_block BIGINT,
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        rpc_url TEXT,
                        pool_address TEXT,
                        from_block BIGINT NOT NULL,
                        to_block BIGINT NOT NULL,
                        scan_start_at TIMESTAMPTZ NOT NULL,
                        scan_end_at TIMESTAMPTZ NOT NULL,
                        discovered_count INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS liquidation_account_health_scans (
                        id BIGSERIAL PRIMARY KEY,
                        account TEXT NOT NULL,
                        scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        health_factor DOUBLE PRECISION,
                        status TEXT,
                        health_factor_band TEXT,
                        candidate_count INTEGER,
                        summary_json TEXT,
                        report_json TEXT
                    )
                    """
                )
                create_liquidation_borrow_health_pool_schema(cursor)
                create_liquidation_high_frequency_pool_schema(cursor)
                create_liquidation_core_opportunity_pool_schema(cursor)
                create_liquidation_borrow_health_scan_batch_schema(cursor)
                create_liquidation_scan_config_library_schema(cursor)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS liquidation_execution_attempts (
                        id BIGSERIAL PRIMARY KEY,
                        market_id TEXT NOT NULL DEFAULT 'avalanche-aave-v3',
                        chain_id INTEGER NOT NULL DEFAULT 43114,
                        network TEXT NOT NULL DEFAULT 'avalanche',
                        protocol TEXT NOT NULL DEFAULT 'aave_v3',
                        source_rpc TEXT,
                        source_block BIGINT,
                        executor_address TEXT,
                        account TEXT,
                        mode TEXT NOT NULL,
                        state TEXT NOT NULL,
                        blocked_reasons_json TEXT,
                        request_json TEXT,
                        quote_json TEXT,
                        preflight_json TEXT,
                        tx_hash TEXT,
                        receipt_json TEXT,
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                create_liquidation_failure_sample_schema(cursor)
                ensure_schema_columns(cursor)
                ensure_liquidation_market_indexes(cursor)
                ensure_deduplication_constraints(cursor)
                ensure_liquidation_scan_config_constraints(cursor)
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_arbitrage_simulations_time "
                    "ON arbitrage_simulations(observed_at)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_liquidation_accounts_active_updated "
                    "ON liquidation_accounts(active, updated_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_liquidation_discovery_scans_mode_time "
                    "ON liquidation_discovery_scans(mode, scan_start_at, scan_end_at)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_liquidation_health_account_scanned "
                    "ON liquidation_account_health_scans(account, scanned_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_liquidation_execution_attempts_time "
                    "ON liquidation_execution_attempts(created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_liquidation_execution_attempts_account_time "
                    "ON liquidation_execution_attempts(account, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_liquidation_execution_attempts_state_time "
                    "ON liquidation_execution_attempts(state, created_at DESC)"
                )
                record_schema_migrations(cursor)
            finally:
                if lock_acquired:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_ADVISORY_LOCK_ID,))


def record_schema_migrations(cursor) -> None:
    for migration_id, description in SCHEMA_MIGRATIONS:
        cursor.execute(
            """
            INSERT INTO schema_migrations (migration_id, description, applied_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (migration_id) DO NOTHING
            """,
            (migration_id, description),
        )


def load_schema_migrations(database_url: str) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT migration_id, description, applied_at
                FROM schema_migrations
                ORDER BY applied_at, migration_id
                """
            )
            rows = cursor.fetchall()
    return [
        {
            "migration_id": str(row[0]),
            "description": str(row[1]),
            "applied_at": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
        }
        for row in rows
    ]


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
        "binance_candidate_price_history": {
            "observed_at": "TIMESTAMPTZ",
            "symbol": "TEXT",
            "price_usdc": "DOUBLE PRECISION",
            "source_price": "DOUBLE PRECISION",
            "usdc_usdt_price": "DOUBLE PRECISION",
            "change_percent": "DOUBLE PRECISION",
            "rank_side": "TEXT",
            "rank_position": "INTEGER",
            "event_time": "TIMESTAMPTZ",
            "source": "TEXT",
        },
        "binance_pair_price_history": {
            "observed_at": "TIMESTAMPTZ",
            "x_symbol": "TEXT",
            "y_symbol": "TEXT",
            "x_usdc_price": "DOUBLE PRECISION",
            "y_usdc_price": "DOUBLE PRECISION",
            "x_y_price": "DOUBLE PRECISION",
            "window_seconds": "DOUBLE PRECISION",
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
        "liquidation_accounts": {
            **LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
            "account": "TEXT",
            "source": "TEXT",
            "active": "BOOLEAN",
            "scan_start_at": "TIMESTAMPTZ",
            "scan_end_at": "TIMESTAMPTZ",
            "added_at": "TIMESTAMPTZ",
            "updated_at": "TIMESTAMPTZ",
            "last_scanned_at": "TIMESTAMPTZ",
            "last_health_factor": "DOUBLE PRECISION",
            "last_status": "TEXT",
            "last_health_factor_band": "TEXT",
            "last_candidate_count": "INTEGER",
            "last_total_collateral_base": "DOUBLE PRECISION",
            "last_total_debt_base": "DOUBLE PRECISION",
            "activity_tier": "TEXT",
            "last_summary_json": "TEXT",
            "last_report_json": "TEXT",
        },
        "liquidation_discovery_scans": {
            **LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
            "mode": "TEXT",
            "status": "TEXT",
            "rpc_url": "TEXT",
            "pool_address": "TEXT",
            "from_block": "BIGINT",
            "to_block": "BIGINT",
            "scan_start_at": "TIMESTAMPTZ",
            "scan_end_at": "TIMESTAMPTZ",
            "discovered_count": "INTEGER",
            "error": "TEXT",
            "created_at": "TIMESTAMPTZ",
        },
        "liquidation_account_health_scans": {
            **LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
            "account": "TEXT",
            "scanned_at": "TIMESTAMPTZ",
            "health_factor": "DOUBLE PRECISION",
            "status": "TEXT",
            "health_factor_band": "TEXT",
            "candidate_count": "INTEGER",
            "summary_json": "TEXT",
            "report_json": "TEXT",
        },
        "liquidation_borrow_health_pool": LIQUIDATION_BORROW_HEALTH_POOL_COLUMNS,
        "liquidation_high_frequency_pool": LIQUIDATION_HIGH_FREQUENCY_POOL_COLUMNS,
        "liquidation_core_opportunity_pool": LIQUIDATION_CORE_OPPORTUNITY_POOL_COLUMNS,
        "liquidation_borrow_health_scan_batches": LIQUIDATION_BORROW_HEALTH_SCAN_BATCH_COLUMNS,
        "liquidation_scan_config_library": LIQUIDATION_SCAN_CONFIG_LIBRARY_COLUMNS,
        "liquidation_execution_attempts": {
            **LIQUIDATION_EXECUTION_NAMESPACE_COLUMNS,
            "account": "TEXT",
            "mode": "TEXT",
            "state": "TEXT",
            "blocked_reasons_json": "TEXT",
            "request_json": "TEXT",
            "quote_json": "TEXT",
            "preflight_json": "TEXT",
            "tx_hash": "TEXT",
            "receipt_json": "TEXT",
            "error": "TEXT",
            "created_at": "TIMESTAMPTZ",
            "updated_at": "TIMESTAMPTZ",
        },
        "liquidation_failure_samples": LIQUIDATION_FAILURE_SAMPLE_COLUMNS,
    }
    for table, columns in migrations.items():
        for column, column_type in columns.items():
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}")


def ensure_liquidation_market_indexes(cursor) -> None:
    market_tables = {
        "liq_acct_mkt": ("liquidation_accounts", "active, updated_at DESC"),
        "liq_borrow_mkt": ("liquidation_borrow_health_pool", "active, health_factor ASC, updated_at DESC"),
        "liq_high_mkt": ("liquidation_high_frequency_pool", "active, priority_score DESC, health_factor ASC, updated_at DESC"),
        "liq_core_mkt": ("liquidation_core_opportunity_pool", "active, priority_score DESC, health_factor ASC, updated_at DESC"),
        "liq_health_scan_mkt": ("liquidation_account_health_scans", "scanned_at DESC"),
        "liq_batch_mkt": ("liquidation_borrow_health_scan_batches", "started_at DESC, id DESC"),
        "liq_discovery_mkt": ("liquidation_discovery_scans", "mode, scan_start_at DESC"),
        "liq_attempt_mkt": ("liquidation_execution_attempts", "created_at DESC"),
        "liq_failure_mkt": ("liquidation_failure_samples", "created_at DESC"),
        "liq_config_mkt": ("liquidation_scan_config_library", "category, updated_at DESC"),
    }
    for index_name, (table, suffix) in market_tables.items():
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{index_name}
            ON {table}(market_id, chain_id, {suffix})
            """
        )


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
        DELETE FROM binance_candidate_price_history keep
        USING binance_candidate_price_history dup
        WHERE keep.id > dup.id
          AND keep.symbol = dup.symbol
          AND keep.event_time = dup.event_time
          AND keep.source = dup.source
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_binance_candidate_symbol_event_source
        ON binance_candidate_price_history(symbol, event_time, source)
        """
    )
    cursor.execute(
        """
        DELETE FROM binance_pair_price_history keep
        USING binance_pair_price_history dup
        WHERE keep.id > dup.id
          AND keep.x_symbol = dup.x_symbol
          AND keep.y_symbol = dup.y_symbol
          AND keep.event_time = dup.event_time
          AND keep.source = dup.source
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_binance_pair_symbols_event_source
        ON binance_pair_price_history(x_symbol, y_symbol, event_time, source)
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
    ensure_liquidation_composite_identity_constraints(cursor)
    ensure_liquidation_discovery_scan_constraints(cursor)


def ensure_liquidation_composite_identity_constraints(cursor) -> None:
    identity_tables = {
        "liquidation_accounts": "uq_liquidation_accounts_market_chain_account",
        "liquidation_borrow_health_pool": "uq_liquidation_borrow_health_pool_market_chain_account",
        "liquidation_high_frequency_pool": "uq_liquidation_high_frequency_pool_market_chain_account",
        "liquidation_core_opportunity_pool": "uq_liquidation_core_opportunity_pool_market_chain_account",
    }
    for table, unique_index in identity_tables.items():
        cursor.execute(
            f"""
            DO $$
            DECLARE single_account_constraint record;
            BEGIN
              FOR single_account_constraint IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{table}'::regclass
                  AND contype IN ('p', 'u')
                  AND array_length(conkey, 1) = 1
                  AND conkey[1] = (
                    SELECT attnum
                    FROM pg_attribute
                    WHERE attrelid = '{table}'::regclass
                      AND attname = 'account'
                  )
              LOOP
                EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', '{table}', single_account_constraint.conname);
              END LOOP;
            END $$;
            """
        )
        cursor.execute(f"DROP INDEX IF EXISTS uq_{table}_account")
        cursor.execute(f"DROP INDEX IF EXISTS {table}_account_key")
        cursor.execute(
            f"""
            DELETE FROM {table} keep
            USING {table} dup
            WHERE keep.ctid > dup.ctid
              AND keep.market_id = dup.market_id
              AND keep.chain_id = dup.chain_id
              AND keep.account = dup.account
            """
        )
        cursor.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {unique_index}
            ON {table}(market_id, chain_id, account)
            """
        )


def ensure_liquidation_scan_config_constraints(cursor) -> None:
    cursor.execute(
        """
        DO $$
        DECLARE single_key_constraint record;
        BEGIN
          FOR single_key_constraint IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'liquidation_scan_config_library'::regclass
              AND contype IN ('p', 'u')
              AND array_length(conkey, 1) = 1
              AND conkey[1] = (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'liquidation_scan_config_library'::regclass
                  AND attname = 'config_key'
              )
          LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', 'liquidation_scan_config_library', single_key_constraint.conname);
          END LOOP;
        END $$;
        """
    )
    cursor.execute("DROP INDEX IF EXISTS liquidation_scan_config_library_config_key_key")
    cursor.execute(
        """
        DELETE FROM liquidation_scan_config_library keep
        USING liquidation_scan_config_library dup
        WHERE keep.ctid > dup.ctid
          AND keep.market_id = dup.market_id
          AND keep.chain_id = dup.chain_id
          AND keep.config_key = dup.config_key
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_liquidation_scan_config_market_chain_key
        ON liquidation_scan_config_library(market_id, chain_id, config_key)
        """
    )


def ensure_liquidation_discovery_scan_constraints(cursor) -> None:
    cursor.execute(
        """
        DROP INDEX IF EXISTS uq_liquidation_discovery_scan_window
        """
    )
    cursor.execute(
        """
        DELETE FROM liquidation_discovery_scans keep
        USING liquidation_discovery_scans dup
        WHERE keep.ctid > dup.ctid
          AND keep.market_id = dup.market_id
          AND keep.chain_id = dup.chain_id
          AND keep.mode = dup.mode
          AND keep.pool_address = dup.pool_address
          AND keep.from_block = dup.from_block
          AND keep.to_block = dup.to_block
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_liquidation_discovery_scan_market_window
        ON liquidation_discovery_scans(market_id, chain_id, mode, pool_address, from_block, to_block)
        """
    )


