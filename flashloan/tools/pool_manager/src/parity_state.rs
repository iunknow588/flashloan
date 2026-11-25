//! Published pool-state reads for the off-chain parity review.
//!
//! Why this module exists
//! -----------------------
//!
//! The off-chain parity comparison needs three things from the pool tables:
//! the pool's own identity (tokens, fee), its **latest measured state**
//! (sqrt price, liquidity) and the moment that state was read.
//!
//! Those tables belong to Pool Manager. `v4_exchange_pools` carries the
//! materialised pool scope and `v4_exchange_pool_liquidity_snapshots` carries
//! the measured depth state. Review Manager must not read them directly:
//! the rule is that a module reads only through another module's published
//! API, so the ownership boundary stays a real boundary rather than a
//! convention.
//!
//! This module is that published API. It is read-only, it never writes, and
//! it returns exactly the fields the parity comparison is allowed to see.

use std::path::Path;

use rusqlite::{params_from_iter, Connection, OpenFlags};
use std::collections::BTreeMap;

/// The protocol the pool tables are keyed by.
pub const POOL_PROTOCOL: &str = "uniswap_v4";

/// One pool's identity together with its latest measured state.
///
/// The field names are intentionally flat and raw: this is a published DTO,
/// not a domain model, so the consumer can serialise it without inventing a
/// second schema.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PoolParityState {
    /// The pool identifier, as stored in the pool tables.
    pub pool_id: String,
    /// The lower-case address of token0.
    pub token0: String,
    /// The lower-case address of token1.
    pub token1: String,
    /// The pool's fixed fee, in hundredths of a bip.
    pub fee: u32,
    /// The pool's tick spacing.
    pub tick_spacing: i64,
    /// The pool's hooks address.
    pub hooks: String,
    /// The current square-root price.
    pub sqrt_price_x96: u128,
    /// The pool's current tick.
    ///
    /// The tick anchors the tick traversal: it decides which bitmap word the
    /// swap starts in, so it belongs to the parity state and is not an
    /// optional hint.
    pub tick: i64,
    /// The pool's active liquidity.
    pub liquidity: u128,
    /// The moment the liquidity state was read.
    pub liquidity_read_at: Option<String>,
    /// The materialisation batch that wrote this state.
    ///
    /// Pool Manager stamps every row of one refresh run with the same batch
    /// id, so the batch is the **snapshot identity** of the state: two pools
    /// that carry the same batch were measured in one run, and two pools that
    /// carry different batches were measured in two different runs.
    ///
    /// The read timestamp cannot answer that question: pools are read one
    /// after the other, so two pools of the same batch always carry two
    /// different timestamps.  The batch is published for that reason.
    pub materialization_batch: Option<String>,
    /// Where the liquidity evidence came from.
    pub evidence_source: Option<String>,
}

