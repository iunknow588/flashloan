//! Shared depth and capacity configuration.
//!
//! Every admission, capacity and closed-loop decision in the Rust toolchain
//! reads its thresholds from this module, so the candidate-pool admission and
//! the closed-loop admission can never disagree about which pools are deep
//! enough.
//!
//! The thresholds are expressed in the pool's own tokens. A USD-denominated
//! threshold cannot express tradable value inside the arbitrage environment,
//! so no USD value appears here.
//!
//! # Single source of truth
//!
//! The values live in `flashloan/config/depth_policy.json`, which the Python
//! orchestrator reads as well. [`depth_policy`] loads that file and
//! [`assert_depth_policy_matches_file`] asserts that the constants below
//! still agree with it. The assertion runs at start-up, so a stale constant
//! fails the tool loudly instead of silently producing a different verdict
//! than the orchestrator.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Location of the shared depth policy, relative to the repository root.
pub const DEPTH_POLICY_RELATIVE_PATH: &str = "flashloan/config/depth_policy.json";

/// Maximum price impact, in basis points, that a sized position may impose.
///
/// The Python side uses the same value in
/// `dex.intent_trade.chains.robinhood.v4_pool_capacity.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS`.
pub const PRICE_IMPACT_LIMIT_BPS: f64 = 10.0;

/// Depth band boundaries, expressed as a DECIMALS-NORMALISED BINDING-SIDE
/// capacity.
///
/// A round-trip arbitrage sends a position through a pool and back, so both
/// sides must absorb it. The binding side is the smaller of the two
/// one-sided capacities after each side is converted into its own token
/// units, because a raw unit is only comparable to another raw unit when
/// both tokens share the same number of decimals.
///
/// The previous raw-unit bands (100 / 1_000 / 10_000) compared 6-decimal
/// stablecoin amounts against 18-decimal token amounts in a single
/// threshold. Measured against the 94 candidate pools they classified 93 of
/// them as `deep`, so they discriminated nothing at all.
///
/// The boundaries are anchored on the position sizes the system actually
/// attempts rather than on percentiles of the pool population. The observed
/// position sizes have a median magnitude of about 0.02 token, so the
/// shallow boundary is the smallest attempted position (0.001 token), the
/// medium boundary is ten times that and the deep boundary is a hundred
/// times that. Measured against the 94 candidate pools this yields 15 dust,
/// 35 shallow, 29 medium and 19 deep.
///
/// The boundary operator is `>=` on both the Rust and the Python side.
pub const DEPTH_BAND_DEEP_CAPACITY: f64 = 0.1;
pub const DEPTH_BAND_MEDIUM_CAPACITY: f64 = 0.01;
pub const DEPTH_BAND_SHALLOW_CAPACITY: f64 = 0.001;

/// Basis the depth bands are computed on.
pub const DEPTH_BAND_BASIS: &str = "binding_side_decimal_normalised";

/// Minimum on-chain capacity, in raw token units, for a pool to be admitted
/// as a candidate and to participate in a closed loop.
///
/// This is a dust guard, not a depth band: it only rejects a capacity that
/// is literally zero. The admission floor is
/// [`MIN_CANDIDATE_BINDING_CAPACITY`].
pub const MIN_CANDIDATE_CAPACITY_RAW: u128 = 100;

/// Minimum decimals-normalised binding-side capacity for a candidate pool.
///
/// A pool must be able to absorb at least this much of its weaker side
/// within the epsilon price-impact budget.
pub const MIN_CANDIDATE_BINDING_CAPACITY: f64 = 0.001;

/// Maximum fixed fee, in hundredths of a bip, that a candidate pool may
/// charge.
pub const MAX_CANDIDATE_FIXED_FEE: i64 = 30_000;

/// Basis recorded on every capacity observation.
pub const DEPTH_BASIS: &str = "onchain_tick_liquidity";

/// The depth verdict labels that may enter the candidate set.
pub const ADMITTED_DEPTH_VERDICTS: [&str; 3] = ["deep", "liquid", "medium"];

/// One pool's admission verdict, as published on the depth document.
///
/// The verdict belongs to the Rust toolchain and is read from the shared
/// depth policy, so the pool manager, the runtime kernel and the orchestrator
/// can never disagree about which pools are deep enough. A pool whose
/// verdict or capacity was never measured is a **coverage gap** of the
/// store, not a statement about the market, so it is not rejected here.
#[derive(Clone, Debug, PartialEq)]
pub struct AdmissionVerdict {
    /// Whether the pool may enter the candidate set.
    pub admitted: bool,
    /// The measured depth verdict, when one was measured.
    pub depth_status: Option<String>,
    /// The decimals-normalised binding-side capacity, when one was measured.
    pub capacity_binding_raw: Option<f64>,
    /// The depth band derived from the measured capacity.
    pub depth_band: Option<String>,
    /// The reason the verdict was reached, for diagnostics.
    pub reason: String,
}

