use crate::metadata::{MetadataSourceKind, RawTokenMetadata, ZERO_ADDRESS};
use rusqlite::{params, Connection, OpenFlags};
use serde_json::Value;
use std::fmt;
use std::fs;
use std::path::Path;

pub const ROBINHOOD_NETWORK: &str = "robinhood";
pub const ROBINHOOD_CHAIN_ID: u64 = 4663;
pub const ROBINHOOD_TESTNET_NETWORK: &str = "robinhood-testnet";
pub const ROBINHOOD_TESTNET_CHAIN_ID: u64 = 46630;

const CORE4_DEFINITIONS: &[CoreTokenDefinition] = &[
    CoreTokenDefinition {
        symbol: "ETH",
        address: ZERO_ADDRESS,
        decimals: 18,
        kind: "native",
        is_native: true,
        is_wrapped_native: false,
        is_stable: false,
    },
    CoreTokenDefinition {
        symbol: "WETH",
        address: "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
        decimals: 18,
        kind: "wrapped_native",
        is_native: false,
        is_wrapped_native: true,
        is_stable: false,
    },
    CoreTokenDefinition {
        symbol: "USDG",
        address: "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
        decimals: 6,
        kind: "stable",
        is_native: false,
        is_wrapped_native: false,
        is_stable: true,
    },
    CoreTokenDefinition {
        symbol: "USDE",
        address: "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
        decimals: 18,
        kind: "stable",
        is_native: false,
        is_wrapped_native: false,
        is_stable: true,
    },
];

#[derive(Debug)]
pub enum SourceError {
    Sqlite(rusqlite::Error),
    Io(std::io::Error),
    Json(serde_json::Error),
    InvalidRegistry(String),
}

impl fmt::Display for SourceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "source SQLite error: {error}"),
            Self::Io(error) => write!(formatter, "source I/O error: {error}"),
            Self::Json(error) => write!(formatter, "source JSON error: {error}"),
            Self::InvalidRegistry(message) => {
                write!(formatter, "invalid stock token registry: {message}")
            }
        }
    }
}

impl std::error::Error for SourceError {}

impl From<rusqlite::Error> for SourceError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

