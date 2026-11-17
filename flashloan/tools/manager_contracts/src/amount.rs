//! Lossless serialization for amounts that do not fit a JSON number.
//!
//! A folded single-tick capacity is a 128-bit quantity. The capacity of
//! a deep pool, folded across three hops and expressed in the anchor
//! token, regularly exceeds `u64::MAX`; one testnet loop measured
//! `2.76e30`.
//!
//! `serde_json` writes a JSON number as a 64-bit integer or a double, so
//! it refuses a `u128` that does not fit with the error "number out of
//! range". Because that error happens while a whole Manager view is being
//! assembled, it takes the *entire* view down with a 503 instead of
//! blanking one field.
//!
//! The decimal string is lossless, is what the database already stores,
//! and is what the consumer already reads back (`int(str(value))`). It
//! is therefore the representation every other layer of the system uses,
//! and the one the Manager views must use as well.

use serde::{Deserialize, Deserializer, Serializer};

/// Serializes an optional 128-bit amount as a decimal string.
pub fn serialize_optional_u128_as_string<S>(
    value: &Option<u128>,
    serializer: S,
) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    match value {
        Some(amount) => serializer.serialize_str(&amount.to_string()),
        None => serializer.serialize_none(),
    }
}

/// Reads back a decimal string into an optional 128-bit amount.
///
/// A JSON number is accepted too, so a view that was written by an older
/// build still reads back.
pub fn deserialize_optional_u128_from_string<'de, D>(
    deserializer: D,
) -> Result<Option<u128>, D::Error>
where
    D: Deserializer<'de>,
{
    let raw = Option::<serde_json::Value>::deserialize(deserializer)?;
    Ok(match raw {
        None | Some(serde_json::Value::Null) => None,
        Some(serde_json::Value::String(text)) => text.trim().parse::<u128>().ok(),
        Some(serde_json::Value::Number(number)) => number.to_string().parse::<u128>().ok(),
        Some(_) => None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::{Deserialize, Serialize};

    #[derive(Debug, PartialEq, Eq, Serialize, Deserialize)]
    struct Holder {
        #[serde(
            serialize_with = "serialize_optional_u128_as_string",
            deserialize_with = "deserialize_optional_u128_from_string"
        )]
        amount: Option<u128>,
    }

    #[test]
    fn an_amount_beyond_u64_is_written_as_a_string_and_read_back() {
        let value = 2_760_429_638_958_378_160_494_971_116_143u128;
        let holder = Holder {
            amount: Some(value),
        };
        let encoded = serde_json::to_string(&holder).expect("encode");
        assert!(encoded.contains("2760429638958378160494971116143"));
        assert_eq!(serde_json::from_str::<Holder>(&encoded).unwrap(), holder);
    }

    #[test]
    fn a_missing_amount_stays_missing() {
        let encoded = serde_json::to_string(&Holder { amount: None }).expect("encode");
        assert_eq!(encoded, "{\"amount\":null}");
        assert_eq!(
            serde_json::from_str::<Holder>(&encoded).unwrap(),
            Holder { amount: None }
        );
    }

    #[test]
    fn a_json_number_from_an_older_build_is_still_readable() {
        let decoded: Holder = serde_json::from_str("{\"amount\":42}").expect("decode");
        assert_eq!(decoded.amount, Some(42u128));
    }
}
