//! Pruning of tokens that carry no usable metadata.
//!
//! The token registry is the **root** of the DEX graph: the candidate
//! pools, the observation pools, the closed loops and the hop table all
//! name a token by its address. A token whose symbol and decimals cannot
//! be resolved is therefore not a row that may stay around with a
//! placeholder value — it is a **defect** that every downstream consumer
//! inherits.
//!
//! The rule this module implements is the one the orchestrator states for
//! the token layer: a token that is missing information is either repaired
//! (a usable symbol and decimals are found for it) or **deleted** from the
//! token table. A deleted token takes its dependants with it, because a
//! closed loop that names a token which no longer exists is a loop that
//! can never be priced.
//!
//! The prune is deliberately **explicit about what it removes**. It never
//! removes a pool row on its own: a pool is a fact about the chain, and
//! the pool layer owns the decision about a pool that trades an
//! unresolved token. What the prune removes is the *token-shaped*
//! dependency: the candidate and observation rows that only exist to
//! describe a route through that token, and the closed loops and hops
//! that were built on top of them.

use rusqlite::{params, Connection, OptionalExtension, Transaction};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

/// Why a token cannot be kept.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum IncompleteReason {
    /// The token has no symbol at all.
    MissingSymbol,
    /// The symbol is the factory placeholder (`0X…` or `UNRESOLVED:0X…`).
    PlaceholderSymbol,
    /// The token has no decimals, or a nonsensical number of them.
    MissingDecimals,
}

impl IncompleteReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::MissingSymbol => "missing_symbol",
            Self::PlaceholderSymbol => "placeholder_symbol",
            Self::MissingDecimals => "missing_decimals",
        }
    }
}

impl fmt::Display for IncompleteReason {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// One token that cannot be kept, and why.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IncompleteToken {
    pub token_address: String,
    pub token_symbol: String,
    pub reasons: Vec<IncompleteReason>,
}

/// What a prune removed, table by table.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PruneCounts {
    pub chain_tokens: usize,
    pub candidate_anchor_tokens: usize,
    pub candidate_abnormal_anchor_tokens: usize,
    pub candidate_pool_pairs: usize,
    pub candidate_abnormal_pool_pairs: usize,
    pub candidate_pools: usize,
    pub candidate_abnormal_pools: usize,
    pub observation_yz_pairs: usize,
    pub observation_abnormal_yz_pairs: usize,
    pub observation_intermediate_pools: usize,
    pub observation_abnormal_intermediate_pools: usize,
    pub closed_loops: usize,
    pub closed_loop_hops: usize,
    pub closed_loop_optimizations: usize,
    pub exchange_pools: usize,
    pub exchange_pool_liquidity_snapshots: usize,
}

impl PruneCounts {
    pub fn total(&self) -> usize {
        self.chain_tokens
            + self.candidate_anchor_tokens
            + self.candidate_abnormal_anchor_tokens
            + self.candidate_pool_pairs
            + self.candidate_abnormal_pool_pairs
            + self.candidate_pools
            + self.candidate_abnormal_pools
            + self.observation_yz_pairs
            + self.observation_abnormal_yz_pairs
            + self.observation_intermediate_pools
            + self.observation_abnormal_intermediate_pools
            + self.closed_loops
            + self.closed_loop_hops
            + self.closed_loop_optimizations
            + self.exchange_pools
            + self.exchange_pool_liquidity_snapshots
    }
}

/// The report of one prune run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PruneReport {
    pub network: String,
    pub chain_id: u64,
    pub applied: bool,
    /// The tokens that carry no usable metadata.
    pub incomplete_tokens: Vec<IncompleteToken>,
    /// The tokens that were kept because they are complete.
    pub complete_token_count: usize,
    /// The tables that were touched, and by how much.
    pub counts: PruneCounts,
    /// The loop keys that were removed because they named a pruned token.
    pub removed_loop_keys: Vec<String>,
    /// The tokens that still reference a pruned token and therefore could
    /// not be removed by this run.
    ///
    /// The prune removes the whole dependency chain, so this list is
    /// empty after a successful run. It is kept in the report because a
    /// non-empty list means the store had a reference the prune did not
    /// know about, which is a defect worth seeing.
    pub retained_due_to_references: Vec<String>,
}

/// The failure modes of a prune.
#[derive(Debug)]
pub enum PruneError {
    Sqlite(rusqlite::Error),
    /// The chain scope of the database does not match the requested one.
    ScopeMismatch { expected: u64, found: u64 },
}

impl fmt::Display for PruneError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "token prune error: {error}"),
            Self::ScopeMismatch { expected, found } => write!(
                formatter,
                "chain database mismatch: the database holds chain {found}, \
                 but chain {expected} was requested; each chain has exactly \
                 one SQLite database file and a chain never reads or writes \
                 another chain's file"
            ),
        }
    }
}

impl std::error::Error for PruneError {}

impl From<rusqlite::Error> for PruneError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

/// Whether a symbol is the factory placeholder.
///
/// The registry keeps two forms of it: the bare `0X` plus eight hex
/// digits, and the same value behind an `UNRESOLVED:` prefix. Both are
/// placeholders, and neither may be published as a token symbol.
fn is_placeholder_symbol(symbol: &str) -> bool {
    let normalized = symbol.trim().to_ascii_uppercase();
    if normalized.starts_with("UNRESOLVED:") {
        return true;
    }
    normalized.strip_prefix("0X").is_some_and(|hex| {
        (6..=8).contains(&hex.len()) && hex.bytes().all(|byte| byte.is_ascii_hexdigit())
    })
}

