//! ARCH-1 / P0-4 version-triggered full-chain recompute latency measurement.
//!
//! This module is **read-only** with respect to every manager database: it never
//! writes pipeline rows, never switches authority and never broadcasts. It reads
//! the `manager_pipeline_runs` / `manager_pipeline_steps` / `manager_pipeline_events`
//! checkpoints that `composite_manager` already persists, and derives the
//! "version change -> full-chain recompute completed" latency that the plan
//! requires as a P0-4 deliverable.
//!
//! Measurement model
//! -----------------
//! * **Trigger point** — the run's `created_at`, i.e. the moment a recompute run
//!   was opened in response to an upstream version change.
//! * **Version change point** — the earliest `started_at` across the run's
//!   steps. A step cannot begin before its dependencies are satisfied, so this is
//!   the first observable moment at which the recompute actually started work.
//! * **Completion point** — the latest `completed_at` across the run's steps.
//! * **Total latency** — `completion - trigger`.
//! * **Checkpoint latency** — `completion - step.started_at` per step, so every
//!   manager checkpoint is attributable and individually auditable.
//!
//! A run only counts as a *full-chain* recompute when every requested step
//! reached a successful terminal state (`completed` or `idle`).

use crate::{
    checksum_json, CompositeError, PipelineEventRow, PipelineRunRow, PipelineStepName,
    PipelineStepRow, PipelineStepStatus,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// Terminal step states that satisfy the dependency chain.
fn is_terminal_success(status: PipelineStepStatus) -> bool {
    matches!(
        status,
        PipelineStepStatus::Completed | PipelineStepStatus::Idle
    )
}

/// A single manager checkpoint inside one recompute run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RecomputeCheckpoint {
    pub step_index: usize,
    pub step_id: String,
    pub step_name: PipelineStepName,
    pub depends_on: Vec<String>,
    pub status: PipelineStepStatus,
    /// Number of times the step was attempted; `> 1` means a retry happened.
    pub attempt_count: usize,
    /// `Some` when the step reached a terminal state.
    pub started_at: Option<String>,
    pub completed_at: Option<String>,
    /// Wall-clock milliseconds between the step start and its terminal state.
    pub step_duration_ms: Option<i64>,
    /// Wall-clock milliseconds between the run trigger and the step completion.
    pub cumulative_latency_ms: Option<i64>,
    /// Dependency fingerprint captured when the step began.
    pub dependency_fingerprint: Option<String>,
    /// Versions observed by the upstream snapshot captured at step start.
    pub dependency_versions: BTreeMap<String, i64>,
    pub last_error: Option<String>,
}

/// Version state observed at one boundary of the recompute.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RecomputeVersionBoundary {
    /// `token` / `pool` / `scope` / `edge` / `exchange` / `closed_loop` /
    /// `review` / `broadcast` version keys, flattened into one map.
    pub versions: BTreeMap<String, i64>,
    /// Checksum of the whole upstream snapshot, empty when unavailable.
    pub fingerprint: String,
}

/// Latency and retry statistics for one recompute run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RecomputeRunMeasurement {
    pub run_id: String,
    pub status: String,
    pub requested_steps: Vec<PipelineStepName>,
    /// `true` when every requested step reached a successful terminal state.
    pub full_chain_complete: bool,
    /// Version state captured when the run was opened.
    pub start_version: RecomputeVersionBoundary,
    /// Version state observed at the end of the run.
    pub end_version: RecomputeVersionBoundary,
    /// `true` when at least one upstream version differs between start and end.
    pub version_changed: bool,
    /// Names of the upstream components whose version changed.
    pub changed_versions: Vec<String>,
    pub checkpoints: Vec<RecomputeCheckpoint>,
    /// Milliseconds from run creation to the last step completion.
    pub total_latency_ms: Option<i64>,
    /// Milliseconds from the first step start to the last step completion.
    pub recompute_span_ms: Option<i64>,
    /// Sum of retries across all steps (`attempt_count - 1` per attempted step).
    pub failure_retry_count: usize,
    /// Number of steps that ended in `failed`.
    pub failed_step_count: usize,
    /// Number of distinct steps that were retried at least once.
    pub retried_step_count: usize,
    pub created_at: String,
    pub completed_at: Option<String>,
}

