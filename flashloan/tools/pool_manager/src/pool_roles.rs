//! Pool role retention policy for the pool manager.
//!
//! The runtime keeps three distinct pool roles:
//!
//! * **candidate** — the best priced pools per direction. At most five per
//!   direction are kept, and they are refreshed dynamically.
//! * **recommendation** — the single best priced pool per direction. It is a
//!   strict subset of the candidates and is refreshed dynamically as well.
//! * **backup** — every other pool that still trades. Backups are retained
//!   while the pool traded within the retention window and are pruned when
//!   the scheduled cleanup runs.
//!
//! The candidates and the recommendations are never deleted by the cleanup:
//! they are refreshed from the chain instead, because a pool that stops being
//! the best priced one is still a valid pool for the next refresh. The
//! cleanup only prunes the backup set, and only when the pool has been
//! inactive for the whole retention window.
//!
//! The cleanup cadence is every three days at 24:00 local time. The window
//! is anchored on the same epoch so that a manual run and the scheduled run
//! agree on which pools are inside the window.

use rusqlite::{params, Connection, OpenFlags};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::time::Duration;

/// The role a pool plays in the candidate selection.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum PoolRole {
    /// One of the best priced pools of a direction.
    Candidate,
    /// The best priced pool of a direction.
    Recommendation,
    /// Any other pool that may be promoted later.
    Backup,
}

impl PoolRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Candidate => "candidate",
            Self::Recommendation => "recommendation",
            Self::Backup => "backup",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "candidate" => Some(Self::Candidate),
            "recommendation" => Some(Self::Recommendation),
            "backup" => Some(Self::Backup),
            _ => None,
        }
    }
}

/// Number of days a backup pool is retained without a trade.
pub const BACKUP_RETENTION_DAYS: i64 = 3;

/// Number of days between two cleanup runs.
pub const CLEANUP_INTERVAL_DAYS: i64 = 3;

/// Hour of the day at which the cleanup is allowed to run.
pub const CLEANUP_HOUR: u32 = 0;

/// The table that carries the last observed trade of every pool.
pub const TRADE_ACTIVITY_TABLE: &str = "pool_trade_activity";

/// The table that carries the role assignment of every pool.
pub const POOL_ROLE_TABLE: &str = "pool_role_assignment";

/// The V4 `Swap` event topic.
///
/// `Swap(PoolId indexed id, address indexed sender, int128 amount0, int128 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick, uint24 fee)`
#[derive(Clone, Copy, Debug)]
pub struct PoolRoleOptions {
    pub retention_days: i64,
    pub cleanup_interval_days: i64,
    pub cleanup_hour: u32,
    pub apply: bool,
    /// Unix seconds used as "now". `None` uses the system clock.
    pub now_unix: Option<i64>,
    /// Timezone offset in seconds used for the 24:00 boundary.
    pub utc_offset_seconds: i64,
    /// The startup gate. The cleanup only runs when the operator enabled it.
    ///
    /// During the first phase of the rollout there is almost no data, so a
    /// cleanup would either do nothing or, worse, remove pools that have not
    /// been measured yet. The cleanup therefore stays off until the runtime
    /// has grown and the operator turns it on explicitly.
    pub cleanup_enabled: bool,
    /// Minimum number of known pools before the cleanup may run.
    pub min_pools: i64,
    /// Minimum number of trade activity rows before the cleanup may run.
    ///
    /// Without observed trades there is no evidence that any pool traded
    /// inside the retention window, so every backup would look idle.
    pub min_trade_observations: i64,
}

impl Default for PoolRoleOptions {
    fn default() -> Self {
        Self {
            retention_days: BACKUP_RETENTION_DAYS,
            cleanup_interval_days: CLEANUP_INTERVAL_DAYS,
            cleanup_hour: CLEANUP_HOUR,
            apply: false,
            now_unix: None,
            utc_offset_seconds: 8 * 3600,
            cleanup_enabled: false,
            min_pools: 500,
            min_trade_observations: 100,
        }
    }
}

/// One pool with its role and its trading evidence.
#[derive(Clone, Debug)]
pub struct PoolRoleRow {
    pub pool_id: String,
    pub role: PoolRole,
    pub anchor_symbol: String,
    pub direction_key: String,
    pub hop: String,
    pub rank: Option<i64>,
    pub last_swap_block: Option<i64>,
    pub last_swap_at: Option<String>,
}

/// The decision taken for one pool by the cleanup.
#[derive(Clone, Debug)]
pub struct PoolRoleDecision {
    pub pool_id: String,
    pub role: PoolRole,
    pub keep: bool,
    pub reason: &'static str,
    pub last_swap_at: Option<String>,
    pub age_days: Option<i64>,
}

/// The report returned by the role cleanup.
#[derive(Clone, Debug, Default)]
pub struct PoolRoleCleanupReport {
    pub network: String,
    pub chain_id: i64,
    pub apply: bool,
    pub cleanup_window_start: String,
    pub cleanup_window_end: String,
    pub candidate_total: usize,
    pub recommendation_total: usize,
    pub backup_total: usize,
    pub backups_kept: usize,
    pub backups_pruned: usize,
    pub decisions: Vec<PoolRoleDecision>,
    /// Whether the cleanup actually ran.
    pub executed: bool,
    /// Why the cleanup was skipped, when it was.
    pub skip_reason: Option<String>,
    /// The known pool count observed by the gate.
    pub known_pool_total: usize,
    /// The trade activity rows observed by the gate.
    pub trade_observation_total: usize,
}

/// Creates the role and trade activity tables when they are missing.
///
/// The operation is idempotent: the columns are inspected first, so the
/// schema can be repaired without dropping any recorded evidence.
pub fn ensure_pool_role_schema(connection: &Connection) -> Result<Vec<&'static str>, String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS pool_trade_activity (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
                pool_id TEXT NOT NULL,
                last_swap_block INTEGER,
                last_swap_at TEXT,
                swap_count INTEGER,
                observed_at TEXT NOT NULL,
                refreshed_at TEXT NOT NULL,
                evidence_source TEXT,
                PRIMARY KEY (network, chain_id, protocol, pool_id)
            );
            CREATE TABLE IF NOT EXISTS pool_role_assignment (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
                pool_id TEXT NOT NULL,
                role TEXT NOT NULL,
                anchor_symbol TEXT NOT NULL DEFAULT '',
                counter_address TEXT NOT NULL DEFAULT '',
                direction_key TEXT NOT NULL DEFAULT '',
                hop TEXT NOT NULL DEFAULT '',
                rank INTEGER,
                assigned_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (network, chain_id, protocol, pool_id)
            );
            CREATE INDEX IF NOT EXISTS idx_pool_role_assignment_scope_role
                ON pool_role_assignment(network, chain_id, protocol, role);
            CREATE INDEX IF NOT EXISTS idx_pool_trade_activity_scope_block
                ON pool_trade_activity(network, chain_id, protocol, last_swap_block);",
        )
        .map_err(|error| error.to_string())?;
    Ok(vec![TRADE_ACTIVITY_TABLE, POOL_ROLE_TABLE])
}

