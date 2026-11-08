use manager_contracts::normalize_address;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use token_manager::{
    audit_library_memberships, classify_tokens_with_liquidity, framework_status,
    mainnet_core4_allowlist, normalize_metadata_batch, pool_is_liquidity_qualified,
    proposed_memberships_from_classification, read_chain_tokens_read_only, read_stock_registry,
    read_target_schema_and_memberships_read_only, read_v4_pool_currency_references_read_only,
    read_v4_pool_liquidity_evidence_read_only, simulate_bootstrap_in_temporary_database,
    ClassificationPolicy, MetadataAuditError, MetadataSourceKind, NormalizedTokenMetadata,
    RawTokenMetadata, V4PoolCurrencyReference, V4PoolLiquidityEvidence,
    MAINNET_TEST_TOKEN_CANDIDATES, ROBINHOOD_CHAIN_ID, ROBINHOOD_NETWORK,
};

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    if arguments.first().map(String::as_str) == Some("audit-metadata") {
        return match metadata_audit(&arguments[1..]) {
            Ok(report) => {
                println!("{report}");
                ExitCode::SUCCESS
            }
            Err(error) => {
                eprintln!("{error}");
                ExitCode::FAILURE
            }
        };
    }
    if arguments.first().map(String::as_str) == Some("audit-library") {
        return match library_admission_audit(&arguments[1..]) {
            Ok(report) => {
                println!("{report}");
                ExitCode::SUCCESS
            }
            Err(error) => {
                eprintln!("{error}");
                ExitCode::FAILURE
            }
        };
    }
    if arguments.first().map(String::as_str) == Some("bootstrap-simulate") {
        return match bootstrap_simulate(&arguments[1..]) {
            Ok(report) => {
                println!("{report}");
                ExitCode::SUCCESS
            }
            Err(error) => {
                eprintln!("{error}");
                ExitCode::FAILURE
            }
        };
    }

    println!("{}", framework_status());
    ExitCode::SUCCESS
}

fn bootstrap_simulate(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let source_database = flag(arguments, "--source-db")
        .map(PathBuf::from)
        .ok_or("bootstrap-simulate requires --source-db <legacy read-only sqlite path>")?;
    let registry = flag(arguments, "--registry")
        .map(PathBuf::from)
        .ok_or("bootstrap-simulate requires --registry <stock registry JSON>")?;

    let core = mainnet_core4_allowlist();
    let chain_tokens =
        read_chain_tokens_read_only(&source_database, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)?;
    let stocks = read_stock_registry(&registry, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)?;
    let pool_references = read_v4_pool_currency_references_read_only(
        &source_database,
        ROBINHOOD_NETWORK,
        ROBINHOOD_CHAIN_ID,
    )?;
    let pool_liquidity = read_v4_pool_liquidity_evidence_read_only(
        &source_database,
        ROBINHOOD_NETWORK,
        ROBINHOOD_CHAIN_ID,
    )?;
    let mut records = Vec::with_capacity(core.len() + chain_tokens.len() + stocks.len());
    records.extend(core);
    records.extend(chain_tokens);
    records.extend(stocks);
    let normalized = normalize_metadata_batch(records);
    let classification = classify_tokens_with_liquidity(
        &normalized.accepted,
        &pool_references,
        &pool_liquidity,
        &ClassificationPolicy::mainnet(),
    );
    let checkpoint_payload = json!({
        "classification": &classification.tokens,
        "classificationIssues": &classification.issues,
        "duplicateSymbols": &classification.duplicate_symbols,
        "sourceOverrides": classification.source_overrides
    });
    let source_checkpoint = format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&checkpoint_payload)?))
    );
    let report = simulate_bootstrap_in_temporary_database(&classification, &source_checkpoint)?;
    let output = json!({
        "source": {
            "database": source_database.display().to_string(),
            "registry": registry.display().to_string(),
            "readOnly": true,
            "normalizedAcceptedTokens": normalized.accepted.len(),
            "normalizedRejectedRecords": normalized.errors.len(),
            "v4PoolReferences": pool_references.len(),
            "v4PoolLiquidityEvidence": pool_liquidity.len()
        },
        "simulation": report,
        "mainnetMigrationOrApply": false
    });
    Ok(serde_json::to_string_pretty(&output)?)
}

