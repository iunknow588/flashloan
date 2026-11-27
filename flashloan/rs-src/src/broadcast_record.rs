//! Turning a broadcast the runtime performed into a broadcast record.
//!
//! The runtime signs and submits the transaction itself, so the broadcast
//! layer never sees a request it could admit.  What it still has to see
//! is the **fact**: which loop, which account, which nonce, which
//! transaction.  Without that fact the monthly trade store stays empty
//! and an operator cannot answer what this deployment actually sent.
//!
//! The facts come from two places, and the split is deliberate:
//!
//! * the **payload** carries what the kernel measured: the closed-loop
//!   key, the signed profit and the verdicts of the gates;
//! * the **broadcast result** carries what the chain returned: the
//!   signer, the nonce, the gas and the transaction hash.
//!
//! Nothing here judges the broadcast.  The judgement happened before the
//! transaction was signed; this module only files what followed.

use broadcast_manager::{
    record_runtime_attempt, BroadcastTerminalStatus, RuntimeAttempt, RuntimeAttemptOutcome,
};
use review_manager::SignedPnl;
use serde_json::{json, Value};

/// File the broadcast the runtime just performed.
///
/// The function returns a summary of what it did.  A failure is an error,
/// not a quiet success: the caller puts the error where it can be seen.
pub fn record_broadcast_outcome(
    database_path: &str,
    network: &str,
    chain_id: i64,
    payload: &Value,
    config: &Value,
) -> Result<Value, String> {
    let Some(attempt) = build_attempt(network, chain_id, payload, config)? else {
        // The runtime did not broadcast: a gate stopped it, or the route
        // was not profitable.  There is no transaction to file, and the
        // reason the kernel published travels back with the summary.
        let reason = payload
            .get("broadcastSkipped")
            .and_then(Value::as_str)
            .unwrap_or("no_broadcast_was_performed")
            .to_owned();
        return Ok(json!({"recorded": false, "reason": reason}));
    };
    let outcome = record_runtime_attempt(database_path, network, chain_id, &attempt)
        .map_err(|error| format!("the broadcast record could not be written: {error}"))?;
    Ok(summary(
        &outcome,
        &attempt.attempt_id,
        attempt.status_reason.as_deref(),
    ))
}

/// Turn the payload and the broadcast result into the facts of one attempt.
///
/// The function reads nothing but the two values it is given, so the
/// answer to "what would this broadcast be recorded as" can be checked
/// without a database.  It returns `None` when the runtime did not
/// broadcast at all: there is no transaction to describe.
fn build_attempt(
    network: &str,
    chain_id: i64,
    payload: &Value,
    config: &Value,
) -> Result<Option<RuntimeAttempt>, String> {
    let broadcast = payload.get("broadcast").cloned().unwrap_or(Value::Null);
    if !broadcast.is_object() {
        return Ok(None);
    }
    let loop_key = payload
        .get("closedLoopKey")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "the broadcast carries no closed-loop key".to_string())?
        .to_owned();
    let signed_pnl = read_signed_pnl(payload)?;
    let signer_address = read_signer(&broadcast, config)?;
    let accepted = broadcast
        .get("ok")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let receipt = broadcast.get("receipt").cloned().unwrap_or(Value::Null);
    // The chain answers after the transaction was sent, and the answer is
    // not part of the send.  A transaction that was sent and not yet mined
    // is therefore **pending**: the send is the fact, the receipt is the
    // answer, and an attempt that is recorded as successful before the
    // chain answered is a claim nobody proved.  The broadcast confirmation
    // thread writes the terminal status once the receipt arrives.
    let outcome = receipt_status(&receipt);
    let reverted = outcome == ReceiptOutcome::Reverted
        || (outcome == ReceiptOutcome::Confirmed && mined_transaction_reverted(&receipt));
    let status = if !accepted {
        BroadcastTerminalStatus::Failed
    } else if reverted {
        BroadcastTerminalStatus::Failed
    } else if outcome == ReceiptOutcome::Confirmed {
        BroadcastTerminalStatus::Success
    } else {
        BroadcastTerminalStatus::Pending
    };
    let status_reason = match status {
        BroadcastTerminalStatus::Success => None,
        BroadcastTerminalStatus::Failed if reverted => {
            Some("the transaction was mined and reverted".to_owned())
        }
        BroadcastTerminalStatus::Failed => Some(
            broadcast
                .get("error")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .unwrap_or("the broadcast was not accepted")
                .to_owned(),
        ),
        BroadcastTerminalStatus::Pending => {
            Some("the transaction was sent and the chain has not answered yet".to_owned())
        }
    };
    let tx_hash = broadcast
        .get("txHash")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let attempt_id = runtime_attempt_id(network, chain_id, &loop_key, tx_hash.as_deref(), &status);
    let attempt = RuntimeAttempt {
        attempt_id: attempt_id.clone(),
        loop_key: loop_key.clone(),
        signer_address,
        nonce: read_nonce(&broadcast)?,
        gas_limit: read_gas_limit(&broadcast),
        gas_price_wei: None,
        max_fee_per_gas_wei: read_gas_value(&broadcast, "maxFeePerGas"),
        max_priority_fee_per_gas_wei: read_gas_value(&broadcast, "maxPriorityFeePerGas"),
        rpc_url: read_rpc_url(payload, config),
        rpc_name: None,
        tx_hash: tx_hash.clone(),
        status,
        status_reason: status_reason.clone(),
        receipt: receipt.clone(),
        evidence: build_evidence(payload, &broadcast),
        signed_pnl,
    };
    Ok(Some(attempt))
}

