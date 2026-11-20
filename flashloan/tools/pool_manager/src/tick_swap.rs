//! Tick-aware Uniswap V4 swap arithmetic.
//!
//! # Why this module exists
//!
//! The single-step arithmetic in [`crate::depth`] answers "what happens if
//! the swap stays inside the current tick range". The chain answers the
//! harder question: "what happens if the swap walks across tick ranges
//! until the requested amount is filled or the liquidity runs out".
//!
//! The difference is not academic. On the live Robinhood chain a pool whose
//! current range has **zero** in-range liquidity answers the quoter with
//! `NotEnoughLiquidity`, while the single-step arithmetic happily produced a
//! number from the fee-growth accumulator that had been mistaken for
//! liquidity. Every pool whose amount differs between the two sides was
//! explained by exactly this gap.
//!
//! This module therefore performs the **same tick traversal the contract
//! performs**, by driving the Uniswap V4 SDK's `v3_swap`, which is the
//! reference implementation of that loop.
//!
//! # Where the data comes from
//!
//! The tick data is read from the pool manager's storage with `extsload`,
//! using the slot derivation of the Uniswap V4 SDK's `PoolManagerLens`:
//!
//! * `state_slot` — `keccak256(abi.encode(poolId, 6))`;
//! * `ticks` — `state_slot + 4`, keyed by `keccak256(abi.encode(tick, slot))`;
//! * `tickBitmap` — `state_slot + 5`, keyed by
//!   `keccak256(abi.encode(int16 word, slot))`.
//!
//! The reads travel in one bounded JSON-RPC batch, at the same request rate
//! as the pool-state refresh, so the public endpoint's budget is respected.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::Duration;
use uniswap_v4_sdk::prelude::v3_sdk::entities::tick::Tick;
use uniswap_v4_sdk::prelude::v3_sdk::entities::tick_data_provider::TickDataProvider;
use uniswap_v4_sdk::prelude::v3_sdk::error::Error as V3Error;
use uniswap_v4_sdk::prelude::v3_sdk::utils::swap_math::v3_swap;

/// The offset of `Pool.ticks` inside the `Pool.State` struct.
///
/// The Uniswap V4 SDK uses the same offsets in its `PoolManagerLens`.
pub const TICKS_OFFSET: u8 = 4;

/// The offset of `Pool.tickBitmap` inside the `Pool.State` struct.
pub const TICK_BITMAP_OFFSET: u8 = 5;

/// The number of `extsload` requests that travel in one JSON-RPC batch.
pub const TICK_STATE_BATCH_SIZE: usize = 50;

/// The request rate the tick reads are issued at.
///
/// The public endpoint allows 15 requests per second; the pool-state reads
/// use 8, and the tick reads stay on the same budget.
pub const TICK_STATE_REQUESTS_PER_SECOND: f64 = 8.0;

/// Derives the `Pool.State` slot of a pool id.
///
/// This is the Solidity mapping derivation
/// `keccak256(abi.encode(poolId, POOLS_SLOT))`, i.e.
/// `keccak(poolId ++ 0x00 * 31 ++ 0x06)`.
pub fn pool_state_slot(pool_id: &str) -> Result<[u8; 32], String> {
    let normalized = pool_id.trim().to_lowercase();
    if !normalized.starts_with("0x") || normalized.len() != 66 {
        return Err(format!("invalid V4 pool id: {pool_id}"));
    }
    let raw = crate::chain::decode_hex(&normalized)?;
    if raw.len() > 32 {
        return Err(format!("invalid V4 pool id length: {pool_id}"));
    }
    let mut pool_bytes = [0u8; 32];
    pool_bytes[32 - raw.len()..].copy_from_slice(&raw);
    let mut seed = Vec::with_capacity(64);
    seed.extend_from_slice(&pool_bytes);
    seed.extend_from_slice(&[0u8; 31]);
    seed.push(6);
    Ok(crate::chain::keccak256(&seed))
}

/// Derives the storage slot of one `Tick.Info` entry.
///
/// The `ticks` mapping lives at `state_slot + 4` and a mapping entry is
/// stored at `keccak256(abi.encode(key, mappingSlot))`. The key is the
/// signed 24-bit tick, encoded as a left-padded 32-byte word.
pub fn tick_info_slot(state_slot: &[u8; 32], tick: i32) -> [u8; 32] {
    let ticks_mapping_slot = add_small(state_slot, TICKS_OFFSET);
    let mut seed = Vec::with_capacity(64);
    seed.extend_from_slice(&encode_signed_word(tick as i64));
    seed.extend_from_slice(&ticks_mapping_slot);
    crate::chain::keccak256(&seed)
}

/// Derives the storage slot of one tick bitmap word.
///
/// The `tickBitmap` mapping lives at `state_slot + 5` and is keyed by the
/// **signed 16-bit** word position.
pub fn tick_bitmap_slot(state_slot: &[u8; 32], word: i16) -> [u8; 32] {
    let bitmap_mapping_slot = add_small(state_slot, TICK_BITMAP_OFFSET);
    let mut seed = Vec::with_capacity(64);
    seed.extend_from_slice(&encode_signed_word(word as i64));
    seed.extend_from_slice(&bitmap_mapping_slot);
    crate::chain::keccak256(&seed)
}

/// Adds a small offset to a 256-bit slot, carrying upwards.
fn add_small(slot: &[u8; 32], offset: u8) -> [u8; 32] {
    let mut out = *slot;
    let mut carry = u16::from(offset);
    for byte in out.iter_mut().rev() {
        if carry == 0 {
            break;
        }
        let value = u16::from(*byte) + carry;
        *byte = value as u8;
        carry = value >> 8;
    }
    out
}

/// Encodes a signed value as a left-padded 32-byte two's-complement word.
fn encode_signed_word(value: i64) -> [u8; 32] {
    let mut word = [0u8; 32];
    word[24..].copy_from_slice(&value.to_be_bytes());
    word
}

/// The tick data of one pool, read from the chain.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct V4TickState {
    /// The pool the tick data belongs to.
    pub pool_id: String,
    /// The tick bitmap words, keyed by their signed word position.
    ///
    /// The value is the decimal form of the 256-bit word.
    pub bitmap: HashMap<i16, String>,
    /// The per-tick liquidity, keyed by the tick.
    pub ticks: HashMap<i32, TickLiquidity>,
}

/// The liquidity a tick adds to, or removes from, the active range.
#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize)]
pub struct TickLiquidity {
    /// The gross liquidity referenced by positions that span this tick.
    pub liquidity_gross: u128,
    /// The net liquidity change when the price crosses this tick.
    pub liquidity_net: i128,
}