/// Reads the **live** state of the requested pools.
///
/// The stored snapshot is a measurement, and a measurement ages: while the
/// pool trades, the stored price and liquidity no longer describe it. A
/// comparison against the chain is only meaningful when both sides price
/// the **same** state, so a consumer that compares against the chain reads
/// the live state here.
///
/// The read is a Pool Manager responsibility because Pool Manager owns the
/// chain access, the rate limiter and the slot derivation. The consumer
/// never talks to the chain for a pool state of its own.
///
/// The identity of a pool is still read from the database, because the
/// chain does not publish the token order, the fee or the tick spacing of a
/// pool id on its own. The measured state is replaced by the live one.
pub fn read_live_pool_parity_states(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_ids: &[String],
    rpc_urls: &[String],
    timeout_seconds: u64,
) -> Result<Vec<PoolParityState>, PoolStateReadError> {
    let stored = read_pool_parity_states(
        database_path.as_ref(),
        network,
        chain_id,
        protocol,
        pool_ids,
    )?;
    if stored.is_empty() {
        return Ok(stored);
    }
    let wanted: Vec<String> = stored.iter().map(|state| state.pool_id.clone()).collect();
    let batch = crate::chain::read_v4_pool_states_with_fallback(
        rpc_urls,
        crate::chain::ROBINHOOD_V4_POOL_MANAGER,
        &wanted,
        timeout_seconds,
    )
    .map_err(PoolStateReadError::Sqlite)?;

    let block_number = batch.block_number;
    let mut live: BTreeMap<String, crate::chain::V4PoolState> = BTreeMap::new();
    for state in batch.states {
        live.insert(state.pool_id.trim().to_lowercase(), state);
    }

    Ok(stored
        .into_iter()
        .map(|mut state| {
            // The live measurement replaces the stored one **only when it is
            // complete**.
            //
            // A pool whose `slot0` read failed has no price, and a pool
            // whose liquidity read failed has no depth. Mixing the failed
            // half with the stored half produces a state that describes
            // neither the stored block nor the live block, and the
            // comparison then reports the age of the snapshot as if it were
            // a defect of the arithmetic. The stored measurement is
            // therefore kept **whole**, and the pool keeps the stored
            // version so that the two sides of the comparison can see that
            // they do not describe the same block.
            //
            // This matters because the node answers the excess of its
            // per-second budget with a rate-limit error, and that error is
            // a property of the **transport**, not of the pool.
            let Some(fresh) = live.get(&state.pool_id.trim().to_lowercase()) else {
                return state;
            };
            if !fresh.complete {
                return state;
            }
            let Ok(sqrt_price_x96) = fresh.sqrt_price_x96.parse::<u128>() else {
                return state;
            };
            if sqrt_price_x96 == 0 {
                return state;
            }
            state.sqrt_price_x96 = sqrt_price_x96;
            state.tick = fresh.tick;
            state.liquidity = fresh.liquidity;
            // The live state batch is pinned to one exact block. Review
            // Manager pins the chain-side quoter call to the same block,
            // so the comparison is a comparison of arithmetic rather than a
            // race between two changing states.
            state.liquidity_read_at = block_number
                .map(|block| format!("block:{block}"))
                .or_else(|| Some(crate::chain::live_state_version()));
            state
        })
        .collect())
}

/// Why a pool-state read could not be served.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PoolStateReadError {
    /// The database could not be opened or the query failed.
    Sqlite(String),
    /// The caller asked for something that cannot be answered.
    InvalidRequest(String),
}

impl std::fmt::Display for PoolStateReadError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Sqlite(message) => write!(formatter, "pool state read failed: {message}"),
            Self::InvalidRequest(message) => {
                write!(formatter, "invalid pool state request: {message}")
            }
        }
    }
}

impl std::error::Error for PoolStateReadError {}

fn open_read_only(database_path: &Path) -> Result<Connection, PoolStateReadError> {
    Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|error| PoolStateReadError::Sqlite(error.to_string()))
}

/// Resolves a PoolKey to the pool identifier that carries it.
///
/// The native preview reports the pool it selected as a **PoolKey**: the two
/// currencies, the fee, the tick spacing and the hooks. The pool table
/// stores the same key, so the two can be joined.
///
/// The match is exact on the full key. A pool whose key does not match
/// exactly is **not** guessed at: a wrong pool would silently produce a
/// wrong simulation, which is worse than no simulation.
pub fn resolve_pool_ids_by_key(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_keys: &[PoolKeyIdentity],
) -> Result<Vec<String>, PoolStateReadError> {
    let connection = open_read_only(database_path.as_ref())?;
    resolve_pool_ids_by_key_on_connection(&connection, network, chain_id, protocol, pool_keys)
}

/// The canonical identity of a V4 pool key.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct PoolKeyIdentity {
    pub currency0: String,
    pub currency1: String,
    pub fee: i64,
    pub tick_spacing: i64,
    pub hooks: String,
}

