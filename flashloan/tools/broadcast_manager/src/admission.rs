//! The broadcast admission decision.
//!
//! This module owns **one** judgement: may this broadcast request leave
//! the system right now?
//!
//! Before this module the decision was scattered across two layers:
//! the review layer published a request when its own rules were met,
//! and the broadcast layer re-checked two of those rules by hand
//! (`request_status` and a positive PnL). Nothing re-checked freshness,
//! nothing recorded *why* a request was refused, and nothing answered
//! "why was this one sent".
//!
//! The rules live here so that:
//!
//! * there is **one** place that decides,
//! * the decision is **auditable** — every rule reports its own verdict,
//! * a refusal is **as well recorded** as a pass (`BC-3`).
//!
//! # The rules
//!
//! `BC-1` is five predicates, all of which must hold:
//!
//! 1. the review measured the loop and it completed (`run.ok`),
//! 2. the route was executable (`run.executable`),
//! 3. the signed PnL is **measured** and strictly positive,
//! 4. the review is **fresh** (inside the freshness window),
//! 5. the evidence is complete (the review row is present).
//!
//! `BC-2` adds the runtime guardrails: chain, account, limit and time
//! window. These are **automatic** — a missing chain or account refuses
//! the send, a breached limit stops the run, and a closed window defers
//! this round. None of them is a human approval step.
//!
//! # Fail-closed
//!
//! Every rule is fail-closed. A value that was never measured reads as
//! "not proven", never as zero and never as a pass. This is why the
//! PnL amount, the run flags and the review stamp are all optional:
//! absent means absent.

use review_manager::BroadcastAdmissionFacts;
use serde::{Deserialize, Serialize};

/// How far a review stamp may sit in the future before it is refused.
///
/// A stamp from the future means the clocks disagree. That is not a
/// measurement, so the request is refused rather than trusted.
pub const MAX_BROADCAST_FUTURE_SKEW_SECONDS: i64 = 30;

/// The runtime guardrails for one chain (`BC-2`).
///
/// The guardrails are **runtime configuration**, not a human approval
/// step. A deployment that cannot name its account or its limit does not
/// get a broadcast; it gets a refusal with a reason.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastGuardPolicy {
    /// The chain this policy governs.
    ///
    /// A request whose chain does not match is refused.
    pub chain_id: Option<i64>,
    /// The account that may sign.
    ///
    /// A request that names a different signer is refused. An absent
    /// account means the deployment never configured one, so every
    /// request is refused.
    pub signer_address: Option<String>,
    /// The largest amount that may be broadcast in one round, in
    /// anchor-token raw units.
    ///
    /// A request above the limit is refused. An absent limit means no
    /// limit was configured, so every request is refused — an unbounded
    /// broadcast is never the safe default.
    pub max_amount_raw: Option<u128>,
    /// The window during which a broadcast may be sent.
    ///
    /// Outside the window the request is **deferred**, not refused: the
    /// review is still valid, it is simply not this round's business.
    pub window_start: Option<String>,
    pub window_end: Option<String>,
}

/// The runtime context a submission is judged against.
///
/// The context is what the **deployment** knows right now: which chain
/// it is on, which account it signs with, how much it may move, and
/// whether the current time is inside its trading window.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BroadcastAdmissionContext {
    /// The guardrails to apply.
    pub policy: BroadcastGuardPolicy,
    /// The amount the request would move, in anchor-token raw units.
    ///
    /// `None` means the amount was not measured. It is **not** zero:
    /// an unmeasured amount cannot be shown to be inside a limit, so it
    /// is refused.
    pub amount_raw: Option<u128>,
    /// The time the decision is made at.
    ///
    /// The caller supplies it so that the decision is reproducible and
    /// testable. When absent the current UTC time is used.
    pub now: Option<String>,
    /// The moment the closed loop of the request is scheduled at.
    ///
    /// The moment is the loop's **own** review time point: the helper
    /// that schedules a loop wrote it, and it already carries whatever
    /// the review interval is.  The admission therefore compares the
    /// current time with that moment and never rebuilds an interval of
    /// its own -- an age counted from the review stamp would count the
    /// time the review itself took, which is not time the market moved.
    pub loop_scheduled_at: Option<String>,
}

