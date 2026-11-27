-- The closed loop carries the **cycle** of its review.
--
-- A closed loop does not wait for its next review passively: the loop has
-- a state machine, and one of its states is the running cycle.  The cycle
-- starts when the round of the review starts -- the round ends with a
-- broadcast that confirmed a profit, or with a measurement that found no
-- profit at all -- and it runs until the moment the loop is due again.
-- When it runs out the state of the loop becomes the review state and the
-- next round of the review starts.
--
-- `review_cycle_started_at` is the moment the round started, so the
-- length of the cycle is known: the cycle is as long as the automatic
-- review interval, measured from the start of the round.
--
-- The loop keeps **no timer**.  The moment the next round may start is
-- the moment the loop is scheduled at (`next_review_at`), and everything
-- an operator sees -- how long the loop still waits, how long it waited
-- already -- is counted from that moment when it is read.
ALTER TABLE manager_v4_closed_loop_runtime_state
    ADD COLUMN review_cycle_started_at TEXT;
