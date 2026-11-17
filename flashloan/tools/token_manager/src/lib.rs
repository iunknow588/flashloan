pub mod admission;
pub mod audit;
pub mod classification;
pub mod economic_gate;
pub mod manager_audit;
pub mod metadata;
pub mod model;
pub mod prune;
pub mod query;
pub mod quote_asset;
pub mod simulation;
pub mod sources;
pub mod store;
pub mod usdg_economic;
pub mod usdg_evidence;
pub mod valuation;
pub mod valuation_cli;
pub mod valuation_freshness;
pub mod valuation_store;
#[cfg(test)]
#[path = "valuation_tests.rs"]
mod valuation_tests;

pub use admission::{
    change_admission, AdmissionChangeOutcome, AdmissionChangeRequest, AdmissionChangeResult,
    AdmissionError,
};
pub use audit::{
    audit_library_memberships, proposed_memberships_from_classification,
    read_target_schema_and_memberships_read_only, AuditError, ConflictAudit, ExistingMembership,
    LibraryAdmissionAudit, MembershipChangeSample, MembershipDiff, PrimaryLibraryConflict,
    ProposedMembership, TargetSchemaInfo,
};
pub use classification::{
    classify_tokens, classify_tokens_with_liquidity, pool_is_liquidity_qualified,
    testnet_test_token_addresses, ClassificationIssue, ClassificationIssueCode,
    ClassificationPolicy, ClassificationReason, ClassificationResult, ClassifiedToken,
    DuplicateSymbolGroup, ExtensionAnchor, LibraryMembership, MAINNET_TEST_TOKEN_CANDIDATES,
    TESTNET_TEST_TOKEN_ADDRESSES,
};
pub use economic_gate::{
    apply_economic_gate, EconomicEvidence, EconomicExclusion, EconomicGateResult,
};
pub use manager_audit::{
    audit_manager_database, AdmissionEventAudit, AdmissionSourceCount, DuplicateTokenSymbol,
    ManagerAuditError, PrimaryLibraryConflictAudit, TokenLibraryAudit, TokenLibraryStateAudit,
    TokenManagerAudit, TokenStatusCounts,
};
pub use metadata::{
    is_placeholder_symbol, normalize_metadata, normalize_metadata_batch, token_metadata_checksum,
    MetadataAuditCode, MetadataAuditError, MetadataNormalizationResult, MetadataSourceKind,
    NormalizedTokenMetadata, RawTokenMetadata, ZERO_ADDRESS,
};
pub use model::{AdmissionStatus, LibraryKey, TokenWrite};
pub use prune::{
    prune_incomplete_tokens, render_prune_report, IncompleteReason, IncompleteToken, PruneCounts,
    PruneError, PruneReport,
};
pub use query::{
    read_library_state, read_library_tokens, resolve_token_symbol, ResolvedToken,
    TokenLibraryMembership, TokenLibraryPage, TokenLibraryState, TokenQueryError,
    TokenSymbolResolution, TokenView,
};
pub use quote_asset::{
    quote_asset_for, restate_floor_in_quote_units, QuoteAsset, QuoteAssetKind,
    NATIVE_COIN_ADDRESS, QUOTE_ASSET_USDG_ADDRESS,
};
pub use simulation::{
    simulate_bootstrap_in_temporary_database, BootstrapMigrationEvidence, BootstrapSimulationError,
    BootstrapSimulationGates, BootstrapSimulationReport, LibrarySimulationEvidence,
};
pub use sources::{
    core4_allowlist, mainnet_core4_allowlist, open_read_only, read_chain_tokens,
    read_chain_tokens_read_only,
    read_stock_registry, read_v4_pool_currency_references,
    read_v4_pool_currency_references_read_only, read_v4_pool_liquidity_evidence,
    read_v4_pool_liquidity_evidence_read_only, SourceError, V4PoolCurrencyReference,
    V4PoolLiquidityEvidence, ROBINHOOD_CHAIN_ID, ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
};
pub use store::{
    library_metadata_checksum, token_writes_metadata_checksum, write_library,
    write_library_dry_run, LibraryWriteResult, StoreError,
};
pub use usdg_economic::{
    assert_economic_policy_matches_file, assert_usdg_peg_policy_matches_file,
    assert_usdg_peg_policy_matches_path, assess_extension_hop, assess_token, assess_tokens,
    economic_policy, extension_library_admission, stock_library_admission, usdg_to_decimal,
    EconomicAssessment, EconomicPolicy, EconomicVerdict, ExtensionAdmissionReport,
    StockAdmissionReport, TokenEconomics, UsdgPegPolicy, CANONICAL_USDG_ADDRESS as USDG_ADDRESS,
    COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW, COMPILED_MIN_POOL_DEPTH_USDG_RAW,
    COMPILED_USDG_PEG_DEVIATION_TOLERANCE_BPS, COMPILED_USDG_PEG_RATIO, USDG_DECIMALS,
};
pub use usdg_evidence::{
    build_evidence, depth_map, read_measured_depths, read_valuation_report,
    render_report as render_evidence_report, EvidenceReport, MeasuredDepth,
};
pub use valuation::{
    build_quote_request, configured_api_key, parse_quote_response, quote_token_to_usdg,
    raw_amount_from_decimal, raw_to_decimal, summarize_batch, value_tokens_to_usdg_batch,
    value_tokens_to_usdg_batch_with_concurrency, TokenValuation, ValuationBatchReport,
    ValuationConfig, ValuationFailureKind, ValuationTarget, ValuationVerdict,
    CANONICAL_USDG_ADDRESS, CANONICAL_USDG_DECIMALS, TRADING_API_KEY_ENV_VARS,
    UNISWAP_TRADING_API_QUOTE_URL,
};
pub use valuation_freshness::{
    approximation_may_promote_to_negative, current_epoch_seconds, effective_verdict,
    evaluate_freshness, evaluate_freshness_now, evaluate_stored_valuations, EffectiveVerdict,
    StaleValuation, ValuationApproximation, ValuationExpiryReport, ValuationFreshness,
    ValuationFreshnessVerdict, ValuationUse, MAGNITUDE_MAX_AGE, MEMBERSHIP_MAX_AGE,
};
pub use valuation_store::{
    audit_valuation_expiry, land_valuation_report, read_stored_valuations, render_landing,
    ValuationLandingResult,
};

