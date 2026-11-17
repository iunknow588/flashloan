//! Freshness and expiry semantics for USDG valuations.
//!
//! Why a separate module
//! --------------------
//!
//! A USDG valuation is an **observation**, not a permanent truth.  The
//! valuation store keeps the timestamp of every observation
//! (`token_usdg_valuations.measured_at`), but until this module existed
//! nothing ever *read* that timestamp back to decide whether the
//! observation could still be relied on.  The gate consumed every stored
//! row as if it had been measured a moment ago.
//!
//! That is a latent fail-open.  Two failure shapes follow from it:
//!
//! * a `no_route` row measured weeks ago kept a stock token *permanently
//!   excluded*, even though the route may have appeared since; and
//! * a `route_found` row measured weeks ago kept a stock token admitted
//!   on a magnitude that no longer reflects the market.
//!
//! Two different ages are needed, because the two consumers of a
//! valuation have genuinely different tolerances:
//!
//! * the **membership** consumer asks "does this token have a USDG path
//!   at all?".  The answer changes slowly, so it may be trusted for a
//!   day.
//! * the **magnitude** consumer asks "what is one unit worth?".  The
//!   answer changes with the market, so it may be trusted for an hour.
//!
//! The fail-closed rule
//! -------------------
//!
//! Every ambiguity resolves to *unknown*, never to a negative:
//!
//! * an observation whose timestamp cannot be parsed is **stale**;
//! * an observation whose timestamp lies in the future is **stale**;
//! * an expired observation is **stale**.
//!
//! "Stale" means the observation may no longer be used as a fact.  It
//! never means "the token has no value" and it never means "the token
//! has value".  A stale `route_found` and a stale `no_route` are the
//! same thing: an unanswered question.
//!
//! The `no_route` approximation boundary
//! --------------------------------------
//!
//! This module deliberately knows about **no approximation at all**.  A
//! local, API-free heuristic that guesses "this token probably has no
//! path" is *not* a real negative, and the whole point of the four-state
//! verdict vocabulary is that a real negative is only ever produced by
//! the upstream service saying so.  If a local approximation is ever
//! adopted, it must carry its own verdict that is classified as unknown
//! here -- see [`ValuationApproximation`], which exists solely to make
//! the boundary explicit and testable.

use serde::Serialize;
use std::collections::BTreeMap;
use std::fmt;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
/// The maximum age of a USDG **membership** observation.
///
/// A membership decision ("this token has no USDG path") is a coarse
/// fact about the token's place in the venue's routing graph.  It changes
/// slowly, and the consequence of being wrong is mild: a token that
/// should have been excluded stays in the library for at most a day, and
/// a token that should have been admitted stays out for at most a day.
///
/// Twenty-four hours.
pub const MEMBERSHIP_MAX_AGE: Duration = Duration::from_secs(24 * 60 * 60);

/// The maximum age of a USDG **magnitude** observation.
///
/// A magnitude decision ("one unit is worth N USDG") is a market
/// observation.  It is used to decide whether a pool can carry a minimum
/// economic position, so an over-stale magnitude could keep a pool in a
/// band it no longer belongs to.  One hour.
pub const MAGNITUDE_MAX_AGE: Duration = Duration::from_secs(60 * 60);

/// How a stored valuation may be used right now.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ValuationFreshness {
    /// The observation is within its tolerance and may be used as a fact.
    Fresh,
    /// The observation is older than its tolerance and may no longer be
    /// used as a fact.  The verdict itself is *not* thereby refuted: a
    /// stale `no_route` is not a positive and a stale `route_found` is
    /// not a negative.  Both are simply unknown now.
    Stale,
    /// The observation carries no usable timestamp.  It is treated
    /// exactly like a stale observation: fail closed.
    UnverifiableTimestamp,
}

impl ValuationFreshness {
    /// Whether the observation may still be used as a fact.
    pub fn is_usable(&self) -> bool {
        matches!(self, ValuationFreshness::Fresh)
    }

    /// Whether the observation has to be treated as unknown.
    ///
    /// This is the same as "not usable", and it is the question every
    /// consumer must ask.  A stale or unverifiable observation is never
    /// allowed to become a negative.
    pub fn is_unknown(&self) -> bool {
        !self.is_usable()
    }

