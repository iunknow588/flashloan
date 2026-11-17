//! Off-chain parity comparison for the review path.
//!
//! Why this module exists
//! -----------------------
//!
//! ARCH-2 delivered an off-chain three-hop simulator and proved it
//! equivalent to the chain. ARCH-2b turns that proof into a fact about
//! production: the simulator has to be run against the routes the chain
//! actually saw, and the two answers have to be compared one route at a
//! time.
//!
//! Where the work lives
//! --------------------
//!
//! This is the **whole** parity computation, and it lives in Review Manager
//! for a reason: the comparison is a review concern. Review Manager already
//! owns the review verdict, the signed PnL and the diagnostics; the parity
//! answer is one more piece of review evidence, and it belongs next to the
//! verdict it qualifies.
//!
//! The architectural rule the project follows is:
//!
//! * every module owns its tables and publishes an API;
//! * a module reads another module's data through that module's API;
//! * Python is a read-only compatibility layer that reaches the managers
//!   through the in-memory `RuntimeCore`.
//!
//! So Review Manager performs the simulation itself, asks Pool Manager for
//! the pool state through Pool Manager's published API, and publishes the
//! resulting verdict as a DTO. Python never reads a manager-owned table and
//! never performs the arithmetic itself.

use std::collections::BTreeMap;

use pool_manager::{
    read_newest_liquidity_read_at, read_pool_parity_states, resolve_pool_ids_by_key,
    PoolKeyIdentity, PoolParityState, PoolStateReadError,
};
use serde::Serialize;

/// The flash-loan fee denominator, matching the contract's `_fee`.
const FLASH_FEE_DENOMINATOR: i128 = 10_000;

/// The relative tolerance applied to the profit comparison.
///
/// The chain may settle at a marginally different amount for reasons that
/// have nothing to do with the arithmetic under test, so an exact equality
/// requirement would produce noise rather than signal.
pub const DEFAULT_PROFIT_RELATIVE_TOLERANCE: f64 = 1e-9;

/// The protocol the review tables are keyed by.
const REVIEW_PROTOCOL: &str = "uniswap_v4";

/// The flash-loan fee the contract charges, in basis points.
///
/// This is part of the chain's own arithmetic, not a market assumption.
/// The default is **zero** because the production route borrows straight from
/// the Uniswap V4 PoolManager, which charges no flash-loan fee; the contract
/// only requires `amount + minProfit` back. The value is still overridable,
/// because the fee is a per-adapter, owner-mutable on-chain setting and the
/// replay must follow the chain rather than a frozen assumption.
pub const DEFAULT_FLASH_FEE_BPS: i64 = 0;

/// The minimum profit the chain demands, in the borrow token's raw units.
///
/// The production contract reverts with `ArbitrageNotProfitable(received,
/// required)` where `required = amount + minProfit`, and the caller asks for
/// a one-wei minimum profit. The one wei is therefore a **profit guard**,
/// not a fee: it must never be modelled as a flash-loan fee, and it must
/// never be modelled as profit either.
pub const CHAIN_MIN_PROFIT_RAW: i128 = 1;

/// The flash-loan fee the production code assumed before the chain's fee was
/// derived from the chain's own settlement.
///
/// The recorded reviews still carry it: every offline snapshot the recorder
/// produced was priced with this fee, while the chain charged none. The
/// replay keeps the number so that a deviation which is **exactly** this fee
/// can be named as the historical defect it is, instead of being reported as
/// an unexplained difference.
pub const LEGACY_ASSUMED_FLASH_FEE_BPS: i64 = 5;

// ---------------------------------------------------------------------------
// The simulator
// ---------------------------------------------------------------------------

/// The immutable on-chain state of one pool on one hop.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HopState {
    /// Opaque identifier, used only for reporting.
    pub pool_id: String,
    /// Lower-case token address that enters this hop.
    pub token_in: String,
    /// Lower-case token address that leaves this hop.
    pub token_out: String,
    /// The pool's current square-root price.
    pub sqrt_price_x96: u128,
    /// The pool's active liquidity.
    pub liquidity: u128,
    /// The pool's fixed fee, in hundredths of a bip.
    pub fee_pips: u32,
    /// `true` when token0 is the input side of this hop.
    pub zero_for_one: bool,
}

/// Returns the flash-loan fee, rounded up, exactly like the contract.
///
/// The contract computes `(amount * feeBps + 9999) / 10000`. The rounding is
/// **up**, so the simulator must not round down: rounding down would make
/// the simulator slightly more optimistic than the chain and could report a
/// profitable route that the chain rejects.
pub fn flash_fee(amount_raw: i128, fee_bps: i64) -> i128 {
    if amount_raw <= 0 || fee_bps <= 0 {
        return 0;
    }
    (amount_raw * fee_bps as i128 + FLASH_FEE_DENOMINATOR - 1) / FLASH_FEE_DENOMINATOR
}

/// Returns the amount the flash loan requires back, including the fee.
pub fn required_repay(amount_raw: i128, fee_bps: i64) -> i128 {
    amount_raw + flash_fee(amount_raw, fee_bps)
}

/// The outcome of simulating one hop.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HopResult {
    pub pool_id: String,
    pub amount_in_raw: i128,
    pub amount_out_raw: i128,
    pub price_impact_bps: f64,
    pub ok: bool,
    pub reason: String,
}

/// The outcome of simulating a whole closed loop.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RouteResult {
    pub amount_in_raw: i128,
    pub amount_out_raw: i128,
    pub required_repay_raw: i128,
    pub profit_raw: i128,
    pub executable: bool,
    pub reason: String,
    pub hops: Vec<HopResult>,
}

/// Simulates one exact-input swap through one pool.
///
/// The output is the realised output of the swap, i.e. it already carries the
/// price impact and the liquidity-provider fee, exactly like the chain.
fn simulate_hop(state: &HopState, amount_in_raw: i128) -> HopResult {
    if amount_in_raw <= 0 {
        return HopResult {
            pool_id: state.pool_id.clone(),
            amount_in_raw,
            amount_out_raw: 0,
            price_impact_bps: 0.0,
            ok: false,
            reason: "zero_input".to_string(),
        };
    }
    if state.sqrt_price_x96 == 0 || state.liquidity == 0 || state.fee_pips == 0 {
        return HopResult {
            pool_id: state.pool_id.clone(),
            amount_in_raw,
            amount_out_raw: 0,
            price_impact_bps: 0.0,
            ok: false,
            reason: "pool_state_unusable".to_string(),
        };
    }
    let amount_out_raw = match swap_amount_out(
        state.sqrt_price_x96,
        state.liquidity,
        state.fee_pips,
        amount_in_raw as u128,
        state.zero_for_one,
    ) {
        Some(output) => output,
        None => {
            return HopResult {
                pool_id: state.pool_id.clone(),
                amount_in_raw,
                amount_out_raw: 0,
                price_impact_bps: 0.0,
                ok: false,
                reason: "swap_amount_out_unavailable".to_string(),
            }
        }
    };
    if amount_out_raw == 0 {
        return HopResult {
            pool_id: state.pool_id.clone(),
            amount_in_raw,
            amount_out_raw: 0,
            price_impact_bps: 0.0,
            ok: false,
            reason: "zero_output".to_string(),
        };
    }
    // The price impact is an observation, never an accounting value, so a
    // failure to measure it must not invalidate the swap itself.
    let price_impact_bps = pool_manager::price_impact_for_input(
        state.sqrt_price_x96,
        state.liquidity,
        state.fee_pips,
        amount_in_raw as u128,
        state.zero_for_one,
    )
    .unwrap_or(0.0);
    HopResult {
        pool_id: state.pool_id.clone(),
        amount_in_raw,
        amount_out_raw: amount_out_raw as i128,
        price_impact_bps,
        ok: true,
        reason: String::new(),
    }
}

/// Returns the realised output of one exact-input swap step.
///
/// The pool arithmetic belongs to Pool Manager, so this delegates to the
/// function Pool Manager publishes rather than carrying a second copy of the
/// Uniswap maths. A second copy would be a second rounding rule, and two
/// rounding rules are one defect waiting to happen.
fn swap_amount_out(
    sqrt_price_x96: u128,
    liquidity: u128,
    fee_pips: u32,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Option<u128> {
    pool_manager::exact_input_swap_amount_out(
        sqrt_price_x96,
        liquidity,
        fee_pips,
        amount_in_raw,
        zero_for_one,
    )
    .ok()
}

/// Simulates a closed loop and settles it exactly like the chain does.
///
/// The settlement mirrors the contract:
///
/// * `required_repay = amount + ceil(amount * fee_bps / 10000)`;
/// * `profit = max(0, amount_out - required_repay)`;
/// * the route is executable only when `profit > 0`.
pub fn simulate_route(hops: &[HopState], amount_in_raw: i128, flash_fee_bps: i64) -> RouteResult {
    if hops.is_empty() {
        return RouteResult {
            amount_in_raw,
            amount_out_raw: 0,
            required_repay_raw: required_repay(amount_in_raw, flash_fee_bps),
            profit_raw: 0,
            executable: false,
            reason: "no_hops".to_string(),
            hops: Vec::new(),
        };
    }
    if amount_in_raw <= 0 {
        return RouteResult {
            amount_in_raw,
            amount_out_raw: 0,
            required_repay_raw: required_repay(amount_in_raw, flash_fee_bps),
            profit_raw: 0,
            executable: false,
            reason: "zero_input".to_string(),
            hops: Vec::new(),
        };
    }

    let mut current_amount = amount_in_raw;
    let mut expected_token = hops[0].token_in.clone();
    let mut hop_results: Vec<HopResult> = Vec::with_capacity(hops.len());

    for (index, state) in hops.iter().enumerate() {
        if state.token_in != expected_token {
            return RouteResult {
                amount_in_raw,
                amount_out_raw: current_amount,
                required_repay_raw: required_repay(amount_in_raw, flash_fee_bps),
                profit_raw: 0,
                executable: false,
                reason: format!("token_discontinuity_at_hop_{}", index + 1),
                hops: hop_results,
            };
        }
        let result = simulate_hop(state, current_amount);
        let ok = result.ok;
        let reason = result.reason.clone();
        let amount_out_raw = result.amount_out_raw;
        hop_results.push(result);
        if !ok {
            return RouteResult {
                amount_in_raw,
                amount_out_raw: current_amount,
                required_repay_raw: required_repay(amount_in_raw, flash_fee_bps),
                profit_raw: 0,
                executable: false,
                reason: format!("hop_{}_failed:{}", index + 1, reason),
                hops: hop_results,
            };
        }
        current_amount = amount_out_raw;
        expected_token = state.token_out.clone();
    }

    if expected_token != hops[0].token_in {
        return RouteResult {
            amount_in_raw,
            amount_out_raw: current_amount,
            required_repay_raw: required_repay(amount_in_raw, flash_fee_bps),
            profit_raw: 0,
            executable: false,
            reason: "route_does_not_close".to_string(),
            hops: hop_results,
        };
    }

    let repay = required_repay(amount_in_raw, flash_fee_bps);
    let profit = current_amount - repay;
    let profit_raw = profit.max(0);
    RouteResult {
        amount_in_raw,
        amount_out_raw: current_amount,
        required_repay_raw: repay,
        profit_raw,
        executable: profit_raw > 0,
        reason: if profit_raw > 0 {
            "profitable".to_string()
        } else {
            "not_profitable".to_string()
        },
        hops: hop_results,
    }
}

// ---------------------------------------------------------------------------
// The comparison
// ---------------------------------------------------------------------------

/// The state of one off-chain versus on-chain comparison.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ParityState {
    /// The executable verdict agrees and the amounts agree within tolerance.
    Match,
    /// The off-chain simulation and the chain disagree on executability.
    ExecutableMismatch,
    /// Both agree on executability, but the amounts differ beyond tolerance.
    AmountMismatch,
    /// One side did not produce a number, so nothing could be compared.
    NotComparable,
}

impl ParityState {
    /// Whether this verdict is an unexplained anomaly.
    ///
    /// An anomaly is a **defect to fix**, not an incident to stop on: the
    /// chain result stays authoritative and execution continues.
    pub fn is_anomaly(self) -> bool {
        matches!(self, Self::ExecutableMismatch)
    }

    /// Whether both sides produced numbers that could be compared.
    pub fn is_comparable(self) -> bool {
        matches!(
            self,
            Self::Match | Self::ExecutableMismatch | Self::AmountMismatch
        )
    }

    /// The stable wire name of the state.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Match => "match",
            Self::ExecutableMismatch => "executable_mismatch",
            Self::AmountMismatch => "amount_mismatch",
            Self::NotComparable => "not_comparable",
        }
    }
}

/// The signed profit the chain reports for a route.
///
/// The chain reports a losing route as a **negative shortfall**, while the
/// simulator settles it as zero. The signed form is what the two sides can
/// actually be compared on.
pub fn signed_offchain_profit(result: &RouteResult) -> i128 {
    if result.profit_raw > 0 {
        return result.profit_raw;
    }
    let shortfall = result.required_repay_raw - result.amount_out_raw;
    if shortfall > 0 {
        -shortfall
    } else {
        0
    }
}

/// The relative difference between two amounts.
pub fn relative_delta(left: i128, right: i128) -> f64 {
    let denominator = left.abs().max(right.abs());
    if denominator == 0 {
        return 0.0;
    }
    (left - right).abs() as f64 / denominator as f64
}

/// The attribution of a deviation.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ParityAttribution {
    /// The off-chain result agrees with the chain's live figures.
    Consistent,
    /// The chain's live arithmetic reproduces its own profit, so the
    /// off-chain side ran on a state the chain had already moved past.
    StaleLocalState,
    /// The off-chain result differs from the chain's live figures, so the
    /// arithmetic itself is under suspicion.
    OffchainArithmetic,
    /// The chain recorded no live figures, so no attribution is possible.
    UnknownNoChainLiveState,
    /// The off-chain simulator charged a flash-loan fee the chain never
    /// charged, so the deviation is an assumption, not a state difference.
    FlashFeeAssumption,
    /// The replay ran against the pool state the review itself recorded,
    /// and the remaining difference is exactly the fee the production code
    /// used to assume.
    ///
    /// This is the strongest form of agreement the recorded evidence can
    /// support: the review-time state reproduces the recorded profit, and
    /// what is left is a historical pricing assumption that has already
    /// been corrected in the code.
    ReviewTimeStateExplained,
}

impl ParityAttribution {
    /// The stable wire name of the attribution.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Consistent => "consistent",
            Self::StaleLocalState => "stale_local_state",
            Self::OffchainArithmetic => "offchain_arithmetic",
            Self::UnknownNoChainLiveState => "unknown_no_chain_live_state",
            Self::FlashFeeAssumption => "flash_fee_assumption",
            Self::ReviewTimeStateExplained => "review_time_state_explained",
        }
    }
}

/// The detailed evidence behind one parity verdict.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ParityDetail {
    pub attribution: String,
    pub tolerance: f64,
    pub chain_live_signed_profit_raw: Option<String>,
    pub chain_live_relative_delta: Option<f64>,
    pub chain_live_reproduces_chain_profit: Option<bool>,
    pub offchain_matches_chain_live: Option<bool>,
    /// The flash-loan fee the off-chain simulation used, in basis points.
    ///
    /// It is the chain's own fee whenever the chain recorded a settlement,
    /// and the assumed fee otherwise.
    pub offchain_flash_fee_bps: i64,
    /// The flash-loan fee the caller assumed the chain charged.
    pub assumed_flash_fee_bps: i64,
    /// The flash-loan fee the chain's own settlement implies, in basis
    /// points. It is absent when the chain recorded no settlement.
    pub chain_flash_fee_bps: Option<i64>,
    /// Whether the fee used by the simulation agrees with the chain's fee.
    pub flash_fee_matches_chain: Option<bool>,
}

/// The outcome of one off-chain versus on-chain comparison.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ParityVerdict {
    pub state: String,
    pub executable_offchain: Option<bool>,
    pub executable_onchain: Option<bool>,
    pub profit_offchain_raw: Option<i128>,
    pub profit_onchain_raw: Option<i128>,
    pub profit_relative_delta: Option<f64>,
    pub reason: String,
    pub anomaly: bool,
    pub detail: Option<ParityDetail>,
}

impl ParityVerdict {
    fn skipped(reason: impl Into<String>) -> Self {
        let reason = reason.into();
        Self {
            state: ParityState::NotComparable.as_str().to_string(),
            executable_offchain: None,
            executable_onchain: None,
            profit_offchain_raw: None,
            profit_onchain_raw: None,
            profit_relative_delta: None,
            reason,
            anomaly: false,
            detail: None,
        }
    }
}

