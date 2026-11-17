//! Candidate-pool admission verdicts owned by the Rust kernel.
//!
//! The runtime kernel is the only component allowed to decide whether a
//! measured pool may enter the candidate set. Python consumes the verdict
//! through the bridge and never carries a private copy of the thresholds.
//!
//! The thresholds are read from the shared policy document
//! (`flashloan/config/depth_policy.json`) through the `pool-manager` crate,
//! so the kernel, the pool manager and the healer can never disagree about
//! which pools are deep enough.
//!
//! Two independent signals must agree before a pool is admitted:
//!
//! 1. the pool manager's own depth verdict (`deep` / `liquid` / `medium`),
//! 2. the decimals-normalised binding-side capacity, which has to reach the
//!    shared admission floor (`minCandidateBindingCapacity`).
//!
//! A pool whose verdict or capacity is missing is a **coverage gap** of the
//! store, not a statement about the market, so it is not rejected here.

use std::collections::BTreeMap;

use pool_manager::depth_config::{
    depth_policy, DEPTH_BAND_DEEP_CAPACITY, DEPTH_BAND_MEDIUM_CAPACITY,
    DEPTH_BAND_SHALLOW_CAPACITY, MIN_CANDIDATE_BINDING_CAPACITY,
};
use serde_json::Value;

/// The depth verdict labels that may enter the candidate set.
const ADMITTED_VERDICTS: [&str; 3] = ["deep", "liquid", "medium"];

/// One pool's admission verdict as published by the kernel.
#[derive(Clone, Debug, PartialEq)]
pub struct AdmissionVerdict {
    /// Whether the pool may enter the candidate set.
    pub admitted: bool,
    /// The pool manager's depth verdict, when one was measured.
    pub depth_status: Option<String>,
    /// The decimals-normalised binding-side capacity, when one was measured.
    pub capacity_binding_raw: Option<f64>,
    /// The depth band derived from the measured capacity.
    pub depth_band: Option<String>,
    /// The reason the verdict was reached, for diagnostics.
    pub reason: String,
}

fn normalize_verdict(value: Option<&str>) -> Option<String> {
    value
        .map(|text| text.trim().to_ascii_lowercase())
        .filter(|text| !text.is_empty())
}

/// Return the decimals-normalised binding-side capacity of a pool row.
///
/// The capacity is read from the depth-band document the pool manager wrote.
/// A row that carries no capacity document is a coverage gap.
pub fn capacity_binding_raw(depth_band_json: Option<&str>) -> Option<f64> {
    let raw = depth_band_json?.trim();
    if raw.is_empty() {
        return None;
    }
    let payload: Value = serde_json::from_str(raw).ok()?;
    let object = payload.as_object()?;
    for key in ["capacityBindingRaw", "capacity_binding_raw"] {
        if let Some(value) = object.get(key).and_then(Value::as_f64) {
            if value.is_finite() {
                return Some(value);
            }
        }
    }
    None
}

/// Classify a decimals-normalised binding-side capacity into a depth band.
///
/// The boundary operator is `>=` and matches the pool manager exactly.
pub fn depth_band_for_capacity(capacity: Option<f64>) -> Option<String> {
    let value = capacity?;
    if !value.is_finite() {
        return None;
    }
    let band = if value >= DEPTH_BAND_DEEP_CAPACITY {
        "deep"
    } else if value >= DEPTH_BAND_MEDIUM_CAPACITY {
        "medium"
    } else if value >= DEPTH_BAND_SHALLOW_CAPACITY {
        "shallow"
    } else {
        "dust"
    };
    Some(band.to_string())
}

/// Whether the measured depth verdict admits a pool into the candidate set.
///
/// A missing verdict is a coverage gap of the store and is not a rejection.
pub fn depth_verdict_admits_pool(depth_status: Option<&str>) -> bool {
    match normalize_verdict(depth_status) {
        Some(verdict) => ADMITTED_VERDICTS.contains(&verdict.as_str()),
        None => true,
    }
}

/// Decide whether one measured pool may enter the candidate set.
///
/// The verdict and the capacity must agree. A pool that the pool manager
/// measured as `deep` but whose capacity is below the shared admission floor
/// is rejected: the label alone never overrides the measured capacity.
pub fn admit_candidate_pool(
    depth_status: Option<&str>,
    depth_band_json: Option<&str>,
) -> AdmissionVerdict {
    admit_candidate_pool_with_liquidity(depth_status, depth_band_json, None)
}

