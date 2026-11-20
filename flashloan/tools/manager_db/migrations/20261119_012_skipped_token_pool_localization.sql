-- P0: a closed-loop defect must be localizable to a concrete pool.
--
-- The closed-loop Manager drops a path when the middle (Y->Z) leg has no
-- measurable pool.  The drop used to be silent: the loop simply did not
-- appear, and the operator had no way to learn which token pair was
-- missing or why.  The Manager now records the drop as a skip reason that
-- names the pair and the diagnosis:
--
--   yz_direction_missing:<y_token>-><z_token>
--   yz_pool_missing:<y_token>-><z_token>:<diagnosis>[:<measured pool ids>]
--
-- The same reasoning applies to `candidate_role`.  The column used to be
-- closed to ('first', 'third'), so a middle-leg drop could not be stored
-- at all.  It is widened to ('first', 'third', 'middle').
--
-- Both columns are **diagnostics**.  A new disqualification reason must
-- never be able to break the write path of the closed-loop library, so the
-- constraint stays a prefix check that keeps the fixed reasons legal
-- while still refusing an empty reason.
--
-- SQLite cannot relax a CHECK constraint in place, so the table is
-- rebuilt: the rows are copied into a new table with the widened
-- constraint, the old table is dropped and the new one takes its place.

CREATE TABLE IF NOT EXISTS manager_v4_closed_loop_skipped_tokens_new (
    network                         TEXT    NOT NULL,
    chain_id                        INTEGER NOT NULL,
    protocol                        TEXT    NOT NULL,
    scope_key                       TEXT    NOT NULL,
    anchor_token_key_id             TEXT    NOT NULL,
    token_key_id                    TEXT    NOT NULL,
    token_symbol                    TEXT    NOT NULL,
    candidate_role                  TEXT    NOT NULL CHECK (candidate_role IN ('first', 'third', 'middle')),
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
                                        OR reason LIKE 'yz_direction_missing:%'
                                        OR reason LIKE 'yz_pool_missing:%'
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
