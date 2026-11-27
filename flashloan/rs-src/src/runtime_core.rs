use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, RwLock, RwLockReadGuard, RwLockWriteGuard};

use broadcast_manager::{
    clear_broadcast_records_write, read_broadcast_snapshot_page, read_broadcast_status,
    BroadcastClearRequest, BroadcastError, BroadcastRecordStore, BroadcastStatus,
};
use candidate_edge_pool_manager::{
    materialize_candidate_edges_write, read_candidate_snapshot, read_candidate_status,
    CandidateEdgeError, CandidateEdgeStatus, CandidateEdgeWriteRequest, ExpectedUpstream,
};
use candidate_exchange_pool_manager::{
    materialize_candidate_exchanges_write, read_candidate_exchange_snapshot,
    read_candidate_exchange_status, CandidateExchangeError, CandidateExchangeStatus,
    CandidateExchangeWriteRequest, ExpectedUpstream as CandidateExchangeExpectedUpstream,
};
use closed_loop_manager::{
    materialize_closed_loops, read_closed_loop_snapshot, read_closed_loop_status, ClosedLoopError,
    ClosedLoopStatus,
};
use composite_manager::{
    read_manager_overview_snapshot, read_pipeline_status, BroadcastRef, CandidateEdgeRef,
    CandidateExchangeRef, ClosedLoopRef, ConsistencyIssue, DirectionalPoolRef,
    PipelineSnapshot, PoolScopeRef, ReviewRef, TokenLibraryRef, UpstreamSnapshot,
};
use directional_pool_manager::{
    materialize_directional_pools_for_chain, read_directional_pool_status, DirectionalPoolError,
    DirectionalPoolStatus,
};
use manager_contracts::TokenAuthorityMode;
use pool_manager::{read_scope_status, resolve_pool_ids_by_key, PoolKeyIdentity, ScopeStatus};
use review_manager::{
    clear_review_records_write, evaluate_route_parity, read_review_page, read_review_status,
    replay_recorded_routes, ParityRequest, ReviewClearRequest, ReviewOutcomeFilter,
    ReviewReadQuery, ReviewRecordStore, ReviewStatus,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use token_manager::{read_library_state, LibraryKey, TokenLibraryState, TokenQueryError};

const RUNTIME_CORE_SCHEMA: &str = "rust_runtime_core_v1";
const DATA_SOURCE: &str = "rust_runtime_core";
const SCOPE_KEY: &str = "core4";
/// The canonical Uniswap V4 quoter used by the Robinhood chain.
///
/// The value mirrors `flashloan/chains/robinhood/chain.json`. It is only
/// a default: a caller may point the shadow comparison at another quoter.
const DEFAULT_V4_QUOTER_ADDRESS: &str = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94";
const EVENT_LIMIT: usize = 64;
const TOKEN_LIBRARIES: [LibraryKey; 4] = [
    LibraryKey::Core,
    LibraryKey::Stocks,
    LibraryKey::Extension,
    LibraryKey::Test,
];

static RUNTIME_CORE_REGISTRY: OnceLock<Mutex<HashMap<i64, Arc<RuntimeCore>>>> = OnceLock::new();
static RUNTIME_CORE_SEQUENCE: AtomicU64 = AtomicU64::new(1);
static RUNTIME_EVENT_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Debug)]
pub enum RuntimeCoreError {
    InvalidIdentity(String),
    AlreadyRegistered {
        chain_id: i64,
        existing_instance_id: String,
        requested_database_path: String,
        existing_broadcast_authority: String,
        requested_broadcast_authority: String,
        existing_candidate_authority: String,
        requested_candidate_authority: String,
        existing_candidate_exchange_authority: String,
        requested_candidate_exchange_authority: String,
    },
    NotRegistered(i64),
    Closed(i64),
    SnapshotUnavailable(i64),
    ManagerRead {
        manager: &'static str,
        message: String,
    },
    ManagerCommand {
        manager: &'static str,
        command: &'static str,
        code: &'static str,
        http_status: u16,
        message: String,
    },
    AuthorityDenied {
        command: &'static str,
        authority: TokenAuthorityMode,
    },
    InconsistentSnapshot(Vec<ConsistencyIssue>),
    InvalidCommand(String),
    InvalidManagerView(String),
    Serialization(String),
}

impl fmt::Display for RuntimeCoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidIdentity(message) => write!(formatter, "invalid runtime identity: {message}"),
            Self::AlreadyRegistered {
                chain_id,
                existing_instance_id,
                requested_database_path,
                existing_broadcast_authority,
                requested_broadcast_authority,
                existing_candidate_authority,
                requested_candidate_authority,
                existing_candidate_exchange_authority,
                requested_candidate_exchange_authority,
            } => write!(
                formatter,
                "chain {chain_id} already has RuntimeCore {existing_instance_id}; requested database {requested_database_path}, broadcast authority {requested_broadcast_authority} (existing {existing_broadcast_authority}), candidate authority {requested_candidate_authority} (existing {existing_candidate_authority}), candidate exchange authority {requested_candidate_exchange_authority} (existing {existing_candidate_exchange_authority})"
            ),
            Self::NotRegistered(chain_id) => {
                write!(formatter, "RuntimeCore is not registered for chain {chain_id}")
            }
            Self::Closed(chain_id) => write!(formatter, "RuntimeCore is closed for chain {chain_id}"),
            Self::SnapshotUnavailable(chain_id) => {
                write!(formatter, "RuntimeCore snapshot is unavailable for chain {chain_id}")
            }
            Self::ManagerRead { manager, message } => {
                write!(formatter, "{manager} snapshot read failed: {message}")
            }
            Self::ManagerCommand {
                manager,
                command,
                message,
                ..
            } => write!(formatter, "{manager} command {command} failed: {message}"),
            Self::AuthorityDenied { command, authority } => write!(
                formatter,
                "RuntimeCore command {command} cannot apply while authority mode is {authority}"
            ),
            Self::InconsistentSnapshot(issues) => write!(
                formatter,
                "RuntimeCore rejected a mixed manager snapshot: {}",
                serde_json::to_string(issues).unwrap_or_else(|_| "[]".to_owned())
            ),
            Self::InvalidCommand(message) => write!(formatter, "invalid RuntimeCore command: {message}"),
            Self::InvalidManagerView(message) => {
                write!(formatter, "invalid RuntimeCore manager view: {message}")
            }
            Self::Serialization(message) => {
                write!(formatter, "RuntimeCore serialization failed: {message}")
            }
        }
    }
}

impl std::error::Error for RuntimeCoreError {}

impl RuntimeCoreError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::InvalidIdentity(_) => "RUNTIME_CORE_INVALID_IDENTITY",
            Self::AlreadyRegistered { .. } => "RUNTIME_CORE_IDENTITY_CONFLICT",
            Self::NotRegistered(_) => "RUNTIME_CORE_NOT_REGISTERED",
            Self::Closed(_) => "RUNTIME_CORE_CLOSED",
            Self::SnapshotUnavailable(_) => "RUNTIME_CORE_SNAPSHOT_UNAVAILABLE",
            Self::ManagerRead { .. } => "RUNTIME_CORE_MANAGER_READ_FAILED",
            Self::ManagerCommand { code, .. } => code,
            Self::AuthorityDenied { command, .. } if command == &"rebuild_candidate_edges" => {
                "CANDIDATE_AUTHORITY_WRITE_DENIED"
            }
            Self::AuthorityDenied { command, .. }
                if command == &"rebuild_candidate_exchange_pools" =>
            {
                "CANDIDATE_EXCHANGE_AUTHORITY_WRITE_DENIED"
            }
            Self::AuthorityDenied { .. } => "BROADCAST_AUTHORITY_WRITE_DENIED",
            Self::InconsistentSnapshot(_) => "RUNTIME_CORE_INCONSISTENT_SNAPSHOT",
            Self::InvalidCommand(_) => "RUNTIME_CORE_INVALID_COMMAND",
            Self::InvalidManagerView(_) => "RUNTIME_CORE_INVALID_MANAGER_VIEW",
            Self::Serialization(_) => "RUNTIME_CORE_SERIALIZATION_FAILED",
        }
    }

    pub fn http_status(&self) -> u16 {
        match self {
            Self::InvalidIdentity(_) | Self::InvalidCommand(_) | Self::InvalidManagerView(_) => 400,
            Self::AlreadyRegistered { .. } | Self::AuthorityDenied { .. } => 403,
            Self::NotRegistered(_) | Self::Closed(_) | Self::SnapshotUnavailable(_) => 503,
            Self::ManagerRead { .. } => 503,
            Self::ManagerCommand { http_status, .. } => *http_status,
            Self::InconsistentSnapshot(_) => 409,
            Self::Serialization(_) => 500,
        }
    }
}

#[derive(Debug, Clone)]
struct RuntimeIdentity {
    chain_id: i64,
    network: String,
    database_path: PathBuf,
    protocol: String,
}

impl RuntimeIdentity {
    fn new(
        chain_id: i64,
        network: impl Into<String>,
        database_path: impl Into<PathBuf>,
        protocol: impl Into<String>,
    ) -> Result<Self, RuntimeCoreError> {
        if chain_id <= 0 {
            return Err(RuntimeCoreError::InvalidIdentity(
                "chain_id must be greater than zero".to_owned(),
            ));
        }
        let network = network.into().trim().to_owned();
        if network.is_empty() {
            return Err(RuntimeCoreError::InvalidIdentity(
                "network must not be empty".to_owned(),
            ));
        }
        let database_path = database_path.into();
        if database_path.as_os_str().is_empty() {
            return Err(RuntimeCoreError::InvalidIdentity(
                "database_path must not be empty".to_owned(),
            ));
        }
        let protocol = protocol.into().trim().to_ascii_lowercase();
        if protocol.is_empty() {
            return Err(RuntimeCoreError::InvalidIdentity(
                "protocol must not be empty".to_owned(),
            ));
        }
        Ok(Self {
            chain_id,
            network,
            database_path,
            protocol,
        })
    }

    fn matches_database(&self, database_path: &Path) -> bool {
        self.database_path == database_path
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCoreEvent {
    pub event_id: String,
    pub event_type: String,
    pub request_id: Option<String>,
    pub idempotency_key: Option<String>,
    pub detail: Value,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCoreStatus {
    pub schema_version: String,
    pub instance_id: String,
    pub chain_id: i64,
    pub network: String,
    pub protocol: String,
    pub candidate_authority: String,
    pub candidate_exchange_authority: String,
    pub broadcast_authority: String,
    pub database_path: String,
    pub state: String,
    pub closed: bool,
    pub generation: u64,
    pub snapshot_available: bool,
    pub last_refresh_at: Option<String>,
    pub last_error: Option<String>,
    pub data_source: String,
    pub recent_events: Vec<RuntimeCoreEvent>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeReadiness {
    pub token_libraries_present: bool,
    pub pool_scope_enabled: bool,
    pub candidate_edge_enabled: bool,
    pub candidate_exchange_enabled: bool,
    pub closed_loop_enabled: bool,
    pub review_enabled: bool,
    pub broadcast_enabled: bool,
    pub pipeline_enabled: bool,
    pub chain_complete: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCoreComponents {
    pub token_libraries: BTreeMap<String, Option<TokenLibraryState>>,
    pub pool_scope: ScopeStatus,
    pub directional_pool: DirectionalPoolStatus,
    pub candidate_edge: CandidateEdgeStatus,
    pub candidate_exchange: CandidateExchangeStatus,
    pub closed_loop: ClosedLoopStatus,
    pub review: ReviewStatus,
    pub broadcast: BroadcastStatus,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCoreSnapshot {
    pub schema_version: String,
    pub instance_id: String,
    pub chain_id: i64,
    pub network: String,
    pub protocol: String,
    pub database_path: String,
    pub generated_at: String,
    pub generation: u64,
    pub data_source: String,
    pub upstream_versions: BTreeMap<String, Option<i64>>,
    pub checksums: BTreeMap<String, Option<String>>,
    pub upstream_fingerprint: String,
    pub snapshot_checksum: String,
    pub readiness: RuntimeReadiness,
    pub components: RuntimeCoreComponents,
    pub pipeline: PipelineSnapshot,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCoreCloseResult {
    pub ok: bool,
    pub chain_id: i64,
    pub instance_id: String,
    pub closed: bool,
}

struct RuntimeCoreState {
    generation: u64,
    snapshot: Option<Arc<RuntimeCoreSnapshot>>,
    last_refresh_at: Option<String>,
    last_error: Option<String>,
    events: VecDeque<RuntimeCoreEvent>,
}

/// The options of the `review_parity_replay` command.
///
/// The replay always reads. The options only narrow **which** database and
/// **how much** of it are read; they never grant authority and never enable
/// a write.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReviewParityReplayOptions {
    network: Option<String>,
    protocol: Option<String>,
    database_path: Option<String>,
    limit: Option<usize>,
    tolerance: Option<f64>,
    /// Which recorded routes the replay covers.
    ///
    /// `newestPerRoute` is the default: a parity check measures the route as
    /// it stands, so the current attempt of each route is the one that
    /// carries the evidence. `allAttempts` replays the whole recorded
    /// history, which repeats the same route many times against older pool
    /// states.
    selection: Option<String>,
}

/// The options of the `review_parity_compare` command.
///
/// The request is shaped by the Python compatibility layer and answered by
/// Review Manager. Python never performs the comparison itself.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReviewParityCompareOptions {
    network: Option<String>,
    protocol: Option<String>,
    route_addresses: Vec<String>,
    pool_ids: Vec<String>,
    amount_raw: Option<String>,
    flash_fee_bps: Option<i64>,
    onchain_profit_raw: Option<String>,
    onchain_executable: Option<bool>,
    chain_live_amount_out_raw: Option<String>,
    chain_live_required_repay_raw: Option<String>,
    tolerance: Option<f64>,
}

/// The options of the `review_parity_replay_payload` command.
///
/// The command is a **read-only** comparison of one live review payload. The
/// payload is carried in memory and is never persisted by the command.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReviewParityReplayPayloadOptions {
    network: Option<String>,
    protocol: Option<String>,
    flash_fee_bps: Option<i64>,
    tolerance: Option<f64>,
    payload: Option<Value>,
}

/// The options of the `pool_roles_manage` command.
///
/// Pool Manager owns the pool roles. The runtime core only names the
/// command, forwards the options and returns the report.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PoolRolesManageOptions {
    pub network: Option<String>,
    pub chain_id: Option<i64>,
    pub protocol: Option<String>,
    /// `materialize` refreshes the role assignment, `cleanup` prunes the
    /// backups, `status` only reports.
    pub action: Option<String>,
    /// The startup gate of the cleanup. It is off by default.
    pub cleanup_enabled: Option<bool>,
    pub retention_days: Option<i64>,
    pub cleanup_interval_days: Option<i64>,
    pub min_pools: Option<i64>,
    pub min_trade_observations: Option<i64>,
    pub apply: Option<bool>,
}

/// The options of the `review_shadow_compare` command.
///
/// The shadow comparison is ARCH-4 stage A: the local memory pool and the
/// chain-side `eth_call` are run side by side and compared. The command is
/// **read-only** and never changes a decision: the chain result stays
/// authoritative, and the memory result never blocks an execution.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReviewShadowCompareOptions {
    network: Option<String>,
    protocol: Option<String>,
    chain_id: Option<i64>,
    hops: Vec<ShadowHopRequest>,
    rpc_urls: Vec<String>,
    quoter_address: Option<String>,
    call_from: Option<String>,
    timeout_seconds: Option<u64>,
    tolerance: Option<f64>,
    affected_path: Option<String>,
    requests_per_second: Option<f64>,
    /// The arbiter version the memory side prices with.
    ///
    /// Version 1 (`single_tick`) constrains every hop to one tick range.
    /// The name travels with the request so the report says which arithmetic
    /// produced it; an unknown name is refused by Review Manager rather
    /// than silently replaced by a default.
    arbiter_version: Option<String>,
}

/// One hop of a shadow comparison, as the compatibility layer sends it.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ShadowHopRequest {
    pool_id: Option<String>,
    token0: Option<String>,
    token1: Option<String>,
    fee: Option<u32>,
    tick_spacing: Option<i64>,
    hooks: Option<String>,
    zero_for_one: Option<bool>,
    amount_in_raw: Option<String>,
    #[serde(default)]
    hook_data: Option<String>,
}

/// The options of the `pool_resolve_keys` command.
///
/// Pool Keys are resolved by Pool Manager because Pool Manager owns the pool
/// table. Python never queries it.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PoolResolveKeysOptions {
    network: Option<String>,
    protocol: Option<String>,
    pool_keys: Vec<PoolKeyRequest>,
}