fn library_admission_audit(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let source_database = flag(arguments, "--source-db")
        .map(PathBuf::from)
        .ok_or("audit-library requires --source-db <legacy read-only sqlite path>")?;
    let target_database = flag(arguments, "--target-db")
        .map(PathBuf::from)
        .ok_or("audit-library requires --target-db <manager read-only sqlite path>")?;
    let registry = flag(arguments, "--registry")
        .map(PathBuf::from)
        .ok_or("audit-library requires --registry <stock registry JSON>")?;

    let core = mainnet_core4_allowlist();
    let chain_tokens =
        read_chain_tokens_read_only(&source_database, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)?;
    let stocks = read_stock_registry(&registry, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)?;
    let pool_references = read_v4_pool_currency_references_read_only(
        &source_database,
        ROBINHOOD_NETWORK,
        ROBINHOOD_CHAIN_ID,
    )?;
    let pool_liquidity = read_v4_pool_liquidity_evidence_read_only(
        &source_database,
        ROBINHOOD_NETWORK,
        ROBINHOOD_CHAIN_ID,
    )?;
    let mut records = Vec::with_capacity(core.len() + chain_tokens.len() + stocks.len());
    records.extend(core);
    records.extend(chain_tokens.clone());
    records.extend(stocks);
    let normalized = normalize_metadata_batch(records);
    let policy = ClassificationPolicy::mainnet();
    let classification = classify_tokens_with_liquidity(
        &normalized.accepted,
        &pool_references,
        &pool_liquidity,
        &policy,
    );
    let proposed = proposed_memberships_from_classification(&classification);
    let extension_input_diagnostics = core_anchor_input_diagnostics(
        &pool_references,
        &pool_liquidity,
        &normalized.accepted,
        &normalized.errors,
        &chain_tokens,
        &policy.core_addresses,
    );
    let (target_schema, existing) = read_target_schema_and_memberships_read_only(
        &target_database,
        ROBINHOOD_NETWORK,
        ROBINHOOD_CHAIN_ID,
    )?;
    let audit = audit_library_memberships(&proposed, &existing);

    let mut rejected_by_code = BTreeMap::<&str, usize>::new();
    let mut rejected_samples_by_code = BTreeMap::new();
    for error in &normalized.errors {
        *rejected_by_code.entry(error.code.as_str()).or_default() += 1;
        rejected_samples_by_code
            .entry(error.code.as_str())
            .or_insert(error);
    }
    let primary_conflict_count = audit.conflicts.proposed_primary_conflicts.len()
        + audit.conflicts.existing_primary_conflicts.len()
        + audit.conflicts.proposal_vs_existing_primary_conflicts.len();
    let liquidity_qualified_blocked = extension_input_diagnostics["union"]
        ["liquidityQualifiedMetadataRejectedCounterparts"]
        .as_u64()
        .unwrap_or(0);
    let report = json!({
        "mode": "read_only_library_admission_diff_audit",
        "network": ROBINHOOD_NETWORK,
        "chainId": ROBINHOOD_CHAIN_ID,
        "sourceDatabase": source_database.display().to_string(),
        "targetDatabase": target_database.display().to_string(),
        "registry": registry.display().to_string(),
        "targetSchema": target_schema,
        "classification": {
            "uniqueTokens": classification.tokens.len(),
            "primaryLibraries": BTreeMap::from([
                ("core", classification.primary_count_for_library(token_manager::LibraryKey::Core)),
                ("stocks", classification.primary_count_for_library(token_manager::LibraryKey::Stocks)),
                ("test", classification.primary_count_for_library(token_manager::LibraryKey::Test)),
            ]),
            "extensionMembers": classification.extension_member_count(),
            "extensionOnly": classification
                .tokens
                .iter()
                .filter(|token| token.extension_member() && token.primary_library.is_none())
                .count(),
            "extensionOverlap": classification
                .tokens
                .iter()
                .filter(|token| token.extension_member() && token.primary_library.is_some())
                .count()
        },
        "sourceRejection": {
            "blockedRecordCount": normalized.errors.len(),
            "byCode": rejected_by_code,
            "samplesByCode": rejected_samples_by_code,
            "liquidityQualifiedBlockedCandidateCount": liquidity_qualified_blocked,
            "extensionInputDiagnostics": extension_input_diagnostics
        },
        "admissionAudit": audit,
        "gates": {
            "targetSchemaState": target_schema.state,
            "schemaComplete": target_schema.is_complete(),
            "primaryConflictCount": primary_conflict_count,
            "proposedInsertCount": audit.diff.inserted,
            "readyForDryRun": target_schema.is_complete() && primary_conflict_count == 0,
            "readyForApply": false,
            "mainnetMigrationOrApply": false,
            "businessWritesPerformed": false
        }
    });
    Ok(serde_json::to_string_pretty(&report)?)
}

