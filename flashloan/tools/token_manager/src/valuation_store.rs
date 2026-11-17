//! The durable store for the USDG valuation of every measured token.
//!
//! The valuation probe produces a report file.  A report file is a
//! transient artifact: it is copied around, it is overwritten by the next
//! run, and nothing in the database knows about it.  The economic gate
//! needs the opposite — a durable, queryable statement of "this token was
//! measured, and this is what it is worth".
//!
//! This module owns that statement.  It writes the `token_usdg_valuations`
//! table and reads it back.
//!
//! What the table preserves
//! ------------------------
//!
//! * **Every verdict, not only the successful ones.**  A `no_route` verdict
//!   is a genuine negative and is stored as a row, because "this token
//!   cannot be exchanged into USDG" is exactly the fact the stock-token
//!   admission rule needs.  An `api_unavailable` or `api_error` verdict is
//!   an unknown and is stored too, so that a failed refresh never looks
//!   like a successful one.
//! * **The distinction between negative and unknown.**  The verdict column
//!   is a closed vocabulary, and a reader can always tell a real negative
//!   from a transport failure.
//! * **The timestamp of the observation.**  A valuation is an observation,
//!   not a permanent truth, and a reader must be able to tell how old it
//!   is.
//!
//! What the module never does
//! --------------------------
//!
//! * It never invents a valuation.  A token without an observation has no
//!   row, and a row without a `route_found` verdict has no value.
//! * It never turns a zero into a value.  A `no_route` token stores a
//!   `NULL` `usdg_amount_raw`, not a zero, because "worth zero" and
//!   "cannot be valued" are different statements.

use std::collections::BTreeMap;
use std::error::Error;

use rusqlite::{Connection, OpenFlags};
use serde_json::Value;

use crate::valuation::{TokenValuation, ValuationBatchReport, ValuationVerdict};
use crate::valuation_freshness::ValuationExpiryReport;

/// The outcome of landing a valuation report into the database.
#[derive(Clone, Debug)]
pub struct ValuationLandingResult {
    /// How many rows the landing wrote.
    pub written: usize,
    /// How many rows carried a `route_found` verdict.
    pub valued: usize,
    /// How many rows carried a `no_route` verdict.
    pub unvaluable: usize,
    /// How many rows carried an unknown verdict.
    pub unknown: usize,
    /// How many unknown observations were refused because a known
    /// observation was already stored for the same token.
    pub protected: usize,
    /// The address of every token that was written.
    pub token_addresses: Vec<String>,
}

/// Reads the stored valuations of one network from the database.
///
/// The database is opened **read-only**: the valuation store is a record of
/// what was observed, and the economic gate never rewrites history.
pub fn read_stored_valuations(
    database: &std::path::Path,
    network: &str,
    chain_id: u64,
) -> Result<Vec<TokenValuation>, Box<dyn Error>> {
    if !database.exists() {
        return Ok(Vec::new());
    }
    let connection = Connection::open_with_flags(
        database,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )?;
    let table_present: i64 = connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master
          WHERE type = 'table' AND name = 'token_usdg_valuations'",
        [],
        |row| row.get(0),
    )?;
    if table_present == 0 {
        return Ok(Vec::new());
    }
    let mut statement = connection.prepare(
        "SELECT token_address, token_symbol, verdict, has_value, input_amount_raw,
                usdg_amount_raw, usdg_amount_decimal, price_impact, gas_estimate,
                route_leg_count, protocols_json, failure_kind, failure_detail,
                source, measured_at
           FROM token_usdg_valuations
          WHERE network = ?1 AND chain_id = ?2
          ORDER BY token_address",
    )?;
    let rows = statement.query_map(rusqlite::params![network, chain_id as i64], |row| {
        let verdict_text: String = row.get(2)?;
        let verdict = parse_verdict(&verdict_text).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(
                2,
                rusqlite::types::Type::Text,
                Box::new(std::io::Error::new(std::io::ErrorKind::InvalidData, error)),
            )
        })?;
        let protocols_json: String = row.get(10)?;
        let protocols: Vec<String> = serde_json::from_str(&protocols_json).unwrap_or_default();
        Ok(TokenValuation {
            token_address: row.get(0)?,
            token_symbol: row.get(1)?,
            verdict,
            has_value: row.get::<_, i64>(3)? != 0,
            input_amount_raw: row.get(4)?,
            usdg_amount_raw: row.get(5)?,
            usdg_amount_decimal: row.get(6)?,
            price_impact: row.get(7)?,
            gas_estimate: row.get(8)?,
            route_leg_count: row.get(9)?,
            protocols,
            failure_kind: None,
            failure_detail: row.get(12)?,
            source: row.get(13)?,
            observed_at: row.get(14)?,
        })
    })?;
    let mut valuations = Vec::new();
    for row in rows {
        valuations.push(row?);
    }
    Ok(valuations)
}