/// One PoolKey, as the compatibility layer sends it.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PoolKeyRequest {
    currency0: Option<String>,
    currency1: Option<String>,
    fee: Option<i64>,
    tick_spacing: Option<i64>,
    hooks: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ManagerViewOptions {
    anchor_token_key_id: Option<String>,
    loop_key: Option<String>,
    category: Option<String>,
    status: Option<String>,
    outcome: Option<String>,
    attempt_id: Option<String>,
    offset: Option<usize>,
}

impl Default for RuntimeCoreState {
    fn default() -> Self {
        Self {
            generation: 0,
            snapshot: None,
            last_refresh_at: None,
            last_error: None,
            events: VecDeque::new(),
        }
    }
}

pub struct RuntimeCore {
    instance_id: String,
    identity: RuntimeIdentity,
    candidate_authority: TokenAuthorityMode,
    candidate_exchange_authority: TokenAuthorityMode,
    broadcast_authority: TokenAuthorityMode,
    closed: AtomicBool,
    refresh_lock: Mutex<()>,
    state: RwLock<RuntimeCoreState>,
}

impl fmt::Debug for RuntimeCore {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RuntimeCore")
            .field("instance_id", &self.instance_id)
            .field("identity", &self.identity)
            .field("candidate_authority", &self.candidate_authority)
            .field(
                "candidate_exchange_authority",
                &self.candidate_exchange_authority,
            )
            .field("broadcast_authority", &self.broadcast_authority)
            .field("closed", &self.closed.load(Ordering::SeqCst))
            .finish_non_exhaustive()
    }
}

impl RuntimeCore {
    #[cfg(test)]
    pub fn register(
        chain_id: i64,
        network: impl Into<String>,
        database_path: impl Into<PathBuf>,
        protocol: impl Into<String>,
    ) -> Result<Arc<Self>, RuntimeCoreError> {
        Self::register_with_authority(
            chain_id,
            network,
            database_path,
            protocol,
            TokenAuthorityMode::Legacy,
        )
    }

    #[cfg(test)]
    pub fn register_with_authority(
        chain_id: i64,
        network: impl Into<String>,
        database_path: impl Into<PathBuf>,
        protocol: impl Into<String>,
        broadcast_authority: TokenAuthorityMode,
    ) -> Result<Arc<Self>, RuntimeCoreError> {
        Self::register_with_authorities(
            chain_id,
            network,
            database_path,
            protocol,
            TokenAuthorityMode::Legacy,
            broadcast_authority,
        )
    }

    #[cfg(test)]
    pub fn register_with_authorities(
        chain_id: i64,
        network: impl Into<String>,
        database_path: impl Into<PathBuf>,
        protocol: impl Into<String>,
        candidate_authority: TokenAuthorityMode,
        broadcast_authority: TokenAuthorityMode,
    ) -> Result<Arc<Self>, RuntimeCoreError> {
        Self::register_with_all_authorities(
            chain_id,
            network,
            database_path,
            protocol,
            candidate_authority,
            broadcast_authority,
            TokenAuthorityMode::Legacy,
        )
    }

    pub fn register_with_all_authorities(
        chain_id: i64,
        network: impl Into<String>,
        database_path: impl Into<PathBuf>,
        protocol: impl Into<String>,
        candidate_authority: TokenAuthorityMode,
        broadcast_authority: TokenAuthorityMode,
        candidate_exchange_authority: TokenAuthorityMode,
    ) -> Result<Arc<Self>, RuntimeCoreError> {
        let identity = RuntimeIdentity::new(chain_id, network, database_path, protocol)?;
        let mut registry = registry_lock();
        if let Some(existing) = registry.get(&chain_id).cloned() {
            if existing.identity.matches_database(&identity.database_path)
                && existing.identity.network == identity.network
                && existing.identity.protocol == identity.protocol
                && existing.candidate_authority == candidate_authority
                && existing.broadcast_authority == broadcast_authority
                && existing.candidate_exchange_authority == candidate_exchange_authority
            {
                return Ok(existing);
            }
            return Err(RuntimeCoreError::AlreadyRegistered {
                chain_id,
                existing_instance_id: existing.instance_id.clone(),
                requested_database_path: identity.database_path.to_string_lossy().into_owned(),
                existing_broadcast_authority: existing.broadcast_authority.as_str().to_owned(),
                requested_broadcast_authority: broadcast_authority.as_str().to_owned(),
                existing_candidate_authority: existing.candidate_authority.as_str().to_owned(),
                requested_candidate_authority: candidate_authority.as_str().to_owned(),
                existing_candidate_exchange_authority: existing
                    .candidate_exchange_authority
                    .as_str()
                    .to_owned(),
                requested_candidate_exchange_authority: candidate_exchange_authority
                    .as_str()
                    .to_owned(),
            });
        }

        let sequence = RUNTIME_CORE_SEQUENCE.fetch_add(1, Ordering::SeqCst);
        let instance_id = format!(
            "runtime-core-{}-{}-{}",
            std::process::id(),
            chain_id,
            sequence
        );
        let core = Arc::new(Self {
            instance_id: instance_id.clone(),
            identity,
            candidate_authority,
            candidate_exchange_authority,
            broadcast_authority,
            closed: AtomicBool::new(false),
            refresh_lock: Mutex::new(()),
            state: RwLock::new(RuntimeCoreState::default()),
        });
        {
            let mut state = write_state(&core.state);
            state.events.push_back(new_event(
                "runtime_registered",
                None,
                None,
                json!({
                    "instanceId": instance_id,
                    "chainId": chain_id,
                }),
            ));
        }
        registry.insert(chain_id, Arc::clone(&core));
        Ok(core)
    }

    pub fn get(chain_id: i64) -> Result<Arc<Self>, RuntimeCoreError> {
        let registry = registry_lock();
        registry
            .get(&chain_id)
            .cloned()
            .ok_or(RuntimeCoreError::NotRegistered(chain_id))
    }

    pub fn close(chain_id: i64) -> Result<RuntimeCoreCloseResult, RuntimeCoreError> {
        let core = {
            let mut registry = registry_lock();
            let core = registry
                .remove(&chain_id)
                .ok_or(RuntimeCoreError::NotRegistered(chain_id))?;
            core
        };
        core.close_instance();
        Ok(RuntimeCoreCloseResult {
            ok: true,
            chain_id,
            instance_id: core.instance_id.clone(),
            closed: true,
        })
    }

