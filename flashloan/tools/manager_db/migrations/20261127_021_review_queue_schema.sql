-- The closed loop carries the state of its review **and** the moment of
-- its next review.
--
-- Two things change here.
--
-- The **index**: the queue of a scope is read by the moment a loop is
-- due, so the moment is indexed.  The index carries the whole scope, so
-- the question "which loops of this scope are due" is answered from
-- the index alone and the table is not touched.
--
-- The **sixth state**: a loop that traded and waits for the receipt of
-- its transaction is a state of its own.  It is not the review state
-- (the loop is not being measured) and it is not the cooldown state
-- (the loop has no scheduled review at all), so it needs its own name.
-- The CHECK constraint of the state belongs to the table and SQLite
-- cannot widen it, so the table is rebuilt: every row is copied into a
-- table whose constraint names the new value, the old table is dropped,
-- and the new one takes its name.
--
-- The rebuild keeps **every** column and **every** row: the state of a
-- loop belongs to the loop, and a migration never resets it.

CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_runtime_state_next (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    loop_key                        TEXT    NOT NULL,
    runtime_status                  TEXT    NOT NULL CHECK (
                                        runtime_status IN (
                                            'ready_for_review', 'review_pending',
                                            'broadcast_pending', 'blocked',
                                            'cooldown', 'retired'
                                        )
                                    ),
    status_reason                   TEXT,
    review_generation               INTEGER NOT NULL,
    review_required                 INTEGER NOT NULL CHECK (review_required IN (0, 1)),
    last_rebuilt_at                 TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    last_reviewed_at                TEXT,
    next_review_at                  TEXT,
    review_cycle_started_at         TEXT,
    PRIMARY KEY (network, chain_id, protocol, scope_key, loop_key)
);

INSERT INTO manager_v4_closed_loop_runtime_state_next (
    network, chain_id, protocol, scope_key, loop_key,
    runtime_status, status_reason, review_generation, review_required,
    last_rebuilt_at, updated_at, last_reviewed_at, next_review_at,
    review_cycle_started_at
)
SELECT network, chain_id, protocol, scope_key, loop_key,
       runtime_status, status_reason, review_generation, review_required,
       last_rebuilt_at, updated_at, last_reviewed_at, next_review_at,
       review_cycle_started_at
  FROM manager_v4_closed_loop_runtime_state;

DROP TABLE manager_v4_closed_loop_runtime_state;

ALTER TABLE manager_v4_closed_loop_runtime_state_next
    RENAME TO manager_v4_closed_loop_runtime_state;

CREATE INDEX IF NOT EXISTS idx_manager_v4_closed_loop_runtime_state_due
    ON manager_v4_closed_loop_runtime_state(
        network, chain_id, protocol, scope_key, next_review_at
    );

