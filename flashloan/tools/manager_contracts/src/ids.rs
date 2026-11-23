use serde::{Deserialize, Serialize};
use std::fmt;
use std::str::FromStr;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum IdError {
    InvalidNetwork,
    InvalidChainId,
    InvalidAddress,
    InvalidTokenKeyId,
    InvalidPairKeyId,
    InvalidRouteKeyId,
    InvalidDirectionalPoolId,
    MixedRouteChains,
    TooFewRouteTokens,
}

impl fmt::Display for IdError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidNetwork => formatter.write_str("network must match [a-z0-9_-]+"),
            Self::InvalidChainId => formatter.write_str("chain id must be a positive integer"),
            Self::InvalidAddress => {
                formatter.write_str("address must be a lowercase 0x-prefixed 20-byte hex value")
            }
            Self::InvalidTokenKeyId => formatter.write_str("invalid token_key_id"),
            Self::InvalidPairKeyId => formatter.write_str("invalid pair_key_id"),
            Self::InvalidRouteKeyId => formatter.write_str("invalid route_key_id"),
            Self::InvalidDirectionalPoolId => formatter.write_str("invalid directional_pool_id"),
            Self::MixedRouteChains => formatter.write_str("route tokens must use one chain id"),
            Self::TooFewRouteTokens => {
                formatter.write_str("route must contain at least two tokens")
            }
        }
    }
}

impl std::error::Error for IdError {}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct TokenKeyId(String);

impl TokenKeyId {
    pub fn new(
        network: impl AsRef<str>,
        chain_id: u64,
        address: impl AsRef<str>,
    ) -> Result<Self, IdError> {
        let network = normalize_network(network.as_ref())?;
        let chain_id = normalize_chain_id(chain_id)?;
        let address = normalize_address(address.as_ref())?;
        Ok(Self(format!("{network}:{chain_id}:{address}")))
    }

    pub fn network(&self) -> &str {
        self.0.split(':').next().unwrap_or_default()
    }

    pub fn chain_id(&self) -> u64 {
        self.0
            .split(':')
            .nth(1)
            .and_then(|value| value.parse().ok())
            .unwrap_or_default()
    }

