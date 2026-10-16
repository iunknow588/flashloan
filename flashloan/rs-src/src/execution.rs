use std::env;
use std::time::{Duration, Instant};

use hex::{decode as hex_decode, encode as hex_encode};
use k256::ecdsa::signature::hazmat::PrehashSigner;
use k256::ecdsa::{SigningKey, VerifyingKey};
use num_bigint::BigUint;
use num_traits::Zero;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use serde_json::{json, Value};
use tiny_keccak::{Hasher, Keccak};

use crate::config::parse_u64_hexish;
use crate::json_result_to_py;

const V4_QUOTE_SIGNATURE: &str =
    "quoteExactInputSingle((address,address,uint24,int24,address),bool,uint128,bytes)";
const MAX_U256: &str =
    "115792089237316195423570985008687907853269984665640564039457584007913129639935";
const DEFAULT_QUOTE_GAS: u64 = 30_000_000;
const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

fn as_py_error(message: String) -> PyErr {
    PyRuntimeError::new_err(message)
}

fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut output = [0u8; 32];
    let mut hasher = Keccak::v256();
    hasher.update(data);
    hasher.finalize(&mut output);
    output
}

fn strip_0x(value: &str) -> &str {
    value.strip_prefix("0x").unwrap_or(value)
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    hex_decode(strip_0x(value)).map_err(|error| format!("invalid hex {value}: {error}"))
}

fn as_hex(value: &[u8]) -> String {
    format!("0x{}", hex_encode(value))
}

fn parse_u64_value(value: &Value) -> Result<u64, String> {
    if let Some(number) = value.as_u64() {
        return Ok(number);
    }
    if let Some(text) = value.as_str() {
        return parse_u64_hexish(text);
    }
    Err(format!("expected integer, got {value}"))
}

fn parse_i64_value(value: &Value) -> Result<i64, String> {
    if let Some(number) = value.as_i64() {
        return Ok(number);
    }
    if let Some(text) = value.as_str() {
        let text = text.trim();
        let unsigned = text.strip_prefix('-').unwrap_or(text);
        let parsed = parse_u64_hexish(unsigned)?;
        return Ok(if text.starts_with('-') {
            -(parsed as i64)
        } else {
            parsed as i64
        });
    }
    Err(format!("expected integer, got {value}"))
}

fn parse_big_uint(value: &Value) -> Result<BigUint, String> {
    if let Some(text) = value.as_str() {
        return BigUint::parse_bytes(strip_0x(text).as_bytes(), 16)
            .ok_or_else(|| format!("invalid big integer {text}"));
    }
    if let Some(number) = value.as_u64() {
        return Ok(BigUint::from(number));
    }
    Err(format!("expected integer, got {value}"))
}

fn uint_to_bytes(value: &BigUint) -> Vec<u8> {
    value.to_bytes_be()
}

fn rlp_encode_bytes(bytes: &[u8]) -> Vec<u8> {
    if bytes.len() == 1 && bytes[0] < 0x80 {
        return vec![bytes[0]];
    }
    if bytes.len() <= 55 {
        let mut output = vec![0x80 + bytes.len() as u8];
        output.extend_from_slice(bytes);
        return output;
    }
    let length_bytes = bytes.len().to_be_bytes();
    let start = length_bytes
        .iter()
        .position(|byte| *byte != 0)
        .unwrap_or(length_bytes.len() - 1);
    let mut output = vec![0xb7 + (length_bytes.len() - start) as u8];
    output.extend_from_slice(&length_bytes[start..]);
    output.extend_from_slice(bytes);
    output
}

fn rlp_encode_integer(value: &BigUint) -> Vec<u8> {
    if value.is_zero() {
        return rlp_encode_bytes(&[]);
    }
    let mut bytes = uint_to_bytes(value);
    if bytes[0] & 0x80 != 0 {
        let mut prefixed = vec![0u8];
        prefixed.append(&mut bytes);
        return rlp_encode_bytes(&prefixed);
    }
    rlp_encode_bytes(&bytes)
}

fn rlp_encode_list(items: &[Vec<u8>]) -> Vec<u8> {
    let mut payload = Vec::new();
    for item in items {
        payload.extend_from_slice(item);
    }
    if payload.len() <= 55 {
        let mut output = vec![0xc0 + payload.len() as u8];
        output.extend_from_slice(&payload);
        return output;
    }
    let length_bytes = payload.len().to_be_bytes();
    let start = length_bytes
        .iter()
        .position(|byte| *byte != 0)
        .unwrap_or(length_bytes.len() - 1);
    let mut output = vec![0xf7 + (length_bytes.len() - start) as u8];
    output.extend_from_slice(&length_bytes[start..]);
    output.extend_from_slice(&payload);
    output
}

fn u256_bytes(value: &BigUint) -> Result<[u8; 32], String> {
    let raw = uint_to_bytes(value);
    if raw.len() > 32 {
        return Err(format!("integer exceeds uint256: {value}"));
    }
    let mut output = [0u8; 32];
    output[32 - raw.len()..].copy_from_slice(&raw);
    Ok(output)
}