/// Reads the trade activity rows of a scope.
pub fn read_trade_activity(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, (Option<i64>, Option<String>, Option<i64>)>, String> {
    let mut statement = match connection.prepare(
        "SELECT pool_id, last_swap_block, last_swap_at, swap_count
         FROM pool_trade_activity
         WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
    ) {
        Ok(statement) => statement,
        Err(error) => {
            if error
                .to_string()
                .contains("no such table: pool_trade_activity")
            {
                return Ok(BTreeMap::new());
            }
            return Err(error.to_string());
        }
    };
    let rows = statement
        .query_map(params![network, chain_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<i64>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<i64>>(3)?,
            ))
        })
        .map_err(|error| error.to_string())?;
    let mut activity = BTreeMap::new();
    for row in rows {
        let (pool_id, block, at, count) = row.map_err(|error| error.to_string())?;
        activity.insert(pool_id, (block, at, count));
    }
    Ok(activity)
}

/// Writes the trade activity evidence for a scope.
///
/// The rows are upserted: a newer observation replaces the older one, and a
/// pool without a trade in the scanned window keeps its previous record
/// because "not observed" must never become "never traded".
pub fn write_trade_activity(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    observations: &[(String, i64, String, i64)],
    observed_at: &str,
) -> Result<usize, String> {
    let mut statement = connection
        .prepare(
            "INSERT INTO pool_trade_activity (
                 network, chain_id, protocol, pool_id, last_swap_block,
                 last_swap_at, swap_count, observed_at, refreshed_at, evidence_source
             ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, ?5, ?6, ?7, ?7, 'v4_swap_logs')
             ON CONFLICT (network, chain_id, protocol, pool_id) DO UPDATE SET
                 last_swap_block = excluded.last_swap_block,
                 last_swap_at = excluded.last_swap_at,
                 swap_count = COALESCE(excluded.swap_count, pool_trade_activity.swap_count),
                 observed_at = excluded.observed_at,
                 refreshed_at = excluded.refreshed_at,
                 evidence_source = excluded.evidence_source
             WHERE excluded.last_swap_block >= COALESCE(pool_trade_activity.last_swap_block, -1)",
        )
        .map_err(|error| error.to_string())?;
    let mut written = 0usize;
    for (pool_id, block, at, count) in observations {
        written += statement
            .execute(params![
                network,
                chain_id,
                pool_id,
                block,
                at,
                count,
                observed_at
            ])
            .map_err(|error| error.to_string())? as usize;
    }
    Ok(written)
}

/// Assigns the pool roles from the current candidate and recommendation rows.
///
/// The candidates and the recommendations are read from the tables the
/// candidate edge manager materialises. Every other known pool becomes a
/// backup. The assignment is a full refresh of the role table, so a pool
/// that lost its candidate role is demoted to a backup instead of keeping a
/// stale role forever.
pub fn materialize_pool_roles(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    now: &str,
) -> Result<Value, String> {
    ensure_pool_role_schema(connection)?;
    let mut candidate_rows = connection
        .prepare(
            "SELECT DISTINCT pool_id FROM dex_candidate_pools
             WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
        )
        .map_err(|error| error.to_string())?;
    let candidates = candidate_rows
        .query_map(params![network, chain_id], |row| row.get::<_, String>(0))
        .map_err(|error| error.to_string())?
        .collect::<Result<BTreeSet<String>, _>>()
        .map_err(|error| error.to_string())?;

    let mut recommendation_rows = connection
        .prepare(
            "SELECT DISTINCT pool_id FROM dex_candidate_pool_recommendations
             WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
        )
        .map_err(|error| error.to_string())?;
    let recommendations = recommendation_rows
        .query_map(params![network, chain_id], |row| row.get::<_, String>(0))
        .map_err(|error| error.to_string())?
        .collect::<Result<BTreeSet<String>, _>>()
        .map_err(|error| error.to_string())?;

    let mut known_rows = connection
        .prepare(
            // A dynamic-fee pool is never a backup: its effective rate is
            // decided by the hook at swap time, so the off-chain depth
            // arithmetic cannot price it. The backup role is therefore
            // restricted to the fixed-fee pools.
            "SELECT pool_id FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'
               AND dynamic_fee = 0 AND special_fee = 0
               AND (fee & ?) = 0",
        )
        .map_err(|error| error.to_string())?;
    let known = known_rows
        .query_map(
            params![
                network,
                chain_id,
                i64::from(manager_contracts::V4_DYNAMIC_FEE_FLAG)
            ],
            |row| row.get::<_, String>(0),
        )
        .map_err(|error| error.to_string())?
        .collect::<Result<BTreeSet<String>, _>>()
        .map_err(|error| error.to_string())?;

    let mut assignments = 0usize;
    let mut demoted = 0usize;
    {
        let mut delete_statement = connection
            .prepare(
                "DELETE FROM pool_role_assignment
                 WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
            )
            .map_err(|error| error.to_string())?;
        delete_statement
            .execute(params![network, chain_id])
            .map_err(|error| error.to_string())?;
        let mut insert_statement = connection
            .prepare(
                "INSERT INTO pool_role_assignment (
                     network, chain_id, protocol, pool_id, role, anchor_symbol,
                     counter_address, direction_key, hop, rank, assigned_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', ?3, ?4, '', '', '', '', NULL, ?5, ?5)",
            )
            .map_err(|error| error.to_string())?;
        for pool_id in &known {
            let role = if recommendations.contains(pool_id) {
                PoolRole::Recommendation
            } else if candidates.contains(pool_id) {
                PoolRole::Candidate
            } else {
                PoolRole::Backup
            };
            insert_statement
                .execute(params![network, chain_id, pool_id, role.as_str(), now])
                .map_err(|error| error.to_string())?;
            assignments += 1;
            if role == PoolRole::Backup {
                demoted += 1;
            }
        }
    }
    Ok(json!({
        "assigned": assignments,
        "candidates": candidates.len(),
        "recommendations": recommendations.len(),
        "backups": demoted,
        "network": network,
        "chainId": chain_id,
    }))
}

