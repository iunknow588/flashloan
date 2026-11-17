//! The USDG economic gate for token library membership.
//!
//! The gate is the bridge between the two libraries' economics and the
//! classification that feeds them.
//!
//! **The stock library** admits a stock token only when the token has a
//! measured USDG valuation that clears the economic floor. A token that
//! was never measured is *pending*, not excluded, so a later refresh can
//! admit it without any human intervention.
//!
//! **The extension library** admits an intermediate hop only when the hop
//! can carry a minimum economic position. A hop does not need a valuation
//! of its own: what matters is whether it can carry the size of the
//! position that enters it.
//!
//! The gate never *adds* a membership. It only removes the ones that
//! cannot be traded.

use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

use crate::classification::{ClassificationResult, ClassifiedToken};
use crate::model::LibraryKey;
use crate::usdg_economic::{EconomicPolicy, EconomicVerdict};

/// The economic evidence used to gate library membership.

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]

pub struct EconomicEvidence {
    /// The USDG value of one whole token, in raw USDG units, by token

    /// address. A missing entry means the token was never measured.
    pub stock_values_usdg_raw: BTreeMap<String, u128>,

    /// The tokens whose USDG valuation was actually attempted.

    ///

    /// This set is what separates "measured and worthless" from "never

    /// measured". Without it a token that nobody has asked about would be

    /// indistinguishable from a token that has no path to USDG.
    pub measured_stock_addresses: BTreeSet<String>,

    /// The pool depth of an intermediate hop, in raw USDG units, by token

    /// address.
    pub extension_depth_usdg_raw: BTreeMap<String, u128>,
}

impl EconomicEvidence {
    /// Builds the evidence for one stock token from a valuation outcome.

    pub fn with_stock_valuation(
        mut self,

        token_address: impl Into<String>,

        usdg_value_raw: Option<u128>,
    ) -> Self {
        let address = token_address.into().to_ascii_lowercase();

        self.measured_stock_addresses.insert(address.clone());

        if let Some(value) = usdg_value_raw {
            self.stock_values_usdg_raw.insert(address, value);
        }

        self
    }

    /// Builds the evidence for one intermediate hop from its measured depth.

    pub fn with_extension_depth(
        mut self,

        token_address: impl Into<String>,

        depth_usdg_raw: u128,
    ) -> Self {
        self.extension_depth_usdg_raw
            .insert(token_address.into().to_ascii_lowercase(), depth_usdg_raw);

        self
    }
}

/// The result of applying the economic gate to a classification.

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]

pub struct EconomicGateResult {
    /// The tokens, with the membership adjusted by the economic gate.
    pub tokens: Vec<ClassifiedToken>,

    /// The tokens the gate excluded from the stock library.
    pub excluded_stock_tokens: Vec<EconomicExclusion>,

    /// The intermediate hops the gate excluded from the extension library.
    pub excluded_extension_hops: Vec<EconomicExclusion>,

    /// The stock tokens that are waiting for a measurement.
    pub pending_stock_tokens: Vec<EconomicExclusion>,

    /// The write that records every membership the gate withdrew.

    ///

    /// The gate removes a membership by taking it away from the

    /// classification, but the row that is already in the library table

    /// would simply stay there, still `admitted`. Without these writes

    /// the database would keep advertising a token the gate has just

    /// rejected.

    ///

    /// Each withdrawal is written as a `blocked` row with the reason

    /// attached, so the decision stays auditable and a later refresh can

    /// reverse it.

    #[serde(skip)]
    pub withdrawn_stock_writes: Vec<crate::model::TokenWrite>,

    /// The extension withdrawals, routed to the extension table.

    #[serde(skip)]
    pub withdrawn_extension_writes: Vec<crate::model::TokenWrite>,
}

/// One token the economic gate acted on.

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]

pub struct EconomicExclusion {
    pub token_address: String,

    pub token_symbol: String,

    pub verdict: EconomicVerdict,

    pub reason: String,
}

/// Apply the USDG economic gate to a classification.

///

/// The gate has two halves, matching the two libraries it governs:

///

/// * the **stock** library admits a stock token only when the token has a

///   measured USDG valuation that clears the economic floor. A token that

///   was never measured is *pending*, not excluded, so a refresh can admit

///   it later without any human intervention;

/// * the **extension** library admits an intermediate hop only when the