/// Audits the expiry state of the stored valuations of one network.
///
/// The database is opened **read-only**: this function only reports which
/// stored observations may still be used as facts.  It never rewrites a
/// row, and it never deletes anything.
///
/// The report is the machine-readable answer to the two questions the
/// P0-5 decision has to answer for every deployment:
///
/// * how much of the stored valuation population is still fresh enough
///   to decide a membership question, and
/// * how much of it is still fresh enough to decide a magnitude
///   question.
///
/// A missing table is not an error: it means the valuation store has
/// never been created on this database, so there is nothing to expire.
pub fn audit_valuation_expiry(
    database: &std::path::Path,
    network: &str,
    chain_id: u64,
) -> Result<ValuationExpiryReport, Box<dyn Error>> {
    if !database.exists() {
        return Ok(crate::valuation_freshness::evaluate_stored_valuations(
            network,
            chain_id as i64,
            &[],
            0,
        ));
    }
    let connection = Connection::open_with_flags(
        database,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )?;
    let table_present: i64 = connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master
          WHERE type = 'table' AND name = 'token_usdg_valuations'",
        [],
        |row| row.get(0),
    )?;
    if table_present == 0 {
        return Ok(crate::valuation_freshness::evaluate_stored_valuations(
            network,
            chain_id as i64,
            &[],
            0,
        ));
    }
    let mut statement = connection.prepare(
        "SELECT token_address, verdict, measured_at
           FROM token_usdg_valuations
          WHERE network = ?1 AND chain_id = ?2
          ORDER BY token_address",
    )?;
    let rows = statement.query_map(rusqlite::params![network, chain_id as i64], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    let mut collected = Vec::new();
    for row in rows {
        collected.push(row?);
    }
    Ok(crate::valuation_freshness::evaluate_stored_valuations(
        network,
        chain_id as i64,
        &collected,
        crate::valuation_freshness::current_epoch_seconds(),
    ))
}