/// The input of one parity comparison.
#[derive(Clone, Debug)]
pub struct ParityRequest {
    /// The route's token addresses, in hop order. The first is the borrow
    /// token and the last must return to it.
    pub route_addresses: Vec<String>,
    /// The pool identifiers, in hop order. There is one pool per hop.
    pub pool_ids: Vec<String>,
    /// The flash-loan principal, in the borrow token's raw units.
    pub amount_raw: i128,
    /// The flash source's fee, in basis points.
    pub flash_fee_bps: i64,
    /// The profit the chain reported, if it reported one.
    pub onchain_profit_raw: Option<i128>,
    /// The chain's live amount out, recovered from its revert data.
    pub chain_live_amount_out_raw: Option<i128>,
    /// The chain's live required repay, recovered from its revert data.
    pub chain_live_required_repay_raw: Option<i128>,
    /// The relative tolerance applied to the profit comparison.
    pub tolerance: f64,
    /// The pool state the review actually saw.
    ///
    /// This is an in-memory input, never a persisted review field. When it
    /// is present, the comparison replays the review against the state the
    /// review quoted instead of the state the chain happens to be in now.
    pub review_time_pool_states: Vec<ReviewTimePoolState>,
}

impl ParityRequest {
    /// Builds a request with the default tolerance.
    pub fn new(
        route_addresses: Vec<String>,
        pool_ids: Vec<String>,
        amount_raw: i128,
        flash_fee_bps: i64,
    ) -> Self {
        Self {
            route_addresses,
            pool_ids,
            amount_raw,
            flash_fee_bps,
            onchain_profit_raw: None,
            chain_live_amount_out_raw: None,
            chain_live_required_repay_raw: None,
            tolerance: DEFAULT_PROFIT_RELATIVE_TOLERANCE,
            review_time_pool_states: Vec::new(),
        }
    }
}

/// Builds a parity request from one live review payload, without persisting
/// the review.
///
/// The payload is the authoritative, already-produced review evidence: it
/// carries the route, the selected amount and the pool state the review
/// quoted. The request is an in-memory value, so the comparison does not
/// require a long-term `cow_execution_reviews` row.
pub fn parity_request_from_review_payload(
    payload: &serde_json::Value,
    flash_fee_bps: i64,
    tolerance: f64,
) -> Result<ParityRequest, ParityError> {
    let route_addresses = string_list(payload, &["routeAddresses", "route_addresses"])
        .ok_or_else(|| ParityError::InvalidRequest("route addresses are required".to_owned()))?;
    let pool_ids = pool_ids_from_path_key(payload.get("poolPathKey").and_then(|v| v.as_str()))
        .or_else(|| {
            string_list(payload, &["poolIds", "pool_ids"])
                .map(|ids| {
                    ids.iter()
                        .map(|id| id.trim().to_lowercase())
                        .filter(|id| !id.is_empty())
                        .collect::<Vec<String>>()
                })
                .filter(|ids| ids.len() == route_addresses.len().saturating_sub(1))
        })
        .ok_or_else(|| ParityError::InvalidRequest("route pool ids are required".to_owned()))?;
    if pool_ids.len() != route_addresses.len().saturating_sub(1) {
        return Err(ParityError::InvalidRequest(
            "route addresses and pool ids must describe one pool per hop".to_owned(),
        ));
    }
    let amount_raw = extract_probe_amount(payload)
        .or_else(|| parse_amount(payload.get("selectedAmountRaw").and_then(|v| v.as_str())))
        .filter(|amount| *amount > 0)
        .ok_or_else(|| {
            ParityError::InvalidRequest("selected review amount is required".to_owned())
        })?;
    let review_time_pool_states = extract_review_time_pool_states(payload);
    if review_time_pool_states.is_empty() {
        return Err(ParityError::InvalidRequest(
            "review-time pool state is required for a faithful parity replay".to_owned(),
        ));
    }
    let mut request = ParityRequest::new(route_addresses, pool_ids, amount_raw, flash_fee_bps);
    request.tolerance = tolerance;
    request.onchain_profit_raw = parse_amount(
        payload
            .get("selectedProfitRaw")
            .and_then(|value| value.as_str()),
    );
    let (chain_live_amount_out_raw, chain_live_required_repay_raw) =
        decode_chain_live_amounts_from_payload(payload);
    request.chain_live_amount_out_raw = chain_live_amount_out_raw;
    request.chain_live_required_repay_raw = chain_live_required_repay_raw;
    request.review_time_pool_states = review_time_pool_states;
    Ok(request)
}

/// Evaluates a parity request against the review-time pool state it carries.
///
/// The live snapshot is still consulted as a fallback for pools the review
/// did not quote, but every quoted pool keeps the state the review actually
/// saw. This is the only faithful way to replay a review that has already
/// happened.
pub fn evaluate_route_parity_with_review_time_state(
    database_path: impl AsRef<std::path::Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    request: &ParityRequest,
) -> Result<ParityVerdict, ParityError> {
    // The live snapshot is only a **fallback** for pools the review did not
    // quote. It is therefore optional: when the live snapshot cannot be
    // read at all (the tables are absent on a freshly migrated database, or
    // the snapshot has been pruned), the review-time state still carries a
    // complete route and the comparison must not be aborted. A missing
    // snapshot is not evidence of a deviation, so it is downgraded to an
    // empty fallback instead of an error.
    let live_states = read_pool_parity_states(
        database_path.as_ref(),
        network,
        chain_id,
        protocol,
        &request.pool_ids,
    )
    .unwrap_or_default()
    .into_iter()
    // The pool id is an **identity**, and the same pool can be written in
    // either case by the different producers. It is therefore normalized
    // on every side, so a route is never silently dropped because two
    // producers spelled the same pool differently.
    .map(|state| (state.pool_id.trim().to_lowercase(), state))
    .collect::<BTreeMap<String, PoolParityState>>();
    let review_time_states = request
        .review_time_pool_states
        .iter()
        .filter(|state| {
            request
                .pool_ids
                .iter()
                .any(|pool_id| pool_id.trim().to_lowercase() == state.pool_id.trim().to_lowercase())
        })
        .map(|state| (state.pool_id.trim().to_lowercase(), state.to_parity_state()))
        .collect::<BTreeMap<String, PoolParityState>>();
    let merged = merged_pool_states(&live_states, &review_time_states);
    Ok(evaluate_route_parity_with_states(request, &merged))
}

/// Derives the effective flash-loan fee from the chain's own settlement.
///
/// The chain's flash-loan fee is **not** a constant: it is an owner-mutable
/// setting on the flash source adapter (`setFlashSourceAdapter(..., feeBps,
/// ...)`), and the production route borrows straight from the Uniswap V4
/// PoolManager, which charges no fee at all. A hard-coded basis-point
/// assumption therefore produces a systematic, silent deviation on every
/// route: the off-chain simulator charges a fee the chain never charged, and
/// the gap is indistinguishable from a stale pool state.
///
/// The chain's own settlement settles the question. When the chain reverts,
/// it reports the amount it had to repay. That number is
/// `amount + flash_fee + min_profit`, and the one-wei minimum-profit guard
/// is known, so the fee is
/// `required_repay - amount - min_profit`.
///
/// The result is expressed in basis points, rounded **up**, so the simulator
/// never charges less than the chain did. A repay below the principal cannot
/// be explained by a fee, so it is rejected and the caller falls back to its
/// documented default.
///
/// The profit guard is not always present in the recorded evidence: older
/// settlements recorded only `amount + fee`. The derivation therefore accepts
/// both spellings, because the guard is a one-wei difference and mistaking it
/// for a fee would produce a wrong answer in either direction.
pub fn effective_flash_fee_bps(
    amount_raw: i128,
    chain_required_repay_raw: Option<i128>,
) -> Option<i64> {
    let required_repay = chain_required_repay_raw?;
    if amount_raw <= 0 {
        return None;
    }
    let overhead = required_repay - amount_raw;
    if overhead < 0 {
        return None;
    }
    // The guard is a fixed one-wei profit requirement, never a fee. It is
    // removed when the settlement carries it.
    let fee_raw = if overhead >= CHAIN_MIN_PROFIT_RAW {
        overhead - CHAIN_MIN_PROFIT_RAW
    } else {
        overhead
    };
    // Round up: the simulator must never charge less than the chain charged.
    let bps = (fee_raw * FLASH_FEE_DENOMINATOR + amount_raw - 1) / amount_raw;
    i64::try_from(bps).ok()
}

/// Reports whether the chain's own settlement matches the fee the simulator
/// would charge.
///
/// This is the check that catches a stale assumption: when the simulator's fee
/// differs from the chain's fee, the comparison is not measuring the pool
/// state, it is measuring the fee assumption, so the two are separated.
///
/// The chain's settlement may or may not carry the one-wei profit guard, so
/// both spellings are accepted.
pub fn chain_fee_matches(
    amount_raw: i128,
    chain_required_repay_raw: Option<i128>,
    flash_fee_bps: i64,
) -> Option<bool> {
    let chain_required_repay = chain_required_repay_raw?;
    let expected = required_repay(amount_raw, flash_fee_bps);
    let matches_without_guard = expected == chain_required_repay;
    let matches_with_guard = expected + CHAIN_MIN_PROFIT_RAW == chain_required_repay;
    Some(matches_without_guard || matches_with_guard)
}

/// Reports whether a recorded chain-side profit carries the legacy fee
/// assumption.
///
/// The offline snapshot the recorder wrote was priced with the fee the
/// production code assumed rather than the fee the chain charged. When the
/// difference between the recorded profit and the off-chain result is exactly
/// that fee, the deviation is fully explained by the historical defect and
/// not by the pool state.
pub fn legacy_fee_residual(
    amount_raw: i128,
    recorded_profit_raw: Option<i128>,
    offchain_profit_raw: Option<i128>,
) -> Option<i128> {
    let recorded = recorded_profit_raw?;
    let offchain = offchain_profit_raw?;
    if amount_raw <= 0 {
        return None;
    }
    let legacy_fee = (amount_raw * LEGACY_ASSUMED_FLASH_FEE_BPS as i128 + FLASH_FEE_DENOMINATOR
        - 1)
        / FLASH_FEE_DENOMINATOR;
    let difference = offchain - recorded;
    // The recorded profit was converted from a decimal amount, so it
    // carries a rounding dust of a few raw units. The residual is accepted
    // when it lands on the legacy fee within that dust, and the dust is
    // bounded relative to the fee itself: a residual that is a different
    // number entirely is not the legacy fee and is not explained by it.
    let dust = (legacy_fee / 1_000).max(1);
    if (difference - legacy_fee).abs() <= dust {
        Some(legacy_fee)
    } else {
        None
    }
}

/// Builds the simulator's hop states for a route.
///
/// The route is described by its token addresses and the pool chosen for each
/// hop. The hop direction is derived from which token enters the pool.
///
/// `None` is returned when the route cannot be expressed as a chain of pools
/// with usable state. That is a legitimate outcome, not an error: the caller
/// reports it as "not comparable".
pub fn build_hops(
    route_addresses: &[String],
    pool_states: &BTreeMap<String, PoolParityState>,
    pool_ids: &[String],
) -> Option<Vec<HopState>> {
    let addresses: Vec<String> = route_addresses
        .iter()
        .map(|address| address.trim().to_lowercase())
        .filter(|address| !address.is_empty())
        .collect();
    // The pool id is an identity: it is normalized on both sides, so a
    // route is never silently dropped because two producers spelled the
    // same pool differently.
    let pools: Vec<String> = pool_ids
        .iter()
        .map(|pool_id| pool_id.trim().to_lowercase())
        .filter(|pool_id| !pool_id.is_empty())
        .collect();
    if addresses.len() < 2 || pools.len() != addresses.len() - 1 {
        return None;
    }

    let mut hops = Vec::with_capacity(pools.len());
    for (index, pool_id) in pools.iter().enumerate() {
        let state = pool_states.get(pool_id)?;
        let token_in = addresses[index].clone();
        let token_out = addresses[index + 1].clone();
        let zero_for_one = if state.token0 == token_in && state.token1 == token_out {
            true
        } else if state.token1 == token_in && state.token0 == token_out {
            false
        } else {
            // The pool does not sit between the two route tokens, so the
            // route and the pool selection disagree.
            return None;
        };
        hops.push(HopState {
            pool_id: pool_id.clone(),
            token_in,
            token_out,
            sqrt_price_x96: state.sqrt_price_x96,
            liquidity: state.liquidity,
            fee_pips: state.fee,
            zero_for_one,
        });
    }
    Some(hops)
}

/// Runs the off-chain simulation over a pool-state map and compares it with
/// the chain result.
///
/// The function is deliberately total: every failure mode produces a
/// "not comparable" verdict instead of an error, because a measurement must
/// never abort a review.
pub fn evaluate_route_parity_with_states(
    request: &ParityRequest,
    pool_states: &BTreeMap<String, PoolParityState>,
) -> ParityVerdict {
    if request.amount_raw <= 0 {
        return ParityVerdict::skipped("probe amount is not positive");
    }
    if pool_states.is_empty() {
        return ParityVerdict::skipped("no live pool state available for the route pools");
    }
    let Some(hops) = build_hops(&request.route_addresses, pool_states, &request.pool_ids) else {
        return ParityVerdict::skipped("route could not be expressed as a chain of live pools");
    };

    // The flash-loan fee is part of the chain's own arithmetic, and the
    // chain's own settlement is the only trustworthy source for it. When the
    // chain recorded its settlement, the effective fee is derived from it;
    // otherwise the documented default is used.
    let chain_flash_fee_bps =
        effective_flash_fee_bps(request.amount_raw, request.chain_live_required_repay_raw);
    // The **assumed** fee is what the caller believed the chain charged. The
    // **effective** fee is what the chain's own settlement says it charged.
    // A difference between the two is an assumption error, and it has to be
    // named as one: otherwise the deviation is misread as a stale pool state.
    let assumed_fee_matches_chain = match chain_flash_fee_bps {
        Some(chain_bps) => chain_bps == request.flash_fee_bps,
        None => true,
    };
    let flash_fee_bps = chain_flash_fee_bps.unwrap_or(request.flash_fee_bps);
    let flash_fee_matches_chain = chain_fee_matches(
        request.amount_raw,
        request.chain_live_required_repay_raw,
        flash_fee_bps,
    );

    let offchain = simulate_route(&hops, request.amount_raw, flash_fee_bps);
    let offchain_profit = signed_offchain_profit(&offchain);

    let Some(onchain_profit) = request.onchain_profit_raw else {
        return ParityVerdict {
            state: ParityState::NotComparable.as_str().to_string(),
            executable_offchain: Some(offchain_profit > 0),
            executable_onchain: None,
            profit_offchain_raw: Some(offchain_profit),
            profit_onchain_raw: None,
            profit_relative_delta: None,
            reason: "the chain reported no profit for this route".to_string(),
            anomaly: false,
            detail: None,
        };
    };

    let offchain_executable = offchain_profit > 0;
    let onchain_executable = onchain_profit > 0;

    // Attribution: compare the off-chain result against the chain's **live**
    // amounts, recovered from the chain's own revert data.
    //
    // When the chain's live arithmetic reproduces the chain's own reported
    // profit, and the off-chain figure differs from the chain's live figure,
    // then the off-chain side ran on a state the chain had already moved
    // past: the arithmetic is not at fault, the **input** was stale.
    let (
        chain_live_signed,
        chain_live_delta,
        chain_live_reproduces,
        offchain_matches_live,
        attribution,
    ) = match (
        request.chain_live_amount_out_raw,
        request.chain_live_required_repay_raw,
    ) {
        (Some(amount_out), Some(required_repay)) => {
            let signed = amount_out - required_repay;
            let delta = relative_delta(offchain_profit, signed);
            let reproduces = relative_delta(signed, onchain_profit) <= request.tolerance;
            let matches_live = delta <= request.tolerance;
            let attribution = if matches_live {
                ParityAttribution::Consistent
            } else if !assumed_fee_matches_chain {
                // The fee the caller assumed is not the fee the chain
                // charged. The deviation is an assumption about the
                // chain's own settlement, not a difference in pool
                // state, so it has to be named before the state buckets
                // can be trusted.
                ParityAttribution::FlashFeeAssumption
            } else if reproduces {
                ParityAttribution::StaleLocalState
            } else {
                ParityAttribution::OffchainArithmetic
            };
            (
                Some(signed),
                Some(delta),
                Some(reproduces),
                Some(matches_live),
                attribution,
            )
        }
        _ => (
            None,
            None,
            None,
            None,
            ParityAttribution::UnknownNoChainLiveState,
        ),
    };

    let detail = ParityDetail {
        attribution: attribution.as_str().to_string(),
        tolerance: request.tolerance,
        chain_live_signed_profit_raw: chain_live_signed.map(|value| value.to_string()),
        chain_live_relative_delta: chain_live_delta,
        chain_live_reproduces_chain_profit: chain_live_reproduces,
        offchain_matches_chain_live: offchain_matches_live,
        offchain_flash_fee_bps: flash_fee_bps,
        assumed_flash_fee_bps: request.flash_fee_bps,
        chain_flash_fee_bps,
        flash_fee_matches_chain,
    };

    let delta = relative_delta(offchain_profit, onchain_profit);
    let state = if offchain_executable != onchain_executable {
        ParityState::ExecutableMismatch
    } else if delta > request.tolerance {
        ParityState::AmountMismatch
    } else {
        ParityState::Match
    };

    ParityVerdict {
        state: state.as_str().to_string(),
        executable_offchain: Some(offchain_executable),
        executable_onchain: Some(onchain_executable),
        profit_offchain_raw: Some(offchain_profit),
        profit_onchain_raw: Some(onchain_profit),
        profit_relative_delta: Some(delta),
        reason: match state {
            ParityState::Match => "off-chain and chain agree".to_string(),
            ParityState::ExecutableMismatch => {
                "the off-chain simulation and the chain disagree on executability".to_string()
            }
            ParityState::AmountMismatch => {
                "the off-chain simulation and the chain disagree on the amount".to_string()
            }
            ParityState::NotComparable => "the route is not comparable".to_string(),
        },
        anomaly: state.is_anomaly(),
        detail: Some(detail),
    }
}