/// Computes the cleanup window boundaries.
///
/// The window is anchored on the local midnight that closes the current
/// three-day cycle. A cleanup run therefore always covers exactly
/// `retention_days` days and the boundaries do not move when the cleanup is
/// executed at a different hour of the day.
pub fn cleanup_window(options: &PoolRoleOptions) -> Result<(String, String), String> {
    let now = options.now_unix.unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_secs() as i64)
            .unwrap_or(0)
    });
    let local = now + options.utc_offset_seconds;
    let seconds_per_day = 86_400i64;
    let day_index = local.div_euclid(seconds_per_day);
    let midnight_local = day_index * seconds_per_day;
    let window_end_local = midnight_local + options.cleanup_hour as i64 * 3600;
    let window_start_local = window_end_local - options.retention_days.max(1) * seconds_per_day;
    let format = |value: i64| -> String {
        chrono::DateTime::from_timestamp(value - options.utc_offset_seconds, 0)
            .map(|value| value.format("%Y-%m-%dT%H:%M:%SZ").to_string())
            .unwrap_or_default()
    };
    Ok((format(window_start_local), format(window_end_local)))
}

/// Counts the pools the runtime knows about.
fn known_pool_count(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<usize, String> {
    // A store that does not carry the pool table yet has no data to clean.
    // The missing table is a coverage gap, not a failure.
    let count: i64 = match connection.query_row(
        "SELECT COUNT(*) FROM v4_exchange_pools
         WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
        params![network, chain_id],
        |row| row.get(0),
    ) {
        Ok(value) => value,
        Err(rusqlite::Error::QueryReturnedNoRows) => 0,
        Err(error) => {
            if error
                .to_string()
                .contains("no such table: v4_exchange_pools")
            {
                0
            } else {
                return Err(error.to_string());
            }
        }
    };
    Ok(count as usize)
}

/// Decides whether the cleanup may run.
///
/// The cleanup is skipped when the operator did not enable it, when the pool
/// set is still small, or when there is not enough trade evidence. In every
/// skipped case the cleanup deletes nothing.
pub fn evaluate_cleanup_gate(
    options: &PoolRoleOptions,
    known_pool_total: usize,
    trade_observation_total: usize,
) -> Option<String> {
    if !options.cleanup_enabled {
        return Some("cleanup_disabled_by_startup_gate".to_string());
    }
    if (known_pool_total as i64) < options.min_pools.max(0) {
        return Some(format!(
            "insufficient_pool_count:{}<{}",
            known_pool_total,
            options.min_pools.max(0)
        ));
    }
    if (trade_observation_total as i64) < options.min_trade_observations.max(0) {
        return Some(format!(
            "insufficient_trade_observations:{}<{}",
            trade_observation_total,
            options.min_trade_observations.max(0)
        ));
    }
    None
}

/// Runs the role cleanup.
///
/// The candidates and the recommendations are never deleted: they are
/// refreshed dynamically. Only the backups are pruned, and only when the
/// pool has no trade inside the retention window.
pub fn cleanup_pool_roles(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    options: &PoolRoleOptions,
) -> Result<PoolRoleCleanupReport, String> {
    // The schema is only repaired on a writable connection. A read only run
    // reports the state of a store that already carries the tables.
    if options.apply {
        ensure_pool_role_schema(connection)?;
    }
    let (window_start, window_end) = cleanup_window(options)?;
    let activity = read_trade_activity(connection, network, chain_id)?;

    let mut statement = match connection.prepare(
        "SELECT pool_id, role FROM pool_role_assignment
         WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'
         ORDER BY pool_id",
    ) {
        Ok(statement) => statement,
        Err(error) => {
            if error
                .to_string()
                .contains("no such table: pool_role_assignment")
            {
                let (window_start, window_end) = cleanup_window(options)?;
                return Ok(PoolRoleCleanupReport {
                    network: network.to_owned(),
                    chain_id,
                    apply: options.apply,
                    cleanup_window_start: window_start,
                    cleanup_window_end: window_end,
                    skip_reason: Some("pool_role_assignment_missing".to_string()),
                    ..PoolRoleCleanupReport::default()
                });
            }
            return Err(error.to_string());
        }
    };
    let roles = statement
        .query_map(params![network, chain_id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<(String, String)>, _>>()
        .map_err(|error| error.to_string())?;

    let now = options.now_unix.unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_secs() as i64)
            .unwrap_or(0)
    });

    let mut report = PoolRoleCleanupReport {
        network: network.to_owned(),
        chain_id,
        apply: options.apply,
        cleanup_window_start: window_start,
        cleanup_window_end: window_end,
        ..PoolRoleCleanupReport::default()
    };

    // The startup gate. The cleanup is a maintenance action for a runtime
    // that already carries a lot of data, so it is skipped while the data is
    // still thin. A skipped cleanup deletes nothing at all.
    let known_pool_total = known_pool_count(connection, network, chain_id)?;
    let trade_observation_total = activity.len();
    report.known_pool_total = known_pool_total;
    report.trade_observation_total = trade_observation_total;
    let gate = evaluate_cleanup_gate(options, known_pool_total, trade_observation_total);
    if let Some(reason) = gate {
        report.skip_reason = Some(reason);
        return Ok(report);
    }
    report.executed = true;

    let mut pruned_pool_ids = Vec::new();
    for (pool_id, role_text) in roles {
        let role = PoolRole::parse(&role_text).unwrap_or(PoolRole::Backup);
        let (_last_swap_block, last_swap_at, _count) = activity
            .get(&pool_id)
            .cloned()
            .unwrap_or((None, None, None));
        let age_days = last_swap_at
            .as_deref()
            .and_then(|value| chrono::DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.timestamp())
            .map(|timestamp| (now - timestamp) / 86_400);
        let (keep, reason) = match role {
            PoolRole::Candidate => (true, "candidate_kept"),
            PoolRole::Recommendation => (true, "recommendation_kept"),
            PoolRole::Backup => match age_days {
                Some(age) if age < options.retention_days.max(1) => {
                    (true, "backup_traded_in_window")
                }
                Some(_) => (false, "backup_idle_beyond_window"),
                None => (false, "backup_without_trade_evidence"),
            },
        };
        match role {
            PoolRole::Candidate => report.candidate_total += 1,
            PoolRole::Recommendation => report.recommendation_total += 1,
            PoolRole::Backup => report.backup_total += 1,
        }
        if keep {
            if role == PoolRole::Backup {
                report.backups_kept += 1;
            }
        } else {
            report.backups_pruned += 1;
            pruned_pool_ids.push(pool_id.clone());
        }
        report.decisions.push(PoolRoleDecision {
            pool_id,
            role,
            keep,
            reason,
            last_swap_at,
            age_days,
        });
    }

    if options.apply && !pruned_pool_ids.is_empty() {
        let mut delete_statement = connection
            .prepare(
                "DELETE FROM pool_role_assignment
                 WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4' AND pool_id = ?",
            )
            .map_err(|error| error.to_string())?;
        for pool_id in &pruned_pool_ids {
            delete_statement
                .execute(params![network, chain_id, pool_id])
                .map_err(|error| error.to_string())?;
        }
        // The trade evidence is kept: a pool that is pruned from the backup
        // set must still be able to prove that it traded when it returns.
    }
    Ok(report)
}

