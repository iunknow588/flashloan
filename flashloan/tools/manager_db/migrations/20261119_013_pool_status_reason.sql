-- P0: a pool defect must be localizable to the pool itself.
--
-- The scope tables classify every pool with a `pool_status`, but they do
-- not record **why** a pool carries that status.  A pool that is rejected
-- as `invalid` looks identical to a pool that was rejected as `blocked`,
-- so an operator who sees a closed loop lose a hop cannot learn which
-- concrete condition disqualified the pool without re-deriving it from
-- the raw pool row.
--
-- `status_reason` carries the diagnosis of the pool itself: the quality
-- reasons that produced the status, joined in a stable order.  It is NULL
-- for an active pool, because an active pool has no defect.
--
-- The two scope tables are the pool Manager's own tables, and the column
-- is additive, so `ALTER TABLE ... ADD COLUMN` is enough and no row has
-- to be rewritten.

ALTER TABLE v4_exchange_pools_core4 ADD COLUMN status_reason TEXT;

ALTER TABLE v4_exchange_pools_candidate_exchange ADD COLUMN status_reason TEXT;
