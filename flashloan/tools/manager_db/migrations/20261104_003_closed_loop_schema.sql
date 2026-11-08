CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_library_state (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    closed_loop_version             INTEGER NOT NULL,
    materialized_checksum           TEXT    NOT NULL,
    candidate_edge_version          INTEGER NOT NULL,
    candidate_edge_checksum         TEXT    NOT NULL,
    observation_version             INTEGER NOT NULL,
    observation_checksum            TEXT    NOT NULL,
    pool_scope_checksum             TEXT    NOT NULL,
    library_versions_json           TEXT    NOT NULL,
    library_checksums_json          TEXT    NOT NULL,
    anchor_count                    INTEGER NOT NULL,
    loop_count                      INTEGER NOT NULL,
    hop_count                       INTEGER NOT NULL,
    optimization_count              INTEGER NOT NULL,
    skipped_token_count             INTEGER NOT NULL,
    consumers_need_rebuild          INTEGER NOT NULL CHECK (consumers_need_rebuild IN (0, 1)),
    generated_at                    TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key)
);

CREATE TABLE IF NOT EXISTS manager_v4_closed_loops (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    route_key_id                    TEXT    NOT NULL,
    anchor_token_key_id             TEXT    NOT NULL,
    anchor_address                  TEXT    NOT NULL,
    anchor_symbol                   TEXT    NOT NULL,
    token_y_key_id                  TEXT    NOT NULL,
    token_z_key_id                  TEXT    NOT NULL,
    path_text                       TEXT    NOT NULL,
    path_addresses_json             TEXT    NOT NULL,
    path_symbols_json               TEXT    NOT NULL,
    pool_path_key                   TEXT    NOT NULL,
    pool_ids_json                   TEXT    NOT NULL,
    backup_pool_ids_json            TEXT    NOT NULL,
    hop_candidate_counts_json       TEXT    NOT NULL,
    hop_candidate_combination_count INTEGER NOT NULL,
    recommendation_basis            TEXT    NOT NULL,
    recommended_first_priority      INTEGER NOT NULL CHECK (recommended_first_priority IN (0, 1)),
    candidate_edge_version          INTEGER NOT NULL,
    candidate_edge_checksum         TEXT    NOT NULL,
    observation_version             INTEGER NOT NULL,
    observation_checksum            TEXT    NOT NULL,
    status                          TEXT    NOT NULL CHECK (status IN ('active', 'blocked', 'retired')),
    source                          TEXT    NOT NULL,
    created_at                      TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key, loop_key)
);

CREATE INDEX IF NOT EXISTS idx_manager_v4_closed_loops_anchor
    ON manager_v4_closed_loops(
        network, chain_id, protocol, scope_key, anchor_token_key_id
    );

CREATE INDEX IF NOT EXISTS idx_manager_v4_closed_loops_route
    ON manager_v4_closed_loops(
        network, chain_id, protocol, scope_key, route_key_id
    );

CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_hops (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    hop_index                       INTEGER NOT NULL CHECK (hop_index BETWEEN 1 AND 3),
    hop_name                        TEXT    NOT NULL CHECK (hop_name IN ('yc', 'y-z', 'zc')),
    input_token_key_id              TEXT    NOT NULL,
    output_token_key_id             TEXT    NOT NULL,
    input_address                   TEXT    NOT NULL,
    output_address                  TEXT    NOT NULL,
    pool_id                         TEXT    NOT NULL,
    base_pool_id                    TEXT    NOT NULL,
    pool_selection_basis            TEXT    NOT NULL,
    optimization_id                 TEXT,
    temporary_override              INTEGER NOT NULL CHECK (temporary_override IN (0, 1)),
    created_at                      TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key, loop_key, hop_index)
);

CREATE INDEX IF NOT EXISTS idx_manager_v4_closed_loop_hops_pool
    ON manager_v4_closed_loop_hops(
        network, chain_id, protocol, scope_key, pool_id
    );

CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_optimizations (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    hop_index                       INTEGER NOT NULL CHECK (hop_index BETWEEN 1 AND 3),
    optimization_id                 TEXT    NOT NULL,
    original_pool_id                TEXT    NOT NULL,
    optimized_pool_id               TEXT    NOT NULL,
    optimization_basis              TEXT    NOT NULL,
    expected_price_improvement      REAL,
    status                          TEXT    NOT NULL CHECK (
                                        status IN ('candidate', 'temporary', 'accepted', 'expired')
                                    ),
    expires_at                      TEXT,
    created_at                      TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key, loop_key, hop_index, optimization_id
    )
);

CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_runtime_state (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    runtime_status                  TEXT    NOT NULL CHECK (
                                        runtime_status IN (
                                            'ready_for_review', 'review_pending', 'blocked',
                                            'cooldown', 'retired'
                                        )
                                    ),
    status_reason                   TEXT,
    review_generation               INTEGER NOT NULL,
    review_required                 INTEGER NOT NULL CHECK (review_required IN (0, 1)),
    last_rebuilt_at                 TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key, loop_key)
);

CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_skipped_tokens (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    anchor_token_key_id             TEXT    NOT NULL,
    token_key_id                    TEXT    NOT NULL,
    token_symbol                    TEXT    NOT NULL,
    candidate_role                  TEXT    NOT NULL CHECK (candidate_role IN ('first', 'third')),
    reason                          TEXT    NOT NULL CHECK (
                                        reason IN (
                                            'no_completing_middle_direction',
                                            'no_completing_first_hop',
                                            'no_completing_third_hop',
                                            'middle_pool_missing',
                                            'anchor_loop_not_allowed'
                                        )
                                    ),
    created_at                      TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, token_key_id, candidate_role, reason
    )
);
