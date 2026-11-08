use axum::{
    extract::{
        rejection::{JsonRejection, QueryRejection},
        Path, Query, State,
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use broadcast_manager::{
    clear_broadcast_records_write, finalize_broadcast_attempts_write, read_broadcast_snapshot_page,
    submit_broadcasts_write, AttemptCreateInput, AttemptFinalizeInput, BroadcastClearRequest,
    BroadcastError, BroadcastFinalizeRequest, BroadcastSubmitRequest,
    ExpectedUpstream as BroadcastExpectedUpstream,
};
use candidate_edge_pool_manager::{
    compare_candidate_edges, materialize_candidate_edges_write, read_candidate_snapshot,
    CandidateEdgeError, CandidateEdgeWriteRequest, ExpectedUpstream,
};
use candidate_exchange_pool_manager::{
    compare_candidate_exchanges, materialize_candidate_exchanges_write,
    read_candidate_exchange_snapshot, CandidateExchangeError, CandidateExchangeWriteRequest,
    ExpectedUpstream as ExchangeExpectedUpstream,
};
use closed_loop_manager::{
    compare_closed_loops, materialize_closed_loops_write, read_closed_loop_snapshot,
    ClosedLoopError, ClosedLoopWriteRequest, ExpectedUpstream as ClosedLoopExpectedUpstream,
};
use composite_manager::{
    begin_pipeline_step_write, complete_pipeline_step_write, read_manager_overview_snapshot,
    read_pipeline_run_snapshot, read_pipeline_runs, read_pipeline_status, refresh_pipeline_write,
    CompositeError, PipelineBeginStepRequest, PipelineCompleteStepRequest, PipelineRefreshRequest,
    PipelineStepName, StepCompletionStatus,
};
use manager_contracts::{
    ErrorCode, ManagerError, ManagerResponse, TokenAuthorityMode, MANAGER_DTO_VERSION,
};
use pool_manager::{materialize_scope_write, read_scope_status, ScopeError, ScopeWriteRequest};
use review_manager::{
    read_review_page, run_reviews_write, ExpectedUpstream as ReviewExpectedUpstream, ReviewError,
    ReviewEvidenceInput, ReviewOutcomeFilter, ReviewReadQuery, ReviewWriteRequest,
};
use serde::{Deserialize, Serialize};
use std::{collections::HashSet, net::SocketAddr, path::PathBuf, sync::Arc};
use token_manager::{
    audit_manager_database, change_admission, read_library_tokens, resolve_token_symbol,
    AdmissionChangeRequest, AdmissionError, AdmissionStatus, LibraryKey, ManagerAuditError,
    TokenQueryError, TokenSymbolResolution, ROBINHOOD_NETWORK,
};

pub const M0_MIGRATION_ID: &str = manager_db::MIGRATION_ID;
pub const M7_MIGRATION_ID: &str = manager_db::BROADCAST_MIGRATION_ID;
pub const M8_MIGRATION_ID: &str = manager_db::LATEST_MIGRATION_ID;

#[derive(Debug, Clone)]
pub struct AppState {
    pub migration_checksum: String,
    pub schema_checksum: String,
    pub token_database_path: Option<PathBuf>,
    pub token_network: String,
    pub token_authority: TokenAuthorityMode,
    pub candidate_authority: TokenAuthorityMode,
    pub exchange_authority: TokenAuthorityMode,
    pub closed_loop_authority: TokenAuthorityMode,
    pub review_authority: TokenAuthorityMode,
    pub broadcast_authority: TokenAuthorityMode,
    pub composite_authority: TokenAuthorityMode,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            migration_checksum: manager_db::migration_checksum(),
            schema_checksum: manager_db::schema_checksum(),
            token_database_path: None,
            token_network: ROBINHOOD_NETWORK.to_owned(),
            token_authority: TokenAuthorityMode::Legacy,
            candidate_authority: TokenAuthorityMode::Legacy,
            exchange_authority: TokenAuthorityMode::Legacy,
            closed_loop_authority: TokenAuthorityMode::Legacy,
            review_authority: TokenAuthorityMode::Legacy,
            broadcast_authority: TokenAuthorityMode::Legacy,
            composite_authority: TokenAuthorityMode::Legacy,
        }
    }
}

impl AppState {
    pub fn with_token_database(database_path: impl Into<PathBuf>) -> Self {
        Self {
            token_database_path: Some(database_path.into()),
            ..Self::default()
        }
    }

    pub fn with_token_authority(mut self, authority: TokenAuthorityMode) -> Self {
        self.token_authority = authority;
        self
    }

    pub fn with_candidate_authority(mut self, authority: TokenAuthorityMode) -> Self {
        self.candidate_authority = authority;
        self
    }

    pub fn with_exchange_authority(mut self, authority: TokenAuthorityMode) -> Self {
        self.exchange_authority = authority;
        self
    }

    pub fn with_closed_loop_authority(mut self, authority: TokenAuthorityMode) -> Self {
        self.closed_loop_authority = authority;
        self
    }

    pub fn with_review_authority(mut self, authority: TokenAuthorityMode) -> Self {
        self.review_authority = authority;
        self
    }

    pub fn with_broadcast_authority(mut self, authority: TokenAuthorityMode) -> Self {
        self.broadcast_authority = authority;
        self
    }

    pub fn with_composite_authority(mut self, authority: TokenAuthorityMode) -> Self {
        self.composite_authority = authority;
        self
    }
}

pub fn app() -> Router {
    app_with_state(Arc::new(AppState::default()))
}

pub fn app_with_state(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/healthz", get(health))
        .route("/v1/capabilities", get(capabilities))
        .route("/v1/tokens", get(list_tokens))
        .route("/v1/tokens/audit", get(audit_tokens))
        .route("/v1/tokens/resolve", post(resolve_token))
        .route("/v1/tokens/admit", post(admit_token))
        .route("/v1/tokens/block", post(block_token))
        .route("/v1/pools/scopes", get(list_pool_scope))
        .route("/v1/pools/scopes/rebuild", post(rebuild_pool_scope))
        .route("/v1/pools/scopes/repair", post(repair_pool_scope))
        .route("/v1/candidate-edge-pools", get(list_candidate_edge_pools))
        .route(
            "/v1/candidate-edge-pools/compare",
            get(compare_candidate_edge_pools),
        )
        .route(
            "/v1/candidate-edge-pools/rebuild",
            post(rebuild_candidate_edge_pools),
        )
        .route(
            "/v1/candidate-exchange-pools",
            get(list_candidate_exchange_pools),
        )
        .route(
            "/v1/candidate-exchange-pools/compare",
            get(compare_candidate_exchange_pools),
        )
        .route(
            "/v1/candidate-exchange-pools/rebuild",
            post(rebuild_candidate_exchange_pools),
        )
        .route("/v1/closed-loops", get(list_closed_loops))
        .route("/v1/closed-loops/compare", get(compare_closed_loop_catalog))
        .route("/v1/closed-loops/rebuild", post(rebuild_closed_loops))
        .route("/v1/reviews", get(list_reviews))
        .route("/v1/reviews/run", post(run_reviews_endpoint))
        .route("/v1/overview", get(overview_endpoint))
        .route("/v1/broadcasts", get(list_broadcasts))
        .route("/v1/broadcasts/submit", post(submit_broadcasts_endpoint))
        .route(
            "/v1/broadcasts/finalize",
            post(finalize_broadcasts_endpoint),
        )
        .route("/v1/broadcasts/clear", post(clear_broadcasts_endpoint))
        .route("/v1/pipeline/status", get(pipeline_status_endpoint))
        .route("/v1/pipeline/runs", get(list_pipeline_runs))
        .route("/v1/pipeline/runs/{run_id}", get(get_pipeline_run))
        .route("/v1/pipeline/refresh", post(refresh_pipeline_endpoint))
        .route(
            "/v1/pipeline/steps/begin",
            post(begin_pipeline_step_endpoint),
        )
        .route(
            "/v1/pipeline/steps/complete",
            post(complete_pipeline_step_endpoint),
        )
        .with_state(state)
}

pub async fn serve(bind: SocketAddr) -> std::io::Result<()> {
    serve_with_state(bind, Arc::new(AppState::default())).await
}

pub async fn serve_with_state(bind: SocketAddr, state: Arc<AppState>) -> std::io::Result<()> {
    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app_with_state(state)).await
}

async fn health() -> impl IntoResponse {
    (
        StatusCode::OK,
        Json(HealthResponse {
            ok: true,
            status: "ok",
            version: MANAGER_DTO_VERSION,
        }),
    )
}

async fn capabilities(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Json(CapabilitiesResponse {
        ok: true,
        version: MANAGER_DTO_VERSION,
        data: CapabilitiesData {
            milestone: "M8",
            migration_id: M8_MIGRATION_ID,
            migration_checksum: state.migration_checksum.clone(),
            schema_checksum: state.schema_checksum.clone(),
            token_authority_mode: state.token_authority.as_str(),
            candidate_authority_mode: state.candidate_authority.as_str(),
            exchange_authority_mode: state.exchange_authority.as_str(),
            closed_loop_authority_mode: state.closed_loop_authority.as_str(),
            review_authority_mode: state.review_authority.as_str(),
            broadcast_authority_mode: state.broadcast_authority.as_str(),
            composite_authority_mode: state.composite_authority.as_str(),
            business_writes_enabled: state.token_database_path.is_some()
                && state.token_authority.manager_apply_enabled(),
            token_reads_enabled: state.token_database_path.is_some(),
            pool_reads_enabled: state.token_database_path.is_some(),
            pool_writes_enabled: state.token_database_path.is_some()
                && state.token_authority.manager_apply_enabled(),
            candidate_reads_enabled: state.token_database_path.is_some(),
            candidate_writes_enabled: state.token_database_path.is_some()
                && state.candidate_authority.manager_apply_enabled(),
            exchange_reads_enabled: state.token_database_path.is_some(),
            exchange_writes_enabled: state.token_database_path.is_some()
                && state.exchange_authority.manager_apply_enabled(),
            closed_loop_reads_enabled: state.token_database_path.is_some(),
            closed_loop_writes_enabled: state.token_database_path.is_some()
                && state.closed_loop_authority.manager_apply_enabled(),
            overview_reads_enabled: state.token_database_path.is_some(),
            review_reads_enabled: state.token_database_path.is_some(),
            review_writes_enabled: state.token_database_path.is_some()
                && state.review_authority.manager_apply_enabled(),
            broadcast_reads_enabled: state.token_database_path.is_some(),
            broadcast_writes_enabled: state.token_database_path.is_some()
                && state.broadcast_authority.manager_apply_enabled(),
            pipeline_reads_enabled: state.token_database_path.is_some(),
            pipeline_writes_enabled: state.token_database_path.is_some()
                && state.composite_authority.manager_apply_enabled(),
            dry_run_enabled: state.token_database_path.is_some(),
            capabilities: vec![
                "health",
                "capabilities",
                "tokens.query",
                "tokens.resolve",
                "tokens.admit",
                "tokens.block",
                "tokens.audit",
                "pools.scope.read",
                "pools.scope.rebuild",
                "pools.scope.repair",
                "candidate_edge.read",
                "candidate_edge.compare",
                "candidate_edge.rebuild",
                "candidate_exchange.read",
                "candidate_exchange.compare",
                "candidate_exchange.rebuild",
                "closed_loop.read",
                "closed_loop.compare",
                "closed_loop.rebuild",
                "review.read",
                "review.run",
                "broadcast.read",
                "broadcast.submit",
                "broadcast.finalize",
                "broadcast.clear",
                "pipeline.read",
                "pipeline.refresh",
                "pipeline.step.begin",
                "pipeline.step.complete",
                "overview.read",
            ],
        },
    })
}

async fn list_tokens(
    State(state): State<Arc<AppState>>,
    query: Result<Query<ListTokensQuery>, QueryRejection>,
) -> Response {
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => {
            return failure(invalid_request(rejection.body_text(), "list_tokens"));
        }
    };
    let library = match parse_library(&query.library, "list_tokens") {
        Ok(library) => library,
        Err(error) => return failure(error),
    };
    let status = match parse_status(query.status.as_deref(), Some(AdmissionStatus::Admitted)) {
        Ok(status) => status,
        Err(error) => return failure(error),
    };
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            "list_tokens",
        ));
    };

    match read_library_tokens(
        database_path,
        &state.token_network,
        query.chain_id,
        library,
        status,
    ) {
        Ok(page) => success(query.chain_id, page),
        Err(error) => failure(query_failure(error, query.chain_id, "list_tokens")),
    }
}

async fn audit_tokens(
    State(state): State<Arc<AppState>>,
    query: Result<Query<TokenAuditQuery>, QueryRejection>,
) -> Response {
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => {
            return failure(invalid_request(rejection.body_text(), "tokens.audit"));
        }
    };
    if query.chain_id == 0 {
        return failure(invalid_request(
            "chainId must be greater than zero",
            "tokens.audit",
        ));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(
            upstream_unavailable("MANAGER_DB_PATH is not configured", "tokens.audit")
                .with_details(serde_json::json!({"chainId": query.chain_id})),
        );
    };
    let recent_event_limit = query.recent_event_limit.unwrap_or(20);

    match audit_manager_database(
        database_path,
        &state.token_network,
        query.chain_id,
        recent_event_limit,
    ) {
        Ok(audit) => success(query.chain_id, audit),
        Err(error) => failure(audit_failure(error, query.chain_id)),
    }
}

async fn admit_token(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<TokenAdmissionHttpRequest>, JsonRejection>,
) -> Response {
    change_token_admission(
        state,
        headers,
        request,
        AdmissionStatus::Admitted,
        "tokens.admit",
    )
}

async fn block_token(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<TokenAdmissionHttpRequest>, JsonRejection>,
) -> Response {
    change_token_admission(
        state,
        headers,
        request,
        AdmissionStatus::Blocked,
        "tokens.block",
    )
}

async fn list_pool_scope(
    State(state): State<Arc<AppState>>,
    query: Result<Query<PoolScopeQuery>, QueryRejection>,
) -> Response {
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => {
            return failure(invalid_request(rejection.body_text(), "pools.scope.read"));
        }
    };
    if query.chain_id == 0 {
        return failure(invalid_request(
            "chainId must be greater than zero",
            "pools.scope.read",
        ));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            "pools.scope.read",
        ));
    };
    let scope_key = query.scope_key.as_deref().unwrap_or("core4");
    match read_scope_status(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        scope_key,
    ) {
        Ok(status) => success(query.chain_id, status),
        Err(error) => failure(scope_failure(error, query.chain_id, "pools.scope.read")),
    }
}

async fn rebuild_pool_scope(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<PoolScopeWriteRequest>, JsonRejection>,
) -> Response {
    change_pool_scope(state, headers, request, "pools.scope.rebuild")
}

async fn repair_pool_scope(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<PoolScopeWriteRequest>, JsonRejection>,
) -> Response {
    change_pool_scope(state, headers, request, "pools.scope.repair")
}

async fn list_candidate_edge_pools(
    State(state): State<Arc<AppState>>,
    query: Result<Query<CandidateEdgeQuery>, QueryRejection>,
) -> Response {
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => {
            return failure(invalid_request(
                rejection.body_text(),
                "candidate_edge.read",
            ));
        }
    };
    if query.chain_id == 0 {
        return failure(invalid_request(
            "chainId must be greater than zero",
            "candidate_edge.read",
        ));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            "candidate_edge.read",
        ));
    };
    let limit = query.limit.unwrap_or(200);
    if limit == 0 || limit > 1_000 {
        return failure(invalid_request(
            "limit must be between 1 and 1000",
            "candidate_edge.read",
        ));
    }
    match read_candidate_snapshot(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        query.anchor_token_key_id.as_deref(),
        limit,
    ) {
        Ok(snapshot) => success(query.chain_id, snapshot),
        Err(error) => failure(candidate_failure(
            error,
            query.chain_id,
            "candidate_edge.read",
        )),
    }
}