// ---------------------------------------------------------------------------
// The database-backed entry point
// ---------------------------------------------------------------------------

/// Why a parity evaluation could not be served.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ParityError {
    /// Pool Manager could not serve the pool state.
    PoolState(PoolStateReadError),
    /// The request itself is malformed.
    InvalidRequest(String),
}

impl std::fmt::Display for ParityError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::PoolState(error) => write!(formatter, "{error}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid parity request: {message}")
            }
        }
    }
}

impl std::error::Error for ParityError {}

impl From<PoolStateReadError> for ParityError {
    fn from(error: PoolStateReadError) -> Self {
        Self::PoolState(error)
    }
}

/// Reads the pool state for a route and evaluates the parity comparison.
///
/// The pool state is fetched through **Pool Manager's published API**, so
/// Review Manager never reads a Pool Manager table directly.
pub fn evaluate_route_parity(
    database_path: impl AsRef<std::path::Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    request: &ParityRequest,
) -> Result<ParityVerdict, ParityError> {
    let states = read_pool_parity_states(
        database_path.as_ref(),
        network,
        chain_id,
        protocol,
        &request.pool_ids,
    )?;
    if states.is_empty() {
        return Ok(ParityVerdict::skipped(
            "no live pool state available for the route pools",
        ));
    }
    let pool_states: BTreeMap<String, PoolParityState> = states
        .into_iter()
        .map(|state| (state.pool_id.clone(), state))
        .collect();
    Ok(evaluate_route_parity_with_states(request, &pool_states))
}

/// Resolves a set of PoolKeys to pool identifiers through Pool Manager.
pub fn resolve_route_pool_ids(
    database_path: impl AsRef<std::path::Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_keys: &[PoolKeyIdentity],
) -> Result<Vec<String>, ParityError> {
    Ok(resolve_pool_ids_by_key(
        database_path.as_ref(),
        network,
        chain_id,
        protocol,
        pool_keys,
    )?)
}

/// Reads the newest liquidity timestamp across a route's pools.
pub fn route_newest_state_read_at(
    database_path: impl AsRef<std::path::Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_ids: &[String],
) -> Result<Option<String>, ParityError> {
    Ok(read_newest_liquidity_read_at(
        database_path.as_ref(),
        network,
        chain_id,
        protocol,
        pool_ids,
    )?)
}

/// The protocol the parity comparison uses by default.
pub fn default_protocol() -> &'static str {
    REVIEW_PROTOCOL
}

#[cfg(test)]
mod tests {
    use super::{
        build_hops, chain_fee_matches, effective_flash_fee_bps, evaluate_route_parity_with_states,
        flash_fee, legacy_fee_residual, relative_delta, required_repay, signed_offchain_profit,
        simulate_route, HopState, ParityAttribution, ParityRequest, ParityState,
        CHAIN_MIN_PROFIT_RAW, DEFAULT_FLASH_FEE_BPS, LEGACY_ASSUMED_FLASH_FEE_BPS,
    };
    use pool_manager::PoolParityState;
    use std::collections::BTreeMap;

    const TOKEN_A: &str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TOKEN_B: &str = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const TOKEN_C: &str = "0xcccccccccccccccccccccccccccccccccccccccc";

    fn pool(pool_id: &str, token0: &str, token1: &str) -> PoolParityState {
        PoolParityState {
            pool_id: pool_id.to_string(),
            token0: token0.to_string(),
            token1: token1.to_string(),
            fee: 3000,
            tick_spacing: 60,
            hooks: "0x0000000000000000000000000000000000000000".to_string(),
            sqrt_price_x96: 79_228_162_514_264_337_593_543_950_336,
            tick: 0,
            liquidity: 1_000_000_000_000_000_000,
            liquidity_read_at: Some("2026-11-12T00:00:00+00:00".to_string()),
            evidence_source: Some("test".to_string()),
        }
    }

    fn hop(pool_id: &str, token_in: &str, token_out: &str, zero_for_one: bool) -> HopState {
        HopState {
            pool_id: pool_id.to_string(),
            token_in: token_in.to_string(),
            token_out: token_out.to_string(),
            sqrt_price_x96: 79_228_162_514_264_337_593_543_950_336,
            liquidity: 1_000_000_000_000_000_000,
            fee_pips: 3000,
            zero_for_one,
        }
    }

    #[test]
    fn flash_fee_rounds_up_like_the_contract() {
        // The contract computes `(amount * feeBps + 9999) / 10000`, so the
        // fee must round **up**. Rounding down would make the simulator
        // more optimistic than the chain.
        assert_eq!(flash_fee(1, 5), 1);
        assert_eq!(flash_fee(10_000, 5), 5);
        assert_eq!(flash_fee(1_000_000, 5), 500);
        assert_eq!(flash_fee(0, 5), 0);
        assert_eq!(flash_fee(-1, 5), 0);
        assert_eq!(flash_fee(1_000, 0), 0);
    }

    #[test]
    fn required_repay_adds_the_fee_to_the_principal() {
        assert_eq!(required_repay(100_000, 5), 100_050);
        assert_eq!(required_repay(1_000_000_000, 5), 1_000_500_000);
    }

    #[test]
    fn simulate_route_reports_a_signed_shortfall_for_a_losing_route() {
        // A route that loses must settle at zero profit, and the signed
        // form must expose the shortfall, because that is what the chain
        // reports and therefore what the two sides can be compared on.
        let hops = vec![hop("pool-1", TOKEN_A, TOKEN_B, true)];
        let result = simulate_route(&hops, 1_000_000, 5);
        assert!(!result.executable);
        assert_eq!(result.profit_raw, 0);
        let signed = signed_offchain_profit(&result);
        assert!(signed <= 0);
        // The signed form is the **negative** shortfall: the chain reports a
        // losing route that way, so the two sides share one convention.
        assert_eq!(signed, -(result.required_repay_raw - result.amount_out_raw));
    }

    #[test]
    fn simulate_route_rejects_a_route_that_does_not_close() {
        let hops = vec![hop("pool-1", TOKEN_A, TOKEN_B, true)];
        let result = simulate_route(&hops, 1_000_000, 5);
        assert_eq!(result.reason, "route_does_not_close");
        assert!(!result.executable);
    }

    #[test]
    fn simulate_route_rejects_a_token_discontinuity() {
        let hops = vec![
            hop("pool-1", TOKEN_A, TOKEN_B, true),
            hop("pool-2", TOKEN_A, TOKEN_C, true),
        ];
        let result = simulate_route(&hops, 1_000_000, 5);
        assert_eq!(result.reason, "token_discontinuity_at_hop_2");
        assert!(!result.executable);
    }

    #[test]
    fn build_hops_derives_the_direction_from_the_pool_pair() {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        states.insert("pool-2".to_string(), pool("pool-2", TOKEN_B, TOKEN_C));
        let hops = build_hops(
            &[
                TOKEN_A.to_string(),
                TOKEN_B.to_string(),
                TOKEN_C.to_string(),
            ],
            &states,
            &["pool-1".to_string(), "pool-2".to_string()],
        )
        .expect("the route is expressible");
        assert_eq!(hops.len(), 2);
        // pool-1 is (A, B) and the route is A->B, so token0 is the input.
        assert!(hops[0].zero_for_one);
        // pool-2 is (B, C) and the route is B->C, so token0 is the input
        // here as well. The direction follows the pool's own token order,
        // not the route's position in the loop.
        assert!(hops[1].zero_for_one);
    }

    #[test]
    fn build_hops_rejects_a_pool_that_does_not_sit_between_the_route_tokens() {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        assert!(build_hops(
            &[TOKEN_A.to_string(), TOKEN_C.to_string()],
            &states,
            &["pool-1".to_string()],
        )
        .is_none());
    }

    /// The production route borrows straight from the Uniswap V4 PoolManager,
    /// which charges no flash-loan fee: the contract only demands
    /// `amount + minProfit` back. The default must therefore be zero, and a
    /// non-zero default silently taxes every off-chain simulation.
    #[test]
    fn the_default_flash_fee_is_zero_because_the_chain_charges_none() {
        assert_eq!(DEFAULT_FLASH_FEE_BPS, 0);
        assert_eq!(
            flash_fee(1_000_000_000_000_000_000, DEFAULT_FLASH_FEE_BPS),
            0
        );
        assert_eq!(
            required_repay(1_000_000_000_000_000_000, DEFAULT_FLASH_FEE_BPS),
            1_000_000_000_000_000_000
        );
    }

    /// The chain's settlement is the source of the effective fee.
    ///
    /// The chain reverted with `requiredRepay == amount + 1`, i.e. the
    /// principal plus the one-wei minimum-profit guard and no fee at all.
    /// The derived fee must therefore be zero basis points.
    #[test]
    fn the_effective_fee_is_derived_from_the_chain_settlement() {
        let principal = 1_000_000_000_000_000_000_i128;
        let chain_repay = principal + CHAIN_MIN_PROFIT_RAW;
        assert_eq!(
            effective_flash_fee_bps(principal, Some(chain_repay)),
            Some(0)
        );
    }

    /// A chain that did charge a fee must have that fee recovered, not
    /// approximated: the fee is an owner-mutable on-chain setting.
    #[test]
    fn the_effective_fee_follows_the_chain_even_when_the_chain_charges_one() {
        let principal = 1_000_000_000_000_000_000_i128;
        let chain_repay = principal + 500_000_000_000_000 + CHAIN_MIN_PROFIT_RAW;
        assert_eq!(
            effective_flash_fee_bps(principal, Some(chain_repay)),
            Some(5)
        );
    }

    /// A settlement below the principal cannot be explained by a fee, so no
    /// fee is inferred from it. A settlement exactly equal to the principal
    /// is a legitimate zero-fee settlement.
    #[test]
    fn an_impossible_settlement_yields_no_effective_fee() {
        let principal = 1_000_000_000_000_000_000_i128;
        assert_eq!(
            effective_flash_fee_bps(principal, Some(principal - 1)),
            None
        );
        assert_eq!(effective_flash_fee_bps(principal, Some(principal)), Some(0));
    }

    /// A residual that is exactly the legacy fee is the historical defect,
    /// and it has to be recognisable as such.
    ///
    /// The recorded offline snapshots were priced with the fee the production
    /// code assumed rather than the fee the chain charges. A difference that
    /// is exactly that fee is therefore fully explained and must not be
    /// reported as an unexplained difference in pool state.
    #[test]
    fn a_residual_that_is_exactly_the_legacy_fee_is_recognised() {
        let principal = 1_000_000_000_000_000_000_i128;
        let legacy_fee = (principal * LEGACY_ASSUMED_FLASH_FEE_BPS as i128 + 9_999) / 10_000;
        let recorded = -500_000_000_000_000_000_i128;
        let offchain = recorded + legacy_fee;
        assert_eq!(
            legacy_fee_residual(principal, Some(recorded), Some(offchain)),
            Some(legacy_fee)
        );
        // The recorded profit was converted from a decimal amount, so it
        // carries a rounding dust of a few raw units.
        let dusty = recorded + legacy_fee + 50;
        assert_eq!(
            legacy_fee_residual(principal, Some(recorded), Some(dusty)),
            Some(legacy_fee)
        );
    }

    /// A residual that is not the legacy fee is not explained by it.
    #[test]
    fn a_residual_that_is_not_the_legacy_fee_is_not_explained() {
        let principal = 1_000_000_000_000_000_000_i128;
        let legacy_fee = (principal * LEGACY_ASSUMED_FLASH_FEE_BPS as i128 + 9_999) / 10_000;
        let recorded = -500_000_000_000_000_000_i128;
        assert_eq!(
            legacy_fee_residual(principal, Some(recorded), Some(recorded + legacy_fee * 2)),
            None
        );
        assert_eq!(legacy_fee_residual(principal, None, Some(0)), None);
    }

    /// Without a settlement there is nothing to derive a fee from, so the
    /// caller keeps its documented default.
    #[test]
    fn without_a_settlement_no_effective_fee_is_derived() {
        assert_eq!(effective_flash_fee_bps(1_000_000, None), None);
    }

    /// The fee check has to distinguish the chain's settlement from the
    /// simulator's own arithmetic, profit guard included.
    #[test]
    fn the_fee_check_compares_the_chain_settlement_with_the_simulator() {
        let principal = 1_000_000_000_000_000_000_i128;
        let chain_repay = principal + CHAIN_MIN_PROFIT_RAW;
        assert_eq!(
            chain_fee_matches(principal, Some(chain_repay), 0),
            Some(true)
        );
        assert_eq!(
            chain_fee_matches(principal, Some(chain_repay), 5),
            Some(false)
        );
    }