fn int256_signed(value: i64) -> Result<[u8; 32], String> {
    let modulus = (BigUint::parse_bytes(MAX_U256.as_bytes(), 10).expect("MAX_U256 parses")) + BigUint::from(1u8);
    let encoded = if value < 0 {
        let magnitude = BigUint::from((value as i128).unsigned_abs());
        &modulus - (magnitude % &modulus)
    } else {
        BigUint::from(value as u64)
    };
    u256_bytes(&encoded)
}

fn word_uint(value: &BigUint) -> Result<Vec<u8>, String> {
    Ok(u256_bytes(value)?.to_vec())
}

fn word_bool(value: bool) -> Vec<u8> {
    let mut word = [0u8; 32];
    word[31] = u8::from(value);
    word.to_vec()
}

fn word_address(value: &str) -> Result<Vec<u8>, String> {
    let raw = strip_0x(value);
    if raw.len() != 40 {
        return Err(format!("invalid Ethereum address: {value}"));
    }
    let mut word = [0u8; 32];
    let bytes = hex_decode(raw).map_err(|error| format!("invalid Ethereum address {value}: {error}"))?;
    word[12..].copy_from_slice(&bytes);
    Ok(word.to_vec())
}

fn encode_bytes_tail(data: &[u8]) -> Vec<u8> {
    let mut tail = vec![0u8; 32];
    let length = BigUint::from(data.len());
    tail[32 - uint_to_bytes(&length).len()..].copy_from_slice(&uint_to_bytes(&length));
    tail.extend_from_slice(data);
    while tail.len() % 32 != 0 {
        tail.push(0);
    }
    tail
}

fn function_selector(signature: &str) -> Vec<u8> {
    keccak256(signature.as_bytes())[..4].to_vec()
}

fn quote_pool_key_from_value(request: &Value) -> Result<Vec<u8>, String> {
    let mut head = Vec::new();
    head.extend_from_slice(&word_address(
        request
            .get("currency0")
            .and_then(Value::as_str)
            .ok_or_else(|| "currency0 is required".to_string())?,
    )?);
    head.extend_from_slice(&word_address(
        request
            .get("currency1")
            .and_then(Value::as_str)
            .ok_or_else(|| "currency1 is required".to_string())?,
    )?);
    let fee = parse_u64_value(request.get("fee").ok_or_else(|| "fee is required".to_string())?)?;
    head.extend_from_slice(&word_uint(&BigUint::from(fee))?);
    let tick_spacing = parse_i64_value(
        request
            .get("tickSpacing")
            .ok_or_else(|| "tickSpacing is required".to_string())?,
    )?;
    head.extend_from_slice(&int256_signed(tick_spacing)?);
    head.extend_from_slice(&word_address(
        request
            .get("hooks")
            .and_then(Value::as_str)
            .unwrap_or(ZERO_ADDRESS),
    )?);
    Ok(head)
}

fn build_v4_quote_calldata_inner(request: &Value) -> Result<String, String> {
    let zero_for_one = request
        .get("zeroForOne")
        .and_then(Value::as_bool)
        .ok_or_else(|| "zeroForOne is required".to_string())?;
    let amount_in_raw = parse_big_uint(
        request
            .get("amountInRaw")
            .ok_or_else(|| "amountInRaw is required".to_string())?,
    )?;
    let hook_data_raw = request.get("hookData").and_then(Value::as_str).unwrap_or("").to_string();
    let hook_data = if hook_data_raw.is_empty() {
        Vec::new()
    } else {
        decode_hex(&hook_data_raw)?
    };

    let mut head = function_selector(V4_QUOTE_SIGNATURE);
    head.extend_from_slice(&quote_pool_key_from_value(request)?);
    head.extend_from_slice(&word_bool(zero_for_one));
    head.extend_from_slice(&word_uint(&amount_in_raw)?);
    let hook_data_offset = BigUint::from((head.len() - 4) + 32);
    head.extend_from_slice(&word_uint(&hook_data_offset)?);
    head.extend_from_slice(&encode_bytes_tail(&hook_data));
    Ok(as_hex(&head))
}

fn transaction_chain_id(transaction: &Value) -> Result<Option<BigUint>, String> {
    transaction.get("chainId").map(parse_big_uint).transpose()
}

fn encode_address_field(value: &Value) -> Result<Vec<u8>, String> {
    let address = value.as_str().unwrap_or("");
    let raw = strip_0x(address);
    if raw.is_empty() {
        return Ok(rlp_encode_bytes(&[]));
    }
    if raw.len() != 40 {
        return Err(format!("invalid transaction recipient address: {address}"));
    }
    Ok(rlp_encode_bytes(&hex_decode(raw).map_err(|error| format!("invalid recipient hex {address}: {error}"))?))
}

fn encode_data_field(value: &Value) -> Result<Vec<u8>, String> {
    let data = value.as_str().unwrap_or("");
    if data.is_empty() {
        return Ok(rlp_encode_bytes(&[]));
    }
    Ok(rlp_encode_bytes(&decode_hex(data)?))
}

