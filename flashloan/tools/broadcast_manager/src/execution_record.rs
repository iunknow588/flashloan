//! Recording a broadcast that the runtime already performed.
//!
//! The broadcast layer has two jobs, and they are easy to confuse:
//!
//! * **deciding** that a request may be sent — the admission decision in
//!   [`crate::admission`], which runs *before* anything is signed;
//! * **recording** what was sent — this module, which runs *after* the
//!   transaction was signed and submitted.
//!
//! The runtime signs and submits on its own, so the second job has no
//! request to admit: the decision is already behind it and there is
//! nothing left to judge.  It still has to be a **fact**, because the
//! monthly trade store is built from those facts, and an operator has to
//! be able to ask "what did this deployment actually send?".
//!
//! Two properties make the record trustworthy:
//!
//! * The **identity and the path** come from the review row of the loop,
//!   not from the runtime.  The review layer owns the closed-loop
//!   identity, so the attempt row and the review row cannot drift apart.
//! * The **transaction facts** — the signer, the nonce, the gas, the
//!   transaction hash, the receipt — come from the runtime, because only
//!   the runtime knows them.
//!
//! The record deliberately does **not** require a broadcast request row.
//! The request table is a snapshot of the *current* generation: the
//! review layer replaces it on every publication, so a request that was
//! generated a minute ago may be gone by the time the transaction comes
//! back.  A fact that happened must not depend on a snapshot that moved.

use std::path::Path;
use std::time::Duration;

use manager_contracts::normalize_address;
use review_manager::{ReviewStatus, SignedPnl};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{
    attempt_counts, broadcast_checksum, insert_attempt, normalize_object, open_connection,
    publish_trade_archives, read_attempts, read_review_upstream, read_status_on_connection,
    required_upstream_checksum, required_upstream_version, upsert_broadcast_state,
    validate_operation, AttemptFinalizeInput, AttemptRow, BroadcastError,
    BroadcastMaterializationResult, BroadcastTerminalStatus, BROADCAST_SCOPE_KEY,
    MAX_INTERNAL_LIMIT, NETWORK_PROTOCOL,
};

/// One broadcast the runtime performed.
///
/// Everything in here is a fact about a transaction.  Nothing in here is
/// a judgement: the judgement happened before the transaction was signed.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeAttempt {
    /// The identifier of the attempt row.
    ///
    /// The caller names the attempt, because only the caller knows what
    /// makes two attempts the same attempt: a transaction that reached
    /// the chain is the same attempt whenever it is recorded again.
    pub attempt_id: String,
    /// The closed loop the transaction belonged to.
    pub loop_key: String,
    /// The account that signed the transaction.
    pub signer_address: String,
    /// The nonce the transaction carried.
    pub nonce: i64,
    /// The gas limit the transaction carried.
    pub gas_limit: Option<i64>,
    /// The gas price the transaction carried.
    pub gas_price_wei: Option<String>,
    /// The maximum fee per gas the transaction carried.
    pub max_fee_per_gas_wei: Option<String>,
    /// The maximum priority fee per gas the transaction carried.
    pub max_priority_fee_per_gas_wei: Option<String>,
    /// The RPC endpoint the transaction was sent through.
    pub rpc_url: String,
    /// A name for the RPC endpoint, when the deployment has one.
    pub rpc_name: Option<String>,
    /// The transaction hash, when the broadcast was accepted.
    pub tx_hash: Option<String>,
    /// Whether the transaction ended on chain.
    pub status: BroadcastTerminalStatus,
    /// Why the transaction did not end on chain.
    pub status_reason: Option<String>,
    /// The receipt, when the chain returned one.
    pub receipt: Value,
    /// Everything the runtime knows about the attempt.
    pub evidence: Value,
    /// The signed profit the runtime measured for the route it broadcast.
    pub signed_pnl: SignedPnl,
}

/// What the record did.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeAttemptOutcome {
    /// The identifier of the attempt row.
    pub attempt_id: String,
    /// The loop the attempt belongs to.
    pub loop_key: String,
    /// The terminal status that was recorded.
    pub status: String,
    /// The transaction hash that was recorded.
    pub tx_hash: Option<String>,
    /// Whether the row was already there and was updated in place.
    pub updated: bool,
}

