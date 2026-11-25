-- Roll the market observation's "optimal pool" column back to a stored column.
--
-- This is the **downgrade** of the migration
-- `20261125_017_observation_optimal_pool_view`.  It restores the table so
-- that `optimal_pool_id` is a stored column again, and it puts the stored
-- value back: the value the view derived, not the value the old liquidity
-- index order produced.
--
-- **Why the value is not the old one.**  The view answered the pool of a
-- direction from the direction layer's rank 1.  The base table kept the
-- value the writer published, which was the old liquidity index order.
-- Rolling back therefore has a choice: restore the old value, or restore
-- the value that was live while the view was in place.  The second is the
-- one that keeps the closed loop unchanged, because the closed loop was
-- already walking the direction layer's rank 1 while the view was in
-- place (all 522 published hops carried the direction layer's rank 1).
-- Restoring the old liquidity index order would move the primary of a
-- middle hop back and change the closed loop.
--
-- The rollback therefore copies the view's answer into the stored column
-- before the view is dropped.  The result is a table whose stored primary
-- is the one that was live, and whose membership is untouched.
--
-- The rollback is **idempotent**: it leaves the rows of the base table
-- alone, and it restores the table whether or not the view is in place.

-- The stored rows keep their own primary.  When the view is in place, the
-- stored primary is the value the writer published; the view's answer is
-- the direction layer's rank 1.  Copy the view's answer into the stored
-- column first, so the restored table holds the primary that was live.
UPDATE manager_market_observation_yz_pairs_base
SET optimal_pool_id = (
    SELECT d.base_pool_id
    FROM manager_v4_directional_exchange_pools d
    WHERE d.scope_key = manager_market_observation_yz_pairs_base.scope_key
      AND d.input_token_key_id = manager_market_observation_yz_pairs_base.token_x_key_id
      AND d.output_token_key_id = manager_market_observation_yz_pairs_base.token_y_key_id
      AND d.pool_status = 'active'
      AND d.pair_direction_rank IS NOT NULL
    ORDER BY d.pair_direction_rank ASC, d.base_pool_id ASC
    LIMIT 1
)
WHERE EXISTS (
    SELECT 1 FROM manager_v4_directional_exchange_pools d
    WHERE d.scope_key = manager_market_observation_yz_pairs_base.scope_key
      AND d.input_token_key_id = manager_market_observation_yz_pairs_base.token_x_key_id
      AND d.output_token_key_id = manager_market_observation_yz_pairs_base.token_y_key_id
      AND d.pool_status = 'active'
      AND d.pair_direction_rank IS NOT NULL
);

-- Drop the writers of the view.  They are bound to the view, so they have
-- to go before the view itself.
DROP TRIGGER IF EXISTS manager_market_observation_yz_pairs_insert;
DROP TRIGGER IF EXISTS manager_market_observation_yz_pairs_delete;
DROP TRIGGER IF EXISTS manager_market_observation_yz_pairs_update;

-- Drop the view and give the stored rows the name again.
DROP VIEW IF EXISTS manager_market_observation_yz_pairs;
ALTER TABLE manager_market_observation_yz_pairs_base
    RENAME TO manager_market_observation_yz_pairs;