impl From<std::io::Error> for SourceError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<serde_json::Error> for SourceError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct V4PoolCurrencyReference {
    pub network: String,
    pub chain_id: u64,
    pub pool_id: String,
    pub side: &'static str,
    pub token_address: Option<String>,
    pub source_locator: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct V4PoolLiquidityEvidence {
    pub network: String,
    pub chain_id: u64,
    pub protocol: String,
    pub pool_id: String,
    pub fee: Option<i64>,
    pub dynamic_fee: i64,
    pub special_fee: i64,
    pub hooks: Option<String>,
    pub has_liquidity: bool,
    pub liquidity_index: Option<f64>,
    pub depth_status: Option<String>,
    pub evidence_source: Option<String>,
}

struct CoreTokenDefinition {
    symbol: &'static str,
    address: &'static str,
    decimals: i64,
    kind: &'static str,
    is_native: bool,
    is_wrapped_native: bool,
    is_stable: bool,
}

pub fn mainnet_core4_allowlist() -> Vec<RawTokenMetadata> {
    CORE4_DEFINITIONS
        .iter()
        .map(|definition| {
            let metadata_json = serde_json::json!({
                "category": definition.kind,
                "source": "fixed_core4_allowlist",
                "allowlistVersion": "core4_v1",
                "symbol": definition.symbol,
            })
            .to_string();
            let source_json = serde_json::json!({
                "seed": "fixed_core4_allowlist",
                "network": ROBINHOOD_NETWORK,
                "chainId": ROBINHOOD_CHAIN_ID,
            })
            .to_string();
            RawTokenMetadata {
                source: MetadataSourceKind::CoreAllowlist,
                source_locator: format!(
                    "core4_allowlist:{ROBINHOOD_NETWORK}:{ROBINHOOD_CHAIN_ID}:{}",
                    definition.symbol
                ),
                network: ROBINHOOD_NETWORK.to_owned(),
                chain_id: ROBINHOOD_CHAIN_ID,
                token_address: Some(definition.address.to_owned()),
                token_symbol: Some(definition.symbol.to_owned()),
                token_name: Some(definition.symbol.to_owned()),
                token_decimals: Some(definition.decimals),
                token_kind: Some(definition.kind.to_owned()),
                is_native: definition.is_native,
                is_wrapped_native: definition.is_wrapped_native,
                is_stable: definition.is_stable,
                admission_source: Some("bootstrap:core4_allowlist".to_owned()),
                metadata_source: Some("fixed_core4_allowlist".to_owned()),
                expected_token_key_id: None,
                token_metadata_json: Some(metadata_json),
                source_json: Some(source_json),
                first_seen_at: None,
            }
        })
        .collect()
}

pub fn open_read_only(path: impl AsRef<Path>) -> Result<Connection, SourceError> {
    Ok(Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?)
}

pub fn read_chain_tokens_read_only(
    path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
) -> Result<Vec<RawTokenMetadata>, SourceError> {
    let connection = open_read_only(path)?;
    read_chain_tokens(&connection, network, chain_id)
}

pub fn read_chain_tokens(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<RawTokenMetadata>, SourceError> {
    let mut statement = connection.prepare(
        "SELECT token_address, token_symbol, token_name, token_decimals,
                token_kind, is_native, is_wrapped_native, is_stable,
                token_metadata_json, source_json, first_seen_at
         FROM chain_tokens
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY token_address",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        let token_address: String = row.get(0)?;
        Ok(RawTokenMetadata {
            source: MetadataSourceKind::ChainTokens,
            source_locator: format!("chain_tokens:{network}:{chain_id}:{token_address}"),
            network: network.to_owned(),
            chain_id,
            token_address: Some(token_address),
            token_symbol: row.get(1)?,
            token_name: row.get(2)?,
            token_decimals: row.get(3)?,
            token_kind: row.get(4)?,
            is_native: row.get::<_, i64>(5)? != 0,
            is_wrapped_native: row.get::<_, i64>(6)? != 0,
            is_stable: row.get::<_, i64>(7)? != 0,
            admission_source: Some("bootstrap:chain_tokens".to_owned()),
            metadata_source: None,
            expected_token_key_id: None,
            token_metadata_json: row.get(8)?,
            source_json: row.get(9)?,
            first_seen_at: row.get(10)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

pub fn read_stock_registry(
    path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
) -> Result<Vec<RawTokenMetadata>, SourceError> {
    let path = path.as_ref();
    let payload: Value = serde_json::from_slice(&fs::read(path)?)?;
    let object = payload
        .as_object()
        .ok_or_else(|| SourceError::InvalidRegistry("root must be an object".to_owned()))?;
    validate_registry_identity(
        object.get("network"),
        object.get("chain_id"),
        network,
        chain_id,
    )?;
    let tokens = object
        .get("tokens")
        .and_then(Value::as_object)
        .ok_or_else(|| SourceError::InvalidRegistry("tokens must be an object".to_owned()))?;

    Ok(tokens
        .iter()
        .map(|(registry_key, value)| {
            let item = value.as_object();
            let address = item
                .and_then(|item| item.get("address"))
                .and_then(Value::as_str)
                .map(str::to_owned);
            let metadata_json = serde_json::json!({
                "category": item
                    .and_then(|item| item.get("category"))
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                "source": item
                    .and_then(|item| item.get("source"))
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                "status": item
                    .and_then(|item| item.get("status"))
                    .and_then(Value::as_str),
                "isin": item
                    .and_then(|item| item.get("isin"))
                    .and_then(Value::as_str),
            })
            .to_string();
            let source_json = serde_json::json!({
                "seed": "robinhood_stock_token_registry",
                "registry": path.display().to_string(),
                "registryKey": registry_key,
            })
            .to_string();
            RawTokenMetadata {
                source: MetadataSourceKind::StockRegistry,
                source_locator: format!("stock_registry:{}:{registry_key}", path.display()),
                network: network.to_owned(),
                chain_id,
                token_address: address,
                token_symbol: item
                    .and_then(|item| item.get("symbol"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                token_name: item
                    .and_then(|item| item.get("name"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                token_decimals: item
                    .and_then(|item| item.get("decimals"))
                    .and_then(value_as_i64),
                token_kind: Some("erc20".to_owned()),
                is_native: false,
                is_wrapped_native: false,
                is_stable: false,
                admission_source: Some("bootstrap:stock_registry".to_owned()),
                metadata_source: item
                    .and_then(|item| item.get("source"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                expected_token_key_id: None,
                token_metadata_json: Some(metadata_json),
                source_json: Some(source_json),
                first_seen_at: None,
            }
        })
        .collect())
}

pub fn read_v4_pool_currency_references_read_only(
    path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
) -> Result<Vec<V4PoolCurrencyReference>, SourceError> {
    let connection = open_read_only(path)?;
    read_v4_pool_currency_references(&connection, network, chain_id)
}

pub fn read_v4_pool_currency_references(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<V4PoolCurrencyReference>, SourceError> {
    let mut statement = connection.prepare(
        "SELECT pool_id, token0, token1
         FROM v4_exchange_pools
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY pool_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?,
            row.get::<_, Option<String>>(2)?,
        ))
    })?;
    let mut references = Vec::new();
    for row in rows {
        let (pool_id, token0, token1) = row?;
        references.push(V4PoolCurrencyReference {
            network: network.to_owned(),
            chain_id,
            source_locator: format!("v4_exchange_pools:{network}:{chain_id}:{pool_id}:token0"),
            pool_id: pool_id.clone(),
            side: "token0",
            token_address: token0,
        });
        references.push(V4PoolCurrencyReference {
            network: network.to_owned(),
            chain_id,
            source_locator: format!("v4_exchange_pools:{network}:{chain_id}:{pool_id}:token1"),
            pool_id,
            side: "token1",
            token_address: token1,
        });
    }
    Ok(references)
}

pub fn read_v4_pool_liquidity_evidence_read_only(
    path: impl AsRef<Path>,
    network: &str,
    chain_id: u64,
) -> Result<Vec<V4PoolLiquidityEvidence>, SourceError> {
    let connection = open_read_only(path)?;
    read_v4_pool_liquidity_evidence(&connection, network, chain_id)
}

pub fn read_v4_pool_liquidity_evidence(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<Vec<V4PoolLiquidityEvidence>, SourceError> {
    let mut statement = connection.prepare(
        "SELECT e.pool_id, e.fee, e.dynamic_fee, e.special_fee,
                lower(e.hooks), s.has_liquidity, s.liquidity_index,
                lower(s.depth_status), lower(s.evidence_source)
         FROM v4_exchange_pools e
         JOIN v4_exchange_pool_liquidity_snapshots s
           ON s.network = e.network
          AND s.chain_id = e.chain_id
          AND s.protocol = e.protocol
          AND s.pool_id = e.pool_id
         WHERE e.network = ?1 AND e.chain_id = ?2 AND e.protocol = 'uniswap_v4'
         ORDER BY e.pool_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok(V4PoolLiquidityEvidence {
            network: network.to_owned(),
            chain_id,
            protocol: "uniswap_v4".to_owned(),
            pool_id: row.get(0)?,
            fee: row.get(1)?,
            dynamic_fee: row.get(2)?,
            special_fee: row.get(3)?,
            hooks: row.get(4)?,
            has_liquidity: row.get::<_, i64>(5)? != 0,
            liquidity_index: row.get(6)?,
            depth_status: row.get(7)?,
            evidence_source: row.get(8)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn validate_registry_identity(
    registry_network: Option<&Value>,
    registry_chain_id: Option<&Value>,
    network: &str,
    chain_id: u64,
) -> Result<(), SourceError> {
    if let Some(registry_network) = registry_network.and_then(Value::as_str) {
        if !registry_network.trim().eq_ignore_ascii_case(network) {
            return Err(SourceError::InvalidRegistry(format!(
                "network {registry_network} does not match {network}"
            )));
        }
    }
    if let Some(registry_chain_id) = registry_chain_id.and_then(value_as_i64) {
        if registry_chain_id != i64::try_from(chain_id).unwrap_or(i64::MAX) {
            return Err(SourceError::InvalidRegistry(format!(
                "chain_id {registry_chain_id} does not match {chain_id}"
            )));
        }
    }
    Ok(())
}

fn value_as_i64(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
}

#[cfg(test)]
mod tests {
    use super::{
        mainnet_core4_allowlist, read_chain_tokens, read_stock_registry,
        read_v4_pool_currency_references, read_v4_pool_liquidity_evidence, ROBINHOOD_CHAIN_ID,
        ROBINHOOD_NETWORK,
    };
    use rusqlite::Connection;

    #[test]
    fn core4_allowlist_is_address_based_and_exact() {
        let rows = mainnet_core4_allowlist();
        assert_eq!(
            rows.iter()
                .map(|row| row.token_symbol.as_deref().unwrap())
                .collect::<Vec<_>>(),
            vec!["ETH", "WETH", "USDG", "USDE"]
        );
        assert_eq!(
            rows[2].token_address.as_deref(),
            Some("0x5fc5360d0400a0fd4f2af552add042d716f1d168")
        );
        assert_eq!(rows[2].token_decimals, Some(6));
    }

    #[test]
    fn chain_token_and_pool_adapters_preserve_source_locations() {
        let connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE TABLE chain_tokens (
                    token_address TEXT NOT NULL,
                    token_symbol TEXT,
                    token_name TEXT,
                    token_decimals INTEGER,
                    token_kind TEXT,
                    is_native INTEGER NOT NULL,
                    is_wrapped_native INTEGER NOT NULL,
                    is_stable INTEGER NOT NULL,
                    token_metadata_json TEXT,
                    source_json TEXT,
                    first_seen_at TEXT NOT NULL,
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL
                );
                CREATE TABLE v4_exchange_pools (
                    pool_id TEXT NOT NULL,
                    token0 TEXT,
                    token1 TEXT,
                    protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
                    fee INTEGER,
                    dynamic_fee INTEGER NOT NULL DEFAULT 0,
                    special_fee INTEGER NOT NULL DEFAULT 0,
                    hooks TEXT,
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    has_liquidity INTEGER,
                    liquidity_index REAL,
                    depth_status TEXT,
                    evidence_source TEXT
                );
                INSERT INTO chain_tokens VALUES (
                    '0x1111111111111111111111111111111111111111',
                    'USDG', 'USDG', 6, 'stable', 0, 0, 1,
                    '{\"source\":\"fixture\"}', '{\"seed\":\"fixture\"}',
                    '2026-11-03T00:00:00Z', 'robinhood', 4663
                );
                INSERT INTO v4_exchange_pools VALUES (
                    'pool-1',
                    '0x1111111111111111111111111111111111111111',
                    '0x0000000000000000000000000000000000000000',
                    'uniswap_v4', 3000, 0, 0,
                    '0x0000000000000000000000000000000000000000',
                    'robinhood', 4663
                );",
            )
            .unwrap();

        let tokens = read_chain_tokens(&connection, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID).unwrap();
        assert_eq!(tokens.len(), 1);
        assert_eq!(
            tokens[0].source_locator,
            "chain_tokens:robinhood:4663:0x1111111111111111111111111111111111111111"
        );

        let pools =
            read_v4_pool_currency_references(&connection, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)
                .unwrap();
        assert_eq!(pools.len(), 2);
        assert_eq!(pools[0].side, "token0");
        assert_eq!(pools[1].side, "token1");
        assert!(pools[0].source_locator.ends_with(":token0"));

        connection
            .execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES (
                    'robinhood', 4663, 'uniswap_v4', 'pool-1',
                    1, 1000.0, 'deep', 'pool_manager_extsload_live_state_batch'
                )",
                [],
            )
            .unwrap();
        let liquidity =
            read_v4_pool_liquidity_evidence(&connection, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)
                .unwrap();
        assert_eq!(liquidity.len(), 1);
        assert_eq!(liquidity[0].pool_id, "pool-1");
        assert_eq!(liquidity[0].fee, Some(3000));
        assert!(liquidity[0].has_liquidity);
        assert_eq!(liquidity[0].depth_status.as_deref(), Some("deep"));
    }

    #[test]
    fn stock_registry_adapter_reads_explicit_metadata() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("stocks.json");
        std::fs::write(
            &path,
            r#"{
                "network": "robinhood",
                "chain_id": 4663,
                "tokens": {
                    "aapl": {
                        "address": "0x2222222222222222222222222222222222222222",
                        "symbol": "AAPL",
                        "name": "Apple",
                        "decimals": 18,
                        "category": "robinhood_stock",
                        "source": "fixture_api",
                        "status": "ASSET_STATUS_ACTIVE",
                        "isin": "US0378331005"
                    }
                }
            }"#,
        )
        .unwrap();

        let rows = read_stock_registry(&path, ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].token_symbol.as_deref(), Some("AAPL"));
        assert_eq!(rows[0].token_decimals, Some(18));
        assert_eq!(rows[0].metadata_source.as_deref(), Some("fixture_api"));
        assert!(rows[0].source_locator.starts_with("stock_registry:"));
    }
}
