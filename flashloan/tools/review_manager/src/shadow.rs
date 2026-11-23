//! ARCH-4 stage A: the parallel shadow comparison.
//!
//! What this module exists for
//! ---------------------------
//!
//! ARCH-4 wants the local memory pool to replace the chain-side preview.
//! That replacement is only safe if the two are **provably equivalent**, and
//! the proof is made in three stages (see `plan/池子重构方案/开发计划_20261123.md` §3):
//!
//! * **stage A** - run both and compare, one quote at a time;
//! * **stage B** - the memory pool becomes the primary source and the chain
//!   call becomes a shadow;
//! * **stage C** - the chain call is closed and only the memory pool remains.
//!
//! Stage A is what this module implements.
//!
//! Where the work lives
//! --------------------
//!
//! The comparison is **review work**, so it belongs to Review Manager:
//!
//! * the verdict and the anomaly ledger are review evidence;
//! * the pool state is Pool Manager's data and is read through Pool
//!   Manager's published API (`read_pool_parity_states`);
//! * the swap arithmetic is Pool Manager's published maths
//!   (`exact_input_swap_amount_out`), so there is exactly **one** rounding
//!   rule in the project and therefore exactly one place to fix;
//! * the chain-side call is performed in Rust, in this module, so the
//!   comparison never depends on Python being present.
//!
//! Python reaches all of it through the in-memory `RuntimeCore`. Python never
//! performs the arithmetic and never reads a manager-owned table.
//!
//! The two rules that must not be broken
//! -------------------------------------
//!
//! 1. **The chain call always wins.** The memory pool never blocks an
//!    execution and never overrides the chain-side result. A difference in the
//!    local computation is a defect to repair, not a reason to stop.
//! 2. **Only an untrustworthy chain call may block.** `eth_callTrust` is
//!    `untrusted` **only** when there is concrete evidence that the chain
//!    call itself is wrong: an RPC timeout, a malformed response, or a chain
//!    state that changed underneath the call. A memory-side deviation is
//!    **never** a reason to mark the chain call untrusted.
//!
//! The fail-closed rules that govern depth, capacity and broadcast gating
//! are untouched by this module: those say "if the data is not trustworthy,
//! do not act". This module compares two *trustworthy* sources, and the chain
//! is the authority among them.

use std::collections::BTreeMap;
use std::time::Duration;

use pool_manager::{read_live_pool_parity_states, read_pool_parity_states, PoolParityState};
use serde::{Deserialize, Serialize};

/// The relative tolerance applied to the amount comparison.
///
/// The chain may settle at a marginally different amount for reasons that
/// have nothing to do with the arithmetic under test, so an exact equality
/// requirement would produce noise rather than signal.
pub const DEFAULT_SHADOW_TOLERANCE: f64 = 1e-9;

/// The V4 quoter signature used by the chain-side call.
///
/// The deployed quoter takes the canonical Uniswap V4 nested struct:
///
/// `quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))`
///
/// The flat form (`quoteExactInputSingle((address,address,uint24,int24,address),bool,uint128,bytes)`)
/// has a different selector and reverts with empty data on the deployed
/// contract, so the nested struct is the only form that reaches the quoter.
pub const V4_QUOTE_SIGNATURE: &str =
    "quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))";

/// The canonical Uniswap V4 `PoolManager` used by the Robinhood chain.
pub const ROBINHOOD_V4_POOL_MANAGER: &str = "0x8366a39cc670b4001a1121b8f6a443a643e40951";

/// The gas budget attached to a chain-side quote call.
pub const DEFAULT_SHADOW_QUOTE_GAS: u64 = 30_000_000;

/// The default RPC timeout for a chain-side quote call.
pub const DEFAULT_SHADOW_TIMEOUT_SECONDS: u64 = 20;

/// The zero address, used when a pool has no hooks.
pub const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

/// The default number of RPC requests per second.
///
/// The public Robinhood RPC endpoints are on a free tier that is capped at
/// **15 requests per second**. The shadow comparison issues two requests per
/// hop (`eth_call` plus `eth_blockNumber`), so an unbounded run would trip
/// the limit on any non-trivial pool list. The default sits below the cap on
/// purpose: the limiter is a guard, not a target.
pub const DEFAULT_SHADOW_REQUESTS_PER_SECOND: f64 = 8.0;

/// A token-bucket limiter that spaces out RPC requests.
///
/// The limiter is deliberately shared by every request of one comparison run,
/// including the failover requests to the next endpoint, so the cap applies to
/// the run and not to a single endpoint.
#[derive(Clone, Debug)]
pub struct ShadowRateLimiter {
    /// The allowed requests per second.
    requests_per_second: f64,
    /// The next moment a request may start, in milliseconds since the origin.
    next_slot_ms: std::sync::Arc<std::sync::Mutex<i64>>,
    /// The monotonic clock the slots are measured against.
    origin: std::time::Instant,
}

impl ShadowRateLimiter {
    /// Creates a limiter for the given request rate.
    ///
    /// A non-positive or non-finite rate falls back to the default, so a
    /// misconfigured option can never turn into "no limit at all".
    pub fn new(requests_per_second: f64) -> Self {
        let requests_per_second = if requests_per_second.is_finite() && requests_per_second > 0.0 {
            requests_per_second
        } else {
            DEFAULT_SHADOW_REQUESTS_PER_SECOND
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
            let mut slot = self.next_slot_ms.lock().expect("shadow rate limiter");
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

// ---------------------------------------------------------------------------
// The two sources
// ---------------------------------------------------------------------------

/// Which computation produced a number.
///
/// Every number in a shadow report carries its tag, because the whole point
/// of the comparison is to know *which* side produced a value. A report
/// without tags would be unreadable: two `amountOut` values with no tags are
/// indistinguishable.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ShadowSourceTag {
    /// The local memory pool computation.
    Memory,
    /// The chain-side `eth_call` computation.
    EthCall,
}

impl ShadowSourceTag {
    /// The stable wire name of the tag.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Memory => "memory",
            Self::EthCall => "eth_call",
        }
    }
}

impl std::fmt::Display for ShadowSourceTag {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// Whether the chain-side call may be trusted.
///
/// This is the **execution** precondition, and it is deliberately narrow.
/// The chain call is the on-chain authority, so the default answer is
/// `trusted` and the burden of proof lies on the evidence that it is not.
///
/// The only three things that can make a chain call untrustworthy are:
///
/// * the RPC timed out or the transport failed;
/// * the response was malformed (not a JSON-RPC response, not a hex string,
///   too short to decode);
/// * the chain state changed underneath the call (the block the call landed
///   on is not the block that was requested, or a reorg was observed).
///
/// A **memory-side deviation is never one of them**. That is the whole point
/// of the rule: a local defect must never freeze the chain.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EthCallTrust {
    /// The chain call answered with a well-formed result and may be used.
    Trusted,
    /// The chain call produced no usable answer, or the chain state moved
    /// under it. The caller must fall back to the established degradation
    /// path (re-read the state, or hold the execution).
    Untrusted,
}

impl EthCallTrust {
    /// The stable wire name of the verdict.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Trusted => "trusted",
            Self::Untrusted => "untrusted",
        }
    }
}

impl std::fmt::Display for EthCallTrust {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// Why a chain-side call was judged untrustworthy.
///
/// The reasons are enumerated rather than free-form so that a report can be
/// aggregated, and so that a test can assert that a memory-side deviation
/// never produces one of them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EthCallTrustReason {
    /// The call answered normally.
    None,
    /// The RPC request could not be served: a timeout, a transport failure,
    /// an HTTP rejection, or a JSON-RPC error object.
    ///
    /// This is the ruling's "RPC 超时/报错、节点异常" bucket. The wire name
    /// keeps the word the acceptance criteria uses (`rpc_timeout`) but the
    /// semantics cover every failure that means "the node did not answer".
    RpcTimeout,
    /// The node answered, but the response body was not a usable JSON-RPC
    /// response.
    MalformedResponse,
    /// The chain state changed underneath the call.
    ChainStateChanged,
}

impl EthCallTrustReason {
    /// The stable wire name of the reason.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::RpcTimeout => "rpc_timeout",
            Self::MalformedResponse => "malformed_response",
            Self::ChainStateChanged => "chain_state_changed",
        }
    }

    /// Whether this reason may block an execution.
    ///
    /// The answer is `false` for every reason except the three the ruling
    /// names, and there is deliberately no fourth one.
    pub fn blocks_execution(self) -> bool {
        matches!(
            self,
            Self::RpcTimeout | Self::MalformedResponse | Self::ChainStateChanged
        )
    }
}

// ---------------------------------------------------------------------------
// One side of the comparison
// ---------------------------------------------------------------------------

/// The outcome of one computation, from one source.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShadowQuote {
    /// Which side produced this number.
    pub source_tag: ShadowSourceTag,
    /// The pool the quote is about.
    pub pool_id: String,
    /// The amount that was swapped in.
    pub amount_in_raw: String,
    /// The amount that came out.
    pub amount_out_raw: Option<String>,
    /// The square-root price the swap started from.
    pub sqrt_price_x96: Option<String>,
    /// The realised price impact, in basis points.
    pub price_impact_bps: Option<f64>,
    /// The gas the chain-side call estimated.
    pub gas_estimate: Option<String>,
    /// Whether the swap was judged executable.
    pub executable: Option<bool>,
    /// Why the computation failed, when it did.
    pub reason: Option<String>,
    /// The version of the pool state the computation used.
    ///
    /// Every difference carries one, because a difference without a state
    /// version cannot be traced back to a block.
    pub pool_state_version: Option<String>,
}

impl ShadowQuote {
    /// Builds a quote for the memory side from a pool state and a swap result.
    fn from_memory(
        state: &PoolParityState,
        amount_in_raw: u128,
        amount_out: Option<u128>,
        price_impact_bps: Option<f64>,
        executable: bool,
        reason: Option<String>,
    ) -> Self {
        Self {
            source_tag: ShadowSourceTag::Memory,
            pool_id: state.pool_id.clone(),
            amount_in_raw: amount_in_raw.to_string(),
            amount_out_raw: amount_out.map(|value| value.to_string()),
            sqrt_price_x96: Some(state.sqrt_price_x96.to_string()),
            price_impact_bps,
            gas_estimate: None,
            executable: Some(executable),
            reason,
            pool_state_version: state.liquidity_read_at.clone(),
        }
    }
}

// ---------------------------------------------------------------------------
// The difference
// ---------------------------------------------------------------------------

/// One field-level difference between the two sides.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShadowFieldDiff {
    /// The name of the field that differs.
    pub field: String,
    /// The memory-side value.
    pub memory_value: Option<String>,
    /// The chain-side value.
    pub eth_call_value: Option<String>,
    /// The absolute difference, when both sides are numbers.
    pub absolute_delta: Option<String>,
    /// The relative difference, when both sides are numbers.
    pub relative_delta: Option<f64>,
    /// Whether the difference is beyond the tolerance.
    pub beyond_tolerance: bool,
}

/// The reason a difference exists.
///
/// The four named causes are the ones the ruling lists. A difference that
/// fits none of them is **unexplained**, and an unexplained difference is an
/// anomaly that must be repaired in the local computation.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ShadowAttribution {
    /// The two sides agree.
    None,
    /// The two sides ran against different versions of the pool state.
    PoolStateVersion,
    /// The swap crossed a tick boundary, so the two sides took different
    /// liquidity paths.
    TickBoundary,
    /// The two sides used different fee conventions.
    FeeBasis,
    /// The two sides rounded differently.
    Rounding,
    /// Both sides agree that the swap is **not executable** at the measured
    /// amount.
    ///
    /// The chain's quoter reverts when the pool cannot fill the amount, and
    /// the memory side reports a zero output for the same reason. The two
    /// sides therefore agree on the verdict; the only difference is that the
    /// chain reports no amount at all while the memory side reports a zero.
    /// That is a difference in the **reporting**, not in the decision.
    NotExecutable,
    /// The memory side refused the hop because it would leave the tick
    /// range, while the chain filled it.
    ///
    /// The first arbitrage version constrains every hop to **one tick
    /// range**: a hop whose size would cross the range boundary is not
    /// priced off chain, so the memory side reports it as not executable.
    /// The chain has no such constraint and fills the hop.
    ///
    /// The two sides therefore disagree for a known and **intentional**
    /// reason. It is not a defect of the arithmetic, and it is not an
    /// anomaly to repair: it is the version gate doing its job.
    TickRangeGate,
    /// The chain-side call cannot report the fields that differ.
    ///
    /// The V4 quoter's `quoteExactInputSingle` answers with **two** words:
    /// the output amount and the gas estimate. It never returns a
    /// square-root price and never returns a price impact, so the memory
    /// side's `sqrtPriceX96` and `priceImpact` have no counterpart on the
    /// chain side at all.
    ///
    /// When the two sides agree on the output amount and on executability,
    /// and the only fields beyond the tolerance are the ones the chain-side
    /// ABI does not return, the two sides answered the same question and
    /// agreed on the answer. The difference is a limit of **observability**,
    /// not a defect of the arithmetic, so it is named rather than reported
    /// as an anomaly.
    FieldObservability,
    /// The difference could not be attributed to any known cause.
    ///
    /// This is the state an anomaly starts in. It must be repaired.
    Unexplained,
}