/// The identifier of a single admission rule.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AdmissionRule {
    /// The review exists and completed.
    ReviewCompleted,
    /// `run.ok` was true.
    RunOk,
    /// `run.executable` was true.
    RunExecutable,
    /// The signed PnL was measured.
    PnlMeasured,
    /// The signed PnL was strictly positive.
    PnlPositive,
    /// The review is inside the freshness window.
    ReviewFresh,
    /// The review row and its evidence are present.
    EvidenceComplete,
    /// The request carries complete per-hop price limits.
    PriceLimitsComplete,
    /// The request names the chain the deployment is on.
    ChainMatches,
    /// The request names the configured account.
    AccountMatches,
    /// The amount is measured and inside the configured limit.
    AmountWithinLimit,
    /// The current time is inside the configured window.
    WithinWindow,
}

impl AdmissionRule {
    /// The stable code recorded in the audit trail.
    pub fn code(self) -> &'static str {
        match self {
            Self::ReviewCompleted => "BC-1.review_completed",
            Self::RunOk => "BC-1.run_ok",
            Self::RunExecutable => "BC-1.run_executable",
            Self::PnlMeasured => "BC-1.pnl_measured",
            Self::PnlPositive => "BC-1.pnl_positive",
            Self::ReviewFresh => "BC-1.review_fresh",
            Self::EvidenceComplete => "BC-1.evidence_complete",
            Self::PriceLimitsComplete => "BC-1.price_limits_complete",
            Self::ChainMatches => "BC-2.chain_matches",
            Self::AccountMatches => "BC-2.account_matches",
            Self::AmountWithinLimit => "BC-2.amount_within_limit",
            Self::WithinWindow => "BC-2.within_window",
        }
    }
}

/// The verdict of a single rule.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AdmissionCheck {
    pub rule: AdmissionRule,
    /// Whether the rule held.
    pub satisfied: bool,
    /// Why it did or did not hold.
    ///
    /// The reason is **always** present, including on a pass. "Why was
    /// this one sent" has to be answerable from the trail alone.
    pub reason: String,
}

/// The outcome of the whole admission decision.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AdmissionDecision {
    /// Whether the request may be sent.
    pub admitted: bool,
    /// Every rule that was checked, in a stable order.
    pub checks: Vec<AdmissionCheck>,
    /// The rules that did not hold.
    ///
    /// A refusal always names at least one. An empty list on a refusal
    /// would be an unauditable refusal.
    pub failed_rules: Vec<String>,
    /// The age of the review, in seconds, at the moment of the decision.
    ///
    /// `None` means the age could not be computed — the stamp was
    /// missing or unparsable. That is **not** zero.
    pub review_age_seconds: Option<i64>,
    /// The amount the request would move, in anchor-token raw units.
    pub amount_raw: Option<u128>,
    /// The time the decision was made at.
    pub decided_at: String,
}

impl AdmissionDecision {
    /// The reason a refusal carries, built from the failed rules.
    pub fn refusal_reason(&self) -> String {
        if self.failed_rules.is_empty() {
            return "admission refused without a recorded reason".to_owned();
        }
        self.failed_rules.join(",")
    }

    fn failed(&mut self, rule: AdmissionRule, reason: impl Into<String>) {
        let reason = reason.into();
        self.checks.push(AdmissionCheck {
            rule,
            satisfied: false,
            reason,
        });
        self.failed_rules.push(rule.code().to_owned());
    }

    fn passed(&mut self, rule: AdmissionRule, reason: impl Into<String>) {
        self.checks.push(AdmissionCheck {
            rule,
            satisfied: true,
            reason: reason.into(),
        });
    }
}