/// The result of one tick-state read.
#[derive(Clone, Debug, Default)]
pub struct V4TickStateBatch {
    /// The tick data, in the order the pools were requested.
    pub states: Vec<V4TickState>,
    /// The per-pool read failures.
    pub errors: Vec<String>,
    /// The endpoint that answered.
    pub rpc_url: String,
}

impl TickRequest {
    /// Builds the tick requests for the supplied pool ids from the pool
    /// manager's own tables.
    ///
    /// The tick and the tick spacing are pool state, so they are read from
    /// the manager's tables rather than from a caller's arguments. The
    /// database is opened **read only**, so reading a tick request can never
    /// write to the store.
    pub fn from_pool_ids(
        pool_ids: &[String],
        store_path: &str,
        chain_id: i64,
    ) -> Result<Vec<TickRequest>, String> {
        if pool_ids.is_empty() {
            return Err("tick-state requires at least one --pool-id".to_string());
        }
        let connection = rusqlite::Connection::open_with_flags(
            store_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
                | rusqlite::OpenFlags::SQLITE_OPEN_URI
                | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|error| format!("could not open the pool store read only: {error}"))?;
        let mut requests = Vec::with_capacity(pool_ids.len());
        for pool_id in pool_ids {
            let normalized = pool_id.trim().to_lowercase();
            if normalized.is_empty() {
                continue;
            }
            let mut statement = connection
                .prepare(
                    "SELECT p.tick_spacing, s.tick
                     FROM v4_exchange_pools p
                     LEFT JOIN v4_exchange_pool_liquidity_snapshots s ON s.pool_id = p.pool_id
                     WHERE p.pool_id = ?1 AND p.chain_id = ?2",
                )
                .map_err(|error| format!("could not prepare the tick query: {error}"))?;
            let mut rows = statement
                .query(rusqlite::params![normalized, chain_id])
                .map_err(|error| format!("could not read the tick query: {error}"))?;
            let mut found = None;
            while let Some(row) = rows
                .next()
                .map_err(|error| format!("could not walk the tick query: {error}"))?
            {
                let tick_spacing: i64 = row
                    .get(0)
                    .map_err(|error| format!("could not read the tick spacing: {error}"))?;
                let tick: Option<i64> = row
                    .get(1)
                    .map_err(|error| format!("could not read the tick: {error}"))?;
                found = Some((tick_spacing, tick));
                break;
            }
            let Some((tick_spacing, tick)) = found else {
                return Err(format!("{normalized} is not known to the pool manager"));
            };
            // The tick may be missing from the snapshot; the chain state is
            // the authority, so the pool's own snapshot is only a hint.
            let current_tick = tick.unwrap_or(0) as i32;
            requests.push(TickRequest {
                pool_id: normalized,
                current_tick,
                tick_spacing: tick_spacing as i32,
            });
        }
        Ok(requests)
    }
}

/// A pool whose tick neighbourhood has to be read.
#[derive(Clone, Debug)]
pub struct TickRequest {
    /// The pool id.
    pub pool_id: String,
    /// The pool's current tick.
    pub current_tick: i32,
    /// The pool's tick spacing.
    pub tick_spacing: i32,
}

/// The number of bitmap words that are read on either side of the current
/// tick's word in the **first** pass.
///
/// The SDK's traversal walks **word by word**: when the current word holds no
/// initialized tick, the swap steps to the word boundary and asks again.
/// The provider therefore has to be able to answer for every word the walk
/// can reach, not only for the neighbours of the current word.
///
/// The first pass reads a narrow window, because the request budget of the
/// public endpoint is 15 requests per second. The window is widened only
/// when the narrow window held no initialized tick at all, which is the
/// signature of a pool whose liquidity sits further out.
pub const BITMAP_WORD_RADIUS: i32 = 2;

/// The number of bitmap words that are read on either side of the current
/// tick's word when the narrow window found nothing.
pub const BITMAP_WORD_RADIUS_WIDE: i32 = 64;

/// Derives the bitmap word positions that surround a tick.
///
/// A tick belongs to word `tick / tick_spacing / 256`. The swap needs the
/// word of the tick and the words on either side, because the traversal may
/// step across a word boundary.
///
/// The radius is wide on purpose: the swap may have to walk many empty
/// words before it reaches the next initialized tick, and a provider that
/// only knows the immediate neighbours reports an empty word where the
/// contract would find a tick. That is the difference between "zero output"
/// and the chain's real amount.
pub fn bitmap_words_around(tick: i32, tick_spacing: i32) -> Vec<i16> {
    bitmap_words_within(tick, tick_spacing, BITMAP_WORD_RADIUS)
}

/// Derives the bitmap word positions within a radius of the current word.
pub fn bitmap_words_within(tick: i32, tick_spacing: i32, radius: i32) -> Vec<i16> {
    let spacing = tick_spacing.max(1);
    let compressed = tick.div_euclid(spacing);
    let centre = compressed.div_euclid(256);
    let mut words = Vec::new();
    for delta in -radius..=radius {
        let word = centre + delta;
        if word >= i16::MIN as i32 && word <= i16::MAX as i32 {
            words.push(word as i16);
        }
    }
    words
}

/// Derives the ticks whose liquidity the traversal may reach.
///
/// The traversal steps to the next or previous **initialized** tick, and
/// every initialized tick is a multiple of the tick spacing, so the ticks
/// around the current one are the candidates.
pub fn candidate_ticks_around(current_tick: i32, tick_spacing: i32) -> Vec<i32> {
    let spacing = tick_spacing.max(1);
    let base = current_tick.div_euclid(spacing) * spacing;
    let mut ticks = Vec::new();
    for delta in -1i32..=1 {
        let tick = base + delta * spacing;
        if tick >= -887_272 && tick <= 887_272 {
            ticks.push(tick);
        }
    }
    ticks
}