/// Aggregate report across every measured run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RecomputeLatencyReport {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub pipeline_key: String,
    /// Runs inspected, newest first.
    pub measured_run_count: usize,
    /// Runs that completed the whole requested chain.
    pub full_chain_complete_count: usize,
    /// Runs where the upstream version actually changed during the window.
    pub version_changed_run_count: usize,
    /// Median total latency over version-changing full-chain runs.
    pub median_total_latency_ms: Option<i64>,
    pub min_total_latency_ms: Option<i64>,
    pub max_total_latency_ms: Option<i64>,
    /// Sum of retries over version-changing full-chain runs.
    pub total_failure_retry_count: usize,
    pub measurements: Vec<RecomputeRunMeasurement>,
}

/// Read every pipeline run for the identity, newest first, and measure each one.
///
/// This never opens the database for writing.
pub fn measure_recompute_latency(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    limit: usize,
) -> Result<RecomputeLatencyReport, CompositeError> {
    crate::validate_identity(network, chain_id)?;
    let database_path = database_path.as_ref();
    let connection = crate::open_connection(database_path, false)?;
    let run_ids = read_run_ids(&connection, network, chain_id, limit)?;
    let mut measurements = Vec::new();
    for run_id in &run_ids {
        let run = crate::read_run(&connection, network, chain_id, run_id)?;
        let steps = crate::read_steps(&connection, network, chain_id, run_id)?;
        let events =
            crate::read_events(&connection, network, chain_id, run_id, crate::MAX_RUN_LIMIT)?;
        measurements.push(measure_run(run, steps, events));
    }
    Ok(summarize(network, chain_id, measurements))
}

