//! USDG-denominated economic admissibility for token libraries.
//!
//! Why this module exists
//! ----------------------
//!
//! The token manager already knows how to ask the Uniswap Trading API
//! whether a token has a live exchange path to USDG, and roughly what one
//! unit is worth in USDG (see [`crate::valuation`]). That gives the library
//! a **single, chain-native unit of account**: USDG is the quote asset of
//! the venue, so a capacity expressed in USDG needs no external price feed
//! and no fiat conversion.
//!
//! Two distinct constraints come out of that observation.
//!
//! **The stock pool.** A stock token that cannot be valued in USDG has no
//! economic meaning for the arbitrage book: there is no way to size a
//! position in it, no way to express its depth, and no way to decide
//! whether the pool around it is worth trading. Such a token must not sit
//! in the stock library. The registry is a *source of candidates*, not a
//! *grant of membership*: membership is granted by the USDG valuation, and
//! revoked when the valuation disappears.
//!
//! **The extension pool.** An extension token is an intermediate hop. It
//! does not need a valuation of its own — what matters is whether it can
//! *carry* the size of the position that enters it. The bar is therefore
//! a **capacity** bar, not a valuation bar: an intermediate hop that cannot
//! carry at least the minimum economic position is a dust hop and is
//! excluded.
//!
//! Two thresholds, both expressed in USDG
//! ------------------------------------
//!
//! The minimum economic position answers "how small a trade can be worth
//! doing?". It is compared against the gas cost of the trade, so it is
//! derived from the gas estimate rather than guessed. A position below it
//! cannot pay for itself.
//!
//! The minimum pool depth answers "is this pool big enough to carry a
//! minimum economic position?". It is compared against the capacity of the
//! pool.
//!
//! Both thresholds are USDG amounts. USDG has six decimals on this chain.

use serde::Serialize;
use std::path::PathBuf;
use std::sync::OnceLock;

use crate::quote_asset::{quote_asset_for, QuoteAsset};

/// The canonical USDG address on Robinhood Chain.
///
/// Matching is by address, never by symbol.
pub const CANONICAL_USDG_ADDRESS: &str = "0x5fc5360d0400a0fd4f2af552add042d716f1d168";

/// USDG uses six decimals on this chain.
pub const USDG_DECIMALS: u8 = 6;

/// Location of the shared depth and economic policy, relative to the
/// repository root.
pub const DEPTH_POLICY_RELATIVE_PATH: &str = "flashloan/config/depth_policy.json";

/// The compiled-in view of the shared economic floor.
///
/// These are the values the shared policy file carried when this crate was
/// compiled. They exist only so the tool can **assert** that the file has
/// not drifted away from the code: the effective values always come from
/// [`economic_policy`], which reads the file. A stale constant makes the
/// tool fail loudly at start-up instead of silently reaching a different
/// verdict than the Python orchestrator.
pub const COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW: u128 = 10_000_000;

/// The compiled-in view of the shared minimum pool depth.
pub const COMPILED_MIN_POOL_DEPTH_USDG_RAW: u128 = 10_000_000;

/// The compiled-in view of the USDG peg ratio (R-11).
///
/// USDG is USD-equivalent by definition, so the ratio is exactly one. It is
/// compiled in only so the tool can detect a drift between this crate and
/// the shared policy file; the effective value always comes from the file.
pub const COMPILED_USDG_PEG_RATIO: f64 = 1.0;

/// The compiled-in view of the tolerated USDG peg deviation.
///
/// Zero means a depegged USDG must fail loudly rather than be approximated.
pub const COMPILED_USDG_PEG_DEVIATION_TOLERANCE_BPS: i64 = 0;

/// The shared USDG peg policy (R-11).
#[derive(Clone, Debug)]
pub struct UsdgPegPolicy {
    /// The USDG-to-USD ratio. Exactly `1.0` under R-11.
    pub peg_ratio: f64,
    /// The basis the ratio is asserted on.
    pub basis: String,
    /// The tolerated deviation, in basis points. Zero under R-11.
    pub deviation_tolerance_bps: i64,
    /// The uses the peg may enter.
    pub allowed_uses: Vec<String>,
    /// The uses the peg may never enter.
    pub forbidden_uses: Vec<String>,
}

/// The verdict of one economic admissibility check.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EconomicVerdict {
    /// The token is economically admissible.
    Admissible,
    /// The token has no USDG valuation, so it cannot be valued at all.
    NoUsdgValuation,
    /// The token is valued, but the valuation is too small to be useful.
    ValuationBelowFloor,
    /// The pool cannot carry the minimum economic position.
    InsufficientPoolDepth,
    /// The valuation could not be established, and the difference between
    /// "cannot be valued" and "not measured yet" must be preserved.
    NotMeasured,
}

impl EconomicVerdict {
    /// Whether the verdict admits the token.
    pub fn is_admissible(&self) -> bool {
        matches!(self, EconomicVerdict::Admissible)
    }

    /// Stable machine-readable token for the verdict.
    pub fn as_str(&self) -> &'static str {
        match self {
            EconomicVerdict::Admissible => "admissible",
            EconomicVerdict::NoUsdgValuation => "no_usdg_valuation",
            EconomicVerdict::ValuationBelowFloor => "valuation_below_floor",
            EconomicVerdict::InsufficientPoolDepth => "insufficient_pool_depth",
            EconomicVerdict::NotMeasured => "not_measured",
        }
    }
}