///   hop can carry a minimum economic position. A hop does not need a

///   valuation of its own.

///

/// The gate never *adds* a membership. It only removes the ones that

/// cannot be traded.

pub fn apply_economic_gate(
    classification: &mut ClassificationResult,

    evidence: &EconomicEvidence,

    policy: &EconomicPolicy,
) -> EconomicGateResult {
    let mut excluded_stock_tokens = Vec::new();

    let mut excluded_extension_hops = Vec::new();

    let mut pending_stock_tokens = Vec::new();

    let mut withdrawn_stock_writes = Vec::new();

    let mut withdrawn_extension_writes = Vec::new();

    for token in classification.tokens.iter_mut() {
        let address = token.token.token_address.to_ascii_lowercase();

        // Only the stock library requires a USDG exchange path. The
        // declaration lives in one place so the requirement cannot drift
        // away from the gate that enforces it.
        let is_stock = token
            .primary_library
            .map(library_requires_usdg_exchange)
            .unwrap_or(false);

        let is_extension = token
            .memberships
            .iter()
            .any(|membership| membership.library == LibraryKey::Extension);

        if is_stock {
            let measured = evidence.measured_stock_addresses.contains(&address);

            let value = evidence.stock_values_usdg_raw.get(&address).copied();

            let economics = crate::usdg_economic::TokenEconomics {
                token_address: address.clone(),

                usdg_value_raw: value,

                pool_depth_usdg_raw: evidence.extension_depth_usdg_raw.get(&address).copied(),

                valuation_attempted: measured,
            };

            let assessment = crate::usdg_economic::assess_token(&economics, policy);

            if assessment.verdict == EconomicVerdict::Admissible {
                continue;
            }

            let exclusion = EconomicExclusion {
                token_address: address.clone(),

                token_symbol: token.token.token_symbol.clone(),

                verdict: assessment.verdict,

                reason: assessment.reason.clone(),
            };

            // The stock membership is the primary library, so the token

            // leaves the library entirely rather than merely losing the

            // primary label.

            token.primary_library = None;

            token
                .memberships
                .retain(|membership| membership.library != LibraryKey::Stocks);

            if assessment.verdict == EconomicVerdict::NotMeasured {
                pending_stock_tokens.push(exclusion);
            } else {
                // The membership is withdrawn, so the row that is

                // already in the library table has to record it.

                withdrawn_stock_writes.push(token.token.to_blocked_token_write(&format!(
                    "usdg_economic_gate: {} ({})",
                    exclusion.reason,
                    exclusion.verdict.as_str()
                )));

                excluded_stock_tokens.push(exclusion);
            }
        }

        if is_extension {
            let depth = evidence.extension_depth_usdg_raw.get(&address).copied();

            let economics = crate::usdg_economic::TokenEconomics {
                token_address: address.clone(),

                usdg_value_raw: None,

                pool_depth_usdg_raw: depth,

                valuation_attempted: depth.is_some(),
            };

            let assessment = crate::usdg_economic::assess_extension_hop(&economics, policy);

            if assessment.verdict == EconomicVerdict::Admissible
                || assessment.verdict == EconomicVerdict::NotMeasured
            {
                continue;
            }

            let exclusion = EconomicExclusion {
                token_address: address.clone(),

                token_symbol: token.token.token_symbol.clone(),

                verdict: assessment.verdict,

                reason: assessment.reason.clone(),
            };

            withdrawn_extension_writes.push(token.token.to_blocked_token_write(&format!(
                "usdg_economic_gate: {} ({})",
                exclusion.reason,
                exclusion.verdict.as_str()
            )));

            excluded_extension_hops.push(exclusion);

            token
                .memberships
                .retain(|membership| membership.library != LibraryKey::Extension);
        }
    }

    EconomicGateResult {
        tokens: classification.tokens.clone(),

        excluded_stock_tokens,

        excluded_extension_hops,

        pending_stock_tokens,

        withdrawn_stock_writes,

        withdrawn_extension_writes,
    }
}