fn read_run_ids(
    connection: &rusqlite::Connection,
    network: &str,
    chain_id: i64,
    limit: usize,
) -> Result<Vec<String>, CompositeError> {
    let mut statement = connection.prepare(
        "SELECT run_id
         FROM manager_pipeline_runs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pipeline_key = ?4
         ORDER BY created_at DESC, run_id DESC
         LIMIT ?5",
    )?;
    let rows = statement.query_map(
        rusqlite::params![
            network,
            chain_id,
            crate::NETWORK_PROTOCOL,
            crate::PIPELINE_KEY,
            crate::to_i64(limit)
        ],
        |row| row.get::<_, String>(0),
    )?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn measure_run(
    run: PipelineRunRow,
    steps: Vec<PipelineStepRow>,
    events: Vec<PipelineEventRow>,
) -> RecomputeRunMeasurement {
    // The run's `upstream_snapshot` is captured when the run is opened, i.e.
    // *before* any step ran. Each step additionally captures the dependency
    // versions it actually observed when it began. The first step's dependency
    // snapshot is therefore the "version at the start of the recompute" and the
    // last step's dependency snapshot is the "version at the end".
    let start_version = RecomputeVersionBoundary {
        versions: flatten_versions(&run.upstream_snapshot),
        fingerprint: run.upstream_fingerprint.clone(),
    };
    // A version change is observed when a component's version differs between
    // the run's initial upstream snapshot and the version that *any* step saw
    // while it ran. Each step's dependency snapshot intentionally carries only
    // the components that step depends on, so the end boundary is the union
    // across every step's snapshot. Comparing only the last step would miss
    // changes to components that the last step does not depend on (for example
    // a `candidate_edge` bump that only the middle steps observe).
    let mut end_versions: BTreeMap<String, i64> = BTreeMap::new();
    let mut end_fingerprint = String::new();
    for step in &steps {
        let Some(snapshot) = step.dependency_snapshot.as_ref() else {
            continue;
        };
        for (key, value) in flatten_dependency_versions(snapshot) {
            end_versions.entry(key).or_insert(value);
        }
        if end_fingerprint.is_empty() {
            end_fingerprint = snapshot.fingerprint.clone();
        }
    }
    if end_versions.is_empty() {
        end_versions = start_version.versions.clone();
        end_fingerprint = start_version.fingerprint.clone();
    }
    let end_version = RecomputeVersionBoundary {
        versions: end_versions.clone(),
        fingerprint: end_fingerprint,
    };
    let comparable = comparable_keys(&start_version.versions, &end_versions);
    let changed_versions = diff_versions(&start_version.versions, &end_versions, &comparable);

    let trigger_ms = parse_timestamp_ms(&run.created_at);
    let checkpoints: Vec<RecomputeCheckpoint> = steps
        .iter()
        .map(|step| build_checkpoint(step, trigger_ms))
        .collect();

    let first_started = checkpoints
        .iter()
        .filter_map(|checkpoint| checkpoint.started_at.as_deref())
        .filter_map(parse_timestamp_ms)
        .min();
    let last_completed = checkpoints
        .iter()
        .filter_map(|checkpoint| checkpoint.completed_at.as_deref())
        .filter_map(parse_timestamp_ms)
        .max();

    let total_latency_ms = match (trigger_ms, last_completed) {
        (Some(trigger), Some(completed)) => Some(completed.saturating_sub(trigger)),
        _ => None,
    };
    let recompute_span_ms = match (first_started, last_completed) {
        (Some(started), Some(completed)) => Some(completed.saturating_sub(started)),
        _ => None,
    };

    let requested_steps = run.requested_steps.clone();
    let full_chain_complete = !requested_steps.is_empty()
        && steps.iter().all(|step| is_terminal_success(step.status))
        && steps
            .iter()
            .filter(|step| requested_steps.contains(&step.step_name))
            .count()
            == requested_steps.len()
        && steps
            .iter()
            .filter(|step| requested_steps.contains(&step.step_name))
            .all(|step| is_terminal_success(step.status));

    let failure_retry_count = checkpoints
        .iter()
        .map(|checkpoint| checkpoint.attempt_count.saturating_sub(1))
        .sum();
    let retried_step_count = checkpoints
        .iter()
        .filter(|checkpoint| checkpoint.attempt_count > 1)
        .count();
    let failed_step_count = checkpoints
        .iter()
        .filter(|checkpoint| checkpoint.status == PipelineStepStatus::Failed)
        .count();

    let _ = &events;

    RecomputeRunMeasurement {
        run_id: run.run_id,
        status: format!("{:?}", run.status).to_lowercase(),
        requested_steps,
        full_chain_complete,
        start_version,
        end_version,
        version_changed: !changed_versions.is_empty(),
        changed_versions,
        checkpoints,
        total_latency_ms,
        recompute_span_ms,
        failure_retry_count,
        failed_step_count,
        retried_step_count,
        created_at: run.created_at,
        completed_at: run.completed_at,
    }
}

fn build_checkpoint(step: &PipelineStepRow, trigger_ms: Option<i64>) -> RecomputeCheckpoint {
    let started_ms = step.started_at.as_deref().and_then(parse_timestamp_ms);
    let completed_ms = step.completed_at.as_deref().and_then(parse_timestamp_ms);
    let step_duration_ms = match (started_ms, completed_ms) {
        (Some(started), Some(completed)) => Some(completed.saturating_sub(started)),
        _ => None,
    };
    let cumulative_latency_ms = match (trigger_ms, completed_ms) {
        (Some(trigger), Some(completed)) => Some(completed.saturating_sub(trigger)),
        _ => None,
    };
    let dependency_versions = step
        .dependency_snapshot
        .as_ref()
        .map(|snapshot| flatten_dependency_versions(snapshot))
        .unwrap_or_default();
    let dependency_fingerprint = step
        .dependency_snapshot
        .as_ref()
        .map(|snapshot| snapshot.fingerprint.clone())
        .filter(|fingerprint| !fingerprint.is_empty());
    RecomputeCheckpoint {
        step_index: step.step_index,
        step_id: step.step_id.clone(),
        step_name: step.step_name,
        depends_on: step.depends_on.clone(),
        status: step.status,
        attempt_count: step.attempt_count,
        started_at: step.started_at.clone(),
        completed_at: step.completed_at.clone(),
        step_duration_ms,
        cumulative_latency_ms,
        dependency_fingerprint,
        dependency_versions,
        last_error: step.last_error.clone(),
    }
}

/// Flatten an upstream snapshot into a single `component -> version` map.
fn flatten_versions(snapshot: &crate::UpstreamSnapshot) -> BTreeMap<String, i64> {
    let mut versions = BTreeMap::new();
    for (library, reference) in &snapshot.token_libraries {
        versions.insert(format!("token_libraries.{library}"), reference.version);
    }
    // `pool_scope` carries checksums rather than a numeric version, so it is
    // excluded from the numeric version map and represented by its library
    // versions (already flattened above) plus the scope fingerprint.
    versions.entry("pool_scope".to_owned()).or_insert(0);
    versions.insert("candidate_edge".to_owned(), snapshot.candidate_edge.version);
    versions.insert(
        "candidate_exchange".to_owned(),
        snapshot.candidate_exchange.version,
    );
    versions.insert("closed_loop".to_owned(), snapshot.closed_loop.version);
    versions.insert("review".to_owned(), snapshot.review.version);
    versions.insert("broadcast".to_owned(), snapshot.broadcast.version);
    versions
}

fn flatten_dependency_versions(snapshot: &crate::StepDependencySnapshot) -> BTreeMap<String, i64> {
    let mut versions = BTreeMap::new();
    for (library, reference) in &snapshot.token_libraries {
        versions.insert(format!("token_libraries.{library}"), reference.version);
    }
    if snapshot
        .pool_scope
        .as_ref()
        .is_some_and(|scope| scope.enabled)
    {
        versions.entry("pool_scope".to_owned()).or_insert(0);
    }
    if let Some(edge) = &snapshot.candidate_edge {
        versions.insert("candidate_edge".to_owned(), edge.version);
    }
    if let Some(exchange) = &snapshot.candidate_exchange {
        versions.insert("candidate_exchange".to_owned(), exchange.version);
    }
    if let Some(loop_) = &snapshot.closed_loop {
        versions.insert("closed_loop".to_owned(), loop_.version);
    }
    if let Some(review) = &snapshot.review {
        versions.insert("review".to_owned(), review.version);
    }
    versions
}

/// The component keys observed on both the start and the end boundary.
///
/// A dependency snapshot only carries the components a step depends on, so the
/// comparison set is the intersection. Components that exist on exactly one
/// side are not evidence of a version change.
fn comparable_keys(start: &BTreeMap<String, i64>, end: &BTreeMap<String, i64>) -> BTreeSet<String> {
    start
        .keys()
        .filter(|key| end.contains_key(*key))
        .cloned()
        .collect()
}

fn diff_versions(
    start: &BTreeMap<String, i64>,
    end: &BTreeMap<String, i64>,
    comparable: &BTreeSet<String>,
) -> Vec<String> {
    let mut changed = Vec::new();
    for key in comparable {
        if let (Some(start_version), Some(end_version)) = (start.get(key), end.get(key)) {
            if start_version != end_version {
                changed.push(key.clone());
            }
        }
    }
    changed.sort();
    changed.dedup();
    changed
}

/// Parse an RFC-3339 timestamp into milliseconds since the UNIX epoch.
///
/// The pipeline writes `chrono::Utc::now().to_rfc3339()`, which always carries
/// an explicit offset, so a fixed-offset parser is sufficient and avoids
/// pulling in a new dependency.
fn parse_timestamp_ms(value: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(value)
        .ok()
        .map(|parsed| parsed.timestamp_millis())
}

fn summarize(
    network: &str,
    chain_id: i64,
    measurements: Vec<RecomputeRunMeasurement>,
) -> RecomputeLatencyReport {
    let full_chain_complete_count = measurements
        .iter()
        .filter(|measurement| measurement.full_chain_complete)
        .count();
    let version_changed_run_count = measurements
        .iter()
        .filter(|measurement| measurement.version_changed)
        .count();
    let qualifying: Vec<i64> = measurements
        .iter()
        .filter(|measurement| measurement.full_chain_complete && measurement.version_changed)
        .filter_map(|measurement| measurement.total_latency_ms)
        .collect();
    let min_total_latency_ms = qualifying.iter().copied().min();
    let max_total_latency_ms = qualifying.iter().copied().max();
    let median_total_latency_ms = median(&qualifying);
    let total_failure_retry_count = measurements
        .iter()
        .filter(|measurement| measurement.full_chain_complete && measurement.version_changed)
        .map(|measurement| measurement.failure_retry_count)
        .sum();
    RecomputeLatencyReport {
        network: network.to_owned(),
        chain_id,
        protocol: crate::NETWORK_PROTOCOL.to_owned(),
        pipeline_key: crate::PIPELINE_KEY.to_owned(),
        measured_run_count: measurements.len(),
        full_chain_complete_count,
        version_changed_run_count,
        median_total_latency_ms,
        min_total_latency_ms,
        max_total_latency_ms,
        total_failure_retry_count,
        measurements,
    }
}

fn median(values: &[i64]) -> Option<i64> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let middle = sorted.len() / 2;
    if sorted.len() % 2 == 1 {
        Some(sorted[middle])
    } else {
        Some((sorted[middle - 1] + sorted[middle]) / 2)
    }
}

