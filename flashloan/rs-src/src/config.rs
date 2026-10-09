use std::env;

pub const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";
pub const V4_DYNAMIC_FEE_FLAG: u64 = 8_388_608;
pub const V4_SPECIAL_FEE_THRESHOLD: u64 = 100_000;

#[derive(Clone, Debug)]
pub struct ChainDataSourceConfig {
    pub network: String,
    pub chain_id: u64,
    pub hyperrpc_url: String,
    pub hypersync_url: String,
    pub envio_api_token: String,
    pub pool_manager: String,
}

impl ChainDataSourceConfig {
    pub fn from_env(
        network: &str,
        chain_id: u64,
        pool_manager: &str,
    ) -> Result<Self, String> {
        let hyperrpc_url = env::var("ROBINHOOD_HyperRPC_URL")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .or_else(|| env::var("HYPERRPC_URL").ok().map(|value| value.trim().to_string()))
            .ok_or_else(|| {
                "ROBINHOOD_HyperRPC_URL is not configured; load flashloan/app_runtime/.env first"
                    .to_string()
            })?;
        let hypersync_url = env::var("ROBINHOOD_HyperSync_URL")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| format!("https://{chain_id}.hypersync.xyz"));
        let envio_api_token = env::var("ENVIO_API_TOKEN")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .unwrap_or_default();
        Ok(Self {
            network: network.to_string(),
            chain_id,
            hyperrpc_url,
            hypersync_url,
            envio_api_token,
            pool_manager: normalize_address(pool_manager),
        })
    }
}

pub fn normalize_address(value: &str) -> String {
    let text = value.trim();
    if text.starts_with("0x") || text.starts_with("0X") {
        text.to_lowercase()
    } else if text.len() == 40 {
        format!("0x{text}").to_lowercase()
    } else {
        text.to_lowercase()
    }
}

pub fn canonical_address(value: &str) -> String {
    let normalized = normalize_address(value);
    if normalized == ZERO_ADDRESS {
        ZERO_ADDRESS.to_string()
    } else {
        normalized
    }
}

pub fn address_from_topic(topic: &str) -> Result<String, String> {
    let raw = topic.strip_prefix("0x").unwrap_or(topic);
    if raw.len() < 40 {
        return Err(format!("topic is too short to be an address: {topic}"));
    }
    Ok(format!("0x{}", &raw[raw.len() - 40..]).to_lowercase())
}

pub fn parse_u64_hexish(value: &str) -> Result<u64, String> {
    let text = value.trim();
    if let Some(hex_value) = text.strip_prefix("0x") {
        u64::from_str_radix(hex_value, 16).map_err(|error| format!("invalid hex u64 {text}: {error}"))
    } else {
        text.parse::<u64>().map_err(|error| format!("invalid u64 {text}: {error}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_address_is_canonical() {
        assert_eq!(canonical_address("0x0000000000000000000000000000000000000000"), ZERO_ADDRESS);
    }

    #[test]
    fn topic_uses_last_twenty_bytes() {
        assert_eq!(
            address_from_topic("0x0000000000000000000000005fc5360d0400a0fd4f2af552add042d716f1d168").unwrap(),
            "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
        );
    }

    #[test]
    fn address_sorting_is_ascending_lexicographic() {
        let lower = "0x0000000000000000000000000000000000000000";
        let higher = "0x5fc5360d0400a0fd4f2af552add042d716f1d168";
        assert!(lower < higher);
        assert_eq!(canonical_address(lower), ZERO_ADDRESS);
    }
}
