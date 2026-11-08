CREATE TABLE IF NOT EXISTS token_core (
    network                 TEXT    NOT NULL,
    chain_id                INTEGER NOT NULL,
    token_key_id            TEXT    NOT NULL,
    v4_currency_key         TEXT    NOT NULL,
    token_address           TEXT    NOT NULL,
    token_symbol            TEXT    NOT NULL,
    token_name              TEXT,
    token_decimals          INTEGER NOT NULL CHECK (token_decimals BETWEEN 0 AND 36),
    token_kind              TEXT    NOT NULL,
    is_native               INTEGER NOT NULL DEFAULT 0 CHECK (is_native IN (0, 1)),
    is_wrapped_native       INTEGER NOT NULL DEFAULT 0 CHECK (is_wrapped_native IN (0, 1)),
    is_stable               INTEGER NOT NULL DEFAULT 0 CHECK (is_stable IN (0, 1)),
    admission_status        TEXT    NOT NULL CHECK (admission_status IN (
                                'admitted', 'blocked', 'candidate_only', 'pending_review'
                            )),
    admission_source        TEXT    NOT NULL,
    metadata_source         TEXT    NOT NULL,
    metadata_checksum       TEXT    NOT NULL,
    source_json             TEXT    NOT NULL DEFAULT '{}',
    first_seen_at           TEXT    NOT NULL,
    first_admitted_at       TEXT,
    updated_at              TEXT    NOT NULL,
    blocked_reason          TEXT,
    PRIMARY KEY (network, chain_id, token_key_id),
    UNIQUE (network, chain_id, v4_currency_key),
    UNIQUE (network, chain_id, token_address)
);

CREATE INDEX IF NOT EXISTS idx_token_core_symbol
    ON token_core(network, chain_id, token_symbol, admission_status);
CREATE INDEX IF NOT EXISTS idx_token_core_status
    ON token_core(network, chain_id, admission_status, token_kind);

CREATE TABLE IF NOT EXISTS token_stocks (
    network                 TEXT    NOT NULL,
    chain_id                INTEGER NOT NULL,
    token_key_id            TEXT    NOT NULL,
    v4_currency_key         TEXT    NOT NULL,
    token_address           TEXT    NOT NULL,
    token_symbol            TEXT    NOT NULL,
    token_name              TEXT,
    token_decimals          INTEGER NOT NULL CHECK (token_decimals BETWEEN 0 AND 36),
    token_kind              TEXT    NOT NULL,
    is_native               INTEGER NOT NULL DEFAULT 0 CHECK (is_native IN (0, 1)),
    is_wrapped_native       INTEGER NOT NULL DEFAULT 0 CHECK (is_wrapped_native IN (0, 1)),
    is_stable               INTEGER NOT NULL DEFAULT 0 CHECK (is_stable IN (0, 1)),
    admission_status        TEXT    NOT NULL CHECK (admission_status IN (
                                'admitted', 'blocked', 'candidate_only', 'pending_review'
                            )),
    admission_source        TEXT    NOT NULL,
    metadata_source         TEXT    NOT NULL,
    metadata_checksum       TEXT    NOT NULL,
    source_json             TEXT    NOT NULL DEFAULT '{}',
    first_seen_at           TEXT    NOT NULL,
    first_admitted_at       TEXT,
    updated_at              TEXT    NOT NULL,
    blocked_reason          TEXT,
    PRIMARY KEY (network, chain_id, token_key_id),
    UNIQUE (network, chain_id, v4_currency_key),
    UNIQUE (network, chain_id, token_address)
);

CREATE INDEX IF NOT EXISTS idx_token_stocks_symbol
    ON token_stocks(network, chain_id, token_symbol, admission_status);
CREATE INDEX IF NOT EXISTS idx_token_stocks_status
    ON token_stocks(network, chain_id, admission_status, token_kind);