/// The summary the runtime publishes next to the broadcast.
fn summary(
    outcome: &RuntimeAttemptOutcome,
    attempt_id: &str,
    status_reason: Option<&str>,
) -> Value {
    json!({
        "recorded": true,
        "attemptId": attempt_id,
        "loopKey": outcome.loop_key,
        "status": outcome.status,
        "txHash": outcome.tx_hash,
        "updated": outcome.updated,
        "statusReason": status_reason,
    })
}

/// The identifier of one runtime attempt.
///
/// A transaction that reached the chain is identified by its hash, so the
/// same transaction is always filed under the same name and a retry
/// updates the row instead of adding a second one.  An attempt that never
/// produced a transaction is identified by the loop and the moment it
/// happened, because two failed attempts of the same loop are two
/// attempts.
fn runtime_attempt_id(
    network: &str,
    chain_id: i64,
    loop_key: &str,
    tx_hash: Option<&str>,
    status: &BroadcastTerminalStatus,
) -> String {
    match tx_hash {
        Some(hash) => format!("runtime:{network}:{chain_id}:{hash}"),
        None => format!(
            "runtime:{network}:{chain_id}:{loop_key}:{status:?}:{}",
            chrono::Utc::now().to_rfc3339()
        ),
    }
}

/// The measured signed profit of the route that was broadcast.
fn read_signed_pnl(payload: &Value) -> Result<SignedPnl, String> {
    let signed = payload
        .get("signedPnl")
        .and_then(Value::as_object)
        .ok_or_else(|| "the broadcast carries no measured profit".to_string())?;
    let raw = signed
        .get("raw")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "the broadcast carries no measured profit".to_string())?
        .to_owned();
    let amount = signed
        .get("amount")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| {
            "the broadcast carries no scaled profit amount; the token decimals are missing"
                .to_string()
        })?;
    let currency = signed
        .get("currency")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or_default()
        .to_owned();
    Ok(SignedPnl {
        raw,
        amount,
        currency,
        address: signed
            .get("address")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned),
        unit: signed
            .get("unit")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("raw")
            .to_owned(),
        source: signed
            .get("source")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("native_rust_ladder")
            .to_owned(),
    })
}

/// The account that signed the transaction.
///
/// The chain names it when the transaction was built.  A broadcast that
/// never reached the signing step has no signer in its result, so the
/// configured account answers for it: that is the account the runtime
/// would have used.
fn read_signer(broadcast: &Value, config: &Value) -> Result<String, String> {
    if let Some(signer) = broadcast
        .get("signer")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return Ok(signer.to_owned());
    }
    let private_key = config
        .get("broadcastPrivateKey")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            "the broadcast names no account and the configuration has no signer".to_string()
        })?;
    crate::execution::signer_address_from_private_key(private_key)
}

/// The nonce the transaction carried.
fn read_nonce(broadcast: &Value) -> Result<i64, String> {
    let nonce = broadcast.get("nonce").ok_or_else(|| {
        "the broadcast carries no nonce; the transaction was never signed".to_string()
    })?;
    if let Some(value) = nonce.as_i64() {
        return Ok(value);
    }
    nonce
        .as_str()
        .and_then(|value| value.trim().parse::<i64>().ok())
        .ok_or_else(|| "the broadcast carries a nonce that is not a number".to_string())
}

fn read_gas_limit(broadcast: &Value) -> Option<i64> {
    broadcast
        .get("gasLimit")
        .and_then(Value::as_i64)
        .or_else(|| {
            broadcast
                .get("gasLimit")
                .and_then(Value::as_str)
                .and_then(|value| value.trim().parse::<i64>().ok())
        })
}

