//! The production-side review publish chain.
//!
//! Why this module exists
//! ----------------------
//!
//! The Review Manager owns the review library, and the library only ever
//! held **one** generation: the closed-loop catalog of the chain at version
//! 14, materialized by a legacy converter that wrote executable=false for
//! every route it converted.  The Rust runtime is the only layer that
//! actually measures a route, and it never published a review at all, so
//! the two execution gates of the Review Manager never ran and the
//! broadcast request count stayed at zero.
//!
//! This module is the missing link.  It does three things and nothing else:
//!
//! 1. It turns the payload of a live review into the evidence the Review
//!    Manager consumes.
//! 2. It collects that evidence across the review groups of a chain until
//!    it covers the **whole** closed-loop catalog.  The Review Manager
//!    replaces the review set of the scope with the evidence it receives,
//!    so a partial set would erase the reviews of every loop it does not
//!    mention.
//! 3. It publishes the generation through the Review Manager's own write
//!    API, with the upstream versions taken from the closed-loop Manager
//!    and an idempotency key that differs for every round.
//!
//! What it deliberately does **not** do
//! -----------------------------------
//!
//! * It does not decide whether a route is executable.  The executable
//!   flag is the kernel's own contract verdict and is **copied**, never
//!   invented: a route the chain did not accept can never reach a gate,
//!   and the Review Manager's two gates stay fail-closed.
//! * It does not relax a threshold, a capacity or a profit rule.
//! * It does not publish a partial generation.
//! * It does not clear consumers_need_rebuild.  That flag belongs to the
//!   closed-loop Manager's own self-healing path.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{json, Value};

use closed_loop_manager::ClosedLoopStatus;
use review_manager::{
    ClosedLoopIdentityEvidence, ExpectedUpstream, ReviewDiagnosticInput, ReviewDiagnosticType,
    ReviewEvidenceInput, ReviewWriteRequest, RunArbitrageEvidence, SignedPnl,
};

use crate::closed_loop_identity::{ClosedLoopCatalog, ClosedLoopUpstream};

/// The operation a production review publication is filed under.
pub const REVIEW_PUBLISH_OPERATION: &str = "review_publish";

/// The maximum number of closed loops one review generation may carry.
///
/// The Review Manager reads the closed-loop catalog through a snapshot, and
/// the snapshot itself is capped.  The cap here is the same one, so the
/// publisher can never ask for more loops than the Manager is willing to
/// serve.
const MAX_REVIEW_LOOPS: usize = 1_000;

/// The unit the signed profit is published in.
const SIGNED_PNL_UNIT: &str = "raw";

/// The source the signed profit is published with.
const SIGNED_PNL_SOURCE: &str = "native_rust_ladder";

/// The keys the Review Manager reads the final input amount from.
///
/// The raw hexadecimal form is the canonical one and the decimal form is
/// the fallback.  The producer copies every one of them, so the Review
/// Manager finds the amount under whichever spelling the kernel published.
const FINAL_AMOUNT_KEYS: [&str; 6] = [
    "selectedAmountRaw",
    "initialAmountRaw",
    "amountInRaw",
    "amountRaw",
    "selectedAmount",
    "initialAmount",
];

/// The keys of the kernel's own run evidence the Review Manager owns.
///
/// They are read from the typed fields of the evidence, so they must not
/// travel a second time inside the flattened remainder.
const RESERVED_RUN_KEYS: [&str; 5] = [
    "ok",
    "executable",
    "blockNumber",
    "signedPnl",
    "diagnostics",
];

/// Why one review generation could not be published.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReviewPublishError {
    /// The closed-loop state could not be read.
    ClosedLoopState(String),
    /// The evidence does not cover the whole closed-loop catalog.
    Coverage(CoverageGap),
    /// The upstream generation moved after the evidence was measured.
    UpstreamMoved {
        /// The generation the evidence was measured under.
        measured: ClosedLoopUpstream,
        /// The generation the closed-loop Manager publishes now.
        current: ClosedLoopUpstream,
    },
    /// The Review Manager rejected the write.
    Manager(String),
}

impl fmt::Display for ReviewPublishError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ClosedLoopState(message) => {
                write!(formatter, "the closed-loop state is unusable: {message}")
            }
            Self::Coverage(gap) => write!(formatter, "{gap}"),
            Self::UpstreamMoved { measured, current } => write!(
                formatter,
                "the upstream generation moved: the evidence was measured under \
                 version {} and the manager publishes version {}",
                measured.closed_loop_version, current.closed_loop_version
            ),
            Self::Manager(message) => {
                write!(formatter, "the review manager rejected the write: {message}")
            }
        }
    }
}

impl std::error::Error for ReviewPublishError {}

/// The difference between the closed-loop catalog and the evidence.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CoverageGap {
    /// How many closed loops the catalog holds.
    pub expected: usize,
    /// How many evidence blocks the publication carried.
    pub actual: usize,
    /// The catalog keys no evidence block named.
    pub missing: Vec<String>,
    /// The keys an evidence block named that the catalog does not hold.
    pub unknown: Vec<String>,
}

impl fmt::Display for CoverageGap {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "the evidence covers {} of {} closed loops ({} missing, {} unknown)",
            self.actual,
            self.expected,
            self.missing.len(),
            self.unknown.len()
        )
    }
}