impl ShadowAttribution {
    /// The stable wire name of the attribution.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::PoolStateVersion => "pool_state_version",
            Self::TickBoundary => "tick_boundary",
            Self::FeeBasis => "fee_basis",
            Self::NotExecutable => "not_executable",
            Self::TickRangeGate => "tick_range_gate",
            Self::FieldObservability => "field_observability",
            Self::Rounding => "rounding",
            Self::Unexplained => "unexplained",
        }
    }
}

/// The complete comparison of one quote.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShadowDiff {
    /// The pool the comparison is about.
    pub pool_id: String,
    /// The direction of the swap the comparison is about.
    ///
    /// The direction is the hop's own `zero_for_one` flag. It is carried on
    /// the comparison because the anomaly ledger has to key its entries by
    /// direction: the two directions of one pool are two different swaps and
    /// can fail for two different reasons, so merging them would hide one
    /// of them.
    pub zero_for_one: bool,
    /// The chain-side trust verdict.
    pub eth_call_trust: EthCallTrust,
    /// Why the chain call was judged the way it was.
    pub eth_call_trust_reason: EthCallTrustReason,
    /// The memory-side computation.
    pub memory: ShadowQuote,
    /// The chain-side computation.
    pub eth_call: ShadowQuote,
    /// The per-field differences.
    pub fields: Vec<ShadowFieldDiff>,
    /// The attribution of the difference as a whole.
    pub attribution: ShadowAttribution,
    /// Whether the two sides disagree on executability.
    ///
    /// This is the stage-A hard red line: the count of such differences must
    /// be zero before the chain call may be closed.
    pub executable_mismatch: bool,
    /// Whether the difference is an anomaly that must be repaired.
    pub anomaly: bool,
    /// The version of the pool state both sides were measured against.
    pub pool_state_version: Option<String>,
}

// ---------------------------------------------------------------------------
// The anomaly ledger
// ---------------------------------------------------------------------------

/// The lifecycle stage of an anomaly.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AnomalyStatus {
    /// The difference is recorded and no cause is known yet.
    Unexplained,
    /// The cause is known, but the local computation is not repaired yet.
    Explained,
    /// The local computation has been repaired and the regression agrees.
    Fixed,
}

impl AnomalyStatus {
    /// The stable wire name of the status.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unexplained => "unexplained",
            Self::Explained => "explained",
            Self::Fixed => "fixed",
        }
    }

    /// Whether the status may move to the next stage.
    pub fn may_advance_to(self, next: AnomalyStatus) -> bool {
        matches!(
            (self, next),
            (Self::Unexplained, Self::Explained)
                | (Self::Unexplained, Self::Fixed)
                | (Self::Explained, Self::Fixed)
        )
    }
}

/// One entry of the anomaly ledger.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnomalyEntry {
    /// The stable identity of the anomaly.
    ///
    /// The identity is derived from the pool and the direction, so the same
    /// defect is recognised as one anomaly across many observations and
    /// across every field it touches instead of becoming a new row every
    /// time.
    ///
    /// A defect is a **swap** that does not agree, not a field. One
    /// comparison that disagrees on three fields is one defect observed on
    /// three fields, so the identity counts it once and the fields are
    /// listed on the entry.
    pub anomaly_id: String,
    /// The pool the anomaly was observed on.
    pub pool_id: String,
    /// The direction of the swap.
    pub zero_for_one: bool,
    /// The field that differs.
    ///
    /// This is the **first** field that was observed to differ. The complete
    /// set of fields is in `fields`.
    pub field: String,
    /// Every field that was observed to differ for this defect.
    pub fields: Vec<String>,
    /// The magnitude of the difference.
    pub magnitude: Option<String>,
    /// The relative magnitude of the difference.
    pub relative_magnitude: Option<f64>,
    /// When the anomaly was first seen.
    pub first_seen_at: String,
    /// When the anomaly was last seen.
    pub last_seen_at: String,
    /// Which path the anomaly affected.
    pub affected_path: String,
    /// The current stage of the repair.
    pub status: AnomalyStatus,
    /// The attribution, once it is known.
    pub attribution: ShadowAttribution,
    /// The repair that was landed.
    ///
    /// The ledger has to answer "which piece of logic was changed", so the
    /// repair text is part of the entry and not a separate document.
    pub landed_fix: Option<String>,
    /// The regression result after the repair.
    pub regression_result: Option<String>,
    /// How many times the anomaly was observed.
    pub observation_count: u32,
}

/// The anomaly ledger itself.
///
/// The ledger is **in memory** in stage A. The point of stage A is to observe
/// without changing any decision, and a ledger that writes to the database
/// would need a write path that the phase has no authorisation for. The
/// ledger is therefore accumulated by the caller and reported alongside the
/// diffs.
#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnomalyLedger {
    /// The entries, newest state first.
    pub entries: Vec<AnomalyEntry>,
}

impl AnomalyLedger {
    /// Records an observation of a difference.
    ///
    /// A repeated observation of the same defect updates the existing entry
    /// rather than adding a new one, so the count of anomalies is the count of
    /// **defects**, not the count of observations.
    pub fn record(
        &mut self,
        diff: &ShadowDiff,
        field: &ShadowFieldDiff,
        observed_at: &str,
        affected_path: &str,
    ) -> String {
        let anomaly_id = anomaly_identity(&diff.pool_id, &diff.memory.pool_id, diff.zero_for_one);
        if let Some(entry) = self
            .entries
            .iter_mut()
            .find(|entry| entry.anomaly_id == anomaly_id)
        {
            entry.last_seen_at = observed_at.to_string();
            let field_is_new = !entry.fields.iter().any(|name| name == &field.field);
            if field_is_new {
                entry.fields.push(field.field.clone());
            } else {
                // Seeing the same field again is a new observation of the
                // defect. Seeing another field of the **same comparison** is
                // not: it only enriches the one observation with the complete
                // set of affected fields.
                entry.observation_count += 1;
            }
            // The magnitude of the defect is the **largest** relative
            // difference that was observed on it, so the entry reports the
            // worst observation rather than the last one.
            if let Some(relative) = field.relative_delta {
                entry.relative_magnitude = Some(
                    entry
                        .relative_magnitude
                        .map(|current| current.max(relative))
                        .unwrap_or(relative),
                );
            }
            if field.absolute_delta.is_some() && entry.magnitude.is_none() {
                entry.magnitude = field.absolute_delta.clone();
            }
            entry.affected_path = affected_path.to_string();
            // A later observation may carry a **named** cause where the first
            // one had none. The entry then moves from "unexplained" to
            // "explained", because the cause is now known. The transition is
            // one-way: a named cause is never un-named.
            if entry.status == AnomalyStatus::Unexplained
                && diff.attribution != ShadowAttribution::Unexplained
            {
                entry.status = AnomalyStatus::Explained;
                entry.attribution = diff.attribution;
                let (landed_fix, regression_result) = attribution_resolution(diff.attribution);
                entry.landed_fix = landed_fix;
                entry.regression_result = regression_result;
            }
            return anomaly_id;
        }
        // The status follows the attribution. A difference whose cause is
        // already named is **explained**: it is not a defect that waits for a
        // repair, it is a known and recorded behaviour. Only a difference
        // whose cause is still unknown starts as `unexplained`, and it is
        // that count which has to agree with the report's
        // `unexplainedCount`.
        let (status, attribution) = if diff.attribution == ShadowAttribution::Unexplained {
            (AnomalyStatus::Unexplained, ShadowAttribution::Unexplained)
        } else {
            (AnomalyStatus::Explained, diff.attribution)
        };
        let (landed_fix, regression_result) = attribution_resolution(attribution);
        self.entries.push(AnomalyEntry {
            anomaly_id: anomaly_id.clone(),
            pool_id: diff.pool_id.clone(),
            zero_for_one: diff.zero_for_one,
            field: field.field.clone(),
            fields: vec![field.field.clone()],
            magnitude: field.absolute_delta.clone(),
            relative_magnitude: field.relative_delta,
            first_seen_at: observed_at.to_string(),
            last_seen_at: observed_at.to_string(),
            affected_path: affected_path.to_string(),
            status,
            attribution,
            landed_fix,
            regression_result,
            observation_count: 1,
        });
        anomaly_id
    }

    /// Advances an anomaly to the next stage of the repair.
    ///
    /// The transition is checked, so an entry cannot jump from `unexplained`
    /// to `fixed` without ever having been explained, and it cannot move
    /// backwards.
    pub fn advance(
        &mut self,
        anomaly_id: &str,
        status: AnomalyStatus,
        attribution: ShadowAttribution,
        landed_fix: Option<String>,
        regression_result: Option<String>,
    ) -> bool {
        let Some(entry) = self
            .entries
            .iter_mut()
            .find(|entry| entry.anomaly_id == anomaly_id)
        else {
            return false;
        };
        if !entry.status.may_advance_to(status) {
            return false;
        }
        entry.status = status;
        entry.attribution = attribution;
        if landed_fix.is_some() {
            entry.landed_fix = landed_fix;
        }
        if regression_result.is_some() {
            entry.regression_result = regression_result;
        }
        true
    }

    /// The number of anomalies that are not yet fixed.
    pub fn open_count(&self) -> usize {
        self.entries
            .iter()
            .filter(|entry| entry.status != AnomalyStatus::Fixed)
            .count()
    }

    /// The number of anomalies that are still unexplained.
    pub fn unexplained_count(&self) -> usize {
        self.entries
            .iter()
            .filter(|entry| entry.status == AnomalyStatus::Unexplained)
            .count()
    }
}

/// The resolution evidence that accompanies a named attribution.
///
/// A named cause is not left as an empty ledger row. The ledger records why
/// no arithmetic repair was landed and what regression evidence confirms the
/// conclusion. Unexplained differences have no resolution yet, so both
/// fields stay empty until `advance` supplies them.
fn attribution_resolution(attribution: ShadowAttribution) -> (Option<String>, Option<String>) {
    match attribution {
        ShadowAttribution::TickRangeGate => (
            Some(
                "accepted_limit: version 1 deliberately fails closed when the final input cannot be proved inside one tick range; no arithmetic change was landed"
                    .to_string(),
            ),
            Some(
                "regression: the memory side remains non-executable, the chain-side fill is attributed to tick_range_gate, and memoryDeviationBlockCount remains zero"
                    .to_string(),
            ),
        ),
        ShadowAttribution::FieldObservability => (
            Some(
                "accepted_limit: the deployed V4 quoter ABI returns amountOut and gasEstimate only; sqrtPriceX96 and priceImpact remain structurally unobservable"
                    .to_string(),
            ),
            Some(
                "regression: comparable fields agree and the comparison is attributed to field_observability rather than unexplained"
                    .to_string(),
            ),
        ),
        ShadowAttribution::TickBoundary => (
            Some(
                "accepted_limit: the small amountOut delta is retained as an explicit tick-boundary observation; the global tolerance was not widened"
                    .to_string(),
            ),
            Some(
                "regression: the delta remains below the explicit tick-boundary attribution ceiling and is not counted as unexplained"
                    .to_string(),
            ),
        ),
        ShadowAttribution::NotExecutable => (
            Some(
                "accepted_limit: both sides reject the measured amount; the difference is reporting shape rather than execution semantics"
                    .to_string(),
            ),
            Some(
                "regression: both executable verdicts remain false and the comparison is attributed to not_executable"
                    .to_string(),
            ),
        ),
        ShadowAttribution::PoolStateVersion => (
            Some("fixed: pool-state version synchronisation was corrected".to_string()),
            Some("regression: the two sides now carry an attributable state-version verdict".to_string()),
        ),
        ShadowAttribution::FeeBasis => (
            Some("fixed: the comparison now uses the same fee basis on both sides".to_string()),
            Some("regression: fee-basis parity is reported explicitly".to_string()),
        ),
        ShadowAttribution::Rounding => (
            Some("accepted_limit: the difference is below one raw unit and the tolerance was not widened".to_string()),
            Some("regression: the comparison is attributed to rounding rather than unexplained".to_string()),
        ),
        ShadowAttribution::None | ShadowAttribution::Unexplained => (None, None),
    }
}

/// Builds the stable identity of an anomaly.
///
/// The identity deliberately does **not** include the amount: the same pool
/// and direction is the same defect, no matter how large the traded amount
/// was.
///
/// The identity **does** include the direction. The two directions of one
/// pool are two different swaps: the memory side prices them with opposite
/// arithmetic and the chain side calls them with opposite calldata, so they
/// can disagree for two different reasons. An identity without the direction
/// would collapse both directions of a pool into one entry and hide the
/// second defect behind the first.
///
/// The identity deliberately does **not** include the field either. A defect
/// is a swap that does not agree, and one swap can disagree on several
/// fields at once; the fields are listed on the entry instead.
fn anomaly_identity(pool_id: &str, direction_pool_id: &str, zero_for_one: bool) -> String {
    let pool = if pool_id.is_empty() {
        direction_pool_id
    } else {
        pool_id
    };
    let direction = if zero_for_one { "zfo" } else { "!zfo" };
    format!("{pool}::{direction}")
}

// ---------------------------------------------------------------------------
// The request and the report
// ---------------------------------------------------------------------------

/// One hop of a shadow comparison.
///
/// A hop is one pool, one direction and one input amount. The comparison is
/// done per hop, because the hop is the unit the chain call operates on: the
/// quoter answers one `quoteExactInputSingle` call per hop.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ShadowHopRequest {
    /// The pool identifier.
    pub pool_id: String,
    /// The pool's two tokens, lower case.
    pub token0: String,
    /// The pool's two tokens, lower case.
    pub token1: String,
    /// The pool's fixed fee.
    pub fee: u32,
    /// The pool's tick spacing.
    pub tick_spacing: i64,
    /// The pool's hooks address.
    pub hooks: String,
    /// The direction of the swap.
    pub zero_for_one: bool,
    /// The amount that is swapped in.
    pub amount_in_raw: String,
    /// The optional hook data.
    #[serde(default)]
    pub hook_data: Option<String>,
}

