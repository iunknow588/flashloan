use std::env;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use hex::{decode as hex_decode, encode as hex_encode};
use k256::ecdsa::signature::hazmat::PrehashSigner;
use k256::ecdsa::{SigningKey, VerifyingKey};
use k256::SecretKey as K256SecretKey;
use num_bigint::{BigInt, BigUint, Sign};
use num_traits::Zero;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use serde_json::{json, Value};
use tiny_keccak::{Hasher, Keccak};

use crate::config::parse_u64_hexish;
use crate::cross_tick_init::{decide as decide_cross_tick, to_json as cross_tick_to_json};
use crate::json_result_to_py;

/// The deployed quoter takes the canonical Uniswap V4 nested struct.
///
/// The flat form has a different selector and reverts with empty data on
/// the deployed contract, so the nested struct is the only form that reaches
/// the quoter. The Python production adapter uses the same nested ABI.
const V4_QUOTE_SIGNATURE: &str =
    "quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))";
const V4_PREVIEW_AMOUNT_SIGNATURE: &str =
    "previewArbitrageBestAmount(address,(address,address,uint24,int24,address)[3][],bool[3][],uint256,uint256,bool)";
const V4_PREVIEW_POOL_SIGNATURE: &str =
    "previewBestPool(address,(address,address,uint24,int24,address)[],bool[],(address,address,uint24,int24,address)[],bool[],(address,address,uint24,int24,address)[],bool[])";
const V4_PREVIEW_POOL_DETAILED_SIGNATURE: &str =
    "previewBestPoolDetailed(address,(address,address,uint24,int24,address)[],bool[],(address,address,uint24,int24,address)[],bool[],(address,address,uint24,int24,address)[],bool[])";
const V4_RUN_ARBITRAGE_SIGNATURE: &str =
    "runArbitrage(address,(address,address,uint24,int24,address)[3],bool[3],uint256,uint256)";
const V4_RUN_BEST_SIGNATURE: &str =
    "runArbitrageBestPoolsWithSelectedAmount(address,(address,address,uint24,int24,address)[],bool[],(address,address,uint24,int24,address)[],bool[],(address,address,uint24,int24,address)[],bool[],uint256,uint256)";
const V4_FLASH_BATCH_SIGNATURE: &str =
    "requestV4FlashLoanBatch((address,uint256,address[],bytes[],address,uint256,uint256)[])";
const V4_POOL_MANAGER_SWAP_SIGNATURE: &str =
    "swap((address,address,uint24,int24,address),(bool,int256,uint160),bytes)";
const MAX_U256: &str =
    "115792089237316195423570985008687907853269984665640564039457584007913129639935";
const DEFAULT_QUOTE_GAS: u64 = 30_000_000;
const OFFICIAL_V4_PREVIEW_GAS: u64 = 50_000_000;
const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

fn dex_rpc_min_interval_seconds() -> f64 {
    env::var("DEX_RPC_MIN_INTERVAL_SECONDS")
        .ok()
        .and_then(|raw| raw.trim().parse::<f64>().ok())
        .filter(|value| *value > 0.0)
        .unwrap_or(0.0)
}

fn throttle_rpc_call(rpc_url: &str) {
    use std::collections::HashMap;
    use std::sync::Mutex;

    static LAST_CALL: Mutex<Option<HashMap<String, Instant>>> = Mutex::new(None);
    let interval = dex_rpc_min_interval_seconds();
    if interval <= 0.0 || rpc_url.trim().is_empty() {
        return;
    }
    let Ok(mut guard) = LAST_CALL.lock() else {
        return;
    };
    let calls = guard.get_or_insert_with(HashMap::new);
    let now = Instant::now();
    let wait = calls
        .get(rpc_url)
        .and_then(|last| now.checked_duration_since(*last))
        .map(|elapsed| (interval - elapsed.as_secs_f64()).max(0.0))
        .unwrap_or(0.0);
    if wait > 0.0 {
        std::thread::sleep(Duration::from_secs_f64(wait));
    }
    calls.insert(rpc_url.to_string(), Instant::now());
}

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

fn parse_uint_text(text: &str) -> Result<BigUint, String> {
    let trimmed = text.trim();
    if let Some(hex) = trimmed.strip_prefix("0x") {
        return BigUint::parse_bytes(hex.as_bytes(), 16)
            .ok_or_else(|| format!("invalid big integer {text}"));
    }
    BigUint::parse_bytes(trimmed.as_bytes(), 10)
        .ok_or_else(|| format!("invalid big integer {text}"))
}

pub(crate) fn parse_big_uint(value: &Value) -> Result<BigUint, String> {
    if let Some(text) = value.as_str() {
        return parse_uint_text(text);
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
    rlp_encode_bytes(&uint_to_bytes(value))
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
    let modulus = (BigUint::parse_bytes(MAX_U256.as_bytes(), 10).expect("MAX_U256 parses"))
        + BigUint::from(1u8);
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
    let bytes =
        hex_decode(raw).map_err(|error| format!("invalid Ethereum address {value}: {error}"))?;
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
    let fee = parse_u64_value(
        request
            .get("fee")
            .ok_or_else(|| "fee is required".to_string())?,
    )?;
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
    let hook_data_raw = request
        .get("hookData")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let hook_data = if hook_data_raw.is_empty() {
        Vec::new()
    } else {
        decode_hex(&hook_data_raw)?
    };

    // The deployed quoter takes one nested struct argument, so the ABI
    // layout has two levels:
    //
    // * the outer argument is a dynamic tuple, so the head carries one
    //   offset that points at the tuple body;
    // * the tuple body holds the static `PoolKey`, the `bool`, the
    //   `uint128` and the offset of the dynamic `bytes` member, which is
    //   relative to the start of the tuple body.
    let mut body = quote_pool_key_from_value(request)?;
    body.extend_from_slice(&word_bool(zero_for_one));
    body.extend_from_slice(&word_uint(&amount_in_raw)?);
    // The `bytes` offset is relative to the start of the tuple body.
    let hook_data_offset = BigUint::from(body.len() + 32);
    body.extend_from_slice(&word_uint(&hook_data_offset)?);
    body.extend_from_slice(&encode_bytes_tail(&hook_data));

    let mut head = function_selector(V4_QUOTE_SIGNATURE);
    // The outer tuple is dynamic, so the head carries the offset of its body.
    head.extend_from_slice(&word_uint(&BigUint::from(32u32))?);
    head.extend_from_slice(&body);
    Ok(as_hex(&head))
}

enum AbiArg {
    Static(Vec<u8>),
    Dynamic(Vec<u8>),
}

fn abi_encode_call(signature: &str, args: Vec<AbiArg>) -> Result<Vec<u8>, String> {
    let mut output = function_selector(signature);
    let mut tail = Vec::new();
    let mut dynamic_slots: Vec<(usize, Vec<u8>)> = Vec::new();
    for arg in args {
        match arg {
            AbiArg::Static(bytes) => output.extend_from_slice(&bytes),
            AbiArg::Dynamic(bytes) => {
                dynamic_slots.push((output.len(), bytes));
                output.extend_from_slice(&[0u8; 32]);
            }
        }
    }
    let head_size_with_selector = output.len();
    for (position, data) in dynamic_slots {
        let offset = BigUint::from(head_size_with_selector - 4 + tail.len());
        let encoded_offset = word_uint(&offset)?;
        output[position..position + 32].copy_from_slice(&encoded_offset);
        tail.extend_from_slice(&data);
    }
    output.extend_from_slice(&tail);
    Ok(output)
}

fn encode_pool_key_value(key: &Value) -> Result<Vec<u8>, String> {
    let currency0 = key
        .get("currency0")
        .and_then(Value::as_str)
        .ok_or_else(|| "pool key currency0 is required".to_string())?;
    let currency1 = key
        .get("currency1")
        .and_then(Value::as_str)
        .ok_or_else(|| "pool key currency1 is required".to_string())?;
    let fee = parse_u64_value(
        key.get("fee")
            .ok_or_else(|| "pool key fee is required".to_string())?,
    )?;
    let tick_spacing = parse_i64_value(
        key.get("tickSpacing")
            .ok_or_else(|| "pool key tickSpacing is required".to_string())?,
    )?;
    let hooks = key
        .get("hooks")
        .and_then(Value::as_str)
        .unwrap_or(ZERO_ADDRESS);
    let mut output = Vec::new();
    output.extend_from_slice(&word_address(currency0)?);
    output.extend_from_slice(&word_address(currency1)?);
    output.extend_from_slice(&word_uint(&BigUint::from(fee))?);
    output.extend_from_slice(&int256_signed(tick_spacing)?);
    output.extend_from_slice(&word_address(hooks)?);
    Ok(output)
}

fn encode_pool_key_triple(keys: &[Value]) -> Result<Vec<u8>, String> {
    if keys.len() != 3 {
        return Err(format!(
            "preview keyset must contain exactly 3 pool keys, got {}",
            keys.len()
        ));
    }
    let mut output = Vec::new();
    for key in keys {
        output.extend_from_slice(&encode_pool_key_value(key)?);
    }
    Ok(output)
}

fn encode_pool_key_array(keys: &[Value]) -> Result<Vec<u8>, String> {
    let mut output = word_uint(&BigUint::from(keys.len()))?;
    for key in keys {
        output.extend_from_slice(&encode_pool_key_value(key)?);
    }
    Ok(output)
}

fn encode_keyset_array(keysets: &[Value]) -> Result<Vec<u8>, String> {
    let mut output = word_uint(&BigUint::from(keysets.len()))?;
    for keyset in keysets {
        let keys: Vec<Value> = keyset
            .as_array()
            .ok_or_else(|| "keysets must be an array of arrays".to_string())?
            .iter()
            .cloned()
            .collect();
        output.extend_from_slice(&encode_pool_key_triple(&keys)?);
    }
    Ok(output)
}

fn encode_bool_array(values: &[Value]) -> Result<Vec<u8>, String> {
    let mut output = word_uint(&BigUint::from(values.len()))?;
    for value in values {
        output.extend_from_slice(&word_bool(
            value
                .as_bool()
                .ok_or_else(|| "direction must be a boolean".to_string())?,
        ));
    }
    Ok(output)
}

fn encode_address_array(values: &[Value]) -> Result<Vec<u8>, String> {
    let mut output = word_uint(&BigUint::from(values.len()))?;
    for value in values {
        output.extend_from_slice(&word_address(
            value
                .as_str()
                .ok_or_else(|| "address array value must be a string".to_string())?,
        )?);
    }
    Ok(output)
}

fn encode_bytes_array(values: &[Value]) -> Result<Vec<u8>, String> {
    let mut output = word_uint(&BigUint::from(values.len()))?;
    let mut tail = Vec::new();
    let head_size = values.len() * 32;
    for value in values {
        let bytes = decode_hex(
            value
                .as_str()
                .ok_or_else(|| "bytes array value must be a hex string".to_string())?,
        )?;
        output.extend_from_slice(&word_uint(&BigUint::from(head_size + tail.len()))?);
        tail.extend_from_slice(&encode_bytes_tail(&bytes));
    }
    output.extend_from_slice(&tail);
    Ok(output)
}

fn word_negative_uint(value: &BigUint) -> Result<Vec<u8>, String> {
    if value.is_zero() {
        return Err("exact-input V4 swap amount must be positive".to_string());
    }
    let modulus = BigUint::from(1u8) << 256usize;
    let int256_max = (BigUint::from(1u8) << 255usize) - BigUint::from(1u8);
    if value > &int256_max {
        return Err(format!(
            "exact-input V4 swap amount exceeds int256: {value}"
        ));
    }
    word_uint(&(&modulus - value))
}

fn build_v4_pool_manager_swap_calldata(
    key: &Value,
    zero_for_one: bool,
    amount_in: &BigUint,
    sqrt_price_limit_x96: &BigUint,
) -> Result<String, String> {
    let uint160_max = (BigUint::from(1u8) << 160usize) - BigUint::from(1u8);
    if sqrt_price_limit_x96.is_zero() || sqrt_price_limit_x96 > &uint160_max {
        return Err(format!(
            "V4 sqrtPriceLimitX96 is outside uint160: {sqrt_price_limit_x96}"
        ));
    }
    let mut params = word_bool(zero_for_one);
    params.extend_from_slice(&word_negative_uint(amount_in)?);
    params.extend_from_slice(&word_uint(sqrt_price_limit_x96)?);
    let calldata = abi_encode_call(
        V4_POOL_MANAGER_SWAP_SIGNATURE,
        vec![
            AbiArg::Static(encode_pool_key_value(key)?),
            AbiArg::Static(params),
            AbiArg::Dynamic(encode_bytes_tail(&[])),
        ],
    )?;
    Ok(as_hex(&calldata))
}

fn route_currency(key: &Value, zero_for_one: bool, input: bool) -> Result<String, String> {
    let field = if zero_for_one == input {
        "currency0"
    } else {
        "currency1"
    };
    key.get(field)
        .and_then(Value::as_str)
        .map(|value| value.to_ascii_lowercase())
        .ok_or_else(|| format!("selected V4 pool key {field} is required"))
}

pub(crate) fn build_v4_flash_executor_request(
    route_payload: &Value,
    route_config: &Value,
    pool_manager: &str,
) -> Result<Value, String> {
    let selected_keys = route_payload
        .get("selectedKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "reviewed route has no selectedKeys".to_string())?;
    let selected_directions = route_payload
        .get("selectedDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "reviewed route has no selectedDirections".to_string())?;
    let hop_limits = route_config
        .get("hopSqrtPriceLimits")
        .or_else(|| route_payload.get("hopSqrtPriceLimits"))
        .and_then(Value::as_array)
        .ok_or_else(|| "reviewed route has no hopSqrtPriceLimits".to_string())?;
    if selected_keys.len() != 3 || selected_directions.len() != 3 || hop_limits.len() != 3 {
        return Err(
            "reviewed executor route must carry exactly three keys, directions, and price limits"
                .to_string(),
        );
    }

    let token = route_config
        .get("token")
        .or_else(|| route_payload.get("token"))
        .and_then(Value::as_str)
        .ok_or_else(|| "reviewed executor route token is required".to_string())?
        .to_ascii_lowercase();
    let selected_amount = parse_big_uint(
        route_payload
            .get("selectedAmountRaw")
            .ok_or_else(|| "reviewed route selectedAmountRaw is required".to_string())?,
    )?;
    let out_tb = parse_big_uint(
        route_payload
            .get("outTb")
            .ok_or_else(|| "reviewed route outTb is required".to_string())?,
    )?;
    let out_ta = parse_big_uint(
        route_payload
            .get("outTa")
            .ok_or_else(|| "reviewed route outTa is required".to_string())?,
    )?;
    let out_tz = parse_big_uint(
        route_payload
            .get("outTz")
            .ok_or_else(|| "reviewed route outTz is required".to_string())?,
    )?;
    let min_profit = parse_big_uint(
        route_config
            .get("minProfitRaw")
            .or_else(|| route_payload.get("minProfitRaw"))
            .ok_or_else(|| "reviewed executor route minProfitRaw is required".to_string())?,
    )?;
    if selected_amount.is_zero() || out_tb.is_zero() || out_ta.is_zero() || out_tz.is_zero() {
        return Err("reviewed executor route amounts must be positive".to_string());
    }

    let directions = selected_directions
        .iter()
        .map(|value| {
            value
                .as_bool()
                .ok_or_else(|| "reviewed route direction must be a boolean".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let first_input = route_currency(&selected_keys[0], directions[0], true)?;
    let first_output = route_currency(&selected_keys[0], directions[0], false)?;
    let second_input = route_currency(&selected_keys[1], directions[1], true)?;
    let second_output = route_currency(&selected_keys[1], directions[1], false)?;
    let third_input = route_currency(&selected_keys[2], directions[2], true)?;
    let third_output = route_currency(&selected_keys[2], directions[2], false)?;
    if first_input != token
        || first_output != second_input
        || second_output != third_input
        || third_output != token
    {
        return Err("reviewed executor route is not a closed three-hop token path".to_string());
    }

    let amounts = [&selected_amount, &out_tb, &out_ta];
    let mut swap_calldatas = Vec::with_capacity(3);
    for index in 0..3 {
        let limit = parse_big_uint(&hop_limits[index])?;
        swap_calldatas.push(build_v4_pool_manager_swap_calldata(
            &selected_keys[index],
            directions[index],
            amounts[index],
            &limit,
        )?);
    }
    let manager = pool_manager.trim().to_ascii_lowercase();
    word_address(&manager)?;
    Ok(json!({
        "token": token,
        "amount": format!("0x{:x}", selected_amount),
        "swapTargets": [manager.clone(), manager.clone(), manager],
        "swapCalldatas": swap_calldatas,
        "finalToken": third_output,
        "minProfit": format!("0x{:x}", min_profit),
        "minAmountOut": format!("0x{:x}", out_tz),
    }))
}

fn encode_v4_flash_batch_request(request: &Value) -> Result<Vec<u8>, String> {
    let token = request
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| "executor batch request token is required".to_string())?;
    let amount = parse_big_uint(
        request
            .get("amount")
            .ok_or_else(|| "executor batch request amount is required".to_string())?,
    )?;
    let swap_targets = request
        .get("swapTargets")
        .and_then(Value::as_array)
        .ok_or_else(|| "executor batch request swapTargets is required".to_string())?;
    let swap_calldatas = request
        .get("swapCalldatas")
        .and_then(Value::as_array)
        .ok_or_else(|| "executor batch request swapCalldatas is required".to_string())?;
    if swap_targets.len() != swap_calldatas.len() {
        return Err("executor batch request swapTargets/swapCalldatas length mismatch".to_string());
    }
    let final_token = request
        .get("finalToken")
        .and_then(Value::as_str)
        .ok_or_else(|| "executor batch request finalToken is required".to_string())?;
    let min_profit = parse_big_uint(
        request
            .get("minProfit")
            .ok_or_else(|| "executor batch request minProfit is required".to_string())?,
    )?;
    // The reviewed minimum output of the route. The contract field was
    // renamed from `gasReserve` to `minAmountOut`; the ABI layout is
    // unchanged, so the encoded word is identical.
    let min_amount_out = parse_big_uint(
        request
            .get("minAmountOut")
            .or_else(|| request.get("gasReserve"))
            .ok_or_else(|| "executor batch request minAmountOut is required".to_string())?,
    )?;

    let targets_tail = encode_address_array(swap_targets)?;
    let calldatas_tail = encode_bytes_array(swap_calldatas)?;
    let tuple_head_size = 7 * 32;
    let mut output = Vec::new();
    output.extend_from_slice(&word_address(token)?);
    output.extend_from_slice(&word_uint(&amount)?);
    output.extend_from_slice(&word_uint(&BigUint::from(tuple_head_size))?);
    output.extend_from_slice(&word_uint(&BigUint::from(
        tuple_head_size + targets_tail.len(),
    ))?);
    output.extend_from_slice(&word_address(final_token)?);
    output.extend_from_slice(&word_uint(&min_profit)?);
    output.extend_from_slice(&word_uint(&min_amount_out)?);
    output.extend_from_slice(&targets_tail);
    output.extend_from_slice(&calldatas_tail);
    Ok(output)
}

fn encode_v4_flash_batch_request_array(requests: &[Value]) -> Result<Vec<u8>, String> {
    if requests.is_empty() || requests.len() > 16 {
        return Err(format!(
            "executor batch request count must be between 1 and 16, got {}",
            requests.len()
        ));
    }
    let mut output = word_uint(&BigUint::from(requests.len()))?;
    let mut tail = Vec::new();
    let head_size = requests.len() * 32;
    for request in requests {
        output.extend_from_slice(&word_uint(&BigUint::from(head_size + tail.len()))?);
        tail.extend_from_slice(&encode_v4_flash_batch_request(request)?);
    }
    output.extend_from_slice(&tail);
    Ok(output)
}

pub(crate) fn build_v4_flash_batch_calldata(requests: &[Value]) -> Result<String, String> {
    let calldata = abi_encode_call(
        V4_FLASH_BATCH_SIGNATURE,
        vec![AbiArg::Dynamic(encode_v4_flash_batch_request_array(
            requests,
        )?)],
    )?;
    Ok(as_hex(&calldata))
}

fn encode_bool_triple(values: &[Value]) -> Result<Vec<u8>, String> {
    if values.len() != 3 {
        return Err(format!(
            "runArbitrage direction set must contain exactly 3 booleans, got {}",
            values.len()
        ));
    }
    let mut output = Vec::new();
    for value in values {
        output.extend_from_slice(&word_bool(
            value
                .as_bool()
                .ok_or_else(|| "direction must be a boolean".to_string())?,
        ));
    }
    Ok(output)
}

fn encode_direction_set_array(valuesets: &[Value]) -> Result<Vec<u8>, String> {
    let mut output = word_uint(&BigUint::from(valuesets.len()))?;
    for values in valuesets {
        let items: Vec<Value> = values
            .as_array()
            .ok_or_else(|| "directionsets must be an array of arrays".to_string())?
            .iter()
            .cloned()
            .collect();
        if items.len() != 3 {
            return Err(format!(
                "preview direction set must contain exactly 3 booleans, got {}",
                items.len()
            ));
        }
        for item in items {
            output.extend_from_slice(&word_bool(
                item.as_bool()
                    .ok_or_else(|| "direction must be a boolean".to_string())?,
            ));
        }
    }
    Ok(output)
}

fn decode_u256_word(result_hex: &str, word_index: usize) -> Result<BigUint, String> {
    let raw = strip_0x(result_hex);
    if raw.len() < (word_index + 1) * 64 {
        return Err(format!(
            "official V4 result is too short at word {word_index}: {result_hex}"
        ));
    }
    BigUint::parse_bytes(&raw[word_index * 64..(word_index + 1) * 64].as_bytes(), 16)
        .ok_or_else(|| format!("official V4 result word {word_index} is not a uint256"))
}

fn decode_i256_word(result_hex: &str, word_index: usize) -> Result<BigInt, String> {
    let raw = strip_0x(result_hex);
    if raw.len() < (word_index + 1) * 64 {
        return Err(format!(
            "official V4 result is too short at word {word_index}: {result_hex}"
        ));
    }
    let word = &raw[word_index * 64..(word_index + 1) * 64];
    let magnitude = BigUint::parse_bytes(word.as_bytes(), 16)
        .ok_or_else(|| format!("official V4 result word {word_index} is not an int256"))?;
    let two_255 = BigUint::from(2u8).pow(255);
    let two_256 = BigUint::from(2u8).pow(256);
    if magnitude < two_255 {
        return Ok(BigInt::from_biguint(Sign::Plus, magnitude));
    }
    if magnitude >= two_256 {
        return Err(format!(
            "official V4 result word {word_index} exceeds int256"
        ));
    }
    Ok(BigInt::from_biguint(Sign::Minus, two_256 - magnitude))
}

fn signed_raw(value: &BigInt) -> String {
    if value.sign() == Sign::Minus {
        format!("-0x{:x}", value.magnitude())
    } else {
        format!("0x{:x}", value)
    }
}

fn build_v4_preview_amount_calldata(
    config: &Value,
    is_best_amount: bool,
) -> Result<String, String> {
    let token = config
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| "preview token is required".to_string())?;
    let keysets = config
        .get("keysets")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview keysets is required".to_string())?
        .to_vec();
    let directionsets = config
        .get("directionsets")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview directionsets is required".to_string())?
        .to_vec();
    let initial_amount_raw = parse_big_uint(
        config
            .get("initialAmountRaw")
            .ok_or_else(|| "preview initialAmountRaw is required".to_string())?,
    )?;
    let min_profit_raw = parse_big_uint(
        config
            .get("minProfitRaw")
            .ok_or_else(|| "preview minProfitRaw is required".to_string())?,
    )?;
    let calldata = abi_encode_call(
        V4_PREVIEW_AMOUNT_SIGNATURE,
        vec![
            AbiArg::Static(word_address(token)?),
            AbiArg::Dynamic(encode_keyset_array(&keysets)?),
            AbiArg::Dynamic(encode_direction_set_array(&directionsets)?),
            AbiArg::Static(word_uint(&initial_amount_raw)?),
            AbiArg::Static(word_uint(&min_profit_raw)?),
            AbiArg::Static(word_bool(is_best_amount)),
        ],
    )?;
    Ok(as_hex(&calldata))
}

fn build_v4_preview_pool_calldata(config: &Value) -> Result<String, String> {
    let token = config
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| "preview pool token is required".to_string())?;
    let entry_keys = config
        .get("entryKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool entryKeys is required".to_string())?;
    let entry_directions = config
        .get("entryDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool entryDirections is required".to_string())?;
    let bridge_keys = config
        .get("bridgeKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool bridgeKeys is required".to_string())?;
    let bridge_directions = config
        .get("bridgeDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool bridgeDirections is required".to_string())?;
    let exit_keys = config
        .get("exitKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool exitKeys is required".to_string())?;
    let exit_directions = config
        .get("exitDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool exitDirections is required".to_string())?;
    let calldata = abi_encode_call(
        V4_PREVIEW_POOL_SIGNATURE,
        vec![
            AbiArg::Static(word_address(token)?),
            AbiArg::Dynamic(encode_pool_key_array(entry_keys)?),
            AbiArg::Dynamic(encode_bool_array(entry_directions)?),
            AbiArg::Dynamic(encode_pool_key_array(bridge_keys)?),
            AbiArg::Dynamic(encode_bool_array(bridge_directions)?),
            AbiArg::Dynamic(encode_pool_key_array(exit_keys)?),
            AbiArg::Dynamic(encode_bool_array(exit_directions)?),
        ],
    )?;
    Ok(as_hex(&calldata))
}

fn build_v4_preview_pool_detailed_calldata(config: &Value) -> Result<String, String> {
    let token = config
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| "preview pool detailed token is required".to_string())?;
    let entry_keys = config
        .get("entryKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool detailed entryKeys is required".to_string())?;
    let entry_directions = config
        .get("entryDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool detailed entryDirections is required".to_string())?;
    let bridge_keys = config
        .get("bridgeKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool detailed bridgeKeys is required".to_string())?;
    let bridge_directions = config
        .get("bridgeDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool detailed bridgeDirections is required".to_string())?;
    let exit_keys = config
        .get("exitKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool detailed exitKeys is required".to_string())?;
    let exit_directions = config
        .get("exitDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool detailed exitDirections is required".to_string())?;
    let calldata = abi_encode_call(
        V4_PREVIEW_POOL_DETAILED_SIGNATURE,
        vec![
            AbiArg::Static(word_address(token)?),
            AbiArg::Dynamic(encode_pool_key_array(entry_keys)?),
            AbiArg::Dynamic(encode_bool_array(entry_directions)?),
            AbiArg::Dynamic(encode_pool_key_array(bridge_keys)?),
            AbiArg::Dynamic(encode_bool_array(bridge_directions)?),
            AbiArg::Dynamic(encode_pool_key_array(exit_keys)?),
            AbiArg::Dynamic(encode_bool_array(exit_directions)?),
        ],
    )?;
    Ok(as_hex(&calldata))
}

fn build_v4_run_arbitrage_calldata(config: &Value) -> Result<String, String> {
    let token = config
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| "runArbitrage token is required".to_string())?;
    let keys = config
        .get("keys")
        .and_then(Value::as_array)
        .ok_or_else(|| "runArbitrage keys is required".to_string())?
        .to_vec();
    let directions = config
        .get("directions")
        .and_then(Value::as_array)
        .ok_or_else(|| "runArbitrage directions is required".to_string())?
        .to_vec();
    let selected_amount_raw = parse_big_uint(
        config
            .get("selectedAmountRaw")
            .ok_or_else(|| "runArbitrage selectedAmountRaw is required".to_string())?,
    )?;
    let min_profit_raw = parse_big_uint(
        config
            .get("minProfitRaw")
            .ok_or_else(|| "runArbitrage minProfitRaw is required".to_string())?,
    )?;
    let mut calldata = function_selector(V4_RUN_ARBITRAGE_SIGNATURE);
    calldata.extend_from_slice(&word_address(token)?);
    calldata.extend_from_slice(&encode_pool_key_triple(&keys)?);
    calldata.extend_from_slice(&encode_bool_triple(&directions)?);
    calldata.extend_from_slice(&word_uint(&selected_amount_raw)?);
    calldata.extend_from_slice(&word_uint(&min_profit_raw)?);
    Ok(as_hex(&calldata))
}

fn build_v4_run_best_calldata(config: &Value) -> Result<String, String> {
    let token = config
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| "run best token is required".to_string())?;
    let entry_keys = config
        .get("entryKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "run best entryKeys is required".to_string())?;
    let entry_directions = config
        .get("entryDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "run best entryDirections is required".to_string())?;
    let bridge_keys = config
        .get("bridgeKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "run best bridgeKeys is required".to_string())?;
    let bridge_directions = config
        .get("bridgeDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "run best bridgeDirections is required".to_string())?;
    let exit_keys = config
        .get("exitKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "run best exitKeys is required".to_string())?;
    let exit_directions = config
        .get("exitDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "run best exitDirections is required".to_string())?;
    let selected_amount_raw = parse_big_uint(
        config
            .get("selectedAmountRaw")
            .ok_or_else(|| "run best selectedAmountRaw is required".to_string())?,
    )?;
    let min_profit_raw = parse_big_uint(
        config
            .get("minProfitRaw")
            .ok_or_else(|| "run best minProfitRaw is required".to_string())?,
    )?;
    let calldata = abi_encode_call(
        V4_RUN_BEST_SIGNATURE,
        vec![
            AbiArg::Static(word_address(token)?),
            AbiArg::Dynamic(encode_pool_key_array(entry_keys)?),
            AbiArg::Dynamic(encode_bool_array(entry_directions)?),
            AbiArg::Dynamic(encode_pool_key_array(bridge_keys)?),
            AbiArg::Dynamic(encode_bool_array(bridge_directions)?),
            AbiArg::Dynamic(encode_pool_key_array(exit_keys)?),
            AbiArg::Dynamic(encode_bool_array(exit_directions)?),
            AbiArg::Static(word_uint(&selected_amount_raw)?),
            AbiArg::Static(word_uint(&min_profit_raw)?),
        ],
    )?;
    Ok(as_hex(&calldata))
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
    Ok(rlp_encode_bytes(&hex_decode(raw).map_err(|error| {
        format!("invalid recipient hex {address}: {error}")
    })?))
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
        transaction
            .get("nonce")
            .ok_or_else(|| "nonce is required".to_string())?,
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
        transaction
            .get("gas")
            .ok_or_else(|| "gas is required".to_string())?,
    )?));
    items.push(encode_address_field(
        transaction.get("to").unwrap_or(&Value::Null),
    )?);
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("value").unwrap_or(&json!(0)),
    )?));
    items.push(encode_data_field(
        transaction.get("data").unwrap_or(&Value::Null),
    )?);
    items.push(access_list_field(
        transaction.get("accessList").unwrap_or(&Value::Null),
    ));
    Ok(items)
}

