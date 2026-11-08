use crate::{read_pipeline_status, CompositeError, PipelineSnapshot};
use broadcast_manager::{read_broadcast_status, BroadcastStatus};
use candidate_edge_pool_manager::{
    read_candidate_snapshot, CandidateEdgeSnapshot, CandidatePoolRow,
};
use candidate_exchange_pool_manager::{
    read_candidate_exchange_snapshot, CandidateExchangeSnapshot, IntermediatePoolRow,
};
use closed_loop_manager::{
    read_closed_loop_snapshot, ClosedLoopHopRow, ClosedLoopRow, ClosedLoopSnapshot,
};
use review_manager::{read_review_snapshot, ReviewRow, ReviewSnapshot};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use token_manager::{read_library_tokens, AdmissionStatus, LibraryKey, TokenQueryError, TokenView};

const OVERVIEW_SOURCE: &str = "manager_closed_loop";
const OVERVIEW_PROTOCOL: &str = "uniswap_v4";
const OVERVIEW_SCOPE: &str = "core4";
const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HopPoolSource {
    CandidateEdge,
    CandidateExchange,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ManagerOverviewSnapshot {
    pub enabled: bool,
    pub source: String,
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,
    pub scope_key: String,
    pub selected_protocols: Vec<String>,
    pub route_framework_source: String,
    pub market_observation_source: String,
    pub market_observation_source_stage: String,
    pub decision_basis: String,
    pub generated_at: String,
    pub summary: OverviewSummary,
    pub arbitrage_paths: Vec<OverviewArbitragePath>,
    pub arbitrage_path_groups: Vec<OverviewPathGroup>,
    pub watch_pairs: Vec<OverviewWatchPair>,
    pub liquid_pool_registry: Vec<OverviewPool>,
    pub flash_loan_liquidity: OverviewFlashLoanLiquidity,
    pub pool_static_table: OverviewPoolStaticTable,
    pub route_framework: OverviewRouteFramework,
    pub versions: OverviewVersionSummary,
    pub data_quality: OverviewDataQuality,
    pub pipeline_status: PipelineSnapshot,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewSummary {
    pub arbitrage_path_count: usize,
    pub candidate_path_count: usize,
    pub market_observation_route_count: usize,
    pub chain_specific_observation_pair_count: usize,
    pub overview_path_group_count: usize,
    pub watch_pair_count: usize,
    pub bridge_path_count: usize,
    pub closed_loop_anchor_symbols: Vec<String>,
    pub overview_profitable_route_count: usize,
    pub overview_probe_complete_count: usize,
    pub overview_chain_submission_candidate_count: usize,
    pub overview_non_stable_chain_submission_candidate_count: usize,
    pub overview_live_quoter_required_count: usize,
    pub overview_best_observed_net_profit_usd: Option<f64>,
    pub overview_probe_notional_usd: f64,
    pub overview_best_token_basis_net_return_bps: Option<f64>,
    pub overview_best_executable_net_profit_usd: Option<f64>,
    pub candidate_cache_refreshed_at: Option<String>,
    pub path_source: String,
    pub review_count: usize,
    pub review_completed_count: usize,
    pub review_data_incomplete_count: usize,
    pub review_not_executable_count: usize,
    pub review_failed_count: usize,
    pub broadcast_request_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewArbitragePath {
    pub symbol_pair: String,
    pub route_symbols: Vec<String>,
    pub route_addresses: Vec<String>,
    pub overview_route: OverviewRoute,
    pub overview_profit: OverviewProfit,
    pub route_framework_source: String,
    pub path_source: String,
    pub market_observation_source_stage: String,
    pub price_evidence_source: String,
    pub price_evidence_source_label: String,
    pub route_usable_for_observation: bool,
    pub route_has_liquidity_evidence: bool,
    pub execution_pools: Vec<OverviewPool>,
    pub preferred_pools: Vec<OverviewPool>,
    pub hop_candidates: Vec<OverviewHopCandidate>,
    pub hop_candidate_combination_count: usize,
    pub flash_loan_linkage: OverviewFlashLoanLinkage,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewRoute {
    pub kind: String,
    pub path_text: String,
    pub anchor_borrow_symbol: String,
    pub target_symbol: String,
    pub candidate_pair_symbols: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewFlashLoanLinkage {
    pub borrow_symbols: Vec<String>,
    pub route_symbols: Vec<String>,
    pub anchor_token_key_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewProfit {
    pub method: String,
    pub notional_usd: f64,
    pub complete: bool,
    pub requires_live_quoter: bool,
    pub adapter_required: bool,
    pub observed_gross_profit_usd: Option<f64>,
    pub flash_loan_fee_usd: Option<f64>,
    pub gas_reserve_usd: Option<f64>,
    pub observed_net_profit_usd: Option<f64>,
    pub reference_net_profit_usd: Option<f64>,
    pub observed_net_profit_bps: Option<f64>,
    pub reference_input_amount: Option<f64>,
    pub reference_input_symbol: Option<String>,
    pub reference_final_output_amount: Option<f64>,
    pub reference_final_output_symbol: Option<String>,
    pub reference_net_profit_amount: Option<f64>,
    pub reference_net_return_bps: Option<f64>,
    pub reference_return_basis: Option<String>,
    pub reference_return_trusted: Option<bool>,
    pub borrow_asset_class: Option<String>,
    pub borrow_asset_is_stable: Option<bool>,
    pub token_basis_profitable: bool,
    pub token_basis_reference_profitable: bool,
    pub chain_submission_candidate: bool,
    pub profitable: bool,
    pub executable_candidate: bool,
    pub trusted_usd_reference: bool,
    pub token_decimals_trusted: bool,
    pub recommendation: OverviewRecommendation,
    pub recommendation_level: String,
    pub recommendation_action: String,
    pub warnings: Vec<String>,
    pub price_evidence_source: String,
    pub price_evidence_mode: String,
    pub price_evidence_source_label: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewRecommendation {
    pub action: String,
    pub level: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewHopCandidate {
    pub leg: String,
    pub direction: String,
    pub symbol_pair: String,
    pub pool_count: usize,
    pub usable_pool_count: usize,
    pub preferred_pool_id: Option<String>,
    pub pools: Vec<OverviewPool>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewPool {
    pub pool: String,
    pub pool_id: String,
    pub protocol: String,
    pub dex: String,
    pub pool_manager: String,
    pub token0: String,
    pub token1: String,
    pub token0_symbol: String,
    pub token1_symbol: String,
    pub token0_key_id: String,
    pub token1_key_id: String,
    pub fee: i64,
    pub fee_label: String,
    pub tick_spacing: i64,
    pub hooks: String,
    pub route_symbols: Vec<String>,
    pub route_text: String,
    pub dynamic_fee: bool,
    pub special_fee: bool,
    pub has_liquidity: Option<bool>,
    pub liquidity: Option<String>,
    pub liquidity_index: Option<f64>,
    pub price: Option<f64>,
    pub depth_status: Option<String>,
    pub depth_band: BTreeMap<String, String>,
    pub liquidity_read_at: Option<String>,
    pub liquidity_block_number: Option<i64>,
    pub evidence_source: Option<String>,
    pub usable_for_observation: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewWatchPair {
    pub anchor_token_key_id: String,
    pub direction_key: String,
    pub symbol_pair: String,
    pub path_text: String,
    pub address_pair: String,
    pub associated_fund_symbols: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewPathGroup {
    pub flash_loan_token: OverviewTokenReference,
    pub start_token: OverviewTokenReference,
    pub end_token: OverviewTokenReference,
    pub paths: Vec<OverviewArbitragePath>,
    pub pool_dependencies: Vec<OverviewPool>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewTokenReference {
    pub token_key_id: String,
    pub address: String,
    pub symbol: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewFlashLoanLiquidity {
    pub chain_id: i64,
    pub source: String,
    pub available_protocols: Vec<String>,
    pub available: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewPoolStaticTable {
    pub chain_id: i64,
    pub network: String,
    pub source: String,
    pub exists: bool,
    pub generated_at: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewRouteFramework {
    pub route_framework_source: String,
    pub market_observation_source_stage: String,
    pub top_route_text: String,
    pub anchor_borrow_symbol: String,
    pub total_loops: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewVersionSummary {
    pub library_versions: BTreeMap<String, i64>,
    pub library_checksums: BTreeMap<String, String>,
    pub pool_scope_checksum: Option<String>,
    pub candidate_edge_version: i64,
    pub candidate_edge_checksum: Option<String>,
    pub observation_version: i64,
    pub observation_checksum: Option<String>,
    pub closed_loop_version: i64,
    pub closed_loop_checksum: Option<String>,
    pub review_version: i64,
    pub review_checksum: Option<String>,
    pub broadcast_version: i64,
    pub broadcast_checksum: Option<String>,
    pub versions_consistent: bool,
    pub consistency_issues: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewDataQuality {
    pub pnl_source: String,
    pub pnl_missing_count: usize,
    pub unresolved_token_count: usize,
    pub missing_pool_reference_count: usize,
    pub unpriced_pool_reference_count: usize,
    pub usd_depth_required: bool,
    pub cross_version_mixing_detected: bool,
    pub notes: Vec<String>,
}

pub fn read_manager_overview_snapshot(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    limit: usize,
) -> Result<ManagerOverviewSnapshot, CompositeError> {
    if chain_id <= 0 {
        return Err(CompositeError::InvalidRequest(
            "chain_id must be greater than zero".to_owned(),
        ));
    }
    if limit == 0 || limit > 1_000 {
        return Err(CompositeError::InvalidRequest(
            "limit must be between 1 and 1000".to_owned(),
        ));
    }
    let database_path = database_path.as_ref();
    let pipeline = read_pipeline_status(database_path, network, chain_id)
        .map_err(|error| upstream_error("composite", error))?;
    let candidate = read_candidate_snapshot(database_path, network, chain_id, None, limit)
        .map_err(|error| upstream_error("candidate_edge", error))?;
    let exchange = read_candidate_exchange_snapshot(database_path, network, chain_id, None, limit)
        .map_err(|error| upstream_error("candidate_exchange", error))?;
    let closed_loop = read_closed_loop_snapshot(database_path, network, chain_id, None, limit)
        .map_err(|error| upstream_error("closed_loop", error))?;
    let review = read_review_snapshot(database_path, network, chain_id, None, limit)
        .map_err(|error| upstream_error("review", error))?;
    let broadcast = read_broadcast_status(database_path, network, chain_id)
        .map_err(|error| upstream_error("broadcast", error))?;
    let tokens = read_token_catalog(database_path, network, chain_id)?;

    Ok(build_manager_overview_snapshot(
        network,
        chain_id,
        pipeline,
        candidate,
        exchange,
        closed_loop,
        review,
        broadcast,
        &tokens,
    ))
}

fn read_token_catalog(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, TokenView>, CompositeError> {
    let mut tokens = BTreeMap::new();
    for library in [
        LibraryKey::Core,
        LibraryKey::Stocks,
        LibraryKey::Extension,
        LibraryKey::Test,
    ] {
        let page = read_library_tokens(
            database_path,
            network,
            chain_id as u64,
            library,
            Some(AdmissionStatus::Admitted),
        )
        .map_err(|error| token_query_error(library, error))?;
        for token in page.tokens {
            tokens.entry(token.token_key_id.clone()).or_insert(token);
        }
    }
    Ok(tokens)
}

#[allow(clippy::too_many_arguments)]
fn build_manager_overview_snapshot(
    network: &str,
    chain_id: i64,
    pipeline: PipelineSnapshot,
    candidate: CandidateEdgeSnapshot,
    exchange: CandidateExchangeSnapshot,
    closed_loop: ClosedLoopSnapshot,
    review: ReviewSnapshot,
    broadcast: BroadcastStatus,
    tokens: &BTreeMap<String, TokenView>,
) -> ManagerOverviewSnapshot {
    let generated_at = chrono::Utc::now().to_rfc3339();
    let pools = build_pool_catalog(&candidate, &exchange, tokens);
    let pool_by_id = pools
        .iter()
        .cloned()
        .map(|pool| (pool.pool_id.clone(), pool))
        .collect::<BTreeMap<_, _>>();
    let review_by_loop = review
        .reviews
        .iter()
        .map(|row| (row.loop_key.clone(), row))
        .collect::<BTreeMap<_, _>>();
    let broadcast_requested = review
        .broadcast_requests
        .iter()
        .map(|row| row.loop_key.clone())
        .collect::<BTreeSet<_>>();

    let mut missing_pool_reference_count = 0usize;
    let mut paths = Vec::with_capacity(closed_loop.loops.len());
    for loop_row in &closed_loop.loops {
        let review_row = review_by_loop.get(&loop_row.loop_key).copied();
        let execution_pools = loop_row
            .pool_ids
            .iter()
            .filter_map(|pool_id| {
                let pool = pool_by_id.get(pool_id).cloned();
                if pool.is_none() {
                    missing_pool_reference_count += 1;
                }
                pool
            })
            .collect::<Vec<_>>();
        let hop_candidates =
            build_hop_candidates(&closed_loop, loop_row, &candidate, &exchange, tokens);
        paths.push(build_overview_path(
            loop_row,
            review_row,
            broadcast_requested.contains(&loop_row.loop_key),
            execution_pools,
            hop_candidates,
            tokens,
            &pool_by_id,
        ));
    }

    let groups = build_path_groups(&paths);
    let watch_pairs = build_watch_pairs(&exchange, tokens);
    let liquid_pool_registry = pools
        .iter()
        .filter(|pool| pool.usable_for_observation)
        .cloned()
        .collect::<Vec<_>>();
    let unresolved_token_count =
        count_unresolved_tokens(&closed_loop, &exchange, &candidate, tokens);
    let pnl_missing_count = count_missing_signed_pnl(&closed_loop, &review);
    let unpriced_pool_reference_count = paths
        .iter()
        .flat_map(|path| path.execution_pools.iter())
        .filter(|pool| pool.has_liquidity != Some(true))
        .count();

    let summary = build_summary(&paths, &watch_pairs, &review, &closed_loop, tokens);
    let versions = build_versions(
        &pipeline,
        &candidate,
        &exchange,
        &closed_loop,
        &review,
        &broadcast,
    );
    let top_route_text = paths
        .first()
        .map(|path| path.overview_route.path_text.clone())
        .unwrap_or_default();
    let anchor_borrow_symbol = paths
        .first()
        .map(|path| path.overview_route.anchor_borrow_symbol.clone())
        .unwrap_or_default();
    let cross_version_mixing_detected = !versions.versions_consistent;

    ManagerOverviewSnapshot {
        enabled: candidate.status.enabled
            || exchange.status.enabled
            || closed_loop.status.enabled
            || review.status.enabled
            || broadcast.enabled,
        source: "manager".to_owned(),
        network: network.to_owned(),
        chain_id,
        protocol: OVERVIEW_PROTOCOL.to_owned(),
        scope_key: OVERVIEW_SCOPE.to_owned(),
        selected_protocols: vec![OVERVIEW_PROTOCOL.to_owned()],
        route_framework_source: OVERVIEW_SOURCE.to_owned(),
        market_observation_source: OVERVIEW_SOURCE.to_owned(),
        market_observation_source_stage: OVERVIEW_SOURCE.to_owned(),
        decision_basis: OVERVIEW_SOURCE.to_owned(),
        generated_at: generated_at.clone(),
        summary,
        arbitrage_paths: paths,
        arbitrage_path_groups: groups,
        watch_pairs,
        liquid_pool_registry,
        flash_loan_liquidity: OverviewFlashLoanLiquidity {
            chain_id,
            source: "manager".to_owned(),
            available_protocols: Vec::new(),
            available: false,
        },
        pool_static_table: OverviewPoolStaticTable {
            chain_id,
            network: network.to_owned(),
            source: OVERVIEW_SOURCE.to_owned(),
            exists: true,
            generated_at,
        },
        route_framework: OverviewRouteFramework {
            route_framework_source: OVERVIEW_SOURCE.to_owned(),
            market_observation_source_stage: OVERVIEW_SOURCE.to_owned(),
            top_route_text,
            anchor_borrow_symbol,
            total_loops: closed_loop.status.loop_count,
        },
        versions,
        data_quality: OverviewDataQuality {
            pnl_source: "manager_review_signed_pnl".to_owned(),
            pnl_missing_count,
            unresolved_token_count,
            missing_pool_reference_count,
            unpriced_pool_reference_count,
            usd_depth_required: false,
            cross_version_mixing_detected,
            notes: vec![
                "USD depth and USD-denominated TVL are not admission requirements.".to_owned(),
                "PnL is projected only from Review Manager signed PnL; missing PnL remains incomplete.".to_owned(),
            ],
        },
        pipeline_status: pipeline,
    }
}

fn build_pool_catalog(
    candidate: &CandidateEdgeSnapshot,
    exchange: &CandidateExchangeSnapshot,
    tokens: &BTreeMap<String, TokenView>,
) -> Vec<OverviewPool> {
    let mut pools = BTreeMap::<String, OverviewPool>::new();
    for row in &candidate.pools {
        let pool = pool_from_candidate_row(row, tokens);
        pools.entry(pool.pool_id.clone()).or_insert(pool);
    }
    for row in &exchange.pools {
        let pool = pool_from_exchange_row(row, tokens);
        pools.entry(pool.pool_id.clone()).or_insert(pool);
    }
    for snapshot in &exchange.liquidity_snapshots {
        if let Some(pool) = pools.get_mut(&snapshot.pool_id) {
            pool.has_liquidity = snapshot.has_liquidity.or(pool.has_liquidity);
            if snapshot.liquidity_index.is_some() {
                pool.liquidity_index = snapshot.liquidity_index;
            }
            if snapshot.liquidity.is_some() {
                pool.liquidity = snapshot.liquidity.clone();
            }
            if snapshot.price.is_some() {
                pool.price = snapshot.price;
            }
            if snapshot.depth_status.is_some() {
                pool.depth_status = snapshot.depth_status.clone();
                pool.depth_band = depth_band(snapshot.depth_status.as_deref());
            }
            if snapshot.liquidity_read_at.is_some() {
                pool.liquidity_read_at = snapshot.liquidity_read_at.clone();
            }
            if snapshot.liquidity_block_number.is_some() {
                pool.liquidity_block_number = snapshot.liquidity_block_number;
            }
            if snapshot.evidence_source.is_some() {
                pool.evidence_source = snapshot.evidence_source.clone();
            }
            pool.usable_for_observation = pool_is_usable(pool);
        }
    }
    pools.into_values().collect()
}

fn pool_from_candidate_row(
    row: &CandidatePoolRow,
    tokens: &BTreeMap<String, TokenView>,
) -> OverviewPool {
    let token0_symbol = symbol_for(tokens, &row.token0_key_id);
    let token1_symbol = symbol_for(tokens, &row.token1_key_id);
    let has_liquidity = Some(
        row.liquidity_index.is_some_and(|value| value > 0.0)
            || row
                .liquidity
                .as_deref()
                .and_then(|value| value.parse::<u128>().ok())
                .is_some_and(|value| value > 0),
    );
    let mut pool = OverviewPool {
        pool: row.pool_id.clone(),
        pool_id: row.pool_id.clone(),
        protocol: OVERVIEW_PROTOCOL.to_owned(),
        dex: row.pool_manager.clone(),
        pool_manager: row.pool_manager.clone(),
        token0: row.token0.clone(),
        token1: row.token1.clone(),
        token0_symbol,
        token1_symbol,
        token0_key_id: row.token0_key_id.clone(),
        token1_key_id: row.token1_key_id.clone(),
        fee: row.fee,
        fee_label: format_fee(row.fee),
        tick_spacing: row.tick_spacing,
        hooks: row.hooks.clone(),
        route_symbols: Vec::new(),
        route_text: String::new(),
        dynamic_fee: false,
        special_fee: !address_is_zero(&row.hooks),
        has_liquidity,
        liquidity: row.liquidity.clone(),
        liquidity_index: row.liquidity_index,
        price: row.price,
        depth_status: row.depth_status.clone(),
        depth_band: depth_band(row.depth_status.as_deref()),
        liquidity_read_at: row.liquidity_read_at.clone(),
        liquidity_block_number: row.liquidity_block_number,
        evidence_source: row.evidence_source.clone(),
        usable_for_observation: false,
    };
    pool.route_symbols = vec![pool.token0_symbol.clone(), pool.token1_symbol.clone()];
    pool.route_text = pool.route_symbols.join(" -> ");
    pool.usable_for_observation = pool_is_usable(&pool);
    pool
}

fn pool_from_exchange_row(
    row: &IntermediatePoolRow,
    tokens: &BTreeMap<String, TokenView>,
) -> OverviewPool {
    let token0_symbol = symbol_for(tokens, &row.token0_key_id);
    let token1_symbol = symbol_for(tokens, &row.token1_key_id);
    let has_liquidity = Some(
        row.liquidity_index.is_some_and(|value| value > 0.0)
            || row
                .liquidity
                .as_deref()
                .and_then(|value| value.parse::<u128>().ok())
                .is_some_and(|value| value > 0),
    );
    let mut pool = OverviewPool {
        pool: row.pool_id.clone(),
        pool_id: row.pool_id.clone(),
        protocol: OVERVIEW_PROTOCOL.to_owned(),
        dex: row.pool_manager.clone(),
        pool_manager: row.pool_manager.clone(),
        token0: row.token0.clone(),
        token1: row.token1.clone(),
        token0_symbol,
        token1_symbol,
        token0_key_id: row.token0_key_id.clone(),
        token1_key_id: row.token1_key_id.clone(),
        fee: row.fee,
        fee_label: format_fee(row.fee),
        tick_spacing: row.tick_spacing,
        hooks: row.hooks.clone(),
        route_symbols: Vec::new(),
        route_text: String::new(),
        dynamic_fee: false,
        special_fee: !address_is_zero(&row.hooks),
        has_liquidity,
        liquidity: row.liquidity.clone(),
        liquidity_index: row.liquidity_index,
        price: row.price,
        depth_status: row.depth_status.clone(),
        depth_band: depth_band(row.depth_status.as_deref()),
        liquidity_read_at: row.liquidity_read_at.clone(),
        liquidity_block_number: row.liquidity_block_number,
        evidence_source: row.evidence_source.clone(),
        usable_for_observation: false,
    };
    pool.route_symbols = vec![pool.token0_symbol.clone(), pool.token1_symbol.clone()];
    pool.route_text = pool.route_symbols.join(" -> ");
    pool.usable_for_observation = pool_is_usable(&pool);
    pool
}

fn build_hop_candidates(
    closed_loop: &ClosedLoopSnapshot,
    loop_row: &ClosedLoopRow,
    candidate: &CandidateEdgeSnapshot,
    exchange: &CandidateExchangeSnapshot,
    tokens: &BTreeMap<String, TokenView>,
) -> Vec<OverviewHopCandidate> {
    closed_loop
        .hops
        .iter()
        .filter(|hop| hop.loop_key == loop_row.loop_key)
        .map(|hop| {
            let matching = if hop_pool_source(&hop.hop_name) == HopPoolSource::CandidateExchange {
                exchange
                    .pools
                    .iter()
                    .filter(|pool| {
                        pool.anchor_token_key_id == loop_row.anchor_token_key_id
                            && ((pool.token0_key_id == hop.input_token_key_id
                                && pool.token1_key_id == hop.output_token_key_id)
                                || (pool.token0_key_id == hop.output_token_key_id
                                    && pool.token1_key_id == hop.input_token_key_id))
                    })
                    .map(|pool| pool_from_exchange_row(pool, tokens))
                    .collect::<Vec<_>>()
            } else {
                candidate
                    .pools
                    .iter()
                    .filter(|pool| {
                        pool.anchor_token_key_id == loop_row.anchor_token_key_id
                            && pool_matches_direction(
                                pool,
                                &hop.input_token_key_id,
                                &hop.output_token_key_id,
                            )
                    })
                    .map(|pool| pool_from_candidate_row(pool, tokens))
                    .collect::<Vec<_>>()
            };
            build_hop_candidate(hop, matching)
        })
        .collect()
}

fn build_hop_candidate(hop: &ClosedLoopHopRow, pools: Vec<OverviewPool>) -> OverviewHopCandidate {
    let usable_pool_count = pools
        .iter()
        .filter(|pool| pool.usable_for_observation)
        .count();
    let direction = pools
        .first()
        .map(|pool| {
            if pool.token0_key_id == hop.input_token_key_id {
                "forward"
            } else {
                "reverse"
            }
        })
        .unwrap_or("unknown")
        .to_owned();
    let symbol_pair = pools
        .first()
        .map(|pool| format!("{}:{}", pool.token0_symbol, pool.token1_symbol))
        .unwrap_or_else(|| format!("{}:{}", hop.input_token_key_id, hop.output_token_key_id));
    OverviewHopCandidate {
        leg: hop.hop_name.clone(),
        direction,
        symbol_pair,
        pool_count: pools.len(),
        usable_pool_count,
        preferred_pool_id: Some(hop.pool_id.clone()),
        pools,
    }
}

fn hop_pool_source(hop_name: &str) -> HopPoolSource {
    if matches!(
        hop_name.trim().to_ascii_lowercase().as_str(),
        "y-z" | "middle" | "yz"
    ) {
        HopPoolSource::CandidateExchange
    } else {
        HopPoolSource::CandidateEdge
    }
}

#[allow(clippy::too_many_arguments)]
fn build_overview_path(
    loop_row: &ClosedLoopRow,
    review_row: Option<&ReviewRow>,
    broadcast_requested: bool,
    execution_pools: Vec<OverviewPool>,
    hop_candidates: Vec<OverviewHopCandidate>,
    tokens: &BTreeMap<String, TokenView>,
    pool_by_id: &BTreeMap<String, OverviewPool>,
) -> OverviewArbitragePath {
    let route_symbols = if loop_row.path_symbols.is_empty() {
        vec![loop_row.anchor_symbol.clone()]
    } else {
        loop_row.path_symbols.clone()
    };
    let target_symbol = route_symbols
        .get(1)
        .cloned()
        .unwrap_or_else(|| loop_row.anchor_symbol.clone());
    let route_has_liquidity_evidence = !execution_pools.is_empty()
        && execution_pools
            .iter()
            .all(|pool| pool.has_liquidity == Some(true));
    let route_usable_for_observation = !execution_pools.is_empty()
        && execution_pools.len() == loop_row.pool_ids.len()
        && execution_pools
            .iter()
            .all(|pool| pool.usable_for_observation);
    let overview_profit = build_overview_profit(review_row, broadcast_requested, tokens, loop_row);
    let candidate_pair_symbols = windows_symbols(&route_symbols);
    let execution_pools = if execution_pools.is_empty() {
        loop_row
            .pool_ids
            .iter()
            .filter_map(|pool_id| pool_by_id.get(pool_id).cloned())
            .collect()
    } else {
        execution_pools
    };

    OverviewArbitragePath {
        symbol_pair: format!("{}:{}", loop_row.anchor_symbol, target_symbol),
        route_symbols: route_symbols.clone(),
        route_addresses: loop_row.path_addresses.clone(),
        overview_route: OverviewRoute {
            kind: "closed_loop".to_owned(),
            path_text: loop_row.path_text.clone(),
            anchor_borrow_symbol: loop_row.anchor_symbol.clone(),
            target_symbol,
            candidate_pair_symbols,
        },
        overview_profit,
        route_framework_source: OVERVIEW_SOURCE.to_owned(),
        path_source: OVERVIEW_SOURCE.to_owned(),
        market_observation_source_stage: OVERVIEW_SOURCE.to_owned(),
        price_evidence_source: if review_row.and_then(|row| row.signed_pnl.as_ref()).is_some() {
            "manager_review_signed_pnl".to_owned()
        } else {
            "manager_review_pending".to_owned()
        },
        price_evidence_source_label:
            if review_row.and_then(|row| row.signed_pnl.as_ref()).is_some() {
                "Manager 复核 PnL"
            } else {
                "Manager 复核待完成"
            }
            .to_owned(),
        route_usable_for_observation,
        route_has_liquidity_evidence,
        preferred_pools: execution_pools.clone(),
        execution_pools,
        hop_candidates,
        hop_candidate_combination_count: loop_row.hop_candidate_combination_count,
        flash_loan_linkage: OverviewFlashLoanLinkage {
            borrow_symbols: vec![loop_row.anchor_symbol.clone()],
            route_symbols,
            anchor_token_key_ids: vec![loop_row.anchor_token_key_id.clone()],
        },
    }
}

fn build_overview_profit(
    review_row: Option<&ReviewRow>,
    broadcast_requested: bool,
    tokens: &BTreeMap<String, TokenView>,
    loop_row: &ClosedLoopRow,
) -> OverviewProfit {
    let signed_pnl = review_row.and_then(|row| row.signed_pnl.as_ref());
    let executable = review_row
        .and_then(|row| row.run_arbitrage.get("executable"))
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let usd_pnl = signed_pnl
        .filter(|pnl| pnl_is_usd(pnl))
        .map(|pnl| pnl.amount);
    let token_pnl = signed_pnl.filter(|pnl| !pnl_is_usd(pnl));
    let profitable = signed_pnl.is_some_and(|pnl| pnl.amount > 0.0);
    let chain_submission_candidate = broadcast_requested && profitable && executable;
    let anchor_is_stable = tokens
        .get(&loop_row.anchor_token_key_id)
        .is_some_and(|token| token.is_stable);
    let review_status = review_row
        .map(|row| row.review_status.clone())
        .unwrap_or_else(|| "review_pending".to_owned());
    let complete = review_status == "completed" && signed_pnl.is_some();
    let source = signed_pnl
        .map(|pnl| pnl.source.clone())
        .unwrap_or_else(|| "manager_review_pending".to_owned());
    let warnings = if complete {
        Vec::new()
    } else {
        vec!["signed_pnl_missing".to_owned()]
    };

    OverviewProfit {
        method: "manager_review_signed_pnl".to_owned(),
        notional_usd: 1_000.0,
        complete,
        requires_live_quoter: !complete,
        adapter_required: !complete,
        observed_gross_profit_usd: None,
        flash_loan_fee_usd: None,
        gas_reserve_usd: None,
        observed_net_profit_usd: usd_pnl,
        reference_net_profit_usd: usd_pnl,
        observed_net_profit_bps: None,
        reference_input_amount: None,
        reference_input_symbol: token_pnl.map(|pnl| pnl.currency.clone()),
        reference_final_output_amount: None,
        reference_final_output_symbol: None,
        reference_net_profit_amount: token_pnl.map(|pnl| pnl.amount),
        reference_net_return_bps: None,
        reference_return_basis: token_pnl.map(|_| "borrow_token".to_owned()),
        reference_return_trusted: token_pnl.map(|_| false),
        borrow_asset_class: tokens.get(&loop_row.anchor_token_key_id).map(|_| {
            if anchor_is_stable {
                "stable".to_owned()
            } else {
                "non_stable".to_owned()
            }
        }),
        borrow_asset_is_stable: tokens
            .get(&loop_row.anchor_token_key_id)
            .map(|_| anchor_is_stable),
        token_basis_profitable: token_pnl.is_some_and(|pnl| pnl.amount > 0.0),
        token_basis_reference_profitable: token_pnl.is_some_and(|pnl| pnl.amount > 0.0),
        chain_submission_candidate,
        profitable,
        executable_candidate: executable,
        trusted_usd_reference: usd_pnl.is_some(),
        token_decimals_trusted: tokens.contains_key(&loop_row.anchor_token_key_id),
        recommendation: OverviewRecommendation {
            action: review_status.clone(),
            level: if chain_submission_candidate {
                "executable"
            } else if complete {
                "reviewed"
            } else {
                "pending"
            }
            .to_owned(),
        },
        recommendation_level: if chain_submission_candidate {
            "executable"
        } else if complete {
            "reviewed"
        } else {
            "pending"
        }
        .to_owned(),
        recommendation_action: review_status,
        warnings,
        price_evidence_source: source.clone(),
        price_evidence_mode: if complete {
            "live_verified".to_owned()
        } else {
            "review_pending".to_owned()
        },
        price_evidence_source_label: source,
    }
}

fn build_watch_pairs(
    exchange: &CandidateExchangeSnapshot,
    tokens: &BTreeMap<String, TokenView>,
) -> Vec<OverviewWatchPair> {
    exchange
        .yz_pairs
        .iter()
        .map(|row| {
            let x_symbol = symbol_for(tokens, &row.token_x_key_id);
            let y_symbol = symbol_for(tokens, &row.token_y_key_id);
            let x_address = address_for(tokens, &row.token_x_key_id);
            let y_address = address_for(tokens, &row.token_y_key_id);
            OverviewWatchPair {
                anchor_token_key_id: row.anchor_token_key_id.clone(),
                direction_key: row.direction_key.clone(),
                symbol_pair: format!("{x_symbol}:{y_symbol}"),
                path_text: format!("{x_symbol} -> {y_symbol}"),
                address_pair: format!("{x_address}:{y_address}"),
                associated_fund_symbols: vec![symbol_for(tokens, &row.anchor_token_key_id)],
            }
        })
        .collect()
}

fn build_path_groups(paths: &[OverviewArbitragePath]) -> Vec<OverviewPathGroup> {
    let mut grouped = BTreeMap::<String, Vec<OverviewArbitragePath>>::new();
    for path in paths {
        grouped
            .entry(path.overview_route.anchor_borrow_symbol.clone())
            .or_default()
            .push(path.clone());
    }
    grouped
        .into_iter()
        .map(|(symbol, paths)| {
            let mut pool_dependencies = BTreeMap::<String, OverviewPool>::new();
            for pool in paths.iter().flat_map(|path| path.execution_pools.iter()) {
                pool_dependencies
                    .entry(pool.pool_id.clone())
                    .or_insert_with(|| pool.clone());
            }
            let token = OverviewTokenReference {
                token_key_id: paths
                    .first()
                    .and_then(|path| path.flash_loan_linkage.anchor_token_key_ids.first())
                    .cloned()
                    .unwrap_or_default(),
                address: paths
                    .first()
                    .and_then(|path| path.route_addresses.first())
                    .cloned()
                    .unwrap_or_default(),
                symbol: symbol.clone(),
            };
            OverviewPathGroup {
                flash_loan_token: token.clone(),
                start_token: token.clone(),
                end_token: token,
                paths,
                pool_dependencies: pool_dependencies.into_values().collect(),
            }
        })
        .collect()
}

fn build_summary(
    paths: &[OverviewArbitragePath],
    watch_pairs: &[OverviewWatchPair],
    review: &ReviewSnapshot,
    closed_loop: &ClosedLoopSnapshot,
    tokens: &BTreeMap<String, TokenView>,
) -> OverviewSummary {
    let mut anchor_symbols = paths
        .iter()
        .map(|path| path.overview_route.anchor_borrow_symbol.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    anchor_symbols.sort();
    let profitable = paths
        .iter()
        .filter(|path| path.overview_profit.profitable)
        .count();
    let complete = paths
        .iter()
        .filter(|path| path.overview_profit.complete)
        .count();
    let executable = paths
        .iter()
        .filter(|path| path.overview_profit.chain_submission_candidate)
        .count();
    let non_stable = paths
        .iter()
        .filter(|path| {
            path.overview_profit.chain_submission_candidate
                && !path
                    .flash_loan_linkage
                    .anchor_token_key_ids
                    .first()
                    .and_then(|token_key_id| tokens.get(token_key_id))
                    .is_some_and(|token| token.is_stable)
        })
        .count();
    let best_usd = paths
        .iter()
        .filter_map(|path| path.overview_profit.observed_net_profit_usd)
        .reduce(f64::max);
    let best_executable = paths
        .iter()
        .filter(|path| path.overview_profit.chain_submission_candidate)
        .filter_map(|path| path.overview_profit.observed_net_profit_usd)
        .reduce(f64::max);
    let bridge_path_count = paths
        .iter()
        .filter(|path| path.overview_route.candidate_pair_symbols.len() > 1)
        .count();

    OverviewSummary {
        arbitrage_path_count: paths.len(),
        candidate_path_count: closed_loop.status.loop_count,
        market_observation_route_count: watch_pairs.len(),
        chain_specific_observation_pair_count: watch_pairs.len(),
        overview_path_group_count: anchor_symbols.len(),
        watch_pair_count: watch_pairs.len(),
        bridge_path_count,
        closed_loop_anchor_symbols: anchor_symbols,
        overview_profitable_route_count: profitable,
        overview_probe_complete_count: complete,
        overview_chain_submission_candidate_count: executable,
        overview_non_stable_chain_submission_candidate_count: non_stable,
        overview_live_quoter_required_count: paths.len().saturating_sub(complete),
        overview_best_observed_net_profit_usd: best_usd,
        overview_probe_notional_usd: 1_000.0,
        overview_best_token_basis_net_return_bps: None,
        overview_best_executable_net_profit_usd: best_executable,
        candidate_cache_refreshed_at: review
            .reviews
            .iter()
            .map(|row| row.reviewed_at.clone())
            .max(),
        path_source: OVERVIEW_SOURCE.to_owned(),
        review_count: review.status.loop_count,
        review_completed_count: review.status.completed_count,
        review_data_incomplete_count: review.status.data_incomplete_count,
        review_not_executable_count: review.status.not_executable_count,
        review_failed_count: review.status.failed_count,
        broadcast_request_count: review.status.broadcast_request_count,
    }
}

fn build_versions(
    pipeline: &PipelineSnapshot,
    candidate: &CandidateEdgeSnapshot,
    exchange: &CandidateExchangeSnapshot,
    closed_loop: &ClosedLoopSnapshot,
    review: &ReviewSnapshot,
    broadcast: &BroadcastStatus,
) -> OverviewVersionSummary {
    let mut issues = pipeline
        .upstream_assessment
        .issues
        .iter()
        .map(|issue| {
            format!(
                "{}:{} expected={} actual={}",
                issue.component, issue.field, issue.expected, issue.actual
            )
        })
        .collect::<Vec<_>>();
    push_version_issue(
        &mut issues,
        "candidate_edge.version",
        closed_loop.status.candidate_edge_version,
        Some(candidate.status.candidate_version),
    );
    push_version_issue(
        &mut issues,
        "candidate_exchange.candidate_edge_version",
        exchange.status.candidate_edge_version,
        Some(candidate.status.candidate_version),
    );
    push_version_issue(
        &mut issues,
        "closed_loop.observation_version",
        closed_loop.status.observation_version,
        Some(exchange.status.observation_version),
    );
    push_version_issue(
        &mut issues,
        "review.closed_loop_version",
        review.status.closed_loop_version,
        Some(closed_loop.status.closed_loop_version),
    );
    push_version_issue(
        &mut issues,
        "broadcast.review_version",
        broadcast.review_version,
        Some(review.status.review_version),
    );
    push_checksum_issue(
        &mut issues,
        "candidate_exchange.candidate_edge_checksum",
        exchange.status.candidate_edge_checksum.as_deref(),
        candidate.status.materialized_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "closed_loop.candidate_edge_checksum",
        closed_loop.status.candidate_edge_checksum.as_deref(),
        candidate.status.materialized_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "closed_loop.observation_checksum",
        closed_loop.status.observation_checksum.as_deref(),
        exchange.status.materialized_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "closed_loop.pool_scope_checksum",
        closed_loop.status.pool_scope_checksum.as_deref(),
        exchange.status.pool_scope_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "review.candidate_edge_checksum",
        review.status.candidate_edge_checksum.as_deref(),
        candidate.status.materialized_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "review.observation_checksum",
        review.status.observation_checksum.as_deref(),
        exchange.status.materialized_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "review.closed_loop_checksum",
        review.status.closed_loop_checksum.as_deref(),
        closed_loop.status.materialized_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "review.pool_scope_checksum",
        review.status.pool_scope_checksum.as_deref(),
        closed_loop.status.pool_scope_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "candidate_exchange.pool_scope_checksum",
        exchange.status.pool_scope_checksum.as_deref(),
        candidate.status.pool_scope_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "closed_loop.pool_scope_checksum",
        closed_loop.status.pool_scope_checksum.as_deref(),
        candidate.status.pool_scope_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "broadcast.review_checksum",
        broadcast.review_checksum.as_deref(),
        review.status.materialized_checksum.as_deref(),
    );
    push_checksum_issue(
        &mut issues,
        "broadcast.closed_loop_checksum",
        broadcast.closed_loop_checksum.as_deref(),
        closed_loop.status.materialized_checksum.as_deref(),
    );
    issues.sort();
    issues.dedup();

    OverviewVersionSummary {
        library_versions: first_non_empty_map([
            &closed_loop.status.library_versions,
            &review.status.library_versions,
            &exchange.status.library_versions,
            &candidate.status.library_versions,
        ]),
        library_checksums: first_non_empty_map([
            &closed_loop.status.library_checksums,
            &review.status.library_checksums,
            &exchange.status.library_checksums,
            &candidate.status.library_checksums,
        ]),
        pool_scope_checksum: closed_loop
            .status
            .pool_scope_checksum
            .clone()
            .or_else(|| exchange.status.pool_scope_checksum.clone())
            .or_else(|| candidate.status.pool_scope_checksum.clone()),
        candidate_edge_version: candidate.status.candidate_version,
        candidate_edge_checksum: candidate.status.materialized_checksum.clone(),
        observation_version: exchange.status.observation_version,
        observation_checksum: exchange.status.materialized_checksum.clone(),
        closed_loop_version: closed_loop.status.closed_loop_version,
        closed_loop_checksum: closed_loop.status.materialized_checksum.clone(),
        review_version: review.status.review_version,
        review_checksum: review.status.materialized_checksum.clone(),
        broadcast_version: broadcast.broadcast_version,
        broadcast_checksum: broadcast.materialized_checksum.clone(),
        versions_consistent: issues.is_empty() && pipeline.upstream_assessment.consistent,
        consistency_issues: issues,
    }
}

fn push_version_issue(
    issues: &mut Vec<String>,
    field: &str,
    left: Option<i64>,
    right: Option<i64>,
) {
    if let (Some(left), Some(right)) = (left, right) {
        if left != right {
            issues.push(format!("{field} expected={left} actual={right}"));
        }
    }
}

fn push_checksum_issue(
    issues: &mut Vec<String>,
    field: &str,
    left: Option<&str>,
    right: Option<&str>,
) {
    if let (Some(left), Some(right)) = (left, right) {
        if left != right {
            issues.push(format!("{field} expected={left} actual={right}"));
        }
    }
}

fn first_non_empty_map<T, const N: usize>(
    candidates: [&BTreeMap<String, T>; N],
) -> BTreeMap<String, T>
where
    T: Clone,
{
    candidates
        .into_iter()
        .find(|candidate| !candidate.is_empty())
        .cloned()
        .unwrap_or_default()
}

fn count_missing_signed_pnl(closed_loop: &ClosedLoopSnapshot, review: &ReviewSnapshot) -> usize {
    let review_by_loop = review
        .reviews
        .iter()
        .map(|row| (row.loop_key.as_str(), row))
        .collect::<BTreeMap<_, _>>();
    closed_loop
        .loops
        .iter()
        .filter(|loop_row| {
            review_by_loop
                .get(loop_row.loop_key.as_str())
                .and_then(|row| row.signed_pnl.as_ref())
                .is_none()
        })
        .count()
}

fn count_unresolved_tokens(
    closed_loop: &ClosedLoopSnapshot,
    exchange: &CandidateExchangeSnapshot,
    candidate: &CandidateEdgeSnapshot,
    tokens: &BTreeMap<String, TokenView>,
) -> usize {
    let mut token_key_ids = BTreeSet::new();
    for row in &closed_loop.loops {
        token_key_ids.extend([
            row.anchor_token_key_id.as_str(),
            row.token_y_key_id.as_str(),
            row.token_z_key_id.as_str(),
        ]);
    }
    for row in &exchange.yz_pairs {
        token_key_ids.extend([
            row.anchor_token_key_id.as_str(),
            row.token_x_key_id.as_str(),
            row.token_y_key_id.as_str(),
        ]);
    }
    for row in &candidate.pools {
        token_key_ids.extend([row.token0_key_id.as_str(), row.token1_key_id.as_str()]);
    }
    for row in &exchange.pools {
        token_key_ids.extend([row.token0_key_id.as_str(), row.token1_key_id.as_str()]);
    }
    token_key_ids
        .into_iter()
        .filter(|token_key_id| !tokens.contains_key(*token_key_id))
        .count()
}

fn pool_matches_direction(
    pool: &CandidatePoolRow,
    input_token_key_id: &str,
    output_token_key_id: &str,
) -> bool {
    (pool.token0_key_id == input_token_key_id && pool.token1_key_id == output_token_key_id)
        || (pool.token0_key_id == output_token_key_id && pool.token1_key_id == input_token_key_id)
}

fn pool_is_usable(pool: &OverviewPool) -> bool {
    pool.has_liquidity == Some(true) && !pool.special_fee && !pool.dynamic_fee
}

fn pnl_is_usd(pnl: &review_manager::SignedPnl) -> bool {
    matches!(
        pnl.unit.trim().to_ascii_lowercase().as_str(),
        "usd" | "usdc" | "usdt"
    ) || matches!(
        pnl.currency.trim().to_ascii_uppercase().as_str(),
        "USD" | "USDC" | "USDT" | "USDG" | "USDE"
    )
}

fn windows_symbols(symbols: &[String]) -> Vec<String> {
    symbols
        .windows(2)
        .map(|window| format!("{}:{}", window[0], window[1]))
        .collect()
}

fn symbol_for(tokens: &BTreeMap<String, TokenView>, token_key_id: &str) -> String {
    tokens
        .get(token_key_id)
        .map(|token| token.symbol.clone())
        .unwrap_or_else(|| token_key_id.to_owned())
}

fn address_for(tokens: &BTreeMap<String, TokenView>, token_key_id: &str) -> String {
    tokens
        .get(token_key_id)
        .map(|token| token.token_address.clone())
        .unwrap_or_default()
}

fn depth_band(value: Option<&str>) -> BTreeMap<String, String> {
    let mut band = BTreeMap::new();
    if let Some(value) = value.filter(|value| !value.trim().is_empty()) {
        band.insert("label".to_owned(), value.to_owned());
    }
    band
}

fn format_fee(fee: i64) -> String {
    format!("{:.4}%", fee as f64 / 10_000.0)
}

fn address_is_zero(value: &str) -> bool {
    value.trim().eq_ignore_ascii_case(ZERO_ADDRESS)
}

fn upstream_error(manager: &'static str, error: impl std::fmt::Display) -> CompositeError {
    CompositeError::UpstreamRead {
        manager,
        message: error.to_string(),
    }
}

fn token_query_error(library: LibraryKey, error: TokenQueryError) -> CompositeError {
    CompositeError::UpstreamRead {
        manager: "token",
        message: format!("{}: {error}", library.as_str()),
    }
}

#[cfg(test)]
mod tests {
    use super::{hop_pool_source, pnl_is_usd, push_checksum_issue, windows_symbols, HopPoolSource};
    use review_manager::SignedPnl;
    use std::collections::BTreeMap;

    #[test]
    fn usd_pnl_detection_accepts_units_and_stable_currencies() {
        let mut pnl = SignedPnl {
            raw: "100".to_owned(),
            amount: 1.0,
            currency: "USDG".to_owned(),
            address: None,
            unit: "token".to_owned(),
            source: "fixture".to_owned(),
        };
        assert!(pnl_is_usd(&pnl));
        pnl.currency = "EXT".to_owned();
        pnl.unit = "usd".to_owned();
        assert!(pnl_is_usd(&pnl));
        pnl.unit = "token".to_owned();
        assert!(!pnl_is_usd(&pnl));
    }

    #[test]
    fn route_symbol_windows_do_not_require_usd_legs() {
        let symbols = vec![
            "EXT".to_owned(),
            "TZ".to_owned(),
            "AMZN".to_owned(),
            "EXT".to_owned(),
        ];
        assert_eq!(
            windows_symbols(&symbols),
            vec!["EXT:TZ", "TZ:AMZN", "AMZN:EXT"]
        );
    }

    #[test]
    fn yz_hop_candidates_use_exchange_pool_snapshots() {
        assert_eq!(hop_pool_source("y-z"), HopPoolSource::CandidateExchange);
        assert_eq!(hop_pool_source("middle"), HopPoolSource::CandidateExchange);
        assert_eq!(hop_pool_source("yc"), HopPoolSource::CandidateEdge);
        assert_eq!(hop_pool_source("zc"), HopPoolSource::CandidateEdge);
    }

    #[test]
    fn checksum_consistency_reports_downstream_drift() {
        let mut issues = Vec::new();
        push_checksum_issue(
            &mut issues,
            "review.closed_loop_checksum",
            Some("sha256:closed-loop-v1"),
            Some("sha256:closed-loop-v2"),
        );
        push_checksum_issue(
            &mut issues,
            "review.observation_checksum",
            Some("sha256:observation-v1"),
            Some("sha256:observation-v1"),
        );

        assert_eq!(
            issues,
            vec![
                "review.closed_loop_checksum expected=sha256:closed-loop-v1 actual=sha256:closed-loop-v2"
            ]
        );
    }

    #[test]
    fn empty_downstream_status_can_fall_back_to_upstream_library_versions() {
        let upstream = BTreeMap::from([("core".to_owned(), 7_i64)]);
        let downstream = BTreeMap::new();

        let selected = super::first_non_empty_map([&downstream, &upstream]);

        assert_eq!(selected, upstream);
    }
}