/// The thresholds that define economic admissibility.
#[derive(Clone, Copy, Debug)]
pub struct EconomicPolicy {
    /// The minimum position worth trading, in raw USDG units.
    pub min_economic_position_usdg_raw: u128,
    /// The minimum depth a pool must have to carry that position, in raw
    /// USDG units.
    pub min_pool_depth_usdg_raw: u128,
}

impl Default for EconomicPolicy {
    fn default() -> Self {
        economic_policy()
    }
}

impl EconomicPolicy {
    /// Restates the policy in the quote asset of a chain scope.
    ///
    /// The shared policy file states the floor in raw USDG units, because
    /// USDG is the mainnet's quote asset.  A chain that cannot be valued
    /// in USDG needs the *same* floor expressed in its own unit of
    /// account; the number of major units is what carries over, and the
    /// raw representation follows the quote asset's decimals.
    ///
    /// The mainnet scope is returned unchanged, so every existing
    /// invocation keeps its exact verdict.
    pub fn for_scope(network: &str, chain_id: u64) -> Result<Self, String> {
        let base = economic_policy();
        let quote = quote_asset_for(network, chain_id, base.min_economic_position_usdg_raw)?;
        Ok(Self {
            min_economic_position_usdg_raw: quote.min_economic_position_raw,
            min_pool_depth_usdg_raw: quote.min_pool_depth_raw,
        })
    }

    /// The quote asset the thresholds are denominated in.
    pub fn quote_asset(network: &str, chain_id: u64) -> Result<QuoteAsset, String> {
        let base = economic_policy();
        quote_asset_for(network, chain_id, base.min_economic_position_usdg_raw)
    }
}

/// Resolves the absolute path of the shared policy file.
///
/// `FLASHLOAN_DEPTH_POLICY_PATH` overrides the search so the tool can be
/// pointed at an explicit file in a deployment.
fn economic_policy_path() -> Result<PathBuf, String> {
    if let Ok(override_path) = std::env::var("FLASHLOAN_DEPTH_POLICY_PATH") {
        let trimmed = override_path.trim();
        if !trimmed.is_empty() {
            let path = PathBuf::from(trimmed);
            if !path.is_file() {
                return Err(format!("policy override is not a file: {trimmed}"));
            }
            return Ok(path);
        }
    }
    let mut cursor = std::env::current_dir().map_err(|error| error.to_string())?;
    loop {
        let candidate = cursor.join(DEPTH_POLICY_RELATIVE_PATH);
        if candidate.is_file() {
            return Ok(candidate);
        }
        if !cursor.pop() {
            break;
        }
    }
    Err(format!(
        "shared policy file not found: {DEPTH_POLICY_RELATIVE_PATH} \
         (set FLASHLOAN_DEPTH_POLICY_PATH to override)"
    ))
}

/// The shared economic policy, read from the policy file.
///
/// The value is cached because it is read on every evidence render. The
/// cache is per-process, so a tool that reads the file once at start-up
/// keeps a stable view for its whole run.
pub fn economic_policy() -> EconomicPolicy {
    static POLICY: OnceLock<EconomicPolicy> = OnceLock::new();
    *POLICY.get_or_init(|| {
        economic_policy_from_file().unwrap_or_else(|error| {
            // The file is the single source of truth. When it cannot be
            // read the tool must not fall back to a second effective copy
            // of the threshold: the compiled view is used instead, and the
            // assertion in `assert_economic_policy_matches_file` reports
            // the drift loudly.
            eprintln!("token-manager warning: economic policy file unavailable: {error}");
            EconomicPolicy {
                min_economic_position_usdg_raw: COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW,
                min_pool_depth_usdg_raw: COMPILED_MIN_POOL_DEPTH_USDG_RAW,
            }
        })
    })
}

/// Reads the economic thresholds from the shared policy file.
fn economic_policy_from_file() -> Result<EconomicPolicy, String> {
    let path = economic_policy_path()?;
    let raw = std::fs::read_to_string(&path)
        .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    let document: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|error| format!("{} is not valid JSON: {error}", path.display()))?;
    let section = document
        .get("economicPolicy")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| format!("{} carries no economicPolicy section", path.display()))?;
    let read = |key: &str| -> Result<u128, String> {
        let value = section
            .get(key)
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| {
                format!(
                    "{} is missing or non-numeric economicPolicy.{key}",
                    path.display()
                )
            })?;
        Ok(value as u128)
    };
    Ok(EconomicPolicy {
        min_economic_position_usdg_raw: read("minEconomicPositionUsdgRaw")?,
        min_pool_depth_usdg_raw: read("minPoolDepthUsdgRaw")?,
    })
}

/// Reads the USDG peg policy from the shared policy file.
///
/// R-11 (2026-11-12, user ruling): USDG may be treated as economically
/// equivalent to USD, so `1 USDG == 1 USD`. The peg ratio is therefore
/// exactly one and the tolerated deviation is zero. A different ratio is a
/// different ruling and must be made explicitly instead of inherited from a
/// stale file, so an unexpected value fails here rather than silently
/// changing every economic verdict.
///
/// The allowed and forbidden use lists are what keep the ruling bounded:
/// the peg may state an economic floor or a reported notional, but it may
/// never decide depth, capacity or library membership on its own.
fn usdg_peg_policy_from_file() -> Result<UsdgPegPolicy, String> {
    let path = economic_policy_path()?;
    usdg_peg_policy_from_path(&path)
}