fn access_list_field(value: &Value) -> Vec<u8> {
    let _ = value;
    rlp_encode_list(&[])
}

fn unsigned_1559_items(transaction: &Value) -> Result<Vec<Vec<u8>>, String> {
    let mut items = Vec::new();
    items.push(rlp_encode_integer(
        &transaction_chain_id(transaction)?
            .ok_or_else(|| "EIP-1559 chainId is required".to_string())?,
    ));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("nonce").ok_or_else(|| "nonce is required".to_string())?,
    )?));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction
            .get("maxPriorityFeePerGas")
            .ok_or_else(|| "maxPriorityFeePerGas is required".to_string())?,
    )?));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction
            .get("maxFeePerGas")
            .ok_or_else(|| "maxFeePerGas is required".to_string())?,
    )?));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("gas").ok_or_else(|| "gas is required".to_string())?,
    )?));
    items.push(encode_address_field(transaction.get("to").unwrap_or(&Value::Null))?);
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("value").unwrap_or(&json!(0)),
    )?));
    items.push(encode_data_field(transaction.get("data").unwrap_or(&Value::Null))?);
    items.push(access_list_field(transaction.get("accessList").unwrap_or(&Value::Null)));
    Ok(items)
}

fn unsigned_155_items(transaction: &Value, chain_id: &BigUint) -> Result<Vec<Vec<u8>>, String> {
    let mut items = Vec::new();
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("nonce").ok_or_else(|| "nonce is required".to_string())?,
    )?));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction
            .get("gasPrice")
            .ok_or_else(|| "gasPrice is required".to_string())?,
    )?));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("gas").ok_or_else(|| "gas is required".to_string())?,
    )?));
    items.push(encode_address_field(transaction.get("to").unwrap_or(&Value::Null))?);
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("value").unwrap_or(&json!(0)),
    )?));
    items.push(encode_data_field(transaction.get("data").unwrap_or(&Value::Null))?);
    items.push(rlp_encode_integer(chain_id));
    items.push(rlp_encode_integer(&BigUint::zero()));
    items.push(rlp_encode_integer(&BigUint::zero()));
    Ok(items)
}

fn address_from_verifying_key(verifying_key: &VerifyingKey) -> String {
    let point = verifying_key.to_encoded_point(false);
    let x = point.x().expect("secp256k1 public key has x coordinate");
    let y = point.y().expect("secp256k1 public key has y coordinate");
    let mut uncompressed = x.to_vec();
    uncompressed.extend_from_slice(&y.to_vec());
    let hash = keccak256(&uncompressed);
    as_hex(&hash[12..])
}

fn sign_typed_transaction_inner(transaction: &Value, private_key: &str) -> Result<Value, String> {
    let key_hex = strip_0x(private_key);
    let key_bytes = hex_decode(key_hex).map_err(|error| format!("invalid private key hex: {error}"))?;
    if key_bytes.len() != 32 {
        return Err(format!("private key must be 32 bytes, got {}", key_bytes.len()));
    }
    let key_array = k256::FieldBytes::clone_from_slice(&key_bytes);
    let signing_key =
        SigningKey::from_bytes(&key_array).map_err(|error| format!("invalid secp256k1 private key: {error}"))?;
    let verifying_key = signing_key.verifying_key();
    let address = address_from_verifying_key(verifying_key);

    let is_typed_1559 = transaction
        .get("type")
        .and_then(Value::as_u64)
        .map(|value| value == 2)
        .unwrap_or_else(|| transaction.get("maxFeePerGas").is_some());
    let chain_id = transaction_chain_id(transaction)?;
    let (unsigned_items, message) = if is_typed_1559 {
        let items = unsigned_1559_items(transaction)?;
        let message = {
            let mut payload = vec![0x02];
            payload.extend_from_slice(&rlp_encode_list(&items));
            keccak256(&payload).to_vec()
        };
        (items, message)
    } else {
        let chain_id = chain_id.clone().unwrap_or_default();
        let items = unsigned_155_items(transaction, &chain_id)?;
        let message = keccak256(&rlp_encode_list(&items)).to_vec();
        (items, message)
    };

    let (signature, recovery_id) = signing_key
        .sign_prehash(&message)
        .map_err(|error| format!("transaction signing failed: {error}"))?;
    let raw_r = BigUint::from_bytes_be(signature.r().to_bytes().as_ref());
    let raw_s = BigUint::from_bytes_be(signature.s().to_bytes().as_ref());
    let y_parity = u64::from(recovery_id.is_y_odd());

    let mut signed_items = unsigned_items;
    let r_bytes = u256_bytes(&raw_r)?;
    let s_bytes = u256_bytes(&raw_s)?;
    if is_typed_1559 {
        signed_items.push(rlp_encode_integer(&BigUint::from(y_parity)));
        signed_items.push(rlp_encode_bytes(&r_bytes));
        signed_items.push(rlp_encode_bytes(&s_bytes));
        let mut raw = vec![0x02];
        raw.extend_from_slice(&rlp_encode_list(&signed_items));
        Ok(json!({
            "raw": as_hex(&raw),
            "hash": as_hex(&keccak256(&raw)),
            "signer": address,
            "yParity": format!("{y_parity}"),
            "v": format!("{y_parity}"),
            "r": format!("0x{:x}", raw_r),
            "s": format!("0x{:x}", raw_s),
        }))
    } else {
        let chain_id = chain_id.clone().unwrap_or_default();
        let legacy_v = if chain_id.is_zero() {
            BigUint::from(27 + y_parity)
        } else {
            (&chain_id * BigUint::from(2u8)) + BigUint::from(35 + y_parity)
        };
        signed_items.truncate(6);
        signed_items.push(rlp_encode_integer(&legacy_v));
        signed_items.push(rlp_encode_bytes(&r_bytes));
        signed_items.push(rlp_encode_bytes(&s_bytes));
        let raw = rlp_encode_list(&signed_items);
        Ok(json!({
            "raw": as_hex(&raw),
            "hash": as_hex(&keccak256(&raw)),
            "signer": address,
            "yParity": format!("{y_parity}"),
            "v": format!("0x{:x}", legacy_v),
            "r": format!("0x{:x}", raw_r),
            "s": format!("0x{:x}", raw_s),
        }))
    }
}

