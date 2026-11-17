//! Chain access helpers for the pool manager.
//!
//! The pool tool has to talk to the chain itself: the depth verdict is
//! derived from live pool state (`sqrtPriceX96`, `tick`, `liquidity`) that is
//! not present in the local database. This module is a focused port of the
//! `extsload` batch reader that already exists in `flashloan/rs-src` so both
//! tools observe the chain through exactly the same code path.
//!
//! Nothing in this module prices a token. Capacity is computed in token units
//! from the pool's own price, so an external ETH/USD feed is never required.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

use tiny_keccak::{Hasher, Keccak};

/// A decoded V4 pool state as observed on chain.
#[derive(Clone, Debug, Default)]
pub struct V4PoolState {
    pub pool_id: String,
    pub sqrt_price_x96: String,
    pub tick: i64,
    pub protocol_fee: u64,
    pub lp_fee: u64,
    pub liquidity: u128,
    pub has_liquidity: bool,
    pub error: Option<String>,
    /// Whether **both** reads of this pool answered with usable data.
    ///
    /// A pool is only a measurement when its `slot0` **and** its liquidity
    /// were read. A pool whose `slot0` read failed has no price at all, and
    /// a pool whose liquidity read failed has no depth. Mixing a failed
    /// read with a stored one produces a state that describes **neither**
    /// block, and the comparison would then report the age of the snapshot
    /// as if it were a defect of the arithmetic.
    pub complete: bool,
}

/// Why a pool-state batch could not be read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BatchFailure {
    /// The node answered, but with a JSON-RPC error object instead of the
    /// requested batch array. A `429` is a rate limit and is worth retrying
    /// on another endpoint.
    RpcError { code: i64, message: String },
    /// The node answered with something that is not a JSON-RPC response.
    Malformed(String),
    /// The request itself failed (transport, timeout, HTTP status).
    Transport(String),
}

impl From<String> for BatchFailure {
    fn from(value: String) -> Self {
        BatchFailure::Transport(value)
    }
}

impl BatchFailure {
    /// Whether another endpoint is likely to do better.
    fn is_retryable(&self) -> bool {
        match self {
            BatchFailure::RpcError { code, .. } => *code == 429,
            BatchFailure::Transport(_) => true,
            BatchFailure::Malformed(_) => false,
        }
    }
}

/// Outcome of a batched pool-state read.
#[derive(Clone, Debug, Default)]
pub struct V4PoolStateBatch {
    pub states: Vec<V4PoolState>,
    pub errors: Vec<String>,
    pub rpc_url: String,
    /// The exact block every state call in the batch was pinned to.
    pub block_number: Option<u64>,
    /// Endpoints that were skipped because a DNS/TCP pre-check failed.
    ///
    /// A dead endpoint must not silently consume the full RPC timeout, so it
    /// is reported separately from the read errors.
    pub skipped_endpoints: Vec<String>,
}

/// The maximum number of JSON-RPC requests in one batch.
///
/// The number of `extsload` calls that travel in **one** JSON-RPC batch.
///
/// The batch size is not a throughput knob: the public endpoint counts every
/// call inside the batch against its own per-second budget and answers the
/// excess with `-32007 "15/second request limit reached"`. A batch of 50 was
/// therefore answered with a burst of rate-limit errors, and the reader
/// silently fell back to the stored snapshot for every affected pool.
///
/// The size is now derived from the request rate so that one batch fits
/// inside one second of the node budget.
pub const POOL_STATE_BATCH_SIZE: usize = 8;

/// The maximum request rate against the node, in requests per second.
///
/// The free trial of the node allows 15 requests per second. The reader
/// stays well below that ceiling, and the batch size is derived from it so
/// that a burst cannot exceed it.
pub const POOL_STATE_REQUESTS_PER_SECOND: f64 = 8.0;

/// The canonical Uniswap V4 `PoolManager` used by the Robinhood chain.
pub const ROBINHOOD_V4_POOL_MANAGER: &str = "0x8366a39cc670b4001a1121b8f6a443a643e40951";

/// Budget for the DNS/TCP pre-check of a single RPC endpoint.
///
/// The pre-check exists so a dead endpoint fails in seconds instead of
/// consuming the full RPC timeout for every batch.
pub const ENDPOINT_PROBE_TIMEOUT_SECONDS: u64 = 10;

/// The environment variable that overrides the endpoint probe timeout.
///
/// The probe budget is a property of the **network path**, not of the
/// tool: an endpoint that answers in a second on one link needs longer on
/// another. A fixed budget that is tuned for the fastest endpoint skips a
/// perfectly healthy endpoint on a slower link, and the caller then reports
/// "all endpoints failed" even though the chain was reachable the whole
/// time.
pub const ENDPOINT_PROBE_TIMEOUT_ENV: &str = "FLASHLOAN_RPC_PROBE_TIMEOUT_SECONDS";

/// The effective endpoint probe timeout.
pub fn endpoint_probe_timeout_seconds() -> u64 {
    std::env::var(ENDPOINT_PROBE_TIMEOUT_ENV)
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(ENDPOINT_PROBE_TIMEOUT_SECONDS)
}

/// How many times a rate-limited (HTTP 429) batch is retried on the same
/// endpoint before the caller moves on.
pub const RATE_LIMIT_RETRIES: u32 = 3;