fn metadata_audit(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let database = flag(arguments, "--db")
        .map(PathBuf::from)
        .ok_or("audit-metadata requires --db <read-only sqlite path>")?;
    let registry = flag(arguments, "--registry")
        .map(PathBuf::from)
        .ok_or("audit-metadata requires --registry <stock registry JSON>")?;

    let core = mainnet_core4_allowlist();
    let chain_tokens =
        read_chain_tokens_read_only(&database, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)?;
    let stocks = read_stock_registry(&registry, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)?;
    let pool_references = read_v4_pool_currency_references_read_only(
        &database,
        ROBINHOOD_NETWORK,
        ROBINHOOD_CHAIN_ID,
    )?;
    let pool_liquidity = read_v4_pool_liquidity_evidence_read_only(
        &database,
        ROBINHOOD_NETWORK,
        ROBINHOOD_CHAIN_ID,
    )?;

    let source_counts = json!({
        "coreAllowlist": core.len(),
        "chainTokens": chain_tokens.len(),
        "stockRegistry": stocks.len(),
        "v4PoolCurrencyReferences": pool_references.len()
    });
    let mut records = Vec::with_capacity(core.len() + chain_tokens.len() + stocks.len());
    records.extend(core);
    records.extend(chain_tokens.clone());
    records.extend(stocks);
    let normalized = normalize_metadata_batch(records);
    let classification_policy = ClassificationPolicy::mainnet();
    let classification = classify_tokens_with_liquidity(
        &normalized.accepted,
        &pool_references,
        &pool_liquidity,
        &classification_policy,
    );
    let extension_input_diagnostics = core_anchor_input_diagnostics(
        &pool_references,
        &pool_liquidity,
        &normalized.accepted,
        &normalized.errors,
        &chain_tokens,
        &classification_policy.core_addresses,
    );
    let mut qualified_pools_by_depth = BTreeMap::<&str, usize>::new();
    for pool in pool_liquidity
        .iter()
        .filter(|pool| pool_is_liquidity_qualified(pool))
    {
        *qualified_pools_by_depth
            .entry(pool.depth_status.as_deref().unwrap_or("missing"))
            .or_default() += 1;
    }

    let mut accepted_by_source = BTreeMap::<&str, usize>::new();
    for token in &normalized.accepted {
        *accepted_by_source.entry(token.source.as_str()).or_default() += 1;
    }
    let mut errors_by_code = BTreeMap::<&str, usize>::new();
    for error in &normalized.errors {
        *errors_by_code.entry(error.code.as_str()).or_default() += 1;
    }
    let mut error_samples_by_code = BTreeMap::new();
    for error in &normalized.errors {
        error_samples_by_code
            .entry(error.code.as_str())
            .or_insert(error);
    }
    let primary_libraries = BTreeMap::from([
        (
            "core",
            classification.primary_count_for_library(token_manager::LibraryKey::Core),
        ),
        (
            "stocks",
            classification.primary_count_for_library(token_manager::LibraryKey::Stocks),
        ),
        (
            "test",
            classification.primary_count_for_library(token_manager::LibraryKey::Test),
        ),
    ]);
    let library_memberships = BTreeMap::from([
        (
            "core",
            classification.membership_count_for_library(token_manager::LibraryKey::Core),
        ),
        (
            "stocks",
            classification.membership_count_for_library(token_manager::LibraryKey::Stocks),
        ),
        (
            "extension",
            classification.membership_count_for_library(token_manager::LibraryKey::Extension),
        ),
        (
            "test",
            classification.membership_count_for_library(token_manager::LibraryKey::Test),
        ),
    ]);
    let extension_overlap_count = classification
        .tokens
        .iter()
        .filter(|token| token.extension_member() && token.primary_library.is_some())
        .count();
    let extension_only_count = classification
        .tokens
        .iter()
        .filter(|token| token.extension_member() && token.primary_library.is_none())
        .count();
    let extension_overlap_by_primary_library = BTreeMap::from([
        (
            "core",
            classification
                .tokens
                .iter()
                .filter(|token| {
                    token.extension_member()
                        && token.primary_library == Some(token_manager::LibraryKey::Core)
                })
                .count(),
        ),
        (
            "stocks",
            classification
                .tokens
                .iter()
                .filter(|token| {
                    token.extension_member()
                        && token.primary_library == Some(token_manager::LibraryKey::Stocks)
                })
                .count(),
        ),
        (
            "test",
            classification
                .tokens
                .iter()
                .filter(|token| {
                    token.extension_member()
                        && token.primary_library == Some(token_manager::LibraryKey::Test)
                })
                .count(),
        ),
    ]);
    let mut extension_anchor_distribution = BTreeMap::<&str, usize>::new();
    for token in &classification.tokens {
        for anchor in &token.extension_anchors {
            *extension_anchor_distribution
                .entry(anchor.as_str())
                .or_default() += 1;
        }
    }
    let mut classification_issues_by_code = BTreeMap::<&str, usize>::new();
    for issue in &classification.issues {
        *classification_issues_by_code
            .entry(issue.code.as_str())
            .or_default() += 1;
    }
    let duplicate_symbol_samples = classification
        .duplicate_symbols
        .iter()
        .take(20)
        .collect::<Vec<_>>();
    let mainnet_test_token_candidates = MAINNET_TEST_TOKEN_CANDIDATES
        .iter()
        .map(|(symbol, address, source)| {
            let classified = classification
                .tokens
                .iter()
                .find(|token| token.token.token_address == *address);
            json!({
                "symbol": symbol,
                "address": address,
                "source": source,
                "primaryLibrary": classified
                    .and_then(|token| token.primary_library)
                    .map(|library| library.as_str()),
                "memberships": classified
                    .map(|token| {
                        token
                            .memberships
                            .iter()
                            .map(|membership| json!({
                                "libraryKey": membership.library.as_str(),
                                "admissionStatus": membership.admission_status.as_str(),
                                "reason": membership.reason
                            }))
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default(),
                "extensionAnchors": classified
                    .map(|token| token.extension_anchors.clone())
                    .unwrap_or_default(),
                "classificationPresent": classified.is_some()
            })
        })
        .collect::<Vec<_>>();
    let report = json!({
        "mode": "read_only_metadata_and_classification_audit",
        "network": ROBINHOOD_NETWORK,
        "chainId": ROBINHOOD_CHAIN_ID,
        "database": database.display().to_string(),
        "registry": registry.display().to_string(),
        "sourceCounts": source_counts,
        "normalization": {
            "accepted": normalized.accepted.len(),
            "rejected": normalized.errors.len(),
            "acceptedBySource": accepted_by_source,
            "errorsByCode": errors_by_code,
            "errorSamplesByCode": error_samples_by_code
        },
        "classification": {
            "uniqueTokens": classification.tokens.len(),
            "primaryLibraries": primary_libraries,
            "libraryMemberships": library_memberships,
            "extensionMembers": classification.extension_member_count(),
            "extensionOverlap": extension_overlap_count,
            "extensionOverlapByPrimaryLibrary": extension_overlap_by_primary_library,
            "extensionOnly": extension_only_count,
            "extensionAnchorDistribution": extension_anchor_distribution,
            "extensionInputDiagnostics": extension_input_diagnostics,
            "unclassified": classification.unclassified_count(),
            "sourceOverrides": classification.source_overrides,
            "duplicateSymbolGroups": classification.duplicate_symbols.len(),
            "duplicateSymbolSamples": duplicate_symbol_samples,
            "issuesByCode": classification_issues_by_code,
            "mainnetTestTokenCandidates": mainnet_test_token_candidates
        },
        "liquidityEligibility": {
            "source": "v4_exchange_pools JOIN v4_exchange_pool_liquidity_snapshots",
            "gateEnabled": true,
            "poolCount": pool_liquidity.len(),
            "qualifiedPoolCount": pool_liquidity
                .iter()
                .filter(|pool| pool_is_liquidity_qualified(pool))
                .count(),
            "qualifiedPoolsByLegacyUsdDepthLabelDiagnosticOnly": qualified_pools_by_depth,
            "usdDepthQualifiedPoolCountDiagnosticOnly": pool_liquidity
                .iter()
                .filter(|pool| pool_is_liquidity_qualified(pool) && has_strict_depth(pool))
                .count(),
            "usdDepthRelationshipRequired": false,
            "rule": "fixed fee in 1..=3000; dynamic/special fee zero; zero hooks; has_liquidity=1 with positive liquidity_index; legacy USD depth labels are diagnostic only and do not gate admission"
        },
        "sourceKinds": [
            MetadataSourceKind::CoreAllowlist.as_str(),
            MetadataSourceKind::ChainTokens.as_str(),
            MetadataSourceKind::StockRegistry.as_str()
        ]
    });
    Ok(serde_json::to_string_pretty(&report)?)
}

fn core_anchor_input_diagnostics(
    pool_references: &[V4PoolCurrencyReference],
    pool_liquidity: &[V4PoolLiquidityEvidence],
    accepted: &[NormalizedTokenMetadata],
    errors: &[MetadataAuditError],
    chain_tokens: &[RawTokenMetadata],
    core_addresses: &BTreeSet<String>,
) -> serde_json::Value {
    let mut pools = BTreeMap::<String, (Option<String>, Option<String>)>::new();
    for reference in pool_references {
        let normalized = reference
            .token_address
            .as_deref()
            .and_then(|address| normalize_address(address).ok());
        let pool = pools
            .entry(reference.pool_id.clone())
            .or_insert((None, None));
        if reference.side == "token0" {
            pool.0 = normalized;
        } else {
            pool.1 = normalized;
        }
    }
    let liquidity_qualified_pool_ids = pool_liquidity
        .iter()
        .filter(|pool| pool_is_liquidity_qualified(pool))
        .map(|pool| pool.pool_id.as_str())
        .collect::<BTreeSet<_>>();
    let strict_depth_qualified_pool_ids = pool_liquidity
        .iter()
        .filter(|pool| pool_is_liquidity_qualified(pool) && has_strict_depth(pool))
        .map(|pool| pool.pool_id.as_str())
        .collect::<BTreeSet<_>>();

    let accepted_addresses = accepted
        .iter()
        .map(|token| token.token_address.as_str())
        .collect::<BTreeSet<_>>();
    let chain_tokens_by_address = chain_tokens
        .iter()
        .map(|token| (token.token_address.as_deref().unwrap_or(""), token))
        .collect::<BTreeMap<_, _>>();
    let mut errors_by_address = BTreeMap::<&str, BTreeSet<&str>>::new();
    for error in errors {
        if let Some(address) = error.token_address.as_deref() {
            errors_by_address
                .entry(address)
                .or_default()
                .insert(error.code.as_str());
        }
    }

    let mut raw_union = BTreeSet::<String>::new();
    let mut accepted_union = BTreeSet::<String>::new();
    let mut chain_token_row_union = BTreeSet::<String>::new();
    let mut symbol_available_union = BTreeSet::<String>::new();
    let mut decimals_available_union = BTreeSet::<String>::new();
    let mut symbol_and_decimals_available_union = BTreeSet::<String>::new();
    let mut liquidity_qualified_union = BTreeSet::<String>::new();
    let mut strict_depth_qualified_union = BTreeSet::<String>::new();
    let mut liquidity_qualified_accepted_union = BTreeSet::<String>::new();
    let mut anchors = BTreeMap::<String, serde_json::Value>::new();
    for anchor in core_addresses {
        let mut raw_counterparts = BTreeSet::<String>::new();
        let mut liquidity_qualified_counterparts = BTreeSet::<String>::new();
        let mut strict_depth_qualified_counterparts = BTreeSet::<String>::new();
        for (pool_id, (token0, token1)) in &pools {
            if token0.as_deref() == Some(anchor.as_str()) {
                if let Some(token) = token1 {
                    raw_counterparts.insert(token.clone());
                    if liquidity_qualified_pool_ids.contains(pool_id.as_str()) {
                        liquidity_qualified_counterparts.insert(token.clone());
                    }
                    if strict_depth_qualified_pool_ids.contains(pool_id.as_str()) {
                        strict_depth_qualified_counterparts.insert(token.clone());
                    }
                }
            }
            if token1.as_deref() == Some(anchor.as_str()) {
                if let Some(token) = token0 {
                    raw_counterparts.insert(token.clone());
                    if liquidity_qualified_pool_ids.contains(pool_id.as_str()) {
                        liquidity_qualified_counterparts.insert(token.clone());
                    }
                    if strict_depth_qualified_pool_ids.contains(pool_id.as_str()) {
                        strict_depth_qualified_counterparts.insert(token.clone());
                    }
                }
            }
        }

        let mut accepted_counterparts = BTreeSet::<String>::new();
        let mut rejected_by_code = BTreeMap::<&str, usize>::new();
        for address in &raw_counterparts {
            if accepted_addresses.contains(address.as_str()) {
                accepted_counterparts.insert(address.clone());
            } else if let Some(codes) = errors_by_address.get(address.as_str()) {
                for code in codes {
                    *rejected_by_code.entry(code).or_default() += 1;
                }
            }
        }
        let liquidity_qualified_accepted_counterparts = liquidity_qualified_counterparts
            .iter()
            .filter(|address| accepted_addresses.contains(address.as_str()))
            .cloned()
            .collect::<BTreeSet<_>>();
        let chain_token_row_counterparts = raw_counterparts
            .iter()
            .filter(|address| chain_tokens_by_address.contains_key(address.as_str()))
            .cloned()
            .collect::<BTreeSet<_>>();
        let symbol_available_counterparts = raw_counterparts
            .iter()
            .filter(|address| {
                chain_tokens_by_address
                    .get(address.as_str())
                    .and_then(|token| raw_metadata_fields(token).0)
                    .is_some()
            })
            .cloned()
            .collect::<BTreeSet<_>>();
        let decimals_available_counterparts = raw_counterparts
            .iter()
            .filter(|address| {
                chain_tokens_by_address
                    .get(address.as_str())
                    .and_then(|token| raw_metadata_fields(token).1)
                    .is_some()
            })
            .cloned()
            .collect::<BTreeSet<_>>();
        let symbol_and_decimals_available_counterparts = raw_counterparts
            .iter()
            .filter(|address| {
                chain_tokens_by_address
                    .get(address.as_str())
                    .is_some_and(|token| {
                        let (symbol, decimals) = raw_metadata_fields(token);
                        symbol.is_some() && decimals.is_some()
                    })
            })
            .cloned()
            .collect::<BTreeSet<_>>();
        let liquidity_qualified_rejected_count = liquidity_qualified_counterparts
            .len()
            .saturating_sub(liquidity_qualified_accepted_counterparts.len());
        raw_union.extend(raw_counterparts.iter().cloned());
        accepted_union.extend(accepted_counterparts.iter().cloned());
        chain_token_row_union.extend(chain_token_row_counterparts.iter().cloned());
        symbol_available_union.extend(symbol_available_counterparts.iter().cloned());
        decimals_available_union.extend(decimals_available_counterparts.iter().cloned());
        symbol_and_decimals_available_union
            .extend(symbol_and_decimals_available_counterparts.iter().cloned());
        liquidity_qualified_union.extend(liquidity_qualified_counterparts.iter().cloned());
        strict_depth_qualified_union.extend(strict_depth_qualified_counterparts.iter().cloned());
        liquidity_qualified_accepted_union
            .extend(liquidity_qualified_accepted_counterparts.iter().cloned());
        anchors.insert(
            anchor.clone(),
            json!({
                "rawDistinctCounterparts": raw_counterparts.len(),
                "chainTokenRowCounterparts": chain_token_row_counterparts.len(),
                "symbolAvailableCounterparts": symbol_available_counterparts.len(),
                "validDecimalsAvailableCounterparts":
                    decimals_available_counterparts.len(),
                "symbolAndValidDecimalsAvailableCounterparts":
                    symbol_and_decimals_available_counterparts.len(),
                "metadataAcceptedCounterparts": accepted_counterparts.len(),
                "liquidityQualifiedCounterparts": liquidity_qualified_counterparts.len(),
                "usdDepthQualifiedCounterpartsDiagnosticOnly":
                    strict_depth_qualified_counterparts.len(),
                "liquidityQualifiedAndMetadataAcceptedCounterparts":
                    liquidity_qualified_accepted_counterparts.len(),
                "liquidityQualifiedMetadataRejectedCounterparts":
                    liquidity_qualified_rejected_count,
                "rejectedByCode": rejected_by_code,
            }),
        );
    }

    json!({
        "anchors": anchors,
        "union": {
            "rawDistinctCounterparts": raw_union.len(),
            "chainTokenRowCounterparts": chain_token_row_union.len(),
            "symbolAvailableCounterparts": symbol_available_union.len(),
            "validDecimalsAvailableCounterparts": decimals_available_union.len(),
            "symbolAndValidDecimalsAvailableCounterparts":
                symbol_and_decimals_available_union.len(),
            "metadataAcceptedCounterparts": accepted_union.len(),
            "liquidityQualifiedCounterparts": liquidity_qualified_union.len(),
            "usdDepthQualifiedCounterpartsDiagnosticOnly": strict_depth_qualified_union.len(),
            "liquidityQualifiedAndMetadataAcceptedCounterparts":
                liquidity_qualified_accepted_union.len(),
            "liquidityQualifiedMetadataRejectedCounterparts":
                liquidity_qualified_union
                    .len()
                    .saturating_sub(liquidity_qualified_accepted_union.len()),
        }
    })
}

fn has_strict_depth(pool: &V4PoolLiquidityEvidence) -> bool {
    matches!(
        pool.depth_status
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "deep" | "liquid" | "live_liquid"
    )
}

fn raw_metadata_fields(token: &RawTokenMetadata) -> (Option<String>, Option<u8>) {
    let metadata_json = token
        .token_metadata_json
        .as_deref()
        .and_then(|value| serde_json::from_str::<serde_json::Value>(value).ok())
        .filter(|value| value.is_object());
    let symbol = token
        .token_symbol
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .filter(|value| !token_manager::is_placeholder_symbol(value))
        .map(str::to_ascii_uppercase)
        .or_else(|| {
            metadata_json
                .as_ref()
                .and_then(|value| value.get("symbol"))
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .filter(|value| !token_manager::is_placeholder_symbol(value))
                .map(str::to_ascii_uppercase)
        });
    let decimals = token
        .token_decimals
        .or_else(|| {
            let value = metadata_json.as_ref()?.get("decimals")?;
            value
                .as_i64()
                .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
        })
        .and_then(|value| u8::try_from(value).ok())
        .filter(|value| *value <= 36);
    (symbol, decimals)
}

fn flag<'a>(arguments: &'a [String], name: &str) -> Option<&'a str> {
    arguments
        .windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].as_str())
}