/// What one publication did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewPublishOutcome {
    /// Whether the generation was written.
    pub published: bool,
    /// How many closed loops the catalog holds.
    pub loop_count: usize,
    /// How many evidence blocks the generation carried.
    pub evidence_count: usize,
    /// How many reviews the generation completed.
    pub completed_count: usize,
    /// How many reviews the generation filed as incomplete.
    pub data_incomplete_count: usize,
    /// How many reviews the generation filed as not executable.
    pub not_executable_count: usize,
    /// How many broadcast requests the generation produced.
    pub broadcast_request_count: usize,
    /// The version the Review Manager gave the generation.
    pub review_version: i64,
    /// The idempotency key the generation was filed under.
    pub idempotency_key: String,
    /// When the generation was published.
    pub published_at: String,
}

/// The upstream versions one review generation is filed against.
///
/// Every field comes from the closed-loop Manager.  The Review Manager
/// compares the evidence against the very same state and rejects a
/// generation that was measured against a different one, so nothing is
/// recomputed here: a second opinion about a version would be a second
/// truth, and the two would drift.
pub fn expected_upstream(status: &ClosedLoopStatus) -> ExpectedUpstream {
    ExpectedUpstream {
        closed_loop_version: Some(status.closed_loop_version),
        closed_loop_checksum: status.materialized_checksum.clone(),
        candidate_edge_version: status.candidate_edge_version,
        candidate_edge_checksum: status.candidate_edge_checksum.clone(),
        observation_version: status.observation_version,
        observation_checksum: status.observation_checksum.clone(),
        pool_scope_checksum: status.pool_scope_checksum.clone(),
        library_versions: status.library_versions.clone(),
        library_checksums: status.library_checksums.clone(),
    }
}

/// Compare the evidence with the closed-loop catalog.
///
/// The comparison is exact: a key the catalog does not hold is as much of a
/// defect as a key the evidence forgot, because the first one would file a
/// review under a loop that was never published and the second one would
/// leave a published loop without a review.
pub fn coverage_gap(
    evidence: &[ReviewEvidenceInput],
    catalog_keys: &BTreeSet<String>,
) -> CoverageGap {
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut unknown: Vec<String> = Vec::new();
    for item in evidence {
        let key = item.loop_key.trim().to_lowercase();
        if key.is_empty() {
            unknown.push(item.loop_key.clone());
            continue;
        }
        if !seen.insert(key.clone()) {
            // A second block for the same loop would overwrite the first
            // one, so it is reported as unknown rather than counted twice.
            unknown.push(key);
            continue;
        }
        if !catalog_keys.contains(&key) {
            unknown.push(key);
        }
    }
    let missing = catalog_keys
        .iter()
        .filter(|key| !seen.contains(*key))
        .cloned()
        .collect();
    CoverageGap {
        expected: catalog_keys.len(),
        actual: evidence.len(),
        missing,
        unknown,
    }
}

/// The identifier of one review sweep.
///
/// The identifier is different for every sweep, so two rounds of the same
/// catalog never share an idempotency key: the second round is a new
/// generation and not a replay of the first one.
fn new_round_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let sequence = COUNTER.fetch_add(1, Ordering::SeqCst);
    format!("r{sequence}-{}", chrono::Utc::now().timestamp_millis())
}

/// The idempotency key of one review publication.
///
/// The key names the upstream generation the review was measured against
/// **and** the round it belongs to, so two rounds of the same catalog never
/// collide while a replay of the same round is recognised as a replay.
fn idempotency_key(
    network: &str,
    chain_id: i64,
    status: &ClosedLoopStatus,
    round_id: &str,
) -> String {
    let checksum: String = status
        .materialized_checksum
        .as_deref()
        .unwrap_or_default()
        .chars()
        .take(64)
        .collect();
    format!(
        "{REVIEW_PUBLISH_OPERATION}/{network}/{chain_id}/v{}/{checksum}/{round_id}",
        status.closed_loop_version
    )
}

/// The evidence of one chain, collected until it covers the whole catalog.
///
/// The Review Manager replaces the review set of the scope with the
/// evidence it receives.  A generation that carries only the loops of one
/// review group would therefore erase the reviews of every other loop, so
/// the evidence of the groups is collected until it covers the catalog.
#[derive(Debug, Default)]
pub struct ReviewEvidenceAccumulator {
    /// The keys the catalog held when the sweep started.
    catalog_keys: BTreeSet<String>,
    /// The upstream generation the sweep measures under.
    ///
    /// The evidence of a sweep has to describe **one** generation of the
    /// closed-loop catalog: the Review Manager replaces the whole review
    /// set of the scope with the evidence it receives, so a set that mixed
    /// two generations would file the loops of the older one under the
    /// newer one's version.
    upstream: Option<ClosedLoopUpstream>,
    /// The evidence of the loops measured so far.
    evidence: BTreeMap<String, ReviewEvidenceInput>,
    /// The identifier of the current sweep.
    round_id: Option<String>,
    /// When the last measurement arrived.
    updated_at: Option<String>,
}

impl ReviewEvidenceAccumulator {
    /// An empty accumulator.
    pub fn new() -> Self {
        Self::default()
    }

    /// Absorb the evidence of one review group.
    ///
    /// A catalog that changed since the last absorption starts a new sweep:
    /// the evidence of the previous catalog describes loops that no longer
    /// exist, and it would be filed against a generation that was never
    /// measured.  The same holds for the **generation**: the closed-loop
    /// Manager republishes its catalog whenever the observation or the
    /// candidate edge moves, and the evidence of the older generation
    /// describes capacities the newer one no longer holds.
    pub fn absorb(
        &mut self,
        catalog_keys: &BTreeSet<String>,
        upstream: Option<&ClosedLoopUpstream>,
        items: Vec<ReviewEvidenceInput>,
        measured_at: &str,
    ) {
        if self.catalog_keys != *catalog_keys || self.upstream.as_ref() != upstream {
            self.catalog_keys = catalog_keys.clone();
            self.upstream = upstream.cloned();
            self.evidence.clear();
            self.round_id = None;
        }
        for item in items {
            let key = item.loop_key.trim().to_lowercase();
            if key.is_empty() {
                continue;
            }
            self.evidence.insert(key, item);
        }
        self.updated_at = Some(measured_at.to_owned());
    }