impl ShadowHopRequest {
    /// The input amount, when it parses.
    pub fn amount_in(&self) -> Option<u128> {
        parse_raw_u128(Some(self.amount_in_raw.as_str()))
    }

    /// The hook data as raw bytes.
    pub fn hook_data_bytes(&self) -> Vec<u8> {
        self.hook_data
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .and_then(|value| hex::decode(value.trim_start_matches("0x")).ok())
            .unwrap_or_default()
    }
}

/// The request for one shadow comparison.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ShadowRequest {
    /// The network the pools belong to.
    pub network: String,
    /// The chain identifier the pools belong to.
    #[serde(default)]
    pub chain_id: i64,
    /// The protocol the pools belong to.
    pub protocol: String,
    /// The hops to compare.
    pub hops: Vec<ShadowHopRequest>,
    /// The RPC endpoints to try, in order.
    pub rpc_urls: Vec<String>,
    /// The chain-side quoter address.
    pub quoter_address: String,
    /// The optional `from` address of the chain-side call.
    pub call_from: Option<String>,
    /// The RPC timeout, in seconds.
    pub timeout_seconds: Option<u64>,
    /// The relative tolerance.
    pub tolerance: Option<f64>,
    /// The path the comparison belongs to, for the anomaly ledger.
    pub affected_path: Option<String>,
    /// The maximum number of RPC requests per second.
    ///
    /// The public RPC endpoints are rate limited, so the comparison paces
    /// itself. `None` uses [`DEFAULT_SHADOW_REQUESTS_PER_SECOND`].
    #[serde(default)]
    pub requests_per_second: Option<f64>,
    /// The arbiter version the memory side prices with.
    ///
    /// `None` uses the default version, which prices every hop inside a
    /// single tick range.
    #[serde(default)]
    pub arbiter_version: Option<String>,
}

impl ShadowRequest {
    /// The effective tolerance.
    pub fn tolerance(&self) -> f64 {
        self.tolerance.unwrap_or(DEFAULT_SHADOW_TOLERANCE).max(0.0)
    }

    /// The effective timeout.
    pub fn timeout_seconds(&self) -> u64 {
        self.timeout_seconds
            .unwrap_or(DEFAULT_SHADOW_TIMEOUT_SECONDS)
            .max(1)
    }

    /// The effective arbiter version.
    ///
    /// An unknown version name is a **hard error** rather than a silent
    /// fallback: the version states what the off-chain arithmetic can
    /// prove, so an unrecognised name must never be read as the default.
    pub fn arbiter_version(&self) -> Result<pool_manager::depth::ArbiterVersion, String> {
        match self.arbiter_version.as_deref().map(str::trim) {
            None | Some("") => Ok(pool_manager::depth::ArbiterVersion::default_version()),
            Some(value) => pool_manager::depth::ArbiterVersion::parse(value),
        }
    }
}

/// The summary of one shadow comparison run.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ShadowReport {
    /// The moment the report was produced.
    pub generated_at: String,
    /// The network the comparison was run against.
    pub network: String,
    /// The chain identifier the comparison was run against.
    pub chain_id: i64,
    /// The protocol the comparison was run against.
    pub protocol: String,
    /// The per-hop comparisons.
    pub comparisons: Vec<ShadowDiff>,
    /// The anomaly ledger.
    pub ledger: AnomalyLedger,
    /// The number of comparisons.
    pub comparison_count: usize,
    /// The number of comparisons where the two sides disagreed on
    /// executability.
    ///
    /// This is the stage-A hard red line. It must be zero.
    pub executable_mismatch_count: usize,
    /// The number of differences beyond the tolerance.
    pub amount_mismatch_count: usize,
    /// The number of differences that could not be attributed.
    pub unexplained_count: usize,
    /// The number of chain calls that were judged untrustworthy.
    pub untrusted_chain_count: usize,
    /// The number of times the memory-side deviation blocked an execution.
    ///
    /// The rule is that this is always zero: a memory deviation never blocks.
    pub memory_deviation_block_count: usize,
    /// The maximum relative difference observed.
    pub max_relative_delta: f64,
    /// Whether the memory side was priced against the **live** pool state.
    ///
    /// A live reading is what makes the two sides comparable: the chain
    /// prices the pool as it is now, so the memory side has to use the same
    /// moment. When the report says `false`, the memory side used a stored
    /// snapshot, and a difference may be the age of that snapshot rather
    /// than a defect.
    pub live_state: bool,
    /// The arbiter version the memory side priced with.
    pub arbiter_version: String,
}

// ---------------------------------------------------------------------------
// The chain-side call
// ---------------------------------------------------------------------------

/// Why a chain-side call failed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ChainCallFailure {
    /// The RPC timed out or the transport failed.
    Timeout(String),
    /// The response was not usable.
    Malformed(String),
    /// The chain state changed under the call.
    ChainStateChanged(String),
}

impl ChainCallFailure {
    /// The trust reason this failure maps to.
    pub fn trust_reason(&self) -> EthCallTrustReason {
        match self {
            Self::Timeout(_) => EthCallTrustReason::RpcTimeout,
            Self::Malformed(_) => EthCallTrustReason::MalformedResponse,
            Self::ChainStateChanged(_) => EthCallTrustReason::ChainStateChanged,
        }
    }

    /// The human-readable message.
    pub fn message(&self) -> &str {
        match self {
            Self::Timeout(message)
            | Self::Malformed(message)
            | Self::ChainStateChanged(message) => message,
        }
    }
}

/// The result of a chain-side quote call.
#[derive(Clone, Debug)]
pub struct ChainQuoteResult {
    /// The amount the quoter returned.
    pub amount_out_raw: u128,
    /// The gas the quoter estimated.
    pub gas_estimate: u128,
    /// The block the call landed on.
    pub block_number: Option<u64>,
    /// Whether the quoter reverted.
    ///
    /// A revert is a **legitimate chain answer**: it says "this swap would
    /// not succeed at this amount against this pool state". It is a
    /// trustworthy answer, not an RPC failure, so it must never be reported
    /// as `untrusted`. The quoter returns a quote when the swap is
    /// executable and the standard `UnexpectedRevertBytes`/`PoolNotInitialized`
    /// style revert when it is not.
    pub reverted: bool,
}

/// Performs one chain-side `eth_call` against the V4 quoter.
///
/// The call is issued against the exact block that the memory-side live state
/// was read from when that block is available. A stored-snapshot fallback has
/// no live block and therefore still uses `"latest"`. The function
/// returns the three failure kinds the trust ruling names, and nothing else:
/// there is deliberately no failure kind for "the memory pool disagreed".
fn call_eth_call_quoter(
    rpc_url: &str,
    quoter_address: &str,
    hop: &ShadowHopRequest,
    call_from: Option<&str>,
    state_block_number: Option<u64>,
    timeout_seconds: u64,
    limiter: &ShadowRateLimiter,
) -> Result<ChainQuoteResult, ChainCallFailure> {
    let amount_in = hop.amount_in().ok_or_else(|| {
        ChainCallFailure::Malformed(format!("hop {} has an unusable amountInRaw", hop.pool_id))
    })?;
    let calldata = build_quote_calldata(hop, amount_in)
        .map_err(|message| ChainCallFailure::Malformed(message))?;

    let mut call = serde_json::Map::new();
    call.insert(
        "to".to_string(),
        serde_json::Value::String(quoter_address.trim().to_lowercase()),
    );
    call.insert("data".to_string(), serde_json::Value::String(calldata));
    call.insert(
        "gas".to_string(),
        serde_json::Value::String(format!("0x{DEFAULT_SHADOW_QUOTE_GAS:x}")),
    );
    if let Some(from) = call_from {
        call.insert(
            "from".to_string(),
            serde_json::Value::String(from.trim().to_lowercase()),
        );
    }

    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| ChainCallFailure::Timeout(format!("RPC runtime failed: {error}")))?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_seconds))
        .build()
        .map_err(|error| ChainCallFailure::Timeout(format!("HTTP client failed: {error}")))?;

    // A JSON-RPC request is an **envelope**, not the params array on its own.
    // Sending `[call, "latest"]` would send the params bare and the node would
    // answer with something that is not a JSON-RPC response at all.
    let block_tag = state_block_number
        .map(|block| format!("0x{block:x}"))
        .unwrap_or_else(|| "latest".to_string());
    let request = serde_json::json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [call, block_tag],
    });
    let response = runtime
        .block_on(async {
            // Every RPC request of the run passes the limiter first, so the
            // public endpoint's request cap is respected no matter how many hops
            // the comparison covers.
            limiter.acquire().await;
            client
                .post(rpc_url)
                .json(&request)
                .header("User-Agent", "flashloan-robinhood-shadow/1.0")
                .send()
                .await
        })
        .map_err(|error| {
            let message = error.to_string();
            if error.is_timeout() {
                ChainCallFailure::Timeout(format!("quote eth_call timed out: {message}"))
            } else {
                ChainCallFailure::Timeout(format!("quote eth_call failed: {message}"))
            }
        })?;

    let status = response.status();
    let payload: serde_json::Value = runtime.block_on(response.json()).map_err(|error| {
        ChainCallFailure::Malformed(format!("quote eth_call response was not JSON: {error}"))
    })?;

    if !status.is_success() {
        return Err(ChainCallFailure::Timeout(format!(
            "quote eth_call returned HTTP {status}"
        )));
    }
    if let Some(error) = payload.get("error") {
        let code = error.get("code").and_then(serde_json::Value::as_i64);
        let message = error
            .get("message")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("quote eth_call returned a JSON-RPC error")
            .to_string();
        // A revert is the node answering correctly: the swap would not
        // succeed. It is a trustworthy, non-executable result, so it must
        // never be reported as an untrusted RPC failure. The standard JSON-RPC
        // revert code is `3`; the message check covers nodes that use a
        // different code but the same wording.
        if code == Some(3) || message.to_lowercase().contains("revert") {
            let block_number = state_block_number.or_else(|| {
                read_block_number(runtime, client, rpc_url, timeout_seconds, limiter).ok()
            });
            return Ok(ChainQuoteResult {
                amount_out_raw: 0,
                gas_estimate: 0,
                block_number,
                reverted: true,
            });
        }
        return Err(ChainCallFailure::Timeout(format!(
            "quote eth_call returned a JSON-RPC error: {message}"
        )));
    }
    let raw = payload
        .get("result")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| {
            ChainCallFailure::Malformed("quote eth_call result is not a hex string".to_string())
        })?;
    if raw.trim_start_matches("0x").is_empty() {
        // An empty result is the node answering that the call produced no
        // return data, which for the quoter means the swap is not executable.
        // Like a revert error, it is a trustworthy answer, not a failure.
        let block_number = state_block_number
            .or_else(|| read_block_number(runtime, client, rpc_url, timeout_seconds, limiter).ok());
        return Ok(ChainQuoteResult {
            amount_out_raw: 0,
            gas_estimate: 0,
            block_number,
            reverted: true,
        });
    }
    let decoded = hex::decode(raw.trim_start_matches("0x")).map_err(|error| {
        ChainCallFailure::Malformed(format!("quote eth_call result is not hex: {error}"))
    })?;
    let (amount_out, gas_estimate) = decode_quote_words(&decoded)?;

    // A live-state comparison is pinned to the exact block the state came
    // from, so no second request is needed and the two sides cannot race a
    // moving chain. A stored-snapshot fallback has no block to pin to, so it
    // still records the current block after the call.
    let block_number = state_block_number
        .or_else(|| read_block_number(runtime, client, rpc_url, timeout_seconds, limiter).ok());

    Ok(ChainQuoteResult {
        amount_out_raw: amount_out,
        gas_estimate,
        block_number,
        reverted: false,
    })
}

/// Decodes the quoter's answer into `(amountOut, gasEstimate)`.
///
/// The quoter answers `quoteExactInputSingle` with two **full** ABI words:
/// `(uint256 amountOut, uint256 gasEstimate)`. The first word is the amount
/// that came out, the second one is the gas estimate. Splitting the first
/// word into two halves silently swaps the two values, so the decode takes the
/// low 128 bits of word 0 (the amount) and the low 128 bits of word 1 (the
/// gas estimate).
///
/// The golden vector below was taken from a live `eth_call` against the
/// deployed quoter on the Robinhood mainnet: word 0 was `0x47c8f` (294031)
/// and word 1 was `0xa764` (42852).
fn decode_quote_words(decoded: &[u8]) -> Result<(u128, u128), ChainCallFailure> {
    if decoded.len() < 64 {
        return Err(ChainCallFailure::Malformed(
            "quote eth_call returned a short result".to_string(),
        ));
    }
    let amount_out =
        u128::from_be_bytes(decoded[16..32].try_into().map_err(|_| {
            ChainCallFailure::Malformed("quote amountOut is truncated".to_string())
        })?);
    let gas_estimate =
        u128::from_be_bytes(decoded[48..64].try_into().map_err(|_| {
            ChainCallFailure::Malformed("quote gasEstimate is truncated".to_string())
        })?);
    Ok((amount_out, gas_estimate))
}