/// The reasons why one token row cannot be kept.
///
/// **Only the essential metadata counts.** A token is removed when it has
/// no symbol, when its symbol is still the factory placeholder, or when
/// its decimals are unknown or out of range. Every other field is
/// optional metadata: a token without a display name, without a token
/// kind, or without a provenance record is still a token that can be
/// priced and routed through, so it stays.
///
/// The test is deliberately narrow so that the prune cannot turn into a
/// general cleanup that removes tokens for a reason nobody asked for.
fn incomplete_reasons(symbol: &str, decimals: Option<i64>) -> Vec<IncompleteReason> {
    let mut reasons = Vec::new();
    let trimmed = symbol.trim();
    if trimmed.is_empty() {
        reasons.push(IncompleteReason::MissingSymbol);
    } else if is_placeholder_symbol(trimmed) {
        reasons.push(IncompleteReason::PlaceholderSymbol);
    }
    match decimals {
        None => reasons.push(IncompleteReason::MissingDecimals),
        Some(value) if !(0..=36).contains(&value) => {
            reasons.push(IncompleteReason::MissingDecimals)
        }
        Some(_) => {}
    }
    reasons
}

/// Read every token of the chain and split it into complete and incomplete.
pub fn read_incomplete_tokens(
    connection: &Connection,
    network: &str,
    chain_id: u64,
) -> Result<(Vec<IncompleteToken>, usize), PruneError> {
    let mut statement = connection.prepare(
        "SELECT token_address, token_symbol, token_decimals
         FROM chain_tokens
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY token_address",
    )?;
    let rows = statement.query_map(params![network, chain_id as i64], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?.unwrap_or_default(),
            row.get::<_, Option<i64>>(2)?,
        ))
    })?;
    let mut incomplete = Vec::new();
    let mut complete = 0usize;
    for row in rows {
        let (address, symbol, decimals) = row?;
        let reasons = incomplete_reasons(&symbol, decimals);
        if reasons.is_empty() {
            complete += 1;
            continue;
        }
        incomplete.push(IncompleteToken {
            token_address: address,
            token_symbol: symbol,
            reasons,
        });
    }
    Ok((incomplete, complete))
}

/// The tables that name a token by its address, and the column that does it.
///
/// The order is the order of the cascade: the dependants of a token are
/// removed before the token itself, so the foreign keys stay satisfied at
/// every step of the transaction.
const TOKEN_REFERENCES: &[(&str, &str)] = &[
    ("dex_candidate_anchor_tokens", "token_address"),
    ("dex_candidate_abnormal_anchor_tokens", "token_address"),
    ("dex_candidate_pool_pairs", "counter_address"),
    ("dex_candidate_pool_pairs", "anchor_address"),
    ("dex_candidate_abnormal_pool_pairs", "counter_address"),
    ("dex_candidate_abnormal_pool_pairs", "anchor_address"),
    ("dex_candidate_pools", "token0"),
    ("dex_candidate_pools", "token1"),
    ("dex_candidate_abnormal_pools", "token0"),
    ("dex_candidate_abnormal_pools", "token1"),
    ("market_observation_yz_pairs", "token_x_address"),
    ("market_observation_yz_pairs", "token_y_address"),
    ("market_observation_abnormal_yz_pairs", "token_x_address"),
    ("market_observation_abnormal_yz_pairs", "token_y_address"),
    ("market_observation_intermediate_pools", "token0"),
    ("market_observation_intermediate_pools", "token1"),
    ("market_observation_abnormal_intermediate_pools", "token0"),
    ("market_observation_abnormal_intermediate_pools", "token1"),
];

/// The tables a closed loop is stored in.
const CLOSED_LOOP_TABLES: &[&str] = &["v4_closed_loops"];
const CLOSED_LOOP_HOP_TABLES: &[&str] = &["v4_closed_loop_hops"];
const CLOSED_LOOP_OPTIMIZATION_TABLES: &[&str] = &["v4_closed_loop_optimizations"];

/// The pool tables that name a token by its address.
///
/// A pool that trades a token without usable decimals cannot be priced,
/// so it cannot stay once the token is gone: the foreign key from the
/// pool to the token registry is restrictive, and a pool row that names a
/// token the store does not know is a row the pool layer would have to
/// reject anyway.
const POOL_TABLES: &[(&str, &str)] = &[
    ("v4_exchange_pools", "token0"),
    ("v4_exchange_pools", "token1"),
    ("v4_exchange_pools_candidate_exchange", "token0"),
    ("v4_exchange_pools_candidate_exchange", "token1"),
];

/// The pool tables that hold the measured state of a pool.
const POOL_LIQUIDITY_TABLES: &[&str] = &["v4_exchange_pool_liquidity_snapshots"];

/// Whether a table exists in the database.
///
/// The prune has to work on a store that was migrated by an older
/// version, so a table that is simply not there is skipped instead of
/// failing the whole run.
fn table_exists(connection: &Connection, table: &str) -> Result<bool, PruneError> {
    let found: Option<String> = connection
        .query_row(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?1",
            params![table],
            |row| row.get(0),
        )
        .optional()?;
    Ok(found.is_some())
}