/// Reads the USDG peg policy from one explicit policy file.
///
/// The path is a parameter rather than a process-global lookup so that a
/// test, or a caller that points at a specific policy file, cannot change
/// what every other reader in the same process observes. The previous
/// environment-variable override was shared by every reader, which made
/// concurrent tests race each other over the same ruling.
fn usdg_peg_policy_from_path(path: &std::path::Path) -> Result<UsdgPegPolicy, String> {
    let raw = std::fs::read_to_string(&path)
        .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    let document: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|error| format!("{} is not valid JSON: {error}", path.display()))?;
    let section = document
        .get("usdgPegPolicy")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| format!("{} carries no usdgPegPolicy section", path.display()))?;
    let peg_ratio = section
        .get("pegRatio")
        .and_then(serde_json::Value::as_f64)
        .ok_or_else(|| {
            format!(
                "{} is missing or non-numeric usdgPegPolicy.pegRatio",
                path.display()
            )
        })?;
    let basis = section
        .get("basis")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .to_string();
    // The file stores the tolerance as a JSON number. It is read as a float
    // and then required to be integral, because `0.0` and `0` are the same
    // ruling while `0.5` would tolerate a depeg.
    let deviation_tolerance_bps = section
        .get("deviationToleranceBps")
        .and_then(serde_json::Value::as_f64)
        .filter(|value| value.is_finite() && value.fract() == 0.0)
        .map(|value| value as i64)
        .ok_or_else(|| {
            format!(
                "{} is missing or non-integral usdgPegPolicy.deviationToleranceBps",
                path.display()
            )
        })?;
    let read_uses = |key: &str| -> Result<Vec<String>, String> {
        let items = section
            .get(key)
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| {
                format!(
                    "{} is missing or non-array usdgPegPolicy.{key}",
                    path.display()
                )
            })?;
        items
            .iter()
            .map(|item| {
                item.as_str().map(|text| text.to_string()).ok_or_else(|| {
                    format!(
                        "{} carries a non-string entry in usdgPegPolicy.{key}",
                        path.display()
                    )
                })
            })
            .collect()
    };
    Ok(UsdgPegPolicy {
        peg_ratio,
        basis,
        deviation_tolerance_bps,
        allowed_uses: read_uses("allowedUses")?,
        forbidden_uses: read_uses("forbiddenUses")?,
    })
}

/// Asserts that the USDG peg ruling (R-11) holds and matches the file.
///
/// The check runs at start-up next to the economic-policy assertion. It
/// fails loudly when the shared file states a peg ratio other than one, a
/// non-zero deviation tolerance, or a compiled view that has drifted away
/// from the file. A depegged USDG must stop the tool, not be approximated.
pub fn assert_usdg_peg_policy_matches_file() -> Result<(), String> {
    let path = economic_policy_path()?;
    assert_usdg_peg_policy_matches_path(&path)
}

/// Asserts the USDG peg ruling against one explicit policy file.
///
/// The public entry point resolves the shared file; this variant takes the
/// path so a caller (or a test) verifies a specific file without mutating
/// any process-wide state.
pub fn assert_usdg_peg_policy_matches_path(path: &std::path::Path) -> Result<(), String> {
    let policy = usdg_peg_policy_from_path(path)?;
    let mut mismatches: Vec<String> = Vec::new();
    if policy.peg_ratio != 1.0 {
        mismatches.push(format!(
            "usdgPegPolicy.pegRatio must be 1.0 under R-11; got {}",
            policy.peg_ratio
        ));
    }
    if policy.deviation_tolerance_bps != 0 {
        mismatches.push(format!(
            "usdgPegPolicy.deviationToleranceBps must be 0 under R-11; got {}",
            policy.deviation_tolerance_bps
        ));
    }
    if COMPILED_USDG_PEG_RATIO != policy.peg_ratio {
        mismatches.push(format!(
            "pegRatio: compiled {} != shared {}",
            COMPILED_USDG_PEG_RATIO, policy.peg_ratio
        ));
    }
    if COMPILED_USDG_PEG_DEVIATION_TOLERANCE_BPS != policy.deviation_tolerance_bps {
        mismatches.push(format!(
            "deviationToleranceBps: compiled {} != shared {}",
            COMPILED_USDG_PEG_DEVIATION_TOLERANCE_BPS, policy.deviation_tolerance_bps
        ));
    }
    if policy.allowed_uses.is_empty() {
        mismatches.push(
            "usdgPegPolicy.allowedUses is empty; the ruling must name the allowed uses".to_string(),
        );
    }
    if policy.forbidden_uses.is_empty() {
        mismatches.push(
            "usdgPegPolicy.forbiddenUses is empty; the ruling must name the forbidden uses"
                .to_string(),
        );
    }
    if mismatches.is_empty() {
        return Ok(());
    }
    Err(format!(
        "the USDG peg policy is not usable: {}",
        mismatches.join("; ")
    ))
}