    pub fn status(&self) -> RuntimeCoreStatus {
        let state = read_state(&self.state);
        let closed = self.closed.load(Ordering::SeqCst);
        let status = if closed {
            "closed"
        } else if state.snapshot.is_some() {
            if state
                .snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.readiness.chain_complete)
            {
                "ready"
            } else {
                "partial"
            }
        } else if state.last_error.is_some() {
            "error"
        } else {
            "registered"
        };
        RuntimeCoreStatus {
            schema_version: RUNTIME_CORE_SCHEMA.to_owned(),
            instance_id: self.instance_id.clone(),
            chain_id: self.identity.chain_id,
            network: self.identity.network.clone(),
            protocol: self.identity.protocol.clone(),
            candidate_authority: self.candidate_authority.as_str().to_owned(),
            candidate_exchange_authority: self.candidate_exchange_authority.as_str().to_owned(),
            broadcast_authority: self.broadcast_authority.as_str().to_owned(),
            database_path: self.identity.database_path.to_string_lossy().into_owned(),
            state: status.to_owned(),
            closed,
            generation: state.generation,
            snapshot_available: state.snapshot.is_some(),
            last_refresh_at: state.last_refresh_at.clone(),
            last_error: state.last_error.clone(),
            data_source: DATA_SOURCE.to_owned(),
            recent_events: state.events.iter().cloned().collect(),
        }
    }

    pub fn record_command_completed(
        &self,
        command: &str,
        request_id: &str,
        idempotency_key: &str,
        dry_run: bool,
        detail: Value,
    ) {
        let mut state = write_state(&self.state);
        push_event(
            &mut state.events,
            new_event(
                "runtime_command_completed",
                Some(request_id.trim().to_owned()),
                Some(idempotency_key.trim().to_owned()),
                json!({
                    "command": command,
                    "dryRun": dry_run,
                    "detail": detail,
                }),
            ),
        );
    }

    pub fn state(&self) -> Result<Arc<RuntimeCoreSnapshot>, RuntimeCoreError> {
        self.ensure_open()?;
        read_state(&self.state)
            .snapshot
            .clone()
            .ok_or(RuntimeCoreError::SnapshotUnavailable(
                self.identity.chain_id,
            ))
    }

    pub fn refresh(&self) -> Result<Arc<RuntimeCoreSnapshot>, RuntimeCoreError> {
        self.refresh_with_context(None, None)
    }

    /// Runs a command without an options document.
    ///
    /// This is the convenience form for commands that take no input. The
    /// options-carrying form is [`Self::execute_command_with_options`].
    #[allow(dead_code)]
    pub fn execute_command(
        &self,
        command: &str,
        request_id: &str,
        idempotency_key: &str,
        dry_run: bool,
    ) -> Result<Value, RuntimeCoreError> {
        self.execute_command_with_options(command, request_id, idempotency_key, dry_run, None)
    }

    /// Runs a command, carrying an optional options document.
    ///
    /// The options document is the only channel through which the Python
    /// compatibility layer can describe *what* a read-only measurement should
    /// look at. It never carries authority and never enables a write: the
    /// write commands ignore it entirely.
    pub fn execute_command_with_options(
        &self,
        command: &str,
        request_id: &str,
        idempotency_key: &str,
        dry_run: bool,
        options_json: Option<&str>,
    ) -> Result<Value, RuntimeCoreError> {
        validate_command_identity(request_id, idempotency_key)?;
        match command.trim().to_ascii_lowercase().as_str() {
            "refresh_snapshot" => {
                let snapshot =
                    self.refresh_with_context(Some(request_id), Some(idempotency_key))?;
                Ok(json!({
                    "accepted": true,
                    "command": "refresh_snapshot",
                    "requestId": request_id,
                    "idempotencyKey": idempotency_key,
                    "dryRun": dry_run,
                    "idempotentReplay": false,
                    "generation": snapshot.generation,
                    "snapshotChecksum": snapshot.snapshot_checksum,
                    "dataSource": DATA_SOURCE,
                }))
            }
            "rebuild_candidate_edges" => {
                self.ensure_open()?;
                if !dry_run && !self.candidate_authority.manager_apply_enabled() {
                    return Err(RuntimeCoreError::AuthorityDenied {
                        command: "rebuild_candidate_edges",
                        authority: self.candidate_authority,
                    });
                }
                let outcome = materialize_candidate_edges_write(
                    &self.identity.database_path,
                    CandidateEdgeWriteRequest {
                        network: self.identity.network.clone(),
                        chain_id: self.identity.chain_id,
                        operation: "candidate_edge.rebuild".to_owned(),
                        request_id: request_id.trim().to_owned(),
                        idempotency_key: idempotency_key.trim().to_owned(),
                        apply: !dry_run,
                        expected_upstream: ExpectedUpstream::default(),
                    },
                )
                .map_err(map_candidate_command_error)?;
                let idempotent_replay = outcome.idempotent_replay;
                let mut result = serde_json::to_value(outcome)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                let object = result.as_object_mut().ok_or_else(|| {
                    RuntimeCoreError::Serialization(
                        "candidate edge rebuild outcome must serialize as an object".to_owned(),
                    )
                })?;
                object.insert("accepted".to_owned(), Value::Bool(true));
                object.insert(
                    "command".to_owned(),
                    Value::String("rebuild_candidate_edges".to_owned()),
                );
                object.insert(
                    "version".to_owned(),
                    Value::String(RUNTIME_CORE_SCHEMA.to_owned()),
                );
                object.insert(
                    "requestId".to_owned(),
                    Value::String(request_id.trim().to_owned()),
                );
                object.insert(
                    "idempotencyKey".to_owned(),
                    Value::String(idempotency_key.trim().to_owned()),
                );
                object.insert(
                    "dataSource".to_owned(),
                    Value::String(DATA_SOURCE.to_owned()),
                );
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "rebuild_candidate_edges",
                            "dryRun": dry_run,
                            "idempotentReplay": idempotent_replay,
                            "candidateVersion": result
                                .get("candidateVersion")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                            "materializedChecksum": result
                                .get("materializedChecksum")
                                .cloned()
                                .unwrap_or(Value::Null),
                            "changedRows": result
                                .get("changedRows")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                            "consumersNeedRebuild": result
                                .get("consumersNeedRebuild")
                                .cloned()
                                .unwrap_or_else(|| json!(false)),
                        }),
                    ),
                );
                Ok(result)
            }
            "rebuild_candidate_exchange_pools" => {
                self.ensure_open()?;
                if !dry_run && !self.candidate_exchange_authority.manager_apply_enabled() {
                    return Err(RuntimeCoreError::AuthorityDenied {
                        command: "rebuild_candidate_exchange_pools",
                        authority: self.candidate_exchange_authority,
                    });
                }
                let outcome = materialize_candidate_exchanges_write(
                    &self.identity.database_path,
                    CandidateExchangeWriteRequest {
                        network: self.identity.network.clone(),
                        chain_id: self.identity.chain_id,
                        operation: "candidate-exchange.rebuild".to_owned(),
                        request_id: request_id.trim().to_owned(),
                        idempotency_key: idempotency_key.trim().to_owned(),
                        apply: !dry_run,
                        expected_upstream: CandidateExchangeExpectedUpstream::default(),
                    },
                )
                .map_err(map_candidate_exchange_command_error)?;
                let idempotent_replay = outcome.idempotent_replay;
                let mut result = serde_json::to_value(outcome)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                let object = result.as_object_mut().ok_or_else(|| {
                    RuntimeCoreError::Serialization(
                        "candidate exchange rebuild outcome must serialize as an object".to_owned(),
                    )
                })?;
                object.insert("accepted".to_owned(), Value::Bool(true));
                object.insert(
                    "command".to_owned(),
                    Value::String("rebuild_candidate_exchange_pools".to_owned()),
                );
                object.insert(
                    "version".to_owned(),
                    Value::String(RUNTIME_CORE_SCHEMA.to_owned()),
                );
                object.insert(
                    "requestId".to_owned(),
                    Value::String(request_id.trim().to_owned()),
                );
                object.insert(
                    "idempotencyKey".to_owned(),
                    Value::String(idempotency_key.trim().to_owned()),
                );
                object.insert(
                    "dataSource".to_owned(),
                    Value::String(DATA_SOURCE.to_owned()),
                );
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "rebuild_candidate_exchange_pools",
                            "dryRun": dry_run,
                            "idempotentReplay": idempotent_replay,
                            "candidateEdgeVersion": result
                                .get("candidateEdgeVersion")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                            "observationVersion": result
                                .get("observationVersion")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                            "materializedChecksum": result
                                .get("materializedChecksum")
                                .cloned()
                                .unwrap_or(Value::Null),
                            "changedRows": result
                                .get("changedRows")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                            "consumersNeedRebuild": result
                                .get("consumersNeedRebuild")
                                .cloned()
                                .unwrap_or_else(|| json!(false)),
                        }),
                    ),
                );
                Ok(result)
            }
            "review_parity_compare" => {
                self.ensure_open()?;
                // A **read-only** comparison between what the chain reported
                // and what the off-chain simulator derives from the local
                // pool state. The comparison itself is Review Manager's work:
                // the recorded reviews are Review Manager's own table and the
                // pool state is read through Pool Manager's published API.
                let options = parse_review_parity_compare_options(options_json)?;
                let network = options
                    .network
                    .clone()
                    .unwrap_or_else(|| self.identity.network.clone());
                let protocol = options
                    .protocol
                    .clone()
                    .unwrap_or_else(|| review_manager::default_protocol().to_owned());
                let amount_raw = options
                    .amount_raw
                    .as_deref()
                    .and_then(|text| parse_signed_raw_option(Some(text)))
                    .unwrap_or_default();
                let mut request = ParityRequest::new(
                    options.route_addresses.clone(),
                    options.pool_ids.clone(),
                    amount_raw,
                    options
                        .flash_fee_bps
                        .unwrap_or(review_manager::DEFAULT_FLASH_FEE_BPS),
                );
                request.onchain_profit_raw = options
                    .onchain_profit_raw
                    .as_deref()
                    .and_then(|text| parse_signed_raw_option(Some(text)));
                request.chain_live_amount_out_raw = options
                    .chain_live_amount_out_raw
                    .as_deref()
                    .and_then(|text| parse_signed_raw_option(Some(text)));
                request.chain_live_required_repay_raw = options
                    .chain_live_required_repay_raw
                    .as_deref()
                    .and_then(|text| parse_signed_raw_option(Some(text)));
                if let Some(tolerance) = options.tolerance {
                    request.tolerance = tolerance;
                }
                let verdict = evaluate_route_parity(
                    &self.identity.database_path,
                    &network,
                    self.identity.chain_id,
                    &protocol,
                    &request,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "review_manager",
                    message: error.to_string(),
                })?;
                let mut payload = serde_json::to_value(&verdict)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                if let Some(object) = payload.as_object_mut() {
                    // The chain's executability is part of the verdict, but
                    // Review Manager compares *amounts*. The executability of
                    // the chain side is carried through as an input so the
                    // compatibility layer can report it unchanged.
                    object.insert(
                        "onchainExecutable".to_owned(),
                        match options.onchain_executable {
                            Some(value) => Value::Bool(value),
                            None => Value::Null,
                        },
                    );
                }
                let mut result = json!({
                    "accepted": true,
                    "command": "review_parity_compare",
                    "version": RUNTIME_CORE_SCHEMA,
                    "requestId": request_id.trim(),
                    "idempotencyKey": idempotency_key.trim(),
                    "dryRun": dry_run,
                    "dataSource": DATA_SOURCE,
                    "network": network,
                    "protocol": protocol,
                    "data": payload,
                });
                if let Some(object) = result.as_object_mut() {
                    object.insert(
                        "generation".to_owned(),
                        Value::Number(read_state(&self.state).generation.into()),
                    );
                }
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "review_parity_compare",
                            "dryRun": dry_run,
                            "state": result
                                .get("data")
                                .and_then(|data| data.get("state"))
                                .cloned()
                                .unwrap_or(Value::Null),
                        }),
                    ),
                );
                Ok(result)
            }
            "review_parity_replay_payload" => {
                self.ensure_open()?;
                // This is a read-only measurement of one **live** review
                // payload. The payload is supplied in memory, is never
                // written to the database, and the comparison itself stays
                // inside Review Manager.
                let options = parse_review_parity_replay_payload_options(options_json)?;
                let network = options
                    .network
                    .clone()
                    .unwrap_or_else(|| self.identity.network.clone());
                let protocol = options
                    .protocol
                    .clone()
                    .unwrap_or_else(|| review_manager::default_protocol().to_owned());
                let payload = options.payload.clone().ok_or_else(|| {
                    RuntimeCoreError::InvalidCommand(
                        "review_parity_replay_payload requires payload".to_owned(),
                    )
                })?;
                let tolerance = options
                    .tolerance
                    .unwrap_or(review_manager::DEFAULT_PROFIT_RELATIVE_TOLERANCE);
                let request = review_manager::parity_request_from_review_payload(
                    &payload,
                    options
                        .flash_fee_bps
                        .unwrap_or(review_manager::DEFAULT_FLASH_FEE_BPS),
                    tolerance,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "review_manager",
                    message: error.to_string(),
                })?;
                let verdict = review_manager::evaluate_route_parity_with_review_time_state(
                    &self.identity.database_path,
                    &network,
                    self.identity.chain_id,
                    &protocol,
                    &request,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "review_manager",
                    message: error.to_string(),
                })?;
                let mut result = serde_json::to_value(verdict)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                if let Some(object) = result.as_object_mut() {
                    object.insert("reviewTimeStateUsed".to_owned(), Value::Bool(true));
                    object.insert(
                        "reviewTimePoolCount".to_owned(),
                        Value::Number(request.review_time_pool_states.len().into()),
                    );
                }
                let mut payload = json!({
                    "accepted": true,
                    "command": "review_parity_replay_payload",
                    "version": RUNTIME_CORE_SCHEMA,
                    "requestId": request_id.trim(),
                    "idempotencyKey": idempotency_key.trim(),
                    "dryRun": dry_run,
                    "dataSource": DATA_SOURCE,
                    "network": network,
                    "protocol": protocol,
                    "persisted": false,
                    "data": result,
                });
                if let Some(object) = payload.as_object_mut() {
                    object.insert(
                        "generation".to_owned(),
                        Value::Number(read_state(&self.state).generation.into()),
                    );
                }
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "review_parity_replay_payload",
                            "dryRun": dry_run,
                            "persisted": false,
                            "state": payload
                                .get("data")
                                .and_then(|data| data.get("state"))
                                .cloned()
                                .unwrap_or(Value::Null),
                        }),
                    ),
                );
                Ok(payload)
            }
            "review_shadow_compare" => {
                self.ensure_open()?;
                // ARCH-4 stage A: the local memory pool and the chain-side
                // `eth_call` are compared side by side. The comparison is
                // **read-only** and never changes a decision:
                //
                // * the chain result stays authoritative;
                // * the memory result never blocks an execution;
                // * a chain call that timed out, answered malformed, or saw
                //   the chain move underneath it is reported as `untrusted`,
                //   and that is the **only** thing that may block.
                //
                // The whole computation lives in Review Manager. The runtime
                // core only names the command and returns the report.
                let options = parse_review_shadow_compare_options(options_json)?;
                let network = options
                    .network
                    .clone()
                    .unwrap_or_else(|| self.identity.network.clone());
                let protocol = options
                    .protocol
                    .clone()
                    .unwrap_or_else(|| review_manager::default_protocol().to_owned());
                let chain_id = options.chain_id.unwrap_or(self.identity.chain_id);
                let mut hops = Vec::with_capacity(options.hops.len());
                for hop in &options.hops {
                    hops.push(review_manager::ShadowHopRequest {
                        pool_id: hop.pool_id.clone().unwrap_or_default(),
                        token0: hop.token0.clone().unwrap_or_default(),
                        token1: hop.token1.clone().unwrap_or_default(),
                        fee: hop.fee.unwrap_or_default(),
                        tick_spacing: hop.tick_spacing.unwrap_or_default(),
                        hooks: hop.hooks.clone().unwrap_or_default(),
                        zero_for_one: hop.zero_for_one.unwrap_or(false),
                        amount_in_raw: hop.amount_in_raw.clone().unwrap_or_default(),
                        hook_data: hop.hook_data.clone(),
                    });
                }
                let request = review_manager::ShadowRequest {
                    network: network.clone(),
                    chain_id,
                    protocol: protocol.clone(),
                    hops,
                    rpc_urls: options.rpc_urls.clone(),
                    quoter_address: options
                        .quoter_address
                        .clone()
                        .unwrap_or_else(|| DEFAULT_V4_QUOTER_ADDRESS.to_string()),
                    call_from: options.call_from.clone(),
                    timeout_seconds: options.timeout_seconds,
                    tolerance: options.tolerance,
                    affected_path: options.affected_path.clone(),
                    requests_per_second: options.requests_per_second,
                    arbiter_version: options.arbiter_version.clone(),
                };
                let report =
                    review_manager::run_shadow_comparison(&self.identity.database_path, &request);
                let payload = serde_json::to_value(&report)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                let mut result = json!({
                    "accepted": true,
                    "command": "review_shadow_compare",
                    "version": RUNTIME_CORE_SCHEMA,
                    "requestId": request_id.trim(),
                    "idempotencyKey": idempotency_key.trim(),
                    "dryRun": dry_run,
                    "dataSource": DATA_SOURCE,
                    "network": network,
                    "protocol": protocol,
                    "data": payload,
                });
                if let Some(object) = result.as_object_mut() {
                    object.insert(
                        "generation".to_owned(),
                        Value::Number(read_state(&self.state).generation.into()),
                    );
                }
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "review_shadow_compare",
                            "dryRun": dry_run,
                            "comparisonCount": report.comparison_count,
                            "executableMismatchCount": report.executable_mismatch_count,
                            "unexplainedCount": report.unexplained_count,
                        }),
                    ),
                );
                Ok(result)
            }
            "pool_roles_manage" => {
                self.ensure_open()?;
                // Pool Manager owns the pool roles:
                //
                // * the candidate pools are the best priced pools of a
                //   direction and are refreshed dynamically;
                // * the recommendation pool is the single best priced pool
                //   of a direction and is refreshed dynamically as well;
                // * the backup pools are the remaining pools and are pruned
                //   when they did not trade inside the retention window.
                //
                // The cleanup carries a startup gate: while the runtime still
                // carries little data, nothing is deleted at all.
                let options = parse_pool_roles_manage_options(options_json)?;
                let network = options
                    .network
                    .clone()
                    .unwrap_or_else(|| self.identity.network.clone());
                let protocol = options
                    .protocol
                    .clone()
                    .unwrap_or_else(|| review_manager::default_protocol().to_owned());
                let chain_id = options.chain_id.unwrap_or(self.identity.chain_id);
                let action = options
                    .action
                    .clone()
                    .unwrap_or_else(|| "status".to_string());
                let apply = options.apply.unwrap_or(false) && !dry_run;
                let database_path = self.identity.database_path.clone();
                let payload = pool_manager::run_pool_role_maintenance(
                    database_path.to_string_lossy().as_ref(),
                    &network,
                    chain_id,
                    &protocol,
                    &action,
                    pool_manager::PoolRoleOptions {
                        retention_days: options
                            .retention_days
                            .unwrap_or(pool_manager::BACKUP_RETENTION_DAYS),
                        cleanup_interval_days: options
                            .cleanup_interval_days
                            .unwrap_or(pool_manager::CLEANUP_INTERVAL_DAYS),
                        cleanup_enabled: options.cleanup_enabled.unwrap_or(false),
                        min_pools: options.min_pools.unwrap_or(500),
                        min_trade_observations: options.min_trade_observations.unwrap_or(100),
                        apply,
                        ..pool_manager::PoolRoleOptions::default()
                    },
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "pool_manager",
                    message: error.to_string(),
                })?;
                let mut result = json!({
                    "accepted": true,
                    "command": "pool_roles_manage",
                    "version": RUNTIME_CORE_SCHEMA,
                    "requestId": request_id.trim(),
                    "idempotencyKey": idempotency_key.trim(),
                    "dryRun": dry_run,
                    "dataSource": DATA_SOURCE,
                    "network": network,
                    "protocol": protocol,
                    "data": payload,
                });
                if let Some(object) = result.as_object_mut() {
                    object.insert(
                        "generation".to_owned(),
                        Value::Number(read_state(&self.state).generation.into()),
                    );
                }
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "pool_roles_manage",
                            "dryRun": dry_run,
                            "action": action,
                        }),
                    ),
                );
                Ok(result)
            }
            "pool_measurement_population" => {
                self.ensure_open()?;
                // The measurement population is resolved by Pool Manager,
                // because Pool Manager owns the admission rules. In
                // particular a **dynamic-fee pool is never part of the
                // population**: its effective rate is decided by a hook at
                // swap time, so the off-chain arithmetic can never reproduce
                // the rate the chain charges.
                //
                // The Python layer reaches this only through the in-memory
                // RuntimeCore, so it never opens the database and never
                // queries a manager-owned table.
                let options = parse_pool_measurement_population_options(options_json)?;
                let network = options
                    .network
                    .clone()
                    .unwrap_or_else(|| self.identity.network.clone());
                let protocol = options
                    .protocol
                    .clone()
                    .unwrap_or_else(|| review_manager::default_protocol().to_owned());
                let chain_id = options.chain_id.unwrap_or(self.identity.chain_id);
                let limit = options.limit.unwrap_or(60).max(1) as usize;
                let database_path = self.identity.database_path.clone();
                let pool_ids = pool_manager::tick_swap::resolve_measurement_pool_ids(
                    database_path.to_string_lossy().as_ref(),
                    &network,
                    chain_id,
                    limit,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "pool_manager",
                    message: error.to_string(),
                })?;
                let mut result = json!({
                    "accepted": true,
                    "command": "pool_measurement_population",
                    "version": RUNTIME_CORE_SCHEMA,
                    "requestId": request_id.trim(),
                    "idempotencyKey": idempotency_key.trim(),
                    "dryRun": dry_run,
                    "dataSource": DATA_SOURCE,
                    "network": network,
                    "protocol": protocol,
                    "data": json!({
                        "poolIds": pool_ids,
                        "poolCount": pool_ids.len(),
                        "excludesDynamicFeePools": true,
                    }),
                });
                if let Some(object) = result.as_object_mut() {
                    object.insert(
                        "generation".to_owned(),
                        Value::Number(read_state(&self.state).generation.into()),
                    );
                }
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "pool_measurement_population",
                            "dryRun": dry_run,
                        }),
                    ),
                );
                Ok(result)
            }
            "pool_resolve_keys" => {
                self.ensure_open()?;
                // Pool Manager owns the pool table, so Pool Manager resolves
                // the PoolKeys. The compatibility layer only carries them.
                let options = parse_pool_resolve_keys_options(options_json)?;
                let network = options
                    .network
                    .clone()
                    .unwrap_or_else(|| self.identity.network.clone());
                let protocol = options
                    .protocol
                    .clone()
                    .unwrap_or_else(|| review_manager::default_protocol().to_owned());
                let mut identities: Vec<PoolKeyIdentity> =
                    Vec::with_capacity(options.pool_keys.len());
                for key in &options.pool_keys {
                    let Some(identity) = PoolKeyIdentity::new(
                        key.currency0.clone().unwrap_or_default(),
                        key.currency1.clone().unwrap_or_default(),
                        key.fee.unwrap_or_default(),
                        key.tick_spacing.unwrap_or_default(),
                        key.hooks.as_deref(),
                    ) else {
                        // A key that does not describe a pool resolves to no
                        // pool. It is not an error: an unknown pool simply
                        // cannot be simulated.
                        identities.push(PoolKeyIdentity {
                            currency0: String::new(),
                            currency1: String::new(),
                            fee: 0,
                            tick_spacing: 0,
                            hooks: String::new(),
                        });
                        continue;
                    };
                    identities.push(identity);
                }
                let pool_ids = resolve_pool_ids_by_key(
                    &self.identity.database_path,
                    &network,
                    self.identity.chain_id,
                    &protocol,
                    &identities,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "pool_manager",
                    message: error.to_string(),
                })?;
                let mut result = json!({
                    "accepted": true,
                    "command": "pool_resolve_keys",
                    "version": RUNTIME_CORE_SCHEMA,
                    "requestId": request_id.trim(),
                    "idempotencyKey": idempotency_key.trim(),
                    "dryRun": dry_run,
                    "dataSource": DATA_SOURCE,
                    "network": network,
                    "protocol": protocol,
                    "data": {
                        "poolIds": pool_ids,
                    },
                });
                if let Some(object) = result.as_object_mut() {
                    object.insert(
                        "generation".to_owned(),
                        Value::Number(read_state(&self.state).generation.into()),
                    );
                }
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "pool_resolve_keys",
                            "dryRun": dry_run,
                            "resolvedCount": pool_ids
                                .iter()
                                .filter(|pool_id| !pool_id.is_empty())
                                .count(),
                        }),
                    ),
                );
                Ok(result)
            }
            "review_parity_replay" => {
                self.ensure_open()?;
                // The parity replay is a **read-only** measurement: it replays
                // the recorded reviews against the local pool state and
                // compares the result with what the chain reported. It never
                // writes, so it needs no authority and it is safe to run
                // against a replica of the production database.
                //
                // The whole computation lives in Review Manager. The runtime
                // core only names the command and returns the report.
                let options = parse_review_parity_replay_options(options_json)?;
                let database_path = options
                    .database_path
                    .clone()
                    .unwrap_or_else(|| self.identity.database_path.to_string_lossy().into_owned());
                let network = options
                    .network
                    .clone()
                    .unwrap_or_else(|| self.identity.network.clone());
                let protocol = options
                    .protocol
                    .clone()
                    .unwrap_or_else(|| review_manager::default_protocol().to_owned());
                let tolerance = options
                    .tolerance
                    .unwrap_or(review_manager::DEFAULT_PROFIT_RELATIVE_TOLERANCE);
                let selection = match options
                    .selection
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .unwrap_or("newestPerEvaluableRoute")
                {
                    "newestPerEvaluableRoute" | "newest_per_evaluable_route" => {
                        review_manager::ReplaySelection::NewestPerEvaluableRoute
                    }
                    "newestPerRoute" | "newest_per_route" => {
                        review_manager::ReplaySelection::NewestPerRoute
                    }
                    "allAttempts" | "all_attempts" => review_manager::ReplaySelection::AllAttempts,
                    other => {
                        return Err(RuntimeCoreError::InvalidCommand(format!(
                            "review_parity_replay selection must be newestPerEvaluableRoute, newestPerRoute or allAttempts, got {other}"
                        )));
                    }
                };
                let report = review_manager::replay_recorded_routes_with(
                    &database_path,
                    &network,
                    self.identity.chain_id,
                    &protocol,
                    options.limit,
                    tolerance,
                    selection,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "review_manager",
                    message: error.to_string(),
                })?;
                let mut result = serde_json::to_value(report)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                let object = result.as_object_mut().ok_or_else(|| {
                    RuntimeCoreError::Serialization(
                        "review parity report must serialize as an object".to_owned(),
                    )
                })?;
                object.insert("accepted".to_owned(), Value::Bool(true));
                object.insert(
                    "command".to_owned(),
                    Value::String("review_parity_replay".to_owned()),
                );
                object.insert(
                    "version".to_owned(),
                    Value::String(RUNTIME_CORE_SCHEMA.to_owned()),
                );
                object.insert(
                    "requestId".to_owned(),
                    Value::String(request_id.trim().to_owned()),
                );
                object.insert(
                    "idempotencyKey".to_owned(),
                    Value::String(idempotency_key.trim().to_owned()),
                );
                object.insert(
                    "dataSource".to_owned(),
                    Value::String(DATA_SOURCE.to_owned()),
                );
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "review_parity_replay",
                            "dryRun": dry_run,
                            "routeCount": result.get("routeCount").cloned().unwrap_or(Value::Null),
                            "comparableCount": result.get("comparableCount").cloned().unwrap_or(Value::Null),
                            "deviationCount": result.get("deviationCount").cloned().unwrap_or(Value::Null),
                            "evidenceAvailable": result.get("evidenceAvailable").cloned().unwrap_or(Value::Null),
                            "evidenceComplete": result.get("evidenceComplete").cloned().unwrap_or(Value::Null),
                            "gatePassed": result.get("gatePassed").cloned().unwrap_or(Value::Null),
                            "gateReason": result.get("gateReason").cloned().unwrap_or(Value::Null),
                        }),
                    ),
                );
                Ok(result)
            }
            "clear_broadcasts" => {
                self.ensure_open()?;
                let record_store = if self.broadcast_authority.manager_apply_enabled() {
                    BroadcastRecordStore::Manager
                } else {
                    BroadcastRecordStore::Legacy
                };
                let outcome = clear_broadcast_records_write(
                    &self.identity.database_path,
                    BroadcastClearRequest {
                        network: self.identity.network.clone(),
                        chain_id: self.identity.chain_id,
                        operation: "broadcast.clear".to_owned(),
                        request_id: request_id.trim().to_owned(),
                        idempotency_key: idempotency_key.trim().to_owned(),
                        apply: !dry_run,
                        record_store,
                    },
                )
                .map_err(map_broadcast_command_error)?;
                let idempotent_replay = outcome.idempotent_replay;
                let mut result = serde_json::to_value(outcome)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                let object = result.as_object_mut().ok_or_else(|| {
                    RuntimeCoreError::Serialization(
                        "broadcast clear outcome must serialize as an object".to_owned(),
                    )
                })?;
                object.insert("accepted".to_owned(), Value::Bool(true));
                object.insert(
                    "command".to_owned(),
                    Value::String("clear_broadcasts".to_owned()),
                );
                object.insert(
                    "version".to_owned(),
                    Value::String(RUNTIME_CORE_SCHEMA.to_owned()),
                );
                object.insert(
                    "requestId".to_owned(),
                    Value::String(request_id.trim().to_owned()),
                );
                object.insert(
                    "idempotencyKey".to_owned(),
                    Value::String(idempotency_key.trim().to_owned()),
                );
                object.insert(
                    "dataSource".to_owned(),
                    Value::String(DATA_SOURCE.to_owned()),
                );
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "clear_broadcasts",
                            "dryRun": dry_run,
                            "idempotentReplay": idempotent_replay,
                            "clearedAttempts": result
                                .get("clearedAttempts")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                            "remainingAttempts": result
                                .get("remainingAttempts")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                        }),
                    ),
                );
                Ok(result)
            }
            "clear_reviews" => {
                self.ensure_open()?;
                // Review records are **regenerable runtime observations**, not
                // long-lived facts. The token and pool catalogs stay untouched;
                // only the per-chain review rows are dropped.
                let record_store = if self.broadcast_authority.manager_apply_enabled() {
                    ReviewRecordStore::Manager
                } else {
                    ReviewRecordStore::Legacy
                };
                let outcome = clear_review_records_write(
                    &self.identity.database_path,
                    ReviewClearRequest {
                        network: self.identity.network.clone(),
                        chain_id: self.identity.chain_id,
                        operation: "review.clear".to_owned(),
                        request_id: request_id.trim().to_owned(),
                        idempotency_key: idempotency_key.trim().to_owned(),
                        apply: !dry_run,
                        record_store,
                    },
                )
                .map_err(map_review_command_error)?;
                let idempotent_replay = outcome.idempotent_replay;
                let mut result = serde_json::to_value(outcome)
                    .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
                let object = result.as_object_mut().ok_or_else(|| {
                    RuntimeCoreError::Serialization(
                        "review clear outcome must serialize as an object".to_owned(),
                    )
                })?;
                object.insert("accepted".to_owned(), Value::Bool(true));
                object.insert(
                    "command".to_owned(),
                    Value::String("clear_reviews".to_owned()),
                );
                object.insert(
                    "version".to_owned(),
                    Value::String(RUNTIME_CORE_SCHEMA.to_owned()),
                );
                object.insert(
                    "requestId".to_owned(),
                    Value::String(request_id.trim().to_owned()),
                );
                object.insert(
                    "idempotencyKey".to_owned(),
                    Value::String(idempotency_key.trim().to_owned()),
                );
                object.insert(
                    "dataSource".to_owned(),
                    Value::String(DATA_SOURCE.to_owned()),
                );
                let mut state = write_state(&self.state);
                push_event(
                    &mut state.events,
                    new_event(
                        "runtime_command_completed",
                        Some(request_id.trim().to_owned()),
                        Some(idempotency_key.trim().to_owned()),
                        json!({
                            "command": "clear_reviews",
                            "dryRun": dry_run,
                            "idempotentReplay": idempotent_replay,
                            "clearedReviews": result
                                .get("clearedReviews")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                            "remainingReviews": result
                                .get("remainingReviews")
                                .cloned()
                                .unwrap_or_else(|| json!(0)),
                        }),
                    ),
                );
                Ok(result)
            }
            other => Err(RuntimeCoreError::InvalidCommand(format!(
                "unsupported command {other}"
            ))),
        }
    }

    pub fn manager_view(&self, view: &str, limit: usize) -> Result<Value, RuntimeCoreError> {
        self.manager_view_with_options(view, limit, None)
    }

    pub fn manager_view_with_options(
        &self,
        view: &str,
        limit: usize,
        options_json: Option<&str>,
    ) -> Result<Value, RuntimeCoreError> {
        self.ensure_open()?;
        let view = view.trim().to_ascii_lowercase();
        if !matches!(
            view.as_str(),
            "overview"
                | "candidate_edge"
                | "candidate_exchange"
                | "closed_loops"
                | "reviews"
                | "review_parity"
                | "broadcasts"
        ) {
            return Err(RuntimeCoreError::InvalidManagerView(format!(
                "unsupported view {view}; supported views: overview, candidate_edge, candidate_exchange, closed_loops, reviews, review_parity, broadcasts"
            )));
        }
        if limit == 0 || limit > 1_000 {
            return Err(RuntimeCoreError::InvalidManagerView(
                "limit must be between 1 and 1000".to_owned(),
            ));
        }
        let options = parse_manager_view_options(options_json)?;
        let allowed = match view.as_str() {
            "overview" => &[][..],
            "candidate_edge" | "candidate_exchange" | "closed_loops" => &["anchorTokenKeyId"][..],
            "reviews" => &["loopKey", "category", "status", "outcome", "offset"][..],
            "review_parity" => &["offset"][..],
            "broadcasts" => &["attemptId", "status", "offset"][..],
            _ => unreachable!(),
        };
        validate_manager_view_options(&options, &view, allowed)?;

        let data = match view.as_str() {
            "overview" => serde_json::to_value(
                read_manager_overview_snapshot(
                    &self.identity.database_path,
                    &self.identity.network,
                    self.identity.chain_id,
                    limit,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "composite_manager",
                    message: error.to_string(),
                })?,
            ),
            "candidate_edge" => serde_json::to_value(
                read_candidate_snapshot(
                    &self.identity.database_path,
                    &self.identity.network,
                    self.identity.chain_id,
                    optional_text(options.anchor_token_key_id.as_deref()),
                    limit,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "candidate_edge_pool_manager",
                    message: error.to_string(),
                })?,
            ),
            "candidate_exchange" => serde_json::to_value(
                read_candidate_exchange_snapshot(
                    &self.identity.database_path,
                    &self.identity.network,
                    self.identity.chain_id,
                    optional_text(options.anchor_token_key_id.as_deref()),
                    limit,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "candidate_exchange_pool_manager",
                    message: error.to_string(),
                })?,
            ),
            "closed_loops" => serde_json::to_value(
                read_closed_loop_snapshot(
                    &self.identity.database_path,
                    &self.identity.network,
                    self.identity.chain_id,
                    optional_text(options.anchor_token_key_id.as_deref()),
                    limit,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "closed_loop_manager",
                    message: error.to_string(),
                })?,
            ),
            "reviews" => {
                let offset = options.offset.unwrap_or(0);
                if offset.saturating_add(limit) > 1_000 {
                    return Err(RuntimeCoreError::InvalidManagerView(
                        "offset plus limit must not exceed 1000".to_owned(),
                    ));
                }
                if optional_text(options.loop_key.as_deref()).is_some() && offset != 0 {
                    return Err(RuntimeCoreError::InvalidManagerView(
                        "offset must be zero when loopKey is supplied".to_owned(),
                    ));
                }
                let outcome = match optional_text(options.outcome.as_deref()) {
                    None => None,
                    Some(value) => Some(ReviewOutcomeFilter::parse(value).ok_or_else(|| {
                        RuntimeCoreError::InvalidManagerView(
                            "outcome must be success or failed".to_owned(),
                        )
                    })?),
                };
                serde_json::to_value(
                    read_review_page(
                        &self.identity.database_path,
                        &self.identity.network,
                        self.identity.chain_id,
                        ReviewReadQuery {
                            loop_key: optional_text(options.loop_key.as_deref()),
                            category: optional_text(options.category.as_deref()),
                            review_status: optional_text(options.status.as_deref()),
                            outcome,
                            offset,
                            limit,
                        },
                    )
                    .map_err(|error| RuntimeCoreError::ManagerRead {
                        manager: "review_manager",
                        message: error.to_string(),
                    })?,
                )
            }
            "review_parity" => {
                let offset = options.offset.unwrap_or(0);
                if offset.saturating_add(limit) > 1_000 {
                    return Err(RuntimeCoreError::InvalidManagerView(
                        "offset plus limit must not exceed 1000".to_owned(),
                    ));
                }
                // The whole parity comparison lives in Review Manager: the
                // recorded reviews are its own table and the pool state comes
                // from Pool Manager through Pool Manager's published API.
                let report = replay_recorded_routes(
                    &self.identity.database_path,
                    &self.identity.network,
                    self.identity.chain_id,
                    review_manager::default_protocol(),
                    Some(limit),
                    review_manager::DEFAULT_PROFIT_RELATIVE_TOLERANCE,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "review_manager",
                    message: error.to_string(),
                })?;
                serde_json::to_value(report)
            }
            "broadcasts" => {
                let offset = options.offset.unwrap_or(0);
                if offset.saturating_add(limit) > 1_000 {
                    return Err(RuntimeCoreError::InvalidManagerView(
                        "offset plus limit must not exceed 1000".to_owned(),
                    ));
                }
                if optional_text(options.attempt_id.as_deref()).is_some() && offset != 0 {
                    return Err(RuntimeCoreError::InvalidManagerView(
                        "offset must be zero when attemptId is supplied".to_owned(),
                    ));
                }
                let mut snapshot = read_broadcast_snapshot_page(
                    &self.identity.database_path,
                    &self.identity.network,
                    self.identity.chain_id,
                    optional_text(options.attempt_id.as_deref()),
                    optional_text(options.status.as_deref()),
                    offset,
                    limit,
                )
                .map_err(|error| RuntimeCoreError::ManagerRead {
                    manager: "broadcast_manager",
                    message: error.to_string(),
                })?;
                let attempt_ids = snapshot
                    .attempts
                    .iter()
                    .map(|attempt| attempt.attempt_id.clone())
                    .collect::<HashSet<_>>();
                snapshot
                    .diagnostics
                    .retain(|diagnostic| attempt_ids.contains(&diagnostic.attempt_id));
                serde_json::to_value(snapshot)
            }
            _ => unreachable!(),
        }
        .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
        let generated_at = chrono::Utc::now().to_rfc3339();
        let state = read_state(&self.state);
        Ok(json!({
            "ok": true,
            "view": view,
            "version": RUNTIME_CORE_SCHEMA,
            "managerVersion": manager_contracts::MANAGER_DTO_VERSION,
            "generatedAt": generated_at,
            "chainId": self.identity.chain_id,
            "dataSource": DATA_SOURCE,
            "limit": limit,
            "data": data,
            "runtimeCore": {
                "chainId": self.identity.chain_id,
                "instanceId": self.instance_id,
                "network": self.identity.network,
                "protocol": self.identity.protocol,
                "databasePath": self.identity.database_path,
                "generation": state.generation,
                "snapshotAvailable": state.snapshot.is_some(),
                "snapshotChecksum": state.snapshot.as_ref().map(|snapshot| &snapshot.snapshot_checksum),
                "upstreamFingerprint": state.snapshot.as_ref().map(|snapshot| &snapshot.upstream_fingerprint),
            },
        }))
    }

    fn refresh_with_context(
        &self,
        request_id: Option<&str>,
        idempotency_key: Option<&str>,
    ) -> Result<Arc<RuntimeCoreSnapshot>, RuntimeCoreError> {
        self.ensure_open()?;
        let _refresh_guard = self
            .refresh_lock
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let next_generation = read_state(&self.state).generation.saturating_add(1);
        let result = read_components(&self.identity).and_then(|components| {
            build_snapshot(
                &self.instance_id,
                &self.identity,
                components,
                next_generation,
            )
        });
        let mut state = write_state(&self.state);
        match result {
            Ok(snapshot) => {
                let snapshot = Arc::new(snapshot);
                state.generation = snapshot.generation;
                state.snapshot = Some(Arc::clone(&snapshot));
                state.last_refresh_at = Some(snapshot.generated_at.clone());
                state.last_error = None;
                push_event(
                    &mut state.events,
                    new_event(
                        "snapshot_committed",
                        request_id.map(str::to_owned),
                        idempotency_key.map(str::to_owned),
                        json!({
                            "generation": snapshot.generation,
                            "snapshotChecksum": snapshot.snapshot_checksum,
                            "upstreamFingerprint": snapshot.upstream_fingerprint,
                            "chainComplete": snapshot.readiness.chain_complete,
                        }),
                    ),
                );
                Ok(snapshot)
            }
            Err(error) => {
                let message = error.to_string();
                state.last_error = Some(message.clone());
                let previous_generation = state.generation;
                push_event(
                    &mut state.events,
                    new_event(
                        "snapshot_rejected",
                        request_id.map(str::to_owned),
                        idempotency_key.map(str::to_owned),
                        json!({
                            "error": message,
                            "previousGeneration": previous_generation,
                        }),
                    ),
                );
                Err(error)
            }
        }
    }

    fn ensure_open(&self) -> Result<(), RuntimeCoreError> {
        if self.closed.load(Ordering::SeqCst) {
            return Err(RuntimeCoreError::Closed(self.identity.chain_id));
        }
        Ok(())
    }

    fn close_instance(&self) {
        self.closed.store(true, Ordering::SeqCst);
        let mut state = write_state(&self.state);
        push_event(
            &mut state.events,
            new_event(
                "runtime_closed",
                None,
                None,
                json!({"instanceId": self.instance_id}),
            ),
        );
    }
}

