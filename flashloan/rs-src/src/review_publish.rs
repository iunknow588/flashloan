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
use sha2::{Digest, Sha256};

use closed_loop_manager::{acknowledge_consumers_rebuilt, ClosedLoopStatus};
use review_manager::{
    ClosedLoopGenerationEvidence, ClosedLoopIdentityEvidence, ExpectedUpstream,
    ReviewDiagnosticInput, ReviewDiagnosticType, ReviewEvidenceInput, ReviewWriteRequest,
    RunArbitrageEvidence, SignedPnl,
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

/// The scope and the generation the consumers were last acknowledged for.
///
/// The acknowledgement is a write on the Manager's own state row, and the
/// publication runs many times per round.  The generation moves only when
/// the closed-loop library is rebuilt, so remembering it keeps one write per
/// generation instead of one write per publication.
#[derive(Clone, PartialEq, Eq)]
struct AcknowledgedGeneration {
    database_path: String,
    network: String,
    chain_id: i64,
    closed_loop_version: i64,
    materialized_checksum: Option<String>,
}

static ACKNOWLEDGED_GENERATIONS: std::sync::Mutex<Vec<AcknowledgedGeneration>> =
    std::sync::Mutex::new(Vec::new());

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
    /// The closed loops the publication left out.
    ///
    /// A loop is left out when the catalog replaced it after the
    /// measurement, or when it is no longer published at all.  The list is
    /// published so the drop is visible instead of silent.
    pub dropped_loop_keys: Vec<String>,
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

// The coverage methods of the accumulator belong to the whole-catalog
// publication.  The per-loop publication keeps the accumulator for the
// round bookkeeping of the scope and reads none of them.
#[allow(dead_code)]
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
#[allow(dead_code)]
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
        dropped_loop_keys: Vec::new(),
    })
}

/// Publish the reviews of the closed loops one review group measured.
///
/// The publication is **per closed loop**.  It writes the reviews of the
/// loops the evidence names and touches nothing else: the review of one
/// closed loop never decides the review of another one, and a closed loop
/// that cannot be published never keeps the other loops from being
/// published.
///
/// The generation the evidence was measured under travels with the
/// evidence, so the store files every review under the generation it was
/// measured against and not under the one the catalog happens to hold when
/// the review lands.  The publication therefore expects **no** upstream
/// generation of its own: a generation that moved on between the
/// measurement and the publication is exactly the case a per-loop
/// publication has to survive.
pub fn publish_review_loops(
    database_path: &str,
    network: &str,
    chain_id: i64,
    evidence: Vec<ReviewEvidenceInput>,
    measured_catalog: &ClosedLoopCatalog,
    round_id: &str,
) -> Result<ReviewPublishOutcome, ReviewPublishError> {
    if evidence.is_empty() {
        return Err(ReviewPublishError::Manager(
            "the publication carries no measured route".to_owned(),
        ));
    }
    // The catalog may have replaced a closed loop since the measurement.
    // Such a loop is **dropped** instead of failing the whole publication:
    // one closed loop that left the catalog must not keep the other closed
    // loops of the group from being published.
    //
    // A loop the catalog still publishes under the same key but with
    // another identity is dropped as well: the measurement describes the
    // loop that no longer exists, and a review of it is not a review of the
    // loop that replaced it.
    let snapshot = closed_loop_manager::read_closed_loop_snapshot(
        database_path,
        network,
        chain_id,
        None,
        MAX_REVIEW_LOOPS,
    )
    .map_err(|error| ReviewPublishError::ClosedLoopState(error.to_string()))?;
    let current = crate::closed_loop_identity::catalog_from_rows(
        &snapshot
            .loops
            .iter()
            .map(identity_row_of)
            .collect::<Vec<Value>>(),
    );
    let known: BTreeSet<String> = snapshot
        .loops
        .iter()
        .map(|row| row.loop_key.trim().to_lowercase())
        .filter(|key| !key.is_empty())
        .collect();
    let mut dropped: Vec<String> = Vec::new();
    let evidence: Vec<ReviewEvidenceInput> = evidence
        .into_iter()
        .filter(|item| {
            let key = item.loop_key.trim().to_lowercase();
            if !known.contains(&key) {
                dropped.push(item.loop_key.clone());
                return false;
            }
            let measured = measured_catalog.identity_by_key(&key);
            let published = current.identity_by_key(&key);
            match (measured, published) {
                (Some(measured), Some(published)) => {
                    if !measured.is_same_loop(published) {
                        dropped.push(item.loop_key.clone());
                        return false;
                    }
                    true
                }
                // A loop the runtime catalog does not hold cannot be
                // compared, and the evidence of the runtime catalog is the
                // one the measurement was made under.
                (None, Some(_)) => true,
                (Some(_), None) => true,
                (None, None) => true,
            }
        })
        .collect();
    if evidence.is_empty() {
        return Err(ReviewPublishError::Manager(format!(
            "every measured closed loop left the catalog: {}",
            dropped.join(", ")
        )));
    }
    let key = loops_idempotency_key(network, chain_id, &evidence, round_id);
    let request = ReviewWriteRequest {
        network: network.to_owned(),
        chain_id,
        operation: REVIEW_PUBLISH_OPERATION.to_owned(),
        request_id: format!("{REVIEW_PUBLISH_OPERATION}-{round_id}"),
        idempotency_key: key.clone(),
        apply: true,
        expected_upstream: ExpectedUpstream::default(),
        evidence,
    };
    let outcome = review_manager::run_reviews_write(database_path, request)
        .map_err(|error| ReviewPublishError::Manager(error.to_string()))?;
    acknowledge_the_rebuilt_consumers(database_path, network, chain_id, measured_catalog);
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
        dropped_loop_keys: dropped,
    })
}

/// Tell the closed-loop Manager that the consumers rebuilt.
///
/// The Manager's `consumers_need_rebuild` flag says that the closed-loop
/// library moved and that the consumers may still hold the generation before
/// it.  This publication was measured against the generation the runtime
/// catalog carries, so the consumers **have** rebuilt, and the Manager may
/// stop saying that they have to.
///
/// The acknowledgement names the generation it was measured under, so it
/// clears the flag only while that generation is the one the library
/// publishes now: a directory that moved again after the measurement leaves
/// the flag set, because the consumers really are behind again.
///
/// A failed acknowledgement does **not** fail the publication.  The reviews
/// are already written, and a flag that stays set is a visible and correct
/// statement about the consumers; losing the reviews over it would be the
/// worse trade.
fn acknowledge_the_rebuilt_consumers(
    database_path: &str,
    network: &str,
    chain_id: i64,
    measured_catalog: &ClosedLoopCatalog,
) {
    let Some(upstream) = measured_catalog.upstream() else {
        return;
    };
    let generation = AcknowledgedGeneration {
        database_path: database_path.to_owned(),
        network: network.to_owned(),
        chain_id,
        closed_loop_version: upstream.closed_loop_version,
        materialized_checksum: upstream.materialized_checksum.clone(),
    };
    if let Ok(acknowledged) = ACKNOWLEDGED_GENERATIONS.lock() {
        if acknowledged.contains(&generation) {
            return;
        }
    }
    match acknowledge_consumers_rebuilt(
        database_path,
        network,
        chain_id,
        generation.closed_loop_version,
        generation.materialized_checksum.as_deref(),
    ) {
        Ok(_) => {
            if let Ok(mut acknowledged) = ACKNOWLEDGED_GENERATIONS.lock() {
                acknowledged.retain(|entry| {
                    entry.database_path != generation.database_path
                        || entry.network != generation.network
                        || entry.chain_id != generation.chain_id
                });
                acknowledged.push(generation);
            }
        }
        Err(error) => {
            eprintln!(
                "review publish: the closed-loop Manager could not be told that the \
                 consumers rebuilt against version {}: {error}",
                generation.closed_loop_version
            );
        }
    }
}