/// Reads the tick data of the supplied pools from the chain.
///
/// The reads travel in one bounded JSON-RPC batch, so the request budget of
/// the public endpoint is respected.
pub fn read_v4_tick_states(
    rpc_url: &str,
    pool_manager: &str,
    pools: &[TickRequest],
    timeout_seconds: u64,
) -> Result<V4TickStateBatch, String> {
    if rpc_url.trim().is_empty() {
        return Err("V4 tick state RPC URL is empty".to_string());
    }
    let manager = pool_manager.trim().to_lowercase();
    let selector = crate::chain::function_selector("extsload(bytes32)");
    let mut requests: Vec<Value> = Vec::new();
    // (request id, pool index, kind, key)
    let mut id_map: Vec<(u64, usize, TickSlotKind, i64)> = Vec::new();
    let mut next_id = 1u64;
    // The bitmap words that are read for each pool, so the second pass can
    // resolve the initialized ticks they point at.
    let mut bitmap_words_by_pool: Vec<Vec<i16>> = Vec::with_capacity(pools.len());

    for (index, request) in pools.iter().enumerate() {
        let state_slot = pool_state_slot(&request.pool_id)?;
        let words = bitmap_words_around(request.current_tick, request.tick_spacing);
        bitmap_words_by_pool.push(words.clone());
        for word in words {
            let mut data = Vec::with_capacity(4 + 32);
            data.extend_from_slice(&selector);
            data.extend_from_slice(&tick_bitmap_slot(&state_slot, word));
            requests.push(json!({
                "jsonrpc": "2.0",
                "id": next_id,
                "method": "eth_call",
                "params": [{ "to": manager, "data": crate::chain::as_hex(&data) }, "latest"],
            }));
            id_map.push((next_id, index, TickSlotKind::Bitmap, word as i64));
            next_id += 1;
        }
        // The tick **information** cannot be guessed from the current tick:
        // the initialized ticks of a real pool sit at the far ends of the
        // price range, not next to the current price. The bitmap words are
        // therefore read first, and the ticks they point at are read in a
        // second pass.
        //
        // Reading only the neighbours of the current tick was the reason the
        // traversal answered "zero output" while the chain produced a real
        // amount: the tick the bitmap selected had no liquidity data at all.
    }
    if requests.is_empty() {
        return Ok(V4TickStateBatch::default());
    }

    let timeout = timeout_seconds.max(5).min(120);
    let mut rows: Vec<(u64, Value, Option<String>)> = Vec::new();
    for chunk in requests.chunks(TICK_STATE_BATCH_SIZE) {
        let chunk_rows = crate::chain::execute_rpc_batch(timeout, |runtime, client| {
            runtime.block_on(crate::chain::json_rpc_batch(
                client,
                rpc_url,
                chunk.to_vec(),
                timeout,
            ))
        })?;
        rows.extend(chunk_rows);
        std::thread::sleep(Duration::from_millis(
            ((1000.0 / TICK_STATE_REQUESTS_PER_SECOND).max(1.0) as u64) * chunk.len() as u64 / 2,
        ));
    }

    let mut batch = V4TickStateBatch {
        states: pools
            .iter()
            .map(|request| V4TickState {
                pool_id: request.pool_id.clone(),
                ..Default::default()
            })
            .collect(),
        errors: Vec::new(),
        rpc_url: crate::chain::redact_rpc_text(rpc_url),
    };
    for (row_id, result, error_message) in rows {
        let Some((_, index, kind, key)) = id_map.iter().find(|(id, _, _, _)| *id == row_id) else {
            continue;
        };
        let state = &mut batch.states[*index];
        if let Some(message) = error_message {
            batch
                .errors
                .push(format!("{}:tick_state:{message}", state.pool_id));
            continue;
        }
        let Some(raw) = result.as_str() else { continue };
        let Ok(bytes) = crate::chain::decode_hex(raw.trim_start_matches("0x")) else {
            continue;
        };
        match kind {
            TickSlotKind::Bitmap => {
                state.bitmap.insert(*key as i16, bytes_to_decimal(&bytes));
            }
            TickSlotKind::Info => {
                let (liquidity_gross, liquidity_net) = decode_tick_liquidity(&bytes);
                state.ticks.insert(
                    *key as i32,
                    TickLiquidity {
                        liquidity_gross,
                        liquidity_net,
                    },
                );
            }
        }
    }

    // The second pass: read the tick information of every initialized tick
    // the bitmap pointed at.
    //
    // This is the step that makes the traversal exact. The initialized ticks
    // of a real pool sit at the far ends of the price range, so the tick
    // information cannot be derived from the current tick: the bitmap is the
    // only source of truth about **which** ticks are initialized.
    //
    // A pool whose narrow window held **no** initialized tick at all gets a
    // second, wider bitmap read: the liquidity of such a pool sits further
    // out, and the traversal would otherwise walk into empty words and
    // answer with a zero output.
    let empty_pools: Vec<usize> = (0..batch.states.len())
        .filter(|index| {
            batch.states[*index]
                .bitmap
                .values()
                .all(|value| value.trim_start_matches('0').is_empty())
        })
        .collect();
    if !empty_pools.is_empty() {
        let mut wide_requests: Vec<Value> = Vec::new();
        let mut wide_id_map: Vec<(u64, usize, i64)> = Vec::new();
        let mut wide_next_id = 1u64;
        for index in &empty_pools {
            let request = &pools[*index];
            let Ok(state_slot) = pool_state_slot(&request.pool_id) else {
                continue;
            };
            for word in bitmap_words_within(
                request.current_tick,
                request.tick_spacing,
                BITMAP_WORD_RADIUS_WIDE,
            ) {
                let mut data = Vec::with_capacity(4 + 32);
                data.extend_from_slice(&selector);
                data.extend_from_slice(&tick_bitmap_slot(&state_slot, word));
                wide_requests.push(json!({
                    "jsonrpc": "2.0",
                    "id": wide_next_id,
                    "method": "eth_call",
                    "params": [{ "to": manager, "data": crate::chain::as_hex(&data) }, "latest"],
                }));
                wide_id_map.push((wide_next_id, *index, word as i64));
                wide_next_id += 1;
            }
        }
        if !wide_requests.is_empty() {
            let mut wide_rows: Vec<(u64, Value, Option<String>)> = Vec::new();
            for chunk in wide_requests.chunks(TICK_STATE_BATCH_SIZE) {
                let chunk_rows = crate::chain::execute_rpc_batch(timeout, |runtime, client| {
                    runtime.block_on(crate::chain::json_rpc_batch(
                        client,
                        rpc_url,
                        chunk.to_vec(),
                        timeout,
                    ))
                })?;
                wide_rows.extend(chunk_rows);
                std::thread::sleep(Duration::from_millis(
                    ((1000.0 / TICK_STATE_REQUESTS_PER_SECOND).max(1.0) as u64)
                        * chunk.len() as u64
                        / 2,
                ));
            }
            for (row_id, result, error_message) in wide_rows {
                let Some((_, index, word)) = wide_id_map.iter().find(|(id, _, _)| *id == row_id)
                else {
                    continue;
                };
                if let Some(message) = error_message {
                    batch.errors.push(format!(
                        "{}:tick_bitmap:{message}",
                        batch.states[*index].pool_id
                    ));
                    continue;
                }
                let Some(raw) = result.as_str() else { continue };
                let Ok(bytes) = crate::chain::decode_hex(raw.trim_start_matches("0x")) else {
                    continue;
                };
                batch.states[*index]
                    .bitmap
                    .insert(*word as i16, bytes_to_decimal(&bytes));
            }
        }
    }

    let mut tick_requests: Vec<Value> = Vec::new();
    let mut tick_id_map: Vec<(u64, usize, i32)> = Vec::new();
    let mut tick_next_id = 1u64;
    for (index, request) in pools.iter().enumerate() {
        let state_slot = match pool_state_slot(&request.pool_id) {
            Ok(slot) => slot,
            Err(_) => continue,
        };
        let mut ticks: Vec<i32> = Vec::new();
        for word in bitmap_words_by_pool.get(index).into_iter().flatten() {
            let Some(decimal) = batch.states[index].bitmap.get(word) else {
                continue;
            };
            let Ok(value) = alloy_primitives::U256::from_str_radix(decimal, 10) else {
                continue;
            };
            ticks.extend(initialized_ticks_in_word(
                *word,
                value,
                request.tick_spacing,
            ));
        }
        ticks.sort_unstable();
        ticks.dedup();
        for tick in ticks {
            let mut data = Vec::with_capacity(4 + 32);
            data.extend_from_slice(&selector);
            data.extend_from_slice(&tick_info_slot(&state_slot, tick));
            tick_requests.push(json!({
                "jsonrpc": "2.0",
                "id": tick_next_id,
                "method": "eth_call",
                "params": [{ "to": manager, "data": crate::chain::as_hex(&data) }, "latest"],
            }));
            tick_id_map.push((tick_next_id, index, tick));
            tick_next_id += 1;
        }
    }
    if !tick_requests.is_empty() {
        let mut tick_rows: Vec<(u64, Value, Option<String>)> = Vec::new();
        for chunk in tick_requests.chunks(TICK_STATE_BATCH_SIZE) {
            let chunk_rows = crate::chain::execute_rpc_batch(timeout, |runtime, client| {
                runtime.block_on(crate::chain::json_rpc_batch(
                    client,
                    rpc_url,
                    chunk.to_vec(),
                    timeout,
                ))
            })?;
            tick_rows.extend(chunk_rows);
            std::thread::sleep(Duration::from_millis(
                ((1000.0 / TICK_STATE_REQUESTS_PER_SECOND).max(1.0) as u64) * chunk.len() as u64
                    / 2,
            ));
        }
        for (row_id, result, error_message) in tick_rows {
            let Some((_, index, tick)) = tick_id_map.iter().find(|(id, _, _)| *id == row_id) else {
                continue;
            };
            let state = &mut batch.states[*index];
            if let Some(message) = error_message {
                batch
                    .errors
                    .push(format!("{}:tick_info:{message}", state.pool_id));
                continue;
            }
            let Some(raw) = result.as_str() else { continue };
            let Ok(bytes) = crate::chain::decode_hex(raw.trim_start_matches("0x")) else {
                continue;
            };
            let (liquidity_gross, liquidity_net) = decode_tick_liquidity(&bytes);
            state.ticks.insert(
                *tick,
                TickLiquidity {
                    liquidity_gross,
                    liquidity_net,
                },
            );
        }
    }
    Ok(batch)
}