/// The facts about the request under judgement.
///
/// These are the values the review layer published. They are read, never
/// re-measured.
#[derive(Debug, Clone, PartialEq)]
pub struct AdmissionCandidate {
    pub loop_key: String,
    /// The chain the request was read under.
    pub chain_id: i64,
    pub request_status: String,
    /// The signed PnL amount as published.
    ///
    /// `None` means the PnL was never measured.
    pub signed_pnl_amount: Option<f64>,
    /// The price limits of each hop, in route order.
    pub hop_sqrt_price_limits: Vec<String>,
    /// The signer the attempt names.
    pub signer_address: Option<String>,
    /// The admission facts the review published.
    pub facts: BroadcastAdmissionFacts,
}

/// Read a stored PnL amount as a **measurement** or as its absence.
///
/// The request row stores the amount in a `REAL NOT NULL` column, so a
/// row always *has* a number in it. That does not mean the number is a
/// measurement: an infinite value is what a producer writes when it
/// had no figure to report, and a `NaN` cannot be stored at all
/// (SQLite turns it into `NULL`, which the `NOT NULL` constraint
/// rejects).
///
/// The distinction matters because the two cases must be refused for
/// **different** reasons. A measured zero is a business outcome; an
/// unmeasured amount is a missing measurement, and it must never be
/// folded into either "zero" or "not matching".
pub fn measured_amount(stored: f64) -> Option<f64> {
    if stored.is_finite() {
        Some(stored)
    } else {
        None
    }
}

/// The clock the freshness rule reads.
///
/// The caller may pass an explicit time so that the decision is
/// reproducible. `None` means the current UTC time.
fn resolve_now(now: Option<&str>) -> String {
    match now {
        Some(value) if !value.trim().is_empty() => value.trim().to_owned(),
        _ => chrono::Utc::now().to_rfc3339(),
    }
}

/// Parse an RFC 3339 stamp, tolerating the shapes the system writes.
fn parse_timestamp(value: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    chrono::DateTime::parse_from_rfc3339(value.trim())
        .ok()
        .map(|parsed| parsed.with_timezone(&chrono::Utc))
}

/// Compare two normalized addresses, case-insensitively.
///
/// The system writes addresses in checksummed form, but a comparison
/// must not depend on that. A malformed address never matches.
fn same_address(left: &str, right: &str) -> bool {
    let left = left.trim().to_lowercase();
    let right = right.trim().to_lowercase();
    !left.is_empty() && left == right
}

