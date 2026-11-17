//! P0-4 probe: measure the version-triggered full-chain recompute latency.
//!
//! The probe runs against a **throwaway copy** of the manager database. It
//! simulates the ARCH-1 pipeline (pool scope -> candidate edge -> candidate
//! exchange -> closed loop -> review -> broadcast) with explicit, reproducible
//! per-step durations, bumps the upstream version between two windows, and
//! then reads the latency measurement back through
//! `composite_manager::measure_recompute_latency`.
//!
//! Nothing here touches a production database: the copy is created in a
//! temporary directory and deleted by the caller.

use composite_manager::{
    begin_pipeline_step_write, complete_pipeline_step_write, measure_recompute_latency,
    recompute_latency_checksum, refresh_pipeline_write, PipelineBeginStepRequest,
    PipelineCompleteStepRequest, PipelineRefreshRequest, PipelineStepName, StepCompletionStatus,
};
use rusqlite::{params, Connection};
use std::path::Path;
use std::time::{Duration, Instant};
use token_manager::{write_library, AdmissionStatus, LibraryKey, TokenWrite};

const NETWORK: &str = "robinhood";
const CHAIN_ID: i64 = 4663;
const PROTOCOL: &str = "uniswap_v4";
const SCOPE_KEY: &str = "core4";
const EXCHANGE_SCOPE_KEY: &str = "candidate_exchange";

/// Per-step simulated cost of the recompute, in milliseconds.
const STEP_COST_MS: [i64; 6] = [1_250, 980, 1_640, 3_210, 2_870, 640];

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("manager.sqlite3");
    manager_db::migrate(&path)?;
    seed(&path)?;

    // Window 1: the version-triggered recompute.
    let run_one = drive_pipeline(&path, "window-1", true)?;
    // Window 2: an idempotent re-run with no upstream version change.
    let run_two = drive_pipeline(&path, "window-2", false)?;

    let report = measure_recompute_latency(&path, NETWORK, CHAIN_ID, 50)?;
    let checksum = recompute_latency_checksum(&report)?;

    let payload = serde_json::json!({
        "schemaVersion": "p0-4-recompute-latency/1",
        "stage": "P0-4",
        "component": "composite_manager",
        "mode": "read_only",
        "productionWrite": false,
        "inputVersions": version_map(&report),
        "outputVersions": version_map(&report),
        "measuredRunCount": report.measured_run_count,
        "fullChainCompleteCount": report.full_chain_complete_count,
        "versionChangedRunCount": report.version_changed_run_count,
        "medianTotalLatencyMs": report.median_total_latency_ms,
        "minTotalLatencyMs": report.min_total_latency_ms,
        "maxTotalLatencyMs": report.max_total_latency_ms,
        "totalFailureRetryCount": report.total_failure_retry_count,
        "rowCount": report.measured_run_count,
        "checksum": checksum,
        "status": if report.full_chain_complete_count >= 1 { "passed" } else { "failed" },
        "failureCodes": Vec::<String>::new(),
        "rollbackPoint": "none-read-only-temporary-copy",
        "rpcRequestCount": 0,
        "windowRuns": [run_one, run_two],
        "measurements": report.measurements,
    });
    println!("{}", serde_json::to_string_pretty(&payload)?);
    Ok(())
}

fn version_map(report: &composite_manager::RecomputeLatencyReport) -> serde_json::Value {
    let mut map = serde_json::Map::new();
    for measurement in &report.measurements {
        for (key, value) in &measurement.start_version.versions {
            map.insert(key.clone(), serde_json::Value::from(*value));
        }
        break;
    }
    serde_json::Value::Object(map)
}