/// The JSON report of a role cleanup.
pub fn role_cleanup_report(report: &PoolRoleCleanupReport) -> Value {
    json!({
        "network": report.network,
        "chainId": report.chain_id,
        "apply": report.apply,
        "cleanupWindowStart": report.cleanup_window_start,
        "cleanupWindowEnd": report.cleanup_window_end,
        "candidateTotal": report.candidate_total,
        "recommendationTotal": report.recommendation_total,
        "backupTotal": report.backup_total,
        "backupsKept": report.backups_kept,
        "backupsPruned": report.backups_pruned,
        "executed": report.executed,
        "skipReason": report.skip_reason,
        "knownPoolTotal": report.known_pool_total,
        "tradeObservationTotal": report.trade_observation_total,
        "decisions": report.decisions.iter().map(|decision| json!({
            "poolId": decision.pool_id,
            "role": decision.role.as_str(),
            "keep": decision.keep,
            "reason": decision.reason,
            "lastSwapAt": decision.last_swap_at,
            "ageDays": decision.age_days,
        })).collect::<Vec<Value>>(),
    })
}

/// Runs the pool role maintenance for the runtime.
///
/// The action decides what happens:
///
/// * `status` reports the role counts and the gate state;
/// * `materialize` refreshes the role assignment from the current candidate
///   and recommendation rows;
/// * `cleanup` prunes the backup pools that did not trade inside the
///   retention window. The startup gate can stop the cleanup before it
///   touches a single row.
pub fn run_pool_role_maintenance(
    database_path: &str,
    network: &str,
    chain_id: i64,
    protocol: &str,
    action: &str,
    options: PoolRoleOptions,
) -> Result<Value, String> {
    let read_only = !options.apply && action != "materialize";
    let connection = open_role_store(database_path, read_only)?;
    match action.trim().to_ascii_lowercase().as_str() {
        "status" => {
            let known_pool_total = known_pool_count(&connection, network, chain_id)?;
            let trade_observation_total =
                read_trade_activity(&connection, network, chain_id)?.len();
            let gate = evaluate_cleanup_gate(&options, known_pool_total, trade_observation_total);
            let counts = role_counts(&connection, network, chain_id)?;
            Ok(json!({
                "action": "status",
                "network": network,
                "chainId": chain_id,
                "protocol": protocol,
                "knownPoolTotal": known_pool_total,
                "tradeObservationTotal": trade_observation_total,
                "roles": counts,
                "cleanupGate": {
                    "enabled": options.cleanup_enabled,
                    "minPools": options.min_pools,
                    "minTradeObservations": options.min_trade_observations,
                    "skipReason": gate,
                },
            }))
        }
        "materialize" => {
            let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
            let mut result = materialize_pool_roles(&connection, network, chain_id, &now)?;
            result["action"] = json!("materialize");
            Ok(result)
        }
        "cleanup" => {
            let report = cleanup_pool_roles(&connection, network, chain_id, &options)?;
            let mut value = role_cleanup_report(&report);
            value["action"] = json!("cleanup");
            Ok(value)
        }
        other => Err(format!(
            "unknown pool role action: {other}; expected status, materialize or cleanup"
        )),
    }
}

