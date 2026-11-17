-- P0-8: the folded single-tick capacity of a closed loop.
--
-- The closed-loop layer already computes the **single-tick capacity** of a
-- path: the capacity of every hop is folded into the anchor token with the
-- cumulative rate of the hops before it, and the smallest folded capacity
-- is the capacity of the whole loop.  That value is the `M_cap` of the
-- single-tick design, and it is the only number that may bound the input
-- amount of the route.
--
-- Until now the fold was used as a *filter* only and then thrown away, so
-- the runtime had to guess a capacity of its own.  The guess was a generic
-- pool-depth share, which is **not** a single-tick bound: it can be larger
-- than the tick range allows, and it is denominated in the wrong token.
--
-- This migration publishes the fold on the closed-loop row:
--
--   single_tick_capacity_anchor_raw       the folded capacity, in anchor
--                                          token raw units
--   single_tick_capacity_binding_pool_id   the hop whose folded capacity is
--                                          the smallest
--
-- Both columns are nullable on purpose.  A loop whose hops do not all
-- publish a usable tick state has **no** folded capacity; the absence is a
-- coverage gap of the pool index, not a market conclusion, so it is stored
-- as NULL instead of being recorded as a measured zero.

ALTER TABLE manager_v4_closed_loops
    ADD COLUMN single_tick_capacity_anchor_raw TEXT;

ALTER TABLE manager_v4_closed_loops
    ADD COLUMN single_tick_capacity_binding_pool_id TEXT;
-- The review row and the broadcast request carry the same bound forward.
--
-- The review gate needs the bound to prove that the **final** input amount
-- still fits inside the single-tick capacity, and the broadcast request
-- needs it so the executor can refuse a payload whose amount is larger than
-- the capacity the review proved.
ALTER TABLE manager_v4_reviews
    ADD COLUMN single_tick_capacity_anchor_raw TEXT;

ALTER TABLE manager_v4_reviews
    ADD COLUMN single_tick_capacity_binding_pool_id TEXT;

ALTER TABLE manager_v4_review_broadcast_requests
    ADD COLUMN single_tick_capacity_anchor_raw TEXT;

ALTER TABLE manager_v4_review_broadcast_requests
    ADD COLUMN single_tick_capacity_binding_pool_id TEXT;
-- The broadcast attempt carries the bound forward as well.
--
-- The attempt is the last durable record of what was actually sent. The
-- bound travels with it so an operator can prove, after the fact, that the
-- amount that was broadcast was inside the capacity the review proved.
ALTER TABLE manager_cow_execution_attempts
    ADD COLUMN single_tick_capacity_anchor_raw TEXT;

ALTER TABLE manager_cow_execution_attempts
    ADD COLUMN single_tick_capacity_binding_pool_id TEXT;