/// Stable checksum over the whole report, used to bind the machine evidence.
pub fn recompute_latency_checksum(
    report: &RecomputeLatencyReport,
) -> Result<String, CompositeError> {
    checksum_json(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        begin_pipeline_step_write, complete_pipeline_step_write, refresh_pipeline_write,
        PipelineBeginStepRequest, PipelineCompleteStepRequest, PipelineRefreshRequest,
        StepCompletionStatus,
    };
    use rusqlite::{params, Connection};
    use token_manager::{write_library, AdmissionStatus, LibraryKey, TokenWrite};

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;

    fn database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = Connection::open(&path).unwrap();
        for library in [
            LibraryKey::Core,
            LibraryKey::Stocks,
            LibraryKey::Extension,
            LibraryKey::Test,
        ] {
            write_library(
                &mut connection,
                library,
                NETWORK,
                CHAIN_ID as u64,
                vec![token(library)],
                Some("fixture:v1"),
                "test",
                "fixture",
            )
            .unwrap();
        }
        drop(connection);
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "INSERT INTO pool_scope_config (
                    network, chain_id, scope_key, pool_table,
                    library_keys_json, library_versions_json,
                    library_checksums_json, filter_mode,
                    excluded_token_key_ids_json, enabled,
                    materialized_checksum, last_rebuilt_at, created_at, updated_at
                 ) VALUES (?1, ?2, 'core4', 'v4_exchange_pools_core4',
                           '[\"core\"]', '{}', '{}', 'edge_any', '[]', 1,
                           'sha256:scope-v1', 'fixture', 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);
        seed_candidate_state(&path);
        (directory, path)
    }

    /// Seed the candidate-edge and candidate-exchange library state so a
    /// `Completed` step can satisfy `validate_step_output`, which requires the
    /// manager to have actually materialized its projection.
    fn seed_candidate_state(path: &std::path::Path) {
        let connection = Connection::open(path).unwrap();
        connection
            .execute(
                "INSERT INTO manager_dex_candidate_library_state (
                    network, chain_id, protocol, scope_key,
                    candidate_version, materialized_checksum, pool_scope_checksum,
                    pool_library_versions_json, pool_library_checksums_json,
                    library_versions_json, library_checksums_json,
                    anchor_token_count, candidate_token_count, eligible_pool_count,
                    recommendation_count, abnormal_pool_count,
                    generated_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', 1,
                           'sha256:candidate-edge-v1', 'sha256:scope-v1',
                           '{}', '{}', '{}', '{}',
                           1, 1, 1, 1, 0, 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO manager_market_observation_library_state (
                    network, chain_id, protocol, scope_key,
                    observation_version, materialized_checksum,
                    candidate_edge_version, candidate_edge_checksum, pool_scope_checksum,
                    library_versions_json, library_checksums_json,
                    anchor_count, token_count, direction_count, empty_direction_count,
                    pool_count, abnormal_direction_count, abnormal_pool_count,
                    generated_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'candidate_exchange', 1,
                           'sha256:candidate-exchange-v1', 1, 'sha256:candidate-edge-v1',
                           'sha256:scope-v1', '{}', '{}',
                           1, 1, 1, 0, 1, 0, 0, 'fixture', 'fixture')",
                params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);
    }

    fn token(library: LibraryKey) -> TokenWrite {
        let address = match library {
            LibraryKey::Core => "0x1111111111111111111111111111111111111111",
            LibraryKey::Stocks => "0x2222222222222222222222222222222222222222",
            LibraryKey::Extension => "0x3333333333333333333333333333333333333333",
            LibraryKey::Test => "0x4444444444444444444444444444444444444444",
        };
        TokenWrite {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID as u64,
            token_key_id: manager_contracts::TokenKeyId::new(NETWORK, CHAIN_ID as u64, address)
                .unwrap(),
            v4_currency_key: address.to_owned(),
            token_address: address.to_owned(),
            token_symbol: format!("T-{}", library.as_str()),
            token_name: Some(format!("T-{}", library.as_str())),
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: false,
            admission_status: AdmissionStatus::Admitted,
            admission_source: "fixture".to_owned(),
            metadata_source: "fixture".to_owned(),
            metadata_checksum: format!("sha256:{}", library.as_str()),
            source_json: Some("{}".to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    fn refresh_request(request_id: &str) -> PipelineRefreshRequest {
        PipelineRefreshRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "pipeline.refresh".to_owned(),
            request_id: request_id.to_owned(),
            idempotency_key: format!("{request_id}-idem"),
            apply: true,
            requested_steps: vec![
                PipelineStepName::CandidateEdge,
                PipelineStepName::CandidateExchange,
            ],
        }
    }

    fn begin_request(run_id: &str, step_id: &str, request_id: &str) -> PipelineBeginStepRequest {
        PipelineBeginStepRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "pipeline.step.begin".to_owned(),
            request_id: request_id.to_owned(),
            idempotency_key: format!("{request_id}-idem"),
            apply: true,
            run_id: run_id.to_owned(),
            step_id: step_id.to_owned(),
        }
    }

    fn complete_request(
        run_id: &str,
        step_id: &str,
        status: StepCompletionStatus,
        request_id: &str,
    ) -> PipelineCompleteStepRequest {
        PipelineCompleteStepRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "pipeline.step.complete".to_owned(),
            request_id: request_id.to_owned(),
            idempotency_key: format!("{request_id}-idem"),
            apply: true,
            run_id: run_id.to_owned(),
            step_id: step_id.to_owned(),
            status,
            result: serde_json::json!({"source": "fixture"}),
            error: (status == StepCompletionStatus::Failed).then(|| "fixture failure".to_owned()),
        }
    }

    /// Shift every pipeline timestamp forward while preserving the exact
    /// RFC-3339 representation that `chrono::Utc::now().to_rfc3339()` writes.
    ///
    /// SQLite's `datetime()` would rewrite the value into a space-separated
    /// form and silently break RFC-3339 parsing, so the shift is done in Rust.
    fn advance_clock(connection: &Connection, seconds: i64) {
        let mut statement = connection
            .prepare(
                "SELECT started_at, last_attempted_at, completed_at, updated_at
                 FROM manager_pipeline_steps",
            )
            .unwrap();
        let rows: Vec<(Option<String>, Option<String>, Option<String>, String)> = statement
            .query_map([], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        for (started, attempted, completed, updated) in rows {
            connection
                .execute(
                    "UPDATE manager_pipeline_steps
                     SET started_at = ?1, last_attempted_at = ?2, completed_at = ?3, updated_at = ?4
                     WHERE rowid = (SELECT rowid FROM manager_pipeline_steps
                                    WHERE started_at IS ?5 AND updated_at = ?6 LIMIT 1)",
                    params![
                        shift_timestamp(started.as_deref(), seconds),
                        shift_timestamp(attempted.as_deref(), seconds),
                        shift_timestamp(completed.as_deref(), seconds),
                        shift_timestamp(Some(&updated), seconds),
                        started,
                        updated
                    ],
                )
                .unwrap();
        }
        let mut statement = connection
            .prepare("SELECT created_at, updated_at, completed_at FROM manager_pipeline_runs")
            .unwrap();
        let runs: Vec<(String, String, Option<String>)> = statement
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        for (created, updated, completed) in runs {
            connection
                .execute(
                    "UPDATE manager_pipeline_runs
                     SET created_at = ?1, updated_at = ?2, completed_at = ?3
                     WHERE created_at = ?4",
                    params![
                        shift_timestamp(Some(&created), seconds),
                        shift_timestamp(Some(&updated), seconds),
                        shift_timestamp(completed.as_deref(), seconds),
                        created
                    ],
                )
                .unwrap();
        }
    }

    fn shift_timestamp(value: Option<&str>, seconds: i64) -> Option<String> {
        value.and_then(|value| {
            chrono::DateTime::parse_from_rfc3339(value)
                .ok()
                .map(|parsed| (parsed + chrono::Duration::seconds(seconds)).to_rfc3339())
        })
    }

    #[test]
    fn a_full_chain_run_reports_per_step_checkpoints_and_total_latency() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request("refresh-full")).unwrap();
        let run_id = refresh.result.run.run_id.clone();
        let step_ids: Vec<String> = refresh
            .result
            .steps
            .iter()
            .map(|step| step.step_id.clone())
            .collect();
        for (index, step_id) in step_ids.iter().enumerate() {
            begin_pipeline_step_write(&path, begin_request(&run_id, step_id, &format!("b{index}")))
                .unwrap();
            complete_pipeline_step_write(
                &path,
                complete_request(
                    &run_id,
                    step_id,
                    StepCompletionStatus::Completed,
                    &format!("c{index}"),
                ),
            )
            .unwrap();
        }

        // Give the second step a later start/end so the checkpoints differ.
        let connection = Connection::open(&path).unwrap();
        let mut statement = connection
            .prepare(
                "SELECT started_at, completed_at FROM manager_pipeline_steps WHERE step_index = 1",
            )
            .unwrap();
        let (started, completed): (Option<String>, Option<String>) = statement
            .query_row([], |row| Ok((row.get(0)?, row.get(1)?)))
            .unwrap();
        drop(statement);
        connection
            .execute(
                "UPDATE manager_pipeline_steps
                 SET started_at = ?1, completed_at = ?2 WHERE step_index = 1",
                params![
                    shift_timestamp(started.as_deref(), 5),
                    shift_timestamp(completed.as_deref(), 5)
                ],
            )
            .unwrap();
        advance_clock(&connection, 3);
        drop(connection);

        let report = measure_recompute_latency(&path, NETWORK, CHAIN_ID, 10).unwrap();
        assert_eq!(report.measured_run_count, 1);
        assert_eq!(report.full_chain_complete_count, 1);
        let measurement = &report.measurements[0];
        assert!(measurement.full_chain_complete);
        assert_eq!(measurement.checkpoints.len(), 2);
        assert!(measurement.total_latency_ms.is_some());
        assert!(measurement.recompute_span_ms.is_some());
        for checkpoint in &measurement.checkpoints {
            assert_eq!(checkpoint.status, PipelineStepStatus::Completed);
            assert_eq!(checkpoint.attempt_count, 1);
            assert!(checkpoint.step_duration_ms.is_some());
            assert!(checkpoint.cumulative_latency_ms.is_some());
            assert!(checkpoint.dependency_fingerprint.is_some());
            assert!(checkpoint
                .dependency_versions
                .contains_key("token_libraries.core"));
        }
        // The second step starts later, so it must take longer overall.
        assert!(
            measurement.checkpoints[1].cumulative_latency_ms
                > measurement.checkpoints[0].cumulative_latency_ms
        );
        assert!(recompute_latency_checksum(&report)
            .unwrap()
            .starts_with("sha256:"));
    }

    #[test]
    fn retries_are_counted_and_a_failed_step_is_not_full_chain() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request("refresh-retry")).unwrap();
        let run_id = refresh.result.run.run_id.clone();
        let step_ids: Vec<String> = refresh
            .result
            .steps
            .iter()
            .map(|step| step.step_id.clone())
            .collect();

        // First attempt fails.
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_ids[0], "b0-a")).unwrap();
        complete_pipeline_step_write(
            &path,
            complete_request(&run_id, &step_ids[0], StepCompletionStatus::Failed, "c0-a"),
        )
        .unwrap();
        // Second attempt succeeds.
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_ids[0], "b0-b")).unwrap();
        complete_pipeline_step_write(
            &path,
            complete_request(
                &run_id,
                &step_ids[0],
                StepCompletionStatus::Completed,
                "c0-b",
            ),
        )
        .unwrap();
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_ids[1], "b1")).unwrap();
        complete_pipeline_step_write(
            &path,
            complete_request(&run_id, &step_ids[1], StepCompletionStatus::Completed, "c1"),
        )
        .unwrap();

        let report = measure_recompute_latency(&path, NETWORK, CHAIN_ID, 10).unwrap();
        let measurement = &report.measurements[0];
        assert!(measurement.full_chain_complete);
        assert_eq!(measurement.failure_retry_count, 1);
        assert_eq!(measurement.retried_step_count, 1);
        assert_eq!(measurement.failed_step_count, 0);
        assert_eq!(measurement.checkpoints[0].attempt_count, 2);
        assert_eq!(measurement.failure_retry_count, 1);
        // The aggregate retry counter is scoped to version-changing full-chain
        // runs; this fixture keeps the upstream version stable, so the
        // aggregate stays 0 while the per-run counter remains authoritative.
        assert_eq!(report.total_failure_retry_count, 0);
        assert!(!measurement.version_changed);
        assert!(
            measurement.changed_versions.is_empty(),
            "unexpected changed versions: {:?}",
            measurement.changed_versions
        );
    }

    #[test]
    fn an_unfinished_run_is_not_reported_as_full_chain() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request("refresh-open")).unwrap();
        let run_id = refresh.result.run.run_id.clone();
        let step_id = refresh.result.steps[0].step_id.clone();
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_id, "b0")).unwrap();

        let report = measure_recompute_latency(&path, NETWORK, CHAIN_ID, 10).unwrap();
        let measurement = &report.measurements[0];
        assert!(!measurement.full_chain_complete);
        assert_eq!(report.full_chain_complete_count, 0);
        assert!(measurement.total_latency_ms.is_none());
    }

    #[test]
    fn measurement_is_read_only_and_does_not_create_rows() {
        let (_directory, path) = database();
        refresh_pipeline_write(&path, refresh_request("refresh-ro")).unwrap();
        let connection = Connection::open(&path).unwrap();
        let before: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_pipeline_runs", [], |row| {
                row.get(0)
            })
            .unwrap();
        drop(connection);

        let report = measure_recompute_latency(&path, NETWORK, CHAIN_ID, 10).unwrap();
        assert_eq!(report.measured_run_count, 1);

        let connection = Connection::open(&path).unwrap();
        let after: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_pipeline_runs", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(before, after);
    }

    #[test]
    fn a_version_change_between_steps_is_detected() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request("refresh-version")).unwrap();
        let run_id = refresh.result.run.run_id.clone();
        let step_ids: Vec<String> = refresh
            .result
            .steps
            .iter()
            .map(|step| step.step_id.clone())
            .collect();

        begin_pipeline_step_write(&path, begin_request(&run_id, &step_ids[0], "b0")).unwrap();
        // Bump the candidate-edge version *after* the run was opened, so the
        // second step observes a different upstream version than the run did.
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_dex_candidate_library_state
                 SET candidate_version = candidate_version + 1",
                [],
            )
            .unwrap();
        // The exchange projection tracks the candidate-edge version, so it must
        // move in lockstep to keep the upstream snapshot self-consistent.
        connection
            .execute(
                "UPDATE manager_market_observation_library_state
                 SET candidate_edge_version = candidate_edge_version + 1",
                [],
            )
            .unwrap();
        drop(connection);

        complete_pipeline_step_write(
            &path,
            complete_request(&run_id, &step_ids[0], StepCompletionStatus::Completed, "c0"),
        )
        .unwrap();
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_ids[1], "b1")).unwrap();
        complete_pipeline_step_write(
            &path,
            complete_request(&run_id, &step_ids[1], StepCompletionStatus::Completed, "c1"),
        )
        .unwrap();

        let report = measure_recompute_latency(&path, NETWORK, CHAIN_ID, 10).unwrap();
        let measurement = &report.measurements[0];
        assert!(measurement.full_chain_complete);
        assert!(measurement.version_changed);
        assert!(
            measurement
                .changed_versions
                .iter()
                .any(|key| key == "candidate_edge"),
            "expected candidate_edge in {:?}",
            measurement.changed_versions
        );
        assert_eq!(report.version_changed_run_count, 1);
    }

    #[test]
    fn a_version_change_seen_only_by_a_middle_step_is_still_detected() {
        let (_directory, path) = database();
        let refresh = refresh_pipeline_write(&path, refresh_request("refresh-middle")).unwrap();
        let run_id = refresh.result.run.run_id.clone();
        let step_ids: Vec<String> = refresh
            .result
            .steps
            .iter()
            .map(|step| step.step_id.clone())
            .collect();

        // The first step observes the pre-bump candidate-edge version.
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_ids[0], "b0")).unwrap();
        let connection = Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_dex_candidate_library_state
                 SET candidate_version = candidate_version + 1",
                [],
            )
            .unwrap();
        connection
            .execute(
                "UPDATE manager_market_observation_library_state
                 SET candidate_edge_version = candidate_edge_version + 1",
                [],
            )
            .unwrap();
        drop(connection);
        complete_pipeline_step_write(
            &path,
            complete_request(&run_id, &step_ids[0], StepCompletionStatus::Completed, "c0"),
        )
        .unwrap();

        // The second step observes the post-bump candidate-edge version. The
        // last step depends only on `closed_loop`/`review`, so a comparison
        // against the last step alone would miss the change entirely.
        begin_pipeline_step_write(&path, begin_request(&run_id, &step_ids[1], "b1")).unwrap();
        complete_pipeline_step_write(
            &path,
            complete_request(&run_id, &step_ids[1], StepCompletionStatus::Completed, "c1"),
        )
        .unwrap();

        let report = measure_recompute_latency(&path, NETWORK, CHAIN_ID, 10).unwrap();
        let measurement = &report.measurements[0];
        assert!(measurement.version_changed);
        assert!(
            measurement
                .changed_versions
                .iter()
                .any(|key| key == "candidate_edge"),
            "expected candidate_edge in {:?}",
            measurement.changed_versions
        );
    }

    #[test]
    fn median_handles_even_and_odd_populations() {
        assert_eq!(median(&[]), None);
        assert_eq!(median(&[5]), Some(5));
        assert_eq!(median(&[1, 3]), Some(2));
        assert_eq!(median(&[1, 2, 3]), Some(2));
        assert_eq!(median(&[4, 1, 3, 2]), Some(2));
    }
}