/// Counts the pools per role.
fn role_counts(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<Value>, String> {
    let mut statement = match connection.prepare(
        "SELECT role, COUNT(*) FROM pool_role_assignment
         WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'
         GROUP BY role ORDER BY role",
    ) {
        Ok(statement) => statement,
        Err(error) => {
            if error
                .to_string()
                .contains("no such table: pool_role_assignment")
            {
                return Ok(Vec::new());
            }
            return Err(error.to_string());
        }
    };
    let rows = statement
        .query_map(params![network, chain_id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<(String, i64)>, _>>()
        .map_err(|error| error.to_string())?;
    Ok(rows
        .into_iter()
        .map(|(role, count)| json!({"role": role, "count": count}))
        .collect())
}

/// Opens the database for a role maintenance run.
pub fn open_role_store(store: &str, read_only: bool) -> Result<Connection, String> {
    let flags = if read_only {
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI
    } else {
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI
    };
    let connection =
        Connection::open_with_flags(store, flags).map_err(|error| error.to_string())?;
    connection
        .busy_timeout(Duration::from_secs(60))
        .map_err(|error| error.to_string())?;
    Ok(connection)
}

/// The RPC topic of the Uniswap V4 `Swap` event.
///
/// `Swap(PoolId indexed id, address indexed sender, int128 amount0, int128 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick, uint24 fee)`
pub const V4_SWAP_TOPIC: &str =
    "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f";

/// Computes the Uniswap V4 `Swap` event topic from its signature.
pub fn v4_swap_topic() -> String {
    use tiny_keccak::{Hasher, Keccak};
    let mut hasher = Keccak::v256();
    hasher.update(b"Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)");
    let mut output = [0u8; 32];
    hasher.finalize(&mut output);
    format!("0x{}", hex::encode(output))
}

/// The observed swap activity of one pool.
#[derive(Clone, Debug, Default)]
pub struct SwapActivity {
    pub pool_id: String,
    pub last_swap_block: i64,
    pub last_swap_at: String,
    pub swap_count: i64,
}

/// The outcome of a swap activity scan.
#[derive(Clone, Debug, Default)]
pub struct SwapScanReport {
    pub scanned_pools: usize,
    pub active_pools: usize,
    pub total_logs: usize,
    pub from_block: i64,
    pub to_block: i64,
    pub rpc_url: String,
    pub observations: Vec<SwapActivity>,
    pub errors: Vec<String>,
}

/// The parameters of a swap activity scan.
#[derive(Clone, Debug)]
pub struct SwapScanOptions {
    pub rpc_url: String,
    pub pool_manager: String,
    pub from_block: i64,
    pub to_block: i64,
    pub chunk_size: i64,
    pub timeout_seconds: u64,
    /// The maximum request rate against the node.
    pub requests_per_second: f64,
}

/// Collects the swap activity of the pools from the chain.
///
/// The scan reads the V4 `Swap` logs of the pool manager and records the
/// newest block and timestamp per pool. The evidence is the input of the
/// backup retention window: a pool that did not trade inside the window is
/// not a backup any more.
pub async fn scan_swap_activity(options: &SwapScanOptions) -> Result<SwapScanReport, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(options.timeout_seconds.max(1)))
        .build()
        .map_err(|error| error.to_string())?;
    let limiter = RateLimiter::new(options.requests_per_second);
    let topic = v4_swap_topic();
    let mut report = SwapScanReport {
        from_block: options.from_block.max(0),
        to_block: options.to_block,
        rpc_url: options.rpc_url.clone(),
        ..SwapScanReport::default()
    };
    let mut cursor = options.from_block.max(0);
    let mut step = options.chunk_size.max(1);
    let mut last_error: Option<String> = None;
    while cursor <= options.to_block {
        let end = options.to_block.min(cursor + step - 1);
        let payload = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [{
                "address": options.pool_manager,
                "fromBlock": format!("0x{cursor:x}"),
                "toBlock": format!("0x{end:x}"),
                "topics": [topic],
            }],
        });
        limiter.acquire().await;
        let response = client
            .post(&options.rpc_url)
            .json(&payload)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let status = response.status();
        let body = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            last_error = Some(format!("rpc http {status}"));
            if step > 1 {
                step = (step / 2).max(1);
                continue;
            }
            break;
        }
        let parsed: Value = serde_json::from_str(&body).map_err(|error| error.to_string())?;
        if let Some(error) = parsed.get("error") {
            last_error = Some(error.to_string());
            if step > 1 {
                step = (step / 2).max(1);
                continue;
            }
            break;
        }
        let logs = parsed
            .get("result")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        report.total_logs += logs.len();
        for log in logs {
            let Some(pool_topic) = log
                .get("topics")
                .and_then(Value::as_array)
                .and_then(|topics| topics.get(1))
                .and_then(Value::as_str)
            else {
                continue;
            };
            let pool_id = pool_topic.to_ascii_lowercase();
            let block = log
                .get("blockNumber")
                .and_then(Value::as_str)
                .and_then(|value| u64::from_str_radix(value.trim_start_matches("0x"), 16).ok())
                .unwrap_or(0) as i64;
            let timestamp = log
                .get("timestamp")
                .and_then(Value::as_str)
                .and_then(|value| u64::from_str_radix(value.trim_start_matches("0x"), 16).ok())
                .map(|value| value as i64)
                .unwrap_or_default();
            let observed_at = if timestamp > 0 {
                chrono::DateTime::from_timestamp(timestamp, 0)
                    .map(|value| value.format("%Y-%m-%dT%H:%M:%SZ").to_string())
                    .unwrap_or_default()
            } else {
                String::new()
            };
            match report
                .observations
                .iter_mut()
                .find(|item| item.pool_id == pool_id)
            {
                Some(existing) => {
                    if block >= existing.last_swap_block {
                        existing.last_swap_block = block;
                        existing.last_swap_at = observed_at.clone();
                    }
                    existing.swap_count += 1;
                }
                None => report.observations.push(SwapActivity {
                    pool_id,
                    last_swap_block: block,
                    last_swap_at: observed_at,
                    swap_count: 1,
                }),
            }
        }
        cursor = end + 1;
        if step < options.chunk_size.max(1) {
            step = (step * 2).min(options.chunk_size.max(1));
        }
    }
    if let Some(error) = last_error {
        if report.observations.is_empty() {
            return Err(error);
        }
        report.errors.push(error);
    }
    report.active_pools = report.observations.len();
    Ok(report)
}

/// The serialisable summary of a swap scan.
pub fn swap_scan_report(report: &SwapScanReport) -> Value {
    json!({
        "rpcUrl": report.rpc_url,
        "fromBlock": report.from_block,
        "toBlock": report.to_block,
        "totalLogs": report.total_logs,
        "activePools": report.active_pools,
        "scannedPools": report.scanned_pools,
        "errors": report.errors,
        "observations": report.observations.iter().map(|item| json!({
            "poolId": item.pool_id,
            "lastSwapBlock": item.last_swap_block,
            "lastSwapAt": item.last_swap_at,
            "swapCount": item.swap_count,
        })).collect::<Vec<Value>>(),
    })
}

// ---------------------------------------------------------------------------
// Block timestamps
// ---------------------------------------------------------------------------