fn unsigned_155_items(transaction: &Value, chain_id: &BigUint) -> Result<Vec<Vec<u8>>, String> {
    let mut items = Vec::new();
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction
            .get("nonce")
            .ok_or_else(|| "nonce is required".to_string())?,
    )?));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction
            .get("gasPrice")
            .ok_or_else(|| "gasPrice is required".to_string())?,
    )?));
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction
            .get("gas")
            .ok_or_else(|| "gas is required".to_string())?,
    )?));
    items.push(encode_address_field(
        transaction.get("to").unwrap_or(&Value::Null),
    )?);
    items.push(rlp_encode_integer(&parse_big_uint(
        transaction.get("value").unwrap_or(&json!(0)),
    )?));
    items.push(encode_data_field(
        transaction.get("data").unwrap_or(&Value::Null),
    )?);
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
    let key_bytes =
        hex_decode(key_hex).map_err(|error| format!("invalid private key hex: {error}"))?;
    if key_bytes.len() != 32 {
        return Err(format!(
            "private key must be 32 bytes, got {}",
            key_bytes.len()
        ));
    }
    let signing_key = K256SecretKey::from_slice(&key_bytes)
        .map_err(|error| format!("invalid secp256k1 private key: {error}"))?;
    let signing_key = SigningKey::from(signing_key);
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
    let key_bytes =
        hex_decode(key_hex).map_err(|error| format!("invalid private key hex: {error}"))?;
    if key_bytes.len() != 32 {
        return Err(format!(
            "private key must be 32 bytes, got {}",
            key_bytes.len()
        ));
    }
    let signing_key = K256SecretKey::from_slice(&key_bytes)
        .map_err(|error| format!("invalid secp256k1 private key: {error}"))?;
    let signing_key = SigningKey::from(signing_key);
    Ok(address_from_verifying_key(signing_key.verifying_key()))
}

/// The address that signs with the given private key.
///
/// The helper above answers the same question, but its name says that it
/// resolves a key.  The caller that wants the **account** says so here,
/// so the two names cannot be confused at the call site.
pub(crate) fn signer_address_from_private_key(private_key: &str) -> Result<String, String> {
    resolve_private_key_hex_inner(private_key)
}

fn redact_rpc_text(value: &str) -> String {
    let mut output = String::new();
    let mut rest = value;
    while let Some(scheme_end) = rest.find("://") {
        let after_scheme = &rest[scheme_end + 3..];
        let authority_end = after_scheme
            .find(|character: char| {
                character == '/'
                    || character == '?'
                    || character == '#'
                    || character.is_whitespace()
            })
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
                    if name.is_empty() {
                        format!("private_{}", index + 1)
                    } else {
                        name.to_string()
                    },
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
    throttle_rpc_call(rpc_url);
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
        let revert_data = error
            .get("data")
            .and_then(Value::as_str)
            .map(str::to_string);
        return Err((message, revert_data));
    }
    payload
        .get("result")
        .cloned()
        .ok_or_else(|| ("RPC response has no result".to_string(), None))
}

fn rpc_error_evidence(
    message: &str,
    revert_data: Option<&str>,
    rpc_method: &str,
    broadcast_channel: &str,
) -> Value {
    let raw_error = redact_rpc_text(message);
    let data = revert_data.unwrap_or_default().to_string();
    let selector = if data.len() >= 10 && data.starts_with("0x") {
        data[..10].to_string()
    } else {
        String::new()
    };
    json!({
        "error": raw_error,
        "rawError": message,
        "revertData": data,
        "revertSelector": selector,
        "rpcMethod": rpc_method,
        "broadcastChannel": broadcast_channel,
        "submitted": false,
        "txHash": Value::Null,
    })
}

/// The maximum number of JSON-RPC requests in one batch.
///
/// The public endpoint allows 15 requests per second. One batch of this
/// size answers in well under a second, and the batches are separated by a
/// pause, so a refresh never bursts past the node budget.
const V4_POOL_STATE_BATCH_SIZE: usize = 50;

/// The maximum request rate against the node, in requests per second.
const V4_POOL_STATE_REQUESTS_PER_SECOND: f64 = 8.0;

fn v4_pool_state_slots(pool_id: &str) -> Result<([u8; 32], [u8; 32]), String> {
    let normalized = pool_id.trim().to_lowercase();
    if !normalized.starts_with("0x") || normalized.len() != 66 {
        return Err(format!("invalid V4 pool id: {pool_id}"));
    }
    let raw = decode_hex(&normalized)?;
    if raw.len() > 32 {
        return Err(format!("invalid V4 pool id length: {pool_id}"));
    }
    let mut pool_bytes = [0u8; 32];
    pool_bytes[32 - raw.len()..].copy_from_slice(&raw);
    let mut slot_seed = Vec::with_capacity(64);
    slot_seed.extend_from_slice(&pool_bytes);
    slot_seed.extend_from_slice(&[0u8; 31]);
    slot_seed.push(6);
    let state_slot = keccak256(&slot_seed);
    let mut liquidity_slot = state_slot;
    // `liquidity` is the fourth word of `Pool.State`, i.e. `state_slot + 3`.
    let mut carry = 3u16;
    for byte in liquidity_slot.iter_mut().rev() {
        let value = u16::from(*byte) + carry;
        *byte = value as u8;
        carry = value >> 8;
        if carry == 0 {
            break;
        }
    }
    Ok((state_slot, liquidity_slot))
}

async fn json_rpc_batch(
    client: &reqwest::Client,
    rpc_url: &str,
    requests: Vec<Value>,
    timeout_seconds: u64,
) -> Result<Vec<(u64, Value, Option<String>)>, String> {
    throttle_rpc_call(rpc_url);
    let response = client
        .post(rpc_url)
        .json(&requests)
        .timeout(Duration::from_secs(timeout_seconds))
        .send()
        .await
        .map_err(|error| format!("V4 pool state batch RPC request failed: {error}"))?;
    let payload: Value = response
        .json()
        .await
        .map_err(|error| format!("V4 pool state batch RPC response was not JSON: {error}"))?;
    let items = payload
        .as_array()
        .ok_or_else(|| "V4 pool state batch RPC response is not an array".to_string())?;
    let mut rows = Vec::new();
    for item in items {
        let Some(id) = item.get("id").and_then(Value::as_u64) else {
            continue;
        };
        if let Some(error) = item.get("error") {
            let message = error
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or(&error.to_string())
                .to_string();
            let revert_data = error
                .get("data")
                .and_then(Value::as_str)
                .map(str::to_string);
            rows.push((id, Value::Null, Some(message)));
            if revert_data.is_some() {
                continue;
            }
        } else {
            rows.push((id, item.get("result").cloned().unwrap_or(Value::Null), None));
        }
    }
    Ok(rows)
}

/// Parse a packed V4 ``Slot0`` storage word using the same layout as the
/// Python readers: ``sqrtPriceX96`` occupies the low 160 bits, followed by
/// ``tick``, ``protocolFee`` and ``lpFee``.
fn parse_v4_slot0_words(raw: &str) -> (BigUint, i64, u64, u64) {
    let bytes = decode_hex(raw).unwrap_or_default();
    let mut word = [0u8; 32];
    let copy_len = bytes.len().min(32);
    word[..copy_len].copy_from_slice(&bytes[..copy_len]);
    let sqrt = BigUint::from_bytes_be(&word[12..32]);
    let tick_bits = (u32::from(word[9]) << 16) | (u32::from(word[10]) << 8) | u32::from(word[11]);
    let tick = if tick_bits & (1 << 23) != 0 {
        i64::from(tick_bits as i32 - (1 << 24))
    } else {
        i64::from(tick_bits)
    };
    let protocol_fee = (u64::from(word[6]) << 16) | (u64::from(word[7]) << 8) | u64::from(word[8]);
    let lp_fee = (u64::from(word[3]) << 16) | (u64::from(word[4]) << 8) | u64::from(word[5]);
    (sqrt, tick, protocol_fee, lp_fee)
}

fn parse_v4_liquidity_word(raw: &str) -> u128 {
    let bytes = decode_hex(raw).unwrap_or_default();
    let start = bytes.len().saturating_sub(16);
    bytes[start..]
        .iter()
        .fold(0u128, |acc, byte| (acc << 8) | u128::from(*byte))
}

pub(crate) fn read_v4_pool_states_impl(
    rpc_url: &str,
    pool_manager: &str,
    pool_ids: &[String],
    timeout_seconds: u64,
) -> Result<Value, String> {
    if rpc_url.trim().is_empty() {
        return Err("V4 pool state RPC URL is empty".to_string());
    }
    let manager = pool_manager.trim().to_lowercase();
    let mut requests = Vec::new();
    let mut id_map: Vec<(u64, String, String, [u8; 32])> = Vec::new();
    let mut next_id = 1u64;
    for pool_id in pool_ids {
        let normalized = pool_id.trim().to_lowercase();
        if !normalized.starts_with("0x") || normalized.len() != 66 {
            continue;
        }
        let (state_slot, liquidity_slot) = v4_pool_state_slots(&normalized)?;
        let selector = function_selector("extsload(bytes32)");
        for (slot, kind) in [
            (state_slot, "slot0".to_string()),
            (liquidity_slot, "liquidity".to_string()),
        ] {
            let mut data = Vec::with_capacity(4 + 32);
            data.extend_from_slice(&selector);
            data.extend_from_slice(&slot);
            requests.push(json!({
                "jsonrpc": "2.0",
                "id": next_id,
                "method": "eth_call",
                "params": [{
                    "to": manager,
                    "data": as_hex(&data),
                }, "latest"],
            }));
            id_map.push((next_id, normalized.clone(), kind, slot));
            next_id += 1;
        }
    }
    if requests.is_empty() {
        return Ok(json!({"ok": true, "states": {}, "errors": []}));
    }
    let timeout = timeout_seconds.max(5).min(120);
    // The requests travel in bounded batches with a pause between them, so
    // a full refresh never bursts past the request budget of the node. The
    // free trial of the endpoint allows 15 requests per second.
    let mut rows = Vec::with_capacity(requests.len());
    for chunk in requests.chunks(V4_POOL_STATE_BATCH_SIZE) {
        let chunk_rows = execute_rpc(timeout, |runtime, client| {
            runtime.block_on(json_rpc_batch(client, rpc_url, chunk.to_vec(), timeout))
        })?;
        rows.extend(chunk_rows);
        std::thread::sleep(std::time::Duration::from_millis(
            ((1000.0 / V4_POOL_STATE_REQUESTS_PER_SECOND).max(1.0) as u64) * chunk.len() as u64 / 2,
        ));
    }
    let mut states = serde_json::Map::new();
    let mut errors = Vec::new();
    for (id, pool_id, kind, slot) in id_map {
        let found = rows.iter().find(|(row_id, _, _)| *row_id == id);
        let Some((_, result, error_message)) = found else {
            errors.push(json!({
                "poolId": pool_id,
                "kind": kind,
                "error": "v4_pool_state_batch_response_missing",
            }));
            continue;
        };
        let entry = states
            .entry(pool_id.clone())
            .or_insert_with(|| json!({"poolId": pool_id.clone()}));
        let object = entry
            .as_object_mut()
            .ok_or_else(|| "V4 pool state JSON state entry is not an object".to_string())?;
        if let Some(message) = error_message {
            object.insert("error".to_string(), json!(message));
            errors.push(json!({"poolId": pool_id, "kind": kind, "error": message}));
            continue;
        }
        let raw = result
            .as_str()
            .ok_or_else(|| format!("V4 pool state {kind} result is not a hex string"))?;
        if kind == "slot0" {
            let (sqrt_price, tick, protocol_fee, lp_fee) = parse_v4_slot0_words(raw);
            object.insert("sqrtPriceX96".to_string(), json!(sqrt_price.to_string()));
            object.insert("tick".to_string(), json!(tick));
            object.insert("protocolFee".to_string(), json!(protocol_fee));
            object.insert("lpFee".to_string(), json!(lp_fee));
            object.insert("stateSlot".to_string(), json!(hex_encode(slot)));
        } else {
            let liquidity = parse_v4_liquidity_word(raw);
            if liquidity == 0 {
                object.insert(
                    "error".to_string(),
                    json!("robinhood mainnet pool manager live liquidity is zero"),
                );
                object.insert("liquidity".to_string(), json!("0"));
                object.insert("liquidityIndex".to_string(), json!(0));
                object.insert("hasLiquidity".to_string(), json!(false));
            } else {
                object.insert("liquidity".to_string(), json!(liquidity.to_string()));
                object.insert("liquidityIndex".to_string(), json!(liquidity.to_string()));
                object.insert("hasLiquidity".to_string(), json!(true));
            }
        }
        object.insert("poolManager".to_string(), json!(manager));
        object.insert(
            "source".to_string(),
            json!("pool_manager_extsload_live_state_batch_rust"),
        );
        object.insert("rpcUrl".to_string(), json!(redact_rpc_text(rpc_url)));
    }
    Ok(json!({
        "ok": true,
        "states": states,
        "errors": errors,
        "source": "rust_pool_manager_extsload_batch",
    }))
}

#[pyfunction]
#[pyo3(signature = (rpc_url, pool_manager, pool_ids_json, timeout_seconds=20))]
pub fn read_v4_pool_states(
    rpc_url: String,
    pool_manager: String,
    pool_ids_json: String,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let pool_ids: Vec<String> = serde_json::from_str(&pool_ids_json)
        .map_err(|error| as_py_error(format!("pool_ids must be a JSON array: {error}")))?;
    let result = read_v4_pool_states_impl(&rpc_url, &pool_manager, &pool_ids, timeout_seconds)
        .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

fn execute_rpc<T, F>(timeout_seconds: u64, block: F) -> Result<T, String>
where
    F: FnOnce(&tokio::runtime::Runtime, &reqwest::Client) -> Result<T, String>,
{
    let runtime = rpc_runtime()?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_seconds.max(1)))
        .build()
        .map_err(|error| format!("HTTP client failed: {error}"))?;
    block(runtime, &client)
}

fn rpc_runtime() -> Result<&'static tokio::runtime::Runtime, String> {
    static RPC_RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    if let Some(runtime) = RPC_RUNTIME.get() {
        return Ok(runtime);
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("flashloan-rpc")
        .enable_all()
        .build()
        .map_err(|error| format!("tokio runtime failed: {error}"))?;
    let _ = RPC_RUNTIME.set(runtime);
    RPC_RUNTIME
        .get()
        .ok_or_else(|| "tokio runtime initialization failed".to_string())
}

fn execute_rpc_with_revert<F>(
    timeout_seconds: u64,
    block: F,
) -> Result<Value, (String, Option<String>)>
where
    F: FnOnce(
        &tokio::runtime::Runtime,
        &reqwest::Client,
    ) -> Result<Value, (String, Option<String>)>,
{
    let runtime = rpc_runtime().map_err(|error| (error, None))?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_seconds.max(1)))
        .build()
        .map_err(|error| (format!("HTTP client failed: {error}"), None))?;
    block(runtime, &client)
}

/// Decodes the V4 quoter answer into `(amountOut, gasEstimate)`.
///
/// The deployed quoter answers `quoteExactInputSingle` with two full ABI
/// words: `(uint256 amountOut, uint256 gasEstimate)`. The golden vector used
/// by the unit test was taken from a live `eth_call`: word 0 was `0x47c8f`
/// (294031) and word 1 was `0xa764` (42852).
fn decode_v4_quote_words(raw: &str) -> Result<(BigUint, BigUint), String> {
    if raw.len() < 128 {
        return Err("quote eth_call returned a short result".to_string());
    }
    let amount_out = BigUint::parse_bytes(&raw[..64].as_bytes(), 16)
        .ok_or_else(|| "quote amountOut decode failed".to_string())?;
    let gas_estimate = BigUint::parse_bytes(&raw[64..128].as_bytes(), 16)
        .ok_or_else(|| "quote gasEstimate decode failed".to_string())?;
    Ok((amount_out, gas_estimate))
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
                    return Err(format!(
                        "quote eth_call returned short result: {result_hex}"
                    ));
                }
                let (amount_out, gas_estimate) = decode_v4_quote_words(&raw)?;
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

fn no_profitable_route(message: &str, revert_data: Option<&str>) -> bool {
    if let Some(data) = revert_data {
        let lowered = data.to_lowercase();
        if lowered.contains("0x4f422847") || lowered.contains("0x16301234") {
            return true;
        }
    }
    let lowered = message.to_lowercase();
    lowered.contains("noprofita")
        || lowered.contains("no profitable route")
        || lowered.contains("arbitragenotprofitable")
        || lowered.contains("arbitrage not profitable")
}

fn preview_best_pool_detailed_unavailable(message: &str, revert_data: &str) -> Value {
    json!({
        "ok": false,
        "numericValuesUnavailable": true,
        "error": "previewBestPoolDetailed unavailable",
        "message": redact_rpc_text(message),
        "revertData": revert_data,
        "fallback": "not_profitable_after_costs_without_numeric_values",
    })
}

fn preview_best_pool_unavailable(message: &str, revert_data: &str) -> Value {
    json!({
        "ok": false,
        "noProfitableRoute": false,
        "numericValuesUnavailable": true,
        "error": "previewBestPool unavailable",
        "message": redact_rpc_text(message),
        "revertData": revert_data,
        "fallback": "not_profitable_after_costs_without_numeric_values",
    })
}

fn arbitrage_not_profitable_values(revert_data: Option<&str>) -> Option<(BigUint, BigUint)> {
    let raw = strip_0x(revert_data?);
    let selector = hex_encode(function_selector("ArbitrageNotProfitable(uint256,uint256)"));
    if !raw.starts_with(&selector) || raw.len() < 136 {
        return None;
    }
    let received = BigUint::parse_bytes(raw[8..72].as_bytes(), 16)?;
    let required = BigUint::parse_bytes(raw[72..136].as_bytes(), 16)?;
    Some((received, required))
}

/// The measured signed loss of the last probe row that carries one.
///
/// The amount ladder prices every size it tries. Each probe that reached the
/// contract comes back either with a positive profit or with the
/// `ArbitrageNotProfitable(received, required)` revert, which decodes into an
/// exact signed loss. That loss is a **measurement**, not a missing value, and
/// it is the number the review has to publish so the review can state the
/// concrete loss it observed instead of failing closed on a missing PnL.
///
/// The rows are scanned from the back so the **largest** priced size wins:
/// the ladder halves the size as it searches downwards, so the last row is the
/// smallest size it tried. The loss of the smallest size is the one that
/// bounds the route, because a route that does not pay even at the smallest
/// executable size never pays at all.
fn measured_probe_loss_raw(attempts: &[Value]) -> Value {
    for row in attempts.iter().rev() {
        let Some(profit) = row.get("profitRaw") else {
            continue;
        };
        if profit.is_null() {
            continue;
        }
        if let Some(text) = profit.as_str() {
            if text.trim().is_empty() {
                continue;
            }
        }
        return profit.clone();
    }
    Value::Null
}

/// Publish the deterministic cross-tick initialization contract.
///
/// The single-tick best amount is the **only** admissible seed of the
/// cross-tick search. A zero best amount never starts the cross-tick mode,
/// and the search ceiling is the depth capacity rather than the single-tick
/// physical ceiling.
fn cross_tick_initialization(
    config: &Value,
    single_tick_best_amount_raw: &BigUint,
    single_tick_best_profit_raw: &BigUint,
) -> Value {
    let threshold = config
        .get("crossTickReviewThresholdUsdgRaw")
        .and_then(|value| parse_big_uint(value).ok());
    let value = config
        .get("borrowTokenUsdgValueRaw")
        .and_then(|value| parse_big_uint(value).ok());
    let decimals = config
        .get("borrowTokenDecimals")
        .and_then(Value::as_u64)
        .and_then(|value| u32::try_from(value).ok());
    let capacity = config
        .get("crossTickCapacityRaw")
        .and_then(|value| parse_big_uint(value).ok());
    let decision = decide_cross_tick(
        single_tick_best_amount_raw,
        single_tick_best_profit_raw,
        threshold.as_ref(),
        value.as_ref(),
        decimals,
        capacity.as_ref(),
    );
    let mut payload = cross_tick_to_json(&decision);
    payload["singleTickBestAmountRaw"] = json!(format!("0x{:x}", single_tick_best_amount_raw));
    payload["singleTickBestProfitRaw"] = json!(format!("0x{:x}", single_tick_best_profit_raw));
    if let (Some(threshold), Some(value), Some(decimals)) = (threshold, value, decimals) {
        let scale = BigUint::from(10u8).pow(decimals);
        let profit_usdg_raw = (single_tick_best_profit_raw * &value) / &scale;
        payload["singleTickProfitUsdgRaw"] = json!(format!("0x{:x}", profit_usdg_raw));
        payload["thresholdUsdgRaw"] = json!(format!("0x{:x}", threshold));
        payload["borrowTokenDecimals"] = json!(decimals);
    }
    if let Some(capacity) = capacity {
        payload["crossTickCapacityRaw"] = json!(format!("0x{:x}", capacity));
    }
    // The seed basis is published by the decision itself and must not be
    // overwritten: "single_tick_best_amount" states **where** the
    // cross-tick search starts, while "usdg_quote_asset_not_usd" states
    // **how** the threshold is denominated. Both facts are needed.
    payload["thresholdBasis"] = json!("usdg_quote_asset_not_usd");
    payload
}

