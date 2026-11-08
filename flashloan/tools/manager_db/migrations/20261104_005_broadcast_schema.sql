CREATE TABLE IF NOT EXISTS manager_broadcast_library_state (
    network                  TEXT    NOT NULL,
    chain_id                 INTEGER NOT NULL,
    protocol                 TEXT    NOT NULL,
    scope_key                TEXT    NOT NULL,
    broadcast_version        INTEGER NOT NULL,
    materialized_checksum    TEXT    NOT NULL,
    review_version           INTEGER NOT NULL,
    review_checksum          TEXT    NOT NULL,
    closed_loop_version      INTEGER NOT NULL,
    closed_loop_checksum     TEXT    NOT NULL,
    request_count            INTEGER NOT NULL,
    attempt_count            INTEGER NOT NULL,
    pending_count            INTEGER NOT NULL,
    success_count            INTEGER NOT NULL,
    failed_count             INTEGER NOT NULL,
    terminal_count           INTEGER NOT NULL,
    generated_at             TEXT    NOT NULL,
    updated_at               TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key)
);

CREATE TABLE IF NOT EXISTS manager_cow_execution_attempts (
    network                          TEXT    NOT NULL,
    chain_id                         INTEGER NOT NULL,
    protocol                         TEXT    NOT NULL,
    scope_key                        TEXT    NOT NULL,
    attempt_id                       TEXT    NOT NULL,
    loop_key                         TEXT    NOT NULL,
    closed_loop_version              INTEGER NOT NULL,
    closed_loop_checksum             TEXT    NOT NULL,
    review_version                   INTEGER NOT NULL,
    review_checksum                  TEXT    NOT NULL,
    anchor_token_key_id              TEXT    NOT NULL,
    path_text                        TEXT    NOT NULL,
    path_addresses_json              TEXT    NOT NULL,
    path_symbols_json                TEXT    NOT NULL,
    pool_path_key                    TEXT    NOT NULL,
    pool_ids_json                    TEXT    NOT NULL,
    signed_pnl_raw                   TEXT    NOT NULL,
    signed_pnl_amount                REAL    NOT NULL,
    signed_pnl_currency              TEXT    NOT NULL,
    signed_pnl_address               TEXT,
    signed_pnl_unit                  TEXT    NOT NULL,
    signed_pnl_source                TEXT    NOT NULL,
    signer_address                   TEXT    NOT NULL,
    nonce                            INTEGER NOT NULL,
    gas_limit                        INTEGER,
    gas_price_wei                    TEXT,
    max_fee_per_gas_wei              TEXT,
    max_priority_fee_per_gas_wei     TEXT,
    rpc_url                          TEXT    NOT NULL,
    rpc_name                         TEXT,
    tx_hash                          TEXT,
    status                           TEXT    NOT NULL CHECK (
                                        status IN ('pending', 'success', 'failed')
                                    ),
    status_reason                    TEXT,
    receipt_json                     TEXT    NOT NULL DEFAULT '{}',
    evidence_json                    TEXT    NOT NULL,
    created_at                       TEXT    NOT NULL,
    updated_at                       TEXT    NOT NULL,
    terminal_at                      TEXT,
    PRIMARY KEY (network, chain_id, protocol, scope_key, attempt_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_cow_execution_attempts_loop_status
    ON manager_cow_execution_attempts(
        network, chain_id, protocol, scope_key, loop_key, status
    );

CREATE INDEX IF NOT EXISTS idx_manager_cow_execution_attempts_rpc_created
    ON manager_cow_execution_attempts(
        network, chain_id, protocol, scope_key, rpc_url, created_at
    );

CREATE TABLE IF NOT EXISTS manager_broadcast_attempt_diagnostics (
    network           TEXT    NOT NULL,
    chain_id          INTEGER NOT NULL,
    protocol          TEXT    NOT NULL,
    scope_key         TEXT    NOT NULL,
    attempt_id        TEXT    NOT NULL,
    loop_key          TEXT    NOT NULL,
    diagnostic_id     TEXT    NOT NULL,
    diagnostic_type   TEXT    NOT NULL CHECK (
                            diagnostic_type IN (
                                'rpc_unavailable', 'gas_estimate_failed',
                                'revert', 'nonce_too_low', 'replaced',
                                'timeout', 'broadcast_failed',
                                'evidence_incomplete', 'other'
                            )
                        ),
    diagnostic_code   TEXT,
    message           TEXT,
    rpc_url           TEXT,
    tx_hash           TEXT,
    payload_json      TEXT    NOT NULL DEFAULT '{}',
    observed_at       TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, attempt_id, diagnostic_id
    )
);

CREATE INDEX IF NOT EXISTS idx_manager_broadcast_attempt_diagnostics_type
    ON manager_broadcast_attempt_diagnostics(
        network, chain_id, protocol, scope_key, diagnostic_type
    );