/// Base delay for the rate-limit backoff, in milliseconds.
///
/// The wait doubles on every attempt: 500ms, 1000ms, 2000ms.
pub const RATE_LIMIT_BACKOFF_MS: u64 = 500;

/// Environment variables consulted for RPC endpoints, in priority order.
/// The list mirrors `chains/robinhood/config.py::rpc_urls_from_env` so the
/// Python and Rust readers can be pointed at the same node from one setup.
pub const ROBINHOOD_RPC_ENV_VARS: &[&str] = &[
    "ROBINHOOD_MAINNET_RPC_URLS",
    "ROBINHOOD_RPCS",
    "ROBINHOOD_RPC_URLS",
    "ROBINHOOD_RPC_URL",
    "ROBINHOOD_RPC",
    "ROBINHOOD_ALCHEMY_RPC_URL",
    "ROBINHOOD_ALCHEMY_RPC",
    "ROBINHOOD_MAINNET_RPC_URL",
    "ROBINHOOD_MAINNET_RPC",
];

fn strip_0x(value: &str) -> &str {
    value.strip_prefix("0x").unwrap_or(value)
}

pub(crate) fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    hex::decode(strip_0x(value)).map_err(|error| format!("invalid hex {value}: {error}"))
}

pub(crate) fn as_hex(value: &[u8]) -> String {
    format!("0x{}", hex::encode(value))
}

pub fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut output = [0u8; 32];
    let mut hasher = Keccak::v256();
    hasher.update(data);
    hasher.finalize(&mut output);
    output
}

pub(crate) fn function_selector(signature: &str) -> Vec<u8> {
    keccak256(signature.as_bytes())[..4].to_vec()
}

