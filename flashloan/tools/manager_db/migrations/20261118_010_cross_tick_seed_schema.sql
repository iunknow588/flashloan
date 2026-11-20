-- P0: the deterministic cross-tick seed of the closed loop.
--
-- The cross-tick review is the **supplement** of the single-tick mode, and
-- its initial amount is the single-tick best amount, never a magic
-- constant.  The kernel therefore needs three numbers per loop before it
-- can decide whether the cross-tick ladder may start at all:
--
--   borrow_token_usdg_value_raw            the value of one whole borrow
--                                          token in raw USDG units, and
--   borrow_token_decimals                   the decimals of that token
--   cross_tick_review_threshold_usdg_raw    the shared economic threshold
--   cross_tick_capacity_anchor_raw          the depth ceiling of the
--                                          cross-tick ladder, folded into
--                                          the anchor token
--
-- The value of one whole borrow token is what makes the threshold
-- expressible without an external price feed: every pool token has a USDG
-- conversion route by convention.
--
-- Every column is nullable except the threshold.  A NULL valuation is a
-- coverage gap of the pool index, never a zero valuation: a zero would
-- silently make every threshold comparison false.

ALTER TABLE manager_v4_closed_loops
    ADD COLUMN borrow_token_usdg_value_raw TEXT;

ALTER TABLE manager_v4_closed_loops
    ADD COLUMN borrow_token_decimals INTEGER;

ALTER TABLE manager_v4_closed_loops
    ADD COLUMN cross_tick_review_threshold_usdg_raw TEXT;

ALTER TABLE manager_v4_closed_loops
    ADD COLUMN cross_tick_capacity_anchor_raw TEXT;