use serde_json::json;
use serde_json::Value;

pub fn framework_status() -> Value {
    json!({
        "tool": "token-manager",
        "m0SchemaReady": true,
        "storageReady": true,
        "metadataNormalizationReady": true,
        "classificationReady": true,
        "libraryAdmissionAuditReady": true,
        "bootstrapSimulationReady": true,
        "admissionWriteApiReady": true,
        "tokenValuationReady": true,
        "usdgValuationSource": "uniswap_trading_api_quote",
        "usdgEconomicAdmissionReady": true,
        "usdgEconomicGateReady": true,
        "writeEnabled": false,
        "note": "M0 schema, transactional storage, metadata normalization, classification, admission audit, temporary-database simulation and guarded token admission writes are available; production data import remains a separate apply step.",
        "libraries": [
            {"key": LibraryKey::Core.as_str(), "table": LibraryKey::Core.table_name()},
            {"key": LibraryKey::Stocks.as_str(), "table": LibraryKey::Stocks.table_name()},
            {"key": LibraryKey::Extension.as_str(), "table": LibraryKey::Extension.table_name()},
            {"key": LibraryKey::Test.as_str(), "table": LibraryKey::Test.table_name()}
        ],
        "storageContract": [
            "a token_key_id has at most one primary library: core, stocks or test",
            "extension is a derived membership library and can overlap primary libraries",
            "library versions advance atomically only when semantic rows change",
            "library checksums use ordered canonical JSON and exclude time-only fields"
        ],
        "metadataContract": [
            "addresses and V4 currency keys are normalized lowercase",
            "symbols are trimmed and uppercased but never guessed from addresses",
            "placeholder symbols, invalid decimals, missing sources and invalid JSON are rejected"
        ],
        "classificationContract": [
            "one address merges to one primary library with core > stock > chain precedence",
            "core, stock and test primary membership use explicit address allowlists",
            "test contains only TZ; TA and TB can be extension members anchored by TZ",
            "extension members require metadata plus a direct liquidity-qualified V4 pool to core, stock or TZ",
            "extension members remain candidate_only until review admits them",
            "duplicate symbols are reported and never used for classification"
        ],
        "valuationContract": [
            "a route_found verdict requires a quote output amount from the Trading API",
            "no_route is a real negative; api_unavailable and api_error stay unknown",
            "the quote target is the canonical USDG address, never a symbol match",
            "route internals are discarded; only the leg count is retained",
            "valuation never gates pool depth, capacity or broadcast"
        ],
        "economicAdmissionContract": [
            "the stock library admits only tokens with a measured USDG valuation",
            "an unmeasured token is pending, never excluded for being unmeasured",
            "only a no_route verdict is a genuine negative; api_unavailable and api_error stay unknown",
            "the extension library admits only hops that can carry a minimum economic position",
            "the extension library does not require a USDG valuation of its own",
            "all thresholds are denominated in USDG, the venue quote asset",
            "the gate only removes memberships; it never adds one",
            "the gate runs before a library is materialized, so an unvaluable stock token is never written"
        ]
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn framework_status_is_read_only_at_the_http_boundary() {
        let status = framework_status();
        assert_eq!(status["writeEnabled"], false);
        assert_eq!(status["m0SchemaReady"], true);
        assert_eq!(status["storageReady"], true);
        assert_eq!(status["metadataNormalizationReady"], true);
        assert_eq!(status["classificationReady"], true);
        assert_eq!(status["libraryAdmissionAuditReady"], true);
        assert_eq!(status["bootstrapSimulationReady"], true);
        assert_eq!(status["admissionWriteApiReady"], true);
        assert_eq!(status["tokenValuationReady"], true);
        assert_eq!(status["usdgEconomicAdmissionReady"], true);
        assert_eq!(status["usdgEconomicGateReady"], true);
        assert_eq!(status["writeEnabled"], false);
    }
}