    pub fn currency_key(&self) -> &str {
        self.0.split(':').nth(2).unwrap_or_default()
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for TokenKeyId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl FromStr for TokenKeyId {
    type Err = IdError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let mut parts = value.split(':');
        let network = parts.next().ok_or(IdError::InvalidTokenKeyId)?;
        let chain_id = parts.next().ok_or(IdError::InvalidTokenKeyId)?;
        let address = parts.next().ok_or(IdError::InvalidTokenKeyId)?;
        if parts.next().is_some() {
            return Err(IdError::InvalidTokenKeyId);
        }
        let chain_id = chain_id
            .parse::<u64>()
            .map_err(|_| IdError::InvalidTokenKeyId)?;
        if normalize_network(network).ok().as_deref() != Some(network) {
            return Err(IdError::InvalidTokenKeyId);
        }
        if normalize_chain_id(chain_id).is_err() {
            return Err(IdError::InvalidTokenKeyId);
        }
        if normalize_address(address).ok().as_deref() != Some(address) {
            return Err(IdError::InvalidTokenKeyId);
        }
        Ok(Self(format!("{network}:{chain_id}:{address}")))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct PairKeyId(String);

impl PairKeyId {
    pub fn new(
        chain_id: u64,
        currency_a: impl AsRef<str>,
        currency_b: impl AsRef<str>,
    ) -> Result<Self, IdError> {
        let chain_id = normalize_chain_id(chain_id)?;
        let mut currencies = [
            normalize_address(currency_a.as_ref())?,
            normalize_address(currency_b.as_ref())?,
        ];
        currencies.sort();
        Ok(Self(format!(
            "pair:v1:{chain_id}:{}:{}",
            currencies[0], currencies[1]
        )))
    }

    pub fn chain_id(&self) -> u64 {
        self.0
            .split(':')
            .nth(2)
            .and_then(|value| value.parse().ok())
            .unwrap_or_default()
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for PairKeyId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl FromStr for PairKeyId {
    type Err = IdError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let parts: Vec<_> = value.split(':').collect();
        if parts.len() != 5 || parts[0] != "pair" || parts[1] != "v1" {
            return Err(IdError::InvalidPairKeyId);
        }
        let chain_id = parts[2]
            .parse::<u64>()
            .map_err(|_| IdError::InvalidPairKeyId)?;
        if normalize_chain_id(chain_id).is_err()
            || normalize_address(parts[3]).ok().as_deref() != Some(parts[3])
            || normalize_address(parts[4]).ok().as_deref() != Some(parts[4])
            || parts[3] >= parts[4]
        {
            return Err(IdError::InvalidPairKeyId);
        }
        Ok(Self(value.to_owned()))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct RouteKeyId(String);

impl RouteKeyId {
    pub fn new(tokens: &[TokenKeyId]) -> Result<Self, IdError> {
        if tokens.len() < 2 {
            return Err(IdError::TooFewRouteTokens);
        }
        let chain_id = tokens[0].chain_id();
        if tokens.iter().any(|token| token.chain_id() != chain_id) {
            return Err(IdError::MixedRouteChains);
        }
        Ok(Self(format!(
            "route:v1:{chain_id}:{}",
            tokens
                .iter()
                .map(TokenKeyId::as_str)
                .collect::<Vec<_>>()
                .join(":")
        )))
    }

    pub fn tokens(&self) -> Vec<TokenKeyId> {
        let mut parts = self.0.split(':');
        parts.next();
        parts.next();
        parts.next();
        let remainder: Vec<_> = parts.collect();
        remainder
            .chunks_exact(3)
            .filter_map(|chunk| {
                format!("{}:{}:{}", chunk[0], chunk[1], chunk[2])
                    .parse()
                    .ok()
            })
            .collect()
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for RouteKeyId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl FromStr for RouteKeyId {
    type Err = IdError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let mut prefix = value.splitn(4, ':');
        if prefix.next() != Some("route") || prefix.next() != Some("v1") {
            return Err(IdError::InvalidRouteKeyId);
        }
        let chain_id = prefix
            .next()
            .and_then(|part| part.parse::<u64>().ok())
            .ok_or(IdError::InvalidRouteKeyId)?;
        let token_text = prefix.next().ok_or(IdError::InvalidRouteKeyId)?;
        let parts: Vec<_> = token_text.split(':').collect();
        if parts.len() < 6 || parts.len() % 3 != 0 {
            return Err(IdError::InvalidRouteKeyId);
        }
        let mut tokens = Vec::with_capacity(parts.len() / 3);
        for chunk in parts.chunks_exact(3) {
            let token = format!("{}:{}:{}", chunk[0], chunk[1], chunk[2])
                .parse::<TokenKeyId>()
                .map_err(|_| IdError::InvalidRouteKeyId)?;
            if token.chain_id() != chain_id {
                return Err(IdError::InvalidRouteKeyId);
            }
            tokens.push(token);
        }
        Self::new(&tokens).map_err(|_| IdError::InvalidRouteKeyId)
    }
}

fn normalize_network(value: &str) -> Result<String, IdError> {
    let normalized = value.trim().to_ascii_lowercase();
    if normalized.is_empty()
        || !normalized.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_' || byte == b'-'
        })
    {
        return Err(IdError::InvalidNetwork);
    }
    Ok(normalized)
}

/// The identity of a **directional pool**: a base V4 pool projected onto one
/// of its two swap directions.
///
/// The identity is the base pool identity plus the direction, so the two
/// rows of one base pool can never collide and a direction can never be
/// silently swapped: the direction is part of the key, not a payload.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct DirectionalPoolId(String);

impl DirectionalPoolId {
    pub fn new(
        network: impl AsRef<str>,
        chain_id: u64,
        scope_key: impl AsRef<str>,
        base_pool_id: impl AsRef<str>,
        direction: u8,
    ) -> Result<Self, IdError> {
        let network = normalize_network(network.as_ref())?;
        let chain_id = normalize_chain_id(chain_id)?;
        let scope_key = normalize_scope_key(scope_key.as_ref())?;
        let base_pool_id = normalize_base_pool_id(base_pool_id.as_ref())?;
        if direction > 1 {
            return Err(IdError::InvalidDirectionalPoolId);
        }
        Ok(Self(format!(
            "dpool:v1:{network}:{chain_id}:{scope_key}:{base_pool_id}:{direction}"
        )))
    }

    pub fn network(&self) -> &str {
        self.0.split(':').nth(2).unwrap_or_default()
    }

    pub fn chain_id(&self) -> u64 {
        self.0
            .split(':')
            .nth(3)
            .and_then(|value| value.parse().ok())
            .unwrap_or_default()
    }

    pub fn scope_key(&self) -> &str {
        self.0.split(':').nth(4).unwrap_or_default()
    }

    pub fn base_pool_id(&self) -> &str {
        self.0.split(':').nth(5).unwrap_or_default()
    }

    /// The stored direction value (`0` or `1`).
    pub fn direction(&self) -> u8 {
        self.0
            .split(':')
            .nth(6)
            .and_then(|value| value.parse().ok())
            .unwrap_or_default()
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for DirectionalPoolId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl FromStr for DirectionalPoolId {
    type Err = IdError;

    fn from_str(value: &str) -> Result<Self, IdError> {
        let parts: Vec<_> = value.split(':').collect();
        if parts.len() != 7
            || parts[0] != "dpool"
            || parts[1] != "v1"
            || normalize_network(parts[2]).ok().as_deref() != Some(parts[2])
            || normalize_scope_key(parts[4]).ok().as_deref() != Some(parts[4])
            || normalize_base_pool_id(parts[5]).ok().as_deref() != Some(parts[5])
            || parts[6] != "0"
                && parts[6] != "1"
        {
            return Err(IdError::InvalidDirectionalPoolId);
        }
        let chain_id = parts[3]
            .parse::<u64>()
            .map_err(|_| IdError::InvalidDirectionalPoolId)?;
        if normalize_chain_id(chain_id).is_err() {
            return Err(IdError::InvalidDirectionalPoolId);
        }
        Ok(Self(value.to_owned()))
    }
}

fn normalize_scope_key(value: &str) -> Result<String, IdError> {
    let normalized = value.trim().to_ascii_lowercase();
    if normalized.is_empty()
        || !normalized.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_' || byte == b'-'
        })
    {
        return Err(IdError::InvalidDirectionalPoolId);
    }
    Ok(normalized)
}

/// A base pool id is the keccak256 digest of the encoded pool key, so it is
/// always `0x` followed by 64 hexadecimal characters.
fn normalize_base_pool_id(value: &str) -> Result<String, IdError> {
    let normalized = value.trim().to_ascii_lowercase();
    let Some(hex) = normalized.strip_prefix("0x") else {
        return Err(IdError::InvalidDirectionalPoolId);
    };
    if hex.len() != 64 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(IdError::InvalidDirectionalPoolId);
    }
    Ok(normalized)
}

fn normalize_chain_id(chain_id: u64) -> Result<u64, IdError> {
    if chain_id == 0 {
        Err(IdError::InvalidChainId)
    } else {
        Ok(chain_id)
    }
}

pub fn normalize_address(value: &str) -> Result<String, IdError> {
    let normalized = value.trim().to_ascii_lowercase();
    let Some(hex) = normalized.strip_prefix("0x") else {
        return Err(IdError::InvalidAddress);
    };
    if hex.len() != 40 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(IdError::InvalidAddress);
    }
    Ok(normalized)
}

#[cfg(test)]
mod tests {
    use super::{
        normalize_address, DirectionalPoolId, IdError, PairKeyId, RouteKeyId, TokenKeyId,
    };
    use std::str::FromStr;

    const USDG: &str = "0x1111111111111111111111111111111111111111";
    const WETH: &str = "0x2222222222222222222222222222222222222222";
    const POOL: &str =
        "0x002eaee43a6c1daa7c29484ee0d44a9abcd65827e72f883a1feb42ab8618ccba";

    #[test]
    fn normalizes_address_and_rejects_invalid_values() {
        assert_eq!(
            normalize_address("0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA").unwrap(),
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        );
        assert_eq!(normalize_address("0x1234"), Err(IdError::InvalidAddress));
        assert_eq!(
            normalize_address("not-an-address"),
            Err(IdError::InvalidAddress)
        );
    }

    #[test]
    fn token_pair_and_route_ids_have_frozen_formats() {
        let usdg = TokenKeyId::new("Robinhood", 4663, USDG).unwrap();
        let weth = TokenKeyId::new("robinhood", 4663, WETH).unwrap();
        assert_eq!(usdg.as_str(), format!("robinhood:4663:{USDG}"));
        assert_eq!(
            PairKeyId::new(4663, WETH, USDG).unwrap().as_str(),
            format!("pair:v1:4663:{USDG}:{WETH}")
        );
        let route = RouteKeyId::new(&[weth.clone(), usdg.clone()]).unwrap();
        assert_eq!(
            route.as_str(),
            format!("route:v1:4663:robinhood:4663:{WETH}:robinhood:4663:{USDG}")
        );
        assert_eq!(route.tokens(), vec![weth, usdg]);
        assert_eq!(RouteKeyId::from_str(route.as_str()).unwrap(), route);
    }

    #[test]
    fn native_zero_address_is_valid_and_distinct_from_weth() {
        let native = TokenKeyId::new(
            "robinhood",
            4663,
            "0x0000000000000000000000000000000000000000",
        )
        .unwrap();
        assert_ne!(native.currency_key(), WETH);
    }

    #[test]
    fn the_directional_pool_id_carries_the_direction() {
        let low = DirectionalPoolId::new("Robinhood", 4663, "candidate_exchange", POOL, 0)
            .unwrap();
        let high = DirectionalPoolId::new("robinhood", 4663, "candidate_exchange", POOL, 1)
            .unwrap();

        // The two rows of one base pool never collide.
        assert_ne!(low, high);
        assert_eq!(low.base_pool_id(), high.base_pool_id());
        assert_eq!(low.direction(), 0);
        assert_eq!(high.direction(), 1);
        assert_eq!(low.chain_id(), 4663);
        assert_eq!(low.scope_key(), "candidate_exchange");
        assert_eq!(DirectionalPoolId::from_str(low.as_str()).unwrap(), low);
        // A direction above one is not a direction.
        assert_eq!(
            DirectionalPoolId::new("robinhood", 4663, "candidate_exchange", POOL, 2),
            Err(IdError::InvalidDirectionalPoolId)
        );
        // A base pool id that is not a keccak256 digest is rejected.
        assert_eq!(
            DirectionalPoolId::new("robinhood", 4663, "candidate_exchange", "0x1234", 0),
            Err(IdError::InvalidDirectionalPoolId)
        );
    }
}