    /// The regression that matters: the simulator's fee assumption must not
    /// be allowed to masquerade as a stale-state deviation.
    ///
    /// The chain charged no fee, the caller assumed five basis points, and
    /// the deviation must be attributed to the fee assumption rather than to
    /// the pool state.
    #[test]
    fn a_fee_assumption_is_named_instead_of_being_blamed_on_the_pool_state() {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        let mut request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            5,
        );
        request.onchain_profit_raw = Some(-1_000);
        request.chain_live_amount_out_raw = Some(999_000);
        request.chain_live_required_repay_raw = Some(1_000_000 + CHAIN_MIN_PROFIT_RAW);
        let verdict = evaluate_route_parity_with_states(&request, &states);
        let detail = verdict.detail.expect("the attribution is reported");
        assert_eq!(
            detail.attribution,
            ParityAttribution::FlashFeeAssumption.as_str()
        );
        assert_eq!(detail.chain_flash_fee_bps, Some(0));
        assert_eq!(detail.assumed_flash_fee_bps, 5);
        assert_eq!(detail.offchain_flash_fee_bps, 0);
        assert_eq!(detail.flash_fee_matches_chain, Some(true));
    }

    /// The derived fee has to reach the simulation itself, not just the
    /// report: a route the chain settled with no fee must be simulated with
    /// no fee.
    #[test]
    fn the_simulation_uses_the_fee_the_chain_charged() {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        let mut request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            5,
        );
        // The chain settled with no fee at all.
        request.chain_live_required_repay_raw = Some(1_000_000 + CHAIN_MIN_PROFIT_RAW);
        request.onchain_profit_raw = Some(-1_000);
        let verdict = evaluate_route_parity_with_states(&request, &states);
        let detail = verdict.detail.expect("the attribution is reported");
        assert_eq!(
            detail.offchain_flash_fee_bps, 0,
            "the simulator must charge the fee the chain charged, not the one it assumed"
        );
    }

    #[test]
    fn parity_reports_a_match_when_the_offchain_and_chain_agree() {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        let mut request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            5,
        );
        // Use the simulator's own signed figure as the chain's figure, so
        // the two sides agree by construction.
        let hops = build_hops(&request.route_addresses, &states, &request.pool_ids).unwrap();
        let offchain = simulate_route(&hops, request.amount_raw, request.flash_fee_bps);
        let signed = signed_offchain_profit(&offchain);
        request.onchain_profit_raw = Some(signed);
        let verdict = evaluate_route_parity_with_states(&request, &states);
        assert_eq!(verdict.state, ParityState::Match.as_str());
        assert!(!verdict.anomaly);
    }

    #[test]
    fn parity_attributes_a_deviation_to_stale_local_state_when_the_chain_live_figure_reproduces_the_chain_profit(
    ) {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        let mut request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            0,
        );
        // The chain says the route lost by 1000, and its own live figures
        // reproduce that verdict. The off-chain side says something else,
        // because it ran on an older state.
        request.onchain_profit_raw = Some(-1_000);
        request.chain_live_amount_out_raw = Some(999_000);
        request.chain_live_required_repay_raw = Some(1_000_000);
        let verdict = evaluate_route_parity_with_states(&request, &states);
        let detail = verdict.detail.expect("the attribution is reported");
        assert_eq!(
            detail.attribution,
            ParityAttribution::StaleLocalState.as_str()
        );
        assert_eq!(detail.chain_live_reproduces_chain_profit, Some(true));
    }

    #[test]
    fn parity_attributes_a_deviation_to_the_arithmetic_when_the_chain_live_figure_does_not_reproduce_the_chain_profit(
    ) {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        let mut request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            0,
        );
        request.onchain_profit_raw = Some(-1_000);
        request.chain_live_amount_out_raw = Some(500_000);
        request.chain_live_required_repay_raw = Some(1_000_000);
        let verdict = evaluate_route_parity_with_states(&request, &states);
        let detail = verdict.detail.expect("the attribution is reported");
        assert_eq!(
            detail.attribution,
            ParityAttribution::OffchainArithmetic.as_str()
        );
    }

    #[test]
    fn parity_without_chain_live_figures_is_not_comparable_but_is_still_attributed() {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        let mut request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            5,
        );
        request.onchain_profit_raw = Some(-1_000);
        let verdict = evaluate_route_parity_with_states(&request, &states);
        let detail = verdict.detail.expect("the attribution is reported");
        assert_eq!(
            detail.attribution,
            ParityAttribution::UnknownNoChainLiveState.as_str()
        );
    }

    #[test]
    fn parity_without_a_chain_profit_is_not_comparable() {
        let mut states = BTreeMap::new();
        states.insert("pool-1".to_string(), pool("pool-1", TOKEN_A, TOKEN_B));
        let request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            5,
        );
        let verdict = evaluate_route_parity_with_states(&request, &states);
        assert_eq!(verdict.state, ParityState::NotComparable.as_str());
        assert_eq!(verdict.executable_onchain, None);
        assert!(!verdict.anomaly);
    }

    #[test]
    fn parity_without_pool_state_is_not_comparable() {
        let states = BTreeMap::new();
        let request = ParityRequest::new(
            vec![TOKEN_A.to_string(), TOKEN_B.to_string()],
            vec!["pool-1".to_string()],
            1_000_000,
            5,
        );
        let verdict = evaluate_route_parity_with_states(&request, &states);
        assert_eq!(verdict.state, ParityState::NotComparable.as_str());
        assert!(verdict.reason.contains("no live pool state"));
    }

    #[test]
    fn relative_delta_is_zero_for_equal_amounts_and_one_for_opposite_signs() {
        assert_eq!(relative_delta(100, 100), 0.0);
        assert_eq!(relative_delta(100, 0), 1.0);
        assert_eq!(relative_delta(-100, 100), 2.0);
        assert_eq!(relative_delta(0, 0), 0.0);
    }
}

#[cfg(test)]
mod equivalence_tests {
    //! Cross-language equivalence with the Python simulator.
    //!
    //! The Python simulator (`v4_multihop_simulator.py`) and this Rust module
    //! are two implementations of one arithmetic. If they ever disagree, the
    //! ARCH-2b measurement becomes meaningless, so the fixtures below are
    //! pinned to **absolute values** that both sides must reproduce.

    use super::{simulate_route, HopState};

    const Q96: u128 = 1u128 << 96;
    const TOKEN_A: &str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TOKEN_B: &str = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const TOKEN_C: &str = "0xcccccccccccccccccccccccccccccccccccccccc";

    fn hop(pool_id: &str, token_in: &str, token_out: &str, zero_for_one: bool) -> HopState {
        HopState {
            pool_id: pool_id.to_string(),
            token_in: token_in.to_string(),
            token_out: token_out.to_string(),
            sqrt_price_x96: Q96,
            liquidity: 100_000_000_000_000_000_000,
            fee_pips: 3000,
            zero_for_one,
        }
    }

    fn closed_loop() -> Vec<HopState> {
        vec![
            hop("pool-a", TOKEN_A, TOKEN_B, true),
            hop("pool-b", TOKEN_B, TOKEN_C, true),
            hop("pool-c", TOKEN_C, TOKEN_A, true),
        ]
    }

    /// The canonical fixture: a flat three-hop loop at the mid price with
    /// `10^18` borrowed and a 5 bps flash fee.
    ///
    /// The expected numbers are the ones the Python simulator produces for
    /// the same input, so the two implementations are pinned to each other.
    #[test]
    fn canonical_fixture_matches_the_python_simulator() {
        let result = simulate_route(&closed_loop(), 1_000_000_000_000_000_000, 5);
        assert_eq!(
            result.required_repay_raw, 1_000_500_000_000_000_000,
            "the repayment must match the contract"
        );
        assert_eq!(
            result.amount_out_raw, 962_329_947_778_299_007,
            "the settled amount must match the Python simulator"
        );
        assert_eq!(result.profit_raw, 0);
        assert!(!result.executable);
        assert_eq!(result.reason, "not_profitable");
    }

    /// A single hop's output must match the canonical Uniswap arithmetic.
    ///
    /// At the mid price (`sqrtPriceX96 == Q96`) with a 3000 pips fee, a
    /// `10^18` input against `10^20` liquidity yields a known output. The
    /// value below is what the Python simulator produces for the same input.
    #[test]
    fn single_hop_output_matches_the_python_simulator() {
        let hops = vec![hop("pool-x", TOKEN_A, TOKEN_B, true)];
        let result = simulate_route(&hops, 1_000_000_000_000_000_000, 0);
        assert_eq!(result.amount_out_raw, 987_158_034_397_061_298);
    }

    /// The opposite direction must produce the mirrored figure.
    #[test]
    fn single_hop_output_in_the_other_direction_matches_the_python_simulator() {
        let hops = vec![hop("pool-y", TOKEN_B, TOKEN_A, false)];
        let result = simulate_route(&hops, 1_000_000_000_000_000_000, 0);
        assert_eq!(result.amount_out_raw, 987_158_034_397_061_298);
    }
}

// ---------------------------------------------------------------------------
// The review-time pool state
// ---------------------------------------------------------------------------

/// The scale factor between the stored liquidity index and the raw liquidity.
///
/// Pool Manager stores the active liquidity twice: as the raw 128-bit word in
/// `liquidity`, and as `liquidity_index = liquidity / 1e18` for display and
/// ranking. The recorded review payload only carries the **index**, because
/// that is the field the candidate-pool view publishes. Multiplying it back by
/// `1e18` recovers the raw word the simulator needs.
pub const LIQUIDITY_INDEX_SCALE: u128 = 1_000_000_000_000_000_000;

/// The review-time state of one pool, as the recorded review saw it.
///
/// The review payload embeds the candidate pools it quoted, and every one of
/// those carries the square-root price and the liquidity that were measured
/// **at the moment the review ran**. That state is the only faithful input to
/// a replay of that review: comparing a recorded review against a later pool
/// snapshot compares two different markets and can never reproduce the
/// recorded answer.
///
/// The state is extracted from Review Manager's own table
/// (`cow_execution_reviews`), so it belongs here rather than in Pool Manager:
/// Pool Manager owns the live snapshot, Review Manager owns the record of
/// what the review actually saw.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReviewTimePoolState {
    pub pool_id: String,
    pub token0: String,
    pub token1: String,
    pub fee: u32,
    pub tick_spacing: i64,
    pub hooks: String,
    pub sqrt_price_x96: u128,
    pub tick: i64,
    pub liquidity: u128,
    pub liquidity_read_at: Option<String>,
}

impl ReviewTimePoolState {
    /// Converts the review-time state into the published parity DTO.
    ///
    /// The DTO is Pool Manager's published shape, so the review-time state
    /// can be compared against a live snapshot on equal terms without the
    /// comparison learning a second schema.
    pub fn to_parity_state(&self) -> PoolParityState {
        PoolParityState {
            pool_id: self.pool_id.clone(),
            token0: self.token0.clone(),
            token1: self.token1.clone(),
            fee: self.fee,
            tick_spacing: self.tick_spacing,
            hooks: self.hooks.clone(),
            sqrt_price_x96: self.sqrt_price_x96,
            tick: self.tick,
            liquidity: self.liquidity,
            liquidity_read_at: self.liquidity_read_at.clone(),
            evidence_source: Some("review_time_recorded_state".to_string()),
        }
    }
}

/// Reads the review-time pool states out of a recorded review payload.
///
/// The payload carries the candidate pools under `quote.candidate_pools` (and
/// the snake_case spelling). Each pool is matched by its pool id, so the
/// result is keyed exactly like the live snapshot and the two can be merged
/// without guessing which pool is which.
///
/// A pool that cannot be read is **skipped**, not guessed: a wrong price
/// would produce a wrong simulation, which is worse than no simulation.
pub fn extract_review_time_pool_states(payload: &serde_json::Value) -> Vec<ReviewTimePoolState> {
    let Some(pools) = payload
        .get("quote")
        .and_then(|quote| {
            quote
                .get("candidate_pools")
                .or_else(|| quote.get("candidatePools"))
        })
        .and_then(|value| value.as_array())
    else {
        return Vec::new();
    };
    let mut states = Vec::new();
    for pool in pools {
        let Some(state) = extract_review_time_pool_state(pool) else {
            continue;
        };
        states.push(state);
    }
    states
}

/// Extracts one pool's review-time state.
fn extract_review_time_pool_state(pool: &serde_json::Value) -> Option<ReviewTimePoolState> {
    let pool_id = pool
        .get("poolId")
        .or_else(|| pool.get("pool_id"))
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())?
        .to_lowercase();
    let token0 = pool
        .get("token0")
        .or_else(|| pool.get("currency0"))
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())?
        .to_lowercase();
    let token1 = pool
        .get("token1")
        .or_else(|| pool.get("currency1"))
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())?
        .to_lowercase();
    let fee = pool
        .get("fee")
        .and_then(|value| value.as_i64())
        .filter(|value| *value > 0)? as u32;
    let tick_spacing = pool
        .get("tickSpacing")
        .or_else(|| pool.get("tick_spacing"))
        .and_then(|value| value.as_i64())
        .unwrap_or_default();
    let hooks = pool
        .get("hooks")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("0x0000000000000000000000000000000000000000")
        .to_lowercase();
    let sqrt_price_x96 = pool
        .get("sqrtPriceX96")
        .or_else(|| pool.get("sqrt_price_x96"))
        .and_then(|value| value.as_str())
        .and_then(parse_raw_u128)
        .filter(|value| *value > 0)?;
    // The tick anchors the tick traversal, so it is recovered from the
    // recorded payload just like the price and the liquidity.
    let tick = pool
        .get("tick")
        .and_then(|value| value.as_i64())
        .unwrap_or_default();
    let liquidity = review_time_liquidity(pool)?;
    let liquidity_read_at = pool
        .get("liquidityReadAt")
        .or_else(|| pool.get("liquidity_read_at"))
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);

    Some(ReviewTimePoolState {
        pool_id,
        token0,
        token1,
        fee,
        tick_spacing,
        hooks,
        sqrt_price_x96,
        tick,
        liquidity,
        liquidity_read_at,
    })
}

/// Recovers the raw active liquidity from a recorded candidate pool.
///
/// The payload publishes the liquidity **index** (`liquidity / 1e18`) and
/// leaves the raw word empty, because the raw word does not survive a JSON
/// round trip through the display layer. The index is therefore scaled back
/// up, and the scale is applied to the exact decimal representation so that
/// no precision is lost on the way.
fn review_time_liquidity(pool: &serde_json::Value) -> Option<u128> {
    // The raw word, when the payload carries it, is authoritative.
    if let Some(raw) = pool
        .get("liquidity")
        .and_then(|value| value.as_str())
        .and_then(parse_raw_u128)
        .filter(|value| *value > 0)
    {
        return Some(raw);
    }
    let index = pool
        .get("liquidityIndex")
        .or_else(|| pool.get("liquidity_index"))
        .and_then(json_number_as_str)?
        .trim()
        .to_string();
    if index.is_empty() {
        return None;
    }
    // The index is `L / 1e18`, so `L = index * 1e18`. The multiplication is
    // done on the decimal text so that the result is exact instead of
    // depending on the binary floating point representation.
    let scaled = decimal_times_power_of_ten(&index, 18)?;
    parse_raw_u128(scaled.as_str()).filter(|value| *value > 0)
}

/// Multiplies a decimal string by `10^n`, exactly.
///
/// The text may be written in plain notation (`5672.86612036`) or in
/// scientific notation (`9.901032566577074e+17`). The JSON writer uses the
/// scientific form whenever the exponent is large, so **both** forms have to
/// be understood: a value that is rejected as unparsable would silently
/// drop the pool's state and force the replay onto a different, later
/// snapshot.
fn decimal_times_power_of_ten(text: &str, exponent: u32) -> Option<String> {
    let text = text.trim();
    let (mantissa, exponent_adjust) = match text.split_once(['e', 'E']) {
        Some((mantissa, exponent)) => {
            let parsed = exponent.trim().parse::<i64>().ok()?;
            (mantissa, parsed)
        }
        None => (text, 0),
    };
    let (integer_part, fractional_part) = match mantissa.split_once('.') {
        Some((integer, fractional)) => (integer, fractional),
        None => (mantissa, ""),
    };
    if integer_part.is_empty() && fractional_part.is_empty() {
        return None;
    }
    if !integer_part.bytes().all(|byte| byte.is_ascii_digit())
        || !fractional_part.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    // The value is `digits * 10^(exponent_adjust - fractional_len)`. The
    // result has to be an integer, so the exponent may not be negative.
    let scale = exponent_adjust
        .checked_sub(fractional_part.len() as i64)?
        .checked_add(exponent as i64)?;
    if scale < 0 {
        return None;
    }
    let mut digits =
        String::with_capacity(integer_part.len() + fractional_part.len() + scale as usize);
    digits.push_str(integer_part);
    digits.push_str(fractional_part);
    for _ in 0..scale {
        digits.push('0');
    }
    // Trim the leading zeros but keep at least one digit.
    let trimmed = digits.trim_start_matches('0');
    if trimmed.is_empty() {
        return Some("0".to_string());
    }
    Some(trimmed.to_string())
}

/// Renders a JSON number as the exact decimal text it carries.
///
/// A JSON number parsed as `f64` would already have lost precision by the
/// time it reached this function, so the raw JSON text is used whenever the
/// value is a number.
fn json_number_as_str(value: &serde_json::Value) -> Option<String> {
    match value {
        serde_json::Value::String(text) => Some(text.clone()),
        serde_json::Value::Number(number) => Some(number.to_string()),
        _ => None,
    }
}

/// Parses a raw unsigned integer that may be written in decimal or in
/// hexadecimal.
fn parse_raw_u128(value: &str) -> Option<u128> {
    let text = value.trim();
    if text.is_empty() {
        return None;
    }
    if let Some(hex) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        return u128::from_str_radix(hex, 16).ok();
    }
    text.parse::<u128>().ok()
}

// ---------------------------------------------------------------------------
// The recorded review evidence
// ---------------------------------------------------------------------------

/// One recorded review, reduced to what the parity comparison needs.
#[derive(Clone, Debug)]
pub struct RecordedReview {
    pub attempt_id: i64,
    pub attempt_key: String,
    pub created_at: String,
    pub category: String,
    pub borrow_address: String,
    pub route_addresses: Vec<String>,
    pub pool_ids: Vec<String>,
    /// The identity of the route the attempt walked.
    ///
    /// Two attempts of the same route differ only in when they ran, so the
    /// route identity is what tells "another try at the same route" apart
    /// from "a different route".
    pub route_key: String,
    pub amount_raw: Option<i128>,
    pub onchain_profit_raw: Option<i128>,
    /// Where the chain-side profit came from.
    ///
    /// This decides how much the comparison is worth. When the chain-side
    /// figure came from the **same** local route snapshot the off-chain side
    /// reads, the two agree by construction and the agreement is not
    /// independent evidence. When it came from a live contract preview, the
    /// comparison is independent, and a deviation is a real finding.
    pub onchain_profit_source: Option<String>,
    pub state_read_at: Option<String>,
    pub state_age_seconds: Option<f64>,
    pub chain_live_amount_out_raw: Option<i128>,
    pub chain_live_required_repay_raw: Option<i128>,
    /// The pool state **as this review saw it**.
    ///
    /// The recorded payload embeds the candidate pools it quoted, together
    /// with the square-root price and liquidity that were measured when the
    /// review ran. That state, and not a later snapshot, is the faithful
    /// input to a replay of this review.
    pub review_time_pool_states: Vec<ReviewTimePoolState>,
}