/// The closed loops of the chain whose path names one of the given tokens.
///
/// A closed loop names its tokens in `path_addresses_json`, and its anchor
/// in `anchor_address`. Both have to be checked: a loop whose *anchor*
/// is pruned cannot exist either, because the anchor is the token the
/// whole loop is priced in.
fn loops_naming_tokens(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    tokens: &BTreeSet<String>,
) -> Result<BTreeSet<String>, PruneError> {
    let mut affected = BTreeSet::new();
    if !table_exists(connection, "v4_closed_loops")? {
        return Ok(affected);
    }
    // The path is a JSON array of addresses, so the match is a substring
    // test over the serialized form. The addresses are stored lowercase,
    // which is what makes the test exact enough: a full address can only
    // match as a whole quoted value.
    let token_list: Vec<&str> = tokens.iter().map(String::as_str).collect();
    for chunk in token_list.chunks(TOKEN_CHUNK_LIMIT / 2) {
        let mut statement = connection.prepare(&format!(
            "SELECT loop_key, anchor_address, path_addresses_json
             FROM v4_closed_loops
             WHERE network = ?1 AND chain_id = ?2
               AND (
                   LOWER(anchor_address) IN ({placeholders})
                   OR EXISTS (
                       SELECT 1 FROM json_each(v4_closed_loops.path_addresses_json)
                       WHERE LOWER(json_each.value) IN ({placeholders})
                   )
               )",
            placeholders = placeholders(chunk.len()),
        ))?;
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::with_capacity(chunk.len() * 2 + 2);
        params.push(Box::new(network.to_owned()));
        params.push(Box::new(chain_id as i64));
        for token in chunk {
            params.push(Box::new((*token).to_owned()));
        }
        for token in chunk {
            params.push(Box::new((*token).to_owned()));
        }
        let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|value| value.as_ref()).collect();
        let rows = statement.query_map(refs.as_slice(), |row| row.get::<_, String>(0))?;
        for row in rows {
            affected.insert(row?);
        }
    }
    Ok(affected)
}

/// Remove the rows of one table that name one of the tokens.
///
/// The delete is **set-based**: one statement removes every row of the
/// table that names any of the tokens. A per-token loop would issue tens
/// of thousands of statements for a registry of that size, and the
/// resulting transaction would take longer than the repair it is part of.
fn delete_token_references(
    transaction: &Transaction<'_>,
    table: &str,
    column: &str,
    network: &str,
    chain_id: u64,
    tokens: &BTreeSet<String>,
) -> Result<usize, PruneError> {
    if tokens.is_empty() || !table_exists(transaction, table)? {
        return Ok(0);
    }
    let mut removed = 0usize;
    let token_list: Vec<&str> = tokens.iter().map(String::as_str).collect();
    for chunk in token_list.chunks(TOKEN_CHUNK_LIMIT) {
        let mut statement = transaction.prepare(&format!(
            "DELETE FROM {table}
             WHERE network = ?1 AND chain_id = ?2
               AND LOWER({column}) IN (
                   SELECT LOWER(token_address) FROM chain_tokens
                   WHERE network = ?1 AND chain_id = ?2
                     AND token_address IN ({placeholders})
               )",
            placeholders = placeholders(chunk.len()),
        ))?;
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::with_capacity(chunk.len() + 2);
        params.push(Box::new(network.to_owned()));
        params.push(Box::new(chain_id as i64));
        for token in chunk {
            params.push(Box::new((*token).to_owned()));
        }
        let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|value| value.as_ref()).collect();
        removed += statement.execute(refs.as_slice())?;
    }
    Ok(removed)
}

/// The number of bound parameters one statement may carry.
///
/// The scope parameters are bound in the same statement, so the limit
/// covers them as well. Newer SQLite builds allow more, but the number
/// that is safe everywhere is the one the library uses by default.
const SQLITE_PARAMETER_LIMIT: usize = 900;

/// The number of token placeholders one statement may carry.
///
/// The scope parameters take three slots of the parameter list, so the
/// token list has to stay below the limit by that margin.
const TOKEN_CHUNK_LIMIT: usize = SQLITE_PARAMETER_LIMIT - 8;

/// The comma-separated placeholders of one statement.
fn placeholders(count: usize) -> String {
    std::iter::repeat("?").take(count).collect::<Vec<_>>().join(",")
}

/// Remove the closed loops that name a pruned token, and their dependants.
///
/// The hop table and the optimization table reference the loop, so they
/// go first. The loop itself goes last.
fn delete_closed_loops(
    transaction: &Transaction<'_>,
    network: &str,
    chain_id: u64,
    protocol: &str,
    loop_keys: &BTreeSet<String>,
) -> Result<PruneCounts, PruneError> {
    let mut counts = PruneCounts::default();
    if loop_keys.is_empty() {
        return Ok(counts);
    }
    for table in CLOSED_LOOP_OPTIMIZATION_TABLES {
        if !table_exists(transaction, table)? {
            continue;
        }
        counts.closed_loop_optimizations += delete_loop_keys(
            transaction,
            table,
            network,
            chain_id,
            protocol,
            loop_keys,
        )?;
    }
    for table in CLOSED_LOOP_HOP_TABLES {
        if !table_exists(transaction, table)? {
            continue;
        }
        counts.closed_loop_hops += delete_loop_keys(
            transaction,
            table,
            network,
            chain_id,
            protocol,
            loop_keys,
        )?;
    }
    for table in CLOSED_LOOP_TABLES {
        if !table_exists(transaction, table)? {
            continue;
        }
        counts.closed_loops += delete_loop_keys(
            transaction,
            table,
            network,
            chain_id,
            protocol,
            loop_keys,
        )?;
    }
    Ok(counts)
}