fn resolve_private_key_hex_inner(private_key: &str) -> Result<String, String> {
    let key_hex = strip_0x(private_key);
    let key_bytes = hex_decode(key_hex).map_err(|error| format!("invalid private key hex: {error}"))?;
    if key_bytes.len() != 32 {
        return Err(format!("private key must be 32 bytes, got {}", key_bytes.len()));
    }
    let key_array = k256::FieldBytes::clone_from_slice(&key_bytes);
    let signing_key =
        SigningKey::from_bytes(&key_array).map_err(|error| format!("invalid secp256k1 private key: {error}"))?;
    Ok(address_from_verifying_key(signing_key.verifying_key()))
}

fn redact_rpc_text(value: &str) -> String {
    let mut output = String::new();
    let mut rest = value;
    while let Some(scheme_end) = rest.find("://") {
        let after_scheme = &rest[scheme_end + 3..];
        let authority_end = after_scheme
            .find(|character: char| character == '/' || character == '?' || character == '#' || character.is_whitespace())
            .unwrap_or(after_scheme.len());
        output.push_str(&rest[..scheme_end + 3]);
        output.push_str("[REDACTED]");
        rest = &after_scheme[authority_end..];
    }
    output.push_str(rest);
    output
}

fn configured_relay_urls(raw: Option<&str>) -> Vec<(String, String)> {
    let value = raw
        .map(|item| item.to_string())
        .unwrap_or_else(|| env::var("LIQUIDATION_PRIVATE_RPC_URLS").unwrap_or_default());
    let mut relays = Vec::new();
    for (index, token) in value.split(',').enumerate() {
        let token = token.trim();
        if token.is_empty() {
            continue;
        }
        if let Some((name, url)) = token.split_once('=') {
            let name = name.trim();
            let url = url.trim();
            if !url.is_empty() {
                relays.push((
                    if name.is_empty() { format!("private_{}", index + 1) } else { name.to_string() },
                    url.to_string(),
                ));
            }
        } else {
            relays.push((format!("private_{}", index + 1), token.to_string()));
        }
    }
    relays
}

async fn json_rpc_call(
    client: &reqwest::Client,
    rpc_url: &str,
    method: &str,
    params: Value,
    timeout_seconds: u64,
) -> Result<Value, (String, Option<String>)> {
    let result = client
        .post(rpc_url)
        .json(&json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
        .timeout(Duration::from_secs(timeout_seconds))
        .send()
        .await;
    let response = match result {
        Ok(response) => response,
        Err(error) => return Err((format!("{method} RPC request failed: {error}"), None)),
    };
    let payload = match response.json::<Value>().await {
        Ok(payload) => payload,
        Err(error) => return Err((format!("{method} RPC response was not JSON: {error}"), None)),
    };
    if let Some(error) = payload.get("error") {
        let message = error
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or(&error.to_string())
            .to_string();
        let revert_data = error.get("data").and_then(Value::as_str).map(str::to_string);
        return Err((message, revert_data));
    }
    payload
        .get("result")
        .cloned()
        .ok_or_else(|| ("RPC response has no result".to_string(), None))
}

fn execute_rpc<T, F>(timeout_seconds: u64, block: F) -> Result<T, String>
where
    F: FnOnce(&tokio::runtime::Runtime, &reqwest::Client) -> Result<T, String>,
{
    let runtime = tokio::runtime::Runtime::new().map_err(|error| format!("tokio runtime failed: {error}"))?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_seconds.max(1)))
        .build()
        .map_err(|error| format!("HTTP client failed: {error}"))?;
    block(&runtime, &client)
}

fn execute_rpc_with_revert<F>(
    timeout_seconds: u64,
    block: F,
) -> Result<Value, (String, Option<String>)>
where
    F: FnOnce(&tokio::runtime::Runtime, &reqwest::Client) -> Result<Value, (String, Option<String>)>,
{
    let runtime = tokio::runtime::Runtime::new()
        .map_err(|error| (format!("tokio runtime failed: {error}"), None))?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_seconds.max(1)))
        .build()
        .map_err(|error| (format!("HTTP client failed: {error}"), None))?;
    block(&runtime, &client)
}