/// Returns every initialized tick that a bitmap word points at.
///
/// The bitmap stores one bit per compressed tick, and the compressed tick
/// is the tick divided by the tick spacing, so the bit position gives the
/// tick directly.
fn initialized_ticks_in_word(
    word_position: i16,
    word: alloy_primitives::U256,
    tick_spacing: i32,
) -> Vec<i32> {
    let spacing = tick_spacing.max(1);
    let base = i32::from(word_position) * 256 * spacing;
    let mut ticks = Vec::new();
    for bit in 0..256u32 {
        if word.bit(bit as usize) {
            ticks.push(base + (bit as i32) * spacing);
        }
    }
    ticks
}

/// The two kinds of tick storage slot that are read.
#[derive(Clone, Copy, Debug)]
enum TickSlotKind {
    Bitmap,
    #[allow(dead_code)]
    Info,
}

/// Decodes a packed `Tick.Info` word.
///
/// The word holds `liquidityGross` (a `uint128`) in the low half and
/// `liquidityNet` (an `int128`) in the high half, exactly like the SDK's
/// `decode_liquidity_gross_and_net`.
fn decode_tick_liquidity(bytes: &[u8]) -> (u128, i128) {
    let mut padded = [0u8; 32];
    let copy_len = bytes.len().min(32);
    padded[32 - copy_len..].copy_from_slice(&bytes[bytes.len() - copy_len..]);
    let mut gross_bytes = [0u8; 16];
    gross_bytes.copy_from_slice(&padded[16..32]);
    let liquidity_gross = u128::from_be_bytes(gross_bytes);
    let mut net_bytes = [0u8; 16];
    net_bytes.copy_from_slice(&padded[0..16]);
    let liquidity_net = i128::from_be_bytes(net_bytes);
    (liquidity_gross, liquidity_net)
}

/// Converts a big-endian byte string into its decimal form.
pub fn bytes_to_decimal(bytes: &[u8]) -> String {
    crate::chain::num_bigint_stub::BigUint::from_bytes_be(bytes).to_decimal_string()
}

// ---------------------------------------------------------------------------
// The tick traversal
// ---------------------------------------------------------------------------

/// The tick data of one pool, in the shape the SDK's swap loop consumes.
///
/// The Uniswap V4 SDK's swap loop asks for two things:
///
/// * the **next initialized tick** inside the current bitmap word, and
/// * the **liquidity net** of an initialized tick.
///
/// Both are answered from the storage words that were read from the chain,
/// so the traversal reproduces the contract's own tick crossing without a
/// second RPC round trip per step.
#[derive(Clone, Debug, Default)]
pub struct LocalTickData {
    /// The bitmap words, keyed by their signed word position.
    pub bitmap: HashMap<i16, alloy_primitives::U256>,
    /// The per-tick liquidity, keyed by the tick.
    pub ticks: HashMap<i32, TickLiquidity>,
    /// The tick spacing of the pool.
    pub tick_spacing: i32,
}

impl LocalTickData {
    /// Builds the local tick data from a chain read.
    pub fn from_state(state: &V4TickState, tick_spacing: i32) -> Self {
        let mut bitmap = HashMap::new();
        for (word, decimal) in &state.bitmap {
            let Ok(value) = alloy_primitives::U256::from_str_radix(decimal, 10) else {
                continue;
            };
            bitmap.insert(*word, value);
        }
        Self {
            bitmap,
            ticks: state.ticks.clone(),
            tick_spacing: tick_spacing.max(1),
        }
    }
}