/// Lands a valuation report into the `token_usdg_valuations` table.
///
/// The write is **idempotent**: the table is keyed by
/// `(network, chain_id, token_address)`, so landing the same report twice
/// leaves the same rows.  A later report replaces the earlier observation
/// of the same token, because the newer observation is the one the caller
/// just made and the older one is stale.
///
/// **An unknown never overwrites a known.**  A verdict of `api_unavailable`
/// or `api_error` says that the probe could not reach an answer.  It does
/// not say that the token has no route, and it does not say that the token
/// has a route.  Writing it over a good observation would therefore
/// *destroy* information: a transient outage would turn every valued token
/// into an unknown, and the economic gate would stop knowing which tokens
/// are admissible.  Such a row is kept only when there is nothing better
/// to keep, and the landing result reports how many rows were protected.
///
/// The caller decides whether the write happens at all: this function is
/// only reached with an explicit `--apply`, and the dry-run variant
/// reports what *would* be written without touching the database.
pub fn land_valuation_report(
    database: &std::path::Path,
    network: &str,
    chain_id: u64,
    report: &ValuationBatchReport,
    apply: bool,
) -> Result<ValuationLandingResult, Box<dyn Error>> {
    let mut valued = 0usize;
    let mut unvaluable = 0usize;
    let mut unknown = 0usize;
    for token in &report.tokens {
        match token.verdict {
            ValuationVerdict::RouteFound => valued += 1,
            ValuationVerdict::NoRoute => unvaluable += 1,
            _ => unknown += 1,
        }
    }
    let mut token_addresses: Vec<String> = report
        .tokens
        .iter()
        .map(|token| token.token_address.to_ascii_lowercase())
        .collect();
    token_addresses.sort();

    let mut written = 0usize;
    // How many unknown observations were refused because the store already
    // held a known observation for the same token.
    let mut protected = 0usize;
    if apply {
        let mut connection = Connection::open(database)?;
        connection.busy_timeout(std::time::Duration::from_secs(30))?;
        let transaction = connection.transaction()?;
        {
            // The verdicts already stored for this network and chain, so
            // that an unknown can be refused in favour of a known.
            let mut known_verdicts: BTreeMap<String, String> = BTreeMap::new();
            {
                let mut existing = transaction.prepare(
                    "SELECT token_address, verdict FROM token_usdg_valuations
                      WHERE network = ?1 AND chain_id = ?2",
                )?;
                let rows = existing
                    .query_map(rusqlite::params![network, chain_id as i64], |row| {
                        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
                    })?;
                for row in rows {
                    let (address, verdict) = row?;
                    known_verdicts.insert(address.to_ascii_lowercase(), verdict);
                }
            }
            let mut statement = transaction.prepare(
                "INSERT INTO token_usdg_valuations (
                     network, chain_id, token_address, token_symbol, verdict,
                     has_value, input_amount_raw, usdg_amount_raw,
                     usdg_amount_decimal, price_impact, gas_estimate,
                     route_leg_count, protocols_json, failure_kind,
                     failure_detail, source, measured_at
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11,
                           ?12, ?13, ?14, ?15, ?16, ?17)
                 ON CONFLICT (network, chain_id, token_address) DO UPDATE SET
                     token_symbol = excluded.token_symbol,
                     verdict = excluded.verdict,
                     has_value = excluded.has_value,
                     input_amount_raw = excluded.input_amount_raw,
                     usdg_amount_raw = excluded.usdg_amount_raw,
                     usdg_amount_decimal = excluded.usdg_amount_decimal,
                     price_impact = excluded.price_impact,
                     gas_estimate = excluded.gas_estimate,
                     route_leg_count = excluded.route_leg_count,
                     protocols_json = excluded.protocols_json,
                     failure_kind = excluded.failure_kind,
                     failure_detail = excluded.failure_detail,
                     source = excluded.source,
                     measured_at = excluded.measured_at",
            )?;
            for token in &report.tokens {
                let address = token.token_address.to_ascii_lowercase();
                // An unknown observation carries no information about the
                // token.  Writing it over a known observation would turn a
                // transport outage into a change of economic fact, so the
                // known observation is kept and the write is refused.
                //
                // The unknown is still recorded when the store holds no
                // observation for the token at all: "we tried and could not
                // find out" is worth remembering, because it is the
                // difference between "not measured" and "measured as
                // unvaluable".
                if token.verdict.is_unknown() {
                    if let Some(stored) = known_verdicts.get(&address) {
                        if stored != "api_unavailable" && stored != "api_error" {
                            protected += 1;
                            continue;
                        }
                    }
                }
                let protocols_json = serde_json::to_string(&token.protocols)?;
                let failure_kind = token.failure_kind.map(|kind| kind.as_str().to_owned());
                statement.execute(rusqlite::params![
                    network,
                    chain_id as i64,
                    address,
                    token.token_symbol,
                    token.verdict.as_str(),
                    if token.has_value { 1i64 } else { 0i64 },
                    token.input_amount_raw,
                    // A token without a route stores NULL, never a zero:
                    // "cannot be valued" is not "worth zero".
                    token.usdg_amount_raw,
                    token.usdg_amount_decimal,
                    token.price_impact,
                    token.gas_estimate,
                    token.route_leg_count,
                    protocols_json,
                    failure_kind,
                    token.failure_detail,
                    token.source,
                    token.observed_at,
                ])?;
                known_verdicts.insert(address, token.verdict.as_str().to_owned());
                written += 1;
            }
        }
        transaction.commit()?;
    } else {
        written = report.tokens.len();
    }

    Ok(ValuationLandingResult {
        written,
        valued,
        unvaluable,
        unknown,
        protected,
        token_addresses,
    })
}

/// Renders the landing result for the CLI.
pub fn render_landing(result: &ValuationLandingResult) -> Value {
    serde_json::json!({
        "written": result.written,
        "valued": result.valued,
        "unvaluable": result.unvaluable,
        "unknown": result.unknown,
        "protected": result.protected,
        "tokenCount": result.token_addresses.len(),
        "verdicts": {
            "route_found": result.valued,
            "no_route": result.unvaluable,
            "unknown": result.unknown,
        },
    })
}