    /// How many evidence blocks the accumulator holds.
    pub fn len(&self) -> usize {
        self.evidence.len()
    }

    /// Whether the accumulator holds nothing at all.
    pub fn is_empty(&self) -> bool {
        self.evidence.is_empty()
    }

    /// When the last measurement arrived.
    pub fn updated_at(&self) -> Option<&str> {
        self.updated_at.as_deref()
    }

    /// The upstream generation the sweep measures under.
    pub fn upstream(&self) -> Option<&ClosedLoopUpstream> {
        self.upstream.as_ref()
    }

    /// The catalog keys no evidence named.
    pub fn missing(&self) -> Vec<String> {
        self.catalog_keys
            .iter()
            .filter(|key| !self.evidence.contains_key(*key))
            .cloned()
            .collect()
    }

    /// Whether the accumulated evidence covers the whole catalog.
    pub fn covers_catalog(&self) -> bool {
        !self.catalog_keys.is_empty()
            && self.evidence.len() == self.catalog_keys.len()
            && self.missing().is_empty()
    }

    /// The evidence, in loop-key order.
    pub fn evidence(&self) -> Vec<ReviewEvidenceInput> {
        self.evidence.values().cloned().collect()
    }

    /// The identifier of the current sweep.
    pub fn round_id(&mut self) -> String {
        if self.round_id.is_none() {
            self.round_id = Some(new_round_id());
        }
        self.round_id.clone().unwrap_or_default()
    }

    /// Start a new sweep with a fresh identifier.
    ///
    /// A publication that failed wrote nothing, so the next attempt may use
    /// a fresh key.  Keeping the old one would make the retry a replay of a
    /// request that never landed, and the Review Manager would reject the
    /// changed content under the reused key.
    pub fn rotate_round(&mut self) {
        self.round_id = None;
    }

    /// Drop the accumulated evidence and start an empty sweep.
    pub fn clear(&mut self) {
        self.evidence.clear();
        self.round_id = None;
        self.updated_at = None;
    }
}

/// Publish one review generation of one chain.
///
/// The generation is published only when the evidence covers the **whole**
/// closed-loop catalog, and only against the upstream versions the
/// closed-loop Manager publishes right now.
pub fn publish_review_generation(
    database_path: &str,
    network: &str,
    chain_id: i64,
    evidence: Vec<ReviewEvidenceInput>,
    round_id: &str,
    measured_upstream: Option<&ClosedLoopUpstream>,
) -> Result<ReviewPublishOutcome, ReviewPublishError> {
    let status = closed_loop_manager::read_closed_loop_status(database_path, network, chain_id)
        .map_err(|error| ReviewPublishError::ClosedLoopState(error.to_string()))?;
    // The evidence has to describe the generation the Manager publishes
    // **now**.  A review that was measured under an older generation
    // describes capacities and identities the newer one no longer holds,
    // and the Review Manager would file it under the newer version all
    // the same.  The refusal keeps the published generation whole.
    if let Some(measured) = measured_upstream {
        let current = ClosedLoopUpstream {
            closed_loop_version: status.closed_loop_version,
            materialized_checksum: status.materialized_checksum.clone(),
        };
        if measured != &current {
            return Err(ReviewPublishError::UpstreamMoved {
                measured: measured.clone(),
                current,
            });
        }
    }
    let snapshot = closed_loop_manager::read_closed_loop_snapshot(
        database_path,
        network,
        chain_id,
        None,
        MAX_REVIEW_LOOPS,
    )
    .map_err(|error| ReviewPublishError::ClosedLoopState(error.to_string()))?;
    let catalog_keys: BTreeSet<String> = snapshot
        .loops
        .iter()
        .map(|row| row.loop_key.trim().to_lowercase())
        .filter(|key| !key.is_empty())
        .collect();
    if catalog_keys.len() != status.loop_count {
        // The catalog was truncated, so the evidence could never cover it.
        // Publishing anyway would file the loops the snapshot holds and
        // erase the reviews of the ones it does not.
        return Err(ReviewPublishError::ClosedLoopState(format!(
            "the closed-loop catalog is truncated: {} of {} loops",
            catalog_keys.len(),
            status.loop_count
        )));
    }
    let gap = coverage_gap(&evidence, &catalog_keys);
    if !gap.missing.is_empty() || !gap.unknown.is_empty() || gap.actual != gap.expected {
        return Err(ReviewPublishError::Coverage(gap));
    }
    let key = idempotency_key(network, chain_id, &status, round_id);
    let request = ReviewWriteRequest {
        network: network.to_owned(),
        chain_id,
        operation: REVIEW_PUBLISH_OPERATION.to_owned(),
        request_id: format!("{REVIEW_PUBLISH_OPERATION}-{round_id}"),
        idempotency_key: key.clone(),
        apply: true,
        expected_upstream: expected_upstream(&status),
        evidence,
    };
    let outcome = review_manager::run_reviews_write(database_path, request)
        .map_err(|error| ReviewPublishError::Manager(error.to_string()))?;
    Ok(ReviewPublishOutcome {
        published: true,
        loop_count: outcome.result.loop_count,
        evidence_count: outcome.result.reviewed_count,
        completed_count: outcome.result.completed_count,
        data_incomplete_count: outcome.result.data_incomplete_count,
        not_executable_count: outcome.result.not_executable_count,
        broadcast_request_count: outcome.result.broadcast_request_count,
        review_version: outcome.result.review_version,
        idempotency_key: key,
        published_at: chrono::Utc::now().to_rfc3339(),
    })
}