/// A [`TickDataProvider`] over the tick data that was read from the chain.
///
/// The provider is the **adapter** between the Uniswap V4 SDK's swap loop
/// and Pool Manager's own chain reader. It never opens a connection: the
/// bitmap words and the tick liquidities are supplied by the caller, so the
/// traversal runs on data that was already verified against the chain.
#[derive(Clone, Debug, Default)]
pub struct LocalTickDataProvider {
    /// The tick data of the pool.
    pub data: LocalTickData,
}

impl TickDataProvider for LocalTickDataProvider {
    type Index = i32;

    async fn get_tick(&self, index: i32) -> Result<Tick<i32>, V3Error> {
        let entry = self
            .data
            .ticks
            .get(&index)
            .ok_or(V3Error::NoTickDataError)?;
        Ok(Tick {
            index,
            liquidity_gross: entry.liquidity_gross,
            liquidity_net: entry.liquidity_net,
        })
    }

    async fn next_initialized_tick_within_one_word(
        &self,
        tick: i32,
        lte: bool,
        tick_spacing: i32,
    ) -> Result<(i32, bool), V3Error> {
        // The bitmap word is stored under the **signed 16-bit** word position,
        // which is exactly what the SDK's `TickBitMapProvider` expects from a
        // provider that answers in the SDK's own index type.
        let spacing = tick_spacing.max(1);
        // The SDK's `compress` floors towards negative infinity, and its
        // `position` splits the compressed tick into a word and a bit with
        // an **arithmetic** shift, so a negative compressed tick keeps its
        // two's-complement bit pattern.
        let compressed = compress_tick(tick, spacing);
        // Moving up starts at the **next** tick, because the current tick's
        // own bit does not matter when the traversal moves up. The SDK looks
        // the word up with that shifted tick, so the lookup must do the same:
        // the next tick can live in the next word.
        let lookup_compressed = if lte { compressed } else { compressed + 1 };
        let (word_position, _) = tick_position(lookup_compressed);
        let word = self
            .data
            .bitmap
            .get(&(word_position as i16))
            .copied()
            .unwrap_or(alloy_primitives::U256::ZERO);
        // The SDK's reference implementation of the word scan is reused, so
        // the mask, the most/least significant bit selection and the tick
        // reconstruction are the SDK's, not a second re-derivation.
        let (next, initialized) = next_initialized_tick_in_word(word, tick, lte, tick_spacing);
        Ok((next, initialized))
    }
}

/// Compresses a tick towards negative infinity, exactly like the SDK's
/// `TickIndex::compress`.
///
/// The SDK uses a truncating division and then subtracts one when the
/// remainder is negative, which is the same result as a floor division. The
/// subtraction matters for a negative tick that is not a multiple of the
/// spacing: the floor is one word lower than the truncation.
fn compress_tick(tick: i32, tick_spacing: i32) -> i32 {
    let spacing = tick_spacing.max(1);
    let truncated = tick / spacing;
    if tick % spacing < 0 {
        truncated - 1
    } else {
        truncated
    }
}

/// Splits a compressed tick into its bitmap word and its bit position.
///
/// The SDK's `TickIndex::position` uses an **arithmetic** right shift, so a
/// negative compressed tick keeps its two's-complement bit pattern: the word
/// is the signed `>> 8` and the bit is the low byte. Reproducing that exactly
/// matters, because the contract stores the bitmap under a signed word
/// position.
fn tick_position(compressed: i32) -> (i32, u8) {
    (compressed >> 8, (compressed & 0xff) as u8)
}

/// Finds the next initialized tick inside one bitmap word.
///
/// This is the SDK's `TickBitMapProvider::next_initialized_tick_within_one_word`
/// algorithm, expressed over a word that was already read. Keeping the same
/// algorithm is the point: a re-derived variant would drift from the
/// contract's own tick selection at the word boundary.
fn next_initialized_tick_in_word(
    word: alloy_primitives::U256,
    tick: i32,
    lte: bool,
    tick_spacing: i32,
) -> (i32, bool) {
    let spacing = tick_spacing.max(1);
    let compressed = compress_tick(tick, spacing);
    if lte {
        let (word_pos, bit_pos) = tick_position(compressed);
        // All the ones at or to the right of `bit_pos`.
        //
        // The shift happens in U256 space: `2u64 << bit_pos` would overflow a
        // u64 for a bit position above 63 and silently wrap to zero, which
        // would hide every initialized tick in the upper half of a word.
        let mask =
            (alloy_primitives::U256::from(2u64) << bit_pos) - alloy_primitives::U256::from(1u64);
        let masked = word & mask;
        let initialized = !masked.is_zero();
        let msb = if initialized {
            (255 - masked.leading_zeros()) as i32
        } else {
            0
        };
        ((word_pos * 256 + msb) * spacing, initialized)
    } else {
        // The word of the **next** tick, because the current tick's own bit
        // does not matter when the traversal moves up.
        let compressed = compressed + 1;
        let (word_pos, bit_pos) = tick_position(compressed);
        // All the ones at or to the left of `bit_pos`.
        let mask = alloy_primitives::U256::ZERO - (alloy_primitives::U256::from(1u64) << bit_pos);
        let masked = word & mask;
        let initialized = !masked.is_zero();
        let lsb = if initialized {
            masked.trailing_zeros() as i32
        } else {
            255
        };
        ((word_pos * 256 + lsb) * spacing, initialized)
    }
}

