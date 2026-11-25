//! Measure the cost of the directional projection without writing it.
//!
//! The performance baseline has to say how long the directional layer takes
//! to project and how long the published table takes to load.  Both are
//! measured here on the **real** store, opened read-only: the projection
//! runs with `apply = false` and the snapshot is only read.
//!
//! Usage:
//!     cargo run --offline -p directional-pool-manager --example materialization_timing -- <store.db> [network] [chain-id]

use std::time::Instant;

use directional_pool_manager::{
    materialize_directional_pools, read_directional_pool_snapshot, MAX_SNAPSHOT_LIMIT,
};

fn main() {
    let mut arguments = std::env::args().skip(1);
    let database_path = arguments
        .next()
        .unwrap_or_else(|| "flashloan/db/local_store_4663.sqlite3".to_owned());
    let network = arguments.next().unwrap_or_else(|| "robinhood".to_owned());
    let chain_id: i64 = arguments
        .next()
        .unwrap_or_else(|| "4663".to_owned())
        .parse()
        .expect("the chain id must be a number");
    let scopes = ["core4", "candidate_exchange"];

    let mut report = serde_json::json!({
        "database": database_path,
        "network": network,
        "chainId": chain_id,
        "mode": "read-only (apply = false; the store is opened read-only)",
        "scopes": [],
    });
    let mut scope_reports = Vec::new();

    for scope in scopes {
        let mut projection_seconds = Vec::new();
        let mut result = None;
        for _ in 0..3 {
            let started = Instant::now();
            let materialized = materialize_directional_pools(
                &database_path,
                &network,
                chain_id,
                scope,
                false,
            )
            .unwrap_or_else(|error| panic!("the projection failed: {error}"));
            projection_seconds.push(started.elapsed().as_secs_f64());
            result = Some(materialized);
        }

        let started = Instant::now();
        let snapshot = read_directional_pool_snapshot(
            &database_path,
            &network,
            chain_id,
            scope,
            None,
            MAX_SNAPSHOT_LIMIT,
        )
        .unwrap_or_else(|error| panic!("the snapshot failed: {error}"));
        let load_seconds = started.elapsed().as_secs_f64();

        let materialized = result.expect("the projection ran at least once");
        scope_reports.push(serde_json::json!({
            "scopeKey": scope,
            "projectionSeconds": projection_seconds,
            "projectionMinSeconds": projection_seconds.iter().cloned().fold(f64::MAX, f64::min),
            "projectionMaxSeconds": projection_seconds.iter().cloned().fold(0.0, f64::max),
            "tableLoadSeconds": load_seconds,
            "loadedRows": snapshot.rows.len(),
            "directionalPoolCount": materialized.directional_pool_count,
            "basePoolCount": materialized.base_pool_count,
            "direction0Count": materialized.direction0_count,
            "direction1Count": materialized.direction1_count,
            "snapshotChecksum": materialized.snapshot_checksum,
            "poolScopeChecksum": materialized.pool_scope_checksum,
        }));
    }

    report["scopes"] = serde_json::Value::Array(scope_reports);
    println!("{}", serde_json::to_string_pretty(&report).expect("the report serialises"));
}