/// Reads the timestamp of a block from the chain.
///
/// The websocket log subscription does not carry a timestamp, so the trade
/// time has to be resolved from the block itself. The newest block of a
/// monitor run is resolved only once: every pool of the run shares it,
/// because the retention window compares days, not seconds.
pub async fn read_block_timestamps(
    rpc_url: &str,
    blocks: &[i64],
    timeout_seconds: u64,
) -> Result<BTreeMap<i64, String>, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_seconds.max(1)))
        .build()
        .map_err(|error| error.to_string())?;
    let limiter = RateLimiter::new(DEFAULT_REQUESTS_PER_SECOND);
    let mut wanted = blocks
        .iter()
        .copied()
        .filter(|block| *block > 0)
        .collect::<Vec<i64>>();
    wanted.sort_unstable();
    wanted.dedup();
    let mut timestamps = BTreeMap::new();
    if wanted.is_empty() {
        return Ok(timestamps);
    }
    // The blocks are read in batches: one request per block would dominate
    // the run time of the monitor and invites the rate limit of the node.
    for chunk in wanted.chunks(20) {
        limiter.acquire().await;
        let payload = chunk
            .iter()
            .enumerate()
            .map(|(index, block)| {
                json!({
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "eth_getBlockByNumber",
                    "params": [format!("0x{block:x}"), false],
                })
            })
            .collect::<Vec<Value>>();
        let response = client
            .post(rpc_url)
            .json(&payload)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let body = response.text().await.map_err(|error| error.to_string())?;
        let parsed: Value = serde_json::from_str(&body).map_err(|error| error.to_string())?;
        let entries = match parsed {
            Value::Array(entries) => entries,
            single => vec![single],
        };
        for entry in entries {
            let Some(index) = entry.get("id").and_then(Value::as_u64) else {
                continue;
            };
            let Some(block) = chunk.get(index as usize) else {
                continue;
            };
            let Some(timestamp) = entry
                .get("result")
                .and_then(|block| block.get("timestamp"))
                .and_then(Value::as_str)
                .and_then(|value| u64::from_str_radix(value.trim_start_matches("0x"), 16).ok())
            else {
                continue;
            };
            let formatted = chrono::DateTime::from_timestamp(timestamp as i64, 0)
                .map(|value| value.format("%Y-%m-%dT%H:%M:%SZ").to_string())
                .unwrap_or_default();
            if !formatted.is_empty() {
                timestamps.insert(*block, formatted);
            }
        }
    }
    Ok(timestamps)
}

// ---------------------------------------------------------------------------
// WebSocket monitoring
// ---------------------------------------------------------------------------

/// A token bucket that keeps the request rate inside the node budget.
///
/// The public endpoint allows 15 requests per second. A refresh driver that
/// bursts past that limit gets throttled by the node, so the limit is
/// enforced here instead of being discovered through failed calls.
#[derive(Clone, Debug)]
pub struct RateLimiter {
    /// Requests per second.
    pub requests_per_second: f64,
    /// The next moment a request may start, in milliseconds.
    next_slot_ms: std::sync::Arc<std::sync::Mutex<i64>>,
    /// The monotonic clock used for the slots.
    origin: std::time::Instant,
}

impl RateLimiter {
    /// Creates a limiter for the given request rate.
    pub fn new(requests_per_second: f64) -> Self {
        let requests_per_second = if requests_per_second.is_finite() && requests_per_second > 0.0 {
            requests_per_second
        } else {
            DEFAULT_REQUESTS_PER_SECOND
        };
        Self {
            requests_per_second,
            next_slot_ms: std::sync::Arc::new(std::sync::Mutex::new(0)),
            origin: std::time::Instant::now(),
        }
    }

    /// Waits until the next request may start.
    pub async fn acquire(&self) {
        let interval_ms = (1000.0 / self.requests_per_second).max(1.0) as i64;
        let wait_ms = {
            let mut slot = self.next_slot_ms.lock().expect("rate limiter");
            let now = self.origin.elapsed().as_millis() as i64;
            let start = (*slot).max(now);
            *slot = start + interval_ms;
            start - now
        };
        if wait_ms > 0 {
            tokio::time::sleep(std::time::Duration::from_millis(wait_ms as u64)).await;
        }
    }

    /// The configured request rate.
    pub fn requests_per_second(&self) -> f64 {
        self.requests_per_second
    }
}

/// The default request rate of the public endpoint.
///
/// The free trial of the node allows 15 requests per second. The driver
/// stays below that ceiling so a refresh never pushes the endpoint into a
/// rate limit.
pub const DEFAULT_REQUESTS_PER_SECOND: f64 = 8.0;

/// The parameters of a WebSocket swap monitor.
///
/// The monitor is the long running half of the dynamic refresh: it keeps a
/// subscription open on the pool manager and records the swap activity of
/// every pool as it happens. The periodic cleanup then only has to look at
/// the recorded evidence instead of rescanning the whole chain.
#[derive(Clone, Debug)]
pub struct WssMonitorOptions {
    pub wss_url: String,
    pub pool_manager: String,
    /// Maximum number of seconds the monitor runs.
    pub duration_seconds: u64,
    /// Idle timeout of a single websocket read.
    pub read_timeout_seconds: u64,
    /// Maximum number of swap observations kept in memory.
    pub max_observations: usize,
}

impl Default for WssMonitorOptions {
    fn default() -> Self {
        Self {
            wss_url: String::new(),
            pool_manager: crate::chain::ROBINHOOD_V4_POOL_MANAGER.to_owned(),
            duration_seconds: 60,
            read_timeout_seconds: 30,
            max_observations: 4096,
        }
    }
}

/// The outcome of a WebSocket monitor run.
#[derive(Clone, Debug, Default)]
pub struct WssMonitorReport {
    pub wss_url: String,
    pub subscribed: bool,
    pub blocks_observed: u64,
    pub swap_events: u64,
    pub active_pools: usize,
    pub duration_seconds: u64,
    pub observations: Vec<SwapActivity>,
    pub errors: Vec<String>,
    /// The blocks the swaps were observed in. They are resolved into
    /// timestamps after the subscription is closed.
    pub blocks_seen: Vec<i64>,
}