/// Judge the request.
///
/// The function **never** fails: a request that cannot be admitted is
/// refused with a reason, not turned into an error. That is what makes
/// the refusal auditable — an error path would leave no trail.
pub fn decide(
    candidate: &AdmissionCandidate,
    context: &BroadcastAdmissionContext,
) -> AdmissionDecision {
    let decided_at = resolve_now(context.now.as_deref());
    let mut decision = AdmissionDecision {
        admitted: false,
        checks: Vec::new(),
        failed_rules: Vec::new(),
        review_age_seconds: None,
        amount_raw: context.amount_raw,
        decided_at: decided_at.clone(),
    };

    // ---- BC-1: the five predicates -------------------------------------
    //
    // `BC-1.evidence_complete` comes first: without the review row there
    // is no evidence to judge, so every other rule would be guessing.

    let facts = &candidate.facts;
    let evidence_complete = !facts.reviewed_at.trim().is_empty();
    if evidence_complete {
        decision.passed(
            AdmissionRule::EvidenceComplete,
            "the review row and its evidence are present",
        );
    } else {
        decision.failed(
            AdmissionRule::EvidenceComplete,
            "no review evidence is attached to this request",
        );
    }

    if facts.review_status.trim() == "completed" {
        decision.passed(
            AdmissionRule::ReviewCompleted,
            "the review completed",
        );
    } else {
        decision.failed(
            AdmissionRule::ReviewCompleted,
            format!(
                "the review status is {:?}, not \"completed\"",
                facts.review_status.trim()
            ),
        );
    }

    match facts.run_ok {
        Some(true) => decision.passed(AdmissionRule::RunOk, "runArbitrage reported ok"),
        Some(false) => decision.failed(
            AdmissionRule::RunOk,
            "runArbitrage reported ok=false",
        ),
        None => decision.failed(
            AdmissionRule::RunOk,
            "runArbitrage did not report an \"ok\" verdict",
        ),
    }

    match facts.run_executable {
        Some(true) => {
            decision.passed(AdmissionRule::RunExecutable, "the route is executable")
        }
        Some(false) => decision.failed(
            AdmissionRule::RunExecutable,
            "runArbitrage reported executable=false",
        ),
        None => decision.failed(
            AdmissionRule::RunExecutable,
            "runArbitrage did not report an \"executable\" verdict",
        ),
    }

    match candidate.signed_pnl_amount {
        Some(amount) if amount.is_finite() => {
            decision.passed(
                AdmissionRule::PnlMeasured,
                format!("the signed PnL was measured at {amount}"),
            );
            if amount > 0.0 {
                decision.passed(
                    AdmissionRule::PnlPositive,
                    format!("the signed PnL {amount} is strictly positive"),
                );
            } else {
                // A measured zero or a measured loss is a **business
                // outcome**, not a defect. It is recorded as such and
                // refuses the broadcast, but it is not an error and it
                // is not a missing measurement.
                decision.failed(
                    AdmissionRule::PnlPositive,
                    format!(
                        "the signed PnL {amount} was measured and is not positive; \
                         this is a business outcome, not a missing measurement"
                    ),
                );
            }
        }
        Some(_) => decision.failed(
            AdmissionRule::PnlMeasured,
            "the signed PnL amount is not a finite number",
        ),
        None => decision.failed(
            AdmissionRule::PnlMeasured,
            "the signed PnL was never measured; it must not be treated as zero",
        ),
    }

    // Freshness.
    //
    // The question is whether the review is still the **current**
    // measurement of its closed loop.  The loop answers it with its own
    // review time point -- the moment it is scheduled at -- so the rule
    // compares the current time with that moment.  No interval is
    // rebuilt here: the helper that schedules the loop already folded
    // the review interval into the moment.
    match parse_timestamp(&facts.reviewed_at) {
        Some(reviewed_at) => {
            let now = parse_timestamp(&decided_at).unwrap_or_else(chrono::Utc::now);
            let age = now.signed_duration_since(reviewed_at).num_seconds();
            decision.review_age_seconds = Some(age);
            let ahead = -age;
            if ahead > MAX_BROADCAST_FUTURE_SKEW_SECONDS {
                decision.failed(
                    AdmissionRule::ReviewFresh,
                    AdmissionRule::ReviewFresh,
                    format!(
                        "the review stamp is {ahead}s in the future, past the \
                         {MAX_BROADCAST_FUTURE_SKEW_SECONDS}s skew allowance"
                    ),
                );
            } else {
                match context
                    .loop_scheduled_at
                    .as_deref()
                    .and_then(parse_timestamp)
                {
                    Some(scheduled) if now < scheduled => {
                        decision.passed(
                            AdmissionRule::ReviewFresh,
                            format!(
                                "the review is still the current measurement of its loop; \
                                 the loop is scheduled at {scheduled}"
                            ),
                        );
                    }
                    Some(scheduled) => {
                        decision.failed(
                            AdmissionRule::ReviewFresh,
                            format!(
                                "the loop was scheduled at {scheduled}, so its review is \
                                 no longer the current measurement of the loop"
                            ),
                        );
                    }
                    None => {
                        decision.failed(
                            AdmissionRule::ReviewFresh,
                            "the loop publishes no review time point, so its review cannot \
                             be proven to be the current measurement of the loop",
                        );
                    }
                }
            }
        }
        None => decision.failed(
            AdmissionRule::ReviewFresh,
            "the review stamp is missing or not an RFC 3339 timestamp",
        ),
    }

    // The per-hop price limits travel with the request. A request
    // without all of them would make the executor fall back to the
    // global V4 price bound, which is not what the review proved.
    let price_limits_complete = candidate.hop_sqrt_price_limits.len() == 3
        && candidate
            .hop_sqrt_price_limits
            .iter()
            .all(|value| !value.trim().is_empty());
    if price_limits_complete {
        decision.passed(
            AdmissionRule::PriceLimitsComplete,
            "the request carries all three per-hop price limits",
        );
    } else {
        decision.failed(
            AdmissionRule::PriceLimitsComplete,
            format!(
                "the request carries {} per-hop price limits, not 3 complete ones",
                candidate.hop_sqrt_price_limits.len()
            ),
        );
    }

    // ---- BC-2: the runtime guardrails ----------------------------------

    match context.policy.chain_id {
        Some(expected) if expected == candidate.chain_id => decision.passed(
            AdmissionRule::ChainMatches,
            format!("the request is on chain {expected}, which the policy governs"),
        ),
        Some(expected) => decision.failed(
            AdmissionRule::ChainMatches,
            format!(
                "the request is on chain {}, but the policy governs chain {expected}",
                candidate.chain_id
            ),
        ),
        None => decision.failed(
            AdmissionRule::ChainMatches,
            "the deployment configured no chain",
        ),
    }

    match (
        context.policy.signer_address.as_deref(),
        candidate.signer_address.as_deref(),
    ) {
        (Some(expected), Some(actual)) if same_address(expected, actual) => decision.passed(
            AdmissionRule::AccountMatches,
            "the attempt names the configured account",
        ),
        (Some(expected), Some(actual)) => decision.failed(
            AdmissionRule::AccountMatches,
            format!(
                "the attempt names {actual}, but the policy authorizes {expected}"
            ),
        ),
        (Some(_), None) => decision.failed(
            AdmissionRule::AccountMatches,
            "the attempt names no account",
        ),
        (None, _) => decision.failed(
            AdmissionRule::AccountMatches,
            "the deployment configured no account",
        ),
    }

    match (context.amount_raw, context.policy.max_amount_raw) {
        (Some(amount), Some(limit)) if amount <= limit => decision.passed(
            AdmissionRule::AmountWithinLimit,
            format!("the amount {amount} is within the limit {limit}"),
        ),
        (Some(amount), Some(limit)) => decision.failed(
            AdmissionRule::AmountWithinLimit,
            format!("the amount {amount} exceeds the limit {limit}"),
        ),
        (Some(_), None) => decision.failed(
            AdmissionRule::AmountWithinLimit,
            "the deployment configured no limit",
        ),
        (None, _) => decision.failed(
            AdmissionRule::AmountWithinLimit,
            "the amount was never measured; it must not be treated as zero",
        ),
    }

    match (
        context.policy.window_start.as_deref(),
        context.policy.window_end.as_deref(),
    ) {
        (Some(start), Some(end)) => {
            let now = parse_timestamp(&decided_at);
            let start = parse_timestamp(start);
            let end = parse_timestamp(end);
            match (now, start, end) {
                (Some(now), Some(start), Some(end)) if start <= now && now <= end => {
                    decision.passed(
                        AdmissionRule::WithinWindow,
                        "the current time is inside the configured window",
                    )
                }
                (Some(now), Some(start), Some(_)) if now < start => decision.failed(
                    AdmissionRule::WithinWindow,
                    format!("the window opens at {start} and it is not open yet"),
                ),
                (Some(_), Some(_), Some(end)) => decision.failed(
                    AdmissionRule::WithinWindow,
                    format!("the window closed at {end}"),
                ),
                _ => decision.failed(
                    AdmissionRule::WithinWindow,
                    "the window bounds are missing or not RFC 3339 timestamps",
                ),
            }
        }
        (None, _) | (_, None) => decision.failed(
            AdmissionRule::WithinWindow,
            "the deployment configured no time window",
        ),
    }

    decision.admitted = decision.failed_rules.is_empty();
    decision
}
