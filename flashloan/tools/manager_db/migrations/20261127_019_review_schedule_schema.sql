-- The closed loop carries the moment of its **last** review and the moment
-- its **next** review is due.
--
-- The review of a loop ages: a measurement taken ten minutes ago describes
-- a market that has traded since, so a loop has to be measured again on a
-- schedule.  The schedule belongs to the **loop**, not to the review row, so
-- it survives a rebuild of the catalog: a rebuild publishes the topology
-- again, it does not reset the review clock of a loop that was measured.
--
-- `last_reviewed_at` is the moment the newest review of the loop was taken;
-- `next_review_at` is the moment that measurement goes stale and the loop
-- has to be measured again.  A loop that was never measured carries NULL in
-- both: it has no schedule yet, and `review_required` says so.
ALTER TABLE manager_v4_closed_loop_runtime_state
    ADD COLUMN last_reviewed_at TEXT;

ALTER TABLE manager_v4_closed_loop_runtime_state
    ADD COLUMN next_review_at TEXT;