/// Decide whether one measured pool may enter the candidate set.
///
/// The explicit liquidity flag is an observation of the pool manager: a
/// pool that reported zero liquidity cannot carry a position, whatever its
/// depth label or capacity says.
pub fn admit_candidate_pool_with_liquidity(
    depth_status: Option<&str>,
    depth_band_json: Option<&str>,
    has_liquidity: Option<bool>,
) -> AdmissionVerdict {
    let verdict = normalize_verdict(depth_status);
    let capacity = capacity_binding_raw(depth_band_json);
    let band = depth_band_for_capacity(capacity);

    let verdict_admits = depth_verdict_admits_pool(depth_status);
    let capacity_measured = capacity.is_some();
    let capacity_admits = match capacity {
        Some(value) => value >= MIN_CANDIDATE_BINDING_CAPACITY,
        None => true,
    };

    let liquidity_rejects = has_liquidity == Some(false);

    let (admitted, reason) = if liquidity_rejects {
        (false, "explicit_zero_liquidity".to_string())
    } else if !verdict_admits {
        (
            false,
            format!(
                "depth_verdict_rejected:{}",
                verdict.clone().unwrap_or_else(|| "unknown".to_string())
            ),
        )
    } else if capacity_measured && !capacity_admits {
        (
            false,
            format!(
                "binding_capacity_below_floor:{:.8}<{:.8}",
                capacity.unwrap_or(0.0),
                MIN_CANDIDATE_BINDING_CAPACITY
            ),
        )
    } else if !capacity_measured {
        (true, "capacity_coverage_gap".to_string())
    } else {
        (true, "admitted".to_string())
    };

    AdmissionVerdict {
        admitted,
        depth_status: verdict,
        capacity_binding_raw: capacity,
        depth_band: band,
        reason,
    }
}

/// Return the shared admission floor, read from the shared policy document.
///
/// The kernel publishes the floor so every consumer observes the same value.
pub fn shared_admission_floor() -> Result<f64, String> {
    let policy = depth_policy()?;
    Ok(policy.min_candidate_binding_capacity)
}

/// Return the admission verdicts for a batch of measured pools.
///
/// The map is keyed by pool id so a caller can publish the verdict next to
/// the pool row without repeating the threshold logic.
pub fn admission_verdicts(
    rows: &BTreeMap<String, (Option<String>, Option<String>, Option<bool>)>,
) -> Result<BTreeMap<String, AdmissionVerdict>, String> {
    // Fail loudly when the shared policy cannot be read: a kernel that
    // silently fell back to a private threshold would break the single
    // source of truth the whole system relies on.
    let _ = shared_admission_floor()?;
    let mut verdicts = BTreeMap::new();
    for (pool_id, (depth_status, depth_band_json, has_liquidity)) in rows {
        verdicts.insert(
            pool_id.clone(),
            admit_candidate_pool_with_liquidity(
                depth_status.as_deref(),
                depth_band_json.as_deref(),
                *has_liquidity,
            ),
        );
    }
    Ok(verdicts)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_depth_verdict_decides_whether_a_pool_enters_a_scope() {
        assert!(depth_verdict_admits_pool(Some("deep")));
        assert!(depth_verdict_admits_pool(Some("liquid")));
        assert!(depth_verdict_admits_pool(Some("medium")));
        assert!(!depth_verdict_admits_pool(Some("shallow")));
        assert!(!depth_verdict_admits_pool(Some("thin")));
        assert!(!depth_verdict_admits_pool(Some("dust")));
        // A store that never measured a verdict is a coverage gap.
        assert!(depth_verdict_admits_pool(None));
        assert!(depth_verdict_admits_pool(Some("")));
    }

    #[test]
    fn the_shared_capacity_floor_rejects_a_dust_capacity() {
        let band = format!(
            "{{\"capacityBindingRaw\":{}}}",
            MIN_CANDIDATE_BINDING_CAPACITY / 10.0
        );
        let verdict = admit_candidate_pool(Some("deep"), Some(&band));
        assert!(!verdict.admitted);
        assert!(verdict.reason.starts_with("binding_capacity_below_floor"));
    }

    #[test]
    fn the_shared_capacity_floor_accepts_a_deep_capacity() {
        let band = format!(
            "{{\"capacityBindingRaw\":{}}}",
            DEPTH_BAND_DEEP_CAPACITY * 10.0
        );
        let verdict = admit_candidate_pool(Some("deep"), Some(&band));
        assert!(verdict.admitted);
        assert_eq!(verdict.depth_band.as_deref(), Some("deep"));
    }

    #[test]
    fn a_rejected_verdict_is_never_overridden_by_the_capacity() {
        let band = format!(
            "{{\"capacityBindingRaw\":{}}}",
            DEPTH_BAND_DEEP_CAPACITY * 10.0
        );
        let verdict = admit_candidate_pool(Some("thin"), Some(&band));
        assert!(!verdict.admitted);
        assert!(verdict.reason.starts_with("depth_verdict_rejected"));
    }

    #[test]
    fn an_explicit_zero_liquidity_rejects_the_pool() {
        let band = format!(
            "{{\"capacityBindingRaw\":{}}}",
            DEPTH_BAND_DEEP_CAPACITY * 10.0
        );
        let verdict =
            admit_candidate_pool_with_liquidity(Some("deep"), Some(&band), Some(false));
        assert!(!verdict.admitted);
        assert_eq!(verdict.reason, "explicit_zero_liquidity");
    }

    #[test]
    fn a_missing_capacity_is_a_coverage_gap_and_not_a_rejection() {
        let verdict = admit_candidate_pool(Some("deep"), None);
        assert!(verdict.admitted);
        assert_eq!(verdict.reason, "capacity_coverage_gap");
    }
}