    /// Stable machine-readable token for the state.
    pub fn as_str(&self) -> &'static str {
        match self {
            ValuationFreshness::Fresh => "fresh",
            ValuationFreshness::Stale => "stale",
            ValuationFreshness::UnverifiableTimestamp => "unverifiable_timestamp",
        }
    }
}

impl fmt::Display for ValuationFreshness {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}
/// The freshness verdict of one stored valuation.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ValuationFreshnessVerdict {
    /// The state of the observation.
    pub freshness: ValuationFreshness,
    /// The age of the observation in seconds, when it could be computed.
    pub age_seconds: Option<i64>,
    /// The tolerance the age was compared against, in seconds.
    pub max_age_seconds: u64,
    /// Why the observation was rejected, when it was.
    pub reason: Option<String>,
}

/// Why the observation timestamp could not be used.
const REASON_UNPARSEABLE: &str = "valuation timestamp could not be parsed";
/// Why the observation timestamp is in the future.
const REASON_FUTURE: &str = "valuation timestamp lies in the future";
/// Why the observation is simply too old.
const REASON_EXPIRED: &str = "valuation observation is older than its accepted maximum age";
/// Why there is no timestamp at all.
const REASON_MISSING: &str = "valuation observation carries no timestamp";

/// What a consumer of a valuation wants to know about it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ValuationUse {
    /// "Does this token have a USDG path at all?"  Membership.
    Membership,
    /// "What is one unit worth in USDG?"  Magnitude.
    Magnitude,
}

impl ValuationUse {
    /// The tolerance that applies to this use.
    pub fn max_age(&self) -> Duration {
        match self {
            ValuationUse::Membership => MEMBERSHIP_MAX_AGE,
            ValuationUse::Magnitude => MAGNITUDE_MAX_AGE,
        }
    }

    /// Stable machine-readable token for the use.
    pub fn as_str(&self) -> &'static str {
        match self {
            ValuationUse::Membership => "membership",
            ValuationUse::Magnitude => "magnitude",
        }
    }
}
/// Parses an RFC-3339 timestamp into seconds since the Unix epoch.
///
/// The valuation store writes `chrono::Utc::now().to_rfc3339()`, which is
/// an RFC-3339 timestamp with an optional fractional part and either a
/// `Z` suffix or a numeric offset.  All of those forms are accepted.
///
/// A timestamp that cannot be parsed yields `None`, which the callers
/// turn into [`ValuationFreshness::UnverifiableTimestamp`].  The parser
/// deliberately performs its own arithmetic instead of asking SQLite to
/// do it: `datetime()` rewrites RFC-3339 into a space-separated form that
/// the caller then has to re-parse, and it silently truncates the
/// fractional part.
fn parse_rfc3339_seconds(text: &str) -> Option<i64> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return None;
    }
    // Split the date part from the time part.
    let (date_part, rest) = trimmed.split_once(['T', 't', ' '])?;
    let mut date_fields = date_part.split('-');
    let year: i64 = date_fields.next()?.parse().ok()?;
    let month: i64 = date_fields.next()?.parse().ok()?;
    let day: i64 = date_fields.next()?.parse().ok()?;
    if date_fields.next().is_some() {
        return None;
    }
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }

    // Split the time part from the zone designator.
    let (time_part, zone) = match rest.find(['+', '-', 'Z', 'z']) {
        Some(index) => (&rest[..index], &rest[index..]),
        None => (rest, ""),
    };
    let mut time_fields = time_part.split(':');
    let hour: i64 = time_fields.next()?.parse().ok()?;
    let minute: i64 = time_fields.next()?.parse().ok()?;
    let second_field = time_fields.next().unwrap_or("0");
    // The fractional part is truncated towards zero: a sub-second
    // difference never changes a freshness decision, because every
    // tolerance in this module is measured in whole seconds.
    let second_text = second_field
        .split_once('.')
        .map_or(second_field, |(s, _)| s);
    let second: i64 = second_text.parse().ok()?;
    if time_fields.next().is_some() {
        return None;
    }
    if !(0..=23).contains(&hour) || !(0..=59).contains(&minute) || !(0..=60).contains(&second) {
        return None;
    }

    // The fractional part is deliberately ignored: a sub-second difference
    // never changes a freshness decision, because every tolerance in this
    // module is measured in whole seconds.

    let offset_seconds: i64 = match zone.as_bytes().first() {
        Some(b'Z') | Some(b'z') => {
            if zone.len() != 1 {
                return None;
            }
            0
        }
        Some(sign @ (b'+' | b'-')) => {
            let digits = &zone[1..];
            let (offset_hours, offset_minutes) = match digits.split_once(':') {
                Some((hours, minutes)) => (hours, minutes),
                None if digits.len() == 4 => (&digits[..2], &digits[2..]),
                None => return None,
            };
            let hours: i64 = offset_hours.parse().ok()?;
            let minutes: i64 = offset_minutes.parse().ok()?;
            if !(0..=23).contains(&hours) || !(0..=59).contains(&minutes) {
                return None;
            }
            let magnitude = hours * 3600 + minutes * 60;
            if *sign == b'+' {
                magnitude
            } else {
                -magnitude
            }
        }
        _ => return None,
    };

    let days = days_from_civil(year, month, day)?;
    let seconds = days * 86_400 + hour * 3600 + minute * 60 + second - offset_seconds;
    Some(seconds)
}
/// Days since 1970-01-01 for a proleptic Gregorian date.
///
/// This is Howard Hinnant's `days_from_civil`, with the era shift
/// applied so that the result is correct for dates before 1970 as well.
fn days_from_civil(year: i64, month: i64, day: i64) -> Option<i64> {
    if !(1..=12).contains(&month) {
        return None;
    }
    if day < 1 || day > days_in_month(year, month) {
        return None;
    }
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let year_of_era = year - era * 400;
    let month_position = if month > 2 { month - 3 } else { month + 9 };
    let day_of_year = (153 * month_position + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    Some(era * 146_097 + day_of_era - 719_468)
}

/// The number of days in a month of a proleptic Gregorian year.
fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
}