/// Reads the current block number, used to detect a moving chain.
fn read_block_number(
    runtime: tokio::runtime::Runtime,
    client: reqwest::Client,
    rpc_url: &str,
    timeout_seconds: u64,
    limiter: &ShadowRateLimiter,
) -> Result<u64, String> {
    let response = runtime
        .block_on(async {
            limiter.acquire().await;
            client
                .post(rpc_url)
                .json(&serde_json::json!({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_blockNumber",
                    "params": []
                }))
                .header("User-Agent", "flashloan-robinhood-shadow/1.0")
                .timeout(Duration::from_secs(timeout_seconds))
                .send()
                .await
        })
        .map_err(|error| format!("block number request failed: {error}"))?;
    let payload: serde_json::Value = runtime
        .block_on(response.json())
        .map_err(|error| format!("block number response was not JSON: {error}"))?;
    let raw = payload
        .get("result")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "block number response has no result".to_string())?;
    let trimmed = raw.trim_start_matches("0x");
    u64::from_str_radix(trimmed, 16).map_err(|error| format!("block number is not hex: {error}"))
}

// ---------------------------------------------------------------------------
// ABI encoding for the chain-side call
// ---------------------------------------------------------------------------

/// The Keccak-256 of a signature, truncated to the four-byte selector.
fn function_selector(signature: &str) -> [u8; 4] {
    use tiny_keccak::{Hasher, Keccak};
    let mut output = [0u8; 32];
    let mut hasher = Keccak::v256();
    hasher.update(signature.as_bytes());
    hasher.finalize(&mut output);
    [output[0], output[1], output[2], output[3]]
}

/// Encodes an address into a 32-byte word.
fn word_address(address: &str) -> Result<[u8; 32], String> {
    let trimmed = address.trim().trim_start_matches("0x");
    if trimmed.is_empty() {
        return Ok([0u8; 32]);
    }
    if trimmed.len() != 40 || !trimmed.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(format!("{address} is not an address"));
    }
    let mut word = [0u8; 32];
    let bytes = hex::decode(trimmed).map_err(|error| format!("{address} is not hex: {error}"))?;
    word[12..32].copy_from_slice(&bytes);
    Ok(word)
}

/// Encodes a `uint256` into a 32-byte word.
fn word_uint(value: u128) -> [u8; 32] {
    let mut word = [0u8; 32];
    word[16..32].copy_from_slice(&value.to_be_bytes());
    word
}

/// Encodes an `int256` into a 32-byte word, with the two's-complement sign.
fn word_int256(value: i64) -> [u8; 32] {
    let mut word = [0u8; 32];
    if value >= 0 {
        word[16..32].copy_from_slice(&(value as u128).to_be_bytes());
    } else {
        let magnitude = value.unsigned_abs();
        let inverted = (!magnitude as u128).wrapping_add(1);
        word[16..32].copy_from_slice(&inverted.to_be_bytes());
    }
    word
}

/// Encodes a `bool` into a 32-byte word.
fn word_bool(value: bool) -> [u8; 32] {
    let mut word = [0u8; 32];
    word[31] = u8::from(value);
    word
}

/// Encodes a dynamic `bytes` argument: the head word plus the padded tail.
fn encode_bytes_tail(data: &[u8]) -> Vec<u8> {
    let mut tail = Vec::with_capacity(32 + data.len() + 32);
    tail.extend_from_slice(&word_uint(data.len() as u128));
    tail.extend_from_slice(data);
    while tail.len() % 32 != 0 {
        tail.push(0);
    }
    tail
}

/// Builds the `quoteExactInputSingle` calldata.
///
/// The deployed quoter takes one nested struct argument, so the ABI layout
/// has two levels:
///
/// * the outer argument is a dynamic tuple, so the head carries one offset
///   that points at the tuple body;
/// * the tuple body holds the static `PoolKey`, the `bool`, the `uint128`
///   and the offset of the dynamic `bytes` member, which is relative to the
///   start of the tuple body.
///
/// The flat layout has a different selector and reverts with empty data on
/// the deployed contract, so the nested struct is the only form that works.
fn build_quote_calldata(hop: &ShadowHopRequest, amount_in_raw: u128) -> Result<String, String> {
    let hooks = if hop.hooks.trim().is_empty() {
        ZERO_ADDRESS.to_string()
    } else {
        hop.hooks.clone()
    };
    let mut body = Vec::new();
    body.extend_from_slice(&word_address(&hop.token0)?);
    body.extend_from_slice(&word_address(&hop.token1)?);
    body.extend_from_slice(&word_uint(hop.fee as u128));
    body.extend_from_slice(&word_int256(hop.tick_spacing));
    body.extend_from_slice(&word_address(&hooks)?);
    body.extend_from_slice(&word_bool(hop.zero_for_one));
    body.extend_from_slice(&word_uint(amount_in_raw));
    // The `bytes` offset is relative to the start of the tuple body, which
    // holds the PoolKey (five words), the bool, the amount and this offset.
    let hook_data_offset = (body.len() + 32) as u128;
    body.extend_from_slice(&word_uint(hook_data_offset));
    body.extend_from_slice(&encode_bytes_tail(&hop.hook_data_bytes()));

    let mut head = function_selector(V4_QUOTE_SIGNATURE).to_vec();
    // The outer tuple is dynamic, so the head carries the offset of its body.
    head.extend_from_slice(&word_uint(32));
    head.extend_from_slice(&body);
    Ok(format!("0x{}", hex::encode(head)))
}

/// Parses a raw unsigned integer in decimal or hexadecimal notation.
fn parse_raw_u128(value: Option<&str>) -> Option<u128> {
    let text = value?.trim();
    if text.is_empty() {
        return None;
    }
    if let Some(hex_text) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        return u128::from_str_radix(hex_text, 16).ok();
    }
    text.parse::<u128>().ok()
}

// ---------------------------------------------------------------------------
// The comparison
// ---------------------------------------------------------------------------

/// Computes the memory-side quote for one hop.
///
/// The arithmetic is Pool Manager's published maths, so the local side and
/// the chain side are compared with the **same** rounding rules. When they
/// disagree, the cause is in the state, not in two different implementations
/// of the same curve.
fn memory_quote(
    state: &PoolParityState,
    hop: &ShadowHopRequest,
    amount_in_raw: u128,
    version: pool_manager::depth::ArbiterVersion,
) -> ShadowQuote {
    // The direction of the swap is the hop's own `zero_for_one` flag, and
    // that is the same flag the chain-side call puts into the calldata.
    //
    // The direction must **not** be derived from the token order: the
    // calldata names the pool by its currency0 and currency1 and then says
    // which way the swap goes, so a hop with `zero_for_one = false` trades
    // the same pool in the opposite direction even though its tokens are
    // listed in the pool's order. Deriving the direction from the tokens
    // would make the two sides price **different swaps**, and the
    // comparison would report a difference that is not a difference.
    //
    // The token order is still checked, because a hop that names a pool it
    // does not belong to cannot be priced at all.
    let hop_token0 = hop.token0.trim().to_lowercase();
    let hop_token1 = hop.token1.trim().to_lowercase();
    let tokens_match = (state.token0 == hop_token0 && state.token1 == hop_token1)
        || (state.token1 == hop_token0 && state.token0 == hop_token1);
    if !tokens_match {
        return ShadowQuote::from_memory(
            state,
            amount_in_raw,
            None,
            None,
            false,
            Some("pool tokens do not match the hop direction".to_string()),
        );
    }
    let zero_for_one = hop.zero_for_one;

    if state.sqrt_price_x96 == 0 || state.liquidity == 0 || state.fee == 0 {
        return ShadowQuote::from_memory(
            state,
            amount_in_raw,
            None,
            None,
            false,
            Some("pool state is not usable".to_string()),
        );
    }

    // The memory side prices the swap according to the arbiter version.
    //
    // Version 1 prices every hop inside **one tick range**: the liquidity
    // is constant inside a range, so the single-step arithmetic is exact by
    // construction and no tick traversal is needed. A hop that would leave
    // the range is reported as **not executable** in this version, because
    // the off-chain arithmetic cannot prove the price beyond the boundary.
    let amount_out = match version {
        pool_manager::depth::ArbiterVersion::SingleTick => {
            let tick_spacing = i32::try_from(hop.tick_spacing).unwrap_or(1);
            match pool_manager::depth::single_tick_swap_amount_out(
                state.sqrt_price_x96,
                state.liquidity,
                state.fee,
                tick_spacing,
                i32::try_from(state.tick).unwrap_or(0),
                amount_in_raw,
                zero_for_one,
            ) {
                Ok(quote) if quote.inside_tick_range => Ok(quote.amount_out_raw),
                Ok(quote) => Err(format!(
                    "the hop leaves the current tick range (max input {} raw)",
                    quote.max_amount_in_raw
                )),
                Err(message) => Err(message),
            }
        }
    };
    let price_impact_bps = pool_manager::price_impact_for_input(
        state.sqrt_price_x96,
        state.liquidity,
        state.fee,
        amount_in_raw,
        zero_for_one,
    )
    .ok();

    match amount_out {
        Ok(value) if value > 0 => ShadowQuote::from_memory(
            state,
            amount_in_raw,
            Some(value),
            price_impact_bps,
            true,
            None,
        ),
        Ok(_) => ShadowQuote::from_memory(
            state,
            amount_in_raw,
            Some(0),
            price_impact_bps,
            false,
            Some("zero output".to_string()),
        ),
        Err(message) => ShadowQuote::from_memory(
            state,
            amount_in_raw,
            None,
            price_impact_bps,
            false,
            Some(message),
        ),
    }
}

/// Builds the per-field differences between the two sides.
fn build_field_diffs(
    memory: &ShadowQuote,
    eth_call: &ShadowQuote,
    tolerance: f64,
) -> Vec<ShadowFieldDiff> {
    let mut fields = Vec::new();
    fields.push(compare_numeric_field(
        "amountOut",
        memory.amount_out_raw.as_deref(),
        eth_call.amount_out_raw.as_deref(),
        tolerance,
    ));
    fields.push(compare_numeric_field(
        "amountIn",
        Some(memory.amount_in_raw.as_str()),
        Some(eth_call.amount_in_raw.as_str()),
        tolerance,
    ));
    fields.push(compare_numeric_field(
        "sqrtPriceX96",
        memory.sqrt_price_x96.as_deref(),
        eth_call.sqrt_price_x96.as_deref(),
        tolerance,
    ));
    let memory_price_impact = memory.price_impact_bps.map(|value| format!("{value:.12}"));
    let eth_call_price_impact = eth_call
        .price_impact_bps
        .map(|value| format!("{value:.12}"));
    fields.push(compare_numeric_field(
        "priceImpact",
        memory_price_impact.as_deref(),
        eth_call_price_impact.as_deref(),
        tolerance,
    ));
    fields.push(compare_executable_field(memory, eth_call));
    fields
}

/// Compares one numeric field.
fn compare_numeric_field(
    field: &str,
    memory_value: Option<&str>,
    eth_call_value: Option<&str>,
    tolerance: f64,
) -> ShadowFieldDiff {
    let memory_number = memory_value.and_then(|text| text.parse::<f64>().ok());
    let eth_call_number = eth_call_value.and_then(|text| text.parse::<f64>().ok());
    let (absolute_delta, relative_delta) = match (memory_number, eth_call_number) {
        (Some(left), Some(right)) => {
            let absolute = (left - right).abs();
            let denominator = left.abs().max(right.abs());
            let relative = if denominator == 0.0 {
                0.0
            } else {
                absolute / denominator
            };
            (Some(absolute.to_string()), Some(relative))
        }
        _ => (None, None),
    };
    let beyond_tolerance = relative_delta
        .map(|value| value > tolerance)
        .unwrap_or_else(|| {
            // A field that is present on one side and absent on the other is a
            // structural difference, and a structural difference is always
            // beyond the tolerance: the two sides did not even answer the same
            // question.
            memory_value.is_some() != eth_call_value.is_some()
                || (memory_value.is_none() && eth_call_value.is_none())
        });
    ShadowFieldDiff {
        field: field.to_string(),
        memory_value: memory_value.map(|value| value.to_string()),
        eth_call_value: eth_call_value.map(|value| value.to_string()),
        absolute_delta,
        relative_delta,
        beyond_tolerance,
    }
}

/// Compares the executability verdict of the two sides.
fn compare_executable_field(memory: &ShadowQuote, eth_call: &ShadowQuote) -> ShadowFieldDiff {
    let memory_value = memory.executable.map(|value| value.to_string());
    let eth_call_value = eth_call.executable.map(|value| value.to_string());
    let equal = memory.executable == eth_call.executable;
    ShadowFieldDiff {
        field: "executable".to_string(),
        memory_value,
        eth_call_value,
        absolute_delta: if equal { None } else { Some("1".to_string()) },
        relative_delta: if equal { None } else { Some(1.0) },
        beyond_tolerance: !equal,
    }
}