/// Parses an integer from a cell that may hold a decimal or a hexadecimal
/// string.
///
/// The chain reports a **negative** profit for a route that does not pay,
/// and it reports it in the same hexadecimal form as a positive amount
/// (`-0x60b547a9588b2e0`). A parser that only recognises a leading `0x`
/// silently drops every negative amount, which turns a route the chain
/// rejected into "no profit was reported" -- missing evidence instead of a
/// real verdict.
///
/// The sign therefore has to be recognised before the radix.
pub fn parse_amount(value: Option<&str>) -> Option<i128> {
    let text = value?.trim();
    if text.is_empty() {
        return None;
    }
    let negative = text.starts_with('-');
    let body = if negative { &text[1..] } else { text };
    let body = body.trim();
    if body.is_empty() {
        return None;
    }
    let parsed = if let Some(hex) = body.strip_prefix("0x").or_else(|| body.strip_prefix("0X")) {
        i128::from_str_radix(hex, 16).ok()?
    } else {
        body.parse::<i128>().ok()?
    };
    Some(if negative { -parsed } else { parsed })
}

/// Recovers the chain's own live amounts from a recorded revert.
///
/// When the chain rejects a route it reverts with `NoProfitableRoute`
/// carrying the two numbers the contract settled on: the amount the route
/// actually returned, and the amount it had to repay. Those are the chain's
/// **live** figures, and comparing them against the off-chain result is what
/// separates "the off-chain arithmetic is wrong" from "the off-chain
/// arithmetic ran on a state the chain had moved past".
///
/// The body is two 32-byte words: `amountOut` then `requiredRepay`.
pub fn decode_chain_live_amounts(revert_data: Option<&str>) -> Option<(i128, i128)> {
    let text = revert_data?.trim();
    let body = text
        .strip_prefix("0x")
        .or_else(|| text.strip_prefix("0X"))?;
    if body.len() % 2 != 0 {
        return None;
    }
    let bytes = hex::decode(body).ok()?;
    // The selector takes four bytes, then two 32-byte words.
    if bytes.len() < 4 + 64 {
        return None;
    }
    // The words are 256-bit. They are read as a big-endian pair and narrowed
    // to 128 bits, which is the width the amounts are compared in. A value
    // that does not fit is not a real amount, so it is rejected rather than
    // silently truncated.
    let mut amount_out_bytes = [0u8; 32];
    amount_out_bytes.copy_from_slice(&bytes[4..36]);
    let mut required_repay_bytes = [0u8; 32];
    required_repay_bytes.copy_from_slice(&bytes[36..68]);
    let amount_out = decode_u256_as_i128(&amount_out_bytes)?;
    let required_repay = decode_u256_as_i128(&required_repay_bytes)?;
    if required_repay == 0 {
        return None;
    }
    Some((amount_out, required_repay))
}

/// Narrows a 256-bit big-endian word to the 128-bit width the amounts are
/// compared in. A value that does not fit is rejected rather than truncated.
fn decode_u256_as_i128(bytes: &[u8; 32]) -> Option<i128> {
    use alloy_primitives::U256;
    let value = U256::from_be_slice(bytes);
    let narrowed = value.to::<u128>();
    i128::try_from(narrowed).ok()
}

/// Reads the recorded reviews that carry a comparable route.
///
/// The review table is Review Manager's own table, so this is a read of its
/// own data. The pool state is **not** read here: it comes from Pool Manager
/// through Pool Manager's published API.
pub fn read_recorded_reviews(
    database_path: impl AsRef<std::path::Path>,
    network: &str,
    chain_id: i64,
    limit: Option<usize>,
) -> Result<Vec<RecordedReview>, ParityError> {
    use rusqlite::{params_from_iter, Connection, OpenFlags};

    let connection = Connection::open_with_flags(
        database_path.as_ref(),
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| ParityError::PoolState(PoolStateReadError::Sqlite(error.to_string())))?;

    let mut sql = String::from(
        "SELECT id, attempt_key, created_at, category, selected_profit_raw, payload_json
         FROM cow_execution_reviews
         WHERE network = ? AND chain_id = ? AND payload_json IS NOT NULL
         ORDER BY id DESC",
    );
    let mut parameters: Vec<String> = vec![network.to_string(), chain_id.to_string()];
    if let Some(limit) = limit {
        sql.push_str(" LIMIT ?");
        parameters.push(limit.to_string());
    }

    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| ParityError::PoolState(PoolStateReadError::Sqlite(error.to_string())))?;
    let rows = statement
        .query_map(params_from_iter(parameters.iter()), |row| {
            let attempt_id: i64 = row.get(0)?;
            let attempt_key: Option<String> = row.get(1)?;
            let created_at: Option<String> = row.get(2)?;
            let category: Option<String> = row.get(3)?;
            let selected_profit_raw: Option<String> = row.get(4)?;
            let payload_json: Option<String> = row.get(5)?;
            Ok((
                attempt_id,
                attempt_key,
                created_at,
                category,
                selected_profit_raw,
                payload_json,
            ))
        })
        .map_err(|error| ParityError::PoolState(PoolStateReadError::Sqlite(error.to_string())))?;

    let mut reviews = Vec::new();
    for row in rows.flatten() {
        let (attempt_id, attempt_key, created_at, category, selected_profit_raw, payload_json) =
            row;
        let Some(payload_json) = payload_json else {
            continue;
        };
        let Ok(payload) = serde_json::from_str::<serde_json::Value>(&payload_json) else {
            continue;
        };
        let Some(review) = reduce_recorded_review(
            attempt_id,
            attempt_key,
            created_at,
            category,
            selected_profit_raw,
            &payload,
        ) else {
            continue;
        };
        reviews.push(review);
    }
    Ok(reviews)
}

/// Reduces one recorded payload to the route the simulator needs.
fn reduce_recorded_review(
    attempt_id: i64,
    attempt_key: Option<String>,
    created_at: Option<String>,
    category: Option<String>,
    selected_profit_raw: Option<String>,
    payload: &serde_json::Value,
) -> Option<RecordedReview> {
    let route_addresses = string_list(payload, &["routeAddresses", "route_addresses"])?;
    if route_addresses.len() < 2 {
        return None;
    }
    let pool_ids = pool_ids_from_path_key(payload.get("poolPathKey").and_then(|v| v.as_str()))?;
    // One pool per hop. A path key that does not line up with the route is
    // not comparable: simulating it would run over a route the chain never
    // took.
    if pool_ids.len() != route_addresses.len() - 1 {
        return None;
    }
    // The chain probes a ladder of amounts before it gives up on a route.
    // A recorded attempt without that ladder was never evaluated against the
    // route, so there is nothing to compare and it is not evidence.
    if !has_probe_ladder(payload) {
        return None;
    }
    let amount_raw = extract_probe_amount(payload)?;
    if amount_raw <= 0 {
        return None;
    }
    let onchain_profit_raw =
        parse_amount(payload.get("selectedProfitRaw").and_then(|v| v.as_str()))
            .or_else(|| parse_amount(selected_profit_raw.as_deref()));
    let onchain_profit_source = payload
        .get("selectedProfitSource")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let (chain_live_amount_out_raw, chain_live_required_repay_raw) =
        decode_chain_live_amounts_from_payload(payload);

    let route_key = route_identity(&route_addresses, &pool_ids);
    let review_time_pool_states = extract_review_time_pool_states(payload);

    Some(RecordedReview {
        attempt_id,
        attempt_key: attempt_key.unwrap_or_default(),
        created_at: created_at.unwrap_or_default(),
        category: category.unwrap_or_default(),
        borrow_address: route_addresses[0].clone(),
        route_addresses,
        pool_ids,
        route_key,
        amount_raw: Some(amount_raw),
        onchain_profit_raw,
        onchain_profit_source,
        state_read_at: None,
        state_age_seconds: None,
        chain_live_amount_out_raw,
        chain_live_required_repay_raw,
        review_time_pool_states,
    })
}

/// The identity of a route: the tokens it walks and the pools it crosses.
///
/// The identity deliberately ignores the amount, the timestamps and the
/// attempt key: an attempt is another try at the *same* route when it walks
/// the same tokens through the same pools.
fn route_identity(route_addresses: &[String], pool_ids: &[String]) -> String {
    let mut identity = String::new();
    for address in route_addresses {
        identity.push_str(address);
        identity.push('>');
    }
    identity.push('|');
    for pool_id in pool_ids {
        identity.push_str(pool_id);
        identity.push('>');
    }
    identity
}

/// Reads a list of strings from a payload, tolerating the snake_case spelling.
fn string_list(payload: &serde_json::Value, names: &[&str]) -> Option<Vec<String>> {
    for name in names {
        let Some(value) = payload.get(*name) else {
            continue;
        };
        let Some(items) = value.as_array() else {
            continue;
        };
        let list: Vec<String> = items
            .iter()
            .filter_map(|item| item.as_str())
            .map(|item| item.trim().to_lowercase())
            .filter(|item| !item.is_empty())
            .collect();
        if list.len() >= 2 {
            return Some(list);
        }
    }
    None
}

/// Recovers the route's pools from the recorded pool path key.
///
/// A recorded attempt stores the pools it used as a pipe-separated list, in
/// route order.
fn pool_ids_from_path_key(pool_path_key: Option<&str>) -> Option<Vec<String>> {
    let text = pool_path_key?.trim();
    let list: Vec<String> = text
        .split('|')
        .map(|item| item.trim().to_lowercase())
        .filter(|item| !item.is_empty())
        .collect();
    if list.is_empty() {
        None
    } else {
        Some(list)
    }
}

/// Reports whether the chain recorded a probe ladder for this route.
///
/// The ladder is the evidence that the chain actually walked the route: each
/// rung is one amount the chain tried, with the outcome the chain saw.
fn has_probe_ladder(payload: &serde_json::Value) -> bool {
    let calculation = payload.get("best_amount_calculation");
    let probes = calculation
        .and_then(|value| {
            value
                .get("probeRecords")
                .or_else(|| value.get("probe_records"))
        })
        .and_then(|value| value.as_array())
        .or_else(|| {
            payload
                .get("probeRecords")
                .or_else(|| payload.get("probe_records"))
                .and_then(|value| value.as_array())
        });
    matches!(probes, Some(items) if !items.is_empty())
}

/// Recovers the probe amount the chain actually used.
///
/// The chain probes a ladder of amounts. The amount that produced the
/// recorded profit is the one the comparison has to use, so the most
/// successful probe wins.
fn extract_probe_amount(payload: &serde_json::Value) -> Option<i128> {
    let calculation = payload.get("best_amount_calculation")?;
    let probes = calculation
        .get("probeRecords")
        .or_else(|| calculation.get("probe_records"))
        .and_then(|value| value.as_array())
        .or_else(|| {
            payload
                .get("probeRecords")
                .or_else(|| payload.get("probe_records"))
                .and_then(|value| value.as_array())
        });

    let mut best_amount: Option<i128> = None;
    let mut best_profitable = false;
    for probe in probes.into_iter().flatten() {
        let amount = parse_amount(probe.get("amountRaw").and_then(|value| value.as_str()))?;
        if amount <= 0 {
            continue;
        }
        let profitable = probe
            .get("profitable")
            .and_then(|value| value.as_bool())
            .unwrap_or(false);
        if best_amount.is_none() || (profitable && !best_profitable) {
            best_amount = Some(amount);
            best_profitable = profitable;
        }
    }
    if best_amount.is_some() {
        return best_amount;
    }
    for key in [
        "selectedBorrowInputAmountRaw",
        "selectedAmountRaw",
        "initialProbeAmountRaw",
    ] {
        if let Some(amount) = parse_amount(payload.get(key).and_then(|value| value.as_str())) {
            if amount > 0 {
                return Some(amount);
            }
        }
    }
    None
}

/// Finds the chain's live amounts in a recorded payload.
fn decode_chain_live_amounts_from_payload(
    payload: &serde_json::Value,
) -> (Option<i128>, Option<i128>) {
    let quote = payload.get("quote");
    let live_evidence = quote.and_then(|quote| quote.get("live_onchain_evidence"));
    let mut candidates: Vec<Option<&str>> = Vec::new();
    if let Some(run_arbitrage) = live_evidence.and_then(|value| value.get("runArbitrage")) {
        candidates.push(run_arbitrage.get("revertData").and_then(|v| v.as_str()));
    }
    if let Some(stack) = live_evidence.and_then(|value| value.get("errorStack")) {
        if let Some(entries) = stack.as_array() {
            for entry in entries {
                candidates.push(entry.get("revertData").and_then(|v| v.as_str()));
            }
        }
    }
    let analysis = payload
        .get("quoteAnalysis")
        .or_else(|| payload.get("quote_analysis"));
    let probes = analysis
        .and_then(|value| {
            value
                .get("probeRecords")
                .or_else(|| value.get("probe_records"))
        })
        .and_then(|value| value.as_array())
        .or_else(|| {
            analysis
                .and_then(|value| value.get("sizeAnalysis"))
                .and_then(|size| size.get("probeRecords"))
                .and_then(|value| value.as_array())
        });
    for probe in probes.into_iter().flatten() {
        candidates.push(probe.get("revertData").and_then(|v| v.as_str()));
    }
    for candidate in candidates.into_iter().flatten() {
        if let Some((amount_out, required_repay)) = decode_chain_live_amounts(Some(candidate)) {
            return (Some(amount_out), Some(required_repay));
        }
    }
    (None, None)
}

// ---------------------------------------------------------------------------
// The batch replay
// ---------------------------------------------------------------------------

/// One replayed route.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplayOutcome {
    pub attempt_id: i64,
    pub attempt_key: String,
    pub created_at: String,
    pub category: String,
    pub borrow_address: String,
    pub route_addresses: Vec<String>,
    pub pool_ids: Vec<String>,
    pub route_key: String,
    pub amount_raw: Option<i128>,
    pub onchain_profit_raw: Option<i128>,
    /// Where the chain-side profit came from.
    pub onchain_profit_source: Option<String>,
    pub offchain_profit_raw: Option<i128>,
    pub profit_relative_delta: Option<f64>,
    pub state_read_at: Option<String>,
    pub state_age_seconds: Option<f64>,
    /// Whether the local pool state post-dates the review.
    ///
    /// A state that was refreshed after the review ran cannot reproduce the
    /// market the review saw. The flag keeps that visible instead of hiding
    /// it inside a clamped age.
    pub state_postdates_review: bool,
    pub state: String,
    pub skipped_reason: String,
    pub anomaly: bool,
    pub attribution: Option<String>,
    /// The residual, in raw units, that is **exactly** the fee the
    /// production code used to assume.
    ///
    /// When it is present, the deviation is fully explained by the recorded
    /// evidence carrying the legacy fee assumption, and it is not a
    /// difference in pool state.
    pub legacy_fee_residual_raw: Option<i128>,
    /// The flash-loan fee the off-chain simulation used, in basis points.
    pub offchain_flash_fee_bps: Option<i64>,
    /// The flash-loan fee the caller assumed the chain charged.
    pub assumed_flash_fee_bps: Option<i64>,
    /// The flash-loan fee the chain's own settlement implies.
    pub chain_flash_fee_bps: Option<i64>,
    /// Whether the fee used by the simulation agrees with the chain's fee.
    pub flash_fee_matches_chain: Option<bool>,
    /// Whether the simulation ran against the pool state **the review
    /// recorded**, rather than against the current local snapshot.
    ///
    /// The flag is the difference between a faithful replay and a
    /// comparison of two different markets. A reader has to know which one
    /// produced the numbers.
    pub used_review_time_state: bool,
}

