-- VAL-4-7: the durable store for the USDG valuation of every measured token.
--
-- The valuation itself is produced by the Uniswap Trading API `/quote`
-- probe and lands in a report file.  A report file is a transient artifact:
-- it is copied around, it is overwritten by the next run, and it says
-- nothing about which database the rest of the system should believe.
--
-- This table makes the observation durable and queryable.  It records the
-- *verdict* of every probe, not just the tokens that happened to have a
-- route, because the difference between "measured and unvaluable" and
-- "never measured" is exactly what the economic gate needs to decide
-- between a negative and a pending token.
--
-- The columns deliberately mirror the vocabulary of the probe:
--
--   verdict            'route_found' | 'no_route' | 'api_unavailable' | 'api_error'
--   usdg_amount_raw    the value of one whole token in raw USDG (6 decimals),
--                      NULL when the token has no route
--   measured_at        when the probe was observed
--   source             the producer of the observation
--
-- `no_route` is a genuine negative and is therefore recorded as a row.
-- `api_unavailable` and `api_error` are unknowns and are recorded too, so
-- that a failed refresh never silently looks like a successful one.
--
-- The primary key is (network, chain_id, token_address): a later probe of
-- the same token replaces the earlier observation instead of accumulating
-- a history that no reader would ever consume.

CREATE TABLE IF NOT EXISTS token_usdg_valuations (
    network               TEXT    NOT NULL,
    chain_id              INTEGER NOT NULL,
    token_address         TEXT    NOT NULL,
    token_symbol          TEXT,
    verdict               TEXT    NOT NULL CHECK (
                              verdict IN (
                                  'route_found', 'no_route',
                                  'api_unavailable', 'api_error'
                              )
                          ),
    has_value             INTEGER NOT NULL CHECK (has_value IN (0, 1)),
    input_amount_raw      TEXT    NOT NULL,
    usdg_amount_raw       TEXT,
    usdg_amount_decimal   TEXT,
    price_impact          TEXT,
    gas_estimate          TEXT,
    route_leg_count       INTEGER,
    protocols_json        TEXT    NOT NULL,
    failure_kind          TEXT,
    failure_detail        TEXT,
    source                TEXT    NOT NULL,
    measured_at           TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, token_address)
);

CREATE INDEX IF NOT EXISTS idx_token_usdg_valuations_verdict
    ON token_usdg_valuations(network, chain_id, verdict);

CREATE INDEX IF NOT EXISTS idx_token_usdg_valuations_measured_at
    ON token_usdg_valuations(network, chain_id, measured_at);
