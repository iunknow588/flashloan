use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    InvalidRequest,
    Unauthorized,
    TokenAuthorityWriteDenied,
    TokenNotFound,
    TokenSymbolAmbiguous,
    TokenNotAdmitted,
    TokenLibraryConflict,
    IdempotencyKeyReused,
    ScopeNotFound,
    ScopeNotImplemented,
    UpstreamVersionChanged,
    PoolIdentityMismatch,
    PoolInvalid,
    ReviewPnlIncomplete,
    ReviewNotExecutable,
    BroadcastRequestNotEligible,
    BroadcastSubmissionFailed,
    RpcUnavailable,
    UpstreamUnavailable,
    InternalError,
}

impl ErrorCode {
    pub fn http_status(self) -> u16 {
        match self {
            Self::InvalidRequest => 400,
            Self::Unauthorized | Self::TokenAuthorityWriteDenied => 403,
            Self::TokenNotFound | Self::ScopeNotFound => 404,
            Self::TokenSymbolAmbiguous
            | Self::TokenNotAdmitted
            | Self::TokenLibraryConflict
            | Self::IdempotencyKeyReused
            | Self::UpstreamVersionChanged
            | Self::PoolIdentityMismatch
            | Self::ReviewNotExecutable
            | Self::BroadcastRequestNotEligible => 409,
            Self::ScopeNotImplemented | Self::PoolInvalid | Self::ReviewPnlIncomplete => 422,
            Self::BroadcastSubmissionFailed => 502,
            Self::RpcUnavailable | Self::UpstreamUnavailable => 503,
            Self::InternalError => 500,
        }
    }

    pub fn retryable(self) -> bool {
        matches!(
            self,
            Self::UpstreamVersionChanged
                | Self::ReviewPnlIncomplete
                | Self::BroadcastSubmissionFailed
                | Self::RpcUnavailable
                | Self::UpstreamUnavailable
                | Self::InternalError
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ManagerError {
    pub code: ErrorCode,
    pub message: String,
    pub retryable: bool,
    pub stage: String,
    pub details: serde_json::Value,
}

impl ManagerError {
    pub fn new(code: ErrorCode, message: impl Into<String>, stage: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            retryable: code.retryable(),
            stage: stage.into(),
            details: serde_json::Value::Object(Default::default()),
        }
    }

    pub fn with_details(mut self, details: serde_json::Value) -> Self {
        self.details = details;
        self
    }
}

impl fmt::Display for ManagerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{:?}: {}", self.code, self.message)
    }
}

impl std::error::Error for ManagerError {}

#[cfg(test)]
mod tests {
    use super::{ErrorCode, ManagerError};

    #[test]
    fn error_codes_have_stable_serialization_and_retry_semantics() {
        let error = ManagerError::new(
            ErrorCode::UpstreamVersionChanged,
            "core library changed",
            "pool_materialization",
        );
        let value = serde_json::to_value(&error).unwrap();
        assert_eq!(value["code"], "UPSTREAM_VERSION_CHANGED");
        assert_eq!(value["retryable"], true);
        assert!(!ErrorCode::PoolIdentityMismatch.retryable());
    }
}