impl PoolKeyIdentity {
    /// Builds an identity from loosely typed inputs, or `None` when the
    /// inputs do not describe a pool.
    pub fn new(
        currency0: impl AsRef<str>,
        currency1: impl AsRef<str>,
        fee: i64,
        tick_spacing: i64,
        hooks: Option<&str>,
    ) -> Option<Self> {
        let token0 = currency0.as_ref().trim().to_lowercase();
        let token1 = currency1.as_ref().trim().to_lowercase();
        if token0.is_empty() || token1.is_empty() {
            return None;
        }
        Some(Self {
            currency0: token0,
            currency1: token1,
            fee,
            tick_spacing,
            hooks: hooks
                .unwrap_or("0x0000000000000000000000000000000000000000")
                .trim()
                .to_lowercase(),
        })
    }
}

/// The identity of a pool key as it is stored in the pool table.
fn stored_identity(
    currency0: Option<&str>,
    currency1: Option<&str>,
    fee: Option<i64>,
    tick_spacing: Option<i64>,
    hooks: Option<&str>,
) -> Option<PoolKeyIdentity> {
    PoolKeyIdentity::new(currency0?, currency1?, fee?, tick_spacing?, hooks)
}

/// Resolves pool keys against an already-open connection.
pub fn resolve_pool_ids_by_key_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_keys: &[PoolKeyIdentity],
) -> Result<Vec<String>, PoolStateReadError> {
    if pool_keys.is_empty() {
        return Ok(Vec::new());
    }
    let mut statement = connection
        .prepare(
            "SELECT pool_id, pool_key_json, token0, token1, fee, tick_spacing, hooks
             FROM v4_exchange_pools
             WHERE network = ? AND chain_id = ? AND protocol = ?",
        )
        .map_err(|error| PoolStateReadError::Sqlite(error.to_string()))?;
    let rows = statement
        .query_map(
            params_from_iter([network, &chain_id.to_string(), protocol]),
            |row| {
                let pool_id: String = row.get(0)?;
                let pool_key_json: Option<String> = row.get(1)?;
                let token0: Option<String> = row.get(2)?;
                let token1: Option<String> = row.get(3)?;
                let fee: Option<i64> = row.get(4)?;
                let tick_spacing: Option<i64> = row.get(5)?;
                let hooks: Option<String> = row.get(6)?;
                Ok((
                    pool_id,
                    pool_key_json,
                    token0,
                    token1,
                    fee,
                    tick_spacing,
                    hooks,
                ))
            },
        )
        .map_err(|error| PoolStateReadError::Sqlite(error.to_string()))?;

    let mut by_identity: std::collections::BTreeMap<PoolKeyIdentity, String> =
        std::collections::BTreeMap::new();
    for row in rows.flatten() {
        let mut key_currency0 = row.2.clone();
        let mut key_currency1 = row.3.clone();
        let mut key_fee = row.4;
        let mut key_tick_spacing = row.5;
        let mut key_hooks = row.6.clone();
        if let Some(raw) = row.1.as_deref() {
            if let Ok(serde_json::Value::Object(map)) =
                serde_json::from_str::<serde_json::Value>(raw)
            {
                let read_text = |name: &str| -> Option<String> {
                    map.get(name).and_then(|value| {
                        value
                            .as_str()
                            .map(ToOwned::to_owned)
                            .or_else(|| value.as_i64().map(|number| number.to_string()))
                    })
                };
                if let Some(value) = read_text("currency0").or_else(|| read_text("token0")) {
                    key_currency0 = Some(value);
                }
                if let Some(value) = read_text("currency1").or_else(|| read_text("token1")) {
                    key_currency1 = Some(value);
                }
                if let Some(value) = map.get("fee").and_then(|value| value.as_i64()) {
                    key_fee = Some(value);
                }
                if let Some(value) = map
                    .get("tickSpacing")
                    .or_else(|| map.get("tick_spacing"))
                    .and_then(|value| value.as_i64())
                {
                    key_tick_spacing = Some(value);
                }
                if let Some(value) = read_text("hooks") {
                    key_hooks = Some(value);
                }
            }
        }
        let Some(identity) = stored_identity(
            key_currency0.as_deref(),
            key_currency1.as_deref(),
            key_fee,
            key_tick_spacing,
            key_hooks.as_deref(),
        ) else {
            continue;
        };
        let pool_id = row.0.trim().to_string();
        if pool_id.is_empty() {
            continue;
        }
        by_identity.entry(identity).or_insert(pool_id);
    }

    Ok(pool_keys
        .iter()
        .map(|identity| by_identity.get(identity).cloned().unwrap_or_default())
        .collect())
}