fn read_gas_value(broadcast: &Value, field: &str) -> Option<String> {
    broadcast
        .get(field)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn read_rpc_url(payload: &Value, config: &Value) -> String {
    payload
        .get("rpcMetadata")
        .and_then(|metadata| metadata.get("selectedRpcUrl"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            config
                .get("rpcUrl")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
        })
        .unwrap_or_default()
}

/// How the chain answered the transaction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ReceiptOutcome {
    /// The chain has not answered yet.
    Pending,
    /// The transaction succeeded.
    Confirmed,
    /// The transaction was mined and reverted.
    Reverted,
}

fn receipt_status(receipt: &Value) -> ReceiptOutcome {
    let status = receipt
        .get("status")
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default()
        .to_lowercase();
    match status.as_str() {
        "0x1" | "confirmed" | "success" => ReceiptOutcome::Confirmed,
        "0x0" | "reverted" | "failed" => ReceiptOutcome::Reverted,
        _ => ReceiptOutcome::Pending,
    }
}

/// Whether the mined transaction reverted.
///
/// The outer object says **whether the chain answered**; the receipt it
/// carries says **how the transaction ended**.  A mined transaction that
/// reverted is a failure, and a mined transaction that succeeded is a
/// success — the two are told apart by the status inside the receipt, not
/// by the fact that a receipt exists.
fn mined_transaction_reverted(receipt: &Value) -> bool {
    let Some(inner) = receipt.get("receipt").and_then(Value::as_object) else {
        return false;
    };
    match inner.get("status") {
        Some(Value::Number(number)) => number.as_u64() == Some(0),
        Some(Value::String(text)) => matches!(text.trim(), "0" | "0x0" | "0x00"),
        _ => false,
    }
}