fn registry_lock() -> MutexGuard<'static, HashMap<i64, Arc<RuntimeCore>>> {
    RUNTIME_CORE_REGISTRY
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn read_state(state: &RwLock<RuntimeCoreState>) -> RwLockReadGuard<'_, RuntimeCoreState> {
    state
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn write_state(state: &RwLock<RuntimeCoreState>) -> RwLockWriteGuard<'_, RuntimeCoreState> {
    state
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn read_components(identity: &RuntimeIdentity) -> Result<RuntimeCoreComponents, RuntimeCoreError> {
    let mut token_libraries = BTreeMap::new();
    for library in TOKEN_LIBRARIES {
        let state = match read_library_state(
            &identity.database_path,
            &identity.network,
            identity.chain_id as u64,
            library,
        ) {
            Ok(state) => Some(state),
            Err(TokenQueryError::LibraryStateMissing { .. }) => None,
            Err(error) => {
                return Err(RuntimeCoreError::ManagerRead {
                    manager: "token_manager",
                    message: error.to_string(),
                })
            }
        };
        token_libraries.insert(library.as_str().to_owned(), state);
    }
    let pool_scope = read_scope_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
        SCOPE_KEY,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "pool_manager",
        message: error.to_string(),
    })?;
    let directional_pool = read_directional_pool_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
        SCOPE_KEY,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "directional_pool_manager",
        message: error.to_string(),
    })?;
    let candidate_edge = read_candidate_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "candidate_edge_pool_manager",
        message: error.to_string(),
    })?;
    let candidate_exchange = read_candidate_exchange_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "candidate_exchange_pool_manager",
        message: error.to_string(),
    })?;
    let closed_loop = read_closed_loop_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "closed_loop_manager",
        message: error.to_string(),
    })?;
    let review = read_review_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "review_manager",
        message: error.to_string(),
    })?;
    let broadcast = read_broadcast_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "broadcast_manager",
        message: error.to_string(),
    })?;
    Ok(RuntimeCoreComponents {
        token_libraries,
        pool_scope,
        directional_pool,
        candidate_edge,
        candidate_exchange,
        closed_loop,
        review,
        broadcast,
    })
}