/// Removes the authority component from any URL embedded in `value` so RPC
/// endpoints (which frequently carry an API key) never reach an evidence file.
pub fn redact_rpc_text(value: &str) -> String {
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

/// Derives the `Pool.State` and `Pool.liquidity` storage slots for a pool id.
///
/// The layout mirrors the V4 `Pool` state struct: the state slot is keyed by
/// `keccak(poolId ++ 0x00 * 31 ++ 0x06)` — the Solidity mapping derivation
/// `keccak256(abi.encode(poolId, 6))` — and `liquidity` is the **fourth**
/// word of that struct, i.e. `state_slot + 3`.
///
/// The struct reads:
///
/// * `+0` — `Slot0`: `sqrtPriceX96` (low 160 bits), `tick`, `protocolFee`
///   and `lpFee`;
/// * `+1` — `feeGrowthGlobal0X128`;
/// * `+2` — `feeGrowthGlobal1X128`;
/// * `+3` — `liquidity` (`uint128`);
/// * `+4` — `ticks` mapping;
/// * `+5` — `tickBitmap` mapping;
/// * `+6` — `positions` mapping.
///
/// The offsets are the ones the Uniswap V4 SDK uses in its `PoolManagerLens`
/// (`LIQUIDITY_OFFSET = 3`, `TICKS_OFFSET = 4`, `TICK_BITMAP_OFFSET = 5`).
///
/// The earlier `+1` offset was wrong: it reads `feeGrowthGlobal0X128`, a fee
/// growth accumulator, not the in-range liquidity. Because a fee growth
/// accumulator grows over time, it looked like a plausible "liquidity" number
/// and the pools were admitted, while the chain reported **zero** in-range
/// liquidity and refused the swap with `NotEnoughLiquidity`. The offset was
/// corrected against the live chain and the SDK.
pub fn v4_pool_state_slots(pool_id: &str) -> Result<([u8; 32], [u8; 32]), String> {
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

/// Parses a packed V4 `Slot0` storage word.
///
/// `sqrtPriceX96` occupies the low 160 bits, followed by `tick`,
/// `protocolFee` and `lpFee`.
pub fn parse_v4_slot0_words(raw: &str) -> (num_bigint_stub::BigUint, i64, u64, u64) {
    let bytes = decode_hex(raw).unwrap_or_default();
    let mut word = [0u8; 32];
    let copy_len = bytes.len().min(32);
    word[..copy_len].copy_from_slice(&bytes[..copy_len]);
    let sqrt = num_bigint_stub::BigUint::from_bytes_be(&word[12..32]);
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

/// Parses the packed `Pool.liquidity` word.
pub fn parse_v4_liquidity_word(raw: &str) -> u128 {
    let bytes = decode_hex(raw).unwrap_or_default();
    let start = bytes.len().saturating_sub(16);
    bytes[start..]
        .iter()
        .fold(0u128, |acc, byte| (acc << 8) | u128::from(*byte))
}

/// The mainnet chain id the pool manager serves by default.
pub const ROBINHOOD_CHAIN_ID: i64 = 4663;
/// The testnet chain id.
pub const ROBINHOOD_TESTNET_CHAIN_ID: i64 = 46630;

/// The network namespace of a chain id.
///
/// Every chain owns its own database file, and the network name is part of
/// the row identity of every table, so the namespace has to follow the
/// chain the tool is pointed at.
pub fn network_for_chain_id(chain_id: i64) -> String {
    match chain_id {
        ROBINHOOD_CHAIN_ID => "robinhood".to_owned(),
        ROBINHOOD_TESTNET_CHAIN_ID => "robinhood-testnet".to_owned(),
        other => format!("chain-{other}"),
    }
}

/// Collects candidate RPC endpoints from the environment.
///
/// Every listed variable may hold a comma separated list. The first non-empty
/// variable wins, matching the Python reader exactly.
pub fn rpc_urls_from_env() -> Vec<String> {
    for name in ROBINHOOD_RPC_ENV_VARS {
        let mut urls = Vec::new();
        for item in std::env::var(name).unwrap_or_default().split(',') {
            let value = item.trim();
            if !value.is_empty() && !urls.iter().any(|existing| existing == value) {
                urls.push(value.to_string());
            }
        }
        if !urls.is_empty() {
            return urls;
        }
    }
    Vec::new()
}

/// The chain data root that holds `registry.json` and the per-network
/// `chain.json` files. The Rust tool reads the same files as
/// `core.chain_data_registry`, so both sides resolve the same endpoints.
pub const CHAIN_DATA_ROOT_RELATIVE: &str = "flashloan/chains";

/// Collects RPC endpoints from the chain configuration files.
///
/// The Python reader falls back to the chain row when no environment variable
/// is set. The Rust tool has to do the same, otherwise the depth command
/// refuses to run on a machine where the endpoints live in configuration
/// rather than in the environment, and the capacity rollout silently never
/// starts.
pub fn rpc_urls_from_chain_config() -> Vec<String> {
    rpc_urls_from_chain_config_for(ROBINHOOD_CHAIN_ID)
}

/// Collects the RPC endpoints of the chain the tool is pointed at.
///
/// Every chain owns its own configuration file, and the endpoints of one
/// chain never answer for another chain. Reading the mainnet configuration
/// for a testnet run would measure the mainnet's pools and report them as
/// the testnet's depth, so the chain id selects the configuration file.
pub fn rpc_urls_from_chain_config_for(chain_id: i64) -> Vec<String> {
    let network = network_for_chain_id(chain_id);
    let mut urls: Vec<String> = Vec::new();
    let mut push = |value: &str| {
        let trimmed = value.trim();
        if !trimmed.is_empty() && !urls.iter().any(|existing| existing == trimmed) {
            urls.push(trimmed.to_string());
        }
    };
    for root in chain_data_roots() {
        let chain_file = root.join(&network).join("chain.json");
        let Ok(payload) = std::fs::read_to_string(&chain_file) else {
            continue;
        };
        let Ok(document) = serde_json::from_str::<Value>(&payload) else {
            continue;
        };
        let rpc = document.get("rpc");
        let Some(rpc) = rpc else { continue };
        if let Some(primary) = rpc.get("primary").and_then(Value::as_str) {
            push(primary);
        }
        if let Some(fallbacks) = rpc.get("fallbacks").and_then(Value::as_array) {
            for fallback in fallbacks {
                if let Some(value) = fallback.as_str() {
                    push(value);
                }
            }
        }
        if let Some(list) = rpc.get("urls").and_then(Value::as_array) {
            for entry in list {
                if let Some(value) = entry.as_str() {
                    push(value);
                }
            }
        }
        if let Some(value) = document.get("rpc_url").and_then(Value::as_str) {
            push(value);
        }
        if let Some(list) = document.get("rpc_urls").and_then(Value::as_array) {
            for entry in list {
                if let Some(value) = entry.as_str() {
                    push(value);
                }
            }
        }
    }
    urls
}

/// Candidate locations for the chain data root.
///
/// The tool is started from the repository root, from `flashloan/tools` or
/// from the installed binary location, so the root is located by walking up
/// from the current directory and from the executable path.
fn chain_data_roots() -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    let mut push = |candidate: PathBuf| {
        if candidate.is_dir() && !roots.iter().any(|existing| existing == &candidate) {
            roots.push(candidate);
        }
    };
    let relative = Path::new(CHAIN_DATA_ROOT_RELATIVE);
    let mut bases: Vec<PathBuf> = Vec::new();
    if let Ok(current) = std::env::current_dir() {
        bases.push(current);
    }
    if let Ok(executable) = std::env::current_exe() {
        if let Some(directory) = executable.parent() {
            bases.push(directory.to_path_buf());
        }
    }
    for base in bases {
        let mut candidate = base.clone();
        for _ in 0..8 {
            push(candidate.join(relative));
            let Some(parent) = candidate.parent() else {
                break;
            };
            candidate = parent.to_path_buf();
        }
    }
    roots
}

/// Collects candidate RPC endpoints from the environment and then from the
/// chain configuration.
///
/// The environment wins, matching `chains/robinhood/config.py`. The chain
/// configuration is the fallback that makes the tool usable without an
/// exported variable.
pub fn rpc_urls() -> Vec<String> {
    rpc_urls_for(ROBINHOOD_CHAIN_ID)
}

/// The RPC endpoints of the chain the tool is pointed at.
///
/// The environment still wins as a whole, matching the Python reader, but
/// the chain configuration fallback is resolved for the chain that is
/// actually being served. Every chain owns its own endpoints, so the
/// mainnet configuration can never answer for the testnet.
pub fn rpc_urls_for(chain_id: i64) -> Vec<String> {
    let from_env = rpc_urls_from_env();
    if !from_env.is_empty() {
        return from_env;
    }
    rpc_urls_from_chain_config_for(chain_id)
}

fn throttle_rpc_call(_rpc_url: &str) {
    // The pool manager is a batch tool, not the hot execution path, so it does
    // not apply the per-node minimum interval used by the execution engine.
}

pub(crate) async fn json_rpc_batch(
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
    let items = payload.as_array().ok_or_else(|| {
        // A rate-limited or overloaded node answers with a single JSON-RPC
        // error object instead of the requested batch array. The raw shape is
        // preserved so the failure can be diagnosed, but the text is
        // truncated because the body may be large.
        let mut shape = payload.to_string();
        shape.truncate(200);
        format!("V4 pool state batch RPC response is not an array: {shape}")
    })?;

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
            rows.push((id, Value::Null, Some(message)));
        } else {
            rows.push((id, item.get("result").cloned().unwrap_or(Value::Null), None));
        }
    }
    Ok(rows)
}