/// Normalise a measured depth verdict.
fn normalize_verdict(value: Option<&str>) -> Option<String> {
    value
        .map(|text| text.trim().to_ascii_lowercase())
        .filter(|text| !text.is_empty())
}

/// Whether the measured depth verdict admits a pool into the candidate set.
///
/// A missing verdict is a coverage gap of the store and is not a rejection.
pub fn depth_verdict_admits_pool(depth_status: Option<&str>) -> bool {
    match normalize_verdict(depth_status) {
        Some(verdict) => ADMITTED_DEPTH_VERDICTS.contains(&verdict.as_str()),
        None => true,
    }
}

/// Classify a decimals-normalised binding-side capacity into a depth band.
///
/// The boundary operator is `>=` and matches the kernel exactly.
pub fn depth_band_for_binding_capacity(capacity: Option<f64>) -> Option<String> {
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

/// Decide whether one measured pool may enter the candidate set.
///
/// The verdict and the capacity must agree. A pool that the pool manager
/// measured as `deep` but whose capacity is below the shared admission floor
/// is rejected: the label alone never overrides the measured capacity.
pub fn admit_candidate_pool_with_liquidity(
    depth_status: Option<&str>,
    binding_capacity: Option<f64>,
    has_liquidity: Option<bool>,
) -> AdmissionVerdict {
    let verdict = normalize_verdict(depth_status);
    let capacity = binding_capacity.filter(|value| value.is_finite());
    let band = depth_band_for_binding_capacity(capacity);
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

/// The shared policy document.
#[derive(Clone, Debug)]
pub struct DepthPolicy {
    pub price_impact_limit_bps: f64,
    pub depth_band_deep_capacity: f64,
    pub depth_band_medium_capacity: f64,
    pub depth_band_shallow_capacity: f64,
    pub min_candidate_capacity_raw: u128,
    pub min_candidate_binding_capacity: f64,
    pub max_candidate_fixed_fee: i64,
    pub depth_basis: String,
    pub depth_band_basis: String,
}

/// Resolves the absolute path of the shared depth policy file.
///
/// `FLASHLOAN_DEPTH_POLICY_PATH` overrides the search so the tool can be
/// pointed at an explicit file in a deployment.
pub fn depth_policy_path() -> Result<PathBuf, String> {
    if let Ok(override_path) = std::env::var("FLASHLOAN_DEPTH_POLICY_PATH") {
        let trimmed = override_path.trim();
        if !trimmed.is_empty() {
            let path = PathBuf::from(trimmed);
            if !path.is_file() {
                return Err(format!("depth policy override is not a file: {trimmed}"));
            }
            return Ok(path);
        }
    }

    // Walk up from the crate directory until the repository-relative path
    // resolves. This keeps the tool working from any working directory.
    let mut roots: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        roots.push(cwd.clone());
        roots.extend(cwd.ancestors().map(Path::to_path_buf));
    }
    if let Ok(manifest) = std::env::var("CARGO_MANIFEST_DIR") {
        let base = PathBuf::from(manifest);
        roots.push(base.clone());
        roots.extend(base.ancestors().map(Path::to_path_buf));
    }
    for root in roots {
        let candidate = root.join(DEPTH_POLICY_RELATIVE_PATH);
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err(format!(
        "shared depth policy file not found: {DEPTH_POLICY_RELATIVE_PATH} \
         (set FLASHLOAN_DEPTH_POLICY_PATH to override)"
    ))
}

/// Loads the shared depth policy document.
pub fn depth_policy() -> Result<&'static DepthPolicy, String> {
    static POLICY: OnceLock<Result<DepthPolicy, String>> = OnceLock::new();
    POLICY
        .get_or_init(|| {
            let path = depth_policy_path()?;
            let raw = std::fs::read_to_string(&path)
                .map_err(|error| format!("cannot read depth policy {}: {error}", path.display()))?;
            let payload: serde_json::Value = serde_json::from_str(&raw).map_err(|error| {
                format!("depth policy is not valid JSON {}: {error}", path.display())
            })?;
            DepthPolicy::from_json(&payload)
                .map_err(|error| format!("depth policy {}: {error}", path.display()))
        })
        .as_ref()
        .map_err(Clone::clone)
}

impl DepthPolicy {
    /// Parses the policy document.
    fn from_json(payload: &serde_json::Value) -> Result<Self, String> {
        let number = |key: &str| -> Result<f64, String> {
            payload
                .get(key)
                .and_then(serde_json::Value::as_f64)
                .ok_or_else(|| format!("missing or non-numeric key {key}"))
        };
        let min_capacity = payload
            .get("minCandidateCapacityRaw")
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| "missing or non-numeric key minCandidateCapacityRaw".to_string())?;
        let max_fee = payload
            .get("maxCandidateFixedFee")
            .and_then(serde_json::Value::as_i64)
            .ok_or_else(|| "missing or non-numeric key maxCandidateFixedFee".to_string())?;
        let depth_basis = payload
            .get("depthBasis")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "missing or non-string key depthBasis".to_string())?
            .to_string();
        let depth_band_basis = payload
            .get("depthBandBasis")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "missing or non-string key depthBandBasis".to_string())?
            .to_string();
        Ok(DepthPolicy {
            price_impact_limit_bps: number("priceImpactLimitBps")?,
            depth_band_deep_capacity: number("depthBandDeepCapacity")?,
            depth_band_medium_capacity: number("depthBandMediumCapacity")?,
            depth_band_shallow_capacity: number("depthBandShallowCapacity")?,
            min_candidate_capacity_raw: min_capacity as u128,
            min_candidate_binding_capacity: number("minCandidateBindingCapacity")?,
            max_candidate_fixed_fee: max_fee,
            depth_basis,
            depth_band_basis,
        })
    }
}

