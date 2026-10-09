use hex::decode as hex_decode;
use serde_json::{json, Value};
use tiny_keccak::{Hasher, Keccak};

use crate::config::{
    address_from_topic, canonical_address, normalize_address, V4_DYNAMIC_FEE_FLAG,
    V4_SPECIAL_FEE_THRESHOLD, ZERO_ADDRESS,
};
use crate::hyperrpc::Log;

pub const V4_INITIALIZE_SIGNATURE: &str =
    "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)";

#[derive(Clone, Debug)]
pub struct V4PoolKey {
    pub currency0: String,
    pub currency1: String,
    pub fee: u64,
    pub tick_spacing: i32,
    pub hooks: String,
}

#[derive(Clone, Debug)]
pub struct V4InitializeRow {
    pub pool_id: String,
    pub pool_key: V4PoolKey,
    pub sqrt_price_x96: String,
    pub tick: i32,
    pub block_number: u64,
    pub transaction_hash: String,
}

fn as_bytes(value: &str) -> Result<Vec<u8>, String> {
    let raw = value.strip_prefix("0x").unwrap_or(value);
    hex_decode(raw).map_err(|error| format!("invalid hex data {value}: {error}"))
}

fn u256_be(bytes: &[u8], offset: usize) -> Result<u128, String> {
    if offset + 32 > bytes.len() {
        return Err(format!("short ABI data at {offset}"));
    }
    let value = bytes[offset..offset + 32].iter().fold(0u128, |acc, byte| (acc << 8) | u128::from(*byte));
    Ok(value)
}

fn i256_be(bytes: &[u8], offset: usize) -> Result<i128, String> {
    if offset + 32 > bytes.len() {
        return Err(format!("short ABI data at {offset}"));
    }
    let raw = &bytes[offset..offset + 32];
    let negative = raw[0] & 0x80 != 0;
    let mut unsigned = 0u128;
    for byte in raw {
        unsigned = (unsigned << 8) | u128::from(*byte);
    }
    if !negative {
        return Ok(unsigned as i128);
    }
    let magnitude = (!unsigned).wrapping_add(1);
    Ok(-(magnitude as i128))
}

fn address_from_abi_word(bytes: &[u8], offset: usize) -> Result<String, String> {
    if offset + 32 > bytes.len() {
        return Err(format!("short ABI address at {offset}"));
    }
    Ok(format!("0x{}", hex::encode(&bytes[offset + 12..offset + 32])).to_lowercase())
}

fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut output = [0u8; 32];
    let mut hasher = Keccak::v256();
    hasher.update(data);
    hasher.finalize(&mut output);
    output
}

pub fn initialize_topic() -> String {
    let digest = keccak256(V4_INITIALIZE_SIGNATURE.as_bytes());
    format!("0x{}", hex::encode(digest))
}

fn pool_key_bytes(key: &V4PoolKey) -> Result<Vec<u8>, String> {
    let currency0 = canonical_address(&key.currency0);
    let currency1 = canonical_address(&key.currency1);
    let hooks = normalize_address(&key.hooks);
    if currency0 >= currency1 {
        return Err(format!("currency0 must be smaller than currency1: {currency0} >= {currency1}"));
    }
    let mut bytes = Vec::with_capacity(32 + 32 + 32 + 32 + 32);
    let addr0 = as_bytes(&currency0)?;
    bytes.extend_from_slice(&[0u8; 12]);
    bytes.extend_from_slice(&addr0);
    let addr1 = as_bytes(&currency1)?;
    bytes.extend_from_slice(&[0u8; 12]);
    bytes.extend_from_slice(&addr1);
    bytes.extend_from_slice(&key.fee.to_be_bytes());
    bytes.extend_from_slice(&[0u8; 28]);
    bytes.extend_from_slice(&key.tick_spacing.to_be_bytes());
    let hook_addr = as_bytes(&hooks)?;
    bytes.extend_from_slice(&[0u8; 12]);
    bytes.extend_from_slice(&hook_addr);
    Ok(bytes)
}

pub fn pool_id(key: &V4PoolKey) -> Result<String, String> {
    let bytes = pool_key_bytes(key)?;
    Ok(format!("0x{}", hex::encode(keccak256(&bytes))).to_lowercase())
}

