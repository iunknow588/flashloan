LIQUIDATION_MARKET_NAMESPACE_COLUMNS = {
    "market_id": "TEXT NOT NULL DEFAULT 'avalanche-aave-v3'",
    "chain_id": "INTEGER NOT NULL DEFAULT 43114",
    "network": "TEXT NOT NULL DEFAULT 'avalanche'",
    "protocol": "TEXT NOT NULL DEFAULT 'aave_v3'",
}

LIQUIDATION_SOURCE_NAMESPACE_COLUMNS = {
    **LIQUIDATION_MARKET_NAMESPACE_COLUMNS,
    "source_rpc": "TEXT",
    "source_block": "BIGINT",
}

LIQUIDATION_EXECUTION_NAMESPACE_COLUMNS = {
    **LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
    "executor_address": "TEXT",
}

LIQUIDATION_FAILURE_SAMPLE_COLUMNS = {
    **LIQUIDATION_EXECUTION_NAMESPACE_COLUMNS,
    "account": "TEXT",
    "block_number": "BIGINT",
    "collateral_asset": "TEXT",
    "debt_asset": "TEXT",
    "failure_type": "TEXT",
    "failure_reason": "TEXT",
    "payload_json": "TEXT",
    "source": "TEXT",
    "created_at": "TIMESTAMPTZ",
}

LIQUIDATION_BORROW_HEALTH_POOL_COLUMNS = {
    **LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
    "account": "TEXT",
    "health_factor": "DOUBLE PRECISION",
    "status": "TEXT",
    "health_factor_band": "TEXT",
    "total_collateral_base": "DOUBLE PRECISION",
    "total_debt_base": "DOUBLE PRECISION",
    "candidate_count": "INTEGER",
    "summary_json": "TEXT",
    "report_json": "TEXT",
    "active": "BOOLEAN",
    "last_scanned_at": "TIMESTAMPTZ",
    "updated_at": "TIMESTAMPTZ",
}

LIQUIDATION_HIGH_FREQUENCY_POOL_COLUMNS = {
    **LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
    "account": "TEXT",
    "health_factor": "DOUBLE PRECISION",
    "status": "TEXT",
    "total_collateral_base": "DOUBLE PRECISION",
    "total_debt_base": "DOUBLE PRECISION",
    "candidate_count": "INTEGER",
    "priority_score": "DOUBLE PRECISION",
    "summary_json": "TEXT",
    "report_json": "TEXT",
    "active": "BOOLEAN",
    "last_scanned_at": "TIMESTAMPTZ",
    "updated_at": "TIMESTAMPTZ",
}

LIQUIDATION_CORE_OPPORTUNITY_POOL_COLUMNS = {
    **LIQUIDATION_EXECUTION_NAMESPACE_COLUMNS,
    "account": "TEXT",
    "health_factor": "DOUBLE PRECISION",
    "priority_score": "DOUBLE PRECISION",
    "total_debt_base": "DOUBLE PRECISION",
    "total_collateral_base": "DOUBLE PRECISION",
    "best_debt_asset": "TEXT",
    "best_collateral_asset": "TEXT",
    "debt_to_cover_units": "TEXT",
    "estimated_operator_net_profit_usd": "DOUBLE PRECISION",
    "estimated_gas_cost_usd": "DOUBLE PRECISION",
    "quote_viable": "BOOLEAN",
    "quote_block": "BIGINT",
    "quote_at": "TIMESTAMPTZ",
    "static_call_status": "TEXT",
    "payload_state": "TEXT",
    "blocked_reasons_json": "TEXT",
    "last_scanned_at": "TIMESTAMPTZ",
    "last_quoted_at": "TIMESTAMPTZ",
    "last_static_call_at": "TIMESTAMPTZ",
    "updated_at": "TIMESTAMPTZ",
    "active": "BOOLEAN",
    "metadata_json": "TEXT",
}

LIQUIDATION_BORROW_HEALTH_SCAN_BATCH_COLUMNS = {
    **LIQUIDATION_SOURCE_NAMESPACE_COLUMNS,
    "id": "BIGSERIAL",
    "started_at": "TIMESTAMPTZ",
    "finished_at": "TIMESTAMPTZ",
    "status": "TEXT",
    "account_count": "INTEGER",
    "scanned_count": "INTEGER",
    "risk_count": "INTEGER",
    "error_count": "INTEGER",
    "entered_count": "INTEGER",
    "exited_count": "INTEGER",
    "rpc_url": "TEXT",
    "block_number": "BIGINT",
    "watch_health_factor": "DOUBLE PRECISION",
    "error": "TEXT",
    "metadata_json": "TEXT",
}

LIQUIDATION_SCAN_CONFIG_LIBRARY_COLUMNS = {
    **LIQUIDATION_MARKET_NAMESPACE_COLUMNS,
    "config_key": "TEXT",
    "category": "TEXT",
    "source_table": "TEXT",
    "source_key": "TEXT",
    "active": "BOOLEAN",
    "payload_json": "TEXT",
    "updated_at": "TIMESTAMPTZ",
}


