CREATE TABLE IF NOT EXISTS manager_pipeline_runs (
    network                 TEXT    NOT NULL,
    chain_id                INTEGER NOT NULL,
    protocol                TEXT    NOT NULL,
    pipeline_key            TEXT    NOT NULL,
    run_id                  TEXT    NOT NULL,
    status                  TEXT    NOT NULL CHECK (
                                status IN ('running', 'completed', 'failed', 'idle')
                            ),
    requested_steps_json    TEXT    NOT NULL,
    upstream_snapshot_json  TEXT    NOT NULL,
    upstream_fingerprint    TEXT    NOT NULL,
    current_step_index      INTEGER NOT NULL,
    total_steps             INTEGER NOT NULL,
    last_completed_step_id  TEXT,
    last_error              TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    completed_at            TEXT,
    PRIMARY KEY (network, chain_id, protocol, pipeline_key, run_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_pipeline_runs_status_updated
    ON manager_pipeline_runs(
        network, chain_id, protocol, pipeline_key, status, updated_at
    );

CREATE TABLE IF NOT EXISTS manager_pipeline_steps (
    network                    TEXT    NOT NULL,
    chain_id                   INTEGER NOT NULL,
    protocol                   TEXT    NOT NULL,
    pipeline_key               TEXT    NOT NULL,
    run_id                     TEXT    NOT NULL,
    step_index                 INTEGER NOT NULL,
    step_id                    TEXT    NOT NULL,
    step_name                  TEXT    NOT NULL CHECK (
                                   step_name IN (
                                       'pool_scope', 'candidate_edge',
                                       'candidate_exchange', 'closed_loop',
                                       'review', 'broadcast'
                                   )
                               ),
    depends_on_json            TEXT    NOT NULL,
    status                     TEXT    NOT NULL CHECK (
                                   status IN (
                                       'pending', 'running', 'completed',
                                       'failed', 'idle'
                                   )
                               ),
    attempt_count              INTEGER NOT NULL,
    dependency_snapshot_json   TEXT,
    result_json                TEXT    NOT NULL DEFAULT '{}',
    last_error                 TEXT,
    started_at                 TEXT,
    last_attempted_at          TEXT,
    completed_at               TEXT,
    updated_at                 TEXT    NOT NULL,
    PRIMARY KEY (
        network, chain_id, protocol, pipeline_key, run_id, step_id
    ),
    UNIQUE (
        network, chain_id, protocol, pipeline_key, run_id, step_index
    )
);

CREATE INDEX IF NOT EXISTS idx_manager_pipeline_steps_run_status
    ON manager_pipeline_steps(
        network, chain_id, protocol, pipeline_key, run_id, status, step_index
    );

CREATE TABLE IF NOT EXISTS manager_pipeline_events (
    network           TEXT    NOT NULL,
    chain_id          INTEGER NOT NULL,
    protocol          TEXT    NOT NULL,
    pipeline_key      TEXT    NOT NULL,
    event_id          TEXT    NOT NULL,
    run_id            TEXT    NOT NULL,
    step_id           TEXT,
    event_type        TEXT    NOT NULL,
    actor             TEXT    NOT NULL,
    request_id        TEXT    NOT NULL,
    payload_json      TEXT    NOT NULL DEFAULT '{}',
    created_at        TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_pipeline_events_run_created
    ON manager_pipeline_events(
        network, chain_id, protocol, pipeline_key, run_id, created_at
    );

CREATE TABLE IF NOT EXISTS manager_events (
    network           TEXT    NOT NULL,
    chain_id          INTEGER NOT NULL,
    event_id          TEXT    NOT NULL,
    source_manager    TEXT    NOT NULL,
    event_type        TEXT    NOT NULL,
    aggregate_type    TEXT    NOT NULL,
    aggregate_id      TEXT    NOT NULL,
    actor             TEXT    NOT NULL,
    request_id        TEXT    NOT NULL,
    payload_json      TEXT    NOT NULL DEFAULT '{}',
    created_at        TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_events_aggregate_created
    ON manager_events(
        network, chain_id, aggregate_type, aggregate_id, created_at
    );

CREATE INDEX IF NOT EXISTS idx_manager_events_source_created
    ON manager_events(network, chain_id, source_manager, created_at);
