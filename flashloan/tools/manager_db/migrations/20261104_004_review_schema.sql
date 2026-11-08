CREATE TABLE IF NOT EXISTS manager_review_library_state (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    review_version                  INTEGER NOT NULL,
    materialized_checksum           TEXT    NOT NULL,
    closed_loop_version             INTEGER NOT NULL,
    closed_loop_checksum            TEXT    NOT NULL,
    candidate_edge_version          INTEGER NOT NULL,
    candidate_edge_checksum         TEXT    NOT NULL,
    observation_version             INTEGER NOT NULL,
    observation_checksum            TEXT    NOT NULL,
    pool_scope_checksum             TEXT    NOT NULL,
    library_versions_json           TEXT    NOT NULL,
    library_checksums_json          TEXT    NOT NULL,
    loop_count                      INTEGER NOT NULL,
    reviewed_count                  INTEGER NOT NULL,
    completed_count                 INTEGER NOT NULL,
    data_incomplete_count           INTEGER NOT NULL,
    not_executable_count            INTEGER NOT NULL,
    failed_count                    INTEGER NOT NULL,
    broadcast_request_count         INTEGER NOT NULL,
    generated_at                    TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key)
);

CREATE TABLE IF NOT EXISTS manager_v4_reviews (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    closed_loop_version             INTEGER NOT NULL,
    closed_loop_checksum            TEXT    NOT NULL,
    candidate_edge_version          INTEGER NOT NULL,
    candidate_edge_checksum         TEXT    NOT NULL,
    observation_version             INTEGER NOT NULL,
    observation_checksum            TEXT    NOT NULL,
    pool_scope_checksum             TEXT    NOT NULL,
    anchor_token_key_id             TEXT    NOT NULL,
    anchor_address                  TEXT    NOT NULL,
    anchor_symbol                   TEXT    NOT NULL,
    path_text                       TEXT    NOT NULL,
    path_addresses_json             TEXT    NOT NULL,
    path_symbols_json               TEXT    NOT NULL,
    pool_path_key                   TEXT    NOT NULL,
    pool_ids_json                   TEXT    NOT NULL,
    review_status                   TEXT    NOT NULL CHECK (
                                        review_status IN (
                                            'completed', 'not_executable',
                                            'data_incomplete', 'failed'
                                        )
                                    ),
    review_reason                   TEXT,
    static_preview_json             TEXT    NOT NULL,
    run_arbitrage_json              TEXT    NOT NULL,
    signed_pnl_raw                  TEXT,
    signed_pnl_amount               REAL,
    signed_pnl_currency             TEXT,
    signed_pnl_address              TEXT,
    signed_pnl_unit                 TEXT,
    signed_pnl_source               TEXT,
    reviewed_at                     TEXT    NOT NULL,
    created_at                      TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key, loop_key)
);

CREATE INDEX IF NOT EXISTS idx_manager_v4_reviews_anchor
    ON manager_v4_reviews(
        network, chain_id, protocol, scope_key, anchor_token_key_id
    );

CREATE TABLE IF NOT EXISTS manager_v4_review_diagnostics (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    diagnostic_id                   TEXT    NOT NULL,
    diagnostic_type                 TEXT    NOT NULL CHECK (
                                        diagnostic_type IN (
                                            'swap_not_resolved', 'revert',
                                            'quote_failed', 'rpc_unavailable',
                                            'evidence_missing', 'other'
                                        )
                                    ),
    diagnostic_code                 TEXT,
    message                         TEXT,
    hop_index                       INTEGER,
    pool_id                         TEXT,
    selector                        TEXT,
    payload_json                    TEXT NOT NULL DEFAULT '{}',
    observed_at                     TEXT NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, loop_key, diagnostic_id
    )
);

CREATE INDEX IF NOT EXISTS idx_manager_v4_review_diagnostics_type
    ON manager_v4_review_diagnostics(
        network, chain_id, protocol, scope_key, diagnostic_type
    );

CREATE TABLE IF NOT EXISTS manager_v4_review_broadcast_requests (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    closed_loop_version             INTEGER NOT NULL,
    closed_loop_checksum            TEXT    NOT NULL,
    anchor_token_key_id             TEXT    NOT NULL,
    path_text                       TEXT    NOT NULL,
    path_addresses_json             TEXT    NOT NULL,
    path_symbols_json               TEXT    NOT NULL,
    pool_path_key                   TEXT    NOT NULL,
    pool_ids_json                   TEXT    NOT NULL,
    signed_pnl_raw                  TEXT    NOT NULL,
    signed_pnl_amount               REAL    NOT NULL,
    signed_pnl_currency             TEXT    NOT NULL,
    signed_pnl_address              TEXT,
    signed_pnl_unit                 TEXT    NOT NULL,
    signed_pnl_source               TEXT    NOT NULL,
    request_status                  TEXT    NOT NULL CHECK (
                                        request_status IN (
                                            'generated', 'confirmed',
                                            'consumed', 'canceled', 'failed'
                                        )
                                    ),
    request_reason                  TEXT,
    created_at                      TEXT NOT NULL,
    updated_at                      TEXT NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key, loop_key)
);

CREATE INDEX IF NOT EXISTS idx_manager_v4_review_broadcast_requests_status
    ON manager_v4_review_broadcast_requests(
        network, chain_id, protocol, scope_key, request_status
    );