/// Continue the generic input/output ladder from the single-tick optimum.
///
/// The single-tick calculation produces the deterministic initial amount;
/// its bucket-cap ceiling is not carried into this second-stage search.
/// The same ladder probes the closed loop's actual swap input/output, now
/// bounded by the Manager-published depth capacity.
fn official_v4_cross_tick_best_amount_inner(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
    single_tick_best_amount_raw: &BigUint,
) -> Result<Value, String> {
    if single_tick_best_amount_raw.is_zero() {
        // A zero single-tick best amount means nothing works inside the
        // current single-tick domain. Do not start the continuation at zero.
        return Ok(json!({
            "method": "previewArbitrageBestAmount",
            "searchMode": "cross_tick",
            "status": "cross_tick_not_started",
            "crossTickReview": json!({
                "started": false,
                "reason": "single_tick_best_amount_zero",
            }),
            "selectedAmountRaw": "0x0",
            "selectedProfitRaw": "0x0",
            "selectedRouteIndex": "0x0",
            "attemptedCount": "0x0",
            "attempts": [],
            // A zero best amount carries a zero profit. That is a
            // **measurement**, not a missing value: zero is greater than
            // every negative result, so the review may publish it and
            // must not classify the route as `data_incomplete`.
            "reportedNetProfitRaw": "0x0",
            "error": "single_tick_best_amount_zero",
        }));
    }
    let Some(capacity_raw) = config
        .get("crossTickCapacityRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?
        .filter(|value| !value.is_zero())
    else {
        return Ok(json!({
            "method": "previewArbitrageBestAmount",
            "searchMode": "cross_tick",
            "status": "cross_tick_capacity_unavailable",
            "ladderBlocked": true,
            "ladderBlockedReason": "cross_tick_capacity_unavailable",
            "repairTarget": "closed_loop_manager",
            "crossTickReview": json!({
                "started": false,
                "reason": "cross_tick_inputs_unavailable",
            }),
            "selectedAmountRaw": "0x0",
            "selectedProfitRaw": "0x0",
            "selectedRouteIndex": "0x0",
            "attemptedCount": "0x0",
            "attempts": [],
            "reportedNetProfitRaw": Value::Null,
            "error": "cross_tick_capacity_unavailable",
        }));
    };
    if single_tick_best_amount_raw > &capacity_raw {
        return Ok(json!({
            "method": "previewArbitrageBestAmount",
            "searchMode": "cross_tick",
            "status": "cross_tick_seed_exceeds_capacity",
            "ladderBlocked": true,
            "ladderBlockedReason": "single_tick_best_amount_exceeds_cross_tick_capacity",
            "crossTickSeedAmountRaw": format!("0x{:x}", single_tick_best_amount_raw),
            "crossTickCapacityRaw": format!("0x{:x}", capacity_raw),
            "crossTickSeedBasis": "single_tick_best_amount",
            "selectedAmountRaw": "0x0",
            "selectedProfitRaw": "0x0",
            "selectedRouteIndex": "0x0",
            "attemptedCount": "0x0",
            "attempts": [],
            "error": "single_tick_best_amount_exceeds_cross_tick_capacity",
        }));
    }

    let mut ladder_config = config.clone();
    ladder_config["initialAmountRaw"] =
        json!(format!("0x{:x}", single_tick_best_amount_raw));
    ladder_config["searchCapacityOverrideRaw"] =
        json!(format!("0x{:x}", capacity_raw));
    ladder_config["maxProbeAmountRaw"] = json!(format!("0x{:x}", capacity_raw));
    ladder_config["searchMode"] = json!("cross_tick");

    let mut result = official_v4_adaptive_best_amount_single_route_inner(
        &ladder_config,
        rpc_url,
        arb_address,
        call_from,
        timeout_seconds,
    )?;
    result["searchMode"] = json!("cross_tick");
    result["crossTickSeedAmountRaw"] =
        json!(format!("0x{:x}", single_tick_best_amount_raw));
    result["crossTickSeedBasis"] = json!("single_tick_best_amount");
    result["crossTickCapacityRaw"] = json!(format!("0x{:x}", capacity_raw));
    result.as_object_mut().map(|payload| {
        payload.remove("singleTickCapacityRaw");
        payload.remove("singleTickCapacityBasis");
    });
    result["crossTickCapacityBasis"] = json!("folded_depth_capacity");
    result["searchCapacityRaw"] = json!(format!("0x{:x}", capacity_raw));
    result["searchCapacityBasis"] = json!("search_capacity_override");
    Ok(result)
}

/// Publish the cross-tick verdict and its typed measurement availability.
fn cross_tick_review_with_search(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
    single_tick_best_amount_raw: &BigUint,
    single_tick_best_profit_raw: &BigUint,
) -> Value {
    let verdict = cross_tick_initialization(
        config,
        single_tick_best_amount_raw,
        single_tick_best_profit_raw,
    );
    if verdict.get("started").and_then(Value::as_bool) != Some(true) {
        return verdict;
    }
    if config.get("searchMode").and_then(Value::as_str) == Some("cross_tick") {
        return verdict;
    }
    match official_v4_cross_tick_best_amount_inner(
        config,
        rpc_url,
        arb_address,
        call_from,
        timeout_seconds,
        single_tick_best_amount_raw,
    ) {
        Ok(search) => {
            let mut payload = verdict;
            payload["search"] = search;
            payload
        }
        Err(error) => {
            let mut payload = verdict;
            payload["searchError"] = json!(error);
            payload
        }
    }
}

fn attach_arbitrage_not_profitable(payload: &mut Value) {
    let revert_data = payload
        .get("revertData")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if let Some((received, required)) = arbitrage_not_profitable_values(Some(revert_data)) {
        let delta = BigInt::from_biguint(Sign::Plus, received.clone())
            - BigInt::from_biguint(Sign::Plus, required.clone());
        payload["arbitrageNotProfitable"] = json!({
            "receivedRaw": format!("0x{:x}", received),
            "requiredRaw": format!("0x{:x}", required),
            "profitDeltaRaw": signed_raw(&delta),
        });
        payload["reportedNetProfitRaw"] = json!(signed_raw(&delta));
    }
}

fn build_official_v4_call(
    calldata: &str,
    arb_address: &str,
    call_from: Option<&str>,
) -> serde_json::Map<String, Value> {
    let mut call: serde_json::Map<String, Value> = serde_json::Map::new();
    call.insert("to".to_string(), json!(arb_address.to_lowercase()));
    call.insert("data".to_string(), json!(calldata));
    call.insert(
        "gas".to_string(),
        json!(format!("0x{OFFICIAL_V4_PREVIEW_GAS:x}")),
    );
    if let Some(from) = call_from {
        call.insert("from".to_string(), json!(from.to_lowercase()));
    }
    call
}

fn call_official_v4_eth_call(
    rpc_url: &str,
    calldata: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<String, (String, Option<String>)> {
    if rpc_url.trim().is_empty() {
        return Err(("official V4 eth_call RPC URL is empty".to_string(), None));
    }
    let call = build_official_v4_call(calldata, arb_address, call_from);
    let mut last_error = String::new();
    for attempt in 0..3u64 {
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
                return result.as_str().map(str::to_string).ok_or_else(|| {
                    (
                        "official V4 eth_call result is not a hex string".to_string(),
                        None,
                    )
                });
            }
            Err((message, Some(revert))) => {
                return Err((message, Some(revert)));
            }
            Err((message, None)) => {
                last_error = message;
                if attempt + 1 < 3 {
                    std::thread::sleep(Duration::from_millis(400 * (attempt + 1)));
                }
            }
        }
    }
    Err((redact_rpc_text(&last_error), None))
}

fn preview_best_pool_inner(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    let calldata = build_v4_preview_pool_calldata(config)?;
    match call_official_v4_eth_call(rpc_url, &calldata, arb_address, call_from, timeout_seconds) {
        Ok(result_hex) => {
            let entry_index = decode_u256_word(&result_hex, 0)?;
            let bridge_index = decode_u256_word(&result_hex, 1)?;
            let exit_index = decode_u256_word(&result_hex, 2)?;
            let net_product = decode_u256_word(&result_hex, 3)?;
            Ok(json!({
                "ok": true,
                "entryIndex": format!("0x{:x}", entry_index),
                "bridgeIndex": format!("0x{:x}", bridge_index),
                "exitIndex": format!("0x{:x}", exit_index),
                "netProduct": format!("0x{:x}", net_product),
            }))
        }
        Err((message, revert)) => {
            if no_profitable_route(&message, revert.as_deref()) {
                return Ok(json!({
                    "ok": false,
                    "noProfitableRoute": true,
                    "error": "NoProfitableRoute",
                    "revertData": revert.unwrap_or_default(),
                }));
            }
            if revert.is_some() {
                return Ok(preview_best_pool_unavailable(
                    &message,
                    revert.as_deref().unwrap_or_default(),
                ));
            }
            Err(format!("official_v4:previewBestPool:eth_call: {message}"))
        }
    }
}

fn preview_best_pool_detailed_inner(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    let calldata = build_v4_preview_pool_detailed_calldata(config)?;
    match call_official_v4_eth_call(rpc_url, &calldata, arb_address, call_from, timeout_seconds) {
        Ok(result_hex) => {
            let entry_index = decode_u256_word(&result_hex, 0)?;
            let bridge_index = decode_u256_word(&result_hex, 1)?;
            let exit_index = decode_u256_word(&result_hex, 2)?;
            let net_product = decode_u256_word(&result_hex, 3)?;
            let net_product_delta = decode_i256_word(&result_hex, 4)?;
            let profitable = decode_u256_word(&result_hex, 5)? != BigUint::zero();
            Ok(json!({
                "ok": true,
                "entryIndex": format!("0x{:x}", entry_index),
                "bridgeIndex": format!("0x{:x}", bridge_index),
                "exitIndex": format!("0x{:x}", exit_index),
                "netProduct": format!("0x{:x}", net_product),
                "netProductDeltaRaw": signed_raw(&net_product_delta),
                "netProductDeltaDec": net_product_delta.to_string(),
                "profitable": profitable,
                "numericValuesAvailable": true,
            }))
        }
        Err((message, revert)) => {
            if no_profitable_route(&message, revert.as_deref()) {
                let mut payload = json!({
                    "ok": false,
                    "noProfitableRoute": true,
                    "numericValuesUnavailable": true,
                    "error": "NoProfitableRoute",
                    "revertData": revert.unwrap_or_default(),
                });
                attach_arbitrage_not_profitable(&mut payload);
                return Ok(payload);
            }
            if revert.is_some() {
                return Ok(preview_best_pool_detailed_unavailable(
                    &message,
                    revert.as_deref().unwrap_or_default(),
                ));
            }
            Err(format!(
                "official_v4:previewBestPoolDetailed:eth_call: {message}"
            ))
        }
    }
}

fn v4_no_profit_payload(
    search_mode: &str,
    pool_preview: Value,
    detailed_preview: Option<Value>,
    run_review: Option<Value>,
    initial_amount_raw: &BigUint,
    min_profit_raw: &BigUint,
    ladder: Option<Value>,
) -> Value {
    let mut payload = json!({
        "method": "previewBestPool+previewArbitrageBestAmount",
        "searchMode": search_mode,
        "status": "not_profitable_after_costs",
        "poolPreview": pool_preview,
        "initialAmountRaw": format!("0x{:x}", initial_amount_raw),
        "minProfitRaw": format!("0x{:x}", min_profit_raw),
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "reportedNetProfitRaw": Value::Null,
    });
    // The amount ladder is the authority on the best trade amount.  The
    // marker below is therefore only correct while the ladder has NOT run.
    //
    // The previous code set it unconditionally, so a payload that carried a
    // real, measured probe ladder (17 concrete sizes, each with the signed
    // profit the contract returned) still announced that the best amount was
    // **missing**.  That self-contradictory payload made the reviewer
    // withdraw the best amount and fail closed, so a route that was simply
    // unprofitable at every size was reported as an unmeasured amount.  The
    // orchestrator then chased a measurement that had never been broken.
    //
    // The marker is cleared below as soon as the ladder contributed at least
    // one usable probe record.
    payload["bestAmountMissing"] = json!(true);
    payload["bestAmountConfirmed"] = json!(false);
    payload["bestAmount"] = Value::Null;
    let mut numeric_values_unavailable = pool_preview
        .get("numericValuesUnavailable")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if let Some(detailed) = detailed_preview.as_ref() {
        payload["poolPreviewDetailed"] = detailed.clone();
        numeric_values_unavailable = numeric_values_unavailable
            || detailed
                .get("numericValuesUnavailable")
                .and_then(Value::as_bool)
                .unwrap_or(false);
        let pool_error = pool_preview
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let detailed_error = detailed
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if !pool_error.is_empty()
            && !detailed_error.is_empty()
            && pool_error != "NoProfitableRoute"
            && pool_error != detailed_error
        {
            payload["error"] = json!(format!("{pool_error}; {detailed_error}"));
        } else if let Some(error) = detailed.get("error") {
            payload["error"] = error.clone();
        }
        if let Some(delta_raw) = detailed.get("netProductDeltaRaw") {
            payload["reportedNetProductDeltaRaw"] = delta_raw.clone();
        }
        if let Some(delta_dec) = detailed.get("netProductDeltaDec") {
            payload["reportedNetProductDeltaDec"] = delta_dec.clone();
        }
    }
    if numeric_values_unavailable {
        payload["status"] = json!("quote_unavailable");
        payload["reviewFailureKind"] = json!("quote_unavailable");
    }
    if let Some(run) = run_review.as_ref() {
        // The run evidence is decoded **here**, in the kernel, so the measured
        // signed loss reaches every consumer. The decoder is idempotent: it
        // only fills the fields when the revert data actually carries them.
        let mut run_payload = run.clone();
        attach_arbitrage_not_profitable(&mut run_payload);
        payload["runArbitrage"] = run_payload.clone();
        if let Some(profit_raw) = run_payload.get("reportedNetProfitRaw") {
            payload["reportedNetProfitRaw"] = profit_raw.clone();
        }
        if let Some(arbitrage) = run_payload.get("arbitrageNotProfitable") {
            payload["arbitrageNotProfitable"] = arbitrage.clone();
        }
    }
    // The amount ladder never ran on this path, so the review has to carry the
    // **probe record of the pool preview itself** as its concrete evidence.
    //
    // The preview is a real measurement of the route at a real size: the
    // detailed preview returns the signed ``netProductDelta`` of that size,
    // and the arbitrage run returns the ``ArbitrageNotProfitable(received,
    // required)`` pair, which is the signed borrow-token loss for that size.
    // Both are concrete numbers, so the review can state the exact loss it
    // measured instead of reporting "no amount" and failing closed.
    //
    // The previous payload carried no ``attempts`` key at all, so the review
    // reached the reviewer with an empty probe ladder. The reviewer then
    // concluded that the ladder never ran and withdrew the best trade amount,
    // which turned a **measured negative result** into a **missing
    // measurement**. The record below closes that gap: the probe ladder
    // carries the measured size and the measured signed profit.
    let probe_amount_raw = format!("0x{:x}", initial_amount_raw);
    let mut probe_profit_raw: Option<String> = None;
    let mut probe_error: Option<String> = None;
    let mut probe_revert_data: Option<String> = None;
    if let Some(detailed) = detailed_preview.as_ref() {
        if let Some(delta) = detailed.get("netProductDeltaRaw") {
            if !delta.is_null() {
                probe_profit_raw = Some(delta.as_str().unwrap_or_default().to_owned());
            }
        }
    }
    if let Some(run) = run_review.as_ref() {
        if probe_profit_raw.is_none() {
            if let Some(delta) = run.get("reportedNetProfitRaw") {
                if !delta.is_null() {
                    probe_profit_raw = Some(delta.as_str().unwrap_or_default().to_owned());
                }
            }
        }
        if let Some(error) = run.get("error") {
            if !error.is_null() {
                probe_error = Some(error.as_str().unwrap_or_default().to_owned());
            }
        }
        if let Some(revert) = run.get("revertData") {
            if !revert.is_null() {
                probe_revert_data = Some(revert.as_str().unwrap_or_default().to_owned());
            }
        }
    }
    if probe_error.is_none() {
        probe_error = pool_preview
            .get("error")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .filter(|text| !text.is_empty());
    }
    // The **amount ladder** is the preferred source of the probe record: it
    // measured the route at every size it tried, so it carries several
    // concrete probe rows with the signed profit of each size. The
    // synthetic single probe below is only the fallback for the case where
    // the ladder could not run at all.
    //
    // The reviewer treats a ladder with fewer than two probe rows as an
    // **incomplete ladder** and withdraws the best trade amount, so a
    // single synthetic row would be indistinguishable from "the ladder
    // never ran". The ladder result therefore wins whenever it carries at
    // least one usable row.
    let mut merged_attempts: Vec<Value> = Vec::new();
    let mut merged_attempted_count: Option<String> = None;
    if let Some(ladder_payload) = ladder.as_ref() {
        if let Some(rows) = ladder_payload.get("attempts").and_then(Value::as_array) {
            merged_attempts = rows.clone();
        }
        if let Some(count) = ladder_payload
            .get("attemptedCount")
            .and_then(Value::as_str)
            .filter(|text| !text.is_empty())
        {
            merged_attempted_count = Some(count.to_owned());
        }
        if let Some(status) = ladder_payload.get("status").and_then(Value::as_str) {
            payload["ladderStatus"] = json!(status);
        }
        if let Some(error) = ladder_payload.get("error").and_then(Value::as_str) {
            if !error.is_empty() && payload.get("error").is_none() {
                payload["error"] = json!(error);
            }
        }
    }
    if merged_attempts.is_empty() {
        merged_attempts = vec![json!({
            "amountRaw": probe_amount_raw,
            "ok": false,
            "profitable": false,
            "profitRaw": probe_profit_raw.clone().map(Value::String).unwrap_or(Value::Null),
            "routeIndex": Value::Null,
            "attemptedCount": "0x1",
            "error": probe_error.clone().map(Value::String).unwrap_or(Value::Null),
            "revertData": probe_revert_data.clone().map(Value::String).unwrap_or(Value::Null),
            "probeBasis": "preview_best_pool_detailed",
        })];
        merged_attempted_count = Some("0x1".to_owned());
    }
    payload["attempts"] = Value::Array(merged_attempts.clone());
    payload["attemptedCount"] = json!(merged_attempted_count.unwrap_or_else(|| {
        format!("0x{:x}", merged_attempts.len())
    }));
    // The ladder's own verdict about the **cross-tick** search travels with
    // the payload.  The kernel decides whether the cross-tick search starts
    // **from the single-tick result**, so that verdict is the only record of
    // the decision: dropping it made every route whose single tick paid at
    // no size look like a route whose cross-tick search never existed, and
    // the reader could not tell "the cross tick found nothing" apart from
    // "the cross tick never ran".  The ceiling travels with it for the
    // same reason: it is the bound the ladder searched inside.
    if let Some(ladder_payload) = ladder.as_ref() {
        if let Some(object) = payload.as_object_mut() {
            for key in [
                "crossTickReview",
                "searchCeilingRaw",
                "searchCeilingBasis",
                "singleTickBestAmountRaw",
                "singleTickBestProfitRaw",
            ] {
                if let Some(value) = ladder_payload.get(key).filter(|value| !value.is_null()) {
                    object.entry(key.to_owned()).or_insert_with(|| value.clone());
                }
            }
        }
    }

    // The ladder is the authority on the best trade amount.  When it ran and
    // produced concrete probe records, the best amount is **measured**, not
    // missing: the route was priced at every size the ladder tried.
    //
    // There are two distinct outcomes and they must stay distinct:
    //
    // * the ladder found a profitable size -> the best amount is that size
    //   and it is confirmed;
    // * the ladder found no profitable size -> the best amount is ``0``,
    //   the measurement is confirmed, and the review states that the route
    //   is unprofitable at every size it tried.
    //
    // Collapsing the second case into "missing" turned a market conclusion
    // into a measurement gap, which sent the orchestrator after a review
    // tool to repair a measurement that was never broken.
    let measured_probes = merged_attempts
        .iter()
        .filter(|row| row.get("amountRaw").and_then(Value::as_str).is_some())
        .count();
    if measured_probes > 0 {
        // The ladder ran. The best amount is no longer missing.
        payload["bestAmountMissing"] = json!(false);
        payload["bestAmountMeasured"] = json!(true);
        // A probe row is only profitable when the probe says so. The ``ok``
        // flag means "the probe reached the contract", which is **also** true
        // for a probe that measured a loss: the contract answered with the
        // `ArbitrageNotProfitable` verdict, so the probe succeeded and the
        // route still did not pay. Treating ``ok`` as profitability turned a
        // measured loss into a "profitable" probe and published its size as
        // the best trade amount.
        let profitable = merged_attempts
            .iter()
            .find(|row| row.get("profitable").and_then(Value::as_bool) == Some(true));
        match profitable {
            Some(row) => {
                let amount_raw = row
                    .get("amountRaw")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned();
                if !amount_raw.is_empty() {
                    payload["bestAmount"] = json!(amount_raw);
                    payload["bestAmountConfirmed"] = json!(true);
                    payload["bestAmountSelectionBasis"] = json!("native_rust_ladder_profitable_probe");
                }
            }
            None => {
                // Every size the ladder tried was rejected. The best amount
                // is a concrete zero: there is no size at which the route
                // pays. The measurement itself is complete.
                payload["bestAmount"] = json!("0x0");
                payload["bestAmountConfirmed"] = json!(true);
                payload["bestAmountSelectionBasis"] =
                    json!("native_rust_ladder_no_profitable_size_measured");
                payload["bestAmountZeroReason"] = json!(format!(
                    "the amount ladder measured {} size(s) and none of them was profitable",
                    measured_probes
                ));
            }
        }
    }
    // The failure classification is added here. The specific failure kind
    // (quote_unavailable, rpc_busy, ...) keeps precedence when one is
    // already present.
    //
    // The kind is no longer ``best_amount_missing``: the amount **was**
    // measured, it simply was not profitable. Reporting it as a missing
    // amount sent the orchestrator after the review tool to repair a
    // measurement that was never broken.
    if payload.get("reviewFailureKind").is_none() {
        payload["reviewFailureKind"] = json!("no_profitable_route");
        if payload.get("bestAmountMeasured").and_then(Value::as_bool) == Some(true) {
            payload["reviewFailureReasons"] = json!([
                "the amount ladder measured every size it tried and none of them was profitable",
            ]);
            payload["reviewFailureDetail"] = json!(
                "the best trade amount is a measured zero: the route pays at no size the ladder tried"
            );
        } else {
            payload["reviewFailureReasons"] = json!([
                "previewBestPool did not resolve a profitable route; the measured probe is recorded",
            ]);
            payload["reviewFailureDetail"] = json!(
                "no concrete best trade amount: the pool preview rejected the route before the amount ladder ran"
            );
        }
    }
    payload
}

fn v4_attempt_no_profit_detailed(
    config: &Value,
    token: &str,
    pool_preview: Value,
    entry_keys: &[Value],
    entry_directions: &[Value],
    bridge_keys: &[Value],
    bridge_directions: &[Value],
    exit_keys: &[Value],
    exit_directions: &[Value],
    initial_amount_raw: &BigUint,
    min_profit_raw: &BigUint,
    search_mode: &str,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    let detailed =
        preview_best_pool_detailed_inner(config, rpc_url, arb_address, call_from, timeout_seconds)?;
    if detailed.get("ok").and_then(Value::as_bool).unwrap_or(false) != true {
        return Ok(v4_no_profit_payload(
            search_mode,
            pool_preview,
            Some(detailed),
            None,
            initial_amount_raw,
            min_profit_raw,
            None,
        ));
    }
    let entry_index =
        big_uint_to_usize(&parse_big_uint(detailed.get("entryIndex").ok_or_else(
            || "previewBestPoolDetailed returned no entryIndex".to_string(),
        )?)?)?;
    let bridge_index =
        big_uint_to_usize(&parse_big_uint(detailed.get("bridgeIndex").ok_or_else(
            || "previewBestPoolDetailed returned no bridgeIndex".to_string(),
        )?)?)?;
    let exit_index =
        big_uint_to_usize(&parse_big_uint(detailed.get("exitIndex").ok_or_else(
            || "previewBestPoolDetailed returned no exitIndex".to_string(),
        )?)?)?;
    let selected_keys = vec![
        entry_keys
            .get(entry_index)
            .ok_or_else(|| {
                format!("previewBestPoolDetailed entryIndex out of range: {entry_index}")
            })?
            .clone(),
        bridge_keys
            .get(bridge_index)
            .ok_or_else(|| {
                format!("previewBestPoolDetailed bridgeIndex out of range: {bridge_index}")
            })?
            .clone(),
        exit_keys
            .get(exit_index)
            .ok_or_else(|| format!("previewBestPoolDetailed exitIndex out of range: {exit_index}"))?
            .clone(),
    ];
    let selected_directions = vec![
        entry_directions
            .get(entry_index)
            .ok_or_else(|| {
                format!("previewBestPoolDetailed entry direction index out of range: {entry_index}")
            })?
            .clone(),
        bridge_directions
            .get(bridge_index)
            .ok_or_else(|| {
                format!(
                    "previewBestPoolDetailed bridge direction index out of range: {bridge_index}"
                )
            })?
            .clone(),
        exit_directions
            .get(exit_index)
            .ok_or_else(|| {
                format!("previewBestPoolDetailed exit direction index out of range: {exit_index}")
            })?
            .clone(),
    ];
    let mut run_config = config.clone();
    run_config["token"] = json!(token);
    run_config["keys"] = json!(selected_keys);
    run_config["directions"] = json!(selected_directions);
    run_config["selectedAmountRaw"] = json!(format!("0x{:x}", initial_amount_raw));
    run_config["minProfitRaw"] = json!(format!("0x{:x}", min_profit_raw));
    let run_review = run_arbitrage_inner(
        &run_config,
        rpc_url,
        arb_address,
        call_from,
        timeout_seconds,
    )
    .ok();
    // The pool preview rejected the route, but the **amount ladder must still
    // run** on that route.
    //
    // The preview selects the route and measures the size the contract could
    // not profit at. It does not search for the size the route *is* profitable
    // at, and a route that is unprofitable at the initial size can be
    // profitable at a smaller one. Skipping the ladder therefore left the
    // review without any probe record at all, which the reviewer read as a
    // **missing measurement** rather than as the measured loss it actually
    // was. Running the ladder here produces the real probe ladder with the
    // signed profit of every size it tried.
    //
    // The ladder is the same mechanism the profitable path uses, so a route
    // that turns out to be profitable at some size is reported as such, and a
    // route that is unprofitable at every size reports the concrete signed
    // loss of the last size it tried.
    let mut ladder_config = config.clone();
    ladder_config["token"] = json!(token);
    ladder_config["keysets"] = json!([selected_keys]);
    ladder_config["directionsets"] = json!([selected_directions]);
    ladder_config["initialAmountRaw"] = json!(format!("0x{:x}", initial_amount_raw));
    ladder_config["minProfitRaw"] = json!(format!("0x{:x}", min_profit_raw));
    ladder_config["searchMode"] = json!(search_mode);
    let ladder = official_v4_adaptive_best_amount_single_route_inner(
        &ladder_config,
        rpc_url,
        arb_address,
        call_from,
        timeout_seconds,
    )
    .ok();
    Ok(v4_no_profit_payload(
        search_mode,
        pool_preview,
        Some(detailed),
        run_review,
        initial_amount_raw,
        min_profit_raw,
        ladder,
    ))
}

