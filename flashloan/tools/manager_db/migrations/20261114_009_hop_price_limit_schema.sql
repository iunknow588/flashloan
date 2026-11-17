-- P0-8: the per-hop price limit of the reviewed route.
--
-- Version 1 may only execute inside a single tick range per hop, so the
-- transaction must carry the price limit of the tick range the review
-- proved.  The arbiter of the mainnet deployment takes no price-limit
-- argument, so the limit has to travel with the **review** and with the
-- **broadcast request**: the executor builds the swap calldata from it, and
-- a payload without a limit is refused instead of silently falling back to
-- the global price bound.
--
-- The column holds the three hop limits in route order:
--
--   hop_sqrt_price_limits_json   a JSON array of three decimal strings,
--                                 the limit of hop 1, 2 and 3
--
-- The value is nullable on purpose.  A review that could not prove the
-- limits produces a NULL, and a NULL is a coverage gap that blocks the
-- broadcast request instead of being recorded as a measured zero.

ALTER TABLE manager_v4_reviews
    ADD COLUMN hop_sqrt_price_limits_json TEXT;

ALTER TABLE manager_v4_review_broadcast_requests
    ADD COLUMN hop_sqrt_price_limits_json TEXT;

ALTER TABLE manager_cow_execution_attempts
    ADD COLUMN hop_sqrt_price_limits_json TEXT;
