use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

const PROTOCOL: &str = "uniswap_v4";
const CANDIDATE_SCOPE: &str = "core4";
const OBSERVATION_SCOPE: &str = "candidate_exchange";

#[derive(Debug)]
pub enum BaselineAuditError {
    InvalidRequest(String),
    Sqlite(rusqlite::Error),
}

impl fmt::Display for BaselineAuditError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidRequest(message) => write!(formatter, "invalid baseline audit: {message}"),
            Self::Sqlite(error) => write!(formatter, "baseline audit sqlite error: {error}"),
        }
    }
}

impl std::error::Error for BaselineAuditError {}

impl From<rusqlite::Error> for BaselineAuditError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenLibraryBaseline {
    pub library_key: String,
    pub total: i64,
    pub admitted: i64,
    pub missing_symbol: i64,
    pub missing_name: i64,
    pub missing_decimals: i64,
    pub missing_address: i64,
    pub state_token_count: Option<i64>,
    pub state_admitted_count: Option<i64>,
    pub state_matches_rows: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExtensionBaseline {
    pub total: i64,
    pub admitted: i64,
    pub candidate_only: i64,
    pub anchored_to_core_or_stocks: i64,
    pub unanchored: i64,
    pub unanchored_tokens: Vec<ExtensionTokenBaseline>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExtensionTokenBaseline {
    pub token_key_id: String,
    pub v4_currency_key: String,
    pub token_address: String,
    pub token_symbol: String,
    pub admission_status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PoolBaseline {
    pub source_pool_count: i64,
    pub candidate_scope_pool_count: i64,
    pub candidate_scope_active_count: i64,
    pub candidate_scope_active_missing_state_count: i64,
    pub candidate_scope_active_invalid_fee_or_hook_count: i64,
    pub candidate_scope_out_of_policy_pool_count: i64,
    pub core_scope_configured_libraries: Vec<String>,
    pub core_scope_policy_matches: bool,
    pub candidate_scope_configured_libraries: Vec<String>,
    pub candidate_scope_policy_matches: bool,
    pub recommendation_count: i64,
    pub top1_recommendation_count: i64,
    pub recommendation_invalid_pool_reference_count: i64,
    pub candidate_pair_count: i64,
    pub candidate_pair_missing_top1_direction_count: i64,
    pub candidate_pool_count: i64,
    pub candidate_pool_invalid_reference_count: i64,
    pub observation_direction_count: i64,
    pub observation_direct_direction_count: i64,
    pub observation_empty_direction_count: i64,
    pub observation_pool_count: i64,
    pub observation_invalid_pool_reference_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosedLoopBaseline {
    pub loop_count: i64,
    pub hop_count: i64,
    pub complete_three_hop_loop_count: i64,
    pub incomplete_loop_count: i64,
    pub invalid_hop_pool_reference_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationBaseline {
    pub ledger: Option<String>,
    pub applied_count: i64,
    pub latest_expected_migration: String,
    pub latest_expected_applied: bool,
    pub unknown_migration_count: i64,
    /// Every known migration that the ledger does not carry, in apply
    /// order. The baseline used to report only "the latest expected
    /// migration is missing", which hid how many migrations a production
    /// apply would actually have to run. An operator could not scope the
    /// change window from the report.
    pub missing_migrations: Vec<String>,
    /// The known migrations the ledger carries, in apply order.
    pub applied_migrations: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MainnetBaselineAudit {
    pub network: String,
    pub chain_id: i64,
    pub valid: bool,
    pub token_libraries: Vec<TokenLibraryBaseline>,
    pub extension: ExtensionBaseline,
    pub pools: PoolBaseline,
    pub closed_loops: ClosedLoopBaseline,
    pub migrations: MigrationBaseline,
    pub issues: Vec<String>,
}

pub fn audit_mainnet_baseline(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
) -> Result<MainnetBaselineAudit, BaselineAuditError> {
    if network.trim().is_empty() || chain_id <= 0 {
        return Err(BaselineAuditError::InvalidRequest(
            "network and positive chain_id are required".to_owned(),
        ));
    }
    let connection = Connection::open_with_flags(
        database_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;

    let mut issues = Vec::new();
    let mut token_libraries = Vec::new();
    for (library_key, table) in [
        ("core", "token_core"),
        ("stocks", "token_stocks"),
        ("extension", "token_extension"),
        ("test", "token_test"),
    ] {
        let baseline = read_token_library(&connection, network, chain_id, library_key, table)?;
        if matches!(library_key, "core" | "stocks") && baseline.admitted == 0 {
            issues.push(format!("{library_key}_library_has_no_admitted_tokens"));
        }
        if baseline.missing_symbol + baseline.missing_decimals + baseline.missing_address > 0 {
            issues.push(format!("{library_key}_library_metadata_incomplete"));
        }
        if !baseline.state_matches_rows {
            issues.push(format!("{library_key}_library_state_mismatch"));
        }
        token_libraries.push(baseline);
    }

    let admitted_by_currency = read_admitted_currency_libraries(&connection, network, chain_id)?;
    let raw_pairs = read_raw_pool_pairs(&connection, network, chain_id)?;
    let extension_tokens = read_extension_tokens(&connection, network, chain_id)?;
    let anchored_tokens =
        anchored_extension_tokens(&extension_tokens, &raw_pairs, &admitted_by_currency);
    let anchored = anchored_tokens.len() as i64;
    let unanchored_tokens = extension_tokens
        .into_iter()
        .filter(|token| !anchored_tokens.contains(token))
        .collect::<Vec<_>>();
    let extension_counts = token_libraries
        .iter()
        .find(|library| library.library_key == "extension")
        .cloned()
        .unwrap_or(TokenLibraryBaseline {
            library_key: "extension".to_owned(),
            total: 0,
            admitted: 0,
            missing_symbol: 0,
            missing_name: 0,
            missing_decimals: 0,
            missing_address: 0,
            state_token_count: None,
            state_admitted_count: None,
            state_matches_rows: false,
        });
    let candidate_only = connection.query_row(
        "SELECT COUNT(*) FROM token_extension
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'candidate_only'",
        params![network, chain_id],
        |row| row.get(0),
    )?;
    let extension = ExtensionBaseline {
        total: extension_counts.total,
        admitted: extension_counts.admitted,
        candidate_only,
        anchored_to_core_or_stocks: anchored,
        unanchored: unanchored_tokens.len() as i64,
        unanchored_tokens,
    };
    if extension.unanchored > 0 {
        issues.push("extension_library_contains_unanchored_tokens".to_owned());
    }

    let pools = read_pool_baseline(&connection, network, chain_id, &admitted_by_currency)?;
    if !pools.core_scope_policy_matches {
        issues.push("core_scope_library_policy_stale".to_owned());
    }
    if !pools.core_scope_policy_matches {
        issues.push("core_scope_library_policy_stale".to_owned());
    }
    if !pools.core_scope_policy_matches {
        issues.push("core_scope_library_policy_stale".to_owned());
    }
    if !pools.core_scope_policy_matches {
        issues.push("core_scope_library_policy_stale".to_owned());
    }
    if !pools.core_scope_policy_matches {
        issues.push("core_scope_library_policy_stale".to_owned());
    }
    if !pools.core_scope_policy_matches {
        issues.push("core_scope_library_policy_stale".to_owned());
    }
    if !pools.candidate_scope_policy_matches {
        issues.push("candidate_scope_library_policy_stale".to_owned());
    }
    if pools.candidate_scope_out_of_policy_pool_count > 0 {
        issues.push("candidate_scope_contains_extension_or_test_tokens".to_owned());
    }
    if pools.candidate_pair_missing_top1_direction_count > 0 {
        issues.push("candidate_pairs_missing_top1_recommendation".to_owned());
    }
    if pools.candidate_scope_active_missing_state_count > 0 {
        issues.push("candidate_scope_active_pool_state_incomplete".to_owned());
    }
    if pools.candidate_scope_active_invalid_fee_or_hook_count > 0 {
        issues.push("candidate_scope_active_pool_policy_invalid".to_owned());
    }
    if pools.recommendation_invalid_pool_reference_count > 0 {
        issues.push("recommendation_pool_reference_invalid".to_owned());
    }
    if pools.candidate_pool_invalid_reference_count > 0 {
        issues.push("candidate_pool_reference_invalid".to_owned());
    }
    if pools.observation_invalid_pool_reference_count > 0 {
        issues.push("observation_pool_reference_invalid".to_owned());
    }

    let closed_loops = read_closed_loop_baseline(&connection, network, chain_id)?;
    if closed_loops.incomplete_loop_count > 0 {
        issues.push("closed_loop_not_exactly_three_hops".to_owned());
    }
    if closed_loops.invalid_hop_pool_reference_count > 0 {
        issues.push("closed_loop_hop_pool_reference_invalid".to_owned());
    }

    let migrations = read_migration_baseline(&connection)?;
    if !migrations.latest_expected_applied {
        issues.push("latest_manager_migration_not_applied".to_owned());
    }
    if migrations.unknown_migration_count > 0 {
        issues.push("migration_ledger_contains_unknown_entries".to_owned());
    }

    Ok(MainnetBaselineAudit {
        network: network.to_owned(),
        chain_id,
        valid: issues.is_empty(),
        token_libraries,
        extension,
        pools,
        closed_loops,
        migrations,
        issues,
    })
}






fn read_token_library(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    library_key: &str,
    table: &str,
) -> Result<TokenLibraryBaseline, BaselineAuditError> {
    let sql = format!(
        "SELECT COUNT(*),
                COALESCE(SUM(CASE WHEN admission_status = 'admitted' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN TRIM(COALESCE(token_symbol, '')) = '' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN TRIM(COALESCE(token_name, '')) = '' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN token_decimals IS NULL THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN TRIM(COALESCE(token_address, '')) = '' THEN 1 ELSE 0 END), 0)
         FROM {table} WHERE network = ?1 AND chain_id = ?2"
    );
    let counts = connection.query_row(&sql, params![network, chain_id], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, i64>(2)?,
            row.get::<_, i64>(3)?,
            row.get::<_, i64>(4)?,
            row.get::<_, i64>(5)?,
        ))
    })?;
    let state = connection
        .query_row(
            "SELECT token_count, admitted_count FROM token_library_state
             WHERE network = ?1 AND chain_id = ?2 AND library_key = ?3",
            params![network, chain_id, library_key],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)),
        )
        .optional()?;
    Ok(TokenLibraryBaseline {
        library_key: library_key.to_owned(),
        total: counts.0,
        admitted: counts.1,
        missing_symbol: counts.2,
        missing_name: counts.3,
        missing_decimals: counts.4,
        missing_address: counts.5,
        state_token_count: state.map(|value| value.0),
        state_admitted_count: state.map(|value| value.1),
        state_matches_rows: state.is_some_and(|value| value == (counts.0, counts.1)),
    })
}

fn read_admitted_currency_libraries(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<BTreeMap<String, String>, BaselineAuditError> {
    let mut statement = connection.prepare(
        "SELECT lower(v4_currency_key), library_key FROM token_library_catalog
         WHERE network = ?1 AND chain_id = ?2 AND admission_status = 'admitted'
         ORDER BY CASE library_key WHEN 'core' THEN 0 WHEN 'stocks' THEN 1
                  WHEN 'extension' THEN 2 ELSE 3 END",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut values = BTreeMap::new();
    for row in rows {
        let (currency, library) = row?;
        values.entry(currency).or_insert(library);
    }
    Ok(values)
}

fn read_extension_tokens(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<ExtensionTokenBaseline>, BaselineAuditError> {
    let mut statement = connection.prepare(
        "SELECT token_key_id, lower(v4_currency_key), lower(token_address),
                token_symbol, admission_status
         FROM token_extension
         WHERE network = ?1 AND chain_id = ?2
         ORDER BY token_key_id",
    )?;
    let rows = statement.query_map(params![network, chain_id], |row| {
        Ok(ExtensionTokenBaseline {
            token_key_id: row.get(0)?,
            v4_currency_key: row.get(1)?,
            token_address: row.get(2)?,
            token_symbol: row.get(3)?,
            admission_status: row.get(4)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn read_raw_pool_pairs(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<Vec<(String, String)>, BaselineAuditError> {
    let mut statement = connection.prepare(
        "SELECT lower(token0), lower(token1) FROM v4_exchange_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
    )?;
    let rows = statement.query_map(params![network, chain_id, PROTOCOL], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn anchored_extension_tokens(
    extension_tokens: &[ExtensionTokenBaseline],
    raw_pairs: &[(String, String)],
    admitted_by_currency: &BTreeMap<String, String>,
) -> BTreeSet<ExtensionTokenBaseline> {
    extension_tokens
        .iter()
        .filter(|token| {
            let currency = &token.v4_currency_key;
            raw_pairs.iter().any(|(token0, token1)| {
                let other = if token0 == currency {
                    Some(token1)
                } else if token1 == currency {
                    Some(token0)
                } else {
                    None
                };
                other.is_some_and(|other| {
                    admitted_by_currency
                        .get(other)
                        .is_some_and(|library| matches!(library.as_str(), "core" | "stocks"))
                })
            })
        })
        .cloned()
        .collect()
}

fn read_pool_baseline(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    admitted_by_currency: &BTreeMap<String, String>,
) -> Result<PoolBaseline, BaselineAuditError> {
    let source_pool_count = count_where(connection, "v4_exchange_pools", network, chain_id, None)?;
    let candidate_scope_pool_count = count_where(
        connection,
        "v4_exchange_pools_candidate_exchange",
        network,
        chain_id,
        None,
    )?;
    let candidate_scope_active_count = count_where(
        connection,
        "v4_exchange_pools_candidate_exchange",
        network,
        chain_id,
        Some("pool_status = 'active'"),
    )?;
    let candidate_scope_active_missing_state_count = connection.query_row(
        "SELECT COUNT(*) FROM v4_exchange_pools_candidate_exchange
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pool_status = 'active'
           AND (sqrt_price_x96 IS NULL OR tick IS NULL OR liquidity IS NULL)",
        params![network, chain_id, PROTOCOL],
        |row| row.get(0),
    )?;
    let candidate_scope_active_invalid_fee_or_hook_count = connection.query_row(
        "SELECT COUNT(*) FROM v4_exchange_pools_candidate_exchange
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
           AND pool_status = 'active'
           AND (fee <= 0 OR fee > 30000 OR dynamic_fee != 0 OR special_fee != 0
                OR lower(hooks) != '0x0000000000000000000000000000000000000000')",
        params![network, chain_id, PROTOCOL],
        |row| row.get(0),
    )?;
    let core_scope_configured_libraries =
        read_scope_libraries(connection, network, chain_id, CANDIDATE_SCOPE)?;
    let core_scope_policy_matches =
        core_scope_configured_libraries == ["core".to_owned(), "stocks".to_owned()];
    let candidate_scope_configured_libraries =
        read_scope_libraries(connection, network, chain_id, OBSERVATION_SCOPE)?;
    let candidate_scope_policy_matches =
        candidate_scope_configured_libraries == ["core".to_owned(), "stocks".to_owned()];
    let mut statement = connection.prepare(
        "SELECT lower(token0), lower(token1) FROM v4_exchange_pools_candidate_exchange
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
    )?;
    let scope_pairs = statement.query_map(params![network, chain_id, PROTOCOL], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut candidate_scope_out_of_policy_pool_count = 0_i64;
    for pair in scope_pairs {
        let (token0, token1) = pair?;
        let allowed = |token: &str| {
            admitted_by_currency
                .get(token)
                .is_some_and(|library| matches!(library.as_str(), "core" | "stocks"))
        };
        if !allowed(&token0) || !allowed(&token1) {
            candidate_scope_out_of_policy_pool_count += 1;
        }
    }
    let recommendation_count = count_where(
        connection,
        "manager_dex_candidate_pool_recommendations",
        network,
        chain_id,
        Some("scope_key = 'core4'"),
    )?;
    let top1_recommendation_count = count_where(
        connection,
        "manager_dex_candidate_pool_recommendations",
        network,
        chain_id,
        Some("scope_key = 'core4' AND rank = 1"),
    )?;
    let recommendation_invalid_pool_reference_count = connection.query_row(
        "SELECT COUNT(*) FROM manager_dex_candidate_pool_recommendations r
         WHERE r.network = ?1 AND r.chain_id = ?2 AND r.protocol = ?3 AND r.scope_key = ?4
           AND NOT EXISTS (
               SELECT 1 FROM v4_exchange_pools_core4 p
               WHERE p.network = r.network AND p.chain_id = r.chain_id
                 AND p.protocol = r.protocol AND p.pool_id = r.pool_id
                 AND p.pool_status = 'active'
           )",
        params![network, chain_id, PROTOCOL, CANDIDATE_SCOPE],
        |row| row.get(0),
    )?;
    let candidate_pair_count = count_where(
        connection,
        "manager_dex_candidate_pool_pairs",
        network,
        chain_id,
        Some("scope_key = 'core4'"),
    )?;
    let candidate_pair_missing_top1_direction_count = candidate_pair_count
        .saturating_mul(2)
        .saturating_sub(top1_recommendation_count);
    let candidate_pool_count = connection.query_row(
        "SELECT COUNT(DISTINCT pool_id) FROM manager_dex_candidate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        params![network, chain_id, PROTOCOL, CANDIDATE_SCOPE],
        |row| row.get(0),
    )?;
    let candidate_pool_invalid_reference_count = connection.query_row(
        "SELECT COUNT(DISTINCT c.pool_id) FROM manager_dex_candidate_pools c
         WHERE c.network = ?1 AND c.chain_id = ?2 AND c.protocol = ?3 AND c.scope_key = ?4
           AND NOT EXISTS (
               SELECT 1 FROM v4_exchange_pools_core4 p
               WHERE p.network = c.network AND p.chain_id = c.chain_id
                 AND p.protocol = c.protocol AND p.pool_id = c.pool_id
                 AND p.pool_status = 'active'
           )",
        params![network, chain_id, PROTOCOL, CANDIDATE_SCOPE],
        |row| row.get(0),
    )?;
    let observation = connection.query_row(
        "SELECT COUNT(*),
                COALESCE(SUM(CASE WHEN has_direct_swap_pool = 1 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN has_direct_swap_pool = 0 THEN 1 ELSE 0 END), 0)
         FROM manager_market_observation_yz_pairs
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        params![network, chain_id, PROTOCOL, OBSERVATION_SCOPE],
        |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, i64>(2)?,
            ))
        },
    )?;
    let observation_pool_count = connection.query_row(
        "SELECT COUNT(DISTINCT pool_id) FROM manager_market_observation_intermediate_pools
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        params![network, chain_id, PROTOCOL, OBSERVATION_SCOPE],
        |row| row.get(0),
    )?;
    let observation_invalid_pool_reference_count = connection.query_row(
        "SELECT COUNT(*) FROM manager_market_observation_intermediate_pools o
         WHERE o.network = ?1 AND o.chain_id = ?2 AND o.protocol = ?3 AND o.scope_key = ?4
           AND NOT EXISTS (
               SELECT 1 FROM v4_exchange_pools_candidate_exchange p
               WHERE p.network = o.network AND p.chain_id = o.chain_id
                 AND p.protocol = o.protocol AND p.pool_id = o.pool_id
                 AND p.pool_status = 'active'
           )",
        params![network, chain_id, PROTOCOL, OBSERVATION_SCOPE],
        |row| row.get(0),
    )?;
    Ok(PoolBaseline {
        source_pool_count,
        candidate_scope_pool_count,
        candidate_scope_active_count,
        candidate_scope_active_missing_state_count,
        candidate_scope_active_invalid_fee_or_hook_count,
        candidate_scope_out_of_policy_pool_count,
        core_scope_configured_libraries,
        core_scope_policy_matches,
        candidate_scope_configured_libraries,
        candidate_scope_policy_matches,
        recommendation_count,
        top1_recommendation_count,
        recommendation_invalid_pool_reference_count,
        candidate_pair_count,
        candidate_pair_missing_top1_direction_count,
        candidate_pool_count,
        candidate_pool_invalid_reference_count,
        observation_direction_count: observation.0,
        observation_direct_direction_count: observation.1,
        observation_empty_direction_count: observation.2,
        observation_pool_count,
        observation_invalid_pool_reference_count,
    })
}

fn read_scope_libraries(
    connection: &Connection,
    network: &str,
    chain_id: i64,
    scope_key: &str,
) -> Result<Vec<String>, BaselineAuditError> {
    let configured_libraries_json = connection
        .query_row(
            "SELECT library_keys_json FROM pool_scope_config
             WHERE network = ?1 AND chain_id = ?2 AND scope_key = ?3",
            params![network, chain_id, scope_key],
            |row| row.get::<_, String>(0),
        )
        .optional()?
        .unwrap_or_else(|| "[]".to_owned());
    Ok(serde_json::from_str::<Vec<String>>(&configured_libraries_json).unwrap_or_default())
}

fn read_closed_loop_baseline(
    connection: &Connection,
    network: &str,
    chain_id: i64,
) -> Result<ClosedLoopBaseline, BaselineAuditError> {
    let loop_count = count_where(
        connection,
        "manager_v4_closed_loops",
        network,
        chain_id,
        Some("scope_key = 'core4'"),
    )?;
    let hop_count = count_where(
        connection,
        "manager_v4_closed_loop_hops",
        network,
        chain_id,
        Some("scope_key = 'core4'"),
    )?;
    let complete_three_hop_loop_count = connection.query_row(
        "SELECT COUNT(*) FROM (
             SELECT loop_key FROM manager_v4_closed_loop_hops
             WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4
             GROUP BY loop_key
             HAVING COUNT(*) = 3
                AND COUNT(DISTINCT hop_index) = 3
                AND COUNT(DISTINCT hop_name) = 3
         )",
        params![network, chain_id, PROTOCOL, CANDIDATE_SCOPE],
        |row| row.get(0),
    )?;
    let invalid_hop_pool_reference_count = connection.query_row(
        "SELECT COUNT(*) FROM manager_v4_closed_loop_hops h
         WHERE h.network = ?1 AND h.chain_id = ?2 AND h.protocol = ?3 AND h.scope_key = ?4
           AND NOT EXISTS (
               SELECT 1 FROM v4_exchange_pools_candidate_exchange p
               WHERE p.network = h.network AND p.chain_id = h.chain_id
                 AND p.protocol = h.protocol AND p.pool_id = h.pool_id
                 AND p.pool_status = 'active'
           )",
        params![network, chain_id, PROTOCOL, CANDIDATE_SCOPE],
        |row| row.get(0),
    )?;
    Ok(ClosedLoopBaseline {
        loop_count,
        hop_count,
        complete_three_hop_loop_count,
        incomplete_loop_count: loop_count.saturating_sub(complete_three_hop_loop_count),
        invalid_hop_pool_reference_count,
    })
}

fn read_migration_baseline(
    connection: &Connection,
) -> Result<MigrationBaseline, BaselineAuditError> {
    let ledger = if table_exists(connection, "schema_migrations")? {
        Some("schema_migrations".to_owned())
    } else if table_exists(connection, "manager_schema_migrations")? {
        Some("manager_schema_migrations".to_owned())
    } else {
        None
    };
    let latest_expected_migration = manager_db::LATEST_MIGRATION_ID.to_owned();
    let Some(ledger_name) = ledger.as_deref() else {
        return Ok(MigrationBaseline {
            ledger,
            applied_count: 0,
            latest_expected_migration,
            latest_expected_applied: false,
            unknown_migration_count: 0,
            missing_migrations: manager_db::MIGRATION_IDS
                .iter()
                .map(|id| (*id).to_owned())
                .collect(),
            applied_migrations: Vec::new(),
        });
    };
    let applied_count =
        connection.query_row(&format!("SELECT COUNT(*) FROM {ledger_name}"), [], |row| {
            row.get(0)
        })?;
    let latest_expected_applied = connection
        .query_row(
            &format!("SELECT 1 FROM {ledger_name} WHERE migration_id = ?1 LIMIT 1"),
            [&latest_expected_migration],
            |_| Ok(true),
        )
        .optional()?
        .unwrap_or(false);
    let known = manager_db::migration_checksums()
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut statement = connection.prepare(&format!(
        "SELECT migration_id FROM {ledger_name} ORDER BY migration_id"
    ))?;
    let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
    let mut unknown_migration_count = 0_i64;
    let mut applied_ids = BTreeSet::new();
    for row in rows {
        let migration_id = row?;
        if !known.contains(&migration_id) {
            unknown_migration_count += 1;
            continue;
        }
        applied_ids.insert(migration_id);
    }
    // The missing list is the exact set of migrations a production apply
    // would have to run. Reporting only "the latest expected migration is
    // missing" hid the size of that change from the operator.
    let missing_migrations = manager_db::MIGRATION_IDS
        .iter()
        .filter(|id| !applied_ids.contains(**id))
        .map(|id| (*id).to_owned())
        .collect::<Vec<_>>();
    let applied_migrations = manager_db::MIGRATION_IDS
        .iter()
        .filter(|id| applied_ids.contains(**id))
        .map(|id| (*id).to_owned())
        .collect::<Vec<_>>();
    Ok(MigrationBaseline {
        ledger,
        applied_count,
        latest_expected_migration,
        latest_expected_applied,
        unknown_migration_count,
        missing_migrations,
        applied_migrations,
    })
}

fn count_where(
    connection: &Connection,
    table: &str,
    network: &str,
    chain_id: i64,
    condition: Option<&str>,
) -> Result<i64, BaselineAuditError> {
    let suffix = condition
        .map(|condition| format!(" AND {condition}"))
        .unwrap_or_default();
    let sql = format!(
        "SELECT COUNT(*) FROM {table}
         WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3{suffix}"
    );
    Ok(connection.query_row(&sql, params![network, chain_id, PROTOCOL], |row| row.get(0))?)
}

fn table_exists(connection: &Connection, table: &str) -> Result<bool, rusqlite::Error> {
    connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1 LIMIT 1",
            [table],
            |_| Ok(true),
        )
        .optional()
        .map(|value| value.unwrap_or(false))
}

#[cfg(test)]
mod tests {
    use super::{
        anchored_extension_tokens, audit_mainnet_baseline, BaselineAuditError,
        ExtensionTokenBaseline,
    };
    use std::collections::BTreeMap;

    fn extension(token_key_id: &str, currency: &str, symbol: &str) -> ExtensionTokenBaseline {
        ExtensionTokenBaseline {
            token_key_id: token_key_id.to_owned(),
            v4_currency_key: currency.to_owned(),
            token_address: currency.to_owned(),
            token_symbol: symbol.to_owned(),
            admission_status: "candidate_only".to_owned(),
        }
    }

    #[test]
    fn extension_anchor_requires_direct_core_or_stock_pair() {
        let core = "0x0000000000000000000000000000000000000001".to_owned();
        let extension_a = "0x0000000000000000000000000000000000000002".to_owned();
        let extension_b = "0x0000000000000000000000000000000000000003".to_owned();
        let tokens = vec![
            extension("robinhood:4663:extension-a", &extension_a, "EXTA"),
            extension("robinhood:4663:extension-b", &extension_b, "EXTB"),
        ];
        let pairs = vec![(extension_a, core.clone()), (extension_b, "0x4".to_owned())];
        let admitted = BTreeMap::from([(core, "core".to_owned())]);

        let anchored = anchored_extension_tokens(&tokens, &pairs, &admitted);

        assert_eq!(anchored.len(), 1);
        assert!(anchored.contains(&tokens[0]));
        assert!(!anchored.contains(&tokens[1]));
    }

    #[test]
    fn the_migration_baseline_serialises_the_camel_case_keys_the_healer_reads() {
        // The healer reads `migrations.missingMigrations` and
        // `migrations.appliedMigrations` by name.  A rename on the Rust side
        // would silently empty the operator report, so the wire format is
        // pinned here.
        let baseline = super::MigrationBaseline {
            ledger: Some("schema_migrations".to_owned()),
            applied_count: 2,
            latest_expected_migration: "20261114_009_hop_price_limit_schema".to_owned(),
            latest_expected_applied: false,
            unknown_migration_count: 0,
            missing_migrations: vec![
                "20261114_009_hop_price_limit_schema".to_owned(),
            ],
            applied_migrations: vec![
                "20261103_001_manager_schema".to_owned(),
                "20261104_001_candidate_edge_schema".to_owned(),
            ],
        };

        let rendered = serde_json::to_value(&baseline).unwrap();
        let object = rendered.as_object().unwrap();

        assert!(object.contains_key("missingMigrations"));
        assert!(object.contains_key("appliedMigrations"));
        assert!(object.contains_key("latestExpectedMigration"));
        assert!(object.contains_key("latestExpectedApplied"));
        assert!(object.contains_key("unknownMigrationCount"));
        assert!(object.contains_key("appliedCount"));

        assert_eq!(
            object["missingMigrations"],
            serde_json::json!(["20261114_009_hop_price_limit_schema"])
        );
        assert_eq!(
            object["appliedMigrations"],
            serde_json::json!([
                "20261103_001_manager_schema",
                "20261104_001_candidate_edge_schema"
            ])
        );
    }

    #[test]
    fn baseline_audit_rejects_invalid_target_before_opening_database() {
        let error = audit_mainnet_baseline("missing.sqlite3", "", 0).unwrap_err();
        assert!(matches!(error, BaselineAuditError::InvalidRequest(_)));
    }

    #[test]
    fn the_missing_migrations_are_the_exact_set_an_apply_would_run() {
        use rusqlite::Connection;

        let connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE TABLE schema_migrations (
                     migration_id TEXT PRIMARY KEY,
                     checksum TEXT NOT NULL,
                     applied_at TEXT NOT NULL,
                     result_json TEXT NOT NULL
                 );
                 INSERT INTO schema_migrations VALUES
                     ('20261103_001_manager_schema', 'x', '2026-11-07', '{}'),
                     ('20261104_001_candidate_edge_schema', 'x', '2026-11-07', '{}');",
            )
            .unwrap();

        let baseline = super::read_migration_baseline(&connection).unwrap();

        // The baseline used to report only that the latest expected
        // migration was missing. An operator could not scope the change
        // window from that.
        assert!(!baseline.latest_expected_applied);
        assert!(!baseline.missing_migrations.is_empty());
        assert!(baseline
            .missing_migrations
            .contains(&manager_db::LATEST_MIGRATION_ID.to_owned()));
        assert!(!baseline
            .missing_migrations
            .contains(&"20261103_001_manager_schema".to_owned()));
        assert_eq!(baseline.applied_migrations.len(), 2);
        assert!(baseline
            .applied_migrations
            .contains(&"20261104_001_candidate_edge_schema".to_owned()));
        assert_eq!(baseline.unknown_migration_count, 0);
    }

    #[test]
    fn an_unknown_ledger_entry_is_counted_and_not_treated_as_applied() {
        use rusqlite::Connection;

        let connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE TABLE schema_migrations (
                     migration_id TEXT PRIMARY KEY,
                     checksum TEXT NOT NULL,
                     applied_at TEXT NOT NULL,
                     result_json TEXT NOT NULL
                 );
                 INSERT INTO schema_migrations VALUES
                     ('20261103_001_manager_schema', 'x', '2026-11-07', '{}'),
                     ('20990101_999_made_up_migration', 'x', '2026-11-07', '{}');",
            )
            .unwrap();

        let baseline = super::read_migration_baseline(&connection).unwrap();

        assert_eq!(baseline.unknown_migration_count, 1);
        // Every known migration is still reported as missing, because the
        // ledger does not carry it.
        assert_eq!(
            baseline.missing_migrations.len(),
            manager_db::MIGRATION_IDS.len() - 1
        );
    }
}