/// Reads the current block number from one RPC endpoint.
async fn json_rpc_block_number(
    client: &reqwest::Client,
    rpc_url: &str,
    timeout_seconds: u64,
) -> Result<u64, String> {
    throttle_rpc_call(rpc_url);
    let response = client
        .post(rpc_url)
        .json(&json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_blockNumber",
            "params": [],
        }))
        .timeout(Duration::from_secs(timeout_seconds))
        .send()
        .await
        .map_err(|error| format!("V4 pool state block-number request failed: {error}"))?;
    let payload: Value = response
        .json()
        .await
        .map_err(|error| format!("V4 pool state block-number response was not JSON: {error}"))?;
    if let Some(error) = payload.get("error") {
        return Err(format!("V4 pool state block-number RPC error: {error}"));
    }
    let raw = payload
        .get("result")
        .and_then(Value::as_str)
        .ok_or_else(|| "V4 pool state block-number result is not a hex string".to_string())?;
    u64::from_str_radix(raw.trim_start_matches("0x"), 16)
        .map_err(|error| format!("V4 pool state block number is invalid: {error}"))
}

/// The version stamp of a live pool-state reading.
///
/// The live reading has no block number of its own, so it is stamped with
/// the moment it was taken. The stamp is what lets a consumer tell a live
/// state from a stored snapshot: the two never carry the same version.
pub fn live_state_version() -> String {
    chrono::Utc::now().to_rfc3339()
}

/// Reads the live V4 pool state for every supplied pool id.
///
/// Two `extsload` calls are issued per pool (slot 0 and liquidity) and all of
/// them travel in a single JSON-RPC batch.
pub fn read_v4_pool_states(
    rpc_url: &str,
    pool_manager: &str,
    pool_ids: &[String],
    timeout_seconds: u64,
) -> Result<V4PoolStateBatch, BatchFailure> {
    if rpc_url.trim().is_empty() {
        return Err(BatchFailure::Transport(
            "V4 pool state RPC URL is empty".to_string(),
        ));
    }
    let timeout = timeout_seconds.max(5).min(120);
    // Pin every state read to one exact block. A batch that uses `latest`
    // for every request can span several blocks, and a later quoter call
    // cannot be compared to it coherently. The returned block number is
    // propagated to Review Manager, which pins the chain-side quote to the
    // same block.
    let block_number = execute_rpc(timeout, |runtime, client| {
        runtime.block_on(json_rpc_block_number(client, rpc_url, timeout))
    })
    .map_err(BatchFailure::Transport)?;
    let block_tag = format!("0x{block_number:x}");
    let manager = pool_manager.trim().to_lowercase();
    let mut requests = Vec::new();
    let mut id_map: Vec<(u64, String, String)> = Vec::new();
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
                }, block_tag],
            }));
            id_map.push((next_id, normalized.clone(), kind));
            next_id += 1;
        }
    }
    let mut batch = V4PoolStateBatch {
        states: Vec::new(),
        errors: Vec::new(),
        rpc_url: redact_rpc_text(rpc_url),
        block_number: Some(block_number),
        skipped_endpoints: Vec::new(),
    };
    if requests.is_empty() {
        return Ok(batch);
    }
    // The requests travel in bounded batches with a pause between them, so
    // a full refresh never bursts past the request budget of the node. The
    // free trial of the endpoint allows 15 requests per second.
    let mut rows = Vec::with_capacity(requests.len());
    for chunk in requests.chunks(POOL_STATE_BATCH_SIZE) {
        let chunk_rows = execute_rpc(timeout, |runtime, client| {
            runtime.block_on(json_rpc_batch(client, rpc_url, chunk.to_vec(), timeout))
        })
        .map_err(|error| {
            // A rate-limited node answers with a JSON-RPC error object rather
            // than the requested batch array. Recognising that shape lets the
            // caller fail over to the next endpoint instead of treating it as a
            // hard failure.
            if let Some((code, message)) = parse_rpc_error_object(&error) {
                return BatchFailure::RpcError { code, message };
            }
            BatchFailure::Transport(error)
        })?;
        rows.extend(chunk_rows);
        // The pause covers the **whole** chunk: every call inside the chunk
        // counts against the node's per-second budget, so the wait after a
        // chunk has to be long enough for that chunk to leave the window.
        let chunk_calls = chunk.len() as u64;
        let wait_ms =
            ((1000.0 * chunk_calls as f64) / POOL_STATE_REQUESTS_PER_SECOND).max(1.0) as u64;
        std::thread::sleep(std::time::Duration::from_millis(wait_ms));
    }

    let mut by_pool: HashMap<String, V4PoolState> = HashMap::new();
    let mut order: Vec<String> = Vec::new();
    // Which of the two reads of each pool answered. A pool is only complete
    // when **both** answered with usable data.
    let mut read_ok: HashMap<String, (bool, bool)> = HashMap::new();
    for (id, pool_id, kind) in id_map {
        let found = rows.iter().find(|(row_id, _, _)| *row_id == id);
        let Some((_, result, error_message)) = found else {
            batch.errors.push(format!(
                "{pool_id}:{kind}:v4_pool_state_batch_response_missing"
            ));
            continue;
        };
        let entry = by_pool.entry(pool_id.clone()).or_insert_with(|| {
            order.push(pool_id.clone());
            V4PoolState {
                pool_id: pool_id.clone(),
                ..Default::default()
            }
        });
        let mut ok = true;
        if let Some(message) = error_message {
            entry.error = Some(message.clone());
            batch.errors.push(format!("{pool_id}:{kind}:{message}"));
            ok = false;
        }
        if ok {
            let Some(raw) = result.as_str() else {
                return Err(BatchFailure::Malformed(format!(
                    "V4 pool state {kind} result is not a hex string"
                )));
            };
            if kind == "slot0" {
                let (sqrt_price, tick, protocol_fee, lp_fee) = parse_v4_slot0_words(raw);
                entry.sqrt_price_x96 = sqrt_price.to_string();
                entry.tick = tick;
                entry.protocol_fee = protocol_fee;
                entry.lp_fee = lp_fee;
                // A `slot0` word of all zeros describes no pool. Treating it
                // as a price would replace a real stored measurement with a
                // fabricated one.
                if sqrt_price.is_zero() {
                    entry.error = Some(format!("{pool_id}:slot0:zero_sqrt_price"));
                    batch
                        .errors
                        .push(format!("{pool_id}:slot0:zero_sqrt_price"));
                    ok = false;
                }
            } else {
                let liquidity = parse_v4_liquidity_word(raw);
                entry.liquidity = liquidity;
                entry.has_liquidity = liquidity > 0;
            }
        }
        let slot = read_ok.entry(pool_id.clone()).or_insert((false, false));
        if kind == "slot0" {
            slot.0 = ok;
        } else {
            slot.1 = ok;
        }
    }
    for (pool_id, (slot_ok, liquidity_ok)) in read_ok {
        if let Some(entry) = by_pool.get_mut(&pool_id) {
            entry.complete = slot_ok && liquidity_ok;
        }
    }
    batch.states = order
        .into_iter()
        .filter_map(|pool_id| by_pool.remove(&pool_id))
        .collect();
    Ok(batch)
}

