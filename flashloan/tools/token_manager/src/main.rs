use manager_contracts::normalize_address;
use rusqlite::{params, Connection};
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use token_manager::{
    apply_economic_gate, audit_library_memberships, build_evidence, classify_tokens_with_liquidity,
    framework_status, mainnet_core4_allowlist, normalize_metadata_batch,
    pool_is_liquidity_qualified, proposed_memberships_from_classification,
    read_chain_tokens_read_only, read_measured_depths, read_stock_registry, read_stored_valuations,
    read_target_schema_and_memberships_read_only, read_v4_pool_currency_references_read_only,
    read_v4_pool_liquidity_evidence_read_only, simulate_bootstrap_in_temporary_database,
    token_writes_metadata_checksum, write_library, write_library_dry_run, ClassificationPolicy,
    AdmissionStatus, EconomicPolicy, LibraryKey, LibraryWriteResult, MetadataAuditError,
    MetadataSourceKind, NormalizedTokenMetadata, RawTokenMetadata, TokenWrite,
    V4PoolCurrencyReference, V4PoolLiquidityEvidence, MAINNET_TEST_TOKEN_CANDIDATES,
    ROBINHOOD_CHAIN_ID, ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK,
};
use manager_contracts::TokenKeyId;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
struct StoredTokenIdentity {
    v4_currency_key: String,
    token_address: String,
    token_symbol: String,
    token_decimals: u8,
    admission_status: String,
}

#[derive(Debug, Clone)]
struct TargetLibrarySnapshot {
    token_count: usize,
    metadata_checksum: String,
    status_distribution: BTreeMap<String, usize>,
    identities: BTreeMap<String, StoredTokenIdentity>,
}

/// The chain scope a token-library materialization runs against.
///
/// The token manager owns the token libraries of both Robinhood chains, so
/// the scope cannot be implied by the binary. It is selected explicitly and
/// defaults to mainnet, which keeps every existing invocation unchanged.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ChainScope {
    Mainnet,
    Testnet,
}

impl ChainScope {
    fn network(&self) -> &'static str {
        match self {
            Self::Mainnet => ROBINHOOD_NETWORK,
            Self::Testnet => ROBINHOOD_TESTNET_NETWORK,
        }
    }

    fn chain_id(&self) -> u64 {
        match self {
            Self::Mainnet => ROBINHOOD_CHAIN_ID,
            Self::Testnet => ROBINHOOD_TESTNET_CHAIN_ID,
        }
    }

    fn core_allowlist(&self) -> Vec<RawTokenMetadata> {
        token_manager::core4_allowlist(self.network(), self.chain_id())
    }

    fn classification_policy(&self) -> ClassificationPolicy {
        match self {
            Self::Mainnet => ClassificationPolicy::mainnet(),
            Self::Testnet => ClassificationPolicy::testnet_test_policy(),
        }
    }

    /// The economic policy for this scope, denominated in the chain's own
    /// quote asset.
    #[allow(dead_code)]
    fn economic_policy(&self) -> Result<EconomicPolicy, Box<dyn std::error::Error>> {
        Ok(EconomicPolicy::for_scope(self.network(), self.chain_id())?)
    }

    fn from_arguments(arguments: &[String]) -> Result<Self, Box<dyn std::error::Error>> {
        match flag(arguments, "--network") {
            Some(network) => match network {
                ROBINHOOD_NETWORK => Ok(Self::Mainnet),
                ROBINHOOD_TESTNET_NETWORK => Ok(Self::Testnet),
                other => Err(format!(
                    "unsupported network: {other}; expected {ROBINHOOD_NETWORK} or {ROBINHOOD_TESTNET_NETWORK}"
                )
                .into()),
            },
            None => {
                if let Some(chain_id) = flag(arguments, "--chain-id") {
                    let chain_id = chain_id
                        .parse::<u64>()
                        .map_err(|error| format!("invalid --chain-id: {error}"))?;
                    return match chain_id {
                        ROBINHOOD_CHAIN_ID => Ok(Self::Mainnet),
                        ROBINHOOD_TESTNET_CHAIN_ID => Ok(Self::Testnet),
                        other => Err(format!(
                            "unsupported chain-id: {other}; expected {ROBINHOOD_CHAIN_ID} or {ROBINHOOD_TESTNET_CHAIN_ID}"
                        )
                        .into()),
                    };
                }
                Ok(Self::Mainnet)
            }
        }
    }
}