def create_liquidation_borrow_health_pool_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidation_borrow_health_pool (
            market_id TEXT NOT NULL DEFAULT 'avalanche-aave-v3',
            chain_id INTEGER NOT NULL DEFAULT 43114,
            network TEXT NOT NULL DEFAULT 'avalanche',
            protocol TEXT NOT NULL DEFAULT 'aave_v3',
            source_rpc TEXT,
            source_block BIGINT,
            account TEXT NOT NULL,
            health_factor DOUBLE PRECISION,
            status TEXT,
            health_factor_band TEXT,
            total_collateral_base DOUBLE PRECISION,
            total_debt_base DOUBLE PRECISION,
            candidate_count INTEGER,
            summary_json TEXT,
            report_json TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            last_scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_liquidation_borrow_health_pool_active_hf "
        "ON liquidation_borrow_health_pool(active, health_factor ASC, updated_at DESC)"
    )


def create_liquidation_high_frequency_pool_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidation_high_frequency_pool (
            market_id TEXT NOT NULL DEFAULT 'avalanche-aave-v3',
            chain_id INTEGER NOT NULL DEFAULT 43114,
            network TEXT NOT NULL DEFAULT 'avalanche',
            protocol TEXT NOT NULL DEFAULT 'aave_v3',
            source_rpc TEXT,
            source_block BIGINT,
            account TEXT NOT NULL,
            health_factor DOUBLE PRECISION,
            status TEXT,
            total_collateral_base DOUBLE PRECISION,
            total_debt_base DOUBLE PRECISION,
            candidate_count INTEGER,
            priority_score DOUBLE PRECISION,
            summary_json TEXT,
            report_json TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            last_scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_liquidation_high_frequency_pool_active_priority "
        "ON liquidation_high_frequency_pool(active, priority_score DESC, health_factor ASC, updated_at DESC)"
    )


def create_liquidation_core_opportunity_pool_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidation_core_opportunity_pool (
            market_id TEXT NOT NULL DEFAULT 'avalanche-aave-v3',
            chain_id INTEGER NOT NULL DEFAULT 43114,
            network TEXT NOT NULL DEFAULT 'avalanche',
            protocol TEXT NOT NULL DEFAULT 'aave_v3',
            source_rpc TEXT,
            source_block BIGINT,
            executor_address TEXT,
            account TEXT NOT NULL,
            health_factor DOUBLE PRECISION,
            priority_score DOUBLE PRECISION,
            total_debt_base DOUBLE PRECISION,
            total_collateral_base DOUBLE PRECISION,
            best_debt_asset TEXT,
            best_collateral_asset TEXT,
            debt_to_cover_units TEXT,
            estimated_operator_net_profit_usd DOUBLE PRECISION,
            estimated_gas_cost_usd DOUBLE PRECISION,
            quote_viable BOOLEAN,
            quote_block BIGINT,
            quote_at TIMESTAMPTZ,
            static_call_status TEXT,
            payload_state TEXT,
            blocked_reasons_json TEXT,
            last_scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_quoted_at TIMESTAMPTZ,
            last_static_call_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            metadata_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_liquidation_core_opportunity_active_priority "
        "ON liquidation_core_opportunity_pool(active, priority_score DESC, health_factor ASC, updated_at DESC)"
    )


def create_liquidation_borrow_health_scan_batch_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidation_borrow_health_scan_batches (
            id BIGSERIAL PRIMARY KEY,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            status TEXT NOT NULL,
            account_count INTEGER NOT NULL DEFAULT 0,
            scanned_count INTEGER NOT NULL DEFAULT 0,
            risk_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            entered_count INTEGER NOT NULL DEFAULT 0,
            exited_count INTEGER NOT NULL DEFAULT 0,
            rpc_url TEXT,
            block_number BIGINT,
            watch_health_factor DOUBLE PRECISION,
            error TEXT,
            metadata_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_liquidation_borrow_health_scan_batches_time "
        "ON liquidation_borrow_health_scan_batches(started_at DESC, id DESC)"
    )


def create_liquidation_failure_sample_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidation_failure_samples (
            id BIGSERIAL PRIMARY KEY,
            account TEXT,
            block_number BIGINT,
            collateral_asset TEXT,
            debt_asset TEXT,
            failure_type TEXT NOT NULL,
            failure_reason TEXT,
            payload_json TEXT,
            source TEXT NOT NULL DEFAULT 'execution_attempt',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_liquidation_failure_samples_time "
        "ON liquidation_failure_samples(created_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_liquidation_failure_samples_type_time "
        "ON liquidation_failure_samples(failure_type, created_at DESC)"
    )


def create_liquidation_scan_config_library_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidation_scan_config_library (
            market_id TEXT NOT NULL DEFAULT 'avalanche-aave-v3',
            chain_id INTEGER NOT NULL DEFAULT 43114,
            network TEXT NOT NULL DEFAULT 'avalanche',
            protocol TEXT NOT NULL DEFAULT 'aave_v3',
            config_key TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'scan',
            source_table TEXT NOT NULL,
            source_key TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            payload_json TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_liquidation_scan_config_library_category_time "
        "ON liquidation_scan_config_library(category, updated_at DESC)"
    )