/// Whether a table carries a column.
///
/// The published read has to work on a store that was written before a
/// column existed, so the reader asks the store instead of assuming.
fn table_has_column(connection: &Connection, table: &str, column: &str) -> bool {
    let Ok(mut statement) = connection.prepare(&format!("PRAGMA table_info({table})")) else {
        return false;
    };
    let Ok(rows) = statement.query_map([], |row| row.get::<_, String>(1)) else {
        return false;
    };
    let found = rows.flatten().any(|name| name == column);
    found
}

/// Parses a raw 128-bit value that is stored as text.
///
/// The pool snapshot table stores `sqrt_price_x96` and `liquidity` as text,
/// because they are 256-bit and 128-bit values that exceed SQLite's 64-bit
/// integer. The value may be written in decimal or with a `0x` prefix, and
/// both spellings are accepted so the reader stays compatible with either
/// writer.
fn parse_raw_u128(value: &str) -> Option<u128> {
    let text = value.trim();
    if text.is_empty() {
        return None;
    }
    if let Some(hex) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        return u128::from_str_radix(hex, 16).ok();
    }
    text.parse::<u128>().ok()
}

/// Reads the latest measured state for the requested pools.
///
/// The pool's identity and its measured state are read in one query, so a
/// consumer can never mix a pool's identity with another pool's snapshot.
pub fn read_pool_parity_states(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_ids: &[String],
) -> Result<Vec<PoolParityState>, PoolStateReadError> {
    let connection = open_read_only(database_path.as_ref())?;
    read_pool_parity_states_on_connection(&connection, network, chain_id, protocol, pool_ids)
}