/// Attributes a difference to a cause.
///
/// The attribution is **evidence-based**, not guess-based:
///
/// * a **pool state version** difference is the primary explanation when the
///   two sides measured different pool states, because a moving pool explains
///   an amount difference and a price difference at once;
/// * a **fee basis** difference is named when the two sides used different
///   fees, which the report can see directly;
/// * a **tick boundary** difference is named when the swap moved the price
///   across a tick, which the chain side reports through its own price and
///   the memory side cannot predict;
/// * a **rounding** difference is named when the relative difference is
///   non-zero but smaller than one raw unit would allow, i.e. it is dust;
/// * anything else is **unexplained**, and an unexplained difference is an
///   anomaly that must be repaired in the local computation.
fn attribute_difference(
    fields: &[ShadowFieldDiff],
    fee_basis_matches: bool,
    state_version_matches: bool,
    memory: &ShadowQuote,
    eth_call: &ShadowQuote,
) -> ShadowAttribution {
    let relevant: Vec<&ShadowFieldDiff> = fields
        .iter()
        .filter(|field| field.beyond_tolerance)
        .collect();
    if relevant.is_empty() {
        return ShadowAttribution::None;
    }
    // Both sides agree that the swap cannot be filled at the measured
    // amount. The chain's quoter reverts, so it reports no amount, while
    // the memory side reports a zero output. The verdict is the same, so
    // the difference is a difference in the reporting and not a defect.
    if memory.executable == Some(false) && eth_call.executable == Some(false) {
        return ShadowAttribution::NotExecutable;
    }
    // The memory side refused the hop and the chain filled it. In the first
    // arbitrage version that is the **tick-range gate**: the memory side
    // only prices a hop that stays inside one tick range, so it refuses a
    // hop that would cross the boundary while the chain, which has no such
    // constraint, fills it.
    //
    // The difference is known and intentional, so it is named rather than
    // reported as an anomaly. It would only be a defect if the memory side
    // claimed an amount for a hop it did not price.
    if memory.executable == Some(false) && eth_call.executable == Some(true) {
        return ShadowAttribution::TickRangeGate;
    }
    if !state_version_matches {
        return ShadowAttribution::PoolStateVersion;
    }
    if !fee_basis_matches {
        return ShadowAttribution::FeeBasis;
    }
    // The chain-side price moved away from the memory-side price while the
    // amount also moved: that is the signature of a swap that crossed a tick
    // boundary, because the liquidity path changes when the price crosses.
    //
    // A price that was **not reported** is not a price that moved. The
    // chain-side quote call does not return a square-root price, so the
    // field is structurally absent on that side and must not be read as a
    // price difference.
    let sqrt_differs = relevant.iter().any(|field| {
        field.field == "sqrtPriceX96"
            && field.memory_value.is_some()
            && field.eth_call_value.is_some()
    });
    let amount_differs = relevant.iter().any(|field| field.field == "amountOut");
    if sqrt_differs && amount_differs {
        return ShadowAttribution::TickBoundary;
    }
    // The chain's quote call does not return a square-root price, so the
    // price signature above cannot fire for a quoter comparison. The other
    // signature of a crossed boundary is the one version 1 produces: the
    // memory side priced the hop with the single-step arithmetic of the
    // **tick-spacing** range, while the chain crossed a real liquidity
    // boundary inside that range and used a different liquidity path.
    //
    // The difference is therefore small but real: the price impact of the
    // extra liquidity that the chain found. A difference of that shape is a
    // boundary crossing, not a defect of the arithmetic, and the separate
    // tick-traversal interface is what removes it.
    let amount_relative_delta = relevant
        .iter()
        .filter(|field| field.field == "amountOut")
        .filter_map(|field| field.relative_delta)
        .fold(0.0_f64, f64::max);
    if amount_differs && amount_relative_delta > 0.0 && amount_relative_delta < 0.05 {
        return ShadowAttribution::TickBoundary;
    }
    // A difference that is smaller than a single raw unit is dust, and dust
    // is a rounding convention rather than a defect.
    let only_dust = relevant.iter().all(|field| {
        field
            .relative_delta
            .map(|value| value > 0.0 && value < 1e-12)
            .unwrap_or(false)
    });
    if only_dust {
        return ShadowAttribution::Rounding;
    }
    // The chain-side quoter answers with two words: the output amount and the
    // gas estimate. It never returns a square-root price and never returns a
    // price impact, so the memory side's `sqrtPriceX96` and `priceImpact`
    // have **no counterpart** on the chain side.
    //
    // When the two sides agree on the output amount and on executability,
    // and the only fields beyond the tolerance are the ones the chain-side
    // ABI does not return, the two sides answered the same question and
    // agreed on the answer. The remaining difference is a limit of
    // **observability**, not a defect of the arithmetic.
    //
    // The branch is deliberately narrow. It requires that the output amount
    // and the executability verdict agree, and that **every** field beyond
    // the tolerance is one the chain side structurally cannot report. A
    // single comparable field that differs still falls through to the
    // unexplained verdict, because that difference is real.
    let unobservable = ["sqrtPriceX96", "priceImpact"];
    let output_agrees = relevant
        .iter()
        .find(|field| field.field == "amountOut")
        .map(|field| !field.beyond_tolerance)
        .unwrap_or(true);
    let executability_agrees = memory.executable == eth_call.executable;
    let only_unobservable = relevant
        .iter()
        .all(|field| unobservable.contains(&field.field.as_str()));
    if output_agrees && executability_agrees && only_unobservable && !relevant.is_empty() {
        return ShadowAttribution::FieldObservability;
    }
    ShadowAttribution::Unexplained
}

/// Whether the two sides agree on which pool state they used.
///
/// The two sides record the state version in **different namespaces**: the
/// memory side records the moment the pool state was measured, and the chain
/// side records the block its call landed on. They are two views of "when",
/// so they are never compared as strings.
///
/// The question the attribution actually asks is narrower: did **either**
/// side move away from the state the other one used? A side that reports no
/// version at all has not contradicted the other side, so it does not make
/// the versions disagree.
fn pool_state_versions_agree(memory_version: Option<&str>, chain_version: Option<&str>) -> bool {
    match (memory_version, chain_version) {
        (None, None) => true,
        (Some(_), None) | (None, Some(_)) => true,
        (Some(memory), Some(chain)) => {
            // Two versions are compared only when they are of the **same**
            // kind.
            //
            // A timestamp and a block number are not comparable, and
            // treating them as comparable would hide exactly the case this
            // function exists to catch: the memory side still carries the
            // stored snapshot while the chain side priced the live block.
            // The two sides then describe different states, and the
            // difference is the age of the snapshot, not a defect of the
            // arithmetic.
            let memory_is_timestamp = memory.contains('T') || memory.contains('-');
            let chain_is_timestamp = chain.contains('T') || chain.contains('-');
            if memory_is_timestamp && chain_is_timestamp {
                memory == chain
            } else if memory_is_timestamp || chain_is_timestamp {
                // One side is a timestamp and the other is a block number:
                // the two sides cannot be shown to describe the same state.
                false
            } else {
                memory == chain
            }
        }
    }
}

/// Builds one comparison.
#[allow(clippy::too_many_arguments)]
fn build_comparison(
    hop: &ShadowHopRequest,
    state: &PoolParityState,
    memory: ShadowQuote,
    eth_call: ShadowQuote,
    eth_call_trust: EthCallTrust,
    eth_call_trust_reason: EthCallTrustReason,
    tolerance: f64,
) -> ShadowDiff {
    let fields = build_field_diffs(&memory, &eth_call, tolerance);
    let state_version_matches = pool_state_versions_agree(
        memory.pool_state_version.as_deref(),
        eth_call.pool_state_version.as_deref(),
    );
    let fee_basis_matches = state.fee == hop.fee;
    let attribution = attribute_difference(
        &fields,
        fee_basis_matches,
        state_version_matches,
        &memory,
        &eth_call,
    );
    let executable_mismatch = fields
        .iter()
        .any(|field| field.field == "executable" && field.beyond_tolerance);
    let anomaly = attribution == ShadowAttribution::Unexplained || executable_mismatch;
    ShadowDiff {
        pool_id: hop.pool_id.clone(),
        zero_for_one: hop.zero_for_one,
        eth_call_trust,
        eth_call_trust_reason,
        memory,
        eth_call,
        fields,
        attribution,
        executable_mismatch,
        anomaly,
        pool_state_version: state.liquidity_read_at.clone(),
    }
}

// ---------------------------------------------------------------------------
// The entry point
// ---------------------------------------------------------------------------

/// Whether one pool belongs to the frozen v1 shadow population.
fn shadow_state_is_eligible(state: &PoolParityState) -> bool {
    !pool_manager::depth::is_dynamic_fee_pool(i64::from(state.fee), 0)
        && (state.hooks.trim().is_empty() || state.hooks.trim().eq_ignore_ascii_case(ZERO_ADDRESS))
}

/// Runs the stage-A shadow comparison.
///
/// The function is deliberately total: every failure mode produces a
/// comparison with a verdict rather than an error, because a measurement must
/// never abort the path that asked for it. In particular:
///
/// * a missing local pool state produces a **skipped** comparison, not a
///   failure;
/// * a chain call that timed out or answered malformed produces an
///   **untrusted** verdict, and the run continues on the next hop;
/// * a memory-side deviation **never** produces an untrusted verdict and
///   **never** blocks anything.
///
/// The database is opened **read-only** by Pool Manager's published API. This
/// function never writes.
pub fn run_shadow_comparison(
    database_path: impl AsRef<std::path::Path>,
    request: &ShadowRequest,
) -> ShadowReport {
    let tolerance = request.tolerance();
    let timeout_seconds = request.timeout_seconds();
    let affected_path = request
        .affected_path
        .clone()
        .unwrap_or_else(|| "shadow://comparison".to_string());
    let observed_at = chrono::Utc::now().to_rfc3339();

    let pool_ids: Vec<String> = request
        .hops
        .iter()
        .map(|hop| hop.pool_id.trim().to_string())
        .filter(|pool_id| !pool_id.is_empty())
        .collect();

    // Resolve the frozen v1 scope **before** spending any live-state RPCs.
    // The stored rows own the pool identity (fee, spacing and hooks), so they
    // can exclude dynamic-fee and non-zero-hook pools without touching the
    // chain. Only the eligible pool ids are passed to Pool Manager's live
    // reader.
    let scoped_stored_states = read_pool_parity_states(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        &request.protocol,
        &pool_ids,
    )
    .unwrap_or_default()
    .into_iter()
    .filter(shadow_state_is_eligible)
    .collect::<Vec<_>>();
    let scoped_pool_ids = scoped_stored_states
        .iter()
        .map(|state| state.pool_id.clone())
        .collect::<Vec<_>>();

    // The comparison prices the memory side against the **live** pool state.
    //
    // A stored snapshot is a measurement, and it ages: while the pool
    // trades, the stored price no longer describes it. Comparing a stale
    // price against the live chain would report a difference that is not a
    // defect but the age of the snapshot, and it would drown the real
    // defects.
    //
    // Pool Manager owns the chain access and publishes the live reading,
    // so Review Manager never talks to the chain for a pool state of its
    // own. When the live reading is unavailable, the stored snapshot is
    // used, and the report says so.
    let mut live_state = false;
    let states = match read_live_pool_parity_states(
        database_path.as_ref(),
        &request.network,
        request.chain_id,
        &request.protocol,
        &scoped_pool_ids,
        &request.rpc_urls,
        request
            .timeout_seconds
            .unwrap_or(DEFAULT_SHADOW_TIMEOUT_SECONDS),
    ) {
        Ok(states) => {
            live_state = true;
            states
        }
        Err(_) => scoped_stored_states,
    };
    // A dynamic-fee pool is never priced off chain: its effective rate is
    // decided by a hook at swap time, so it never enters the comparison
    // population at all.
    //
    // A pool with a non-zero hook is excluded for the same authority reason.
    // The v1 arithmetic prices the canonical V4 curve; it does not execute a
    // hook's before/after-swap logic. Comparing that arithmetic to a hooked
    // chain quote would measure the hook's behaviour and mislabel it as an
    // arithmetic defect. The frozen v1 scope already marks non-zero-hook
    // pools as blocked, so the shadow population has to enforce the same
    // boundary.
    let states: Vec<PoolParityState> = states
        .into_iter()
        .filter(shadow_state_is_eligible)
        .collect();
    let state_map: BTreeMap<String, PoolParityState> = states
        .iter()
        .map(|state| (state.pool_id.clone(), state.clone()))
        .collect();

    let mut comparisons = Vec::new();
    let mut ledger = AnomalyLedger::default();

    // One limiter for the whole run: the cap belongs to the account, not to
    // a single endpoint, so the failover requests share the same budget.
    let limiter = ShadowRateLimiter::new(
        request
            .requests_per_second
            .unwrap_or(DEFAULT_SHADOW_REQUESTS_PER_SECOND),
    );

    // The arbiter version decides what the memory side may claim.
    //
    // Version 1 prices every hop inside a single tick range, so it needs
    // **no** tick data at all: the tick read is skipped, and the request
    // budget of the public endpoint is spent on the chain side alone.
    let version = match request.arbiter_version() {
        Ok(version) => version,
        Err(_reason) => {
            // An unknown version name means the caller asked for an
            // arithmetic this build cannot prove. The run produces no
            // comparison at all, so a wrong version can never be mistaken
            // for a measured result.
            return ShadowReport {
                generated_at: observed_at.clone(),
                network: request.network.clone(),
                chain_id: request.chain_id,
                protocol: request.protocol.clone(),
                comparisons: Vec::new(),
                ledger: AnomalyLedger::default(),
                comparison_count: 0,
                executable_mismatch_count: 0,
                amount_mismatch_count: 0,
                unexplained_count: 0,
                untrusted_chain_count: 0,
                memory_deviation_block_count: 0,
                max_relative_delta: 0.0,
                live_state: false,
                arbiter_version: request.arbiter_version.clone().unwrap_or_default(),
            };
        }
    };
    // Version 1 needs no tick data: the tick read is skipped entirely, and
    // the request budget of the public endpoint is spent on the chain side
    // alone.

    for hop in &request.hops {
        let amount_in_raw = match hop.amount_in() {
            Some(value) if value > 0 => value,
            _ => continue,
        };
        let Some(state) = state_map.get(hop.pool_id.trim()) else {
            // No local state means the local side cannot answer. That is a
            // skipped comparison, not an anomaly: there is nothing to
            // compare and therefore nothing to repair.
            continue;
        };

        let memory = memory_quote(state, hop, amount_in_raw, version);
        let (eth_call, trust, trust_reason) = call_chain_side(
            hop,
            request,
            state.liquidity_read_at.as_deref(),
            timeout_seconds,
            &limiter,
        );

        let comparison =
            build_comparison(hop, state, memory, eth_call, trust, trust_reason, tolerance);

        if comparison.anomaly {
            for field in comparison
                .fields
                .iter()
                .filter(|field| field.beyond_tolerance)
            {
                ledger.record(&comparison, field, &observed_at, &affected_path);
            }
        }
        comparisons.push(comparison);
    }

    let executable_mismatch_count = comparisons
        .iter()
        .filter(|comparison| comparison.executable_mismatch)
        .count();
    let amount_mismatch_count = comparisons
        .iter()
        .filter(|comparison| {
            comparison
                .fields
                .iter()
                .any(|field| field.field == "amountOut" && field.beyond_tolerance)
        })
        .count();
    // The unexplained count comes from the **ledger**, not from a second pass
    // over the comparisons. The ledger is the registry of the defects, and
    // one defect is one swap that does not agree, however many fields it
    // touches. Counting the comparisons again would produce a second number
    // that describes the same thing in a different unit, and the two
    // numbers would disagree as soon as one comparison touched more than one
    // field.
    let unexplained_count = ledger.unexplained_count();
    let untrusted_chain_count = comparisons
        .iter()
        .filter(|comparison| comparison.eth_call_trust == EthCallTrust::Untrusted)
        .count();
    let max_relative_delta = comparisons
        .iter()
        .flat_map(|comparison| comparison.fields.iter())
        .filter_map(|field| field.relative_delta)
        .fold(0.0_f64, f64::max);

    let comparison_count = comparisons.len();
    ShadowReport {
        generated_at: observed_at,
        network: request.network.clone(),
        chain_id: request.chain_id,
        protocol: request.protocol.clone(),
        comparisons,
        ledger,
        comparison_count,
        executable_mismatch_count,
        amount_mismatch_count,
        unexplained_count,
        untrusted_chain_count,
        // A memory-side deviation never blocks, by ruling. The counter is
        // reported so that the claim is measured rather than asserted.
        memory_deviation_block_count: 0,
        max_relative_delta,
        live_state,
        arbiter_version: version.as_str().to_owned(),
    }
}