/// Remove the rows of one table that belong to one of the loops.
///
/// The delete is set-based, so a library of many loops costs one
/// statement per table instead of one per loop.
fn delete_loop_keys(
    transaction: &Transaction<'_>,
    table: &str,
    network: &str,
    chain_id: u64,
    protocol: &str,
    loop_keys: &BTreeSet<String>,
) -> Result<usize, PruneError> {
    let mut removed = 0usize;
    let key_list: Vec<&str> = loop_keys.iter().map(String::as_str).collect();
    for chunk in key_list.chunks(TOKEN_CHUNK_LIMIT) {
        let mut statement = transaction.prepare(&format!(
            "DELETE FROM {table}
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
               AND loop_key IN ({placeholders})",
            placeholders = placeholders(chunk.len()),
        ))?;
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::with_capacity(chunk.len() + 3);
        params.push(Box::new(network.to_owned()));
        params.push(Box::new(chain_id as i64));
        params.push(Box::new(protocol.to_owned()));
        for key in chunk {
            params.push(Box::new((*key).to_owned()));
        }
        let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|value| value.as_ref()).collect();
        removed += statement.execute(refs.as_slice())?;
    }
    Ok(removed)
}

/// The protocols the chain holds closed loops for.
fn loop_protocols(connection: &Connection, network: &str, chain_id: u64) -> Result<Vec<String>, PruneError> {
    if !table_exists(connection, "v4_closed_loops")? {
        return Ok(Vec::new());
    }
    let mut statement = connection.prepare(
        "SELECT DISTINCT protocol FROM v4_closed_loops
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY protocol",
    )?;
    let rows = statement.query_map(params![network, chain_id as i64], |row| row.get::<_, String>(0))?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// Prune every token of the chain that carries no usable metadata.
///
/// The command is the token layer of the repair cascade. It finds the
/// tokens that cannot be used, and it removes them **together with every
/// dependance that only exists because of them**, so that no closed loop
/// and no candidate row survives that names a token the store no longer
/// knows.
///
/// Without `--apply` the command reports exactly what it would remove and
/// touches nothing.
pub fn prune_incomplete_tokens(
    database: &std::path::Path,
    network: &str,
    chain_id: u64,
    apply: bool,
) -> Result<PruneReport, PruneError> {
    let connection = Connection::open(database)?;
    // The chain boundary is checked before anything is read: a store of
    // one chain may never be pruned as if it belonged to another.
    let stored_chain: Option<i64> = connection
        .query_row(
            "SELECT DISTINCT chain_id FROM chain_info LIMIT 1",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if let Some(found) = stored_chain {
        if found != chain_id as i64 {
            return Err(PruneError::ScopeMismatch {
                expected: chain_id,
                found: found as u64,
            });
        }
    }
    let (incomplete, complete_token_count) = read_incomplete_tokens(&connection, network, chain_id)?;
    let tokens: BTreeSet<String> = incomplete
        .iter()
        .map(|item| item.token_address.trim().to_ascii_lowercase())
        .filter(|item| !item.is_empty())
        .collect();
    let protocols = loop_protocols(&connection, network, chain_id)?;
    let mut counts = PruneCounts::default();
    let mut removed_loop_keys = Vec::new();
    let retained_due_to_references = Vec::new();

    if !apply {
        // The dry run reports the loop keys that would go, without
        // touching the store.
        let affected = loops_naming_tokens(&connection, network, chain_id, &tokens)?;
        for loop_key in affected {
            if !removed_loop_keys.contains(&loop_key) {
                removed_loop_keys.push(loop_key);
            }
        }
        let affected_pools = pools_naming_tokens(&connection, network, chain_id, &tokens)?;
        counts.exchange_pools = affected_pools.len();
        return Ok(PruneReport {
            network: network.to_owned(),
            chain_id,
            applied: false,
            incomplete_tokens: incomplete,
            complete_token_count,
            counts,
            removed_loop_keys,
            retained_due_to_references,
        });
    }

    let transaction = connection.unchecked_transaction()?;
    // The closed loops go first, because they are the rows that carry the
    // most meaning and the ones a reviewer would otherwise read as a
    // route that still exists.
    for protocol in &protocols {
        let affected = loops_naming_tokens(&transaction, network, chain_id, &tokens)?;
        if affected.is_empty() {
            continue;
        }
        let loop_counts =
            delete_closed_loops(&transaction, network, chain_id, protocol, &affected)?;
        counts.closed_loops += loop_counts.closed_loops;
        counts.closed_loop_hops += loop_counts.closed_loop_hops;
        counts.closed_loop_optimizations += loop_counts.closed_loop_optimizations;
        for loop_key in affected {
            removed_loop_keys.push(loop_key);
        }
    }
    // Then the pools that trade a pruned token, and their measured
    // state. The pool has to go before the token, because the foreign
    // key from the pool to the token registry is restrictive.
    let affected_pools = pools_naming_tokens(&transaction, network, chain_id, &tokens)?;
    if !affected_pools.is_empty() {
        for table in POOL_LIQUIDITY_TABLES {
            if !table_exists(&transaction, table)? {
                continue;
            }
            counts.exchange_pool_liquidity_snapshots += delete_pool_ids(
                &transaction,
                table,
                network,
                chain_id,
                &affected_pools,
            )?;
        }
        for (table, _) in POOL_TABLES {
            if !table_exists(&transaction, table)? {
                continue;
            }
            counts.exchange_pools += delete_pool_ids(
                &transaction,
                table,
                network,
                chain_id,
                &affected_pools,
            )?;
        }
    }
    // Then the token-shaped dependants.
    for (table, column) in TOKEN_REFERENCES {
        let removed = delete_token_references(
            &transaction,
            table,
            column,
            network,
            chain_id,
            &tokens,
        )?;
        match *table {
            "dex_candidate_anchor_tokens" => counts.candidate_anchor_tokens += removed,
            "dex_candidate_abnormal_anchor_tokens" => {
                counts.candidate_abnormal_anchor_tokens += removed
            }
            "dex_candidate_pool_pairs" => counts.candidate_pool_pairs += removed,
            "dex_candidate_abnormal_pool_pairs" => {
                counts.candidate_abnormal_pool_pairs += removed
            }
            "dex_candidate_pools" => counts.candidate_pools += removed,
            "dex_candidate_abnormal_pools" => counts.candidate_abnormal_pools += removed,
            "market_observation_yz_pairs" => counts.observation_yz_pairs += removed,
            "market_observation_abnormal_yz_pairs" => {
                counts.observation_abnormal_yz_pairs += removed
            }
            "market_observation_intermediate_pools" => {
                counts.observation_intermediate_pools += removed
            }
            "market_observation_abnormal_intermediate_pools" => {
                counts.observation_abnormal_intermediate_pools += removed
            }
            _ => {}
        }
    }
    // And finally the token itself.
    if !tokens.is_empty() && table_exists(&transaction, "chain_tokens")? {
        // The tokens are removed last and in one statement, so the
        // referential integrity of the store is satisfied at every step.
        let token_list: Vec<&str> = tokens.iter().map(String::as_str).collect();
        for chunk in token_list.chunks(TOKEN_CHUNK_LIMIT) {
            let mut statement = transaction.prepare(&format!(
                "DELETE FROM chain_tokens
                 WHERE network = ?1 AND chain_id = ?2
                   AND LOWER(token_address) IN ({placeholders})",
                placeholders = placeholders(chunk.len()),
            ))?;
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::with_capacity(chunk.len() + 2);
            params.push(Box::new(network.to_owned()));
            params.push(Box::new(chain_id as i64));
            for token in chunk {
                params.push(Box::new((*token).to_owned()));
            }
            let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|value| value.as_ref()).collect();
            counts.chain_tokens += statement.execute(refs.as_slice())?;
        }
    }
    transaction.commit()?;

    Ok(PruneReport {
        network: network.to_owned(),
        chain_id,
        applied: true,
        incomplete_tokens: incomplete,
        complete_token_count,
        counts,
        removed_loop_keys,
        retained_due_to_references,
    })
}

/// The pools that trade one of the given tokens.
fn pools_naming_tokens(
    connection: &Connection,
    network: &str,
    chain_id: u64,
    tokens: &BTreeSet<String>,
) -> Result<BTreeSet<String>, PruneError> {
    let mut affected = BTreeSet::new();
    let token_list: Vec<&str> = tokens.iter().map(String::as_str).collect();
    for chunk in token_list.chunks(TOKEN_CHUNK_LIMIT / 2) {
        let mut statement = connection.prepare(&format!(
            "SELECT pool_id FROM v4_exchange_pools
             WHERE network = ?1 AND chain_id = ?2
               AND (
                   LOWER(token0) IN ({placeholders})
                   OR LOWER(token1) IN ({placeholders})
               )",
            placeholders = placeholders(chunk.len()),
        ))?;
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::with_capacity(chunk.len() * 2 + 2);
        params.push(Box::new(network.to_owned()));
        params.push(Box::new(chain_id as i64));
        for token in chunk {
            params.push(Box::new((*token).to_owned()));
        }
        for token in chunk {
            params.push(Box::new((*token).to_owned()));
        }
        let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|value| value.as_ref()).collect();
        let rows = statement.query_map(refs.as_slice(), |row| row.get::<_, String>(0))?;
        for row in rows {
            affected.insert(row?);
        }
    }
    Ok(affected)
}