pub fn decode_initialize_log(log: &Log, _pool_manager: &str) -> Result<V4InitializeRow, String> {
    if log.topics.len() < 4 {
        return Err(format!("Initialize log has {} topics, expected 4", log.topics.len()));
    }
    let currency0 = address_from_topic(&log.topics[2])?;
    let currency1 = address_from_topic(&log.topics[3])?;
    let bytes = as_bytes(&log.data)?;
    let fee = u256_be(&bytes, 0)? as u64;
    let tick_spacing = i256_be(&bytes, 32)? as i32;
    let hooks = address_from_abi_word(&bytes, 64)?;
    let sqrt_price_x96 = u256_be(&bytes, 96)?;
    let tick = i256_be(&bytes, 128)? as i32;
    let hooks = if hooks == ZERO_ADDRESS { ZERO_ADDRESS.to_string() } else { hooks };
    let key = V4PoolKey {
        currency0,
        currency1,
        fee,
        tick_spacing,
        hooks,
    };
    let pool_id = pool_id(&key)?;
    Ok(V4InitializeRow {
        pool_id,
        pool_key: key,
        sqrt_price_x96: sqrt_price_x96.to_string(),
        tick,
        block_number: log.block_number,
        transaction_hash: log.transaction_hash.clone(),
    })
}

pub fn dynamic_fee(fee: u64) -> bool {
    fee > 0 && fee & V4_DYNAMIC_FEE_FLAG != 0
}

pub fn special_fee(fee: u64) -> bool {
    fee == 0 || dynamic_fee(fee) || fee > V4_SPECIAL_FEE_THRESHOLD
}

pub fn fee_label(fee: u64) -> Value {
    if dynamic_fee(fee) {
        json!("dynamic")
    } else if special_fee(fee) {
        json!("special")
    } else {
        json!(fee)
    }
}

pub fn row_to_json(row: &V4InitializeRow, pool_manager: &str) -> Value {
    json!({
        "poolId": row.pool_id,
        "poolIdentityMode": "v4_pool_id",
        "poolManager": normalize_address(pool_manager),
        "poolKey": {
            "currency0": row.pool_key.currency0,
            "currency1": row.pool_key.currency1,
            "token0": row.pool_key.currency0,
            "token1": row.pool_key.currency1,
            "fee": row.pool_key.fee,
            "tickSpacing": row.pool_key.tick_spacing,
            "hooks": row.pool_key.hooks,
            "sqrtPriceX96": row.sqrt_price_x96,
            "tick": row.tick,
            "poolId": row.pool_id,
            "poolKeyDecoded": true,
        },
        "token0": row.pool_key.currency0,
        "token1": row.pool_key.currency1,
        "fee": row.pool_key.fee,
        "feeLabel": fee_label(row.pool_key.fee),
        "dynamicFee": dynamic_fee(row.pool_key.fee),
        "specialFee": special_fee(row.pool_key.fee),
        "tickSpacing": row.pool_key.tick_spacing,
        "hooks": row.pool_key.hooks,
        "sqrtPriceX96": row.sqrt_price_x96,
        "tick": row.tick,
        "initializeBlockNumber": row.block_number,
        "initializeTransactionHash": row.transaction_hash,
        "source": "hyperrpc_initialize_logs",
        "status": "initialized_pool",
        "depthStatus": "initialized_unpriced",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keccak_is_stable() {
        let digest = keccak256(b"");
        assert_eq!(
            hex::encode(digest),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
        );
    }

    #[test]
    fn decode_initialize_log_uses_last_20_bytes_for_address_topics() {
        let log = Log {
            address: "0xpoolmanager".to_string(),
            topics: vec![
                initialize_topic(),
                "0x1234".to_string(),
                "0x0000000000000000000000000000000000000000".to_string(),
                "0x0000000000000000000000005fc5360d0400a0fd4f2af552add042d716f1d168".to_string(),
            ],
            data: format!(
                "0x{}",
                hex::encode([
                    // fee = 3000
                    0u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11, 184,
                    // tickSpacing = 60
                    0u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 60,
                    // hooks = zero address
                    0u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    // sqrtPriceX96 = 1
                    0u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
                    // tick = 0
                    0u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                ])
            ),
            block_number: 4663,
            transaction_hash: "0x123456".to_string(),
            log_index: Some(1),
        };
        let row = decode_initialize_log(&log, "0x8366a39cc670b4001a1121b8f6a443a643e40951").unwrap();
        assert_eq!(row.pool_key.currency0, ZERO_ADDRESS);
        assert_eq!(row.pool_key.currency1, "0x5fc5360d0400a0fd4f2af552add042d716f1d168");
        assert_eq!(row.pool_key.fee, 3000);
        assert_eq!(row.pool_key.tick_spacing, 60);
        assert_eq!(row.pool_key.hooks, ZERO_ADDRESS);
        assert!(!row.pool_id.is_empty());
    }
}