CREATE TABLE IF NOT EXISTS token_extension (
    network                 TEXT    NOT NULL,
    chain_id                INTEGER NOT NULL,
    token_key_id            TEXT    NOT NULL,
    v4_currency_key         TEXT    NOT NULL,
    token_address           TEXT    NOT NULL,
    token_symbol            TEXT    NOT NULL,
    token_name              TEXT,
    token_decimals          INTEGER NOT NULL CHECK (token_decimals BETWEEN 0 AND 36),
    token_kind              TEXT    NOT NULL,
    is_native               INTEGER NOT NULL DEFAULT 0 CHECK (is_native IN (0, 1)),
    is_wrapped_native       INTEGER NOT NULL DEFAULT 0 CHECK (is_wrapped_native IN (0, 1)),
    is_stable               INTEGER NOT NULL DEFAULT 0 CHECK (is_stable IN (0, 1)),
    admission_status        TEXT    NOT NULL CHECK (admission_status IN (
                                'admitted', 'blocked', 'candidate_only', 'pending_review'
                            )),
    admission_source        TEXT    NOT NULL,
    metadata_source         TEXT    NOT NULL,
    metadata_checksum       TEXT    NOT NULL,
    source_json             TEXT    NOT NULL DEFAULT '{}',
    first_seen_at           TEXT    NOT NULL,
    first_admitted_at       TEXT,
    updated_at              TEXT    NOT NULL,
    blocked_reason          TEXT,
    PRIMARY KEY (network, chain_id, token_key_id),
    UNIQUE (network, chain_id, v4_currency_key),
    UNIQUE (network, chain_id, token_address)
);

CREATE INDEX IF NOT EXISTS idx_token_extension_symbol
    ON token_extension(network, chain_id, token_symbol, admission_status);
CREATE INDEX IF NOT EXISTS idx_token_extension_status
    ON token_extension(network, chain_id, admission_status, token_kind);

CREATE TABLE IF NOT EXISTS token_test (
    network                 TEXT    NOT NULL,
    chain_id                INTEGER NOT NULL,
    token_key_id            TEXT    NOT NULL,
    v4_currency_key         TEXT    NOT NULL,
    token_address           TEXT    NOT NULL,
    token_symbol            TEXT    NOT NULL,
    token_name              TEXT,
    token_decimals          INTEGER NOT NULL CHECK (token_decimals BETWEEN 0 AND 36),
    token_kind              TEXT    NOT NULL,
    is_native               INTEGER NOT NULL DEFAULT 0 CHECK (is_native IN (0, 1)),
    is_wrapped_native       INTEGER NOT NULL DEFAULT 0 CHECK (is_wrapped_native IN (0, 1)),
    is_stable               INTEGER NOT NULL DEFAULT 0 CHECK (is_stable IN (0, 1)),
    admission_status        TEXT    NOT NULL CHECK (admission_status IN (
                                'admitted', 'blocked', 'candidate_only', 'pending_review'
                            )),
    admission_source        TEXT    NOT NULL,
    metadata_source         TEXT    NOT NULL,
    metadata_checksum       TEXT    NOT NULL,
    source_json             TEXT    NOT NULL DEFAULT '{}',
    first_seen_at           TEXT    NOT NULL,
    first_admitted_at       TEXT,
    updated_at              TEXT    NOT NULL,
    blocked_reason          TEXT,
    PRIMARY KEY (network, chain_id, token_key_id),
    UNIQUE (network, chain_id, v4_currency_key),
    UNIQUE (network, chain_id, token_address)
);

CREATE INDEX IF NOT EXISTS idx_token_test_symbol
    ON token_test(network, chain_id, token_symbol, admission_status);
CREATE INDEX IF NOT EXISTS idx_token_test_status
    ON token_test(network, chain_id, admission_status, token_kind);

CREATE TABLE IF NOT EXISTS token_library_state (
    network             TEXT    NOT NULL,
    chain_id            INTEGER NOT NULL,
    library_key         TEXT    NOT NULL CHECK (library_key IN (
                            'core', 'stocks', 'extension', 'test'
                        )),
    library_version     INTEGER NOT NULL,
    token_count         INTEGER NOT NULL,
    admitted_count      INTEGER NOT NULL,
    metadata_checksum   TEXT    NOT NULL,
    source_checkpoint   TEXT,
    generated_at        TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, library_key)
);

