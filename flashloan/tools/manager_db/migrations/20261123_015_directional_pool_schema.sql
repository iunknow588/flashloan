-- Directional exchange pool layer.
--
-- Every row is one base V4 pool projected onto one of its two swap
-- directions.  Direction 0 trades the higher address token into the lower
-- one (token1 -> token0), direction 1 trades the lower address token into
-- the higher one (token0 -> token1).
--
-- The direction is a property of the pool, not of a route, so the same base
-- pool appears exactly twice and the two rows differ only in the
-- direction dependent fields.  The layer adds **no new on-chain fact**: the
-- identity columns come from the base pool table and the quantities come
-- from the existing liquidity snapshot table.

CREATE TABLE IF NOT EXISTS manager_v4_directional_exchange_pools (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL DEFAULT 'uniswap_v4',
    scope_key                   TEXT    NOT NULL,

    -- Base pool identity and direction.
    base_pool_id                TEXT    NOT NULL,
    direction                   INTEGER NOT NULL CHECK (direction IN (0, 1)),
    directional_pool_id         TEXT    NOT NULL,

    -- The fixed on-chain facts of the base pool.  This layer adds none.
    pool_manager                TEXT    NOT NULL,
    pool_key_json               TEXT    NOT NULL,
    token0                      TEXT    NOT NULL,
    token1                      TEXT    NOT NULL,
    token0_key_id               TEXT    NOT NULL,
    token1_key_id               TEXT    NOT NULL,
    fee                         INTEGER NOT NULL,
    tick_spacing                INTEGER NOT NULL,
    hooks                       TEXT    NOT NULL,

    -- Derived direction facts.  They are pure functions of the token order,
    -- so they cannot contradict the direction column.
    pair_key_id                 TEXT    NOT NULL,
    input_token_key_id          TEXT    NOT NULL,
    output_token_key_id         TEXT    NOT NULL,
    zero_for_one                INTEGER NOT NULL CHECK (zero_for_one IN (0, 1)),

    -- The dynamic state of the snapshot carrier.  Nothing is re-collected.
    sqrt_price_x96              TEXT,
    tick                        INTEGER,
    liquidity                   TEXT,
    price                       REAL,
    depth_status                TEXT,
    liquidity_read_at           TEXT,
    liquidity_block_number      INTEGER,
    evidence_source             TEXT,

    -- Direction capacity: how much of the input token this direction can
    -- carry, in raw units.  Direction 0 takes single_tick_max_token1_raw
    -- (the input is token1), direction 1 takes single_tick_max_token0_raw.
    -- NULL means "not measured" and '0' means "measured and empty"; the
    -- two are different facts and must never be mixed.
    input_capacity_raw          TEXT,
    input_capacity_measured     INTEGER NOT NULL DEFAULT 0 CHECK (input_capacity_measured IN (0, 1)),

    -- Direction price quality: the exact output of a reference trade inside
    -- the current tick range, decimal normalized.  This is the ordering
    -- quantity of the main sequence.
    directional_output          REAL,
    directional_output_measured INTEGER NOT NULL DEFAULT 0 CHECK (directional_output_measured IN (0, 1)),

    -- The 1-based rank inside (pair_key_id, direction).  Unmeasured rows
    -- have no rank.
    pair_direction_rank         INTEGER CHECK (pair_direction_rank IS NULL OR pair_direction_rank > 0),

    pool_status                 TEXT    NOT NULL DEFAULT 'active' CHECK (pool_status IN (
                                 'active', 'stale', 'invalid', 'identity_mismatch', 'blocked'
                             )),
    pool_status_reason          TEXT,
    source_block_number         INTEGER,
    source_tx_hash              TEXT,

    materialized_at             TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,

    PRIMARY KEY (network, chain_id, protocol, scope_key, base_pool_id, direction)
);

-- The closed loop selection path: take the pool by pair, direction and rank.
CREATE INDEX IF NOT EXISTS idx_v4_directional_pools_pair
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, pair_key_id, direction, pair_direction_rank
    );

-- The "ordered by its own direction" view: direction 0 by the amount of
-- the higher address token, direction 1 by the amount of the lower address
-- token.  This view serves display and diagnostics only; selection always
-- uses the rank inside (pair, direction).
CREATE INDEX IF NOT EXISTS idx_v4_directional_pools_direction
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, direction, input_capacity_raw
    );

CREATE INDEX IF NOT EXISTS idx_v4_directional_pools_base
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, base_pool_id
    );

-- The uniqueness of the directional pool identity.  SQLite requires a
-- parent key to be UNIQUE for a foreign key, so this index is the condition
-- for turning directional_pool_id into a hard foreign key later.
CREATE UNIQUE INDEX IF NOT EXISTS ux_v4_directional_pools_id
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, directional_pool_id
    );

-- The library state of the directional pool layer.  Every layer publishes a
-- version and a checksum to the layer below it, which uses them to decide
-- whether it has to rebuild.
CREATE TABLE IF NOT EXISTS manager_v4_directional_pool_library_state (
    network                    TEXT    NOT NULL,
    chain_id                   INTEGER NOT NULL,
    protocol                   TEXT    NOT NULL,
    scope_key                  TEXT    NOT NULL,
    directional_pool_version   INTEGER NOT NULL,
    materialized_checksum      TEXT    NOT NULL,
    pool_scope_version         INTEGER NOT NULL,
    pool_scope_checksum        TEXT    NOT NULL,
    snapshot_checksum          TEXT    NOT NULL,
    library_versions_json      TEXT    NOT NULL,
    library_checksums_json     TEXT    NOT NULL,
    base_pool_count            INTEGER NOT NULL,
    directional_pool_count     INTEGER NOT NULL,
    direction0_count           INTEGER NOT NULL,
    direction1_count           INTEGER NOT NULL,
    eligible_pair_count        INTEGER NOT NULL,
    measured_capacity_count    INTEGER NOT NULL,
    measured_output_count      INTEGER NOT NULL,
    excluded_pool_count        INTEGER NOT NULL,
    generated_at               TEXT    NOT NULL,
    updated_at                 TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key)
);
