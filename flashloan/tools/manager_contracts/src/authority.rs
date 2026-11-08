use serde::{Deserialize, Serialize};
use std::fmt;
use std::str::FromStr;

pub const TOKEN_MANAGER_AUTHORITY_ENV: &str = "TOKEN_MANAGER_AUTHORITY";
pub const TOKEN_MANAGER_ROLLBACK_REASON_ENV: &str = "TOKEN_MANAGER_ROLLBACK_REASON";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TokenAuthorityMode {
    #[default]
    Legacy,
    Manager,
    Rollback,
}

impl TokenAuthorityMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Legacy => "legacy",
            Self::Manager => "manager",
            Self::Rollback => "rollback",
        }
    }

    pub fn manager_apply_enabled(self) -> bool {
        self == Self::Manager
    }
}

impl fmt::Display for TokenAuthorityMode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for TokenAuthorityMode {
    type Err = AuthorityModeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "" | "legacy" => Ok(Self::Legacy),
            "manager" => Ok(Self::Manager),
            "rollback" => Ok(Self::Rollback),
            _ => Err(AuthorityModeError::InvalidMode(value.trim().to_owned())),
        }
    }
}

impl TokenAuthorityMode {
    pub fn from_env() -> Result<Self, AuthorityModeError> {
        std::env::var(TOKEN_MANAGER_AUTHORITY_ENV)
            .unwrap_or_default()
            .parse()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityModeError {
    InvalidMode(String),
}

impl fmt::Display for AuthorityModeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidMode(value) => write!(
                formatter,
                "invalid {TOKEN_MANAGER_AUTHORITY_ENV} value {value:?}; expected legacy, manager, or rollback"
            ),
        }
    }
}

impl std::error::Error for AuthorityModeError {}

#[cfg(test)]
mod tests {
    use super::TokenAuthorityMode;

    #[test]
    fn authority_modes_are_explicit_and_stable() {
        assert_eq!(
            "".parse::<TokenAuthorityMode>().unwrap(),
            TokenAuthorityMode::Legacy
        );
        assert_eq!(
            " manager ".parse::<TokenAuthorityMode>().unwrap(),
            TokenAuthorityMode::Manager
        );
        assert_eq!(TokenAuthorityMode::Rollback.as_str(), "rollback");
        assert!(TokenAuthorityMode::Manager.manager_apply_enabled());
        assert!(!TokenAuthorityMode::Legacy.manager_apply_enabled());
        assert!("dual".parse::<TokenAuthorityMode>().is_err());
    }
}