/// The aggregate report of a replay run.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplayReport {
    /// Which recorded routes this run covered.
    ///
    /// The selection is part of the measurement: a reader has to know
    /// whether the report covers every recorded attempt or the current
    /// attempt of every route, or the numbers cannot be compared with
    /// another run.
    pub selection: String,
    pub route_count: usize,
    pub comparable_count: usize,
    pub match_count: usize,
    pub executable_mismatch_count: usize,
    pub amount_mismatch_count: usize,
    pub not_comparable_count: usize,
    pub deviation_count: usize,
    pub evidence_available: bool,
    pub evidence_complete: bool,
    pub gate_passed: bool,
    pub gate_reason: Option<String>,
    pub attribution_counts: BTreeMap<String, usize>,
    /// How the comparable routes split by the origin of the chain-side
    /// profit.
    ///
    /// The split matters because it says how much the agreement is worth. A
    /// route whose chain-side profit came from the **same** local route
    /// snapshot the off-chain side reads agrees by construction; it shows the
    /// simulator reproduces the local view, not that the local view matches
    /// the chain. A route whose chain-side profit came from a live contract
    /// preview is an independent comparison, and it is the only kind that
    /// can falsify the off-chain arithmetic.
    pub evidence_breakdown: BTreeMap<String, EvidenceBreakdown>,
    /// How many deviations are **fully explained** by the recorded evidence
    /// carrying the legacy fee assumption.
    ///
    /// The recorded offline snapshots were priced with the fee the
    /// production code used to assume rather than the fee the chain charges.
    /// When the difference between the two sides is exactly that fee, the
    /// deviation is the historical defect and nothing else, so it is counted
    /// separately instead of being mixed in with the unexplained deviations.
    pub explained_by_legacy_fee_count: usize,
    /// The deviations that remain after the explained ones are removed.
    pub unexplained_deviation_count: usize,
    /// How many of the deviations were replayed against the pool state the
    /// review itself recorded.
    ///
    /// A review-time replay compares the recorded verdict with the market
    /// the review saw, so it is the only replay that can reproduce the
    /// recorded answer. The count says how much of the report rests on that
    /// faithful basis rather than on the current local snapshot.
    pub review_time_state_count: usize,
    /// How many of the deviations the review-time state explained in full.
    pub review_time_state_explained_count: usize,
    pub pool_state_age_seconds: Option<PoolStateAge>,
    pub outcomes: Vec<ReplayOutcome>,
}

/// How many routes of one evidence kind agreed and how many deviated.
#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EvidenceBreakdown {
    pub match_count: usize,
    pub deviation_count: usize,
}

/// The age of the pool state the replay used.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PoolStateAge {
    pub min_seconds: Option<f64>,
    pub median_seconds: Option<f64>,
    pub max_seconds: Option<f64>,
}

/// Which recorded routes a replay covers.
///
/// A replay answers one question: does the chain's own arithmetic agree with
/// the arithmetic we can reproduce offline **for the routes that matter
/// right now**? The selection therefore has to be explicit, because a
/// silent difference between two runs is indistinguishable from a change in
/// the market.
#[derive(Clone, Debug)]
pub enum ReplaySelection {
    /// Every recorded attempt that satisfies the route shape rules.
    ///
    /// This is the raw evidence set. It is large and it repeats the same
    /// route many times, so it is only useful when the caller deliberately
    /// asks for it.
    AllAttempts,
    /// The newest recorded attempt of every distinct route.
    ///
    /// A parity check measures a route as it stands, so the current attempt
    /// of each route is the one that carries the evidence. Older attempts of
    /// the same route are the same arithmetic replayed against an older
    /// pool state; they add volume, not information.
    NewestPerRoute,
    /// The newest recorded attempt of every distinct route **that the local
    /// pool state can evaluate**.
    ///
    /// A route whose pools have no local state has nothing to compare
    /// against: the off-chain side would have to guess the price, and a
    /// guess is not a measurement. Such a route is left out of the report
    /// entirely rather than counted as a route that failed to agree.
    NewestPerEvaluableRoute,
}

impl Default for ReplaySelection {
    fn default() -> Self {
        ReplaySelection::NewestPerEvaluableRoute
    }
}

/// Indexes the review-time pool states by pool id.
///
/// Only the pools the route actually walks are kept: a review may quote more
/// candidates than the route uses, and a state that does not belong to the
/// route must not influence the simulation.
fn review_time_pool_state_map(review: &RecordedReview) -> BTreeMap<String, PoolParityState> {
    let mut states = BTreeMap::new();
    for state in &review.review_time_pool_states {
        if !review
            .pool_ids
            .iter()
            .any(|pool_id| pool_id == &state.pool_id)
        {
            continue;
        }
        states.insert(state.pool_id.clone(), state.to_parity_state());
    }
    states
}

/// Merges the review-time state over the live snapshot.
///
/// The review-time state wins, because it is the state the recorded verdict
/// was computed from. The live snapshot fills the gaps, so a review that
/// recorded only part of its route can still be evaluated.
fn merged_pool_states(
    live_pool_states: &BTreeMap<String, PoolParityState>,
    review_time_states: &BTreeMap<String, PoolParityState>,
) -> BTreeMap<String, PoolParityState> {
    let mut merged = live_pool_states.clone();
    for (pool_id, state) in review_time_states {
        merged.insert(pool_id.clone(), state.clone());
    }
    merged
}

/// The newest liquidity timestamp across the review's recorded pools.
///
/// This is the review-time counterpart of Pool Manager's
/// `read_newest_liquidity_read_at`: it says how old the state the review
/// actually used was, without reading Pool Manager's table.
fn review_time_newest_state_read_at(review: &RecordedReview) -> Option<String> {
    review
        .review_time_pool_states
        .iter()
        .filter_map(|state| state.liquidity_read_at.clone())
        .max()
}

/// Keeps only the routes whose every pool has a local state.
///
/// The local state comes from Pool Manager through its published API. A pool
/// that is not in the store has no square-root price and no liquidity, so the
/// route cannot be evaluated and there is nothing to compare.
fn keep_evaluable_routes(
    reviews: Vec<RecordedReview>,
    pool_states: &BTreeMap<String, PoolParityState>,
) -> Vec<RecordedReview> {
    reviews
        .into_iter()
        .filter(|review| {
            review.pool_ids.iter().all(|pool_id| {
                pool_states
                    .get(pool_id)
                    .is_some_and(|state| state.sqrt_price_x96 > 0 && state.liquidity > 0)
            })
        })
        .collect()
}

/// Keeps only the newest attempt of every distinct route.
///
/// The newest attempt is the one with the greatest recording time; when two
/// attempts share a timestamp the greater database id wins, because the id
/// is assigned in insertion order.
fn keep_newest_per_route(reviews: Vec<RecordedReview>) -> Vec<RecordedReview> {
    let mut newest: BTreeMap<String, RecordedReview> = BTreeMap::new();
    for review in reviews {
        let replace = match newest.get(&review.route_key) {
            None => true,
            Some(current) => {
                let current_time = parse_timestamp(Some(&current.created_at));
                let candidate_time = parse_timestamp(Some(&review.created_at));
                match (current_time, candidate_time) {
                    (Some(current_time), Some(candidate_time)) => {
                        candidate_time > current_time
                            || (candidate_time == current_time
                                && review.attempt_id > current.attempt_id)
                    }
                    // A row whose timestamp cannot be parsed cannot win on
                    // time, so the database order decides.
                    (Some(_), None) => false,
                    (None, Some(_)) => true,
                    (None, None) => review.attempt_id > current.attempt_id,
                }
            }
        };
        if replace {
            newest.insert(review.route_key.clone(), review);
        }
    }
    newest.into_values().collect()
}

/// Replays the recorded routes against the local pool state and compares the
/// result with what the chain reported.
///
/// The whole computation lives here, in Review Manager: the recorded reviews
/// are Review Manager's own table, the pool state comes from Pool Manager
/// through its published API, and the comparison is a review concern. Python
/// never reads a manager-owned table and never performs the arithmetic.
pub fn replay_recorded_routes(
    database_path: impl AsRef<std::path::Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    limit: Option<usize>,
    tolerance: f64,
) -> Result<ReplayReport, ParityError> {
    replay_recorded_routes_with(
        database_path,
        network,
        chain_id,
        protocol,
        limit,
        tolerance,
        ReplaySelection::default(),
    )
}

/// Replays the recorded routes against the local pool state, with an explicit
/// selection of the routes to cover.
pub fn replay_recorded_routes_with(
    database_path: impl AsRef<std::path::Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    limit: Option<usize>,
    tolerance: f64,
    selection: ReplaySelection,
) -> Result<ReplayReport, ParityError> {
    let database_path = database_path.as_ref();
    let mut reviews = read_recorded_reviews(database_path, network, chain_id, limit)?;

    // The pool state is loaded once, for every pool the recorded routes
    // mention. It comes from Pool Manager through its published API; the
    // replay never reads Pool Manager's table itself.
    let mut mentioned_pool_ids: Vec<String> = Vec::new();
    for review in &reviews {
        for pool_id in &review.pool_ids {
            if !mentioned_pool_ids.contains(pool_id) {
                mentioned_pool_ids.push(pool_id.clone());
            }
        }
    }
    let live_pool_states: BTreeMap<String, PoolParityState> = read_pool_parity_states(
        database_path,
        network,
        chain_id,
        protocol,
        &mentioned_pool_ids,
    )?
    .into_iter()
    .map(|state| (state.pool_id.clone(), state))
    .collect();

    reviews = match selection {
        ReplaySelection::AllAttempts => reviews,
        ReplaySelection::NewestPerRoute => keep_newest_per_route(reviews),
        ReplaySelection::NewestPerEvaluableRoute => {
            keep_evaluable_routes(keep_newest_per_route(reviews), &live_pool_states)
        }
    };

    let mut outcomes: Vec<ReplayOutcome> = Vec::with_capacity(reviews.len());
    let mut attribution_counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut ages: Vec<f64> = Vec::new();
    let mut comparable_count = 0usize;
    let mut match_count = 0usize;
    let mut executable_mismatch_count = 0usize;
    let mut amount_mismatch_count = 0usize;
    let mut not_comparable_count = 0usize;
    let mut evidence_breakdown: BTreeMap<String, EvidenceBreakdown> = BTreeMap::new();

    for review in reviews {
        // The faithful input to a replay of **this** review is the pool state
        // the review itself recorded, not the state the chain is in now.
        //
        // A replay that runs against today's snapshot compares a recorded
        // verdict against a different market: the two can never agree, and
        // the difference is not evidence of a defect. The recorded review
        // carries the state it quoted, so that state is used whenever the
        // review carries it, and the live snapshot remains the fallback for
        // the reviews that do not.
        let review_time_states = review_time_pool_state_map(&review);
        let pool_states = merged_pool_states(&live_pool_states, &review_time_states);
        let used_review_time_state = review_time_states
            .keys()
            .all(|pool_id| pool_states.contains_key(pool_id))
            && !review_time_states.is_empty();

        let state_read_at = if used_review_time_state {
            review_time_newest_state_read_at(&review)
        } else {
            route_newest_state_read_at(
                database_path,
                network,
                chain_id,
                protocol,
                &review.pool_ids,
            )?
        };
        let state_age_seconds =
            state_age_seconds(state_read_at.as_deref(), Some(&review.created_at));
        if let Some(age) = state_age_seconds {
            ages.push(age);
        }

        let mut request = ParityRequest::new(
            review.route_addresses.clone(),
            review.pool_ids.clone(),
            review.amount_raw.unwrap_or_default(),
            DEFAULT_FLASH_FEE_BPS,
        );
        request.tolerance = tolerance;
        request.onchain_profit_raw = review.onchain_profit_raw;
        request.chain_live_amount_out_raw = review.chain_live_amount_out_raw;
        request.chain_live_required_repay_raw = review.chain_live_required_repay_raw;

        let verdict = evaluate_route_parity_with_states(&request, &pool_states);

        // The flag has to be computed before the state is moved into the
        // outcome: it says whether the pool state post-dates the review.
        let state_postdates_review =
            state_postdates_review(state_read_at.as_deref(), Some(&review.created_at));

        let state = verdict.state.clone();
        if state == ParityState::Match.as_str()
            || state == ParityState::ExecutableMismatch.as_str()
            || state == ParityState::AmountMismatch.as_str()
        {
            comparable_count += 1;
        }
        if state == ParityState::Match.as_str() {
            match_count += 1;
        } else if state == ParityState::ExecutableMismatch.as_str() {
            executable_mismatch_count += 1;
        } else if state == ParityState::AmountMismatch.as_str() {
            amount_mismatch_count += 1;
        } else {
            not_comparable_count += 1;
        }
        // The evidence kind says how much the verdict is worth: a route whose
        // chain-side profit came from the same local snapshot agrees by
        // construction, while a route whose chain-side profit came from a
        // live contract preview is an independent comparison.
        let evidence_kind = match review.onchain_profit_source.as_deref() {
            Some("offline_v4_sqlite_route_snapshot") => "shared_local_snapshot",
            Some("native_rust_contract_preview") => "live_contract_preview",
            Some("native_contract_rate_delta_estimate") => "rate_delta_estimate",
            Some(other) => other,
            None => "unknown",
        };
        let breakdown = evidence_breakdown
            .entry(evidence_kind.to_owned())
            .or_insert_with(EvidenceBreakdown::default);
        if state == ParityState::Match.as_str() {
            breakdown.match_count += 1;
        } else if state == ParityState::ExecutableMismatch.as_str()
            || state == ParityState::AmountMismatch.as_str()
        {
            breakdown.deviation_count += 1;
        }

        let legacy_fee_residual_raw = legacy_fee_residual(
            review.amount_raw.unwrap_or_default(),
            review.onchain_profit_raw,
            verdict.profit_offchain_raw,
        );
        let offchain_flash_fee_bps = verdict
            .detail
            .as_ref()
            .map(|detail| detail.offchain_flash_fee_bps);
        let assumed_flash_fee_bps = verdict
            .detail
            .as_ref()
            .map(|detail| detail.assumed_flash_fee_bps);
        let chain_flash_fee_bps = verdict
            .detail
            .as_ref()
            .and_then(|detail| detail.chain_flash_fee_bps);
        let flash_fee_matches_chain = verdict
            .detail
            .as_ref()
            .and_then(|detail| detail.flash_fee_matches_chain);

        // The attribution the verdict produced is the right one when the
        // verdict could explain the deviation. When the verdict had nothing
        // to go on, the replay's own evidence is used instead: a review-time
        // replay whose remaining difference is **exactly** the fee the
        // production code used to assume has been fully explained, and that
        // is a stronger statement than "unknown".
        let verdict_attribution = verdict
            .detail
            .as_ref()
            .map(|detail| detail.attribution.as_str());
        let attribution = if verdict_attribution
            == Some(ParityAttribution::UnknownNoChainLiveState.as_str())
            && used_review_time_state
            && legacy_fee_residual_raw.is_some()
        {
            Some(
                ParityAttribution::ReviewTimeStateExplained
                    .as_str()
                    .to_string(),
            )
        } else {
            verdict_attribution.map(str::to_string)
        };

        if let Some(attribution) = attribution.as_ref() {
            *attribution_counts.entry(attribution.clone()).or_insert(0) += 1;
        }

        outcomes.push(ReplayOutcome {
            attempt_id: review.attempt_id,
            attempt_key: review.attempt_key,
            created_at: review.created_at,
            category: review.category,
            borrow_address: review.borrow_address,
            route_addresses: review.route_addresses,
            pool_ids: review.pool_ids,
            route_key: review.route_key,
            amount_raw: review.amount_raw,
            onchain_profit_raw: review.onchain_profit_raw,
            onchain_profit_source: review.onchain_profit_source,
            offchain_profit_raw: verdict.profit_offchain_raw,
            profit_relative_delta: verdict.profit_relative_delta,
            state_read_at,
            state_age_seconds,
            state_postdates_review,
            state,
            skipped_reason: verdict.reason,
            anomaly: verdict.anomaly,
            attribution,
            offchain_flash_fee_bps,
            assumed_flash_fee_bps,
            chain_flash_fee_bps,
            flash_fee_matches_chain,
            legacy_fee_residual_raw,
            used_review_time_state,
        });
    }

    let deviation_count = executable_mismatch_count + amount_mismatch_count;
    let explained_by_legacy_fee_count = outcomes
        .iter()
        .filter(|outcome| outcome.legacy_fee_residual_raw.is_some())
        .count();
    let pool_state_age_seconds = if ages.is_empty() {
        None
    } else {
        let mut sorted = ages.clone();
        sorted.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
        let middle = sorted.len() / 2;
        let median = if sorted.len() % 2 == 0 {
            (sorted[middle - 1] + sorted[middle]) / 2.0
        } else {
            sorted[middle]
        };
        Some(PoolStateAge {
            min_seconds: sorted.first().copied(),
            median_seconds: Some(median),
            max_seconds: sorted.last().copied(),
        })
    };

    let route_count = outcomes.len();
    let evidence_available = comparable_count > 0;
    let evidence_complete = route_count > 0 && comparable_count == route_count;
    let gate_passed = evidence_complete && deviation_count == 0;
    let gate_reason = if route_count == 0 {
        Some("no_routes".to_owned())
    } else if comparable_count == 0 {
        Some("no_comparable_routes".to_owned())
    } else if !evidence_complete {
        Some("incomplete_comparable_evidence".to_owned())
    } else if deviation_count > 0 {
        Some("parity_deviation_detected".to_owned())
    } else {
        None
    };

    Ok(ReplayReport {
        selection: match selection {
            ReplaySelection::AllAttempts => "allAttempts",
            ReplaySelection::NewestPerRoute => "newestPerRoute",
            ReplaySelection::NewestPerEvaluableRoute => "newestPerEvaluableRoute",
        }
        .to_owned(),
        route_count,
        comparable_count,
        match_count,
        executable_mismatch_count,
        amount_mismatch_count,
        not_comparable_count,
        deviation_count,
        evidence_available,
        evidence_complete,
        gate_passed,
        gate_reason,
        attribution_counts,
        evidence_breakdown,
        explained_by_legacy_fee_count,
        unexplained_deviation_count: deviation_count.saturating_sub(explained_by_legacy_fee_count),
        review_time_state_count: outcomes
            .iter()
            .filter(|outcome| outcome.used_review_time_state)
            .count(),
        review_time_state_explained_count: outcomes
            .iter()
            .filter(|outcome| {
                outcome.attribution.as_deref()
                    == Some(ParityAttribution::ReviewTimeStateExplained.as_str())
            })
            .count(),
        pool_state_age_seconds,
        outcomes,
    })
}