fn run_arbitrage_inner(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    let calldata = build_v4_run_arbitrage_calldata(config)?;
    match call_official_v4_eth_call(rpc_url, &calldata, arb_address, call_from, timeout_seconds) {
        Ok(result_hex) => {
            let out_tb = decode_u256_word(&result_hex, 0)?;
            let out_ta = decode_u256_word(&result_hex, 1)?;
            let out_tz = decode_u256_word(&result_hex, 2)?;
            let profit = decode_u256_word(&result_hex, 3)?;
            // The contract accepted the route, so the route is executable.
            // The executable flag and the signed profit are published here,
            // in the kernel, so every consumer reads the same conclusion and
            // no other layer has to derive it.
            Ok(json!({
                "ok": true,
                "executable": true,
                "outTb": format!("0x{:x}", out_tb),
                "outTa": format!("0x{:x}", out_ta),
                "outTz": format!("0x{:x}", out_tz),
                "profit": format!("0x{:x}", profit),
                "reportedNetProfitRaw": format!("0x{:x}", profit),
            }))
        }
        Err((message, revert)) => {
            if no_profitable_route(&message, revert.as_deref()) {
                let revert_data = revert.unwrap_or_default();
                let mut payload = json!({
                    "ok": false,
                    // The contract answered with a measured verdict, so the
                    // call itself succeeded. The route is not profitable at
                    // this size, but the measurement is concrete.
                    "executable": true,
                    "noProfitableRoute": true,
                    "error": "NoProfitableRoute",
                    "rawError": redact_rpc_text(&message),
                    "revertData": revert_data,
                    "revertSelector": revert_selector(&revert_data),
                });
                if let Some((received, required)) = arbitrage_not_profitable_values(
                    payload.get("revertData").and_then(Value::as_str),
                ) {
                    let delta = BigInt::from_biguint(Sign::Plus, received.clone())
                        - BigInt::from_biguint(Sign::Plus, required.clone());
                    payload["arbitrageNotProfitable"] = json!({
                        "receivedRaw": format!("0x{:x}", received),
                        "requiredRaw": format!("0x{:x}", required),
                        "profitDeltaRaw": signed_raw(&delta),
                    });
                    payload["reportedNetProfitRaw"] = json!(signed_raw(&delta));
                }
                return Ok(payload);
            }
            let revert_data = revert.unwrap_or_default();
            Ok(json!({
                "ok": false,
                "noProfitableRoute": false,
                "error": redact_rpc_text(&message),
                "rawError": message,
                "revertData": revert_data,
                "revertSelector": revert_selector(&revert_data),
            }))
        }
    }
}

fn revert_selector(revert_data: &str) -> String {
    if revert_data.len() >= 10 && revert_data.starts_with("0x") {
        revert_data[..10].to_string().to_lowercase()
    } else {
        String::new()
    }
}

fn preview_arbitrage_best_amount_inner(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
    is_best_amount: bool,
) -> Result<Value, String> {
    if rpc_url.trim().is_empty() {
        return Err("official V4 preview RPC URL is empty".to_string());
    }
    let calldata = build_v4_preview_amount_calldata(config, is_best_amount)?;
    let call = build_official_v4_call(&calldata, arb_address, call_from);
    let mut last_error = String::new();
    let mut last_attempt = 0usize;
    for attempt in 0..3u64 {
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
                    .ok_or_else(|| "official V4 preview result is not a hex string".to_string())?;
                let selected_amount_raw = decode_u256_word(result_hex, 0)?;
                let selected_route_index = decode_u256_word(result_hex, 1)?;
                let selected_profit_raw = decode_u256_word(result_hex, 2)?;
                let attempted_count = decode_u256_word(result_hex, 3)?;
                return Ok(json!({
                    "ok": true,
                    "selectedAmountRaw": format!("0x{:x}", selected_amount_raw),
                    "selectedRouteIndex": format!("0x{:x}", selected_route_index),
                    "selectedProfitRaw": format!("0x{:x}", selected_profit_raw),
                    "attemptedCount": format!("0x{:x}", attempted_count),
                }));
            }
            Err((message, revert)) => {
                if no_profitable_route(&message, revert.as_deref()) {
                    let mut payload = json!({
                        "ok": false,
                        "noProfitableRoute": true,
                        "error": "NoProfitableRoute",
                        "revertData": revert.unwrap_or_default(),
                    });
                    attach_arbitrage_not_profitable(&mut payload);
                    return Ok(payload);
                }
                last_error = message.clone();
                last_attempt = attempt as usize;
                if attempt + 1 < 3 {
                    std::thread::sleep(Duration::from_millis(400 * (attempt + 1)));
                }
            }
        }
    }
    Err(format!(
        "official_v4:previewArbitrageBestAmount:eth_call:attempt={}: {}",
        (last_attempt + 1),
        redact_rpc_text(&last_error)
    ))
}

fn bounded_double(amount: &BigUint, max_amount: Option<&BigUint>) -> BigUint {
    let max_u256 = BigUint::parse_bytes(MAX_U256.as_bytes(), 10).expect("MAX_U256 parses");
    let doubled = amount * BigUint::from(2u8);
    if doubled > max_u256 {
        return BigUint::zero();
    }
    if let Some(max_amount) = max_amount {
        if doubled > *max_amount {
            return max_amount.clone();
        }
    }
    doubled
}

fn bounded_half(amount: &BigUint, min_amount: &BigUint) -> BigUint {
    let half = amount / BigUint::from(2u8);
    if half < *min_amount {
        min_amount.clone()
    } else {
        half
    }
}

/// The **search ceiling** of the amount ladder.
///
/// The ceiling is the domain the route may actually be executed at, so it is
/// the published capacity whenever a capacity was published: the folded
/// single-tick capacity is the bound the review enforces with
/// `final_input_exceeds_single_tick_capacity`, and a size above it can never
/// be broadcast.  The generic probe maximum only narrows that domain, never
/// widens it.
fn derive_search_ceiling(
    capacity_limit_raw: &BigUint,
    max_probe_raw: Option<&BigUint>,
) -> BigUint {
    match max_probe_raw {
        Some(max_probe) => {
            if max_probe < capacity_limit_raw {
                max_probe.clone()
            } else {
                capacity_limit_raw.clone()
            }
        }
        None => capacity_limit_raw.clone(),
    }
}

fn big_uint_to_usize(value: &BigUint) -> Result<usize, String> {
    value
        .to_str_radix(10)
        .parse::<usize>()
        .map_err(|_| format!("index exceeds usize: {value}"))
}

/// Resolve the **search capacity** that bounds the amount ladder.
///
/// The kernel owns this number. The Manager publishes the folded single-tick
/// capacity for a closed loop, and the ladder treats that fold as the
/// authoritative bound because it is the only value that was actually proved
/// for the route. The two compatibility fallbacks are deliberately weaker:
/// an explicit override wins when the caller knows a wider search domain,
/// and the generic probe maximum is the last resort for a route that has no
/// published fold yet.
///
/// A missing capacity is **not** an error. It is the blocked case that the
/// ladder and the preview-pool path both report, so the orchestrator knows to
/// call the closed-loop manager instead of the review manager. A JSON `null`
/// therefore means exactly the same thing as an absent key.
fn resolve_search_capacity(
    config: &Value,
) -> Result<(Option<BigUint>, String, &'static str), String> {
    let search_capacity_override_raw = config
        .get("searchCapacityOverrideRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    let folded_capacity_raw = config
        .get("singleTickCapacityRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    let max_probe_raw = config
        .get("maxProbeAmountRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    Ok(
        if let Some(capacity) = search_capacity_override_raw
            .as_ref()
            .filter(|value| !value.is_zero())
        {
            (
                Some(capacity.clone()),
                "search_capacity_override".to_string(),
                "searchCapacityRaw",
            )
        } else {
            match folded_capacity_raw
                .as_ref()
                .filter(|value| !value.is_zero())
            {
                Some(capacity) => (
                    Some(capacity.clone()),
                    "folded_single_tick_capacity".to_string(),
                    "singleTickCapacityRaw",
                ),
                None => (
                    max_probe_raw
                        .as_ref()
                        .filter(|value| !value.is_zero())
                        .cloned(),
                    "max_probe_amount_fallback".to_string(),
                    "singleTickCapacityRaw",
                ),
            }
        },
    )
}

/// Derive the **initial amount** of the ladder from the published capacity.
///
/// The Python runtime never computes a trading number: it only describes the
/// environment (chain, pools, protocol). The kernel owns the amount, so the
/// start is derived here from the same single-tick capacity the ladder uses.
/// When the caller supplied an explicit start it wins, but it is clamped to
/// the capacity so a caller can never push the search past the bound that was
/// proved for the route.
fn derive_initial_amount_raw(
    config: &Value,
    capacity_limit_raw: Option<&BigUint>,
) -> Result<DerivedInitialAmount, String> {
    let requested_initial_amount_raw = config
        .get("initialAmountRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    let ladder_start_fraction = config
        .get("ladderStartCapacityFraction")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.5)
        .clamp(0.01, 1.0);
    let min_probe_raw = config
        .get("minProbeAmountRaw")
        .map(parse_big_uint)
        .transpose()?
        .unwrap_or_else(BigUint::zero);
    let economic_floor_raw = config
        .get("economicFloorRequiredAmountRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    let Some(capacity_limit_raw) = capacity_limit_raw else {
        // Without a published capacity there is no defensible start amount.
        // The caller decides whether that is a blocked route or a hard error;
        // this helper only reports the missing bound.
        return Ok(DerivedInitialAmount {
            initial_amount_raw: BigUint::zero(),
            derived_initial_amount_raw: BigUint::zero(),
            basis: "single_tick_capacity_unavailable".to_string(),
        });
    };
    let fraction_scaled_start = capacity_limit_raw
        * BigUint::from((ladder_start_fraction * 1_000_000_000_000f64).round() as u64)
        / BigUint::from(1_000_000_000_000u64);
    let mut derived_initial_amount_raw = fraction_scaled_start;
    if let Some(required) = economic_floor_raw.as_ref() {
        derived_initial_amount_raw = derived_initial_amount_raw.max(required.clone());
    }
    derived_initial_amount_raw = derived_initial_amount_raw.max(min_probe_raw.clone());
    derived_initial_amount_raw = derived_initial_amount_raw.min(capacity_limit_raw.clone());
    let initial_amount_raw = requested_initial_amount_raw
        .clone()
        .unwrap_or_else(|| derived_initial_amount_raw.clone())
        .min(capacity_limit_raw.clone());
    let basis = if requested_initial_amount_raw.is_some() {
        "caller_provided_start".to_string()
    } else {
        "single_tick_capacity_fraction".to_string()
    };
    Ok(DerivedInitialAmount {
        initial_amount_raw,
        derived_initial_amount_raw,
        basis,
    })
}

/// The outcome of deriving the ladder start from the published capacity.
struct DerivedInitialAmount {
    /// The amount the ladder starts from.
    initial_amount_raw: BigUint,
    /// The amount the kernel derived on its own, before any caller override.
    derived_initial_amount_raw: BigUint,
    /// How the start was determined.
    basis: String,
}

fn official_v4_probe_one(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
    amount_raw: &BigUint,
    is_best_amount: bool,
    attempts: &mut Vec<Value>,
    total_attempted: &mut BigUint,
) -> Result<Value, String> {
    let mut probe_config = config.clone();
    probe_config["initialAmountRaw"] = json!(format!("0x{:x}", amount_raw));
    probe_config["isBestAmount"] = json!(is_best_amount);
    let result = match preview_arbitrage_best_amount_inner(
        &probe_config,
        rpc_url,
        arb_address,
        call_from,
        timeout_seconds,
        is_best_amount,
    ) {
        Ok(result) => result,
        Err(error) => {
            let located = format!("official_v4:probeAmount=0x{:x}:{error}", amount_raw);
            attempts.push(json!({
                "amountRaw": format!("0x{:x}", amount_raw),
                "ok": false,
                "profitable": false,
                "profitRaw": Value::Null,
                "routeIndex": Value::Null,
                "attemptedCount": Value::Null,
                "error": located.clone(),
                "revertData": Value::Null,
            }));
            return Err(located);
        }
    };
    let ok = result.get("ok").and_then(Value::as_bool).unwrap_or(false);
    let profit_raw = result
        .get("selectedProfitRaw")
        .and_then(Value::as_str)
        .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
        .unwrap_or_else(BigUint::zero);
    let route_index = result
        .get("selectedRouteIndex")
        .and_then(Value::as_str)
        .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
        .unwrap_or_else(BigUint::zero);
    let attempted = result
        .get("attemptedCount")
        .and_then(Value::as_str)
        .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
        .unwrap_or_else(BigUint::zero);
    *total_attempted += attempted.clone();
    attempts.push(json!({
        "amountRaw": format!("0x{:x}", amount_raw),
        "ok": ok,
        "profitable": ok && profit_raw > BigUint::zero(),
        "profitRaw": result
            .get("reportedNetProfitRaw")
            .filter(|value| !value.is_null())
            .cloned()
            .or_else(|| result.get("selectedProfitRaw").cloned())
            .unwrap_or(Value::Null),
        "routeIndex": format!("0x{:x}", route_index),
        "attemptedCount": format!("0x{:x}", attempted),
        "error": result.get("error").cloned().unwrap_or(Value::Null),
        "revertData": result.get("revertData").cloned().unwrap_or(Value::Null),
    }));
    Ok(result)
}

pub(crate) fn official_v4_adaptive_best_amount_single_route_inner(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    // D4-e: the token-denominated economic floor short-circuits the ladder.
    //
    // A route whose tightest measured capacity cannot carry the amount needed
    // to cover Gas can never be profitable at any size the pool can absorb.
    // Probing it would burn up to max_steps on-chain calls to reach a
    // conclusion that is already known, so the ladder returns immediately
    // without a single probe.
    //
    // The threshold is expressed in the borrow token's own raw units. No USD
    // quote participates: 86 of 88 production loops borrow a token that is
    // neither ETH nor a USD stablecoin, so a USD valuation could not be
    // computed for the active book at all.
    // The economic floor travels with the task config as a **number**, not
    // as a verdict: the Python runtime does not compare it against the
    // capacity, because the capacity belongs to the kernel. The kernel
    // performs the comparison once it knows the Manager-published capacity.
    let requested_initial_amount_raw = config
        .get("initialAmountRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    let min_profit_raw = parse_big_uint(
        config
            .get("minProfitRaw")
            .ok_or_else(|| "minProfitRaw is required".to_string())?,
    )?;
    let min_probe_raw = config
        .get("minProbeAmountRaw")
        .map(parse_big_uint)
        .transpose()?
        .unwrap_or_else(BigUint::zero);
    // The generic probe maximum bounds the **expansion** of the ladder, but it
    // is never the only bound: the ladder searches inside the domain the
    // route may actually be executed at, and that domain is the capacity the
    // Manager published (see `search_ceiling_raw` below).
    let max_probe_raw = config
        .get("maxProbeAmountRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    // The **kernel** owns the starting amount of the ladder. The Python
    // runtime never computes a trading number: it only describes the
    // environment (chain, pools, protocol) and the kernel derives the
    // single-tick start from the capacity the Manager published.
    //
    // The single tick is the **method** that produces the initial amount,
    // not a restriction of the later search. Its bucket capacity
    // `M_cap` therefore bounds only the single-tick domain; the cross-tick
    // continuation is bounded by the depth capacity instead.
    let economic_floor_raw = config
        .get("economicFloorRequiredAmountRaw")
        .filter(|value| !value.is_null())
        .map(parse_big_uint)
        .transpose()?;
    // The authoritative bound is the **folded single-tick capacity** that
    // the closed-loop Manager published for this route. `maxProbeAmountRaw`
    // is accepted as a compatibility fallback for a caller that has not
    // published the fold yet, but the fold always wins when both are
    // present: the generic probe maximum is not a single-tick bound and may
    // be larger than the tick range allows.
    // A **missing** capacity is not an error: it is the blocked case the
    // ladder reports below. A JSON ``null`` therefore means "no capacity was
    // published", exactly like an absent key, and must not turn into a
    // parse failure that hides the real reason.
    let (capacity_limit_raw, capacity_basis, search_capacity_key) =
        resolve_search_capacity(config)?;
    let Some(capacity_limit_raw) = capacity_limit_raw.as_ref() else {
        // The route has no published capacity, so the ladder is **blocked**
        // before it probes anything. Nothing was measured, and the record
        // must say so: an empty ``attempts`` array with ``attemptedCount``
        // zero is the honest answer here.
        //
        // The reviewer distinguishes this blocked ladder from a ladder
        // that ran and found nothing, through the explicit status below.
        // The status is what tells the orchestrator that the route was
        // blocked by a **missing published capacity** and not by a
        // **missing measurement**, so the right tool is called: the closed
        // loop manager (which publishes the capacity), not the review
        // manager (which cannot repair a missing capacity).
        return Ok(json!({
            "method": "previewArbitrageBestAmount",
            "searchMode": config.get("searchMode").cloned().unwrap_or(Value::Null),
            "requestedInitialAmountRaw": requested_initial_amount_raw
                .as_ref()
                .map(|value| json!(format!("0x{:x}", value)))
                .unwrap_or(Value::Null),
            "initialAmountRaw": "0x0",
            "minProfitRaw": format!("0x{:x}", min_profit_raw),
            "status": "single_tick_capacity_unavailable",
            "ladderBlocked": true,
            "ladderBlockedReason": "single_tick_capacity_unavailable",
            "repairTarget": "closed_loop_manager",
            "selectedAmountRaw": "0x0",
            "selectedProfitRaw": "0x0",
            "selectedRouteIndex": "0x0",
            "attemptedCount": "0x0",
            "attempts": [],
            "error": "single_tick_capacity_unavailable",
        }));
    };
    // The **search ceiling** of the ladder is the domain the route may
    // actually be executed at.
    //
    // The folded single-tick capacity is that domain: the review rejects a
    // final input above it with `final_input_exceeds_single_tick_capacity`,
    // so a size above the capacity can never be broadcast.  A ladder that
    // walks past that ceiling spends its probes on sizes that are known to
    // be rejected, and it picks the **largest** of them whenever the profit
    // grows with the size — which is exactly how a profitable route ended up
    // unexecutable on the live book.
    //
    // The ladder therefore searches *inside* the ceiling, so the size it
    // selects is a size the system can execute.  The cross-tick search
    // publishes its own capacity as the search override, so there the
    // ceiling *is* the cross-tick capacity and the domain is unchanged.
    let search_ceiling_raw = derive_search_ceiling(capacity_limit_raw, max_probe_raw.as_ref());
    if min_probe_raw > *capacity_limit_raw {
        // The published capacity is smaller than the smallest size the
        // ladder is allowed to probe. The route is blocked before it probes
        // anything, so the record carries the concrete capacity that
        // blocked it and nothing else.
        return Ok(json!({
            "method": "previewArbitrageBestAmount",
            "searchMode": config.get("searchMode").cloned().unwrap_or(Value::Null),
            "requestedInitialAmountRaw": requested_initial_amount_raw
                .as_ref()
                .map(|value| json!(format!("0x{:x}", value)))
                .unwrap_or(Value::Null),
            "initialAmountRaw": "0x0",
            "minProfitRaw": format!("0x{:x}", min_profit_raw),
            "singleTickCapacityRaw": format!("0x{:x}", capacity_limit_raw),
            "singleTickCapacityBasis": capacity_basis,
            "status": "single_tick_capacity_below_min_probe",
            "ladderBlocked": true,
            "ladderBlockedReason": "single_tick_capacity_below_min_probe",
            "repairTarget": "closed_loop_manager",
            "selectedAmountRaw": "0x0",
            "selectedProfitRaw": "0x0",
            "selectedRouteIndex": "0x0",
            "attemptedCount": "0x0",
            "attempts": [],
            "error": "single_tick_capacity_below_min_probe",
        }));
    }
    // The economic floor is compared against the capacity **here**, because
    // the capacity is the kernel's own value. A pool that cannot carry the
    // amount needed to cover Gas can never be profitable at any size it can
    // absorb, so the route is refused before a single probe is spent.
    if let Some(required) = economic_floor_raw.as_ref() {
        if !required.is_zero() && required > capacity_limit_raw {
            return Ok(json!({
                "method": "previewArbitrageBestAmount",
                "searchMode": config.get("searchMode").cloned().unwrap_or(Value::Null),
                "requestedInitialAmountRaw": requested_initial_amount_raw
                    .as_ref()
                    .map(|value| json!(format!("0x{:x}", value)))
                    .unwrap_or(Value::Null),
                "initialAmountRaw": "0x0",
                "minProfitRaw": format!("0x{:x}", min_profit_raw),
                "singleTickCapacityRaw": format!("0x{:x}", capacity_limit_raw),
                "singleTickCapacityBasis": capacity_basis,
                "status": "economically_infeasible",
                "ladderBlocked": true,
                "ladderBlockedReason": "capacity_cannot_cover_gas",
                "economicFloorGate": {
                    "verdict": "dust_unprofitable",
                    "blocked": true,
                    "reason": "capacity_cannot_cover_gas",
                    "measured": true,
                    "capacityRaw": format!("0x{:x}", capacity_limit_raw),
                    "requiredRaw": format!("0x{:x}", required),
                    "basis": "token_denominated_economic_floor",
                },
                "selectedAmountRaw": "0x0",
                "selectedProfitRaw": "0x0",
                "selectedRouteIndex": "0x0",
                "attemptedCount": "0x0",
                "attempts": [],
                "error": "dust_unprofitable",
            }));
        }
    }
    // Derive the ladder start inside the kernel. The Manager-published
    // single-tick capacity is the bucket-shortest-board bound `M_cap`.
    // The start is the capacity fraction that is also raised to the
    // economic floor and the minimum probe size, but never exceeds the
    // capacity itself. A caller-provided start remains an explicit
    // override for compatibility, never a Python-computed default.
    let derived_start = derive_initial_amount_raw(config, Some(&capacity_limit_raw))?;
    let initial_amount_raw = derived_start.initial_amount_raw.clone();
    let derived_initial_amount_raw = derived_start.derived_initial_amount_raw.clone();
    let initial_amount_basis = derived_start.basis.clone();
    let max_steps = config
        .get("maxProbeAttempts")
        .and_then(Value::as_u64)
        .unwrap_or(24)
        .clamp(4, 64) as usize;
    let search_mode = config
        .get("searchMode")
        .and_then(Value::as_str)
        .unwrap_or("external_ladder")
        .to_string();

    if search_mode == "inner_single_call" {
        let mut bounded_config = config.clone();
        bounded_config["initialAmountRaw"] = json!(format!("0x{:x}", initial_amount_raw));
        let single = preview_arbitrage_best_amount_inner(
            &bounded_config,
            rpc_url,
            arb_address,
            call_from,
            timeout_seconds,
            false,
        )?;
        let ok = single.get("ok").and_then(Value::as_bool).unwrap_or(false);
        let profit_raw = single
            .get("selectedProfitRaw")
            .and_then(Value::as_str)
            .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
            .unwrap_or_else(BigUint::zero);
        let single_tick_best_amount_raw = single
            .get("selectedAmountRaw")
            .and_then(Value::as_str)
            .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
            .unwrap_or_else(BigUint::zero);
        let attempts = vec![json!({
            "amountRaw": config.get("initialAmountRaw").cloned().unwrap_or(Value::Null),
            "ok": ok,
            "profitable": ok && profit_raw > BigUint::zero(),
            "profitRaw": single
                .get("reportedNetProfitRaw")
                .filter(|value| !value.is_null())
                .cloned()
                .or_else(|| single.get("selectedProfitRaw").cloned())
                .unwrap_or(Value::Null),
            "routeIndex": single.get("selectedRouteIndex").cloned().unwrap_or(Value::Null),
            "attemptedCount": single.get("attemptedCount").cloned().unwrap_or(Value::Null),
            "error": single.get("error").cloned().unwrap_or(Value::Null),
            "revertData": single.get("revertData").cloned().unwrap_or(Value::Null),
        })];
        return Ok(json!({
            "method": "previewArbitrageBestAmount",
            "searchMode": search_mode,
            "requestedInitialAmountRaw": requested_initial_amount_raw
                .as_ref()
                .map(|value| json!(format!("0x{:x}", value)))
                .unwrap_or(Value::Null),
            "initialAmountRaw": format!("0x{:x}", initial_amount_raw),
            "singleTickCapacityRaw": format!("0x{:x}", capacity_limit_raw),
            "singleTickCapacityBasis": capacity_basis,
            search_capacity_key: format!("0x{:x}", capacity_limit_raw),
            "searchCapacityBasis": capacity_basis,
            "searchCeilingRaw": format!("0x{:x}", search_ceiling_raw),
            "searchCeilingBasis": capacity_basis,
            "ladderStartBasis": initial_amount_basis,
            "derivedInitialAmountRaw": format!("0x{:x}", derived_initial_amount_raw),
            "minProfitRaw": format!("0x{:x}", min_profit_raw),
            "status": if ok { "profitable_amount_found" } else { "not_profitable_after_costs" },
            "selectedAmountRaw": single.get("selectedAmountRaw").cloned().unwrap_or(Value::Null),
            "selectedProfitRaw": single.get("selectedProfitRaw").cloned().unwrap_or(Value::Null),
            "singleTickBestAmountRaw": format!("0x{:x}", single_tick_best_amount_raw),
            "singleTickBestProfitRaw": format!("0x{:x}", profit_raw),
            "selectedRouteIndex": single.get("selectedRouteIndex").cloned().unwrap_or(Value::Null),
            "attemptedCount": single.get("attemptedCount").cloned().unwrap_or(Value::Null),
            "attempts": attempts,
            // The measured signed loss of the size that was priced. Without it
            // the ladder reports "no profit" without ever saying **how much**
            // was lost, and the review cannot publish a concrete PnL.
            "reportedNetProfitRaw": measured_probe_loss_raw(&attempts),
            "crossTickReview": cross_tick_review_with_search(
                config,
                rpc_url,
                arb_address,
                call_from,
                timeout_seconds,
                &single_tick_best_amount_raw,
                &profit_raw,
            ),
        }));
    }

    let mut attempts: Vec<Value> = Vec::new();
    let mut total_attempted = BigUint::zero();
    let initial_result = official_v4_probe_one(
        config,
        rpc_url,
        arb_address,
        call_from,
        timeout_seconds,
        &initial_amount_raw,
        true,
        &mut attempts,
        &mut total_attempted,
    );
    let initial_probe = match initial_result {
        Ok(payload) => payload,
        Err(error) => {
            return Ok(json!({
                "method": "previewArbitrageBestAmount",
                "searchMode": search_mode,
                "requestedInitialAmountRaw": requested_initial_amount_raw
                    .as_ref()
                    .map(|value| json!(format!("0x{:x}", value)))
                    .unwrap_or(Value::Null),
                "initialAmountRaw": format!("0x{:x}", initial_amount_raw),
                "singleTickCapacityRaw": format!("0x{:x}", capacity_limit_raw),
            "singleTickCapacityBasis": capacity_basis,
                search_capacity_key: format!("0x{:x}", capacity_limit_raw),
                "searchCapacityBasis": capacity_basis,
                "searchCeilingRaw": format!("0x{:x}", search_ceiling_raw),
                "searchCeilingBasis": capacity_basis,
                "ladderStartBasis": initial_amount_basis,
                "derivedInitialAmountRaw": format!("0x{:x}", derived_initial_amount_raw),
                "minProfitRaw": format!("0x{:x}", min_profit_raw),
                "status": "preflight_failed",
                "selectedAmountRaw": "0x0",
                "selectedProfitRaw": "0x0",
                "selectedRouteIndex": "0x0",
                "attemptedCount": format!("0x{:x}", total_attempted),
                "attempts": attempts,
                "reportedNetProfitRaw": measured_probe_loss_raw(&attempts),
                "error": error,
            }));
        }
    };
    let initial_ok = initial_probe
        .get("ok")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut selected_amount = BigUint::zero();
    let mut selected_profit = BigUint::zero();
    let mut selected_route_index = BigUint::zero();
    let mut base_amount = BigUint::zero();
    let mut lower_amount = BigUint::zero();
    let mut upper_amount = BigUint::zero();
    let mut has_upper_amount = false;

    if initial_ok {
        selected_amount = initial_amount_raw.clone();
        selected_profit = initial_probe
            .get("selectedProfitRaw")
            .and_then(Value::as_str)
            .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
            .unwrap_or_else(BigUint::zero);
        selected_route_index = initial_probe
            .get("selectedRouteIndex")
            .and_then(Value::as_str)
            .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
            .unwrap_or_else(BigUint::zero);
        base_amount = initial_amount_raw.clone();
        lower_amount = bounded_half(&initial_amount_raw, &min_probe_raw);
        let mut candidate = bounded_double(&base_amount, Some(&search_ceiling_raw));
        while attempts.len() < max_steps && candidate > base_amount {
            let payload = official_v4_probe_one(
                config,
                rpc_url,
                arb_address,
                call_from,
                timeout_seconds,
                &candidate,
                true,
                &mut attempts,
                &mut total_attempted,
            )?;
            if !payload.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                upper_amount = candidate.clone();
                has_upper_amount = true;
                break;
            }
            let candidate_profit = payload
                .get("selectedProfitRaw")
                .and_then(Value::as_str)
                .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                .unwrap_or_else(BigUint::zero);
            if candidate_profit > selected_profit {
                selected_amount = candidate.clone();
                selected_profit = candidate_profit;
                selected_route_index = payload
                    .get("selectedRouteIndex")
                    .and_then(Value::as_str)
                    .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                    .unwrap_or_else(BigUint::zero);
                base_amount = candidate.clone();
                lower_amount = bounded_half(&candidate, &min_probe_raw);
            } else {
                upper_amount = candidate.clone();
                has_upper_amount = true;
                break;
            }
            let next = bounded_double(&candidate, Some(&search_ceiling_raw));
            if next <= candidate {
                break;
            }
            candidate = next;
        }
        if !has_upper_amount && selected_amount > BigUint::zero() {
            upper_amount = bounded_double(&selected_amount, Some(&search_ceiling_raw));
            base_amount = selected_amount.clone();
            lower_amount = bounded_half(&selected_amount, &min_probe_raw);
            has_upper_amount = upper_amount > BigUint::zero() && upper_amount > selected_amount;
        }
    } else {
        // D4-b: the first probe reverted, so the search must continue in BOTH
        // directions.
        //
        // The previous code only searched downward from the initial amount
        // (halving towards min_probe_raw). A revert at the start therefore
        // terminated the search and every size ABOVE the start was
        // unreachable, even when the pool could carry a profitable position
        // larger than the start. The downward search is kept, and an upward
        // search towards max_probe_raw is added.
        //
        // The two searches are interleaved by probe budget: the ladder stops
        // after max_steps attempts, so the upward search is attempted first
        // while attempts remain, because a larger position is the case the
        // old code could never reach.
        upper_amount = initial_amount_raw.clone();
        let mut candidate = bounded_half(&initial_amount_raw, &min_probe_raw);
        while attempts.len() < max_steps && candidate > BigUint::zero() {
            let payload = official_v4_probe_one(
                config,
                rpc_url,
                arb_address,
                call_from,
                timeout_seconds,
                &candidate,
                true,
                &mut attempts,
                &mut total_attempted,
            )?;
            if payload.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                selected_amount = candidate.clone();
                selected_profit = payload
                    .get("selectedProfitRaw")
                    .and_then(Value::as_str)
                    .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                    .unwrap_or_else(BigUint::zero);
                selected_route_index = payload
                    .get("selectedRouteIndex")
                    .and_then(Value::as_str)
                    .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                    .unwrap_or_else(BigUint::zero);
                base_amount = candidate.clone();
                lower_amount = bounded_half(&candidate, &min_probe_raw);
                has_upper_amount = true;
                break;
            }
            upper_amount = candidate.clone();
            let next = bounded_half(&candidate, &min_probe_raw);
            if next == candidate {
                break;
            }
            candidate = next;
        }

        // D4-b: search upward as well. The ladder now climbs from the
        // capacity-informed start towards max_probe_raw, so a position larger
        // than the start becomes reachable even when the first probe reverted.
        if selected_amount == BigUint::zero() {
            let mut upward = bounded_double(&initial_amount_raw, Some(&search_ceiling_raw));
            while attempts.len() < max_steps
                && upward > initial_amount_raw
                && upward > BigUint::zero()
            {
                let payload = official_v4_probe_one(
                    config,
                    rpc_url,
                    arb_address,
                    call_from,
                    timeout_seconds,
                    &upward,
                    true,
                    &mut attempts,
                    &mut total_attempted,
                )?;
                if payload.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                    selected_amount = upward.clone();
                    selected_profit = payload
                        .get("selectedProfitRaw")
                        .and_then(Value::as_str)
                        .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                        .unwrap_or_else(BigUint::zero);
                    selected_route_index = payload
                        .get("selectedRouteIndex")
                        .and_then(Value::as_str)
                        .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                        .unwrap_or_else(BigUint::zero);
                    base_amount = upward.clone();
                    lower_amount = bounded_half(&upward, &min_probe_raw);
                    // The amount above the profitable point is unknown; the
                    // refinement loop uses the configured ceiling.
                    has_upper_amount = true;
                    upper_amount = bounded_double(&upward, Some(&search_ceiling_raw));
                    break;
                }
                // The upward probe reverted, so this size is too large. Keep
                // halving back down towards the start instead of doubling
                // further away from a reachable size.
                let next = bounded_half(&upward, &min_probe_raw);
                if next >= upward || next <= initial_amount_raw {
                    break;
                }
                upward = next;
            }
        }
    }

    if selected_amount == BigUint::zero() {
        return Ok(json!({
            "method": "previewArbitrageBestAmount",
            "searchMode": search_mode,
            "requestedInitialAmountRaw": requested_initial_amount_raw
                .as_ref()
                .map(|value| format!("0x{:x}", value))
                .unwrap_or_else(|| Value::Null.to_string()),
            "initialAmountRaw": format!("0x{:x}", initial_amount_raw),
            "singleTickCapacityRaw": format!("0x{:x}", capacity_limit_raw),
            "singleTickCapacityBasis": capacity_basis,
            search_capacity_key: format!("0x{:x}", capacity_limit_raw),
            "searchCapacityBasis": capacity_basis,
            "searchCeilingRaw": format!("0x{:x}", search_ceiling_raw),
            "searchCeilingBasis": capacity_basis,
            "ladderStartBasis": initial_amount_basis,
            "derivedInitialAmountRaw": format!("0x{:x}", derived_initial_amount_raw),
            "minProfitRaw": format!("0x{:x}", min_profit_raw),
            "status": "not_profitable_after_costs",
            "selectedAmountRaw": "0x0",
            "selectedProfitRaw": "0x0",
            "selectedRouteIndex": "0x0",
            "attemptedCount": format!("0x{:x}", total_attempted),
            "attempts": attempts,
            // The measured signed loss of the last size the ladder priced.
            // The probes already carry it; publishing it here keeps the
            // ladder's own conclusion instead of dropping it.
            "reportedNetProfitRaw": measured_probe_loss_raw(&attempts),
            // The single-tick mode is the default. A route whose single-tick
            // result is worth less than the shared USDG threshold is worth
            // a cross-tick review, because the cross-tick mode may still
            // find room that the single-tick range cannot express.
            "crossTickReview": cross_tick_review_with_search(
                config,
                rpc_url,
                arb_address,
                call_from,
                timeout_seconds,
                &BigUint::zero(),
                &BigUint::zero(),
            ),
        }));
    }

    while attempts.len() < max_steps
        && upper_amount > base_amount.clone() + BigUint::from(1u8)
        && lower_amount < base_amount
        && upper_amount > lower_amount
    {
        let left_mid = &lower_amount + ((&base_amount - &lower_amount) / BigUint::from(2u8));
        let right_mid = &base_amount + ((&upper_amount - &base_amount) / BigUint::from(2u8));
        if left_mid <= lower_amount
            || left_mid >= base_amount
            || right_mid <= base_amount
            || right_mid >= upper_amount
        {
            break;
        }
        let left_result = official_v4_probe_one(
            config,
            rpc_url,
            arb_address,
            call_from,
            timeout_seconds,
            &left_mid,
            true,
            &mut attempts,
            &mut total_attempted,
        );
        let right_result = official_v4_probe_one(
            config,
            rpc_url,
            arb_address,
            call_from,
            timeout_seconds,
            &right_mid,
            true,
            &mut attempts,
            &mut total_attempted,
        );
        let (left_payload, right_payload) = match (left_result, right_result) {
            (Ok(left), Ok(right)) => (left, right),
            _ => break,
        };
        let left_ok = left_payload
            .get("ok")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let right_ok = right_payload
            .get("ok")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if !left_ok && !right_ok {
            break;
        }
        let left_profit = if left_ok {
            left_payload
                .get("selectedProfitRaw")
                .and_then(Value::as_str)
                .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                .unwrap_or_else(BigUint::zero)
        } else {
            BigUint::zero()
        };
        let right_profit = if right_ok {
            right_payload
                .get("selectedProfitRaw")
                .and_then(Value::as_str)
                .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
                .unwrap_or_else(BigUint::zero)
        } else {
            BigUint::zero()
        };
        let left_improves = left_ok && left_profit > selected_profit;
        let right_improves = right_ok && right_profit > selected_profit;
        if !left_improves && !right_improves {
            break;
        }
        let left_index = left_payload
            .get("selectedRouteIndex")
            .and_then(Value::as_str)
            .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
            .unwrap_or_else(BigUint::zero);
        let right_index = right_payload
            .get("selectedRouteIndex")
            .and_then(Value::as_str)
            .and_then(|value| BigUint::parse_bytes(strip_0x(value).as_bytes(), 16))
            .unwrap_or_else(BigUint::zero);
        if left_ok && right_ok {
            if left_profit >= right_profit {
                if left_improves {
                    selected_amount = left_mid.clone();
                    selected_profit = left_profit.clone();
                    selected_route_index = left_index;
                }
            } else if right_improves {
                selected_amount = right_mid.clone();
                selected_profit = right_profit.clone();
                selected_route_index = right_index;
            }
        } else if left_ok {
            if left_improves {
                selected_amount = left_mid.clone();
                selected_profit = left_profit.clone();
                selected_route_index = left_index;
            }
        } else if right_ok {
            if right_improves {
                selected_amount = right_mid.clone();
                selected_profit = right_profit.clone();
                selected_route_index = right_index;
            }
        }
        let old_base = base_amount.clone();
        if left_ok && right_ok {
            if left_profit >= right_profit {
                upper_amount = old_base;
                base_amount = left_mid;
            } else {
                lower_amount = old_base;
                base_amount = right_mid;
            }
        } else if left_ok {
            upper_amount = old_base;
            base_amount = left_mid;
        } else {
            lower_amount = old_base;
            base_amount = right_mid;
        }
    }

    Ok(json!({
        "method": "previewArbitrageBestAmount",
        "searchMode": search_mode,
        "requestedInitialAmountRaw": requested_initial_amount_raw
            .as_ref()
            .map(|value| json!(format!("0x{:x}", value)))
            .unwrap_or(Value::Null),
        "ladderStartBasis": initial_amount_basis,
        "derivedInitialAmountRaw": format!("0x{:x}", derived_initial_amount_raw),
        "initialAmountRaw": format!("0x{:x}", initial_amount_raw),
        "singleTickCapacityRaw": format!("0x{:x}", capacity_limit_raw),
            "singleTickCapacityBasis": capacity_basis,
        search_capacity_key: format!("0x{:x}", capacity_limit_raw),
        "searchCapacityBasis": capacity_basis,
        "searchCeilingRaw": format!("0x{:x}", search_ceiling_raw),
        "searchCeilingBasis": capacity_basis,
        "minProfitRaw": format!("0x{:x}", min_profit_raw),
        "status": "profitable_amount_found",
        "selectedAmountRaw": format!("0x{:x}", selected_amount),
        "selectedProfitRaw": format!("0x{:x}", selected_profit),
        "singleTickBestAmountRaw": format!("0x{:x}", selected_amount),
        "singleTickBestProfitRaw": format!("0x{:x}", selected_profit),
        "selectedRouteIndex": format!("0x{:x}", selected_route_index),
        "attemptedCount": format!("0x{:x}", total_attempted),
        "attempts": attempts,
        "baseAmountRaw": format!("0x{:x}", base_amount),
        "lowerAmountRaw": format!("0x{:x}", lower_amount),
        "upperAmountRaw": format!("0x{:x}", upper_amount),
        "hasUpperBound": has_upper_amount,
        // The single-tick mode is the default. A route whose single-tick
        // profit is worth less than the shared USDG threshold is worth a
        // cross-tick review, because the cross-tick mode may still find
        // room that the single-tick range cannot express.
        "crossTickReview": cross_tick_review_with_search(
            config,
            rpc_url,
            arb_address,
            call_from,
            timeout_seconds,
            &selected_amount,
            &selected_profit,
        ),
    }))
}

