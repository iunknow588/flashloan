use crate::model::{AdmissionStatus, TokenWrite};
use manager_contracts::{normalize_address, TokenKeyId};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fmt;
use std::str::FromStr;

pub const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum MetadataSourceKind {
    ChainTokens,
    StockRegistry,
    CoreAllowlist,
}

impl MetadataSourceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ChainTokens => "chain_tokens",
            Self::StockRegistry => "stock_registry",
            Self::CoreAllowlist => "core_allowlist",
        }
    }

    pub fn precedence(self) -> u8 {
        match self {
            Self::CoreAllowlist => 3,
            Self::StockRegistry => 2,
            Self::ChainTokens => 1,
        }
    }
}

impl fmt::Display for MetadataSourceKind {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RawTokenMetadata {
    pub source: MetadataSourceKind,
    pub source_locator: String,
    pub network: String,
    pub chain_id: u64,
    pub token_address: Option<String>,
    pub token_symbol: Option<String>,
    pub token_name: Option<String>,
    pub token_decimals: Option<i64>,
    pub token_kind: Option<String>,
    pub is_native: bool,
    pub is_wrapped_native: bool,
    pub is_stable: bool,
    pub admission_source: Option<String>,
    pub metadata_source: Option<String>,
    pub expected_token_key_id: Option<String>,
    pub token_metadata_json: Option<String>,
    pub source_json: Option<String>,
    pub first_seen_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NormalizedTokenMetadata {
    pub source: MetadataSourceKind,
    pub source_locator: String,
    pub token_key_id: TokenKeyId,
    pub v4_currency_key: String,
    pub token_address: String,
    pub token_symbol: String,
    pub token_name: Option<String>,
    pub token_decimals: u8,
    pub token_kind: String,
    pub is_native: bool,
    pub is_wrapped_native: bool,
    pub is_stable: bool,
    pub admission_source: String,
    pub metadata_source: String,
    pub metadata_checksum: String,
    pub source_json: String,
    pub first_seen_at: Option<String>,
}

impl NormalizedTokenMetadata {
    pub fn to_token_write(&self, admission_status: AdmissionStatus) -> TokenWrite {
        TokenWrite {
            network: self.token_key_id.network().to_owned(),
            chain_id: self.token_key_id.chain_id(),
            token_key_id: self.token_key_id.clone(),
            v4_currency_key: self.v4_currency_key.clone(),
            token_address: self.token_address.clone(),
            token_symbol: self.token_symbol.clone(),
            token_name: self.token_name.clone(),
            token_decimals: self.token_decimals,
            token_kind: self.token_kind.clone(),
            is_native: self.is_native,
            is_wrapped_native: self.is_wrapped_native,
            is_stable: self.is_stable,
            admission_status,
            admission_source: self.admission_source.clone(),
            metadata_source: self.metadata_source.clone(),
            metadata_checksum: self.metadata_checksum.clone(),
            source_json: Some(self.source_json.clone()),
            first_seen_at: self.first_seen_at.clone(),
            blocked_reason: None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum MetadataAuditCode {
    MissingAddress,
    InvalidAddress,
    TokenKeyMismatch,
    MissingSymbol,
    PlaceholderSymbol,
    MissingDecimals,
    InvalidDecimals,
    MissingTokenKind,
    InvalidTokenKind,
    InvalidFlags,
    MissingSource,
    InvalidJson,
}

impl MetadataAuditCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::MissingAddress => "MISSING_ADDRESS",
            Self::InvalidAddress => "INVALID_ADDRESS",
            Self::TokenKeyMismatch => "TOKEN_KEY_MISMATCH",
            Self::MissingSymbol => "MISSING_SYMBOL",
            Self::PlaceholderSymbol => "PLACEHOLDER_SYMBOL",
            Self::MissingDecimals => "MISSING_DECIMALS",
            Self::InvalidDecimals => "INVALID_DECIMALS",
            Self::MissingTokenKind => "MISSING_TOKEN_KIND",
            Self::InvalidTokenKind => "INVALID_TOKEN_KIND",
            Self::InvalidFlags => "INVALID_FLAGS",
            Self::MissingSource => "MISSING_SOURCE",
            Self::InvalidJson => "INVALID_JSON",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MetadataAuditError {
    pub code: MetadataAuditCode,
    pub source: MetadataSourceKind,
    pub source_locator: String,
    pub token_address: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MetadataNormalizationResult {
    pub accepted: Vec<NormalizedTokenMetadata>,
    pub errors: Vec<MetadataAuditError>,
}

pub fn normalize_metadata_batch(
    records: impl IntoIterator<Item = RawTokenMetadata>,
) -> MetadataNormalizationResult {
    let mut result = MetadataNormalizationResult::default();
    for record in records {
        match normalize_metadata(record) {
            Ok(token) => result.accepted.push(token),
            Err(error) => result.errors.push(error),
        }
    }
    result
}

pub fn normalize_metadata(
    record: RawTokenMetadata,
) -> Result<NormalizedTokenMetadata, MetadataAuditError> {
    let token_address = required_address(&record)?;
    let token_key_id =
        TokenKeyId::new(&record.network, record.chain_id, &token_address).map_err(|error| {
            audit_error(
                &record,
                MetadataAuditCode::InvalidAddress,
                error.to_string(),
            )
        })?;
    if let Some(expected) = non_empty(record.expected_token_key_id.as_deref()) {
        let expected = TokenKeyId::from_str(expected).map_err(|error| {
            audit_error(
                &record,
                MetadataAuditCode::TokenKeyMismatch,
                error.to_string(),
            )
        })?;
        if expected != token_key_id {
            return Err(audit_error(
                &record,
                MetadataAuditCode::TokenKeyMismatch,
                format!("expected {expected}, computed {token_key_id}"),
            ));
        }
    }

    let metadata_json = parse_json_object(record.token_metadata_json.as_deref(), &record)?;
    let token_symbol = required_symbol(&record, metadata_json.as_ref())?;
    let token_decimals = required_decimals(&record, metadata_json.as_ref())?;
    let token_kind = required_kind(&record)?;
    let source_json = parse_json_object(record.source_json.as_deref(), &record)?;
    let metadata_source = first_non_empty(&[
        record.metadata_source.as_deref(),
        metadata_json
            .as_ref()
            .and_then(|value| value.get("source"))
            .and_then(serde_json::Value::as_str),
        metadata_json
            .as_ref()
            .and_then(|value| value.get("seed"))
            .and_then(serde_json::Value::as_str),
        source_json
            .as_ref()
            .and_then(|value| value.get("source"))
            .and_then(serde_json::Value::as_str),
        source_json
            .as_ref()
            .and_then(|value| value.get("seed"))
            .and_then(serde_json::Value::as_str),
    ])
    .ok_or_else(|| {
        audit_error(
            &record,
            MetadataAuditCode::MissingSource,
            "metadata source must be explicit",
        )
    })?;
    let admission_source = non_empty(record.admission_source.as_deref())
        .map(str::to_owned)
        .ok_or_else(|| {
            audit_error(
                &record,
                MetadataAuditCode::MissingSource,
                "admission source must be explicit",
            )
        })?;
    validate_flags(
        &record,
        &token_address,
        &token_kind,
        record.is_native,
        record.is_wrapped_native,
        record.is_stable,
    )?;

    let source_json = serde_json::json!({
        "sourceKind": record.source.as_str(),
        "sourceLocator": record.source_locator,
        "metadata": metadata_json.clone().unwrap_or_else(|| serde_json::json!({})),
        "source": source_json.unwrap_or_else(|| serde_json::json!({})),
    })
    .to_string();
    let token_name = non_empty(record.token_name.as_deref())
        .map(str::to_owned)
        .or_else(|| {
            metadata_json
                .as_ref()
                .and_then(|value| value.get("name"))
                .and_then(serde_json::Value::as_str)
                .and_then(|value| non_empty(Some(value)))
                .map(str::to_owned)
        });
    let mut token = NormalizedTokenMetadata {
        source: record.source,
        source_locator: record.source_locator,
        token_key_id,
        v4_currency_key: token_address.clone(),
        token_address,
        token_symbol,
        token_name,
        token_decimals,
        token_kind,
        is_native: record.is_native,
        is_wrapped_native: record.is_wrapped_native,
        is_stable: record.is_stable,
        admission_source,
        metadata_source,
        metadata_checksum: String::new(),
        source_json,
        first_seen_at: record.first_seen_at,
    };
    token.metadata_checksum = token_metadata_checksum(&token);
    Ok(token)
}

pub fn token_metadata_checksum(token: &NormalizedTokenMetadata) -> String {
    #[derive(Serialize)]
    #[serde(rename_all = "camelCase")]
    struct CanonicalTokenMetadata<'a> {
        token_key_id: &'a str,
        v4_currency_key: &'a str,
        token_address: &'a str,
        token_symbol: &'a str,
        token_name: Option<&'a str>,
        token_decimals: u8,
        token_kind: &'a str,
        is_native: bool,
        is_wrapped_native: bool,
        is_stable: bool,
        admission_source: &'a str,
        metadata_source: &'a str,
    }

    let payload = CanonicalTokenMetadata {
        token_key_id: token.token_key_id.as_str(),
        v4_currency_key: &token.v4_currency_key,
        token_address: &token.token_address,
        token_symbol: &token.token_symbol,
        token_name: token.token_name.as_deref(),
        token_decimals: token.token_decimals,
        token_kind: &token.token_kind,
        is_native: token.is_native,
        is_wrapped_native: token.is_wrapped_native,
        is_stable: token.is_stable,
        admission_source: &token.admission_source,
        metadata_source: &token.metadata_source,
    };
    let digest =
        Sha256::digest(serde_json::to_vec(&payload).expect("serializing metadata cannot fail"));
    format!("sha256:{}", hex::encode(digest))
}

pub fn is_placeholder_symbol(value: &str) -> bool {
    let normalized = value.trim().to_ascii_uppercase();
    if normalized.starts_with("UNRESOLVED:") {
        return true;
    }
    normalized.strip_prefix("0X").is_some_and(|hex| {
        (6..=8).contains(&hex.len()) && hex.bytes().all(|byte| byte.is_ascii_hexdigit())
    })
}

fn required_address(record: &RawTokenMetadata) -> Result<String, MetadataAuditError> {
    let raw = non_empty(record.token_address.as_deref()).ok_or_else(|| {
        audit_error(
            record,
            MetadataAuditCode::MissingAddress,
            "token address is required",
        )
    })?;
    normalize_address(raw)
        .map_err(|error| audit_error(record, MetadataAuditCode::InvalidAddress, error.to_string()))
}

fn required_symbol(
    record: &RawTokenMetadata,
    metadata_json: Option<&serde_json::Value>,
) -> Result<String, MetadataAuditError> {
    if let Some(symbol) = non_empty(record.token_symbol.as_deref()) {
        if !is_placeholder_symbol(symbol) {
            return Ok(symbol.to_uppercase());
        }
        if let Some(fallback) = metadata_json_symbol(metadata_json) {
            if !is_placeholder_symbol(&fallback) {
                return Ok(fallback.to_uppercase());
            }
        }
        return Err(audit_error(
            record,
            MetadataAuditCode::PlaceholderSymbol,
            format!("placeholder symbol is not allowed: {symbol}"),
        ));
    }
    if let Some(fallback) = metadata_json_symbol(metadata_json) {
        if !is_placeholder_symbol(&fallback) {
            return Ok(fallback.to_uppercase());
        }
    }
    Err(audit_error(
        record,
        MetadataAuditCode::MissingSymbol,
        "token symbol is required",
    ))
}

fn required_decimals(
    record: &RawTokenMetadata,
    metadata_json: Option<&serde_json::Value>,
) -> Result<u8, MetadataAuditError> {
    let decimals = record
        .token_decimals
        .or_else(|| metadata_json_decimals(metadata_json))
        .ok_or_else(|| {
            audit_error(
                record,
                MetadataAuditCode::MissingDecimals,
                "token decimals are required",
            )
        })?;
    u8::try_from(decimals)
        .ok()
        .filter(|value| *value <= 36)
        .ok_or_else(|| {
            audit_error(
                record,
                MetadataAuditCode::InvalidDecimals,
                format!("token decimals must be between 0 and 36, got {decimals}"),
            )
        })
}

fn metadata_json_symbol(metadata_json: Option<&serde_json::Value>) -> Option<String> {
    metadata_json?
        .get("symbol")?
        .as_str()
        .and_then(|value| non_empty(Some(value)))
        .map(str::to_owned)
}

fn metadata_json_decimals(metadata_json: Option<&serde_json::Value>) -> Option<i64> {
    let metadata_json = metadata_json?;
    metadata_json
        .get("decimals")
        .and_then(serde_json::Value::as_i64)
        .or_else(|| {
            metadata_json
                .get("decimals")
                .and_then(serde_json::Value::as_str)
                .and_then(|value| value.parse().ok())
        })
}

fn required_kind(record: &RawTokenMetadata) -> Result<String, MetadataAuditError> {
    let kind = non_empty(record.token_kind.as_deref()).ok_or_else(|| {
        audit_error(
            record,
            MetadataAuditCode::MissingTokenKind,
            "token kind is required",
        )
    })?;
    let kind = kind.to_ascii_lowercase();
    if !matches!(
        kind.as_str(),
        "native" | "wrapped_native" | "erc20" | "stable"
    ) {
        return Err(audit_error(
            record,
            MetadataAuditCode::InvalidTokenKind,
            format!("unsupported token kind: {kind}"),
        ));
    }
    Ok(kind)
}

fn parse_json_object(
    value: Option<&str>,
    record: &RawTokenMetadata,
) -> Result<Option<serde_json::Value>, MetadataAuditError> {
    let Some(value) = non_empty(value) else {
        return Ok(None);
    };
    let parsed = serde_json::from_str::<serde_json::Value>(value)
        .map_err(|error| audit_error(record, MetadataAuditCode::InvalidJson, error.to_string()))?;
    if !parsed.is_object() {
        return Err(audit_error(
            record,
            MetadataAuditCode::InvalidJson,
            "JSON metadata must be an object",
        ));
    }
    Ok(Some(parsed))
}

fn validate_flags(
    record: &RawTokenMetadata,
    token_address: &str,
    token_kind: &str,
    is_native: bool,
    is_wrapped_native: bool,
    is_stable: bool,
) -> Result<(), MetadataAuditError> {
    let is_zero_address = token_address == ZERO_ADDRESS;
    let valid = if token_kind == "native" {
        is_zero_address && is_native && !is_wrapped_native && !is_stable
    } else if token_kind == "wrapped_native" {
        !is_zero_address && !is_native && is_wrapped_native && !is_stable
    } else if token_kind == "stable" {
        !is_zero_address && !is_native && !is_wrapped_native && is_stable
    } else {
        !is_zero_address && !is_native && !is_wrapped_native
    };
    if valid {
        Ok(())
    } else {
        Err(audit_error(
            record,
            MetadataAuditCode::InvalidFlags,
            "native, wrapped-native, stable and token-kind flags are inconsistent",
        ))
    }
}

fn first_non_empty(values: &[Option<&str>]) -> Option<String> {
    values
        .iter()
        .flatten()
        .find_map(|value| non_empty(Some(value)).map(str::to_owned))
}

fn non_empty(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn audit_error(
    record: &RawTokenMetadata,
    code: MetadataAuditCode,
    message: impl Into<String>,
) -> MetadataAuditError {
    MetadataAuditError {
        code,
        source: record.source,
        source_locator: record.source_locator.clone(),
        token_address: record.token_address.clone(),
        message: message.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        is_placeholder_symbol, normalize_metadata, normalize_metadata_batch, MetadataAuditCode,
        MetadataSourceKind, RawTokenMetadata, ZERO_ADDRESS,
    };
    use crate::model::AdmissionStatus;

    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const WETH: &str = "0x2222222222222222222222222222222222222222";

    fn fixture(address: &str, symbol: &str, kind: &str, decimals: Option<i64>) -> RawTokenMetadata {
        RawTokenMetadata {
            source: MetadataSourceKind::ChainTokens,
            source_locator: format!("chain_tokens:robinhood:4663:{address}"),
            network: "Robinhood".to_owned(),
            chain_id: 4663,
            token_address: Some(address.to_ascii_uppercase().replace("0X", "0x")),
            token_symbol: Some(format!(" {symbol} ")),
            token_name: Some(format!(" {symbol} token ")),
            token_decimals: decimals,
            token_kind: Some(kind.to_owned()),
            is_native: kind == "native",
            is_wrapped_native: kind == "wrapped_native",
            is_stable: kind == "stable",
            admission_source: Some("bootstrap:chain_tokens".to_owned()),
            metadata_source: Some("fixture_source".to_owned()),
            expected_token_key_id: None,
            token_metadata_json: Some(r#"{"category":"fixture"}"#.to_owned()),
            source_json: Some(r#"{"seed":"fixture"}"#.to_owned()),
            first_seen_at: Some("2026-11-03T00:00:00Z".to_owned()),
        }
    }

    #[test]
    fn normalization_is_deterministic_and_converts_to_token_write() {
        let raw = fixture(USDG, "usdg", "stable", Some(6));
        let first = normalize_metadata(raw.clone()).unwrap();
        let second = normalize_metadata(raw).unwrap();

        assert_eq!(first, second);
        assert_eq!(
            first.token_key_id.as_str(),
            format!("robinhood:4663:{USDG}")
        );
        assert_eq!(first.token_symbol, "USDG");
        assert_eq!(first.token_decimals, 6);
        assert!(first.metadata_checksum.starts_with("sha256:"));

        let write = first.to_token_write(AdmissionStatus::Admitted);
        assert_eq!(write.token_key_id, first.token_key_id);
        assert_eq!(write.metadata_checksum, first.metadata_checksum);
    }

    #[test]
    fn native_uses_zero_address_and_remains_distinct_from_weth() {
        let native = fixture(ZERO_ADDRESS, "ETH", "native", Some(18));
        let weth = fixture(WETH, "WETH", "wrapped_native", Some(18));
        let native = normalize_metadata(native).unwrap();
        let weth = normalize_metadata(weth).unwrap();

        assert_eq!(native.v4_currency_key, ZERO_ADDRESS);
        assert_eq!(native.token_symbol, "ETH");
        assert_eq!(weth.token_symbol, "WETH");
        assert_ne!(native.token_key_id, weth.token_key_id);
    }

    #[test]
    fn accepted_and_error_split_is_stable() {
        let mut missing_symbol = fixture(USDG, "USDG", "stable", Some(6));
        missing_symbol.token_symbol = Some("   ".to_owned());
        let mut placeholder = fixture(USDG, "0X12345678", "stable", Some(6));
        placeholder.token_symbol = Some("0X12345678".to_owned());
        let mut missing_decimals = fixture(USDG, "USDG", "stable", None);
        missing_decimals.token_symbol = Some("USDGX".to_owned());
        let mut missing_source = fixture(WETH, "WETHX", "erc20", Some(18));
        missing_source.metadata_source = None;
        missing_source.token_metadata_json = Some("{}".to_owned());
        missing_source.source_json = Some("{}".to_owned());

        let result = normalize_metadata_batch(vec![
            fixture(USDG, "USDG", "stable", Some(6)),
            missing_symbol,
            placeholder,
            missing_decimals,
            missing_source,
        ]);

        assert_eq!(result.accepted.len(), 1);
        assert_eq!(
            result
                .errors
                .iter()
                .map(|error| error.code)
                .collect::<Vec<_>>(),
            vec![
                MetadataAuditCode::MissingSymbol,
                MetadataAuditCode::PlaceholderSymbol,
                MetadataAuditCode::MissingDecimals,
                MetadataAuditCode::MissingSource
            ]
        );
    }

    #[test]
    fn invalid_address_key_decimals_json_and_flags_are_rejected() {
        let mut invalid_json = fixture(USDG, "USDG", "stable", Some(6));
        invalid_json.source_json = Some("{".to_owned());

        let mut token_key_mismatch = fixture(WETH, "WETHX", "erc20", Some(18));
        token_key_mismatch.expected_token_key_id =
            Some(format!("robinhood:4663:{USDG}").to_uppercase());

        let mut invalid_decimals = fixture(WETH, "WETHX", "erc20", Some(37));
        invalid_decimals.token_symbol = Some("WETHX".to_owned());

        let mut invalid_flags = fixture(WETH, "WETHX", "erc20", Some(18));
        invalid_flags.token_symbol = Some("WETHX".to_owned());
        invalid_flags.is_native = true;

        let mut invalid_address = fixture(USDG, "USDGX", "stable", Some(6));
        invalid_address.token_address = Some("0x1234".to_owned());

        let result = normalize_metadata_batch(vec![
            invalid_json,
            token_key_mismatch,
            invalid_decimals,
            invalid_flags,
            invalid_address,
        ]);
        assert_eq!(
            result
                .errors
                .iter()
                .map(|error| error.code)
                .collect::<Vec<_>>(),
            vec![
                MetadataAuditCode::InvalidJson,
                MetadataAuditCode::TokenKeyMismatch,
                MetadataAuditCode::InvalidDecimals,
                MetadataAuditCode::InvalidFlags,
                MetadataAuditCode::InvalidAddress
            ]
        );
    }

    #[test]
    fn placeholder_detection_matches_existing_python_rules() {
        assert!(is_placeholder_symbol("0X123456"));
        assert!(is_placeholder_symbol("0x12345678"));
        assert!(is_placeholder_symbol("unresolved:abc"));
        assert!(!is_placeholder_symbol("0X12345"));
        assert!(!is_placeholder_symbol("USDG"));
    }

    #[test]
    fn metadata_json_can_fill_placeholder_symbol_name_and_decimals() {
        let mut raw = fixture(USDG, "0X12345678", "erc20", None);
        raw.token_metadata_json = Some(
            r#"{
                "source": "chain_live_erc20",
                "symbol": "RIGGLE",
                "name": "Riggles",
                "decimals": 6
            }"#
            .to_owned(),
        );
        raw.token_name = None;
        raw.is_stable = false;
        raw.metadata_source = None;

        let token = normalize_metadata(raw).unwrap();
        assert_eq!(token.token_symbol, "RIGGLE");
        assert_eq!(token.token_name.as_deref(), Some("Riggles"));
        assert_eq!(token.token_decimals, 6);
        assert_eq!(token.metadata_source, "chain_live_erc20");
    }

    #[test]
    fn metadata_json_symbol_without_decimals_still_fails_cleanly() {
        let mut raw = fixture(USDG, "0X12345678", "erc20", None);
        raw.token_metadata_json = Some(
            r#"{
                "source": "chain_live_erc20",
                "symbol": "OPEN",
                "name": "Open pool"
            }"#
            .to_owned(),
        );
        raw.is_stable = false;

        let result = normalize_metadata_batch(vec![raw]);
        assert_eq!(result.accepted.len(), 0);
        assert_eq!(result.errors.len(), 1);
        assert_eq!(result.errors[0].code, MetadataAuditCode::MissingDecimals);
    }
}