fn quote_v4_exact_input_single_inner(
    request: &Value,
    rpc_url: &str,
    quoter_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    if rpc_url.trim().is_empty() {
        return Err("quote RPC URL is empty".to_string());
    }
    let calldata = build_v4_quote_calldata_inner(request)?;
    let mut call: serde_json::Map<String, Value> = serde_json::Map::new();
    call.insert("to".to_string(), json!(quoter_address.to_lowercase()));
    call.insert("data".to_string(), json!(calldata));
    call.insert("gas".to_string(), json!(format!("0x{DEFAULT_QUOTE_GAS:x}")));
    if let Some(from) = call_from {
        call.insert("from".to_string(), json!(from.to_lowercase()));
    }
    let attempts = 3u64;
    let mut last_error: Option<String> = None;
    let mut revert_data: Option<String> = None;
    for attempt in 0..attempts {
        let result = execute_rpc_with_revert(timeout_seconds.max(1), |runtime, client| {
            runtime.block_on(json_rpc_call(
                client,
                rpc_url,
                "eth_call",
                json!([call, "latest"]),
                timeout_seconds.max(1),
            ))
        });
        match result {
            Ok(result) => {
                let result_hex = result
                    .as_str()
                    .ok_or_else(|| "quote eth_call result is not a hex string".to_string())?;
                let raw = strip_0x(result_hex);
                if raw.len() < 128 {
                    return Err(format!("quote eth_call returned short result: {result_hex}"));
                }
                let amount_out = BigUint::parse_bytes(&raw[..64].as_bytes(), 16)
                    .ok_or_else(|| "quote amountOut decode failed".to_string())?;
                let gas_estimate = BigUint::parse_bytes(&raw[64..128].as_bytes(), 16)
                    .ok_or_else(|| "quote gasEstimate decode failed".to_string())?;
                return Ok(json!({
                    "ok": true,
                    "amountOutRaw": format!("0x{:x}", amount_out),
                    "gasEstimate": format!("0x{:x}", gas_estimate),
                }));
            }
            Err((message, revert)) => {
                if revert.is_some() {
                    revert_data = revert;
                    last_error = Some(message.clone());
                    break;
                }
                last_error = Some(message.clone());
                if attempt + 1 < attempts {
                    std::thread::sleep(Duration::from_millis(400 * (attempt + 1)));
                }
            }
        }
    }
    Ok(json!({
        "ok": false,
        "error": redact_rpc_text(last_error.as_deref().unwrap_or("quote eth_call failed")),
        "revertData": revert_data.unwrap_or_default(),
    }))
}

fn submit_v4_route_inner(
    config: &Value,
    raw_transaction: &str,
    relay_urls: Option<&str>,
) -> Result<Value, String> {
    let raw = decode_hex(raw_transaction)?;
    let rpc_url = config.get("rpc_url").and_then(Value::as_str).unwrap_or("").to_string();
    let timeout_seconds = config.get("timeout_seconds").and_then(Value::as_u64).unwrap_or(15);
    let allow_public_fallback = config
        .get("allow_public_fallback")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let target_block = config
        .get("target_block")
        .and_then(Value::as_u64)
        .map(|value| value.to_string())
        .unwrap_or_default();
    let relays = configured_relay_urls(relay_urls);
    let mut metrics = Vec::new();
    let mut errors = Vec::new();
    for (name, url) in relays {
        let started = Instant::now();
        let result = execute_rpc(timeout_seconds.max(1), |runtime, client| {
            runtime
                .block_on(json_rpc_call(
                    client,
                    &url,
                    "eth_sendRawTransaction",
                    json!([as_hex(&raw)]),
                    timeout_seconds.max(1),
                ))
                .map_err(|(message, _)| message)
        });
        let response_ms = started.elapsed().as_millis().max(0) as u64;
        match result {
            Ok(tx_hash) => {
                metrics.push(json!({
                    "channel": "private_rpc",
                    "relay": name,
                    "responseMs": response_ms,
                    "submittedAt": chrono::Utc::now().to_rfc3339(),
                    "targetBlock": target_block,
                    "status": "accepted_by_private_rpc",
                }));
                return Ok(json!({
                    "tx_hash": tx_hash.as_str().map(str::to_string).unwrap_or_default(),
                    "broadcast_channel": "private_rpc",
                    "relay": name,
                    "private_relay_metrics": metrics,
                }));
            }
            Err(message) => {
                let redacted = redact_rpc_text(&message);
                errors.push(json!({"relay": name, "error": redacted}));
                metrics.push(json!({
                    "channel": "private_rpc",
                    "relay": name,
                    "responseMs": response_ms,
                    "submittedAt": chrono::Utc::now().to_rfc3339(),
                    "targetBlock": target_block,
                    "status": "relay_error",
                }));
            }
        }
    }
    if !allow_public_fallback {
        return Ok(json!({
            "tx_hash": Value::Null,
            "broadcast_channel": "not_broadcast",
            "status": "private_relay_failed_public_fallback_disabled",
            "relay_errors": errors,
            "private_relay_metrics": metrics,
        }));
    }
    if rpc_url.trim().is_empty() {
        return Err("public RPC URL is empty; cannot fall back".to_string());
    }
    let started = Instant::now();
    let result = execute_rpc(timeout_seconds.max(1), |runtime, client| {
        runtime
            .block_on(json_rpc_call(
                client,
                &rpc_url,
                "eth_sendRawTransaction",
                json!([as_hex(&raw)]),
                timeout_seconds.max(1),
            ))
            .map_err(|(message, _)| message)
    });
    let response_ms = started.elapsed().as_millis().max(0) as u64;
    match result {
        Ok(tx_hash) => {
            metrics.push(json!({
                "channel": "public_rpc",
                "relay": "",
                "responseMs": response_ms,
                "submittedAt": chrono::Utc::now().to_rfc3339(),
                "targetBlock": target_block,
                "status": "public_fallback_submitted",
            }));
            Ok(json!({
                "tx_hash": tx_hash.as_str().map(str::to_string).unwrap_or_default(),
                "broadcast_channel": "public_rpc",
                "relay_errors": errors,
                "private_relay_metrics": metrics,
            }))
        }
        Err(message) => Ok(json!({
            "tx_hash": Value::Null,
            "broadcast_channel": "not_broadcast",
            "status": "public_fallback_failed",
            "relay_errors": errors,
            "error": redact_rpc_text(&message),
            "private_relay_metrics": metrics,
        })),
    }
}