/// The structural facts about one closed loop, as the review layer published them.
#[derive(Debug, Clone)]
struct ReviewLoopFacts {
    closed_loop_version: i64,
    closed_loop_checksum: String,
    anchor_token_key_id: String,
    path_text: String,
    path_addresses: Vec<String>,
    path_symbols: Vec<String>,
    pool_path_key: String,
    pool_ids: Vec<String>,
    single_tick_capacity_anchor_raw: Option<u128>,
    single_tick_capacity_binding_pool_id: Option<String>,
    hop_sqrt_price_limits: Vec<String>,
}

/// Record one broadcast the runtime performed.
///
/// The record is written to the main store and is then archived into the
/// monthly trade store, so the deployment ends up with both: the row that
/// belongs to the current library state, and the month file that holds
/// the history.
pub fn record_runtime_attempt(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    attempt: &RuntimeAttempt,
) -> Result<RuntimeAttemptOutcome, BroadcastError> {
    validate_operation(network, chain_id)?;
    let path = database_path.as_ref();
    let mut connection = open_connection(path, true)?;
    connection.busy_timeout(Duration::from_secs(60))?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let outcome = record_on_connection(&transaction, network, chain_id, attempt)?;
    transaction.commit()?;
    publish_trade_archives(
        path,
        network,
        chain_id,
        &[AttemptFinalizeInput {
            attempt_id: outcome.attempt_id.clone(),
            status: attempt.status,
            tx_hash: attempt.tx_hash.clone(),
            receipt: attempt.receipt.clone(),
            status_reason: attempt.status_reason.clone(),
            diagnostics: Vec::new(),
            evidence: attempt.evidence.clone(),
            finalized_at: None,
        }],
    );
    Ok(outcome)
}

fn record_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    attempt: &RuntimeAttempt,
) -> Result<RuntimeAttemptOutcome, BroadcastError> {
    let review = read_review_upstream(connection, network, chain_id)?;
    let facts = read_review_loop_facts(connection, network, chain_id, &attempt.loop_key)?;
    // The caller names the attempt: only the caller knows whether two
    // records are the same attempt, because only the caller knows the
    // transaction hash.  Deriving a second name here would give the
    // row and the payload two different identities for one broadcast.
    let attempt_id = attempt.attempt_id.trim().to_owned();
    if attempt_id.is_empty() {
        return Err(BroadcastError::InvalidRequest(
            "attempt.attemptId must not be empty".to_owned(),
        ));
    }
    if attempt_id.chars().count() > crate::MAX_IDENTIFIER_LENGTH {
        return Err(BroadcastError::InvalidRequest(
            "attempt.attemptId is too long".to_owned(),
        ));
    }
    let signer_address = normalize_address(&attempt.signer_address).map_err(|_| {
        BroadcastError::InvalidRequest("attempt.signerAddress is not a valid address".to_owned())
    })?;
    let now = chrono::Utc::now().to_rfc3339();
    let status = attempt.status.as_str().to_owned();
    let row = AttemptRow {
        attempt_id: attempt_id.clone(),
        loop_key: attempt.loop_key.clone(),
        closed_loop_version: facts.closed_loop_version,
        closed_loop_checksum: facts.closed_loop_checksum,
        review_version: review.review_version,
        review_checksum: required_upstream_checksum(
            review.materialized_checksum.as_deref(),
            "review_checksum",
        )?,
        anchor_token_key_id: facts.anchor_token_key_id,
        path_text: facts.path_text,
        path_addresses: facts.path_addresses,
        path_symbols: facts.path_symbols,
        pool_path_key: facts.pool_path_key,
        pool_ids: facts.pool_ids,
        signed_pnl: attempt.signed_pnl.clone(),
        signer_address,
        nonce: attempt.nonce,
        gas_limit: attempt.gas_limit,
        gas_price_wei: attempt.gas_price_wei.clone(),
        max_fee_per_gas_wei: attempt.max_fee_per_gas_wei.clone(),
        max_priority_fee_per_gas_wei: attempt.max_priority_fee_per_gas_wei.clone(),
        rpc_url: attempt.rpc_url.trim().to_owned(),
        rpc_name: attempt.rpc_name.clone(),
        tx_hash: attempt.tx_hash.clone(),
        status: status.clone(),
        status_reason: attempt.status_reason.clone(),
        receipt: normalize_object(&attempt.receipt),
        evidence: normalize_object(&attempt.evidence),
        created_at: now.clone(),
        updated_at: now.clone(),
        // A pending attempt has no end yet: the transaction was sent and
        // the chain has not answered, so the attempt only becomes terminal
        // when the broadcast confirmation thread writes the receipt.
        terminal_at: if status == "pending" {
            None
        } else {
            Some(now.clone())
        },
        single_tick_capacity_anchor_raw: facts.single_tick_capacity_anchor_raw,
        single_tick_capacity_binding_pool_id: facts.single_tick_capacity_binding_pool_id,
        hop_sqrt_price_limits: facts.hop_sqrt_price_limits,
    };
    let updated = upsert_attempt(connection, network, chain_id, &row)?;
    // A broadcast that succeeded ends the round of the loop: the loop has
    // been traded, so it starts the clock of its **next** round.  The
    // clock belongs to the closed loop, so it is started through the
    // closed-loop Manager: the loop waits for the interval and is due
    // again when the clock runs out.
    if attempt.status == BroadcastTerminalStatus::Success {
        review_manager::start_review_clock_on_connection(
            connection,
            network,
            chain_id,
            &attempt.loop_key,
            "broadcast_confirmed",
        )
        .map_err(|error| BroadcastError::ClosedLoopRead(error.to_string()))?;
    }
    refresh_broadcast_state(connection, network, chain_id, &review, &now)?;
    Ok(RuntimeAttemptOutcome {
        attempt_id,
        loop_key: attempt.loop_key.clone(),
        status,
        tx_hash: attempt.tx_hash.clone(),
        updated,
    })
}

