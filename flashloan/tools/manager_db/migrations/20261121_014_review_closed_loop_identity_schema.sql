-- P0-9: the closed-loop identity of a review record.
--
-- The Review Manager owns `manager_v4_reviews`.  It is the only review
-- source that can prove a review belongs to the current closed-loop
-- library, because it carries the upstream versions and checksums the
-- review was measured against.
--
-- The **identity** of a review was not part of that proof.  The identity is
-- the closed-loop key the runtime published for the route it measured, and
-- the **diagnosis** of that identity:
--
--   closed_loop_identity_status        whether the route matched the
--                                       closed-loop catalog ('matched',
--                                       'mismatch', ...)
--   closed_loop_pool_identity_status   whether every pool the route priced
--                                       is a pool the closed-loop Manager
--                                       published for that hop
--   identity_mismatch                  the diagnosis of a mismatch, naming
--                                       the pool that broke it
--   closed_loop_repair_target          the repair target the identity
--                                       mismatch names
--
-- Without these columns a consumer of the Manager library could not tell
-- three very different situations apart:
--
--   1. the route matched the closed-loop catalog,
--   2. the route priced pools the closed-loop Manager does not publish,
--   3. the closed-loop catalog was not loaded at all.
--
-- All three looked like the same thing -- a review row with no identity
-- information -- so the reader had to re-derive the identity outside the
-- Manager.  That is exactly the reading the Manager owns: the identity is
-- computed by the closed-loop kernel against the catalog the closed-loop
-- Manager publishes, and only the Manager can persist and publish it.
--
-- The columns are nullable on purpose.  A review measured against a
-- catalog that published no loop has **no** identity; the absence is a
-- coverage gap, not a mismatch, so it is stored as NULL instead of being
-- recorded as a negative verdict.

ALTER TABLE manager_v4_reviews
    ADD COLUMN closed_loop_identity_status TEXT;

ALTER TABLE manager_v4_reviews
    ADD COLUMN closed_loop_pool_identity_status TEXT;

ALTER TABLE manager_v4_reviews
    ADD COLUMN identity_mismatch TEXT;

ALTER TABLE manager_v4_reviews
    ADD COLUMN closed_loop_repair_target TEXT;
