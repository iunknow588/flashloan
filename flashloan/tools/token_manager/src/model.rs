use manager_contracts::TokenKeyId;
use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LibraryKey {
    Core,
    Stocks,
    Extension,
    Test,
}

impl LibraryKey {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Core => "core",
            Self::Stocks => "stocks",
            Self::Extension => "extension",
            Self::Test => "test",
        }
    }

    pub fn table_name(self) -> &'static str {
        match self {
            Self::Core => "token_core",
            Self::Stocks => "token_stocks",
            Self::Extension => "token_extension",
            Self::Test => "token_test",
        }
    }

    pub fn from_key(value: &str) -> Option<Self> {
        match value {
            "core" => Some(Self::Core),
            "stocks" => Some(Self::Stocks),
            "extension" => Some(Self::Extension),
            "test" => Some(Self::Test),
            _ => None,
        }
    }
}

impl fmt::Display for LibraryKey {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AdmissionStatus {
    Admitted,
    Blocked,
    CandidateOnly,
    PendingReview,
}

impl AdmissionStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Admitted => "admitted",
            Self::Blocked => "blocked",
            Self::CandidateOnly => "candidate_only",
            Self::PendingReview => "pending_review",
        }
    }

    pub fn from_storage(value: &str) -> Option<Self> {
        match value {
            "admitted" => Some(Self::Admitted),
            "blocked" => Some(Self::Blocked),
            "candidate_only" => Some(Self::CandidateOnly),
            "pending_review" => Some(Self::PendingReview),
            _ => None,
        }
    }
}

impl fmt::Display for AdmissionStatus {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenWrite {
    pub network: String,
    pub chain_id: u64,
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
    pub admission_status: AdmissionStatus,
    pub admission_source: String,
    pub metadata_source: String,
    pub metadata_checksum: String,
    pub source_json: Option<String>,
    pub first_seen_at: Option<String>,
    pub blocked_reason: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::{AdmissionStatus, LibraryKey};

    #[test]
    fn storage_keys_are_stable() {
        assert_eq!(LibraryKey::Core.as_str(), "core");
        assert_eq!(LibraryKey::Stocks.table_name(), "token_stocks");
        assert_eq!(LibraryKey::Extension.as_str(), "extension");
        assert_eq!(LibraryKey::Test.table_name(), "token_test");
        assert_eq!(LibraryKey::from_key("core"), Some(LibraryKey::Core));
        assert_eq!(AdmissionStatus::Admitted.as_str(), "admitted");
        assert_eq!(
            AdmissionStatus::from_storage("candidate_only"),
            Some(AdmissionStatus::CandidateOnly)
        );
    }
}