fn build_snapshot(
    instance_id: &str,
    identity: &RuntimeIdentity,
    components: RuntimeCoreComponents,
    generation: u64,
) -> Result<RuntimeCoreSnapshot, RuntimeCoreError> {
    let pipeline = read_pipeline_status(
        &identity.database_path,
        &identity.network,
        identity.chain_id,
    )
    .map_err(|error| RuntimeCoreError::ManagerRead {
        manager: "composite_manager",
        message: error.to_string(),
    })?;
    let component_upstream = upstream_snapshot_from_components(&components);
    let component_fingerprint = checksum_json(&component_upstream)?;
    let mut issues = pipeline.upstream_assessment.issues.clone();
    if component_fingerprint != pipeline.upstream_assessment.fingerprint {
        issues.push(ConsistencyIssue {
            component: "runtime_core".to_owned(),
            field: "upstreamFingerprint".to_owned(),
            expected: pipeline.upstream_assessment.fingerprint.clone(),
            actual: component_fingerprint,
        });
    }
    if !pipeline.upstream_assessment.consistent || !issues.is_empty() {
        return Err(RuntimeCoreError::InconsistentSnapshot(issues));
    }

    let readiness = build_readiness(&components, pipeline.enabled);
    let upstream_versions = component_versions(&components);
    let checksums = component_checksums(&components);
    let generated_at = chrono::Utc::now().to_rfc3339();
    let mut snapshot = RuntimeCoreSnapshot {
        schema_version: RUNTIME_CORE_SCHEMA.to_owned(),
        instance_id: instance_id.to_owned(),
        chain_id: identity.chain_id,
        network: identity.network.clone(),
        protocol: identity.protocol.clone(),
        database_path: identity.database_path.to_string_lossy().into_owned(),
        generated_at,
        generation,
        data_source: DATA_SOURCE.to_owned(),
        upstream_versions,
        checksums,
        upstream_fingerprint: pipeline.upstream_assessment.fingerprint.clone(),
        snapshot_checksum: String::new(),
        readiness,
        components,
        pipeline,
    };
    snapshot.snapshot_checksum = snapshot_checksum(&snapshot)?;
    Ok(snapshot)
}

fn upstream_snapshot_from_components(components: &RuntimeCoreComponents) -> UpstreamSnapshot {
    let token_libraries = TOKEN_LIBRARIES
        .into_iter()
        .map(|library| {
            let key = library.as_str().to_owned();
            let reference = components
                .token_libraries
                .get(&key)
                .and_then(Option::as_ref)
                .map(|state| TokenLibraryRef {
                    enabled: true,
                    version: state.library_version,
                    checksum: state.metadata_checksum.clone(),
                    token_count: usize_from_i64(state.token_count),
                    admitted_count: usize_from_i64(state.admitted_count),
                })
                .unwrap_or(TokenLibraryRef {
                    enabled: false,
                    version: 0,
                    checksum: String::new(),
                    token_count: 0,
                    admitted_count: 0,
                });
            (key, reference)
        })
        .collect::<BTreeMap<_, _>>();
    UpstreamSnapshot {
        token_libraries,
        pool_scope: PoolScopeRef {
            enabled: components.pool_scope.enabled,
            materialized_checksum: components.pool_scope.materialized_checksum.clone(),
            live_checksum: components.pool_scope.live_checksum.clone(),
            library_versions: components.pool_scope.library_versions.clone(),
            library_checksums: components.pool_scope.library_checksums.clone(),
            active_pool_count: components.pool_scope.active_pool_count,
        },
        directional_pool: DirectionalPoolRef {
            enabled: components.directional_pool.enabled,
            version: components.directional_pool.directional_pool_version,
            checksum: components.directional_pool.materialized_checksum.clone(),
            pool_scope_version: components.directional_pool.pool_scope_version,
            pool_scope_checksum: components.directional_pool.pool_scope_checksum.clone(),
            snapshot_checksum: components.directional_pool.snapshot_checksum.clone(),
            base_pool_count: components.directional_pool.base_pool_count,
            directional_pool_count: components.directional_pool.directional_pool_count,
            direction0_count: components.directional_pool.direction0_count,
            direction1_count: components.directional_pool.direction1_count,
            excluded_pool_count: components.directional_pool.excluded_pool_count,
        },
        candidate_edge: CandidateEdgeRef {
            enabled: components.candidate_edge.enabled,
            version: components.candidate_edge.candidate_version,
            checksum: components.candidate_edge.materialized_checksum.clone(),
            pool_scope_checksum: components.candidate_edge.pool_scope_checksum.clone(),
            library_versions: components.candidate_edge.library_versions.clone(),
            library_checksums: components.candidate_edge.library_checksums.clone(),
            anchor_count: components.candidate_edge.anchor_token_count,
            pool_count: components.candidate_edge.eligible_pool_count,
        },
        candidate_exchange: CandidateExchangeRef {
            enabled: components.candidate_exchange.enabled,
            version: components.candidate_exchange.observation_version,
            checksum: components.candidate_exchange.materialized_checksum.clone(),
            candidate_edge_version: components.candidate_exchange.candidate_edge_version,
            candidate_edge_checksum: components
                .candidate_exchange
                .candidate_edge_checksum
                .clone(),
            pool_scope_checksum: components.candidate_exchange.pool_scope_checksum.clone(),
            library_versions: components.candidate_exchange.library_versions.clone(),
            library_checksums: components.candidate_exchange.library_checksums.clone(),
            direction_count: components.candidate_exchange.direction_count,
            pool_count: components.candidate_exchange.pool_count,
        },
        closed_loop: ClosedLoopRef {
            enabled: components.closed_loop.enabled,
            version: components.closed_loop.closed_loop_version,
            checksum: components.closed_loop.materialized_checksum.clone(),
            candidate_edge_version: components.closed_loop.candidate_edge_version,
            candidate_edge_checksum: components.closed_loop.candidate_edge_checksum.clone(),
            observation_version: components.closed_loop.observation_version,
            observation_checksum: components.closed_loop.observation_checksum.clone(),
            pool_scope_checksum: components.closed_loop.pool_scope_checksum.clone(),
            loop_count: components.closed_loop.loop_count,
            consumers_need_rebuild: components.closed_loop.consumers_need_rebuild,
        },
        review: ReviewRef {
            enabled: components.review.enabled,
            version: components.review.review_version,
            checksum: components.review.materialized_checksum.clone(),
            closed_loop_version: components.review.closed_loop_version,
            closed_loop_checksum: components.review.closed_loop_checksum.clone(),
            completed_count: components.review.completed_count,
            data_incomplete_count: components.review.data_incomplete_count,
            broadcast_request_count: components.review.broadcast_request_count,
        },
        broadcast: BroadcastRef {
            enabled: components.broadcast.enabled,
            version: components.broadcast.broadcast_version,
            checksum: components.broadcast.materialized_checksum.clone(),
            review_version: components.broadcast.review_version,
            review_checksum: components.broadcast.review_checksum.clone(),
            closed_loop_version: components.broadcast.closed_loop_version,
            closed_loop_checksum: components.broadcast.closed_loop_checksum.clone(),
            pending_count: components.broadcast.pending_count,
            success_count: components.broadcast.success_count,
            failed_count: components.broadcast.failed_count,
            terminal_count: components.broadcast.terminal_count,
        },
    }
}

fn build_readiness(components: &RuntimeCoreComponents, pipeline_enabled: bool) -> RuntimeReadiness {
    let token_libraries_present = TOKEN_LIBRARIES.iter().all(|library| {
        components
            .token_libraries
            .get(library.as_str())
            .is_some_and(Option::is_some)
    });
    let pool_scope_enabled = components.pool_scope.enabled;
    let candidate_edge_enabled = components.candidate_edge.enabled;
    let candidate_exchange_enabled = components.candidate_exchange.enabled;
    let closed_loop_enabled = components.closed_loop.enabled;
    let review_enabled = components.review.enabled;
    let broadcast_enabled = components.broadcast.enabled;
    RuntimeReadiness {
        token_libraries_present,
        pool_scope_enabled,
        candidate_edge_enabled,
        candidate_exchange_enabled,
        closed_loop_enabled,
        review_enabled,
        broadcast_enabled,
        pipeline_enabled,
        chain_complete: token_libraries_present
            && pool_scope_enabled
            && candidate_edge_enabled
            && candidate_exchange_enabled
            && closed_loop_enabled
            && review_enabled
            && broadcast_enabled,
    }
}

fn component_versions(components: &RuntimeCoreComponents) -> BTreeMap<String, Option<i64>> {
    let mut versions = BTreeMap::new();
    for library in TOKEN_LIBRARIES {
        versions.insert(
            format!("token.{}", library.as_str()),
            components
                .token_libraries
                .get(library.as_str())
                .and_then(Option::as_ref)
                .map(|state| state.library_version),
        );
    }
    versions.insert(
        "poolScope".to_owned(),
        components.pool_scope.enabled.then_some(0),
    );
    versions.insert(
        "directionalPool".to_owned(),
        components
            .directional_pool
            .enabled
            .then_some(components.directional_pool.directional_pool_version),
    );
    versions.insert(
        "candidateEdge".to_owned(),
        components
            .candidate_edge
            .enabled
            .then_some(components.candidate_edge.candidate_version),
    );
    versions.insert(
        "candidateExchange".to_owned(),
        components
            .candidate_exchange
            .enabled
            .then_some(components.candidate_exchange.observation_version),
    );
    versions.insert(
        "closedLoop".to_owned(),
        components
            .closed_loop
            .enabled
            .then_some(components.closed_loop.closed_loop_version),
    );
    versions.insert(
        "review".to_owned(),
        components
            .review
            .enabled
            .then_some(components.review.review_version),
    );
    versions.insert(
        "broadcast".to_owned(),
        components
            .broadcast
            .enabled
            .then_some(components.broadcast.broadcast_version),
    );
    versions
}

fn component_checksums(components: &RuntimeCoreComponents) -> BTreeMap<String, Option<String>> {
    let mut checksums = BTreeMap::new();
    for library in TOKEN_LIBRARIES {
        checksums.insert(
            format!("token.{}", library.as_str()),
            components
                .token_libraries
                .get(library.as_str())
                .and_then(Option::as_ref)
                .map(|state| state.metadata_checksum.clone()),
        );
    }
    checksums.insert(
        "poolScope".to_owned(),
        components
            .pool_scope
            .materialized_checksum
            .clone()
            .or_else(|| Some(components.pool_scope.live_checksum.clone())),
    );
    checksums.insert(
        "directionalPool".to_owned(),
        components.directional_pool.materialized_checksum.clone(),
    );
    checksums.insert(
        "candidateEdge".to_owned(),
        components.candidate_edge.materialized_checksum.clone(),
    );
    checksums.insert(
        "candidateExchange".to_owned(),
        components.candidate_exchange.materialized_checksum.clone(),
    );
    checksums.insert(
        "closedLoop".to_owned(),
        components.closed_loop.materialized_checksum.clone(),
    );
    checksums.insert(
        "review".to_owned(),
        components.review.materialized_checksum.clone(),
    );
    checksums.insert(
        "broadcast".to_owned(),
        components.broadcast.materialized_checksum.clone(),
    );
    checksums
}

fn snapshot_checksum(snapshot: &RuntimeCoreSnapshot) -> Result<String, RuntimeCoreError> {
    checksum_json(&json!({
        "schemaVersion": snapshot.schema_version,
        "instanceId": snapshot.instance_id,
        "chainId": snapshot.chain_id,
        "network": snapshot.network,
        "protocol": snapshot.protocol,
        "databasePath": snapshot.database_path,
        "generation": snapshot.generation,
        "upstreamFingerprint": snapshot.upstream_fingerprint,
        "upstreamVersions": snapshot.upstream_versions,
        "checksums": snapshot.checksums,
        "readiness": snapshot.readiness,
    }))
}

fn checksum_json<T: Serialize>(value: &T) -> Result<String, RuntimeCoreError> {
    let bytes = serde_json::to_vec(value)
        .map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
    Ok(format!("sha256:{}", hex::encode(Sha256::digest(bytes))))
}

fn usize_from_i64(value: i64) -> usize {
    usize::try_from(value.max(0)).unwrap_or(usize::MAX)
}