/// The evidence of the attempt.
///
/// The evidence carries the whole broadcast result and the verdicts of
/// the gates, so the record answers what it sent and what allowed it
/// without opening anything else.
fn build_evidence(payload: &Value, broadcast: &Value) -> Value {
    json!({
        "source": "native_rust_runtime",
        "broadcast": broadcast,
        "closedLoopKey": payload.get("closedLoopKey").cloned().unwrap_or(Value::Null),
        "token": payload.get("token").cloned().unwrap_or(Value::Null),
        "selectedAmountRaw": payload.get("selectedAmountRaw").cloned().unwrap_or(Value::Null),
        "selectedProfitRaw": payload.get("selectedProfitRaw").cloned().unwrap_or(Value::Null),
        "minProfitRaw": payload.get("minProfitRaw").cloned().unwrap_or(Value::Null),
        "offchainGasGate": payload.get("offchainGasGate").cloned().unwrap_or(Value::Null),
        "capacityGate": payload.get("capacityGate").cloned().unwrap_or(Value::Null),
        "economicFloorGate": payload.get("economicFloorGate").cloned().unwrap_or(Value::Null),
        "hopPriceLimitGate": payload.get("hopPriceLimitGate").cloned().unwrap_or(Value::Null),
        "rpcMetadata": payload.get("rpcMetadata").cloned().unwrap_or(Value::Null),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const LOOP_KEY: &str = "loop-1";
    const SIGNER: &str = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TX_HASH: &str = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    /// The key whose account is `PRIVATE_KEY_ACCOUNT`.
    const PRIVATE_KEY: &str =
        "0x0000000000000000000000000000000000000000000000000000000000000001";
    const PRIVATE_KEY_ACCOUNT: &str = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf";

    /// A payload of a broadcast that went through.
    fn payload() -> Value {
        json!({
            "closedLoopKey": LOOP_KEY,
            "signedPnl": {
                "raw": "1250000000000000",
                "amount": 3.5,
                "currency": "USDG",
                "address": "0x1111111111111111111111111111111111111111",
                "unit": "token",
                "source": "runArbitrage"
            },
            "broadcast": {
                "ok": true,
                "txHash": TX_HASH,
                "nonce": 42,
                "signer": SIGNER,
                "receipt": {"status": "0x1"}
            }
        })
    }

    #[test]
    fn a_broadcast_payload_becomes_an_attempt() {
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload(), &json!({}))
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.loop_key, LOOP_KEY);
        assert_eq!(attempt.attempt_id, format!("runtime:{NETWORK}:{CHAIN_ID}:{TX_HASH}"));
        assert_eq!(attempt.tx_hash.as_deref(), Some(TX_HASH));
        assert_eq!(attempt.signer_address, SIGNER);
        assert_eq!(attempt.nonce, 42);
        assert_eq!(attempt.status, BroadcastTerminalStatus::Success);
        assert!(attempt.status_reason.is_none());
        assert_eq!(attempt.signed_pnl.raw, "1250000000000000");
        assert_eq!(attempt.signed_pnl.amount, 3.5);
    }

    #[test]
    fn a_payload_without_a_broadcast_describes_no_attempt() {
        let mut payload = payload();
        payload
            .as_object_mut()
            .unwrap()
            .remove("broadcast");
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({})).unwrap();
        assert!(attempt.is_none());
    }

    #[test]
    fn a_reverted_receipt_is_a_failed_attempt() {
        let mut payload = payload();
        payload["broadcast"]["receipt"] = json!({"status": "0x0"});
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({}))
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.status, BroadcastTerminalStatus::Failed);
        assert_eq!(
            attempt.status_reason.as_deref(),
            Some("the transaction was mined and reverted")
        );
    }

    #[test]
    fn a_transaction_the_chain_has_not_answered_is_a_pending_attempt() {
        // The send is the fact and the receipt is the answer, and the
        // answer has not come yet: the attempt is pending, not
        // successful.  An attempt that claims a success nobody proved
        // is a claim, not a record.
        let mut payload = payload();
        payload["broadcast"]["receipt"] = json!({"status": "pending"});
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({}))
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.status, BroadcastTerminalStatus::Pending);
        assert_eq!(
            attempt.status_reason.as_deref(),
            Some("the transaction was sent and the chain has not answered yet")
        );
    }

    #[test]
    fn a_broadcast_without_a_receipt_is_a_pending_attempt() {
        let mut payload = payload();
        payload["broadcast"]
            .as_object_mut()
            .unwrap()
            .remove("receipt");
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({}))
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.status, BroadcastTerminalStatus::Pending);
    }

    #[test]
    fn a_mined_transaction_that_reverted_is_a_failed_attempt() {
        // The outer object says the chain answered; the receipt it
        // carries says how the transaction ended.  A mined transaction
        // that reverted is a failure, not a success.
        let mut payload = payload();
        payload["broadcast"]["receipt"] = json!({
            "status": "confirmed",
            "receipt": {"status": 0},
        });
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({}))
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.status, BroadcastTerminalStatus::Failed);
        assert_eq!(
            attempt.status_reason.as_deref(),
            Some("the transaction was mined and reverted")
        );
    }

    #[test]
    fn a_mined_transaction_that_succeeded_is_a_successful_attempt() {
        let mut payload = payload();
        payload["broadcast"]["receipt"] = json!({
            "status": "confirmed",
            "receipt": {"status": 1},
        });
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({}))
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.status, BroadcastTerminalStatus::Success);
        assert!(attempt.status_reason.is_none());
    }

    #[test]
    fn a_broadcast_that_was_not_accepted_is_a_failed_attempt() {
        let mut payload = payload();
        payload["broadcast"]["ok"] = json!(false);
        payload["broadcast"]["error"] = json!("rpc rejected transaction");
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({}))
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.status, BroadcastTerminalStatus::Failed);
        assert_eq!(
            attempt.status_reason.as_deref(),
            Some("rpc rejected transaction")
        );
    }

    #[test]
    fn a_payload_without_a_measured_profit_is_refused() {
        let mut payload = payload();
        payload.as_object_mut().unwrap().remove("signedPnl");
        let error = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({})).unwrap_err();
        assert!(error.contains("measured profit"), "{error}");
    }

    #[test]
    fn a_broadcast_without_a_nonce_is_refused() {
        let mut payload = payload();
        payload["broadcast"].as_object_mut().unwrap().remove("nonce");
        let error = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({})).unwrap_err();
        assert!(error.contains("nonce"), "{error}");
    }

    #[test]
    fn a_broadcast_without_a_signer_uses_the_configured_account() {
        let mut payload = payload();
        payload["broadcast"].as_object_mut().unwrap().remove("signer");
        let config = json!({"broadcastPrivateKey": PRIVATE_KEY});
        let attempt = build_attempt(NETWORK, CHAIN_ID, &payload, &config)
            .unwrap()
            .expect("the payload describes a broadcast");
        assert_eq!(attempt.signer_address, PRIVATE_KEY_ACCOUNT);
    }

    #[test]
    fn a_broadcast_without_a_signer_and_without_a_key_is_refused() {
        let mut payload = payload();
        payload["broadcast"].as_object_mut().unwrap().remove("signer");
        let error = build_attempt(NETWORK, CHAIN_ID, &payload, &json!({})).unwrap_err();
        assert!(error.contains("names no account"), "{error}");
    }
}