/// Whether a library requires a token to be exchangeable into USDG before it
/// may hold that membership.
///
/// The requirement is deliberately **not** uniform across the libraries:
///
/// * the **stock** library requires it. A stock token that cannot be
///   exchanged into USDG has no economic meaning for the arbitrage book:
///   there is no way to size a position in it and no way to express its
///   depth. Such a token must not sit in the stock library, and therefore
///   must not reach the fund pool either.
///
/// * the **core** and **test** libraries do **not** require it. They are
///   structural libraries: the core library carries the venue's own quote
///   and collateral assets, and the test library carries fixtures that are
///   deliberately disconnected from any market.
///
/// * the **extension** library does **not** require it *of the token
///   itself*. What an extension hop must satisfy is the capacity to carry a
///   minimum economic position. Its relationship to USDG still holds
///   logically, because the extension hop is reached through a stock token
///   or a core token, both of which are themselves USDG-reachable. The
///   constraint is therefore satisfied **transitively** rather than
///   directly.
pub fn library_requires_usdg_exchange(library: LibraryKey) -> bool {
    matches!(library, LibraryKey::Stocks)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::classification::classify_tokens;
    use crate::metadata::MetadataSourceKind;
    use std::collections::BTreeMap;

    // ----------------------------------------------------------------
    // Economic gate (USDG denominated)
    // ----------------------------------------------------------------

    const CORE: &str = "0x1111111111111111111111111111111111111111";
    const STOCK: &str = "0x2222222222222222222222222222222222222222";
    const EXTENSION: &str = "0x3333333333333333333333333333333333333333";

    fn token(
        address: &str,
        symbol: &str,
        source: crate::metadata::MetadataSourceKind,
        checksum_suffix: &str,
    ) -> crate::metadata::NormalizedTokenMetadata {
        let network = "robinhood";
        let chain_id = 4663;
        crate::metadata::NormalizedTokenMetadata {
            source,
            source_locator: format!("{}:{network}:{chain_id}:{address}", source.as_str()),
            token_key_id: manager_contracts::TokenKeyId::new(network, chain_id, address).unwrap(),
            v4_currency_key: address.to_owned(),
            token_address: address.to_owned(),
            token_symbol: symbol.to_owned(),
            token_name: Some(symbol.to_owned()),
            token_decimals: 18,
            token_kind: "erc20".to_owned(),
            is_native: false,
            is_wrapped_native: false,
            is_stable: false,
            admission_source: format!("bootstrap:{}", source.as_str()),
            metadata_source: "fixture".to_owned(),
            metadata_checksum: format!("sha256:{checksum_suffix:0>64}"),
            source_json: "{}".to_owned(),
            first_seen_at: None,
        }
    }

    fn pool(
        network: &str,
        chain_id: u64,
        token0: &str,
        token1: &str,
        pool_id: &str,
    ) -> Vec<crate::sources::V4PoolCurrencyReference> {
        vec![
            crate::sources::V4PoolCurrencyReference {
                network: network.to_owned(),
                chain_id,
                pool_id: pool_id.to_owned(),
                side: "token0",
                token_address: Some(token0.to_owned()),
                source_locator: format!("pool:{pool_id}:token0"),
            },
            crate::sources::V4PoolCurrencyReference {
                network: network.to_owned(),
                chain_id,
                pool_id: pool_id.to_owned(),
                side: "token1",
                token_address: Some(token1.to_owned()),
                source_locator: format!("pool:{pool_id}:token1"),
            },
        ]
    }

    fn mainnet_policy(core: &[&str], test: &[&str]) -> crate::classification::ClassificationPolicy {
        crate::classification::ClassificationPolicy::new(
            "robinhood",
            4663,
            core.iter().map(|address| (*address).to_owned()),
            test.iter().map(|address| (*address).to_owned()),
        )
    }

    fn membership_libraries(
        token: &crate::classification::ClassifiedToken,
    ) -> Vec<crate::model::LibraryKey> {
        token
            .memberships
            .iter()
            .map(|membership| membership.library)
            .collect()
    }

    fn economic_classification() -> crate::classification::ClassificationResult {
        let records = vec![
            token(CORE, "USDG", MetadataSourceKind::CoreAllowlist, "core"),
            token(STOCK, "AAPL", MetadataSourceKind::StockRegistry, "stock"),
            token(EXTENSION, "MID", MetadataSourceKind::ChainTokens, "mid"),
        ];
        classify_tokens(
            &records,
            &[
                pool("robinhood", 4663, STOCK, CORE, "pool-stock"),
                pool("robinhood", 4663, EXTENSION, CORE, "pool-mid"),
            ]
            .concat(),
            &mainnet_policy(&[CORE], &[]),
        )
    }

    fn libraries_of(
        result: &crate::classification::ClassificationResult,
        address: &str,
    ) -> Vec<LibraryKey> {
        let token = result
            .tokens
            .iter()
            .find(|token| token.token.token_address.eq_ignore_ascii_case(address))
            .expect("token present");
        membership_libraries(token)
    }

    #[test]
    fn a_valued_stock_token_stays_in_the_stock_library() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default()
            .with_stock_valuation(STOCK, Some(150_000_000))
            .with_extension_depth(EXTENSION, 50_000_000);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );
        assert!(outcome.excluded_stock_tokens.is_empty());
        assert!(outcome.excluded_extension_hops.is_empty());
        assert!(outcome.pending_stock_tokens.is_empty());
        assert!(libraries_of(&classification, STOCK).contains(&crate::model::LibraryKey::Stocks));
        assert!(
            libraries_of(&classification, EXTENSION).contains(&crate::model::LibraryKey::Extension)
        );
    }

    #[test]
    fn a_stock_token_without_a_usdg_path_leaves_the_stock_library() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default()
            .with_stock_valuation(STOCK, None)
            .with_extension_depth(EXTENSION, 50_000_000);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );
        assert_eq!(outcome.excluded_stock_tokens.len(), 1);
        assert_eq!(
            outcome.excluded_stock_tokens[0].verdict,
            crate::usdg_economic::EconomicVerdict::NoUsdgValuation
        );
        assert!(outcome.pending_stock_tokens.is_empty());
        assert!(!libraries_of(&classification, STOCK).contains(&crate::model::LibraryKey::Stocks));
    }

    /// R-12 regression: removing a membership from the classification is
    /// not enough. The row that is already in the library table would
    /// simply stay there, still `admitted`, and the database would keep
    /// advertising a token the gate has just rejected.
    ///
    /// The gate therefore has to produce a withdrawal write: a `blocked`
    /// row with the reason attached. The row stays in the table, so the
    /// decision is auditable and a later refresh can reverse it, but it
    /// no longer counts as an admitted member.
    #[test]
    fn an_excluded_stock_token_produces_a_blocked_withdrawal_write() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default()
            .with_stock_valuation(STOCK, None)
            .with_extension_depth(EXTENSION, 50_000_000);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );

        assert_eq!(outcome.withdrawn_stock_writes.len(), 1);
        let write = &outcome.withdrawn_stock_writes[0];
        assert_eq!(
            write.admission_status,
            crate::model::AdmissionStatus::Blocked
        );
        let reason = write.blocked_reason.clone().unwrap_or_default();
        assert!(
            reason.contains("no exchange path to USDG"),
            "the withdrawal has to say why: {reason}"
        );
        assert_eq!(
            write.token_address.to_ascii_lowercase(),
            STOCK.to_ascii_lowercase()
        );

        // The extension hop was not excluded, so it must not produce a
        // withdrawal write of its own.
        assert!(outcome.withdrawn_extension_writes.is_empty());
    }

    /// R-12 regression: a token that is only *pending* must not be
    /// blocked. A pending token has not been measured yet, and blocking
    /// it would turn "nobody asked" into "the answer is no".
    #[test]
    fn a_pending_stock_token_never_produces_a_withdrawal_write() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default().with_extension_depth(EXTENSION, 50_000_000);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );

        assert_eq!(outcome.pending_stock_tokens.len(), 1);
        assert!(
            outcome.withdrawn_stock_writes.is_empty(),
            "a pending token must stay untouched so a later refresh can admit it"
        );
    }

    /// R-12 regression: an extension hop that cannot carry a minimum
    /// position is withdrawn from the extension library, and the
    /// withdrawal is routed to the extension table.
    #[test]
    fn an_excluded_extension_hop_produces_an_extension_withdrawal_write() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default()
            .with_stock_valuation(STOCK, Some(100_000_000))
            .with_extension_depth(EXTENSION, 1);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );

        assert_eq!(outcome.excluded_extension_hops.len(), 1);
        assert_eq!(outcome.withdrawn_extension_writes.len(), 1);
        assert_eq!(
            outcome.withdrawn_extension_writes[0].admission_status,
            crate::model::AdmissionStatus::Blocked
        );
        assert!(outcome.withdrawn_stock_writes.is_empty());
    }

    #[test]
    fn an_unmeasured_stock_token_is_pending_and_never_excluded() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default().with_extension_depth(EXTENSION, 50_000_000);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );
        assert!(outcome.excluded_stock_tokens.is_empty());
        assert_eq!(outcome.pending_stock_tokens.len(), 1);
        assert_eq!(
            outcome.pending_stock_tokens[0].verdict,
            crate::usdg_economic::EconomicVerdict::NotMeasured
        );
        assert!(!libraries_of(&classification, STOCK).contains(&crate::model::LibraryKey::Stocks));
    }

    #[test]
    fn only_the_stock_library_requires_a_usdg_exchange_path() {
        // A stock token that cannot be exchanged into USDG has no economic
        // meaning for the arbitrage book, so it must not reach the stock
        // library and therefore must not reach the fund pool either.
        assert!(library_requires_usdg_exchange(LibraryKey::Stocks));

        // The core library carries the venue's own quote and collateral
        // assets, and the test library carries fixtures. Neither is gated on
        // a market measurement.
        assert!(!library_requires_usdg_exchange(LibraryKey::Core));
        assert!(!library_requires_usdg_exchange(LibraryKey::Test));

        // An extension hop needs capacity, not a valuation of its own. Its
        // relationship to USDG holds transitively, because the hop is
        // reached through a stock or core token that is itself USDG
        // reachable.
        assert!(!library_requires_usdg_exchange(LibraryKey::Extension));
    }

    #[test]
    fn a_core_token_without_a_usdg_path_keeps_its_membership() {
        // The gate must never touch the core or test libraries, even when no
        // measurement exists for them.
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default()
            .with_stock_valuation(STOCK, Some(150_000_000))
            .with_extension_depth(EXTENSION, 50_000_000);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );
        assert!(outcome.excluded_stock_tokens.is_empty());
        assert!(libraries_of(&classification, CORE).contains(&LibraryKey::Core));
    }

    #[test]
    fn an_extension_hop_that_cannot_carry_a_minimum_position_is_excluded() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default()
            .with_stock_valuation(STOCK, Some(150_000_000))
            .with_extension_depth(EXTENSION, 1);
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );
        assert_eq!(outcome.excluded_extension_hops.len(), 1);
        assert_eq!(
            outcome.excluded_extension_hops[0].verdict,
            crate::usdg_economic::EconomicVerdict::InsufficientPoolDepth
        );
        assert!(!libraries_of(&classification, EXTENSION)
            .contains(&crate::model::LibraryKey::Extension));
        // The stock membership is untouched: excluding an intermediate hop
        // must never remove the token that is being routed.
        assert!(libraries_of(&classification, STOCK).contains(&crate::model::LibraryKey::Stocks));
    }

    #[test]
    fn an_extension_hop_with_unmeasured_depth_is_pending_and_never_excluded() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default().with_stock_valuation(STOCK, Some(150_000_000));
        let outcome = apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );
        assert!(outcome.excluded_extension_hops.is_empty());
        assert!(
            libraries_of(&classification, EXTENSION).contains(&crate::model::LibraryKey::Extension)
        );
    }

    #[test]
    fn the_gate_never_adds_a_membership() {
        let mut classification = economic_classification();
        let evidence = EconomicEvidence::default()
            .with_stock_valuation(STOCK, Some(150_000_000))
            .with_extension_depth(EXTENSION, 50_000_000);
        let before: BTreeMap<String, Vec<LibraryKey>> = classification
            .tokens
            .iter()
            .map(|token| {
                (
                    token.token.token_address.clone(),
                    membership_libraries(token),
                )
            })
            .collect();
        apply_economic_gate(
            &mut classification,
            &evidence,
            &crate::usdg_economic::EconomicPolicy::default(),
        );
        let after: BTreeMap<String, Vec<LibraryKey>> = classification
            .tokens
            .iter()
            .map(|token| {
                (
                    token.token.token_address.clone(),
                    membership_libraries(token),
                )
            })
            .collect();
        for (address, libraries) in before {
            let current = after.get(&address).expect("token present");
            assert!(
                current.len() <= libraries.len(),
                "the gate must never add a membership for {address}"
            );
        }
    }
}