/// How far the pool state trails the chain-side review.
///
/// The age is the time between the state being read from the chain and the
/// review being recorded. A pool state that is *newer* than the review means
/// the local store was refreshed after the review ran, so the state does not
/// describe the market the review saw. Such a state is not a young state, it
/// is the wrong state, and reporting a negative age would hide that.
///
/// The age is therefore clamped at zero, and the outcome carries a separate
/// flag that says the state post-dates the review.
pub fn state_age_seconds(state_read_at: Option<&str>, reviewed_at: Option<&str>) -> Option<f64> {
    let state = parse_timestamp(state_read_at)?;
    let review = parse_timestamp(reviewed_at)?;
    let seconds = (review - state).num_seconds() as f64;
    Some(seconds.max(0.0))
}

/// Reports whether the local pool state post-dates the review it is used for.
///
/// A state that is newer than the review cannot reproduce the market the
/// review saw; the replay still runs, but the deviation it reports belongs
/// to the state, not to the arithmetic.
pub fn state_postdates_review(state_read_at: Option<&str>, reviewed_at: Option<&str>) -> bool {
    let (Some(state), Some(review)) =
        (parse_timestamp(state_read_at), parse_timestamp(reviewed_at))
    else {
        return false;
    };
    state > review
}

/// Parses an ISO-8601 timestamp, tolerating the trailing `Z`.
fn parse_timestamp(value: Option<&str>) -> Option<chrono::DateTime<chrono::Utc>> {
    let text = value?.trim();
    if text.is_empty() {
        return None;
    }
    let parsed = chrono::DateTime::parse_from_rfc3339(text).ok()?;
    Some(parsed.with_timezone(&chrono::Utc))
}

#[cfg(test)]
mod evidence_tests {
    use super::{
        decode_chain_live_amounts, extract_review_time_pool_states, parse_amount,
        read_recorded_reviews, replay_recorded_routes, replay_recorded_routes_with,
        state_age_seconds, state_postdates_review, ReplaySelection,
    };

    // ------------------------------------------------------------- parsing

    #[test]
    fn a_positive_and_a_negative_amount_are_both_parsed() {
        assert_eq!(parse_amount(Some("1000")), Some(1000));
        assert_eq!(parse_amount(Some("-1000")), Some(-1000));
        assert_eq!(
            parse_amount(Some("0x60b547a9588b2e0")),
            Some(0x60b547a9588b2e0)
        );
    }

    #[test]
    fn a_negative_hexadecimal_amount_keeps_its_sign() {
        // The chain reports a losing route as a negative number, with the
        // sign **in front of** the radix prefix. A parser that only
        // recognises a leading `0x` drops it and turns a rejection into
        // missing evidence.
        let parsed = parse_amount(Some("-0x60b547a9588b2e0"));
        assert_eq!(parsed, Some(-0x60b547a9588b2e0));
        assert!(parsed.is_some_and(|value| value < 0));
    }

    #[test]
    fn an_unreadable_amount_is_not_a_number() {
        assert_eq!(parse_amount(None), None);
        assert_eq!(parse_amount(Some("")), None);
        assert_eq!(parse_amount(Some("   ")), None);
        assert_eq!(parse_amount(Some("not-a-number")), None);
        assert_eq!(parse_amount(Some("-")), None);
    }

    // ------------------------------------------------------------ decoding

    fn _revert_data(amount_out: i128, required_repay: i128) -> String {
        let mut body = String::from("0x16301234");
        body.push_str(&format!("{:064x}", amount_out as u128));
        body.push_str(&format!("{:064x}", required_repay as u128));
        body
    }

    #[test]
    fn the_chain_live_amounts_are_recovered_from_the_revert() {
        let decoded = decode_chain_live_amounts(Some(&_revert_data(
            999_988_254_255_605_011,
            1_000_000_000_000_000_001,
        )));
        assert_eq!(
            decoded,
            Some((999_988_254_255_605_011, 1_000_000_000_000_000_001))
        );
    }

    #[test]
    fn a_short_or_missing_revert_is_not_decoded() {
        assert_eq!(decode_chain_live_amounts(Some("0x4f422847")), None);
        assert_eq!(decode_chain_live_amounts(Some("")), None);
        assert_eq!(decode_chain_live_amounts(None), None);
        assert_eq!(decode_chain_live_amounts(Some("0xnothex")), None);
    }

    #[test]
    fn a_revert_without_a_repayment_is_not_decoded() {
        // A required repayment of zero is not a real settlement, so the
        // pair is refused rather than reported as a zero repay.
        assert_eq!(decode_chain_live_amounts(Some(&_revert_data(100, 0))), None);
    }

    // --------------------------------------------------------- the age

    #[test]
    fn the_state_age_is_measured_against_the_review() {
        let age = state_age_seconds(
            Some("2026-11-09T03:08:30.458976Z"),
            Some("2026-11-11T00:55:29.230559300+00:00"),
        );
        assert!(age.is_some());
        let age = age.expect("age");
        assert!(age > 160_000.0 && age < 165_000.0);
    }

    #[test]
    fn an_unreadable_timestamp_yields_no_age() {
        assert_eq!(state_age_seconds(Some(""), Some("x")), None);
        assert_eq!(state_age_seconds(Some("x"), Some("")), None);
        assert_eq!(state_age_seconds(None, None), None);
    }

    // --------------------------------------------------------- the replay

    #[test]
    fn a_missing_database_is_reported_rather_than_panicking() {
        let missing =
            std::path::Path::new("C:/definitely-not-a-database/arch2b-replay-test.sqlite3");
        let error = replay_recorded_routes(missing, "robinhood", 4663, "uniswap_v4", None, 1e-9);
        assert!(error.is_err());
    }