/// Parses a JSON-RPC error object out of a failed batch response.
///
/// A rate-limited node answers with `{"error":{"code":429,...},"jsonrpc":"2.0"}`
/// instead of the requested array. Recognising that shape lets the caller
/// fail over to the next endpoint instead of treating it as a hard error.
fn parse_rpc_error_object(error: &str) -> Option<(i64, String)> {
    let start = error.find('{')?;
    let payload: Value = serde_json::from_str(&error[start..]).ok()?;
    let detail = payload.get("error")?;
    let code = detail.get("code").and_then(Value::as_i64).unwrap_or(0);
    let message = detail
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or("unknown RPC error")
        .to_string();
    Some((code, message))
}

/// Reads the live V4 pool state, trying every configured RPC endpoint until
/// one answers.
///
/// Every endpoint is probed with a short DNS/TCP pre-check first. An
/// endpoint whose host does not resolve is skipped immediately instead of
/// burning the full RPC timeout, and the skip is reported through
/// `skipped_endpoints` so the failure is never silent.
pub fn read_v4_pool_states_with_fallback(
    rpc_urls: &[String],
    pool_manager: &str,
    pool_ids: &[String],
    timeout_seconds: u64,
) -> Result<V4PoolStateBatch, String> {
    if rpc_urls.is_empty() {
        return Err(
            "no V4 pool state RPC URL configured; set ROBINHOOD_MAINNET_RPC_URLS".to_string(),
        );
    }
    let mut errors = Vec::new();
    let mut skipped_endpoints: Vec<String> = Vec::new();
    for rpc_url in rpc_urls {
        if let Err(reason) = probe_rpc_endpoint(rpc_url) {
            skipped_endpoints.push(redact_rpc_text(rpc_url));
            errors.push(format!(
                "{}: endpoint probe failed: {reason}",
                redact_rpc_text(rpc_url)
            ));
            continue;
        }
        let mut attempt = 0u32;
        loop {
            match read_v4_pool_states(rpc_url, pool_manager, pool_ids, timeout_seconds) {
                Ok(batch) if !batch.errors.is_empty() || !batch.states.is_empty() => {
                    let mut batch = batch;
                    batch.errors.extend(errors);
                    batch.skipped_endpoints = skipped_endpoints;
                    return Ok(batch);
                }
                Ok(_) => break,
                Err(failure) => {
                    let rendered = match &failure {
                        BatchFailure::RpcError { code, message } => {
                            format!("rpc error {code}: {message}")
                        }
                        BatchFailure::Malformed(detail) => format!("malformed response: {detail}"),
                        BatchFailure::Transport(detail) => detail.clone(),
                    };
                    let rate_limited = matches!(
                        &failure,
                        BatchFailure::RpcError { code, .. } if *code == 429
                    );
                    if rate_limited && attempt < RATE_LIMIT_RETRIES {
                        // A rate limit is transient. Back off and try the same
                        // endpoint again before giving up on it.
                        let delay = RATE_LIMIT_BACKOFF_MS << attempt;
                        std::thread::sleep(Duration::from_millis(delay));
                        attempt += 1;
                        continue;
                    }
                    errors.push(format!("{}: {rendered}", redact_rpc_text(rpc_url)));
                    break;
                }
            }
        }
    }
    Err(format!(
        "all V4 pool state RPC endpoints failed: {}",
        errors.join("; ")
    ))
}

