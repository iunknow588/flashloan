use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, RwLock, RwLockReadGuard, RwLockWriteGuard};

use broadcast_manager::{
    clear_broadcast_records_write, read_broadcast_snapshot_page, read_broadcast_status,
    BroadcastClearRequest, BroadcastError, BroadcastStatus,
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
use closed_loop_manager::{read_closed_loop_snapshot, read_closed_loop_status, ClosedLoopStatus};
use composite_manager::{
    read_manager_overview_snapshot, read_pipeline_status, BroadcastRef, CandidateEdgeRef,
    CandidateExchangeRef, ClosedLoopRef, ConsistencyIssue, PipelineSnapshot, PoolScopeRef,
    ReviewRef, TokenLibraryRef, UpstreamSnapshot,
};
use manager_contracts::TokenAuthorityMode;
use pool_manager::{read_scope_status, ScopeStatus};
use review_manager::{
    read_review_page, read_review_status, ReviewOutcomeFilter, ReviewReadQuery, ReviewStatus,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use token_manager::{read_library_state, LibraryKey, TokenLibraryState, TokenQueryError};

const RUNTIME_CORE_SCHEMA: &str = "rust_runtime_core_v1";
const DATA_SOURCE: &str = "rust_runtime_core";
const SCOPE_KEY: &str = "core4";
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

    pub fn execute_command(
        &self,
        command: &str,
        request_id: &str,
        idempotency_key: &str,
        dry_run: bool,
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
            "clear_broadcasts" => {
                self.ensure_open()?;
                if !dry_run && !self.broadcast_authority.manager_apply_enabled() {
                    return Err(RuntimeCoreError::AuthorityDenied {
                        command: "clear_broadcasts",
                        authority: self.broadcast_authority,
                    });
                }
                let outcome = clear_broadcast_records_write(
                    &self.identity.database_path,
                    BroadcastClearRequest {
                        network: self.identity.network.clone(),
                        chain_id: self.identity.chain_id,
                        operation: "broadcast.clear".to_owned(),
                        request_id: request_id.trim().to_owned(),
                        idempotency_key: idempotency_key.trim().to_owned(),
                        apply: !dry_run,
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
                | "broadcasts"
        ) {
            return Err(RuntimeCoreError::InvalidManagerView(format!(
                "unsupported view {view}; supported views: overview, candidate_edge, candidate_exchange, closed_loops, reviews, broadcasts"
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
        | BroadcastError::AttemptNotFound(_)
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
        assert_eq!(dry["anchorTokenCount"], 1);

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
    fn clear_broadcasts_command_apply_requires_broadcast_manager_authority() {
        let _guard = TEST_LOCK.lock().unwrap();
        let (_directory, path) = database();
        insert_broadcast_attempt(&path, "attempt-terminal", "success");
        let core =
            RuntimeCore::register(4663, "robinhood", path.clone(), "uniswap_v4").expect("core");

        let dry = core
            .execute_command("clear_broadcasts", "clear-dry", "clear-dry-key", true)
            .expect("dry run remains available");
        assert_eq!(dry["clearedAttempts"], 1);
        let error = core
            .execute_command("clear_broadcasts", "clear-apply", "clear-apply-key", false)
            .expect_err("legacy authority must reject apply");
        assert!(matches!(
            error,
            RuntimeCoreError::AuthorityDenied {
                command: "clear_broadcasts",
                authority: manager_contracts::TokenAuthorityMode::Legacy,
            }
        ));
        let connection = Connection::open(path).expect("database");
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
}