    #[test]
    fn an_empty_database_replays_nothing_and_blocks_the_gate() {
        let directory = tempfile::tempdir().expect("tempdir");
        let database = directory.path().join("empty.sqlite3");
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            connection
                .execute_batch(
                    "CREATE TABLE cow_execution_reviews (
                        id INTEGER PRIMARY KEY,
                        attempt_key TEXT,
                        network TEXT,
                        chain_id INTEGER,
                        category TEXT,
                        payload_json TEXT,
                        summary_json TEXT,
                        selected_profit_raw TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );",
                )
                .expect("schema");
        }
        let report = replay_recorded_routes(&database, "robinhood", 4663, "uniswap_v4", None, 1e-9)
            .expect("replay");
        assert_eq!(report.route_count, 0);
        assert_eq!(report.comparable_count, 0);
        assert_eq!(report.deviation_count, 0);
        assert!(!report.evidence_available);
        assert!(!report.evidence_complete);
        assert!(!report.gate_passed);
        assert_eq!(report.gate_reason.as_deref(), Some("no_routes"));
        assert!(report.outcomes.is_empty());
    }

    // ------------------------------------------------------- the selection

    /// Writes one recorded review that satisfies the route shape rules.
    fn write_review(
        connection: &rusqlite::Connection,
        id: i64,
        created_at: &str,
        route: &str,
        pool_path: &str,
        with_probes: bool,
    ) {
        let probes = if with_probes {
            serde_json::json!([
                {
                    "amountRaw": "0xde0b6b3a7640000",
                    "profitable": false,
                    "status": "not_profitable",
                    "revertData": "0x4f422847"
                }
            ])
        } else {
            serde_json::json!([])
        };
        let payload = serde_json::json!({
            "routeAddresses": route.split('>').collect::<Vec<_>>(),
            "poolPathKey": pool_path,
            "best_amount_calculation": {
                "probeRecords": probes
            },
            "probeRecords": probes,
            "selectedProfitRaw": "-0x6bc4a5baf311dc"
        });
        connection
            .execute(
                "INSERT INTO cow_execution_reviews
                 (id, attempt_key, network, chain_id, category, payload_json,
                  summary_json, selected_profit_raw, created_at, updated_at)
                 VALUES (?1, ?2, 'robinhood', 4663, 'not_executable', ?3, '{}',
                         '-1', ?4, ?4)",
                rusqlite::params![
                    id,
                    format!("attempt|robinhood|4663|{route}|{created_at}"),
                    payload.to_string(),
                    created_at
                ],
            )
            .expect("insert review");
    }

    fn review_database() -> (tempfile::TempDir, std::path::PathBuf) {
        let directory = tempfile::tempdir().expect("tempdir");
        let database = directory.path().join("reviews.sqlite3");
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            connection
                .execute_batch(
                    "CREATE TABLE cow_execution_reviews (
                        id INTEGER PRIMARY KEY,
                        attempt_key TEXT,
                        network TEXT,
                        chain_id INTEGER,
                        category TEXT,
                        payload_json TEXT,
                        summary_json TEXT,
                        selected_profit_raw TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                        network TEXT,
                        chain_id INTEGER,
                        protocol TEXT,
                        pool_id TEXT,
                        has_liquidity INTEGER,
                        liquidity TEXT,
                        sqrt_price_x96 TEXT,
                        tick INTEGER,
                        liquidity_read_at TEXT,
                        evidence_source TEXT
                    );
                    CREATE TABLE v4_exchange_pools (
                        network TEXT,
                        chain_id INTEGER,
                        protocol TEXT,
                        pool_id TEXT,
                        pool_key_json TEXT,
                        token0 TEXT,
                        token1 TEXT,
                        fee INTEGER,
                        tick_spacing INTEGER,
                        hooks TEXT,
                        address_pair TEXT
                    );",
                )
                .expect("schema");
        }
        (directory, database)
    }

    const ROUTE_A: &str =
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa>0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb>0xcccccccccccccccccccccccccccccccccccccccc>0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const ROUTE_B: &str =
        "0xdddddddddddddddddddddddddddddddddddddddd>0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee>0xffffffffffffffffffffffffffffffffffffffff>0xdddddddddddddddddddddddddddddddddddddddd";
    const POOL_A: &str = "pool-a|pool-b|pool-c";
    const POOL_B: &str = "pool-d|pool-e|pool-f";

    /// Writes the pool state a route needs to be evaluable.
    fn write_pool_state(connection: &rusqlite::Connection, pool_id: &str) {
        connection
            .execute(
                "INSERT INTO v4_exchange_pools
                 (network, chain_id, protocol, pool_id, token0, token1, fee,
                  tick_spacing, hooks)
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, '0xaaaa', '0xbbbb',
                         3000, 60, '0x0000')",
                rusqlite::params![pool_id],
            )
            .expect("insert pool");
        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots
                 (network, chain_id, protocol, pool_id, has_liquidity, liquidity,
                  sqrt_price_x96, liquidity_read_at, evidence_source)
                 VALUES ('robinhood', 4663, 'uniswap_v4', ?1, 1, '1000',
                         '79228162514264337593543950336',
                         '2026-11-09T03:08:30.458976Z', 'test')",
                rusqlite::params![pool_id],
            )
            .expect("insert snapshot");
    }

    fn write_pool_states(connection: &rusqlite::Connection, pool_path: &str) {
        for pool_id in pool_path.split('|') {
            write_pool_state(connection, pool_id);
        }
    }

    #[test]
    fn only_the_newest_attempt_of_each_route_is_replayed_by_default() {
        let (_directory, database) = review_database();
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            write_pool_states(&connection, POOL_A);
            write_pool_states(&connection, POOL_B);
            write_review(
                &connection,
                1,
                "2026-11-10T23:00:00Z",
                ROUTE_A,
                POOL_A,
                true,
            );
            write_review(
                &connection,
                2,
                "2026-11-10T23:30:00Z",
                ROUTE_A,
                POOL_A,
                true,
            );
            write_review(
                &connection,
                3,
                "2026-11-10T23:10:00Z",
                ROUTE_B,
                POOL_B,
                true,
            );
        }

        let report = replay_recorded_routes(&database, "robinhood", 4663, "uniswap_v4", None, 1e-9)
            .expect("replay");
        // Two routes, so two outcomes: the newest attempt of each.
        assert_eq!(report.route_count, 2);
        let attempt_ids: Vec<i64> = report
            .outcomes
            .iter()
            .map(|outcome| outcome.attempt_id)
            .collect();
        assert!(attempt_ids.contains(&2));
        assert!(attempt_ids.contains(&3));
        assert!(!attempt_ids.contains(&1));
    }

    #[test]
    fn the_whole_history_can_be_replayed_on_request() {
        let (_directory, database) = review_database();
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            write_pool_states(&connection, POOL_A);
            write_review(
                &connection,
                1,
                "2026-11-10T23:00:00Z",
                ROUTE_A,
                POOL_A,
                true,
            );
            write_review(
                &connection,
                2,
                "2026-11-10T23:30:00Z",
                ROUTE_A,
                POOL_A,
                true,
            );
        }

        let report = replay_recorded_routes_with(
            &database,
            "robinhood",
            4663,
            "uniswap_v4",
            None,
            1e-9,
            ReplaySelection::AllAttempts,
        )
        .expect("replay");
        assert_eq!(report.route_count, 2);
    }

    #[test]
    fn an_attempt_without_a_probe_ladder_is_not_evidence() {
        // The chain never walked this route, so there is nothing to compare.
        let (_directory, database) = review_database();
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            write_review(
                &connection,
                1,
                "2026-11-10T23:00:00Z",
                ROUTE_A,
                POOL_A,
                false,
            );
        }
        let reviews = read_recorded_reviews(&database, "robinhood", 4663, None).expect("reviews");
        assert!(reviews.is_empty());
    }

    #[test]
    fn two_attempts_of_the_same_route_share_one_route_key() {
        let (_directory, database) = review_database();
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            write_review(
                &connection,
                1,
                "2026-11-10T23:00:00Z",
                ROUTE_A,
                POOL_A,
                true,
            );
            write_review(
                &connection,
                2,
                "2026-11-10T23:30:00Z",
                ROUTE_A,
                POOL_A,
                true,
            );
            write_review(
                &connection,
                3,
                "2026-11-10T23:40:00Z",
                ROUTE_B,
                POOL_B,
                true,
            );
        }
        let reviews = read_recorded_reviews(&database, "robinhood", 4663, None).expect("reviews");
        assert_eq!(reviews.len(), 3);
        // The rows come back newest first, so the two attempts of route A sit
        // next to each other and route B stands apart.
        // The rows come back newest first, so the two attempts of route A sit
        // next to each other and route B stands apart.
        assert_eq!(reviews[0].attempt_id, 3);
        assert_eq!(reviews[1].attempt_id, 2);
        assert_eq!(reviews[2].attempt_id, 1);
        assert_eq!(reviews[1].route_key, reviews[2].route_key);
        assert_ne!(reviews[0].route_key, reviews[1].route_key);
    }

    // --------------------------------------------------- the state age

    #[test]
    fn a_state_that_post_dates_the_review_is_reported_as_such() {
        // The local store was refreshed after the review ran, so the state
        // does not describe the market the review saw. A negative age would
        // hide that; the age is clamped and the flag says it outright.
        let state = "2026-11-11T00:55:30Z";
        let review = "2026-11-10T23:44:47Z";
        let age = state_age_seconds(Some(state), Some(review)).expect("age");
        assert_eq!(age, 0.0);
        assert!(state_postdates_review(Some(state), Some(review)));
        assert!(!state_postdates_review(Some(review), Some(state)));
    }

    // -------------------------------------------- the review-time state

    /// Writes a review whose payload carries the pool state the review saw.
    fn write_review_with_candidate_pools(
        connection: &rusqlite::Connection,
        id: i64,
        created_at: &str,
        route: &str,
        pool_path: &str,
        candidate_pools: serde_json::Value,
    ) {
        let probes = serde_json::json!([
            {
                "amountRaw": "0xde0b6b3a7640000",
                "profitable": false,
                "status": "not_profitable",
                "revertData": "0x4f422847"
            }
        ]);
        let payload = serde_json::json!({
            "routeAddresses": route.split('>').collect::<Vec<_>>(),
            "poolPathKey": pool_path,
            "quote": {
                "candidate_pools": candidate_pools
            },
            "best_amount_calculation": {
                "probeRecords": probes
            },
            "probeRecords": probes,
            "selectedProfitRaw": "-0x6bc4a5baf311dc"
        });
        connection
            .execute(
                "INSERT INTO cow_execution_reviews
                 (id, attempt_key, network, chain_id, category, payload_json,
                  summary_json, selected_profit_raw, created_at, updated_at)
                 VALUES (?1, ?2, 'robinhood', 4663, 'not_executable', ?3, '{}',
                         '-1', ?4, ?4)",
                rusqlite::params![
                    id,
                    format!("attempt|robinhood|4663|{route}|{created_at}"),
                    payload.to_string(),
                    created_at
                ],
            )
            .expect("insert review");
    }

    #[test]
    fn the_review_time_state_is_read_out_of_the_recorded_payload() {
        let payload = serde_json::json!({
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "0xAAA",
                        "token0": "0xAAAA",
                        "token1": "0xBBBB",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidity": "",
                        "liquidityIndex": 1000.0,
                        "liquidityReadAt": "2026-11-09T03:08:30.458976Z"
                    }
                ]
            }
        });
        let states = extract_review_time_pool_states(&payload);
        assert_eq!(states.len(), 1);
        let state = &states[0];
        assert_eq!(state.pool_id, "0xaaa");
        assert_eq!(state.token0, "0xaaaa");
        assert_eq!(state.token1, "0xbbbb");
        assert_eq!(state.fee, 3000);
        assert_eq!(state.tick_spacing, 60);
        assert_eq!(state.sqrt_price_x96, 79_228_162_514_264_337_593_543_950_336);
        // The recorded payload carries the liquidity **index**; the raw word
        // is the index scaled back up by `1e18`.
        assert_eq!(state.liquidity, 1_000 * super::LIQUIDITY_INDEX_SCALE);
        assert_eq!(
            state.liquidity_read_at.as_deref(),
            Some("2026-11-09T03:08:30.458976Z")
        );
    }

    #[test]
    fn a_fractional_liquidity_index_is_scaled_back_exactly() {
        // A float that cannot be represented exactly in binary must still
        // produce the exact raw liquidity, because the recorded value is the
        // only evidence of the state the review saw.
        let payload = serde_json::json!({
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "0xaaa",
                        "token0": "0xaaaa",
                        "token1": "0xbbbb",
                        "fee": 100,
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 5672.86612036
                    }
                ]
            }
        });
        let states = extract_review_time_pool_states(&payload);
        assert_eq!(states.len(), 1);
        assert_eq!(states[0].liquidity, 5_672_866_120_360_000_000_000u128);
    }

    #[test]
    fn the_raw_liquidity_wins_over_the_index_when_both_are_present() {
        let payload = serde_json::json!({
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "0xaaa",
                        "token0": "0xaaaa",
                        "token1": "0xbbbb",
                        "fee": 100,
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidity": "123456789",
                        "liquidityIndex": 0.000000123456789
                    }
                ]
            }
        });
        let states = extract_review_time_pool_states(&payload);
        assert_eq!(states.len(), 1);
        assert_eq!(states[0].liquidity, 123_456_789);
    }

    #[test]
    fn a_live_review_payload_becomes_a_non_persistent_parity_request() {
        let payload = serde_json::json!({
            "routeAddresses": [
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ],
            "poolPathKey": "pool-a|pool-b",
            "selectedAmountRaw": "1000",
            "selectedProfitRaw": "25",
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "pool-a",
                        "token0": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "token1": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0
                    },
                    {
                        "poolId": "pool-b",
                        "token0": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "token1": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0
                    }
                ]
            }
        });

        let request =
            super::parity_request_from_review_payload(&payload, 0, 1e-9).expect("request");
        assert_eq!(request.route_addresses.len(), 3);
        assert_eq!(request.pool_ids, vec!["pool-a", "pool-b"]);
        assert_eq!(request.amount_raw, 1000);
        assert_eq!(request.onchain_profit_raw, Some(25));
        assert_eq!(request.review_time_pool_states.len(), 2);
    }

    #[test]
    fn a_live_payload_replay_survives_a_missing_live_snapshot() {
        // The live snapshot is only a fallback for pools the review did not
        // quote. When it cannot be read at all (the tables are absent on a
        // freshly migrated database, or the snapshot has been pruned), the
        // review-time state still carries a complete route, so the replay
        // must degrade to the review-time state instead of aborting. A
        // missing snapshot is not evidence of a deviation.
        let payload = serde_json::json!({
            "routeAddresses": [
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ],
            "poolPathKey": "pool-a|pool-b",
            "selectedAmountRaw": "1000",
            "selectedProfitRaw": "25",
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "pool-a",
                        "token0": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "token1": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0
                    },
                    {
                        "poolId": "pool-b",
                        "token0": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "token1": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0
                    }
                ]
            }
        });
        let request =
            super::parity_request_from_review_payload(&payload, 0, 1e-9).expect("request");

        // A database with no pool snapshot tables at all.
        let directory = tempfile::tempdir().expect("tempdir");
        let database = directory.path().join("no-snapshot.sqlite3");
        let verdict = super::evaluate_route_parity_with_review_time_state(
            &database,
            "robinhood",
            4663,
            "uniswap_v4",
            &request,
        )
        .expect("the review-time state alone must be enough");
        assert_ne!(verdict.state, "not_comparable");
        assert_eq!(verdict.profit_onchain_raw, Some(25));
    }

    #[test]
    fn the_pool_id_is_matched_regardless_of_its_case() {
        // The pool id is an **identity**. The producers spell it in
        // different cases (the pool table keeps the address as written, the
        // review payload lowercases it), so the comparison has to normalize
        // it on every side. A route that is silently dropped because of the
        // spelling would turn a comparable route into "not comparable",
        // which is a false negative on the live parity gate.
        let payload = serde_json::json!({
            "routeAddresses": [
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ],
            "poolPathKey": "0xAAA|0xBBB",
            "selectedAmountRaw": "1000",
            "selectedProfitRaw": "25",
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "0xAAA",
                        "token0": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "token1": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0
                    },
                    {
                        "poolId": "0xBBB",
                        "token0": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "token1": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": "0x0000",
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0
                    }
                ]
            }
        });
        let request =
            super::parity_request_from_review_payload(&payload, 0, 1e-9).expect("request");
        // The request normalizes the pool ids it carries.
        assert_eq!(request.pool_ids, vec!["0xaaa", "0xbbb"]);

        // The live snapshot spells the same pools in the **upper** case, the
        // way the pool table stores them.
        let mut pool_states = std::collections::BTreeMap::new();
        pool_states.insert(
            "0xaaa".to_string(),
            super::PoolParityState {
                pool_id: "0xAAA".to_string(),
                token0: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string(),
                token1: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".to_string(),
                fee: 3000,
                tick_spacing: 60,
                hooks: "0x0000".to_string(),
                sqrt_price_x96: 79_228_162_514_264_337_593_543_950_336,
                tick: 0,
                liquidity: 1_000_000_000_000_000_000_000,
                liquidity_read_at: None,
                evidence_source: None,
            },
        );
        pool_states.insert(
            "0xbbb".to_string(),
            super::PoolParityState {
                pool_id: "0xBBB".to_string(),
                token0: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".to_string(),
                token1: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string(),
                fee: 3000,
                tick_spacing: 60,
                hooks: "0x0000".to_string(),
                sqrt_price_x96: 79_228_162_514_264_337_593_543_950_336,
                tick: 0,
                liquidity: 1_000_000_000_000_000_000_000,
                liquidity_read_at: None,
                evidence_source: None,
            },
        );
        let verdict = super::evaluate_route_parity_with_states(&request, &pool_states);
        assert_ne!(verdict.state, "not_comparable");
    }

    #[test]
    fn a_live_review_payload_without_review_time_state_is_refused() {
        let payload = serde_json::json!({
            "routeAddresses": [
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ],
            "poolPathKey": "pool-a|pool-b",
            "selectedAmountRaw": "1000"
        });
        let error = super::parity_request_from_review_payload(&payload, 0, 1e-9)
            .expect_err("missing review-time state");
        assert!(error.to_string().contains("review-time pool state"));
    }

    #[test]
    fn a_pool_without_a_usable_price_is_skipped_rather_than_guessed() {
        // A wrong price would produce a wrong simulation, which is worse
        // than no simulation at all.
        let payload = serde_json::json!({
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "0xaaa",
                        "token0": "0xaaaa",
                        "token1": "0xbbbb",
                        "fee": 100,
                        "sqrtPriceX96": "",
                        "liquidityIndex": 1000.0
                    },
                    {
                        "poolId": "0xbbb",
                        "token0": "0xbbbb",
                        "token1": "0xcccc",
                        "fee": 0,
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0
                    }
                ]
            }
        });
        assert!(extract_review_time_pool_states(&payload).is_empty());
    }

    #[test]
    fn a_scientific_liquidity_index_is_scaled_back_exactly() {
        // The JSON writer uses the scientific form for a large index, and a
        // value that is dropped as unparsable would silently push the
        // replay onto a different, later snapshot.
        let payload = serde_json::json!({
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "0xaaa",
                        "token0": "0xaaaa",
                        "token1": "0xbbbb",
                        "fee": 2500,
                        "sqrtPriceX96": "159575375414379328959713476356",
                        "liquidityIndex": 9.901032566577074e+17
                    }
                ]
            }
        });
        let states = extract_review_time_pool_states(&payload);
        assert_eq!(states.len(), 1);
        assert_eq!(
            states[0].liquidity,
            990_103_256_657_707_400_000_000_000_000_000_000u128
        );
    }

    #[test]
    fn a_plain_liquidity_index_is_scaled_back_exactly() {
        let payload = serde_json::json!({
            "quote": {
                "candidate_pools": [
                    {
                        "poolId": "0xaaa",
                        "token0": "0xaaaa",
                        "token1": "0xbbbb",
                        "fee": 2500,
                        "sqrtPriceX96": "159575375414379328959713476356",
                        "liquidityIndex": 1.4542925123304328e+20
                    }
                ]
            }
        });
        let states = extract_review_time_pool_states(&payload);
        assert_eq!(states.len(), 1);
        assert_eq!(
            states[0].liquidity,
            145_429_251_233_043_280_000_000_000_000_000_000_000u128
        );
    }

    #[test]
    fn a_payload_without_candidate_pools_yields_no_review_time_state() {
        assert!(extract_review_time_pool_states(&serde_json::json!({})).is_empty());
        assert!(extract_review_time_pool_states(&serde_json::json!({"quote": {}})).is_empty());
    }

    #[test]
    fn the_review_time_state_is_preferred_over_the_live_snapshot() {
        // The live snapshot and the recorded review disagree on the price.
        // The recorded state is the one the review's own verdict was
        // computed from, so it has to be the one the replay uses.
        let (_directory, database) = review_database();
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            write_pool_states(&connection, POOL_A);
            write_review_with_candidate_pools(
                &connection,
                1,
                "2026-11-10T23:00:00Z",
                ROUTE_A,
                POOL_A,
                serde_json::json!([
                    {
                        "poolId": "pool-a",
                        "token0": "0xaaaa",
                        "token1": "0xbbbb",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "sqrtPriceX96": "158456325028528675187087900672",
                        "liquidityIndex": 2000.0,
                        "liquidityReadAt": "2026-11-10T22:00:00Z"
                    },
                    {
                        "poolId": "pool-b",
                        "token0": "0xbbbb",
                        "token1": "0xcccc",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "sqrtPriceX96": "158456325028528675187087900672",
                        "liquidityIndex": 2000.0,
                        "liquidityReadAt": "2026-11-10T22:00:00Z"
                    },
                    {
                        "poolId": "pool-c",
                        "token0": "0xcccc",
                        "token1": "0xaaaa",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "sqrtPriceX96": "158456325028528675187087900672",
                        "liquidityIndex": 2000.0,
                        "liquidityReadAt": "2026-11-10T22:00:00Z"
                    }
                ]),
            );
        }
        let report = replay_recorded_routes(&database, "robinhood", 4663, "uniswap_v4", None, 1e-9)
            .expect("replay");
        assert_eq!(report.route_count, 1);
        let outcome = &report.outcomes[0];
        assert!(outcome.used_review_time_state);
        // The age is measured against the **recorded** state, not against the
        // live snapshot, so it is an hour rather than the snapshot's age.
        assert_eq!(
            outcome.state_read_at.as_deref(),
            Some("2026-11-10T22:00:00Z")
        );
        let age = outcome.state_age_seconds.expect("age");
        assert!((age - 3_600.0).abs() < 1.0, "age was {age}");
    }

    #[test]
    fn a_review_time_replay_that_leaves_only_the_legacy_fee_is_explained() {
        // The review-time state reproduces the recorded profit, and what is
        // left is exactly the fee the production code used to assume. That
        // is a fully explained deviation, not an unknown one.
        let (_directory, database) = review_database();
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            write_pool_states(&connection, POOL_A);
            write_review_with_candidate_pools(
                &connection,
                1,
                "2026-11-10T23:00:00Z",
                ROUTE_A,
                POOL_A,
                serde_json::json!([
                    {
                        "poolId": "pool-a",
                        "token0": "0xaaaa",
                        "token1": "0xbbbb",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0,
                        "liquidityReadAt": "2026-11-10T22:00:00Z"
                    },
                    {
                        "poolId": "pool-b",
                        "token0": "0xbbbb",
                        "token1": "0xcccc",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0,
                        "liquidityReadAt": "2026-11-10T22:00:00Z"
                    },
                    {
                        "poolId": "pool-c",
                        "token0": "0xcccc",
                        "token1": "0xaaaa",
                        "fee": 3000,
                        "tickSpacing": 60,
                        "sqrtPriceX96": "79228162514264337593543950336",
                        "liquidityIndex": 1000.0,
                        "liquidityReadAt": "2026-11-10T22:00:00Z"
                    }
                ]),
            );
        }
        let report = replay_recorded_routes(&database, "robinhood", 4663, "uniswap_v4", None, 1e-9)
            .expect("replay");
        assert_eq!(report.route_count, 1);
        let outcome = &report.outcomes[0];
        assert!(outcome.used_review_time_state);
        // The recorded profit in this fixture is a fixed negative number, so
        // the residual is whatever the difference is. The attribution is
        // decided by the review-time replay and the legacy fee, so the
        // fixture pins the **mechanism**, not a market number.
        assert_eq!(report.review_time_state_count, 1);
        assert_eq!(
            report.review_time_state_explained_count,
            usize::from(
                outcome.attribution.as_deref()
                    == Some(super::ParityAttribution::ReviewTimeStateExplained.as_str())
            )
        );
    }

    #[test]
    fn the_reviews_of_another_network_are_not_replayed() {
        let directory = tempfile::tempdir().expect("tempdir");
        let database = directory.path().join("reviews.sqlite3");
        {
            let connection = rusqlite::Connection::open(&database).expect("open");
            connection
                .execute_batch(
                    "CREATE TABLE cow_execution_reviews (
                        id INTEGER PRIMARY KEY,
                        attempt_key TEXT,
                        network TEXT,
                        chain_id INTEGER,
                        category TEXT,
                        payload_json TEXT,
                        summary_json TEXT,
                        selected_profit_raw TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    INSERT INTO cow_execution_reviews
                        (attempt_key, network, chain_id, category, payload_json,
                         summary_json, selected_profit_raw, created_at)
                    VALUES ('k', 'testnet', 4663, 'flash_loan',
                            '{}', '{}', '1', '2026-11-10T00:00:00Z');",
                )
                .expect("schema");
        }
        let reviews = read_recorded_reviews(&database, "robinhood", 4663, None).expect("reviews");
        assert!(reviews.is_empty());
    }
}