pub(crate) fn official_v4_preview_and_ladder_inner(
    config: &Value,
    rpc_url: &str,
    arb_address: &str,
    call_from: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    let token = config
        .get("token")
        .and_then(Value::as_str)
        .ok_or_else(|| "preview pool token is required".to_string())?;
    let entry_keys = config
        .get("entryKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool entryKeys is required".to_string())?;
    let entry_directions = config
        .get("entryDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool entryDirections is required".to_string())?;
    let bridge_keys = config
        .get("bridgeKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool bridgeKeys is required".to_string())?;
    let bridge_directions = config
        .get("bridgeDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool bridgeDirections is required".to_string())?;
    let exit_keys = config
        .get("exitKeys")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool exitKeys is required".to_string())?;
    let exit_directions = config
        .get("exitDirections")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview pool exitDirections is required".to_string())?;
    let min_profit_raw = parse_big_uint(
        config
            .get("minProfitRaw")
            .ok_or_else(|| "preview pool minProfitRaw is required".to_string())?,
    )?;
    let search_mode = config
        .get("searchMode")
        .and_then(Value::as_str)
        .unwrap_or("inner_single_call")
        .to_string();
    // The kernel owns the trading amount. The Python runtime only describes
    // the environment (chain, pools, protocol) and never publishes an
    // `initialAmountRaw`, so requiring that key here would turn every route
    // into a hard error before a single pool was even queried. The start is
    // derived from the same published single-tick capacity the ladder uses.
    let (capacity_limit_raw, capacity_basis, search_capacity_key) =
        resolve_search_capacity(config)?;
    let derived_start = derive_initial_amount_raw(config, capacity_limit_raw.as_ref())?;
    let initial_amount_raw = derived_start.initial_amount_raw.clone();
    let initial_amount_basis = derived_start.basis.clone();
    let derived_initial_amount_raw = derived_start.derived_initial_amount_raw.clone();
    let Some(capacity_limit_raw) = capacity_limit_raw.as_ref() else {
        // No published capacity means there is no defensible start amount, so
        // the route is blocked before it probes anything. The record says so
        // explicitly and points at the closed-loop manager, which is the tool
        // that publishes the capacity.
        return Ok(json!({
            "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
            "searchMode": search_mode,
            "status": "single_tick_capacity_unavailable",
            "ladderBlocked": true,
            "ladderBlockedReason": "single_tick_capacity_unavailable",
            "repairTarget": "closed_loop_manager",
            "requestedInitialAmountRaw": config
                .get("initialAmountRaw")
                .cloned()
                .unwrap_or(Value::Null),
            "initialAmountRaw": "0x0",
            "derivedInitialAmountRaw": "0x0",
            "minProfitRaw": format!("0x{:x}", min_profit_raw),
            "selectedAmountRaw": "0x0",
            "selectedProfitRaw": "0x0",
            "selectedRouteIndex": "0x0",
            "attemptedCount": "0x0",
            "attempts": [],
            "error": "single_tick_capacity_unavailable",
        }));
    };
    let capacity_limit_raw = capacity_limit_raw.clone();
    let pool_preview =
        preview_best_pool_inner(config, rpc_url, arb_address, call_from, timeout_seconds)?;
    if pool_preview
        .get("ok")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        != true
    {
        return v4_attempt_no_profit_detailed(
            config,
            token,
            pool_preview,
            entry_keys,
            entry_directions,
            bridge_keys,
            bridge_directions,
            exit_keys,
            exit_directions,
            &initial_amount_raw,
            &min_profit_raw,
            &search_mode,
            rpc_url,
            arb_address,
            call_from,
            timeout_seconds,
        );
    }
    let entry_index = big_uint_to_usize(&parse_big_uint(
        pool_preview
            .get("entryIndex")
            .ok_or_else(|| "previewBestPool returned no entryIndex".to_string())?,
    )?)?;
    let bridge_index = big_uint_to_usize(&parse_big_uint(
        pool_preview
            .get("bridgeIndex")
            .ok_or_else(|| "previewBestPool returned no bridgeIndex".to_string())?,
    )?)?;
    let exit_index = big_uint_to_usize(&parse_big_uint(
        pool_preview
            .get("exitIndex")
            .ok_or_else(|| "previewBestPool returned no exitIndex".to_string())?,
    )?)?;
    let net_product = parse_big_uint(
        pool_preview
            .get("netProduct")
            .ok_or_else(|| "previewBestPool returned no netProduct".to_string())?,
    )?;
    if net_product <= BigUint::from(10u32).pow(18) {
        return v4_attempt_no_profit_detailed(
            config,
            token,
            pool_preview,
            entry_keys,
            entry_directions,
            bridge_keys,
            bridge_directions,
            exit_keys,
            exit_directions,
            &initial_amount_raw,
            &min_profit_raw,
            &search_mode,
            rpc_url,
            arb_address,
            call_from,
            timeout_seconds,
        );
    }
    let selected_keys = vec![
        entry_keys
            .get(entry_index)
            .ok_or_else(|| format!("previewBestPool entryIndex out of range: {entry_index}"))?
            .clone(),
        bridge_keys
            .get(bridge_index)
            .ok_or_else(|| format!("previewBestPool bridgeIndex out of range: {bridge_index}"))?
            .clone(),
        exit_keys
            .get(exit_index)
            .ok_or_else(|| format!("previewBestPool exitIndex out of range: {exit_index}"))?
            .clone(),
    ];
    let selected_directions = vec![
        entry_directions
            .get(entry_index)
            .ok_or_else(|| {
                format!("previewBestPool entry direction index out of range: {entry_index}")
            })?
            .clone(),
        bridge_directions
            .get(bridge_index)
            .ok_or_else(|| {
                format!("previewBestPool bridge direction index out of range: {bridge_index}")
            })?
            .clone(),
        exit_directions
            .get(exit_index)
            .ok_or_else(|| {
                format!("previewBestPool exit direction index out of range: {exit_index}")
            })?
            .clone(),
    ];
    let mut ladder_config = config.clone();
    ladder_config["token"] = json!(token);
    ladder_config["keysets"] = json!([selected_keys]);
    ladder_config["directionsets"] = json!([selected_directions]);
    ladder_config["initialAmountRaw"] = json!(format!("0x{:x}", initial_amount_raw));
    ladder_config["minProfitRaw"] = json!(format!("0x{:x}", min_profit_raw));
    ladder_config["searchMode"] = json!(search_mode);
    let ladder = official_v4_adaptive_best_amount_single_route_inner(
        &ladder_config,
        rpc_url,
        arb_address,
        call_from,
        timeout_seconds,
    )?;
    let mut payload = json!({
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": search_mode,
        "status": ladder.get("status").cloned().unwrap_or(Value::Null),
        "initialAmountRaw": format!("0x{:x}", initial_amount_raw),
        "derivedInitialAmountRaw": format!("0x{:x}", derived_initial_amount_raw),
        "ladderStartBasis": initial_amount_basis,
        "singleTickCapacityRaw": format!("0x{:x}", capacity_limit_raw),
        "singleTickCapacityBasis": capacity_basis,
        search_capacity_key: format!("0x{:x}", capacity_limit_raw),
        "searchCapacityBasis": capacity_basis,
        "minProfitRaw": format!("0x{:x}", min_profit_raw),
        "poolPreview": pool_preview,
        "selectedPool": {
            "entryIndex": entry_index,
            "bridgeIndex": bridge_index,
            "exitIndex": exit_index,
            "netProduct": format!("0x{:x}", net_product),
        },
        "selectedKeys": selected_keys,
        "selectedDirections": selected_directions,
        "selectedAmountRaw": ladder.get("selectedAmountRaw").cloned().unwrap_or(Value::Null),
        "selectedProfitRaw": ladder.get("selectedProfitRaw").cloned().unwrap_or(Value::Null),
        "selectedRouteIndex": ladder.get("selectedRouteIndex").cloned().unwrap_or(Value::Null),
        "attemptedCount": ladder.get("attemptedCount").cloned().unwrap_or(Value::Null),
        "attempts": ladder.get("attempts").cloned().unwrap_or_else(|| json!([])),
        "ladder": ladder,
    });
    let selected_amount_raw = parse_big_uint(
        payload
            .get("selectedAmountRaw")
            .ok_or_else(|| "ladder returned no selectedAmountRaw".to_string())?,
    )
    .unwrap_or_default();
    let selected_profit_raw = parse_big_uint(
        payload
            .get("selectedProfitRaw")
            .ok_or_else(|| "ladder returned no selectedProfitRaw".to_string())?,
    )
    .unwrap_or_default();
    if selected_amount_raw > BigUint::zero() && selected_profit_raw > BigUint::zero() {
        let mut run_config = config.clone();
        run_config["token"] = json!(token);
        run_config["keys"] = json!(selected_keys);
        run_config["directions"] = json!(selected_directions);
        run_config["selectedAmountRaw"] = json!(format!("0x{:x}", selected_amount_raw));
        run_config["minProfitRaw"] = json!(format!("0x{:x}", min_profit_raw));
        match run_arbitrage_inner(
            &run_config,
            rpc_url,
            arb_address,
            call_from,
            timeout_seconds,
        ) {
            Ok(run_result) => {
                payload["runArbitrage"] = run_result.clone();
                if run_result
                    .get("ok")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    payload["outTb"] = run_result.get("outTb").cloned().unwrap_or(Value::Null);
                    payload["outTa"] = run_result.get("outTa").cloned().unwrap_or(Value::Null);
                    payload["outTz"] = run_result.get("outTz").cloned().unwrap_or(Value::Null);
                    payload["profitRaw"] = run_result.get("profit").cloned().unwrap_or(Value::Null);
                }
            }
            Err(error) => {
                payload["runArbitrage"] = json!({"ok": false, "error": error});
            }
        }
    }
    if payload.get("status").and_then(Value::as_str) != Some("profitable_amount_found") {
        if payload.get("poolPreviewDetailed").is_none() {
            if let Ok(detailed) = preview_best_pool_detailed_inner(
                config,
                rpc_url,
                arb_address,
                call_from,
                timeout_seconds,
            ) {
                payload["poolPreviewDetailed"] = detailed.clone();
                if let Some(delta) = detailed.get("netProductDeltaRaw") {
                    payload["reportedNetProductDeltaRaw"] = delta.clone();
                }
                if let Some(delta_dec) = detailed.get("netProductDeltaDec") {
                    payload["reportedNetProductDeltaDec"] = delta_dec.clone();
                }
            }
        }
        if payload.get("runArbitrage").is_none() {
            let run_amount_raw = if selected_amount_raw > BigUint::zero() {
                selected_amount_raw.clone()
            } else {
                initial_amount_raw.clone()
            };
            if run_amount_raw > BigUint::zero() {
                let mut run_config = config.clone();
                run_config["token"] = json!(token);
                run_config["keys"] = json!(selected_keys);
                run_config["directions"] = json!(selected_directions);
                run_config["selectedAmountRaw"] = json!(format!("0x{:x}", run_amount_raw));
                run_config["minProfitRaw"] = json!(format!("0x{:x}", min_profit_raw));
                if let Ok(run_result) = run_arbitrage_inner(
                    &run_config,
                    rpc_url,
                    arb_address,
                    call_from,
                    timeout_seconds,
                ) {
                    payload["runArbitrage"] = run_result.clone();
                    if let Some(profit_raw) = run_result.get("reportedNetProfitRaw") {
                        payload["reportedNetProfitRaw"] = profit_raw.clone();
                    }
                    if let Some(arbitrage) = run_result.get("arbitrageNotProfitable") {
                        payload["arbitrageNotProfitable"] = arbitrage.clone();
                    }
                }
            }
        }
    }
    // The amount ladder is the authority on the best trade amount, and this
    // path measured it just like the no-profit path does.  The payload must
    // therefore publish the measurement, otherwise a route whose pool
    // preview succeeded but whose ladder found no profitable size is
    // reported as "the best amount is missing" even though the ladder
    // measured every size it tried.
    //
    // The two outcomes stay distinct:
    //
    // * the ladder found a profitable size -> the best amount is that size
    //   and it is confirmed;
    // * the ladder found no profitable size -> the best amount is ``0``,
    //   the measurement is confirmed, and the review states that the route
    //   is unprofitable at every size the ladder tried.
    //
    // A ladder that was blocked before it probed publishes no probe row at
    // all, and that case keeps its own blocked status: nothing was measured,
    // so nothing may be claimed.
    publish_ladder_best_amount(&mut payload);
    Ok(payload)
}

