use crate::ids::IdError;
use crate::{normalize_address, V4PoolKey};
use std::fmt;
use tiny_keccak::{Hasher, Keccak};

pub const V4_DYNAMIC_FEE_FLAG: u32 = 8_388_608;
pub const V4_SPECIAL_FEE_THRESHOLD: u32 = 100_000;
pub const V4_MAX_FEE: u32 = 0x00ff_ffff;
pub const V4_MIN_TICK_SPACING: i32 = -8_388_608;
pub const V4_MAX_TICK_SPACING: i32 = 8_388_607;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PoolKeyError {
    InvalidAddress(IdError),
    CurrencyOrder {
        currency0: String,
        currency1: String,
    },
    SameCurrency(String),
    FeeOutOfRange(u32),
    TickSpacingOutOfRange(i32),
}

impl fmt::Display for PoolKeyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidAddress(error) => write!(formatter, "invalid pool key address: {error}"),
            Self::CurrencyOrder {
                currency0,
                currency1,
            } => write!(
                formatter,
                "currency0 must be smaller than currency1: {currency0} >= {currency1}"
            ),
            Self::SameCurrency(currency) => {
                write!(formatter, "pool currencies must be distinct: {currency}")
            }
            Self::FeeOutOfRange(fee) => {
                write!(formatter, "fee {fee} is outside the V4 uint24 range")
            }
            Self::TickSpacingOutOfRange(tick_spacing) => {
                write!(
                    formatter,
                    "tick spacing {tick_spacing} is outside the V4 int24 range"
                )
            }
        }
    }
}

impl std::error::Error for PoolKeyError {}

impl From<IdError> for PoolKeyError {
    fn from(error: IdError) -> Self {
        Self::InvalidAddress(error)
    }
}

impl V4PoolKey {
    pub fn normalized(&self) -> Result<Self, PoolKeyError> {
        let currency0 = normalize_address(&self.currency0)?;
        let currency1 = normalize_address(&self.currency1)?;
        let hooks = normalize_address(&self.hooks)?;
        if currency0 == currency1 {
            return Err(PoolKeyError::SameCurrency(currency0));
        }
        if currency0 >= currency1 {
            return Err(PoolKeyError::CurrencyOrder {
                currency0,
                currency1,
            });
        }
        if self.fee > V4_MAX_FEE {
            return Err(PoolKeyError::FeeOutOfRange(self.fee));
        }
        if !(V4_MIN_TICK_SPACING..=V4_MAX_TICK_SPACING).contains(&self.tick_spacing) {
            return Err(PoolKeyError::TickSpacingOutOfRange(self.tick_spacing));
        }
        Ok(Self {
            currency0,
            currency1,
            fee: self.fee,
            tick_spacing: self.tick_spacing,
            hooks,
        })
    }

    pub fn abi_encode(&self) -> Result<Vec<u8>, PoolKeyError> {
        let key = self.normalized()?;
        let mut bytes = Vec::with_capacity(32 * 5);
        bytes.extend_from_slice(&address_word(&key.currency0));
        bytes.extend_from_slice(&address_word(&key.currency1));
        bytes.extend_from_slice(&uint24_word(key.fee));
        bytes.extend_from_slice(&int24_word(key.tick_spacing));
        bytes.extend_from_slice(&address_word(&key.hooks));
        Ok(bytes)
    }

    pub fn pool_id(&self) -> Result<String, PoolKeyError> {
        let digest = keccak256(&self.abi_encode()?);
        Ok(format!("0x{}", hex::encode(digest)))
    }
}

pub fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut output = [0u8; 32];
    let mut hasher = Keccak::v256();
    hasher.update(data);
    hasher.finalize(&mut output);
    output
}

fn address_word(address: &str) -> [u8; 32] {
    let mut word = [0u8; 32];
    let raw = hex::decode(address.trim_start_matches("0x")).expect("validated address");
    word[12..].copy_from_slice(&raw);
    word
}

fn uint24_word(value: u32) -> [u8; 32] {
    let mut word = [0u8; 32];
    word[29..].copy_from_slice(&value.to_be_bytes()[1..]);
    word
}

fn int24_word(value: i32) -> [u8; 32] {
    let mut word = if value < 0 { [0xffu8; 32] } else { [0u8; 32] };
    word[29..].copy_from_slice(&(value as u32).to_be_bytes()[1..]);
    word
}

#[cfg(test)]
mod tests {
    use super::V4PoolKey;

    fn native_pool_key() -> V4PoolKey {
        V4PoolKey {
            currency0: "0x0000000000000000000000000000000000000000".to_owned(),
            currency1: "0x0040070833df5bcf9644a5c9732a1fe906c27777".to_owned(),
            fee: 10_000,
            tick_spacing: 200,
            hooks: "0x0000000000000000000000000000000000000000".to_owned(),
        }
    }

    #[test]
    fn pool_key_serialization_is_camel_case() {
        let key = V4PoolKey {
            currency0: "0x0000000000000000000000000000000000000000".to_owned(),
            currency1: "0x2222222222222222222222222222222222222222".to_owned(),
            fee: 3000,
            tick_spacing: 60,
            hooks: "0x0000000000000000000000000000000000000000".to_owned(),
        };
        let value = serde_json::to_value(key).unwrap();
        assert_eq!(value["tickSpacing"], 60);
        assert!(value.get("tick_spacing").is_none());
    }

    #[test]
    fn pool_id_matches_mainnet_chain_truth_golden_vector() {
        let key = native_pool_key();
        assert_eq!(key.abi_encode().unwrap().len(), 160);
        assert_eq!(
            key.pool_id().unwrap(),
            "0x0061a6568e820becb465c3e6c8817bba1e8b6a91feeef04756bf6e15263cd252"
        );
    }

    #[test]
    fn normalization_and_int24_sign_extension_are_stable() {
        let mut key = native_pool_key();
        key.currency1 = key.currency1.to_ascii_uppercase().replace("0X", "0x");
        key.tick_spacing = -200;
        let encoded = key.abi_encode().unwrap();
        assert_eq!(&encoded[64..93], &[0u8; 29]);
        assert_eq!(&encoded[93..96], &[0x00, 0x27, 0x10]);
        assert_eq!(&encoded[96..125], &[0xffu8; 29]);
        assert_eq!(&encoded[125..128], &[0xff, 0xff, 0x38]);
        assert!(key.pool_id().unwrap().starts_with("0x"));
    }
}