/// Subscribes to the V4 swap logs and records the activity until the deadline.
///
/// The monitor is intentionally read only: it observes the chain and returns
/// the evidence. The caller decides whether the evidence is written into the
/// database, so a monitoring run can never mutate the production store by
/// accident.
pub async fn monitor_swap_activity_wss(
    options: &WssMonitorOptions,
) -> Result<WssMonitorReport, String> {
    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::Message;

    if options.wss_url.trim().is_empty() {
        return Err("wss url is required".to_string());
    }
    let (socket, _) = tokio_tungstenite::connect_async(options.wss_url.trim())
        .await
        .map_err(|error| error.to_string())?;
    let (mut sink, mut stream) = socket.split();

    let topic = v4_swap_topic();
    let subscribe = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": ["logs", {"address": options.pool_manager, "topics": [topic]}],
    });
    sink.send(Message::Text(subscribe.to_string().into()))
        .await
        .map_err(|error| error.to_string())?;

    let deadline = tokio::time::Instant::now()
        + std::time::Duration::from_secs(options.duration_seconds.max(1));
    let mut report = WssMonitorReport {
        wss_url: options.wss_url.clone(),
        duration_seconds: options.duration_seconds,
        ..WssMonitorReport::default()
    };
    let mut activity: BTreeMap<String, SwapActivity> = BTreeMap::new();
    let mut pending_blocks: BTreeMap<i64, String> = BTreeMap::new();

    while tokio::time::Instant::now() < deadline {
        let next = tokio::time::timeout(
            std::time::Duration::from_secs(options.read_timeout_seconds.max(1)),
            stream.next(),
        )
        .await;
        let message = match next {
            Err(_) => continue,
            Ok(None) => break,
            Ok(Some(Err(error))) => {
                report.errors.push(error.to_string());
                break;
            }
            Ok(Some(Ok(message))) => message,
        };
        let text = match message {
            Message::Text(text) => text.to_string(),
            Message::Binary(bytes) => String::from_utf8_lossy(&bytes).to_string(),
            Message::Ping(_) | Message::Pong(_) | Message::Frame(_) => continue,
            Message::Close(_) => break,
        };
        let Ok(parsed) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        if parsed.get("id").is_some() {
            if parsed.get("result").is_some() {
                report.subscribed = true;
            } else if let Some(error) = parsed.get("error") {
                report.errors.push(error.to_string());
            }
            continue;
        }
        if parsed.get("method").and_then(Value::as_str) != Some("eth_subscription") {
            continue;
        }
        let Some(result) = parsed.get("params").and_then(|params| params.get("result")) else {
            continue;
        };
        let Some(topics) = result.get("topics").and_then(Value::as_array) else {
            continue;
        };
        let Some(pool_topic) = topics.get(1).and_then(Value::as_str) else {
            continue;
        };
        let pool_id = pool_topic.to_ascii_lowercase();
        let block = result
            .get("blockNumber")
            .and_then(Value::as_str)
            .and_then(|value| u64::from_str_radix(value.trim_start_matches("0x"), 16).ok())
            .unwrap_or(0) as i64;
        let transaction = result
            .get("transactionHash")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        pending_blocks.insert(block, transaction);
        report.swap_events += 1;
        let entry = activity
            .entry(pool_id.clone())
            .or_insert_with(|| SwapActivity {
                pool_id: pool_id.clone(),
                last_swap_block: block,
                last_swap_at: String::new(),
                swap_count: 0,
            });
        if block >= entry.last_swap_block {
            entry.last_swap_block = block;
        }
        entry.swap_count += 1;
        if activity.len() > options.max_observations {
            if let Some(oldest) = activity
                .keys()
                .min_by_key(|key| {
                    activity
                        .get(*key)
                        .map(|item| item.last_swap_block)
                        .unwrap_or(0)
                })
                .cloned()
            {
                activity.remove(&oldest);
            }
        }
    }

    report.active_pools = activity.len();
    report.observations = activity.into_values().collect();
    report.blocks_observed = pending_blocks.len() as u64;
    report.blocks_seen = pending_blocks.keys().copied().collect();
    if !report.subscribed && report.errors.is_empty() {
        report
            .errors
            .push("subscription was not acknowledged".to_string());
    }
    Ok(report)
}

