pub mod admission;
pub mod audit;
pub mod classification;
pub mod manager_audit;
pub mod metadata;
pub mod model;
pub mod query;
pub mod simulation;
pub mod sources;
pub mod store;

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
pub use query::{
    read_library_state, read_library_tokens, resolve_token_symbol, ResolvedToken,
    TokenLibraryMembership, TokenLibraryPage, TokenLibraryState, TokenQueryError,
    TokenSymbolResolution, TokenView,
};
pub use simulation::{
    simulate_bootstrap_in_temporary_database, BootstrapMigrationEvidence, BootstrapSimulationError,
    BootstrapSimulationGates, BootstrapSimulationReport, LibrarySimulationEvidence,
};
pub use sources::{
    mainnet_core4_allowlist, open_read_only, read_chain_tokens, read_chain_tokens_read_only,
    read_stock_registry, read_v4_pool_currency_references,
    read_v4_pool_currency_references_read_only, read_v4_pool_liquidity_evidence,
    read_v4_pool_liquidity_evidence_read_only, SourceError, V4PoolCurrencyReference,
    V4PoolLiquidityEvidence, ROBINHOOD_CHAIN_ID, ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
};
pub use store::{
    library_metadata_checksum, write_library, write_library_dry_run, LibraryWriteResult, StoreError,
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
        assert_eq!(status["writeEnabled"], false);
    }
}