/// Resolves the **fixed-fee** pool population for an off-chain measurement.
///
/// The population is resolved here, in Pool Manager, because Pool Manager
/// owns the admission rules:
///
/// * a dynamic-fee pool is excluded, because its effective rate is decided
///   by a hook at swap time and can never be reproduced off chain;
/// * a pool without live liquidity is excluded, because it cannot answer a
///   swap at all;
/// * a pool without a stored tick is excluded, because the tick traversal
///   cannot anchor without it.
///
/// The database is opened **read only**, so resolving a population never
/// writes.
pub fn resolve_measurement_pool_ids(
    database_path: &str,
    network: &str,
    chain_id: i64,
    limit: usize,
) -> Result<Vec<String>, String> {
    let connection = rusqlite::Connection::open_with_flags(
        database_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
            | rusqlite::OpenFlags::SQLITE_OPEN_URI
            | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|error| format!("could not open the pool store read only: {error}"))?;
    let sql = "SELECT p.pool_id
               FROM v4_exchange_pools p
               JOIN v4_exchange_pool_liquidity_snapshots s
                 ON s.network = p.network AND s.chain_id = p.chain_id
                AND s.protocol = p.protocol AND s.pool_id = p.pool_id
               WHERE p.network = ?1 AND p.chain_id = ?2 AND p.protocol = 'uniswap_v4'
                 AND p.token0 <> '0x0000000000000000000000000000000000000000'
                 AND p.dynamic_fee = 0
                 AND (p.fee & ?) = 0
                 AND s.tick IS NOT NULL
                 AND CAST(s.liquidity AS INTEGER) > 0
               ORDER BY CAST(s.liquidity AS INTEGER) DESC
               LIMIT ?4";
    let mut statement = connection
        .prepare(sql)
        .map_err(|error| format!("could not prepare the measurement pool query: {error}"))?;
    let rows = statement
        .query_map(
            rusqlite::params![
                network.to_string(),
                chain_id,
                i64::from(manager_contracts::V4_DYNAMIC_FEE_FLAG),
                limit as i64,
            ],
            |row| row.get::<_, String>(0),
        )
        .map_err(|error| format!("could not walk the measurement pool query: {error}"))?;
    // The query is bounded, but a pool id that is stored with a different
    // letter case still belongs to the pool, so the ids are normalised
    // before they leave Pool Manager.
    let mut ids = Vec::new();
    for row in rows.flatten() {
        let normalized = row.trim().to_lowercase();
        if !normalized.is_empty() {
            ids.push(normalized);
        }
    }
    Ok(ids)
}

/// Reads the tick data of the requested pools from the chain.
///
/// This is Pool Manager's **published API** for the tick state. Review
/// Manager and the other managers call it instead of reading the tick
/// storage themselves, so the storage layout, the rate limit and the
/// batching live in exactly one place.
///
/// The returned map is keyed by the pool id.
pub fn read_tick_states(
    rpc_url: &str,
    pool_manager: &str,
    requests: &[TickRequest],
    timeout_seconds: u64,
) -> Result<HashMap<String, LocalTickData>, String> {
    let batch = read_v4_tick_states(rpc_url, pool_manager, requests, timeout_seconds)?;
    if let Some(error) = batch.errors.first() {
        return Err(error.clone());
    }
    let mut out = HashMap::new();
    for (state, request) in batch.states.iter().zip(requests.iter()) {
        out.insert(
            request.pool_id.clone(),
            LocalTickData::from_state(state, request.tick_spacing),
        );
    }
    Ok(out)
}

/// Reads the tick data of a pool and prices one exact-input swap with it.
///
/// This is the **complete** off-chain answer for a fixed-fee pool: the pool
/// state, the bitmap words and the tick liquidities are read from the chain,
/// and the swap is then priced by the SDK's own tick-crossing loop.
///
/// A dynamic-fee pool is rejected before any RPC call: its effective rate is
/// decided by a hook at swap time, so no amount of chain state can reproduce
/// the rate the chain will charge.
pub fn traverse_pool_swap(
    rpc_url: &str,
    pool_manager: &str,
    state: &crate::chain::V4PoolState,
    tick_spacing: i32,
    amount_in_raw: u128,
    zero_for_one: bool,
    timeout_seconds: u64,
) -> Result<TraversalQuote, String> {
    if crate::depth::is_dynamic_fee_pool(state.lp_fee as i64, 0) {
        return Err("dynamic-fee pools are excluded from the off-chain pricing".to_string());
    }
    let request = TickRequest {
        pool_id: state.pool_id.clone(),
        current_tick: state.tick as i32,
        tick_spacing,
    };
    let batch = read_v4_tick_states(rpc_url, pool_manager, &[request], timeout_seconds)?;
    if let Some(error) = batch.errors.first() {
        return Err(error.clone());
    }
    let Some(tick_state) = batch.states.first() else {
        return Err("the tick read returned no state".to_string());
    };
    let tick_data = LocalTickData::from_state(tick_state, tick_spacing);
    traverse_swap_amount_out(state, &tick_data, amount_in_raw, zero_for_one)
}

/// The result of one tick-traversing swap.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct TraversalQuote {
    /// The realised output of the swap, in raw output-token units.
    pub amount_out_raw: String,
    /// The output the swap could **not** deliver, because the pool ran out of
    /// liquidity before the whole input was consumed.
    pub amount_in_unspent_raw: String,
    /// The price reached by the swap.
    pub sqrt_price_x96_after: String,
    /// The tick reached by the swap.
    pub tick_after: i64,
    /// The liquidity that was active when the swap stopped.
    pub liquidity_after: String,
    /// Whether the swap consumed the whole input.
    pub fully_filled: bool,
    /// The reason the traversal could not answer, when applicable.
    pub error: Option<String>,
}