fn new_event(
    event_type: impl Into<String>,
    request_id: Option<String>,
    idempotency_key: Option<String>,
    detail: Value,
) -> RuntimeCoreEvent {
    let sequence = RUNTIME_EVENT_SEQUENCE.fetch_add(1, Ordering::SeqCst);
    RuntimeCoreEvent {
        event_id: format!("runtime-event-{sequence}"),
        event_type: event_type.into(),
        request_id,
        idempotency_key,
        detail,
        created_at: chrono::Utc::now().to_rfc3339(),
    }
}

fn push_event(events: &mut VecDeque<RuntimeCoreEvent>, event: RuntimeCoreEvent) {
    while events.len() >= EVENT_LIMIT {
        events.pop_front();
    }
    events.push_back(event);
}

fn parse_manager_view_options(
    options_json: Option<&str>,
) -> Result<ManagerViewOptions, RuntimeCoreError> {
    match options_json
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None => Ok(ManagerViewOptions::default()),
        Some(value) => serde_json::from_str(value).map_err(|error| {
            RuntimeCoreError::InvalidManagerView(format!("options must be a JSON object: {error}"))
        }),
    }
}

fn parse_review_parity_replay_options(
    options_json: Option<&str>,
) -> Result<ReviewParityReplayOptions, RuntimeCoreError> {
    match options_json
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None => Ok(ReviewParityReplayOptions::default()),
        Some(value) => serde_json::from_str(value).map_err(|error| {
            RuntimeCoreError::InvalidCommand(format!(
                "review_parity_replay options must be a JSON object: {error}"
            ))
        }),
    }
}

fn parse_review_parity_compare_options(
    options_json: Option<&str>,
) -> Result<ReviewParityCompareOptions, RuntimeCoreError> {
    match options_json
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None => Ok(ReviewParityCompareOptions::default()),
        Some(value) => serde_json::from_str(value).map_err(|error| {
            RuntimeCoreError::InvalidCommand(format!(
                "review_parity_compare options must be a JSON object: {error}"
            ))
        }),
    }
}

fn parse_review_parity_replay_payload_options(
    options_json: Option<&str>,
) -> Result<ReviewParityReplayPayloadOptions, RuntimeCoreError> {
    match options_json
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None => Ok(ReviewParityReplayPayloadOptions::default()),
        Some(value) => serde_json::from_str(value).map_err(|error| {
            RuntimeCoreError::InvalidCommand(format!(
                "review_parity_replay_payload options must be a JSON object: {error}"
            ))
        }),
    }
}

/// The options of the `pool_measurement_population` command.
#[derive(Debug, Default)]
struct PoolMeasurementPopulationOptions {
    network: Option<String>,
    protocol: Option<String>,
    chain_id: Option<i64>,
    limit: Option<u64>,
}

fn parse_pool_measurement_population_options(
    options_json: Option<&str>,
) -> Result<PoolMeasurementPopulationOptions, RuntimeCoreError> {
    let Some(text) = options_json else {
        return Ok(PoolMeasurementPopulationOptions::default());
    };
    let value: Value = serde_json::from_str(text).map_err(|error| {
        RuntimeCoreError::Serialization(format!(
            "pool_measurement_population options must be a JSON object: {error}"
        ))
    })?;
    let object = value.as_object().ok_or_else(|| {
        RuntimeCoreError::Serialization(
            "pool_measurement_population options must be a JSON object".to_owned(),
        )
    })?;
    Ok(PoolMeasurementPopulationOptions {
        network: object
            .get("network")
            .and_then(Value::as_str)
            .map(str::to_owned),
        protocol: object
            .get("protocol")
            .and_then(Value::as_str)
            .map(str::to_owned),
        chain_id: object.get("chainId").and_then(Value::as_i64),
        limit: object.get("limit").and_then(Value::as_u64),
    })
}

fn parse_pool_roles_manage_options(
    options_json: Option<&str>,
) -> Result<PoolRolesManageOptions, RuntimeCoreError> {
    let Some(text) = options_json else {
        return Ok(PoolRolesManageOptions::default());
    };
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Ok(PoolRolesManageOptions::default());
    }
    serde_json::from_str(trimmed).map_err(|error| {
        RuntimeCoreError::InvalidCommand(format!(
            "pool_roles_manage options must be a JSON object: {error}"
        ))
    })
}

fn parse_review_shadow_compare_options(
    options_json: Option<&str>,
) -> Result<ReviewShadowCompareOptions, RuntimeCoreError> {
    match options_json
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None => Ok(ReviewShadowCompareOptions::default()),
        Some(value) => serde_json::from_str(value).map_err(|error| {
            RuntimeCoreError::InvalidCommand(format!(
                "review_shadow_compare options must be a JSON object: {error}"
            ))
        }),
    }
}

fn parse_pool_resolve_keys_options(
    options_json: Option<&str>,
) -> Result<PoolResolveKeysOptions, RuntimeCoreError> {
    match options_json
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        None => Ok(PoolResolveKeysOptions::default()),
        Some(value) => serde_json::from_str(value).map_err(|error| {
            RuntimeCoreError::InvalidCommand(format!(
                "pool_resolve_keys options must be a JSON object: {error}"
            ))
        }),
    }
}

/// Parses a raw token amount that may arrive as a decimal string or as a
/// `0x`-prefixed hexadecimal string.
///
/// The compatibility layer sends amounts as strings because a raw token
/// amount does not always fit a JavaScript number. A **negative** amount is
/// meaningful: the chain reports a losing route as a negative profit, and the
/// sign sits in front of the radix prefix (`-0x...`), so the sign has to be
/// read before the radix is stripped.
fn parse_signed_raw_option(value: Option<&str>) -> Option<i128> {
    let text = value?.trim();
    if text.is_empty() {
        return None;
    }
    let (negative, digits) = match text.strip_prefix('-') {
        Some(rest) => (true, rest.trim()),
        None => (false, text),
    };
    let magnitude = if let Some(hex) = digits
        .strip_prefix("0x")
        .or_else(|| digits.strip_prefix("0X"))
    {
        i128::from_str_radix(hex, 16).ok()?
    } else {
        digits.parse::<i128>().ok()?
    };
    if negative {
        Some(-magnitude)
    } else {
        Some(magnitude)
    }
}

fn validate_manager_view_options(
    options: &ManagerViewOptions,
    view: &str,
    allowed: &[&str],
) -> Result<(), RuntimeCoreError> {
    let supplied = [
        ("anchorTokenKeyId", options.anchor_token_key_id.is_some()),
        ("loopKey", options.loop_key.is_some()),
        ("category", options.category.is_some()),
        ("status", options.status.is_some()),
        ("outcome", options.outcome.is_some()),
        ("attemptId", options.attempt_id.is_some()),
        ("offset", options.offset.is_some()),
    ];
    if let Some((field, _)) = supplied
        .into_iter()
        .find(|(field, present)| *present && !allowed.contains(field))
    {
        return Err(RuntimeCoreError::InvalidManagerView(format!(
            "{field} is not supported by view {view}"
        )));
    }
    Ok(())
}