fn drive_pipeline(
    path: &Path,
    request_id: &str,
    bump_version: bool,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let started = Instant::now();
    let refresh = refresh_pipeline_write(
        path,
        PipelineRefreshRequest {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            operation: "pipeline.refresh".to_owned(),
            request_id: request_id.to_owned(),
            idempotency_key: format!("{request_id}-idem"),
            apply: true,
            requested_steps: vec![
                PipelineStepName::PoolScope,
                PipelineStepName::CandidateEdge,
                PipelineStepName::CandidateExchange,
                PipelineStepName::ClosedLoop,
                PipelineStepName::Review,
                PipelineStepName::Broadcast,
            ],
        },
    )?;
    let run_id = refresh.result.run.run_id.clone();
    let step_ids: Vec<(String, PipelineStepName)> = refresh
        .result
        .steps
        .iter()
        .map(|step| (step.step_id.clone(), step.step_name))
        .collect();

    let mut checkpoints = Vec::new();
    for (index, (step_id, step_name)) in step_ids.iter().enumerate() {
        begin_pipeline_step_write(
            path,
            PipelineBeginStepRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "pipeline.step.begin".to_owned(),
                request_id: format!("{request_id}-b{index}"),
                idempotency_key: format!("{request_id}-b{index}-idem"),
                apply: true,
                run_id: run_id.clone(),
                step_id: step_id.clone(),
            },
        )?;
        // Simulate the manager's own recompute cost.
        std::thread::sleep(Duration::from_millis(STEP_COST_MS[index] as u64));
        complete_pipeline_step_write(
            path,
            PipelineCompleteStepRequest {
                network: NETWORK.to_owned(),
                chain_id: CHAIN_ID,
                operation: "pipeline.step.complete".to_owned(),
                request_id: format!("{request_id}-c{index}"),
                idempotency_key: format!("{request_id}-c{index}-idem"),
                apply: true,
                run_id: run_id.clone(),
                step_id: step_id.clone(),
                status: StepCompletionStatus::Completed,
                result: serde_json::json!({"source": "p0-4-probe"}),
                error: None,
            },
        )?;
        checkpoints.push(serde_json::json!({
            "stepIndex": index,
            "stepName": step_name.as_str(),
            "simulatedCostMs": STEP_COST_MS[index],
        }));
        // The version bump happens right after the first manager finished, so
        // the downstream projections observe a new upstream version.
        if bump_version && index == 0 {
            bump_candidate_versions(path)?;
        }
    }

    Ok(serde_json::json!({
        "requestId": request_id,
        "runId": run_id,
        "wallClockMs": started.elapsed().as_millis() as i64,
        "stepCount": step_ids.len(),
        "versionBumped": bump_version,
        "checkpoints": checkpoints,
    }))
}

fn bump_candidate_versions(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let connection = Connection::open(path)?;
    connection.execute(
        "UPDATE manager_dex_candidate_library_state
         SET candidate_version = candidate_version + 1",
        [],
    )?;
    connection.execute(
        "UPDATE manager_market_observation_library_state
         SET candidate_edge_version = candidate_edge_version + 1,
             observation_version = observation_version + 1",
        [],
    )?;
    connection.execute(
        "UPDATE manager_v4_closed_loop_library_state
         SET candidate_edge_version = candidate_edge_version + 1,
             observation_version = observation_version + 1",
        [],
    )?;
    Ok(())
}