/// Publish the measured best trade amount of a ladder result.
///
/// The helper is idempotent: it only fills the fields when the ladder
/// actually produced concrete probe records, so a payload that already
/// carries the measurement is left untouched.
fn publish_ladder_best_amount(payload: &mut Value) {
    let Some(attempts) = payload
        .get("attempts")
        .and_then(Value::as_array)
        .cloned()
    else {
        return;
    };
    let measured_probes = attempts
        .iter()
        .filter(|row| row.get("amountRaw").and_then(Value::as_str).is_some())
        .count();
    if measured_probes == 0 {
        return;
    }
    payload["bestAmountMissing"] = json!(false);
    payload["bestAmountMeasured"] = json!(true);
    let profitable = attempts
        .iter()
        .find(|row| row.get("profitable").and_then(Value::as_bool) == Some(true));
    match profitable {
        Some(row) => {
            let amount_raw = row
                .get("amountRaw")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            if !amount_raw.is_empty() {
                payload["bestAmount"] = json!(amount_raw);
                payload["bestAmountConfirmed"] = json!(true);
                payload["bestAmountSelectionBasis"] =
                    json!("native_rust_ladder_profitable_probe");
            }
        }
        None => {
            payload["bestAmount"] = json!("0x0");
            payload["bestAmountConfirmed"] = json!(true);
            payload["bestAmountSelectionBasis"] =
                json!("native_rust_ladder_no_profitable_size_measured");
            payload["bestAmountZeroReason"] = json!(format!(
                "the amount ladder measured {} size(s) and none of them was profitable",
                measured_probes
            ));
        }
    }
}

fn submit_v4_route_inner(
    config: &Value,
    raw_transaction: &str,
    relay_urls: Option<&str>,
) -> Result<Value, String> {
    let raw = decode_hex(raw_transaction)?;
    let rpc_url = config
        .get("rpc_url")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let timeout_seconds = config
        .get("timeout_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(15);
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
        let result = execute_rpc_with_revert(timeout_seconds.max(1), |runtime, client| {
            runtime.block_on(json_rpc_call(
                client,
                &url,
                "eth_sendRawTransaction",
                json!([as_hex(&raw)]),
                timeout_seconds.max(1),
            ))
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
            Err((message, revert_data)) => {
                let mut evidence = rpc_error_evidence(
                    &message,
                    revert_data.as_deref(),
                    "eth_sendRawTransaction",
                    "private_rpc",
                );
                evidence["relay"] = json!(name);
                errors.push(evidence);
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
            "error": "private relay rejected transaction; public fallback disabled",
            "rawError": "private relay rejected transaction; public fallback disabled",
            "revertData": errors.first().and_then(|item| item.get("revertData")).cloned().unwrap_or_else(|| json!("")),
            "revertSelector": errors.first().and_then(|item| item.get("revertSelector")).cloned().unwrap_or_else(|| json!("")),
            "rpcMethod": "eth_sendRawTransaction",
            "submitted": false,
            "private_relay_metrics": metrics,
        }));
    }
    if rpc_url.trim().is_empty() {
        return Err("public RPC URL is empty; cannot fall back".to_string());
    }
    let started = Instant::now();
    let result = execute_rpc_with_revert(timeout_seconds.max(1), |runtime, client| {
        runtime.block_on(json_rpc_call(
            client,
            &rpc_url,
            "eth_sendRawTransaction",
            json!([as_hex(&raw)]),
            timeout_seconds.max(1),
        ))
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
        Err((message, revert_data)) => {
            let mut evidence = rpc_error_evidence(
                &message,
                revert_data.as_deref(),
                "eth_sendRawTransaction",
                "public_rpc",
            );
            evidence["tx_hash"] = Value::Null;
            evidence["broadcast_channel"] = json!("not_broadcast");
            evidence["status"] = json!("public_fallback_failed");
            evidence["relay_errors"] = json!(errors);
            evidence["private_relay_metrics"] = json!(metrics);
            Ok(evidence)
        }
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

/// The receipt of a transaction that was just sent, read **without waiting**.
///
/// The send is the fact; the receipt is the answer, and the answer arrives
/// when the chain mines the transaction. Waiting for it here would hold the
/// thread that sent the transaction for as long as the chain takes, and a
/// thread that is held cannot send the next opportunity.
///
/// The lookup therefore reads the receipt once and reports what the node
/// knows at that moment. A transaction that is still pending is the normal
/// case, and the broadcast confirmation thread reads the rest of the answer
/// when it arrives.
pub(crate) fn receipt_without_waiting(rpc_url: &str, tx_hash: &str, timeout_seconds: u64) -> Value {
    load_v4_receipt_inner(&vec![rpc_url.to_string()], tx_hash, false, timeout_seconds)
        .unwrap_or_else(|error| json!({"status": "pending", "error": error}))
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

fn rpc_quantity_u64(
    rpc_url: &str,
    method: &str,
    params: Value,
    timeout_seconds: u64,
) -> Result<u64, String> {
    let result = execute_rpc(timeout_seconds.max(1), |runtime, client| {
        runtime
            .block_on(json_rpc_call(
                client,
                rpc_url,
                method,
                params,
                timeout_seconds.max(1),
            ))
            .map_err(|(message, _)| message)
    })?;
    let text = result
        .as_str()
        .ok_or_else(|| format!("{method} result is not a hex quantity"))?;
    parse_u64_hexish(text)
}

fn estimate_official_v4_gas(
    rpc_url: &str,
    from: &str,
    to: &str,
    data: &str,
    timeout_seconds: u64,
) -> Result<u64, String> {
    let result = execute_rpc(timeout_seconds.max(1), |runtime, client| {
        runtime
            .block_on(json_rpc_call(
                client,
                rpc_url,
                "eth_estimateGas",
                json!([{
                    "from": from.to_lowercase(),
                    "to": to.to_lowercase(),
                    "data": data,
                }]),
                timeout_seconds.max(1),
            ))
            .map_err(|(message, _)| message)
    })?;
    let text = result
        .as_str()
        .ok_or_else(|| "eth_estimateGas result is not a hex quantity".to_string())?;
    parse_u64_hexish(text)
}

fn broadcast_calldata_inner(
    config: &Value,
    calldata: String,
    rpc_url: &str,
    contract_address: &str,
    private_key: &str,
    chain_id: u64,
    relay_urls: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    let signer = resolve_private_key_hex_inner(private_key)?;
    let nonce = rpc_quantity_u64(
        rpc_url,
        "eth_getTransactionCount",
        json!([signer.to_lowercase(), "pending"]),
        timeout_seconds,
    )?;
    let gas_limit = if let Some(raw) = config.get("gasLimitRaw").and_then(Value::as_str) {
        parse_u64_hexish(raw)?
    } else if let Some(raw) = config.get("gasLimit").and_then(Value::as_u64) {
        raw
    } else {
        estimate_official_v4_gas(
            rpc_url,
            &signer,
            contract_address,
            &calldata,
            timeout_seconds,
        )?
        .max(300_000)
    };
    let max_priority_fee = if let Some(raw) = config
        .get("maxPriorityFeePerGasRaw")
        .and_then(Value::as_str)
    {
        parse_u64_hexish(raw)?
    } else if let Some(raw) = config.get("maxPriorityFeePerGas").and_then(Value::as_u64) {
        raw
    } else {
        rpc_quantity_u64(
            rpc_url,
            "eth_maxPriorityFeePerGas",
            json!([]),
            timeout_seconds,
        )
        .unwrap_or(1_000_000_000u64)
    };
    let max_fee = if let Some(raw) = config.get("maxFeePerGasRaw").and_then(Value::as_str) {
        parse_u64_hexish(raw)?
    } else if let Some(raw) = config.get("maxFeePerGas").and_then(Value::as_u64) {
        raw
    } else {
        let gas_price = rpc_quantity_u64(rpc_url, "eth_gasPrice", json!([]), timeout_seconds)?;
        gas_price
            .saturating_mul(2)
            .saturating_add(max_priority_fee)
            .max(max_priority_fee)
    };

    let transaction = json!({
        "type": 2,
        "chainId": chain_id,
        "nonce": nonce,
        "maxPriorityFeePerGas": format!("0x{max_priority_fee:x}"),
        "maxFeePerGas": format!("0x{max_fee:x}"),
        "gas": format!("0x{gas_limit:x}"),
        "to": contract_address.to_lowercase(),
        "value": 0,
        "data": calldata,
    });
    let signed = sign_typed_transaction_inner(&transaction, private_key)?;
    let raw = signed
        .get("raw")
        .and_then(Value::as_str)
        .ok_or_else(|| "signed transaction has no raw payload".to_string())?
        .to_string();
    let mut submit_config = config.clone();
    submit_config["rpc_url"] = json!(rpc_url);
    submit_config["timeout_seconds"] = json!(timeout_seconds);
    let submitted = submit_v4_route_inner(&submit_config, &raw, relay_urls)?;
    let tx_hash = submitted
        .get("tx_hash")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if tx_hash.is_empty() {
        return Ok(json!({
            "ok": false,
            "broadcast": submitted,
            "status": "broadcast_not_accepted",
            "txHash": Value::Null,
            "error": submitted.get("error").cloned().unwrap_or_else(|| json!("broadcast_not_accepted")),
            "rawError": submitted.get("rawError").cloned().unwrap_or(Value::Null),
            "revertData": submitted.get("revertData").cloned().unwrap_or_else(|| json!("")),
            "revertSelector": submitted.get("revertSelector").cloned().unwrap_or_else(|| json!("")),
            "rpcMethod": submitted.get("rpcMethod").cloned().unwrap_or_else(|| json!("eth_sendRawTransaction")),
            "broadcastChannel": submitted.get("broadcast_channel").cloned().unwrap_or_else(|| json!("not_broadcast")),
            "relayErrors": submitted.get("relay_errors").cloned().unwrap_or_else(|| json!([])),
            "submitted": false,
        }));
    }
    let receipt = receipt_without_waiting(rpc_url, &tx_hash, timeout_seconds);
    Ok(json!({
        "ok": true,
        "txHash": tx_hash,
        "signer": signer,
        "calldata": calldata,
        "nonce": nonce,
        "gasLimit": gas_limit,
        "maxPriorityFeePerGas": format!("0x{max_priority_fee:x}"),
        "maxFeePerGas": format!("0x{max_fee:x}"),
        "broadcast": submitted,
        "receipt": receipt,
    }))
}

pub(crate) fn broadcast_v4_best_pools_inner(
    config: &Value,
    selected_amount_raw: &BigUint,
    min_profit_raw: &BigUint,
    rpc_url: &str,
    arb_address: &str,
    private_key: &str,
    chain_id: u64,
    relay_urls: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    if rpc_url.trim().is_empty() {
        return Err("official V4 broadcast RPC URL is empty".to_string());
    }
    if private_key.trim().is_empty() {
        return Err("official V4 broadcast signer is not configured".to_string());
    }
    let signer = resolve_private_key_hex_inner(private_key)?;
    let mut run_config = config.clone();
    run_config["selectedAmountRaw"] = json!(format!("0x{:x}", selected_amount_raw));
    run_config["minProfitRaw"] = json!(format!("0x{:x}", min_profit_raw));
    let calldata = build_v4_run_best_calldata(&run_config)?;

    let nonce = rpc_quantity_u64(
        rpc_url,
        "eth_getTransactionCount",
        json!([signer.to_lowercase(), "pending"]),
        timeout_seconds,
    )?;
    let gas_limit = if let Some(raw) = config.get("gasLimitRaw").and_then(Value::as_str) {
        parse_u64_hexish(raw)?
    } else if let Some(raw) = config.get("gasLimit").and_then(Value::as_u64) {
        raw
    } else {
        estimate_official_v4_gas(rpc_url, &signer, arb_address, &calldata, timeout_seconds)?
            .max(300_000)
    };
    let max_priority_fee = if let Some(raw) = config
        .get("maxPriorityFeePerGasRaw")
        .and_then(Value::as_str)
    {
        parse_u64_hexish(raw)?
    } else if let Some(raw) = config.get("maxPriorityFeePerGas").and_then(Value::as_u64) {
        raw
    } else {
        rpc_quantity_u64(
            rpc_url,
            "eth_maxPriorityFeePerGas",
            json!([]),
            timeout_seconds,
        )
        .unwrap_or(1_000_000_000u64)
    };
    let max_fee = if let Some(raw) = config.get("maxFeePerGasRaw").and_then(Value::as_str) {
        parse_u64_hexish(raw)?
    } else if let Some(raw) = config.get("maxFeePerGas").and_then(Value::as_u64) {
        raw
    } else {
        let gas_price = rpc_quantity_u64(rpc_url, "eth_gasPrice", json!([]), timeout_seconds)?;
        gas_price
            .saturating_mul(2)
            .saturating_add(max_priority_fee)
            .max(max_priority_fee)
    };

    let transaction = json!({
        "type": 2,
        "chainId": chain_id,
        "nonce": nonce,
        "maxPriorityFeePerGas": format!("0x{max_priority_fee:x}"),
        "maxFeePerGas": format!("0x{max_fee:x}"),
        "gas": format!("0x{gas_limit:x}"),
        "to": arb_address.to_lowercase(),
        "value": 0,
        "data": calldata,
    });
    let signed = sign_typed_transaction_inner(&transaction, private_key)?;
    let raw = signed
        .get("raw")
        .and_then(Value::as_str)
        .ok_or_else(|| "signed transaction has no raw payload".to_string())?
        .to_string();
    let mut submit_config = config.clone();
    submit_config["rpc_url"] = json!(rpc_url);
    submit_config["timeout_seconds"] = json!(timeout_seconds);
    let submitted = submit_v4_route_inner(&submit_config, &raw, relay_urls)?;
    let tx_hash = submitted
        .get("tx_hash")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if tx_hash.is_empty() {
        return Ok(json!({
            "ok": false,
            "broadcast": submitted,
            "status": "broadcast_not_accepted",
            "txHash": Value::Null,
            "error": submitted.get("error").cloned().unwrap_or_else(|| json!("broadcast_not_accepted")),
            "rawError": submitted.get("rawError").cloned().unwrap_or(Value::Null),
            "revertData": submitted.get("revertData").cloned().unwrap_or_else(|| json!("")),
            "revertSelector": submitted.get("revertSelector").cloned().unwrap_or_else(|| json!("")),
            "rpcMethod": submitted.get("rpcMethod").cloned().unwrap_or_else(|| json!("eth_sendRawTransaction")),
            "broadcastChannel": submitted.get("broadcast_channel").cloned().unwrap_or_else(|| json!("not_broadcast")),
            "relayErrors": submitted.get("relay_errors").cloned().unwrap_or_else(|| json!([])),
            "submitted": false,
        }));
    }
    let receipt = receipt_without_waiting(rpc_url, &tx_hash, timeout_seconds);
    Ok(json!({
        "ok": true,
        "txHash": tx_hash,
        "signer": signer,
        "calldata": calldata,
        "nonce": nonce,
        "gasLimit": gas_limit,
        "maxPriorityFeePerGas": format!("0x{max_priority_fee:x}"),
        "maxFeePerGas": format!("0x{max_fee:x}"),
        "broadcast": submitted,
        "receipt": receipt,
    }))
}

pub(crate) fn broadcast_v4_flash_batch_inner(
    config: &Value,
    requests: &[Value],
    rpc_url: &str,
    executor_address: &str,
    private_key: &str,
    chain_id: u64,
    relay_urls: Option<&str>,
    timeout_seconds: u64,
) -> Result<Value, String> {
    if rpc_url.trim().is_empty() {
        return Err("V4 executor batch broadcast RPC URL is empty".to_string());
    }
    if executor_address.trim().is_empty() {
        return Err("V4 executor batch address is empty".to_string());
    }
    if private_key.trim().is_empty() {
        return Err("V4 executor batch signer is not configured".to_string());
    }
    let calldata = build_v4_flash_batch_calldata(requests)?;
    let mut result = broadcast_calldata_inner(
        config,
        calldata,
        rpc_url,
        executor_address,
        private_key,
        chain_id,
        relay_urls,
        timeout_seconds,
    )?;
    result["batchRequestCount"] = json!(requests.len());
    result["broadcastMode"] = json!("bounded_v4_flash_batch");
    Ok(result)
}

fn parse_json(raw: &str, label: &str) -> Result<Value, PyErr> {
    serde_json::from_str(raw)
        .map_err(|error| as_py_error(format!("{label} must be valid JSON: {error}")))
}

#[pyfunction]
pub fn resolve_private_key_hex(private_key: String) -> PyResult<PyObject> {
    let address = resolve_private_key_hex_inner(&private_key).map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, json!({"address": address}))))
}

#[pyfunction]
#[pyo3(signature = (transaction_json, private_key))]
pub fn sign_typed_transaction_json(
    transaction_json: String,
    private_key: String,
) -> PyResult<PyObject> {
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
#[pyo3(signature = (config_json, method))]
pub fn build_v4_official_calldata(config_json: String, method: String) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let calldata = match method.as_str() {
        "previewArbitrageBestAmount" => {
            let is_best_amount = config
                .get("isBestAmount")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            build_v4_preview_amount_calldata(&config, is_best_amount)
        }
        "previewBestPool" => build_v4_preview_pool_calldata(&config),
        "previewBestPoolDetailed" => build_v4_preview_pool_detailed_calldata(&config),
        "runArbitrageBestPoolsWithSelectedAmount" => build_v4_run_best_calldata(&config),
        other => Err(format!("unsupported official V4 method: {other}")),
    }
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, json!({"calldata": calldata}))))
}