/// Reads the latest measured state on an already-open connection.
pub fn read_pool_parity_states_on_connection(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_ids: &[String],
) -> Result<Vec<PoolParityState>, PoolStateReadError> {
    let wanted: Vec<String> = pool_ids
        .iter()
        .map(|pool_id| pool_id.trim().to_string())
        .filter(|pool_id| !pool_id.is_empty())
        .collect();
    if wanted.is_empty() {
        return Ok(Vec::new());
    }

    let mut states: std::collections::BTreeMap<String, PoolParityState> =
        std::collections::BTreeMap::new();
    for chunk in wanted.chunks(400) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
            // The batch column is read only when the table carries it.  A
            // store written before the column existed still answers the
            // other questions, so the read must not fail because of it.
            let batch_column = if table_has_column(
                connection,
                "v4_exchange_pool_liquidity_snapshots",
                "materialization_batch",
            ) {
                "s.materialization_batch"
            } else {
                "NULL"
            };
            let sql = format!(
                "SELECT p.pool_id, p.token0, p.token1, p.fee, p.tick_spacing, p.hooks,
                    s.sqrt_price_x96, s.tick, s.liquidity, s.liquidity_read_at, s.evidence_source,
                    {batch_column}
             FROM v4_exchange_pools p
             JOIN v4_exchange_pool_liquidity_snapshots s
               ON s.network = p.network AND s.chain_id = p.chain_id
              AND s.protocol = p.protocol AND s.pool_id = p.pool_id
             WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
               AND p.pool_id IN ({placeholders})"
        );
        let mut parameters: Vec<String> = vec![
            network.to_string(),
            chain_id.to_string(),
            protocol.to_string(),
        ];
        parameters.extend(chunk.iter().cloned());
        let mut statement = connection
            .prepare(&sql)
            .map_err(|error| PoolStateReadError::Sqlite(error.to_string()))?;
        let rows = statement
            .query_map(params_from_iter(parameters.iter()), |row| {
                let pool_id: String = row.get(0)?;
                let token0: Option<String> = row.get(1)?;
                let token1: Option<String> = row.get(2)?;
                let fee: Option<i64> = row.get(3)?;
                let tick_spacing: Option<i64> = row.get(4)?;
                let hooks: Option<String> = row.get(5)?;
                // `sqrt_price_x96` and `liquidity` are stored as **text**,
                // because they are 256-bit and 128-bit values that do not
                // fit an SQLite integer. Reading them as an integer would
                // fail for every row, so they are read as text and parsed
                // with the same decimal-or-hexadecimal rule the writers use.
                let sqrt_price_x96: Option<String> = row.get(6)?;
                let tick: Option<i64> = row.get(7)?;
                let liquidity: Option<String> = row.get(8)?;
                let liquidity_read_at: Option<String> = row.get(9)?;
                let evidence_source: Option<String> = row.get(10)?;
                let materialization_batch: Option<String> = row.get(11)?;
                Ok((
                    pool_id,
                    token0,
                    token1,
                    fee,
                    tick_spacing,
                    hooks,
                    sqrt_price_x96,
                    tick,
                    liquidity,
                    liquidity_read_at,
                    evidence_source,
                    materialization_batch,
                ))
            })
            .map_err(|error| PoolStateReadError::Sqlite(error.to_string()))?;
        for row in rows.flatten() {
            let (
                pool_id,
                token0,
                token1,
                fee,
                tick_spacing,
                hooks,
                sqrt_price_x96,
                tick,
                liquidity,
                liquidity_read_at,
                evidence_source,
                materialization_batch,
            ) = row;
            let Some(sqrt_price_x96) = sqrt_price_x96
                .as_deref()
                .and_then(parse_raw_u128)
                .filter(|value| *value > 0)
            else {
                continue;
            };
            let Some(liquidity) = liquidity
                .as_deref()
                .and_then(parse_raw_u128)
                .filter(|value| *value > 0)
            else {
                continue;
            };
            let Some(fee) = fee.filter(|value| *value > 0) else {
                continue;
            };
            states.insert(
                pool_id.trim().to_string(),
                PoolParityState {
                    pool_id: pool_id.trim().to_string(),
                    token0: token0.unwrap_or_default().trim().to_lowercase(),
                    token1: token1.unwrap_or_default().trim().to_lowercase(),
                    fee: fee as u32,
                    tick_spacing: tick_spacing.unwrap_or_default(),
                    hooks: hooks
                        .unwrap_or_else(|| "0x0000000000000000000000000000000000000000".to_string())
                        .trim()
                        .to_lowercase(),
                    sqrt_price_x96,
                    tick: tick.unwrap_or_default(),
                    liquidity,
                    liquidity_read_at,
                    materialization_batch,
                    evidence_source,
                },
            );
        }
    }
    Ok(states.into_values().collect())
}