fn seed(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let mut connection = Connection::open(path)?;
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
            "p0-4-probe",
            "fixture",
        )?;
    }
    drop(connection);

    let connection = Connection::open(path)?;
    connection.execute(
        "INSERT INTO pool_scope_config (
            network, chain_id, scope_key, pool_table,
            library_keys_json, library_versions_json,
            library_checksums_json, filter_mode,
            excluded_token_key_ids_json, enabled,
            materialized_checksum, last_rebuilt_at, created_at, updated_at
         ) VALUES (?1, ?2, ?3, 'v4_exchange_pools_core4',
                   '[\"core\"]', '{}', '{}', 'edge_any', '[]', 1,
                   'sha256:scope-v1', 'fixture', 'fixture', 'fixture')",
        params![NETWORK, CHAIN_ID, SCOPE_KEY],
    )?;
    connection.execute(
        "INSERT INTO manager_dex_candidate_library_state (
            network, chain_id, protocol, scope_key,
            candidate_version, materialized_checksum, pool_scope_checksum,
            pool_library_versions_json, pool_library_checksums_json,
            library_versions_json, library_checksums_json,
            anchor_token_count, candidate_token_count, eligible_pool_count,
            recommendation_count, abnormal_pool_count,
            generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, 1,
                   'sha256:candidate-edge-v1', 'sha256:scope-v1',
                   '{}', '{}', '{}', '{}',
                   1, 1, 1, 1, 0, 'fixture', 'fixture')",
        params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
    )?;
    connection.execute(
        "INSERT INTO manager_market_observation_library_state (
            network, chain_id, protocol, scope_key,
            observation_version, materialized_checksum,
            candidate_edge_version, candidate_edge_checksum, pool_scope_checksum,
            library_versions_json, library_checksums_json,
            anchor_count, token_count, direction_count, empty_direction_count,
            pool_count, abnormal_direction_count, abnormal_pool_count,
            generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, 1,
                   'sha256:candidate-exchange-v1', 1, 'sha256:candidate-edge-v1',
                   'sha256:scope-v1', '{}', '{}',
                   1, 1, 1, 0, 1, 0, 0, 'fixture', 'fixture')",
        params![NETWORK, CHAIN_ID, PROTOCOL, EXCHANGE_SCOPE_KEY],
    )?;
    connection.execute(
        "INSERT INTO manager_v4_closed_loop_library_state (
            network, chain_id, protocol, scope_key,
            closed_loop_version, materialized_checksum,
            candidate_edge_version, candidate_edge_checksum,
            observation_version, observation_checksum, pool_scope_checksum,
            library_versions_json, library_checksums_json,
            anchor_count, loop_count, hop_count, optimization_count,
            skipped_token_count, consumers_need_rebuild,
            generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, 1,
                   'sha256:closed-loop-v1', 1, 'sha256:candidate-edge-v1',
                   1, 'sha256:candidate-exchange-v1', 'sha256:scope-v1',
                   '{}', '{}',
                   1, 1, 3, 0, 0, 0, 'fixture', 'fixture')",
        params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
    )?;
    connection.execute(
        "INSERT INTO manager_review_library_state (
            network, chain_id, protocol, scope_key,
            review_version, materialized_checksum,
            closed_loop_version, closed_loop_checksum,
            candidate_edge_version, candidate_edge_checksum,
            observation_version, observation_checksum, pool_scope_checksum,
            library_versions_json, library_checksums_json,
            loop_count, reviewed_count, completed_count, data_incomplete_count,
            not_executable_count, failed_count, broadcast_request_count,
            generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, 1,
                   'sha256:review-v1', 1, 'sha256:closed-loop-v1',
                   1, 'sha256:candidate-edge-v1',
                   1, 'sha256:candidate-exchange-v1', 'sha256:scope-v1',
                   '{}', '{}',
                   1, 1, 1, 0, 0, 0, 0, 'fixture', 'fixture')",
        params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
    )?;
    connection.execute(
        "INSERT INTO manager_broadcast_library_state (
            network, chain_id, protocol, scope_key,
            broadcast_version, materialized_checksum,
            review_version, review_checksum, closed_loop_version, closed_loop_checksum,
            request_count, attempt_count, pending_count, success_count,
            failed_count, terminal_count,
            generated_at, updated_at
         ) VALUES (?1, ?2, ?3, ?4, 1,
                   'sha256:broadcast-v1', 1, 'sha256:review-v1',
                   1, 'sha256:closed-loop-v1',
                   0, 0, 0, 0, 0, 0, 'fixture', 'fixture')",
        params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY],
    )?;
    drop(connection);
    Ok(())
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
            .expect("fixture token key id is valid"),
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