/// The current time in seconds since the Unix epoch.
///
/// The function is public because the store audit has to evaluate a whole
/// table against one single instant, and it has to be able to name that
/// instant in its report.
pub fn current_epoch_seconds() -> i64 {
    now_seconds()
}

/// The current time in seconds since the Unix epoch.
fn now_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs() as i64)
        .unwrap_or(0)
}
/// Decides whether one stored valuation may still be used as a fact.
///
/// `measured_at` is the raw timestamp stored next to the observation.
/// `now_seconds` is injectable so that the decision is reproducible in
/// a test and so that a caller can evaluate a whole table against one
/// single instant.
pub fn evaluate_freshness(
    measured_at: Option<&str>,
    use_kind: ValuationUse,
    now_seconds: i64,
) -> ValuationFreshnessVerdict {
    let max_age = use_kind.max_age();
    let max_age_seconds = max_age.as_secs();
    let Some(text) = measured_at else {
        return ValuationFreshnessVerdict {
            freshness: ValuationFreshness::UnverifiableTimestamp,
            age_seconds: None,
            max_age_seconds,
            reason: Some(REASON_MISSING.to_owned()),
        };
    };
    let Some(observed) = parse_rfc3339_seconds(text) else {
        return ValuationFreshnessVerdict {
            freshness: ValuationFreshness::UnverifiableTimestamp,
            age_seconds: None,
            max_age_seconds,
            reason: Some(REASON_UNPARSEABLE.to_owned()),
        };
    };
    let age = now_seconds - observed;
    if age < 0 {
        return ValuationFreshnessVerdict {
            freshness: ValuationFreshness::Stale,
            age_seconds: Some(age),
            max_age_seconds,
            reason: Some(REASON_FUTURE.to_owned()),
        };
    }
    if age as u64 > max_age_seconds {
        return ValuationFreshnessVerdict {
            freshness: ValuationFreshness::Stale,
            age_seconds: Some(age),
            max_age_seconds,
            reason: Some(REASON_EXPIRED.to_owned()),
        };
    }
    ValuationFreshnessVerdict {
        freshness: ValuationFreshness::Fresh,
        age_seconds: Some(age),
        max_age_seconds,
        reason: None,
    }
}

/// Evaluates the freshness of one stored valuation against the current
/// wall clock.
pub fn evaluate_freshness_now(
    measured_at: Option<&str>,
    use_kind: ValuationUse,
) -> ValuationFreshnessVerdict {
    evaluate_freshness(measured_at, use_kind, now_seconds())
}
/// One row the expiry policy rejected.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StaleValuation {
    /// The token address, lowercase.
    pub token_address: String,
    /// The verdict that was stored.
    pub stored_verdict: String,
    /// The age of the observation, in seconds.
    pub age_seconds: Option<i64>,
    /// The tolerance it was compared against, in seconds.
    pub max_age_seconds: u64,
    /// Why it was rejected.
    pub reason: String,
}