/// The catalog identity of one closed-loop row.
///
/// The runtime catalog is read out of the SQL rows, and those rows carry
/// the JSON columns the identity reader expects.  The rows the Manager
/// publishes carry the same facts under their own names, so the row is
/// translated here and the identity reader stays the only place that knows
/// the shape of an identity.
fn identity_row_of(row: &closed_loop_manager::ClosedLoopRow) -> Value {
    json!({
        "loop_key": row.loop_key,
        "path_addresses_json": serde_json::to_string(&row.path_addresses)
            .unwrap_or_else(|_| "[]".to_owned()),
        "pool_ids_json": serde_json::to_string(&row.directional_pool_ids)
            .unwrap_or_else(|_| "[]".to_owned()),
        "single_tick_capacity_anchor_raw": row.single_tick_capacity_anchor_raw
            .map(|value| value.to_string()),
        "single_tick_capacity_binding_pool_id": row
            .single_tick_capacity_binding_directional_pool_id
            .clone(),
        "borrow_token_usdg_value_raw": row.borrow_token_usdg_value_raw
            .map(|value| value.to_string()),
        "borrow_token_decimals": row.borrow_token_decimals,
        "cross_tick_review_threshold_usdg_raw": row
            .cross_tick_review_threshold_usdg_raw
            .to_string(),
        "cross_tick_capacity_anchor_raw": row.cross_tick_capacity_anchor_raw
            .map(|value| value.to_string()),
    })
}