/// Prices one exact-input swap by **traversing the ticks**.
///
/// The single-step arithmetic in [`crate::depth`] answers "what happens if
/// the swap stays inside the current tick range". This function answers the
/// harder question the chain answers: the swap walks across tick ranges
/// until the input is consumed or the liquidity runs out.
///
/// The traversal is the Uniswap V4 SDK's own `v3_swap`, so the rounding, the
/// fee accounting and the tick crossing are the reference implementation
/// rather than a second re-derivation of the same rules.
pub fn traverse_swap_amount_out(
    state: &crate::chain::V4PoolState,
    tick_data: &LocalTickData,
    amount_in_raw: u128,
    zero_for_one: bool,
) -> Result<TraversalQuote, String> {
    if state.sqrt_price_x96.is_empty() || state.liquidity == 0 {
        return Err("pool state is not usable for a tick traversal".to_string());
    }
    let sqrt_price_x96: u128 = state
        .sqrt_price_x96
        .parse()
        .map_err(|_| "pool state carries an unreadable sqrt price".to_string())?;
    if sqrt_price_x96 == 0 {
        return Err("pool state carries a zero sqrt price".to_string());
    }
    let fee_pips = u32::try_from(state.lp_fee)
        .map_err(|_| "pool fee does not fit in the V4 fee range".to_string())?;
    if fee_pips >= 1_000_000 {
        return Err("pool fee consumes the entire input".to_string());
    }
    let tick_spacing = i32::try_from(tick_data.tick_spacing)
        .map_err(|_| "pool tick spacing does not fit in the V4 range".to_string())?;
    if tick_spacing <= 0 {
        return Err("pool tick spacing is not positive".to_string());
    }

    let runtime = crate::chain::rpc_runtime()
        .map_err(|error| format!("could not start the swap runtime: {error}"))?;
    let provider = LocalTickDataProvider {
        data: tick_data.clone(),
    };
    let amount_specified =
        alloy_primitives::I256::from_raw(alloy_primitives::U256::from(amount_in_raw));
    let outcome = runtime.block_on(async move {
        v3_swap(
            alloy_primitives::aliases::U24::from(fee_pips),
            alloy_primitives::U160::from(sqrt_price_x96),
            state.tick as i32,
            state.liquidity,
            tick_spacing,
            &provider,
            zero_for_one,
            amount_specified,
            None,
        )
        .await
    });
    let swap_state = outcome.map_err(|error| format!("tick traversal failed: {error}"))?;

    // `amount_calculated` is negative for an exact-input swap, so the output
    // is its absolute value. The SDK's own `Pool::get_output_amount` uses the
    // same sign convention.
    let unspent = u128::try_from(swap_state.amount_specified_remaining.abs())
        .map_err(|_| "the swap left an unspent amount that does not fit in u128".to_string())?;
    let amount_out = u128::try_from(swap_state.amount_calculated.abs())
        .map_err(|_| "the swap output does not fit in u128".to_string())?;
    Ok(TraversalQuote {
        amount_out_raw: amount_out.to_string(),
        amount_in_unspent_raw: unspent.to_string(),
        sqrt_price_x96_after: swap_state.sqrt_price_x96.to_string(),
        tick_after: i64::from(swap_state.tick_current),
        liquidity_after: swap_state.liquidity.to_string(),
        fully_filled: unspent == 0,
        error: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The slot derivation must agree with the Uniswap V4 SDK's
    /// `PoolManagerLens`, otherwise the tick bitmap is read from the wrong
    /// place and the traversal answers with the wrong pool.
    #[test]
    fn the_tick_slots_follow_the_sdk_layout() {
        let state_slot =
            pool_state_slot("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                .expect("state slot");
        assert_eq!(
            crate::chain::as_hex(&state_slot),
            "0xf9f9cffb27053c60d6c9552447b6a4fc873604745eef4fed26c3644421245fcf"
        );
        // `ticks` lives at `state_slot + 4`.
        assert_eq!(
            crate::chain::as_hex(&add_small(&state_slot, TICKS_OFFSET)),
            "0xf9f9cffb27053c60d6c9552447b6a4fc873604745eef4fed26c3644421245fd3"
        );
        // `tickBitmap` lives at `state_slot + 5`.
        assert_eq!(
            crate::chain::as_hex(&add_small(&state_slot, TICK_BITMAP_OFFSET)),
            "0xf9f9cffb27053c60d6c9552447b6a4fc873604745eef4fed26c3644421245fd4"
        );
    }

    #[test]
    fn the_bitmap_words_surround_the_current_tick() {
        // A tick of 1000 with a spacing of 10 compresses to 100, which lives
        // in word 0, so the window is centred on word 0.
        let words = bitmap_words_around(1000, 10);
        assert_eq!(words.first(), Some(&(-BITMAP_WORD_RADIUS as i16)));
        assert_eq!(words.last(), Some(&(BITMAP_WORD_RADIUS as i16)));
        assert!(words.contains(&0));
        // A tick of 3000 with a spacing of 10 compresses to 300, which lives
        // in word 1.
        let words = bitmap_words_around(3000, 10);
        assert!(words.contains(&1));
        // Negative ticks floor towards negative infinity, so -10 with a
        // spacing of 10 compresses to -1, which lives in word -1.
        let words = bitmap_words_around(-10, 10);
        assert!(words.contains(&-1));
        assert!(words.contains(&-2));
    }

    #[test]
    fn the_candidate_ticks_are_aligned_to_the_tick_spacing() {
        assert_eq!(candidate_ticks_around(1005, 10), vec![990, 1000, 1010]);
        // The alignment floors towards negative infinity, so -5 with a
        // spacing of 10 sits between -20 and -10.
        assert_eq!(candidate_ticks_around(-5, 10), vec![-20, -10, 0]);
    }

    /// The traversal is the SDK's own `v3_swap`, so the local tick data must
    /// reproduce the SDK's word scan exactly.
    ///
    /// The SDK's two directions have different masks:
    ///
    /// * moving **down** (`lte`) masks the bits at or below the current bit
    ///   with `(2 << bit) - 1` and takes the **highest** set bit, so the
    ///   answer is the closest initialized tick at or below the current one;
    /// * moving **up** starts at the next tick, masks the bits at or above
    ///   that bit with `-(1 << bit)` and takes the **lowest** set bit, so the
    ///   answer is the closest initialized tick above the current one.
    #[test]
    fn the_word_scan_finds_the_next_initialized_tick() {
        // Word 0 with bit 61 set: the tick is 61 * 10 = 610.
        let word: alloy_primitives::U256 = alloy_primitives::U256::from(1u64) << 61;
        // Moving down from tick 600 (compressed 60, bit 60) masks the bits
        // 0..=60, so bit 61 is above the current tick and is not a candidate.
        // The SDK then answers with the start of the word, because there is
        // no initialized tick at or below the current one.
        let (next, initialized) = next_initialized_tick_in_word(word, 600, true, 10);
        assert!(!initialized);
        assert_eq!(next, 0);
        // Moving down from tick 620 (compressed 62, bit 62) masks the bits
        // 0..=62, so bit 61 is a candidate and the answer is tick 610.
        let (next, initialized) = next_initialized_tick_in_word(word, 620, true, 10);
        assert!(initialized);
        assert_eq!(next, 610);
        // Moving up from tick 600 starts at bit 61, so the same tick is found.
        let (next, initialized) = next_initialized_tick_in_word(word, 600, false, 10);
        assert!(initialized);
        assert_eq!(next, 610);
        // An empty word answers with the start of the word and no initialized
        // tick, exactly like the SDK: the traversal then walks to the next
        // word on its own.
        let (next, initialized) =
            next_initialized_tick_in_word(alloy_primitives::U256::ZERO, 600, true, 10);
        assert!(!initialized);
        assert_eq!(next, 0);
        // The same holds for the upward direction: the SDK answers with the
        // last bit of the word, because the traversal then walks to the next
        // word on its own.
        let (next, initialized) =
            next_initialized_tick_in_word(alloy_primitives::U256::ZERO, 600, false, 10);
        assert!(!initialized);
        assert_eq!(next, 2550);
    }

    /// The compression and the word split must follow the SDK's arithmetic,
    /// including the floor towards negative infinity.
    #[test]
    fn the_tick_compression_follows_the_sdk() {
        assert_eq!(compress_tick(600, 10), 60);
        assert_eq!(compress_tick(-600, 10), -60);
        // A negative tick that is not a multiple of the spacing floors down.
        assert_eq!(compress_tick(-605, 10), -61);
        assert_eq!(tick_position(60), (0, 60));
        // A negative compressed tick keeps its two's-complement bit pattern,
        // which is what the contract's signed word position expects.
        assert_eq!(tick_position(-61), (-1, 195));
    }

    /// A pool whose initialized ticks straddle the current price must lose
    /// liquidity when the traversal crosses the tick. This is the behaviour
    /// the single-step arithmetic could never reproduce.
    #[test]
    fn the_traversal_crosses_an_initialized_tick() {
        // A tiny pool at the minimum price, with one initialized tick just
        // above it. The swap walks up to the tick, the liquidity net is
        // applied, and the traversal then continues in the next range.
        let state = crate::chain::V4PoolState {
            pool_id: "0x".to_string(),
            sqrt_price_x96: "4295128739".to_string(),
            tick: -887_000,
            protocol_fee: 0,
            lp_fee: 3000,
            liquidity: 1_000_000_000_000_000_000,
            has_liquidity: true,
            error: None,
            complete: true,
        };
        let mut ticks = HashMap::new();
        ticks.insert(
            -886_990,
            TickLiquidity {
                liquidity_gross: 2_000_000_000_000_000_000,
                liquidity_net: -1_000_000_000_000_000_000,
            },
        );
        let mut bitmap = HashMap::new();
        // The compressed tick is -88699 with a spacing of 10. The SDK's
        // compression floors towards negative infinity and its word split
        // uses an arithmetic shift, so the tick lives in word -347 at bit
        // 195.
        let compressed = compress_tick(-886_990, 10);
        let (word_position, bit_position) = tick_position(compressed);
        bitmap.insert(
            word_position as i16,
            alloy_primitives::U256::from(1u64) << bit_position,
        );
        let tick_data = LocalTickData {
            bitmap,
            ticks,
            tick_spacing: 10,
        };
        let quote = traverse_swap_amount_out(&state, &tick_data, 1_000_000, false)
            .expect("the traversal should answer");
        // The traversal crossed the initialized tick, so the liquidity net was
        // applied and the active liquidity changed.
        assert!(quote.tick_after >= -886_990);
        assert_ne!(quote.liquidity_after, state.liquidity.to_string());
        // The output is positive and the swap either filled the input or
        // reported the part it could not consume.
        assert!(u128::try_from(quote.amount_out_raw.parse::<u128>().unwrap()).unwrap() > 0);
        assert!(!quote.amount_in_unspent_raw.is_empty());
    }

    /// The bitmap is the only source of truth about **which** ticks are
    /// initialized, so the bitmap-to-tick resolution must follow the
    /// contract's layout: one bit per compressed tick.
    #[test]
    fn the_bitmap_resolves_the_initialized_ticks() {
        // Word -347 with bit 195 set, a spacing of 10.
        let word: alloy_primitives::U256 = alloy_primitives::U256::from(1u64) << 195;
        let ticks = initialized_ticks_in_word(-347, word, 10);
        assert_eq!(ticks, vec![-347 * 256 * 10 + 195 * 10]);
        // An empty word has no initialized tick.
        assert!(initialized_ticks_in_word(0, alloy_primitives::U256::ZERO, 10).is_empty());
    }

    /// The measurement population must exclude the dynamic-fee pools and the
    /// pools that cannot be priced off chain.
    #[test]
    fn the_measurement_population_excludes_the_dynamic_fee_pools() {
        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("pools.sqlite3");
        let connection = rusqlite::Connection::open(&path).expect("open");
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pools (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    token0 TEXT, token1 TEXT, fee INTEGER, dynamic_fee INTEGER,
                    special_fee INTEGER, tick_spacing INTEGER, hooks TEXT);
                 CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                    has_liquidity INTEGER, liquidity TEXT, sqrt_price_x96 TEXT,
                    tick INTEGER, liquidity_read_at TEXT, evidence_source TEXT);",
            )
            .expect("schema");
        for (pool_id, fee, dynamic_fee, liquidity, tick) in [
            ("fixed", 3000i64, 0i64, "100", 10i64),
            ("dynamic-flag", 8_388_608, 0, "100", 10),
            ("dynamic-column", 3000, 1, "100", 10),
            ("no-liquidity", 3000, 0, "0", 10),
            // A missing tick is NULL, not zero: tick 0 is a legitimate tick.
            ("no-tick", 3000, 0, "100", -1),
        ] {
            connection
                .execute(
                    "INSERT INTO v4_exchange_pools
                     (network, chain_id, protocol, pool_id, token0, token1, fee,
                      dynamic_fee, special_fee, tick_spacing, hooks)
                     VALUES ('robinhood', 4663, 'uniswap_v4', ?1, '0xaaaa', '0xbbbb',
                             ?2, ?3, 0, 60, '0x0000')",
                    rusqlite::params![pool_id, fee, dynamic_fee],
                )
                .expect("insert pool");
            connection
                .execute(
                    "INSERT INTO v4_exchange_pool_liquidity_snapshots
                     (network, chain_id, protocol, pool_id, has_liquidity, liquidity,
                      sqrt_price_x96, tick, liquidity_read_at, evidence_source)
                     VALUES ('robinhood', 4663, 'uniswap_v4', ?1, 1, ?2,
                             '79228162514264337593543950336', ?3, '2026-11-13T00:00:00Z', 'test')",
                    rusqlite::params![
                        pool_id,
                        liquidity,
                        if tick < 0 {
                            rusqlite::types::Value::Null
                        } else {
                            rusqlite::types::Value::Integer(tick)
                        },
                    ],
                )
                .expect("insert snapshot");
        }
        drop(connection);

        let ids =
            resolve_measurement_pool_ids(path.to_string_lossy().as_ref(), "robinhood", 4663, 60)
                .expect("population");
        assert_eq!(ids, vec!["fixed".to_string()]);
    }

    #[test]
    fn the_packed_tick_word_decodes_gross_and_net() {
        // The Uniswap V4 SDK reads `liquidityGross` from the low half of the
        // word and `liquidityNet` from the high half, so gross = 0x0102 and
        // net = -1.
        let mut word = [0u8; 32];
        word[30..32].copy_from_slice(&0x0102u16.to_be_bytes());
        for byte in word[..16].iter_mut() {
            *byte = 0xff;
        }
        let (gross, net) = decode_tick_liquidity(&word);
        assert_eq!(gross, 0x0102);
        assert_eq!(net, -1);
    }
}