/// Write the attempt, or update it when the same attempt is recorded twice.
fn upsert_attempt(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    row: &AttemptRow,
) -> Result<bool, BroadcastError> {
    let existing: Option<String> = connection
        .query_row(
            "SELECT attempt_id FROM manager_cow_execution_attempts
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4 AND attempt_id = ?5",
            params![
                network,
                chain_id,
                NETWORK_PROTOCOL,
                BROADCAST_SCOPE_KEY,
                row.attempt_id
            ],
            |row| row.get(0),
        )
        .optional()?;
    if existing.is_some() {
        update_recorded_attempt(connection, network, chain_id, row)?;
        return Ok(true);
    }
    insert_attempt(connection, network, chain_id, row)?;
    Ok(false)
}

/// Update the attempt that is already there.
///
/// `created_at` is left alone on purpose: the monthly trade store is
/// keyed by it, so a row that is recorded twice must not migrate into a
/// later month.
fn update_recorded_attempt(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    row: &AttemptRow,
) -> Result<(), BroadcastError> {
    connection.execute(
        "UPDATE manager_cow_execution_attempts
         SET signed_pnl_raw = ?6,
             signed_pnl_amount = ?7,
             signed_pnl_currency = ?8,
             signed_pnl_address = ?9,
             signed_pnl_unit = ?10,
             signed_pnl_source = ?11,
             signer_address = ?12,
             nonce = ?13,
             gas_limit = ?14,
             gas_price_wei = ?15,
             max_fee_per_gas_wei = ?16,
             max_priority_fee_per_gas_wei = ?17,
             rpc_url = ?18,
             rpc_name = ?19,
             tx_hash = ?20,
             status = ?21,
             status_reason = ?22,
             receipt_json = ?23,
             evidence_json = ?24,
             updated_at = ?25,
             terminal_at = ?26
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND scope_key = ?4 AND attempt_id = ?5",
        params![
            network,
            chain_id,
            NETWORK_PROTOCOL,
            BROADCAST_SCOPE_KEY,
            row.attempt_id,
            row.signed_pnl.raw,
            row.signed_pnl.amount,
            row.signed_pnl.currency,
            row.signed_pnl.address,
            row.signed_pnl.unit,
            row.signed_pnl.source,
            row.signer_address,
            row.nonce,
            row.gas_limit,
            row.gas_price_wei,
            row.max_fee_per_gas_wei,
            row.max_priority_fee_per_gas_wei,
            row.rpc_url,
            row.rpc_name,
            row.tx_hash,
            row.status,
            row.status_reason,
            serde_json::to_string(&row.receipt)?,
            serde_json::to_string(&row.evidence)?,
            row.updated_at,
            row.terminal_at,
        ],
    )?;
    Ok(())
}

