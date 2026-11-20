-- P0: the skip reason of a closed-loop candidate token is open-ended.
--
-- The closed-loop Manager records **why** a candidate token did not take
-- part in a loop.  The reason is what the operator reads to find out which
-- pool disqualified the path, so the Manager names the blocking pool
-- inside the reason:
--
--   path_single_tick_bound_unusable:<pool_id>
--   path_capacity_below_minimum:<pool_id>
--
-- The original schema closed the column to a fixed list of five reasons,
-- which could not express those two.  Every rebuild that reached a path
-- with an unusable tick bound or a capacity under the floor therefore
-- failed with a CHECK constraint error, and the closed-loop library could
-- not be rebuilt at all.
--
-- The column is a **diagnostic**, not an enum: a new disqualification
-- reason must never be able to break the write path of the library.  The
-- constraint is therefore widened to a prefix check that keeps the two
-- parameterised reasons (and the fixed ones) legal while still refusing
-- an empty reason.
--
-- SQLite cannot relax a CHECK constraint in place, so the table is
-- rebuilt: the rows are copied into a new table with the widened
-- constraint, the old table is dropped and the new one takes its place.
-- The copy keeps every row that the old constraint allowed.

CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_skipped_tokens_new (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    anchor_token_key_id             TEXT    NOT NULL,
    token_key_id                    TEXT    NOT NULL,
    token_symbol                    TEXT    NOT NULL,
    candidate_role                  TEXT    NOT NULL CHECK (candidate_role IN ('first', 'third')),
    reason                          TEXT    NOT NULL CHECK (
                                        reason IN (
                                            'no_completing_middle_direction',
                                            'no_completing_first_hop',
                                            'no_completing_third_hop',
                                            'middle_pool_missing',
                                            'anchor_loop_not_allowed'
                                        )
                                        OR reason LIKE 'path_single_tick_bound_unusable:%'
                                        OR reason LIKE 'path_capacity_below_minimum:%'
                                    ),
    created_at                      TEXT    NOT NULL,
    updated_at                      TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, scope_key,
        anchor_token_key_id, token_key_id, candidate_role, reason
    )
);

INSERT INTO manager_v4_closed_loop_skipped_tokens_new (
    network, chain_id, protocol, scope_key,
    anchor_token_key_id, token_key_id, token_symbol,
    candidate_role, reason, created_at, updated_at
)
SELECT
    network, chain_id, protocol, scope_key,
    anchor_token_key_id, token_key_id, token_symbol,
    candidate_role, reason, created_at, updated_at
FROM manager_v4_closed_loop_skipped_tokens;

DROP TABLE manager_v4_closed_loop_skipped_tokens;

ALTER TABLE manager_v4_closed_loop_skipped_tokens_new
    RENAME TO manager_v4_closed_loop_skipped_tokens;
