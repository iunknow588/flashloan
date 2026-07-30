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