async fn compare_candidate_edge_pools(
    State(state): State<Arc<AppState>>,
    query: Result<Query<CandidateEdgeComparisonQuery>, QueryRejection>,
) -> Response {
    let stage = "candidate_edge.compare";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let sample_limit = query.sample_limit.unwrap_or(50);
    if sample_limit == 0 || sample_limit > 500 {
        return failure(invalid_request(
            "sampleLimit must be between 1 and 500",
            stage,
        ));
    }
    let legacy_anchor_scope = query
        .legacy_anchor_scope
        .unwrap_or_else(|| format!("dex-arbitrage-{}-uniswap-v4", query.chain_id));
    if legacy_anchor_scope.trim().is_empty() {
        return failure(invalid_request(
            "legacyAnchorScope must not be empty",
            stage,
        ));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match compare_candidate_edges(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        &legacy_anchor_scope,
        sample_limit,
    ) {
        Ok(comparison) => success(query.chain_id, comparison),
        Err(error) => failure(candidate_failure(error, query.chain_id, stage)),
    }
}

async fn rebuild_candidate_edge_pools(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<CandidateEdgeHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "candidate_edge.rebuild";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.candidate_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Candidate Edge Manager apply is disabled while authority mode is {}",
                    state.candidate_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.candidate_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match materialize_candidate_edges_write(
        database_path,
        CandidateEdgeWriteRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            expected_upstream: request.expected_upstream.unwrap_or_default(),
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            candidate_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn list_candidate_exchange_pools(
    State(state): State<Arc<AppState>>,
    query: Result<Query<CandidateExchangeQuery>, QueryRejection>,
) -> Response {
    let stage = "candidate_exchange.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let limit = query.limit.unwrap_or(200);
    if limit == 0 || limit > 1_000 {
        return failure(invalid_request("limit must be between 1 and 1000", stage));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_candidate_exchange_snapshot(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        query.anchor_token_key_id.as_deref(),
        limit,
    ) {
        Ok(snapshot) => success(query.chain_id, snapshot),
        Err(error) => failure(candidate_exchange_failure(error, query.chain_id, stage)),
    }
}

async fn compare_candidate_exchange_pools(
    State(state): State<Arc<AppState>>,
    query: Result<Query<CandidateExchangeComparisonQuery>, QueryRejection>,
) -> Response {
    let stage = "candidate_exchange.compare";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let sample_limit = query.sample_limit.unwrap_or(50);
    if sample_limit == 0 || sample_limit > 500 {
        return failure(invalid_request(
            "sampleLimit must be between 1 and 500",
            stage,
        ));
    }
    let legacy_anchor_scope = query
        .legacy_anchor_scope
        .unwrap_or_else(|| format!("dex-arbitrage-{}-uniswap-v4", query.chain_id));
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match compare_candidate_exchanges(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        &legacy_anchor_scope,
        sample_limit,
    ) {
        Ok(comparison) => success(query.chain_id, comparison),
        Err(error) => failure(candidate_exchange_failure(error, query.chain_id, stage)),
    }
}

async fn rebuild_candidate_exchange_pools(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<CandidateExchangeHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "candidate_exchange.rebuild";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.exchange_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Candidate Exchange Manager apply is disabled while authority mode is {}",
                    state.exchange_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.exchange_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match materialize_candidate_exchanges_write(
        database_path,
        CandidateExchangeWriteRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            expected_upstream: request.expected_upstream.unwrap_or_default(),
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            candidate_exchange_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn list_closed_loops(
    State(state): State<Arc<AppState>>,
    query: Result<Query<ClosedLoopQuery>, QueryRejection>,
) -> Response {
    let stage = "closed_loop.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let limit = query.limit.unwrap_or(200);
    if limit == 0 || limit > 1_000 {
        return failure(invalid_request("limit must be between 1 and 1000", stage));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_closed_loop_snapshot(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        query.anchor_token_key_id.as_deref(),
        limit,
    ) {
        Ok(snapshot) => success(query.chain_id, snapshot),
        Err(error) => failure(closed_loop_failure(error, query.chain_id, stage)),
    }
}

async fn compare_closed_loop_catalog(
    State(state): State<Arc<AppState>>,
    query: Result<Query<ClosedLoopComparisonQuery>, QueryRejection>,
) -> Response {
    let stage = "closed_loop.compare";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let sample_limit = query.sample_limit.unwrap_or(50);
    if sample_limit == 0 || sample_limit > 500 {
        return failure(invalid_request(
            "sampleLimit must be between 1 and 500",
            stage,
        ));
    }
    let legacy_market_id = query
        .legacy_market_id
        .unwrap_or_else(|| format!("dex-arbitrage-{}-uniswap-v4", query.chain_id));
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match compare_closed_loops(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        &legacy_market_id,
        sample_limit,
    ) {
        Ok(comparison) => success(query.chain_id, comparison),
        Err(error) => failure(closed_loop_failure(error, query.chain_id, stage)),
    }
}

async fn rebuild_closed_loops(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<ClosedLoopHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "closed_loop.rebuild";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.closed_loop_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Closed Loop Manager apply is disabled while authority mode is {}",
                    state.closed_loop_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.closed_loop_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match materialize_closed_loops_write(
        database_path,
        ClosedLoopWriteRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            expected_upstream: request.expected_upstream.unwrap_or_default(),
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            closed_loop_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn list_reviews(
    State(state): State<Arc<AppState>>,
    query: Result<Query<ReviewQuery>, QueryRejection>,
) -> Response {
    let stage = "review.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let limit = query.limit.unwrap_or(200);
    if limit == 0 || limit > 1_000 {
        return failure(invalid_request("limit must be between 1 and 1000", stage));
    }
    let offset = query.offset.unwrap_or(0);
    if offset.saturating_add(limit) > 1_000 {
        return failure(invalid_request(
            "offset plus limit must not exceed 1000",
            stage,
        ));
    }
    let loop_key = query
        .loop_key
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if loop_key.is_some() && offset != 0 {
        return failure(invalid_request(
            "offset must be zero when loopKey is supplied",
            stage,
        ));
    }
    let category = query
        .category
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if category.is_some_and(|value| {
        !matches!(
            value,
            "ready_not_submitted"
                | "confirmed_no_profit"
                | "data_incomplete"
                | "review_failed"
                | "other"
        )
    }) {
        return failure(invalid_request(
            "category must be ready_not_submitted, confirmed_no_profit, data_incomplete, review_failed, or other",
            stage,
        ));
    }
    let review_status = query
        .status
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if review_status.is_some_and(|value| {
        !matches!(
            value,
            "completed" | "data_incomplete" | "not_executable" | "failed"
        )
    }) {
        return failure(invalid_request(
            "status must be completed, data_incomplete, not_executable, or failed",
            stage,
        ));
    }
    let outcome = match query.outcome.as_deref().map(str::trim) {
        None | Some("") => None,
        Some(value) => match ReviewOutcomeFilter::parse(value) {
            Some(outcome) => Some(outcome),
            None => {
                return failure(invalid_request("outcome must be success or failed", stage));
            }
        },
    };
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_review_page(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        ReviewReadQuery {
            loop_key,
            category,
            review_status,
            outcome,
            offset,
            limit,
        },
    ) {
        Ok(page) => success(query.chain_id, page),
        Err(error) => failure(review_failure(error, query.chain_id, stage)),
    }
}

async fn run_reviews_endpoint(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<ReviewHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "review.run";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.review_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Review Manager apply is disabled while authority mode is {}",
                    state.review_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.review_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match run_reviews_write(
        database_path,
        ReviewWriteRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            expected_upstream: request.expected_upstream.unwrap_or_default(),
            evidence: request.evidence,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            review_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn overview_endpoint(
    State(state): State<Arc<AppState>>,
    query: Result<Query<OverviewQuery>, QueryRejection>,
) -> Response {
    let stage = "overview.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let limit = query.limit.unwrap_or(1_000);
    if limit == 0 || limit > 1_000 {
        return failure(invalid_request("limit must be between 1 and 1000", stage));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_manager_overview_snapshot(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        limit,
    ) {
        Ok(snapshot) => success(query.chain_id, snapshot),
        Err(error) => failure(composite_failure(error, query.chain_id, stage)),
    }
}

async fn list_broadcasts(
    State(state): State<Arc<AppState>>,
    query: Result<Query<BroadcastQuery>, QueryRejection>,
) -> Response {
    let stage = "broadcast.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let limit = query.limit.unwrap_or(200);
    if limit == 0 || limit > 1_000 {
        return failure(invalid_request("limit must be between 1 and 1000", stage));
    }
    let status = query
        .status
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    if status.is_some_and(|value| !matches!(value, "pending" | "success" | "failed")) {
        return failure(invalid_request(
            "status must be pending, success, or failed",
            stage,
        ));
    }
    let offset = query.offset.unwrap_or(0);
    if offset.saturating_add(limit) > 1_000 {
        return failure(invalid_request(
            "offset plus limit must not exceed 1000",
            stage,
        ));
    }
    if query.attempt_id.is_some() && offset != 0 {
        return failure(invalid_request(
            "offset must be zero when attemptId is supplied",
            stage,
        ));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_broadcast_snapshot_page(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        query.attempt_id.as_deref(),
        status,
        offset,
        limit,
    ) {
        Ok(mut snapshot) => {
            let attempt_ids = snapshot
                .attempts
                .iter()
                .map(|attempt| attempt.attempt_id.as_str())
                .collect::<HashSet<_>>();
            snapshot
                .diagnostics
                .retain(|diagnostic| attempt_ids.contains(diagnostic.attempt_id.as_str()));
            success(query.chain_id, snapshot)
        }
        Err(error) => failure(broadcast_failure(error, query.chain_id, stage)),
    }
}

async fn submit_broadcasts_endpoint(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<BroadcastSubmitHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "broadcast.submit";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.broadcast_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Broadcast Manager apply is disabled while authority mode is {}",
                    state.broadcast_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.broadcast_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match submit_broadcasts_write(
        database_path,
        BroadcastSubmitRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            expected_upstream: request.expected_upstream.unwrap_or_default(),
            attempts: request.attempts,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            broadcast_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn finalize_broadcasts_endpoint(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<BroadcastFinalizeHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "broadcast.finalize";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.broadcast_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Broadcast Manager apply is disabled while authority mode is {}",
                    state.broadcast_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.broadcast_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match finalize_broadcast_attempts_write(
        database_path,
        BroadcastFinalizeRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            expected_upstream: request.expected_upstream.unwrap_or_default(),
            finalizations: request.finalizations,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            broadcast_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn clear_broadcasts_endpoint(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<BroadcastClearHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "broadcast.clear";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.broadcast_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Broadcast Manager apply is disabled while authority mode is {}",
                    state.broadcast_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.broadcast_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match clear_broadcast_records_write(
        database_path,
        BroadcastClearRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            broadcast_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn pipeline_status_endpoint(
    State(state): State<Arc<AppState>>,
    query: Result<Query<PipelineQuery>, QueryRejection>,
) -> Response {
    let stage = "pipeline.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_pipeline_status(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
    ) {
        Ok(snapshot) => success(query.chain_id, snapshot),
        Err(error) => failure(composite_failure(error, query.chain_id, stage)),
    }
}

async fn list_pipeline_runs(
    State(state): State<Arc<AppState>>,
    query: Result<Query<PipelineQuery>, QueryRejection>,
) -> Response {
    let stage = "pipeline.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let limit = query.limit.unwrap_or(50);
    if limit == 0 || limit > 200 {
        return failure(invalid_request("limit must be between 1 and 200", stage));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_pipeline_runs(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        limit,
    ) {
        Ok(runs) => success(query.chain_id, runs),
        Err(error) => failure(composite_failure(error, query.chain_id, stage)),
    }
}

async fn get_pipeline_run(
    State(state): State<Arc<AppState>>,
    query: Result<Query<PipelineRunQuery>, QueryRejection>,
    Path(run_id): Path<String>,
) -> Response {
    let stage = "pipeline.read";
    let Query(query) = match query {
        Ok(query) => query,
        Err(rejection) => return failure(invalid_request(rejection.body_text(), stage)),
    };
    if query.chain_id == 0 {
        return failure(invalid_request("chainId must be greater than zero", stage));
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            stage,
        ));
    };
    match read_pipeline_run_snapshot(
        database_path,
        &network_for_chain_id(query.chain_id),
        query.chain_id as i64,
        &run_id,
    ) {
        Ok(detail) => success(query.chain_id, detail),
        Err(error) => failure(composite_failure(error, query.chain_id, stage)),
    }
}

async fn refresh_pipeline_endpoint(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<PipelineRefreshHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "pipeline.refresh";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.composite_authority.manager_apply_enabled() {
        return failure_with_request_id(
            composite_authority_denied(state.composite_authority, request.chain_id, stage),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    let requested_steps = request
        .requested_steps
        .unwrap_or_else(default_pipeline_steps);
    match refresh_pipeline_write(
        database_path,
        PipelineRefreshRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            requested_steps,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            composite_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn begin_pipeline_step_endpoint(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<PipelineStepBeginHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "pipeline.step.begin";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.composite_authority.manager_apply_enabled() {
        return failure_with_request_id(
            composite_authority_denied(state.composite_authority, request.chain_id, stage),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match begin_pipeline_step_write(
        database_path,
        PipelineBeginStepRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            run_id: request.run_id,
            step_id: request.step_id,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            composite_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

async fn complete_pipeline_step_endpoint(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    request: Result<Json<PipelineStepCompleteHttpRequest>, JsonRejection>,
) -> Response {
    let stage = "pipeline.step.complete";
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.composite_authority.manager_apply_enabled() {
        return failure_with_request_id(
            composite_authority_denied(state.composite_authority, request.chain_id, stage),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    match complete_pipeline_step_write(
        database_path,
        PipelineCompleteStepRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
            run_id: request.run_id,
            step_id: request.step_id,
            status: request.status,
            result: request.result.unwrap_or(serde_json::json!({})),
            error: request.error,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            composite_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

fn change_pool_scope(
    state: Arc<AppState>,
    headers: HeaderMap,
    request: Result<Json<PoolScopeWriteRequest>, JsonRejection>,
    stage: &str,
) -> Response {
    let request_id = match required_write_header_for(&headers, "X-Request-Id", stage) {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header_for(&headers, "Idempotency-Key", stage) {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.token_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Pool Manager apply is disabled while authority mode is {}",
                    state.token_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.token_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    let scope_key = request.scope_key.as_deref().unwrap_or("core4");
    match materialize_scope_write(
        database_path,
        ScopeWriteRequest {
            network: network_for_chain_id(request.chain_id),
            chain_id: request.chain_id as i64,
            scope_key: scope_key.to_owned(),
            operation: stage.to_owned(),
            request_id: request_id.clone(),
            idempotency_key,
            apply: !dry_run,
        },
    ) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            scope_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

fn change_token_admission(
    state: Arc<AppState>,
    headers: HeaderMap,
    request: Result<Json<TokenAdmissionHttpRequest>, JsonRejection>,
    target_status: AdmissionStatus,
    stage: &str,
) -> Response {
    let request_id = match required_write_header(&headers, "X-Request-Id") {
        Ok(value) => value,
        Err(error) => return failure(error),
    };
    let idempotency_key = match required_write_header(&headers, "Idempotency-Key") {
        Ok(value) => value,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure_with_request_id(
                invalid_request(rejection.body_text(), stage),
                Some(&request_id),
            );
        }
    };
    let Some(dry_run) = request.dry_run else {
        return failure_with_request_id(
            invalid_request("dryRun is required and must be true or false", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };
    if request.chain_id == 0 {
        return failure_with_request_id(
            invalid_request("chainId must be greater than zero", stage),
            Some(&request_id),
        );
    }
    if !dry_run && !state.token_authority.manager_apply_enabled() {
        return failure_with_request_id(
            ManagerError::new(
                ErrorCode::TokenAuthorityWriteDenied,
                format!(
                    "Token Manager admission apply is disabled while authority mode is {}",
                    state.token_authority
                ),
                stage,
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "authorityMode": state.token_authority.as_str(),
                "dryRun": false
            })),
            Some(&request_id),
        );
    }
    let library = match parse_library(&request.library_key, stage) {
        Ok(library) => library,
        Err(error) => return failure_with_request_id(error, Some(&request_id)),
    };
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure_with_request_id(
            upstream_unavailable("MANAGER_DB_PATH is not configured", stage)
                .with_details(serde_json::json!({"chainId": request.chain_id})),
            Some(&request_id),
        );
    };

    let admission_request = AdmissionChangeRequest {
        network: state.token_network.clone(),
        chain_id: request.chain_id,
        library,
        token_key_id: request.token_key_id,
        target_status,
        reason: request.reason,
        evidence: request.evidence,
        request_id: request_id.clone(),
        idempotency_key,
        dry_run,
        actor: "manager_api".to_owned(),
    };
    match change_admission(database_path, admission_request) {
        Ok(outcome) => {
            success_with_request_id(request.chain_id, outcome, Some(request_id.as_str()))
        }
        Err(error) => failure_with_request_id(
            admission_failure(error, request.chain_id, stage),
            Some(&request_id),
        ),
    }
}

fn required_write_header(headers: &HeaderMap, name: &str) -> Result<String, ManagerError> {
    required_write_header_for(headers, name, "token_admission")
}

fn required_write_header_for(
    headers: &HeaderMap,
    name: &str,
    stage: &str,
) -> Result<String, ManagerError> {
    let value = headers
        .get(name)
        .ok_or_else(|| invalid_request(format!("{name} header is required"), stage))?;
    let value = value.to_str().map_err(|_| {
        invalid_request(
            format!("{name} header must contain visible ASCII characters"),
            stage,
        )
    })?;
    if value.trim().is_empty() {
        return Err(invalid_request(
            format!("{name} header must not be empty"),
            stage,
        ));
    }
    Ok(value.trim().to_owned())
}

async fn resolve_token(
    State(state): State<Arc<AppState>>,
    request: Result<Json<ResolveTokenRequest>, JsonRejection>,
) -> Response {
    let Json(request) = match request {
        Ok(request) => request,
        Err(rejection) => {
            return failure(invalid_request(rejection.body_text(), "resolve_token"));
        }
    };
    let required_status = match parse_status(request.required_status.as_deref(), None) {
        Ok(status) => status,
        Err(error) => return failure(error),
    };
    let Some(database_path) = state.token_database_path.as_deref() else {
        return failure(upstream_unavailable(
            "MANAGER_DB_PATH is not configured",
            "resolve_token",
        ));
    };

    match resolve_token_symbol(
        database_path,
        &state.token_network,
        request.chain_id,
        &request.symbol,
        required_status,
    ) {
        Ok(TokenSymbolResolution::Unique { token }) => success(request.chain_id, token),
        Ok(TokenSymbolResolution::NotFound) => failure(
            ManagerError::new(
                ErrorCode::TokenNotFound,
                format!("token symbol {} was not found", request.symbol.trim()),
                "resolve_token",
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "symbol": request.symbol.trim().to_ascii_uppercase()
            })),
        ),
        Ok(TokenSymbolResolution::Ambiguous { candidates }) => failure(
            ManagerError::new(
                ErrorCode::TokenSymbolAmbiguous,
                format!(
                    "token symbol {} resolves to multiple token_key_id values",
                    request.symbol.trim()
                ),
                "resolve_token",
            )
            .with_details(serde_json::json!({
                "chainId": request.chain_id,
                "symbol": request.symbol.trim().to_ascii_uppercase(),
                "candidateTokenKeyIds": candidates
                    .iter()
                    .map(|candidate| candidate.token_key_id.as_str())
                    .collect::<Vec<_>>()
            })),
        ),
        Err(error) => failure(query_failure(error, request.chain_id, "resolve_token")),
    }
}

fn parse_library(value: &str, stage: &str) -> Result<LibraryKey, ManagerError> {
    let normalized = value.trim().to_ascii_lowercase();
    LibraryKey::from_key(&normalized).ok_or_else(|| {
        invalid_request(
            format!("unknown library {value}; expected core, stocks, extension, or test"),
            stage,
        )
    })
}

fn parse_status(
    value: Option<&str>,
    default: Option<AdmissionStatus>,
) -> Result<Option<AdmissionStatus>, ManagerError> {
    let Some(value) = value else {
        return Ok(default);
    };
    let normalized = value.trim().to_ascii_lowercase();
    if normalized == "all" {
        return Ok(None);
    }
    AdmissionStatus::from_storage(&normalized)
        .map(Some)
        .ok_or_else(|| {
            invalid_request(
                format!(
                    "unknown admission status {value}; expected admitted, blocked, \
                     candidate_only, pending_review, or all"
                ),
                "token_status",
            )
        })
}

fn query_failure(error: TokenQueryError, chain_id: u64, stage: &str) -> ManagerError {
    match error {
        TokenQueryError::InvalidSymbol(symbol) => {
            invalid_request(format!("invalid token symbol: {symbol}"), stage)
                .with_details(serde_json::json!({"chainId": chain_id}))
        }
        other => upstream_unavailable(other.to_string(), stage).with_details(serde_json::json!({
            "chainId": chain_id,
            "reason": other.to_string()
        })),
    }
}

fn admission_failure(error: AdmissionError, chain_id: u64, stage: &str) -> ManagerError {
    match error {
        AdmissionError::InvalidRequest(message) => {
            invalid_request(message, stage).with_details(serde_json::json!({"chainId": chain_id}))
        }
        AdmissionError::TokenNotFound {
            library,
            token_key_id,
        } => ManagerError::new(
            ErrorCode::TokenNotFound,
            format!(
                "token {token_key_id} is not present in library {}",
                library.as_str()
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "libraryKey": library.as_str(),
            "tokenKeyId": token_key_id
        })),
        AdmissionError::LibraryStateMissing {
            network, library, ..
        } => ManagerError::new(
            ErrorCode::TokenLibraryConflict,
            format!(
                "library state is missing for {network}:{chain_id}:{}",
                library.as_str()
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "libraryKey": library.as_str()
        })),
        AdmissionError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        other
        @ (AdmissionError::Sqlite(_) | AdmissionError::Store(_) | AdmissionError::Json(_)) => {
            ManagerError::new(ErrorCode::InternalError, other.to_string(), stage)
                .with_details(serde_json::json!({"chainId": chain_id}))
        }
    }
}

fn audit_failure(error: ManagerAuditError, chain_id: u64) -> ManagerError {
    match error {
        ManagerAuditError::InvalidRequest(message) => invalid_request(message, "tokens.audit")
            .with_details(serde_json::json!({"chainId": chain_id})),
        ManagerAuditError::DataIntegrity(message) => {
            ManagerError::new(ErrorCode::TokenLibraryConflict, message, "tokens.audit")
                .with_details(serde_json::json!({"chainId": chain_id}))
        }
        ManagerAuditError::Sqlite(error) => upstream_unavailable(error.to_string(), "tokens.audit")
            .with_details(serde_json::json!({"chainId": chain_id})),
    }
}

fn invalid_request(message: impl Into<String>, stage: &str) -> ManagerError {
    ManagerError::new(ErrorCode::InvalidRequest, message, stage)
}

fn upstream_unavailable(message: impl Into<String>, stage: &str) -> ManagerError {
    ManagerError::new(ErrorCode::UpstreamUnavailable, message, stage)
}

fn network_for_chain_id(chain_id: u64) -> String {
    match chain_id {
        4663 => "robinhood".to_owned(),
        46630 => "robinhood-testnet".to_owned(),
        other => format!("chain-{other}"),
    }
}

fn scope_failure(error: ScopeError, chain_id: u64, stage: &str) -> ManagerError {
    match error {
        ScopeError::UnsupportedScope(_) => {
            ManagerError::new(ErrorCode::ScopeNotImplemented, error.to_string(), stage)
                .with_details(serde_json::json!({
                    "chainId": chain_id,
                    "reason": error.to_string()
                }))
        }
        ScopeError::NoAdmittedAnchorTokens | ScopeError::MissingLibraryState(_) => {
            ManagerError::new(ErrorCode::TokenNotAdmitted, error.to_string(), stage).with_details(
                serde_json::json!({
                    "chainId": chain_id,
                    "reason": error.to_string()
                }),
            )
        }
        ScopeError::InvalidRawPool(_) => {
            ManagerError::new(ErrorCode::PoolInvalid, error.to_string(), stage).with_details(
                serde_json::json!({
                    "chainId": chain_id,
                    "reason": error.to_string()
                }),
            )
        }
        ScopeError::Sqlite(_) => {
            ManagerError::new(ErrorCode::UpstreamUnavailable, error.to_string(), stage)
                .with_details(serde_json::json!({
                    "chainId": chain_id,
                    "reason": error.to_string()
                }))
        }
        ScopeError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        ScopeError::Json(_) => {
            ManagerError::new(ErrorCode::InternalError, error.to_string(), stage).with_details(
                serde_json::json!({
                    "chainId": chain_id,
                    "reason": error.to_string()
                }),
            )
        }
    }
}

fn candidate_failure(error: CandidateEdgeError, chain_id: u64, stage: &str) -> ManagerError {
    let message = error.to_string();
    match error {
        CandidateEdgeError::InvalidRequest(message) => invalid_request(message, stage)
            .with_details(serde_json::json!({
                "chainId": chain_id
            })),
        CandidateEdgeError::ExpectedUpstreamMismatch {
            field,
            expected,
            actual,
        } => ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage).with_details(
            serde_json::json!({
                "chainId": chain_id,
                "field": field,
                "expected": expected,
                "actual": actual
            }),
        ),
        CandidateEdgeError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        CandidateEdgeError::MissingPoolScope
        | CandidateEdgeError::MissingCoreLibraryState
        | CandidateEdgeError::NoAdmittedCoreTokens => {
            ManagerError::new(ErrorCode::TokenNotAdmitted, message.clone(), stage).with_details(
                serde_json::json!({
                    "chainId": chain_id,
                    "reason": message
                }),
            )
        }
        CandidateEdgeError::InvalidStoredPool(_) => {
            ManagerError::new(ErrorCode::PoolInvalid, message.clone(), stage).with_details(
                serde_json::json!({
                    "chainId": chain_id,
                    "reason": message
                }),
            )
        }
        CandidateEdgeError::Sqlite(_) | CandidateEdgeError::ComparisonRead { .. } => {
            ManagerError::new(ErrorCode::UpstreamUnavailable, message.clone(), stage).with_details(
                serde_json::json!({
                    "chainId": chain_id,
                    "reason": message
                }),
            )
        }
        CandidateEdgeError::Json(_) => {
            ManagerError::new(ErrorCode::InternalError, message.clone(), stage).with_details(
                serde_json::json!({
                    "chainId": chain_id,
                    "reason": message
                }),
            )
        }
    }
}

fn candidate_exchange_failure(
    error: CandidateExchangeError,
    chain_id: u64,
    stage: &str,
) -> ManagerError {
    let message = error.to_string();
    match error {
        CandidateExchangeError::InvalidRequest(message) => {
            invalid_request(message, stage).with_details(serde_json::json!({"chainId": chain_id}))
        }
        CandidateExchangeError::ExpectedUpstreamMismatch {
            field,
            expected,
            actual,
        } => ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage).with_details(
            serde_json::json!({
                "chainId": chain_id,
                "field": field,
                "expected": expected,
                "actual": actual
            }),
        ),
        CandidateExchangeError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        CandidateExchangeError::MissingCandidateEdgeState
        | CandidateExchangeError::MissingPoolScope => {
            ManagerError::new(ErrorCode::TokenNotAdmitted, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        CandidateExchangeError::InvalidStoredPool(_) => {
            ManagerError::new(ErrorCode::PoolInvalid, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        CandidateExchangeError::Sqlite(_)
        | CandidateExchangeError::CandidateEdgeRead(_)
        | CandidateExchangeError::PoolScopeRead(_)
        | CandidateExchangeError::ComparisonRead { .. } => {
            ManagerError::new(ErrorCode::UpstreamUnavailable, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        CandidateExchangeError::Json(_) => {
            ManagerError::new(ErrorCode::InternalError, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
    }
}

fn closed_loop_failure(error: ClosedLoopError, chain_id: u64, stage: &str) -> ManagerError {
    let message = error.to_string();
    match error {
        ClosedLoopError::InvalidRequest(message) => {
            invalid_request(message, stage).with_details(serde_json::json!({"chainId": chain_id}))
        }
        ClosedLoopError::ExpectedUpstreamMismatch {
            field,
            expected,
            actual,
        } => ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage).with_details(
            serde_json::json!({
                "chainId": chain_id,
                "field": field,
                "expected": expected,
                "actual": actual
            }),
        ),
        ClosedLoopError::UpstreamInconsistent {
            field,
            upstream: _,
            value: actual,
        } => ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage).with_details(
            serde_json::json!({
                "chainId": chain_id,
                "field": field,
                "expected": "consistent-observation-required",
                "actual": actual
            }),
        ),
        ClosedLoopError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        ClosedLoopError::MissingCandidateEdgeState | ClosedLoopError::MissingObservationState => {
            ManagerError::new(ErrorCode::TokenNotAdmitted, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        ClosedLoopError::InvalidStoredTopology(_) => {
            ManagerError::new(ErrorCode::PoolInvalid, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        ClosedLoopError::Sqlite(_) | ClosedLoopError::ComparisonRead { .. } => {
            ManagerError::new(ErrorCode::UpstreamUnavailable, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        ClosedLoopError::Json(_) => {
            ManagerError::new(ErrorCode::InternalError, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
    }
}

fn review_failure(error: ReviewError, chain_id: u64, stage: &str) -> ManagerError {
    let message = error.to_string();
    match error {
        ReviewError::InvalidRequest(_)
        | ReviewError::UnknownClosedLoop(_)
        | ReviewError::DuplicateEvidence(_)
        | ReviewError::DuplicateDiagnostic { .. } => {
            invalid_request(message, stage).with_details(serde_json::json!({"chainId": chain_id}))
        }
        ReviewError::ExpectedUpstreamMismatch {
            field,
            expected,
            actual,
        } => ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage).with_details(
            serde_json::json!({
                "chainId": chain_id,
                "field": field,
                "expected": expected,
                "actual": actual
            }),
        ),
        ReviewError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        ReviewError::MissingClosedLoopState | ReviewError::ClosedLoopSnapshotTruncated { .. } => {
            ManagerError::new(ErrorCode::TokenNotAdmitted, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        ReviewError::ClosedLoopRead(_) | ReviewError::Sqlite(_) => {
            ManagerError::new(ErrorCode::UpstreamUnavailable, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        ReviewError::Json(_) => ManagerError::new(ErrorCode::InternalError, message, stage)
            .with_details(serde_json::json!({"chainId": chain_id})),
    }
}

fn broadcast_failure(error: BroadcastError, chain_id: u64, stage: &str) -> ManagerError {
    let message = error.to_string();
    match error {
        BroadcastError::InvalidRequest(_)
        | BroadcastError::DuplicateAttemptInput(_)
        | BroadcastError::DuplicateAttempt(_)
        | BroadcastError::DuplicateDiagnostic { .. } => {
            invalid_request(message, stage).with_details(serde_json::json!({"chainId": chain_id}))
        }
        BroadcastError::ExpectedUpstreamMismatch {
            field,
            expected,
            actual,
        } => ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage).with_details(
            serde_json::json!({
                "chainId": chain_id,
                "field": field,
                "expected": expected,
                "actual": actual
            }),
        ),
        BroadcastError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        BroadcastError::UnknownRequest(_)
        | BroadcastError::BroadcastRequestNotEligible { .. }
        | BroadcastError::AttemptNotFound(_)
        | BroadcastError::AttemptAlreadyTerminal { .. } => ManagerError::new(
            ErrorCode::BroadcastRequestNotEligible,
            message.clone(),
            stage,
        )
        .with_details(serde_json::json!({"chainId": chain_id, "reason": message})),
        BroadcastError::MissingReviewState | BroadcastError::ReviewSnapshotTruncated { .. } => {
            ManagerError::new(ErrorCode::TokenNotAdmitted, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        BroadcastError::ReviewRead(_) | BroadcastError::Sqlite(_) => {
            ManagerError::new(ErrorCode::UpstreamUnavailable, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        BroadcastError::Json(_) => ManagerError::new(ErrorCode::InternalError, message, stage)
            .with_details(serde_json::json!({"chainId": chain_id})),
    }
}

fn composite_failure(error: CompositeError, chain_id: u64, stage: &str) -> ManagerError {
    let message = error.to_string();
    match error {
        CompositeError::InvalidRequest(_) => {
            invalid_request(message, stage).with_details(serde_json::json!({"chainId": chain_id}))
        }
        CompositeError::RunNotFound(_) => {
            ManagerError::new(ErrorCode::TokenNotFound, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        CompositeError::StepNotFound { .. } => {
            ManagerError::new(ErrorCode::ScopeNotFound, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        CompositeError::UpstreamInconsistent { issues } => {
            ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage)
                .with_details(serde_json::json!({"chainId": chain_id, "issues": issues}))
        }
        CompositeError::MissingDependency(dependency) => {
            ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage)
                .with_details(serde_json::json!({"chainId": chain_id, "dependency": dependency}))
        }
        CompositeError::ExpectedDependencyMismatch {
            field,
            expected,
            actual,
        } => ManagerError::new(ErrorCode::UpstreamVersionChanged, message, stage).with_details(
            serde_json::json!({
                "chainId": chain_id,
                "field": field,
                "expected": expected,
                "actual": actual
            }),
        ),
        CompositeError::StepAlreadyFinal { .. }
        | CompositeError::StepNotRunning { .. }
        | CompositeError::StepOutOfOrder { .. }
        | CompositeError::OutputNotVerified { .. }
        | CompositeError::PendingBroadcastAttempts(_) => {
            ManagerError::new(ErrorCode::ReviewNotExecutable, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        CompositeError::IdempotencyKeyReused {
            idempotency_key,
            existing_request_id,
            submitted_request_id,
        } => ManagerError::new(
            ErrorCode::IdempotencyKeyReused,
            format!(
                "Idempotency-Key {idempotency_key} was already used for different request content"
            ),
            stage,
        )
        .with_details(serde_json::json!({
            "chainId": chain_id,
            "idempotencyKey": idempotency_key,
            "existingRequestId": existing_request_id,
            "submittedRequestId": submitted_request_id
        })),
        CompositeError::UpstreamRead { manager, message } => ManagerError::new(
            ErrorCode::UpstreamUnavailable,
            format!("{manager} status read failed: {message}"),
            stage,
        )
        .with_details(serde_json::json!({"chainId": chain_id, "manager": manager})),
        CompositeError::Sqlite(_) => {
            ManagerError::new(ErrorCode::UpstreamUnavailable, message.clone(), stage)
                .with_details(serde_json::json!({"chainId": chain_id, "reason": message}))
        }
        CompositeError::Json(_) => ManagerError::new(ErrorCode::InternalError, message, stage)
            .with_details(serde_json::json!({"chainId": chain_id})),
    }
}

fn composite_authority_denied(
    authority: TokenAuthorityMode,
    chain_id: u64,
    stage: &str,
) -> ManagerError {
    ManagerError::new(
        ErrorCode::TokenAuthorityWriteDenied,
        format!("Composite Manager apply is disabled while authority mode is {authority}"),
        stage,
    )
    .with_details(serde_json::json!({
        "chainId": chain_id,
        "authorityMode": authority.as_str(),
        "dryRun": false
    }))
}

fn default_pipeline_steps() -> Vec<PipelineStepName> {
    vec![
        PipelineStepName::CandidateEdge,
        PipelineStepName::CandidateExchange,
        PipelineStepName::ClosedLoop,
        PipelineStepName::Review,
        PipelineStepName::Broadcast,
    ]
}

fn success<T>(chain_id: u64, data: T) -> Response
where
    T: Serialize,
{
    success_with_request_id(chain_id, data, None)
}

fn success_with_request_id<T>(chain_id: u64, data: T, request_id: Option<&str>) -> Response
where
    T: Serialize,
{
    let mut response =
        ManagerResponse::success(Some(chain_id), chrono::Utc::now().to_rfc3339(), data);
    if let Some(request_id) = request_id {
        response = response.with_request_id(request_id);
    }
    (StatusCode::OK, Json(response)).into_response()
}

fn failure(error: ManagerError) -> Response {
    failure_with_request_id(error, None)
}

fn failure_with_request_id(error: ManagerError, request_id: Option<&str>) -> Response {
    let status =
        StatusCode::from_u16(error.code.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let chain_id = error
        .details
        .get("chainId")
        .and_then(serde_json::Value::as_u64);
    let mut response = ManagerResponse::<serde_json::Value>::failure(
        chain_id,
        chrono::Utc::now().to_rfc3339(),
        error,
    );
    if let Some(request_id) = request_id {
        response = response.with_request_id(request_id);
    }
    (status, Json(response)).into_response()
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse {
    ok: bool,
    status: &'static str,
    version: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CapabilitiesResponse {
    ok: bool,
    version: &'static str,
    data: CapabilitiesData,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CapabilitiesData {
    milestone: &'static str,
    migration_id: &'static str,
    migration_checksum: String,
    schema_checksum: String,
    token_authority_mode: &'static str,
    candidate_authority_mode: &'static str,
    exchange_authority_mode: &'static str,
    closed_loop_authority_mode: &'static str,
    review_authority_mode: &'static str,
    broadcast_authority_mode: &'static str,
    composite_authority_mode: &'static str,
    business_writes_enabled: bool,
    token_reads_enabled: bool,
    pool_reads_enabled: bool,
    pool_writes_enabled: bool,
    candidate_reads_enabled: bool,
    candidate_writes_enabled: bool,
    exchange_reads_enabled: bool,
    exchange_writes_enabled: bool,
    closed_loop_reads_enabled: bool,
    closed_loop_writes_enabled: bool,
    review_reads_enabled: bool,
    review_writes_enabled: bool,
    broadcast_reads_enabled: bool,
    broadcast_writes_enabled: bool,
    overview_reads_enabled: bool,
    pipeline_reads_enabled: bool,
    pipeline_writes_enabled: bool,
    dry_run_enabled: bool,
    capabilities: Vec<&'static str>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ListTokensQuery {
    chain_id: u64,
    library: String,
    status: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ResolveTokenRequest {
    chain_id: u64,
    symbol: String,
    required_status: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct TokenAuditQuery {
    chain_id: u64,
    recent_event_limit: Option<u32>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PoolScopeQuery {
    chain_id: u64,
    scope_key: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PoolScopeWriteRequest {
    chain_id: u64,
    scope_key: Option<String>,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CandidateEdgeQuery {
    chain_id: u64,
    anchor_token_key_id: Option<String>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CandidateEdgeComparisonQuery {
    chain_id: u64,
    legacy_anchor_scope: Option<String>,
    sample_limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CandidateEdgeHttpRequest {
    chain_id: u64,
    dry_run: Option<bool>,
    expected_upstream: Option<ExpectedUpstream>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CandidateExchangeQuery {
    chain_id: u64,
    anchor_token_key_id: Option<String>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CandidateExchangeComparisonQuery {
    chain_id: u64,
    legacy_anchor_scope: Option<String>,
    sample_limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CandidateExchangeHttpRequest {
    chain_id: u64,
    dry_run: Option<bool>,
    expected_upstream: Option<ExchangeExpectedUpstream>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ClosedLoopQuery {
    chain_id: u64,
    anchor_token_key_id: Option<String>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ClosedLoopComparisonQuery {
    chain_id: u64,
    legacy_market_id: Option<String>,
    sample_limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ClosedLoopHttpRequest {
    chain_id: u64,
    dry_run: Option<bool>,
    expected_upstream: Option<ClosedLoopExpectedUpstream>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ReviewQuery {
    chain_id: u64,
    loop_key: Option<String>,
    category: Option<String>,
    status: Option<String>,
    outcome: Option<String>,
    offset: Option<usize>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReviewHttpRequest {
    chain_id: u64,
    expected_upstream: Option<ReviewExpectedUpstream>,
    evidence: Vec<ReviewEvidenceInput>,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct BroadcastQuery {
    chain_id: u64,
    attempt_id: Option<String>,
    status: Option<String>,
    offset: Option<usize>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BroadcastSubmitHttpRequest {
    chain_id: u64,
    expected_upstream: Option<BroadcastExpectedUpstream>,
    attempts: Vec<AttemptCreateInput>,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BroadcastFinalizeHttpRequest {
    chain_id: u64,
    expected_upstream: Option<BroadcastExpectedUpstream>,
    finalizations: Vec<AttemptFinalizeInput>,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BroadcastClearHttpRequest {
    chain_id: u64,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PipelineQuery {
    chain_id: u64,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct OverviewQuery {
    chain_id: u64,
    limit: Option<usize>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PipelineRunQuery {
    chain_id: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PipelineRefreshHttpRequest {
    chain_id: u64,
    requested_steps: Option<Vec<PipelineStepName>>,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PipelineStepBeginHttpRequest {
    chain_id: u64,
    run_id: String,
    step_id: String,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PipelineStepCompleteHttpRequest {
    chain_id: u64,
    run_id: String,
    step_id: String,
    status: StepCompletionStatus,
    result: Option<serde_json::Value>,
    error: Option<String>,
    dry_run: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TokenAdmissionHttpRequest {
    chain_id: u64,
    library_key: String,
    token_key_id: String,
    reason: String,
    #[serde(default)]
    evidence: serde_json::Value,
    dry_run: Option<bool>,
}

#[cfg(test)]
mod tests {
    use super::{app, app_with_state, AppState, M8_MIGRATION_ID};
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use http_body_util::BodyExt;
    use manager_contracts::{TokenAuthorityMode, TokenKeyId, V4PoolKey};
    use rusqlite::Connection;
    use std::sync::Arc;
    use token_manager::{write_library, AdmissionStatus, LibraryKey, TokenWrite};
    use tower::ServiceExt;

    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const USDE: &str = "0x2222222222222222222222222222222222222222";
    const UNIQUE: &str = "0x3333333333333333333333333333333333333333";

    fn fixture(address: &str, symbol: &str) -> TokenWrite {
        TokenWrite {
            network: "robinhood".to_owned(),
            chain_id: 4663,
            token_key_id: TokenKeyId::new("robinhood", 4663, address).unwrap(),
            v4_currency_key: address.to_owned(),
            token_address: address.to_owned(),
            token_symbol: symbol.to_owned(),
            token_name: Some(symbol.to_owned()),
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: false,
            admission_status: AdmissionStatus::Admitted,
            admission_source: "fixture".to_owned(),
            metadata_source: "fixture".to_owned(),
            metadata_checksum: format!("sha256:{symbol:0>64}"),
            source_json: Some("{}".to_owned()),
            first_seen_at: None,
            blocked_reason: None,
        }
    }

    fn temporary_state() -> (tempfile::TempDir, Arc<AppState>, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = Connection::open(&path).unwrap();
        write_library(
            &mut connection,
            LibraryKey::Core,
            "robinhood",
            4663,
            vec![fixture(USDG, "USDG")],
            Some("fixture:v1"),
            "test",
            "fixture",
        )
        .unwrap();
        write_library(
            &mut connection,
            LibraryKey::Stocks,
            "robinhood",
            4663,
            vec![fixture(USDE, "USDG")],
            Some("fixture:v1"),
            "test",
            "fixture",
        )
        .unwrap();
        write_library(
            &mut connection,
            LibraryKey::Extension,
            "robinhood",
            4663,
            vec![fixture(UNIQUE, "UNIQUE")],
            Some("fixture:v1"),
            "test",
            "fixture",
        )
        .unwrap();
        drop(connection);
        let state = Arc::new(
            AppState::with_token_database(&path).with_token_authority(TokenAuthorityMode::Manager),
        );
        (directory, state, path)
    }

    fn pipeline_state() -> (tempfile::TempDir, Arc<AppState>, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let mut connection = Connection::open(&path).unwrap();
        for (library, address, symbol) in [
            (
                LibraryKey::Core,
                "0x1111111111111111111111111111111111111111",
                "USDG",
            ),
            (
                LibraryKey::Stocks,
                "0x2222222222222222222222222222222222222222",
                "AMZN",
            ),
            (
                LibraryKey::Extension,
                "0x3333333333333333333333333333333333333333",
                "EXT",
            ),
            (
                LibraryKey::Test,
                "0x4444444444444444444444444444444444444444",
                "TZ",
            ),
        ] {
            write_library(
                &mut connection,
                library,
                "robinhood",
                4663,
                vec![fixture(address, symbol)],
                Some("fixture:v1"),
                "test",
                "fixture",
            )
            .unwrap();
        }
        let mut versions = std::collections::BTreeMap::new();
        let mut checksums = std::collections::BTreeMap::new();
        {
            let mut statement = connection
                .prepare(
                    "SELECT library_key, library_version, metadata_checksum
                     FROM token_library_state
                     WHERE network = 'robinhood' AND chain_id = 4663",
                )
                .unwrap();
            let rows = statement
                .query_map([], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, String>(2)?,
                    ))
                })
                .unwrap();
            for row in rows {
                let (library, version, checksum) = row.unwrap();
                versions.insert(library.clone(), version);
                checksums.insert(library, checksum);
            }
        }
        connection
            .execute(
                "INSERT INTO pool_scope_config (
                    network, chain_id, scope_key, pool_table,
                    library_keys_json, library_versions_json,
                    library_checksums_json, filter_mode,
                    excluded_token_key_ids_json, enabled,
                    materialized_checksum, last_rebuilt_at, created_at, updated_at
                 ) VALUES (
                    'robinhood', 4663, 'core4', 'v4_exchange_pools_core4',
                    '[\"core\",\"stocks\",\"extension\",\"test\"]', ?1, ?2,
                    'edge_any', '[]', 1, 'sha256:pipeline-scope',
                    'fixture', 'fixture', 'fixture'
                 )",
                rusqlite::params![
                    serde_json::to_string(&versions).unwrap(),
                    serde_json::to_string(&checksums).unwrap()
                ],
            )
            .unwrap();
        drop(connection);
        let state = Arc::new(
            AppState::with_token_database(&path)
                .with_token_authority(TokenAuthorityMode::Manager)
                .with_composite_authority(TokenAuthorityMode::Manager),
        );
        (directory, state, path)
    }

    fn pipeline_refresh_body(dry_run: Option<bool>, steps: &[&str]) -> String {
        serde_json::json!({
            "chainId": 4663,
            "requestedSteps": steps,
            "dryRun": dry_run
        })
        .to_string()
    }

    fn pipeline_step_body(
        endpoint: &str,
        run_id: &str,
        step_id: &str,
        dry_run: Option<bool>,
    ) -> String {
        let mut body = serde_json::json!({
            "chainId": 4663,
            "runId": run_id,
            "stepId": step_id,
            "dryRun": dry_run
        });
        if endpoint.ends_with("/complete") {
            body["status"] = serde_json::json!("failed");
            body["error"] = serde_json::json!("fixture failure");
            body["result"] = serde_json::json!({"source": "fixture"});
        }
        body.to_string()
    }

    async fn response_json(response: axum::response::Response) -> serde_json::Value {
        let body = response.into_body().collect().await.unwrap().to_bytes();
        serde_json::from_slice(&body).unwrap()
    }

    fn admission_body(dry_run: Option<bool>, reason: &str) -> String {
        serde_json::json!({
            "chainId": 4663,
            "libraryKey": "core",
            "tokenKeyId": format!("robinhood:4663:{USDG}"),
            "reason": reason,
            "evidence": {"ticket": "OP-1"},
            "dryRun": dry_run
        })
        .to_string()
    }

    fn seed_scope_pool(path: &std::path::Path) {
        let connection = Connection::open(path).unwrap();
        connection
            .execute_batch(
                r#"
                CREATE TABLE v4_exchange_pools (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    pool_manager TEXT,
                    pool_key_json TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    tick_spacing INTEGER,
                    hooks TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    has_liquidity INTEGER,
                    liquidity_index REAL,
                    sqrt_price_x96 TEXT,
                    tick INTEGER,
                    liquidity TEXT,
                    price REAL,
                    depth_status TEXT,
                    liquidity_read_at TEXT,
                    liquidity_block_number INTEGER,
                    evidence_source TEXT,
                    PRIMARY KEY (network, chain_id, protocol, pool_id)
                );
                "#,
            )
            .unwrap();
        let key = V4PoolKey {
            currency0: USDG.to_owned(),
            currency1: USDE.to_owned(),
            fee: 500,
            tick_spacing: 10,
            hooks: "0x0000000000000000000000000000000000000000".to_owned(),
        };
        let pool_id = key.pool_id().unwrap();
        connection
            .execute(
                "INSERT INTO v4_exchange_pools (
                    network, chain_id, protocol, pool_id, pool_manager,
                    pool_key_json, token0, token1, fee, dynamic_fee,
                    special_fee, tick_spacing, hooks
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', ?1,
                           '0x4444444444444444444444444444444444444444', ?2,
                           ?3, ?4, 500, 0, 0, 10,
                           '0x0000000000000000000000000000000000000000')",
                rusqlite::params![pool_id, serde_json::to_string(&key).unwrap(), USDG, USDE],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots (
                    network, chain_id, protocol, pool_id, has_liquidity,
                    liquidity_index
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', ?1, 1, 0.01)",
                rusqlite::params![pool_id],
            )
            .unwrap();
    }

    fn scope_write_request(dry_run: Option<bool>) -> String {
        serde_json::json!({
            "chainId": 4663,
            "scopeKey": "core4",
            "dryRun": dry_run
        })
        .to_string()
    }

    fn candidate_write_request(dry_run: Option<bool>) -> String {
        serde_json::json!({
            "chainId": 4663,
            "dryRun": dry_run
        })
        .to_string()
    }

    fn admission_request(
        uri: &str,
        body: String,
        request_id: Option<&str>,
        idempotency_key: Option<&str>,
    ) -> Request<Body> {
        let mut builder = Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json");
        if let Some(request_id) = request_id {
            builder = builder.header("x-request-id", request_id);
        }
        if let Some(idempotency_key) = idempotency_key {
            builder = builder.header("idempotency-key", idempotency_key);
        }
        builder.body(Body::from(body)).unwrap()
    }

    #[tokio::test]
    async fn health_is_available_without_business_state() {
        let response = app()
            .oneshot(
                Request::builder()
                    .uri("/healthz")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value = response_json(response).await;
        assert_eq!(value["status"], "ok");
        assert_eq!(value["version"], "manager_dto_v1");
    }

    #[tokio::test]
    async fn capabilities_describe_m8_pipeline_routes() {
        let response = app()
            .oneshot(
                Request::builder()
                    .uri("/v1/capabilities")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value = response_json(response).await;
        assert_eq!(value["data"]["migrationId"], M8_MIGRATION_ID);
        assert_eq!(value["data"]["milestone"], "M8");
        assert_eq!(value["data"]["tokenAuthorityMode"], "legacy");
        assert_eq!(value["data"]["candidateAuthorityMode"], "legacy");
        assert_eq!(value["data"]["exchangeAuthorityMode"], "legacy");
        assert_eq!(value["data"]["closedLoopAuthorityMode"], "legacy");
        assert_eq!(value["data"]["reviewAuthorityMode"], "legacy");
        assert_eq!(value["data"]["broadcastAuthorityMode"], "legacy");
        assert_eq!(value["data"]["compositeAuthorityMode"], "legacy");
        assert_eq!(value["data"]["businessWritesEnabled"], false);
        assert_eq!(value["data"]["tokenReadsEnabled"], false);
        assert_eq!(value["data"]["poolReadsEnabled"], false);
        assert_eq!(value["data"]["exchangeReadsEnabled"], false);
        assert_eq!(value["data"]["poolWritesEnabled"], false);
        assert_eq!(value["data"]["candidateReadsEnabled"], false);
        assert_eq!(value["data"]["candidateWritesEnabled"], false);
        assert_eq!(value["data"]["closedLoopReadsEnabled"], false);
        assert_eq!(value["data"]["closedLoopWritesEnabled"], false);
        assert_eq!(value["data"]["overviewReadsEnabled"], false);
        assert_eq!(value["data"]["reviewReadsEnabled"], false);
        assert_eq!(value["data"]["reviewWritesEnabled"], false);
        assert_eq!(value["data"]["broadcastReadsEnabled"], false);
        assert_eq!(value["data"]["broadcastWritesEnabled"], false);
        assert_eq!(value["data"]["pipelineReadsEnabled"], false);
        assert_eq!(value["data"]["pipelineWritesEnabled"], false);
        assert_eq!(value["data"]["dryRunEnabled"], false);
        assert_eq!(value["data"]["capabilities"][2], "tokens.query");
        assert_eq!(value["data"]["capabilities"][3], "tokens.resolve");
        assert_eq!(value["data"]["capabilities"][4], "tokens.admit");
        assert_eq!(value["data"]["capabilities"][5], "tokens.block");
        assert_eq!(value["data"]["capabilities"][6], "tokens.audit");
        assert_eq!(value["data"]["capabilities"][7], "pools.scope.read");
        assert_eq!(value["data"]["capabilities"][8], "pools.scope.rebuild");
        assert_eq!(value["data"]["capabilities"][9], "pools.scope.repair");
        assert_eq!(value["data"]["capabilities"][10], "candidate_edge.read");
        assert_eq!(value["data"]["capabilities"][11], "candidate_edge.compare");
        assert_eq!(value["data"]["capabilities"][12], "candidate_edge.rebuild");
        assert_eq!(value["data"]["capabilities"][13], "candidate_exchange.read");
        assert_eq!(
            value["data"]["capabilities"][14],
            "candidate_exchange.compare"
        );
        assert_eq!(
            value["data"]["capabilities"][15],
            "candidate_exchange.rebuild"
        );
        assert_eq!(value["data"]["capabilities"][16], "closed_loop.read");
        assert_eq!(value["data"]["capabilities"][17], "closed_loop.compare");
        assert_eq!(value["data"]["capabilities"][18], "closed_loop.rebuild");
        assert_eq!(value["data"]["capabilities"][19], "review.read");
        assert_eq!(value["data"]["capabilities"][20], "review.run");
        assert_eq!(value["data"]["capabilities"][21], "broadcast.read");
        assert_eq!(value["data"]["capabilities"][22], "broadcast.submit");
        assert_eq!(value["data"]["capabilities"][23], "broadcast.finalize");
        assert_eq!(value["data"]["capabilities"][24], "broadcast.clear");
        assert_eq!(value["data"]["capabilities"][25], "pipeline.read");
        assert_eq!(value["data"]["capabilities"][26], "pipeline.refresh");
        assert_eq!(value["data"]["capabilities"][27], "pipeline.step.begin");
        assert_eq!(value["data"]["capabilities"][28], "pipeline.step.complete");
        assert_eq!(value["data"]["capabilities"][29], "overview.read");
    }

    #[tokio::test]
    async fn pool_scope_routes_enforce_headers_dry_run_and_idempotent_rebuild() {
        let (_directory, state, path) = temporary_state();
        seed_scope_pool(&path);
        let app = app_with_state(state);

        let missing_headers = app
            .clone()
            .oneshot(admission_request(
                "/v1/pools/scopes/rebuild",
                scope_write_request(Some(true)),
                None,
                None,
            ))
            .await
            .unwrap();
        assert_eq!(missing_headers.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(missing_headers).await["error"]["code"],
            "INVALID_REQUEST"
        );

        let dry_run = app
            .clone()
            .oneshot(admission_request(
                "/v1/pools/scopes/rebuild",
                scope_write_request(Some(true)),
                Some("pool-dry-run"),
                Some("pool-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(dry_run.status(), StatusCode::OK);
        let dry_value = response_json(dry_run).await;
        assert_eq!(dry_value["requestId"], "pool-dry-run");
        assert_eq!(dry_value["data"]["dryRun"], true);
        assert_eq!(dry_value["data"]["idempotentReplay"], false);
        assert_eq!(dry_value["data"]["activePoolCount"], 1);
        assert_eq!(dry_value["data"]["changedRows"], 1);
        let checksum = dry_value["data"]["materializedChecksum"]
            .as_str()
            .unwrap()
            .to_owned();
        let connection = Connection::open(&path).unwrap();
        let rows_after_dry_run: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_exchange_pools_core4", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(rows_after_dry_run, 0);
        drop(connection);

        let apply = app
            .clone()
            .oneshot(admission_request(
                "/v1/pools/scopes/rebuild",
                scope_write_request(Some(false)),
                Some("pool-apply"),
                Some("pool-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(apply.status(), StatusCode::OK);
        let apply_value = response_json(apply).await;
        assert_eq!(apply_value["data"]["dryRun"], false);
        assert_eq!(apply_value["data"]["idempotentReplay"], false);
        assert_eq!(apply_value["data"]["changedRows"], 1);
        assert_eq!(apply_value["data"]["materializedChecksum"], checksum);

        let replay = app
            .clone()
            .oneshot(admission_request(
                "/v1/pools/scopes/rebuild",
                scope_write_request(Some(false)),
                Some("pool-replay"),
                Some("pool-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(replay.status(), StatusCode::OK);
        let replay_value = response_json(replay).await;
        assert_eq!(replay_value["data"]["idempotentReplay"], true);
        assert_eq!(replay_value["data"]["changedRows"], 1);
        assert_eq!(
            replay_value["data"]["materializedChecksum"],
            checksum.as_str()
        );
        let connection = Connection::open(&path).unwrap();
        let write_request_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(write_request_count, 1);
        let persisted_json: String = connection
            .query_row(
                "SELECT response_json FROM manager_write_requests
                 WHERE idempotency_key = 'pool-idem'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let mut persisted: serde_json::Value = serde_json::from_str(&persisted_json).unwrap();
        persisted["requestDigest"] = serde_json::json!("sha256:tampered");
        connection
            .execute(
                "UPDATE manager_write_requests
                 SET request_digest = 'sha256:tampered', response_json = ?1
                 WHERE idempotency_key = 'pool-idem'",
                rusqlite::params![persisted.to_string()],
            )
            .unwrap();
        drop(connection);

        let conflict = app
            .clone()
            .oneshot(admission_request(
                "/v1/pools/scopes/rebuild",
                scope_write_request(Some(false)),
                Some("pool-conflict"),
                Some("pool-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(conflict.status(), StatusCode::CONFLICT);
        let conflict_value = response_json(conflict).await;
        assert_eq!(conflict_value["error"]["code"], "IDEMPOTENCY_KEY_REUSED");
        assert_eq!(
            conflict_value["error"]["details"]["existingRequestId"],
            "pool-apply"
        );
        assert_eq!(
            conflict_value["error"]["details"]["submittedRequestId"],
            "pool-conflict"
        );

        let status = app
            .oneshot(
                Request::builder()
                    .uri("/v1/pools/scopes?chainId=4663&scopeKey=core4")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(status.status(), StatusCode::OK);
        let status_value = response_json(status).await;
        assert_eq!(status_value["data"]["enabled"], true);
        assert_eq!(status_value["data"]["poolCount"], 1);
        assert_eq!(status_value["data"]["activePoolCount"], 1);
        assert_eq!(status_value["data"]["checksumMatchesConfig"], true);
    }

    #[tokio::test]
    async fn pool_scope_repair_requires_explicit_dry_run() {
        let (_directory, state, path) = temporary_state();
        seed_scope_pool(&path);
        let response = app_with_state(state)
            .oneshot(admission_request(
                "/v1/pools/scopes/repair",
                scope_write_request(None),
                Some("pool-repair-missing-dry-run"),
                Some("pool-repair-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let value = response_json(response).await;
        assert_eq!(value["requestId"], "pool-repair-missing-dry-run");
        assert_eq!(value["error"]["code"], "INVALID_REQUEST");
        assert_eq!(value["error"]["details"]["chainId"], 4663);
    }

    #[tokio::test]
    async fn candidate_edge_routes_require_dry_run_and_isolated_authority() {
        let (_directory, _manager_state, path) = temporary_state();
        seed_scope_pool(&path);
        pool_manager::materialize_scope(&path, "robinhood", 4663, "core4", true).unwrap();

        let legacy_state = Arc::new(
            AppState::with_token_database(&path)
                .with_token_authority(TokenAuthorityMode::Manager)
                .with_candidate_authority(TokenAuthorityMode::Legacy),
        );
        let denied = app_with_state(legacy_state)
            .oneshot(admission_request(
                "/v1/candidate-edge-pools/rebuild",
                candidate_write_request(Some(false)),
                Some("candidate-authority-denied"),
                Some("candidate-authority-denied-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(denied.status(), StatusCode::FORBIDDEN);
        let denied_value = response_json(denied).await;
        assert_eq!(denied_value["error"]["details"]["authorityMode"], "legacy");

        let manager_state = Arc::new(
            AppState::with_token_database(&path)
                .with_candidate_authority(TokenAuthorityMode::Manager),
        );
        let app = app_with_state(manager_state);
        let missing_dry_run = app
            .clone()
            .oneshot(admission_request(
                "/v1/candidate-edge-pools/rebuild",
                candidate_write_request(None),
                Some("candidate-missing-dry-run"),
                Some("candidate-missing-dry-run-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(missing_dry_run.status(), StatusCode::BAD_REQUEST);

        let dry_run = app
            .clone()
            .oneshot(admission_request(
                "/v1/candidate-edge-pools/rebuild",
                candidate_write_request(Some(true)),
                Some("candidate-dry-run"),
                Some("candidate-dry-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(dry_run.status(), StatusCode::OK);
        let dry_value = response_json(dry_run).await;
        let checksum = dry_value["data"]["materializedChecksum"]
            .as_str()
            .unwrap()
            .to_owned();
        let connection = Connection::open(&path).unwrap();
        let dry_rows: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_dex_candidate_pools",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(dry_rows, 0);
        drop(connection);

        let apply = app
            .clone()
            .oneshot(admission_request(
                "/v1/candidate-edge-pools/rebuild",
                candidate_write_request(Some(false)),
                Some("candidate-apply"),
                Some("candidate-apply-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(apply.status(), StatusCode::OK);
        let apply_value = response_json(apply).await;
        assert_eq!(
            apply_value["data"]["materializedChecksum"],
            checksum.as_str()
        );

        let snapshot = app
            .oneshot(
                Request::builder()
                    .uri("/v1/candidate-edge-pools?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(snapshot.status(), StatusCode::OK);
        let snapshot_value = response_json(snapshot).await;
        assert_eq!(snapshot_value["data"]["status"]["enabled"], true);
        assert_eq!(snapshot_value["data"]["status"]["eligiblePoolCount"], 1);
    }

    #[tokio::test]
    async fn candidate_edge_comparison_route_is_read_only_and_validates_query() {
        let invalid = app()
            .oneshot(
                Request::builder()
                    .uri("/v1/candidate-edge-pools/compare?chainId=4663&sampleLimit=0")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(invalid).await["error"]["code"],
            "INVALID_REQUEST"
        );

        let unavailable = app()
            .oneshot(
                Request::builder()
                    .uri("/v1/candidate-edge-pools/compare?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(unavailable.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            response_json(unavailable).await["error"]["code"],
            "UPSTREAM_UNAVAILABLE"
        );
    }

    #[tokio::test]
    async fn candidate_exchange_routes_require_explicit_dry_run_and_isolated_authority() {
        let unavailable = app()
            .oneshot(
                Request::builder()
                    .uri("/v1/candidate-exchange-pools?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(unavailable.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            response_json(unavailable).await["error"]["code"],
            "UPSTREAM_UNAVAILABLE"
        );

        let (_directory, _manager_state, path) = temporary_state();
        let legacy_state = Arc::new(
            AppState::with_token_database(&path)
                .with_token_authority(TokenAuthorityMode::Manager)
                .with_candidate_authority(TokenAuthorityMode::Manager)
                .with_exchange_authority(TokenAuthorityMode::Legacy),
        );
        let denied = app_with_state(legacy_state)
            .oneshot(admission_request(
                "/v1/candidate-exchange-pools/rebuild",
                candidate_write_request(Some(false)),
                Some("exchange-authority-denied"),
                Some("exchange-authority-denied-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(denied.status(), StatusCode::FORBIDDEN);
        let denied_value = response_json(denied).await;
        assert_eq!(denied_value["error"]["details"]["authorityMode"], "legacy");

        let manager_state = Arc::new(
            AppState::with_token_database(&path)
                .with_exchange_authority(TokenAuthorityMode::Manager),
        );
        let app = app_with_state(manager_state);
        let missing_dry_run = app
            .clone()
            .oneshot(admission_request(
                "/v1/candidate-exchange-pools/rebuild",
                candidate_write_request(None),
                Some("exchange-missing-dry-run"),
                Some("exchange-missing-dry-run-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(missing_dry_run.status(), StatusCode::BAD_REQUEST);

        let comparison = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/candidate-exchange-pools/compare?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(comparison.status(), StatusCode::OK);
        assert_eq!(response_json(comparison).await["data"]["enabled"], false);

        let snapshot = app
            .oneshot(
                Request::builder()
                    .uri("/v1/candidate-exchange-pools?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(snapshot.status(), StatusCode::OK);
        let snapshot_value = response_json(snapshot).await;
        assert_eq!(snapshot_value["data"]["status"]["enabled"], false);
        assert_eq!(
            snapshot_value["data"]["yzPairs"].as_array().unwrap().len(),
            0
        );
    }

    #[tokio::test]
    async fn closed_loop_routes_require_explicit_dry_run_and_isolated_authority() {
        let unavailable = app()
            .oneshot(
                Request::builder()
                    .uri("/v1/closed-loops?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(unavailable.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            response_json(unavailable).await["error"]["code"],
            "UPSTREAM_UNAVAILABLE"
        );

        let (_directory, _manager_state, path) = temporary_state();
        let legacy_state = Arc::new(
            AppState::with_token_database(&path)
                .with_candidate_authority(TokenAuthorityMode::Manager)
                .with_exchange_authority(TokenAuthorityMode::Manager)
                .with_closed_loop_authority(TokenAuthorityMode::Legacy),
        );
        let denied = app_with_state(legacy_state)
            .oneshot(admission_request(
                "/v1/closed-loops/rebuild",
                candidate_write_request(Some(false)),
                Some("closed-loop-authority-denied"),
                Some("closed-loop-authority-denied-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(denied.status(), StatusCode::FORBIDDEN);
        let denied_value = response_json(denied).await;
        assert_eq!(denied_value["error"]["details"]["authorityMode"], "legacy");

        let manager_state = Arc::new(
            AppState::with_token_database(&path)
                .with_closed_loop_authority(TokenAuthorityMode::Manager),
        );
        let app = app_with_state(manager_state);
        let missing_dry_run = app
            .clone()
            .oneshot(admission_request(
                "/v1/closed-loops/rebuild",
                candidate_write_request(None),
                Some("closed-loop-missing-dry-run"),
                Some("closed-loop-missing-dry-run-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(missing_dry_run.status(), StatusCode::BAD_REQUEST);

        let comparison = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/closed-loops/compare?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(comparison.status(), StatusCode::OK);
        assert_eq!(response_json(comparison).await["data"]["enabled"], false);

        let snapshot = app
            .oneshot(
                Request::builder()
                    .uri("/v1/closed-loops?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(snapshot.status(), StatusCode::OK);
        let snapshot_value = response_json(snapshot).await;
        assert_eq!(snapshot_value["data"]["status"]["enabled"], false);
        assert_eq!(snapshot_value["data"]["loops"].as_array().unwrap().len(), 0);
    }

    #[tokio::test]
    async fn pool_scope_apply_fails_closed_outside_manager_authority() {
        let (_directory, _manager_state, path) = temporary_state();
        seed_scope_pool(&path);
        let legacy_state = Arc::new(
            AppState::with_token_database(&path).with_token_authority(TokenAuthorityMode::Legacy),
        );
        let response = app_with_state(legacy_state)
            .oneshot(admission_request(
                "/v1/pools/scopes/rebuild",
                scope_write_request(Some(false)),
                Some("scope-authority-denied"),
                Some("scope-authority-denied-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        let value = response_json(response).await;
        assert_eq!(value["error"]["code"], "TOKEN_AUTHORITY_WRITE_DENIED");
        assert_eq!(value["error"]["details"]["authorityMode"], "legacy");

        let connection = Connection::open(path).unwrap();
        let pool_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_exchange_pools_core4", [], |row| {
                row.get(0)
            })
            .unwrap();
        let request_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(pool_count, 0);
        assert_eq!(request_count, 0);
    }

    #[tokio::test]
    async fn admission_apply_fails_closed_outside_manager_authority() {
        let (_directory, manager_state, path) = temporary_state();
        let legacy_state = Arc::new(
            AppState::with_token_database(&path).with_token_authority(TokenAuthorityMode::Legacy),
        );
        let response = app_with_state(legacy_state)
            .oneshot(admission_request(
                "/v1/tokens/block",
                admission_body(Some(false), "must not write"),
                Some("request-authority-denied"),
                Some("idem-authority-denied"),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        let value = response_json(response).await;
        assert_eq!(value["error"]["code"], "TOKEN_AUTHORITY_WRITE_DENIED");
        assert_eq!(value["error"]["details"]["authorityMode"], "legacy");
        assert_eq!(value["error"]["details"]["dryRun"], false);

        let connection = Connection::open(path).unwrap();
        let status: String = connection
            .query_row("SELECT admission_status FROM token_core", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(status, "admitted");
        drop(manager_state);
    }

    #[tokio::test]
    async fn token_query_reads_persisted_library_without_writing_events() {
        let (_directory, state, path) = temporary_state();
        let app = app_with_state(state);
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/tokens?chainId=4663&library=core&status=admitted")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value = response_json(response).await;
        assert_eq!(value["ok"], true);
        assert_eq!(value["chainId"], 4663);
        assert_eq!(value["data"]["libraryKey"], "core");
        assert_eq!(value["data"]["libraryVersion"], 1);
        assert_eq!(
            value["data"]["tokens"][0]["tokenKeyId"],
            format!("robinhood:4663:{USDG}")
        );
        assert_eq!(value["data"]["tokens"][0]["symbol"], "USDG");
        assert_eq!(value["data"]["tokens"][0]["decimals"], 18);

        let connection = Connection::open(path).unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(event_count, 3);
    }

    #[tokio::test]
    async fn token_resolve_reports_unique_and_ambiguous_symbols() {
        let (_directory, state, _path) = temporary_state();
        let app = app_with_state(state);

        let unique_token = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/tokens/resolve")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        r#"{"chainId":4663,"symbol":"unique","requiredStatus":"admitted"}"#,
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(unique_token.status(), StatusCode::OK);
        let unique_value = response_json(unique_token).await;
        assert_eq!(
            unique_value["data"]["tokenKeyId"],
            format!("robinhood:4663:{UNIQUE}")
        );

        let ambiguous = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/tokens/resolve")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        r#"{"chainId":4663,"symbol":"usdg","requiredStatus":"admitted"}"#,
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(ambiguous.status(), StatusCode::CONFLICT);
        let ambiguous_value = response_json(ambiguous).await;
        assert_eq!(ambiguous_value["error"]["code"], "TOKEN_SYMBOL_AMBIGUOUS");
        assert_eq!(
            ambiguous_value["error"]["details"]["candidateTokenKeyIds"]
                .as_array()
                .unwrap()
                .len(),
            2
        );

        let not_found = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/tokens/resolve")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        r#"{"chainId":4663,"symbol":"NOPE","requiredStatus":"admitted"}"#,
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(not_found.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            response_json(not_found).await["error"]["code"],
            "TOKEN_NOT_FOUND"
        );
    }

    #[tokio::test]
    async fn token_reads_fail_closed_without_database_configuration() {
        let response = app()
            .oneshot(
                Request::builder()
                    .uri("/v1/tokens?chainId=4663&library=core&status=admitted")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            response_json(response).await["error"]["code"],
            "UPSTREAM_UNAVAILABLE"
        );
    }

    #[tokio::test]
    async fn token_audit_reports_counts_duplicates_and_recent_events_without_writing() {
        let (_directory, state, path) = temporary_state();
        let connection = Connection::open(&path).unwrap();
        let event_count_before: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        drop(connection);

        let response = app_with_state(state)
            .oneshot(
                Request::builder()
                    .uri("/v1/tokens/audit?chainId=4663&recentEventLimit=1")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let value = response_json(response).await;
        assert_eq!(value["ok"], true);
        assert_eq!(value["data"]["chainId"], 4663);
        assert_eq!(value["data"]["membershipCount"], 3);
        assert_eq!(value["data"]["distinctTokenCount"], 3);
        assert_eq!(value["data"]["statusTotals"]["admitted"], 3);
        assert_eq!(value["data"]["libraries"].as_array().unwrap().len(), 4);
        assert_eq!(value["data"]["duplicateSymbols"][0]["symbol"], "USDG");
        assert_eq!(
            value["data"]["duplicateSymbols"][0]["tokenKeyIds"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(value["data"]["recentEventLimit"], 1);
        assert_eq!(value["data"]["recentEvents"].as_array().unwrap().len(), 1);

        let connection = Connection::open(path).unwrap();
        let event_count_after: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(event_count_after, event_count_before);
    }

    #[tokio::test]
    async fn token_audit_rejects_excessive_recent_event_limit() {
        let (_directory, state, _path) = temporary_state();
        let response = app_with_state(state)
            .oneshot(
                Request::builder()
                    .uri("/v1/tokens/audit?chainId=4663&recentEventLimit=101")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let value = response_json(response).await;
        assert_eq!(value["error"]["code"], "INVALID_REQUEST");
        assert_eq!(value["error"]["details"]["chainId"], 4663);
    }

    #[tokio::test]
    async fn token_admission_requires_headers_and_explicit_dry_run() {
        let (_directory, state, _path) = temporary_state();
        let app = app_with_state(state);

        let missing_both = app
            .clone()
            .oneshot(admission_request(
                "/v1/tokens/block",
                admission_body(Some(true), "operator verified"),
                None,
                None,
            ))
            .await
            .unwrap();
        assert_eq!(missing_both.status(), StatusCode::BAD_REQUEST);
        let missing_both_value = response_json(missing_both).await;
        assert_eq!(missing_both_value["error"]["code"], "INVALID_REQUEST");
        assert!(missing_both_value["requestId"].is_null());

        let missing_idempotency = app
            .clone()
            .oneshot(admission_request(
                "/v1/tokens/block",
                admission_body(Some(true), "operator verified"),
                Some("request-missing-idem"),
                None,
            ))
            .await
            .unwrap();
        assert_eq!(missing_idempotency.status(), StatusCode::BAD_REQUEST);
        let missing_idempotency_value = response_json(missing_idempotency).await;
        assert_eq!(
            missing_idempotency_value["requestId"],
            "request-missing-idem"
        );
        assert_eq!(
            missing_idempotency_value["error"]["code"],
            "INVALID_REQUEST"
        );

        let missing_dry_run = app
            .oneshot(admission_request(
                "/v1/tokens/block",
                admission_body(None, "operator verified"),
                Some("request-missing-dry-run"),
                Some("idem-missing-dry-run"),
            ))
            .await
            .unwrap();
        assert_eq!(missing_dry_run.status(), StatusCode::BAD_REQUEST);
        let missing_dry_run_value = response_json(missing_dry_run).await;
        assert_eq!(
            missing_dry_run_value["requestId"],
            "request-missing-dry-run"
        );
        assert_eq!(missing_dry_run_value["error"]["code"], "INVALID_REQUEST");
        assert_eq!(missing_dry_run_value["error"]["details"]["chainId"], 4663);
    }

    #[tokio::test]
    async fn token_admission_dry_run_apply_and_idempotent_replay_are_consistent() {
        let (_directory, state, path) = temporary_state();
        let app = app_with_state(state);
        let connection = Connection::open(&path).unwrap();
        let checksum_before: String = connection
            .query_row(
                "SELECT metadata_checksum FROM token_library_state WHERE library_key = 'core'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let event_count_before: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        drop(connection);

        let dry_run = app
            .clone()
            .oneshot(admission_request(
                "/v1/tokens/block",
                admission_body(Some(true), "operator verified"),
                Some("request-dry-run"),
                Some("idem-shared"),
            ))
            .await
            .unwrap();
        assert_eq!(dry_run.status(), StatusCode::OK);
        let dry_run_value = response_json(dry_run).await;
        assert_eq!(dry_run_value["requestId"], "request-dry-run");
        assert_eq!(dry_run_value["data"]["dryRun"], true);
        assert_eq!(dry_run_value["data"]["changed"], true);
        assert_eq!(dry_run_value["data"]["idempotentReplay"], false);
        assert_eq!(dry_run_value["data"]["versionBefore"], 1);
        assert_eq!(dry_run_value["data"]["versionAfter"], 2);
        assert!(dry_run_value["data"]["eventId"].is_null());
        let preview_checksum = dry_run_value["data"]["metadataChecksum"]
            .as_str()
            .unwrap()
            .to_owned();
        assert_ne!(preview_checksum, checksum_before);

        let connection = Connection::open(&path).unwrap();
        let status_after_dry_run: String = connection
            .query_row("SELECT admission_status FROM token_core", [], |row| {
                row.get(0)
            })
            .unwrap();
        let version_after_dry_run: i64 = connection
            .query_row(
                "SELECT library_version FROM token_library_state WHERE library_key = 'core'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let checksum_after_dry_run: String = connection
            .query_row(
                "SELECT metadata_checksum FROM token_library_state WHERE library_key = 'core'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let event_count_after_dry_run: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        drop(connection);
        assert_eq!(status_after_dry_run, "admitted");
        assert_eq!(version_after_dry_run, 1);
        assert_eq!(checksum_after_dry_run, checksum_before);
        assert_eq!(event_count_after_dry_run, event_count_before);

        let apply = app
            .clone()
            .oneshot(admission_request(
                "/v1/tokens/block",
                admission_body(Some(false), "operator verified"),
                Some("request-apply"),
                Some("idem-shared"),
            ))
            .await
            .unwrap();
        assert_eq!(apply.status(), StatusCode::OK);
        let apply_value = response_json(apply).await;
        assert_eq!(apply_value["requestId"], "request-apply");
        assert_eq!(apply_value["data"]["dryRun"], false);
        assert_eq!(apply_value["data"]["changed"], true);
        assert_eq!(apply_value["data"]["idempotentReplay"], false);
        assert_eq!(apply_value["data"]["versionBefore"], 1);
        assert_eq!(apply_value["data"]["versionAfter"], 2);
        assert_eq!(apply_value["data"]["metadataChecksum"], preview_checksum);
        let event_id = apply_value["data"]["eventId"].as_str().unwrap().to_owned();

        let replay = app
            .clone()
            .oneshot(admission_request(
                "/v1/tokens/block",
                admission_body(Some(false), "operator verified"),
                Some("request-apply"),
                Some("idem-shared"),
            ))
            .await
            .unwrap();
        assert_eq!(replay.status(), StatusCode::OK);
        let replay_value = response_json(replay).await;
        assert_eq!(replay_value["data"]["idempotentReplay"], true);
        assert_eq!(replay_value["data"]["eventId"], event_id);
        assert_eq!(replay_value["data"]["requestId"], "request-apply");

        let conflict = app
            .oneshot(admission_request(
                "/v1/tokens/admit",
                admission_body(Some(false), "different intent"),
                Some("request-conflict"),
                Some("idem-shared"),
            ))
            .await
            .unwrap();
        assert_eq!(conflict.status(), StatusCode::CONFLICT);
        let conflict_value = response_json(conflict).await;
        assert_eq!(conflict_value["error"]["code"], "IDEMPOTENCY_KEY_REUSED");
        assert_eq!(conflict_value["requestId"], "request-conflict");
        assert_eq!(
            conflict_value["error"]["details"]["existingRequestId"],
            "request-apply"
        );
        assert_eq!(
            conflict_value["error"]["details"]["submittedRequestId"],
            "request-conflict"
        );

        let connection = Connection::open(path).unwrap();
        let (status, blocked_reason): (String, Option<String>) = connection
            .query_row(
                "SELECT admission_status, blocked_reason FROM token_core",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        let version: i64 = connection
            .query_row(
                "SELECT library_version FROM token_library_state WHERE library_key = 'core'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let event_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_admission_events", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(status, "blocked");
        assert_eq!(blocked_reason.as_deref(), Some("operator verified"));
        assert_eq!(version, 2);
        assert_eq!(event_count, event_count_before + 1);
    }

    fn seed_review_closed_loop(path: &std::path::Path) {
        let connection = Connection::open(path).unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_closed_loop_library_state (
                    network, chain_id, protocol, scope_key, closed_loop_version,
                    materialized_checksum, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, pool_scope_checksum,
                    library_versions_json, library_checksums_json, anchor_count,
                    loop_count, hop_count, optimization_count, skipped_token_count,
                    consumers_need_rebuild, generated_at, updated_at
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'core4', 9,
                           'sha256:closed-loop', 7, 'sha256:candidate-edge', 4,
                           'sha256:observation', 'sha256:pool-scope',
                           '{\"core\":3}', '{\"core\":\"sha256:core\"}', 1, 1, 3, 0,
                           0, 0, 'fixture', 'fixture')",
                [],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_closed_loops (
                    network, chain_id, protocol, scope_key, loop_key, route_key_id,
                    anchor_token_key_id, anchor_address, anchor_symbol,
                    token_y_key_id, token_z_key_id, path_text,
                    path_addresses_json, path_symbols_json, pool_path_key,
                    pool_ids_json, backup_pool_ids_json, hop_candidate_counts_json,
                    hop_candidate_combination_count, recommendation_basis,
                    recommended_first_priority, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, status, source, created_at, updated_at
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'core4', 'loop-1',
                           'route:v1',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           '0x1111111111111111111111111111111111111111', 'USDG',
                           'robinhood:4663:0x2222222222222222222222222222222222222222',
                           'robinhood:4663:0x3333333333333333333333333333333333333333',
                           'USDG -> AMZN -> TA -> USDG',
                           '[\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x3333333333333333333333333333333333333333\",\"0x1111111111111111111111111111111111111111\"]',
                           '[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]', 'pool-path:1',
                           '[\"pool-a\",\"pool-yz\",\"pool-d\"]', '{}',
                           '{\"1\":1,\"2\":1,\"3\":1}', 1, 'fixture', 1, 7,
                           'sha256:candidate-edge', 4, 'sha256:observation',
                           'active', 'fixture', 'fixture', 'fixture')",
                [],
            )
            .unwrap();
    }

    fn review_body(dry_run: Option<bool>) -> String {
        serde_json::json!({
            "chainId": 4663,
            "dryRun": dry_run,
            "evidence": [{
                "loopKey": "loop-1",
                "staticPreview": {"ok": true},
                "reviewedAt": "2026-11-04T00:00:00Z",
                "runArbitrage": {
                    "ok": true,
                    "executable": true,
                    "blockNumber": 100,
                    "signedPnl": {
                        "raw": "12.5 USDG",
                        "amount": 12.5,
                        "currency": "USDG",
                        "address": "0x1111111111111111111111111111111111111111",
                        "unit": "token",
                        "source": "runArbitrage"
                    },
                    "gasEstimate": "120000"
                }
            }]
        })
        .to_string()
    }

    fn insert_review_query_fixture(
        path: &std::path::Path,
        loop_key: &str,
        review_status: &str,
        signed_pnl_amount: Option<f64>,
        reviewed_at: &str,
    ) {
        let connection = Connection::open(path).unwrap();
        let signed_pnl_raw = signed_pnl_amount.map(|amount| format!("{amount} USDG"));
        connection
            .execute(
                "INSERT INTO manager_v4_reviews (
                    network, chain_id, protocol, scope_key, loop_key,
                    closed_loop_version, closed_loop_checksum,
                    candidate_edge_version, candidate_edge_checksum,
                    observation_version, observation_checksum, pool_scope_checksum,
                    anchor_token_key_id, anchor_address, anchor_symbol,
                    path_text, path_addresses_json, path_symbols_json,
                    pool_path_key, pool_ids_json, review_status, review_reason,
                    static_preview_json, run_arbitrage_json, signed_pnl_raw,
                    signed_pnl_amount, signed_pnl_currency, signed_pnl_address,
                    signed_pnl_unit, signed_pnl_source, reviewed_at, created_at,
                    updated_at
                 ) VALUES (
                    'robinhood', 4663, 'uniswap_v4', 'core4', ?1,
                    9, 'sha256:closed-loop', 7, 'sha256:candidate-edge',
                    4, 'sha256:observation', 'sha256:pool-scope',
                    'robinhood:4663:0x1111111111111111111111111111111111111111',
                    '0x1111111111111111111111111111111111111111', 'USDG',
                    ?2, '[\"0x1111111111111111111111111111111111111111\"]',
                    '[\"USDG\"]', 'pool-path:fixture', '[\"pool-a\"]',
                    ?3, NULL, '{\"ok\":true}', '{\"ok\":true,\"executable\":true}',
                    ?4, ?5, ?6,
                    '0x1111111111111111111111111111111111111111',
                    'token', 'runArbitrage', ?7, ?7, ?7
                 )",
                rusqlite::params![
                    loop_key,
                    format!("USDG -> {loop_key}"),
                    review_status,
                    signed_pnl_raw,
                    signed_pnl_amount,
                    signed_pnl_amount.map(|_| "USDG".to_owned()),
                    reviewed_at,
                ],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_review_diagnostics (
                    network, chain_id, protocol, scope_key, loop_key,
                    diagnostic_id, diagnostic_type, diagnostic_code, message,
                    payload_json, observed_at
                 ) VALUES (
                    'robinhood', 4663, 'uniswap_v4', 'core4', ?1, ?2,
                    'other', 'fixture', 'fixture diagnostic', '{}', ?3
                 )",
                rusqlite::params![loop_key, format!("diag-{loop_key}"), reviewed_at],
            )
            .unwrap();
        if signed_pnl_amount.is_some() {
            connection
                .execute(
                    "INSERT INTO manager_v4_review_broadcast_requests (
                        network, chain_id, protocol, scope_key, loop_key,
                        closed_loop_version, closed_loop_checksum,
                        anchor_token_key_id, path_text, path_addresses_json,
                        path_symbols_json, pool_path_key, pool_ids_json,
                        signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                        signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                        request_status, created_at, updated_at
                     ) VALUES (
                        'robinhood', 4663, 'uniswap_v4', 'core4', ?1, 9,
                        'sha256:closed-loop',
                        'robinhood:4663:0x1111111111111111111111111111111111111111',
                        ?2, '[\"0x1111111111111111111111111111111111111111\"]',
                        '[\"USDG\"]', 'pool-path:fixture', '[\"pool-a\"]',
                        ?3, ?4, 'USDG',
                        '0x1111111111111111111111111111111111111111',
                        'token', 'runArbitrage', 'generated', ?5, ?5
                     )",
                    rusqlite::params![
                        loop_key,
                        format!("USDG -> {loop_key}"),
                        signed_pnl_raw,
                        signed_pnl_amount,
                        reviewed_at,
                    ],
                )
                .unwrap();
        }
    }

    fn seed_broadcast_review(path: &std::path::Path) {
        seed_review_closed_loop(path);
        let connection = Connection::open(path).unwrap();
        connection
            .execute(
                "INSERT INTO manager_review_library_state (
                    network, chain_id, protocol, scope_key, review_version,
                    materialized_checksum, closed_loop_version,
                    closed_loop_checksum, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, pool_scope_checksum,
                    library_versions_json, library_checksums_json, loop_count,
                    reviewed_count, completed_count, data_incomplete_count,
                    not_executable_count, failed_count, broadcast_request_count,
                    generated_at, updated_at
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'core4', 9,
                           'sha256:review', 7, 'sha256:closed-loop', 5,
                           'sha256:candidate-edge', 3, 'sha256:observation',
                           'sha256:pool-scope', '{\"core\":3}',
                           '{\"core\":\"sha256:core\"}', 1, 1, 1, 0, 0, 0, 1,
                           'fixture', 'fixture')",
                [],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_review_broadcast_requests (
                    network, chain_id, protocol, scope_key, loop_key,
                    closed_loop_version, closed_loop_checksum,
                    anchor_token_key_id, path_text, path_addresses_json,
                    path_symbols_json, pool_path_key, pool_ids_json,
                    signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                    signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                    request_status, request_reason, created_at, updated_at
                 ) VALUES ('robinhood', 4663, 'uniswap_v4', 'core4', 'loop-1',
                           7, 'sha256:closed-loop',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           'USDG -> AMZN -> TA -> USDG',
                           '[\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x3333333333333333333333333333333333333333\",\"0x1111111111111111111111111111111111111111\"]',
                           '[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]', 'pool-path:1',
                           '[\"pool-a\",\"pool-yz\",\"pool-d\"]',
                           '12.5 USDG', 12.5, 'USDG',
                           '0x1111111111111111111111111111111111111111',
                           'token', 'runArbitrage', 'generated',
                           'positive executable signed PnL', 'fixture', 'fixture')",
                [],
            )
            .unwrap();
        drop(connection);
    }

    fn broadcast_submit_body(dry_run: Option<bool>) -> String {
        serde_json::json!({
            "chainId": 4663,
            "dryRun": dry_run,
            "attempts": [{
                "attemptId": "attempt-1",
                "loopKey": "loop-1",
                "signerAddress": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "nonce": 42,
                "gasLimit": 500000,
                "gasPriceWei": "1000000000",
                "rpcUrl": "https://rpc.example.invalid",
                "rpcName": "fixture-primary",
                "evidence": {"source": "fixture"}
            }]
        })
        .to_string()
    }

    fn broadcast_finalize_body(dry_run: Option<bool>) -> String {
        serde_json::json!({
            "chainId": 4663,
            "dryRun": dry_run,
            "finalizations": [{
                "attemptId": "attempt-1",
                "status": "failed",
                "txHash": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "receipt": {"status": "0x0"},
                "statusReason": "rpc rejected transaction",
                "diagnostics": [{
                    "diagnosticId": "diag-1",
                    "diagnosticType": "rpc_unavailable",
                    "diagnosticCode": "RPC_TIMEOUT",
                    "message": "primary RPC timed out",
                    "rpcUrl": "https://rpc.example.invalid",
                    "txHash": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "payload": {"attempt": 1},
                    "observedAt": "2026-11-04T01:00:00Z"
                }],
                "evidence": {"receipt": "timeout"},
                "finalizedAt": "2026-11-04T01:00:01Z"
            }]
        })
        .to_string()
    }

    #[tokio::test]
    async fn review_run_enforces_headers_dry_run_and_authority() {
        let (_directory, _state, path) = temporary_state();
        seed_review_closed_loop(&path);
        let legacy = Arc::new(
            AppState::with_token_database(&path).with_review_authority(TokenAuthorityMode::Legacy),
        );

        let missing_headers = app_with_state(legacy.clone())
            .oneshot(admission_request(
                "/v1/reviews/run",
                review_body(Some(true)),
                None,
                None,
            ))
            .await
            .unwrap();
        assert_eq!(missing_headers.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(missing_headers).await["error"]["code"],
            "INVALID_REQUEST"
        );

        let denied = app_with_state(legacy)
            .oneshot(admission_request(
                "/v1/reviews/run",
                review_body(Some(false)),
                Some("review-denied"),
                Some("review-denied-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(denied.status(), StatusCode::FORBIDDEN);
        let denied_value = response_json(denied).await;
        assert_eq!(
            denied_value["error"]["code"],
            "TOKEN_AUTHORITY_WRITE_DENIED"
        );
        assert_eq!(denied_value["error"]["details"]["authorityMode"], "legacy");

        let connection = Connection::open(&path).unwrap();
        let review_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_v4_reviews", [], |row| {
                row.get(0)
            })
            .unwrap();
        let request_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(review_count, 0);
        assert_eq!(request_count, 0);
    }

    #[tokio::test]
    async fn review_dry_run_apply_and_idempotent_replay_are_consistent() {
        let (_directory, _state, path) = temporary_state();
        seed_review_closed_loop(&path);
        let state = Arc::new(
            AppState::with_token_database(&path).with_review_authority(TokenAuthorityMode::Manager),
        );
        let app = app_with_state(state);

        let dry = app
            .clone()
            .oneshot(admission_request(
                "/v1/reviews/run",
                review_body(Some(true)),
                Some("review-dry"),
                Some("review-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(dry.status(), StatusCode::OK);
        let dry_value = response_json(dry).await;
        assert_eq!(dry_value["data"]["dryRun"], true);
        assert_eq!(dry_value["data"]["completedCount"], 1);
        assert_eq!(dry_value["data"]["broadcastRequestCount"], 1);
        assert_eq!(dry_value["data"]["idempotentReplay"], false);

        let connection = Connection::open(&path).unwrap();
        let review_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_v4_reviews", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(review_count, 0);
        drop(connection);

        let apply = app
            .clone()
            .oneshot(admission_request(
                "/v1/reviews/run",
                review_body(Some(false)),
                Some("review-apply"),
                Some("review-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(apply.status(), StatusCode::OK);
        let apply_value = response_json(apply).await;
        assert_eq!(apply_value["requestId"], "review-apply");
        assert_eq!(apply_value["data"]["dryRun"], false);
        assert_eq!(apply_value["data"]["reviewVersion"], 1);
        assert_eq!(apply_value["data"]["idempotentReplay"], false);

        let replay = app
            .clone()
            .oneshot(admission_request(
                "/v1/reviews/run",
                review_body(Some(false)),
                Some("review-new-request"),
                Some("review-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(replay.status(), StatusCode::OK);
        assert_eq!(
            response_json(replay).await["data"]["idempotentReplay"],
            true
        );

        let snapshot = app
            .oneshot(
                Request::builder()
                    .uri("/v1/reviews?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(snapshot.status(), StatusCode::OK);
        let snapshot_value = response_json(snapshot).await;
        assert_eq!(snapshot_value["data"]["status"]["enabled"], true);
        assert_eq!(snapshot_value["data"]["status"]["broadcastRequestCount"], 1);
        assert_eq!(
            snapshot_value["data"]["reviews"][0]["reviewStatus"],
            "completed"
        );
        assert_eq!(
            snapshot_value["data"]["broadcastRequests"][0]["requestStatus"],
            "generated"
        );
    }

    #[tokio::test]
    async fn review_query_filters_categories_outcomes_and_paginates_related_rows() {
        let (_directory, _state, path) = temporary_state();
        seed_review_closed_loop(&path);
        let state = Arc::new(
            AppState::with_token_database(&path).with_review_authority(TokenAuthorityMode::Manager),
        );
        let app = app_with_state(state);

        let applied = app
            .clone()
            .oneshot(admission_request(
                "/v1/reviews/run",
                review_body(Some(false)),
                Some("review-query-seed"),
                Some("review-query-seed-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(applied.status(), StatusCode::OK);

        insert_review_query_fixture(
            &path,
            "loop-zero",
            "completed",
            Some(0.0),
            "2026-11-04T00:00:05Z",
        );
        insert_review_query_fixture(
            &path,
            "loop-data-incomplete",
            "data_incomplete",
            None,
            "2026-11-04T00:00:04Z",
        );
        insert_review_query_fixture(
            &path,
            "loop-not-executable",
            "not_executable",
            Some(-1.0),
            "2026-11-04T00:00:03Z",
        );
        insert_review_query_fixture(&path, "loop-failed", "failed", None, "2026-11-04T00:00:02Z");

        let success = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/reviews?chainId=4663&outcome=success&offset=0&limit=20")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(success.status(), StatusCode::OK);
        let success_value = response_json(success).await;
        assert_eq!(success_value["data"]["filteredCount"], 2);
        assert_eq!(success_value["data"]["categoryCounts"]["total"], 5);
        assert_eq!(
            success_value["data"]["categoryCounts"]["readyNotSubmitted"],
            1
        );
        assert_eq!(
            success_value["data"]["categoryCounts"]["confirmedNoProfit"],
            1
        );
        assert_eq!(
            success_value["data"]["reviews"].as_array().unwrap().len(),
            2
        );

        let ready = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/reviews?chainId=4663&category=ready_not_submitted")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(ready.status(), StatusCode::OK);
        let ready_value = response_json(ready).await;
        assert_eq!(ready_value["data"]["filteredCount"], 1);
        assert_eq!(ready_value["data"]["reviews"][0]["loopKey"], "loop-1");
        assert_eq!(
            ready_value["data"]["reviews"][0]["pageCategory"],
            "ready_not_submitted"
        );
        assert_eq!(
            ready_value["data"]["broadcastRequests"]
                .as_array()
                .unwrap()
                .len(),
            1
        );

        let incomplete = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/reviews?chainId=4663&category=data_incomplete")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(incomplete.status(), StatusCode::OK);
        let incomplete_value = response_json(incomplete).await;
        assert_eq!(incomplete_value["data"]["filteredCount"], 1);
        assert_eq!(
            incomplete_value["data"]["reviews"][0]["reviewStatus"],
            "data_incomplete"
        );

        let failed = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/reviews?chainId=4663&outcome=failed&category=review_failed&offset=1&limit=1")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(failed.status(), StatusCode::OK);
        let failed_value = response_json(failed).await;
        assert_eq!(failed_value["data"]["filteredCount"], 2);
        assert_eq!(failed_value["data"]["offset"], 1);
        assert_eq!(failed_value["data"]["limit"], 1);
        assert_eq!(failed_value["data"]["reviews"].as_array().unwrap().len(), 1);
        assert_eq!(failed_value["data"]["reviews"][0]["loopKey"], "loop-failed");
        assert_eq!(
            failed_value["data"]["diagnostics"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            failed_value["data"]["diagnostics"][0]["loopKey"],
            "loop-failed"
        );

        let bad_category = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/reviews?chainId=4663&category=unknown")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(bad_category.status(), StatusCode::BAD_REQUEST);

        let invalid_page = app
            .oneshot(
                Request::builder()
                    .uri("/v1/reviews?chainId=4663&offset=900&limit=200")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(invalid_page.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn broadcast_routes_enforce_headers_dry_run_authority_and_terminal_state() {
        let (_directory, _state, path) = temporary_state();
        seed_broadcast_review(&path);

        let legacy = Arc::new(
            AppState::with_token_database(&path)
                .with_broadcast_authority(TokenAuthorityMode::Legacy),
        );
        let missing_headers = app_with_state(legacy.clone())
            .oneshot(admission_request(
                "/v1/broadcasts/submit",
                broadcast_submit_body(Some(true)),
                None,
                None,
            ))
            .await
            .unwrap();
        assert_eq!(missing_headers.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(missing_headers).await["error"]["code"],
            "INVALID_REQUEST"
        );

        let missing_dry_run = app_with_state(legacy.clone())
            .oneshot(admission_request(
                "/v1/broadcasts/submit",
                broadcast_submit_body(None),
                Some("broadcast-missing-dry-run"),
                Some("broadcast-missing-dry-run-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(missing_dry_run.status(), StatusCode::BAD_REQUEST);

        let denied = app_with_state(legacy)
            .oneshot(admission_request(
                "/v1/broadcasts/submit",
                broadcast_submit_body(Some(false)),
                Some("broadcast-denied"),
                Some("broadcast-denied-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(denied.status(), StatusCode::FORBIDDEN);
        let denied_value = response_json(denied).await;
        assert_eq!(
            denied_value["error"]["code"],
            "TOKEN_AUTHORITY_WRITE_DENIED"
        );
        assert_eq!(denied_value["error"]["details"]["authorityMode"], "legacy");

        let state = Arc::new(
            AppState::with_token_database(&path)
                .with_broadcast_authority(TokenAuthorityMode::Manager),
        );
        let app = app_with_state(state);
        let dry = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/submit",
                broadcast_submit_body(Some(true)),
                Some("broadcast-dry"),
                Some("broadcast-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(dry.status(), StatusCode::OK);
        let dry_value = response_json(dry).await;
        assert_eq!(dry_value["data"]["dryRun"], true);
        assert_eq!(dry_value["data"]["pendingCount"], 1);
        assert_eq!(dry_value["data"]["idempotentReplay"], false);

        let connection = Connection::open(&path).unwrap();
        let attempt_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(attempt_count, 0);
        drop(connection);

        let apply = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/submit",
                broadcast_submit_body(Some(false)),
                Some("broadcast-apply"),
                Some("broadcast-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(apply.status(), StatusCode::OK);
        let apply_value = response_json(apply).await;
        assert_eq!(apply_value["data"]["dryRun"], false);
        assert_eq!(apply_value["data"]["pendingCount"], 1);
        assert_eq!(apply_value["data"]["idempotentReplay"], false);

        let connection = Connection::open(&path).unwrap();
        let attempt_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(attempt_count, 1);
        drop(connection);

        let snapshot_before = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/broadcasts?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(snapshot_before.status(), StatusCode::OK);
        let snapshot_value = response_json(snapshot_before).await;
        assert_eq!(snapshot_value["data"]["attempts"][0]["status"], "pending");
        assert_eq!(snapshot_value["data"]["attempts"][0]["nonce"], 42);

        let replay = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/submit",
                broadcast_submit_body(Some(false)),
                Some("broadcast-new-request"),
                Some("broadcast-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(replay.status(), StatusCode::OK);
        assert_eq!(
            response_json(replay).await["data"]["idempotentReplay"],
            true
        );

        let finalize_dry = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/finalize",
                broadcast_finalize_body(Some(true)),
                Some("broadcast-finalize-dry"),
                Some("broadcast-finalize-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(finalize_dry.status(), StatusCode::OK);
        let connection = Connection::open(&path).unwrap();
        let terminal_count: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts
                 WHERE status IN ('success', 'failed')",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(terminal_count, 0);
        drop(connection);

        let finalize = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/finalize",
                broadcast_finalize_body(Some(false)),
                Some("broadcast-finalize"),
                Some("broadcast-finalize-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(finalize.status(), StatusCode::OK);
        let finalize_value = response_json(finalize).await;
        assert_eq!(finalize_value["data"]["failedCount"], 1);
        assert_eq!(finalize_value["data"]["terminalCount"], 1);

        let snapshot_after = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/broadcasts?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(snapshot_after.status(), StatusCode::OK);
        let value = response_json(snapshot_after).await;
        assert_eq!(value["data"]["attempts"][0]["status"], "failed");
        assert_eq!(
            value["data"]["attempts"][0]["rpcUrl"],
            "https://rpc.example.invalid"
        );
        assert_eq!(value["data"]["attempts"][0]["nonce"], 42);
        assert_eq!(
            value["data"]["attempts"][0]["txHash"],
            "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        );
        assert_eq!(value["data"]["diagnostics"].as_array().unwrap().len(), 1);
        assert_eq!(
            value["data"]["diagnostics"][0]["diagnosticType"],
            "rpc_unavailable"
        );

        let filtered_snapshot = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/broadcasts?chainId=4663&attemptId=attempt-1&limit=1")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(filtered_snapshot.status(), StatusCode::OK);
        let filtered_value = response_json(filtered_snapshot).await;
        assert_eq!(
            filtered_value["data"]["attempts"].as_array().unwrap().len(),
            1
        );
        assert_eq!(
            filtered_value["data"]["attempts"][0]["attemptId"],
            "attempt-1"
        );
        assert_eq!(
            filtered_value["data"]["diagnostics"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            filtered_value["data"]["diagnostics"][0]["attemptId"],
            "attempt-1"
        );

        let failed_by_status = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/broadcasts?chainId=4663&status=failed&offset=0&limit=1")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(failed_by_status.status(), StatusCode::OK);
        let failed_value = response_json(failed_by_status).await;
        assert_eq!(
            failed_value["data"]["attempts"].as_array().unwrap().len(),
            1
        );
        assert_eq!(failed_value["data"]["attempts"][0]["status"], "failed");
        assert_eq!(
            failed_value["data"]["diagnostics"]
                .as_array()
                .unwrap()
                .len(),
            1
        );

        let success_by_status = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/broadcasts?chainId=4663&status=success&offset=0&limit=1")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(success_by_status.status(), StatusCode::OK);
        let success_value = response_json(success_by_status).await;
        assert!(success_value["data"]["attempts"]
            .as_array()
            .unwrap()
            .is_empty());
        assert!(success_value["data"]["diagnostics"]
            .as_array()
            .unwrap()
            .is_empty());

        let clear_without_dry_run = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/clear",
                r#"{"chainId":4663}"#.to_owned(),
                Some("clear-missing-dry-run"),
                Some("clear-missing-dry-run"),
            ))
            .await
            .unwrap();
        assert_eq!(clear_without_dry_run.status(), StatusCode::BAD_REQUEST);

        let clear_dry = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/clear",
                r#"{"chainId":4663,"dryRun":true}"#.to_owned(),
                Some("clear-dry"),
                Some("clear-dry"),
            ))
            .await
            .unwrap();
        assert_eq!(clear_dry.status(), StatusCode::OK);
        let clear_dry_value = response_json(clear_dry).await;
        assert_eq!(clear_dry_value["data"]["dryRun"], true);
        assert_eq!(clear_dry_value["data"]["clearedAttempts"], 1);
        assert_eq!(clear_dry_value["data"]["remainingAttempts"], 1);

        let clear_apply = app
            .clone()
            .oneshot(admission_request(
                "/v1/broadcasts/clear",
                r#"{"chainId":4663,"dryRun":false}"#.to_owned(),
                Some("clear-apply"),
                Some("clear-apply"),
            ))
            .await
            .unwrap();
        assert_eq!(clear_apply.status(), StatusCode::OK);
        let clear_apply_value = response_json(clear_apply).await;
        assert_eq!(clear_apply_value["data"]["dryRun"], false);
        assert_eq!(clear_apply_value["data"]["remainingAttempts"], 0);

        let clear_replay = app
            .oneshot(admission_request(
                "/v1/broadcasts/clear",
                r#"{"chainId":4663,"dryRun":false}"#.to_owned(),
                Some("clear-replay"),
                Some("clear-apply"),
            ))
            .await
            .unwrap();
        assert_eq!(clear_replay.status(), StatusCode::OK);
        assert_eq!(
            response_json(clear_replay).await["data"]["idempotentReplay"],
            true
        );
    }

    #[tokio::test]
    async fn pipeline_routes_enforce_authority_dry_run_idempotency_and_checkpoint() {
        let (_directory, manager_state, path) = pipeline_state();
        let legacy_state = Arc::new(
            AppState::with_token_database(&path).with_token_authority(TokenAuthorityMode::Manager),
        );
        let legacy_app = app_with_state(legacy_state);

        let missing_headers = legacy_app
            .clone()
            .oneshot(admission_request(
                "/v1/pipeline/refresh",
                pipeline_refresh_body(Some(true), &["candidate_edge"]),
                None,
                None,
            ))
            .await
            .unwrap();
        assert_eq!(missing_headers.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(missing_headers).await["error"]["code"],
            "INVALID_REQUEST"
        );

        let missing_dry_run = legacy_app
            .clone()
            .oneshot(admission_request(
                "/v1/pipeline/refresh",
                pipeline_refresh_body(None, &["candidate_edge"]),
                Some("pipeline-missing-dry-run"),
                Some("pipeline-missing-dry-run-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(missing_dry_run.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response_json(missing_dry_run).await["error"]["code"],
            "INVALID_REQUEST"
        );

        let denied = legacy_app
            .clone()
            .oneshot(admission_request(
                "/v1/pipeline/refresh",
                pipeline_refresh_body(Some(false), &["candidate_edge"]),
                Some("pipeline-denied"),
                Some("pipeline-denied-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(denied.status(), StatusCode::FORBIDDEN);
        let denied_value = response_json(denied).await;
        assert_eq!(
            denied_value["error"]["code"],
            "TOKEN_AUTHORITY_WRITE_DENIED"
        );
        assert_eq!(denied_value["error"]["details"]["authorityMode"], "legacy");

        let dry_run = legacy_app
            .oneshot(admission_request(
                "/v1/pipeline/refresh",
                pipeline_refresh_body(Some(true), &["candidate_edge"]),
                Some("pipeline-dry-run"),
                Some("pipeline-dry-run-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(dry_run.status(), StatusCode::OK);
        let dry_value = response_json(dry_run).await;
        assert_eq!(dry_value["data"]["dryRun"], true);
        assert_eq!(dry_value["data"]["run"]["status"], "running");
        let connection = Connection::open(&path).unwrap();
        let run_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_pipeline_runs", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(run_count, 0);
        drop(connection);

        let app = app_with_state(manager_state);
        let applied = app
            .clone()
            .oneshot(admission_request(
                "/v1/pipeline/refresh",
                pipeline_refresh_body(Some(false), &["candidate_edge"]),
                Some("pipeline-apply"),
                Some("pipeline-apply-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(applied.status(), StatusCode::OK);
        let applied_value = response_json(applied).await;
        assert_eq!(applied_value["data"]["dryRun"], false);
        assert_eq!(applied_value["data"]["idempotentReplay"], false);
        let run_id = applied_value["data"]["run"]["runId"]
            .as_str()
            .unwrap()
            .to_owned();
        let step_id = applied_value["data"]["steps"][0]["stepId"]
            .as_str()
            .unwrap()
            .to_owned();

        let reused = app
            .clone()
            .oneshot(admission_request(
                "/v1/pipeline/refresh",
                pipeline_refresh_body(Some(false), &["candidate_exchange"]),
                Some("pipeline-reused"),
                Some("pipeline-apply-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(reused.status(), StatusCode::CONFLICT);
        assert_eq!(
            response_json(reused).await["error"]["code"],
            "IDEMPOTENCY_KEY_REUSED"
        );

        let begin = app
            .clone()
            .oneshot(admission_request(
                "/v1/pipeline/steps/begin",
                pipeline_step_body("/begin", &run_id, &step_id, Some(false)),
                Some("pipeline-begin"),
                Some("pipeline-begin-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(begin.status(), StatusCode::OK);
        let begin_value = response_json(begin).await;
        assert_eq!(begin_value["data"]["step"]["status"], "running");
        assert_eq!(begin_value["data"]["step"]["attemptCount"], 1);

        let status = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/pipeline/status?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(status.status(), StatusCode::OK);
        let status_value = response_json(status).await;
        assert_eq!(status_value["data"]["latestRun"]["runId"], run_id);
        assert_eq!(status_value["data"]["currentStep"]["stepId"], step_id);

        let runs = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/v1/pipeline/runs?chainId=4663")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(runs.status(), StatusCode::OK);
        assert_eq!(response_json(runs).await["data"][0]["runId"], run_id);

        let detail = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/v1/pipeline/runs/{run_id}?chainId=4663"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(detail.status(), StatusCode::OK);
        assert_eq!(
            response_json(detail).await["data"]["steps"][0]["status"],
            "running"
        );

        let complete = app
            .clone()
            .oneshot(admission_request(
                "/v1/pipeline/steps/complete",
                pipeline_step_body("/complete", &run_id, &step_id, Some(false)),
                Some("pipeline-complete-failed"),
                Some("pipeline-complete-failed-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(complete.status(), StatusCode::OK);
        let complete_value = response_json(complete).await;
        assert_eq!(complete_value["data"]["step"]["status"], "failed");
        assert_eq!(complete_value["data"]["run"]["status"], "failed");

        let retry = app
            .oneshot(admission_request(
                "/v1/pipeline/steps/begin",
                pipeline_step_body("/begin", &run_id, &step_id, Some(false)),
                Some("pipeline-begin-retry"),
                Some("pipeline-begin-retry-idem"),
            ))
            .await
            .unwrap();
        assert_eq!(retry.status(), StatusCode::OK);
        let retry_value = response_json(retry).await;
        assert_eq!(retry_value["data"]["run"]["status"], "running");
        assert_eq!(retry_value["data"]["step"]["attemptCount"], 2);
    }

    #[tokio::test]
    async fn overview_endpoint_returns_manager_projection_without_legacy_reads() {
        let (_directory, state, _path) = pipeline_state();
        let app = app_with_state(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/v1/overview?chainId=4663&limit=100")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let value = response_json(response).await;
        assert_eq!(value["data"]["source"], "manager");
        assert_eq!(value["data"]["chainId"], 4663);
        assert_eq!(value["data"]["selectedProtocols"][0], "uniswap_v4");
        assert_eq!(value["data"]["decisionBasis"], "manager_closed_loop");
        assert_eq!(
            value["data"]["marketObservationSourceStage"],
            "manager_closed_loop"
        );
        assert_eq!(value["data"]["summary"]["arbitragePathCount"], 0);
        assert_eq!(value["data"]["summary"]["watchPairCount"], 0);
        assert_eq!(value["data"]["dataQuality"]["usdDepthRequired"], false);
    }
}