/// Parses a stored verdict back into the typed enum.
///
/// A verdict outside the closed vocabulary is a **corrupt row**, not an
/// unknown verdict.  The reader therefore fails loudly instead of guessing
/// one of the four known verdicts: guessing would turn a damaged record
/// into a plausible-looking economic decision.
fn parse_verdict(text: &str) -> Result<ValuationVerdict, String> {
    match text {
        "route_found" => Ok(ValuationVerdict::RouteFound),
        "no_route" => Ok(ValuationVerdict::NoRoute),
        "api_unavailable" => Ok(ValuationVerdict::ApiUnavailable),
        "api_error" => Ok(ValuationVerdict::ApiError),
        other => Err(format!("stored valuation has an unknown verdict: {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::valuation::ValuationVerdict;

    fn valuation(address: &str, verdict: ValuationVerdict, value: Option<&str>) -> TokenValuation {
        TokenValuation {
            token_address: address.to_ascii_lowercase(),
            token_symbol: Some("TEST".to_owned()),
            verdict,
            has_value: value.is_some(),
            input_amount_raw: "1000000000000000000".to_owned(),
            usdg_amount_raw: value.map(|v| v.to_owned()),
            usdg_amount_decimal: value.map(|v| {
                let raw: u128 = v.parse().unwrap_or(0);
                format!("{}.{:06}", raw / 1_000_000, raw % 1_000_000)
            }),
            price_impact: None,
            gas_estimate: None,
            route_leg_count: Some(1),
            protocols: vec!["V4".to_owned()],
            failure_kind: None,
            failure_detail: None,
            source: "uniswap_trading_api_quote".to_owned(),
            observed_at: "2026-11-12T00:00:00Z".to_owned(),
        }
    }

    fn report() -> ValuationBatchReport {
        ValuationBatchReport::from_tokens(vec![
            valuation("0xAAA", ValuationVerdict::RouteFound, Some("1500000")),
            valuation("0xBBB", ValuationVerdict::NoRoute, None),
            valuation("0xCCC", ValuationVerdict::ApiError, None),
        ])
    }

    fn migrated_database(path: &std::path::Path) {
        manager_db::migrate(path).expect("migrate");
    }

    #[test]
    fn a_landing_round_trips_through_the_database() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);

        let result = land_valuation_report(&path, "robinhood", 4663, &report(), true).unwrap();
        assert_eq!(result.written, 3);
        assert_eq!(result.valued, 1);
        assert_eq!(result.unvaluable, 1);
        assert_eq!(result.unknown, 1);

        let stored = read_stored_valuations(&path, "robinhood", 4663).unwrap();
        assert_eq!(stored.len(), 3);
        let valued = stored
            .iter()
            .find(|token| token.token_address == "0xaaa")
            .expect("valued token");
        assert_eq!(valued.verdict, ValuationVerdict::RouteFound);
        assert_eq!(valued.usdg_amount_raw.as_deref(), Some("1500000"));
    }

    #[test]
    fn a_negative_verdict_stores_no_value() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);
        land_valuation_report(&path, "robinhood", 4663, &report(), true).unwrap();

        let connection = Connection::open(&path).unwrap();
        let value: Option<String> = connection
            .query_row(
                "SELECT usdg_amount_raw FROM token_usdg_valuations
                  WHERE token_address = '0xbbb'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        // "cannot be valued" must never be stored as a zero.
        assert_eq!(value, None);
    }

    #[test]
    fn landing_the_same_report_twice_is_idempotent() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);
        land_valuation_report(&path, "robinhood", 4663, &report(), true).unwrap();
        land_valuation_report(&path, "robinhood", 4663, &report(), true).unwrap();

        let connection = Connection::open(&path).unwrap();
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_usdg_valuations", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 3);
    }

    #[test]
    fn a_newer_observation_replaces_the_older_one() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);
        land_valuation_report(&path, "robinhood", 4663, &report(), true).unwrap();

        let mut newer = report();
        newer.tokens[0].usdg_amount_raw = Some("2500000".to_owned());
        newer.tokens[0].observed_at = "2026-11-13T00:00:00Z".to_owned();
        land_valuation_report(&path, "robinhood", 4663, &newer, true).unwrap();

        let stored = read_stored_valuations(&path, "robinhood", 4663).unwrap();
        let valued = stored
            .iter()
            .find(|token| token.token_address == "0xaaa")
            .expect("valued token");
        assert_eq!(valued.usdg_amount_raw.as_deref(), Some("2500000"));
        assert_eq!(valued.observed_at, "2026-11-13T00:00:00Z");
    }

    #[test]
    fn a_dry_run_writes_nothing() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);
        let result = land_valuation_report(&path, "robinhood", 4663, &report(), false).unwrap();
        assert_eq!(result.written, 3);

        let connection = Connection::open(&path).unwrap();
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM token_usdg_valuations", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn a_missing_table_reads_as_no_observation() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("empty.sqlite3");
        Connection::open(&path).unwrap().close().unwrap();
        let stored = read_stored_valuations(&path, "robinhood", 4663).unwrap();
        assert!(stored.is_empty());
    }

    #[test]
    fn an_unknown_observation_never_overwrites_a_known_one() {
        // A probe that could not reach the routing service says nothing
        // about the token.  Writing it over a good observation would turn
        // a transport outage into a change of economic fact.
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);
        land_valuation_report(&path, "robinhood", 4663, &report(), true).unwrap();

        let mut unknown = report();
        for token in &mut unknown.tokens {
            token.verdict = ValuationVerdict::ApiUnavailable;
            token.has_value = false;
            token.usdg_amount_raw = None;
            token.usdg_amount_decimal = None;
        }
        let result = land_valuation_report(&path, "robinhood", 4663, &unknown, true).unwrap();
        // The fixture holds one valued token, one real negative and one
        // unknown.  The first two are protected; the third was already an
        // unknown, so replacing an unknown with an unknown is allowed.
        assert_eq!(result.protected, 2);
        assert_eq!(result.written, 1);

        let stored = read_stored_valuations(&path, "robinhood", 4663).unwrap();
        let valued = stored
            .iter()
            .find(|token| token.token_address == "0xaaa")
            .expect("valued token");
        assert_eq!(valued.verdict, ValuationVerdict::RouteFound);
        assert_eq!(valued.usdg_amount_raw.as_deref(), Some("1500000"));
        let negative = stored
            .iter()
            .find(|token| token.token_address == "0xbbb")
            .expect("negative token");
        assert_eq!(negative.verdict, ValuationVerdict::NoRoute);
    }

    #[test]
    fn an_unknown_observation_is_recorded_when_nothing_is_known_yet() {
        // "We tried and could not find out" is worth remembering: it is the
        // difference between "not measured" and "measured as unvaluable".
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);

        let mut unknown = report();
        for token in &mut unknown.tokens {
            token.verdict = ValuationVerdict::ApiUnavailable;
            token.has_value = false;
            token.usdg_amount_raw = None;
        }
        let result = land_valuation_report(&path, "robinhood", 4663, &unknown, true).unwrap();
        assert_eq!(result.written, 3);
        assert_eq!(result.protected, 0);

        let stored = read_stored_valuations(&path, "robinhood", 4663).unwrap();
        assert_eq!(stored.len(), 3);
        assert!(stored
            .iter()
            .all(|token| token.verdict == ValuationVerdict::ApiUnavailable));
    }

    #[test]
    fn a_real_negative_still_replaces_a_real_negative() {
        // The protection is only against *unknowns*.  A fresh, genuine
        // negative observation replaces an older one, because the token
        // really did lose its route.
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("valuations.sqlite3");
        migrated_database(&path);
        land_valuation_report(&path, "robinhood", 4663, &report(), true).unwrap();

        let mut negative = report();
        for token in &mut negative.tokens {
            token.verdict = ValuationVerdict::NoRoute;
            token.has_value = false;
            token.usdg_amount_raw = None;
            token.usdg_amount_decimal = None;
        }
        let result = land_valuation_report(&path, "robinhood", 4663, &negative, true).unwrap();
        assert_eq!(result.written, 3);
        assert_eq!(result.protected, 0);

        let stored = read_stored_valuations(&path, "robinhood", 4663).unwrap();
        assert!(stored
            .iter()
            .all(|token| token.verdict == ValuationVerdict::NoRoute));
    }

    #[test]
    fn the_verdict_vocabulary_is_closed_at_the_schema_level() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("corrupt.sqlite3");
        migrated_database(&path);
        let connection = Connection::open(&path).unwrap();
        // A verdict outside the closed vocabulary is refused by the schema
        // itself.  The reader's own guard is the second line of defence,
        // but the row can never be written in the first place.
        let error = connection
            .execute(
                "INSERT INTO token_usdg_valuations
                   (network, chain_id, token_address, verdict, has_value,
                    input_amount_raw, protocols_json, source, measured_at)
                 VALUES ('robinhood', 4663, '0xddd', 'made_up', 0,
                         '1', '[]', 'test', '2026-11-12T00:00:00Z')",
                [],
            )
            .unwrap_err();
        assert!(
            error.to_string().contains("CHECK constraint failed"),
            "unexpected error: {error}"
        );
    }
}