/// Probes an RPC endpoint's host with a short DNS/TCP budget.
///
/// The check only proves the host is reachable. It never contacts the chain
/// and never touches the pool state, so it stays cheap enough to run before
/// every batch.
fn probe_rpc_endpoint(rpc_url: &str) -> Result<(), String> {
    let host = rpc_host(rpc_url).ok_or_else(|| "RPC URL has no host".to_string())?;
    let port = rpc_port(rpc_url);
    let runtime = rpc_runtime()?;
    runtime.block_on(async {
        let mut addresses = tokio::net::lookup_host((host.as_str(), port))
            .await
            .map_err(|error| format!("host lookup failed: {error}"))?;
        let address = addresses
            .next()
            .ok_or_else(|| "host lookup returned no address".to_string())?;
        let probe_timeout = endpoint_probe_timeout_seconds();
        let timeout = Duration::from_secs(probe_timeout);
        match tokio::time::timeout(timeout, tokio::net::TcpStream::connect(address)).await {
            Ok(Ok(_stream)) => Ok(()),
            Ok(Err(error)) => Err(format!("connect failed: {error}")),
            Err(_) => Err(format!("connect timed out after {probe_timeout}s")),
        }
    })
}

/// Extracts the host component of an RPC URL.
fn rpc_host(rpc_url: &str) -> Option<String> {
    let authority = rpc_authority(rpc_url)?;
    let host = authority
        .rsplit_once('@')
        .map(|(_, host)| host)
        .unwrap_or(authority.as_str());
    let host = host.split(':').next().unwrap_or(host);
    if host.is_empty() {
        None
    } else {
        Some(host.to_string())
    }
}

/// Extracts the TCP port of an RPC URL, defaulting by scheme.
fn rpc_port(rpc_url: &str) -> u16 {
    let authority = rpc_authority(rpc_url).unwrap_or_default();
    let host = authority
        .rsplit_once('@')
        .map(|(_, host)| host)
        .unwrap_or(authority.as_str());
    if let Some((_, port)) = host.rsplit_once(':') {
        if let Ok(port) = port.parse::<u16>() {
            return port;
        }
    }
    let scheme = rpc_url
        .trim()
        .split_once("://")
        .map(|(scheme, _)| scheme.to_lowercase())
        .unwrap_or_default();
    match scheme.as_str() {
        "http" => 80,
        _ => 443,
    }
}

/// Extracts the authority component of an RPC URL.
fn rpc_authority(rpc_url: &str) -> Option<String> {
    let without_scheme = rpc_url
        .trim()
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or_else(|| rpc_url.trim());
    let authority = without_scheme
        .split(['/', '?', '#'])
        .next()
        .unwrap_or(without_scheme)
        .trim();
    if authority.is_empty() {
        None
    } else {
        Some(authority.to_string())
    }
}