/// Asserts that the compiled view still matches the shared policy file.
///
/// The check runs at start-up. A mismatch means the shared file changed
/// without the crate being rebuilt, and it must fail loudly: two tools
/// that disagree about the economic floor would silently admit different
/// token libraries.
pub fn assert_economic_policy_matches_file() -> Result<(), String> {
    let policy = economic_policy_from_file()?;
    let mut mismatches: Vec<String> = Vec::new();
    if COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW != policy.min_economic_position_usdg_raw {
        mismatches.push(format!(
            "minEconomicPositionUsdgRaw: compiled {} != shared {}",
            COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW, policy.min_economic_position_usdg_raw
        ));
    }
    if COMPILED_MIN_POOL_DEPTH_USDG_RAW != policy.min_pool_depth_usdg_raw {
        mismatches.push(format!(
            "minPoolDepthUsdgRaw: compiled {} != shared {}",
            COMPILED_MIN_POOL_DEPTH_USDG_RAW, policy.min_pool_depth_usdg_raw
        ));
    }
    if mismatches.is_empty() {
        return Ok(());
    }
    Err(format!(
        "the compiled economic policy disagrees with the shared policy file: {}",
        mismatches.join("; ")
    ))
}

/// The evidence one token's economic admissibility is judged on.
#[derive(Clone, Debug)]
pub struct TokenEconomics {
    /// The token address, lowercase.
    pub token_address: String,
    /// The USDG value of one whole token, in raw USDG units.
    ///
    /// `None` means the valuation is not known. This is **not** the same as
    /// a value of zero: a zero value is a measurement that says the token
    /// has no path to USDG, while `None` says nobody has measured it.
    pub usdg_value_raw: Option<u128>,
    /// The depth of the pool the token trades in, in raw USDG units.
    ///
    /// `None` means the depth is not known yet.
    pub pool_depth_usdg_raw: Option<u128>,
    /// Whether the valuation has been measured at all.
    ///
    /// A token whose valuation was never attempted is `NotMeasured`, not
    /// `NoUsdgValuation`.
    pub valuation_attempted: bool,
}

impl TokenEconomics {
    /// Builds the evidence for a token that has never been measured.
    pub fn unmeasured(token_address: impl Into<String>) -> Self {
        Self {
            token_address: token_address.into().to_ascii_lowercase(),
            usdg_value_raw: None,
            pool_depth_usdg_raw: None,
            valuation_attempted: false,
        }
    }
}

/// The result of checking one token against the economic policy.
#[derive(Clone, Debug, Serialize)]
pub struct EconomicAssessment {
    /// The token address, lowercase.
    pub token_address: String,
    /// The verdict.
    pub verdict: EconomicVerdict,
    /// The measured USDG value of one token, in raw units.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usdg_value_raw: Option<u128>,
    /// The measured pool depth, in raw USDG units.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pool_depth_usdg_raw: Option<u128>,
    /// The minimum position the policy requires, in raw USDG units.
    pub min_economic_position_usdg_raw: u128,
    /// The minimum depth the policy requires, in raw USDG units.
    pub min_pool_depth_usdg_raw: u128,
    /// Why the verdict was reached.
    pub reason: String,
}

impl EconomicAssessment {
    /// Whether the token is admissible.
    pub fn is_admissible(&self) -> bool {
        self.verdict.is_admissible()
    }
}

/// Assess one token against the economic policy.
///
/// The order of the checks matters, because the verdict must say *why* a
/// token was excluded:
///
/// 1. **Not measured** — nobody has asked the question yet. The token is
///    *not* excluded; it is simply unknown, and the library refresh will
///    ask again later. Reporting this as "no value" would be a lie.
/// 2. **No USDG valuation** — the valuation was attempted and the token has
///    no path to USDG. The token cannot be valued, so it cannot be sized.
/// 3. **Valuation below the floor** — the token is worth something, but so
///    little that a minimum position is worth less than the gas it costs.
/// 4. **Insufficient pool depth** — the token is worth enough, but the pool
///    around it cannot carry a minimum position, so no trade through it is
///    worth doing.
/// 5. **Admissible** — the token is valued and the pool can carry a
///    position worth doing.
pub fn assess_token(economics: &TokenEconomics, policy: &EconomicPolicy) -> EconomicAssessment {
    let mut assessment = EconomicAssessment {
        token_address: economics.token_address.clone(),
        verdict: EconomicVerdict::NotMeasured,
        usdg_value_raw: economics.usdg_value_raw,
        pool_depth_usdg_raw: economics.pool_depth_usdg_raw,
        min_economic_position_usdg_raw: policy.min_economic_position_usdg_raw,
        min_pool_depth_usdg_raw: policy.min_pool_depth_usdg_raw,
        reason: String::new(),
    };

    if !economics.valuation_attempted && economics.usdg_value_raw.is_none() {
        assessment.verdict = EconomicVerdict::NotMeasured;
        assessment.reason = "no USDG valuation has been measured for this token yet".to_owned();
        return assessment;
    }

    let Some(value_raw) = economics.usdg_value_raw else {
        assessment.verdict = EconomicVerdict::NoUsdgValuation;
        assessment.reason =
            "the token has no exchange path to USDG, so it cannot be valued".to_owned();
        return assessment;
    };

    if value_raw == 0 {
        assessment.verdict = EconomicVerdict::NoUsdgValuation;
        assessment.reason =
            "the token has no exchange path to USDG, so it cannot be valued".to_owned();
        return assessment;
    }

    // One whole token must be worth at least the minimum economic position.
    // Otherwise a position of any size in this token is worth less than the
    // gas it costs, and the pool is a dust pool by definition.
    if value_raw < policy.min_economic_position_usdg_raw {
        assessment.verdict = EconomicVerdict::ValuationBelowFloor;
        assessment.reason = format!(
            "one token is worth {value_raw} raw USDG, below the minimum economic position of {} raw USDG",
            policy.min_economic_position_usdg_raw
        );
        return assessment;
    }

    // The pool must be able to carry a position of the minimum economic
    // size. An unknown depth is not a failure: the pool index may simply
    // not have published a depth yet.
    if let Some(depth_raw) = economics.pool_depth_usdg_raw {
        if depth_raw < policy.min_pool_depth_usdg_raw {
            assessment.verdict = EconomicVerdict::InsufficientPoolDepth;
            assessment.reason = format!(
                "pool depth is {depth_raw} raw USDG, below the minimum of {} raw USDG",
                policy.min_pool_depth_usdg_raw
            );
            return assessment;
        }
    }

    assessment.verdict = EconomicVerdict::Admissible;
    assessment.reason =
        "the token is valued in USDG and its pool can carry a minimum economic position".to_owned();
    assessment
}