/// The outcome of applying the expiry policy to a whole valuation table.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ValuationExpiryReport {
    /// The network and chain the report covers.
    pub network: String,
    /// The chain id the report covers.
    pub chain_id: i64,
    /// The instant the report was evaluated against, in seconds since the
    /// Unix epoch.
    pub evaluated_at_epoch_seconds: i64,
    /// The tolerance applied to membership decisions, in seconds.
    pub membership_max_age_seconds: u64,
    /// The tolerance applied to magnitude decisions, in seconds.
    pub magnitude_max_age_seconds: u64,
    /// How many rows the store holds.
    pub row_count: usize,
    /// How many rows are fresh for a membership decision.
    pub membership_fresh_count: usize,
    /// How many rows are stale for a membership decision.
    pub membership_stale_count: usize,
    /// How many rows cannot be timestamp-verified.
    pub membership_unverifiable_count: usize,
    /// How many rows are fresh for a magnitude decision.
    pub magnitude_fresh_count: usize,
    /// How many rows are stale for a magnitude decision.
    pub magnitude_stale_count: usize,
    /// How many rows cannot be timestamp-verified.
    pub magnitude_unverifiable_count: usize,
    /// The rows that are stale for the membership decision, by token.
    pub stale_membership_tokens: BTreeMap<String, StaleValuation>,
    /// The rows that are stale for the magnitude decision, by token.
    pub stale_magnitude_tokens: BTreeMap<String, StaleValuation>,
    /// How many stored rows carry an *unknown* verdict.
    ///
    /// An unknown row is neither a negative nor a positive, and it can
    /// never be promoted into one by the expiry policy.
    pub unknown_verdict_count: usize,
    /// How many stored rows carry a real negative verdict.
    pub negative_verdict_count: usize,
    /// How many stored rows carry a positive verdict.
    pub positive_verdict_count: usize,
}
/// Evaluates the expiry policy over a set of stored valuations.
///
/// The report is the machine-readable answer to "which stored USDG
/// observations may still be used as facts, and which have expired?".
/// It never mutates anything.
///
/// `rows` carries `(token_address, verdict, measured_at)`.
pub fn evaluate_stored_valuations(
    network: &str,
    chain_id: i64,
    rows: &[(String, String, String)],
    now_seconds: i64,
) -> ValuationExpiryReport {
    let mut report = ValuationExpiryReport {
        network: network.to_owned(),
        chain_id,
        evaluated_at_epoch_seconds: now_seconds,
        membership_max_age_seconds: MEMBERSHIP_MAX_AGE.as_secs(),
        magnitude_max_age_seconds: MAGNITUDE_MAX_AGE.as_secs(),
        row_count: rows.len(),
        membership_fresh_count: 0,
        membership_stale_count: 0,
        membership_unverifiable_count: 0,
        magnitude_fresh_count: 0,
        magnitude_stale_count: 0,
        magnitude_unverifiable_count: 0,
        stale_membership_tokens: BTreeMap::new(),
        stale_magnitude_tokens: BTreeMap::new(),
        unknown_verdict_count: 0,
        negative_verdict_count: 0,
        positive_verdict_count: 0,
    };

    for (address, verdict, measured_at) in rows {
        match verdict.as_str() {
            "route_found" => report.positive_verdict_count += 1,
            "no_route" => report.negative_verdict_count += 1,
            _ => report.unknown_verdict_count += 1,
        }

        let membership = evaluate_freshness(
            Some(measured_at.as_str()),
            ValuationUse::Membership,
            now_seconds,
        );
        match membership.freshness {
            ValuationFreshness::Fresh => report.membership_fresh_count += 1,
            ValuationFreshness::Stale => report.membership_stale_count += 1,
            ValuationFreshness::UnverifiableTimestamp => report.membership_unverifiable_count += 1,
        }
        if !membership.freshness.is_usable() {
            report.stale_membership_tokens.insert(
                address.clone(),
                StaleValuation {
                    token_address: address.clone(),
                    stored_verdict: verdict.clone(),
                    age_seconds: membership.age_seconds,
                    max_age_seconds: membership.max_age_seconds,
                    reason: membership
                        .reason
                        .clone()
                        .unwrap_or_else(|| "expired".to_owned()),
                },
            );
        }

        let magnitude = evaluate_freshness(
            Some(measured_at.as_str()),
            ValuationUse::Magnitude,
            now_seconds,
        );
        match magnitude.freshness {
            ValuationFreshness::Fresh => report.magnitude_fresh_count += 1,
            ValuationFreshness::Stale => report.magnitude_stale_count += 1,
            ValuationFreshness::UnverifiableTimestamp => report.magnitude_unverifiable_count += 1,
        }
        if !magnitude.freshness.is_usable() {
            report.stale_magnitude_tokens.insert(
                address.clone(),
                StaleValuation {
                    token_address: address.clone(),
                    stored_verdict: verdict.clone(),
                    age_seconds: magnitude.age_seconds,
                    max_age_seconds: magnitude.max_age_seconds,
                    reason: magnitude
                        .reason
                        .clone()
                        .unwrap_or_else(|| "expired".to_owned()),
                },
            );
        }
    }

    report
}
/// The decision the expiry policy reaches for one stored verdict.
///
/// This is the single place where "is this observation still usable?"
/// turns into "what may the consumer conclude?".  The rule is simple
/// and deliberately asymmetric:
///
/// * a **fresh** observation keeps its meaning;
/// * a **stale or unverifiable** observation means *unknown*, no matter
///   what it said.
///
/// A stale `no_route` does **not** stay a negative.  A stale
/// `route_found` does **not** stay a positive.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectiveVerdict {
    /// The token has a live USDG path and the observation is fresh.
    Positive,
    /// The token has no USDG path and the observation is fresh.
    Negative,
    /// The observation is expired or unverifiable: the token's state is
    /// unknown and no membership conclusion may be drawn from it.
    UnknownExpired,
    /// The stored verdict was itself unknown (a transport failure).
    UnknownProbe,
}