fn normalize_receipt(tx_hash: &str, receipt: &Value) -> Value {
    let status = receipt
        .get("status")
        .and_then(Value::as_str)
        .and_then(|value| parse_u64_hexish(value).ok())
        .unwrap_or_default();
    json!({
        "hash": tx_hash.to_lowercase(),
        "transactionHash": tx_hash.to_lowercase(),
        "blockNumber": receipt.get("blockNumber").cloned().unwrap_or(Value::Null),
        "status": status,
        "gasUsed": receipt.get("gasUsed").cloned().unwrap_or(Value::Null),
        "effectiveGasPrice": receipt.get("effectiveGasPrice").cloned().unwrap_or(Value::Null),
        "from": receipt.get("from").cloned().unwrap_or(Value::Null),
        "to": receipt.get("to").cloned().unwrap_or(Value::Null),
        "logs": receipt.get("logs").cloned().unwrap_or_else(|| json!([])),
        "blockHash": receipt.get("blockHash").cloned().unwrap_or(Value::Null),
        "transactionIndex": receipt.get("transactionIndex").cloned().unwrap_or(Value::Null),
        "cumulativeGasUsed": receipt.get("cumulativeGasUsed").cloned().unwrap_or(Value::Null),
        "contractAddress": receipt.get("contractAddress").cloned().unwrap_or(Value::Null),
        "logsBloom": receipt.get("logsBloom").cloned().unwrap_or(Value::Null),
    })
}

fn normalize_transaction(tx_hash: &str, transaction: &Value) -> Value {
    json!({
        "hash": tx_hash.to_lowercase(),
        "from": transaction.get("from").cloned().unwrap_or(Value::Null),
        "to": transaction.get("to").cloned().unwrap_or(Value::Null),
        "blockNumber": transaction.get("blockNumber").cloned().unwrap_or(Value::Null),
        "value": transaction.get("value").cloned().unwrap_or(Value::Null),
    })
}

fn load_v4_receipt_inner(
    rpc_urls: &[String],
    tx_hash: &str,
    wait: bool,
    timeout_seconds: u64,
) -> Result<Value, String> {
    let normalized_hash = tx_hash.to_lowercase();
    if rpc_urls.is_empty() {
        return Err("no RPC URLs configured for receipt lookup".to_string());
    }
    let deadline = Instant::now() + Duration::from_secs(timeout_seconds.max(1));
    loop {
        for rpc_url in rpc_urls {
            let result = execute_rpc(timeout_seconds.max(1), |runtime, client| {
                runtime
                    .block_on(json_rpc_call(
                        client,
                        rpc_url,
                        "eth_getTransactionReceipt",
                        json!([normalized_hash]),
                        timeout_seconds.max(1),
                    ))
                    .map_err(|(message, _)| message)
            });
            match result {
                Ok(receipt) => {
                    if !receipt.is_null() {
                        return Ok(json!({
                            "status": "confirmed",
                            "rpcUrl": rpc_url,
                            "receipt": normalize_receipt(&normalized_hash, &receipt),
                            "transaction": Value::Null,
                        }));
                    }
                    let transaction = execute_rpc(timeout_seconds.max(1), |runtime, client| {
                        runtime
                            .block_on(json_rpc_call(
                                client,
                                rpc_url,
                                "eth_getTransactionByHash",
                                json!([normalized_hash]),
                                timeout_seconds.max(1),
                            ))
                            .map_err(|(message, _)| message)
                    });
                    if let Ok(transaction) = transaction {
                        if !transaction.is_null() {
                            return Ok(json!({
                                "status": "pending",
                                "rpcUrl": rpc_url,
                                "receipt": Value::Null,
                                "transaction": normalize_transaction(&normalized_hash, &transaction),
                            }));
                        }
                    }
                }
                Err(message) => {
                    let _ = format!("{rpc_url}: {message}");
                }
            }
        }
        if !wait {
            return Ok(json!({
                "status": "pending",
                "rpcUrl": rpc_urls.first().cloned().unwrap_or_default(),
                "receipt": Value::Null,
                "transaction": Value::Null,
            }));
        }
        if Instant::now() >= deadline {
            return Err("tx_receipt_timeout".to_string());
        }
        std::thread::sleep(Duration::from_millis(1200));
    }
}

