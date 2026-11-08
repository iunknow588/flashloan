CREATE TABLE IF NOT EXISTS v4_exchange_pools_candidate_exchange (
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
    sqrt_price_x96          TEXT,
    tick                    INTEGER,
    liquidity               TEXT,
    liquidity_index         REAL,
    price                   REAL,
    depth_status            TEXT,
    liquidity_read_at       TEXT,
    liquidity_block_number  INTEGER,
    evidence_source         TEXT,
    source_block_number     INTEGER,
    source_tx_hash          TEXT,
    pool_status             TEXT    NOT NULL DEFAULT 'active' CHECK (pool_status IN (
                                'active', 'stale', 'invalid', 'identity_mismatch', 'blocked'
                            )),
    materialized_at         TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, pool_id)
);

CREATE INDEX IF NOT EXISTS idx_v4_pools_candidate_exchange_tokens
    ON v4_exchange_pools_candidate_exchange(
        network, chain_id, token0_key_id, token1_key_id, pool_status
    );

CREATE TABLE IF NOT EXISTS manager_market_observation_library_state (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    observation_version             INTEGER NOT NULL,
    materialized_checksum           TEXT    NOT NULL,
    candidate_edge_version          INTEGER NOT NULL,
    candidate_edge_checksum         TEXT    NOT NULL,
    pool_scope_checksum             TEXT    NOT NULL,
    library_versions_json           TEXT    NOT NULL,
    library_checksums_json          TEXT    NOT NULL,
    anchor_count                    INTEGER NOT NULL,
    token_count                     INTEGER NOT NULL,
    direction_count                 INTEGER NOT NULL,
    empty_direction_count           INTEGER NOT NULL,
    pool_count                      INTEGER NOT NULL,
    abnormal_direction_count        INTEGER NOT NULL,
    abnormal_pool_count             INTEGER NOT NULL,
    generated_at                    TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key)
);

CREATE TABLE IF NOT EXISTS manager_market_observation_yz_pairs (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    direction_key               TEXT    NOT NULL,
    token_x_key_id              TEXT    NOT NULL,
    token_y_key_id              TEXT    NOT NULL,
    has_direct_swap_pool        INTEGER NOT NULL CHECK (has_direct_swap_pool IN (0, 1)),
    direct_swap_pool_count      INTEGER NOT NULL,
    pool_ids_json               TEXT    NOT NULL DEFAULT '[]',
    optimal_pool_id             TEXT,
    observation_empty_reason    TEXT,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, anchor_token_key_id, direction_key
    )
);

CREATE INDEX IF NOT EXISTS idx_manager_market_observation_yz_edge
    ON manager_market_observation_yz_pairs(
        network, chain_id, protocol, scope_key,
        token_x_key_id, token_y_key_id, direct_swap_pool_count
    );

CREATE TABLE IF NOT EXISTS manager_market_observation_intermediate_pools (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    pool_id                     TEXT    NOT NULL,
    pool_manager                TEXT    NOT NULL,
    pool_key_json               TEXT    NOT NULL,
    token0                      TEXT    NOT NULL,
    token1                      TEXT    NOT NULL,
    token0_key_id               TEXT    NOT NULL,
    token1_key_id               TEXT    NOT NULL,
    fee                         INTEGER NOT NULL,
    tick_spacing                INTEGER NOT NULL,
    hooks                       TEXT    NOT NULL,
    sqrt_price_x96              TEXT,
    tick                        INTEGER,
    liquidity                   TEXT,
    liquidity_index             REAL,
    price                       REAL,
    depth_status                TEXT,
    liquidity_read_at           TEXT,
    liquidity_block_number      INTEGER,
    evidence_source             TEXT,
    selection_rank              INTEGER NOT NULL,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, anchor_token_key_id, pool_id
    )
);

CREATE INDEX IF NOT EXISTS idx_manager_market_observation_pool_pair
    ON manager_market_observation_intermediate_pools(
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, token0_key_id, token1_key_id
    );

CREATE TABLE IF NOT EXISTS manager_market_observation_abnormal_yz_pairs (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    direction_key               TEXT    NOT NULL,
    token_x_key_id              TEXT    NOT NULL,
    token_y_key_id              TEXT    NOT NULL,
    abnormal_pool_count         INTEGER NOT NULL,
    pool_ids_json               TEXT    NOT NULL DEFAULT '[]',
    reasons_json                TEXT    NOT NULL DEFAULT '[]',
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, anchor_token_key_id, direction_key
    )
);

CREATE TABLE IF NOT EXISTS manager_market_observation_abnormal_intermediate_pools (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    pool_id                     TEXT    NOT NULL,
    pool_status                 TEXT    NOT NULL,
    pool_key_json               TEXT    NOT NULL,
    token0_key_id               TEXT    NOT NULL,
    token1_key_id               TEXT    NOT NULL,
    fee                         INTEGER NOT NULL,
    tick_spacing                INTEGER NOT NULL,
    hooks                       TEXT    NOT NULL,
    reasons_json                TEXT    NOT NULL DEFAULT '[]',
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, anchor_token_key_id, pool_id
    )
);

CREATE TABLE IF NOT EXISTS manager_market_observation_liquidity_snapshots (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    pool_id                     TEXT    NOT NULL,
    has_liquidity               INTEGER,
    liquidity                   TEXT,
    liquidity_index             REAL,
    sqrt_price_x96              TEXT,
    price                       REAL,
    tick                        INTEGER,
    depth_status                TEXT,
    liquidity_read_at           TEXT,
    liquidity_block_number      INTEGER,
    evidence_source             TEXT,
    observed_at                 TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, anchor_token_key_id, pool_id
    )
);
