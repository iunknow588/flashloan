-- Downgrade the market observation's "optimal pool" column to a view.
--
-- The market observation used to pick the pool of a token pair by its
-- liquidity index.  The directional layer prices both directions of every
-- pool and ranks the pools of a `(pair, direction)` group by the price
-- quality of that direction.  The direction layer took over the closed
-- loop's primary order (the `T-closed.01` switch, `D-116`), so the
-- liquidity index is no longer the authority of "the pool of this pair".
--
-- This migration makes that authority **structural** instead of a
-- convention: `optimal_pool_id` becomes a **view column** derived from the
-- directional layer's rank 1, and the stored column moves to the base
-- table.  A query that reads the column keeps working and now reads the
-- directional layer's answer, so no consumer has to change.
--
-- The view is **writable**: the managers that publish the observation
-- (the candidate exchange manager and the closed loop manager's fixtures)
-- delete and insert rows under the table name, so the view carries
-- `INSTEAD OF` triggers that forward the writes to the base table.
--
-- **What the view answers.**  The group is the *direction* the row
-- measured -- `token_x_key_id -> token_y_key_key_id` -- and not the
-- canonical token pair.  A row names one direction, and the pool of that
-- direction is the pool that trades it, so the view looks the rank up by
-- the direction's input and output token.  A pool the direction layer did
-- not rank was never priced and is not a candidate of the group, so it
-- cannot be the primary.
--
-- **What the view does not change.**  The membership of the group stays in
-- `pool_ids_json`; the view only names the primary of the group.  The
-- abnormal observation keeps its own table, because an abnormal pair does
-- not take part in the closed loop's primary order at all.

-- The stored rows.  The table keeps its name in every query, so the rename
-- is invisible to the readers; only the object that answers the name
-- changes from a table to a view.
ALTER TABLE manager_market_observation_yz_pairs
    RENAME TO manager_market_observation_yz_pairs_base;

-- The view.  It publishes every column of the table, so a reader that
-- selects the table by name sees the same shape it saw before.
--
-- The primary of the direction is the pool the direction layer ranked
-- first.  The tie-break is the pool id, so the answer of the view is a
-- function of the stored rows alone: the same rows always produce the
-- same primary, and the view never depends on the order the rows happen
-- to be read in.
CREATE VIEW manager_market_observation_yz_pairs AS
SELECT
    yz.network,
    yz.chain_id,
    yz.protocol,
    yz.scope_key,
    yz.anchor_token_key_id,
    yz.direction_key,
    yz.token_x_key_id,
    yz.token_y_key_id,
    yz.has_direct_swap_pool,
    yz.direct_swap_pool_count,
    yz.pool_ids_json,
    (
        SELECT d.base_pool_id
        FROM manager_v4_directional_exchange_pools d
        WHERE d.scope_key = yz.scope_key
          AND d.input_token_key_id = yz.token_x_key_id
          AND d.output_token_key_id = yz.token_y_key_id
          AND d.pool_status = 'active'
          AND d.pair_direction_rank IS NOT NULL
        ORDER BY d.pair_direction_rank ASC, d.base_pool_id ASC
        LIMIT 1
    ) AS optimal_pool_id,
    yz.observation_empty_reason,
    yz.created_at,
    yz.updated_at
FROM manager_market_observation_yz_pairs_base yz;

-- The writers.  A view cannot be written to directly, so the writes that
-- the managers publish are forwarded to the base table.
--
-- The insert forwards the row as it was given.  The `optimal_pool_id` of
-- the inserted row is the value the writer computed; the view derives its
-- own answer from the direction layer, and the stored value is only the
-- record of what the writer published.
CREATE TRIGGER manager_market_observation_yz_pairs_insert
INSTEAD OF INSERT ON manager_market_observation_yz_pairs
BEGIN
    INSERT INTO manager_market_observation_yz_pairs_base (
        network,
        chain_id,
        protocol,
        scope_key,
        anchor_token_key_id,
        direction_key,
        token_x_key_id,
        token_y_key_id,
        has_direct_swap_pool,
        direct_swap_pool_count,
        pool_ids_json,
        optimal_pool_id,
        observation_empty_reason,
        created_at,
        updated_at
    ) VALUES (
        NEW.network,
        NEW.chain_id,
        NEW.protocol,
        NEW.scope_key,
        NEW.anchor_token_key_id,
        NEW.direction_key,
        NEW.token_x_key_id,
        NEW.token_y_key_id,
        NEW.has_direct_swap_pool,
        NEW.direct_swap_pool_count,
        NEW.pool_ids_json,
        NEW.optimal_pool_id,
        NEW.observation_empty_reason,
        NEW.created_at,
        NEW.updated_at
    );
END;

-- The delete forwards the scope the managers clear before they republish.
CREATE TRIGGER manager_market_observation_yz_pairs_delete
INSTEAD OF DELETE ON manager_market_observation_yz_pairs
BEGIN
    DELETE FROM manager_market_observation_yz_pairs_base
    WHERE network = OLD.network
      AND chain_id = OLD.chain_id
      AND protocol = OLD.protocol
      AND scope_key = OLD.scope_key
      AND anchor_token_key_id = OLD.anchor_token_key_id
      AND direction_key = OLD.direction_key;
END;

-- The update forwards the columns a writer may change in place.  The
-- primary of a direction is the direction layer's answer, so the stored
-- value follows the writer, and the view keeps deriving its own.
CREATE TRIGGER manager_market_observation_yz_pairs_update
INSTEAD OF UPDATE ON manager_market_observation_yz_pairs
BEGIN
    UPDATE manager_market_observation_yz_pairs_base
    SET network = NEW.network,
        chain_id = NEW.chain_id,
        protocol = NEW.protocol,
        scope_key = NEW.scope_key,
        anchor_token_key_id = NEW.anchor_token_key_id,
        direction_key = NEW.direction_key,
        token_x_key_id = NEW.token_x_key_id,
        token_y_key_id = NEW.token_y_key_id,
        has_direct_swap_pool = NEW.has_direct_swap_pool,
        direct_swap_pool_count = NEW.direct_swap_pool_count,
        pool_ids_json = NEW.pool_ids_json,
        optimal_pool_id = NEW.optimal_pool_id,
        observation_empty_reason = NEW.observation_empty_reason,
        created_at = NEW.created_at,
        updated_at = NEW.updated_at
    WHERE network = OLD.network
      AND chain_id = OLD.chain_id
      AND protocol = OLD.protocol
      AND scope_key = OLD.scope_key
      AND anchor_token_key_id = OLD.anchor_token_key_id
      AND direction_key = OLD.direction_key;
END;
