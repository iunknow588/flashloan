ALTER TABLE v4_exchange_pools_core4 ADD COLUMN sqrt_price_x96 TEXT;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN tick INTEGER;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN liquidity TEXT;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN liquidity_index REAL;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN price REAL;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN depth_status TEXT;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN liquidity_read_at TEXT;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN liquidity_block_number INTEGER;
ALTER TABLE v4_exchange_pools_core4 ADD COLUMN evidence_source TEXT;

CREATE TABLE IF NOT EXISTS manager_dex_candidate_library_state (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    candidate_version           INTEGER NOT NULL,
    materialized_checksum       TEXT    NOT NULL,
    pool_scope_checksum         TEXT    NOT NULL,
    pool_library_versions_json  TEXT    NOT NULL,
    pool_library_checksums_json TEXT    NOT NULL,
    library_versions_json       TEXT    NOT NULL,
    library_checksums_json      TEXT    NOT NULL,
    anchor_token_count          INTEGER NOT NULL,
    candidate_token_count       INTEGER NOT NULL,
    eligible_pool_count         INTEGER NOT NULL,
    recommendation_count        INTEGER NOT NULL,
    abnormal_pool_count         INTEGER NOT NULL,
    generated_at                TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key)
);

CREATE TABLE IF NOT EXISTS manager_dex_candidate_anchor_tokens (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    token_key_id                TEXT    NOT NULL,
    token_role                  TEXT    NOT NULL CHECK (token_role = 'yc_zc'),
    anchor_library_key          TEXT    NOT NULL,
    token_library_key           TEXT    NOT NULL,
    token_symbol                TEXT    NOT NULL,
    pool_count                  INTEGER NOT NULL,
    yc_primary_pool_id          TEXT,
    zc_primary_pool_id          TEXT,
    yc_backup_pool_ids_json     TEXT    NOT NULL DEFAULT '[]',
    zc_backup_pool_ids_json     TEXT    NOT NULL DEFAULT '[]',
    yc_zc_same_set              INTEGER NOT NULL CHECK (yc_zc_same_set IN (0, 1)),
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, anchor_token_key_id, token_key_id
    )
);

CREATE INDEX IF NOT EXISTS idx_manager_dex_candidate_anchor_tokens_token
    ON manager_dex_candidate_anchor_tokens(
        network, chain_id, protocol, scope_key, token_key_id
    );

CREATE TABLE IF NOT EXISTS manager_dex_candidate_pool_pairs (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    candidate_token_key_id      TEXT    NOT NULL,
    pair_key_id                 TEXT    NOT NULL,
    first_direction_key         TEXT    NOT NULL,
    third_direction_key         TEXT    NOT NULL,
    pool_count                  INTEGER NOT NULL,
    first_primary_pool_id       TEXT,
    third_primary_pool_id       TEXT,
    first_backup_pool_ids_json  TEXT    NOT NULL DEFAULT '[]',
    third_backup_pool_ids_json  TEXT    NOT NULL DEFAULT '[]',
    selection_basis             TEXT    NOT NULL,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, candidate_token_key_id
    )
);

CREATE TABLE IF NOT EXISTS manager_dex_candidate_pools (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    candidate_token_key_id      TEXT    NOT NULL,
    pair_key_id                 TEXT    NOT NULL,
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
    is_first_hop_recommended    INTEGER NOT NULL CHECK (is_first_hop_recommended IN (0, 1)),
    is_third_hop_recommended     INTEGER NOT NULL CHECK (is_third_hop_recommended IN (0, 1)),
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, candidate_token_key_id, pool_id
    )
);

CREATE INDEX IF NOT EXISTS idx_manager_dex_candidate_pools_token_pair
    ON manager_dex_candidate_pools(
        network, chain_id, protocol, scope_key, pair_key_id, pool_id
    );

CREATE TABLE IF NOT EXISTS manager_dex_candidate_pool_recommendations (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    candidate_token_key_id      TEXT    NOT NULL,
    pair_key_id                 TEXT    NOT NULL,
    direction_key               TEXT    NOT NULL,
    hop                         TEXT    NOT NULL CHECK (hop IN ('first', 'third')),
    rank                        INTEGER NOT NULL CHECK (rank > 0),
    pool_id                     TEXT    NOT NULL,
    depth_score                 REAL,
    fee                         INTEGER NOT NULL,
    selection_basis             TEXT    NOT NULL,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, candidate_token_key_id, hop, rank
    )
);

CREATE TABLE IF NOT EXISTS manager_dex_candidate_abnormal_anchor_tokens (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    token_key_id                TEXT    NOT NULL,
    anchor_library_key          TEXT    NOT NULL,
    token_library_key           TEXT    NOT NULL,
    token_symbol                TEXT    NOT NULL,
    pool_count                  INTEGER NOT NULL,
    reasons_json                TEXT    NOT NULL,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, anchor_token_key_id, token_key_id
    )
);

CREATE TABLE IF NOT EXISTS manager_dex_candidate_abnormal_pool_pairs (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    candidate_token_key_id      TEXT    NOT NULL,
    pair_key_id                 TEXT    NOT NULL,
    pool_count                  INTEGER NOT NULL,
    reasons_json                TEXT    NOT NULL,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, candidate_token_key_id
    )
);

CREATE TABLE IF NOT EXISTS manager_dex_candidate_abnormal_pools (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL,
    scope_key                   TEXT    NOT NULL,
    anchor_token_key_id         TEXT    NOT NULL,
    candidate_token_key_id      TEXT    NOT NULL,
    pair_key_id                 TEXT    NOT NULL,
    pool_id                     TEXT    NOT NULL,
    pool_status                 TEXT    NOT NULL,
    pool_key_json               TEXT    NOT NULL,
    token0_key_id               TEXT    NOT NULL,
    token1_key_id               TEXT    NOT NULL,
    fee                         INTEGER NOT NULL,
    tick_spacing                INTEGER NOT NULL,
    hooks                       TEXT    NOT NULL,
    reason                      TEXT    NOT NULL,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, candidate_token_key_id, pool_id
    )
);
