use pool_manager::{
    add_pair, apply_cleanup, assert_depth_policy_matches_file, audit, audit_mainnet_baseline,
    cleanup_pool_roles, ensure_depth_schema, ensure_indexes, ensure_pool_role_schema,
    materialize_pool_roles, materialize_scope, monitor_swap_activity_wss, open_role_store,
    read_scope_status, refresh_depth, refresh_key, refresh_pair, role_cleanup_report,
    scan_swap_activity, swap_scan_report, wss_monitor_report, DepthOptions, DepthScope,
    PoolRoleOptions, SwapScanOptions, WssMonitorOptions,
};
use serde_json::json;
use std::env;

fn env_value(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.trim().is_empty())
}

fn next_arg(args: &[String], index: &mut usize, label: &str) -> Result<String, String> {
    if *index + 1 >= args.len() {
        return Err(format!("missing {label}"));
    }
    *index += 1;
    Ok(args[*index].clone())
}

/// Reports or refreshes the pool role assignment.
///
/// The candidate and the recommendation pools are never deleted by the
/// cleanup: they are refreshed dynamically. Only the backups are pruned, and
/// only when the pool has been idle for the whole retention window.
fn pool_roles_command(
    store: &str,
    chain_id: i64,
    apply: bool,
) -> Result<serde_json::Value, String> {
    let connection = open_role_store(store, !apply).map_err(|error| error.to_string())?;
    if !apply {
        ensure_pool_role_schema(&connection).map_err(|error| error.to_string())?;
        let mut statement = connection
            .prepare(
                "SELECT role, COUNT(*) FROM pool_role_assignment
                 WHERE network = 'robinhood' AND chain_id = ?1 AND protocol = 'uniswap_v4'
                 GROUP BY role ORDER BY role",
            )
            .map_err(|error| error.to_string())?;
        let counts = statement
            .query_map([chain_id], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
            })
            .map_err(|error| error.to_string())?
            .collect::<Result<Vec<(String, i64)>, _>>()
            .map_err(|error| error.to_string())?;
        return Ok(json!({
            "roles": counts
                .into_iter()
                .map(|(role, count)| json!({"role": role, "count": count}))
                .collect::<Vec<_>>()
        }));
    }
    let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
    materialize_pool_roles(&connection, "robinhood", chain_id, &now)
        .map_err(|error| error.to_string())
}

/// Scans the V4 swap logs and, in apply mode, records the evidence.
///
/// The scan is read only unless `--apply` is given, so a monitoring run can
/// never mutate the production store by accident.
fn swap_activity_command(
    store: &str,
    chain_id: i64,
    apply: bool,
    rpc_url: &str,
    from_block: i64,
    to_block: i64,
    chunk_size: i64,
    timeout_seconds: u64,
    requests_per_second: f64,
) -> Result<serde_json::Value, String> {
    let resolved = if rpc_url.trim().is_empty() {
        pool_manager::rpc_urls_for(chain_id).into_iter().next()
    } else {
        Some(rpc_url.to_string())
    };
    let Some(resolved) = resolved else {
        return Err("no rpc url available".to_string());
    };
    let runtime = tokio::runtime::Runtime::new().map_err(|error| error.to_string())?;
    let report = runtime
        .block_on(scan_swap_activity(&SwapScanOptions {
            rpc_url: resolved,
            pool_manager: pool_manager::ROBINHOOD_V4_POOL_MANAGER.to_string(),
            from_block,
            to_block,
            chunk_size,
            timeout_seconds,
            requests_per_second: requests_per_second,
        }))
        .map_err(|error| error.to_string())?;
    if !apply {
        return Ok(swap_scan_report(&report));
    }
    let observed_at = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
    let observations = report
        .observations
        .iter()
        .map(|item| {
            (
                item.pool_id.clone(),
                item.last_swap_block,
                item.last_swap_at.clone(),
                item.swap_count,
            )
        })
        .collect::<Vec<_>>();
    let connection = open_role_store(store, false).map_err(|error| error.to_string())?;
    let written = pool_manager::write_trade_activity(
        &connection,
        "robinhood",
        chain_id,
        &observations,
        &observed_at,
    )
    .map_err(|error| error.to_string())?;
    let mut value = swap_scan_report(&report);
    value["written"] = json!(written);
    Ok(value)
}