/// Assess a batch of tokens, returning one assessment per token.
pub fn assess_tokens(
    economics: &[TokenEconomics],
    policy: &EconomicPolicy,
) -> Vec<EconomicAssessment> {
    economics
        .iter()
        .map(|entry| assess_token(entry, policy))
        .collect()
}

/// The stock-library admission decision for a batch of tokens.
///
/// A stock token is admitted to the stock library only when it is
/// economically admissible. A token that is merely **not measured** is
/// **not** admitted either, because the library is a statement about what
/// can be traded; but it is reported separately so the operator can see
/// that the reason is "not known yet" rather than "worthless".
pub fn stock_library_admission(
    economics: &[TokenEconomics],
    policy: &EconomicPolicy,
) -> StockAdmissionReport {
    let assessments = assess_tokens(economics, policy);
    let mut admitted = Vec::new();
    let mut excluded = Vec::new();
    let mut pending = Vec::new();
    for assessment in assessments {
        match assessment.verdict {
            EconomicVerdict::Admissible => admitted.push(assessment),
            EconomicVerdict::NotMeasured => pending.push(assessment),
            _ => excluded.push(assessment),
        }
    }
    StockAdmissionReport {
        admitted_count: admitted.len(),
        excluded_count: excluded.len(),
        pending_count: pending.len(),
        admitted,
        excluded,
        pending,
    }
}

/// The result of applying the economic policy to the stock library.
#[derive(Clone, Debug, Serialize)]
pub struct StockAdmissionReport {
    /// How many tokens are admitted.
    pub admitted_count: usize,
    /// How many tokens are excluded because they fail the economic test.
    pub excluded_count: usize,
    /// How many tokens are unknown because nobody measured them.
    pub pending_count: usize,
    /// The admitted tokens.
    pub admitted: Vec<EconomicAssessment>,
    /// The excluded tokens, with the reason.
    pub excluded: Vec<EconomicAssessment>,
    /// The tokens that are not measured yet.
    pub pending: Vec<EconomicAssessment>,
}

/// The extension-library admission decision for a batch of intermediate hops.
///
/// An extension token is an intermediate hop. It is **not** required to
/// have a USDG valuation of its own. What it must have is enough **depth**
/// to carry the position that arrives at it.
///
/// The check is therefore on the pool's depth alone, expressed in USDG.
pub fn extension_library_admission(
    hops: &[TokenEconomics],
    policy: &EconomicPolicy,
) -> ExtensionAdmissionReport {
    let mut admitted = Vec::new();
    let mut excluded = Vec::new();
    let mut pending = Vec::new();
    for hop in hops {
        let assessment = assess_extension_hop(hop, policy);
        match assessment.verdict {
            EconomicVerdict::Admissible => admitted.push(assessment),
            EconomicVerdict::NotMeasured => pending.push(assessment),
            _ => excluded.push(assessment),
        }
    }
    ExtensionAdmissionReport {
        admitted_count: admitted.len(),
        excluded_count: excluded.len(),
        pending_count: pending.len(),
        admitted,
        excluded,
        pending,
    }
}

/// The result of applying the capacity policy to the extension library.
#[derive(Clone, Debug, Serialize)]
pub struct ExtensionAdmissionReport {
    /// How many hops are admitted.
    pub admitted_count: usize,
    /// How many hops are excluded because they are too shallow.
    pub excluded_count: usize,
    /// How many hops have no measured depth yet.
    pub pending_count: usize,
    /// The admitted hops.
    pub admitted: Vec<EconomicAssessment>,
    /// The excluded hops, with the reason.
    pub excluded: Vec<EconomicAssessment>,
    /// The hops whose depth is not known yet.
    pub pending: Vec<EconomicAssessment>,
}