/// The idempotency key of one per-loop publication.
///
/// The key names the round, the loops the round carried and the generation
/// they were measured under.  The loops are part of the key because two
/// groups publish disjoint sets of closed loops, and the generation is part
/// of it because a review is filed under the generation it was measured
/// against.
fn loops_idempotency_key(
    network: &str,
    chain_id: i64,
    evidence: &[ReviewEvidenceInput],
    round_id: &str,
) -> String {
    let mut loops = BTreeSet::new();
    let mut generations = BTreeSet::new();
    for item in evidence {
        loops.insert(item.loop_key.trim().to_lowercase());
        if let Some(generation) = item.closed_loop_generation.as_ref() {
            generations.insert(format!(
                "{}:{}:{}:{}:{}:{}",
                generation.closed_loop_version.unwrap_or_default(),
                generation.closed_loop_checksum.clone().unwrap_or_default(),
                generation.candidate_edge_version.unwrap_or_default(),
                generation.candidate_edge_checksum.clone().unwrap_or_default(),
                generation.observation_version.unwrap_or_default(),
                generation.observation_checksum.clone().unwrap_or_default(),
            ));
        }
    }
    let mut digest = Sha256::new();
    for loop_key in &loops {
        digest.update(loop_key.as_bytes());
        digest.update(b"\n");
    }
    for generation in &generations {
        digest.update(generation.as_bytes());
        digest.update(b"\n");
    }
    let fingerprint = hex::encode(digest.finalize());
    format!(
        "{REVIEW_PUBLISH_OPERATION}/{network}/{chain_id}/loops/{}/{round_id}",
        &fingerprint[..32]
    )
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
        // The generation the measurement was made under travels with the
        // evidence.  The store files the review under that generation and
        // not under the one the catalog holds when the review lands, so a
        // measurement is never relabelled as a newer one.
        closed_loop_generation: measured_generation(catalog, loop_key),
        static_preview: static_preview(route, measured_at, decimals),
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

/// The generation one closed loop was measured under.
///
/// The generation of the catalog names the whole materialization, and the
/// candidate edge and the observation name the two libraries the loop was
/// built from.  A closed loop the catalog does not publish has no
/// generation: a review that names one would borrow the generation of a
/// loop it never measured.
fn measured_generation(
    catalog: &ClosedLoopCatalog,
    loop_key: &str,
) -> Option<ClosedLoopGenerationEvidence> {
    let identity = catalog.identity_by_key(loop_key)?;
    let upstream = catalog.upstream();
    Some(ClosedLoopGenerationEvidence {
        closed_loop_version: upstream.map(|value| value.closed_loop_version),
        closed_loop_checksum: upstream.and_then(|value| value.materialized_checksum.clone()),
        candidate_edge_version: identity.candidate_edge_version,
        candidate_edge_checksum: identity.candidate_edge_checksum.clone(),
        observation_version: identity.observation_version,
        observation_checksum: identity.observation_checksum.clone(),
    })
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
fn static_preview(route: &Value, measured_at: &str, decimals: Option<i64>) -> Value {
    json!({
        "source": "native_rust_review",
        "measuredAt": measured_at,
        "status": route.get("status").cloned().unwrap_or(Value::Null),
        "routeSymbols": route.get("routeSymbols").cloned().unwrap_or(Value::Null),
        "routeAddresses": route.get("routeAddresses").cloned().unwrap_or(Value::Null),
        "selectedAmountRaw": route.get("selectedAmountRaw").cloned().unwrap_or(Value::Null),
        "selectedProfitRaw": route.get("selectedProfitRaw").cloned().unwrap_or(Value::Null),
        "attemptedCount": route.get("attemptedCount").cloned().unwrap_or(Value::Null),
        // The **ceiling** the ladder searched inside travels with the
        // review.  The selected amount is executable only when it stayed
        // inside the published single-tick capacity, so a stored review
        // that cannot show the ceiling cannot show that its own amount
        // was inside the bound the review enforces.
        "searchCeilingRaw": route
            .get("ladder")
            .and_then(|ladder| ladder.get("searchCeilingRaw"))
            .or_else(|| route.get("searchCeilingRaw"))
            .cloned()
            .unwrap_or(Value::Null),
        "searchCeilingBasis": route
            .get("ladder")
            .and_then(|ladder| ladder.get("searchCeilingBasis"))
            .or_else(|| route.get("searchCeilingBasis"))
            .cloned()
            .unwrap_or(Value::Null),
        // The amount the route was measured at, and the ladder it was
        // measured over, travel with the review.  Without them a stored
        // review carries no amount at all, and every reader has to
        // conclude that the route was never measured.
        "bestAmountCalculation": best_amount_calculation(route, decimals),
    })
}

/// The best-trade-amount evidence of one measured route.
///
/// The kernel owns the amount ladder: it measures the route at every size
/// the ladder tries, and it owns the difference between "the ladder never
/// ran" (a measurement gap) and "the ladder ran and no size paid" (a
/// measured zero).  That evidence used to be dropped on the way into the
/// Manager review, so every stored review looked like a review that never
/// measured a trade amount.  It travels with the review instead.
fn best_amount_calculation(route: &Value, decimals: Option<i64>) -> Value {
    let ladder = amount_ladder(route, decimals);
    let best_amount = route.get("bestAmount").cloned().unwrap_or(Value::Null);
    // The single-tick search and the cross-tick search are two different
    // measurements, and only the first one produced the amount above.  The
    // cross-tick search is started by the kernel **only** when the
    // single-tick search found a profitable size, so a route that paid at
    // no size has no cross-tick result at all.  The evidence below states
    // the two searches separately, so a reader never has to guess which one
    // a number belongs to.
    let tick = tick_search_evidence(route);
    let confirmed = route
        .get("bestAmountConfirmed")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    // The kernel marks the amount as missing until the ladder contributes a
// usable probe record, so a route whose ladder never ran carries no amount
// at all.  The default here is the same one: **no measurement means the
// amount is missing**, never "not missing but empty".
    let missing = route
        .get("bestAmountMissing")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let measured = route
        .get("bestAmountMeasured")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    // The kernel publishes the diagnosis of a ladder that never probed on
    // the **ladder payload** (the route's "ladder" member), not on the
    // route itself: the route carries the measurement, the ladder carries
    // the reason the measurement does not exist.  A publisher that reads
    // the route alone therefore cannot tell "the ladder was blocked
    // before it probed" apart from "the measurement was dropped on the
    // way in", and the reviewer is sent after a measurement that was
    // never broken.  The ladder payload is the kernel's own record, so it
    // is read from there; the flat shape stays a fallback for a payload
    // that carries the marker on the route itself.
    let ladder_payload = route.get("ladder").filter(|value| value.is_object());
    let ladder_blocked = route
        .get("ladderBlocked")
        .and_then(Value::as_bool)
        .or_else(|| {
            ladder_payload
                .and_then(|payload| payload.get("ladderBlocked"))
                .and_then(Value::as_bool)
        })
        .unwrap_or(false);
    let ladder_blocked_reason = route
        .get("ladderBlockedReason")
        .filter(|value| !value.is_null())
        .cloned()
        .or_else(|| {
            ladder_payload
                .and_then(|payload| payload.get("ladderBlockedReason"))
                .filter(|value| !value.is_null())
                .cloned()
        })
        .unwrap_or(Value::Null);
    // The ladder's own status says **which** block it was, and the kernel's
    // repair target says **which manager** publishes what the ladder
    // needs.  Both travel with the review so the reviewer calls the right
    // tool instead of the review manager.
    let ladder_status = ladder_payload
        .and_then(|payload| payload.get("status"))
        .filter(|value| !value.is_null())
        .cloned()
        .or_else(|| route.get("status").filter(|value| !value.is_null()).cloned())
        .unwrap_or(Value::Null);
    let repair_target = ladder_payload
        .and_then(|payload| payload.get("repairTarget"))
        .filter(|value| !value.is_null())
        .cloned()
        .unwrap_or(Value::Null);
    // One sentence that says why there is no amount at all.  The block
    // reason wins, because it names the concrete limit; otherwise the
    // route's own error (or the run's error) is the next best witness.
    let missing_reason = if !ladder_blocked_reason.is_null() {
        Some(ladder_blocked_reason.clone())
    } else {
        route
            .get("error")
            .filter(|value| !value.is_null())
            .cloned()
            .or_else(|| {
                route
                    .get("runArbitrage")
                    .and_then(|run| run.get("error"))
                    .filter(|value| !value.is_null())
                    .cloned()
            })
            .or_else(|| route.get("errorKind").filter(|value| !value.is_null()).cloned())
    };
    json!({
        "method": "native_rust_review_ladder",
        "bestAmount": best_amount,
        "bestAmountRaw": best_amount,
        "bestAmountAmount": decimals.and_then(|value| signed_raw_to_amount(&best_amount, value)),
        "bestAmountConfirmed": confirmed,
        "selectionConfirmed": confirmed,
        "bestAmountMissing": missing,
        "bestAmountMeasured": measured,
        "bestAmountSelectionBasis": route
            .get("bestAmountSelectionBasis")
            .cloned()
            .unwrap_or(Value::Null),
        "bestAmountZeroReason": route.get("bestAmountZeroReason").cloned().unwrap_or(Value::Null),
        "initialProbeAmount": route.get("initialProbeAmount").cloned().unwrap_or(Value::Null),
        "initialAmountRaw": route.get("initialAmountRaw").cloned().unwrap_or(Value::Null),
        "requestedAmount": route.get("selectedAmountRaw").cloned().unwrap_or(Value::Null),
        "requestedSymbol": borrow_symbol(route),
        // A route the capacity blocked before it probed anything carries the
        // reason with it, so a reader can tell "the ladder never ran"
        // apart from "the measurement was dropped".
        "bestAmountLadderBlocked": ladder_blocked,
        "bestAmountLadderBlockedReason": ladder_blocked_reason,
        "bestAmountLadderStatus": ladder_status,
        "bestAmountRepairTarget": repair_target,
        "bestAmountMissingReason": missing_reason.unwrap_or(Value::Null),
        "probeCount": ladder.len(),
        "probeRecords": ladder,
        // The single-tick result.  A measured zero is a **measurement**
        // ("no size of the single-tick domain paid"), never a missing
        // amount.
        "singleTickBestAmountRaw": tick.single_tick_best_amount_raw,
        "singleTickProfitable": tick.single_tick_profitable,
        // The cross-tick result.  When the cross-tick search did not run,
        // its best amount is the **single-tick** one: the cross-tick search
        // has no own answer, and the reader learns from the single-tick
        // amount alone that the single tick paid nothing.  The reason says
        // why the search was not started.
        "crossTickStarted": tick.cross_tick_started,
        "crossTickReason": tick.cross_tick_reason,
        "crossTickBestAmountRaw": tick.cross_tick_best_amount_raw,
        "crossTickBestAmountBasis": tick.cross_tick_best_amount_basis,
        "crossTickStatus": tick.cross_tick_status,
        "crossTickReview": tick.cross_tick_review,
        "crossTickSearch": tick.cross_tick_search,
        "searchMode": tick.search_mode,
    })
}

/// The single-tick and the cross-tick measurement of one route.
///
/// The kernel measures a route with two searches.  The **single tick**
/// search starts at the amount the single-tick capacity produces and
/// searches inside the folded single-tick capacity.  The **cross tick**
/// search continues from the single-tick optimum and searches inside the
/// folded depth capacity, and it is only started when the single-tick
/// search found a profitable size: a single-tick best amount of zero never
/// starts it (`single_tick_best_amount_zero`).
///
/// The two searches are therefore two different measurements, and a review
/// that published only one number mixed them.  A measured single-tick zero
/// used to be compared with the single-tick start as if it were an amount,
/// and the comparison answered "no" for a route that had never produced a
/// trade amount at all.  The evidence below keeps the two apart:
///
/// * `single_tick_best_amount_raw` is the single-tick answer.  It is the
///   measured zero `0x0` when the ladder priced the route and no size paid,
///   and it is null when the ladder never ran at all.
/// * `single_tick_profitable` says whether the single-tick search paid.
/// * `cross_tick_started` says whether the cross-tick search ran, and
///   `cross_tick_reason` says why it did not.
/// * `cross_tick_best_amount_raw` is the cross-tick answer when the search
///   ran, and the **single-tick** answer when it did not, with
///   `cross_tick_best_amount_basis` naming which of the two it is.
struct TickSearchEvidence {
    search_mode: Value,
    single_tick_best_amount_raw: Value,
    single_tick_profitable: bool,
    cross_tick_started: Option<bool>,
    cross_tick_reason: Value,
    cross_tick_best_amount_raw: Value,
    cross_tick_best_amount_basis: Value,
    cross_tick_status: Value,
    cross_tick_review: Value,
    cross_tick_search: Value,
}

/// Read the single-tick and the cross-tick measurement of one route.
fn tick_search_evidence(route: &Value) -> TickSearchEvidence {
    let ladder = route.get("ladder").filter(|value| value.is_object());
    // The cross-tick verdict is published by the kernel on the ladder
    // payload; the flat shape stays a fallback for a route that carries it
    // on the route itself.
    let cross_tick_review = ladder
        .and_then(|payload| payload.get("crossTickReview"))
        .filter(|value| value.is_object())
        .or_else(|| route.get("crossTickReview").filter(|value| value.is_object()))
        .cloned()
        .unwrap_or(Value::Null);
    let search_mode = ladder
        .and_then(|payload| payload.get("searchMode"))
        .filter(|value| !value.is_null())
        .cloned()
        .or_else(|| route.get("searchMode").filter(|value| !value.is_null()).cloned())
        .unwrap_or(Value::Null);
    let selection_basis = route
        .get("bestAmountSelectionBasis")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let measured = route
        .get("bestAmountMeasured")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let probes = route.get("attempts").and_then(Value::as_array);
    let probe_count = probes.map(|rows| rows.len()).unwrap_or(0);
    let profitable_probe = probes
        .map(|rows| {
            rows.iter()
                .any(|row| row.get("profitable").and_then(Value::as_bool) == Some(true))
        })
        .unwrap_or(false);
    let single_tick_profitable =
        profitable_probe || selection_basis == "native_rust_ladder_profitable_probe";
    let single_tick_best_amount_raw = if single_tick_profitable {
        route
            .get("bestAmount")
            .filter(|value| !value.is_null())
            .cloned()
            .or_else(|| {
                ladder
                    .and_then(|payload| payload.get("singleTickBestAmountRaw"))
                    .filter(|value| !value.is_null())
                    .cloned()
            })
            .unwrap_or(Value::Null)
    } else if measured || probe_count > 0 {
        // The ladder priced the route and no size paid: a **measured zero**.
        json!("0x0")
    } else {
        // The ladder never ran, so there is no single-tick answer at all.
        Value::Null
    };
    // The kernel's verdict is the only record of the decision, so an absent
    // verdict is **unknown** and never a "did not start".
    let cross_tick_started = cross_tick_review.get("started").and_then(Value::as_bool);
    let cross_tick_reason = cross_tick_review
        .get("reason")
        .filter(|value| !value.is_null())
        .cloned()
        .unwrap_or(Value::Null);
    let cross_tick_search = cross_tick_review
        .get("search")
        .filter(|value| value.is_object())
        .cloned();
    let (cross_tick_best_amount_raw, cross_tick_best_amount_basis, cross_tick_status) =
        if cross_tick_started == Some(true) {
            let amount = cross_tick_search
                .as_ref()
                .and_then(|search| search.get("bestAmount").filter(|value| !value.is_null()))
                .or_else(|| {
                    cross_tick_search
                        .as_ref()
                        .and_then(|search| search.get("selectedAmountRaw"))
                        .filter(|value| !value.is_null())
                })
                .cloned()
                .unwrap_or(Value::Null);
            (
                amount,
                json!("cross_tick_search"),
                cross_tick_search
                    .as_ref()
                    .and_then(|search| search.get("status").cloned())
                    .unwrap_or(Value::Null),
            )
        } else {
            // The cross-tick search was not started, so it has no own
            // answer.  The cross-tick best amount is the single-tick one,
            // and the reason says why the search never ran.
            (
                single_tick_best_amount_raw.clone(),
                json!("single_tick_best_amount"),
                Value::Null,
            )
        };
    // The published cross-tick verdict carries the seed, the capacity and
    // the single-tick numbers, but not the whole search payload: the search
    // is published as its own summary.
    let mut published_review = cross_tick_review.clone();
    if let Some(object) = published_review.as_object_mut() {
        object.remove("search");
    }
    let cross_tick_search_summary = cross_tick_search.as_ref().map(|search| {
        json!({
            "status": search.get("status").cloned().unwrap_or(Value::Null),
            "searchMode": search.get("searchMode").cloned().unwrap_or(Value::Null),
            "selectedAmountRaw": search.get("selectedAmountRaw").cloned().unwrap_or(Value::Null),
            "selectedProfitRaw": search.get("selectedProfitRaw").cloned().unwrap_or(Value::Null),
            "initialAmountRaw": search.get("initialAmountRaw").cloned().unwrap_or(Value::Null),
            "searchCeilingRaw": search.get("searchCeilingRaw").cloned().unwrap_or(Value::Null),
            "probeCount": search
                .get("attempts")
                .and_then(Value::as_array)
                .map(|rows| rows.len())
                .unwrap_or(0),
            "attempts": search.get("attempts").cloned().unwrap_or_else(|| json!([])),
        })
    });
    TickSearchEvidence {
        search_mode,
        single_tick_best_amount_raw,
        single_tick_profitable,
        cross_tick_started,
        cross_tick_reason,
        cross_tick_best_amount_raw,
        cross_tick_best_amount_basis,
        cross_tick_status,
        cross_tick_review: published_review,
        cross_tick_search: cross_tick_search_summary.unwrap_or(Value::Null),
    }
}

/// The measured amount ladder of one route.
///
/// Every rung is one size the chain priced, with the outcome it saw.  The
/// raw amount is the contract's own integer; the human amount is the same
/// number scaled by the borrow token's decimals, so a reader never has to
/// guess the scale of a stored amount.
fn amount_ladder(route: &Value, decimals: Option<i64>) -> Vec<Value> {
    let Some(rows) = route.get("attempts").and_then(Value::as_array) else {
        return Vec::new();
    };
    rows.iter()
        .filter(|row| row.is_object())
        .map(|row| {
            let amount_raw = row.get("amountRaw").cloned().unwrap_or(Value::Null);
            let profit_raw = row.get("profitRaw").cloned().unwrap_or(Value::Null);
            let amount = decimals.and_then(|value| signed_raw_to_amount(&amount_raw, value));
            let profit = decimals.and_then(|value| signed_raw_to_amount(&profit_raw, value));
            let profitable = row.get("profitable").and_then(Value::as_bool);
            let status = if profitable == Some(true) {
                "live_verified"
            } else {
                "not_profitable"
            };
            json!({
                "amountRaw": amount_raw,
                "amount": amount,
                "borrowAmount": amount,
                "requestedAmount": amount,
                "profitRaw": profit_raw,
                "profit": profit,
                "profitAmount": profit,
                "ok": row.get("ok").cloned().unwrap_or(Value::Null),
                "profitable": profitable,
                "status": status,
                "error": row.get("error").cloned().unwrap_or(Value::Null),
                "revertData": row.get("revertData").cloned().unwrap_or(Value::Null),
                "routeIndex": row.get("routeIndex").cloned().unwrap_or(Value::Null),
            })
        })
        .collect()
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

    /// **T-10**: the published review carries the **ceiling** the amount
    /// ladder searched inside.
    ///
    /// The selected amount is executable only when it stayed inside the
    /// published single-tick capacity.  The kernel publishes that ceiling
    /// on the ladder payload, but a stored review that dropped it could no
    /// longer show that its own amount was inside the bound the review
    /// enforces — the evidence of the bound would be gone with the round
    /// that produced it.
    #[test]
    fn the_published_review_carries_the_ceiling_the_ladder_searched_inside() {
        let mut payload = route(LOOP_KEY);
        payload["ladder"] = json!({
            "searchCeilingRaw": "0x1bc16d674ec80000",
            "searchCeilingBasis": "folded_single_tick_capacity",
            "attempts": [],
        });
        payload["attempts"] = json!([
            {
                "amountRaw": "0x0de0b6b3a7640000",
                "ok": true,
                "profitable": true,
                "profitRaw": "0x0de0b6b3a7640000",
                "routeIndex": 0
            }
        ]);
        payload["bestAmount"] = json!("0x0de0b6b3a7640000");
        payload["bestAmountConfirmed"] = json!(true);
        payload["bestAmountMissing"] = json!(false);
        payload["bestAmountMeasured"] = json!(true);
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        assert_eq!(
            evidence
                .static_preview
                .get("searchCeilingRaw")
                .and_then(Value::as_str),
            Some("0x1bc16d674ec80000")
        );
        assert_eq!(
            evidence
                .static_preview
                .get("searchCeilingBasis")
                .and_then(Value::as_str),
            Some("folded_single_tick_capacity")
        );
    }

    /// **T-9**: the published review carries the amount the route was
    /// measured at **and** the ladder it was measured over.
    ///
    /// The amount used to be dropped on the way into the Manager review, so
    /// every stored review looked like a review that never measured a
    /// trade amount.
    #[test]
    fn the_published_review_carries_the_measured_amount_and_its_ladder() {
        let mut payload = route(LOOP_KEY);
        payload["attempts"] = json!([
            {
                "amountRaw": "0x0de0b6b3a7640000",
                "ok": true,
                "profitable": true,
                "profitRaw": "0x0de0b6b3a7640000",
                "routeIndex": 0
            },
            {
                "amountRaw": "0x1bc16d674ec80000",
                "ok": true,
                "profitable": false,
                "profitRaw": "-0x0de0b6b3a7640000",
                "routeIndex": 1
            }
        ]);
        payload["bestAmount"] = json!("0x0de0b6b3a7640000");
        payload["bestAmountConfirmed"] = json!(true);
        payload["bestAmountMissing"] = json!(false);
        payload["bestAmountMeasured"] = json!(true);
        payload["bestAmountSelectionBasis"] = json!("native_rust_ladder_profitable_probe");
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        let calculation = evidence
            .static_preview
            .get("bestAmountCalculation")
            .and_then(Value::as_object)
            .expect("the published review carries the amount calculation");
        assert_eq!(
            calculation.get("bestAmount").and_then(Value::as_str),
            Some("0x0de0b6b3a7640000")
        );
        assert_eq!(
            calculation.get("bestAmountMeasured").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            calculation.get("bestAmountMissing").and_then(Value::as_bool),
            Some(false)
        );
        assert_eq!(
            calculation.get("probeCount").and_then(Value::as_u64),
            Some(2)
        );
        let probes = calculation
            .get("probeRecords")
            .and_then(Value::as_array)
            .expect("the published review carries the measured ladder");
        assert_eq!(probes.len(), 2);
        // The raw amount is the contract's own integer and the human amount
        // is the same number in the borrow token's decimals.
        assert_eq!(
            probes[0].get("amountRaw").and_then(Value::as_str),
            Some("0x0de0b6b3a7640000")
        );
        assert_eq!(probes[0].get("amount").and_then(Value::as_f64), Some(1.0));
        // A measured loss stays a measurement: a negative profit is not a
        // missing profit.
        assert_eq!(probes[1].get("profit").and_then(Value::as_f64), Some(-1.0));
        assert_eq!(
            probes[1].get("status").and_then(Value::as_str),
            Some("not_profitable")
        );
    }

    /// **T-10**: a route whose ladder **never ran** carries the missing
    /// marker and the reason it was blocked.
    ///
    /// "The ladder never ran" is a measurement gap; "the measurement was
    /// dropped" is a publisher defect.  A review that says only "not
    /// missing but empty" cannot tell the two apart, so the missing marker
    /// follows the kernel's own default and the block reason travels with
    /// it.
    ///
    /// The fixture uses the **kernel's own shape**: the diagnosis sits on the
    /// route's "ladder" member, because the route carries the measurement
    /// and the ladder carries the reason.  A publisher that reads the route
    /// alone would lose the reason and report a blocked ladder as a dropped
    /// measurement.
    #[test]
    fn a_route_whose_ladder_never_ran_carries_the_missing_marker() {
        let mut payload = route(LOOP_KEY);
        payload["attempts"] = json!([]);
        payload["status"] = json!("not_profitable_after_costs");
        payload["ladder"] = json!({
            "status": "single_tick_capacity_below_min_probe",
            "ladderBlocked": true,
            "ladderBlockedReason": "single_tick_capacity_below_min_probe",
            "repairTarget": "closed_loop_manager",
            "attempts": [],
        });
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        let calculation = evidence
            .static_preview
            .get("bestAmountCalculation")
            .and_then(Value::as_object)
            .expect("the published review carries the amount calculation");
        assert_eq!(
            calculation.get("bestAmountMissing").and_then(Value::as_bool),
            Some(true)
        );
        assert!(calculation.get("bestAmount").unwrap().is_null());
        assert_eq!(
            calculation.get("probeCount").and_then(Value::as_u64),
            Some(0)
        );
        assert_eq!(
            calculation.get("bestAmountLadderBlocked").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            calculation.get("bestAmountLadderBlockedReason").and_then(Value::as_str),
            Some("single_tick_capacity_below_min_probe")
        );
        assert_eq!(
            calculation.get("bestAmountLadderStatus").and_then(Value::as_str),
            Some("single_tick_capacity_below_min_probe")
        );
        // The kernel names the manager that publishes what the ladder
        // needs; the reviewer then calls that manager instead of the review
        // manager, which cannot repair a missing capacity.
        assert_eq!(
            calculation.get("bestAmountRepairTarget").and_then(Value::as_str),
            Some("closed_loop_manager")
        );
        assert_eq!(
            calculation.get("bestAmountMissingReason").and_then(Value::as_str),
            Some("single_tick_capacity_below_min_probe")
        );
    }

    /// **T-11**: a route whose ladder ran but whose chain call reverted
    /// says why there is no amount, in the amount block itself.
    ///
    /// The kernel never measured an amount here, so the review carries no
    /// amount.  It still has to say **why**: the run reverted on the chain,
    /// and a reader that only sees "amount missing" cannot tell that apart
    /// from a dropped measurement.
    #[test]
    fn a_route_whose_run_reverted_states_why_there_is_no_amount() {
        let mut payload = route(LOOP_KEY);
        payload["status"] = json!("not_profitable_after_costs");
        payload["attempts"] = json!([]);
        payload["error"] = json!("execution reverted");
        payload["runArbitrage"] = json!({
            "ok": false,
            "executable": false,
            "error": "execution reverted",
            "revertSelector": "0xb20c2204",
        });
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        );
        let calculation = evidence
            .static_preview
            .get("bestAmountCalculation")
            .and_then(Value::as_object)
            .expect("the published review carries the amount calculation");
        assert_eq!(
            calculation.get("bestAmountMissing").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            calculation.get("probeCount").and_then(Value::as_u64),
            Some(0)
        );
        // The ladder was not blocked by a capacity; the chain refused the
        // route.  The reason says so, and it is not a capacity repair.
        assert_eq!(
            calculation.get("bestAmountLadderBlocked").and_then(Value::as_bool),
            Some(false)
        );
        assert_eq!(
            calculation.get("bestAmountMissingReason").and_then(Value::as_str),
            Some("execution reverted")
        );
        assert!(calculation
            .get("bestAmountRepairTarget")
            .unwrap()
            .is_null());
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
                    directional_pool_ids_json, backup_directional_pool_ids_json,
                    hop_candidate_counts_json,
                    hop_candidate_combination_count, recommendation_basis,
                    recommended_first_priority, candidate_edge_version,
                    candidate_edge_checksum, observation_version,
                    observation_checksum, status, source,
                    single_tick_capacity_anchor_raw,
                    single_tick_capacity_binding_directional_pool_id,
                    created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 'route:v1',
                           'robinhood:4663:0x0000000000000000000000000000000000000000',
                           '0x0000000000000000000000000000000000000000', 'USDG',
                           'robinhood:4663:0x1111111111111111111111111111111111111111',
                           'robinhood:4663:0x2222222222222222222222222222222222222222',
                           'USDG -> AMZN -> TA -> USDG',
                           '[\"0x0000000000000000000000000000000000000000\",\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x0000000000000000000000000000000000000000\"]',
                           '[\"USDG\",\"AMZN\",\"TA\",\"USDG\"]', 'pool-path:1',
                           '[\"dpool:v1:robinhood:4663:core4:pool-a:1\",\"dpool:v1:robinhood:4663:core4:pool-yz:1\",\"dpool:v1:robinhood:4663:core4:pool-d:1\"]', '{}', '{\"1\":1}',
                           1, 'fixture', 1, 7, 'sha256:candidate-edge', 4,
                           'sha256:observation', 'active', 'fixture',
                           '1000000000000000', 'dpool:v1:robinhood:4663:core4:pool-yz:1',
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

    /// The second closed loop of the two-loop fixture.
    const LOOP_KEY_B: &str = "0x3333333333333333333333333333333333333333:0x4444444444444444444444444444444444444444:0x5555555555555555555555555555555555555555";

    /// A closed loop the Manager library does not publish at all.
    const LOOP_KEY_C: &str = "0x6666666666666666666666666666666666666666:0x7777777777777777777777777777777777777777:0x8888888888888888888888888888888888888888";

    /// The manager fixture with **two** closed loops.
    ///
    /// Two loops make the independence of a per-loop publication
    /// observable: a loop the measurement no longer describes must not
    /// keep the other loop from being published.
    fn manager_database_with_two_loops() -> (tempfile::TempDir, String) {
        let (directory, path) = manager_database();
        let connection = rusqlite::Connection::open(&path).unwrap();
        let path_addresses = serde_json::to_string(&json!([
            "0x3333333333333333333333333333333333333333",
            "0x4444444444444444444444444444444444444444",
            "0x5555555555555555555555555555555555555555",
            "0x3333333333333333333333333333333333333333"
        ]))
        .unwrap();
        let path_symbols = serde_json::to_string(&json!(["BBB", "CCC", "DDD", "BBB"])).unwrap();
        let directional_pool_ids = serde_json::to_string(&json!([
            "dpool:v1:robinhood:4663:core4:pool-b:1",
            "dpool:v1:robinhood:4663:core4:pool-yz:1",
            "dpool:v1:robinhood:4663:core4:pool-d:1"
        ]))
        .unwrap();
        let hop_candidate_counts = serde_json::to_string(&json!({"1": 1})).unwrap();
        connection
            .execute(
                "INSERT INTO manager_v4_closed_loops (
                    network, chain_id, protocol, scope_key, loop_key, route_key_id,
                    anchor_token_key_id, anchor_address, anchor_symbol,
                    token_y_key_id, token_z_key_id, path_text,
                    path_addresses_json, path_symbols_json, pool_path_key,
                    directional_pool_ids_json, backup_directional_pool_ids_json,
                    hop_candidate_counts_json,
                    hop_candidate_combination_count, recommendation_basis, recommended_first_priority,
                    candidate_edge_version, candidate_edge_checksum, observation_version,
                    observation_checksum, status, source,
                    single_tick_capacity_anchor_raw, single_tick_capacity_binding_directional_pool_id,
                    created_at, updated_at
                 ) VALUES (?1, ?2, 'uniswap_v4', 'core4', ?3, 'route:v1',
                           'robinhood:4663:0x3333333333333333333333333333333333333333',
                           '0x3333333333333333333333333333333333333333', 'BBB',
                           'robinhood:4663:0x4444444444444444444444444444444444444444',
                           'robinhood:4663:0x5555555555555555555555555555555555555555',
                           'BBB -> CCC -> DDD -> BBB',
                           ?4, ?5, 'pool-path:2',
                           ?6, '{}', ?7,
                           1, 'fixture', 1, 7, 'sha256:candidate-edge', 4,
                           'sha256:observation', 'active', 'fixture',
                           '2000000000000000', 'dpool:v1:robinhood:4663:core4:pool-yz:1',
                           'fixture', 'fixture')",
                rusqlite::params![
                    NETWORK,
                    CHAIN_ID,
                    LOOP_KEY_B,
                    path_addresses,
                    path_symbols,
                    directional_pool_ids,
                    hop_candidate_counts
                ],
            )
            .unwrap();
        drop(connection);
        (directory, path)
    }

    /// One closed loop as the runtime catalog holds it.
    ///
    /// The row carries the same facts the Manager publishes, so the
    /// identity the two sides hold can be compared with
    /// `is_same_loop`.
    fn identity_row(
        loop_key: &str,
        path_addresses_json: &str,
        pool_ids_json: &str,
        capacity: &str,
        binding: Option<&str>,
    ) -> Value {
        json!({
            "loop_key": loop_key,
            "path_addresses_json": path_addresses_json,
            "pool_ids_json": pool_ids_json,
            "single_tick_capacity_anchor_raw": capacity,
            "single_tick_capacity_binding_pool_id": binding,
            "candidate_edge_version": 7,
            "candidate_edge_checksum": "sha256:candidate-edge",
            "observation_version": 4,
            "observation_checksum": "sha256:observation"
        })
    }

    /// The address path of the first closed loop.
    const PATH_A: &str = "[\"0x0000000000000000000000000000000000000000\",\"0x1111111111111111111111111111111111111111\",\"0x2222222222222222222222222222222222222222\",\"0x0000000000000000000000000000000000000000\"]";

    /// The address path of the second closed loop.
    const PATH_B: &str = "[\"0x3333333333333333333333333333333333333333\",\"0x4444444444444444444444444444444444444444\",\"0x5555555555555555555555555555555555555555\",\"0x3333333333333333333333333333333333333333\"]";

    /// The runtime catalog of the two-loop fixture.
    ///
    /// The catalog holds both loops with the identity the Manager
    /// publishes, so a publication of both loops succeeds.
    fn measured_catalog_of_two_loops() -> ClosedLoopCatalog {
        crate::closed_loop_identity::catalog_from_rows(&[
            identity_row(
                LOOP_KEY,
                PATH_A,
                "[\"pool-a\",\"pool-yz\",\"pool-d\"]",
                "1000000000000000",
                Some("pool-yz"),
            ),
            identity_row(
                LOOP_KEY_B,
                PATH_B,
                "[\"pool-b\",\"pool-yz\",\"pool-d\"]",
                "2000000000000000",
                Some("pool-yz"),
            ),
        ])
    }

    /// The runtime catalog in which the second loop was **rebuilt**.
    ///
    /// The measurement was made against another identity of the second
    /// loop, so the publication has to drop it: a review of the loop that
    /// no longer exists is not a review of the loop that replaced it.
    fn measured_catalog_with_a_rebuilt_second_loop() -> ClosedLoopCatalog {
        crate::closed_loop_identity::catalog_from_rows(&[
            identity_row(
                LOOP_KEY,
                PATH_A,
                "[\"pool-a\",\"pool-yz\",\"pool-d\"]",
                "1000000000000000",
                Some("pool-yz"),
            ),
            identity_row(
                LOOP_KEY_B,
                PATH_B,
                "[\"pool-b\",\"pool-x\",\"pool-d\"]",
                "3000000000000000",
                Some("pool-x"),
            ),
        ])
    }

    /// The measured route of the second closed loop.
    fn route_b() -> Value {
        json!({
            "closedLoopKey": LOOP_KEY_B,
            "routeSymbols": ["BBB", "CCC", "DDD", "BBB"],
            "routeAddresses": [
                "0x3333333333333333333333333333333333333333",
                "0x4444444444444444444444444444444444444444",
                "0x5555555555555555555555555555555555555555",
                "0x3333333333333333333333333333333333333333"
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

    /// The evidence of the second closed loop.
    fn evidence_of_b(catalog: &ClosedLoopCatalog) -> ReviewEvidenceInput {
        route_evidence(
            &route_b(),
            LOOP_KEY_B,
            catalog,
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        )
    }

    /// The evidence of the first closed loop, measured under a catalog.
    ///
    /// The evidence is built from the catalog the measurement was made
    /// under, because the generation of the measurement is read out of it.
    fn evidence_of_in(catalog: &ClosedLoopCatalog) -> ReviewEvidenceInput {
        route_evidence(
            &route(LOOP_KEY),
            LOOP_KEY,
            catalog,
            None,
            &|_| Some(18),
            "2026-11-24T00:00:00Z",
        )
    }

    /// **T-11**: a per-loop publication publishes the measured loop and
    /// asks for no upstream generation of its own.
    ///
    /// The generation the measurement was made under travels with the
    /// evidence, so a closed-loop library that moved on between the
    /// measurement and the publication does not stop the publication.
    #[test]
    fn a_per_loop_publication_publishes_the_measured_loop_under_its_own_generation() {
        let (_directory, path) = manager_database();
        let measured = measured_catalog_of_two_loops().with_upstream(Some(ClosedLoopUpstream {
            closed_loop_version: 7,
            materialized_checksum: Some("sha256:measured".to_owned()),
        }));
        let outcome = publish_review_loops(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of_in(&measured)],
            &measured,
            "r1-1",
        )
        .expect("the measured loop is still published");
        assert!(outcome.published);
        assert_eq!(outcome.loop_count, 1);
        assert_eq!(outcome.evidence_count, 1);
        assert!(outcome.dropped_loop_keys.is_empty());
        let connection = rusqlite::Connection::open(&path).unwrap();
        let (rows, version): (i64, i64) = connection
            .query_row(
                "SELECT COUNT(*), MAX(closed_loop_version) FROM manager_v4_reviews WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(rows, 1, "one review row per measured closed loop");
        assert_eq!(
            version, 7,
            "the review is filed under the generation it was measured against, not under the one the library holds now"
        );
    }

    /// The consumers rebuilt, and the Manager is told about it.
    ///
    /// The publication was measured against the generation the Manager
    /// publishes now, so the consumers are no longer behind and the flag
    /// that says they are may go.
    #[test]
    fn a_publication_tells_the_manager_that_the_consumers_rebuilt() {
        let (_directory, path) = manager_database();
        mark_the_consumers_behind(&path);
        let measured = measured_catalog_of_two_loops().with_upstream(Some(ClosedLoopUpstream {
            closed_loop_version: 19,
            materialized_checksum: Some("sha256:closed-loop".to_owned()),
        }));
        publish_review_loops(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of_in(&measured)],
            &measured,
            "r-ack-1",
        )
        .expect("the measured loop is published");
        assert_eq!(
            stored_consumers_need_rebuild(&path),
            0,
            "the consumers rebuilt against the generation the library publishes \
             now, so the flag must be cleared"
        );
    }

    /// A publication of another generation leaves the flag set.
    ///
    /// The consumers measured against a generation the library has already
    /// left.  They are behind, and a flag that this publication clears is a
    /// flag that lies.
    #[test]
    fn a_publication_of_another_generation_leaves_the_flag_set() {
        let (_directory, path) = manager_database();
        mark_the_consumers_behind(&path);
        let measured = measured_catalog_of_two_loops().with_upstream(Some(ClosedLoopUpstream {
            closed_loop_version: 7,
            materialized_checksum: Some("sha256:measured".to_owned()),
        }));
        publish_review_loops(
            &path,
            NETWORK,
            CHAIN_ID,
            vec![evidence_of_in(&measured)],
            &measured,
            "r-ack-2",
        )
        .expect("the measured loop is published");
        assert_eq!(
            stored_consumers_need_rebuild(&path),
            1,
            "the consumers are behind the generation the library publishes now, \
             so the flag must survive the publication"
        );
    }

    /// Tell the Manager that its consumers still have to rebuild.
    fn mark_the_consumers_behind(path: &str) {
        let connection = rusqlite::Connection::open(path).unwrap();
        connection
            .execute(
                "UPDATE manager_v4_closed_loop_library_state
                 SET consumers_need_rebuild = 1
                 WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
            )
            .unwrap();
    }

    /// What the Manager says about its consumers.
    fn stored_consumers_need_rebuild(path: &str) -> i64 {
        let connection = rusqlite::Connection::open(path).unwrap();
        connection
            .query_row(
                "SELECT consumers_need_rebuild FROM manager_v4_closed_loop_library_state
                 WHERE network = ?1 AND chain_id = ?2",
                rusqlite::params![NETWORK, CHAIN_ID],
                |row| row.get(0),
            )
            .unwrap()
    }

    /// **T-12**: one closed loop that cannot be published does not keep
    /// the other closed loops from being published.
    ///
    /// The evidence carries three closed loops: one the Manager still
    /// publishes with the same identity, one it publishes under another
    /// identity, and one it does not publish at all.  The first one is
    /// published, the other two are dropped and named.
    #[test]
    fn a_loop_that_cannot_be_published_does_not_block_the_other_loops() {
        let (_directory, path) = manager_database_with_two_loops();
        let measured = measured_catalog_with_a_rebuilt_second_loop();
        let evidence = vec![
            evidence_of_in(&measured),
            evidence_of_b(&measured),
            ReviewEvidenceInput {
                loop_key: LOOP_KEY_C.to_owned(),
                ..evidence_of_in(&measured)
            },
        ];
        let outcome = publish_review_loops(&path, NETWORK, CHAIN_ID, evidence, &measured, "r1-2")
            .expect("one measured loop is still published");
        assert_eq!(
            outcome.evidence_count, 1,
            "only the loop that can be published is reviewed"
        );
        assert_eq!(outcome.dropped_loop_keys.len(), 2, "both unusable loops are named");
        assert!(outcome
            .dropped_loop_keys
            .iter()
            .any(|key| key.eq_ignore_ascii_case(LOOP_KEY_B)));
        assert!(outcome
            .dropped_loop_keys
            .iter()
            .any(|key| key.eq_ignore_ascii_case(LOOP_KEY_C)));
        let connection = rusqlite::Connection::open(&path).unwrap();
        let loop_keys: Vec<String> = connection
            .prepare(
                "SELECT loop_key FROM manager_v4_reviews WHERE network = ?1 AND chain_id = ?2",
            )
            .unwrap()
            .query_map(rusqlite::params![NETWORK, CHAIN_ID], |row| row.get(0))
            .unwrap()
            .map(|row| row.unwrap())
            .collect();
        assert_eq!(loop_keys.len(), 1, "only the published loop has a review row");
        assert!(loop_keys[0].eq_ignore_ascii_case(LOOP_KEY));
    }

    /// **D-244**: the published review keeps the single-tick and the
    /// cross-tick measurement apart.
    ///
    /// A route whose ladder priced several sizes and paid at none of them
    /// has a **measured** single-tick zero, and the kernel never starts the
    /// cross-tick search for it.  The review says so, and it writes the
    /// single-tick amount into the cross-tick best amount, so a reader can
    /// tell from the amount alone that the single tick paid nothing.
    #[test]
    fn a_route_that_pays_at_no_size_publishes_the_two_searches_apart() {
        let mut payload = route(LOOP_KEY);
        payload["status"] = json!("not_profitable_after_costs");
        payload["runArbitrage"] = json!({
            "ok": false,
            "executable": false,
            "error": "NoProfitableRoute"
        });
        payload["attempts"] = json!([
            {"amountRaw": "0x64", "ok": false, "profitable": false, "profitRaw": "-0x1"},
            {"amountRaw": "0x32", "ok": false, "profitable": false, "profitRaw": "-0x1"}
        ]);
        payload["bestAmount"] = json!("0x0");
        payload["bestAmountConfirmed"] = json!(true);
        payload["bestAmountMissing"] = json!(false);
        payload["bestAmountMeasured"] = json!(true);
        payload["bestAmountSelectionBasis"] =
            json!("native_rust_ladder_no_profitable_size_measured");
        payload["ladder"] = json!({
            "searchMode": "external_ladder",
            "attempts": [],
            "crossTickReview": {
                "started": false,
                "reason": "single_tick_best_amount_zero",
                "singleTickBestAmountRaw": "0x0",
                "singleTickBestProfitRaw": "0x0"
            }
        });
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-28T00:00:00Z",
        );
        let calculation = evidence
            .static_preview
            .get("bestAmountCalculation")
            .expect("the amount evidence is published");
        assert_eq!(
            calculation
                .get("singleTickBestAmountRaw")
                .and_then(Value::as_str),
            Some("0x0"),
            "the single tick paid at no size"
        );
        assert_eq!(
            calculation.get("singleTickProfitable").and_then(Value::as_bool),
            Some(false)
        );
        assert_eq!(
            calculation.get("crossTickStarted").and_then(Value::as_bool),
            Some(false),
            "a single-tick best amount of zero never starts the cross tick"
        );
        assert_eq!(
            calculation.get("crossTickReason").and_then(Value::as_str),
            Some("single_tick_best_amount_zero")
        );
        assert_eq!(
            calculation
                .get("crossTickBestAmountRaw")
                .and_then(Value::as_str),
            Some("0x0"),
            "the cross-tick best amount is the single-tick one when the search never ran"
        );
        assert_eq!(
            calculation
                .get("crossTickBestAmountBasis")
                .and_then(Value::as_str),
            Some("single_tick_best_amount")
        );
    }

    /// **D-244**: a route whose single tick paid publishes the cross-tick
    /// result of its own.
    ///
    /// The cross-tick search is a second measurement, and its answer is the
    /// one the cross-tick best amount carries.  The basis says which search
    /// the number belongs to, so a measured zero of the cross-tick search is
    /// never read as a zero of the single tick.
    #[test]
    fn a_route_whose_single_tick_paid_publishes_the_cross_tick_result() {
        let mut payload = route(LOOP_KEY);
        payload["attempts"] = json!([
            {"amountRaw": "0x64", "ok": true, "profitable": true, "profitRaw": "0x2"}
        ]);
        payload["bestAmount"] = json!("0x64");
        payload["bestAmountConfirmed"] = json!(true);
        payload["bestAmountMissing"] = json!(false);
        payload["bestAmountMeasured"] = json!(true);
        payload["bestAmountSelectionBasis"] = json!("native_rust_ladder_profitable_probe");
        payload["ladder"] = json!({
            "searchMode": "external_ladder",
            "singleTickBestAmountRaw": "0x64",
            "attempts": [],
            "crossTickReview": {
                "started": true,
                "reason": "single_tick_profit_below_threshold",
                "seedAmountRaw": "0x64",
                "capacityRaw": "0xc8",
                "search": {
                    "status": "not_profitable_after_costs",
                    "searchMode": "cross_tick",
                    "selectedAmountRaw": "0x0",
                    "attempts": [
                        {"amountRaw": "0x64", "ok": false, "profitable": false}
                    ]
                }
            }
        });
        let evidence = route_evidence(
            &payload,
            LOOP_KEY,
            &catalog(),
            None,
            &|_| Some(18),
            "2026-11-28T00:00:00Z",
        );
        let calculation = evidence
            .static_preview
            .get("bestAmountCalculation")
            .expect("the amount evidence is published");
        assert_eq!(
            calculation.get("singleTickProfitable").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            calculation
                .get("singleTickBestAmountRaw")
                .and_then(Value::as_str),
            Some("0x64")
        );
        assert_eq!(
            calculation.get("crossTickStarted").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            calculation.get("crossTickReason").and_then(Value::as_str),
            Some("single_tick_profit_below_threshold")
        );
        assert_eq!(
            calculation
                .get("crossTickBestAmountRaw")
                .and_then(Value::as_str),
            Some("0x0"),
            "the cross-tick search paid at no size of its own domain"
        );
        assert_eq!(
            calculation
                .get("crossTickBestAmountBasis")
                .and_then(Value::as_str),
            Some("cross_tick_search")
        );
        assert_eq!(
            calculation
                .get("crossTickSearch")
                .and_then(|search| search.get("probeCount"))
                .and_then(Value::as_u64),
            Some(1),
            "the cross-tick measurement travels with the review"
        );
    }
}