/// Read the structural facts of one loop from its review row.
///
/// The review row is the right source because the review layer owns the
/// closed-loop identity.  A loop without a review row has no identity to
/// file the attempt under, so the attempt is refused rather than filed
/// against a guess.
fn read_review_loop_facts(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    loop_key: &str,
) -> Result<ReviewLoopFacts, BroadcastError> {
    let row = connection
        .query_row(
            "SELECT closed_loop_version, closed_loop_checksum, anchor_token_key_id,
                    path_text, path_addresses_json, path_symbols_json, pool_path_key,
                    pool_ids_json, single_tick_capacity_anchor_raw,
                    single_tick_capacity_binding_pool_id, hop_sqrt_price_limits_json
             FROM manager_v4_reviews
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND scope_key = ?4 AND loop_key = ?5",
            params![network, chain_id, NETWORK_PROTOCOL, BROADCAST_SCOPE_KEY, loop_key],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, Option<String>>(8)?,
                    row.get::<_, Option<String>>(9)?,
                    row.get::<_, Option<String>>(10)?,
                ))
            },
        )
        .optional()?;
    let Some((
        closed_loop_version,
        closed_loop_checksum,
        anchor_token_key_id,
        path_text,
        path_addresses_json,
        path_symbols_json,
        pool_path_key,
        pool_ids_json,
        single_tick_capacity_anchor_raw,
        single_tick_capacity_binding_pool_id,
        hop_sqrt_price_limits_json,
    )) = row
    else {
        return Err(BroadcastError::ReviewRowMissing {
            loop_key: loop_key.to_owned(),
        });
    };
    Ok(ReviewLoopFacts {
        closed_loop_version,
        closed_loop_checksum,
        anchor_token_key_id,
        path_text,
        path_addresses: parse_json_list(&path_addresses_json)?,
        path_symbols: parse_json_list(&path_symbols_json)?,
        pool_path_key,
        pool_ids: parse_json_list(&pool_ids_json)?,
        single_tick_capacity_anchor_raw: single_tick_capacity_anchor_raw
            .as_deref()
            .and_then(|value| value.trim().parse::<u128>().ok()),
        single_tick_capacity_binding_pool_id,
        hop_sqrt_price_limits: hop_sqrt_price_limits_json
            .as_deref()
            .map(parse_json_list)
            .transpose()?
            .unwrap_or_default(),
    })
}

fn parse_json_list(value: &str) -> Result<Vec<String>, BroadcastError> {
    let parsed: Vec<String> = serde_json::from_str(value).map_err(|error| {
        BroadcastError::Json(serde_json::Error::io(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            error.to_string(),
        )))
    })?;
    Ok(parsed)
}