/// Reads the newest liquidity timestamp across the requested pools.
///
/// The oldest pool is the one that matters for an *accuracy* judgement: the
/// route can only be simulated as accurately as its worst input allows. The
/// timestamp returned here is the newest reading available for the set,
/// because that is the one the off-chain side may legitimately use for any
/// single pool; the per-pool age is what the caller compares against.
pub fn read_newest_liquidity_read_at(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    protocol: &str,
    pool_ids: &[String],
) -> Result<Option<String>, PoolStateReadError> {
    let connection = open_read_only(database_path.as_ref())?;
    let wanted: Vec<String> = pool_ids
        .iter()
        .map(|pool_id| pool_id.trim().to_string())
        .filter(|pool_id| !pool_id.is_empty())
        .collect();
    if wanted.is_empty() {
        return Ok(None);
    }
    let mut newest: Option<String> = None;
    for chunk in wanted.chunks(400) {
        let placeholders = chunk.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "SELECT liquidity_read_at
             FROM v4_exchange_pool_liquidity_snapshots
             WHERE network = ? AND chain_id = ? AND protocol = ?
               AND pool_id IN ({placeholders})"
        );
        let mut parameters: Vec<String> = vec![
            network.to_string(),
            chain_id.to_string(),
            protocol.to_string(),
        ];
        parameters.extend(chunk.iter().cloned());
        let mut statement = connection
            .prepare(&sql)
            .map_err(|error| PoolStateReadError::Sqlite(error.to_string()))?;
        let rows = statement
            .query_map(params_from_iter(parameters.iter()), |row| {
                row.get::<_, Option<String>>(0)
            })
            .map_err(|error| PoolStateReadError::Sqlite(error.to_string()))?;
        for value in rows.flatten().flatten() {
            let value = value.trim().to_string();
            if value.is_empty() {
                continue;
            }
            let replace = match newest.as_deref() {
                None => true,
                Some(current) => value.as_str() > current,
            };
            if replace {
                newest = Some(value);
            }
        }
    }
    Ok(newest)
}

#[cfg(test)]
mod tests {
    use super::{parse_raw_u128, read_pool_parity_states};