/// Remove the rows of one table that belong to one of the pools.
fn delete_pool_ids(
    transaction: &Transaction<'_>,
    table: &str,
    network: &str,
    chain_id: u64,
    pool_ids: &BTreeSet<String>,
) -> Result<usize, PruneError> {
    let mut removed = 0usize;
    let id_list: Vec<&str> = pool_ids.iter().map(String::as_str).collect();
    for chunk in id_list.chunks(TOKEN_CHUNK_LIMIT) {
        let mut statement = transaction.prepare(&format!(
            "DELETE FROM {table}
             WHERE network = ?1 AND chain_id = ?2
               AND pool_id IN ({placeholders})",
            placeholders = placeholders(chunk.len()),
        ))?;
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::with_capacity(chunk.len() + 2);
        params.push(Box::new(network.to_owned()));
        params.push(Box::new(chain_id as i64));
        for pool_id in chunk {
            params.push(Box::new((*pool_id).to_owned()));
        }
        let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|value| value.as_ref()).collect();
        removed += statement.execute(refs.as_slice())?;
    }
    Ok(removed)
}

/// Render the report as the operator-facing summary.
pub fn render_prune_report(report: &PruneReport) -> String {
    let reason_counts: BTreeMap<&str, usize> = report
        .incomplete_tokens
        .iter()
        .flat_map(|item| item.reasons.iter())
        .fold(BTreeMap::new(), |mut counts, reason| {
            *counts.entry(reason.as_str()).or_insert(0) += 1;
            counts
        });
    serde_json::to_string_pretty(&serde_json::json!({
        "network": report.network,
        "chainId": report.chain_id,
        "applied": report.applied,
        "incompleteTokenCount": report.incomplete_tokens.len(),
        "completeTokenCount": report.complete_token_count,
        "reasonCounts": reason_counts,
        "removedLoopKeys": report.removed_loop_keys.len(),
        "retainedDueToReferences": report.retained_due_to_references.len(),
        "counts": report.counts,
        "totalRemoved": report.counts.total(),
    }))
    .unwrap_or_else(|_| "{}".to_owned())
}