impl EffectiveVerdict {
    /// Whether the verdict is a genuine negative.
    ///
    /// Only a **fresh** `no_route` is a negative.  This is the property
    /// that keeps an expired observation from ever removing a token.
    pub fn is_negative(&self) -> bool {
        matches!(self, EffectiveVerdict::Negative)
    }

    /// Whether the verdict is a genuine positive.
    pub fn is_positive(&self) -> bool {
        matches!(self, EffectiveVerdict::Positive)
    }

    /// Whether the verdict leaves the token's state unknown.
    pub fn is_unknown(&self) -> bool {
        matches!(
            self,
            EffectiveVerdict::UnknownExpired | EffectiveVerdict::UnknownProbe
        )
    }

    /// Stable machine-readable token for the verdict.
    pub fn as_str(&self) -> &'static str {
        match self {
            EffectiveVerdict::Positive => "positive",
            EffectiveVerdict::Negative => "negative",
            EffectiveVerdict::UnknownExpired => "unknown_expired",
            EffectiveVerdict::UnknownProbe => "unknown_probe",
        }
    }
}

/// Combines a stored verdict with its freshness into the effective
/// verdict a consumer may act on.
pub fn effective_verdict(stored_verdict: &str, freshness: ValuationFreshness) -> EffectiveVerdict {
    if !freshness.is_usable() {
        return EffectiveVerdict::UnknownExpired;
    }
    match stored_verdict {
        "route_found" => EffectiveVerdict::Positive,
        "no_route" => EffectiveVerdict::Negative,
        _ => EffectiveVerdict::UnknownProbe,
    }
}
/// The boundary of the local `no_route` approximation.
///
/// **No local approximation is adopted.**  The verdict vocabulary of
/// this crate has exactly one producer for a real negative: the upstream
/// routing service saying so.  A local, API-free heuristic cannot
/// produce a real negative, because "there is probably no path" and
/// "there is no path" are different statements and only the first one can
/// be established without asking.
///
/// This enum exists so that the boundary is *code* and not only prose.
/// The variant [`ValuationApproximation::Rejected`] is the decision,
/// and it is the default for every candidate: an approximation may only
/// ever be recorded as a screening hint in an operator-facing report, it
/// may never be written to `token_usdg_valuations` with a `no_route`
/// verdict, and it may never move a membership.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ValuationApproximation {
    /// The local approximation is not adopted.  Any candidate heuristic
    /// is refused, and the only producer of a real negative remains the
    /// upstream service.
    Rejected,
}

impl ValuationApproximation {
    /// Stable machine-readable token for the decision.
    pub fn as_str(&self) -> &'static str {
        match self {
            ValuationApproximation::Rejected => "rejected",
        }
    }
}