fn get_v4_pending_nonce_inner(
    rpc_url: &str,
    signer_address: &str,
    timeout_seconds: u64,
) -> Result<Value, String> {
    if rpc_url.trim().is_empty() {
        return Err("nonce RPC URL is empty".to_string());
    }
    let result = execute_rpc(timeout_seconds.max(1), |runtime, client| {
        runtime
            .block_on(json_rpc_call(
                client,
                rpc_url,
                "eth_getTransactionCount",
                json!([signer_address.to_lowercase(), "pending"]),
                timeout_seconds.max(1),
            ))
            .map_err(|(message, _)| message)
    });
    let nonce = result?;
    let nonce_text = nonce
        .as_str()
        .ok_or_else(|| "nonce result is not a hex string".to_string())?;
    Ok(json!({"nonce": parse_u64_hexish(nonce_text)?}))
}

fn parse_json(raw: &str, label: &str) -> Result<Value, PyErr> {
    serde_json::from_str(raw).map_err(|error| as_py_error(format!("{label} must be valid JSON: {error}")))
}

#[pyfunction]
pub fn resolve_private_key_hex(private_key: String) -> PyResult<PyObject> {
    let address = resolve_private_key_hex_inner(&private_key).map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, json!({"address": address}))))
}

#[pyfunction]
#[pyo3(signature = (transaction_json, private_key))]
pub fn sign_typed_transaction_json(transaction_json: String, private_key: String) -> PyResult<PyObject> {
    let transaction = parse_json(&transaction_json, "transaction")?;
    let signed = sign_typed_transaction_inner(&transaction, &private_key).map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, signed)))
}

#[pyfunction]
#[pyo3(signature = (request_json))]
pub fn build_v4_quote_calldata(request_json: String) -> PyResult<PyObject> {
    let request = parse_json(&request_json, "request")?;
    let calldata = build_v4_quote_calldata_inner(&request).map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, json!({"calldata": calldata}))))
}

#[pyfunction]
#[pyo3(signature = (request_json, rpc_url, quoter_address, call_from=None, timeout_seconds=20))]
pub fn quote_v4_exact_input_single(
    request_json: String,
    rpc_url: String,
    quoter_address: String,
    call_from: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let request = parse_json(&request_json, "request")?;
    let result = quote_v4_exact_input_single_inner(
        &request,
        &rpc_url,
        &quoter_address,
        call_from.as_deref(),
        timeout_seconds,
    )
    .unwrap_or_else(|error| json!({"ok": false, "error": error, "revertData": ""}));
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (config_json, raw_transaction, relay_urls=None))]
pub fn submit_v4_route(
    config_json: String,
    raw_transaction: String,
    relay_urls: Option<String>,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let result = submit_v4_route_inner(&config, &raw_transaction, relay_urls.as_deref()).map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (rpc_urls_json, tx_hash, wait, timeout_seconds))]
pub fn load_v4_receipt(
    rpc_urls_json: String,
    tx_hash: String,
    wait: bool,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let rpc_urls_value = parse_json(&rpc_urls_json, "rpc_urls")?;
    let rpc_urls: Vec<String> = rpc_urls_value
        .as_array()
        .ok_or_else(|| PyValueError::new_err("rpc_urls must be a JSON array"))?
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect();
    let result = load_v4_receipt_inner(&rpc_urls, &tx_hash, wait, timeout_seconds).map_err(|error| {
        if error == "tx_receipt_timeout" {
            PyRuntimeError::new_err("tx_receipt_timeout")
        } else {
            as_py_error(error)
        }
    })?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (rpc_url, signer_address, timeout_seconds=20))]