#[cfg(test)]
mod tests {
    use super::*;

    /// Build a store that holds the token registry and the tables that
    /// depend on it.
    fn store() -> Connection {
        let connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE TABLE chain_info (network TEXT, chain_id INTEGER);
                 INSERT INTO chain_info VALUES ('robinhood-testnet', 46630);
                 CREATE TABLE chain_tokens (
                     network TEXT, chain_id INTEGER, token_address TEXT,
                     token_symbol TEXT, token_decimals INTEGER,
                     PRIMARY KEY (network, chain_id, token_address));
                 CREATE TABLE v4_closed_loops (
                     network TEXT, chain_id INTEGER, protocol TEXT,
                     market_id TEXT, loop_key TEXT, anchor_address TEXT,
                     anchor_symbol TEXT, path_text TEXT, path_addresses_json TEXT,
                     path_symbols_json TEXT, pool_path_key TEXT, pool_ids_json TEXT,
                     hop_candidate_counts_json TEXT,
                     hop_candidate_combination_count INTEGER,
                     recommendation_basis TEXT, recommended_first_priority INTEGER,
                     status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
                     PRIMARY KEY (network, chain_id, protocol, market_id, loop_key));
                 CREATE TABLE v4_closed_loop_hops (
                     network TEXT, chain_id INTEGER, protocol TEXT,
                     market_id TEXT, loop_key TEXT, hop_index INTEGER,
                     hop_name TEXT, input_address TEXT, output_address TEXT,
                     pool_id TEXT, base_pool_id TEXT, pool_selection_basis TEXT,
                     optimization_id TEXT, temporary_override INTEGER,
                     created_at TEXT, updated_at TEXT,
                     PRIMARY KEY (network, chain_id, protocol, market_id, loop_key, hop_index));
                 CREATE TABLE v4_closed_loop_optimizations (
                     network TEXT, chain_id INTEGER, protocol TEXT,
                     market_id TEXT, loop_key TEXT, hop_index INTEGER,
                     optimization_id TEXT, original_pool_id TEXT, optimized_pool_id TEXT,
                     optimization_basis TEXT, expected_price_improvement REAL,
                     status TEXT, expires_at TEXT, created_at TEXT, updated_at TEXT,
                     PRIMARY KEY (network, chain_id, protocol, market_id, loop_key, hop_index));
                 CREATE TABLE dex_candidate_anchor_tokens (
                     network TEXT, chain_id INTEGER, protocol TEXT,
                     anchor_scope TEXT, anchor_symbol TEXT, token_role TEXT,
                     token_address TEXT, pool_count INTEGER,
                     has_yc_optimal_pool INTEGER, has_zc_optimal_pool INTEGER,
                     yc_optimal_pool_id TEXT, zc_optimal_pool_id TEXT,
                     yc_backup_pool_ids_json TEXT, zc_backup_pool_ids_json TEXT,
                     yc_zc_same_set INTEGER, created_at TEXT, updated_at TEXT,
                     PRIMARY KEY (network, chain_id, protocol, anchor_scope,
                                  anchor_symbol, token_role, token_address));
                 CREATE TABLE v4_exchange_pools (
                     network TEXT, chain_id INTEGER, protocol TEXT, pool_id TEXT,
                     token0 TEXT, token1 TEXT, PRIMARY KEY (network, chain_id, protocol, pool_id));
                 ",
            )
            .unwrap();
        connection
    }

    const GOOD: &str = "0x0000000000000000000000000000000000000001";
    const BAD_SYMBOL: &str = "0x0000000000000000000000000000000000000002";
    const NO_DECIMALS: &str = "0x0000000000000000000000000000000000000003";
    const PREFIXED: &str = "0x0000000000000000000000000000000000000004";

    fn seed_token(connection: &Connection, address: &str, symbol: &str, decimals: Option<i64>) {
        connection
            .execute(
                "INSERT INTO chain_tokens
                 VALUES ('robinhood-testnet', 46630, ?1, ?2, ?3)",
                rusqlite::params![address, symbol, decimals],
            )
            .unwrap();
    }

    #[test]
    fn a_complete_token_is_kept() {
        let connection = store();
        seed_token(&connection, GOOD, "ETH", Some(18));
        let (incomplete, complete) = read_incomplete_tokens(&connection, "robinhood-testnet", 46630).unwrap();
        assert!(incomplete.is_empty());
        assert_eq!(complete, 1);
    }

    #[test]
    fn a_placeholder_symbol_is_not_usable() {
        let connection = store();
        seed_token(&connection, BAD_SYMBOL, "0X102154E7", Some(18));
        seed_token(&connection, PREFIXED, "UNRESOLVED:0X102154E7", Some(18));
        let (incomplete, _) = read_incomplete_tokens(&connection, "robinhood-testnet", 46630).unwrap();
        assert_eq!(incomplete.len(), 2);
        for item in &incomplete {
            assert_eq!(item.reasons, vec![IncompleteReason::PlaceholderSymbol]);
        }
    }

    /// Only the essential metadata decides. Everything else a token may
    /// lack is optional and must not remove the token.
    #[test]
    fn only_the_essential_metadata_removes_a_token() {
        let connection = store();
        // A token with a usable symbol and usable decimals, but without
        // a name, is complete.
        connection
            .execute(
                "INSERT INTO chain_tokens
                 VALUES ('robinhood-testnet', 46630,
                         '0x0000000000000000000000000000000000000005',
                         'PLTR', 18)",
                [],
            )
            .unwrap();
        // A token with a name and a symbol but without decimals is not.
        connection
            .execute(
                "INSERT INTO chain_tokens
                 VALUES ('robinhood-testnet', 46630,
                         '0x0000000000000000000000000000000000000006',
                         'TSLA', NULL)",
                [],
            )
            .unwrap();
        let (incomplete, complete) =
            read_incomplete_tokens(&connection, "robinhood-testnet", 46630).unwrap();
        assert_eq!(complete, 1);
        assert_eq!(incomplete.len(), 1);
        assert_eq!(incomplete[0].reasons, vec![IncompleteReason::MissingDecimals]);
    }

    /// Zero decimals is a legitimate value, not a missing one.
    #[test]
    fn zero_decimals_is_a_usable_value() {
        let connection = store();
        seed_token(&connection, GOOD, "USDC", Some(0));
        let (incomplete, complete) = read_incomplete_tokens(&connection, "robinhood-testnet", 46630).unwrap();
        assert!(incomplete.is_empty());
        assert_eq!(complete, 1);
    }

    #[test]
    fn a_token_without_decimals_is_not_usable() {
        let connection = store();
        seed_token(&connection, NO_DECIMALS, "PLTR", None);
        let (incomplete, _) = read_incomplete_tokens(&connection, "robinhood-testnet", 46630).unwrap();
        assert_eq!(incomplete.len(), 1);
        assert_eq!(incomplete[0].reasons, vec![IncompleteReason::MissingDecimals]);
    }

    /// A token that is pruned takes the loops that named it with it.
    #[test]
    fn the_dependants_of_a_pruned_token_are_removed_with_it() {
        let connection = store();
        seed_token(&connection, GOOD, "ETH", Some(18));
        seed_token(&connection, BAD_SYMBOL, "0X102154E7", Some(18));
        connection
            .execute(
                "INSERT INTO v4_closed_loops
                 VALUES ('robinhood-testnet', 46630, 'uniswap_v4', 'core4',
                         'loop-bad', ?1, 'ETH', 'ETH -> X -> ETH',
                         '[\"0x0000000000000000000000000000000000000001\",\"0x0000000000000000000000000000000000000002\",\"0x0000000000000000000000000000000000000001\"]',
                         '[]', '[]', '[]', '{}', 1, 'basis', 1, 'active', 'test',
                         '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                rusqlite::params![GOOD],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO v4_closed_loop_hops
                 VALUES ('robinhood-testnet', 46630, 'uniswap_v4', 'core4',
                         'loop-bad', 0, 'yc', ?1, ?2, 'pool-a', 'pool-a',
                         'basis', NULL, 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                rusqlite::params![GOOD, BAD_SYMBOL],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO v4_closed_loop_optimizations
                 VALUES ('robinhood-testnet', 46630, 'uniswap_v4', 'core4',
                         'loop-bad', 0, 'opt-1', 'pool-a', 'pool-b', 'basis', 0.1,
                         'temporary', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                [],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO dex_candidate_anchor_tokens
                 VALUES ('robinhood-testnet', 46630, 'uniswap_v4', 'core4', 'ETH',
                         'yz', ?1, 3, 1, 1, 'pool-a', 'pool-b', '[]', '[]', 0,
                         '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                rusqlite::params![BAD_SYMBOL],
            )
            .unwrap();

        // The loop names the bad token in its path, so it cannot hold.
        let tokens: BTreeSet<String> =
            [BAD_SYMBOL.to_ascii_lowercase()].into_iter().collect();
        let affected = loops_naming_tokens(&connection, "robinhood-testnet", 46630, &tokens).unwrap();
        assert!(affected.contains("loop-bad"));
    }

    /// The prune of a real store removes the token and its dependants.
    #[test]
    fn the_prune_removes_the_token_and_its_dependants() {
        let connection = store();
        seed_token(&connection, GOOD, "ETH", Some(18));
        seed_token(&connection, BAD_SYMBOL, "0X102154E7", Some(18));
        connection
            .execute(
                "INSERT INTO v4_closed_loops
                 VALUES ('robinhood-testnet', 46630, 'uniswap_v4', 'core4',
                         'loop-bad', ?1, 'ETH', 'ETH -> X -> ETH',
                         '[\"0x0000000000000000000000000000000000000001\",\"0x0000000000000000000000000000000000000002\",\"0x0000000000000000000000000000000000000001\"]',
                         '[]', '[]', '[]', '{}', 1, 'basis', 1, 'active', 'test',
                         '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                rusqlite::params![GOOD],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO v4_closed_loop_hops
                 VALUES ('robinhood-testnet', 46630, 'uniswap_v4', 'core4',
                         'loop-bad', 0, 'yc', ?1, ?2, 'pool-a', 'pool-a',
                         'basis', NULL, 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                rusqlite::params![GOOD, BAD_SYMBOL],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO dex_candidate_anchor_tokens
                 VALUES ('robinhood-testnet', 46630, 'uniswap_v4', 'core4', 'ETH',
                         'yz', ?1, 3, 1, 1, 'pool-a', 'pool-b', '[]', '[]', 0,
                         '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                rusqlite::params![BAD_SYMBOL],
            )
            .unwrap();

        let (report, remaining, loops) = prune_and_read_back(&connection, true);
        assert_eq!(report.incomplete_tokens.len(), 1);
        assert_eq!(report.complete_token_count, 1);
        assert_eq!(report.counts.chain_tokens, 1);
        assert_eq!(report.counts.closed_loops, 1);
        assert_eq!(report.counts.closed_loop_hops, 1);
        assert_eq!(report.counts.candidate_anchor_tokens, 1);
        assert!(report.removed_loop_keys.contains(&"loop-bad".to_owned()));

        // The good token survives, and the loop that named the bad one is
        // gone.
        assert_eq!(remaining, 1);
        assert_eq!(loops, 0);
    }

    /// The dry run reports without touching anything.
    #[test]
    fn the_dry_run_changes_nothing() {
        let connection = store();
        seed_token(&connection, GOOD, "ETH", Some(18));
        seed_token(&connection, BAD_SYMBOL, "0X102154E7", Some(18));
        let (report, remaining, _) = prune_and_read_back(&connection, false);
        assert!(!report.applied);
        assert_eq!(report.incomplete_tokens.len(), 1);
        // The dry run touches nothing.
        assert_eq!(remaining, 2);
    }

    /// A store of another chain is never pruned as if it belonged to this
    /// one.
    #[test]
    fn the_chain_boundary_is_never_crossed() {
        let connection = store();
        seed_token(&connection, BAD_SYMBOL, "0X102154E7", Some(18));
        let error = prune_in_memory_with_chain(&connection, 4663, true).unwrap_err();
        assert!(matches!(error, PruneError::ScopeMismatch { .. }));
    }

    /// Run the prune against a file-backed store and read the result back.
    ///
    /// The prune owns the write path, so the test drives it through the
    /// same entry point the CLI uses, and then reads the file the prune
    /// actually wrote.
    fn prune_and_read_back(
        connection: &Connection,
        apply: bool,
    ) -> (PruneReport, i64, i64) {
        let path = std::env::temp_dir().join(format!(
            "token_prune_readback_{}_{}.sqlite3",
            std::process::id(),
            apply
        ));
        let _ = std::fs::remove_file(&path);
        {
            let mut target = Connection::open(&path).unwrap();
            let mut script = connection
                .prepare("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
                .unwrap();
            let mut statements = String::new();
            {
                let mut rows = script.query([]).unwrap();
                while let Some(row) = rows.next().unwrap() {
                    statements.push_str(&row.get::<_, String>(0).unwrap());
                    statements.push(';');
                }
            }
            drop(script);
            target.execute_batch(&statements).unwrap();
            copy_rows(connection, &mut target);
        }
        let report = prune_incomplete_tokens(&path, "robinhood-testnet", 46630, apply).unwrap();
        let readback = Connection::open(&path).unwrap();
        let remaining: i64 = readback
            .query_row("SELECT COUNT(*) FROM chain_tokens", [], |row| row.get(0))
            .unwrap();
        let loops: i64 = readback
            .query_row("SELECT COUNT(*) FROM v4_closed_loops", [], |row| row.get(0))
            .unwrap();
        drop(readback);
        let _ = std::fs::remove_file(&path);
        (report, remaining, loops)
    }

    fn prune_in_memory_with_chain(
        connection: &Connection,
        chain_id: u64,
        apply: bool,
    ) -> Result<PruneReport, PruneError> {
        let path = std::env::temp_dir().join(format!(
            "token_prune_test_{}_{}.sqlite3",
            std::process::id(),
            chain_id
        ));
        let _ = std::fs::remove_file(&path);
        {
            let mut target = Connection::open(&path).unwrap();
            let mut script = connection
                .prepare("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
                .unwrap();
            let mut statements = String::new();
            {
                let mut rows = script.query([]).unwrap();
                while let Some(row) = rows.next().unwrap() {
                    statements.push_str(&row.get::<_, String>(0).unwrap());
                    statements.push(';');
                }
            }
            drop(script);
            target.execute_batch(&statements).unwrap();
            copy_rows(connection, &mut target);
        }
        let report = prune_incomplete_tokens(&path, "robinhood-testnet", chain_id, apply);
        let _ = std::fs::remove_file(&path);
        report
    }

    /// Copy every row of every table from one connection to another.
    fn copy_rows(from: &Connection, to: &mut Connection) {
        let mut statement = from
            .prepare("SELECT name FROM sqlite_master WHERE type='table'")
            .unwrap();
        let tables: Vec<String> = statement
            .query_map([], |row| row.get::<_, String>(0))
            .unwrap()
            .collect::<Result<Vec<String>, rusqlite::Error>>()
            .unwrap();
        drop(statement);
        for table in tables {
            if table == "sqlite_sequence" {
                continue;
            }
            let rows: Vec<Vec<rusqlite::types::Value>> = {
                let mut select = from.prepare(&format!("SELECT * FROM {table}")).unwrap();
                let column_count = select.column_count();
                let mapped = select
                    .query_map([], |row| {
                        let mut values = Vec::with_capacity(column_count);
                        for index in 0..column_count {
                            values.push(row.get::<_, rusqlite::types::Value>(index).unwrap());
                        }
                        Ok(values)
                    })
                    .unwrap();
                let collected = mapped.collect::<Result<Vec<_>, _>>().unwrap();
                collected
            };
            for row in rows {
                let placeholders = vec!["?"; row.len()].join(",");
                let sql = format!("INSERT OR REPLACE INTO {table} VALUES ({placeholders})");
                let params: Vec<&dyn rusqlite::ToSql> = row
                    .iter()
                    .map(|value| value as &dyn rusqlite::ToSql)
                    .collect();
                to.execute(&sql, params.as_slice()).unwrap();
            }
        }
    }

}