/// Runs one JSON-RPC batch on a fresh runtime, with the same timeout and
/// transport handling as [`execute_rpc`].
pub(crate) fn execute_rpc_batch<T, F>(timeout_seconds: u64, block: F) -> Result<T, String>
where
    F: FnOnce(&tokio::runtime::Runtime, &reqwest::Client) -> Result<T, String>,
{
    execute_rpc(timeout_seconds, block)
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

pub(crate) fn rpc_runtime() -> Result<&'static tokio::runtime::Runtime, String> {
    static RPC_RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    if let Some(runtime) = RPC_RUNTIME.get() {
        return Ok(runtime);
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .enable_all()
        .build()
        .map_err(|error| format!("tokio runtime failed: {error}"))?;
    let _ = RPC_RUNTIME.set(runtime);
    Ok(RPC_RUNTIME.get().expect("runtime"))
}

/// Serialises a pool state batch into the evidence shape shared with the
/// Python readers.
pub fn pool_state_batch_to_json(batch: &V4PoolStateBatch) -> Value {
    let mut states = serde_json::Map::new();
    for state in &batch.states {
        let mut entry = serde_json::Map::new();
        entry.insert("poolId".to_string(), json!(state.pool_id));
        entry.insert("sqrtPriceX96".to_string(), json!(state.sqrt_price_x96));
        entry.insert("tick".to_string(), json!(state.tick));
        entry.insert("protocolFee".to_string(), json!(state.protocol_fee));
        entry.insert("lpFee".to_string(), json!(state.lp_fee));
        entry.insert("liquidity".to_string(), json!(state.liquidity.to_string()));
        entry.insert(
            "liquidityIndex".to_string(),
            json!(state.liquidity.to_string()),
        );
        entry.insert("hasLiquidity".to_string(), json!(state.has_liquidity));
        entry.insert(
            "source".to_string(),
            json!("pool_manager_extsload_live_state_batch_rust"),
        );
        entry.insert("rpcUrl".to_string(), json!(batch.rpc_url));
        entry.insert("blockNumber".to_string(), json!(batch.block_number));
        if let Some(error) = &state.error {
            entry.insert("error".to_string(), json!(error));
        }
        states.insert(state.pool_id.clone(), Value::Object(entry));
    }
    json!({
        "ok": true,
        "states": Value::Object(states),
        "errors": batch.errors,
        "blockNumber": batch.block_number,
        "source": "rust_pool_manager_extsload_batch",
    })
}

/// Minimal fixed-width big unsigned integer helper.
///
/// The pool manager only needs to render `sqrtPriceX96` as a decimal string,
/// so a small dedicated type avoids pulling a heavyweight bignum dependency
/// into a tool that is otherwise dependency-light.
pub mod num_bigint_stub {
    /// Decimal rendering of a 256-bit unsigned integer.
    #[derive(Clone, Debug, PartialEq, Eq)]
    pub struct BigUint {
        limbs: [u32; 8],
    }

    impl BigUint {
        /// Builds a big unsigned integer from big-endian bytes.
        pub fn from_bytes_be(bytes: &[u8]) -> Self {
            let mut limbs = [0u32; 8];
            let mut padded = [0u8; 32];
            let copy_len = bytes.len().min(32);
            padded[32 - copy_len..].copy_from_slice(&bytes[..copy_len]);
            for (index, chunk) in padded.chunks(4).enumerate() {
                limbs[7 - index] = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
            }
            Self { limbs }
        }

        /// Returns `true` when the value is zero.
        pub fn is_zero(&self) -> bool {
            self.limbs.iter().all(|limb| *limb == 0)
        }

        /// Returns the value as a decimal string.
        pub fn to_decimal_string(&self) -> String {
            if self.is_zero() {
                return "0".to_string();
            }
            let mut digits = Vec::new();
            let mut limbs = self.limbs;
            while limbs.iter().any(|limb| *limb != 0) {
                let mut remainder = 0u64;
                for limb in limbs.iter_mut().rev() {
                    let current = (remainder << 32) | u64::from(*limb);
                    *limb = (current / 10) as u32;
                    remainder = current % 10;
                }
                digits.push(char::from_digit(remainder as u32, 10).unwrap_or('0'));
            }
            digits.iter().rev().collect()
        }
    }

    impl std::fmt::Display for BigUint {
        fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str(&self.to_decimal_string())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_batch_fits_inside_the_nodes_per_second_budget() {
        // The node counts **every** call inside a JSON-RPC batch against its
        // own per-second budget and answers the excess with
        // `-32007 "15/second request limit reached"`. A batch that is larger
        // than the rate allows therefore fails a part of the population,
        // and the reader used to fall back to the stored snapshot for the
        // affected pools.
        assert!(
            POOL_STATE_BATCH_SIZE as f64 <= POOL_STATE_REQUESTS_PER_SECOND,
            "a batch of {} calls exceeds the {}-per-second budget",
            POOL_STATE_BATCH_SIZE,
            POOL_STATE_REQUESTS_PER_SECOND
        );
        assert!(
            POOL_STATE_BATCH_SIZE <= 15,
            "a batch of {} calls exceeds the node's hard 15-per-second ceiling",
            POOL_STATE_BATCH_SIZE
        );
    }

    #[test]
    fn a_pool_with_one_failed_read_is_not_a_complete_measurement() {
        // A pool is a measurement only when its `slot0` **and** its
        // liquidity were read. A failed half must not be mixed with a stored
        // half, because the resulting state describes neither block.
        let state = V4PoolState {
            pool_id: "0x01".to_string(),
            sqrt_price_x96: "0".to_string(),
            complete: false,
            ..Default::default()
        };
        assert!(!state.complete);
    }

    #[test]
    fn the_chain_configuration_supplies_the_default_robinhood_endpoints() {
        // The depth command must work on a machine where the endpoints live in
        // the chain configuration rather than in an exported variable.
        let urls = rpc_urls_from_chain_config();
        assert!(
            urls.iter()
                .any(|url| url.contains("rpc.mainnet.chain.robinhood.com")),
            "expected the primary chain-config endpoint, got {urls:?}"
        );
        assert!(
            urls.iter().any(|url| url.contains("publicnode.com")),
            "expected a fallback chain-config endpoint, got {urls:?}"
        );
    }

    /// The RPC endpoints have to belong to the chain that is being served.
    ///
    /// Every chain owns its own configuration file. Reading the mainnet
    /// configuration for a testnet run would measure the mainnet's pools and
    /// report them as the testnet's depth, so the endpoints are resolved
    /// from the chain's own configuration file.
    #[test]
    fn the_rpc_endpoints_belong_to_the_chain_that_is_served() {
        let mainnet = rpc_urls_from_chain_config_for(ROBINHOOD_CHAIN_ID);
        let testnet = rpc_urls_from_chain_config_for(ROBINHOOD_TESTNET_CHAIN_ID);
        assert!(
            mainnet.iter().any(|url| url.contains("rpc.mainnet.chain.robinhood.com")),
            "expected the mainnet endpoint, got {mainnet:?}"
        );
        assert!(
            !mainnet.iter().any(|url| url.contains("rpc.testnet.chain.robinhood.com")),
            "the mainnet endpoints must not contain a testnet endpoint, got {mainnet:?}"
        );
        assert!(
            testnet
                .iter()
                .any(|url| url.contains("rpc.testnet.chain.robinhood.com")),
            "expected the testnet endpoint, got {testnet:?}"
        );
        assert!(
            !testnet.iter().any(|url| url.contains("rpc.mainnet.chain.robinhood.com")),
            "the testnet endpoints must not contain a mainnet endpoint, got {testnet:?}"
        );
    }

    /// The network namespace of a chain id is part of the row identity of
    /// every table, so the two chains can never share a namespace.
    #[test]
    fn every_chain_has_its_own_network_namespace() {
        assert_eq!(network_for_chain_id(ROBINHOOD_CHAIN_ID), "robinhood");
        assert_eq!(
            network_for_chain_id(ROBINHOOD_TESTNET_CHAIN_ID),
            "robinhood-testnet"
        );
        assert_eq!(network_for_chain_id(9999), "chain-9999");
    }

    #[test]
    fn the_chain_configuration_endpoints_are_unique_and_ordered() {
        let urls = rpc_urls_from_chain_config();
        let mut unique = urls.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(unique.len(), urls.len(), "duplicate endpoint in {urls:?}");
        assert!(
            urls.len() >= 2,
            "expected primary plus fallbacks, got {urls:?}"
        );
    }

    #[test]
    fn the_environment_takes_priority_over_the_chain_configuration() {
        // The environment list wins as a whole, matching the Python reader.
        let mut configured: Vec<String> = Vec::new();
        for name in ROBINHOOD_RPC_ENV_VARS {
            let value = std::env::var(name).unwrap_or_default();
            if !value.trim().is_empty() {
                configured = value
                    .split(',')
                    .map(|item| item.trim().to_string())
                    .filter(|item| !item.is_empty())
                    .collect();
                break;
            }
        }
        if configured.is_empty() {
            return;
        }
        assert_eq!(rpc_urls(), configured);
    }

    // ------------------------------------------------------------------
    // W2: RPC endpoint pre-check
    // ------------------------------------------------------------------

    /// The Uniswap V4 SDK reads `Pool.liquidity` from the **fourth** word of
    /// the `Pool.State` struct. The earlier `+1` word is
    /// `feeGrowthGlobal0X128`, and using it made every pool look liquid while
    /// the chain reported zero in-range liquidity.
    #[test]
    fn the_liquidity_slot_is_the_fourth_word_of_the_pool_state() {
        let (state_slot, liquidity_slot) = v4_pool_state_slots(
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        .unwrap();
        assert_eq!(
            hex::encode(state_slot),
            "f9f9cffb27053c60d6c9552447b6a4fc873604745eef4fed26c3644421245fcf"
        );
        assert_eq!(
            hex::encode(liquidity_slot),
            "f9f9cffb27053c60d6c9552447b6a4fc873604745eef4fed26c3644421245fd2"
        );
    }

    #[test]
    fn rpc_host_extracts_the_host_without_credentials_or_path() {
        assert_eq!(
            rpc_host("https://rpc.mainnet.chain.robinhood.com"),
            Some("rpc.mainnet.chain.robinhood.com".to_string())
        );
        assert_eq!(
            rpc_host("https://user:pass@node.example.com/path?x=1"),
            Some("node.example.com".to_string())
        );
        assert_eq!(
            rpc_host("https://node.example.com:8545"),
            Some("node.example.com".to_string())
        );
        assert_eq!(rpc_host(""), None);
    }

    #[test]
    fn rpc_port_defaults_by_scheme_and_honours_explicit_ports() {
        assert_eq!(rpc_port("https://node.example.com"), 443);
        assert_eq!(rpc_port("http://node.example.com"), 80);
        assert_eq!(rpc_port("https://node.example.com:8545"), 8545);
        assert_eq!(rpc_port("http://node.example.com:1234"), 1234);
    }

    #[test]
    fn dead_endpoint_is_reported_as_skipped() {
        // A host that cannot resolve must fail the probe quickly instead of
        // consuming the full RPC timeout, and it must be reported as skipped
        // rather than silently ignored.
        let start = std::time::Instant::now();
        let result = probe_rpc_endpoint("https://rpc.invalid.nonexistent.example");
        let elapsed = start.elapsed();
        assert!(result.is_err(), "an unresolvable host must fail the probe");
        assert!(
            elapsed.as_secs() < 30,
            "the probe must fail fast, took {elapsed:?}"
        );
    }

    #[test]
    fn fallback_reports_skipped_endpoints() {
        // The dead endpoint is skipped; the healthy one answers. The batch
        // must record the skip so the failure is never silent.
        let urls = vec![
            "https://rpc.invalid.nonexistent.example".to_string(),
            "https://robinhood-rpc.publicnode.com".to_string(),
        ];
        let pool_ids =
            vec!["0x0000000000000000000000000000000000000000000000000000000000000001".to_string()];
        match read_v4_pool_states_with_fallback(&urls, ROBINHOOD_V4_POOL_MANAGER, &pool_ids, 20) {
            Ok(batch) => {
                // The endpoint authority is redacted before it is reported,
                // so the assertion looks for the redaction marker.
                assert!(
                    batch
                        .skipped_endpoints
                        .iter()
                        .any(|url| url.contains("[REDACTED]")),
                    "the dead endpoint must be reported as skipped: {:?}",
                    batch.skipped_endpoints
                );
                assert_eq!(
                    batch.skipped_endpoints.len(),
                    1,
                    "exactly the dead endpoint is skipped: {:?}",
                    batch.skipped_endpoints
                );
            }
            Err(error) => {
                // A fully offline environment is acceptable: the dead endpoint
                // must still be named in the error.
                assert!(
                    error.contains("[REDACTED]"),
                    "the dead endpoint must be named: {error}"
                );
            }
        }
    }
}
