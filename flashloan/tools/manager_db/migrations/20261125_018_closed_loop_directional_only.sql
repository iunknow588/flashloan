
-- The closed loop holds **directional** pools and nothing else.
--
-- The closed loop is folded on the direction layer, and the direction layer
-- row carries the base pool facts **inside itself**.  A hop therefore names
-- the directional pool it walks, and the base pool columns are gone from
-- the hop table: a consumer that needs the base facts resolves them through
-- the direction layer, so it can never reach a base pool without the
-- direction that goes with it.
--
-- The same rule applies to the loop body: `pool_ids_json` (the base pools of
-- the three hops) is dropped, because the loop already publishes
-- `directional_pool_ids_json` -- the very pools the hops walk.  The backup
-- list and the capacity binding pool become directional names as well.
--
-- The hop table is **rebuilt** rather than altered, because the directional
-- columns have to become NOT NULL: a hop that does not name a directional
-- pool cannot be traded, and the storage layer is where that has to hold.
-- The rows that do not name a directional pool are dropped -- they are
-- derived rows, and the closed loop manager republishes every hop of every
-- loop on the next materialization.

DROP INDEX IF EXISTS idx_manager_v4_closed_loop_hops_pool;

CREATE TABLE manager_v4_closed_loop_hops_directional (
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
    pool_selection_basis            TEXT    NOT NULL,
    optimization_id                 TEXT,
    temporary_override              INTEGER NOT NULL CHECK (temporary_override IN (0, 1)),
    created_at                      TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    directional_pool_id             TEXT    NOT NULL,
    hop_direction                   INTEGER NOT NULL CHECK (hop_direction IN (0, 1)),
    directional_scope_key           TEXT    NOT NULL,
    directional_pool_rank           INTEGER,
    PRIMARY KEY (network, chain_id, protocol, scope_key, loop_key, hop_index)
);

INSERT INTO manager_v4_closed_loop_hops_directional (
    network, chain_id, protocol, scope_key, loop_key, hop_index, hop_name,
    input_token_key_id, output_token_key_id, input_address, output_address,
    pool_selection_basis, optimization_id, temporary_override,
    created_at, updated_at,
    directional_pool_id, hop_direction, directional_scope_key, directional_pool_rank
)
SELECT
    network, chain_id, protocol, scope_key, loop_key, hop_index, hop_name,
    input_token_key_id, output_token_key_id, input_address, output_address,
    pool_selection_basis, optimization_id, temporary_override,
    created_at, updated_at,
    directional_pool_id, hop_direction, directional_scope_key, directional_pool_rank
FROM manager_v4_closed_loop_hops
WHERE COALESCE(directional_pool_id, '') <> ''
  AND hop_direction IS NOT NULL
  AND COALESCE(directional_scope_key, '') <> '';

DROP TABLE manager_v4_closed_loop_hops;

ALTER TABLE manager_v4_closed_loop_hops_directional
    RENAME TO manager_v4_closed_loop_hops;

CREATE INDEX IF NOT EXISTS idx_manager_v4_closed_loop_hops_directional
    ON manager_v4_closed_loop_hops(
        network, chain_id, protocol, scope_key, directional_pool_id
    );

-- The loop body: the base pool list is gone, the directional one stays.
ALTER TABLE manager_v4_closed_loops DROP COLUMN pool_ids_json;
ALTER TABLE manager_v4_closed_loops
    RENAME COLUMN backup_pool_ids_json TO backup_directional_pool_ids_json;
ALTER TABLE manager_v4_closed_loops
    RENAME COLUMN single_tick_capacity_binding_pool_id
    TO single_tick_capacity_binding_directional_pool_id;

-- The optimization rows name the pools of the hop, so they name the
-- directional pools as well.
ALTER TABLE manager_v4_closed_loop_optimizations
    RENAME COLUMN original_pool_id TO original_directional_pool_id;
ALTER TABLE manager_v4_closed_loop_optimizations
    RENAME COLUMN optimized_pool_id TO optimized_directional_pool_id;