CREATE TABLE IF NOT EXISTS token_admission_events (
    event_id            TEXT    NOT NULL,
    network             TEXT    NOT NULL,
    chain_id            INTEGER NOT NULL,
    token_key_id        TEXT    NOT NULL,
    library_key         TEXT    NOT NULL,
    previous_status     TEXT,
    new_status          TEXT    NOT NULL,
    reason              TEXT    NOT NULL,
    actor               TEXT    NOT NULL,
    evidence_json       TEXT    NOT NULL DEFAULT '{}',
    created_at          TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_token_admission_events_token
    ON token_admission_events(network, chain_id, token_key_id, created_at);

CREATE TABLE IF NOT EXISTS manager_write_requests (
    network             TEXT    NOT NULL,
    chain_id            INTEGER NOT NULL,
    operation           TEXT    NOT NULL,
    idempotency_key     TEXT    NOT NULL,
    request_id          TEXT    NOT NULL,
    request_digest      TEXT    NOT NULL,
    request_json        TEXT    NOT NULL,
    response_json       TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, operation, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_manager_write_requests_request_id
    ON manager_write_requests(network, chain_id, request_id);

CREATE VIEW IF NOT EXISTS token_library_catalog AS
SELECT 'core' AS library_key,
       network, chain_id, token_key_id, v4_currency_key, token_address,
       token_symbol, token_name, token_decimals, token_kind, is_native,
       is_wrapped_native, is_stable, admission_status, admission_source,
       metadata_source, metadata_checksum, source_json, first_seen_at,
       first_admitted_at, updated_at, blocked_reason
FROM token_core
UNION ALL
SELECT 'stocks' AS library_key,
       network, chain_id, token_key_id, v4_currency_key, token_address,
       token_symbol, token_name, token_decimals, token_kind, is_native,
       is_wrapped_native, is_stable, admission_status, admission_source,
       metadata_source, metadata_checksum, source_json, first_seen_at,
       first_admitted_at, updated_at, blocked_reason
FROM token_stocks
UNION ALL
SELECT 'extension' AS library_key,
       network, chain_id, token_key_id, v4_currency_key, token_address,
       token_symbol, token_name, token_decimals, token_kind, is_native,
       is_wrapped_native, is_stable, admission_status, admission_source,
       metadata_source, metadata_checksum, source_json, first_seen_at,
       first_admitted_at, updated_at, blocked_reason
FROM token_extension
UNION ALL
SELECT 'test' AS library_key,
       network, chain_id, token_key_id, v4_currency_key, token_address,
       token_symbol, token_name, token_decimals, token_kind, is_native,
       is_wrapped_native, is_stable, admission_status, admission_source,
       metadata_source, metadata_checksum, source_json, first_seen_at,
       first_admitted_at, updated_at, blocked_reason
FROM token_test;

CREATE TABLE IF NOT EXISTS pool_scope_config (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    scope_key                   TEXT    NOT NULL,
    pool_table                  TEXT    NOT NULL,
    library_keys_json           TEXT    NOT NULL,
    library_versions_json       TEXT    NOT NULL,
    library_checksums_json      TEXT    NOT NULL,
    filter_mode                 TEXT    NOT NULL CHECK (filter_mode IN ('edge_any', 'both_end')),
    excluded_token_key_ids_json TEXT    NOT NULL DEFAULT '[]',
    enabled                     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    materialized_checksum       TEXT,
    last_rebuilt_at             TEXT,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, scope_key),
    UNIQUE (network, chain_id, pool_table)
);

CREATE TABLE IF NOT EXISTS v4_exchange_pools_core4 (
    network                 TEXT    NOT NULL,
    chain_id                INTEGER NOT NULL,
    protocol                TEXT    NOT NULL DEFAULT 'uniswap_v4',
    pool_id                 TEXT    NOT NULL,
    pool_manager            TEXT    NOT NULL,
    pool_key_json           TEXT    NOT NULL,
    token0                  TEXT    NOT NULL,
    token1                  TEXT    NOT NULL,
    token0_key_id           TEXT    NOT NULL,
    token1_key_id           TEXT    NOT NULL,
    fee                     INTEGER NOT NULL,
    dynamic_fee             INTEGER NOT NULL DEFAULT 0,
    special_fee             INTEGER NOT NULL DEFAULT 0,
    tick_spacing            INTEGER NOT NULL,
    hooks                   TEXT    NOT NULL,
    source_block_number     INTEGER,
    source_tx_hash          TEXT,
    pool_status             TEXT    NOT NULL DEFAULT 'active' CHECK (pool_status IN (
                                'active', 'stale', 'invalid', 'identity_mismatch', 'blocked'
                            )),
    materialized_at         TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, pool_id)
);

CREATE INDEX IF NOT EXISTS idx_v4_pools_core4_tokens
    ON v4_exchange_pools_core4(
        network, chain_id, token0_key_id, token1_key_id, pool_status
    );