impl Default for ValuationApproximation {
    fn default() -> Self {
        ValuationApproximation::Rejected
    }
}

/// Whether a candidate local approximation may be promoted to a real
/// negative verdict.
///
/// The answer is always "no", and the function exists so that the
/// refusal is enforced at the one place where a verdict would be
/// written.  It takes the approximation kind so that a future
/// implementation cannot quietly widen the question.
pub fn approximation_may_promote_to_negative(_kind: &str) -> bool {
    false
}
#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_800_000_000; // 2027-01-15T08:00:00Z

    fn at(offset_seconds: i64) -> String {
        // Build an RFC-3339 timestamp `offset_seconds` before NOW.
        let seconds = NOW - offset_seconds;
        let days = seconds.div_euclid(86_400);
        let remainder = seconds.rem_euclid(86_400);
        let (year, month, day) = civil_from_days(days);
        format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}+00:00",
            year,
            month,
            day,
            remainder / 3600,
            (remainder % 3600) / 60,
            remainder % 60
        )
    }

    /// The inverse of `days_from_civil`.
    fn civil_from_days(days: i64) -> (i64, i64, i64) {
        let z = days + 719_468;
        let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
        let day_of_era = z - era * 146_097;
        let year_of_era =
            (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
        let year = year_of_era + era * 400;
        let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
        let month_position = (5 * day_of_year + 2) / 153;
        let day = day_of_year - (153 * month_position + 2) / 5 + 1;
        let month = if month_position < 10 {
            month_position + 3
        } else {
            month_position - 9
        };
        let year = if month <= 2 { year + 1 } else { year };
        (year, month, day)
    }

    #[test]
    fn a_fresh_membership_observation_is_usable() {
        let verdict = evaluate_freshness(Some(&at(60)), ValuationUse::Membership, NOW);
        assert_eq!(verdict.freshness, ValuationFreshness::Fresh);
        assert!(verdict.freshness.is_usable());
        assert_eq!(verdict.age_seconds, Some(60));
        assert_eq!(verdict.max_age_seconds, MEMBERSHIP_MAX_AGE.as_secs());
    }

    #[test]
    fn membership_survives_for_a_full_day_and_expires_after() {
        let almost = evaluate_freshness(
            Some(&at(MEMBERSHIP_MAX_AGE.as_secs() as i64 - 1)),
            ValuationUse::Membership,
            NOW,
        );
        assert_eq!(almost.freshness, ValuationFreshness::Fresh);
        let expired = evaluate_freshness(
            Some(&at(MEMBERSHIP_MAX_AGE.as_secs() as i64 + 1)),
            ValuationUse::Membership,
            NOW,
        );
        assert_eq!(expired.freshness, ValuationFreshness::Stale);
        assert_eq!(expired.reason.as_deref(), Some(REASON_EXPIRED));
    }

    #[test]
    fn magnitude_expires_far_sooner_than_membership() {
        // Two hours old: fresh for membership, stale for magnitude.
        let verdict = evaluate_freshness(Some(&at(7200)), ValuationUse::Membership, NOW);
        assert_eq!(verdict.freshness, ValuationFreshness::Fresh);
        let verdict = evaluate_freshness(Some(&at(7200)), ValuationUse::Magnitude, NOW);
        assert_eq!(verdict.freshness, ValuationFreshness::Stale);
    }

    #[test]
    fn an_unparseable_timestamp_is_unverifiable_and_never_fresh() {
        for text in [
            "",
            "   ",
            "not a timestamp",
            "2026-13-01T00:00:00Z",
            "2026-01-99",
        ] {
            let verdict = evaluate_freshness(Some(text), ValuationUse::Membership, NOW);
            assert_eq!(
                verdict.freshness,
                ValuationFreshness::UnverifiableTimestamp,
                "text {text:?} must not be usable"
            );
            assert!(verdict.freshness.is_unknown());
        }
        let verdict = evaluate_freshness(None, ValuationUse::Membership, NOW);
        assert_eq!(verdict.freshness, ValuationFreshness::UnverifiableTimestamp);
        assert_eq!(verdict.reason.as_deref(), Some(REASON_MISSING));
    }

    #[test]
    fn a_future_timestamp_is_stale_not_fresh() {
        let verdict = evaluate_freshness(Some(&at(-120)), ValuationUse::Membership, NOW);
        assert_eq!(verdict.freshness, ValuationFreshness::Stale);
        assert_eq!(verdict.reason.as_deref(), Some(REASON_FUTURE));
    }

    #[test]
    fn rfc3339_forms_are_all_parsed() {
        // The same instant expressed four ways.
        let zulu = "2026-11-12T05:51:13Z";
        let offset = "2026-11-12T13:51:13+08:00";
        let fractional = "2026-11-12T05:51:13.215896200Z";
        let numeric_offset = "2026-11-12T04:51:13-01:00";
        let expected = parse_rfc3339_seconds(zulu).expect("zulu must parse");
        assert_eq!(parse_rfc3339_seconds(offset), Some(expected));
        assert_eq!(parse_rfc3339_seconds(fractional), Some(expected));
        assert_eq!(parse_rfc3339_seconds(numeric_offset), Some(expected));
    }

    #[test]
    fn a_stale_negative_never_remains_a_negative() {
        let freshness = evaluate_freshness(
            Some(&at(MEMBERSHIP_MAX_AGE.as_secs() as i64 + 60)),
            ValuationUse::Membership,
            NOW,
        );
        let effective = effective_verdict("no_route", freshness.freshness);
        assert_eq!(effective, EffectiveVerdict::UnknownExpired);
        assert!(!effective.is_negative());
        assert!(effective.is_unknown());
    }

    #[test]
    fn a_stale_positive_never_remains_a_positive() {
        let freshness = evaluate_freshness(
            Some(&at(MAGNITUDE_MAX_AGE.as_secs() as i64 + 60)),
            ValuationUse::Magnitude,
            NOW,
        );
        let effective = effective_verdict("route_found", freshness.freshness);
        assert_eq!(effective, EffectiveVerdict::UnknownExpired);
        assert!(!effective.is_positive());
    }

    #[test]
    fn a_fresh_negative_is_a_real_negative() {
        let freshness = evaluate_freshness(Some(&at(120)), ValuationUse::Membership, NOW);
        let effective = effective_verdict("no_route", freshness.freshness);
        assert_eq!(effective, EffectiveVerdict::Negative);
        assert!(effective.is_negative());
    }

    #[test]
    fn a_fresh_probe_failure_stays_unknown() {
        let freshness = evaluate_freshness(Some(&at(120)), ValuationUse::Membership, NOW);
        for stored in ["api_unavailable", "api_error"] {
            let effective = effective_verdict(stored, freshness.freshness);
            assert_eq!(effective, EffectiveVerdict::UnknownProbe);
            assert!(effective.is_unknown());
            assert!(!effective.is_negative());
        }
    }

    #[test]
    fn the_table_report_separates_fresh_stale_and_unverifiable() {
        let rows = vec![
            ("0xaaa".to_owned(), "route_found".to_owned(), at(60)),
            ("0xbbb".to_owned(), "no_route".to_owned(), at(7200)),
            (
                "0xccc".to_owned(),
                "route_found".to_owned(),
                at(MEMBERSHIP_MAX_AGE.as_secs() as i64 + 60),
            ),
            (
                "0xddd".to_owned(),
                "api_error".to_owned(),
                "garbage".to_owned(),
            ),
        ];
        let report = evaluate_stored_valuations("robinhood", 4663, &rows, NOW);
        assert_eq!(report.row_count, 4);
        assert_eq!(report.membership_fresh_count, 2);
        assert_eq!(report.membership_stale_count, 1);
        assert_eq!(report.membership_unverifiable_count, 1);
        assert_eq!(report.magnitude_fresh_count, 1);
        assert_eq!(report.magnitude_stale_count, 2);
        assert_eq!(report.magnitude_unverifiable_count, 1);
        assert_eq!(report.positive_verdict_count, 2);
        assert_eq!(report.negative_verdict_count, 1);
        assert_eq!(report.unknown_verdict_count, 1);
        assert!(report.stale_membership_tokens.contains_key("0xccc"));
        assert!(!report.stale_membership_tokens.contains_key("0xbbb"));
    }

    #[test]
    fn the_local_approximation_is_rejected() {
        assert_eq!(
            ValuationApproximation::default(),
            ValuationApproximation::Rejected
        );
        for kind in [
            "liquidity_screen",
            "symbol_match",
            "pool_absence",
            "anything",
        ] {
            assert!(
                !approximation_may_promote_to_negative(kind),
                "{kind} must never be promoted to a negative"
            );
        }
    }
}