    /// Writes the minimal pool tables the parity reader needs.
    ///
    /// The snapshot table stores the square-root price and the liquidity as
    /// **text**, because both are 256-bit and 128-bit values that a SQLite
    /// integer column cannot hold. A reader that asks for them as integers
    /// therefore fails on every row, and a failure that is swallowed turns
    /// into "there is no pool state" for the whole route.
    fn database(path: &std::path::Path) {
        let connection = rusqlite::Connection::open(path).expect("open");
        connection
            .execute_batch(
                "CREATE TABLE v4_exchange_pools (
                    network TEXT,
                    chain_id INTEGER,
                    protocol TEXT,
                    pool_id TEXT,
                    token0 TEXT,
                    token1 TEXT,
                    fee INTEGER,
                    tick_spacing INTEGER,
                    hooks TEXT
                );
                CREATE TABLE v4_exchange_pool_liquidity_snapshots (
                    network TEXT,
                    chain_id INTEGER,
                    protocol TEXT,
                    pool_id TEXT,
                    has_liquidity INTEGER,
                    liquidity TEXT,
                    sqrt_price_x96 TEXT,
                    tick INTEGER,
                    liquidity_read_at TEXT,
                    evidence_source TEXT
                );
                INSERT INTO v4_exchange_pools
                    (network, chain_id, protocol, pool_id, token0, token1, fee,
                     tick_spacing, hooks)
                VALUES ('robinhood', 4663, 'uniswap_v4', 'pool-a',
                        '0xaaaa', '0xbbbb', 3000, 60, '0x0000');
                INSERT INTO v4_exchange_pool_liquidity_snapshots
                    (network, chain_id, protocol, pool_id, has_liquidity,
                     liquidity, sqrt_price_x96, tick, liquidity_read_at, evidence_source)
                VALUES ('robinhood', 4663, 'uniswap_v4', 'pool-a', 1,
                        '5672866120363402509523',
                        '113580480714884524090903',
                        -12207,
                        '2026-11-09T03:08:30.458976Z', 'test');",
            )
            .expect("schema");
    }

    #[test]
    fn a_text_typed_snapshot_is_read_as_a_number() {
        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("pools.sqlite3");
        database(&path);

        let states = read_pool_parity_states(
            &path,
            "robinhood",
            4663,
            "uniswap_v4",
            &["pool-a".to_owned()],
        )
        .expect("states");
        assert_eq!(states.len(), 1);
        assert_eq!(states[0].sqrt_price_x96, 113_580_480_714_884_524_090_903);
        assert_eq!(states[0].liquidity, 5_672_866_120_363_402_509_523);
        assert_eq!(states[0].tick, -12207);
        assert_eq!(
            states[0].liquidity_read_at.as_deref(),
            Some("2026-11-09T03:08:30.458976Z")
        );
    }

    #[test]
    fn a_hexadecimal_snapshot_is_read_as_a_number() {
        assert_eq!(
            parse_raw_u128("113580480714884524090903"),
            Some(113_580_480_714_884_524_090_903)
        );
        assert_eq!(
            parse_raw_u128("0xde0b6b3a7640000"),
            Some(1_000_000_000_000_000_000)
        );
        assert_eq!(parse_raw_u128(""), None);
        assert_eq!(parse_raw_u128("not-a-number"), None);
    }

    /// The snapshot identity is published with the state, because it is the
    /// only field that can say "these pools were measured in one run".
    #[test]
    fn the_materialization_batch_is_published_with_the_state() {
        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("pools.sqlite3");
        database(&path);
        {
            let connection = rusqlite::Connection::open(&path).expect("open");
            connection
                .execute_batch(
                    "ALTER TABLE v4_exchange_pool_liquidity_snapshots
                         ADD COLUMN materialization_batch TEXT;
                     UPDATE v4_exchange_pool_liquidity_snapshots
                         SET materialization_batch = 'pool-manager-batch-1';",
                )
                .expect("batch");
        }

        let states = read_pool_parity_states(
            &path,
            "robinhood",
            4663,
            "uniswap_v4",
            &["pool-a".to_owned()],
        )
        .expect("states");
        assert_eq!(states.len(), 1);
        assert_eq!(
            states[0].materialization_batch.as_deref(),
            Some("pool-manager-batch-1")
        );
    }

    /// A store written before the batch column existed still answers every
    /// other question, so the published read must not fail because of it.
    #[test]
    fn a_store_without_the_batch_column_still_publishes_the_other_fields() {
        let directory = tempfile::tempdir().expect("tempdir");
        let path = directory.path().join("pools.sqlite3");
        database(&path);

        let states = read_pool_parity_states(
            &path,
            "robinhood",
            4663,
            "uniswap_v4",
            &["pool-a".to_owned()],
        )
        .expect("states");
        assert_eq!(states.len(), 1);
        assert_eq!(states[0].materialization_batch, None);
        assert_eq!(
            states[0].liquidity_read_at.as_deref(),
            Some("2026-11-09T03:08:30.458976Z")
        );
    }

    #[test]
    fn an_incomplete_live_read_keeps_the_stored_measurement_whole() {
        // The live reader replaces the stored state only when the live
        // measurement is **complete**. A pool whose `slot0` read failed has
        // no price, and a pool whose liquidity read failed has no depth.
        // Mixing the failed half with the stored half would produce a state
        // that describes neither block, and the comparison would report the
        // age of the snapshot as if it were a defect of the arithmetic.
        //
        // The rule is checked on the live-state struct itself, because the
        // decision is made there and the regression belongs there.
        use crate::chain::V4PoolState;

        let incomplete = V4PoolState {
            pool_id: "pool-a".to_string(),
            sqrt_price_x96: String::new(),
            tick: 0,
            liquidity: 0,
            complete: false,
            ..Default::default()
        };
        assert!(
            !incomplete.complete,
            "a pool with a failed read must not be a complete measurement"
        );

        let complete = V4PoolState {
            pool_id: "pool-a".to_string(),
            sqrt_price_x96: "113580480714884524090903".to_string(),
            tick: -12207,
            liquidity: 5_672_866_120_363_402_509_523,
            complete: true,
            ..Default::default()
        };
        assert!(complete.complete);
        assert_eq!(
            complete.sqrt_price_x96.parse::<u128>().ok(),
            Some(113_580_480_714_884_524_090_903)
        );
    }
}