/// Asserts that the compiled-in constants still match the shared policy.
///
/// The check runs at start-up. A mismatch means the shared file changed
/// without the crate being rebuilt, and it must fail loudly: two tools that
/// disagree about the depth threshold would silently admit different pools.
pub fn assert_depth_policy_matches_file() -> Result<(), String> {
    let policy = depth_policy()?;
    let mut mismatches: Vec<String> = Vec::new();
    let compare_f64 = |name: &str, expected: f64, actual: f64, mismatches: &mut Vec<String>| {
        if (expected - actual).abs() > f64::EPSILON * expected.abs().max(1.0) {
            mismatches.push(format!("{name}: compiled {expected} != shared {actual}"));
        }
    };
    compare_f64(
        "priceImpactLimitBps",
        PRICE_IMPACT_LIMIT_BPS,
        policy.price_impact_limit_bps,
        &mut mismatches,
    );
    compare_f64(
        "depthBandDeepCapacity",
        DEPTH_BAND_DEEP_CAPACITY,
        policy.depth_band_deep_capacity,
        &mut mismatches,
    );
    compare_f64(
        "depthBandMediumCapacity",
        DEPTH_BAND_MEDIUM_CAPACITY,
        policy.depth_band_medium_capacity,
        &mut mismatches,
    );
    compare_f64(
        "depthBandShallowCapacity",
        DEPTH_BAND_SHALLOW_CAPACITY,
        policy.depth_band_shallow_capacity,
        &mut mismatches,
    );
    if MIN_CANDIDATE_CAPACITY_RAW != policy.min_candidate_capacity_raw {
        mismatches.push(format!(
            "minCandidateCapacityRaw: compiled {} != shared {}",
            MIN_CANDIDATE_CAPACITY_RAW, policy.min_candidate_capacity_raw
        ));
    }
    compare_f64(
        "minCandidateBindingCapacity",
        MIN_CANDIDATE_BINDING_CAPACITY,
        policy.min_candidate_binding_capacity,
        &mut mismatches,
    );
    if DEPTH_BAND_BASIS != policy.depth_band_basis {
        mismatches.push(format!(
            "depthBandBasis: compiled {DEPTH_BAND_BASIS} != shared {}",
            policy.depth_band_basis
        ));
    }
    if MAX_CANDIDATE_FIXED_FEE != policy.max_candidate_fixed_fee {
        mismatches.push(format!(
            "maxCandidateFixedFee: compiled {} != shared {}",
            MAX_CANDIDATE_FIXED_FEE, policy.max_candidate_fixed_fee
        ));
    }
    if DEPTH_BASIS != policy.depth_basis {
        mismatches.push(format!(
            "depthBasis: compiled {DEPTH_BASIS} != shared {}",
            policy.depth_basis
        ));
    }
    if mismatches.is_empty() {
        return Ok(());
    }
    Err(format!(
        "the compiled depth policy disagrees with the shared policy file: {}",
        mismatches.join("; ")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compiled_constants_match_the_shared_policy_file() {
        assert_depth_policy_matches_file().expect("shared depth policy must agree");
    }

    #[test]
    fn shared_policy_file_is_discoverable() {
        let path = depth_policy_path().expect("depth policy path");
        assert!(
            path.is_file(),
            "depth policy must exist at {}",
            path.display()
        );
    }

    #[test]
    fn band_boundaries_use_the_documented_operator() {
        // The band operator is `>=` on both sides, so a pool sitting exactly
        // on a boundary belongs to the higher band.
        let policy = depth_policy().expect("depth policy");
        assert!(policy.depth_band_deep_capacity >= policy.depth_band_medium_capacity);
        assert!(policy.depth_band_medium_capacity >= policy.depth_band_shallow_capacity);
        assert!(policy.depth_band_shallow_capacity > 0.0);
        assert!(
            policy.min_candidate_binding_capacity <= policy.depth_band_shallow_capacity,
            "the admission floor must not be stricter than the shallow band"
        );
    }
}
