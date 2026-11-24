
-- Wire the closed loop to the directional pool layer.
--
-- The directional layer is a **projection** of the base pools, so this
-- migration adds no new fact: it only carries the projection's identity
-- into the rows that walk it, and publishes the one depth quantity the
-- projection did not carry yet.

-- The depth capacity of a base pool.
--
-- The depth capacity is the price-impact budget of the pool and does not
-- depend on the direction, so it belongs to the **base** pool and is
-- carried by both of its directional rows.  The closed loop folds it with
-- the same cumulative-rate fold it used before, and the fold arithmetic is
-- unchanged: the closed loop now reads the number from the directional row
-- instead of from the snapshot table.
ALTER TABLE manager_v4_directional_exchange_pools ADD COLUMN depth_capacity_raw TEXT;

-- The closed loop hop records **which** directional pool it walked.
--
-- `directional_pool_id` is the identity of the row; `hop_direction` is the
-- direction that row carries (0 or 1); `directional_scope_key` says which
-- projection the row came from, because the closed loop draws its first and
-- third hop from the core scope and its middle hop from the candidate
-- exchange scope; `directional_pool_rank` is the rank the row held inside
-- its (pair, direction), which is the ordering the selection uses.
ALTER TABLE manager_v4_closed_loop_hops ADD COLUMN directional_pool_id TEXT;
ALTER TABLE manager_v4_closed_loop_hops ADD COLUMN hop_direction INTEGER;
ALTER TABLE manager_v4_closed_loop_hops ADD COLUMN directional_scope_key TEXT;
ALTER TABLE manager_v4_closed_loop_hops ADD COLUMN directional_pool_rank INTEGER;

-- The closed loop body records the directional pools its hops walked.
--
-- The list is ordered by hop index and holds the `directional_pool_id` of
-- every hop.  It makes the loop body self-describing: the identity of the
-- projection the loop was built against can be read without joining the
-- hop table, and the equivalence check compares one field per loop.
ALTER TABLE manager_v4_closed_loops ADD COLUMN directional_pool_ids_json TEXT;

-- The closed loop library state records the directional layer it consumed.
--
-- The version and the checksum are the ones of the closed loop's **own**
-- scope (`core4`).  The candidate exchange scope, which supplies the
-- middle hop, is recorded in the library version and checksum maps that
-- the state table already carries, so no second pair of columns is needed.
ALTER TABLE manager_v4_closed_loop_library_state ADD COLUMN directional_pool_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE manager_v4_closed_loop_library_state ADD COLUMN directional_pool_checksum TEXT NOT NULL DEFAULT '';