/// Build the evidence of every route the review measured.
///
/// One route produces one evidence block.  The loop key is the one the
/// closed-loop Manager published; a route the Manager published no loop for
/// is skipped, because a review filed under a self-made key would borrow
/// the capacity of a loop that was never proved for it.
pub fn build_route_evidence(
    payload: &Value,
    catalog: &ClosedLoopCatalog,
    pool_states: &BTreeMap<String, Vec<Value>>,
    decimals_for: &dyn Fn(&str) -> Option<i64>,
    measured_at: &str,
) -> Vec<ReviewEvidenceInput> {
    let Some(routes) = payload.get("routes").and_then(Value::as_array) else {
        return Vec::new();
    };
    let mut evidence = Vec::new();
    for route in routes {
        let Some(loop_key) = route_loop_key(route) else {
            continue;
        };
        let states = pool_states.get(&loop_key.to_lowercase());
        evidence.push(route_evidence(
            route,
            &loop_key,
            catalog,
            states,
            decimals_for,
            measured_at,
        ));
    }
    evidence
}

/// The closed-loop key the Manager published for one route.
fn route_loop_key(route: &Value) -> Option<String> {
    route
        .get("closedLoopKey")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

/// The evidence of one measured route.
fn route_evidence(
    route: &Value,
    loop_key: &str,
    catalog: &ClosedLoopCatalog,
    pool_states: Option<&Vec<Value>>,
    decimals_for: &dyn Fn(&str) -> Option<i64>,
    measured_at: &str,
) -> ReviewEvidenceInput {
    let borrow_symbol = borrow_symbol(route);
    let borrow_address = borrow_address(route);
    let decimals = decimals_for(&borrow_address).or_else(|| {
        catalog
            .identity_by_key(loop_key)
            .and_then(|identity| identity.borrow_token_decimals)
            .map(i64::from)
    });
    let signed_pnl = measured_signed_pnl(route, &borrow_symbol, &borrow_address, decimals);
    let run = route.get("runArbitrage").and_then(Value::as_object);
    // The ok and the executable flags are the **kernel's** verdict: the
    // chain accepted the route or it did not.  The publisher copies them
    // and never invents one, so a route the chain rejected can never reach
    // an execution gate.
    let ok = run
        .and_then(|run| run.get("ok"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let executable = run
        .and_then(|run| run.get("executable"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let block_number = run
        .and_then(|run| run.get("blockNumber"))
        .and_then(Value::as_i64);
    let mut raw: BTreeMap<String, Value> = BTreeMap::new();
    if let Some(run) = run {
        for (key, value) in run {
            if RESERVED_RUN_KEYS.contains(&key.as_str()) {
                continue;
            }
            raw.insert(key.clone(), value.clone());
        }
    }
    // The amount the route was reviewed with travels with the evidence: the
    // Review Manager re-prices every hop with it.
    for key in FINAL_AMOUNT_KEYS {
        if let Some(value) = route.get(key) {
            raw.entry(key.to_owned()).or_insert_with(|| value.clone());
        }
    }
    // The pool states the review saw travel with the evidence as well: the
    // state-transition gate compares them against the state the Manager
    // holds now, and it cannot prove anything without them.
    if let Some(states) = pool_states {
        raw.insert("quote".to_owned(), json!({ "candidate_pools": states }));
    }
    let diagnostics = measurement_diagnostics(route, signed_pnl.is_some(), measured_at);
    ReviewEvidenceInput {
        loop_key: loop_key.to_owned(),
        static_preview: static_preview(route, measured_at),
        run_arbitrage: Some(RunArbitrageEvidence {
            ok,
            executable,
            block_number,
            signed_pnl: signed_pnl.clone(),
            diagnostics: Vec::new(),
            raw,
        }),
        diagnostics,
        reviewed_at: Some(measured_at.to_owned()),
        closed_loop_identity: Some(closed_loop_identity(route)),
    }
}

/// The diagnostic that names why a route carries no measured profit.
///
/// A route that paid nothing was still **measured**, and the measurement
/// is the profit itself.  Only a route that was never priced at all has no
/// measurement, and the diagnostic says which of the two happened.
fn measurement_diagnostics(
    route: &Value,
    measured: bool,
    measured_at: &str,
) -> Vec<ReviewDiagnosticInput> {
    if measured {
        return Vec::new();
    }
    vec![ReviewDiagnosticInput {
        diagnostic_id: None,
        diagnostic_type: ReviewDiagnosticType::EvidenceMissing,
        diagnostic_code: Some("measured_signed_pnl_missing".to_owned()),
        message: Some(
            "the route was not priced, so it carries no measured signed profit".to_owned(),
        ),
        hop_index: None,
        pool_id: None,
        selector: None,
        payload: json!({
            "status": route.get("status").cloned().unwrap_or(Value::Null),
            "error": route.get("error").cloned().unwrap_or(Value::Null),
            "errorKind": route.get("errorKind").cloned().unwrap_or(Value::Null),
            "ladderBlockedReason": route.get("ladderBlockedReason").cloned().unwrap_or(Value::Null),
        }),
        observed_at: Some(measured_at.to_owned()),
    }]
}

/// The static preview of one measured route.
fn static_preview(route: &Value, measured_at: &str) -> Value {
    json!({
        "source": "native_rust_review",
        "measuredAt": measured_at,
        "status": route.get("status").cloned().unwrap_or(Value::Null),
        "routeSymbols": route.get("routeSymbols").cloned().unwrap_or(Value::Null),
        "routeAddresses": route.get("routeAddresses").cloned().unwrap_or(Value::Null),
        "selectedAmountRaw": route.get("selectedAmountRaw").cloned().unwrap_or(Value::Null),
        "selectedProfitRaw": route.get("selectedProfitRaw").cloned().unwrap_or(Value::Null),
        "attemptedCount": route.get("attemptedCount").cloned().unwrap_or(Value::Null),
    })
}

/// The identity diagnosis the kernel published for one route.
fn closed_loop_identity(route: &Value) -> ClosedLoopIdentityEvidence {
    ClosedLoopIdentityEvidence {
        closed_loop_identity_status: route
            .get("closedLoopIdentityStatus")
            .and_then(Value::as_str)
            .map(str::to_owned),
        closed_loop_pool_identity_status: route
            .get("closedLoopPoolIdentityStatus")
            .and_then(Value::as_str)
            .map(str::to_owned),
        identity_mismatch: route
            .get("identityMismatch")
            .and_then(Value::as_str)
            .map(str::to_owned),
        closed_loop_repair_target: route
            .get("repairTarget")
            .and_then(Value::as_str)
            .map(str::to_owned),
    }
}

/// The borrow token of one route, as the kernel published it.
fn borrow_symbol(route: &Value) -> String {
    route
        .get("routeSymbols")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_uppercase()
}

/// The borrow token address of one route, as the kernel published it.
fn borrow_address(route: &Value) -> String {
    route
        .get("routeAddresses")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_lowercase()
}

/// The measured signed profit of one route payload, in raw units.
///
/// The order below matters, and it is the order the kernel publishes:
///
/// 1. reportedNetProfitRaw is the kernel's own conclusion for the
///    reviewed size and wins whenever it carries a value.
/// 2. The **probe rows** are the fallback.  The amount ladder prices the
///    route at several concrete sizes and records the signed profit of
///    each one, so a route that pays at no size still has a measured loss.
/// 3. The runArbitrage object of the route carries the verdict the
///    contract gave for the reviewed size.  When the contract rejected the
///    route with ArbitrageNotProfitable(received, required), that pair is
///    the exact borrow-token loss of the size.
/// 4. selectedProfitRaw is the last resort, and only when it is not the
///    unsigned zero placeholder.
pub fn measured_signed_profit_raw(route_payload: &Value) -> Option<Value> {
    let run_arbitrage = route_payload
        .get("runArbitrage")
        .or_else(|| {
            route_payload
                .get("selectedRoutePayload")
                .and_then(|inner| inner.get("runArbitrage"))
        })
        .cloned()
        .unwrap_or(Value::Null);
    route_payload
        .get("reportedNetProfitRaw")
        .cloned()
        .filter(|value| !value.is_null())
        .or_else(|| {
            run_arbitrage
                .get("reportedNetProfitRaw")
                .cloned()
                .filter(|value| !value.is_null())
        })
        .or_else(|| {
            run_arbitrage
                .get("arbitrageNotProfitable")
                .and_then(|value| value.get("profitDeltaRaw"))
                .cloned()
                .filter(|value| !value.is_null())
        })
        .or_else(|| measured_probe_loss_raw(route_payload.get("attempts")))
        .or_else(|| {
            route_payload
                .get("selectedProfitRaw")
                .cloned()
                .filter(|value| !value.is_null() && !is_unsigned_zero_placeholder(value))
        })
}

/// The measured signed profit of one route, as the Review Manager consumes it.
pub fn measured_signed_pnl(
    route_payload: &Value,
    borrow_symbol: &str,
    borrow_address: &str,
    decimals: Option<i64>,
) -> Option<SignedPnl> {
    let raw = measured_signed_profit_raw(route_payload)?;
    let raw_text = raw.as_str()?.trim().to_owned();
    if raw_text.is_empty() {
        return None;
    }
    let amount = signed_raw_to_amount(&raw, decimals?)?;
    Some(SignedPnl {
        raw: raw_text,
        amount,
        currency: borrow_symbol.to_owned(),
        address: Some(borrow_address.to_owned()),
        unit: SIGNED_PNL_UNIT.to_owned(),
        source: SIGNED_PNL_SOURCE.to_owned(),
    })
}

/// Return the signed profit of the last **priced** probe row.
///
/// The rows are scanned from the back because the ladder halves the size
/// while it searches downwards: the last row is the smallest size it
/// tried, and the loss at the smallest executable size is the one that
/// bounds the route.
pub fn measured_probe_loss_raw(attempts: Option<&Value>) -> Option<Value> {
    let rows = attempts?.as_array()?;
    for row in rows.iter().rev() {
        let Some(profit) = row.get("profitRaw") else {
            continue;
        };
        if profit.is_null() {
            continue;
        }
        if let Some(text) = profit.as_str() {
            if text.trim().is_empty() || is_unsigned_zero_placeholder(profit) {
                continue;
            }
        }
        return Some(profit.clone());
    }
    None
}

/// Return whether a raw value is the unsigned zero placeholder.
///
/// The kernel publishes "0x0" when it measured nothing. Treating it as a
/// measured zero would turn a measurement gap into a measured result.
pub fn is_unsigned_zero_placeholder(value: &Value) -> bool {
    let Some(text) = value.as_str() else {
        return false;
    };
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return false;
    }
    let digits = trimmed
        .strip_prefix("0x")
        .or_else(|| trimmed.strip_prefix("0X"))
        .unwrap_or(trimmed);
    !digits.is_empty() && digits.chars().all(|character| character == '0')
}

/// Turn a signed raw amount into the human amount of its token.
///
/// The raw form is what the contract produced: a hexadecimal integer with
/// an optional minus sign. The human amount is the same number scaled by
/// the decimals of the token, so the two always describe the same value.
pub fn signed_raw_to_amount(raw: &Value, decimals: i64) -> Option<f64> {
    let text = raw.as_str()?.trim();
    if text.is_empty() {
        return None;
    }
    let (negative, digits) = if let Some(rest) = text.strip_prefix('-') {
        (true, rest.trim())
    } else {
        (false, text.trim())
    };
    let digits = digits.strip_prefix("0x").or_else(|| digits.strip_prefix("0X"))?;
    let magnitude = num_bigint::BigUint::parse_bytes(digits.as_bytes(), 16)?;
    let value = num_bigint::BigInt::from_biguint(num_bigint::Sign::Plus, magnitude);
    let signed = if negative { -value } else { value };
    let scale = 10f64.powi(decimals as i32);
    if !scale.is_finite() || scale <= 0.0 {
        return None;
    }
    // The raw value is exact up to this many digits; beyond it the f64
    // mantissa runs out, so the amount is published with the precision the
    // float can carry rather than a number that pretends to be exact.
    let human = signed.to_string().parse::<f64>().ok()? / scale;
    human.is_finite().then_some(human)
}

#[cfg(test)]
mod tests {
    use super::*;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const LOOP_KEY: &str = "0x0000000000000000000000000000000000000000:0x1111111111111111111111111111111111111111:0x2222222222222222222222222222222222222222";

    fn catalog() -> ClosedLoopCatalog {
        crate::closed_loop_identity::catalog_from_rows(&[json!({
            "loop_key": LOOP_KEY,
            "path_addresses_json": "[\"0x0000000000000000000000000000000000000000\",\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x0000000000000000000000000000000000000000\"]",
            "pool_ids_json": "[\"pool-a\",\"pool-yz\",\"pool-d\"]",
            "single_tick_capacity_anchor_raw": "1000000000000000",
            "borrow_token_decimals": 18,
        })])
    }

    fn route(loop_key: &str) -> Value {
        json!({
            "closedLoopKey": loop_key,
            "routeSymbols": ["USDG", "AMZN", "TA", "USDG"],
            "routeAddresses": [
                "0x0000000000000000000000000000000000000000",
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
                "0x0000000000000000000000000000000000000000"
            ],
            "status": "profitable_amount_found",
            "selectedAmountRaw": "0x0de0b6b3a7640000",
            "selectedProfitRaw": "0x0de0b6b3a7640000",
            "runArbitrage": {
                "ok": true,
                "executable": true,
                "blockNumber": 12345,
                "selectedAmountRaw": "0x0de0b6b3a7640000",
                "reportedNetProfitRaw": "0x0de0b6b3a7640000"
            }
        })
    }

    fn evidence_of(loop_key: &str) -> ReviewEvidenceInput {
        route_evidence(
            &route(loop_key),
            loop_key,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        )
    }

    /// The generation the fixture's library state publishes.
    fn fixture_upstream() -> ClosedLoopUpstream {
        ClosedLoopUpstream {
            closed_loop_version: 19,
            materialized_checksum: Some("sha256:closed-loop".to_owned()),
        }
    }

    /// The manager fixture the publish gate reads: one closed loop with a
    /// published capacity and the pool states the gates need.
    fn manager_database() -> (tempfile::TempDir, String) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        let connection = rusqlite::Connection::open(&path).unwrap();
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
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', 19, 'sha256:closed-loop',
                           7, 'sha256:candidate-edge', 4, 'sha256:observation',
                           'sha256:pool-scope', '{\"core\":3}', '{\"core\":\"sha256:core\"}',
                           1, 1, 3, 0, 0, 0, 'fixture', 'fixture')",
                rusqlite::params![NETWORK, CHAIN_ID],
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
                    observation_checksum, status, source,
                    single_tick_capacity_anchor_raw,
                    single_tick_capacity_binding_pool_id,
                    created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 'route:v1',
                           'robinhood:4663:0x0000000000000000000000000000000000000000',
                           '0x0000000000000000000000000000000000000000', 'USDG',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           'robinhood:4663:0x2222222222222222222222222222222222222222',
                           'USDG -> AMZN -> TA -> USDG',
                           '[\"0x0000000000000000000000000000000000000000\",\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x0000000000000000000000000000000000000000\"]',
                           '[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]', 'pool-path:1',
                           '[\"pool-a\",\"pool-yz\",\"pool-d\"]', '{}', '{\"1\":1}',
                           1, 'fixture', 1, 7, 'sha256:candidate-edge', 4,
                           'sha256:observation', 'active', 'fixture',
                           '1000000000000000', 'pool-yz',
                           'fixture', 'fixture')",
                rusqlite::params![NETWORK, CHAIN_ID, LOOP_KEY],
            )
            .unwrap();
        drop(connection);
        (directory, path.to_string_lossy().into_owned())
    }

    /// **T-1**: the evidence of the whole catalog is published, and the
    /// review library holds one row per closed loop.
    #[test]
    fn a_full_generation_is_published_and_fills_the_review_library() {
        let (_directory, path) = manager_database();
        let evidence = vec![evidence_of(LOOP_KEY)];
        let outcome = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            evidence,
            "r0-1",
            Some(&fixture_upstream()),
        )
        .expect("the generation covers the catalog");
        assert!(outcome.published);
        assert_eq!(outcome.loop_count, 1);
        assert_eq!(outcome.evidence_count, 1);
        assert_eq!(outcome.data_incomplete_count, 0);
        assert!(outcome.idempotency_key.contains("r0-1"));
        let connection = rusqlite::Connection::open(&path).unwrap();
        let rows: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_v4_reviews WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 1, "one review row per closed loop");
    }

    /// **T-2**: a partial generation is rejected, and the reviews that were
    /// already published survive the rejected attempt.
    #[test]
    fn a_partial_generation_is_rejected_and_keeps_the_published_reviews() {
        let (_directory, path) = manager_database();
        publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of(LOOP_KEY)],
            "r0-1",
            Some(&fixture_upstream()),
        )
        .expect("the first generation covers the catalog");
        let error = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            Vec::new(),
            "r0-2",
            Some(&fixture_upstream()),
        )
        .expect_err("an empty generation does not cover the catalog");
        match error {
            ReviewPublishError::Coverage(gap) => {
                assert_eq!(gap.expected, 1);
                assert_eq!(gap.actual, 0);
                assert_eq!(gap.missing.len(), 1);
            }
            other => panic!("expected a coverage failure, got {other:?}"),
        }
        let connection = rusqlite::Connection::open(&path).unwrap();
        let rows: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_v4_reviews WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 1, "the rejected generation did not erase the reviews");
    }

    /// **T-3**: an evidence block that names a loop the Manager never
    /// published is rejected, and it never becomes a review row.
    #[test]
    fn an_unknown_loop_key_is_rejected_and_never_published() {
        let (_directory, path) = manager_database();
        let error = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of("loop-that-was-never-published")],
            "r0-3",
            Some(&fixture_upstream()),
        )
        .expect_err("an unknown loop key does not cover the catalog");
        match error {
            ReviewPublishError::Coverage(gap) => {
                assert_eq!(gap.unknown.len(), 1);
                assert_eq!(gap.missing.len(), 1);
            }
            other => panic!("expected a coverage failure, got {other:?}"),
        }
        let connection = rusqlite::Connection::open(&path).unwrap();
        let rows: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_v4_reviews WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 0, "an unmatched key never produced a review row");
    }

    /// **T-4**: the idempotency key is not a constant, and reusing it with
    /// different content is an error rather than a silent overwrite.
    #[test]
    fn the_idempotency_key_differs_per_round_and_rejects_a_changed_replay() {
        let (_directory, path) = manager_database();
        let first = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of(LOOP_KEY)],
            "r0-1",
            Some(&fixture_upstream()),
        )
        .expect("the first generation covers the catalog");
        let second = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of(LOOP_KEY)],
            "r0-2",
            Some(&fixture_upstream()),
        )
        .expect("the second generation covers the catalog");
        assert_ne!(
            first.idempotency_key, second.idempotency_key,
            "two rounds must not share an idempotency key"
        );
        // The same key with different content is rejected by the Review
        // Manager's own ledger.
        let mut changed = evidence_of(LOOP_KEY);
        changed.reviewed_at = Some("2026-11-24T01:00:00Z".to_owned());
        let error = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![changed],
            "r0-2",
            Some(&fixture_upstream()),
        )
        .expect_err("the reused key must not accept different content");
        assert!(
            matches!(error, ReviewPublishError::Manager(_)),
            "got {error:?}"
        );
    }

    /// **T-5**: a generation that was measured under an upstream the
    /// closed-loop Manager has replaced is rejected, and the review
    /// library keeps the reviews it already held.
    #[test]
    fn a_generation_against_another_upstream_is_rejected() {
        let (_directory, path) = manager_database();
        // A first generation under the current upstream, so the library
        // holds something the rejected attempt must not erase.
        publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of(LOOP_KEY)],
            "r0-4",
            Some(&fixture_upstream()),
        )
        .expect("the first generation covers the catalog");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_library_state
                    SET closed_loop_version = 20 WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
            )
            .unwrap();
        drop(connection);
        // The evidence was measured under version 19, and the Manager now
        // publishes version 20: the publisher has to refuse, because the
        // Review Manager would file the evidence of the older generation
        // under the newer one's version.
        let error = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of(LOOP_KEY)],
            "r0-5",
            Some(&fixture_upstream()),
        )
        .expect_err("the upstream moved");
        assert!(
            matches!(error, ReviewPublishError::UpstreamMoved { .. }),
            "got {error:?}"
        );
        let connection = rusqlite::Connection::open(&path).unwrap();
        let rows: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM manager_v4_reviews WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 1, "the refused generation did not touch the library");
        let version: i64 = connection
            .query_row(
                "SELECT closed_loop_version FROM manager_v4_reviews \
                 WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(version, 19, "the review still names the generation it was measured under");
    }

    /// **T-6**: a signed profit that is missing one of its parts is not a
    /// signed profit, and the route then carries no measurement.
    #[test]
    fn an_incomplete_signed_profit_is_no_measurement() {
        let mut payload = route(LOOP_KEY);
        payload["runArbitrage"]["reportedNetProfitRaw"] = Value::Null;
        payload["selectedProfitRaw"] = Value::Null;
        payload["attempts"] = json!([]);
        assert!(measured_signed_pnl(&payload, "USDG", "0xabc", Some(18)).is_none());
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        assert!(evidence.run_arbitrage.is_some());
        assert!(evidence.run_arbitrage.unwrap().signed_pnl.is_none());
        assert_eq!(evidence.diagnostics.len(), 1);
    }

    /// **T-7**: a measured zero or negative profit is a **measurement** and
    /// never becomes a missing value.
    #[test]
    fn a_measured_loss_is_kept_as_a_measurement() {
        let mut payload = route(LOOP_KEY);
        payload["status"] = json!("not_profitable_after_costs");
        payload["runArbitrage"] = json!({
            "ok": false,
            "executable": true,
            "noProfitableRoute": true,
            "reportedNetProfitRaw": "-0x64"
        });
        payload["selectedProfitRaw"] = json!("0x0");
        let signed = measured_signed_pnl(&payload, "USDG", "0xabc", Some(18))
            .expect("the measured loss is a measurement");
        assert_eq!(signed.raw, "-0x64");
        assert!(signed.amount < 0.0);
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        assert!(evidence.diagnostics.is_empty());
        let run = evidence.run_arbitrage.expect("the run evidence");
        assert!(run.signed_pnl.is_some());
        assert!(!run.ok);
    }

    /// **T-8**: a failed publication is reported as a failure and never as
    /// a healthy review.
    #[test]
    fn a_failed_publication_reports_the_failure() {
        let (_directory, path) = manager_database();
        let error = publish_review_generation(
            &path,
            NETWORK,
            CHAIN_ID,
            Vec::new(),
            "r0-8",
            Some(&fixture_upstream()),
        )
        .expect_err("an empty generation is not a publication");
        let message = error.to_string();
        assert!(message.contains("closed loops"), "got {message}");
    }

    /// The accumulator collects the evidence of the groups until it covers
    /// the catalog, and a catalog that changed starts a new sweep.
    #[test]
    fn the_accumulator_covers_the_catalog_before_it_publishes() {
        let mut accumulator = ReviewEvidenceAccumulator::new();
        let mut keys = BTreeSet::new();
        keys.insert(LOOP_KEY.to_lowercase());
        accumulator.absorb(
            &keys,
            Some(&fixture_upstream()),
            vec![evidence_of(LOOP_KEY)],
            "2026-11-24T00:00:00Z",
        );
        assert_eq!(accumulator.len(), 1);
        assert!(accumulator.covers_catalog());
        assert!(accumulator.missing().is_empty());
        // A catalog that changed starts a new sweep: the evidence of the
        // previous catalog describes loops that no longer exist.
        let mut other = BTreeSet::new();
        other.insert("other-loop".to_owned());
        accumulator.absorb(
            &other,
            Some(&fixture_upstream()),
            vec![],
            "2026-11-24T00:01:00Z",
        );
        assert_eq!(accumulator.len(), 0);
        assert!(!accumulator.covers_catalog());
    }

    /// The generation is part of the sweep: when the closed-loop Manager
    /// republishes its catalog, the evidence of the older generation is
    /// dropped and the sweep starts over under the new one.
    #[test]
    fn a_new_generation_starts_a_new_sweep() {
        let mut accumulator = ReviewEvidenceAccumulator::new();
        let mut keys = BTreeSet::new();
        keys.insert(LOOP_KEY.to_lowercase());
        accumulator.absorb(
            &keys,
            Some(&fixture_upstream()),
            vec![evidence_of(LOOP_KEY)],
            "2026-11-24T00:00:00Z",
        );
        assert_eq!(accumulator.len(), 1);
        assert_eq!(accumulator.upstream(), Some(&fixture_upstream()));
        let newer = ClosedLoopUpstream {
            closed_loop_version: 20,
            materialized_checksum: Some("sha256:closed-loop-2".to_owned()),
        };
        accumulator.absorb(
            &keys,
            Some(&newer),
            vec![evidence_of(LOOP_KEY)],
            "2026-11-24T00:05:00Z",
        );
        assert_eq!(accumulator.upstream(), Some(&newer));
        assert_eq!(
            accumulator.len(),
            1,
            "the sweep restarted, so it holds only the new measurement"
        );
        assert!(accumulator.covers_catalog());
    }

    /// The route's own key is the one the Manager published, and a route
    /// without one is not evidence at all.
    #[test]
    fn only_a_manager_published_key_becomes_evidence() {
        let mut payload = route(LOOP_KEY);
        payload["closedLoopKey"] = Value::Null;
        let evidence = build_route_evidence(
            &json!({"routes": [payload]}),
            &catalog(),
            &BTreeMap::new(),
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        assert!(evidence.is_empty());
    }

    /// The measured pool states travel with the evidence, because the
    /// state-transition gate compares them against the current state.
    #[test]
    fn the_review_time_pool_states_travel_with_the_evidence() {
        let mut states = BTreeMap::new();
        states.insert(
            LOOP_KEY.to_lowercase(),
            vec![json!({"poolId": "pool-a", "sqrtPriceX96": "1"})],
        );
        let evidence = build_route_evidence(
            &json!({"routes": [route(LOOP_KEY)]}),
            &catalog(),
            &states,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        let run = evidence[0].run_arbitrage.clone().expect("the run evidence");
        assert_eq!(
            run.raw.get("quote").and_then(|value| value.get("candidate_pools")),
            Some(&json!([{"poolId": "pool-a", "sqrtPriceX96": "1"}]))
        );
    }
}