/// Monitors the V4 swap activity over the websocket endpoint.
///
/// The monitor is the long running half of the dynamic refresh: it keeps the
/// subscription open and reports what it observed. The block timestamps are
/// resolved after the subscription is closed, because the websocket log
/// payload does not carry one.
fn wss_monitor_command(
    store: &str,
    chain_id: i64,
    apply: bool,
    wss_url: &str,
    duration_seconds: u64,
) -> Result<serde_json::Value, String> {
    let resolved = if wss_url.trim().is_empty() {
        env_value("ROBINHOOD_MAINNET_WSS_URLS").and_then(|value| {
            value
                .split(',')
                .map(str::trim)
                .find(|item| !item.is_empty())
                .map(str::to_owned)
        })
    } else {
        Some(wss_url.to_string())
    };
    let Some(resolved) = resolved else {
        return Err("no wss url available".to_string());
    };
    let runtime = tokio::runtime::Runtime::new().map_err(|error| error.to_string())?;
    let mut report = runtime
        .block_on(monitor_swap_activity_wss(&WssMonitorOptions {
            wss_url: resolved.clone(),
            duration_seconds,
            ..WssMonitorOptions::default()
        }))
        .map_err(|error| error.to_string())?;
    // The block timestamps are resolved through the first endpoint that
    // answers. A dead endpoint must not swallow the whole evidence, because
    // "unknown timestamp" would silently disable the retention window.
    for rpc_url in pool_manager::rpc_urls_for(chain_id) {
        let resolved_timestamps = runtime
            .block_on(pool_manager::read_block_timestamps(
                &rpc_url,
                &report.blocks_seen,
                20,
            ))
            .unwrap_or_default();
        if resolved_timestamps.is_empty() {
            continue;
        }
        // The newest resolved block at or below the swap block is used, so a
        // block that the node did not return still receives the time of the
        // closest older block that it did.
        let resolved_blocks = resolved_timestamps
            .iter()
            .map(|(block, timestamp)| (*block, timestamp.clone()))
            .collect::<Vec<(i64, String)>>();
        for observation in report.observations.iter_mut() {
            if let Some((_, timestamp)) = resolved_blocks
                .iter()
                .filter(|(block, _)| *block <= observation.last_swap_block)
                .max_by_key(|(block, _)| *block)
            {
                observation.last_swap_at = timestamp.clone();
            }
        }
        break;
    }
    if !apply {
        return Ok(wss_monitor_report(&report));
    }
    let observed_at = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
    let observations = report
        .observations
        .iter()
        .map(|item| {
            (
                item.pool_id.clone(),
                item.last_swap_block,
                item.last_swap_at.clone(),
                item.swap_count,
            )
        })
        .collect::<Vec<_>>();
    let connection = open_role_store(store, false).map_err(|error| error.to_string())?;
    let written = pool_manager::write_trade_activity(
        &connection,
        "robinhood",
        chain_id,
        &observations,
        &observed_at,
    )
    .map_err(|error| error.to_string())?;
    let mut value = wss_monitor_report(&report);
    value["written"] = json!(written);
    Ok(value)
}