pub fn get_v4_pending_nonce(
    rpc_url: String,
    signer_address: String,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let result = get_v4_pending_nonce_inner(&rpc_url, &signer_address, timeout_seconds).map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[cfg(test)]
mod tests {
    use super::*;

    const PRIVATE_KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    fn sign_for_test(transaction: Value) -> Value {
        sign_typed_transaction_inner(&transaction, PRIVATE_KEY).expect("sign succeeds")
    }

    #[test]
    fn private_key_resolves_known_address() {
        let signed = sign_for_test(json!({
            "type": 2,
            "chainId": 4663,
            "nonce": 0,
            "maxPriorityFeePerGas": 1,
            "maxFeePerGas": 1,
            "gas": 21000,
            "to": "0x1111111111111111111111111111111111111111",
            "value": 0,
            "data": "",
        }));
        assert_eq!(
            signed["signer"].as_str().unwrap(),
            "0xfcad0b19bb29d4674531d6f115237e16afce377c"
        );
    }

    #[test]
    fn rlp_encodes_known_vectors() {
        assert_eq!(rlp_encode_integer(&BigUint::from(0u8)), vec![0x80]);
        assert_eq!(rlp_encode_integer(&BigUint::from(7u8)), vec![0x07]);
        assert_eq!(
            rlp_encode_integer(&BigUint::from(100_000u32)),
            vec![0x83, 0x01, 0x86, 0xa0]
        );
        assert_eq!(rlp_encode_list(&[vec![0x80], vec![0x80]]), vec![0xc2, 0x80, 0x80]);
    }

    #[test]
    fn eip1559_signing_matches_python_vector() {
        let signed = sign_for_test(json!({
            "type": 2,
            "chainId": 4663,
            "nonce": 7,
            "maxPriorityFeePerGas": 1000000000u64,
            "maxFeePerGas": 30000000000u64,
            "gas": 21000,
            "to": "0x1111111111111111111111111111111111111111",
            "value": 123456789,
            "data": "0xdeadbeef",
        }));
        assert_eq!(
            signed["raw"].as_str().unwrap(),
            "0x02f87582123707843b9aca008506fc23ac0082520894111111111111111111111111111111111111111184075bcd1584deadbeefc001a0cdbfb813c5d5642cea775332969b9b9196b2b5f7f976d0d5854cc48ad82025e7a046d07f27dfd635ee0c02401c3bf845564fcdc56837e3befc31b1197d7900c598"
        );
        assert_eq!(signed["yParity"].as_str().unwrap(), "1");
        assert_eq!(signed["hash"].as_str().unwrap(), "0x6dff37dd1a996412e30723462231d2a67bc7d66702b498aa739746797c4302a3");
    }

    #[test]
    fn eip155_signing_matches_python_vector() {
        let signed = sign_for_test(json!({
            "chainId": 4663,
            "nonce": 3,
            "gasPrice": 25000000000u64,
            "gas": 21000,
            "to": "0x2222222222222222222222222222222222222222",
            "value": 0,
            "data": "",
        }));
        assert_eq!(
            signed["raw"].as_str().unwrap(),
            "0xf866038505d21dba008252089422222222222222222222222222222222222222228080822492a0301ebd5bbcba5f2696b79b00ae2f232956872c16da3dd34e67d8334eedafdc32a04bd09628410a977139f5a6762063c083072291816c174109ed42b03a8bab1ab8"
        );
        assert_eq!(signed["v"].as_str().unwrap(), "0x2492");
        assert_eq!(signed["hash"].as_str().unwrap(), "0x94f30669049e68cf827b37b07f41aad94bed8230ef9ad54db4128048b85c833e");
    }

    #[test]
    fn quote_calldata_matches_python_vector() {
        let request = json!({
            "currency0": "0x0000000000000000000000000000000000000000",
            "currency1": "0x1111111111111111111111111111111111111111",
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
            "zeroForOne": true,
            "amountInRaw": 1000000,
            "hookData": "0xabcdef",
        });
        assert_eq!(
            build_v4_quote_calldata_inner(&request).unwrap(),
            "0x6501a3ce000000000000000000000000000000000000000000000000000000000000000000000000000000000000000011111111111111111111111111111111111111110000000000000000000000000000000000000000000000000000000000000bb8000000000000000000000000000000000000000000000000000000000000003c0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000f424000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000000003abcdef0000000000000000000000000000000000000000000000000000000000"
        );
    }

    #[test]
    fn relay_parsing_accepts_name_url_and_bare_url() {
        let relays = configured_relay_urls(Some("alpha=https://a.example, https://b.example"));
        assert_eq!(relays, vec![
            ("alpha".to_string(), "https://a.example".to_string()),
            ("private_2".to_string(), "https://b.example".to_string()),
        ]);
    }

    #[test]
    fn rpc_error_text_is_redacted() {
        let text = "eth_sendRawTransaction request failed at https://user:pass@relay.example:8545/path";
        let redacted = redact_rpc_text(text);
        assert!(!redacted.contains("user:pass"));
        assert!(redacted.contains("https://[REDACTED]"));
    }

    #[test]
    fn negative_tick_spacing_encodes_twos_complement() {
        let request = json!({
            "currency0": "0x0000000000000000000000000000000000000000",
            "currency1": "0x1111111111111111111111111111111111111111",
            "fee": 500,
            "tickSpacing": -60,
            "hooks": "0x0000000000000000000000000000000000000000",
            "zeroForOne": false,
            "amountInRaw": "1000000000000000000",
            "hookData": "",
        });
        let calldata = build_v4_quote_calldata_inner(&request).unwrap();
        assert!(calldata.contains("ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc4"));
    }
}