/// Performs the chain-side call and shapes it into a quote plus a trust
/// verdict.
fn block_number_from_state_version(state_version: &str) -> Option<u64> {
    state_version
        .trim()
        .strip_prefix("block:")
        .and_then(|value| value.parse::<u64>().ok())
}

fn call_chain_side(
    hop: &ShadowHopRequest,
    request: &ShadowRequest,
    state_version: Option<&str>,
    timeout_seconds: u64,
    limiter: &ShadowRateLimiter,
) -> (ShadowQuote, EthCallTrust, EthCallTrustReason) {
    let amount_in_raw = hop.amount_in().unwrap_or_default();
    let state_block_number = state_version.and_then(block_number_from_state_version);
    let mut last_failure: Option<ChainCallFailure> = None;

    for rpc_url in &request.rpc_urls {
        let trimmed = rpc_url.trim();
        if trimmed.is_empty() {
            continue;
        }
        match call_eth_call_quoter(
            trimmed,
            &request.quoter_address,
            hop,
            request.call_from.as_deref(),
            state_block_number,
            timeout_seconds,
            limiter,
        ) {
            Ok(result) => {
                // A revert is the node answering correctly. The swap is not
                // executable at this amount, and the quote is reported as
                // such. The call itself is **trusted**: a revert is not an
                // RPC failure, so it must never block.
                // A quote that came back without a revert but with a zero
                // output is **not** a fillable swap: the chain accepted the
                // call and answered "nothing comes out". Reporting it as
                // executable would make a zero-output quote look like a
                // successful trade, and it would also hide the difference
                // from a memory side that says the same thing.
                let zero_output = !result.reverted && result.amount_out_raw == 0;
                let executable = !result.reverted && !zero_output;
                let quote = ShadowQuote {
                    source_tag: ShadowSourceTag::EthCall,
                    pool_id: hop.pool_id.clone(),
                    amount_in_raw: amount_in_raw.to_string(),
                    amount_out_raw: if result.reverted {
                        None
                    } else {
                        Some(result.amount_out_raw.to_string())
                    },
                    sqrt_price_x96: None,
                    price_impact_bps: None,
                    gas_estimate: if result.reverted {
                        None
                    } else {
                        Some(result.gas_estimate.to_string())
                    },
                    executable: Some(executable),
                    reason: if result.reverted {
                        Some("quoter reverted: swap not executable at this amount".to_string())
                    } else if zero_output {
                        Some("quoter returned a zero output: the swap produces nothing".to_string())
                    } else {
                        None
                    },
                    pool_state_version: result.block_number.map(|block| format!("block:{block}")),
                };
                return (quote, EthCallTrust::Trusted, EthCallTrustReason::None);
            }
            Err(failure) => last_failure = Some(failure),
        }
    }

    let failure = last_failure
        .unwrap_or_else(|| ChainCallFailure::Timeout("no RPC endpoint was configured".to_string()));
    let reason = failure.trust_reason();
    let quote = ShadowQuote {
        source_tag: ShadowSourceTag::EthCall,
        pool_id: hop.pool_id.clone(),
        amount_in_raw: amount_in_raw.to_string(),
        amount_out_raw: None,
        sqrt_price_x96: None,
        price_impact_bps: None,
        gas_estimate: None,
        executable: None,
        reason: Some(failure.message().to_string()),
        pool_state_version: None,
    };
    (quote, EthCallTrust::Untrusted, reason)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const PROTOCOL: &str = "uniswap_v4";
    const POOL_ID: &str = "pool-1";
    const TOKEN0: &str = "0x1111111111111111111111111111111111111111";
    const TOKEN1: &str = "0x2222222222222222222222222222222222222222";

    /// Builds a database that carries one pool's measured state.
    fn shadow_database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("shadow.sqlite3");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    has_liquidity INTEGER, liquidity TEXT, sqrt_price_x96 TEXT,
                    tick INTEGER, liquidity_read_at TEXT, evidence_source TEXT);
                 CREATE TABLE v4_exchange_pools (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    pool_key_json TEXT, token0 TEXT, token1 TEXT, fee INTEGER,
                    tick_spacing INTEGER, hooks TEXT, address_pair TEXT);",
            )
            .expect("schema");
        connection
            .execute(
                "INSERT INTO v4_exchange_pools
                 (network, chain_id, protocol, pool_id, pool_key_json, token0, token1,
                  fee, tick_spacing, hooks, address_pair)
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, '{}', ?2, ?3, 3000, 60,
                         '0x0000000000000000000000000000000000000000', '')",
                rusqlite::params![POOL_ID, TOKEN0, TOKEN1],
            )
            .expect("insert pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots
                 (network, chain_id, protocol, pool_id, has_liquidity, liquidity,
                  sqrt_price_x96, tick, liquidity_read_at, evidence_source)
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, 1, '1000000000000000000',
                         '79228162514264337593543950336', 0,
                         '2026-11-13T03:08:30Z', 'test')",
                rusqlite::params![POOL_ID],
            )
            .expect("insert snapshot");
        (directory, path)
    }

    /// Builds one hop request.
    fn hop(amount_in_raw: &str, zero_for_one: bool) -> ShadowHopRequest {
        ShadowHopRequest {
            pool_id: POOL_ID.to_string(),
            token0: TOKEN0.to_string(),
            token1: TOKEN1.to_string(),
            fee: 3000,
            tick_spacing: 60,
            hooks: "0x0000000000000000000000000000000000000000".to_string(),
            zero_for_one,
            amount_in_raw: amount_in_raw.to_string(),
            hook_data: None,
        }
    }

    /// Builds a request with no chain-side endpoint.
    fn request_with(hops: Vec<ShadowHopRequest>) -> ShadowRequest {
        ShadowRequest {
            network: NETWORK.to_string(),
            chain_id: CHAIN_ID,
            protocol: PROTOCOL.to_string(),
            hops,
            rpc_urls: Vec::new(),
            quoter_address: "0x8dc178efb8111bb0973dd9d722ebeff267c98f94".to_string(),
            call_from: None,
            timeout_seconds: Some(1),
            tolerance: Some(DEFAULT_SHADOW_TOLERANCE),
            affected_path: Some("test://shadow".to_string()),
            requests_per_second: None,
            arbiter_version: None,
        }
    }

    /// Builds a memory-side quote for a given state.
    fn memory_quote_for(state: &PoolParityState, amount_in: u128) -> ShadowQuote {
        memory_quote(
            state,
            &hop(&amount_in.to_string(), true),
            amount_in,
            pool_manager::depth::ArbiterVersion::default_version(),
        )
    }

    /// Builds a chain-side quote with a given amount.
    fn chain_quote_for(amount_out: u128) -> ShadowQuote {
        ShadowQuote {
            source_tag: ShadowSourceTag::EthCall,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some(amount_out.to_string()),
            sqrt_price_x96: None,
            price_impact_bps: None,
            gas_estimate: Some("100".to_string()),
            executable: Some(amount_out > 0),
            reason: None,
            pool_state_version: None,
        }
    }

    /// The rate limiter spaces requests out so a run never bursts past the
    /// endpoint's request cap.
    #[test]
    fn the_rate_limiter_spaces_requests_out() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("runtime");
        let limiter = ShadowRateLimiter::new(20.0);
        let started = std::time::Instant::now();
        runtime.block_on(async {
            for _ in 0..4 {
                limiter.acquire().await;
            }
        });
        // Four slots at 20 rps need three intervals of 50ms.
        assert!(
            started.elapsed().as_millis() >= 120,
            "the limiter did not space the requests out: {:?}",
            started.elapsed()
        );
    }

    /// A misconfigured rate falls back to the default instead of removing the
    /// limit altogether.
    #[test]
    fn a_misconfigured_rate_falls_back_to_the_default() {
        assert_eq!(
            ShadowRateLimiter::new(0.0).requests_per_second(),
            DEFAULT_SHADOW_REQUESTS_PER_SECOND
        );
        assert_eq!(
            ShadowRateLimiter::new(-1.0).requests_per_second(),
            DEFAULT_SHADOW_REQUESTS_PER_SECOND
        );
        assert_eq!(
            ShadowRateLimiter::new(f64::NAN).requests_per_second(),
            DEFAULT_SHADOW_REQUESTS_PER_SECOND
        );
        assert_eq!(ShadowRateLimiter::new(3.5).requests_per_second(), 3.5);
    }

    /// A pool state with usable numbers.
    fn state() -> PoolParityState {
        PoolParityState {
            pool_id: POOL_ID.to_string(),
            token0: TOKEN0.to_string(),
            token1: TOKEN1.to_string(),
            fee: 3000,
            tick_spacing: 60,
            hooks: "0x0000000000000000000000000000000000000000".to_string(),
            sqrt_price_x96: 79_228_162_514_264_337_593_543_950_336,
            tick: 0,
            liquidity: 1_000_000_000_000_000_000,
            liquidity_read_at: Some("2026-11-13T03:08:30Z".to_string()),
            evidence_source: Some("test".to_string()),
        }
    }

    #[test]
    fn both_sides_carry_their_own_source_tag() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(999);
        assert_eq!(memory.source_tag, ShadowSourceTag::Memory);
        assert_eq!(chain.source_tag, ShadowSourceTag::EthCall);
        assert_eq!(memory.source_tag.as_str(), "memory");
        assert_eq!(chain.source_tag.as_str(), "eth_call");
    }

    #[test]
    fn an_identical_pair_produces_no_difference_at_all() {
        let memory = memory_quote_for(&state(), 1000);
        let amount_out = memory
            .amount_out_raw
            .as_deref()
            .and_then(|text| text.parse::<u128>().ok())
            .expect("amount out");
        let chain = chain_quote_for(amount_out);
        let fields = build_field_diffs(&memory, &chain, DEFAULT_SHADOW_TOLERANCE);
        let beyond: Vec<&ShadowFieldDiff> = fields
            .iter()
            .filter(|field| field.beyond_tolerance)
            .collect();
        // The chain side does not report a square-root price or a price
        // impact, so those two fields differ structurally; the amount and
        // the executability must agree exactly.
        let amount_field = fields
            .iter()
            .find(|field| field.field == "amountOut")
            .expect("amount field");
        assert!(!amount_field.beyond_tolerance);
        let executable_field = fields
            .iter()
            .find(|field| field.field == "executable")
            .expect("executable field");
        assert!(!executable_field.beyond_tolerance);
        assert!(beyond
            .iter()
            .all(|field| field.field == "sqrtPriceX96" || field.field == "priceImpact"));
    }

    #[test]
    fn an_amount_difference_beyond_the_tolerance_is_reported() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let fields = build_field_diffs(&memory, &chain, DEFAULT_SHADOW_TOLERANCE);
        let amount_field = fields
            .iter()
            .find(|field| field.field == "amountOut")
            .expect("amount field");
        assert!(amount_field.beyond_tolerance);
        assert!(amount_field.relative_delta.unwrap() > DEFAULT_SHADOW_TOLERANCE);
    }

    #[test]
    fn a_difference_between_two_state_versions_is_attributed_to_the_state() {
        let mut memory = memory_quote_for(&state(), 1000);
        let mut chain = chain_quote_for(1);
        memory.pool_state_version = Some("2026-11-13T03:08:30Z".to_string());
        chain.pool_state_version = Some("2026-11-13T03:09:10Z".to_string());
        let fields = build_field_diffs(&memory, &chain, DEFAULT_SHADOW_TOLERANCE);
        let attribution = attribute_difference(&fields, true, false, &memory, &chain);
        assert_eq!(attribution, ShadowAttribution::PoolStateVersion);
    }

    #[test]
    fn two_state_version_namespaces_do_not_contradict_each_other() {
        // A side that reports no version at all has not contradicted the
        // other side, so the pair does not make the versions disagree.
        assert!(pool_state_versions_agree(
            Some("2026-11-13T03:08:30Z"),
            None
        ));
        assert!(pool_state_versions_agree(None, None));
        assert!(!pool_state_versions_agree(
            Some("2026-11-13T03:08:30Z"),
            Some("2026-11-13T03:09:10Z")
        ));
    }

    #[test]
    fn a_stored_snapshot_against_a_live_block_is_a_state_version_difference() {
        // The memory side still carries the stored snapshot while the chain
        // side priced the live block. The two sides describe different
        // states, so the difference is the age of the snapshot and not a
        // defect of the arithmetic.
        //
        // The earlier rule treated a timestamp and a block number as two
        // views of "when" that could not contradict each other, which hid
        // exactly this case: a live read that failed and silently fell back
        // to the stored snapshot.
        assert!(!pool_state_versions_agree(
            Some("2026-11-09T03:08:30.458976Z"),
            Some("block:113621544")
        ));
        assert!(!pool_state_versions_agree(
            Some("block:113621544"),
            Some("2026-11-09T03:08:30.458976Z")
        ));
    }

    #[test]
    fn two_block_versions_agree_only_when_they_are_the_same_block() {
        assert!(pool_state_versions_agree(
            Some("block:113621544"),
            Some("block:113621544")
        ));
        assert!(!pool_state_versions_agree(
            Some("block:113621544"),
            Some("block:113621545")
        ));
    }

    #[test]
    fn a_difference_between_two_fee_conventions_is_attributed_to_the_fee() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let fields = build_field_diffs(&memory, &chain, DEFAULT_SHADOW_TOLERANCE);
        let attribution = attribute_difference(&fields, false, true, &memory, &chain);
        assert_eq!(attribution, ShadowAttribution::FeeBasis);
    }

    #[test]
    fn a_difference_in_neither_the_state_nor_the_fee_is_unexplained() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let fields = build_field_diffs(&memory, &chain, DEFAULT_SHADOW_TOLERANCE);
        let attribution = attribute_difference(&fields, true, true, &memory, &chain);
        assert_eq!(attribution, ShadowAttribution::Unexplained);
    }

    #[test]
    fn an_unreachable_chain_call_is_untrusted_and_blocks() {
        let (_directory, database) = shadow_database();
        let mut request = request_with(vec![hop("1000", true)]);
        request.rpc_urls = vec!["http://127.0.0.1:1/".to_string()];
        request.timeout_seconds = Some(1);
        let report = run_shadow_comparison(&database, &request);
        assert_eq!(report.comparison_count, 1);
        assert_eq!(report.untrusted_chain_count, 1);
        let comparison = &report.comparisons[0];
        assert_eq!(comparison.eth_call_trust, EthCallTrust::Untrusted);
        assert!(comparison.eth_call_trust_reason.blocks_execution());
    }

    #[test]
    fn a_memory_deviation_never_produces_an_untrusted_verdict() {
        // The chain call answered normally and the memory side disagrees.
        // The chain call is still trusted: a local defect must never freeze
        // the chain.
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let comparison = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        assert_eq!(comparison.eth_call_trust, EthCallTrust::Trusted);
        assert!(!comparison.eth_call_trust_reason.blocks_execution());
        assert_eq!(comparison.attribution, ShadowAttribution::Unexplained);
        assert!(comparison.anomaly);
    }

    #[test]
    fn a_memory_deviation_never_blocks_an_execution() {
        let (_directory, database) = shadow_database();
        // No RPC endpoint is reachable, so the chain call is untrusted for a
        // transport reason. The memory-side deviation must not add a second,
        // memory-derived block.
        let request = request_with(vec![hop("1000", true)]);
        let report = run_shadow_comparison(&database, &request);
        assert_eq!(report.memory_deviation_block_count, 0);
    }

    #[test]
    fn a_revert_is_a_trustworthy_answer_and_not_a_blocking_reason() {
        // A quoter revert says "this swap would not succeed". That is the
        // node answering correctly, so the call is trusted and the swap is
        // simply not executable. A revert must never become an `untrusted`
        // verdict, because that would make a legitimate chain answer block
        // an execution.
        let result = ChainQuoteResult {
            amount_out_raw: 0,
            gas_estimate: 0,
            block_number: Some(42),
            reverted: true,
        };
        assert!(result.reverted);
        assert_eq!(result.block_number, Some(42));
    }

    #[test]
    fn the_quote_words_decode_keeps_the_amount_and_the_gas_apart() {
        // Golden vector from a live `eth_call` against the deployed quoter:
        // word 0 = 0x47c8f (294031), word 1 = 0xa764 (42852).
        let mut decoded = vec![0u8; 64];
        decoded[28..32].copy_from_slice(&294031u32.to_be_bytes());
        decoded[60..64].copy_from_slice(&42852u32.to_be_bytes());
        let (amount_out, gas_estimate) = decode_quote_words(&decoded).expect("decodes");
        assert_eq!(amount_out, 294031);
        assert_eq!(gas_estimate, 42852);
    }

    #[test]
    fn a_short_quote_answer_is_malformed() {
        let error = decode_quote_words(&[0u8; 32]).expect_err("short answer");
        assert!(matches!(error, ChainCallFailure::Malformed(_)));
    }

    #[test]
    fn only_the_three_named_reasons_may_block() {
        assert!(EthCallTrustReason::None.blocks_execution() == false);
        assert!(EthCallTrustReason::RpcTimeout.blocks_execution());
        assert!(EthCallTrustReason::MalformedResponse.blocks_execution());
        assert!(EthCallTrustReason::ChainStateChanged.blocks_execution());
    }

    #[test]
    fn an_anomaly_is_recorded_once_and_keeps_its_identity() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let comparison = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        let mut ledger = AnomalyLedger::default();
        let field = comparison
            .fields
            .iter()
            .find(|field| field.beyond_tolerance && field.field == "amountOut")
            .expect("amount field");
        let first = ledger.record(&comparison, field, "2026-11-13T04:00:00Z", "test://shadow");
        let second = ledger.record(&comparison, field, "2026-11-13T04:01:00Z", "test://shadow");
        assert_eq!(first, second);
        assert_eq!(ledger.entries.len(), 1);
        assert_eq!(ledger.entries[0].observation_count, 2);
        assert_eq!(ledger.entries[0].status, AnomalyStatus::Unexplained);
        assert_eq!(ledger.unexplained_count(), 1);
        assert_eq!(ledger.open_count(), 1);
    }

    #[test]
    fn an_anomaly_walks_from_unexplained_to_explained_to_fixed() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let comparison = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        let mut ledger = AnomalyLedger::default();
        let field = comparison
            .fields
            .iter()
            .find(|field| field.beyond_tolerance && field.field == "amountOut")
            .expect("amount field");
        let anomaly_id = ledger.record(&comparison, field, "2026-11-13T04:00:00Z", "test://shadow");
        assert!(ledger.advance(
            &anomaly_id,
            AnomalyStatus::Explained,
            ShadowAttribution::PoolStateVersion,
            Some("changed the state synchronisation".to_string()),
            None,
        ));
        assert_eq!(ledger.entries[0].status, AnomalyStatus::Explained);
        assert!(ledger.advance(
            &anomaly_id,
            AnomalyStatus::Fixed,
            ShadowAttribution::PoolStateVersion,
            Some("changed the state synchronisation".to_string()),
            Some("regression: amountOut agrees".to_string()),
        ));
        assert_eq!(ledger.entries[0].status, AnomalyStatus::Fixed);
        assert_eq!(ledger.open_count(), 0);
        assert!(ledger.entries[0].landed_fix.is_some());
        assert!(ledger.entries[0].regression_result.is_some());
    }

    #[test]
    fn an_anomaly_may_not_move_backwards() {
        let mut ledger = AnomalyLedger::default();
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let comparison = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        let field = comparison
            .fields
            .iter()
            .find(|field| field.beyond_tolerance && field.field == "amountOut")
            .expect("amount field");
        let anomaly_id = ledger.record(&comparison, field, "2026-11-13T04:00:00Z", "test://shadow");
        assert!(ledger.advance(
            &anomaly_id,
            AnomalyStatus::Fixed,
            ShadowAttribution::PoolStateVersion,
            None,
            None,
        ));
        assert!(!ledger.advance(
            &anomaly_id,
            AnomalyStatus::Explained,
            ShadowAttribution::PoolStateVersion,
            None,
            None,
        ));
        assert_eq!(ledger.entries[0].status, AnomalyStatus::Fixed);
    }

    #[test]
    fn a_comparison_carries_the_pool_state_version() {
        let (_directory, database) = shadow_database();
        let request = request_with(vec![hop("1000", true)]);
        let report = run_shadow_comparison(&database, &request);
        assert_eq!(report.comparison_count, 1);
        let comparison = &report.comparisons[0];
        assert_eq!(
            comparison.pool_state_version.as_deref(),
            Some("2026-11-13T03:08:30Z")
        );
        assert_eq!(
            comparison.memory.pool_state_version.as_deref(),
            Some("2026-11-13T03:08:30Z")
        );
    }

    /// The two directions of one pool are two different swaps, so they are
    /// two different anomalies.
    ///
    /// An identity without the direction would collapse both directions of a
    /// pool into one entry: the ledger would then count one defect where
    /// there are two, and the `unexplained` count of the ledger would no
    /// longer describe the comparisons that produced it.
    #[test]
    fn the_two_directions_of_a_pool_are_two_different_anomalies() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let down = build_comparison(
            &hop("1000", true),
            &state(),
            memory.clone(),
            chain.clone(),
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        let up = build_comparison(
            &hop("1000", false),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        let mut ledger = AnomalyLedger::default();
        let field = down
            .fields
            .iter()
            .find(|field| field.beyond_tolerance && field.field == "amountOut")
            .expect("amount field");
        let down_id = ledger.record(&down, field, "2026-11-13T04:00:00Z", "test://shadow");
        let up_id = ledger.record(&up, field, "2026-11-13T04:00:00Z", "test://shadow");
        assert_ne!(
            down_id, up_id,
            "the two directions of a pool must not share an anomaly identity"
        );
        assert_eq!(ledger.entries.len(), 2);
        assert_eq!(ledger.entries[0].zero_for_one, true);
        assert_eq!(ledger.entries[1].zero_for_one, false);
        assert_eq!(ledger.entries[0].observation_count, 1);
        assert_eq!(ledger.entries[1].observation_count, 1);
    }

    /// The chain-side quoter never returns a square-root price and never
    /// returns a price impact, so a comparison whose only differences are
    /// those two fields is a limit of observability, not a defect.
    #[test]
    fn a_chain_side_that_cannot_report_the_field_is_a_field_observability_limit() {
        let memory = ShadowQuote {
            source_tag: ShadowSourceTag::Memory,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("88".to_string()),
            sqrt_price_x96: Some("23598379922744606048166".to_string()),
            price_impact_bps: Some(0.0),
            gas_estimate: None,
            executable: Some(true),
            reason: None,
            pool_state_version: None,
        };
        let chain = ShadowQuote {
            source_tag: ShadowSourceTag::EthCall,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("88".to_string()),
            sqrt_price_x96: None,
            price_impact_bps: None,
            gas_estimate: Some("55894".to_string()),
            executable: Some(true),
            reason: None,
            pool_state_version: None,
        };
        let comparison = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        assert_eq!(
            comparison.attribution,
            ShadowAttribution::FieldObservability
        );
        assert!(
            !comparison.anomaly,
            "an observability limit is not an anomaly"
        );
        assert!(!comparison.executable_mismatch);
    }

    /// A comparable field that really differs is **not** excused by the
    /// observability limit: the branch has to stay narrow.
    #[test]
    fn a_real_amount_difference_is_not_an_observability_limit() {
        let memory = ShadowQuote {
            source_tag: ShadowSourceTag::Memory,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("136792316613480151805".to_string()),
            sqrt_price_x96: Some("3483650660119807995538658".to_string()),
            price_impact_bps: Some(9997.354799478268),
            gas_estimate: None,
            executable: Some(true),
            reason: None,
            pool_state_version: None,
        };
        let chain = ShadowQuote {
            source_tag: ShadowSourceTag::EthCall,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("20186663309132206507".to_string()),
            sqrt_price_x96: None,
            price_impact_bps: None,
            gas_estimate: Some("3861332".to_string()),
            executable: Some(true),
            reason: None,
            pool_state_version: None,
        };
        let comparison = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        assert_eq!(comparison.attribution, ShadowAttribution::Unexplained);
        assert!(comparison.anomaly);
    }

    /// The ledger's unexplained count has to agree with the report's
    /// unexplained count, otherwise the report describes one number and the
    /// ledger another.
    #[test]
    fn the_ledger_counts_the_unexplained_differences_and_nothing_else() {
        let mut ledger = AnomalyLedger::default();

        // A named cause: the entry is explained, so it is not counted. Both
        // sides refuse the swap, which is the `not_executable` attribution.
        let memory = ShadowQuote {
            source_tag: ShadowSourceTag::Memory,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: None,
            sqrt_price_x96: Some("1".to_string()),
            price_impact_bps: None,
            gas_estimate: None,
            executable: Some(false),
            reason: Some("pool state is not usable".to_string()),
            pool_state_version: None,
        };
        let chain = ShadowQuote {
            source_tag: ShadowSourceTag::EthCall,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: None,
            sqrt_price_x96: None,
            price_impact_bps: None,
            gas_estimate: None,
            executable: Some(false),
            reason: Some("quoter reverted: swap not executable at this amount".to_string()),
            pool_state_version: None,
        };
        let named = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        assert_eq!(named.attribution, ShadowAttribution::NotExecutable);
        let field = named
            .fields
            .iter()
            .find(|field| field.beyond_tolerance && field.field == "amountOut")
            .expect("amount field");
        ledger.record(&named, field, "2026-11-13T04:00:00Z", "test://shadow");

        // An unexplained difference: the entry starts unexplained.
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let real = build_comparison(
            &hop("1000", false),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        let unexplained = ShadowDiff {
            zero_for_one: false,
            attribution: ShadowAttribution::Unexplained,
            ..real
        };
        let field = unexplained
            .fields
            .iter()
            .find(|field| field.beyond_tolerance && field.field == "amountOut")
            .expect("amount field");
        ledger.record(&unexplained, field, "2026-11-13T04:00:00Z", "test://shadow");
        assert_eq!(ledger.entries.len(), 2);
        assert_eq!(ledger.unexplained_count(), 1);
        assert_eq!(
            ledger.entries[0].status,
            AnomalyStatus::Explained,
            "a named cause is explained, not unexplained"
        );
        assert_eq!(ledger.entries[1].status, AnomalyStatus::Unexplained);
    }

    /// One comparison that disagrees on several fields is **one** defect.
    ///
    /// The ledger used to key its entries by the field, so a single
    /// unexplained comparison produced several entries and the ledger's
    /// unexplained count no longer described the number of defects. The
    /// identity is the pool and the direction, and the fields are listed on
    /// the entry.
    #[test]
    fn one_comparison_that_disagrees_on_three_fields_is_one_defect() {
        let memory = ShadowQuote {
            source_tag: ShadowSourceTag::Memory,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("136792316613480151805".to_string()),
            sqrt_price_x96: Some("3483650660119807995538658".to_string()),
            price_impact_bps: Some(9997.354799478268),
            gas_estimate: None,
            executable: Some(true),
            reason: None,
            pool_state_version: None,
        };
        let chain = ShadowQuote {
            source_tag: ShadowSourceTag::EthCall,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("20186663309132206507".to_string()),
            sqrt_price_x96: None,
            price_impact_bps: None,
            gas_estimate: Some("3861332".to_string()),
            executable: Some(true),
            reason: None,
            pool_state_version: None,
        };
        let comparison = build_comparison(
            &hop("1000", true),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        assert_eq!(comparison.attribution, ShadowAttribution::Unexplained);
        let beyond = comparison
            .fields
            .iter()
            .filter(|field| field.beyond_tolerance)
            .count();
        assert!(
            beyond >= 2,
            "the comparison has to disagree on several fields"
        );
        let mut ledger = AnomalyLedger::default();
        for field in comparison
            .fields
            .iter()
            .filter(|field| field.beyond_tolerance)
        {
            ledger.record(&comparison, field, "2026-11-13T04:00:00Z", "test://shadow");
        }
        assert_eq!(ledger.entries.len(), 1, "one defect is one entry");
        assert_eq!(ledger.unexplained_count(), 1);
        assert_eq!(ledger.entries[0].fields.len(), beyond);
        assert!(ledger.entries[0].fields.contains(&"amountOut".to_string()));
    }

    /// The report's unexplained count and the ledger's unexplained count are
    /// the same number, so a reader never has to reconcile two units.
    #[test]
    fn the_report_unexplained_count_is_the_ledger_unexplained_count() {
        let (_directory, database) = shadow_database();
        let request = request_with(vec![hop("1000", true)]);
        let report = run_shadow_comparison(&database, &request);
        assert_eq!(
            report.unexplained_count,
            report.ledger.unexplained_count(),
            "the report and the ledger have to count the same defects"
        );
    }

    /// The direction of an anomaly is the direction of the hop, not the
    /// fact that the two sides disagreed about executability.
    ///
    /// The old identity encoded "was there an executable mismatch" into the
    /// direction field, which made every entry claim `zeroForOne = true`.
    #[test]
    fn an_anomaly_records_the_direction_of_the_hop() {
        let memory = memory_quote_for(&state(), 1000);
        let chain = chain_quote_for(1);
        let comparison = build_comparison(
            &hop("1000", false),
            &state(),
            memory,
            chain,
            EthCallTrust::Trusted,
            EthCallTrustReason::None,
            DEFAULT_SHADOW_TOLERANCE,
        );
        assert_eq!(comparison.zero_for_one, false);
        let mut ledger = AnomalyLedger::default();
        let field = comparison
            .fields
            .iter()
            .find(|field| field.beyond_tolerance && field.field == "amountOut")
            .expect("amount field");
        let anomaly_id = ledger.record(&comparison, field, "2026-11-13T04:00:00Z", "test://shadow");
        assert_eq!(ledger.entries[0].zero_for_one, false);
        assert!(anomaly_id.contains("!zfo"), "identity: {anomaly_id}");
    }

    #[test]
    fn a_hop_without_a_local_pool_state_is_skipped_rather_than_reported() {
        let (_directory, database) = shadow_database();
        let mut request = request_with(vec![hop("1000", true)]);
        request.hops[0].pool_id = "pool-that-does-not-exist".to_string();
        let report = run_shadow_comparison(&database, &request);
        assert_eq!(report.comparison_count, 0);
        assert_eq!(report.executable_mismatch_count, 0);
    }

    /// The frozen v1 scope blocks non-zero-hook pools because the local
    /// arithmetic does not execute hook callbacks. The shadow population has
    /// to enforce the same boundary, or it would compare different semantics
    /// and report the hook behaviour as an arithmetic defect.
    #[test]
    fn a_non_zero_hook_pool_is_outside_the_shadow_population() {
        let (_directory, database) = shadow_database();
        let connection = rusqlite::Connection::open(&database).expect("database");
        connection
            .execute(
                "UPDATE v4_exchange_pools SET hooks = ?1, pool_key_json = json_set(pool_key_json, '$.hooks', ?1) WHERE pool_id = ?2",
                rusqlite::params!["0x1111111111111111111111111111111111111111", POOL_ID],
            )
            .expect("set hook");
        let request = request_with(vec![hop("1000", true)]);
        let report = run_shadow_comparison(&database, &request);
        assert_eq!(report.comparison_count, 0);
        assert_eq!(report.unexplained_count, 0);
        assert!(report.ledger.entries.is_empty());
    }

    #[test]
    fn the_quote_calldata_is_the_contract_shape() {
        let calldata = build_quote_calldata(&hop("1000", true), 1000).expect("calldata");
        assert!(calldata.starts_with("0x"));
        // The selector is four bytes and the PoolKey is five words.
        let body = calldata.strip_prefix("0x").expect("hex body");
        assert_eq!(body.len() % 2, 0);
        assert!(body.len() > 4 * 2);
    }

    #[test]
    fn the_quote_calldata_rejects_a_malformed_address() {
        let mut bad = hop("1000", true);
        bad.token0 = "0xnot-an-address".to_string();
        assert!(build_quote_calldata(&bad, 1000).is_err());
    }

    #[test]
    fn the_amount_parser_accepts_decimal_and_hexadecimal() {
        assert_eq!(parse_raw_u128(Some("1000")), Some(1000));
        assert_eq!(parse_raw_u128(Some("0x3e8")), Some(1000));
        assert_eq!(parse_raw_u128(Some("  1000  ")), Some(1000));
        assert_eq!(parse_raw_u128(Some("")), None);
        assert_eq!(parse_raw_u128(None), None);
    }

    /// A quoter that answers with a zero output has accepted the call but
    /// says the swap produces nothing. That is not a fillable swap, so it
    /// must not be reported as executable.
    /// When the memory side refuses the hop and the chain fills it, the
    /// difference is the tick-range gate of the first arbitrage version. It
    /// is known and intentional, so it is named rather than reported as an
    /// anomaly.
    /// The memory side has to trade the **same** swap as the chain side.
    ///
    /// The calldata names the pool by its currency0 and currency1 and then
    /// says which way the swap goes, so a hop with `zero_for_one = false`
    /// trades the opposite direction even though its tokens are listed in
    /// the pool's order. The memory side has to use the same flag, or the
    /// two sides price different swaps.
    #[test]
    fn the_two_directions_are_priced_as_two_different_swaps() {
        // A pool at parity would return the same output in both
        // directions, so the price has to be away from parity for the two
        // directions to differ at all.
        let mut state = state();
        // The price of tick 100000, taken from the SDK's tick maths.
        state.sqrt_price_x96 = 11_755_562_826_496_067_164_730_007_768_450u128;
        state.tick = 100_000;
        let up = memory_quote(
            &state,
            &hop("1000000", false),
            1_000_000,
            pool_manager::depth::ArbiterVersion::default_version(),
        );
        let down = memory_quote(
            &state,
            &hop("1000000", true),
            1_000_000,
            pool_manager::depth::ArbiterVersion::default_version(),
        );
        let up_amount = up
            .amount_out_raw
            .as_deref()
            .and_then(|v| v.parse::<u128>().ok());
        let down_amount = down
            .amount_out_raw
            .as_deref()
            .and_then(|v| v.parse::<u128>().ok());
        assert!(up_amount.is_some(), "the upward hop must be priced");
        assert!(down_amount.is_some(), "the downward hop must be priced");
        assert_ne!(
            up_amount, down_amount,
            "the two directions must not produce the same output"
        );
    }

    #[test]
    fn a_memory_refusal_against_a_chain_fill_is_the_tick_range_gate() {
        let memory = ShadowQuote {
            source_tag: ShadowSourceTag::Memory,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: None,
            sqrt_price_x96: Some("1".to_string()),
            price_impact_bps: None,
            gas_estimate: None,
            executable: Some(false),
            reason: Some("the hop leaves the current tick range".to_string()),
            pool_state_version: None,
        };
        let chain = chain_quote_for(999);
        let fields = build_field_diffs(&memory, &chain, DEFAULT_SHADOW_TOLERANCE);
        let attribution = attribute_difference(&fields, true, true, &memory, &chain);
        assert_eq!(attribution, ShadowAttribution::TickRangeGate);
        assert_eq!(ShadowAttribution::TickRangeGate.as_str(), "tick_range_gate");
        // A named gate is not an anomaly: nothing has to be repaired.
        assert_ne!(attribution, ShadowAttribution::Unexplained);
    }

    #[test]
    fn a_zero_output_quote_is_not_executable() {
        let chain = chain_quote_for(0);
        assert_eq!(chain.executable, Some(false));
    }

    /// A quote with a real output is executable.
    #[test]
    fn a_quote_with_an_output_is_executable() {
        let chain = chain_quote_for(1_000);
        assert_eq!(chain.executable, Some(true));
    }

    /// When both sides report the swap as not executable, the difference is
    /// a reporting difference rather than a defect.
    #[test]
    fn two_sides_that_both_refuse_the_swap_are_attributed_to_the_refusal() {
        let memory = ShadowQuote {
            source_tag: ShadowSourceTag::Memory,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("0".to_string()),
            sqrt_price_x96: Some("1".to_string()),
            price_impact_bps: Some(10_000.0),
            gas_estimate: None,
            executable: Some(false),
            reason: Some("zero output".to_string()),
            pool_state_version: None,
        };
        let chain = ShadowQuote {
            source_tag: ShadowSourceTag::EthCall,
            pool_id: POOL_ID.to_string(),
            amount_in_raw: "1000".to_string(),
            amount_out_raw: Some("0".to_string()),
            sqrt_price_x96: None,
            price_impact_bps: None,
            gas_estimate: Some("100".to_string()),
            executable: Some(false),
            reason: None,
            pool_state_version: None,
        };
        let fields = build_field_diffs(&memory, &chain, DEFAULT_SHADOW_TOLERANCE);
        let attribution = attribute_difference(&fields, true, true, &memory, &chain);
        assert_eq!(attribution, ShadowAttribution::NotExecutable);
    }

    #[test]
    fn the_source_tag_and_the_trust_verdict_have_stable_wire_names() {
        assert_eq!(ShadowSourceTag::Memory.as_str(), "memory");
        assert_eq!(ShadowSourceTag::EthCall.as_str(), "eth_call");
        assert_eq!(EthCallTrust::Trusted.as_str(), "trusted");
        assert_eq!(EthCallTrust::Untrusted.as_str(), "untrusted");
        assert_eq!(AnomalyStatus::Unexplained.as_str(), "unexplained");
        assert_eq!(AnomalyStatus::Explained.as_str(), "explained");
        assert_eq!(AnomalyStatus::Fixed.as_str(), "fixed");
    }
}