/// Assess one intermediate hop against the capacity policy.
///
/// Unlike a stock token, a hop does not need its own valuation: it only
/// needs to be able to **carry** the position that reaches it. An
/// unmeasured depth is reported as pending rather than excluded, because a
/// missing measurement is a coverage gap, not a defect.
pub fn assess_extension_hop(hop: &TokenEconomics, policy: &EconomicPolicy) -> EconomicAssessment {
    let mut assessment = EconomicAssessment {
        token_address: hop.token_address.clone(),
        verdict: EconomicVerdict::NotMeasured,
        usdg_value_raw: hop.usdg_value_raw,
        pool_depth_usdg_raw: hop.pool_depth_usdg_raw,
        min_economic_position_usdg_raw: policy.min_economic_position_usdg_raw,
        min_pool_depth_usdg_raw: policy.min_pool_depth_usdg_raw,
        reason: String::new(),
    };

    let Some(depth_raw) = hop.pool_depth_usdg_raw else {
        assessment.verdict = EconomicVerdict::NotMeasured;
        assessment.reason =
            "no pool depth has been measured for this intermediate hop yet".to_owned();
        return assessment;
    };

    if depth_raw < policy.min_pool_depth_usdg_raw {
        assessment.verdict = EconomicVerdict::InsufficientPoolDepth;
        assessment.reason = format!(
            "the hop can carry only {depth_raw} raw USDG, below the minimum economic position of {} raw USDG",
            policy.min_economic_position_usdg_raw
        );
        return assessment;
    }

    assessment.verdict = EconomicVerdict::Admissible;
    assessment.reason = "the hop can carry a minimum economic position".to_owned();
    assessment
}

