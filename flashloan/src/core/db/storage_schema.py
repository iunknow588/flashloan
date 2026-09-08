from typing import Any

from core.db.storage_common import SCHEMA_ADVISORY_LOCK_ID, SCHEMA_MIGRATIONS, db_connection, require_psycopg
from core.db.storage_chain_markets import ensure_chain_market_registry_schema, seed_builtin_chain_markets
from core.db.storage_liquidation_schema import (
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
                        market_id TEXT NOT NULL,
                        chain_id INTEGER NOT NULL,
                        network TEXT NOT NULL,
                        protocol TEXT NOT NULL,
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
                        market_id TEXT NOT NULL,
                        chain_id INTEGER NOT NULL,
                        network TEXT NOT NULL,
                        protocol TEXT NOT NULL,
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
                        market_id TEXT NOT NULL,
                        chain_id INTEGER NOT NULL,
                        network TEXT NOT NULL,
                        protocol TEXT NOT NULL,
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
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cow_supported_tokens (
                        network TEXT NOT NULL,
                        chain_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        address TEXT NOT NULL,
                        decimals INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        token_json TEXT NOT NULL,
                        refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (network, address)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cow_execution_attempts (
                        id BIGSERIAL PRIMARY KEY,
                        observed_at TIMESTAMPTZ,
                        network TEXT NOT NULL,
                        chain_id INTEGER,
                        owner_address TEXT,
                        pair TEXT,
                        pair_rank INTEGER,
                        priority_reason TEXT,
                        route_path_json TEXT,
                        state TEXT NOT NULL,
                        execution_phase TEXT NOT NULL,
                        checks_passed BOOLEAN NOT NULL DEFAULT FALSE,
                        can_submit_order BOOLEAN NOT NULL DEFAULT FALSE,
                        order_submission_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                        auto_execute_requested BOOLEAN NOT NULL DEFAULT FALSE,
                        final_delta_amount TEXT,
                        final_symbol TEXT,
                        blocked_reasons_json TEXT,
                        quote_json TEXT,
                        precheck_json TEXT,
                        market_state_json TEXT,
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS control_panel_parameters (
                        namespace TEXT NOT NULL,
                        parameter_key TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (namespace, parameter_key)
                    )
                    """
                )
                ensure_chain_market_registry_schema(cursor)
                seed_builtin_chain_markets(cursor)
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
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cow_supported_tokens_network_symbol "
                    "ON cow_supported_tokens(network, symbol)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_network_time "
                    "ON cow_execution_attempts(network, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_state_time "
                    "ON cow_execution_attempts(state, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_control_panel_parameters_namespace "
                    "ON control_panel_parameters(namespace, updated_at DESC)"
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

from core.db.storage_schema_ddl import (
    ensure_deduplication_constraints,
    ensure_liquidation_composite_identity_constraints,
    ensure_liquidation_discovery_scan_constraints,
    ensure_liquidation_market_indexes,
    ensure_liquidation_scan_config_constraints,
    ensure_schema_columns,
)