fn optional_text(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

pub(crate) fn validate_command_identity(
    request_id: &str,
    idempotency_key: &str,
) -> Result<(), RuntimeCoreError> {
    if request_id.trim().is_empty() {
        return Err(RuntimeCoreError::InvalidCommand(
            "requestId is required".to_owned(),
        ));
    }
    if idempotency_key.trim().is_empty() {
        return Err(RuntimeCoreError::InvalidCommand(
            "idempotencyKey is required".to_owned(),
        ));
    }
    Ok(())
}

fn map_candidate_command_error(error: CandidateEdgeError) -> RuntimeCoreError {
    let (code, http_status) = match &error {
        CandidateEdgeError::InvalidRequest(_) => ("INVALID_REQUEST", 400),
        CandidateEdgeError::ExpectedUpstreamMismatch { .. } => ("UPSTREAM_VERSION_CHANGED", 409),
        CandidateEdgeError::IdempotencyKeyReused { .. } => ("IDEMPOTENCY_KEY_REUSED", 409),
        CandidateEdgeError::MissingPoolScope
        | CandidateEdgeError::MissingCoreLibraryState
        | CandidateEdgeError::NoAdmittedCoreTokens => ("TOKEN_NOT_ADMITTED", 409),
        CandidateEdgeError::InvalidStoredPool(_) => ("POOL_INVALID", 409),
        CandidateEdgeError::Sqlite(_) | CandidateEdgeError::ComparisonRead { .. } => {
            ("UPSTREAM_UNAVAILABLE", 503)
        }
        CandidateEdgeError::Json(_) => ("INTERNAL_ERROR", 500),
    };
    RuntimeCoreError::ManagerCommand {
        manager: "candidate_edge_pool_manager",
        command: "rebuild_candidate_edges",
        code,
        http_status,
        message: error.to_string(),
    }
}

fn map_candidate_exchange_command_error(error: CandidateExchangeError) -> RuntimeCoreError {
    let (code, http_status) = match &error {
        CandidateExchangeError::InvalidRequest(_) => ("INVALID_REQUEST", 400),
        CandidateExchangeError::ExpectedUpstreamMismatch { .. } => {
            ("UPSTREAM_VERSION_CHANGED", 409)
        }
        CandidateExchangeError::IdempotencyKeyReused { .. } => ("IDEMPOTENCY_KEY_REUSED", 409),
        CandidateExchangeError::MissingCandidateEdgeState
        | CandidateExchangeError::MissingPoolScope => ("TOKEN_NOT_ADMITTED", 409),
        CandidateExchangeError::InvalidStoredPool(_) => ("POOL_INVALID", 409),
        CandidateExchangeError::Sqlite(_)
        | CandidateExchangeError::CandidateEdgeRead(_)
        | CandidateExchangeError::PoolScopeRead(_)
        | CandidateExchangeError::ComparisonRead { .. } => ("UPSTREAM_UNAVAILABLE", 503),
        CandidateExchangeError::Json(_) => ("INTERNAL_ERROR", 500),
    };
    RuntimeCoreError::ManagerCommand {
        manager: "candidate_exchange_pool_manager",
        command: "rebuild_candidate_exchange_pools",
        code,
        http_status,
        message: error.to_string(),
    }
}

fn map_review_command_error(error: review_manager::ReviewError) -> RuntimeCoreError {
    let (code, http_status) = match &error {
        review_manager::ReviewError::InvalidRequest(_)
        | review_manager::ReviewError::DuplicateEvidence(_)
        | review_manager::ReviewError::DuplicateDiagnostic { .. } => ("INVALID_REQUEST", 400),
        review_manager::ReviewError::IdempotencyKeyReused { .. } => ("IDEMPOTENCY_KEY_REUSED", 409),
        review_manager::ReviewError::ExpectedUpstreamMismatch { .. } => {
            ("UPSTREAM_VERSION_CHANGED", 409)
        }
        review_manager::ReviewError::MissingClosedLoopState
        | review_manager::ReviewError::ClosedLoopSnapshotTruncated { .. } => {
            ("TOKEN_NOT_ADMITTED", 409)
        }
        review_manager::ReviewError::Sqlite(_) | review_manager::ReviewError::ClosedLoopRead(_) => {
            ("UPSTREAM_UNAVAILABLE", 503)
        }
        review_manager::ReviewError::Json(_) => ("INTERNAL_ERROR", 500),
        review_manager::ReviewError::UnknownClosedLoop(_) => ("REVIEW_ROUTE_NOT_FOUND", 404),
    };
    RuntimeCoreError::ManagerCommand {
        manager: "review_manager",
        command: "clear_reviews",
        code,
        http_status,
        message: error.to_string(),
    }
}

fn map_broadcast_command_error(error: BroadcastError) -> RuntimeCoreError {
    let (code, http_status) = match &error {
        BroadcastError::InvalidRequest(_)
        | BroadcastError::DuplicateAttemptInput(_)
        | BroadcastError::DuplicateAttempt(_)
        | BroadcastError::DuplicateDiagnostic { .. } => ("INVALID_REQUEST", 400),
        BroadcastError::IdempotencyKeyReused { .. } => ("IDEMPOTENCY_KEY_REUSED", 409),
        BroadcastError::ExpectedUpstreamMismatch { .. } => ("UPSTREAM_VERSION_CHANGED", 409),
        BroadcastError::UnknownRequest(_)
        | BroadcastError::BroadcastRequestNotEligible { .. }
        | BroadcastError::BroadcastRequestNotAdmitted { .. }
        | BroadcastError::AttemptNotFound(_)
        | BroadcastError::ReviewRowMissing { .. }
        | BroadcastError::AttemptAlreadyTerminal { .. } => ("BROADCAST_REQUEST_NOT_ELIGIBLE", 409),
        BroadcastError::MissingReviewState | BroadcastError::ReviewSnapshotTruncated { .. } => {
            ("TOKEN_NOT_ADMITTED", 409)
        }
        BroadcastError::ReviewRead(_) | BroadcastError::Sqlite(_) => ("UPSTREAM_UNAVAILABLE", 503),
        BroadcastError::Json(_) => ("INTERNAL_ERROR", 500),
    };
    RuntimeCoreError::ManagerCommand {
        manager: "broadcast_manager",
        command: "clear_broadcasts",
        code,
        http_status,
        message: error.to_string(),
    }
}

/// The runtime maintenance entries.
///
/// The maintenance chain owns **when** a layer is rebuilt; it does not own
/// **how**.  Both rebuilds therefore live in the manager that owns the
/// layer, and the runtime reaches them through these two functions.  A
/// caller that rebuilt the layer itself would own a second copy of the
/// admission, ranking and folding rules, and the two copies would drift
/// the first time a rule changed.
///
/// Both entries return the manager's own receipt.  The receipt is the
/// contract: the caller reads the numbers the manager published, stores
/// them and reports them, and it never recomputes them.
pub fn rebuild_directional_pools(
    database_path: &str,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<Value, RuntimeCoreError> {
    let receipt = materialize_directional_pools_for_chain(
        database_path,
        network,
        chain_id,
        apply,
    )
    .map_err(map_directional_command_error)?;
    let mut value =
        serde_json::to_value(receipt).map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
    if let Some(object) = value.as_object_mut() {
        object.insert("ok".to_owned(), Value::Bool(true));
        object.insert(
            "manager".to_owned(),
            Value::String("directional_pool_manager".to_owned()),
        );
    }
    Ok(value)
}

/// Rebuild the closed loop layer of one chain.
///
/// The closed loop layer is built from the candidate edge, the
/// observation and the **directional** pool layers, so this entry is only
/// meaningful once the directional layer has been materialized.
pub fn rebuild_closed_loops(
    database_path: &str,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<Value, RuntimeCoreError> {
    let receipt = materialize_closed_loops(database_path, network, chain_id, apply)
        .map_err(map_closed_loop_command_error)?;
    let mut value =
        serde_json::to_value(receipt).map_err(|error| RuntimeCoreError::Serialization(error.to_string()))?;
    if let Some(object) = value.as_object_mut() {
        object.insert("ok".to_owned(), Value::Bool(true));
        object.insert(
            "manager".to_owned(),
            Value::String("closed_loop_manager".to_owned()),
        );
    }
    Ok(value)
}

fn map_directional_command_error(error: DirectionalPoolError) -> RuntimeCoreError {
    let (code, http_status) = match &error {
        DirectionalPoolError::InvalidRequest(_) => ("INVALID_REQUEST", 400),
        DirectionalPoolError::MissingPoolScope(_)
        | DirectionalPoolError::UnsupportedPoolTable(_) => ("POOL_SCOPE_UNAVAILABLE", 409),
        DirectionalPoolError::MissingSnapshotTable => ("SNAPSHOT_TABLE_MISSING", 409),
        DirectionalPoolError::IdempotencyKeyReused { .. } => ("IDEMPOTENCY_KEY_REUSED", 409),
        DirectionalPoolError::Sqlite(_) => ("UPSTREAM_UNAVAILABLE", 503),
        DirectionalPoolError::Json(_) => ("INTERNAL_ERROR", 500),
    };
    RuntimeCoreError::ManagerCommand {
        manager: "directional_pool_manager",
        command: "rebuild_directional_pools",
        code,
        http_status,
        message: error.to_string(),
    }
}

fn map_closed_loop_command_error(error: ClosedLoopError) -> RuntimeCoreError {
    let (code, http_status) = match &error {
        ClosedLoopError::InvalidRequest(_) => ("INVALID_REQUEST", 400),
        // The store has no manager-side upstream at all.  That is a
        // different fact from "the rebuild broke": the closed loop layer
        // simply is not part of this store, and the caller falls back to
        // the legacy path instead of treating it as a failure.
        ClosedLoopError::MissingCandidateEdgeState | ClosedLoopError::MissingObservationState => {
            ("UPSTREAM_NOT_MATERIALIZED", 409)
        }
        ClosedLoopError::InvalidStoredTopology(_)
        | ClosedLoopError::UpstreamInconsistent { .. }
        | ClosedLoopError::ExpectedUpstreamMismatch { .. } => ("UPSTREAM_VERSION_CHANGED", 409),
        ClosedLoopError::IdempotencyKeyReused { .. } => ("IDEMPOTENCY_KEY_REUSED", 409),
        ClosedLoopError::ComparisonRead { .. } | ClosedLoopError::Sqlite(_) => {
            ("UPSTREAM_UNAVAILABLE", 503)
        }
        ClosedLoopError::Json(_) => ("INTERNAL_ERROR", 500),
    };
    RuntimeCoreError::ManagerCommand {
        manager: "closed_loop_manager",
        command: "rebuild_closed_loops",
        code,
        http_status,
        message: error.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::{RuntimeCore, RuntimeCoreError};
    use rusqlite::{params, Connection};
    use std::path::{Path, PathBuf};
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn database() -> (tempfile::TempDir, PathBuf) {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).expect("manager schema");
        (directory, path)
    }

    fn insert_library_state(path: &Path, version: i64, checksum: &str) {
        let connection = Connection::open(path).expect("database");
        for library_key in ["core", "stocks", "extension", "test"] {
            connection
                .execute(
                    "INSERT INTO token_library_state (
                        network, chain_id, library_key, library_version, token_count,
                        admitted_count, metadata_checksum, source_checkpoint,
                        generated_at, updated_at
                     ) VALUES (?1, ?2, ?3, ?4, 0, 0, ?5, NULL, ?6, ?6)",
                    params![
                        "robinhood",
                        4663_i64,
                        library_key,
                        version,
                        checksum,
                        "2026-11-06T00:00:00Z"
                    ],
                )
                .expect("token state");
        }
    }

    fn insert_scope_state(path: &Path, core_version: i64) {
        let connection = Connection::open(path).expect("database");
        connection
            .execute(
                "INSERT INTO pool_scope_config (
                    network, chain_id, scope_key, pool_table, library_keys_json,
                    library_versions_json, library_checksums_json, filter_mode,
                    excluded_token_key_ids_json, enabled, materialized_checksum,
                    last_rebuilt_at, created_at, updated_at
                 ) VALUES (
                    ?1, ?2, 'core4', 'v4_exchange_pools_core4', '[\"core\"]',
                    ?3, '{\"core\":\"token-v1\"}', 'edge_any', '[]', 1,
                    'scope-v1', ?4, ?4, ?4
                 )",
                params![
                    "robinhood",
                    4663_i64,
                    format!("{{\"core\":{core_version}}}"),
                    "2026-11-06T00:00:00Z"
                ],
            )
            .expect("scope state");
    }

    fn insert_core_anchor(path: &Path) {
        let connection = Connection::open(path).expect("database");
        let address = "0x1111111111111111111111111111111111111111";
        connection
            .execute(
                "INSERT INTO token_core (
                    network, chain_id, token_key_id, v4_currency_key,
                    token_address, token_symbol, token_name, token_decimals,
                    token_kind, is_native, is_wrapped_native, is_stable,
                    admission_status, admission_source, metadata_source,
                    metadata_checksum, source_json, first_seen_at,
                    first_admitted_at, updated_at, blocked_reason
                 ) VALUES (
                    'robinhood', 4663, ?1, ?2, ?2, 'USDG', 'USDG', 18,
                    'erc20', 0, 0, 0, 'admitted', 'fixture', 'fixture',
                    'sha256:fixture', '{}', '2026-11-06T00:00:00Z',
                    '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z', NULL
                 )",
                params![format!("robinhood:4663:{address}"), address],
            )
            .expect("core anchor token");
    }

    fn insert_candidate_exchange_upstream(path: &Path) {
        let anchor = "0x2222222222222222222222222222222222222222";
        let stock = "0x4444444444444444444444444444444444444444";
        let connection = Connection::open(path).expect("database");
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pools (
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
                 );",
            )
            .expect("candidate exchange business tables");
        for (address, symbol, table) in [
            (anchor, "USDG", "token_core"),
            (stock, "AMZN", "token_stocks"),
        ] {
            let sql = format!(
                "INSERT INTO {table} (
                    network, chain_id, token_key_id, v4_currency_key,
                    token_address, token_symbol, token_name, token_decimals,
                    token_kind, is_native, is_wrapped_native, is_stable,
                    admission_status, admission_source, metadata_source,
                    metadata_checksum, source_json, first_seen_at,
                    first_admitted_at, updated_at, blocked_reason
                 ) VALUES (
                    'robinhood', 4663, ?1, ?1, ?1, ?2, ?2, 18,
                    'erc20', 0, 0, 0, 'admitted', 'fixture', 'fixture',
                    'sha256:fixture', '{{}}', '2026-11-06T00:00:00Z',
                    '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z', NULL
                 )"
            );
            connection
                .execute(&sql, params![format!("robinhood:4663:{address}"), symbol])
                .expect("candidate exchange token");
        }
        for (library_key, version, token_count, admitted_count) in [
            ("core", 3, 1, 1),
            ("stocks", 2, 1, 1),
            ("extension", 1, 0, 0),
            ("test", 1, 0, 0),
        ] {
            connection
                .execute(
                    "INSERT INTO token_library_state (
                        network, chain_id, library_key, library_version,
                        token_count, admitted_count, metadata_checksum,
                        source_checkpoint, generated_at, updated_at
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'fixture', ?8, ?8)",
                    params![
                        "robinhood",
                        4663_i64,
                        library_key,
                        version,
                        token_count,
                        admitted_count,
                        format!("sha256:{library_key}-fixture"),
                        "2026-11-06T00:00:00Z"
                    ],
                )
                .expect("candidate exchange library state");
        }
        drop(connection);
        pool_manager::materialize_scope(path, "robinhood", 4663, "candidate_exchange", true)
            .expect("candidate exchange pool scope");

        let connection = Connection::open(path).expect("database");
        connection
            .execute(
                "INSERT INTO manager_dex_candidate_library_state (
                    network, chain_id, protocol, scope_key, candidate_version,
                    materialized_checksum, pool_scope_checksum,
                    pool_library_versions_json, pool_library_checksums_json,
                    library_versions_json, library_checksums_json,
                    anchor_token_count, candidate_token_count, eligible_pool_count,
                    recommendation_count, abnormal_pool_count, generated_at, updated_at
                 ) VALUES (
                    'robinhood', 4663, 'uniswap_v4', 'core4', 7,
                    'sha256:candidate-edge-fixture', 'sha256:core4-fixture',
                    '{}', '{}', '{}', '{}', 1, 2, 0, 0, 0,
                    '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z'
                 )",
                [],
            )
            .expect("candidate edge state");
        for (address, library_key, symbol) in [(anchor, "core", "USDG"), (stock, "stocks", "AMZN")]
        {
            connection
                .execute(
                    "INSERT INTO manager_dex_candidate_anchor_tokens (
                        network, chain_id, protocol, scope_key, anchor_token_key_id,
                        token_key_id, token_role, anchor_library_key,
                        token_library_key, token_symbol, pool_count,
                        yc_primary_pool_id, zc_primary_pool_id,
                        yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                        yc_zc_same_set, created_at, updated_at
                     ) VALUES (
                        'robinhood', 4663, 'uniswap_v4', 'core4', ?1, ?2,
                        'yc_zc', 'core', ?3, ?4, 0, NULL, NULL, '[]', '[]', 1,
                        'fixture', 'fixture'
                     )",
                    params![
                        format!("robinhood:4663:{anchor}"),
                        format!("robinhood:4663:{address}"),
                        library_key,
                        symbol
                    ],
                )
                .expect("candidate anchor token");
        }
    }

    fn insert_broadcast_attempt(path: &Path, attempt_id: &str, status: &str) {
        let connection = Connection::open(path).expect("database");
        connection
            .execute(
                "INSERT INTO manager_cow_execution_attempts (
                    network, chain_id, protocol, scope_key, attempt_id, loop_key,
                    closed_loop_version, closed_loop_checksum, review_version,
                    review_checksum, anchor_token_key_id, path_text,
                    path_addresses_json, path_symbols_json, pool_path_key,
                    pool_ids_json, signed_pnl_raw, signed_pnl_amount,
                    signed_pnl_currency, signed_pnl_address, signed_pnl_unit,
                    signed_pnl_source, signer_address, nonce, gas_limit,
                    gas_price_wei, max_fee_per_gas_wei, max_priority_fee_per_gas_wei,
                    rpc_url, rpc_name, tx_hash, status, status_reason, receipt_json,
                    evidence_json, created_at, updated_at, terminal_at
                 ) VALUES (
                    'robinhood', 4663, 'uniswap_v4', 'core4', ?1, 'loop-1',
                    1, 'closed-v1', 1, 'review-v1', 'token-core-usdg',
                    'USDG > ETH > USDG', '[\"0x1\",\"0x2\"]', '[\"USDG\",\"ETH\"]',
                    'pool-path-v1', '[\"pool-1\",\"pool-2\"]', '1', 1.0,
                    'USDG', '0x1', 'token', 'review_manager', '0xsigner', 42,
                    210000, '1', '2', '1', 'https://rpc.example.invalid', 'fixture',
                    NULL, ?2, NULL, '{}', '{}', '2026-11-06T00:00:00Z',
                    '2026-11-06T00:00:00Z', NULL
                 )",
                params![attempt_id, status],
            )
            .expect("broadcast attempt");
        connection
            .execute(
                "INSERT INTO manager_broadcast_attempt_diagnostics (
                    network, chain_id, protocol, scope_key, attempt_id, loop_key,
                    diagnostic_id, diagnostic_type, diagnostic_code, message,
                    rpc_url, tx_hash, payload_json, observed_at
                 ) VALUES (
                    'robinhood', 4663, 'uniswap_v4', 'core4', ?1, 'loop-1',
                    'diagnostic-1', 'rpc_unavailable', 'fixture', 'fixture',
                    'https://rpc.example.invalid', NULL, '{}', '2026-11-06T00:00:00Z'
                 )",
                params![attempt_id],
            )
            .expect("broadcast diagnostic");
    }

    #[test]
    fn register_is_unique_per_chain_and_does_not_register_during_lookup() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        let first = RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4")
            .expect("first core");
        let same = RuntimeCore::register(4663, "robinhood", path, "uniswap_v4").expect("same core");
        assert!(std::sync::Arc::ptr_eq(&first, &same));
        assert_eq!(first.status().state, "registered");
        assert_eq!(first.status().recent_events.len(), 1);
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn register_rejects_candidate_exchange_authority_identity_conflict() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        RuntimeCore::register_with_all_authorities(
            4663,
            "robinhood",
            path.clone(),
            "uniswap_v4",
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Manager,
        )
        .expect("first core");

        let error = RuntimeCore::register_with_all_authorities(
            4663,
            "robinhood",
            path,
            "uniswap_v4",
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Legacy,
        )
        .expect_err("candidate exchange authority mismatch must not reuse the instance");
        assert!(matches!(
            &error,
            RuntimeCoreError::AlreadyRegistered {
                existing_candidate_exchange_authority,
                requested_candidate_exchange_authority,
                ..
            } if existing_candidate_exchange_authority == "manager"
                && requested_candidate_exchange_authority == "legacy"
        ));
        assert_eq!(error.code(), "RUNTIME_CORE_IDENTITY_CONFLICT");
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn snapshot_commits_as_one_generation_and_preserves_previous_on_version_error() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        insert_library_state(&path, 1, "token-v1");
        insert_scope_state(&path, 1);
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");
        let first = core.refresh().expect("first snapshot");
        assert_eq!(first.generation, 1);
        assert_eq!(first.data_source, "rust_runtime_core");
        assert_eq!(first.chain_id, 4663);

        let connection = Connection::open(&path).expect("database");
        connection
            .execute(
                "UPDATE token_library_state
                 SET library_version = 2, metadata_checksum = 'token-v2'
                 WHERE network = 'robinhood' AND chain_id = 4663",
                [],
            )
            .expect("version drift");
        let error = core.refresh().expect_err("mixed snapshot rejected");
        assert!(matches!(error, RuntimeCoreError::InconsistentSnapshot(_)));
        let state = core.state().expect("old snapshot retained");
        assert_eq!(state.generation, 1);
        assert_eq!(state.checksums["token.core"], Some("token-v1".to_owned()));
        assert_eq!(core.status().state, "partial");
        assert!(core.status().last_error.is_some());
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn command_requires_request_and_idempotency_keys() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        let core = RuntimeCore::register(4663, "robinhood", path, "uniswap_v4").expect("core");
        assert!(matches!(
            core.execute_command("refresh_snapshot", "", "idem", true),
            Err(RuntimeCoreError::InvalidCommand(_))
        ));
        assert!(matches!(
            core.execute_command("unsupported", "request", "idem", false),
            Err(RuntimeCoreError::InvalidCommand(_))
        ));
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn rebuild_candidate_edges_command_is_dry_run_safe_idempotent_and_authority_gated() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        insert_library_state(&path, 1, "token-v1");
        insert_scope_state(&path, 1);
        insert_core_anchor(&path);
        let core = RuntimeCore::register_with_authorities(
            4663,
            "robinhood",
            path.clone(),
            "uniswap_v4",
            manager_contracts::TokenAuthorityMode::Manager,
            manager_contracts::TokenAuthorityMode::Legacy,
        )
        .expect("core");

        let dry = core
            .execute_command(
                "rebuild_candidate_edges",
                "candidate-dry",
                "candidate-dry-key",
                true,
            )
            .expect("dry candidate rebuild");
        assert_eq!(dry["command"], "rebuild_candidate_edges");
        assert_eq!(dry["dataSource"], "rust_runtime_core");
        assert_eq!(dry["chainId"], 4663);
        assert_eq!(dry["dryRun"], true);
        assert_eq!(dry["idempotentReplay"], false);
        // The anchor count describes the anchor groups that were actually
        // materialized. The fixture has one admitted core token but no
        // pool for it, so no anchor group is realized and the count is
        // zero. A count taken from the catalog instead would claim an
        // anchor group that no reader can find in the anchor rows.
        assert_eq!(dry["anchorTokenCount"], 0);

        let connection = Connection::open(&path).expect("database");
        let writes: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .expect("write request count");
        assert_eq!(writes, 0);

        let applied = core
            .execute_command(
                "rebuild_candidate_edges",
                "candidate-apply",
                "candidate-apply-key",
                false,
            )
            .expect("apply candidate rebuild");
        assert_eq!(applied["dryRun"], false);
        assert_eq!(applied["idempotentReplay"], false);
        assert_eq!(applied["candidateVersion"], 1);
        assert!(applied["materializedChecksum"]
            .as_str()
            .is_some_and(|value| value.starts_with("sha256:")));

        let replay = core
            .execute_command(
                "rebuild_candidate_edges",
                "candidate-replay",
                "candidate-apply-key",
                false,
            )
            .expect("replay candidate rebuild");
        assert_eq!(replay["idempotentReplay"], true);
        assert_eq!(replay["candidateVersion"], applied["candidateVersion"]);
        assert_eq!(
            replay["materializedChecksum"],
            applied["materializedChecksum"]
        );
        RuntimeCore::close(4663).expect("close");

        let legacy = RuntimeCore::register(4663, "robinhood", path, "uniswap_v4").expect("core");
        let error = legacy
            .execute_command(
                "rebuild_candidate_edges",
                "candidate-legacy",
                "candidate-legacy-key",
                false,
            )
            .expect_err("legacy candidate authority must reject apply");
        assert!(matches!(
            &error,
            RuntimeCoreError::AuthorityDenied {
                command: "rebuild_candidate_edges",
                authority: manager_contracts::TokenAuthorityMode::Legacy,
            }
        ));
        assert_eq!(error.code(), "CANDIDATE_AUTHORITY_WRITE_DENIED");
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn candidate_exchange_command_is_dry_run_safe_idempotent_and_authority_gated() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        insert_candidate_exchange_upstream(&path);
        let core = RuntimeCore::register_with_all_authorities(
            4663,
            "robinhood",
            path.clone(),
            "uniswap_v4",
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Manager,
        )
        .expect("core");

        let dry = core
            .execute_command(
                "rebuild_candidate_exchange_pools",
                "exchange-dry",
                "exchange-dry-key",
                true,
            )
            .expect("dry candidate exchange rebuild");
        assert_eq!(dry["command"], "rebuild_candidate_exchange_pools");
        assert_eq!(dry["dataSource"], "rust_runtime_core");
        assert_eq!(dry["chainId"], 4663);
        assert_eq!(dry["dryRun"], true);
        assert_eq!(dry["idempotentReplay"], false);
        assert_eq!(dry["anchorCount"], 1);
        assert_eq!(dry["tokenCount"], 2);

        let connection = Connection::open(&path).expect("database");
        let writes: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .expect("write request count");
        assert_eq!(writes, 0);

        let applied = core
            .execute_command(
                "rebuild_candidate_exchange_pools",
                "exchange-apply",
                "exchange-apply-key",
                false,
            )
            .expect("apply candidate exchange rebuild");
        assert_eq!(applied["dryRun"], false);
        assert_eq!(applied["idempotentReplay"], false);
        assert_eq!(applied["observationVersion"], 1);
        assert!(applied["materializedChecksum"]
            .as_str()
            .is_some_and(|value| value.starts_with("sha256:")));

        let replay = core
            .execute_command(
                "rebuild_candidate_exchange_pools",
                "exchange-replay",
                "exchange-apply-key",
                false,
            )
            .expect("replay candidate exchange rebuild");
        assert_eq!(replay["idempotentReplay"], true);
        assert_eq!(replay["observationVersion"], applied["observationVersion"]);
        assert_eq!(
            replay["materializedChecksum"],
            applied["materializedChecksum"]
        );
        RuntimeCore::close(4663).expect("close");

        let legacy = RuntimeCore::register_with_all_authorities(
            4663,
            "robinhood",
            path,
            "uniswap_v4",
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Legacy,
            manager_contracts::TokenAuthorityMode::Legacy,
        )
        .expect("core");
        let error = legacy
            .execute_command(
                "rebuild_candidate_exchange_pools",
                "exchange-legacy",
                "exchange-legacy-key",
                false,
            )
            .expect_err("legacy candidate exchange authority must reject apply");
        assert!(matches!(
            &error,
            RuntimeCoreError::AuthorityDenied {
                command: "rebuild_candidate_exchange_pools",
                authority: manager_contracts::TokenAuthorityMode::Legacy,
            }
        ));
        assert_eq!(error.code(), "CANDIDATE_EXCHANGE_AUTHORITY_WRITE_DENIED");
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn clear_broadcasts_command_is_dry_run_safe_idempotent_and_rejects_pending_work() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        insert_broadcast_attempt(&path, "attempt-terminal", "success");
        let core = RuntimeCore::register_with_authority(
            4663,
            "robinhood",
            path.clone(),
            "uniswap_v4",
            manager_contracts::TokenAuthorityMode::Manager,
        )
        .expect("core");

        let dry = core
            .execute_command("clear_broadcasts", "clear-dry", "clear-key", true)
            .expect("dry clear");
        assert_eq!(dry["command"], "clear_broadcasts");
        assert_eq!(dry["dataSource"], "rust_runtime_core");
        assert_eq!(dry["chainId"], 4663);
        assert_eq!(dry["dryRun"], true);
        assert_eq!(dry["clearedAttempts"], 1);
        assert_eq!(dry["clearedDiagnostics"], 1);
        assert_eq!(dry["remainingAttempts"], 1);
        assert_eq!(dry["idempotentReplay"], false);

        let connection = Connection::open(&path).expect("database");
        let attempts: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts",
                [],
                |row| row.get(0),
            )
            .expect("attempt count");
        let writes: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .expect("write request count");
        assert_eq!(attempts, 1);
        assert_eq!(writes, 0);

        let applied = core
            .execute_command("clear_broadcasts", "clear-apply", "clear-key", false)
            .expect("apply clear");
        assert_eq!(applied["clearedAttempts"], 1);
        assert_eq!(applied["remainingAttempts"], 0);
        assert_eq!(applied["idempotentReplay"], false);

        let replay = core
            .execute_command("clear_broadcasts", "clear-replay", "clear-key", false)
            .expect("replay clear");
        assert_eq!(replay["clearedAttempts"], applied["clearedAttempts"]);
        assert_eq!(replay["remainingAttempts"], applied["remainingAttempts"]);
        assert_eq!(replay["idempotentReplay"], true);

        insert_broadcast_attempt(&path, "attempt-pending", "pending");
        let pending = core
            .execute_command(
                "clear_broadcasts",
                "clear-pending",
                "clear-pending-key",
                false,
            )
            .expect_err("pending work must reject clear");
        assert!(matches!(
            pending,
            RuntimeCoreError::ManagerCommand {
                code: "INVALID_REQUEST",
                http_status: 400,
                ..
            }
        ));
        let attempts: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts",
                [],
                |row| row.get(0),
            )
            .expect("attempt count");
        assert_eq!(attempts, 1);
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn clear_broadcasts_command_uses_legacy_store_under_legacy_authority() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        let connection = Connection::open(&path).expect("database");
        connection
            .execute_batch(
                "CREATE TABLE cow_execution_attempts (
                id INTEGER PRIMARY KEY,
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                category TEXT NOT NULL
            );
            INSERT INTO cow_execution_attempts(network,chain_id,category)
            VALUES ('robinhood',4663,'execution_success');",
            )
            .expect("legacy attempts");
        drop(connection);
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");

        let dry = core
            .execute_command("clear_broadcasts", "clear-dry", "clear-dry-key", true)
            .expect("dry run remains available");
        assert_eq!(dry["clearedAttempts"], 1);
        assert_eq!(dry["recordStore"], "legacy");
        let applied = core
            .execute_command("clear_broadcasts", "clear-apply", "clear-apply-key", false)
            .expect("legacy authority clears the authoritative legacy table");
        assert_eq!(applied["clearedAttempts"], 1);
        assert_eq!(applied["remainingAttempts"], 0);
        let connection = Connection::open(path).expect("database");
        let attempts: i64 = connection
            .query_row("SELECT COUNT(*) FROM cow_execution_attempts", [], |row| {
                row.get(0)
            })
            .expect("attempt count");
        assert_eq!(attempts, 0);
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn clear_reviews_command_uses_legacy_store_and_preserves_catalogs() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        let connection = Connection::open(&path).expect("database");
        connection
            .execute_batch(
                "CREATE TABLE cow_execution_reviews (
                id INTEGER PRIMARY KEY,
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                loop_key TEXT NOT NULL
            );
            CREATE TABLE chain_tokens (
                id INTEGER PRIMARY KEY,
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                address TEXT NOT NULL
            );
            CREATE TABLE v4_exchange_pools (
                id INTEGER PRIMARY KEY,
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                pool_key TEXT NOT NULL
            );
            INSERT INTO cow_execution_reviews(network,chain_id,loop_key)
            VALUES ('robinhood',4663,'loop-a'), ('robinhood',4663,'loop-b'),
                   ('other',1,'loop-c');
            INSERT INTO chain_tokens(network,chain_id,address)
            VALUES ('robinhood',4663,'0xtoken');
            INSERT INTO v4_exchange_pools(network,chain_id,pool_key)
            VALUES ('robinhood',4663,'0xpool');",
            )
            .expect("legacy fixtures");
        drop(connection);
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");

        let dry = core
            .execute_command("clear_reviews", "review-dry", "review-dry-key", true)
            .expect("dry run remains available");
        assert_eq!(dry["clearedReviews"], 2);
        assert_eq!(dry["remainingReviews"], 2);
        assert_eq!(dry["recordStore"], "legacy");
        assert_eq!(dry["dryRun"], true);

        let applied = core
            .execute_command("clear_reviews", "review-apply", "review-apply-key", false)
            .expect("legacy authority clears the authoritative legacy table");
        assert_eq!(applied["clearedReviews"], 2);
        assert_eq!(applied["remainingReviews"], 0);

        let connection = Connection::open(path).expect("database");
        let reviews: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM cow_execution_reviews WHERE network = 'robinhood' AND chain_id = 4663",
                [],
                |row| row.get(0),
            )
            .expect("review count");
        assert_eq!(reviews, 0);
        let other_scope: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM cow_execution_reviews WHERE network <> 'robinhood'",
                [],
                |row| row.get(0),
            )
            .expect("other scope count");
        assert_eq!(other_scope, 1);
        let tokens: i64 = connection
            .query_row("SELECT COUNT(*) FROM chain_tokens", [], |row| row.get(0))
            .expect("token count");
        assert_eq!(tokens, 1);
        let pools: i64 = connection
            .query_row("SELECT COUNT(*) FROM v4_exchange_pools", [], |row| {
                row.get(0)
            })
            .expect("pool count");
        assert_eq!(pools, 1);
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn registration_status_never_accesses_business_database() {
        let _guard = TEST_LOCK.lock().unwrap();
        let core = RuntimeCore::register(
            4663,
            "robinhood",
            "E:/path/that/does/not/exist.sqlite3",
            "uniswap_v4",
        )
        .expect("core registered without database access");
        let status = core.status();
        assert_eq!(status.state, "registered");
        assert!(!status.snapshot_available);
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn manager_view_reads_overview_without_side_effects_and_rejects_unknown_views() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        insert_library_state(&path, 1, "token-v1");
        insert_scope_state(&path, 1);
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");

        let value = core.manager_view("Overview", 100).expect("overview view");
        assert_eq!(value["data"]["chainId"], 4663);
        assert_eq!(value["data"]["source"], "manager");
        assert_eq!(value["dataSource"], "rust_runtime_core");
        assert_eq!(
            value["managerVersion"],
            manager_contracts::MANAGER_DTO_VERSION
        );
        assert_eq!(value["runtimeCore"]["chainId"], 4663);
        assert!(value["data"]["versions"]["versionsConsistent"].is_boolean());

        let candidate = core
            .manager_view_with_options("candidate_edge", 25, None)
            .expect("candidate view");
        assert_eq!(candidate["view"], "candidate_edge");
        assert_eq!(candidate["data"]["status"]["chainId"], 4663);
        assert_eq!(candidate["limit"], 25);

        let reviews = core
            .manager_view_with_options("reviews", 20, Some(r#"{"offset":0}"#))
            .expect("review view");
        assert_eq!(reviews["data"]["offset"], 0);
        assert_eq!(reviews["data"]["limit"], 20);

        assert!(matches!(
            core.manager_view("unknown", 100),
            Err(RuntimeCoreError::InvalidManagerView(_))
        ));
        assert!(matches!(
            core.manager_view("overview", 0),
            Err(RuntimeCoreError::InvalidManagerView(_))
        ));
        assert!(matches!(
            core.manager_view_with_options("candidate_edge", 100, Some(r#"{"status":"failed"}"#)),
            Err(RuntimeCoreError::InvalidManagerView(_))
        ));
        assert!(matches!(
            core.manager_view_with_options("reviews", 100, Some("{")),
            Err(RuntimeCoreError::InvalidManagerView(_))
        ));

        let connection = Connection::open(path).expect("database");
        let quick_check: String = connection
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .expect("quick check");
        assert_eq!(quick_check, "ok");
        let write_requests: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .expect("write request count");
        assert_eq!(write_requests, 0);
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn refresh_only_reads_manager_tables() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        insert_library_state(&path, 1, "token-v1");
        insert_scope_state(&path, 1);
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");
        let snapshot = core.refresh().expect("snapshot");
        assert_eq!(snapshot.readiness.token_libraries_present, true);

        let connection = Connection::open(&path).expect("database");
        let quick_check: String = connection
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .expect("quick check");
        assert_eq!(quick_check, "ok");
        let write_requests: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .expect("write request count");
        assert_eq!(write_requests, 0);
        RuntimeCore::close(4663).expect("close");
    }

    /// Builds one live review payload that carries the pool state the review
    /// quoted.
    ///
    /// The payload is the *only* input the non-persistent parity replay
    /// accepts, so the fixture has to be shaped exactly like the payload the
    /// live review path produces.
    fn live_review_payload() -> serde_json::Value {
        serde_json::json!({
            "routeAddresses": [
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ],
            "poolPathKey": "pool-a|pool-b",
            "selectedAmountRaw": "1000000",
            "selectedProfitRaw": "25",
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "pool-a",
                        "token0": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "token1": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000000000000000000000000000000000000000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidity": "1000000000000000000000000"
                    },
                    {
                        "poolId": "pool-b",
                        "token0": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "token1": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000000000000000000000000000000000000000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidity": "1000000000000000000000000"
                    }
                ]
            }
        })
    }

    #[test]
    fn review_parity_replay_payload_is_read_only_and_uses_the_review_time_state() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");

        let payload = live_review_payload();
        let options = serde_json::json!({
            "payload": payload,
            "flashFeeBps": 0,
            "tolerance": 1e-9,
        });
        let result = core
            .execute_command_with_options(
                "review_parity_replay_payload",
                "parity-payload-1",
                "parity-payload-1-key",
                true,
                Some(&options.to_string()),
            )
            .expect("live parity payload replay");

        assert_eq!(result["accepted"], true);
        assert_eq!(result["command"], "review_parity_replay_payload");
        assert_eq!(result["persisted"], false);
        assert_eq!(result["dryRun"], true);
        assert_eq!(result["data"]["reviewTimeStateUsed"], true);
        assert_eq!(result["data"]["reviewTimePoolCount"], 2);
        // The review-time state is authoritative, so the route stays
        // comparable even though the database holds no pool snapshot and no
        // review row at all.
        assert_ne!(result["data"]["state"], "not_comparable");
        assert_eq!(result["data"]["profitOnchainRaw"], 25);

        // The command is read-only: it wrote no review row and no manager
        // write request.
        let connection = Connection::open(&path).expect("database");
        let quick_check: String = connection
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .expect("quick check");
        assert_eq!(quick_check, "ok");
        let write_requests: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .expect("write request count");
        assert_eq!(write_requests, 0);
        RuntimeCore::close(4663).expect("close");
    }

    #[test]
    fn review_parity_replay_payload_refuses_a_missing_payload_or_review_time_state() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");

        // A missing payload is refused outright.
        let missing = core
            .execute_command_with_options(
                "review_parity_replay_payload",
                "parity-payload-missing",
                "parity-payload-missing-key",
                true,
                Some("{}"),
            )
            .expect_err("missing payload must fail closed");
        assert!(matches!(&missing, RuntimeCoreError::InvalidCommand(_)));
        assert!(missing.to_string().contains("requires payload"));

        // A payload without the pool state the review quoted is refused too:
        // replaying it against a later snapshot would compare two different
        // markets and could never reproduce the recorded answer.
        let mut payload = live_review_payload();
        payload["quote"]["candidate_pools"] = serde_json::json!([]);
        let options = serde_json::json!({ "payload": payload });
        let no_state = core
            .execute_command_with_options(
                "review_parity_replay_payload",
                "parity-payload-no-state",
                "parity-payload-no-state-key",
                true,
                Some(&options.to_string()),
            )
            .expect_err("missing review-time state must fail closed");
        assert!(matches!(&no_state, RuntimeCoreError::ManagerRead { .. }));
        assert!(no_state.to_string().contains("review-time pool state"));

        let connection = Connection::open(&path).expect("database");
        let write_requests: i64 = connection
            .query_row("SELECT COUNT(*) FROM manager_write_requests", [], |row| {
                row.get(0)
            })
            .expect("write request count");
        assert_eq!(write_requests, 0);
        RuntimeCore::close(4663).expect("close");
    }
}
