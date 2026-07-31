LIQUIDATION_FAILURE_SAMPLE_COLUMNS = {
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


def create_liquidation_borrow_health_pool_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS liquidation_borrow_health_pool (
            account TEXT PRIMARY KEY,
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