fn main() -> ExitCode {
    // The shared depth/economic policy must agree with the compiled-in
    // view. A mismatch means the policy file changed without the tool
    // being rebuilt, and the tool refuses to run instead of silently
    // reaching a different verdict than the Python orchestrator.
    if let Err(error) = token_manager::assert_economic_policy_matches_file() {
        eprintln!("token-manager error: {error}");
        return ExitCode::FAILURE;
    }
    // R-11: USDG is USD-equivalent by definition. The peg policy states the
    // ratio, the tolerated deviation and the uses the peg may enter. It is
    // asserted at start-up so a depegged USDG or a widened use list stops the
    // tool instead of silently changing every economic verdict.
    if let Err(error) = token_manager::assert_usdg_peg_policy_matches_file() {
        eprintln!("token-manager error: {error}");
        return ExitCode::FAILURE;
    }
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
    if arguments.first().map(String::as_str) == Some("land-valuations") {
        return match land_valuations(&arguments[1..]) {
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
    if arguments.first().map(String::as_str) == Some("audit-valuation-expiry") {
        return match audit_valuation_expiry(&arguments[1..]) {
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
    if arguments.first().map(String::as_str) == Some("prune-incomplete-tokens") {
        return match prune_incomplete_tokens(&arguments[1..]) {
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
    if arguments.first().map(String::as_str) == Some("materialize-libraries") {
        return match materialize_libraries(&arguments[1..]) {
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

    if arguments.first().map(String::as_str) == Some("value-tokens") {
        return match token_manager::valuation_cli::run(&arguments[1..]) {
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

/// Prune every token of the chain that carries no usable metadata.
///
/// This is the token layer of the repair cascade. A token that has no
/// symbol, or whose symbol is still the factory placeholder, or whose
/// decimals are unknown, cannot be priced and cannot be routed through.
/// The token manager therefore **removes** it, and it removes the
/// dependances that only exist because of it: the candidate and
/// observation rows that describe a route through that token, and the
/// closed loops and their hops that were built on top of them.
///
/// The command never writes without an explicit `--apply`. The dry run
/// reports exactly which tokens and which loops would go, so the
/// orchestrator can decide before anything is removed.
fn prune_incomplete_tokens(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let database = flag(arguments, "--database")
        .map(PathBuf::from)
        .ok_or("prune-incomplete-tokens requires --database <manager sqlite path>")?;
    let apply = arguments.iter().any(|argument| argument == "--apply");
    let scope = ChainScope::from_arguments(arguments)?;
    let report = token_manager::prune_incomplete_tokens(
        &database,
        scope.network(),
        scope.chain_id(),
        apply,
    )?;
    Ok(token_manager::render_prune_report(&report))
}

/// Lands a USDG valuation report into the durable valuation store.
///
/// VAL-4-7: the valuation probe produces a report file, and a report file
/// is a transient artifact.  This command turns it into the database
/// statement the economic gate reads, so that "this token has no exchange
/// path into USDG" survives a restart and can never be lost because
/// somebody moved a file.
///
/// The write only happens with an explicit `--apply`; without it the
/// command reports what would land and touches nothing.
fn land_valuations(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let database = flag(arguments, "--database")
        .map(PathBuf::from)
        .ok_or("land-valuations requires --database <manager sqlite path>")?;
    let report_path = flag(arguments, "--valuation-report")
        .map(PathBuf::from)
        .ok_or("land-valuations requires --valuation-report <report json path>")?;
    let apply = arguments.iter().any(|argument| argument == "--apply");
    let scope = ChainScope::from_arguments(arguments)?;
    let report = token_manager::read_valuation_report(&report_path)?
        .ok_or("land-valuations requires a valuation report that carries a token list")?;
    let result = token_manager::valuation_store::land_valuation_report(
        &database,
        scope.network(),
        scope.chain_id(),
        &report,
        apply,
    )?;
    let mut rendered = token_manager::valuation_store::render_landing(&result);
    rendered["applied"] = json!(apply);
    rendered["database"] = json!(database.to_string_lossy());
    rendered["network"] = json!(scope.network());
    rendered["chainId"] = json!(scope.chain_id());
    rendered["observedAt"] = json!(report.observed_at);
    Ok(rendered.to_string())
}

/// Audits the expiry state of the stored USDG valuations.
///
/// P0-5: the valuation store keeps the timestamp of every observation,
/// but the gate used to consume every stored row as if it were current.
/// This command is the operator-facing view of the expiry policy: it
/// reports, read-only, how much of the stored valuation population is
/// still fresh enough to decide a membership question and how much is
/// still fresh enough to decide a magnitude question.
///
/// It never writes, and it never deletes a row.  Expiry is a *reading*
/// rule, not a delete job: an expired row stays in the store so that the
/// audit trail is complete, and a later refresh can replace it.
fn audit_valuation_expiry(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let database = flag(arguments, "--database")
        .map(PathBuf::from)
        .ok_or("audit-valuation-expiry requires --database <manager sqlite path>")?;
    let scope = ChainScope::from_arguments(arguments)?;
    let report = token_manager::audit_valuation_expiry(
        &database,
        scope.network(),
        scope.chain_id(),
    )?;
    Ok(serde_json::to_string_pretty(&report)?)
}

/// Withdraw every row of a library that the source no longer carries.
///
/// The write path only upserts, so a token whose address changed on the
/// chain -- or a token the chain's own allowlist no longer declares -- would
/// stay in the library with its old, now wrong address. The library would
/// then hold two admitted rows for one symbol and the audit would read the
/// stale one, which is exactly the defect the audit has to report.
///
/// A withdrawal is a `blocked` row that carries the reason, so the token
/// stops counting as a library member while the decision stays auditable.
/// The **extension** library is exempt: it holds candidates that no source
/// enumerates, so an absence there says nothing.
fn withdraw_rows_missing_from_source(
    connection: &Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
    source_token_keys: &BTreeSet<String>,
) -> Result<Vec<TokenWrite>, Box<dyn std::error::Error>> {
    if library == LibraryKey::Extension {
        return Ok(Vec::new());
    }
    let table = library.table_name();
    let mut statement = connection.prepare(&format!(
        "SELECT token_key_id, v4_currency_key, token_address, token_symbol,
                token_name, token_decimals, token_kind, is_native,
                is_wrapped_native, is_stable, admission_status,
                admission_source, metadata_source, metadata_checksum,
                source_json, first_seen_at
         FROM {table} WHERE network = ?1 AND chain_id = ?2"
    ))?;
    let rows = statement.query_map(params![network, chain_id as i64], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<String>>(3)?,
            row.get::<_, Option<String>>(4)?,
            row.get::<_, Option<i64>>(5)?,
            row.get::<_, Option<String>>(6)?,
            row.get::<_, Option<i64>>(7)?,
            row.get::<_, Option<i64>>(8)?,
            row.get::<_, Option<i64>>(9)?,
            row.get::<_, Option<String>>(10)?,
            row.get::<_, Option<String>>(11)?,
            row.get::<_, Option<String>>(12)?,
            row.get::<_, Option<String>>(13)?,
            row.get::<_, Option<String>>(14)?,
            row.get::<_, Option<String>>(15)?,
        ))
    })?;
    let mut withdrawn = Vec::new();
    for row in rows {
        let (
            token_key_id,
            v4_currency_key,
            token_address,
            token_symbol,
            token_name,
            token_decimals,
            token_kind,
            is_native,
            is_wrapped_native,
            is_stable,
            admission_status,
            admission_source,
            metadata_source,
            metadata_checksum,
            source_json,
            first_seen_at,
        ) = row?;
        // A row the source carries is upserted by the normal path.
        if source_token_keys.contains(&token_key_id) {
            continue;
        }
        // A row that is already blocked stays blocked; rewriting it would
        // produce a second, meaningless admission event.
        if admission_status.as_deref() == Some("blocked") {
            continue;
        }
        let token_key_id = TokenKeyId::new(network, chain_id, &token_address)
            .map_err(|error| format!("cannot build the token key id: {error}"))?;
        withdrawn.push(TokenWrite {
            network: network.to_owned(),
            chain_id,
            token_key_id,
            v4_currency_key,
            token_address,
            token_symbol: token_symbol.unwrap_or_default(),
            token_name,
            token_decimals: token_decimals.unwrap_or(0).clamp(0, u8::MAX as i64) as u8,
            token_kind: token_kind.unwrap_or_else(|| "erc20".to_owned()),
            is_native: is_native.unwrap_or(0) != 0,
            is_wrapped_native: is_wrapped_native.unwrap_or(0) != 0,
            is_stable: is_stable.unwrap_or(0) != 0,
            admission_status: AdmissionStatus::Blocked,
            admission_source: admission_source.unwrap_or_else(|| "bootstrap".to_owned()),
            metadata_source: metadata_source.unwrap_or_else(|| "unknown".to_owned()),
            metadata_checksum: metadata_checksum.unwrap_or_default(),
            source_json,
            first_seen_at,
            blocked_reason: Some(
                "the chain's own source no longer declares this token; the admission                  was withdrawn so the library does not keep a stale address"
                    .to_owned(),
            ),
        });
    }
    Ok(withdrawn)
}

fn materialize_libraries(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let source_database = flag(arguments, "--source-db")
        .map(PathBuf::from)
        .ok_or("materialize-libraries requires --source-db <legacy read-only sqlite path>")?;
    let target_database = flag(arguments, "--target-db")
        .map(PathBuf::from)
        .ok_or("materialize-libraries requires --target-db <manager sqlite path>")?;
    let registry = flag(arguments, "--registry")
        .map(PathBuf::from)
        .ok_or("materialize-libraries requires --registry <stock registry JSON>")?;
    let apply = arguments.iter().any(|argument| argument == "--apply");
    let actor = flag(arguments, "--actor").unwrap_or("token-manager-cli");
    let scope = ChainScope::from_arguments(arguments)?;
    let default_reason = format!("materialize token libraries from {} sources", scope.network());
    let reason = flag(arguments, "--reason").unwrap_or(&default_reason);

    let core = scope.core_allowlist();
    let chain_tokens = read_chain_tokens_read_only(&source_database, scope.network(), scope.chain_id())?;
    let stocks = read_stock_registry(&registry, scope.network(), scope.chain_id())?;
    let pool_references = read_v4_pool_currency_references_read_only(
        &source_database,
        scope.network(),
        scope.chain_id(),
    )?;
    let pool_liquidity = read_v4_pool_liquidity_evidence_read_only(
        &source_database,
        scope.network(),
        scope.chain_id(),
    )?;
    let chain_token_row_count = chain_tokens.len();
    let stock_registry_row_count = stocks.len();
    let mut records = Vec::with_capacity(core.len() + chain_tokens.len() + stocks.len());
    records.extend(core);
    records.extend(chain_tokens);
    records.extend(stocks);
    let normalized = normalize_metadata_batch(records);
    let classification = classify_tokens_with_liquidity(
        &normalized.accepted,
        &pool_references,
        &pool_liquidity,
        &scope.classification_policy(),
    );
    if !classification.issues.is_empty() {
        return Err(format!(
            "materialize-libraries refused classification with {} issue(s)",
            classification.issues.len()
        )
        .into());
    }

    // The economic gate runs *before* any library is written, so a stock
    // token without a USDG valuation is never materialized into the stock
    // library in the first place.
    //
    // The valuation evidence has two possible sources, and they are not
    // interchangeable:
    //
    //   --valuation-report   a report file produced by the probe
    //   --valuation-db       the durable store the report was landed in
    //
    // The durable store wins when both are given: it is the statement the
    // database actually holds, and a report file that has been copied
    // around since the last landing can describe an older observation.
    // A source that is present but empty is *not* silently replaced by the
    // other one — an empty durable store means "nothing has been landed
    // yet", which is different from "everything has a valuation".
    let valuation_database = flag(arguments, "--valuation-db").map(PathBuf::from);
    let stored_valuations = match valuation_database.as_ref() {
        Some(path) => Some(read_stored_valuations(
            path,
            scope.network(),
            scope.chain_id(),
        )?),
        None => None,
    };
    let valuation_report = match stored_valuations.as_ref() {
        Some(stored) => Some(token_manager::valuation::ValuationBatchReport::from_tokens(
            stored.clone(),
        )),
        None => flag(arguments, "--valuation-report")
            .map(PathBuf::from)
            .map(|path| token_manager::read_valuation_report(&path))
            .transpose()?
            .flatten(),
    };
    let depth_database = flag(arguments, "--depth-db").map(PathBuf::from);
    let depths = match depth_database.as_ref() {
        Some(path) => read_measured_depths(path, scope.network(), scope.chain_id())?,
        None => Vec::new(),
    };
    let evidence_report = build_evidence(valuation_report.as_ref(), &depths);
    // The economic floor is a shared number, but it is denominated in the
    // chain's own quote asset. The mainnet values in USDG; the testnet
    // values in the native coin, because USDG has no pool path there.
    // The mainnet scope resolves to exactly the shared policy, so its
    // verdicts are unchanged.
    let economic_policy = EconomicPolicy::for_scope(scope.network(), scope.chain_id())?;
    let quote_asset =
        token_manager::quote_asset_for(scope.network(), scope.chain_id(), economic_policy.min_economic_position_usdg_raw)?;
    let mut gated_classification = classification.clone();
    let gate = apply_economic_gate(
        &mut gated_classification,
        &evidence_report.evidence,
        &economic_policy,
    );

    let checkpoint_payload = json!({
        "classification": &gated_classification.tokens,
        "classificationIssues": &gated_classification.issues,
        "duplicateSymbols": &gated_classification.duplicate_symbols,
        "sourceOverrides": gated_classification.source_overrides,
        "economicGate": {
            "excludedStockTokens": gate.excluded_stock_tokens,
            "excludedExtensionHops": gate.excluded_extension_hops,
            "pendingStockTokens": gate.pending_stock_tokens
        }
    });
    let source_checkpoint = format!(
        "sha256:{}",
        hex::encode(Sha256::digest(serde_json::to_vec(&checkpoint_payload)?))
    );
    let mut writes_by_library = gated_classification.writes_by_library();
    // The source set of every library, taken from the classification
    // **before** the economic gate narrowed it. A token the gate withdrew
    // or left pending is still a token of the source -- it is only not
    // admitted -- so it must not be mistaken for a token the source no
    // longer carries.
    let source_token_keys_by_library = classification.source_token_keys_by_library();
    // The gate withdraws a membership by taking it away from the
    // classification, but the row that is already in the library table
    // would simply stay there, still `admitted`. The withdrawal writes
    // put the decision back into the table: a `blocked` row with the
    // reason attached, so the token stops counting as a member and the
    // decision stays auditable.
    //
    // A withdrawal is routed to the library the token was withdrawn
    // *from*, which is not always the library it is being written into:
    // a stock token can also be an extension member.
    let mut withdrawn_by_library: BTreeMap<LibraryKey, Vec<TokenWrite>> = BTreeMap::new();
    for write in gate.withdrawn_stock_writes {
        withdrawn_by_library
            .entry(LibraryKey::Stocks)
            .or_default()
            .push(write);
    }
    for write in gate.withdrawn_extension_writes {
        withdrawn_by_library
            .entry(LibraryKey::Extension)
            .or_default()
            .push(write);
    }
    let mut connection = Connection::open(&target_database)?;
    let mut library_results = Vec::new();
    let mut migration_receipts = Vec::new();
    for library in [
        LibraryKey::Core,
        LibraryKey::Stocks,
        LibraryKey::Test,
        LibraryKey::Extension,
    ] {
        let mut writes = writes_by_library.remove(&library).unwrap_or_default();
        // A token that the gate withdrew and a token that it kept must
        // not both be written. The withdrawal wins: it carries the
        // reason, and the kept write carries no reason at all.
        let withdrawn = withdrawn_by_library.remove(&library).unwrap_or_default();
        let withdrawn_keys: BTreeSet<String> = withdrawn
            .iter()
            .map(|write| write.token_key_id.as_str().to_owned())
            .collect();
        writes.retain(|write| !withdrawn_keys.contains(write.token_key_id.as_str()));
        writes.extend(withdrawn.clone());
        // A row that the source no longer contains has to be withdrawn.
        //
        // The write path only upserts, so a token whose address changed on
        // the chain -- or a token that the chain's own allowlist no longer
        // declares -- would stay in the library with its old, now wrong
        // address. The library would then hold two admitted rows for one
        // symbol, and the audit would read the stale one. The materializer
        // therefore compares the library against the source and withdraws
        // every row the source does not carry.
        //
        // The gate withdrawals are part of the source set: a token the gate
        // withdrew is still a token of the source, it is only not admitted,
        // so it must not be counted as missing.
        writes.extend(withdraw_rows_missing_from_source(
            &connection,
            library,
            scope.network(),
            scope.chain_id(),
            &source_token_keys_by_library
                .get(&library)
                .cloned()
                .unwrap_or_default(),
        )?);
        let current_target = read_target_library_snapshot(
            &connection,
            library,
            scope.network(),
            scope.chain_id(),
        )?;
        let planned_writes = writes.clone();
        let result = if apply {
            write_library(
                &mut connection,
                library,
                scope.network(),
                scope.chain_id(),
                writes,
                Some(&source_checkpoint),
                actor,
                reason,
            )?
        } else {
            write_library_dry_run(
                &mut connection,
                library,
                scope.network(),
                scope.chain_id(),
                writes,
                Some(&source_checkpoint),
                actor,
                reason,
            )?
        };
        migration_receipts.push(build_library_migration_receipt(
            library,
            scope.network(),
            scope.chain_id(),
            &planned_writes,
            &current_target,
            &result,
        )?);
        library_results.push(result);
    }
    let quick_check: String = connection.query_row("PRAGMA quick_check", [], |row| row.get(0))?;
    if quick_check != "ok" {
        return Err(format!("target database quick_check failed: {quick_check}").into());
    }
    let migration_receipt_valid = migration_receipts
        .iter()
        .all(|receipt| receipt["projectedConsistentWithSource"] == json!(true));

    Ok(serde_json::to_string_pretty(&json!({
        "mode": if apply { "apply" } else { "dry_run" },
        "network": scope.network(),
        "chainId": scope.chain_id(),
        "sourceDatabase": source_database.display().to_string(),
        "targetDatabase": target_database.display().to_string(),
        "registry": registry.display().to_string(),
        "sourceCheckpoint": source_checkpoint,
        "migrationReceipt": {
            "valid": migration_receipt_valid,
            "source": {
                "chainTokenRows": chain_token_row_count,
                "stockRegistryRows": stock_registry_row_count,
                "poolCurrencyReferences": pool_references.len(),
                "poolLiquidityEvidenceRows": pool_liquidity.len(),
                "normalizedAcceptedRows": normalized.accepted.len(),
                "normalizedRejectedRows": normalized.errors.len()
            },
            "libraries": migration_receipts
        },
        "normalization": {
            "accepted": normalized.accepted.len(),
            "rejected": normalized.errors.len()
        },
        "classification": {
            "tokens": gated_classification.tokens.len(),
            "issues": gated_classification.issues.len(),
            "duplicateSymbolGroups": gated_classification.duplicate_symbols.len(),
            "unclassified": gated_classification.unclassified_count()
        },
        "economicGate": {
            "policy": {
                "minimumEconomicPositionUsdgRaw":
                    economic_policy.min_economic_position_usdg_raw,
                "minimumPoolDepthUsdgRaw":
                    economic_policy.min_pool_depth_usdg_raw,
                "quoteAsset": quote_asset,
                "quoteAssetAddress": quote_asset.address,
                "quoteAssetDecimals": quote_asset.decimals
            },
            "evidence": token_manager::render_evidence_report(&evidence_report),
            "excludedStockTokens": gate.excluded_stock_tokens,
            "excludedStockCount": gate.excluded_stock_tokens.len(),
            "excludedExtensionHops": gate.excluded_extension_hops,
            "excludedExtensionCount": gate.excluded_extension_hops.len(),
            "pendingStockTokens": gate.pending_stock_tokens,
            "pendingStockCount": gate.pending_stock_tokens.len()
        },
        "libraries": library_results,
        "quickCheck": quick_check,
        "applied": apply
    }))?)
}

fn read_target_library_snapshot(
    connection: &Connection,
    library: LibraryKey,
    network: &str,
    chain_id: u64,
) -> Result<TargetLibrarySnapshot, Box<dyn std::error::Error>> {
    let mut statement = connection.prepare(&format!(
        "SELECT token_key_id, lower(v4_currency_key), lower(token_address), token_symbol,
                token_decimals, admission_status
         FROM {} WHERE network = ?1 AND chain_id = ?2
         ORDER BY token_key_id",
        library.table_name()
    ))?;
    let rows = statement.query_map((network, chain_id), |row| {
        Ok((
            row.get::<_, String>(0)?,
            StoredTokenIdentity {
                v4_currency_key: row.get(1)?,
                token_address: row.get(2)?,
                token_symbol: row.get(3)?,
                token_decimals: row.get(4)?,
                admission_status: row.get(5)?,
            },
        ))
    })?;
    let identities = rows.collect::<Result<BTreeMap<_, _>, _>>()?;
    let mut status_distribution = BTreeMap::new();
    for identity in identities.values() {
        *status_distribution
            .entry(identity.admission_status.clone())
            .or_insert(0) += 1;
    }
    Ok(TargetLibrarySnapshot {
        token_count: identities.len(),
        metadata_checksum: token_manager::library_metadata_checksum(
            connection, library, network, chain_id,
        )?,
        status_distribution,
        identities,
    })
}

fn build_library_migration_receipt(
    library: LibraryKey,
    network: &str,
    chain_id: u64,
    planned_writes: &[TokenWrite],
    current_target: &TargetLibrarySnapshot,
    projected: &LibraryWriteResult,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let planned_identities = planned_writes
        .iter()
        .map(|write| {
            (
                write.token_key_id.as_str().to_owned(),
                StoredTokenIdentity {
                    v4_currency_key: write.v4_currency_key.to_ascii_lowercase(),
                    token_address: write.token_address.to_ascii_lowercase(),
                    token_symbol: write.token_symbol.clone(),
                    token_decimals: write.token_decimals,
                    admission_status: write.admission_status.as_str().to_owned(),
                },
            )
        })
        .collect::<BTreeMap<_, _>>();
    let planned_status_distribution = status_distribution(planned_identities.values());
    let missing_from_current = planned_identities
        .keys()
        .filter(|token_key_id| !current_target.identities.contains_key(*token_key_id))
        .cloned()
        .collect::<Vec<_>>();
    let unexpected_in_current = current_target
        .identities
        .keys()
        .filter(|token_key_id| !planned_identities.contains_key(*token_key_id))
        .cloned()
        .collect::<Vec<_>>();
    let identity_mismatches = planned_identities
        .iter()
        .filter_map(|(token_key_id, planned)| {
            current_target
                .identities
                .get(token_key_id)
                .filter(|current| *current != planned)
                .map(|_| token_key_id.clone())
        })
        .collect::<Vec<_>>();
    let planned_metadata_checksum = token_writes_metadata_checksum(
        network,
        chain_id,
        planned_writes.to_vec(),
    )?;
    let projected_status_distribution = BTreeMap::from([
        ("admitted".to_owned(), projected.admitted_count as usize),
        (
            "candidate_only".to_owned(),
            projected.candidate_only_count as usize,
        ),
        ("blocked".to_owned(), projected.blocked_count as usize),
        (
            "pending_review".to_owned(),
            projected.pending_review_count as usize,
        ),
    ]);
    let projected_consistent = planned_identities.len() == projected.token_count as usize
        && planned_metadata_checksum == projected.metadata_checksum
        && planned_status_distribution == projected_status_distribution;
    Ok(json!({
        "library": library,
        "planned": {
            "tokenCount": planned_identities.len(),
            "metadataChecksum": planned_metadata_checksum,
            "statusDistribution": planned_status_distribution
        },
        "currentTarget": {
            "tokenCount": current_target.token_count,
            "metadataChecksum": current_target.metadata_checksum,
            "statusDistribution": current_target.status_distribution,
            "missingTokenKeyIds": missing_from_current,
            "unexpectedTokenKeyIds": unexpected_in_current,
            "identityMismatchTokenKeyIds": identity_mismatches
        },
        "projectedTarget": {
            "tokenCount": projected.token_count,
            "metadataChecksum": projected.metadata_checksum,
            "statusDistribution": projected_status_distribution
        },
        "projectedConsistentWithSource": projected_consistent
    }))
}

fn status_distribution<'a>(
    identities: impl Iterator<Item = &'a StoredTokenIdentity>,
) -> BTreeMap<String, usize> {
    let mut distribution = BTreeMap::from([
        ("admitted".to_owned(), 0),
        ("candidate_only".to_owned(), 0),
        ("blocked".to_owned(), 0),
        ("pending_review".to_owned(), 0),
    ]);
    for identity in identities {
        *distribution
            .entry(identity.admission_status.clone())
            .or_insert(0) += 1;
    }
    distribution
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