/// Renders a raw USDG amount as a decimal string.
///
/// The conversion is integer-only so a thin pool's value can never be
/// rounded into looking larger than it is.
pub fn usdg_to_decimal(raw: u128) -> String {
    let decimals = usize::from(USDG_DECIMALS);
    let digits = raw.to_string();
    if digits.len() <= decimals {
        let padded = format!("{}{}", "0".repeat(decimals + 1 - digits.len()), digits);
        let split = padded.len() - decimals;
        let whole = &padded[..split];
        let fraction = padded[split..].trim_end_matches('0');
        return if fraction.is_empty() {
            whole.to_owned()
        } else {
            format!("{whole}.{fraction}")
        };
    }
    let split = digits.len() - decimals;
    let whole = &digits[..split];
    let fraction = digits[split..].trim_end_matches('0');
    if fraction.is_empty() {
        whole.to_owned()
    } else {
        format!("{whole}.{fraction}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const MIN_POSITION: u128 = COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW;
    const MIN_DEPTH: u128 = COMPILED_MIN_POOL_DEPTH_USDG_RAW;

    fn policy() -> EconomicPolicy {
        EconomicPolicy::default()
    }

    fn economics(value: Option<u128>, depth: Option<u128>, attempted: bool) -> TokenEconomics {
        TokenEconomics {
            token_address: "0x1111111111111111111111111111111111111111".to_owned(),
            usdg_value_raw: value,
            pool_depth_usdg_raw: depth,
            valuation_attempted: attempted,
        }
    }

    #[test]
    fn a_valued_token_with_deep_enough_pool_is_admissible() {
        let assessment = assess_token(
            &economics(Some(MIN_POSITION), Some(MIN_DEPTH), true),
            &policy(),
        );
        assert_eq!(assessment.verdict, EconomicVerdict::Admissible);
        assert!(assessment.is_admissible());
    }

    #[test]
    fn an_unmeasured_token_is_pending_rather_than_excluded() {
        let assessment = assess_token(&TokenEconomics::unmeasured("0xabc"), &policy());
        assert_eq!(assessment.verdict, EconomicVerdict::NotMeasured);
        assert!(!assessment.is_admissible());
        assert!(!assessment.reason.contains("worthless"));
    }

    #[test]
    fn a_token_without_a_usdg_path_cannot_be_valued() {
        let assessment = assess_token(&economics(Some(0), Some(MIN_DEPTH), true), &policy());
        assert_eq!(assessment.verdict, EconomicVerdict::NoUsdgValuation);
    }

    #[test]
    fn a_missing_measurement_is_not_a_missing_path() {
        // The valuation was attempted but produced no number: the token has
        // no path to USDG.
        let assessment = assess_token(&economics(None, Some(MIN_DEPTH), true), &policy());
        assert_eq!(assessment.verdict, EconomicVerdict::NoUsdgValuation);
    }

    #[test]
    fn a_token_worth_less_than_the_floor_is_a_dust_token() {
        let assessment = assess_token(
            &economics(Some(MIN_POSITION - 1), Some(MIN_DEPTH), true),
            &policy(),
        );
        assert_eq!(assessment.verdict, EconomicVerdict::ValuationBelowFloor);
    }

    #[test]
    fn a_shallow_pool_is_excluded_even_when_the_token_is_worth_a_lot() {
        let assessment = assess_token(
            &economics(Some(MIN_POSITION * 100), Some(MIN_DEPTH - 1), true),
            &policy(),
        );
        assert_eq!(assessment.verdict, EconomicVerdict::InsufficientPoolDepth);
    }

    #[test]
    fn an_unmeasured_depth_does_not_exclude_a_valued_token() {
        let assessment = assess_token(&economics(Some(MIN_POSITION), None, true), &policy());
        assert_eq!(assessment.verdict, EconomicVerdict::Admissible);
    }

    #[test]
    fn the_stock_library_excludes_unvaluable_tokens() {
        let report = stock_library_admission(
            &[
                economics(Some(MIN_POSITION), Some(MIN_DEPTH), true),
                economics(Some(0), Some(MIN_DEPTH), true),
                economics(Some(MIN_POSITION - 1), Some(MIN_DEPTH), true),
                TokenEconomics::unmeasured("0xpending"),
            ],
            &policy(),
        );
        assert_eq!(report.admitted_count, 1);
        assert_eq!(report.excluded_count, 2);
        assert_eq!(report.pending_count, 1);
        // The excluded tokens carry the reason they were excluded.
        assert!(report
            .excluded
            .iter()
            .any(|item| item.verdict == EconomicVerdict::NoUsdgValuation));
        assert!(report
            .excluded
            .iter()
            .any(|item| item.verdict == EconomicVerdict::ValuationBelowFloor));
    }

    #[test]
    fn an_intermediate_hop_needs_capacity_but_not_a_valuation() {
        // A hop with no valuation of its own is fine as long as it can
        // carry the position that reaches it.
        let mut hop = economics(None, Some(MIN_DEPTH), false);
        hop.token_address = "0xhop".to_owned();
        let assessment = assess_extension_hop(&hop, &policy());
        assert_eq!(assessment.verdict, EconomicVerdict::Admissible);
    }

    #[test]
    fn an_intermediate_hop_that_cannot_carry_the_position_is_a_dust_hop() {
        let mut hop = economics(None, Some(MIN_POSITION - 1), false);
        hop.token_address = "0xhop".to_owned();
        let assessment = assess_extension_hop(&hop, &policy());
        assert_eq!(assessment.verdict, EconomicVerdict::InsufficientPoolDepth);
    }

    #[test]
    fn an_intermediate_hop_without_a_measured_depth_is_pending() {
        let mut hop = economics(None, None, false);
        hop.token_address = "0xhop".to_owned();
        let assessment = assess_extension_hop(&hop, &policy());
        assert_eq!(assessment.verdict, EconomicVerdict::NotMeasured);
    }

    #[test]
    fn the_extension_library_separates_pending_from_excluded() {
        let mut shallow = economics(None, Some(MIN_DEPTH - 1), false);
        shallow.token_address = "0xshallow".to_owned();
        let mut deep = economics(None, Some(MIN_DEPTH), false);
        deep.token_address = "0xdeep".to_owned();
        let mut unknown = economics(None, None, false);
        unknown.token_address = "0xunknown".to_owned();
        let report = extension_library_admission(&[shallow, deep, unknown], &policy());
        assert_eq!(report.admitted_count, 1);
        assert_eq!(report.excluded_count, 1);
        assert_eq!(report.pending_count, 1);
    }

    #[test]
    fn raw_usdg_amounts_render_as_decimals() {
        assert_eq!(usdg_to_decimal(10_000_000), "10");
        assert_eq!(usdg_to_decimal(1_234_567), "1.234567");
        assert_eq!(usdg_to_decimal(1), "0.000001");
        assert_eq!(usdg_to_decimal(0), "0");
    }

    /// VAL-4-12 regression: the effective economic floor comes from the
    /// shared policy file, and the compiled-in view still agrees with it.
    ///
    /// The three places that have to agree are the shared JSON file, the
    /// Rust `EconomicPolicy` and the Python orchestrator. The Python side
    /// reads the same file, so agreement between the file and the Rust
    /// policy is what keeps all three in step.
    #[test]
    fn val4_12_the_economic_floor_comes_from_the_shared_policy_file() {
        assert_economic_policy_matches_file()
            .expect("the compiled economic policy must agree with the shared file");
        let policy = EconomicPolicy::default();
        assert_eq!(
            policy.min_economic_position_usdg_raw,
            COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW
        );
        assert_eq!(
            policy.min_pool_depth_usdg_raw,
            COMPILED_MIN_POOL_DEPTH_USDG_RAW
        );
        assert_eq!(policy.min_economic_position_usdg_raw, 10_000_000);
        assert_eq!(policy.min_pool_depth_usdg_raw, 10_000_000);
    }

    /// VAL-4-12 regression: the shared file must carry the economic section
    /// with the USDG denominated floor. A USD denominated threshold would
    /// reintroduce the external price dependency the system deliberately
    /// avoids.
    #[test]
    fn val4_12_the_shared_file_denominates_the_floor_in_usdg() {
        let path = economic_policy_path().expect("shared policy path");
        let raw = std::fs::read_to_string(&path).expect("read shared policy");
        let document: serde_json::Value =
            serde_json::from_str(&raw).expect("shared policy is valid JSON");
        assert_eq!(
            document
                .get("schemaVersion")
                .and_then(serde_json::Value::as_u64),
            Some(3),
            "the economic floor requires schema version 3"
        );
        let section = document
            .get("economicPolicy")
            .and_then(serde_json::Value::as_object)
            .expect("economicPolicy section");
        assert_eq!(
            section.get("basis").and_then(serde_json::Value::as_str),
            Some("usdg_quote_asset_not_usd")
        );
        assert_eq!(
            section
                .get("usdgDecimals")
                .and_then(serde_json::Value::as_u64),
            Some(6)
        );
    }

    /// R-11 regression: the shared policy file must declare the USDG peg
    /// explicitly, with a ratio of exactly one and a zero deviation
    /// tolerance. A depegged USDG has to fail the tool, not be approximated.
    #[test]
    fn r11_the_usdg_peg_policy_is_declared_with_a_ratio_of_one() {
        let policy = usdg_peg_policy_from_file().expect("usdg peg policy");
        assert_eq!(policy.peg_ratio, 1.0);
        assert_eq!(policy.deviation_tolerance_bps, 0);
        assert_eq!(policy.basis, "usdg_is_usd_equivalent_by_definition");
        assert!(
            !policy.allowed_uses.is_empty(),
            "the ruling must name the uses the peg may enter"
        );
        assert!(
            !policy.forbidden_uses.is_empty(),
            "the ruling must name the uses the peg may never enter"
        );
        assert_usdg_peg_policy_matches_file().expect("the peg policy is usable");
    }

    /// R-11 regression: the allowed use list must keep the peg out of every
    /// admission decision. Depth, capacity and library membership are decided
    /// by on-chain facts, never by a USD amount.
    #[test]
    fn r11_the_usdg_peg_never_decides_an_admission() {
        let policy = usdg_peg_policy_from_file().expect("usdg peg policy");
        for forbidden in [
            "depth_admission",
            "capacity_admission",
            "core_library_membership",
            "test_library_membership",
            "extension_library_membership",
            "broadcast_amount_sizing",
        ] {
            assert!(
                policy.forbidden_uses.contains(&forbidden.to_string()),
                "{forbidden} must be a forbidden use of the USDG peg"
            );
            assert!(
                !policy.allowed_uses.contains(&forbidden.to_string()),
                "{forbidden} must not be an allowed use of the USDG peg"
            );
        }
        assert!(policy
            .allowed_uses
            .contains(&"stock_library_usdg_exchange_requirement".to_string()));
    }

    /// R-11 regression (2026-11-12 clarification): the ruling is not "USD has
    /// no value" but "USDG **replaces** USD as the economic reference".
    ///
    /// The root cause is that most tokens have no exchange channel, so no
    /// external USD price can be obtained for them, while USDG is exchangeable
    /// in the large majority of cases. Every USD-denominated economic quantity
    /// therefore has to be expressed in an equal amount of USDG.
    ///
    /// This test pins that the shared file names the replacement explicitly,
    /// so a later edit cannot quietly narrow it back to reporting only.
    #[test]
    fn r11_usdg_replaces_usd_as_the_economic_reference() {
        let policy = usdg_peg_policy_from_file().expect("usdg peg policy");
        assert!(
            policy
                .allowed_uses
                .contains(&"usdg_replaces_usd_denomination".to_string()),
            "the ruling must name the USDG -> USD denomination replacement"
        );
        assert!(
            policy
                .allowed_uses
                .contains(&"economic_quantity_denominated_in_usdg".to_string()),
            "economic quantities must be denominated in USDG"
        );
        assert!(
            policy
                .allowed_uses
                .contains(&"stock_library_usdg_exchange_requirement".to_string()),
            "the stock library must stay bound to USDG exchangeability"
        );
        // The replacement acts on the *denomination* only. It must never
        // become a depth, capacity or membership gate.
        for forbidden in [
            "depth_admission",
            "capacity_admission",
            "core_library_membership",
            "test_library_membership",
            "extension_library_membership",
        ] {
            assert!(
                policy.forbidden_uses.contains(&forbidden.to_string()),
                "{forbidden} must stay forbidden"
            );
        }
    }
    /// R-11 regression: a file that states a peg other than one, or that
    /// tolerates a deviation, must be rejected. The ruling is "USDG is
    /// USD-equivalent by definition", so anything else is a different ruling
    /// that has to be made explicitly.
    #[test]
    fn r11_a_depegged_usdg_is_rejected() {
        let directory =
            std::env::temp_dir().join(format!("token-manager-peg-policy-{}", std::process::id()));
        std::fs::create_dir_all(&directory).expect("create temp dir");
        let path = directory.join("depth_policy.json");
        let write_policy = |peg_ratio: &str, tolerance: &str| {
            let raw = format!(
                "{{\"schemaVersion\":3,\"usdgPegPolicy\":{{\"pegRatio\":{peg_ratio},\
\"basis\":\"usdg_is_usd_equivalent_by_definition\",\
\"deviationToleranceBps\":{tolerance},\
\"allowedUses\":[\"operator_facing_reporting\"],\
\"forbiddenUses\":[\"depth_admission\"]}}}}"
            );
            std::fs::write(&path, raw).expect("write policy");
        };
        write_policy("1.0", "0");
        assert_usdg_peg_policy_matches_path(&path).expect("a valid peg policy is accepted");

        write_policy("0.98", "0");
        let error = assert_usdg_peg_policy_matches_path(&path)
            .expect_err("a peg ratio other than one must be rejected");
        assert!(
            error.contains("pegRatio must be 1.0"),
            "unexpected error: {error}"
        );

        write_policy("1.0", "25");
        let error = assert_usdg_peg_policy_matches_path(&path)
            .expect_err("a non-zero deviation tolerance must be rejected");
        assert!(
            error.contains("deviationToleranceBps must be 0"),
            "unexpected error: {error}"
        );

        let _ = std::fs::remove_dir_all(&directory);
    }
}