/// The serialisable summary of a WebSocket monitor run.
pub fn wss_monitor_report(report: &WssMonitorReport) -> Value {
    json!({
        "wssUrl": report.wss_url,
        "subscribed": report.subscribed,
        "blocksObserved": report.blocks_observed,
        "swapEvents": report.swap_events,
        "activePools": report.active_pools,
        "durationSeconds": report.duration_seconds,
        "blocksSeen": report.blocks_seen,
        "errors": report.errors,
        "observations": report.observations.iter().map(|item| json!({
            "poolId": item.pool_id,
            "lastSwapBlock": item.last_swap_block,
            "lastSwapAt": item.last_swap_at,
            "swapCount": item.swap_count,
        })).collect::<Vec<Value>>(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn options(now_unix: i64) -> PoolRoleOptions {
        PoolRoleOptions {
            apply: false,
            now_unix: Some(now_unix),
            cleanup_enabled: true,
            min_pools: 0,
            min_trade_observations: 0,
            ..PoolRoleOptions::default()
        }
    }

    fn insert_role(connection: &Connection, pool_id: &str, role: &str) {
        connection
            .execute(
                "INSERT INTO pool_role_assignment (network, chain_id, protocol, pool_id, role, anchor_symbol, counter_address, direction_key, hop, rank, assigned_at, updated_at) VALUES ('robinhood', 4663, 'uniswap_v4', ?1, ?2, '', '', '', '', NULL, 'now', 'now')",
                params![pool_id, role],
            )
            .expect("role");
    }

    #[test]
    fn swap_topic_matches_known_value() {
        assert_eq!(v4_swap_topic(), V4_SWAP_TOPIC);
    }

    #[test]
    fn cleanup_window_covers_three_days() {
        let (start, end) = cleanup_window(&options(1_800_000_000)).expect("window");
        assert!(start < end);
        assert_eq!(start.len(), 20);
        assert_eq!(end.len(), 20);
    }

    #[test]
    fn candidate_and_recommendation_are_never_pruned() {
        let connection = Connection::open_in_memory().expect("memory");
        ensure_pool_role_schema(&connection).expect("schema");
        insert_role(&connection, "0x01", "candidate");
        insert_role(&connection, "0x02", "recommendation");
        insert_role(&connection, "0x03", "backup");
        let report = cleanup_pool_roles(&connection, "robinhood", 4663, &options(1_800_000_000))
            .expect("cleanup");
        assert_eq!(report.candidate_total, 1);
        assert_eq!(report.recommendation_total, 1);
        assert_eq!(report.backup_total, 1);
        assert_eq!(report.backups_pruned, 1);
        assert!(report
            .decisions
            .iter()
            .all(|decision| decision.keep || decision.role == PoolRole::Backup));
    }

    #[test]
    fn backup_with_recent_trade_is_kept() {
        let connection = Connection::open_in_memory().expect("memory");
        ensure_pool_role_schema(&connection).expect("schema");
        insert_role(&connection, "0x03", "backup");
        let now = 1_800_000_000i64;
        let recent = chrono::DateTime::from_timestamp(now - 3600, 0)
            .expect("timestamp")
            .format("%Y-%m-%dT%H:%M:%SZ")
            .to_string();
        write_trade_activity(
            &connection,
            "robinhood",
            4663,
            &[("0x03".to_owned(), 100, recent, 3)],
            "now",
        )
        .expect("activity");
        let report =
            cleanup_pool_roles(&connection, "robinhood", 4663, &options(now)).expect("cleanup");
        assert_eq!(report.backups_kept, 1);
        assert_eq!(report.backups_pruned, 0);
    }

    #[test]
    fn the_cleanup_is_off_by_default() {
        // AC-129: the three day backup cleanup must stay off until the
        // operator turns it on.  The default options are what every caller
        // that does not spell the switch out receives, so the default has to
        // be the safe one.
        let defaults = PoolRoleOptions::default();

        assert!(!defaults.cleanup_enabled);
        assert!(!defaults.apply);
        // The three day retention and the three day interval are the
        // contract AC-129 names.
        assert_eq!(defaults.retention_days, 3);
        assert_eq!(defaults.cleanup_interval_days, 3);
    }

    #[test]
    fn the_cleanup_is_skipped_while_the_startup_gate_is_closed() {
        let connection = Connection::open_in_memory().expect("memory");
        ensure_pool_role_schema(&connection).expect("schema");
        insert_role(&connection, "0x03", "backup");
        let mut gated = options(1_800_000_000);
        gated.cleanup_enabled = false;
        let report = cleanup_pool_roles(&connection, "robinhood", 4663, &gated).expect("cleanup");
        assert!(!report.executed);
        assert_eq!(
            report.skip_reason.as_deref(),
            Some("cleanup_disabled_by_startup_gate")
        );
        assert_eq!(report.backups_pruned, 0);
        let remaining: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM pool_role_assignment WHERE pool_id = '0x03'",
                [],
                |row| row.get(0),
            )
            .expect("count");
        assert_eq!(remaining, 1);
    }

    #[test]
    fn the_cleanup_is_skipped_when_the_data_is_still_thin() {
        let connection = Connection::open_in_memory().expect("memory");
        ensure_pool_role_schema(&connection).expect("schema");
        insert_role(&connection, "0x03", "backup");
        let mut gated = options(1_800_000_000);
        gated.min_pools = 10;
        let report = cleanup_pool_roles(&connection, "robinhood", 4663, &gated).expect("cleanup");
        assert!(!report.executed);
        assert!(report
            .skip_reason
            .as_deref()
            .unwrap_or_default()
            .starts_with("insufficient_pool_count"));
        assert_eq!(report.backups_pruned, 0);
    }

    #[test]
    fn the_cleanup_is_skipped_without_trade_evidence() {
        let connection = Connection::open_in_memory().expect("memory");
        ensure_pool_role_schema(&connection).expect("schema");
        insert_role(&connection, "0x03", "backup");
        let mut gated = options(1_800_000_000);
        gated.min_trade_observations = 5;
        let report = cleanup_pool_roles(&connection, "robinhood", 4663, &gated).expect("cleanup");
        assert!(!report.executed);
        assert!(report
            .skip_reason
            .as_deref()
            .unwrap_or_default()
            .starts_with("insufficient_trade_observations"));
    }

    /// A dynamic-fee pool's effective rate is decided by its hook at swap
    /// time, so the off-chain depth arithmetic cannot price it. It must
    /// therefore never become a backup pool.
    #[test]
    fn a_dynamic_fee_pool_is_never_a_backup() {
        let connection = Connection::open_in_memory().expect("memory");
        ensure_pool_role_schema(&connection).expect("schema");
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pools (network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT, fee INTEGER NOT NULL DEFAULT 0, dynamic_fee INTEGER NOT NULL DEFAULT 0, special_fee INTEGER NOT NULL DEFAULT 0);
                 CREATE TABLE dex_candidate_pools (network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT);
                 CREATE TABLE dex_candidate_pool_recommendations (network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT);
                 INSERT INTO v4_exchange_pools VALUES ('robinhood', 4663, 'uniswap_v4', '0x01', 3000, 0, 0);
                 INSERT INTO v4_exchange_pools VALUES ('robinhood', 4663, 'uniswap_v4', '0x02', 8388608, 1, 0);",
            )
            .expect("fixtures");
        let result = materialize_pool_roles(&connection, "robinhood", 4663, "now").expect("roles");
        assert_eq!(result["backups"], 1);
        let dynamic_backups: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM pool_role_assignment WHERE pool_id = '0x02'",
                [],
                |row| row.get(0),
            )
            .expect("count");
        assert_eq!(
            dynamic_backups, 0,
            "a dynamic-fee pool must never be assigned a role"
        );
    }

    #[test]
    fn role_materialisation_keeps_candidates_out_of_backup() {
        let connection = Connection::open_in_memory().expect("memory");
        ensure_pool_role_schema(&connection).expect("schema");
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pools (network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT, fee INTEGER NOT NULL DEFAULT 0, dynamic_fee INTEGER NOT NULL DEFAULT 0, special_fee INTEGER NOT NULL DEFAULT 0);
                 CREATE TABLE dex_candidate_pools (network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT);
                 CREATE TABLE dex_candidate_pool_recommendations (network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT);
                 INSERT INTO v4_exchange_pools VALUES ('robinhood', 4663, 'uniswap_v4', '0x01', 3000, 0, 0);
                 INSERT INTO v4_exchange_pools VALUES ('robinhood', 4663, 'uniswap_v4', '0x02', 3000, 0, 0);
                 INSERT INTO v4_exchange_pools VALUES ('robinhood', 4663, 'uniswap_v4', '0x03', 3000, 0, 0);
                 INSERT INTO dex_candidate_pools VALUES ('robinhood', 4663, 'uniswap_v4', '0x02');
                 INSERT INTO dex_candidate_pool_recommendations VALUES ('robinhood', 4663, 'uniswap_v4', '0x02');",
            )
            .expect("fixtures");
        let result = materialize_pool_roles(&connection, "robinhood", 4663, "now").expect("roles");
        assert_eq!(result["candidates"], 1);
        assert_eq!(result["recommendations"], 1);
        assert_eq!(result["backups"], 2);
        let recommendation_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM pool_role_assignment WHERE role = 'recommendation'",
                [],
                |row| row.get(0),
            )
            .expect("count");
        assert_eq!(recommendation_count, 1);
    }
}