fn main() {
    // The shared depth policy must agree with the compiled-in constants.
    // A mismatch means the two tools would reach different depth verdicts,
    // so the tool refuses to start instead of silently disagreeing.
    if let Err(error) = assert_depth_policy_matches_file() {
        eprintln!("pool-manager error: {error}");
        std::process::exit(2);
    }

    let args: Vec<String> = env::args().collect();
    let store = env_value("POOL_MANAGER_STORE")
        .unwrap_or_else(|| "flashloan/db/local_store_4663.sqlite3".to_string());
    if args.len() < 2 {
        eprintln!(
            "usage: pool-manager <audit|baseline-audit|cleanup|add-pair|refresh-pair|update-pair|refresh-key|materialize-scope|rebuild-scope|scope-status|scope|ensure-indexes|ensure-depth-schema|depth|tick-state|pool-roles|pool-role-cleanup|swap-activity|wss-monitor> \
             --store PATH --chain-id N [options]
  depth: --depth-scope candidate|all (default candidate) --resume --max-pools N --progress-every N \
         --usdg-valuation-report PATH
  pool-role-cleanup: --cleanup-enabled --cleanup-min-pools N --cleanup-min-trade-observations N
    --usdg-valuation-db PATH"
        );
        std::process::exit(2);
    }
    let mut index = 1;
    let command = &args[index];
    index += 1;
    let mut chain_id = 4663i64;
    let mut apply = false;
    let mut base = String::new();
    let mut quote = String::new();
    let mut key_id = String::new();
    let mut pool_group = "PAIR".to_string();
    let mut limit = 5usize;
    let mut minimal = false;
    let mut scope = "core4".to_string();
    let mut dry_run = false;
    let mut apply_flag = false;
    let mut rpc_url = String::new();
    let mut pool_ids: Vec<String> = Vec::new();
    let mut batch_size = 10usize;
    let mut timeout_seconds = 20u64;
    let mut depth_scope = "candidate".to_string();
    let mut depth_resume = false;
    let mut depth_max_pools: Option<usize> = None;
    let mut depth_progress_every = 0usize;
    let mut usdg_valuation_report: Option<String> = None;
    let mut usdg_valuation_db: Option<String> = None;
    let mut wss_url = String::new();
    let mut duration_seconds = 60u64;
    let mut from_block = 0i64;
    let mut to_block = 0i64;
    let mut chunk_size = 5_000i64;
    let mut retention_days = 3i64;
    let mut cleanup_interval_days = 3i64;
    let mut cleanup_enabled = false;
    let mut cleanup_min_pools = 500i64;
    let mut cleanup_min_trade_observations = 100i64;
    let mut requests_per_second = 8.0f64;
    let mut store = store;
    while index < args.len() {
        match args[index].as_str() {
            "--store" => {
                store =
                    next_arg(&args, &mut index, "--store").unwrap_or_else(|error| panic!("{error}"))
            }
            "--chain-id" => {
                let value = next_arg(&args, &mut index, "--chain-id")
                    .unwrap_or_else(|error| panic!("{error}"));
                chain_id = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid chain-id: {value}"));
            }
            "--apply" => {
                apply = true;
                apply_flag = true;
            }
            "--dry-run" => dry_run = true,
            "--base" => {
                base =
                    next_arg(&args, &mut index, "--base").unwrap_or_else(|error| panic!("{error}"))
            }
            "--quote" => {
                quote =
                    next_arg(&args, &mut index, "--quote").unwrap_or_else(|error| panic!("{error}"))
            }
            "--key-id" => {
                key_id = next_arg(&args, &mut index, "--key-id")
                    .unwrap_or_else(|error| panic!("{error}"))
            }
            "--minimal" => minimal = true,
            "--pool-group" | "--anchor-symbol" => {
                pool_group = next_arg(&args, &mut index, "--pool-group")
                    .unwrap_or_else(|error| panic!("{error}"));
            }
            "--limit" => {
                let value = next_arg(&args, &mut index, "--limit")
                    .unwrap_or_else(|error| panic!("{error}"));
                limit = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid limit: {value}"));
            }
            "--scope" => {
                scope =
                    next_arg(&args, &mut index, "--scope").unwrap_or_else(|error| panic!("{error}"))
            }
            "--rpc-url" => {
                rpc_url = next_arg(&args, &mut index, "--rpc-url")
                    .unwrap_or_else(|error| panic!("{error}"))
            }
            "--wss-url" => {
                wss_url = next_arg(&args, &mut index, "--wss-url")
                    .unwrap_or_else(|error| panic!("{error}"))
            }
            "--duration-seconds" => {
                let value = next_arg(&args, &mut index, "--duration-seconds")
                    .unwrap_or_else(|error| panic!("{error}"));
                duration_seconds = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid duration-seconds: {value}"));
            }
            "--from-block" => {
                let value = next_arg(&args, &mut index, "--from-block")
                    .unwrap_or_else(|error| panic!("{error}"));
                from_block = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid from-block: {value}"));
            }
            "--to-block" => {
                let value = next_arg(&args, &mut index, "--to-block")
                    .unwrap_or_else(|error| panic!("{error}"));
                to_block = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid to-block: {value}"));
            }
            "--chunk-size" => {
                let value = next_arg(&args, &mut index, "--chunk-size")
                    .unwrap_or_else(|error| panic!("{error}"));
                chunk_size = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid chunk-size: {value}"));
            }
            "--retention-days" => {
                let value = next_arg(&args, &mut index, "--retention-days")
                    .unwrap_or_else(|error| panic!("{error}"));
                retention_days = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid retention-days: {value}"));
            }
            "--cleanup-interval-days" => {
                let value = next_arg(&args, &mut index, "--cleanup-interval-days")
                    .unwrap_or_else(|error| panic!("{error}"));
                cleanup_interval_days = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid cleanup-interval-days: {value}"));
            }
            "--cleanup-enabled" => cleanup_enabled = true,
            "--requests-per-second" => {
                let value = next_arg(&args, &mut index, "--requests-per-second")
                    .unwrap_or_else(|error| panic!("{error}"));
                requests_per_second = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid requests-per-second: {value}"));
            }
            "--cleanup-min-pools" => {
                let value = next_arg(&args, &mut index, "--cleanup-min-pools")
                    .unwrap_or_else(|error| panic!("{error}"));
                cleanup_min_pools = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid cleanup-min-pools: {value}"));
            }
            "--cleanup-min-trade-observations" => {
                let value = next_arg(&args, &mut index, "--cleanup-min-trade-observations")
                    .unwrap_or_else(|error| panic!("{error}"));
                cleanup_min_trade_observations = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid cleanup-min-trade-observations: {value}"));
            }
            "--pool-id" => {
                let value = next_arg(&args, &mut index, "--pool-id")
                    .unwrap_or_else(|error| panic!("{error}"));
                pool_ids.push(value);
            }
            "--batch-size" => {
                let value = next_arg(&args, &mut index, "--batch-size")
                    .unwrap_or_else(|error| panic!("{error}"));
                batch_size = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid batch-size: {value}"));
            }
            "--timeout" => {
                let value = next_arg(&args, &mut index, "--timeout")
                    .unwrap_or_else(|error| panic!("{error}"));
                timeout_seconds = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid timeout: {value}"));
            }
            "--depth-scope" => {
                depth_scope = next_arg(&args, &mut index, "--depth-scope")
                    .unwrap_or_else(|error| panic!("{error}"));
            }
            "--resume" => depth_resume = true,
            "--max-pools" => {
                let value = next_arg(&args, &mut index, "--max-pools")
                    .unwrap_or_else(|error| panic!("{error}"));
                depth_max_pools = Some(
                    value
                        .parse()
                        .unwrap_or_else(|_| panic!("invalid max-pools: {value}")),
                );
            }
            "--progress-every" => {
                let value = next_arg(&args, &mut index, "--progress-every")
                    .unwrap_or_else(|error| panic!("{error}"));
                depth_progress_every = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid progress-every: {value}"));
            }
            "--usdg-valuation-report" => {
                usdg_valuation_report = Some(
                    next_arg(&args, &mut index, "--usdg-valuation-report")
                        .unwrap_or_else(|error| panic!("{error}")),
                )
            }
            "--usdg-valuation-db" => {
                usdg_valuation_db = Some(
                    next_arg(&args, &mut index, "--usdg-valuation-db")
                        .unwrap_or_else(|error| panic!("{error}")),
                )
            }
            other => panic!("unknown argument: {other}"),
        }
        index += 1;
    }
    if dry_run && apply_flag {
        panic!("--dry-run and --apply are mutually exclusive");
    }
    if dry_run {
        apply = false;
    }

    let network = match chain_id {
        4663 => "robinhood",
        46630 => "robinhood-testnet",
        other => {
            eprintln!("unsupported chain-id: {other}");
            std::process::exit(2);
        }
    };

    let result = match command.as_str() {
        "audit" => audit(&store, chain_id),
        "baseline-audit" => audit_mainnet_baseline(&store, network, chain_id)
            .map(|value| json!(value))
            .map_err(|error| error.to_string()),
        "cleanup" => {
            if !apply {
                audit(&store, chain_id)
            } else {
                apply_cleanup(&store, chain_id)
            }
        }
        "add-pair" => {
            if base.is_empty() || quote.is_empty() {
                Err("add-pair requires --base and --quote".to_string())
            } else {
                add_pair(&store, chain_id, &base, &quote, &pool_group, limit, apply)
            }
        }
        "refresh-pair" | "update-pair" => {
            if base.is_empty() || quote.is_empty() {
                Err("refresh-pair requires --base and --quote".to_string())
            } else {
                refresh_pair(&store, chain_id, &base, &quote, &pool_group, limit, apply)
            }
        }
        "refresh-key" => {
            if key_id.is_empty() {
                Err("refresh-key requires --key-id <tokenX:tokenY>".to_string())
            } else {
                if minimal {
                    refresh_key(&store, chain_id, &key_id, &pool_group, limit, apply)
                        .map(|_| json!({"refreshKey": true}))
                } else {
                    refresh_key(&store, chain_id, &key_id, &pool_group, limit, apply)
                }
            }
        }
        "materialize-scope" | "rebuild-scope" => {
            materialize_scope(&store, network, chain_id, &scope, apply)
                .map(|value| json!(value))
                .map_err(|error| error.to_string())
        }
        "scope-status" | "scope" => read_scope_status(&store, network, chain_id, &scope)
            .map(|value| json!(value))
            .map_err(|error| error.to_string()),
        "ensure-indexes" => {
            if !apply {
                Err("ensure-indexes requires --apply (it writes schema indexes)".to_string())
            } else {
                ensure_indexes(&store, chain_id).map(|value| json!(value))
            }
        }
        "ensure-depth-schema" => {
            if !apply {
                Err("ensure-depth-schema requires --apply (it adds snapshot columns)".to_string())
            } else {
                ensure_depth_schema(&store, chain_id).map(|value| json!(value))
            }
        }
        "depth" => {
            let scope = DepthScope::parse(&depth_scope).unwrap_or_else(|error| panic!("{error}"));
            refresh_depth(
                &store,
                chain_id,
                &DepthOptions {
                    pool_ids,
                    scope,
                    resume: depth_resume,
                    max_pools: depth_max_pools,
                    progress_every: depth_progress_every,
                    rpc_url: if rpc_url.trim().is_empty() {
                        None
                    } else {
                        Some(rpc_url)
                    },
                    batch_size,
                    timeout_seconds,
                    apply,
                    usdg_valuation_report,
                    usdg_valuation_db,
                },
            )
            .map(|value| json!(value))
        }
        "tick-state" => {
            match pool_manager::tick_swap::TickRequest::from_pool_ids(&pool_ids, &store, chain_id) {
                Err(error) => Err(error),
                Ok(requests) => {
                    let urls = if rpc_url.trim().is_empty() {
                        pool_manager::chain::rpc_urls_for(chain_id)
                    } else {
                        vec![rpc_url.clone()]
                    };
                    if urls.is_empty() {
                        Err("no RPC endpoint configured for the tick-state read".to_string())
                    } else {
                        match pool_manager::tick_swap::read_v4_tick_states(
                            &urls[0],
                            &pool_manager::chain::ROBINHOOD_V4_POOL_MANAGER,
                            &requests,
                            timeout_seconds,
                        ) {
                            Err(error) => Err(error),
                            Ok(batch) => Ok(serde_json::json!({
                                "ok": true,
                                "poolCount": requests.len(),
                                "rpcEndpointUsed": batch.rpc_url,
                                "errors": batch.errors,
                                "states": batch.states,
                            })),
                        }
                    }
                }
            }
        }
        "pool-roles" => pool_roles_command(&store, chain_id, apply),
        "pool-role-cleanup" => {
            let options = PoolRoleOptions {
                retention_days,
                cleanup_interval_days,
                cleanup_enabled,
                min_pools: cleanup_min_pools,
                min_trade_observations: cleanup_min_trade_observations,
                apply,
                ..PoolRoleOptions::default()
            };
            match open_role_store(&store, !apply) {
                Err(error) => Err(error),
                Ok(connection) => cleanup_pool_roles(&connection, "robinhood", chain_id, &options)
                    .map(|report| role_cleanup_report(&report))
                    .map_err(|error| error.to_string()),
            }
        }
        "swap-activity" => swap_activity_command(
            &store,
            chain_id,
            apply,
            &rpc_url,
            from_block,
            to_block,
            chunk_size,
            timeout_seconds,
            requests_per_second,
        ),
        "wss-monitor" => wss_monitor_command(&store, chain_id, apply, &wss_url, duration_seconds),
        other => Err(format!("unknown command: {other}")),
    };

    match result {
        Ok(value) => {
            println!("{value}");
        }
        Err(error) => {
            eprintln!("pool-manager error: {error}");
            std::process::exit(1);
        }
    }
}