/// Refresh the broadcast library state after a runtime attempt.
fn refresh_broadcast_state(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    review: &ReviewStatus,
    now: &str,
) -> Result<(), BroadcastError> {
    let attempts = read_attempts(
        connection,
        network,
        chain_id,
        None,
        None,
        0,
        MAX_INTERNAL_LIMIT,
    )?;
    let counts = attempt_counts(&attempts);
    let checksum = broadcast_checksum(review, &attempts)?;
    let previous = read_status_on_connection(connection, network, chain_id)?;
    let materialized_changed =
        previous.materialized_checksum.as_deref() != Some(checksum.as_str());
    let broadcast_version = if materialized_changed {
        previous.broadcast_version.saturating_add(1).max(1)
    } else {
        previous.broadcast_version.max(1)
    };
    let result = BroadcastMaterializationResult {
        network: network.to_owned(),
        chain_id,
        protocol: NETWORK_PROTOCOL.to_owned(),
        scope_key: BROADCAST_SCOPE_KEY.to_owned(),
        apply: true,
        dry_run: false,
        review_version: review.review_version,
        review_checksum: required_upstream_checksum(
            review.materialized_checksum.as_deref(),
            "review_checksum",
        )?,
        closed_loop_version: required_upstream_version(
            review.closed_loop_version,
            "closed_loop_version",
        )?,
        closed_loop_checksum: required_upstream_checksum(
            review.closed_loop_checksum.as_deref(),
            "closed_loop_checksum",
        )?,
        request_count: review.broadcast_request_count,
        attempt_count: counts.attempts,
        pending_count: counts.pending,
        success_count: counts.success,
        failed_count: counts.failed,
        terminal_count: counts.terminal,
        changed_rows: 1,
        broadcast_version,
        materialized_checksum: checksum,
    };
    upsert_broadcast_state(connection, network, chain_id, &result, now)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::database_with_record_root;
    use std::path::PathBuf;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const LOOP_KEY: &str = "loop-1";
    const SIGNER: &str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TX_HASH: &str = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const ATTEMPT_ID: &str = "runtime:robinhood:4663:0xbbbb";

    /// One broadcast the runtime performed.
    fn runtime_attempt(attempt_id: &str) -> RuntimeAttempt {
        RuntimeAttempt {
            attempt_id: attempt_id.to_owned(),
            loop_key: LOOP_KEY.to_owned(),
            signer_address: SIGNER.to_owned(),
            nonce: 42,
            gas_limit: Some(500_000),
            gas_price_wei: Some("1000000000".to_owned()),
            max_fee_per_gas_wei: None,
            max_priority_fee_per_gas_wei: None,
            rpc_url: "https://rpc.example.invalid".to_owned(),
            rpc_name: Some("fixture-primary".to_owned()),
            tx_hash: Some(TX_HASH.to_owned()),
            status: BroadcastTerminalStatus::Success,
            status_reason: None,
            receipt: serde_json::json!({"status": "0x1"}),
            evidence: serde_json::json!({"source": "fixture"}),
            signed_pnl: SignedPnl {
                raw: "1250000000000000".to_owned(),
                amount: 3.5,
                currency: "USDG".to_owned(),
                address: Some("0x1111111111111111111111111111111111111111".to_owned()),
                unit: "token".to_owned(),
                source: "runArbitrage".to_owned(),
            },
        }
    }

    /// The number of attempt rows in the main store.
    fn attempt_count(path: &Path) -> i64 {
        let connection = Connection::open(path).unwrap();
        connection
            .query_row(
                "SELECT COUNT(*) FROM manager_cow_execution_attempts
                 WHERE network = ?1 AND chain_id = ?2",
                params![NETWORK, CHAIN_ID],
                |row| row.get(0),
            )
            .unwrap()
    }

    #[test]
    fn a_runtime_attempt_with_a_review_row_is_recorded() {
        let (_directory, path) = database_with_record_root();
        let outcome =
            record_runtime_attempt(&path, NETWORK, CHAIN_ID, &runtime_attempt(ATTEMPT_ID)).unwrap();
        assert_eq!(outcome.attempt_id, ATTEMPT_ID);
        assert_eq!(outcome.loop_key, LOOP_KEY);
        assert_eq!(outcome.status, "success");
        assert_eq!(outcome.tx_hash.as_deref(), Some(TX_HASH));
        assert!(!outcome.updated);

        // The identity of the attempt comes from the review row, so the
        // attempt row and the review row cannot tell two stories.
        let connection = Connection::open(&path).unwrap();
        let (closed_loop_version, anchor, path_text, pool_path_key, signed_pnl_raw): (
            i64,
            String,
            String,
            String,
            String,
        ) = connection
            .query_row(
                "SELECT closed_loop_version, anchor_token_key_id, path_text,
                        pool_path_key, signed_pnl_raw
                 FROM manager_cow_execution_attempts WHERE attempt_id = ?1",
                params![ATTEMPT_ID],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(closed_loop_version, 7);
        assert_eq!(
            anchor,
            "robinhood:4663:0x1111111111111111111111111111111111111111"
        );
        assert_eq!(path_text, "USDG -> AMZN -> TA -> USDG");
        assert_eq!(pool_path_key, "pool-path:1");
        assert_eq!(signed_pnl_raw, "1250000000000000");
    }

    #[test]
    fn a_runtime_attempt_without_a_review_row_is_refused() {
        let (_directory, path) = database_with_record_root();
        let mut attempt = runtime_attempt(ATTEMPT_ID);
        attempt.loop_key = "loop-without-a-review".to_owned();
        let error = record_runtime_attempt(&path, NETWORK, CHAIN_ID, &attempt).unwrap_err();
        match error {
            BroadcastError::ReviewRowMissing { loop_key } => {
                assert_eq!(loop_key, "loop-without-a-review");
            }
            other => panic!("the attempt was refused for the wrong reason: {other:?}"),
        }
        assert_eq!(attempt_count(&path), 0);
    }

    #[test]
    fn an_attempt_without_an_identity_is_refused() {
        let (_directory, path) = database_with_record_root();
        let mut attempt = runtime_attempt(ATTEMPT_ID);
        attempt.attempt_id = "   ".to_owned();
        let error = record_runtime_attempt(&path, NETWORK, CHAIN_ID, &attempt).unwrap_err();
        assert!(matches!(error, BroadcastError::InvalidRequest(_)));
        assert_eq!(attempt_count(&path), 0);
    }

    #[test]
    fn recording_the_same_attempt_twice_updates_the_row() {
        let (_directory, path) = database_with_record_root();
        let attempt = runtime_attempt(ATTEMPT_ID);
        let first = record_runtime_attempt(&path, NETWORK, CHAIN_ID, &attempt).unwrap();
        assert!(!first.updated);
        let connection = Connection::open(&path).unwrap();
        let created_at: String = connection
            .query_row(
                "SELECT created_at FROM manager_cow_execution_attempts
                 WHERE attempt_id = ?1",
                params![ATTEMPT_ID],
                |row| row.get(0),
            )
            .unwrap();
        drop(connection);

        let second = record_runtime_attempt(&path, NETWORK, CHAIN_ID, &attempt).unwrap();
        assert!(second.updated);
        assert_eq!(second.attempt_id, first.attempt_id);
        // The row was updated, not added, and it kept the month it was
        // created in: a record that is written twice does not migrate.
        assert_eq!(attempt_count(&path), 1);
        let connection = Connection::open(&path).unwrap();
        let created_at_again: String = connection
            .query_row(
                "SELECT created_at FROM manager_cow_execution_attempts
                 WHERE attempt_id = ?1",
                params![ATTEMPT_ID],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(created_at_again, created_at);
    }

    #[test]
    fn a_recorded_attempt_reaches_the_monthly_trade_store() {
        let (_directory, path) = database_with_record_root();
        record_runtime_attempt(&path, NETWORK, CHAIN_ID, &runtime_attempt(ATTEMPT_ID)).unwrap();

        // The month store is written by the background archiver, so the
        // file is waited for instead of assumed.
        let month_file = wait_for_month_file(&path, Duration::from_secs(30));
        let connection = Connection::open(&month_file).unwrap();
        let (count, status): (i64, String) = connection
            .query_row(
                "SELECT COUNT(*), MAX(status) FROM manager_cow_execution_attempts
                 WHERE attempt_id = ?1",
                params![ATTEMPT_ID],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(count, 1);
        assert_eq!(status, "success");
    }

    /// Wait for the month store of the fixture to appear.
    fn wait_for_month_file(database_path: &Path, timeout: Duration) -> PathBuf {
        let chain_directory = database_path
            .parent()
            .unwrap()
            .join("broadcast_transaction_db")
            .join(NETWORK)
            .join(CHAIN_ID.to_string());
        let deadline = std::time::Instant::now() + timeout;
        loop {
            if let Ok(entries) = std::fs::read_dir(&chain_directory) {
                for entry in entries.flatten() {
                    let candidate = entry.path().join("trades.sqlite3");
                    if candidate.exists() {
                        return candidate;
                    }
                }
            }
            assert!(
                std::time::Instant::now() < deadline,
                "the month store was not written within {timeout:?}"
            );
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}