#[pyfunction]
#[pyo3(signature = (config_json, rpc_url, arb_address, call_from=None, timeout_seconds=20))]
pub fn preview_v4_arb_best_amount(
    config_json: String,
    rpc_url: String,
    arb_address: String,
    call_from: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let is_best_amount = config
        .get("isBestAmount")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let result = preview_arbitrage_best_amount_inner(
        &config,
        &rpc_url,
        &arb_address,
        call_from.as_deref(),
        timeout_seconds,
        is_best_amount,
    )
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (config_json, rpc_url, arb_address, call_from=None, timeout_seconds=20))]
pub fn run_v4_arb_best_amount_ladder(
    config_json: String,
    rpc_url: String,
    arb_address: String,
    call_from: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let result = official_v4_adaptive_best_amount_single_route_inner(
        &config,
        &rpc_url,
        &arb_address,
        call_from.as_deref(),
        timeout_seconds,
    )
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

/// Run the cross-tick search with the generic best-amount ladder.
///
/// The seed is the single-tick best amount, which the caller passes in as
/// a raw amount. The kernel owns that value: it is the structural product
/// of the single-tick computation, not a value Python reconstructs.
#[pyfunction]
#[pyo3(signature = (config_json, single_tick_best_amount_raw, rpc_url, arb_address, call_from=None, timeout_seconds=20))]
pub fn run_v4_arb_best_amount_cross_tick(
    config_json: String,
    single_tick_best_amount_raw: String,
    rpc_url: String,
    arb_address: String,
    call_from: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let seed = parse_big_uint(&Value::String(single_tick_best_amount_raw)).map_err(as_py_error)?;
    let result = official_v4_cross_tick_best_amount_inner(
        &config,
        &rpc_url,
        &arb_address,
        call_from.as_deref(),
        timeout_seconds,
        &seed,
    )
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (config_json, rpc_url, arb_address, call_from=None, timeout_seconds=20))]
pub fn preview_v4_best_pool(
    config_json: String,
    rpc_url: String,
    arb_address: String,
    call_from: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let result = preview_best_pool_inner(
        &config,
        &rpc_url,
        &arb_address,
        call_from.as_deref(),
        timeout_seconds,
    )
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (config_json, rpc_url, arb_address, call_from=None, timeout_seconds=20))]
pub fn preview_v4_best_pool_detailed(
    config_json: String,
    rpc_url: String,
    arb_address: String,
    call_from: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let result = preview_best_pool_detailed_inner(
        &config,
        &rpc_url,
        &arb_address,
        call_from.as_deref(),
        timeout_seconds,
    )
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (config_json, rpc_url, arb_address, call_from=None, timeout_seconds=20))]
pub fn run_v4_preview_and_ladder(
    config_json: String,
    rpc_url: String,
    arb_address: String,
    call_from: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let result = official_v4_preview_and_ladder_inner(
        &config,
        &rpc_url,
        &arb_address,
        call_from.as_deref(),
        timeout_seconds,
    )
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[pyfunction]
#[pyo3(signature = (config_json, selected_amount_raw, min_profit_raw, rpc_url, arb_address, private_key, chain_id, relay_urls=None, timeout_seconds=30))]
pub fn broadcast_v4_best_pools(
    config_json: String,
    selected_amount_raw: String,
    min_profit_raw: String,
    rpc_url: String,
    arb_address: String,
    private_key: String,
    chain_id: u64,
    relay_urls: Option<String>,
    timeout_seconds: u64,
) -> PyResult<PyObject> {
    let config = parse_json(&config_json, "config")?;
    let selected_amount =
        parse_big_uint(&serde_json::Value::String(selected_amount_raw)).map_err(as_py_error)?;
    let min_profit =
        parse_big_uint(&serde_json::Value::String(min_profit_raw)).map_err(as_py_error)?;
    let result = broadcast_v4_best_pools_inner(
        &config,
        &selected_amount,
        &min_profit,
        &rpc_url,
        &arb_address,
        &private_key,
        chain_id,
        relay_urls.as_deref(),
        timeout_seconds,
    )
    .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
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
    let result = submit_v4_route_inner(&config, &raw_transaction, relay_urls.as_deref())
        .map_err(as_py_error)?;
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
    let result =
        load_v4_receipt_inner(&rpc_urls, &tx_hash, wait, timeout_seconds).map_err(|error| {
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
    let result = get_v4_pending_nonce_inner(&rpc_url, &signer_address, timeout_seconds)
        .map_err(as_py_error)?;
    Python::with_gil(|py| Ok(json_result_to_py(py, result)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_single_tick_profit_below_the_usdg_threshold_starts_the_cross_tick_search() {
        // The borrow token has eighteen decimals and is worth 1_000_000 raw
        // USDG (one USDG) per whole token, so one whole token of profit is
        // worth one USDG, which is below the ten USDG threshold. The
        // cross-tick capacity is larger than the single-tick optimum, so
        // the seed is the optimum itself and is **not** clamped.
        let config = json!({
            "crossTickReviewThresholdUsdgRaw": "0x989680",
            "borrowTokenUsdgValueRaw": "0xf4240",
            "borrowTokenDecimals": 18,
            "crossTickCapacityRaw": "0xde0b6b3a764000000",
        });
        let seed = BigUint::from(7u32) * BigUint::from(10u32).pow(18);
        let verdict = cross_tick_initialization(
            &config,
            &seed,
            &BigUint::from(10u32).pow(18),
        );
        assert_eq!(verdict["started"], json!(true));
        assert_eq!(verdict["seedAmountRaw"], json!(format!("0x{:x}", seed)));
        assert_eq!(
            verdict["singleTickProfitUsdgRaw"],
            json!(format!("0x{:x}", BigUint::from(1_000_000u64)))
        );
        // The seed basis states where the search starts; the threshold
        // basis states how the threshold is denominated. Neither may
        // overwrite the other.
        assert_eq!(verdict["basis"], json!("single_tick_best_amount"));
        assert_eq!(
            verdict["thresholdBasis"],
            json!("usdg_quote_asset_not_usd")
        );
    }

    #[test]
    fn a_zero_seed_never_starts_the_cross_tick_search() {
        let config = json!({
            "crossTickReviewThresholdUsdgRaw": "0x989680",
            "borrowTokenUsdgValueRaw": "0xf4240",
            "borrowTokenDecimals": 18,
            "crossTickCapacityRaw": "0xde0b6b3a7640000",
        });
        let verdict = cross_tick_initialization(
            &config,
            &BigUint::zero(),
            &BigUint::zero(),
        );
        assert_eq!(verdict["started"], json!(false));
        assert_eq!(verdict["reason"], json!("single_tick_best_amount_zero"));
    }

    #[test]
    fn a_route_without_the_usdg_valuation_never_starts_the_cross_tick_search() {
        // The cross-tick mode is a supplement. A route that carries no
        // valuation must never lose its measured single-tick result.
        let config = json!({
            "crossTickReviewThresholdUsdgRaw": "0x989680",
            "borrowTokenDecimals": 18,
            "crossTickCapacityRaw": "0xde0b6b3a7640000",
        });
        let verdict = cross_tick_initialization(
            &config,
            &BigUint::from(10u32).pow(18),
            &BigUint::from(10u32).pow(18),
        );
        assert_eq!(verdict["started"], json!(false));
        assert_eq!(verdict["reason"], json!("cross_tick_inputs_unavailable"));
    }

    #[test]
    fn the_v4_quote_words_decode_keeps_the_amount_and_the_gas_apart() {
        // Golden vector from a live `eth_call` against the deployed quoter:
        // word 0 = 0x47c8f (294031), word 1 = 0xa764 (42852).
        let raw = format!("{:064x}{:064x}", 294031u64, 42852u64);
        let (amount_out, gas_estimate) = decode_v4_quote_words(&raw).expect("decodes");
        assert_eq!(amount_out, BigUint::from(294031u64));
        assert_eq!(gas_estimate, BigUint::from(42852u64));
    }

    #[test]
    fn a_short_v4_quote_answer_is_rejected() {
        let error = decode_v4_quote_words("00").expect_err("short answer");
        assert_eq!(error, "quote eth_call returned a short result");
    }

    #[test]
    fn big_integer_parse_uses_hex_prefix_and_decimal_otherwise() {
        assert_eq!(
            parse_big_uint(&json!("1000000000000000000")).unwrap(),
            BigUint::from(10u32).pow(18)
        );
        assert_eq!(
            parse_big_uint(&json!("0x1000000000000000000")).unwrap(),
            BigUint::from(2u8).pow(72)
        );
        assert_eq!(
            parse_big_uint(&json!("46630")).unwrap(),
            BigUint::from(46630u32)
        );
        assert!(parse_big_uint(&json!("not-a-number")).is_err());
    }

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
        assert_eq!(
            rlp_encode_integer(&BigUint::from(46_630u32)),
            vec![0x82, 0xb6, 0x26]
        );
        assert_eq!(
            rlp_encode_list(&[vec![0x80], vec![0x80]]),
            vec![0xc2, 0x80, 0x80]
        );
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
        assert_eq!(
            signed["hash"].as_str().unwrap(),
            "0x6dff37dd1a996412e30723462231d2a67bc7d66702b498aa739746797c4302a3"
        );
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
        assert_eq!(
            signed["hash"].as_str().unwrap(),
            "0x94f30669049e68cf827b37b07f41aad94bed8230ef9ad54db4128048b85c833e"
        );
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
        // The deployed quoter takes the nested struct ABI. The flat form has
        // the selector `0x6501a3ce` and reverts with empty data, so the
        // golden vector below is the nested one produced by `eth_abi`.
        assert_eq!(
            build_v4_quote_calldata_inner(&request).unwrap(),
            "0xaa9d21cb0000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000000000000000000000000000000011111111111111111111111111111111111111110000000000000000000000000000000000000000000000000000000000000bb8000000000000000000000000000000000000000000000000000000000000003c0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000f424000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000000003abcdef0000000000000000000000000000000000000000000000000000000000"
        );
    }

    #[test]
    fn relay_parsing_accepts_name_url_and_bare_url() {
        let relays = configured_relay_urls(Some("alpha=https://a.example, https://b.example"));
        assert_eq!(
            relays,
            vec![
                ("alpha".to_string(), "https://a.example".to_string()),
                ("private_2".to_string(), "https://b.example".to_string()),
            ]
        );
    }

    #[test]
    fn rpc_error_text_is_redacted() {
        let text =
            "eth_sendRawTransaction request failed at https://user:pass@relay.example:8545/path";
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
        assert!(
            calldata.contains("ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc4")
        );
    }

    #[test]
    fn int256_word_decodes_negative_and_positive_twos_complement() {
        let one_wei_below_one_ether = BigInt::from(999_999_999_999_999_999i64);
        let decoded = decode_i256_word(
            &format!(
                "0x{:0>64x}{:0>64x}",
                one_wei_below_one_ether.magnitude(),
                BigUint::zero()
            ),
            0,
        )
        .unwrap();
        assert_eq!(decoded, one_wei_below_one_ether);

        let negative_one_wei = BigInt::from(-1i64);
        let negative_hex = format!("{:0>64x}", negative_one_wei.magnitude());
        let negative_hex = format!(
            "{:0>64x}{negative_hex}",
            BigUint::from(2u8).pow(256) - BigUint::from(1u8)
        );
        let decoded = decode_i256_word(&format!("0x{negative_hex}"), 0).unwrap();
        assert_eq!(decoded, negative_one_wei);
        assert_eq!(signed_raw(&decoded), "-0x1");
    }

    #[test]
    fn arbitrage_not_profitable_revert_decodes_received_and_required() {
        // The selector is pinned to the value the deployed contract uses.
        // Keccak-256 and SHA3-256 are different functions, so a selector
        // computed with the wrong hash would silently never match and every
        // measured loss would stay undecoded.
        assert_eq!(
            hex_encode(function_selector("ArbitrageNotProfitable(uint256,uint256)")),
            "16301234"
        );
        assert_eq!(hex_encode(function_selector("NoProfitableRoute()")), "4f422847");
        let revert_hex = format!(
            "0x{}00000000000000000000000000000000000000000000000000000000000003e800000000000000000000000000000000000000000000000000000000000004b0",
            hex_encode(function_selector("ArbitrageNotProfitable(uint256,uint256)"))
        );
        let (received, required) = arbitrage_not_profitable_values(Some(&revert_hex)).unwrap();
        assert_eq!(received, BigUint::from(1000u32));
        assert_eq!(required, BigUint::from(1200u32));
    }

    #[test]
    fn detailed_unavailable_fallback_stays_no_profit_without_numeric_values() {
        let detailed = preview_best_pool_detailed_unavailable("execution reverted", "0x");
        assert_eq!(detailed["ok"], json!(false));
        assert_eq!(detailed["numericValuesUnavailable"], json!(true));
        assert_eq!(
            detailed["error"],
            json!("previewBestPoolDetailed unavailable")
        );
        assert_eq!(
            detailed["fallback"],
            json!("not_profitable_after_costs_without_numeric_values")
        );

        let payload = v4_no_profit_payload(
            "inner_single_call",
            json!({"ok": true}),
            Some(detailed),
            None,
            &BigUint::from(1u32),
            &BigUint::from(2u32),
            None,
        );
        assert_eq!(payload["status"], json!("quote_unavailable"));
        assert_eq!(payload["reviewFailureKind"], json!("quote_unavailable"));
        assert_eq!(payload["initialAmountRaw"], json!("0x1"));
        assert_eq!(payload["minProfitRaw"], json!("0x2"));
        assert_eq!(
            payload["error"],
            json!("previewBestPoolDetailed unavailable")
        );
        assert_eq!(
            payload["poolPreviewDetailed"]["error"],
            json!("previewBestPoolDetailed unavailable")
        );
    }

    #[test]
    fn preview_unavailable_revert_stays_no_profit_without_numeric_values() {
        let preview = preview_best_pool_unavailable(
            "execution reverted: arithmetic underflow or overflow",
            "0x",
        );
        assert_eq!(preview["ok"], json!(false));
        assert_eq!(preview["noProfitableRoute"], json!(false));
        assert_eq!(preview["numericValuesUnavailable"], json!(true));
        assert_eq!(preview["error"], json!("previewBestPool unavailable"));
        assert_eq!(
            preview["fallback"],
            json!("not_profitable_after_costs_without_numeric_values")
        );

        let payload = v4_no_profit_payload(
            "inner_single_call",
            preview,
            Some(preview_best_pool_detailed_unavailable(
                "execution reverted",
                "0x",
            )),
            None,
            &BigUint::from(0xde0b6b3a7640000u64),
            &BigUint::from(1u32),
            None,
        );
        assert_eq!(payload["status"], json!("quote_unavailable"));
        assert_eq!(payload["reviewFailureKind"], json!("quote_unavailable"));
        assert_eq!(payload["initialAmountRaw"], json!("0xde0b6b3a7640000"));
        assert_eq!(payload["minProfitRaw"], json!("0x1"));
        assert_eq!(
            payload["error"],
            json!("previewBestPool unavailable; previewBestPoolDetailed unavailable")
        );
        assert_eq!(
            payload["poolPreview"]["error"],
            json!("previewBestPool unavailable")
        );
        assert_eq!(
            payload["poolPreviewDetailed"]["error"],
            json!("previewBestPoolDetailed unavailable")
        );
    }

    const PREVIEW_AMOUNT_VECTOR: &str = concat!(
        "615bf656000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa00000000000000000000000000000000000000000000000000000000",
        "000000c000000000000000000000000000000000000000000000000000000000000002c00000000000000000000000000000000000000000000000000de0b6b3",
        "a7640000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000001000000000000000000000000000000000000000000000000000000000000000100000000000000000000000011111111111111111111111111111111",
        "11111111000000000000000000000000222222222222222222222222222222222222222200000000000000000000000000000000000000000000000000000000",
        "00000bb8000000000000000000000000000000000000000000000000000000000000003c00000000000000000000000033333333333333333333333333333333",
        "33333333000000000000000000000000444444444444444444444444444444444444444400000000000000000000000055555555555555555555555555555555",
        "5555555500000000000000000000000000000000000000000000000000000000000001f400000000000000000000000000000000000000000000000000000000",
        "00000078000000000000000000000000333333333333333333333333333333333333333300000000000000000000000011111111111111111111111111111111",
        "11111111000000000000000000000000222222222222222222222222222222222222222200000000000000000000000000000000000000000000000000000000",
        "00000bb8000000000000000000000000000000000000000000000000000000000000003c00000000000000000000000033333333333333333333333333333333",
        "33333333000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000",
        "00000001000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000001",
    );

    const PREVIEW_POOL_VECTOR: &str = concat!(
        "c5e1d636000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa00000000000000000000000000000000000000000000000000000000",
        "000000e0000000000000000000000000000000000000000000000000000000000000024000000000000000000000000000000000000000000000000000000000",
        "000002a0000000000000000000000000000000000000000000000000000000000000040000000000000000000000000000000000000000000000000000000000",
        "00000460000000000000000000000000000000000000000000000000000000000000052000000000000000000000000000000000000000000000000000000000",
        "00000002000000000000000000000000111111111111111111111111111111111111111100000000000000000000000022222222222222222222222222222222",
        "222222220000000000000000000000000000000000000000000000000000000000000bb800000000000000000000000000000000000000000000000000000000",
        "0000003c000000000000000000000000333333333333333333333333333333333333333300000000000000000000000044444444444444444444444444444444",
        "44444444000000000000000000000000555555555555555555555555555555555555555500000000000000000000000000000000000000000000000000000000",
        "000001f4000000000000000000000000000000000000000000000000000000000000007800000000000000000000000033333333333333333333333333333333",
        "33333333000000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000",
        "00000001000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000002000000000000000000000000444444444444444444444444444444444444444400000000000000000000000055555555555555555555555555555555",
        "5555555500000000000000000000000000000000000000000000000000000000000001f400000000000000000000000000000000000000000000000000000000",
        "00000078000000000000000000000000333333333333333333333333333333333333333300000000000000000000000011111111111111111111111111111111",
        "11111111000000000000000000000000222222222222222222222222222222222222222200000000000000000000000000000000000000000000000000000000",
        "00000bb8000000000000000000000000000000000000000000000000000000000000003c00000000000000000000000033333333333333333333333333333333",
        "33333333000000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000",
        "00000001000000000000000000000000111111111111111111111111111111111111111100000000000000000000000022222222222222222222222222222222",
        "222222220000000000000000000000000000000000000000000000000000000000000bb800000000000000000000000000000000000000000000000000000000",
        "0000003c000000000000000000000000333333333333333333333333333333333333333300000000000000000000000000000000000000000000000000000000",
        "000000010000000000000000000000000000000000000000000000000000000000000001",
    );

    const RUN_BEST_VECTOR: &str = concat!(
        "4cd63f66000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa00000000000000000000000000000000000000000000000000000000",
        "00000120000000000000000000000000000000000000000000000000000000000000028000000000000000000000000000000000000000000000000000000000",
        "000002e0000000000000000000000000000000000000000000000000000000000000044000000000000000000000000000000000000000000000000000000000",
        "000004a0000000000000000000000000000000000000000000000000000000000000056000000000000000000000000000000000000000000000000000000000",
        "075bcd15000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000002000000000000000000000000111111111111111111111111111111111111111100000000000000000000000022222222222222222222222222222222",
        "222222220000000000000000000000000000000000000000000000000000000000000bb800000000000000000000000000000000000000000000000000000000",
        "0000003c000000000000000000000000333333333333333333333333333333333333333300000000000000000000000044444444444444444444444444444444",
        "44444444000000000000000000000000555555555555555555555555555555555555555500000000000000000000000000000000000000000000000000000000",
        "000001f4000000000000000000000000000000000000000000000000000000000000007800000000000000000000000033333333333333333333333333333333",
        "33333333000000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000",
        "00000001000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
        "00000002000000000000000000000000444444444444444444444444444444444444444400000000000000000000000055555555555555555555555555555555",
        "5555555500000000000000000000000000000000000000000000000000000000000001f400000000000000000000000000000000000000000000000000000000",
        "00000078000000000000000000000000333333333333333333333333333333333333333300000000000000000000000011111111111111111111111111111111",
        "11111111000000000000000000000000222222222222222222222222222222222222222200000000000000000000000000000000000000000000000000000000",
        "00000bb8000000000000000000000000000000000000000000000000000000000000003c00000000000000000000000033333333333333333333333333333333",
        "33333333000000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000",
        "00000000000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000",
        "00000001000000000000000000000000111111111111111111111111111111111111111100000000000000000000000022222222222222222222222222222222",
        "222222220000000000000000000000000000000000000000000000000000000000000bb800000000000000000000000000000000000000000000000000000000",
        "0000003c000000000000000000000000333333333333333333333333333333333333333300000000000000000000000000000000000000000000000000000000",
        "000000010000000000000000000000000000000000000000000000000000000000000001",
    );

    fn sample_pool_key(currency0: &str, currency1: &str, fee: u64, tick_spacing: i64) -> Value {
        json!({
            "currency0": currency0,
            "currency1": currency1,
            "fee": fee,
            "tickSpacing": tick_spacing,
            "hooks": "0x3333333333333333333333333333333333333333",
        })
    }

    #[test]
    fn official_v4_preview_amount_calldata_matches_py_vector() {
        let pool1 = sample_pool_key(
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            3000,
            60,
        );
        let pool2 = sample_pool_key(
            "0x4444444444444444444444444444444444444444",
            "0x5555555555555555555555555555555555555555",
            500,
            120,
        );
        let config = json!({
            "token": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "keysets": [[pool1, pool2, pool1]],
            "directionsets": [[true, false, true]],
            "initialAmountRaw": "0xde0b6b3a7640000",
            "minProfitRaw": "0",
            "isBestAmount": true,
        });
        assert_eq!(
            build_v4_preview_amount_calldata(&config, true).unwrap(),
            format!("0x{PREVIEW_AMOUNT_VECTOR}")
        );
    }

    #[test]
    fn official_v4_preview_pool_calldata_matches_py_vector() {
        let pool1 = sample_pool_key(
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            3000,
            60,
        );
        let pool2 = sample_pool_key(
            "0x4444444444444444444444444444444444444444",
            "0x5555555555555555555555555555555555555555",
            500,
            120,
        );
        let config = json!({
            "token": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "entryKeys": [pool1, pool2],
            "entryDirections": [true, false],
            "bridgeKeys": [pool2, pool1],
            "bridgeDirections": [false, true],
            "exitKeys": [pool1],
            "exitDirections": [true],
        });
        assert_eq!(
            build_v4_preview_pool_calldata(&config).unwrap(),
            format!("0x{PREVIEW_POOL_VECTOR}")
        );
    }

    #[test]
    fn official_v4_run_best_calldata_matches_py_vector() {
        let pool1 = sample_pool_key(
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            3000,
            60,
        );
        let pool2 = sample_pool_key(
            "0x4444444444444444444444444444444444444444",
            "0x5555555555555555555555555555555555555555",
            500,
            120,
        );
        let config = json!({
            "token": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "entryKeys": [pool1, pool2],
            "entryDirections": [true, false],
            "bridgeKeys": [pool2, pool1],
            "bridgeDirections": [false, true],
            "exitKeys": [pool1],
            "exitDirections": [true],
            "selectedAmountRaw": 123456789u64,
            "minProfitRaw": "0",
        });
        assert_eq!(
            build_v4_run_best_calldata(&config).unwrap(),
            format!("0x{RUN_BEST_VECTOR}")
        );
    }

    #[test]
    fn v4_flash_batch_calldata_matches_ethers_vector() {
        let request = json!({
            "token": "0x1111111111111111111111111111111111111111",
            "amount": 1000,
            "swapTargets": [],
            "swapCalldatas": [],
            "finalToken": "0x1111111111111111111111111111111111111111",
            "minProfit": 7,
            "gasReserve": 0
        });
        assert_eq!(
            build_v4_flash_batch_calldata(&[request]).unwrap(),
            "0x5f957efb000000000000000000000000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000020000000000000000000000000111111111111111111111111111111111111111100000000000000000000000000000000000000000000000000000000000003e800000000000000000000000000000000000000000000000000000000000000e0000000000000000000000000000000000000000000000000000000000000010000000000000000000000000011111111111111111111111111111111111111110000000000000000000000000000000000000000000000000000000000000007000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        );
    }

    #[test]
    fn v4_flash_batch_calldata_carries_the_reviewed_minimum_output() {
        // The reviewed minimum output of the route travels in the request's
        // `gasReserve` field, which the contract reads as the route's output
        // floor. The encoded word must therefore carry the floor verbatim.
        let request = json!({
            "token": "0x1111111111111111111111111111111111111111",
            "amount": 1000,
            "swapTargets": [],
            "swapCalldatas": [],
            "finalToken": "0x1111111111111111111111111111111111111111",
            "minProfit": 7,
            "gasReserve": 4242
        });
        let calldata = build_v4_flash_batch_calldata(&[request]).unwrap();
        let hex = calldata.trim_start_matches("0x");
        // The floor is the seventh word of the request tuple, right after the
        // minimum profit. It must be present verbatim.
        let floor_word = format!("{:064x}", 4242u64);
        assert!(
            hex.contains(&floor_word),
            "the reviewed minimum output must be encoded in the request"
        );
        // The word must sit directly after the minimum profit (7).
        let profit_word = format!("{:064x}", 7u64);
        let profit_at = hex.find(&profit_word).expect("minimum profit word");
        assert_eq!(
            profit_at + profit_word.len(),
            hex.find(&floor_word).unwrap()
        );
    }

    #[test]
    fn reviewed_route_builds_bounded_pool_manager_executor_request() {
        let token_a = "0x1111111111111111111111111111111111111111";
        let token_b = "0x2222222222222222222222222222222222222222";
        let token_c = "0x3333333333333333333333333333333333333333";
        let manager = "0x4444444444444444444444444444444444444444";
        let keys = vec![
            sample_pool_key(token_a, token_b, 3_000, 60),
            sample_pool_key(token_b, token_c, 500, 10),
            sample_pool_key(token_a, token_c, 1_000, 20),
        ];
        let route_payload = json!({
            "selectedKeys": keys,
            "selectedDirections": [true, true, false],
            "selectedAmountRaw": "0x64",
            "outTb": "0x5a",
            "outTa": "0x50",
            "outTz": "0x6e",
        });
        let route_config = json!({
            "token": token_a,
            "minProfitRaw": "0x05",
            "hopSqrtPriceLimits": ["0x10", "0x20", "0x30"],
        });
        let request =
            build_v4_flash_executor_request(&route_payload, &route_config, manager).unwrap();
        assert_eq!(request["token"], json!(token_a));
        assert_eq!(request["amount"], json!("0x64"));
        assert_eq!(request["finalToken"], json!(token_a));
        assert_eq!(request["minProfit"], json!("0x5"));
        assert_eq!(request["minAmountOut"], json!("0x6e"));
        assert_eq!(request["swapTargets"], json!([manager, manager, manager]));
        let calldatas = request["swapCalldatas"].as_array().unwrap();
        assert_eq!(calldatas.len(), 3);
        let selector = as_hex(&function_selector(V4_POOL_MANAGER_SWAP_SIGNATURE));
        assert!(calldatas.iter().all(|value| {
            value
                .as_str()
                .map(|calldata| calldata.starts_with(&selector))
                .unwrap_or(false)
        }));
    }

    #[test]
    fn executor_request_rejects_a_non_closed_reviewed_route() {
        let route_payload = json!({
            "selectedKeys": [
                sample_pool_key(
                    "0x1111111111111111111111111111111111111111",
                    "0x2222222222222222222222222222222222222222",
                    3_000,
                    60,
                ),
                sample_pool_key(
                    "0x2222222222222222222222222222222222222222",
                    "0x3333333333333333333333333333333333333333",
                    500,
                    10,
                ),
                sample_pool_key(
                    "0x3333333333333333333333333333333333333333",
                    "0x4444444444444444444444444444444444444444",
                    1_000,
                    20,
                ),
            ],
            "selectedDirections": [true, true, true],
            "selectedAmountRaw": "0x64",
            "outTb": "0x5a",
            "outTa": "0x50",
            "outTz": "0x6e",
        });
        let route_config = json!({
            "token": "0x1111111111111111111111111111111111111111",
            "minProfitRaw": "0x05",
            "hopSqrtPriceLimits": ["0x10", "0x20", "0x30"],
        });
        assert!(build_v4_flash_executor_request(
            &route_payload,
            &route_config,
            "0x5555555555555555555555555555555555555555",
        )
        .is_err());
    }

    #[test]
    fn v4_flash_batch_calldata_enforces_bounds_and_parallel_arrays() {
        assert!(build_v4_flash_batch_calldata(&[]).is_err());
        let request = json!({
            "token": "0x1111111111111111111111111111111111111111",
            "amount": 1,
            "swapTargets": ["0x2222222222222222222222222222222222222222"],
            "swapCalldatas": [],
            "finalToken": "0x1111111111111111111111111111111111111111",
            "minProfit": 0,
            "gasReserve": 0
        });
        assert!(build_v4_flash_batch_calldata(&[request]).is_err());
    }

    #[test]
    fn v4_pool_state_slots_match_python_keccak_layout() {
        let (state_slot, liquidity_slot) = v4_pool_state_slots(
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        .unwrap();
        assert_eq!(
            hex_encode(state_slot),
            "f9f9cffb27053c60d6c9552447b6a4fc873604745eef4fed26c3644421245fcf"
        );
        // `liquidity` is the fourth word of the `Pool.State` struct, so the
        // liquidity slot is `state_slot + 3`. The `+1` word is
        // `feeGrowthGlobal0X128`, which is not a liquidity number.
        assert_eq!(
            hex_encode(liquidity_slot),
            "f9f9cffb27053c60d6c9552447b6a4fc873604745eef4fed26c3644421245fd2"
        );
    }

    #[test]
    fn v4_slot0_parse_matches_python_low_bits_layout() {
        let raw = "0x000000000bb800002affff851111111111111111111111111111111111111111";
        let (sqrt, tick, protocol_fee, lp_fee) = parse_v4_slot0_words(raw);
        assert_eq!(
            sqrt.to_string(),
            "97433442488726861213578988847752201310395502865"
        );
        assert_eq!(tick, -123);
        assert_eq!(protocol_fee, 42);
        assert_eq!(lp_fee, 3000);
    }

    #[test]
    fn v4_liquidity_word_parse_matches_python() {
        let raw = "0x0000000000000000000000000000000012121212121212121212121212121212";
        let liquidity = parse_v4_liquidity_word(raw);
        assert_eq!(
            liquidity.to_string(),
            "24019931782654479773885266406948344338"
        );
    }

    /// D4-e: the economic floor must short-circuit the ladder before a single
    /// on-chain probe is spent on a route that can never cover Gas.
    #[test]
    fn d4_economic_floor_short_circuits_the_ladder_without_probing() {
        let config = json!({
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "singleTickCapacityRaw": "0x3e8",
            "maxProbeAttempts": 24,
            "economicFloorRequiredAmountRaw": "0x5e69f40"
        });

        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the economic floor returns a verdict rather than an error");

        assert_eq!(
            result.get("status").and_then(Value::as_str),
            Some("economically_infeasible")
        );
        // No probe may have been attempted.
        let attempts = result
            .get("attempts")
            .and_then(Value::as_array)
            .expect("attempts is an array");
        assert!(attempts.is_empty(), "the economic floor must not probe");
        assert_eq!(
            result.get("attemptedCount").and_then(Value::as_str),
            Some("0x0")
        );
        assert_eq!(
            result.get("error").and_then(Value::as_str),
            Some("dust_unprofitable")
        );
        assert_eq!(
            result.get("ladderBlockedReason").and_then(Value::as_str),
            Some("capacity_cannot_cover_gas")
        );
    }

    /// D4-e: a route that is NOT blocked must not be short-circuited. The
    /// ladder then fails normally (the unreachable RPC makes every probe
    /// fail), which proves the gate only fires on a real block.
    #[test]
    fn d4_economic_floor_does_not_block_a_feasible_route() {
        let config = json!({
            "initialAmountRaw": "0x1",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAmountRaw": "0x2",
            "maxProbeAttempts": 4,
            "economicFloorGate": {
                "verdict": "pass",
                "blocked": false,
                "reason": null
            }
        });

        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        );

        // The call may fail because the RPC is unreachable, but it must NOT
        // report the economic short-circuit.
        if let Ok(payload) = result {
            assert_ne!(
                payload.get("status").and_then(Value::as_str),
                Some("economically_infeasible")
            );
        }
    }

    #[test]
    fn p0_8_missing_single_tick_capacity_blocks_without_probing() {
        let config = json!({
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAttempts": 4
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("missing capacity returns a fail-closed verdict");
        assert_eq!(
            result.get("status").and_then(Value::as_str),
            Some("single_tick_capacity_unavailable")
        );
        assert_eq!(
            result.get("attemptedCount").and_then(Value::as_str),
            Some("0x0")
        );
        assert!(result
            .get("attempts")
            .and_then(Value::as_array)
            .is_some_and(Vec::is_empty));
    }

    #[test]
    fn p0_8_initial_probe_is_clamped_to_the_single_tick_capacity() {
        let config = json!({
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAmountRaw": "0x32",
            "maxProbeAttempts": 4
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the failed preflight still reports the bounded amount");
        assert_eq!(
            result
                .get("requestedInitialAmountRaw")
                .and_then(Value::as_str),
            Some("0x64")
        );
        assert_eq!(
            result.get("initialAmountRaw").and_then(Value::as_str),
            Some("0x32")
        );
        assert_eq!(
            result.get("singleTickCapacityRaw").and_then(Value::as_str),
            Some("0x32")
        );
    }

    /// The folded single-tick capacity is the authoritative bound.
    ///
    /// The generic probe maximum is not a single-tick bound: it can be
    /// larger than the tick range allows. When both are present the fold
    /// wins, so the ladder can never probe past the boundary the version
    /// forbids.
    #[test]
    fn p0_8_the_folded_capacity_wins_over_the_generic_probe_maximum() {
        let config = json!({
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAmountRaw": "0x1000",
            "singleTickCapacityRaw": "0x32",
            "maxProbeAttempts": 4
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the failed preflight still reports the bounded amount");
        assert_eq!(
            result.get("singleTickCapacityRaw").and_then(Value::as_str),
            Some("0x32")
        );
        assert_eq!(
            result
                .get("singleTickCapacityBasis")
                .and_then(Value::as_str),
            Some("folded_single_tick_capacity")
        );
        assert_eq!(
            result.get("initialAmountRaw").and_then(Value::as_str),
            Some("0x32")
        );
    }

    /// The ladder searches **inside** the domain the route can be executed at.
    ///
    /// The folded single-tick capacity is that domain: the review rejects a
    /// final input above it, so a size above the capacity can never be
    /// broadcast. The search ceiling is therefore the published capacity, and
    /// the generic probe maximum only narrows it.
    #[test]
    fn p0_9_the_search_ceiling_is_the_published_capacity() {
        let config = json!({
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAmountRaw": "0x1000",
            "singleTickCapacityRaw": "0x32",
            "maxProbeAttempts": 4
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the failed preflight still reports the bounded amount");
        assert_eq!(
            result.get("searchCeilingRaw").and_then(Value::as_str),
            Some("0x32")
        );
        assert_eq!(
            result.get("searchCeilingBasis").and_then(Value::as_str),
            Some("folded_single_tick_capacity")
        );
    }

    /// A route that publishes no generic probe maximum still searches inside
    /// the published capacity: the ceiling is never the absence of a bound.
    #[test]
    fn p0_9_the_search_ceiling_is_the_capacity_without_a_generic_probe_maximum() {
        let config = json!({
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "singleTickCapacityRaw": "0x32",
            "maxProbeAttempts": 4
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the failed preflight still reports the bounded amount");
        assert_eq!(
            result.get("searchCeilingRaw").and_then(Value::as_str),
            Some("0x32")
        );
    }

    /// The generic probe maximum narrows the ceiling but never widens it.
    #[test]
    fn p0_9_the_generic_probe_maximum_narrows_the_search_ceiling() {
        let config = json!({
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAmountRaw": "0x32",
            "singleTickCapacityRaw": "0x1000",
            "maxProbeAttempts": 4
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the failed preflight still reports the bounded amount");
        assert_eq!(
            result.get("searchCeilingRaw").and_then(Value::as_str),
            Some("0x32")
        );
    }

    /// The regression of the live book: the expansion of the ladder must not
    /// leave the published capacity.
    ///
    /// The route that was blocked by `final_input_exceeds_single_tick_capacity`
    /// started at 1_015_283_473_444_041 — exactly half of the folded capacity
    /// 2_030_566_946_888_083 — and the ladder doubled its way to
    /// 4_061_133_893_776_164, which is twice the capacity. The size it picked
    /// was outside the bound the review enforces, so the route could never be
    /// broadcast.
    #[test]
    fn p0_9_the_expansion_never_leaves_the_published_capacity() {
        let capacity = BigUint::from(2_030_566_946_888_083u64);
        let ceiling = derive_search_ceiling(&capacity, None);
        let mut candidate = BigUint::from(1_015_283_473_444_041u64);
        for _ in 0..8 {
            candidate = bounded_double(&candidate, Some(&ceiling));
            assert!(
                candidate <= capacity,
                "the ladder walked past the published capacity: {candidate}"
            );
        }
        // The size the live ladder selected is outside the ceiling, so the
        // ceiling is what stops it.
        let live_selection = BigUint::from(4_061_133_893_776_164u64);
        assert!(live_selection > capacity);
    }

    /// A caller that has not published the fold yet keeps working through
    /// the compatibility fallback, and the evidence says so.
    #[test]
    fn p0_8_the_generic_probe_maximum_is_reported_as_a_fallback() {
        let config = json!({
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAmountRaw": "0x32",
            "maxProbeAttempts": 4
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the failed preflight still reports the bounded amount");
        assert_eq!(
            result
                .get("singleTickCapacityBasis")
                .and_then(Value::as_str),
            Some("max_probe_amount_fallback")
        );
    }

    /// D4-b: the bounded helpers used by the ladder must keep the upward
    /// search inside the configured ceiling and never collapse to zero.
    #[test]
    fn d4_bounded_double_stays_within_the_probe_ceiling() {
        let max_amount = BigUint::from(1000u32);
        assert_eq!(
            bounded_double(&BigUint::from(400u32), Some(&max_amount)),
            BigUint::from(800u32)
        );
        // A doubled amount beyond the ceiling is clamped, never exceeded.
        assert_eq!(
            bounded_double(&BigUint::from(800u32), Some(&max_amount)),
            max_amount
        );
    }

    /// D4-b: the upward search must be able to reach a size above the start,
    /// which is exactly what the old downward-only failure branch could not
    /// do. The bounded helpers make that reachable while staying inside the
    /// probe window.
    #[test]
    fn d4_upward_search_can_reach_sizes_above_the_start() {
        let start = BigUint::from(1000u32);
        let max_probe = BigUint::from(64_000u32);
        let mut upward = bounded_double(&start, Some(&max_probe));
        let mut seen_above_start = false;
        while upward > start && upward <= max_probe {
            if upward > start {
                seen_above_start = true;
                break;
            }
            let next = bounded_double(&upward, Some(&max_probe));
            if next <= upward {
                break;
            }
            upward = next;
        }
        assert!(
            seen_above_start,
            "the upward search must reach above the start"
        );
    }

    /// The no-profit payload must carry the **real** probe ladder when the
    /// ladder ran. The reviewer treats a ladder with fewer than two probe
    /// rows as incomplete, so the synthetic single probe must never
    /// override a ladder that actually measured several sizes.
    #[test]
    fn the_no_profit_payload_prefers_the_measured_ladder_attempts() {
        let ladder = json!({
            "status": "not_profitable_after_costs",
            "attemptedCount": "0x3",
            "attempts": [
                {"amountRaw": "0x1", "ok": true, "profitable": false, "profitRaw": "-0x2"},
                {"amountRaw": "0x2", "ok": true, "profitable": false, "profitRaw": "-0x1"},
                {"amountRaw": "0x4", "ok": true, "profitable": false, "profitRaw": "-0x1"},
            ],
        });
        let payload = v4_no_profit_payload(
            "inner_single_call",
            json!({"ok": false, "error": "NoProfitableRoute"}),
            None,
            None,
            &BigUint::from(1u32),
            &BigUint::from(1u32),
            Some(ladder),
        );
        let attempts = payload["attempts"].as_array().expect("attempts array");
        assert_eq!(attempts.len(), 3, "the measured ladder must win");
        assert_eq!(payload["attemptedCount"], json!("0x3"));
        assert_eq!(payload["ladderStatus"], json!("not_profitable_after_costs"));
        assert_eq!(attempts[0]["amountRaw"], json!("0x1"));
    }

    /// A ladder that ran and priced every size it tried is a **measured**
    /// result, not a missing measurement. The payload must say so, because
    /// the reviewer otherwise withdraws the best amount and the orchestrator
    /// chases a review repair for a measurement that was never broken.
    #[test]
    fn the_no_profit_payload_reports_a_measured_ladder_as_confirmed() {
        let ladder = json!({
            "status": "not_profitable_after_costs",
            "attemptedCount": "0x3",
            "attempts": [
                {"amountRaw": "0x1", "ok": true, "profitable": false, "profitRaw": "-0x2"},
                {"amountRaw": "0x2", "ok": true, "profitable": false, "profitRaw": "-0x1"},
                {"amountRaw": "0x4", "ok": true, "profitable": false, "profitRaw": "-0x1"},
            ],
        });
        let payload = v4_no_profit_payload(
            "inner_single_call",
            json!({"ok": false, "error": "NoProfitableRoute"}),
            None,
            None,
            &BigUint::from(1u32),
            &BigUint::from(1u32),
            Some(ladder),
        );
        assert_eq!(
            payload["bestAmountMissing"], json!(false),
            "a measured ladder must never be reported as a missing amount"
        );
        assert_eq!(payload["bestAmountMeasured"], json!(true));
        assert_eq!(payload["bestAmountConfirmed"], json!(true));
        assert_eq!(payload["bestAmount"], json!("0x0"));
        assert_eq!(
            payload["bestAmountSelectionBasis"],
            json!("native_rust_ladder_no_profitable_size_measured")
        );
        assert!(payload["bestAmountZeroReason"]
            .as_str()
            .expect("zero reason")
            .contains("3 size(s)"));
        assert_eq!(payload["reviewFailureKind"], json!("no_profitable_route"));
    }

    /// The kernel's verdict about the **cross-tick** search must reach the
    /// payload.  The no-profit path used to drop it, so a route whose single
    /// tick paid at no size looked like a route whose cross-tick search never
    /// existed, and a reader could not tell "the cross tick found nothing"
    /// apart from "the cross tick never ran".
    #[test]
    fn the_no_profit_payload_keeps_the_cross_tick_verdict_of_the_ladder() {
        let ladder = json!({
            "status": "not_profitable_after_costs",
            "attemptedCount": "0x3",
            "searchCeilingRaw": "0x1bc16d674ec80000",
            "searchCeilingBasis": "folded_single_tick_capacity",
            "crossTickReview": {
                "started": false,
                "reason": "single_tick_best_amount_zero",
                "singleTickBestAmountRaw": "0x0",
                "singleTickBestProfitRaw": "0x0"
            },
            "attempts": [
                {"amountRaw": "0x1", "ok": true, "profitable": false, "profitRaw": "-0x2"},
                {"amountRaw": "0x2", "ok": true, "profitable": false, "profitRaw": "-0x1"},
            ],
        });
        let payload = v4_no_profit_payload(
            "external_ladder",
            json!({"ok": false, "error": "NoProfitableRoute"}),
            None,
            None,
            &BigUint::from(1u32),
            &BigUint::from(1u32),
            Some(ladder),
        );
        assert_eq!(payload["crossTickReview"]["started"], json!(false));
        assert_eq!(
            payload["crossTickReview"]["reason"],
            json!("single_tick_best_amount_zero"),
            "a single-tick best amount of zero never starts the cross tick"
        );
        assert_eq!(payload["searchCeilingRaw"], json!("0x1bc16d674ec80000"));
        assert_eq!(
            payload["searchCeilingBasis"],
            json!("folded_single_tick_capacity")
        );
    }

    /// The measured signed loss of the `ArbitrageNotProfitable` revert must
    /// reach the payload. Without it every review was filed as
    /// `data_incomplete`, because the Review Manager saw no signed PnL.
    #[test]
    fn the_no_profit_payload_publishes_the_measured_signed_loss() {
        // ArbitrageNotProfitable(received=100, required=130) -> loss of -30.
        let revert_data = format!(
            "0x16301234{:064x}{:064x}",
            100u128, 130u128
        );
        let run_review = json!({
            "ok": false,
            "executable": true,
            "noProfitableRoute": true,
            "error": "NoProfitableRoute",
            "revertData": revert_data,
            "revertSelector": "0x16301234",
        });
        let payload = v4_no_profit_payload(
            "inner_single_call",
            json!({"ok": false, "error": "NoProfitableRoute"}),
            None,
            Some(run_review),
            &BigUint::from(1u32),
            &BigUint::from(1u32),
            None,
        );
        assert_eq!(
            payload["reportedNetProfitRaw"], json!("-0x1e"),
            "the measured signed loss must be published"
        );
        assert!(payload["arbitrageNotProfitable"].is_object());
        assert_eq!(
            payload["runArbitrage"]["reportedNetProfitRaw"],
            json!("-0x1e"),
            "the nested run evidence must carry the loss as well"
        );
    }

    /// The ladder must publish the measured loss of the size it priced. The
    /// probes already carry it; dropping it at the ladder boundary is what
    /// left every review without a concrete PnL.
    #[test]
    fn the_measured_probe_loss_is_promoted_out_of_the_probe_rows() {
        let attempts = vec![
            json!({"amountRaw": "0x1", "ok": true, "profitable": false, "profitRaw": "-0x2"}),
            json!({"amountRaw": "0x2", "ok": true, "profitable": false, "profitRaw": "-0x1"}),
        ];
        assert_eq!(measured_probe_loss_raw(&attempts), json!("-0x1"));
        // A row without a measured profit contributes nothing.
        let without = vec![json!({"amountRaw": "0x1", "ok": true, "profitRaw": Value::Null})];
        assert!(measured_probe_loss_raw(&without).is_null());
        assert!(measured_probe_loss_raw(&[]).is_null());

        // The profitable path publishes the same measurement.  A route whose
        // pool preview succeeded but whose ladder found no profitable size
        // used to be reported as "the best amount is missing", even though
        // the ladder measured every size it tried.
        let mut payload = json!({
            "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
            "attempts": [
                {"amountRaw": "0x1", "ok": false, "profitable": false, "profitRaw": "-0x2"},
                {"amountRaw": "0x2", "ok": false, "profitable": false, "profitRaw": "-0x1"},
            ],
        });
        publish_ladder_best_amount(&mut payload);
        assert_eq!(payload["bestAmountMissing"], json!(false));
        assert_eq!(payload["bestAmountMeasured"], json!(true));
        assert_eq!(payload["bestAmountConfirmed"], json!(true));
        assert_eq!(payload["bestAmount"], json!("0x0"));
        assert_eq!(
            payload["bestAmountSelectionBasis"],
            json!("native_rust_ladder_no_profitable_size_measured")
        );

        // A profitable probe publishes its size.
        let mut winning = json!({
            "attempts": [
                {"amountRaw": "0x1", "ok": false, "profitable": false},
                {"amountRaw": "0x4", "ok": true, "profitable": true},
            ],
        });
        publish_ladder_best_amount(&mut winning);
        assert_eq!(winning["bestAmount"], json!("0x4"));
        assert_eq!(
            winning["bestAmountSelectionBasis"],
            json!("native_rust_ladder_profitable_probe")
        );

        // A ladder that never probed claims nothing.
        let mut blocked = json!({"attempts": [], "ladderBlocked": true});
        publish_ladder_best_amount(&mut blocked);
        assert!(blocked.get("bestAmountMeasured").is_none());
        assert!(blocked.get("bestAmount").is_none());
    }

    /// Without a ladder the payload still has to carry a concrete probe
    /// record. An empty ladder is read as "the measurement never ran",
    /// which is a different (and wrong) conclusion.
    #[test]
    fn the_no_profit_payload_falls_back_to_one_measured_probe() {
        let payload = v4_no_profit_payload(
            "inner_single_call",
            json!({"ok": false, "error": "NoProfitableRoute"}),
            None,
            None,
            &BigUint::from(7u32),
            &BigUint::from(1u32),
            None,
        );
        let attempts = payload["attempts"].as_array().expect("attempts array");
        assert_eq!(attempts.len(), 1);
        assert_eq!(attempts[0]["amountRaw"], json!("0x7"));
        assert_eq!(payload["attemptedCount"], json!("0x1"));
    }

    /// A route without a published capacity is **blocked** before it probes
    /// anything. The record must say that explicitly, so the orchestrator
    /// calls the closed-loop manager (which publishes the capacity) rather
    /// than the review manager (which cannot repair a missing capacity).
    #[test]
    fn a_route_without_capacity_is_marked_as_a_blocked_ladder() {
        let mut config = json!({
            "token": "0x0000000000000000000000000000000000000000",
            "keys": [],
            "directions": [],
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAttempts": 4,
        });
        config["singleTickCapacityRaw"] = Value::Null;
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the ladder reports the blocked route");
        assert_eq!(result["status"], json!("single_tick_capacity_unavailable"));
        assert_eq!(result["ladderBlocked"], json!(true));
        assert_eq!(
            result["ladderBlockedReason"],
            json!("single_tick_capacity_unavailable")
        );
        assert_eq!(result["repairTarget"], json!("closed_loop_manager"));
        assert_eq!(result["attemptedCount"], json!("0x0"));
        assert!(result["attempts"].as_array().is_some_and(Vec::is_empty));
    }

    #[test]
    fn a_zero_single_tick_best_amount_never_starts_the_cross_tick_search() {
        let config = json!({
            "token": "0x0000000000000000000000000000000000000000",
            "keys": [],
            "directions": [],
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "crossTickCapacityRaw": "0xde0b6b3a7640000",
        });
        let result = official_v4_cross_tick_best_amount_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
            &BigUint::zero(),
        )
        .expect("the cross-tick mode reports that it did not start");
        assert_eq!(result["status"], json!("cross_tick_not_started"));
        assert_eq!(result["error"], json!("single_tick_best_amount_zero"));
        assert_eq!(
            result["crossTickReview"]["reason"],
            json!("single_tick_best_amount_zero")
        );
        assert_eq!(result["attemptedCount"], json!("0x0"));
        assert!(result["attempts"].as_array().is_some_and(Vec::is_empty));
    }

    #[test]
    fn a_cross_tick_search_without_a_depth_ceiling_is_blocked() {
        let config = json!({
            "token": "0x0000000000000000000000000000000000000000",
            "keys": [],
            "directions": [],
            "initialAmountRaw": "0x64",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
        });
        let result = official_v4_cross_tick_best_amount_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
            &BigUint::from(100u32),
        )
        .expect("the cross-tick mode reports the blocked route");
        assert_eq!(result["status"], json!("cross_tick_capacity_unavailable"));
        assert_eq!(result["ladderBlocked"], json!(true));
        assert_eq!(result["repairTarget"], json!("closed_loop_manager"));
        assert_eq!(result["attemptedCount"], json!("0x0"));
    }

    #[test]
    fn the_cross_tick_search_starts_at_the_single_tick_best_amount() {
        // The seed is the structural product of the single-tick
        // computation. The search must start there, not at a constant, and
        // the ceiling is the depth capacity rather than the single-tick
        // physical ceiling.
        let config = json!({
            "token": "0x0000000000000000000000000000000000000000",
            "keys": [],
            "directions": [],
            "initialAmountRaw": "0x1",
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAttempts": 4,
            "singleTickCapacityRaw": "0x64",
            "crossTickCapacityRaw": "0xde0b6b3a7640000",
        });
        let seed = BigUint::from(7u32);
        let result = official_v4_cross_tick_best_amount_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
            &seed,
        )
        .expect("the cross-tick mode runs");
        assert_eq!(result["searchMode"], json!("cross_tick"));
        assert_eq!(result["crossTickSeedAmountRaw"], json!("0x7"));
        assert_eq!(
            result["crossTickSeedBasis"],
            json!("single_tick_best_amount")
        );
        assert_eq!(
            result["crossTickCapacityRaw"],
            json!("0xde0b6b3a7640000")
        );
        // The single-tick capacity is not the cross-tick ladder ceiling.
        assert!(result.get("singleTickCapacityRaw").is_none());
        assert_eq!(
            result["searchCapacityBasis"],
            json!("search_capacity_override")
        );
        assert_eq!(
            result["crossTickCapacityBasis"],
            json!("folded_depth_capacity")
        );
    }

    #[test]
    fn the_kernel_derives_the_ladder_start_when_the_caller_omits_it() {
        // The Python runtime is a pure consumer: it never computes a
        // trading number. When the caller omits the start, the kernel
        // derives it from the Manager-published single-tick capacity.
        let config = json!({
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "maxProbeAttempts": 4,
            "singleTickCapacityRaw": "0x64",
        });
        let result = official_v4_adaptive_best_amount_single_route_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect("the kernel derives its own start");
        assert_eq!(result["ladderStartBasis"], json!("single_tick_capacity_fraction"));
        assert_eq!(result["derivedInitialAmountRaw"], json!("0x32"));
        assert_eq!(result["initialAmountRaw"], json!("0x32"));
        // The absent start is published as JSON null, not as the string
        // "null".
        assert_eq!(result["requestedInitialAmountRaw"], json!(null));
    }

    #[test]
    fn the_preview_pool_path_does_not_require_the_caller_to_supply_an_initial_amount() {
        // Regression: the preview-pool path used to read
        // `config.initialAmountRaw` as a **required** field and failed with
        // "preview pool initialAmountRaw is required" for every route, because
        // the Python runtime is a pure consumer that never publishes a
        // trading number. The kernel now derives the start from the published
        // single-tick capacity, so a missing start is no longer a hard error.
        let config = json!({
            "token": "0x0000000000000000000000000000000000000001",
            "entryKeys": [{"poolId": "0x1"}],
            "entryDirections": [true],
            "bridgeKeys": [{"poolId": "0x2"}],
            "bridgeDirections": [true],
            "exitKeys": [{"poolId": "0x3"}],
            "exitDirections": [true],
            "minProfitRaw": "0x1",
            "minProbeAmountRaw": "0x1",
            "singleTickCapacityRaw": "0x64",
        });
        // The RPC endpoint is intentionally unreachable: the assertion is
        // about the **error kind**. A missing `initialAmountRaw` must never
        // surface as the configuration error any more.
        let error = official_v4_preview_and_ladder_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
        )
        .expect_err("the unreachable RPC still fails");
        assert!(
            !error.contains("initialAmountRaw is required"),
            "the preview-pool path must not require a caller-supplied start: {error}"
        );
    }

    #[test]
    fn a_cross_tick_seed_above_depth_capacity_is_blocked_without_clamping() {
        let config = json!({
            "crossTickCapacityRaw": "0x64",
        });
        let result = official_v4_cross_tick_best_amount_inner(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
            &BigUint::from(101u32),
        )
        .expect("an invalid seed is explicitly blocked");
        assert_eq!(result["status"], json!("cross_tick_seed_exceeds_capacity"));
        assert_eq!(result["crossTickSeedAmountRaw"], json!("0x65"));
        assert_eq!(result["crossTickCapacityRaw"], json!("0x64"));
        assert_eq!(result["attemptedCount"], json!("0x0"));
    }

    #[test]
    fn a_cross_tick_ladder_does_not_launch_another_cross_tick_ladder() {
        let config = json!({
            "searchMode": "cross_tick",
            "crossTickReviewThresholdUsdgRaw": "0x989680",
            "borrowTokenUsdgValueRaw": "0xf4240",
            "borrowTokenDecimals": 18,
            "crossTickCapacityRaw": "0xde0b6b3a764000000",
        });
        let verdict = cross_tick_review_with_search(
            &config,
            "http://127.0.0.1:1",
            "0x0000000000000000000000000000000000000001",
            None,
            1,
            &BigUint::from(7u32),
            &BigUint::from(1u32),
        );
        assert_eq!(verdict["started"], json!(true));
        assert_eq!(verdict["basis"], json!("single_tick_best_amount"));
        assert!(verdict.get("search").is_none());
        assert!(verdict.get("searchError").is_none());
    }
}
